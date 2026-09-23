"""Structured failures: every adapter raises PipelineFailure carrying a schema-valid Error."""
from __future__ import annotations

import uuid
from typing import Iterable

from . import contract


def make_error(
    code: str,
    stage: str,
    message: str,
    *,
    retryable: bool = False,
    retry_after_seconds: int | None = None,
    details: Iterable[tuple[str, str]] = (),
    trace_id: str | None = None,
) -> dict:
    error = {
        "schema_version": contract.CONTRACT_RELEASE,
        "code": code,
        "message": message,
        "stage": stage,
        "retryable": retryable,
        "retry_after_seconds": retry_after_seconds,
        "trace_id": trace_id or str(uuid.uuid4()),
        "details": [{"field": f, "reason": r} for f, r in details],
    }
    contract.validate("Error", error)
    return error


def failure(code: str, stage: str, message: str, **kwargs) -> contract.PipelineFailure:
    """Build (not raise) a PipelineFailure; callers `raise failure(...)`."""
    error = make_error(code, stage, message, **kwargs)
    exc = contract.PipelineFailure(f"{code}: {message}")
    exc.error = error
    return exc
