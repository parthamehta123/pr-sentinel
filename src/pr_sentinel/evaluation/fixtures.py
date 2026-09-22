"""Loading the golden set."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..domain.models import CodeChunk, DiffFile, PullRequestContext
from ..forge.diff import build_diff_file

GOLDEN_DIR = Path(__file__).resolve().parents[3] / "tests" / "eval" / "golden"

SEVERITY_ORDER = ["info", "minor", "major", "critical"]


@dataclass
class Label:
    file_path: str
    line: int
    note: str = ""
    agent: str | None = None
    category: str | None = None
    min_severity: str = "info"

    @property
    def min_severity_rank(self) -> int:
        return SEVERITY_ORDER.index(self.min_severity)


@dataclass
class EvalCase:
    id: str
    title: str
    summary: str
    expected_decision: str | None
    files: list[DiffFile]
    context_chunks: list[CodeChunk]
    expected: list[Label]
    must_not_find: list[Label] = field(default_factory=list)

    def pull_request(self) -> PullRequestContext:
        return PullRequestContext(
            repo_full_name=f"eval/{self.id}",
            repo_github_id=0,
            is_private=True,
            number=1,
            title=self.title,
            body=self.summary,
            author="eval",
            head_sha="e" * 40,
            base_sha="b" * 40,
            files=self.files,
        )


def load_cases(directory: Path | None = None, only: list[str] | None = None) -> list[EvalCase]:
    directory = directory or GOLDEN_DIR
    cases: list[EvalCase] = []
    for path in sorted(directory.glob("*.json")):
        raw = json.loads(path.read_text())
        if only and raw["id"] not in only:
            continue
        cases.append(
            EvalCase(
                id=raw["id"],
                title=raw["title"],
                summary=raw.get("summary", ""),
                expected_decision=raw.get("expected_decision"),
                files=[build_diff_file(f) for f in raw["files"]],
                context_chunks=[_chunk(c) for c in raw.get("context_files", [])],
                expected=[Label(**_label(x)) for x in raw["expected"]],
                must_not_find=[Label(**_label(x)) for x in raw.get("must_not_find", [])],
            )
        )
    if not cases:
        raise FileNotFoundError(f"no fixtures in {directory}. Run: python scripts/build_eval_fixtures.py")
    return cases


def _label(raw: dict) -> dict:
    return {
        "file_path": raw["file_path"],
        "line": raw["line"],
        "note": raw.get("note", ""),
        "agent": raw.get("agent"),
        "category": raw.get("category"),
        "min_severity": raw.get("min_severity", "info"),
    }


def _chunk(raw: dict) -> CodeChunk:
    """Context files are injected directly rather than indexed.

    Retrieval quality is measured separately; what this harness measures is
    whether the agents *use* repository context they have been given. Skipping the
    index keeps the eval runnable with no database and makes the context identical
    on every run, which a real retriever would not be.
    """
    content = raw["content"]
    return CodeChunk(
        file_path=raw["path"],
        start_line=1,
        end_line=len(content.splitlines()),
        content=content,
        score=1.0,
    )
