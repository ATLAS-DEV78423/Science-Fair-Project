"""Immune cell assets: T cell, NK cell, dendritic cell, macrophage.

**These are conceptual representations, not reconstructions.** Nothing here
resolves cellular or molecular structure. Each cell is built to be readable at
the scale and distances the four cameras actually work at, and to carry a
simulation state cleanly.

Four classes, one shared builder, four public functions::

    create_tcell(location, scale, rotation, seed)
    create_nk_cell(location, scale, rotation, seed)
    create_dendritic_cell(location, scale, rotation, seed)
    create_macrophage(location, scale, rotation, seed)

T cell
------
A rounded lymphocyte: roughly spherical body, a large nucleus filling most of
the volume, and subtle membrane ruffles. Lymphocytes are characterised by high
nuclear-to-cytoplasmic ratio, so the nucleus dominates and the cytoplasmic rim
is thin. That is modelled, not incidental.

NK cell
-------
Built to be *visually similar* to the T cell, deliberately.

**A caution worth stating plainly: in reality, appearance does not distinguish
NK cells from T cells.** Both are small granular lymphocytes and they are
morphologically near-identical under a light microscope. Telling them apart
requires immunophenotyping -- surface markers such as CD3 and CD56 -- not
shape. The difference in this model is a *visualisation convention*, needed so
a viewer can follow both populations in an animation, and the two are given
only a modest colour and size offset so the similarity stays honest.

Dendritic cell
--------------
A central body with branching dendritic processes. The branching is the whole
point of the cell: it maximises contact with antigen, which is how it reaches
lymphocytes. Processes are generated recursively and merged into a single mesh,
because a branched structure is one connected object and should be one
datablock, unlike the ECM fibres which must stay separate.

Macrophage
----------
Larger, with an irregular amoeboid membrane built from broad soft lobes rather
than spikes, and a smaller eccentric nucleus. Macrophages are the largest of
the four here and are amoeboid in a way lymphocytes are not.

Animation and interaction support
---------------------------------
Every class gets the same set of hooks, all on the cell's root empty:

* **Smooth movement** -- the root is the sole transform handle. Animate it and
  the whole cell follows, including ruffles and dendritic processes.
* **Selection** -- a ``selected`` boolean property, plus the emission channel
  so a selected cell can be shown brighter. Selection state is data, not just
  a viewport highlight, so the web viewer can read it.
* **Highlighting** -- every material has its emission channel preconfigured at
  zero strength, so a highlight is one keyed value.
* **Interaction** -- target interaction needs a transform to move toward and a
  way to know who the target is, which the ``immune_class`` property and the
  parenting provide.
* **Animation** -- no baked animation anywhere, and no mesh carries its own
  keys. Same rule as the rest of the project: animate the root.

Run standalone::

    blender --background --python blender/scripts/asset_immune_cells.py
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

COLLECTION = "04_IMMUNE_SYSTEM"

PREFIX_TCELL = "IMMUNE_TCELL"
PREFIX_NK = "IMMUNE_NK"
PREFIX_DENDRITIC = "IMMUNE_DENDRITIC"
PREFIX_MACROPHAGE = "IMMUNE_MACROPHAGE"

SUFFIX_MEMBRANE = "MEMBRANE"
SUFFIX_CYTOPLASM = "CYTOPLASM"
SUFFIX_NUCLEUS = "NUCLEUS"
SUFFIX_RUFFLE = "RUFFLE"
SUFFIX_PROCESS = "PROCESS"

#: Reference radii in micrometres == Blender units here. Lymphocytes are
#: 7-10 um, so ~4 um radius; a macrophage is several times larger.
RADIUS_TCELL_UM = 4.0
RADIUS_NK_UM = 4.6
RADIUS_DENDRITIC_UM = 5.0
RADIUS_MACROPHAGE_UM = 13.0

SUBDIVISIONS = 4
SUBDIVISIONS_SMALL = 3
#: Ruffles are small and numerous, so they get the cheapest mesh that still
#: shades smoothly, shared by every ruffled cell in the scene.
RUFFLE_SUBDIVISIONS = 2
RUFFLE_RADIUS_UM = 0.30

#: Cytoplasm radius as a fraction of membrane radius. Lymphocytes have scant
#: cytoplasm, so the inset leaves only a thin rim outside the nucleus.
CYTOPLASM_INSET = 0.88

NOISE_SCALE_COARSE = 1.7
NOISE_SCALE_FINE = 4.1
NOISE_FINE_WEIGHT = 0.45

#: Gentle surface variation. Kept low: these are cells, not asteroids.
IRREGULARITY_MEMBRANE = 0.045
IRREGULARITY_CYTOPLASM = 0.040
IRREGULARITY_NUCLEUS = 0.040
#: Macrophage membrane variation is higher and is delivered as broad lobes by
#: the amoeboid term rather than as noise, so it reads as soft pseudopods
#: instead of a lumpy ball.
IRREGULARITY_MACROPHAGE = 0.035
AMOEBOID_LOBES = 5
AMOEBOID_AMPLITUDE = 0.34
AMOEBOID_WIDTH = 0.55

#: Dendritic process tree.
PROCESS_DEPTH = 3
PROCESS_BRANCHES = 2
PROCESS_LENGTH_UM = 7.0
PROCESS_LENGTH_FALLOFF = 0.68
PROCESS_RADIUS_UM = 0.62
PROCESS_RADIUS_FALLOFF = 0.62
PROCESS_SEGMENTS = 8
#: Branching pushes a process off the trunk by at least this angle, so children
#: separate visibly instead of running parallel.
PROCESS_SPREAD_DEG = 34.0

SIZE_JITTER = 0.07
ASPECT_RANGE = (0.94, 1.06)
NUCLEUS_ASPECT_RANGE = (0.90, 1.10)
NUCLEUS_OFFSET = 0.14

#: Shared procedural texture for membrane deformation. One texture for every
#: immune cell; per-cell variation comes from OBJECT-space coordinates against
#: each cell's own root. Carries no animation -- the idle layer owns that.
TEX_DISPLACE_NAME = "TEX_ImmuneCellDisplace"
MEMBRANE_DEFORM_STRENGTH = 0.32

PROP_CLASS = "immune_class"
PROP_SELECTED = "selected"

FORBIDDEN_NAMES = {"Cube", "Sphere", "Object", "Mesh", "Material", "Icosphere"}

_PREFIXES = (PREFIX_TCELL, PREFIX_NK, PREFIX_DENDRITIC, PREFIX_MACROPHAGE)
_PARTS = (SUFFIX_MEMBRANE, SUFFIX_CYTOPLASM, SUFFIX_NUCLEUS, SUFFIX_RUFFLE,
          SUFFIX_PROCESS)

#: Matches IMMUNE_<CLASS>_<PART>_<NNN>, plus the ruffle form
#: IMMUNE_<CLASS>_RUFFLE_<cell NNN>_<marker NN>. Both alternations must be
#: grouped: an ungrouped "|" would bind the anchors to the first alternative
#: only and silently match one class.
PART_PATTERN = re.compile(
    r"^(?P<prefix>{})_(?P<part>{})_(?P<index>\d{{3}})(?:_(?P<sub>\d{{2}}))?$".format(
        "|".join(re.escape(p) for p in _PREFIXES),
        "|".join(re.escape(s) for s in _PARTS)))


# ---------------------------------------------------------------------------
# Class table
# ---------------------------------------------------------------------------
# One entry per class. The four public functions are thin wrappers over
# _create_immune_cell, so adding a fifth class is a table row and a wrapper.

IMMUNE_CLASSES = {
    "TCELL": dict(
        prefix=PREFIX_TCELL,
        radius_um=RADIUS_TCELL_UM,
        # Lymphocytes: a large nucleus and scant cytoplasm.
        nucleus_ratio=0.72,
        nucleus_offset=0.10,
        ruffles=22,
        amoeboid=False,
        processes=False,
        membrane_color=(0.50, 0.66, 0.74, 1.0),
        cytoplasm_color=(0.58, 0.71, 0.76, 1.0),
        nucleus_color=(0.20, 0.29, 0.48, 1.0),
        highlight=(0.55, 0.85, 1.00, 1.0),
    ),
    "NK": dict(
        prefix=PREFIX_NK,
        radius_um=RADIUS_NK_UM,
        nucleus_ratio=0.70,
        nucleus_offset=0.12,
        ruffles=26,
        amoeboid=False,
        processes=False,
        # Only a modest offset from the T cell. They must stay recognisably
        # alike, because they are alike -- see the module docstring.
        membrane_color=(0.47, 0.70, 0.62, 1.0),
        cytoplasm_color=(0.55, 0.73, 0.68, 1.0),
        nucleus_color=(0.18, 0.35, 0.42, 1.0),
        highlight=(0.50, 0.95, 0.80, 1.0),
    ),
    "DENDRITIC": dict(
        prefix=PREFIX_DENDRITIC,
        radius_um=RADIUS_DENDRITIC_UM,
        nucleus_ratio=0.55,
        nucleus_offset=0.16,
        ruffles=14,
        amoeboid=False,
        processes=True,
        membrane_color=(0.48, 0.52, 0.74, 1.0),
        cytoplasm_color=(0.55, 0.58, 0.76, 1.0),
        nucleus_color=(0.25, 0.23, 0.52, 1.0),
        highlight=(0.65, 0.70, 1.00, 1.0),
    ),
    "MACROPHAGE": dict(
        prefix=PREFIX_MACROPHAGE,
        radius_um=RADIUS_MACROPHAGE_UM,
        nucleus_ratio=0.38,
        nucleus_offset=0.22,
        ruffles=0,
        amoeboid=True,
        processes=False,
        membrane_color=(0.54, 0.63, 0.63, 1.0),
        cytoplasm_color=(0.60, 0.67, 0.66, 1.0),
        nucleus_color=(0.23, 0.30, 0.36, 1.0),
        highlight=(0.70, 0.90, 0.85, 1.0),
    ),
}


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------


def _class_materials(name: str, spec: dict) -> dict:
    """Create or update this class's membrane, cytoplasm and nucleus materials.

    Separate datablocks per class, per the brief, so each immune population can
    be recoloured or restyled independently. The membrane and cytoplasm are
    translucent so the nucleus reads through them, which matters most for the
    lymphocytes where the nucleus is the dominant feature.
    """
    return {
        "membrane": _material(
            "MAT_Immune_{}_Membrane".format(name),
            spec["membrane_color"], roughness=0.30, subsurface=0.26,
            alpha=0.20, emission=spec["highlight"]),
        "cytoplasm": _material(
            "MAT_Immune_{}_Cytoplasm".format(name),
            spec["cytoplasm_color"], roughness=0.46, subsurface=0.20,
            alpha=0.15, emission=spec["highlight"]),
        "nucleus": _material(
            "MAT_Immune_{}_Nucleus".format(name),
            spec["nucleus_color"], roughness=0.38, subsurface=0.12,
            alpha=0.94, emission=spec["highlight"], transparent=False),
    }


def _material(name, color, roughness, subsurface, alpha, emission,
              transparent: bool = True):
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


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def _surface_factor(direction, rng_state, *, irregularity, lobes=None,
                    aspect=(1.0, 1.0, 1.0)) -> float:
    """Radial displacement along one direction, aspect applied.

    A pure function of the state dict, so the membrane mesh and any surface
    detail placed on it can never disagree about where the surface is.
    """
    factor = 1.0
    factor += irregularity * noise.noise(
        direction * NOISE_SCALE_COARSE + rng_state["phase_coarse"])
    factor += irregularity * NOISE_FINE_WEIGHT * noise.noise(
        direction * NOISE_SCALE_FINE + rng_state["phase_fine"])
    if lobes:
        # Broad soft lobes. exp() peaks where the direction aligns with the
        # lobe and falls off smoothly, which gives pseudopods rather than
        # spikes -- a macrophage is amoeboid, not spiky.
        for lobe_dir, amplitude, width in lobes:
            factor += amplitude * math.exp((direction.dot(lobe_dir) - 1.0) / width)
    scaled = Vector((direction.x * aspect[0], direction.y * aspect[1],
                     direction.z * aspect[2]))
    return factor / rng_state["norm"], scaled


def _make_state(rng, radius, aspect, irregularity, *, amoeboid=False):
    """Per-cell shape state, including the macrophage's lobes.

    Also computes ``norm``, the mean radial distance over the whole surface,
    so that the declared *radius* is the cell's mean radius by construction.

    Without this the amoeboid lobes inflate the macrophage's mean radius to
    well over its declared size, which makes the declared size a lie and makes
    the radius check meaningless. Dividing the factor by its own mean keeps
    lobes as expressive as needed while the size stays honest -- and because
    the correction lives in the state, the membrane mesh and every piece of
    surface detail placed on it stay consistent.
    """
    state = {
        "radius": radius,
        "aspect": aspect,
        "phase_coarse": Vector([rng.uniform(-40.0, 40.0) for _ in range(3)]),
        "phase_fine": Vector([rng.uniform(-40.0, 40.0) for _ in range(3)]),
        "lobes": None,
        "norm": 1.0,
    }
    if amoeboid:
        state["lobes"] = [
            (Vector([rng.uniform(-1.0, 1.0) for _ in range(3)]).normalized(),
             AMOEBOID_AMPLITUDE * rng.uniform(0.6, 1.4),
             AMOEBOID_WIDTH * rng.uniform(0.7, 1.3))
            for _ in range(AMOEBOID_LOBES)
        ]

    # Sample the surface and average the radial distance, before normalising it.
    total = 0.0
    samples = 256
    for _ in range(samples):
        direction = Vector([rng.uniform(-1.0, 1.0) for _ in range(3)]).normalized()
        factor = 1.0
        factor += irregularity * noise.noise(
            direction * NOISE_SCALE_COARSE + state["phase_coarse"])
        factor += irregularity * NOISE_FINE_WEIGHT * noise.noise(
            direction * NOISE_SCALE_FINE + state["phase_fine"])
        for lobe_dir, amplitude, width in state["lobes"] or ():
            factor += amplitude * math.exp((direction.dot(lobe_dir) - 1.0) / width)
        scaled = Vector((direction.x * aspect[0], direction.y * aspect[1],
                         direction.z * aspect[2]))
        total += scaled.length * factor
    state["norm"] = max(total / samples, 1e-6)
    return state


def _shell_mesh(name, state, aspect, irregularity, subdivisions):
    """A deformed, closed, smooth-shaded shell around the cell's radius."""
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=1.0)
    for vert in bm.verts:
        factor, scaled = _surface_factor(
            vert.co.normalized(), state, irregularity=irregularity,
            lobes=state["lobes"], aspect=aspect)
        vert.co = scaled * (state["radius"] * factor)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.shade_smooth()
    return mesh


