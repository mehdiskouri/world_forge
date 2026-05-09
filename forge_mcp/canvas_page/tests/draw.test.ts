import { describe, expect, it } from "vitest";

import { buildPixelPoints, projectToPixels, DEFAULT_VIEWPORT } from "../src/canvas/projection.ts";

describe("projectToPixels", () => {
  it("projects the world origin to the viewport center", () => {
    const [x, y] = projectToPixels([0, 0], DEFAULT_VIEWPORT);
    expect(x).toBeCloseTo(DEFAULT_VIEWPORT.width / 2);
    expect(y).toBeCloseTo(DEFAULT_VIEWPORT.height / 2);
  });

  it("flips the Y axis so positive world Y goes up", () => {
    const [, lowY] = projectToPixels([0, -100], DEFAULT_VIEWPORT);
    const [, highY] = projectToPixels([0, 100], DEFAULT_VIEWPORT);
    expect(lowY).toBeGreaterThan(highY);
  });
});

describe("buildPixelPoints", () => {
  it("flattens polygon coords to [x0,y0,x1,y1,...]", () => {
    const points = buildPixelPoints(
      {
        node_id: "r",
        parent_node: "world_root",
        name: "R",
        spatial_bounds: {
          coords: { coords: [[0, 0], [10, 0], [10, 10]] },
        },
      },
      DEFAULT_VIEWPORT,
    );
    expect(points).toHaveLength(6);
  });
});
