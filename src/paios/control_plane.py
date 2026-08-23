"""The control plane pipeline.

Implements the flow documented in docs/PAIOS_CONTROL_PLANE.md:

    Request -> Identity -> Classification -> Risk -> Policy -> Routing
            -> Approval (when required) -> Execution -> Output review -> Audit

Every stage emits an audit event before the next stage runs, so a request that
fails midway still leaves a complete trail up to the point of failure.
"""

from __future__ import annotations

from collections.abc import Callable

from .audit import AuditSink, AuditStage, AuditTrail, InMemoryAuditSink
from .classification import Classifier, RuleClassifier
from .models import (
    Approval,
    ApprovalState,
    Disposition,
    Execution,
    Outcome,
    Request,
)
from .policy import PolicyEngine, PolicySet
from .providers.base import ModelProvider, ProviderError
from .providers.mock import MockProvider
from .risk import RiskEngine
from .routing import Router, authorize, candidate_agent

# An approval handler receives the in-progress outcome and returns a decision.
# In a deployment this is a Teams adaptive card, a Power Automate flow, or a
# queue a human drains — never an automatic yes.
ApprovalHandler = Callable[[Outcome], Approval]


def deny_by_default(_: Outcome) -> Approval:
    """Default handler. No human present means no approval."""
    return Approval(
        state=ApprovalState.TIMED_OUT,
        note="no approval handler configured; denied by default",
    )


class ControlPlane:
    def __init__(
        self,
        *,
        provider: ModelProvider | None = None,
        policy_engine: PolicyEngine | None = None,
        classifier: Classifier | None = None,
        risk_engine: RiskEngine | None = None,
        router: Router | None = None,
        audit_sink: AuditSink | None = None,
        approval_handler: ApprovalHandler | None = None,
        environment: str = "dev",
    ) -> None:
        self.environment = environment
        self.provider = provider or MockProvider()
        self.classifier = classifier or RuleClassifier()
        self.risk_engine = risk_engine or RiskEngine()
        self.router = router or Router()
        self.audit_sink = audit_sink or InMemoryAuditSink()
        self.approval_handler = approval_handler or deny_by_default

        if policy_engine is None:
            from .config import DEFAULT_POLICY_PATH

            policy_engine = PolicyEngine(PolicySet.from_file(DEFAULT_POLICY_PATH))
        self.policy_engine = policy_engine

    def handle(self, request: Request, correlation_id: str | None = None) -> Outcome:
        trail = AuditTrail(self.audit_sink, correlation_id)
        subject = request.identity.subject

        trail.emit(
            request.id,
            AuditStage.RECEIVED,
            subject,
            content_length=len(request.content),
        )

        trail.emit(
            request.id,
            AuditStage.IDENTITY_CHECKED,
            subject,
            authenticated=request.identity.authenticated,
            roles=sorted(request.identity.roles),
        )

        violations = authorize(request)
        trail.emit(
            request.id,
            AuditStage.AUTHORIZED,
            subject,
            failure_count=len(violations),
            failures=[v.detail for v in violations],
        )

        classification = self.classifier.classify(request)
        trail.emit(
            request.id,
            AuditStage.CLASSIFIED,
            subject,
            request_type=classification.request_type.value,
            confidence=classification.confidence,
            signals=list(classification.signals),
        )

        risk = self.risk_engine.assess(request, classification)
        trail.emit(
            request.id,
            AuditStage.RISK_ASSESSED,
            subject,
            **risk.to_dict(),
            triggers=list(risk.triggers),
        )

        agent = candidate_agent(classification)
        policy = self.policy_engine.evaluate(
            request,
            classification,
            risk,
            agent=agent,
            environment=self.environment,
        )
        trail.emit(
            request.id,
            AuditStage.POLICY_EVALUATED,
            subject,
            **policy.to_dict(),
        )

        routing = self.router.route(
            request, classification, risk, violations, policy
        )
        trail.emit(
            request.id,
            AuditStage.ROUTED,
            subject,
            disposition=routing.disposition.value,
            agent=routing.agent.value if routing.agent else None,
            reason=routing.reason,
        )

        outcome = Outcome(
            request=request,
            classification=classification,
            risk=risk,
            routing=routing,
            violations=violations,
            policy=policy,
        )

        if routing.disposition is Disposition.BLOCKED:
            trail.emit(
                request.id, AuditStage.BLOCKED, subject, reason=routing.reason
            )
            return self._finalise(outcome, trail)

        approval: Approval | None = None
        if routing.requires_human:
            trail.emit(
                request.id,
                AuditStage.APPROVAL_REQUESTED,
                subject,
                disposition=routing.disposition.value,
            )
            approval = self.approval_handler(outcome)
            trail.emit(
                request.id,
                AuditStage.APPROVAL_DECIDED,
                subject,
                state=approval.state.value,
                approver=approval.approver,
                note=approval.note,
            )
            outcome = _replace(outcome, approval=approval)

            if approval.state is not ApprovalState.APPROVED:
                trail.emit(
                    request.id,
                    AuditStage.BLOCKED,
                    subject,
                    reason=f"approval {approval.state.value}",
                )
                return self._finalise(outcome, trail)

        assert routing.agent is not None
        try:
            output = self.provider.complete(request.content)
        except ProviderError as exc:
            trail.emit(request.id, AuditStage.ERROR, subject, error=str(exc))
            return self._finalise(outcome, trail)

        execution = Execution(
            output=output,
            agent=routing.agent,
            provider=self.provider.info.name,
            model=self.provider.info.model,
        )
        trail.emit(
            request.id,
            AuditStage.EXECUTED,
            subject,
            agent=routing.agent.value,
            provider=execution.provider,
            model=execution.model,
            output_length=len(output),
        )
        trail.emit(request.id, AuditStage.OUTPUT_REVIEWED, subject, reviewed=True)

        outcome = _replace(outcome, execution=execution)
        return self._finalise(outcome, trail)

    @staticmethod
    def _finalise(outcome: Outcome, trail: AuditTrail) -> Outcome:
        return _replace(outcome, audit_ids=trail.event_ids)


def _replace(outcome: Outcome, **changes: object) -> Outcome:
    import dataclasses

    return dataclasses.replace(outcome, **changes)  # type: ignore[arg-type]
