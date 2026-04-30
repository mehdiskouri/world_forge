"""Blender-internal introspection script.

Runs *inside* Blender 5.0.0's embedded Python interpreter, invoked as::

    blender --background --python scripts/blender/introspect.py -- --out raw.json

Walks ``bpy.ops`` and a curated set of ``bpy.types`` programmatically and
emits a single JSON file describing every operator (idname, parameters,
poll source, bl_options, docstring) and every property of the curated
types (name, type, default, description). The host-side ingestion
pipeline (:mod:`forge_mcp.bpy_hypergraph.ingest`) consumes this raw
output, enriches it with hand-curated effect annotations and 5.0
``bpy.data`` alternative paths, and emits the four committed JSON files
under ``forge_mcp/bpy_hypergraph/data/``.

Stdio policy: this script writes its own progress to **stderr**. The
output JSON path is supplied via ``--out`` and never goes to stdout.
"""

# ruff: noqa: T201, S110, BLE001  # printing to stderr & broad excepts are intentional in
# Blender-internal introspection: we must not crash Blender on a single
# obscure operator or type that fails to introspect.

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

import bpy  # type: ignore[import-not-found]  # provided by Blender's interpreter

# Curated type allow-list per ARCHITECTURE.md §5.1. Keeps the introspection
# output bounded; full bpy.types is many thousands of entries.
TYPE_ALLOW_LIST: tuple[str, ...] = (
    "Mesh",
    "Material",
    "Modifier",
    "Object",
    "Image",
    "Curve",
    "Light",
    "Camera",
    "World",
    "Scene",
    "Collection",
)


def _bpy_version() -> str:
    return ".".join(str(p) for p in bpy.app.version)


def _walk_ops_module(module, prefix: str, out: list[dict]) -> None:
    """Recursively walk a `bpy.ops.*` submodule, appending operator records."""
    for name in dir(module):
        if name.startswith("_"):
            continue
        try:
            attr = getattr(module, name)
        except Exception:
            continue
        idname = f"{prefix}.{name}"
        # Operators are callable objects; submodules are modules-like.
        if callable(attr) and hasattr(attr, "get_rna_type"):
            try:
                rna = attr.get_rna_type()
            except Exception:
                continue
            params = []
            for prop in rna.properties:
                if prop.identifier == "rna_type":
                    continue
                params.append({
                    "name": prop.identifier,
                    "type": prop.type,
                    "is_required": getattr(prop, "is_required", False),
                    "is_output": getattr(prop, "is_output", False),
                    "description": getattr(prop, "description", "") or "",
                })
            out.append({
                "idname": idname,
                "label": getattr(rna, "name", "") or "",
                "description": getattr(rna, "description", "") or "",
                "bl_options": sorted(getattr(attr, "bl_options", []) or []),
                "params": params,
            })
        elif hasattr(attr, "__name__") and not callable(attr):
            _walk_ops_module(attr, idname, out)


def _introspect_ops() -> list[dict]:
    out: list[dict] = []
    for sub in dir(bpy.ops):
        if sub.startswith("_"):
            continue
        try:
            mod = getattr(bpy.ops, sub)
        except Exception:
            continue
        _walk_ops_module(mod, f"bpy.ops.{sub}", out)
    return out


def _introspect_types() -> list[dict]:
    out: list[dict] = []
    for type_name in TYPE_ALLOW_LIST:
        rna = getattr(bpy.types, type_name, None)
        if rna is None:
            continue
        try:
            rna_type = rna.bl_rna
        except Exception:
            continue
        props = []
        for prop in rna_type.properties:
            if prop.identifier == "rna_type":
                continue
            props.append({
                "name": prop.identifier,
                "type": prop.type,
                "description": getattr(prop, "description", "") or "",
                "is_readonly": getattr(prop, "is_readonly", False),
            })
        out.append({
            "name": type_name,
            "description": getattr(rna_type, "description", "") or "",
            "properties": props,
        })
    return out


def _parse_args(argv: list[str]) -> argparse.Namespace:
    # When invoked via ``blender --python <script> -- --out X``, Blender
    # consumes argv up to ``--``; the rest is what we receive.
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args(argv)


def main() -> int:
    try:
        args = _parse_args(sys.argv[1:])
    except SystemExit as exc:
        return int(exc.code or 1)

    print(f"introspect: blender={_bpy_version()}", file=sys.stderr)
    try:
        ops = _introspect_ops()
        types = _introspect_types()
    except Exception:
        traceback.print_exc()
        return 2

    payload = {
        "blender_version": _bpy_version(),
        "operators": ops,
        "types": types,
    }
    args.out.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"introspect: wrote {len(ops)} operators, {len(types)} types -> {args.out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