def _dendrite_mesh(name, state, aspect, radius, rng):
    """Recursively branched dendritic processes, merged into one mesh.

    Each branch segment is a tapered cone, oriented by a matrix that maps +Z
    onto the branch direction. Children branch off at least
    ``PROCESS_SPREAD_DEG`` from the trunk so the structure reads as a tree
    rather than as a bundle of parallel spikes.

    All segments go into one bmesh: a branched structure is a single connected
    object, so it should be a single datablock. (Contrast the ECM fibres, which
    must stay separate objects so each stays individually animatable.)
    """
    bm = bmesh.new()
    origins = []

    def grow(start, direction, length, thickness, depth):
        end = start + direction * length
        orientation = direction.to_track_quat("Z", "Y").to_matrix().to_4x4()
        placement = Matrix.Translation((start + end) / 2.0) @ orientation
        bmesh.ops.create_cone(
            bm, cap_ends=(depth == 0), cap_tris=False, segments=PROCESS_SEGMENTS,
            radius1=thickness, radius2=thickness * 0.72,
            depth=length, matrix=placement,
        )
        if depth == 0:
            return
        branches = PROCESS_BRANCHES + (1 if rng.random() < 0.35 else 0)
        for _ in range(branches):
            spread = math.radians(PROCESS_SPREAD_DEG * rng.uniform(0.8, 1.5))
            # Rotate the trunk direction away from itself about a random axis.
            axis = direction.cross(
                Vector([rng.uniform(-1.0, 1.0) for _ in range(3)]))
            if axis.length < 1e-6:
                axis = direction.cross(Vector((1.0, 0.0, 0.0)))
            # Vector.rotate() mutates in place and returns None, so this cannot
            # be chained.
            child_dir = direction.copy()
            child_dir.rotate(Matrix.Rotation(
                spread * rng.choice((-1, 1)), 3, axis.normalized()))
            child_dir.normalize()
            grow(end, child_dir, length * PROCESS_LENGTH_FALLOFF * rng.uniform(0.85, 1.15),
                 thickness * PROCESS_RADIUS_FALLOFF, depth - 1)

    # Primary processes start on the body surface and point outward.
    for _ in range(3 + (1 if rng.random() < 0.5 else 0)):
        direction = Vector([rng.uniform(-1.0, 1.0) for _ in range(3)]).normalized()
        factor, scaled = _surface_factor(
            direction, state, irregularity=IRREGULARITY_MEMBRANE,
            lobes=state["lobes"], aspect=aspect)
        origins.append(scaled * (state["radius"] * factor * 0.92))
        grow(scaled * (state["radius"] * factor * 0.92), direction,
             PROCESS_LENGTH_UM * rng.uniform(0.85, 1.2), PROCESS_RADIUS_UM, PROCESS_DEPTH)

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.shade_smooth()
    return mesh


