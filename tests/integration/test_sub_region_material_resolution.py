"""End-to-end sub-region material resolution against a real Blender 5.0 host.

Phase 6-c Phase G: drive ``forge.create_sub_region`` +
``forge.apply_material`` through the MCP tool surface, generate the
parent region, and assert that the resolved
:class:`CompositeMaterialPlan` carries the sub-region application as
an additional layer with a :class:`PredicateMask`, that the realized
scene binds a single material slot named after the deterministic plan
id, that two consecutive resolves produce identical plan ids, and
that updating the predicate produces a deterministically *different*
plan id.

Also includes the **half-strength regression gate**: with the
Highlands sub-region's predicate widened to cover 100% of the
surface, the rendered ortho-top preview must be dominated by the
snow archetype's near-white colour. The pre-hotfix bug in
``_build_base_mask_factor`` produced a 50/50 grass+snow blend whose
mean blue channel sits *below* the mean red channel; the post-fix
shader graph drives the MixShader Fac to 1.0 so the predicate band
fully replaces the underlying region material.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import numpy as np
import pytest
from forge_mcp.realize import BlenderProcess
from forge_mcp.server.tools.generation import generate_region
from forge_mcp.server.tools.materials import (
    apply_material,
    create_material_archetype,
    resolve_material,
)
from forge_mcp.server.tools.sub_regions import (
    create_sub_region,
    preview_sub_region_coverage,
    update_sub_region,
)
from PIL import Image

from tests.integration.conftest import bootstrap_region

if TYPE_CHECKING:
    from pathlib import Path

    from forge_mcp.project.service import ProjectService

# A height band wide enough that any rolling-hills heightmap will have at
# least one pixel inside it; the seed-7 region used by ``bootstrap_region``
# routinely produces elevations spanning -300..+300 m.
_HIGHLANDS_PRED: dict[str, object] = {
    "kind": "height_band",
    "low_m": -10000.0,
    "high_m": 10000.0,
}
_HIGHLANDS_PRED_NARROWED: dict[str, object] = {
    "kind": "height_band",
    "low_m": -5000.0,
    "high_m": 5000.0,
}
_ELEVATION_BAND_PAIR = 2
_EXPECTED_LAYER_COUNT = 2


def _ok(envelope: dict[str, object]) -> dict[str, object]:
    assert envelope["ok"] is True, envelope
    return cast("dict[str, object]", envelope["result"])


def _scene(rid: str) -> tuple[str, str]:
    """Create base + sub-region archetypes, sub_region node, and apply both.

    Returns ``(sub_region_id, sub_region_application_edge_id)``.
    """
    base = _ok(
        create_material_archetype(
            "valley_grass",
            "flat_color",
            parameters={"color": [0.30, 0.55, 0.20, 1.0]},
        ),
    )
    snow = _ok(
        create_material_archetype(
            "alpine_snow",
            "flat_color",
            parameters={"color": [0.96, 0.97, 0.99, 1.0]},
        ),
    )
    base_id = cast("str", base["node_id"])
    snow_id = cast("str", snow["node_id"])
    _ok(
        apply_material(
            base_id,
            rid,
            attrs={"scope": "region", "priority": 0},
        ),
    )
    sub = _ok(create_sub_region(rid, "Highlands", _HIGHLANDS_PRED))
    sub_id = cast("str", sub["node_id"])
    sub_app = _ok(
        apply_material(
            snow_id,
            sub_id,
            attrs={"scope": "sub_region", "priority": 5},
        ),
    )
    return sub_id, cast("str", sub_app["edge_id"])


def _assert_snow_dominated_preview(preview_path: str) -> None:
    """Assert the rendered ortho-top preview is dominated by the snow archetype.

    With the Highlands predicate covering 100% of the surface, the
    ortho-top preview must be dominated by the snow archetype
    (R=0.96, G=0.97, B=0.99 — near-white, B>R). The pre-hotfix
    adapter produced a constant 0.5 base mask factor that, multiplied
    with the predicate factor of 1.0, drove ``MixShader.Fac`` to 0.5
    and gave a 50/50 grass + snow blend whose mean R≈0.63 sits
    *above* mean B≈0.59. Asserting B > R isolates the bug from
    render-engine / lighting noise: only a snow-dominated frame has
    B above R.
    """
    with Image.open(preview_path) as image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    mean_r = float(rgb[..., 0].mean())
    mean_g = float(rgb[..., 1].mean())
    mean_b = float(rgb[..., 2].mean())
    assert mean_b > mean_r, (
        f"sub-region material composited at half strength: "
        f"mean RGB = ({mean_r:.3f}, {mean_g:.3f}, {mean_b:.3f}); "
        f"snow-dominated frame must have B > R"
    )


def _assert_single_material_slot(blend_path: str, object_name: str, expected: str) -> None:
    """Open ``blend_path`` and assert ``object_name`` carries one slot named ``expected``."""
    with BlenderProcess() as proc:
        proc.client.call("bpy.ops.wm.open_mainfile", {"filepath": blend_path})
        slot_list = proc.client.call(
            "get_property",
            {"collection": "objects", "name": object_name, "path": "data.materials"},
        )
    assert isinstance(slot_list, dict)
    slot_value = slot_list.get("value")
    assert isinstance(slot_value, list)
    assert len(slot_value) == 1, slot_value
    slot_repr = slot_value[0]
    assert isinstance(slot_repr, str)
    assert expected in slot_repr, slot_repr


@pytest.mark.blender_integration
def test_sub_region_material_resolves_and_renders_with_deterministic_plan_id(
    tmp_path: Path,
    isolated_service: ProjectService,  # noqa: ARG001 - autouses set_service
    real_blender_factory: None,  # noqa: ARG001 - autouses set_realizer_factory
) -> None:
    """Sub-region scoped application becomes an extra PredicateMask layer."""
    rid = bootstrap_region(tmp_path)
    sub_id, _sub_app_edge_id = _scene(rid)

    result = _ok(generate_region(rid))
    object_name = f"terrain_{rid}"

    realization = result["realization"]
    assert isinstance(realization, dict)
    band = realization.get("elevation_band")
    assert isinstance(band, list)
    assert len(band) == _ELEVATION_BAND_PAIR
    elevation_min = float(cast("float", band[0]))
    elevation_max = float(cast("float", band[1]))

    # Determinism gate: two resolves on the same scene yield identical plans.
    preview_a = _ok(
        resolve_material(
            rid,
            mesh_name=object_name,
            elevation_min=elevation_min,
            elevation_max=elevation_max,
        ),
    )
    preview_b = _ok(
        resolve_material(
            rid,
            mesh_name=object_name,
            elevation_min=elevation_min,
            elevation_max=elevation_max,
        ),
    )
    assert preview_a == preview_b

    expected_plan_id = preview_a["plan_id"]
    assert isinstance(expected_plan_id, str)
    assert expected_plan_id.startswith("mplan_")

    layers = preview_a["layers"]
    assert isinstance(layers, list)
    assert len(layers) == _EXPECTED_LAYER_COUNT
    # Sub-region application has higher precedence (rank 3 > region rank 2)
    # so it is the second / outermost layer.
    sub_layer = layers[-1]
    assert isinstance(sub_layer, dict)
    predicate_mask = sub_layer.get("predicate_mask")
    assert isinstance(predicate_mask, dict)
    assert predicate_mask.get("kind") == "predicate"
    inner = predicate_mask.get("predicate")
    assert isinstance(inner, dict)
    assert inner.get("kind") == "height_band"

    # Coverage-preview gate: a wide band selects every vertex.
    coverage = _ok(preview_sub_region_coverage(sub_id))
    assert coverage["coverage_fraction"] == pytest.approx(1.0)

    # Predicate update produces a deterministically different plan id.
    _ok(update_sub_region(sub_id, predicate=_HIGHLANDS_PRED_NARROWED))
    preview_c = _ok(
        resolve_material(
            rid,
            mesh_name=object_name,
            elevation_min=elevation_min,
            elevation_max=elevation_max,
        ),
    )
    assert preview_c["plan_id"] != expected_plan_id

    # End-to-end Blender realization gate: open the saved blend, confirm a
    # single material slot whose name matches the deterministic plan id.
    expected_material_name = f"forge.material.{expected_plan_id}"
    blend_path = cast("str", result["blend_path"])
    _assert_single_material_slot(blend_path, object_name, expected_material_name)

    # Half-strength regression gate (see ``_assert_snow_dominated_preview``).
    previews = result.get("previews")
    assert isinstance(previews, dict), previews
    ortho = previews.get("ortho_top")
    assert isinstance(ortho, dict), previews
    preview_path = cast("str", ortho["preview_path"])
    with Image.open(preview_path) as image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    mean_r = float(rgb[..., 0].mean())
    mean_g = float(rgb[..., 1].mean())
    mean_b = float(rgb[..., 2].mean())
    assert mean_b > mean_r, (
        f"sub-region material composited at half strength: "
        f"mean RGB = ({mean_r:.3f}, {mean_g:.3f}, {mean_b:.3f}); "
        f"snow-dominated frame must have B > R"
    )
