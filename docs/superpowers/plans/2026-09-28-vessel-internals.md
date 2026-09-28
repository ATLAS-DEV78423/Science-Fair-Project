# Vessel Internals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the missing contents and wall tissue of a tumour blood vessel — plasma/fibrin, smooth muscle, basement membrane, pericytes, discrete endothelial cells with leak gaps, a thrombus, and white cells that marginate and extravasate.

**Architecture:** All work lands in the single existing module `blender/scripts/asset_blood_vessel.py`. The lumen and endothelium radii are frozen so every existing containment calculation is untouched; the new wall layers are absorbed *outward* into a growing adventitia. `BLOOD_CLASSES` splits into `LUMEN_CONTENTS` and `WALL_BOUND` so each group gets the containment or pinning check that is actually true of it.

**Tech Stack:** Blender 5.2, `bpy`/`bmesh`, `mathutils`, Cycles. Python 3.14. No new dependencies. Tests are the module's own validator run under `blender --background`.

**Spec:** `docs/superpowers/specs/2026-09-28-vessel-internals-design.md`

## File Structure

| File | Change |
|---|---|
| `blender/scripts/asset_blood_vessel.py` | Modified throughout. Every task below touches it. No new files. |

This module is already 1501 lines and will reach roughly 2100. It stays one file deliberately: the new wall layers, the lumen contents and the validator are one unit — they share the shell-radius arithmetic, and a second module would have to import another module's private helpers (`_tube_mesh`, `_parallel_frames`, `_distance_to_path`) to do it. The README's own "Visual legibility is not automatic" section is the thing that would be lost by splitting, because the render check is the only way to validate the layering.

## Global Constraints

Applies to every task. These are not per-task choices.

- **1 Blender unit == 1 micrometre.** Never introduce a scale factor. `ut.SCENE_SCALE_LENGTH` is `1e-6`.
- **Never bake animation.** No keyframes, no drivers, no actions on any object. The animation scripts own the keys. The only permitted constraint type is `FOLLOW_PATH`.
- **Instancing, never duplication.** Every repeating component is an instance of exactly one shared mesh datablock, created through `ut.instance_linked` from a source built by `ut.get_or_create_object`.
- **Every generated object is fetched by name before creation** via `ut.get_or_create_object(name, ...)`. The name is the identity key. Never `bpy.data.objects.new` directly for a named object.
- **Names** via `ut.obj_name(prefix, index)` → `<PREFIX>_<NNN>`, zero-padded to 3, 1-based, clamped at 1. Sources use index 1; per-vessel instances start at index 2.
- **Materials** named `MAT_Vessel_<Part>`. The `MAT_Vessel_*` domain already exists.
- **A builder only touches its own collection.** Enforced by `ut.link_object`; never unlink into another collection by hand.
- **Deletion only via `ut.clear_collection(coll, prefix)`** with an explicit prefix. Never delete by hand.
- **Seeded randomness.** Every `random.Random(seed)` is constructed once in `create_blood_vessel` and threaded down. Same seed → same vessel.
- **Objects are parented with `ut.set_parent`**, never by assigning `.parent` directly — it sets `matrix_parent_inverse` to identity, which is what makes `obj.location` mean parent-local space.
- **Units in comments and messages are µm**, matching the code's existing convention.
- **Test command for every task:**
  ```sh
  cd ~/Work/science-fair-virotherapy && blender --background --python blender/scripts/asset_blood_vessel.py
  ```
  Passes when the final line is `[vessel] VALIDATION PASSED`. `main()` raises `SystemExit` otherwise, so a non-zero exit is a failure.

## Review Focus

The five failure modes a reasonable person would expect to be handled but that the spec's own tests do not obviously pin. Each has its test assigned to the owning task below.

1. **Rebuild runs twice.** Calling `build_test_vessel()` twice must not accumulate `VESSEL_002.001` names or grow `bpy.data`. → Task 1
2. **A vessel too narrow for a component carries none of it.** A capillary must not gain a red cell, a platelet, or a pericyte it cannot physically hold. → Task 5
3. **A gap must actually be a gap.** If `gap_count` is requested and no cell is omitted, the validator must fail rather than pass. → Task 6
4. **A white cell must not float off into space.** A cell with role `EXTRAVASATING` is outside the lumen by design, so the containment check skips it — it needs its own check that it is still within the vessel. → Task 7
5. **The clot must not intersect the blood column.** The existing containment check measures distance from the centreline only and cannot see a wall-attached clot reaching inward. → Task 6

---

### Task 1: Wall layer arithmetic and the new shells

The foundation. Everything else places itself relative to a named surface radius, so this task establishes those radii and proves them from geometry.

**Files:**
- Modify: `blender/scripts/asset_blood_vessel.py:100-102` (suffix constants), `:112-118` (thickness constants), `:522` (`_tube_mesh` — no change needed, reuse), `:973` (`create_blood_vessel` shell block), `:1126` (`build_test_vessel`)

**Interfaces:**
- Consumes: `_tube_mesh(name, frames, radius, factor, *, cap_start=True, cap_end=True)`, `_resample_even`, `_parallel_frames`, `_radius_profile(rng)`, `ut.obj_name`, `ut.get_or_create_object`, `ut.assign_material`
- Produces:
  - `SUFFIX_SMOOTH_MUSCLE = "SMOOTH_MUSCLE"`, `SUFFIX_BASEMENT = "BASEMENT_MEMBRANE"`
  - `MUSCLE_MIN_RADIUS_UM = 5.0`, `MUSCLE_WALL_FRACTION = 0.18`, `MUSCLE_MIN_THICKNESS_UM = 0.3`, `BASEMENT_THICKNESS_UM = 0.15`, `ADVENTITIA_MIN_UM = 0.3`
  - `def muscle_thickness(radius: float) -> float` — returns `0.0` below `MUSCLE_MIN_RADIUS_UM`, else `MUSCLE_MIN_THICKNESS_UM + (radius - MUSCLE_MIN_RADIUS_UM) * MUSCLE_WALL_FRACTION`
  - `def wall_radii(radius: float, lumen_radius: float, endothelium_radius: float) -> dict` — returns keys `"basement"`, `"muscle"`, `"adventitia"`, `"outer"`, each a float; `"muscle"` is `None` when `muscle_thickness` is 0.0
  - `WALL_SHELL_ORDER` tuple for the validator: `("outer", "muscle", "basement", "endothelium", "lumen")`