def _ruffle_mesh(radius: float = RUFFLE_RADIUS_UM):
    """Get or create the one ruffle mesh the whole scene shares."""
    name = "MESH_{}_SHARED".format(SUFFIX_RUFFLE)
    mesh = bpy.data.meshes.get(name)
    if mesh is not None:
        return mesh
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=RUFFLE_SUBDIVISIONS, radius=radius)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.shade_smooth()
    return mesh


def _surface_directions(count, rng):
    """Evenly spread unit directions with seeded jitter, via a Fibonacci spiral.

    Random points clump, and a clumped ruffle distribution reads as a mistake.
    """
    golden = math.pi * (3.0 - math.sqrt(5.0))
    spin = rng.uniform(0.0, 2.0 * math.pi)
    out = []
    for i in range(count):
        z = 1.0 - (2.0 * i + 1.0) / count
        r = math.sqrt(max(0.0, 1.0 - z * z))
        theta = golden * i + spin + rng.uniform(-0.3, 0.3)
        out.append(Vector((math.cos(theta) * r, math.sin(theta) * r, z)))
    return out


def _add_membrane_deformation(obj, root) -> None:
    """Attach the animatable Displace modifier to a membrane.

    All immune cells share one Clouds texture but use OBJECT-space coordinates
    relative to their own root, so each deforms differently from the same
    texture and moving a cell carries its deformation field along.

    Ruffles are separate objects parented to the membrane, not part of its
    mesh, so they hold still while the surface moves under them. At this
    strength the gap is well inside the ruffle radius and does not read.
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
# Shared builder
# ---------------------------------------------------------------------------


def _create_immune_cell(kind: str, location=(0.0, 0.0, 0.0), scale: float = 1.0,
                        rotation=(0.0, 0.0, 0.0), seed: int = 0, *,
                        index: int = 1, collection=None, parent=None) -> dict:
    """Build one immune cell of class *kind*. See the public wrappers."""
    if kind not in IMMUNE_CLASSES:
        raise ValueError("unknown immune class {!r}".format(kind))
    spec = IMMUNE_CLASSES[kind]
    coll = collection or ut.resolve_collection(COLLECTION)
    rng = random.Random(seed)

    radius = spec["radius_um"] * scale * (1.0 + rng.uniform(-SIZE_JITTER, SIZE_JITTER))
    aspect = tuple(rng.uniform(*ASPECT_RANGE) for _ in range(3))
    state = _make_state(rng, radius, aspect, IRREGULARITY_MEMBRANE,
                        amoeboid=spec["amoeboid"])
    prefix = spec["prefix"]

    root = ut.new_empty(ut.obj_name(prefix, index), coll, location=location,
                        parent=parent, size=radius * 0.6)
    root.rotation_euler = rotation
    root[PROP_CLASS] = kind
    root[PROP_SELECTED] = False

    materials = _class_materials(kind, spec)

    membrane_mesh = _shell_mesh(
        "MESH_{}_{}".format(prefix, SUFFIX_MEMBRANE), state, aspect,
        IRREGULARITY_MACROPHAGE if spec["amoeboid"] else IRREGULARITY_MEMBRANE,
        SUBDIVISIONS)
    membrane = ut.get_or_create_object(
        ut.obj_name("{}_{}".format(prefix, SUFFIX_MEMBRANE), index), coll,
        lambda n: membrane_mesh)
    ut.set_parent(membrane, root)
    ut.assign_material(membrane, materials["membrane"])
    _add_membrane_deformation(membrane, root)

    # Cytoplasm shares the envelope's aspect so the layers stay concentric, but
    # gets its own noise phase: an interior does not mirror its shell.
    inner = _make_state(rng, radius, aspect, IRREGULARITY_CYTOPLASM,
                        amoeboid=spec["amoeboid"])
    cytoplasm_mesh = _shell_mesh(
        "MESH_{}_{}".format(prefix, SUFFIX_CYTOPLASM), inner, aspect,
        IRREGULARITY_CYTOPLASM, SUBDIVISIONS_SMALL)
    cytoplasm = ut.get_or_create_object(
        ut.obj_name("{}_{}".format(prefix, SUFFIX_CYTOPLASM), index), coll,
        lambda n: cytoplasm_mesh)
    ut.set_parent(cytoplasm, root)
    ut.assign_material(cytoplasm, materials["cytoplasm"])

    n_radius = radius * spec["nucleus_ratio"]
    n_aspect = tuple(rng.uniform(*NUCLEUS_ASPECT_RANGE) for _ in range(3))
    n_state = _make_state(rng, n_radius, n_aspect, IRREGULARITY_NUCLEUS)
    nucleus_mesh = _shell_mesh(
        "MESH_{}_{}".format(prefix, SUFFIX_NUCLEUS), n_state, n_aspect,
        IRREGULARITY_NUCLEUS, SUBDIVISIONS_SMALL)
    nucleus = ut.get_or_create_object(
        ut.obj_name("{}_{}".format(prefix, SUFFIX_NUCLEUS), index), coll,
        lambda n: nucleus_mesh)
    ut.set_parent(nucleus, root)
    nucleus.location = (Vector([rng.uniform(-1.0, 1.0) for _ in range(3)])
                        .normalized() * (radius * spec["nucleus_offset"]))
    ut.assign_material(nucleus, materials["nucleus"])

    processes = None
    if spec["processes"]:
        processes = ut.get_or_create_object(
            ut.obj_name("{}_{}".format(prefix, SUFFIX_PROCESS), index), coll,
            lambda n: _dendrite_mesh("MESH_{}_{}".format(prefix, SUFFIX_PROCESS),
                                     state, aspect, radius, rng))
        ut.set_parent(processes, root)
        ut.assign_material(processes, materials["membrane"])

    ruffles = []
    if spec["ruffles"]:
        shared = _ruffle_mesh()
        for i, direction in enumerate(_surface_directions(spec["ruffles"], rng)):
            factor, scaled = _surface_factor(
                direction, state, irregularity=0.0, lobes=state["lobes"], aspect=aspect)
            ruffle = ut.get_or_create_object(
                "{}_{}_{:03d}_{:02d}".format(prefix, SUFFIX_RUFFLE, index, i + 1),
                coll, lambda n, m=shared: m,
                location=scaled * (state["radius"] * factor))
            ut.set_parent(ruffle, membrane)
            ut.assign_material(ruffle, materials["membrane"])
            ruffles.append(ruffle)

    return {
        "root": root,
        "membrane": membrane,
        "cytoplasm": cytoplasm,
        "nucleus": nucleus,
        "processes": processes,
        "ruffles": ruffles,
        "class": kind,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_tcell(location=(0.0, 0.0, 0.0), scale: float = 1.0,
                 rotation=(0.0, 0.0, 0.0), seed: int = 0, **kwargs) -> dict:
    """Create a T cell: rounded lymphocyte, large nucleus, subtle ruffles.

    Args:
        location: World-space location of the cell centre.
        scale: Uniform size multiplier on top of :data:`RADIUS_TCELL_UM`.
        rotation: Euler rotation in radians, applied to the root.
        seed: Controls all variation. Same seed, same cell, every time.
        index: 1-based ordinal used for the object names.
        collection: Target collection. Defaults to ``04_IMMUNE_SYSTEM``.
        parent: Optional parent object, normally ``CTRL_Immune``.
    """
    return _create_immune_cell("TCELL", location, scale, rotation, seed, **kwargs)


def create_nk_cell(location=(0.0, 0.0, 0.0), scale: float = 1.0,
                   rotation=(0.0, 0.0, 0.0), seed: int = 0, **kwargs) -> dict:
    """Create an NK cell.

    Intentionally similar to the T cell. **Appearance does not distinguish NK
    cells from T cells in reality** -- they are morphologically near-identical
    and are told apart by surface markers, not shape. The offset here is a
    visualisation convention so both populations can be followed in an
    animation. See the module docstring.
    """
    return _create_immune_cell("NK", location, scale, rotation, seed, **kwargs)


def create_dendritic_cell(location=(0.0, 0.0, 0.0), scale: float = 1.0,
                          rotation=(0.0, 0.0, 0.0), seed: int = 0, **kwargs) -> dict:
    """Create a dendritic cell: central body with branching processes.

    The branching is the defining feature and the reason this cell exists in
    the sequence: it is how antigen is presented to lymphocytes.
    """
    return _create_immune_cell("DENDRITIC", location, scale, rotation, seed, **kwargs)


def create_macrophage(location=(0.0, 0.0, 0.0), scale: float = 1.0,
                      rotation=(0.0, 0.0, 0.0), seed: int = 0, **kwargs) -> dict:
    """Create a macrophage: larger, amoeboid, with broad soft membrane lobes.

    The lobes are deliberately rounded and smooth. A spiky macrophage would be
    the same caricature as a spiky cancer cell, and just as wrong.
    """
    return _create_immune_cell("MACROPHAGE", location, scale, rotation, seed, **kwargs)


#: Dispatch by class name, for callers that hold a class as data.
CREATE_BY_CLASS = {
    "TCELL": create_tcell,
    "NK": create_nk_cell,
    "DENDRITIC": create_dendritic_cell,
    "MACROPHAGE": create_macrophage,
}


# ---------------------------------------------------------------------------
# Test collection
# ---------------------------------------------------------------------------

TEST_COLLECTION = "TEST_ImmuneCells"
TEST_PER_CLASS = 2
TEST_SPACING_UM = 30.0
TEST_COLUMNS = 4


def build_test_cells(collection=None, per_class: int = TEST_PER_CLASS,
                     clear: bool = True) -> list:
    """Generate two of each class into TEST_ImmuneCells, in a compact grid.

    Two per class rather than one, so seeded variation is visible and the
    validator has something to compare within a class. Laid out in rows so the
    whole set fits one Macro-camera framing and can be inspected at a glance.
    """
    coll = collection or ut.get_or_create_collection(
        TEST_COLLECTION, ut.resolve_collection("10_DEBUG"))
    if clear:
        for spec in IMMUNE_CLASSES.values():
            ut.clear_collection(coll, prefix=spec["prefix"])

    built = []
    index = 1
    for kind, factory in CREATE_BY_CLASS.items():
        for k in range(per_class):
            row, column = divmod(index - 1, TEST_COLUMNS)
            built.append(factory(
                location=((column - 1.5) * TEST_SPACING_UM,
                          (row - 0.5) * TEST_SPACING_UM, 0.0),
                rotation=(0.0, 0.0, index * 0.8),
                seed=3000 + index,
                index=index,
                collection=coll,
            ))
            index += 1
    return built


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _lumpiness(mesh, aspect) -> float:
    """Stddev of vertex radius over mean radius, aspect divided out.

    Without the normalisation a flattened cell scores as lumpy purely for
    being squashed, which would make this metric report shape as roughness.
    """
    radii = []
    for vert in mesh.vertices:
        co = vert.co
        radii.append(Vector((co.x / aspect[0], co.y / aspect[1],
                             co.z / aspect[2])).length)
    mean = sum(radii) / len(radii)
    if not mean:
        return 0.0
    variance = sum((r - mean) ** 2 for r in radii) / len(radii)
    return (variance ** 0.5) / mean


def _mean_radius(mesh) -> float:
    """Mean vertex distance from the cell centre.

    Used for the declared-radius check rather than the bounding box. A
    macrophage's amoeboid lobes push the bounding box out well past the body
    radius, so comparing max-dimension/2 against the declared radius would
    report a correctly built lobed cell as badly oversized.
    """
    if not mesh.vertices:
        return 0.0
    return sum(v.co.length for v in mesh.vertices) / len(mesh.vertices)


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
    if bm.calc_volume(signed=True) <= 0.0:
        problems.append("{}: normals point inward or volume is zero".format(obj.name))
    bm.free()
    return problems


def _check_reproducibility() -> list:
    """Same seed must reproduce a cell; different seeds must differ."""
    problems = []

    def signature(seed):
        rng = random.Random(seed)
        spec = IMMUNE_CLASSES["TCELL"]
        state = _make_state(rng, spec["radius_um"], (1.0, 1.0, 1.0),
                            IRREGULARITY_MEMBRANE)
        mesh = _shell_mesh("MESH_TMP_Repro", state, (1.0, 1.0, 1.0),
                           IRREGULARITY_MEMBRANE, SUBDIVISIONS_SMALL)
        value = round(sum(v.co.length for v in mesh.vertices), 4)
        bpy.data.meshes.remove(mesh)
        return value

    if signature(11) != signature(11):
        problems.append("seed is not reproducible")
    if signature(11) == signature(12):
        problems.append("seed is ignored: different seeds gave identical cells")
    return problems


def validate_immune_cells(collection=None, per_class: int = TEST_PER_CLASS) -> dict:
    """Validate the generated immune cells.

    Checks per-cell integrity (topology, naming, materials, transforms) plus
    the two properties the brief is really about: that the four classes are
    distinguishable, and that NK and T cells are *similar* rather than
    different.
    """
    coll = collection or ut.resolve_collection(TEST_COLLECTION)
    failures = []
    measurements = {"radius": {}, "lumpiness": {}, "nucleus_ratio": {},
                    "ruffles": {}, "process_extent": {}}

    roots = [o for o in coll.objects if o.type == "EMPTY"
             and any(o.name.startswith(s["prefix"]) for s in IMMUNE_CLASSES.values())]
    meshes = [o for o in coll.objects if o.type == "MESH"]

    if len(roots) != len(IMMUNE_CLASSES) * per_class:
        failures.append("expected {} cells, found {}".format(
            len(IMMUNE_CLASSES) * per_class, len(roots)))

    for obj in meshes:
        failures.extend(_check_topology(obj))

    for root in roots:
        if any(root.name == bad for bad in FORBIDDEN_NAMES):
            failures.append("{}: forbidden generic name".format(root.name))
        if len(root.name) > 8 and root.name[-4] == "." and root.name[-3:].isdigit():
            failures.append("{}: has a .001 duplicate suffix".format(root.name))
        kind = root.get(PROP_CLASS)
        if kind not in IMMUNE_CLASSES:
            failures.append("{}: bad or missing {!r}".format(root.name, PROP_CLASS))
            continue
        if PROP_SELECTED not in root:
            failures.append("{}: missing {!r} property".format(root.name, PROP_SELECTED))

        spec = IMMUNE_CLASSES[kind]
        parts, ruffles, process = {}, [], None
        for obj in coll.objects:
            if obj.type != "MESH":
                continue
            match = PART_PATTERN.match(obj.name)
            if not match:
                failures.append("{}: name does not match the IMMUNE_*_<PART>_<NNN> rule".format(obj.name))
                continue
            role = match.group("part")
            if role == SUFFIX_RUFFLE and obj.parent is not None \
                    and obj.parent.parent == root:
                ruffles.append(obj)
            elif obj.parent == root:
                if role == SUFFIX_PROCESS:
                    process = obj
                else:
                    parts[role] = obj

        for required in (SUFFIX_MEMBRANE, SUFFIX_CYTOPLASM, SUFFIX_NUCLEUS):
            if required not in parts:
                failures.append("{}: missing part {}".format(root.name, required))

        membrane = parts.get(SUFFIX_MEMBRANE)
        if membrane is not None:
            mod = membrane.modifiers.get("MOD_MembraneDeform")
            if mod is None:
                failures.append("{}: membrane deformation modifier missing".format(
                    membrane.name))
            elif mod.texture_coords_object != root:
                failures.append("{}: deformation not bound to its own root".format(
                    membrane.name))

        if spec["processes"] and process is None:
            failures.append("{}: {} requires a dendritic process mesh".format(
                root.name, kind))
        if not spec["processes"] and process is not None:
            failures.append("{}: {} should not have a process mesh".format(root.name, kind))
        if len(ruffles) != spec["ruffles"]:
            failures.append("{}: expected {} ruffles, found {}".format(
                root.name, spec["ruffles"], len(ruffles)))

        measurements["ruffles"][root.name] = len(ruffles)

        membrane, nucleus = parts.get(SUFFIX_MEMBRANE), parts.get(SUFFIX_NUCLEUS)
        if membrane is not None:
            radius = _mean_radius(membrane.data)
            measurements["radius"][root.name] = round(radius, 3)
            if not (spec["radius_um"] * 0.82 <= radius <= spec["radius_um"] * 1.18):
                failures.append("{}: mean radius {:.2f} BU far from the declared {:.1f} um".format(
                    root.name, radius, spec["radius_um"]))
            for obj in parts.values():
                if len(obj.data.materials) != 1:
                    failures.append("{}: expected 1 material slot".format(obj.name))
                    continue
                mat = obj.data.materials[0].name
                if not mat.startswith("MAT_Immune_{}_".format(kind)):
                    failures.append("{}: material {!r} does not belong to class {}".format(
                        obj.name, mat, kind))
        if membrane is not None and nucleus is not None:
            ratio = _mean_radius(nucleus.data) / _mean_radius(membrane.data)
            measurements["nucleus_ratio"][root.name] = round(ratio, 3)
            if abs(ratio - spec["nucleus_ratio"]) > 0.14:
                failures.append("{}: nucleus ratio {:.2f} far from the declared {:.2f}".format(
                    root.name, ratio, spec["nucleus_ratio"]))
        if process is not None:
            extent = max(process.dimensions) / 2.0
            measurements["process_extent"][root.name] = round(extent, 2)
            if extent <= spec["radius_um"] * 1.4:
                failures.append("{}: dendritic extent {:.1f} is not clearly beyond the body".format(
                    root.name, extent))
            # Branching means far more geometry than a bare body shell.
            if len(process.data.polygons) < 200:
                failures.append("{}: process mesh has only {} faces; branching is not reading".format(
                    root.name, len(process.data.polygons)))

    # --- shared ruffle mesh ------------------------------------------------
    all_ruffles = [o for o in meshes if SUFFIX_RUFFLE in o.name]
    if all_ruffles:
        shared = {r.data.name for r in all_ruffles}
        if len(shared) != 1:
            failures.append("ruffles do not share one mesh datablock: {}".format(sorted(shared)))

    # --- class-level claims -----------------------------------------------
    # NK and T cells must stay similar, not different.
    tc = [r for r in measurements["radius"] if r.startswith(PREFIX_TCELL)]
    nk = [r for r in measurements["radius"] if r.startswith(PREFIX_NK)]
    if tc and nk:
        tc_mean = sum(measurements["radius"][r] for r in tc) / len(tc)
        nk_mean = sum(measurements["radius"][r] for r in nk) / len(nk)
        ratio = nk_mean / tc_mean
        if not (0.95 <= ratio <= 1.45):
            failures.append(
                "NK and T cell radii differ by {:.2f}x; they should read as similar "
                "because they are similar".format(ratio))
        measurements["nk_vs_tcell_radius_ratio"] = round(ratio, 3)

    # The macrophage must actually be amoeboid.
    mp_roots = [r for r in roots if r.get(PROP_CLASS) == "MACROPHAGE"]
    if mp_roots:
        aspects = []
        for root in mp_roots:
            membrane = next((o for o in coll.objects
                             if o.type == "MESH" and o.parent == root
                             and o.name.endswith(SUFFIX_MEMBRANE)), None)
            if membrane is not None:
                aspects.append(sorted(membrane.dimensions)[-1] / sorted(membrane.dimensions)[0])
        if aspects and min(aspects) < 1.12:
            failures.append("macrophage membrane is not irregular enough (aspect {:.2f})".format(
                min(aspects)))

    # Every class must be visually distinct from every other.
    means = {}
    for kind, spec in IMMUNE_CLASSES.items():
        radii = [measurements["radius"][r.name] for r in roots
                 if r.get(PROP_CLASS) == kind and r.name in measurements["radius"]]
        if radii:
            means[kind] = sum(radii) / len(radii)
    distinct_pairs = [("TCELL", "NK"), ("TCELL", "DENDRITIC"), ("TCELL", "MACROPHAGE"),
                      ("DENDRITIC", "MACROPHAGE")]
    for a, b in distinct_pairs:
        if a in means and b in means and abs(means[a] - means[b]) < 0.25:
            failures.append("{} and {} are nearly the same size; they must be distinguishable".format(a, b))
    measurements["mean_radius_by_class"] = {k: round(v, 2) for k, v in means.items()}

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
            "ruffles": len(all_ruffles),
            "materials": sorted({m.name for o in meshes for m in o.data.materials}),
        },
        "measurements": measurements,
    }


def main() -> None:
    """Generate the test collection, validate it, and report."""
    ut.setup_units()
    build_test_cells()
    report = validate_immune_cells()

    print("[immune] built {} cells in {}".format(report["checks"]["cells"], TEST_COLLECTION))
    print("[immune] checks: {}".format(report["checks"]))
    for key in ("mean_radius_by_class", "nk_vs_tcell_radius_ratio", "process_extent"):
        if key in report["measurements"]:
            print("[immune] {}: {}".format(key, report["measurements"][key]))
    if report["ok"]:
        print("[immune] VALIDATION PASSED")
    else:
        for failure in report["failures"]:
            print("[immune] FAIL: {}".format(failure))
        raise SystemExit("[immune] VALIDATION FAILED ({} problems)".format(
            len(report["failures"])))


if __name__ == "__main__":
    main()
