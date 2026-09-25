# pr-sentinel — Product Requirements Document

## Overview

A selective, evidence-grounded multi-agent PR reviewer that surfaces findings
worth a senior engineer's attention and defers the rest. Built with a
framework-agnostic orchestration layer, measured by a 65-case eval harness.

## Completion Criteria

The project is DONE when ALL of the following are true:

- [ ] `make test` passes (247+ tests, 0 failures)
- [ ] `make lint` passes (ruff check + ruff format + mypy)
- [ ] `make demo` runs the full pipeline offline in <5 seconds
- [ ] `make eval` gates against the saved baseline
- [ ] `make doctor` reports all green (config, extensions, schema, embedding dim)
- [ ] CI is green on GitHub (lint-and-unit job)
- [ ] All 65 eval cases have correct fixtures (`build_eval_fixtures.py --check`)
- [ ] Holdout manifest has 0 drifted cases
- [ ] No hardcoded credentials in any file (GitHub push protection passes)
- [ ] README.md accurately describes the project

## Architecture Requirements

| Component | Required | Measured |
|---|---|---|
| 4 specialist agents (security, correctness, tests, docs) | Yes | Eval baseline |
| Framework-agnostic orchestration (3 engines) | Yes | Test runs same panel through all |
| Hybrid retrieval (vector + FTS) | Yes | Retrieval recall measured |
| Confidence gate with HITL | Yes | Gate accuracy 100% across 3 runs |
| Append-only event spine | Yes | Doctor verifies with attempted mutations |
| Budget guard (per-review + daily) | Yes | Continuous aggregates |
| Feedback learning loop | Yes | Preference extraction + decay |
| Dashboard (queue + trace + costs + feedback) | Yes | Server-rendered pages |
| Deployment config (Docker + Railway) | Yes | Dockerfile + railway.toml |

## Eval Requirements

| Metric | Minimum | Current |
|---|---|---|
| Precision (strict) | >90% | 93.8% |
| Precision (lenient) | >95% | 99.6% |
| Recall | >95% | 97.8% |
| Gate decision accuracy | 100% | 100% |
| False positives | 0 systematic | 0 in all 3 runs |
| Cost per case | <$0.15 | $0.079 |

## Tasks (for task-loop.sh)

### Phase 1: Core (COMPLETE)
- [x] Webhook ingress with HMAC + idempotency
- [x] Redis/ARQ job queue
- [x] 4 specialist agents with fan-out/fan-in
- [x] LangGraph + asyncio orchestration engines
- [x] Hybrid retrieval (pgvector + FTS)
- [x] Aggregator with noisy-OR confidence
- [x] Confidence gate + HITL routing
- [x] Event spine (TimescaleDB hypertable)
- [x] GitHub PR posting
- [x] Budget guard

### Phase 2: Eval (COMPLETE)
- [x] 65 labelled cases with golden fixtures
- [x] Held-out manifest for independently-sourced labels
- [x] 3-run live baseline on Anthropic API
- [x] Unknown category rejection at build time
- [x] Distinct-label recall (not duplicate matches)
- [x] Degraded-case separation from recall

### Phase 3: Production Features (COMPLETE)
- [x] Temporal engine (third orchestration option)
- [x] Feedback learning loop (preference extraction + decay)
- [x] Dashboard: trace viewer, cost dashboard, feedback page
- [x] Dockerfile (multi-stage, non-root)
- [x] Railway deployment config
- [x] TigerData cloud support
- [x] Model routing per agent (Opus for security, Sonnet for docs)
- [x] Prompt registry with versioning

### Phase 4: Agent Tooling (COMPLETE)
- [x] CONTEXT.md — agnostic project context file
- [x] Agent aliases (CLAUDE.md, .cursorrules, AGENTS.md, copilot-instructions.md)
- [x] task-loop.sh — agnostic iterative task runner
- [x] PRD.md with completion criteria
