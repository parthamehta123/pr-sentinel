# ADR-0001 — A queue between ingress and review

**Status:** Accepted · 2026-09-22

## Context

GitHub gives a webhook consumer a small number of seconds to acknowledge before it
records the delivery as failed and retries. A four-agent review with retrieval
takes minutes. There is no version of "do the work in the request handler" that
survives contact with that constraint.

Even ignoring the deadline, ingress and review have opposite scaling shapes.
Ingress must answer in milliseconds under any load and holds no state. A review
is long, expensive, and stateful. Sizing one process for both means sizing a
webhook endpoint for LLM latency.

## Decision

Ingress verifies, deduplicates, enqueues to Redis via arq, and returns 202. A
separate worker pool runs reviews. Nothing else crosses that boundary.

The job payload is deliberately small — identifiers only. The worker re-fetches
the diff from GitHub rather than trusting a payload that has been sitting in
Redis, which also means a retry after a force-push reviews the current commit
rather than a stale one.

## Consequences

**Gained.** GitHub always gets a fast acknowledgement. The two halves scale
independently. A crashed worker loses a job, not a webhook. Retries, backoff and
concurrency limits are the queue's problem and already solved.

**Given up.** Redis is now on the critical path for accepting work, and there is
no synchronous "review this now" path — `pr-sentinel replay` exists because
operators need one.

**Idempotency became mandatory.** A queue means at-least-once delivery, so three
layers guard it: Redis `SET NX`, arq's `_job_id`, and a unique constraint on
`(pr_id, delivery_id)`. Any one of them is enough; all three are cheap.

## What would make this wrong

If GitHub introduced a long-lived acknowledgement, or if reviews got fast enough
to finish inside the deadline, the queue would be pure overhead. Neither is close.
