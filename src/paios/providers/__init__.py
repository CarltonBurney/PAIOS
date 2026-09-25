"""Model providers. Provider choice is configuration, not a code path."""

from .base import ModelProvider, ProviderError, ProviderInfo
from .mock import MockProvider

__all__ = [
    "MockProvider",
    "ModelProvider",
    "ProviderError",
    "ProviderInfo",
    "build_provider",
]


def build_provider(settings: "Settings") -> ModelProvider:  # noqa: F821
    """Construct the provider named in configuration."""
    name = settings.provider

    if name == "mock":
        return MockProvider()

    if name in ("foundry", "azure", "azure_ai_foundry"):
        from .foundry import FoundryProvider

        endpoint, deployment = settings.require_foundry()
        return FoundryProvider(
            endpoint=endpoint,
            deployment=deployment,
            api_version=settings.foundry_api_version,
        )

    raise ValueError(
        f"unknown provider '{name}'; expected one of: mock, foundry"
    )
