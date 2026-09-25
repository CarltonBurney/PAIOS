"""Model provider abstraction.

PAIOS treats the model as replaceable. Every provider implements this one
interface, and provider choice is configuration, never a code path scattered
through the application.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    model: str


@runtime_checkable
class ModelProvider(Protocol):
    @property
    def info(self) -> ProviderInfo: ...

    def complete(self, prompt: str, *, system: str | None = None) -> str: ...


class ProviderError(RuntimeError):
    """Raised when a provider cannot fulfil a request."""
