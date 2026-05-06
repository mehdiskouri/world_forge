"""Tests for the Phase 6 Stage C edge-conform pass."""

from __future__ import annotations

import numpy as np
import pytest
from forge_mcp.generate import boundary_conform
from forge_mcp.generate.boundary_conform import (
    apply_edge_contract,
    edge_profile,
)
from forge_mcp.generate.heightmap import Heightmap


def _flat_heightmap(value: float = 0.5, size: int = 16, res: float = 10.0) -> Heightmap:
    data = np.full((size, size), value, dtype=np.float32)
    return Heightmap(
        data=data,
        resolution_meters_per_pixel=res,
        origin=(0.0, 0.0),
        elevation_band=(0.0, 1.0),
    )


def test_smoothstep_endpoints_and_monotone() -> None:
    u = np.linspace(0.0, 1.0, 11, dtype=np.float32)
    s = boundary_conform._smoothstep(u)  # noqa: SLF001 - testing helper
    assert s[0] == pytest.approx(0.0)
    assert s[-1] == pytest.approx(1.0)
    assert np.all(np.diff(s) >= 0.0)


def test_smoothstep_clipped_outside_unit_interval() -> None:
    u = np.array([-1.0, -0.1, 0.0, 0.5, 1.0, 1.5], dtype=np.float32)
    s = boundary_conform._smoothstep(u)  # noqa: SLF001 - testing helper
    assert s[0] == pytest.approx(0.0)
    assert s[1] == pytest.approx(0.0)
    assert s[-1] == pytest.approx(1.0)
    assert s[3] == pytest.approx(0.5)


def test_resample_samples_constant_extends_to_pixel_count() -> None:
    out = boundary_conform._resample_samples_to_pixels((0.7,), 5)  # noqa: SLF001
    assert out.shape == (5,)
    assert np.allclose(out, 0.7)


def test_resample_samples_linear_endpoints_preserved() -> None:
    out = boundary_conform._resample_samples_to_pixels((0.0, 1.0), 5)  # noqa: SLF001
    assert out[0] == pytest.approx(0.0)
    assert out[-1] == pytest.approx(1.0)
    assert out[2] == pytest.approx(0.5)


def test_resample_samples_rejects_empty() -> None:
    with pytest.raises(ValueError, match="samples must be non-empty"):
        boundary_conform._resample_samples_to_pixels((), 4)  # noqa: SLF001


def test_resample_samples_rejects_zero_pixel_count() -> None:
    with pytest.raises(ValueError, match="pixel_count must be positive"):
        boundary_conform._resample_samples_to_pixels((0.5,), 0)  # noqa: SLF001


def test_apply_edge_contract_sets_north_row_to_target() -> None:
    hm = _flat_heightmap(value=0.0)
    samples = tuple([0.9] * 16)
    out = apply_edge_contract(
        hm,
        side="north",
        samples=samples,
        inland_falloff_m=50.0,
    )
    # Row 0 (north edge) should be exactly the contract.
    assert np.allclose(out.data[0, :], 0.9, atol=1e-6)
    # Bottom row is far inland of a 50 m falloff at 10 m/px (5 px) → unchanged.
    assert np.allclose(out.data[-1, :], 0.0, atol=1e-6)


def test_apply_edge_contract_south_row_at_bottom() -> None:
    hm = _flat_heightmap(value=0.0)
    out = apply_edge_contract(
        hm,
        side="south",
        samples=tuple([0.4] * 16),
        inland_falloff_m=30.0,
    )
    assert np.allclose(out.data[-1, :], 0.4, atol=1e-6)
    assert np.allclose(out.data[0, :], 0.0, atol=1e-6)


def test_apply_edge_contract_west_column() -> None:
    hm = _flat_heightmap(value=0.0)
    out = apply_edge_contract(
        hm,
        side="west",
        samples=tuple([0.3] * 16),
        inland_falloff_m=30.0,
    )
    assert np.allclose(out.data[:, 0], 0.3, atol=1e-6)
    assert np.allclose(out.data[:, -1], 0.0, atol=1e-6)


def test_apply_edge_contract_east_column() -> None:
    hm = _flat_heightmap(value=0.0)
    out = apply_edge_contract(
        hm,
        side="east",
        samples=tuple([0.6] * 16),
        inland_falloff_m=30.0,
    )
    assert np.allclose(out.data[:, -1], 0.6, atol=1e-6)
    assert np.allclose(out.data[:, 0], 0.0, atol=1e-6)


def test_apply_edge_contract_falloff_monotone_inland() -> None:
    hm = _flat_heightmap(value=0.0)
    samples = tuple([1.0] * 16)
    out = apply_edge_contract(
        hm,
        side="north",
        samples=samples,
        inland_falloff_m=80.0,
    )
    col = out.data[:, 0]
    # Falloff is 8 px; col should decrease monotonically over that range.
    assert np.all(np.diff(col[:8]) <= 1e-6)  # noqa: PLR2004 - epsilon for monotonicity
    # Past the falloff band, the heightmap is untouched.
    assert col[-1] == pytest.approx(0.0)


def test_apply_edge_contract_rejects_non_positive_falloff() -> None:
    hm = _flat_heightmap()
    with pytest.raises(ValueError, match="inland_falloff_m must be positive"):
        apply_edge_contract(
            hm,
            side="north",
            samples=(0.5,),
            inland_falloff_m=0.0,
        )


def test_edge_profile_north_returns_top_row() -> None:
    data = np.arange(16, dtype=np.float32).reshape((4, 4))
    hm = Heightmap(
        data=data,
        resolution_meters_per_pixel=10.0,
        origin=(0.0, 0.0),
        elevation_band=(0.0, 1.0),
    )
    profile = edge_profile(hm, "north")
    assert profile.tolist() == [0.0, 1.0, 2.0, 3.0]


def test_edge_profile_west_returns_first_column() -> None:
    data = np.arange(16, dtype=np.float32).reshape((4, 4))
    hm = Heightmap(
        data=data,
        resolution_meters_per_pixel=10.0,
        origin=(0.0, 0.0),
        elevation_band=(0.0, 1.0),
    )
    profile = edge_profile(hm, "west")
    assert profile.tolist() == [0.0, 4.0, 8.0, 12.0]