- [ ] **Step 1: Write the failing test for `muscle_thickness` and `wall_radii`**

Add to the bottom of the module, above `main()`:

```python
def _selftest_wall_geometry() -> dict:
    """Pure-arithmetic check on the wall layer radii. No bpy needed."""
    out = {}
    # Below the threshold there is no media at all: a capillary is a single
    # endothelial layer, so the muscle shell must be absent, not zero-thick.
    assert muscle_thickness(2.5) == 0.0
    # Above it, floored, so the shell is always a resolvable surface. An
    # unfloored proportional term is 0.0018 um at r=5.01, which a 24-segment
    # ring cannot represent.
    assert abs(muscle_thickness(5.01) - 0.3018) < 1e-9
    assert abs(muscle_thickness(15.0) - 2.1) < 1e-9

    for radius in (CAPILLARY_RADIUS_UM, 5.0, 5.01, VENULE_RADIUS_UM, 30.0):
        endo = radius - WALL_THICKNESS_UM
        lumen = endo - LUMEN_THICKNESS_UM
        radii = wall_radii(radius, lumen, endo)
        # Lumen and endothelium are FROZEN. This is the load-bearing
        # guarantee: every containment check in the project is computed
        # against the lumen radius, and it must not move.
        assert radii["basement"] == endo + BASEMENT_THICKNESS_UM
        # The outer wall never shrinks below the requested radius.
        assert radii["outer"] >= radius - 1e-9
        # Nesting is monotonic over whatever shells exist.
        chain = [radii["outer"]]
        if radii["muscle"] is not None:
            chain.append(radii["muscle"])
        chain += [radii["basement"], endo, lumen]
        assert all(a > b for a, b in zip(chain, chain[1:])), chain
        out[radius] = chain
    return out
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```sh
cd ~/Work/science-fair-virotherapy && blender --background --python-expr "
import sys; sys.path.insert(0,'blender/scripts')
import asset_blood_vessel as bv
bv._selftest_wall_geometry()
"
```
Expected: `NameError: name 'muscle_thickness' is not defined`

- [ ] **Step 3: Implement the constants and the two functions**

Insert after `LUMEN_THICKNESS_UM` (line ~116):

```python
#: Radii at or below this get no smooth muscle shell at all. A capillary is a
#: single endothelial layer with scattered pericytes; it has no media layer.
#: The shell is *absent* rather than zero-thickness, because a zero-thickness
#: tube is degenerate geometry.
MUSCLE_MIN_RADIUS_UM = 5.0
#: Media growth per micrometre of radius, above the threshold.
MUSCLE_WALL_FRACTION = 0.18
#: Floor on media thickness. Not cosmetic: unfloored, the term is 0.0018 um at
#: r=5.01, which is below what a 24-segment ring resolves, so the validator's
#: geometric nesting check would pass by luck. Same rule as LUMEN_THICKNESS_UM
#: -- a distinct visible surface, not a modelled layer.
MUSCLE_MIN_THICKNESS_UM = 0.3
#: The basement membrane is a thin sheet under the endothelium.
BASEMENT_THICKNESS_UM = 0.15
#: Floor on the outermost connective layer, so it never collapses onto the
#: media and the nesting chain keeps five distinct surfaces.
ADVENTITIA_MIN_UM = 0.3
```

and the functions:

```python
def muscle_thickness(radius: float) -> float:
    """Media thickness for a vessel of *radius*. Zero below the capillary scale."""
    if radius < MUSCLE_MIN_RADIUS_UM:
        return 0.0
    return MUSCLE_MIN_THICKNESS_UM + (radius - MUSCLE_MIN_RADIUS_UM) * MUSCLE_WALL_FRACTION


def wall_radii(radius: float, lumen_radius: float,
               endothelium_radius: float) -> dict:
    """Radii of the wall layers outside the endothelium.

    The lumen and endothelium radii are inputs, not outputs, and are returned
    unchanged: every containment check in this project is computed against the
    lumen, so the new tissue is added *outward* rather than eating into it.

    The three new layers absorb whatever is left of *radius*, which is why the
    outer wall lands exactly on *radius* for a capillary and slightly outside
    it for a larger vessel.
    """
    basement = endothelium_radius + BASEMENT_THICKNESS_UM
    muscle = muscle_thickness(radius)
    adventitia = max(ADVENTITIA_MIN_UM, radius - basement - muscle)
    return {"basement": basement,
            "muscle": basement + muscle if muscle > 0.0 else None,
            "adventitia": adventitia,
            "outer": basement + muscle + adventitia}
```

- [ ] **Step 4: Run the test to verify it passes**

Run the Step 2 command again.
Expected: no output, exit 0. (It prints nothing because the function returns a dict and the expression statement discards it.)

- [ ] **Step 5: Wire the two new shells into `create_blood_vessel`**

In `create_blood_vessel`, replace the `radii` dict with one built from `wall_radii`, and add the two new shells to the shell loop. The existing `shells` loop iterates `(("wall", SUFFIX_OUTER_WALL), ("endothelium", ...), ("lumen", ...))`; make it iterate the full `WALL_SHELL_ORDER`, skipping `None` muscle, and set the outer wall's radius from `wall_radii(...)["outer"]` rather than from `radius`.

Careful: the loop currently reads `radii[key]`. Introduce `radii` as the merged dict of the three existing keys plus the new ones so the loop body and `materials[key]` lookup keep working — the material dict keys must match, so `ensure_materials()` needs `"muscle"` and `"basement"` entries (Task 2 adds them; for this task pass the wall material for both so the build stays green, then Task 2 replaces them).

Add to `ensure_materials()`'s tuple so the lookup cannot `KeyError`:
`("muscle", MAT_WALL), ("basement", MAT_ENDOTHELIAL),` reusing existing materials as placeholders.

- [ ] **Step 6: Run the full suite**

Run: `blender --background --python blender/scripts/asset_blood_vessel.py`
Expected: `VALIDATION PASSED`, and `checks.shells` rises from 9 to 15 (three vessels: capillary gains basement only, the two venules gain all three).

- [ ] **Step 7: Commit**

```bash
git add blender/scripts/asset_blood_vessel.py
git commit -m "Add smooth muscle, basement membrane and adventitia to the vessel wall

