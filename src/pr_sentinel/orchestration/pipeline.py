"""The review, end to end.

    fetch -> persist -> retrieve -> panel -> aggregate -> gate -> act

Every step writes to the event spine, every step can fail without taking the
review with it, and the whole thing is resumable: agent verdicts are persisted as
they land, so a retry after a crash re-runs only what is missing. That is the
cheapest useful form of durable execution, and it does not require the
orchestration framework to provide one (ADR-0004).
"""

from __future__ import annotations

import uuid

from ..db.repositories import reviews as review_repo
from ..domain.enums import ALL_AGENTS, AgentType, Decision, ReviewStatus
from ..domain.models import PullRequestContext, ReviewOutcome, WebhookJob
from ..events.spine import EventSpine
from ..forge.github import GitHubClient
from ..gate import evaluate, render_review_body
from ..llm.registry import bundle_version
from ..logging import get_logger
from ..reliability.budget import BudgetExceeded, BudgetGuard
from ..retrieval.context import build_context
from . import aggregator
from .engine import get_engine

log = get_logger(__name__)


async def review_pull_request(
    job: WebhookJob, engine_name: str | None = None, *, post: bool = True
) -> ReviewOutcome:
    """Review one pull request.

    `post=False` computes everything and records it, but never writes to GitHub.
    It is a separate argument rather than "unset the token" because the token is
    also what authenticates *reads* — dropping it to stay safe silently demotes
    the run to unauthenticated fetches and a 60-per-hour rate limit.
    """
    spine = EventSpine(delivery_id=job.delivery_id, repo_full_name=job.repo_full_name)

    # Ingress normally writes the delivery row before enqueueing, but the pipeline
    # must not depend on that: `replay` and any future trigger reach here without
    # passing through ingress, and reviews.delivery_id is a foreign key. The insert
    # is ON CONFLICT DO NOTHING, so the webhook path is unaffected.
    await review_repo.record_delivery(job)
    repo_id = await review_repo.upsert_repository(job.repo_full_name, job.repo_github_id)

    async with GitHubClient() as gh:
        async with spine.span("review.fetch", repo=job.repo_full_name, pr=job.pr_number):
            pr = await gh.build_context(job.repo_full_name, job.pr_number, job.repo_github_id)

        pr_id = await review_repo.upsert_pull_request(repo_id, pr)
        review_id, is_new = await review_repo.open_review(pr_id, job.delivery_id, bundle_version())
        spine.review_id = review_id
        log.info("review.open", review_id=str(review_id), new=is_new, files=len(pr.files))

        budget = await BudgetGuard.load(review_id)
        outcome = await _run(pr, repo_id, review_id, spine, budget, engine_name)

        await _act(gh, pr, review_id, outcome, spine, post=post)
        return outcome


async def _run(
    pr: PullRequestContext,
    repo_id: int,
    review_id: uuid.UUID,
    spine: EventSpine,
    budget: BudgetGuard,
    engine_name: str | None,
) -> ReviewOutcome:
    async with spine.span("review.retrieve"):
        ctx = await build_context(pr, repo_id, spine)

    # Resume: agents that already returned on an earlier attempt are not re-run.
    done = await review_repo.completed_agents(review_id)
    todo: list[AgentType] = [a for a in ALL_AGENTS if str(a) not in done]
    if done:
        log.info("review.resume", review_id=str(review_id), skipping=sorted(done))
        await spine.decision("review.resumed", completed=sorted(done), remaining=[str(a) for a in todo])

    budget_exhausted = False
    verdicts = []
    if todo:
        engine = get_engine(engine_name)
        try:
            verdicts = await engine.run_panel(ctx, todo, spine, budget)
        except BudgetExceeded as exc:
            budget_exhausted = True
            await spine.error("review.budget_exhausted", exc)
        for verdict in verdicts:
            await review_repo.save_verdict(review_id, verdict)

    all_verdicts = await review_repo.load_verdicts(review_id) or verdicts

    async with spine.span("review.aggregate"):
        findings, confidence = aggregator.aggregate(all_verdicts)
        await review_repo.save_findings(review_id, findings)

    result = evaluate(
        findings,
        all_verdicts,
        confidence,
        budget_exhausted=budget_exhausted or any(v.status == "budget_denied" for v in all_verdicts),
        is_public_repo=not pr.is_private,
    )
    await spine.decision(
        "gate.decided",
        decision=str(result.decision),
        reason=str(result.reason) if result.reason else None,
        confidence=confidence,
        findings=len(findings),
        postable=len(result.postable),
        explanation=result.explanation,
    )

    return ReviewOutcome(
        review_id=review_id,
        decision=result.decision,
        reason=result.reason,
        overall_confidence=confidence,
        findings=findings,
        postable=result.postable,
        verdicts=all_verdicts,
        summary=aggregator.summarise(findings, all_verdicts),
        total_cost_usd=sum(v.cost_usd for v in all_verdicts),
    )


