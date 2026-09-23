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


async def run_case(
    case: EvalCase,
    engine_name: str | None,
    budget_cap_usd: float | None,
    no_context: bool = False,
):
    settings = get_settings()
    pr = case.pull_request()
    diff_text, truncated = render_for_prompt(pr.files, settings.max_diff_bytes)
    # The eval feeds the panel each case's hand-authored context_files rather
    # than anything retrieval produced — so these numbers have always assumed
    # perfect retrieval. `no_context` is the other end of that bracket: what
    # the panel scores with no repository context at all. Real retrieval sits
    # between the two, and at recall@12 0.337 it sits nearer this end.
    chunks = [] if no_context else case.context_chunks
    ctx = ReviewContext(pr=pr, diff_text=diff_text, chunks=chunks, truncated=truncated)

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
    no_context: bool = False,
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
            return await run_case(case, engine_name, budget_cap_usd, no_context=no_context)

    reports: list[EvalReport] = []

    for run in range(repeat):
        if repeat > 1:
            log.info("eval.run", run=run + 1, of=repeat)
        results = await asyncio.gather(*(guarded(c) for c in cases))
        try:
            _refuse_if_everything_failed(list(results))
        except EvalRunFailed:
            # An outage in run N does not retract runs 1..N-1. Those completed,
            # every case in them got its whole panel, and they cost real money.
            # Discarding them loses good data to protect against bad data that is
            # already being discarded on its own. Keep what completed, drop the
            # outage run, and say so — the caller reports a spread over fewer runs
            # rather than over none.
            if not reports:
                raise
            log.warning(
                "eval.run.outage",
                run=run + 1,
                of=repeat,
                completed=len(reports),
                detail="run discarded; earlier completed runs kept",
            )
            break
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


# Above this share of cases losing their whole panel, the run is an outage.
# Below it, a degraded result is still a result and is scored.
OUTAGE_THRESHOLD = 0.25

# And above this share of individual agent calls failing, regardless of how
# they are distributed across cases. Lower than the whole-panel bar because a
# run can be thoroughly degraded without any single case losing all four.
DEGRADED_THRESHOLD = 0.10


def _refuse_if_everything_failed(results: list) -> None:
    """Stop a dead or half-dead run from being mistaken for a result.

    First seen when an exhausted credit balance made every agent fail: the report
    read `calibration error 0.000`, `0 findings`, `$0.0000` — which is also what a
    flawless run of a reviewer that says nothing looks like — and it overwrote a
    good baseline on its way past.

    Then seen again, worse, when the balance ran out *part way through*: two runs
    of a repeat completed and the third lost 31 of 53 cases. Nothing refused it,
    and because the summary reports the last run, the headline recall read 0.349
    for a reviewer that had just scored 1.000 twice. A partial outage is more
    dangerous than a total one, because it looks like a measurement.
    """
    if not results:
        return
    dead = sum(1 for r in results if len(r.failed_agents) == len(ALL_AGENTS))
    if dead and dead / len(results) > OUTAGE_THRESHOLD:
        raise EvalRunFailed(
            f"{dead} of {len(results)} case(s) lost their whole panel — this is an "
            "outage, not a result, and the cases that did run are whichever ones got "
            "in first. Nothing was scored or saved. Check the worker log for the "
            "provider error (an exhausted credit balance looks exactly like this)."
        )

    # Widespread *partial* loss, which the whole-panel test below cannot see. A
    # run where a fifth of the agent calls failed is not a measurement of the
    # reviewer, even if no single case lost all four: seen for real when credits
    # ran out mid-run and 46 of 252 calls died across 63 cases, none of them
    # taking a whole panel with it. The report printed recall 0.726 and a tests
    # agent at 0.00 — which was eleven failed calls, not a silent specialist.
    calls = len(results) * len(ALL_AGENTS)
    lost = sum(len(r.failed_agents) for r in results)
    if calls and lost / calls > DEGRADED_THRESHOLD:
        raise EvalRunFailed(
            f"{lost} of {calls} agent call(s) failed ({lost / calls:.0%}) — the panel was "
            "degraded across the run, so the findings are whichever agents happened to "
            "answer. Nothing was scored or saved. Check the worker log for the provider "
            "error (an exhausted credit balance looks exactly like this)."
        )