The lumen and endothelium radii are frozen and every new layer is
absorbed outward, so all existing containment maths is untouched. The
media shell is absent below 5 um radius, because a capillary is a single
endothelial layer with no media."
```

---

### Task 2: Materials for the new layers

**Files:**
- Modify: `blender/scripts/asset_blood_vessel.py:259-316` (material specs), `:327` (`ensure_materials`), `:438-450` (new specs), `:316` (`_material` — no change)

**Interfaces:**
- Consumes: `ut.principled_material`, `_material(spec)`
- Produces: `MAT_SMOOTH_MUSCLE`, `MAT_BASEMENT`, `MAT_PERICYTE`, `MAT_ENDO_CELL`, `MAT_FIBRIN`, `MAT_CLOT` spec dicts; `ensure_materials()` gains keys `"muscle"`, `"basement"`, `"pericyte"`, `"endo_cell"`, `"fibrin"`, `"clot"`

- [ ] **Step 1: Write the failing test**

```python
def _selftest_materials() -> None:
    mats = ensure_materials()
    for key in ("muscle", "basement", "pericyte", "endo_cell", "fibrin", "clot"):
        assert key in mats, key
        assert mats[key].name.startswith("MAT_Vessel_"), (key, mats[key].name)
    # The alpha budget is the whole point of this task. Bulk tissue fades back,
    # discrete objects hold: seven nested translucent shells is the legibility
    # risk, and the mitigation is that new shells are the MOST transparent and
    # new cells the MOST opaque.
    assert mats["muscle"].node_tree.nodes["Principled BSDF"].inputs["Alpha"].default_value < 0.2
    assert mats["basement"].node_tree.nodes["Principled BSDF"].inputs["Alpha"].default_value < 0.2
    assert mats["pericyte"].node_tree.nodes["Principled BSDF"].inputs["Alpha"].default_value > 0.8
    assert mats["endo_cell"].node_tree.nodes["Principled BSDF"].inputs["Alpha"].default_value > 0.8
    # Idempotent: a second call must not create a second datablock.
    names = {m.name for m in ensure_materials().values()}
    assert len(names) == len(mats)
```

- [ ] **Step 2: Run it to verify it fails**

```sh
cd ~/Work/science-fair-virotherapy && blender --background --python-expr "
import sys; sys.path.insert(0,'blender/scripts')
import asset_blood_vessel as bv
bv._selftest_materials()
"
```
Expected: `AssertionError: muscle`

- [ ] **Step 3: Add the six specs**

Append after `MAT_PLATELET`. Each uses the existing `dict(...)` shape with keys `name`, `base_color`, `roughness`, `subsurface`, `alpha`, `emission`. Exact alpha values from the spec:

| Constant | name | base_color | alpha |
|---|---|---|---|
| `MAT_SMOOTH_MUSCLE` | `MAT_Vessel_SmoothMuscle` | `(0.72, 0.42, 0.40, 1.0)` | `0.12` |
| `MAT_BASEMENT` | `MAT_Vessel_BasementMembrane` | `(0.85, 0.72, 0.68, 1.0)` | `0.10` |
| `MAT_PERICYTE` | `MAT_Vessel_Pericyte` | `(0.42, 0.46, 0.60, 1.0)` | `0.88` |
| `MAT_ENDO_CELL` | `MAT_Vessel_EndothelialCell` | `(0.88, 0.66, 0.60, 1.0)` | `0.90` |
| `MAT_FIBRIN` | `MAT_Vessel_Fibrin` | `(0.90, 0.86, 0.84, 1.0)` | `0.50` |
| `MAT_CLOT` | `MAT_Vessel_FibrinClot` | `(0.55, 0.10, 0.10, 1.0)` | `0.75` |

Roughness 0.5 for all; subsurface 0.15 for the two shells and 0.25 for the cells; emission matching the base colour with strength 0.0 (the `_material` helper already zeroes it).

Add `"muscle"`, `"basement"`, `"pericyte"`, `"endo_cell"`, `"fibrin"`, `"clot"` to the tuple in `ensure_materials`, replacing Task 1's placeholder entries.

- [ ] **Step 4: Run it to verify it passes**

Run the Step 2 command.
Expected: no output, exit 0.

- [ ] **Step 5: Run the full suite**

Run: `blender --background --python blender/scripts/asset_blood_vessel.py`
Expected: `VALIDATION PASSED`.

- [ ] **Step 6: Commit**

```bash
git add blender/scripts/asset_blood_vessel.py
git commit -m "Add materials for the vessel wall and blood components

Alpha is budgeted deliberately: the new bulk shells are the most
transparent surfaces and the new cells the most opaque, because seven
nested translucent shells between camera and blood is the real legibility
risk in this asset."
```

---

### Task 3: Fibrin and the thrombus meshes

Geometry only, no placement. Placement is Task 6.

**Files:**
- Modify: `blender/scripts/asset_blood_vessel.py` (new functions after `_platelet_mesh`, ~line 656)

**Interfaces:**
- Consumes: `bmesh`, `bpy`, `mathutils.noise`, `_extent`, `_finish_source`, `ut.obj_name`, `ut.get_or_create_object`, `ut.assign_material`
- Produces:
  - `PREFIX_FIBRIN = "FIBRIN_STRAND"`, `PREFIX_CLOT = "FIBRIN_CLOT"`
  - `FIBRIN_LENGTH_UM = 14.0`, `FIBRIN_RADIUS_UM = 0.06`, `FIBRIN_CLOT_RADIUS_UM = 3.0`
  - `_fibrin_mesh(name) -> object` — a thin curved strand
  - `_clot_mesh(name, radius) -> object` — a lumpy closed mass
  - `create_fibrin_strand(index=1, collection=None, location=(0,0,0), parent=None) -> object`
  - `create_fibrin_clot(index=1, collection=None, location=(0,0,0), parent=None) -> object`
  - `PROP_CLASS` on each: `"FIBRIN"` and `"FIBRIN_CLOT"`

- [ ] **Step 1: Write the failing test**

```python
def _selftest_clot_mesh() -> None:
    clot = _clot_mesh("MESH_TEST_CLOT", FIBRIN_CLOT_RADIUS_UM)
    assert len(clot.vertices) > 0
    # Closed, positive volume -- a clot is a solid mass, not a shell.
    bm = bmesh.new()
    bm.from_mesh(clot)
    assert not any(not v.link_faces for v in bm.verts), "loose verts"
    assert bm.calc_volume(signed=True) > 0.0, "inward normals or zero volume"
    bm.free()
    # Its extent is what the builder and validator both read, via PROP_EXTENT.
    assert _extent(clot) > FIBRIN_CLOT_RADIUS_UM, "lumpy mass is not larger than its nominal radius"
