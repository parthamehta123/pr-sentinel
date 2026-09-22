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


def test_recall_counts_distinct_defects_not_matches():
    """Regression: four agents describing one defect must not read as four hits.

    Recall over matches lets a duplicate-happy run score well while missing real
    labels. Seen live: five findings landed on one labelled line.
    """
    findings = [
        finding(line=10, agent=a)
        for a in (AgentType.SECURITY, AgentType.CORRECTNESS, AgentType.TESTS, AgentType.DOCS)
    ]
    case = EvalCase(
        id="c",
        title="t",
        summary="",
        expected_decision=None,
        files=[],
        context_chunks=[],
        expected=[label(line=10), label(line=900)],
        must_not_find=[],
    )
    r = score_case(case, findings, decision="auto_post", confidence=0.9, cost_usd=0.0, duration_ms=1)
    rep = score([r], "test", {})
    assert rep.overall.hits == 4
    assert rep.overall.labels_found == 1
    assert rep.overall.misses == 1
    assert rep.overall.recall == pytest.approx(0.5)  # one of two defects, not 4/5
    assert rep.overall.duplicate_rate == pytest.approx(4.0)


def test_per_agent_recall_also_counts_distinct_defects():
    findings = [finding(line=10), finding(line=11), finding(line=12)]
    case = EvalCase(
        id="c",
        title="t",
        summary="",
        expected_decision=None,
        files=[],
        context_chunks=[],
        expected=[label(line=10)],
        must_not_find=[],
    )
    rep = score(
        [score_case(case, findings, decision="auto_post", confidence=0.9, cost_usd=0.0, duration_ms=1)],
        "test",
        {},
    )
    assert rep.by_agent["security"].labels_found == 1
    assert rep.by_agent["security"].recall == pytest.approx(1.0)


def test_duplicate_rate_counts_concerns_not_labels():
    """A docs finding and a tests finding on one line are two concerns, not a duplicate.

    The label here carries no category, so it matches on location alone — which is
    the only way two different-family findings can both be hits now that a hit
    requires concern agreement. The denominator still has to be concerns, because
    the alternative over-reports duplication.
    """
    findings = [
        finding(line=10, agent=AgentType.TESTS, category="test_coverage", severity="minor"),
        finding(line=10, agent=AgentType.DOCS, category="documentation", severity="info"),
    ]
    uncategorised = Label(file_path="a.py", line=10, agent="tests", note="n")
    case = EvalCase(
        id="c",
        title="t",
        summary="",
        expected_decision=None,
        files=[],
        context_chunks=[],
        expected=[uncategorised],
        must_not_find=[],
    )
    rep = score(
        [score_case(case, findings, decision="auto_post", confidence=0.9, cost_usd=0.0, duration_ms=1)],
        "test",
        {},
    )
    assert rep.overall.hits == 2
    assert rep.overall.labels_found == 1
    assert rep.overall.concerns == 2
    assert rep.overall.duplicate_rate == pytest.approx(1.0)  # not 2.0


def test_the_same_concern_said_twice_does_count_as_duplication():
    findings = [
        finding(line=10, agent=AgentType.SECURITY, category="injection"),
        finding(line=10, agent=AgentType.CORRECTNESS, category="input_validation"),
    ]
    case = EvalCase(
        id="c",
        title="t",
        summary="",
        expected_decision=None,
        files=[],
        context_chunks=[],
        expected=[label(line=10)],
        must_not_find=[],
    )
    rep = score(
        [score_case(case, findings, decision="auto_post", confidence=0.9, cost_usd=0.0, duration_ms=1)],
        "test",
        {},
    )
    assert rep.overall.concerns == 1
    assert rep.overall.duplicate_rate == pytest.approx(2.0)


def test_a_run_where_every_agent_failed_is_refused_not_scored():
    """Regression: an exhausted credit balance produced a report reading
    `calibration error 0.000`, `0 findings`, `$0.0000` — indistinguishable from a
    flawless run of a reviewer that says nothing — and overwrote a good baseline.
    A regression gate fed that would have passed."""
    from pr_sentinel.domain.enums import ALL_AGENTS
    from pr_sentinel.evaluation.runner import EvalRunFailed, _refuse_if_everything_failed

    case = EvalCase(
        id="c",
        title="t",
        summary="",
        expected_decision=None,
        files=[],
        context_chunks=[],
        expected=[label()],
        must_not_find=[],
    )
    dead = [
        score_case(
            case,
            [],
            decision="escalate",
            confidence=0.0,
            cost_usd=0.0,
            duration_ms=1,
            failed_agents=[str(a) for a in ALL_AGENTS],
        )
        for _ in range(3)
    ]
    with pytest.raises(EvalRunFailed, match="outage"):
        _refuse_if_everything_failed(dead)


