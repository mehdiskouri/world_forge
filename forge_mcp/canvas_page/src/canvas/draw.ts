/**
 * Konva polygon-drawing tools for the canvas view.
 *
 * Pure projection helpers live in :mod:`./projection.ts` so they can
 * be unit-tested without pulling Konva (which needs node-canvas
 * outside the browser) into the test runner.
 */

import Konva from "konva";

import type { Snapshot } from "../types.ts";
import {
  DEFAULT_VIEWPORT,
  buildPixelPoints,
  projectToPixels,
  type CanvasViewport,
} from "./projection.ts";

export { DEFAULT_VIEWPORT, buildPixelPoints, projectToPixels };
export type { CanvasViewport };

const REGION_FILL = "rgba(80, 140, 220, 0.25)";
const REGION_STROKE = "#5fa9ff";
const REGION_STROKE_WIDTH = 2;

/**
 * Draw every region in the snapshot onto ``layer``.
 *
 * Existing children are removed first so the function can be called
 * directly from the WS-snapshot handler without bookkeeping.
 */
export function renderSnapshot(
  layer: Konva.Layer,
  snapshot: Snapshot,
  viewport: CanvasViewport,
): void {
  layer.destroyChildren();
  for (const region of snapshot.regions) {
    const polygon = new Konva.Line({
      points: buildPixelPoints(region, viewport),
      closed: true,
      fill: REGION_FILL,
      stroke: REGION_STROKE,
      strokeWidth: REGION_STROKE_WIDTH,
      name: `region:${region.node_id}`,
    });
    layer.add(polygon);
    const [labelX, labelY] = projectToPixels(
      region.spatial_bounds.coords.coords[0] ?? [0, 0],
      viewport,
    );
    const label = new Konva.Text({
      x: labelX + 6,
      y: labelY - 18,
      text: region.name,
      fontSize: 12,
      fill: "#eaecef",
    });
    layer.add(label);
  }
  layer.draw();
}
