"""Both orchestration engines must be interchangeable — ADR-0004's actual test."""

from __future__ import annotations

import uuid

import pytest

from pr_sentinel.domain.enums import ALL_AGENTS, VerdictStatus
from pr_sentinel.events.spine import NullSpine
from pr_sentinel.forge.diff import render_for_prompt
from pr_sentinel.llm.client import set_provider
from pr_sentinel.llm.echo_provider import EchoProvider
from pr_sentinel.orchestration.local_engine import LocalEngine
from pr_sentinel.reliability.budget import BudgetGuard
from pr_sentinel.retrieval.context import ReviewContext

try:
    from pr_sentinel.orchestration.langgraph_engine import LangGraphEngine

    ENGINES = [LocalEngine, LangGraphEngine]
except ImportError:  # pragma: no cover
    ENGINES = [LocalEngine]


@pytest.fixture(autouse=True)
def _echo():
    set_provider(EchoProvider())
    yield
    set_provider(None)


def _ctx(pr_context):
    text, _ = render_for_prompt(pr_context.files)
    return ReviewContext(pr=pr_context, diff_text=text)


@pytest.mark.parametrize("engine_cls", ENGINES, ids=lambda c: c.name)
async def test_every_agent_returns_a_verdict(engine_cls, pr_context):
    verdicts = await engine_cls().run_panel(
        _ctx(pr_context),
        list(ALL_AGENTS),
        NullSpine(),
        BudgetGuard(review_id=uuid.uuid4(), enabled=False),
    )
    assert {v.agent for v in verdicts} == set(ALL_AGENTS)
    assert all(v.status is VerdictStatus.OK for v in verdicts)


@pytest.mark.parametrize("engine_cls", ENGINES, ids=lambda c: c.name)
async def test_findings_are_grounded_in_the_diff(engine_cls, pr_context):
    verdicts = await engine_cls().run_panel(
        _ctx(pr_context),
        list(ALL_AGENTS),
        NullSpine(),
        BudgetGuard(review_id=uuid.uuid4(), enabled=False),
    )
    addressable = pr_context.files[0].addressable_lines()
    for v in verdicts:
        for f in v.findings:
            assert f.file_path == "billing/handler.py"
            assert f.line_start in addressable


async def test_the_two_engines_produce_the_same_verdicts(pr_context):
    if len(ENGINES) < 2:
        pytest.skip("langgraph not installed")
    budget = BudgetGuard(review_id=uuid.uuid4(), enabled=False)
    a = await ENGINES[0]().run_panel(_ctx(pr_context), list(ALL_AGENTS), NullSpine(), budget)
    b = await ENGINES[1]().run_panel(_ctx(pr_context), list(ALL_AGENTS), NullSpine(), budget)
    key = lambda vs: sorted(  # noqa: E731
        (str(v.agent), tuple(sorted((f.file_path, f.line_start, f.title) for f in v.findings))) for v in vs
    )
    assert key(a) == key(b)


async def test_a_crashing_agent_does_not_take_the_panel_down(pr_context, monkeypatch):
    from pr_sentinel.agents import specialists

    class Exploding(specialists.SecurityAgent):
        async def run(self, ctx, spine, budget):
            raise RuntimeError("provider melted")

    monkeypatch.setitem(specialists.AGENT_CLASSES, specialists.AgentType.SECURITY, Exploding)
    verdicts = await LocalEngine().run_panel(
        _ctx(pr_context),
        list(ALL_AGENTS),
        NullSpine(),
        BudgetGuard(review_id=uuid.uuid4(), enabled=False),
    )
    assert len(verdicts) == len(ALL_AGENTS)
    failed = [v for v in verdicts if not v.ok]
    assert len(failed) == 1 and "provider melted" in (failed[0].error or "")


async def test_a_denied_budget_fails_the_agent_rather_than_the_review(pr_context):
    from pr_sentinel.agents import build_agent
    from pr_sentinel.domain.enums import AgentType

    exhausted = BudgetGuard(review_id=uuid.uuid4(), day_spent=10_000.0)
    verdict = await build_agent(AgentType.SECURITY).run(_ctx(pr_context), NullSpine(), exhausted)
    assert verdict.status is VerdictStatus.BUDGET_DENIED
    assert verdict.findings == []
