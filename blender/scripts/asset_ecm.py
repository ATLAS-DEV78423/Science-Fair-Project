"""Extracellular matrix asset: ECM_FIBER_NNN.

Scientific reference
--------------------
The fibrous scaffold everything is embedded in, and it is what makes a cell
suspension read as *tissue* rather than as objects floating in a void.

* **Collagen fibres** -- the dominant component. Long, thin, roughly 0.05-10 um
  in diameter and often hundreds of micrometres long, so they read as long
  curves rather than as tubes. In a tumour the matrix is often denser and
  stiffer than in healthy tissue, which also limits how far a virus can
  physically spread.
* **Ground substance** -- the hydrated gel filling the space between fibres.
  Nearly transparent, so it should read as depth rather than as a solid.
* **Fibronectin / laminin** -- the adhesive proteins that actually anchor cells
  to the matrix and to each other. Contact inhibition, the thing tumour cells
  lose, is partly maintained through these.

This is the one asset in the project that is genuinely a large number of
objects. A convincing matrix is hundreds of fibres, which is exactly why
``ut.instance_linked`` matters: a single fibre mesh instanced several hundred
times, with per-instance rotation, is one datablock and a few hundred cheap
objects. Do not build this as a merged mesh -- it has to stay editable per
fibre, and a merged blob cannot be animated.

**The network is visually connected, not topologically connected.** Fibres cross
and overlap so the matrix reads as a web, but no two fibres share a junction
point. That is a consequence of instancing, not an oversight: the source mesh's
endpoints are transformed differently per instance, so two instances can never
agree on where a node is. True node-sharing would require every fibre to be
unique geometry, which would forfeit per-fibre editability and make the file far
heavier. The read is what a science-fair audience needs; the junction graph is
not measured here.

Not yet implemented: ground substance, and fibronectin / laminin.
"""

from __future__ import annotations

import math
import os
import random
import sys

import bpy
from mathutils import Quaternion, Vector
from mathutils import noise as mnoise

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utilities as ut  # noqa: E402
# Reused rather than reimplemented. These three are proven and unit-tested in
# the vessel module, and a second copy of parallel-transport framing is a
# second chance to get the inflection-point flip wrong.
# ponytail: private imports across modules. Promote _catmull_rom,
# _parallel_frames and _tube_mesh into utilities.py once a third module needs
# them; two modules is not yet.
from asset_blood_vessel import (  # noqa: E402
    _catmull_rom,
    _parallel_frames,
    _tube_mesh,
)

COLLECTION = "06_EXTRACELLULAR_MATRIX"
PREFIX = "ECM_FIBER"

#: Reference dimensions in micrometres == Blender units here.
FIBER_RADIUS_UM = 0.06
FIBER_LENGTH_UM = 180.0

#: The radius actually *drawn*, which is not :data:`FIBER_RADIUS_UM`.
#:
#: Real collagen is thin enough to be honest and too thin to see. Rendering it
#: at its declared size makes the matrix invisible, so the drawn value is
#: exaggerated -- but this project's own README records that 0.16 um detail was
#: invisible and 0.30 um detail was raised to it, and an early render at 0.18 um
#: radius put a 60-pixel rope across a 10 um cell. "Subtle enough that cells
#: remain readable" outranks the ruffle lesson here, because the two assets are
#: not the same problem: a ruffle is *part of* the cell and has to be seen,
#: while the matrix sits in front of the cell and must not compete with it.
#:
#: At the ~14 um camera standoff the project uses, 0.10 um across is roughly 20
#: pixels: a thread that is unmistakably there and covers very little. It also
#: goes near-invisible on the macro camera, which is the right behaviour -- a
#: real matrix is structure you notice when you look for it.
FIBER_RENDER_RADIUS_UM = 0.05

#: The region a tumour environment occupies, in micrometres, as
#: ``((x0, y0, z0), (x1, y1, z1))``. Wide enough in x to hold the 420 um
#: vessel with tissue around it.
DEFAULT_BOUNDS_UM = ((-280.0, -150.0, -150.0), (280.0, 150.0, 150.0))

#: Expected fibres per million cubic micrometres at ``density == 1.0``. Chosen
#: so the default region holds ~1500 fibres at full density: dense enough to
#: read as stroma, few enough that the collection stays browsable.
FIBERS_PER_MILLION_UM3 = 30.0

