"""Determinism check for ``forge.generate_region`` (phase 4 §10).

Running the same descriptor + seed twice (in fresh project trees) must
yield byte-identical preview PNGs. The ``.blend`` file embeds a build
timestamp so we hash the deterministic ortho preview PNG instead.
"""

from __future__ import annotations

import hashlib
import struct
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from forge_mcp.descriptor.schema import StructuredDescriptor, Terrain, TerrainPrimary
from forge_mcp.project.service import ProjectService
from forge_mcp.realize import BlenderProcess
from forge_mcp.realize.engine import RealizerEngine
from forge_mcp.server.tools import set_realizer_factory, set_service
from forge_mcp.server.tools.generation import generate_region
from forge_mcp.server.tools.projects import create_project
from forge_mcp.server.tools.regions import create_region

if TYPE_CHECKING:
    from collections.abc import Iterator

_BOUNDS: dict[str, object] = {"min": [0.0, 0.0], "max": [10.0, 10.0]}
_SQUARE = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]


def _ok(envelope: dict[str, object]) -> dict[str, object]:
    assert envelope["ok"] is True, envelope
    return cast("dict[str, object]", envelope["result"])


def _provision(root: Path) -> str:
    set_service(ProjectService())
    _ok(create_project(str(root), "DeterminismDemo", _BOUNDS))
    descriptor = StructuredDescriptor(terrain=Terrain(primary=TerrainPrimary.ROLLING_HILLS))
    region = _ok(
        create_region(
            "Alpha",
            _SQUARE,
            structured_descriptor=descriptor.model_dump(mode="json"),
            seed=7,
        ),
    )
    return cast("str", region["node_id"])


def _digest_ortho_preview(result: dict[str, object]) -> str:
    previews = cast("dict[str, dict[str, object]]", result["previews"])
    ortho = previews["ortho_top"]
    path = Path(cast("str", ortho["preview_path"]))
    # Hash only PNG IDAT (pixel) chunks; libpng writes wall-clock-influenced
    # tEXt/tIME metadata that is not part of the determinism contract.
    data = path.read_bytes()
    digest = hashlib.blake2b(digest_size=16)
    pos = 8  # skip 8-byte PNG signature
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        chunk_type = data[pos + 4 : pos + 8]
        chunk_data = data[pos + 8 : pos + 8 + length]
        if chunk_type == b"IDAT":
            digest.update(chunk_data)
        pos += 8 + length + 4
    return digest.hexdigest()


@pytest.fixture
def real_blender_factory_owned() -> Iterator[None]:
    @contextmanager
    def factory() -> Iterator[RealizerEngine]:
        with BlenderProcess() as proc:
            yield RealizerEngine(proc.client)

    set_realizer_factory(factory)
    try:
        yield
    finally:
        set_realizer_factory(None)


@pytest.mark.blender_integration
def test_generate_region_is_byte_deterministic_across_runs(
    tmp_path: Path,
    real_blender_factory_owned: None,  # noqa: ARG001 - fixture installs factory
) -> None:
    root_a = tmp_path / "run_a"
    root_a.mkdir()
    rid_a = _provision(root_a)
    result_a = _ok(generate_region(rid_a))
    digest_a = _digest_ortho_preview(result_a)

    root_b = tmp_path / "run_b"
    root_b.mkdir()
    rid_b = _provision(root_b)
    result_b = _ok(generate_region(rid_b))
    digest_b = _digest_ortho_preview(result_b)

    assert digest_a == digest_b, f"ortho preview not byte-deterministic: {digest_a} != {digest_b}"
