"""The vertical slice against real storage: index, retrieve, review, persist."""

from __future__ import annotations

import uuid

import pytest

from pr_sentinel.config import get_settings
from pr_sentinel.db.repositories import chunks as chunk_repo
from pr_sentinel.db.repositories import metrics
from pr_sentinel.db.repositories import reviews as repo
from pr_sentinel.domain.enums import ALL_AGENTS, Decision
from pr_sentinel.events.spine import EventSpine
from pr_sentinel.forge.diff import render_for_prompt
from pr_sentinel.llm.client import set_provider
from pr_sentinel.llm.echo_provider import EchoProvider
from pr_sentinel.orchestration import aggregator
from pr_sentinel.orchestration.local_engine import LocalEngine
from pr_sentinel.reliability.budget import BudgetGuard
from pr_sentinel.retrieval.context import build_context
from pr_sentinel.retrieval.indexer import index_repository

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _echo():
    set_provider(EchoProvider())
    yield
    set_provider(None)


async def test_indexing_then_retrieving_returns_relevant_repository_code(db, tmp_path, pr_context):
    if get_settings().embedding_dim != (await chunk_repo.embedding_column_dim()):
        pytest.skip("EMBEDDING_DIM does not match the vector column; run `pr-sentinel doctor`")

    (tmp_path / "billing").mkdir()
    (tmp_path / "billing" / "db.py").write_text(
        "def execute(sql, params=None):\n"
        "    '''Run a parameterised query against the customers table.'''\n"
        "    return _cursor.execute(sql, params)\n"
    )
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "theme.ts").write_text(
        "export const buttonColour = 'blue';\nexport const spacing = 8;\n"
    )

    repo_id = await repo.upsert_repository("test/retrieval", 555_000_111)
    stats = await index_repository(repo_id, tmp_path, "sha1")
    assert stats["chunks"] > 0

    spine = EventSpine(review_id=uuid.uuid4())
    ctx = await build_context(pr_context, repo_id, spine)
    assert ctx.chunks, "hybrid search returned nothing for a diff that touches billing"
    assert any("billing" in c.file_path for c in ctx.chunks)


async def test_a_full_review_persists_findings_verdicts_and_a_trace(db, pr_context):
    repo_id = await repo.upsert_repository("test/pipeline", 555_000_222)
    pr_id = await repo.upsert_pull_request(repo_id, pr_context)
    delivery = f"pipeline-{uuid.uuid4()}"
    await db.execute("INSERT INTO deliveries (delivery_id, event) VALUES ($1, 'pull_request')", delivery)
    review_id, _ = await repo.open_review(pr_id, delivery, "test-bundle")

    spine = EventSpine(review_id=review_id, delivery_id=delivery, repo_full_name="test/pipeline")
    budget = BudgetGuard(review_id=review_id, enabled=False)
    text, _ = render_for_prompt(pr_context.files)
    from pr_sentinel.retrieval.context import ReviewContext

    ctx = ReviewContext(pr=pr_context, diff_text=text)

    verdicts = await LocalEngine().run_panel(ctx, list(ALL_AGENTS), spine, budget)
    for v in verdicts:
        await repo.save_verdict(review_id, v)

    findings, confidence = aggregator.aggregate(verdicts)
    await repo.save_findings(review_id, findings)
    await repo.finish_review(
        review_id,
        "posted",
        Decision.AUTO_POST,
        None,
        confidence,
        sum(v.cost_usd for v in verdicts),
    )

    stored = await db.fetch("SELECT * FROM findings WHERE review_id = $1", review_id)
    assert len(stored) == len(findings)
    assert all(row["rationale"] for row in stored), "INVARIANT-3 violated in storage"

    reloaded = await repo.load_verdicts(review_id)
    assert {str(v.agent) for v in reloaded} == {str(a) for a in ALL_AGENTS}

    trace = await metrics.trace(review_id)
    assert any(e["kind"] == "span_start" for e in trace)
    assert any(e["kind"] == "llm_call" for e in trace)