#: Spatial frequency of the density field, in 1/um. 0.011 gives a wavelength
#: around 90 um, so a dense region and an open region both fit several times
#: inside the default bounds.
DENSITY_FIELD_SCALE = 0.011

#: Preferred fibre axes and their weights. Collagen is *anisotropic* -- fibres
#: align with mechanical stress rather than pointing every which way -- and the
#: visual difference is large: three weak biases read as tissue, a uniform
#: random soup reads as static. The per-instance jitter is large enough that no
#: two fibres end up parallel, which is what stops this looking like three
#: bundles.
PREFERRED_AXES = (
    (Vector((1.0, 0.0, 0.0)), 0.42),
    (Vector((0.0, 1.0, 0.0)), 0.33),
    (Vector((0.0, 0.0, 1.0)), 0.25),
)
#: Radians of wander added to the sampled preferred axis. At 0.85 the bias is
#: still a bias and no fibre is exactly axis-aligned.
ORIENTATION_JITTER_RAD = 0.85

#: Per-instance length, as a fraction of :data:`FIBER_LENGTH_UM`.
LENGTH_JITTER = (0.55, 1.45)
#: Per-instance multipliers on the two off-axis components of the instance
#: scale. Scaling the off-axis axes of an S-bend is the *only* way curvature
#: varies while the mesh is shared, and it is enough: a 1.6x reads as a hook and
#: a 0.75x as a lazy arc.
#:
#: The cost is that those same axes carry the tube cross-section, so off-axis
#: scale *is* thickness scale. These two ranges are deliberately tight for that
#: reason -- their product runs 0.6 to 2.0, putting the drawn radius between
#: 0.03 and 0.10 um at :data:`FIBER_RENDER_RADIUS_UM` = 0.05. Widening
#: BEND_JITTER straightens the curve variation and thickens the fibres with it,
#: and the thick end stops reading as a fibre.
BEND_JITTER = (0.75, 1.6)
THICKNESS_JITTER = (0.8, 1.25)

#: Fraction of fibres placed beside an already-placed fibre rather than at a
#: fresh random point, and the radius of that offset in micrometres. This is
#: what makes the network read as connected.
CROSSING_FRACTION = 0.42
CROSSING_RADIUS_UM = 12.0

#: Grid resolution and cell size, in micrometres, for the two checks that ask
#: whether placement is nonuniform. The cell size is the density field's own
#: wavelength on purpose: a cell much larger than the wavelength averages the
#: field away to a constant, and one much smaller finds empty cells.
FIELD_GRID = 7
FIELD_CELL_UM = 90.0

#: Give-up threshold on rejection sampling, as a multiple of the target count.
#: The density field averages ~0.5, so a target is typically reached in about
#: twice its count in draws; 12 leaves room for a field-biased region without
#: hanging on a pathological seed.
MAX_ATTEMPTS_RATIO = 12.0

#: Lateral offset of the source fibre's S-bend, in micrometres. A gentle bend:
#: the instance scale jitter does the dramatic work.
SOURCE_BEND_UM = 18.0
SOURCE_CONTROL_POINTS = 4
SOURCE_SAMPLES_PER_SEGMENT = 3

#: Alpha ceiling for the collagen material. The matrix has to stay *subtle* --
#: the whole reason it exists is to make the cells it surrounds read as cells.
#: Everything translucent in this project stacks, and a 1500-fibre network
#: stacked over a cell is exactly the wash-out the README warns about.
MAX_FIBER_ALPHA = 0.35

#: The source fibre owns ordinal 1; instances start at 2. The source is a real
#: object in the collection, so the numbering has to say which is which rather
#: than leaving the validator to guess.
SOURCE_INDEX = 1

#: Custom property marking the shared source, so a human can find it in the
#: outliner without knowing about the ordinal convention.
PROP_IS_SOURCE = "ecm_is_source"

MAT_COLLAGEN = dict(
    name="MAT_ECM_Collagen",
    base_color=(0.86, 0.84, 0.80, 1.0),
    roughness=0.42,
    subsurface=0.10,
    alpha=0.18,
    emission=(0.86, 0.84, 0.80, 1.0),
)

