"""HSV-1 virion: envelope, glycoproteins, tegument, icosahedral capsid, genome.

Scientific context
------------------
T-VEC (talimogene laherparepvec) is an engineered HSV-1. This model is an
**educational representation of HSV-1-like architecture**, not an
atomic-resolution reconstruction. Nothing here resolves a capsomere, a lipid
molecule, or a base pair, and the genome is not an HSV sequence.

Overall scale is roughly right: the virion is ~200 nm across, so the envelope
radius is 0.1 BU and the capsid ~125 nm across. It is still a very small
object in micrometre units, which is exactly why ``CAMERA_Virus`` exists and
why every camera carries a 0.01 near-clip.

Layers, outermost first
-----------------------
    VIRUS_HSV1_Envelope_001        lipid bilayer, subtle irregularity
    VIRUS_HSV1_Glycoproteins_001_* membrane-embedded surface proteins
    VIRUS_HSV1_Tegument_001_*      protein-rich layer between capsid and envelope
    VIRUS_HSV1_Capsid_001          icosahedral protein shell
    VIRUS_HSV1_Genome_001_*        densely packed abstract dsDNA

Three of those five layers are *distributions of elements* rather than single
shells, and are built as instanced object sets sharing one mesh datablock each.
That is the instanced equivalent of the single merged layer object the brief
names: a virion with 42 glycoprotein pins, 110 tegument elements and 180
genome segments would be 330 duplicated meshes if built by duplication, and
three meshes if built this way. The validator asserts each set shares exactly
one datablock, so the saving cannot be quietly lost.

Capsid
------
**Flat-shaded and genuinely icosahedral.** A smooth sphere would be the single
easiest way to ruin this asset, so the capsid is built as an icosphere at
:data:`CAPSID_SUBDIVISIONS` and deliberately *not* smooth shaded. The facets
are the read. The validator checks both the face count and that no face is
smooth-shaded, because a later "tidy up the normals" pass would otherwise
silently destroy the one thing this layer exists to show.

The real capsid is T=16: 12 pentons and 150 hexons, 162 capsomeres. A geodesic
icosphere is a clean icosahedral structure but not a capsomere lattice, and
building a proper Goldberg polyhedron is a larger piece of work than this
asset warrants. That fidelity gap is deliberate and recorded.

Tegument
--------
An irregular protein-rich layer, deliberately **not** another smooth shell: a
second perfect sphere between the capsid and the envelope would read as a
modelling artefact rather than as protein. It is a seeded distribution of small
elements filling the space between.

Glycoproteins
-------------
Abstract membrane-embedded protrusions. They **vary**: seeded per-instance
scale and a slight tilt off the surface normal, from one shared base mesh, so
they share a general shape without being identical spikes.

**No individual spike is labelled as a specific HSV glycoprotein.** Real HSV-1
has named envelope proteins (gB, gC, gD, gH, gK, gL) with distinct roles, and
modelling or naming any of them would assert a specificity this asset does not
have. The validator greps every object and material name for those designators
and fails if one appears, so a future edit cannot quietly introduce a claim the
model does not support.

Genome
------
An abstract dense fill suggesting packaged DNA. Not a sequence, not a
histogram, and deliberately not a literal double helix -- the genome in a
real capsid is nowhere near that shape. It reads as dense granular material
packed into the core, which is what it is standing in for.

Two versions
------------
``exploded=False`` is the intact virion, all layers concentric at their true
radii.

``exploded=True`` separates the five layers along one shared axis, ordered
envelope at top down to genome, per the brief, with every layer staying on that
axis so the structure reads. This is the version where the interior is meant to
be read; the intact version only hints at it through a translucent capsid.

Run standalone::

    blender --background --python blender/scripts/asset_virus_hsv1.py
"""

from __future__ import annotations

import math
import os
import random
import re
import sys

import bpy
import bmesh
from mathutils import Matrix, Vector, noise

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utilities as ut  # noqa: E402

# ---------------------------------------------------------------------------
# Contract constants
# ---------------------------------------------------------------------------

COLLECTION = "03_VIRUSES"

PREFIX = "VIRUS_HSV1"
SUFFIX_ENVELOPE = "Envelope"
SUFFIX_GLYCOPROTEIN = "Glycoproteins"
SUFFIX_TEGUMENT = "Tegument"
SUFFIX_CAPSID = "Capsid"
SUFFIX_GENOME = "Genome"

#: Radius order, outermost first, in micrometres == Blender units.
#: Envelope 0.1 BU radius = 200 nm virion across; capsid 0.0625 = 125 nm.
RADIUS_ENVELOPE = 0.100
RADIUS_TEGUMENT_OUTER = 0.090
RADIUS_TEGUMENT_INNER = 0.066
RADIUS_CAPSID = 0.0625
RADIUS_GENOME = 0.030

