# Blender project — oncolytic virotherapy visualization

Blender source-of-truth for the science-fair visualization. The model is a
**simplified educational** model inspired by oncolytic virotherapy, with
**T-VEC / talimogene laherparepvec** as the real-world reference.

It is **not** a molecular simulation and **not** a clinical simulator. Nothing
here is a quantitative model of infection kinetics, viral load, or patient
outcome. Every visual is chosen for scientific plausibility and clear
communication first, and reuse and animation-readiness second.

---

## Quick start

Rebuild the entire scene scaffold from source:

```sh
blender --background --python blender/scripts/scene_tumor.py -- --save
```

Check that a rebuild is genuinely idempotent (see [Rebuild safety](#rebuild-safety)):

```sh
blender --background --python blender/scripts/scene_tumor.py -- --selftest
```

Work interactively instead — start Blender, then in the Scripting workspace:

```python
import sys; sys.path.insert(0, "/path/to/science-fair-virotherapy/blender/scripts")
import scene_tumor; scene_tumor.build()
```

---

## Current status

**Scaffold only — no biological assets yet, by design.**

Built and verified: 11 collections, 5 controllers, 4 cameras, 3 lights, world
ambient, render settings. The 6 asset scripts and 6 animation scripts exist as
**API contracts only**; their entry points raise `NotImplementedError` with a
note on what belongs there. That is intentional — the signatures are fixed now
so nothing drifts, but no speculative geometry has been written.

---

## Collection structure

Eleven collections, flat under `Scene Collection`. Flat rather than nested on
purpose: glTF export and outliner filtering both behave predictably, and
nesting buys nothing at this size.

| Collection | Holds |
|---|---|
| `00_MASTER` | `CTRL_Master` only. The rig root. |
| `01_TUMOR` | Malignant cells, their nuclei, and `CTRL_Tumor`. |
| `02_HEALTHY_TISSUE` | Normal cells and epithelial sheets. The visual contrast to `01_TUMOR`. |
| `03_VIRUSES` | Virions, and `CTRL_Virus`. |
| `04_IMMUNE_SYSTEM` | T cells, NK cells, dendritic cells, and `CTRL_Immune`. |
| `05_BLOOD_VESSELS` | Capillaries, venules, vessel networks. |
| `06_EXTRACELLULAR_MATRIX` | Collagen fibres, ground substance. |
| `07_EFFECTS` | Particles, bursts, anything transient. Not biological structure. |
| `08_CAMERAS` | The four cameras, and `CTRL_Camera`. |
| `09_LIGHTING` | The three-point light rig. |
| `10_DEBUG` | Scratch. Calibration probes, guides, alignment helpers. Never ship this in an export. |

### Controllers

```
CTRL_Master              (00_MASTER)
├── CTRL_Tumor           (01_TUMOR)
├── CTRL_Virus           (03_VIRUSES)
├── CTRL_Immune          (04_IMMUNE_SYSTEM)
└── CTRL_Camera          (08_CAMERAS)
```

Each domain controller lives **inside the collection it controls**, not all
five in `00_MASTER`, so a collection stays self-contained: duplicating or
exporting `03_VIRUSES` brings `CTRL_Virus` with it. All four are parented to
`CTRL_Master`, so one transform moves the whole rig.

**Animate the controllers, not the meshes.** The web viewer should receive a
small node graph to drive, not hundreds of animated meshes. A mesh that
genuinely needs its own keys — an unzipping envelope, say — is the exception.

---

## Naming convention

Explicit and semantic, always. Never ship `Sphere.001`, `Cube.032`, `Object`,
`Mesh`, or `Material`.

**Objects** — `<SEMANTIC_PREFIX>_<NNN>`, zero-padded to three digits, 1-based:

```
CELL_TUMOR_001
CELL_TUMOR_NUCLEUS_001
CELL_HEALTHY_001
VIRUS_HSV1_001
IMMUNE_TCELL_001
IMMUNE_NK_001
IMMUNE_DENDRITIC_001
VESSEL_001
ECM_FIBER_001
```

Build them with `ut.obj_name("CELL_TUMOR", 1)` rather than string-formatting by
hand, so the padding and the clamp stay consistent.

Other prefixes in use: `CTRL_*` (controllers), `CAMERA_*`, `LIGHT_*`,
`WORLD_*`.

**Materials** — `MAT_<Domain>_<Part>`. Domains are fixed:

```
MAT_Tumor_*        MAT_Healthy_*      MAT_Virus_*
MAT_Immune_*       MAT_Vessel_*       MAT_ECM_*
MAT_Effect_*
```

**The name is the identity key.** Every generated object is fetched by name
before creation, which is what makes rebuilds idempotent. A rename is
therefore a new object, not a rename.

---

## Script structure

```
blender/scripts/
    utilities.py                  # shared foundation — real, working
    scene_tumor.py                # scene builder — real, working

    asset_healthy_cell.py         # CELL_HEALTHY_*
    asset_tumor_cell.py           # CELL_TUMOR_*, CELL_TUMOR_NUCLEUS_*
    asset_virus_hsv1.py           # VIRUS_HSV1_*
    asset_immune_cells.py         # IMMUNE_TCELL_*, IMMUNE_NK_*, IMMUNE_DENDRITIC_*
    asset_blood_vessel.py         # VESSEL_*
    asset_ecm.py                  # ECM_FIBER_*

    animation_virus_entry.py      # frames    1-120
    animation_replication.py      # frames  120-240
    animation_cell_death.py       # frames  240-360
    animation_spread.py           # frames  360-480
    animation_immune_response.py  # frames  480-600
    animation_full_sequence.py    # orchestrates all five + camera cuts
```

Every asset script declares its target `COLLECTION`, its object-name
`PREFIX` or prefixes, and its reference dimensions in micrometres at module
level. Those constants are the contract — read them before implementing.

Each exposes a public `build_*` or `animate_*` function that raises
`NotImplementedError` until written, plus a `main()` for
`blender --background --python <script>`.

`animation_full_sequence.py` owns the scene frame range and the camera cuts.
The individual beats own nothing but their own keyframes, so a single beat can
be re-rendered in isolation without rebuilding the timeline.

### utilities.py

The one module everything routes through, so naming, collection scope, and
instancing behave identically across the project.

| Function | Purpose |
|---|---|
| `obj_name(prefix, index)` | The naming convention, in one place. |
| `get_or_create_object(...)` | **The** idempotent creation path. |
| `get_or_create_collection(...)` | Idempotent collection fetch/create. |
| `link_object(obj, coll)` | Links to exactly one collection, never more. |
| `clear_collection(coll, prefix)` | The only deletion path. Narrow by design. |
| `clear_animation(target)` | Stops a re-run stacking duplicate keyframes. |
| `instance_linked(source, ...)` | Linked duplicate — one mesh, many objects. |
| `instance_grid(...)` | Seeded, reproducible instancing over a grid. |
| `principled_material(...)` | glTF-friendly material creation. |
| `set_parent(obj, parent)` | Parenting that keeps world transform. |
| `purge_orphans()` | Drops zero-user datablocks after a rebuild. |
| `remove_startup_objects()` | Drops the factory Cube/Light/Camera. |
| `setup_units(scene)` | Sets the micrometre convention. |

---

## Rebuild safety

This is the answer to *how do I avoid overwriting existing assets*.

### The scope rule

> **A builder may only create or modify objects inside the collection it
> targets. It never touches another collection, and never deletes anything
> without an explicit name-prefix match.**

Because every builder hard-codes its own target collection, rebuilding
`01_TUMOR` cannot damage `03_VIRUSES`. There is no code path that reaches
across collections.

### Non-destructive by default

`get_or_create_object()` fetches by name before creating, so a re-run
**updates in place** rather than duplicating. This also preserves object
identity, which means keyframes and parent relationships survive a rebuild.

Deletion is opt-in and narrow. `clear_collection(coll, prefix)` removes only
objects whose name starts with `prefix`:

```python
ut.clear_collection(ut.resolve_collection("03_VIRUSES"), prefix="VIRUS_HSV1")
```

Pass the prefix and hand-placed work under a different name is safe. Never call
`clear_collection(coll)` with no prefix in a collection holding real work.

### Instancing, not duplication

Do not hard-code hundreds of duplicated objects. `instance_linked()` creates
objects that **share** one mesh datablock:

```python
first  = ut.instance_linked(source, ut.obj_name("VIRUS_HSV1", 1), coll)
second = ut.instance_linked(source, ut.obj_name("VIRUS_HSV1", 2), coll)
```

`second.data is first.data` — one mesh, two objects.

**The gotcha, and it is the point:** the data is shared, so editing the source
mesh in Edit Mode updates *every* instance at once. To vary one instance, make
it single-user first (`Object > Relations > Make Single User`).

`06_EXTRACELLULAR_MATRIX` is the asset that forces this: a convincing matrix is
hundreds of fibres, and they must stay individually editable, so a merged blob
is not an option.

### Verifying it

`--selftest` builds twice and asserts no `.001` duplicates, no deleted objects,
and no material drift. It guards a bug this project already had:
`bpy.data.objects.new()` is not idempotent, so a re-run used to leave
`LIGHT_Key.001` behind with the stale original still lighting the scene, and
changed settings silently failed to apply.

---

## Scene conventions

### Units — 1 Blender unit = 1 micrometre

`unit_settings.scale_length = 1e-6`, `length_unit = MICROMETERS`. A ~20 µm
tumour cell is ~20 BU, a ~200 nm virion is ~0.2 BU. Blender's UI reports real
biological sizes.

Two consequences, both already handled:

- **Near clip.** Blender's default `clip_start` of 0.1 would clip a virion
  entirely. All cameras use `clip_start = 0.01`.
- **Light power.** Blender attenuates light over raw BU, not scaled BU, so
  wattages are large (`LIGHT_Key` is 3.5e5 W). They were calibrated against a
  cell-sized test sphere at the distances in `scene_tumor.py`. **Re-tune them
  together if you move the lights** — the constants are in one block at the top
  of that file.

### glTF export scale — read this before the web pipeline

glTF is defined in **metres**. This scene is modelled in micrometres, so on
export apply a **`1e-6` scale to the root node**, or a 20 BU cell lands at 20 m
in three.js. Verify this on the first real export rather than assuming the
exporter handles it.

Materials are built from stock Principled BSDF inputs only, with **no
procedural texture nodes** — the glTF exporter cannot carry those across. Add
detail as image textures, not as node setups.

### Render settings

Cycles, AgX view transform, `look = None`, OpenImageDenoise, 1920×1080, 24 fps,
adaptive sampling. Preview 64 samples, final 256.

**AgX is load-bearing, not cosmetic.** It rolls highlights off instead of
clipping them, which is what stops bright emissive virus particles against the
dark background from blowing out into neon. The dark scientific look is partly
AgX and partly restraint in the light rig.

To iterate faster, or to approximate what a WebGL viewer shows:

```python
scene_tumor.set_engine("eevee")
```

Note that Blender's static RNA introspection does not list addon-registered
engines, so the valid `engine` values are not discoverable that way in a
script — `set_engine()` exists partly to paper over that.

### Lighting

Three-point rig plus world ambient:

| Light | Role |
|---|---|
| `LIGHT_Key` | Main. Neutral-cool, front-left, 45°. |
| `LIGHT_Fill` | Dim, blue-shifted, front-right. |
| `LIGHT_Rim` | Behind, accent, for silhouette separation. |
| `WORLD_Master` | Ambient. Dark desaturated blue-grey `0.020, 0.025, 0.035`. |

Ambient is a **World node setup, not a lamp** — the correct way to do global
fill, and it exports far more cleanly than a giant shadow-casting area light.

The background is very dark blue-grey rather than pure black. Pure black reads
as an empty void; the tinted dark reads as depth, which is the microscopy look
this project wants. Avoid neon and cyberpunk styling — it fights the
plausibility goal.

### Cameras

| Camera | Lens | Beat |
|---|---|---|
| `CAMERA_Master` | 35 mm | Establishing, open and close. Scene camera. |
| `CAMERA_Macro` | 50 mm | Spread. Holds several cells plus surrounding matrix. |
| `CAMERA_Cell` | 85 mm | Replication through immune response. |
| `CAMERA_Virus` | 100 mm | Entry and replication, where the virion is 0.2 µm. |

Unkeyed for now. `animation_full_sequence.py` will cut between them with
timeline markers rather than camera keyframes, so the web viewer can seek
without evaluating the camera's fcurves.

---

## Deliberately deferred

- **Volumetric atmosphere.** The single largest cost in this scene, and it was
  conditional on performance anyway. `cycles.volume_bounces = 0` for now. Add
  it via a volume scatter on `WORLD_Master` once there is geometry to justify
  it.
- **All biological assets.** The six `asset_*.py` scripts are contracts only.
- **All animation.** The six `animation_*.py` scripts are contracts only.
- **glTF export.** Not yet wired. Read the export-scale note above first.

## Known gaps

- `00_MASTER` and `00`-numbered collections are created but only `CTRL_Master`
  exists in them; the rest gain contents as assets land.
- No `.blend` is committed to git — the scene is fully regenerable from
  `scene_tumor.py`. See the repository `.gitignore`.
