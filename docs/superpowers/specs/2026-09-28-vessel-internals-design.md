# Vessel internals design

Date: 2026-09-28
Module: `blender/scripts/asset_blood_vessel.py`
Collection: `05_BLOOD_VESSELS` (test build in `TEST_BloodVessel`)

## Goal

Fill in what the blood vessel currently leaves out. The asset builds three
smooth nested shells and a suspension of red cells, platelets and white cells.
It is missing the plasma the cells are suspended in, the wall tissue around
the endothelium, and the abnormal features that make a *tumour* vessel
different from a plumbing pipe — which is the actual subject of the
visualisation.

Five additions, all in `asset_blood_vessel.py`:

1. Plasma and fibrin in the lumen.
2. Smooth muscle, basement membrane and pericytes in the wall.
3. Discrete endothelial cells on the lumen surface.
4. Gaps, leaks and a thrombus — the tumour-vasculature story.
5. White cells that marginate and extravasate instead of floating free.

## Constraints carried from the existing module

These are not negotiable; the module's docstring, README and validator all
depend on them.

- **The lumen is genuinely hollow.** Anything travelling through the vessel
  later must not intersect solid geometry. Nested shells, not a solid rod with
  a separate blood mesh.
- **Shells are translucent**, including the lumen, because the lumen surface is
  the *near* surface from any viewpoint. Only discrete cells stay opaque.
- **One Blender unit == 1 micrometre.** `unit_settings.scale_length` is 1e-6.
- **Instancing, never duplication.** One shared mesh datablock per class.
- **No baked animation.** No keyframes, no drivers. The centreline stays a
  real curve and every flowing object carries a `FOLLOW_PATH` constraint. The
  animation scripts own the keys.
- **The name is the identity key.** `ut.get_or_create_object` fetches by name
  before creating, which is what makes rebuilds idempotent.
- **Component ratios are a visualisation choice**, not a claim about
  haematology, and the docstring says so wherever a ratio is set.
- **A builder only touches its own collection** (`utilities.py` scope rule).

## The one structural problem

`BLOOD_CLASSES` currently gates a single check: *is this component inside the
lumen?* Every existing class passes it by construction.

The new classes do not. Pericytes and endothelial cells sit **on the wall**,
not in the lumen, so they would fail the containment check by construction —
the check would be reporting a bug that is actually the design.

So the class list splits, and each half gets the check that is actually true of
it:

```python
LUMEN_CONTENTS = ("RED_BLOOD_CELL", "PLATELET", "LYMPHOCYTE", "MONOCYTE", "FIBRIN")
WALL_BOUND = ("ENDOTHELIAL_CELL", "PERICYTE")
BLOOD_CLASSES = LUMEN_CONTENTS + WALL_BOUND
```

`BLOOD_CLASSES` is kept as the union because the topology, source-mesh and
instancing checks are class-agnostic and must keep covering everything.
Containment is rewritten to run over `LUMEN_CONTENTS` only.

`WALL_BOUND` gets a *pinning* check instead: the cell's centre must sit within
one extent of the named surface radius. Same shape, different constant — the
question changes from "inside the lumen" to "on the endothelium".

Per-object behaviour for the white cells is carried in a new
`vessel_role` property, because a lymphocyte is the same class whether it is
flowing or squeezing out, and the validator needs to know which.

## Geometry

### Layer order

Real vessel, outside in: adventitia → media (smooth muscle) → basement
membrane → endothelium → lumen. So the new layers sit *between* the existing
outer wall and the endothelium, and **the outer wall moves outward** to make
room.

**The lumen and endothelium radii are frozen.** They stay exactly where they
are today:

```
lumen_radius  = radius - WALL_THICKNESS_UM - LUMEN_THICKNESS_UM
endothelium   = radius - WALL_THICKNESS_UM
```

This is the load-bearing decision. Every containment check, every placement
radius and the whole blood column are computed against the lumen radius, and
freezing it means none of that moves. The new tissue is added outward from
there.

New radii, above the endothelium:

```
basement   = endothelium + BASEMENT_THICKNESS_UM
muscle     = basement + muscle_thickness(radius)      # shell absent in a capillary
adventitia = max(ADVENTITIA_MIN_UM, radius - basement - muscle_thickness)
outer      = basement + muscle_thickness + adventitia
```

`muscle_thickness(r)` is **zero or absent below** `MUSCLE_MIN_RADIUS_UM` — a
capillary is a single endothelial layer with scattered pericytes and has no
media, so it gets no muscle shell rather than a zero-thickness one. Above the
threshold it is floored, because an unfloored proportional term is
indistinguishable from zero at the threshold:

