"""Healthy tissue cell asset: CELL_HEALTHY_001.

Scientific reference
--------------------
A generic mammalian tissue cell. Deliberately *not* one specific human cell
type, and deliberately not a "perfect cell" -- real cells are lumpy, slightly
asymmetric, and their nuclei sit off-centre.

    Healthy vs tumour, the contrast the visualization depends on:
      * regular, well-spread, flat-ish, ordered
      * small regular nucleus, low nuclear-to-cytoplasmic ratio
      * uniform appearance, the opposite of piled-up disorganisation

Healthy cells are functionally non-permissive to T-VEC: the virus is engineered
to divide only in dividing tumour cells, so normal tissue largely escapes both
infection and the lysis that follows it. This asset is the control in that
comparison, so its regularity is doing scientific work, not decoration.

Geometry
--------
Four parts under one root, layered so each is separately visible:

    CELL_HEALTHY_001                  root empty; the transform handle
    +- CELL_HEALTHY_MEMBRANE_001      outer shell, translucent
    +- CELL_HEALTHY_CYTOPLASM_001     inner volume, just inside the membrane
    +- CELL_HEALTHY_NUCLEUS_001       off-centre, distinct colour
       +- CELL_HEALTHY_NUCLEOLUS_001  one or two, optional

The layers are separated by a small deliberate gap rather than being
coincident. Coincident shells z-fight, and a visible gap is what makes the
membrane read as a membrane from across the scene.

Organelles beyond the nucleus are omitted on purpose -- the brief asks for a
simplified interior, and every extra organelle costs readability at the
distances the four cameras actually work at.

Animation readiness
-------------------
* **Floating motion** -- animate the root empty's location. One keyframed
  empty, not five meshes.
* **Membrane deformation** -- each membrane carries a Displace modifier whose
  coordinates are OBJECT-space relative to its own root. All cells share one
  Clouds texture, so moving or scaling a cell shifts its deformation field
  too: per-cell variation out of one texture, and ``strength`` is keyframable
  per cell.
* **Highlighting** -- every material has its emission channel preconfigured
  with the intended highlight colour at zero strength, so a highlight is a
  single keyed value.
* **Transparency** -- the membrane is genuinely translucent, not opaque with
  a low alpha faked in the viewport.
* **Cutaway** -- the layering *is* the cutaway mechanism. Hide or clip
  CELL_HEALTHY_MEMBRANE_001 and the interior is exposed with no extra setup.

Run standalone::

    blender --background --python blender/scripts/asset_healthy_cell.py
"""

from __future__ import annotations

import os
import random
import re
import sys

import bpy
import bmesh
from mathutils import Vector, noise

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utilities as ut  # noqa: E402

# ---------------------------------------------------------------------------
# Contract constants
# ---------------------------------------------------------------------------

COLLECTION = "02_HEALTHY_TISSUE"

PREFIX = "CELL_HEALTHY"
SUFFIX_MEMBRANE = "MEMBRANE"
SUFFIX_CYTOPLASM = "CYTOPLASM"
SUFFIX_NUCLEUS = "NUCLEUS"
SUFFIX_NUCLEOLUS = "NUCLEOLUS"

#: Reference radii in micrometres, which is also Blender units here.
#: 10 um radius = 20 um diameter, mid-range for a generic mammalian cell.
RADIUS_UM = 10.0
NUCLEUS_RADIUS_UM = 4.5
NUCLEOLUS_RADIUS_UM = 1.1

#: Icosphere subdivision. 4 gives 2562 verts / 5120 faces, which is where noise
#: displacement starts reading as a smooth surface instead of visible facets.
SUBDIVISIONS = 4
SUBDIVISIONS_SMALL = 3

#: Cytoplasm radius as a fraction of membrane radius. See module docstring.
CYTOPLASM_INSET = 0.93

#: Radial noise amplitude as a fraction of radius. Kept small deliberately: a
#: tissue cell is a slightly lumpy ball, not a starfish.
IRREGULARITY_MEMBRANE = 0.055
IRREGULARITY_CYTOPLASM = 0.045
IRREGULARITY_NUCLEUS = 0.045

