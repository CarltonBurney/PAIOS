"""Core domain types for the PAIOS control plane.

These types are the vocabulary the whole control plane speaks. They mirror the
categories defined in architecture/workflows/request-classification-flow.md so
that the code and the documentation stay in step.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class RequestType(str, Enum):
    """Classification categories. See request-classification-flow.md."""

    PROJECT = "project"
    TECHNICAL = "technical"
    LOGICAL = "logical"
    CORE = "core"
    GOVERNANCE_CHANGE = "governance_change"


class RiskLevel(str, Enum):
    """Impact scale — how much a request can affect, ordered L0 to L4.

    This axis answers "how much damage could this do", nothing else. What
    *kind* of concern a request raises is carried separately on RiskDomain,
    because a request can raise several kinds at once and they do not sit on
    one line.
    """

    L0 = "L0"  # informational / read-only / public
    L1 = "L1"  # low-impact internal
    L2 = "L2"  # controlled business action
    L3 = "L3"  # sensitive / high-impact, approval required
    L4 = "L4"  # prohibited, or executive / security escalation

    @property
    def rank(self) -> int:
        return _RISK_ORDER.index(self)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.rank < other.rank

    def __le__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.rank <= other.rank

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.rank > other.rank

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.rank >= other.rank


_RISK_ORDER = [
    RiskLevel.L0,
    RiskLevel.L1,
    RiskLevel.L2,
    RiskLevel.L3,
    RiskLevel.L4,
]


class RiskDomain(str, Enum):
    """Kind of concern a request raises.

    Orthogonal to RiskLevel and non-exclusive: one request can be both a
    security and a compliance matter, at any impact level.
    """

    SECURITY = "security"
    COMPLIANCE = "compliance"
    PRIVACY = "privacy"
    FINANCIAL = "financial"
    OPERATIONAL = "operational"
    GOVERNANCE = "governance"


class Disposition(str, Enum):
    """What the control plane decided to do with a request."""

    AUTO_EXECUTE = "auto_execute"
    HUMAN_REVIEW = "human_review"
    ADMIN_ESCALATION = "admin_escalation"
    BLOCKED = "blocked"


class ApprovalState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"


class Agent(str, Enum):
    """Execution targets. Each request type has a default agent."""

    PROJECT = "project_agent"
    TECHNICAL = "technical_agent"
    LOGICAL = "logical_agent"
    CORE = "core_agent"


@dataclass(frozen=True)
class Identity:
    """The caller. Populated from Entra ID in an Azure deployment."""

    subject: str
    roles: frozenset[str] = frozenset()
    authenticated: bool = False
    tenant_id: str | None = None
    department: str | None = None
    groups: frozenset[str] = frozenset()

    def has_role(self, role: str) -> bool:
        return role in self.roles


@dataclass(frozen=True)
class Request:
    """An inbound unit of work."""

    content: str
    identity: Identity
    id: str = field(default_factory=lambda: _new_id("req"))
    metadata: dict[str, Any] = field(default_factory=dict)
    received_at: datetime = field(default_factory=_now)


@dataclass(frozen=True)
class Classification:
    request_type: RequestType
    confidence: float
    signals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0,1], got {self.confidence}")


@dataclass(frozen=True)
class RiskAssessment:
    """The two-axis risk verdict: how much impact, and what kinds of concern."""

    level: RiskLevel
    domains: frozenset[RiskDomain] = frozenset()
    triggers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Canonical serialization, as carried on audit records."""
        return {
            "risk_level": self.level.value,
            "risk_domains": sorted(d.value for d in self.domains),
        }

    def in_domain(self, domain: RiskDomain) -> bool:
        return domain in self.domains


@dataclass(frozen=True)
class PolicyViolation:
    """An authorization failure. Blocks the request outright."""

    policy_id: str
    control: str
    detail: str


@dataclass(frozen=True)
class PolicyDecision:
    """The merged effects of every policy that matched a request."""

    matched: tuple[str, ...] = ()
    require_approval: bool = False
    allowed_tools: frozenset[str] | None = None
    denied_tools: frozenset[str] = frozenset()
    audit_level: str = "standard"

    def permits_tool(self, tool: str) -> bool:
        """Deny always wins; an absent allow-list means no allow constraint."""
        if tool in self.denied_tools:
            return False
        if self.allowed_tools is None:
            return True
        return tool in self.allowed_tools

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched_policies": list(self.matched),
            "require_approval": self.require_approval,
            "allowed_tools": (
                sorted(self.allowed_tools) if self.allowed_tools is not None else None
            ),
            "denied_tools": sorted(self.denied_tools),
            "audit_level": self.audit_level,
        }


@dataclass(frozen=True)
class RoutingDecision:
    disposition: Disposition
    agent: Agent | None = None
    reason: str = ""

    @property
    def requires_human(self) -> bool:
        return self.disposition in (
            Disposition.HUMAN_REVIEW,
            Disposition.ADMIN_ESCALATION,
        )


@dataclass(frozen=True)
class Approval:
    state: ApprovalState
    approver: str | None = None
    decided_at: datetime | None = None
    note: str = ""


@dataclass(frozen=True)
class Execution:
    output: str
    agent: Agent
    provider: str
    model: str
    completed_at: datetime = field(default_factory=_now)


@dataclass(frozen=True)
class Outcome:
    """The full record of one request's passage through the control plane."""

    request: Request
    classification: Classification | None = None
    risk: RiskAssessment | None = None
    routing: RoutingDecision | None = None
    approval: Approval | None = None
    execution: Execution | None = None
    violations: tuple[PolicyViolation, ...] = ()
    policy: PolicyDecision | None = None
    audit_ids: tuple[str, ...] = ()

    @property
    def delivered(self) -> bool:
        return self.execution is not None

    @property
    def blocked(self) -> bool:
        return (
            self.routing is not None
            and self.routing.disposition is Disposition.BLOCKED
        )
