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


@dataclass
class ReviewContext:
    pr: PullRequestContext
    diff_text: str
    chunks: list[CodeChunk] = field(default_factory=list)
    conventions: list[str] = field(default_factory=list)
    truncated: bool = False

    def render_repository_context(self, max_chars: int = 24_000) -> str:
        if not self.chunks:
            return "(no repository context retrieved — treat unseen code as unknown)"
        out: list[str] = []
        used = 0
        for chunk in self.chunks:
            block = chunk.render()
            if used + len(block) > max_chars:
                break
            out.append(block)
            used += len(block)
        return "\n\n".join(out)

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
    per_file = max(2, settings.retrieval_top_k // max(len(source_files[:12]), 1))
    seen: set[tuple[str, int]] = set()
    collected: list[CodeChunk] = []

    for f, vector, terms in zip(source_files[:12], vectors, term_queries, strict=True):
        try:
            hits = await chunk_repo.hybrid_search(
                repo_id, vector, terms, top_k=per_file, exclude_paths=changed_paths
            )
        except Exception as exc:
            await spine.error("retrieval.search_failed", exc, file=f.path)
            continue
        for hit in hits:
            key = (hit.file_path, hit.start_line)
            if key not in seen:
                seen.add(key)
                collected.append(hit)

    collected.sort(key=lambda c: c.score, reverse=True)
    ctx.chunks = collected[: settings.retrieval_top_k]

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
    )
    return ctx
