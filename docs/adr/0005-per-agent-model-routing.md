# ADR-0005 — A provider seam and per-agent model routing

**Status:** Accepted · 2026-09-22

## Context

Four agents, four different jobs. The security agent's question — trace
attacker-controlled data from entry to sink, across retrieved code — is genuinely
hard reasoning where a miss is expensive. The docs agent's question is whether a
docstring still matches the code beneath it. Running both on the most capable
model is straightforwardly wasteful; running both on the cheapest is negligent.

Separately: model APIs differ in ways that are easy to get wrong and expensive to
debug. `claude-opus-5` and `claude-sonnet-5` take `thinking: {"type": "adaptive"}`
and reject `budget_tokens` outright. `claude-haiku-4-5` is the other way round and
also rejects `output_config.effort`. Encoding that in four agents means getting it
wrong in at least one.

## Decision

An `LLMProvider` interface (`LLMRequest` in, `LLMResponse` out), a per-model
capability and pricing table, and a router that picks a model per agent:

| Agent | Model | Reasoning |
|---|---|---|
| security | `claude-opus-5` | highest consequence of a miss |
| correctness | `claude-opus-5` | must reason across retrieved callers |
| tests | `claude-sonnet-5` | narrower question, still needs judgement |
| docs | `claude-haiku-4-5` | most mechanical, least consequential |

All four are `.env` variables. Every call goes through one facade that applies the
budget check, the circuit breaker, the retry policy, and the event-spine write.

## Consequences

**Gained.** Roughly two thirds of the per-review cost is the two Opus agents, so
routing the other two down is a material saving with no measurable quality cost on
their questions. The capability table keeps per-model API quirks in one place. An
`EchoProvider` — a deterministic fake reviewer with real static rules — makes the
entire pipeline testable offline, for free, in CI.

**Given up.** Four models means four cache namespaces, since prompt caches are
model-scoped. Quality differences between agents are now partly attributable to
routing, which makes prompt iteration harder to read.

**Not deferred by accident.** Embeddings need a second provider regardless —
Anthropic has no embeddings API — so the default is a local feature-hashing
embedder that keeps the system runnable with one key, with OpenAI behind a flag.

## What would make this wrong

If measurement showed the tests or docs agent missing things the larger models
catch, the routing is wrong and the right fix is to raise the model rather than to
rewrite the prompt. That measurement needs the eval harness, which is why the eval
harness is the top item on the roadmap.

## Amendment, 2026-09-25 — docs moves from Haiku to Sonnet

The original split put the docs specialist on the cheapest model because its job
looked the most mechanical: find the docstring the diff made untrue. That holds
for most of what it does and is wrong about the part that matters.

`doc-misleading-name` — a function called `get_tenant_fresh` that writes to a
shared cache and evicts from it — was missed run after run. **Three attempts to
fix it by rewording the prompt failed**, and the last of those correctly found and
repaired a genuine contradiction between the system prompt and the per-run focus
line, and still did not move the number. It was recorded here as a measured
capability limit.

That conclusion was premature. The model was never varied:

| | name found |
|---|---|
| `claude-haiku-4-5` | **3 of 10 runs** |
| `claude-sonnet-5` | **10 of 10 runs** |

Docs is now on `claude-sonnet-5`.

**Cost.** Roughly 8% more on this case ($0.91 → $0.98 over ten runs), because the
docs agent's input is the smallest on the panel — it reads the diff and the
conventions, not the retrieved neighbourhood.

**Noise.** The obvious risk is that a stronger model finds more to say on changes
that deserve silence. Measured across every negative control plus the MD5 trap,
three runs each: **zero docs findings on any of them**, and no decision changed.
The quietness the docs prompt asks for survives the model change.

**What this says about the original split.** "Mechanical" was the wrong axis.
Deciding whether a name misleads is a judgement about what a reader will assume,
and it is the hardest thing the docs agent is asked to do, not the easiest. The
rest of its work — is this docstring now untrue — really is mechanical, which is
why the gap took three prompt attempts and a deliberate model comparison to find.
