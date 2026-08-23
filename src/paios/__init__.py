"""PAIOS — governance-first AI control plane.

STATUS: pre-specification reference slice.

This package is a working vertical slice through the governance core, written
before the PAIOS Master Build Specification exists. It demonstrates the request
lifecycle end to end with no cloud dependency. It is expected to be superseded
by the specification — in particular, risk tiers here are pattern-driven rather
than the configurable L0–L4 model the specification calls for.
"""

from .audit import AuditEvent, AuditSink, AuditStage, InMemoryAuditSink, JsonlAuditSink
from .classification import ModelAssistedClassifier, RuleClassifier
from .config import ConfigError, Settings
from .control_plane import ControlPlane
from .execution import (
    ExecutionGateway,
    ExecutionRejected,
    ExecutionResult,
    Principal,
    RejectionReason,
    StaticHandlerResolver,
)
from .models import (
    Agent,
    Approval,
    ApprovalState,
    Classification,
    Disposition,
    Execution,
    Identity,
    Outcome,
    PolicyDecision,
    PolicyOutcome,
    PolicyViolation,
    Request,
    RequestType,
    RiskAssessment,
    RiskDomain,
    RiskLevel,
    RoutingDecision,
)
from .policy import (
    Policy,
    PolicyConditions,
    PolicyEffects,
    PolicyEngine,
    PolicyScope,
    PolicySet,
)
from .risk import RiskEngine, RiskModel
from .routing import Router, authorize, candidate_agent
from .tools import (
    CallerType,
    OperationType,
    ToolConstraints,
    ToolDefinition,
    ToolRegistry,
)

__version__ = "0.1.0"

__all__ = [
    "Agent",
    "Approval",
    "ApprovalState",
    "AuditEvent",
    "AuditSink",
    "AuditStage",
    "authorize",
    "CallerType",
    "candidate_agent",
    "Classification",
    "ConfigError",
    "ControlPlane",
    "Disposition",
    "Execution",
    "ExecutionGateway",
    "ExecutionRejected",
    "ExecutionResult",
    "Identity",
    "InMemoryAuditSink",
    "JsonlAuditSink",
    "ModelAssistedClassifier",
    "OperationType",
    "Outcome",
    "Policy",
    "PolicyConditions",
    "PolicyDecision",
    "PolicyEffects",
    "PolicyEngine",
    "PolicyOutcome",
    "PolicyScope",
    "PolicySet",
    "PolicyViolation",
    "Principal",
    "RejectionReason",
    "Request",
    "RequestType",
    "RiskAssessment",
    "RiskDomain",
    "RiskEngine",
    "RiskLevel",
    "RiskModel",
    "Router",
    "RoutingDecision",
    "RuleClassifier",
    "Settings",
    "StaticHandlerResolver",
    "ToolConstraints",
    "ToolDefinition",
    "ToolRegistry",
    "__version__",
]
