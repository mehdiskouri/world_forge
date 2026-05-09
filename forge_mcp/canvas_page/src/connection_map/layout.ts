/**
 * Connection-map view: d3-force layout over the project hypergraph.
 *
 * Nodes are regions (`spatial_containment` layer parents are summarised
 * as a synthetic root), edges are boundaries (`spatial_adjacency`).
 * The frontend toggles which edge layers feed the simulation; Phase 7
 * Stage F ships adjacency + containment, with hydrology behind a flag
 * (data is forwarded from `forge.query_layer` once that tool grows
 * snapshot fields).
 */

import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type Simulation,
  type SimulationNodeDatum,
  type SimulationLinkDatum,
} from "d3-force";

import type { Snapshot } from "../types.ts";

export interface LayoutNode extends SimulationNodeDatum {
  id: string;
  label: string;
  kind: "region";
}

export interface LayoutLink extends SimulationLinkDatum<LayoutNode> {
  id: string;
  source: string | LayoutNode;
  target: string | LayoutNode;
  kind: "adjacency" | "containment";
}

export interface LayoutResult {
  nodes: LayoutNode[];
  links: LayoutLink[];
}

export interface LayoutOptions {
  /** World-space width to spread nodes across. */
  width?: number;
  /** World-space height to spread nodes across. */
  height?: number;
  /** Layers to feed the simulation. */
  layers?: ReadonlySet<LayoutLink["kind"]>;
}

const DEFAULT_OPTIONS: Required<Pick<LayoutOptions, "width" | "height">> = {
  width: 800,
  height: 600,
};

const DEFAULT_LAYERS: ReadonlySet<LayoutLink["kind"]> = new Set([
  "adjacency",
  "containment",
]);

/**
 * Build the node/link arrays the d3-force simulation operates on.
 *
 * Pure function — no DOM, no simulation side-effects — so it can be
 * unit-tested under Vitest without jsdom.
 */
export function buildLayout(
  snapshot: Snapshot,
  options: LayoutOptions = {},
): LayoutResult {
  const layers = options.layers ?? DEFAULT_LAYERS;
  const nodes: LayoutNode[] = snapshot.regions.map((region) => ({
    id: region.node_id,
    label: region.name,
    kind: "region",
  }));

  const links: LayoutLink[] = [];
  if (layers.has("adjacency")) {
    for (const boundary of snapshot.boundaries) {
      links.push({
        id: boundary.boundary_id,
        source: boundary.region_a,
        target: boundary.region_b,
        kind: "adjacency",
      });
    }
  }
  if (layers.has("containment")) {
    for (const region of snapshot.regions) {
      // Containment edges link every region to the world root parent.
      // The frontend filters the synthetic root out when rendering.
      links.push({
        id: `containment:${region.node_id}`,
        source: region.parent_node,
        target: region.node_id,
        kind: "containment",
      });
    }
  }
  return { nodes, links };
}

/** Configure a d3-force simulation with the standard force budget. */
export function buildSimulation(
  result: LayoutResult,
  options: LayoutOptions = {},
): Simulation<LayoutNode, LayoutLink> {
  const width = options.width ?? DEFAULT_OPTIONS.width;
  const height = options.height ?? DEFAULT_OPTIONS.height;
  return forceSimulation<LayoutNode, LayoutLink>(result.nodes)
    .force(
      "link",
      forceLink<LayoutNode, LayoutLink>(result.links)
        .id((node) => node.id)
        .distance(80)
        .strength(0.5),
    )
    .force("charge", forceManyBody<LayoutNode>().strength(-180))
    .force("center", forceCenter(width / 2, height / 2))
    .force("collide", forceCollide<LayoutNode>(20));
}
