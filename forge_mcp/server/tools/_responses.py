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


__all__ = ["fail", "ok"]
