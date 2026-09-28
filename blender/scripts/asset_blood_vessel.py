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
PREFIX_PATH = "VESSEL_PATH"

SUFFIX_OUTER_WALL = "OUTER_WALL"
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

#: Radial irregularity, as a fraction of the radius. Visible but not lumpy --
#: a vessel is a tube that wanders, not a potato.
IRREGULARITY_RADIUS = 0.055
#: Spacing between noise samples along the length. Shorter spacing means more
#: of the length varies, which is what makes the calibre read as irregular.
NOISE_SCALE_ALONG = 0.045
NOISE_WEIGHT_FINE = 0.40
NOISE_SCALE_FINE = 0.13

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
RBC_SPIN_STEPS = 20

#: How many red blood cells a default vessel carries. Low enough to stay
#: cheap, high enough that the lumen reads as full of blood rather than as an
#: empty pipe.
RBC_COUNT = 15
#: Keep cells off the wall: a fraction of the room actually available between
#: the lumen surface and the outermost a cell can reach.
RBC_WALL_CLEARANCE = 0.72
#: A cell is a disc, not a point, and it is randomly tilted, so its outer edge
#: reaches further than its radius. Without this margin a tilted disc pokes
#: through the lumen surface -- the exact artefact the hollow build exists to
#: avoid.
RBC_CLEARANCE_MARGIN = 1.35

#: Slight size variation between cells, so a packed lumen is not a grid.
RBC_SIZE_JITTER = 0.12

#: Tolerance when comparing a cell's *measured* centreline distance against the
#: radius it was *placed* at. These differ by a fraction of a micrometre
#: because Follow Path applies the offset in the curve's own frame, whose roll
#: is Blender's to choose and is not the sweep's parallel-transport frame. The
#: absolute containment check -- no cell reaching the wall -- stays strict; this
#: only relaxes the self-consistency check, and the residual is bounded.
RBC_FRAME_ROLL_TOLERANCE_UM = 0.5

PROP_CLASS = "vessel_class"
PROP_NOTE = "representation_note"
#: Set on the one red blood cell that is the shared source mesh rather than an
#: instance of it. Lets the validator tell the source from its instances
#: without matching on a name, which is how the source gets renamed.
PROP_RBC_SOURCE = "rbc_source"

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


def _material(spec: dict):
    """Build one :data:`MAT_*` material. Emission strength starts at zero."""
    return ut.principled_material(
        spec["name"], base_color=spec["base_color"], roughness=spec["roughness"],
        alpha=spec["alpha"], emission=spec["emission"], emission_strength=0.0,
        subsurface=spec.get("subsurface", 0.0),
    )


def ensure_materials() -> dict:
    """Create or update all four vessel materials. Idempotent."""
    return {key: _material(spec) for key, spec in (
        ("wall", MAT_WALL), ("endothelium", MAT_ENDOTHELIAL),
        ("lumen", MAT_LUMEN), ("rbc", MAT_RBC))}


def _rbc_extent() -> float:
    """Half-extent a randomly tilted disc presents to the lumen.

    Deliberately larger than the cell radius: a tilted disc's outer edge
    reaches past its own radius, and a cell placed exactly one radius from the
    centreline pokes through the wall about half the time.
    """
    return RBC_DIAMETER_UM * 0.5 * RBC_CLEARANCE_MARGIN


def _placement_radius(lumen_radius: float) -> float:
    """How far off the centreline a cell centre may sit and stay inside.

    Shared by the builder and the validator on purpose. When these two
    disagreed about where a cell fits, the validator would report a correctly
    built vessel as broken -- which is the same class of bug that made an
    earlier version of this project measure declared radius two different ways.
    """
    return max(lumen_radius - _rbc_extent(), 0.0) * RBC_WALL_CLEARANCE


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


