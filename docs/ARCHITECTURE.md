# Architecture

This document explains how the design was derived rather than just what it is.
Each section states the problem a component exists to solve, the choice made, and
the ways it is expected to fail.

---

## 0. The premise

Remove the tool and look at what happens without it. A pull request opens; it
waits for a senior engineer to notice; they context-switch into an unfamiliar
change; they read it; they leave comments. The costs are the waiting, the
switching, and the fact that the tenth review of the day is not the first review
of the day — fatigue makes it inconsistent in ways nobody can see from outside.

The naive reading is "automate the reviewing". That is wrong, and it is wrong in
a way that produces a tool people turn off. The mechanical parts of a review are
exactly the parts a linter and a type checker already do for free. What is scarce
is *judgement*, and judgement does not get cheaper by being asked for more often.

So the problem is not coverage. It is selectivity:

> Surface what is worth a senior engineer's attention. Defer the rest. Be able to
> prove afterwards why each thing landed on which side of that line.

Three consequences follow immediately, and they are the spine of everything below:

1. **A finding must be defensible.** Not "this looks risky" but "line 41 reaches
   the query without passing the validator on line 12". That means rationale and
   evidence are required fields, not nice-to-haves.
2. **A finding must carry calibrated uncertainty.** Without it there is nothing to
   gate on, and every finding is either always posted or always escalated.
3. **The system must be auditable after the fact.** A disputed finding three weeks
   later needs the prompt version, the retrieved context, the model, and the cost.

---

## 1. Ingress

**Problem.** GitHub gives a webhook consumer a small number of seconds to
acknowledge before it records a failed delivery and retries. A four-agent review
takes minutes. Those two facts are irreconcilable in one request handler.

**Choice.** Ingress does the minimum that is still safe, then hands off:

```
verify HMAC  →  claim idempotency key  →  parse  →  enqueue  →  202
```

The order is the design, not tidiness. Parsing attacker-controlled JSON and
writing an attacker-chosen repository name into the delivery ledger are both real
work done on an unauthenticated request. Verification comes first, always, and
returns an uninformative 401 so a forger learns nothing about which part failed.

**Failure modes and what handles them.**

| Failure | Handling |
|---|---|
| Forged webhook | HMAC-SHA256, constant-time compare. `==` here leaks the signature a byte at a time |
| GitHub redelivers | Redis `SET NX` (fast) + `deliveries` primary key (durable) + arq `_job_id` (race-proof) |
| Authenticated but malformed body | 400, nothing enqueued. Not a 500 — that would put a bad payload into the retry loop forever |
| Queue outgrows the workers | Ingress is stateless: scale it and the worker pool independently. That is the entire reason they are separate processes |
| Draft PRs, label events, stars | Acknowledged with 202 and dropped. Cheaper than making the worker decide |

---

## 2. The data layer

**Problem.** Three genuinely different shapes of state:

| Shape | What | Access pattern |
|---|---|---|
| Semantic | the repository as retrievable memory | approximate nearest neighbour + exact identifier match |
| Relational | reviews, findings, verdicts, the human queue | joins, constraints, transactions |
| Temporal | every span, call, decision and cost | time-ordered append, windowed rollup |

The reflex is three stores: a vector database, Postgres, and something
time-series. Each is individually the better tool.

**Choice.** One managed Postgres with extensions — pgvector, pgvectorscale,
TimescaleDB — because the question the system actually asks is *"for this pull
request, what did we retrieve, what did we conclude, and what did it cost?"*, and
that is one join in one store or a three-way stitch in application code. Three
stores also means three connection pools, three backup stories, three failure
modes, and three things that can be independently down at 3am.

The cost is real: pgvector at very large scale is not Qdrant, and TimescaleDB is
not ClickHouse. Both are the right trade at this size and the wrong trade at some
larger one. ADR-0002 records where the line is.

**Details that matter.**