#: The capsid is the whole point of this asset, so it is built at low
#: subdivision and left flat shaded. 1 gives the true 20-face icosahedron,
#: 2 gives 80 facets. Do not raise this: more subdivision plus smooth shading
#: trends back toward a sphere.
CAPSID_SUBDIVISIONS = 2

ENVELOPE_SUBDIVISIONS = 4

#: Envelope is "subtly irregular" per the brief. Keep it that way -- a lumpy
#: envelope reads as damage rather than as a lipid bilayer.
IRREGULARITY_ENVELOPE = 0.022
NOISE_SCALE_COARSE = 1.9
NOISE_SCALE_FINE = 4.3
NOISE_FINE_WEIGHT = 0.45

#: Glycoproteins: one shared base mesh, seeded per-instance scale and tilt so
#: they are not identical spikes while keeping a consistent general shape.
GLYCOPROTEIN_COUNT = 42
GLYCOPROTEIN_LENGTH = 0.024
GLYCOPROTEIN_RADIUS = 0.0055
GLYCOPROTEIN_SCALE_RANGE = (0.80, 1.25)
#: How far a pin's centre sits outside the envelope surface. Negative would
#: bury it; this puts the base inside the bilayer so it reads as embedded.
GLYCOPROTEIN_EMBED = 0.004
GLYCOPROTEIN_TILT_DEG = 16.0

#: Tegument: a seeded fill between capsid and envelope, not a shell.
#: Count and size are set so the elements stay a *layer*. Crowding them into a
#: solid ring of beads makes the tegument compete with the capsid, and the
#: capsid's icosahedral facets are the single thing this asset most needs to
#: show. Half the original count at two thirds the radius reads as a
#: protein-rich layer and lets the capsid through.
TEGUMENT_COUNT = 55
TEGUMENT_RADIUS = 0.0055
TEGUMENT_SUBDIVISIONS = 2

#: Genome: dense abstract fill inside the capsid core. Deliberately not a
#: sequence and not a literal helix.
GENOME_COUNT = 180
GENOME_RADIUS = 0.0016
GENOME_SEGMENT_LENGTH = 0.009
GENOME_SUBDIVISIONS = 1

SIZE_JITTER = 0.05

#: Explode offsets along the virion's local +Z, ordered envelope at top down to
#: genome, matching the brief's reading order. The intact version uses 0.
EXPLODE_OFFSETS = {
    SUFFIX_ENVELOPE: 0.30,
    SUFFIX_GLYCOPROTEIN: 0.18,
    SUFFIX_TEGUMENT: 0.06,
    SUFFIX_CAPSID: -0.06,
    SUFFIX_GENOME: -0.18,
}

#: Real HSV-1 envelope protein designators. None of these may ever appear in an
#: object or material name from this module: the pins are deliberately abstract
#: and naming one would claim a specificity the model does not have.
FORBIDDEN_DESIGNATORS = (
    "gB", "gC", "gD", "gE", "gH", "gI", "gJ", "gK", "gL", "gM",
    "UL", "VP", "ICP", "US", "TK", "thymidine_kinase",
)

#: Face counts a real icosphere produces, for the capsid facet assertion.
ICOSAHEDRON_FACES = {1: 20, 2: 80, 3: 320, 4: 1280}

PART_PATTERN = re.compile(
    r"^{}_(?P<part>{})_(?P<index>\d{{3}})(?:_(?P<sub>\d{{2,3}}))?$".format(
        re.escape(PREFIX),
        "|".join(re.escape(s) for s in (
            SUFFIX_ENVELOPE, SUFFIX_GLYCOPROTEIN, SUFFIX_TEGUMENT,
            SUFFIX_CAPSID, SUFFIX_GENOME))))

PROP_EXPLODED = "exploded"
PROP_NOTE = "representation_note"

#: Carried on the root, so the "not a reconstruction" framing travels with the
#: asset into any export rather than living only in this docstring.
ILLUSTRATIVE_NOTE = ("Educational representation of HSV-1-like architecture; "
                     "not an atomic-resolution reconstruction.")


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

MAT_ENVELOPE = dict(
    name="MAT_Virus_Envelope",
    base_color=(0.62, 0.58, 0.38, 1.0),
    roughness=0.30,
    subsurface=0.22,
    alpha=0.18,
    emission=(0.90, 0.85, 0.55, 1.0),
)

#: Cool against the warm envelope, so pins read as proteins rather than as
#: more envelope. Opaque: a translucent pin disappears at virion scale.
MAT_GLYCOPROTEIN = dict(
    name="MAT_Virus_Glycoprotein",
    base_color=(0.55, 0.85, 0.92, 1.0),
    roughness=0.28,
    alpha=1.0,
    emission=(0.60, 0.95, 1.00, 1.0),
)

