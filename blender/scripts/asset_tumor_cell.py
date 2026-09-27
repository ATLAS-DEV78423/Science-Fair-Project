"""Tumour cell asset: CELL_TUMOR_001.

Scientific constraint
---------------------
**Cancer cells do not have one universal physical morphology.** There is no
single shape that is "a cancer cell", and the spiky, jagged, menacing blob is a
caricature with nothing behind it. Modelling that would actively teach the
wrong lesson at a science fair.

So tumour identity is *not* carried by a signature shape. It is carried by
four things that are actually true:

* **Morphological variation** -- cells differ from each other. A real tumour is
  a heterogeneous population, not a repeated unit.
* **Context** -- these cells sit in a packed mass with the ECM around them,
  read against the regular healthy tissue beside them.
* **Proliferative behaviour** -- the identity that matters is what the cell
  *does*: divides, and permits viral replication. That is animation, and it is
  where the biology should be spent.
* **Simulation state** -- susceptibility, infection and death are states a cell
  is *in*, not shapes it has.

That is also why the variations here stay mild. A cell that is dramatically
deformed is as much a lie as one that is spiky.

Morphology vocabulary
---------------------
Five seeded morphologies, all bounded, none extreme:

    ROUND        near-spherical, lightly textured
    ELONGATED    stretched on one axis, modestly
    IRREGULAR    lumpier surface, roughly spherical envelope
    COMPRESSED   flattened against the substrate
    ASYMMETRIC   one-sided bulge, envelope still smooth

Nucleus
-------
Prominent and clearly visible, varying per cell in size, position and shape.

**No nucleus morphology here is claimed to be a cancer hallmark.** A raised
nuclear-to-cytoplasmic ratio is common but neither universal nor diagnostic,
and plenty of tumour cells have unremarkable nuclei. The ratio therefore
varies across the declared range per cell, including values below the healthy
cell's, rather than being pinned high. What a viewer should take away is
"nuclei vary", not "big dark nucleus means cancer".

Receptor markers — illustrative only
------------------------------------
Abstract membrane markers showing *surface receptor abundance*. Every one is
labelled internally:

    "Illustrative receptor representation"

They are **not** a specific receptor, and no receptor is modelled. They encode
one thing only: more markers means more permissive to infection, which is the
direction the real biology runs for an oncolytic virus.

The three susceptibility states differ **only in marker count**. Geometry,
marker shape, and material are identical between states, because a
susceptibility state is a property of the cell, not a different kind of cell.

    TUMOR_SUSCEPTIBLE        44 markers
    TUMOR_LESS_SUSCEPTIBLE   20 markers
    TUMOR_NONPERMISSIVE       6 markers

**These are simulation states, not universal biological classifications.** Real
tumours contain a continuum of permissiveness; three discrete states is a
modelling convenience for driving an animation, and nothing more.

Geometry
--------
    CELL_TUMOR_001                    root empty; the single transform handle
    +- CELL_TUMOR_MEMBRANE_001        outer shell, carries the receptors
    +- CELL_TUMOR_CYTOPLASM_001       inner volume
    +- CELL_TUMOR_NUCLEUS_001         prominent, off-centre, varied
    +- CELL_TUMOR_RECEPTOR_001_01..   linked instances, one shared mesh

Simulation hooks
----------------
The root carries custom properties the animation layer can read and key:

    susceptibility_state   which of the three states this cell is in
    morphology             which morphology family it belongs to
    receptor_count         marker count, matching the state
    illustrative_note      "Illustrative receptor representation"

Preparation for each requested capability, all via that one root:

    infection       key root location/scale; the virus entry beat owns it
    replication     raise receptor_count-adjacent virion count; root is the handle
    stress          key material emission strength on any part
    cell death      key root scale down, or hide_render
    disappearance   key root scale to zero, or hide_render / hide_viewport
    highlighting    key emission strength; all four materials have it at zero
    duplication     every part is its own mesh datablock, so a cell duplicates
                    with ut.instance_linked and costs no new geometry

Run standalone::

    blender --background --python blender/scripts/asset_tumor_cell.py
"""

from __future__ import annotations

import math
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

COLLECTION = "01_TUMOR"

PREFIX = "CELL_TUMOR"
SUFFIX_MEMBRANE = "MEMBRANE"
SUFFIX_CYTOPLASM = "CYTOPLASM"
SUFFIX_NUCLEUS = "NUCLEUS"
SUFFIX_RECEPTOR = "RECEPTOR"

#: Every receptor marker carries this, as a custom property and in the material
#: name. The brief requires the label be carried internally, so it is asserted
#: by the validator rather than merely mentioned in a docstring.
ILLUSTRATIVE_NOTE = "Illustrative receptor representation"

#: Reference radius in micrometres == Blender units here. Matches the healthy
#: cell's nominal size so the two populations are directly comparable.
RADIUS_UM = 10.0

SUBDIVISIONS = 4
SUBDIVISIONS_SMALL = 3
#: Markers are small and there can be hundreds, so they get the cheapest mesh
#: that still shades smoothly. 42 verts, shared by the whole population.
RECEPTOR_SUBDIVISIONS = 2

CYTOPLASM_INSET = 0.93

NOISE_SCALE_COARSE = 1.7
NOISE_SCALE_FINE = 4.1
NOISE_FINE_WEIGHT = 0.45

