# Forge v1 — Architecture

**Status:** Draft (Revision 3)
**Owner:** Mehdi
**Companion to:** PRD v3.0
**Document version:** 3.0

> *Working name: Forge. Replace when branding lands.*

---

## 1. Architectural overview

Forge v1 is an MCP server that owns a structured worldbuilding project format and drives Blender 5.0 to realize it. The agent (Claude Code, Cursor, etc.) is a client of Forge, not a component of it. The user interacts with the agent for conversation and intent, and with the popup canvas page for spatial input. Forge owns everything else.

**Key v3.0 commitments:**
- Forge is purely deterministic Python with zero LLM calls.
- Forge targets Blender 5.0.x specifically (not 4.4 LTS).
- All semantic interpretation of free text happens in the agent's context using the plan skill's embedded descriptor schema.

```
┌──────────────────────────────────────────────────────────────────┐
│                  User's MCP-capable Agent Client                 │
│   (Claude Code, Cursor, Claude Desktop, GitHub Copilot, ...)     │
│                                                                  │
│   - Conversation with user                                       │
│   - Loads Forge skills (forge.plan carries descriptor schema)    │
│   - Extracts structured descriptors from free text               │
│   - Invokes Forge tools with structured input                    │
│   - May spawn audit subagent (v1's only subagent)                │
└────────────────────┬─────────────────────────────────────────────┘
                     │ MCP (stdio or HTTP transport)
                     │ Structured tool calls + image content
┌────────────────────▼─────────────────────────────────────────────┐
│            Forge MCP Server (pure Python, deterministic)         │
│  ┌────────────────────┐  ┌──────────────────────────────────┐   │
│  │  Project Service   │  │  Plan/Realize Loop               │   │
│  │  - load/save       │  │  - structured desc → terrain spec│   │
│  │  - hypergraph      │  │    (deterministic mapping)       │   │
│  │  - history/undo    │  │  - terrain generator (Python)    │   │
│  │  - locks/seeds     │  │  - boundary contract solver      │   │
│  │                    │  │  - realizer engine (over bpy HG) │   │
│  └────────────────────┘  └──────────────────────────────────┘   │
│  ┌────────────────────┐  ┌──────────────────────────────────┐   │
│  │  Canvas Server     │  │  bpy 5.0 Knowledge Hypergraph    │   │
│  │  - HTTP page       │  │  - operators, types, properties  │   │
│  │  - WebSocket sync  │  │  - contexts, effects             │   │
│  └────────────────────┘  └──────────────────────────────────┘   │
└──────┬─────────────────────────────────────┬─────────────────────┘
       │ stdio JSON-RPC                      │ HTTP + WebSocket
       │                                     │
┌──────▼──────────────────┐    ┌─────────────▼──────────────────┐
│  Blender 5.0 (headless) │    │  Popup Canvas Page             │
│  - long-lived process   │    │  - VSCode webview OR           │
│  - bpy 5.0 via stdio    │    │    standalone browser tab      │
│  - IDProperties for     │    │  - draws polygons              │
│    project node linking │    │  - renders connection map      │
└─────────────────────────┘    └────────────────────────────────┘
```

**Note the absence of any external LLM dependency.** Forge makes zero outbound network calls in v1. The agent client may make calls to its own model provider, but those are outside Forge's trust boundary and configuration surface.

**The three planes** (PRD §5.4):

- **Forge plane** — the MCP server and everything it owns. Source of truth. Pure Python.
- **Agent plane** — the user's MCP client. Orchestrates intent, performs descriptor extraction using the plan skill's schema, presents results.
- **User plane** — the human, interacting through agent client and popup canvas.

## 2. Technology choices

| Concern | Choice | Rationale |
|---------|--------|-----------|
| Core language | Python 3.13 | Mehdi's primary stack; mature ecosystem; native bpy 5.0 compatibility |
| MCP framework | Official Python MCP SDK (`mcp` package) | Standard, maintained by Anthropic |
| RPC to Blender | stdio JSON-RPC | Simpler than ZMQ for v1; no extra dependencies; can migrate to ZMQ in v2 |
| Schema validation | Pydantic v2 | First-class typing, JSON Schema export (used by both Forge tools and the descriptor schema) |
| Numerics | numpy, scipy, optionally numba for erosion inner loops | Standard scientific Python |
| Storage | Plain filesystem (JSON files + binary derivatives) | Filesystem is the project format; no database |
| Canvas server | Embedded HTTP server (FastAPI mini-app inside MCP process) + WebSocket | Single process, no separate service |
| Canvas frontend | Vanilla TypeScript + Konva.js (single static page) | Minimal — not a React app |
| LLM | **None inside Forge.** External agent client uses whatever model it's configured for. | Architectural commitment in v3.0 |
| Versioning | Git (user-managed) | Project format is git-friendly |
| Packaging | uv for Python | Modern, fast |
| **Blender version** | **5.0.x (specific patch pinned per build)** | API harmonization, data-block-centric access, type hinting improvements crucial for bpy hypergraph ingestion |