MAT_TEGUMENT = dict(
    name="MAT_Virus_Tegument",
    base_color=(0.55, 0.50, 0.42, 1.0),
    roughness=0.55,
    alpha=0.88,
    emission=(0.85, 0.78, 0.60, 1.0),
)

#: Slate blue, separating it from both the warm envelope and the warm tegument.
#: Semi-transparent so the genome hints through in the intact version; the
#: facets still read because alpha does not affect surface shading.
MAT_CAPSID = dict(
    name="MAT_Virus_Capsid",
    base_color=(0.45, 0.60, 0.78, 1.0),
    roughness=0.34,
    subsurface=0.10,
    alpha=0.45,
    emission=(0.55, 0.80, 1.00, 1.0),
)

MAT_GENOME = dict(
    name="MAT_Virus_Genome",
    base_color=(0.90, 0.95, 1.00, 1.0),
    roughness=0.25,
    alpha=1.0,
    emission=(0.95, 0.98, 1.00, 1.0),
)


def _material(name, color, roughness, alpha, emission, subsurface=0.0,
              transparent=True):
    """Create or update one material, configuring EEVEE transparency too.

    Cycles honours Principled alpha directly, but EEVEE and the glTF viewer need
    ``surface_render_method`` set to BLENDED or a low-alpha surface renders
    essentially opaque.
    """
    mat = ut.principled_material(name, base_color=color, roughness=roughness,
                                 subsurface=subsurface, alpha=alpha,
                                 emission=emission, emission_strength=0.0)
    if transparent:
        mat.surface_render_method = "BLENDED"
        mat.use_backface_culling = False
    return mat


def ensure_materials() -> dict:
    """Create all five viral materials, keyed by layer role."""
    return {
        "envelope": _material(MAT_ENVELOPE["name"], MAT_ENVELOPE["base_color"],
                              MAT_ENVELOPE["roughness"], MAT_ENVELOPE["alpha"],
                              MAT_ENVELOPE["emission"], MAT_ENVELOPE["subsurface"]),
        "glycoprotein": _material(MAT_GLYCOPROTEIN["name"], MAT_GLYCOPROTEIN["base_color"],
                                  MAT_GLYCOPROTEIN["roughness"],
                                  MAT_GLYCOPROTEIN["alpha"],
                                  MAT_GLYCOPROTEIN["emission"], transparent=False),
        "tegument": _material(MAT_TEGUMENT["name"], MAT_TEGUMENT["base_color"],
                              MAT_TEGUMENT["roughness"], MAT_TEGUMENT["alpha"],
                              MAT_TEGUMENT["emission"]),
        "capsid": _material(MAT_CAPSID["name"], MAT_CAPSID["base_color"],
                            MAT_CAPSID["roughness"], MAT_CAPSID["alpha"],
                            MAT_CAPSID["emission"], MAT_CAPSID["subsurface"]),
        "genome": _material(MAT_GENOME["name"], MAT_GENOME["base_color"],
                            MAT_GENOME["roughness"], MAT_GENOME["alpha"],
                            MAT_GENOME["emission"], transparent=False),
    }


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def _envelope_factor(direction, state):
    """Radial factor for the envelope surface. Subtle by design."""
    factor = 1.0
    factor += IRREGULARITY_ENVELOPE * noise.noise(
        direction * NOISE_SCALE_COARSE + state["phase_coarse"])
    factor += IRREGULARITY_ENVELOPE * NOISE_FINE_WEIGHT * noise.noise(
        direction * NOISE_SCALE_FINE + state["phase_fine"])
    return factor / state["norm"]


def _make_state(rng, radius):
    """Envelope shape state, with the factor normalised by its own mean.

    Normalising keeps the declared envelope radius true as a mean radius
    instead of letting the noise and the sampling bias it -- the same lesson
    the macrophage taught.
    """
    state = {
        "radius": radius,
        "phase_coarse": Vector([rng.uniform(-40.0, 40.0) for _ in range(3)]),
        "phase_fine": Vector([rng.uniform(-40.0, 40.0) for _ in range(3)]),
        "norm": 1.0,
    }
    total = 0.0
    samples = 256
    for _ in range(samples):
        direction = Vector([rng.uniform(-1.0, 1.0) for _ in range(3)]).normalized()
        factor = 1.0
        factor += IRREGULARITY_ENVELOPE * noise.noise(
            direction * NOISE_SCALE_COARSE + state["phase_coarse"])
        factor += IRREGULARITY_ENVELOPE * NOISE_FINE_WEIGHT * noise.noise(
            direction * NOISE_SCALE_FINE + state["phase_fine"])
        total += factor
    state["norm"] = max(total / samples, 1e-6)
    return state


def _envelope_mesh(name, state):
    """Closed, smooth-shaded, subtly irregular envelope shell."""
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=ENVELOPE_SUBDIVISIONS, radius=1.0)
    for vert in bm.verts:
        vert.co = vert.co.normalized() * (state["radius"] * _envelope_factor(
            vert.co.normalized(), state))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.shade_smooth()
    return mesh