def _rbc_mesh(name: str = "MESH_{}".format(PREFIX_RBC)) -> object:
    """A biconcave disc: an RBC, spun from a profile.

    The profile is the cell's cross-section: an open polyline running from the
    centre of one face, out over the rim, and back to the centre of the other
    face. Spinning that about the cell's short axis closes the surface at both
    ends, because each end sits *on* the axis and all its spun copies collapse
    to one point.

    Both faces are in the profile, which is the part that is easy to get wrong:
    a profile running only from rim to centre revolves into an open bowl, not
    a disc. And the dimple is what makes it a red blood cell rather than a
    lentil -- it is why the cell deforms to squeeze through a capillary
    narrower than itself, which the test scene checks by building one.

    The disc ends up lying on its side, axis along Y. That is harmless and
    cheaper to fix than to special-case: the discs are randomly rotated anyway.
    """
    half = RBC_DIAMETER_UM * 0.5
    dimple = RBC_HALF_THICKNESS_UM * RBC_DIMPLE
    rim = RBC_HALF_THICKNESS_UM

    # s sweeps 0 at the face centre to 1 at the rim. The order matters twice
    # over: the profile's *ends* have to be the on-axis points, or the revolve
    # leaves a hole at the rim instead of closing there. And thickness has to
    # grow towards the rim, or the disc comes out biconvex -- a lens, not a red
    # blood cell.
    half_profile = []
    for i in range(RBC_PROFILE_STEPS + 1):
        s = i / float(RBC_PROFILE_STEPS)
        # Smoothstep so the dimple and the rim both curve instead of creasing.
        eased = s * s * (3.0 - 2.0 * s)
        half_profile.append((half * eased, dimple + (rim - dimple) * eased))

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
                   angle=2.0 * math.pi, steps=RBC_SPIN_STEPS, use_merge=True)
    # Weld the seam and the two on-axis poles, then dissolve what the welding
    # left behind. Order matters: removing the doubles is what collapses the
    # seam ring and the axis points into single vertices, and *that* is what
    # leaves zero-area faces behind, so dissolving first would miss them.
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
    bmesh.ops.dissolve_degenerate(bm, dist=1e-6, edges=bm.edges)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
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


def create_red_blood_cell(index: int = 1, collection=None,
                          location=(0.0, 0.0, 0.0), parent=None):
    """Create the shared red blood cell: a simple biconcave disc.

    One source object for the whole project, shared by every vessel's cells.
    This is the *source* that everything else instances, which is why it is
    the one red blood cell in the scene that is not an instance -- there has to
    be something to instance. It is hidden from render and viewport, because
    its job is to be the datablock, not to be seen.

    Calling this more than once returns the same object rather than piling up
    near-duplicates, which is the same rule every other builder here follows.

    Args:
        index: 1-based ordinal, used for the object name.
        collection: Target collection. Defaults to ``05_BLOOD_VESSELS``.
        location: World-space location.
        parent: Optional parent object. Left alone by default, so the source
            is not dragged around when a vessel root moves.

    Returns:
        The shared red blood cell object.
    """
    coll = collection or ut.resolve_collection(COLLECTION)
    obj = ut.get_or_create_object(
        ut.obj_name(PREFIX_RBC, index), coll,
        lambda n: _rbc_mesh("MESH_{}".format(n)), location=location)
    if parent is not None:
        ut.set_parent(obj, parent)
    ut.assign_material(obj, ensure_materials()["rbc"])
    obj[PROP_RBC_SOURCE] = True
    obj.hide_render = True
    obj.hide_viewport = True
    return obj


