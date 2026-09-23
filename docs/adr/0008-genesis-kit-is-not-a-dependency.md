# ADR-0008 — Genesis Kit is not a dependency; three of its diagnoses are

**Status:** Accepted · 2026-09-24

## Context

This reviewer was built while following a walkthrough that used [Genesis
Kit](https://github.com/ayush488-glitch/genesis-kit) as its development harness.
Because the two appear together there, the reasonable question keeps coming back:
is Genesis missing from this repository, and should it be installed?

Genesis is a harness for coding agents. It sits beside a project and forces the
agent to keep a durable record — `.genesis/` state, a locked `DONE.html`, spec and
plan approvals, gates that refuse to call a milestone finished without fresh
proof, a code graph the agent can query over MCP, and a baseline-versus-candidate
loop where a human promotes a rule before it is adopted.

pr-sentinel is the thing you would build *with* such a harness. None of Genesis is
present here, and that was checked rather than assumed: no `.genesis/`, no
`SPEC.md`, no `KICKOFF.md`, no `recipes/`, no `skills/`, no `tools/genesis`, no MCP
server. Design decisions live in these ADRs instead; the only dashboard is the
cost and human-queue view for reviews, which is about pull requests and not about
agent work.

## Decision

**Genesis is not taken as a dependency.** It solves a different problem —
governing the agent that writes the code — and installing it would add a harness
without changing a line of the reviewer.

**Three of the five failure modes it names were live defects here, and those are
adopted.** Genesis's diagnosis turned out to be worth more than its code:

| Genesis failure mode | Found here | Response |
|---|---|---|
| Memory loss — state only in context | No. RESULTS.md, recorded runs, golden fixtures, append-only event spine | — |
| Untiered model use | No. `claude-opus-5` for security and correctness, `claude-sonnet-5` for tests, `claude-haiku-4-5` for docs (ADR-0005) | — |
| Duplication — rebuilding what exists | Not applicable to a service of this size | — |
| **Narration over execution** | **Yes.** RESULTS.md was prose around hand-transcribed tables, where a mistyped digit is indistinguishable from a measurement | A test recomputes each recorded baseline's per-run counts and fails if the document does not state them |
| **Self-grading** | **Yes**, twice over | See below |

Self-grading was the expensive one. Two instances:

- Eval labels are written in response to model output, which is why strict
  precision became unfalsifiable: the loop "measure → label whatever is
  unlabelled → report 1.000" cannot report a bad result. Mitigated by the rule
  that a label derived from model output is `ALLOW` and may only become `EXPECT`
  on independent evidence, which keeps recall honest even when precision cannot be.
- The audit *of those labels* was run by the agent that wrote them, knowing it was
  hunting overstatement. Replacing it with a checker that sees the case and the
  note but no verdict — `scripts/blind_label_audit.py` — found **5 problems where
  the hand audit found 3**, including a plain factual error about `NPM_TOKEN` that
  two agents had written and reviewed without reading the `env:` block three lines
  below it.

## Consequences

**Gained.** The reviewer stays a reviewer, with no second framework's state
machine to keep alive. The three adopted ideas are now enforced by tests rather
than by intention, which is the only form of a process rule that survives.

**Given up.** There is no harness governing the agents that work on *this*
repository: no spec approval, no completion gates, no forced proof-before-done.
That cost is real and has been paid in visible ways — this repository has, on
record, had eight hand-written negative controls turn out to contain real defects,
two commits pushed past a red test, and a recorded baseline overwritten by its own
rescoring pass. A harness of the Genesis kind is aimed squarely at that class of
mistake.

**Not a closed door.** Installing Genesis is an install, not a rewrite. Nothing in
the reviewer conflicts with it, and adopting it later would change how work on the
repository is governed without touching what the repository does.

## What would make this wrong

More than one person working on this at once, or an agent running unattended
against it. Both failures the harness prevents — work claimed done without proof,
and context lost between sessions — are mild with a single operator reading every
diff and severe without one. If this stops being a repository one person reads end
to end, revisit.
