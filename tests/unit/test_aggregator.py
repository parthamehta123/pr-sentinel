"""Fan-in behaviour: what merges, what does not, and what that does to confidence."""

from __future__ import annotations

import pytest

from pr_sentinel.config import get_settings
from pr_sentinel.domain.enums import AgentType, Severity, VerdictStatus
from pr_sentinel.domain.models import AgentVerdict, Finding
from pr_sentinel.orchestration import aggregator


def finding(
    agent,
    *,
    line=14,
    category="injection",
    severity="critical",
    confidence=0.8,
    title="SQL built by string interpolation",
    path="billing/handler.py",
):
    return Finding(
        agent=agent,
        category=category,
        severity=severity,
        file_path=path,
        line_start=line,
        line_end=line,
        title=title,
        body="body",
        rationale=f"because line {line} interpolates",
        confidence=confidence,
    )


def verdict(agent, findings, status=VerdictStatus.OK):
    return AgentVerdict(agent=agent, status=status, findings=findings)


def test_two_agents_agreeing_raises_confidence_above_either_alone():
    merged, _ = aggregator.aggregate(
        [
            verdict(AgentType.SECURITY, [finding(AgentType.SECURITY, confidence=0.7)]),
            verdict(AgentType.CORRECTNESS, [finding(AgentType.CORRECTNESS, confidence=0.7)]),
        ]
    )
    assert len(merged) == 1
    # noisy-OR: 1 - 0.3*0.3
    assert merged[0].confidence == pytest.approx(0.91, abs=0.01)
    assert merged[0].agreeing == ["correctness", "security"]


def test_one_agent_repeating_itself_earns_no_boost():
    """A model saying the same thing twice is not two pieces of evidence."""
    merged, _ = aggregator.aggregate(
        [
            verdict(
                AgentType.SECURITY,
                [
                    finding(AgentType.SECURITY, confidence=0.7),
                    finding(AgentType.SECURITY, confidence=0.7),
                ],
            ),
        ]
    )
    assert len(merged) == 1
    assert merged[0].confidence == pytest.approx(0.7)
    assert merged[0].agreeing == ["security"]


def test_confidence_never_reaches_certainty():
    merged, _ = aggregator.aggregate(
        [
            verdict(AgentType.SECURITY, [finding(AgentType.SECURITY, confidence=0.99)]),
            verdict(AgentType.CORRECTNESS, [finding(AgentType.CORRECTNESS, confidence=0.99)]),
            verdict(AgentType.TESTS, [finding(AgentType.TESTS, confidence=0.99)]),
        ]
    )
    assert merged[0].confidence <= 0.99


def test_findings_on_different_lines_do_not_merge():
    merged, _ = aggregator.aggregate(
        [
            verdict(AgentType.SECURITY, [finding(AgentType.SECURITY, line=14)]),
            verdict(AgentType.CORRECTNESS, [finding(AgentType.CORRECTNESS, line=40)]),
        ]
    )
    assert len(merged) == 2


def test_findings_in_different_files_do_not_merge():
    merged, _ = aggregator.aggregate(
        [
            verdict(AgentType.SECURITY, [finding(AgentType.SECURITY, path="a.py")]),
            verdict(AgentType.CORRECTNESS, [finding(AgentType.CORRECTNESS, path="b.py")]),
        ]
    )
    assert len(merged) == 2


def test_overlapping_lines_with_unrelated_titles_stay_separate():
    merged, _ = aggregator.aggregate(
        [
            verdict(
                AgentType.SECURITY,
                [
                    finding(
                        AgentType.SECURITY, category="injection", title="SQL built by string interpolation"
                    )
                ],
            ),
            verdict(
                AgentType.DOCS,
                [
                    finding(
                        AgentType.DOCS,
                        category="documentation",
                        severity="info",
                        title="Missing docstring here",
                    )
                ],
            ),
        ]
    )
    assert len(merged) == 2


def test_the_merged_finding_keeps_the_most_severe_wording():
    merged, _ = aggregator.aggregate(
        [
            verdict(
                AgentType.SECURITY,
                [finding(AgentType.SECURITY, severity="critical", title="SQL built by string interpolation")],
            ),
            verdict(
                AgentType.CORRECTNESS,
                [finding(AgentType.CORRECTNESS, severity="minor", title="SQL string interpolation here")],
            ),
        ]
    )
    assert merged[0].severity is Severity.CRITICAL
    assert merged[0].title == "SQL built by string interpolation"


