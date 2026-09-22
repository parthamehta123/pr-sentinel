# ADR-0003 — Raw SQL over an ORM

**Status:** Accepted · 2026-09-22

## Context

The schema is small — nine tables — but three of them use features no ORM models
well: pgvector distance operators, `tsvector` with `websearch_to_tsquery`,
`create_hypertable`, continuous aggregates, and triggers that make a table
append-only.

## Decision

asyncpg with hand-written SQL, organised into repository modules. Migrations are
plain `.sql` files applied by a small runner.

## Consequences

**Gained.** The hybrid search query — a CTE fusing vector kNN and full-text by
reciprocal rank — is written once, readable, and tunable. Migrations say exactly
what they do. No mapping layer to fight when TimescaleDB needs something an ORM
has never heard of.

**Given up.** No compile-time checking of column names. Repositories are more
verbose. Changing a column means finding every query that touches it.

**An unexpected cost.** The migration runner needed a dollar-quote-aware statement
splitter, because a continuous aggregate cannot be created inside a transaction
block and a multi-statement simple query *is* one. Splitting naively on `;` breaks
every PL/pgSQL body in the schema — which is how a runner silently applies half a
migration. It is about sixty lines and has its own tests.

## What would make this wrong

If the schema tripled and most of the new tables were ordinary CRUD, an ORM for
those with raw SQL reserved for the extension-heavy ones would pay for itself.
