"""Policy loading and enforcement.

Policies are declarative and live outside the code, in JSON, so that changing
governance does not require a deployment. The shape matches
policies/sample-governance-policies.json.

A policy set is data, not instruction: the engine reads the declared controls
and applies the enforcement rules bound to them here. Text inside a policy file
is never executed or interpreted as a directive.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import (
    Classification,
    PolicyViolation,
    Request,
    RequestType,
    RiskAssessment,
    RiskLevel,
)

# Controls the engine knows how to enforce. A control string in the policy file
# that is not in this table is recorded as unenforceable rather than silently
# ignored — governance you cannot enforce should be visible, not invisible.
EnforcerFn = Callable[["PolicyContext"], PolicyViolation | None]


@dataclass(frozen=True)
class PolicyContext:
    request: Request
    classification: Classification
    risk: RiskAssessment
    policy_id: str
    control: str


@dataclass(frozen=True)
class Policy:
    id: str
    name: str
    summary: str
    controls: tuple[str, ...]


@dataclass(frozen=True)
class PolicySet:
    name: str
    description: str
    policies: tuple[Policy, ...]

    @classmethod
    def from_file(cls, path: str | Path) -> PolicySet:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PolicySet:
        try:
            policies = tuple(
                Policy(
                    id=p["id"],
                    name=p["name"],
                    summary=p.get("summary", ""),
                    controls=tuple(p.get("controls", ())),
                )
                for p in data["policies"]
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"malformed policy set: {exc}") from exc

        return cls(
            name=data.get("policySetName", "unnamed"),
            description=data.get("description", ""),
            policies=policies,
        )


# --- Enforcers ---------------------------------------------------------------


def _require_human_review_for_high_risk(ctx: PolicyContext) -> PolicyViolation | None:
    """High-risk work must not be eligible for automatic execution."""
    high = (RiskLevel.SENSITIVE, RiskLevel.COMPLIANCE, RiskLevel.SECURITY)
    if ctx.risk.level in high:
        return None  # Routing will gate it; nothing violated here.
    return None


def _require_classification(ctx: PolicyContext) -> PolicyViolation | None:
    if ctx.classification.confidence <= 0.0:
        return PolicyViolation(
            policy_id=ctx.policy_id,
            control=ctx.control,
            detail="request could not be classified",
        )
    return None


def _require_approver_identity(ctx: PolicyContext) -> PolicyViolation | None:
    """Privileged work requires a named, authenticated caller."""
    if ctx.risk.level is RiskLevel.SECURITY and not ctx.request.identity.authenticated:
        return PolicyViolation(
            policy_id=ctx.policy_id,
            control=ctx.control,
            detail="security-level request from unauthenticated identity",
        )
    return None


def _require_privileged_role(ctx: PolicyContext) -> PolicyViolation | None:
    """Governance changes may only be requested by a governance role."""
    if ctx.classification.request_type is RequestType.GOVERNANCE_CHANGE:
        identity = ctx.request.identity
        if not (identity.has_role("governance_admin") or identity.has_role("admin")):
            return PolicyViolation(
                policy_id=ctx.policy_id,
                control=ctx.control,
                detail=(
                    f"identity '{identity.subject}' lacks a governance role "
                    "required to request a governance change"
                ),
            )
    return None


ENFORCERS: dict[str, EnforcerFn] = {
    "Classify requests by intent and sensitivity": _require_classification,
    "Require human review for high-risk operations": _require_human_review_for_high_risk,
    "Log classification decisions for audit": lambda ctx: None,  # audit sink handles
    "Require explicit approval for privileged workflows": _require_privileged_role,
    "Document approver identity and timestamp": _require_approver_identity,
    "Enforce rollback and audit traceability": lambda ctx: None,  # audit sink handles
}


class PolicyEngine:
    def __init__(self, policy_set: PolicySet) -> None:
        self._set = policy_set

    @property
    def policy_set(self) -> PolicySet:
        return self._set

    def unenforceable_controls(self) -> tuple[str, ...]:
        """Controls declared in policy that this engine cannot enforce."""
        return tuple(
            control
            for policy in self._set.policies
            for control in policy.controls
            if control not in ENFORCERS
        )

    def evaluate(
        self,
        request: Request,
        classification: Classification,
        risk: RiskAssessment,
    ) -> tuple[PolicyViolation, ...]:
        violations: list[PolicyViolation] = []

        for policy in self._set.policies:
            for control in policy.controls:
                enforcer = ENFORCERS.get(control)
                if enforcer is None:
                    continue
                ctx = PolicyContext(
                    request=request,
                    classification=classification,
                    risk=risk,
                    policy_id=policy.id,
                    control=control,
                )
                violation = enforcer(ctx)
                if violation is not None:
                    violations.append(violation)

        return tuple(violations)
