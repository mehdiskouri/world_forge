"""Tests for the ``forge-skills`` CLI."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from forge_mcp.skills.cli import main
from forge_mcp.skills.loader import SHIPPED_SKILL_NAMES

_EXIT_UNSUPPORTED_CLIENT = 2
"""Documented CLI exit code for ``install --client <unsupported>``."""

if TYPE_CHECKING:
    from pathlib import Path


def test_list_prints_every_shipped_skill(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``forge-skills list`` emits one line per shipped skill in canonical order."""
    rc = main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == len(SHIPPED_SKILL_NAMES)
    for line, expected in zip(lines, SHIPPED_SKILL_NAMES, strict=True):
        assert line.startswith(expected)


def test_install_writes_each_skill_md(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``forge-skills install --dest`` writes 5 SKILL.md files."""
    rc = main(["install", "--dest", str(tmp_path)])
    assert rc == 0
    for name in SHIPPED_SKILL_NAMES:
        target = tmp_path / name / "SKILL.md"
        assert target.is_file(), f"{target} not written"
        assert target.read_text(encoding="utf-8").startswith("---")
    out = capsys.readouterr().out
    for name in SHIPPED_SKILL_NAMES:
        assert name in out


def test_install_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    """A second install without ``--force`` aborts at the first existing file."""
    main(["install", "--dest", str(tmp_path)])
    with pytest.raises(FileExistsError):
        main(["install", "--dest", str(tmp_path)])


def test_install_force_overwrites(tmp_path: Path) -> None:
    """``--force`` makes install idempotent."""
    main(["install", "--dest", str(tmp_path)])
    rc = main(["install", "--dest", str(tmp_path), "--force"])
    assert rc == 0
    target = tmp_path / "forge.plan" / "SKILL.md"
    assert target.is_file()


def test_install_unknown_client_returns_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A non-Claude client prints a manual-paste hint and returns 2."""
    rc = main(["install", "--client", "cursor", "--dest", str(tmp_path)])
    assert rc == _EXIT_UNSUPPORTED_CLIENT
    err = capsys.readouterr().err
    assert "manual paste" in err


def test_export_writes_a_single_bundle(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``forge-skills export`` writes one bundle file containing every skill."""
    out_path = tmp_path / "bundle.md"
    rc = main(["export", "--out", str(out_path)])
    assert rc == 0
    bundle = out_path.read_text(encoding="utf-8")
    for name in SHIPPED_SKILL_NAMES:
        assert f"# {name}" in bundle
    assert "skills" in capsys.readouterr().out


def test_show_prints_one_skill_body(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``forge-skills show forge.plan`` prints the body markdown."""
    rc = main(["show", "forge.plan"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "forge.plan" in out


def test_install_creates_atomic_writes(tmp_path: Path) -> None:
    """No ``.tmp`` sidecars survive a successful install."""
    main(["install", "--dest", str(tmp_path)])
    survivors = sorted(p.name for p in tmp_path.rglob("*.tmp.*"))
    assert survivors == []


def test_install_payload_is_valid_frontmatter(tmp_path: Path) -> None:
    """Each installed SKILL.md still parses with the loader."""
    main(["install", "--dest", str(tmp_path)])
    # Re-parse manually because the loader points at the in-package files.
    for name in SHIPPED_SKILL_NAMES:
        text = (tmp_path / name / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---\n")
        body = text.split("---", 2)[1]
        # Each frontmatter line is parseable JSON after the colon.
        for line in body.strip().splitlines():
            if not line.strip():
                continue
            _, _, value = line.partition(":")
            json.loads(value.strip())
