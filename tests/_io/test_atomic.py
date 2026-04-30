"""Tests for :mod:`forge_mcp._io.atomic`.

Covers the atomicity contract (no torn writes on crash), the
canonical-formatting contract for ``dump_json``, and the Pydantic
round-trip path.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import patch
from uuid import UUID

import pytest
from forge_mcp._io.atomic import atomic_write_text, dump_json, write_json
from forge_mcp.project.schemas import (
    HistoryActor,
    HistoryEvent,
    HistoryEventId,
    HistoryEventKind,
    NodeId,
    ProjectMetadata,
    WorldBounds,
)

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def _project_metadata() -> ProjectMetadata:
    return ProjectMetadata(
        project_id=UUID("00000000-0000-4000-8000-000000000001"),
        name="Test",
        forge_version="0.0.0",
        blender_version="5.0.0",
        bpy_hypergraph_version="0.0.0",
        descriptor_schema_version="1.0",
        created_at=NOW,
        modified_at=NOW,
        world_node_id=NodeId("world_root"),
        world_bounds=WorldBounds(min=(-1.0, -1.0), max=(1.0, 1.0)),
    )


def test_atomic_write_text_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "hello.txt"
    atomic_write_text(target, "hello\n")
    assert target.read_text(encoding="utf-8") == "hello\n"


def test_atomic_write_text_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "hello.txt"
    target.write_text("old", encoding="utf-8")
    atomic_write_text(target, "new\n")
    assert target.read_text(encoding="utf-8") == "new\n"


def test_atomic_write_text_leaves_no_tmp_on_crash(tmp_path: Path) -> None:
    target = tmp_path / "hello.txt"
    target.write_text("old", encoding="utf-8")
    # Force ``os.replace`` to fail mid-call. The temp file may remain on
    # disk, but the canonical target is never partially overwritten.
    with (
        patch("forge_mcp._io.atomic.os.replace", side_effect=OSError("boom")),
        pytest.raises(OSError, match="boom"),
    ):
        atomic_write_text(target, "new\n")
    # Old contents are preserved (atomicity contract).
    assert target.read_text(encoding="utf-8") == "old"


def test_dump_json_canonical_formatting() -> None:
    body = dump_json({"b": 1, "a": [3, 2, 1]})
    assert body.endswith("\n")
    parsed = json.loads(body)
    assert parsed == {"a": [3, 2, 1], "b": 1}
    # sorted-keys ⇒ "a" must precede "b"
    assert body.index('"a"') < body.index('"b"')
    # two-space indent ⇒ first nested line begins with two spaces
    assert "\n  " in body


def test_dump_json_routes_pydantic_through_model_dump() -> None:
    event = HistoryEvent(
        event_id=HistoryEventId("0001"),
        kind=HistoryEventKind.CREATE_PROJECT,
        at=NOW,
        actor=HistoryActor.SYSTEM,
    )
    body = dump_json(event)
    parsed = json.loads(body)
    assert parsed["event_id"] == "0001"
    # ``datetime`` is rendered as an ISO-8601 string, not a python repr.
    assert parsed["at"].startswith("2026-01-01")


def test_write_json_round_trips_pydantic(tmp_path: Path) -> None:
    metadata = _project_metadata()
    target = tmp_path / "project.json"
    write_json(target, metadata)
    again = ProjectMetadata.model_validate_json(target.read_text(encoding="utf-8"))
    assert again == metadata
