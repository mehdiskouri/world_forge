"""Host-side hypergraph builder.

Reads the raw introspection dump produced by
``scripts/blender/introspect.py`` and a curated overlay (the v1 operator
allow-list, hand-written effect annotations and ``bpy.data`` alternative
paths from ARCHITECTURE.md §5.4) and writes the four committed JSON
artifacts under ``forge_mcp/bpy_hypergraph/data/``.

This script runs in the host Python interpreter (not Blender's). It
takes raw bpy data as input — it does not require ``bpy`` itself — so
it is safe to run in CI to detect drift.

Run::

    uv run python scripts/host/build_hypergraph.py \
        --raw /tmp/forge_raw.json \
        --out forge_mcp/bpy_hypergraph/data
"""

# ruff: noqa: T201  # CLI tool prints progress

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

# Curated v1 operator allow-list. Aligned with ARCHITECTURE.md §5.4 and
# §5.5 — the ops the v1 macros actually call. Anything not on this list
# is excluded from the hypergraph artifact (the raw introspection has
# ~2.4k ops; the v1 surface is intentionally tiny).
V1_OPERATORS: Final[tuple[str, ...]] = (
    # Mesh creation
    "bpy.ops.mesh.primitive_plane_add",
    "bpy.ops.mesh.primitive_cube_add",
    "bpy.ops.mesh.primitive_grid_add",
    "bpy.ops.mesh.primitive_uv_sphere_add",
    "bpy.ops.mesh.primitive_ico_sphere_add",
    "bpy.ops.mesh.primitive_cylinder_add",
    # Mesh edit
    "bpy.ops.mesh.subdivide",
    "bpy.ops.object.shade_smooth",
    "bpy.ops.object.shade_flat",
    # Modifiers (legacy ops path; prefer obj.modifiers.new in 5.0)
    "bpy.ops.object.modifier_add",
    "bpy.ops.object.modifier_apply",
    "bpy.ops.object.modifier_remove",
    # Object / scene management
    "bpy.ops.object.select_all",
    "bpy.ops.object.delete",
    "bpy.ops.object.transform_apply",
    "bpy.ops.object.origin_set",
    "bpy.ops.object.parent_set",
    # Image / heightmap
    "bpy.ops.image.open",
    # Render
    "bpy.ops.render.render",
    # File / scene
    "bpy.ops.wm.save_as_mainfile",
    "bpy.ops.wm.save_mainfile",
    "bpy.ops.wm.open_mainfile",
    "bpy.ops.wm.read_factory_settings",
    "bpy.ops.wm.quit_blender",
)

