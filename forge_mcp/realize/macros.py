"""Typed Python facade over the curated v1 macro library.

One function per curated sequence (Architecture section 5.7). Each
function accepts a strongly-typed input model that mirrors the macro's
``inputs_schema`` and delegates to
:py:meth:`forge_mcp.realize.engine.RealizerEngine.execute_macro`,
returning its full :class:`~forge_mcp.realize.engine.RealizationResult`
so callers always have access to the trace.

This module deliberately contains **zero** ``bpy`` references — a CI
grep guard (``tests/realize/test_no_bpy_in_macros.py``) keeps it that
way. All Blender-side execution happens through the adapter via the
engine; macros only build the input payload and call the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from forge_mcp.realize.engine import RealizationResult, RealizerEngine
    from forge_mcp.realize.rpc import JsonValue


# --- Macro input models ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreateTerrainInputs:
    """Inputs for the ``create_terrain_from_heightmap`` macro."""

    object_name: str
    vertices: Sequence[Sequence[float]]
    faces: Sequence[Sequence[int]]
    region_id: str
    spec_id: str


@dataclass(frozen=True, slots=True)
class ApplyTerrainMaterialInputs:
    """Inputs for the ``apply_terrain_material`` macro."""

    object_name: str
    material_name: str
    color_ramp_stops: Sequence[JsonValue]
    slope_threshold: float


@dataclass(frozen=True, slots=True)
class CarveStreamInputs:
    """Inputs for the ``carve_stream`` macro."""

    curve_name: str
    region_id: str


@dataclass(frozen=True, slots=True)
class SetCameraOverviewInputs:
    """Inputs for the ``set_camera_overview`` macro."""

    ortho_camera_name: str
    perspective_camera_name: str


@dataclass(frozen=True, slots=True)
class AddBasicLightingInputs:
    """Inputs for the ``add_basic_lighting`` macro."""

    sun_name: str
    world_name: str


@dataclass(frozen=True, slots=True)
class RenderPreviewInputs:
    """Inputs for the ``render_preview`` macro."""

    filepath: str
    resolution_x: int
    resolution_y: int
    camera_name: str
    engine: str


@dataclass(frozen=True, slots=True)
class SaveBlendInputs:
    """Inputs for the ``save_blend`` macro."""

    filepath: str


@dataclass(frozen=True, slots=True)
class RealizeRegionInputs:
    """Inputs for the composite ``realize_region`` macro."""

    object_name: str
    vertices: Sequence[Sequence[float]]
    faces: Sequence[Sequence[int]]
    region_id: str
    spec_id: str
    material_name: str
    color_ramp_stops: Sequence[JsonValue]
    slope_threshold: float
    curve_name: str
    ortho_camera_name: str
    perspective_camera_name: str
    sun_name: str
    world_name: str
    blend_filepath: str


# --- Macro names -------------------------------------------------------------


MACRO_RESET_SCENE: str = "reset_scene"
MACRO_CREATE_TERRAIN: str = "create_terrain_from_heightmap"
MACRO_APPLY_TERRAIN_MATERIAL: str = "apply_terrain_material"
MACRO_CARVE_STREAM: str = "carve_stream"
MACRO_SET_CAMERA_OVERVIEW: str = "set_camera_overview"
MACRO_ADD_BASIC_LIGHTING: str = "add_basic_lighting"
MACRO_RENDER_PREVIEW: str = "render_preview"
MACRO_SAVE_BLEND: str = "save_blend"
MACRO_REALIZE_REGION: str = "realize_region"


# --- Facade ------------------------------------------------------------------


def _to_inputs(payload: object) -> dict[str, JsonValue]:
    """Convert a frozen dataclass payload to the engine's JSON-shaped input."""
    if not is_dataclass(payload) or isinstance(payload, type):
        msg = f"expected a dataclass instance, got {type(payload).__name__}"
        raise TypeError(msg)
    return {f.name: getattr(payload, f.name) for f in fields(payload)}


def reset_scene(engine: RealizerEngine) -> RealizationResult:
    """Run the ``reset_scene`` curated macro."""
    return engine.execute_macro(MACRO_RESET_SCENE, {})


def create_terrain_from_heightmap(
    engine: RealizerEngine,
    inputs: CreateTerrainInputs,
) -> RealizationResult:
    """Run the ``create_terrain_from_heightmap`` curated macro."""
    return engine.execute_macro(MACRO_CREATE_TERRAIN, _to_inputs(inputs))


def apply_terrain_material(
    engine: RealizerEngine,
    inputs: ApplyTerrainMaterialInputs,
) -> RealizationResult:
    """Run the ``apply_terrain_material`` curated macro."""
    return engine.execute_macro(MACRO_APPLY_TERRAIN_MATERIAL, _to_inputs(inputs))


def carve_stream(
    engine: RealizerEngine,
    inputs: CarveStreamInputs,
) -> RealizationResult:
    """Run the ``carve_stream`` curated macro."""
    return engine.execute_macro(MACRO_CARVE_STREAM, _to_inputs(inputs))


def set_camera_overview(
    engine: RealizerEngine,
    inputs: SetCameraOverviewInputs,
) -> RealizationResult:
    """Run the ``set_camera_overview`` curated macro."""
    return engine.execute_macro(MACRO_SET_CAMERA_OVERVIEW, _to_inputs(inputs))


def add_basic_lighting(
    engine: RealizerEngine,
    inputs: AddBasicLightingInputs,
) -> RealizationResult:
    """Run the ``add_basic_lighting`` curated macro."""
    return engine.execute_macro(MACRO_ADD_BASIC_LIGHTING, _to_inputs(inputs))


def render_preview(
    engine: RealizerEngine,
    inputs: RenderPreviewInputs,
) -> RealizationResult:
    """Run the ``render_preview`` curated macro.

    The engine enforces the NF-1.5 200 KB ceiling automatically (via the
    macro's ``expects.png_max_bytes``); on failure the
    :class:`~forge_mcp.realize.engine.RealizerStepError` carries the
    rendered file size in its trace so callers can decide whether to
    re-render at a tighter compression / lower resolution.
    """
    return engine.execute_macro(MACRO_RENDER_PREVIEW, _to_inputs(inputs))


def save_blend(
    engine: RealizerEngine,
    inputs: SaveBlendInputs,
) -> RealizationResult:
    """Run the ``save_blend`` curated macro.

    Atomic-write semantics (write to ``<path>.tmp``, ``os.replace`` to
    the final path) are the host's responsibility — the macro itself
    just calls ``bpy.ops.wm.save_as_mainfile``.
    """
    return engine.execute_macro(MACRO_SAVE_BLEND, _to_inputs(inputs))


def realize_region(
    engine: RealizerEngine,
    inputs: RealizeRegionInputs,
) -> RealizationResult:
    """Run the composite ``realize_region`` macro end-to-end."""
    return engine.execute_macro(MACRO_REALIZE_REGION, _to_inputs(inputs))
