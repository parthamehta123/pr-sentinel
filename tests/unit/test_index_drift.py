"""A stale semantic index used to look identical to a current one."""

from __future__ import annotations

import pytest

from pr_sentinel.db.repositories import chunks as chunk_repo
from pr_sentinel.events.spine import EventSpine
from pr_sentinel.retrieval.context import (
    build_context,
    index_drift,
    index_drift_note,
)


def test_a_matching_commit_is_not_drift():
    sha = "a" * 40
    assert index_drift(sha, sha) is None
    assert index_drift_note(None, sha, sha) is None


def test_missing_index_and_a_different_commit_are_distinct():
    base = "b" * 40
    assert index_drift(None, base) == "unindexed"
    assert index_drift("c" * 40, base) == "stale"
    # No base means we cannot tell, and must not invent a warning.
    assert index_drift(None, "") is None
    assert index_drift("c" * 40, "") is None


def test_the_note_names_the_two_commits_and_does_not_invent_a_caller():
    note = index_drift_note("stale", "c" * 40, "b" * 40)
    assert note is not None
    assert "cccccccccccc" in note
    assert "bbbbbbbbbbbb" in note
    unindexed = index_drift_note("unindexed", None, "b" * 40)
    assert unindexed is not None
    assert "no semantic index" in unindexed


def test_the_prompt_carries_the_warning_above_whatever_was_retrieved(pr_context):
    from pr_sentinel.retrieval.context import ReviewContext

    ctx = ReviewContext(pr=pr_context, diff_text="", index_drift="unindexed")
    rendered = ctx.render_repository_context()
    assert rendered.startswith("This repository has no semantic index")
    assert "treat unseen code as unknown" in rendered

    current = ReviewContext(pr=pr_context, diff_text="")
    assert "semantic index" not in current.render_repository_context()


@pytest.mark.asyncio
async def test_build_context_records_drift_without_retrieving(pr_context, monkeypatch):
    pr_context.files = []
    seen: list[tuple[str, dict]] = []

    async def sha(_repo_id: int) -> str:
        return "c" * 40

    async def decision(self, name: str, **attrs):
        seen.append((name, attrs))

    monkeypatch.setattr(chunk_repo, "indexed_sha", sha)
    monkeypatch.setattr(EventSpine, "decision", decision)

    ctx = await build_context(pr_context, repo_id=1, spine=EventSpine())
    assert ctx.index_drift == "stale"
    assert ctx.indexed_sha == "c" * 40
    assert seen == [
        (
            "retrieval.index_drift",
            {"drift": "stale", "indexed_sha": "c" * 40, "base_sha": "b" * 40},
        )
    ]
    assert "not this pull request's base" in ctx.render_repository_context()
