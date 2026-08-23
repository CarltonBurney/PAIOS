"""Policy-governed registry mutations (§3B).

Auditing a lifecycle change is not governing it. These tests prove the Registry
Service performs a state transition only after an allowing decision, that
approval requirements cannot be self-satisfied, and that a refused mutation is
itself audited.
"""

from __future__ import annotations

import pytest

from paios import Approval, ApprovalState, Owner, OwnerType
from paios.config import DEFAULT_REGISTRY_POLICY_PATH
from paios.registry import (
    MutationContext,
    RegistryDenied,
    RegistryOperation,
    RegistryType,
    ResourceStatus,
    Version,
    VersionBump,
)
from paios.registry_governance import (
    APPROVAL_REQUIRED,
    SEPARATION_OF_DUTIES,
    RegistryGovernance,
)
from paios.tools import ToolRegistry
from test_registry_lifecycle import tool

ADMIN = frozenset({"registry_admin", "platform_admin"})
APPROVED = Approval(state=ApprovalState.APPROVED, approver="boss@contoso.com")
SELF_APPROVED = Approval(state=ApprovalState.APPROVED, approver="alice@contoso.com")
REJECTED = Approval(state=ApprovalState.REJECTED, approver="boss@contoso.com")


@pytest.fixture
def governance() -> RegistryGovernance:
    return RegistryGovernance.from_file(DEFAULT_REGISTRY_POLICY_PATH)


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.service.register(
        tool(), principal="alice@contoso.com", principal_roles=ADMIN
    )
    return reg


def context(
    operation: RegistryOperation,
    *,
    principal: str = "alice@contoso.com",
    roles: frozenset[str] = frozenset(),
    target_environment: str | None = None,
    resource_environments: frozenset[str] = frozenset(),
    risk_level: str | None = None,
    approver: str | None = None,
    granted: bool = False,
) -> MutationContext:
    return MutationContext(
        registry_type=RegistryType.TOOL,
        resource_id="sample_tool",
        version=Version(1, 0, 0),
        operation=operation,
        principal=principal,
        principal_roles=roles,
        target_environment=target_environment,
        resource_environments=resource_environments,
        resource_status=ResourceStatus.ACTIVE,
        resource_risk_level=risk_level,
        approver=approver,
        approval_granted=granted,
    )


class TestGovernorDecisions:
    def test_non_production_promotion_is_allowed(self, governance):
        verdict = governance.evaluate(
            context(RegistryOperation.PROMOTE, target_environment="test")
        )
        assert verdict.allowed

    def test_production_promotion_requires_approval(self, governance):
        verdict = governance.evaluate(
            context(RegistryOperation.PROMOTE, target_environment="prod")
        )
        assert not verdict.allowed
        assert "PRODUCTION_PROMOTION_APPROVAL" in verdict.reason_codes
        assert APPROVAL_REQUIRED in verdict.reason_codes

    def test_production_promotion_proceeds_with_approval(self, governance):
        verdict = governance.evaluate(
            context(
                RegistryOperation.PROMOTE,
                target_environment="prod",
                approver="boss@contoso.com",
                granted=True,
            )
        )
        assert verdict.allowed

    def test_ownership_transfer_requires_approval(self, governance):
        verdict = governance.evaluate(context(RegistryOperation.TRANSFER_OWNERSHIP))
        assert not verdict.allowed
        assert "OWNERSHIP_TRANSFER_APPROVAL" in verdict.reason_codes

    def test_production_retirement_requires_elevated_role(self, governance):
        verdict = governance.evaluate(
            context(
                RegistryOperation.RETIRE,
                resource_environments=frozenset({"prod"}),
            )
        )
        assert not verdict.allowed
        assert (
            "PRODUCTION_RETIREMENT_ELEVATED_ROLE_REQUIRED" in verdict.reason_codes
        )

    def test_high_risk_production_promotion_requires_elevated_role(self, governance):
        verdict = governance.evaluate(
            context(
                RegistryOperation.PROMOTE,
                target_environment="prod",
                risk_level="L4",
                approver="boss@contoso.com",
                granted=True,
            )
        )
        assert not verdict.allowed
        assert (
            "HIGH_RISK_PROMOTION_ELEVATED_ROLE_REQUIRED" in verdict.reason_codes
        )

    def test_high_risk_promotion_allowed_for_elevated_role_with_approval(
        self, governance
    ):
        verdict = governance.evaluate(
            context(
                RegistryOperation.PROMOTE,
                target_environment="prod",
                risk_level="L4",
                roles=ADMIN,
                approver="boss@contoso.com",
                granted=True,
            )
        )
        assert verdict.allowed

    def test_deny_outranks_approval_on_registry_mutations(self, governance):
        """Same precedence as execution policy: approval cannot beat deny."""
        verdict = governance.evaluate(
            context(
                RegistryOperation.RETIRE,
                resource_environments=frozenset({"prod"}),
                approver="boss@contoso.com",
                granted=True,
            )
        )
        assert not verdict.allowed
        assert verdict.decision == "deny"

    def test_suspension_is_allowed_with_controls(self, governance):
        """Emergency control must not be gated behind approval."""
        verdict = governance.evaluate(context(RegistryOperation.SUSPEND))
        assert verdict.allowed
        assert verdict.decision == "allow_with_controls"


