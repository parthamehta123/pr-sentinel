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
4. Overall confidence below `AUTO_POST_CONFIDENCE` → escalate.
5. Otherwise → post, but only findings above `FINDING_POST_CONFIDENCE`.

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
