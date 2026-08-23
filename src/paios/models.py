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
    """Risk levels, ordered from least to most restrictive."""

    LOW = "low"
    STANDARD = "standard"
    SENSITIVE = "sensitive"
    COMPLIANCE = "compliance"
    SECURITY = "security"

    @property
    def rank(self) -> int:
        return _RISK_ORDER.index(self)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.rank < other.rank


_RISK_ORDER = [
    RiskLevel.LOW,
    RiskLevel.STANDARD,
    RiskLevel.SENSITIVE,
    RiskLevel.COMPLIANCE,
    RiskLevel.SECURITY,
]


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
    level: RiskLevel
    triggers: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyViolation:
    policy_id: str
    control: str
    detail: str


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
