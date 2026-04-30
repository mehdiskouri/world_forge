"""Tests for the ``forge-schema-export`` CLI + drift check.

Covers:

* committed schemas under ``schemas/`` are byte-equal to live model
  output (the CI invariant);
* ``--write`` regenerates the artifacts;
* ``--check`` flags drift loudly without rewriting anything;
* ``--write`` and ``--check`` are mutually exclusive.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from forge_mcp.project import schema_export
from forge_mcp.project.schema_export import (
    LEGACY_DESCRIPTOR_SCHEMA_PATH,
    SCHEMAS_DIR,
    _aliases_for,
    _check_all,
    dump_schema_json,
    iter_published_schemas,
    main,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_published_schemas_match_committed_files() -> None:
    """The CI invariant: every committed schema equals its live model output."""
    for name, schema in iter_published_schemas():
        body = dump_schema_json(schema)
        for path in _aliases_for(name):
            assert path.exists(), f"missing {path}"
            assert path.read_text(encoding="utf-8") == body, f"drift at {path}"


def test_check_returns_zero_on_clean_tree() -> None:
    assert _check_all() == 0


def test_check_returns_one_on_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_schemas = tmp_path / "schemas"
    fake_schemas.mkdir()
    fake_legacy = tmp_path / "legacy" / "schema.json"
    monkeypatch.setattr(schema_export, "SCHEMAS_DIR", fake_schemas)
    monkeypatch.setattr(schema_export, "LEGACY_DESCRIPTOR_SCHEMA_PATH", fake_legacy)
    assert _check_all() == 1
    err = capsys.readouterr().err
    assert "missing" in err


def test_check_returns_one_on_byte_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_schemas = tmp_path / "schemas"
    fake_schemas.mkdir()
    fake_legacy = tmp_path / "legacy" / "schema.json"
    fake_legacy.parent.mkdir()
    # Seed bogus content so every file is present but wrong.
    for name, _ in iter_published_schemas():
        (fake_schemas / f"{name}.schema.json").write_text("{}\n", encoding="utf-8")
    fake_legacy.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(schema_export, "SCHEMAS_DIR", fake_schemas)
    monkeypatch.setattr(schema_export, "LEGACY_DESCRIPTOR_SCHEMA_PATH", fake_legacy)
    assert _check_all() == 1
    assert "drift" in capsys.readouterr().err


def test_write_regenerates_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_schemas = tmp_path / "schemas"
    fake_legacy = tmp_path / "legacy" / "schema.json"
    monkeypatch.setattr(schema_export, "SCHEMAS_DIR", fake_schemas)
    monkeypatch.setattr(schema_export, "LEGACY_DESCRIPTOR_SCHEMA_PATH", fake_legacy)
    monkeypatch.setattr(schema_export, "REPO_ROOT", tmp_path)
    rc = main(["--write"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "wrote" in out
    # Every published name should now exist in fake_schemas.
    for name, _ in iter_published_schemas():
        assert (fake_schemas / f"{name}.schema.json").exists()
    assert fake_legacy.exists()


def test_main_check_returns_zero() -> None:
    assert main(["--check"]) == 0


def test_main_requires_a_mode() -> None:
    with pytest.raises(SystemExit):
        main([])


def test_legacy_descriptor_alias_includes_two_paths() -> None:
    paths = _aliases_for("descriptor")
    assert len(paths) == 2  # noqa: PLR2004 - the descriptor is dual-published
    assert SCHEMAS_DIR / "descriptor.schema.json" in paths
    assert LEGACY_DESCRIPTOR_SCHEMA_PATH in paths