### 2.1 Why Blender 5.0 over 4.4 LTS

V3.0 commits to Blender 5.0 over the safer 4.4 LTS. The reasoning, summarized for architectural reference:

- **API harmonization.** 5.0 unified naming conventions across render engines (Eevee, Cycles, VSE), reducing ambiguity in the bpy hypergraph effect annotations.
- **Data-block-centric access.** 5.0 expanded `bpy.data` paths that don't require window/area/region context. Forge's realizer engine prefers these paths over `bpy.ops` to minimize "poll failed" errors during agent-triggered execution.
- **PEP 484 compliance.** 5.0's improved type hinting makes programmatic introspection during bpy hypergraph ingestion significantly more reliable. Operators expose their parameter types more consistently.
- **Geometry nodes maturation.** 5.0's full integration of the legacy modifier stack into the geometry node logic makes the scene a unified graph — better suited to Forge's hypergraph reasoning model. Useful for v2 vegetation and biome work even though v1 doesn't lean on it.

**Known risk:** 5.0's IDProperty refactor is bleeding-edge. Forge uses custom IDProperties to link Blender objects back to project node IDs. The pre-week-1 spike validates IDProperty round-trip; if unstable, fallback is scene-level metadata dict keyed by object name.

> **Phase 1 verdict (validated 2026-04-30):** Pinned to **Blender 5.0.0** (`/usr/bin/blender`, env var `FORGE_BLENDER_BIN`). Stdio JSON-RPC adapter
> ([scripts/blender/adapter.py](../scripts/blender/adapter.py)) measured at sub-millisecond
> ping latency in-process. **IDProperty round-trip works** end-to-end against
> a real Blender 5.0.0 binary (3 integration tests in
> [tests/realize/test_blender_proc.py](../tests/realize/test_blender_proc.py));
> the scene-metadata-dict fallback is **not needed for v1**.
> See [docs/spikes/02-blender-rpc-adapter.md](../docs/spikes/02-blender-rpc-adapter.md).

## 3. Project format and folder layout

```
my_world/
├── project.json                    # top-level metadata, includes blender_version
├── nodes/
│   ├── world.json
│   └── regions/
│       ├── alpheim_north.json
│       └── ...
├── edges/
│   ├── spatial_containment.json
│   ├── spatial_adjacency.json
│   └── hydrology.json
├── specs/
│   ├── spec_a8f3e1.json            # content-addressable
│   └── ...
├── boundaries/
│   ├── bdy_alpheim_n_c.json
│   └── ...
├── locks/
│   └── locks.json
├── audits/
│   ├── audit_001.json
│   └── ...
├── history/
│   ├── 0001_create_region.json
│   └── ...
├── realizations/                   # gitignored
│   └── blender/
│       ├── alpheim_north.blend
│       ├── alpheim_north_ortho.png
│       └── alpheim_north_perspective.png
└── .gitignore
```

Note: `cache/descriptor_compile/` is gone — there is no descriptor compilation in Forge anymore.

### 3.1 project.json

```json
{
  "project_id": "uuid",
  "name": "My World",
  "forge_version": "0.1.0",
  "blender_version": "5.0.2",
  "bpy_hypergraph_version": "blender-5.0.2-v1",
  "descriptor_schema_version": "1.0",
  "created_at": "2026-04-29T...",
  "modified_at": "...",
  "world_node_id": "world_root",
  "registered_layers": [
    "spatial_containment",
    "spatial_adjacency",
    "hydrology"
  ],
  "world_bounds": {
    "kind": "rectangle",
    "min": [0, 0],
    "max": [10000, 10000],
    "units": "meters"
  }
}
```

The `llm_config` field from v2.0 is removed. The pinned Blender patch version is now first-class metadata.

### 3.2 Region node schema

Unchanged from v2.0.

```json
{
  "node_id": "region_alpheim_north",
  "kind": "region",
  "tier": "unique",
  "scale_level": 2,
  "parent_node": "world_root",
  "children": [],
  "name": "Alpheim North",
  "spec_id": "spec_a8f3e1",
  "spatial_bounds": {
    "kind": "polygon",
    "coords": [[100, 100], [500, 100], [500, 400], [100, 400]],
    "elevation_range": [1800, 2900]
  },
  "tags": [],
  "seed": 8472619384,
  "created_at": "...",
  "modified_at": "..."
}
```

### 3.3 Structured descriptor schema (v1.0)

