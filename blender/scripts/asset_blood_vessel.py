"""Blood vessel asset: VESSEL_* and RBC_*.

Scientific reference
--------------------
The delivery route, and the reason the virus can reach a tumour at all: T-VEC
is administered intratumorally in the clinic, but a web visualization will
want to show systemic spread through vasculature, so both are supported here.

* **Capillary lumen** -- 5-10 um diameter, the smallest vessels. A capillary
  is barely wider than a red blood cell, so a virion fits through one.
* **Endothelium** -- the single cell layer lining the vessel. The blood-brain
  and tumour vasculature are typically abnormal here: disorganised, leaky,
  and irregular rather than smooth and evenly walled.
* **Larger vessels** -- venules and arterioles, tens of um, with smooth muscle
  in the wall. Branching is worth showing, since virus and immune cell both
  follow the vascular tree.

**This is a conceptual representation of the tumour-associated vascular
environment, not an anatomically accurate human blood vessel.** Nothing here
models real vessel histology. The wall is three smooth shells, not a layered
tissue with pericytes, basement membrane and smooth muscle, and the lumen is
empty space rather than plasma.

Geometry
--------
Three nested shells, ``VESSEL_OUTER_WALL``, ``VESSEL_ENDOTHELIAL_LAYER`` and
``VESSEL_INNER_LUMEN``, swept along a spline through the caller's path points.
Nested shells, not a solid rod with a separate blood mesh, because the lumen
has to be *genuinely* hollow: anything later shown travelling through the
vessel would otherwise intersect solid geometry and read as clipping through
it. The wall is semi-transparent so the lumen and the red blood cells inside
it are visible from outside.

The trajectory is irregular by construction -- a Catmull-Rom spline through
whatever points the caller supplies, resampled to even arc length, with the
radius perturbed by noise along the length. A mathematically perfect cylinder
would read as plumbing, not as a tumour vessel.

Frames
------
The sweep uses **parallel transport**, not Frenet frames. Frenet's normal is
undefined at an inflection point and flips there, which twists a tube inside
out along a curved path. Parallel transport carries the previous frame's
normal forward by the minimal rotation, so a smooth path gives a smooth tube.

Red blood cells
---------------
Simple biconcave discs, 7.5 um across, spun from a profile. They are
**instanced**, never duplicated: one mesh datablock shared by every RBC, via
:func:`utilities.instance_linked`. A vessel with hundreds of cells in flow
costs one mesh, and the count is a parameter so the animation pass can raise
it without touching the geometry.

Flow support, without animation
--------------------------------
The centreline is kept as a real curve object, ``VESSEL_PATH_NNN``, and every
RBC carries a Follow Path constraint targeting it. That is the whole of the
"supports blood flow" requirement: ``offset_factor`` is a single value to key
per cell to make blood move along the vessel, and ``use_curve_follow`` orients
each disc to the local tangent so it does not slide sideways. There is **no
baked animation here** -- no keyframes, no drivers. Same rule as the rest of
the project: the animation scripts own the keys.

The first RBC is the shared source mesh and is the one exception to the
instancing rule, since there has to be something to instance.

Run standalone::

    blender --background --python blender/scripts/asset_blood_vessel.py
"""

from __future__ import annotations

import math
import os
import random
import sys

import bpy
import bmesh
from mathutils import Matrix, Quaternion, Vector, noise

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utilities as ut  # noqa: E402

# ---------------------------------------------------------------------------
# Contract constants
# ---------------------------------------------------------------------------

COLLECTION = "05_BLOOD_VESSELS"
TEST_COLLECTION = "TEST_BloodVessel"

PREFIX = "VESSEL"
PREFIX_RBC = "RBC_DISC"
PREFIX_PLATELET = "PLATELET"
PREFIX_WBC = "WBC"
PREFIX_PATH = "VESSEL_PATH"

SUFFIX_OUTER_WALL = "OUTER_WALL"
SUFFIX_SMOOTH_MUSCLE = "SMOOTH_MUSCLE"
SUFFIX_BASEMENT = "BASEMENT_MEMBRANE"
SUFFIX_ENDOTHELIAL = "ENDOTHELIAL_LAYER"
SUFFIX_INNER_LUMEN = "INNER_LUMEN"

#: Reference dimensions in micrometres == Blender units here.
#: 5-10 um capillary diameter means a 2.5 um radius.
CAPILLARY_RADIUS_UM = 2.5
#: Venules and arterioles are tens of um. This is the default because a
#: capillary is barely wider than one red blood cell, so a capillary full of
#: cells is a single-file queue and reads as almost empty.
VENULE_RADIUS_UM = 15.0

#: Wall thickness for the endothelial lining.
WALL_THICKNESS_UM = 0.8
#: Thickness of the lumen surface inside the endothelium. Thin: it only needs
#: to be a distinct visible surface, not a modelled layer.
LUMEN_THICKNESS_UM = 0.4

#: Radii below this get no smooth muscle shell at all. A capillary is a
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

#: Radial irregularity, as a fraction of the radius. Visible but not lumpy --
#: a vessel is a tube that wanders, not a potato.
IRREGULARITY_RADIUS = 0.055
#: Spacing between noise samples along the length. Shorter spacing means more
#: of the length varies, which is what makes the calibre read as irregular.
NOISE_SCALE_ALONG = 0.045
NOISE_WEIGHT_FINE = 0.40
NOISE_SCALE_FINE = 0.13

#: Slack allowed on the measured-radius check, as a fraction of the expected
#: radius, derived from the noise that puts the shells off nominal in the first
#: place. A single vertex can sit ``IRREGULARITY_RADIUS * (1 +
#: NOISE_WEIGHT_FINE)`` = 7.7% away from the nominal radius, but
#: :func:`_mean_radius` averages that field over every vertex of the shell --
#: 2304 of them, on a noise whose wavelength is long next to the ring spacing --
#: and the sign changes cancel. Measured on the three test vessels, the mean
#: lands 0.19% to 0.39% out. A quarter of the coarse amplitude sits between the
#: two numbers that matter: 3.5x the worst residual actually observed, so it
#: cannot false-fail, and 0.4 um on the widest test vessel, so a one-micrometre
#: drift is caught with room to spare. A tolerance near zero would fire on the
#: noise; a tolerance near the per-vertex bound would pass a lumen that is a
#: quarter too narrow.
MEASURED_RADIUS_TOLERANCE = IRREGULARITY_RADIUS * 0.25

#: Tube resolution. The tube is the biggest mesh in the scene, so this is
#: deliberately the only place a coarse ring pays off.
STATIONS = 96
RING_SEGMENTS = 24

#: Red blood cell: ~7.5 um diameter, ~2.5 um thick at the rim, and dished on
#: both faces. The dish is what distinguishes a red blood cell from a
#: lentil, and it is why the cell is flexible enough to pass capillaries
#: narrower than itself.
RBC_DIAMETER_UM = 7.5
RBC_HALF_THICKNESS_UM = 1.25
#: How deep the central dimple cuts, as a fraction of the half-thickness.
RBC_DIMPLE = 0.62
RBC_PROFILE_STEPS = 9
#: Raised from 20 because the membrane noise below is finer than a 20-step ring
#: can carry. At 20 steps there is 2*pi*3.75/20 = 1.18 um between neighbouring
#: ring vertices, and undulation with a wavelength under about twice that
#: aliases into a faceted ring rather than reading as a membrane. 32 puts the
#: spacing at 0.74 um, which resolves the wavelength. The cost is 360 -> 576
#: quads on one shared datablock, which is the cheapest place to spend them.
RBC_SPIN_STEPS = 32
#: Membrane relief, as a fraction of the in-plane radius. Deliberately small:
#: a real erythrocyte membrane undulates by tens of nanometres against a 3.75
#: um half-radius, so this is already an exaggeration, and the budget for
#: exaggeration is set by how much of the cell it is allowed to hide -- which
#: at this amplitude is nothing. It is here to give the silhouette a catch
#: light, not to model topology.
RBC_MEMBRANE_NOISE = 0.035
#: Angular frequency of the membrane relief. Low, because the wavelength has to
#: clear the ring spacing set by :data:`RBC_SPIN_STEPS`; high enough that the
#: disc is not merely a circle of revolution, which is the failure mode that
#: makes a biconcave cell read as a lentil.
RBC_MEMBRANE_FREQUENCY = 3.0

#: How many red blood cells a default vessel carries. Low enough to stay
#: cheap, high enough that the lumen reads as full of blood rather than as an
#: empty pipe.
RBC_COUNT = 15
#: Keep cells off the wall: a fraction of the room actually available between
#: the lumen surface and the outermost a cell can reach.
RBC_WALL_CLEARANCE = 0.72
#: A blood component is a finite object, not a point, and it is randomly
#: tilted, so its outer edge reaches further than its own radius. Without this
#: margin a tilted disc pokes through the lumen surface -- the exact artefact
#: the hollow build exists to avoid.
RBC_CLEARANCE_MARGIN = 1.35

#: Tolerance when comparing a cell's *measured* centreline distance against the
#: radius it was *placed* at. These differ by a fraction of a micrometre
#: because Follow Path applies the offset in the curve's own frame, whose roll
#: is Blender's to choose and is not the sweep's parallel-transport frame. The
#: absolute containment check -- no cell reaching the wall -- stays strict; this
#: only relaxes the self-consistency check, and the residual is bounded.
RBC_FRAME_ROLL_TOLERANCE_UM = 0.5

# ---------------------------------------------------------------------------
# Platelets
# ---------------------------------------------------------------------------

#: ~2-3 um across, versus 7.5 for a red blood cell, so they are a third of the
#: width and are easy to lose in a crowded lumen.
PLATELET_DIAMETER_UM = 2.5
#: A platelet is a cell *fragment*, not a cell: no nucleus, and biconvex rather
#: than biconcave, which is why the two disc profiles differ only in which of
#: centre/rim is thicker. See :func:`_disc_mesh`.
PLATELET_CENTRE_THICKNESS_UM = 0.55
PLATELET_RIM_THICKNESS_UM = 0.18
#: Fewer profile points than a red blood cell. At a third the diameter, with
#: the same count, the silhouette turns faceted.
PLATELET_PROFILE_STEPS = 6
PLATELET_SPIN_STEPS = 12

