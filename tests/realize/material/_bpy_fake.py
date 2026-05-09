"""Minimal in-process ``bpy`` fake for adapter shader-builder unit tests.

The Blender adapter (``scripts/blender/adapter.py``) starts with
``import bpy`` and constructs shader graphs by calling
``nodes.new("ShaderNodeMath")``, setting attributes like
``node.operation = "SMOOTHSTEP"``, populating
``node.inputs[i].default_value = ...`` and finally
``links.new(out_socket, in_socket)``. The real ``bpy`` module is only
available inside the Blender process, which makes per-builder unit
tests prohibitively slow.

This module provides a permissive fake that:

* records every node created (in order) with its ``bl_idname``,
* records every link as an ordered ``(from_socket, to_socket)`` pair,
* lets builders write arbitrary attributes (``operation``,
  ``use_clamp``, ``default_value``) without raising,
* lets builders look up sockets by index *or* by name interchangeably,
* covers the ``ShaderNodeValToRGB.color_ramp.elements`` API used by
  the ``principled_height_ramp`` recipe,
* covers ``bpy.data.materials.{get,new}`` and
  ``bpy.data.objects.get`` for the composite-material RPC handler.

It deliberately does *not* simulate node evaluation — tests assert on
the *structure* of the recorded graph (which nodes were added, how
their default values were set, which sockets are linked). Pixel-level
correctness is still validated by ``make integration`` against real
Blender.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType


class FakeSocket:
    """One fake input or output socket on a :class:`FakeNode`.

    ``default_value`` is permissive — adapter code writes scalars,
    booleans, and 4-tuples to it. The fake stores whatever it gets.
    """

    def __init__(self, owner: FakeNode, name: str, *, is_output: bool) -> None:
        self.node = owner
        self.name = name
        self.is_output = is_output
        self.default_value: object = 0.0

    def __repr__(self) -> str:
        side = "out" if self.is_output else "in"
        return f"<{side} {self.node.bl_idname}.{self.name}>"


class FakeSocketArray:
    """Lazy container that materialises sockets on first int/str access."""

    def __init__(self, owner: FakeNode, *, is_output: bool) -> None:
        self._node = owner
        self._is_output = is_output
        self._by_name: dict[str, FakeSocket] = {}
        self._by_index: dict[int, FakeSocket] = {}

    def __getitem__(self, key: int | str) -> FakeSocket:
        if isinstance(key, int):
            socket = self._by_index.get(key)
            if socket is None:
                socket = FakeSocket(self._node, f"#{key}", is_output=self._is_output)
                self._by_index[key] = socket
            return socket
        socket = self._by_name.get(key)
        if socket is None:
            socket = FakeSocket(self._node, key, is_output=self._is_output)
            self._by_name[key] = socket
        return socket

    def __contains__(self, key: object) -> bool:
        # Permissive on purpose: the adapter uses ``"X" in node.inputs``
        # only as a cross-Blender-version socket-rename guard
        # (e.g. "Coat Weight" vs "Clearcoat"). Returning ``True`` makes
        # unit tests take the modern Blender 5.0.0 branch, matching
        # production behaviour. Real existence checks go through
        # :meth:`named` which only reflects sockets actually accessed.
        return isinstance(key, (str, int))

    def named(self) -> dict[str, FakeSocket]:
        """Return only sockets that were addressed by name."""
        return dict(self._by_name)


class FakeColorRampElement:
    """One stop in a :class:`FakeColorRamp`."""

    def __init__(self, position: float) -> None:
        self.position = position
        self.color: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)


class FakeColorRampElements:
    """Mimics ``ShaderNodeValToRGB.color_ramp.elements``.

    Real Blender exposes a list-like with ``.new(position)`` returning
    a new stop and ``.remove(stop)`` deleting one. The fake supports
    indexing by int (including ``-1``).
    """

    def __init__(self) -> None:
        self._items: list[FakeColorRampElement] = [FakeColorRampElement(0.0)]

    def __getitem__(self, index: int) -> FakeColorRampElement:
        return self._items[index]

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[FakeColorRampElement]:
        return iter(self._items)

    def new(self, position: float) -> FakeColorRampElement:
        elem = FakeColorRampElement(position)
        self._items.append(elem)
        return elem

    def remove(self, elem: FakeColorRampElement) -> None:
        self._items.remove(elem)


class FakeColorRamp:
    """Mimics ``ShaderNodeValToRGB.color_ramp``."""

    def __init__(self) -> None:
        self.elements = FakeColorRampElements()


class FakeNode:
    """Permissive shader node used by the adapter under test.

    The fake accepts arbitrary attribute writes (``operation``,
    ``use_clamp``, ``mode``, etc.) without raising, and exposes
    ``inputs``/``outputs`` socket arrays plus a ``color_ramp`` for
    the few node types that need it.
    """

    def __init__(self, bl_idname: str) -> None:
        self.bl_idname = bl_idname
        self.inputs = FakeSocketArray(self, is_output=False)
        self.outputs = FakeSocketArray(self, is_output=True)
        self.color_ramp = FakeColorRamp()

    def __repr__(self) -> str:
        return f"<FakeNode {self.bl_idname}>"


class FakeNodes:
    """Mimics ``Material.node_tree.nodes`` — ordered, list-like."""

    def __init__(self) -> None:
        self._items: list[FakeNode] = []

    def __iter__(self) -> Iterator[FakeNode]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def new(self, bl_idname: str) -> FakeNode:
        node = FakeNode(bl_idname)
        self._items.append(node)
        return node

    def remove(self, node: FakeNode) -> None:
        self._items.remove(node)

    def of_type(self, bl_idname: str) -> list[FakeNode]:
        """Return every recorded node with the given ``bl_idname``."""
        return [n for n in self._items if n.bl_idname == bl_idname]


class FakeLinks:
    """Mimics ``Material.node_tree.links`` — ordered, list-like."""

    def __init__(self) -> None:
        self._items: list[tuple[FakeSocket, FakeSocket]] = []

    def __iter__(self) -> Iterator[tuple[FakeSocket, FakeSocket]]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def new(self, from_socket: FakeSocket, to_socket: FakeSocket) -> tuple[FakeSocket, FakeSocket]:
        edge = (from_socket, to_socket)
        self._items.append(edge)
        return edge

    def for_input(self, node: FakeNode, name: str) -> list[FakeSocket]:
        """Return every ``from_socket`` linked into ``node.inputs[name]``."""
        return [src for src, dst in self._items if dst.node is node and dst.name == name]


class FakeNodeTree:
    """Mimics ``Material.node_tree``."""

    def __init__(self) -> None:
        self.nodes = FakeNodes()
        self.links = FakeLinks()


class FakeMaterial:
    """Mimics ``bpy.data.materials`` entries."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.use_nodes = False
        self.node_tree = FakeNodeTree()


