# Phase 5 R-9 sanity walkthrough (manual)

This walkthrough is the **mandatory manual gate** for closing
Phase 5. It exercises the full free-text → terrain → audit loop
through a real Claude Code session and proves that the five shipped
skills actually shape agent behaviour.

It is the companion to the wire-level checklist in
[`docs/p5_verification_walkthrough.md`](p5_verification_walkthrough.md)
and is referenced from
[`AGENT/dev_phases/phase5.md`](../AGENT/dev_phases/phase5.md) Stage G.

If any step below fails to behave as described, **stop** and follow
§7 ("Failure response") — do not silently lower the bar.

---

## 0. Prerequisites

| Requirement                | Why                                                                |
| -------------------------- | ------------------------------------------------------------------ |
| Linux / macOS              | dev target; Windows not supported                                  |
| Python 3.13 + `uv` ≥ 0.9   | enforced by `pyproject.toml`                                       |
| **Blender 5.0.0** binary   | the realizer; pin per Architecture §15                             |
| `FORGE_BLENDER_BIN` env    | absolute path to the Blender 5.0.0 binary                          |
| **Claude Code** CLI        | the v1 reference agent host with native subagent (Task) primitive  |
| `git` working tree         | the project tree the agent writes is meant to be diffable          |

Everything in [`docs/p4_verification_walkthrough.md`](p4_verification_walkthrough.md)
must already pass on the same machine. Phase 5 builds on top of
Phase 4 — the realizer must be functional before the audit loop is
worth exercising.

---

## 1. Install Forge + skills

```bash
git clone https://github.com/mehdiskouri/world_forge.git
cd world_forge
uv sync
uv run pre-commit install
```

Sanity gates (same set as CI):

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q --cov=forge_mcp --cov-branch --cov-fail-under=90
uv run forge-schema-export --check
```

All five must be green. Then install the skills into Claude Code:

```bash
uv run forge-skills install                    # default: ~/.claude/skills/
ls ~/.claude/skills/forge.*                    # → 5 directories
```

`forge-skills install` is atomic per skill; if the destination
already contains an older version, re-run with `--force` to
overwrite.

---

## 2. Register the MCP server with Claude Code

```bash
uv run which forge-mcp
# e.g. /workspace/world_forge/.venv/bin/forge-mcp

claude mcp add world-forge \
  --transport stdio \
  -e "FORGE_BLENDER_BIN=$FORGE_BLENDER_BIN" \
  -- "$(uv run which forge-mcp)"
