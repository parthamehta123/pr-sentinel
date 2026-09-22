# pr-sentinel

A pull-request reviewer that stays quiet.

Most AI review tools optimise for coverage: read the diff, ask a model what is
wrong, post everything it says. That produces a tool people mute within a
fortnight, because the cost of a wrong comment is not zero — it is the attention
of the person who reads it and the credibility of every comment after it.

pr-sentinel optimises for a different thing:

> **Spend a senior engineer's attention only where that attention is worth
> something, and be able to prove, weeks later, why every call was made.**

Everything in the architecture falls out of that one sentence. Four specialists
instead of one prompt, because that is how a reviewer actually reads a diff.
Retrieval, because a reviewer who does not know the repository is guessing.
Confidence and rationale on every finding, because a gate needs something to gate
on and a dispute needs something to appeal to. An append-only event spine,
because "why did it say that" is a question someone will ask in three weeks.

---

## What it does

```
 GitHub webhook
      │  HMAC verified, deduplicated, acknowledged in milliseconds
      ▼
  Redis (arq)                          ← ingress and review scale separately
      │
      ▼
  Orchestrator ──► retrieval  (hybrid vector + full-text over the repo)
      │
      ├──► security      ┐
      ├──► correctness   │  four specialists, in parallel, each grounded
      ├──► tests         │  in the diff and the retrieved neighbourhood
      └──► docs          ┘
      │
      ▼
  Aggregator            merge, dedupe, noisy-OR across agreeing agents
      │
      ▼
  Confidence gate ──► post to the PR   (confident, non-critical-security)
                 └──► human queue      (everything else)
      │
      ▼
  Event spine           every span, call, cost and decision — append-only
```

## Quick start

Nothing below needs an API key. The `echo` provider is a deterministic fake
reviewer with real static rules, so the whole pipeline runs offline.

```bash
cd /Users/parthamehta/pr-sentinel
make install        # venv + dependencies
make up             # Postgres (TimescaleDB + pgvector) and Redis on 5434 / 6381
make migrate        # apply the schema
make doctor         # verify extensions, the append-only trail, the vector width
make test           # 183 tests, no services needed for the unit half
make demo           # the whole pipeline end to end, offline, in ~2 seconds
make eval           # score the panel against the golden set
```

`make demo` walks the real code path and prints what each stage produced:

```
1. webhook signature — INVARIANT-1
   valid signature accepted   : True
   tampered body rejected     : True
   ...
4. aggregation
   [critical] billing/handler.py:16   0.96  SQL built by string interpolation  (correctness + security)
   [critical] billing/handler.py:13   0.82  Credential literal committed to the repository  (security)
   [major   ] billing/handler.py:19   0.71  Bare except swallows control-flow exceptions  (correctness)
   overall confidence         : 0.788

5. the confidence gate
   decision                   : escalate
   reason                     : critical_security
   why                        : 2 critical security finding(s); not posted to the pull
                                request to avoid disclosure.
```

Two things worth noticing in that output. The SQL finding reached 0.96 because
*two* specialists found it independently — agreement is the strongest signal this
system produces. And nothing was posted, because a comment describing a live
vulnerability on a pull request is a disclosure; it went to a private queue
instead.

## Measuring it

34 labelled pull requests live in [`tests/eval/`](tests/eval/): 37 labelled
findings across Python, TypeScript and Terraform, **18 false-positive traps**,
four cases where the right answer is silence, and five multi-file changes.

Sixteen of the cases put a real defect next to a plausible look-alike — a path
join with no confinement check one function below one with `realpath`; a signature
compared with `==` one line below one compared with `compare_digest` — because a
set where every defect is obvious stops discriminating once a model gets good.

```bash
make eval         # offline, free, gated against a saved baseline
make eval-live    # real models — needs ANTHROPIC_API_KEY, costs money
```

The headline number is **calibration**, not precision: does a stated confidence of
0.8 turn out right about 80% of the time? Every threshold in the gate assumes it
does, and that assumption is otherwise untested.

Measured against `claude-opus-5` (security, correctness), `claude-sonnet-5`
(tests) and `claude-haiku-4-5` (docs):

