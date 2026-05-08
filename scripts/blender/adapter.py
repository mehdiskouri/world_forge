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
* ``material.build_composite``      — build a composite material from a
  resolved CompositeMaterialPlan (one or more layered shader recipes
  combined via MixShader nodes driven by per-layer mask specs) and
  assign it to a mesh object (Phase 6-bis)
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
    return {
        "alive": True,
        "blender": _bpy_version(),
        "render_devices": _probe_render_devices(),
    }


_CYCLES_DEVICE_TYPES: tuple[str, ...] = ("OPTIX", "CUDA", "HIP", "METAL", "CPU")


def _probe_render_devices() -> dict[str, Any]:
    """Return the available Cycles compute devices and a default hint.

    ``available`` is the union of devices Blender's Cycles add-on
    reports for each backend. ``default_device_type`` is the highest-
    priority non-CPU backend that has at least one device, falling
    back to ``"CPU"`` so the field is always populated. The probe is
    side-effect-free apart from importing the Cycles add-on (Blender
    bundles it; the import succeeds on every host).
    """
    available: list[dict[str, Any]] = []
    default_type = "CPU"
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except KeyError:
        return {"available": available, "default_device_type": default_type}
    for device_type in _CYCLES_DEVICE_TYPES:
        try:
            devices = prefs.get_devices_for_type(device_type)
        except (RuntimeError, TypeError):
            continue
        for device in devices or ():
            available.append(
                {
                    "type": device_type,
                    "name": str(getattr(device, "name", "")),
                },
            )
    for device in available:
        if device["type"] != "CPU":
            default_type = device["type"]
            break
    return {"available": available, "default_device_type": default_type}


def _handle_render_set_engine_device(params: dict[str, Any]) -> dict[str, Any]:
    """Configure ``scene.render.engine`` plus the Cycles device selection.

    Idempotent. ``device_type`` is one of
    ``OPTIX|CUDA|HIP|METAL|CPU``. For ``BLENDER_EEVEE`` the device
    selection is ignored (the engine has no compute-device concept).
    For ``CYCLES`` the function flips the Cycles add-on's
    ``compute_device_type``, enables every device matching that type
    plus the host CPU, disables the rest, and sets
    ``scene.cycles.device`` to ``GPU`` (or ``CPU`` for the CPU-only
    case). When ``device_type == "CPU"`` only the CPU is enabled and
    ``scene.cycles.device`` becomes ``CPU``.

    Optional ``samples`` (Cycles only) sets ``scene.cycles.samples``;
    when supplied with ``use_adaptive_sampling=False`` (the default)
    adaptive sampling is also turned off so two renders at the same
    sample count produce identical IDAT digests.
    """
    engine = params.get("engine")
    device_type = params.get("device_type", "CPU")
    samples = params.get("samples")
    if not isinstance(engine, str):
        msg = "render.set_engine_device requires string 'engine'"
        raise ValueError(msg)
    if not isinstance(device_type, str):
        msg = "render.set_engine_device requires string 'device_type'"
        raise ValueError(msg)
    scene = bpy.context.scene
    scene.render.engine = engine
    resolved_samples = 0
    if engine == "CYCLES":
        cycles_prefs = bpy.context.preferences.addons["cycles"].preferences
        cycles_prefs.compute_device_type = device_type if device_type != "CPU" else "NONE"
        for device in cycles_prefs.devices:
            if device_type == "CPU":
                device.use = device.type == "CPU"
            else:
                device.use = device.type in (device_type, "CPU")
        scene.cycles.device = "CPU" if device_type == "CPU" else "GPU"
        if samples is not None:
            scene.cycles.samples = int(samples)
            scene.cycles.use_adaptive_sampling = False
        resolved_samples = int(scene.cycles.samples)
    return {
        "engine": engine,
        "device_type": device_type,
        "samples": resolved_samples,
    }


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


