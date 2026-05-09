"""Phase 7 Stage C: end-to-end region/feature lock enforcement in ``generate_region``."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import numpy as np
import pytest
from forge_mcp.descriptor.schema import (
    StructuredDescriptor,
    Terrain,
    TerrainPrimary,
)
from forge_mcp.generate import terrain as terrain_generator
from forge_mcp.project.schemas import (
    HistoryEventKind,
    LockKind,
    RegionId,
)
from forge_mcp.project.service import ProjectService
from forge_mcp.server.tools import get_service, set_service
from forge_mcp.server.tools.generation import generate_region
from forge_mcp.server.tools.projects import create_project
from forge_mcp.server.tools.regions import create_region
from freezegun import freeze_time

if TYPE_CHECKING:
    from pathlib import Path


_FROZEN = "2026-05-09T12:00:00+00:00"
_BOUNDS: dict[str, object] = {"min": [0.0, 0.0], "max": [10.0, 10.0]}
_SQUARE = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
_SHAPE = (32, 32)


@pytest.fixture(autouse=True)
def _isolated_service() -> None:
    set_service(ProjectService())


@pytest.fixture(autouse=True)
def _small_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(terrain_generator, "_shape_from_spec", lambda _axis: _SHAPE)


def _ok(envelope: dict[str, object]) -> dict[str, object]:
    assert envelope["ok"] is True, envelope
    result = envelope["result"]
    assert isinstance(result, dict)
    return result


def _err(envelope: dict[str, object]) -> dict[str, object]:
    assert envelope["ok"] is False, envelope
    error = envelope["error"]
    assert isinstance(error, dict)
    return error


def _make_region(name: str = "Alpha", seed: int = 7) -> str:
    descriptor = StructuredDescriptor(terrain=Terrain(primary=TerrainPrimary.ROLLING_HILLS))
    region = _ok(
        create_region(
            name,
            _SQUARE,
            structured_descriptor=descriptor.model_dump(mode="json"),
            seed=seed,
        ),
    )
    return cast("str", region["node_id"])


# ---------------------------------------------------------------------------
# Region-lock short-circuit
# ---------------------------------------------------------------------------


@freeze_time(_FROZEN)
def test_region_lock_skips_generate_region(tmp_path: Path) -> None:
    _ok(create_project(str(tmp_path), "Demo", _BOUNDS))
    rid_str = _make_region()
    rid = RegionId(rid_str)
    svc = get_service()
    record = svc.create_region_lock(region_id=rid)
    history_count_before = svc.state.history.count

    error = _err(generate_region(rid_str))
    assert error["code"] == "region_lock_skipped"
    details = error["details"]
    assert isinstance(details, dict)
    assert details["region_id"] == rid_str
    assert details["lock_id"] == str(record.lock_id)

    # No spec, heightmap, or generation history must be written.
    assert not tmp_path.joinpath("realizations", "heightmap", f"{rid_str}.npy").exists()
    region = svc.state.regions[rid]
    assert region.spec_id is None

    # A `region_lock_skipped` history event was appended.
    assert svc.state.history.count == history_count_before + 1
    events = list(svc.state.history.iter_events())
    last = events[-1]
    assert last.kind is HistoryEventKind.REGION_LOCK_SKIPPED
    assert last.payload["region_id"] == rid_str
    assert last.payload["lock_id"] == str(record.lock_id)


# ---------------------------------------------------------------------------
# Feature-lock blend path
# ---------------------------------------------------------------------------


@freeze_time(_FROZEN)
def test_feature_lock_patch_is_blended_into_regenerated_heightmap(
    tmp_path: Path,
) -> None:
    _ok(create_project(str(tmp_path), "Demo", _BOUNDS))
    rid_str = _make_region()
    rid = RegionId(rid_str)
    svc = get_service()

    # Generate once so a heightmap exists; lock a large inland patch
    # (40m x 40m at 2 m/px == 20 px, so the 4-pixel feather leaves a
    # unity-weight interior the test can sample.)
    _ok(generate_region(rid_str))
    bbox = (10.0, 10.0, 50.0, 50.0)
    record = svc.create_feature_lock(region_id=rid, bbox_world=bbox)

    # Replace the captured patch with a constant sentinel so the blend
    # is observable in the regenerated heightmap (the centre of the
    # patch must equal the sentinel after the cosine feather settles).
    patch_path = svc.state.paths.feature_lock_patch_path(record.lock_id)
    assert patch_path.exists()
    sentinel = np.float32(123.5)
    patch_shape = np.load(patch_path, allow_pickle=False).shape
    np.save(patch_path, np.full(patch_shape, sentinel, dtype=np.float32))

    result = _ok(generate_region(rid_str))
    generators = result["generators_used"]
    assert isinstance(generators, list)
    assert "locks.feature_blend" in generators

    # Centre pixel of the patch should reflect the sentinel within
    # blending tolerance (interior weight is 1.0 inside the feather).
    from forge_mcp.generate.heightmap import load_npy  # noqa: PLC0415

    regen_hm = load_npy(svc.state.paths.heightmap_npy_path(rid))
    ox, oy = regen_hm.origin
    res = regen_hm.resolution_meters_per_pixel
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    centre_col = int((cx - ox) / res)
    centre_row = int((cy - oy) / res)
    assert regen_hm.data[centre_row, centre_col] == pytest.approx(sentinel, abs=1e-3)


@freeze_time(_FROZEN)
def test_feature_lock_missing_patch_returns_envelope(tmp_path: Path) -> None:
    _ok(create_project(str(tmp_path), "Demo", _BOUNDS))
    rid_str = _make_region()
    rid = RegionId(rid_str)
    svc = get_service()
    _ok(generate_region(rid_str))
    record = svc.create_feature_lock(region_id=rid, bbox_world=(2.0, 2.0, 6.0, 6.0))
    # Nuke the patch file so loading fails on the next run.
    svc.state.paths.feature_lock_patch_path(record.lock_id).unlink()

    error = _err(generate_region(rid_str))
    assert error["code"] == "feature_lock_patch_missing"
    message = error["message"]
    assert isinstance(message, str)
    assert str(record.lock_id) in message


@freeze_time(_FROZEN)
def test_feature_lock_records_carry_lock_id(tmp_path: Path) -> None:
    """Defensive: every feature lock yielded to generation has the right kind."""
    _ok(create_project(str(tmp_path), "Demo", _BOUNDS))
    rid_str = _make_region()
    rid = RegionId(rid_str)
    svc = get_service()
    _ok(generate_region(rid_str))
    record = svc.create_feature_lock(region_id=rid, bbox_world=(2.0, 2.0, 6.0, 6.0))
    assert record.kind is LockKind.FEATURE
    assert str(record.lock_id).startswith("lock_")