class FakeMaterialDB:
    """Mimics ``bpy.data.materials``."""

    def __init__(self) -> None:
        self._items: dict[str, FakeMaterial] = {}

    def get(self, name: str) -> FakeMaterial | None:
        return self._items.get(name)

    def new(self, name: str) -> FakeMaterial:
        mat = FakeMaterial(name)
        self._items[name] = mat
        return mat


class FakeObjectMaterialSlots(list[FakeMaterial]):
    """Mimics ``Object.data.materials`` — a list with ``append``."""


class FakeObjectData:
    """Mimics ``Object.data`` (mesh data block) — only ``materials`` matters."""

    def __init__(self) -> None:
        self.materials = FakeObjectMaterialSlots()


class FakeModifier:
    """Permissive Geometry-Nodes / Displace modifier stub.

    Real Blender exposes per-modifier-type fields like
    ``node_group``, ``texture``, ``strength``, etc. The fake accepts
    arbitrary attribute writes plus a permissive subscript map for
    GN modifier input bindings (``modifier["Input_3"] = value``).
    """

    def __init__(self, name: str, mod_type: str) -> None:
        self.name = name
        self.type = mod_type
        self.node_group: FakeNodeGroup | None = None
        self._inputs: dict[str, object] = {}

    def __setitem__(self, key: str, value: object) -> None:
        self._inputs[key] = value

    def __getitem__(self, key: str) -> object:
        return self._inputs[key]

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self._inputs


