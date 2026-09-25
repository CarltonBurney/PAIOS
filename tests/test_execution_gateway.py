"""Tool Registry and Execution Gateway enforcement.

Covers required tests 8-14: unknown/disabled/denied tools cannot execute,
approval cannot be bypassed, a registered authorized tool can execute, direct
execution is not a supported public path, and every attempt is audited.
"""

from __future__ import annotations

import pytest

from paios import Approval, ApprovalState, Identity, InMemoryAuditSink, PolicyDecision
from paios.audit import AuditStage, AuditTrail
from paios.config import DEFAULT_TOOL_REGISTRY_PATH
from paios.execution import (
    ExecutionGateway,
    Principal,
    RejectionReason,
    StaticHandlerResolver,
)
from paios.models import PolicyOutcome
from paios.tools import CallerType, ToolRegistry, ToolRegistryError


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry.from_file(DEFAULT_TOOL_REGISTRY_PATH)


@pytest.fixture
def resolver() -> StaticHandlerResolver:
    return StaticHandlerResolver(
        {
            "directory_read": lambda args: {"user": args.get("user_id"), "ok": True},
            "security_read": lambda args: {"resource": args.get("resource_id")},
            "security_write": lambda args: {"written": True},
            "bulk_export": lambda args: {"rows": 0},
            "legacy_report": lambda args: {"legacy": True},
            "retired_export": lambda args: {"retired": True},
        }
    )


@pytest.fixture
def gateway(registry, resolver) -> ExecutionGateway:
    return ExecutionGateway(registry, resolver, environment="dev")


def principal(*, roles=(), scopes=(), caller=CallerType.HUMAN) -> Principal:
    return Principal(
        identity=Identity(
            subject="alice@contoso.com",
            roles=frozenset(roles),
            authenticated=True,
        ),
        caller_type=caller,
        scopes=frozenset(scopes),
    )


ALLOW = PolicyDecision(decision=PolicyOutcome.ALLOW)


class TestRegistry:
    def test_registry_loads_from_file(self, registry):
        assert len(registry) == 6
        assert "security_read" in registry

    def test_unknown_tool_is_not_registered(self, registry):
        assert registry.get("invented_by_an_agent") is None
        assert "invented_by_an_agent" not in registry

    def test_require_raises_on_unknown_tool(self, registry):
        with pytest.raises(ToolRegistryError):
            registry.require("invented_by_an_agent")

    def test_retired_tools_are_excluded_from_enabled_set(self, registry):
        """`enabled` is computed from lifecycle status, not stored."""
        enabled = {t.tool_id for t in registry.enabled_tools()}
        assert "retired_export" not in enabled
        # Deprecated is still nominally enabled — it is policy-gated, not off.
        assert "legacy_report" in enabled

    def test_duplicate_tool_ids_are_rejected(self):
        with pytest.raises(ToolRegistryError):
            ToolRegistry.from_dict(
                {
                    "tools": [
                        {"tool_id": "dupe", "handler": "a"},
                        {"tool_id": "dupe", "handler": "b"},
                    ]
                }
            )

    def test_tool_carries_its_own_governance_metadata(self, registry):
        tool = registry.require("security_write")
        assert tool.risk_level.value == "L4"
        assert "security_admin" in tool.required_roles
        assert tool.approval_policy == "PAIOS-SEC-001"


