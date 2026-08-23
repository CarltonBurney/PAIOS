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
    RiskDomain,
    RiskLevel,
)
from paios.audit import AuditStage
from paios.providers.mock import MockProvider


def make_request(content: str, **identity_kwargs) -> Request:
    defaults = {"subject": "alice@contoso.com", "authenticated": True}
    defaults.update(identity_kwargs)
    roles = defaults.pop("roles", frozenset())
    return Request(
        content=content, identity=Identity(roles=frozenset(roles), **defaults)
    )


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
        cp = ControlPlane(provider=MockProvider("summary text"))
        outcome = cp.handle(make_request("what did the meeting cover"))

        assert outcome.delivered
        assert outcome.execution.output == "summary text"
        assert outcome.routing.disposition is Disposition.AUTO_EXECUTE
        assert outcome.approval is None

    def test_audit_trail_covers_every_stage(self):
        sink = InMemoryAuditSink()
        cp = ControlPlane(audit_sink=sink)

        cp.handle(make_request("what did the meeting cover"))

        stages = sink.stages()
        for expected in (
            AuditStage.RECEIVED,
            AuditStage.IDENTITY_CHECKED,
            AuditStage.CLASSIFIED,
            AuditStage.RISK_ASSESSED,
            AuditStage.AUTHORIZED,
            AuditStage.POLICY_EVALUATED,
            AuditStage.ROUTED,
            AuditStage.EXECUTED,
            AuditStage.OUTPUT_REVIEWED,
        ):
            assert expected in stages, f"missing audit stage {expected}"

    def test_all_events_share_one_correlation_id(self):
        sink = InMemoryAuditSink()
        cp = ControlPlane(audit_sink=sink)
        cp.handle(make_request("what did the meeting cover"))
        assert len({e.correlation_id for e in sink.events}) == 1


class TestRiskSerialization:
    def test_assessment_serializes_to_the_canonical_shape(self):
        cp = ControlPlane(approval_handler=approve())
        outcome = cp.handle(
            make_request("assess GDPR obligations for the access control model")
        )

        payload = outcome.risk.to_dict()
        assert set(payload) == {"risk_level", "risk_domains"}
        assert payload["risk_level"] == "L3"
        assert payload["risk_domains"] == ["compliance", "security"]

    def test_audit_record_carries_both_axes(self):
        sink = InMemoryAuditSink()
        cp = ControlPlane(audit_sink=sink, approval_handler=approve())
        cp.handle(make_request("assess GDPR obligations for the access control model"))

        assessed = [e for e in sink.events if e.stage is AuditStage.RISK_ASSESSED]
        assert assessed[0].detail["risk_level"] == "L3"
        assert "security" in assessed[0].detail["risk_domains"]


class TestRiskAxes:
    def test_level_and_domains_are_independent(self):
        """A request can sit at L3 while carrying several domains."""
        cp = ControlPlane(approval_handler=approve())
        outcome = cp.handle(
            make_request("update the payroll record for employee ssn 123-45-6789")
        )

        assert outcome.risk.level is RiskLevel.L3
        assert RiskDomain.PRIVACY in outcome.risk.domains
        assert RiskDomain.FINANCIAL in outcome.risk.domains

    def test_level_escalates_to_the_highest_detector(self):
        cp = ControlPlane(approval_handler=approve())
        outcome = cp.handle(
            make_request("grant admin access and update the client record")
        )

        assert outcome.risk.level is RiskLevel.L4

    def test_domains_accumulate_rather_than_replace(self):
        cp = ControlPlane(approval_handler=approve())
        outcome = cp.handle(
            make_request("revoke the api-key and review the GDPR contract terms")
        )

        assert RiskDomain.SECURITY in outcome.risk.domains
        assert RiskDomain.COMPLIANCE in outcome.risk.domains

    def test_routine_read_stays_at_the_floor(self):
        cp = ControlPlane()
        outcome = cp.handle(make_request("analyze the quarterly research findings"))
        assert outcome.risk.level is RiskLevel.L0


