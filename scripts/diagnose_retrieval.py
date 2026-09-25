#!/usr/bin/env python3
"""Why a needed definition is not retrieved: never a candidate, or ranked too low?

    python scripts/diagnose_retrieval.py [--sample N]

`eval_retrieval.py` says how often retrieval finds the definitions a diff calls.
This says why it misses the rest, by splitting each miss into causes that have
different fixes:

  never indexed   the chunker never produced a chunk for that file
  in top-k        retrieved
  just outside    ranked within reach of a slightly wider window
  far outside     a ranking failure, not a window problem
  cut by split    would have been in reach but the per-file quota dropped it

An earlier version of this ran against DiffFile objects with no hunks parsed, so
every query was the file path alone and every conclusion from it was withdrawn.
It builds queries through `build_diff_file` now, the same path production uses.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_retrieval import (  # noqa: E402
    CLONES,
    called_but_not_defined,
    definitions_in,
    git,
    sample_commits,
)

REPOS = ("tornadoweb/tornado", "urllib3/urllib3", "psf/requests", "scrapy/scrapy")


async def diagnose(repo: str, n: int, top_k: int, acc: dict) -> None:
    from pr_sentinel.db import pool
    from pr_sentinel.db.repositories import chunks as chunk_repo
    from pr_sentinel.forge.diff import build_diff_file, diff_query_text, fts_query
    from pr_sentinel.llm.embeddings import get_embedder
    from pr_sentinel.retrieval.indexer import index_repository

    work = CLONES / repo.replace("/", "__")
    await pool.get_pool()
    repo_id = await pool.fetchval(
        """INSERT INTO repositories (github_repo_id, full_name, default_branch)
           VALUES (-998, $1, 'main')
           ON CONFLICT (github_repo_id) DO UPDATE SET full_name = EXCLUDED.full_name
           RETURNING id""",
        f"diagnose/{repo.replace('/', '-')}",
    )
    embedder = get_embedder()

    for sha in sample_commits(work, n):
        parent = f"{sha}^1"
        git("checkout", "--quiet", "--force", parent, cwd=work)
        changed = [p for p in git("diff", "--name-only", parent, sha, cwd=work).split() if p.endswith(".py")]
        if not changed:
            continue
        added = "\n".join(
            line[1:]
            for line in git("diff", parent, sha, cwd=work).splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        truth = definitions_in(work, called_but_not_defined(added), skip=set(changed))
        if not truth:
            continue
        await index_repository(repo_id, work, sha)
        relevant = {p for paths in truth.values() for p in paths}

        files = []
        for path in changed:
            patch = git("diff", parent, sha, "--", path, cwd=work)
            files.append(
                build_diff_file(
                    {"filename": path, "status": "modified", "patch": "\n".join(patch.splitlines()[4:])}
                )
            )
        vectors = await embedder.embed([diff_query_text([f], limit=1200) for f in files])
        per_file = max(2, top_k // max(len(files), 1))

        best: dict[str, int] = {}
        within_quota: set[str] = set()
        for f, vector in zip(files, vectors, strict=True):
            hits = await chunk_repo.hybrid_search(
                repo_id, vector, fts_query([f]), top_k=200, exclude_paths=changed
            )
            for rank, hit in enumerate(hits, 1):
                if hit.file_path in relevant:
                    best[hit.file_path] = min(best.get(hit.file_path, 10**6), rank)
                    if rank <= per_file:
                        within_quota.add(hit.file_path)

        indexed = {
            r["file_path"]
            for r in await pool.fetch(
                "SELECT DISTINCT file_path FROM code_chunks WHERE repo_id = $1", repo_id
            )
        }
        for path in relevant:
            acc["needed"] = acc.get("needed", 0) + 1
            if path not in indexed:
                acc["never_indexed"] = acc.get("never_indexed", 0) + 1
                continue
            rank = best.get(path)
            if rank is None or rank > 50:
                acc["far_outside"] = acc.get("far_outside", 0) + 1
            elif rank <= top_k:
                acc["in_top_k"] = acc.get("in_top_k", 0) + 1
            else:
                acc["just_outside"] = acc.get("just_outside", 0) + 1
            if rank is not None and rank <= top_k and path not in within_quota:
                acc["cut_by_split"] = acc.get("cut_by_split", 0) + 1
    await pool.close_pool()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=12)
    ap.add_argument("--top-k", type=int, default=20)
    args = ap.parse_args()
    for line in (ROOT / ".env").read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    acc: dict = {}
    for repo in REPOS:
        asyncio.run(diagnose(repo, args.sample, args.top_k, acc))
        print(f"  {repo:<22} running total {acc}", file=sys.stderr)
    n = acc.get("needed", 0) or 1
    print(f"\n  needed definitions: {acc.get('needed', 0)}   (top_k={args.top_k})")
    for key in ("never_indexed", "in_top_k", "just_outside", "far_outside", "cut_by_split"):
        v = acc.get(key, 0)
        print(f"    {key:<14} {v:4d}  ({v / n:.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
