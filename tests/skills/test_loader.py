"""Tests for :mod:`forge_mcp.skills.loader`."""

from __future__ import annotations

import pytest
from forge_mcp.skills import _schema
from forge_mcp.skills.loader import (
    SHIPPED_SKILL_NAMES,
    SkillFrontmatterError,
    SkillNotFoundError,
    iter_skills,
    load_skill,
    skill_root,
)

_BAD_VERSION_LINE = 3
"""Line number of the bad ``version: 0.1`` field in the synthetic SKILL.md."""


def test_shipped_skill_names_are_canonical() -> None:
    """The five Phase-5 skills are shipped in the documented order."""
    assert SHIPPED_SKILL_NAMES == (
        "forge.plan",
        "forge.visualize",
        "forge.audit",
        "forge.cleanup",
        "forge.connect",
    )


def test_iter_skills_loads_every_shipped_skill() -> None:
    """Every shipped skill loads with valid frontmatter."""
    records = list(iter_skills())
    assert [r.frontmatter.name for r in records] == list(SHIPPED_SKILL_NAMES)
    for record in records:
        assert record.frontmatter.version
        assert record.body_markdown


def test_load_skill_round_trips_frontmatter_fields() -> None:
    """``load_skill`` produces a record matching the on-disk SKILL.md."""
    record = load_skill("forge.plan")
    assert record.frontmatter.name == "forge.plan"
    assert record.frontmatter.requires_subagent is False
    assert "forge.create_region" in record.frontmatter.requires_tools
    assert record.body_markdown.startswith("# forge.plan")


def test_audit_skill_requires_subagent() -> None:
    """``forge.audit`` is the only Stage-A skill that requires a subagent."""
    requires = {
        record.frontmatter.name: record.frontmatter.requires_subagent for record in iter_skills()
    }
    assert requires == {
        "forge.plan": False,
        "forge.visualize": False,
        "forge.audit": True,
        "forge.cleanup": False,
        "forge.connect": False,
    }


def test_load_skill_rejects_unknown_name() -> None:
    """Unknown skills raise :class:`SkillNotFoundError`."""
    with pytest.raises(SkillNotFoundError):
        load_skill("forge.does_not_exist")


def test_skill_root_points_at_skills_package() -> None:
    """``skill_root`` exposes the importlib resource for the skills folder."""
    root = skill_root()
    children = {child.name for child in root.iterdir()}
    for name in SHIPPED_SKILL_NAMES:
        assert name in children


def test_frontmatter_parser_rejects_missing_opening_delimiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skill body without a leading ``---`` raises a precise error."""
    bad_text = 'name: "forge.plan"\nversion: "0.1.0"\n'
    _install_fake_skill(monkeypatch, "forge.plan", bad_text)
    with pytest.raises(SkillFrontmatterError) as excinfo:
        load_skill("forge.plan")
    assert excinfo.value.line_number == 1


def test_frontmatter_parser_rejects_missing_closing_delimiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skill body with no closing ``---`` is rejected."""
    bad_text = '---\nname: "forge.plan"\n'
    _install_fake_skill(monkeypatch, "forge.plan", bad_text)
    with pytest.raises(SkillFrontmatterError) as excinfo:
        load_skill("forge.plan")
    assert "missing closing" in excinfo.value.reason


def test_frontmatter_parser_rejects_non_json_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-JSON-literal frontmatter values are rejected."""
    bad_text = (
        "---\n"
        'name: "forge.plan"\n'
        "version: not_json\n"  # bare token; not a JSON literal
        "---\n"
        "body\n"
    )
    _install_fake_skill(monkeypatch, "forge.plan", bad_text)
    with pytest.raises(SkillFrontmatterError) as excinfo:
        load_skill("forge.plan")
    assert excinfo.value.line_number == _BAD_VERSION_LINE


def test_frontmatter_parser_rejects_duplicate_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated keys are rejected with the second-occurrence line number."""
    bad_text = (
        '---\nname: "forge.plan"\nname: "forge.plan"\nversion: "0.1.0"\ndescription: "x"\n---\n'
    )
    _install_fake_skill(monkeypatch, "forge.plan", bad_text)
    with pytest.raises(SkillFrontmatterError) as excinfo:
        load_skill("forge.plan")
    assert "duplicate" in excinfo.value.reason


def test_frontmatter_parser_rejects_mismatched_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Frontmatter ``name`` must match the folder name."""
    bad_text = '---\nname: "forge.somethingelse"\nversion: "0.1.0"\ndescription: "x"\n---\n'
    _install_fake_skill(monkeypatch, "forge.plan", bad_text)
    with pytest.raises(SkillFrontmatterError):
        load_skill("forge.plan")


def test_frontmatter_schema_rejects_bad_version() -> None:
    """``SkillFrontmatter`` rejects non-semver versions."""
    with pytest.raises(ValueError, match=r"MAJOR\.MINOR\.PATCH"):
        _schema.SkillFrontmatter.model_validate(
            {"name": "forge.plan", "version": "0.1", "description": "x"},
        )


def test_frontmatter_schema_rejects_bad_name() -> None:
    """``SkillFrontmatter`` rejects names outside the ``forge.<lower>`` namespace."""
    with pytest.raises(ValueError, match="skill name must match"):
        _schema.SkillFrontmatter.model_validate(
            {"name": "Forge.Plan", "version": "0.1.0", "description": "x"},
        )


def _install_fake_skill(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    text: str,
) -> None:
    """Redirect ``load_skill`` for one name to read a synthetic SKILL.md."""
    from forge_mcp.skills import loader  # noqa: PLC0415  # local import to avoid cycle

    real_root = loader.skill_root

    class _FakeFile:
        def __init__(self, payload: str) -> None:
            self._payload = payload

        def is_file(self) -> bool:
            return True

        def read_text(self, encoding: str = "utf-8") -> str:
            assert encoding == "utf-8"
            return self._payload

    class _FakeFolder:
        def __init__(self, target: str, payload: str) -> None:
            self._target = target
            self._payload = payload

        def __truediv__(self, child: str) -> object:
            if child == "SKILL.md":
                return _FakeFile(self._payload)
            return _FakeFile("")

        def iterdir(self) -> list[object]:
            return []

    class _FakeRoot:
        def __truediv__(self, child: str) -> object:
            if child == name:
                return _FakeFolder(child, text)
            return real_root() / child

    monkeypatch.setattr(loader, "skill_root", _FakeRoot)
