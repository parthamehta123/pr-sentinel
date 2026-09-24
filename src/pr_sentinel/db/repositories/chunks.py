"""Semantic memory access: index the repo, retrieve slices of it."""

from __future__ import annotations

from ...domain.models import CodeChunk
from .. import pool


async def replace_repo_chunks(repo_id: int, commit_sha: str, rows: list[dict]) -> int:
    """Swap in a fresh index for one commit, atomically."""
    pg = await pool.get_pool()
    async with pg.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM code_chunks WHERE repo_id = $1", repo_id)
        if rows:
            await conn.executemany(
                """
                INSERT INTO code_chunks (repo_id, commit_sha, file_path, language, symbol,
                                         start_line, end_line, content, content_hash, embedding)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::vector)
                ON CONFLICT DO NOTHING
                """,
                [
                    (
                        repo_id,
                        commit_sha,
                        r["file_path"],
                        r.get("language"),
                        r.get("symbol"),
                        r["start_line"],
                        r["end_line"],
                        r["content"],
                        r["content_hash"],
                        _vec(r["embedding"]),
                    )
                    for r in rows
                ],
            )
        await conn.execute(
            "UPDATE repositories SET indexed_sha = $2, indexed_at = now() WHERE id = $1",
            repo_id,
            commit_sha,
        )
    return len(rows)


def _vec(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in values) + "]"


async def hybrid_search(
    repo_id: int,
    embedding: list[float],
    query_text: str,
    top_k: int = 12,
    exclude_paths: list[str] | None = None,
) -> list[CodeChunk]:
    """Vector kNN fused with full-text search by reciprocal rank.

    Pure vector search is bad at exact identifiers — the thing a reviewer most
    often needs to look up. Pure FTS is bad at "code that does something like
    this". RRF needs no score calibration between the two, which is why it is
    here instead of a tuned weighted sum.
    """
    rows = await pool.fetch(
        """
        WITH params AS (
            SELECT $2::vector AS q_vec,
                   -- NULLIF keeps an empty term list from producing a tsquery that
                   -- matches everything; the vector half then carries the query alone.
                   websearch_to_tsquery('english', NULLIF($3, '')) AS q_fts
        ),
        vec AS (
            SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> p.q_vec) AS rnk
              FROM code_chunks c, params p
             WHERE c.repo_id = $1 AND c.embedding IS NOT NULL
             ORDER BY c.embedding <=> p.q_vec
             LIMIT $4
        ),
        txt AS (
            SELECT c.id, row_number() OVER (ORDER BY ts_rank(c.fts, p.q_fts) DESC) AS rnk
              FROM code_chunks c, params p
             WHERE c.repo_id = $1 AND p.q_fts IS NOT NULL AND c.fts @@ p.q_fts
             ORDER BY ts_rank(c.fts, p.q_fts) DESC
             LIMIT $4
        ),
        fused AS (
            SELECT id, sum(1.0 / (60 + rnk)) AS score
              FROM (SELECT * FROM vec UNION ALL SELECT * FROM txt) u
             GROUP BY id
        )
        SELECT c.file_path, c.start_line, c.end_line, c.content, c.symbol, c.language,
               f.score
          FROM fused f
          JOIN code_chunks c ON c.id = f.id
         WHERE ($5::text[] IS NULL OR NOT (c.file_path = ANY($5::text[])))
         ORDER BY f.score DESC
         LIMIT $6
        """,
        repo_id,
        _vec(embedding),
        query_text or "code",
        max(top_k * 4, 40),
        exclude_paths or None,
        top_k,
    )
    return [
        CodeChunk(
            file_path=r["file_path"],
            start_line=r["start_line"],
            end_line=r["end_line"],
            content=r["content"],
            symbol=r["symbol"],
            language=r["language"],
            score=float(r["score"]),
        )
        for r in rows
    ]


async def conventions_for(repo_id: int) -> list[str]:
    rows = await pool.fetch(
        """
        SELECT rule FROM conventions
         WHERE active AND (repo_id = $1 OR repo_id IS NULL)
         ORDER BY repo_id NULLS LAST, id
         LIMIT 60
        """,
        repo_id,
    )
    return [r["rule"] for r in rows]


async def indexed_sha(repo_id: int) -> str | None:
    """The commit the semantic index was built from, or None if never indexed."""
    return await pool.fetchval("SELECT indexed_sha FROM repositories WHERE id = $1", repo_id)


async def embedding_column_dim() -> int | None:
    """Read the declared vector width straight out of the catalog.

    The one bug that costs the most: the column says 1536, the env says 256, and
    nobody finds out until the first INSERT after a full embedding run.
    """
    return await pool.fetchval(
        """
        SELECT atttypmod
          FROM pg_attribute
         WHERE attrelid = 'code_chunks'::regclass AND attname = 'embedding'
        """
    )