#: Two noise octaves, so the surface has both broad lobes and fine detail
#: instead of looking like one uniform crinkle.
NOISE_SCALE_COARSE = 1.7
NOISE_SCALE_FINE = 4.1
NOISE_FINE_WEIGHT = 0.45

#: Nucleus offset from cell centre, as a fraction of radius. The brief asks
#: for "approximately but not perfectly" centred.
NUCLEUS_OFFSET = 0.18

#: Seeded per-cell variation. Aspect is the per-axis squash; size_jitter is a
#: small radius multiplier. Both are applied on top of the caller's `scale`.
ASPECT_RANGE = (0.92, 1.08)
SIZE_JITTER = 0.05

#: Shared procedural texture for membrane deformation. One texture for the
#: whole project; per-cell variation comes from OBJECT-space coordinates.
TEX_DISPLACE_NAME = "TEX_HealthyCellDisplace"
MEMBRANE_DEFORM_STRENGTH = 0.35

#: Names that must never appear in a generated cell.
FORBIDDEN_NAMES = {"Cube", "Sphere", "Object", "Mesh", "Material", "Icosphere"}

#: Matches a part name: CELL_HEALTHY_<PART>_<NNN>. The project's naming
#: convention puts the part before the ordinal, so the nucleus of cell 1 is
#: CELL_HEALTHY_NUCLEUS_001. Which cell a part belongs to is carried by
#: parenting, not by its name.
PART_PATTERN = re.compile(r"^{}_(?P<part>{})_(?P<index>\d{{3}})$".format(
    re.escape(PREFIX),
    "|".join(re.escape(s) for s in (
        SUFFIX_MEMBRANE, SUFFIX_CYTOPLASM, SUFFIX_NUCLEUS, SUFFIX_NUCLEOLUS)),
))


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

#: Membrane: pale desaturated blue-green, genuinely translucent. This is what
#: lets the interior stay readable from CAMERA_Cell and CAMERA_Master alike.
MAT_MEMBRANE = dict(
    name="MAT_Healthy_Membrane",
    base_color=(0.60, 0.76, 0.70, 1.0),
    roughness=0.22,
    subsurface=0.30,
    alpha=0.22,
    emission=(0.55, 0.95, 0.85, 1.0),
)

#: Cytoplasm: warm cream, heavily subsurface-scattered. The classic pale
#: cytoplasm look, and the subsurface is what stops it reading as flat plastic.
#:
#: Alpha is the lever that makes the nucleus readable. At 0.55 the cytoplasm
#: sat at the same value as the nucleus after AgX and swallowed it whole; the
#: interior has to be genuinely see-through for the layering to work at all.
MAT_CYTOPLASM = dict(
    name="MAT_Healthy_Cytoplasm",
    base_color=(0.88, 0.80, 0.58, 1.0),
    roughness=0.45,
    subsurface=0.55,
    alpha=0.26,
    emission=(0.95, 0.85, 0.55, 1.0),
)

#: Nucleus: deep indigo, near-opaque, low subsurface. Kept dark and saturated
#: on purpose -- a healthy cell has a *low* nuclear-to-cytoplasmic ratio, so
#: the nucleus is correctly small, and contrast rather than size is what has
#: to carry it. Less subsurface also keeps the colour from washing out.
MAT_NUCLEUS = dict(
    name="MAT_Healthy_Nucleus",
    base_color=(0.26, 0.22, 0.54, 1.0),
    roughness=0.40,
    subsurface=0.12,
    alpha=0.95,
    emission=(0.45, 0.40, 0.95, 1.0),
)

#: Nucleolus: darker still, so it separates from the nucleus around it.
MAT_NUCLEOLUS = dict(
    name="MAT_Healthy_Nucleolus",
    base_color=(0.12, 0.08, 0.28, 1.0),
    roughness=0.35,
    subsurface=0.05,
    alpha=1.0,
    emission=(0.55, 0.35, 0.90, 1.0),
)


