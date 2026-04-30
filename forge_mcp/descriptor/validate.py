"""Validation entry point for incoming descriptor payloads.

Returns a :class:`StructuredDescriptor` on success or a
:class:`ValidationFailure` carrying a flat list of issues the agent can
self-correct from. No exceptions cross this boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ValidationError

from forge_mcp.descriptor.schema import StreamCharacter, StructuredDescriptor

if TYPE_CHECKING:
    from pydantic_core import ErrorDetails

type JsonValue = str | int | float | bool | None | Mapping[str, JsonValue] | Sequence[JsonValue]
"""Recursive JSON type alias.

Used at every Forge boundary that accepts untrusted JSON, to satisfy
mypy's ``disallow_any_explicit`` without sprinkling ``Any``.
"""


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A single structured validation issue.

    Attributes:
        path: Dotted JSON path into the rejected payload, e.g. ``terrain.ruggedness``.
        message: Human-readable explanation suitable for surfacing back to the agent.
        code: Stable machine-readable code (Pydantic error type or a Forge-defined
            cross-field code such as ``hydrology.stream_required``).
    """

    path: str
    message: str
    code: str


@dataclass(frozen=True, slots=True)
class ValidationFailure:
    """Aggregate failure carrying all issues for one validation pass."""

    issues: tuple[ValidationIssue, ...]


def _issue_from_pydantic(err: ErrorDetails) -> ValidationIssue:
    loc = err.get("loc", ())
    path = ".".join(str(part) for part in loc)
    return ValidationIssue(
        path=path,
        message=str(err.get("msg", "")),
        code=str(err.get("type", "")),
    )


def _check_cross_field(descriptor: StructuredDescriptor) -> tuple[ValidationIssue, ...]:
    """Cross-field invariants the Pydantic models cannot express alone."""
    issues: list[ValidationIssue] = []

    terrain = descriptor.terrain
    if terrain.elevation_band is not None:
        low, high = terrain.elevation_band
        if low > high:
            issues.append(
                ValidationIssue(
                    path="terrain.elevation_band",
                    message="elevation_band low must be <= high",
                    code="terrain.elevation_band.inverted",
                ),
            )

    hydro = descriptor.hydrology
    if (
        hydro is not None
        and hydro.has_stream is True
        and (hydro.stream_character is None or hydro.stream_character is StreamCharacter.NONE)
    ):
        issues.append(
            ValidationIssue(
                path="hydrology.stream_character",
                message=("stream_character must be set (and not 'none') when has_stream is true"),
                code="hydrology.stream_required",
            ),
        )

    return tuple(issues)


def validate(payload: JsonValue) -> StructuredDescriptor | ValidationFailure:
    """Validate a JSON payload against :class:`StructuredDescriptor`.

    Args:
        payload: The raw JSON-shaped value (typically a ``dict`` from
            :func:`json.loads`). Top-level non-mapping values are rejected.

    Returns:
        A frozen :class:`StructuredDescriptor` on success, otherwise a
        :class:`ValidationFailure` aggregating every issue found in one
        pass. Cross-field invariants (inverted elevation band, hydrology
        consistency) are reported alongside Pydantic-level errors.
    """
    if not isinstance(payload, dict):
        return ValidationFailure(
            issues=(
                ValidationIssue(
                    path="",
                    message="payload must be a JSON object",
                    code="payload.not_object",
                ),
            ),
        )

    try:
        descriptor = StructuredDescriptor.model_validate(payload)
    except ValidationError as exc:
        return ValidationFailure(
            issues=tuple(_issue_from_pydantic(err) for err in exc.errors()),
        )

    cross = _check_cross_field(descriptor)
    if cross:
        return ValidationFailure(issues=cross)
    return descriptor