```
muscle_thickness(r) = 0                                             if r < 5.0
                    = MUSCLE_MIN_THICKNESS_UM + (r - 5.0) * 0.18   otherwise
```

The floor is not cosmetic. At `r = 5.01` the unfloored term is 0.0018 µm,
which is under a thousandth of a micrometre — far below what a 24-segment
ring resolves, so the validator's geometric nesting check would report a
spurious failure or pass by luck. The floor is the same "a distinct visible
surface, not a modelled layer" rule `LUMEN_THICKNESS_UM` already follows.

Because the three new layers absorb whatever is left of `radius`,
**the outer wall lands exactly on `radius` for capillaries and just outside it
for larger vessels**. A 15 µm venule goes from 15.0 to 16.75 µm outer radius.
The vessel thickens as it should, and the lumen — the thing everything else is
measured against — does not move.

Constants:

| Constant | Value | Why |
|---|---|---|
| `MUSCLE_MIN_RADIUS_UM` | 5.0 | Below this there is no media layer; the vessel is a capillary. |
| `MUSCLE_WALL_FRACTION` | 0.18 | Media growth per µm of radius, above the threshold. |
| `MUSCLE_MIN_THICKNESS_UM` | 0.3 | Floor so the media shell is always a resolvable surface. |
| `BASEMENT_THICKNESS_UM` | 0.15 | A thin sheet. It only has to be a distinct visible surface. |
| `ADVENTITIA_MIN_UM` | 0.3 | Floor so the outermost shell never collapses onto the muscle. |

### Plasma

**No new mesh.** The existing `VESSEL_INNER_LUMEN` shell already *is* the
blood volume — it is a solid translucent rod, not a hollow one, and the cells
sit inside it. A separate plasma object would z-fight it and add a sixth
surface between the camera and the red cells for nothing. `MAT_Vessel_Lumen`
is retitled in the docstring as the plasma medium; the geometry is unchanged.

### Fibrin

Instanced thin strands on a loose spiral inside the lumen, same placement path
as the red cells. One shared strand mesh, `FIBRIN_STRAND_NNN`, class `FIBRIN`,
role `FLOW`.

### Pericytes

Instanced, pinned to the **outer face of the basement membrane** — the
abluminal side, which is where a pericyte actually sits, under the media
rather than in the lumen. Class `PERICYTE`, wall-bound.

Coverage is deliberately sparse and the docstring says why: pericyte
under-coverage is a real feature of tumour vasculature and part of why tumour
vessels leak. Drawing them everywhere would draw a healthy vessel.

### Endothelial cells

Instanced, pinned to the endothelium. They are the surface lining, so at this
camera distance they read as a tiled cobblestone layer rather than as
individual cells. Class `ENDOTHELIAL_CELL`, wall-bound.

**The existing endothelial shell stays exactly as it is.** It is what keeps the
lumen airtight, it is what the validator's nesting check measures, and
replacing it with discrete cells would put the hollow-lumen guarantee at risk
for no visual gain. The cells sit on it.

### Gaps and leaks

A gap is an **omitted endothelial cell**, not a boolean-cut hole. You see
lumen through the gap, which is what a leak looks like, and it costs no
boolean against the mesh the blood containment check measures.

Gaps are chosen from a deterministic lattice: the builder lays out
`cell_count + gap_count` candidate slots and omits the last `gap_count` of
them. That makes the omission checkable — the validator asserts
`len(cell instances) == cell_count`, which fails loudly if the gap logic
silently stops omitting anything.

Fibrin escaping through a gap is a small number of `FIBRIN_STRAND_NNN`
objects placed *outside* the endothelium, class `FIBRIN`, role `ESCAPING`.

### Thrombus

One `FIBRIN_CLOT_001` per vessel, a lumpy mass sitting against the wall with
blood flowing past it — not a plug across the lumen, which would contradict
the blood still being in there.

This is the only addition that can visibly intersect the existing red cells,
because the existing containment check measures distance from the centreline
only, and a wall-attached clot reaches inward. Handled by giving
`_place_component` a `blocked_spans` argument: a candidate whose `u` lands
inside an occupied span is retried, so the cell count and the spread along the
vessel are both preserved and nothing lands in the clot.

New validator check: no lumen occupant's centre is within the clot's extent of
the clot's centre. This is a real check, not a formality — it is the only thing
that would catch a clot growing into the blood column.

### Margination and extravasation

`wbc_count` splits three ways instead of all flowing free:

