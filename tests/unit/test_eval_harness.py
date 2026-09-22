"""The eval harness scores the reviewer, so something has to score the harness."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pr_sentinel.domain.enums import AgentType
from pr_sentinel.domain.models import Finding
from pr_sentinel.evaluation.fixtures import GOLDEN_DIR, EvalCase, Label, load_cases
from pr_sentinel.evaluation.matching import classify, missed
from pr_sentinel.evaluation.metrics import score, score_case

ROOT = Path(__file__).resolve().parents[2]


def finding(
    path="a.py", line=10, agent=AgentType.SECURITY, category="injection", severity="critical", confidence=0.9
):
    return Finding(
        agent=agent,
        category=category,
        severity=severity,
        file_path=path,
        line_start=line,
        line_end=line,
        title="A finding",
        body="body",
        rationale=f"because of line {line}",
        confidence=confidence,
    )


def label(path="a.py", line=10, agent="security", category="injection", min_severity="major"):
    return Label(
        file_path=path, line=line, agent=agent, category=category, min_severity=min_severity, note="n"
    )


# --- matching ---------------------------------------------------------------


def test_a_finding_on_the_labelled_line_is_a_hit():
    m = classify([finding()], [label()], [])[0]
    assert m.kind == "hit" and m.agent_correct and m.category_correct and m.severity_sufficient


def test_a_finding_a_couple_of_lines_off_still_counts():
    assert classify([finding(line=12)], [label(line=10)], [])[0].kind == "hit"


def test_a_finding_far_from_the_label_does_not_count():
    assert classify([finding(line=40)], [label(line=10)], [])[0].kind == "unlabelled"


def test_a_range_finding_covering_the_label_counts():
    f = finding(line=5)
    f.line_end = 20
    assert classify([f], [label(line=12)], [])[0].kind == "hit"


def test_a_finding_in_another_file_does_not_count():
    assert classify([finding(path="b.py")], [label(path="a.py")], [])[0].kind == "unlabelled"


def test_a_finding_on_a_clean_line_is_a_false_positive():
    m = classify([finding()], [], [label()])[0]
    assert m.kind == "false_positive"


def test_expected_labels_beat_traps_when_both_would_match():
    """A line cannot be both; the positive label wins so a mislabelled set fails loudly."""
    assert classify([finding()], [label()], [label()])[0].kind == "hit"


def test_the_wrong_specialist_finding_it_still_counts_as_found():
    """System recall and per-agent attribution are different questions."""
    m = classify([finding(agent=AgentType.CORRECTNESS)], [label(agent="security")], [])[0]
    assert m.kind == "hit" and not m.agent_correct


def test_severity_below_the_floor_is_recorded_as_insufficient():
    m = classify([finding(severity="info")], [label(min_severity="critical")], [])[0]
    assert m.kind == "hit" and not m.severity_sufficient


def test_unfound_labels_are_reported_as_missed():
    assert (
        len(missed([label(line=10), label(line=99)], classify([finding(line=10)], [label(line=10)], []))) >= 1
    )


# --- metrics ----------------------------------------------------------------


def _result(matches_spec, missed_labels=0, decision="auto_post", expected="auto_post", cost=0.01):
    """Build a scored case from a compact spec of ("hit"|"fp"|"unlabelled", confidence)."""
    findings, expected_labels, traps = [], [], []
    for kind, conf in matches_spec:
        f = finding(line=10 + len(findings) * 10, confidence=conf)
        findings.append(f)
        if kind == "hit":
            expected_labels.append(label(line=f.line_start))
        elif kind == "fp":
            traps.append(label(line=f.line_start))
    for i in range(missed_labels):
        expected_labels.append(label(line=500 + i * 10))

    case = EvalCase(
        id="c",
        title="t",
        summary="",
        expected_decision=expected,
        files=[],
        context_chunks=[],
        expected=expected_labels,
        must_not_find=traps,
    )
    return score_case(case, findings, decision=decision, confidence=0.9, cost_usd=cost, duration_ms=1)


def test_precision_is_reported_strictly_and_leniently():
    r = _result([("hit", 0.9), ("unlabelled", 0.9), ("fp", 0.9)])
    rep = score([r], "test", {})
    assert rep.overall.precision_strict == pytest.approx(1 / 3)
    assert rep.overall.precision_lenient == pytest.approx(1 / 2)


def test_recall_counts_missed_labels():
    rep = score([_result([("hit", 0.9)], missed_labels=3)], "test", {})
    assert rep.overall.recall == pytest.approx(0.25)


def test_a_perfectly_calibrated_run_has_near_zero_error():
    """Nine findings at 0.9 confidence of which eight are right."""
    spec = [("hit", 0.9)] * 8 + [("unlabelled", 0.9)]
    rep = score([_result(spec)], "test", {})
    assert rep.ece < 0.05


def test_systematic_overconfidence_is_measured():
    """Ten findings at 0.95; only two are right."""
    spec = [("hit", 0.95)] * 2 + [("unlabelled", 0.95)] * 8
    rep = score([_result(spec)], "test", {})
    assert rep.ece > 0.6
    hot = next(b for b in rep.calibration if b.count)
    assert hot.hit_rate - hot.mean_confidence < -0.5


def test_gate_decisions_are_compared_against_the_label():
    good = _result([("hit", 0.9)], decision="auto_post", expected="auto_post")
    bad = _result([("hit", 0.9)], decision="escalate", expected="auto_post")
    assert score([good, bad], "test", {}).decision_accuracy == pytest.approx(0.5)


def test_cost_is_totalled_and_averaged():
    rep = score([_result([("hit", 0.9)], cost=0.02), _result([("hit", 0.9)], cost=0.04)], "t", {})
    assert rep.total_cost_usd == pytest.approx(0.06)
    assert rep.cost_per_case_usd == pytest.approx(0.03)


def test_an_empty_run_does_not_divide_by_zero():
    rep = score([], "test", {})
    assert rep.overall.precision_strict == 0.0 and rep.ece == 0.0


def test_the_report_serialises_to_json():
    json.dumps(score([_result([("hit", 0.9)])], "test", {"security": "m"}).as_dict())


# --- the golden set itself --------------------------------------------------


def test_the_golden_set_loads_and_is_not_trivial():
    cases = load_cases()
    assert len(cases) >= 12
    assert sum(len(c.expected) for c in cases) >= 12


def test_every_label_points_at_a_line_that_is_actually_in_the_diff():
    """A label outside the diff would score as a permanent miss and never be noticed."""
    for case in load_cases():
        addressable = {f.path: f.addressable_lines() for f in case.files}
        for lab in case.expected + case.must_not_find:
            assert lab.file_path in addressable, f"{case.id}: {lab.file_path} not in the diff"
            assert lab.line in addressable[lab.file_path], (
                f"{case.id}: {lab.file_path}:{lab.line} is labelled but is not in the diff"
            )


def test_the_set_covers_every_specialist():
    agents = {lab.agent for c in load_cases() for lab in c.expected}
    assert {"security", "correctness", "tests", "docs"} <= agents


def test_the_set_contains_false_positive_traps():
    """A set that only rewards recall optimises straight into noise."""
    assert sum(len(c.must_not_find) for c in load_cases()) >= 4


def test_the_set_contains_a_case_where_the_right_answer_is_silence():
    assert any(c.expected_decision == "suppress" and not c.expected for c in load_cases())


def test_no_marker_text_leaked_into_a_fixture():
    """A label visible in the diff would hand the model the answer."""
    for path in GOLDEN_DIR.glob("*.json"):
        raw = json.loads(path.read_text())
        for f in raw["files"]:
            assert "#!EXPECT" not in f["patch"] and "#!CLEAN" not in f["patch"], path.name
        for c in raw.get("context_files", []):
            assert "#!" not in c["content"], path.name


def test_the_committed_fixtures_match_their_source():
    """`cases.py` is the source of truth; the JSON is generated and committed."""
    proc = subprocess.run(
        [sys.executable, "scripts/build_eval_fixtures.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
