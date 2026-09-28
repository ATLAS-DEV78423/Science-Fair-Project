# Cellular Idle Animation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ambient idle motion on all three cellular asset classes — healthy cells breathe faintly, tumour cells undulate irregularly, immune cells drift — with a hard constraint that tumour cells never read as faster than healthy ones.

**Architecture:** One new module `blender/scripts/animation_idle.py` owns every keyframe it writes and imports none of the six narrative beat modules. It drives the `MOD_MembraneDeform` Displace strength that healthy and tumour cells already ship; Task 1 adds the same hook to immune cells so all three are uniform. Pause and reduced-motion are one `motion_scale` multiplier applied to every amplitude at key time.

**Tech Stack:** Blender 5.2 Python API (`bpy`), `mathutils`, project-local `utilities.py`. Validation runs headless via `blender --background --python <script>`. No test framework — each asset module carries its own `main()` + `validate_*()` + pass-marker convention.

**Spec:** `docs/superpowers/specs/2026-09-28-cellular-idle-animation-design.md`

## Global Constraints

- **1 Blender unit == 1 micrometre.** `unit_settings.scale_length` is 1e-6. Every amplitude is in micrometres.
- **The name is the identity key.** `ut.get_or_create_object` fetches by name before creating. Cell idle motion is a pure function of the cell's ordinal, parsed from its name.
- **Animation lives in the animation module.** `asset_immune_cells.py` gains only a deformation *hook* (a modifier, zero keyframes) in Task 1, never animation.
- **Rebuilds are idempotent.** Call `ut.clear_animation(target)` before inserting keys, always. The project has been bitten by stacked F-curves once.
- **No glowing, pulsing, or emission animation.** Ruled out by the brief and wrong for the microscopy look.
- **Verification command shape** for every task:
  ```sh
  cd ~/Work/science-fair-virotherapy && blender --background --python blender/scripts/<module>.py
  ```
  Expected output ends in the module's pass marker. Failure prints `[<tag>] FAIL: <reason>` and exits non-zero.

## Review Focus

Five failure modes the spec implies that naive tests miss. Each line gets a test in the owning task.

1. **A cell whose period does not divide 240** arrives at the loop wrap in a different pose — the loop jumps. *Tumor Task 2, Step 2.*
2. **Tumour peak speed exceeding healthy's** — the exact false lesson the spec exists to prevent. Must be *measured per channel*, not compared as constants, so a regression through any channel is caught. *Tumor Task 2, Step 3.*
3. **`motion_scale=0.0` deleting keys instead of writing flat ones** — then re-enabling desynchronises from the beat layer sharing frames 1–240. *Tumor Task 3, Step 2.*
4. **Rebuild stacking F-curves** — running `animate_idle()` twice leaves both key sets. *Tumor Task 4, Step 2.*
5. **Animating adding geometry** — the layer must add keys, not objects. A cell count change means the feature leaked into geometry. *Tumor Task 4, Step 3.*

---

### Task 1: Add the membrane deformation hook to immune cells

The other two cell classes already ship a Displace modifier; immune cells do not, so the idle layer would have nothing to drive. This task adds only the hook. No keyframes.

**Files:**
- Modify: `blender/scripts/asset_immune_cells.py`
- Test: same file's `validate_immune_cells()`

**Interfaces:**
- Consumes: `ut.get_or_create_object`, `ut.set_parent` (existing)
- Produces: `TEX_DISPLACE_NAME: str`, `MEMBRANE_DEFORM_STRENGTH: float`, `_add_membrane_deformation(obj, root) -> None` in `asset_immune_cells.py`. `_create_immune_cell` attaches the modifier, so every immune membrane has `modifiers["MOD_MembraneDeform"]` with `texture_coords_object == root`.

- [ ] **Step 1: Add the failing check to `validate_immune_cells`**

Inside the existing per-root loop, after the required-parts check, mirror the block at `asset_healthy_cell.py:620-625`:

```python
        membrane = parts.get(SUFFIX_MEMBRANE)
        if membrane is not None:
            mod = membrane.modifiers.get("MOD_MembraneDeform")
            if mod is None:
                failures.append("{}: membrane deformation modifier missing".format(
                    membrane.name))
            elif mod.texture_coords_object != root:
                failures.append("{}: deformation not bound to its own root".format(
                    membrane.name))
```