# ---------------------------------------------------------------------------
# White blood cells
# ---------------------------------------------------------------------------

#: Circulating white cells, as (prefix, builder name, scale) triples. These are
#: the project's existing immune cell meshes rather than new geometry: a
#: lymphocyte and a monocyte are real blood white cells, and reusing them means
#: the cells in the vessel are the same cells the animation shows in tissue.
#:
#: The monocyte is scaled down because :mod:`asset_immune_cells` models a
#: *tissue* macrophage at ~12.9 um radius, which is larger than a circulating
#: monocyte (7-10 um) and would not fit a venule at all. The lymphocyte is
#: used as built.
WBC_CLASSES = (
    ("LYMPHOCYTE", "create_tcell", 1.0),
    ("MONOCYTE", "create_macrophage", 0.60),
)

#: Every ``vessel_class`` value a blood component instance can carry. Sources
#: and instances both use these, which is what lets the validator look up a
#: class's extent without matching on object names.
BLOOD_CLASSES = ("RED_BLOOD_CELL", "PLATELET") + tuple(
    label for label, _, _ in WBC_CLASSES)

#: How many white cells a default vessel carries. Real blood has roughly one
#: per 500 red cells; at that ratio a viewer sees red cells and nothing else.
#: See :data:`PLATELETS_PER_RBC` for the same trade-off on platelets.
WBC_COUNT = 2

# ---------------------------------------------------------------------------
# Component ratios -- deliberately exaggerated
# ---------------------------------------------------------------------------

#: One platelet per this many red blood cells. Real blood is nearer 1:10 to
#: 1:20, which at any visible count of red cells is one or two platelets
#: scrolling past. :data:`PLATELETS_PER_RBC` is a visualisation choice, not a
#: claim about haematology, and the docstring says so.
PLATELETS_PER_RBC = 6
#: Floor on platelets per vessel, so that a small vessel still shows the
#: component at all rather than dropping to zero by arithmetic.
MIN_PLATELETS = 3
#: Sizes are jittered per instance for all three components, so a lumen is not
#: a grid of identical objects.
COMPONENT_SIZE_JITTER = 0.12

#: Scratch collection the immune cells are built in while their parts are merged
#: into a white cell source mesh. Removed again immediately.
SCRATCH_COLLECTION = "_SCRATCH_BloodComponents"

PROP_CLASS = "vessel_class"
PROP_NOTE = "representation_note"
#: True on the one source object per blood component, False on its instances.
#: Lets the validator tell sources from instances without matching on a name,
#: which is how a source gets renamed.
PROP_SOURCE = "blood_source"
#: How much room the source needs in the lumen, in micrometres. Written by the
#: builder from the source's own geometry and read back by the validator, so
#: the two cannot disagree about what fits.
PROP_EXTENT = "blood_extent_um"
#: The radius this vessel was asked for, in micrometres, written by
#: :func:`create_blood_vessel` and read back by the validator. Every containment
#: check in the project is computed against a *measured* radius, so without this
#: the build has nothing to be measured against: the validator can confirm the
#: shells are the right way round and no further.
PROP_REQUESTED_RADIUS = "requested_radius_um"

ILLUSTRATIVE_NOTE = ("Conceptual representation of tumour-associated "
                     "vasculature; not an anatomically accurate vessel.")

# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

#: All three shells are translucent, and the innermost is the most so. This is
#: not a stylistic choice: the brief needs the lumen and the red blood cells
#: inside it to be *visible*, and a closed opaque tube hides them completely.
#: Rendered opaque, the vessel is a featureless dark pipe -- which is what the
#: first version of this asset did, and why a green validator did not mean the
#: model was any good.
#:
#: The lumen has to be translucent too, not just the wall. The cells sit
#: *inside* the lumen radius, so from any viewpoint the near side of the lumen
#: surface is between the camera and the cells; an opaque lumen hides them just
#: as effectively as an opaque wall.
MAT_WALL = dict(
    name="MAT_Vessel_Wall",
    base_color=(0.46, 0.30, 0.29, 1.0),
    roughness=0.62,
    subsurface=0.08,
    alpha=0.24,
    emission=(0.95, 0.70, 0.62, 1.0),
)

#: Warmer and lighter than the wall, so the endothelial lining reads as a
#: separate layer rather than as more wall.
MAT_ENDOTHELIAL = dict(
    name="MAT_Vessel_Endothelium",
    base_color=(0.80, 0.55, 0.50, 1.0),
    roughness=0.42,
    subsurface=0.22,
    alpha=0.16,
    emission=(1.00, 0.78, 0.70, 1.0),
)

#: The blood-filled interior. Deep red and translucent, so it reads as a volume
#: of blood with the cells suspended in it. Alpha is low because this surface
#: is the last thing between the camera and the cells.
MAT_LUMEN = dict(
    name="MAT_Vessel_Lumen",
    base_color=(0.55, 0.05, 0.06, 1.0),
    roughness=0.35,
    subsurface=0.15,
    alpha=0.40,
    emission=(0.85, 0.14, 0.12, 1.0),
)

#: Opaque and bright. Every other surface in the asset is translucent, so an
#: opaque cell is the one thing that stays solid -- which is exactly what
#: makes it read as a cell rather than as a tint.
MAT_RBC = dict(
    name="MAT_RBC_Disc",
    base_color=(0.82, 0.11, 0.13, 1.0),
    roughness=0.30,
    subsurface=0.30,
    alpha=1.0,
    emission=(1.00, 0.20, 0.16, 1.0),
)

#: Platelets are pale and slightly translucent, not red. Colouring them like red
#: blood cells would imply they are the same thing, which is the one thing they
#: are not: they are colourless cell fragments whose real colour comes from
#: whatever they have coated themselves in. Pale grey-lilac is close enough to
#: read correctly against the lumen and different enough to tell apart.
MAT_PLATELET = dict(
    name="MAT_Platelet",
    base_color=(0.78, 0.74, 0.80, 1.0),
    roughness=0.35,
    subsurface=0.25,
    alpha=0.85,
    emission=(0.88, 0.85, 0.95, 1.0),
)

#: The media. Warmer and less saturated than the outer wall it sits inside, so
#: the layer boundary reads as a change of tissue rather than as a second wall
#: of the same colour, and the most transparent of the new shells: the shell
#: loop now puts three of these between the camera and the blood, and the
#: alpha budget only works if each one buys its legibility with less opacity
#: than the surfaces already there.
MAT_SMOOTH_MUSCLE = dict(
    name="MAT_Vessel_SmoothMuscle",
    base_color=(0.72, 0.42, 0.40, 1.0),
    roughness=0.5,
    subsurface=0.15,
    alpha=0.12,
    emission=(0.72, 0.42, 0.40, 1.0),
)

#: The basement membrane, the thinnest surface in the wall and the palest. At
#: :data:`BASEMENT_THICKNESS_UM` there is barely a ring's worth of it to see, so
#: it is tinted towards the endothelial lining immediately outside it and given
#: the lowest alpha of anything built here: a 0.15 um sheet drawn at 0.20 alpha
#: is still a stack of two walls in every frame, and this is the layer whose job
#: is to be *implied* rather than looked at.
MAT_BASEMENT = dict(
    name="MAT_Vessel_BasementMembrane",
    base_color=(0.85, 0.72, 0.68, 1.0),
    roughness=0.5,
    subsurface=0.15,
    alpha=0.10,
    emission=(0.85, 0.72, 0.68, 1.0),
)

#: Pericytes wrap the capillary and post-capillary endothelium from the outside.
#: Opaque, because they are discrete cells rather than tissue, and the same
#: reason red blood cells are: every surface around them is translucent, and an
#: object that fades with its surroundings is an object nobody can count. Cool
#: and desaturated, which is the only way to pick them out of the red column
#: they sit in.
MAT_PERICYTE = dict(
    name="MAT_Vessel_Pericyte",
    base_color=(0.42, 0.46, 0.60, 1.0),
    roughness=0.5,
    subsurface=0.25,
    alpha=0.88,
    emission=(0.42, 0.46, 0.60, 1.0),
)

#: Endothelial cells tiled along the lumen wall. Opaque, and warm rather than
#: the translucent :data:`MAT_ENDOTHELIAL` sheet they sit on: that sheet is the
#: *lumen's* surface, this is the cells drawn on it, and if the two share a
#: material the tiling vanishes into the wall it is supposed to break up.
MAT_ENDO_CELL = dict(
    name="MAT_Vessel_EndothelialCell",
    base_color=(0.88, 0.66, 0.60, 1.0),
    roughness=0.5,
    subsurface=0.25,
    alpha=0.90,
    emission=(0.88, 0.66, 0.60, 1.0),
)

#: Fibrin: the strands a vessel can be laced with, so it is a mesh rather than a
#: tissue and the only surface here at mid alpha. Opaque enough to read as a
#: solid strand, translucent enough that cells caught in the net stay visible
#: through it, which is the entire point of showing fibrin.
MAT_FIBRIN = dict(
    name="MAT_Vessel_Fibrin",
    base_color=(0.90, 0.86, 0.84, 1.0),
    roughness=0.5,
    subsurface=0.15,
    alpha=0.50,
    emission=(0.90, 0.86, 0.84, 1.0),
)

#: A clot is fibrin that has already set, so it keeps fibrin's pale colour turned
#: dark and opaque. The resemblance is the message: same protein, later state.
MAT_CLOT = dict(
    name="MAT_Vessel_FibrinClot",
    base_color=(0.55, 0.10, 0.10, 1.0),
    roughness=0.5,
    subsurface=0.15,
    alpha=0.75,
    emission=(0.55, 0.10, 0.10, 1.0),
)


def _material(spec: dict):
    """Build one :data:`MAT_*` material. Emission strength starts at zero."""
    return ut.principled_material(
        spec["name"], base_color=spec["base_color"], roughness=spec["roughness"],
        alpha=spec["alpha"], emission=spec["emission"], emission_strength=0.0,
        subsurface=spec.get("subsurface", 0.0),
    )


