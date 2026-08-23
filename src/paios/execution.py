"""Execution Gateway — the enforcement boundary.

Everything upstream of this module produces *decisions*. This is the only place
that turns a decision into an action, and it re-validates independently rather
than trusting that a caller consulted policy first. A caller who never called
``permits_tool()`` gets the same answer as one who did.

Direct handler invocation is deliberately not a supported path. There is no
public ``tool.execute(args)`` — the only way to reach a handler is::

    gateway.execute(
        principal=principal,
        tool_id="security_read",
        args={...},
        governance_context=decision,
    )

Rejection is a first-class outcome, not an exception to be swallowed: every
attempt, allowed or rejected, produces an audit event carrying the correlation
ID. A silent refusal would be indistinguishable from a tool that did nothing.

DESIGN RULE — risk never authorizes execution. The gateway does not consult
RiskLevel to decide whether to dispatch. Risk is an input to policy; policy,
authorization, registration, and approval are separate inputs here. There is no
path of the form ``if risk <= L1: execute()``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

from .audit import AuditStage, AuditTrail
from .models import Approval, ApprovalState, Identity, PolicyDecision
from .registry import ResourceStatus
from .tools import CallerType, ToolDefinition, ToolRegistry

Handler = Callable[[dict[str, Any]], Any]


class RejectionReason(str, Enum):
    """Why the gateway refused. Each maps to exactly one guard below."""

    UNKNOWN_TOOL = "unknown_tool"
    TOOL_DISABLED = "tool_disabled"
    TOOL_DRAFT = "tool_draft"
    TOOL_RETIRED = "tool_retired"
    TOOL_SUSPENDED = "tool_suspended"
    TOOL_DEPRECATED_NOT_PERMITTED = "tool_deprecated_not_permitted"
    TOOL_NOT_IN_ENVIRONMENT = "tool_not_in_environment"
    POLICY_DENIED = "policy_denied"
    TOOL_DENIED_BY_POLICY = "tool_denied_by_policy"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_NOT_GRANTED = "approval_not_granted"
    MISSING_ROLE = "missing_role"
    MISSING_SCOPE = "missing_scope"
    ENVIRONMENT_NOT_PERMITTED = "environment_not_permitted"
    CALLER_TYPE_NOT_PERMITTED = "caller_type_not_permitted"
    CONTRACT_VIOLATION = "contract_violation"
    NO_HANDLER = "no_handler"
    HANDLER_ERROR = "handler_error"


class ExecutionRejected(RuntimeError):
    """Raised when the gateway refuses to dispatch."""

    def __init__(self, reason: RejectionReason, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Principal:
    """Who is invoking. Distinct from Identity: carries caller type and scopes."""

    identity: Identity
    caller_type: CallerType = CallerType.HUMAN
    scopes: frozenset[str] = frozenset()
    agent_id: str | None = None
    workflow_id: str | None = None


@dataclass(frozen=True)
class ExecutionResult:
    tool_id: str
    allowed: bool
    output: Any = None
    reason: RejectionReason | None = None
    detail: str = ""
    correlation_id: str | None = None
    completed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "allowed": self.allowed,
            "reason": self.reason.value if self.reason else None,
            "detail": self.detail,
        }


class HandlerResolver(Protocol):
    def resolve(self, tool: ToolDefinition) -> Handler | None: ...


class StaticHandlerResolver:
    """Maps tool_id to a callable. Registration is explicit, never by name."""

    def __init__(self, handlers: dict[str, Handler] | None = None) -> None:
        self._handlers: dict[str, Handler] = dict(handlers or {})

    def register(self, tool_id: str, handler: Handler) -> None:
        self._handlers[tool_id] = handler

    def resolve(self, tool: ToolDefinition) -> Handler | None:
        return self._handlers.get(tool.tool_id)


class ExecutionGateway:
    """Validates every execution attempt against every governance input."""

    def __init__(
        self,
        registry: ToolRegistry,
        resolver: HandlerResolver | None = None,
        *,
        environment: str = "dev",
    ) -> None:
        self.registry = registry
        self.resolver = resolver or StaticHandlerResolver()
        self.environment = environment

    def execute(
        self,
        *,
        principal: Principal,
        tool_id: str,
        args: dict[str, Any] | None = None,
        governance_context: PolicyDecision | None = None,
        approval: Approval | None = None,
        trail: AuditTrail | None = None,
        request_id: str = "",
    ) -> ExecutionResult:
        args = args or {}
        decision = governance_context or PolicyDecision()
        subject = principal.identity.subject

        def reject(reason: RejectionReason, detail: str) -> ExecutionResult:
            result = ExecutionResult(
                tool_id=tool_id,
                allowed=False,
                reason=reason,
                detail=detail,
                correlation_id=trail.correlation_id if trail else None,
            )
            if trail is not None:
                trail.emit(
                    request_id,
                    AuditStage.EXECUTION_REJECTED,
                    subject,
                    **result.to_dict(),
                )
            return result

        # 1. The tool must be registered. An unknown id is not a tool.
        tool = self.registry.get_for_execution(
            tool_id, self.environment
        ) or self.registry.get(tool_id)
        if tool is None:
            return reject(
                RejectionReason.UNKNOWN_TOOL,
                f"'{tool_id}' is not in the tool registry",
            )

        # 2. Lifecycle status gates execution before any permission question.
        #    Registry activation is not authorization — an available resource
        #    still has to clear every check below.
        status = tool.metadata.status
        if status is ResourceStatus.DRAFT:
            return reject(
                RejectionReason.TOOL_DRAFT,
                f"tool '{tool_id}' is in draft and is not executable",
            )
        if status is ResourceStatus.RETIRED:
            return reject(
                RejectionReason.TOOL_RETIRED, f"tool '{tool_id}' is retired"
            )
        if status is ResourceStatus.SUSPENDED:
            return reject(
                RejectionReason.TOOL_SUSPENDED,
                f"tool '{tool_id}' is suspended",
            )
        if status is ResourceStatus.DEPRECATED:
            # Deprecated resources run only where policy explicitly permits.
            if not decision.permits_deprecated:
                return reject(
                    RejectionReason.TOOL_DEPRECATED_NOT_PERMITTED,
                    f"tool '{tool_id}' is deprecated and policy does not "
                    "permit deprecated resources",
                )

        # The version must have been promoted into this environment.
        if not tool.metadata.in_environment(self.environment):
            return reject(
                RejectionReason.TOOL_NOT_IN_ENVIRONMENT,
                f"tool '{tool_id}' {tool.version} is not promoted to "
                f"{self.environment}",
            )

        # 3. A policy deny is final. Approval cannot override it.
        if decision.denied:
            codes = ", ".join(decision.reason_codes) or "policy denied"
            return reject(RejectionReason.POLICY_DENIED, codes)

        # 4. Tool-level allow/deny from the merged policy decision.
        if not decision.permits_tool(tool_id):
            return reject(
                RejectionReason.TOOL_DENIED_BY_POLICY,
                f"policy does not permit tool '{tool_id}'",
            )

        # 5. An approval requirement must be satisfied by an actual approval.
        if decision.require_approval or tool.approval_policy:
            if approval is None:
                return reject(
                    RejectionReason.APPROVAL_REQUIRED,
                    f"tool '{tool_id}' requires approval and none was supplied",
                )
            if approval.state is not ApprovalState.APPROVED:
                return reject(
                    RejectionReason.APPROVAL_NOT_GRANTED,
                    f"approval state is {approval.state.value}",
                )

        # 6. The principal must satisfy the tool's own contract.
        missing_roles = tool.missing_roles(principal.identity)
        if missing_roles:
            return reject(
                RejectionReason.MISSING_ROLE,
                f"missing role(s): {', '.join(sorted(missing_roles))}",
            )

        missing_scopes = tool.missing_scopes(principal.scopes)
        if missing_scopes:
            return reject(
                RejectionReason.MISSING_SCOPE,
                f"missing scope(s): {', '.join(sorted(missing_scopes))}",
            )

        # 7. Declared constraints.
        if not tool.constraints.permits_environment(self.environment):
            return reject(
                RejectionReason.ENVIRONMENT_NOT_PERMITTED,
                f"tool '{tool_id}' is not permitted in {self.environment}",
            )

        if not tool.constraints.permits_caller(principal.caller_type):
            return reject(
                RejectionReason.CALLER_TYPE_NOT_PERMITTED,
                f"caller type {principal.caller_type.value} may not call "
                f"'{tool_id}'",
            )

        # 8. The call must stay inside the registered input contract.
        unexpected = _contract_violations(tool, args)
        if unexpected:
            return reject(
                RejectionReason.CONTRACT_VIOLATION,
                f"argument(s) outside the registered contract: "
                f"{', '.join(sorted(unexpected))}",
            )

        handler = self.resolver.resolve(tool)
        if handler is None:
            return reject(
                RejectionReason.NO_HANDLER,
                f"no handler registered for '{tool_id}'",
            )

        try:
            output = handler(args)
        except Exception as exc:  # noqa: BLE001 - surfaced as a rejection
            return reject(RejectionReason.HANDLER_ERROR, str(exc))

        result = ExecutionResult(
            tool_id=tool_id,
            allowed=True,
            output=output,
            correlation_id=trail.correlation_id if trail else None,
        )
        if trail is not None:
            trail.emit(
                request_id,
                AuditStage.EXECUTION_ALLOWED,
                subject,
                tool_id=tool_id,
                version=str(tool.version),
                status=tool.status.value,
                operation_type=tool.operation_type.value,
                audit_level=max(tool.audit_level, decision.audit_level),
            )
        return result


def _contract_violations(tool: ToolDefinition, args: dict[str, Any]) -> set[str]:
    """Arguments not declared in the tool's input schema.

    Only enforced when the tool declares properties; a tool with no declared
    schema accepts anything, which is recorded in the registry rather than
    assumed here.
    """
    properties = tool.input_schema.get("properties")
    if not isinstance(properties, dict):
        return set()
    return set(args) - set(properties)