FORBIDDEN_NAMES = {"Cube", "Sphere", "Object", "Mesh", "Material", "Icosphere"}

TEST_COLLECTION = "TEST_ECM"
TEST_DENSITY = 0.5
#: Densities the validator sweeps to prove ``density`` is actually wired to
#: anything. Three is enough to show monotonicity; more is just runtime.
DENSITY_SWEEP = (0.25, 0.5, 0.75)


# ---------------------------------------------------------------------------
# Density field
# ---------------------------------------------------------------------------


def _seed_offset(seed: int) -> Vector:
    """A large position offset that makes `seed` change the field.

    Pure function of *seed*. Offsets the *sample position* rather than calling
    ``mnoise.seed_set``, because the seed_set route mutates global state and
    would make :func:`density_at` depend on whatever ran before it.
    """
    rng = random.Random(seed)
    return Vector((rng.uniform(0.0, 1000.0),
                   rng.uniform(0.0, 1000.0),
                   rng.uniform(0.0, 1000.0)))


# ponytail: purity assumes the global Perlin seed is at its default, which
# nothing in this project changes. Thread the seed through noise_basis or a
# private noise instance if a future asset starts calling mnoise.seed_set.
def density_at(point, seed: int = 0) -> float:
    """Local matrix density at *point*, in ``[0.0, 1.0]``.

    **The public hook a later viral-spread animation will use.** A virion
    moving through stroma is slowed where this returns high, and travels
    through where it returns low -- so the animation samples this same function
    rather than re-deriving "how dense is it here" from the fibre objects and
    getting a subtly different answer.

    Two octaves of Perlin noise remapped to ``[0, 1]``: a coarse octave for the
    large dense and open regions, a finer one so the boundary between them is
    ragged rather than a clean isosurface. Pure in (*point*, *seed*) -- no
    global state, same arguments always give the same value.

    Args:
        point: World-space position, micrometres.
        seed: The region seed. A different seed is a different network.

    Returns:
        Density in ``[0.0, 1.0]``.
    """
    v = Vector(point)
    offset = _seed_offset(seed)
    coarse = mnoise.noise(v * DENSITY_FIELD_SCALE + offset)
    fine = mnoise.noise(v * DENSITY_FIELD_SCALE * 2.7 + offset * 1.7)
    field = 0.5 + 0.5 * (0.65 * coarse + 0.35 * fine)
    return min(1.0, max(0.0, field))


def _expected_count(volume_um3: float, density: float) -> int:
    """Fibres to place for a region of *volume_um3* at *density*."""
    return max(0, int(FIBERS_PER_MILLION_UM3 * density * volume_um3 / 1e6))


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------


def ensure_materials() -> dict:
    """Create or update this module's material. Idempotent."""
    return {"collagen": ut.principled_material(
        MAT_COLLAGEN["name"], base_color=MAT_COLLAGEN["base_color"],
        roughness=MAT_COLLAGEN["roughness"], alpha=MAT_COLLAGEN["alpha"],
        emission=MAT_COLLAGEN["emission"], emission_strength=0.0,
        subsurface=MAT_COLLAGEN.get("subsurface", 0.0))}


# ---------------------------------------------------------------------------
# Fibre source
# ---------------------------------------------------------------------------


def _fiber_source(collection):
    """Build the one shared fibre mesh, as an object, and return it.

    A gentle S-bend along local +X, swept as a tube. The bend is what the
    per-instance scale jitter works on; everything else about the shape is
    shared, which is the price of instancing and is paid knowingly.
    """
    half = FIBER_LENGTH_UM * 0.5
    bend = SOURCE_BEND_UM
    control = [Vector((-half, 0.0, 0.0)),
               Vector((-half / 3.0, bend, 0.0)),
               Vector((half / 3.0, -bend, 0.0)),
               Vector((half, 0.0, 0.0))]
    path = _catmull_rom(control, SOURCE_SAMPLES_PER_SEGMENT)
    frames = _parallel_frames(path)

    def _round(_u, _theta):
        return 1.0

    mesh_factory = lambda name: _tube_mesh(name, frames, FIBER_RENDER_RADIUS_UM, _round)
    source = ut.get_or_create_object(ut.obj_name(PREFIX, SOURCE_INDEX), collection,
                                     mesh_factory)
    source[PROP_IS_SOURCE] = True
    ut.assign_material(source, ensure_materials()["collagen"])
    return source


