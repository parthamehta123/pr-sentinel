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

## What the data says to do next

- **The tests agent is now the weakest**: precision 0.866, and it misses the same
  labelled defect in every run — an assertion-free test. It reliably finds the
  *missing* test and never the test that cannot fail. That gap has held across
  seven live runs, which makes it the strongest signal in this file.
- **Calibration is drifting slightly worse** and is the one metric where the new
  prompt may have cost something. Within noise at n=3; worth watching.
- **The set is too small.** Run-to-run spread on an unchanged configuration is
  ±0.04 overall precision and ±0.15 for a single agent. Anything smaller than
  that cannot be measured here. More cases, not more repetitions.
