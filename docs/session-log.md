# Session log

Living hand-off for the virotherapy Blender project. Update the **Latest state**
block at the end of each working session; history below is append-only.

**Verify before you trust this file.** A session that ends without running the
suite leaves the "Status" line stale. The one command that settles it:

```sh
cd ~/Work/science-fair-virotherapy && blender --background --python blender/scripts/asset_blood_vessel.py
```

Passes when the final line is `[vessel] VALIDATION PASSED`.

---

## Architecture decisions that are settled — do not relitigate

### One `.blend` for the whole scene

There is **one** scene file, `blender/scenes/virotherapy_main.blend`. The blood
vessel and the cells live in the same file because that is the design, not an
oversight. The separation is one layer down, at the scripts.

| Layer | Granularity |
|---|---|
| `.blend` | one, whole scene |
| `blender/scripts/asset_*.py` | one per asset — the real unit of work |
| collection | one per domain — the real unit of blast radius |

Every `asset_*.py` hard-codes the single collection it targets, so rebuilding
`05_BLOOD_VESSELS` cannot damage `01_TUMOR` or `04_IMMUNE_SYSTEM`. That is where
the isolation actually comes from, and it is a code guarantee rather than a
filename convention.

### Why there is exactly one `.blend`

- **It is regenerable.** The scene is fully rebuildable from
  `blender/scripts/scene_tumor.py`. Nothing lives only in the `.blend`.
- **Git deliberately excludes it.** `.gitignore` says `*.blend` with the reason
  spelled out in the file: *"its merges are not resolvable by hand."* A
  binary that merges by hand is worse than no binary.
- **glTF export wants one file.** The web viewer is the destination. Splitting the
  scene means stitching per-asset exports and reconciling transforms by hand.
- **The whole asset is one unit.** Wall layers, lumen contents and the validator
  share shell-radius arithmetic. A second module would have to import the first
  one's private helpers to get at `_tube_mesh`, `_parallel_frames` and
  `_distance_to_path`.

`virotherapy_main.blend` is a **build artifact**. Treat it as disposable: if it
is lost, rebuild it. Do not hand-edit it and do not hand-merge it.

The second file on disk, `mcp-smoke-test.blend`, is throwaway — 2 objects, 1
material, EEVEE. It exists to prove the MCP bridge connects. Ignore it.

### The only `.blend` in the repo is untracked

`git status` will never show the scene, and should not. If a session ends with
the scene looking untracked, that is correct, not a problem to fix.

---

## Status

