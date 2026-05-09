import { describe, expect, it, vi } from "vitest";

import { WsClient } from "../src/ws_client.ts";
import type { Snapshot } from "../src/types.ts";

interface FakeSocket {
  url: string;
  sent: string[];
  onopen: (() => void) | null;
  onmessage: ((event: MessageEvent<string>) => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
  readyState: number;
  send(data: string): void;
  close(): void;
}

function makeFakeSocket(url: string): FakeSocket {
  const sock: FakeSocket = {
    url,
    sent: [],
    onopen: null,
    onmessage: null,
    onclose: null,
    onerror: null,
    readyState: 1,
    send(data: string) {
      this.sent.push(data);
    },
    close() {
      this.readyState = 3;
      this.onclose?.();
    },
  };
  return sock;
}

const SNAPSHOT: Snapshot = {
  project_open: true,
  project: { name: "Demo", root: "/tmp/demo" },
  regions: [
    {
      node_id: "region_alpha",
      parent_node: "world_root",
      name: "Alpha",
      spatial_bounds: {
        coords: { coords: [[0, 0], [1, 0], [1, 1], [0, 1]] },
      },
    },
  ],
  boundaries: [],
};

describe("WsClient", () => {
  it("forwards snapshot envelopes to listeners", () => {
    const sock = makeFakeSocket("ws://localhost/ws");
    const client = new WsClient("ws://localhost/ws", {
      socketFactory: () => sock as unknown as WebSocket,
    });
    const seen: Snapshot[] = [];
    client.onSnapshot((s) => seen.push(s));
    client.start();
    sock.onopen?.();
    sock.onmessage?.(
      new MessageEvent("message", {
        data: JSON.stringify({ type: "snapshot", state: SNAPSHOT }),
      }),
    );
    expect(seen).toHaveLength(1);
    expect(seen[0]?.regions[0]?.name).toBe("Alpha");
    client.stop();
  });

  it("applies replace patches as full snapshots", () => {
    const sock = makeFakeSocket("ws://localhost/ws");
    const client = new WsClient("ws://localhost/ws", {
      socketFactory: () => sock as unknown as WebSocket,
    });
    const seen: Snapshot[] = [];
    client.onSnapshot((s) => seen.push(s));
    client.start();
    sock.onopen?.();
    sock.onmessage?.(
      new MessageEvent("message", {
        data: JSON.stringify({
          type: "patch",
          event: "create_region",
          ops: [{ op: "replace", path: "", value: SNAPSHOT }],
        }),
      }),
    );
    expect(seen.at(-1)?.regions).toHaveLength(1);
    client.stop();
  });

  it("propagates status changes to subscribers", () => {
    const sock = makeFakeSocket("ws://localhost/ws");
    const client = new WsClient("ws://localhost/ws", {
      socketFactory: () => sock as unknown as WebSocket,
    });
    const statuses: string[] = [];
    client.onStatus((s) => statuses.push(s));
    client.start();
    sock.onopen?.();
    expect(statuses).toContain("connecting");
    expect(statuses).toContain("open");
    client.stop();
    expect(statuses.at(-1)).toBe("closed");
  });

  it("ignores malformed JSON", () => {
    const sock = makeFakeSocket("ws://localhost/ws");
    const client = new WsClient("ws://localhost/ws", {
      socketFactory: () => sock as unknown as WebSocket,
    });
    const calls = vi.fn();
    client.onSnapshot(calls);
    client.start();
    sock.onopen?.();
    sock.onmessage?.(new MessageEvent("message", { data: "not json" }));
    expect(calls).not.toHaveBeenCalled();
    client.stop();
  });
});
