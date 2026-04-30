"""Centralized typing primitives shared across Forge.

Phase 1 introduced a recursive ``JsonValue`` alias at the RPC boundary
(:mod:`forge_mcp.realize.rpc`); Phase 2 consolidates it here so every
JSON-touching surface (project schemas, history payloads, RPC params)
uses the same definition. Strict type discipline is non-negotiable
(``.github/instructions.md`` §2): no naked ``Any`` is allowed at any
JSON boundary.

The alias is declared with the PEP 695 ``type`` statement so Pydantic
v2 can resolve the recursive reference without overflowing the type
evaluator (``RecursionError`` otherwise — see Pydantic 2.13 docs on
named recursive types).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

type JsonValue = str | int | float | bool | None | Sequence[JsonValue] | Mapping[str, JsonValue]
"""Recursive alias for a value that round-trips through JSON.

``Sequence`` is intentionally narrower than ``Iterable`` so values like
generators are rejected at the boundary; mappings must be keyed by
``str`` (JSON does not support non-string keys).
"""
