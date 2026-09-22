# Measured results

Every number here is reproducible:

```bash
make eval-live                                         # one run
pr-sentinel eval --provider anthropic --repeat 3 --save arm.json
python scripts/compare_arms.py baseline.json arm.json  # which differences are real
```

Models: `claude-opus-5` (security, correctness), `claude-sonnet-5` (tests),
`claude-haiku-4-5` (docs). 34 cases, 37 labelled findings, 18 false-positive traps.

---

## Current

Mean over three runs of the 34-case set:

```
                        mean      min      max   spread
  precision strict     0.980    0.963    0.988   +0.025
  precision lenient    0.992    0.987    1.000   +0.013
  recall               0.892    0.892    0.892   +0.000
  f1                   0.934    0.926    0.938   +0.012
  calibration error    0.177    0.172    0.186   +0.014
  gate decision match  0.861    0.833    0.917   +0.084
  cost per case       $0.066   $0.065   $0.068   +0.003
```

On the previous 16-case set precision was 1.000 in all three runs — the set had
stopped discriminating. It measures again.

**Three findings that reproduce in every run**, none of which the smaller set
could see:

1. **A consolidated coverage comment cites only one file's worth of locations.**
   *Fixed — see below. The description first given here was wrong: a reviewer
   reading the comment was never underinformed.*

2. **Wildcard CORS with credentials is not rated `critical`.** Every run finds the
   defect and rates it below critical, so the gate's never-post-critical-security
   rule does not fire and the review is auto-posted. Whether that is wrong is a
   judgement call — but it is a reproducible one, and it is the sort of severity
   boundary that was invisible when every security case was an obvious injection.

3. **A clean TypeScript refactor draws a false positive** in two runs of three:
   the tests agent asks for a test of a pure type-narrowing change. Negative
   controls are how that becomes visible.

---

---

## A/B: the docs agent prompt

Three runs per arm. Raw data in `baselines/ab-docs-prompt-old.json` and
`ab-docs-prompt-new.json`.

The docs agent was the panel's noise source. The rewritten prompt states that
zero findings is the expected output, defines a finding as documentation the diff
has made *wrong*, enumerates what is explicitly not a finding, and draws a hard
boundary against other specialists' territory.

**docs agent**

| metric | old (range) | new (range) | verdict |
|---|---|---|---|
| findings produced | 10.3 (9–12) | **3.7 (3–5)** | separated, better |
| "lacks a docstring" notes | 6.7 (5–9) | **1.0 (0–2)** | separated, better |
| findings that would be **posted** | 9.0 (7–11) | **3.3 (3–4)** | separated, better |
| precision | 0.85 (0.75–0.90) | **1.00 (1.00–1.00)** | separated, better |
| recall | 1.00 | 1.00 | within noise |
| out-of-lane findings | 0.33 (0–1) | 0.33 (0–1) | within noise |

**whole panel**

| metric | old (range) | new (range) | verdict |
|---|---|---|---|
| findings that would be posted | 41.7 (41–43) | **35.7 (35–36)** | separated, better |
| precision (strict) | 0.918 (0.89–0.93) | **0.948 (0.95)** | separated, better |
| recall | 0.938 | 0.938 | within noise |
| calibration error | 0.109 (0.08–0.14) | 0.122 (0.11–0.13) | within noise |
| cost per review | $0.074 | $0.072 | within noise |

The security, correctness and tests agents are unchanged on every metric, which
is the control: the effect is isolated to the agent whose prompt changed.

**The result that matters is `posted`.** A reviewer now receives six fewer
comments per pull request, with identical recall — the same defects found, less
attention spent finding out. Precision is a proxy; posted findings are the
product.

### Two claims a single run had gotten wrong

Both were reported as established after one run each, and both dissolved under
repetition:

- **"It stopped straying out of its lane."** One run of the old prompt produced a
  `critical` race-condition finding from the docs agent. Across three runs each,
  both arms average 0.33 — it was a coincidence, not a fix.
- **"Its missing-documentation notes moved from `minor` to `info`."** Also within
  noise. The new prompt does not re-rate those notes; it mostly stops producing
  them.

This is the whole argument for `--repeat`. Two of three headline claims from
single runs did not survive, and the one that did survived convincingly.

---

## A/B: the tests agent prompt

Three runs per arm; arm A is the `ab-docs-prompt-new.json` configuration, which is
identical apart from the tests prompt. Raw data in `baselines/ab-tests-prompt-new.json`.

The diagnosis: across 45 findings in three runs, **every one was a coverage
complaint** — around 13 per pull request of "no test covers X". The agent produced
exactly one test-*quality* finding, never the second quality defect in the same
file, and anchored coverage findings on the test file rather than on the untested
code.