This schema is now load-bearing — it's what the agent uses to extract structured input from user free text. Embedded in the plan skill, available via `get_descriptor_schema` tool, validated by Forge on every region creation/update.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ForgeStructuredDescriptor",
  "type": "object",
  "required": ["terrain"],
  "properties": {
    "terrain": {
      "type": "object",
      "required": ["primary"],
      "properties": {
        "primary": {
          "type": "string",
          "enum": [
            "alpine_valley",
            "alpine_peaks",
            "rolling_hills",
            "plains",
            "desert_mesa",
            "desert_dunes",
            "boreal_lowland",
            "marsh",
            "volcanic_cone",
            "coastal_cliffs",
            "river_valley",
            "canyon"
          ]
        },
        "elevation_band": {
          "type": "array",
          "items": {"type": "number"},
          "minItems": 2,
          "maxItems": 2,
          "description": "[low_meters, high_meters]"
        },
        "ruggedness": {
          "type": "number",
          "minimum": 0,
          "maximum": 1,
          "description": "0 = smooth, 1 = highly broken terrain"
        },
        "notes": {"type": "string", "maxLength": 200}
      }
    },
    "hydrology": {
      "type": "object",
      "properties": {
        "has_stream": {"type": "boolean"},
        "stream_character": {
          "type": "string",
          "enum": ["alpine_creek", "meandering_river", "dry_wash", "none"]
        }
      }
    }
  }
}
```

This is intentionally narrow for v1. v2 expands with biome, settlement, culture, era axes. The schema is versioned so v2 expansions don't break v1 projects.

### 3.4 Terrain spec schema

Largely unchanged from v2.0; descriptor section now reflects no LLM provenance.

```json
{
  "spec_id": "spec_a8f3e1",
  "spec_version": "1.0",
  "node_id": "region_alpheim_north",
  "node_kind": "region",
  "seed": 8472619384,
  "descriptor": {
    "structured": {
      "terrain": {
        "primary": "alpine_valley",
        "elevation_band": [1800, 2900],
        "ruggedness": 0.8,
        "notes": "rugged with prominent valley floor"
      },
      "hydrology": {
        "has_stream": true,
        "stream_character": "alpine_creek"
      }
    },
    "provenance": {
      "supplied_by": "agent",
      "schema_version": "1.0",
      "received_at": "..."
    }
  },
  "asset_source": "procedural",
  "axes": {
    "terrain": {
      "generator": "ridged_multifractal_v1",
      "params": {
        "octaves": 7,
        "lacunarity": 2.1,
        "persistence": 0.45,
        "warp": 0.3,
        "scale_meters": 800
      },
      "post_passes": [
        {"kind": "hydraulic_erosion", "iterations": 80, "rain": 0.02, "evaporation": 0.05},
        {"kind": "thermal_erosion", "talus_angle_degrees": 33, "iterations": 40}
      ],
      "feature_injectors": [
        {"kind": "stream", "anchor_in": null, "anchor_out": null, "width_meters": 4.0, "carving_depth": 2.0}
      ],
      "elevation_band": [1800, 2900],
      "resolution_meters_per_pixel": 2.0
    }
  },
  "boundary_requirements": [...],
  "summary": {...},
  "generation_metadata": {
    "compiler_version": "0.1.0",
    "generators_used": ["ridged_multifractal_v1"],
    "bpy_hypergraph_version": "blender-5.0.2-v1",
    "blender_version": "5.0.2",
    "parent_spec_hash": null,
    "conflicts_resolved": []
  }
}
```

The `descriptor.provenance.compiled_by: "llm"` field from v2.0 is replaced with `supplied_by: "agent"`. No LLM, no model name, no compile timestamp.

### 3.5 Other schemas

Edge, boundary, lock, history, audit schemas: unchanged from v2.0.

## 4. Forge MCP Server — internal architecture

```
forge_mcp/
├── server/
│   ├── mcp.py            # MCP server entrypoint, tool registration
│   ├── tools/            # one module per tool group
│   │   ├── projects.py
│   │   ├── regions.py
│   │   ├── generation.py
│   │   ├── locks.py
│   │   ├── inspection.py
│   │   ├── hypergraph.py
│   │   ├── schema.py     # get_descriptor_schema
│   │   └── history.py
│   └── canvas_server.py
├── project/
│   ├── service.py
│   ├── schemas.py        # Pydantic models incl. structured descriptor schema
│   ├── history.py
│   └── locks.py
├── descriptor/           # NEW: deterministic descriptor → spec mapping
│   ├── schema.py         # the structured descriptor Pydantic model
│   ├── validate.py       # validation, structured error reporting
│   └── map_to_spec.py    # deterministic Python: structured → terrain spec params
├── generate/
│   ├── terrain.py
│   ├── stream.py
│   └── deterministic.py
├── boundary/
│   ├── adjacency.py
│   ├── contract.py
│   └── apply.py
├── analyze/
│   ├── terrain_analysis.py
│   └── stream_analysis.py
├── realize/
│   ├── engine.py
│   ├── blender_proc.py
│   ├── rpc.py
│   └── macros.py
├── bpy_hypergraph/
│   ├── ingest.py         # Blender 5.0-targeted ingestion
│   ├── data/
│   │   ├── operators.json
│   │   ├── types.json
│   │   ├── effects.json
│   │   └── curated_sequences.json
│   └── query.py
├── skills/
│   ├── forge.plan/SKILL.md         # carries descriptor schema
│   ├── forge.visualize/SKILL.md
│   ├── forge.audit/SKILL.md
│   ├── forge.cleanup/SKILL.md
│   └── forge.connect/SKILL.md
├── canvas_page/
│   ├── index.html
│   ├── canvas.ts
│   ├── connection_map.ts
│   └── styles.css
└── tests/
```

The previous `compile/` module (LLM-driven) is replaced by `descriptor/` (pure deterministic).

### 4.1 The plan/realize loop (no compile stage)

```python
def regenerate_region(region_id: str, options: RegenerateOptions) -> RegenerationResult:
    region = project.load_region(region_id)
    locks = project.locks.for_region(region_id)
    boundaries = project.boundaries.for_region(region_id)

    # 1. Validate the structured descriptor (already supplied by agent at create/update time)
    structured_descriptor = region.structured_descriptor  # Pydantic model
    descriptor.validate(structured_descriptor)

    # 2. Map structured descriptor → terrain spec (deterministic Python)
    spec = descriptor.map_to_spec(structured_descriptor, region.seed)

    # 3. Apply locks
    spec = locks.apply_to_spec(spec, locks)

    # 4. Resolve boundary contracts
    for bdy in boundaries:
        contract = boundary.contract.resolve(bdy, region, neighbors_of(region))
        spec = boundary.apply.inject_constraints(spec, contract)

    # 5. Generate heightmap (deterministic)
    heightmap, stream_geometry = generate.terrain.run(spec, locks, contracts)

    # 6. Realize in Blender 5.0 via realizer engine
    realization = realize.engine.execute_macro(
        macro="realize_region",
        inputs={"heightmap": heightmap, "stream": stream_geometry, "spec": spec},
    )

    # 7. Analyze for perception payload
    analysis = analyze.terrain_analysis.compute(heightmap, stream_geometry)

    # 8. Persist
    project.save_spec(spec)
    project.save_region(region.with_realization(realization))
    project.history.append(RegenerationEvent(...))

    # 9. Notify canvas
    canvas_server.broadcast_state_update(project.snapshot())

    return RegenerationResult(
        spec=spec,
        analysis=analysis,
        preview_image=realization.preview_path,
        full_image=realization.full_path,
    )
