# Runbook

## Daily checks

```bash
make doctor                       # config, extensions, append-only trail, vector width
.venv/bin/pr-sentinel costs       # spend today, cost by agent, escalation rate
.venv/bin/pr-sentinel queue       # what is waiting for a human
```

Open `:8081` for the same three things rendered, plus a trace viewer.

## The numbers that matter

| Signal | Healthy | What a bad reading means |
|---|---|---|
| Escalation rate | 20–50% | **>80%**: the model is not calibrated, or the thresholds are too high. **<5%**: the model is probably not being honest about its uncertainty — spot-check some auto-posted reviews |
| Queue depth | trending flat | Growing means humans cannot keep up. Raise thresholds, narrow which repos are watched, or add reviewers — do not just let it grow |
| Dispute rate per agent | low and stable | A rising rate for one agent points at its prompt or its model, not at the system |
| Spend vs daily cap | under | At the cap, reviews escalate instead of running. Visible, but only if someone looks |
| `p95_ms` per agent | steady | A climbing p95 on one agent usually means diffs are getting larger, not that the model got slower |

```sql
-- escalation reasons over the last week, most common first
SELECT decision_reason, count(*)
  FROM reviews
 WHERE started_at > now() - INTERVAL '7 days' AND decision = 'escalate'
 GROUP BY 1 ORDER BY 2 DESC;

-- findings that got disputed, by agent — the prompt-quality signal
SELECT f.agent, count(*) FILTER (WHERE fb.verdict = 'disputed') AS disputed, count(*) AS total
  FROM findings f LEFT JOIN feedback fb ON fb.finding_id = f.id
 WHERE f.created_at > now() - INTERVAL '30 days'
 GROUP BY 1 ORDER BY 2 DESC;

-- how often the grounding filter is discarding findings, per agent
SELECT agent,
       sum((attributes->>'dropped_ungrounded')::int) AS dropped,
       sum((attributes->>'findings')::int)           AS kept
  FROM agent_events
 WHERE kind = 'span_end' AND name LIKE 'agent.%' AND ts > now() - INTERVAL '7 days'
 GROUP BY 1;
```

That last one is the early warning for a model drifting: a rising drop rate means
it is increasingly citing code it was not shown.

---

## Incidents

### The queue is growing and nothing is being reviewed

```bash
docker exec sentinel-redis redis-cli zcard arq:queue   # depth
.venv/bin/pr-sentinel costs                            # is the cap hit?
```

In order of likelihood: the daily cost cap is reached (everything escalates —
raise it or wait for midnight); the worker is down (`make worker`); the LLM
circuit breaker is open (look for `circuit.open` in the worker log — it probes
again after 30s on its own); the worker pool is too small (`max_jobs` in
`WorkerSettings`).

### Reviews are failing with a database error

```bash
make doctor
```

If the vector dimension is reported as a mismatch, stop. Either `EMBEDDING_DIM`
changed or the column did. Do not "fix" it by re-indexing — decide which value is
correct first, since changing the column width discards every existing embedding.

### GitHub is returning 403

Almost always the secondary rate limiter. The client already honours
`Retry-After`, so this resolves itself unless the token is wrong or lacks
`pull_requests: write`. Check the worker log for `github.403`.

### A finding is wrong and someone is annoyed

```bash
.venv/bin/pr-sentinel trace <review-id>
```

That gives the prompt bundle, the model, the retrieved context and the cost for
every step. The review row records which prompt bundle produced it, so the exact
prompt text is recoverable from git.

Record the dispute through the dashboard so it lands in `feedback`. Nothing acts
on a single dispute (ADR-0007) — it is a metric, not a lever.

### The reviewer posted something it should not have

The gate withholds critical security findings, so this means either
`ESCALATE_CRITICAL_SECURITY` is off or the finding was not classified as security.
Check the category on the finding row; if the category was wrong, that is a prompt
problem in the specialist that raised it.

---

## Routine operations

**Re-index a repository** — after a large merge, or when retrieval starts
returning stale code:

```bash
.venv/bin/pr-sentinel index owner/repo ~/src/repo --commit "$(git -C ~/src/repo rev-parse HEAD)"
```

Full re-index; it replaces the previous one in a single transaction, so retrieval
never sees a half-built index.

**Add a team convention** — these are injected verbatim into every prompt and are
the highest-leverage, lowest-risk way to change behaviour:

```sql
INSERT INTO conventions (repo_id, rule, source)
VALUES ((SELECT id FROM repositories WHERE full_name = 'owner/repo'),
        'Database access goes through the repository layer, never raw SQL in handlers.',
        'CONTRIBUTING.md');
```

**Change a prompt.** Edit the file in `src/pr_sentinel/llm/prompts/`, bump its
`<!-- version: -->` header. The bundle hash changes automatically and is stamped
on every subsequent review, so before/after is separable in the data.

**Raise or lower a threshold.** `.env`, then restart the worker. Move in steps of
0.05 and watch the escalation rate for a day before moving again.

---

## Deployment notes

- Ingress and worker are separate processes and scale independently. Ingress is
  stateless — run several behind a load balancer.
- One worker at a time should run migrations. `make migrate` is idempotent and
  records checksums, but concurrent runs are untested.
- Health check for the load balancer: `GET /healthz` on ingress.
- The worker holds no local state. Kill it whenever; in-flight reviews resume
  from persisted verdicts and re-run only what is missing.
- `GITHUB_WEBHOOK_SECRET` left at its placeholder refuses to start outside `dev`.
- Set `APP_ENV=prod` for JSON logs.

## Backup and restore

One Postgres, so one `pg_dump`. `agent_events` will dominate the size; if that
becomes a problem the right answer is a TimescaleDB retention policy on the raw
hypertable while keeping the continuous aggregates, since the rollups are what the
dashboard actually reads:

```sql
SELECT add_retention_policy('agent_events', INTERVAL '90 days');
```

Think before enabling that — it caps how far back a disputed finding can be
defended, which is the reason the spine exists.
