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

## What would make this wrong

A private repository with a security team already in the loop might reasonably
post critical findings directly — `ESCALATE_CRITICAL_SECURITY=false` exists for
that, and it defaults to on because the failure is asymmetric.