def _ensure_material(spec, transparent: bool) -> bpy.types.Material:
    """Create the material from *spec*, configuring transparency for EEVEE too.

    Cycles honours Principled alpha directly, but EEVEE and the glTF viewer need
    ``surface_render_method`` set to BLENDED or a low-alpha surface renders
    essentially opaque. Setting it here means the cell reads the same in all
    three. Inputs are rewritten on every run, so editing a constant here
    actually takes effect.
    """
    spec = dict(spec)
    name = spec.pop("name")
    emission = spec.pop("emission", None)
    mat = ut.principled_material(name, emission=emission, emission_strength=0.0, **spec)
    if transparent:
        mat.surface_render_method = "BLENDED"
        mat.use_backface_culling = False
    return mat


def ensure_materials() -> dict:
    """Create all four materials and return them keyed by role."""
    return {
        "membrane": _ensure_material(MAT_MEMBRANE, transparent=True),
        "cytoplasm": _ensure_material(MAT_CYTOPLASM, transparent=True),
        "nucleus": _ensure_material(MAT_NUCLEUS, transparent=False),
        "nucleolus": _ensure_material(MAT_NUCLEOLUS, transparent=False),
    }


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def _organic_icosphere(name: str, radius: float, subdivisions: int, rng,
                       irregularity: float, aspect=(1.0, 1.0, 1.0)):
    """Build an icosphere displaced radially by two octaves of Perlin noise.

    Procedural rather than a perfect sphere, per the brief, but smooth: the
    displacement is a continuous noise field sampled at each vertex, so the
    result has no hard edges and shades cleanly.

    Args:
        name: Mesh datablock name.
        radius: Mean radius in Blender units.
        subdivisions: Icosphere subdivision level.
        rng: ``random.Random`` supplying the noise phase for this cell.
        irregularity: Peak radial deviation as a fraction of *radius*.
        aspect: Per-axis multiplier, giving the cell its seeded squash.

    Returns:
        A smooth-shaded mesh datablock.
    """
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=1.0)

    # Randomised noise phase is what makes two cells with the same parameters
    # still differ in shape, without needing different geometry.
    phase_coarse = Vector([rng.uniform(-40.0, 40.0) for _ in range(3)])
    phase_fine = Vector([rng.uniform(-40.0, 40.0) for _ in range(3)])

    for vert in bm.verts:
        direction = vert.co.normalized()
        offset = 1.0
        offset += irregularity * noise.noise(vert.co * NOISE_SCALE_COARSE + phase_coarse)
        offset += irregularity * NOISE_FINE_WEIGHT * noise.noise(
            vert.co * NOISE_SCALE_FINE + phase_fine
        )
        co = direction * (offset * radius)
        vert.co = Vector((co.x * aspect[0], co.y * aspect[1], co.z * aspect[2]))

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.shade_smooth()
    return mesh


