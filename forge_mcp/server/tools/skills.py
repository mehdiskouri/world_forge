"""``forge.list_skills`` and ``forge.get_skill`` MCP tools.

These two tools give agent clients without a filesystem skill-loader
(Cursor, Copilot today) a way to discover and fetch shipped skill
bodies over the same MCP transport they already use for region tools.

Both tools return JSON-serialisable plain ``dict``s wrapped in the
standard :func:`forge_mcp.server.tools._responses.ok` /
:func:`fail` envelope.
"""

from __future__ import annotations

from forge_mcp.server.tools._responses import fail, ok
from forge_mcp.skills.loader import (
    SkillFrontmatterError,
    SkillNotFoundError,
    iter_skills,
    load_skill,
)


def list_skills() -> dict[str, object]:
    """Return summary records for every shipped skill.

    Result shape::

        {"ok": True, "result": {"skills": [
            {"name": ..., "version": ..., "description": ...,
             "triggers": [...], "requires_subagent": bool},
            ...
        ]}}
    """
    summaries: list[dict[str, object]] = []
    for record in iter_skills():
        front = record.frontmatter
        summaries.append(
            {
                "name": front.name,
                "version": front.version,
                "description": front.description,
                "triggers": list(front.triggers),
                "requires_tools": list(front.requires_tools),
                "requires_subagent": front.requires_subagent,
            },
        )
    return ok({"skills": summaries})


def get_skill(name: str) -> dict[str, object]:
    """Return one full SKILL.md body plus parsed frontmatter."""
    try:
        record = load_skill(name)
    except SkillNotFoundError as exc:
        return fail("skill_not_found", str(exc), details={"name": name})
    except SkillFrontmatterError as exc:
        return fail(
            "skill_frontmatter_invalid",
            str(exc),
            details={
                "name": exc.skill_name,
                "line": exc.line_number,
                "reason": exc.reason,
            },
        )
    front = record.frontmatter
    return ok(
        {
            "frontmatter": {
                "name": front.name,
                "version": front.version,
                "description": front.description,
                "triggers": list(front.triggers),
                "requires_tools": list(front.requires_tools),
                "requires_subagent": front.requires_subagent,
            },
            "body_markdown": record.body_markdown,
            "embedded_assets": dict(record.embedded_assets),
        },
    )


__all__ = ["get_skill", "list_skills"]
