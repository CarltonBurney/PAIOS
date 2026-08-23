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
from .models import (
    Agent,
    Approval,
    ApprovalState,
    Classification,
    Disposition,
    Execution,
    Identity,
    Outcome,
    PolicyViolation,
    Request,
    RequestType,
    RiskAssessment,
    RiskLevel,
    RoutingDecision,
)
from .policy import Policy, PolicyEngine, PolicySet
from .risk import RiskEngine
from .routing import Router

__version__ = "0.1.0"

__all__ = [
    "Agent",
    "Approval",
    "ApprovalState",
    "AuditEvent",
    "AuditSink",
    "AuditStage",
    "Classification",
    "ConfigError",
    "ControlPlane",
    "Disposition",
    "Execution",
    "Identity",
    "InMemoryAuditSink",
    "JsonlAuditSink",
    "ModelAssistedClassifier",
    "Outcome",
    "Policy",
    "PolicyEngine",
    "PolicySet",
    "PolicyViolation",
    "Request",
    "RequestType",
    "RiskAssessment",
    "RiskEngine",
    "RiskLevel",
    "Router",
    "RoutingDecision",
    "RuleClassifier",
    "Settings",
    "__version__",
]
