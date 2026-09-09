"""Project registry.

The dashboard renders whatever projects this file describes. Registry entries are
presentation and source-of-truth wiring only — the name and emoji shown on a card,
which GitHub repositories to poll, where the Perplexity project lives. Everything
that changes as work progresses lives in the store, not here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# The card accent colours the stylesheet defines. An unknown colour would render
# with no accent at all, so the registry is validated against this set on load.
VALID_COLORS = frozenset(
    {"primary", "gold", "blue", "purple", "success", "warning", "neutral"}
)


class RegistryError(ValueError):
    """Raised when a projects file cannot be used as written."""


@dataclass(frozen=True)
class Project:
    """One card on the dashboard."""

    id: str
    name: str
    emoji: str
    category: str
    color: str
    pplx_project_url: str
    repos: tuple[str, ...] = field(default=())

    @property
    def primary_repo(self) -> str | None:
        """The repository whose push time and issue link represent the project."""
        return self.repos[0] if self.repos else None


def _require_str(raw: dict, key: str, project_id: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"project {project_id!r}: {key!r} must be a non-empty string")
    return value


def parse_project(project_id: str, raw: dict) -> Project:
    """Build one Project, rejecting anything the dashboard could not render."""
    if not isinstance(raw, dict):
        raise RegistryError(f"project {project_id!r}: entry must be an object")

    color = raw.get("color", "primary")
    if color not in VALID_COLORS:
        raise RegistryError(
            f"project {project_id!r}: color {color!r} is not one of "
            f"{sorted(VALID_COLORS)}"
        )

    repos = raw.get("repos", [])
    if not isinstance(repos, list) or any(not isinstance(r, str) for r in repos):
        raise RegistryError(f"project {project_id!r}: 'repos' must be a list of strings")
    for repo in repos:
        # The GitHub collector interpolates this straight into an API path.
        if repo.count("/") != 1 or repo.startswith("/") or repo.endswith("/"):
            raise RegistryError(
                f"project {project_id!r}: repo {repo!r} must be in 'owner/name' form"
            )

    return Project(
        id=project_id,
        name=_require_str(raw, "name", project_id),
        emoji=raw.get("emoji", "•"),
        category=raw.get("category", ""),
        color=color,
        pplx_project_url=raw.get("pplx_project_url", ""),
        repos=tuple(repos),
    )


def load_projects(path: Path) -> dict[str, Project]:
    """Read a projects file. Order is preserved: it is the card order on screen."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RegistryError(f"no projects file at {path}") from exc
    except json.JSONDecodeError as exc:
        raise RegistryError(f"{path} is not valid JSON: {exc}") from exc

    projects = raw.get("projects") if isinstance(raw, dict) else None
    if not isinstance(projects, dict):
        raise RegistryError(f"{path} must contain a 'projects' object")

    return {pid: parse_project(pid, entry) for pid, entry in projects.items()}
