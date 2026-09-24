#!/usr/bin/env python3
"""Measure repository retrieval: does it surface the definitions a diff calls?

    python scripts/eval_retrieval.py [--repo tornadoweb/tornado] [--top-k 12]

What is measured
----------------
Every other component in this project has a number and retrieval has never had
one. The hard part is ground truth: "relevant context" is a judgement, and a
relevance set picked by hand by whoever also reads the results is worth about as
much as a negative control written by whoever writes the labels — which this
repository has already been burned by four times.

So the ground truth here is mechanical. For a diff, take the identifiers the
added lines *call* but do not define, and find where the repository defines them.
Those definitions are what a reviewer has to look up to review the change, they
are derivable by grep rather than by opinion, and they are exactly what
repository retrieval exists to supply.

Metrics are the standard ones for that question:

  recall@k   of the definitions the diff needs, how many were retrieved
  MRR        how far down the list the first useful one sits
  precision  how much of the returned context was one of them

A caveat that belongs in the headline, not a footnote: `exclude_paths` removes
the changed files from the results, so a definition that lives in a file the diff
also touches cannot be retrieved by construction and is dropped from the truth
set rather than counted as a miss.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
CLONES = Path(tempfile.gettempdir()) / "pr-sentinel-clones"

CALL = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]{3,})\s*\(")
DEFINES = re.compile(r"^\s*(?:def|class)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.M)


def git(*args: str, cwd: Path) -> str:
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", *args],  # noqa: S607
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    return out.stdout


def called_but_not_defined(added: str) -> set[str]:
    """Identifiers the added lines call and do not themselves define."""
    called = set(CALL.findall(added))
    defined = set(DEFINES.findall(added))
    builtins = set(dir(__builtins__)) | {
        "print",
        "len",
        "str",
        "int",
        "dict",
        "list",
        "set",
        "tuple",
        "super",
        "isinstance",
        "getattr",
        "setattr",
        "hasattr",
        "range",
        "open",
        "format",
        "type",
        "bool",
        "float",
        "bytes",
        "sorted",
        "enumerate",
        "zip",
        "min",
        "max",
    }
    return {c for c in called - defined - builtins if not c.startswith("_test")}


def definitions_in(root: Path, names: set[str], skip: set[str]) -> dict[str, set[str]]:
    """Where the checkout defines each name, excluding the changed files."""
    found: dict[str, set[str]] = {}
    for path in root.rglob("*.py"):
        rel = str(path.relative_to(root))
        if rel in skip or "/test" in rel or rel.startswith("test"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name in DEFINES.findall(text):
            if name in names:
                found.setdefault(name, set()).add(rel)
    return found


async def measure(repo: str, shas: list[str], top_k: int) -> None:
    from pr_sentinel.db import pool
    from pr_sentinel.db.repositories import chunks as chunk_repo
    from pr_sentinel.forge.diff import diff_query_text, fts_query
    from pr_sentinel.llm.embeddings import get_embedder
    from pr_sentinel.retrieval.indexer import index_repository

    work = CLONES / repo.replace("/", "__")
    if not (work / ".git").exists():
        print(f"no clone at {work}", file=sys.stderr)
        return

    await pool.get_pool()
    # A scratch repository row the measurement owns, so indexing cannot disturb
    # a real one. Reused across runs; its chunks are replaced each time.
    repo_id = await pool.fetchval(
        """
        INSERT INTO repositories (github_repo_id, full_name, default_branch)
        VALUES (-999, $1, 'main')
        ON CONFLICT (github_repo_id) DO UPDATE SET full_name = EXCLUDED.full_name
        RETURNING id
        """,
        f"retrieval-eval/{repo.replace('/', '-')}",
    )
    embedder = get_embedder()
    print(f"embedder: {embedder.name}  (a hashing embedder is lexical, not semantic)\n")

    rows = []
    for sha in shas:
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
        names = called_but_not_defined(added)
        truth = definitions_in(work, names, skip=set(changed))
        if not truth:
            print(f"{sha[:8]}  no externally-defined callee — skipped")
            continue

        stats = await index_repository(repo_id, work, sha)
        relevant = {p for paths in truth.values() for p in paths}

        # Query exactly as build_context does: per changed file.
        from pr_sentinel.forge.diff import build_diff_file

        # Per-file patches with hunks PARSED. Both halves of the query walk
        # `DiffFile.hunks`; an unparsed DiffFile yields a query built from the
        # file path alone. Every retrieval number recorded before this was
        # measured that way, with the whole content side of the query missing.
        files = []
        for path in changed:
            patch = git("diff", parent, sha, "--", path, cwd=work)
            files.append(
                build_diff_file(
                    {
                        "filename": path,
                        "status": "modified",
                        "patch": "\n".join(patch.splitlines()[4:]),
                    }
                )
            )
        vectors = await embedder.embed([diff_query_text([f], limit=1200) for f in files])
        got: list[str] = []
        for f, vec in zip(files, vectors, strict=True):
            hits = await chunk_repo.hybrid_search(
                repo_id, vec, fts_query([f]), top_k=top_k, exclude_paths=changed
            )
            got.extend(h.file_path for h in hits)

        seen: list[str] = []
        for p in got:
            if p not in seen:
                seen.append(p)
        hit_set = set(seen) & relevant
        rr = 0.0
        for i, p in enumerate(seen, 1):
            if p in relevant:
                rr = 1.0 / i
                break
        rows.append(
            {
                "sha": sha[:8],
                "chunks": stats["chunks"],
                "needed": len(relevant),
                "found": len(hit_set),
                "recall": len(hit_set) / len(relevant),
                "rr": rr,
                "precision": len(hit_set) / max(len(seen), 1),
                "names": sorted(names)[:4],
            }
        )
        r = rows[-1]
        print(
            f"  {r['sha']}  needed {r['needed']:2d}  found {r['found']:2d}  "
            f"recall@{top_k} {r['recall']:.2f}  RR {r['rr']:.2f}  prec {r['precision']:.2f}",
            flush=True,
        )

    if rows:
        n = len(rows)
        print(
            f"\n  cases {n}   recall@{top_k} {sum(r['recall'] for r in rows) / n:.3f}   "
            f"MRR {sum(r['rr'] for r in rows) / n:.3f}   "
            f"precision {sum(r['precision'] for r in rows) / n:.3f}"
        )
    await pool.close_pool()


def sample_commits(work: Path, n: int) -> list[str]:
    """Commits that touch Python files, spread across the repository's history.

    Hand-picking the commits to measure on would make this another relevance set
    chosen by whoever reads the result. Taking every Nth Python-touching commit
    is arbitrary in a way that cannot be steered.
    """
    # From the remote's default branch, never from current HEAD. `measure` leaves
    # the clone checked out at whichever commit it looked at last, so sampling
    # from HEAD makes the case set depend on the previous run — two arms of a
    # comparison silently get different commits, and the numbers look like a
    # result. Found exactly that way: 21 cases in one arm, 14 in the next.
    ref = git("symbolic-ref", "--short", "refs/remotes/origin/HEAD", cwd=work).strip()
    if not ref:
        ref = "origin/HEAD"
    shas = git("log", "--format=%H", ref, "--", "*.py", cwd=work).split()
    if not shas:
        return []
    step = max(len(shas) // max(n, 1), 1)
    return shas[::step][:n]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="tornadoweb/tornado")
    ap.add_argument("--top-k", type=int, default=12)
    ap.add_argument("--sample", type=int, default=0, help="sample N commits instead of --shas")
    ap.add_argument("--shas", nargs="*", default=["9e965556", "4a4d8717"])
    args = ap.parse_args()

    for line in (ROOT / ".env").read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    shas = args.shas
    if args.sample:
        work = CLONES / args.repo.replace("/", "__")
        shas = sample_commits(work, args.sample)
        print(f"sampled {len(shas)} commits from {args.repo}", file=sys.stderr)
    asyncio.run(measure(args.repo, shas, args.top_k))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
