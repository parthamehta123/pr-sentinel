"""Cost arithmetic and the prompt registry."""

from __future__ import annotations

import pytest

from pr_sentinel.config import get_settings
from pr_sentinel.domain.enums import ALL_AGENTS
from pr_sentinel.llm.pricing import MODELS, estimate_cost, spec_for
from pr_sentinel.llm.registry import bundle_version, load_prompt, reset_cache


def test_cost_is_per_million_tokens():
    assert estimate_cost("claude-opus-5", 1_000_000, 0) == pytest.approx(5.0)
    assert estimate_cost("claude-opus-5", 0, 1_000_000) == pytest.approx(25.0)


def test_cached_input_is_far_cheaper_than_fresh_input():
    fresh = estimate_cost("claude-opus-5", 1_000_000, 0)
    cached = estimate_cost("claude-opus-5", 0, 0, cached_read_tokens=1_000_000)
    assert cached < fresh / 5


def test_an_unknown_model_is_priced_rather_than_treated_as_free():
    """A silent zero would disable the budget guard the moment a model is renamed."""
    assert estimate_cost("claude-something-unreleased", 1_000_000, 0) > 0


def test_every_routed_model_has_a_price_and_a_capability_entry():
    s = get_settings()
    for agent in ALL_AGENTS:
        model = s.model_for(str(agent))
        assert model in MODELS, f"{agent} routes to unpriced model {model}"


def test_haiku_is_marked_as_rejecting_effort():
    """Sending output_config.effort to Haiku 4.5 is a 400; the table is what prevents it."""
    assert spec_for("claude-haiku-4-5").supports_effort is False
    assert spec_for("claude-haiku-4-5").thinking_style == "budget"


def test_current_models_use_adaptive_thinking():
    for model in ("claude-opus-5", "claude-sonnet-5"):
        assert spec_for(model).thinking_style == "adaptive"
        assert spec_for(model).supports_effort


def test_every_agent_prompt_loads_and_inherits_the_shared_rules():
    for agent in ALL_AGENTS:
        text, version = load_prompt(str(agent))
        assert "Grounding rules" in text, f"{agent} lost the shared grounding contract"
        assert "confidence" in text.lower()
        assert str(agent) in version and "base@" in version


def test_prompts_are_distinct_from_one_another():
    bodies = {str(a): load_prompt(str(a))[0] for a in ALL_AGENTS}
    assert len(set(bodies.values())) == len(bodies)


def test_the_bundle_version_changes_when_a_prompt_changes(tmp_path, monkeypatch):
    before = bundle_version()
    import pr_sentinel.llm.registry as reg

    target = reg.PROMPT_DIR / "security.md"
    original = target.read_text()
    try:
        target.write_text(original + "\n\nAn extra instruction.\n")
        reset_cache()
        assert bundle_version() != before
    finally:
        target.write_text(original)
        reset_cache()
    assert bundle_version() == before


def test_a_missing_prompt_fails_loudly():
    with pytest.raises(FileNotFoundError):
        load_prompt("nonexistent-agent")
