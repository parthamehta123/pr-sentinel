#!/usr/bin/env python3
"""Compare two repeated eval runs and say which differences are real.

    python scripts/compare_arms.py baseline.json candidate.json

Both files must come from `pr-sentinel eval --repeat N --save`, which records
every run rather than only the last. The verdict is deliberately crude: if the
two observed ranges overlap, the change has not been demonstrated. With three
samples per arm that is a sanity check, not a significance test — but it is
enough to stop a single lucky run being reported as an improvement, which is the
failure mode this exists to prevent.

The metric that matters most is `docs_posted` / `total_posted`: findings that
clear both the confidence and severity bars and therefore actually consume a
reviewer's attention. Precision is a proxy; posted findings are the product.
"""

from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

NOISE_PHRASES = (
    "lacks",
    "missing",
    "no docstring",
    "undocumented",
    "has no",
    "not documented",
)
DOCS_CATEGORIES = ("documentation", "readability", "convention")

# Lower is better for these; for everything else higher is better.
LOWER_IS_BETTER = {
    "findings_total",
    "noise_notes",
    "out_of_lane",
    "posted",
    "calibration_error",
    "cost_per_case",
}


def postable(finding: dict, min_confidence: float = 0.60) -> bool:
    """Mirrors the gate: a finding must clear confidence AND severity to be posted."""
    return finding["confidence"] >= min_confidence and finding["severity"] != "info"


def per_run(report: dict, agent: str | None) -> dict:
    findings = [f for c in report["per_case"] for f in c.get("findings", [])]
    scoped = [f for f in findings if agent is None or f["agent"] == agent]
    bucket = report["by_agent"].get(agent, {}) if agent else report["overall"]
    return {
        "findings_total": len(scoped),
        "noise_notes": sum(1 for f in scoped if any(w in f["title"].lower() for w in NOISE_PHRASES)),
        "out_of_lane": sum(1 for f in scoped if agent == "docs" and f["category"] not in DOCS_CATEGORIES),
        "posted": sum(1 for f in scoped if postable(f)),
        "precision": bucket.get("precision_strict", 0.0),
        "recall": bucket.get("recall", 0.0),
        "calibration_error": report["calibration"]["expected_calibration_error"],
        "cost_per_case": report["cost"]["per_case_usd"],
    }


def load(path: Path, agent: str | None) -> list[dict]:
    payload = json.loads(path.read_text())
    runs = payload.get("runs")
    if not runs:
        raise SystemExit(
            f"{path} holds a single run. Re-record it with `eval --repeat N --save`; "
            "comparing one sample against one sample is what this script exists to avoid."
        )
    return [per_run(r, agent) for r in runs]


def render(a: list[dict], b: list[dict], label_a: str, label_b: str, scope: str) -> str:
    out = [
        "",
        f"  {scope}   n={len(a)} vs n={len(b)}",
        f"  {'metric':<20}{label_a:>10}{'range':>13}{label_b:>10}{'range':>13}   verdict",
        "  " + "-" * 80,
    ]
    verdicts: dict[str, str] = {}
    for key in a[0]:
        va, vb = [r[key] for r in a], [r[key] for r in b]
        am, bm = st.mean(va), st.mean(vb)
        alo, ahi, blo, bhi = min(va), max(va), min(vb), max(vb)
        if not (ahi < blo or bhi < alo):
            verdict = "within noise"
        else:
            improved = (bm < am) if key in LOWER_IS_BETTER else (bm > am)
            verdict = "separated, better" if improved else "separated, WORSE"
        verdicts[key] = verdict
        out.append(
            f"  {key:<20}{am:>10.3f}{f'{alo:.2f}-{ahi:.2f}':>13}"
            f"{bm:>10.3f}{f'{blo:.2f}-{bhi:.2f}':>13}   {verdict}"
        )
    return "\n".join(out)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    a_path, b_path = Path(sys.argv[1]), Path(sys.argv[2])
    label_a, label_b = a_path.stem[:9], b_path.stem[:9]

    for scope, agent in (
        ("OVERALL", None),
        ("docs agent", "docs"),
        ("security agent", "security"),
        ("correctness agent", "correctness"),
        ("tests agent", "tests"),
    ):
        print(render(load(a_path, agent), load(b_path, agent), label_a, label_b, scope))

    print("\n  Ranges that overlap mean the change has not been demonstrated.")
    print("  With three samples per arm, 'separated' is suggestive, not significant.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
