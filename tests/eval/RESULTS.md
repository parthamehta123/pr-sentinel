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

## The matcher was measuring the wrong thing

`agent_attribution` had sat at 0.33 for every run — only a third of findings came
from the specialist that should have found them — and it was reported in passing
several times without being investigated. It should have been.

Splitting the hits over three recorded runs:

| | count | share |
|---|---|---|
| expected agent, same concern | 92 | 35.7% |
| **different agent, different concern** | **146** | **56.6%** |
| different agent, same concern | 20 | 7.8% |

Only 7.8% was genuine cross-agent overlap. The majority were findings that landed
near a labelled line while talking about something else entirely — the tests agent
noting "no test covers this" on a line labelled for SQL injection, scored as a hit
on the injection. Labelled *security* defects were credited to correctness 36% of
the time, tests 28%, and security 21%.

**The architecture was fine; the metric was broken.** Two corrections:

- **A hit now requires the finding to be about the label's family of concern.**
  Landing nearby while discussing something else makes it unlabelled, which is
  what it is.
- **A trap fires only on the line it declares clean**, with no snapping tolerance.
  Twelve of thirteen recorded "false positives" were three to five lines away,
  aimed squarely at the defective function below the clean one and dragged onto it
  by the ±3 window. That tolerance is right for deciding whether a real defect was
  found and wrong for deciding whether a clean line was flagged.

Re-scored over the same recorded findings — the models were not re-run, only the
arithmetic changed:

| | before | after |
|---|---|---|
| agent attribution | 0.331 | **0.808** |
| category agreement | 0.387 | **0.947** |
| recall | 1.000 | 0.964 |
| precision strict | 0.983 | **0.402** |
| precision lenient | 0.983 | 0.879 |
| false positives per run | ~1 | 4.3 |

Recall barely moved, so the specialists really do describe almost every labelled
defect. **Precision was the inflated number** — 0.983 was measuring "did a finding
land near a labelled line", not "was it about the defect".

`scripts/rescore.py` exists because of this. Scoring has now changed four times,
and each time the recorded numbers quietly stopped meaning what the current code
would produce. Re-running the models to find out costs about ten dollars and
twenty minutes; the findings have not changed, only the arithmetic over them, so
re-scoring does it for nothing.

### Labelling what the models already find

0.402 strict against 0.879 lenient meant about sixty per cent of findings matched
no label. Those were mined out of the recorded runs — 71 distinct unlabelled
findings, 28 of which appeared in all three runs and so were worth a decision.

Judging them turned up something the set had no way to express. Six were **real
defects nobody had labelled**, including several worth having:

- renaming `--workers` to `--concurrency` breaks every existing invocation and any
  caller reading `args.workers` — a contract break the case only labelled as a
  documentation problem
- a wildcard CORS origin with `credentials: true` is *rejected by browsers*, so
  the configuration does not work at all; the case labelled the security problem
  and missed that it is also simply broken
- `csv_rows` joins raw values and never calls `csv_escape`, in a case written to
  be about test coverage

But fifteen were the tests agent correctly noting that a new function has no test,
on a line labelled for something else. **Neither `EXPECT` nor `CLEAN` was right.**
Requiring them would encode "always ask for tests" and penalise exactly the
restraint the system is built around; calling them false positives would label a
true statement a lie.

So the set gained a third kind of label:

```
EXPECT   a defect that must be found; missing it costs recall
ALLOW    a legitimate observation that is optional; it counts neither way
CLEAN    flagging this is a false positive
```

`ALLOW` findings are excluded from both precision denominators and from recall.
Making one costs nothing; not making one costs nothing.

Re-scored over the same recorded findings:

| | before labelling | after |
|---|---|---|
| precision strict | 0.402 | **0.653** |
| precision lenient | 0.879 | 0.898 |
| recall | 0.964 | 0.969 |
| agent attribution | 0.808 | **0.841** |
| unlabelled findings per run | 42.3 | **15.7** |

The set is now 53 required findings, 21 permitted, 21 traps. Strict precision
moved because the labels got more honest, not because the reviewer got better —
worth remembering when reading the number.

Fifteen or so findings per run are still unlabelled, and they are the next batch
to judge.

### What the honest precision number is telling us

