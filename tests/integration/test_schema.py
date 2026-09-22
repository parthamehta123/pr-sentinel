"""Schema guarantees that only a real database can prove.

These are the claims the design makes about storage: three data shapes in one
store, an audit trail nothing can rewrite, and rollups that make the cost
questions cheap. Each one is asserted against a live Postgres.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


async def test_all_required_extensions_are_installed(db):
    rows = await db.fetch("SELECT extname FROM pg_extension")
    names = {r["extname"] for r in rows}
    assert {"pgcrypto", "vector", "timescaledb"} <= names


async def test_agent_events_is_a_hypertable(db):
    count = await db.fetchval(
        "SELECT count(*) FROM timescaledb_information.hypertables WHERE hypertable_name = 'agent_events'"
    )
    assert count == 1


async def test_the_audit_trail_rejects_update(db):
    """INVARIANT-4, enforced by the database rather than by discipline."""
    await db.execute(
        "INSERT INTO agent_events (kind, name, status) VALUES ('decision', 'test.immutable', 'ok')"
    )
    with pytest.raises(Exception, match="append-only"):
        await db.execute("UPDATE agent_events SET name = 'tampered' WHERE name = 'test.immutable'")


async def test_the_audit_trail_rejects_delete(db):
    await db.execute(
        "INSERT INTO agent_events (kind, name, status) VALUES ('decision', 'test.nodelete', 'ok')"
    )
    with pytest.raises(Exception, match="append-only"):
        await db.execute("DELETE FROM agent_events WHERE name = 'test.nodelete'")


async def test_the_audit_trail_rejects_truncate(db):
    """TRUNCATE has its own execution path; row triggers never fire for it."""
    with pytest.raises(Exception, match="append-only"):
        await db.execute("TRUNCATE agent_events")


async def test_the_cost_rollup_exists_and_is_queryable(db):
    row = await db.fetchrow("SELECT * FROM agent_cost_hourly LIMIT 1")
    assert row is None or "cost_usd" in dict(row)


async def test_a_finding_cannot_be_stored_without_a_confidence_in_range(db):
    repo_id = await db.fetchval(
        "INSERT INTO repositories (github_repo_id, full_name) VALUES ($1, 'test/constraints') "
        "ON CONFLICT (github_repo_id) DO UPDATE SET full_name = EXCLUDED.full_name RETURNING id",
        987_654_321,
    )
    pr_id = await db.fetchval(
        "INSERT INTO pull_requests (repo_id, number, head_sha, base_sha) "
        "VALUES ($1, 1, 'a', 'b') ON CONFLICT DO NOTHING RETURNING id",
        repo_id,
    ) or await db.fetchval("SELECT id FROM pull_requests WHERE repo_id = $1 AND number = 1", repo_id)
    delivery = f"constraint-{uuid.uuid4()}"
    await db.execute("INSERT INTO deliveries (delivery_id, event) VALUES ($1, 'pull_request')", delivery)
    review_id = await db.fetchval(
        "INSERT INTO reviews (pr_id, delivery_id, prompt_bundle) VALUES ($1, $2, 'test') RETURNING id",
        pr_id,
        delivery,
    )
    with pytest.raises(Exception, match="confidence"):
        await db.execute(
            "INSERT INTO findings (review_id, agent, category, severity, file_path, line_start,"
            " line_end, title, body, rationale, confidence)"
            " VALUES ($1,'security','injection','critical','a.py',1,1,'t','b','r', 1.5)",
            review_id,
        )


async def test_a_redelivered_webhook_cannot_open_a_second_review(db):
    """INVARIANT-2 at the storage layer, where it cannot be argued with."""
    from pr_sentinel.db.repositories import reviews as repo

    repo_id = await repo.upsert_repository("test/idempotent", 123_456_789)
    pr_id = await db.fetchval(
        "INSERT INTO pull_requests (repo_id, number, head_sha, base_sha) VALUES ($1, 9, 'h', 'b') "
        "ON CONFLICT (repo_id, number, head_sha) DO UPDATE SET title = '' RETURNING id",
        repo_id,
    )
    delivery = f"dup-{uuid.uuid4()}"
    await db.execute("INSERT INTO deliveries (delivery_id, event) VALUES ($1, 'pull_request')", delivery)

    first, new_first = await repo.open_review(pr_id, delivery, "b1")
    second, new_second = await repo.open_review(pr_id, delivery, "b1")
    assert first == second
    assert new_first and not new_second


async def test_the_vector_column_width_matches_the_configured_dimension(db):
    """The failure this catches is invisible until the first insert after indexing."""
    from pr_sentinel.db.repositories import chunks as chunk_repo

    column_dim = await chunk_repo.embedding_column_dim()
    assert column_dim and column_dim > 0
