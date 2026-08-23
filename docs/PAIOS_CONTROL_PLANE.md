# PAIOS Control Plane

## Overview

The PAIOS control plane is a programmable orchestration layer designed to coordinate AI requests, governance enforcement, knowledge management, and workflow automation within enterprise environments.

The control plane sits above individual AI models and below the human or business process requesting work. Its function is to structure how AI requests are classified, routed, governed, documented, and reused.

The framework is model-agnostic. The underlying model can change while the control layer and governance logic remain consistent.

## Core Principle

Every AI action should be:

1. Authorized against the caller's identity
2. Classified
3. Risk-assessed on impact and concern domain
4. Governed by declarative policy
5. Routed
6. Enforced at the execution boundary
7. Logged with a correlation ID
8. Reusable when appropriate
9. Escalated to a human when policy requires it

## The Enterprise Problem

Most AI implementations start with access to a model.

That is not sufficient for enterprise deployment.

Enterprise environments require:

- Access boundaries
- Approval workflows
- Auditability
- Role-specific behavior
- Knowledge ownership
- Documentation standards
- Governance over agents and automations
- Integration with existing systems

Without those controls, AI becomes another unmanaged tool layer operating outside organizational accountability structures.

## Control Plane Responsibilities

The control plane is designed around:

- Multi-agent orchestration
- Request classification
- Governance logic enforcement
- Human review path management
- Knowledge management and capture
- Workflow automation
- Microsoft 365 integration
- Model-agnostic routing

## Control Plane Flow

```text
Authentication
  ↓
Identity Resolution
  ↓
Authorization
  ↓
Classification
  ↓
Risk Assessment
  ↓
Policy Evaluation
  ↓
Approval Gate
  ↓
Tool / Workflow Execution
  ↓
Output Validation
  ↓
Audit
```

Authorization runs before classification because it is identity-only: it asks
whether this identity may attempt this class of operation at all, and does not
need to know what the request turns out to be. Contextual refusal happens later,
in policy evaluation.

## Risk Model

Risk is assessed on two independent axes:

```json
{ "risk_level": "L3", "risk_domains": ["security", "compliance"] }
```

- **`risk_level`** — ordered impact severity, `L0` through `L4`. How much
  damage could this do. Exactly one per request.
- **`risk_domains`** — non-exclusive kinds of concern: `security`,
  `compliance`, `privacy`, `financial`, `operational`, `governance`. What kind
  of concern is this. Zero or more per request.

> **Migration note.** Previous PAIOS revisions represented Low, Standard,
> Sensitive, Compliance, and Security within a single taxonomy. PAIOS now
> separates impact severity (L0–L4) from non-exclusive risk domains.

Risk levels and domains are defined in `policies/risk-model.json`. Detectors
escalate but never de-escalate.

**Risk never authorizes execution.** A request being `L0` or `L1` does not make
a tool permissible. Risk, identity and authorization, policy, tool
registration, and approval are separate inputs to the final execution decision.

## Governance Layers

| Layer | Question it answers |
|-------|--------------------|
| **Authorization** | Is this identity entitled to attempt this class of operation? |
| **Policy** | May this specific, otherwise-authorized request proceed under current governance conditions? |
| **Tool Registry** | Does this tool exist, and what does its contract require? |
| **Execution Gateway** | May this principal invoke this tool, right now, with these arguments? |

Each layer is independently sufficient to refuse and none can be skipped. A
policy decision is advisory until the Execution Gateway re-validates it — the
gateway does not trust that a caller consulted policy first.

Policy decisions resolve by precedence:

```
deny  >  require_approval  >  allow_with_controls  >  allow
```

Any applicable `deny` wins. Approval can never override a deny.

## Enterprise Value

The distinguishing value of the control plane is not the ability to call an AI model.

The value is the governance layer that determines how AI work moves through an organization — who can request work, what kind of work is permitted, when human approval is required, what gets logged, and how knowledge is preserved after work is completed.