def _capsid_mesh(name, radius):
    """The capsid: a real icosahedral shell, deliberately NOT smooth shaded.

    Flat facets are the entire read for this layer. Every other mesh in the
    project is smooth shaded; this one must not be, and the validator asserts
    it, because a "tidy up the normals" pass would silently destroy the only
    thing the capsid is for.
    """
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=CAPSID_SUBDIVISIONS, radius=radius)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    for polygon in mesh.polygons:
        polygon.use_smooth = False
    return mesh


def _element_mesh(name, radius, subdivisions, length=None):
    """A small element mesh: a sphere, or an elongated rod when *length* is set."""
    bm = bmesh.new()
    if length is None:
        bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=radius)
    else:
        # A rod along +Z, built as a cone so it stays cheap at high counts.
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=True,
                              segments=max(4, subdivisions * 4),
                              radius1=radius, radius2=radius, depth=length)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.shade_smooth()
    return mesh


def _shared_element_mesh(key, name, radius, subdivisions, length=None):
    """Get or create one element mesh per layer, shared across every virion."""
    mesh = bpy.data.meshes.get(name)
    if mesh is None:
        mesh = _element_mesh(name, radius, subdivisions, length)
    return mesh


def _fibre_directions(count, rng, min_cos=-1.0, max_cos=1.0):
    """Evenly spread unit directions via a Fibonacci spiral, with seeded jitter.

    Random directions clump, and a clumped distribution reads as a mistake --
    on a virion that is only 0.2 BU across, clumping is very visible.
    """
    golden = math.pi * (3.0 - math.sqrt(5.0))
    spin = rng.uniform(0.0, 2.0 * math.pi)
    out = []
    for i in range(count):
        z = min_cos + (max_cos - min_cos) * (i + 0.5) / count
        r = math.sqrt(max(0.0, 1.0 - z * z))
        theta = golden * i + spin + rng.uniform(-0.25, 0.25)
        out.append(Vector((math.cos(theta) * r, math.sin(theta) * r, z)))
    return out


def _random_unit(rng):
    return Vector([rng.uniform(-1.0, 1.0) for _ in range(3)]).normalized()


def _point_in_ball(rng, radius, bias=0.65):
    """Uniform-ish point inside a ball, biased toward the centre.

    Concentrating elements near the middle matches how the genome is actually
    packed and avoids an unnaturally hollow shell.
    """
    direction = _random_unit(rng)
    return direction * (radius * (rng.random() ** bias))