def create_blood_vessel(path_points, radius: float = VENULE_RADIUS_UM, *,
                        rbc_count: int = RBC_COUNT, index: int = 1,
                        collection=None, parent=None, seed: int = 0) -> dict:
    """Create a blood vessel along *path_points*.

    Builds three nested shells and a scatter of instanced red blood cells. The
    wall is opaque, the endothelial lining translucent, and the lumen surface
    dark, so the red blood cells are visible from outside while the vessel
    still reads as a solid tube.

    Nothing is animated. The centreline is kept as a curve and every red
    blood cell carries a Follow Path constraint, so blood flow later is a
    matter of keying ``offset_factor`` per cell and nothing else.

    Args:
        path_points: Sequence of at least 2 world-space points defining the
            centreline. A gentle S-curve is the default in the test scene.
        radius: Lumen-scale outer radius in micrometres. Use
            :data:`VENULE_RADIUS_UM` for a readable vessel;
            :data:`CAPILLARY_RADIUS_UM` is faithful but barely wider than one
            red blood cell.
        rbc_count: Number of red blood cells to place. All share one mesh.
        index: 1-based ordinal, used for the object names.
        collection: Target collection. Defaults to ``05_BLOOD_VESSELS``.
        parent: Optional parent object.
        seed: Controls all variation. Same seed, same vessel, every time.

    Returns:
        A dict with the shell objects, the path object, the red blood cell
        source and the instances, under the keys ``"wall"``, ``"endothelium"``,
        ``"lumen"``, ``"path"``, ``"rbc_source"``, ``"rbcs"`` and ``"root"``.
    """
    if radius <= 0.0:
        raise ValueError("radius must be positive, got {}".format(radius))
    coll = collection or ut.resolve_collection(COLLECTION)
    rng = random.Random(seed)
    materials = ensure_materials()

    stations = _resample_even(_catmull_rom(path_points), STATIONS)
    frames = _parallel_frames(stations)
    factor = _radius_profile(rng)

    lumen_radius = max(radius - WALL_THICKNESS_UM - LUMEN_THICKNESS_UM, radius * 0.2)
    radii = {"wall": radius,
             "endothelium": radius - WALL_THICKNESS_UM,
             "lumen": lumen_radius}

    root = ut.new_empty(ut.obj_name(PREFIX, index), coll, size=radius * 0.5,
                        parent=parent)
    root[PROP_CLASS] = "BLOOD_VESSEL"
    root[PROP_NOTE] = ILLUSTRATIVE_NOTE

    shells = {}
    for key, suffix in (("wall", SUFFIX_OUTER_WALL),
                        ("endothelium", SUFFIX_ENDOTHELIAL),
                        ("lumen", SUFFIX_INNER_LUMEN)):
        obj_name = ut.obj_name("{}_{}".format(PREFIX, suffix), index)
        mesh = _tube_mesh("MESH_{}".format(obj_name), frames, radii[key], factor)
        shell = ut.get_or_create_object(obj_name, coll, lambda n, m=mesh: m)
        ut.set_parent(shell, root)
        ut.assign_material(shell, materials[key])
        shells[key] = shell

    path = _path_object(ut.obj_name(PREFIX_PATH, index), stations, coll,
                        parent=root)

    # --- red blood cells ---------------------------------------------------
    # Placement is in the vessel's local space: the cells are positioned on the
    # centreline and then constrained onto the path, so moving the vessel root
    # carries the whole blood column with it. The source is shared across every
    # vessel in the project, so only the instance *names* are per-vessel.
    rbc_source = create_red_blood_cell(index=1, collection=coll)
    rbc_prefix = ut.obj_name(PREFIX_RBC, index)

    clearance = _placement_radius(lumen_radius)
    rbcs = []
    for i in range(rbc_count):
        u = (i + 0.5) / float(max(1, rbc_count))
        # Deterministic offset within the tube, on a loose spiral so the cells
        # do not line up in a single row.
        angle = u * 9.4 * math.pi + rng.uniform(-0.35, 0.35)
        distance = clearance * math.sqrt(rng.uniform(0.05, 1.0))
        radial = Vector((math.cos(angle) * distance,
                         math.sin(angle) * distance, 0.0))
        # Express the offset in the *path's own frame* at this station, not in
        # root space. Follow Path applies the cell's location in the curve's
        # frame, so a root-space offset is remapped by the tangent rotation and
        # ends up pointing partly along the vessel -- on a curving path that
        # puts the cell measurably further from the centreline than it was
        # placed, which is how cells ended up within a hair of the wall.
        station = min(int(round(u * (len(frames) - 1))), len(frames) - 1)
        local = frames[station].to_3x3() @ radial

        size = 1.0 + rng.uniform(-RBC_SIZE_JITTER, RBC_SIZE_JITTER)
        cell = ut.instance_linked(
            rbc_source, ut.obj_name(rbc_prefix, i + 2), coll,
            location=local,
            rotation=(rng.uniform(0.0, math.pi), rng.uniform(0.0, math.pi),
                      rng.uniform(0.0, math.pi)),
            scale=(size, size, size), parent=root)
        cell[PROP_RBC_SOURCE] = False
        follow = cell.constraints.new("FOLLOW_PATH")
        follow.name = "FLOW"
        follow.target = path
        follow.use_curve_follow = True
        follow.forward_axis = "TRACK_NEGATIVE_Z"
        follow.up_axis = "UP_Y"
        # Required, and easy to miss. With use_fixed_location off, Follow Path
        # ignores offset_factor entirely and uses the frame-based `offset`
        # instead, so every cell collapses onto frame 1's position on the path
        # -- the whole blood column piles up at one end of the vessel, while
        # the offset_factor values look perfectly correct in the UI.
        follow.use_fixed_location = True
        # Spread along the path so a keyframe on offset_factor moves each cell
        # from a different start point, which is what makes flow read as flow.
        follow.offset_factor = u
        cell[PROP_CLASS] = "RED_BLOOD_CELL"
        rbcs.append(cell)

    return {"root": root, "path": path, "rbc_source": rbc_source,
            "rbcs": rbcs, **shells}


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
        ut.clear_collection(coll, PREFIX)
        ut.clear_collection(coll, PREFIX_RBC)
        ut.clear_collection(coll, PREFIX_PATH)

    # A capillary gets no red blood cells, and that is not a shortcut: its
    # lumen is ~1.3 um across and a red blood cell is 7.5 um. Cells deform to
    # squeeze through, which a rigid disc cannot show, so a cell in a capillary
    # would be geometry poking through the wall. The validator asserts the
    # absence rather than ignoring it.
    cases = [
        ("capillary", CAPILLARY_RADIUS_UM, 0),
        ("venule", VENULE_RADIUS_UM, 15),
        ("venule_sparse", VENULE_RADIUS_UM * 2.0, 40),
    ]

    built = []
    for i, (_label, radius, count) in enumerate(cases):
        offset = Vector((0.0, TEST_RADIUS_CM * i, 0.0))
        path = [Vector(p) + offset for p in default_path(radius)]
        built.append(create_blood_vessel(path, radius, rbc_count=count,
                                         index=i + 1, collection=coll, seed=7 + i))
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


