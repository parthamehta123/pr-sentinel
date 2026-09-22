"""The event spine.

One append-only stream carries three jobs that would otherwise need three
systems: reconstruct any review end-to-end, defend any single finding, and price
the whole thing. Nothing else in the codebase is allowed to be the source of
truth for "what happened" — repositories record conclusions, the spine records
the walk.

Writes are best-effort by design: losing an audit row must never fail a review
that is otherwise fine. Failures are logged loudly and counted.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from ..db import pool
from ..logging import get_logger

log = get_logger(__name__)


@dataclass
class Span:
    """A unit of work in flight. Mutate it as you learn things; it is flushed on exit."""

    span_id: uuid.UUID
    name: str
    agent: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    status: str = "ok"
    attributes: dict[str, Any] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    _started: float = field(default_factory=time.perf_counter)

    def set(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            if hasattr(self, key) and not key.startswith("_"):
                setattr(self, key, value)
            else:
                self.attributes[key] = value

    @property
    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self._started) * 1000)


class EventSpine:
    """Writes to `agent_events`. Instantiate once per review and pass it down."""

    def __init__(
        self,
        review_id: uuid.UUID | None = None,
        delivery_id: str | None = None,
        repo_full_name: str | None = None,
    ) -> None:
        self.review_id = review_id
        self.delivery_id = delivery_id
        self.repo_full_name = repo_full_name
        self._stack: list[uuid.UUID] = []
        self.dropped = 0

    async def _write(self, **row: Any) -> None:
        try:
            await pool.execute(
                """
                INSERT INTO agent_events (
                    review_id, delivery_id, repo_full_name, span_id, parent_span_id, kind, name,
                    agent, model, prompt_version, status, duration_ms,
                    input_tokens, output_tokens, cached_tokens, cost_usd, attributes)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                """,
                self.review_id,
                self.delivery_id,
                self.repo_full_name,
                row.get("span_id"),
                row.get("parent_span_id"),
                row["kind"],
                row["name"],
                row.get("agent"),
                row.get("model"),
                row.get("prompt_version"),
                row.get("status"),
                row.get("duration_ms"),
                row.get("input_tokens"),
                row.get("output_tokens"),
                row.get("cached_tokens"),
                row.get("cost_usd"),
                row.get("attributes") or {},
            )
        except Exception as exc:
            self.dropped += 1
            log.warning("spine.write_failed", kind=row.get("kind"), name=row.get("name"), error=str(exc))

    @asynccontextmanager
    async def span(self, name: str, **fields: Any) -> AsyncIterator[Span]:
        span = Span(
            span_id=uuid.uuid4(),
            name=name,
            **{k: v for k, v in fields.items() if k in {"agent", "model", "prompt_version"}},
        )
        span.attributes.update(
            {k: v for k, v in fields.items() if k not in {"agent", "model", "prompt_version"}}
        )
        parent = self._stack[-1] if self._stack else None
        self._stack.append(span.span_id)
        await self._write(
            kind="span_start",
            name=name,
            span_id=span.span_id,
            parent_span_id=parent,
            agent=span.agent,
            model=span.model,
            prompt_version=span.prompt_version,
            attributes=span.attributes,
        )
        try:
            yield span
        except Exception as exc:
            span.status = "error"
            span.attributes["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._stack.pop()
            await self._write(
                kind="span_end",
                name=name,
                span_id=span.span_id,
                parent_span_id=parent,
                agent=span.agent,
                model=span.model,
                prompt_version=span.prompt_version,
                status=span.status,
                duration_ms=span.elapsed_ms,
                input_tokens=span.input_tokens,
                output_tokens=span.output_tokens,
                cached_tokens=span.cached_tokens,
                cost_usd=span.cost_usd,
                attributes=span.attributes,
            )

    async def llm_call(
        self,
        *,
        name: str,
        agent: str | None,
        model: str,
        prompt_version: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int,
        cost_usd: float,
        duration_ms: int,
        status: str = "ok",
        **attrs: Any,
    ) -> None:
        """The row the cost rollups are built from. Nothing else writes kind='llm_call'."""
        await self._write(
            kind="llm_call",
            name=name,
            span_id=self._stack[-1] if self._stack else None,
            agent=agent,
            model=model,
            prompt_version=prompt_version,
            status=status,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            cost_usd=cost_usd,
            attributes=attrs,
        )

    async def decision(self, name: str, **attrs: Any) -> None:
        await self._write(
            kind="decision",
            name=name,
            span_id=self._stack[-1] if self._stack else None,
            status="ok",
            attributes=attrs,
        )

    async def retrieval(self, name: str, *, duration_ms: int, **attrs: Any) -> None:
        await self._write(
            kind="retrieval",
            name=name,
            span_id=self._stack[-1] if self._stack else None,
            status="ok",
            duration_ms=duration_ms,
            attributes=attrs,
        )

    async def error(self, name: str, exc: BaseException, **attrs: Any) -> None:
        await self._write(
            kind="error",
            name=name,
            span_id=self._stack[-1] if self._stack else None,
            status="error",
            attributes={"error": f"{type(exc).__name__}: {exc}", **attrs},
        )


class NullSpine(EventSpine):
    """For unit tests and the offline demo — same surface, no database."""

    async def _write(self, **row: Any) -> None:
        return None
