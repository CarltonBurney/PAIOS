"""Request classification.

Classification is deliberately deterministic and rule-driven. The control plane
must be able to explain why a request was routed the way it was, and a keyword
signal is auditable in a way that a model's opinion is not.

A model-assisted classifier can be layered on top via `ModelAssistedClassifier`,
but it only ever *raises* specificity — it can never downgrade a governance
change into a routine request.
"""

from __future__ import annotations

import re
from typing import Protocol

from .models import Classification, Request, RequestType

# Governance signals are checked first and are non-negotiable. Any request that
# proposes changing the rules is a governance change regardless of how it is
# phrased or what else it looks like.
_GOVERNANCE_PATTERNS = (
    r"\bgovernance\b",
    r"\bpolicy\b",
    r"\bpolicies\b",
    r"\bapproval (?:rule|workflow|gate)s?\b",
    r"\brisk (?:level|threshold)s?\b",
    r"\baudit (?:rule|requirement)s?\b",
    r"\bcontrol plane\b",
    r"\bchange the rules\b",
    r"\bbypass\b",
    r"\boverride\b",
    r"\bexempt\b",
)

_TYPE_PATTERNS: dict[RequestType, tuple[str, ...]] = {
    RequestType.PROJECT: (
        r"\bproject\b",
        r"\bmilestone\b",
        r"\btimeline\b",
        r"\broadmap\b",
        r"\bsprint\b",
        r"\bbacklog\b",
        r"\bdeadline\b",
        r"\bschedul",
        r"\bcoordinat",
        r"\bstakeholder\b",
    ),
    RequestType.TECHNICAL: (
        r"\bdeploy\b",
        r"\bserver\b",
        r"\bdatabase\b",
        r"\berror\b",
        r"\bbug\b",
        r"\bcrash\b",
        r"\bconfigur",
        r"\binstall\b",
        r"\btroubleshoot\b",
        r"\bnetwork\b",
        r"\bapi\b",
        r"\bcode\b",
        r"\bpipeline\b",
        r"\bintegrat",
    ),
    RequestType.LOGICAL: (
        r"\banalyz",
        r"\banalys",
        r"\bresearch\b",
        r"\bcompare\b",
        r"\bevaluat",
        r"\bassess\b",
        r"\bwhy\b",
        r"\breason",
        r"\btrade-?off\b",
        r"\brecommend",
        r"\bforecast\b",
    ),
    RequestType.CORE: (
        r"\bdocument\b",
        r"\bwrite\b",
        r"\bdraft\b",
        r"\bsummar",
        r"\bemail\b",
        r"\bnotify\b",
        r"\bcommunicat",
        r"\breport\b",
    ),
}


def _count_hits(text: str, patterns: tuple[str, ...]) -> list[str]:
    return [p for p in patterns if re.search(p, text, re.IGNORECASE)]


class Classifier(Protocol):
    def classify(self, request: Request) -> Classification: ...


class RuleClassifier:
    """Keyword-signal classifier. Explainable and fully deterministic."""

    def classify(self, request: Request) -> Classification:
        text = request.content

        governance_hits = _count_hits(text, _GOVERNANCE_PATTERNS)
        if governance_hits:
            return Classification(
                request_type=RequestType.GOVERNANCE_CHANGE,
                confidence=1.0,
                signals=tuple(governance_hits),
            )

        scores: dict[RequestType, list[str]] = {
            rtype: _count_hits(text, patterns)
            for rtype, patterns in _TYPE_PATTERNS.items()
        }

        best_type = max(scores, key=lambda t: len(scores[t]))
        best_hits = scores[best_type]

        if not best_hits:
            # Nothing matched. Default to CORE — the general-operations agent —
            # with low confidence so downstream risk logic can react to it.
            return Classification(
                request_type=RequestType.CORE,
                confidence=0.25,
                signals=("no_signal_default",),
            )

        total_hits = sum(len(h) for h in scores.values())
        confidence = len(best_hits) / total_hits if total_hits else 0.25

        return Classification(
            request_type=best_type,
            confidence=round(confidence, 3),
            signals=tuple(best_hits),
        )


class ModelAssistedClassifier:
    """Wraps a rule classifier, consulting a model only on ambiguous requests.

    The model can refine a low-confidence guess but is never trusted to move a
    request *out* of GOVERNANCE_CHANGE. That escalation is one-way by design:
    a prompt-injected request must not be able to talk its way down the ladder.
    """

    def __init__(
        self,
        base: Classifier,
        provider: ModelProvider,  # noqa: F821 - avoids a circular import
        confidence_floor: float = 0.5,
    ) -> None:
        self._base = base
        self._provider = provider
        self._floor = confidence_floor

    def classify(self, request: Request) -> Classification:
        result = self._base.classify(request)

        if result.request_type is RequestType.GOVERNANCE_CHANGE:
            return result
        if result.confidence >= self._floor:
            return result

        allowed = [t.value for t in RequestType]
        prompt = (
            "Classify the following request into exactly one category.\n"
            f"Categories: {', '.join(allowed)}\n"
            "Reply with the category name only.\n\n"
            f"Request: {request.content}"
        )
        raw = self._provider.complete(prompt).strip().lower()

        for candidate in RequestType:
            if candidate.value == raw:
                return Classification(
                    request_type=candidate,
                    confidence=0.6,
                    signals=result.signals + ("model_assisted",),
                )

        # Model returned something unusable. Keep the deterministic answer.
        return Classification(
            request_type=result.request_type,
            confidence=result.confidence,
            signals=result.signals + ("model_assist_failed",),
        )
