"""Persistence layer for audit verdicts (Phase 5 Stage D).

The service is intentionally decoupled from :class:`ProjectService`:
it only needs the ``audits/`` directory plus an opaque
``history_appender`` callable. That keeps the unit tests fast (no
project bootstrap required) and lets the MCP tool layer wire the two
together without circular imports.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic field type at runtime
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from forge_mcp._io.atomic import write_json
from forge_mcp.audit.paths import AuditPaths
from forge_mcp.audit.verdict import (
    AuditId,
    AuditVerdict,
    AuditVerdictValue,
)
from forge_mcp.project.schemas import (  # noqa: TC001 - Pydantic field type at runtime
    RegionId,
    SpecId,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class AuditError(Exception):
    """Base class for audit-layer errors."""


class AuditValidationError(AuditError):
    """Raised when a verdict body fails Pydantic validation."""

    def __init__(self, *, reason: str, field: str | None = None) -> None:
        """Carry both a human reason and the offending JSON pointer."""
        suffix = f" (field={field})" if field else ""
        super().__init__(f"{reason}{suffix}")
        self.reason = reason
        self.field = field


class AuditNotFoundError(AuditError):
    """Raised when ``get_audit`` cannot locate an audit by id."""

    def __init__(self, audit_id: AuditId) -> None:
        """Tell the caller which id was missing."""
        super().__init__(f"audit {audit_id!r} not found")
        self.audit_id = audit_id


class AuditSummary(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Compact audit row exposed by ``forge.list_audits``.

    Holding this in :file:`_index.json` lets ``list_audits`` answer in
    O(1) disk reads instead of walking ``audits/<region>/*.json``.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    audit_id: AuditId
    region_id: RegionId
    spec_id: SpecId
    verdict: AuditVerdictValue
    created_at: datetime


class AuditIndexFile(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """On-disk shape of ``audits/_index.json``."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    summaries: tuple[AuditSummary, ...] = ()


HistoryAppender = "Callable[[RegionId, SpecId, AuditId, AuditVerdictValue], object]"
"""Callback type alias used in :meth:`AuditService.record`."""


class AuditService:
    """Atomic-write + index-maintenance over ``<project>/audits/``."""

    def __init__(
        self,
        audits_root: Path,
        *,
        history_appender: Callable[
            [RegionId, SpecId, AuditId, AuditVerdictValue],
            object,
        ]
        | None = None,
    ) -> None:
        """Bind to one project's ``audits/`` directory.

        ``history_appender`` is invoked after a successful ``record``
        with ``(region_id, spec_id, audit_id, verdict)`` so the caller
        can wire it to :class:`HistoryLog`. Optional so unit tests can
        skip history bookkeeping.
        """
        self._paths = AuditPaths(audits_root)
        self._history_appender = history_appender

    @property
    def paths(self) -> AuditPaths:
        """Return the bound :class:`AuditPaths` helper."""
        return self._paths

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------
    def record(self, verdict: AuditVerdict) -> AuditVerdict:
        """Persist one verdict atomically and update the index.

        Returns the validated verdict (Pydantic round-trip ensures the
        on-disk and in-memory shapes agree). If ``history_appender`` is
        bound, the caller's append is invoked exactly once after the
        verdict file is on disk.
        """
        # ``AuditVerdict`` is frozen + ``extra="forbid"``; passing it
        # back through model_validate is a cheap defensive round-trip.
        try:
            checked = AuditVerdict.model_validate(verdict.model_dump(mode="json"))
        except ValidationError as exc:  # pragma: no cover  # defensive
            raise AuditValidationError(reason=str(exc)) from exc

        region_dir = self._paths.region_dir(checked.region_id)
        region_dir.mkdir(parents=True, exist_ok=True)
        write_json(self._paths.verdict_path(checked.region_id, checked.audit_id), checked)
        self._upsert_index(checked)
        if self._history_appender is not None:
            self._history_appender(
                checked.region_id,
                checked.spec_id,
                checked.audit_id,
                checked.verdict,
            )
        return checked

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def list_audits(
        self,
        *,
        region_id: RegionId | None = None,
    ) -> tuple[AuditSummary, ...]:
        """Return every recorded summary, optionally filtered by region."""
        index = self._load_index()
        summaries = index.summaries
        if region_id is not None:
            summaries = tuple(s for s in summaries if s.region_id == region_id)
        # ``created_at`` ascending matches the on-disk insertion order
        # but defends against manual edits to ``_index.json``.
        return tuple(sorted(summaries, key=lambda s: (s.created_at, s.audit_id)))

    def get(self, audit_id: AuditId) -> AuditVerdict:
        """Return the full verdict for ``audit_id``.

        Walks the index to find the owning region, then loads the
        per-region JSON. Raises :class:`AuditNotFoundError` if either
        the index lookup or the disk read fails.
        """
        index = self._load_index()
        match = next((s for s in index.summaries if s.audit_id == audit_id), None)
        if match is None:
            raise AuditNotFoundError(audit_id)
        path = self._paths.verdict_path(match.region_id, audit_id)
        if not path.is_file():
            raise AuditNotFoundError(audit_id)
        return AuditVerdict.model_validate_json(path.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _load_index(self) -> AuditIndexFile:
        path = self._paths.index_path
        if not path.is_file():
            return AuditIndexFile()
        try:
            return AuditIndexFile.model_validate_json(path.read_text(encoding="utf-8"))
        except ValidationError as exc:
            msg = f"corrupt audit index at {path}: {exc}"
            raise AuditValidationError(reason=msg) from exc

    def _upsert_index(self, verdict: AuditVerdict) -> None:
        existing = self._load_index().summaries
        without_dup = tuple(s for s in existing if s.audit_id != verdict.audit_id)
        summary = AuditSummary(
            audit_id=verdict.audit_id,
            region_id=verdict.region_id,
            spec_id=verdict.spec_id,
            verdict=verdict.verdict,
            created_at=verdict.created_at,
        )
        next_index = AuditIndexFile(summaries=(*without_dup, summary))
        self._paths.root.mkdir(parents=True, exist_ok=True)
        write_json(self._paths.index_path, next_index)


__all__ = [
    "AuditError",
    "AuditIndexFile",
    "AuditNotFoundError",
    "AuditService",
    "AuditSummary",
    "AuditValidationError",
    "HistoryAppender",
]