```

The loop is now strictly deterministic. No network calls, no caching needed, no retry logic for LLM failures, no schema retry loops.

### 4.2 The descriptor → spec mapping

Pure Python, lookup-table driven:

```python
TERRAIN_PROFILES = {
    "alpine_valley": {
        "octaves_base": 7,
        "lacunarity_base": 2.1,
        "persistence_base": 0.45,
        "warp_base": 0.3,
        "erosion_iterations_base": 80,
        "talus_angle_base": 33,
    },
    "rolling_hills": {
        "octaves_base": 5,
        "lacunarity_base": 2.0,
        "persistence_base": 0.5,
        "warp_base": 0.15,
        "erosion_iterations_base": 40,
        "talus_angle_base": 28,
    },
    # ... one entry per enum value in the descriptor schema
}

def map_to_spec(descriptor: StructuredDescriptor, seed: int) -> TerrainSpec:
    profile = TERRAIN_PROFILES[descriptor.terrain.primary]
    ruggedness = descriptor.terrain.ruggedness or 0.5

    # Ruggedness modulates octaves, persistence, erosion intensity
    octaves = profile["octaves_base"] + int(ruggedness * 3)
    persistence = profile["persistence_base"] + ruggedness * 0.15
    erosion_iterations = int(profile["erosion_iterations_base"] * (1 + ruggedness * 0.5))

    feature_injectors = []
    if descriptor.hydrology and descriptor.hydrology.has_stream:
        feature_injectors.append({
            "kind": "stream",
            "anchor_in": None,
            "anchor_out": None,
            "width_meters": stream_width_for(descriptor.hydrology.stream_character),
            "carving_depth": stream_depth_for(descriptor.hydrology.stream_character),
        })

    return TerrainSpec(
        seed=seed,
        axes={"terrain": TerrainAxisSpec(
            generator="ridged_multifractal_v1",
            params={"octaves": octaves, "persistence": persistence, ...},
            post_passes=[...],
            feature_injectors=feature_injectors,
            elevation_band=descriptor.terrain.elevation_band,
        )},
        ...
    )
