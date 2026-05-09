/**
 * Entry point for the Forge popup canvas.
 *
 * Reads ``?view=`` from the location URL and mounts either the Konva
 * canvas view or the d3-force connection-map view. Both views share a
 * single ``WsClient`` and toolbar.
 */

import Konva from "konva";

import { renderSnapshot, DEFAULT_VIEWPORT } from "./canvas/draw.ts";
import { buildLayout, buildSimulation } from "./connection_map/layout.ts";
import { WsClient, resolveWsUrl, type WsStatus } from "./ws_client.ts";

type ViewName = "canvas" | "connection-map";

function readView(): ViewName {
  const params = new URLSearchParams(window.location.search);
  const view = params.get("view");
  return view === "connection-map" ? "connection-map" : "canvas";
}

function highlightToolbar(view: ViewName): void {
  for (const link of document.querySelectorAll<HTMLAnchorElement>(
    "#toolbar a[data-view]",
  )) {
    if (link.dataset["view"] === view) {
      link.classList.add("active");
    } else {
      link.classList.remove("active");
    }
  }
}

function statusBadge(status: WsStatus): string {
  switch (status) {
    case "open":
      return "live";
    case "connecting":
      return "connecting…";
    case "closed":
      return "disconnected";
  }
}

function mountCanvasView(stage: HTMLElement, client: WsClient): void {
  const konvaStage = new Konva.Stage({
    container: stage as HTMLDivElement,
    width: stage.clientWidth || DEFAULT_VIEWPORT.width,
    height: stage.clientHeight || DEFAULT_VIEWPORT.height,
  });
  const layer = new Konva.Layer();
  konvaStage.add(layer);
  client.onSnapshot((snapshot) => {
    renderSnapshot(layer, snapshot, {
      ...DEFAULT_VIEWPORT,
      width: konvaStage.width(),
      height: konvaStage.height(),
    });
  });
}

function mountConnectionMapView(stage: HTMLElement, client: WsClient): void {
  stage.innerHTML = "";
  const svgNs = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNs, "svg");
  svg.setAttribute("width", "100%");
  svg.setAttribute("height", "100%");
  svg.setAttribute("data-view", "connection-map");
  const linkGroup = document.createElementNS(svgNs, "g");
  linkGroup.setAttribute("stroke", "#5fa9ff");
  linkGroup.setAttribute("stroke-opacity", "0.6");
  const nodeGroup = document.createElementNS(svgNs, "g");
  svg.appendChild(linkGroup);
  svg.appendChild(nodeGroup);
  stage.appendChild(svg);

  client.onSnapshot((snapshot) => {
    const width = stage.clientWidth || 800;
    const height = stage.clientHeight || 600;
    const layout = buildLayout(snapshot);
    linkGroup.replaceChildren();
    nodeGroup.replaceChildren();
    const lineEls = layout.links.map((link) => {
      const line = document.createElementNS(svgNs, "line");
      line.setAttribute("data-edge-kind", link.kind);
      line.setAttribute("stroke-width", link.kind === "containment" ? "1" : "2");
      linkGroup.appendChild(line);
      return line;
    });
    const nodeEls = layout.nodes.map((node) => {
      const group = document.createElementNS(svgNs, "g");
      group.setAttribute("data-node-id", node.id);
      group.setAttribute("class", "node");
      const circle = document.createElementNS(svgNs, "circle");
      circle.setAttribute("r", "10");
      circle.setAttribute("fill", "#2a5fad");
      circle.setAttribute("stroke", "#eaecef");
      circle.setAttribute("stroke-width", "1.5");
      const text = document.createElementNS(svgNs, "text");
      text.setAttribute("dx", "14");
      text.setAttribute("dy", "4");
      text.setAttribute("fill", "#eaecef");
      text.setAttribute("font-size", "12");
      text.textContent = node.label;
      group.appendChild(circle);
      group.appendChild(text);
      nodeGroup.appendChild(group);
      return group;
    });

    const sim = buildSimulation(layout, { width, height });
    sim.on("tick", () => {
      for (const [i, link] of layout.links.entries()) {
        const line = lineEls[i];
        if (line === undefined) continue;
        const source = link.source as { x?: number; y?: number };
        const target = link.target as { x?: number; y?: number };
        line.setAttribute("x1", String(source.x ?? 0));
        line.setAttribute("y1", String(source.y ?? 0));
        line.setAttribute("x2", String(target.x ?? 0));
        line.setAttribute("y2", String(target.y ?? 0));
      }
      for (const [i, node] of layout.nodes.entries()) {
        const el = nodeEls[i];
        if (el === undefined) continue;
        el.setAttribute("transform", `translate(${node.x ?? 0},${node.y ?? 0})`);
      }
    });
  });
}

function bootstrap(): void {
  const view = readView();
  highlightToolbar(view);
  const stage = document.getElementById("stage");
  const status = document.getElementById("status");
  if (stage === null || status === null) return;

  const client = new WsClient(resolveWsUrl());
  client.onStatus((s) => {
    status.textContent = statusBadge(s);
  });
  if (view === "canvas") {
    mountCanvasView(stage, client);
  } else {
    mountConnectionMapView(stage, client);
  }
  client.start();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bootstrap);
} else {
  bootstrap();
}
