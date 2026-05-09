"""MCP cleanup tools (Phase 7 Stage G).

Read-only diagnostics plus one explicit-opt-in mutator for finding and
purging stale or dangling artefacts inside an open project. The
``forge.cleanup`` skill names these four tools by contract; they were
stubbed out in the skill catalogue but never implemented until this
stage.

* ``forge.find_orphans`` -- orphan specs (file on disk but no
  ``RegionNode.spec_id`` references it), material applications whose
  edge endpoints reference a missing archetype, and environment
  bindings whose target environment id no longer exists. Read-only.
* ``forge.find_stale_realizations`` -- ``.blend`` files whose source
  spec JSON has a newer ``mtime`` (i.e. user re-ran ``compile_spec``
  but never re-rendered). Read-only.
* ``forge.find_lock_conflicts`` -- locks whose target region was
  deleted, or property locks whose ``expected_value`` no longer
  matches the live region JSON. Read-only.
* ``forge.purge_orphans`` -- the only mutator. Defaults to
  ``dry_run=True``; ``dry_run=False`` must be passed explicitly to
  delete anything. Removes orphan spec files atomically.

All four tools use the standard ``ok()`` / ``fail()`` envelope and
surface :class:`NoOpenProjectError` as ``no_open_project``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_mcp.project.lock_enforcement import _resolve_path
from forge_mcp.project.schemas import (
    LAYER_MATERIAL_APPLICATION,
    LockKind,
    PropertyLockPayload,
)
from forge_mcp.project.service import NoOpenProjectError
from forge_mcp.server.tools import get_service
from forge_mcp.server.tools._responses import fail, ok

if TYPE_CHECKING:
    from pathlib import Path

    from forge_mcp.project.service import ProjectState


def _state() -> ProjectState:
    return get_service().state


def _orphan_specs(state: ProjectState) -> list[dict[str, object]]:
    """Return a list of ``{spec_id, path}`` dicts for unreferenced spec files."""
    specs_dir = state.paths.specs_dir
    if not specs_dir.exists():
        return []
    referenced: set[str] = {
        str(region.spec_id) for region in state.regions.values() if region.spec_id is not None
    }
    orphans: list[dict[str, object]] = []
    for path in sorted(specs_dir.glob("*.json")):
        spec_id = path.stem
        if spec_id not in referenced:
            orphans.append({"spec_id": spec_id, "path": str(path)})
    return orphans


def _orphan_material_applications(state: ProjectState) -> list[dict[str, object]]:
    """Return material_application edges whose archetype endpoint is missing."""
    edges = state.edges.get(LAYER_MATERIAL_APPLICATION, ())
    archetype_ids: set[str] = {str(node_id) for node_id in state.archetypes}
    orphans: list[dict[str, object]] = []
    for edge in edges:
        for endpoint in edge.endpoints:
            endpoint_str = str(endpoint)
            # Material-application edges are (target_node, archetype_node);
            # archetypes are the only kind we own, so any endpoint that
            # is *not* present in regions/sub_regions/environments AND not
            # in archetypes is the dangling one.
            in_known_targets = (
                endpoint_str in {str(rid) for rid in state.regions}
                or endpoint_str in {str(sid) for sid in state.sub_regions}
                or endpoint_str in {str(eid) for eid in state.environments}
            )
            if not in_known_targets and endpoint_str not in archetype_ids:
                orphans.append(
                    {
                        "edge_id": str(edge.edge_id),
                        "missing_endpoint": endpoint_str,
                    },
                )
                break
    return orphans


def _orphan_environment_bindings(state: ProjectState) -> list[dict[str, object]]:
    """Return regions whose ``environment_id`` no longer exists."""
    known_envs: set[str] = {str(env_id) for env_id in state.environments}
    orphans: list[dict[str, object]] = []
    for region in state.regions.values():
        if region.environment_id is None:
            continue
        if str(region.environment_id) not in known_envs:
            orphans.append(
                {
                    "region_id": str(region.node_id),
                    "missing_environment_id": str(region.environment_id),
                },
            )
    return orphans


def find_orphans() -> dict[str, object]:
    """Return orphan specs, material applications and environment bindings."""
    try:
        state = _state()
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    return ok(
        {
            "specs": _orphan_specs(state),
            "material_applications": _orphan_material_applications(state),
            "environment_bindings": _orphan_environment_bindings(state),
        },
    )


def _spec_path_for_region(state: ProjectState, region_id: object) -> Path | None:
    region = next(
        (r for r in state.regions.values() if str(r.node_id) == str(region_id)),
        None,
    )
    if region is None or region.spec_id is None:
        return None
    return state.paths.spec_path(region.spec_id)


def find_stale_realizations() -> dict[str, object]:
    """Return ``.blend`` files whose source spec JSON has a newer mtime."""
    try:
        state = _state()
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    blender_dir = state.paths.blender_dir
    if not blender_dir.exists():
        return ok({"stale": []})
    stale: list[dict[str, object]] = []
    for blend_path in sorted(blender_dir.glob("*.blend")):
        region_id_str = blend_path.stem
        # Match by RegionId surface form; regions dict is keyed by RegionId.
        match = next(
            (rid for rid in state.regions if str(rid) == region_id_str),
            None,
        )
        if match is None:
            stale.append({"region_id": region_id_str, "reason": "region_deleted"})
            continue
        spec_path = _spec_path_for_region(state, match)
        if spec_path is None or not spec_path.exists():
            stale.append({"region_id": region_id_str, "reason": "spec_missing"})
            continue
        if spec_path.stat().st_mtime > blend_path.stat().st_mtime:
            stale.append(
                {
                    "region_id": region_id_str,
                    "reason": "spec_newer_than_blend",
                    "blend_mtime": blend_path.stat().st_mtime,
                    "spec_mtime": spec_path.stat().st_mtime,
                },
            )
    return ok({"stale": stale})


def find_lock_conflicts() -> dict[str, object]:
    """Return locks whose target is gone or whose pinned value drifted."""
    try:
        state = _state()
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    conflicts: list[dict[str, object]] = []
    for record in state.lock_store.records:
        target_id = str(record.region_id)
        region = next(
            (r for r in state.regions.values() if str(r.node_id) == target_id),
            None,
        )
        if region is None:
            conflicts.append(
                {
                    "lock_id": str(record.lock_id),
                    "kind": record.kind.value,
                    "reason": "target_missing",
                    "target": target_id,
                },
            )
            continue
        if record.kind is not LockKind.PROPERTY:
            continue
        payload = record.typed_payload()
        if not isinstance(payload, PropertyLockPayload):  # pragma: no cover - defensive
            continue
        live_doc = region.model_dump(mode="json")
        found, actual = _resolve_path(live_doc, payload.json_path)
        if not found or actual != payload.expected_value:
            conflicts.append(
                {
                    "lock_id": str(record.lock_id),
                    "kind": record.kind.value,
                    "reason": "expected_value_drift",
                    "json_path": payload.json_path,
                    "expected": payload.expected_value,
                    "actual": actual if found else None,
                },
            )
    return ok({"conflicts": conflicts})


def purge_orphans(*, dry_run: bool = True) -> dict[str, object]:
    """Delete orphan spec files. Defaults to dry-run."""
    try:
        state = _state()
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    orphans = _orphan_specs(state)
    removed: list[dict[str, object]] = []
    if dry_run:
        return ok({"dry_run": True, "would_remove": orphans, "removed": []})
    for entry in orphans:
        path_str = entry["path"]
        if not isinstance(path_str, str):  # pragma: no cover - dict shape is ours
            continue
        from pathlib import Path  # noqa: PLC0415

        path_obj = Path(path_str)
        # ``Path.unlink(missing_ok=True)`` is atomic at the FS level
        # (single ``unlinkat`` syscall); no temp-file dance needed for
        # deletion. Concurrent writers are not a concern here because
        # ProjectService is single-writer per process.
        path_obj.unlink(missing_ok=True)
        removed.append(entry)
    return ok({"dry_run": False, "would_remove": [], "removed": removed})


__all__ = [
    "find_lock_conflicts",
    "find_orphans",
    "find_stale_realizations",
    "purge_orphans",
]
