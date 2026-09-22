#!/usr/bin/env python3
"""Have a model verify each label's note against the diff, blind to any verdict.

    python scripts/blind_label_audit.py [--rev <git-rev>] [--limit N]

Why this exists
---------------
Genesis Kit's sharpest point is that the checker must not see the builder's
reasoning. The label notes in this repository were written by one agent and then
audited by another — but the auditor read the note, the diff, *and* knew it was
looking for overstatement, and it reported three. There is no way to tell from
that whether three is the real number or simply the number that agent noticed.

This runs the check the other way. For each label it sends a model the case diff
and the note, and nothing else: no verdict, no hint that anything is wrong, no
count to find. The model's only job is to decide whether the diff supports every
factual claim the note makes.

The result is comparable to the hand audit only because the hand audit's
conclusions are not in the prompt.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = "claude-opus-5"

PROMPT = """You are checking whether a note attached to a code-review test fixture is \
accurate. The note explains why a particular observation about a diff is considered \
legitimate.

Your only question: **does the diff actually support every factual claim the note makes?**

A note is SUPPORTED when every claim in it can be verified from the diff shown.
A note is OVERSTATED when it asserts something the diff does not show — for example \
claiming a behaviour changed when the diff shows it behaving the same way before and \
after, or asserting how callers elsewhere use a symbol when no caller or signature is \
visible anywhere in the material below. Judge against everything you are given — the \
title, the summary, the unchanged context files and the diff — not the diff alone. \
Widely known behaviour of the language, standard library or platform counts as \
established and does not need to appear in the diff.
A note is CONTRADICTED when the diff shows the opposite of what it claims.

Judge only the note's factual accuracy. Do not judge whether the observation is worth \
making, whether it is severe, or whether it is correctly categorised.

PULL REQUEST TITLE: {title}
SUMMARY: {summary}

The observation is attributed to the {agent} agent, category {category}, at {path}:{line}.
{context}
DIFF UNDER REVIEW:
{diff}

THE NOTE TO CHECK:
{note}

Reply with JSON only: {{"verdict": "SUPPORTED" | "OVERSTATED" | "CONTRADICTED", \
"reason": "<one or two sentences citing the diff>"}}"""


def labels_at(rev: str) -> list[dict]:
    """Every permitted label at `rev` that is not present at the pre-pass commit."""
    base = "81215c8"  # the baseline commit before the labelling pass

    def load(r: str) -> dict:
        out = {}
        names = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "ls-tree", "--name-only", f"{r}:tests/eval/golden"],  # noqa: S607
            capture_output=True,
            text=True,
            cwd=ROOT,
        ).stdout.split()
        for name in names:
            txt = subprocess.run(  # noqa: S603 - fixed argv, no shell
                ["git", "show", f"{r}:tests/eval/golden/{name}"],  # noqa: S607
                capture_output=True,
                text=True,
                cwd=ROOT,
            ).stdout
            if not txt.strip():
                continue
            d = json.loads(txt)
            for m in d.get("may_find") or []:
                key = (d["id"], m.get("file_path"), m.get("line"), m.get("agent"), m.get("category"))
                out[key] = (m.get("note", ""), d)
        return out

    old, new = load(base), load(rev)
    rows = []
    for key, (note, doc) in new.items():
        if key in old:
            continue
        cid, path, line, agent, cat = key
        diff = "\n".join(f"--- {f['filename']} ---\n{f.get('patch') or ''}" for f in doc["files"])
        rows.append(
            {
                "case_id": cid,
                "path": path,
                "line": line,
                "agent": agent,
                "category": cat,
                "note": note,
                "diff": diff,
                "title": doc.get("title", ""),
                "summary": doc.get("summary", ""),
                "context": "".join(
                    f"\nUNCHANGED CONTEXT FILE {cf['path']}:\n{cf['content']}\n"
                    for cf in (doc.get("context_files") or [])
                ),
            }
        )
    return sorted(rows, key=lambda r: (r["case_id"], r["line"] or 0))


async def judge(client, row: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        msg = await client.messages.create(
            model=MODEL,
            max_tokens=2000,
            thinking={"type": "adaptive"},
            messages=[
                {
                    "role": "user",
                    "content": PROMPT.format(
                        title=row["title"],
                        summary=row["summary"],
                        context=row["context"][:6000],
                        agent=row["agent"],
                        category=row["category"],
                        path=row["path"],
                        line=row["line"],
                        diff=row["diff"][:12000],
                        note=row["note"],
                    ),
                }
            ],
        )
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    start, end = text.find("{"), text.rfind("}")
    try:
        parsed = json.loads(text[start : end + 1])
    except Exception:
        parsed = {"verdict": "UNPARSED", "reason": text[:200]}
    return {**row, **parsed}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rev", default="b2991ad", help="revision whose notes to audit")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    for line in (ROOT / ".env").read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    import anthropic

    rows = labels_at(args.rev)
    if args.limit:
        rows = rows[: args.limit]
    print(f"auditing {len(rows)} notes at {args.rev} with {MODEL}, blind to any verdict\n", file=sys.stderr)

    client = anthropic.AsyncAnthropic()
    sem = asyncio.Semaphore(4)
    results = await asyncio.gather(*(judge(client, r, sem) for r in rows))

    flagged = [r for r in results if r["verdict"] != "SUPPORTED"]
    for r in results:
        mark = "  ok " if r["verdict"] == "SUPPORTED" else ">>> "
        print(f"{mark}{r['verdict']:<13} {r['case_id']}:{r['line']} [{r['agent']}/{r['category']}]")
        if r["verdict"] != "SUPPORTED":
            print(f"      {r['reason']}")
    print(f"\n{len(flagged)} of {len(results)} notes flagged")
    (ROOT / "tests/eval/audits").mkdir(parents=True, exist_ok=True)
    (ROOT / "tests/eval/audits/blind-label-audit.json").write_text(
        json.dumps([{k: v for k, v in r.items() if k not in ("diff", "context")} for r in results], indent=2)
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
