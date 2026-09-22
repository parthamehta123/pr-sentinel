# Measured results

Every number here is reproducible:

```bash
make eval-live                                         # one run
pr-sentinel eval --provider anthropic --repeat 3 --save arm.json
python scripts/compare_arms.py baseline.json arm.json  # which differences are real
```

Models: `claude-opus-5` (security, correctness), `claude-sonnet-5` (tests),
`claude-haiku-4-5` (docs). 15 cases, 16 labelled findings, 5 false-positive traps.

---

## Current

```
precision 0.948 strict / 0.973 lenient      recall 0.938  (15 of 16 defects)
calibration error 0.122                     findings per concern 1.05
gate decision match 1.000                   $0.072 per review
```

Recorded in `baselines/anthropic.json`, which carries every individual finding so
a run can be diagnosed without paying to reproduce it.

**Calibration** is the number to watch, because every threshold in the gate
assumes a stated 0.8 means roughly 80%. The models come out mildly
*under*confident in the middle of the range and slightly over at the top — the
opposite of the usual worry, and the reason to measure rather than assume. The
auto-post threshold of 0.70 is therefore conservative rather than reckless.

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