def ensure_materials() -> dict:
    """Create or update this module's own materials. Idempotent.

    Deliberately does *not* cover white blood cells: those carry the immune
    cell asset's own materials across from their source meshes, so a white cell
    in a vessel looks exactly like the same cell in tissue.
    """
    return {key: _material(spec) for key, spec in (
        ("outer", MAT_WALL), ("muscle", MAT_SMOOTH_MUSCLE),
        ("endothelium", MAT_ENDOTHELIAL), ("basement", MAT_BASEMENT),
        ("lumen", MAT_LUMEN), ("rbc", MAT_RBC), ("platelet", MAT_PLATELET),
        ("pericyte", MAT_PERICYTE), ("endo_cell", MAT_ENDO_CELL),
        ("fibrin", MAT_FIBRIN), ("clot", MAT_CLOT))}


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


# ---------------------------------------------------------------------------
# Wall layer arithmetic
# ---------------------------------------------------------------------------

#: The shells of the vessel wall, outermost first. The builder sweeps them in
#: this order and the validator nests them in this order, so a layer's position
#: in the wall is stated once rather than twice.
#:
#: The adventitia is deliberately absent from this tuple. It is the layer that
#: absorbs whatever radial room the new tissue needs, so its thickness is a
#: *consequence* of the other three rather than a decision, and sweeping it
#: would double-count a thickness already accounted for. What it measures is
#: still worth knowing -- that is what :func:`wall_radii` returns it for.
WALL_SHELL_ORDER = ("outer", "muscle", "basement", "endothelium", "lumen")
#: Object-name suffix per shell key. The validator matches shells by suffix, so
#: these strings are the contract between the builder and the validator.
WALL_SHELL_SUFFIXES = {"outer": SUFFIX_OUTER_WALL, "muscle": SUFFIX_SMOOTH_MUSCLE,
                       "basement": SUFFIX_BASEMENT,
                       "endothelium": SUFFIX_ENDOTHELIAL,
                       "lumen": SUFFIX_INNER_LUMEN}


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


def _selftest_rbc_membrane() -> dict:
    """Check the red cell's membrane relief. Needs bpy, so it builds meshes.

    Three properties, split deliberately rather than measured as one number.

    **The relief varies with angle.** A surface of revolution perturbed
    symmetrically is still a surface of revolution, so it would cost polygons
    and change nothing a viewer can see -- and that is the entire reason the
    noise is there. Checked on the factory, because that is where the property
    lives and where it is unambiguous.

    **The relief reaches the mesh.** Checked by building the same disc twice,
    once with the factor and once without, and diffing vertex by vertex. The
    two builds have identical topology and identical vertex order, so the diff
    is exact rather than a nearest-neighbour guess. A factor wired up wrongly
    -- applied to the thickness, or dropped from the ``_rbc_mesh`` call --
    moves nothing, and this is the assertion that catches it.

    **The rim does not grow past its declared bound.** This is the one that
    protects the build, because :func:`_extent` takes the *maximum* vertex
    radius and that number feeds :func:`_placement_radius`. A mesh that grew
    would push red cells into the wall while every containment check stayed
    green, since the validator measures the same mesh it checks:
    self-consistent growth is exactly the bug a self-consistent validator
    cannot see. The bound is derived, not guessed -- the multiplier cannot
    exceed ``1 + RBC_MEMBRANE_NOISE``, so the test asserts the construction's
    own ceiling and cannot pass by luck.

    An earlier version of this measured one combined "spread" figure over a
    wide band of near-rim vertices, and it was wrong twice over: the band's own
    profile curvature swamped the relief, so a *symmetric* factor passed, and
    a narrow-band version collapsed to two vertices on some seeds, which made
    the result depend on the seed. Measuring the factory and the mesh
    separately removes the fragile band entirely.
    """
    half = RBC_DIAMETER_UM * 0.5
    nominal = (RBC_HALF_THICKNESS_UM * RBC_DIMPLE, RBC_HALF_THICKNESS_UM)

    factor = _disc_noise(random.Random(0))

    # The relief is angular. Sampled at fixed s, because the s term is the
    # radial one and mixing the two would hide a factor that ignored theta.
    by_angle = [factor(2.0 * math.pi * i / RBC_SPIN_STEPS, 1.0)
                for i in range(RBC_SPIN_STEPS)]
    angular = max(by_angle) - min(by_angle)
    # A quarter of the full possible swing, which is 2x the amplitude. Wide
    # enough that no seed falls below it, far above the 0.0 a symmetric
    # factor produces.
    assert angular > RBC_MEMBRANE_NOISE * 0.25, \
        "membrane relief does not vary with angle: range {:.5f}".format(angular)

    rough = _disc_mesh("MESH_SELFTEST_RBC_RELIEF", RBC_DIAMETER_UM,
                       nominal[0], nominal[1], steps=RBC_PROFILE_STEPS,
                       spin=RBC_SPIN_STEPS, factor=factor)
    smooth = _disc_mesh("MESH_SELFTEST_RBC_SMOOTH", RBC_DIAMETER_UM,
                        nominal[0], nominal[1], steps=RBC_PROFILE_STEPS,
                        spin=RBC_SPIN_STEPS)
    try:
        assert len(rough.vertices) == len(smooth.vertices), \
            "relief changed vertex count: {} vs {}".format(
                len(rough.vertices), len(smooth.vertices))
        moved = [1 if (a.co - b.co).length > 1e-9 else 0
                 for a, b in zip(rough.vertices, smooth.vertices)]
        assert any(moved), "membrane relief never reached the mesh"
        assert max(moved) == 1, \
            "some vertices are shared between the two builds; vertex order " \
            "is not what the diff assumes"
        # The two on-axis poles are where the profile closes, and relief is
        # scaled by s so they are stationary by construction. Located by
        # geometry rather than by index: the spin rewrites vertex order, so
        # they are not first and last. If they move, the radius guard below is
        # not measuring the rim.
        poles = [i for i, v in enumerate(smooth.vertices) if v.co.xy.length <= 1e-9]
        assert len(poles) == 2, \
            "expected 2 on-axis poles, found {}".format(len(poles))
        assert not any(moved[i] for i in poles), \
            "on-axis pole moved; relief is not scaled by distance from centre"

        # Ceiling on the multiplier, applied to the whole mesh rather than to a
        # selected band: the rim is the widest thing on it, so the maximum
        # vertex radius anywhere is the bound.
        widest = max(v.co.xy.length for v in rough.vertices)
        ceiling = half * (1.0 + RBC_MEMBRANE_NOISE)
        assert widest <= ceiling + 1e-6, \
            "mesh reaches {:.4f} um, past the {:.4f} um the multiplier can " \
            "produce".format(widest, ceiling)

        return {"moved_vertices": sum(moved), "verts": len(rough.vertices),
                "angular_range": angular, "max_radius": widest,
                "ceiling": ceiling, "polys": len(rough.polygons)}
    finally:
        bpy.data.meshes.remove(rough)
        bpy.data.meshes.remove(smooth)


def _extent(mesh, margin: float = None) -> float:
    """Half-extent a randomly tilted blood component presents to the lumen.

    Measured as the mesh's *maximum* vertex radius, not its mean. For
    containment the question is "does the furthest point reach the wall", and
    for a disc the furthest point is the rim: a red blood cell reaches 3.75 um
    however much of its surface is dished in towards the middle, so its mean
    vertex radius of 2.39 um understates it by a third. An earlier version used
    the mean, following the cell assets' convention of normalising declared
    size by the mean -- but that convention exists to stop an amoeboid
    *macrophage's lobes* inflating a size claim, and here it quietly let a
    2.5 um platelet through a 1.3 um capillary lumen. Containment wants the
    conservative number, so this takes the maximum.

    The margin on top accounts for tilt: a tilted object reaches past its own
    radius, and something placed exactly one radius from the centreline pokes
    through the wall about half the time.
    """
    margin = RBC_CLEARANCE_MARGIN if margin is None else margin
    if not mesh.vertices:
        return 0.0
    return max(v.co.length for v in mesh.vertices) * margin


def _placement_radius(lumen_radius: float, extent: float) -> float:
    """How far off the centreline a component may sit and stay inside.

    Shared by the builder and the validator on purpose. When these two
    disagreed about what fits, the validator would report a correctly built
    vessel as broken -- which is the same class of bug that made an earlier
    version of this project measure declared radius two different ways.
    """
    return max(lumen_radius - extent, 0.0) * RBC_WALL_CLEARANCE


# ---------------------------------------------------------------------------
# Path: spline, resampling, frames
# ---------------------------------------------------------------------------


def _catmull_rom(points, samples_per_segment: int = 8) -> list:
    """Sample a Catmull-Rom spline through *points*.

    Endpoints are duplicated rather than reflected, which keeps the tube from
    flaring outward at the first and last station -- a reflected control point
    sends the curve backwards off the end of the path.
    """
    pts = [Vector(p) for p in points]
    if len(pts) < 2:
        raise ValueError("path_points needs at least 2 points, got {}".format(len(pts)))
    if len(pts) == 2:
        return [pts[0].lerp(pts[1], i / float(samples_per_segment))
                for i in range(samples_per_segment + 1)]

    ext = [pts[0]] + pts + [pts[-1]]
    out = []
    for i in range(len(pts) - 1):
        p0, p1, p2, p3 = ext[i], ext[i + 1], ext[i + 2], ext[i + 3]
        for s in range(samples_per_segment):
            t = s / float(samples_per_segment)
            t2, t3 = t * t, t * t * t
            out.append(0.5 * ((2.0 * p1) + (-p0 + p2) * t
                              + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
                              + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3))
    out.append(pts[-1])
    return out


