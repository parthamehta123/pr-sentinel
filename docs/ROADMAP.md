# Roadmap

What is deliberately not built, in the order it should be, with the reason each
one is where it is.

## 0. Done — the eval harness

Built. `tests/eval/` holds 15 labelled cases, `src/pr_sentinel/evaluation/` the
matching and scoring, and `make eval` gates against a saved baseline. It measures
calibration first, precision strictly and leniently, recall, category agreement,
agent attribution, gate-decision match and cost.

## 0. Keep labelling what the models find

Done once: 28 stable unlabelled findings mined from the recorded runs, six of them
real defects the set had missed, fifteen legitimate-but-optional. Unlabelled
findings fell from 42 per run to 15.7 and strict precision from a misleading 0.402
to 0.653.

It also produced the `ALLOW` label — an observation that is legitimate but not
required, counting neither for recall nor against precision — which is what the
set needed to stop choosing between demanding every true statement and calling it
noise.

About fifteen findings per run are still unlabelled. The loop is cheap now:
`scripts/rescore.py` re-scores any recorded run for nothing, so a labelling pass
costs no API calls at all.

## 1. Grow the eval set

Run against real models: precision 0.886/0.975, recall 0.933, calibration error
0.110, $0.07 per review. Recorded in `tests/eval/baselines/anthropic-3run.json`.

Calibration came out better than feared and in an unexpected direction — the
models are mildly *under*confident in the middle of the range rather than over.
That means the auto-post threshold of 0.70 is, if anything, conservative, and
there is room to lower it once the set is big enough for the number to be
trustworthy.

Which is the actual next step: the set needs to get bigger and harder. The 15 cases are hand-authored from patterns
that recur in review — clean, unambiguous, and easier than reality. Mining
labelled cases from real merged pull requests is what turns a regression gate into
a measurement. Thirty to fifty, split train/test, is the target.

**The set is too small to settle the questions it raises.** Two runs of an
identical configuration measured 0.909 and 0.923 strict precision, and the docs
agent produced 5 findings in one and 9 in the other. Most changes worth making
are smaller than that spread. `--repeat N` now reports mean and spread; the fix
is more cases, not more repetitions.

Answered since: the docs-agent prompt rewrite was measured over three runs per
arm and is real — six fewer posted comments per pull request at identical recall.
Two further claims made from single runs turned out to be coincidence. Tables in
[tests/eval/RESULTS.md](../tests/eval/RESULTS.md).

## 2. Decide whether the gate's severity rule is right

Baseline refreshed: recall 1.000 across three runs, all 37 defects found.

The one thing that disagrees in every run recorded so far is
`sec-cors-wildcard-credentials`. The model finds it, rates it below `critical`,
and the review auto-posts instead of escalating. Six consistent samples, so this
is a real property and not variance.

Two defensible readings, and the data cannot pick between them. Either the model
is under-rating a genuine critical — a wildcard origin with credentials lets any
site read authenticated responses — or the label is wrong and this belongs in the
`major` band, where auto-posting is correct. Deciding it is a judgement call about
what the gate is for, and the answer should be written down in ADR-0006 either
way.

## 2. Learning from recorded disputes

Designed in ADR-0007, deliberately unbuilt. Needs the eval harness first, because
the only way to know a suppression rule helped is to measure it. The
minimum-evidence rule — several independent disputes, different actors, similar
findings — is the part that makes it safe, and it is worthless without a way to
check it worked.

## 3. GitHub App authentication

A personal access token is fine for one organisation and wrong for anything
installed by someone else. Installation tokens, per-repository permissions, and a
webhook secret per installation. Mechanical work, no design questions, which is
why it is below the two items that have design questions.

## 4. Incremental indexing

Currently a full re-index per repository. Should index only files changed since
`repositories.indexed_sha`. The user-visible problem is not speed — it is that a
stale index degrades retrieval quietly, and nothing currently surfaces the drift.
Surface the drift first; the incremental path is an optimisation after that.

## 5. Semantic caching across similar diffs

Prompt caching on the system block is already in. The bigger win is recognising
that a force-push changed three lines of a diff the panel reviewed twenty minutes
ago, and re-running only the agents whose input actually moved. Needs a stable
per-agent input hash, which the persisted-verdict machinery is already most of.

## 6. More specialists

Performance, dependencies and licensing, migration safety, and API compatibility
are all plausible fifth agents. Each one costs a model call per review, so each
one needs to earn it against the eval set — which is another reason the eval
harness is first.

## 7. Repository-specific calibration

Thresholds are global today. A repository where the team disputes 40% of findings
and one where they dispute 2% should not share an auto-post threshold. Per-repo
thresholds derived from dispute history, with a global default and a floor.

---

## Explicitly not planned

- **Blocking merges.** The review is advisory (`event=COMMENT`) and should stay
  that way. A tool that can block on a hallucination is a tool that gets removed.
- **Auto-fixing.** Suggesting a patch is a different product with a different risk
  profile, and it undermines the selectivity premise: the value here is deciding
  what is worth a human's attention, not doing the work for them.
- **Reviewing its own reviews.** An LLM judge over LLM findings compounds
  correlated errors rather than cancelling them. Cross-agent agreement, which is
  already in the aggregator, is the version of this that works.
