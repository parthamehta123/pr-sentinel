"""A deterministic provider for tests and the offline demo.

It is not a mock of the transport — it is a fake reviewer with crude but real
rules (an f-string in a SQL call, a hardcoded token, a bare except). That means
the pipeline downstream of it can be exercised end to end, for free, in CI, and
the grounding and dedupe logic gets fed plausible input instead of a fixture
that was written to pass.
"""

from __future__ import annotations

import json
import re

from .provider import LLMProvider, LLMRequest, LLMResponse

_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|[_-]key|secret|token|password|passwd)\s*[:=]\s*[\"'][^\"']{12,}[\"']"
)
_SQL_FSTRING = re.compile(r"(?i)(execute|executemany|cursor\.execute|query)\s*\(\s*f[\"']")
_BARE_EXCEPT = re.compile(r"except\s*:")
# The agents are shown the *rendered* diff, where every line carries its
# new-file line number. Parse that, not raw unified diff — matching what the
# model actually sees is the point of this provider.
_FILE_HEADER = re.compile(r"^\+\+\+ (\S+)")
_RENDERED_ADD = re.compile(r"^\s*(\d+) \+ (.*)$")
_RAW_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class EchoProvider(LLMProvider):
    name = "echo"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        agent = str(request.metadata.get("agent", "correctness"))
        findings = _scan(request.user, agent)
        payload = {
            "summary": (
                f"[echo:{agent}] {len(findings)} finding(s) from static patterns."
                if findings
                else f"[echo:{agent}] nothing matched."
            ),
            "findings": findings,
        }
        text = json.dumps(payload)
        return LLMResponse(
            text=text,
            model=request.model,
            input_tokens=len(request.user) // 4,
            output_tokens=len(text) // 4,
            duration_ms=1,
            stop_reason="end_turn",
        )


def _added_lines(prompt: str) -> list[tuple[str, int, str]]:
    """Yield (path, new_line_number, source) for every added line in the prompt."""
    out: list[tuple[str, int, str]] = []
    path = "unknown"
    raw_line = 0
    for line in prompt.splitlines():
        header = _FILE_HEADER.match(line)
        if header:
            path = header.group(1).removeprefix("b/")
            continue
        rendered = _RENDERED_ADD.match(line)
        if rendered:
            out.append((path, int(rendered.group(1)), rendered.group(2)))
            continue
        hunk = _RAW_HUNK.match(line)
        if hunk:
            raw_line = int(hunk.group(1)) - 1
            continue
        if line.startswith("+") and not line.startswith("+++"):
            raw_line += 1
            out.append((path, raw_line, line[1:]))
        elif line.startswith(" "):
            raw_line += 1
    return out


def _scan(prompt: str, agent: str) -> list[dict]:
    findings: list[dict] = []
    has_docstring = '"""' in prompt

    for path, line_no, body in _added_lines(prompt):
        if agent == "security" and _SQL_FSTRING.search(body):
            findings.append(
                _f(
                    path,
                    line_no,
                    "injection",
                    "critical",
                    0.88,
                    "SQL built by string interpolation",
                    "The query is assembled with an f-string, so any caller-controlled "
                    "value becomes SQL. Use a parameterised query.",
                    "Interpolated SQL on this line; no placeholder binding present.",
                    body,
                )
            )
        if agent == "security" and _SECRET.search(body):
            findings.append(
                _f(
                    path,
                    line_no,
                    "secrets",
                    "critical",
                    0.82,
                    "Credential literal committed to the repository",
                    "A secret is hardcoded here. Move it to configuration and rotate it.",
                    "A long string literal is assigned to a secret-shaped name.",
                    body,
                )
            )
        if agent == "correctness" and _BARE_EXCEPT.search(body):
            findings.append(
                _f(
                    path,
                    line_no,
                    "error_handling",
                    "major",
                    0.71,
                    "Bare except swallows control-flow exceptions",
                    "`except:` also catches KeyboardInterrupt and SystemExit. Catch "
                    "`Exception`, or the specific error.",
                    "Bare `except:` with no exception class on this line.",
                    body,
                )
            )
        if agent == "correctness" and _SQL_FSTRING.search(body):
            # Same category and same line as the security agent's finding, on
            # purpose: this is the path through the aggregator where two agents
            # agree and noisy-OR raises the merged confidence.
            findings.append(
                _f(
                    path,
                    line_no,
                    "injection",
                    "major",
                    0.64,
                    "Unvalidated identifier reaches the query",
                    "`customer_id` is interpolated without a type check or binding.",
                    "The value flows from the parameter straight into the SQL string.",
                    body,
                )
            )
        if agent == "tests" and body.strip().startswith("def ") and "test" not in path:
            findings.append(
                _f(
                    path,
                    line_no,
                    "test_coverage",
                    "minor",
                    0.55,
                    "New function arrives without a test",
                    "No test file in this diff covers the new function.",
                    "Function defined here; no matching test path in the changed set.",
                    body,
                )
            )
        if agent == "docs" and body.strip().startswith(("def ", "class ")) and not has_docstring:
            findings.append(
                _f(
                    path,
                    line_no,
                    "documentation",
                    "info",
                    0.45,
                    "Public definition has no docstring",
                    "Add a one-line docstring describing the contract.",
                    "Definition on this line, no docstring in the added block.",
                    body,
                )
            )
    return findings[:8]


def _f(path, line, category, severity, confidence, title, body, rationale, excerpt) -> dict:
    return {
        "file_path": path,
        "line_start": line,
        "line_end": line,
        "category": category,
        "severity": severity,
        "confidence": confidence,
        "title": title,
        "body": body,
        "rationale": rationale,
        "evidence": [
            {
                "kind": "diff",
                "file_path": path,
                "line_start": line,
                "line_end": line,
                "excerpt": excerpt.strip()[:200],
            }
        ],
    }
