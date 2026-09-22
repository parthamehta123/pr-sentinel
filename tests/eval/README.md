# The eval set

47 labelled pull requests, 47 labelled findings, 21 false-positive traps, seven
cases where the correct answer is to say nothing at all, six multi-file changes,
and thirteen whose defect cannot be seen without the retrieved repository context.
Python, TypeScript, Go, SQL, Terraform and YAML.

```bash
make eval        # offline, free, gated against a saved baseline
make eval-live   # real models. needs ANTHROPIC_API_KEY. costs money.
```

## Why the cases are written as before/after source

`cases.py` holds BEFORE and AFTER file contents, not diffs. The diff is computed,
and labels are attached with marker lines that are stripped before the diff is
generated:

```python
#!EXPECT agent=security category=injection severity>=critical :: `term` is interpolated into the WHERE clause
sql = f"SELECT * FROM customers WHERE name LIKE '%{term}%'"
```

Hand-written diffs with hand-written line numbers rot: one edit shifts everything
and the labels silently stop pointing at the code they describe. Here a label
cannot drift from its line, the marker text never reaches the model, and
`build_eval_fixtures.py` *fails* if a label lands outside the diff — a golden file
whose expected line is unreachable would score as a permanent miss and never be
questioned.

`scripts/build_eval_fixtures.py` regenerates `golden/*.json`, which is committed so
the set is inspectable and diffable. `--check` verifies the committed files are
current, and CI runs it.

## What is measured

**Calibration first.** Does a stated confidence of 0.8 turn out right about 80% of
the time? Every threshold in this system assumes it does, and almost nothing in
the field measures it. Reported as per-bin stated-vs-observed and as expected
calibration error.

**Precision, twice.** A finding matching no label is not automatically wrong — a
model can spot a real defect nobody labelled — but counting all of them as correct
makes precision meaningless. Strict counts them against, lenient does not, and the
truth is between.

**Recall last.** Missing a finding costs what the status quo already costs. A
wrong finding costs credibility, which is harder to get back.

Also: category agreement (right problem, right name), agent attribution (found by
the specialist that should have found it), gate-decision match, and cost per case.

## Measured

Mean over three runs: precision 0.987 strict / 1.000 lenient, recall 1.000 (37 of
37 defects, every run), calibration error 0.182, gate decision match 0.917, $0.070
per review. Recorded in
`baselines/anthropic-3run.json`, which carries every individual finding — with the
labels it matched and the locations it cited — so a run can be diagnosed without
paying to reproduce it. Full tables and the story of each change in
[RESULTS.md](RESULTS.md).

Three bugs the live runs found that no offline test could: the structured-output
schema carried numeric range constraints the API rejects, so every agent call
400'd; recall was computed over matches rather than distinct defects; and the
duplicate-rate metric counted per label, which made co-located findings from
different concerns look like duplication when they are not.

## Three kinds of label

```
EXPECT   a defect that must be found; missing it costs recall
ALLOW    a legitimate observation that is optional; it counts neither way
CLEAN    flagging this is a false positive
```

Two of these are declared once for the whole set rather than marked per line — the
tests agent noting new code has no test, and the docs agent noting it is
undocumented. Both are legitimate anywhere and required nowhere. A `CLEAN` trap
still overrides them, because traps are checked first.

A label may also name alternative categories, `category=injection|logic`, because
one defect genuinely has more than one fair reading: a traceback returned to a
client is information disclosure *and* an error-handling mistake, and rejecting
the second reading cost a correct finding at confidence 0.99.

`ALLOW` exists because most of what a competent reviewer could say about a diff is
neither required nor wrong. Fifteen of the findings it covers are the tests agent
correctly noting that a new function has no test, on a line labelled for something
else. Requiring those would encode "always ask for tests" and penalise restraint;
calling them false positives would label a true statement a lie.

## Traps, and scoping them

A `CLEAN` marker claims a finding at that line would be a false positive. Unscoped,
that is a very strong promise — *nothing here is worth saying* — and it is much
harder to earn than it looks. Three rounds of correcting this set's "clean" cases
still left real defects in them, which the models found each time: a path
traversal in an interpolated output path, an ambiguous string concatenation in a
cache key, a test fixture that imported nothing.

So a trap may be scoped:

```python
#!CLEAN agent=security :: MD5 keys a cache here; no secret, no adversary
```

which says only that flagging this as a security problem is wrong, while noting
that it has no test is fair. Nineteen of the twenty-one traps stay unscoped,
because a set of only scoped traps stops testing restraint.

## The traps matter as much as the labels

Five lines are labelled `CLEAN` — a correctly parameterised query one line above an
injection, a fake credential in a `tests/fixtures/` path, SHA-256 used for a file
checksum next to MD5 used for a password. Plus `neg-clean-extract-method`, a pure
refactor where any finding is a false positive.

A set that only rewards recall optimises straight into noise. The offline echo
provider falls into two of these traps, which is the traps working.

## Run it more than once

```bash
.venv/bin/pr-sentinel eval --provider anthropic --repeat 3
```

Two runs of an identical configuration were measured at 0.909 and 0.923 strict
precision, with one agent producing 5 findings in one run and 9 in the other. A
single run cannot separate a real improvement from the model having a good day.
`--repeat` reports mean and spread, and the rule is simple: a change smaller than
the spread has not been demonstrated.

## Honest limits

- **Hand-authored, not mined.** The cases come from patterns that recur in review,
  not from production pull requests. That makes them clean and unambiguous, which
  is right for a regression gate, and easier than reality, which is the caveat.
  Mining labelled cases from real merged PRs is the next step.
- **Retrieval is not measured.** Context files are injected directly rather than
  indexed, so what is scored is whether the agents *use* context they were given.
  Retrieval quality needs its own harness and its own labels.
- **37 labelled findings across 34 cases is still small.** Measured run-to-run spread on
  an unchanged configuration is around ±0.015 overall precision and ±0.09 for a
  single agent. That is wider than most changes worth making, which is the real
  argument for growing the set.
- **The offline numbers mean nothing about review quality.** The `echo` provider is
  a regex matcher. Running `make eval` proves the harness works; only
  `make eval-live` says anything about the reviewer.
