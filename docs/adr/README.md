# Architecture decision records

One file per decision that would be expensive to reverse. Each records what was
decided, what it cost, and what would make it wrong — that last part is what makes
an ADR worth writing rather than a paragraph in a design doc.

| | Decision | Status |
|---|---|---|
| [0001](0001-queue-between-ingress-and-review.md) | A queue between ingress and review | Accepted |
| [0002](0002-one-postgres-for-three-data-shapes.md) | One Postgres for all three data shapes | Accepted |
| [0003](0003-raw-sql-over-an-orm.md) | Raw SQL over an ORM | Accepted |
| [0004](0004-orchestration-behind-an-interface.md) | Orchestration behind an interface, with two implementations | Accepted |
| [0005](0005-per-agent-model-routing.md) | A provider seam and per-agent model routing | Accepted |
| [0006](0006-confidence-gate-and-security-escalation.md) | The confidence gate, and never posting critical security findings | Accepted |
| [0007](0007-feedback-requires-minimum-evidence.md) | Feedback is recorded but never acted on alone | Accepted |
| [0008](0008-genesis-kit-is-not-a-dependency.md) | Genesis Kit is not a dependency; three of its diagnoses are | Accepted |
