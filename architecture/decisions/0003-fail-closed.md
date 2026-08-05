# ADR-0003: Degraded components fail closed to human review

**Status:** Accepted

## Context

A governance layer that stops governing under load is not a governance layer. The failure that matters is not the classifier timing out — it is the request that proceeds ungoverned because the classifier timed out and nothing observed the gap.

The tempting default is to treat a missing signal as a benign one: no sensitivity signal found, therefore low risk. That inverts the intent of the control.

## Decision

Absence of a signal is never treated as absence of risk. Any component that cannot complete its stage transitions the request to `AWAITING_APPROVAL` with an obligation naming the failed stage.

Two failures are handled differently. An unreadable policy bundle halts intake rather than escalating, because there is no authority under which to evaluate anything, and falling back to a cached or default bundle would mean governing against rules nobody published. An unresolved identity is denied rather than escalated, because an unverified principal cannot meaningfully be presented to an approver.

Approval timeouts escalate. They never auto-approve, and they never auto-deny — either would discard the accountability the gate exists to create.

## Consequences

Degraded operation always produces more human review, never less, so an outage raises reviewer load rather than lowering assurance. Sustained degradation is visible as review volume instead of being silently absorbed.

The cost is that a flaky dependency is felt directly by reviewers. That is the intended trade: the alternative moves the cost to the audit trail, where it surfaces much later and at much higher price.