def _build_principled_height_ramp(  # noqa: C901, PLR0915 - shader graph assembly is linear
    nodes: Any,  # noqa: ANN401 - bpy node tree is dynamically typed
    links: Any,  # noqa: ANN401
    parameters: dict[str, Any],
) -> Any:  # noqa: ANN401
    """Build the elevation-driven Principled BSDF; return its surface output socket."""
    color_ramp_stops = parameters.get("color_ramp_stops") or []
    slope_threshold = float(parameters.get("slope_threshold", 0.5))
    elevation_min = float(parameters.get("elevation_min", 0.0))
    elevation_max = float(parameters.get("elevation_max", 1.0))
    elevation_span = elevation_max - elevation_min
    if elevation_span <= 0.0:
        elevation_span = 1.0
    if not isinstance(color_ramp_stops, list) or not color_ramp_stops:
        msg = "principled_height_ramp recipe requires non-empty color_ramp_stops"
        raise ValueError(msg)
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
    sub = nodes.new("ShaderNodeMath")
    sub.operation = "SUBTRACT"
    sub.inputs[1].default_value = elevation_min
    div = nodes.new("ShaderNodeMath")
    div.operation = "DIVIDE"
    div.inputs[1].default_value = elevation_span
    div.use_clamp = True
    links.new(geom.outputs["Position"], sep.inputs["Vector"])
    links.new(sep.outputs["Z"], sub.inputs[0])
    links.new(sub.outputs["Value"], div.inputs[0])
    links.new(div.outputs["Value"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    return bsdf.outputs["BSDF"]


def _build_triplanar_rock(
    nodes: Any,  # noqa: ANN401
    links: Any,  # noqa: ANN401
    parameters: dict[str, Any],
) -> Any:  # noqa: ANN401
    """Build a basic triplanar-projection Principled BSDF."""
    base_color = parameters.get("base_color") or [0.5, 0.5, 0.5, 1.0]
    roughness = float(parameters.get("roughness", 0.7))
    scale = float(parameters.get("scale_meters", 1.0))
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    rgba_min = 4
    r, g, b = float(base_color[0]), float(base_color[1]), float(base_color[2])
    a = float(base_color[3]) if len(base_color) >= rgba_min else 1.0
    bsdf.inputs["Base Color"].default_value = (r, g, b, a)
    bsdf.inputs["Roughness"].default_value = roughness
    # Light triplanar wiring: feed world-space position (scaled) into a
    # Voronoi for surface variation. Kept intentionally simple in v1; a
    # real triplanar would split per-axis projections.
    geom = nodes.new("ShaderNodeNewGeometry")
    mul = nodes.new("ShaderNodeVectorMath")
    mul.operation = "SCALE"
    mul.inputs["Scale"].default_value = scale
    voronoi = nodes.new("ShaderNodeTexVoronoi")
    mix = nodes.new("ShaderNodeMixRGB")
    mix.inputs["Fac"].default_value = 0.15
    mix.inputs["Color1"].default_value = (r, g, b, a)
    links.new(geom.outputs["Position"], mul.inputs["Vector"])
    links.new(mul.outputs["Vector"], voronoi.inputs["Vector"])
    links.new(voronoi.outputs["Color"], mix.inputs["Color2"])
    links.new(mix.outputs["Color"], bsdf.inputs["Base Color"])
    return bsdf.outputs["BSDF"]


def _build_flat_color(
    nodes: Any,  # noqa: ANN401
    _links: Any,  # noqa: ANN401
    parameters: dict[str, Any],
) -> Any:  # noqa: ANN401
    """Build a minimal solid-color Principled BSDF."""
    color = parameters.get("color") or [0.5, 0.5, 0.5, 1.0]
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    rgba_min = 4
    r, g, b = float(color[0]), float(color[1]), float(color[2])
    a = float(color[3]) if len(color) >= rgba_min else 1.0
    bsdf.inputs["Base Color"].default_value = (r, g, b, a)
    return bsdf.outputs["BSDF"]


def _build_pbr_layered(  # noqa: C901, PLR0912, PLR0915 - shader graph assembly is linear
    nodes: Any,  # noqa: ANN401 - bpy node tree is dynamically typed
    links: Any,  # noqa: ANN401
    parameters: dict[str, Any],
) -> Any:  # noqa: ANN401
    """Build a generalised procedural Principled BSDF (Phase 6-e Stage B).

    All optional knobs default to ``0`` / off so a minimal
    ``{"base_color": [r, g, b, a]}`` parameter dict produces a graph
    that is *behaviourally* equivalent to ``flat_color`` (single
    Principled BSDF with the given Base Color), only with the extra
    Position-driven scaling node attached. See
    :func:`forge_mcp.realize.material.defaults._validate_pbr_layered`
    for the parameter contract.
    """
    base_color = parameters.get("base_color") or [0.5, 0.5, 0.5, 1.0]
    base_color_variation = float(parameters.get("base_color_variation", 0.0))
    roughness = float(parameters.get("roughness", 0.5))
    roughness_variation = float(parameters.get("roughness_variation", 0.0))
    normal_detail = float(parameters.get("normal_detail", 0.0))
    metallic = float(parameters.get("metallic", 0.0))
    clearcoat = float(parameters.get("clearcoat", 0.0))
    scale = float(parameters.get("triplanar_scale_m", 1.0))

    rgba_min = 4
    r, g, b = float(base_color[0]), float(base_color[1]), float(base_color[2])
    a = float(base_color[3]) if len(base_color) >= rgba_min else 1.0

    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    if "Coat Weight" in bsdf.inputs:
        bsdf.inputs["Coat Weight"].default_value = clearcoat
    elif "Clearcoat" in bsdf.inputs:  # pragma: no cover - older Blender naming
        bsdf.inputs["Clearcoat"].default_value = clearcoat

    # Procedural inputs all share the same world-space position, scaled
    # so a single ``triplanar_scale_m`` knob controls every octave's
    # frequency uniformly.
    geom = nodes.new("ShaderNodeNewGeometry")
    scaled_pos = nodes.new("ShaderNodeVectorMath")
    scaled_pos.operation = "SCALE"
    scaled_pos.inputs["Scale"].default_value = scale
    links.new(geom.outputs["Position"], scaled_pos.inputs["Vector"])

    # Base color: optionally mix the configured RGBA with a Voronoi
    # color (offset by 50 % luminance toward the Voronoi color).
    if base_color_variation > 0.0:
        voronoi = nodes.new("ShaderNodeTexVoronoi")
        mix = nodes.new("ShaderNodeMixRGB")
        mix.inputs["Fac"].default_value = base_color_variation
        mix.inputs["Color1"].default_value = (r, g, b, a)
        links.new(scaled_pos.outputs["Vector"], voronoi.inputs["Vector"])
        links.new(voronoi.outputs["Color"], mix.inputs["Color2"])
        links.new(mix.outputs["Color"], bsdf.inputs["Base Color"])
    else:
        bsdf.inputs["Base Color"].default_value = (r, g, b, a)

    # Roughness: optionally add Noise-driven variation around the base
    # ``roughness`` value. Mix.Color1 = constant base, Color2 = noise.
    if roughness_variation > 0.0:
        noise = nodes.new("ShaderNodeTexNoise")
        rmix = nodes.new("ShaderNodeMixRGB")
        rmix.inputs["Fac"].default_value = roughness_variation
        rmix.inputs["Color1"].default_value = (roughness, roughness, roughness, 1.0)
        links.new(scaled_pos.outputs["Vector"], noise.inputs["Vector"])
        links.new(noise.outputs["Fac"], rmix.inputs["Color2"])
        links.new(rmix.outputs["Color"], bsdf.inputs["Roughness"])

    # Normal detail: drive a Bump node from Noise, feed into Principled
    # BSDF Normal input.
    if normal_detail > 0.0:
        nnoise = nodes.new("ShaderNodeTexNoise")
        bump = nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = normal_detail
        links.new(scaled_pos.outputs["Vector"], nnoise.inputs["Vector"])
        links.new(nnoise.outputs["Fac"], bump.inputs["Height"])
        links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    return bsdf.outputs["BSDF"]


def _build_procedural_snow(
    nodes: Any,  # noqa: ANN401
    links: Any,  # noqa: ANN401
    parameters: dict[str, Any],
) -> Any:  # noqa: ANN401
    """Phase 6-e Stage C: powdery snow surface (no volume; surface-only v1)."""
    base_color = parameters.get("base_color") or [0.95, 0.96, 0.98, 1.0]
    sparkle_density = float(parameters.get("sparkle_density", 0.3))
    sparkle_scale = float(parameters.get("sparkle_scale_m", 0.05))
    drift_strength = float(parameters.get("drift_strength", 0.15))
    drift_scale = float(parameters.get("drift_scale_m", 4.0))
    subsurface_weight = float(parameters.get("subsurface_weight", 0.4))

    rgba_min = 4
    r, g, b = float(base_color[0]), float(base_color[1]), float(base_color[2])
    a = float(base_color[3]) if len(base_color) >= rgba_min else 1.0

    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (r, g, b, a)
    bsdf.inputs["Roughness"].default_value = 0.15
    if "Subsurface Weight" in bsdf.inputs:
        bsdf.inputs["Subsurface Weight"].default_value = subsurface_weight
    elif "Subsurface" in bsdf.inputs:  # pragma: no cover - older Blender naming
        bsdf.inputs["Subsurface"].default_value = subsurface_weight

    geom = nodes.new("ShaderNodeNewGeometry")

    if sparkle_density > 0.0:
        sparkle_pos = nodes.new("ShaderNodeVectorMath")
        sparkle_pos.operation = "SCALE"
        sparkle_pos.inputs["Scale"].default_value = 1.0 / max(sparkle_scale, 1e-4)
        voronoi = nodes.new("ShaderNodeTexVoronoi")
        threshold = nodes.new("ShaderNodeMath")
        threshold.operation = "GREATER_THAN"
        threshold.inputs[1].default_value = 1.0 - sparkle_density
        links.new(geom.outputs["Position"], sparkle_pos.inputs["Vector"])
        links.new(sparkle_pos.outputs["Vector"], voronoi.inputs["Vector"])
        links.new(voronoi.outputs["Distance"], threshold.inputs[0])
        if "Specular IOR Level" in bsdf.inputs:
            links.new(threshold.outputs["Value"], bsdf.inputs["Specular IOR Level"])
        elif "Specular" in bsdf.inputs:  # pragma: no cover - older naming
            links.new(threshold.outputs["Value"], bsdf.inputs["Specular"])

    if drift_strength > 0.0:
        drift_pos = nodes.new("ShaderNodeVectorMath")
        drift_pos.operation = "SCALE"
        drift_pos.inputs["Scale"].default_value = 1.0 / max(drift_scale, 1e-4)
        noise = nodes.new("ShaderNodeTexNoise")
        bump = nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = drift_strength
        links.new(geom.outputs["Position"], drift_pos.inputs["Vector"])
        links.new(drift_pos.outputs["Vector"], noise.inputs["Vector"])
        links.new(noise.outputs["Fac"], bump.inputs["Height"])
        links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    return bsdf.outputs["BSDF"]


def _build_procedural_sand(  # noqa: C901, PLR0912, PLR0915 - shader graph assembly is linear
    nodes: Any,  # noqa: ANN401
    links: Any,  # noqa: ANN401
    parameters: dict[str, Any],
) -> Any:  # noqa: ANN401
    """Phase 6-e Stage C: warm-tan sand surface with grain + ripples + wet band."""
    base_color = parameters.get("base_color") or [0.78, 0.66, 0.45, 1.0]
    grain_amount = float(parameters.get("grain_amount", 0.25))
    grain_scale = float(parameters.get("grain_scale_m", 0.5))
    ripple_strength = float(parameters.get("ripple_strength", 0.15))
    ripple_scale = float(parameters.get("ripple_scale_m", 2.0))
    wet_band = parameters.get("wet_band")

    rgba_min = 4
    r, g, b = float(base_color[0]), float(base_color[1]), float(base_color[2])
    a = float(base_color[3]) if len(base_color) >= rgba_min else 1.0

    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 0.85

    geom = nodes.new("ShaderNodeNewGeometry")

    color_socket: Any = None
    if grain_amount > 0.0:
        grain_pos = nodes.new("ShaderNodeVectorMath")
        grain_pos.operation = "SCALE"
        grain_pos.inputs["Scale"].default_value = 1.0 / max(grain_scale, 1e-4)
        v1 = nodes.new("ShaderNodeTexVoronoi")
        v2_pos = nodes.new("ShaderNodeVectorMath")
        v2_pos.operation = "SCALE"
        v2_pos.inputs["Scale"].default_value = 4.0
        v2 = nodes.new("ShaderNodeTexVoronoi")
        mix1 = nodes.new("ShaderNodeMixRGB")
        mix1.inputs["Fac"].default_value = grain_amount
        mix1.inputs["Color1"].default_value = (r, g, b, a)
        mix2 = nodes.new("ShaderNodeMixRGB")
        mix2.inputs["Fac"].default_value = grain_amount * 0.5
        links.new(geom.outputs["Position"], grain_pos.inputs["Vector"])
        links.new(grain_pos.outputs["Vector"], v1.inputs["Vector"])
        links.new(grain_pos.outputs["Vector"], v2_pos.inputs["Vector"])
        links.new(v2_pos.outputs["Vector"], v2.inputs["Vector"])
        links.new(v1.outputs["Color"], mix1.inputs["Color2"])
        links.new(mix1.outputs["Color"], mix2.inputs["Color1"])
        links.new(v2.outputs["Color"], mix2.inputs["Color2"])
        color_socket = mix2.outputs["Color"]
    else:
        bsdf.inputs["Base Color"].default_value = (r, g, b, a)

    if wet_band is not None:
        low_m = float(wet_band["low_m"])
        high_m = float(wet_band["high_m"])
        darken = float(wet_band["darken"])
        wet_sep = nodes.new("ShaderNodeSeparateXYZ")
        wet_smooth = nodes.new("ShaderNodeMath")
        wet_smooth.operation = "SMOOTHSTEP"
        wet_smooth.inputs[1].default_value = high_m
        wet_smooth.inputs[2].default_value = low_m
        wet_invert = nodes.new("ShaderNodeMath")
        wet_invert.operation = "SUBTRACT"
        wet_invert.inputs[0].default_value = 1.0
        wet_scale = nodes.new("ShaderNodeMath")
        wet_scale.operation = "MULTIPLY"
        wet_scale.inputs[1].default_value = darken
        wet_mix = nodes.new("ShaderNodeMixRGB")
        wet_mix.inputs["Color2"].default_value = (
            r * (1.0 - darken),
            g * (1.0 - darken),
            b * (1.0 - darken),
            a,
        )
        links.new(geom.outputs["Position"], wet_sep.inputs["Vector"])
        links.new(wet_sep.outputs["Z"], wet_smooth.inputs[0])
        links.new(wet_smooth.outputs["Value"], wet_invert.inputs[1])
        links.new(wet_invert.outputs["Value"], wet_scale.inputs[0])
        links.new(wet_scale.outputs["Value"], wet_mix.inputs["Fac"])
        if color_socket is not None:
            links.new(color_socket, wet_mix.inputs["Color1"])
        else:
            wet_mix.inputs["Color1"].default_value = (r, g, b, a)
        color_socket = wet_mix.outputs["Color"]

    if color_socket is not None:
        links.new(color_socket, bsdf.inputs["Base Color"])

    if ripple_strength > 0.0:
        ripple_pos = nodes.new("ShaderNodeVectorMath")
        ripple_pos.operation = "SCALE"
        ripple_pos.inputs["Scale"].default_value = 1.0 / max(ripple_scale, 1e-4)
        wave = nodes.new("ShaderNodeTexWave")
        bump = nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = ripple_strength
        links.new(geom.outputs["Position"], ripple_pos.inputs["Vector"])
        links.new(ripple_pos.outputs["Vector"], wave.inputs["Vector"])
        links.new(wave.outputs["Fac"], bump.inputs["Height"])
        links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    return bsdf.outputs["BSDF"]


def _build_procedural_water(
    nodes: Any,  # noqa: ANN401
    links: Any,  # noqa: ANN401
    parameters: dict[str, Any],
) -> Any:  # noqa: ANN401
    """Phase 6-e Stage C: transmission-dominant water surface (no volume; v1)."""
    base_color = parameters.get("base_color") or [0.05, 0.20, 0.35, 1.0]
    ior = float(parameters.get("ior", 1.33))
    roughness = float(parameters.get("roughness", 0.0))
    wave_strength = float(parameters.get("wave_strength", 0.2))
    wave_scale = float(parameters.get("wave_scale_m", 1.5))
    transmission = float(parameters.get("transmission", 1.0))

    rgba_min = 4
    r, g, b = float(base_color[0]), float(base_color[1]), float(base_color[2])
    a = float(base_color[3]) if len(base_color) >= rgba_min else 1.0

    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (r, g, b, a)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["IOR"].default_value = ior
    if "Transmission Weight" in bsdf.inputs:
        bsdf.inputs["Transmission Weight"].default_value = transmission
    elif "Transmission" in bsdf.inputs:  # pragma: no cover - older naming
        bsdf.inputs["Transmission"].default_value = transmission

    if wave_strength > 0.0:
        geom = nodes.new("ShaderNodeNewGeometry")
        wave_pos = nodes.new("ShaderNodeVectorMath")
        wave_pos.operation = "SCALE"
        wave_pos.inputs["Scale"].default_value = 1.0 / max(wave_scale, 1e-4)
        v_swell = nodes.new("ShaderNodeTexVoronoi")
        n_chop = nodes.new("ShaderNodeTexNoise")
        chop_pos = nodes.new("ShaderNodeVectorMath")
        chop_pos.operation = "SCALE"
        chop_pos.inputs["Scale"].default_value = 4.0
        add = nodes.new("ShaderNodeMath")
        add.operation = "ADD"
        bump = nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = wave_strength
        links.new(geom.outputs["Position"], wave_pos.inputs["Vector"])
        links.new(wave_pos.outputs["Vector"], v_swell.inputs["Vector"])
        links.new(wave_pos.outputs["Vector"], chop_pos.inputs["Vector"])
        links.new(chop_pos.outputs["Vector"], n_chop.inputs["Vector"])
        links.new(v_swell.outputs["Distance"], add.inputs[0])
        links.new(n_chop.outputs["Fac"], add.inputs[1])
        links.new(add.outputs["Value"], bump.inputs["Height"])
        links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    return bsdf.outputs["BSDF"]


_RECIPE_BUILDERS = {
    "principled_height_ramp": _build_principled_height_ramp,
    "triplanar_rock": _build_triplanar_rock,
    "flat_color": _build_flat_color,
    "pbr_layered": _build_pbr_layered,
    "procedural_snow": _build_procedural_snow,
    "procedural_sand": _build_procedural_sand,
    "procedural_water": _build_procedural_water,
}


def _build_mask_factor(
    nodes: Any,  # noqa: ANN401
    links: Any,  # noqa: ANN401
    mask: dict[str, Any] | None,
    weight: float | None,
    predicate_mask: dict[str, Any] | None = None,
) -> Any:  # noqa: ANN401
    """Return a node-graph value socket suitable for a MixShader Fac.

    ``None`` mask + ``None`` weight → constant ``1.0`` (full
    replacement; any caller-supplied predicate becomes the sole gate).
    A height-ramp mask blends along world-space Z. A constant mask
    emits its configured value. Weight further scales the resulting
    factor.

    Phase 6-c: when ``predicate_mask`` is provided (sub-region scoped
    application), the predicate's per-pixel boolean is built as a 0/1
    step factor and multiplied with the application's own mask factor
    so the predicate gates the layer's region of effect while the mask
    modulates within it.
    """
    base_factor = _build_base_mask_factor(nodes, links, mask, weight)
    if predicate_mask is None:
        return base_factor
    predicate_factor = _build_predicate_factor(nodes, links, predicate_mask)
    multiply = nodes.new("ShaderNodeMath")
    multiply.operation = "MULTIPLY"
    multiply.use_clamp = True
    links.new(base_factor, multiply.inputs[0])
    links.new(predicate_factor, multiply.inputs[1])
    return multiply.outputs["Value"]


def _build_base_mask_factor(
    nodes: Any,  # noqa: ANN401
    links: Any,  # noqa: ANN401
    mask: dict[str, Any] | None,
    weight: float | None,
) -> Any:  # noqa: ANN401
    """Return the application's own mask factor (no predicate gating).

    With no explicit ``mask`` and no explicit ``weight`` the factor is
    constant ``1.0`` so that any predicate gate from the caller is the
    *sole* selector (predicate=1 ``\u2192`` Fac=1 ``\u2192`` full replacement
    inside the band). Earlier revisions defaulted to ``0.5`` which was
    a holdover from the pre-Phase-6-c "two materials, blend evenly"
    case; combined with a predicate factor of 0/1 it caused
    sub-region-scoped layers without an explicit mask or weight to
    composite at half strength inside their predicate band, leaking
    the underlying region material through at equal intensity. See
    [`AGENT/dev_phases/phase6_c_subregion.md`](../../AGENT/dev_phases/phase6_c_subregion.md)
    "Hotfix follow-up" for the diagnosis.
    """
    weight_value = 1.0 if weight is None else float(weight)
    if mask is None:
        const = nodes.new("ShaderNodeValue")
        const.outputs[0].default_value = weight_value
        return const.outputs[0]
    kind = mask.get("kind")
    if kind == "constant":
        const = nodes.new("ShaderNodeValue")
        const.outputs[0].default_value = float(mask.get("value", 0.5)) * weight_value
        return const.outputs[0]
    if kind == "height_ramp":
        low = float(mask.get("low_m", 0.0))
        high = float(mask.get("high_m", 1.0))
        span = high - low if high > low else 1.0
        geom = nodes.new("ShaderNodeNewGeometry")
        sep = nodes.new("ShaderNodeSeparateXYZ")
        sub = nodes.new("ShaderNodeMath")
        sub.operation = "SUBTRACT"
        sub.inputs[1].default_value = low
        div = nodes.new("ShaderNodeMath")
        div.operation = "DIVIDE"
        div.inputs[1].default_value = span
        div.use_clamp = True
        scale = nodes.new("ShaderNodeMath")
        scale.operation = "MULTIPLY"
        scale.inputs[1].default_value = weight_value
        scale.use_clamp = True
        links.new(geom.outputs["Position"], sep.inputs["Vector"])
        links.new(sep.outputs["Z"], sub.inputs[0])
        links.new(sub.outputs["Value"], div.inputs[0])
        links.new(div.outputs["Value"], scale.inputs[0])
        return scale.outputs["Value"]
    if kind == "slope":
        return _build_slope_mask_factor(
            nodes,
            links,
            float(mask.get("low", 0.0)),
            float(mask.get("high", 1.0)),
            float(mask.get("softness", 0.0)),
            weight_value,
        )
    # Unknown mask kind: fall back to constant weight + warn so future
    # schema additions cannot silently no-op the way the slope mask did
    # before Phase 6-e Stage A.
    sys.stderr.write(
        f"forge: unknown mask kind {kind!r}; falling back to constant weight\n",
    )
    const = nodes.new("ShaderNodeValue")
    const.outputs[0].default_value = weight_value
    return const.outputs[0]


def _build_slope_mask_factor(  # noqa: PLR0913 - five mask-domain inputs + weight
    nodes: Any,  # noqa: ANN401 - bpy node tree is dynamically typed
    links: Any,  # noqa: ANN401
    low: float,
    high: float,
    softness: float,
    weight: float,
) -> Any:  # noqa: ANN401
    """Return a ``Fac`` socket for a :class:`SlopeMask` mix factor.

    Domain matches the Pydantic model: ``low``/``high`` are
    cosine-of-normal thresholds in ``[0, 1]`` where ``1`` is flat
    ground and ``0`` is a vertical cliff. The factor smoothsteps from
    ``0`` at ``low - softness`` to ``1`` at ``high + softness`` so the
    mask transitions cleanly through the band, then scales by the
    application's ``weight``.
    """
    geom = nodes.new("ShaderNodeNewGeometry")
    sep = nodes.new("ShaderNodeSeparateXYZ")
    abs_z = nodes.new("ShaderNodeMath")
    abs_z.operation = "ABSOLUTE"
    smooth = nodes.new("ShaderNodeMath")
    smooth.operation = "SMOOTHSTEP"
    smooth.inputs[1].default_value = max(0.0, low - softness)
    smooth.inputs[2].default_value = min(1.0, high + softness)
    scale = nodes.new("ShaderNodeMath")
    scale.operation = "MULTIPLY"
    scale.inputs[1].default_value = weight
    scale.use_clamp = True
    links.new(geom.outputs["Normal"], sep.inputs["Vector"])
    links.new(sep.outputs["Z"], abs_z.inputs[0])
    links.new(abs_z.outputs["Value"], smooth.inputs[0])
    links.new(smooth.outputs["Value"], scale.inputs[0])
    return scale.outputs["Value"]


def _build_predicate_factor(
    nodes: Any,  # noqa: ANN401
    links: Any,  # noqa: ANN401
    predicate_mask: dict[str, Any],
) -> Any:  # noqa: ANN401
    """Return a 0-or-1 step factor encoding the sub-region predicate.

    Builds a small per-predicate-kind shader-node graph:

    * ``height_band``: world-space Z compared against ``[low_m, high_m)``;
    * ``slope``: surface-normal Z → arccos → degrees, compared against
      ``[min_deg, max_deg)``;
    * ``aspect``: surface-normal X/Y → arctan2 → compass degrees,
      compared against ``[min_deg, max_deg)`` with wrap-through-north
      when ``min_deg > max_deg``;
    * ``distance_to_stream``: not realisable in shader-only Phase 6-c
      (needs a baked vertex attribute or texture); falls back to an
      always-on factor with a stderr warning so the rest of the
      composite still renders.
    """
    predicate = predicate_mask.get("predicate")
    if not isinstance(predicate, dict):
        return _build_constant_factor(nodes, 1.0)
    kind = predicate.get("kind")
    if kind == "height_band":
        return _build_height_band_factor(
            nodes,
            links,
            float(predicate.get("low_m", 0.0)),
            float(predicate.get("high_m", 1.0)),
        )
    if kind == "slope":
        return _build_slope_factor(
            nodes,
            links,
            float(predicate.get("min_deg", 0.0)),
            float(predicate.get("max_deg", 90.0)),
        )
    if kind == "aspect":
        return _build_aspect_factor(
            nodes,
            links,
            float(predicate.get("min_deg", 0.0)),
            float(predicate.get("max_deg", 360.0)),
        )
    if kind == "distance_to_stream":
        sys.stderr.write(
            "forge: distance_to_stream predicate is not realisable in shader nodes; "
            "falling back to always-on factor\n",
        )
        return _build_constant_factor(nodes, 1.0)
    return _build_constant_factor(nodes, 1.0)


def _build_constant_factor(nodes: Any, value: float) -> Any:  # noqa: ANN401
    const = nodes.new("ShaderNodeValue")
    const.outputs[0].default_value = value
    return const.outputs[0]


def _build_in_band_factor(
    nodes: Any,  # noqa: ANN401
    links: Any,  # noqa: ANN401
    value_socket: Any,  # noqa: ANN401
    low: float,
    high: float,
) -> Any:  # noqa: ANN401
    """Return ``1.0`` when ``low <= value < high`` else ``0.0``."""
    ge_low = nodes.new("ShaderNodeMath")
    ge_low.operation = "GREATER_THAN"
    ge_low.inputs[1].default_value = low - 1e-6
    lt_high = nodes.new("ShaderNodeMath")
    lt_high.operation = "LESS_THAN"
    lt_high.inputs[1].default_value = high
    multiply = nodes.new("ShaderNodeMath")
    multiply.operation = "MULTIPLY"
    multiply.use_clamp = True
    links.new(value_socket, ge_low.inputs[0])
    links.new(value_socket, lt_high.inputs[0])
    links.new(ge_low.outputs["Value"], multiply.inputs[0])
    links.new(lt_high.outputs["Value"], multiply.inputs[1])
    return multiply.outputs["Value"]


def _build_height_band_factor(
    nodes: Any,  # noqa: ANN401
    links: Any,  # noqa: ANN401
    low_m: float,
    high_m: float,
) -> Any:  # noqa: ANN401
    geom = nodes.new("ShaderNodeNewGeometry")
    sep = nodes.new("ShaderNodeSeparateXYZ")
    links.new(geom.outputs["Position"], sep.inputs["Vector"])
    return _build_in_band_factor(nodes, links, sep.outputs["Z"], low_m, high_m)


def _build_slope_factor(
    nodes: Any,  # noqa: ANN401
    links: Any,  # noqa: ANN401
    min_deg: float,
    max_deg: float,
) -> Any:  # noqa: ANN401
    """Build a slope-degrees value socket and gate it on ``[min_deg, max_deg)``."""
    geom = nodes.new("ShaderNodeNewGeometry")
    sep = nodes.new("ShaderNodeSeparateXYZ")
    links.new(geom.outputs["Normal"], sep.inputs["Vector"])
    # slope = arccos(|normal.z|) in radians; clamp to [-1, 1] to be safe.
    abs_z = nodes.new("ShaderNodeMath")
    abs_z.operation = "ABSOLUTE"
    links.new(sep.outputs["Z"], abs_z.inputs[0])
    arccos = nodes.new("ShaderNodeMath")
    arccos.operation = "ARCCOSINE"
    links.new(abs_z.outputs["Value"], arccos.inputs[0])
    # radians -> degrees.
    to_deg = nodes.new("ShaderNodeMath")
    to_deg.operation = "MULTIPLY"
    to_deg.inputs[1].default_value = 180.0 / 3.141592653589793
    links.new(arccos.outputs["Value"], to_deg.inputs[0])
    return _build_in_band_factor(nodes, links, to_deg.outputs["Value"], min_deg, max_deg)


def _build_aspect_factor(
    nodes: Any,  # noqa: ANN401
    links: Any,  # noqa: ANN401
    min_deg: float,
    max_deg: float,
) -> Any:  # noqa: ANN401
    """Build an aspect-degrees value socket and gate it on ``[min_deg, max_deg)``.

    When ``min_deg > max_deg`` the band wraps through north; we OR the
    two half-bands ``[min, 360)`` and ``[0, max)`` by adding their two
    in-band factors and clamping back to ``[0, 1]``.
    """
    aspect_socket = _build_aspect_value_socket(nodes, links)
    if min_deg < max_deg:
        return _build_in_band_factor(nodes, links, aspect_socket, min_deg, max_deg)
    upper = _build_in_band_factor(nodes, links, aspect_socket, min_deg, 360.0)
    lower = _build_in_band_factor(nodes, links, aspect_socket, 0.0, max_deg)
    add = nodes.new("ShaderNodeMath")
    add.operation = "ADD"
    add.use_clamp = True
    links.new(upper, add.inputs[0])
    links.new(lower, add.inputs[1])
    return add.outputs["Value"]


def _build_aspect_value_socket(
    nodes: Any,  # noqa: ANN401
    links: Any,  # noqa: ANN401
) -> Any:  # noqa: ANN401
    """Compute aspect (compass bearing of downhill, ``[0, 360)``) as a value socket.

    Mirrors ``forge_mcp.analyze.terrain_analysis._slope_and_aspect``:
    aspect = ``atan2(-normal.x, -normal.y)`` in radians, converted to
    degrees and wrapped to ``[0, 360)``.
    """
    geom = nodes.new("ShaderNodeNewGeometry")
    sep = nodes.new("ShaderNodeSeparateXYZ")
    links.new(geom.outputs["Normal"], sep.inputs["Vector"])
    neg_x = nodes.new("ShaderNodeMath")
    neg_x.operation = "MULTIPLY"
    neg_x.inputs[1].default_value = -1.0
    links.new(sep.outputs["X"], neg_x.inputs[0])
    neg_y = nodes.new("ShaderNodeMath")
    neg_y.operation = "MULTIPLY"
    neg_y.inputs[1].default_value = -1.0
    links.new(sep.outputs["Y"], neg_y.inputs[0])
    atan2 = nodes.new("ShaderNodeMath")
    atan2.operation = "ARCTAN2"
    links.new(neg_x.outputs["Value"], atan2.inputs[0])
    links.new(neg_y.outputs["Value"], atan2.inputs[1])
    to_deg = nodes.new("ShaderNodeMath")
    to_deg.operation = "MULTIPLY"
    to_deg.inputs[1].default_value = 180.0 / 3.141592653589793
    links.new(atan2.outputs["Value"], to_deg.inputs[0])
    # wrap to [0, 360): degrees mod 360.
    wrap = nodes.new("ShaderNodeMath")
    wrap.operation = "MODULO"
    wrap.inputs[1].default_value = 360.0
    # Add 360 first to handle negatives produced by atan2 in [-180, 180].
    add360 = nodes.new("ShaderNodeMath")
    add360.operation = "ADD"
    add360.inputs[1].default_value = 360.0
    links.new(to_deg.outputs["Value"], add360.inputs[0])
    links.new(add360.outputs["Value"], wrap.inputs[0])
    return wrap.outputs["Value"]


def _handle_material_build_composite(params: dict[str, Any]) -> dict[str, Any]:
    """Realize a :class:`CompositeMaterialPlan` into a Blender material.

    The plan's content-hashed ``plan_id`` is used as the Blender
    material name (prefixed with ``forge.material.``) so two regions
    that resolve to the same plan share a single ``bpy.data.materials``
    data-block and a single shader compile.
    """
    target_object = params.get("target_object")
    plan = params.get("plan")
    if not isinstance(target_object, str):
        msg = "material.build_composite requires string 'target_object'"
        raise ValueError(msg)
    if not isinstance(plan, dict):
        msg = "material.build_composite requires 'plan' object"
        raise ValueError(msg)
    plan_id = plan.get("plan_id")
    layers = plan.get("layers")
    if not isinstance(plan_id, str) or not isinstance(layers, list) or not layers:
        msg = "plan must have string 'plan_id' and non-empty 'layers'"
        raise ValueError(msg)
    obj = bpy.data.objects.get(target_object)
    if obj is None:
        msg = f"no object named {target_object!r}"
        raise KeyError(msg)
    material_name = f"forge.material.{plan_id}"
    existing = bpy.data.materials.get(material_name)
    if existing is not None:
        # Reuse the shared material; just bind it to the mesh.
        mat = existing
    else:
        mat = bpy.data.materials.new(name=material_name)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        for node in list(nodes):
            nodes.remove(node)
        output = nodes.new("ShaderNodeOutputMaterial")
        # Build each layer's shader, mixing into a running composite.
        composite = None
        for layer in layers:
            recipe = layer.get("recipe")
            parameters = layer.get("parameters") or {}
            builder = _RECIPE_BUILDERS.get(recipe)
            if builder is None:
                msg = f"unknown material recipe {recipe!r}"
                raise ValueError(msg)
            shader_socket = builder(nodes, links, parameters)
            if composite is None:
                composite = shader_socket
                continue
            mix = nodes.new("ShaderNodeMixShader")
            fac_socket = _build_mask_factor(
                nodes,
                links,
                layer.get("mask"),
                layer.get("weight"),
                layer.get("predicate_mask"),
            )
            links.new(fac_socket, mix.inputs["Fac"])
            links.new(composite, mix.inputs[1])
            links.new(shader_socket, mix.inputs[2])
            composite = mix.outputs["Shader"]
        if composite is not None:
            links.new(composite, output.inputs["Surface"])
    if obj.data is not None and hasattr(obj.data, "materials"):
        if len(obj.data.materials) > 0:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)
    return {"material_name": mat.name, "plan_id": plan_id}


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
    if (
        not isinstance(name, str)
        or not isinstance(data_collection, str)
        or not isinstance(
            data_name,
            str,
        )
    ):
        msg = "object.from_data requires string 'name', 'data_collection', 'data_name'"
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
    if method == "render.set_engine_device":
        return _handle_render_set_engine_device(params)
    if method == "material.build_composite":
        return _handle_material_build_composite(params)
    if method == "object.from_data":
        return _handle_object_from_data(params)
    if method == "scene.assign_world":
        return _handle_scene_assign_world(params)
    if method == "scene.diff":
        return _handle_scene_diff(params)
    msg = f"unknown method: {method!r}"
    raise LookupError(msg)


def _make_response(
    req_id: object, result: dict[str, Any] | None, error: dict[str, Any] | None
) -> str:
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
        return _make_response(
            None, None, {"code": ERR_INVALID_REQUEST, "message": "not an object"}
        ), False
    req_id = req.get("id")
    method = req.get("method")
    params_raw = req.get("params", {})
    if not isinstance(method, str) or not isinstance(params_raw, dict):
        return _make_response(
            req_id, None, {"code": ERR_INVALID_REQUEST, "message": "bad method/params"}
        ), False
    if method == "shutdown":
        return _make_response(req_id, {"shutdown": True}, None), True
    try:
        result = _dispatch(method, params_raw)
    except LookupError as exc:
        return _make_response(
            req_id, None, {"code": ERR_METHOD_NOT_FOUND, "message": str(exc)}
        ), False
    except (ValueError, KeyError, TypeError, FileNotFoundError) as exc:
        return _make_response(
            req_id, None, {"code": ERR_INVALID_PARAMS, "message": str(exc)}
        ), False
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