The rewrite puts quality first (a test that exists and cannot fail creates false
confidence; a missing test only leaves you where you were), tells it to report
every quality defect in a file rather than the first, limits coverage complaints
to one per pull request, and anchors them on the changed code.

| metric | old (range) | new (range) | verdict |
|---|---|---|---|
| precision — whole panel | 0.948 (0.95) | **0.992 (0.98–1.00)** | separated, better |
| precision — tests agent | 0.866 (0.86–0.88) | **1.000 (1.00)** | separated, better |
| recall | 0.938 | 0.938 | within noise |
| findings produced | 15.0 (14–16) | 14.0 (13–15) | within noise |
| findings posted | 14.0 | 14.0 | within noise |
| cost | $0.072 | $0.073 | within noise |

**The main goal failed.** The point of the rewrite was to stop the agent filing a
coverage complaint per function. "Say this once per pull request" did not take:
the count is unchanged. Precision improved because the false positive and the
mislocated finding disappeared, not because the agent got quieter.

**Two specific fixes did land:**

- *Anchoring*, cleanly and in all three runs. The "no test for the `CouponExpired`
  branch" finding moved from `tests/test_coupons.py:1` to `billing/coupons.py:6` —
  from a comment on line 1 of a test file to a comment on the untested branch,
  which is where the fix happens. It also stopped scoring as unlabelled, because
  it now lands on the defect it describes.
- *The assertion-free test*, partially. Missed in all seven runs before, found in
  one of three after. Better, not fixed.

### The calibration number went up, and that is not a regression

`calibration_error` reads 0.122 → 0.179, "separated, WORSE". It is an artifact,
and an instructive one:

```
             observed accuracy   mean stated confidence   gap
  old              0.948                 0.843           +0.105
  new              0.992                 0.813           +0.179
```

Every gap is positive: the model is *under*confident, and it became more so
because it became more accurate while claiming slightly less. Expected calibration
error cannot be small when observed accuracy sits near 1.0 unless stated
confidence is also near 1.0 — and asking a model to claim near-certainty is the
last thing this system wants, because the confidence field is what the gate
depends on when a case is genuinely hard.

**Calibration error saturates as an objective once precision approaches 1.0 on a
small set.** `compare_arms.py` now prints a warning above 0.95 precision. Read ECE
alongside precision, never alone.

### A tooling bug this A/B exposed