def _mean_radius(obj, stations, samples: int = 25) -> float:
    """Mean distance from a shell's vertices to the vessel centreline.

    Distance to the *path*, not to the object origin. A vessel is far longer
    than it is wide, so distance from the origin measures mostly path length:
    an earlier version of this validator did that and confidently reported a
    1.3 um capillary as 106 um across, which made every containment check
    below it pass without ever being tested.

    Args:
        obj: The shell object.
        stations: Centreline points, in the shell's local space.
        samples: How many stations to test against. Sub-sampling keeps this
            linear rather than quadratic; the centreline is smooth enough that
            a quarter of the stations places the radius to well within the
            tolerance a containment check needs.
    """
    if not obj.data.vertices or not stations:
        return 0.0
    step = max(1, len(stations) // samples)
    sampled = stations[::step]
    total = 0.0
    for vert in obj.data.vertices:
        co = vert.co
        total += min((co - s).length_squared for s in sampled) ** 0.5
    return total / len(obj.data.vertices)


def _nearest_station(point, stations, step: int = 1):
    """Index of the centreline station closest to *point*, and that distance."""
    sampled = stations[::step]
    best_i, best_d = 0, 1e18
    for i, s in enumerate(sampled):
        d = (point - s).length_squared
        if d < best_d:
            best_i, best_d = i, d
    return best_i * step, best_d ** 0.5


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
    measurements = {"radius": {}, "rbc_clearance": {}, "rbc_spread": {}}

    roots = [o for o in coll.objects
             if o.type == "EMPTY" and o.get(PROP_CLASS) == "BLOOD_VESSEL"]
    shells = [o for o in coll.objects if o.type == "MESH"
              and any(s in o.name for s in (SUFFIX_OUTER_WALL, SUFFIX_ENDOTHELIAL,
                                            SUFFIX_INNER_LUMEN))]
    # Instances only. The source mesh is excluded by its own property, not by
    # name, so a renamed source cannot quietly become an instance of itself.
    rbcs = [o for o in coll.objects if o.type == "MESH"
            and o.get(PROP_CLASS) == "RED_BLOOD_CELL"]

    if not roots:
        return {"ok": False, "failures": ["no vessels built"],
                "checks": {}, "measurements": measurements}

    for obj in shells:
        failures.extend(_check_topology(obj))
    for obj in rbcs:
        failures.extend(_check_topology(obj))

    # --- one shared red blood cell mesh, for the whole project --------------
    sources = [o for o in coll.objects if o.get(PROP_RBC_SOURCE)]
    for source in sources:
        failures.extend(_check_topology(source))
    if len(sources) > 1:
        failures.append("{} red blood cell sources, expected 1: {}".format(
            len(sources), sorted(o.name for o in sources)))
    shared = {r.data.name for r in rbcs}
    if rbcs and len(shared) != 1:
        failures.append("red blood cells use {} mesh datablocks, expected 1: "
                        "{}".format(len(shared), sorted(shared)))
    for source in sources:
        if rbcs and source.data.name not in shared:
            failures.append("{}: instances do not share the source mesh".format(
                source.name))
    measurements["rbc_mesh_datablocks"] = len(shared)
    measurements["rbc_count"] = len(rbcs)

    # --- shell nesting -----------------------------------------------------
    suffixes = (("wall", SUFFIX_OUTER_WALL), ("endothelium", SUFFIX_ENDOTHELIAL),
                ("lumen", SUFFIX_INNER_LUMEN))
    lumen_radii = {}
    for root in roots:
        path = _path_of(root, coll)
        if path is None:
            failures.append("{}: no flow path; the red blood cells have nothing "
                            "to follow".format(root.name))
            continue
        stations = _path_stations(path)

        found = {}
        for obj in coll.objects:
            if obj.type != "MESH" or obj.parent != root:
                continue
            for key, suffix in suffixes:
                if suffix in obj.name:
                    found[key] = _mean_radius(obj, stations)
        if set(found) != {"wall", "endothelium", "lumen"}:
            failures.append("{}: missing a shell layer (found {})".format(
                root.name, sorted(found)))
            continue
        lumen_radii[root.name] = found["lumen"]
        measurements["radius"][root.name] = {k: round(v, 3) for k, v in found.items()}
        if not found["wall"] > found["endothelium"] > found["lumen"] > 0.0:
            failures.append("{}: shells are not nested wall>endo>lumen ({})".format(
                root.name, {k: round(v, 2) for k, v in found.items()}))

    # --- red blood cells are inside the lumen ------------------------------
    for root in roots:
        lumen_r = lumen_radii.get(root.name)
        if lumen_r is None:
            continue
        # Follow Path puts the cell's origin on the centreline and applies the
        # cell's own location as an offset from it, so the magnitude of that
        # offset *is* the distance from the centreline. No evaluation needed.
        cells = [c for c in coll.objects
                 if c.type == "MESH" and c.parent == root
                 and c.get(PROP_CLASS) == "RED_BLOOD_CELL"]
        if not cells:
            # A lumen too narrow for a cell must be empty, not full of cells
            # clipping through it.
            if lumen_r > _rbc_extent():
                failures.append("{}: no red blood cells in a lumen {} um wide; "
                                "it has room for them".format(
                                    root.name, round(lumen_r, 1)))
            continue
        if lumen_r <= _rbc_extent():
            failures.append("{}: a red blood cell in a {} um lumen cannot fit; "
                            "a cell is {} um across".format(
                                root.name, round(lumen_r, 1), RBC_DIAMETER_UM))
            continue

        # Check where the cells actually end up, constraints evaluated.
        path = _path_of(root, coll)
        stations_world = [root.matrix_world @ s for s in _path_stations(path)]
        positions = _evaluated_positions(cells)
        extent = _rbc_extent()
        indices, worst = [], 0.0
        for cell in cells:
            point = positions[cell.name]
            index, distance = _nearest_station(point, stations_world,
                                               step=max(1, len(stations_world) // 40))
            indices.append(index)
            worst = max(worst, distance + extent)

        allowed = _placement_radius(lumen_r)
        measurements["rbc_clearance"][root.name] = round(lumen_r - worst, 3)
        if worst > lumen_r + 1e-6:
            failures.append("{}: a red blood cell reaches {:.2f} um from the "
                            "centreline; the lumen is only {} um wide".format(
                                root.name, worst, round(lumen_r, 2)))
        # Compare like with like: *worst* includes the cell's own extent, while
        # *allowed* is a centreline distance. Comparing the two against each
        # other flags every cell as a breach, because the cell is always wider
        # than its placement radius.
        if worst - _rbc_extent() > allowed + RBC_FRAME_ROLL_TOLERANCE_UM:
            failures.append("{}: a red blood cell sits {:.2f} um off the centreline, "
                            "past the {:.2f} um that leaves clearance to the wall".format(
                                root.name, worst - _rbc_extent(), allowed))

        # The cells must actually be spread along the vessel. This is the check
        # that catches a Follow Path constraint which resolves to a single
        # point: the geometry is all correct and every cell is inside the
        # lumen, they are just all in the same place.
        spread = (max(indices) - min(indices)) / float(max(1, len(stations_world) - 1))
        measurements["rbc_spread"][root.name] = round(spread, 3)
        if spread < 0.4:
            failures.append("{}: red blood cells occupy only {:.0%} of the vessel; "
                            "they are not spread along it".format(
                                root.name, spread))

    # --- instancing, not duplication ---------------------------------------
    per_vessel = sorted({r.parent.name for r in rbcs if r.parent is not None})
    measurements["rbcs_per_vessel"] = {
        name: sum(1 for r in rbcs if r.parent is not None and r.parent.name == name)
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
            "rbcs": len(rbcs),
            "materials": sorted({m.name for o in shells + rbcs for m in o.data.materials}),
        },
        "measurements": measurements,
    }


def main() -> None:
    """Generate the test collection, validate it, and report."""
    ut.setup_units()
    build_test_vessel()
    report = validate_blood_vessel()

    print("[vessel] built {} vessels in {}".format(
        report["checks"].get("vessels"), TEST_COLLECTION))
    print("[vessel] checks: {}".format(report["checks"]))
    for key in ("rbc_mesh_datablocks", "rbc_count", "rbc_clearance"):
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
