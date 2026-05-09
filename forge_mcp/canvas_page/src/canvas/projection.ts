/**
 * Pure projection helpers for the canvas view.
 *
 * Split out from ``draw.ts`` so unit tests can exercise the math
 * without dragging in Konva (which requires ``node-canvas`` outside
 * the browser).
 */

import type { Region } from "../types.ts";

export interface CanvasViewport {
  /** World-space bounds: [min_x, min_y, max_x, max_y]. */
  worldBounds: readonly [number, number, number, number];
  /** Pixel size of the rendering surface. */
  width: number;
  height: number;
}

/** Default viewport for a fresh ``WorldBounds`` of (-100, 100). */
export const DEFAULT_VIEWPORT: CanvasViewport = {
  worldBounds: [-100, -100, 100, 100],
  width: 800,
  height: 600,
};

/**
 * Project world-space coordinates onto pixel coordinates inside the
 * Konva stage. Pure function so unit tests don't need a DOM.
 */
export function projectToPixels(
  point: readonly [number, number],
  viewport: CanvasViewport,
): [number, number] {
  const [minX, minY, maxX, maxY] = viewport.worldBounds;
  const w = maxX - minX || 1;
  const h = maxY - minY || 1;
  const x = ((point[0] - minX) / w) * viewport.width;
  // Flip Y so positive world Y goes "up".
  const y = viewport.height - ((point[1] - minY) / h) * viewport.height;
  return [x, y];
}

/** Build a flat ``[x0, y0, x1, y1, ...]`` array suitable for ``Konva.Line``. */
export function buildPixelPoints(
  region: Region,
  viewport: CanvasViewport,
): number[] {
  const flat: number[] = [];
  for (const coord of region.spatial_bounds.coords.coords) {
    const [px, py] = projectToPixels(coord, viewport);
    flat.push(px, py);
  }
  return flat;
}
