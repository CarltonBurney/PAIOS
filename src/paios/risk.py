"""Risk level assignment.

Risk is assigned by taking the *highest* level any detector fires on. Levels
never cancel out: a request that touches both client PII and a permission
change is a SECURITY request, not an averaged one.

The detectors here are intentionally conservative. In a governance control
plane, a false positive costs a human review; a false negative costs an
unreviewed action on sensitive data.
"""

from __future__ import annotations

import re

from .models import (
    Classification,
    Request,
    RequestType,
    RiskAssessment,
    RiskLevel,
)

# --- Detector patterns -------------------------------------------------------

_SECURITY_PATTERNS = (
    r"\bpermission(s)?\b",
    r"\baccess (?:control|right|level)s?\b",
    r"\bgrant\b",
    r"\brevoke\b",
    r"\bprivilege\b",
    r"\badmin(?:istrator)?\b",
    r"\broot\b",
    r"\bcredential\b",
    r"\bsecret\b",
    r"\bapi[- ]?key\b",
    r"\bpassword\b",
    r"\btoken\b",
    r"\bfirewall\b",
    r"\bdelete (?:the )?(?:database|tenant|account|user)\b",
    r"\bdisable (?:logging|audit|mfa)\b",
    r"\bservice principal\b",
    r"\brole assignment\b",
)

_COMPLIANCE_PATTERNS = (
    r"\bgdpr\b",
    r"\bhipaa\b",
    r"\bsox\b",
    r"\bpci\b",
    r"\bccpa\b",
    r"\bregulat",
    r"\bcompliance\b",
    r"\blegal\b",
    r"\bcontract\b",
    r"\bliabilit",
    r"\bretention (?:policy|period|schedule)\b",
    r"\bdata residency\b",
    r"\baudit(?:or|ed)\b",
    r"\bsubpoena\b",
    r"\bdisclosure\b",
)

_SENSITIVE_PATTERNS = (
    r"\bssn\b",
    r"\bsocial security\b",
    r"\bdate of birth\b",
    r"\bdob\b",
    r"\bsalary\b",
    r"\bcompensation\b",
    r"\bmedical\b",
    r"\bdiagnos",
    r"\bpersonal (?:data|information)\b",
    r"\bpii\b",
    r"\bclient (?:data|record|list|information)\b",
    r"\bcustomer (?:data|record|list)\b",
    r"\bemployee record\b",
    r"\bhome address\b",
    r"\bpayroll\b",
)

# Structural PII — matched on shape rather than vocabulary.
_PII_SHAPES: tuple[tuple[str, str], ...] = (
    ("email_address", r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"),
    ("ssn_format", r"\b\d{3}-\d{2}-\d{4}\b"),
    ("credit_card_format", r"\b(?:\d[ -]?){13,16}\b"),
    ("phone_format", r"\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b"),
)

_STANDARD_PATTERNS = (
    r"\bupdate\b",
    r"\bcreate\b",
    r"\bmodify\b",
    r"\bchange\b",
    r"\bsend\b",
    r"\bpublish\b",
    r"\bapprove\b",
)


def _hits(text: str, patterns: tuple[str, ...]) -> list[str]:
    return [p for p in patterns if re.search(p, text, re.IGNORECASE)]


class RiskEngine:
    """Assigns a RiskLevel by escalation — highest detector wins."""

    def assess(
        self,
        request: Request,
        classification: Classification,
    ) -> RiskAssessment:
        text = request.content
        triggers: list[str] = []
        level = RiskLevel.LOW

        def escalate(to: RiskLevel, reasons: list[str], label: str) -> None:
            nonlocal level, triggers
            if not reasons:
                return
            triggers.extend(f"{label}:{r}" for r in reasons)
            if to.rank > level.rank:
                level = to

        # A governance change is at minimum SENSITIVE — it alters the rules the
        # rest of the system is judged against.
        if classification.request_type is RequestType.GOVERNANCE_CHANGE:
            triggers.append("classification:governance_change")
            level = RiskLevel.SENSITIVE

        escalate(RiskLevel.STANDARD, _hits(text, _STANDARD_PATTERNS), "standard")
        escalate(RiskLevel.SENSITIVE, _hits(text, _SENSITIVE_PATTERNS), "sensitive")

        pii_found = [
            name for name, shape in _PII_SHAPES if re.search(shape, text)
        ]
        escalate(RiskLevel.SENSITIVE, pii_found, "pii")

        escalate(RiskLevel.COMPLIANCE, _hits(text, _COMPLIANCE_PATTERNS), "compliance")
        escalate(RiskLevel.SECURITY, _hits(text, _SECURITY_PATTERNS), "security")

        # An unauthenticated caller is a security concern in its own right.
        if not request.identity.authenticated:
            triggers.append("identity:unauthenticated")
            level = RiskLevel.SECURITY

        # A request we could not confidently classify does not get to be LOW.
        if classification.confidence < 0.3 and level is RiskLevel.LOW:
            triggers.append("classification:low_confidence")
            level = RiskLevel.STANDARD

        return RiskAssessment(level=level, triggers=tuple(triggers))
