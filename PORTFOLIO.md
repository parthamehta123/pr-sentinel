# pr-sentinel — Complete Project Portfolio

> Last updated: 2026-09-26 (production state)

---

## Part 1: Origin — Ayush Singh's Resources

### YouTube Video

**Title**: Designing & Building PR Review Multi Agent System (3 Hours Build)
**Creator**: Ayush Singh (@AyushSinghSh, 145k subscribers)
**Link**: https://www.youtube.com/watch?v=RiN02OXjeeQ
**Views**: ~69,000 | Published: 22 Jul 2026

### Step-by-step content

**Step 1 — Problem Reframing (0:00–10:00)**
- Rejects the naive approach (diff → LLM → comments). 400 tutorials doing that already, none production-grade.
- Reframes: "reclaim senior engineer attention by automating the mechanical part, so humans only spend judgment where genuinely required."
- Introduces selectivity as the core design principle.

**Step 2 — Map the Mess (10:00–15:00)**
- Observe what a senior engineer actually does: notification → context-switch → read diff → pull branch → leave comments → developer responds → repeat.
- Waste: waiting (pure cost), context switching, fatigue (10th review ≠ 1st review quality), inconsistency across reviewers.

**Step 3 — Trigger and Output (15:00–22:00)**
- Trigger: GitHub webhook when PR is opened (precise, not vague).
- Output: Structured review with each finding carrying: agent_type, severity + category, file:line, confidence, rationale with evidence.

**Step 4 — Autonomy Levels (22:00–31:00)**
- Golden rule: if AI deals with financials, legals, or health, you cannot choose full autonomy.
- Consequence of error analysis: wrong style comment = annoying; missing SQL injection = dangerous.
- Reversibility: an auto-posted review can be disputed; a merged migration cannot be unrun.
- System maturity: start with more human involvement, reduce as system proves itself.

**Step 5 — Failure Mode Analysis (31:00–45:00)**
- For every component, ask "what can go wrong" from engineering and LLM directions.
- Defenses: citation requirement + confidence + HITL gate (hallucination), monitoring + prompt updates (model drift), retry + circuit breakers (API timeout), timeouts on every node (deadlock), minimum evidence + decay (feedback poisoning), queue prioritization (human bottleneck), flag low confidence + audits (almost-right), idempotency dedup (duplicate posting).

**Step 6 — First Principles Architecture (45:00–58:00)**
- Senior engineers do 4 things a naive LLM reviewer doesn't:
  - a. Bring codebase context → needs retrieval (RAG)
  - b. Reason across separate concerns → needs multi-agent
  - c. Stay skeptical, cite evidence → needs confidence + rationale on every finding
  - d. Know the repo → needs three types of memory
- Four specialist agents: Security, Correctness, Testing, Docs.
- Design pattern: fan-out/fan-in — four agents in parallel, aggregator merges results.

**Step 7 — Three Memory Types (58:00–1:10:00)**
- Semantic memory — the codebase itself (vector embeddings + similarity search).
- Episodic memory — past reviews, what was flagged/disputed (timestamped relational rows).
- Procedural memory — how the team likes things done (conventions, rules).

**Step 8 — Observability & Trust (1:00:00–1:10:00)**
- Every action recorded as a time-ordered event (event spine).
- Powers: trace viewer, audit trail, economics.
- HITL gate: confidence ≥ threshold → auto-post; < threshold → escalate; critical security → always escalate.

**Step 9 — Database Architecture (1:10:00–1:28:00)**
- Three data shapes: memory (vectors), truth (relational), time (observability).
- Solution: TigerData (managed Postgres) with pgvector, pgvectorscale + DiskANN, TimescaleDB hypertables, continuous aggregates.
- Redis + ARQ for job queue (fast ack to GitHub).