class TestSeparationOfDuties:
    def test_requester_cannot_approve_their_own_mutation(self, governance):
        verdict = governance.evaluate(
            context(
                RegistryOperation.PROMOTE,
                target_environment="prod",
                principal="alice@contoso.com",
                approver="alice@contoso.com",
                granted=True,
            )
        )
        assert not verdict.allowed
        assert SEPARATION_OF_DUTIES in verdict.reason_codes
        assert verdict.decision == "deny"

    def test_a_different_approver_satisfies_the_gate(self, governance):
        verdict = governance.evaluate(
            context(
                RegistryOperation.PROMOTE,
                target_environment="prod",
                principal="alice@contoso.com",
                approver="boss@contoso.com",
                granted=True,
            )
        )
        assert verdict.allowed


class TestServiceEnforcement:
    def test_service_refuses_ungoverned_production_promotion(self, registry):
        with pytest.raises(RegistryDenied) as exc:
            registry.service.promote(
                "sample_tool",
                "1.0.0",
                source_environment="test",
                target_environment="prod",
                requested_by="alice@contoso.com",
            )
        assert APPROVAL_REQUIRED in exc.value.verdict.reason_codes

    def test_state_does_not_change_when_governance_refuses(self, registry):
        with pytest.raises(RegistryDenied):
            registry.service.promote(
                "sample_tool",
                "1.0.0",
                source_environment="test",
                target_environment="prod",
                requested_by="alice@contoso.com",
            )
        after = registry.get("sample_tool")
        assert after.status is ResourceStatus.DRAFT
        assert "prod" not in after.metadata.environments

    def test_approved_production_promotion_succeeds(self, registry):
        promoted = registry.service.promote(
            "sample_tool",
            "1.0.0",
            source_environment="test",
            target_environment="prod",
            requested_by="alice@contoso.com",
            approval=APPROVED,
            principal_roles=ADMIN,
        )
        assert promoted.metadata.status is ResourceStatus.ACTIVE
        assert "prod" in promoted.metadata.environments

    def test_self_approved_promotion_is_refused_by_the_service(self, registry):
        with pytest.raises(RegistryDenied) as exc:
            registry.service.promote(
                "sample_tool",
                "1.0.0",
                source_environment="test",
                target_environment="prod",
                requested_by="alice@contoso.com",
                approval=SELF_APPROVED,
                principal_roles=ADMIN,
            )
        assert SEPARATION_OF_DUTIES in exc.value.verdict.reason_codes

    def test_rejected_approval_does_not_satisfy_the_gate(self, registry):
        with pytest.raises(RegistryDenied):
            registry.service.promote(
                "sample_tool",
                "1.0.0",
                source_environment="test",
                target_environment="prod",
                requested_by="alice@contoso.com",
                approval=REJECTED,
                principal_roles=ADMIN,
            )

    def test_ownership_transfer_is_refused_without_approval(self, registry):
        with pytest.raises(RegistryDenied):
            registry.service.transfer_ownership(
                "sample_tool",
                Owner(OwnerType.USER, "carlton@contoso.com"),
                principal="alice@contoso.com",
            )
        assert registry.get("sample_tool").owner == "platform"

    def test_high_risk_version_change_requires_approval(self):
        from paios.models import RiskLevel
        from paios.registry import GovernedResourceMetadata
        from paios.tools import ToolDefinition, ToolSpec

        reg = ToolRegistry()
        risky = ToolDefinition(
            metadata=GovernedResourceMetadata(
                registry_type=RegistryType.TOOL,
                resource_id="risky_tool",
                version=Version(1, 0, 0),
                owner=Owner(OwnerType.GROUP, "security"),
            ),
            spec=ToolSpec(risk_level=RiskLevel.L4, handler="paios.tools.risky"),
        )
        reg.service.register(risky, principal="alice@contoso.com")

        with pytest.raises(RegistryDenied):
            reg.service.create_version(
                "risky_tool", VersionBump.MAJOR, principal="alice@contoso.com"
            )

        created = reg.service.create_version(
            "risky_tool",
            VersionBump.MAJOR,
            principal="alice@contoso.com",
            approval=APPROVED,
        )
        assert str(created.metadata.version) == "2.0.0"


class TestRefusalIsAudited:
    def test_denied_mutation_emits_an_audit_event(self, registry):
        with pytest.raises(RegistryDenied):
            registry.service.promote(
                "sample_tool",
                "1.0.0",
                source_environment="test",
                target_environment="prod",
                requested_by="alice@contoso.com",
                trace_id="trace-deny",
            )

        events = registry.service.events_for("sample_tool")
        promote_events = [
            e for e in events if e.operation is RegistryOperation.PROMOTE
        ]
        assert promote_events, "a refused mutation must still be audited"
        assert promote_events[-1].trace_id == "trace-deny"
        assert promote_events[-1].environment == "prod"

    def test_refusal_does_not_advance_state_in_the_audit_record(self, registry):
        with pytest.raises(RegistryDenied):
            registry.service.promote(
                "sample_tool",
                "1.0.0",
                source_environment="test",
                target_environment="prod",
                requested_by="alice@contoso.com",
            )
        event = registry.service.events_for("sample_tool")[-1]
        assert event.previous_state is event.new_state is ResourceStatus.DRAFT


class TestUngovernedIsExplicit:
    def test_ungoverned_registry_records_the_absence_of_governance(self):
        """An ungoverned registry is audited but not policy-gated.

        The verdict says `ungoverned` rather than `allow`, so the audit trail
        distinguishes "policy permitted this" from "nothing evaluated it".
        """
        reg = ToolRegistry(governed=False)
        reg.service.register(tool(), principal="alice@contoso.com")
        promoted = reg.service.promote(
            "sample_tool",
            "1.0.0",
            source_environment="test",
            target_environment="prod",
            requested_by="alice@contoso.com",
        )
        assert promoted.metadata.status is ResourceStatus.ACTIVE

    def test_tool_registry_is_governed_by_default(self):
        reg = ToolRegistry()
        assert reg.service._governor is not None
