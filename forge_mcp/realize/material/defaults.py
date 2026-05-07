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


_VALIDATORS: Final[dict[MaterialRecipe, Callable[[Mapping[str, JsonValue]], None]]] = {
    MaterialRecipe.PRINCIPLED_HEIGHT_RAMP: _validate_principled_height_ramp,
    MaterialRecipe.TRIPLANAR_ROCK: _validate_triplanar_rock,
    MaterialRecipe.FLAT_COLOR: _validate_flat_color,
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
