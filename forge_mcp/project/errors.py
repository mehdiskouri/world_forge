"""Shared base exception for the :mod:`forge_mcp.project` package.

Lives in its own module so cross-cutting helpers
(e.g. :mod:`forge_mcp.project.lock_enforcement`) can inherit from it
without pulling in :mod:`forge_mcp.project.service` and triggering an
import cycle.
"""

from __future__ import annotations


class ProjectError(Exception):
    """Base class for all project-management failures."""


__all__ = ["ProjectError"]
