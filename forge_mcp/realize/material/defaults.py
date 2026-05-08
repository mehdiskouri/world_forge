"""Built-in default material archetype + per-recipe parameter validators.

The default archetype is the Phase-4-compatibility gate: when a region
has no ``material_application`` edges, the resolver synthesizes this
archetype so the rendered terrain is byte-for-byte identical to the
legacy monolithic ``apply_terrain_material`` macro. Removing this would
break the Phase-4 reference renders (and the integration tests that
diff against them).

Parameter validators are intentionally *advisory*: bad parameters land
on disk as opaque JSON because :class:`MaterialArchetypeNode` keeps the
``parameters`` field loose. The resolver calls these validators before
emitting a plan layer so adapter code can assume the shape it expects.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from forge_mcp.project.schemas import (
    MaterialArchetypeId,
    MaterialArchetypeNode,
    MaterialRecipe,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from forge_mcp._types import JsonValue


DEFAULT_TERRAIN_ARCHETYPE_ID: Final[MaterialArchetypeId] = MaterialArchetypeId(
    "material_forge_terrain_default",
)
"""Synthetic archetype id used when a region has no material applications."""


_DEFAULT_COLOR_RAMP_STOPS: Final[tuple[tuple[float, tuple[float, float, float, float]], ...]] = (
    (0.0, (0.18, 0.34, 0.12, 1.0)),
    (0.5, (0.45, 0.36, 0.27, 1.0)),
    (1.0, (0.95, 0.95, 0.95, 1.0)),
)
_DEFAULT_SLOPE_THRESHOLD: Final[float] = 0.35
_EPOCH: Final[datetime] = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)


def default_terrain_archetype(
    *,
    elevation_min: float,
    elevation_max: float,
) -> MaterialArchetypeNode:
    """Return the synthetic Phase-4-compatible default archetype.

    The returned node never lives in ``state.archetypes``; it exists
    only inside the resolver so projects without explicit material
    applications still produce a renderable plan with stable parameter
    bytes.
    """
    parameters: dict[str, JsonValue] = {
        "color_ramp_stops": [
            {"position": position, "color": list(color)}
            for position, color in _DEFAULT_COLOR_RAMP_STOPS
        ],
        "slope_threshold": float(_DEFAULT_SLOPE_THRESHOLD),
        "elevation_min": float(elevation_min),
        "elevation_max": float(elevation_max),
    }
    return MaterialArchetypeNode(
        node_id=DEFAULT_TERRAIN_ARCHETYPE_ID,
        name="Forge Terrain Default",
        recipe=MaterialRecipe.PRINCIPLED_HEIGHT_RAMP,
        parameters=parameters,
        notes="Synthetic Phase-4-compat default; never persisted.",
        created_at=_EPOCH,
        modified_at=_EPOCH,
    )


# ---------------------------------------------------------------------------
# Recipe parameter validators
# ---------------------------------------------------------------------------


class RecipeParameterError(ValueError):
    """Raised when a parameter dict does not match its recipe's contract."""


def _require_keys(
    recipe: MaterialRecipe,
    parameters: Mapping[str, JsonValue],
    required: tuple[str, ...],
) -> None:
    missing = [k for k in required if k not in parameters]
    if missing:
        msg = f"recipe {recipe.value!r} is missing required parameters: {missing!r}"
        raise RecipeParameterError(msg)


def _validate_principled_height_ramp(parameters: Mapping[str, JsonValue]) -> None:
    _require_keys(
        MaterialRecipe.PRINCIPLED_HEIGHT_RAMP,
        parameters,
        ("color_ramp_stops", "slope_threshold", "elevation_min", "elevation_max"),
    )
    stops = parameters["color_ramp_stops"]
    if not isinstance(stops, list) or not stops:
        msg = "color_ramp_stops must be a non-empty list"
        raise RecipeParameterError(msg)
    for stop in stops:
        if not isinstance(stop, dict) or "position" not in stop or "color" not in stop:
            msg = f"color ramp stop must have 'position' and 'color' keys, got {stop!r}"
            raise RecipeParameterError(msg)