```

Iterating on this mapping during week 2 is straightforward — change a profile entry, re-run the eval set, see the visual difference. No LLM in the loop means tight feedback.

### 4.3 Determinism

All generators take an explicit RNG; no module-level state. Seeds derived deterministically per pass. With LLM removal, the determinism contract has no asterisks.

## 5. The bpy Knowledge Hypergraph (Blender 5.0)

The bpy hypergraph is Forge's typed command vocabulary for Blender 5.0. Structurally identical to the worldbuilding hypergraph but lives separately in `forge_mcp/bpy_hypergraph/data/` and loads once at startup.

### 5.1 Node types

- **Module nodes** — `bpy.ops.mesh`, `bpy.ops.object`, `bpy.data.materials`. **In 5.0, `bpy.data.*` paths are preferred where available** since they don't require context.
- **Operator nodes** — `bpy.ops.mesh.primitive_plane_add`, etc. Each carries: signature, parameters with types and defaults, poll function, bl_options, description, **and a flag indicating whether a `bpy.data` equivalent exists** (5.0's harmonization gives us this for many operators).
- **Property nodes** — settable properties on Blender data.
- **Type nodes** — `Mesh`, `Material`, `Modifier`, `Object`. Blender's type system as graph structure.
- **Context nodes** — `OBJECT_MODE`, `EDIT_MODE`, etc. (5.0 reduces but doesn't eliminate context requirements.)
- **Effect nodes** — `creates_object`, `modifies_mesh`, etc.

### 5.2 Layers

- **containment**, **signature**, **context_requirement**, **effect_layer**, **type_relations**, **common_sequences**, **documentation** — same as v2.0.
- **NEW: alternative_paths** — for operators that have `bpy.data` equivalents in 5.0, this layer encodes the preferred non-ops path. The realizer engine prefers `bpy.data` paths to minimize context-related failures.

### 5.3 Ingestion pipeline (Blender 5.0)

Built once during pre-week-1, regenerated on Blender patch version updates.

```python
def ingest_bpy_hypergraph(blender_executable: Path) -> BpyHypergraph:
    # Step 1: Introspect bpy 5.0 programmatically
    introspected = run_in_blender(blender_executable, introspect_script)
    # 5.0's improved PEP 484 compliance gives us better parameter type info

    # Step 2: Parse Sphinx HTML docs for 5.0
    docs = parse_blender_sphinx_docs(blender_5_0_docs_path)

    # Step 3: Match introspection ↔ docs
    enriched = enrich(introspected, docs)

    # Step 4: Hand-curated effect annotations
    effects = load_curated_effects("effects_v1.json")

    # Step 5: Identify bpy.data alternatives for ops (5.0 harmonization)
    alternative_paths = derive_alternative_paths(enriched)

    # Step 6: Hand-curated common sequences (v1 macros)
    sequences = load_curated_sequences("sequences_v1.json")

    # Step 7: Build hypergraph
    return build_hypergraph(enriched, effects, alternative_paths, sequences)
```

The ingestion is targeted at a specific 5.0 patch version. Forge stores the patch version with the hypergraph (e.g., `blender-5.0.2-v1`) and refuses to load if the running Blender doesn't match.

### 5.4 The v1 operator set (~30–50 operators)

> **Phase 1 verdict (validated 2026-04-30):** ingestion against the
> Blender 5.0.0 introspector
> ([scripts/blender/introspect.py](../scripts/blender/introspect.py))
> emitted **2 441 raw operators**; the curated v1 set is **24 operators**
> spanning **11 types**, **24 effect annotations**, and **7 alternative-paths**
> entries (`schema_tag = blender-5.0.0-v1`).
> See [docs/spikes/01-bpy-hypergraph.md](../docs/spikes/01-bpy-hypergraph.md).

Curated for v1 macros. In 5.0, more of these can be expressed as direct `bpy.data` calls:

- **Mesh creation:** `bpy.ops.mesh.primitive_plane_add` (or `bpy.data.meshes.new` + manual vertex setup for finer control)
- **Modifiers:** `bpy.data.objects[name].modifiers.new(name, type)` — preferred over `bpy.ops.object.modifier_add` in 5.0
- **Subdivision, shading:** Mix of ops and direct API
- **Image/heightmap loading:** `bpy.data.images.load()` — direct
- **Materials:** `bpy.data.materials.new()` + node tree manipulation — direct
- **Curves (streams):** `bpy.data.curves.new()` + spline setup — direct
- **Lights:** `bpy.data.lights.new()` + `bpy.data.objects.new()` — direct
- **Cameras:** `bpy.data.cameras.new()` + placement — direct
- **World/sky:** `bpy.data.worlds.new()` + node setup — direct
- **Render:** `bpy.ops.render.render()` — context-needed
- **Save:** `bpy.ops.wm.save_as_mainfile()` — context-needed

The v1 macros lean heavily on `bpy.data` paths where 5.0 enables it. This is the architectural payoff for choosing 5.0 over 4.4 LTS.

### 5.5 The v1 macros

1. **`reset_scene`** — clear via `bpy.data` collections
2. **`create_terrain_from_heightmap`** — load image, create mesh with subdivisions, add displace modifier (mix of data + ops)
3. **`apply_terrain_material`** — create material with elevation/slope-driven nodes (data only)
4. **`carve_stream`** — create curve, convert to mesh, apply water material (mix)
5. **`set_camera_overview`** — create camera, set transform (data only)
6. **`add_basic_lighting`** — create sun lamp, set world HDRI (data only)
7. **`render_preview`** — render to PNG (ops, context-needed)
8. **`save_blend`** — save .blend (ops, context-needed)

Composite: **`realize_region`** — full sequence.

### 5.6 IDProperties for project node linking

Forge sets a custom IDProperty on each Blender object identifying its source project node:

```python
obj["forge_node_id"] = "region_alpheim_north"
obj["forge_spec_id"] = "spec_a8f3e1"
obj["forge_kind"] = "terrain_mesh"
```

This lets the realizer query by node ID later (e.g., for incremental updates). 5.0's IDProperty refactor is bleeding-edge — pre-week-1 validates round-trip. Fallback: scene-level metadata dict keyed by object name.

> **Phase 1 verdict:** IDProperty round-trip works against real Blender 5.0.0 — the fallback path is not used in v1. Verified in
> [tests/realize/test_blender_proc.py](../tests/realize/test_blender_proc.py).

### 5.7 The realizer engine

```python
class RealizerEngine:
    def __init__(self, bpy_hg: BpyHypergraph, blender_proc: BlenderProcess):
        self.hg = bpy_hg
        self.blender = blender_proc
        # Verify Blender version matches hypergraph version
        assert self.blender.version_tuple() == self.hg.target_version_tuple()

    def execute_macro(self, macro: str, inputs: dict) -> RealizationResult:
        sequence = self.hg.get_curated_sequence(macro)
        scene_state = self.blender.get_scene_state()
        for step in sequence:
            params = step.bind_params(inputs, scene_state)
            # Prefer bpy.data path if available (5.0 advantage)
            actual_call = step.preferred_path()
            self.hg.check_preconditions(actual_call, scene_state, params)
            result = self.blender.call(actual_call, params)
            self.hg.check_postconditions(actual_call, result, scene_state)
            scene_state = result.new_scene_state
        return RealizationResult(...)