**Step 10 — Full Architecture Assembly (1:28:00–1:52:00)**
- GitHub PR → FastAPI ingress (HMAC + idempotency) → Redis/ARQ → LangGraph fan-out → 4 agents → aggregator → confidence gate → auto-post or HITL queue.
- Framework-agnostic via WorkflowEngine ABC.
- Model routing + prompt registry.

**Step 11 — Genesis Kit & Implementation (1:52:00–3:10:00)**
- Genesis Kit: done.html, implementation_notes.html, context_graph, plan.md, loops.md.
- Built M1 (webhook) and M2 (TigerData) live; told viewers to complete M3-M9.

### Blog
**Link**: https://www.antern.co/blogs/genesis-kit/
1. "The Loop That Prompts Itself" — how Genesis maintains state across AI coding sessions.
2. "Structural Reasoning is the Missing Piece of Agentic AI" — why agents need invariants, dependency graphs, and verification gates.

### GitHub Repository
**Link**: https://github.com/ayush488-glitch/genesis-kit
- Open-source framework for working with any AI coding agent.
- Key components: kickoff prompt, done.html, loops.md, AgenticSW Kit, cognitive skills (Detective, Verify, Blueprint, Scout, Council/Mirror, Ghost, Foresight).
- Token budget management and model routing built in.

---

## Part 2: pr-sentinel — Complete Architecture (Production State, 2026-09-26)

**Repository**: https://github.com/parthamehta123/pr-sentinel

### Design Thesis

The system optimizes for **selectivity** — surfacing findings worth a senior engineer's attention and deferring the rest. Not coverage, not maximal output. Every architectural decision traces back to this.

### Architecture Diagram

```
GitHub PR opened
    │
    ▼
┌─────────────────────────────────────────────────┐
│  FastAPI Ingress (webhook receiver)              │
│  ├── HMAC-SHA256 signature verification          │
│  ├── Idempotency key dedup (delivery_id)         │
│  └── Returns 202 immediately to GitHub           │
│  Prod: webhook-production-1c8c.up.railway.app    │
└──────────────┬──────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────┐
│  Redis + ARQ Job Queue                           │
│  Prod: Railway managed Redis (redis:8.2)         │
│  Local: redis:7-alpine on :6381                  │
└──────────────┬──────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────┐
│  WorkflowEngine (abstract interface)             │
│  ├── LangGraphEngine (default)                   │
│  ├── LocalEngine (plain asyncio fallback)        │
│  └── TemporalEngine (enterprise option)          │
│                                                   │
│  Fan-out: 4 specialist agents in parallel         │
│  ┌──────────┐ ┌──────────────┐ ┌───────┐ ┌────┐ │
│  │ Security │ │ Correctness  │ │ Tests │ │Docs│ │
│  │ (Opus 5) │ │ (Opus 5)     │ │(Son.5)│ │(S5)│ │
│  └────┬─────┘ └──────┬───────┘ └───┬───┘ └─┬──┘ │
│       └──────────────┴─────────────┴────────┘    │
│                      │                            │
│  Each agent grounded by retrieval:                │
│  ┌───────────────────────────────────────┐       │
│  │  Context Builder                       │       │
│  │  ├── Embed diff (OpenAI text-emb-3-s)  │       │
│  │  ├── Vector search (pgvector)          │       │
│  │  ├── Full-text search (tsvector)       │       │
│  │  ├── Merge + rank by RRF              │       │
│  │  └── Budget: 24,000 chars (~15 chunks) │       │
│  └───────────────────────────────────────┘       │
│                      │                            │
│                      ▼                            │
│  ┌───────────────────────────────────────┐       │
│  │  Aggregator                            │       │
│  │  ├── Merge duplicate findings          │       │
│  │  ├── Noisy-OR confidence (cross-agent) │       │
│  │  └── Compute overall confidence        │       │
│  └───────────────┬───────────────────────┘       │
└──────────────────┼──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│  Confidence Gate                                 │
│  ├── conf ≥ 0.70 → auto-post to GitHub PR       │
│  ├── conf < 0.70 → route to HITL approval queue  │
│  ├── critical security → always escalate          │
│  └── severity < minor → suppress                  │
└──────────────┬───────────────┬──────────────────┘
               │               │
         ┌─────┘               └──────┐
         ▼                            ▼
┌─────────────────┐      ┌─────────────────────┐
│ GitHub PR Review │      │ HITL Approval Queue  │
│ (auto-posted)    │      │ dashboard-production │
│                  │      │ -6fe7.up.railway.app │
└─────────────────┘      └──────────┬────────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ Feedback Learning    │
                         │ ├── Record disputes  │
                         │ ├── Extract prefs    │
                         │ │   (min 3 disputes) │
                         │ ├── Decay old (90d)  │
                         │ └── Inject into      │
                         │     agent prompts    │
                         └─────────────────────┘
```

