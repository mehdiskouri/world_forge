"""Default environment + per-recipe parameter validators (Phase 6-f Stage C).

Mirrors :mod:`forge_mcp.realize.material.defaults`: a synthetic
fallback :class:`EnvironmentNode` keeps renders working when no scope
binds an environment, and a frozen ``_VALIDATORS`` dict guarantees every
:class:`~forge_mcp.project.schemas.EnvironmentRecipe` enum value has a
matching validator (regression guard for forward compat).

Most recipe-specific constraints already live on
:class:`EnvironmentParameters` (Pydantic ranges + RGBA validators), so
the per-recipe validators here are intentionally light: they exist to
(a) prove exhaustive coverage of the enum and (b) flag recipe-specific
nonsense (e.g. ``night`` with daylight-grade ``sun_intensity_w_m2``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from forge_mcp.project.schemas import (
    EnvironmentNode,
    EnvironmentNodeId,
    EnvironmentParameters,
    EnvironmentRecipe,
    Season,
)

if TYPE_CHECKING:
    from collections.abc import Callable


DEFAULT_ENVIRONMENT_ID: Final[EnvironmentNodeId] = EnvironmentNodeId(
    "env_forge_default",
)
"""Synthetic environment id used when no scope binds an environment."""

_DEFAULT_DATETIME: Final[datetime] = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)
_EPOCH: Final[datetime] = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)


def default_environment_node() -> EnvironmentNode:
    """Return the synthetic Phase-6-f-compat default environment.

    Equator, summer solstice solar noon UTC, ``CLEAR`` recipe with
    schema defaults. The returned node never lives in
    ``state.environments``; it is only used by the resolver as the
    bottom of the binding fallback chain.
    """
    return EnvironmentNode(
        node_id=DEFAULT_ENVIRONMENT_ID,
        name="Forge Environment Default",
        recipe=EnvironmentRecipe.CLEAR,
        parameters=EnvironmentParameters(
            datetime_utc=_DEFAULT_DATETIME,
            latitude_deg=0.0,
            longitude_deg=0.0,
            season=Season.SUMMER,
        ),
        notes="Synthetic Phase-6-f-compat default; never persisted.",
        created_at=_EPOCH,
        modified_at=_EPOCH,
    )


# ---------------------------------------------------------------------------
# Recipe parameter validators
# ---------------------------------------------------------------------------


class EnvironmentParameterError(ValueError):
    """Raised when environment parameters violate recipe-specific contracts."""


_NIGHT_SUN_INTENSITY_MAX: Final[float] = 50.0
"""Cap for ``night`` recipe sun intensity (W/m^2). Anything brighter is
not "night" in any sensible reading; surface as a recipe-validator
error rather than letting an absurd renderer state slip through."""

_SUNSET_ELEVATION_BAND_MAX_DEG: Final[float] = 30.0
"""Advisory: ``sunset`` is most plausible when the sun is below 30 deg
elevation. We do not enforce this on the parameters because the sun
elevation is *derived* (from lat/lon/datetime), not a parameter. This
constant is kept to document the intent for adapter-side warnings."""


def _validate_clear(parameters: EnvironmentParameters) -> None:
    """Daytime clear sky: all knobs honored, no recipe-specific extra constraints."""
    _ = parameters  # all checks already encoded on EnvironmentParameters fields


def _validate_overcast(parameters: EnvironmentParameters) -> None:
    """Diffuse cloud cover: all knobs honored. No extra constraints in v1."""
    _ = parameters


def _validate_sunset(parameters: EnvironmentParameters) -> None:
    """Low-angle warm sun: all knobs honored. No extra constraints in v1."""
    _ = parameters


def _validate_night(parameters: EnvironmentParameters) -> None:
    """Moonlit dim: cap ``sun_intensity_w_m2`` so the recipe stays plausible."""
    if parameters.sun_intensity_w_m2 > _NIGHT_SUN_INTENSITY_MAX:
        msg = (
            f"recipe 'night' requires sun_intensity_w_m2 <= "
            f"{_NIGHT_SUN_INTENSITY_MAX}; got {parameters.sun_intensity_w_m2}"
        )
        raise EnvironmentParameterError(msg)


def _validate_procedural_sky(parameters: EnvironmentParameters) -> None:
    """Cycles Nishita: sky_zenith / sky_horizon are advisory but kept on the model."""
    _ = parameters


_VALIDATORS: Final[dict[EnvironmentRecipe, Callable[[EnvironmentParameters], None]]] = {
    EnvironmentRecipe.CLEAR: _validate_clear,
    EnvironmentRecipe.OVERCAST: _validate_overcast,
    EnvironmentRecipe.SUNSET: _validate_sunset,
    EnvironmentRecipe.NIGHT: _validate_night,
    EnvironmentRecipe.PROCEDURAL_SKY: _validate_procedural_sky,
}


def validate_environment_parameters(
    recipe: EnvironmentRecipe,
    parameters: EnvironmentParameters,
) -> None:
    """Raise :class:`EnvironmentParameterError` when parameters violate ``recipe``.

    The :data:`_VALIDATORS` dict is exhaustive over
    :class:`EnvironmentRecipe` (enforced by a unit test), so a missing
    enum entry surfaces as a ``KeyError`` only inside development, not
    in production calls.
    """
    validator = _VALIDATORS[recipe]
    validator(parameters)


__all__ = [
    "DEFAULT_ENVIRONMENT_ID",
    "EnvironmentParameterError",
    "default_environment_node",
    "validate_environment_parameters",
]
