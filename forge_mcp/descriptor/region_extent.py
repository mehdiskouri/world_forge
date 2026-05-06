"""Region-extent datum threaded into the descriptor->spec mapping.

Carries the *horizontal* footprint of a region polygon as a frozen
named tuple so the Phase 3 mapping can clamp the elevation band to a
slope-plausible range, and the Phase 6 boundary contract solver can
size its edge-sample arrays consistently. Both consumers want the
same numbers (axis-aligned bounding-box width / height / area in
metres), so the datum is centralised here rather than recomputed at
each call site.

The XY footprint comes from the region's
:attr:`forge_mcp.project.schemas.SpatialBounds.coords`. The polygon
is canonicalised on construction (see :class:`Polygon2D`) so the
bounding box is well defined; this module is a thin reduction of the
canonical coords to scalar width/height/area, with no further
geometric checks (the schema layer already rejects degenerate
polygons via the area-epsilon validator).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Sequence


class RegionExtent(NamedTuple):
    """Axis-aligned footprint of a region polygon in metres.

    Attributes:
        width_m: ``max_x - min_x`` of the polygon's bounding box.
        height_m: ``max_y - min_y`` of the polygon's bounding box.
        area_m2: Bounding-box area (``width_m * height_m``). Not the
            polygon's own area; the bounding box is the right datum
            for slope-plausibility ceilings (the worst-case relief
            spreads across the bounding box, not the polygon
            interior).
    """

    width_m: float
    height_m: float
    area_m2: float

    @classmethod
    def from_polygon_coords(
        cls,
        coords: Sequence[tuple[float, float]],
    ) -> RegionExtent:
        """Reduce a polygon's vertices to the axis-aligned bounding box.

        Args:
            coords: Polygon vertices in any winding; the schema layer
                guarantees at least three distinct, non-degenerate
                vertices.

        Returns:
            The corresponding :class:`RegionExtent`.

        Raises:
            ValueError: If ``coords`` is empty (defensive; the schema
                layer already prevents this upstream).
        """
        if not coords:
            msg = "RegionExtent requires at least one vertex"
            raise ValueError(msg)
        xs = tuple(x for x, _ in coords)
        ys = tuple(y for _, y in coords)
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        return cls(width_m=width, height_m=height, area_m2=width * height)

    @property
    def min_extent_m(self) -> float:
        """Shorter of the two axes; drives the slope-plausibility ceiling.

        Returns the smaller of ``width_m`` / ``height_m``. The clamp
        uses the *minimum* extent because vertical relief that would
        be plausible across the long axis can still produce
        implausible mean slopes when projected onto the short axis.
        """
        return min(self.width_m, self.height_m)


__all__ = ["RegionExtent"]
