# ADR-0002 — One Postgres for all three data shapes

**Status:** Accepted · 2026-09-22

## Context

Three genuinely different shapes of state: semantic (the repository as retrievable
memory), relational (reviews, findings, the human queue), and temporal (spans,
costs, decisions). The specialist answer is three stores — a vector database,
Postgres, and something time-series — and each is individually better at its job.

But the question the system actually asks is *"for this pull request, what did we
retrieve, what did we conclude, and what did it cost?"* Three stores turn that
into a three-way stitch in application code with no transactional story, plus
three connection pools, three backup procedures, three upgrade paths, and three
things that can be independently down at 3am.

## Decision

One Postgres. pgvector and pgvectorscale for the semantic shape, plain tables for
the relational shape, TimescaleDB hypertables and continuous aggregates for the
temporal shape. `timescale/timescaledb-ha` ships all of them; Tiger Cloud is the
same image managed, so the migrations apply unchanged either way.

## Consequences

**Gained.** One durable store, one backup, one place to query. A retrieved chunk
can be joined to the review that used it and the cost that produced it in a single
statement. The append-only guarantee on the audit trail is enforced by database
triggers rather than by application discipline.

**Given up.** pgvector at very large scale is not Qdrant. TimescaleDB is not
ClickHouse. Both are the right trade at this size and the wrong trade at some
larger one.

**A sharp edge.** `code_chunks.embedding` is `vector(1536)` and `EMBEDDING_DIM`
must agree. pgvector neither pads nor truncates — it raises at INSERT time, long
after a full embedding run has been paid for. `pr-sentinel doctor` compares the
catalog against the configuration and fails loudly, because nothing else will.

## What would make this wrong

Roughly: tens of millions of chunks where DiskANN recall or latency stops being
acceptable, or event volume where hypertable ingest becomes the bottleneck. Both
are a long way past where this system is useful, and the migration path out is
per-shape rather than all-or-nothing.