- `agent_events` is a hypertable with `BEFORE UPDATE/DELETE/TRUNCATE` triggers
  that raise. TRUNCATE needs its own statement-level trigger because it is a
  distinct command whose execution path never fires row-level DELETE triggers —
  which is how "append-only" tables quietly turn out not to be.
- Continuous aggregates roll cost and latency into hourly buckets, with
  `materialized_only = false` so a read includes the current, not-yet-materialised
  hour. Without that the budget guard is blind to exactly the spike it exists to
  catch.
- `code_chunks.embedding` is `vector(1536)` and `EMBEDDING_DIM` must match.
  `pr-sentinel doctor` compares them and refuses to start otherwise, because this
  particular mismatch is invisible until the first insert after a full,
  already-paid-for indexing run.

---

## 3. Retrieval

**Problem.** A reviewer who does not know the repository is guessing. A model
shown only the diff is exactly that reviewer. But the repository does not fit in
the context window, and stuffing it there would be expensive and worse — the
relevant 200 lines get lost among 200 000 irrelevant ones.

**Choice.** Hybrid search, fused by reciprocal rank, queried **per file**.

Pure vector search is bad at exact identifiers, which is the single most common
thing a reviewer needs to look up: *who else calls this function?* Pure full-text
is bad at "code that does something like this". Reciprocal rank fusion combines
them without needing the two scores to be calibrated against each other, which is
why it is here instead of a tuned weighted sum that would need re-tuning whenever
either side changed.

Per-file rather than one query for the whole PR: a pull request touching an auth
handler and a CSS file has two unrelated neighbourhoods, and one blended query
returns the centroid of both, which is nothing.

**Chunking.** Split on top-level definition boundaries where the language makes
that cheap to detect, fall back to overlapping windows elsewhere, and window
anything over 120 lines rather than truncating it. No tree-sitter: a wrong
boundary costs recall, not correctness, because the file path and line range
travel with every chunk.

**Failure modes.** Embedding or search failure degrades rather than fails — agents
still get the diff and the conventions, and the prompt already tells them to treat
unseen code as unknown. A stale index is the quiet one: retrieval returns the
previous commit's code and nobody notices. `repositories.indexed_sha` records
what was indexed; surfacing drift is on the roadmap.

---

## 4. The panel

**Problem.** One prompt asked to check security, correctness, tests and
documentation does all four worse than four prompts doing one each — the same way
a human does. Attention is finite in both.

**Choice.** Four specialists, fanned out in parallel, fanned back in by a
deterministic aggregator. Each gets its own framing, its own retrieved context,
and its own model:

| Agent | Question | Model | Why |
|---|---|---|---|
| security | could this be exploited, and by whom? | `claude-opus-5` | highest consequence of a miss |
| correctness | does it do what it evidently intends? | `claude-opus-5` | needs to reason across retrieved callers |
| tests | if this were wrong, would anything fail? | `claude-sonnet-5` | narrower question, still needs judgement |
| docs | will the next person understand it? | `claude-haiku-4-5` | most mechanical, least consequential |

**The grounding filter is the highest-leverage code in the system.** Every finding
must cite a new-file line that exists in the diff. A model that cites a line
outside it has either miscounted inside a hunk — recoverable, so snap to the
nearest real line within three — or invented the code, which is not, so the
finding is dropped and counted. The same check is what stops GitHub rejecting an
inline comment on a line it does not recognise: one mechanism, two payoffs.

The other enforcement is structural. Findings come back through
`output_config.format` with a JSON schema that makes `rationale` and `confidence`
required, and the Pydantic model rejects an empty rationale. A model cannot
produce a finding in this system without saying why.

**Failure modes.** A crashed, timed-out or budget-denied agent returns a failed
verdict rather than taking the panel down — and a panel with a hole in it cannot
auto-post, because three quarters of a panel is not a panel. A refusal
(`stop_reason == "refusal"`, which arrives as HTTP 200) is caught explicitly;
reading `content` without checking would yield an empty review that looks exactly
like a clean bill of health.

