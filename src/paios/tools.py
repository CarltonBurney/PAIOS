"""Tool Registry — first-class governed tool definitions.

A tool is a governed capability, not a function an agent can name into
existence. Agents may *reference* registered tools; they may not create
executable tool identities. Anything not in the registry cannot run, which is
what makes the tool surface auditable.

Every definition carries its own governance metadata — risk level and domains,
required roles and scopes, approval policy, audit level — so the Execution
Gateway can re-validate a call against the tool's own contract rather than
trusting whatever the caller supplied.

Extension points are declared but not yet enforced (environment restrictions,
data classifications, network restrictions, timeout, retries, rate limits,
idempotency, caller types, allowed agents, allowed workflows). They are parsed
and carried so registry documents can start expressing them, and the gateway
can begin honouring them without a schema migration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .models import Identity, RiskDomain, RiskLevel


class ToolRegistryError(ValueError):
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
    """Declared extension points. Parsed and carried; enforcement is staged."""

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


@dataclass(frozen=True)
class ToolDefinition:
    tool_id: str
    version: str = "1.0.0"
    enabled: bool = True
    owner: str = ""
    description: str = ""
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

    def missing_roles(self, identity: Identity) -> frozenset[str]:
        return frozenset(self.required_roles - identity.roles)

    def missing_scopes(self, granted: frozenset[str]) -> frozenset[str]:
        return frozenset(self.required_scopes - granted)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "version": self.version,
            "enabled": self.enabled,
            "owner": self.owner,
            "operation_type": self.operation_type.value,
            "risk_level": self.risk_level.value,
            "risk_domains": sorted(d.value for d in self.risk_domains),
            "audit_level": self.audit_level,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolDefinition:
        try:
            raw_constraints = data.get("constraints", {}) or {}
            return cls(
                tool_id=data["tool_id"],
                version=str(data.get("version", "1.0.0")),
                enabled=bool(data.get("enabled", True)),
                owner=data.get("owner", ""),
                description=data.get("description", ""),
                operation_type=OperationType(data.get("operation_type", "read")),
                risk_level=RiskLevel(data.get("risk_level", "L0")),
                risk_domains=frozenset(
                    RiskDomain(d) for d in data.get("risk_domains", ())
                ),
                required_roles=frozenset(data.get("required_roles", ())),
                required_scopes=frozenset(data.get("required_scopes", ())),
                approval_policy=data.get("approval_policy"),
                input_schema=data.get("input_schema", {}) or {},
                output_schema=data.get("output_schema", {}) or {},
                handler=data.get("handler", ""),
                audit_level=data.get("audit_level", "standard"),
                constraints=ToolConstraints(
                    environments=tuple(raw_constraints.get("environments", ("*",))),
                    data_classifications=tuple(
                        raw_constraints.get("data_classifications", ())
                    ),
                    network_destinations=tuple(
                        raw_constraints.get("network_destinations", ())
                    ),
                    timeout_seconds=raw_constraints.get("timeout_seconds"),
                    max_retries=int(raw_constraints.get("max_retries", 0)),
                    rate_limit_per_minute=raw_constraints.get(
                        "rate_limit_per_minute"
                    ),
                    idempotent=bool(raw_constraints.get("idempotent", False)),
                    caller_types=frozenset(
                        CallerType(c) for c in raw_constraints.get("caller_types", ())
                    ),
                    allowed_agents=tuple(
                        raw_constraints.get("allowed_agents", ("*",))
                    ),
                    allowed_workflows=tuple(
                        raw_constraints.get("allowed_workflows", ("*",))
                    ),
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolRegistryError(f"malformed tool definition: {exc}") from exc


class ToolRegistry:
    """The authoritative set of tools that may be invoked.

    Lookup is by ``tool_id``. An unregistered id is not a tool — the registry
    returns None and the gateway rejects, rather than attempting a dispatch on
    a name an agent produced.
    """

    def __init__(self, tools: tuple[ToolDefinition, ...] = ()) -> None:
        duplicates = _duplicates(t.tool_id for t in tools)
        if duplicates:
            raise ToolRegistryError(
                f"duplicate tool_id in registry: {', '.join(sorted(duplicates))}"
            )
        self._tools: dict[str, ToolDefinition] = {t.tool_id: t for t in tools}

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
        return cls(tuple(ToolDefinition.from_dict(t) for t in raw))

    def get(self, tool_id: str) -> ToolDefinition | None:
        return self._tools.get(tool_id)

    def require(self, tool_id: str) -> ToolDefinition:
        tool = self.get(tool_id)
        if tool is None:
            raise ToolRegistryError(f"unknown tool: {tool_id}")
        return tool

    def is_registered(self, tool_id: str) -> bool:
        return tool_id in self._tools

    def enabled_tools(self) -> tuple[ToolDefinition, ...]:
        return tuple(t for t in self._tools.values() if t.enabled)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, tool_id: object) -> bool:
        return isinstance(tool_id, str) and tool_id in self._tools


def _duplicates(values: Any) -> set[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for value in values:
        if value in seen:
            dupes.add(value)
        seen.add(value)
    return dupes
