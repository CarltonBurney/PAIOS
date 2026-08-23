"""End-to-end tests for the control plane pipeline."""

from __future__ import annotations

import pytest

from paios import (
    Approval,
    ApprovalState,
    ControlPlane,
    Disposition,
    Identity,
    InMemoryAuditSink,
    Request,
    RequestType,
    RiskLevel,
)
from paios.audit import AuditStage
from paios.providers.mock import MockProvider


def make_request(content: str, **identity_kwargs) -> Request:
    defaults = {"subject": "alice@contoso.com", "authenticated": True}
    defaults.update(identity_kwargs)
    roles = defaults.pop("roles", frozenset())
    return Request(content=content, identity=Identity(roles=frozenset(roles), **defaults))


def approve(approver: str = "manager@contoso.com"):
    return lambda _outcome: Approval(
        state=ApprovalState.APPROVED, approver=approver, note="ok"
    )


def reject():
    return lambda _outcome: Approval(
        state=ApprovalState.REJECTED, approver="manager@contoso.com"
    )


class TestHappyPath:
    def test_low_risk_request_executes_without_approval(self):
        sink = InMemoryAuditSink()
        cp = ControlPlane(
            provider=MockProvider("summary text"), audit_sink=sink
        )

        outcome = cp.handle(make_request("summarize the meeting notes"))

        assert outcome.delivered
        assert outcome.execution is not None
        assert outcome.execution.output == "summary text"
        assert outcome.routing.disposition is Disposition.AUTO_EXECUTE
        assert outcome.approval is None

    def test_audit_trail_covers_every_stage(self):
        sink = InMemoryAuditSink()
        cp = ControlPlane(audit_sink=sink)

        cp.handle(make_request("draft a status report"))

        stages = sink.stages()
        for expected in (
            AuditStage.RECEIVED,
            AuditStage.IDENTITY_CHECKED,
            AuditStage.CLASSIFIED,
            AuditStage.RISK_ASSESSED,
            AuditStage.POLICY_EVALUATED,
            AuditStage.ROUTED,
            AuditStage.EXECUTED,
            AuditStage.OUTPUT_REVIEWED,
        ):
            assert expected in stages, f"missing audit stage {expected}"

    def test_all_events_share_one_correlation_id(self):
        sink = InMemoryAuditSink()
        cp = ControlPlane(audit_sink=sink)

        cp.handle(make_request("draft a status report"))

        ids = {e.correlation_id for e in sink.events}
        assert len(ids) == 1


class TestIdentityGate:
    def test_unauthenticated_request_is_blocked(self):
        cp = ControlPlane()
        outcome = cp.handle(make_request("summarize notes", authenticated=False))

        assert outcome.blocked
        assert not outcome.delivered

    def test_unauthenticated_request_never_reaches_the_model(self):
        provider = MockProvider()
        cp = ControlPlane(provider=provider)

        cp.handle(make_request("summarize notes", authenticated=False))

        assert provider.calls == []


class TestRiskEscalation:
    def test_pii_forces_human_review(self):
        cp = ControlPlane(approval_handler=approve())
        outcome = cp.handle(
            make_request("update the client record for bob@example.com")
        )

        assert outcome.risk.level >= RiskLevel.SENSITIVE
        assert outcome.approval is not None
        assert outcome.approval.state is ApprovalState.APPROVED

    def test_security_request_escalates_to_admin(self):
        cp = ControlPlane(approval_handler=approve())
        outcome = cp.handle(
            make_request("grant admin permissions to the contractor account")
        )

        assert outcome.risk.level is RiskLevel.SECURITY
        assert outcome.routing.disposition is Disposition.ADMIN_ESCALATION

    def test_compliance_keywords_escalate(self):
        cp = ControlPlane(approval_handler=approve())
        outcome = cp.handle(make_request("review the GDPR retention policy impact"))

        assert outcome.risk.level >= RiskLevel.COMPLIANCE

    def test_highest_detector_wins(self):
        """PII plus a permission change is SECURITY, not averaged down."""
        cp = ControlPlane(approval_handler=approve())
        outcome = cp.handle(
            make_request("revoke access for employee ssn 123-45-6789")
        )

        assert outcome.risk.level is RiskLevel.SECURITY


class TestApprovalGate:
    def test_rejected_approval_blocks_execution(self):
        provider = MockProvider()
        cp = ControlPlane(provider=provider, approval_handler=reject())

        outcome = cp.handle(make_request("update the client record for payroll"))

        assert not outcome.delivered
        assert provider.calls == []

    def test_missing_approval_handler_denies_by_default(self):
        provider = MockProvider()
        cp = ControlPlane(provider=provider)

        outcome = cp.handle(make_request("update the client salary record"))

        assert outcome.approval is not None
        assert outcome.approval.state is ApprovalState.TIMED_OUT
        assert not outcome.delivered
        assert provider.calls == []

    def test_approver_identity_is_recorded_in_audit(self):
        sink = InMemoryAuditSink()
        cp = ControlPlane(audit_sink=sink, approval_handler=approve("boss@contoso.com"))

        cp.handle(make_request("update the client record for payroll"))

        decided = [e for e in sink.events if e.stage is AuditStage.APPROVAL_DECIDED]
        assert decided
        assert decided[0].detail["approver"] == "boss@contoso.com"


class TestGovernanceChange:
    def test_governance_change_requires_governance_role(self):
        cp = ControlPlane(approval_handler=approve())
        outcome = cp.handle(make_request("change the approval workflow policy"))

        assert outcome.classification.request_type is RequestType.GOVERNANCE_CHANGE
        assert outcome.blocked
        assert outcome.violations

    def test_governance_admin_still_needs_human_approval(self):
        cp = ControlPlane(approval_handler=approve())
        outcome = cp.handle(
            make_request(
                "change the approval workflow policy",
                roles={"governance_admin"},
            )
        )

        assert not outcome.blocked
        assert outcome.approval is not None
        assert outcome.approval.state is ApprovalState.APPROVED

    def test_bypass_attempt_is_a_governance_change(self):
        cp = ControlPlane()
        outcome = cp.handle(make_request("bypass the approval gate for this one"))

        assert outcome.classification.request_type is RequestType.GOVERNANCE_CHANGE
        assert not outcome.delivered


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("update the project timeline and milestones", RequestType.PROJECT),
        ("the deployment pipeline throws an error", RequestType.TECHNICAL),
        ("analyze the tradeoffs and recommend an option", RequestType.LOGICAL),
        ("draft an email to the team", RequestType.CORE),
        ("modify the governance policy", RequestType.GOVERNANCE_CHANGE),
    ],
)
def test_classification_routes_to_expected_type(content, expected):
    cp = ControlPlane(approval_handler=approve())
    outcome = cp.handle(make_request(content, roles={"governance_admin"}))
    assert outcome.classification.request_type is expected
