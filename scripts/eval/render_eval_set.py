"""Render the Phase-3 acceptance contact sheet.

Local-only (per the Phase-3 plan, Stage G). Run with::

    uv run python scripts/eval/render_eval_set.py [--out DIR]

Default output directory: ``docs/eval/phase3/<UTC-timestamp>/``.
Writes ``contact_sheet.png`` (the five eval previews tiled
horizontally, upscaled with nearest-neighbour to 256x256 each), plus
``analyses.json`` and ``manifest.json`` capturing the inputs and
analyses for the same run. Determinism: descriptors and seed live in
:mod:`forge_mcp.eval`, shared with the regression test, so this
script and CI never disagree on inputs.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from forge_mcp.analyze.terrain_analysis import analyze
from forge_mcp.descriptor.map_to_spec import map_to_spec
from forge_mcp.eval import (
    EVAL_BLENDER_VERSION,
    EVAL_BPY_HYPERGRAPH_VERSION,
    EVAL_DESCRIPTORS,
    EVAL_NOW,
    EVAL_SEED,
    EVAL_SHAPE,
)
from forge_mcp.generate.terrain import run
from PIL import Image

if TYPE_CHECKING:
    from numpy.typing import NDArray

_TILE_PIXELS = 256
_FLAT_GRID_TOLERANCE = 1e-9
_QUANT_MAX = 255.0


def _heightmap_to_tile(data: NDArray[np.float32]) -> Image.Image:
    """Upscale and 8-bit-quantise a heightmap to a fixed-size preview tile."""
    arr = np.asarray(data, dtype=np.float32)
    lo = float(arr.min())
    hi = float(arr.max())
    normalised = (
        np.zeros_like(arr, dtype=np.float32)
        if hi - lo < _FLAT_GRID_TOLERANCE  # pragma: no cover - constant grids do not arise
        else (arr - lo) / (hi - lo)
    )
    quantised = (normalised * _QUANT_MAX).astype(np.uint8)
    return Image.fromarray(quantised).resize((_TILE_PIXELS, _TILE_PIXELS), Image.NEAREST)


def render(out_dir: Path) -> Path:
    """Render the contact sheet + JSON manifests under ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tiles: list[Image.Image] = []
    analyses: list[dict[str, object]] = []
    manifest_entries: list[dict[str, object]] = []

    for label, descriptor in EVAL_DESCRIPTORS:
        spec = map_to_spec(
            descriptor,
            seed=EVAL_SEED,
            blender_version=EVAL_BLENDER_VERSION,
            bpy_hypergraph_version=EVAL_BPY_HYPERGRAPH_VERSION,
            now=EVAL_NOW,
        )
        result = run(spec, seed=EVAL_SEED, shape=EVAL_SHAPE)
        analysis = analyze(result.heightmap, result.stream_geometry)
        tiles.append(_heightmap_to_tile(result.heightmap.data))
        analyses.append({"label": label, "analysis": analysis.model_dump(mode="json")})
        manifest_entries.append(
            {
                "label": label,
                "spec_id": str(spec.spec_id),
                "primary": descriptor.terrain.primary.value,
                "generators_used": list(result.generators_used),
            },
        )

    sheet = Image.new("L", (_TILE_PIXELS * len(tiles), _TILE_PIXELS), color=0)
    for index, tile in enumerate(tiles):
        sheet.paste(tile, (index * _TILE_PIXELS, 0))
    sheet_path = out_dir / "contact_sheet.png"
    sheet.save(sheet_path)

    (out_dir / "analyses.json").write_text(
        json.dumps(analyses, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out_dir / "manifest.json").write_text(
        json.dumps(
            {
                "seed": EVAL_SEED,
                "shape": list(EVAL_SHAPE),
                "entries": manifest_entries,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return sheet_path


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Destination directory (defaults to docs/eval/phase3/<UTC-timestamp>/).",
    )
    args = parser.parse_args()

    if args.out is None:
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        out_dir = Path(__file__).resolve().parents[2] / "docs" / "eval" / "phase3" / stamp
    else:
        out_dir = args.out

    sheet_path = render(out_dir)
    sys.stdout.write(f"wrote {sheet_path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
