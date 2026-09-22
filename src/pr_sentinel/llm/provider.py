"""The provider seam.

Everything above this line speaks `LLMRequest`/`LLMResponse`. Swapping vendors,
or dropping in a deterministic fake for tests, is a subclass — not a rewrite of
four agents. ADR-0005.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .pricing import estimate_cost


class LLMError(Exception):
    """Anything that went wrong talking to a model."""


class LLMRefusal(LLMError):
    """The model declined the request on safety grounds."""

    def __init__(self, category: str | None, explanation: str | None) -> None:
        super().__init__(f"model refused ({category}): {explanation}")
        self.category = category
        self.explanation = explanation


@dataclass
class LLMRequest:
    system: str
    user: str
    model: str
    max_tokens: int = 16000
    effort: str | None = "high"
    thinking: bool = True
    json_schema: dict[str, Any] | None = None
    cache_system: bool = True
    timeout_s: float = 180.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_read_tokens: int = 0
    cache_write_tokens: int = 0
    duration_ms: int = 0
    stop_reason: str | None = None

    @property
    def cost_usd(self) -> float:
        return estimate_cost(
            self.model,
            self.input_tokens,
            self.output_tokens,
            self.cached_read_tokens,
            self.cache_write_tokens,
        )


class LLMProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse: ...

    async def aclose(self) -> None:
        return None
