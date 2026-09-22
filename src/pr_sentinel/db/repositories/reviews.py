"""Everything that reads or writes the relational truth of a review."""

from __future__ import annotations

import uuid
from typing import Any

from ...domain.enums import Decision, EscalationReason, ReviewStatus, VerdictStatus
from ...domain.models import AgentVerdict, Finding, PullRequestContext, WebhookJob
from .. import pool


async def record_delivery(job: WebhookJob, status: str = "accepted") -> bool:
    """Durable half of idempotency. Returns False if this delivery was already seen."""
    row = await pool.fetchrow(
        """
        INSERT INTO deliveries (delivery_id, event, action, repo_full_name, pr_number, status)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (delivery_id) DO NOTHING
        RETURNING delivery_id
        """,
        job.delivery_id,
        job.event,
        job.action,
        job.repo_full_name,
        job.pr_number,
        status,
    )
    return row is not None


async def upsert_repository(full_name: str, github_id: int, default_branch: str = "main") -> int:
    return await pool.fetchval(
        """
        INSERT INTO repositories (github_repo_id, full_name, default_branch)
        VALUES ($1, $2, $3)
        ON CONFLICT (github_repo_id)
          DO UPDATE SET full_name = EXCLUDED.full_name
        RETURNING id
        """,
        github_id,
        full_name,
        default_branch,
    )


async def upsert_pull_request(repo_id: int, ctx: PullRequestContext) -> int:
    return await pool.fetchval(
        """
        INSERT INTO pull_requests (repo_id, number, head_sha, base_sha, title, author, is_private)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (repo_id, number, head_sha)
          DO UPDATE SET title = EXCLUDED.title
        RETURNING id
        """,
        repo_id,
        ctx.number,
        ctx.head_sha,
        ctx.base_sha,
        ctx.title,
        ctx.author,
        ctx.is_private,
    )


async def open_review(pr_id: int, delivery_id: str, prompt_bundle: str) -> tuple[uuid.UUID, bool]:
    """Create (or recover) the review row.

    Returns (review_id, is_new). A re-delivery of the same webhook lands on the
    existing row rather than producing a second review of the same commit —
    INVARIANT-2, enforced by the unique constraint rather than by hope.
    """
    row = await pool.fetchrow(
        """
        INSERT INTO reviews (pr_id, delivery_id, status, prompt_bundle)
        VALUES ($1, $2, 'running', $3)
        ON CONFLICT (pr_id, delivery_id) DO NOTHING
        RETURNING id
        """,
        pr_id,
        delivery_id,
        prompt_bundle,
    )
    if row is not None:
        return row["id"], True
    existing = await pool.fetchval(
        "SELECT id FROM reviews WHERE pr_id = $1 AND delivery_id = $2", pr_id, delivery_id
    )
    return existing, False


async def completed_agents(review_id: uuid.UUID) -> set[str]:
    rows = await pool.fetch(
        "SELECT agent FROM agent_verdicts WHERE review_id = $1 AND status = 'ok'", review_id
    )
    return {r["agent"] for r in rows}


async def load_verdicts(review_id: uuid.UUID) -> list[AgentVerdict]:
    rows = await pool.fetch("SELECT * FROM agent_verdicts WHERE review_id = $1 ORDER BY agent", review_id)
    out: list[AgentVerdict] = []
    for r in rows:
        raw: dict[str, Any] = r["raw"] or {}
        out.append(
            AgentVerdict(
                agent=r["agent"],
                status=VerdictStatus(r["status"]),
                findings=[Finding.model_validate(f) for f in raw.get("findings", [])],
                summary=raw.get("summary", ""),
                model=r["model"] or "",
                prompt_version=r["prompt_version"] or "",
                input_tokens=r["input_tokens"],
                output_tokens=r["output_tokens"],
                cached_tokens=r["cached_tokens"],
                cost_usd=float(r["cost_usd"]),
                duration_ms=r["duration_ms"],
                error=r["error"],
            )
        )
    return out


