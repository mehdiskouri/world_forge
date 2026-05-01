"""Phase-3 evaluation set: the canonical 5 descriptors used to lock the generator.

Lives outside the test tree so :mod:`scripts.eval.render_eval_set` and
:mod:`tests.descriptor.test_eval_set` can both import the same source
of truth without test-import gymnastics. See the Phase-3 plan
(Stage G) for the rationale: a tiny set picked for visual contrast,
held stable so contact sheets across PRs are diffable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from forge_mcp.descriptor.schema import (
    Hydrology,
    StreamCharacter,
    StructuredDescriptor,
    Terrain,
    TerrainPrimary,
)

EVAL_SHAPE: Final[tuple[int, int]] = (512, 512)
"""Per-axis pixel count used by every eval-set render.

At the default ``resolution_meters_per_pixel = 2`` from
:mod:`forge_mcp.descriptor.map_to_spec`, this gives a 1024 m x 1024 m
world per region — wide enough that the 80-220 m noise scales in
:data:`TERRAIN_PROFILES` produce ~5-12 visible features per axis
instead of one or two blobs. The realizer mesh stays capped at
``MAX_RESOLUTION = 256`` verts/axis (see :mod:`heightmap_mesh`), so
this only costs heightmap-generation time, not Blender time.
"""
EVAL_SEED: Final[int] = 17
EVAL_NOW: Final[datetime] = datetime(2026, 4, 30, tzinfo=UTC)
EVAL_BLENDER_VERSION: Final[str] = "5.0.0"
EVAL_BPY_HYPERGRAPH_VERSION: Final[str] = "0.0.0"


def _descriptor(
    primary: TerrainPrimary,
    *,
    ruggedness: float | None = None,
    stream: StreamCharacter | None = None,
) -> StructuredDescriptor:
    """Build one frozen :class:`StructuredDescriptor` for the eval set."""
    hydrology = Hydrology(has_stream=True, stream_character=stream) if stream is not None else None
    return StructuredDescriptor(
        terrain=Terrain(primary=primary, ruggedness=ruggedness),
        hydrology=hydrology,
    )


EVAL_DESCRIPTORS: Final[tuple[tuple[str, StructuredDescriptor], ...]] = (
    (
        "alpine_valley_with_creek",
        _descriptor(
            TerrainPrimary.ALPINE_VALLEY,
            ruggedness=0.8,
            stream=StreamCharacter.ALPINE_CREEK,
        ),
    ),
    ("rolling_hills_dry", _descriptor(TerrainPrimary.ROLLING_HILLS, ruggedness=0.4)),
    ("desert_mesa", _descriptor(TerrainPrimary.DESERT_MESA, ruggedness=0.6)),
    (
        "boreal_lowland_meander",
        _descriptor(
            TerrainPrimary.BOREAL_LOWLAND,
            ruggedness=0.2,
            stream=StreamCharacter.MEANDERING_RIVER,
        ),
    ),
    (
        "canyon_dry_wash",
        _descriptor(
            TerrainPrimary.CANYON,
            ruggedness=0.7,
            stream=StreamCharacter.DRY_WASH,
        ),
    ),
)


__all__ = [
    "EVAL_BLENDER_VERSION",
    "EVAL_BPY_HYPERGRAPH_VERSION",
    "EVAL_DESCRIPTORS",
    "EVAL_NOW",
    "EVAL_SEED",
    "EVAL_SHAPE",
]