class FakeModifiers:
    """Mimics ``Object.modifiers`` — ordered list with ``new`` / ``remove``."""

    def __init__(self) -> None:
        self._items: list[FakeModifier] = []

    def __iter__(self) -> Iterator[FakeModifier]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, key: int | str) -> FakeModifier:
        if isinstance(key, int):
            return self._items[key]
        for mod in self._items:
            if mod.name == key:
                return mod
        raise KeyError(key)

    def get(self, name: str) -> FakeModifier | None:
        for mod in self._items:
            if mod.name == name:
                return mod
        return None

    def new(self, name: str, type: str) -> FakeModifier:  # noqa: A002 - bpy API name
        mod = FakeModifier(name, type)
        self._items.append(mod)
        return mod

    def remove(self, mod: FakeModifier) -> None:
        self._items.remove(mod)


class FakeObject:
    """Mimics ``bpy.data.objects`` entries."""

    def __init__(self, name: str, object_data: object | None = None) -> None:
        self.name = name
        self.data = object_data if object_data is not None else FakeObjectData()
        self.modifiers = FakeModifiers()
        self.use_fake_user = False
        self.rotation_euler: tuple[float, float, float] = (0.0, 0.0, 0.0)


class FakeObjectDB:
    """Mimics ``bpy.data.objects``."""

    def __init__(self) -> None:
        self._items: dict[str, FakeObject] = {}

    def get(self, name: str) -> FakeObject | None:
        return self._items.get(name)

    def add(self, name: str) -> FakeObject:
        """Test helper — pre-register an object the adapter can look up."""
        obj = FakeObject(name)
        self._items[name] = obj
        return obj

    def new(self, name: str, object_data: object | None = None) -> FakeObject:
        """Mirror ``bpy.data.objects.new(name, object_data)``."""
        obj = FakeObject(name, object_data=object_data)
        self._items[name] = obj
        return obj

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._items


class FakeMesh:
    """Mimics ``bpy.data.meshes`` entries — minimal verts/faces + materials."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.materials = FakeObjectMaterialSlots()
        self._verts: list[tuple[float, float, float]] = []
        self._edges: list[tuple[int, int]] = []
        self._faces: list[tuple[int, ...]] = []

    def from_pydata(
        self,
        verts: list[tuple[float, float, float]],
        edges: list[tuple[int, int]],
        faces: list[tuple[int, ...]],
    ) -> None:
        self._verts = list(verts)
        self._edges = list(edges)
        self._faces = list(faces)

    def update(self) -> None:
        return


class FakeMeshDB:
    """Mimics ``bpy.data.meshes``."""

    def __init__(self) -> None:
        self._items: dict[str, FakeMesh] = {}

    def get(self, name: str) -> FakeMesh | None:
        return self._items.get(name)

    def new(self, name: str) -> FakeMesh:
        mesh = FakeMesh(name)
        self._items[name] = mesh
        return mesh

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._items


class FakeNodeGroup:
    """Mimics ``bpy.data.node_groups`` entries (``GeometryNodeTree``)."""

    def __init__(self, name: str, group_type: str) -> None:
        self.name = name
        self.bl_idname = group_type
        self.nodes = FakeNodes()
        self.links = FakeLinks()
        self.interface = FakeNodeGroupInterface()


class FakeNodeGroupInterface:
    """Mimics ``NodeTree.interface`` — Blender 4+ socket-declaration API."""

    def __init__(self) -> None:
        self.items_tree: list[object] = []

    def new_socket(
        self,
        name: str,
        *,
        in_out: str = "INPUT",
        socket_type: str = "NodeSocketGeometry",
    ) -> object:
        item = type(
            "FakeInterfaceSocket",
            (),
            {"name": name, "in_out": in_out, "socket_type": socket_type},
        )()
        self.items_tree.append(item)
        return item


class FakeNodeGroupDB:
    """Mimics ``bpy.data.node_groups``."""

    def __init__(self) -> None:
        self._items: dict[str, FakeNodeGroup] = {}

    def get(self, name: str) -> FakeNodeGroup | None:
        return self._items.get(name)

    def new(self, name: str, type: str) -> FakeNodeGroup:  # noqa: A002 - bpy API name
        group = FakeNodeGroup(name, type)
        self._items[name] = group
        return group

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._items


class FakeBpyData:
    """Mimics ``bpy.data``."""

    def __init__(self) -> None:
        self.materials = FakeMaterialDB()
        self.objects = FakeObjectDB()
        self.meshes = FakeMeshDB()
        self.node_groups = FakeNodeGroupDB()
        self.worlds = FakeWorldDB()
        self.lights = FakeLightDB()


class FakeWorld:
    """Mimics ``bpy.data.worlds`` entries."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.use_nodes = False
        self.node_tree = FakeNodeTree()


