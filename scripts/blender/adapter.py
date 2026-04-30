"""Blender-internal JSON-RPC adapter (Phase 1 spike 2).

Launched via::

    blender --background --python scripts/blender/adapter.py

Speaks JSON-RPC 2.0 over stdio. The host (`forge_mcp.realize.blender_proc`)
sends one request per line on stdin; the adapter writes one response per
line on stdout. **All Blender chatter goes to stderr** so the framing
stays clean — Blender prints version banners and other noise on stdout
by default unless redirected.

Methods (v1 surface — Phase 1 only validates the dispatch loop, the
realizer is Phase 4):

* ``ping``                          — `{ "alive": true, "blender": "5.0.0" }`
* ``shutdown``                      — graceful exit
* ``bpy.ops.<group>.<name>``        — call an operator with kwargs
* ``bpy.data.<collection>.new``     — create a datablock
* ``bpy.data.<collection>.remove``  — remove a datablock
* ``set_property`` / ``get_property`` — read/write attribute paths on
  a named datablock (e.g., `Object.location`)
* ``set_idprop`` / ``get_idprop``   — IDProperty round-trip on Object/Mesh

Errors follow JSON-RPC 2.0: ``-32601`` method not found, ``-32602``
invalid params, ``-32000`` Blender execution error.
"""

# ruff: noqa: T201, BLE001  # printing to stderr & broad excepts are intentional in
# the Blender-internal adapter: an uncaught Python exception would
# crash Blender; we want to surface it to the host as a JSON-RPC error.

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

import bpy  # type: ignore[import-not-found]  # Blender-provided

# JSON-RPC error codes (per spec)
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603
ERR_BLENDER = -32000


def _bpy_version() -> str:
    return ".".join(str(p) for p in bpy.app.version)


def _resolve_attr(root: object, path: str) -> object:
    obj: object = root
    for part in path.split("."):
        if part:
            obj = getattr(obj, part)
    return obj


def _set_attr(root: object, path: str, value: object) -> None:
    parts = path.split(".")
    target = root
    for part in parts[:-1]:
        target = getattr(target, part)
    setattr(target, parts[-1], value)


def _handle_ping(_params: dict[str, Any]) -> dict[str, Any]:
    return {"alive": True, "blender": _bpy_version()}


def _handle_bpy_ops(method: str, params: dict[str, Any]) -> dict[str, Any]:
    # method is e.g. "bpy.ops.mesh.primitive_plane_add"
    parts = method.split(".")
    if len(parts) < 4 or parts[0] != "bpy" or parts[1] != "ops":  # noqa: PLR2004
        msg = f"not a bpy.ops method: {method!r}"
        raise ValueError(msg)
    op = bpy.ops
    for part in parts[2:]:
        op = getattr(op, part)
    result = op(**params)
    return {"status": list(result) if hasattr(result, "__iter__") else str(result)}


def _handle_bpy_data_new(method: str, params: dict[str, Any]) -> dict[str, Any]:
    # method is "bpy.data.<collection>.new"
    parts = method.split(".")
    expected_parts = 4
    if len(parts) != expected_parts or parts[3] != "new":
        msg = f"not a bpy.data.*.new method: {method!r}"
        raise ValueError(msg)
    collection = getattr(bpy.data, parts[2])
    created = collection.new(**params)
    return {"name": created.name}


def _handle_bpy_data_remove(method: str, params: dict[str, Any]) -> dict[str, Any]:
    parts = method.split(".")
    expected_parts = 4
    if len(parts) != expected_parts or parts[3] != "remove":
        msg = f"not a bpy.data.*.remove method: {method!r}"
        raise ValueError(msg)
    name = params.get("name")
    if not isinstance(name, str):
        msg = "remove requires string 'name'"
        raise ValueError(msg)
    collection = getattr(bpy.data, parts[2])
    target = collection.get(name)
    if target is None:
        msg = f"no datablock named {name!r} in bpy.data.{parts[2]}"
        raise KeyError(msg)
    collection.remove(target)
    return {"removed": name}


def _handle_set_property(params: dict[str, Any]) -> dict[str, Any]:
    collection_name = params["collection"]
    name = params["name"]
    path = params["path"]
    value = params["value"]
    target = getattr(bpy.data, collection_name).get(name)
    if target is None:
        msg = f"no datablock named {name!r} in bpy.data.{collection_name}"
        raise KeyError(msg)
    _set_attr(target, path, value)
    return {"set": True}


