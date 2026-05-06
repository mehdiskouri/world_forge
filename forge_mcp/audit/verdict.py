"""Pydantic schema for audit verdicts (Phase 5 Stage D).

The verdict is the only on-disk audit artefact in v1. It is
content-addressed via :func:`new_audit_id` so identical verdicts
collapse to one identifier (cheap dedupe across re-runs).

Schema version 1.0 freezes:

* exactly four dimensions (``descriptor_coherence``,
  ``geometric_validity``, ``render_quality``, ``spec_alignment``),
* the three-valued verdict literal ``pass`` / ``fail`` / ``warn``,
* the ``SubagentContext`` shape recording which client produced the
  verdict and whether it ran in isolation.

Future schema bumps are documented in [phase5.md](../../AGENT/dev_phases/phase5.md)
"Confirmed decisions".
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime  # noqa: TC003 - Pydantic field type at runtime
from typing import ClassVar, Final, Literal, NewType, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from forge_mcp.project.schemas import (  # noqa: TC001 - Pydantic field type at runtime
    RegionId,
    SpecId,
)

AuditId = NewType("AuditId", str)
"""Audit-record identifier: ``audit_<12-hex>`` over the canonical body."""

AuditVerdictValue = Literal["pass", "fail", "warn"]
"""Allowed verdict literal for both the top-level and per-dimension fields."""

AUDIT_VERDICT_VALUES: Final[tuple[AuditVerdictValue, ...]] = get_args(AuditVerdictValue)
"""Runtime tuple of every valid verdict value (used by tests + tools)."""

AuditDimensionName = Literal[
    "descriptor_coherence",
    "geometric_validity",
    "render_quality",
    "spec_alignment",
]
"""Closed set of audit dimensions for v1.

* ``descriptor_coherence`` — extracted descriptor matches user intent.
* ``geometric_validity`` — mesh, polygon, scale plausible.
* ``render_quality`` — preview not broken/black/clipped.
* ``spec_alignment`` — realizer outputs honour spec params.
"""

AUDIT_DIMENSION_NAMES: Final[tuple[AuditDimensionName, ...]] = get_args(AuditDimensionName)
"""Runtime tuple of every dimension; ``len == 4`` and order is canonical."""

AUDIT_SCHEMA_VERSION: Final = "1.0"
"""Frozen schema version; bumping requires a migration note in docs/audit.md."""

_AUDIT_ID_DIGEST_BYTES: Final[int] = 6
"""6 bytes -> 12 hex chars; matches the spec_id pattern in Phase 3."""

_SUMMARY_MAX_CHARS: Final[int] = 500
"""Audit summaries are short; long prose belongs in dimension evidence."""

_CONFIDENCE_MIN: Final[float] = 0.0
_CONFIDENCE_MAX: Final[float] = 1.0


class SubagentContext(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Provenance metadata recorded with every verdict.

    Forge cannot enforce isolation (the subagent runs in the agent
    client, not in Forge); ``isolated`` is a best-effort claim
    reported by the client. ``tool_calls_observed`` lists the tool
    names the subagent actually called so a reviewer can spot-check
    that no mutation tools (``forge.generate_region``,
    ``forge.reroll_seed``, locks, etc.) leaked into the audit context.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    client_name: str = Field(min_length=1, max_length=64)
    isolated: bool
    tool_calls_observed: tuple[str, ...] = ()


class AuditDimension(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """One scored axis of the verdict."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    name: AuditDimensionName
    verdict: AuditVerdictValue
    confidence: float = Field(ge=_CONFIDENCE_MIN, le=_CONFIDENCE_MAX)
    evidence: tuple[str, ...] = ()


class AuditVerdict(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Persisted audit-verdict record (frozen, ``extra="forbid"``)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    audit_id: AuditId
    schema_version: Literal["1.0"] = AUDIT_SCHEMA_VERSION
    region_id: RegionId
    spec_id: SpecId
    verdict: AuditVerdictValue
    dimensions: tuple[AuditDimension, ...]
    summary: str = Field(min_length=1, max_length=_SUMMARY_MAX_CHARS)
    created_at: datetime
    subagent_context: SubagentContext

    @model_validator(mode="after")
    def _validate_dimension_set(self) -> AuditVerdict:
        """Reject verdicts that don't cover exactly the canonical dimensions."""
        observed = tuple(dim.name for dim in self.dimensions)
        if len(observed) != len(set(observed)):
            msg = f"audit dimensions must be unique, got {observed}"
            raise ValueError(msg)
        if set(observed) != set(AUDIT_DIMENSION_NAMES):
            missing = sorted(set(AUDIT_DIMENSION_NAMES) - set(observed))
            extra = sorted(set(observed) - set(AUDIT_DIMENSION_NAMES))
            msg = (
                f"audit dimensions must be exactly {sorted(AUDIT_DIMENSION_NAMES)}; "
                f"missing={missing}, extra={extra}"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_audit_id_matches_body(self) -> AuditVerdict:
        """Verify the recorded ``audit_id`` is the content hash of the body."""
        expected = new_audit_id(_audit_id_payload(self))
        if self.audit_id != expected:
            msg = (
                f"audit_id mismatch: got {self.audit_id!r}, "
                f"expected {expected!r} for the canonical body"
            )
            raise ValueError(msg)
        return self


def _audit_id_payload(verdict: AuditVerdict) -> dict[str, object]:
    """Return the dict that ``new_audit_id`` hashes to derive the id.

    Excludes ``audit_id`` itself (so the hash is a true content
    address) and the ``created_at`` timestamp (so re-running the same
    audit at a different second still collapses to one record).
    """
    body = verdict.model_dump(mode="json")
    body.pop("audit_id", None)
    body.pop("created_at", None)
    return body


def new_audit_id(body: dict[str, object]) -> AuditId:
    """Derive a deterministic ``audit_<12-hex>`` id from a verdict body."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    digest = hashlib.blake2b(
        canonical.encode("utf-8"),
        digest_size=_AUDIT_ID_DIGEST_BYTES,
    ).hexdigest()
    return AuditId(f"audit_{digest}")


def audit_verdict_json_schema() -> dict[str, object]:
    """Return the published JSON Schema for :class:`AuditVerdict`.

    Wraps ``model_json_schema`` so external callers (the
    ``forge.get_audit_schema`` MCP tool, the ``forge-schema-export``
    CLI, the ``forge.audit`` SKILL.md byte-identity test) get the same
    canonical artifact.
    """
    schema = AuditVerdict.model_json_schema()
    schema["title"] = "ForgeAuditVerdict"
    return schema


__all__ = [
    "AUDIT_DIMENSION_NAMES",
    "AUDIT_SCHEMA_VERSION",
    "AUDIT_VERDICT_VALUES",
    "AuditDimension",
    "AuditDimensionName",
    "AuditId",
    "AuditVerdict",
    "AuditVerdictValue",
    "SubagentContext",
    "audit_verdict_json_schema",
    "new_audit_id",
]
