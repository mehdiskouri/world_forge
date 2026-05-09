"""Shared response shapes for the v1 MCP tool surface.

Every tool returns a JSON-serialisable ``dict[str, object]`` shaped as
either::

    {"ok": True,  "result": <payload>}
    {"ok": False, "error": {"code": "...", "message": "...", "details": {...}}}

This is the same envelope the agent already learned from Phase 1's
``forge.ping`` and lets the host distinguish "tool ran, returned a
domain failure" from "tool raised, MCP transport error" without
guessing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forge_mcp.project.lock_enforcement import LockViolationError


def ok(result: object) -> dict[str, object]:
    """Return a successful tool response envelope."""
    return {"ok": True, "result": result}


def fail(
    code: str,
    message: str,
    *,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return a structured-error tool response envelope."""
    error: dict[str, object] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"ok": False, "error": error}


def lock_violation_envelope(exc: LockViolationError) -> dict[str, object]:
    """Map a :class:`LockViolationError` onto the standard ``lock_violation`` envelope.

    Centralised so every Phase-7 mutator surface produces the same
    ``details`` shape (``lock_id``, ``json_path``, ``expected``,
    ``actual``).
    """
    return fail(
        "lock_violation",
        str(exc),
        details={
            "lock_id": str(exc.lock_id),
            "json_path": exc.json_path,
            "expected": exc.expected,
            "actual": exc.actual,
        },
    )


__all__ = ["fail", "lock_violation_envelope", "ok"]