```

- [ ] **Step 2: Run it to verify it fails**

```sh
cd ~/Work/science-fair-virotherapy && blender --background --python-expr "
import sys; sys.path.insert(0,'blender/scripts')
import asset_blood_vessel as bv
bv._selftest_clot_mesh()
"
```
Expected: `NameError: name '_clot_mesh' is not defined`

- [ ] **Step 3: Implement `_clot_mesh`**

Build a UV sphere with `bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=10, radius=radius)`, then displace every vertex radially by a seeded noise term scaled to `radius * 0.35` — the same noise idiom `_radius_profile` already uses, so the mass is lumpy and reproducible. Recalculate normals, write to a new mesh, `shade_smooth()`.

The noise is what makes it a thrombus rather than a ball. Keep the amplitude at 0.35: higher and it stops fitting between the wall and the blood column.

- [ ] **Step 4: Implement `_fibrin_mesh`**

A short curved tube. Sweep 7 stations along a gently arced 3-point path using `bmesh.ops.spin`-free construction: create a ring of 6 verts per station at `FIBRIN_RADIUS_UM`, bridge consecutive rings with quads, cap both ends. Reuse the same parallel-transport approach as `_tube_mesh` but with a 2-point path, since the strand is short enough that the frame cannot twist.

Simplest correct approach: call the existing `_tube_mesh` with frames from `_parallel_frames` over a 3-point arc and `factor=lambda u, theta: 1.0`. That reuses code that is already tested rather than writing a second tube builder.

- [ ] **Step 5: Implement the two `create_*` builders**

Both follow `create_platelet` exactly, calling `_finish_source` so they get `PROP_SOURCE` and `PROP_EXTENT` from their own geometry:

```python
def create_fibrin_strand(index=1, collection=None, location=(0.0, 0.0, 0.0), parent=None):
    coll = collection or ut.resolve_collection(COLLECTION)
    obj = ut.get_or_create_object(
        ut.obj_name(PREFIX_FIBRIN, index), coll,
        lambda n: _fibrin_mesh("MESH_{}".format(n)))
    ut.assign_material(obj, ensure_materials()["fibrin"])
    obj[PROP_CLASS] = "FIBRIN"
    return _finish_source(obj, coll, location, parent)
```

`create_fibrin_clot` is identical with `PREFIX_CLOT`, `_clot_mesh("MESH_{}".format(n), FIBRIN_CLOT_RADIUS_UM)`, `ensure_materials()["clot"]`, `PROP_CLASS = "FIBRIN_CLOT"`.

- [ ] **Step 6: Run the test to verify it passes**

Run the Step 2 command.
Expected: no output, exit 0.

- [ ] **Step 7: Run the full suite**

Run: `blender --background --python blender/scripts/asset_blood_vessel.py`
Expected: `VALIDATION PASSED`. The new sources are not placed yet, so no new objects appear in the test collection.

- [ ] **Step 8: Commit**

```bash
git add blender/scripts/asset_blood_vessel.py
git commit -m "Add fibrin strand and thrombus meshes

The clot is a lumpy closed mass rather than a ball because a thrombus is
attached to the wall with blood flowing past it -- a ball in the middle
of the lumen would contradict the blood still being in there. Both are
instanced from one shared mesh, like every other blood component."
```

---

### Task 4: Wall-bound cell meshes — pericyte and endothelial cell

**Files:**
- Modify: `blender/scripts/asset_blood_vessel.py` (new functions after `create_fibrin_clot`)

**Interfaces:**
- Consumes: `bmesh`, `_finish_source`, `_disc_mesh`, `ut.obj_name`, `ut.get_or_create_object`, `ut.assign_material`
- Produces:
  - `PREFIX_PERICYTE = "PERICYTE"`, `PREFIX_ENDO_CELL = "VESSEL_ENDOTHELIAL_CELL"`
  - `PERICYTE_DIAMETER_UM = 8.0`, `PERICYTE_THICKNESS_UM = 1.2`, `PERICYTE_PROCESSES = 3`
  - `ENDO_CELL_DIAMETER_UM = 14.0`, `ENDO_CELL_THICKNESS_UM = 1.0`
  - `_pericyte_mesh(name) -> object`, `_endo_cell_mesh(name) -> object`
  - `create_pericyte(index=1, collection=None, location=(0,0,0), parent=None) -> object` — `PROP_CLASS = "PERICYTE"`
  - `create_endothelial_cell(index=1, collection=None, location=(0,0,0), parent=None) -> object` — `PROP_CLASS = "ENDOTHELIAL_CELL"`

- [ ] **Step 1: Write the failing test**

```python
def _selftest_wall_cells() -> None:
    for mesh_fn, nominal in ((_pericyte_mesh, PERICYTE_DIAMETER_UM * 0.5),
                             (_endo_cell_mesh, ENDO_CELL_DIAMETER_UM * 0.5)):
        mesh = mesh_fn("MESH_TEST")
        assert len(mesh.vertices) > 0
        bm = bmesh.new()
        bm.from_mesh(mesh)
        assert not any(not v.link_faces for v in bm.verts), "loose verts"
        assert bm.calc_volume(signed=True) > 0.0, "inward normals or zero volume"
        bm.free()
        # A wall cell is flat, not a ball: it has to read as a tile on a
        # surface. Its extent must be dominated by its width.
        measured = max(v.co.length for v in mesh.vertices)
        assert measured < nominal * 1.2, (nominal, measured)
