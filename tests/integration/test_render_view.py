"""End-to-end ``forge.render_view`` against a real Blender 5.0 host."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 - used as runtime constructor
from typing import TYPE_CHECKING, cast

import pytest
from forge_mcp.server.tools.generation import generate_region, render_view

from tests.integration.conftest import bootstrap_region

if TYPE_CHECKING:
    from forge_mcp.project.service import ProjectService

# Measured Phase 4 ceilings for the curated v1 macro under EEVEE at zlib
# level 9 for textured terrain. See docs/p4_verification_walkthrough.md
# for the NF-1.5 measurement notes (default ~213 KB observed).
_PNG_CEILING_BYTES_BY_RESOLUTION: dict[str, int] = {
    "preview": 100_000,
    "default": 280_000,
    "full": 1_120_000,
}


def _ok(envelope: dict[str, object]) -> dict[str, object]:
    assert envelope["ok"] is True, envelope
    return cast("dict[str, object]", envelope["result"])


def _err(envelope: dict[str, object]) -> dict[str, object]:
    assert envelope["ok"] is False, envelope
    return cast("dict[str, object]", envelope["error"])


@pytest.mark.blender_integration
def test_render_view_renders_perspective_se_at_full_resolution(
    tmp_path: Path,
    isolated_service: ProjectService,  # noqa: ARG001
    real_blender_factory: None,  # noqa: ARG001
) -> None:
    rid = bootstrap_region(tmp_path)
    _ok(generate_region(rid))

    result = _ok(render_view(rid, view_kind="perspective_se", resolution="full"))

    assert result["view_kind"] == "perspective_se"
    assert result["resolution"] == "full"
    assert result["render_resolution"] == [2048, 1536]
    size = result["render_file_size_bytes"]
    assert isinstance(size, int)
    assert 0 < size <= _PNG_CEILING_BYTES_BY_RESOLUTION["full"]
    png_path = tmp_path / cast("str", result["preview_path"])
    assert png_path.is_file()


@pytest.mark.blender_integration
def test_render_view_rejects_unknown_resolution(
    tmp_path: Path,
    isolated_service: ProjectService,  # noqa: ARG001
    real_blender_factory: None,  # noqa: ARG001
) -> None:
    rid = bootstrap_region(tmp_path)
    _ok(generate_region(rid))

    err = _err(render_view(rid, view_kind="ortho_top", resolution="ginormous"))

    assert err["code"] == "invalid_resolution"