- [ ] **Step 2: Run to verify it fails**

Run: `blender --background --python blender/scripts/asset_immune_cells.py`
Expected: `[immune] FAIL: IMMUNE_TCELL_001_MEMBRANE: membrane deformation modifier missing` and a non-zero exit. Every one of the four classes fails this.

- [ ] **Step 3: Add the constants and the hook function**

Near the other contract constants in `asset_immune_cells.py`:

```python
TEX_DISPLACE_NAME = "TEX_ImmuneCellDisplace"
MEMBRANE_DEFORM_STRENGTH = 0.32
```

and the function, matching the two existing implementations in `asset_healthy_cell.py:273` and `asset_tumor_cell.py:415` — `CLOUDS` texture at `noise_scale = 0.35`, `noise_depth = 2`, modifier `direction = "NORMAL"`, `mid_level = 0.5`, `texture_coords = "OBJECT"`, `texture_coords_object = root`. Use `obj.modifiers.get(...)` / `obj.modifiers.new(...)` so a rebuild reuses the existing modifier rather than stacking.

- [ ] **Step 4: Call it from `_create_immune_cell`**

Immediately after `ut.assign_material(membrane, materials["membrane"])` in `_create_immune_cell`, add `_add_membrane_deformation(membrane, root)`.

- [ ] **Step 5: Run to verify it passes**

Run: `blender --background --python blender/scripts/asset_immune_cells.py`
Expected: `[immune] VALIDATION PASSED`, exit 0.

- [ ] **Step 6: Confirm the other two cell modules are unregressed**

Run: `blender --background --python blender/scripts/asset_healthy_cell.py && blender --background --python blender/scripts/asset_tumor_cell.py`
Expected: both print their `VALIDATION PASSED`.

- [ ] **Step 7: Commit**

```bash
git add blender/scripts/asset_immune_cells.py
git commit -m "Give immune cells the membrane deformation hook

Healthy and tumour cells already shipped MOD_MembraneDeform; immune cells did
not, so the idle layer would have had nothing to drive on 04_IMMUNE_SYSTEM.
Same modifier, same per-cell OBJECT-space binding, no keyframes -- the idle
layer still owns all animation."
```

---

### Task 2: The idle module — periods, per-cell variation, and the tumour guard

The core of the feature. Membrane undulation for all three classes, nucleus drift, immune root drift, and the validator measurement that enforces "tumour is not faster".

**Files:**
- Create: `blender/scripts/animation_idle.py`
- Test: same file — `validate_idle()` + `main()`

**Interfaces:**
- Consumes: `ut.clear_animation(target)`, `ut.obj_name(prefix, index)`, `COLLECTION`/`PREFIX` constants from `asset_healthy_cell`, `asset_tumor_cell`, `asset_immune_cells` (all four immune prefixes).
- Produces: everything in the spec's Public API —
  `IDLE_MOTION_SCALE: float`, `LOOP_FRAMES: int = 240`, `PERIOD_DIVISORS`,
  `IDLE_SPECS: dict`, `cell_period(index, cls) -> int`, `animate_idle(healthy_ctrl=None, tumor_ctrl=None, immune_ctrl=None, motion_scale=IDLE_MOTION_SCALE, frame_start=1, frame_end=LOOP_FRAMES) -> dict`, `validate_idle(collection=None) -> dict`, `main() -> None`.

- [ ] **Step 1: Write the module skeleton with the spec, period set, and cell discovery**

Constants, then the three class specs. Each spec carries `prefix`, `membrane_amplitude`, `nucleus_amplitude`, `period_pool`, `drifts`, and `noise_scale`:

| Class | membrane_amplitude | nucleus_amplitude | period_pool | drifts | noise_scale |
|---|---|---|---|---|---|
| healthy | 0.06 | 0.12 | (240, 120, 80, 60) | False | 0.35 |
| tumor | 0.05 | 0.14 | (240, 120, 80, 60) | False | 0.20 |
| immune | 0.07 | 0.10 | (120, 80, 60, 48, 40) | True | 0.35 |

