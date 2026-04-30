"""Pydantic models for the v1 structured descriptor schema.

Mirrors ``AGENT/ARCHITECTURE.md`` §3.3 verbatim. The schema is versioned
via :data:`SCHEMA_VERSION`; bumping is breaking unless additive.

The exported JSON Schema (see :func:`descriptor_json_schema`) is what the
``forge.plan`` skill embeds and what the ``get_descriptor_schema`` MCP
tool returns. The committed ``schema.json`` artifact is regenerated from
these models and verified in CI for drift.
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict, field_validator

# Pydantic ``BaseModel`` and ``field_validator`` stubs leak ``Any`` through
# their descriptor / classmethod machinery, tripping mypy's
# ``disallow_any_explicit``. We acknowledge the leak with scoped
# ``# type: ignore[explicit-any]`` markers carrying this reason. See
# .github/instructions.md §2 — type: ignore is allowed with a rule code.

SCHEMA_VERSION: Final[str] = "1.0"
"""Semantic version of the descriptor schema. Embedded in ``project.json``."""


class TerrainPrimary(StrEnum):
    """Primary terrain archetype enumerated by the v1 design space.

    Each value maps to a profile in the Phase 3 ``TERRAIN_PROFILES``
    lookup. Adding values is a minor schema bump; removing or renaming is
    breaking.
    """

    ALPINE_VALLEY = "alpine_valley"
    ALPINE_PEAKS = "alpine_peaks"
    ROLLING_HILLS = "rolling_hills"
    PLAINS = "plains"
    DESERT_MESA = "desert_mesa"
    DESERT_DUNES = "desert_dunes"
    BOREAL_LOWLAND = "boreal_lowland"
    MARSH = "marsh"
    VOLCANIC_CONE = "volcanic_cone"
    COASTAL_CLIFFS = "coastal_cliffs"
    RIVER_VALLEY = "river_valley"
    CANYON = "canyon"


class StreamCharacter(StrEnum):
    """Hydrological character of a region's primary stream, if any."""

    ALPINE_CREEK = "alpine_creek"
    MEANDERING_RIVER = "meandering_river"
    DRY_WASH = "dry_wash"
    NONE = "none"


class Terrain(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Terrain sub-descriptor.

    Only :attr:`primary` is required. Optional fields modulate the
    deterministic Phase 3 mapping; absent fields fall back to per-profile
    defaults.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    primary: TerrainPrimary
    elevation_band: tuple[float, float] | None = None
    """``[low_meters, high_meters]``; cross-field check enforces ``low <= high``."""
    ruggedness: float | None = None
    notes: str | None = None

    _RUGGEDNESS_MIN: ClassVar[float] = 0.0
    _RUGGEDNESS_MAX: ClassVar[float] = 1.0
    _NOTES_MAX_LEN: ClassVar[int] = 200

    @field_validator("ruggedness")
    @classmethod
    def _check_ruggedness(cls, value: float | None) -> float | None:
        if value is not None and not (cls._RUGGEDNESS_MIN <= value <= cls._RUGGEDNESS_MAX):
            msg = (
                f"ruggedness must be within "
                f"[{cls._RUGGEDNESS_MIN}, {cls._RUGGEDNESS_MAX}], got {value}"
            )
            raise ValueError(msg)
        return value

    @field_validator("notes")
    @classmethod
    def _check_notes_length(cls, value: str | None) -> str | None:
        if value is not None and len(value) > cls._NOTES_MAX_LEN:
            msg = f"notes must be <= {cls._NOTES_MAX_LEN} characters"
            raise ValueError(msg)
        return value


class Hydrology(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Hydrology sub-descriptor.

    A region with ``has_stream=True`` must declare ``stream_character`` to
    something other than ``NONE``; this is checked in
    :func:`forge_mcp.descriptor.validate.validate`.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    has_stream: bool | None = None
    stream_character: StreamCharacter | None = None


class StructuredDescriptor(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Top-level structured descriptor handed by the agent to Forge.

    Frozen and ``extra='forbid'``: the schema is the contract, agents
    cannot smuggle extra fields, and Forge can hash descriptors safely.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    terrain: Terrain
    hydrology: Hydrology | None = None


def descriptor_json_schema() -> dict[str, object]:
    r"""Return the JSON Schema (draft 2020-12) for :class:`StructuredDescriptor`.

    The committed ``forge_mcp/descriptor/schema.json`` artifact is
    expected to be byte-equal to ``json.dumps(descriptor_json_schema(),
    indent=2, sort_keys=True) + "\\n"``. CI fails on drift.
    """
    schema = StructuredDescriptor.model_json_schema()
    schema["title"] = "ForgeStructuredDescriptor"
    schema["x-schema-version"] = SCHEMA_VERSION
    return schema
