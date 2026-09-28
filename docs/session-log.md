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

**Branch** `vessel-internals` · base `5e5bd0b` · plan
`docs/superpowers/plans/2026-09-28-vessel-internals.md` (8 tasks) · spec
`docs/superpowers/specs/2026-09-28-vessel-internals-design.md`

Suite **passing** as of 2026-09-28: all six implemented assets green.
Vessel: 3 vessels, 14 shells, 70 components, 4 shared source meshes.
ECM: 756 fibres, 1 shared mesh, `MAT_ECM_Collagen` only.

| Task | Subject | State |
|---|---|---|
| — | `asset_ecm.py` collagen network | **done, uncommitted** |
| 1 | Wall layer arithmetic and the new shells | done — `c716bb8`, `f84a910` |
| 2 | Materials for the new layers | done, **uncommitted** |
| 3 | Fibrin and the thrombus meshes | not started |
| 4 | Wall-bound cells — pericyte, endothelial | not started |
| 5 | Class split and wall-bound placement | not started |
| 6 | Gaps, the clot, containment that can see them | not started |
| 7 | Margination and extravasation | not started |
| 8 | Documentation and the render check | not started |

**Uncommitted work:**
- `blender/scripts/asset_blood_vessel.py` (+151/−6): Task 2 material specs
  `MAT_Vessel_SmoothMuscle` / `BasementMembrane` / `Pericyte`, plus validator
  plumbing `MEASURED_RADIUS_TOLERANCE` and `PROP_REQUESTED_RADIUS`. Suite passes.
- `blender/scripts/asset_ecm.py` (new), plus `blender/README.md`.

**Next up:** Task 3 — `_clot_mesh`, `_fibrin_mesh`, and their two `create_*`
builders. Neither function exists yet.

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
