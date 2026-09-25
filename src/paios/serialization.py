"""Canonical JSON shapes for control plane results.

The HTTP surface is not the only thing that will ever need to render an
``Outcome`` as JSON — an audit exporter, a queue worker, and a CLI all want the
same shape. Keeping the mapping here rather than inside the request handler
means there is exactly one answer to "what does a decision look like on the
wire", and it can be tested without a socket.

Two rules hold throughout:

* **Nothing here invents a value.** A stage the control plane never reached
  serializes as ``null``, never as a zero or an empty success. A caller must be
  able to tell "policy allowed this" from "policy never ran".
* **Nothing here emits a secret.** Identity carries a subject and role names,
  which are already recorded in the audit trail; tokens and credentials are not
  part of any shape below.
"""

from __future__ import annotations

from typing import Any

from .audit import AuditEvent
from .models import (
    Approval,
    Classification,
    Execution,
    Identity,
    Outcome,
    PolicyViolation,
    Request,
    RiskAssessment,
)


def identity_to_dict(identity: Identity) -> dict[str, Any]:
    return {
        "subject": identity.subject,
        "authenticated": identity.authenticated,
        "roles": sorted(identity.roles),
        "groups": sorted(identity.groups),
        "tenant_id": identity.tenant_id,
        "department": identity.department,
    }


def request_to_dict(request: Request) -> dict[str, Any]:
    return {
        "id": request.id,
        "content_length": len(request.content),
        "identity": identity_to_dict(request.identity),
        "metadata": dict(request.metadata),
        "received_at": request.received_at.isoformat(),
    }


def classification_to_dict(classification: Classification) -> dict[str, Any]:
    return {
        "request_type": classification.request_type.value,
        "confidence": classification.confidence,
        "signals": list(classification.signals),
    }


def risk_to_dict(risk: RiskAssessment) -> dict[str, Any]:
    return {
        "level": risk.level.value,
        "domains": sorted(d.value for d in risk.domains),
        "triggers": list(risk.triggers),
    }


def violation_to_dict(violation: PolicyViolation) -> dict[str, Any]:
    return {
        "policy_id": violation.policy_id,
        "control": violation.control,
        "detail": violation.detail,
    }


def approval_to_dict(approval: Approval) -> dict[str, Any]:
    return {
        "state": approval.state.value,
        "approver": approval.approver,
        "decided_at": (
            approval.decided_at.isoformat() if approval.decided_at else None
        ),
        "note": approval.note,
    }


def execution_to_dict(execution: Execution) -> dict[str, Any]:
    return {
        "output": execution.output,
        "agent": execution.agent.value,
        "provider": execution.provider,
        "model": execution.model,
        "completed_at": execution.completed_at.isoformat(),
    }


def outcome_to_dict(outcome: Outcome) -> dict[str, Any]:
    """The full record of one request's passage, as JSON.

    ``disposition`` is lifted to the top level because it is the one field a
    caller almost always wants first: it says whether the request ran, waits on
    a human, or was refused.
    """
    routing = outcome.routing
    return {
        "request": request_to_dict(outcome.request),
        "disposition": routing.disposition.value if routing else None,
        "delivered": outcome.delivered,
        "blocked": outcome.blocked,
        "classification": (
            classification_to_dict(outcome.classification)
            if outcome.classification
            else None
        ),
        "risk": risk_to_dict(outcome.risk) if outcome.risk else None,
        "policy": outcome.policy.to_dict() if outcome.policy else None,
        "routing": (
            {
                "disposition": routing.disposition.value,
                "agent": routing.agent.value if routing.agent else None,
                "reason": routing.reason,
                "requires_human": routing.requires_human,
            }
            if routing
            else None
        ),
        "violations": [violation_to_dict(v) for v in outcome.violations],
        "approval": (
            approval_to_dict(outcome.approval) if outcome.approval else None
        ),
        "execution": (
            execution_to_dict(outcome.execution) if outcome.execution else None
        ),
        "audit_ids": list(outcome.audit_ids),
    }


def audit_event_to_dict(event: AuditEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "correlation_id": event.correlation_id,
        "request_id": event.request_id,
        "stage": event.stage.value,
        "subject": event.subject,
        "detail": event.detail,
        "recorded_at": event.recorded_at.isoformat(),
    }