The first comparison reported `noise_notes` as "separated, WORSE" for the tests
agent — 5.7 → 13.3. The heuristic matched phrases like "has no", which is the docs
agent generating noise and the tests agent *doing its job* ("has no test covering
the race"). The script applied a docs-specific phrase list to every agent. It is
now per-agent, and undefined metrics are omitted rather than scored.

---

## The aggregator collapse: a negative result

Built to fix a problem that does not exist. Recorded because the reasoning
failure is worth more than the code.

**The claim.** "The tests agent files one coverage comment per changed function —
around 13 per pull request." I wrote that after reading three runs.

**The mistake.** It was 13 findings across *fifteen separate pull requests* —
about one each. Every eval case touches one or two files, so no agent ever had the
chance to repeat itself. The "13 per PR" figure was an extrapolation to a 31-file
pull request, presented as a measurement.

**The fix, built anyway.** `_collapse_repeated` folds several findings of the same
collapsible category from one agent into a single comment carrying every location
as evidence, above a configurable threshold. Only `test_coverage` qualifies:
"add a test for this" is one ask however many functions it covers, whereas two SQL
injections in two files are two things to fix. Nine unit tests pin the behaviour.

**The measurement.** A new case — `tst-repeated-coverage-gap`, six new public
functions across three files, none tested — gave the failure mode somewhere to
happen. It didn't:

| run | coverage findings | locations named (of 6) |
|---|---|---|
| 1 | 1 | 6 |
| 2 | 1 | 4 |
| 3 | 1 | 2 |

**One consolidated finding, every run.** The agent already says it once. The
collapse has never fired against a real model. It is kept as a guard — the failure
mode is plausible for other models or much larger diffs, and the threshold makes
it a no-op until it isn't — but it should be deleted if it has still never fired
in six months.

### What the exercise did produce

- **A matcher fix.** A consolidated finding citing six locations covers six
  defects; the matcher counted only the first, so the system scored 2 of 6 for
  doing exactly the right thing. Findings now match every label their own range
  *or* their cited evidence covers. Recall figures from before this change are not
  comparable with figures after it.
- **A real weakness, newly measurable.** The consolidated comment names an
  inconsistent subset of the locations it is consolidating — six, then four, then
  two, across three runs of an identical configuration. A reviewer reading the
  two-location version is told about two of six untested functions. That is a
  prompt gap ("name every location you are consolidating"), it is the single
  largest source of missed labels in the set, and it did not exist as a measurable
  thing before this case was added.
- **The set's first multi-file case.** Fifteen of sixteen cases touch one or two
  files. Whole classes of behaviour — repetition, truncation, cross-file
  reasoning — were simply invisible.

---

## Fixing the incomplete citation — and correcting the claim

The 34-case set reported four missed labels in every run, all on
`tst-repeated-coverage-gap`. Written up as "a reviewer acting on that comment
fixes a third of the problem."

**That was wrong, and reading the comment shows it.** The body says:

> csv_rows, csv_escape, dump_streaming, dump_pretty, margins_for, and
> usable_width are all new public functions with no accompanying tests…

All six named. A reviewer is fully informed. What was incomplete was `evidence` —
the machine-readable record of where a finding applies, which is what is stored,
audited and read back weeks later by someone deciding whether the finding was
fair. Prose in `body` is not that record.

Which still matters, and there was a real bug underneath it: **the evidence list
was capped at five entries.** A finding covering six locations lost one silently,
so the case was unwinnable whatever the model did. The cap is now 12, with a unit
test.

With the cap raised, one instruction in the shared prompt — cite every location a
finding covers, not just the first — settled it. Three runs per arm on the
affected case:

| | before | after |
|---|---|---|
| evidence locations cited, per run | 1, 1, 3 | **6, 6, 6** |
| recall | 0.555 (0.333–1.000) | **1.000 (spread 0.000)** |

Raw data in `baselines/ab-evidence-citation-{old,new}.json`. The cap fix is
present in both arms, so the table isolates the prompt change.

### A failure the incident exposed

The full-set refresh after this change returned **zero findings, zero cost, and a
calibration error of 0.000** — which is also what a flawless run of a reviewer
that says nothing looks like. The cause was an exhausted API credit balance. The
run scored itself as a result and overwrote a good baseline on the way past; a
regression gate fed that report would have passed it.

The runner now refuses to score a run in which every agent failed in every case,
and saves nothing. Partial failure is still scored, because a degraded result is
a real result — only a total outage is refused.

---

## How the set grew

| | before | after |
|---|---|---|
| cases | 16 | **34** |
| labelled findings | 22 | **37** |
| false-positive traps | 5 | **18** |
| multi-file changes | 1 | **5** |
| cases where the answer is silence | 1 | **4** |
| languages | Python | Python, TypeScript, Terraform, TOML, Markdown |

The second tranche was written to be harder in three specific ways, because a set
where every labelled defect is obvious stops discriminating once a model gets good:

- **A real defect sits next to a plausible look-alike.** A path join with no
  confinement check, one function below one with `realpath` and a prefix test. A
  signature compared with `==`, one line below one compared with `compare_digest`.
  A mutable default argument beside a correct `None` sentinel. A retry decorator
  on a charge, beside a retry decorator on a read. Sixteen of the thirty-four
  cases now carry at least one trap.
- **Not everything is Python.** A Terraform bucket made world-readable by copying
  the public-assets block above it; wildcard CORS with credentials in TypeScript;
  a null dereference on a genuinely optional field.
- **More of them have nothing wrong.** Four negative controls, including a pull
  request that only adds good tests and a routine dependency bump — the shapes
  where a reviewer's credibility is actually spent.

---

## What the data says to do next

- **Precision has run out of room.** 0.992 on 15 cases with 16 labels means the
  set can no longer tell a good change from a great one. Every remaining question
  needs harder cases, not more tuning against these.
- **The tests agent still files one coverage comment per function.** Two attempts
  at instructing it otherwise have not worked. The next thing to try is structural
  rather than textual: have the aggregator collapse same-agent coverage findings
  across a pull request into one, which does not depend on the model complying.
- **The assertion-free test is found one run in three.** That is the clearest
  remaining quality gap and the one a larger set would let us fix with confidence.
- **Matching is location-based**, so a coverage complaint that happens to land on
  a line labelled for security counts as a hit. It inflates per-agent numbers for
  whichever agent comments most. Agent attribution (0.33) is the honest view; a
  family-aware matcher would be better.
- **The set is too small.** Run-to-run spread on an unchanged configuration is
  ±0.04 overall precision and ±0.15 for a single agent. Anything smaller than that
  cannot be measured here. More cases, not more repetitions.