def _validate_triplanar_rock(parameters: Mapping[str, JsonValue]) -> None:
    _require_keys(MaterialRecipe.TRIPLANAR_ROCK, parameters, ("base_color",))
    base_color = parameters["base_color"]
    expected_rgba = 4
    if not isinstance(base_color, list) or len(base_color) != expected_rgba:
        msg = f"base_color must be an RGBA list of 4 floats, got {base_color!r}"
        raise RecipeParameterError(msg)


def _validate_flat_color(parameters: Mapping[str, JsonValue]) -> None:
    _require_keys(MaterialRecipe.FLAT_COLOR, parameters, ("color",))
    color = parameters["color"]
    expected_rgba = 4
    if not isinstance(color, list) or len(color) != expected_rgba:
        msg = f"color must be an RGBA list of 4 floats, got {color!r}"
        raise RecipeParameterError(msg)


def _validate_unit_interval(name: str, value: JsonValue) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        msg = f"{name} must be a number in [0, 1], got {value!r}"
        raise RecipeParameterError(msg)
    if not 0.0 <= float(value) <= 1.0:
        msg = f"{name} must be in [0, 1], got {value!r}"
        raise RecipeParameterError(msg)


def _validate_pbr_layered(parameters: Mapping[str, JsonValue]) -> None:
    """Validate the Phase 6-e Stage B ``pbr_layered`` recipe.

    Required: ``base_color`` (RGBA list of 4 floats).

    Optional knobs (all default to ``0`` / off if absent):
    ``base_color_variation`` (Voronoi mix amount, ``[0, 1]``),
    ``roughness`` (base roughness, ``[0, 1]``),
    ``roughness_variation`` (Noise contribution, ``[0, 1]``),
    ``normal_detail`` (Bump strength, ``[0, 1]``),
    ``metallic`` (``[0, 1]``),
    ``clearcoat`` (``[0, 1]``),
    ``triplanar_scale_m`` (positive float, world-space scale for
    procedural inputs).
    """
    _require_keys(MaterialRecipe.PBR_LAYERED, parameters, ("base_color",))
    base_color = parameters["base_color"]
    expected_rgba = 4
    if not isinstance(base_color, list) or len(base_color) != expected_rgba:
        msg = f"base_color must be an RGBA list of 4 floats, got {base_color!r}"
        raise RecipeParameterError(msg)
    for unit_key in (
        "base_color_variation",
        "roughness",
        "roughness_variation",
        "normal_detail",
        "metallic",
        "clearcoat",
    ):
        if unit_key in parameters:
            _validate_unit_interval(unit_key, parameters[unit_key])
    if "triplanar_scale_m" in parameters:
        scale = parameters["triplanar_scale_m"]
        if not isinstance(scale, (int, float)) or isinstance(scale, bool) or float(scale) <= 0.0:
            msg = f"triplanar_scale_m must be a positive number, got {scale!r}"
            raise RecipeParameterError(msg)


def _validate_positive_number(name: str, value: JsonValue) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or float(value) <= 0.0:
        msg = f"{name} must be a positive number, got {value!r}"
        raise RecipeParameterError(msg)


def _validate_optional_rgba(parameters: Mapping[str, JsonValue]) -> None:
    if "base_color" not in parameters:
        return
    base_color = parameters["base_color"]
    expected_rgba = 4
    if not isinstance(base_color, list) or len(base_color) != expected_rgba:
        msg = f"base_color must be an RGBA list of 4 floats, got {base_color!r}"
        raise RecipeParameterError(msg)


def _validate_procedural_snow(parameters: Mapping[str, JsonValue]) -> None:
    """Phase 6-e Stage C/E: powdery snow surface, optional volume scatter.

    All knobs optional; ``{}`` renders plausible snow.
    Optional surface knobs: ``base_color`` (RGBA), ``sparkle_density``
    ``[0, 1]``, ``sparkle_scale_m`` (positive), ``drift_strength``
    ``[0, 1]``, ``drift_scale_m`` (positive), ``subsurface_weight``
    ``[0, 1]``. Optional Stage E volume knob:
    ``volume_scatter_density`` (positive) opts the layer into a
    ``ShaderNodeVolumeScatter`` linked to the parallel composite
    Volume socket.
    """
    _validate_optional_rgba(parameters)
    for unit_key in ("sparkle_density", "drift_strength", "subsurface_weight"):
        if unit_key in parameters:
            _validate_unit_interval(unit_key, parameters[unit_key])
    for positive_key in ("sparkle_scale_m", "drift_scale_m", "volume_scatter_density"):
        if positive_key in parameters:
            _validate_positive_number(positive_key, parameters[positive_key])


