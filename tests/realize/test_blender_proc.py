"""Tests for blender_proc.BlenderProcess (env handling + integration)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from forge_mcp.realize import (
    BLENDER_BIN_ENV,
    BlenderNotConfiguredError,
    BlenderProcess,
    RpcError,
    blender_binary,
)

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