def _resample_even(polyline, count: int) -> list:
    """Resample a polyline to *count* points at even arc-length spacing.

    Even spacing is what makes the noise term below read as calibre variation
    rather than as varying patch density: sampling a spline uniformly in *t*
    bunches stations wherever the curve is slow.
    """
    if len(polyline) < 2:
        raise ValueError("polyline needs at least 2 points")

    lengths = [0.0]
    for a, b in zip(polyline, polyline[1:]):
        lengths.append(lengths[-1] + (b - a).length)
    total = lengths[-1]
    if total <= 1e-9:
        raise ValueError("path_points are coincident; vessel has zero length")

    out = []
    for i in range(count):
        target = total * i / float(count - 1)
        # Walk the cumulative table; the path is short so this is cheaper than
        # a bisect and matches the rest of the project's plain-Python style.
        j = 0
        while j < len(lengths) - 2 and lengths[j + 1] < target:
            j += 1
        span = lengths[j + 1] - lengths[j]
        local = 0.0 if span <= 1e-12 else (target - lengths[j]) / span
        out.append(polyline[j].lerp(polyline[j + 1], min(1.0, max(0.0, local))))
    return out


def _parallel_frames(points: list) -> list:
    """Rotation-minimising frames along a polyline, one 4x4 per station.

    Parallel transport, not Frenet. Frenet's normal is undefined at an
    inflection point and flips sign there, which twists a swept tube inside
    out -- a plain S-curve has an inflection in the middle, so this is not a
    hypothetical. Transport carries the previous normal forward by the
    smallest rotation that aligns it with the new tangent, so a smooth path
    yields a smooth tube.
    """
    tangents = []
    for i in range(len(points)):
        if i == 0:
            t = points[1] - points[0]
        elif i == len(points) - 1:
            t = points[-1] - points[-2]
        else:
            t = points[i + 1] - points[i - 1]
        if t.length < 1e-9:
            t = Vector((0.0, 0.0, 1.0))
        tangents.append(t.normalized())

    # Seed the first normal perpendicular to the first tangent. Any vector not
    # parallel to the tangent will do; using world X unless it is degenerate.
    seed = Vector((1.0, 0.0, 0.0))
    if abs(tangents[0].dot(seed)) > 0.9:
        seed = Vector((0.0, 1.0, 0.0))
    normal = (seed - tangents[0] * tangents[0].dot(seed)).normalized()

    frames = []
    for i, tangent in enumerate(tangents):
        prev_tangent = frames[-1][-1].to_3d() if frames else None
        if prev_tangent is not None:
            axis = prev_tangent.cross(tangent)
            if axis.length > 1e-9:
                normal = (Quaternion(axis.normalized(),
                                      prev_tangent.angle(tangent)) @ normal)
        # Re-orthogonalise against drift, then build the frame: local +Z runs
        # along the path so swept rings are easy to reason about.
        normal = (normal - tangent * tangent.dot(normal))
        if normal.length < 1e-9:
            normal = (seed - tangent * tangent.dot(seed))
        normal.normalize()
        binormal = tangent.cross(normal)
        frame = Matrix((
            (normal.x, binormal.x, tangent.x),
            (normal.y, binormal.y, tangent.y),
            (normal.z, binormal.z, tangent.z),
        )).to_4x4()
        # The station's own position. Without this every ring is concentric at
        # the origin and the sweep silently produces a stack of coincident
        # rings instead of a tube: a closed, near-zero-volume mesh that passes
        # a topology check while being entirely the wrong shape.
        frame.translation = points[i]
        frames.append(frame)
    return frames


def _radius_profile(rng):
    """Return ``f(u, theta) -> multiplier`` for the vessel radius.

    ``u`` is 0..1 along the path, ``theta`` is the angle around it. The
    coarse term makes the calibre vary along the length; the fine term stops
    that from reading as a smooth swelling. Both are seeded, so a vessel is
    reproducible.
    """
    coarse_phase = Vector([rng.uniform(-40.0, 40.0) for _ in range(3)])
    fine_phase = Vector([rng.uniform(-40.0, 40.0) for _ in range(3)])

    def factor(u: float, theta: float) -> float:
        coarse = noise.noise(Vector((u * NOISE_SCALE_ALONG, theta * 0.35, 0.0))
                             + coarse_phase)
        fine = noise.noise(Vector((u * NOISE_SCALE_FINE, theta * 0.8, 1.7))
                           + fine_phase)
        return 1.0 + IRREGULARITY_RADIUS * (coarse + NOISE_WEIGHT_FINE * fine)

    return factor


# ---------------------------------------------------------------------------
# Mesh construction
# ---------------------------------------------------------------------------


def _tube_mesh(name: str, frames: list, radius: float, factor, *,
               cap_start: bool = True, cap_end: bool = True) -> object:
    """Sweep a closed tube along *frames* at *radius* scaled by ``factor``.

    One station per frame, one ring of :data:`RING_SEGMENTS` around each.
    Caps are optional: the wall's open ends are what show the wall is a tube,
    so they are capped here, but the radius ordering is validated separately
    rather than being trusted.
    """
    bm = bmesh.new()
    stations = len(frames)
    rings = []
    for i, frame in enumerate(frames):
        centre = frame.translation
        u = i / float(stations - 1)
        ring = []
        for j in range(RING_SEGMENTS):
            theta = 2.0 * math.pi * j / RING_SEGMENTS
            local = Vector((math.cos(theta), math.sin(theta), 0.0))
            world = (frame.to_3x3() @ local)
            ring.append(bm.verts.new(centre + world * (radius * factor(u, theta))))
        rings.append(ring)

    for i in range(stations - 1):
        a, b = rings[i], rings[i + 1]
        for j in range(RING_SEGMENTS):
            k = (j + 1) % RING_SEGMENTS
            bm.faces.new((a[j], a[k], b[k], b[j]))

    if cap_start:
        bm.faces.new(list(reversed(rings[0])))
    if cap_end:
        bm.faces.new(rings[-1])

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.shade_smooth()
    return mesh


def _disc_noise(rng):
    """Return ``f(theta, s) -> multiplier`` for a disc's in-plane radius.

    The same shape as :func:`_radius_profile` and for the same reason: one
    seeded closure, so a disc is reproducible from its seed, and the noise
    lives in the geometry rather than in a texture that the validator cannot
    see.

    *theta* is the angle around the spin axis and *s* runs 0 at the face centre
    to 1 at the rim, so the relief is strongest at the rim -- the membrane is
    slack where it is anchored, which is also where the silhouette is read from.

    The multiplier is 1.0 plus a single signed noise octave: one octave is what
    gives an asymmetric wobble, and asymmetry is the entire point. A symmetric
    perturbation of a surface of revolution is still a surface of revolution,
    so it costs polygons and changes nothing a viewer can see.
    """
    phase = Vector([rng.uniform(-40.0, 40.0) for _ in range(3)])

    def factor(theta: float, s: float) -> float:
        # The radius term is what makes the relief wavelength-independent of
        # how far out the vertex sits; without it the dimple floor, where s is
        # near zero, would wobble as hard as the rim.
        n = noise.noise(Vector((math.cos(theta) * RBC_MEMBRANE_FREQUENCY,
                                math.sin(theta) * RBC_MEMBRANE_FREQUENCY,
                                s * 0.8)) + phase)
        return 1.0 + RBC_MEMBRANE_NOISE * s * n

    return factor


def _disc_mesh(name: str, diameter: float, centre_thickness: float,
               rim_thickness: float, steps: int = 9, spin: int = 20,
               factor=None) -> object:
    """A disc of revolution: a blood platelet, or a red blood cell.

    The profile is the cell's cross-section: an open polyline running from the
    centre of one face, out over the rim, and back to the centre of the other
    face. Spinning that about the cell's short axis closes the surface at both
    ends, because each end sits *on* the axis and all its spun copies collapse
    to one point.

    Both faces are in the profile, which is the part that is easy to get wrong:
    a profile running only from rim to centre revolves into an open bowl, not
    a disc.

    Which of the two shapes you get is entirely the relationship between
    *centre_thickness* and *rim_thickness*:

    * rim thicker than centre -> **biconcave**, a red blood cell. The dimple is
      what makes it an RBC rather than a lentil, and it is why the cell
      deforms to squeeze through a capillary narrower than itself -- which the
      test scene checks by building one.
    * centre thicker than rim -> **biconvex**, a lens. That is a platelet.

    Two near-identical spin functions would have been the alternative; this is
    one function and two callers.

    *factor* is an optional ``f(theta, s) -> multiplier`` on the in-plane
    radius, matching :func:`_radius_profile`'s signature. It is deliberately
    not applied to *centre_thickness* or *rim_thickness*: the biconcave
    profile is the recognisable part of a red blood cell, and relief on the
    faces would eat the dimple that distinguishes it from a platelet.

    The disc ends up lying on its side, axis along Y. That is harmless and
    cheaper to fix than to special-case: the discs are randomly rotated anyway.
    """
    half = diameter * 0.5

    # s sweeps 0 at the face centre to 1 at the rim. The order matters: the
    # profile's *ends* have to be the on-axis points, or the revolve leaves a
    # hole at the rim instead of closing there.
    half_profile = []
    for i in range(steps + 1):
        s = i / float(steps)
        # Smoothstep so the dimple and the rim both curve instead of creasing.
        eased = s * s * (3.0 - 2.0 * s)
        half_profile.append((half * eased,
                             centre_thickness + (rim_thickness - centre_thickness) * eased))

    # Centre of the top face, out over the rim, back to the centre of the
    # bottom face. Open, with both endpoints on the axis.
    profile = ([(radius, thickness) for radius, thickness in half_profile]
               + [(radius, -thickness) for radius, thickness in reversed(half_profile)])

    bm = bmesh.new()
    # Profile in the XZ plane: x is distance from the spin axis, z is position
    # along it. Both profile axes must lie in the sweep plane, so the thickness
    # goes on z and the spin is about z. Putting thickness on y instead sweeps
    # a flat ring of radius sqrt(x^2 + y^2) and the result is a zero-thickness
    # disc with no volume.
    verts = [bm.verts.new((radius, 0.0, thickness)) for radius, thickness in profile]
    chain = [bm.edges.new((verts[i], verts[i + 1]))
             for i in range(len(verts) - 1)]
    bmesh.ops.spin(bm, geom=chain + verts, axis=(0.0, 0.0, 1.0),
                   cent=(0.0, 0.0, 0.0), dvec=(0.0, 0.0, 0.0),
                   angle=2.0 * math.pi, steps=spin, use_merge=True)
    # Weld the seam and the two on-axis poles, then dissolve what the welding
    # left behind. Order matters: removing the doubles is what collapses the
    # seam ring and the axis points into single vertices, and *that* is what
    # leaves zero-area faces behind, so dissolving first would miss them.
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
    bmesh.ops.dissolve_degenerate(bm, dist=1e-6, edges=bm.edges)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    # Membrane relief, applied after the spin rather than to the profile.
    # The spin sweeps one profile around a full turn, so a theta-dependent
    # multiplier cannot be baked into the profile -- every spun copy shares the
    # profile's radius. Displacing the spun vertices lets each one see its own
    # angle, which is the whole mechanism.
    if factor is not None:
        for vert in bm.verts:
            co = vert.co
            # The spin axis is Z, so the in-plane radius is the length of the
            # XY component. The on-axis poles have none, and stay put: a
            # zero-length radius would give a zero s and displace nothing.
            in_plane = math.hypot(co.x, co.y)
            if in_plane <= 1e-9:
                continue
            theta = math.atan2(co.y, co.x)
            scale = factor(theta, in_plane / half)
            vert.co.x = co.x * scale
            vert.co.y = co.y * scale

    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.shade_smooth()
    return mesh


