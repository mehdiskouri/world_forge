"""Phase 6-e Stage D: instancer density-cap unit tests.

Exercises ``_check_instancer_density`` in isolation so the per-region
primitive ceiling is enforced before any heavy realize work runs.
"""

from __future__ import annotations

import pytest
from forge_mcp.project.schemas import RegionId
from forge_mcp.server.tools.generation import (
    GrassDensityTooHighError,
    _check_instancer_density,
)


def test_check_instancer_density_empty_layers_passes() -> None:
    """Empty ``instancer_layers`` is a no-op."""
    _check_instancer_density([], area_m2=1_000.0, region_id=RegionId("r1"))


def test_check_instancer_density_layer_without_instancer_passes() -> None:
    """Layers lacking an ``instancer`` block contribute zero density."""
    _check_instancer_density(
        [{"recipe": "procedural_grass", "parameters": {}}],
        area_m2=1_000_000.0,
        region_id=RegionId("r2"),
    )


def test_check_instancer_density_under_cap_passes() -> None:
    """``density * area`` at or below the cap is accepted."""
    _check_instancer_density(
        [
            {
                "recipe": "procedural_grass",
                "instancer": {"density_per_m2": 100.0},
            },
        ],
        area_m2=1_000.0,
        region_id=RegionId("r3"),
    )


def test_check_instancer_density_over_cap_raises() -> None:
    """``density * area`` above the cap raises with structured fields."""
    with pytest.raises(GrassDensityTooHighError) as exc_info:
        _check_instancer_density(
            [
                {
                    "recipe": "procedural_grass",
                    "instancer": {"density_per_m2": 1_000.0},
                },
            ],
            area_m2=1_000_000.0,
            region_id=RegionId("r4"),
        )
    err = exc_info.value
    assert err.region_id == RegionId("r4")
    assert err.requested == pytest.approx(1_000_000_000.0)
    assert err.cap == pytest.approx(5_000_000.0)
    assert err.area_m2 == pytest.approx(1_000_000.0)
    assert err.density_per_m2 == pytest.approx(1_000.0)


def test_check_instancer_density_sums_across_layers() -> None:
    """Multiple instancer layers contribute additively to the total."""
    with pytest.raises(GrassDensityTooHighError) as exc_info:
        _check_instancer_density(
            [
                {"instancer": {"density_per_m2": 50.0}},
                {"instancer": {"density_per_m2": 60.0}},
            ],
            area_m2=100_000.0,
            region_id=RegionId("r5"),
        )
    err = exc_info.value
    assert err.density_per_m2 == pytest.approx(110.0)
    assert err.requested == pytest.approx(11_000_000.0)
