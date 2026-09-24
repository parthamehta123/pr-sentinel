"""The confidence gate. Precedence order is the contract."""

from __future__ import annotations

import pytest

from pr_sentinel.config import get_settings
from pr_sentinel.domain.enums import AgentType, Decision, EscalationReason, VerdictStatus
from pr_sentinel.domain.models import AgentVerdict, Finding
from pr_sentinel.gate import evaluate, render_review_body


def finding(agent=AgentType.CORRECTNESS, severity="major", confidence=0.85, category="logic", line=14):
    return Finding(
        agent=agent,
        category=category,
        severity=severity,
        file_path="a.py",
        line_start=line,
        line_end=line,
        title="Something is wrong",
        body="fix it",
        rationale=f"because of line {line}",
        confidence=confidence,
    )


def healthy_panel():
    return [AgentVerdict(agent=a, status=VerdictStatus.OK) for a in AgentType]


def test_confident_findings_are_posted():
    result = evaluate([finding()], healthy_panel(), 0.9)
    assert result.decision is Decision.AUTO_POST
    assert len(result.postable) == 1


def test_low_overall_confidence_escalates_instead_of_posting():
    """Something worth saying, but the panel is unsure of it: that is a human's call.

    The finding must itself clear the posting bar. This test used to pass a
    finding below that bar, which made it assert two different things at once —
    see the test below for the half it was accidentally covering.
    """
    s = get_settings()
    result = evaluate([finding(confidence=s.finding_post_confidence + 0.05)], healthy_panel(), 0.5)
    assert result.decision is Decision.ESCALATE
    assert result.reason is EscalationReason.LOW_CONFIDENCE


def test_nothing_worth_posting_suppresses_even_when_confidence_is_low():
    """No content to route means no escalation, however unsure the panel is.

    Low overall confidence is the normal state of a review that found only weak
    signals, so escalating on it regardless of whether anything clears the
    posting bars spends a human on precisely the pull requests with least to say.
    Measured, this was `neg-allowlisted-dynamic-sql`: one coverage nit at 0.55,
    nothing postable, and a human summoned to look at it.
    """
    s = get_settings()
    weak = finding(confidence=s.finding_post_confidence - 0.1)
    result = evaluate([weak], healthy_panel(), 0.4)
    assert result.decision is Decision.SUPPRESS
    assert result.reason is None
    assert result.postable == []


def test_individually_weak_findings_are_withheld_from_a_confident_review():
    s = get_settings()
    strong = finding(confidence=0.95)
    weak = finding(confidence=s.finding_post_confidence - 0.1, line=40)
    result = evaluate([strong, weak], healthy_panel(), 0.9)
    assert result.decision is Decision.AUTO_POST
    assert result.postable == [strong]


def test_a_critical_security_finding_is_never_posted():
    """Describing a live vulnerability in a PR thread is a disclosure."""
    result = evaluate(
        [finding(agent=AgentType.SECURITY, severity="critical", category="injection", confidence=0.99)],
        healthy_panel(),
        0.99,
    )
    assert result.decision is Decision.ESCALATE
    assert result.reason is EscalationReason.CRITICAL_SECURITY
    assert result.postable == []
    assert result.priority == 1


def test_a_critical_non_security_finding_is_still_posted():
    result = evaluate(
        [finding(agent=AgentType.CORRECTNESS, severity="critical", category="logic", confidence=0.95)],
        healthy_panel(),
        0.95,
    )
    assert result.decision is Decision.AUTO_POST


def test_a_merged_critical_keeps_the_security_category_the_primary_lost():
    """The invoices bucket: correctness won the wording, security had called it authz.

    The primary category is logic, so the old check posted a critical exposure
    on the pull request. The merge already recorded authz on `categories`.
    """
    merged = finding(agent=AgentType.CORRECTNESS, severity="critical", category="logic", confidence=0.99)
    merged.categories = ["authz", "logic"]
    merged.agreeing = ["correctness", "security"]
    result = evaluate([merged], healthy_panel(), 0.99)
    assert result.decision is Decision.ESCALATE
    assert result.reason is EscalationReason.CRITICAL_SECURITY
    assert result.postable == []


