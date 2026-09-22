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

Mean over three runs of the 34-case set, after the evidence-citation fix:

```
                        mean      min      max   spread
  precision strict     0.987    0.973    1.000   +0.027
  precision lenient    1.000    1.000    1.000   +0.000
  recall               1.000    1.000    1.000   +0.000
  f1                   0.993    0.986    1.000   +0.014
  calibration error    0.182    0.157    0.201   +0.044
  findings per concern 0.921    0.912    0.932   +0.019
  cost per case       $0.070   $0.067   $0.075   +0.008
```

**37 of 37 labelled defects found, in every run.** Against the pre-fix baseline
that is recall 0.892 → 1.000 with non-overlapping ranges and a spread of exactly
zero; every other metric moved within noise, including cost. The four misses that
had reproduced in all three previous runs are gone.

**Recall has now saturated too.** That is the honest reading: with precision at
0.987 and recall at 1.000, the set measures almost nothing about whether the next
change is an improvement. It remains useful as a regression gate — it would still
catch something breaking — but it can no longer rank two good configurations, and
that is again an argument for more and harder cases rather than more tuning.

**The one surviving disagreement has been resolved** — see the severity A/B
below.

The false positive on the clean TypeScript refactor did not appear in any of the
three runs after the fix, having appeared in two of three before. Nothing in the
change plausibly addresses it and `compare_arms.py` reads the difference as noise,
so treat it as unresolved.

---

---

## A/B: what counts as a `critical` security finding

`sec-cors-wildcard-credentials` was found in all six runs recorded up to this
point, rated `major` every time, and therefore auto-posted rather than escalated.
The data could not say whether the model was under-rating it or the label was
wrong; that was a judgement call, and it was made: a wildcard origin with
credentials lets any site read authenticated responses, so it is critical.

The guidance written from that is about the principle, not about CORS — a rule
naming one misconfiguration teaches nothing about the next one. Rate by what is at
stake if you are right, not by how many steps someone would need to take; and for
security, `critical` covers a control removed, weakened or bypassed, whether or
not an end-to-end exploit is demonstrated.

Three runs per arm, raw data in `baselines/ab-severity-{old,new}.json`:

| | old | new |
|---|---|---|
| the case's severity | `major`, `major`, `major` | **`critical` ×3** |
| the case's decision | `auto_post` ×3 | **`escalate` ×3** |
| precision strict | 0.987 | 0.983 |
| recall | 1.000 | 1.000 |
| negative controls | 12× suppress | 12× suppress |
| escalated cases, of 34 | 8.0 (7–9) | 9.7 (9–10) |
| cost per review | $0.070 | $0.071 |

Everything except the target moved within noise, and the four negative controls
stayed silent in all three runs, so the broadening did not leak into cases where
the right answer is nothing.

**The number to watch is escalations: 8.0 → 9.7 of 34.** Within noise at n=3, but
in the direction you would expect, and it is the real cost of the decision.
Broadening `critical` spends human queue capacity, which is the resource the whole
system exists to protect.

One wobble worth recording: `sec-path-traversal` wanted `escalate` and got
`auto_post` in one run of three, having escalated reliably before. One sample in
three is not a finding, but it is the kind of thing that becomes one.

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
