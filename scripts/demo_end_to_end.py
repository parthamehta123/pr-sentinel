#!/usr/bin/env python3
"""The proof.

The fake credential below deliberately uses a fictional `acme_live_` prefix rather
than a real vendor's key format. A realistic-looking one trips GitHub's push
protection and every downstream secret scanner forever, which costs real attention
to prove a point a made-up prefix proves just as well.

Runs the full path — signed webhook verification, grounding, the four-agent
panel, aggregation, the confidence gate — against a synthetic pull request, with
no network, no database and no API key. Using the deterministic `echo` provider
means this is a real exercise of the pipeline rather than a mock of it, which is
why it can run in CI on every commit.

    make demo
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("LLM_PROVIDER", "echo")
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "demo-secret-for-local-proof-only")

from pr_sentinel.domain.enums import ALL_AGENTS
from pr_sentinel.domain.models import PullRequestContext
from pr_sentinel.events.spine import NullSpine
from pr_sentinel.forge.diff import build_diff_file
from pr_sentinel.gate import evaluate, render_review_body
from pr_sentinel.ingress.security import compute_signature, verify_signature
from pr_sentinel.llm.registry import bundle_version
from pr_sentinel.orchestration import aggregator
from pr_sentinel.orchestration.local_engine import LocalEngine
from pr_sentinel.reliability.budget import BudgetGuard
from pr_sentinel.retrieval.context import ReviewContext

PATCH = """@@ -10,6 +10,17 @@ class BillingHandler:
     def __init__(self, db):
         self.db = db
 
+    ACMEPAY_SECRET = "acme_live_7f3c9d2e8b1a45061f2d8c9e3b7a"
+
+    def charge_customer(self, customer_id, amount):
+        row = self.db.execute(f"SELECT * FROM customers WHERE id = {customer_id}")
+        try:
+            return self._charge(row, amount)
+        except:
+            return None
+
     def refund(self, charge_id):
         return self.db.refunds.create(charge_id)
"""


def banner(text: str) -> None:
    print(f"\n\033[1m{text}\033[0m\n" + "─" * 68)


async def main() -> int:
    banner("1. webhook signature — INVARIANT-1")
    secret = os.environ["GITHUB_WEBHOOK_SECRET"]
    body = b'{"action":"opened","pull_request":{"number":42}}'
    good = compute_signature(secret, body)
    print(f"  valid signature accepted   : {verify_signature(secret, body, good)}")
    print(f"  tampered body rejected     : {not verify_signature(secret, body + b'x', good)}")
    print(f"  wrong secret rejected      : {not verify_signature('nope', body, good)}")
    print(f"  missing header rejected    : {not verify_signature(secret, body, None)}")

    banner("2. diff parsing and grounding")
    diff_file = build_diff_file(
        {
            "filename": "billing/handler.py",
            "status": "modified",
            "additions": 11,
            "deletions": 0,
            "patch": PATCH,
        }
    )
    addressable = sorted(diff_file.addressable_lines())
    print(f"  hunks parsed               : {len(diff_file.hunks)}")
    print(f"  addressable new-file lines : {addressable[0]}-{addressable[-1]} ({len(addressable)} lines)")
    print("  a finding citing line 9999 is discarded before a human ever sees it.")

    pr = PullRequestContext(
        repo_full_name="acme/payments",
        repo_github_id=1,
        is_private=True,
        number=42,
        title="Add charge_customer",
        author="dev",
        head_sha="abc123",
        base_sha="def456",
        files=[diff_file],
    )
    from pr_sentinel.forge.diff import render_for_prompt

    diff_text, _ = render_for_prompt(pr.files)
    ctx = ReviewContext(
        pr=pr,
        diff_text=diff_text,
        conventions=[
            "Never interpolate values into SQL.",
            "Secrets come from the environment, never the repo.",
        ],
    )

    banner("3. four specialists, in parallel")
    verdicts = await LocalEngine().run_panel(
        ctx, list(ALL_AGENTS), NullSpine(), BudgetGuard(review_id=uuid.uuid4(), enabled=False)
    )
    for v in verdicts:
        print(f"  {v.agent!s:<12} {v.status:<8} {len(v.findings)} finding(s)  {v.summary}")

    banner("4. aggregation")
    findings, confidence = aggregator.aggregate(verdicts)
    for f in findings:
        who = " + ".join(f.agreeing) if f.agreeing else str(f.agent)
        print(f"  [{f.severity!s:<8}] {f.file_path}:{f.line_start:<4} {f.confidence:.2f}  {f.title}  ({who})")
    print(f"\n  overall confidence         : {confidence:.3f}")

    banner("5. the confidence gate")
    result = evaluate(findings, verdicts, confidence, is_public_repo=False)
    print(f"  decision                   : {result.decision}")
    print(f"  reason                     : {result.reason or '—'}")
    print(f"  postable to the PR         : {len(result.postable)}")
    print(f"  why                        : {result.explanation}")

    if result.postable:
        banner("6. what GitHub would receive")
        print(
            render_review_body(
                result.postable,
                aggregator.summarise(findings, verdicts),
                len(findings) - len(result.postable),
                bundle_version(),
            )
        )
    else:
        banner("6. nothing is posted")
        print("  A critical security finding on a pull request is a disclosure, so it goes")
        print("  to a private human queue instead. That is the gate working, not failing.")

    banner("summary")
    print(
        json.dumps(
            {
                "agents_run": len(verdicts),
                "findings": len(findings),
                "confidence": confidence,
                "decision": str(result.decision),
                "escalation_reason": str(result.reason) if result.reason else None,
                "prompt_bundle": bundle_version(),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
