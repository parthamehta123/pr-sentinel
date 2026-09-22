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
    repeat: int = 1,
) -> list[EvalReport]:
    """Run the set `repeat` times and return one report per run.

    Repetition is not optional rigour on a set this small. Two runs of an
    identical prompt were measured at 5 and 9 findings from the same agent, and
    0.80 vs 0.89 precision — a spread wider than most changes worth making. A
    single run cannot tell a real improvement from the model having a good day.
    """
    cases = load_cases(only=only)
    settings = get_settings()
    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(case: EvalCase):
        async with semaphore:
            log.info("eval.case", id=case.id)
            return await run_case(case, engine_name, budget_cap_usd)

    reports: list[EvalReport] = []

    for run in range(repeat):
        if repeat > 1:
            log.info("eval.run", run=run + 1, of=repeat)
        results = await asyncio.gather(*(guarded(c) for c in cases))
        _refuse_if_everything_failed(list(results))
        reports.append(
            score(
                list(results),
                provider=get_provider().name,
                models={str(a): settings.model_for(str(a)) for a in ALL_AGENTS},
            )
        )
    return reports


class EvalRunFailed(RuntimeError):
    """Every agent failed, so the run measures the outage and not the reviewer."""


def _refuse_if_everything_failed(results: list) -> None:
    """Stop a dead run from being mistaken for a perfect one.

    An exhausted credit balance made every agent fail. The report that came back
    read `calibration error 0.000`, `0 findings`, `$0.0000` — which is what a
    flawless run of a reviewer that says nothing also looks like — and it
    overwrote a good baseline on its way past. A regression gate fed that would
    have passed.
    """
    if not results:
        return
    if all(len(r.failed_agents) == len(ALL_AGENTS) for r in results):
        raise EvalRunFailed(
            f"every agent failed in all {len(results)} case(s) — this is an outage, "
            "not a result. Nothing was scored or saved. Check the worker log for the "
            "provider error (an exhausted credit balance looks exactly like this)."
        )