def _tilt_matrix(direction, rng, max_deg):
    """Orientation with +Z along *direction*, then tilted off it by a seeded angle."""
    basis = direction.to_track_quat("Z", "Y").to_matrix().to_4x4()
    if max_deg <= 0.0:
        return basis
    axis = _random_unit(rng)
    spin = Matrix.Rotation(math.radians(max_deg * rng.uniform(0.2, 1.0)), 4, axis)
    return spin @ basis


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_hsv1_virion(location=(0.0, 0.0, 0.0), scale: float = 1.0,
                       rotation=(0.0, 0.0, 0.0), exploded: bool = False, *,
                       seed: int = 0, index: int = 1, collection=None,
                       parent=None, name_prefix: str = PREFIX) -> dict:
    """Create one HSV-1 virion: intact, or exploded as a structural diagram.

    Args:
        location: World-space location of the virion centre.
        scale: Uniform size multiplier on the declared radii.
        rotation: Euler rotation in radians, applied to the root so the whole
            virion turns coherently. In exploded mode the whole diagram rotates
            together.
        exploded: ``False`` for the intact virion, ``True`` to separate the five
            layers along the local +Z axis for a structural diagram.
        seed: Controls envelope irregularity and every element's placement,
            scale and tilt.
        index: 1-based ordinal used for the object names.
        collection: Target collection. Defaults to ``03_VIRUSES``.
        parent: Optional parent object, normally ``CTRL_Virus``.
        name_prefix: Object name prefix, normally ``VIRUS_HSV1``.

    Returns:
        ``{"root", "envelope", "glycoproteins", "tegument", "capsid",
        "genome", "exploded"}``. *root* is the object to animate.
    """
    coll = collection or ut.resolve_collection(COLLECTION)
    rng = random.Random(seed)
    size = scale * (1.0 + rng.uniform(-SIZE_JITTER, SIZE_JITTER))

    root = ut.new_empty(ut.obj_name(name_prefix, index), coll,
                        location=location, parent=parent, size=0.4 * size)
    root.rotation_euler = rotation
    root[PROP_EXPLODED] = bool(exploded)
    root[PROP_NOTE] = ILLUSTRATIVE_NOTE

    materials = ensure_materials()
    offsets = EXPLODE_OFFSETS if exploded else {k: 0.0 for k in EXPLODE_OFFSETS}

    # --- envelope ---------------------------------------------------------
    state = _make_state(rng, RADIUS_ENVELOPE * size)
    envelope = ut.get_or_create_object(
        ut.obj_name("{}_{}".format(name_prefix, SUFFIX_ENVELOPE), index), coll,
        lambda n: _envelope_mesh("MESH_{}_{}".format(name_prefix, SUFFIX_ENVELOPE), state))
    ut.set_parent(envelope, root)
    ut.assign_material(envelope, materials["envelope"])
    envelope.location = (0.0, 0.0, offsets[SUFFIX_ENVELOPE] * size)

    # --- glycoproteins ----------------------------------------------------
    # Parented to the root, not the envelope, so they can separate from it in
    # the exploded version. Their positions are still computed on the envelope
    # surface, so they read as a surface layer in both versions.
    pin_mesh = _shared_element_mesh(
        "glycoprotein",
        "MESH_{}_{}_SHARED".format(name_prefix, SUFFIX_GLYCOPROTEIN),
        GLYCOPROTEIN_RADIUS * size, 2, length=GLYCOPROTEIN_LENGTH * size)
    glycoproteins = []
    for i, direction in enumerate(_fibre_directions(GLYCOPROTEIN_COUNT, rng)):
        point = direction * (state["radius"] * _envelope_factor(direction, state))
        point = point + direction * GLYCOPROTEIN_EMBED * size
        pin = ut.get_or_create_object(
            "{}_{}_{:03d}_{:02d}".format(name_prefix, SUFFIX_GLYCOPROTEIN, index, i + 1),
            coll, lambda n, m=pin_mesh: m, location=point)
        pin.rotation_euler = _tilt_matrix(
            direction, rng, GLYCOPROTEIN_TILT_DEG).to_euler()
        s = rng.uniform(*GLYCOPROTEIN_SCALE_RANGE)
        pin.scale = (s, s, s)
        pin.location.z += offsets[SUFFIX_GLYCOPROTEIN] * size
        ut.set_parent(pin, root)
        ut.assign_material(pin, materials["glycoprotein"])
        glycoproteins.append(pin)

    # --- tegument ---------------------------------------------------------
    # A seeded fill of small elements between capsid and envelope, not a shell.
    tegument_mesh = _shared_element_mesh(
        "tegument",
        "MESH_{}_{}_SHARED".format(name_prefix, SUFFIX_TEGUMENT),
        TEGUMENT_RADIUS * size, TEGUMENT_SUBDIVISIONS)
    mid = (RADIUS_TEGUMENT_INNER + RADIUS_TEGUMENT_OUTER) / 2.0
    half_band = (RADIUS_TEGUMENT_OUTER - RADIUS_TEGUMENT_INNER) / 2.0
    tegument = []
    for i in range(TEGUMENT_COUNT):
        direction = _random_unit(rng)
        radius = mid + rng.uniform(-half_band, half_band) * 0.8
        element = ut.get_or_create_object(
            "{}_{}_{:03d}_{:02d}".format(name_prefix, SUFFIX_TEGUMENT, index, i + 1),
            coll, lambda n, m=tegument_mesh: m, location=direction * (radius * size))
        s = rng.uniform(0.75, 1.35)
        element.scale = (s, s, s)
        element.location.z += offsets[SUFFIX_TEGUMENT] * size
        ut.set_parent(element, root)
        ut.assign_material(element, materials["tegument"])
        tegument.append(element)

    # --- capsid -----------------------------------------------------------
    capsid = ut.get_or_create_object(
        ut.obj_name("{}_{}".format(name_prefix, SUFFIX_CAPSID), index), coll,
        lambda n: _capsid_mesh("MESH_{}_{}".format(name_prefix, SUFFIX_CAPSID),
                               RADIUS_CAPSID * size))
    ut.set_parent(capsid, root)
    ut.assign_material(capsid, materials["capsid"])
    capsid.location = (0.0, 0.0, offsets[SUFFIX_CAPSID] * size)

    # --- genome -----------------------------------------------------------
    genome_mesh = _shared_element_mesh(
        "genome", "MESH_{}_{}_SHARED".format(name_prefix, SUFFIX_GENOME),
        GENOME_RADIUS * size, GENOME_SUBDIVISIONS,
        length=GENOME_SEGMENT_LENGTH * size)
    genome = []
    for i in range(GENOME_COUNT):
        segment = ut.get_or_create_object(
            "{}_{}_{:03d}_{:03d}".format(name_prefix, SUFFIX_GENOME, index, i + 1),
            coll, lambda n, m=genome_mesh: m,
            location=_point_in_ball(rng, RADIUS_GENOME * size))
        segment.rotation_euler = _tilt_matrix(_random_unit(rng), rng, 180.0).to_euler()
        s = rng.uniform(0.6, 1.4)
        segment.scale = (s, s, s)
        segment.location.z += offsets[SUFFIX_GENOME] * size
        ut.set_parent(segment, root)
        ut.assign_material(segment, materials["genome"])
        genome.append(segment)

    return {
        "root": root,
        "envelope": envelope,
        "glycoproteins": glycoproteins,
        "tegument": tegument,
        "capsid": capsid,
        "genome": genome,
        "exploded": exploded,
    }