The two constraints that matter are visible in that table: tumour's amplitude is
**lower** and its `noise_scale` **finer** (lumpier, not faster), and its period
pool is the same set as healthy's so the validator's speed comparison is not
handed an excuse by differing period ranges.

`cell_period(index, cls)` must return a value that divides `LOOP_FRAMES` exactly —
`PERIOD_DIVISORS = (240, 120, 80, 60, 48, 40)`, chosen with `random.Random(index)`
seeded per cell. Assert `LOOP_FRAMES % period == 0` in the function itself.

Discovery: walk each target collection, find root empties by prefix, and take each
root's children by suffix (`MEMBRANE`, `NUCLEUS`). Immune roots come from all four
prefixes. Do not parse suffixes off child names — take the parented children, which
is what the asset modules already guarantee.

- [ ] **Step 2: Write the failing loop test**

```python
def test_loop_is_seamless():
    """Every animated channel returns to its frame-1 value at LOOP_FRAMES + 1."""
```

Build a small test population (4 healthy, 4 tumor, 2 immune), call `animate_idle`,
then for each animated object's `animation_data.action`, for each fcurve, sample the
evaluated value at frame 1 and at frame `LOOP_FRAMES + 1` and assert they are equal
within 1e-4.

This samples the curves rather than trusting the construction, so it catches a
period that does not divide the loop.

- [ ] **Step 3: Write the failing tumour-speed test**

```python
def test_tumor_not_faster_than_healthy():
    """The spec's core constraint, measured per channel."""
```

For each class, evaluate the animated membrane-strength fcurve and the nucleus-location
fcurves across the loop, derive peak per-frame surface speed in micrometres, and
assert `tumor_peak <= healthy_peak`. Measure, do not compare the amplitude constants
directly — a regression introduced through the noise scale or the period pool must
also be caught, and a constant comparison would miss those.

- [ ] **Step 4: Run to verify both fail**

Run: `blender --background --python blender/scripts/animation_idle.py`
Expected: import error or `[idle] FAIL:` on the unimplemented `animate_idle`.

- [ ] **Step 5: Implement `animate_idle`**

For each discovered cell, in order:

1. `ut.clear_animation(obj)` on the membrane and the nucleus.
2. Get `P = cell_period(index, cls)`.
3. Membrane: key `modifiers["MOD_MembraneDeform"].strength` at frames
   `1, 1+P, 1+P/2` (in that insertion order is irrelevant; sort by frame), values
   `base, base, base + membrane_amplitude * motion_scale`. Add a `CYCLES` modifier
   with `mode_before='REPEAT'`, `mode_after='REPEAT'`. Set
   `kf.interpolation = 'BEZIER'` with `handle_left_type`/`handle_right_type` of
   `'AUTO_CLAMPED'` — clamped, so the curve cannot overshoot and push the surface
   outside its authored bounds.
4. Nucleus: key `location` on a small closed path over the same period, so it
   returns exactly to start. Same `CYCLES` treatment, same clamped interpolation.
5. Immune roots only: key `location` on a closed drift path, seeded per cell for
   axis and sign.

Set the class's `noise_scale` on the shared texture in this same pass. Because all
membranes share one texture per class, do this once per class before the cell loop,
not per cell.

Return a report dict: per-class object counts, total fcurve count, and the measured
peak speed per class that `validate_idle` reads.

- [ ] **Step 6: Run to verify both pass**

Run: `blender --background --python blender/scripts/animation_idle.py`
Expected: `[idle] checks: {...'tumor_peak_speed': ..., 'healthy_peak_speed': ...}` then `[idle] VALIDATION PASSED`.

- [ ] **Step 7: Commit**

```bash
git add blender/scripts/animation_idle.py
git commit -m "Animate idle cell motion and guard the tumour speed constraint

Healthy and tumour cells undulate on the existing Displace hook, immune cells
drift, and every period is drawn from exact divisors of 240 so the loop closes.
Tumour cells differ by a finer noise scale and lower amplitude rather than a
faster rate, and validate_idle measures peak surface speed per class so that
constraint fails the build if a later edit breaks it."
```

---

### Task 3: Pause and reduced motion

