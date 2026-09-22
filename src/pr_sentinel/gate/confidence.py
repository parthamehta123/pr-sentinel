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
  4. Overall confidence low  -> escalate.
  5. Otherwise               -> post, but only the findings that individually
                                clear the posting threshold.
"""

from __future__ import annotations

from ..config import get_settings
from ..domain.enums import Decision, EscalationReason, Severity
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

    if overall_confidence < settings.auto_post_confidence:
        return GateResult(
            Decision.ESCALATE,
            EscalationReason.LOW_CONFIDENCE,
            [],
            50,
            f"Overall confidence {overall_confidence:.2f} is below the "
            f"{settings.auto_post_confidence:.2f} auto-post threshold.",
        )

    postable = [f for f in findings if f.confidence >= settings.finding_post_confidence]
    if not postable:
        return GateResult(
            Decision.SUPPRESS,
            None,
            [],
            100,
            "Nothing cleared the per-finding posting threshold. Staying quiet.",
        )

    held = len(findings) - len(postable)
    return GateResult(
        Decision.AUTO_POST,
        None,
        postable,
        100,
        f"Posting {len(postable)} finding(s)"
        + (f"; {held} held back below the individual threshold." if held else "."),
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
