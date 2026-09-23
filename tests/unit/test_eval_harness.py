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
    """Nine judged findings at 0.9 confidence, of which eight are right."""
    spec = [("hit", 0.9)] * 8 + [("fp", 0.9)]
    rep = score([_result(spec)], "test", {})
    assert rep.ece < 0.05


def test_systematic_overconfidence_is_measured():
    """Ten judged findings at 0.95; only two are right."""
    spec = [("hit", 0.95)] * 2 + [("fp", 0.95)] * 8
    rep = score([_result(spec)], "test", {})
    assert rep.ece > 0.6
    hot = next(b for b in rep.calibration if b.count)
    assert hot.hit_rate - hot.mean_confidence < -0.5


def test_unjudged_findings_do_not_create_false_overconfidence():
    """The same shape with unlabelled instead of false positives is not evidence.

    This is the distinction the metric now draws: eight wrong answers at 0.95 is
    overconfidence, eight unjudged findings at 0.95 is an unlabelled backlog.
    """
    spec = [("hit", 0.95)] * 2 + [("unlabelled", 0.95)] * 8
    rep = score([_result(spec)], "test", {})
    assert rep.ece < 0.1


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


def test_every_case_has_an_author_body_that_is_not_the_summary():
    """Regression: empty bodies unanchor the gate; summaries as bodies leak the answer.

    After the body leak closed, every case rendered "(no description)" and gate
    decision match fell from a flat 1.000 to 0.900/0.900/0.850, mean 0.883,
    mostly neg-* escalations at confidence 0.55 to 0.60. The
    summary field is documentation for readers of cases.py and must never reach
    the model (14 of them narrated the defect). Each case needs a written body:
    what an author would plausibly say, without narrating the defect.
    """
    cases = load_cases()
    assert cases
    for case in cases:
        assert case.body.strip(), f"{case.id}: empty body — panel sees '(no description)'"
        assert case.body != case.summary, f"{case.id}: body equals summary — reintroduces the leak"
        assert case.pull_request().body == case.body, f"{case.id}: PR body is not case.body"


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


def test_a_partial_outage_is_refused_too():
    """Regression: the credit balance ran out part way through a repeat.

    Two runs completed; the third lost 31 of 53 cases. Nothing refused it, and
    because the summary reports the last run, the headline recall read 0.349 for
    a reviewer that had just scored 1.000 twice. A partial outage is more
    dangerous than a total one, because it looks like a measurement.
    """
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
        for _ in range(31)
    ]
    alive = [
        score_case(
            case,
            [finding()],
            decision="auto_post",
            confidence=0.9,
            cost_usd=0.01,
            duration_ms=1,
            failed_agents=[],
        )
        for _ in range(22)
    ]
    with pytest.raises(EvalRunFailed, match="lost their whole panel"):
        _refuse_if_everything_failed(dead + alive)


def test_a_few_failed_cases_are_still_scored():
    """Degraded is not the same as dead. Only an outage-sized share is refused."""
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
    results = [
        score_case(
            case,
            [],
            decision="escalate",
            confidence=0.0,
            cost_usd=0.0,
            duration_ms=1,
            failed_agents=[str(a) for a in ALL_AGENTS],
        )
    ]
    results += [
        score_case(
            case,
            [finding()],
            decision="auto_post",
            confidence=0.9,
            cost_usd=0.01,
            duration_ms=1,
            failed_agents=[],
        )
        for _ in range(20)
    ]
    _refuse_if_everything_failed(results)  # does not raise


def test_attribution_asks_who_contributed_not_who_won_the_merge():
    """Regression: the security agent scored 0.000 on six advisory cases it found.

    The aggregator keeps the highest-severity contributor as primary, so those
    merged findings carried correctness's name. Comparing the label against that
    alone made a correct attribution look like a miss every time.
    """
    merged = finding(line=10, agent=AgentType.CORRECTNESS, category="input_validation")
    merged.agreeing = ["correctness", "security"]
    merged.categories = ["crypto", "input_validation"]
    m = classify([merged], [label(line=10, agent="security", category="crypto")], [])[0]
    assert m.kind == "hit"
    assert m.agent_correct, "security contributed, so attribution is correct"
    assert m.category_correct


def test_a_merged_finding_matches_on_any_contributing_concern():
    merged = finding(line=10, agent=AgentType.CORRECTNESS, category="logic")
    merged.agreeing = ["correctness", "security"]
    merged.categories = ["crypto", "logic"]
    assert classify([merged], [label(line=10, agent="security", category="crypto")], [])[0].kind == "hit"


def test_attribution_is_still_wrong_when_the_expected_agent_never_contributed():
    solo = finding(line=10, agent=AgentType.TESTS, category="test_coverage", severity="minor")
    m = classify([solo], [label(line=10, agent="security", category="test_coverage")], [])[0]
    assert m.kind == "hit" and not m.agent_correct