```

- [ ] **Step 2: Run it to verify it fails**

```sh
cd ~/Work/science-fair-virotherapy && blender --background --python-expr "
import sys; sys.path.insert(0,'blender/scripts')
import asset_blood_vessel as bv
bv._selftest_wall_cells()
"
```
Expected: `NameError: name '_pericyte_mesh' is not defined`

- [ ] **Step 3: Implement `_endo_cell_mesh`**

An endothelial cell is a flat tile with a slightly domed centre, so the existing `_disc_mesh` is exactly right — reuse it rather than writing a new surface of revolution:

```python
_disc_mesh(name, ENDO_CELL_DIAMETER_UM, ENDO_CELL_THICKNESS_UM * 0.75,
           ENDO_CELL_THICKNESS_UM, steps=7, spin=16)
```

Centre thinner than rim reads as a shallow dish. The disc's spin axis is Y, so the cell lies flat in XZ and spans XY — Task 5's placement rotates it onto the surface, so the axis convention does not matter here.

- [ ] **Step 4: Implement `_pericyte_mesh`**

A pericyte is a stellate cell with long processes that wrap the vessel it sits on. Not a revolution surface, so this one is built directly:

1. `bmesh.ops.create_icosphere(bm, subdivisions=2, radius=PERICYTE_DIAMETER_UM * 0.28)` for the cell body.
2. For each of `PERICYTE_PROCESSES`, take a body vertex, pick a direction in the XY plane, and add a tapered 3-segment tube of radius `0.18` tapering to `0.05`, length `PERICYTE_DIAMETER_UM * 0.36`.

The processes are what make a pericyte read as a pericyte rather than as a bead glued to the wall. Keep them in the XY plane so they lie *along* the vessel surface once placed — a pericyte wraps the vessel, it does not stick straight out of it.

- [ ] **Step 5: Implement the two `create_*` builders**

Same shape as `create_fibrin_strand`, with `PERICYTE` / `ENDOTHELIAL_CELL` prefixes, the `_pericyte_mesh` / `_endo_cell_mesh` factories, `ensure_materials()["pericyte"]` / `["endo_cell"]`, and the classes above.

- [ ] **Step 6: Run the test to verify it passes**

Run the Step 2 command.
Expected: no output, exit 0.

- [ ] **Step 7: Run the full suite**

Run: `blender --background --python blender/scripts/asset_blood_vessel.py`
Expected: `VALIDATION PASSED`.

- [ ] **Step 8: Commit**

```bash
git add blender/scripts/asset_blood_vessel.py
git commit -m "Add pericyte and endothelial cell meshes

The endothelial cell reuses _disc_mesh rather than a second revolution
builder, because a wall cell is a flat domed tile and that is what a
disc of revolution already is. The pericyte needs its own mesh: its
wrapping processes are the whole reason it reads as a pericyte rather
than a bead on the wall."
```

---

### Task 5: The class split and wall-bound placement

The structural change. Nothing is placed on the wall until `BLOOD_CLASSES` splits, because until it does the validator will report every wall cell as a containment failure.

**Files:**
- Modify: `blender/scripts/asset_blood_vessel.py:202` (`BLOOD_CLASSES`), `:889` (`_place_component`), `:973` (`create_blood_vessel`), `:1126` (`build_test_vessel`)

**Interfaces:**
- Consumes: `ut.instance_linked`, `_parallel_frames`, `ensure_materials`, `create_pericyte`, `create_endothelial_cell`
- Produces:
  - `LUMEN_CONTENTS = ("RED_BLOOD_CELL", "PLATELET", "LYMPHOCYTE", "MONOCYTE", "FIBRIN")`
  - `WALL_BOUND = ("ENDOTHELIAL_CELL", "PERICYTE")`
  - `BLOOD_CLASSES = LUMEN_CONTENTS + WALL_BOUND`
  - `WALL_SURFACES = {"ENDOTHELIAL_CELL": "endothelium", "PERICYTE": "basement"}` — the shell each wall-bound class pins to
  - `DEFAULT_ENDO_CELLS = 24`, `DEFAULT_PERICYTES = 10`
  - `PROP_CELLS_REQUESTED = "cells_requested"`, written on the vessel **root** as an integer in Step 5, and read by the validator in Task 6 Step 7
  - `_place_wall_component(source, prefix, count, frames, surface_radius, rng, vessel_class, root, coll, spread=1.0) -> list`
  - `create_blood_vessel(..., endothelium_cells=DEFAULT_ENDO_CELLS, pericytes=DEFAULT_PERICYTES, ...)`

- [ ] **Step 1: Write the failing test for the split**

```python
def _selftest_class_split() -> None:
    assert set(LUMEN_CONTENTS) | set(WALL_BOUND) == set(BLOOD_CLASSES)
    assert not set(LUMEN_CONTENTS) & set(WALL_BOUND)
    # A wall-bound class must NOT be lumen-contained, or the validator would
    # report the design as a bug.
    for label in WALL_BOUND:
        assert label not in LUMEN_CONTENTS
        assert label in WALL_SURFACES
```

- [ ] **Step 2: Run it to verify it fails**

```sh
cd ~/Work/science-fair-virotherapy && blender --background --python-expr "
import sys; sys.path.insert(0,'blender/scripts')
import asset_blood_vessel as bv
bv._selftest_class_split()
"
```
Expected: `NameError: name 'LUMEN_CONTENTS' is not defined`

- [ ] **Step 3: Make the split**

Replace the `BLOOD_CLASSES` definition with the three names above plus `WALL_SURFACES`. `BLOOD_CLASSES` stays the union on purpose: the topology, source-mesh and instancing checks are class-agnostic and must keep covering every component.

- [ ] **Step 4: Implement `_place_wall_component`**

Model it on `_place_component` but with two differences, both of them the point:

```python
def _place_wall_component(source, prefix, count, frames, surface_radius, rng,
                          vessel_class, root, coll, spread=1.0) -> list:
    """Place *count* wall-bound cells on the vessel surface. No constraint.

    Wall cells do not flow, so unlike _place_component this adds no FOLLOW_PATH
    constraint at all. That keeps the validator's "no constraint outside
    FOLLOW_PATH" rule true without exception.

    Position is frame-space at a station, offset radially by the surface radius
    plus a jitter, so the cell sits ON the surface rather than in the blood.
    The offset is expressed in the *sweep's* parallel-transport frame, which is
    the same frame the shells were built in, so the cell lands on the geometry
    it is meant to be sitting on.
    """
