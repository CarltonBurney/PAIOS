"""Deterministic provider for tests and offline development.

Lets the whole control plane be exercised end to end with no Azure dependency,
which is what makes the governance logic testable in CI.
"""

from __future__ import annotations

from .base import ModelProvider, ProviderInfo


class MockProvider(ModelProvider):
    def __init__(
        self,
        response: str = "mock response",
        *,
        scripted: dict[str, str] | None = None,
        model: str = "mock-1",
    ) -> None:
        self._response = response
        self._scripted = scripted or {}
        self._model = model
        self.calls: list[tuple[str, str | None]] = []

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(name="mock", model=self._model)

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        self.calls.append((prompt, system))
        for needle, reply in self._scripted.items():
            if needle.lower() in prompt.lower():
                return reply
        return self._response