```
                        mean      min      max
  precision strict     0.980    0.963    0.988
  precision lenient    0.992    0.987    1.000
  recall               0.892    0.892    0.892
  calibration error    0.177    0.172    0.186
  gate decision match  0.861    0.833    0.917
  cost per review     $0.066   $0.065   $0.068
```

These figures predate the evidence-citation fix described in
[RESULTS.md](tests/eval/RESULTS.md); a refreshed full-set baseline is pending.

Two things still reproduce in every run, neither visible on the smaller set:
wildcard CORS with credentials is found but not rated `critical`, so it auto-posts
instead of escalating; and a clean TypeScript refactor draws a false positive two
runs in three.

Always a mean over repeats, never a single run: an identical configuration has
been seen to vary by 0.18 in recall. And read the calibration error next to the
precision, never alone — above ~0.95 precision it mostly measures how far a stated
confidence sits below an observed accuracy near 1.0, which is underconfidence
rather than miscalibration.

```bash
pr-sentinel eval --provider anthropic --repeat 3 --save arm.json
python scripts/compare_arms.py baseline.json arm.json
```

That comparison is what settled whether rewriting the docs-agent prompt helped.
It did — a reviewer receives **six fewer comments per pull request at identical
recall**. It also showed that several claims made from single runs were
coincidence, and that one fix built from a confident diagnosis addressed a problem
that does not occur. Both are written up, including the reasoning failures, in
[tests/eval/RESULTS.md](tests/eval/RESULTS.md).

On calibration — the number the whole gate rests on — the models come out mildly
**under**confident in the middle of the range and slightly over at the top. That
is the opposite of the usual worry, and it means the 0.70 auto-post threshold is
conservative rather than reckless. It is also exactly the kind of thing you
cannot guess.

Precision is reported twice — strictly (a finding matching no label counts
against) and leniently (it does not) — because a model can find a real defect
nobody labelled, and pretending otherwise is how a set gets gamed.

Cases are written as before/after source in `tests/eval/cases.py`, never as
hand-written diffs; the builder computes the diff and **fails** if a label lands
outside it. See [tests/eval/README.md](tests/eval/README.md) for the limits of the
set, which are real.

## Running it for real

```bash
cp .env.example .env          # then fill in the three values below
```

| Variable | Why |
|---|---|
| `GITHUB_WEBHOOK_SECRET` | `python -c "import secrets; print(secrets.token_hex(32))"`, and the same value in the GitHub webhook settings |
| `GITHUB_TOKEN` | fine-grained PAT: Pull requests read/write, Contents read |
| `ANTHROPIC_API_KEY` | or omit it and sign in with `ant auth login` — the SDK finds the profile |

Then set `LLM_PROVIDER=anthropic` and:

```bash
make api            # webhook ingress on :8080
make worker         # the review worker
make dashboard      # human queue, traces and cost on :8081

# index a repository so retrieval has something to retrieve
.venv/bin/pr-sentinel index owner/repo ~/src/repo

# review a real PR without waiting for a webhook (read-only by default)
.venv/bin/pr-sentinel replay owner/repo 128
```

Point a GitHub webhook at `https://your-host/webhooks/github`, content type
`application/json`, event `Pull requests`.

## Configuration worth understanding

**Per-agent model routing.** Security and correctness get `claude-opus-5`; tests
get `claude-sonnet-5`; docs gets `claude-haiku-4-5`. The docs agent does the most
mechanical work and produces the least consequential findings, so it does not
need the most capable model. Change any of them in `.env`.

**The two thresholds.**

- `AUTO_POST_CONFIDENCE` (0.70) — below this, the *whole review* goes to a human.
- `FINDING_POST_CONFIDENCE` (0.60) — individual findings under this are withheld
  even from a review that cleared the gate.

Start both high. The escalation rate on the dashboard tells you when to lower
them; a system that escalates 90% of reviews is not yet earning its keep, and one
that escalates 2% is probably not being honest about its confidence.

**Cost caps.** `REVIEW_COST_CAP_USD` stops one 40 000-line pull request eating the
day; `DAILY_COST_CAP_USD` stops a webhook storm eating the month. Hitting either
escalates rather than degrading silently — a human should know the reviewer went
quiet.