```

Body: for each of `count`, pick `u` spread along the path as `_place_component` does, pick an angle, compute `local = frames[station].to_3x3() @ Vector((cos*d, sin*d, 0.0))` where `d = surface_radius * rng.uniform(0.96, 1.04)`, then `ut.instance_linked(source, ut.obj_name(prefix, i + 2), coll, location=local, rotation=(rng.uniform(0, math.pi) * 0.5, rng.uniform(0, math.pi) * 0.5, angle), scale=..., parent=root)`, set `obj[PROP_SOURCE] = False` and `obj[PROP_CLASS] = vessel_class`.

Note the instance rotation: yaw follows the surface angle so the cell's flat face lies along the wall, pitch/roll are small random tilts. A cell randomly oriented on all three axes stands on edge half the time and reads as debris rather than as a lining.

- [ ] **Step 5: Wire placement into `create_blood_vessel`**

Add the two keyword arguments. Place pericytes on the `basement` radius and endothelial cells on the `endothelium` radius, both from the `wall_radii(...)` result. Both respect a fit check first: a pericyte wider than the vessel's surface circumference cannot wrap it, so skip placement when `pericyte_extent * 2 > basement * math.pi * 2` (i.e. the cell is wider than the circumference). `wbc_count=0` and `rbc_count=0` must still produce a vessel with no cells of that class, so the fit check gates placement rather than placement-then-discard.

- [ ] **Step 6: Run the suite and expect a specific failure**

Run: `blender --background --python blender/scripts/asset_blood_vessel.py`
Expected: **FAIL**, with wall-cell containment errors naming `PERICYTE` and `ENDOTHELIAL_CELL`.

This failure is expected and correct — it is Review Focus item 1's sibling, the split's whole reason for existing. Do not "fix" it by weakening the containment check. Task 6 Step 3 is where it is resolved.

- [ ] **Step 7: Confirm the idempotency Review Focus item**

Run twice, in one Blender session, and assert no name accumulation:
```sh
cd ~/Work/science-fair-virotherapy && blender --background --python-expr "
import sys; sys.path.insert(0,'blender/scripts')
import bpy, asset_blood_vessel as bv
bv.ut.setup_units()
bv.build_test_vessel(); n1 = len(bpy.data.objects)
bv.build_test_vessel(); n2 = len(bpy.data.objects)
assert n1 == n2, (n1, n2)
print('IDEMPOTENT', n1, n2)
"
```
Expected: `IDEMPOTENT <n> <n>`. Fix any `.001` name by finding the builder that used `bpy.data.objects.new` or a name that `ut.clear_collection`'s prefix list does not cover — the prefix list in `build_test_vessel` must now include `PREFIX_PERICYTE`, `PREFIX_ENDO_CELL`, `PREFIX_FIBRIN`, `PREFIX_CLOT`.

- [ ] **Step 8: Commit**

```bash
git add blender/scripts/asset_blood_vessel.py
git commit -m "Split blood classes and place wall-bound cells

LUMEN_CONTENTS and WALL_BOUND are separated because the existing
containment check would report a pericyte pinned to the wall as a
containment failure -- the check would be calling the design a bug.
BLOOD_CLASSES stays the union because the topology and instancing
checks are class-agnostic.

Wall cells get their own placement helper and deliberately carry no
constraint: they do not flow, and the validator's FOLLOW_PATH-only rule
stays true without exception."
```

---

### Task 6: Gaps, the clot, and containment that can see them

**Files:**
- Modify: `blender/scripts/asset_blood_vessel.py:889` (`_place_component` — add `radial_fraction` and `blocked_spans`), `:973` (`create_blood_vessel`), `:1126` (`build_test_vessel` — add the leaky case), `:1282` (`validate_blood_vessel`)

**Interfaces:**
- Consumes: everything from Tasks 1-5
- Produces:
  - `_place_component(source, prefix, count, frames, lumen_radius, rng, vessel_class, path, root, coll, spread=1.0, radial_fraction=1.0, blocked_spans=None) -> list`
  - `PROP_ROLE = "vessel_role"` — `"FLOW"` / `"MARGINATED"` / `"EXTRAVASATING"` / `"ESCAPING"`, and `"WALL"` for wall-bound cells
  - `PROP_GAPS_REQUESTED = "gaps_requested"` and `PROP_GAPS_OMITTED = "gaps_omitted"`, both written on the vessel **root** as integers
  - `DEFAULT_FIBRIN_COUNT = 12`, `DEFAULT_GAP_COUNT = 3`
  - `create_blood_vessel(..., fibrin_count=DEFAULT_FIBRIN_COUNT, gap_count=DEFAULT_GAP_COUNT)`
  - New validator measurements: `measurements["gaps"]`, `measurements["clot"]`

- [ ] **Step 1: Write the failing validator tests**

Add to `_selftest` coverage — these are assertions on `validate_blood_vessel`'s returned dict, run after `build_test_vessel()`:

```python
def _selftest_pathology() -> dict:
    report = validate_blood_vessel()
    m = report["measurements"]
    # Gaps must actually omit cells. If the omission logic silently stops
    # working, the vessel still looks plausible and nothing else catches it.
    assert "gaps" in m, "no gap measurement"
    for root_name, entry in m["gaps"].items():
        if entry["requested_gaps"]:
            assert entry["cells_placed"] == entry["cells_requested"], (root_name, entry)
    # The clot must not reach into the blood column. The containment check
    # measures distance from the centreline and cannot see this, so it needs
    # its own assertion.
    for root_name, entry in m["clot"].items():
        assert entry["min_centre_gap_um"] > 0.0, (root_name, entry)
    return report
