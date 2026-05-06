"""Forge skill bundle (SKILL.md files) shipped inside the wheel.

Phase 5 ships five Anthropic-style skills under
``forge_mcp/skills/forge.<name>/SKILL.md``. The skills steer the
agent client (Claude Code primary, Cursor/Copilot via manual paste)
through Forge's structured workflows: descriptor extraction
(``forge.plan``), preview rendering (``forge.visualize``), audit
subagent invocation (``forge.audit``), spec/realization cleanup
(``forge.cleanup``), and hypergraph traversal (``forge.connect``).

This package only contains the loader, frontmatter schema, and the
CLI installer. Skill body markdown is treated as data — never
imported, never executed; it is shipped verbatim and validated only
by the unit tests under ``tests/skills/``.
"""

from __future__ import annotations

from forge_mcp.skills._schema import SkillFrontmatter
from forge_mcp.skills.loader import (
    SHIPPED_SKILL_NAMES,
    SkillNotFoundError,
    SkillRecord,
    iter_skills,
    load_skill,
    skill_root,
)

__all__ = [
    "SHIPPED_SKILL_NAMES",
    "SkillFrontmatter",
    "SkillNotFoundError",
    "SkillRecord",
    "iter_skills",
    "load_skill",
    "skill_root",
]