#: Marker geometry, constant across every cell and every state. Changing these
#: would break the promise that the three states differ only in count.
RECEPTOR_RADIUS_UM = 0.34
#: How far a marker sits proud of the membrane, as a fraction of its radius.
#: Slightly embedded rather than floating or buried.
RECEPTOR_EMBED = 0.30

#: Nucleus varies per cell. The range deliberately dips below the healthy
#: cell's 0.45 ratio: plenty of tumour cells have unremarkable nuclei, and
#: pinning every nucleus high would assert a hallmark that does not hold.
NUCLEUS_RATIO_RANGE = (0.38, 0.66)
NUCLEUS_OFFSET_RANGE = (0.05, 0.30)
NUCLEUS_ASPECT_RANGE = (0.88, 1.12)
IRREGULARITY_NUCLEUS = 0.05

#: Tumour cells vary in size more than healthy ones, but a real tumour is still
#: one population, so the range stays bounded. See the coherence checks.
SIZE_JITTER = 0.12
#: Per-cell jitter on top of the chosen morphology, so two cells sharing a
#: morphology are still not identical.
MORPH_JITTER_ASPECT = 0.06
MORPH_JITTER_IRREGULARITY = 0.18

#: The five morphology families. aspect is per-axis, irregularity is peak
#: radial deviation as a fraction of radius, asymmetry is a one-sided bulge.
#: All values are deliberately mild -- see the module docstring.
MORPHOLOGIES = {
    "ROUND": dict(aspect=(1.00, 1.00, 1.00), irregularity=0.030, asymmetry=0.000),
    "ELONGATED": dict(aspect=(1.00, 0.70, 0.84), irregularity=0.035, asymmetry=0.000),
    "IRREGULAR": dict(aspect=(0.98, 0.95, 1.02), irregularity=0.075, asymmetry=0.000),
    "COMPRESSED": dict(aspect=(1.02, 0.96, 0.55), irregularity=0.030, asymmetry=0.000),
    "ASYMMETRIC": dict(aspect=(0.95, 0.90, 1.00), irregularity=0.040, asymmetry=0.075),
}
MORPHOLOGY_NAMES = tuple(MORPHOLOGIES)

#: Simulation states. receptor_count is the ONLY thing that differs between
#: them -- see the module docstring.
SUSCEPTIBILITY_STATES = {
    "TUMOR_SUSCEPTIBLE": 44,
    "TUMOR_LESS_SUSCEPTIBLE": 20,
    "TUMOR_NONPERMISSIVE": 6,
}

TEX_DISPLACE_NAME = "TEX_TumorCellDisplace"
MEMBRANE_DEFORM_STRENGTH = 0.30

FORBIDDEN_NAMES = {"Cube", "Sphere", "Object", "Mesh", "Material", "Icosphere"}

#: Matches CELL_TUMOR_<PART>_<NNN>, plus the receptor form
#: CELL_TUMOR_RECEPTOR_<cell NNN>_<marker NN> -- markers are one of several
#: per cell, so they carry a second ordinal.
PART_PATTERN = re.compile(
    r"^{}_(?P<part>{})_(?P<index>\d{{3}})(?:_(?P<marker>\d{{2}}))?$".format(
        re.escape(PREFIX),
        "|".join(re.escape(s) for s in (
            SUFFIX_MEMBRANE, SUFFIX_CYTOPLASM, SUFFIX_NUCLEUS, SUFFIX_RECEPTOR))))

#: Root custom properties, so the animation layer can read state off a cell.
PROP_STATE = "susceptibility_state"
PROP_MORPHOLOGY = "morphology"
PROP_RECEPTOR_COUNT = "receptor_count"
PROP_NOTE = "illustrative_note"
PROP_ASPECT = "aspect"


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------
# Warm and more saturated than the healthy cell's cool blue-green, so the two
# populations separate at a glance. Still muted: this is a tissue sample, not
# a warning sign.

MAT_MEMBRANE = dict(
    name="MAT_Tumor_Membrane",
    base_color=(0.74, 0.54, 0.38, 1.0),
    roughness=0.28,
    subsurface=0.28,
    alpha=0.24,
    emission=(0.98, 0.78, 0.45, 1.0),
)

MAT_CYTOPLASM = dict(
    name="MAT_Tumor_Cytoplasm",
    base_color=(0.80, 0.53, 0.50, 1.0),
    roughness=0.48,
    subsurface=0.50,
    alpha=0.30,
    emission=(0.98, 0.62, 0.55, 1.0),
)

#: Deeper and more saturated than the healthy nucleus, and more opaque, so a
#: prominent nucleus stays readable through two translucent layers.
MAT_NUCLEUS = dict(
    name="MAT_Tumor_Nucleus",
    base_color=(0.30, 0.13, 0.34, 1.0),
    roughness=0.38,
    subsurface=0.10,
    alpha=0.96,
    emission=(0.80, 0.30, 0.85, 1.0),
)

#: Gold accent. Distinct from all three cell layers so markers read as markers
#: rather than as part of the cell. Named to carry the illustrative label.
MAT_RECEPTOR = dict(
    name="MAT_Tumor_Receptor_Illustrative",
    base_color=(0.96, 0.74, 0.30, 1.0),
    roughness=0.35,
    metallic=0.15,
    alpha=1.0,
    emission=(1.00, 0.82, 0.40, 1.0),
)


