#!/usr/bin/env python3
"""Turn tests/eval/cases.py into tests/eval/golden/*.json.

Two jobs. It computes the diff from BEFORE/AFTER so labelled line numbers cannot
drift, and it *validates* every label against the parsed diff — a golden file whose
expected line is not even addressable in the diff would silently score as a miss
forever, which is worse than having no eval at all.

    python scripts/build_eval_fixtures.py [--check]

`--check` verifies the committed fixtures are current without writing, which is
what CI runs.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "eval"))

from cases import CASES  # noqa: E402

from pr_sentinel.forge.diff import build_diff_file  # noqa: E402

GOLDEN = ROOT / "tests" / "eval" / "golden"
MARKER = re.compile(r"^\s*(?:#!|//!)\s*(EXPECT|CLEAN)\b(?P<attrs>[^:]*?)(?:\s*::\s*(?P<note>.*))?$")
SEVERITY_ORDER = ["info", "minor", "major", "critical"]


def strip_markers(text: str) -> tuple[str, list[dict]]:
    """Remove marker lines and attach each to the line that follows it."""
    out: list[str] = []
    labels: list[dict] = []
    pending: list[dict] = []

    for raw in text.splitlines():
        m = MARKER.match(raw)
        if m:
            pending.append(_parse_marker(m))
            continue
        out.append(raw)
        for label in pending:
            label["line"] = len(out)
            labels.append(label)
        pending = []

    if pending:
        raise ValueError(f"marker with no following line: {pending}")
    return "\n".join(out) + ("\n" if text.endswith("\n") else ""), labels


def _parse_marker(m: re.Match) -> dict:
    kind = m.group(1)
    attrs = (m.group("attrs") or "").strip()
    label: dict = {"kind": kind, "note": (m.group("note") or "").strip()}
    for token in attrs.split():
        if ">=" in token:
            key, _, value = token.partition(">=")
            label[f"min_{key}"] = value
        elif "=" in token:
            key, _, value = token.partition("=")
            label[key] = value
    if kind == "CLEAN" and ("agent" in label or "category" in label):
        # A scoped trap: only this reading of the line is a false positive.
        pass
    if kind == "EXPECT":
        if "agent" not in label:
            raise ValueError(f"EXPECT marker needs agent=: {m.group(0)!r}")
        if "min_severity" not in label:
            label["min_severity"] = "info"
        if label["min_severity"] not in SEVERITY_ORDER:
            raise ValueError(f"unknown severity {label['min_severity']!r}")
    return label


def make_patch(path: str, before: str, after: str) -> str:
    """A GitHub-shaped patch: hunks only, no ---/+++ header."""
    diff = list(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )
    body = [ln.rstrip("\n") for ln in diff[2:]]
    return "\n".join(body)


def build_case(case: dict) -> dict:
    before_files: dict[str, str] = case.get("before", {})
    after_raw: dict[str, str] = case["after"]

    files: list[dict] = []
    expected: list[dict] = []
    forbidden: list[dict] = []

    for path, raw_after in after_raw.items():
        after, labels = strip_markers(raw_after)
        before = before_files.get(path, "")
        patch = make_patch(path, before, after)
        if not patch.strip():
            raise ValueError(f"{case['id']}: {path} produced an empty patch")

        payload = {
            "filename": path,
            "status": "added" if path not in before_files else "modified",
            "additions": sum(1 for ln in patch.splitlines() if ln.startswith("+")),
            "deletions": sum(1 for ln in patch.splitlines() if ln.startswith("-")),
            "patch": patch,
        }
        files.append(payload)

        addressable = build_diff_file(payload).addressable_lines()
        for label in labels:
            entry = {k: v for k, v in label.items() if k != "kind"}
            entry["file_path"] = path
            if label["line"] not in addressable:
                raise ValueError(
                    f"{case['id']}: {path}:{label['line']} is labelled {label['kind']} but is not "
                    f"in the diff (addressable: {sorted(addressable)[:12]}…). "
                    "Give the marker more surrounding change, or move it."
                )
            (expected if label["kind"] == "EXPECT" else forbidden).append(entry)

    # Files that exist only so retrieval has a repository to find. Never diffed.
    context = [{"path": p, "content": c} for p, c in (case.get("context") or {}).items()]

    return {
        "id": case["id"],
        "title": case["title"],
        "summary": case.get("summary", ""),
        "expected_decision": case.get("expected_decision"),
        "files": files,
        "context_files": context,
        "expected": expected,
        "must_not_find": forbidden,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify without writing")
    args = parser.parse_args()

    GOLDEN.mkdir(parents=True, exist_ok=True)
    stale: list[str] = []
    built = 0
    expected_total = forbidden_total = 0

    for case in CASES:
        fixture = build_case(case)
        expected_total += len(fixture["expected"])
        forbidden_total += len(fixture["must_not_find"])
        target = GOLDEN / f"{fixture['id']}.json"
        rendered = json.dumps(fixture, indent=2, sort_keys=True) + "\n"

        if args.check:
            if not target.exists() or target.read_text() != rendered:
                stale.append(fixture["id"])
        else:
            target.write_text(rendered)
        built += 1

    if args.check:
        if stale:
            print(f"stale fixtures: {', '.join(stale)}", file=sys.stderr)
            print("run: python scripts/build_eval_fixtures.py", file=sys.stderr)
            return 1
        print(f"{built} fixtures up to date")
        return 0

    print(
        f"built {built} fixtures — {expected_total} labelled findings, {forbidden_total} false-positive traps"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