class TestAuthorization:
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

    def test_governance_change_requires_a_governance_role(self):
        cp = ControlPlane(approval_handler=approve())
        outcome = cp.handle(make_request("change the approval workflow policy"))

        assert outcome.classification.request_type is RequestType.GOVERNANCE_CHANGE
        assert outcome.blocked
        assert outcome.violations

    def test_governance_admin_still_needs_human_approval(self):
        cp = ControlPlane(approval_handler=approve())
        outcome = cp.handle(
            make_request(
                "change the approval workflow policy", roles={"governance_admin"}
            )
        )

        assert not outcome.blocked
        assert outcome.approval.state is ApprovalState.APPROVED

    def test_bypass_attempt_is_a_governance_change(self):
        cp = ControlPlane()
        outcome = cp.handle(make_request("bypass the approval gate for this one"))
        assert outcome.classification.request_type is RequestType.GOVERNANCE_CHANGE
        assert not outcome.delivered


class TestPolicyEngine:
    def test_security_policy_matches_in_prod_only(self):
        """PAIOS-SEC-001 is scoped to prod; dev must not match it."""
        content = "grant admin permissions to the contractor"

        prod = ControlPlane(environment="prod", approval_handler=approve())
        dev = ControlPlane(environment="dev", approval_handler=approve())

        assert "PAIOS-SEC-001" in prod.handle(make_request(content)).policy.matched
        assert "PAIOS-SEC-001" not in dev.handle(make_request(content)).policy.matched

    def test_matching_policy_raises_audit_level_to_full(self):
        cp = ControlPlane(environment="prod", approval_handler=approve())
        outcome = cp.handle(make_request("grant admin permissions to the contractor"))
        assert outcome.policy.audit_level == "full"

    def test_denied_tool_is_refused(self):
        cp = ControlPlane(environment="prod", approval_handler=approve())
        outcome = cp.handle(make_request("grant admin permissions to the contractor"))

        assert not outcome.policy.permits_tool("security_write")
        assert outcome.policy.permits_tool("security_read")

    def test_allow_list_excludes_unlisted_tools(self):
        cp = ControlPlane(environment="prod", approval_handler=approve())
        outcome = cp.handle(make_request("grant admin permissions to the contractor"))
        assert not outcome.policy.permits_tool("some_other_tool")

    def test_policy_can_force_approval(self):
        cp = ControlPlane(environment="prod", approval_handler=approve())
        outcome = cp.handle(make_request("grant admin permissions to the contractor"))

        assert outcome.policy.require_approval
        assert outcome.routing.requires_human

    def test_baseline_policy_always_matches(self):
        cp = ControlPlane()
        outcome = cp.handle(make_request("what did the meeting cover"))
        assert "PAIOS-BASE-001" in outcome.policy.matched

    def test_unmatched_domain_does_not_apply_effects(self):
        cp = ControlPlane(environment="prod")
        outcome = cp.handle(make_request("what did the meeting cover"))
        assert "PAIOS-SEC-001" not in outcome.policy.matched
        assert outcome.policy.permits_tool("anything")


class TestApprovalGate:
    def test_rejected_approval_blocks_execution(self):
        provider = MockProvider()
        cp = ControlPlane(provider=provider, approval_handler=reject())
        outcome = cp.handle(make_request("update the client payroll record"))

        assert not outcome.delivered
        assert provider.calls == []

    def test_missing_approval_handler_denies_by_default(self):
        provider = MockProvider()
        cp = ControlPlane(provider=provider)
        outcome = cp.handle(make_request("update the client salary record"))

        assert outcome.approval.state is ApprovalState.TIMED_OUT
        assert not outcome.delivered
        assert provider.calls == []

    def test_approver_identity_is_recorded_in_audit(self):
        sink = InMemoryAuditSink()
        cp = ControlPlane(
            audit_sink=sink, approval_handler=approve("boss@contoso.com")
        )
        cp.handle(make_request("update the client payroll record"))

        decided = [e for e in sink.events if e.stage is AuditStage.APPROVAL_DECIDED]
        assert decided[0].detail["approver"] == "boss@contoso.com"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("the project timeline and milestones slipped", RequestType.PROJECT),
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
