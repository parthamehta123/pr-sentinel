"""What every specialist shares: prompt assembly, the call, and grounding.

The grounding filter is the single highest-leverage piece of code in the system.
A model that cites a line outside the diff has, empirically, either miscounted
inside a hunk or invented the code. The first is recoverable — snap to the
nearest real line — and the second is not, so it is dropped and counted. Findings
dropped here never reach a human, which is the whole point.
"""

from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod

from ..config import get_settings
from ..domain.enums import AgentType, Category, Severity, VerdictStatus
from ..domain.models import AgentVerdict, Evidence, Finding
from ..events.spine import EventSpine
from ..llm.client import call_llm
from ..llm.provider import LLMRefusal, LLMRequest
from ..llm.registry import load_prompt
from ..logging import get_logger
from ..reliability.budget import BudgetExceeded, BudgetGuard
from ..retrieval.context import ReviewContext
from .schema import AGENT_OUTPUT_SCHEMA

log = get_logger(__name__)

# Models miscount line numbers inside a hunk by a line or two far more often than
# they invent a location. Snap within this window; drop beyond it.
SNAP_WINDOW = 3


class SpecialistAgent(ABC):
    agent: AgentType
    prompt_name: str
    # None where the routed model rejects output_config.effort (Haiku 4.5).
    effort: str | None = "high"
    thinking: bool = True

    @abstractmethod
    def focus(self, ctx: ReviewContext) -> str:
        """Agent-specific framing appended to the user message."""

    async def run(self, ctx: ReviewContext, spine: EventSpine, budget: BudgetGuard) -> AgentVerdict:
        settings = get_settings()
        model = settings.model_for(str(self.agent))
        system, prompt_version = load_prompt(self.prompt_name)
        started = time.perf_counter()

        async with spine.span(
            f"agent.{self.agent}",
            agent=str(self.agent),
            model=model,
            prompt_version=prompt_version,
        ) as span:
            request = LLMRequest(
                system=system,
                user=self._user_message(ctx),
                model=model,
                max_tokens=settings.max_output_tokens,
                effort=self.effort,
                thinking=self.thinking,
                json_schema=AGENT_OUTPUT_SCHEMA,
                timeout_s=settings.llm_timeout_s,
                metadata={"agent": str(self.agent)},
            )
            try:
                response = await asyncio.wait_for(
                    call_llm(
                        request,
                        spine=spine,
                        budget=budget,
                        agent=str(self.agent),
                        prompt_version=prompt_version,
                        call_name=f"agent.{self.agent}",
                    ),
                    timeout=settings.agent_timeout_s,
                )
            except BudgetExceeded as exc:
                span.set(status="error", reason="budget")
                return self._failed(VerdictStatus.BUDGET_DENIED, model, prompt_version, exc, started)
            except TimeoutError as exc:
                span.set(status="error", reason="timeout")
                return self._failed(VerdictStatus.TIMEOUT, model, prompt_version, exc, started)
            except LLMRefusal as exc:
                # A refusal is a real signal about the diff, not an outage.
                span.set(status="error", reason="refusal", category=exc.category)
                return self._failed(VerdictStatus.FAILED, model, prompt_version, exc, started)
            except Exception as exc:
                span.set(status="error", reason=type(exc).__name__)
                await spine.error(f"agent.{self.agent}.failed", exc)
                return self._failed(VerdictStatus.FAILED, model, prompt_version, exc, started)

            findings, dropped, snapped, summary = self._parse(response.text, ctx)
            span.set(
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cached_tokens=response.cached_read_tokens,
                cost_usd=response.cost_usd,
                findings=len(findings),
                dropped_ungrounded=dropped,
                snapped_lines=snapped,
            )
            if dropped:
                log.info("agent.grounding_dropped", agent=str(self.agent), dropped=dropped)

            return AgentVerdict(
                agent=self.agent,
                status=VerdictStatus.OK,
                findings=findings,
                summary=summary,
                model=response.model,
                prompt_version=prompt_version,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cached_tokens=response.cached_read_tokens,
                cost_usd=response.cost_usd,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

    # ---- internals ---------------------------------------------------------

    def _user_message(self, ctx: ReviewContext) -> str:
        pr = ctx.pr
        return (
            f"# Pull request\n"
            f"{pr.repo_full_name}#{pr.number} — {pr.title}\n"
            f"Author: {pr.author or 'unknown'} · {len(pr.files)} file(s) · "
            f"{pr.total_changes} changed line(s)\n\n"
            f"{(pr.body or '(no description)')[:1500]}\n\n"
            f"# Team conventions\n{ctx.render_conventions()}\n\n"
            f"# Repository context (retrieved, not part of this change)\n"
            f"{ctx.render_repository_context()}\n\n"
            f"# The diff under review\n"
            f"Line numbers in the left gutter are new-file line numbers. Cite those.\n"
            f"{'**This diff was truncated; unseen files exist.**' if ctx.truncated else ''}\n\n"
            f"```diff\n{ctx.diff_text}\n```\n\n"
            f"# Your focus\n{self.focus(ctx)}\n"
        )

    def _failed(
        self,
        status: VerdictStatus,
        model: str,
        prompt_version: str,
        exc: BaseException,
        started: float,
    ) -> AgentVerdict:
        return AgentVerdict(
            agent=self.agent,
            status=status,
            model=model,
            prompt_version=prompt_version,
            error=f"{type(exc).__name__}: {exc}",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def _parse(self, text: str, ctx: ReviewContext) -> tuple[list[Finding], int, int, str]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = _salvage_json(text)
            if payload is None:
                log.warning("agent.unparseable", agent=str(self.agent), head=text[:200])
                return [], 0, 0, "(model returned unparseable output)"

        summary = str(payload.get("summary", ""))[:1000]
        findings: list[Finding] = []
        dropped = snapped = 0

        for raw in payload.get("findings", []) or []:
            if not isinstance(raw, dict):
                dropped += 1
                continue
            grounded = self._ground(raw, ctx)
            if grounded is None:
                dropped += 1
                continue
            finding, was_snapped = grounded
            snapped += int(was_snapped)
            findings.append(finding)

        return findings, dropped, snapped, summary

    def _ground(self, raw: dict, ctx: ReviewContext) -> tuple[Finding, bool] | None:
        path = str(raw.get("file_path", "")).lstrip("./")
        diff_file = ctx.pr.file(path)
        if diff_file is None:
            # Try a suffix match: models sometimes shorten or lengthen the path.
            candidates = [f for f in ctx.pr.files if f.path.endswith(path) or path.endswith(f.path)]
            if len(candidates) != 1:
                return None
            diff_file = candidates[0]

        addressable = diff_file.addressable_lines()
        if not addressable:
            return None

        try:
            start = int(raw.get("line_start", 0))
            end = int(raw.get("line_end", start))
            confidence = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            return None

        was_snapped = False
        if start not in addressable:
            nearest = min(addressable, key=lambda n: abs(n - start))
            if abs(nearest - start) > SNAP_WINDOW:
                return None
            start, end, was_snapped = nearest, max(nearest, min(end, max(addressable))), True
        end = max(start, min(end, max(addressable)))

        try:
            finding = Finding(
                agent=self.agent,
                category=_coerce(Category, raw.get("category"), Category.OTHER),
                severity=_coerce(Severity, raw.get("severity"), Severity.MINOR),
                file_path=diff_file.path,
                line_start=start,
                line_end=end,
                title=str(raw.get("title", ""))[:200],
                body=str(raw.get("body", ""))[:4000],
                rationale=str(raw.get("rationale", ""))[:4000],
                confidence=max(0.0, min(1.0, confidence)),
                evidence=_evidence(raw.get("evidence"), diff_file.path),
            )
        except ValueError:
            return None  # missing title or rationale — INVARIANT-3
        return finding, was_snapped


def _coerce(enum_cls, value, default):
    try:
        return enum_cls(str(value).lower())
    except ValueError:
        return default


def _evidence(raw, default_path: str) -> list[Evidence]:
    out: list[Evidence] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        try:
            out.append(
                Evidence(
                    kind=str(item.get("kind", "diff")),
                    file_path=str(item.get("file_path") or default_path),
                    line_start=item.get("line_start"),
                    line_end=item.get("line_end"),
                    excerpt=str(item.get("excerpt", ""))[:500],
                )
            )
        except ValueError:
            continue
    return out[:5]


def _salvage_json(text: str) -> dict | None:
    """Last resort when the schema constraint did not hold (older model, proxy)."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None
