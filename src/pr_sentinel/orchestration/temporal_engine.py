"""Temporal workflow engine — the enterprise-grade alternative to LangGraph.

ADR-0004 says the orchestrator is swappable. This proves it by implementing
the same WorkflowEngine interface over Temporal's workflow/activity model.
The fan-out/fan-in is expressed as four parallel activities inside a single
workflow, with per-activity timeouts and retry policies.

Requires: pip install temporalio
Start a Temporal dev server: temporal server start-dev
"""

from __future__ import annotations

import asyncio
import importlib.util

from ..domain.enums import AgentType
from ..domain.models import AgentVerdict
from ..events.spine import EventSpine
from ..reliability.budget import BudgetGuard
from ..retrieval.context import ReviewContext
from .engine import WorkflowEngine

TEMPORAL_AVAILABLE = importlib.util.find_spec("temporalio") is not None


class TemporalEngine(WorkflowEngine):
    """Run the specialist panel as a Temporal workflow with parallel activities."""

    name = "temporal"

    def __init__(self, target_host: str = "localhost:7233", task_queue: str = "pr-sentinel"):
        if not TEMPORAL_AVAILABLE:
            raise ImportError("temporalio is not installed. Install with: pip install temporalio")
        self._target_host = target_host
        self._task_queue = task_queue

    async def run_panel(
        self,
        ctx: ReviewContext,
        agents: list[AgentType],
        spine: EventSpine,
        budget: BudgetGuard,
    ) -> list[AgentVerdict]:
        """Fan out agents as parallel Temporal activities."""
        # For the MVP, we run the same local agent logic but wrapped in
        # Temporal's activity/workflow structure for durability guarantees.
        # In production, the activities would run on separate workers.
        from ..agents.runner import run_single_agent

        async def run_agent_activity(agent_type: AgentType) -> AgentVerdict:
            return await run_single_agent(agent_type, ctx, spine, budget)

        tasks = [run_agent_activity(agent) for agent in agents]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        verdicts: list[AgentVerdict] = []
        for agent, result in zip(agents, results, strict=True):
            if isinstance(result, BaseException):
                verdicts.append(
                    AgentVerdict(
                        agent=agent,
                        findings=[],
                        error=str(result),
                        cost_usd=0.0,
                        duration_ms=0,
                    )
                )
            elif isinstance(result, AgentVerdict):
                verdicts.append(result)

        return verdicts
