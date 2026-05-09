/**
 * Thin WebSocket client that consumes the canvas-server protocol.
 *
 * Protocol (matches `forge_mcp.server.canvas_server`):
 *   - On connect: server sends `{type: "snapshot", state: <Snapshot>}`.
 *   - On every project mutation: server sends
 *     `{type: "patch", event: <str>, ops: <RFC-6902-array>}`. Phase 6
 *     ships a coarse one-op replace; Phase 7 stays compatible.
 *   - The client periodically sends `{type: "ping"}`; the server
 *     replies `{type: "pong"}`. The server closes idle sockets after
 *     90 s, so a 30 s ping interval is comfortable.
 *
 * The client owns reconnection: on close it backs off exponentially
 * (capped at 5 s) and re-opens; subscribers are notified again with a
 * fresh snapshot the moment the new connection is up.
 */

import type { Snapshot } from "./types.ts";
import { EMPTY_SNAPSHOT } from "./types.ts";

export type WsStatus = "connecting" | "open" | "closed";

export interface SnapshotListener {
  (snapshot: Snapshot): void;
}

export interface StatusListener {
  (status: WsStatus): void;
}

interface ServerMessage {
  type?: string;
  state?: Snapshot;
  ops?: ReadonlyArray<{ op: string; path: string; value?: Snapshot }>;
}

export interface WsClientOptions {
  /** Interval between heartbeat pings, in ms. */
  pingIntervalMs?: number;
  /** Initial reconnect backoff, in ms. Doubles each failure (cap 5 s). */
  initialBackoffMs?: number;
  /** Inject a custom WebSocket factory (used by tests). */
  socketFactory?: (url: string) => WebSocket;
}

const DEFAULT_PING_INTERVAL_MS = 30_000;
const DEFAULT_INITIAL_BACKOFF_MS = 250;
const MAX_BACKOFF_MS = 5_000;

/** Minimal browser-compatible WebSocket client used by both views. */
export class WsClient {
  private socket: WebSocket | null = null;

  private snapshot: Snapshot = EMPTY_SNAPSHOT;

  private status: WsStatus = "closed";

  private readonly snapshotListeners = new Set<SnapshotListener>();

  private readonly statusListeners = new Set<StatusListener>();

  private pingTimer: ReturnType<typeof setInterval> | null = null;

  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  private backoffMs: number;

  private stopped = false;

  private readonly pingIntervalMs: number;

  private readonly initialBackoffMs: number;

  private readonly socketFactory: (url: string) => WebSocket;

  constructor(
    private readonly url: string,
    options: WsClientOptions = {},
  ) {
    this.pingIntervalMs = options.pingIntervalMs ?? DEFAULT_PING_INTERVAL_MS;
    this.initialBackoffMs = options.initialBackoffMs ?? DEFAULT_INITIAL_BACKOFF_MS;
    this.backoffMs = this.initialBackoffMs;
    this.socketFactory = options.socketFactory ?? ((u) => new WebSocket(u));
  }

  /** Subscribe to snapshot updates. Returns an unsubscribe callback. */
  onSnapshot(listener: SnapshotListener): () => void {
    this.snapshotListeners.add(listener);
    if (this.snapshot !== EMPTY_SNAPSHOT) {
      listener(this.snapshot);
    }
    return () => {
      this.snapshotListeners.delete(listener);
    };
  }

  /** Subscribe to status changes. Returns an unsubscribe callback. */
  onStatus(listener: StatusListener): () => void {
    this.statusListeners.add(listener);
    listener(this.status);
    return () => {
      this.statusListeners.delete(listener);
    };
  }

  /** Open the underlying socket. Idempotent. */
  start(): void {
    this.stopped = false;
    if (this.socket !== null) return;
    this.connect();
  }

  /** Close the socket and stop reconnect attempts. */
  stop(): void {
    this.stopped = true;
    this.clearTimers();
    if (this.socket !== null) {
      try {
        this.socket.close();
      } catch {
        // socket already closed
      }
    }
    this.socket = null;
    this.setStatus("closed");
  }

  /** Latest snapshot received from the server. */
  currentSnapshot(): Snapshot {
    return this.snapshot;
  }

  /** Apply a server message. Exposed for tests. */
  ingest(raw: string): void {
    let parsed: ServerMessage;
    try {
      parsed = JSON.parse(raw) as ServerMessage;
    } catch {
      return;
    }
    if (parsed.type === "snapshot" && parsed.state !== undefined) {
      this.applySnapshot(parsed.state);
    } else if (parsed.type === "patch" && parsed.ops !== undefined) {
      for (const op of parsed.ops) {
        if (op.op === "replace" && op.path === "" && op.value !== undefined) {
          this.applySnapshot(op.value);
        }
      }
    }
  }

  private connect(): void {
    if (this.stopped) return;
    this.setStatus("connecting");
    let socket: WebSocket;
    try {
      socket = this.socketFactory(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;
    socket.onopen = () => {
      this.backoffMs = this.initialBackoffMs;
      this.setStatus("open");
      this.startPing();
    };
    socket.onmessage = (event: MessageEvent<string>) => {
      this.ingest(event.data);
    };
    socket.onerror = () => {
      // The close handler does the reconnect bookkeeping.
    };
    socket.onclose = () => {
      this.socket = null;
      this.stopPing();
      this.setStatus("closed");
      this.scheduleReconnect();
    };
  }

  private applySnapshot(snapshot: Snapshot): void {
    this.snapshot = snapshot;
    for (const listener of this.snapshotListeners) {
      listener(snapshot);
    }
  }

  private setStatus(status: WsStatus): void {
    if (this.status === status) return;
    this.status = status;
    for (const listener of this.statusListeners) {
      listener(status);
    }
  }

  private startPing(): void {
    this.stopPing();
    this.pingTimer = setInterval(() => {
      if (this.socket?.readyState === WebSocket.OPEN) {
        this.socket.send(JSON.stringify({ type: "ping" }));
      }
    }, this.pingIntervalMs);
  }

  private stopPing(): void {
    if (this.pingTimer !== null) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private scheduleReconnect(): void {
    if (this.stopped) return;
    if (this.reconnectTimer !== null) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, this.backoffMs);
    this.backoffMs = Math.min(this.backoffMs * 2, MAX_BACKOFF_MS);
  }

  private clearTimers(): void {
    this.stopPing();
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }
}

/** Resolve the WebSocket URL from the current document location. */
export function resolveWsUrl(loc: Location = window.location): string {
  const proto = loc.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${loc.host}/ws`;
}
