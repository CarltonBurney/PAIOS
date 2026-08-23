"""Policy engine.

Policies are declarative records loaded from JSON, never code. Each policy is
scoped (department / agent / environment), conditioned (risk level and domain),
and carries effects (approval requirement, tool permissions, audit level).

Merge semantics, applied across every policy that matches:

- ``require_approval`` — logical OR. Any policy demanding approval wins.
- ``denied_tools``     — union. A denial anywhere is a denial everywhere.
- ``allowed_tools``    — intersection of every allow-list that is specified.
  A policy with no allow-list imposes no constraint; a policy with one narrows
  the permitted set. This is least-privilege: adding a policy can never widen
  what a caller may do.
- ``audit_level``      — maximum, ordered minimal < standard < full.

Policies are evaluated in descending ``priority`` for deterministic reporting,
but the merge itself is order-independent by construction, so two policies at
the same priority cannot produce different results depending on file order.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import (
    Agent,
    Classification,
    Identity,
    PolicyDecision,
    Request,
    RiskAssessment,
    RiskDomain,
    RiskLevel,
)

WILDCARD = "*"

_AUDIT_ORDER = ("minimal", "standard", "full")


class PolicyError(ValueError):
    """Raised when a policy document is malformed."""


@dataclass(frozen=True)
class PolicyScope:
    departments: tuple[str, ...] = (WILDCARD,)
    agents: tuple[str, ...] = (WILDCARD,)
    environments: tuple[str, ...] = (WILDCARD,)

    @staticmethod
    def _match(values: tuple[str, ...], candidate: str | None) -> bool:
        if WILDCARD in values:
            return True
        if candidate is None:
            return False
        return candidate in values

    def matches(
        self,
        *,
        department: str | None,
        agent: str | None,
        environment: str | None,
    ) -> bool:
        return (
            self._match(self.departments, department)
            and self._match(self.agents, agent)
            and self._match(self.environments, environment)
        )


@dataclass(frozen=True)
class PolicyConditions:
    """Empty condition sets mean "any" — an unconditioned policy always fires."""

    risk_level: frozenset[RiskLevel] = frozenset()
    risk_domains: frozenset[RiskDomain] = frozenset()

    def matches(self, risk: RiskAssessment) -> bool:
        if self.risk_level and risk.level not in self.risk_level:
            return False
        # Domains match on overlap: a policy scoped to "security" fires on a
        # request that is both a security and a compliance matter.
        if self.risk_domains and not (self.risk_domains & risk.domains):
            return False
        return True


@dataclass(frozen=True)
class PolicyEffects:
    require_approval: bool = False
    allowed_tools: tuple[str, ...] = ()
    denied_tools: tuple[str, ...] = ()
    audit_level: str = "standard"


@dataclass(frozen=True)
class Policy:
    policy_id: str
    enabled: bool = True
    priority: int = 0
    scope: PolicyScope = field(default_factory=PolicyScope)
    conditions: PolicyConditions = field(default_factory=PolicyConditions)
    effects: PolicyEffects = field(default_factory=PolicyEffects)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Policy:
        try:
            scope_raw = data.get("scope", {}) or {}
            cond_raw = data.get("conditions", {}) or {}
            eff_raw = data.get("effects", {}) or {}

            audit_level = eff_raw.get("audit_level", "standard")
            if audit_level not in _AUDIT_ORDER:
                raise ValueError(
                    f"audit_level must be one of {_AUDIT_ORDER}, got {audit_level!r}"
                )

            return cls(
                policy_id=data["policy_id"],
                enabled=bool(data.get("enabled", True)),
                priority=int(data.get("priority", 0)),
                scope=PolicyScope(
                    departments=tuple(scope_raw.get("departments", (WILDCARD,))),
                    agents=tuple(scope_raw.get("agents", (WILDCARD,))),
                    environments=tuple(scope_raw.get("environments", (WILDCARD,))),
                ),
                conditions=PolicyConditions(
                    risk_level=frozenset(
                        RiskLevel(v) for v in cond_raw.get("risk_level", ())
                    ),
                    risk_domains=frozenset(
                        RiskDomain(v) for v in cond_raw.get("risk_domains", ())
                    ),
                ),
                effects=PolicyEffects(
                    require_approval=bool(eff_raw.get("require_approval", False)),
                    allowed_tools=tuple(eff_raw.get("allowed_tools", ())),
                    denied_tools=tuple(eff_raw.get("denied_tools", ())),
                    audit_level=audit_level,
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PolicyError(f"malformed policy: {exc}") from exc


@dataclass(frozen=True)
class PolicySet:
    name: str
    description: str
    policies: tuple[Policy, ...]

    @classmethod
    def from_file(cls, path: str | Path) -> PolicySet:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PolicyError(f"cannot read policy set at {path}: {exc}") from exc
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PolicySet:
        raw = data.get("policies")
        if not isinstance(raw, list):
            raise PolicyError("policy set must contain a 'policies' array")
        return cls(
            name=data.get("policySetName", "unnamed"),
            description=data.get("description", ""),
            policies=tuple(Policy.from_dict(p) for p in raw),
        )


class PolicyEngine:
    def __init__(self, policy_set: PolicySet) -> None:
        self._set = policy_set

    @property
    def policy_set(self) -> PolicySet:
        return self._set

    def evaluate(
        self,
        request: Request,
        classification: Classification,
        risk: RiskAssessment,
        *,
        agent: Agent | None = None,
        environment: str | None = None,
    ) -> PolicyDecision:
        department = _department_of(request.identity)
        agent_name = agent.value if agent else None

        candidates = sorted(
            (p for p in self._set.policies if p.enabled),
            key=lambda p: (-p.priority, p.policy_id),
        )

        matched: list[str] = []
        require_approval = False
        allowed: frozenset[str] | None = None
        denied: set[str] = set()
        audit_rank = 0

        for policy in candidates:
            if not policy.scope.matches(
                department=department,
                agent=agent_name,
                environment=environment,
            ):
                continue
            if not policy.conditions.matches(risk):
                continue

            matched.append(policy.policy_id)
            effects = policy.effects

            require_approval = require_approval or effects.require_approval
            denied.update(effects.denied_tools)

            if effects.allowed_tools:
                incoming = frozenset(effects.allowed_tools)
                allowed = incoming if allowed is None else (allowed & incoming)

            audit_rank = max(audit_rank, _AUDIT_ORDER.index(effects.audit_level))

        return PolicyDecision(
            matched=tuple(matched),
            require_approval=require_approval,
            allowed_tools=allowed,
            denied_tools=frozenset(denied),
            audit_level=_AUDIT_ORDER[audit_rank],
        )


def _department_of(identity: Identity) -> str | None:
    return identity.department
