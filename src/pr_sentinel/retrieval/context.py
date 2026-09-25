"""Context assembly: turn a diff into the packet an agent actually reads.

Retrieval here is per-file rather than one query for the whole PR. A pull request
that touches an auth handler and a CSS file has two unrelated neighbourhoods, and
one blended query returns the centroid of both — which is nothing. Per-file
queries cost a few more embeddings and return context that is actually about the
code under review.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..config import get_settings
from ..db.repositories import chunks as chunk_repo
from ..domain.models import CodeChunk, PullRequestContext
from ..events.spine import EventSpine
from ..forge.diff import diff_query_text, fts_query, render_for_prompt
from ..llm.embeddings import get_embedder
from ..logging import get_logger

log = get_logger(__name__)


def index_drift(indexed_sha: str | None, base_sha: str) -> str | None:
    """Why the semantic index is not the tree this diff applies to.

    ``None`` means the index is that base, or the base is unknown so a claim
    of staleness would itself be a guess. ``unindexed`` and ``stale`` are the
    two states that used to degrade retrieval with no trace.
    """
    if not base_sha:
        return None
    if not indexed_sha:
        return "unindexed"
    if indexed_sha != base_sha:
        return "stale"
    return None


def index_drift_note(kind: str | None, indexed_sha: str | None, base_sha: str) -> str | None:
    if kind == "unindexed":
        return (
            "This repository has no semantic index. "
            "Anything not in the diff is unknown; do not invent callers or conventions."
        )
    if kind == "stale":
        have = (indexed_sha or "")[:12]
        want = base_sha[:12]
        return (
            f"The semantic index is commit {have}, not this pull request's base {want}. "
            "Retrieved code may be stale. A disagreement between it and the diff is unresolved."
        )
    return None


@dataclass
class ReviewContext:
    pr: PullRequestContext
    diff_text: str
    chunks: list[CodeChunk] = field(default_factory=list)
    conventions: list[str] = field(default_factory=list)
    truncated: bool = False
    index_drift: str | None = None
    indexed_sha: str | None = None

    def render_repository_context(self, max_chars: int | None = None) -> str:
        if max_chars is None:
            max_chars = get_settings().retrieval_context_chars
        note = index_drift_note(self.index_drift, self.indexed_sha, self.pr.base_sha)
        if not self.chunks:
            base = "(no repository context retrieved — treat unseen code as unknown)"
            return f"{note}\n\n{base}" if note else base
        out: list[str] = []
        used = 0
        for chunk in self.chunks:
            block = chunk.render()
            if used + len(block) > max_chars:
                break
            out.append(block)
            used += len(block)
        body = "\n\n".join(out)
        return f"{note}\n\n{body}" if note else body

    def render_conventions(self) -> str:
        if not self.conventions:
            return "(none recorded)"
        return "\n".join(f"- {c}" for c in self.conventions)


async def build_context(pr: PullRequestContext, repo_id: int | None, spine: EventSpine) -> ReviewContext:
    settings = get_settings()
    diff_text, truncated = render_for_prompt(pr.files, settings.max_diff_bytes)
    ctx = ReviewContext(pr=pr, diff_text=diff_text, truncated=truncated)

    if repo_id is None:
        return ctx

    await _note_index_drift(ctx, repo_id, spine)

    started = time.perf_counter()
    embedder = get_embedder()
    source_files = [f for f in pr.files if not f.binary and f.status != "removed"]
    if not source_files:
        return ctx

    queries = [diff_query_text([f], limit=1200) for f in source_files[:12]]
    # The two halves of hybrid search want different shapes: free text for the
    # embedding, a bounded OR of distinctive identifiers for full-text.
    term_queries = [fts_query([f]) for f in source_files[:12]]
    try:
        vectors = await embedder.embed(queries)
    except Exception as exc:
        # Degraded, not dead: agents still get the diff and the conventions, and
        # the prompt tells them unseen code is unknown.
        await spine.error("retrieval.embed_failed", exc)
        log.warning("retrieval.embed_failed", error=str(exc))
        return ctx

    changed_paths = [f.path for f in pr.files]
    # A wide candidate pool per file, merged round-robin rather than by score
    # alone. Three arrangements were measured over 22 real diffs:
    #
    #   fixed quota of top_k//n_files, then sort   recall 0.481
    #   no quota, pure global score sort           recall 0.457
    #   wide pool, round-robin by rank             see below
    #
    # Pure global ranking is worse because one file's neighbourhood monopolises
    # the window; the quota is worse than it looks because a diff touching six
    # files gives each three slots, and 16.4% of the definitions a diff calls
    # ranked inside the final top-k and were dropped before the sort anyway.
    # Round-robin keeps the diversity the quota was buying without capping a
    # file that genuinely has more to contribute.
    per_file = settings.retrieval_top_k
    seen: set[tuple[str, int]] = set()
    per_file_hits: dict[str, list[CodeChunk]] = {}

    for f, vector, terms in zip(source_files[:12], vectors, term_queries, strict=True):
        try:
            hits = await chunk_repo.hybrid_search(
                repo_id, vector, terms, top_k=per_file, exclude_paths=changed_paths
            )
        except Exception as exc:
            await spine.error("retrieval.search_failed", exc, file=f.path)
            continue
        kept: list[CodeChunk] = []
        for hit in hits:
            key = (hit.file_path, hit.start_line)
            if key not in seen:
                seen.add(key)
                kept.append(hit)
        per_file_hits[f.path] = kept

    # Interleave: each file's best, then each file's second, and so on. Within
    # one round the better score goes first, so this is a diversity constraint
    # on an otherwise score-ordered list, not a replacement for scoring.
    merged: list[CodeChunk] = []
    for rank in range(max((len(v) for v in per_file_hits.values()), default=0)):
        row = [hits[rank] for hits in per_file_hits.values() if rank < len(hits)]
        row.sort(key=lambda c: c.score, reverse=True)
        merged.extend(row)
    ctx.chunks = merged[: settings.retrieval_top_k]

    try:
        ctx.conventions = await chunk_repo.conventions_for(repo_id)
    except Exception as exc:
        await spine.error("retrieval.conventions_failed", exc)

    await spine.retrieval(
        "retrieval.hybrid",
        duration_ms=int((time.perf_counter() - started) * 1000),
        files_queried=len(source_files[:12]),
        chunks=len(ctx.chunks),
        conventions=len(ctx.conventions),
        embedder=embedder.name,
        index_drift=ctx.index_drift,
        indexed_sha=ctx.indexed_sha,
    )
    return ctx


async def _note_index_drift(ctx: ReviewContext, repo_id: int, spine: EventSpine) -> None:
    try:
        stored = await chunk_repo.indexed_sha(repo_id)
    except Exception as exc:
        await spine.error("retrieval.index_lookup_failed", exc)
        return
    kind = index_drift(stored, ctx.pr.base_sha)
    ctx.indexed_sha = stored
    ctx.index_drift = kind
    if kind is None:
        return
    await spine.decision(
        "retrieval.index_drift",
        drift=kind,
        indexed_sha=stored,
        base_sha=ctx.pr.base_sha,
    )