async def _act(
    gh: GitHubClient,
    pr: PullRequestContext,
    review_id: uuid.UUID,
    outcome: ReviewOutcome,
    spine: EventSpine,
    post: bool = True,
) -> None:
    """Carry out the gate's decision. This is the only place that talks back to GitHub."""
    result_reason = outcome.reason

    if outcome.decision is Decision.AUTO_POST and not post:
        await spine.decision("review.not_posted", reason="dry_run", would_post=len(outcome.postable))
        await review_repo.finish_review(
            review_id,
            ReviewStatus.SUPPRESSED,
            Decision.AUTO_POST,
            None,
            outcome.overall_confidence,
            outcome.total_cost_usd,
        )
        log.info("review.dry_run", review_id=str(review_id), would_post=len(outcome.postable))
        return

    if outcome.decision is Decision.AUTO_POST:
        body = render_review_body(
            outcome.postable,
            outcome.summary,
            held_back=len(outcome.findings) - len(outcome.postable),
            bundle=bundle_version(),
        )
        try:
            async with spine.span("review.post", comments=len(outcome.postable)):
                github_review_id = await gh.post_review(
                    pr.repo_full_name, pr.number, pr.head_sha, body, outcome.postable
                )
            await review_repo.mark_posted([f.id for f in outcome.postable])
            await review_repo.finish_review(
                review_id,
                ReviewStatus.POSTED,
                Decision.AUTO_POST,
                None,
                outcome.overall_confidence,
                outcome.total_cost_usd,
                github_review_id,
            )
            return
        except Exception as exc:
            # The review is good; only delivery failed. Escalating beats losing it.
            await spine.error("review.post_failed", exc)
            log.error("review.post_failed", review_id=str(review_id), error=str(exc))
            from ..domain.enums import EscalationReason

            outcome.decision = Decision.ESCALATE
            result_reason = EscalationReason.POLICY

    if outcome.decision is Decision.ESCALATE:
        from ..domain.enums import EscalationReason

        reason = result_reason or EscalationReason.LOW_CONFIDENCE
        priority = {
            EscalationReason.CRITICAL_SECURITY: 1,
            EscalationReason.BUDGET_EXCEEDED: 10,
            EscalationReason.AGENT_FAILURE: 20,
            EscalationReason.LOW_CONFIDENCE: 50,
            EscalationReason.POLICY: 30,
        }[reason]
        await review_repo.enqueue_hitl(
            review_id,
            reason,
            f"{pr.repo_full_name}#{pr.number} — {outcome.summary}",
            priority,
        )
        await review_repo.finish_review(
            review_id,
            ReviewStatus.AWAITING_HUMAN,
            Decision.ESCALATE,
            reason,
            outcome.overall_confidence,
            outcome.total_cost_usd,
        )
        return

    await review_repo.finish_review(
        review_id,
        ReviewStatus.SUPPRESSED,
        Decision.SUPPRESS,
        None,
        outcome.overall_confidence,
        outcome.total_cost_usd,
    )
