/**
 * Snapshot envelope shared between the canvas server and the frontend.
 *
 * Mirrors the shape produced by ``forge_mcp.server.canvas_server._build_snapshot``;
 * keep field names in lock-step.
 */

export interface RegionPolygon {
  coords: ReadonlyArray<readonly [number, number]>;
}

export interface RegionBounds {
  coords: RegionPolygon;
}

export interface Region {
  node_id: string;
  parent_node: string;
  name: string;
  spatial_bounds: RegionBounds;
}

export interface Boundary {
  boundary_id: string;
  region_a: string;
  region_b: string;
  shared_edge?: ReadonlyArray<readonly [number, number]>;
}

export interface ProjectMeta {
  name: string;
  root: string;
}

export interface Snapshot {
  project_open: boolean;
  project?: ProjectMeta;
  regions: Region[];
  boundaries: Boundary[];
}

export const EMPTY_SNAPSHOT: Snapshot = {
  project_open: false,
  regions: [],
  boundaries: [],
};