# ---------------------------------------------------------------------------
# Test collection
# ---------------------------------------------------------------------------

TEST_COLLECTION = "TEST_HSV1_Virions"
#: Intact then exploded, side by side. The exploded one is scaled up: the
#: layers are separated by 0.3 BU, so at true scale the diagram needs the same
#: camera distance as the intact virion but reads much smaller.
TEST_SPACING = 0.9
TEST_EXPLODED_SCALE = 2.2


def build_test_virions(collection=None, clear: bool = True) -> list:
    """Generate one intact and one exploded virion into TEST_HSV1_Virions."""
    coll = collection or ut.get_or_create_collection(
        TEST_COLLECTION, ut.resolve_collection("10_DEBUG"))
    if clear:
        ut.clear_collection(coll, prefix=PREFIX)

    return [
        create_hsv1_virion(location=(-TEST_SPACING, 0.0, 0.0), scale=1.0,
                           rotation=(0.0, 0.0, 0.0), exploded=False,
                           seed=71, index=1, collection=coll),
        create_hsv1_virion(location=(TEST_SPACING, 0.0, 0.0),
                           scale=TEST_EXPLODED_SCALE,
                           rotation=(0.0, 0.0, 0.0), exploded=True,
                           seed=71, index=2, collection=coll),
    ]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _mean_radius(mesh) -> float:
    if not mesh.vertices:
        return 0.0
    return sum(v.co.length for v in mesh.vertices) / len(mesh.vertices)


def layer_radius(obj, layer, built_scale, exploded) -> float:
    """Distance of an element from the virion axis, ignoring the explode shift.

    In the exploded version every layer is translated along +Z, so a raw 3D
    distance from the origin folds that translation into what should be a
    purely radial measurement and every radial check fails. Subtracting the
    known offset first makes the measurement correct for both versions.
    """
    local = Vector(obj.location)
    if exploded:
        local.z -= EXPLODE_OFFSETS[layer] * built_scale
    return local.length


def _check_topology(obj) -> list:
    problems = []
    mesh = obj.data
    if not mesh.vertices or not mesh.polygons:
        return ["{}: empty mesh".format(obj.name)]
    bm = bmesh.new()
    bm.from_mesh(mesh)
    if any(not v.link_faces for v in bm.verts):
        problems.append("{}: has loose verts".format(obj.name))
    if any(f.calc_area() < 1e-14 for f in bm.faces):
        problems.append("{}: has degenerate faces".format(obj.name))
    if bm.calc_volume(signed=True) <= 0.0:
        problems.append("{}: normals point inward or volume is zero".format(obj.name))
    bm.free()
    return problems


def _check_reproducibility() -> list:
    """Same seed must reproduce a virion; different seeds must differ."""
    problems = []

    def signature(seed):
        rng = random.Random(seed)
        state = _make_state(rng, RADIUS_ENVELOPE)
        mesh = _envelope_mesh("MESH_TMP_Repro", state)
        value = round(sum(v.co.length for v in mesh.vertices), 6)
        bpy.data.meshes.remove(mesh)
        return value

    if signature(5) != signature(5):
        problems.append("seed is not reproducible")
    if signature(5) == signature(6):
        problems.append("seed is ignored: different seeds gave identical virions")
    return problems


