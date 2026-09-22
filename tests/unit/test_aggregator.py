"""Fan-in behaviour: what merges, what does not, and what that does to confidence."""

from __future__ import annotations

import pytest

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
