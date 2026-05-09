---
name: "forge.cleanup"
version: "0.3.0"
description: "Find orphaned specs, stale realization artefacts, and lock conflicts; optionally purge orphan specs."
triggers: ["clean up", "remove orphans", "find stale realizations", "audit project size", "what's safe to delete", "lock conflict"]
requires_tools: ["forge.find_orphans", "forge.find_stale_realizations", "forge.find_lock_conflicts", "forge.purge_orphans", "forge.list_locks"]
requires_subagent: false
---

# forge.cleanup

Use this skill to **find** disposable artefacts and lock conflicts in
a Forge project, and (with explicit user consent) purge orphan spec
files. Three of the four tools are read-only; only `forge.purge_orphans`
mutates state, and it defaults to `dry_run=True`.

## When to invoke

Invoke when the user asks to:

* clean up a project / find orphans / free disk space,
* explain why a specific spec/render/blend file is on disk,
* diagnose a lock conflict ("why does this lock not apply?").

Do **not** invoke when:

* the user wants to delete a *region* — that's `forge.delete_region`
  driven directly, not through this skill,
* the user wants a quality verdict — use `forge.audit`,
* the user is asking how regions connect — use `forge.connect`.

## Tool inventory

| Tool | Purpose | Mutates? |
|---|---|---|
| `forge.find_orphans` | Specs not referenced by any region; material applications with missing archetype endpoints; environment bindings to deleted environments. | No |
| `forge.find_stale_realizations` | `.blend` files whose source spec JSON is newer (re-compiled but not re-rendered), or whose region/spec was deleted. | No |
| `forge.find_lock_conflicts` | Locks whose target region was deleted, plus property locks whose `expected_value` no longer matches the live region JSON. | No |
| `forge.purge_orphans` | Deletes the orphan spec files reported by `forge.find_orphans`. Defaults to `dry_run=True`; pass `dry_run=False` to actually delete. | Yes (only when `dry_run=False`) |
| `forge.list_locks` | Background context (filter the conflict list by region). | No |

## Worked patterns

### Pattern: "find and confirm orphan specs"

```
1. forge.find_orphans()
   → result.specs is a list of {spec_id, path}
2. show the user the list and ask whether to delete
3. if the user confirms, forge.purge_orphans(dry_run=False)
   → result.removed lists each path that was unlinked
```

### Pattern: "why didn't my latest render appear?"

```
forge.find_stale_realizations()
  → entries with reason="spec_newer_than_blend" mean the user
    re-ran compile but never re-ran forge.generate_region
  → entries with reason="region_deleted" mean a leftover .blend
    that should be removed manually
```

### Pattern: "why isn't my lock holding?"

```
forge.find_lock_conflicts()
  → reason="target_missing" → the locked region was deleted; tell
    the user to forge.unlock(lock_id) to clear the dangling record
  → reason="expected_value_drift" → the locked field already
    changed; either forge.unlock or re-lock at the new value
```

## Mutator safety

`forge.purge_orphans` is the only tool in this skill that writes. It
defaults to `dry_run=True`; the response shape is:

```
{"dry_run": true, "would_remove": [...], "removed": []}
```

To actually delete, you must pass `dry_run=False` explicitly. Always
show the `would_remove` list to the user and get a yes before flipping
the flag.

## Common pitfalls

* **Confusing "stale" with "deletable"**: `find_stale_realizations`
  flags files that are *out of sync* with their spec, not files that
  the user wants gone. Recommend a re-render via
  `forge.generate_region`, not deletion, unless the user explicitly
  asks to drop the artefact.
* **Touching `history/`**: never recommend deletion under
  `history/` — those files back `forge.undo` and rotating them is a
  project-format change, not a cleanup.
* **Releasing locks by editing `locks/locks.json` while a project is
  open**: the in-memory state will diverge. Use `forge.unlock` instead.

## Failure recovery

| Error code | Meaning | Recovery |
|---|---|---|
| `no_open_project` | No project is loaded. | Tell the user to open a project; this skill can't operate without one. |
