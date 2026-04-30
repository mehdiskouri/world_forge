"""Tests for the realization-trace sidecar models + persistence."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from forge_mcp.realize.engine import RealizationResult, RealizationTraceStep
from forge_mcp.realize.realization import (
    RealizationTraceRecord,
    record_from_result,
    write_trace_record,
)

if TYPE_CHECKING:
    from pathlib import Path


def _make_result() -> RealizationResult:
    return RealizationResult(
        macro="reset_scene",
        trace=(
            RealizationTraceStep(
                sequence_name="reset_scene",
                step_index=0,
                call="bpy.ops.wm.read_factory_settings",
                duration_ms=12.5,
                scene_diff_before={"objects": 3},
                scene_diff_after={"objects": 0},
                result={"ok": True},
            ),
        ),
        final_result={"ok": True},
        total_duration_ms=12.5,
        sequence_id="abcdef0123456789abcd",
    )


def test_record_from_result_round_trips_through_pydantic() -> None:
    record = record_from_result(_make_result(), region_id="r-1", view_kind="preview")
    assert isinstance(record, RealizationTraceRecord)
    assert record.region_id == "r-1"
    assert record.view_kind == "preview"
    assert record.macro == "reset_scene"
    assert record.sequence_id == "abcdef0123456789abcd"
    assert record.steps[0].scene_diff_before == {"objects": 3}
    assert record.steps[0].scene_diff_after == {"objects": 0}


def test_write_trace_record_writes_canonical_json(tmp_path: Path) -> None:
    record = record_from_result(_make_result(), region_id="r-1", view_kind="default")
    path = tmp_path / "out.json"
    write_trace_record(path, record)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["region_id"] == "r-1"
    assert payload["view_kind"] == "default"
    assert payload["steps"][0]["call"] == "bpy.ops.wm.read_factory_settings"
    # canonical JSON: keys sorted
    assert list(payload.keys()) == sorted(payload.keys())


def test_record_handles_missing_scene_diffs() -> None:
    result = RealizationResult(
        macro="ping",
        trace=(
            RealizationTraceStep(
                sequence_name="ping",
                step_index=0,
                call="ping",
                duration_ms=1.0,
                scene_diff_before=None,
                scene_diff_after=None,
                result=None,
            ),
        ),
        final_result=None,
        total_duration_ms=1.0,
        sequence_id="0" * 20,
    )
    record = record_from_result(result, region_id="r-2", view_kind="full")
    assert record.steps[0].scene_diff_before is None
    assert record.steps[0].scene_diff_after is None