def _handle_get_property(params: dict[str, Any]) -> dict[str, Any]:
    collection_name = params["collection"]
    name = params["name"]
    path = params["path"]
    target = getattr(bpy.data, collection_name).get(name)
    if target is None:
        msg = f"no datablock named {name!r} in bpy.data.{collection_name}"
        raise KeyError(msg)
    raw = _resolve_attr(target, path)
    return {"value": _serialize(raw)}


def _serialize(value: object) -> object:
    """Best-effort coercion of bpy values to JSON-safe types."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "__iter__") and not isinstance(value, dict):
        try:
            return [_serialize(v) for v in value]  # type: ignore[union-attr]
        except TypeError:
            return str(value)
    return str(value)


def _handle_set_idprop(params: dict[str, Any]) -> dict[str, Any]:
    collection_name = params["collection"]
    name = params["name"]
    key = params["key"]
    value = params["value"]
    target = getattr(bpy.data, collection_name).get(name)
    if target is None:
        msg = f"no datablock named {name!r} in bpy.data.{collection_name}"
        raise KeyError(msg)
    target[key] = value
    return {"set": True}


def _handle_get_idprop(params: dict[str, Any]) -> dict[str, Any]:
    collection_name = params["collection"]
    name = params["name"]
    key = params["key"]
    target = getattr(bpy.data, collection_name).get(name)
    if target is None:
        msg = f"no datablock named {name!r} in bpy.data.{collection_name}"
        raise KeyError(msg)
    if key not in target.keys():  # noqa: SIM118  # bpy IDProperties expose .keys() but not __contains__
        msg = f"no IDProperty {key!r} on {collection_name}/{name}"
        raise KeyError(msg)
    return {"value": _serialize(target[key])}


def _dispatch(method: str, params: dict[str, Any]) -> dict[str, Any]:
    if method == "ping":
        return _handle_ping(params)
    if method.startswith("bpy.ops."):
        return _handle_bpy_ops(method, params)
    if method.startswith("bpy.data.") and method.endswith(".new"):
        return _handle_bpy_data_new(method, params)
    if method.startswith("bpy.data.") and method.endswith(".remove"):
        return _handle_bpy_data_remove(method, params)
    if method == "set_property":
        return _handle_set_property(params)
    if method == "get_property":
        return _handle_get_property(params)
    if method == "set_idprop":
        return _handle_set_idprop(params)
    if method == "get_idprop":
        return _handle_get_idprop(params)
    msg = f"unknown method: {method!r}"
    raise LookupError(msg)


def _make_response(req_id: object, result: dict[str, Any] | None, error: dict[str, Any] | None) -> str:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    return json.dumps(payload, separators=(",", ":"))


def _process_line(line: str) -> tuple[str | None, bool]:
    """Process one JSON-RPC line. Returns (response_or_None, should_shutdown)."""
    line = line.strip()
    if not line:
        return None, False
    try:
        req = json.loads(line)
    except json.JSONDecodeError as exc:
        return _make_response(None, None, {"code": ERR_PARSE, "message": str(exc)}), False
    if not isinstance(req, dict):
        return _make_response(None, None, {"code": ERR_INVALID_REQUEST, "message": "not an object"}), False
    req_id = req.get("id")
    method = req.get("method")
    params_raw = req.get("params", {})
    if not isinstance(method, str) or not isinstance(params_raw, dict):
        return _make_response(req_id, None, {"code": ERR_INVALID_REQUEST, "message": "bad method/params"}), False
    if method == "shutdown":
        return _make_response(req_id, {"shutdown": True}, None), True
    try:
        result = _dispatch(method, params_raw)
    except LookupError as exc:
        return _make_response(req_id, None, {"code": ERR_METHOD_NOT_FOUND, "message": str(exc)}), False
    except (ValueError, KeyError, TypeError) as exc:
        return _make_response(req_id, None, {"code": ERR_INVALID_PARAMS, "message": str(exc)}), False
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        return _make_response(req_id, None, {"code": ERR_BLENDER, "message": str(exc)}), False
    return _make_response(req_id, result, None), False


def main() -> int:
    print(f"adapter: blender={_bpy_version()} ready", file=sys.stderr)
    sys.stderr.flush()
    for raw in sys.stdin:
        response, done = _process_line(raw)
        if response is not None:
            sys.stdout.write(response + "\n")
            sys.stdout.flush()
        if done:
            break
    print("adapter: shutdown", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
