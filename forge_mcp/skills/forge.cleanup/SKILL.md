---
name: "forge.cleanup"
version: "0.2.0"
description: "Find orphaned specs, stale realization artefacts, and conflicting locks. Reports what to remove; does not delete by itself in v1."
triggers: ["clean up", "remove orphans", "find stale realizations", "audit project size", "what's safe to delete", "lock conflict"]
requires_tools: ["forge.list_regions", "forge.get_region", "forge.inspect_spec", "forge.list_locks"]
requires_subagent: false
---

# forge.cleanup

Use this skill to **find** disposable artefacts and lock conflicts in a
Forge project. v1 of this skill is **read-only**: it surfaces the list
of suspect paths and a brief recovery script, but it never removes
anything itself. The user (or a follow-up tool turn the user
authorises) does the actual deletion.

## When to invoke

Invoke when the user asks to:

* clean up a project / find orphans / free disk space,
* explain why a specific spec/render/blend file is on disk,
* diagnose a lock conflict ("why can't I edit this region?").

Do **not** invoke when:

* the user wants to delete a *region* — that's `forge.delete_region`
  driven directly, not through this skill,
* the user wants a quality verdict — use `forge.audit`,
* the user is asking how regions connect — use `forge.connect`.

## Tool inventory

| Tool | Purpose |
|---|---|
| `forge.list_regions` | Enumerate every region (spec_id may be `None` if not generated). |
| `forge.get_region` | Per-region detail — used to confirm a spec is referenced. |
| `forge.inspect_spec` | Resolve a `SpecRecord` to confirm it exists on disk. |
| `forge.list_locks` | List active locks; optionally filter by `region_id`. |

This skill does **not** call any mutation tool. There is no v1
`forge.delete_spec` / `forge.purge_realization` — that is intentional
(see [phase5.md](../../../AGENT/dev_phases/phase5.md) "Confirmed
decisions"). Recommend manual deletion to the user with a clear
prompt.

## What counts as cleanup-worthy

### 1. Orphan specs

A spec file under `specs/` that no region's `spec_id` points to.
Possible causes: a `forge.reroll_seed` that produced a new spec while
leaving the old one on disk, or a deleted region that left its spec
behind.

**Detection**:

```
regions = forge.list_regions()
referenced_spec_ids = {r.spec_id for r in regions if r.spec_id}
on_disk_spec_ids = ... (read specs/ directory contents from project_root)
orphans = on_disk_spec_ids - referenced_spec_ids
```

The skill cannot list `specs/` itself (no MCP tool exposes that). Ask
the user for the project root or instruct them to run
`ls <project>/specs/` and paste the result.

### 2. Stale realizations

Files under `realizations/heightmap/` or `realizations/blender/` whose
filename stem is a `region_id` that no longer exists in the project.
Detect by listing `forge.list_regions` and asking the user to
intersect against their disk.

### 3. Lock conflicts

`forge.list_locks(region_id=...)` returning more than one record on
the same scope, or a lock whose `holder` is `"agent"` but whose
`acquired_at` is well in the past (suggesting an orphaned lock from a
crashed agent run).

In v1, locks are listed but not released by an MCP tool. Tell the
user the on-disk path (`locks/locks.json`) and let them decide.

## Worked patterns

### Pattern: "find orphan specs"

```
1. forge.list_regions()
   → collect every non-null spec_id
2. ask user: "What's under <project>/specs/ ?"
3. diff the two sets; report the orphans
4. for each orphan, recommend:
   "rm <project>/specs/<spec_id>.json"
```

### Pattern: "why is this region locked?"

```
forge.list_locks(region_id="alpine-bowl")
  → if empty, tell the user there is no lock
  → if one, surface holder + acquired_at + scope
  → if more than one, name the conflict and tell the user to inspect
    locks/locks.json directly
```

### Pattern: "find stale renders"

```
1. forge.list_regions()
   → collect every region_id
2. ask user: "What's under <project>/realizations/blender/ ?"
3. report any filename stem that is not in the region set
4. recommend "rm <project>/realizations/blender/<stem>.*"
```

## Common pitfalls

* **Recommending deletion of a region's spec**: a region's `spec_id`
  is part of its identity — deleting that file breaks
  `forge.inspect_spec`, `forge.analyze_region`, and any audit. Always
  confirm the spec is *not* referenced before recommending removal.
* **Deleting `realizations/heightmap/<rid>.npy` without the .png**:
  the PNG is just a preview, but the `.npy` is the source of truth
  for `forge.analyze_region`. Recommend deleting the pair together,
  or neither.
* **Touching `history/`**: never recommend deletion under
  `history/` — those files back `forge.undo` (Phase 7) and rotating
  them out is a project-format change, not a cleanup.
* **Releasing locks by editing `locks/locks.json` while a project is
  open**: the in-memory state will diverge. Recommend the user close
  the project first, then edit, then reopen.

## Failure recovery

| Error code | Meaning | Recovery |
|---|---|---|
| `no_open_project` | No project is loaded. | Tell the user to open a project; this skill can't operate without one. |
| `unknown_region` | The user named a region that no longer exists. | That itself may be a cleanup signal — ask the user whether to recommend purging the corresponding realization files. |
| `unknown_spec` | A spec id the user supplied is not on disk. | The spec is already gone; there is nothing to clean. |
