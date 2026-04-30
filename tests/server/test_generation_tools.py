"""End-to-end tests for the Phase-3 generation MCP tool surface."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from forge_mcp.descriptor.schema import (
    Hydrology,
    StreamCharacter,
    StructuredDescriptor,
    Terrain,
    TerrainPrimary,
)
from forge_mcp.generate import terrain as terrain_generator
from forge_mcp.project.service import ProjectService
from forge_mcp.server.tools import set_service
from forge_mcp.server.tools.generation import (
    analyze_region,
    generate_region,
    inspect_spec,
    reroll_seed,
)
from forge_mcp.server.tools.history import history as history_tool
from forge_mcp.server.tools.projects import create_project
from forge_mcp.server.tools.regions import create_region
from freezegun import freeze_time

if TYPE_CHECKING:
    from pathlib import Path


_FROZEN = "2024-01-01T12:00:00+00:00"
_BOUNDS: dict[str, object] = {"min": [0.0, 0.0], "max": [10.0, 10.0]}
_SQUARE = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
_SHAPE = (32, 32)
_REROLL_SEED = 999
_REROLL_SEED_ALT = 12345


@pytest.fixture(autouse=True)
def _isolated_service() -> None:
    set_service(ProjectService())


@pytest.fixture(autouse=True)
def _small_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the Phase-3 hard-coded shape so tests run in tens of milliseconds."""
    monkeypatch.setattr(terrain_generator, "_shape_from_spec", lambda _axis: _SHAPE)


def _ok(envelope: dict[str, object]) -> dict[str, object]:
    assert envelope["ok"] is True, envelope
    result = envelope["result"]
    assert isinstance(result, dict)
    return result


def _err(envelope: dict[str, object]) -> dict[str, object]:
    assert envelope["ok"] is False, envelope
    error = envelope["error"]
    assert isinstance(error, dict)
    return error


def _bootstrap(tmp_path: Path) -> None:
    _ok(create_project(str(tmp_path), "Demo", _BOUNDS))


def _make_region(
    name: str = "Alpha",
    *,
    primary: TerrainPrimary = TerrainPrimary.ROLLING_HILLS,
    hydrology: Hydrology | None = None,
    seed: int = 7,
) -> str:
    descriptor = StructuredDescriptor(
        terrain=Terrain(primary=primary),
        hydrology=hydrology,
    )
    region = _ok(
        create_region(
            name,
            _SQUARE,
            structured_descriptor=descriptor.model_dump(mode="json"),
            seed=seed,
        ),
    )
    return cast("str", region["node_id"])


# ---------------------------------------------------------------------------
# generate_region
# ---------------------------------------------------------------------------