def test_a_failed_agent_outranks_high_confidence():
    panel = healthy_panel()
    panel[0] = AgentVerdict(agent=AgentType.SECURITY, status=VerdictStatus.TIMEOUT)
    result = evaluate([finding(confidence=0.99)], panel, 0.99)
    assert result.reason is EscalationReason.AGENT_FAILURE


def test_budget_exhaustion_outranks_everything():
    panel = healthy_panel()
    panel[0] = AgentVerdict(agent=AgentType.SECURITY, status=VerdictStatus.TIMEOUT)
    result = evaluate(
        [finding(agent=AgentType.SECURITY, severity="critical", category="injection")],
        panel,
        0.99,
        budget_exhausted=True,
    )
    assert result.reason is EscalationReason.BUDGET_EXCEEDED


def test_a_clean_diff_is_suppressed_rather_than_congratulated():
    result = evaluate([], healthy_panel(), 1.0)
    assert result.decision is Decision.SUPPRESS
    assert result.postable == []


def test_everything_below_threshold_means_staying_quiet():
    result = evaluate([finding(confidence=0.1)], healthy_panel(), 0.95)
    assert result.decision is Decision.SUPPRESS


def test_public_repositories_are_named_in_the_escalation_text():
    result = evaluate(
        [finding(agent=AgentType.SECURITY, severity="critical", category="secrets")],
        healthy_panel(),
        0.99,
        is_public_repo=True,
    )
    assert "public repository" in result.explanation


def test_review_body_declares_its_limits_and_provenance():
    body = render_review_body([finding()], "1 major", held_back=2, bundle="abc123")
    assert "never blocks a merge" in body
    assert "abc123" in body
    assert "2 lower-confidence" in body


def test_review_body_highlights_cross_agent_agreement():
    f = finding()
    f.agreeing = ["correctness", "security"]
    body = render_review_body([f], "1 major", held_back=0, bundle="abc")
    assert "more than one" in body


@pytest.mark.parametrize("confidence", [0.0, 0.699, 0.7, 0.701, 1.0])
def test_threshold_boundary_is_inclusive_upward(confidence):
    result = evaluate([finding(confidence=0.9)], healthy_panel(), confidence)
    expected = Decision.AUTO_POST if confidence >= get_settings().auto_post_confidence else Decision.ESCALATE
    assert result.decision is expected


# --- severity floor ---------------------------------------------------------
#
# From the live runs: the docs agent, once it stopped inflating severities,
# produced correct `info` findings at confidence 0.72 — comfortably over the
# confidence threshold and still not worth a reviewer's attention. Confidence
# answers "is this real"; severity answers "does it matter". Both must pass.


def test_an_info_finding_is_not_posted_however_confident_it_is():
    result = evaluate([finding(severity="info", confidence=0.99)], healthy_panel(), 0.99)
    assert result.decision is Decision.SUPPRESS
    assert result.postable == []


def test_a_minor_finding_still_posts():
    result = evaluate([finding(severity="minor", confidence=0.9)], healthy_panel(), 0.9)
    assert result.decision is Decision.AUTO_POST
    assert len(result.postable) == 1


def test_info_findings_are_filtered_out_of_a_mixed_review():
    strong = finding(severity="major", confidence=0.9)
    trivial = finding(severity="info", confidence=0.95, line=40)
    result = evaluate([strong, trivial], healthy_panel(), 0.9)
    assert result.postable == [strong]
    assert "below minor severity" in result.explanation


def test_the_two_hold_back_reasons_are_reported_separately():
    """An operator reading the summary should know which bar each finding failed."""
    result = evaluate(
        [
            finding(severity="major", confidence=0.95),
            finding(severity="major", confidence=0.1, line=40),
            finding(severity="info", confidence=0.95, line=80),
        ],
        healthy_panel(),
        0.9,
    )
    assert "below the confidence threshold" in result.explanation
    assert "below minor severity" in result.explanation


def test_the_floor_is_configurable(monkeypatch):
    monkeypatch.setenv("POST_MIN_SEVERITY", "info")
    get_settings.cache_clear()
    result = evaluate([finding(severity="info", confidence=0.9)], healthy_panel(), 0.9)
    assert result.decision is Decision.AUTO_POST