def test_calibration_ignores_findings_the_set_has_not_judged():
    """An unknown is not a wrong answer.

    Unlabelled findings cluster at low confidence — the reviewer is least sure
    about exactly the things nobody has labelled — so scoring them as failures
    made the 0.50-0.80 bins read as badly overconfident when what they mostly
    contained was "not judged yet".
    """
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
    findings = [finding(line=10, confidence=0.6)]  # a hit at 0.6
    findings += [finding(line=500 + i * 10, confidence=0.6) for i in range(8)]  # unjudged
    rep = score(
        [score_case(case, findings, decision="auto_post", confidence=0.6, cost_usd=0.0, duration_ms=1)],
        "t",
        {},
    )
    scored = next(b for b in rep.calibration if b.count)
    assert scored.count == 1, "only the judged finding informs calibration"
    assert scored.hit_rate == 1.0


def test_rescore_refuses_to_overwrite_the_recorded_run(tmp_path):
    """A recorded run is the raw measurement and must stay immutable.

    Rescoring answers "what would today's labels make of that run" — a derived
    number. Writing it back over the source destroys the only record of the
    original scoring, so every later comparison "against the baseline" silently
    compares against post-hoc relabelling. This has happened once; the guard is
    cheaper than noticing it again.
    """
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    recorded = tmp_path / "run.json"
    recorded.write_text('{"per_case": []}')
    before = recorded.read_text()

    out = subprocess.run(
        [sys.executable, str(root / "scripts" / "rescore.py"), str(recorded), "--save", str(recorded)],
        capture_output=True,
        text=True,
    )
    assert out.returncode != 0
    assert "refusing to write over" in out.stderr
    assert recorded.read_text() == before


def _run_eval_with_outage_on(run_number: int, *, repeat: int, monkeypatch):
    """Drive the real run_eval, failing the guard on `run_number`."""
    import asyncio

    from pr_sentinel.evaluation import runner as R

    seen = {"n": 0}

    def fake_guard(results):
        seen["n"] += 1
        if seen["n"] == run_number:
            raise R.EvalRunFailed("simulated outage")

    async def fake_run_case(case, engine_name, budget_cap_usd, **kwargs):
        return object()

    monkeypatch.setattr(R, "_refuse_if_everything_failed", fake_guard)
    monkeypatch.setattr(R, "run_case", fake_run_case)
    fake_case = type("C", (), {"id": "fake-case"})()
    monkeypatch.setattr(R, "load_cases", lambda only=None: [fake_case])
    monkeypatch.setattr(R, "score", lambda results, **kw: {"run": seen["n"]})
    monkeypatch.setattr(R, "get_provider", lambda: type("P", (), {"name": "fake"})())
    return asyncio.run(R.run_eval(repeat=repeat))


def test_outage_in_a_later_run_keeps_the_runs_that_completed(monkeypatch):
    """A credit exhaustion in run 2 must not retract run 1.

    Run 1 completed, every case in it got its whole panel, and it cost real
    money. Discarding it loses good data to guard against bad data that is
    already being discarded on its own. Seen for real: run 1 of a --repeat 3
    finished cleanly, run 2 lost 39 of 63 panels, and the exception threw away
    run 1 along with it.
    """
    reports = _run_eval_with_outage_on(2, repeat=3, monkeypatch=monkeypatch)
    assert len(reports) == 1, "the completed run should survive the later outage"


def test_outage_in_the_first_run_still_refuses(monkeypatch):
    """With nothing completed there is nothing to keep, so it must propagate."""
    import pytest

    from pr_sentinel.evaluation.runner import EvalRunFailed

    with pytest.raises(EvalRunFailed):
        _run_eval_with_outage_on(1, repeat=3, monkeypatch=monkeypatch)


def test_a_clean_repeat_still_returns_every_run(monkeypatch):
    """The guard must not cost a run when nothing goes wrong."""
    reports = _run_eval_with_outage_on(99, repeat=3, monkeypatch=monkeypatch)
    assert len(reports) == 3


def _report_with_unlabelled(specs):
    """Build a minimal EvalReport carrying the given unlabelled findings.

    `specs` is a list of (case_id, path, line, agent, title, confidence).
    """
    from types import SimpleNamespace

    by_case: dict[str, list] = {}
    for case_id, path, line, agent, title, conf in specs:
        finding = SimpleNamespace(file_path=path, line_start=line, agent=agent, title=title, confidence=conf)
        by_case.setdefault(case_id, []).append(SimpleNamespace(finding=finding))
    cases = [SimpleNamespace(case_id=cid, unlabelled=matches) for cid, matches in by_case.items()]
    return SimpleNamespace(cases=cases)


