"""Tests for :mod:`forge_mcp.generate.deterministic` — the single-source-of-truth
RNG factory.

These tests pin the determinism contract, the closed purpose registry,
and the per-purpose independence invariant. Drift here is by design a
CI failure that demands a ``compiler_version`` bump in
:class:`forge_mcp.project.schemas.GenerationMetadata`.
"""

from __future__ import annotations

import numpy as np
import pytest
from forge_mcp.generate.deterministic import (
    PURPOSES,
    UnknownPurposeError,
    make_rng,
)

_DRAW_SIZE = 16
"""Number of floats sampled per generator in determinism comparisons."""

_EXPECTED_PURPOSES = frozenset(
    {
        "noise.base",
        "noise.warp",
        "erosion.hydraulic",
        "erosion.thermal",
        "stream.path_jitter",
    },
)


def _draw(rng: np.random.Generator) -> np.typing.NDArray[np.float64]:
    return rng.random(_DRAW_SIZE)


def test_purpose_registry_is_locked() -> None:
    """The set of legal purposes is part of the determinism contract."""
    assert _EXPECTED_PURPOSES == PURPOSES


@pytest.mark.parametrize("purpose", sorted(PURPOSES))
def test_make_rng_is_deterministic_per_purpose(purpose: str) -> None:
    """Two calls with identical inputs return byte-identical streams."""
    a = _draw(make_rng(42, purpose=purpose))
    b = _draw(make_rng(42, purpose=purpose))
    assert np.array_equal(a, b)


def test_make_rng_streams_are_independent_across_purposes() -> None:
    """Different purposes from one seed must not share a stream."""
    seed = 12345
    streams = {name: _draw(make_rng(seed, purpose=name)) for name in PURPOSES}
    pairs = [(a, b) for a in streams for b in streams if a < b]
    for a, b in pairs:
        assert not np.array_equal(streams[a], streams[b]), f"streams for {a!r} and {b!r} collide"


def test_make_rng_streams_differ_across_seeds() -> None:
    """Same purpose, different seed → different stream."""
    a = _draw(make_rng(1, purpose="noise.base"))
    b = _draw(make_rng(2, purpose="noise.base"))
    assert not np.array_equal(a, b)


def test_make_rng_handles_negative_seeds_distinctly() -> None:
    """A negative seed must not collide with its absolute value."""
    pos = _draw(make_rng(7, purpose="noise.base"))
    neg = _draw(make_rng(-7, purpose="noise.base"))
    assert not np.array_equal(pos, neg)


def test_make_rng_rejects_unknown_purpose() -> None:
    """The closed registry is enforced; bad names fail loudly."""
    with pytest.raises(UnknownPurposeError, match="unknown purpose"):
        make_rng(0, purpose="not.a.real.purpose")
