# Architecture Decision Records

Each record captures one structural decision in the PAIOS design: the context that forced a choice, the decision, and what it costs.

Records are immutable once accepted. A decision that changes is superseded by a new record rather than edited, for the same reason policy bundles are versioned rather than modified — the reasoning behind a past state has to remain recoverable.

| ID | Decision | Status |
|----|----------|--------|
| [0001](0001-policy-as-versioned-data.md) | Policy is versioned, immutable data | Accepted |
| [0002](0002-deterministic-gate.md) | The decision gate is deterministic and separate from classification | Accepted |
| [0003](0003-fail-closed.md) | Degraded components fail closed to human review | Accepted |
| [0004](0004-audit-before-execute.md) | Audit is written before execution and is append-only | Accepted |
| [0005](0005-binder-enforcement.md) | Agent constraints are enforced at the binder, not in prompts | Accepted |
