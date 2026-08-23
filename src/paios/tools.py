"""Tool Registry — the first registry built on the governed substrate.

``ToolDefinition`` is ``GovernedResourceMetadata`` + ``ToolSpec`` by
composition. Governance identity and lifecycle live in the shared metadata;
everything tool-specific stays in the spec. The same pattern will carry the
Agent, Model, and Workflow registries without a universal schema.

A tool is a governed capability, not a function an agent can name into
existence. Agents may *reference* registered tools; they may not create
executable tool identities.

DESIGN RULE — registry activation is not authorization. An ``active`` tool is
operationally available. Whether *this* principal may invoke it is decided by
identity, authorization, policy, and the Execution Gateway.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any

from .models import Identity, RiskDomain, RiskLevel
from .registry import (
    ExecutionAvailability,
    GovernedResourceMetadata,
    RegistryError,
    RegistryService,
    RegistryType,
    ResourceStatus,
    Version,
)


class ToolRegistryError(RegistryError):
    """Raised when a registry document is malformed."""


class OperationType(str, Enum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    EXECUTE = "execute"
    ADMIN = "admin"


class CallerType(str, Enum):
    HUMAN = "human"
    AGENT = "agent"
    WORKFLOW = "workflow"


@dataclass(frozen=True)
class ToolConstraints:
    """Declared extension points. Environment and caller type are enforced."""

    environments: tuple[str, ...] = ("*",)
    data_classifications: tuple[str, ...] = ()
    network_destinations: tuple[str, ...] = ()
    timeout_seconds: float | None = None
    max_retries: int = 0
    rate_limit_per_minute: int | None = None
    idempotent: bool = False
    caller_types: frozenset[CallerType] = frozenset()
    allowed_agents: tuple[str, ...] = ("*",)
    allowed_workflows: tuple[str, ...] = ("*",)

    def permits_environment(self, environment: str | None) -> bool:
        if "*" in self.environments:
            return True
        return environment is not None and environment in self.environments

    def permits_caller(self, caller: CallerType | None) -> bool:
        if not self.caller_types:
            return True
        return caller is not None and caller in self.caller_types

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ToolConstraints:
        return cls(
            environments=tuple(raw.get("environments", ("*",))),
            data_classifications=tuple(raw.get("data_classifications", ())),
            network_destinations=tuple(raw.get("network_destinations", ())),
            timeout_seconds=raw.get("timeout_seconds"),
            max_retries=int(raw.get("max_retries", 0)),
            rate_limit_per_minute=raw.get("rate_limit_per_minute"),
            idempotent=bool(raw.get("idempotent", False)),
            caller_types=frozenset(
                CallerType(c) for c in raw.get("caller_types", ())
            ),
            allowed_agents=tuple(raw.get("allowed_agents", ("*",))),
            allowed_workflows=tuple(raw.get("allowed_workflows", ("*",))),
        )


@dataclass(frozen=True)
class ToolSpec:
    """Tool-specific schema. Carries no governance identity of its own."""

    operation_type: OperationType = OperationType.READ
    risk_level: RiskLevel = RiskLevel.L0
    risk_domains: frozenset[RiskDomain] = frozenset()
    required_roles: frozenset[str] = frozenset()
    required_scopes: frozenset[str] = frozenset()
    approval_policy: str | None = None
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    handler: str = ""
    audit_level: str = "standard"
    constraints: ToolConstraints = field(default_factory=ToolConstraints)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ToolSpec:
        return cls(
            operation_type=OperationType(raw.get("operation_type", "read")),
            risk_level=RiskLevel(raw.get("risk_level", "L0")),
            risk_domains=frozenset(
                RiskDomain(d) for d in raw.get("risk_domains", ())
            ),
            required_roles=frozenset(raw.get("required_roles", ())),
            required_scopes=frozenset(raw.get("required_scopes", ())),
            approval_policy=raw.get("approval_policy"),
            input_schema=raw.get("input_schema", {}) or {},
            output_schema=raw.get("output_schema", {}) or {},
            handler=raw.get("handler", ""),
            audit_level=raw.get("audit_level", "standard"),
            constraints=ToolConstraints.from_dict(raw.get("constraints", {}) or {}),
        )


@dataclass(frozen=True)
class ToolDefinition:
    """Governed metadata composed with a tool-specific spec."""

    metadata: GovernedResourceMetadata
    spec: ToolSpec

    # -- governance proxies --------------------------------------------------

    @property
    def tool_id(self) -> str:
        return self.metadata.resource_id

    @property
    def version(self) -> Version:
        return self.metadata.version

    @property
    def status(self) -> ResourceStatus:
        return self.metadata.status

    @property
    def availability(self) -> ExecutionAvailability:
        return self.metadata.availability

    @property
    def enabled(self) -> bool:
        """Computed from lifecycle status, never stored independently."""
        return self.metadata.enabled

    @property
    def owner(self) -> str:
        return self.metadata.owner.owner_id

    @property
    def description(self) -> str:
        return self.metadata.description

    # -- spec proxies --------------------------------------------------------

    @property
    def operation_type(self) -> OperationType:
        return self.spec.operation_type

    @property
    def risk_level(self) -> RiskLevel:
        return self.spec.risk_level

    @property
    def risk_domains(self) -> frozenset[RiskDomain]:
        return self.spec.risk_domains

    @property
    def required_roles(self) -> frozenset[str]:
        return self.spec.required_roles

    @property
    def required_scopes(self) -> frozenset[str]:
        return self.spec.required_scopes

    @property
    def approval_policy(self) -> str | None:
        return self.spec.approval_policy

    @property
    def input_schema(self) -> dict[str, Any]:
        return self.spec.input_schema

    @property
    def output_schema(self) -> dict[str, Any]:
        return self.spec.output_schema

    @property
    def handler(self) -> str:
        return self.spec.handler

    @property
    def audit_level(self) -> str:
        return self.spec.audit_level

    @property
    def constraints(self) -> ToolConstraints:
        return self.spec.constraints

    # -- lifecycle support ---------------------------------------------------

    def with_metadata(self, metadata: GovernedResourceMetadata) -> ToolDefinition:
        return replace(self, metadata=metadata)

    def with_spec_changes(self, changes: dict[str, Any]) -> ToolDefinition:
        return replace(self, spec=replace(self.spec, **changes))

    def missing_roles(self, identity: Identity) -> frozenset[str]:
        return frozenset(self.required_roles - identity.roles)

    def missing_scopes(self, granted: frozenset[str]) -> frozenset[str]:
        return frozenset(self.required_scopes - granted)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.metadata.to_dict(),
            "tool_id": self.tool_id,
            "operation_type": self.operation_type.value,
            "risk_level": self.risk_level.value,
            "risk_domains": sorted(d.value for d in self.risk_domains),
            "audit_level": self.audit_level,
            "availability": self.availability.value,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ToolDefinition:
        try:
            resource_id = raw["tool_id"]
        except (KeyError, TypeError) as exc:
            raise ToolRegistryError(f"malformed tool definition: {exc}") from exc
        try:
            return cls(
                metadata=GovernedResourceMetadata.from_dict(
                    raw, RegistryType.TOOL, resource_id
                ),
                spec=ToolSpec.from_dict(raw),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolRegistryError(
                f"malformed tool definition '{resource_id}': {exc}"
            ) from exc


def validate_tool(resource: Any) -> list[str]:
    """Validation hook run before promotion."""
    problems: list[str] = []
    if not isinstance(resource, ToolDefinition):
        return ["not a tool definition"]
    if not resource.handler:
        problems.append(f"tool '{resource.tool_id}' declares no handler")
    if not resource.metadata.owner.owner_id or (
        resource.metadata.owner.owner_id == "unassigned"
    ):
        problems.append(f"tool '{resource.tool_id}' has no accountable owner")
    return problems


class ToolRegistry:
    """Tool-specific facade over the shared RegistryService.

    Lookup is by ``tool_id``. An unregistered id is not a tool — the registry
    returns None and the gateway rejects, rather than dispatching on a name an
    agent produced.
    """

    def __init__(self, tools: tuple[ToolDefinition, ...] = ()) -> None:
        self.service = RegistryService(
            RegistryType.TOOL, validators=(validate_tool,)
        )
        for tool in tools:
            if self.service.get(tool.tool_id, tool.version) is not None:
                raise ToolRegistryError(
                    f"duplicate tool_id in registry: {tool.tool_id}"
                )
            self.service.register(tool, principal="bootstrap")

    @classmethod
    def from_file(cls, path: str | Path) -> ToolRegistry:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ToolRegistryError(
                f"cannot read tool registry at {path}: {exc}"
            ) from exc
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolRegistry:
        raw = data.get("tools")
        if not isinstance(raw, list):
            raise ToolRegistryError("tool registry must contain a 'tools' array")

        seen: set[str] = set()
        for entry in raw:
            tool_id = entry.get("tool_id") if isinstance(entry, dict) else None
            if tool_id in seen:
                raise ToolRegistryError(
                    f"duplicate tool_id in registry: {tool_id}"
                )
            if tool_id is not None:
                seen.add(tool_id)

        return cls(tuple(ToolDefinition.from_dict(t) for t in raw))

    # -- read ----------------------------------------------------------------

    def get(
        self, tool_id: str, version: str | Version | None = None
    ) -> ToolDefinition | None:
        result = self.service.get(tool_id, version)
        return result  # type: ignore[return-value]

    def get_for_execution(
        self, tool_id: str, environment: str | None = None
    ) -> ToolDefinition | None:
        """The version operationally available in an environment, if any."""
        result = self.service.get_active(tool_id, environment)
        return result  # type: ignore[return-value]

    def require(self, tool_id: str) -> ToolDefinition:
        tool = self.get(tool_id)
        if tool is None:
            raise ToolRegistryError(f"unknown tool: {tool_id}")
        return tool

    def is_registered(self, tool_id: str) -> bool:
        return self.get(tool_id) is not None

    def enabled_tools(self) -> tuple[ToolDefinition, ...]:
        return tuple(t for t in self if t.enabled)  # type: ignore[misc]

    def versions_of(self, tool_id: str) -> tuple[ToolDefinition, ...]:
        return self.service.versions_of(tool_id)  # type: ignore[return-value]

    def __len__(self) -> int:
        return len({t.tool_id for t in self})  # type: ignore[misc]

    def __iter__(self):
        return iter(self.service)

    def __contains__(self, tool_id: object) -> bool:
        return isinstance(tool_id, str) and self.is_registered(tool_id)