One multiplier. Pausing writes flat keys rather than removing them, so re-enabling cannot desynchronise the beat layer.

**Files:**
- Modify: `blender/scripts/animation_idle.py`
- Test: same file — `validate_idle()`

**Interfaces:**
- Consumes: `animate_idle(...)` from Task 2
- Produces: `set_idle_motion(scale: float) -> float`, `REDUCED_MOTION_SCALE: float = 0.3`

- [ ] **Step 1: Add the failing test for both modes**

```python
def test_reduced_motion_is_quieter():
    """scale 0.3 displaces no further than 1.0; scale 0.0 is perfectly flat."""
```

Build, animate at `motion_scale=1.0`, record peak displacement. Rebuild at `0.3` and
at `0.0`. Assert the `0.3` peak is `<=` the `1.0` peak, and that at `0.0` every
channel's value is identical across the loop.

- [ ] **Step 2: Write the failing test for pause preserving structure**

```python
def test_pause_keeps_keys():
    """Pausing must not delete keys -- the beat layer shares frames 1-240."""
```

Animate at `0.0` and assert the fcurve and keyframe count matches the count from
`motion_scale=1.0`, and that every key's value is identical to its neighbour.

- [ ] **Step 3: Run to verify both fail**

Expected: `[idle] FAIL:` — `set_idle_motion` does not exist yet.

- [ ] **Step 4: Implement `set_idle_motion`**

```python
def set_idle_motion(scale: float) -> float:
    """Re-key the idle layer at *scale*. 1.0 full, 0.3 reduced motion, 0.0 paused."""
```

Clamp to `[0.0, 1.0]`, store on the module, and re-run `animate_idle` with that
scale. Re-keying rather than editing existing keys is the simple option and the
project's `clear_animation` already makes it safe. Add `REDUCED_MOTION_SCALE = 0.3`
as the documented reduced-motion value.

- [ ] **Step 5: Run to verify both pass**

Run: `blender --background --python blender/scripts/animation_idle.py`
Expected: `[idle] VALIDATION PASSED`.

- [ ] **Step 6: Commit**

```bash
git add blender/scripts/animation_idle.py
git commit -m "Add pause and reduced-motion as one strength multiplier

Reduced motion is scale 0.3 and pause is scale 0.0 -- the same knob, so there is
one control to document. Pausing writes flat keys instead of clearing them, so
re-enabling cannot desynchronise from the beat layer sharing frames 1-240."
```

---

### Task 4: Idempotent rebuild, no geometry change, and docs

Proves the layer adds keys and not objects, and that a second run is safe.

**Files:**
- Modify: `blender/scripts/animation_idle.py` (validator)
- Modify: `blender/README.md`
- Modify: `blender/scripts/scene_tumor.py`

**Interfaces:**
- Consumes: `animate_idle`, `validate_idle` from Tasks 2–3
- Produces: `IDLE_PARENT` in `scene_tumor.py`; README entry; `clear_idle() -> int`

- [ ] **Step 1: Write the failing idempotency test**

```python
def test_rebuild_is_idempotent():
    """A second animate_idle leaves the same F-curve count and no duplicate keys."""
```

Run `animate_idle` twice; assert the fcurve count and total keyframe count are
identical, and that no object has more than one `CYCLES` modifier per fcurve.

- [ ] **Step 2: Write the failing no-geometry test**

```python
def test_animating_adds_no_geometry():
    """The layer adds keys, not objects."""
```

Record the object count and the mesh datablock count before and after
`animate_idle`. Both must be unchanged. This is the test that keeps the feature an
animation layer instead of letting it leak into geometry.

- [ ] **Step 3: Run to verify they fail, or expose the real defect**

Expected: either a failure, or confirmation they already hold. If they already
hold, keep the tests — they are the guard for Review Focus items 4 and 5.

- [ ] **Step 4: Add `clear_idle` and wire the two checks into `validate_idle`**

```python
def clear_idle() -> int:
    """Remove every keyframe the idle layer wrote. Returns the object count."""
```

Walk the three target collections, `ut.clear_animation` each object that has
animation data. Used to reset the layer without touching geometry.

- [ ] **Step 5: Wire the idle layer into `scene_tumor.py`**

