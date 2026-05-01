"""Shared fixtures for the Blender-host integration suite."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from forge_mcp.descriptor.schema import StructuredDescriptor, Terrain, TerrainPrimary
from forge_mcp.project.service import ProjectService
from forge_mcp.realize import BLENDER_BIN_ENV, BlenderProcess
from forge_mcp.realize.engine import RealizerEngine
from forge_mcp.server.tools import set_realizer_factory, set_service
from forge_mcp.server.tools.projects import create_project
from forge_mcp.server.tools.regions import create_region

if TYPE_CHECKING:
    from collections.abc import Iterator

_BOUNDS: dict[str, object] = {"min": [0.0, 0.0], "max": [10.0, 10.0]}
_SQUARE = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
_REASON = f"requires ${BLENDER_BIN_ENV} pointing at a real Blender 5.0 binary"


def _blender_unavailable() -> bool:
    raw = os.environ.get(BLENDER_BIN_ENV)
    return not raw or not Path(raw).exists()


# Skip the entire integration package whenever the env var is missing so the
# default headless test run keeps these out of the way without per-test
# decorators.
collect_ignore_glob: list[str] = []
if _blender_unavailable():
    pytest.skip(_REASON, allow_module_level=True)


@pytest.fixture
def isolated_service() -> Iterator[ProjectService]:
    """Install a fresh in-memory ProjectService for the test."""
    svc = ProjectService()
    set_service(svc)
    try:
        yield svc
    finally:
        set_realizer_factory(None)


@pytest.fixture
def real_blender_factory() -> Iterator[None]:
    """Install a realizer factory backed by a real Blender 5.0 process."""

    @contextmanager
    def factory() -> Iterator[RealizerEngine]:
        with BlenderProcess() as proc:
            yield RealizerEngine(proc.client)

    set_realizer_factory(factory)
    try:
        yield
    finally:
        set_realizer_factory(None)


def _ok(envelope: dict[str, object]) -> dict[str, object]:
    assert envelope["ok"] is True, envelope
    result = envelope["result"]
    assert isinstance(result, dict)
    return result


def bootstrap_region(tmp_path: Path) -> str:
    """Create a project + a single rolling-hills region; return its node id."""
    _ok(create_project(str(tmp_path), "IntegrationDemo", _BOUNDS))
    descriptor = StructuredDescriptor(terrain=Terrain(primary=TerrainPrimary.ROLLING_HILLS))
    region = _ok(
        create_region(
            "Alpha",
            _SQUARE,
            structured_descriptor=descriptor.model_dump(mode="json"),
            seed=7,
        ),
    )
    return cast("str", region["node_id"])
