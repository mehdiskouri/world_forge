# Verifying the v1 MCP tool surface from Claude Code

This walkthrough is the manual companion to the Phase 2 verification line in
[`AGENT/ROADMAP.md`](../AGENT/ROADMAP.md):

> create+open+save project via MCP from Claude Code; agent lists regions;
> project directory diffable in git; `pytest` green; CI green.

It takes you from a fresh clone to driving the 19 v1 tools through Claude Code
(or any MCP-capable host) and verifying that the on-disk project produced by
the agent matches the format documented in
[`docs/project_format.md`](project_format.md).

The flow is intentionally end-to-end: install → register MCP server → handshake →
exercise each tool family → inspect the resulting tree on disk → tear down.

---

## 0. Prerequisites

| Requirement       | Why                                                     |
| ----------------- | ------------------------------------------------------- |
| Linux (or macOS)  | dev target; Windows not supported in v1                 |
| Python 3.13       | enforced by `pyproject.toml` (`requires-python`)        |
| `uv` ≥ 0.9        | sole supported package manager                          |
| `git`             | every project tree is meant to be diffable              |
| Claude Code (CLI) | the v1 reference agent host; any MCP host with stdio works |

Optional but recommended:

- `jq` for inspecting the JSON files the server writes;
- `tree` for screenshotting the project layout.

Blender 5.0.x is **not** required for Phase 2 — the realizer arrives in Phase 4.

---

## 1. Install Forge locally

```bash
git clone https://github.com/mehdiskouri/world_forge.git
cd world_forge
uv sync                       # creates .venv, resolves deps from uv.lock
uv run pre-commit install     # one-time hook setup (recommended)
```

Sanity-check the install gates the same way CI does:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q --cov=forge_mcp --cov-branch --cov-fail-under=90
uv run forge-schema-export --check
```

All five must be green before you proceed; if any fails, the agent integration
will fail in subtler ways.

---

## 2. Confirm the `forge-mcp` entry point

The MCP server is installed as a console script by `pyproject.toml`:

```toml
[project.scripts]
forge-mcp = "forge_mcp.server.mcp:main"
```

Verify it resolves inside the project's virtual environment:

```bash
uv run which forge-mcp
uv run forge-mcp --help        # prints SDK help / version
```

The exact absolute path printed by `uv run which forge-mcp` is what you give
to Claude Code in the next step. Do **not** rely on `forge-mcp` being on your
shell `PATH` outside `uv run`; the wrapper sets up the venv for you.

---

## 3. Register the server with Claude Code

Claude Code manages MCP servers through its `claude mcp` CLI; there is no
hand-edited JSON config to maintain. Add Forge as a stdio server scoped to
your user account:

```bash
claude mcp add forge \
  --scope user \
  --transport stdio \
  -- /workspace/world_forge/.venv/bin/forge-mcp
