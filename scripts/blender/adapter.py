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
* ``mesh.from_pydata``              — create a Mesh + Object from raw
  vertex/edge/face data (Phase 4 realize path)
* ``image.from_file``               — load an image file into
  ``bpy.data.images`` (Phase 4 displacement texture path)
* ``render.to_file``                — render the active scene to a
  PNG path with controlled resolution / compression (Phase 4 preview)
* ``material.build_terrain``        — build a single elevation-driven
  terrain material and assign it to a mesh object (Phase 4)
* ``object.from_data``              — wrap a named ``bpy.data.<coll>``
  data-block in an object and link it into the scene collection
  (Phase 4 camera / lamp creation path)
* ``scene.assign_world``            — assign a named
  ``bpy.data.worlds`` entry to ``bpy.context.scene.world`` (Phase 4)
* ``scene.diff``                    — return per-collection counts
  for postcondition checks (Phase 4 engine)

Errors follow JSON-RPC 2.0: ``-32601`` method not found, ``-32602``
invalid params, ``-32000`` Blender execution error.
"""

# ruff: noqa: T201, BLE001  # printing to stderr & broad excepts are intentional in
# the Blender-internal adapter: an uncaught Python exception would
# crash Blender; we want to surface it to the host as a JSON-RPC error.

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
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


# ---------------------------------------------------------------------------
# Phase 4 additions: mesh / image / render / material / scene-diff
# ---------------------------------------------------------------------------


def _handle_mesh_from_pydata(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    vertices = params.get("vertices", [])
    edges = params.get("edges", [])
    faces = params.get("faces", [])
    if not isinstance(name, str):
        msg = "mesh.from_pydata requires string 'name'"
        raise ValueError(msg)
    if not isinstance(vertices, list) or not isinstance(edges, list) or not isinstance(faces, list):
        msg = "mesh.from_pydata requires list 'vertices', 'edges', 'faces'"
        raise ValueError(msg)
    mesh = bpy.data.meshes.new(name=name)
    mesh.from_pydata(
        [tuple(v) for v in vertices],
        [tuple(e) for e in edges],
        [tuple(f) for f in faces],
    )
    mesh.update()
    obj = bpy.data.objects.new(name=name, object_data=mesh)
    bpy.context.scene.collection.objects.link(obj)
    return {"mesh_name": mesh.name, "object_name": obj.name}


def _handle_image_from_file(params: dict[str, Any]) -> dict[str, Any]:
    filepath = params.get("filepath")
    if not isinstance(filepath, str):
        msg = "image.from_file requires string 'filepath'"
        raise ValueError(msg)
    if not Path(filepath).exists():
        msg = f"image file does not exist: {filepath!r}"
        raise FileNotFoundError(msg)
    image = bpy.data.images.load(filepath, check_existing=False)
    return {
        "name": image.name,
        "width": int(image.size[0]),
        "height": int(image.size[1]),
    }


def _handle_render_to_file(params: dict[str, Any]) -> dict[str, Any]:
    filepath = params.get("filepath")
    resolution_x = int(params.get("resolution_x", 1024))
    resolution_y = int(params.get("resolution_y", 768))
    file_format = params.get("file_format", "PNG")
    color_mode = params.get("color_mode", "RGB")
    color_depth = str(params.get("color_depth", "8"))
    compression = int(params.get("compression", 15))
    camera_name = params.get("camera_name")
    engine = params.get("engine")
    if not isinstance(filepath, str):
        msg = "render.to_file requires string 'filepath'"
        raise ValueError(msg)
    scene = bpy.context.scene
    if engine is not None:
        scene.render.engine = engine
    if camera_name is not None:
        cam = bpy.data.objects.get(camera_name)
        if cam is None:
            msg = f"no camera object named {camera_name!r}"
            raise KeyError(msg)
        scene.camera = cam
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = filepath
    scene.render.use_file_extension = False
    scene.render.image_settings.file_format = file_format
    scene.render.image_settings.color_mode = color_mode
    scene.render.image_settings.color_depth = color_depth
    scene.render.image_settings.compression = compression
    scene.render.resolution_x = resolution_x
    scene.render.resolution_y = resolution_y
    bpy.ops.render.render(write_still=True)
    size_bytes = Path(filepath).stat().st_size
    return {
        "path": filepath,
        "file_size_bytes": size_bytes,
        "width": resolution_x,
        "height": resolution_y,
    }


def _handle_material_build_terrain(params: dict[str, Any]) -> dict[str, Any]:
    material_name = params.get("material_name")
    target_object = params.get("target_object")
    color_ramp_stops = params.get("color_ramp_stops", [])
    slope_threshold = float(params.get("slope_threshold", 0.5))
    if not isinstance(material_name, str) or not isinstance(target_object, str):
        msg = "material.build_terrain requires string 'material_name' and 'target_object'"
        raise ValueError(msg)
    if not isinstance(color_ramp_stops, list) or not color_ramp_stops:
        msg = "material.build_terrain requires non-empty 'color_ramp_stops'"
        raise ValueError(msg)
    obj = bpy.data.objects.get(target_object)
    if obj is None:
        msg = f"no object named {target_object!r}"
        raise KeyError(msg)
    mat = bpy.data.materials.new(name=material_name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    for node in list(nodes):
        nodes.remove(node)
    output = nodes.new("ShaderNodeOutputMaterial")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    geom = nodes.new("ShaderNodeNewGeometry")
    sep = nodes.new("ShaderNodeSeparateXYZ")
    ramp = nodes.new("ShaderNodeValToRGB")
    cr = ramp.color_ramp
    while len(cr.elements) > 1:
        cr.elements.remove(cr.elements[-1])
    for index, stop in enumerate(color_ramp_stops):
        position = float(stop["position"])
        color = stop["color"]
        elem = cr.elements[0] if index == 0 else cr.elements.new(position)
        if index == 0:
            elem.position = position
        rgba_min = 4
        r, g, b = float(color[0]), float(color[1]), float(color[2])
        a = float(color[3]) if len(color) >= rgba_min else 1.0
        elem.color = (r, g, b, a)
    bsdf.inputs["Roughness"].default_value = slope_threshold
    links.new(geom.outputs["Position"], sep.inputs["Vector"])
    links.new(sep.outputs["Z"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    if obj.data is not None and hasattr(obj.data, "materials"):
        if len(obj.data.materials) > 0:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)
    return {"material_name": mat.name}


def _handle_mesh_add_displace_modifier(params: dict[str, Any]) -> dict[str, Any]:
    object_name = params.get("object_name")
    image_filepath = params.get("image_filepath")
    modifier_name = params.get("modifier_name", "forge_displace")
    texture_name = params.get("texture_name", "forge_heightmap")
    strength = float(params.get("strength", 1.0))
    midlevel = float(params.get("midlevel", 0.5))
    if not isinstance(object_name, str) or not isinstance(image_filepath, str):
        msg = "mesh.add_displace_modifier requires string 'object_name' and 'image_filepath'"
        raise ValueError(msg)
    if not Path(image_filepath).exists():
        msg = f"image file does not exist: {image_filepath!r}"
        raise FileNotFoundError(msg)
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        msg = f"no object named {object_name!r}"
        raise KeyError(msg)
    image = bpy.data.images.load(image_filepath, check_existing=True)
    texture = bpy.data.textures.new(name=texture_name, type="IMAGE")
    texture.image = image
    modifier = obj.modifiers.new(name=modifier_name, type="DISPLACE")
    modifier.texture = texture
    modifier.strength = strength
    modifier.mid_level = midlevel
    modifier.texture_coords = "UV"
    return {
        "object_name": object_name,
        "modifier_name": modifier.name,
        "texture_name": texture.name,
        "image_name": image.name,
        "strength": strength,
        "midlevel": midlevel,
    }


def _handle_object_from_data(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    data_collection = params.get("data_collection")
    data_name = params.get("data_name")
    if not isinstance(name, str) or not isinstance(data_collection, str) or not isinstance(
        data_name, str,
    ):
        msg = (
            "object.from_data requires string 'name', 'data_collection', 'data_name'"
        )
        raise ValueError(msg)
    coll = getattr(bpy.data, data_collection, None)
    if coll is None:
        msg = f"unknown bpy.data collection {data_collection!r}"
        raise KeyError(msg)
    data_block = coll.get(data_name)
    if data_block is None:
        msg = f"no datablock named {data_name!r} in bpy.data.{data_collection}"
        raise KeyError(msg)
    obj = bpy.data.objects.new(name=name, object_data=data_block)
    bpy.context.scene.collection.objects.link(obj)
    return {"object_name": obj.name, "data_name": data_block.name}


def _handle_scene_assign_world(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    if not isinstance(name, str):
        msg = "scene.assign_world requires string 'name'"
        raise ValueError(msg)
    world = bpy.data.worlds.get(name)
    if world is None:
        msg = f"no world named {name!r}"
        raise KeyError(msg)
    bpy.context.scene.world = world
    return {"world_name": world.name}


def _handle_scene_diff(_params: dict[str, Any]) -> dict[str, Any]:
    return {
        "objects": len(bpy.data.objects),
        "meshes": len(bpy.data.meshes),
        "materials": len(bpy.data.materials),
        "images": len(bpy.data.images),
        "lights": len(bpy.data.lights),
        "cameras": len(bpy.data.cameras),
        "curves": len(bpy.data.curves),
        "worlds": len(bpy.data.worlds),
        "textures": len(bpy.data.textures),
    }


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
    if method == "mesh.from_pydata":
        return _handle_mesh_from_pydata(params)
    if method == "mesh.add_displace_modifier":
        return _handle_mesh_add_displace_modifier(params)
    if method == "image.from_file":
        return _handle_image_from_file(params)
    if method == "render.to_file":
        return _handle_render_to_file(params)
    if method == "material.build_terrain":
        return _handle_material_build_terrain(params)
    if method == "object.from_data":
        return _handle_object_from_data(params)
    if method == "scene.assign_world":
        return _handle_scene_assign_world(params)
    if method == "scene.diff":
        return _handle_scene_diff(params)
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
    except (ValueError, KeyError, TypeError, FileNotFoundError) as exc:
        return _make_response(req_id, None, {"code": ERR_INVALID_PARAMS, "message": str(exc)}), False
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        return _make_response(req_id, None, {"code": ERR_BLENDER, "message": str(exc)}), False
    return _make_response(req_id, result, None), False


def main() -> int:
    # Blender's renderer (and any C-level Blender chatter) writes to fd 1
    # (stdout) directly, which would corrupt the JSON-RPC framing. Save a
    # duplicate of fd 1 for our own response stream and redirect fd 1 to
    # fd 2 so all foreign chatter ends up on stderr.
    rpc_fd = os.dup(1)
    os.dup2(2, 1)
    rpc_out = os.fdopen(rpc_fd, "w", buffering=1)
    print(f"adapter: blender={_bpy_version()} ready", file=sys.stderr)
    sys.stderr.flush()
    for raw in sys.stdin:
        response, done = _process_line(raw)
        if response is not None:
            rpc_out.write(response + "\n")
            rpc_out.flush()
        if done:
            break
    print("adapter: shutdown", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
