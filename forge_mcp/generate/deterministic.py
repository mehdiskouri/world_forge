"""Deterministic RNG factory for the Phase 3 generation pipeline.

Architecture §4.3 invariant: *every generator takes an explicit RNG; no
module-level state*. Composing that invariant with reproducibility
across machines and Python versions requires a single, audited entry
point that maps ``(seed, purpose)`` to a fresh
:class:`numpy.random.Generator`.

This module is that entry point. The mixer is BLAKE2b over the UTF-8
bytes of the purpose string plus the integer seed, fed as ``entropy=``
to a :class:`numpy.random.SeedSequence` — explicit, audit-friendly,
and independent of NumPy's internal hashing.

Purposes are a closed registry (:data:`PURPOSES`). Adding or renaming a
purpose is a determinism-breaking change and must bump
``GenerationMetadata.compiler_version`` in
:mod:`forge_mcp.project.schemas`. The registry's contents are pinned by
:mod:`tests.generate.test_deterministic`.
"""

from __future__ import annotations

from hashlib import blake2b
from typing import Final

import numpy as np

__all__ = ["PURPOSES", "Purpose", "UnknownPurposeError", "make_rng"]

Purpose = str
"""Purpose alias; stays a free string at the type level so callers can
declare locals without importing the registry. Validity is enforced at
runtime by :func:`make_rng`."""

PURPOSES: Final[frozenset[Purpose]] = frozenset(
    {
        "noise.base",
        "noise.warp",
        "erosion.hydraulic",
        "erosion.thermal",
        "stream.path_jitter",
        "macro.lowland_tilt",
    },
)
"""Closed registry of every RNG purpose the Phase 3 pipeline will consume.

Membership is asserted by :mod:`tests.generate.test_deterministic` so
that drift here triggers a CI failure that demands a
``compiler_version`` bump and a regenerated golden-spec corpus.
"""

_DIGEST_SIZE: Final[int] = 16
"""Bytes of BLAKE2b output folded into ``SeedSequence.entropy``; 128 bits
of mixed entropy is more than the underlying PCG64 state needs and is
plenty for collision avoidance across the registry."""


class UnknownPurposeError(ValueError):
    """Raised when :func:`make_rng` is called with an unknown purpose.

    The set of legal purposes lives in :data:`PURPOSES`.
    """


def make_rng(seed: int, *, purpose: Purpose) -> np.random.Generator:
    """Return a fresh :class:`numpy.random.Generator` for one purpose.

    Args:
        seed: The integer seed recorded on the region (or spec). May be
            negative; sign is folded into the mixer.
        purpose: One of :data:`PURPOSES`. The purpose is mixed into the
            entropy so two purposes from the same seed produce
            independent streams.

    Returns:
        A :class:`numpy.random.Generator` backed by PCG64, seeded
        deterministically from ``(seed, purpose)``. The generator is
        owned by the caller and must not be shared across purposes.

    Raises:
        UnknownPurposeError: ``purpose`` is not in :data:`PURPOSES`.
    """
    if purpose not in PURPOSES:
        msg = f"unknown purpose {purpose!r}; allowed: {sorted(PURPOSES)}"
        raise UnknownPurposeError(msg)
    mixer = blake2b(digest_size=_DIGEST_SIZE)
    # Fold seed (signed) and purpose into one digest. The seed is
    # serialised as a fixed-width signed-128-bit two's-complement
    # representation so positive/negative seeds never collide.
    mixer.update(seed.to_bytes(16, byteorder="big", signed=True))
    mixer.update(purpose.encode("utf-8"))
    entropy = int.from_bytes(mixer.digest(), byteorder="big")
    return np.random.default_rng(np.random.SeedSequence(entropy=entropy))