def _rbc_mesh(name: str = "MESH_{}".format(PREFIX_RBC),
              seed: int = 0) -> object:
    """A biconcave disc: a red blood cell. See :func:`_disc_mesh`.

    The membrane relief is seeded, so the cell is reproducible. Every
    instance shares this one mesh -- the validator enforces it -- which is why
    the relief is worth having at all: a fixed asymmetric wobble, seen at the
    fifteen different rolls the instances already get, reads as fifteen
    different silhouettes. That is the ceiling instancing puts on per-cell
    variation, and it is the reason this is membrane noise rather than
    per-cell geometry.
    """
    rng = random.Random(seed)
    return _disc_mesh(name, RBC_DIAMETER_UM,
                      RBC_HALF_THICKNESS_UM * RBC_DIMPLE, RBC_HALF_THICKNESS_UM,
                      steps=RBC_PROFILE_STEPS, spin=RBC_SPIN_STEPS,
                      factor=_disc_noise(rng))


def _platelet_mesh(name: str = "MESH_{}".format(PREFIX_PLATELET)) -> object:
    """A biconvex lens: a platelet. See :func:`_disc_mesh`.

    Smaller and flatter than an RBC, and convex rather than concave: platelets
    are cell fragments, not cells, which is why they have no nucleus and why
    the shape is the opposite. The colour is pale rather than red for the same
    reason they are not a red blood cell.
    """
    return _disc_mesh(name, PLATELET_DIAMETER_UM,
                      PLATELET_CENTRE_THICKNESS_UM, PLATELET_RIM_THICKNESS_UM,
                      steps=PLATELET_PROFILE_STEPS, spin=PLATELET_SPIN_STEPS)


def _wbc_mesh(name: str, cell, scale: float) -> object:
    """Flatten an immune cell's parts into one mesh, so a WBC is one object.

    The immune cell asset builds each cell as a root empty with membrane,
    cytoplasm and nucleus underneath. That is the right shape for *tissue*,
    where the parts animate independently -- but a white blood cell travelling
    down a vessel is one object, and instancing needs one datablock. So the
    part meshes are copied into a single bmesh, transformed into place, and
    left alone: the originals are shared datablocks that other cells use, and
    editing them here would change every lymphocyte in the scene.

    Material slots are carried across and remapped rather than flattened. A
    merged cell with a single material loses the translucent membrane over a
    visible nucleus, and a white blood cell that reads as a solid blob is not
    worth the saved object count.

    Args:
        name: Name for the new mesh.
        cell: The immune cell root object.
        scale: Uniform scale. Tissue macrophages are bigger than circulating
            monocytes, so the monocyte uses a scale below 1.
    """
    parts = [p for p in sorted(cell.children_recursive, key=lambda o: o.name)
             if p.type == "MESH"]
    if not parts:
        raise ValueError("no mesh parts under {!r}".format(cell.name))

    materials = []
    material_slots = {}
    bm = bmesh.new()
    for part in parts:
        matrix = part.matrix_local @ Matrix.Diagonal((scale, scale, scale, 1.0))

        slots = []
        for material in part.data.materials:
            if material.name not in material_slots:
                material_slots[material.name] = len(materials)
                materials.append(material)
            slots.append(material_slots[material.name])

        # poly.vertices holds vertex *indices*, so the copy has to be an
        # index-aligned list rather than a dict keyed by vertex object.
        vertices = [bm.verts.new(matrix @ v.co) for v in part.data.vertices]
        for poly in part.data.polygons:
            try:
                face = bm.faces.new([vertices[i] for i in poly.vertices])
            except ValueError:
                continue          # duplicate face, already contributed
            if poly.material_index < len(slots):
                face.material_index = slots[poly.material_index]

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    for material in materials:
        mesh.materials.append(material)
    mesh.shade_smooth()
    return mesh




# ---------------------------------------------------------------------------
# Flow path
# ---------------------------------------------------------------------------


def _path_object(name: str, points: list, collection, parent=None):
    """A real curve object along the centreline, used as the flow path.

    Kept as a curve rather than discarded after the sweep because the red
    blood cells' Follow Path constraints need something to follow. It is
    hidden in renders and in the viewport by default: it is scaffolding, and
    a visible wire through the middle of the vessel would read as geometry.
    """
    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    spline = curve.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for point, co in zip(spline.points, points):
        point.co = (co.x, co.y, co.z, 1.0)

    obj = ut.get_or_create_object(name, collection, lambda n: curve)
    if parent is not None:
        ut.set_parent(obj, parent)
    obj.hide_render = True
    obj.display_type = "WIRE"
    return obj


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _finish_source(obj, collection, location, parent, mesh_name=None):
    """Common tail for every blood-component source object.

    Sources are hidden from render and viewport: their job is to be the
    datablock, not to be seen. And each carries its own ``blood_extent_um``,
    which is the single source of truth for how much room it needs. The
    validator reads that rather than recomputing an extent of its own, so the
    two cannot drift apart -- which is exactly the mistake this module already
    made once, measuring radius two different ways.
    """
    if location is not None:
        obj.location = location
    if parent is not None:
        ut.set_parent(obj, parent)
    if not obj.data.materials:
        obj.data.materials.append(ut.principled_material(
            "MAT_{}_Default".format(obj.name), base_color=(0.8, 0.4, 0.4, 1.0)))
    obj[PROP_SOURCE] = True
    obj[PROP_EXTENT] = round(_extent(obj.data), 4)
    obj.hide_render = True
    obj.hide_viewport = True
    return obj


def create_red_blood_cell(index: int = 1, collection=None,
                          location=(0.0, 0.0, 0.0), parent=None):
    """Create the shared red blood cell: a simple biconcave disc.

    One source object for the whole project, shared by every vessel's cells.
    This is the *source* that everything else instances, which is why it is
    the one red blood cell in the scene that is not an instance -- there has to
    be something to instance.

    Calling this more than once returns the same object rather than piling up
    near-duplicates, which is the same rule every other builder here follows.

    Args:
        index: 1-based ordinal, used for the object name.
        collection: Target collection. Defaults to ``05_BLOOD_VESSELS``.
        location: World-space location. Left unparented by default, so the
            source is not dragged around when a vessel root moves.
        parent: Optional parent object.

    Returns:
        The shared red blood cell object.
    """
    coll = collection or ut.resolve_collection(COLLECTION)
    obj = ut.get_or_create_object(
        ut.obj_name(PREFIX_RBC, index), coll,
        lambda n: _rbc_mesh("MESH_{}".format(n)))
    ut.assign_material(obj, ensure_materials()["rbc"])
    obj[PROP_CLASS] = "RED_BLOOD_CELL"
    return _finish_source(obj, coll, location, parent)


def create_platelet(index: int = 1, collection=None,
                    location=(0.0, 0.0, 0.0), parent=None):
    """Create the shared platelet: a small biconvex lens.

    The third formed element of blood, alongside red cells and white cells.
    A platelet is a cell *fragment* shed from a megakaryocyte rather than a
    cell in its own right, which is why it has no nucleus, is a third of a red
    cell's width, and is biconvex where a red cell is biconcave.

    One shared source for the whole project, as with the red blood cell.

    Args:
        index: 1-based ordinal, used for the object name.
        collection: Target collection. Defaults to ``05_BLOOD_VESSELS``.
        location: World-space location.
        parent: Optional parent object.

    Returns:
        The shared platelet object.
    """
    coll = collection or ut.resolve_collection(COLLECTION)
    obj = ut.get_or_create_object(
        ut.obj_name(PREFIX_PLATELET, index), coll,
        lambda n: _platelet_mesh("MESH_{}".format(n)))
    ut.assign_material(obj, ensure_materials()["platelet"])
    obj[PROP_CLASS] = "PLATELET"
    return _finish_source(obj, coll, location, parent)


def create_white_blood_cells(collection=None, seed: int = 0) -> dict:
    """Create the shared white blood cell sources, one per class in :data:`WBC_CLASSES`.

    These are the project's own immune cell meshes, harvested into single
    instanced meshes. That is a deliberate reuse: a lymphocyte and a monocyte
    are real blood white cells, and it means the cell seen travelling in a
    vessel is the same cell the animation later shows in tissue, rather than a
    second, subtly different model of it.

    The immune cells are built into a scratch collection and removed once their
    parts have been merged. Their part meshes are shared datablocks, so nothing
    is deleted -- only the temporary objects.

    Args:
        collection: Target collection for the sources. Defaults to
            ``05_BLOOD_VESSELS``.
        seed: Passed to the immune cell builders, so the harvested geometry is
            reproducible.

    Returns:
        A dict of class label to source object, e.g. ``{"LYMPHOCYTE": obj}``.
    """
    coll = collection or ut.resolve_collection(COLLECTION)
    import asset_immune_cells as ic

    sources = {}
    for label, builder_name, scale in WBC_CLASSES:
        name = ut.obj_name("{}_{}".format(PREFIX_WBC, label), 1)
        obj = bpy.data.objects.get(name)
        if obj is None:
            scratch = ut.get_or_create_collection(SCRATCH_COLLECTION)
            built = getattr(ic, builder_name)(location=(0.0, 0.0, 0.0),
                                              seed=seed, index=999,
                                              collection=scratch)
            # The immune cell builders return a dict of parts, not the root
            # object, so take the root out of it.
            cell = built["root"]
            mesh = _wbc_mesh("MESH_{}".format(name), cell, scale)
            obj = ut.get_or_create_object(name, coll, lambda n, m=mesh: m)

            # Drop the temporary cell, leaving its shared part meshes alone.
            for victim in [cell] + list(cell.children_recursive):
                bpy.data.objects.remove(victim, do_unlink=True)
            if not scratch.objects and not scratch.children:
                bpy.data.collections.remove(scratch)
        obj[PROP_CLASS] = label
        _finish_source(obj, coll, (0.0, 0.0, 0.0), None)
        sources[label] = obj
    return sources


