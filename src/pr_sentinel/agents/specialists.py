"""The four specialists.

They are four because that is how the reviewers this system replaces actually
think — a person reading a diff switches between "can this be attacked", "is this
right", "would anything catch it if it weren't", and "will the next person
understand it", and does each one worse when doing them at once. Four prompts
with four framings and four sets of retrieved context beat one prompt asked to
hold all four questions.
"""

from __future__ import annotations

from ..domain.enums import AgentType
from ..retrieval.context import ReviewContext
from .base import SpecialistAgent


class SecurityAgent(SpecialistAgent):
    agent = AgentType.SECURITY
    prompt_name = "security"
    effort = "high"

    def focus(self, ctx: ReviewContext) -> str:
        return (
            "Trace attacker-controlled data from where it enters to where it is used. "
            "Name the entry point and the sink. If you cannot see the entry point in "
            "this diff or in the retrieved context, say so in the rationale and lower "
            "your confidence rather than assuming the worst case."
        )


class CorrectnessAgent(SpecialistAgent):
    agent = AgentType.CORRECTNESS
    prompt_name = "correctness"
    effort = "high"

    def focus(self, ctx: ReviewContext) -> str:
        paths = ", ".join(f.path for f in ctx.pr.files[:8]) or "(none)"
        return (
            f"Changed files: {paths}. "
            "Prioritise contract breakage — a changed signature, return shape or default "
            "that an existing caller still assumes. The retrieved context is where those "
            "callers are; check it before and after you form an opinion."
        )


class TestsAgent(SpecialistAgent):
    agent = AgentType.TESTS
    prompt_name = "tests"
    effort = "medium"

    def focus(self, ctx: ReviewContext) -> str:
        test_files = [f.path for f in ctx.pr.files if "test" in f.path.lower()]
        note = (
            f"Tests changed in this PR: {', '.join(test_files[:6])}."
            if test_files
            else "No test file appears in this diff."
        )
        return (
            f"{note} For each behaviour change, state what would still pass if the change "
            "were wrong. Judge against the coverage the surrounding code actually has, not "
            "against an ideal."
        )


class DocsAgent(SpecialistAgent):
    agent = AgentType.DOCS
    prompt_name = "docs"
    # Haiku 4.5 rejects output_config.effort, and the effort knob buys little on a
    # task this mechanical. The router picks the model; this keeps the call legal.
    effort = None
    thinking = False

    def focus(self, ctx: ReviewContext) -> str:
        return (
            "Start with documentation this diff has made wrong — that outranks anything "
            "missing. Be the quietest agent on the panel: if nothing is now untrue and "
            "no public contract is undocumented, return zero findings."
        )


AGENT_CLASSES: dict[AgentType, type[SpecialistAgent]] = {
    AgentType.SECURITY: SecurityAgent,
    AgentType.CORRECTNESS: CorrectnessAgent,
    AgentType.TESTS: TestsAgent,
    AgentType.DOCS: DocsAgent,
}


def build_agent(agent: AgentType) -> SpecialistAgent:
    return AGENT_CLASSES[agent]()