def _add_membrane_deformation(obj, root) -> None:
    """Attach the animatable Displace modifier to a membrane.

    All cells share one Clouds texture but use OBJECT-space coordinates
    relative to their own root, so each cell deforms differently from the same
    texture and moving a cell carries its deformation field along.
    """
    texture = bpy.data.textures.get(TEX_DISPLACE_NAME)
    if texture is None:
        texture = bpy.data.textures.new(TEX_DISPLACE_NAME, type="CLOUDS")
    texture.noise_scale = 0.35
    texture.noise_depth = 2

    modifier = obj.modifiers.get("MOD_MembraneDeform")
    if modifier is None:
        modifier = obj.modifiers.new("MOD_MembraneDeform", "DISPLACE")
    modifier.texture = texture
    modifier.texture_coords = "OBJECT"
    modifier.texture_coords_object = root
    modifier.direction = "NORMAL"
    modifier.mid_level = 0.5
    modifier.strength = MEMBRANE_DEFORM_STRENGTH


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_healthy_cell(location=(0.0, 0.0, 0.0), scale: float = 1.0,
                        rotation=(0.0, 0.0, 0.0), seed: int = 0, *,
                        index: int = 1, collection=None, parent=None,
                        name_prefix: str = PREFIX, nucleoli: int = 2,
                        membrane_deform: bool = True) -> dict:
    """Create one healthy cell: root empty plus membrane, cytoplasm, nucleus.

    This is the reusable entry point. *seed* is the only thing that varies
    between two otherwise identical calls, and it drives shape, nucleus offset,
    and size jitter -- enough that a handful of cells read as a population
    rather than as clones.

    Args:
        location: World-space location of the cell centre.
        scale: Uniform size multiplier on top of :data:`RADIUS_UM`.
        rotation: Euler rotation in radians, applied to the root so the whole
            cell turns coherently.
        seed: Controls the variation. Same seed, same cell, every time.
        index: 1-based ordinal used for the object name.
        collection: Target collection. Defaults to ``02_HEALTHY_TISSUE``.
        parent: Optional parent object, normally a controller.
        name_prefix: Object name prefix, normally ``CELL_HEALTHY``.
        nucleoli: How many simplified nucleoli to add. 0 for none.
        membrane_deform: Attach the animatable Displace modifier to the membrane.

    Returns:
        ``{"root", "membrane", "cytoplasm", "nucleus", "nucleoli"}``. *root* is
        the object to animate; the rest are its children, in layers.
    """
    coll = collection or ut.resolve_collection(COLLECTION)
    rng = random.Random(seed)

    aspect = tuple(rng.uniform(*ASPECT_RANGE) for _ in range(3))
    size_factor = 1.0 + rng.uniform(-SIZE_JITTER, SIZE_JITTER)
    radius = RADIUS_UM * scale * size_factor

    root = ut.new_empty(
        ut.obj_name(name_prefix, index), coll,
        location=location, parent=parent, size=radius * 0.6,
    )
    root.rotation_euler = rotation

    materials = ensure_materials()

    membrane_mesh = _organic_icosphere(
        "MESH_{}".format(ut.obj_name("{}_{}".format(PREFIX, SUFFIX_MEMBRANE), index)),
        radius, SUBDIVISIONS, rng, IRREGULARITY_MEMBRANE, aspect,
    )
    membrane = ut.get_or_create_object(
        ut.obj_name("{}_{}".format(PREFIX, SUFFIX_MEMBRANE), index), coll,
        lambda n: membrane_mesh,
    )
    ut.set_parent(membrane, root)
    ut.assign_material(membrane, materials["membrane"])
    if membrane_deform:
        _add_membrane_deformation(membrane, root)

    # Cytoplasm inherits the membrane's aspect so the two stay concentric, but
    # gets its own noise phase: a real interior does not mirror the shell.
    cytoplasm_mesh = _organic_icosphere(
        "MESH_{}".format(ut.obj_name("{}_{}".format(PREFIX, SUFFIX_CYTOPLASM), index)),
        radius * CYTOPLASM_INSET, SUBDIVISIONS_SMALL, rng,
        IRREGULARITY_CYTOPLASM, aspect,
    )
    cytoplasm = ut.get_or_create_object(
        ut.obj_name("{}_{}".format(PREFIX, SUFFIX_CYTOPLASM), index), coll,
        lambda n: cytoplasm_mesh,
    )
    ut.set_parent(cytoplasm, root)
    ut.assign_material(cytoplasm, materials["cytoplasm"])

    # Off-centre nucleus, offset in a seeded direction.
    offset = Vector([rng.uniform(-1.0, 1.0) for _ in range(3)]).normalized()
    nucleus_mesh = _organic_icosphere(
        "MESH_{}".format(ut.obj_name("{}_{}".format(PREFIX, SUFFIX_NUCLEUS), index)),
        NUCLEUS_RADIUS_UM * scale * size_factor, SUBDIVISIONS_SMALL, rng,
        IRREGULARITY_NUCLEUS,
    )
    nucleus = ut.get_or_create_object(
        ut.obj_name("{}_{}".format(PREFIX, SUFFIX_NUCLEUS), index), coll,
        lambda n: nucleus_mesh,
    )
    ut.set_parent(nucleus, root)
    nucleus.location = offset * (radius * NUCLEUS_OFFSET)
    ut.assign_material(nucleus, materials["nucleus"])

    # Nucleoli are the one part that is genuinely multiple per cell, so they
    # need their own ordinal space. Deriving it from the cell index keeps it
    # monotonic and collision-free across a build. Which cell a nucleolus
    # belongs to is carried by parenting, not by string parsing.
    nucleolus_objects = []
    for j in range(max(0, nucleoli)):
        n_index = (index - 1) * max(1, nucleoli) + j + 1
        mesh = _organic_icosphere(
            "MESH_{}".format(ut.obj_name("{}_{}".format(PREFIX, SUFFIX_NUCLEOLUS), n_index)),
            NUCLEOLUS_RADIUS_UM * scale * size_factor, SUBDIVISIONS_SMALL, rng,
            0.10,
        )
        obj = ut.get_or_create_object(
            ut.obj_name("{}_{}".format(PREFIX, SUFFIX_NUCLEOLUS), n_index), coll,
            lambda n, m=mesh: m,
        )
        ut.set_parent(obj, nucleus)
        obj.location = Vector([rng.uniform(-1.0, 1.0) for _ in range(3)]).normalized() * (
            NUCLEUS_RADIUS_UM * scale * size_factor * 0.45
        )
        ut.assign_material(obj, materials["nucleolus"])
        nucleolus_objects.append(obj)

    return {
        "root": root,
        "membrane": membrane,
        "cytoplasm": cytoplasm,
        "nucleus": nucleus,
        "nucleoli": nucleolus_objects,
    }


