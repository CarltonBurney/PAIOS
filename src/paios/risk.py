"""Risk assessment — two axes, loaded from configuration.

`level` is an ordered impact scale (L0-L4). `domains` are non-exclusive kinds
of concern. A request carries exactly one level and any number of domains.

Detectors only ever escalate: the level ratchets upward as detectors fire and
domains accumulate. Nothing lowers a level once raised. In a governance control
plane a false positive costs a human review; a false negative costs an
unreviewed action on sensitive data.

Detectors live in policies/risk-model.json, not in this file, so tuning risk
does not require a deployment.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import (
    Classification,
    Request,
    RequestType,
    RiskAssessment,
    RiskDomain,
    RiskLevel,
)


class RiskModelError(ValueError):
    """Raised when the risk model file is malformed."""


@dataclass(frozen=True)
class Detector:
    id: str
    level: RiskLevel
    domains: frozenset[RiskDomain]
    patterns: tuple[re.Pattern[str], ...]

    def matches(self, text: str) -> bool:
        return any(p.search(text) for p in self.patterns)


@dataclass(frozen=True)
class LevelSpec:
    label: str
    disposition: str
    logged: bool


@dataclass(frozen=True)
class EscalationRule:
    level: RiskLevel
    domains: frozenset[RiskDomain]


@dataclass(frozen=True)
class RiskModel:
    name: str
    default_level: RiskLevel
    levels: dict[RiskLevel, LevelSpec]
    detectors: tuple[Detector, ...]
    unauthenticated: EscalationRule
    governance_minimum: EscalationRule
    low_confidence_minimum: EscalationRule
    low_confidence_threshold: float

    @classmethod
    def from_file(cls, path: str | Path) -> RiskModel:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RiskModelError(f"cannot read risk model at {path}: {exc}") from exc
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RiskModel:
        try:
            levels = {
                RiskLevel(key): LevelSpec(
                    label=spec.get("label", ""),
                    disposition=spec["disposition"],
                    logged=bool(spec.get("logged", True)),
                )
                for key, spec in data["levels"].items()
            }

            detectors: list[Detector] = []
            for raw in data.get("detectors", ()):
                detectors.append(
                    Detector(
                        id=raw["id"],
                        level=RiskLevel(raw["level"]),
                        domains=_domains(raw.get("domains", ())),
                        patterns=tuple(
                            re.compile(p, re.IGNORECASE) for p in raw["patterns"]
                        ),
                    )
                )
            for raw in data.get("structuralDetectors", ()):
                detectors.append(
                    Detector(
                        id=raw["id"],
                        level=RiskLevel(raw["level"]),
                        domains=_domains(raw.get("domains", ())),
                        # Structural shapes are case-sensitive by design.
                        patterns=(re.compile(raw["pattern"]),),
                    )
                )

            rules = data.get("rules", {})
            return cls(
                name=data.get("riskModelName", "unnamed"),
                default_level=RiskLevel(data.get("defaultLevel", "L0")),
                levels=levels,
                detectors=tuple(detectors),
                unauthenticated=_rule(rules.get("unauthenticated"), RiskLevel.L4),
                governance_minimum=_rule(
                    rules.get("governanceChangeMinimum"), RiskLevel.L3
                ),
                low_confidence_minimum=_rule(
                    rules.get("lowConfidenceMinimum"), RiskLevel.L1
                ),
                low_confidence_threshold=float(
                    rules.get("lowConfidenceThreshold", 0.3)
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RiskModelError(f"malformed risk model: {exc}") from exc

    def disposition_for(self, level: RiskLevel) -> str:
        spec = self.levels.get(level)
        if spec is None:
            raise RiskModelError(f"no level spec configured for {level.value}")
        return spec.disposition


def _domains(values: Any) -> frozenset[RiskDomain]:
    return frozenset(RiskDomain(v) for v in values)


def _rule(raw: dict[str, Any] | None, fallback: RiskLevel) -> EscalationRule:
    if not raw:
        return EscalationRule(level=fallback, domains=frozenset())
    return EscalationRule(
        level=RiskLevel(raw.get("level", fallback.value)),
        domains=_domains(raw.get("domains", ())),
    )


class RiskEngine:
    """Assigns a level and domains by escalation — highest level wins."""

    def __init__(self, model: RiskModel | None = None) -> None:
        if model is None:
            from .config import DEFAULT_RISK_MODEL_PATH

            model = RiskModel.from_file(DEFAULT_RISK_MODEL_PATH)
        self.model = model

    def assess(
        self,
        request: Request,
        classification: Classification,
    ) -> RiskAssessment:
        text = request.content
        level = self.model.default_level
        domains: set[RiskDomain] = set()
        triggers: list[str] = []

        def escalate(rule_level: RiskLevel, rule_domains: frozenset[RiskDomain]) -> None:
            nonlocal level
            if rule_level > level:
                level = rule_level
            domains.update(rule_domains)

        for detector in self.model.detectors:
            if detector.matches(text):
                triggers.append(f"detector:{detector.id}")
                escalate(detector.level, detector.domains)

        # A governance change alters the rules everything else is judged against.
        if classification.request_type is RequestType.GOVERNANCE_CHANGE:
            triggers.append("classification:governance_change")
            escalate(
                self.model.governance_minimum.level,
                self.model.governance_minimum.domains,
            )

        # An unclassifiable request does not get to sit at the floor.
        if classification.confidence < self.model.low_confidence_threshold:
            triggers.append("classification:low_confidence")
            escalate(
                self.model.low_confidence_minimum.level,
                self.model.low_confidence_minimum.domains,
            )

        # An unauthenticated caller is a security concern in its own right.
        if not request.identity.authenticated:
            triggers.append("identity:unauthenticated")
            escalate(
                self.model.unauthenticated.level,
                self.model.unauthenticated.domains,
            )

        return RiskAssessment(
            level=level,
            domains=frozenset(domains),
            triggers=tuple(triggers),
        )