# Hand-curated effect annotations: per-operator pre/post conditions and
# scene-state mutations. ARCHITECTURE §5.1 describes effect nodes; v1 we
# encode them as JSON dicts so the runtime can query them without code.
# Conservative on the first pass — only the shapes the v1 macros need.
EFFECTS: Final[dict[str, dict[str, list[str]]]] = {
    "bpy.ops.mesh.primitive_plane_add": {
        "preconditions": ["context.mode == 'OBJECT'"],
        "postconditions": ["new_object_active", "object_kind == 'MESH'"],
        "mutates": ["scene.objects", "scene.collection", "active_object"],
    },
    "bpy.ops.mesh.primitive_cube_add": {
        "preconditions": ["context.mode == 'OBJECT'"],
        "postconditions": ["new_object_active", "object_kind == 'MESH'"],
        "mutates": ["scene.objects", "scene.collection", "active_object"],
    },
    "bpy.ops.mesh.primitive_grid_add": {
        "preconditions": ["context.mode == 'OBJECT'"],
        "postconditions": ["new_object_active", "object_kind == 'MESH'"],
        "mutates": ["scene.objects", "scene.collection", "active_object"],
    },
    "bpy.ops.mesh.primitive_uv_sphere_add": {
        "preconditions": ["context.mode == 'OBJECT'"],
        "postconditions": ["new_object_active", "object_kind == 'MESH'"],
        "mutates": ["scene.objects", "active_object"],
    },
    "bpy.ops.mesh.primitive_ico_sphere_add": {
        "preconditions": ["context.mode == 'OBJECT'"],
        "postconditions": ["new_object_active", "object_kind == 'MESH'"],
        "mutates": ["scene.objects", "active_object"],
    },
    "bpy.ops.mesh.primitive_cylinder_add": {
        "preconditions": ["context.mode == 'OBJECT'"],
        "postconditions": ["new_object_active", "object_kind == 'MESH'"],
        "mutates": ["scene.objects", "active_object"],
    },
    "bpy.ops.mesh.subdivide": {
        "preconditions": ["context.mode == 'EDIT_MESH'", "active_mesh_has_selection"],
        "postconditions": ["mesh_vertex_count_increased"],
        "mutates": ["active_mesh.vertices", "active_mesh.edges", "active_mesh.polygons"],
    },
    "bpy.ops.object.shade_smooth": {
        "preconditions": ["context.mode == 'OBJECT'", "selected_objects_nonempty"],
        "postconditions": ["selected_meshes_have_smooth_shading"],
        "mutates": ["object.data.polygons.use_smooth"],
    },
    "bpy.ops.object.shade_flat": {
        "preconditions": ["context.mode == 'OBJECT'", "selected_objects_nonempty"],
        "postconditions": ["selected_meshes_have_flat_shading"],
        "mutates": ["object.data.polygons.use_smooth"],
    },
    "bpy.ops.object.modifier_add": {
        "preconditions": ["context.mode == 'OBJECT'", "active_object_supports_modifiers"],
        "postconditions": ["active_object.modifiers_count_increased"],
        "mutates": ["active_object.modifiers"],
    },
    "bpy.ops.object.modifier_apply": {
        "preconditions": ["context.mode == 'OBJECT'", "named_modifier_exists"],
        "postconditions": ["named_modifier_removed", "geometry_baked"],
        "mutates": ["active_object.data", "active_object.modifiers"],
    },
    "bpy.ops.object.modifier_remove": {
        "preconditions": ["context.mode == 'OBJECT'", "named_modifier_exists"],
        "postconditions": ["named_modifier_removed"],
        "mutates": ["active_object.modifiers"],
    },
    "bpy.ops.object.select_all": {
        "preconditions": ["context.mode == 'OBJECT'"],
        "postconditions": ["selection_state_predictable"],
        "mutates": ["scene.selection"],
    },
    "bpy.ops.object.delete": {
        "preconditions": ["context.mode == 'OBJECT'", "selected_objects_nonempty"],
        "postconditions": ["selected_objects_removed"],
        "mutates": ["scene.objects", "active_object"],
    },
    "bpy.ops.object.transform_apply": {
        "preconditions": ["context.mode == 'OBJECT'", "selected_objects_nonempty"],
        "postconditions": ["object_transform_identity_for_chosen_axes"],
        "mutates": [
            "object.location",
            "object.rotation_euler",
            "object.scale",
            "object.data",
        ],
    },
    "bpy.ops.object.origin_set": {
        "preconditions": ["context.mode == 'OBJECT'", "selected_objects_nonempty"],
        "postconditions": ["object_origin_changed"],
        "mutates": ["object.location", "object.data"],
    },
    "bpy.ops.object.parent_set": {
        "preconditions": [
            "context.mode == 'OBJECT'",
            "active_object_set",
            "selected_objects_count >= 2",
        ],
        "postconditions": ["children_have_parent_active"],
        "mutates": ["object.parent", "object.matrix_parent_inverse"],
    },
    "bpy.ops.image.open": {
        "preconditions": ["filepath_exists", "filepath_is_image"],
        "postconditions": ["image_in_bpy_data_images"],
        "mutates": ["bpy.data.images"],
    },
    "bpy.ops.render.render": {
        "preconditions": ["scene.camera_set", "render_engine_configured"],
        "postconditions": ["render_result_available"],
        "mutates": ["bpy.data.images['Render Result']"],
    },
    "bpy.ops.wm.save_as_mainfile": {
        "preconditions": ["filepath_dir_writable"],
        "postconditions": ["blend_file_written", "bpy.data.filepath_updated"],
        "mutates": ["filesystem", "bpy.data.filepath"],
    },
    "bpy.ops.wm.save_mainfile": {
        "preconditions": ["bpy.data.filepath_set"],
        "postconditions": ["blend_file_overwritten"],
        "mutates": ["filesystem"],
    },
    "bpy.ops.wm.open_mainfile": {
        "preconditions": ["filepath_exists", "filepath_is_blend"],
        "postconditions": ["scene_replaced", "bpy.data.filepath_updated"],
        "mutates": ["bpy.data", "bpy.context.scene"],
    },
    "bpy.ops.wm.read_factory_settings": {
        "preconditions": [],
        "postconditions": ["scene_reset_to_defaults"],
        "mutates": ["bpy.data", "bpy.context.scene"],
    },
    "bpy.ops.wm.quit_blender": {
        "preconditions": [],
        "postconditions": ["process_terminating"],
        "mutates": ["process_lifecycle"],
    },
}