def _validate_wet_band(band: JsonValue) -> None:
    if not isinstance(band, dict):
        msg = f"wet_band must be a dict, got {band!r}"
        raise RecipeParameterError(msg)
    for key in ("low_m", "high_m", "darken"):
        if key not in band:
            msg = f"wet_band missing required key {key!r}"
            raise RecipeParameterError(msg)
    low_m = band["low_m"]
    high_m = band["high_m"]
    if not isinstance(low_m, (int, float)) or not isinstance(high_m, (int, float)):
        msg = f"wet_band low_m and high_m must be numbers, got {band!r}"
        raise RecipeParameterError(msg)
    if float(low_m) >= float(high_m):
        msg = f"wet_band requires low_m < high_m, got [{low_m}, {high_m}]"
        raise RecipeParameterError(msg)
    _validate_unit_interval("wet_band.darken", band["darken"])


def _validate_procedural_sand(parameters: Mapping[str, JsonValue]) -> None:
    """Phase 6-e Stage C: warm-tan sand surface recipe.

    All knobs optional. Optional: ``base_color`` (RGBA),
    ``grain_amount`` ``[0, 1]``, ``grain_scale_m`` (positive),
    ``ripple_strength`` ``[0, 1]``, ``ripple_scale_m`` (positive),
    ``wet_band`` (``{"low_m": float, "high_m": float, "darken": [0, 1]}``
    — when present, darkens base color along the low-Z region).
    """
    _validate_optional_rgba(parameters)
    for unit_key in ("grain_amount", "ripple_strength"):
        if unit_key in parameters:
            _validate_unit_interval(unit_key, parameters[unit_key])
    for positive_key in ("grain_scale_m", "ripple_scale_m"):
        if positive_key in parameters:
            _validate_positive_number(positive_key, parameters[positive_key])
    if "wet_band" in parameters:
        _validate_wet_band(parameters["wet_band"])


def _validate_procedural_water(parameters: Mapping[str, JsonValue]) -> None:
    """Phase 6-e Stage C/E: transmission-dominant water, optional absorption.

    All knobs optional with sensible defaults for calm water. Optional
    surface knobs: ``base_color`` (RGBA tint), ``ior`` (positive,
    default 1.33), ``roughness`` ``[0, 1]`` (default 0.0 for
    glass-flat), ``wave_strength`` ``[0, 1]``, ``wave_scale_m``
    (positive), ``transmission`` ``[0, 1]`` (default 1.0). Optional
    Stage E volume knobs: ``volume_absorption_density`` (positive)
    opts the layer into a ``ShaderNodeVolumeAbsorption`` linked to
    the parallel composite Volume socket;
    ``volume_absorption_color`` (RGBA, defaults to ``base_color``)
    tints the absorbed light.
    """
    _validate_optional_rgba(parameters)
    if "volume_absorption_color" in parameters:
        absorb = parameters["volume_absorption_color"]
        expected_rgba = 4
        if not isinstance(absorb, list) or len(absorb) != expected_rgba:
            msg = f"volume_absorption_color must be an RGBA list of 4 floats, got {absorb!r}"
            raise RecipeParameterError(msg)
    for unit_key in ("roughness", "wave_strength", "transmission"):
        if unit_key in parameters:
            _validate_unit_interval(unit_key, parameters[unit_key])
    for positive_key in ("ior", "wave_scale_m", "volume_absorption_density"):
        if positive_key in parameters:
            _validate_positive_number(positive_key, parameters[positive_key])


def _validate_blade_color(parameters: Mapping[str, JsonValue]) -> None:
    if "blade_color" not in parameters:
        return
    color = parameters["blade_color"]
    expected_rgba = 4
    if not isinstance(color, list) or len(color) != expected_rgba:
        msg = f"blade_color must be an RGBA list of 4 floats, got {color!r}"
        raise RecipeParameterError(msg)


