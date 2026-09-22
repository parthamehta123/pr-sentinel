#!/usr/bin/env python3
"""Find the commit that *introduced* a defect, not the one that fixed it.

    python scripts/mine_introducers.py <repo> <fix-sha> <path> [<repo> <fix-sha> <path> ...]

Every case mined before this one is an inverted fix: the change under review is a
patch that puts a known bug back. That is a usable proxy and it is not what a
reviewer sees. The fix already knows where the bug is — its diff is centred on the
defective line, so inverting it yields a small, suspiciously well-aimed patch. A
reviewer sees the commit that shipped the defect in the first place: usually a
feature or a refactor, usually larger, with the defect one line among many that
all look equally plausible.

This walks back to that commit, then prints it for a human to judge.

Why `git log -S` and not `git blame`
------------------------------------
Blaming the fix's parent is the obvious approach and it does not survive contact
with real repositories. Two things break it:

  * The fix's merge SHA usually does not resolve. GitHub's `merge_commit_sha` is
    a test-merge it computed for the PR page; for older PRs it was frequently
    never pushed, so the clone has never heard of it.
  * Blame lands on reformatting. Every one of these repositories has a commit
    like tornado's `e211ec0a "adding black formatter to all the code"` touching
    300+ files, and blame faithfully reports it as the author of every line.
    Skipping such commits by subject and file count is a heuristic that has to
    be re-tuned per repository, and it still misses smaller reindents.

`git log -S<content>` searches for the commits that changed the *number of
occurrences* of a string. Reformatting does not change that count, so a pure
whitespace commit is invisible to it for free — no heuristic, no tuning. The
earliest such commit is the one that introduced the line.

The tradeoff: it matches on content, so a line that is not distinctive (`pass`,
`return None`) will match everywhere. Pass a distinctive fragment of the
defective line, which in practice is what a fix's removed line is anyway.

Needs a full clone. Clones are cached under the scratch directory and reused.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

CACHE = Path(tempfile.gettempdir()) / "pr-sentinel-clones"


def run(*args: str, cwd: Path | None = None) -> str:
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell
        args, capture_output=True, text=True, cwd=cwd
    )
    return out.stdout if out.returncode == 0 else ""


def clone(repo: str) -> Path | None:
    target = CACHE / repo.replace("/", "__")
    if not (target / ".git").exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        print(f"  cloning {repo} (full history)…", file=sys.stderr)
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "clone", "--quiet", f"https://github.com/{repo}.git", str(target)],  # noqa: S607
            capture_output=True,
        )
    return target if (target / ".git").exists() else None


def removed_lines(work: Path, fix: str, path: str) -> list[str]:
    """The content the fix deleted — the defective lines themselves."""
    diff = run("git", "diff", f"{fix}^1", fix, "--", path, cwd=work)
    out = []
    for line in diff.splitlines():
        if line.startswith("-") and not line.startswith("---"):
            body = line[1:].strip()
            if len(body) > 12:  # too short to be distinctive
                out.append(body)
    return out


def introducer(work: Path, fix: str, path: str, content: str) -> dict | None:
    """The earliest commit that added `content` to `path`, at or before `fix`."""
    log = run(
        "git",
        "log",
        "-S",
        content,
        "--reverse",
        "--format=%H%x00%ad%x00%s",
        "--date=short",
        f"{fix}^1",
        "--",
        path,
        cwd=work,
    )
    for row in log.splitlines():
        sha, _, rest = row.partition("\0")
        date, _, subject = rest.partition("\0")
        stat = run("git", "show", "--stat", "--format=", sha, cwd=work)
        files = sum(1 for x in stat.splitlines() if "|" in x)
        return {"sha": sha[:8], "date": date, "subject": subject, "files": files}
    return None


def main() -> int:
    args = sys.argv[1:]
    if not args or len(args) % 3:
        print(__doc__, file=sys.stderr)
        return 2

    for repo, fix, path in zip(args[0::3], args[1::3], args[2::3], strict=True):
        print(f"\n{repo} {fix} {path}")
        work = clone(repo)
        if work is None:
            print("  could not clone")
            continue

        fixed = run("git", "log", "-1", "--format=%ad %s", "--date=short", fix, cwd=work).strip()
        print(f"  fix         {fix[:8]}  {fixed}")

        candidates = removed_lines(work, fix, path)
        if not candidates:
            print("  the fix removes no distinctive line — nothing to search for")
            continue

        seen: set[str] = set()
        for content in candidates:
            hit = introducer(work, fix, path, content)
            if hit is None or hit["sha"] in seen:
                continue
            seen.add(hit["sha"])
            print(f"  introduced  {hit['sha']}  {hit['date']} {hit['subject']}")
            print(f"              {hit['files']} file(s) changed")
            print(f"              via: {content[:80]}")

        if not seen:
            print(
                "  no introducing commit found — the line predates this path "
                "(try following the file through its renames)"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