**Branch** `vessel-internals` · HEAD `8b06783` "Animate idle cell motion and
guard the tumour speed constraint" · plan
`docs/superpowers/plans/2026-09-28-cellular-idle-animation.md` (the vessel
plan's 8 tasks are still the longer-range track)

**Suite fully green, re-verified 2026-09-29** — all six asset/animation scripts
plus the scene selftest:

| Script | Marker |
|---|---|
| `asset_healthy_cell.py` | `[healthy_cell] VALIDATION PASSED` |
| `asset_tumor_cell.py` | `[tumor_cell] VALIDATION PASSED` |
| `asset_immune_cells.py` | `[immune] VALIDATION PASSED` |
| `asset_ecm.py` | `[ecm] VALIDATION PASSED` |
| `animation_idle.py` | `[idle] VALIDATION PASSED` |
| `asset_blood_vessel.py` | `[vessel] VALIDATION PASSED` |
| `scene_tumor.py -- --selftest` | `{'objects': 1525, 'collections': 11, 'duplicated': [], 'ok': True}` |

Sweep command (one line, ~3 min total):

```sh
cd ~/Work/science-fair-virotherapy && for s in asset_healthy_cell asset_tumor_cell \
  asset_immune_cells asset_ecm animation_idle asset_blood_vessel; do
  blender --background --python blender/scripts/$s.py 2>&1 | grep -E "VALIDATION PASSED|Traceback"; done
```

### Uncommitted work — 4 files, +624/−82

`animation_idle.py` (+441) is the bulk: nucleus gets its own period
(`_nucleus_period`, `NUCLEUS_RATE_RATIO`), action ownership tagging
(`ACT_Idle_` prefix + `_is_idle`) so the validator stops policing foreign
curves, nucleus-binding fences that **fail loudly** instead of guessing
(`_layered_parts` now returns a `problem`, `KNOWN_LAYER_COUNTS = (3, 4)`,
`NUCLEUS_SIZE_RATIO = (0.25, 0.85)`), and a `motion_scale` default that was
binding at def-time rather than call-time — now `None` + resolved inside.

`asset_blood_vessel.py` (+208): RBC membrane relief. `_disc_noise`, a `factor`
kwarg on `_disc_mesh`, `RBC_MEMBRANE_NOISE = 0.035`, `RBC_SPIN_STEPS` 20→32,
and `_selftest_rbc_membrane()` wired into `main()`.

`asset_ecm.py` (+30): `set_ecm_visible(visible)` + its validation.
`scene_tumor.py` (+27): `build_ecm()` and the **first ECM wiring into the scene**.

**Next up, in order:**
1. **Plan Task 4 Step 5 is missing.** `scene_tumor.build()` imports `asset_ecm`
   but never imports or calls `animation_idle`. A rebuilt scene is static —
   the exact failure the idle feature exists to prevent. Needs the call, plus
   the `"idle_cells"` return key.
2. **Plan Task 4 Step 7: `blender/README.md` is stale.** `animation_idle.py` is
   missing from the script tree, and line 437 still claims "The six
   `animation_*.py` scripts are contracts only."
3. Vessel plan Tasks 3–8 still not started — `_clot_mesh` and `_fibrin_mesh`
   do not exist.
4. Two vacuous asserts to delete: `asset_ecm.py:652` compares
   `set_ecm_visible`'s own return value against itself;
   `asset_blood_vessel.py:647` `assert max(moved) == 1` is implied by the
   `any(moved)` on the line above.

### ECM — decided, and why it matters for Task 7

`asset_ecm.density_at(point, seed) -> float` is the **public hook** the
viral-spread animation will sample to slow a virion in dense stroma. It is a
pure function of position and seed, so the builder and the animation cannot
disagree about where the matrix is thick. The spread animation is deliberately
unwritten — but that function is the whole reason the density field is a
function and not a number baked into placement.

The network is **visually** connected, not topologically. Instancing forbids
shared junctions; fibres cross instead. Not to be "fixed" later.

---

## Latest state — 2026-09-29

Reconnected to the session. Blender was **not running** — only the MCP server
process was, so the bridge refused. Launched detached, MCP addon autostarts so
no flag is needed:

```sh
cd ~/Work/science-fair-virotherapy && setsid nohup blender \
  ~/Work/science-fair-virotherapy/blender/scenes/virotherapy_main.blend \
  >/tmp/opencode/blender-launch.log 2>&1 </dev/null &
```

Bridge live on `127.0.0.1:9876`; `virotherapy_main.blend` clean, not dirty.

**Scene as loaded:** 2809 meshes, 125 mesh datablocks, 30 materials, 0 actions,
Cycles, frames 1–240. Real content in `06_EXTRACELLULAR_MATRIX` (1513 fibre
instances off **one** shared mesh, 1513 users). The `TEST_*` collections hold the
cell/vessel/virion scratch (560 HSV1, 564 tumour, 158 immune, 30 healthy,
21 vessel) — the viewport is mostly that, not the scene build.

Recovered the work from `git log` + `git diff`, not from this file: the log had
not been updated past the ECM commit, and the real state was four files ahead.
Full re-verified status is in the **Status** block above.

**PENDING**
- Wire `animation_idle` into `scene_tumor.build()` (Task 4 Step 5) — the gap
  that matters most.
- Update `blender/README.md` (Task 4 Step 7).
- Delete the two vacuous asserts.
- Commit the 4 uncommitted files.
- Vessel plan Tasks 3–8.
- `set_ecm_visible` and `_rbc_mesh(seed=...)` have no production caller.

---

## Latest state — 2026-09-28

Recovered the session by file mtime, not by memory: Memorix had no stored
context for this project, which is why this log exists.

- Opened `virotherapy_main.blend` in the interactive Blender and confirmed the
  MCP bridge is live on `127.0.0.1:9876`. Launched detached:
  ```sh
  setsid nohup blender ~/Work/science-fair-virotherapy/blender/scenes/virotherapy_main.blend >/tmp/opencode/blender-launch.log 2>&1 </dev/null &
  ```
  The MCP addon autostarts its server, so no extra flag is needed.
- Ran the vessel suite: `[vessel] VALIDATION PASSED`.
- Confirmed from the datablock summary that `10_DEBUG/TEST_HSV1_Virions` is the
  only populated collection, so what is rendered in the viewport is virion test
  scratch, not the real scene contents.
- Established the one-`.blend` architecture decision above, from `.gitignore`,
  `README.md:429` and the plan's File Structure section.

**PENDING**
- Commit the ECM asset and the Task 2 remainder.
- Task 3 onward.
- All six `animation_*.py` remain contracts only.
- ECM ground substance and fibronectin/laminin are absent.

### Later — ECM collagen network
Implemented `create_ecm_region(bounds, density, seed)` on the existing
`06_EXTRACELLULAR_MATRIX` collection. Rejection sampling against a two-octave
Perlin field gives nonuniform density that falls out of the field rather than
being hand-placed; 42% of fibres are seeded beside an existing fibre, which is
the whole of the "interconnected" requirement. One shared mesh, ~1500 fibres at
full density in the default region.

Three things the render check caught that the validator could not:
- Drawn radius had to drop from 0.18 to 0.05 µm. At the ~14 µm camera standoff
  the project uses, 0.18 drew a 60-pixel rope across a 10 µm cell.
- The first field-correlation check was a *mean* test and could not fail
  usefully — the field spans 0.26-0.67, so no algorithm moves the mean far.
  Replaced with a coefficient-of-variation test against what an even scatter
  would give.
- `matrix_world` is depsgraph-evaluated and still identity under
  `--background`, so the orientation check was reading every fibre as pointing
  along world +X. Read the quaternion instead.

---

## Session history

### 2026-09-28 — vessel internals, tasks 1-2
Wall layer arithmetic and the new shells committed; smooth muscle, basement
membrane and adventitia added to the wall. Media threshold wording corrected in
the plan. Wall geometry self-test moved to the suite entry point.