def _ensure_material(spec, transparent: bool) -> bpy.types.Material:
    """Create or update a material, configuring transparency for EEVEE too.

    Cycles honours Principled alpha directly, but EEVEE and the glTF viewer need
    ``surface_render_method`` set to BLENDED or a low-alpha surface renders
    essentially opaque. Inputs are rewritten on every run, so editing a
    constant here actually takes effect.
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
    """Create all four tumour materials and return them keyed by role."""
    return {
        "membrane": _ensure_material(MAT_MEMBRANE, transparent=True),
        "cytoplasm": _ensure_material(MAT_CYTOPLASM, transparent=True),
        "nucleus": _ensure_material(MAT_NUCLEUS, transparent=False),
        "receptor": _ensure_material(MAT_RECEPTOR, transparent=False),
    }


# ---------------------------------------------------------------------------
# Surface maths
# ---------------------------------------------------------------------------


def _surface_factor(shape: dict, direction: Vector) -> float:
    """Radial displacement factor for one direction on a cell's surface.

    Pure function of the shape description, which is what lets the receptor
    placement sit markers exactly on the deformed membrane rather than on the
    sphere the membrane started as. Both the mesh build and the receptor
    placement call this, so they cannot disagree.

    Args:
        shape: Per-cell parameter dict from :func:`_make_shape`.
        direction: Unit vector.

    Returns:
        Multiplier to apply to the cell radius along *direction*.
    """
    factor = 1.0
    factor += shape["irregularity"] * noise.noise(
        direction * NOISE_SCALE_COARSE + shape["phase_coarse"])
    factor += shape["irregularity"] * NOISE_FINE_WEIGHT * noise.noise(
        direction * NOISE_SCALE_FINE + shape["phase_fine"])
    if shape["asymmetry"]:
        # One-sided bulge: only the hemisphere facing asym_dir is displaced.
        factor += shape["asymmetry"] * max(0.0, direction.dot(shape["asym_dir"]))
    return factor


def _surface_point(shape: dict, direction: Vector) -> Vector:
    """Point on the deformed cell surface along *direction*, aspect applied."""
    scaled = Vector((direction.x * shape["aspect"][0],
                     direction.y * shape["aspect"][1],
                     direction.z * shape["aspect"][2]))
    return scaled * (shape["radius"] * _surface_factor(shape, direction))


def _make_shape(rng, radius: float, morphology: str) -> dict:
    """Build the per-cell shape description from a morphology and an RNG."""
    base = MORPHOLOGIES[morphology]
    aspect = tuple(
        max(0.35, min(1.4, a * (1.0 + rng.uniform(-MORPH_JITTER_ASPECT,
                                                 MORPH_JITTER_ASPECT))))
        for a in base["aspect"]
    )
    return {
        "radius": radius,
        "aspect": aspect,
        "irregularity": base["irregularity"] * (
            1.0 + rng.uniform(-MORPH_JITTER_IRREGULARITY, MORPH_JITTER_IRREGULARITY)),
        "asymmetry": base["asymmetry"],
        "asym_dir": Vector([rng.uniform(-1.0, 1.0) for _ in range(3)]).normalized(),
        "phase_coarse": Vector([rng.uniform(-40.0, 40.0) for _ in range(3)]),
        "phase_fine": Vector([rng.uniform(-40.0, 40.0) for _ in range(3)]),
    }


def _organic_icosphere(name: str, shape: dict, subdivisions: int, direction_scale=1.0):
    """Icosphere wrapped onto the shape's deformed surface, smooth shaded.

    Args:
        name: Mesh datablock name.
        shape: Per-cell shape description.
        subdivisions: Icosphere subdivision level.
        direction_scale: Optional multiplier on the radius, for inner layers.

    Returns:
        A smooth-shaded mesh datablock.
    """
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=1.0)
    for vert in bm.verts:
        vert.co = _surface_point(shape, vert.co.normalized()) * direction_scale
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.shade_smooth()
    return mesh


def _receptor_directions(count: int, rng) -> list:
    """Evenly spread unit directions, with seeded jitter so cells differ.

    A Fibonacci spiral rather than pure random: random points clump, and a
    clumped marker distribution looks like a mistake. The seeded rotation and
    per-point jitter keep neighbouring cells from looking stamped.
    """
    golden = math.pi * (3.0 - math.sqrt(5.0))
    spin = rng.uniform(0.0, 2.0 * math.pi)
    directions = []
    for i in range(count):
        z = 1.0 - (2.0 * i + 1.0) / count
        r = math.sqrt(max(0.0, 1.0 - z * z))
        theta = golden * i + spin + rng.uniform(-0.35, 0.35)
        directions.append(Vector((math.cos(theta) * r, math.sin(theta) * r, z)))
    return directions


def _receptor_mesh(radius: float = RECEPTOR_RADIUS_UM):
    """Get or create the one marker mesh the whole population shares.

    Every receptor in every cell instances this single datablock. A population
    of 20 cells at high susceptibility is ~600 markers; that must cost one mesh,
    not six hundred.
    """
    name = "MESH_{}_ILLUSTRATIVE".format(SUFFIX_RECEPTOR)
    mesh = bpy.data.meshes.get(name)
    if mesh is not None:
        return mesh
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=RECEPTOR_SUBDIVISIONS, radius=radius)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.shade_smooth()
    return mesh


def _add_membrane_deformation(obj, root) -> None:
    """Attach the animatable Displace modifier to a membrane.

    All cells share one Clouds texture but use OBJECT-space coordinates relative
    to their own root, so each deforms differently from the same texture and
    moving a cell carries its deformation field along.
    """
    texture = bpy.data.textures.get(TEX_DISPLACE_NAME)
    if texture is None:
        texture = bpy.data.textures.new(TEX_DISPLACE_NAME, type="CLOUDS")
    texture.noise_scale = 0.4
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


def create_tumor_cell(location=(0.0, 0.0, 0.0), scale: float = 1.0,
                      rotation=(0.0, 0.0, 0.0), morphology_seed: int = 0,
                      susceptibility_state: str = "TUMOR_SUSCEPTIBLE", *,
                      index: int = 1, morphology: str | None = None,
                      collection=None, parent=None, name_prefix: str = PREFIX,
                      membrane_deform: bool = True) -> dict:
    """Create one tumour cell: root empty, three layers, and receptor markers.

    *morphology_seed* is the only source of variation, and it drives size,
    morphology family, aspect, noise phase, asymmetry direction, and nucleus
    size, position and shape. Same seed, same cell, every time.

    *susceptibility_state* is one of :data:`SUSCEPTIBILITY_STATES` and changes
    nothing but the receptor marker count.

    Args:
        location: World-space location of the cell centre.
        scale: Uniform size multiplier on top of :data:`RADIUS_UM`.
        rotation: Euler rotation in radians, applied to the root so the whole
            cell turns coherently.
        morphology_seed: Controls all variation.
        susceptibility_state: ``TUMOR_SUSCEPTIBLE``,
            ``TUMOR_LESS_SUSCEPTIBLE`` or ``TUMOR_NONPERMISSIVE``.
        index: 1-based ordinal used for the object names.
        morphology: Force a specific morphology family. ``None`` picks one from
            the seed, which is what a population build wants.
        collection: Target collection. Defaults to ``01_TUMOR``.
        parent: Optional parent object, normally ``CTRL_Tumor``.
        name_prefix: Object name prefix, normally ``CELL_TUMOR``.
        membrane_deform: Attach the animatable Displace modifier.

    Returns:
        ``{"root", "membrane", "cytoplasm", "nucleus", "receptors", "morphology",
        "susceptibility_state"}``. *root* is the object to animate.
    """
    if susceptibility_state not in SUSCEPTIBILITY_STATES:
        raise ValueError(
            "create_tumor_cell: unknown susceptibility_state {!r}; expected one of {}".format(
                susceptibility_state, sorted(SUSCEPTIBILITY_STATES)))

    coll = collection or ut.resolve_collection(COLLECTION)
    rng = random.Random(morphology_seed)

    if morphology is None:
        morphology = MORPHOLOGY_NAMES[rng.randrange(len(MORPHOLOGY_NAMES))]
    if morphology not in MORPHOLOGIES:
        raise ValueError("create_tumor_cell: unknown morphology {!r}".format(morphology))

    receptor_count = SUSCEPTIBILITY_STATES[susceptibility_state]
    size_factor = 1.0 + rng.uniform(-SIZE_JITTER, SIZE_JITTER)
    radius = RADIUS_UM * scale * size_factor

    shape = _make_shape(rng, radius, morphology)

    root = ut.new_empty(
        ut.obj_name(name_prefix, index), coll,
        location=location, parent=parent, size=radius * 0.6,
    )
    root.rotation_euler = rotation
    root[PROP_STATE] = susceptibility_state
    root[PROP_MORPHOLOGY] = morphology
    root[PROP_RECEPTOR_COUNT] = receptor_count
    root[PROP_NOTE] = ILLUSTRATIVE_NOTE
    # Morphology parameters, kept on the root so the simulation can read a
    # cell's actual shape rather than assuming the nominal for its family.
    root[PROP_ASPECT] = list(shape["aspect"])

    materials = ensure_materials()

    membrane_mesh = _organic_icosphere(
        "MESH_{}".format(ut.obj_name("{}_{}".format(PREFIX, SUFFIX_MEMBRANE), index)),
        shape, SUBDIVISIONS)
    membrane = ut.get_or_create_object(
        ut.obj_name("{}_{}".format(PREFIX, SUFFIX_MEMBRANE), index), coll,
        lambda n: membrane_mesh)
    ut.set_parent(membrane, root)
    ut.assign_material(membrane, materials["membrane"])
    if membrane_deform:
        _add_membrane_deformation(membrane, root)

    # Cytoplasm shares the envelope's aspect so the layers stay concentric, but
    # gets its own noise phase: a real interior does not mirror its shell.
    inner = dict(shape)
    inner["phase_coarse"] = Vector([rng.uniform(-40.0, 40.0) for _ in range(3)])
    inner["phase_fine"] = Vector([rng.uniform(-40.0, 40.0) for _ in range(3)])
    cytoplasm_mesh = _organic_icosphere(
        "MESH_{}".format(ut.obj_name("{}_{}".format(PREFIX, SUFFIX_CYTOPLASM), index)),
        inner, SUBDIVISIONS_SMALL, direction_scale=CYTOPLASM_INSET)
    cytoplasm = ut.get_or_create_object(
        ut.obj_name("{}_{}".format(PREFIX, SUFFIX_CYTOPLASM), index), coll,
        lambda n: cytoplasm_mesh)
    ut.set_parent(cytoplasm, root)
    ut.assign_material(cytoplasm, materials["cytoplasm"])

    # Nucleus: size, position and shape all vary, and the ratio range dips below
    # the healthy cell's on purpose. See the module docstring.
    n_ratio = rng.uniform(*NUCLEUS_RATIO_RANGE)
    n_offset = rng.uniform(*NUCLEUS_OFFSET_RANGE)
    n_aspect = tuple(rng.uniform(*NUCLEUS_ASPECT_RANGE) for _ in range(3))
    n_shape = {
        "radius": radius * n_ratio,
        "aspect": n_aspect,
        "irregularity": IRREGULARITY_NUCLEUS,
        "asymmetry": 0.0,
        "asym_dir": Vector((0.0, 0.0, 1.0)),
        "phase_coarse": Vector([rng.uniform(-40.0, 40.0) for _ in range(3)]),
        "phase_fine": Vector([rng.uniform(-40.0, 40.0) for _ in range(3)]),
    }
    nucleus_mesh = _organic_icosphere(
        "MESH_{}".format(ut.obj_name("{}_{}".format(PREFIX, SUFFIX_NUCLEUS), index)),
        n_shape, SUBDIVISIONS_SMALL)
    nucleus = ut.get_or_create_object(
        ut.obj_name("{}_{}".format(PREFIX, SUFFIX_NUCLEUS), index), coll,
        lambda n: nucleus_mesh)
    ut.set_parent(nucleus, root)
    nucleus.location = (Vector([rng.uniform(-1.0, 1.0) for _ in range(3)])
                        .normalized() * (radius * n_offset))
    ut.assign_material(nucleus, materials["nucleus"])

    # Receptor markers, parented to the membrane because that is what they sit
    # on. Placed via the same surface function the membrane was built from, so
    # they land on the deformed surface rather than on the original sphere.
    receptors = []
    shared_mesh = _receptor_mesh()
    for i, direction in enumerate(_receptor_directions(receptor_count, rng)):
        point = _surface_point(shape, direction)
        point = point + direction * (RECEPTOR_RADIUS_UM * RECEPTOR_EMBED)
        marker = ut.get_or_create_object(
            "{}_{}_{:03d}_{:02d}".format(PREFIX, SUFFIX_RECEPTOR, index, i + 1),
            coll, lambda n, m=shared_mesh: m, location=point)
        ut.set_parent(marker, membrane)
        ut.assign_material(marker, materials["receptor"])
        marker[PROP_NOTE] = ILLUSTRATIVE_NOTE
        receptors.append(marker)

    return {
        "root": root,
        "membrane": membrane,
        "cytoplasm": cytoplasm,
        "nucleus": nucleus,
        "receptors": receptors,
        "morphology": morphology,
        "susceptibility_state": susceptibility_state,
    }


# ---------------------------------------------------------------------------
# Test population
# ---------------------------------------------------------------------------

#: Scratch collection, nested under 10_DEBUG so excluding it from an export is
#: structural rather than remembered.
TEST_COLLECTION = "TEST_TumorCells"
TEST_CELL_COUNT = 20
#: Cells are ~20 um across, so 23 um spacing with jitter packs them into a mass
#: the way a tumour is, rather than a tidy grid.
TEST_SPACING_UM = 23.0
TEST_JITTER_UM = 2.5


def build_test_cells(collection=None, count: int = TEST_CELL_COUNT,
                     clear: bool = True) -> list:
    """Generate a varied but coherent tumour population into TEST_TumorCells.

    Every cell gets its own seed, so the population spans all five
    morphologies and the full nucleus variation rather than repeating one
    design twenty times. Susceptibility states are spread deliberately instead
    of randomly, so the validator can confirm all three are represented.

    Returns:
        The list of per-cell dicts from :func:`create_tumor_cell`.
    """
    coll = collection or ut.get_or_create_collection(
        TEST_COLLECTION, ut.resolve_collection("10_DEBUG"))
    if clear:
        ut.clear_collection(coll, prefix=PREFIX)

    columns = 5
    states = tuple(SUSCEPTIBILITY_STATES)
    built = []
    for i in range(count):
        row, column = divmod(i, columns)
        built.append(create_tumor_cell(
            location=(
                (column - (columns - 1) / 2.0) * TEST_SPACING_UM,
                (row - 1.5) * TEST_SPACING_UM,
                (i % 3) * 1.4 - 1.4,
            ),
            rotation=(0.0, 0.0, i * 0.7),
            morphology_seed=2000 + i,
            # Round-robin over the three states so all are present.
            susceptibility_state=states[i % len(states)],
            index=i + 1,
            collection=coll,
        ))
    # Seeded positional jitter, applied after so it cannot perturb the
    # per-cell seeds that govern shape.
    rng = random.Random(4242)
    for cell in built:
        cell["root"].location.x += rng.uniform(-TEST_JITTER_UM, TEST_JITTER_UM)
        cell["root"].location.y += rng.uniform(-TEST_JITTER_UM, TEST_JITTER_UM)
    return built


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _lumpiness(mesh, aspect) -> float:
    """Stddev of vertex radius over mean radius, with the aspect divided out.

    Higher means lumpier. The aspect normalisation matters: measured on raw
    vertex positions, a flattened COMPRESSED cell scores as extremely lumpy
    purely because it is squashed, which would make the metric report shape
    variation as surface roughness and flag the whole population as
    incoherent. Removing the squash leaves the noise term only.
    """
    radii = []
    for vert in mesh.vertices:
        co = vert.co
        radii.append(Vector((co.x / aspect[0],
                             co.y / aspect[1],
                             co.z / aspect[2])).length)
    mean = sum(radii) / len(radii)
    if not mean:
        return 0.0
    variance = sum((r - mean) ** 2 for r in radii) / len(radii)
    return (variance ** 0.5) / mean


def _elongation(obj) -> float:
    """Longest axis over shortest. 1.0 is a sphere."""
    dims = sorted(obj.dimensions)
    return dims[-1] / dims[0] if dims[0] else 0.0


def _check_topology(obj) -> list:
    """Topology problems for one mesh: loose verts, degenerate faces, open
    edges, inward normals."""
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
    if any(len(e.link_faces) != 2 for e in bm.edges):
        problems.append("{}: has open or non-manifold edges".format(obj.name))
    if bm.calc_volume(signed=True) <= 0.0:
        problems.append("{}: normals point inward".format(obj.name))
    bm.free()
    return problems


def _check_reproducibility() -> list:
    """Same seed must reproduce a cell; different seeds must differ.

    Two separate properties, and easy to have one without the other: ignoring
    the seed gives twenty clones, deriving shape from something unseeded gives
    a cell that changes every run and cannot be reproduced.
    """
    problems = []

    def signature(seed):
        rng = random.Random(seed)
        shape = _make_shape(rng, RADIUS_UM, MORPHOLOGY_NAMES[rng.randrange(len(MORPHOLOGY_NAMES))])
        mesh = _organic_icosphere("MESH_TMP_Repro", shape, SUBDIVISIONS_SMALL)
        value = round(sum(v.co.length for v in mesh.vertices), 4)
        bpy.data.meshes.remove(mesh)
        return value

    if signature(7) != signature(7):
        problems.append("morphology_seed is not reproducible")
    if signature(7) == signature(8):
        problems.append("morphology_seed is ignored: different seeds gave identical cells")
    return problems


def validate_tumor_cells(collection=None, count: int = TEST_CELL_COUNT) -> dict:
    """Validate the generated population. Returns findings and any failures.

    Beyond the per-cell checks (topology, naming, materials, transforms), this
    asserts the two properties the brief is actually about: that the three
    susceptibility states differ only in marker count, and that twenty varied
    cells still read as one population.
    """
    coll = collection or ut.resolve_collection(TEST_COLLECTION)
    failures = []
    measurements = {"morphology": {}, "lumpiness": {}, "elongation": {},
                    "nucleus_ratio": {}, "receptor_count": {}}

    roots = [o for o in coll.objects if o.name.startswith(PREFIX) and o.type == "EMPTY"]
    meshes = [o for o in coll.objects if o.type == "MESH"]

    if len(roots) != count:
        failures.append("expected {} cells, found {}".format(count, len(roots)))

    # --- naming -----------------------------------------------------------
    for root in roots:
        if any(root.name == bad for bad in FORBIDDEN_NAMES):
            failures.append("{}: forbidden generic name".format(root.name))
        if len(root.name) > len(PREFIX) + 4 and root.name[-4] == "." and root.name[-3:].isdigit():
            failures.append("{}: has a .001 duplicate suffix".format(root.name))

    # --- topology ---------------------------------------------------------
    for obj in meshes:
        failures.extend(_check_topology(obj))

    # --- materials --------------------------------------------------------
    expected_mat = {
        SUFFIX_MEMBRANE: "MAT_Tumor_Membrane",
        SUFFIX_CYTOPLASM: "MAT_Tumor_Cytoplasm",
        SUFFIX_NUCLEUS: "MAT_Tumor_Nucleus",
        SUFFIX_RECEPTOR: "MAT_Tumor_Receptor_Illustrative",
    }
    for obj in meshes:
        match = PART_PATTERN.match(obj.name)
        if match is None:
            failures.append("{}: name does not match CELL_TUMOR_<PART>_<NNN>".format(obj.name))
            continue
        role = match.group("part").split("_")[0]
        if len(obj.data.materials) != 1:
            failures.append("{}: expected 1 material slot, found {}".format(
                obj.name, len(obj.data.materials)))
            continue
        actual = obj.data.materials[0].name
        if actual != expected_mat.get(role):
            failures.append("{}: material is {!r}, expected {!r}".format(
                obj.name, actual, expected_mat.get(role)))
        if not actual.startswith("MAT_Tumor_"):
            failures.append("{}: material {!r} breaks the MAT_Tumor_* rule".format(obj.name, actual))

    # --- transforms, morphology coverage, per-cell measurements ------------
    states_seen = set()
    for root in roots:
        state = root.get(PROP_STATE)
        morphology = root.get(PROP_MORPHOLOGY)
        measurements["morphology"][root.name] = morphology
        measurements["receptor_count"][root.name] = root.get(PROP_RECEPTOR_COUNT)
        if state not in SUSCEPTIBILITY_STATES:
            failures.append("{}: bad or missing {!r}".format(root.name, PROP_STATE))
        else:
            states_seen.add(state)
            if root.get(PROP_RECEPTOR_COUNT) != SUSCEPTIBILITY_STATES[state]:
                failures.append("{}: {!r} does not match state {!r}".format(
                    root.name, PROP_RECEPTOR_COUNT, state))
        if morphology not in MORPHOLOGIES:
            failures.append("{}: bad or missing {!r}".format(root.name, PROP_MORPHOLOGY))
        if root.get(PROP_NOTE) != ILLUSTRATIVE_NOTE:
            failures.append("{}: missing the illustrative receptor label".format(root.name))

        parts = {}
        for obj in coll.objects:
            if obj.type != "MESH":
                continue
            match = PART_PATTERN.match(obj.name)
            if not match:
                continue
            role = match.group("part")
            if role != SUFFIX_RECEPTOR and obj.parent == root:
                parts[role] = obj

        for required in (SUFFIX_MEMBRANE, SUFFIX_CYTOPLASM, SUFFIX_NUCLEUS):
            if required not in parts:
                failures.append("{}: missing part {}".format(root.name, required))

        membrane = parts.get(SUFFIX_MEMBRANE)
        nucleus = parts.get(SUFFIX_NUCLEUS)

        if membrane is not None:
            aspect = tuple(root.get(PROP_ASPECT) or (1.0, 1.0, 1.0))
            measurements["lumpiness"][root.name] = round(_lumpiness(membrane.data, aspect), 4)
            measurements.setdefault("lumpiness_by_morphology", {}).setdefault(
                morphology, []).append(measurements["lumpiness"][root.name])
            measured_elongation = _elongation(membrane)
            measurements["elongation"][root.name] = round(measured_elongation, 3)

            # Validate each cell against its OWN morphology's declared band
            # rather than one global number. A flattened COMPRESSED cell
            # legitimately has a high bounding-box aspect -- that is what
            # flattened means -- so a single global limit would flag ordinary
            # biology as extreme. What must hold is that no cell strays far
            # from the family it claims to belong to.
            if morphology in MORPHOLOGIES:
                declared = MORPHOLOGIES[morphology]["aspect"]
                expected = max(declared) / min(declared)
                tolerance = MORPH_JITTER_ASPECT * 2 + 0.06
                if abs(measured_elongation - expected) / expected > tolerance:
                    failures.append(
                        "{}: elongation {:.2f} strays from {} band ~{:.2f}".format(
                            root.name, measured_elongation, morphology, expected))

            mod = membrane.modifiers.get("MOD_MembraneDeform")
            if mod is None:
                failures.append("{}: membrane deformation modifier missing".format(membrane.name))
            elif mod.texture_coords_object != root:
                failures.append("{}: deformation not bound to its own root".format(membrane.name))

        if membrane is not None and nucleus is not None:
            m_radius = max(membrane.dimensions) / 2.0
            n_radius = max(nucleus.dimensions) / 2.0
            ratio = n_radius / m_radius
            measurements["nucleus_ratio"][root.name] = round(ratio, 3)
            if not (NUCLEUS_RATIO_RANGE[0] * 0.85 <= ratio <= NUCLEUS_RATIO_RANGE[1] * 1.15):
                failures.append("{}: nucleus ratio {:.2f} outside the declared range {}".format(
                    root.name, ratio, NUCLEUS_RATIO_RANGE))

    # --- receptors: label, shared mesh, identical geometry across states ---
    all_receptors = [o for o in meshes
                     if PART_PATTERN.match(o.name)
                     and PART_PATTERN.match(o.name).group("part") == SUFFIX_RECEPTOR]
    if not all_receptors:
        failures.append("no receptor markers found")

    shared_meshes = {r.data.name for r in all_receptors}
    if len(shared_meshes) != 1:
        failures.append("receptors do not share one mesh datablock: {}".format(sorted(shared_meshes)))

    for marker in all_receptors:
        if marker.get(PROP_NOTE) != ILLUSTRATIVE_NOTE:
            failures.append("{}: missing the illustrative receptor label".format(marker.name))
            break  # one report is enough; they are all built the same way

    per_state_counts = {}
    for root in roots:
        state = root.get(PROP_STATE)
        actual = sum(1 for r in all_receptors if r.get(PROP_NOTE) and _belongs_to(r, root))
        per_state_counts.setdefault(state, []).append(actual)
    for state, counts in per_state_counts.items():
        if state in SUSCEPTIBILITY_STATES and any(
                c != SUSCEPTIBILITY_STATES[state] for c in counts):
            failures.append("{}: receptor counts {} do not all equal {}".format(
                state, sorted(set(counts)), SUSCEPTIBILITY_STATES[state]))

    if len(states_seen) != len(SUSCEPTIBILITY_STATES):
        failures.append("population does not cover all three states: saw {}".format(
            sorted(states_seen)))

    morphs_seen = {m for m in measurements["morphology"].values() if m}
    if morphs_seen != set(MORPHOLOGY_NAMES):
        failures.append("population does not cover all five morphologies: saw {}".format(
            sorted(morphs_seen)))

    # --- population coherence ---------------------------------------------
    # The brief's real requirement: twenty varied cells that still read as one
    # population. Bounded spread on every axis, so nothing is an extreme outlier.
    radii = [max(coll.objects[root.name + "_MEMBRANE_{:03d}".format(i)].dimensions) / 2.0
             for root in roots
             for i in [int(root.name.rsplit("_", 1)[1])]
             if root.name + "_MEMBRANE_{:03d}".format(i) in coll.objects]
    if radii:
        spread = max(radii) / min(radii)
        if spread > 1.45:
            failures.append("population size spread {:.2f} too wide to read as one tumour".format(spread))
        measurements["size_spread"] = round(spread, 3)

    elong = [v for v in measurements["elongation"].values() if v]
    if elong:
        # Loose global sanity bound only. The precise check is per-morphology,
        # above; this just catches a cell that somehow left the declared range
        # entirely.
        if max(elong) > 2.4:
            failures.append("a cell is over-extended (elongation {:.2f})".format(max(elong)))
        measurements["elongation_range"] = (round(min(elong), 3), round(max(elong), 3))

    lump = [v for v in measurements["lumpiness"].values() if v]
    by_morph = measurements.get("lumpiness_by_morphology", {})
    if lump:
        measurements["lumpiness_range"] = (round(min(lump), 4), round(max(lump), 4))

        # Coherence is checked *within* each morphology family, not across the
        # whole population. A wide cross-family spread is exactly what the
        # brief asked for -- variation is how tumour identity is communicated
        # -- so failing on it would be failing the design. What must hold is
        # that the cells of one family genuinely resemble each other, and that
        # a cell is not an outlier from the family it claims to belong to.
        #
        # The absolute numbers are not checked against a global bound on
        # purpose: roughness combines the noise term and, for ASYMMETRIC, the
        # one-sided bulge, so an absolute limit would flag the bulge as if it
        # were wild surface noise.
        for morphology, values in by_morph.items():
            mean = sum(values) / len(values)
            measurements.setdefault("lumpiness_mean", {})[morphology] = round(mean, 4)
            for value in values:
                if mean and not (0.5 * mean <= value <= 2.0 * mean):
                    failures.append(
                        "{}: roughness {:.4f} is an outlier for {} (family mean {:.4f})".format(
                            morphology, value, morphology, mean))

        # The declared families must actually produce different surfaces, or
        # the variation is cosmetic. These orderings hold by construction
        # given the declared irregularity values and the jitter bounds.
        means = {m: sum(v) / len(v) for m, v in by_morph.items()}
        for rougher, smoother in (("IRREGULAR", "ROUND"), ("IRREGULAR", "COMPRESSED"),
                                  ("ASYMMETRIC", "ROUND")):
            if rougher in means and smoother in means:
                if means[rougher] <= means[smoother] * 1.4:
                    failures.append(
                        "{} did not come out rougher than {} ({:.4f} vs {:.4f}); "
                        "the declared morphology variation is not being applied".format(
                            rougher, smoother, means[rougher], means[smoother]))

    # --- seeded variation -------------------------------------------------
    failures.extend(_check_reproducibility())

    scene = bpy.context.scene
    if abs(scene.unit_settings.scale_length - ut.SCENE_SCALE_LENGTH) > 1e-12:
        failures.append("scene scale_length is not 1e-6; micrometre convention broken")

    return {
        "ok": not failures,
        "failures": failures,
        "checks": {
            "cells": len(roots),
            "meshes": len(meshes),
            "receptors": len(all_receptors),
            "shared_receptor_meshes": sorted(shared_meshes),
            "materials": sorted({m.name for o in meshes for m in o.data.materials}),
            "morphologies": sorted(morphs_seen),
            "states": sorted(states_seen),
        },
        "measurements": measurements,
    }


def _belongs_to(obj, root) -> bool:
    """True if *obj* is a receptor on the cell owned by *root*.

    Receptors hang off the membrane, not the root, so this walks one level up.
    """
    return obj.parent is not None and obj.parent.parent == root


def main() -> None:
    """Generate the test population, validate it, and report.

    Establishes the micrometre unit convention first: this asset's dimensions
    are only meaningful under it, and the validator asserts it.
    """
    ut.setup_units()
    build_test_cells()
    report = validate_tumor_cells()

    print("[tumor_cell] built {} cells in {}".format(
        report["checks"]["cells"], TEST_COLLECTION))
    print("[tumor_cell] checks: {}".format(report["checks"]))
    for key in ("size_spread", "elongation_range", "lumpiness_range", "lumpiness_by_morphology"):
        if key in report["measurements"]:
            print("[tumor_cell] {}: {}".format(key, report["measurements"][key]))
    if report["ok"]:
        print("[tumor_cell] VALIDATION PASSED")
    else:
        for failure in report["failures"]:
            print("[tumor_cell] FAIL: {}".format(failure))
        raise SystemExit("[tumor_cell] VALIDATION FAILED ({} problems)".format(
            len(report["failures"])))


if __name__ == "__main__":
    main()
