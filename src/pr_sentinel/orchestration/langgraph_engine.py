"""LangGraph implementation of the same panel.

`Send` is the reason to reach for LangGraph here at all: it expresses "run this
node once per item, in parallel, and reduce the results" as a first-class edge
rather than as a gather buried in a node. The reducer on `verdicts` is what makes
the fan-in declarative.

Everything below is confined to this file. If LangGraph's API moves, exactly one
module breaks — which is the entire point of ADR-0004.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

try:  # the Send import moved between releases
    from langgraph.types import Send
except ImportError:  # pragma: no cover - older langgraph
    from langgraph.constants import Send  # type: ignore[no-redef]

from ..agents import build_agent
from ..domain.enums import AgentType, VerdictStatus
from ..domain.models import AgentVerdict
from ..events.spine import EventSpine
from ..logging import get_logger
from ..reliability.budget import BudgetGuard
from ..retrieval.context import ReviewContext
from .engine import WorkflowEngine

log = get_logger(__name__)


class PanelState(TypedDict, total=False):
    agents: list[str]
    verdicts: Annotated[list[AgentVerdict], operator.add]


class LangGraphEngine(WorkflowEngine):
    name = "langgraph"

    def __init__(self) -> None:
        self._graph = None  # built per run: it closes over ctx/spine/budget

    async def run_panel(
        self, ctx: ReviewContext, agents: list[AgentType], spine: EventSpine, budget: BudgetGuard
    ) -> list[AgentVerdict]:
        async def specialist(state: dict[str, Any]) -> dict[str, list[AgentVerdict]]:
            agent = AgentType(state["agent"])
            try:
                verdict = await build_agent(agent).run(ctx, spine, budget)
            except Exception as exc:
                log.error("panel.agent_crashed", agent=str(agent), error=str(exc))
                verdict = AgentVerdict(
                    agent=agent,
                    status=VerdictStatus.FAILED,
                    error=f"{type(exc).__name__}: {exc}",
                )
            return {"verdicts": [verdict]}

        def fan_out(state: PanelState) -> list[Send]:
            return [Send("specialist", {"agent": a}) for a in state["agents"]]

        builder = StateGraph(PanelState)
        builder.add_node("specialist", specialist)  # type: ignore[type-var]  # Send payload is a plain dict
        builder.add_conditional_edges(START, fan_out, ["specialist"])
        builder.add_edge("specialist", END)
        graph = builder.compile()

        async with spine.span("panel.fan_out", engine=self.name, agents=[str(a) for a in agents]):
            final = await graph.ainvoke({"agents": [str(a) for a in agents], "verdicts": []})

        verdicts: list[AgentVerdict] = list(final.get("verdicts", []))
        returned = {v.agent for v in verdicts}
        for agent in agents:
            if agent not in returned:
                verdicts.append(
                    AgentVerdict(agent=agent, status=VerdictStatus.FAILED, error="no verdict returned")
                )
        return verdicts