| Role | Position | Constraint |
|---|---|---|
| `FLOW` | anywhere in the lumen, as today | `FOLLOW_PATH` |
| `MARGINATED` | against the endothelial surface, rolling | `FOLLOW_PATH` |
| `EXTRAVASATING` | past the endothelium, squeezing into tissue | `FOLLOW_PATH` |

All three use the existing single `FOLLOW_PATH` constraint, so the validator's
"no constraint type outside `FOLLOW_PATH`" rule is untouched. Margination and
extravasation are *placement*, not animation: the animation scripts key
`offset_factor` as they already do, and a marginated cell keys the same value
its flowing siblings do.

Implemented as two arguments to `_place_component`, not a second function:

- `radial_fraction` — scales the centreline distance. `1.0` is the wall
  (marginated); greater than 1.0 pushes past the endothelium
  (extravasating). Free-flowing keeps the current `sqrt()`-distributed draw
  scaled by this.
- `blocked_spans` — the clot exclusion above.

Behaviour is carried in `vessel_role`, not in the object name. A lymphocyte is
a lymphocyte whether it is flowing or extravasating, and the project already
puts metadata in properties and only type-and-instance in names.

## Materials

`MAT_Vessel_*` already exists as a domain, so all of these are additive.

| Material | Alpha | Reasoning |
|---|---|---|
| `MAT_Vessel_SmoothMuscle` | 0.12 | Bulk tissue, read as a soft pink haze, not a surface you look through twice. |
| `MAT_Vessel_BasementMembrane` | 0.10 | Thinnest layer, most transparent. It only has to register as a boundary. |
| `MAT_Vessel_Pericyte` | 0.88 | A cell. Opaque, so it reads as an object rather than a tint. |
| `MAT_Vessel_EndothelialCell` | 0.90 | A cell. Same reasoning. |
| `MAT_Vessel_Fibrin` | 0.50 | Pale and translucent — it is a protein mesh, not a solid. |
| `MAT_Vessel_FibrinClot` | 0.75 | Denser than a strand; it is a mass of them. |

**This is the real legibility risk, stated up front.** Seven nested translucent
shells plus a translucent lumen is a lot of surfaces between the camera and the
blood. The README already records that two translucent layers plus AgX rolloff
washed the interior to a featureless pale ball.

The mitigation is that the *new* shells are the most transparent ones (0.10,
0.12) and the new *cells* are the most opaque (0.88, 0.90). Bulk tissue fades
back; discrete objects hold. Whether that is enough is not knowable without
looking at it, so "render it and inspect it" is a required step below, not a
nice-to-have.

## API

`create_blood_vessel` gains keyword-only arguments, all with defaults that
reproduce today's vessel:

```
wall=True, endothelium_cells=24, pericytes=10, fibrin_count=12,
gap_count=3, wbc_margin_frac=0.5
```

Defaults chosen so a caller who knows nothing about the new arguments gets a
vessel with all of it — a plain `create_blood_vessel(path, 15.0)` should be a
vessel, not an empty tube.

`wbc_count` keeps its meaning as the total, split by `wbc_margin_frac`:
`round(wbc_count * wbc_margin_frac)` marginated, the remainder free, and
extravasating cells drawn from the marginated share. `wbc_count=0` gives no
white cells at all, as today.

New public builders, each idempotent, each returning its shared source, matching
the existing `create_red_blood_cell` / `create_platelet` pattern:

- `create_fibrin_strand(index=1, ...)`
- `create_fibrin_clot(index=1, ...)`
- `create_pericyte(index=1, ...)`
- `create_endothelial_cell(index=1, ...)`

All carry `PROP_SOURCE = True` and `PROP_EXTENT` via the existing
`_finish_source`, so the extent the builder used and the extent the validator
checks are the same number by construction — the mistake this module already
made once, measuring radius two different ways.

New private helpers, following the existing `_<verb>` convention:

- `_place_wall_component(source, prefix, count, frames, surface_radius, rng,
  vessel_class, root, coll, spread)` — static placement, **no constraint**.
  Wall cells do not flow.
- `_clot_mesh(name, radius)` — lumpy mass, same noise idiom as `_radius_profile`.

New module constants: `PREFIX_PERICYTE`, `PREFIX_ENDO_CELL`,
`PREFIX_FIBRIN`, `PREFIX_CLOT`, the four `SUFFIX_*` for the new shells, the
dimension constants above, and the material specs.

`build_test_vessel` gains a fourth case so the test collection covers the
pathology rather than only the healthy vessel: a `venule_leaky` at
`VENULE_RADIUS_UM` with `gap_count=4` and a clot. The existing three cases keep
their current parameters, so a regression in the healthy vessel still fails.

## Validation

