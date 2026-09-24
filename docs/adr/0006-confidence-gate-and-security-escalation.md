# ADR-0006 — The confidence gate, and never posting critical security findings

**Status:** Accepted · 2026-09-22

## Context

The system's entire premise is selectivity. Something has to decide what reaches a
pull request, what reaches a human queue, and what reaches neither. Get it wrong
in one direction and the tool is noise; wrong in the other and it is decorative.

There is also a specific hazard that generic confidence thresholds do not cover.
A critical SQL injection posted as a public pull-request comment is a zero-day
announcement with a permalink, and the more confident the model was, the worse the
disclosure.

## Decision

A gate with a strict precedence order:

1. Budget exhausted → escalate.
2. Any agent failed → escalate.
3. Any **critical security** finding → escalate, never post, priority 1.
4. Nothing clears both posting bars → suppress. *(Amended 2026-09-24; this rule
   was originally fifth, below the confidence test.)*
5. Overall confidence below `AUTO_POST_CONFIDENCE` → escalate.
6. Otherwise → post, but only findings above `FINDING_POST_CONFIDENCE`.

Two thresholds rather than one: the first governs the review, the second governs
each finding, so a confident review can still withhold its weakest observations
and say it did.

## Consequences

**Gained.** Every escalation has a machine-readable reason and a priority, so the
queue sorts itself — a critical security finding sits above a low-confidence
review, always. Rules 1 and 2 mean an incomplete review is never presented as a
complete one. Rule 3 makes responsible disclosure structural rather than a policy
document nobody reads.

**Given up.** Reviews that would have been fine get escalated because one agent
timed out, which costs human attention — the resource this system exists to
protect. Rule 3 in particular means the findings with the highest value are the
ones that never get posted automatically, which reads as a weakness in a demo and
is the correct behaviour in production.

**Thresholds are guesses.** 0.70 and 0.60 are starting points, not measurements.
The escalation rate on the dashboard is the feedback signal: 90% means the model
is not calibrated or the thresholds are too high; 2% probably means the model is
not being honest about its uncertainty.

## Amendment, 2026-09-22 — what counts as `critical`

Six consecutive eval runs showed wildcard CORS with credentials being found every
time and rated `major`, so the never-post rule never fired and the review was
posted publicly. Two readings were defensible: the model was under-rating a
genuine critical, or the band was right and auto-posting was correct.

**Decided: it is critical.** A wildcard origin combined with credentials lets any
site on the internet read authenticated responses. That the attack needs a victim
to visit a page is the attacker's problem, not a reason to discount what they get.

The guidance that follows from it is deliberately about the principle rather than
about CORS, because a rule naming one misconfiguration teaches nothing about the
next one:

> Rate by what is at stake if you are right, not by how many steps someone would
> have to take to get there.

and, for security specifically, `critical` covers a change that removes, weakens
or bypasses a control — an authorisation check no longer applied, an allowlist
widened to admit anything, verification switched off, a credential exposed —
whether or not an end-to-end exploit is demonstrated. "The control is off" is more
serious than "the control has a bug in it", not less. `major` keeps the defects
that make an attack easier without themselves granting access: a weak hash behind
a strong one, a missing rate limit, a timing side channel.

Measured over three runs per arm: the case moved to `critical` and `escalate` in
all three, precision and recall held, the four negative controls stayed silent,
and escalations across the set went from 8.0 to 9.7 of 34 cases — within noise at
n=3, but in the direction you would expect. **That number is the cost of this
decision.** Broadening `critical` spends human queue capacity, which is the
resource the whole system exists to protect, so it is worth watching as the corpus
grows rather than assuming it stays small.

## What would make this wrong

A private repository with a security team already in the loop might reasonably
post critical findings directly — `ESCALATE_CRITICAL_SECURITY=false` exists for
that, and it defaults to on because the failure is asymmetric.

## Amendment, 2026-09-24 — suppress is checked before low confidence

The original order tested "overall confidence is low" before "nothing is worth
posting". That made the gate behave backwards on trivia:

- a **confident** trivial finding suppressed, and
- an **uncertain** trivial finding escalated.

Low overall confidence is the *normal* state of a review that found only weak
signals, so the effect was to spend a human on the pull requests with least to
say — precisely the noise failure rules 1 through 5 exist to prevent. It surfaced
on `neg-allowlisted-dynamic-sql`: one test-coverage remark at confidence 0.55,
nothing clearing the posting bars, and a human summoned to look at it.

The principle is that escalation *routes a finding to a person*. With nothing that
clears the bars there is nothing to route, and escalating asserts "look at this"
about no content. Suppress is therefore checked first.

**This is adopted on the argument, not on a measurement.** Every recorded gate
decision was recomputed offline under four candidate orderings — free, and it
reproduces the live gate exactly — and the reorder changes one decision in one run
of three. The eval does not demonstrate it and is not claimed to.

Note also what the reorder does *not* fix. `neg-allowlisted-dynamic-sql` merely
moves from `escalate` to `auto_post`, because no defensible gate stays silent
about a finding the eval's own `PERMITTED_CONCERNS` policy declares legitimate
everywhere. That case was failing because the fixture was wrong — it guarded both
`sort` and `direction` while testing only `sort`, so "no test rejects an invalid
direction" was simply true. The gate metric had been reporting a broken fixture,
not a broken gate.

The unit test covering rule 5 was asserting two things at once, using a finding
that was itself below the posting bar; it is now split into the two behaviours it
was conflating.

## Amendment, 2026-09-24 — a merged finding keeps every contributor's category

Rule 3 used to read only the primary `category` and the primary agent. The
aggregator keeps the most severe wording, so a security specialist's `authz`
can lose the tie to a correctness specialist's `logic` while still being
recorded on `categories`. Measured on `sec-terraform-public-bucket`: the
invoices bucket was critical, confidence 0.99, `categories` contained `authz`,
and the review auto-posted. That is the disclosure rule 3 exists to stop.

`is_security` is now true when any contributor's category is a security
category, not only the one that won the merge. A critical finding that is only
logic, from only the correctness agent, still posts.