def _validate_height_band(parameters: Mapping[str, JsonValue]) -> None:
    if "height_band" not in parameters:
        return
    band = parameters["height_band"]
    if not isinstance(band, dict):
        msg = f"height_band must be a dict, got {band!r}"
        raise RecipeParameterError(msg)
    for key in ("z_low", "z_high"):
        if key not in band:
            msg = f"height_band missing required key {key!r}"
            raise RecipeParameterError(msg)
        value = band[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            msg = f"height_band.{key} must be numeric, got {value!r}"
            raise RecipeParameterError(msg)
    z_low = float(band["z_low"])
    z_high = float(band["z_high"])
    if z_low >= z_high:
        msg = f"height_band.z_low must be < z_high, got {z_low!r} >= {z_high!r}"
        raise RecipeParameterError(msg)


def _validate_procedural_grass(parameters: Mapping[str, JsonValue]) -> None:
    """Phase 6-e Stage D: geometry-nodes instanced grass blades.

    Routed through ``material.attach_instancer`` rather than the
    surface composite. All knobs optional; ``{}`` renders the v1
    default green meadow at 200 blades/m².

    Optional parameters:
    - ``density_per_m2`` (positive) — Distribute Points on Faces density.
    - ``blade_height_m`` (positive) — blade triangle height.
    - ``blade_color`` (RGBA) — base color of the per-blade material.
    - ``slope_max_cos`` ``[0, 1]`` — only distribute where
      ``dot(normal, +Z) >= slope_max_cos``; 1.0 means flat ground only.
    - ``height_band`` ``{z_low, z_high}`` — optional altitude clamp.
    - ``rotation_jitter_deg`` (positive) — per-instance Z rotation jitter.
    - ``scale_jitter`` ``[0, 1]`` — per-instance scale jitter.
    - ``translucency`` ``[0, 1]`` — translucent BSDF mix weight.
    - ``seed`` (int) — Distribute Points seed.
    """
    _validate_blade_color(parameters)
    _validate_height_band(parameters)
    for unit_key in ("slope_max_cos", "scale_jitter", "translucency"):
        if unit_key in parameters:
            _validate_unit_interval(unit_key, parameters[unit_key])
    for positive_key in (
        "density_per_m2",
        "blade_height_m",
        "rotation_jitter_deg",
    ):
        if positive_key in parameters:
            _validate_positive_number(positive_key, parameters[positive_key])
    if "seed" in parameters:
        seed = parameters["seed"]
        if not isinstance(seed, int) or isinstance(seed, bool):
            msg = f"seed must be an integer, got {seed!r}"
            raise RecipeParameterError(msg)


_VALIDATORS: Final[dict[MaterialRecipe, Callable[[Mapping[str, JsonValue]], None]]] = {
    MaterialRecipe.PRINCIPLED_HEIGHT_RAMP: _validate_principled_height_ramp,
    MaterialRecipe.TRIPLANAR_ROCK: _validate_triplanar_rock,
    MaterialRecipe.FLAT_COLOR: _validate_flat_color,
    MaterialRecipe.PBR_LAYERED: _validate_pbr_layered,
    MaterialRecipe.PROCEDURAL_SNOW: _validate_procedural_snow,
    MaterialRecipe.PROCEDURAL_SAND: _validate_procedural_sand,
    MaterialRecipe.PROCEDURAL_WATER: _validate_procedural_water,
    MaterialRecipe.PROCEDURAL_GRASS: _validate_procedural_grass,
}


def validate_recipe_parameters(
    recipe: MaterialRecipe,
    parameters: Mapping[str, JsonValue],
) -> None:
    """Raise :class:`RecipeParameterError` if ``parameters`` violates ``recipe``."""
    validator = _VALIDATORS[recipe]
    validator(parameters)


if TYPE_CHECKING:
    from collections.abc import Callable


__all__ = [
    "DEFAULT_TERRAIN_ARCHETYPE_ID",
    "RecipeParameterError",
    "default_terrain_archetype",
    "validate_recipe_parameters",
]