class TestGatewayRejections:
    def test_unknown_tool_cannot_execute(self, gateway):
        """Required test 8."""
        result = gateway.execute(
            principal=principal(), tool_id="not_a_tool", governance_context=ALLOW
        )
        assert not result.allowed
        assert result.reason is RejectionReason.UNKNOWN_TOOL

    def test_retired_tool_cannot_execute(self, gateway):
        """Required test 9 — a retired tool is unavailable regardless."""
        result = gateway.execute(
            principal=principal(), tool_id="retired_export", governance_context=ALLOW
        )
        assert not result.allowed
        assert result.reason is RejectionReason.TOOL_RETIRED

    def test_denied_tool_cannot_execute(self, gateway):
        """Required test 10."""
        decision = PolicyDecision(
            decision=PolicyOutcome.ALLOW, denied_tools=frozenset({"directory_read"})
        )
        result = gateway.execute(
            principal=principal(),
            tool_id="directory_read",
            governance_context=decision,
        )
        assert not result.allowed
        assert result.reason is RejectionReason.TOOL_DENIED_BY_POLICY

    def test_policy_deny_blocks_even_a_permitted_tool(self, gateway):
        decision = PolicyDecision(
            decision=PolicyOutcome.DENY, reason_codes=("PROHIBITED_OPERATION",)
        )
        result = gateway.execute(
            principal=principal(),
            tool_id="directory_read",
            governance_context=decision,
        )
        assert not result.allowed
        assert result.reason is RejectionReason.POLICY_DENIED
        assert "PROHIBITED_OPERATION" in result.detail

    def test_tool_outside_the_allow_list_cannot_execute(self, gateway):
        decision = PolicyDecision(allowed_tools=frozenset({"security_read"}))
        result = gateway.execute(
            principal=principal(),
            tool_id="directory_read",
            governance_context=decision,
        )
        assert not result.allowed
        assert result.reason is RejectionReason.TOOL_DENIED_BY_POLICY

    def test_missing_role_is_rejected(self, gateway):
        result = gateway.execute(
            principal=principal(scopes=["security.write"]),
            tool_id="security_write",
            governance_context=ALLOW,
            approval=Approval(state=ApprovalState.APPROVED, approver="boss"),
        )
        assert not result.allowed
        assert result.reason is RejectionReason.MISSING_ROLE

    def test_missing_scope_is_rejected(self, gateway):
        result = gateway.execute(
            principal=principal(roles=["security_admin"]),
            tool_id="security_write",
            governance_context=ALLOW,
            approval=Approval(state=ApprovalState.APPROVED, approver="boss"),
        )
        assert not result.allowed
        assert result.reason is RejectionReason.MISSING_SCOPE

    def test_environment_restriction_is_enforced(self, registry, resolver):
        prod = ExecutionGateway(registry, resolver, environment="prod")
        result = prod.execute(
            principal=principal(roles=["data_steward"], scopes=["data.export"]),
            tool_id="bulk_export",
            governance_context=ALLOW,
            approval=Approval(state=ApprovalState.APPROVED, approver="boss"),
        )
        assert not result.allowed
        # Promotion is authoritative and is checked before declared constraints.
        assert result.reason is RejectionReason.TOOL_NOT_IN_ENVIRONMENT

    def test_caller_type_restriction_is_enforced(self, gateway):
        result = gateway.execute(
            principal=principal(
                roles=["security_admin"],
                scopes=["security.write"],
                caller=CallerType.AGENT,
            ),
            tool_id="security_write",
            governance_context=ALLOW,
            approval=Approval(state=ApprovalState.APPROVED, approver="boss"),
        )
        assert not result.allowed
        assert result.reason is RejectionReason.CALLER_TYPE_NOT_PERMITTED

    def test_arguments_outside_the_contract_are_rejected(self, gateway):
        result = gateway.execute(
            principal=principal(),
            tool_id="directory_read",
            args={"user_id": "u1", "sneaky_extra": "payload"},
            governance_context=ALLOW,
        )
        assert not result.allowed
        assert result.reason is RejectionReason.CONTRACT_VIOLATION

    def test_missing_handler_is_rejected(self, registry):
        bare = ExecutionGateway(registry, StaticHandlerResolver(), environment="dev")
        result = bare.execute(
            principal=principal(), tool_id="directory_read", governance_context=ALLOW
        )
        assert not result.allowed
        assert result.reason is RejectionReason.NO_HANDLER


class TestApprovalCannotBeBypassed:
    def test_required_approval_cannot_be_bypassed(self, gateway):
        """Required test 11 — no approval object means no execution."""
        decision = PolicyDecision(decision=PolicyOutcome.REQUIRE_APPROVAL)
        result = gateway.execute(
            principal=principal(),
            tool_id="directory_read",
            governance_context=decision,
        )
        assert not result.allowed
        assert result.reason is RejectionReason.APPROVAL_REQUIRED

    def test_rejected_approval_does_not_satisfy_the_gate(self, gateway):
        decision = PolicyDecision(decision=PolicyOutcome.REQUIRE_APPROVAL)
        result = gateway.execute(
            principal=principal(),
            tool_id="directory_read",
            governance_context=decision,
            approval=Approval(state=ApprovalState.REJECTED),
        )
        assert not result.allowed
        assert result.reason is RejectionReason.APPROVAL_NOT_GRANTED

    def test_tool_level_approval_policy_applies_without_policy_requirement(
        self, gateway
    ):
        """security_write declares its own approval_policy in the registry."""
        result = gateway.execute(
            principal=principal(roles=["security_admin"], scopes=["security.write"]),
            tool_id="security_write",
            governance_context=ALLOW,
        )
        assert not result.allowed
        assert result.reason is RejectionReason.APPROVAL_REQUIRED

    def test_approval_cannot_override_a_deny(self, gateway):
        decision = PolicyDecision(
            decision=PolicyOutcome.DENY, reason_codes=("PROHIBITED_OPERATION",)
        )
        result = gateway.execute(
            principal=principal(),
            tool_id="directory_read",
            governance_context=decision,
            approval=Approval(state=ApprovalState.APPROVED, approver="boss"),
        )
        assert not result.allowed
        assert result.reason is RejectionReason.POLICY_DENIED