### Data Layer (single Postgres spine)

```
TigerData Cloud — PostgreSQL 18.6
Service: sx5baxyev7 · Region: us-east-1
Addons: time-series, ai
pgvector 0.8.6, TimescaleDB 2.30.1, pgvectorscale 0.9.0, pgcrypto 1.4

Semantic Memory:
  code_chunks (embedding vector(1536))
  Embedder: OpenAI text-embedding-3-small

Truth (relational):
  12 tables: deliveries, reviews, findings, hitl_items, feedback,
  pull_requests, repositories, agent_verdicts, code_chunks,
  conventions, schema_migrations

Time (observability):
  agent_events HYPERTABLE (append-only, triggers reject UPDATE/DELETE/TRUNCATE)
  agent_costs_hourly CONTINUOUS AGGREGATE (real-time)
```

### Reliability Layer

| Mechanism | What it does |
|-----------|-------------|
| HMAC verification | Rejects forged webhooks before any processing |
| Idempotency dedup | Prevents duplicate reviews from GitHub retries |
| Budget guard | Per-review ($1.50) + daily ($25) caps via continuous aggregates |
| Circuit breaker | Opens after N consecutive failures; graceful degradation |
| Agent timeouts | 240s per agent; aggregator doesn't wait forever |
| Retry with backoff | LLM calls retry with exponential backoff |
| Append-only audit | Database triggers reject UPDATE/DELETE/TRUNCATE on agent_events |

### Orchestration (framework-agnostic)

```python
class WorkflowEngine(ABC):
    @abstractmethod
    async def run_panel(self, ctx, agents, spine, budget) -> list[AgentVerdict]: ...

class LangGraphEngine(WorkflowEngine): ...   # Default — fan-out via Send API
class LocalEngine(WorkflowEngine): ...       # Plain asyncio.gather fallback
class TemporalEngine(WorkflowEngine): ...    # Enterprise — durable workflows
```

A test runs the same panel through all engines and asserts identical verdicts.

### Model Routing

| Agent | Model | Why |
|-------|-------|-----|
| Security | claude-opus-5 | Highest stakes — missing an injection is dangerous |
| Correctness | claude-opus-5 | Logic bugs need deep reasoning |
| Tests | claude-sonnet-5 | Pattern recognition sufficient |
| Docs | claude-sonnet-5 | Was Haiku (3/10); Sonnet gets 10/10 for 8% more cost |

### Prompt Registry

- 5 versioned Markdown files: `_base.md`, `security.md`, `correctness.md`, `tests.md`, `docs.md`
- Each has `<!-- version: 2026-09-22.1 -->` header
- Base prompt prepended to all specialist prompts
- Bundle hash stamped on every review for traceability

### Eval Results (live Anthropic run, 2026-09-26)

| Metric | Value |
|--------|-------|
| Precision (strict) | 94.9% |
| Precision (lenient) | 98.7% |
| Recall | **100%** (0 misses) |
| F1 | 0.974 |
| Calibration error | 0.095 |
| Gate decision match | 100% |
| Cost per case | $0.08 |
| False positives | 1 (routine version bump) |

#### By Agent