def test_results_are_ordered_by_severity_then_confidence():
    merged, _ = aggregator.aggregate(
        [
            verdict(
                AgentType.DOCS,
                [
                    finding(
                        AgentType.DOCS,
                        line=90,
                        severity="info",
                        category="documentation",
                        title="Docs",
                        confidence=0.99,
                    )
                ],
            ),
            verdict(
                AgentType.SECURITY,
                [finding(AgentType.SECURITY, line=14, severity="critical", confidence=0.6)],
            ),
            verdict(
                AgentType.TESTS,
                [
                    finding(
                        AgentType.TESTS,
                        line=50,
                        severity="major",
                        category="test_coverage",
                        title="Untested",
                        confidence=0.9,
                    )
                ],
            ),
        ]
    )
    assert [str(f.severity) for f in merged] == ["critical", "major", "info"]


def test_a_failed_agent_discounts_the_whole_review():
    healthy = aggregator.aggregate(
        [
            verdict(a, [finding(a, line=10 + i * 10, category="logic", title=f"t{i}")])
            for i, a in enumerate(AgentType)
        ]
    )[1]
    degraded = aggregator.aggregate(
        [
            verdict(AgentType.SECURITY, [], VerdictStatus.TIMEOUT),
            verdict(AgentType.CORRECTNESS, [finding(AgentType.CORRECTNESS, confidence=0.9)]),
            verdict(AgentType.TESTS, []),
            verdict(AgentType.DOCS, []),
        ]
    )[1]
    assert degraded < healthy


def test_a_clean_diff_from_a_full_panel_is_maximally_confident():
    _, confidence = aggregator.aggregate([verdict(a, []) for a in AgentType])
    assert confidence == 1.0


def test_a_clean_diff_from_a_broken_panel_is_not():
    _, confidence = aggregator.aggregate(
        [
            verdict(AgentType.SECURITY, [], VerdictStatus.FAILED),
            verdict(AgentType.CORRECTNESS, []),
            verdict(AgentType.TESTS, []),
            verdict(AgentType.DOCS, []),
        ]
    )
    assert confidence == pytest.approx(0.75)


def test_findings_from_failed_agents_are_not_counted():
    merged, _ = aggregator.aggregate(
        [
            verdict(AgentType.SECURITY, [finding(AgentType.SECURITY)], VerdictStatus.FAILED),
        ]
    )
    assert merged == []


def test_summary_names_unavailable_agents():
    text = aggregator.summarise(
        [finding(AgentType.CORRECTNESS)],
        [
            verdict(AgentType.SECURITY, [], VerdictStatus.TIMEOUT),
            verdict(AgentType.CORRECTNESS, [finding(AgentType.CORRECTNESS)]),
        ],
    )
    assert "security" in text and "1 critical" in text


# --- concern families -------------------------------------------------------
#
# Regression tests from the first live run: matching on identical category alone
# produced 3.5 findings per labelled defect, because four specialists name the
# same problem four different ways.


def test_agents_naming_one_defect_differently_still_merge():
    """security says `injection`, correctness says `input_validation`. One defect."""
    merged, _ = aggregator.aggregate(
        [
            verdict(
                AgentType.SECURITY,
                [
                    finding(
                        AgentType.SECURITY,
                        category="injection",
                        title="SQL built by string interpolation",
                        confidence=0.9,
                    )
                ],
            ),
            verdict(
                AgentType.CORRECTNESS,
                [
                    finding(
                        AgentType.CORRECTNESS,
                        category="input_validation",
                        title="Unvalidated identifier reaches the query",
                        confidence=0.7,
                    )
                ],
            ),
        ]
    )
    assert len(merged) == 1
    assert merged[0].agreeing == ["correctness", "security"]
    assert merged[0].confidence > 0.9  # agreement still boosts


def test_correctness_concerns_on_one_line_collapse():
    merged, _ = aggregator.aggregate(
        [
            verdict(
                AgentType.CORRECTNESS,
                [
                    finding(AgentType.CORRECTNESS, category="logic", title="Off by one"),
                    finding(AgentType.CORRECTNESS, category="error_handling", title="Unhandled failure"),
                ],
            ),
        ]
    )
    assert len(merged) == 1