def test_a_run_with_one_healthy_case_is_still_scored():
    """Partial failure is a result — degraded, but real. Only a total outage is refused."""
    from pr_sentinel.domain.enums import ALL_AGENTS
    from pr_sentinel.evaluation.runner import _refuse_if_everything_failed

    case = EvalCase(
        id="c",
        title="t",
        summary="",
        expected_decision=None,
        files=[],
        context_chunks=[],
        expected=[label()],
        must_not_find=[],
    )
    mixed = [
        score_case(
            case,
            [],
            decision="escalate",
            confidence=0.0,
            cost_usd=0.0,
            duration_ms=1,
            failed_agents=[str(a) for a in ALL_AGENTS],
        ),
        score_case(
            case,
            [finding()],
            decision="auto_post",
            confidence=0.9,
            cost_usd=0.01,
            duration_ms=1,
            failed_agents=[],
        ),
    ]
    _refuse_if_everything_failed(mixed)  # does not raise


# --- scoped traps -----------------------------------------------------------
#
# An unscoped CLEAN claims nothing at that line is a finding, which is a strong
# promise and hard to earn: two rounds of correcting this set's "clean" cases
# still left real defects that the models duly found. A scoped trap claims only
# that one reading is wrong.


def test_an_unscoped_trap_catches_any_finding_on_the_line():
    trap = Label(file_path="a.py", line=10, note="nothing here")
    assert (
        classify([finding(agent=AgentType.DOCS, category="documentation")], [], [trap])[0].kind
        == "false_positive"
    )


def test_a_scoped_trap_only_catches_the_reading_it_names():
    trap = Label(file_path="a.py", line=10, agent="security", note="not an injection")
    security = finding(agent=AgentType.SECURITY, category="injection")
    tests_note = finding(agent=AgentType.TESTS, category="test_coverage", severity="minor")

    assert classify([security], [], [trap])[0].kind == "false_positive"
    # A different concern at the same line is a legitimate observation, not a trap hit.
    assert classify([tests_note], [], [trap])[0].kind == "unlabelled"


def test_a_category_scoped_trap_discriminates_within_one_agent():
    trap = Label(file_path="a.py", line=10, category="crypto", note="md5 is fine here")
    assert classify([finding(category="crypto")], [], [trap])[0].kind == "false_positive"
    assert classify([finding(category="logic")], [], [trap])[0].kind == "unlabelled"


def test_most_traps_in_the_set_stay_unscoped():
    """Scoping is the exception. A set of only scoped traps stops testing restraint."""
    cases = load_cases()
    traps = [t for c in cases for t in c.must_not_find]
    unscoped = [t for t in traps if t.agent is None and t.category is None]
    assert len(unscoped) / len(traps) > 0.7


# --- a hit needs the right concern, not just the right line -----------------
#
# Investigating agent attribution 0.33 found the cause: matching on location
# alone. Over three recorded runs, 56.6% of "hits" were a different agent
# describing a different concern that happened to land on the same line — the
# tests agent noting "no test covers this" on a line labelled for SQL injection.
# Corrected, attribution is 0.81 and the four-specialist split is fine; it was the
# metric that was broken, not the architecture.


def test_a_finding_about_another_concern_is_not_a_hit():
    injection = label(line=10, agent="security", category="injection")
    coverage = finding(line=10, agent=AgentType.TESTS, category="test_coverage", severity="minor")
    assert classify([coverage], [injection], [])[0].kind == "unlabelled"


def test_a_different_agent_on_the_same_concern_is_still_a_hit():
    """Cross-agent agreement is the signal the aggregator is built on; keep it."""
    injection = label(line=10, agent="security", category="injection")
    from_correctness = finding(line=10, agent=AgentType.CORRECTNESS, category="input_validation")
    m = classify([from_correctness], [injection], [])[0]
    assert m.kind == "hit" and not m.agent_correct


def test_a_label_without_a_category_matches_on_location_alone():
    loose = Label(file_path="a.py", line=10, agent="security", note="n")
    assert classify([finding(line=10, category="documentation")], [loose], [])[0].kind == "hit"


# --- traps fire only on the line they declare clean -------------------------


def test_a_trap_does_not_catch_a_finding_aimed_at_a_nearby_line():
    """Regression: the +/-3 snapping tolerance manufactured false positives.

    Twelve of thirteen recorded "false positives" were three to five lines from
    the trap, aimed squarely at the defective function below the clean one. The
    tolerance is right for deciding whether a real defect was found and wrong for
    deciding whether a clean line was flagged.
    """
    trap = Label(file_path="a.py", line=5, note="this line is fine")
    nearby = finding(line=8)
    nearby.line_end = 10
    assert classify([nearby], [], [trap])[0].kind == "unlabelled"


def test_a_trap_catches_a_finding_whose_range_covers_it():
    trap = Label(file_path="a.py", line=8, note="this line is fine")
    spanning = finding(line=5)
    spanning.line_end = 12
    assert classify([spanning], [], [trap])[0].kind == "false_positive"


