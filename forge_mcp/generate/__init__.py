"""Deterministic generation primitives (Phase 3).

This package owns the RNG-derivation contract and (in later PRs) the
noise / erosion / stream / orchestrator stack. Every public function
takes its RNG explicitly via :func:`forge_mcp.generate.deterministic.make_rng`
— there is no module-level RNG anywhere under ``forge_mcp/generate/``.
"""

from __future__ import annotations
