"""Build the semantic index for a repository from a local checkout.

Deliberately filesystem-based rather than API-based: indexing a repo through the
GitHub contents API is thousands of requests against a rate limit, and every CI
runner already has the tree on disk.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..db.repositories import chunks as chunk_repo
from ..llm.embeddings import get_embedder
from ..logging import get_logger
from .chunking import Chunk, chunk_file, should_index

log = get_logger(__name__)


async def index_repository(repo_id: int, root: Path, commit_sha: str, batch_size: int = 64) -> dict:
    files = _walk(root)
    all_chunks: list[Chunk] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(root))
        all_chunks.extend(chunk_file(rel, text))

    embedder = get_embedder()
    rows: list[dict] = []
    for i in range(0, len(all_chunks), batch_size):
        batch = all_chunks[i : i + batch_size]
        vectors = await embedder.embed([c.content for c in batch])
        rows.extend(c.as_row(v) for c, v in zip(batch, vectors, strict=True))
        log.info("index.progress", done=min(i + batch_size, len(all_chunks)), total=len(all_chunks))

    written = await chunk_repo.replace_repo_chunks(repo_id, commit_sha, rows)
    log.info("index.done", files=len(files), chunks=written, embedder=embedder.name)
    return {"files": len(files), "chunks": written, "embedder": embedder.name}


def _walk(root: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") or d in {".github"}]
        for name in filenames:
            p = Path(dirpath) / name
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if should_index(str(p.relative_to(root)), size):
                out.append(p)
    return out