# ---------------------------------------------------------------------------
# Region builder
# ---------------------------------------------------------------------------


def _pick_axis(rng):
    """Draw a preferred fibre axis, honouring :data:`PREFERRED_AXES` weights."""
    roll = rng.random()
    cumulative = 0.0
    for axis, weight in PREFERRED_AXES:
        cumulative += weight
        if roll <= cumulative:
            return axis
    return PREFERRED_AXES[-1][0]


def _fiber_orientation(rng):
    """A rotation for one fibre: a jittered preferred axis, spun freely about it.

    The free spin is what stops the network reading as three bundles. The axis
    only biases the choice; it does not constrain it.
    """
    direction = _pick_axis(rng)
    jitter = Vector((rng.gauss(0.0, 1.0), rng.gauss(0.0, 1.0), rng.gauss(0.0, 1.0)))
    if jitter.length > 1e-9:
        direction = direction + jitter.normalized() * rng.uniform(0.0, ORIENTATION_JITTER_RAD)
    if direction.length < 1e-9:
        direction = Vector((1.0, 0.0, 0.0))
    return Quaternion(direction.normalized(), rng.uniform(0.0, math.tau))


def create_ecm_region(bounds=DEFAULT_BOUNDS_UM, density: float = 1.0,
                      seed: int = 0, parent=None, collection=None,
                      clear: bool = True):
    """Scatter a collagen network through *bounds* and return the fibre objects.

    Rejection sampling against :func:`density_at`: draw a point, keep it with
    probability equal to the local field. That is what produces genuinely
    nonuniform density -- local dense regions and open regions fall out of the
    field rather than being placed by hand -- and it is the same field a later
    viral-spread animation will sample. Because the loop stops at the target
    count, the *first* points to clear the threshold are the ones kept, so the
    survivors are biased toward the dense parts of the field for free.

    Fibres are placed by their *origin* and are not shrunk to fit. A real matrix
    has no hard boundary, and forcing every fibre inside the box would carve a
    visible edge into the tissue. Origins are validated to be in bounds.

    Args:
        bounds: ``((x0, y0, z0), (x1, y1, z1))`` in micrometres.
        density: ``0.0``-``1.0``. Scales the expected fibre count. The density
            *field* is applied at every density, so dense and open regions
            exist even at low settings.
        seed: Seeded, so the same arguments rebuild the same network.
        parent: Optional parent. Origins become parent-local.
        collection: Owning collection. Defaults to :data:`COLLECTION`.
        clear: Remove this prefix first. On by default so a second call with the
            same arguments is a rebuild rather than an accumulation, which is
            what the idempotency check relies on.

    Returns:
        The list of fibre objects, source excluded.
    """
    coll = collection or ut.resolve_collection(COLLECTION)
    if clear:
        ut.clear_collection(coll, prefix=PREFIX)

    source = _fiber_source(coll)
    if parent is not None:
        ut.set_parent(source, parent)

    lo, hi = Vector(bounds[0]), Vector(bounds[1])
    span = hi - lo
    target = _expected_count(span.x * span.y * span.z, density)
    if target <= 0:
        return []

    rng = random.Random(seed)
    anchors: list = []
    fibers: list = []
    attempts = 0
    max_attempts = int(target * MAX_ATTEMPTS_RATIO) + 8

    while len(fibers) < target and attempts < max_attempts:
        attempts += 1
        if anchors and rng.random() < CROSSING_FRACTION:
            # Sit beside a fibre already placed. This is what makes the network
            # read as a web rather than as a scatter, and it is the whole of
            # the "interconnected" requirement.
            origin = anchors[rng.randrange(len(anchors))].copy()
            nudge = Vector((rng.gauss(0.0, 1.0), rng.gauss(0.0, 1.0),
                            rng.gauss(0.0, 1.0)))
            if nudge.length > 1e-9:
                origin = origin + nudge.normalized() * rng.uniform(0.0, CROSSING_RADIUS_UM)

        else:
            origin = Vector((lo.x + rng.random() * span.x,
                             lo.y + rng.random() * span.y,
                             lo.z + rng.random() * span.z))

        # A crossing-biased origin walks off the edge of the region when its
        # anchor is already near an edge, so bounds are enforced here rather
        # than trusted to the sampler that produced the anchor.
        if not (lo.x <= origin.x <= hi.x and lo.y <= origin.y <= hi.y
                and lo.z <= origin.z <= hi.z):
            continue

        if rng.random() >= density_at(origin, seed):
            continue

        bend = rng.uniform(*BEND_JITTER) * rng.uniform(*THICKNESS_JITTER)
        obj = ut.instance_linked(
            source, ut.obj_name(PREFIX, SOURCE_INDEX + 1 + len(fibers)), coll,
            location=origin,
            scale=(rng.uniform(*LENGTH_JITTER), bend, bend))
        obj.rotation_mode = "QUATERNION"
        obj.rotation_quaternion = _fiber_orientation(rng)
        if parent is not None:
            ut.set_parent(obj, parent)
        fibers.append(obj)
        anchors.append(origin)

    return fibers


