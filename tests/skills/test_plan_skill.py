"""Phase 5 Stage B coherence tests for ``forge.plan``.

Validates:

* The shipped ``forge.plan/eval_set.json`` file parses, has >= 10
  examples, and every example descriptor validates against the live
  ``StructuredDescriptor`` schema.
* The skill version stays in lock-step with
  ``forge_mcp.descriptor.SCHEMA_VERSION`` (Phase 5 Stage B item 4).

The full byte-identity test for the embedded JSON Schema fence lives
in Phase 5 Stage F (``tests/skills/test_skill_files.py``).
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest
from forge_mcp.descriptor.schema import SCHEMA_VERSION, StructuredDescriptor
from forge_mcp.server.mcp import build_server
from forge_mcp.skills.loader import load_skill

if TYPE_CHECKING:
    from collections.abc import Iterator

_MIN_EXAMPLE_COUNT = 10


def _load_eval_set() -> dict[str, object]:
    skill = load_skill("forge.plan")
    body = skill.embedded_assets["eval_set.json"]
    parsed = json.loads(body)
    assert isinstance(parsed, dict)
    return parsed


def _iter_examples() -> Iterator[dict[str, object]]:
    eval_set = _load_eval_set()
    examples = eval_set["examples"]
    assert isinstance(examples, list)
    for entry in examples:
        assert isinstance(entry, dict)
        yield entry


def test_eval_set_has_at_least_ten_examples() -> None:
    """PRD R-2 mitigation: at least 10 canonical examples."""
    count = sum(1 for _ in _iter_examples())
    assert count >= _MIN_EXAMPLE_COUNT


def test_every_example_validates_against_descriptor_schema() -> None:
    """Each example descriptor round-trips through the Pydantic model."""
    for entry in _iter_examples():
        descriptor = entry["descriptor"]
        StructuredDescriptor.model_validate(descriptor)


def test_every_example_has_unique_id_and_freetext() -> None:
    """Examples are keyed by ``id`` and carry the original prompt verbatim."""
    seen: set[str] = set()
    for entry in _iter_examples():
        eid = entry["id"]
        assert isinstance(eid, str)
        assert eid not in seen, f"duplicate example id: {eid!r}"
        seen.add(eid)
        assert isinstance(entry["free_text"], str)
        assert entry["free_text"].strip() != ""


def test_eval_set_pins_descriptor_schema_version() -> None:
    """The fixture records the schema version so drift is loud."""
    eval_set = _load_eval_set()
    assert eval_set["schema_version"] == SCHEMA_VERSION


def test_skill_version_tracks_descriptor_schema_version() -> None:
    """``forge.plan`` major.minor must match descriptor schema version.

    Stage B item 4: the skill body is a presentation of the schema; if
    the schema bumps, the skill text must be rewritten and the version
    bumped at the same time.
    """
    skill = load_skill("forge.plan")
    skill_major_minor = ".".join(skill.frontmatter.version.split(".")[:2])
    assert skill_major_minor == SCHEMA_VERSION, (
        f"forge.plan version {skill.frontmatter.version!r} drifted from "
        f"descriptor SCHEMA_VERSION={SCHEMA_VERSION!r}; bump together."
    )


def test_skill_lists_only_registered_tools() -> None:
    """``requires_tools`` must reference real ``forge.*`` MCP tools."""
    server = build_server()
    tool_names = {tool.name for tool in asyncio.run(server.list_tools())}
    skill = load_skill("forge.plan")
    declared = set(skill.frontmatter.requires_tools)
    missing = sorted(declared - tool_names)
    assert not missing, f"forge.plan requires_tools references unknown tools: {missing}"


def test_skill_body_quotes_each_eval_set_freetext() -> None:
    """Every eval-set ``free_text`` appears verbatim in the SKILL.md body.

    Stage B requirement: SKILL.md examples and eval_set are the single
    source of truth. Cheap substring check is enough to catch drift; the
    Stage F harness adds the descriptor-equality check.
    """
    skill = load_skill("forge.plan")
    body = skill.body_markdown
    for entry in _iter_examples():
        free_text = entry["free_text"]
        assert isinstance(free_text, str)
        assert free_text in body, (
            f"eval_set example {entry['id']!r} text not found verbatim in SKILL.md; "
            "skill examples and eval_set must stay aligned."
        )


@pytest.mark.parametrize(
    "stream_character",
    [
        "alpine_creek",
        "meandering_river",
        "dry_wash",
    ],
)
def test_eval_set_covers_each_stream_character(stream_character: str) -> None:
    """At least one example descriptor uses every concrete stream character."""
    matches = [
        e
        for e in _iter_examples()
        if isinstance(e["descriptor"], dict)
        and isinstance(e["descriptor"].get("hydrology"), dict)
        and e["descriptor"]["hydrology"].get("stream_character") == stream_character
    ]
    assert matches, f"no eval-set example uses stream_character={stream_character!r}"