def test_different_families_on_the_same_line_stay_separate():
    """A missing docstring and an injection are two things. Merging buries one."""
    merged, _ = aggregator.aggregate(
        [
            verdict(
                AgentType.SECURITY,
                [finding(AgentType.SECURITY, category="injection", title="SQL injection here")],
            ),
            verdict(
                AgentType.DOCS,
                [
                    finding(
                        AgentType.DOCS,
                        category="documentation",
                        severity="info",
                        title="No docstring on this function",
                    )
                ],
            ),
        ]
    )
    assert len(merged) == 2


def test_test_concerns_do_not_absorb_security_concerns():
    merged, _ = aggregator.aggregate(
        [
            verdict(
                AgentType.SECURITY,
                [finding(AgentType.SECURITY, category="secrets", title="Hardcoded credential")],
            ),
            verdict(
                AgentType.TESTS,
                [
                    finding(
                        AgentType.TESTS,
                        category="test_coverage",
                        severity="minor",
                        title="No test covers this",
                    )
                ],
            ),
        ]
    )
    assert len(merged) == 2


def test_the_other_category_does_not_act_as_a_universal_solvent():
    """`other` is the fallback for an unparseable category; it must not swallow things."""
    merged, _ = aggregator.aggregate(
        [
            verdict(
                AgentType.SECURITY,
                [finding(AgentType.SECURITY, category="other", title="Something unusual here")],
            ),
            verdict(
                AgentType.DOCS,
                [
                    finding(
                        AgentType.DOCS,
                        category="other",
                        severity="info",
                        title="A completely different observation",
                    )
                ],
            ),
        ]
    )
    assert len(merged) == 1  # same category still merges
    merged2, _ = aggregator.aggregate(
        [
            verdict(
                AgentType.SECURITY,
                [finding(AgentType.SECURITY, category="other", title="Something unusual here")],
            ),
            verdict(
                AgentType.DOCS,
                [
                    finding(
                        AgentType.DOCS,
                        category="documentation",
                        severity="info",
                        title="A different observation",
                    )
                ],
            ),
        ]
    )
    assert len(merged2) == 2  # other + a real family does not merge


# --- collapsing a repeated recommendation -----------------------------------
#
# `_merge` only joins findings that overlap on the same lines, which does nothing
# for one agent making the same request about eight different functions. A human
# reviewer says "this needs tests" once and lists what.


def coverage(agent=AgentType.TESTS, path="a.py", line=10, confidence=0.8, severity="major"):
    return Finding(
        agent=agent,
        category="test_coverage",
        severity=severity,
        file_path=path,
        line_start=line,
        line_end=line,
        title=f"No test covers {path}:{line}",
        body="add one",
        rationale=f"nothing exercises {path}:{line}",
        confidence=confidence,
    )


def test_a_repeated_coverage_ask_becomes_one_finding():
    merged, _ = aggregator.aggregate(
        [
            verdict(AgentType.TESTS, [coverage(path=f"m{i}.py", line=10 + i) for i in range(6)]),
        ]
    )
    assert len(merged) == 1
    assert "and 5 more" in merged[0].title


def test_every_folded_location_survives_as_evidence():
    """Consolidation must not lose information, only threads."""
    findings = [coverage(path=f"m{i}.py", line=10 + i) for i in range(5)]
    merged, _ = aggregator.aggregate([verdict(AgentType.TESTS, findings)])
    cited = {(e.file_path, e.line_start) for e in merged[0].evidence}
    for f in findings:
        assert (f.file_path, f.line_start) in cited or f.file_path == merged[0].file_path


def test_the_body_lists_the_other_places():
    merged, _ = aggregator.aggregate(
        [
            verdict(AgentType.TESTS, [coverage(path=f"m{i}.py", line=10 + i) for i in range(4)]),
        ]
    )
    assert "m1.py:11" in merged[0].body
    assert "one piece of work" in merged[0].body


def test_a_couple_of_asks_are_left_where_the_work_is():
    """Below the threshold, specific anchoring is more useful than consolidation."""
    merged, _ = aggregator.aggregate(
        [
            verdict(AgentType.TESTS, [coverage(path="a.py", line=10), coverage(path="b.py", line=20)]),
        ]
    )
    assert len(merged) == 2


