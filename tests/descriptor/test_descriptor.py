"""Spike 4 — descriptor schema validation, drift check, and eval-set."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from forge_mcp.descriptor import (
    SCHEMA_VERSION,
    Hydrology,
    StreamCharacter,
    StructuredDescriptor,
    Terrain,
    TerrainPrimary,
    ValidationFailure,
    descriptor_json_schema,
    validate,
)
from pydantic import ValidationError

from tests.descriptor.eval_descriptors import EVAL_PAIRS, EvalPair

if TYPE_CHECKING:
    from forge_mcp.descriptor.validate import JsonValue

SCHEMA_JSON_PATH = Path(__file__).resolve().parents[2] / "forge_mcp" / "descriptor" / "schema.json"

EXPECTED_EVAL_PAIRS = 10
MIN_DISTINCT_PRIMARIES = 9


# --------------------------------------------------------------------------- #
# Eval set: every hand-extracted pair must validate cleanly.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("pair", EVAL_PAIRS, ids=[p.prompt for p in EVAL_PAIRS])
def test_eval_pair_validates(pair: EvalPair) -> None:
    result = validate(cast("JsonValue", pair.expected))
    assert isinstance(result, StructuredDescriptor), result


def test_eval_set_covers_design_space() -> None:
    primaries: set[object] = set()
    for pair in EVAL_PAIRS:
        terrain = pair.expected["terrain"]
        assert isinstance(terrain, dict)
        primaries.add(terrain["primary"])
    assert len(EVAL_PAIRS) == EXPECTED_EVAL_PAIRS
    assert len(primaries) >= MIN_DISTINCT_PRIMARIES


# --------------------------------------------------------------------------- #
# Round-trip and frozen behavior.
# --------------------------------------------------------------------------- #
def test_round_trip_through_dict() -> None:
    original = StructuredDescriptor(
        terrain=Terrain(
            primary=TerrainPrimary.ALPINE_VALLEY,
            elevation_band=(1800.0, 2900.0),
            ruggedness=0.8,
        ),
        hydrology=Hydrology(has_stream=True, stream_character=StreamCharacter.ALPINE_CREEK),
    )
    payload = original.model_dump()
    rebuilt = validate(payload)
    assert isinstance(rebuilt, StructuredDescriptor)
    assert rebuilt == original


def test_models_are_frozen() -> None:
    desc = StructuredDescriptor(terrain=Terrain(primary=TerrainPrimary.PLAINS))
    with pytest.raises(ValidationError):
        desc.terrain.primary = TerrainPrimary.MARSH


# --------------------------------------------------------------------------- #
# Rejection cases produce structured ValidationFailure.
# --------------------------------------------------------------------------- #
def test_rejects_unknown_terrain_primary() -> None:
    result = validate({"terrain": {"primary": "ocean_floor"}})
    assert isinstance(result, ValidationFailure)
    assert any("primary" in i.path for i in result.issues)


def test_rejects_out_of_range_ruggedness() -> None:
    result = validate({"terrain": {"primary": "plains", "ruggedness": 1.5}})
    assert isinstance(result, ValidationFailure)
    assert any("ruggedness" in i.path for i in result.issues)


def test_rejects_extra_fields() -> None:
    result = validate({"terrain": {"primary": "plains", "secret_field": True}})
    assert isinstance(result, ValidationFailure)
    assert any("secret_field" in i.path or "extra" in i.code for i in result.issues)


def test_rejects_inverted_elevation_band() -> None:
    result = validate(
        {"terrain": {"primary": "plains", "elevation_band": [500.0, 100.0]}},
    )
    assert isinstance(result, ValidationFailure)
    assert any(i.code == "terrain.elevation_band.inverted" for i in result.issues)


def test_rejects_has_stream_without_character() -> None:
    result = validate(
        {
            "terrain": {"primary": "plains"},
            "hydrology": {"has_stream": True, "stream_character": "none"},
        },
    )
    assert isinstance(result, ValidationFailure)
    assert any(i.code == "hydrology.stream_required" for i in result.issues)


def test_rejects_non_object_payload() -> None:
    result = validate("not a dict")
    assert isinstance(result, ValidationFailure)
    assert result.issues[0].code == "payload.not_object"


def test_rejects_malformed_elevation_band_length() -> None:
    result = validate({"terrain": {"primary": "plains", "elevation_band": [1.0, 2.0, 3.0]}})
    assert isinstance(result, ValidationFailure)
    assert any("elevation_band" in i.path for i in result.issues)


# --------------------------------------------------------------------------- #
# JSON Schema artifact drift check.
# --------------------------------------------------------------------------- #
def test_schema_version_is_published() -> None:
    schema = descriptor_json_schema()
    assert schema["x-schema-version"] == SCHEMA_VERSION


def test_committed_schema_json_matches_models() -> None:
    """If this fails, regenerate forge_mcp/descriptor/schema.json.

    Run::

        uv run python -c "import json; from forge_mcp.descriptor.schema import \\
            descriptor_json_schema; \\
            open('forge_mcp/descriptor/schema.json','w').write( \\
            json.dumps(descriptor_json_schema(), indent=2, sort_keys=True)+'\\n')"
    """
    expected = json.dumps(descriptor_json_schema(), indent=2, sort_keys=True) + "\n"
    actual = SCHEMA_JSON_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "schema.json drifted from Pydantic model — regenerate via the snippet "
        "in this test's docstring."
    )
