"""Phase-4 realization bench.

Exercises the full realize-region path against a real Blender 5.0
subprocess for every entry in :data:`forge_mcp.eval.EVAL_DESCRIPTORS`.
Local-only — requires ``$FORGE_BLENDER_BIN`` to point at a Blender 5.0
binary (Architecture §15). Skips with a clear message when unset.

Run with::

    FORGE_BLENDER_BIN=/usr/bin/blender uv run python scripts/eval/bench_phase4.py

Default output directory: ``docs/eval/phase4/<UTC-timestamp>/``.
For each descriptor the bench:

1. Compiles the spec, runs the terrain pipeline, persists heightmap.
2. Runs the curated ``realize_region`` macro against Blender.
3. Renders a preview PNG via ``render_preview`` at 512x384.
4. Records ``timings.json`` (per-macro wall-clock + per-step trace) and
   copies the preview PNG into the output directory.

Outputs ``manifest.json`` summarising every run plus the contact sheet
``contact_sheet.png`` (previews tiled horizontally, upscaled with
nearest-neighbour to 256x192 each).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from forge_mcp.descriptor.map_to_spec import map_to_spec
from forge_mcp.eval import (
    EVAL_BLENDER_VERSION,
    EVAL_BPY_HYPERGRAPH_VERSION,
    EVAL_DESCRIPTORS,
    EVAL_NOW,
    EVAL_SEED,
    EVAL_SHAPE,
)
from forge_mcp.generate.heightmap import save_png16
from forge_mcp.generate.terrain import run as run_terrain
from forge_mcp.realize import BLENDER_BIN_ENV, BlenderNotConfiguredError, BlenderProcess
from forge_mcp.realize.engine import RealizerEngine
from forge_mcp.realize.heightmap_mesh import mesh_from_heightmap, scene_framing_from_heightmap
from forge_mcp.realize.macros import (
    RealizeRegionInputs,
    RenderPreviewInputs,
    realize_region,
    render_preview,
)
from PIL import Image

if TYPE_CHECKING:
    from forge_mcp.realize.engine import RealizationResult


_TILE_W = 256
_TILE_H = 192
_PREVIEW_W = 512
_PREVIEW_H = 384
_RENDER_ENGINE = "BLENDER_EEVEE"  # Blender 5.0 enum identifier
_DEFAULT_COLOR_RAMP_STOPS = (
    {"position": 0.0, "color": [0.18, 0.34, 0.12, 1.0]},
    {"position": 0.5, "color": [0.45, 0.36, 0.27, 1.0]},
    {"position": 1.0, "color": [0.95, 0.95, 0.95, 1.0]},
)
_DEFAULT_SLOPE_THRESHOLD = 0.35


def _trace_summary(result: RealizationResult) -> list[dict[str, object]]:
    return [
        {
            "sequence_name": step.sequence_name,
            "step_index": step.step_index,
            "call": step.call,
            "duration_ms": round(step.duration_ms, 3),
        }
        for step in result.trace
    ]


def _bench_one(
    label: str,
    descriptor: object,
    out_dir: Path,
) -> dict[str, object]:
    spec = map_to_spec(
        descriptor,  # type: ignore[arg-type]
        seed=EVAL_SEED,
        blender_version=EVAL_BLENDER_VERSION,
        bpy_hypergraph_version=EVAL_BPY_HYPERGRAPH_VERSION,
        now=EVAL_NOW,
    )
    gen_result = run_terrain(spec, seed=EVAL_SEED, shape=EVAL_SHAPE)
    vertices, faces = mesh_from_heightmap(gen_result.heightmap)

    blend_path = out_dir / f"{label}.blend"
    preview_path = out_dir / f"{label}.preview.png"
    heightmap_png = out_dir / f"{label}.heightmap.png"
    save_png16(gen_result.heightmap, heightmap_png)
    # See generation._run_realizer for why this is zero.
    displace_strength = 0.0
    framing = scene_framing_from_heightmap(gen_result.heightmap)

    inputs = RealizeRegionInputs(
        object_name=f"terrain_{label}",
        vertices=vertices,
        faces=faces,
        region_id=label,
        spec_id=str(spec.spec_id),
        material_name=f"mat_{label}",
        color_ramp_stops=list(_DEFAULT_COLOR_RAMP_STOPS),
        slope_threshold=_DEFAULT_SLOPE_THRESHOLD,
        elevation_min=float(gen_result.heightmap.elevation_band[0]),
        elevation_max=float(gen_result.heightmap.elevation_band[1]),
        curve_name=f"stream_{label}",
        ortho_camera_name=f"cam_ortho_{label}",
        perspective_camera_name=f"cam_persp_{label}",
        ortho_location=list(framing.ortho_location),
        ortho_rotation_euler=list(framing.ortho_rotation_euler),
        ortho_scale=framing.ortho_scale,
        perspective_location=list(framing.perspective_location),
        perspective_rotation_euler=list(framing.perspective_rotation_euler),
        sun_name=f"sun_{label}",
        world_name=f"world_{label}",
        sun_location=list(framing.sun_location),
        sun_rotation_euler=list(framing.sun_rotation_euler),
        blend_filepath=str(blend_path),
        heightmap_image_filepath=str(heightmap_png),
        displace_strength=displace_strength,
    )
    render_inputs = RenderPreviewInputs(
        filepath=str(preview_path),
        resolution_x=_PREVIEW_W,
        resolution_y=_PREVIEW_H,
        camera_name=f"cam_persp_{label}",
        engine=_RENDER_ENGINE,
    )

    with BlenderProcess() as proc:
        engine = RealizerEngine(proc.client)
        t0 = time.monotonic()
        realize = realize_region(engine, inputs)
        t1 = time.monotonic()
        render = render_preview(engine, render_inputs)
        t2 = time.monotonic()

    return {
        "label": label,
        "spec_id": str(spec.spec_id),
        "primary": getattr(getattr(descriptor, "terrain", None), "primary", None).value
        if hasattr(descriptor, "terrain")
        else None,
        "blend_path": str(blend_path),
        "preview_path": str(preview_path),
        "realize_wall_ms": round((t1 - t0) * 1000.0, 3),
        "render_wall_ms": round((t2 - t1) * 1000.0, 3),
        "realize_trace": _trace_summary(realize),
        "render_trace": _trace_summary(render),
        "render_final": render.final_result,
    }


def _write_contact_sheet(rows: list[dict[str, object]], out_dir: Path) -> Path:
    tiles: list[Image.Image] = []
    for row in rows:
        preview = Path(str(row["preview_path"]))
        if preview.is_file():
            img = Image.open(preview).convert("RGB")
            img = img.resize((_TILE_W, _TILE_H), Image.NEAREST)
            tiles.append(img)
    sheet_path = out_dir / "contact_sheet.png"
    if not tiles:
        return sheet_path
    sheet = Image.new("RGB", (_TILE_W * len(tiles), _TILE_H), color=(0, 0, 0))
    for index, tile in enumerate(tiles):
        sheet.paste(tile, (index * _TILE_W, 0))
    sheet.save(sheet_path)
    return sheet_path


def bench(out_dir: Path) -> int:
    """Run the full bench, writing artifacts under ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for label, descriptor in EVAL_DESCRIPTORS:
        sys.stdout.write(f"-> {label}\n")
        sys.stdout.flush()
        rows.append(_bench_one(label, descriptor, out_dir))

    (out_dir / "manifest.json").write_text(
        json.dumps(
            {
                "seed": EVAL_SEED,
                "shape": list(EVAL_SHAPE),
                "render_engine": _RENDER_ENGINE,
                "preview_resolution": [_PREVIEW_W, _PREVIEW_H],
                "entries": rows,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    sheet = _write_contact_sheet(rows, out_dir)
    sys.stdout.write(f"wrote {sheet}\n")
    return 0


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Destination directory (defaults to docs/eval/phase4/<UTC-timestamp>/).",
    )
    args = parser.parse_args()

    try:
        BlenderProcess()  # validates env
    except BlenderNotConfiguredError as exc:
        sys.stderr.write(
            f"skipping: {exc}. Set ${BLENDER_BIN_ENV} to a Blender 5.0 binary to run.\n",
        )
        return 0

    if args.out is None:
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        out_dir = Path(__file__).resolve().parents[2] / "docs" / "eval" / "phase4" / stamp
    else:
        out_dir = args.out

    # Make sure prior runs do not leak into this directory.
    if out_dir.exists():
        shutil.rmtree(out_dir)
    return bench(out_dir)


if __name__ == "__main__":
    raise SystemExit(main())
