"""Filesystem layout helpers for ``<project>/audits/``.

Architecture §3 reserves ``<project>/audits/`` for Phase-5 artefacts.
Phase 5 fills it with one JSON per verdict plus a per-project index:

::

    audits/
        _index.json                       # AuditIndexFile (cheap listing)
        <region_id>/
            audit_<12-hex>.json           # AuditVerdict body

The index is rebuilt on every ``record`` so the ``list_audits`` tool
does not have to walk every region directory just to summarise them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from forge_mcp.audit.verdict import AuditId
    from forge_mcp.project.schemas import RegionId

_INDEX_FILENAME = "_index.json"
"""Cache file that holds ``{audit_id: AuditSummary}``."""


class AuditPaths:
    """Resolve every per-project audit-related path.

    Mirrors :class:`forge_mcp.project.service.ProjectPaths` so callers
    do not have to thread literals through the codebase. Constructed
    against the project root; pure path arithmetic — never touches the
    filesystem.
    """

    def __init__(self, audits_root: Path) -> None:
        """Bind the helper to ``<project>/audits/``."""
        self._root = audits_root

    @property
    def root(self) -> Path:
        """Return the ``<project>/audits/`` directory path."""
        return self._root

    @property
    def index_path(self) -> Path:
        """Return the path of the ``_index.json`` cache file."""
        return self._root / _INDEX_FILENAME

    def region_dir(self, region_id: RegionId) -> Path:
        """Return the per-region audit folder."""
        return self._root / region_id

    def verdict_path(self, region_id: RegionId, audit_id: AuditId) -> Path:
        """Return the on-disk path for one verdict body."""
        return self.region_dir(region_id) / f"{audit_id}.json"


__all__ = ["AuditPaths"]
