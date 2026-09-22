"""The orchestration seam.

Picking LangGraph was an MVP decision, not a permanent one (ADR-0004). The way
to keep it reversible is not to promise to be careful — it is to make the rest of
the system depend on this three-method interface and nothing else, and then keep
two implementations alive so the abstraction is exercised rather than aspirational.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..domain.enums import AgentType
from ..domain.models import AgentVerdict
from ..events.spine import EventSpine
from ..reliability.budget import BudgetGuard
from ..retrieval.context import ReviewContext


class WorkflowEngine(ABC):
    name: str = "abstract"

    @abstractmethod
    async def run_panel(
        self,
        ctx: ReviewContext,
        agents: list[AgentType],
        spine: EventSpine,
        budget: BudgetGuard,
    ) -> list[AgentVerdict]:
        """Run the specialists and return one verdict per agent."""


def get_engine(preferred: str | None = None) -> WorkflowEngine:
    """LangGraph when it is installed, plain asyncio otherwise.

    The fallback is not a degraded mode — for a single fan-out/fan-in step
    `asyncio.gather` is the whole of what the graph does. It exists so the system
    has no hard dependency on a fast-moving framework, and so CI proves the seam
    holds by running the same tests through both.
    """
    if preferred == "local":
        from .local_engine import LocalEngine

        return LocalEngine()
    try:
        from .langgraph_engine import LangGraphEngine

        return LangGraphEngine()
    except ImportError:
        from .local_engine import LocalEngine

        return LocalEngine()