0.402 strict against 0.879 lenient, with roughly 42 unlabelled findings per run,
means **about sixty per cent of what the system produces is not described by any
label**. Those are mostly legitimate observations the set does not cover: a real
missing test, a real undocumented parameter, on a line labelled for something
else. They are neither right nor wrong as far as the set is concerned.

That was the clearest direction for the set, and it is what the section above
acted on. Precision is still reported twice, and quoting the strict figure alone
would be as misleading as quoting 0.983 was.

---

## Current

Mean over three runs of the 34-case set. **These are pre-fix scores** — the
committed baseline JSON was produced by the old matcher. `scripts/rescore.py`
prints the current-matcher numbers over the same findings, and they are in the
table above.

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

## Third tranche — and how hard it is to write a clean case

Precision 0.983 and recall 1.000 meant the set had saturated again. Thirteen more
cases, attacking the two things it could not see.

**Defects invisible in the diff alone.** A migration that duplicates an index an
earlier migration already created; a retry default lowered from 5 to 1 where the
retrieved caller depends on the old value to survive a dependency that 503s one
call in three. Thirteen of the forty-seven cases now carry repository context that
the finding depends on, which is what retrieval is for and what almost nothing in
the set was testing.

**Whole cases that look alarming and are correct.** Dynamic SQL built by string
formatting from a closed allowlist. A refactor from `shell=True` to a list argv.
MD5 used to key a render cache. Every earlier trap was a clean sibling beside a
real defect, which is a weaker test than a change that reads as a vulnerability
from top to bottom and is not one.

Plus the severity boundary from both sides after ADR-0006 widened `critical` — a
deleted authorisation check against a removed rate limit and a leaked stack trace;
Go, SQL and a GitHub Actions workflow using `pull_request_target`; deletions, where
the defect is a guard that is gone rather than a line that is wrong; and one
defect buried in a forty-function mechanical rename.

| | before | after |
|---|---|---|
| cases | 34 | **47** |
| labelled findings | 37 | **47** |
| false-positive traps | 18 | **21** |
| cases needing repository context | ~3 | **13** |
| languages | 5 | **8** (adds Go, SQL, YAML) |

### The part worth reading

Writing a case with a planted bug is easy. Writing one that is genuinely clean is
much harder, and three rounds of trying failed:

- Round 1, eight trap hits. Inspection: *"directory passed to tar without `--`,
  allowing option injection"* — correct, a directory beginning with `-` is read by
  tar as an option. *"unknown sort raises KeyError instead of being rejected"* —
  correct, a 500 where a 400 belongs.
- Round 2, four. *"`f"/exports/{name}.tar.gz"` is still interpolated without
  validation"* — correct, a genuine path traversal I had left in.
  *"template and context concatenated without a delimiter"* — correct, `"ab"+"c"`
  and `"a"+"bc"` collide.
- Round 3, one. *"Test module never imports create_archive; both tests error"* at
  confidence 0.96 — simply true of the fixture I had written.

Each round the models were right and the label was wrong. Two conclusions came out
of it, and both are now in the harness.

**Traps can be scoped.** `#!CLEAN agent=security` claims only that flagging this
line as a security problem is a false positive, while observing that it has no
test is a legitimate thing to say. An unscoped `CLEAN` still claims that nothing
at all is a finding — 19 of the 21 traps keep that stronger promise, because a set
of only scoped traps stops testing restraint.

**Two of the negative controls were relabelled rather than defended.** They are
`trap-` rather than `neg-` now, with no gate expectation, because they are not
"nothing to find" cases: they test one specific reflex — MD5 is not always weak
crypto, `shell=False` is not command execution — while leaving other legitimate
observations available.

On the thirteen new cases, one run: precision 0.778 strict and 0.955 lenient,
recall 1.000. The gap between the two numbers is the design working — the
unlabelled findings behind it are, on inspection, mostly real.

**The full 47-case baseline is not recorded yet.** The API credit balance ran out
mid-run. The runner refused to score it, saved nothing, and left the previous
baseline intact — which is the guard added after the last time this happened
doing its job. The committed `baselines/anthropic-3run.json` is therefore the
34-case one, and the numbers at the top of this file are its numbers.

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