```

The `claude mcp add` CLI takes the server command as a positional
argument after `--` (not a `--command` flag), and `-e KEY=value` for
environment variables. Use `-s user` if you want the registration
to persist across project directories instead of the default
project-local scope.

In a Claude Code session:

```
/mcp world-forge ping
```

— the server should respond and the five skills should be visible
under "Available skills".

---

## 3. Exercise the loop

> The exact agent prompts below are reference text. Their phrasing
> can vary; what matters is that the agent's tool calls and the
> server's responses match the expected payloads.

### 3.1. New project

User → agent:

> Create a new Forge project at `/tmp/p5_sanity` called "alpine demo".

Expected agent calls:

```text
forge.create_project(path="/tmp/p5_sanity", name="alpine demo")
forge.open_project(path="/tmp/p5_sanity")
```

Expected on disk:

```text
/tmp/p5_sanity/
├── project.json           # name + schema_version + created_at
├── regions/
├── realizations/
└── audits/
```

### 3.2. Draw a region

The popup canvas lands in Phase 6, so for this walkthrough you (or
the agent on your behalf) post a region polygon directly:

User → agent:

> Add a 4 km × 4 km square region called "alpine_valley_01" centered
> at the origin.

Expected agent call:

```text
forge.create_region(
  region_id="rgn_alpine_valley_01",
  polygon=[[-2000,-2000],[2000,-2000],[2000,2000],[-2000,2000]],
  ...
)
```

> **Why 4 km × 4 km, not the original 200 m × 200 m?** The Phase 6
> Stage A region-extent-aware elevation-band clamp resolves the
> archetype's default 1.6 km of relief against the polygon's
> bounding-box extent. A 200 m polygon is plausible only for terrain
> with ≤ ~115 m of relief (mean slope ≤ 30°); the alpine_valley
> archetype wants room for proper macro relief. The clamp would
> otherwise shrink the band on your behalf and you'd see the
> conflict surface in the spec's
> ``generation_metadata.conflicts_resolved``. See
> [`AGENT/follow_ups/phase5-elevation-band-scaling.md`](../AGENT/follow_ups/phase5-elevation-band-scaling.md).

### 3.3. Free text → descriptor → terrain

User → agent:

> Make rgn_alpine_valley_01 a rugged alpine valley with a small
> creek running through it.

Expected agent behaviour, driven by `forge.plan/SKILL.md`:

1. The agent extracts a `Descriptor` matching the
   `alpine_valley_with_creek` row of
   [`forge_mcp/skills/forge.plan/eval_set.json`](../forge_mcp/skills/forge.plan/eval_set.json).
2. It calls `forge.generate_region(region_id=..., descriptor=...)`.
3. The server writes
   `realizations/blender/rgn_alpine_valley_01.{blend,preview.png,trace.json}`
   atomically and returns the preview as MCP image content.
4. The agent renders the preview inline in chat.

Record:

- the extracted descriptor JSON → `docs/eval/phase5/sanity/descriptor.json`
- the preview PNG → `docs/eval/phase5/sanity/preview.png`
  (must be < 100 KB at 512×384; resize before committing)
- the agent's response transcript → `docs/eval/phase5/sanity/transcript.md`

### 3.4. Audit the region

User → agent:

> Audit this region.

Expected agent behaviour, driven by `forge.audit/SKILL.md`:

1. The agent spawns a Claude Code Task subagent with an isolated
   context (no inherited project chat history).
2. The subagent calls only **read-only** Forge tools
   (`forge.get_region`, `forge.get_audit_schema`,
   `forge.render_view`, `forge.list_audits`, `forge.get_audit`).
3. It scores the four `AuditDimension`s (descriptor_coherence,
   geometric_validity, render_quality, spec_alignment) on the
   `pass / warn / fail` scale.
4. It posts the verdict via `forge.record_audit(...)`.
5. The server validates against `AuditVerdict`, computes the
   deterministic `audit_id`, writes
   `audits/rgn_alpine_valley_01/<audit_id>.json` atomically, and
   appends a `HistoryEventKind.AUDIT_RECORDED` event to history.

Record the persisted verdict → `docs/eval/phase5/sanity/audit.json`.

### 3.5. Inspect the on-disk tree

```bash
tree /tmp/p5_sanity
# project.json
# regions/rgn_alpine_valley_01/region.json
# realizations/blender/rgn_alpine_valley_01.{blend,preview.png,trace.json}
# audits/_index.json
# audits/rgn_alpine_valley_01/audit_<hex>.json
```

`audits/_index.json` summarises the verdict; the per-region file
contains the full schema-validated body. Both diff cleanly under git.

---

## 4. Plan-skill eval (recorded run)

Using the same Claude Code session:

1. Paste each of the 12 free-text descriptors from
   [`forge_mcp/skills/forge.plan/eval_set.json`](../forge_mcp/skills/forge.plan/eval_set.json)
   in turn (one per turn; clean context per descriptor is preferred).
2. Save each extracted descriptor JSON keyed by the free-text string
   into `docs/eval/phase5/sanity/extractions.json` in the shape
   `{"extractions": {free_text: descriptor}}`.
3. Run the deterministic harness:

```bash
uv run python scripts/eval/skill_plan_eval.py \
    --extractions docs/eval/phase5/sanity/extractions.json \
    --out docs/eval/phase5/<UTC-timestamp>/
```

Pass threshold: `exact_match_count >= 8`. The harness exits `0` on
pass, `1` on fail. The run report (`report.md`) and the structured
diff (`diffs.json`) are committed under
`docs/eval/phase5/<UTC-timestamp>/`.

---

## 5. Acceptance checklist

Tick all of these before opening the close-out PR:

- [ ] `uv run pytest -q --cov=forge_mcp --cov-branch --cov-fail-under=90` exits 0
- [ ] `uv run forge-schema-export --check` exits 0
- [ ] `uv run forge-skills install` writes 5 folders into `~/.claude/skills/`
- [ ] Claude Code session creates a project, generates a region from
      free text, and renders the preview inline
- [ ] `audits/<region_id>/audit_<hex>.json` exists after the audit step
- [ ] `_index.json` lists the verdict
- [ ] Plan-skill eval scores ≥ 8 / 10 against the canonical fixture
- [ ] Sanity transcript + descriptor + preview + audit committed under
      [`docs/eval/phase5/sanity/`](eval/phase5/sanity/)

---

## 6. Tear down

```bash
rm -rf /tmp/p5_sanity
claude mcp remove world-forge
```

The sanity transcript and harness output remain in git history; the
working project tree is disposable.

---

## 7. Failure response

If Claude Code does not reliably extract the descriptor:

1. Iterate **only** the plan skill — add or sharpen worked examples
   in `forge.plan/SKILL.md`, tighten the pitfall guidance, but do
   **not** widen the descriptor schema or relax the eval threshold.
2. Re-run §3.3 + §4. The threshold is fixed at 8 / 10 by PRD R-2.
3. After two iterations without success, surface the failure as a
   phase blocker in [`AGENT/dev_phases/phase5.md`](../AGENT/dev_phases/phase5.md)
   §7 ("Failure response") — escalate, do not silently lower the bar.

If the audit subagent does not run (Cursor / Copilot lacking a
Task primitive): use the inline isolated-context fallback documented
in [`forge_mcp/skills/forge.audit/SKILL.md`](../forge_mcp/skills/forge.audit/SKILL.md)
§3, and record the `subagent_context.transport` as
`"isolated_inline"` in the verdict.