| Agent | Hits | Miss | FP | Precision | Recall |
|-------|------|------|----|-----------|--------|
| security | 17 | 0 | 0 | 1.00 | 1.00 |
| correctness | 48 | 0 | 1 | 0.92 | 1.00 |
| tests | 5 | 0 | 0 | 1.00 | 1.00 |
| docs | 4 | 0 | 0 | 1.00 | 1.00 |

### Dashboard

| Page | URL | What it shows |
|------|-----|---------------|
| HITL Queue | `/` | Items awaiting human decision, spend today, escalation rate |
| Review Detail | `/review/<id>` | All findings with severity, agent, confidence, rationale |
| Trace Viewer | `/trace/<id>` | Full event timeline — every span, LLM call, cost |
| Cost Dashboard | `/costs` | Daily spend (30d), hourly by agent (7d), budget bar |
| Feedback | `/feedback` | Recent disputes/approvals, learning loop |
| Health | `/health` | Health check for load balancers |

### Production Deployment

| Component | Where | URL/Details |
|-----------|-------|-------------|
| Webhook | Railway | https://webhook-production-1c8c.up.railway.app |
| Worker | Railway | Internal (no public URL) |
| Dashboard | Railway | https://dashboard-production-6fe7.up.railway.app |
| Redis | Railway | Managed redis:8.2 (internal) |
| Postgres | TigerData | PG 18.6, service sx5baxyev7, us-east-1 |
| GitHub webhook | GitHub | Hook 686048522 → Railway webhook URL |
| Railway project | Railway | https://railway.com/project/9ea4f4f1-04a7-4fe8-aa79-de246448bc78 |
| TigerData console | TigerData | https://console.cloud.tigerdata.com/dashboard/services/sx5baxyev7 |

### External Services

| Service | What for | Status |
|---------|----------|--------|
| Anthropic | LLM (4 specialist agents) | Connected |
| GitHub | Webhook source + post reviews | Connected |
| TigerData | Managed PG 18.6 (pgvector + TimescaleDB) | Connected |
| OpenAI | Embeddings (text-embedding-3-small, 1536d) | Connected |
| Railway | Container deployment (3 services + Redis) | Live |

### Agent Tooling (agnostic — Ralph Loop equivalent)

**CONTEXT.md** — single source of truth, symlinked to four agent-specific locations:

| Agent | File it reads |
|-------|--------------|
| Claude Code | `CLAUDE.md` → `CONTEXT.md` (symlink) |
| Cursor | `.cursorrules` → `CONTEXT.md` (symlink) |
| Codex | `AGENTS.md` → `CONTEXT.md` (symlink) |
| GitHub Copilot | `.github/copilot-instructions.md` → `CONTEXT.md` (copy) |

One file to maintain, four agents read it.

**task-loop.sh** — agent-agnostic iterative task runner (the Ralph Loop):

```bash
./scripts/task-loop.sh --agent claude --max-iterations 10   # Claude Code
./scripts/task-loop.sh --agent cursor                        # Cursor
./scripts/task-loop.sh --agent codex                         # Codex
./scripts/task-loop.sh                                       # Interactive
```

The loop: read PRD → read progress → do one task → run tests → update progress → repeat until ALL_TASKS_COMPLETE or max iterations hit.

**PRD.md** — completion criteria across 4 phases with architecture requirements and eval minimums.

**progress.md** — tracks state across sessions. Persists across context window resets.

### What pr-sentinel builds beyond the video

| Feature | Video | pr-sentinel |
|---------|-------|-------------|
| Specialist agents | Described | Built + measured (100% recall) |
| Orchestration engines | LangGraph only | LangGraph + asyncio + Temporal, all live |
| Eval harness | Mentioned, not built | 65 cases, live baseline, 6 measurement bugs found |
| Feedback learning | Mentioned | Built — extraction, decay, injection |
| Dashboard | Mentioned | Built — queue, trace, costs, feedback, health |
| Deployment | Railway mentioned | Live on Railway (3 services + Redis) |
| TigerData cloud | Used in video | Connected (PG 18.6, all 5 migrations applied) |
| OpenAI embeddings | Mentioned | Live (text-embedding-3-small, 1536d) |
| Self-review | Not done | PR #3 reviewed by its own pipeline |
| Agent tooling | Genesis Kit (Claude-specific) | Agnostic: CONTEXT.md → 4 agents, task-loop.sh |