class FakeWorldDB:
    """Mimics ``bpy.data.worlds``."""

    def __init__(self) -> None:
        self._items: dict[str, FakeWorld] = {}

    def get(self, name: str) -> FakeWorld | None:
        return self._items.get(name)

    def new(self, name: str) -> FakeWorld:
        world = FakeWorld(name)
        self._items[name] = world
        return world

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._items

    def __len__(self) -> int:
        return len(self._items)


class FakeLight:
    """Mimics ``bpy.data.lights`` entries (SUN/POINT/AREA share the surface)."""

    def __init__(self, name: str, light_type: str) -> None:
        self.name = name
        self.type = light_type
        self.color: tuple[float, float, float] = (1.0, 1.0, 1.0)
        self.energy: float = 0.0


class FakeLightDB:
    """Mimics ``bpy.data.lights``."""

    def __init__(self) -> None:
        self._items: dict[str, FakeLight] = {}

    def get(self, name: str) -> FakeLight | None:
        return self._items.get(name)

    def new(self, name: str, type: str) -> FakeLight:  # noqa: A002 - bpy API name
        light = FakeLight(name, type)
        self._items[name] = light
        return light

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._items

    def __len__(self) -> int:
        return len(self._items)


class FakeSceneCollection:
    """Mimics ``bpy.context.scene.collection``."""

    def __init__(self) -> None:
        self.objects = FakeSceneObjectsLink()


class FakeSceneObjectsLink:
    """Mimics ``Scene.collection.objects`` — only ``link()`` is exercised."""

    def __init__(self) -> None:
        self.linked: list[FakeObject] = []

    def link(self, obj: FakeObject) -> None:
        self.linked.append(obj)


class FakeScene:
    """Mimics ``bpy.context.scene`` — minimal world + collection."""

    def __init__(self) -> None:
        self.world: FakeWorld | None = None
        self.collection = FakeSceneCollection()


class FakeContext:
    """Mimics ``bpy.context``."""

    def __init__(self) -> None:
        self.scene = FakeScene()


class FakeBpy:
    """Top-level fake module exposing ``bpy.data`` and ``bpy.context``."""

    def __init__(self) -> None:
        self.data = FakeBpyData()
        self.context = FakeContext()


def install_fake_bpy() -> FakeBpy:
    """Insert a fresh :class:`FakeBpy` into ``sys.modules['bpy']``.

    Returns the installed instance so tests can pre-register objects
    via ``fake.data.objects.add(...)`` and inspect created materials
    afterwards.
    """
    fake = FakeBpy()
    sys.modules["bpy"] = fake  # type: ignore[assignment]  # ducktype is the point
    return fake


_REPO_ROOT: Path = Path(__file__).resolve().parents[3]
_ADAPTER_PATH: Path = _REPO_ROOT / "scripts" / "blender" / "adapter.py"


def load_adapter_module() -> ModuleType:
    """Import ``scripts/blender/adapter.py`` under the currently-installed fake.

    Caller is responsible for installing a :class:`FakeBpy` first
    (typically via the ``fake_bpy`` pytest fixture in the sibling
    ``conftest.py``).
    """
    spec = importlib.util.spec_from_file_location(
        "forge_adapter_under_test",
        _ADAPTER_PATH,
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        msg = f"could not load adapter spec from {_ADAPTER_PATH!s}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
