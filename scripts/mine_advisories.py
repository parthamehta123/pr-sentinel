#!/usr/bin/env python3
"""Find real security fixes to build eval cases from, via the GitHub advisory database.

    python scripts/mine_advisories.py > /tmp/advisories.json

Companion to `mine_cases.py`, which mines ordinary merged fix commits. That
produced six good cases and not one security defect among them, because small
merged fixes skew heavily towards correctness. Advisories are the other end: every
one is a security defect somebody took seriously enough to publish, most link to
the commit that fixed it, and the CWE gives an independent label for the class of
defect.

Same method and the same caveat: a case is built by inverting the fix, so the
change under review is the one that reintroduces the vulnerability. This finds
candidates only — each still has to be read, because "this commit is referenced by
an advisory" does not tell you which line is the defect.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

ECOSYSTEMS = ["pip", "npm", "go"]
PERMISSIVE = {"mit", "apache-2.0", "bsd-3-clause", "bsd-2-clause", "isc", "0bsd"}
CODE_SUFFIXES = (".py", ".ts", ".js", ".go")
MAX_FILES = 3
MAX_CHANGED_LINES = 30
COMMIT_REF = re.compile(r"https://github\.com/([^/]+)/([^/]+)/commit/([0-9a-f]{7,40})")

_licence_cache: dict[str, str | None] = {}


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


def licence_of(repo: str) -> str | None:
    if repo not in _licence_cache:
        meta = gh("api", f"repos/{repo}")
        spdx = None
        if isinstance(meta, dict) and isinstance(meta.get("license"), dict):
            spdx = (meta["license"].get("spdx_id") or "").lower() or None
        _licence_cache[repo] = spdx
    return _licence_cache[repo]


def fixing_commit(advisory: dict) -> tuple[str, str] | None:
    for ref in advisory.get("references") or []:
        m = COMMIT_REF.match(ref)
        if m:
            return f"{m.group(1)}/{m.group(2)}", m.group(3)
    return None


def candidates(ecosystem: str, pages: int = 3) -> list[dict]:
    out = []
    for page in range(1, pages + 1):
        batch = gh(
            "api",
            f"/advisories?ecosystem={ecosystem}&type=reviewed&per_page=100&page={page}",
        )
        if not isinstance(batch, list) or not batch:
            break
        for adv in batch:
            if adv.get("severity") not in ("high", "critical", "moderate"):
                continue
            found = fixing_commit(adv)
            if not found:
                continue
            repo, sha = found
            licence = licence_of(repo)
            if licence not in PERMISSIVE:
                continue
            commit = gh("api", f"repos/{repo}/commits/{sha}")
            if not isinstance(commit, dict):
                continue
            files = [
                f
                for f in commit.get("files", [])
                if f["filename"].endswith(CODE_SUFFIXES)
                and "test" not in f["filename"].lower()
                and f.get("patch")
            ]
            if not files or len(files) > MAX_FILES:
                continue
            churn = sum(f["additions"] + f["deletions"] for f in files)
            if not 2 <= churn <= MAX_CHANGED_LINES:
                continue
            out.append(
                {
                    "ghsa": adv["ghsa_id"],
                    "severity": adv["severity"],
                    "cwes": [c["cwe_id"] for c in adv.get("cwes", [])],
                    "summary": adv.get("summary", ""),
                    "description": (adv.get("description") or "")[:700],
                    "repo": repo,
                    "licence": licence,
                    "sha": sha[:12],
                    "commit_url": f"https://github.com/{repo}/commit/{sha}",
                    "files": [{"filename": f["filename"], "patch": f["patch"]} for f in files],
                }
            )
            print(f"  {adv['ghsa_id']}  {repo}  {churn} lines", file=sys.stderr)
    return out


def main() -> int:
    found: list[dict] = []
    for eco in ECOSYSTEMS:
        print(f"--- {eco} ---", file=sys.stderr)
        found.extend(candidates(eco))
    print(f"\n  {len(found)} candidates", file=sys.stderr)
    json.dump(found, sys.stdout, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
