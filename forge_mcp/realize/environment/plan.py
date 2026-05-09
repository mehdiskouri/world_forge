"""Resolved environment plan IR + content-addressed plan id (Phase 6-f Stage C).

A :class:`ResolvedEnvironment` is the flat, recipe-agnostic payload the
Blender adapter consumes to build (or reuse) a ``forge.world.<plan_id>``
shader graph + ``forge.sun.<plan_id>`` lamp. The ``plan_id`` is a
content-addressed digest so two scopes with byte-equal resolved fields
share a single Blender world.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - runtime needed by Pydantic
from hashlib import blake2b
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from forge_mcp._io.atomic import dump_json
from forge_mcp.project.schemas import EnvironmentPlanId, EnvironmentRecipe, Season


class ResolvedEnvironment(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Flat resolved environment payload (Phase 6-f Stage C).

    All recipe-agnostic knobs from
    :class:`~forge_mcp.project.schemas.EnvironmentParameters` plus the
    derived solar position (from
    :func:`forge_mcp.environment.sun.compute_sun_direction`) and a
    diagnostic ``scope_label`` describing where the resolver sourced the
    binding (``"region:<id>"``, ``"world_root"``, or ``"default"``).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    plan_id: EnvironmentPlanId
    recipe: EnvironmentRecipe
    sun_color: tuple[float, float, float, float]
    sun_intensity_w_m2: float = Field(ge=0.0)
    sun_azimuth_deg: float
    sun_elevation_deg: float
    sun_vector: tuple[float, float, float]
    sky_zenith_color: tuple[float, float, float, float]
    sky_horizon_color: tuple[float, float, float, float]
    ambient_color: tuple[float, float, float, float]
    ambient_strength: float = Field(ge=0.0)
    fog_color: tuple[float, float, float, float]
    fog_density: float = Field(ge=0.0)
    fog_height_falloff_m: float = Field(gt=0.0)
    season: Season
    datetime_utc: datetime
    latitude_deg: float
    longitude_deg: float
    scope_label: str
    source_environment_id: str | None = None


_HASH_KEYS: tuple[str, ...] = (
    "recipe",
    "sun_color",
    "sun_intensity_w_m2",
    "sun_azimuth_deg",
    "sun_elevation_deg",
    "sun_vector",
    "sky_zenith_color",
    "sky_horizon_color",
    "ambient_color",
    "ambient_strength",
    "fog_color",
    "fog_density",
    "fog_height_falloff_m",
    "season",
    "datetime_utc",
    "latitude_deg",
    "longitude_deg",
)


def compute_environment_plan_id(payload: dict[str, object]) -> EnvironmentPlanId:
    """Return the ``eplan_<10-hex>`` content address for ``payload``.

    Only the keys in :data:`_HASH_KEYS` participate in the digest; the
    diagnostic ``scope_label`` and ``source_environment_id`` are
    deliberately excluded so two scopes with the same effective
    environment hash to the same id (and therefore reuse a single
    Blender world).
    """
    body = {key: payload[key] for key in _HASH_KEYS if key in payload}
    digest = blake2b(dump_json(body).encode("utf-8"), digest_size=10).hexdigest()
    return EnvironmentPlanId(f"eplan_{digest}")


__all__ = [
    "ResolvedEnvironment",
    "compute_environment_plan_id",
]