```

- [ ] **Step 2: Run it to verify it fails**

```sh
cd ~/Work/science-fair-virotherapy && blender --background --python-expr "
import sys; sys.path.insert(0,'blender/scripts')
import asset_blood_vessel as bv
bv.ut.setup_units(); bv.build_test_vessel(); bv._selftest_pathology()
"
```
Expected: `AssertionError: no gap measurement`

- [ ] **Step 3: Resolve the wall-cell containment failures from Task 5**

In `validate_blood_vessel`, change the containment loop to iterate `LUMEN_CONTENTS` instead of `BLOOD_CLASSES`, and add a sibling pinning loop for `WALL_BOUND`:

For each wall-bound instance, its centre must satisfy `abs(_distance_to_path(centre, stations_world) - surface_radius) <= extent + RBC_FRAME_ROLL_TOLERANCE_UM`, where `surface_radius` is the vessel's measured `found[WALL_SURFACES[label]]` from the shell-nesting pass. Record the worst deviation in `measurements["pinned"]`.

Use `_distance_to_path` — distance to the *segment* — not the nearest station. The station bias is larger than a capillary is wide, and pinning a cell to a surface it is measured 0.5 µm away from is a false pass or a false failure depending on where the station lands.

- [ ] **Step 4: Add `radial_fraction` and `blocked_spans` to `_place_component`**

```python
def _place_component(source, prefix, count, frames, lumen_radius, rng,
                     vessel_class, path, root, coll, spread=1.0,
                     radial_fraction=1.0, blocked_spans=None) -> list:
```

- `radial_fraction` multiplies `clearance` before the `sqrt()` draw. `1.0` is today's behaviour. `2.0` pushes a cell past the endothelium.
- `blocked_spans` is a list of `(u_lo, u_hi)` pairs. A candidate whose `u` lands in a blocked span is retried up to 8 times at a jittered `u`; if every retry fails, the cell is skipped and the loop takes an extra one to keep the count. This preserves both the cell count and the spread along the vessel, which is what the existing `spread` check measures.
- Set `obj[PROP_ROLE] = "FLOW"` on every cell this function places. The caller overrides it afterwards for marginated and extravasating cells.

- [ ] **Step 5: Implement gaps as omitted cells**

In `create_blood_vessel`, place `endothelium_cells + gap_count` slots and drop the last `gap_count`. Record both numbers on the vessel root as `PROP_GAPS_REQUESTED` and `PROP_GAPS_OMITTED` so the validator reads the builder's intent rather than recounting and hoping.

Dropping the *last* slots means gaps cluster at one end of the vessel. Shuffle the slot order with the seeded `rng` first so they scatter, then drop the last `gap_count` — deterministic, and it does not read as a torn-off end.

Add fibrin strands escaping at each gap: same strand mesh, placed *outside* the endothelium radius, `PROP_ROLE = "ESCAPING"`.

- [ ] **Step 6: Place the clot and exclude it from the blood column**

Place one `FIBRIN_CLOT_001` against the wall, then compute its `u` span and pass that span as `blocked_spans` to the red cell, platelet and white cell placement calls. The clot's span is its half-length along the path plus its extent.

- [ ] **Step 7: Add the two new validator checks**

**Gap check.** The root records what the builder intended — `gaps_requested` and `gaps_omitted` — and the validator checks two things against it:

```python
root[PROP_GAPS_OMITTED] == root[PROP_GAPS_REQUESTED]
len(endo_cell_instances) == root[PROP_CELLS_REQUESTED] - root[PROP_GAPS_REQUESTED]
```

That needs a third root property, `PROP_CELLS_REQUESTED = "cells_requested"`, written in Task 5 Step 5 when `endothelium_cells` is accepted. Without it the validator would have to recount the builder's input by inferring it, which is the same "measure it two different ways" mistake this module already made once.

Assert the second form, not `omitted + placed == requested`, because the first is what actually catches the failure: if the gap logic silently stops omitting, `placed` rises and the count no longer matches `requested - gaps`. Record both numbers and the placed count in `measurements["gaps"]`.

**Clot check:** for each vessel with a clot, get its evaluated centre, then for every lumen occupant compute `_distance_to_path(occupant_centre, stations_world)` minus the distance from the centreline to the clot centre, minus both extents. Assert the result is positive. Record the worst in `measurements["clot"]["min_centre_gap_um"]`.

This is the check that Review Focus item 5 asks for, and it is the only thing standing between a wall-attached clot and a clot growing into the blood.

- [ ] **Step 8: Add the `venule_leaky` test case**

Append to the `cases` list in `build_test_vessel`: `("venule_leaky", VENULE_RADIUS_UM, 20, 3)` with `gap_count=4` passed through. The three existing cases keep their current parameters unchanged, so a regression in the healthy vessel still fails.

Widen `TEST_RADIUS_CM` spacing if the new case overlaps the others.

- [ ] **Step 9: Run the full suite**

Run: `blender --background --python blender/scripts/asset_blood_vessel.py`
Expected: `VALIDATION PASSED`, with `gaps`, `clot` and `pinned` in the printed measurements. Add those three keys to the tuple in `main()`'s print loop so they are actually visible.

- [ ] **Step 10: Commit**

```bash
git add blender/scripts/asset_blood_vessel.py
git commit -m "Add leak gaps, escaping fibrin and a wall-attached thrombus

Gaps are omitted endothelial cells rather than boolean-cut holes: the
lumen shows through, which is what a leak looks like, and no boolean
runs against the mesh the containment check measures.

A clot is given its own clearance check because the existing one
measures distance from the centreline and cannot see a wall-attached
mass reaching inward. Placement excludes the clot's span so the blood
column keeps both its count and its spread."
```

---

### Task 7: Margination and extravasation

**Files:**
- Modify: `blender/scripts/asset_blood_vessel.py:973` (`create_blood_vessel`), `:1282` (`validate_blood_vessel`)

**Interfaces:**
- Consumes: `PROP_ROLE`, `_place_component(..., radial_fraction=...)` from Task 6
- Produces: `DEFAULT_WBC_MARGIN_FRAC = 0.5`; `create_blood_vessel(..., wbc_margin_frac=DEFAULT_WBC_MARGIN_FRAC)`; `measurements["roles"]`

- [ ] **Step 1: Write the failing test**

```python
def _selftest_roles() -> dict:
    report = validate_blood_vessel()
    roles = report["measurements"]["roles"]
    # A vessel with enough white cells has all three behaviours present. With
    # fewer than three, no role is asserted -- there is not enough to split.
    for root_name, entry in roles.items():
        if entry["wbc_count"] >= 3:
            assert entry["MARGINATED"] > 0, (root_name, entry)
            assert entry["FLOW"] > 0, (root_name, entry)
        if entry["wbc_count"] == 0:
            assert entry["FLOW"] == 0 and entry["MARGINATED"] == 0
    return report
