"""Pydantic schema for a SKILL.md frontmatter block.

Forge does not depend on PyYAML; instead the frontmatter is a tiny
JSON-literal dialect parsed by :mod:`forge_mcp.skills.loader`. Keeping
the schema in a Pydantic model makes the contract explicit and lets
the validation tests in ``tests/skills/`` reuse the same model that
the loader uses, so drift is impossible.

A frontmatter block looks like::

    ---
    name: "forge.plan"
    version: "1.0.0"
    description: "Extract a structured descriptor from free text..."
    triggers: ["create a region", "describe terrain", "make this a..."]
    requires_tools: ["forge.create_region", "forge.generate_region"]
    requires_subagent: false
    ---

Each value is a JSON literal: strings are quoted, lists use ``[..]``,
booleans use ``true``/``false``. This is intentionally stricter than
YAML so the loader stays small and unambiguous.
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

_NAME_PATTERN = re.compile(r"^forge\.[a-z][a-z0-9_]{1,30}$")
"""All shipped Forge skills are namespaced ``forge.<name>``."""

_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
"""Frontmatter ``version`` must be a strict ``MAJOR.MINOR.PATCH`` triple."""

_DESCRIPTION_MAX_CHARS = 500
"""Anthropic skills documentation recommends a one- to two-sentence summary."""


class SkillFrontmatter(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Validated YAML-frontmatter shape for every shipped SKILL.md."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(description="Skill identifier; must match folder name.")
    version: str = Field(description="Strict MAJOR.MINOR.PATCH triple.")
    description: str = Field(description="One- to two-sentence summary for the agent.")
    triggers: tuple[str, ...] = Field(
        default=(),
        description="Free-text phrases that should make the agent invoke this skill.",
    )
    requires_tools: tuple[str, ...] = Field(
        default=(),
        description="MCP tool names the skill body assumes are available.",
    )
    requires_subagent: bool = Field(
        default=False,
        description="True for skills that the agent must run inside an isolated subagent.",
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _NAME_PATTERN.fullmatch(value):
            msg = f"skill name must match {_NAME_PATTERN.pattern!r}, got {value!r}"
            raise ValueError(msg)
        return value

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        if not _SEMVER_PATTERN.fullmatch(value):
            msg = f"skill version must be MAJOR.MINOR.PATCH, got {value!r}"
            raise ValueError(msg)
        return value

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "skill description must not be empty"
            raise ValueError(msg)
        if len(stripped) > _DESCRIPTION_MAX_CHARS:
            msg = (
                f"skill description must be <= {_DESCRIPTION_MAX_CHARS} chars, got {len(stripped)}"
            )
            raise ValueError(msg)
        return stripped


__all__ = ["SkillFrontmatter"]
