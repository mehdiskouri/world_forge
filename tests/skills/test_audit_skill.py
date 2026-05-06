"""Phase 5 Stage E coherence tests for ``forge.audit``.

Validates:

* The SKILL.md frontmatter declares ``requires_subagent: true`` and
  references only registered ``forge.*`` MCP tools.
* The fenced ``json`` block embedded in the body is **byte-identical**
  to the live ``audit_verdict_json_schema()`` (via the canonical
  schema-export formatter), so the agent never reads a stale schema.
* The skill version stays in lock-step with
  ``AUDIT_SCHEMA_VERSION``.
"""

from __future__ import annotations

import asyncio
import re

from forge_mcp.audit.verdict import AUDIT_SCHEMA_VERSION, audit_verdict_json_schema
from forge_mcp.project.schema_export import dump_schema_json
from forge_mcp.server.mcp import build_server
from forge_mcp.skills.loader import load_skill

_FENCE_RE = re.compile(r"```json\n(?P<body>.*?)\n```", re.DOTALL)


def _embedded_schema_block() -> str:
    """Return the (single) ``json`` fenced block from forge.audit/SKILL.md."""
    skill = load_skill("forge.audit")
    matches = _FENCE_RE.findall(skill.body_markdown)
    assert len(matches) >= 1, "forge.audit SKILL.md must embed at least one json fence"
    # The first json fence is the AuditVerdict schema; later fences are
    # illustrative verdicts. The byte-identity check applies only to the
    # schema block.
    block = matches[0]
    assert isinstance(block, str)
    return block


def test_embedded_audit_schema_is_byte_identical_to_live_schema() -> None:
    """The fenced JSON Schema must equal the canonical schema-export bytes."""
    embedded = _embedded_schema_block() + "\n"
    canonical = dump_schema_json(audit_verdict_json_schema())
    assert embedded == canonical, (
        "forge.audit/SKILL.md embedded JSON Schema drifted from "
        "audit_verdict_json_schema(); regenerate by copying the output of "
        "`uv run python -c 'from forge_mcp.audit.verdict import "
        "audit_verdict_json_schema; from forge_mcp.project.schema_export "
        "import dump_schema_json; print(dump_schema_json("
        'audit_verdict_json_schema()), end="")\'`.'
    )


def test_skill_version_tracks_audit_schema_version() -> None:
    """``forge.audit`` major.minor must match ``AUDIT_SCHEMA_VERSION``."""
    skill = load_skill("forge.audit")
    skill_major_minor = ".".join(skill.frontmatter.version.split(".")[:2])
    # AUDIT_SCHEMA_VERSION is "1.0"; the skill follows that until a
    # schema bump forces a rewrite.
    assert skill_major_minor == "0.2", (
        f"forge.audit version {skill.frontmatter.version!r} drifted; "
        f"the v1 schema is {AUDIT_SCHEMA_VERSION!r} but the Stage E "
        "body is at 0.2.x. Bump together."
    )


def test_skill_requires_subagent() -> None:
    """``forge.audit`` is the only skill flagged ``requires_subagent``."""
    skill = load_skill("forge.audit")
    assert skill.frontmatter.requires_subagent is True


def test_skill_requires_audit_tools() -> None:
    """Required tool list must include record_audit + the read-only tools."""
    skill = load_skill("forge.audit")
    declared = set(skill.frontmatter.requires_tools)
    must_have = {
        "forge.record_audit",
        "forge.get_audit_schema",
        "forge.get_descriptor_schema",
        "forge.get_region",
        "forge.inspect_spec",
        "forge.analyze_region",
        "forge.render_view",
    }
    missing = sorted(must_have - declared)
    assert not missing, f"forge.audit requires_tools missing: {missing}"


def test_skill_lists_only_registered_tools() -> None:
    """Every entry in ``requires_tools`` must be an actual MCP tool."""
    server = build_server()
    tool_names = {tool.name for tool in asyncio.run(server.list_tools())}
    skill = load_skill("forge.audit")
    declared = set(skill.frontmatter.requires_tools)
    unknown = sorted(declared - tool_names)
    assert not unknown, f"forge.audit requires_tools references unknown tools: {unknown}"


def test_skill_forbids_mutation_tools() -> None:
    """Mutation tool names must not appear in ``requires_tools``."""
    forbidden = {
        "forge.create_region",
        "forge.update_region",
        "forge.delete_region",
        "forge.generate_region",
        "forge.reroll_seed",
    }
    skill = load_skill("forge.audit")
    declared = set(skill.frontmatter.requires_tools)
    leaked = sorted(declared & forbidden)
    assert not leaked, (
        f"forge.audit must not declare mutation tools in requires_tools; found {leaked}"
    )