@freeze_time(_FROZEN)
def test_generate_region_persists_spec_heightmap_and_history(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    rid = _make_region()
    result = _ok(generate_region(rid))
    spec_id = result["spec_id"]
    assert isinstance(spec_id, str)
    assert tmp_path.joinpath("specs", f"{spec_id}.json").is_file()
    npy = result["heightmap_npy_path"]
    png = result["heightmap_png_path"]
    assert isinstance(npy, str)
    assert isinstance(png, str)
    assert tmp_path.joinpath("realizations", "heightmap", f"{rid}.npy").is_file()
    assert tmp_path.joinpath("realizations", "heightmap", f"{rid}.png").is_file()
    assert result["stream_geometry_path"] is None
    assert result["blend_path"] is None
    generators = result["generators_used"]
    assert isinstance(generators, list)
    assert generators[0] == "noise.ridged_multifractal"
    analysis = result["analysis"]
    assert isinstance(analysis, dict)
    assert analysis["stream"] is None
    events = _ok(history_tool())["events"]
    assert isinstance(events, list)
    kinds = [cast("dict[str, object]", e)["kind"] for e in events]
    assert "generate_region" in kinds


def test_generate_region_with_hydrology_persists_stream_geometry(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    rid = _make_region(
        hydrology=Hydrology(has_stream=True, stream_character=StreamCharacter.MEANDERING_RIVER),
    )
    result = _ok(generate_region(rid))
    stream_path = result["stream_geometry_path"]
    assert isinstance(stream_path, str)
    assert tmp_path.joinpath("realizations", "heightmap", f"{rid}.stream.json").is_file()
    analysis = cast("dict[str, object]", result["analysis"])
    assert analysis["stream"] is not None


def test_generate_region_unknown_id(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    error = _err(generate_region("region_missing"))
    assert error["code"] == "unknown_region"


def test_generate_region_no_open_project() -> None:
    error = _err(generate_region("region_x"))
    assert error["code"] == "no_open_project"


def test_generate_region_missing_descriptor(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    region = _ok(create_region("NoDescriptor", _SQUARE))
    rid = cast("str", region["node_id"])
    error = _err(generate_region(rid))
    assert error["code"] == "missing_descriptor"


def test_generate_region_summary_is_recorded_in_persisted_spec(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    rid = _make_region()
    result = _ok(generate_region(rid))
    spec_id = cast("str", result["spec_id"])
    inspected = _ok(inspect_spec(spec_id=spec_id))
    body = cast("dict[str, object]", inspected["body"])
    summary = cast("dict[str, object]", body["summary"])
    assert summary["mean_elevation"] != 0.0


# ---------------------------------------------------------------------------
# reroll_seed
# ---------------------------------------------------------------------------


def test_reroll_seed_uses_supplied_value(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    rid = _make_region(seed=1)
    result = _ok(reroll_seed(rid, seed=_REROLL_SEED))
    assert result["seed"] == _REROLL_SEED


def test_reroll_seed_derives_deterministically_when_omitted(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    rid = _make_region(seed=1)
    first = _ok(reroll_seed(rid))
    # Same region + history-count would give the same seed; after the first
    # reroll the history advanced, so a second reroll yields a different seed.
    second = _ok(reroll_seed(rid))
    assert first["seed"] != second["seed"]
    assert isinstance(first["seed"], int)


def test_reroll_seed_unknown_region(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    error = _err(reroll_seed("region_missing"))
    assert error["code"] == "unknown_region"


def test_reroll_seed_changes_subsequent_generation(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    rid = _make_region(seed=1)
    first = _ok(generate_region(rid))
    _ok(reroll_seed(rid, seed=_REROLL_SEED_ALT))
    second = _ok(generate_region(rid))
    # Different seeds yield different spec ids only via the summary path
    # (descriptor + generators are unchanged); but the heightmap analysis
    # mean changes because the noise base does.
    first_analysis = cast("dict[str, object]", first["analysis"])
    second_analysis = cast("dict[str, object]", second["analysis"])
    first_elev = cast("dict[str, object]", first_analysis["elevation"])
    second_elev = cast("dict[str, object]", second_analysis["elevation"])
    assert first_elev["mean"] != second_elev["mean"]


# ---------------------------------------------------------------------------
# analyze_region
# ---------------------------------------------------------------------------


def test_analyze_region_round_trips_after_generate(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    rid = _make_region()
    generated = _ok(generate_region(rid))
    re_analyzed = _ok(analyze_region(rid))
    assert re_analyzed["analysis"] == generated["analysis"]


def test_analyze_region_before_generation(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    rid = _make_region()
    error = _err(analyze_region(rid))
    assert error["code"] == "not_generated"


def test_analyze_region_picks_up_persisted_stream(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    rid = _make_region(
        hydrology=Hydrology(has_stream=True, stream_character=StreamCharacter.MEANDERING_RIVER),
    )
    _ok(generate_region(rid))
    result = _ok(analyze_region(rid))
    analysis = cast("dict[str, object]", result["analysis"])
    assert analysis["stream"] is not None


# ---------------------------------------------------------------------------
# inspect_spec
# ---------------------------------------------------------------------------


def test_inspect_spec_by_region_id(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    rid = _make_region()
    generated = _ok(generate_region(rid))
    inspected = _ok(inspect_spec(region_id=rid))
    assert inspected["spec_id"] == generated["spec_id"]


def test_inspect_spec_by_spec_id(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    rid = _make_region()
    generated = _ok(generate_region(rid))
    spec_id = cast("str", generated["spec_id"])
    inspected = _ok(inspect_spec(spec_id=spec_id))
    assert inspected["spec_id"] == spec_id


def test_inspect_spec_requires_exactly_one_argument(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    neither = _err(inspect_spec())
    both = _err(inspect_spec(spec_id="x", region_id="y"))
    assert neither["code"] == "invalid_arguments"
    assert both["code"] == "invalid_arguments"


def test_inspect_spec_unknown_id(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    error = _err(inspect_spec(spec_id="ffffffffffff"))
    assert error["code"] == "unknown_spec"


def test_inspect_spec_region_without_generation(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    rid = _make_region()
    error = _err(inspect_spec(region_id=rid))
    assert error["code"] == "not_generated"


def test_inspect_spec_unknown_region(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    error = _err(inspect_spec(region_id="region_missing"))
    assert error["code"] == "unknown_region"
