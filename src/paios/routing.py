"""Routing and authorization.

**Authorization** answers *is this identity entitled to attempt this class of
operation?* It runs before classification and is identity-only.

**Policy** answers *may this specific, otherwise-authorized request proceed
under current governance conditions?* It runs after risk assessment and can
return DENY.

Both layers are required and neither subsumes the other. An authenticated,
properly authorized administrator can still submit a request that policy must
refuse — a prohibited L4 operation, an export violating a data-handling rule,
an operation forbidden in production.

**Disposition** is the routing verdict: execute, wait for a human, escalate, or
block. The level-to-disposition mapping lives in policies/risk-model.json, so
the routing table is configuration rather than a table in this file.

A disposition is not permission to execute. It selects a path; the Execution
Gateway independently decides whether any given tool call may proceed.
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


def candidate_agent(classification: Classification) -> Agent:
    """The agent a request would run on, resolved before policy evaluation."""
    return DEFAULT_AGENTS[classification.request_type]


def authorize(request: Request) -> tuple[PolicyViolation, ...]:
    """Identity-level entitlement. Runs BEFORE classification.

    Answers only: *is this identity entitled to attempt this class of
    operation?* It knows nothing about what the request turns out to be, which
    is why it runs first and stays cheap.

    Context-dependent refusal — "this otherwise-authorized request may not
    proceed under current governance conditions" — is the policy engine's job
    and produces PolicyOutcome.DENY. The two layers answer different questions
    and both are required: an authorized administrator can still submit a
    request that policy must refuse.
    """
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

        # A policy deny is final and outranks everything below it. Approval
        # cannot override it and neither can a low risk level.
        if policy is not None and policy.denied:
            codes = ", ".join(policy.reason_codes) or "policy denied"
            return RoutingDecision(
                disposition=Disposition.BLOCKED,
                agent=None,
                reason=f"policy denied: {codes}",
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
