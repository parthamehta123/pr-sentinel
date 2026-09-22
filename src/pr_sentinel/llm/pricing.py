"""Model prices and per-model API capabilities.

Prices are USD per million tokens, first-party Anthropic API rates. They are
data, not truth: check https://claude.com/pricing before trusting a number here
for anything that matters, and update this one table rather than chasing
constants through the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass

CACHE_READ_MULTIPLIER = 0.10  # cached input is roughly a tenth of input
CACHE_WRITE_MULTIPLIER = 1.25  # writing the cache costs a little more than input


@dataclass(frozen=True)
class ModelSpec:
    input_per_mtok: float
    output_per_mtok: float
    context_window: int
    # "adaptive" -> thinking={"type": "adaptive"}
    # "budget"   -> thinking={"type": "enabled", "budget_tokens": N}
    # "none"     -> omit
    thinking_style: str = "adaptive"
    supports_effort: bool = True
    supports_prefill: bool = False


MODELS: dict[str, ModelSpec] = {
    "claude-fable-5-1": ModelSpec(10.0, 50.0, 1_000_000, "adaptive", True),
    "claude-fable-5": ModelSpec(10.0, 50.0, 1_000_000, "adaptive", True),
    "claude-opus-5": ModelSpec(5.0, 25.0, 1_000_000, "adaptive", True),
    "claude-opus-4-8": ModelSpec(5.0, 25.0, 1_000_000, "adaptive", True),
    "claude-opus-4-7": ModelSpec(5.0, 25.0, 1_000_000, "adaptive", True),
    "claude-opus-4-6": ModelSpec(5.0, 25.0, 1_000_000, "adaptive", True),
    "claude-sonnet-5": ModelSpec(2.0, 10.0, 1_000_000, "adaptive", True),
    "claude-sonnet-4-6": ModelSpec(3.0, 15.0, 1_000_000, "adaptive", True),
    # Haiku 4.5 predates adaptive thinking and rejects output_config.effort.
    "claude-haiku-4-5": ModelSpec(1.0, 5.0, 200_000, "budget", False),
}

# Priced as if it were Sonnet: unknown-but-plausible beats a silent zero, which
# would make the budget guard useless exactly when a new model appears.
_FALLBACK = ModelSpec(2.0, 10.0, 200_000, "adaptive", True)


def spec_for(model: str) -> ModelSpec:
    return MODELS.get(model, _FALLBACK)


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    s = spec_for(model)
    return (
        input_tokens * s.input_per_mtok
        + output_tokens * s.output_per_mtok
        + cached_read_tokens * s.input_per_mtok * CACHE_READ_MULTIPLIER
        + cache_write_tokens * s.input_per_mtok * CACHE_WRITE_MULTIPLIER
    ) / 1_000_000
