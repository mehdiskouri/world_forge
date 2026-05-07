"""Pure unit tests for RPC framing and the host-side client."""

from __future__ import annotations

import io
import json
import threading

import pytest
from forge_mcp.realize import (
    RpcClient,
    RpcError,
    RpcMethods,
    RpcProtocolError,
    RpcRequest,
    RpcResponse,
)

# Stable JSON-RPC error code constants (mirror scripts/blender/adapter.py).
ERR_METHOD_NOT_FOUND = -32601
SAMPLE_REQUEST_ID = 7


def test_rpc_request_serializes_with_required_fields() -> None:
    req = RpcRequest(method="ping", params={}, id=1)
    payload = json.loads(req.to_json())
    assert payload == {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}


def test_rpc_response_parses_success() -> None:
    line = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": SAMPLE_REQUEST_ID,
            "result": {"alive": True},
        }
    )
    resp = RpcResponse.from_json(line)
    assert resp.id == SAMPLE_REQUEST_ID
    assert resp.error is None
    assert resp.result == {"alive": True}


def test_rpc_response_parses_error() -> None:
    line = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": SAMPLE_REQUEST_ID,
            "error": {"code": ERR_METHOD_NOT_FOUND, "message": "unknown"},
        }
    )
    resp = RpcResponse.from_json(line)
    assert resp.error is not None
    assert resp.error.code == ERR_METHOD_NOT_FOUND
    assert resp.result is None


def test_rpc_response_rejects_non_json() -> None:
    with pytest.raises(RpcProtocolError, match="non-JSON"):
        RpcResponse.from_json("not json")


def test_rpc_response_rejects_non_object() -> None:
    with pytest.raises(RpcProtocolError, match="not an object"):
        RpcResponse.from_json("[1,2,3]")


def test_rpc_response_rejects_bad_jsonrpc_version() -> None:
    with pytest.raises(RpcProtocolError, match="bad jsonrpc"):
        RpcResponse.from_json(json.dumps({"jsonrpc": "1.0", "id": 1, "result": {}}))


def test_rpc_response_rejects_non_object_error() -> None:
    line = json.dumps({"jsonrpc": "2.0", "id": 1, "error": "boom"})
    with pytest.raises(RpcProtocolError, match="error not an object"):
        RpcResponse.from_json(line)


class _FakeStreams:
    """In-memory peer that responds to known requests."""

    def __init__(self, responder: object) -> None:
        self._responder = responder
        self._stdin = io.StringIO()
        self._stdout = io.StringIO()
        self._lock = threading.Lock()

    def write(self, data: str) -> int:
        """Receive bytes from RpcClient.stdin, eagerly produce stdout responses."""
        self._stdin.write(data)
        with self._lock:
            self._stdin.seek(0)
            content = self._stdin.read()
            self._stdin.seek(0)
            self._stdin.truncate()
            for line in content.splitlines():
                if not line.strip():
                    continue
                req = json.loads(line)
                resp = self._responder(req)  # type: ignore[operator]
                self._stdout.write(json.dumps(resp) + "\n")
            self._stdout.seek(0)
        return len(data)

    def flush(self) -> None:
        """No-op; in-memory streams have no buffer to flush."""

    def readline(self) -> str:
        """Return the next queued response line, or '' if peer closed."""
        with self._lock:
            return self._stdout.readline()


def test_client_call_round_trips() -> None:
    def responder(req: dict[str, object]) -> dict[str, object]:
        assert req["method"] == "ping"
        return {"jsonrpc": "2.0", "id": req["id"], "result": {"alive": True}}

    streams = _FakeStreams(responder)
    client = RpcClient(stdin=streams, stdout=streams)  # type: ignore[arg-type]
    result = client.call("ping")
    assert result == {"alive": True}


def test_client_call_raises_rpc_error() -> None:
    def responder(req: dict[str, object]) -> dict[str, object]:
        return {
            "jsonrpc": "2.0",
            "id": req["id"],
            "error": {"code": ERR_METHOD_NOT_FOUND, "message": "no such method"},
        }

    streams = _FakeStreams(responder)
    client = RpcClient(stdin=streams, stdout=streams)  # type: ignore[arg-type]
    with pytest.raises(RpcError) as excinfo:
        client.call("nope")
    assert excinfo.value.code == ERR_METHOD_NOT_FOUND


def test_client_call_detects_id_mismatch() -> None:
    def responder(_req: dict[str, object]) -> dict[str, object]:
        return {"jsonrpc": "2.0", "id": 9999, "result": {}}

    streams = _FakeStreams(responder)
    client = RpcClient(stdin=streams, stdout=streams)  # type: ignore[arg-type]
    with pytest.raises(RpcProtocolError, match="id mismatch"):
        client.call("ping")


def test_client_call_detects_closed_peer() -> None:
    class ClosedStream:
        """Stream that accepts writes silently and never returns data."""

        def write(self, _data: str) -> int:
            """Discard the write; signature parity for the IO[str] protocol."""
            return 0

        def flush(self) -> None:
            """No-op."""

        def readline(self) -> str:
            """Always return '' to signal a closed peer."""
            return ""

    stream = ClosedStream()
    client = RpcClient(stdin=stream, stdout=stream)  # type: ignore[arg-type]
    with pytest.raises(RpcProtocolError, match="closed stdout"):
        client.call("ping")


def test_rpc_error_str_includes_code_and_message() -> None:
    err = RpcError(code=ERR_METHOD_NOT_FOUND, message="not found")
    assert str(ERR_METHOD_NOT_FOUND) in str(err)
    assert "not found" in str(err)


def test_rpc_methods_static_constants_match_adapter_surface() -> None:
    assert RpcMethods.PING == "ping"
    assert RpcMethods.SHUTDOWN == "shutdown"
    assert RpcMethods.SET_PROPERTY == "set_property"
    assert RpcMethods.GET_PROPERTY == "get_property"
    assert RpcMethods.SET_IDPROP == "set_idprop"
    assert RpcMethods.GET_IDPROP == "get_idprop"
    assert RpcMethods.MESH_FROM_PYDATA == "mesh.from_pydata"
    assert RpcMethods.IMAGE_FROM_FILE == "image.from_file"
    assert RpcMethods.RENDER_TO_FILE == "render.to_file"
    assert RpcMethods.MATERIAL_BUILD_COMPOSITE == "material.build_composite"
    assert RpcMethods.SCENE_DIFF == "scene.diff"


def test_rpc_methods_dynamic_helpers_compose_method_names() -> None:
    assert RpcMethods.bpy_ops("mesh", "primitive_plane_add") == "bpy.ops.mesh.primitive_plane_add"
    assert RpcMethods.bpy_data_new("meshes") == "bpy.data.meshes.new"
    assert RpcMethods.bpy_data_remove("objects") == "bpy.data.objects.remove"
