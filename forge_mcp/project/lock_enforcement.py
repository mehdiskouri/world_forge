"""Property-lock enforcement (Phase 7 Stage B).

Stage A persists ``PROPERTY`` locks alongside the rest of the project
state. This module is the single chokepoint that *enforces* them: every
service mutator that updates a node calls
:func:`check_property_locks` with the JSON-shaped before/after snapshot
of the affected node, and the function raises
:class:`LockViolationError` on the first lock whose ``json_path`` value
no longer matches its captured ``expected_value``.

The check is intentionally pre-persistence: callers run it after
applying the in-memory ``model_copy`` but *before* writing the new node
to disk. That way a violation needs no rollback - the on-disk state was
never touched.

Locks today key on :class:`forge_mcp.project.schemas.RegionId`; the
node-id argument is accepted as a free-form string so future Phase-7
work can target sub-regions, environments, or material archetypes
without re-shaping the call sites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_mcp.project.errors import ProjectError
from forge_mcp.project.schemas import LockKind, PropertyLockPayload

if TYPE_CHECKING:
    from forge_mcp._types import JsonValue
    from forge_mcp.project.schemas import LockId, NodeId
    from forge_mcp.project.service import ProjectState


class LockViolationError(ProjectError):
    """Raised when a mutation would change the value pinned by a property lock.

    Carries the offending ``lock_id``, the ``json_path`` it pins, the
    ``expected`` value captured at lock time, and the ``actual`` value
    the mutation would produce. The MCP tool layer maps this onto the
    ``lock_violation`` envelope code.
    """

    def __init__(
        self,
        lock_id: LockId,
        json_path: str,
        expected: JsonValue,
        actual: JsonValue,
    ) -> None:
        """Capture the violated lock's identifying fields and build the message."""
        self.lock_id = lock_id
        self.json_path = json_path
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"property lock {lock_id!r} pins {json_path!r} to {expected!r}; "
            f"mutation would change it to {actual!r}",
        )


def _resolve_path(doc: JsonValue, json_path: str) -> tuple[bool, JsonValue]:
    """Walk ``doc`` along the dot-separated ``json_path``.

    Returns ``(found, value)``. ``found`` is ``False`` when any segment
    is absent or traverses a non-mapping value, in which case
    ``value`` is ``None`` and callers should not compare against it.
    Distinguishing the absent case lets callers treat a missing path
    as a violation rather than a coincidental ``None`` match.
    """
    node: JsonValue = doc
    for segment in json_path.split("."):
        if not isinstance(node, dict) or segment not in node:
            return False, None
        node = node[segment]
    return True, node


def check_property_locks(
    state: ProjectState,
    node_id: NodeId,
    before_doc: JsonValue,  # noqa: ARG001 - kept for API symmetry; future use
    after_doc: JsonValue,
) -> None:
    """Raise :class:`LockViolationError` on the first violated property lock.

    A lock is violated when the value at its ``json_path`` in
    ``after_doc`` differs from the lock's ``expected_value`` (or the
    path is missing entirely from ``after_doc``). ``before_doc`` is
    accepted but currently unused; it is part of the contract so future
    "only fail when the path actually changes" semantics can be added
    without touching every call site.
    """
    target = str(node_id)
    for record in state.lock_store.records:
        if record.kind is not LockKind.PROPERTY:
            continue
        if str(record.region_id) != target:
            continue
        payload = record.typed_payload()
        if not isinstance(payload, PropertyLockPayload):  # pragma: no cover - defensive
            continue
        actual_found, actual = _resolve_path(after_doc, payload.json_path)
        if not actual_found or actual != payload.expected_value:
            raise LockViolationError(
                lock_id=record.lock_id,
                json_path=payload.json_path,
                expected=payload.expected_value,
                actual=actual,
            )


__all__ = ["LockViolationError", "check_property_locks"]