### Repository Layout

```
pr-sentinel/
├── CONTEXT.md                    # Agnostic project context (single source of truth)
├── CLAUDE.md → CONTEXT.md        # Claude Code reads this
├── .cursorrules → CONTEXT.md     # Cursor reads this
├── AGENTS.md → CONTEXT.md        # Codex reads this
├── .github/
│   ├── copilot-instructions.md → CONTEXT.md  # Copilot reads this
│   └── workflows/ci.yml         # CI: lint + unit tests
├── PRD.md                        # Product requirements + completion criteria
├── progress.md                   # Task progress tracker
├── PORTFOLIO.md                  # This file
├── Dockerfile                    # Multi-stage, entrypoint.sh routes via SERVICE_TYPE
├── entrypoint.sh                 # webhook | worker | dashboard routing
├── Procfile                      # webhook + worker + dashboard
├── railway.toml                  # Railway deployment (3 services)
├── docker-compose.yml            # Local dev (Postgres + Redis)
├── Makefile                      # All commands
├── pyproject.toml                # Package config + dependencies
├── src/pr_sentinel/
│   ├── ingress/                  # FastAPI webhook receiver
│   ├── queue/                    # Redis/ARQ job queue + worker
│   ├── orchestration/
│   │   ├── engine.py             # WorkflowEngine ABC
│   │   ├── langgraph_engine.py   # LangGraph implementation
│   │   ├── local_engine.py       # Plain asyncio implementation
│   │   └── temporal_engine.py    # Temporal implementation
│   ├── agents/                   # 4 specialist agents + runner
│   ├── retrieval/                # Hybrid vector+FTS search, context builder
│   ├── llm/
│   │   ├── client.py             # Provider abstraction (Anthropic, echo)
│   │   ├── registry.py           # Prompt registry with versioning
│   │   ├── pricing.py            # Model cost calculation
│   │   ├── embeddings.py         # Hash/OpenAI/Local embedding providers
│   │   └── prompts/              # Versioned .md files per agent
│   ├── gate/                     # Confidence gate + HITL routing
│   ├── aggregation/              # Finding merger, dedup, noisy-OR
│   ├── forge/                    # GitHub API client (post reviews)
│   ├── feedback/
│   │   └── learning.py           # Preference extraction, decay, injection
│   ├── events/                   # Event spine (observability)
│   ├── reliability/              # Budget guard, circuit breaker, retry
│   ├── evaluation/               # Eval harness, metrics, report
│   ├── dashboard/
│   │   ├── app.py                # FastAPI: queue, trace, costs, feedback, health
│   │   └── templates/            # Server-rendered HTML
│   ├── db/                       # Pool, migrations, repositories
│   ├── domain/                   # Enums, models, finding schema
│   ├── config.py                 # Typed settings (pydantic-settings)
│   └── cli.py                    # CLI entrypoint (typer)
├── tests/
│   ├── unit/                     # 247 offline tests
│   ├── integration/              # 13 tests against live Postgres/Redis
│   └── eval/                     # 65 labelled cases, golden fixtures, baselines
├── migrations/                   # 5 SQL migrations
├── scripts/                      # task-loop.sh, demo, eval fixtures, mining scripts
└── docs/
    ├── ARCHITECTURE.md           # Derives each component from its problem
    ├── ROADMAP.md                # What's deliberately absent and why
    ├── RUNBOOK.md                # Diagnostic SQL + operations
    └── adr/                      # 8 Architecture Decision Records
```

---

## Part 3: Complete Codebase Exploration Order (90 files, 13 layers)

