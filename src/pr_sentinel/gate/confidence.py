"""The confidence gate — where selectivity actually happens.

Everything upstream produces findings. This decides which of them are worth a
public comment, which are worth a human's time, and which are worth neither. Get
this wrong in one direction and the tool is noise; wrong in the other and it is
decorative.

Rules, in precedence order, and the order is deliberate:

  1. Budget exhausted        -> escalate. A partial review presented as complete
                                is worse than an honest "I ran out".
  2. An agent failed         -> escalate. Three quarters of a panel is not a panel.
  3. Critical security       -> escalate, never post. A comment describing a live
                                vulnerability on a public PR is a disclosure.
  4. Nothing worth posting   -> suppress. Checked before confidence, because
                                escalation routes findings to a human and
                                there are none to route.
  5. Overall confidence low  -> escalate.
  6. Otherwise               -> post, but only the findings that individually
                                clear both the confidence and the severity floor.

A finding has to clear two independent bars to be posted, because they answer
different questions. Confidence is "is this real". Severity is "does it matter".
An `info` finding at confidence 0.95 is very probably true and still not worth
interrupting anyone for — measured, that is exactly what the docs agent produces
when it is being honest.
"""

from __future__ import annotations

from ..config import get_settings
from ..domain.enums import SEVERITY_ORDER, Decision, EscalationReason, Severity
from ..domain.models import AgentVerdict, Finding


class GateResult:
    def __init__(
        self,
        decision: Decision,
        reason: EscalationReason | None,
        postable: list[Finding],
        priority: int,
        explanation: str,
    ) -> None:
        self.decision = decision
        self.reason = reason
        self.postable = postable
        self.priority = priority
        self.explanation = explanation


def evaluate(
    findings: list[Finding],
    verdicts: list[AgentVerdict],
    overall_confidence: float,
    *,
    budget_exhausted: bool = False,
    is_public_repo: bool = False,
) -> GateResult:
    settings = get_settings()

    if budget_exhausted:
        return GateResult(
            Decision.ESCALATE,
            EscalationReason.BUDGET_EXCEEDED,
            [],
            10,
            "Cost cap reached before the panel finished; review is incomplete.",
        )

    failed = [v for v in verdicts if not v.ok]
    if failed:
        names = ", ".join(str(v.agent) for v in failed)
        return GateResult(
            Decision.ESCALATE,
            EscalationReason.AGENT_FAILURE,
            [],
            20,
            f"Incomplete panel — {names} did not return.",
        )

    critical_security = [f for f in findings if f.severity is Severity.CRITICAL and f.is_security]
    if critical_security and settings.escalate_critical_security:
        where = "public repository" if is_public_repo else "pull request"
        return GateResult(
            Decision.ESCALATE,
            EscalationReason.CRITICAL_SECURITY,
            [],
            1,
            f"{len(critical_security)} critical security finding(s); not posted to the "
            f"{where} to avoid disclosure. Route through the security channel.",
        )

    floor = SEVERITY_ORDER.index(Severity(settings.post_min_severity))
    postable = [
        f
        for f in findings
        if f.confidence >= settings.finding_post_confidence and SEVERITY_ORDER.index(f.severity) >= floor
    ]

    # Before the confidence test, not after it. Escalation exists to route a
    # finding to a human; with nothing that clears the posting bars there is
    # nothing to route, and escalating says "look at this" about no content.
    #
    # The old order made that backwards in a way that only showed up under
    # measurement: a *confident* trivial finding suppressed, while an *uncertain*
    # trivial one escalated. Since low overall confidence is the normal state of
    # a review that found only weak signals, the effect was to spend a human on
    # exactly the pull requests with least to say — the noise failure this whole
    # gate exists to prevent.
    if not postable:
        return GateResult(
            Decision.SUPPRESS,
            None,
            [],
            100,
            "Nothing cleared the per-finding posting threshold. Staying quiet.",
        )

    if overall_confidence < settings.auto_post_confidence:
        return GateResult(
            Decision.ESCALATE,
            EscalationReason.LOW_CONFIDENCE,
            [],
            50,
            f"Overall confidence {overall_confidence:.2f} is below the "
            f"{settings.auto_post_confidence:.2f} auto-post threshold.",
        )

    # Two bars, reported separately: an operator reading this should know which
    # one each withheld finding failed, because they mean different things.
    held_low_confidence = sum(1 for f in findings if f.confidence < settings.finding_post_confidence)
    held_low_severity = sum(
        1
        for f in findings
        if f.confidence >= settings.finding_post_confidence and SEVERITY_ORDER.index(f.severity) < floor
    )
    reasons = []
    if held_low_confidence:
        reasons.append(f"{held_low_confidence} below the confidence threshold")
    if held_low_severity:
        reasons.append(f"{held_low_severity} below {settings.post_min_severity} severity")
    return GateResult(
        Decision.AUTO_POST,
        None,
        postable,
        100,
        f"Posting {len(postable)} finding(s)" + (f"; held back: {', '.join(reasons)}." if reasons else "."),
    )


def render_review_body(postable: list[Finding], summary: str, held_back: int, bundle: str) -> str:
    head = f"### pr-sentinel review\n\n{summary}. {len(postable)} comment(s) below."
    if held_back:
        head += f" {held_back} lower-confidence observation(s) withheld."
    agreed = [f for f in postable if len(f.agreeing) > 1]
    if agreed:
        head += (
            f"\n\n{len(agreed)} finding(s) were raised independently by more than one "
            "specialist, which is the strongest signal this tool produces."
        )
    head += (
        "\n\n<sub>Advisory only — this review never blocks a merge. "
        f"Reply to any comment to dispute it; disputes are recorded. "
        f"Prompt bundle `{bundle}`.</sub>"
    )
    return head
