import { describe, expect, it } from "vitest";

import { buildLayout } from "../src/connection_map/layout.ts";
import type { Snapshot } from "../src/types.ts";

const SNAPSHOT: Snapshot = {
  project_open: true,
  project: { name: "Demo", root: "/tmp/demo" },
  regions: [
    {
      node_id: "region_alpha",
      parent_node: "world_root",
      name: "Alpha",
      spatial_bounds: { coords: { coords: [[0, 0]] } },
    },
    {
      node_id: "region_beta",
      parent_node: "world_root",
      name: "Beta",
      spatial_bounds: { coords: { coords: [[5, 5]] } },
    },
  ],
  boundaries: [
    {
      boundary_id: "boundary_ab",
      region_a: "region_alpha",
      region_b: "region_beta",
    },
  ],
};

describe("buildLayout", () => {
  it("emits one node per region", () => {
    const layout = buildLayout(SNAPSHOT);
    expect(layout.nodes.map((n) => n.id).sort()).toEqual([
      "region_alpha",
      "region_beta",
    ]);
  });

  it("emits adjacency + containment links by default", () => {
    const layout = buildLayout(SNAPSHOT);
    const kinds = layout.links.map((l) => l.kind).sort();
    expect(kinds).toEqual(["adjacency", "containment", "containment"]);
  });

  it("respects the layer toggle", () => {
    const layout = buildLayout(SNAPSHOT, {
      layers: new Set(["adjacency"]),
    });
    expect(layout.links.every((l) => l.kind === "adjacency")).toBe(true);
    expect(layout.links).toHaveLength(1);
  });
});