### Layer 0: Meta-files (read first — they frame everything)

| # | File | What it tells you |
|---|------|-------------------|
| 1 | `CONTEXT.md` | Project thesis, 7 invariants, coding conventions, eval rules, "what NOT to do" |
| 2 | `PRD.md` | Completion criteria across 4 phases, architecture requirements, eval minimums |
| 3 | `progress.md` | What's done, known gaps, measurement bugs found — the Ralph Loop state file |
| 4 | `README.md` | Project overview and quick start |

### Layer 1: Architecture docs (understand why before how)

| # | File | What it tells you |
|---|------|-------------------|
| 5 | `docs/ARCHITECTURE.md` | Derives each component from its problem |
| 6 | `docs/ROADMAP.md` | What's deliberately absent and why |
| 7 | `docs/RUNBOOK.md` | Diagnostic SQL queries + operational procedures |
| 8 | `docs/adr/README.md` | ADR index |
| 9 | `docs/adr/0001-queue-between-ingress-and-review.md` | Why Redis/ARQ sits between webhook and agents |
| 10 | `docs/adr/0002-one-postgres-for-three-data-shapes.md` | Why one Postgres instead of three databases |
| 11 | `docs/adr/0003-raw-sql-over-an-orm.md` | Why asyncpg + raw SQL, not SQLAlchemy |
| 12 | `docs/adr/0004-orchestration-behind-an-interface.md` | Why WorkflowEngine ABC with 3 implementations |
| 13 | `docs/adr/0005-per-agent-model-routing.md` | Why Opus for security, Sonnet for docs |
| 14 | `docs/adr/0006-confidence-gate-and-security-escalation.md` | Why critical security never auto-posts |
| 15 | `docs/adr/0007-feedback-requires-minimum-evidence.md` | Why 3 disputes minimum before learning |
| 16 | `docs/adr/0008-genesis-kit-is-not-a-dependency.md` | Why pr-sentinel isn't locked to Genesis Kit |

### Layer 2: Agent tooling (the Ralph Loop)

| # | File | What it tells you |
|---|------|-------------------|
| 17 | `scripts/task-loop.sh` | The agnostic iterative task runner (Ralph Loop equivalent) |
| 18 | `CLAUDE.md` → symlink to `CONTEXT.md` | Claude Code reads this |
| 19 | `.cursorrules` → symlink to `CONTEXT.md` | Cursor reads this |
| 20 | `AGENTS.md` → symlink to `CONTEXT.md` | Codex reads this |
| 21 | `.github/copilot-instructions.md` | Copilot reads this (copy, not symlink) |

### Layer 3: Domain model (what the data looks like)

| # | File | What it tells you |
|---|------|-------------------|
| 22 | `src/pr_sentinel/domain/` | Enums (severity, category), models, finding schema |
| 23 | `src/pr_sentinel/config.py` | All 30+ settings, typed via pydantic-settings |
| 24 | `migrations/001_core.sql` | Core tables: deliveries, repositories, pull_requests, reviews, findings, hitl_items |
| 25 | `migrations/002_semantic.sql` | code_chunks with vector(1536), full-text search indexes |
| 26 | `migrations/003_timeseries.sql` | agent_events hypertable, append-only triggers |
| 27 | `migrations/004_realtime_aggregates.sql` | agent_costs_hourly continuous aggregate |
| 28 | `migrations/005_feedback_decay.sql` | feedback table, decay functions |

### Layer 4: Entry point → queue (how work arrives)

| # | File | What it tells you |
|---|------|-------------------|
| 29 | `src/pr_sentinel/ingress/security.py` | HMAC-SHA256 signature verification |
| 30 | `src/pr_sentinel/ingress/routes.py` | `/webhooks/github` handler, `/healthz`, idempotency |
| 31 | `src/pr_sentinel/ingress/app.py` | FastAPI app, lifespan, middleware |
| 32 | `src/pr_sentinel/queue/` | ARQ worker setup, job enqueue, run_review entry point |

