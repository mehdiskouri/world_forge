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

from dataclasses import dataclass, fields, is_dataclass, replace
from typing import TYPE_CHECKING, Final

from forge_mcp.realize.engine import REASON_PNG_OVERSIZE, RealizerStepError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from forge_mcp.realize.engine import RealizationResult, RealizerEngine
    from forge_mcp.realize.rpc import JsonValue


# --- Constants ---------------------------------------------------------------


DEFAULT_PNG_COMPRESSION: Final[int] = 15
RETRY_PNG_COMPRESSION: Final[int] = 100
DEFAULT_PNG_MAX_BYTES: Final[int] = 280_000


# --- Macro input models ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreateTerrainInputs:
    """Inputs for the ``create_terrain_from_heightmap`` macro."""

    object_name: str
    vertices: Sequence[Sequence[float]]
    faces: Sequence[Sequence[int]]
    region_id: str
    spec_id: str
    heightmap_image_filepath: str
    displace_strength: float
    displace_midlevel: float
    displace_modifier_name: str
    displace_texture_name: str


@dataclass(frozen=True, slots=True)
class ApplyTerrainMaterialInputs:
    """Inputs for the ``apply_terrain_material`` macro.

    Carries the *resolved* :class:`CompositeMaterialPlan` produced by
    :func:`forge_mcp.realize.material.resolve_plan`. The plan_id is
    used as the canonical material name so two regions resolving to
    the same plan share a single Blender ``Material`` data-block.
    """

    object_name: str
    plan: JsonValue
    plan_id: str
    instancer_layers: JsonValue


@dataclass(frozen=True, slots=True)
class CarveStreamInputs:
    """Inputs for the ``carve_stream`` macro."""

    curve_name: str
    region_id: str


@dataclass(frozen=True, slots=True)
class SetCameraOverviewInputs:
    """Inputs for the ``set_camera_overview`` macro.

    The location / rotation / ortho_scale fields are mandatory in v1
    because Blender's default camera (location=(0,0,0), rotation=(0,0,0),
    type=PERSP) sits underground for any non-trivial terrain and would
    render solid grey. The host computes them from the heightmap's
    world extent and elevation band.
    """

    ortho_camera_name: str
    perspective_camera_name: str
    ortho_location: Sequence[float]
    ortho_rotation_euler: Sequence[float]
    ortho_scale: float
    perspective_location: Sequence[float]
    perspective_rotation_euler: Sequence[float]


@dataclass(frozen=True, slots=True)
class AddBasicLightingInputs:
    """Inputs for the ``add_basic_lighting`` macro.

    The sun's rotation must be set explicitly: a sun lamp at Euler
    (0, 0, 0) points straight down, which kills every cast shadow and
    flattens the terrain in the render.
    """

    sun_name: str
    world_name: str
    sun_location: Sequence[float]
    sun_rotation_euler: Sequence[float]


@dataclass(frozen=True, slots=True)
class ApplyEnvironmentInputs:
    """Inputs for the Phase 6-f Stage D ``apply_environment`` macro.

    Carries the *resolved* :class:`ResolvedEnvironment` payload and
    its content-addressed ``plan_id``. The adapter side keys both the
    world data-block and SUN lamp on ``plan_id`` so two scopes that
    resolve to the same effective environment share a single
    ``forge.world.<plan_id>`` + ``forge.sun.<plan_id>`` pair.
    """

    plan: JsonValue
    plan_id: str


@dataclass(frozen=True, slots=True)
class RenderPreviewInputs:
    """Inputs for the ``render_preview`` macro."""

    filepath: str
    resolution_x: int
    resolution_y: int
    camera_name: str
    engine: str
    device_type: str
    cycles_samples: int
    compression: int = DEFAULT_PNG_COMPRESSION
    png_max_bytes: int = DEFAULT_PNG_MAX_BYTES


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
    plan: JsonValue
    plan_id: str
    instancer_layers: JsonValue
    curve_name: str
    ortho_camera_name: str
    perspective_camera_name: str
    ortho_location: Sequence[float]
    ortho_rotation_euler: Sequence[float]
    ortho_scale: float
    perspective_location: Sequence[float]
    perspective_rotation_euler: Sequence[float]
    sun_name: str
    world_name: str
    sun_location: Sequence[float]
    sun_rotation_euler: Sequence[float]
    blend_filepath: str
    heightmap_image_filepath: str
    displace_strength: float
    displace_midlevel: float = 0.5
    displace_modifier_name: str = "forge_displace"
    displace_texture_name: str = "forge_heightmap"


# --- Macro names -------------------------------------------------------------


MACRO_RESET_SCENE: str = "reset_scene"
MACRO_CREATE_TERRAIN: str = "create_terrain_from_heightmap"
MACRO_APPLY_TERRAIN_MATERIAL: str = "apply_terrain_material"
MACRO_CARVE_STREAM: str = "carve_stream"
MACRO_SET_CAMERA_OVERVIEW: str = "set_camera_overview"
MACRO_ADD_BASIC_LIGHTING: str = "add_basic_lighting"
MACRO_APPLY_ENVIRONMENT: str = "apply_environment"
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


def apply_environment(
    engine: RealizerEngine,
    inputs: ApplyEnvironmentInputs,
) -> RealizationResult:
    """Run the Phase 6-f Stage D ``apply_environment`` curated macro.

    Single-step wrapper around the ``world.build_environment`` RPC that
    builds (or reuses) the ``forge.world.<plan_id>`` shader graph plus
    the ``forge.sun.<plan_id>`` SUN lamp from a content-addressed
    :class:`~forge_mcp.realize.environment.plan.ResolvedEnvironment`.
    """
    return engine.execute_macro(MACRO_APPLY_ENVIRONMENT, _to_inputs(inputs))


def render_preview(
    engine: RealizerEngine,
    inputs: RenderPreviewInputs,
) -> RealizationResult:
    """Run the ``render_preview`` curated macro.

    The engine enforces the NF-1.5 200 KB ceiling automatically (via the
    macro's ``expects.png_max_bytes``); on the first overflow this
    facade transparently re-runs the macro once with PNG ``compression``
    bumped from :data:`DEFAULT_PNG_COMPRESSION` (15) to
    :data:`RETRY_PNG_COMPRESSION` (100) before re-raising. Any second
    failure (or a non-budget step error) propagates unchanged so the
    trace reaches the caller.
    """
    try:
        return engine.execute_macro(MACRO_RENDER_PREVIEW, _to_inputs(inputs))
    except RealizerStepError as exc:
        if exc.reason_code != REASON_PNG_OVERSIZE:
            raise
        if inputs.compression >= RETRY_PNG_COMPRESSION:
            raise
        retry_inputs = replace(inputs, compression=RETRY_PNG_COMPRESSION)
        return engine.execute_macro(MACRO_RENDER_PREVIEW, _to_inputs(retry_inputs))


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
