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
class SetPredicate:
    """A predicate over a set of values. Three operators, uniform semantics.

    Conditions describe *predicates*, never business meanings. The policy
    language grows by adding operators to this type, not by adding
    special-purpose fields like ``principal_is_governance_admin``.

    - ``any_of``  — candidate ∩ any_of ≠ ∅   (the default for a bare array)
    - ``all_of``  — all_of ⊆ candidate
    - ``none_of`` — candidate ∩ none_of = ∅

    An operator left empty imposes no constraint. An empty predicate matches
    everything.
    """

    any_of: frozenset[str] = frozenset()
    all_of: frozenset[str] = frozenset()
    none_of: frozenset[str] = frozenset()

    @property
    def is_empty(self) -> bool:
        return not (self.any_of or self.all_of or self.none_of)

    def matches(self, candidate: frozenset[str]) -> bool:
        if self.any_of and not (self.any_of & candidate):
            return False
        if self.all_of and not self.all_of.issubset(candidate):
            return False
        if self.none_of and (self.none_of & candidate):
            return False
        return True

    @classmethod
    def from_conditions(
        cls, raw: dict[str, Any], base: str, *, legacy_keys: tuple[str, ...] = ()
    ) -> SetPredicate:
        """Read ``<base>_any_of`` / ``_all_of`` / ``_none_of`` from a document.

        ``legacy_keys`` names older bare-array fields that mean any_of. A bare
        array has always meant any_of and must never be reinterpreted as exact
        equality.
        """
        any_of = set(raw.get(f"{base}_any_of", ()))
        for legacy in legacy_keys:
            any_of.update(raw.get(legacy, ()))
        return cls(
            any_of=frozenset(any_of),
            all_of=frozenset(raw.get(f"{base}_all_of", ())),
            none_of=frozenset(raw.get(f"{base}_none_of", ())),
        )


def _validate_members(predicate: SetPredicate, enum_cls: type, field: str) -> None:
    """Fail loudly on a value the enum does not define, rather than never matching."""
    valid = {member.value for member in enum_cls}
    unknown = (predicate.any_of | predicate.all_of | predicate.none_of) - valid
    if unknown:
        raise ValueError(
            f"unknown {field} value(s): {', '.join(sorted(unknown))}; "
            f"expected from {sorted(valid)}"
        )


@dataclass(frozen=True)
class PolicyConditions:
    """Predicate set. An empty condition block matches every request.

    Every family uses the same three operators — see SetPredicate. Bare legacy
    arrays (``risk_domains``, ``request_types``, ``principal_roles_none_of``)
    are still read and map onto the normalized form.
    """

    risk_level: SetPredicate = field(default_factory=SetPredicate)
    risk_domains: SetPredicate = field(default_factory=SetPredicate)
    request_types: SetPredicate = field(default_factory=SetPredicate)
    principal_roles: SetPredicate = field(default_factory=SetPredicate)
    principal_groups: SetPredicate = field(default_factory=SetPredicate)

    # Generalized attribute predicates. Any `<name>_any_of` / `_all_of` /
    # `_none_of` key that is not one of the families above becomes an entry
    # here, so new decision dimensions (registry_operation, target_environment,
    # data_classification, …) need no schema change. An attribute condition
    # whose candidate set the caller does not supply does NOT match — a
    # condition we cannot evaluate must never silently pass.
    attributes: dict[str, SetPredicate] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PolicyConditions:
        risk_level = SetPredicate.from_conditions(
            raw, "risk_level", legacy_keys=("risk_level",)
        )
        risk_domains = SetPredicate.from_conditions(
            raw, "risk_domains", legacy_keys=("risk_domains",)
        )
        request_types = SetPredicate.from_conditions(
            raw, "request_types", legacy_keys=("request_types",)
        )
        _validate_members(risk_level, RiskLevel, "risk_level")
        _validate_members(risk_domains, RiskDomain, "risk_domains")
        _validate_members(request_types, RequestType, "request_types")

        known = {
            "risk_level",
            "risk_domains",
            "request_types",
            "principal_roles",
            "principal_groups",
        }
        attribute_names: set[str] = set()
        for key in raw:
            for suffix in ("_any_of", "_all_of", "_none_of"):
                if key.endswith(suffix):
                    base = key[: -len(suffix)]
                    if base not in known:
                        attribute_names.add(base)

        return cls(
            risk_level=risk_level,
            risk_domains=risk_domains,
            request_types=request_types,
            principal_roles=SetPredicate.from_conditions(raw, "principal_roles"),
            principal_groups=SetPredicate.from_conditions(raw, "principal_groups"),
            attributes={
                name: SetPredicate.from_conditions(raw, name)
                for name in sorted(attribute_names)
            },
        )

    def matches(
        self,
        risk: RiskAssessment,
        classification: Classification | None = None,
        identity: Identity | None = None,
        attributes: dict[str, frozenset[str]] | None = None,
    ) -> bool:
        if not self.risk_level.matches(frozenset({risk.level.value})):
            return False
        if not self.risk_domains.matches(frozenset(d.value for d in risk.domains)):
            return False

        if not self.request_types.is_empty:
            if classification is None:
                return False
            candidate = frozenset({classification.request_type.value})
            if not self.request_types.matches(candidate):
                return False

        if not self.principal_roles.is_empty:
            if identity is None:
                return False
            if not self.principal_roles.matches(frozenset(identity.roles)):
                return False

        if not self.principal_groups.is_empty:
            if identity is None:
                return False
            if not self.principal_groups.matches(frozenset(identity.groups)):
                return False

        for name, predicate in self.attributes.items():
            candidate = (attributes or {}).get(name)
            if candidate is None:
                return False
            if not predicate.matches(candidate):
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
                conditions=PolicyConditions.from_dict(cond_raw),
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
        attributes: dict[str, frozenset[str]] | None = None,
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
                risk, classification, request.identity, attributes
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
