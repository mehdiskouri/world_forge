"""Tests for :mod:`forge_mcp.generate.stream`."""

from __future__ import annotations

import numpy as np
from forge_mcp.generate.heightmap import Heightmap
from forge_mcp.generate.stream import StreamGeometry, inject_stream
from forge_mcp.project.schemas import StreamFeatureInjector

_TEST_WIDTH = 12.0
_TEST_DEPTH = 5.0


def _slope_terrain(shape: tuple[int, int] = (32, 32)) -> Heightmap:
    """Build a tilted-plane heightmap with a small horizontal ripple.

    The horizontal ripple gives the steepest-descent walker meaningful
    east/west choices, so RNG jitter actually changes the realised path
    across seeds rather than collapsing onto a single ridgeline.
    """
    height, width = shape
    rows = np.linspace(100.0, 0.0, height, dtype=np.float32).reshape(-1, 1)
    ripple = (np.sin(np.linspace(0.0, 4.0 * float(np.pi), width, dtype=np.float32)) * 5.0).reshape(
        1, -1
    )
    return Heightmap(
        data=(rows + ripple).astype(np.float32),
        resolution_meters_per_pixel=2.0,
        origin=(0.0, 0.0),
        elevation_band=(0.0, 100.0),
    )


def _injector(width: float = 6.0, depth: float = 3.0) -> StreamFeatureInjector:
    return StreamFeatureInjector(width_meters=width, carving_depth=depth)


def test_inject_is_deterministic_for_same_seed() -> None:
    hm = _slope_terrain()
    a_hm, a_geo = inject_stream(hm, _injector(), seed=1)
    b_hm, b_geo = inject_stream(hm, _injector(), seed=1)
    assert np.array_equal(a_hm.data, b_hm.data)
    assert a_geo == b_geo


def test_inject_differs_for_different_seeds() -> None:
    hm = _slope_terrain()
    a_hm, _ = inject_stream(hm, _injector(), seed=1)
    b_hm, _ = inject_stream(hm, _injector(), seed=2)
    assert not np.array_equal(a_hm.data, b_hm.data)


def test_carving_lowers_elevation_along_path() -> None:
    hm = _slope_terrain()
    out_hm, _ = inject_stream(hm, _injector(), seed=3)
    assert float(out_hm.data.min()) < float(hm.data.min())
    # Mass change must be no greater than worst-case carving (channel
    # touches every cell at maximum depth — generous upper bound).
    max_loss = float(hm.data.size) * 3.0  # carving_depth=3.0
    assert float((hm.data - out_hm.data).sum()) <= max_loss


def test_geometry_anchors_lie_on_opposite_edges() -> None:
    hm = _slope_terrain()
    _, geo = inject_stream(hm, _injector(), seed=5)
    height, width = hm.data.shape
    res = hm.resolution_meters_per_pixel
    in_x, in_y = geo.anchor_in
    out_x, out_y = geo.anchor_out

    def on_edge(x: float, y: float) -> bool:
        return x in {0.0, (width - 1) * res} or y in {0.0, (height - 1) * res}

    assert on_edge(in_x, in_y)
    assert on_edge(out_x, out_y)
    # Anchors must not coincide.
    assert (in_x, in_y) != (out_x, out_y)


def test_geometry_records_injector_dimensions() -> None:
    hm = _slope_terrain()
    inj = _injector(width=_TEST_WIDTH, depth=_TEST_DEPTH)
    _, geo = inject_stream(hm, inj, seed=7)
    assert isinstance(geo, StreamGeometry)
    assert geo.width_meters == _TEST_WIDTH
    assert geo.carving_depth == _TEST_DEPTH


def test_path_includes_both_anchor_endpoints() -> None:
    hm = _slope_terrain()
    _, geo = inject_stream(hm, _injector(), seed=9)
    assert geo.path[0] == geo.anchor_in
    assert geo.path[-1] == geo.anchor_out


def test_geometry_preserves_heightmap_referencing() -> None:
    hm = _slope_terrain()
    out_hm, _ = inject_stream(hm, _injector(), seed=13)
    assert out_hm.resolution_meters_per_pixel == hm.resolution_meters_per_pixel
    assert out_hm.origin == hm.origin
    assert out_hm.elevation_band == hm.elevation_band
