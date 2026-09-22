"""Operator CLI.

pr-sentinel migrate                      apply SQL migrations
pr-sentinel doctor                       check config, extensions, dimensions
pr-sentinel index owner/repo ./checkout  build the semantic index
pr-sentinel replay owner/repo 42         review a real PR on demand
pr-sentinel trace <review-id>            print the event spine for one review
pr-sentinel costs                        spend by agent over the last day
pr-sentinel queue                        the human queue, most urgent first
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import typer

from .config import get_settings
from .logging import configure_logging, get_logger

app = typer.Typer(add_completion=False, help="pr-sentinel operator commands")


class _Rollback(Exception):
    """Aborts a probe transaction so the check leaves no trace."""


log = get_logger(__name__)


def _run(coro):
    configure_logging(get_settings().log_level)
    return asyncio.run(coro)


@app.command()
def migrate() -> None:
    """Apply every pending SQL migration."""

    async def _go():
        from .db import pool
        from .db.migrate import apply_migrations

        pg = await pool.get_pool()
        async with pg.acquire() as conn:
            applied = await apply_migrations(conn)
        await pool.close_pool()
        typer.echo(f"applied: {', '.join(applied) if applied else '(nothing pending)'}")

    _run(_go())


@app.command()
def doctor() -> None:
    """Verify that configuration, the database and the embedder actually agree.

    The check that earns its keep is the last one: an embedding dimension that
    disagrees with the vector column is invisible until the first insert after a
    full, paid-for indexing run.
    """

    async def _go():
        from .db import pool
        from .db.repositories import chunks as chunk_repo
        from .llm.embeddings import get_embedder
        from .queue.dispatch import close_redis, get_redis

        s = get_settings()
        problems: list[str] = []
        typer.echo(f"env         : {s.app_env}")

        try:
            version = await pool.fetchval("SELECT version()")
            typer.echo(f"postgres    : ok — {str(version).split(',')[0]}")
        except Exception as exc:
            problems.append(f"postgres unreachable: {exc}")
            typer.echo(f"postgres    : FAIL — {exc}")
            _report(problems)
            return

        rows = await pool.fetch(
            "SELECT extname, extversion FROM pg_extension "
            "WHERE extname IN ('vector','vectorscale','timescaledb','pgcrypto')"
        )
        present = {r["extname"]: r["extversion"] for r in rows}
        for ext in ("pgcrypto", "vector", "timescaledb"):
            if ext in present:
                typer.echo(f"ext {ext:<11}: ok {present[ext]}")
            else:
                problems.append(f"required extension missing: {ext}")
                typer.echo(f"ext {ext:<11}: MISSING")
        typer.echo(
            "ext vectorscale: "
            + (
                f"ok {present['vectorscale']}"
                if "vectorscale" in present
                else "absent (HNSW fallback in use)"
            )
        )

        try:
            hyper = await pool.fetchval(
                "SELECT count(*) FROM timescaledb_information.hypertables "
                "WHERE hypertable_name = 'agent_events'"
            )
            typer.echo(f"hypertable  : {'ok' if hyper else 'MISSING'}")
            if not hyper:
                problems.append("agent_events is not a hypertable")
        except Exception as exc:
            typer.echo(f"hypertable  : unknown — {exc}")

        # Append-only, proved rather than asserted. A row-level BEFORE UPDATE
        # trigger only fires when a row is actually touched, so the probe has to
        # insert one first — inside a transaction that is always rolled back, so
        # the check leaves nothing behind in the audit trail.
        pg = await pool.get_pool()
        for op, statement in (
            ("UPDATE", "UPDATE agent_events SET name = 'x' WHERE name = 'doctor.probe'"),
            ("DELETE", "DELETE FROM agent_events WHERE name = 'doctor.probe'"),
            ("TRUNCATE", "TRUNCATE agent_events"),
        ):
            rejected = False
            try:
                async with pg.acquire() as conn, conn.transaction():
                    await conn.execute(
                        "INSERT INTO agent_events (kind, name, status) "
                        "VALUES ('decision', 'doctor.probe', 'ok')"
                    )
                    await conn.execute(statement)
                    raise _Rollback()
            except _Rollback:
                pass
            except Exception as exc:
                rejected = "append-only" in str(exc)
                if not rejected:
                    problems.append(f"append-only probe for {op} failed unexpectedly: {exc}")
            if rejected:
                typer.echo(f"append-only : ok — {op} rejected by the database")
            else:
                typer.echo(f"append-only : FAIL — {op} was accepted")
                problems.append(f"agent_events accepts {op}; the immutability trigger is missing")

        embedder = get_embedder()
        column_dim = await chunk_repo.embedding_column_dim()
        typer.echo(f"embedder    : {embedder.name} dim={embedder.dim}")
        typer.echo(f"vector col  : vector({column_dim})")
        if column_dim and column_dim != embedder.dim:
            problems.append(
                f"EMBEDDING_DIM={embedder.dim} but code_chunks.embedding is vector({column_dim}). "
                "Every insert will fail. Fix the env var or re-run a migration that widens the column."
            )
            typer.echo("dimension   : MISMATCH")
        else:
            typer.echo("dimension   : ok")

        try:
            await (await get_redis()).ping()
            typer.echo("redis       : ok")
        except Exception as exc:
            problems.append(f"redis unreachable: {exc}")
            typer.echo(f"redis       : FAIL — {exc}")

        typer.echo(f"llm         : {s.llm_provider} (security={s.model_security}, docs={s.model_docs})")
        if s.llm_provider == "anthropic" and not s.anthropic_api_key:
            typer.echo("              no ANTHROPIC_API_KEY set; the SDK will look for a profile")
        if s.github_webhook_secret == "change-me":  # noqa: S105 - placeholder sentinel
            problems.append("GITHUB_WEBHOOK_SECRET is still the placeholder")
            typer.echo("webhook     : PLACEHOLDER SECRET")
        if not s.github_token:
            typer.echo("github      : no token — reviews can be computed but not posted")

        await close_redis()
        await pool.close_pool()
        _report(problems)

    _run(_go())


def _report(problems: list[str]) -> None:
    typer.echo("")
    if problems:
        typer.secho(f"{len(problems)} problem(s):", fg=typer.colors.RED, bold=True)
        for p in problems:
            typer.secho(f"  - {p}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    typer.secho("all checks passed", fg=typer.colors.GREEN, bold=True)


@app.command()
def index(
    repo: str = typer.Argument(..., help="owner/name"),
    path: Path = typer.Argument(..., help="local checkout to index"),
    commit: str = typer.Option("HEAD", help="commit sha to record"),
    github_id: int = typer.Option(0, help="GitHub repo id; 0 derives a stable local id"),
) -> None:
    """Build the semantic index for a repository from a local checkout."""

    resolved = path.resolve()

    async def _go():
        from .db import pool
        from .db.repositories import reviews as review_repo
        from .retrieval.indexer import index_repository

        rid = github_id or (abs(hash(repo)) % (10**9))
        repo_id = await review_repo.upsert_repository(repo, rid)
        stats = await index_repository(repo_id, resolved, commit)
        await pool.close_pool()
        typer.echo(json.dumps(stats, indent=2))

    _run(_go())


@app.command()
def replay(
    repo: str = typer.Argument(..., help="owner/name"),
    number: int = typer.Argument(..., help="pull request number"),
    engine: str = typer.Option("", help="'local' to bypass LangGraph"),
    post: bool = typer.Option(
        False,
        "--post/--no-post",
        help=(
            "actually write the review to GitHub. Off by default: a replay is for "
            "looking at output, and should not be one flag away from commenting "
            "on someone's work."
        ),
    ),
) -> None:
    """Review a real pull request now, without waiting for a webhook."""

    async def _go():
        from .db import pool
        from .domain.models import WebhookJob
        from .forge.github import GitHubClient
        from .orchestration.pipeline import review_pull_request

        async with GitHubClient() as gh:
            pr = await gh.get_pull_request(repo, number)
            repo_meta = pr["base"]["repo"]

        job = WebhookJob(
            delivery_id=f"replay-{uuid.uuid4()}",
            event="pull_request",
            action="synchronize",
            repo_full_name=repo,
            repo_github_id=int(repo_meta["id"]),
            is_private=bool(repo_meta.get("private", True)),
            pr_number=number,
            head_sha=pr["head"]["sha"],
            base_sha=pr["base"]["sha"],
            title=pr.get("title") or "",
            author=(pr.get("user") or {}).get("login") or "",
        )
        outcome = await review_pull_request(job, engine_name=engine or None, post=post)
        await pool.close_pool()

        typer.echo(
            json.dumps(
                {
                    "review_id": str(outcome.review_id),
                    "posted_to_github": post and str(outcome.decision) == "auto_post",
                    "decision": str(outcome.decision),
                    "reason": str(outcome.reason) if outcome.reason else None,
                    "confidence": outcome.overall_confidence,
                    "cost_usd": round(outcome.total_cost_usd, 4),
                    "agents": {
                        str(v.agent): {
                            "status": str(v.status),
                            "findings": len(v.findings),
                            "model": v.model,
                            "ms": v.duration_ms,
                        }
                        for v in outcome.verdicts
                    },
                    "findings": [
                        {
                            "severity": str(f.severity),
                            "category": str(f.category),
                            "confidence": round(f.confidence, 3),
                            "where": f"{f.file_path}:{f.line_start}",
                            "title": f.title,
                            "raised_by": f.agreeing or [str(f.agent)],
                        }
                        for f in outcome.findings
                    ],
                },
                indent=2,
            )
        )

    _run(_go())


@app.command()
def trace(review_id: str) -> None:
    """Reconstruct one review from the event spine."""

    async def _go():
        from .db import pool
        from .db.repositories import metrics

        rows = await metrics.trace(uuid.UUID(review_id))
        for r in rows:
            cost = f" ${float(r['cost_usd']):.4f}" if r["cost_usd"] else ""
            dur = f" {r['duration_ms']}ms" if r["duration_ms"] else ""
            typer.echo(
                f"{r['ts']:%H:%M:%S.%f} {r['kind']:<11} {r['name']:<24} "
                f"{r['agent'] or '':<12}{dur}{cost} {r['status'] or ''}"
            )
        await pool.close_pool()

    _run(_go())


@app.command()
def costs(hours: int = typer.Option(24)) -> None:
    """Spend by agent and model, read from the continuous aggregate."""

    async def _go():
        from .db import pool
        from .db.repositories import metrics

        typer.echo(
            json.dumps(
                {
                    "spend_today_usd": round(await metrics.spend_today_usd(), 4),
                    "by_agent": [
                        {
                            k: (float(v) if hasattr(v, "__float__") and not isinstance(v, (int, str)) else v)
                            for k, v in row.items()
                        }
                        for row in await metrics.cost_by_agent(hours)
                    ],
                    "reviews": await metrics.escalation_rate(hours),
                },
                indent=2,
                default=str,
            )
        )
        await pool.close_pool()

    _run(_go())


@app.command()
def queue(limit: int = typer.Option(20)) -> None:
    """The human queue, most urgent first."""

    async def _go():
        from .db import pool
        from .db.repositories import metrics

        for item in await metrics.open_hitl(limit):
            typer.echo(
                f"[p{item['priority']:>3}] {item['reason']:<18} "
                f"{item['full_name']}#{item['pr_number']}  {item['summary']}"
            )
        await pool.close_pool()

    _run(_go())


@app.command()
def eval(
    only: list[str] = typer.Option(None, "--only", help="fixture id; repeatable"),
    provider: str = typer.Option("", help="override LLM_PROVIDER for this run"),
    engine: str = typer.Option("", help="'local' to bypass LangGraph"),
    concurrency: int = typer.Option(3, help="cases in flight at once"),
    repeat: int = typer.Option(
        1,
        help=(
            "run the set N times and report mean and spread. On a set this small "
            "a single run cannot separate a real change from model variance."
        ),
    ),
    budget: float = typer.Option(0.0, help="per-case cost cap in USD; 0 disables"),
    save: Path = typer.Option(None, help="write the JSON report here"),
    baseline: Path = typer.Option(None, help="compare against a saved report and fail on regression"),
    verbose: bool = typer.Option(False, help="also list unlabelled findings"),
    as_json: bool = typer.Option(False, "--json", help="print JSON instead of the report"),
) -> None:
    """Score the panel against the golden set.

    With the default `echo` provider this costs nothing and proves the harness.
    Against a real provider it measures the thing that actually matters — whether
    a stated confidence of 0.8 turns out right about 80% of the time.
    """
    import os

    if provider:
        os.environ["LLM_PROVIDER"] = provider
        get_settings.cache_clear()
        from .llm.client import set_provider

        set_provider(None)
    if budget:
        os.environ["REVIEW_COST_CAP_USD"] = str(budget)
        get_settings.cache_clear()

    async def _go():
        from .evaluation.report import compare, render, stability, unlabelled_union
        from .evaluation.runner import run_eval

        reports = await run_eval(
            only=list(only) if only else None,
            engine_name=engine or None,
            concurrency=concurrency,
            budget_cap_usd=budget or None,
            repeat=repeat,
        )
        report = reports[-1]
        payload = report.as_dict()
        if len(reports) > 1:
            payload["stability"] = {
                "runs": len(reports),
                "precision_strict": [round(r.overall.precision_strict, 3) for r in reports],
                "recall": [round(r.overall.recall, 3) for r in reports],
                "calibration_error": [round(r.ece, 3) for r in reports],
                "cost_per_case_usd": [round(r.cost_per_case_usd, 4) for r in reports],
            }
            # Every run in full, not just the last. Comparing two configurations
            # means comparing distributions, and a summary of three numbers is not
            # enough to see which findings moved.
            payload["runs"] = [r.as_dict() for r in reports]

        if as_json:
            typer.echo(json.dumps(payload, indent=2))
        else:
            typer.echo(render(report, verbose=verbose))
            if verbose:
                # render() shows the last run only; across a repeat that hides
                # unlabelled findings from every earlier run.
                typer.echo(unlabelled_union(reports))
            typer.echo(stability(reports))

        if save:
            save.parent.mkdir(parents=True, exist_ok=True)
            save.write_text(json.dumps(payload, indent=2) + "\n")  # noqa: ASYNC240
            typer.echo(f"saved: {save}")

        if baseline:
            rendered, regressed = compare(report, json.loads(baseline.read_text()))  # noqa: ASYNC240
            typer.echo(rendered)
            if regressed:
                typer.secho("REGRESSION against baseline", fg=typer.colors.RED, bold=True)
                raise typer.Exit(code=1)

    _run(_go())


if __name__ == "__main__":
    app()
