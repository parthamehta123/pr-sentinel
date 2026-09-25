# pr-sentinel — Project Context for AI Coding Agents

This file is the single source of truth for any AI coding agent working on this
codebase. It is symlinked/copied to agent-specific locations (CLAUDE.md,
.cursorrules, AGENTS.md, etc.) so every tool reads the same instructions.

## What this project is

A selective, evidence-grounded multi-agent pull request reviewer with a
human-in-the-loop gate. It reclaims senior engineer attention by automating the
mechanical part of code review, surfacing only findings worth a human's time.

**Thesis:** selectivity, not coverage. The system stays quiet and only wakes a
human when it matters.

## Architecture (30-second version)

```
GitHub webhook → FastAPI ingress (HMAC + idempotency)
  → Redis/ARQ job queue (fast ack to GitHub)
  → WorkflowEngine (LangGraph | asyncio | Temporal)
    → 4 specialist agents in parallel:
        security (Opus) | correctness (Opus) | tests (Sonnet) | docs (Sonnet)
    → each grounded by retrieval (pgvector hybrid search)
  → Aggregator (merge, dedup, noisy-OR confidence)
  → Confidence gate:
      ≥0.70 → auto-post to GitHub
      <0.70 → HITL approval queue
      critical security → always escalate (never disclose in PR)
  → Event spine (TimescaleDB hypertable, append-only)
```

## Key invariants (non-negotiable)

1. Every webhook payload passes HMAC-SHA256 signature verification before processing
2. Every delivery is deduplicated via its delivery_id (idempotency key)
3. Every specialist finding carries confidence and rationale
4. The agent_events table is append-only (triggers reject UPDATE, DELETE, TRUNCATE)
5. Critical security findings are never posted to public PR threads
6. Labels derived from model output are ALLOW, not EXPECT (prevents circular recall)
7. The orchestration engine is swappable — code depends on the WorkflowEngine ABC only

## Repository layout

```
src/pr_sentinel/
├── ingress/          # FastAPI webhook receiver
├── queue/            # Redis/ARQ job queue + worker
├── orchestration/    # WorkflowEngine ABC + 3 implementations
├── agents/           # 4 specialist agents + runner
├── retrieval/        # Hybrid vector+FTS search, context builder
├── llm/              # Provider abstraction, prompt registry, pricing
├── gate/             # Confidence gate + HITL routing
├── aggregation/      # Finding merger, dedup, noisy-OR
├── forge/            # GitHub API client (post reviews)
├── feedback/         # Learning loop (preference extraction, decay)
├── events/           # Event spine (observability)
├── reliability/      # Budget guard, circuit breaker, retry
├── evaluation/       # Eval harness, metrics, report
├── dashboard/        # Server-rendered HITL queue, trace viewer, cost dashboard
├── db/               # Pool, migrations, repositories
├── domain/           # Enums, models, finding schema
├── config.py         # Typed settings (pydantic-settings)
└── cli.py            # CLI entrypoint (typer)

tests/
├── unit/             # 247 offline tests
├── integration/      # 13 tests against live Postgres/Redis
└── eval/             # 65 labelled cases, golden fixtures, baselines

migrations/           # 5 SQL migrations (core, semantic, timeseries, caggs, feedback)
docs/                 # ARCHITECTURE.md, ROADMAP.md, RUNBOOK.md, 8 ADRs
```

## Commands

```bash
make install          # Create venv + install package with dev extras
make up               # Start Postgres + Redis (Docker Compose)
make migrate          # Apply SQL migrations
make doctor           # Verify config, extensions, schema, embedding dim
make test             # Run unit tests (offline, ~4s)
make lint             # ruff check + ruff format --check + mypy
make demo             # Run the full pipeline offline with echo provider (~2s)
make eval             # Score the panel offline (echo provider)
make eval-live        # Score against real models (needs ANTHROPIC_API_KEY, ~$5)
make api              # Start webhook ingress (port 8080)
make worker           # Start ARQ review worker
make dashboard        # Start dashboard (port 8081)
```

## Coding conventions

- Python 3.12+, type hints everywhere, `from __future__ import annotations`
- ruff for linting and formatting (line length 110)
- mypy strict on `src/`, ignore missing imports
- pytest with asyncio_mode=auto
- Structured logging via structlog
- No ORMs — raw SQL via asyncpg
- Every new feature needs a test. Every bug fix needs a regression test.
- Prompts are versioned Markdown files in `src/pr_sentinel/llm/prompts/`
- ADRs in `docs/adr/` — each has a "what would make this wrong" section

## Eval rules

- Labels from model output are ALLOW (permitted), not EXPECT (required)
- EXPECT labels come only from independent evidence (upstream fixes, advisories)
- 16 held-out cases have hashed manifests — editing them requires `--write` and a commit explaining why
- `build_eval_fixtures.py` rejects unknown categories at build time
- Full-panel recall counts distinct labels, not duplicate matches

## What NOT to do

- Don't add EXPECT labels based on what the model found (circular recall)
- Don't override the holdout manifest to silence a test
- Don't hardcode credentials (even fabricated ones that match vendor patterns)
- Don't use `len(c.hits)` for recall — use distinct label deduplication
- Don't report agent outages as reviewer misses — separate availability from recall
