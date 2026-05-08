"""Recipe registry exhaustiveness gate (Phase 6-e Stage B).

Adding a new :class:`~forge_mcp.project.schemas.MaterialRecipe` enum
value requires four coordinated edits per the docstring on
``MaterialRecipe``:

1. The enum value itself in ``forge_mcp.project.schemas``.
2. A parameter validator in
   :mod:`forge_mcp.realize.material.defaults` keyed in ``_VALIDATORS``.
3. A builder function in ``scripts/blender/adapter.py`` keyed in
   ``_RECIPE_BUILDERS``.
4. A regenerated JSON schema under ``schemas/`` that lists the new
   enum value (covered by the existing ``forge-schema-export --check``
   gate, but mirrored here for fast feedback).

This test fails loudly if any of these drift apart so future recipe
work cannot silently leave one of the four out of sync.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from forge_mcp.project.schemas import MaterialRecipe
from forge_mcp.realize.material.defaults import _VALIDATORS

from tests.realize.material._bpy_fake import install_fake_bpy, load_adapter_module

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_PATH = _REPO_ROOT / "schemas" / "material_archetype.schema.json"


@pytest.fixture(scope="module")
def adapter_recipe_keys() -> frozenset[str]:
    """Collect ``_RECIPE_BUILDERS`` keys from the adapter under the fake bpy."""
    install_fake_bpy()
    module = load_adapter_module()
    return frozenset(module._RECIPE_BUILDERS)  # noqa: SLF001 - test inspects adapter registry


@pytest.fixture(scope="module")
def adapter_instancer_keys() -> frozenset[str]:
    """Collect ``_INSTANCER_BUILDERS`` keys from the adapter under the fake bpy.

    Stage F adds the registry (empty); Stage D wires
    ``procedural_grass`` into it. Recipes routed through the
    instancer have no surface-builder counterpart, so they show up
    here instead of in ``_RECIPE_BUILDERS``.
    """
    install_fake_bpy()
    module = load_adapter_module()
    return frozenset(module._INSTANCER_BUILDERS)  # noqa: SLF001 - test inspects adapter registry


def test_validators_cover_every_recipe() -> None:
    """Every ``MaterialRecipe`` enum value must have a validator entry."""
    missing = {recipe for recipe in MaterialRecipe if recipe not in _VALIDATORS}
    assert not missing, f"recipes missing from _VALIDATORS: {sorted(r.value for r in missing)}"


def test_adapter_builders_cover_every_recipe(
    adapter_recipe_keys: frozenset[str],
    adapter_instancer_keys: frozenset[str],
) -> None:
    """Every ``MaterialRecipe`` enum value must have a builder entry.

    Surface recipes live in ``_RECIPE_BUILDERS``; instancer recipes
    (Stage D's ``procedural_grass``) live in ``_INSTANCER_BUILDERS``.
    """
    known = adapter_recipe_keys | adapter_instancer_keys
    missing = {recipe.value for recipe in MaterialRecipe if recipe.value not in known}
    assert not missing, f"recipes missing from adapter builder registries: {sorted(missing)}"


def test_schema_enum_lists_every_recipe() -> None:
    """The exported JSON schema enum must match the Python enum."""
    payload = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    schema_enum = frozenset(payload["$defs"]["MaterialRecipe"]["enum"])
    python_enum = frozenset(r.value for r in MaterialRecipe)
    assert schema_enum == python_enum, (
        f"schema enum drift: only-in-schema={sorted(schema_enum - python_enum)}, "
        f"only-in-python={sorted(python_enum - schema_enum)}"
    )