# ---------------------------------------------------------------------------
# Test collection
# ---------------------------------------------------------------------------

#: Scratch collection for asset validation. Nested under 10_DEBUG so that
#: "never ship the debug tree" is structural rather than a convention someone
#: has to remember at export time.
TEST_COLLECTION = "TEST_HealthyCells"
TEST_CELL_COUNT = 5
#: Cells are 20 um across, so this spacing keeps them from intersecting.
TEST_SPACING_UM = 26.0


def build_test_cells(collection=None, count: int = TEST_CELL_COUNT,
                     clear: bool = True) -> list:
    """Generate *count* varied cells into ``TEST_HealthyCells``.

    Each cell gets its own seed, rotation and position, so the result is a
    population rather than five clones. A shared seed would defeat the point of
    having a seed parameter at all.

    Returns:
        The list of per-cell part dicts from :func:`create_healthy_cell`.
    """
    coll = collection or ut.get_or_create_collection(TEST_COLLECTION, ut.resolve_collection("10_DEBUG"))
    if clear:
        ut.clear_collection(coll, prefix=PREFIX)

    built = []
    span = (count - 1) * TEST_SPACING_UM
    for i in range(count):
        built.append(
            create_healthy_cell(
                location=(-span / 2.0 + i * TEST_SPACING_UM, 0.0, 0.0),
                rotation=(0.0, 0.0, i * 1.1),
                seed=1000 + i,
                index=i + 1,
                collection=coll,
            )
        )
    return built


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _check_topology(obj) -> list:
    """Return a list of topology problems for one mesh object."""
    problems = []
    mesh = obj.data
    if not mesh.vertices or not mesh.polygons:
        problems.append("{}: empty mesh".format(obj.name))
        return problems

    bm = bmesh.new()
    bm.from_mesh(mesh)

    loose = [v.index for v in bm.verts if not v.link_faces]
    if loose:
        problems.append("{}: {} loose verts".format(obj.name, len(loose)))

    degenerate = [f.index for f in bm.faces if f.calc_area() < 1e-12]
    if degenerate:
        problems.append("{}: {} degenerate faces".format(obj.name, len(degenerate)))

    open_edges = [e.index for e in bm.edges if len(e.link_faces) != 2]
    if open_edges:
        problems.append("{}: {} non-manifold/open edges".format(obj.name, len(open_edges)))

    if bm.calc_volume(signed=True) <= 0.0:
        problems.append("{}: normals point inward".format(obj.name))

    bm.free()
    return problems


def _check_reproducibility() -> list:
    """Verify the seed gives both repeatability and variation.

    The brief asks for controlled variation, which is two separate properties
    and it is easy to have one without the other: a generator that ignores its
    seed produces five clones, and one that derives shape from something
    unseeded produces a different cell every run and cannot be reproduced.

    Builds throwaway meshes in memory rather than scene objects, so this costs
    nothing and leaves no trace in the outliner.
    """
    problems = []

    def span(seed: int) -> float:
        mesh = _organic_icosphere(
            "MESH_TMP_Repro", RADIUS_UM, SUBDIVISIONS_SMALL,
            random.Random(seed), IRREGULARITY_MEMBRANE,
        )
        coords = [v.co for v in mesh.vertices]
        value = max(
            max(c[axis] for c in coords) - min(c[axis] for c in coords)
            for axis in range(3)
        )
        bpy.data.meshes.remove(mesh)
        return round(value, 6)

    if span(7) != span(7):
        problems.append("seed is not reproducible: same seed gave different shapes")
    if span(7) == span(8):
        problems.append("seed is ignored: different seeds gave identical shapes")
    return problems


