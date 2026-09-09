"""Assembles the status document the dashboard renders.

A poll is the only thing that touches the network. ``/api/status`` serves the last
assembled document, so the dashboard's 20-second refresh costs nothing and the
GitHub rate limit is spent only when someone asks for a poll.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .collectors import CollectorError, collect_github, collect_perplexity
from .config import Project
from .store import ManualState, Store

GitHubCollector = Callable[[tuple[str, ...]], dict]
PerplexityCollector = Callable[[str], dict]


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


class StatusService:
    """Holds the project registry, the store, and the last assembled status."""

    def __init__(
        self,
        projects: dict[str, Project],
        store: Store,
        github_collector: GitHubCollector = collect_github,
        perplexity_collector: PerplexityCollector = collect_perplexity,
    ) -> None:
        self.projects = projects
        self.store = store
        self._github = github_collector
        self._perplexity = perplexity_collector
        self._generated_at: str | None = None

    # ---- reads ------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        """The current document, assembled from stored state without any network."""
        return {
            "generated_at": self._generated_at,
            "projects": {
                pid: self._project_view(project)
                for pid, project in self.projects.items()
            },
        }

    def log(self) -> list[dict[str, Any]]:
        return self.store.log()

    # ---- writes -----------------------------------------------------------
    def set_manual(self, project_id: str, manual: ManualState) -> None:
        if project_id not in self.projects:
            raise KeyError(project_id)
        self.store.set_manual(project_id, manual)

    def poll(self, reason: str = "manual") -> dict[str, Any]:
        """Refresh every project from its sources.

        One project's source failing must not cost the others their refresh, so each
        collector is attempted independently and its failure recorded as an error
        string rather than raised.
        """
        errors: list[str] = []

        for pid, project in self.projects.items():
            if project.repos:
                try:
                    self.store.set_snapshot(pid, "github", self._github(project.repos))
                except CollectorError as exc:
                    errors.append(f"{pid}: {exc}")

            perplexity = self._perplexity(pid)
            if perplexity.get("available"):
                self.store.set_snapshot(pid, "perplexity", perplexity)

        self._generated_at = _utcnow()
        self.store.append_log(reason, not errors, errors)
        self.store.save()
        return {"status": self.status(), "errors": errors}

    # ---- assembly ---------------------------------------------------------
    def _project_view(self, project: Project) -> dict[str, Any]:
        return {
            "name": project.name,
            "emoji": project.emoji,
            "category": project.category,
            "color": project.color,
            "pplx_project_url": project.pplx_project_url,
            "manual": self.store.get_manual(project.id).to_json(),
            "github": self._github_view(project),
            "perplexity": self._perplexity_view(project),
        }

    def _github_view(self, project: Project) -> dict[str, Any]:
        snapshot = self.store.get_snapshot(project.id, "github")
        if snapshot:
            return snapshot
        # repos is carried even when unavailable: the dashboard shows its "GitHub
        # data unavailable" banner only for a project that is supposed to have some.
        return {"available": False, "repos": list(project.repos)}

    def _perplexity_view(self, project: Project) -> dict[str, Any]:
        snapshot = self.store.get_snapshot(project.id, "perplexity")
        if snapshot:
            # Last-known counts are shown, but never as though they were fresh.
            return {**snapshot, "available": False}
        return {"available": False, "session_count": None, "recent_sessions": []}
