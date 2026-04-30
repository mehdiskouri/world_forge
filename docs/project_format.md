# Project format

A Forge project is a folder of plain-text JSON. The shape is fixed,
versioned, and validated by Pydantic models in
`forge_mcp/project/schemas.py`. The MCP tool surface (Phase 2 Stage G)
is the canonical writer; this document is for humans who want to
hand-author or hand-inspect a project.

The contract behind this format:

- every file is small, deterministic, and round-trip stable through
  `forge_mcp._io.atomic.dump_json` (sorted keys, two-space indent, no
  ASCII escaping, trailing newline);
- every model uses `extra="forbid"`, so unknown keys fail loudly at
  load time — agents cannot smuggle private fields in;
- the layout mirrors the architecture document
  ([`AGENT/ARCHITECTURE.md`](../AGENT/ARCHITECTURE.md) §3) verbatim;
- every model is published as a JSON Schema under
  [`schemas/`](../schemas/) and CI fails if the source models drift
  from the committed schemas.

## Folder layout

```
my_world/
├── project.json              # ProjectMetadata
├── .gitignore                # pre-seeded; ignores realizations/
├── nodes/
│   └── world.json            # WorldRootNode
├── regions/                  # one <region_id>.json per region
├── edges/                    # one <layer>.json per registered hypergraph layer
│   ├── spatial_containment.json
│   ├── spatial_adjacency.json
│   └── hydrology.json
├── boundaries/               # one <boundary_id>.json per adjacency boundary
├── locks/
│   └── locks.json            # LockStoreFile
├── history/                  # one <event_id>_<kind>.json per recorded event
├── specs/                    # Phase 3 fills these
├── realizations/             # Phase 4; gitignored
└── audits/                   # Phase 5
```

## Walkthrough: build a tiny project by hand

You can assemble a working project with a text editor and let
`forge.open_project` ingest it.

### 1. `project.json`

```json
{
  "blender_version": "5.0.0",
  "bpy_hypergraph_version": "0.0.0",
  "created_at": "2024-01-01T12:00:00+00:00",
  "descriptor_schema_version": "0.1.0",
  "forge_version": "0.0.0+local",
  "modified_at": "2024-01-01T12:00:00+00:00",
  "name": "Demo World",
  "project_id": "00000000-0000-4000-8000-000000000000",
  "registered_layers": [
    "spatial_containment",
    "spatial_adjacency",
    "hydrology"
  ],
  "world_bounds": {
    "kind": "rectangle",
    "max": [10.0, 10.0],
    "min": [0.0, 0.0],
    "units": "meters"
  },
  "world_node_id": "world_root"
}
```

`descriptor_schema_version` must match what this Forge speaks
(`forge_mcp.descriptor.schema.SCHEMA_VERSION`); a mismatch makes
`open_project` refuse to load.

### 2. `nodes/world.json`

```json
{
  "created_at": "2024-01-01T12:00:00+00:00",
  "kind": "world_root",
  "name": "World",
  "node_id": "world_root"
}
```

### 3. One region under `regions/region_alpha.json`

```json
{
  "children": [],
  "created_at": "2024-01-01T12:00:00+00:00",
  "kind": "region",
  "modified_at": "2024-01-01T12:00:00+00:00",
  "name": "Alpha",
  "node_id": "region_alpha",
  "parent_node": "world_root",
  "scale_level": 2,
  "seed": 0,
  "spatial_bounds": {
    "coords": {
      "coords": [
        [0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]
      ]
    },
    "elevation_range": null,
    "kind": "polygon"
  },
  "spec_id": null,
  "structured_descriptor": null,
  "tags": [],
  "tier": "unique"
}
```

`Polygon2D` canonicalises the vertex list to counter-clockwise order
starting from the lex-min vertex, so two polygons with the same shape
compare equal regardless of how a human typed them.

### 4. Empty edge layers

For each registered layer, write `edges/<layer>.json` like:

```json
{ "edges": [], "layer": "spatial_containment" }
```

### 5. Empty `locks/locks.json`

```json
{ "locks": [] }
```

### 6. (Optional) seed history with a creation event

`history/0001_create_project.json`:

```json
{
  "actor": "agent",
  "at": "2024-01-01T12:00:00+00:00",
  "event_id": "0001",
  "kind": "create_project",
  "payload": { "name": "Demo World" }
}
```

The history sequence is monotonic and gap-free; if you write `0001`
and `0003` without `0002`, `forge.history` raises
`HistoryGapError`.

## Validation contract

| Field                            | Validation                                                  |
| -------------------------------- | ----------------------------------------------------------- |
| `Polygon2D.coords`               | ≥3 distinct vertices, non-degenerate, canonical CCW order   |
| `WorldBounds`                    | `min[i] < max[i]` per axis                                  |
| `BoundaryStub`                   | `region_a < region_b` lex-sorted, `length_meters > 0`       |
| `Edge.endpoints`                 | ≥2 endpoints                                                |
| `HistoryEventId`                 | zero-padded ≥4 digits, monotonic across the journal         |
| Every model                      | `extra="forbid"`, `frozen=True`                             |

## See also

- [`AGENT/ARCHITECTURE.md`](../AGENT/ARCHITECTURE.md) §3 — the source of truth.
- [`schemas/`](../schemas/) — generated JSON Schemas for every model.
- [`AGENT/dev_phases/phase2.md`](../AGENT/dev_phases/phase2.md) —
  the implementation plan that produced this format.