```

Anything after the bare `--` is the command Claude Code will spawn; flags
and env vars for the server itself go before it (`--env KEY=VALUE`,
`--cwd /some/path`, etc.). For Phase 2 no env vars or args are needed.

Useful sibling commands:

```bash
claude mcp list                # see every registered server and its scope
claude mcp get forge           # show the exact command + scope for "forge"
claude mcp remove forge        # tear it down when you're done
```

Scopes (pick one when adding):

| Scope     | Stored in                          | When to use                                     |
| --------- | ---------------------------------- | ----------------------------------------------- |
| `local`   | per-project, gitignored            | quick experiments tied to one repo              |
| `user`    | your home dir, all projects        | normal dev workflow — recommended for Forge     |
| `project` | committed to the repo (`.mcp.json`) | sharing a server config with collaborators      |

Notes:

- Use the **absolute** path returned by `uv run which forge-mcp`. The CLI
  does not expand `~` or resolve `PATH` lazily.
- The transport is stdio; no port, no socket. Forge does not support SSE
  or HTTP transports in v1.
- `claude mcp add` writes the registration immediately; Claude Code picks
  it up on the next session start. If a session is already open, run
  `/mcp` inside it to force a re-handshake, or restart the session.

### 3.1 Equivalent registration in other hosts

| Host           | Where                                                  |
| -------------- | ------------------------------------------------------ |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) — `mcpServers` JSON object with the same `command` / `args` / `env` shape |
| Cursor         | Settings → MCP → add stdio server with the same command |

---

## 4. Handshake: confirm the 19 tools are visible

Open a fresh Claude Code session and ask:

> List every tool whose name starts with `forge.`.

You should see exactly these 19 tool names:

```
forge.ping
forge.echo
forge.get_descriptor_schema
forge.create_project
forge.open_project
forge.save_project
forge.close_project
forge.create_region
forge.update_region
forge.delete_region
forge.list_regions
forge.get_region
forge.query_layer
forge.list_boundaries
forge.inspect_boundary
forge.history
forge.undo
forge.list_locks
```

Then verify the server is alive and reporting the version Forge expects:

> Call `forge.ping`.

The result should look like `{"ok": true, "result": {"name": "forge", "version": "..."}}`
(envelope shape detailed in §6).

If the tool list is missing or `forge.ping` errors, the failure is almost
always in step 3 (wrong path, wrong JSON shape, host not restarted). The
server itself does not need network access and never logs to stdout — its
stdout is owned by the MCP transport.

---

## 5. Walkthrough: drive a project end-to-end through the agent

Pick a workspace directory the agent can write to (e.g.,
`/tmp/forge-walkthrough`). Then issue these prompts to the agent in order.
Every step prints what to expect.

### 5.1 Create a fresh project

> Use `forge.create_project` to create a project named "Walkthrough" at
> `/tmp/forge-walkthrough/demo` with a 100×100 meter rectangular world
> bounds from (0,0) to (100,100).

The tool call payload the agent should send:

```json
{
  "path": "/tmp/forge-walkthrough/demo",
  "name": "Walkthrough",
  "world_bounds": {
    "kind": "rectangle",
    "min": [0.0, 0.0],
    "max": [100.0, 100.0],
    "units": "meters"
  }
}
```

Expected envelope:

```json
{ "ok": true, "result": { "project_id": "...", "path": ".../demo" } }
```

Verify on disk:

```bash
tree /tmp/forge-walkthrough/demo
cat /tmp/forge-walkthrough/demo/project.json | jq .
```

You should see the layout described in
[`docs/project_format.md`](project_format.md): `project.json`, `nodes/`,
`regions/`, `edges/`, `boundaries/`, `locks/`, `history/`, `specs/`,
`realizations/`, `audits/`, plus a pre-seeded `.gitignore`.

### 5.2 Reopen it from a clean session

Close the agent session, start a new one, and:

> Open `/tmp/forge-walkthrough/demo` with `forge.open_project`.

This proves persistence: nothing lives in agent memory, every byte that
matters is on disk. A `project_format_error` or `project_version_mismatch`
envelope here means the on-disk tree is corrupt or written by a different
schema version — not a tool bug.

### 5.3 Create two adjacent regions

> Create a region named "North" with polygon
> `[[0,50],[100,50],[100,100],[0,100]]`. Then create a second region
> named "South" with polygon `[[0,0],[100,0],[100,50],[0,50]]`.

After both `forge.create_region` calls succeed, ask:

> Call `forge.list_regions` and `forge.list_boundaries`.

Expected:

- `list_regions` returns two summaries sorted by `node_id`;
- `list_boundaries` returns one boundary stub for the shared edge between
  North and South. This proves automatic adjacency detection (Stage E /
  PRD F-6.6).

### 5.4 Force the validation paths to fire

Provoke each error code so you can see the structured envelope:

> Try to create a third region with polygon `[[10,10],[90,10],[90,90],[10,90]]`.

Expected: `{"ok": false, "error": {"code": "region_overlap", ...}}` because
that polygon overlaps both existing regions.

> Try to update region "North" with polygon `[[0,0],[1,0]]`.

Expected: `invalid_polygon` (fewer than 3 distinct vertices).

> Call `forge.update_region` with `region_id` set to `"region_ghost"`.

Expected: `unknown_region`.

### 5.5 Inspect history

> Call `forge.history` with no arguments.

You should see one event per state-mutating call so far (create_project,
create_region ×2, the failed mutations are **not** recorded). Events are
returned oldest-first, monotonically numbered, and gap-free.

> Call `forge.undo`.

Expected (Phase-2 contract): `{"ok": false, "error": {"code": "not_implemented",
"details": {"available_in_phase": 7}}}`. This is intentional — the lock /
undo machinery lands in Phase 7.

### 5.6 Persist and close

> Call `forge.save_project` then `forge.close_project`.

After `close_project`, calling any region tool must return
`no_open_project`. Reopening the project must show identical state.

### 5.7 Diff-friendliness check

```bash
cd /tmp/forge-walkthrough/demo
git init && git add -A && git commit -m "initial state"
# In a new agent session: open, create one more region, save, close.
git diff --stat
git diff regions/
```

The diff should be small, human-readable, and confined to the files the
mutation actually touched (one new `regions/<id>.json`, one new
`history/000N_create_region.json`, possibly one updated edge layer and one
new boundary). This is the "project directory diffable in git" half of the
verification line.

---

## 6. Envelope contract reference

Every tool returns one of two shapes. Knowing them by sight is the fastest
way to debug a session.

Success:

```json
{ "ok": true, "result": <tool-specific JSON> }
```

Failure:

```json
{
  "ok": false,
  "error": {
    "code": "<stable_machine_readable_code>",
    "message": "<human-readable explanation>",
    "details": <optional tool-specific JSON or null>
  }
}
```

Stable error codes you should expect to see during this walkthrough:

| Code                       | Meaning                                              |
| -------------------------- | ---------------------------------------------------- |
| `project_already_exists`   | `create_project` target path is non-empty            |
| `project_not_found`        | `open_project` target path missing                   |
| `project_format_error`     | Tree present but malformed / wrong schema version    |
| `project_version_mismatch` | `descriptor_schema_version` ≠ this Forge             |
| `no_open_project`          | Region / history / lock tool called with nothing open |
| `invalid_world_bounds`     | `WorldBounds` validation failed                      |
| `invalid_polygon_coords`   | Coords not a list-of-pairs at the tool boundary      |
| `invalid_polygon`          | Polygon failed `Polygon2D` validation                |
| `invalid_descriptor`       | `StructuredDescriptor` validation failed             |
| `region_overlap`           | New / updated polygon overlaps an existing region    |
| `unknown_region`           | `region_id` not in the project                       |
| `unknown_layer`            | `query_layer` got a layer the project does not register |
| `unknown_boundary`         | `inspect_boundary` got an unknown id                 |
| `history_error`            | History log corrupt (e.g., gap)                      |
| `not_implemented`          | Tool stubbed for a later phase (see `details.available_in_phase`) |

A tool returning anything other than these two shapes is a bug; please open
an issue with the tool name and the offending payload.

---

## 7. Tear-down

```bash
rm -rf /tmp/forge-walkthrough
```

Then remove the `forge` entry from your MCP config if you no longer want
the server registered. The Forge process exits when its host disconnects —
there is no daemon to stop.

---

## 8. Where to look when something fails

| Symptom                                             | Most likely cause                                                  |
| --------------------------------------------------- | ------------------------------------------------------------------ |
| Tool list missing in Claude Code                    | `command` path passed to `claude mcp add` is wrong, or session wasn't restarted (`/mcp` in-session, or new session) |
| `forge.ping` works, region tools all fail with `no_open_project` | You're calling them in a session where `open_project` hasn't been called |
| `project_version_mismatch` on a fresh project       | Two Forge installs on the same machine producing tree A vs reading with B |
| `region_overlap` you didn't expect                  | `Polygon2D` canonicalisation: vertex order doesn't matter, the shape does |
| Test run green locally but agent sees stale tools   | Host caches the tool list; restart it                              |

For deeper debugging:

- run the gate (`uv run pytest -q ...`) — if it's green, the server logic is
  fine and the issue is in the host wiring;
- read the relevant tool module under
  [`forge_mcp/server/tools/`](../forge_mcp/server/tools/) — each is small,
  single-purpose, and lists every error code it can return;
- consult [`docs/project_format.md`](project_format.md) for the on-disk
  contract the tools enforce.