class TestAllowedPath:
    def test_authorized_registered_tool_can_execute(self, gateway):
        """Required test 12."""
        result = gateway.execute(
            principal=principal(),
            tool_id="directory_read",
            args={"user_id": "u1"},
            governance_context=ALLOW,
        )
        assert result.allowed
        assert result.output == {"user": "u1", "ok": True}
        assert result.reason is None

    def test_full_path_registry_policy_gateway(self, gateway):
        result = gateway.execute(
            principal=principal(roles=["security_admin"], scopes=["security.write"]),
            tool_id="security_write",
            args={"resource_id": "r1", "value": "v"},
            governance_context=PolicyDecision(
                decision=PolicyOutcome.REQUIRE_APPROVAL,
                allowed_tools=frozenset({"security_write"}),
            ),
            approval=Approval(state=ApprovalState.APPROVED, approver="boss"),
        )
        assert result.allowed


class TestNoDirectExecutionPath:
    def test_direct_execution_is_not_exposed_on_the_public_api(self):
        """Required test 13.

        The package must not export a way to reach a handler without the
        gateway. A ``tool.execute(args)`` shape anywhere in the public surface
        would make enforcement optional.
        """
        import paios

        for name in paios.__all__:
            attr = getattr(paios, name)
            assert not hasattr(attr, "execute") or name == "ExecutionGateway", (
                f"public export {name!r} exposes .execute() outside the gateway"
            )

    def test_tool_definitions_carry_no_callable_handler(self, registry):
        """A registry entry names a handler; it does not hold one."""
        tool = registry.require("directory_read")
        assert isinstance(tool.handler, str)
        assert not callable(tool.handler)

    def test_registry_cannot_dispatch(self, registry):
        assert not hasattr(registry, "execute")
        assert not hasattr(registry, "invoke")
        assert not hasattr(registry, "call")


class TestExecutionAuditing:
    def _trail(self):
        sink = InMemoryAuditSink()
        return sink, AuditTrail(sink)

    def test_allowed_execution_produces_an_audit_record(self, gateway):
        """Required test 14 (allowed half)."""
        sink, trail = self._trail()
        gateway.execute(
            principal=principal(),
            tool_id="directory_read",
            args={"user_id": "u1"},
            governance_context=ALLOW,
            trail=trail,
            request_id="req-1",
        )
        assert AuditStage.EXECUTION_ALLOWED in sink.stages()

    def test_rejected_execution_produces_an_audit_record(self, gateway):
        """Required test 14 (rejected half) — refusal is never silent."""
        sink, trail = self._trail()
        gateway.execute(
            principal=principal(),
            tool_id="not_a_tool",
            governance_context=ALLOW,
            trail=trail,
            request_id="req-1",
        )
        assert AuditStage.EXECUTION_REJECTED in sink.stages()

    @pytest.mark.parametrize(
        ("tool_id", "decision"),
        [
            ("not_a_tool", ALLOW),
            ("retired_export", ALLOW),
            ("directory_read", PolicyDecision(decision=PolicyOutcome.DENY)),
            ("directory_read", PolicyDecision(decision=PolicyOutcome.REQUIRE_APPROVAL)),
        ],
    )
    def test_every_rejection_path_is_audited(self, gateway, tool_id, decision):
        sink, trail = self._trail()
        result = gateway.execute(
            principal=principal(),
            tool_id=tool_id,
            governance_context=decision,
            trail=trail,
            request_id="req-1",
        )
        assert not result.allowed
        events = [e for e in sink.events if e.stage is AuditStage.EXECUTION_REJECTED]
        assert len(events) == 1
        assert events[0].detail["reason"] == result.reason.value

    def test_audit_record_carries_the_correlation_id(self, gateway):
        sink, trail = self._trail()
        result = gateway.execute(
            principal=principal(),
            tool_id="directory_read",
            args={"user_id": "u1"},
            governance_context=ALLOW,
            trail=trail,
            request_id="req-1",
        )
        assert result.correlation_id == trail.correlation_id
        assert all(e.correlation_id == trail.correlation_id for e in sink.events)
