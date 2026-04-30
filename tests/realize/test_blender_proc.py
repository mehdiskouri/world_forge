"""Tests for blender_proc.BlenderProcess (env handling + integration)."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
from forge_mcp.realize import (
    BLENDER_BIN_ENV,
    BlenderNotConfiguredError,
    BlenderProcess,
    RpcError,
    blender_binary,
)
from PIL import Image

ERR_METHOD_NOT_FOUND = -32601


def test_blender_binary_raises_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(BLENDER_BIN_ENV, raising=False)
    with pytest.raises(BlenderNotConfiguredError, match="not set"):
        blender_binary()


def test_blender_binary_raises_when_path_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bogus = tmp_path / "nope"
    monkeypatch.setenv(BLENDER_BIN_ENV, str(bogus))
    with pytest.raises(BlenderNotConfiguredError, match="does not exist"):
        blender_binary()


def test_blender_binary_returns_path_when_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake = tmp_path / "blender"
    fake.write_text("")
    monkeypatch.setenv(BLENDER_BIN_ENV, str(fake))
    assert blender_binary() == fake


def test_blender_process_client_property_raises_before_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake = tmp_path / "blender"
    fake.write_text("")
    monkeypatch.setenv(BLENDER_BIN_ENV, str(fake))
    proc = BlenderProcess()
    with pytest.raises(RuntimeError, match="not started"):
        _ = proc.client


def test_blender_process_stop_is_noop_before_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake = tmp_path / "blender"
    fake.write_text("")
    monkeypatch.setenv(BLENDER_BIN_ENV, str(fake))
    proc = BlenderProcess()
    assert proc.stop() == 0


# -- Integration tests (gated on FORGE_BLENDER_BIN pointing at real Blender) --


@pytest.mark.blender_integration
@pytest.mark.skipif(
    not os.environ.get(BLENDER_BIN_ENV) or not Path(os.environ[BLENDER_BIN_ENV]).exists(),
    reason=f"requires ${BLENDER_BIN_ENV} pointing at a real Blender 5.0 binary",
)
def test_integration_ping() -> None:
    with BlenderProcess() as proc:
        result = proc.client.call("ping")
    assert isinstance(result, dict)
    assert result.get("alive") is True
    assert isinstance(result.get("blender"), str)


@pytest.mark.blender_integration
@pytest.mark.skipif(
    not os.environ.get(BLENDER_BIN_ENV) or not Path(os.environ[BLENDER_BIN_ENV]).exists(),
    reason=f"requires ${BLENDER_BIN_ENV} pointing at a real Blender 5.0 binary",
)
def test_integration_idprop_round_trip() -> None:
    """Round-trip an IDProperty on a freshly-created Mesh datablock.

    Validates the bleeding-edge 5.0 IDProperty refactor (PRD §11.7);
    a regression here is the canary that triggers the fallback path
    documented in ARCHITECTURE §5.6.
    """
    with BlenderProcess() as proc:
        proc.client.call("bpy.data.meshes.new", {"name": "forge_probe"})
        proc.client.call(
            "set_idprop",
            {
                "collection": "meshes",
                "name": "forge_probe",
                "key": "forge_node_id",
                "value": "region_alpheim_north",
            },
        )
        got = proc.client.call(
            "get_idprop",
            {"collection": "meshes", "name": "forge_probe", "key": "forge_node_id"},
        )
    assert isinstance(got, dict)
    assert got.get("value") == "region_alpheim_north"


@pytest.mark.blender_integration
@pytest.mark.skipif(
    not os.environ.get(BLENDER_BIN_ENV) or not Path(os.environ[BLENDER_BIN_ENV]).exists(),
    reason=f"requires ${BLENDER_BIN_ENV} pointing at a real Blender 5.0 binary",
)
def test_integration_unknown_method_returns_jsonrpc_error() -> None:
    with BlenderProcess() as proc, pytest.raises(RpcError) as excinfo:
        proc.client.call("does_not_exist")
    assert excinfo.value.code == ERR_METHOD_NOT_FOUND


@pytest.mark.blender_integration
@pytest.mark.skipif(
    not os.environ.get(BLENDER_BIN_ENV) or not Path(os.environ[BLENDER_BIN_ENV]).exists(),
    reason=f"requires ${BLENDER_BIN_ENV} pointing at a real Blender 5.0 binary",
)
def test_integration_mesh_from_pydata_creates_mesh_and_object() -> None:
    """``mesh.from_pydata`` produces a Mesh datablock + linked Object."""
    with BlenderProcess() as proc:
        result = proc.client.call(
            "mesh.from_pydata",
            {
                "name": "forge_quad",
                "vertices": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0]],
                "edges": [],
                "faces": [[0, 1, 2, 3]],
            },
        )
        diff = proc.client.call("scene.diff")
    assert isinstance(result, dict)
    assert result.get("mesh_name") == "forge_quad"
    assert result.get("object_name") == "forge_quad"
    assert isinstance(diff, dict)
    diff_objects = diff.get("objects")
    diff_meshes = diff.get("meshes")
    assert isinstance(diff_objects, int)
    assert isinstance(diff_meshes, int)
    assert diff_objects >= 1
    assert diff_meshes >= 1


@pytest.mark.blender_integration
@pytest.mark.skipif(
    not os.environ.get(BLENDER_BIN_ENV) or not Path(os.environ[BLENDER_BIN_ENV]).exists(),
    reason=f"requires ${BLENDER_BIN_ENV} pointing at a real Blender 5.0 binary",
)
def test_integration_image_from_file_loads_png(tmp_path: Path) -> None:
    """``image.from_file`` loads a tiny PNG into ``bpy.data.images``."""
    png_path = tmp_path / "tiny.png"
    Image.fromarray(np.zeros((4, 4), dtype=np.uint8), mode="L").save(png_path)
    with BlenderProcess() as proc:
        result = proc.client.call("image.from_file", {"filepath": str(png_path)})
    assert isinstance(result, dict)
    expected_dim = 4
    assert result.get("width") == expected_dim
    assert result.get("height") == expected_dim


@pytest.mark.blender_integration
@pytest.mark.skipif(
    not os.environ.get(BLENDER_BIN_ENV) or not Path(os.environ[BLENDER_BIN_ENV]).exists(),
    reason=f"requires ${BLENDER_BIN_ENV} pointing at a real Blender 5.0 binary",
)
def test_integration_image_from_file_missing_path_raises_invalid_params(tmp_path: Path) -> None:
    bogus = tmp_path / "missing.png"
    err_invalid_params = -32602
    with BlenderProcess() as proc, pytest.raises(RpcError) as excinfo:
        proc.client.call("image.from_file", {"filepath": str(bogus)})
    assert excinfo.value.code == err_invalid_params


@pytest.mark.blender_integration
@pytest.mark.skipif(
    not os.environ.get(BLENDER_BIN_ENV) or not Path(os.environ[BLENDER_BIN_ENV]).exists(),
    reason=f"requires ${BLENDER_BIN_ENV} pointing at a real Blender 5.0 binary",
)
def test_integration_render_to_file_writes_png(tmp_path: Path) -> None:
    """``render.to_file`` produces a non-empty PNG at the requested path."""
    out = tmp_path / "preview.png"
    res_x = 64
    res_y = 48
    with BlenderProcess() as proc:
        proc.client.call("bpy.data.cameras.new", {"name": "render_cam"})
        proc.client.call(
            "bpy.ops.object.camera_add",
            {"location": [0.0, -5.0, 2.0], "rotation": [1.2, 0.0, 0.0]},
        )
        result = proc.client.call(
            "render.to_file",
            {
                "filepath": str(out),
                "resolution_x": res_x,
                "resolution_y": res_y,
                "engine": "CYCLES",
                "camera_name": "Camera",
            },
        )
    assert isinstance(result, dict)
    assert result.get("width") == res_x
    assert result.get("height") == res_y
    file_size = result.get("file_size_bytes")
    assert isinstance(file_size, int)
    assert file_size > 0
    assert out.exists()


@pytest.mark.blender_integration
@pytest.mark.skipif(
    not os.environ.get(BLENDER_BIN_ENV) or not Path(os.environ[BLENDER_BIN_ENV]).exists(),
    reason=f"requires ${BLENDER_BIN_ENV} pointing at a real Blender 5.0 binary",
)
def test_integration_material_build_terrain_assigns_material() -> None:
    """``material.build_terrain`` builds a node tree and binds it to the mesh."""
    with BlenderProcess() as proc:
        proc.client.call(
            "mesh.from_pydata",
            {
                "name": "mat_target",
                "vertices": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                "edges": [],
                "faces": [[0, 1, 2]],
            },
        )
        result = proc.client.call(
            "material.build_terrain",
            {
                "material_name": "forge_terrain_v1",
                "target_object": "mat_target",
                "color_ramp_stops": [
                    {"position": 0.0, "color": [0.1, 0.2, 0.4, 1.0]},
                    {"position": 1.0, "color": [0.9, 0.9, 0.9, 1.0]},
                ],
                "slope_threshold": 0.6,
            },
        )
    assert isinstance(result, dict)
    assert result.get("material_name") == "forge_terrain_v1"


@pytest.mark.blender_integration
@pytest.mark.skipif(
    not os.environ.get(BLENDER_BIN_ENV) or not Path(os.environ[BLENDER_BIN_ENV]).exists(),
    reason=f"requires ${BLENDER_BIN_ENV} pointing at a real Blender 5.0 binary",
)
def test_integration_scene_diff_reports_all_collections() -> None:
    """``scene.diff`` reports counts for every collection the engine watches."""
    expected_keys = {
        "objects",
        "meshes",
        "materials",
        "images",
        "lights",
        "cameras",
        "curves",
        "worlds",
    }
    with BlenderProcess() as proc:
        diff = proc.client.call("scene.diff")
    assert isinstance(diff, dict)
    assert expected_keys.issubset(diff.keys())
    for key in expected_keys:
        assert isinstance(diff[key], int)
