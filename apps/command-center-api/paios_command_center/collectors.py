"""Collectors that turn an external source into one card's worth of numbers.

Every collector returns a dict carrying an ``available`` flag rather than raising.
A source being down is a normal state for this dashboard, not an error condition:
the card falls back to its last known snapshot and shows a banner.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

GITHUB_API = "https://api.github.com"
USER_AGENT = "paios-command-center"
DEFAULT_TIMEOUT = 10.0


class CollectorError(RuntimeError):
    """A source could not be read on this attempt."""


def _github_request(path: str, timeout: float) -> Any:
    """One authenticated GET against the GitHub REST API."""
    request = urllib.request.Request(f"{GITHUB_API}{path}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", USER_AGENT)
    # Unauthenticated requests are capped at 60/hour, which a few projects on a
    # 20-second refresh will exhaust. A token raises that to 5,000.
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise CollectorError(f"GitHub {path} returned {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise CollectorError(f"GitHub {path} unreachable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CollectorError(f"GitHub {path} returned malformed JSON") from exc


def _open_issue_count(repo: str, repo_data: dict, timeout: float) -> int:
    """Open issues excluding pull requests.

    GitHub's ``open_issues_count`` counts pull requests as issues. A status board
    that reports "open issues" while quietly including every open PR overstates the
    backlog, so the open PRs are counted and subtracted.
    """
    total = repo_data.get("open_issues_count") or 0
    pulls = _github_request(f"/repos/{repo}/pulls?state=open&per_page=100", timeout)
    open_pulls = len(pulls) if isinstance(pulls, list) else 0
    return max(total - open_pulls, 0)


def collect_github(repos: tuple[str, ...], timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Aggregate every repository backing one project into a single card block."""
    if not repos:
        return {"available": False, "repos": [], "reason": "no repositories configured"}

    total_issues = 0
    last_pushed: str | None = None
    primary_url = f"https://github.com/{repos[0]}"

    for repo in repos:
        data = _github_request(f"/repos/{repo}", timeout)
        if not isinstance(data, dict):
            raise CollectorError(f"GitHub /repos/{repo} returned an unexpected shape")

        total_issues += _open_issue_count(repo, data, timeout)

        pushed_at = data.get("pushed_at")
        # ISO-8601 UTC strings from GitHub sort lexicographically, so the latest
        # push across a project's repositories is just the greatest string.
        if isinstance(pushed_at, str):
            if last_pushed is None or pushed_at > last_pushed:
                last_pushed = pushed_at

        if repo == repos[0]:
            primary_url = data.get("html_url") or primary_url

    return {
        "available": True,
        "repos": list(repos),
        "total_open_issues": total_issues,
        "last_pushed_at": last_pushed,
        "primary_repo_url": primary_url,
    }


def collect_perplexity(project_id: str) -> dict:
    """Perplexity session activity.

    Perplexity publishes no API for listing the sessions inside a project, so there
    is nothing honest to poll. This reports unavailable so the card falls back to
    whatever was last recorded, and the dashboard says so rather than showing a
    fabricated zero. Wire a real source here if one becomes available.
    """
    return {
        "available": False,
        "session_count": None,
        "recent_sessions": [],
        "reason": "no Perplexity session API is available",
    }
