"""Governed registry lifecycle — the common substrate, proven through tools.

Covers required tests 1-15 for the registry slice: draft/promote/activate,
suspend/resume, deprecate, retire, version immutability, ownership transfer,
audit completeness, and the rule that registry state does not authorize
execution.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from paios import (
    Approval,
    ApprovalState,
    Identity,
    InMemoryAuditSink,
    PolicyDecision,
)
from paios.audit import AuditTrail
from paios.execution import (
    ExecutionGateway,
    Principal,
    RejectionReason,
    StaticHandlerResolver,
)
from paios.registry import (
    ExecutionAvailability,
    GovernedResourceMetadata,
    Owner,
    OwnerType,
    RegistryError,
    RegistryOperation,
    RegistryType,
    ResourceStatus,
    Version,
    VersionBump,
)
from paios.tools import (
    OperationType,
    ToolDefinition,
    ToolRegistry,
    ToolSpec,
)


def tool(
    tool_id: str = "sample_tool",
    *,
    status: ResourceStatus = ResourceStatus.DRAFT,
    version: str = "1.0.0",
    environments: tuple[str, ...] = (),
    owner: str = "platform",
) -> ToolDefinition:
    return ToolDefinition(
        metadata=GovernedResourceMetadata(
            registry_type=RegistryType.TOOL,
            resource_id=tool_id,
            version=Version.parse(version),
            display_name=tool_id,
            owner=Owner(OwnerType.GROUP, owner),
            status=status,
            environments=frozenset(environments),
        ),
        spec=ToolSpec(
            operation_type=OperationType.READ,
            handler=f"paios.tools.{tool_id}",
        ),
    )


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.service.register(tool(), principal="alice")
    return reg


# Registry mutations are governed. Non-production promotion is auto-allowed;
# production promotion requires a real approval from a different principal.
ADMIN = frozenset({"registry_admin", "platform_admin"})
APPROVED_BY_BOSS = Approval(state=ApprovalState.APPROVED, approver="boss@contoso.com")


def gateway_for(registry: ToolRegistry, environment: str = "test") -> ExecutionGateway:
    return ExecutionGateway(
        registry,
        StaticHandlerResolver({"sample_tool": lambda args: {"ran": True}}),
        environment=environment,
    )


def principal() -> Principal:
    return Principal(
        identity=Identity(subject="alice@contoso.com", authenticated=True)
    )


ALLOW = PolicyDecision()


class TestDraftLifecycle:
    def test_tool_can_be_registered_in_draft(self, registry):
        """Required test 1."""
        resource = registry.get("sample_tool")
        assert resource.status is ResourceStatus.DRAFT
        assert resource.availability is ExecutionAvailability.UNAVAILABLE
        assert not resource.enabled

    def test_draft_tool_cannot_execute(self, registry):
        """Required test 2."""
        result = gateway_for(registry).execute(
            principal=principal(), tool_id="sample_tool", governance_context=ALLOW
        )
        assert not result.allowed
        assert result.reason is RejectionReason.TOOL_DRAFT


class TestPromotion:
    def test_validated_version_can_be_promoted(self, registry):
        """Required test 3."""
        promoted = registry.service.promote(
            "sample_tool",
            "1.0.0",
            source_environment="dev",
            target_environment="test",
            requested_by="alice",
        )
        assert promoted.metadata.status is ResourceStatus.ACTIVE
        assert "test" in promoted.metadata.environments

    def test_promotion_fails_validation_without_a_handler(self):
        reg = ToolRegistry()
        broken = ToolDefinition(
            metadata=tool("broken").metadata,
            spec=ToolSpec(handler=""),
        )
        reg.service.register(broken)
        with pytest.raises(RegistryError, match="declares no handler"):
            reg.service.promote(
                "broken",
                "1.0.0",
                source_environment="dev",
                target_environment="test",
                requested_by="alice",
            )

    def test_active_production_tool_can_execute_when_policy_permits(self, registry):
        """Required test 4."""
        registry.service.promote(
            "sample_tool",
            "1.0.0",
            source_environment="dev",
            target_environment="prod",
            requested_by="alice",
            approved_by="boss@contoso.com",
            principal_roles=ADMIN,
        )
        result = gateway_for(registry, "prod").execute(
            principal=principal(), tool_id="sample_tool", governance_context=ALLOW
        )
        assert result.allowed
        assert result.output == {"ran": True}

    def test_production_promotion_is_audited(self, registry):
        """Required test 12."""
        registry.service.promote(
            "sample_tool",
            "1.0.0",
            source_environment="test",
            target_environment="prod",
            requested_by="alice",
            approved_by="boss@contoso.com",
            principal_roles=ADMIN,
            trace_id="trace-123",
        )
        record = registry.service.promotions[-1]
        assert record.target_environment == "prod"
        assert record.approved_by == "boss@contoso.com"
        assert record.trace_id == "trace-123"

        events = [
            e
            for e in registry.service.events_for("sample_tool")
            if e.operation is RegistryOperation.PROMOTE
        ]
        assert events and events[-1].environment == "prod"

    def test_promotion_does_not_mutate_the_source_version(self, registry):
        before = registry.get("sample_tool", "1.0.0")
        registry.service.promote(
            "sample_tool",
            "1.0.0",
            source_environment="dev",
            target_environment="test",
            requested_by="alice",
        )
        # The original object is frozen and unchanged.
        assert before.metadata.status is ResourceStatus.DRAFT
        assert before.metadata.environments == frozenset()

    def test_retired_resource_cannot_be_promoted(self, registry):
        registry.service.retire("sample_tool", principal="alice")
        with pytest.raises(RegistryError, match="retired"):
            registry.service.promote(
                "sample_tool",
                "1.0.0",
                source_environment="dev",
                target_environment="prod",
                requested_by="alice",
            )


class TestSuspension:
    def _active(self, registry):
        registry.service.promote(
            "sample_tool",
            "1.0.0",
            source_environment="dev",
            target_environment="test",
            requested_by="alice",
        )

    def test_suspended_tool_immediately_fails_at_gateway(self, registry):
        """Required test 5."""
        self._active(registry)
        registry.service.suspend("sample_tool", principal="alice", reason="incident")

        result = gateway_for(registry).execute(
            principal=principal(), tool_id="sample_tool", governance_context=ALLOW
        )
        assert not result.allowed
        assert result.reason is RejectionReason.TOOL_SUSPENDED

    def test_suspension_requires_no_version_change(self, registry):
        self._active(registry)
        registry.service.suspend("sample_tool", principal="alice")
        assert registry.get("sample_tool").version == Version(1, 0, 0)

    def test_resume_restores_availability(self, registry):
        """Required test 6."""
        self._active(registry)
        registry.service.suspend("sample_tool", principal="alice")
        registry.service.resume(
            "sample_tool", principal="alice", principal_roles=ADMIN
        )

        result = gateway_for(registry).execute(
            principal=principal(), tool_id="sample_tool", governance_context=ALLOW
        )
        assert result.allowed

    def test_suspension_is_audited(self, registry):
        self._active(registry)
        registry.service.suspend("sample_tool", principal="alice", reason="incident")
        event = registry.service.events_for("sample_tool")[-1]
        assert event.operation is RegistryOperation.SUSPEND
        assert event.previous_state is ResourceStatus.ACTIVE
        assert event.new_state is ResourceStatus.SUSPENDED
        assert event.reason == "incident"

    def test_cannot_resume_something_never_suspended(self, registry):
        self._active(registry)
        with pytest.raises(RegistryError, match="cannot resume"):
            registry.service.resume(
                "sample_tool", principal="alice", principal_roles=ADMIN
            )


class TestDeprecation:
    def _active(self, registry):
        registry.service.promote(
            "sample_tool",
            "1.0.0",
            source_environment="dev",
            target_environment="test",
            requested_by="alice",
        )

    def test_deprecated_tool_behaviour_follows_policy(self, registry):
        """Required test 7 — runs only where policy permits."""
        self._active(registry)
        registry.service.deprecate(
            "sample_tool",
            principal="alice",
            replacement_resource_id="sample_tool_v2",
        )
        gw = gateway_for(registry)

        refused = gw.execute(
            principal=principal(),
            tool_id="sample_tool",
            governance_context=PolicyDecision(permits_deprecated=False),
        )
        assert not refused.allowed
        assert refused.reason is RejectionReason.TOOL_DEPRECATED_NOT_PERMITTED

        permitted = gw.execute(
            principal=principal(),
            tool_id="sample_tool",
            governance_context=PolicyDecision(permits_deprecated=True),
        )
        assert permitted.allowed

    def test_deprecation_records_replacement_metadata(self, registry):
        self._active(registry)
        registry.service.deprecate(
            "sample_tool",
            principal="alice",
            replacement_resource_id="sample_tool_v2",
            replacement_version="2.0.0",
        )
        meta = registry.get("sample_tool").metadata
        assert meta.deprecated_at is not None
        assert meta.replacement_resource_id == "sample_tool_v2"
        assert meta.replacement_version == "2.0.0"


class TestRetirement:
    def test_retired_tool_cannot_execute(self, registry):
        """Required test 8."""
        registry.service.promote(
            "sample_tool",
            "1.0.0",
            source_environment="dev",
            target_environment="test",
            requested_by="alice",
        )
        registry.service.retire("sample_tool", principal="alice")

        result = gateway_for(registry).execute(
            principal=principal(), tool_id="sample_tool", governance_context=ALLOW
        )
        assert not result.allowed
        assert result.reason is RejectionReason.TOOL_RETIRED

    def test_retired_id_cannot_be_reused(self, registry):
        """Required test 9."""
        registry.service.retire("sample_tool", principal="alice")
        assert registry.service.is_retired_id("sample_tool")

        with pytest.raises(RegistryError, match="retired and cannot be reused"):
            registry.service.register(tool("sample_tool", version="2.0.0"))

    def test_retired_resource_remains_addressable_for_audit(self, registry):
        """Required test 14."""
        registry.service.retire("sample_tool", principal="alice", reason="eol")

        resolved = registry.get("sample_tool", "1.0.0")
        assert resolved is not None
        assert resolved.status is ResourceStatus.RETIRED
        assert resolved.metadata.owner.owner_id == "platform"

        event = registry.service.events_for("sample_tool")[-1]
        assert event.operation is RegistryOperation.RETIRE
        assert event.reason == "eol"


class TestVersioning:
    def test_new_version_preserves_the_historical_version(self, registry):
        """Required test 10."""
        registry.service.promote(
            "sample_tool",
            "1.0.0",
            source_environment="dev",
            target_environment="test",
            requested_by="alice",
        )
        registry.service.create_version(
            "sample_tool", VersionBump.MINOR, principal="alice"
        )

        old = registry.get("sample_tool", "1.0.0")
        new = registry.get("sample_tool", "1.1.0")

        assert old is not None and new is not None
        assert old.status is ResourceStatus.ACTIVE
        assert new.status is ResourceStatus.DRAFT
        assert len(registry.versions_of("sample_tool")) == 2

    def test_registering_an_existing_version_is_refused(self, registry):
        with pytest.raises(RegistryError, match="versions are immutable"):
            registry.service.register(tool("sample_tool", version="1.0.0"))

    @pytest.mark.parametrize(
        ("bump", "expected"),
        [
            (VersionBump.PATCH, "1.0.1"),
            (VersionBump.MINOR, "1.1.0"),
            (VersionBump.MAJOR, "2.0.0"),
        ],
    )
    def test_version_bump_semantics(self, registry, bump, expected):
        created = registry.service.create_version(
            "sample_tool", bump, principal="alice"
        )
        assert str(created.metadata.version) == expected

    def test_new_version_starts_unpromoted(self, registry):
        registry.service.promote(
            "sample_tool",
            "1.0.0",
            source_environment="dev",
            target_environment="test",
            requested_by="alice",
        )
        created = registry.service.create_version(
            "sample_tool", VersionBump.MAJOR, principal="alice"
        )
        assert created.metadata.environments == frozenset()

    def test_malformed_version_is_rejected(self):
        with pytest.raises(RegistryError):
            Version.parse("not-a-version")


class TestOwnership:
    def test_ownership_transfer_is_audited(self, registry):
        """Required test 11."""
        registry.service.transfer_ownership(
            "sample_tool",
            Owner(OwnerType.USER, "carlton@contoso.com"),
            principal="alice",
            approval_reference="APPROVAL-77",
            approval=APPROVED_BY_BOSS,
        )
        assert registry.get("sample_tool").owner == "carlton@contoso.com"

        event = registry.service.events_for("sample_tool")[-1]
        assert event.operation is RegistryOperation.TRANSFER_OWNERSHIP
        assert event.approval_reference == "APPROVAL-77"
        assert event.principal == "alice"

    def test_every_resource_has_an_accountable_owner(self, registry):
        assert registry.get("sample_tool").metadata.owner.owner_id


class TestNoLifecycleBypass:
    def test_direct_mutation_cannot_bypass_lifecycle(self, registry):
        """Required test 13 — stored definitions are frozen."""
        resource = registry.get("sample_tool")

        with pytest.raises(FrozenInstanceError):
            resource.metadata.status = ResourceStatus.ACTIVE  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            resource.spec.handler = "something_else"  # type: ignore[misc]

        assert registry.get("sample_tool").status is ResourceStatus.DRAFT

    def test_service_exposes_no_generic_execute(self, registry):
        """Execution stays resource-specific and governed elsewhere."""
        assert not hasattr(registry.service, "execute")

    def test_mutating_a_returned_copy_does_not_affect_the_registry(self, registry):
        from dataclasses import replace

        detached = replace(
            registry.get("sample_tool").metadata, status=ResourceStatus.ACTIVE
        )
        assert detached.status is ResourceStatus.ACTIVE
        assert registry.get("sample_tool").status is ResourceStatus.DRAFT


class TestRegistryStateIsNotAuthorization:
    def test_registry_state_does_not_authorize_execution(self, registry):
        """Required test 15.

        An active, promoted tool is *operationally available*. It is still
        refused when policy denies — availability is one input, never a grant.
        """
        registry.service.promote(
            "sample_tool",
            "1.0.0",
            source_environment="dev",
            target_environment="test",
            requested_by="alice",
        )
        active = registry.get("sample_tool")
        assert active.status is ResourceStatus.ACTIVE
        assert active.availability is ExecutionAvailability.AVAILABLE

        from paios.models import PolicyOutcome

        denied = gateway_for(registry).execute(
            principal=principal(),
            tool_id="sample_tool",
            governance_context=PolicyDecision(
                decision=PolicyOutcome.DENY, reason_codes=("PROHIBITED_OPERATION",)
            ),
        )
        assert not denied.allowed
        assert denied.reason is RejectionReason.POLICY_DENIED

    def test_active_tool_still_requires_approval_when_policy_says_so(self, registry):
        registry.service.promote(
            "sample_tool",
            "1.0.0",
            source_environment="dev",
            target_environment="test",
            requested_by="alice",
        )
        from paios.models import PolicyOutcome

        result = gateway_for(registry).execute(
            principal=principal(),
            tool_id="sample_tool",
            governance_context=PolicyDecision(
                decision=PolicyOutcome.REQUIRE_APPROVAL
            ),
        )
        assert not result.allowed
        assert result.reason is RejectionReason.APPROVAL_REQUIRED


class TestLifecycleAuditCompleteness:
    def test_every_lifecycle_operation_emits_an_event(self, registry):
        svc = registry.service
        svc.promote(
            "sample_tool",
            "1.0.0",
            source_environment="dev",
            target_environment="test",
            requested_by="alice",
        )
        svc.suspend("sample_tool", principal="alice")
        svc.resume("sample_tool", principal="alice", principal_roles=ADMIN)
        svc.deprecate("sample_tool", principal="alice")
        svc.retire("sample_tool", principal="alice", principal_roles=ADMIN)

        operations = [e.operation for e in svc.events_for("sample_tool")]
        for expected in (
            RegistryOperation.REGISTER,
            RegistryOperation.PROMOTE,
            RegistryOperation.SUSPEND,
            RegistryOperation.RESUME,
            RegistryOperation.DEPRECATE,
            RegistryOperation.RETIRE,
        ):
            assert expected in operations, f"missing lifecycle event {expected}"

    def test_event_carries_the_full_audit_shape(self, registry):
        event = registry.service.events_for("sample_tool")[0]
        payload = event.to_dict()
        for key in (
            "registry_event_id",
            "trace_id",
            "registry_type",
            "resource_id",
            "version",
            "operation",
            "previous_state",
            "new_state",
            "principal",
            "timestamp",
            "reason",
            "approval_reference",
            "environment",
        ):
            assert key in payload, f"audit record missing {key}"

    def test_gateway_execution_still_audits_alongside_registry_events(self, registry):
        registry.service.promote(
            "sample_tool",
            "1.0.0",
            source_environment="dev",
            target_environment="test",
            requested_by="alice",
        )
        sink = InMemoryAuditSink()
        trail = AuditTrail(sink)
        gateway_for(registry).execute(
            principal=principal(),
            tool_id="sample_tool",
            governance_context=ALLOW,
            trail=trail,
            request_id="req-1",
        )
        assert sink.events
        assert registry.service.events_for("sample_tool")
