# ADR-0004: Audit is written before execution and is append-only

**Status:** Accepted

## Context

Audit written after the fact records what a system believes happened. If execution succeeds and the audit write then fails, the action exists and the record does not — precisely the case an audit trail is meant to cover.

Mutable audit has the same weakness in a different form: a record that can be corrected in place cannot prove it was not.

## Decision

An execution record is committed to the audit sink before the model or agent is invoked. A failure to write audit is a failure to execute — an unavailable sink is a hard stop, not a degraded mode.

The sink accepts no updates and no deletes. A correction is a new record carrying a `corrects` reference to the record it amends.

Audit records outlive the requests they describe. Deleting a request under a records policy must not delete the record that it occurred.

## Consequences

There is no execution the audit trail does not know about, and the history of any request is complete including its corrections.

The costs are real: audit sink availability becomes a hard dependency of the whole control plane, and storage grows monotonically. Both are accepted, since the property being bought — that the record cannot be quietly wrong — is the one the framework exists to provide.
