"""Azure AI Foundry provider.

Authentication uses DefaultAzureCredential so the same code path works with a
managed identity in Azure and a developer's `az login` locally. No key is ever
read from source — if a key-based path is ever required it belongs in Key Vault
and should be injected as configuration.

The azure SDK imports are deferred so that the rest of the control plane, and
its test suite, run without the Azure packages installed.
"""

from __future__ import annotations

from typing import Any

from .base import ModelProvider, ProviderError, ProviderInfo

_SCOPE = "https://cognitiveservices.azure.com/.default"


class FoundryProvider(ModelProvider):
    """Calls a model deployment in an Azure AI Foundry project.

    Args:
        endpoint: The Foundry/Azure OpenAI endpoint URL.
        deployment: The model deployment name in that project.
        api_version: Azure OpenAI data-plane API version.
        credential: Optional pre-built credential, primarily for testing.
    """

    def __init__(
        self,
        endpoint: str,
        deployment: str,
        *,
        api_version: str = "2024-10-21",
        credential: Any | None = None,
        client: Any | None = None,
    ) -> None:
        if not endpoint:
            raise ValueError("endpoint is required")
        if not deployment:
            raise ValueError("deployment is required")

        self._endpoint = endpoint.rstrip("/")
        self._deployment = deployment
        self._api_version = api_version
        self._credential = credential
        self._client = client

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(name="azure_ai_foundry", model=self._deployment)

    def _build_client(self) -> Any:
        try:
            from azure.identity import (
                DefaultAzureCredential,
                get_bearer_token_provider,
            )
            from openai import AzureOpenAI
        except ImportError as exc:  # pragma: no cover - depends on env
            raise ProviderError(
                "Azure provider requires the 'azure' extra: "
                "pip install -e '.[azure]'"
            ) from exc

        credential = self._credential or DefaultAzureCredential()
        token_provider = get_bearer_token_provider(credential, _SCOPE)

        return AzureOpenAI(
            azure_endpoint=self._endpoint,
            azure_ad_token_provider=token_provider,
            api_version=self._api_version,
        )

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        if self._client is None:
            self._client = self._build_client()

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = self._client.chat.completions.create(
                model=self._deployment,
                messages=messages,
            )
        except Exception as exc:  # pragma: no cover - network path
            raise ProviderError(f"Foundry call failed: {exc}") from exc

        choice = response.choices[0]
        return (choice.message.content or "").strip()
