"""Read shipped SKILL.md files into validated :class:`SkillRecord` objects.

The loader uses :mod:`importlib.resources` so it works whether Forge
is installed from the wheel or run from the source checkout. It owns
the only frontmatter parser in the project; everything else (tests,
CLI, MCP tools) goes through :func:`load_skill` or :func:`iter_skills`.

Frontmatter dialect (intentionally tiny, no PyYAML dependency):

* Block delimited by ``---`` on its own line at top of file and again
  before the body.
* Each non-blank line is ``key: <json-literal>``.
* Strings must be JSON-quoted (``"..."``).
* Booleans are ``true`` / ``false``.
* Lists use ``[...]`` JSON syntax.

Anything else raises :class:`SkillFrontmatterError` with a precise
``(skill_name, line_number, reason)`` triple so the validation tests
can quote the failure exactly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import TYPE_CHECKING, Final

from pydantic import ValidationError

from forge_mcp.skills._schema import SkillFrontmatter

if TYPE_CHECKING:
    from collections.abc import Iterator
    from importlib.resources.abc import Traversable

SHIPPED_SKILL_NAMES: Final[tuple[str, ...]] = (
    "forge.plan",
    "forge.visualize",
    "forge.audit",
    "forge.cleanup",
    "forge.connect",
)
"""Canonical Phase-5 skill ordering. The CLI / MCP tools preserve it."""

_FRONTMATTER_DELIMITER: Final[str] = "---"


class SkillError(Exception):
    """Base class for skill-loader exceptions."""


class SkillNotFoundError(SkillError):
    """Raised when a requested skill name is not shipped."""


class SkillFrontmatterError(SkillError):
    """Raised when a SKILL.md frontmatter block fails to parse or validate."""

    def __init__(self, skill_name: str, line_number: int, reason: str) -> None:
        """Build a frontmatter error pointing at one offending line.

        Args:
            skill_name: Skill identifier (folder name).
            line_number: 1-based line number inside the SKILL.md file.
            reason: Human-readable diagnosis.
        """
        self.skill_name = skill_name
        self.line_number = line_number
        self.reason = reason
        super().__init__(f"{skill_name}:{line_number}: {reason}")


@dataclass(frozen=True, slots=True)
class SkillRecord:
    """One shipped skill: parsed frontmatter plus raw markdown body.

    Attributes:
        frontmatter: Validated :class:`SkillFrontmatter`.
        body_markdown: Markdown after the closing ``---`` delimiter,
            verbatim. Includes a single leading newline trim so the
            body starts at the first content line.
        embedded_assets: Mapping of sibling-file basenames to their
            ``utf-8``-decoded contents (for example
            ``forge.plan/eval_set.json``). Empty when the skill folder
            contains only ``SKILL.md``.
    """

    frontmatter: SkillFrontmatter
    body_markdown: str
    embedded_assets: dict[str, str]


def skill_root() -> Traversable:
    """Return the :mod:`importlib.resources` traversable for the skills package."""
    return files("forge_mcp.skills")


def iter_skills() -> Iterator[SkillRecord]:
    """Yield every shipped skill in :data:`SHIPPED_SKILL_NAMES` order."""
    for name in SHIPPED_SKILL_NAMES:
        yield load_skill(name)


def load_skill(name: str) -> SkillRecord:
    """Load and validate one shipped skill.

    Args:
        name: Skill identifier (e.g. ``"forge.plan"``).

    Returns:
        Parsed :class:`SkillRecord`.

    Raises:
        SkillNotFoundError: If the named skill is not shipped.
        SkillFrontmatterError: If the SKILL.md frontmatter is malformed
            or fails validation.
    """
    if name not in SHIPPED_SKILL_NAMES:
        msg = f"unknown skill {name!r}; shipped: {list(SHIPPED_SKILL_NAMES)}"
        raise SkillNotFoundError(msg)
    folder = skill_root() / name
    skill_md = folder / "SKILL.md"
    if not skill_md.is_file():
        msg = f"shipped skill {name!r} is missing SKILL.md"
        raise SkillNotFoundError(msg)
    text = skill_md.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(name, text)
    assets = _load_embedded_assets(folder, exclude={"SKILL.md"})
    return SkillRecord(frontmatter=frontmatter, body_markdown=body, embedded_assets=assets)


def _split_frontmatter(name: str, text: str) -> tuple[SkillFrontmatter, str]:
    """Split a SKILL.md into validated frontmatter and raw body markdown."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_DELIMITER:
        raise SkillFrontmatterError(
            name,
            1,
            f"first line must be {_FRONTMATTER_DELIMITER!r}",
        )
    closing_index: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == _FRONTMATTER_DELIMITER:
            closing_index = index
            break
    if closing_index is None:
        raise SkillFrontmatterError(
            name,
            len(lines),
            f"missing closing {_FRONTMATTER_DELIMITER!r} delimiter",
        )
    frontmatter_lines = lines[1:closing_index]
    payload = _parse_frontmatter_lines(name, frontmatter_lines, line_offset=2)
    try:
        frontmatter = SkillFrontmatter.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(part) for part in first["loc"]) or "<root>"
        raise SkillFrontmatterError(name, 1, f"{loc}: {first['msg']}") from exc
    if frontmatter.name != name:
        raise SkillFrontmatterError(
            name,
            1,
            f"frontmatter name {frontmatter.name!r} must match folder name {name!r}",
        )
    body_text = "\n".join(lines[closing_index + 1 :])
    body_text = body_text.lstrip("\n")
    return frontmatter, body_text


def _parse_frontmatter_lines(
    name: str,
    lines: list[str],
    *,
    line_offset: int,
) -> dict[str, object]:
    """Parse the frontmatter line block into a Python dict.

    Each line must be ``key: <json-literal>``. Blank lines are skipped.
    Anything else is rejected with a precise line-number error.
    """
    result: dict[str, object] = {}
    for relative_index, raw_line in enumerate(lines):
        line_number = line_offset + relative_index
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if ":" not in line:
            raise SkillFrontmatterError(
                name,
                line_number,
                "frontmatter line must be 'key: <json-literal>'",
            )
        key, _, value_text = line.partition(":")
        key = key.strip()
        if not key:
            raise SkillFrontmatterError(name, line_number, "missing key before ':'")
        if key in result:
            raise SkillFrontmatterError(name, line_number, f"duplicate key {key!r}")
        value_text = value_text.strip()
        if not value_text:
            raise SkillFrontmatterError(
                name,
                line_number,
                f"missing JSON literal value for key {key!r}",
            )
        try:
            value = json.loads(value_text)
        except json.JSONDecodeError as exc:
            raise SkillFrontmatterError(
                name,
                line_number,
                f"value for {key!r} is not a JSON literal: {exc.msg}",
            ) from exc
        result[key] = value
    return result


def _load_embedded_assets(folder: Traversable, *, exclude: set[str]) -> dict[str, str]:
    """Read every text-mode sibling file alongside ``SKILL.md``."""
    assets: dict[str, str] = {}
    for child in folder.iterdir():
        if not child.is_file():
            continue
        if child.name in exclude:
            continue
        assets[child.name] = child.read_text(encoding="utf-8")
    return assets


__all__ = [
    "SHIPPED_SKILL_NAMES",
    "SkillError",
    "SkillFrontmatterError",
    "SkillNotFoundError",
    "SkillRecord",
    "iter_skills",
    "load_skill",
    "skill_root",
]
