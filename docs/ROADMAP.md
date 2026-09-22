# Roadmap

What is deliberately not built, in the order it should be, with the reason each
one is where it is.

## 0. Done — the eval harness

Built. `tests/eval/` holds 15 labelled cases, `src/pr_sentinel/evaluation/` the
matching and scoring, and `make eval` gates against a saved baseline. It measures
calibration first, precision strictly and leniently, recall, category agreement,
agent attribution, gate-decision match and cost.

## 1. Run the eval against real models, and grow the set

The harness exists; the numbers that matter do not, because the reviewer has never
been run against a real model. `make eval-live` produces them.

Then the set needs to get harder. The 15 cases are hand-authored from patterns
that recur in review — clean, unambiguous, and easier than reality. Mining
labelled cases from real merged pull requests is what turns a regression gate into
a measurement. Thirty to fifty, split train/test, is the target.

Watch the calibration number above everything else. If a stated 0.8 turns out
right 50% of the time, every threshold in the gate is arbitrary and no amount of
prompt tuning fixes it — the fix is to tell the models what the confidence field
is for, which is a prompt change the harness can now score.

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
