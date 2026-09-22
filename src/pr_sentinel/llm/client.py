"""The single door every model call goes through.

Everything the system needs to be able to say about an LLM call — what it cost,
how long it took, which prompt version produced it, whether the budget allowed
it — is enforced here rather than in four agents that each remember differently.
"""

from __future__ import annotations

from ..config import get_settings
from ..events.spine import EventSpine
from ..logging import get_logger
from ..reliability.budget import BudgetGuard
from ..reliability.circuit import breaker
from ..reliability.retry import RetryPolicy, with_retry
from .pricing import estimate_cost
from .provider import LLMError, LLMProvider, LLMRefusal, LLMRequest, LLMResponse

log = get_logger(__name__)

_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        s = get_settings()
        if s.llm_provider == "echo":
            from .echo_provider import EchoProvider

            _provider = EchoProvider()
        else:
            from .anthropic_provider import AnthropicProvider

            _provider = AnthropicProvider(api_key=s.anthropic_api_key)
        log.info("llm.provider", provider=_provider.name)
    return _provider


def set_provider(provider: LLMProvider | None) -> None:
    """Test seam. Pass None to fall back to configuration."""
    global _provider
    _provider = provider


async def call_llm(
    request: LLMRequest,
    *,
    spine: EventSpine,
    budget: BudgetGuard | None = None,
    agent: str | None = None,
    prompt_version: str = "unknown",
    call_name: str = "llm.complete",
) -> LLMResponse:
    if budget is not None:
        # Charge the worst case up front so a budget that is nearly exhausted
        # cannot be blown through by a single very expensive call.
        budget.check(_worst_case_cost(request))

    provider = get_provider()
    cb = breaker(f"llm:{provider.name}", failure_threshold=5, recovery_time=30.0)
    policy = RetryPolicy(
        attempts=3,
        base_delay=1.0,
        max_delay=20.0,
        retry_on=(LLMError,),
        give_up_on=(LLMRefusal,),  # a refusal is a verdict, not a blip
    )

    async def _once() -> LLMResponse:
        return await cb.call(lambda: provider.complete(request))

    response = await with_retry(_once, policy, name=call_name)

    if budget is not None:
        budget.charge(response.cost_usd)

    await spine.llm_call(
        name=call_name,
        agent=agent,
        model=response.model,
        prompt_version=prompt_version,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cached_tokens=response.cached_read_tokens,
        cost_usd=response.cost_usd,
        duration_ms=response.duration_ms,
        stop_reason=response.stop_reason,
        cache_write_tokens=response.cache_write_tokens,
    )
    return response


def _worst_case_cost(request: LLMRequest) -> float:
    approx_input = len(request.system) // 4 + len(request.user) // 4
    return estimate_cost(request.model, approx_input, request.max_tokens)
