"""Tests for ``forge.list_skills`` / ``forge.get_skill`` MCP tools."""

from __future__ import annotations

from forge_mcp.server.tools.skills import get_skill, list_skills
from forge_mcp.skills.loader import SHIPPED_SKILL_NAMES


def test_list_skills_envelope() -> None:
    """``list_skills`` returns the standard ``ok`` envelope with all five skills."""
    response = list_skills()
    assert response["ok"] is True
    payload = response["result"]
    assert isinstance(payload, dict)
    skills = payload["skills"]
    assert isinstance(skills, list)
    assert [entry["name"] for entry in skills] == list(SHIPPED_SKILL_NAMES)
    for entry in skills:
        assert set(entry).issuperset(
            {"name", "version", "description", "triggers", "requires_tools", "requires_subagent"},
        )


def test_get_skill_returns_body_markdown() -> None:
    """``get_skill('forge.plan')`` round-trips the body."""
    response = get_skill("forge.plan")
    assert response["ok"] is True
    payload = response["result"]
    assert isinstance(payload, dict)
    front = payload["frontmatter"]
    assert isinstance(front, dict)
    assert front["name"] == "forge.plan"
    body = payload["body_markdown"]
    assert isinstance(body, str)
    assert body.startswith("# forge.plan")


def test_get_skill_unknown_name_returns_error_envelope() -> None:
    """An unknown name yields ``ok=False`` with ``skill_not_found``."""
    response = get_skill("forge.nope")
    assert response["ok"] is False
    error = response["error"]
    assert isinstance(error, dict)
    assert error["code"] == "skill_not_found"