```

- [ ] **Step 2: Run it to verify it fails**

Run the Task 6 Step 2 command pattern.
Expected: `KeyError: 'roles'`

- [ ] **Step 3: Split `wbc_count` three ways**

`wbc_count` keeps its meaning as the total. `marginated = round(wbc_count * wbc_margin_frac)`, `extravasating = min(marginated, max(0, wbc_count - 2))`, `flowing = wbc_count - marginated - extravasating`. With `wbc_count=0` all three are 0, so a vessel with no white cells still has none.

Place marginated cells with `radial_fraction=1.0` — at the wall, inside the lumen. Place extravasating cells with `radial_fraction` computed as `(endothelium_radius + extent) / clearance`, which is >1 and puts the cell's centre outside the endothelium. Both keep the single `FOLLOW_PATH` constraint.

- [ ] **Step 4: Check the extravasating cells are still in the vessel**

This is Review Focus item 4. An extravasating cell is outside the lumen by design, so the containment loop skips it — which means nothing else would catch one that flew off into space. Add a check: for every cell whose `PROP_ROLE` is `EXTRAVASATING` or `ESCAPING`, its centre must be within `endothelium_radius * 1.6` of the centreline. Beyond that it has left the vessel entirely, which is not what extravasation looks like.

- [ ] **Step 5: Record roles in the measurements**

Add `measurements["roles"]` keyed by root, with a count per role and the vessel's `wbc_count`.

- [ ] **Step 6: Run the full suite**

Run: `blender --background --python blender/scripts/asset_blood_vessel.py`
Expected: `VALIDATION PASSED`, with `roles` in the printed output.

- [ ] **Step 7: Commit**

```bash
git add blender/scripts/asset_blood_vessel.py
git commit -m "Marginate and extravasate white cells

Margination and extravasation are placement, not animation: the cells
keep the same single FOLLOW_PATH constraint and the animation scripts key
offset_factor exactly as they already do. A marginated cell is a
placement at the wall; an extravasating one is placed past the
endothelium.

Cells outside the lumen are checked for still being inside the vessel,
because skipping them for containment removes the only thing that would
catch one that left entirely."
```

---

### Task 8: Documentation and the render check

**Files:**
- Modify: `blender/README.md` (Current status, Assets list), `blender/scripts/asset_blood_vessel.py` (module docstring)

**Interfaces:**
- Consumes: everything
- Produces: no new code. The docstring's "three smooth shells" claim and the "not a layered tissue with pericytes, basement membrane and smooth muscle" disclaimer must both be corrected, because both are now false.

- [ ] **Step 1: Correct the module docstring**

The docstring currently says the wall is "three smooth shells, not a layered tissue with pericytes, basement membrane and smooth muscle, and the lumen is empty space rather than plasma." All three clauses are now false. Rewrite that paragraph to describe what is actually built, and keep the illustrative-model disclaimer — that part is still true and still required.

- [ ] **Step 2: Update the README**

Add `asset_blood_vessel.py`'s new contents to the assets paragraph, and add a lessons line recording the alpha-budget finding once the render has confirmed or refuted it. Do not write the lesson before the render — the whole point of the project's rule is that it is not written speculatively.

- [ ] **Step 3: Render and inspect — the step that is not optional**

```sh
cd ~/Work/science-fair-virotherapy && blender --background --python-expr "
import sys; sys.path.insert(0,'blender/scripts')
import bpy, asset_blood_vessel as bv
bv.ut.setup_units(); bv.build_test_vessel()
bpy.context.scene.camera = bpy.data.objects['CAMERA_Cell']
bpy.context.scene.render.filepath = '/tmp/vessel_cell.png'
bpy.ops.render.render(write_still=True)
"
```

Look at the image. Check, in this order:

1. **Are the red cells still visible** through seven translucent shells? This is the risk the spec named, and a green validator cannot see it.
2. Does the wall read as layered tissue, or as one thick tube?
3. Do pericytes and endothelial cells read as discrete objects, or as tint?
4. Do the gaps read as gaps?
5. Does the clot visibly avoid the blood column?

- [ ] **Step 4: Fix legibility with alpha, not by deleting layers**

If the interior washes out, change `MAT_SMOOTH_MUSCLE` and `MAT_BASEMENT` alpha in `MAT_*` specs and re-render. The layers carry the biology; the alpha carries the legibility. Record the working values in the README lesson.

- [ ] **Step 5: Rebuild the main scene and confirm nothing regressed**

```sh
cd ~/Work/science-fair-virotherapy && blender --background --python blender/scripts/scene_tumor.py -- --selftest
```
Expected: the scene rebuild self-test passes.

- [ ] **Step 6: Commit**

```bash
git add blender/README.md blender/scripts/asset_blood_vessel.py
git commit -m "Document the vessel internals and record the alpha budget

Corrects the module docstring, which claimed a three-shell wall with an
empty lumen. The illustrative-model disclaimer stays: this is still a
conceptual tumour vasculature, not an anatomically accurate vessel."
```

---

## Out of Scope

Not in this plan, and not to be added while implementing it:

- `asset_ecm.py` and the six `animation_*.py` modules. They stay API-contract-only.
- A branching vessel *network*. This plan makes `05_BLOOD_VESSELS` able to hold the new content; populating it is separate work.
- Moving vessels into the main scene. `05_BLOOD_VESSELS` stays empty in `virotherapy_main.blend` until something calls `create_blood_vessel` with it.
- Alpha keyframes. The new shells are static.
- Splitting the module. See File Structure.