def _place_component(source, prefix: str, count: int, frames, lumen_radius: float,
                     rng, vessel_class: str, path, root, coll,
                     spread: float = 1.0) -> list:
    """Instance *count* copies of *source* along the vessel, and return them.

    One function for all three blood components. Placement is in the vessel's
    local space: each component is positioned relative to the centreline and
    then constrained onto the path, so moving the vessel root carries the whole
    blood column with it.

    Args:
        source: The shared source object to instance.
        prefix: Name prefix for the instances.
        count: How many to place. Zero is allowed and means "this component
            does not appear in this vessel" -- a capillary carries no red cells
            because none would fit.
        frames: Parallel-transport frames, one per station.
        lumen_radius: Inner radius, used to size the placement allowance.
        rng: Seeded RNG for reproducible jitter.
        vessel_class: Value written to each instance's ``vessel_class``.
        path: The flow path curve.
        root: The vessel root to parent to.
        coll: Owning collection.
        spread: Fraction of the path length this component occupies. Red cells
            fill the vessel; a couple of white cells occupy a stretch of it.

    Returns:
        The list of created objects.
    """
    made = []
    if count <= 0:
        return made

    extent = source[PROP_EXTENT]
    clearance = _placement_radius(lumen_radius, extent)
    # First instance is index 2: index 1 is the shared source.
    for i in range(count):
        # Spread the run along the vessel, then jitter within it.
        base = spread * (i + 0.5) / float(count)
        u = min(0.995, max(0.005, (0.5 - spread * 0.5) + spread * base
                           + rng.uniform(-0.35, 0.35) * spread / count))
        # Deterministic offset within the tube, on a loose spiral so the
        # components do not line up in a single row.
        angle = u * 9.4 * math.pi + rng.uniform(-0.35, 0.35)
        distance = clearance * math.sqrt(rng.uniform(0.05, 1.0))
        radial = Vector((math.cos(angle) * distance,
                         math.sin(angle) * distance, 0.0))
        # Express the offset in the *path's own frame* at this station, not in
        # root space. Follow Path applies the component's location in the
        # curve's frame, so a root-space offset is remapped by the tangent
        # rotation and ends up pointing partly along the vessel -- on a curving
        # path that puts it measurably further from the centreline than it was
        # placed, which is how cells ended up within a hair of the wall.
        station = min(int(round(u * (len(frames) - 1))), len(frames) - 1)
        local = frames[station].to_3x3() @ radial

        size = 1.0 + rng.uniform(-COMPONENT_SIZE_JITTER, COMPONENT_SIZE_JITTER)
        obj = ut.instance_linked(
            source, ut.obj_name(prefix, i + 2), coll, location=local,
            rotation=(rng.uniform(0.0, math.pi), rng.uniform(0.0, math.pi),
                      rng.uniform(0.0, math.pi)),
            scale=(size, size, size), parent=root)
        obj[PROP_SOURCE] = False
        follow = obj.constraints.new("FOLLOW_PATH")
        follow.name = "FLOW"
        follow.target = path
        follow.use_curve_follow = True
        follow.forward_axis = "TRACK_NEGATIVE_Z"
        follow.up_axis = "UP_Y"
        # Required, and easy to miss. With use_fixed_location off, Follow Path
        # ignores offset_factor entirely and uses the frame-based `offset`
        # instead, so every component collapses onto frame 1's position on the
        # path -- the whole blood column piles up at one end of the vessel,
        # while the offset_factor values look perfectly correct in the UI.
        follow.use_fixed_location = True
        # Spread along the path so a keyframe on offset_factor moves each
        # component from a different start point, which is what makes flow read
        # as flow.
        follow.offset_factor = u
        obj[PROP_CLASS] = vessel_class
        made.append(obj)
    return made


