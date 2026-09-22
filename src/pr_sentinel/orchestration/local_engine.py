"""Fan-out/fan-in on plain asyncio. No framework, no graph, no surprises."""

from __future__ import annotations

import asyncio

from ..agents import build_agent
from ..domain.enums import AgentType, VerdictStatus
from ..domain.models import AgentVerdict
from ..events.spine import EventSpine
from ..logging import get_logger
from ..reliability.budget import BudgetGuard
from ..retrieval.context import ReviewContext
from .engine import WorkflowEngine

log = get_logger(__name__)


class LocalEngine(WorkflowEngine):
    name = "local"

    async def run_panel(
        self, ctx: ReviewContext, agents: list[AgentType], spine: EventSpine, budget: BudgetGuard
    ) -> list[AgentVerdict]:
        async with spine.span("panel.fan_out", engine=self.name, agents=[str(a) for a in agents]):
            results = await asyncio.gather(
                *(build_agent(a).run(ctx, spine, budget) for a in agents),
                return_exceptions=True,
            )

        verdicts: list[AgentVerdict] = []
        for agent, result in zip(agents, results, strict=True):
            if isinstance(result, BaseException):
                # SpecialistAgent.run already traps its own failures; reaching
                # here means something outside it broke. Still not fatal.
                log.error("panel.agent_crashed", agent=str(agent), error=str(result))
                verdicts.append(
                    AgentVerdict(
                        agent=agent,
                        status=VerdictStatus.FAILED,
                        error=f"{type(result).__name__}: {result}",
                    )
                )
            else:
                verdicts.append(result)
        return verdicts