**Embeddings.** Default is `hash`: a deterministic local feature-hashing embedder,
no key, no network, clearly weaker recall. Switch to `EMBEDDING_PROVIDER=openai`
when retrieval quality starts to matter. If you change `EMBEDDING_DIM`, change the
`vector(N)` column in `migrations/002_semantic.sql` too — `make doctor` refuses to
start on a mismatch, because otherwise it stays invisible until the first insert
after a full, paid-for indexing run.

## The four invariants

These are the things the system is not allowed to get wrong, and each one is
enforced somewhere it cannot be argued with rather than by convention.

| | Invariant | Enforced by |
|---|---|---|
| 1 | Nothing is parsed, logged, enqueued or stored before the webhook signature verifies | `ingress/security.py`, first statement of the handler |
| 2 | A redelivered webhook never produces a second review | Redis `SET NX`, an arq job id, and a unique constraint |
| 3 | Every finding carries a rationale, a confidence and a line inside the diff | Pydantic validators, the grounding filter, `NOT NULL` + `CHECK` |
| 4 | The audit trail cannot be rewritten | `BEFORE UPDATE/DELETE/TRUNCATE` triggers on `agent_events` |

`make doctor` proves 4 against the live database on every run, by attempting all
three mutations inside a transaction it then rolls back.

## Where to read next

| | |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | how the design was derived, component by component, and what each component's failure modes are |
| [docs/adr/](docs/adr/) | seven decisions, each with what was given up |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | operating it: what breaks, how it looks, what to do |
| [docs/ROADMAP.md](docs/ROADMAP.md) | what is deliberately not built yet, and why |

## Layout

```
src/pr_sentinel/
  ingress/      FastAPI webhook: verify, dedupe, enqueue, 202. Nothing else.
  queue/        arq dispatch and the review worker
  orchestration/ pipeline, two interchangeable engines, the aggregator
  agents/       four specialists + the grounding filter they share
  llm/          provider seam, model router, pricing, versioned prompts
  retrieval/    chunking, indexing, hybrid search, context assembly
  gate/         the confidence gate
  forge/        GitHub client and unified-diff parsing
  events/       the append-only spine
  db/           asyncpg pool, migration runner, repositories
  reliability/  retry, circuit breaker, budget guard
  dashboard/    human queue, trace viewer, cost
  evaluation/   the golden-set harness: matching, calibration, scoring
migrations/     four SQL files; three data shapes, one store
tests/          167 unit (offline) + 16 integration (live Postgres)
tests/eval/     the golden set: cases.py is the source, golden/*.json is generated
```

## Status

The vertical slice runs end to end — webhook through to a posted review or a
queued escalation, with traces and costs recorded — and has been exercised against
real pull requests on github.com, which found two bugs that no test had:

- `replay` reached the pipeline without passing through ingress, so no
  `deliveries` row existed for a foreign key that needed one.
- The full-text half of hybrid search was built with `websearch_to_tsquery`, which
  **ANDs** unquoted terms. A query built from a 31-file diff both exceeded the
  tsquery parser stack and, when it did not crash, demanded all two hundred terms
  in a single chunk — so that half of retrieval was silently doing nothing.

The eval has also been run against real models, which found three more:

- The structured-output schema carried `minimum`/`maximum` on `confidence`. The
  API rejects numeric range constraints, so **every agent call 400'd**. The
  reliability layer handled it correctly — retried, opened the circuit breaker,
  spent nothing — which is how the bug stayed cheap.
- Recall was computed over matches rather than over distinct defects, so a run
  producing four findings for one defect would have reported inflated recall.
- Seven of the fifteen `expected_decision` labels were ill-defined: the gate's
  outcome there depends on model confidence, which is a property of the model
  under test, not of the case. They are now set only where a structural rule
  determines the answer, and match 5/5.

All fixed with regression tests.

What is deliberately not built — GitHub App auth, incremental re-indexing, mining
the eval set from real merged PRs, and learning from recorded disputes — is in
[docs/ROADMAP.md](docs/ROADMAP.md) with the reasoning for each.