`validate_blood_vessel` is extended, not replaced. Every existing check stays
and must still pass.

New:

1. **Layer nesting.** Five shells nest `outer > smooth_muscle > basement >
   endothelium > lumen`. Run over the shells that exist, because the muscle
   shell is absent at capillary scale — with a companion assertion that the
   muscle shell is present **iff** `radius >= MUSCLE_MIN_RADIUS_UM`. A fixed
   five-way chain would either fail the capillary or pass a missing layer.
2. **Wall-bound pinning.** Each `ENDOTHELIAL_CELL` / `PERICYTE` centre within
   one extent of its named surface radius, measured through the same
   `_distance_to_path` helper containment uses — distance to the *segment*,
   not the nearest station. The segment version exists because on a 2.5 µm
   capillary the station bias is larger than the vessel.
3. **Gap count.** Endothelial cell instance count equals the requested count,
   so the omission logic cannot silently stop working.
4. **Clot clearance.** No lumen occupant centre within the clot's extent of
   the clot centre.
5. **Role coverage.** With `wbc_count >= 3`, at least one cell of each
   non-zero role exists; with `wbc_count < 3`, no role is asserted. Mirrors the
   existing "a lumen with room for them has none" check.
6. **One mesh datablock per new class** — falls out of the existing instancing
   check once the classes join `BLOOD_CLASSES`.

Rewritten:

- Containment runs over `LUMEN_CONTENTS` only, and skips objects whose
  `vessel_role` is `EXTRAVASATING` or `ESCAPING`, because those are outside the
  lumen by design. They are checked instead by being within the vessel — a
  new check, so a cell that flies off into space is still caught.

All new measurements land in `measurements` under new keys
(`"pinned"`, `"gaps"`, `"clot"`, `"roles"`) alongside the existing
`"clearance"`, `"spread"` and `"per_class"`, so the printed report shows the
new numbers the same way.

## Verification

In order. The render check is not optional and comes last on purpose.

1. `blender --background --python blender/scripts/asset_blood_vessel.py`
   passes. This runs the builder, then `validate_blood_vessel`, and exits
   non-zero on any failure. It is the project's test harness for every asset
   and it is where the new checks run.
2. A rebuild is idempotent: run it twice, confirm no `.001` name accumulation
   and no `bpy.data` growth. The `ut.get_or_create_object` path should make
   this free, but the new sources and the new shells are new call sites, so it
   is worth confirming.
3. Rebuild `virotherapy_main.blend` and confirm `05_BLOOD_VESSELS` accepts the
   new content when a caller passes it, and that `TEST_BloodVessel` still
   rebuilds clean under `10_DEBUG`.
4. **Render the `venule` and `venule_leaky` cases at `CAMERA_Cell` distance
   and look at them.** Check specifically: the red cells are still visible
   through seven translucent shells; the wall reads as layered tissue rather
   than as one thick tube; pericytes and endothelial cells read as discrete
   objects and not as tint; the gaps read as gaps; the clot does not intersect
   the blood column.
5. If the interior washes out, the fix is alpha on the new shells
   (`MAT_Vessel_SmoothMuscle`, `MAT_Vessel_BasementMembrane`), not deleting
   layers. The layers carry the biology; the alpha carries the legibility.

## Out of scope

- `asset_ecm.py` and the six `animation_*.py` modules. They stay
  API-contract-only.
- A vessel *network* — branching, arteriole-into-capillary trees. The README
  lists "vessel networks" under `05_BLOOD_VESSELS`, and nothing builds one
  today. This change makes the collection able to hold the new content when a
  caller asks for it; populating it is separate work.
- Moving the vessels into the main scene. `05_BLOOD_VESSELS` is empty in
  `virotherapy_main.blend` now and stays empty until something calls
  `create_blood_vessel` with it. `build_test_vessel` continues to build into
  `TEST_BloodVessel`.
- Alpha keyframes. The translucent shells are static; the animation scripts
  own anything that moves.

## Risks

| Risk | Mitigation |
|---|---|
| Interior washes out under seven translucent shells. Alpha budgeted low on the new shells, high on the new cells. Verified by render, not by validator. |
| The clot intersects the blood column, and the existing containment check cannot see it because it measures centreline distance only. `blocked_spans` at placement plus a new validator check. |
| The outer wall moving outward breaks the nesting check. `adventitia` absorbs the remainder, so the chain is correct by construction, and the validator re-measures it from geometry rather than trusting the constants. |
| Endothelial cells occlude the blood they are meant to sit around. Kept at low count and pinned to the surface, not the lumen centre. Confirmed by the render check. |