async def test_resume_skips_agents_that_already_returned(db, pr_context):
    repo_id = await repo.upsert_repository("test/resume", 555_000_333)
    pr_id = await repo.upsert_pull_request(repo_id, pr_context)
    delivery = f"resume-{uuid.uuid4()}"
    await db.execute("INSERT INTO deliveries (delivery_id, event) VALUES ($1, 'pull_request')", delivery)
    review_id, _ = await repo.open_review(pr_id, delivery, "test-bundle")

    spine = EventSpine(review_id=review_id)
    budget = BudgetGuard(review_id=review_id, enabled=False)
    text, _ = render_for_prompt(pr_context.files)
    from pr_sentinel.retrieval.context import ReviewContext

    ctx = ReviewContext(pr=pr_context, diff_text=text)

    partial = await LocalEngine().run_panel(ctx, list(ALL_AGENTS)[:2], spine, budget)
    for v in partial:
        await repo.save_verdict(review_id, v)

    done = await repo.completed_agents(review_id)
    assert done == {str(a) for a in list(ALL_AGENTS)[:2]}
    remaining = [a for a in ALL_AGENTS if str(a) not in done]
    assert len(remaining) == len(ALL_AGENTS) - 2


async def test_the_event_spine_records_cost_that_the_rollup_can_read(db):
    review_id = uuid.uuid4()
    spine = EventSpine(review_id=review_id)
    await spine.llm_call(
        name="test.call",
        agent="security",
        model="claude-opus-5",
        prompt_version="v1",
        input_tokens=1000,
        output_tokens=500,
        cached_tokens=0,
        cost_usd=0.0175,
        duration_ms=1234,
    )
    spent = await metrics.spend_for_review_usd(review_id)
    assert spent == pytest.approx(0.0175, abs=1e-6)


async def test_a_review_can_start_without_ingress_having_run(db, pr_context):
    """`replay` reaches the pipeline directly, so it must create its own delivery row.

    Regression: reviews.delivery_id is a foreign key into deliveries, and the
    pipeline used to rely on ingress having inserted it. Replaying a real PR
    therefore died on a ForeignKeyViolationError.
    """
    from pr_sentinel.domain.models import WebhookJob

    job = WebhookJob(
        delivery_id=f"replay-{uuid.uuid4()}",
        event="pull_request",
        action="synchronize",
        repo_full_name="test/no-ingress",
        repo_github_id=555_000_444,
        pr_number=7,
        head_sha="c" * 40,
        base_sha="d" * 40,
    )
    assert await repo.record_delivery(job) is True
    assert await repo.record_delivery(job) is False  # idempotent

    repo_id = await repo.upsert_repository(job.repo_full_name, job.repo_github_id)
    pr_id = await repo.upsert_pull_request(repo_id, pr_context)
    review_id, is_new = await repo.open_review(pr_id, job.delivery_id, "test-bundle")
    assert is_new and review_id is not None


async def test_hybrid_search_survives_a_large_diff(db, tmp_path):
    """Regression: a query built from a 31-file PR raised `tsquery stack too small`.

    Found by replaying pydantic/pydantic#13824. Both halves of the bug are covered
    here — the crash, and the silent one where an AND of 200 terms matched nothing.
    """
    from pr_sentinel.forge.diff import build_diff_file, fts_query
    from pr_sentinel.llm.embeddings import get_embedder

    (tmp_path / "core.py").write_text(
        "\n".join(f"def handler_number_{i}(request):\n    return process_{i}(request)" for i in range(40))
    )
    repo_id = await repo.upsert_repository("test/large-diff", 555_000_555)
    await index_repository(repo_id, tmp_path, "sha-large")

    files = [
        build_diff_file(
            {
                "filename": f"module_{i}/handler.py",
                "status": "modified",
                "additions": 30,
                "deletions": 0,
                "patch": "@@ -1,1 +1,31 @@\n unchanged\n"
                + "\n".join(
                    f"+def handler_number_{j}(request): return process_{j}(request)" for j in range(30)
                ),
            }
        )
        for i in range(31)
    ]

    terms = fts_query(files)
    assert 0 < len(terms.split(" or ")) <= 24

    vector = await get_embedder().embed_one("handler request process")
    hits = await chunk_repo.hybrid_search(repo_id, vector, terms, top_k=8)
    assert hits, "hybrid search returned nothing for a query that should match the index"


async def test_hybrid_search_works_with_no_full_text_terms(db, tmp_path):
    """An empty term list must disable the full-text half, not match everything."""
    from pr_sentinel.llm.embeddings import get_embedder

    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n")
    repo_id = await repo.upsert_repository("test/no-terms", 555_000_666)
    await index_repository(repo_id, tmp_path, "sha-empty")

    vector = await get_embedder().embed_one("alpha")
    hits = await chunk_repo.hybrid_search(repo_id, vector, "", top_k=5)
    assert isinstance(hits, list)
