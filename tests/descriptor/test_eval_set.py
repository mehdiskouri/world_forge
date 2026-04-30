"""Phase-3 acceptance: structural regressions over the canonical eval set.

The descriptors live in :mod:`forge_mcp.eval`; this module asserts
ordering rules between their analyses so a regression in
``TERRAIN_PROFILES`` cannot land silently. Visual contrast is the
contact-sheet's job (``scripts/eval/render_eval_set.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest
from forge_mcp.analyze.terrain_analysis import TerrainAnalysis, analyze
from forge_mcp.descriptor.map_to_spec import map_to_spec
from forge_mcp.eval import (
    EVAL_BLENDER_VERSION,
    EVAL_BPY_HYPERGRAPH_VERSION,
    EVAL_DESCRIPTORS,
    EVAL_NOW,
    EVAL_SEED,
    EVAL_SHAPE,
)
from forge_mcp.generate.terrain import run

if TYPE_CHECKING:
    from forge_mcp.descriptor.schema import StructuredDescriptor

_EXPECTED_ENTRY_COUNT: Final[int] = 5


def _analyze_one(descriptor: StructuredDescriptor) -> TerrainAnalysis:
    spec = map_to_spec(
        descriptor,
        seed=EVAL_SEED,
        blender_version=EVAL_BLENDER_VERSION,
        bpy_hypergraph_version=EVAL_BPY_HYPERGRAPH_VERSION,
        now=EVAL_NOW,
    )
    result = run(spec, seed=EVAL_SEED, shape=EVAL_SHAPE)
    return analyze(result.heightmap, result.stream_geometry)


@pytest.fixture(scope="module")
def analyses() -> dict[str, TerrainAnalysis]:
    """Run the full eval set once and share the results."""
    return {label: _analyze_one(d) for label, d in EVAL_DESCRIPTORS}


def test_descriptor_set_is_complete() -> None:
    labels = [label for label, _ in EVAL_DESCRIPTORS]
    assert len(labels) == len(set(labels))
    assert len(EVAL_DESCRIPTORS) == _EXPECTED_ENTRY_COUNT


def test_canyon_carries_more_slope_tail_than_rolling_hills(
    analyses: dict[str, TerrainAnalysis],
) -> None:
    assert (
        analyses["canyon_dry_wash"].slope_degrees.p95
        > analyses["rolling_hills_dry"].slope_degrees.p95
    )


def test_alpine_valley_more_rugged_than_boreal_lowland(
    analyses: dict[str, TerrainAnalysis],
) -> None:
    assert (
        analyses["alpine_valley_with_creek"].slope_degrees.p95
        > analyses["boreal_lowland_meander"].slope_degrees.p95
    )


def test_alpine_valley_taller_than_boreal_lowland(
    analyses: dict[str, TerrainAnalysis],
) -> None:
    assert (
        analyses["alpine_valley_with_creek"].elevation.max
        > analyses["boreal_lowland_meander"].elevation.max
    )


def test_streams_present_only_when_descriptor_has_hydrology(
    analyses: dict[str, TerrainAnalysis],
) -> None:
    expected_with_stream = {
        "alpine_valley_with_creek",
        "boreal_lowland_meander",
        "canyon_dry_wash",
    }
    for label, analysis in analyses.items():
        if label in expected_with_stream:
            assert analysis.stream is not None, label
        else:
            assert analysis.stream is None, label


def test_eval_set_is_deterministic() -> None:
    descriptor = EVAL_DESCRIPTORS[0][1]
    first = _analyze_one(descriptor)
    second = _analyze_one(descriptor)
    assert first.model_dump_json() == second.model_dump_json()
