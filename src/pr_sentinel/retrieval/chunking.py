"""Split source files into retrievable chunks.

Chunk boundaries are the thing that decides whether retrieval is useful. Fixed
windows cut functions in half and return something that looks relevant and
answers nothing. So: split on definition boundaries where the language makes
that cheap to spot, and fall back to overlapping windows everywhere else.

No tree-sitter. The indentation and brace heuristics below get the common
languages right, and a wrong boundary costs recall, not correctness — the file
path and line range travel with every chunk, so the model always knows what it
is looking at.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

MAX_CHUNK_LINES = 120
MIN_CHUNK_LINES = 5
WINDOW_OVERLAP = 15

_PY_DEF = re.compile(r"^(?:async\s+def|def|class)\s+(\w+)")
_BRACE_DEF = re.compile(
    r"^\s*(?:export\s+)?(?:public|private|protected|static|final|async|func|fn|pub)?[\s\w<>,\[\]*&:]*?"
    r"\b(?:function|func|fn|class|interface|struct|type|impl|def)\s+(\w+)"
)

SKIP_DIRS = {
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "target",
    ".next",
    ".mypy_cache",
    ".pytest_cache",
    "vendor",
    ".terraform",
    "migrations/__pycache__",
}
INDEXABLE_EXT = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".rb",
    ".php",
    ".cs",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".hpp",
    ".sql",
    ".sh",
    ".tf",
    ".md",
    ".yaml",
    ".yml",
}
LANGUAGE_BY_EXT = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".sql": "sql",
    ".sh": "shell",
    ".tf": "terraform",
    ".md": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
}


@dataclass
class Chunk:
    file_path: str
    start_line: int
    end_line: int
    content: str
    symbol: str | None = None
    language: str | None = None

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8", "replace")).hexdigest()[:32]

    def as_row(self, embedding: list[float]) -> dict:
        return {
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "content": self.content,
            "symbol": self.symbol,
            "language": self.language,
            "content_hash": self.content_hash,
            "embedding": embedding,
        }


def chunk_file(path: str, text: str, language: str | None = None) -> list[Chunk]:
    lines = text.splitlines()
    if not lines:
        return []
    language = language or LANGUAGE_BY_EXT.get("." + path.rsplit(".", 1)[-1], None)

    boundaries = _python_boundaries(lines) if language == "python" else _brace_boundaries(lines)
    chunks = (
        _from_boundaries(path, lines, boundaries, language) if boundaries else _windows(path, lines, language)
    )
    # A definition longer than the cap gets windowed rather than truncated —
    # a 400-line function is exactly the kind of thing worth retrieving.
    out: list[Chunk] = []
    for c in chunks:
        span = c.end_line - c.start_line + 1
        if span <= MAX_CHUNK_LINES:
            out.append(c)
        else:
            out.extend(_windows(path, lines, language, c.start_line, c.end_line, c.symbol))
    return [c for c in out if c.content.strip()]


def _python_boundaries(lines: list[str]) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if line != stripped:
            # Top-level definitions only. Splitting a class into one chunk per
            # method looks tempting, but the retrieved chunk then no longer says
            # which class it came from — and exact method names are what the
            # full-text half of hybrid search is for.
            continue
        m = _PY_DEF.match(stripped)
        if m:
            found.append((i + 1, m.group(1)))
    return found


def _brace_boundaries(lines: list[str]) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = _BRACE_DEF.match(line)
        if m:
            found.append((i + 1, m.group(1)))
    return found


def _from_boundaries(
    path: str, lines: list[str], boundaries: list[tuple[int, str]], language: str | None
) -> list[Chunk]:
    chunks: list[Chunk] = []
    # Module header (imports, constants) is its own chunk — it is what tells a
    # reviewer what this file is allowed to reach for.
    if boundaries[0][0] > MIN_CHUNK_LINES:
        chunks.append(
            Chunk(
                path, 1, boundaries[0][0] - 1, "\n".join(lines[: boundaries[0][0] - 1]), "<module>", language
            )
        )
    for idx, (start, symbol) in enumerate(boundaries):
        end = boundaries[idx + 1][0] - 1 if idx + 1 < len(boundaries) else len(lines)
        if end < start:
            continue
        chunks.append(Chunk(path, start, end, "\n".join(lines[start - 1 : end]), symbol, language))
    return chunks


def _windows(
    path: str,
    lines: list[str],
    language: str | None,
    start: int = 1,
    end: int | None = None,
    symbol: str | None = None,
) -> list[Chunk]:
    end = end or len(lines)
    chunks: list[Chunk] = []
    cursor = start
    step = MAX_CHUNK_LINES - WINDOW_OVERLAP
    while cursor <= end:
        stop = min(cursor + MAX_CHUNK_LINES - 1, end)
        chunks.append(Chunk(path, cursor, stop, "\n".join(lines[cursor - 1 : stop]), symbol, language))
        if stop >= end:
            break
        cursor += step
    return chunks


def should_index(path: str, size_bytes: int, max_bytes: int = 400_000) -> bool:
    if any(part in SKIP_DIRS for part in path.split("/")):
        return False
    if size_bytes > max_bytes:
        return False
    ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
    return ext in INDEXABLE_EXT
