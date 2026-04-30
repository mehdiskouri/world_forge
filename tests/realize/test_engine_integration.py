"""Integration tests for RealizerEngine driving real Blender 5.0."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from forge_mcp.realize import BLENDER_BIN_ENV, BlenderProcess
from forge_mcp.realize.engine import RealizerEngine

_REASON = f"requires ${BLENDER_BIN_ENV} pointing at a real Blender 5.0 binary"


def _blender_unavailable() -> bool:
    raw = os.environ.get(BLENDER_BIN_ENV)
    return not raw or not Path(raw).exists()


@pytest.mark.blender_integration
@pytest.mark.skipif(_blender_unavailable(), reason=_REASON)
def test_engine_constructs_against_real_blender_and_ping_matches_version() -> None:
    with BlenderProcess() as proc:
        engine = RealizerEngine(proc.client)
        assert engine.bundle.blender_version == "5.0.0"


@pytest.mark.blender_integration
@pytest.mark.skipif(_blender_unavailable(), reason=_REASON)
def test_engine_executes_reset_scene_macro() -> None:
    with BlenderProcess() as proc:
        engine = RealizerEngine(proc.client)
        result = engine.execute_macro("reset_scene", {})
    assert result.macro == "reset_scene"
    assert len(result.trace) >= 1
    assert all(t.duration_ms >= 0 for t in result.trace)