# ---------------------------------------------------------------------------
# Test collection
# ---------------------------------------------------------------------------


def build_test_matrix(collection=None, bounds=DEFAULT_BOUNDS_UM,
                      density: float = TEST_DENSITY, seed: int = 0,
                      clear: bool = True):
    """Build one matrix in ``TEST_ECM`` for inspection and validation."""
    coll = collection or ut.get_or_create_collection(
        TEST_COLLECTION, parent=ut.resolve_collection("10_DEBUG"))
    return create_ecm_region(bounds=bounds, density=density, seed=seed,
                            collection=coll, clear=clear)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _fibers_in(coll) -> list:
    return [o for o in coll.objects
            if o.name.startswith(PREFIX) and not o.get(PROP_IS_SOURCE, False)]


def _bounds_volume(bounds) -> float:
    lo, hi = Vector(bounds[0]), Vector(bounds[1])
    return float((hi - lo).x * (hi - lo).y * (hi - lo).z)


def validate_ecm(collection=None, bounds=DEFAULT_BOUNDS_UM,
                 seed: int = 0) -> dict:
    """Build and check an ECM region. Returns a report dict."""
    failures = []
    coll = collection or ut.resolve_collection(TEST_COLLECTION)
    lo, hi = Vector(bounds[0]), Vector(bounds[1])
    span = hi - lo
    span_x, span_y, span_z = span.x, span.y, span.z

    # --- density_at purity and seed sensitivity ---------------------------
    probes = [Vector((0.0, 0.0, 0.0)), Vector((37.0, -12.0, 88.0)),
              Vector((-140.0, 60.0, -95.0))]
    for probe in probes:
        first = density_at(probe, seed)
        second = density_at(probe, seed)
        if abs(first - second) > 1e-12:
            failures.append("density_at is not pure at {}: {} vs {}".format(
                tuple(probe), first, second))
        if not 0.0 <= first <= 1.0:
            failures.append("density_at({}) = {} outside [0, 1]".format(
                tuple(probe), first))
    if all(abs(density_at(p, seed) - density_at(p, seed + 1)) < 1e-6 for p in probes):
        failures.append("density_at ignores seed: every probe matches seed+1")

    # --- the field must actually vary -------------------------------------
    samples = []
    for ix in range(5):
        for iy in range(5):
            for iz in range(5):
                samples.append(density_at(Vector(
                    (lo.x + span_x * ix / 4.0,
                     lo.y + span_y * iy / 4.0,
                     lo.z + span_z * iz / 4.0)), seed))
    field_lo, field_hi = min(samples), max(samples)
    if field_hi - field_lo < 0.30:
        failures.append(
            "density field is nearly flat (range {:.3f}); no dense or open "
            "regions".format(field_hi - field_lo))

    # --- build the region under test -------------------------------------
    fibers = _fibers_in_after_build(coll, bounds, seed)

    expected = _expected_count(_bounds_volume(bounds), TEST_DENSITY)
    if not fibers:
        failures.append("region built zero fibres; expected about {}".format(expected))
    else:
        if len(fibers) < expected * 0.85 or len(fibers) > expected * 1.15:
            failures.append("built {} fibres, expected about {} (+/-15%)".format(
                len(fibers), expected))

    # --- one shared mesh datablock ----------------------------------------
    meshes = {o.data.name for o in fibers}
    if len(meshes) > 1:
        failures.append("fibres span {} mesh datablocks; expected 1 shared: {}".format(
            len(meshes), sorted(meshes)))

    # --- naming -----------------------------------------------------------
    for obj in fibers:
        if FORBIDDEN_NAMES & set(obj.name.split("_")):
            failures.append("{}: forbidden name token".format(obj.name))
        if obj.name in FORBIDDEN_NAMES:
            failures.append("{}: forbidden name".format(obj.name))

    # --- bounds -----------------------------------------------------------
    outside = [o.name for o in fibers
               if not (lo.x <= o.location.x <= hi.x
                       and lo.y <= o.location.y <= hi.y
                       and lo.z <= o.location.z <= hi.z)]
    if outside:
        failures.append("{} fibres placed outside bounds, e.g. {}".format(
            len(outside), outside[:3]))

    # --- orientation spread: not a bundle, not random soup ----------------
    # Read the quaternion, not matrix_world. matrix_world is depsgraph-evaluated
    # and is still identity here, so it reports every fibre as pointing along
    # world +X -- the same class of bug this project already paid for once, on
    # the vessel's Follow Path constraint.
    directions = [o.rotation_quaternion @ Vector((1.0, 0.0, 0.0)) for o in fibers]
    if directions:
        resultant = Vector((sum(d.x for d in directions), sum(d.y for d in directions),
                            sum(d.z for d in directions))) / len(directions)
        if resultant.length > 0.85:
            failures.append(
                "fibres are near-parallel (mean resultant {:.3f}); the network "
                "reads as a bundle".format(resultant.length))
        if len({(round(d.x, 4), round(d.y, 4), round(d.z, 4)) for d in directions}) \
                < max(3, len(directions) // 4):
            failures.append("too few distinct fibre directions; looks repetitive")

    # --- the field must actually drive placement --------------------------
    # Two checks, because neither alone says what it needs to.
    #
    # A mean-shift test is too weak on its own: the field spans only 0.26-0.67
    # across this region, so *no* algorithm moves the mean field at the fibres
    # far from the mean field overall. The threshold is therefore derived from
    # what the field makes achievable rather than picked as a round number --
    # sampling exactly proportional to the field would land at E[f^2]/E[f], and
    # the requirement is half of that gap.
    #
    # The companion check is the one that matches the brief. "Local dense
    # regions and open regions" is a statement about *variance*, so it is tested
    # as variance: an even scatter of this many fibres into cells of this size
    # would show a coefficient of variation near 1/sqrt(fibres per cell), and
    # anything at or below that is a uniform scatter wearing a density field.
    if len(fibers) >= 40:
        grid, cells = FIELD_GRID, FIELD_CELL_UM
        fields = [density_at(Vector((lo.x + span_x * (ix + 0.5) / grid,
                                     lo.y + span_y * (iy + 0.5) / grid,
                                     lo.z + span_z * (iz + 0.5) / grid)), seed)
                  for ix in range(grid) for iy in range(grid) for iz in range(grid)]
        field_mean = sum(fields) / len(fields)
        # The mean field an exactly-proportional scatter would produce.
        proportional = (sum(f * f for f in fields) / len(fields)) / field_mean
        required = field_mean + 0.5 * (proportional - field_mean)

        placed = [density_at(o.location, seed) for o in fibers]
        placed_mean = sum(placed) / len(placed)
        if placed_mean < required:
            failures.append(
                "fibre placement barely follows the density field: mean field at "
                "fibres {:.3f}, need >= {:.3f} (even scatter {:.3f}, proportional "
                "scatter {:.3f})".format(placed_mean, required, field_mean, proportional))

        nx = max(1, int(span_x // cells))
        ny = max(1, int(span_y // cells))
        nz = max(1, int(span_z // cells))
        occupancy = [0] * (nx * ny * nz)
        for obj in fibers:
            occupancy[(min(nx - 1, int((obj.location.x - lo.x) // cells)) * ny
                       + min(ny - 1, int((obj.location.y - lo.y) // cells))) * nz
                      + min(nz - 1, int((obj.location.z - lo.z) // cells))] += 1
        mean_occ = sum(occupancy) / len(occupancy)
        variance = sum((c - mean_occ) ** 2 for c in occupancy) / len(occupancy)
        cv = math.sqrt(variance) / mean_occ if mean_occ > 0 else 0.0
        even_cv = 1.0 / math.sqrt(mean_occ) if mean_occ > 0 else 0.0
        if cv <= even_cv:
            failures.append(
                "fibre count is evenly scattered across the region (cv={:.3f}, an "
                "even scatter of {:.1f} per cell gives {:.3f}); there are no dense "
                "or open regions".format(cv, mean_occ, even_cv))

    # --- material: present, and subtle -----------------------------------
    mat_names = {m.name for o in fibers for m in o.data.materials}
    if mat_names and mat_names != {MAT_COLLAGEN["name"]}:
        failures.append("fibres carry unexpected materials: {}".format(sorted(mat_names)))
    collagen = bpy.data.materials.get(MAT_COLLAGEN["name"])
    if collagen is None:
        failures.append("{} missing".format(MAT_COLLAGEN["name"]))
    else:
        alpha = collagen.node_tree.nodes["Principled BSDF"].inputs["Alpha"].default_value
        if alpha > MAX_FIBER_ALPHA:
            failures.append(
                "collagen alpha {:.2f} exceeds {}; the matrix will wash out the "
                "cells it exists to support".format(alpha, MAX_FIBER_ALPHA))

    # --- units ------------------------------------------------------------
    if abs(bpy.context.scene.unit_settings.scale_length - ut.SCENE_SCALE_LENGTH) > 1e-12:
        failures.append("scene scale_length is not 1e-6; micrometre convention broken")

    # --- density must be wired to the count, monotonically ----------------
    sweep = []
    for value in DENSITY_SWEEP:
        sweep.append(len(_fibers_in_after_build(coll, bounds, seed, density=value)))
    if not all(a < b for a, b in zip(sweep, sweep[1:])):
        failures.append("fibre count is not monotonic in density: {}".format(sweep))

    # --- zero density is empty, not a fallback ----------------------------
    zero = _fibers_in_after_build(coll, bounds, seed, density=0.0)
    if zero:
        failures.append("density=0.0 produced {} fibres; expected none".format(len(zero)))

    # --- idempotency: rebuild twice, nothing accumulates ------------------
    _fibers_in_after_build(coll, bounds, seed)
    first_count = len(_fibers_in(coll))
    _fibers_in_after_build(coll, bounds, seed)
    second_count = len(_fibers_in(coll))
    if first_count != second_count:
        failures.append("rebuild is not idempotent: {} fibres then {}".format(
            first_count, second_count))
    suffix_drift = [o.name for o in coll.objects if ".0" in o.name]
    if suffix_drift:
        failures.append("rebuild left numbered duplicates: {}".format(suffix_drift[:3]))

    # leave a populated region behind for inspection
    final = _fibers_in_after_build(coll, bounds, seed)

    return {
        "ok": not failures,
        "failures": failures,
        "checks": {
            "fibers": len(final),
            "meshes": len(meshes),
            "materials": sorted(mat_names),
            "field_range": [round(field_lo, 3), round(field_hi, 3)],
            "density_sweep": dict(zip([str(d) for d in DENSITY_SWEEP], sweep)),
        },
    }


def _fibers_in_after_build(coll, bounds, seed, density: float = TEST_DENSITY) -> list:
    create_ecm_region(bounds=bounds, density=density, seed=seed, collection=coll)
    return _fibers_in(coll)


def main() -> None:
    """Generate the test collection, validate it, and report.

    Establishes the micrometre unit convention first: this asset's dimensions
    are only meaningful under it, and the validator asserts it.
    """
    ut.setup_units()
    ut.ensure_project_collections()
    build_test_matrix()
    report = validate_ecm()

    print("[ecm] built {} fibres in {}".format(
        report["checks"]["fibers"], TEST_COLLECTION))
    print("[ecm] checks: {}".format(report["checks"]))
    if report["ok"]:
        print("[ecm] VALIDATION PASSED")
    else:
        for failure in report["failures"]:
            print("[ecm] FAIL: {}".format(failure))
        raise SystemExit("[ecm] VALIDATION FAILED ({} problems)".format(
            len(report["failures"])))


if __name__ == "__main__":
    main()
