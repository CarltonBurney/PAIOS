"""Configuration.

Every tenant-specific value is read from the environment. Nothing that
identifies a tenant, subscription, or deployment is committed to source, and no
secret is read from a file in the repository.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = REPO_ROOT / "policies" / "policy-rules.json"
DEFAULT_RISK_MODEL_PATH = REPO_ROOT / "policies" / "risk-model.json"
DEFAULT_TOOL_REGISTRY_PATH = REPO_ROOT / "policies" / "tool-registry.json"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


@dataclass(frozen=True)
class Settings:
    environment: str = "dev"
    provider: str = "mock"

    # Azure AI Foundry — required only when provider == "foundry".
    foundry_endpoint: str | None = None
    foundry_deployment: str | None = None
    foundry_api_version: str = "2024-10-21"

    # Entra ID — required only when identity verification is enabled.
    tenant_id: str | None = None
    client_id: str | None = None

    policy_path: Path = DEFAULT_POLICY_PATH
    risk_model_path: Path = DEFAULT_RISK_MODEL_PATH
    tool_registry_path: Path = DEFAULT_TOOL_REGISTRY_PATH
    audit_path: Path | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        src = env if env is not None else dict(os.environ)

        def get(key: str, default: str | None = None) -> str | None:
            value = src.get(key, default)
            return value.strip() if isinstance(value, str) else value

        policy = get("PAIOS_POLICY_PATH")
        risk_model = get("PAIOS_RISK_MODEL_PATH")
        tool_registry = get("PAIOS_TOOL_REGISTRY_PATH")
        audit = get("PAIOS_AUDIT_PATH")

        return cls(
            environment=get("PAIOS_ENV", "dev") or "dev",
            provider=(get("PAIOS_PROVIDER", "mock") or "mock").lower(),
            foundry_endpoint=get("AZURE_AI_FOUNDRY_ENDPOINT"),
            foundry_deployment=get("AZURE_AI_FOUNDRY_DEPLOYMENT"),
            foundry_api_version=get("AZURE_OPENAI_API_VERSION", "2024-10-21")
            or "2024-10-21",
            tenant_id=get("AZURE_TENANT_ID"),
            client_id=get("AZURE_CLIENT_ID"),
            policy_path=Path(policy) if policy else DEFAULT_POLICY_PATH,
            risk_model_path=(
                Path(risk_model) if risk_model else DEFAULT_RISK_MODEL_PATH
            ),
            tool_registry_path=(
                Path(tool_registry) if tool_registry else DEFAULT_TOOL_REGISTRY_PATH
            ),
            audit_path=Path(audit) if audit else None,
        )

    def require_foundry(self) -> tuple[str, str]:
        missing = [
            name
            for name, value in (
                ("AZURE_AI_FOUNDRY_ENDPOINT", self.foundry_endpoint),
                ("AZURE_AI_FOUNDRY_DEPLOYMENT", self.foundry_deployment),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "Foundry provider selected but these environment variables are "
                f"not set: {', '.join(missing)}"
            )
        assert self.foundry_endpoint and self.foundry_deployment
        return self.foundry_endpoint, self.foundry_deployment