---

## 5. Aggregation

Deterministic. There is no model in the merge step, on purpose: an aggregator that
reasons can hallucinate a finding no specialist made, and then nobody owns it.

Findings cluster by file, overlapping line range, and either a shared category or
a similar title. Within a cluster:

- **Different agents agreeing** → noisy-OR. Two independent 0.7s become 0.91.
  Independent agreement is the strongest signal this system produces.
- **The same agent repeating itself** → no boost at all. A model saying something
  twice is not two pieces of evidence.
- Confidence is capped at 0.99. Nothing here earns certainty.

Overall confidence is a severity-weighted mean, multiplied by the fraction of the
panel that reported in. A review where the security agent timed out is not a
confident review, whatever the survivors said.

---

## 6. The gate

Where selectivity actually happens. Precedence order is the contract:

| | Condition | Decision | Why it outranks what is below it |
|---|---|---|---|
| 1 | budget exhausted | escalate | a partial review presented as complete is worse than an honest "I ran out" |
| 2 | any agent failed | escalate | an incomplete panel has an unknown blind spot |
| 3 | critical **security** finding | escalate, never post | a comment describing a live vulnerability is a disclosure |
| 4 | overall confidence below threshold | escalate | the whole point |
| 5 | otherwise | post findings clearing their own threshold | |

Rule 3 is the one most systems get wrong. A critical SQL injection posted as a
public PR comment is a zero-day announcement with a permalink. It goes to a
private queue regardless of how confident the model was.

A review that clears the gate still withholds individual findings below
`FINDING_POST_CONFIDENCE`, and says so in the summary. If nothing clears it, the
system posts nothing at all — staying quiet is a valid, and frequently correct,
outcome.

---

## 7. The event spine

One append-only stream does three jobs that would otherwise need three systems:
reconstruct any review end to end, defend any single finding, and price the whole
thing. Spans nest, `kind='llm_call'` rows are the sole source for the cost
rollups, and `kind='decision'` rows record every gate outcome with its reasoning.

Writes are best-effort by design — losing an audit row must never fail a review
that is otherwise fine — and every drop is logged and counted.

Repositories record conclusions. The spine records the walk. Nothing else is
allowed to be the source of truth for "what happened".

---

## 8. Reliability

| Mechanism | Guards against | Note |
|---|---|---|
| Retry with full jitter | transient blips | Full jitter, not fixed backoff: synchronised retries are how a recovering service gets knocked over twice |
| `Retry-After` honoured | GitHub's secondary rate limiter | A server that told us when to come back knows better than our curve |
| Circuit breaker per dependency | sustained outages | Retries make an outage *worse*: every worker pays full timeout on every call |
| Per-node timeout | a hung agent | An aggregator waiting forever is the classic fan-in deadlock |
| Budget guard | runaway cost | Charges the worst case *before* the call, so a nearly-exhausted budget cannot be blown through by one expensive request |
| Persisted verdicts | crashes mid-review | A retry re-runs only the agents with no row. The cheapest useful durable execution, and it needs nothing from the orchestration framework |
| Idempotency, three layers | duplicate reviews | Redis, arq job id, unique constraint |

---

## 9. What is deliberately absent

- **A harder eval set.** The cases are hand-authored, not mined from real merged
  pull requests. Good enough to catch a regression, not enough for a confident
  absolute number.
- **Retrieval evaluation.** The harness injects context rather than retrieving it,
  so it scores the agents and not the retriever.
- **Learning from disputes.** Feedback is recorded but nothing acts on it. One
  grumpy afternoon should not be able to retrain the reviewer; the
  minimum-evidence rule that makes this safe is designed (ADR-0007) and not built.
- **GitHub App authentication.** A PAT is fine for one org and wrong for a product.
- **Incremental indexing.** Currently a full re-index per repository.
- **Semantic caching.** Prompt caching on the system block is in; caching across
  near-identical diffs is not.
