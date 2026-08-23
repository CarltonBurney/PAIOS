"""Policy engine.

Policies are declarative records loaded from JSON, never code. Each policy is
scoped (department / agent / environment), conditioned (risk level and domain),
and carries effects (approval requirement, tool permissions, audit level).

Merge semantics, applied across every policy that matches:

- ``decision``         — strongest wins, by the precedence
  ``deny > require_approval > allow_with_controls > allow``. A deny from any
  applicable policy is final; approval can never override it.
- ``denied_tools``     — union. A denial anywhere is a denial everywhere.
- ``allowed_tools``    — intersection of every allow-list that is specified.
  A policy with no allow-list imposes no constraint; a policy with one narrows
  the permitted set. This is least-privilege: adding a policy can never widen
  what a caller may do.
- ``audit_level``      — maximum, ordered minimal < standard < full.

Policies are evaluated in descending ``priority`` for deterministic reporting,
but the merge itself is order-independent by construction, so two policies at
the same priority cannot produce different results depending on file order.

DESIGN RULE — risk never authorizes execution.
----------------------------------------------
A low risk level is not permission. ``RiskLevel`` is one *input* to the policy
decision, alongside identity, environment, and the tool contract; it is never
a shortcut past them. Code of the shape ``if risk <= L1: execute()`` would
collapse the control plane into a scoring function and must not be written.
Execution is authorized only by the Execution Gateway, which re-validates
independently of whatever risk said.
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
    PolicyOutcome,
    Request,
    RequestType,
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
    """Empty condition sets mean "any" — an unconditioned policy always fires.

    ``risk_domains`` matches on OVERLAP, never exact equality: a policy scoped
    to ``["security"]`` fires on a request carrying
    ``["security", "compliance"]``. Formally::

        policy_domains ∩ request_domains ≠ ∅

    A future schema revision may add explicit ``any_of`` / ``all_of`` /
    ``none_of`` operators. Until then the bare array always means any_of, and
    must never be silently reinterpreted as exact equality.
    """

    risk_level: frozenset[RiskLevel] = frozenset()
    risk_domains: frozenset[RiskDomain] = frozenset()
    request_types: frozenset[RequestType] = frozenset()
    principal_roles_none_of: frozenset[str] = frozenset()

    def matches(
        self,
        risk: RiskAssessment,
        classification: Classification | None = None,
        identity: Identity | None = None,
    ) -> bool:
        if self.risk_level and risk.level not in self.risk_level:
            return False
        if self.risk_domains and not (self.risk_domains & risk.domains):
            return False
        if self.request_types:
            if classification is None:
                return False
            if classification.request_type not in self.request_types:
                return False
        if self.principal_roles_none_of:
            if identity is None:
                return False
            # Fires only when the principal holds NONE of the listed roles.
            if identity.roles & self.principal_roles_none_of:
                return False
        return True


@dataclass(frozen=True)
class PolicyEffects:
    decision: PolicyOutcome = PolicyOutcome.ALLOW
    allowed_tools: tuple[str, ...] = ()
    denied_tools: tuple[str, ...] = ()
    audit_level: str = "standard"
    reason_code: str | None = None


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

            # `decision` is authoritative. The older boolean `require_approval`
            # is still honoured so existing policy documents keep working, but
            # it can only ever raise the outcome, never lower it.
            if "decision" in eff_raw:
                decision = PolicyOutcome(eff_raw["decision"])
                if eff_raw.get("require_approval") and decision.precedence < (
                    PolicyOutcome.REQUIRE_APPROVAL.precedence
                ):
                    decision = PolicyOutcome.REQUIRE_APPROVAL
            elif eff_raw.get("require_approval"):
                decision = PolicyOutcome.REQUIRE_APPROVAL
            else:
                decision = PolicyOutcome.ALLOW

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
                    request_types=frozenset(
                        RequestType(v) for v in cond_raw.get("request_types", ())
                    ),
                    principal_roles_none_of=frozenset(
                        cond_raw.get("principal_roles_none_of", ())
                    ),
                ),
                effects=PolicyEffects(
                    decision=decision,
                    allowed_tools=tuple(eff_raw.get("allowed_tools", ())),
                    denied_tools=tuple(eff_raw.get("denied_tools", ())),
                    audit_level=audit_level,
                    reason_code=eff_raw.get("reason_code"),
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
        reason_codes: list[str] = []
        outcomes: list[PolicyOutcome] = []
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
            if not policy.conditions.matches(
                risk, classification, request.identity
            ):
                continue

            matched.append(policy.policy_id)
            effects = policy.effects
            outcomes.append(effects.decision)

            if effects.reason_code:
                reason_codes.append(effects.reason_code)

            denied.update(effects.denied_tools)

            if effects.allowed_tools:
                incoming = frozenset(effects.allowed_tools)
                allowed = incoming if allowed is None else (allowed & incoming)

            audit_rank = max(audit_rank, _AUDIT_ORDER.index(effects.audit_level))

        return PolicyDecision(
            decision=PolicyOutcome.strongest(outcomes),
            matched=tuple(matched),
            reason_codes=tuple(dict.fromkeys(reason_codes)),
            allowed_tools=allowed,
            denied_tools=frozenset(denied),
            audit_level=_AUDIT_ORDER[audit_rank],
        )


def _department_of(identity: Identity) -> str | None:
    return identity.department
