"""The objects that travel between components.

`Finding` is the unit the whole system is built around. Its shape is the design:
which agent raised it, how bad it is, exactly where, why, and how sure. Drop any
one of those fields and something downstream stops being possible — dedupe needs
the location, the gate needs the confidence, and an appeal three weeks later
needs the rationale.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import AgentType, Category, Decision, EscalationReason, Severity, VerdictStatus


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Hunk(BaseModel):
    """One @@ block of a unified diff, in post-image (new file) coordinates."""

    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    header: str = ""
    added_lines: list[int] = Field(default_factory=list)
    context_lines: list[int] = Field(default_factory=list)
    text: str = ""

    @property
    def new_end(self) -> int:
        return self.new_start + max(self.new_lines, 1) - 1


class DiffFile(BaseModel):
    path: str
    previous_path: str | None = None
    status: str = "modified"  # added | modified | removed | renamed
    additions: int = 0
    deletions: int = 0
    hunks: list[Hunk] = Field(default_factory=list)
    patch: str = ""
    binary: bool = False

    @property
    def language(self) -> str | None:
        ext = self.path.rsplit(".", 1)[-1].lower() if "." in self.path else ""
        return {
            "py": "python",
            "ts": "typescript",
            "tsx": "typescript",
            "js": "javascript",
            "jsx": "javascript",
            "go": "go",
            "rs": "rust",
            "java": "java",
            "kt": "kotlin",
            "rb": "ruby",
            "php": "php",
            "cs": "csharp",
            "c": "c",
            "h": "c",
            "cc": "cpp",
            "cpp": "cpp",
            "hpp": "cpp",
            "sql": "sql",
            "sh": "shell",
            "yml": "yaml",
            "yaml": "yaml",
            "tf": "terraform",
            "md": "markdown",
        }.get(ext)

    def addressable_lines(self) -> set[int]:
        """Lines a GitHub inline comment can legally attach to.

        GitHub rejects a review comment whose line is not part of the diff, and
        an LLM that cites a line outside the diff is usually hallucinating about
        code it never saw. Same check, two payoffs.
        """
        lines: set[int] = set()
        for hunk in self.hunks:
            lines.update(hunk.added_lines)
            lines.update(hunk.context_lines)
        return lines


class PullRequestContext(BaseModel):
    repo_full_name: str
    repo_github_id: int
    is_private: bool = True
    number: int
    title: str = ""
    body: str = ""
    author: str = ""
    head_sha: str = ""
    base_sha: str = ""
    base_ref: str = "main"
    files: list[DiffFile] = Field(default_factory=list)
    truncated: bool = False

    @property
    def total_changes(self) -> int:
        return sum(f.additions + f.deletions for f in self.files)

    def file(self, path: str) -> DiffFile | None:
        return next((f for f in self.files if f.path == path), None)


class CodeChunk(BaseModel):
    """A retrieved slice of the repository, with provenance attached."""

    file_path: str
    start_line: int
    end_line: int
    content: str
    symbol: str | None = None
    language: str | None = None
    score: float = 0.0

    def render(self, max_chars: int = 2400) -> str:
        head = f"{self.file_path}:{self.start_line}-{self.end_line}"
        if self.symbol:
            head += f"  ({self.symbol})"
        body = (
            self.content if len(self.content) <= max_chars else self.content[:max_chars] + "\n… [truncated]"
        )
        return f"--- {head}\n{body}"


class Evidence(BaseModel):
    """Where a claim came from. Either the diff or a retrieved chunk — never neither."""

    kind: str  # "diff" | "chunk" | "convention"
    file_path: str
    line_start: int | None = None
    line_end: int | None = None
    excerpt: str = ""


class Finding(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    agent: AgentType
    category: Category = Category.OTHER
    severity: Severity = Severity.MINOR
    file_path: str
    line_start: int
    line_end: int
    title: str
    body: str
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)
    agreeing: list[str] = Field(default_factory=list)
    merged_into: uuid.UUID | None = None

    @field_validator("line_end")
    @classmethod
    def _end_after_start(cls, v: int, info) -> int:
        start = info.data.get("line_start")
        return max(v, start) if isinstance(start, int) else v

    @field_validator("rationale", "title")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("findings must carry a non-empty title and rationale")
        return v.strip()

    @property
    def is_security(self) -> bool:
        from .enums import SECURITY_CATEGORIES

        return self.agent is AgentType.SECURITY or self.category in SECURITY_CATEGORIES

    def dedupe_key(self) -> str:
        norm = "".join(ch for ch in self.title.lower() if ch.isalnum() or ch == " ")
        return hashlib.sha1(  # noqa: S324 - grouping key, not a security primitive
            f"{self.file_path}|{norm}".encode()
        ).hexdigest()[:16]

    def overlaps(self, other: Finding) -> bool:
        return (
            self.file_path == other.file_path
            and self.line_start <= other.line_end
            and other.line_start <= self.line_end
        )


class AgentVerdict(BaseModel):
    agent: AgentType
    status: VerdictStatus = VerdictStatus.OK
    findings: list[Finding] = Field(default_factory=list)
    summary: str = ""
    model: str = ""
    prompt_version: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is VerdictStatus.OK


class ReviewOutcome(BaseModel):
    review_id: uuid.UUID
    decision: Decision
    reason: EscalationReason | None = None
    overall_confidence: float = 0.0
    findings: list[Finding] = Field(default_factory=list)
    postable: list[Finding] = Field(default_factory=list)
    verdicts: list[AgentVerdict] = Field(default_factory=list)
    summary: str = ""
    total_cost_usd: float = 0.0
    created_at: datetime = Field(default_factory=_utcnow)

    @property
    def failed_agents(self) -> list[AgentType]:
        return [v.agent for v in self.verdicts if not v.ok]


class WebhookJob(BaseModel):
    """What ingress hands the queue. Deliberately small — the worker re-fetches
    the diff rather than trusting a payload that sat in Redis."""

    delivery_id: str
    event: str
    action: str
    repo_full_name: str
    repo_github_id: int
    is_private: bool = True
    pr_number: int
    head_sha: str
    base_sha: str
    title: str = ""
    author: str = ""
    received_at: datetime = Field(default_factory=_utcnow)