def validate_healthy_cells(collection=None, count: int = TEST_CELL_COUNT) -> dict:
    """Validate the generated test cells. Returns findings and any failures.

    Checks the five things the brief asks for: topology, naming, materials,
    transforms, and scale consistency. Every check is a real measurement, not
    an assertion that something was constructed the way this script wanted.

    Args:
        collection: Collection to validate. Defaults to ``TEST_HealthyCells``.
        count: Expected number of cells.

    Returns:
        ``{"ok", "failures", "checks", "measurements"}``.
    """
    coll = collection or ut.resolve_collection(TEST_COLLECTION)
    failures = []
    measurements = {}

    roots = [o for o in coll.objects if o.name.startswith(PREFIX)
             and o.type == "EMPTY"]
    meshes = [o for o in coll.objects if o.type == "MESH"]

    # --- counts -----------------------------------------------------------
    if len(roots) != count:
        failures.append("expected {} cells, found {}".format(count, len(roots)))

    # --- naming -----------------------------------------------------------
    seen_indexes = set()
    for root in roots:
        if any(root.name == bad for bad in FORBIDDEN_NAMES):
            failures.append("{}: forbidden generic name".format(root.name))
        if len(root.name) > len(PREFIX) + 4 and root.name[-4] == "." and root.name[-3:].isdigit():
            failures.append("{}: has a .001 duplicate suffix".format(root.name))
        try:
            seen_indexes.add(int(root.name.rsplit("_", 1)[1]))
        except (IndexError, ValueError):
            failures.append("{}: name does not end in an ordinal".format(root.name))
    if len(seen_indexes) != len(roots):
        failures.append("duplicate cell ordinals: {}".format(sorted(seen_indexes)))

    # --- topology ---------------------------------------------------------
    for obj in meshes:
        failures.extend(_check_topology(obj))

    # --- materials --------------------------------------------------------
    # Parts are identified by name pattern, and their cell membership by
    # parenting. Parenting is authoritative; never infer it from a substring.
    expected_mat = {
        SUFFIX_MEMBRANE: "MAT_Healthy_Membrane",
        SUFFIX_CYTOPLASM: "MAT_Healthy_Cytoplasm",
        SUFFIX_NUCLEUS: "MAT_Healthy_Nucleus",
        SUFFIX_NUCLEOLUS: "MAT_Healthy_Nucleolus",
    }
    for obj in meshes:
        match = PART_PATTERN.match(obj.name)
        if match is None:
            failures.append("{}: name does not match CELL_HEALTHY_<PART>_<NNN>".format(obj.name))
            continue
        role = match.group("part")
        if role not in expected_mat:
            failures.append("{}: unknown part role {!r}".format(obj.name, role))
            continue
        if len(obj.data.materials) != 1:
            failures.append("{}: expected 1 material slot, found {}".format(
                obj.name, len(obj.data.materials)))
            continue
        actual = obj.data.materials[0].name
        if actual != expected_mat[role]:
            failures.append("{}: material is {!r}, expected {!r}".format(
                obj.name, actual, expected_mat[role]))
        if not actual.startswith("MAT_Healthy_"):
            failures.append("{}: material {!r} breaks the MAT_Healthy_* rule".format(
                obj.name, actual))

    # --- transforms -------------------------------------------------------
    for root in roots:
        if root.parent is not None:
            failures.append("{}: test cells should be unparented".format(root.name))
        parts = {}
        for obj in coll.objects:
            if obj.type != "MESH" or obj.parent != root:
                continue
            match = PART_PATTERN.match(obj.name)
            if match:
                parts[match.group("part")] = obj
        for required in (SUFFIX_MEMBRANE, SUFFIX_CYTOPLASM, SUFFIX_NUCLEUS):
            if required not in parts:
                failures.append("{}: missing part {}".format(root.name, required))

        membrane = parts.get(SUFFIX_MEMBRANE)
        if membrane is not None:
            mod = membrane.modifiers.get("MOD_MembraneDeform")
            if mod is None:
                failures.append("{}: membrane deformation modifier missing".format(membrane.name))
            elif mod.texture_coords_object != root:
                failures.append("{}: deformation not bound to its own root".format(membrane.name))

        nucleus = parts.get(SUFFIX_NUCLEUS)
        if nucleus is not None:
            if len([o for o in coll.objects if o.parent == nucleus]) < 1:
                failures.append("{}: nucleus has no nucleoli".format(nucleus.name))
            offset = nucleus.matrix_local.translation.length
            if offset > RADIUS_UM * NUCLEUS_OFFSET * 1.5:
                failures.append("{}: nucleus offset {:.2f} BU exceeds the seeded maximum".format(
                    nucleus.name, offset))

    # --- scale consistency ------------------------------------------------
    scale_tolerance = 0.15  # seeded jitter is +/-5%, noise is ~5%, so 15% is slack
    for root in roots:
        parts = {}
        for obj in coll.objects:
            if obj.type != "MESH" or obj.parent != root:
                continue
            match = PART_PATTERN.match(obj.name)
            if match:
                parts[match.group("part")] = obj

        membrane = parts.get(SUFFIX_MEMBRANE)
        if membrane is None:
            continue
        measured = max(membrane.dimensions) / 2.0
        expected = RADIUS_UM
        ratio = measured / expected
        measurements[root.name] = round(ratio, 4)
        if abs(ratio - 1.0) > scale_tolerance:
            failures.append("{}: membrane radius {:.2f} BU, expected ~{:.1f} (+/-{:.0%})".format(
                root.name, measured, expected, scale_tolerance))

        ordering = {SUFFIX_MEMBRANE: measured}
        for role in (SUFFIX_CYTOPLASM, SUFFIX_NUCLEUS):
            if role in parts:
                ordering[role] = max(parts[role].dimensions) / 2.0
        if len(ordering) == 3 and not (
            ordering[SUFFIX_MEMBRANE] > ordering[SUFFIX_CYTOPLASM] > ordering[SUFFIX_NUCLEUS]
        ):
            failures.append("{}: layer sizes not ordered membrane > cytoplasm > nucleus: {}".format(
                root.name, {k: round(v, 2) for k, v in ordering.items()}))

    scene = bpy.context.scene
    if abs(scene.unit_settings.scale_length - ut.SCENE_SCALE_LENGTH) > 1e-12:
        failures.append("scene scale_length is not 1e-6; micrometre convention broken")

    # --- seeded variation -------------------------------------------------
    failures.extend(_check_reproducibility())
    distinct = len(set(measurements.values()))
    if distinct != len(measurements):
        failures.append("test cells are clones: only {} distinct shapes across {} cells".format(
            distinct, len(measurements)))

    return {
        "ok": not failures,
        "failures": failures,
        "checks": {
            "cells": len(roots),
            "meshes": len(meshes),
            "topology_checked": len(meshes),
            "materials": sorted({m.name for o in meshes for m in o.data.materials}),
        },
        "measurements": measurements,
    }


def main() -> None:
    """Generate the test collection, validate it, and report.

    Establishes the micrometre unit convention first: this asset's dimensions
    are only meaningful under it, and the validator asserts it. Normally
    :mod:`scene_tumor` has already done this, but a standalone run starts from
    a factory scene where it is not set.
    """
    ut.setup_units()
    build_test_cells()
    report = validate_healthy_cells()

    print("[healthy_cell] built {} cells in {}".format(
        report["checks"]["cells"], TEST_COLLECTION))
    print("[healthy_cell] checks: {}".format(report["checks"]))
    print("[healthy_cell] radius ratios vs {} BU: {}".format(
        RADIUS_UM, report["measurements"]))
    if report["ok"]:
        print("[healthy_cell] VALIDATION PASSED")
    else:
        for failure in report["failures"]:
            print("[healthy_cell] FAIL: {}".format(failure))
        raise SystemExit("[healthy_cell] VALIDATION FAILED ({} problems)".format(
            len(report["failures"])))


if __name__ == "__main__":
    main()