def create_blood_vessel(path_points, radius: float = VENULE_RADIUS_UM, *,
                        rbc_count: int = RBC_COUNT, wbc_count: int = WBC_COUNT,
                        platelet_count: int = None, index: int = 1,
                        collection=None, parent=None, seed: int = 0) -> dict:
    """Create a blood vessel along *path_points*, with its blood contents.

    Builds five nested shells and a suspension inside them: red blood cells,
    platelets, and white blood cells. All three components are instanced from
    one shared source mesh each, so a full vessel costs a handful of datablocks
    rather than one per object.

    All five shells are translucent, including the lumen, because the lumen
    surface is the *near* one from any viewpoint: an opaque lumen hides the
    contents just as thoroughly as an opaque wall.

    Nothing is animated. The centreline is kept as a curve and every component
    carries a Follow Path constraint, so blood flow later is a matter of
    keying ``offset_factor`` per object and nothing else.

    **The component ratios are exaggerated.** Real blood is roughly 500 red
    cells per white cell and 10-20 red cells per platelet; at those ratios a
    viewer sees red cells and nothing else. The ratios here are chosen so the
    suspension reads as mixed. This is a visualisation decision and is not a
    claim about haematology.

    Args:
        path_points: Sequence of at least 2 world-space points defining the
            centreline. A gentle S-curve is the default in the test scene.
        radius: Lumen-scale *requested* outer radius in micrometres. Use
            :data:`VENULE_RADIUS_UM` for a readable vessel;
            :data:`CAPILLARY_RADIUS_UM` is faithful but barely wider than one
            red blood cell, and carries no cells at all. The wall layers grow
            *outward* from it, so the built outer shell can sit slightly outside
            this value; the lumen and endothelium never move.
        rbc_count: Number of red blood cells. All share one mesh.
        wbc_count: Number of white blood cells, split across the classes in
            :data:`WBC_CLASSES`.
        platelet_count: Number of platelets. Defaults to ``rbc_count //
            PLATELETS_PER_RBC``, with a floor of :data:`MIN_PLATELETS`.
        index: 1-based ordinal, used for the object names.
        collection: Target collection. Defaults to ``05_BLOOD_VESSELS``.
        parent: Optional parent object.
        seed: Controls all variation. Same seed, same vessel, every time.

    Returns:
        A dict with the shell objects under every key in
        :data:`WALL_SHELL_ORDER`; the flow path under ``"path"``; the vessel
        root under ``"root"``; each component's instances under ``"rbcs"``,
        ``"platelets"`` and ``"wbcs"``; and each component's shared source
        under ``"rbc_source"``, ``"platelet_source"`` and ``"wbc_sources"``.
    """
    if radius <= 0.0:
        raise ValueError("radius must be positive, got {}".format(radius))
    coll = collection or ut.resolve_collection(COLLECTION)
    rng = random.Random(seed)
    materials = ensure_materials()

    if platelet_count is None:
        platelet_count = max(MIN_PLATELETS, rbc_count // PLATELETS_PER_RBC)

    stations = _resample_even(_catmull_rom(path_points), STATIONS)
    frames = _parallel_frames(stations)
    factor = _radius_profile(rng)

    lumen_radius = max(radius - WALL_THICKNESS_UM - LUMEN_THICKNESS_UM, radius * 0.2)
    # The new wall tissue is added outward from the frozen endothelium, so the
    # requested *radius* is no longer a shell radius: the outer surface lands
    # outside it whenever the media is thicker than the adventitia's floor.
    wall = wall_radii(radius, lumen_radius, radius - WALL_THICKNESS_UM)
    radii = dict(wall, endothelium=radius - WALL_THICKNESS_UM, lumen=lumen_radius)

    root = ut.new_empty(ut.obj_name(PREFIX, index), coll, size=radius * 0.5,
                        parent=parent)
    root[PROP_CLASS] = "BLOOD_VESSEL"
    root[PROP_NOTE] = ILLUSTRATIVE_NOTE
    root[PROP_REQUESTED_RADIUS] = round(radius, 4)

    shells = {}
    for key in WALL_SHELL_ORDER:
        # A None radius means the layer is anatomically absent, not zero-thick,
        # so there is no shell to build: a capillary is endothelium and nothing
        # else. See :func:`muscle_thickness`.
        if radii[key] is None:
            continue
        obj_name = ut.obj_name(
            "{}_{}".format(PREFIX, WALL_SHELL_SUFFIXES[key]), index)
        mesh = _tube_mesh("MESH_{}".format(obj_name), frames, radii[key], factor)
        shell = ut.get_or_create_object(obj_name, coll, lambda n, m=mesh: m)
        ut.set_parent(shell, root)
        ut.assign_material(shell, materials[key])
        shells[key] = shell

    path = _path_object(ut.obj_name(PREFIX_PATH, index), stations, coll,
                        parent=root)

    # --- the blood ---------------------------------------------------------
    # Sources are shared across every vessel in the project, so only the
    # instance *names* are per-vessel.
    rbc_source = create_red_blood_cell(index=1, collection=coll)
    platelet_source = create_platelet(index=1, collection=coll)
    wbc_sources = create_white_blood_cells(collection=coll, seed=seed)

    # A component wider than the lumen cannot be placed honestly, so a vessel
    # too narrow for one carries none of that component. This is a real
    # constraint, not a skipped check: a capillary is narrower than a red cell,
    # and cells only pass by deforming. Decided *before* placing, because
    # placing and then discarding leaves the objects in the collection, where
    # they still count as instances.
    def fits(source) -> bool:
        return lumen_radius > _extent(source.data)

    rbcs = _place_component(
        rbc_source, ut.obj_name(PREFIX_RBC, index),
        rbc_count if fits(rbc_source) else 0,
        frames, lumen_radius, rng, "RED_BLOOD_CELL", path, root, coll)

    platelets = _place_component(
        platelet_source, ut.obj_name(PREFIX_PLATELET, index),
        platelet_count if fits(platelet_source) else 0,
        frames, lumen_radius, rng, "PLATELET", path, root, coll, spread=0.8)

    # White cells alternate between the classes, so wbc_count=2 gives one of
    # each rather than two lymphocytes.
    wbcs = []
    labels = list(wbc_sources)
    for i, label in enumerate(labels):
        share = wbc_count // len(labels) + (1 if i < wbc_count % len(labels) else 0)
        wbcs.extend(_place_component(
            wbc_sources[label], ut.obj_name("{}_{}".format(PREFIX_WBC, label), index),
            share if fits(wbc_sources[label]) else 0,
            frames, lumen_radius, rng, label, path, root, coll, spread=0.5))

    return {"root": root, "path": path, "rbcs": rbcs, "platelets": platelets,
            "wbcs": wbcs, "rbc_source": rbc_source,
            "platelet_source": platelet_source, "wbc_sources": wbc_sources,
            **shells}


def default_path(radius: float = VENULE_RADIUS_UM, length_um: float = 420.0) -> list:
    """A gentle S-curve, used by the test build and as a sensible default.

    A straight path would make the parallel-transport sweep look like it was
    never exercised, and a vessel is not straight.
    """
    half = length_um * 0.5
    bend = radius * 2.2
    return [(-half, 0.0, 0.0),
            (-half * 0.5, bend, bend * 0.35),
            (0.0, 0.0, 0.0),
            (half * 0.5, -bend, -bend * 0.35),
            (half, 0.0, 0.0)]


# ---------------------------------------------------------------------------
# Test collection
# ---------------------------------------------------------------------------


TEST_PER_VESSEL = 1
TEST_RADIUS_CM = 40.0


def build_test_vessel(collection=None, per_vessel: int = TEST_PER_VESSEL,
                      clear: bool = True) -> dict:
    """Build vessels covering every radius class, for inspection.

    Returns:
        The dict from the last :func:`create_blood_vessel` call, plus a
        ``"built"`` list of every vessel dict.
    """
    coll = collection or ut.resolve_collection(TEST_COLLECTION)
    if clear:
        for prefix in (PREFIX, PREFIX_RBC, PREFIX_PLATELET, PREFIX_WBC, PREFIX_PATH):
            ut.clear_collection(coll, prefix)

    # A capillary gets no blood at all, and that is not a shortcut: its lumen
    # is ~1.3 um and a red blood cell is 7.5 um across. Cells deform to squeeze
    # through, which a rigid disc cannot show, so a cell in a capillary would be
    # geometry poking through the wall. The validator asserts the absence rather
    # than ignoring it.
    #
    # The wide vessel is the one that exercises a monocyte comfortably: at
    # 30 um radius the lumen is far wider than a white cell, where in a 15 um
    # venule the monocyte very nearly fills it.
    cases = [
        ("capillary", CAPILLARY_RADIUS_UM, 0, 0),
        ("venule", VENULE_RADIUS_UM, 15, 2),
        ("venule_sparse", VENULE_RADIUS_UM * 2.0, 40, 4),
    ]

    built = []
    for i, (_label, radius, count, wbc_count) in enumerate(cases):
        offset = Vector((0.0, TEST_RADIUS_CM * i, 0.0))
        path = [Vector(p) + offset for p in default_path(radius)]
        built.append(create_blood_vessel(path, radius, rbc_count=count,
                                         wbc_count=wbc_count, index=i + 1,
                                         collection=coll, seed=7 + i))
    return {"built": built, "last": built[-1]}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _check_topology(obj) -> list:
    problems = []
    mesh = obj.data
    if not mesh.vertices or not mesh.polygons:
        return ["{}: empty mesh".format(obj.name)]
    bm = bmesh.new()
    bm.from_mesh(mesh)
    if any(not v.link_faces for v in bm.verts):
        problems.append("{}: has loose verts".format(obj.name))
    if any(f.calc_area() < 1e-12 for f in bm.faces):
        problems.append("{}: has degenerate faces".format(obj.name))
    # A tube's *shell* has positive signed volume only if its normals face out.
    if bm.calc_volume(signed=True) <= 0.0:
        problems.append("{}: normals point inward or volume is zero".format(obj.name))
    bm.free()
    return problems


def _path_stations(path_obj) -> list:
    """The centreline as a list of points in the vessel's own local space.

    Same frame as the shell meshes, which are parented to the same root and
    carry their geometry in local coordinates. Comparing the two therefore
    needs no matrix work at all.
    """
    spline = path_obj.data.splines[0]
    return [Vector(p.co[:3]) for p in spline.points]


def _point_segment_distance(point, a, b) -> float:
    """Distance from *point* to the segment *a*-*b*."""
    ab = b - a
    denom = ab.length_squared
    if denom < 1e-18:
        return (point - a).length
    t = (point - a).dot(ab) / denom
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return (point - (a + ab * t)).length


def _distance_to_path(point, stations, coarse: int = 4) -> float:
    """True perpendicular distance from *point* to the centreline polyline.

    Distance to the nearest *station* over-estimates, by up to half a station
    spacing, because the nearest station is rarely at the same point along the
    path. That bias is negligible on a 30 um vessel and enormous on a 2.5 um
    capillary: with 4.4 um between stations, a 2.5 um capillary measured 3.6 um
    across, and the validator then concluded it had room for a red blood cell
    that cannot fit in it. Distance to the *segment* has no such bias.

    Coarse scan for the neighbourhood, then an exact local pass. A tube is
    locally straight, so only segments near the nearest station can win.
    """
    count = len(stations)
    step = max(1, count // 32)
    best_i, best_d = 0, 1e18
    for i in range(0, count, step):
        d = (point - stations[i]).length_squared
        if d < best_d:
            best_i, best_d = i, d
    lo = max(0, best_i - step)
    hi = min(count - 1, best_i + step)
    return min(_point_segment_distance(point, stations[i], stations[i + 1])
               for i in range(lo, hi))


def _mean_radius(obj, stations) -> float:
    """Mean perpendicular distance from a shell's vertices to the centreline.

    Distance to the *path*, not to the object origin. A vessel is far longer
    than it is wide, so distance from the origin measures mostly path length:
    an earlier version of this validator did that and confidently reported a
    1.3 um capillary as 106 um across, which made every containment check
    below it pass without ever being tested.
    """
    if not obj.data.vertices or not stations:
        return 0.0
    return sum(_distance_to_path(v.co, stations) for v in obj.data.vertices) \
        / len(obj.data.vertices)


def _nearest_station(point, stations, coarse: int = 4):
    """Index of the centreline station nearest *point*, and its distance."""
    count = len(stations)
    step = max(1, count // 32)
    best_i, best_d = 0, 1e18
    for i in range(0, count, step):
        d = (point - stations[i]).length_squared
        if d < best_d:
            best_i, best_d = i, d
    return best_i, best_d ** 0.5


def _evaluated_positions(objects) -> dict:
    """World positions of *objects* with their constraints evaluated.

    Not optional. Reading ``object.location`` reports where the builder said to
    put the cell, not where the cell ends up: a Follow Path constraint
    overrides the location entirely, and a misconfigured one left the entire
    blood column stacked at one end of the vessel while every location value
    looked correct.
    """
    depsgraph = bpy.context.evaluated_depsgraph_get()
    return {o.name: o.evaluated_get(depsgraph).matrix_world.translation.copy()
            for o in objects}


def _path_of(root, coll):
    """The flow path curve belonging to *root*, or None."""
    return next((o for o in coll.objects
                 if o.type == "CURVE" and o.parent == root), None)


def validate_blood_vessel(collection=None) -> dict:
    """Validate the generated vessels.

    The checks that matter here are the ones the brief is actually about: the
    lumen is genuinely hollow, the red blood cells are inside it, and the
    red blood cells really are instanced rather than duplicated.
    """
    coll = collection or ut.resolve_collection(TEST_COLLECTION)
    failures = []
    measurements = {"radius": {}, "clearance": {}, "spread": {},
                    "per_class": {}}

    roots = [o for o in coll.objects
             if o.type == "EMPTY" and o.get(PROP_CLASS) == "BLOOD_VESSEL"]
    shells = [o for o in coll.objects if o.type == "MESH"
              and any(s in o.name for s in WALL_SHELL_SUFFIXES.values())]
    # Instances only. Sources are excluded by their own property, not by name,
    # so a renamed source cannot quietly become an instance of itself.
    components = [o for o in coll.objects if o.type == "MESH"
                  and o.get(PROP_CLASS) in BLOOD_CLASSES and not o.get(PROP_SOURCE)]
    sources = [o for o in coll.objects if o.get(PROP_SOURCE)]
    extents = {s.name: s[PROP_EXTENT] for s in sources if PROP_EXTENT in s}
    #: Per-class extent, from the sources. The single source of truth for how
    #: much room each blood component needs.
    class_extent = {s[PROP_CLASS]: s[PROP_EXTENT] for s in sources
                    if PROP_CLASS in s and PROP_EXTENT in s}

    if not roots:
        return {"ok": False, "failures": ["no vessels built"],
                "checks": {}, "measurements": measurements}

    for obj in shells + components + sources:
        if obj.type == "MESH":
            failures.extend(_check_topology(obj))

    # --- one shared mesh per component, not one per object ------------------
    for label in BLOOD_CLASSES:
        instances = [o for o in components if o.get(PROP_CLASS) == label]
        shared = {o.data.name for o in instances}
        if instances and len(shared) != 1:
            failures.append("{} uses {} mesh datablocks, expected 1: {}".format(
                label, len(shared), sorted(shared)))
        if not instances:
            continue
        origin = instances[0].data.name
        owners = {o.data.name for o in sources}
        if origin not in owners:
            failures.append("{}: instances use {!r}, which is not any source "
                            "mesh".format(label, origin))
        measurements["per_class"][label] = {
            "count": len(instances),
            "meshes": len(shared),
            "extent_um": round(class_extent.get(label, 0.0), 2),
        }
    measurements["source_meshes"] = len({s.data.name for s in sources})

    # --- shell nesting -----------------------------------------------------
    lumen_radii = {}
    for root in roots:
        path = _path_of(root, coll)
        if path is None:
            failures.append("{}: no flow path; the blood has nothing to "
                            "follow".format(root.name))
            continue
        stations = _path_stations(path)

        found = {}
        for obj in coll.objects:
            if obj.type != "MESH" or obj.parent != root:
                continue
            for key, suffix in WALL_SHELL_SUFFIXES.items():
                if suffix in obj.name:
                    found[key] = _mean_radius(obj, stations)
        # The media is optional by design, so only the two ends of the chain and
        # the endothelium are required to be present. Everything that *is* built
        # still has to be strictly inside everything outside it.
        if not {"outer", "endothelium", "lumen"} <= set(found):
            failures.append("{}: missing a shell layer (found {})".format(
                root.name, sorted(found)))
            continue
        lumen_radii[root.name] = found["lumen"]
        measurements["radius"][root.name] = {k: round(v, 3) for k, v in found.items()}
        chain = [found[key] for key in WALL_SHELL_ORDER if key in found]
        if not all(a > b for a, b in zip(chain, chain[1:])):
            failures.append("{}: shells are not nested {} ({})".format(
                root.name, ">".join(WALL_SHELL_ORDER),
                {k: round(v, 2) for k, v in found.items()}))
        # A capillary has no media and a venule has no excuse for one, so the
        # shell's presence is checked against the radius actually built. This is
        # the wiring check: a builder that silently dropped the muscle layer
        # everywhere would still nest correctly, just too shallowly.
        if ("muscle" in found) != (found["outer"] >= MUSCLE_MIN_RADIUS_UM):
            failures.append(
                "{}: {} a smooth muscle shell for a {:.2f} um outer radius".format(
                    root.name, "has" if "muscle" in found else "is missing",
                    found["outer"]))
        # The two surfaces every later task places geometry against, checked
        # against the radius this vessel was *asked* for. Ordering alone does not
        # catch a drifted build: the shells stay correctly nested, and the
        # containment pass below measures against the measured lumen rather than
        # the requested one, so a lumen a micrometre too narrow is entirely
        # self-consistent and passes every check above. Left unasserted, that
        # drift lands in the radius of every cell, pericyte and clot placed
        # against it for the rest of this project.
        requested = root.get(PROP_REQUESTED_RADIUS)
        if requested is None:
            failures.append("{}: no {!r}, so the built radii cannot be checked "
                            "against the radius they were built to be".format(
                                root.name, PROP_REQUESTED_RADIUS))
            continue
        for key, expected in (("endothelium", requested - WALL_THICKNESS_UM),
                              ("lumen", requested - WALL_THICKNESS_UM
                               - LUMEN_THICKNESS_UM)):
            tolerance = MEASURED_RADIUS_TOLERANCE * expected
            drift = found[key] - expected
            if abs(drift) > tolerance:
                failures.append(
                    "{}: the {} measures {:.2f} um, not the {:.2f} um it was built "
                    "to be ({:+.2f} um out, tolerance {:.2f} um)".format(
                        root.name, key, found[key], expected, drift, tolerance))

    # --- every component is inside the lumen, and spread along it ------------
    for root in roots:
        lumen_r = lumen_radii.get(root.name)
        if lumen_r is None:
            continue
        path = _path_of(root, coll)
        stations_world = [root.matrix_world @ s for s in _path_stations(path)]
        positions = _evaluated_positions(
            [c for c in coll.objects if c.type == "MESH" and c.parent == root
             and c.get(PROP_CLASS) in BLOOD_CLASSES])

        for label in BLOOD_CLASSES:
            cells = [c for c in coll.objects
                     if c.type == "MESH" and c.parent == root
                     and c.get(PROP_CLASS) == label
                     and not c.get(PROP_SOURCE)]
            # A cell wider than the lumen cannot be placed honestly, so a
            # vessel too narrow for one carries none. That is a real
            # constraint, not a skipped check: a capillary is narrower than a
            # red cell, and cells only pass by deforming.
            extent = max((extents.get(o.name, 0.0) for o in cells),
                         default=class_extent.get(label, 0.0))
            if not cells:
                if lumen_r > class_extent.get(label, 0.0):
                    failures.append("{}: no {} in a lumen {} um wide; it has room "
                                    "for them".format(root.name, label,
                                                      round(lumen_r, 1)))
                continue
            if lumen_r <= extent:
                failures.append("{}: a {} does not fit a {} um lumen "
                                "(it needs {} um of room)".format(
                                    root.name, label, round(lumen_r, 1),
                                    round(extent, 1)))
                continue

            indices, worst = [], 0.0
            for cell in cells:
                point = positions[cell.name]
                indices.append(_nearest_station(point, stations_world)[0])
                worst = max(worst, _distance_to_path(point, stations_world) + extent)

            allowed = _placement_radius(lumen_r, extent)
            measurements["clearance"].setdefault(root.name, {})[label] = round(
                lumen_r - worst, 3)

            if worst > lumen_r + 1e-6:
                failures.append("{}: a {} reaches {:.2f} um from the centreline; "
                                "the lumen is only {} um wide".format(
                                    root.name, label, worst, round(lumen_r, 2)))
            # Compare like with like: *worst* includes the component's own
            # extent, while *allowed* is a centreline distance. Comparing the
            # two against each other flags everything as a breach, because
            # every component is wider than its own placement radius.
            if worst - extent > allowed + RBC_FRAME_ROLL_TOLERANCE_UM:
                failures.append("{}: a {} sits {:.2f} um off the centreline, past "
                                "the {:.2f} um that leaves clearance to the "
                                "wall".format(root.name, label, worst - extent,
                                              allowed))

            # Red cells must actually be spread along the vessel. This is the
            # check that catches a Follow Path constraint resolving to a single
            # point: the geometry is all correct and every cell is inside the
            # lumen, they are just all in the same place.
            #
            # Measured for red cells only. They are the component that fills
            # the vessel; white cells and platelets are placed in a
            # deliberately short stretch of it and there are too few of them
            # for a spread figure to mean anything.
            if label == "RED_BLOOD_CELL" and len(cells) >= 3:
                spread = ((max(indices) - min(indices))
                          / float(max(1, len(stations_world) - 1)))
                measurements["spread"].setdefault(root.name, {})[label] = round(spread, 3)
                if spread < 0.4:
                    failures.append("{}: {} occupy only {:.0%} of the vessel; they "
                                    "are not spread along it".format(
                                        root.name, label, spread))

    # --- instancing, not duplication ---------------------------------------
    per_vessel = sorted({r.parent.name for r in components if r.parent is not None})
    measurements["components_per_vessel"] = {
        name: sum(1 for r in components if r.parent is not None and r.parent.name == name)
        for name in per_vessel}

    # --- no animation ------------------------------------------------------
    for obj in coll.objects:
        if obj.animation_data and obj.animation_data.action:
            failures.append("{}: has baked animation; the asset must not".format(obj.name))
        for constraint in obj.constraints:
            if constraint.type not in {"FOLLOW_PATH"}:
                failures.append("{}: unexpected {!r} constraint".format(
                    obj.name, constraint.type))

    scene = bpy.context.scene
    if abs(scene.unit_settings.scale_length - ut.SCENE_SCALE_LENGTH) > 1e-12:
        failures.append("scene scale_length is not 1e-6; micrometre convention broken")

    return {
        "ok": not failures,
        "failures": failures,
        "checks": {
            "vessels": len(roots),
            "shells": len(shells),
            "components": len(components),
            "sources": len(sources),
            "materials": sorted({m.name for o in shells + components
                                 for m in o.data.materials}),
        },
        "measurements": measurements,
    }


def main() -> None:
    """Check the frozen wall radii, generate the test collection, validate, report."""
    ut.setup_units()
    # Runs before the build, and asserts the one property the validator's
    # ordering check cannot see: that the wall arithmetic is anchored outward on
    # the endothelium and lumen it was handed, and that the outer surface still
    # reaches the requested radius. A wall that is correctly *ordered* but the
    # wrong *size* passes every shell check in :func:`validate_blood_vessel`.
    try:
        _selftest_wall_geometry()
    except AssertionError as exc:
        print("[vessel] FAIL: wall geometry: {}".format(exc))
        raise SystemExit("[vessel] WALL GEOMETRY CHECK FAILED")
    print("[vessel] wall geometry: endothelium and lumen anchored, "
          "wall grows outward")

    # Also before the build, for the same reason as the wall check: the mesh
    # assertions run on a cell the validator has not seen, so a membrane that
    # grew the geometry is caught here rather than being agreed with by the
    # containment check downstream.
    try:
        membrane = _selftest_rbc_membrane()
    except AssertionError as exc:
        print("[vessel] FAIL: rbc membrane: {}".format(exc))
        raise SystemExit("[vessel] RBC MEMBRANE CHECK FAILED")
    print("[vessel] rbc membrane: {} of {} vertices displaced, angular "
          "range {:.4f}, max radius {:.4f} um (ceiling {:.4f})".format(
              membrane["moved_vertices"], membrane["verts"],
              membrane["angular_range"], membrane["max_radius"],
              membrane["ceiling"]))

    build_test_vessel()
    report = validate_blood_vessel()

    print("[vessel] built {} vessels in {}".format(
        report["checks"].get("vessels"), TEST_COLLECTION))
    print("[vessel] checks: {}".format(report["checks"]))
    for key in ("per_class", "source_meshes", "clearance", "spread"):
        if key in report["measurements"]:
            print("[vessel] {}: {}".format(key, report["measurements"][key]))
    if report["ok"]:
        print("[vessel] VALIDATION PASSED")
    else:
        for failure in report["failures"]:
            print("[vessel] FAIL: {}".format(failure))
        raise SystemExit("[vessel] VALIDATION FAILED ({} problems)".format(
            len(report["failures"])))


if __name__ == "__main__":
    main()