```

The engine in v1 executes curated sequences only. v2 adds the general planner.

## 6. Skills

Skills are SKILL.md files in `forge_mcp/skills/`. The plan skill is now load-bearing — it carries the structured descriptor schema.

### 6.1 forge.plan (load-bearing in v1)

**Triggers:** any user intent involving region creation, descriptor changes, or content generation.

**Skill content includes:**
- The full structured descriptor JSON schema (embedded inline)
- Worked examples mapping free text → structured descriptor (e.g., "rugged alpine valley with stream" → full JSON)
- Step-by-step workflow for the agent
- Tool call patterns
- Common pitfalls (forgetting hydrology, malformed elevation_band, ruggedness out of range)

**Workflow:**
1. Receive user intent in free text.
2. Extract structured descriptor matching the embedded schema.
3. Self-validate against schema; fix and retry if invalid.
4. (Optional) Call `get_descriptor_schema` to double-check schema version.
5. Construct plan: tool sequence, expected change set, cost estimate.
6. Surface plan to user (headline + change summary).
7. Execute on approval.
8. Verify success via `analyze_region`.
9. Report final state.

The schema is embedded directly in the skill so the agent doesn't need to call `get_descriptor_schema` for every interaction — it has the schema in its skill context. The tool exists for self-correction and forward-compatibility.

### 6.2 forge.visualize, forge.audit, forge.cleanup, forge.connect

Largely unchanged from v2.0. Audit subagent's verdict format unchanged.

One refinement: forge.audit now also checks descriptor schema conformance as part of its verdict. If the agent extracted a descriptor that mismatches user intent (e.g., user said "snowy peaks," agent extracted `alpine_valley`), audit can flag this for re-extraction.

## 7. Blender 5.0 realizer

### 7.1 Process model

A long-lived headless Blender 5.0 process is launched as a child of the Forge MCP server. Communication via stdio JSON-RPC.

### 7.2 The Blender 5.0 adapter

Inside the Blender process, a small Python script provides JSON-RPC and dispatches to bpy. In 5.0 this adapter is simpler than it would be in 4.4 because more calls go through `bpy.data` (no context dance):

```python
# Inside Blender 5.0 (started with: blender --background --python adapter.py)
def main():
    setup_jsonrpc_over_stdio()
    while True:
        request = read_request()
        if request.method == "shutdown":
            break
        response = dispatch(request.method, request.params)
        write_response(response)

def dispatch(method: str, params: dict) -> dict:
    # method is fully-qualified bpy path: "bpy.ops.mesh.primitive_plane_add"
    # or "bpy.data.materials.new" or "set_property:object.modifiers[Displace].strength"
    target = resolve_path(method)
    result = invoke(target, params)
    return {
        "ok": True,
        "result": serialize(result),
        "scene_state_diff": compute_scene_state_diff(),
    }
