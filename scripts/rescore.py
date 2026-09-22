#!/usr/bin/env python3
"""Re-score a recorded eval run under the current matcher, without calling a model.

    python scripts/rescore.py tests/eval/baselines/anthropic-3run.json

Scoring has now been changed three times — once for recall over distinct defects,
once for evidence-based coverage, once to require the finding to be about the
labelled concern — and each time the recorded numbers silently stopped meaning
what the current code would produce. Re-running the models to find out costs about
ten dollars and takes twenty minutes; the findings have not changed, only the
arithmetic over them, so this does it for nothing.
"""

from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pr_sentinel.domain.models import Evidence, Finding  # noqa: E402
from pr_sentinel.evaluation.fixtures import load_cases  # noqa: E402
from pr_sentinel.evaluation.metrics import score, score_case  # noqa: E402


def _rebuild(raw: dict) -> Finding:
    """Reconstruct enough of a Finding for scoring from its recorded shape."""
    where = raw["where"]
    path, _, span = where.rpartition(":")
    start, _, end = span.partition("-")
    line_start = int(start)
    line_end = int(end) if end else line_start
    evidence = []
    for cite in raw.get("cites", []):
        cpath, _, cline = cite.rpartition(":")
        evidence.append(Evidence(kind="diff", file_path=cpath, line_start=int(cline), line_end=int(cline)))
    return Finding(
        agent=raw["agent"],
        category=raw["category"],
        severity=raw["severity"],
        file_path=path,
        line_start=line_start,
        line_end=line_end,
        title=raw["title"],
        body=raw.get("body") or raw["title"],
        rationale=raw["title"],
        confidence=raw["confidence"],
        evidence=evidence,
    )


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    payload = json.loads(Path(sys.argv[1]).read_text())
    runs = payload.get("runs") or [payload]
    cases = {c.id: c for c in load_cases()}

    reports = []
    for run in runs:
        results = []
        for recorded in run["per_case"]:
            case = cases.get(recorded["id"])
            if case is None:
                continue  # the set has moved on since this run
            results.append(
                score_case(
                    case,
                    [_rebuild(f) for f in recorded.get("findings", [])],
                    decision=recorded["decision"],
                    confidence=recorded["confidence"],
                    cost_usd=recorded["cost_usd"],
                    duration_ms=0,
                    failed_agents=recorded.get("failed_agents", []),
                )
            )
        reports.append(score(results, payload.get("provider", "?"), payload.get("models", {})))

    print(f"  re-scored {len(reports)} recorded run(s) over {len(reports[0].cases)} case(s)")
    print("  recorded numbers are from a previous matcher; these are the current one\n")
    print(f"  {'metric':<24}{'mean':>9}{'min':>9}{'max':>9}")
    for name, fn in [
        ("precision strict", lambda r: r.overall.precision_strict),
        ("precision lenient", lambda r: r.overall.precision_lenient),
        ("recall", lambda r: r.overall.recall),
        ("agent attribution", lambda r: r.agent_attribution),
        ("category agreement", lambda r: r.category_agreement),
        ("unlabelled findings", lambda r: float(r.overall.unlabelled)),
        ("false positives", lambda r: float(r.overall.false_positives)),
    ]:
        v = [fn(r) for r in reports]
        print(f"  {name:<24}{st.mean(v):>9.3f}{min(v):>9.3f}{max(v):>9.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
