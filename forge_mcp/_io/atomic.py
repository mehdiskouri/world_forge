"""Atomic JSON / text writes plus the canonical project ``dump_json``.

Every JSON write that lands on disk inside a Forge project goes through
this module. Centralizing the chokepoint gives us:

* atomicity (NF-3.1 + ``.github/instructions.md`` §6) — no torn writes,
  even if the process dies mid-flush;
* byte-stable formatting — sorted keys, two-space indent, trailing
  newline — so git diffs stay reviewable;
* a single place to reason about Pydantic ``model_dump(mode='json')``
  vs. raw JSON-compatible payloads.
"""

from __future__ import annotations

import json
import os
import secrets
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from pydantic import BaseModel


_TMP_SUFFIX_RAND_BYTES = 4


def atomic_write_text(path: Path, data: str) -> None:
    """Atomically write ``data`` to ``path``.

    Implementation: write to a sibling ``<path>.tmp.<pid>.<rand>`` file
    in the same directory, fsync, then ``os.replace`` over the target.
    ``os.replace`` is the POSIX + Windows atomic-rename primitive, so a
    crash mid-write leaves either the old file intact or no file at all
    (never a half-written file at the canonical path).

    Caller is expected to ensure ``path.parent`` already exists.
    """
    parent = path.parent
    suffix = f".tmp.{os.getpid()}.{secrets.token_hex(_TMP_SUFFIX_RAND_BYTES)}"
    tmp = parent / (path.name + suffix)
    # ``open`` with ``"x"`` keeps two concurrent writers from clobbering
    # each other's tmp files; the random suffix already makes collision
    # vanishingly unlikely but exclusivity is the right default.
    with tmp.open("x", encoding="utf-8") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)  # noqa: PTH105 - we need the os primitive on a Path argument


def dump_json(payload: BaseModel | Mapping[str, object]) -> str:
    """Serialize ``payload`` with Forge's canonical JSON style.

    * ``indent=2``, ``sort_keys=True``, ``separators=(",", ": ")``,
      ``ensure_ascii=False`` — matches
      :func:`forge_mcp.project.schema_export.dump_schema_json` so on-disk
      bodies and committed schemas share one formatting policy.
    * Pydantic models are routed through ``model_dump(mode='json')`` so
      datetimes become ISO strings, UUIDs become hex, etc.
    * A trailing newline is appended (POSIX text-file convention; keeps
      ``git diff`` quiet).
    """
    if hasattr(payload, "model_dump"):
        # Pydantic stubs leak Any through ``model_dump``; cast keeps the
        # downstream ``json.dumps`` call typed.
        body = cast("object", payload.model_dump(mode="json"))
    else:
        body = payload
    return (
        json.dumps(
            body,
            indent=2,
            sort_keys=True,
            separators=(",", ": "),
            ensure_ascii=False,
        )
        + "\n"
    )


def write_json(path: Path, payload: BaseModel | Mapping[str, object]) -> None:
    """Convenience wrapper: ``atomic_write_text(path, dump_json(payload))``."""
    atomic_write_text(path, dump_json(payload))


__all__ = ["atomic_write_text", "dump_json", "write_json"]
