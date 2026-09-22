"""Rendering an eval report a human will actually read."""

from __future__ import annotations

from ..domain.enums import family_of
from .metrics import EvalReport


def render(report: EvalReport, verbose: bool = False) -> str:
    out: list[str] = []
    add = out.append
    b = report.overall

    add("=" * 74)
    add(f"  eval — {len(report.cases)} cases · provider={report.provider}")
    add("=" * 74)
    if report.provider == "echo":
        add("")
        add("  NOTE: the `echo` provider is a regex-based fake, not a reviewer. These")
        add("  numbers measure whether the harness works — matching, calibration binning,")
        add("  trap detection, gate comparison — and say nothing about review quality.")
        add("  Run with --provider anthropic for numbers that mean something.")

    add("")
    add("CALIBRATION   does a confidence of X turn out right X of the time?")
    add(f"  expected calibration error : {report.ece:.3f}   (lower is better; <0.10 is good)")
    if any(x.count for x in report.calibration):
        add("")
        add(f"  {'confidence':<14}{'n':>5}{'stated':>10}{'observed':>11}{'gap':>9}")
        for cb in report.calibration:
            if not cb.count:
                continue
            flag = "  ← overconfident" if cb.hit_rate - cb.mean_confidence < -0.15 else ""
            add(
                f"  {cb.low:.2f}-{cb.high:<9.2f}{cb.count:>5}"
                f"{cb.mean_confidence:>10.2f}{cb.hit_rate:>11.2f}"
                f"{cb.hit_rate - cb.mean_confidence:>+9.2f}{flag}"
            )

    add("")
    add("ACCURACY")
    add(f"  precision (strict)  : {b.precision_strict:.3f}   unlabelled findings count against")
    add(f"  precision (lenient) : {b.precision_lenient:.3f}   only labelled traps count against")
    add(f"  recall              : {b.recall:.3f}   over distinct labels, not matches")
    add(f"  f1                  : {b.f1:.3f}")
    add(
        f"  {b.labels_found} of {b.labels_found + b.misses} defects found by "
        f"{b.hits} finding(s) · {b.false_positives} false positive(s) "
        f"· {b.unlabelled} unlabelled"
    )
    add(
        f"  findings per concern: {b.duplicate_rate:.2f}"
        + ("   ← the aggregator is under-merging" if b.duplicate_rate > 1.5 else "")
    )
    add(f"  category agreement  : {report.category_agreement:.3f}   right problem, right name")
    add(f"  agent attribution   : {report.agent_attribution:.3f}   found by the expected specialist")
    if report.decision_accuracy is not None:
        add(f"  gate decision match : {report.decision_accuracy:.3f}")

    add("")
    add("BY AGENT")
    add(f"  {'agent':<14}{'hits':>6}{'miss':>6}{'fp':>5}{'unlab':>7}{'prec':>8}{'recall':>8}")
    for name, bucket in report.by_agent.items():
        add(
            f"  {name:<14}{bucket.hits:>6}{bucket.misses:>6}{bucket.false_positives:>5}"
            f"{bucket.unlabelled:>7}{bucket.precision_strict:>8.2f}{bucket.recall:>8.2f}"
        )

    add("")
    add("COST")
    add(f"  total     : ${report.total_cost_usd:.4f}")
    add(f"  per case  : ${report.cost_per_case_usd:.4f}")

    misses = [(c, m) for c in report.cases for m in c.missed]
    if misses:
        add("")
        add(f"MISSED  ({len(misses)})")
        for case, label in misses:
            add(f"  {case.case_id}")
            add(f"    {label.file_path}:{label.line} [{label.agent}/{label.category}] {label.note}")

    traps = [(c, m) for c in report.cases for m in c.false_positives]
    if traps:
        add("")
        add(f"FALSE POSITIVES  ({len(traps)})  — flagged a line labelled as clean")
        for case, match in traps:
            add(f"  {case.case_id}")
            add(
                f"    {match.finding.file_path}:{match.finding.line_start} "
                f"[{match.finding.agent}] {match.finding.title} ({match.finding.confidence:.2f})"
            )
            if match.label:
                add(f"    why it is clean: {match.label.note}")

    if b.duplicate_rate > 1.5:
        add("")
        add(
            f"DUPLICATES  — {b.hits} findings across {b.concerns} distinct concerns. "
            "Same concern, same lines, said more than once:"
        )
        for case in report.cases:
            per_concern: dict[tuple, list] = {}
            for m in case.hits:
                per_concern.setdefault((id(m.label), family_of(m.finding.category)), []).append(m)
            for group in per_concern.values():
                if len(group) < 2:
                    continue
                first = group[0]
                add(
                    f"  {case.case_id:<34} {first.finding.file_path}:"
                    f"{first.finding.line_start} — {len(group)} findings"
                )
                for m in group:
                    add(
                        f"      [{m.finding.agent!s:<11} {m.finding.category!s:<17} "
                        f"{m.finding.confidence:.2f}] {m.finding.title}"
                    )

    wrong_gate = [c for c in report.cases if c.decision_correct is False]
    if wrong_gate:
        add("")
        add(f"GATE DISAGREEMENTS  ({len(wrong_gate)})")
        for c in wrong_gate:
            add(
                f"  {c.case_id:<34} expected {c.expected_decision:<10} got {c.decision:<10} "
                f"(confidence {c.confidence:.2f})"
            )

    if verbose:
        unlabelled = [(c, m) for c in report.cases for m in c.unlabelled]
        if unlabelled:
            add("")
            add(f"UNLABELLED  ({len(unlabelled)})  — real defects or noise; label them to find out")
            for case, match in unlabelled:
                add(
                    f"  {case.case_id:<34} {match.finding.file_path}:{match.finding.line_start} "
                    f"[{match.finding.agent}] {match.finding.title} ({match.finding.confidence:.2f})"
                )

    add("")
    return "\n".join(out)


