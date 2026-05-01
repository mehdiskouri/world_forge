"""Tests for the Phase-4 realizer wiring in ``forge.generate_region`` + ``forge.render_view``."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from forge_mcp.descriptor.schema import (
    StructuredDescriptor,
    Terrain,
    TerrainPrimary,
)
from forge_mcp.generate import terrain as terrain_generator
from forge_mcp.project.service import ProjectService
from forge_mcp.realize.engine import (
    RealizationResult,
    RealizationTraceStep,
    RealizerStepError,
)
from forge_mcp.server.tools import set_realizer_factory, set_service
from forge_mcp.server.tools.generation import (
    generate_region,
    render_view,
)
from forge_mcp.server.tools.projects import create_project
from forge_mcp.server.tools.regions import create_region

if TYPE_CHECKING:
    from collections.abc import Iterator

    from forge_mcp.realize.engine import RealizerEngine
    from forge_mcp.realize.macros import RealizeRegionInputs, RenderPreviewInputs


_BOUNDS: dict[str, object] = {"min": [0.0, 0.0], "max": [10.0, 10.0]}
_SQUARE = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
_SHAPE = (16, 16)


@pytest.fixture(autouse=True)
def _isolated_service() -> Iterator[None]:
    set_service(ProjectService())
    yield
    set_realizer_factory(None)


@pytest.fixture(autouse=True)
def _small_grid(monkeypatch: pytest.MonkeyPatch) -> None:
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


def _bootstrap(tmp_path: Path) -> str:
    _ok(create_project(str(tmp_path), "Demo", _BOUNDS))
    descriptor = StructuredDescriptor(terrain=Terrain(primary=TerrainPrimary.ROLLING_HILLS))
    region = _ok(
        create_region(
            "Alpha",
            _SQUARE,
            structured_descriptor=descriptor.model_dump(mode="json"),
            seed=7,
        ),
    )
    return cast("str", region["node_id"])


class _FakeEngine:
    """Stand-in for :class:`RealizerEngine` used by the generation tools."""

    def __init__(self) -> None:
        self.macros_called: list[str] = []


def _install_fake_factory(
    monkeypatch: pytest.MonkeyPatch,
    *,
    on_realize: object = None,
    on_render: object = None,
) -> _FakeEngine:
    """Install a fake factory + monkeypatch the macros to write tmp artifacts.

    ``on_realize`` / ``on_render`` may be a callable that receives
    ``(engine, inputs)`` to override the default success behaviour
    (write tmp files + return canned results).
    """
    fake = _FakeEngine()

    @contextmanager
    def factory() -> Iterator[RealizerEngine]:
        yield cast("RealizerEngine", fake)

    set_realizer_factory(factory)

    def _trace_step(call: str) -> RealizationTraceStep:
        return RealizationTraceStep(
            sequence_name="realize_region",
            step_index=0,
            call=call,
            duration_ms=1.0,
            scene_diff_before=None,
            scene_diff_after=None,
            result=None,
        )

    def default_realize(
        engine: RealizerEngine,
        inputs: RealizeRegionInputs,
    ) -> RealizationResult:
        # simulate save_blend writing the tmp file
        Path(inputs.blend_filepath).write_bytes(b"BLEND-TMP")
        cast("_FakeEngine", engine).macros_called.append("realize_region")
        return RealizationResult(
            macro="realize_region",
            trace=(_trace_step("seq:save_blend"),),
            final_result={"blend_filepath": inputs.blend_filepath},
            total_duration_ms=2.0,
            sequence_id="a" * 20,
        )

    def default_render(
        engine: RealizerEngine,
        inputs: RenderPreviewInputs,
    ) -> RealizationResult:
        Path(inputs.filepath).write_bytes(b"PNG-TMP")
        cast("_FakeEngine", engine).macros_called.append("render_preview")
        size = len(b"PNG-TMP")
        return RealizationResult(
            macro="render_preview",
            trace=(_trace_step("render.to_file"),),
            final_result={
                "path": inputs.filepath,
                "file_size_bytes": size,
                "width": inputs.resolution_x,
                "height": inputs.resolution_y,
            },
            total_duration_ms=3.0,
            sequence_id="b" * 20,
        )

    monkeypatch.setattr(
        "forge_mcp.server.tools.generation.realize_region",
        on_realize if on_realize is not None else default_realize,
    )
    monkeypatch.setattr(
        "forge_mcp.server.tools.generation.render_preview",
        on_render if on_render is not None else default_render,
    )
    return fake


# ---------------------------------------------------------------------------
# generate_region with realizer
# ---------------------------------------------------------------------------


def test_generate_region_skips_realizer_when_no_factory(tmp_path: Path) -> None:
    rid = _bootstrap(tmp_path)
    result = _ok(generate_region(rid))
    assert result["blend_path"] is None
    assert result["previews"] is None
    assert result["realization"] is None


def test_generate_region_runs_realizer_when_factory_installed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rid = _bootstrap(tmp_path)
    fake = _install_fake_factory(monkeypatch)

    result = _ok(generate_region(rid))

    # One realize_region call, then one render per view (ortho_top + perspective_se).
    assert fake.macros_called == [
        "realize_region",
        "render_preview",
        "render_preview",
    ]
    blend = result["blend_path"]
    previews = result["previews"]
    assert isinstance(blend, str)
    assert isinstance(previews, dict)
    assert set(previews) == {"ortho_top", "perspective_se"}
    assert tmp_path.joinpath("realizations", "blender", f"{rid}.blend").is_file()
    for view_kind in ("ortho_top", "perspective_se"):
        assert tmp_path.joinpath(
            "realizations",
            "blender",
            f"{rid}.{view_kind}.default.png",
        ).is_file()
        view = previews[view_kind]
        assert isinstance(view, dict)
        assert view["resolution"] == "default"
        assert view["render_resolution"] == [1024, 768]
        assert view["render_file_size_bytes"] == len(b"PNG-TMP")

        # Trace sidecar persisted as canonical JSON.
        trace_path = tmp_path / cast("str", view["realization_trace_path"])
        payload = json.loads(trace_path.read_text(encoding="utf-8"))
        assert payload["region_id"] == rid
        assert payload["view_kind"] == view_kind
        assert payload["macro"] == "render_preview"

    summary = result["realization"]
    assert isinstance(summary, dict)
    assert summary["macro"] == "realize_region"
    assert summary["default_view_kind"] == "ortho_top"
    assert summary["default_resolution"] == [1024, 768]


def test_generate_region_realizer_failure_returns_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rid = _bootstrap(tmp_path)

    def boom(_engine: RealizerEngine, _inputs: RealizeRegionInputs) -> RealizationResult:
        msg = "kaboom"
        raise RealizerStepError(
            msg,
            sequence_name="realize_region",
            step_index=0,
            trace=(),
        )

    _install_fake_factory(monkeypatch, on_realize=boom)
    error = _err(generate_region(rid))
    assert error["code"] == "realizer_failed"
    assert "kaboom" in cast("str", error["message"])


# ---------------------------------------------------------------------------
# render_view
# ---------------------------------------------------------------------------


def test_render_view_rejects_unknown_view_kind(tmp_path: Path) -> None:
    rid = _bootstrap(tmp_path)
    error = _err(render_view(rid, view_kind="ultra"))
    assert error["code"] == "invalid_view_kind"


def test_render_view_rejects_unknown_resolution(tmp_path: Path) -> None:
    rid = _bootstrap(tmp_path)
    error = _err(render_view(rid, view_kind="ortho_top", resolution="huge"))
    assert error["code"] == "invalid_resolution"


def test_render_view_requires_existing_region(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    error = _err(render_view("r-missing", view_kind="ortho_top"))
    assert error["code"] == "unknown_region"


def test_render_view_requires_prior_generation(tmp_path: Path) -> None:
    rid = _bootstrap(tmp_path)
    error = _err(render_view(rid, view_kind="ortho_top"))
    assert error["code"] == "not_generated"


def test_render_view_requires_realizer_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ARG001 - reuses small-grid fixture only
) -> None:
    rid = _bootstrap(tmp_path)
    _ok(generate_region(rid))
    error = _err(render_view(rid, view_kind="ortho_top"))
    assert error["code"] == "realizer_not_configured"


def test_render_view_writes_blend_preview_and_trace_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rid = _bootstrap(tmp_path)
    _install_fake_factory(monkeypatch)
    _ok(generate_region(rid))  # establishes heightmap + initial realization

    result = _ok(render_view(rid, view_kind="perspective_se", resolution="full"))
    assert result["view_kind"] == "perspective_se"
    assert result["resolution"] == "full"
    assert result["render_resolution"] == [2048, 1536]
    blend = tmp_path.joinpath("realizations", "blender", f"{rid}.blend")
    preview = tmp_path.joinpath(
        "realizations",
        "blender",
        f"{rid}.perspective_se.full.png",
    )
    trace = tmp_path.joinpath(
        "realizations",
        "blender",
        f"{rid}.perspective_se.full.trace.json",
    )
    assert blend.is_file()
    assert preview.is_file()
    assert trace.is_file()
    payload = json.loads(trace.read_text(encoding="utf-8"))
    assert payload["view_kind"] == "perspective_se"
    # No leftover .tmp files.
    assert not list(blend.parent.glob("*.tmp"))


def test_render_view_with_no_render_size_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rid = _bootstrap(tmp_path)

    def render_no_size(
        engine: RealizerEngine,
        inputs: RenderPreviewInputs,
    ) -> RealizationResult:
        Path(inputs.filepath).write_bytes(b"PNG")
        cast("_FakeEngine", engine).macros_called.append("render_preview")
        return RealizationResult(
            macro="render_preview",
            trace=(),
            final_result={"path": inputs.filepath},  # missing file_size_bytes
            total_duration_ms=0.0,
            sequence_id="c" * 20,
        )

    _install_fake_factory(monkeypatch, on_render=render_no_size)
    _ok(generate_region(rid))
    result = _ok(render_view(rid, view_kind="ortho_top", resolution="preview"))
    assert result["render_file_size_bytes"] is None