async def save_verdict(review_id: uuid.UUID, verdict: AgentVerdict) -> None:
    raw = {
        "summary": verdict.summary,
        "findings": [f.model_dump(mode="json") for f in verdict.findings],
    }
    await pool.execute(
        """
        INSERT INTO agent_verdicts (review_id, agent, status, model, prompt_version, raw,
                                    input_tokens, output_tokens, cached_tokens, cost_usd,
                                    duration_ms, error)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        ON CONFLICT (review_id, agent) DO UPDATE SET
            status = EXCLUDED.status, model = EXCLUDED.model,
            prompt_version = EXCLUDED.prompt_version, raw = EXCLUDED.raw,
            input_tokens = EXCLUDED.input_tokens, output_tokens = EXCLUDED.output_tokens,
            cached_tokens = EXCLUDED.cached_tokens, cost_usd = EXCLUDED.cost_usd,
            duration_ms = EXCLUDED.duration_ms, error = EXCLUDED.error
        """,
        review_id,
        str(verdict.agent),
        str(verdict.status),
        verdict.model,
        verdict.prompt_version,
        raw,
        verdict.input_tokens,
        verdict.output_tokens,
        verdict.cached_tokens,
        verdict.cost_usd,
        verdict.duration_ms,
        verdict.error,
    )


async def save_findings(review_id: uuid.UUID, findings: list[Finding]) -> None:
    if not findings:
        return
    pg = await pool.get_pool()
    async with pg.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM findings WHERE review_id = $1", review_id)
        await conn.executemany(
            """
            INSERT INTO findings (id, review_id, agent, category, severity, file_path,
                                  line_start, line_end, title, body, rationale, confidence,
                                  evidence, agreeing, merged_into)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            """,
            [
                (
                    f.id,
                    review_id,
                    str(f.agent),
                    str(f.category),
                    str(f.severity),
                    f.file_path,
                    f.line_start,
                    f.line_end,
                    f.title,
                    f.body,
                    f.rationale,
                    f.confidence,
                    [e.model_dump(mode="json") for e in f.evidence],
                    f.agreeing,
                    None,
                )
                for f in findings
            ],
        )
        # merged_into is applied second so a parent always exists when a child points at it.
        merged = [(f.merged_into, f.id) for f in findings if f.merged_into]
        if merged:
            await conn.executemany("UPDATE findings SET merged_into = $1 WHERE id = $2", merged)


async def mark_posted(finding_ids: list[uuid.UUID]) -> None:
    if finding_ids:
        await pool.execute("UPDATE findings SET posted = TRUE WHERE id = ANY($1::uuid[])", finding_ids)


async def finish_review(
    review_id: uuid.UUID,
    status: ReviewStatus,
    decision: Decision | None,
    reason: EscalationReason | None,
    confidence: float,
    cost_usd: float,
    github_review_id: int | None = None,
    error: str | None = None,
) -> None:
    await pool.execute(
        """
        UPDATE reviews
           SET status = $2, decision = $3, decision_reason = $4, overall_confidence = $5,
               total_cost_usd = $6, github_review_id = $7, error = $8, finished_at = now()
         WHERE id = $1
        """,
        review_id,
        str(status),
        str(decision) if decision else None,
        str(reason) if reason else None,
        round(confidence, 3),
        round(cost_usd, 6),
        github_review_id,
        error,
    )


async def enqueue_hitl(
    review_id: uuid.UUID, reason: EscalationReason, summary: str, priority: int
) -> uuid.UUID:
    return await pool.fetchval(
        """
        INSERT INTO hitl_items (review_id, reason, summary, priority)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        review_id,
        str(reason),
        summary,
        priority,
    )


async def record_feedback(
    review_id: uuid.UUID | None,
    finding_id: uuid.UUID | None,
    source: str,
    verdict: str,
    actor: str | None = None,
    note: str | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO feedback (review_id, finding_id, source, verdict, actor, note)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        review_id,
        finding_id,
        source,
        verdict,
        actor,
        note,
    )
