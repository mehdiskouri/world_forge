"""MCP lock tools (Phase 7 Stage A).

Exposes the four mutating lock operations:

* ``forge.lock_property`` -- pin a JSON path on a region.
* ``forge.lock_feature`` -- capture a heightmap rectangle so it survives
  seed rerolls.
* ``forge.lock_region`` -- short-circuit regeneration entirely.
* ``forge.unlock`` -- remove a lock by id.

All tools return the standard ``{"ok": True, "result": ...}`` /
``{"ok": False, "error": {...}}`` envelope. Domain failures from the
service layer (:class:`UnknownRegionError`,
:class:`LockTargetNotFoundError`,
:class:`OverlappingFeatureLockError`, :class:`UnknownLockError`,
:class:`DuplicateLockError`) are mapped to stable error codes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic import ValidationError

from forge_mcp.project.locks import DuplicateLockError
from forge_mcp.project.schemas import LockId, RegionId
from forge_mcp.project.service import (
    LockTargetNotFoundError,
    NoOpenProjectError,
    OverlappingFeatureLockError,
    UnknownLockError,
    UnknownRegionError,
)
from forge_mcp.server.tools import get_service
from forge_mcp.server.tools._responses import fail, ok

if TYPE_CHECKING:
    from forge_mcp.project.schemas import LockRecord

_BBOX_LEN: Final[int] = 4


def _ok_lock(record: LockRecord) -> dict[str, object]:
    return ok({"lock": record.model_dump(mode="json")})


def _coerce_bbox(value: object) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != _BBOX_LEN:
        msg = "bbox_world must be a 4-element list [x0, y0, x1, y1]"
        raise TypeError(msg)
    out: list[float] = []
    for component in value:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            msg = "bbox_world components must be numbers"
            raise TypeError(msg)
        out.append(float(component))
    return (out[0], out[1], out[2], out[3])


def lock_property(region_id: str, json_path: str) -> dict[str, object]:
    """Create a property lock pinning ``json_path`` on ``region_id``."""
    try:
        record = get_service().create_property_lock(
            region_id=RegionId(region_id),
            json_path=json_path,
        )
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except UnknownRegionError as exc:
        return fail("unknown_region", str(exc))
    except LockTargetNotFoundError as exc:
        return fail("lock_target_not_found", str(exc))
    except DuplicateLockError as exc:
        return fail("duplicate_lock", str(exc))
    except ValidationError as exc:
        return fail("invalid_lock_payload", str(exc))
    return _ok_lock(record)


def lock_feature(region_id: str, bbox_world: object) -> dict[str, object]:  # noqa: PLR0911 - one branch per service exception kind
    """Capture a heightmap patch covering ``bbox_world`` as a feature lock."""
    try:
        bbox = _coerce_bbox(bbox_world)
    except TypeError as exc:
        return fail("invalid_bbox", str(exc))
    try:
        record = get_service().create_feature_lock(
            region_id=RegionId(region_id),
            bbox_world=bbox,
        )
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except UnknownRegionError as exc:
        return fail("unknown_region", str(exc))
    except LockTargetNotFoundError as exc:
        return fail("lock_target_not_found", str(exc))
    except OverlappingFeatureLockError as exc:
        return fail("overlapping_feature_lock", str(exc))
    except DuplicateLockError as exc:
        return fail("duplicate_lock", str(exc))
    except ValidationError as exc:
        return fail("invalid_lock_payload", str(exc))
    return _ok_lock(record)


def lock_region(region_id: str) -> dict[str, object]:
    """Create a region lock that short-circuits regeneration."""
    try:
        record = get_service().create_region_lock(region_id=RegionId(region_id))
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except UnknownRegionError as exc:
        return fail("unknown_region", str(exc))
    except DuplicateLockError as exc:
        return fail("duplicate_lock", str(exc))
    except ValidationError as exc:
        return fail("invalid_lock_payload", str(exc))
    return _ok_lock(record)


def unlock(lock_id: str) -> dict[str, object]:
    """Remove the lock identified by ``lock_id``."""
    try:
        record = get_service().remove_lock(LockId(lock_id))
    except NoOpenProjectError as exc:
        return fail("no_open_project", str(exc))
    except UnknownLockError as exc:
        return fail("unknown_lock", str(exc))
    return _ok_lock(record)


__all__ = ["lock_feature", "lock_property", "lock_region", "unlock"]