def test_a_trap_catches_a_finding_that_cites_it_as_evidence():
    from pr_sentinel.domain.models import Evidence

    trap = Label(file_path="b.py", line=40, note="this line is fine")
    citing = finding(path="a.py", line=10)
    citing.evidence = [Evidence(kind="diff", file_path="b.py", line_start=40, line_end=40)]
    assert classify([citing], [], [trap])[0].kind == "false_positive"


# --- legitimate but optional -------------------------------------------------
#
# Mining 28 stable unlabelled findings out of the recorded runs found that most
# were neither required nor wrong: fifteen were the tests agent correctly noting a
# new function has no test, on a line labelled for something else. Requiring them
# would encode "always ask for tests" and penalise restraint; calling them false
# positives would label a true statement a lie.


def _case_with_allowed():
    return EvalCase(
        id="c",
        title="t",
        summary="",
        expected_decision=None,
        files=[],
        context_chunks=[],
        expected=[label(line=10, agent="security", category="injection")],
        must_not_find=[],
        may_find=[Label(file_path="a.py", line=10, agent="tests", category="test_coverage", note="optional")],
    )


def test_a_permitted_finding_costs_nothing():
    """It is not a hit, not a false positive, and not held against precision."""
    optional = finding(line=10, agent=AgentType.TESTS, category="test_coverage", severity="minor")
    required = finding(line=10, agent=AgentType.SECURITY, category="injection")
    case = _case_with_allowed()
    rep = score(
        [
            score_case(
                case, [required, optional], decision="auto_post", confidence=0.9, cost_usd=0.0, duration_ms=1
            )
        ],
        "t",
        {},
    )
    assert rep.overall.hits == 1
    assert rep.overall.allowed == 1
    assert rep.overall.unlabelled == 0
    assert rep.overall.precision_strict == pytest.approx(1.0)


def test_not_making_a_permitted_finding_costs_nothing_either():
    """Restraint must not be punished: recall is over required labels only."""
    case = _case_with_allowed()
    rep = score(
        [
            score_case(
                case,
                [finding(line=10, agent=AgentType.SECURITY, category="injection")],
                decision="auto_post",
                confidence=0.9,
                cost_usd=0.0,
                duration_ms=1,
            )
        ],
        "t",
        {},
    )
    assert rep.overall.recall == pytest.approx(1.0)
    assert rep.overall.allowed == 0


def test_a_permitted_label_does_not_rescue_an_unrelated_finding():
    case = _case_with_allowed()
    unrelated = finding(line=10, agent=AgentType.DOCS, category="documentation", severity="info")
    rep = score(
        [score_case(case, [unrelated], decision="auto_post", confidence=0.9, cost_usd=0.0, duration_ms=1)],
        "t",
        {},
    )
    assert rep.overall.unlabelled == 1 and rep.overall.allowed == 0


def test_every_case_carries_the_standing_permission_policy():
    """The two observations that are legitimate anywhere and required nowhere.

    Stated once in the builder rather than as a marker on every line they could
    apply to — nineteen such markers were removed when this replaced them.
    """
    cases = load_cases()
    assert cases
    for case in cases:
        assert ("tests", "test_coverage") in case.permitted_concerns, case.id
        assert ("docs", "documentation") in case.permitted_concerns, case.id


def test_a_permitted_concern_is_still_overridden_by_a_trap():
    """A case can declare a line where even a normally-permitted observation is wrong.

    Traps are checked before permissions, so `neg-clean-extract-method` still
    penalises asking for a test on a documented, tested pure extraction.
    """
    case = EvalCase(
        id="c",
        title="t",
        summary="",
        expected_decision=None,
        files=[],
        context_chunks=[],
        expected=[],
        must_not_find=[Label(file_path="a.py", line=10, note="nothing here")],
        permitted_concerns=[("tests", "test_coverage")],
    )
    normally_fine = finding(line=10, agent=AgentType.TESTS, category="test_coverage", severity="minor")
    rep = score(
        [score_case(case, [normally_fine], decision="escalate", confidence=0.5, cost_usd=0.0, duration_ms=1)],
        "t",
        {},
    )
    assert rep.overall.false_positives == 1 and rep.overall.allowed == 0


def test_a_label_accepts_alternative_categories():
    """One defect can have more than one fair reading.

    A traceback returned to a client is information disclosure and an
    error-handling mistake; rejecting the second cost a correct finding at 0.99.
    """
    either = Label(
        file_path="a.py", line=10, agent="security", category="input_validation|error_handling", note="n"
    )
    for cat in ("input_validation", "error_handling"):
        m = classify([finding(line=10, category=cat)], [either], [])[0]
        assert m.kind == "hit" and m.category_correct, cat
    assert classify([finding(line=10, category="documentation")], [either], [])[0].kind == "unlabelled"
