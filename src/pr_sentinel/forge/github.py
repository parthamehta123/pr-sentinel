"""GitHub REST client.

Only the four calls this system actually makes. Everything is wrapped in the
same retry + circuit breaker as the model calls, and `Retry-After` is honoured
rather than guessed at — GitHub's secondary rate limiter is unforgiving about
clients that ignore it.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..config import get_settings
from ..domain.models import DiffFile, Finding, PullRequestContext
from ..logging import get_logger
from ..reliability.circuit import breaker
from ..reliability.retry import RetryPolicy, with_retry
from .diff import build_diff_file

log = get_logger(__name__)


class GitHubError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"github {status}: {message}")
        self.status = status
        self.message = message


class RetryableGitHubError(GitHubError):
    """5xx, 429 and connection failures — worth another attempt."""


def _retry_after(exc: BaseException) -> float | None:
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    header = resp.headers.get("retry-after")
    if header:
        try:
            return min(float(header), 60.0)
        except ValueError:
            return None
    if resp.headers.get("x-ratelimit-remaining") == "0":
        return 60.0
    return None


class GitHubClient:
    def __init__(self, token: str = "", api_url: str = "", timeout: float = 20.0) -> None:
        s = get_settings()
        self._client = httpx.AsyncClient(
            base_url=api_url or s.github_api_url,
            timeout=timeout or s.github_timeout_s,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "pr-sentinel/0.1",
                **(
                    {"Authorization": f"Bearer {token or s.github_token}"}
                    if (token or s.github_token)
                    else {}
                ),
            },
        )
        self._breaker = breaker("github", failure_threshold=5, recovery_time=30.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        async def _once() -> Any:
            try:
                resp = await self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                raise RetryableGitHubError(0, str(exc)) from exc
            if resp.status_code in (429, 500, 502, 503, 504):
                err = RetryableGitHubError(resp.status_code, resp.text[:300])
                err.response = resp  # type: ignore[attr-defined]
                raise err
            if resp.status_code >= 400:
                raise GitHubError(resp.status_code, resp.text[:300])
            return resp.json() if resp.content else None

        policy = RetryPolicy(
            attempts=4,
            base_delay=1.0,
            max_delay=30.0,
            retry_on=(RetryableGitHubError,),
            give_up_on=(GitHubError,),
        )
        return await with_retry(
            lambda: self._breaker.call(_once),
            policy,
            name=f"github.{method} {path}",
            retry_after=_retry_after,
        )

    async def get_pull_request(self, repo: str, number: int) -> dict:
        return await self._request("GET", f"/repos/{repo}/pulls/{number}")

    async def list_files(self, repo: str, number: int, max_files: int = 300) -> list[DiffFile]:
        files: list[DiffFile] = []
        page = 1
        while len(files) < max_files:
            batch = await self._request(
                "GET",
                f"/repos/{repo}/pulls/{number}/files",
                params={"per_page": 100, "page": page},
            )
            if not batch:
                break
            files.extend(build_diff_file(item) for item in batch)
            if len(batch) < 100:
                break
            page += 1
        return files[:max_files]

    async def build_context(self, repo: str, number: int, repo_github_id: int) -> PullRequestContext:
        pr = await self.get_pull_request(repo, number)
        files = await self.list_files(repo, number)
        return PullRequestContext(
            repo_full_name=repo,
            repo_github_id=repo_github_id,
            is_private=bool((pr.get("base", {}).get("repo") or {}).get("private", True)),
            number=number,
            title=pr.get("title") or "",
            body=(pr.get("body") or "")[:4000],
            author=(pr.get("user") or {}).get("login") or "",
            head_sha=pr["head"]["sha"],
            base_sha=pr["base"]["sha"],
            base_ref=pr["base"].get("ref", "main"),
            files=files,
        )

    async def post_review(
        self,
        repo: str,
        number: int,
        commit_sha: str,
        body: str,
        findings: list[Finding],
    ) -> int | None:
        """Post one review with inline comments.

        One review, not N standalone comments: a single notification, and one
        thing to dismiss. `event=COMMENT` because this system is advisory — it
        never blocks a merge on its own judgement.
        """
        comments = [
            {
                "path": f.file_path,
                "line": f.line_end,
                "side": "RIGHT",
                "body": _render_comment(f),
            }
            for f in findings
        ]
        payload: dict[str, Any] = {
            "commit_id": commit_sha,
            "body": body,
            "event": "COMMENT",
        }
        if comments:
            payload["comments"] = comments
        try:
            result = await self._request("POST", f"/repos/{repo}/pulls/{number}/reviews", json=payload)
        except GitHubError as exc:
            # 422 here is almost always a line that is not in the diff. The
            # grounding filter should have caught it; fall back to a summary-only
            # review rather than losing the whole thing.
            if exc.status == 422 and comments:
                log.warning("github.inline_rejected", repo=repo, pr=number, error=exc.message)
                result = await self._request(
                    "POST",
                    f"/repos/{repo}/pulls/{number}/reviews",
                    json={
                        "commit_id": commit_sha,
                        "body": body + _fallback_appendix(findings),
                        "event": "COMMENT",
                    },
                )
            else:
                raise
        return int(result["id"]) if result and "id" in result else None


_SEVERITY_BADGE = {
    "critical": "🔴 **critical**",
    "major": "🟠 **major**",
    "minor": "🟡 minor",
    "info": "🔵 info",
}


def _render_comment(f: Finding) -> str:
    return (
        f"{_SEVERITY_BADGE.get(str(f.severity), str(f.severity))} · `{f.category}` · "
        f"{f.agent} agent · confidence {f.confidence:.2f}\n\n"
        f"**{f.title}**\n\n{f.body}\n\n"
        f"<details><summary>Why this was raised</summary>\n\n{f.rationale}\n\n</details>"
    )


def _fallback_appendix(findings: list[Finding]) -> str:
    lines = ["\n\n---\n\n_Inline comments were rejected; findings listed here instead._\n"]
    for f in findings:
        lines.append(f"- `{f.file_path}:{f.line_start}` — **{f.title}** ({f.severity}, {f.confidence:.2f})")
    return "\n".join(lines)
