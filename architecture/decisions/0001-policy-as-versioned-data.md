# ADR-0001: Policy is versioned, immutable data

**Status:** Accepted

## Context

Governance rules change more often than the systems that enforce them. Approval thresholds, sensitivity definitions, and agent boundaries are adjusted by governance owners, not by engineers.

If rules live in control plane code, every governance change becomes a deployment, governance owners cannot own their own policy, and a decision made six months ago cannot be explained because the code that produced it has since been rewritten.

## Decision

Governance rules are expressed as versioned policy bundles evaluated at runtime. Bundles are immutable once published; a change produces a new version. A request pins a bundle version when its context is resolved and evaluates against that version for its entire lifetime.

Publishing a bundle is itself a governed request, classified as `governance_change` and routed through approval.

## Consequences

A past decision can be reconstructed exactly, because the bundle that produced it still exists in the form it had. Governance ownership separates cleanly from control plane engineering.

The cost is that every published bundle is retained indefinitely, and long-lived requests may execute against a bundle that is no longer current. That is accepted: consistency within a request matters more than currency, and a request whose rules changed mid-flight is re-evaluated rather than allowed to straddle two versions.
