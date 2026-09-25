#!/usr/bin/env python3
"""Freeze the labels of the held-out cases so precision can fall again.

    python scripts/holdout_manifest.py [--write]

Strict precision on this set is not falsifiable. The loop that produces it is
"measure, examine whatever came back unlabelled, verify it, mark it ALLOW" — after
which precision is 1.000 by construction, because a finding can only stay
unlabelled if nobody has looked at it yet. The `ALLOW`-only rule keeps recall
honest but does nothing for precision.

The held-out slice is the cases whose labels came from somewhere other than this
system's output: an upstream fix, or a published advisory. Those are the ones
where a label cannot have been written to accommodate a finding, because the
finding did not exist when the label was written.

This records a hash of each held-out case's labels. The test fails if any of them
changes. Changing one is then a deliberate act — edit the case, re-run with
`--write`, and say in the commit why an independently-sourced label moved — rather
than something that happens quietly during a labelling pass.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "eval" / "holdout.json"
GOLDEN = ROOT / "tests" / "eval" / "golden"


def label_digest(case: dict) -> str:
    """Every label on the case, in a stable order, independent of note wording.

    Notes are prose and get corrected; what must not drift is which line, agent,
    category and severity the set demands.
    """
    rows = []
    for kind in ("expected", "may_find", "must_not_find"):
        for label in case.get(kind) or []:
            rows.append(
                [
                    kind,
                    label.get("file_path"),
                    label.get("line"),
                    label.get("agent"),
                    label.get("category"),
                    label.get("min_severity"),
                ]
            )
    rows.sort(key=lambda r: [str(x) for x in r])
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16]


def current() -> dict[str, str]:
    out = {}
    for path in sorted(GOLDEN.glob("*.json")):
        case = json.loads(path.read_text())
        if case.get("provenance"):
            out[case["id"]] = label_digest(case)
    return out


def main() -> int:
    now = current()
    if "--write" in sys.argv:
        MANIFEST.write_text(json.dumps(now, indent=2, sort_keys=True) + "\n")
        print(f"wrote {len(now)} held-out case digests to {MANIFEST.relative_to(ROOT)}")
        return 0
    if not MANIFEST.exists():
        print("no manifest; run with --write", file=sys.stderr)
        return 1
    saved = json.loads(MANIFEST.read_text())
    drift = {k: (saved.get(k), v) for k, v in now.items() if saved.get(k) != v}
    gone = set(saved) - set(now)
    print(f"  held-out cases: {len(now)}   drifted: {len(drift)}   missing: {len(gone)}")
    for cid, (was, is_) in sorted(drift.items()):
        print(f"    {cid}: {was} -> {is_}")
    return 1 if drift or gone else 0


if __name__ == "__main__":
    raise SystemExit(main())
