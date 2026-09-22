"""A migration runner small enough to read in one sitting.

Statements are applied one at a time because `CREATE MATERIALIZED VIEW ... WITH
(timescaledb.continuous)` refuses to run inside a transaction block, and a
multi-statement simple query is exactly that. Splitting therefore has to respect
dollar-quoted function bodies, which is the only interesting part of this file.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import asyncpg

from ..logging import get_logger

log = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"

_LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_DOLLAR_TAG = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")


def split_statements(sql: str) -> list[str]:
    """Split on top-level `;`, honouring '…', "…", -- comments and $tag$ bodies."""
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    dollar_tag: str | None = None
    in_single = in_double = in_line_comment = False
    in_block_comment = False

    while i < n:
        ch = sql[i]
        two = sql[i : i + 2]

        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            buf.append(ch)
            if two == "*/":
                buf.append(sql[i + 1])
                i += 2
                in_block_comment = False
                continue
            i += 1
            continue
        if dollar_tag is not None:
            if sql.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
                continue
            buf.append(ch)
            i += 1
            continue
        if in_single:
            buf.append(ch)
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            buf.append(ch)
            if ch == '"':
                in_double = False
            i += 1
            continue

        if two == "--":
            in_line_comment = True
            buf.append(two)
            i += 2
            continue
        if two == "/*":
            in_block_comment = True
            buf.append(two)
            i += 2
            continue
        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            buf.append(ch)
            i += 1
            continue
        if ch == "$":
            m = _DOLLAR_TAG.match(sql, i)
            if m:
                dollar_tag = m.group(0)
                buf.append(dollar_tag)
                i += len(dollar_tag)
                continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return [s for s in out if not _is_only_comments(s)]


def _is_only_comments(stmt: str) -> bool:
    stripped = "\n".join(
        line for line in stmt.splitlines() if line.strip() and not line.strip().startswith("--")
    ).strip()
    return not stripped


async def apply_migrations(conn: asyncpg.Connection, directory: Path | None = None) -> list[str]:
    directory = directory or MIGRATIONS_DIR
    await conn.execute(_LEDGER)
    applied: list[str] = []

    for path in sorted(directory.glob("*.sql")):
        sql = path.read_text()
        checksum = hashlib.sha256(sql.encode()).hexdigest()[:16]
        row = await conn.fetchrow("SELECT checksum FROM schema_migrations WHERE filename = $1", path.name)
        if row is not None:
            if row["checksum"] != checksum:
                log.warning(
                    "migrate.checksum_drift",
                    file=path.name,
                    note="already applied with different contents; not re-running",
                )
            continue

        for stmt in split_statements(sql):
            try:
                await conn.execute(stmt)
            except asyncpg.PostgresError as exc:
                raise RuntimeError(
                    f"{path.name}: statement failed: {exc}\n--- statement ---\n{stmt[:600]}"
                ) from exc

        await conn.execute(
            "INSERT INTO schema_migrations (filename, checksum) VALUES ($1, $2)",
            path.name,
            checksum,
        )
        applied.append(path.name)
        log.info("migrate.applied", file=path.name)

    return applied