def compare(current: EvalReport, baseline: dict) -> tuple[str, bool]:
    """Diff against a saved run. Returns (rendered, regressed)."""
    base = baseline.get("overall", {})
    rows = [
        ("precision (strict)", base.get("precision_strict", 0.0), current.overall.precision_strict, 1),
        ("recall", base.get("recall", 0.0), current.overall.recall, 1),
        ("f1", base.get("f1", 0.0), current.overall.f1, 1),
        (
            "calibration error",
            baseline.get("calibration", {}).get("expected_calibration_error", 0.0),
            current.ece,
            -1,  # lower is better
        ),
        (
            "cost per case",
            baseline.get("cost", {}).get("per_case_usd", 0.0),
            current.cost_per_case_usd,
            -1,
        ),
    ]

    out = ["", "AGAINST BASELINE", f"  {'metric':<22}{'baseline':>10}{'current':>10}{'delta':>10}"]
    regressed = False
    for name, before, after, direction in rows:
        delta = after - before
        # Small wobble is noise, not signal; only a real move counts as a regression.
        if direction * delta < -0.02:
            regressed = True
            mark = "  ← regression"
        else:
            mark = ""
        out.append(f"  {name:<22}{before:>10.3f}{after:>10.3f}{delta:>+10.3f}{mark}")
    out.append("")
    return "\n".join(out), regressed


def stability(reports: list[EvalReport]) -> str:
    """Mean and spread across repeated runs of the same configuration.

    The spread is the number that matters: a change smaller than it has not been
    demonstrated, however good the headline looks.
    """
    if len(reports) < 2:
        return ""

    metrics: list[tuple[str, list[float]]] = [
        ("precision (strict)", [r.overall.precision_strict for r in reports]),
        ("precision (lenient)", [r.overall.precision_lenient for r in reports]),
        ("recall", [r.overall.recall for r in reports]),
        ("f1", [r.overall.f1 for r in reports]),
        ("calibration error", [r.ece for r in reports]),
        ("findings per concern", [r.overall.duplicate_rate for r in reports]),
        (
            "findings produced",
            [float(r.overall.hits + r.overall.unlabelled + r.overall.false_positives) for r in reports],
        ),
        ("cost per case ($)", [r.cost_per_case_usd for r in reports]),
    ]

    out = [
        "",
        f"STABILITY over {len(reports)} runs of the same configuration",
        f"  {'metric':<22}{'mean':>9}{'min':>9}{'max':>9}{'spread':>9}",
    ]
    for name, values in metrics:
        mean = sum(values) / len(values)
        lo, hi = min(values), max(values)
        out.append(f"  {name:<22}{mean:>9.3f}{lo:>9.3f}{hi:>9.3f}{hi - lo:>+9.3f}")
    out += [
        "",
        "  A change smaller than the spread above has not been demonstrated.",
        "  If the spread is uncomfortably wide, the set is too small — add cases",
        "  before tuning against it.",
        "",
    ]
    return "\n".join(out)
