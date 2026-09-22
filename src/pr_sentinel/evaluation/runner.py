"""Run the panel over the golden set.

No database and no GitHub. Retrieval is replaced by the fixture's own context
files, so what is measured is the agents, the grounding filter, the aggregator and
the gate — not the retriever, which needs its own harness and its own labels.
"""

from __future__ import annotations

import asyncio
import time
import uuid

from ..config import get_settings
from ..domain.enums import ALL_AGENTS
from ..events.spine import NullSpine
from ..forge.diff import render_for_prompt
from ..gate import evaluate
from ..llm.client import get_provider
from ..logging import get_logger
from ..orchestration import aggregator
from ..orchestration.engine import get_engine
from ..reliability.budget import BudgetGuard
from ..retrieval.context import ReviewContext
from .fixtures import EvalCase, load_cases
from .metrics import EvalReport, score, score_case

log = get_logger(__name__)


async def run_case(case: EvalCase, engine_name: str | None, budget_cap_usd: float | None):
    settings = get_settings()
    pr = case.pull_request()
    diff_text, truncated = render_for_prompt(pr.files, settings.max_diff_bytes)
    ctx = ReviewContext(pr=pr, diff_text=diff_text, chunks=case.context_chunks, truncated=truncated)

    review_id = uuid.uuid4()
    budget = BudgetGuard(review_id=review_id, enabled=budget_cap_usd is not None)
    spine = NullSpine(review_id=review_id)
    started = time.perf_counter()

    verdicts = await get_engine(engine_name).run_panel(ctx, list(ALL_AGENTS), spine, budget)
    findings, confidence = aggregator.aggregate(verdicts)
    gate = evaluate(findings, verdicts, confidence, is_public_repo=False)

    return score_case(
        case,
        findings,
        decision=str(gate.decision),
        confidence=confidence,
        cost_usd=sum(v.cost_usd for v in verdicts),
        duration_ms=int((time.perf_counter() - started) * 1000),
        failed_agents=[str(v.agent) for v in verdicts if not v.ok],
    )


async def run_eval(
    only: list[str] | None = None,
    engine_name: str | None = None,
    concurrency: int = 3,
    budget_cap_usd: float | None = None,
) -> EvalReport:
    cases = load_cases(only=only)
    settings = get_settings()
    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(case: EvalCase):
        async with semaphore:
            log.info("eval.case", id=case.id)
            return await run_case(case, engine_name, budget_cap_usd)

    results = await asyncio.gather(*(guarded(c) for c in cases))
    return score(
        list(results),
        provider=get_provider().name,
        models={str(a): settings.model_for(str(a)) for a in ALL_AGENTS},
    )
