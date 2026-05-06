---
name: "forge.connect"
version: "0.2.0"
description: "Traverse the bpy hypergraph: list boundaries, query containment / adjacency / hydrology layers, inspect specific edges. Read-only; no canvas in v1."
triggers: ["how are these regions connected", "show the hypergraph", "list boundaries", "what borders alpine-bowl", "is x adjacent to y", "what's the watershed", "BFS the layer"]
requires_tools: ["forge.query_layer", "forge.list_boundaries", "forge.inspect_boundary", "forge.list_regions", "forge.get_region"]
requires_subagent: false
---

# forge.connect

Use this skill to answer **structural** questions about a Forge
project: which regions touch which, what shares a watershed, how many
boundaries the project has, etc. The skill is a **read-only**
traversal of the bpy hypergraph; it never mutates anything.

## When to invoke

Invoke when the user asks:

* "how are X and Y connected?",
* "what borders <region>?",
* "list boundaries / show the hypergraph",
* "is the river continuous between X and Y?",
* "everything contained in <region>".

Do **not** invoke when:

* the user wants a picture — that's `forge.visualize`,
* the user wants to *change* connectivity — region CRUD goes through
  `forge.create_region` / `forge.update_region` directly.

## Tool inventory

| Tool | Purpose |
|---|---|
| `forge.list_regions` | Enumerate every region (for naming). |
| `forge.get_region` | One region's record (polygon, scale, descriptor). |
| `forge.list_boundaries` | Every persisted `BoundaryStub` in the project. |
| `forge.inspect_boundary` | One boundary's full record (vertices, kind, the two regions it joins). |
| `forge.query_layer` | BFS over one named hypergraph layer. |

## The three v1 layers

`registered_layers` (set in `ProjectMetadata`) is exactly:

| Layer | Edges | Use for |
|---|---|---|
| `"spatial_containment"` | World-root → every region (DAG). | "Everything in this project / contained in this region." |
| `"spatial_adjacency"` | Region ↔ region where polygons share a boundary. | "What borders X? Is X adjacent to Y?" |
| `"hydrology"` | Stream-edge upstream/downstream pairs. | Watershed, flow direction. Only populated for regions whose descriptor has hydrology. |

There is no v1 canvas / interactive graph — this skill is text-only.
Visualising the hypergraph is a Phase-6 concern; do not promise it.

## Decision tree

1. **"What borders X?"** → `forge.query_layer(layer="spatial_adjacency", root_node=X, depth=1)`.
2. **"What is contained in X?"** → `forge.query_layer(layer="spatial_containment", root_node=X)`.
3. **"List every boundary"** → `forge.list_boundaries()`. For one
   specific boundary, follow with `forge.inspect_boundary(boundary_id)`.
4. **"Watershed / flow direction"** → `forge.query_layer(layer="hydrology", root_node=X)`.
5. **"Everything in the project"** → `forge.query_layer(layer="spatial_containment")` (no root → starts at world).

## Worked patterns

### Pattern: "what borders alpine-bowl?"

```
forge.query_layer(layer="spatial_adjacency",
                  root_node="alpine-bowl",
                  depth=1)
  → returns ["bog-1", "ridge-2", ...]
```

Surface them as a list. If the user wants the actual shared edge,
follow with `forge.list_boundaries()` and filter for the pair.

### Pattern: "are alpine-bowl and bog-1 adjacent?"

```
neighbors = forge.query_layer(
    layer="spatial_adjacency",
    root_node="alpine-bowl",
    depth=1,
)
→ check whether "bog-1" is in the result
```

If yes, optionally inspect the shared boundary with
`forge.list_boundaries()` then `forge.inspect_boundary(<id>)`.

### Pattern: "show me the watershed downstream of headwaters"

```
forge.query_layer(layer="hydrology",
                  root_node="headwaters")
  → ordered region ids in flow order
```

If the result is empty, the region's descriptor has no
`hydrology.streams` entry; tell the user explicitly rather than
silently returning an empty list.

### Pattern: "list every boundary"

```
forge.list_boundaries()
  → boundaries: [{boundary_id, region_a, region_b, ...}, ...]
```

For a specific edge, fetch with `forge.inspect_boundary(boundary_id)`
to surface vertex coordinates and kind.

### Pattern: "everything in the project"

```
forge.query_layer(layer="spatial_containment")
  → starts from the world root and yields every contained node
```

## Common pitfalls

* **Confusing "adjacency" with "boundary"**: adjacency is the *edge*
  in the hypergraph layer; the boundary is the **persisted geometric
  record** of that adjacency. Use `query_layer` for connectivity
  questions, `list_boundaries` / `inspect_boundary` for geometry.
* **Asking for `depth=0`**: `depth=0` returns just the root, which is
  rarely what the user wants. Use `depth=1` for "direct neighbours".
* **Calling `query_layer` with an unknown layer name**: returns the
  structured `unknown_layer` error. Stick to the three v1 names; do
  not invent layer names like `"adjacency"` or `"contains"`.
* **Treating `query_layer` results as ordered for adjacency**:
  adjacency is an undirected layer; the BFS order has no semantic
  meaning. Hydrology *is* directed (upstream → downstream), so the
  order *does* matter there.

## Failure recovery

| Error code | Meaning | Recovery |
|---|---|---|
| `no_open_project` | No project is loaded. | Tell the user to open a project. |
| `unknown_layer` | The layer name is not registered. | List the v1 layers (`spatial_containment`, `spatial_adjacency`, `hydrology`) and ask the user to pick. |
| `unknown_boundary` | `boundary_id` is not in the project. | Run `forge.list_boundaries` and surface the available ids. |
| `unknown_region` | `root_node` is not a known region. | Run `forge.list_regions` and ask the user to pick. |