def validate_hsv1_virions(collection=None, expected: int = 2) -> dict:
    """Validate the generated virions.

    The capsid assertions are the important ones: a smooth sphere would pass
    every other check in this file while destroying the asset's whole reason
    for existing, so face count and flat shading are both asserted directly.
    """
    coll = collection or ut.resolve_collection(TEST_COLLECTION)
    failures = []
    measurements = {"radius": {}, "capsid_faces": {}, "counts": {}}

    roots = [o for o in coll.objects if o.type == "EMPTY" and o.name.startswith(PREFIX)]
    if len(roots) != expected:
        failures.append("expected {} virions, found {}".format(expected, len(roots)))

    for obj in coll.objects:
        if obj.type == "MESH":
            failures.extend(_check_topology(obj))

    expected_mat = {
        SUFFIX_ENVELOPE: "MAT_Virus_Envelope",
        SUFFIX_GLYCOPROTEIN: "MAT_Virus_Glycoprotein",
        SUFFIX_TEGUMENT: "MAT_Virus_Tegument",
        SUFFIX_CAPSID: "MAT_Virus_Capsid",
        SUFFIX_GENOME: "MAT_Virus_Genome",
    }

    for root in roots:
        exploded = bool(root.get(PROP_EXPLODED))
        if root.get(PROP_NOTE) != ILLUSTRATIVE_NOTE:
            failures.append("{}: missing the representation note".format(root.name))

        layers = {SUFFIX_ENVELOPE: [], SUFFIX_GLYCOPROTEIN: [], SUFFIX_TEGUMENT: [],
                  SUFFIX_CAPSID: [], SUFFIX_GENOME: []}
        for obj in coll.objects:
            if obj.type != "MESH" or obj.parent != root:
                continue
            match = PART_PATTERN.match(obj.name)
            if match is None:
                failures.append("{}: name does not match VIRUS_HSV1_<Layer>_<NNN>".format(obj.name))
                continue
            layers[match.group("part")].append(obj)

        for layer in (SUFFIX_ENVELOPE, SUFFIX_CAPSID):
            if len(layers[layer]) != 1:
                failures.append("{}: expected 1 {}, found {}".format(
                    root.name, layer, len(layers[layer])))
        for layer in (SUFFIX_GLYCOPROTEIN, SUFFIX_TEGUMENT, SUFFIX_GENOME):
            if not layers[layer]:
                failures.append("{}: no {} elements".format(root.name, layer))

        # --- materials ----------------------------------------------------
        for layer, objs in layers.items():
            for obj in objs:
                if len(obj.data.materials) != 1:
                    failures.append("{}: expected 1 material slot".format(obj.name))
                    continue
                actual = obj.data.materials[0].name
                if actual != expected_mat[layer]:
                    failures.append("{}: material is {!r}, expected {!r}".format(
                        obj.name, actual, expected_mat[layer]))

        # --- the capsid must be visibly icosahedral -----------------------
        capsid = layers[SUFFIX_CAPSID][0] if layers[SUFFIX_CAPSID] else None
        if capsid is not None:
            faces = len(capsid.data.polygons)
            measurements["capsid_faces"][capsid.name] = faces
            if faces != ICOSAHEDRON_FACES[CAPSID_SUBDIVISIONS]:
                failures.append(
                    "{}: capsid has {} faces; a subdivision-{} icosphere has {}. "
                    "The capsid must stay icosahedral.".format(
                        capsid.name, faces, CAPSID_SUBDIVISIONS,
                        ICOSAHEDRON_FACES[CAPSID_SUBDIVISIONS]))
            smooth = [p.index for p in capsid.data.polygons if p.use_smooth]
            if smooth:
                failures.append(
                    "{}: {} capsid faces are smooth shaded. The capsid must be "
                    "flat shaded or the icosahedral facets stop reading.".format(
                        capsid.name, len(smooth)))
            if len(capsid.data.vertices) != 12 and CAPSID_SUBDIVISIONS == 1:
                failures.append("{}: capsid should have 12 verts at subdivision 1".format(
                    capsid.name))

        # --- radius ordering ----------------------------------------------
        envelope = layers[SUFFIX_ENVELOPE][0] if layers[SUFFIX_ENVELOPE] else None
        if envelope is not None:
            env_radius = _mean_radius(envelope.data)
            # Derive the built scale from the envelope actually produced,
            # rather than trusting the requested one, so the checks are made
            # against real geometry.
            built_scale = env_radius / RADIUS_ENVELOPE
            measurements["radius"][envelope.name] = round(env_radius, 5)
            if capsid is not None:
                cap_radius = _mean_radius(capsid.data)
                measurements["radius"][capsid.name] = round(cap_radius, 5)
                if not cap_radius < env_radius:
                    failures.append("{}: capsid radius {:.4f} must sit inside the envelope {:.4f}".format(
                        capsid.name, cap_radius, env_radius))
            # Tegument must live in the gap between capsid and envelope.
            if capsid is not None and layers[SUFFIX_TEGUMENT]:
                radii = [layer_radius(e, SUFFIX_TEGUMENT, built_scale, exploded)
                         for e in layers[SUFFIX_TEGUMENT]]
                low, high = min(radii), max(radii)
                if high >= env_radius:
                    failures.append("{}: tegument reaches {:.4f}, outside the envelope {:.4f}".format(
                        root.name, high, env_radius))
                if low <= cap_radius:
                    failures.append("{}: tegument reaches {:.4f}, inside the capsid {:.4f}".format(
                        root.name, low, cap_radius))
                measurements["counts"]["{}.tegument_band".format(root.name)] = (
                    round(low, 4), round(high, 4))
            # Glycoproteins must emerge through the envelope, and start inside it.
            if layers[SUFFIX_GLYCOPROTEIN]:
                # Derive the built scale from the envelope actually produced,
                # rather than trusting the requested one, so the check is made
                # against real geometry.
                outer, inner = [], []
                for pin in layers[SUFFIX_GLYCOPROTEIN]:
                    # The rod was built along +Z with length GLYCOPROTEIN_LENGTH,
                    # then scaled per instance, so its tip reaches
                    # centre + half the scaled length along the surface normal.
                    centre = layer_radius(pin, SUFFIX_GLYCOPROTEIN, built_scale, exploded)
                    half_len = 0.5 * pin.scale.z * GLYCOPROTEIN_LENGTH * built_scale
                    outer.append(centre + half_len)
                    inner.append(centre - half_len)
                measurements["counts"]["{}.glycoprotein_span".format(root.name)] = (
                    round(min(inner), 4), round(max(outer), 4))
                if min(outer) <= env_radius:
                    failures.append("{}: no glycoprotein emerges past the envelope".format(
                        root.name))
                if max(inner) >= env_radius:
                    failures.append("{}: glycoproteins are not embedded in the envelope".format(
                        root.name))

        # --- shared datablocks --------------------------------------------
        for layer in (SUFFIX_GLYCOPROTEIN, SUFFIX_TEGUMENT, SUFFIX_GENOME):
            meshes = {o.data.name for o in layers[layer]}
            if len(meshes) != 1:
                failures.append("{} {} elements do not share one mesh datablock: {}".format(
                    root.name, layer, sorted(meshes)))

        # --- exploded alignment -------------------------------------------
        if exploded:
            z = {}
            for layer, objs in layers.items():
                if objs:
                    z[layer] = sum(Vector(o.location).z for o in objs) / len(objs)
            order = [SUFFIX_ENVELOPE, SUFFIX_GLYCOPROTEIN, SUFFIX_TEGUMENT,
                     SUFFIX_CAPSID, SUFFIX_GENOME]
            measured = [z.get(layer, 0.0) for layer in order]
            measurements["counts"]["{}.explode_z".format(root.name)] = [
                round(v, 4) for v in measured]
            if not all(measured[i] > measured[i + 1] for i in range(len(measured) - 1)):
                failures.append("{}: exploded layers are not ordered envelope > glycoproteins > "
                                "tegument > capsid > genome: {}".format(root.name, measured))
            for layer, objs in layers.items():
                off_axis = [o for o in objs
                            if abs(Vector(o.location).x) > 1e-6
                            or abs(Vector(o.location).y) > 1e-6]
                if off_axis:
                    # Elements legitimately sit off-axis in X/Y; only the
                    # single-shell layers must stay on the axis.
                    if layer in (SUFFIX_ENVELOPE, SUFFIX_CAPSID) and off_axis:
                        failures.append("{}: {} is off the explode axis".format(root.name, layer))

    # --- no specific glycoprotein designators anywhere --------------------
    for name in [o.name for o in bpy.data.objects] + [m.name for m in bpy.data.materials]:
        for designator in FORBIDDEN_DESIGNATORS:
            # Whole-token match on both sides. A bare substring test flags "US"
            # inside "VIRUS", which is every object this module creates.
            if re.search(r"(?<![A-Za-z0-9]){}(?![A-Za-z])".format(re.escape(designator)), name):
                failures.append(
                    "{!r} contains the designator {!r}. The glycoprotein pins are "
                    "abstract; naming a specific HSV protein would claim a "
                    "specificity this model does not have.".format(name, designator))

    failures.extend(_check_reproducibility())

    return {
        "ok": not failures,
        "failures": failures,
        "checks": {
            "virions": len(roots),
            "meshes": len([o for o in coll.objects if o.type == "MESH"]),
            "materials": sorted({m.name for o in coll.objects
                                 if o.type == "MESH" for m in o.data.materials}),
        },
        "measurements": measurements,
    }


def main() -> None:
    """Generate the test virions, validate them, and report."""
    build_test_virions()
    report = validate_hsv1_virions()

    print("[hsv1] built {} virions in {}".format(
        report["checks"]["virions"], TEST_COLLECTION))
    print("[hsv1] checks: {}".format(report["checks"]))
    print("[hsv1] capsid_faces: {} (subdivision {} expects {})".format(
        report["measurements"].get("capsid_faces"),
        CAPSID_SUBDIVISIONS, ICOSAHEDRON_FACES[CAPSID_SUBDIVISIONS]))
    for key, value in sorted(report["measurements"].get("counts", {}).items()):
        print("[hsv1] {}: {}".format(key, value))
    if report["ok"]:
        print("[hsv1] VALIDATION PASSED")
    else:
        for failure in report["failures"]:
            print("[hsv1] FAIL: {}".format(failure))
        raise SystemExit("[hsv1] VALIDATION FAILED ({} problems)".format(
            len(report["failures"])))


if __name__ == "__main__":
    main()