Add `import animation_idle` beside `import asset_ecm`, and call
`animation_idle.animate_idle(...)` from `build()` after `build_ecm(...)`, passing
the three controllers. Parent the same way the ECM parents — to `CTRL_Master` for
the matrix, and to each class's own controller for its cells. Add `"idle_cells"` to
the `build()` return dict.

Keep it unconditioned. The idle layer is ambient, so a scene built without it would
look static, which is the failure this feature exists to prevent.

- [ ] **Step 6: Verify the scene builder is still idempotent with the layer attached**

Run: `blender --background --python blender/scripts/scene_tumor.py -- --selftest`
Expected: `{'duplicated': [], 'ok': True}`. The existing selftest builds twice and
would catch a name collision between the idle layer and the scaffold.

- [ ] **Step 7: Document it in `blender/README.md`**

Add `animation_idle.py` to the script-structure tree, and a short "Idle motion"
subsection covering: the three class behaviours, the `motion_scale` values
(`1.0` / `0.3` / `0.0`), the 240-frame loop with divisor-constrained periods, and
the tumour-is-not-faster rule with a pointer to the validator check that enforces it.

- [ ] **Step 8: Run the full validation sweep**

```sh
cd ~/Work/science-fair-virotherapy
for m in asset_healthy_cell asset_tumor_cell asset_immune_cells animation_idle; do
  blender --background --python blender/scripts/$m.py || exit 1
done
blender --background --python blender/scripts/scene_tumor.py -- --selftest
```

Expected: every module prints its `VALIDATION PASSED`; selftest reports `ok: True`.

- [ ] **Step 9: Commit**

```bash
git add blender/scripts/animation_idle.py blender/scripts/scene_tumor.py blender/README.md
git commit -m "Wire idle motion into the scene and document it

Adds the rebuild and no-geometry guards, clear_idle, and a scene_tumor call so a
rebuilt scene is never static. The no-geometry check is what keeps this an
animation layer rather than a geometry one."
```

---

## Self-Review

**Spec coverage.** Looping → Task 2 Steps 1–2, 5. Per-cell variation → Task 2 Step 1
(`cell_period` plus seeded phase/axis). Healthy/tumour/immune behaviours → Task 2
Step 5. Tumour-irregular-not-faster → Task 2 Steps 1, 3, 5. Pause and reduced
motion → Task 3. Performance (bounded keys, no handlers, shared texture) → Task 2
Steps 1, 5. All seven spec checks map: loop→T2S2, tumour speed→T2S3, reduced
motion→T3S1, periods divide→T2S2, cells vary→T2S5, idempotent→T4S1, no
geometry→T4S2. Nothing in the spec is unmapped.

**Step scan.** Every step is one action with a checkable result. No step says
"handle edge cases" or names a function no task defines. The two measurement
tests (T2S2, T2S3) state the assertion and the sampling method without
transcribing the sampling loop, because the method is a judgment the implementer
should make once against the real curve data.

**Type consistency.** `IDLE_MOTION_SCALE`, `LOOP_FRAMES`, `PERIOD_DIVISORS`,
`IDLE_SPECS`, `cell_period`, `animate_idle`, `set_idle_motion`, `clear_idle`,
`validate_idle` are named identically in the interfaces blocks, the code steps, and
the commit messages. `REDUCED_MOTION_SCALE` is introduced in Task 3 and not used
before it exists. `animate_idle`'s return is called a "report dict" consistently and
Task 2 Step 5 is the only place its shape is defined.

**Review Focus.** All five lines are covered by a named test in the owning task:
RF1→T2S2, RF2→T2S3, RF3→T3S2, RF4→T4S1, RF5→T4S2. RF3 is the subtle one — it is
why Task 3 Step 2 exists as a separate test from Step 1 rather than being folded
into it.

**Proportion.** The plan is four tasks, twenty-four steps, against a spec of about
200 lines. Code blocks are limited to the constants table, the validator snippet
that must mirror an existing one, and three test headers. The heavy lifting —
`animate_idle`'s body — is left as signature plus per-channel instructions, which
is the right level: the spec pins the values, the tests pin the behaviour, and the
body is the implementer's.
