"""``forge-skills`` CLI: list / install / export shipped skills.

Phase 5 Stage A. Default ``install`` target is
``~/.claude/skills/forge.<name>/SKILL.md`` because Claude Code is the
only client today with a clean isolated-context subagent primitive
(see [phase5.md](../../AGENT/dev_phases/phase5.md) "Confirmed
decisions"). Cursor / Copilot users invoke ``forge-skills export``
and paste the bundle into their system prompt.

All filesystem writes go through :mod:`forge_mcp._io.atomic` so a
crashed install leaves either the previous SKILL.md intact or no
file at all.
"""

from __future__ import annotations

import argparse
import sys
from importlib.resources import as_file
from pathlib import Path
from typing import TYPE_CHECKING, Final

from forge_mcp._io.atomic import atomic_write_text
from forge_mcp.skills.loader import (
    SHIPPED_SKILL_NAMES,
    SkillRecord,
    iter_skills,
    load_skill,
    skill_root,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from _typeshed import SupportsWrite

_DEFAULT_CLIENT: Final[str] = "claude"
"""Stage A only ships a Claude-Code installer; other clients use export."""

_SUPPORTED_CLIENTS: Final[tuple[str, ...]] = ("claude",)


def _claude_skills_dir() -> Path:
    """Default install root for Claude Code (``~/.claude/skills``)."""
    return Path.home() / ".claude" / "skills"


def _print_skill_list(stream: SupportsWrite[str]) -> None:
    """Emit a stable, human-readable listing of every shipped skill."""
    for record in iter_skills():
        line = (
            f"{record.frontmatter.name}  "
            f"v{record.frontmatter.version}  "
            f"{record.frontmatter.description}"
        )
        print(line, file=stream)


def _install_skill(record: SkillRecord, dest_root: Path, *, force: bool) -> Path:
    """Install one skill folder under ``dest_root``; return the SKILL.md path."""
    target_dir = dest_root / record.frontmatter.name
    target_dir.mkdir(parents=True, exist_ok=True)
    folder = skill_root() / record.frontmatter.name
    written: Path | None = None
    for child_name in (*record.embedded_assets.keys(), "SKILL.md"):
        target = target_dir / child_name
        if target.exists() and not force:
            msg = f"refusing to overwrite {target}; pass --force to replace existing skill files"
            raise FileExistsError(msg)
        with as_file(folder / child_name) as src:
            atomic_write_text(target, src.read_text(encoding="utf-8"))
        if child_name == "SKILL.md":
            written = target
    if written is None:  # pragma: no cover  # defensive
        msg = f"internal error: SKILL.md not written for {record.frontmatter.name}"
        raise RuntimeError(msg)
    return written


def _cmd_list(_args: argparse.Namespace) -> int:
    _print_skill_list(sys.stdout)
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    client: str = args.client
    if client not in _SUPPORTED_CLIENTS:
        print(  # noqa: T201 - CLI output
            f"forge-skills: client {client!r} requires manual paste; "
            f"run 'forge-skills export --out PATH' and follow docs/skills.md",
            file=sys.stderr,
        )
        return 2
    dest = Path(args.dest) if args.dest is not None else _claude_skills_dir()
    dest.mkdir(parents=True, exist_ok=True)
    for record in iter_skills():
        path = _install_skill(record, dest, force=args.force)
        print(f"installed {record.frontmatter.name} -> {path}")  # noqa: T201 - CLI output
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    out = Path(args.out)
    sections: list[str] = []
    for record in iter_skills():
        front = record.frontmatter
        sections.append(
            f"# {front.name} v{front.version}\n\n"
            f"> {front.description}\n\n"
            f"{record.body_markdown.rstrip()}\n",
        )
    bundle = "\n\n---\n\n".join(sections) + "\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, bundle)
    print(f"wrote bundle with {len(SHIPPED_SKILL_NAMES)} skills -> {out}")  # noqa: T201 - CLI output
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    record = load_skill(args.name)
    sys.stdout.write(record.body_markdown)
    if not record.body_markdown.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forge-skills",
        description=(
            "List, install, or export the SKILL.md files Forge ships under ``forge_mcp/skills/``."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List shipped skills.")
    list_parser.set_defaults(func=_cmd_list)

    install_parser = sub.add_parser(
        "install",
        help="Copy shipped skills into the agent client skill directory.",
    )
    install_parser.add_argument(
        "--client",
        default=_DEFAULT_CLIENT,
        help=(
            "Agent client name. Stage A supports 'claude' (Claude Code) only; "
            "other clients require manual paste via 'export'."
        ),
    )
    install_parser.add_argument(
        "--dest",
        default=None,
        help=("Override the install directory. Defaults to ~/.claude/skills/ for client=claude."),
    )
    install_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing skill files instead of aborting.",
    )
    install_parser.set_defaults(func=_cmd_install)

    export_parser = sub.add_parser(
        "export",
        help="Write every shipped skill body to a single bundle file for manual paste.",
    )
    export_parser.add_argument(
        "--out",
        required=True,
        help="Destination bundle path (atomic write).",
    )
    export_parser.set_defaults(func=_cmd_export)

    show_parser = sub.add_parser("show", help="Print one skill body to stdout.")
    show_parser.add_argument("name", help="Skill identifier, e.g. forge.plan")
    show_parser.set_defaults(func=_cmd_show)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``forge-skills`` CLI."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    func: object = args.func
    if not callable(func):  # pragma: no cover  # defensive
        msg = "argparse subcommand did not register a callable"
        raise TypeError(msg)
    return int(func(args))


if __name__ == "__main__":  # pragma: no cover  # script entry
    raise SystemExit(main())


__all__ = ["main"]
