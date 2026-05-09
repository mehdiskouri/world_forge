"""Phase 7 Stage D: locks survive ``forge.reroll_seed`` regenerations.

Acceptance for PRD §8.2: lock three named features on a generated
region, reroll the seed three times with ``regenerate=True``, and
confirm the locked patch values reappear in every regenerated
heightmap. A region-lock case also confirms regeneration is skipped
and the on-disk ``.blend`` artefact's mtime stays stable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from forge_mcp.generate.heightmap import load_npy
from forge_mcp.project.schemas import RegionId
from forge_mcp.realize import BlenderProcess
from forge_mcp.server.tools import get_service
from forge_mcp.server.tools.generation import generate_region, reroll_seed
from forge_mcp.server.tools.locks import lock_feature, lock_region

from tests.integration.conftest import bootstrap_region

if TYPE_CHECKING:
    from pathlib import Path

    from forge_mcp.project.service import ProjectService


_TOLERANCE = 1.0e-3
_REROLL_SEEDS = (101, 202, 303)


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


def _bbox_pixel_centre(
    bbox: tuple[float, float, float, float],
    origin: tuple[float, float],
    resolution_m: float,
) -> tuple[int, int]:
    cx = (bbox[0] + bbox[2]) / 2.0
    cy = (bbox[1] + bbox[3]) / 2.0
    return int((cy - origin[1]) / resolution_m), int((cx - origin[0]) / resolution_m)


@pytest.mark.blender_integration
def test_three_feature_locks_survive_three_rerolls(
    tmp_path: Path,
    isolated_service: ProjectService,  # noqa: ARG001 - autouses set_service
    real_blender_factory: None,  # noqa: ARG001 - autouses set_realizer_factory
) -> None:
    """PRD §8.2: locked patches reappear after three seed rerolls."""
    rid_str = bootstrap_region(tmp_path)
    rid = RegionId(rid_str)
    _ok(generate_region(rid_str))

    svc = get_service()
    npy_path = svc.state.paths.heightmap_npy_path(rid)
    baseline = load_npy(npy_path)
    height_m, width_m = baseline.data.shape[0], baseline.data.shape[1]
    res = baseline.resolution_meters_per_pixel
    ox, oy = baseline.origin
    # Three non-overlapping bboxes covering interior pixels (each is
    # large enough that the 4-pixel cosine feather still leaves a
    # unity-weight interior pixel at its centre).
    span_world = max(res * 12.0, 12.0)
    bbox_a = (ox + res * 4, oy + res * 4, ox + res * 4 + span_world, oy + res * 4 + span_world)
    bbox_b = (
        ox + res * (width_m - 16),
        oy + res * 4,
        ox + res * (width_m - 16) + span_world,
        oy + res * 4 + span_world,
    )
    bbox_c = (
        ox + res * 4,
        oy + res * (height_m - 16),
        ox + res * 4 + span_world,
        oy + res * (height_m - 16) + span_world,
    )
    bboxes = (bbox_a, bbox_b, bbox_c)

    # Capture the expected centre values from the baseline heightmap
    # *before* locking (locks copy the patch from the live heightmap).
    expected: list[tuple[int, int, float]] = []
    for bbox in bboxes:
        row, col = _bbox_pixel_centre(bbox, (ox, oy), res)
        expected.append((row, col, float(baseline.data[row, col])))
        _ok(lock_feature(rid_str, list(bbox)))

    for new_seed in _REROLL_SEEDS:
        out = _ok(reroll_seed(rid_str, seed=new_seed, regenerate=True))
        assert out["seed"] == new_seed
        regen = load_npy(npy_path)
        for row, col, expected_value in expected:
            assert regen.data[row, col] == pytest.approx(expected_value, abs=_TOLERANCE), (
                f"locked centre ({row},{col}) drifted after reroll seed={new_seed}"
            )

    # Sanity: the latest blend file is still openable by Blender (the
    # regenerated realization survived the lock-blend pipeline).
    blend_path = svc.state.paths.blend_path(rid)
    assert blend_path.is_file()
    with BlenderProcess() as proc:
        proc.client.call("bpy.ops.wm.open_mainfile", {"filepath": str(blend_path)})


@pytest.mark.blender_integration
def test_region_lock_keeps_blend_mtime_stable_across_rerolls(
    tmp_path: Path,
    isolated_service: ProjectService,  # noqa: ARG001
    real_blender_factory: None,  # noqa: ARG001
) -> None:
    """A ``LockKind.REGION`` lock skips regen and leaves the .blend untouched."""
    rid_str = bootstrap_region(tmp_path)
    rid = RegionId(rid_str)
    _ok(generate_region(rid_str))

    svc = get_service()
    blend_path = svc.state.paths.blend_path(rid)
    assert blend_path.is_file()
    mtime_before = blend_path.stat().st_mtime_ns

    _ok(lock_region(rid_str))

    for new_seed in _REROLL_SEEDS:
        # ``regenerate=True`` returns the skip envelope from generate_region.
        error = _err(reroll_seed(rid_str, seed=new_seed, regenerate=True))
        assert error["code"] == "region_lock_skipped"
        details = error["details"]
        assert isinstance(details, dict)
        assert details["region_id"] == rid_str

    assert blend_path.stat().st_mtime_ns == mtime_before, (
        "region-locked .blend was rewritten across rerolls"
    )

    # The seed itself still rolled forward (reroll_seed mutates before regen).
    region = svc.state.regions[rid]
    assert region.seed == _REROLL_SEEDS[-1]
    _ = cast("object", region)