```

Path resolution handles three cases: operator invocation (`bpy.ops.*`), data API call (`bpy.data.*` constructors and methods), property setting (a synthetic `set_property:*` path). All three are deterministic dispatches.

### 7.3 Process lifecycle

- **Startup** — Forge spawns Blender 5.0 on first realization request. Subsequent requests reuse.
- **Reset between regions** — `realize_region` macro starts with `reset_scene` to clear state.
- **Restart on failure** — Forge detects timeout/crash, kills, respawns. State rebuilt from project on demand.
- **Shutdown** — clean shutdown command, SIGKILL after grace period.

## 8. Boundary contract solver

(Unchanged from v2.0 in mechanism.)

### 8.1 Adjacency detection, 8.2 Elevation contract negotiation, 8.3 Constraint application, 8.4 Stream crossings

All as v2.0. See that document for full algorithms.

## 9. Locks and editing semantics

(Unchanged from v2.0.)

Property locks via JSON path. Feature locks capture heightmap patches at lock time, blend back during regeneration. Region wholesale locks skip regeneration entirely. Undo replays history minus last N events.

## 10. Popup canvas page

(Unchanged from v2.0.)

Embedded HTTP server in Forge process. Vanilla TS + Konva.js + d3-force. Two views: Canvas (polygon drawing) and Connection Map (force-directed hypergraph). Two delivery modes: VSCode webview, standalone browser tab. State sync via WebSocket.

## 11. Agent perception channels

(Unchanged from v2.0 in principle.)

Three channels: structured state (primary, cheap), quantitative analysis (verification, cheap), rendered views (verification, expensive). Agent perception cost scales with intent uncertainty, not project size.

## 12. Testing strategy

### 12.1 Unit tests

- Schema validation (every JSON file type, structured descriptor schema, with golden files)
- **Descriptor → spec mapping** (pure Python; deterministic; comprehensive coverage easy)
- Deterministic generators
- Boundary contract negotiation
- Lock application
- bpy hypergraph queries (5.0-specific operator presence, alternative path resolution)

### 12.2 Integration tests

- Full regenerate loop with real Blender 5.0 process (CI: install Blender 5.0, run headless)
- Project save/load round-trip
- MCP tool invocations end-to-end with mock client
- IDProperty round-trip (specifically validates 5.0's refactor doesn't drop our metadata)

### 12.3 Skill tests

- Plan skill with Claude Code: 10 free-text descriptors, verify extraction matches expected structured descriptors
- Other skills tested with representative trigger prompts
- Audit subagent verdict format validated

### 12.4 Acceptance tests

The four success-criteria tests from PRD §8 are formalized as a manual test script. v1 sign-off requires all four pass.

## 13. Observability

Unchanged from v2.0. Structured logs, healthz endpoint, --debug flag. Blender 5.0 version logged at startup; bpy hypergraph version verified to match.

## 14. Open architectural questions

1. **MCP transport choice (stdio vs HTTP).** Recommendation: stdio for v1.
2. **VSCode webview integration.** Recommendation: standalone browser tab first; webview enhancement.
3. **Schema sharing between Python and TS canvas.** Recommendation: generate JSON Schema from Pydantic, consume in TS via generated types.
4. **Heightmap storage format.** Recommendation: numpy `.npy` internal, 16-bit PNG for Blender ingestion.
5. **bpy hypergraph effect curation completeness.** Recommendation: hand-curate v1 set; document gaps; v2 adds empirical derivation.
6. **Audit subagent invocation.** Recommendation: agent client's subagent mechanism (Claude Code has this); fall back to inline isolated-context call.
7. **Blender 5.0.x patch pinning policy.** Recommendation: pin to specific patch (e.g., 5.0.2); regenerate bpy hypergraph on each Blender update; document migration steps.
8. **IDProperty fallback decision.** Recommendation: validate during pre-week-1 spike; if unstable, switch to scene-level metadata dict before week 1 starts.

## 15. Architectural invariants (do not violate without revision)

- The project format is the source of truth. Binary outputs are derived.
- Generation is deterministic given (spec, seed, generator version, contracts, bpy hypergraph version).
- The MCP server is the source of truth. The canvas page and any agent client are presentational.
- Every Blender operation goes through the bpy hypergraph. No ad-hoc bpy calls in macros.
- **Forge contains zero LLM calls.** All semantic interpretation lives in the agent's context.
- The structured descriptor schema is versioned and published via `get_descriptor_schema` and embedded in the plan skill.
- The hypergraph is multilayer from day 1.
- Skills are the agent's API; tools are the implementation layer. The plan skill is load-bearing.
- The agent's perception cost scales with intent uncertainty, not project size.
- Folder layout is git-friendly. Binary outputs gitignored.
- Forge does not depend on third-party MCP servers.
- **Blender version is pinned per build.** Forge refuses to load if running Blender doesn't match the bpy hypergraph's target version.

These invariants are what makes v2 work cheap. Violating them in v1 to save days costs weeks later.

---

## Phase 3 measurements (2026-04-30)

- **RNG pass-name registry** (locked in `forge_mcp/generate/deterministic.py`):
  `noise.base`, `noise.warp`, `erosion.hydraulic`, `erosion.thermal`,
  `stream.path_jitter`. Adding or renaming a name bumps the generator
  contract; CI guards the set.
- **Generator pipeline order** (recorded in
  `SpecRecord.body.generation_metadata.generators_used`):
  `noise.ridged_multifractal`, then declared `post_passes` in order
  (`erosion.hydraulic` / `erosion.thermal`), then declared
  `feature_injectors` in order (`stream.injector`).
- **Spec content addressing**: `spec_id = "spec_" + blake2b(canonical_json(body), digest_size=6).hex()`.
  Identical descriptor + seed + generator versions across regions
  yield identical spec ids — intentional dedup property.
- **Realisations layout**: `realizations/heightmap/<region_id>.{npy,png[,stream.json]}`;
  `realizations/` is gitignored.
- **Eval acceptance artefact**: `docs/eval/phase3/<UTC-timestamp>/contact_sheet.png`
  + `analyses.json` + `manifest.json`. Inputs locked in
  `forge_mcp.eval`; structural ordering rules in
  `tests/descriptor/test_eval_set.py`.
- **Perf gate**: local-only via `make perf`; no CI threshold (NF-1.2
  is runner-sensitive). Phase 4 reopens the budget.
- **Local stubs**: `stubs/scipy/ndimage.pyi` covers the `sobel` and
  `gaussian_filter` surface used by `forge_mcp.analyze`. mypy
  `mypy_path = "stubs"` keeps strict + `disallow_any_explicit` clean
  without a runtime dep on third-party stub packages.

---

## Phase 4 measurements

- **Curated v1 macro library**:
  `forge_mcp/bpy_hypergraph/data/curated_sequences.json` ships the nine
  macros `reset_scene`, `create_terrain_from_heightmap`,
  `apply_terrain_material`, `carve_stream`, `set_camera_overview`,
  `add_basic_lighting`, `render_preview`, `save_blend`, and the
  composite `realize_region`. Each carries a `version` and is hashed
  with BLAKE2b (`digest_size=10`) to a 20-character hex `sequence_id`.
- **Engine semantics** (`forge_mcp/realize/engine.py`): pings the
  adapter on construction and refuses to run on a Blender version
  mismatch (`BlenderVersionMismatchError`); resolves `${name}`
  placeholders whole-value from inputs; recurses into `seq:<other>`
  steps with depth 1; verifies `expects.scene_diff` and
  `expects.png_max_bytes` postconditions; raises `RealizerStepError`
  carrying the partial trace on failure.
- **Macro facade** (`forge_mcp/realize/macros.py`): one frozen+slots
  `*Inputs` dataclass per macro, never imports `bpy`; thin
  `realize_region(engine, inputs)` etc. wrappers that call
  `engine.execute_macro(MACRO_NAME, inputs)` and return the engine's
  `RealizationResult`.
- **Heightmap tessellation** (`forge_mcp/realize/heightmap_mesh.py`):
  row-major vertex order `y * W + x`; quad faces `(tl, tr, br, bl)`;
  rejects grids smaller than 2x2.
- **On-disk realization layout** (added by
  `forge_mcp/project/service.py`):
  `<project>/realizations/blender/<region_id>.{blend,preview.png,trace.json}`,
  with `realizations/blender/` registered in
  `ProjectPaths.all_directories()`.
- **Atomic publish** (`forge_mcp/server/tools/generation.py`): both
  the `.blend` and the preview PNG are written to `<path>.tmp` first
  and `os.replace`d into place only after the realize + render pair
  both succeed.
- **`forge.render_view`** preset resolutions: `preview` 512x384,
  `default` 1024x768, `full` 2048x1536. Default render engine string
  is `BLENDER_EEVEE_NEXT`. The NF-1.5 200 KB ceiling on the preview
  PNG is enforced by the engine via the macro's
  `expects.png_max_bytes` postcondition.
- **Realization trace sidecar** (`forge_mcp/realize/realization.py`):
  pydantic `RealizationTraceRecord` (frozen, `extra="forbid"`)
  carrying region id, view kind, macro name, sequence id, total
  duration, the engine's `final_result`, and per-step
  `TraceStepRecord`s.
- **Realizer-factory injection**: `forge.generate_region` /
  `forge.render_view` look up an installed factory via
  `forge_mcp.server.tools.set_realizer_factory(...)`. With no factory
  installed, the heightmap pipeline still runs and the realization
  fields come back `None`.
- **Local bench**: `scripts/eval/bench_phase4.py` exercises the full
  realize + render path against a real Blender 5.0 binary
  (`$FORGE_BLENDER_BIN`) for every entry in
  `forge_mcp.eval.EVAL_DESCRIPTORS` and writes
  `manifest.json` + per-region `.blend`, `.preview.png`, and a
  `contact_sheet.png` under `docs/eval/phase4/<UTC-timestamp>/`.
- **Adapter isolation**: the `forge_mcp.realize` package never
  imports `bpy`; `scripts/blender/adapter.py` runs inside Blender's
  embedded Python and is type-checked separately against
  `fake-bpy-module-5.0` (the `mypy-blender-scripts` CI step).
