"""End-to-end Cycles render-options coverage against a real Blender 5.0 host.

Phase 6-d gate: assert ``forge.generate_region`` honours an explicit
``render_options={"engine": "CYCLES", "device": "CPU", ...}`` payload
and that two renders with the same Cycles sample count produce
byte-identical PNG IDAT digests.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path  # noqa: TC003 - used as runtime constructor
from typing import TYPE_CHECKING, cast

import pytest
from forge_mcp.server.tools.generation import generate_region

from tests.integration.conftest import bootstrap_region

if TYPE_CHECKING:
    from forge_mcp.project.service import ProjectService


def _ok(envelope: dict[str, object]) -> dict[str, object]:
    assert envelope["ok"] is True, envelope
    return cast("dict[str, object]", envelope["result"])


def _png_idat_digest(path: Path) -> str:
    """SHA-256 over the concatenated IDAT chunk payloads of a PNG file.

    Excludes the IHDR/IEND framing and ancillary chunks so the digest
    is invariant under metadata drift but sensitive to pixel changes.
    """
    raw = path.read_bytes()
    sig_len = 8
    assert raw[:sig_len] == b"\x89PNG\r\n\x1a\n", path
    offset = sig_len
    chunks: list[bytes] = []
    while offset < len(raw):
        (length,) = struct.unpack(">I", raw[offset : offset + 4])
        chunk_type = raw[offset + 4 : offset + 8]
        payload = raw[offset + 8 : offset + 8 + length]
        if chunk_type == b"IDAT":
            chunks.append(payload)
        offset += 8 + length + 4
        if chunk_type == b"IEND":
            break
    return hashlib.sha256(b"".join(chunks)).hexdigest()


@pytest.mark.blender_integration
def test_generate_region_renders_with_cycles_cpu(
    tmp_path: Path,
    isolated_service: ProjectService,  # noqa: ARG001
    real_blender_factory: None,  # noqa: ARG001
) -> None:
    """Cycles/CPU happy path: trace records the engine + device."""
    rid = bootstrap_region(tmp_path)
    result = _ok(
        generate_region(
            rid,
            render_options={
                "engine": "CYCLES",
                "device": "CPU",
                "cycles_samples": 16,
            },
        ),
    )
    realization = cast("dict[str, object]", result["realization"])
    assert realization["render_engine"] == "CYCLES"
    assert realization["render_device_type"] == "CPU"

    previews = cast("dict[str, dict[str, object]]", result["previews"])
    ortho = previews["ortho_top"]
    assert ortho["render_engine"] == "CYCLES"
    assert ortho["render_device_type"] == "CPU"
    assert ortho["render_cycles_samples"] == 16  # noqa: PLR2004 - pinned override
    preview = tmp_path / cast("str", ortho["preview_path"])
    assert preview.is_file()
    assert preview.stat().st_size > 0


@pytest.mark.blender_integration
def test_cycles_cpu_pinned_samples_are_deterministic(
    tmp_path: Path,
    isolated_service: ProjectService,  # noqa: ARG001
    real_blender_factory: None,  # noqa: ARG001
) -> None:
    """Same descriptor + Cycles/CPU + pinned samples ⇒ identical IDAT digests."""
    rid = bootstrap_region(tmp_path)
    options: dict[str, object] = {
        "engine": "CYCLES",
        "device": "CPU",
        "cycles_samples": 16,
    }

    first = _ok(generate_region(rid, render_options=options))
    first_previews = cast("dict[str, dict[str, object]]", first["previews"])
    first_preview = tmp_path / cast("str", first_previews["ortho_top"]["preview_path"])
    digest_a = _png_idat_digest(first_preview)

    second = _ok(generate_region(rid, render_options=options))
    second_previews = cast("dict[str, dict[str, object]]", second["previews"])
    second_preview = tmp_path / cast("str", second_previews["ortho_top"]["preview_path"])
    digest_b = _png_idat_digest(second_preview)

    assert digest_a == digest_b, (digest_a, digest_b)
