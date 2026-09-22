# ADR-0007 — Feedback is recorded but never acted on alone

**Status:** Accepted · 2026-09-22

## Context

The obvious loop is: developer disputes a finding, system learns not to raise it
again. The obvious loop is also how a reviewer gets quietly lobotomised. A single
dispute carries almost no information about whether a finding was wrong. It might
mean the finding was wrong. It might mean the developer disagreed, was in a hurry,
did not understand it, or did not want to fix it before a deadline. A junior
engineer dismissing a real authorisation finding is indistinguishable, at the data
layer, from a senior engineer correcting a false positive.

## Decision

Every human decision — gate approvals, gate disputes, PR replies — is recorded in
`feedback` with its source and actor. Nothing reads that table to change behaviour
yet. When something does, it will be behind a minimum-evidence rule: a pattern
needs several independent disputes, from different actors, on findings that are
actually similar, before it may suppress anything — and suppression will be
surfaced rather than silent.

Recorded alongside it, and deliberately not used yet, is decay: guidance that
stops being reinforced should fade rather than accumulate forever.

## Consequences

**Gained.** The data is being collected from day one, so the loop can be built on
real history rather than on a fresh start. A dispute is already useful today as a
metric — a rising dispute rate for one agent is the clearest signal that its
prompt or its model is wrong.

**Given up.** The system does not currently improve from use, which is the feature
people most expect. That is a deliberate trade: an unsafe learning loop is worse
than none, because its failures are silent and compound.

## What would make this wrong

Nothing about the principle. But if dispute volume stays low enough that a
minimum-evidence threshold is never met, the loop is theatre and the honest move
is a curated `conventions` table maintained by humans — which already exists, is
injected into every prompt, and is the boring answer that probably wins.
