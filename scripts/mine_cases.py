#!/usr/bin/env python3
"""Find real merged bug-fix pull requests to build eval cases from.

    python scripts/mine_cases.py > /tmp/candidates.json

Every case in this set so far was written by me, which means I knew where the
defect was before the reviewer did. Real defects are the only cure for that, and
merged fix commits are the cleanest ground truth available: a human looked at the
code, decided it was wrong, and changed it. The fix says what the defect was.

The cases are built by **inverting** a fix — the change under review is the one
that reintroduces the bug. That is not identical to the pull request that
originally introduced it, and the writeup says so; what it does give is a defect
whose existence was judged by someone other than me.

This script only finds candidates. Every one still has to be read and labelled by
hand, because "this commit says fix" is not the same as "here is the defect and
here is where it is".
"""

from __future__ import annotations

import json
import subprocess
import sys

# Permissively licensed, actively reviewed, and written in languages the set
# already covers. Provenance and licence travel with every case built from them.
SOURCES = [
    ("encode/httpx", "BSD-3-Clause"),
    ("psf/requests", "Apache-2.0"),
    ("pallets/click", "BSD-3-Clause"),
    ("pallets/flask", "BSD-3-Clause"),
    ("urllib3/urllib3", "MIT"),
    ("pydantic/pydantic", "MIT"),
    ("fastapi/fastapi", "MIT"),
    ("encode/starlette", "BSD-3-Clause"),
    ("tornadoweb/tornado", "Apache-2.0"),
    ("aio-libs/aiohttp", "Apache-2.0"),
]

# Words that suggest a defect was fixed rather than a feature added.
FIX_WORDS = (
    "fix",
    "bug",
    "regression",
    "incorrect",
    "wrong",
    "crash",
    "leak",
    "race",
    "off-by-one",
    "None",
    "null",
    "broken",
    "fail",
    "error",
    "typo-free",
)
# Words that usually mean a rename, a version bump or a docs change.
SKIP_WORDS = (
    "bump",
    "release",
    "changelog",
    "typo",
    "docs:",
    "readme",
    "lint",
    "pre-commit",
    "dependabot",
    "version",
    "translat",
)

MAX_FILES = 2
MAX_CHANGED_LINES = 24
CODE_SUFFIXES = (".py", ".ts", ".go")


def gh(*args: str) -> object:
    out = subprocess.run(  # noqa: S603 - local helper, fixed argv, no shell
        ["gh", *args],  # noqa: S607 - `gh` on PATH is the documented way to call it
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


def looks_like_a_fix(title: str) -> bool:
    low = title.lower()
    if any(w in low for w in SKIP_WORDS):
        return False
    return any(w in low for w in FIX_WORDS)


def candidates_for(repo: str, licence: str, per_repo: int = 60) -> list[dict]:
    prs = gh("api", f"repos/{repo}/pulls?state=closed&per_page={per_repo}&sort=updated")
    if not isinstance(prs, list):
        return []
    out = []
    for pr in prs:
        if not pr.get("merged_at") or not looks_like_a_fix(pr.get("title", "")):
            continue
        detail = gh("api", f"repos/{repo}/pulls/{pr['number']}")
        if not isinstance(detail, dict):
            continue
        if (detail.get("changed_files") or 99) > MAX_FILES:
            continue
        churn = (detail.get("additions") or 0) + (detail.get("deletions") or 0)
        if not 2 <= churn <= MAX_CHANGED_LINES:
            continue
        files = gh("api", f"repos/{repo}/pulls/{pr['number']}/files")
        if not isinstance(files, list):
            continue
        code = [f for f in files if f["filename"].endswith(CODE_SUFFIXES) and "test" not in f["filename"]]
        if len(code) != 1 or not code[0].get("patch"):
            continue
        out.append(
            {
                "repo": repo,
                "licence": licence,
                "number": pr["number"],
                "title": pr["title"],
                "url": pr["html_url"],
                "merge_commit": detail.get("merge_commit_sha", "")[:12],
                "additions": detail["additions"],
                "deletions": detail["deletions"],
                "file": code[0]["filename"],
                "patch": code[0]["patch"],
                "body": (pr.get("body") or "")[:600],
            }
        )
    return out


def main() -> int:
    found: list[dict] = []
    for repo, licence in SOURCES:
        got = candidates_for(repo, licence)
        print(f"  {repo:<24} {len(got)} candidate(s)", file=sys.stderr)
        found.extend(got)
    print(f"\n  {len(found)} total", file=sys.stderr)
    json.dump(found, sys.stdout, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
