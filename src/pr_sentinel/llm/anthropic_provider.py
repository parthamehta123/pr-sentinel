"""Anthropic implementation.

Three details that are easy to get wrong and expensive to debug:

  * Thinking config is per-model. Opus 5 / Sonnet 5 take `{"type": "adaptive"}`;
    `budget_tokens` is rejected outright. Haiku 4.5 is the other way round and
    also rejects `output_config.effort`. The capability table in pricing.py is
    what keeps that out of the agents.
  * `stop_reason == "refusal"` returns HTTP 200. Read `content` without checking
    it and you get an empty review that looks like a clean bill of health.
  * The system prompt is cached and the diff is not. Caching is a prefix match,
    so the stable half has to come first or nothing caches at all.
"""

from __future__ import annotations

import time
from typing import Any

import anthropic

from ..logging import get_logger
from .pricing import spec_for
from .provider import LLMError, LLMProvider, LLMRefusal, LLMRequest, LLMResponse

log = get_logger(__name__)


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str = "", max_retries: int = 2) -> None:
        kwargs: dict[str, Any] = {"max_retries": max_retries}
        if api_key:
            kwargs["api_key"] = api_key
        # No api_key -> the SDK resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN
        # or an `ant auth login` profile on its own.
        self._client = anthropic.AsyncAnthropic(**kwargs)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        spec = spec_for(request.model)
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": [
                {
                    "type": "text",
                    "text": request.system,
                    **({"cache_control": {"type": "ephemeral"}} if request.cache_system else {}),
                }
            ],
            "messages": [{"role": "user", "content": request.user}],
        }

        if request.thinking:
            if spec.thinking_style == "adaptive":
                params["thinking"] = {"type": "adaptive"}
            elif spec.thinking_style == "budget":
                # Must be below max_tokens, minimum 1024.
                params["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": max(1024, min(4096, request.max_tokens - 1024)),
                }

        output_config: dict[str, Any] = {}
        if request.effort and spec.supports_effort:
            output_config["effort"] = request.effort
        if request.json_schema:
            output_config["format"] = {"type": "json_schema", "schema": request.json_schema}
        if output_config:
            params["output_config"] = output_config

        started = time.perf_counter()
        try:
            message = await self._client.with_options(timeout=request.timeout_s).messages.create(**params)
        except anthropic.APIStatusError as exc:
            raise LLMError(f"anthropic {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"anthropic connection error: {exc}") from exc
        duration_ms = int((time.perf_counter() - started) * 1000)

        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            raise LLMRefusal(getattr(details, "category", None), getattr(details, "explanation", None))

        text = "".join(b.text for b in message.content if getattr(b, "type", None) == "text")
        usage = message.usage
        response = LLMResponse(
            text=text,
            model=message.model or request.model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cached_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            duration_ms=duration_ms,
            stop_reason=message.stop_reason,
        )
        if response.stop_reason == "max_tokens":
            log.warning("llm.truncated", model=request.model, max_tokens=request.max_tokens)
        return response

    async def aclose(self) -> None:
        await self._client.close()