### Layer 5: The core pipeline (how reviews happen)

| # | File | What it tells you |
|---|------|-------------------|
| 33 | `src/pr_sentinel/orchestration/engine.py` | WorkflowEngine ABC — the interface all engines implement |
| 34 | `src/pr_sentinel/orchestration/local_engine.py` | Simplest impl — `asyncio.gather` over 4 agents |
| 35 | `src/pr_sentinel/orchestration/langgraph_engine.py` | Production impl — LangGraph fan-out via Send API |
| 36 | `src/pr_sentinel/orchestration/temporal_engine.py` | Enterprise impl — Temporal durable workflows |
| 37 | `src/pr_sentinel/agents/` | 4 specialist agents (security, correctness, tests, docs) + runner |
| 38 | `src/pr_sentinel/llm/prompts/_base.md` | Base prompt prepended to all specialists |
| 39 | `src/pr_sentinel/llm/prompts/security.md` | Security agent prompt |
| 40 | `src/pr_sentinel/llm/prompts/correctness.md` | Correctness agent prompt |
| 41 | `src/pr_sentinel/llm/prompts/tests.md` | Tests agent prompt |
| 42 | `src/pr_sentinel/llm/prompts/docs.md` | Docs agent prompt |
| 43 | `src/pr_sentinel/llm/client.py` | Provider abstraction (Anthropic, echo) |
| 44 | `src/pr_sentinel/llm/registry.py` | Prompt versioning + bundle hash |
| 45 | `src/pr_sentinel/llm/pricing.py` | Model cost calculation |

### Layer 6: Grounding (how agents get context)

| # | File | What it tells you |
|---|------|-------------------|
| 46 | `src/pr_sentinel/retrieval/context.py` | Hybrid vector+FTS search, RRF merge, context builder |
| 47 | `src/pr_sentinel/retrieval/chunking.py` | Code chunking for embedding |
| 48 | `src/pr_sentinel/retrieval/indexer.py` | Embedding + indexing pipeline |
| 49 | `src/pr_sentinel/llm/embeddings.py` | 3 providers: HashingEmbedder, OpenAIEmbedder, LocalEmbedder |

### Layer 7: Post-pipeline (what happens after agents run)

| # | File | What it tells you |
|---|------|-------------------|
| 50 | `src/pr_sentinel/aggregation/` | Finding merger, dedup, noisy-OR confidence |
| 51 | `src/pr_sentinel/gate/` | Confidence gate + HITL routing logic |
| 52 | `src/pr_sentinel/forge/github.py` | GitHub API client — post reviews to PRs |
| 53 | `src/pr_sentinel/feedback/learning.py` | Preference extraction, 90-day decay, prompt injection |

### Layer 8: Observability + reliability

| # | File | What it tells you |
|---|------|-------------------|
| 54 | `src/pr_sentinel/events/` | Event spine — append-only TimescaleDB hypertable |
| 55 | `src/pr_sentinel/reliability/` | Budget guard, circuit breaker, retry with backoff |
| 56 | `src/pr_sentinel/db/` | Connection pool, migration runner, repository pattern |

### Layer 9: Dashboard + CLI

| # | File | What it tells you |
|---|------|-------------------|
| 57 | `src/pr_sentinel/dashboard/app.py` | 6 routes: queue, review, trace, costs, feedback, health |
| 58 | `src/pr_sentinel/dashboard/templates/` | Server-rendered Jinja2 HTML templates |
| 59 | `src/pr_sentinel/cli.py` | typer CLI: migrate, doctor, eval, worker commands |

### Layer 10: Evaluation