def test_unlabelled_union_surfaces_findings_the_last_run_did_not_have():
    """The last run printing nothing must not hide the earlier runs.

    This is the real shape that exposed it: run 3 produced no unlabelled
    findings, so `--verbose` printed an empty block while four sat in runs 1
    and 2, recoverable only from the saved JSON.
    """
    from pr_sentinel.evaluation.report import unlabelled_union

    r1 = _report_with_unlabelled(
        [
            ("intro-requests-poolmanager", "requests/sessions.py", 134, "security", "verify dropped", 0.62),
            ("tst-time-dependent-flaky", "tests/test_clock.py", 4, "tests", "wall clock", 0.55),
        ]
    )
    r2 = _report_with_unlabelled(
        [
            ("intro-requests-poolmanager", "requests/sessions.py", 134, "security", "verify dropped", 0.70),
            ("cor-check-then-act-race", "svc/lock.py", 9, "correctness", "TOCTOU", 0.60),
        ]
    )
    r3 = _report_with_unlabelled([])

    out = unlabelled_union([r1, r2, r3])
    assert "3 distinct" in out
    # Seen twice, so it sorts first and is marked as recurring.
    assert "[2/3] intro-requests-poolmanager" in out
    assert "[1/3] cor-check-then-act-race" in out
    assert "[1/3] tst-time-dependent-flaky" in out
    # Mean confidence across the runs it appeared in, not the last value.
    assert "(0.66)" in out
    # And it must come first, since recurrence is what deserves attention.
    assert out.index("intro-requests-poolmanager") < out.index("cor-check-then-act-race")


def test_unlabelled_union_is_silent_for_a_single_run():
    """render() already covers one run; printing it twice is noise."""
    from pr_sentinel.evaluation.report import unlabelled_union

    r = _report_with_unlabelled([("c", "a.py", 1, "docs", "t", 0.5)])
    assert unlabelled_union([r]) == ""


def test_unlabelled_union_is_silent_when_there_is_nothing_to_report():
    from pr_sentinel.evaluation.report import unlabelled_union

    empty = _report_with_unlabelled([])
    assert unlabelled_union([empty, empty, empty]) == ""


def test_results_md_per_run_claims_match_the_recorded_runs():
    """Numbers quoted in RESULTS.md must be computed, not typed from memory.

    RESULTS.md is hand-written prose around hand-transcribed tables, and a
    mistyped digit in it is invisible — it looks exactly like a measurement.
    Every recorded run is on disk, so the per-run triples it states can simply
    be recomputed and looked for.

    This checks the shape most often quoted (the per-run unlabelled counts) for
    every recorded run. It deliberately does not try to parse the tables: it
    recomputes the triple and asserts the document states it somewhere, which is
    robust to how the row happens to be formatted.
    """
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    results = (root / "tests" / "eval" / "RESULTS.md").read_text()
    recorded = sorted((root / "tests" / "eval" / "recorded").glob("*.json"))
    assert recorded, "no recorded runs to check against"

    for path in recorded:
        payload = json.loads(path.read_text())
        if not isinstance(payload, dict):
            continue  # not a recorded run
        runs = payload.get("runs") or [payload]
        if len(runs) < 2:
            continue
        triple = ", ".join(str(sum(c["unlabelled"] for c in r["per_case"])) for r in runs)
        assert triple in results, (
            f"{path.name} has per-run unlabelled counts of '{triple}', "
            f"which RESULTS.md does not state anywhere"
        )


def test_no_body_narrates_its_own_defect():
    """A body must describe the change, not the bug hiding in it.

    The summary leak was found by reading 63 summaries by hand. This is the same
    check, computed: how much of each EXPECT note's vocabulary the body reuses. A
    body that restates its label is handing the panel the answer, and the numbers
    it produces then measure the fixture rather than the reviewer.

    The threshold is loose on purpose. Real overlap is low — median 0.10 across
    the set, with the highest legitimate case at 0.43 where the body states a
    return-type change the diff shows anyway. This fires on a body that reuses
    most of its label's wording, which is what narration looks like, not on one
    that happens to name the same function.
    """
    import re

    stop = set(
        "the a an and or of to in is are for with that this it its on by as be we our "
        "you can not no if then so at from will would should when where which what "
        "into out up down over under also only just now new add adds added use uses "
        "using make makes made set sets".split()
    )

    def terms(text: str) -> set[str]:
        return {w for w in re.findall(r"[a-z_]{4,}", (text or "").lower())} - stop

    worst: tuple[float, str] = (0.0, "")
    for case in load_cases():
        body = terms(case.body)
        for label in case.expected:
            note = terms(label.note)
            if not note:
                continue
            overlap = len(body & note) / len(note)
            if overlap > worst[0]:
                worst = (overlap, f"{case.id} :: {label.note[:70]}")
            assert overlap < 0.60, (
                f"{case.id}: body reuses {overlap:.0%} of its label's wording — "
                f"it is narrating the defect rather than describing the change.\n"
                f"  body: {case.body}\n  note: {label.note}"
            )
    assert worst[0] > 0.0, "overlap never computed — the check is not running"
