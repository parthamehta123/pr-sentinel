"""Unified-diff parsing.

Only one thing here really matters: `addressable_lines`. Knowing exactly which
new-file line numbers exist in the diff is what lets the aggregator throw away a
finding about code the model never saw, and it is also what stops GitHub
rejecting an inline comment on a line outside the diff. One parser, two
guarantees.
"""

from __future__ import annotations

import re

from ..domain.models import DiffFile, Hunk

_HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_lines>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_lines>\d+))? @@(?P<header>.*)$"
)


def parse_patch(patch: str) -> list[Hunk]:
    """Parse the `patch` field GitHub returns for one file."""
    hunks: list[Hunk] = []
    current: Hunk | None = None
    new_line = 0
    buf: list[str] = []

    for line in (patch or "").splitlines():
        m = _HUNK_HEADER.match(line)
        if m:
            if current is not None:
                current.text = "\n".join(buf)
                hunks.append(current)
            buf = [line]
            current = Hunk(
                old_start=int(m.group("old_start")),
                old_lines=int(m.group("old_lines") or 1),
                new_start=int(m.group("new_start")),
                new_lines=int(m.group("new_lines") or 1),
                header=(m.group("header") or "").strip(),
            )
            new_line = current.new_start - 1
            continue

        if current is None:
            continue
        buf.append(line)

        if line.startswith("+"):
            new_line += 1
            current.added_lines.append(new_line)
        elif line.startswith("-"):
            continue
        elif line.startswith("\\"):  # "\ No newline at end of file"
            continue
        else:  # context (leading space, or an empty line in some diffs)
            new_line += 1
            current.context_lines.append(new_line)

    if current is not None:
        current.text = "\n".join(buf)
        hunks.append(current)
    return hunks


def build_diff_file(payload: dict) -> DiffFile:
    """Turn one element of GitHub's `GET /pulls/{n}/files` into a DiffFile."""
    patch = payload.get("patch") or ""
    return DiffFile(
        path=payload["filename"],
        previous_path=payload.get("previous_filename"),
        status=payload.get("status", "modified"),
        additions=int(payload.get("additions", 0)),
        deletions=int(payload.get("deletions", 0)),
        patch=patch,
        hunks=parse_patch(patch),
        binary=not patch and payload.get("status") != "removed",
    )


def render_for_prompt(files: list[DiffFile], max_bytes: int = 400_000) -> tuple[str, bool]:
    """Render the diff for a model, with new-file line numbers made explicit.

    Models are unreliable at counting lines inside a hunk, and every downstream
    check keys off the line number. Annotating each added line removes the whole
    class of off-by-N citation errors for the price of a few tokens.
    """
    out: list[str] = []
    used = 0
    truncated = False

    for f in files:
        if f.binary:
            out.append(f"+++ {f.path}\n[binary file, {f.additions} additions]")
            continue
        header = f"+++ {f.path}  ({f.status}, +{f.additions}/-{f.deletions})"
        block = [header]
        for hunk in f.hunks:
            block.append(
                f"@@ -{hunk.old_start},{hunk.old_lines} +{hunk.new_start},{hunk.new_lines} @@ {hunk.header}"
            )
            new_line = hunk.new_start - 1
            for line in hunk.text.splitlines()[1:]:
                if line.startswith("+"):
                    new_line += 1
                    block.append(f"{new_line:>6} + {line[1:]}")
                elif line.startswith("-"):
                    block.append(f"       - {line[1:]}")
                elif line.startswith("\\"):
                    continue
                else:
                    new_line += 1
                    block.append(f"{new_line:>6}   {line[1:] if line.startswith(' ') else line}")
        rendered = "\n".join(block)
        if used + len(rendered) > max_bytes:
            truncated = True
            out.append(f"+++ {f.path}\n[omitted: diff budget exhausted]")
            continue
        used += len(rendered)
        out.append(rendered)

    return "\n\n".join(out), truncated


def diff_query_text(files: list[DiffFile], limit: int = 2000) -> str:
    """Free text for the embedding side of retrieval: paths, hunk headers, added code."""
    parts: list[str] = []
    for f in files:
        parts.append(f.path.replace("/", " ").replace("_", " ").replace(".", " "))
        for hunk in f.hunks:
            if hunk.header:
                parts.append(hunk.header)
            for line in hunk.text.splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    parts.append(line[1:].strip())
    return " ".join(parts)[:limit]


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")

# Words that appear in every file and therefore distinguish nothing.
_STOPWORDS = frozenset(
    {
        "self",
        "none",
        "true",
        "false",
        "null",
        "this",
        "return",
        "import",
        "from",
        "class",
        "def",
        "func",
        "function",
        "const",
        "type",
        "interface",
        "struct",
        "public",
        "private",
        "static",
        "async",
        "await",
        "with",
        "else",
        "elif",
        "then",
        "when",
        "case",
        "while",
        "for",
        "and",
        "not",
        "the",
        "that",
        "value",
        "data",
        "item",
        "items",
        "args",
        "kwargs",
        "test",
        "tests",
        "assert",
        "raise",
        "throw",
        "catch",
        "except",
        "finally",
        "print",
        "string",
        "number",
        "boolean",
        "object",
        "list",
        "dict",
        "array",
        "param",
        "params",
    }
)


def fts_query(files: list[DiffFile], max_terms: int = 24) -> str:
    """Build the full-text half of the retrieval query.

    Two things this has to get right, both learned the hard way against a real
    31-file pull request:

    * `websearch_to_tsquery` **ANDs** unquoted terms. Throwing the whole diff at it
      produces a query demanding every one of two hundred tokens in a single chunk,
      which matches nothing — the full-text half of hybrid search silently does no
      work. Terms are therefore joined with `or`.
    * A query that deep also exceeds the tsquery parser's stack and raises
      `tsquery stack too small`. Hence a hard cap on term count.

    Terms are the most frequent distinctive identifiers in the added lines, which
    is what "who else touches this symbol?" actually needs.
    """
    counts: dict[str, int] = {}
    for f in files:
        for segment in re.split(r"[/._-]", f.path):
            if len(segment) >= 4 and segment.lower() not in _STOPWORDS:
                counts[segment.lower()] = counts.get(segment.lower(), 0) + 3
        for hunk in f.hunks:
            for line in hunk.text.splitlines():
                if not line.startswith("+") or line.startswith("+++"):
                    continue
                for token in _IDENTIFIER.findall(line):
                    lowered = token.lower()
                    if lowered not in _STOPWORDS:
                        counts[lowered] = counts.get(lowered, 0) + 1

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:max_terms]
    return " or ".join(term for term, _ in ranked)
