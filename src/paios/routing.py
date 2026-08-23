"""Routing — turning a classification and a risk level into a disposition.

This is the table from request-classification-flow.md expressed as code:

    Low         -> auto-execute
    Standard    -> auto-execute with logging
    Sensitive   -> human review
    Compliance  -> human review + documentation
    Security    -> admin escalation

Policy violations and unauthenticated callers short-circuit to BLOCKED before
the risk table is consulted.
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
    RiskLevel,
    RoutingDecision,
)

DEFAULT_AGENTS: dict[RequestType, Agent] = {
    RequestType.PROJECT: Agent.PROJECT,
    RequestType.TECHNICAL: Agent.TECHNICAL,
    RequestType.LOGICAL: Agent.LOGICAL,
    RequestType.CORE: Agent.CORE,
    # A governance change has no execution agent — it is a decision, not a task.
    RequestType.GOVERNANCE_CHANGE: Agent.CORE,
}

_RISK_DISPOSITION: dict[RiskLevel, Disposition] = {
    RiskLevel.LOW: Disposition.AUTO_EXECUTE,
    RiskLevel.STANDARD: Disposition.AUTO_EXECUTE,
    RiskLevel.SENSITIVE: Disposition.HUMAN_REVIEW,
    RiskLevel.COMPLIANCE: Disposition.HUMAN_REVIEW,
    RiskLevel.SECURITY: Disposition.ADMIN_ESCALATION,
}


class Router:
    def route(
        self,
        request: Request,
        classification: Classification,
        risk: RiskAssessment,
        violations: tuple[PolicyViolation, ...] = (),
    ) -> RoutingDecision:
        agent = DEFAULT_AGENTS[classification.request_type]

        if violations:
            detail = "; ".join(v.detail for v in violations)
            return RoutingDecision(
                disposition=Disposition.BLOCKED,
                agent=None,
                reason=f"policy violation: {detail}",
            )

        if not request.identity.authenticated:
            return RoutingDecision(
                disposition=Disposition.BLOCKED,
                agent=None,
                reason="unauthenticated identity",
            )

        # A governance change always goes to a human, whatever the risk maths say.
        if classification.request_type is RequestType.GOVERNANCE_CHANGE:
            return RoutingDecision(
                disposition=Disposition.HUMAN_REVIEW,
                agent=agent,
                reason="governance changes require human approval",
            )

        disposition = _RISK_DISPOSITION[risk.level]
        return RoutingDecision(
            disposition=disposition,
            agent=agent,
            reason=f"risk level {risk.level.value}",
        )
