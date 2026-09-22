# The eval set

15 labelled pull requests, 15 labelled findings, 5 false-positive traps, and one
case where the correct answer is to say nothing at all.

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

## The traps matter as much as the labels

Five lines are labelled `CLEAN` — a correctly parameterised query one line above an
injection, a fake credential in a `tests/fixtures/` path, SHA-256 used for a file
checksum next to MD5 used for a password. Plus `neg-clean-extract-method`, a pure
refactor where any finding is a false positive.

A set that only rewards recall optimises straight into noise. The offline echo
provider falls into two of these traps, which is the traps working.

## Honest limits

- **Hand-authored, not mined.** The cases come from patterns that recur in review,
  not from production pull requests. That makes them clean and unambiguous, which
  is right for a regression gate, and easier than reality, which is the caveat.
  Mining labelled cases from real merged PRs is the next step.
- **Retrieval is not measured.** Context files are injected directly rather than
  indexed, so what is scored is whether the agents *use* context they were given.
  Retrieval quality needs its own harness and its own labels.
- **15 cases is small.** Enough to catch a regression, not enough for a confident
  absolute number. Treat deltas as signal and absolutes as indicative.
- **The offline numbers mean nothing about review quality.** The `echo` provider is
  a regex matcher. Running `make eval` proves the harness works; only
  `make eval-live` says anything about the reviewer.
