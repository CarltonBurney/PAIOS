# Approval Sequence

The end-to-end sequence for a request that requires human approval, showing where audit records are written and where the decision is enforced.

The critical ordering property is that the audit record precedes execution, and that the approval is bound to the policy bundle version the approver saw.

```mermaid
sequenceDiagram
    autonumber
    actor U as Requester
    participant ADP as Ingress Adapter
    participant CTX as Context Resolver
    participant CLS as Classifier
    participant PDP as Policy Decision Point
    participant APR as Approval Broker
    actor R as Approver
    participant BIND as Persona Binder
    participant RUN as Execution Adapter
    participant AUD as Audit Sink

    U->>ADP: Submit request
    Note over ADP: Governance fields in the<br/>payload are stripped
    ADP->>CTX: Normalized envelope
    CTX->>CTX: Resolve identity server-side
    CTX->>AUD: RECEIVED → CONTEXT_RESOLVED
    Note over CTX: Policy bundle version<br/>pinned here

    CTX->>CLS: Context record
    CLS->>CLS: Deterministic pass
    CLS->>CLS: Model-assisted pass (unmatched only)
    CLS->>AUD: CONTEXT_RESOLVED → CLASSIFIED

    CLS->>PDP: Classification + risk level
    PDP->>PDP: Collect matched rules,<br/>deny overrides
    PDP->>AUD: SCORED → DECIDED (effect, rule IDs, bundle version)

    PDP->>APR: require_approval + obligations
    APR->>AUD: DECIDED → AWAITING_APPROVAL
    APR->>R: Approval payload (approver role from obligations)

    alt Approved within timeout
        R->>APR: Approve
        APR->>APR: Verify pinned bundle unchanged
        APR->>AUD: Approval recorded (approver, timestamp, bundle version)
        APR->>BIND: Proceed with obligations
        BIND->>BIND: Apply data source and action<br/>restrictions to the adapter
        BIND->>AUD: AWAITING_APPROVAL → EXECUTING
        Note over AUD,RUN: Audit committed BEFORE invocation
        BIND->>RUN: Bound execution
        RUN->>AUD: Execution outcome + model identifier
        RUN-->>U: Output (after review and publication)
    else Rejected
        R->>APR: Reject
        APR->>AUD: AWAITING_APPROVAL → DENIED
        APR-->>U: Denied
    else Timeout
        APR->>AUD: AWAITING_APPROVAL → ESCALATED
        APR->>R: Escalate to administrator role
        Note over APR: Timeout never resolves to<br/>approved or denied
    else Bundle version changed while pending
        APR->>AUD: Approval invalidated
        APR->>PDP: Re-evaluate against current bundle
        Note over APR,PDP: Consent applies to the rules<br/>the approver saw
    end
```

## Enforcement points

| Step | Enforcement |
|------|-------------|
| Ingress | Caller-supplied classification, risk, and routing fields are rejected |
| Context resolution | Identity is resolved server-side; the bundle version is pinned |
| Decision | The single point where the governance outcome is determined |
| Approval | Bound to a decision and a bundle version; invalidated if either changes |
| Binder | Data source and action restrictions applied to the adapter, not the prompt |
| Audit | Written before invocation; an unavailable sink blocks execution |

Related: [`../DESIGN.md`](../DESIGN.md) §4 (lifecycle), §5 (policy evaluation), §9 (trust boundaries).