def test_the_threshold_is_configurable(monkeypatch):
    monkeypatch.setenv("COLLAPSE_REPEATED_AFTER", "2")
    get_settings.cache_clear()
    merged, _ = aggregator.aggregate(
        [
            verdict(AgentType.TESTS, [coverage(path="a.py", line=10), coverage(path="b.py", line=20)]),
        ]
    )
    assert len(merged) == 1


def test_distinct_defects_are_never_collapsed():
    """Two SQL injections in two files are two things to fix, not one comment."""
    merged, _ = aggregator.aggregate(
        [
            verdict(
                AgentType.SECURITY,
                [
                    finding(
                        AgentType.SECURITY,
                        category="injection",
                        path=f"m{i}.py",
                        line=10 + i,
                        title=f"SQL injection in m{i}",
                    )
                    for i in range(5)
                ],
            ),
        ]
    )
    assert len(merged) == 5


def test_test_quality_findings_are_not_collapsed():
    """`this test cannot fail` is a distinct defect per test, with a distinct fix."""
    merged, _ = aggregator.aggregate(
        [
            verdict(
                AgentType.TESTS,
                [
                    finding(
                        AgentType.TESTS,
                        category="test_quality",
                        severity="major",
                        path="t.py",
                        line=10 + i * 10,
                        title=f"Test {i} has no assertion",
                    )
                    for i in range(5)
                ],
            ),
        ]
    )
    assert len(merged) == 5


def test_collapsing_keeps_the_most_severe_and_the_highest_confidence():
    merged, _ = aggregator.aggregate(
        [
            verdict(
                AgentType.TESTS,
                [
                    coverage(path="a.py", line=10, severity="minor", confidence=0.6),
                    coverage(path="b.py", line=20, severity="major", confidence=0.7),
                    coverage(path="c.py", line=30, severity="minor", confidence=0.95),
                ],
            ),
        ]
    )
    assert len(merged) == 1
    assert str(merged[0].severity) == "major"
    assert merged[0].confidence == pytest.approx(0.95)


def test_each_agent_collapses_its_own_asks_separately():
    """Grouping is per agent: two agents asking for tests give two comments.

    Note the distinct line ranges. Findings from two agents on the *same* lines
    are the cross-agent agreement case and are joined by `_merge` first, which is
    correct and is a different mechanism from this one.
    """
    merged, _ = aggregator.aggregate(
        [
            verdict(
                AgentType.TESTS,
                [coverage(agent=AgentType.TESTS, path=f"m{i}.py", line=10 + i) for i in range(4)],
            ),
            verdict(
                AgentType.DOCS,
                [coverage(agent=AgentType.DOCS, path=f"d{i}.py", line=50 + i) for i in range(4)],
            ),
        ]
    )
    assert len(merged) == 2
    assert {tuple(f.agreeing) or (str(f.agent),) for f in merged} == {("tests",), ("docs",)}


def test_a_merged_finding_carries_every_contributing_category():
    """The primary's category is what gets posted; the full set is kept too.

    Without it, a merged finding is judged only on the framing of whichever
    contributor happened to win the merge — which scored the security agent at
    zero attribution on six advisory cases it had found every one of.
    """
    merged, _ = aggregator.aggregate(
        [
            verdict(
                AgentType.SECURITY,
                [
                    finding(
                        AgentType.SECURITY, category="crypto", title="Plaintext by default", confidence=0.9
                    )
                ],
            ),
            verdict(
                AgentType.CORRECTNESS,
                [
                    finding(
                        AgentType.CORRECTNESS,
                        category="input_validation",
                        title="Plaintext default here",
                        confidence=0.95,
                    )
                ],
            ),
        ]
    )
    assert len(merged) == 1
    assert merged[0].categories == ["crypto", "input_validation"]
    assert merged[0].agreeing == ["correctness", "security"]


def test_an_unmerged_finding_has_no_category_list():
    """Only merges populate it; a single finding is described by its own category."""
    merged, _ = aggregator.aggregate(
        [
            verdict(AgentType.SECURITY, [finding(AgentType.SECURITY, category="injection")]),
        ]
    )
    assert merged[0].categories == []
