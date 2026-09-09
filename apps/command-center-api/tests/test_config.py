from __future__ import annotations

import json

import pytest

from paios_command_center.config import RegistryError, load_projects, parse_project


def test_parse_project_reads_every_card_field():
    project = parse_project(
        "paios",
        {
            "name": "PAIOS",
            "emoji": "🧠",
            "category": "Personal AI OS",
            "color": "blue",
            "pplx_project_url": "https://example.invalid/p",
            "repos": ["owner/repo", "owner/other"],
        },
    )

    assert project.id == "paios"
    assert project.name == "PAIOS"
    assert project.color == "blue"
    assert project.repos == ("owner/repo", "owner/other")
    assert project.primary_repo == "owner/repo"


def test_project_without_repos_has_no_primary():
    assert parse_project("x", {"name": "X"}).primary_repo is None


def test_unknown_colour_is_rejected():
    # An unknown colour renders with no accent, so it fails at load rather than
    # silently producing a colourless card.
    with pytest.raises(RegistryError, match="color"):
        parse_project("x", {"name": "X", "color": "chartreuse"})


def test_missing_name_is_rejected():
    with pytest.raises(RegistryError, match="name"):
        parse_project("x", {})


@pytest.mark.parametrize("repo", ["not-a-repo", "too/many/slashes", "/leading", "trail/"])
def test_malformed_repo_is_rejected(repo):
    with pytest.raises(RegistryError, match="owner/name"):
        parse_project("x", {"name": "X", "repos": [repo]})


def test_load_projects_preserves_declaration_order(tmp_path):
    path = tmp_path / "projects.json"
    path.write_text(
        json.dumps(
            {"projects": {"b": {"name": "B"}, "a": {"name": "A"}, "c": {"name": "C"}}}
        ),
        encoding="utf-8",
    )
    # Card order on screen is registry order, so it must survive the round trip.
    assert list(load_projects(path)) == ["b", "a", "c"]


def test_missing_file_names_the_path(tmp_path):
    with pytest.raises(RegistryError, match="no projects file"):
        load_projects(tmp_path / "absent.json")


def test_malformed_json_is_reported(tmp_path):
    path = tmp_path / "projects.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RegistryError, match="not valid JSON"):
        load_projects(path)