# bpy.data alternative paths per ARCHITECTURE §5.4 — Blender 5.0's
# harmonization expresses many traditional ops via direct bpy.data calls.
# Where the table lists ``preferred = "data"``, the realizer should call
# the data path; where ``preferred = "ops"`` (e.g., render, save), the
# operator path is required because it's context-dependent.
ALTERNATIVE_PATHS: Final[dict[str, dict[str, str]]] = {
    "bpy.ops.mesh.primitive_plane_add": {
        "data_path": "bpy.data.meshes.new + bmesh plane construction",
        "preferred": "ops",  # primitive_*_add is concise; bmesh path is for advanced control
        "notes": "5.0: ops path is canonical for primitives; switch to data for custom topologies.",
    },
    "bpy.ops.object.modifier_add": {
        "data_path": "bpy.data.objects[name].modifiers.new(name, type)",
        "preferred": "data",
        "notes": "5.0: data path avoids context.active_object dependency.",
    },
    "bpy.ops.object.modifier_apply": {
        "data_path": "bpy.context.evaluated_depsgraph_get + mesh.from_existing",
        "preferred": "ops",
        "notes": "Data path requires depsgraph evaluation; ops path is simpler in v1.",
    },
    "bpy.ops.image.open": {
        "data_path": "bpy.data.images.load(filepath, check_existing=True)",
        "preferred": "data",
        "notes": "5.0: data path is the canonical loader; ops path is editor-only.",
    },
    "bpy.ops.object.delete": {
        "data_path": "bpy.data.objects.remove(obj, do_unlink=True)",
        "preferred": "data",
        "notes": "5.0: data path is deterministic; ops path depends on selection state.",
    },
    "bpy.ops.render.render": {
        "data_path": None,
        "preferred": "ops",
        "notes": "Render is fundamentally context-bound; no equivalent data path.",
    },
    "bpy.ops.wm.save_as_mainfile": {
        "data_path": None,
        "preferred": "ops",
        "notes": "Save is window-manager-bound; no equivalent data path.",
    },
}


def _filter_operators(raw_ops: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    selected = set(V1_OPERATORS)
    out: list[dict[str, object]] = []
    seen: set[str] = set()
    for op in raw_ops:
        idname = op.get("idname")
        if idname in selected and isinstance(idname, str) and idname not in seen:
            out.append(dict(op))
            seen.add(idname)
    missing = selected - seen
    if missing:
        msg = f"v1 operators missing from raw introspection: {sorted(missing)}"
        raise ValueError(msg)
    out.sort(key=lambda r: str(r["idname"]))
    return out


def _write_json(path: Path, payload: object) -> None:
    """Write ``payload`` to ``path`` as pretty, deterministic JSON."""
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build(raw_path: Path, out_dir: Path) -> None:
    """Read raw introspection JSON and write the four hypergraph artifacts."""
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    blender_version = raw["blender_version"]
    schema_tag = f"blender-{blender_version}-v1"

    operators = _filter_operators(raw["operators"])
    types = sorted(raw["types"], key=lambda r: str(r["name"]))

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        out_dir / "operators.json",
        {"schema_tag": schema_tag, "blender_version": blender_version, "operators": operators},
    )
    _write_json(
        out_dir / "types.json",
        {"schema_tag": schema_tag, "blender_version": blender_version, "types": types},
    )
    _write_json(
        out_dir / "effects.json",
        {"schema_tag": schema_tag, "effects": EFFECTS},
    )
    _write_json(
        out_dir / "alternative_paths.json",
        {"schema_tag": schema_tag, "alternative_paths": ALTERNATIVE_PATHS},
    )
    print(
        f"build_hypergraph: wrote {len(operators)} operators, {len(types)} types,"
        f" {len(EFFECTS)} effect entries, {len(ALTERNATIVE_PATHS)} alternative paths"
        f" (tag={schema_tag})",
    )


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the host-side hypergraph builder."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    """CLI entry point."""
    args = _parse_args()
    build(args.raw, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
