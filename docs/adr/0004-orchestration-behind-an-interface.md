# ADR-0004 — Orchestration behind an interface, with two implementations

**Status:** Accepted · 2026-09-22

## Context

The panel is one fan-out/fan-in step. LangGraph expresses that well — `Send` makes
"run this node per item in parallel and reduce" a first-class edge rather than a
`gather` buried in a node — and it is moving quickly, with import paths that have
already relocated between releases. Temporal is the durable-execution answer, and
it is a server to run, a worker model to learn, and workflow-shape constraints to
design around, none of which this system needs yet.

## Decision

Three things, together:

1. A `WorkflowEngine` interface with a single method, `run_panel`.
2. **Two** implementations: `LangGraphEngine` and `LocalEngine` (plain
   `asyncio.gather`). LangGraph is used when installed; the local engine otherwise.
3. Durability from persisted verdicts rather than from framework checkpointing.
   Each agent's verdict is written as it lands, and a retry re-runs only the
   agents with no row.

## Consequences

**Gained.** LangGraph is confined to one file. The abstraction is *exercised*
rather than aspirational — a test runs the same panel through both engines and
asserts the verdicts match, so a leak fails CI instead of being discovered during
a migration. Resume works without any framework support, which means it keeps
working through a framework swap.

**Given up.** Two code paths to maintain, and the local engine is what actually
runs in the offline demo and in CI, so the LangGraph path gets less production
exposure than its position in the stack suggests.

**A note on the second implementation.** Writing a fallback you do not need is
usually waste. It is justified here because it is twenty lines, it is what proves
the interface is real, and it removes a hard dependency on a fast-moving library
from a system that otherwise has none.

## What would make this wrong

If the workflow grew conditional routing, human-in-the-loop resumption mid-graph,
or multi-day durability, the local engine would stop being a twenty-line
equivalent and Temporal would start being worth its operational cost. The
interface is what makes that a new file rather than a rewrite.