| # | File | What it tells you |
|---|------|-------------------|
| 60 | `tests/eval/README.md` | Eval harness overview |
| 61 | `tests/eval/RESULTS.md` | Recorded results and analysis |
| 62 | `tests/eval/cases.py` | 65 labelled cases — the ground truth |
| 63 | `tests/eval/holdout.json` | 16 held-out case manifest (hashed) |
| 64 | `src/pr_sentinel/evaluation/runner.py` | Eval harness — runs cases against models |
| 65 | `src/pr_sentinel/evaluation/report.py` | Metrics: precision, recall, calibration, cost |
| 66 | `tests/eval/baselines/` | Saved baselines: `echo.json` + `anthropic.json` |
| 67 | `tests/eval/sources/` | Real-world code snapshots used in eval cases |

### Layer 11: Tests

| # | File | What it tells you |
|---|------|-------------------|
| 68 | `tests/unit/` | 247 offline tests (~1.2s) — scan for patterns |
| 69 | `tests/integration/` | 13 tests against live Postgres/Redis |

### Layer 12: Deployment + ops

| # | File | What it tells you |
|---|------|-------------------|
| 70 | `Dockerfile` | Multi-stage build, `entrypoint.sh` routes via SERVICE_TYPE |
| 71 | `entrypoint.sh` | webhook/worker/dashboard routing for single image |
| 72 | `docker-compose.yml` | Local dev: PG16 (`:5434`) + Redis (`:6381`) |
| 73 | `railway.toml` | Railway deployment config (3 services) |
| 74 | `Procfile` | Heroku/Railway process types |
| 75 | `Makefile` | All commands: install, up, migrate, doctor, test, lint, demo, eval |
| 76 | `pyproject.toml` | Package config, dependencies, tool config (ruff, mypy, pytest) |
| 77 | `.github/workflows/ci.yml` | CI: lint + unit tests |

### Layer 13: Scripts

| # | File | What it tells you |
|---|------|-------------------|
| 78 | `scripts/task-loop.sh` | Agnostic iterative task runner (the Ralph Loop) |
| 79 | `scripts/demo_end_to_end.py` | Full pipeline demo (offline, echo provider) |
| 80 | `scripts/build_eval_fixtures.py` | Rebuild golden set from cases.py |
| 81 | `scripts/holdout_manifest.py` | Hash held-out case labels |
| 82 | `scripts/mine_cases.py` | Mine new eval cases from real PRs |
| 83 | `scripts/mine_advisories.py` | Mine security advisories for eval labels |
| 84 | `scripts/mine_introducers.py` | Find commit that introduced a bug |
| 85 | `scripts/blind_label_audit.py` | Audit labels without seeing model output |
| 86 | `scripts/compare_arms.py` | Compare eval arms (A/B testing) |
| 87 | `scripts/diagnose_retrieval.py` | Debug retrieval quality |
| 88 | `scripts/eval_retrieval.py` | Measure retrieval recall@k |
| 89 | `scripts/rescore.py` | Re-score saved results with new metrics |
| 90 | `scripts/wait_for_services.py` | Wait for Postgres/Redis before starting |

---

## Part 4: GitHub Profile Pins

**Profile**: https://github.com/parthamehta123

### Recommended 6 Pins

| Slot | Repo | Why |
|------|------|-----|
| 1 | **pr-sentinel** | Most ambitious project. Multi-agent, production-deployed, 100% recall eval, 247 tests. |
| 2 | **call-intelligence-platform** | 10TB/day pipeline with agent security boundary. Shows scale. |
| 3 | **sentinelgraph-ai** | Financial crime + graph ML + governed GenAI. Shows domain depth. |
| 4 | **safeagent** | Security control plane for AI agents. Unique problem space. |
| 5 | **claude-code-agent** | Single-file AI agent. Raw mechanics, no frameworks. |
| 6 | **production-rag** | RAG with eval gates. "Not a tutorial — a system you can deploy." |

### Pin Narrative

> "I build production-grade AI systems: multi-agent reviewers, financial crime detection, call intelligence at 10TB/day, RAG with eval gates, and I care about security governance for AI agents. I understand the raw mechanics (single-file agent) and the full stack (Railway + TigerData + pgvector + TimescaleDB)."
