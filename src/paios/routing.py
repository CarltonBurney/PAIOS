"""Routing and authorization.

Two distinct things happen here, and the order matters:

1. **Authorization** — is this caller allowed to make this request at all?
   Failures here block outright. Authorization is identity-based and is
   deliberately *not* expressed in the policy engine: policy decides what a
   permitted request may do, authorization decides whether it is permitted.

2. **Disposition** — given the risk level and the merged policy decision, does
   this execute, wait for a human, or escalate?

The level-to-disposition mapping lives in policies/risk-model.json, so the
routing table is configuration rather than a table in this file.
"""

from __future__ import annotations

from .models import (
    Agent,
    Classification,
    Disposition,
    PolicyViolation,
    Request,
    RequestType,
    RiskAssessment,
    RoutingDecision,
)
from .policy import PolicyDecision
from .risk import RiskModel

DEFAULT_AGENTS: dict[RequestType, Agent] = {
    RequestType.PROJECT: Agent.PROJECT,
    RequestType.TECHNICAL: Agent.TECHNICAL,
    RequestType.LOGICAL: Agent.LOGICAL,
    RequestType.CORE: Agent.CORE,
    # A governance change has no execution agent — it is a decision, not a task.
    RequestType.GOVERNANCE_CHANGE: Agent.CORE,
}

GOVERNANCE_ROLES = frozenset({"governance_admin", "admin"})


def candidate_agent(classification: Classification) -> Agent:
    """The agent a request would run on, resolved before policy evaluation."""
    return DEFAULT_AGENTS[classification.request_type]


def authorize(
    request: Request,
    classification: Classification,
) -> tuple[PolicyViolation, ...]:
    """Identity-based checks. Any failure blocks the request."""
    failures: list[PolicyViolation] = []
    identity = request.identity

    if not identity.authenticated:
        failures.append(
            PolicyViolation(
                policy_id="AUTHZ-001",
                control="Caller must be authenticated",
                detail="unauthenticated identity",
            )
        )

    if classification.request_type is RequestType.GOVERNANCE_CHANGE:
        if not (identity.roles & GOVERNANCE_ROLES):
            failures.append(
                PolicyViolation(
                    policy_id="AUTHZ-002",
                    control="Governance changes require a governance role",
                    detail=(
                        f"identity '{identity.subject}' lacks a governance role "
                        "required to request a governance change"
                    ),
                )
            )

    return tuple(failures)


class Router:
    def __init__(self, risk_model: RiskModel | None = None) -> None:
        if risk_model is None:
            from .config import DEFAULT_RISK_MODEL_PATH

            risk_model = RiskModel.from_file(DEFAULT_RISK_MODEL_PATH)
        self.risk_model = risk_model

    def route(
        self,
        request: Request,
        classification: Classification,
        risk: RiskAssessment,
        violations: tuple[PolicyViolation, ...] = (),
        policy: PolicyDecision | None = None,
    ) -> RoutingDecision:
        agent = candidate_agent(classification)

        if violations:
            detail = "; ".join(v.detail for v in violations)
            return RoutingDecision(
                disposition=Disposition.BLOCKED,
                agent=None,
                reason=f"authorization failed: {detail}",
            )

        configured = self.risk_model.disposition_for(risk.level)
        disposition = Disposition(configured)

        # A governance change always reaches a human, whatever the risk maths say.
        if classification.request_type is RequestType.GOVERNANCE_CHANGE:
            if disposition is Disposition.AUTO_EXECUTE:
                disposition = Disposition.HUMAN_REVIEW

        # Policy can demand approval for something risk alone would auto-run.
        if policy is not None and policy.require_approval:
            if disposition is Disposition.AUTO_EXECUTE:
                disposition = Disposition.HUMAN_REVIEW

        reason = f"risk {risk.level.value}"
        if risk.domains:
            reason += f" [{', '.join(sorted(d.value for d in risk.domains))}]"
        if policy is not None and policy.matched:
            reason += f"; policies {', '.join(policy.matched)}"

        return RoutingDecision(
            disposition=disposition,
            agent=agent,
            reason=reason,
        )
