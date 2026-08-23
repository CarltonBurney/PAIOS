"""Append-only audit trail.

Every decision the control plane makes emits an audit event. The sink is an
interface so the durable implementation (Blob Storage, PostgreSQL, Log
Analytics) can be swapped in without touching the pipeline.

Events carry a correlation ID so one request's full passage can be reassembled
from the log.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol


class AuditStage(str, Enum):
    RECEIVED = "received"
    IDENTITY_CHECKED = "identity_checked"
    IDENTITY_RESOLVED = "identity_resolved"
    AUTHORIZED = "authorized"
    CLASSIFIED = "classified"
    RISK_ASSESSED = "risk_assessed"
    POLICY_EVALUATED = "policy_evaluated"
    ROUTED = "routed"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_DECIDED = "approval_decided"
    EXECUTED = "executed"
    EXECUTION_ALLOWED = "execution_allowed"
    EXECUTION_REJECTED = "execution_rejected"
    OUTPUT_REVIEWED = "output_reviewed"
    BLOCKED = "blocked"
    ERROR = "error"


@dataclass(frozen=True)
class AuditEvent:
    correlation_id: str
    request_id: str
    stage: AuditStage
    subject: str
    detail: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"aud-{uuid.uuid4().hex[:12]}")
    recorded_at: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )

    def to_json(self) -> str:
        payload = asdict(self)
        payload["stage"] = self.stage.value
        payload["recorded_at"] = self.recorded_at.isoformat()
        return json.dumps(payload, sort_keys=True, default=str)


class AuditSink(Protocol):
    def record(self, event: AuditEvent) -> None: ...


class InMemoryAuditSink:
    """Test and development sink. Thread-safe, append-only."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = threading.Lock()

    def record(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def for_correlation(self, correlation_id: str) -> tuple[AuditEvent, ...]:
        return tuple(e for e in self.events if e.correlation_id == correlation_id)

    def stages(self) -> tuple[AuditStage, ...]:
        return tuple(e.stage for e in self.events)


class JsonlAuditSink:
    """Writes newline-delimited JSON. Suitable for shipping to Log Analytics."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(self, event: AuditEvent) -> None:
        line = event.to_json()
        with self._lock:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


class AuditTrail:
    """Convenience wrapper binding a correlation ID to a sink."""

    def __init__(self, sink: AuditSink, correlation_id: str | None = None) -> None:
        self._sink = sink
        self.correlation_id = correlation_id or f"cor-{uuid.uuid4().hex[:12]}"
        self._ids: list[str] = []

    def emit(
        self,
        request_id: str,
        stage: AuditStage,
        subject: str,
        **detail: Any,
    ) -> AuditEvent:
        event = AuditEvent(
            correlation_id=self.correlation_id,
            request_id=request_id,
            stage=stage,
            subject=subject,
            detail=detail,
        )
        self._sink.record(event)
        self._ids.append(event.id)
        return event

    @property
    def event_ids(self) -> tuple[str, ...]:
        return tuple(self._ids)
