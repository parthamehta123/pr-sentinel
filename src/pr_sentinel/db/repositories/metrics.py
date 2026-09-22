"""Reads for the budget guard and the cost dashboard.

Both go through the continuous aggregate first and only touch raw rows for the
current, not-yet-materialised bucket. That keeps "what have we spent today"
cheap no matter how many million events are behind it.
"""

from __future__ import annotations

import uuid

from .. import pool


async def spend_today_usd() -> float:
    # Real-time aggregation (migration 004) means this single read already covers
    # the current, not-yet-materialised hour. Without it the budget guard would be
    # blind to exactly the spike it exists to catch.
    val = await pool.fetchval(
        "SELECT COALESCE(sum(cost_usd), 0) FROM agent_cost_hourly WHERE bucket >= date_trunc('day', now())"
    )
    return float(val or 0.0)


async def spend_for_review_usd(review_id: uuid.UUID) -> float:
    val = await pool.fetchval(
        "SELECT COALESCE(sum(cost_usd), 0) FROM agent_events WHERE review_id = $1 AND kind = 'llm_call'",
        review_id,
    )
    return float(val or 0.0)


async def cost_by_agent(hours: int = 24) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT agent, model, sum(calls)::bigint AS calls, sum(cost_usd)::float8 AS cost_usd,
               sum(input_tokens)::bigint AS input_tokens,
               sum(output_tokens)::bigint AS output_tokens,
               sum(cached_tokens)::bigint AS cached_tokens
          FROM agent_cost_hourly
         WHERE bucket > now() - ($1 || ' hours')::interval
         GROUP BY agent, model
         ORDER BY cost_usd DESC
        """,
        str(hours),
    )
    return [dict(r) for r in rows]


async def latency_snapshot() -> list[dict]:
    rows = await pool.fetch("SELECT * FROM agent_latency_recent ORDER BY agent")
    return [dict(r) for r in rows]


async def escalation_rate(hours: int = 24) -> dict:
    row = await pool.fetchrow(
        """
        SELECT count(*)                                              AS reviews,
               count(*) FILTER (WHERE decision = 'escalate')          AS escalated,
               count(*) FILTER (WHERE decision = 'auto_post')         AS auto_posted,
               count(*) FILTER (WHERE status = 'failed')              AS failed,
               COALESCE(avg(overall_confidence), 0)::float8           AS avg_confidence,
               COALESCE(sum(total_cost_usd), 0)::float8               AS cost_usd
          FROM reviews
         WHERE started_at > now() - ($1 || ' hours')::interval
        """,
        str(hours),
    )
    d = dict(row) if row else {}
    reviews = d.get("reviews") or 0
    d["escalation_rate"] = (d.get("escalated") or 0) / reviews if reviews else 0.0
    return d


async def trace(review_id: uuid.UUID) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT ts, kind, name, agent, model, status, duration_ms,
               input_tokens, output_tokens, cost_usd, attributes
          FROM agent_events
         WHERE review_id = $1
         ORDER BY ts
        """,
        review_id,
    )
    return [dict(r) for r in rows]


async def open_hitl(limit: int = 100) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT h.id, h.review_id, h.reason, h.priority, h.summary, h.created_at,
               r.overall_confidence, pr.number AS pr_number, repo.full_name
          FROM hitl_items h
          JOIN reviews r        ON r.id = h.review_id
          JOIN pull_requests pr ON pr.id = r.pr_id
          JOIN repositories repo ON repo.id = pr.repo_id
         WHERE h.state = 'open'
         ORDER BY h.priority, h.created_at
         LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]
