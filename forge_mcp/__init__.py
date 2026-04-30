"""Forge — agent-native worldbuilding MCP server (v1).

Public package marker. Phase 0 contains only the version constant so the
toolchain (uv, ruff, mypy, pytest) has something concrete to operate on.
Real modules land starting in Phase 2 per ``AGENT/ROADMAP.md``.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__: str = "0.0.0"
