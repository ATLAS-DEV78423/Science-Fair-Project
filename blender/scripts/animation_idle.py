"""Ambient idle motion for the cell layer: membrane, nucleus, immune drift.

A render at frame 1 and a render at frame 120 being the same image is what
makes a scene read as *frozen* even when nothing in it is wrong. This module
puts a small, continuous, looping motion under every cell so the frame is
always alive, and keeps it small enough that it never competes with the six
narrative beats in the other ``animation_*.py`` modules. It is a background
layer, not a beat: it owns its own keyframes, imports no beat, and no beat
imports it.

The scientific constraint this module exists to enforce
------------------------------------------------------
**The animation must not imply that cancer cells move faster because they are
cancerous.** A science-fair audience that walks away believing "cancer =
faster" has been taught something false; real tumour cells are not faster than
healthy ones. What distinguishes them is *irregular membrane* -- higher spatial
frequency, lumpier, not quicker.

So the difference between the two classes is expressed as **morphology, never
as rate**:

=============  ======  ======  ==========  ======  ==========
class          membrane nucleus  period pool  drifts  noise scale
=============  ======  ======  ==========  ======  ==========
``healthy``    0.06    0.12    240..60     no      0.35
``tumor``      0.05    0.14    240..60     no      0.20 (finer)
``immune``     0.07    0.10    120..40     yes     0.35
=============  ======  ======  ==========  ======  ==========

Tumour amplitude is *lower* and its noise scale *finer*: smaller, more numerous
wrinkles, not a faster wobble. The two classes draw from the same period pool
so the rate comparison is not handed an excuse by differing period ranges.

That is enforced by measurement rather than by convention.
:func:`validate_idle` samples the evaluated F-curves, derives the peak
per-frame travel of the membrane surface for each class, and fails the build if
tumour ever exceeds healthy. A constant comparison would only prove the table
was edited consistently; measuring the curves also catches a regression that
arrives through the period pool or the phase offsets. The morphology half of
the claim -- the noise scale -- is asserted directly, because a spatial
frequency is a shape parameter and a rate measurement cannot see it.

Looping
-------
:data:`LOOP_FRAMES` (240) is the scene's frame range. A cell whose period is
``P`` gets its keys laid out over ``[start, start + P]`` with the value at
``start + P`` equal to the value at ``start`` -- one full cycle, ``P + 1`` keys
per channel rather than 241. A ``CYCLES`` F-curve modifier in ``REPEAT`` mode
before and after then extends that block over the rest of the range.

The subtlety is that **periods must divide 240 exactly** or the seam shows. A
137-frame cell arrives at frame 241 in a different pose from frame 1 and the
loop visibly jumps, so periods are drawn from :data:`PERIOD_DIVISORS` only, and
:func:`cell_period` asserts the division rather than trusting the table.
Interpolation is ``BEZIER`` with ``AUTO_CLAMPED`` handles: smooth at the seam,
and unable to overshoot the keyed values, which matters because an overshooting
membrane curve would push the surface outside its authored bounds.

Per-cell variation
------------------
A population that moves in lockstep reads as clones, so period, phase, drift
axis and drift sign are all seeded from the cell's ordinal -- a pure function
of identity, which is what lets the validator reproduce and check the motion.

Performance
-----------
No drivers, no per-frame handlers, no emission animation: keys only, so
viewport scrubbing and headless render are unaffected. The membrane channel is
a single Displace ``strength`` value, evaluated on the GPU, and every membrane
in a class shares one Clouds texture, so cost does not scale with cell count.
:func:`validate_idle` reports the keyframe total so growth stays visible.

Pause and reduced motion
------------------------
One multiplier, :data:`IDLE_MOTION_SCALE`, applied to every amplitude at key
time: ``1.0`` full, ``0.3`` reduced, ``0.0`` paused. At ``0.0`` the curves are
written flat rather than deleted, so the timeline structure is identical and
re-enabling is the one call in :func:`set_idle_motion` -- pause and resume
cannot desynchronise the beat layer that shares the same frame range.

Run standalone::

    blender --background --python blender/scripts/animation_idle.py
"""

from __future__ import annotations

import math
import os
import random
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utilities as ut  # noqa: E402
import asset_healthy_cell  # noqa: E402
import asset_immune_cells  # noqa: E402
import asset_tumor_cell  # noqa: E402

# ---------------------------------------------------------------------------
# Contract constants
# ---------------------------------------------------------------------------

#: Amplitude multiplier applied to every amplitude at key time. 1.0 full,
#: 0.3 reduced motion, 0.0 paused. One knob for both, per the design.
IDLE_MOTION_SCALE = 1.0

#: The scene's frame range, and therefore the length of the loop this module
#: has to close. 240 frames = 10 s at 24 fps.
LOOP_FRAMES = 240

#: Every period a cell may be given: the exact divisors of :data:`LOOP_FRAMES`
#: that are long enough to read as idle rather than as a twitch. Constraining
#: the set is what makes "loops seamlessly" true for every cell rather than
#: true on average.
PERIOD_DIVISORS = (240, 120, 80, 60, 48, 40)

#: The deformation modifier every asset module attaches to every membrane.
#: Named here because this module animates it; the asset modules own it.
MEMBRANE_MODIFIER = "MOD_MembraneDeform"
MEMBRANE_STRENGTH_PATH = 'modifiers["{}"].strength'.format(MEMBRANE_MODIFIER)

#: Radius of the closed drift path an immune cell's root walks, in micrometres,
#: before :data:`IDLE_MOTION_SCALE`. Immune cells genuinely patrol, and this is
#: the only channel in the project that translates a cell at all -- one class
#: moves, so there is no cross-class translation rate to misread as "cancer is
#: faster". Small against a 4-13 um cell: a drift, not locomotion.
DRIFT_AMPLITUDE_UM = 0.8

#: Aspect of the closed drift/offset path, major to minor. A lazy ellipse rather
#: than a circle, so the loop has a direction to read.
PATH_ASPECT = 0.6

#: Per-class idle behaviour. Beyond the six values that carry the science
#: (prefix, amplitudes, period pool, drifts, noise scale) each entry also names
#: the collection to search, the shared texture whose noise scale it owns, and
#: the modifier strength the membrane oscillates around -- read from the asset
#: module rather than restated here, so a change to the asset's authored
#: deformation is picked up rather than silently forked.
IDLE_SPECS = {
    "healthy": dict(
        prefix=(asset_healthy_cell.PREFIX,),
        collection=asset_healthy_cell.COLLECTION,
        texture=asset_healthy_cell.TEX_DISPLACE_NAME,
        deform_strength=asset_healthy_cell.MEMBRANE_DEFORM_STRENGTH,
        membrane_amplitude=0.06,
        nucleus_amplitude=0.12,
        period_pool=(240, 120, 80, 60),
        drifts=False,
        noise_scale=0.35,
    ),
    "tumor": dict(
        prefix=(asset_tumor_cell.PREFIX,),
        collection=asset_tumor_cell.COLLECTION,
        texture=asset_tumor_cell.TEX_DISPLACE_NAME,
        deform_strength=asset_tumor_cell.MEMBRANE_DEFORM_STRENGTH,
        # Lower amplitude and a finer noise scale: smaller, more numerous
        # wrinkles. Lumpy, not quick. See the module docstring.
        membrane_amplitude=0.05,
        nucleus_amplitude=0.14,
        # The same pool as healthy, so the rate comparison in validate_idle is
        # not handed an excuse by differing period ranges.
        period_pool=(240, 120, 80, 60),
        drifts=False,
        noise_scale=0.20,
    ),
    "immune": dict(
        prefix=(asset_immune_cells.PREFIX_TCELL, asset_immune_cells.PREFIX_NK,
                asset_immune_cells.PREFIX_DENDRITIC, asset_immune_cells.PREFIX_MACROPHAGE),
        collection=asset_immune_cells.COLLECTION,
        texture=asset_immune_cells.TEX_DISPLACE_NAME,
        deform_strength=asset_immune_cells.MEMBRANE_DEFORM_STRENGTH,
        membrane_amplitude=0.07,
        nucleus_amplitude=0.10,
        # Immune cells are the only ones that translate, so they are also the
        # only ones allowed a shorter period -- their drift has to be legible.
        period_pool=(120, 80, 60, 48, 40),
        drifts=True,
        noise_scale=0.35,
    ),
}

# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _layered_parts(root):
    """The membrane and nucleus under *root*, found without parsing a name.

    The membrane is the child carrying the deformation modifier that every
    asset module already attaches. Of the remaining children the nucleus is the
    smallest: membrane > cytoplasm > nucleus is an invariant all three modules
    build to, and two of them already assert it. Which child belongs to which
    cell is carried by parenting, never by a substring of its name.
    """
    children = [child for child in root.children if child.type == "MESH"]
    membranes = [c for c in children if c.modifiers.get(MEMBRANE_MODIFIER)]
    if not membranes:
        return None, None
    membrane = membranes[0]
    rest = [c for c in children if c is not membrane]
    return membrane, (min(rest, key=lambda c: max(c.dimensions)) if rest else None)


def _discover(cls, collection=None) -> list:
    """Every cell of one class, as ``(index, root, membrane, nucleus)``.

    The ordinal is read off the root's name because that *is* the project's
    identity key, and a cell's idle motion has to be a pure function of it.
    """
    spec = IDLE_SPECS[cls]
    coll = collection or ut.resolve_collection(spec["collection"])
    cells = []
    for root in sorted(coll.objects, key=lambda obj: obj.name):
        if root.type != "EMPTY" or not root.name.startswith(spec["prefix"]):
            continue
        try:
            index = int(root.name.rsplit("_", 1)[1])
        except (IndexError, ValueError):
            continue
        membrane, nucleus = _layered_parts(root)
        if membrane is None or nucleus is None:
            continue
        cells.append((index, root, membrane, nucleus))
    return cells


def _discover_all(collection=None) -> list:
    """Every cell in the scene, across all three classes."""
    return [(cls,) + cell for cls in IDLE_SPECS for cell in _discover(cls, collection)]


# ---------------------------------------------------------------------------
# Per-cell signal
# ---------------------------------------------------------------------------


def _period_and_phase(index: int, cls: str):
    """A cell's period and phase offset, both pure functions of its identity.

    The phase is a frame offset into the cycle, so two cells on the same period
    sit at different points in it instead of pulsing together.
    """
    pool = IDLE_SPECS[cls]["period_pool"]
    rng = random.Random(int(index))
    period = pool[rng.randrange(len(pool))]
    # Asserted rather than assumed: a period that does not divide the loop is
    # the one error here that looks fine in a still and obvious in playback.
    assert LOOP_FRAMES % period == 0, (
        "idle period {} does not divide LOOP_FRAMES {}; the loop jumps at the seam".format(
            period, LOOP_FRAMES))
    return period, rng.randrange(period)


def cell_period(index: int, cls: str) -> int:
    """The loop period in frames for cell *index* of class *cls*.

    Always an exact divisor of :data:`LOOP_FRAMES`, asserted on the way out.
    """
    return _period_and_phase(index, cls)[0]


def _path_plane(rng) -> tuple:
    """Two orthonormal axes for a cell's drift path, plus a random direction.

    Seeded from the cell's identity, so its drift is reproducible and different
    from every other cell's.
    """
    primary = Vector([rng.uniform(-1.0, 1.0) for _ in range(3)])
    if primary.length < 1e-6:
        primary = Vector((1.0, 0.0, 0.0))
    primary.normalize()
    secondary = primary.cross(Vector((0.0, 0.0, 1.0)))
    if secondary.length < 1e-6:
        secondary = primary.cross(Vector((0.0, 1.0, 0.0)))
    secondary.normalize()
    if rng.random() < 0.5:
        primary = -primary
    return primary, secondary


def _closed_path(origin: Vector, axis_a, axis_b, amplitude: float,
                 start: int, period: int) -> list:
    """Five ``(frame, Vector)`` samples of a closed path around *origin*.

    The last sample lands exactly on the first, one full period later, which is
    what makes the curve loop without a jump. *amplitude* is the path's
    semi-major axis, so it is the cell's peak displacement from its rest pose.
    """
    samples = []
    for step in range(5):
        angle = math.tau * step / 4.0
        offset = (axis_a * (amplitude * math.cos(angle))
                  + axis_b * (amplitude * PATH_ASPECT * math.sin(angle)))
        samples.append((start + period * step // 4, origin + offset))
    return samples


# ---------------------------------------------------------------------------
# Keyframe writing
# ---------------------------------------------------------------------------


def _fcurves(obj) -> list:
    """The F-curves on *obj*, read through the slotted-action API.

    ``Action.fcurves`` is gone in Blender 5, so this walks the action's layers
    down to the channelbag for the object's own slot. An object with no
    animation yet, or none of this module's, yields an empty list.
    """
    data = obj.animation_data
    if data is None or data.action is None or data.action_slot is None:
        return []
    found = []
    for layer in data.action.layers:
        for strip in layer.strips:
            if strip.type != "KEYFRAME":
                continue
            bag = strip.channelbag(data.action_slot)
            if bag is not None:
                found.extend(bag.fcurves)
    return found


def _finish(obj, data_path: str, indices: tuple) -> list:
    """Clamp and cycle the F-curves just written to *obj* at *data_path*.

    ``AUTO_CLAMPED`` handles make the seam smooth and make overshoot
    impossible, which matters because an overshooting membrane curve would push
    the surface outside its authored bounds. The ``CYCLES`` repeat is what
    carries a ``P``-frame block over the rest of the loop without writing
    241 keys.
    """
    written = [curve for curve in _fcurves(obj)
               if curve.data_path == data_path and curve.array_index in indices]
    for curve in written:
        for point in curve.keyframe_points:
            point.interpolation = "BEZIER"
            point.handle_left_type = "AUTO_CLAMPED"
            point.handle_right_type = "AUTO_CLAMPED"
        curve.update()
        cycles = curve.modifiers.new("CYCLES")
        cycles.mode_before = "REPEAT"
        cycles.mode_after = "REPEAT"
    return written


def _key(obj, data_path: str, samples: list, apply, indices: tuple) -> list:
    """Key *samples* -- ``(frame, value)`` pairs -- onto *obj* at *data_path*.

    *apply* writes one value onto the property. It is a callback because the two
    channels this module owns are reached differently: a Displace strength is a
    scalar on a nested struct, a location is a whole vector.
    """
    for frame, value in sorted(samples, key=lambda pair: pair[0]):
        apply(value)
        obj.keyframe_insert(data_path=data_path, frame=frame)
    return _finish(obj, data_path, indices)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def animate_idle(healthy_ctrl=None, tumor_ctrl=None, immune_ctrl=None,
                 motion_scale: float = IDLE_MOTION_SCALE,
                 frame_start: int = 1, frame_end: int = LOOP_FRAMES,
                 collection=None) -> dict:
    """Key ambient idle motion onto every cell in the scene. Idempotent.

    Membrane strength, nucleus location, and for immune cells the root's drift.
    Keys only -- no drivers, no handlers -- so scrubbing and headless render
    stay fast.

    Args:
        healthy_ctrl, tumor_ctrl, immune_ctrl: Beat controllers. Accepted for
            call-site parity with the other ``animation_*.py`` modules and
            deliberately unused: the idle layer animates cells where they
            already are and must not depend on a controller existing, because
            it runs underneath all six beats whether or not they are staged.
        motion_scale: Amplitude multiplier. ``0.0`` writes flat curves rather
            than deleting them, so a pause cannot desynchronise the beats.
        frame_start: Frame the first key of a cycle sits on.
        frame_end: Last frame of the range the report is measured over.
        collection: Search here instead of each class's own collection. For
            :func:`validate_idle`, which animates a population of its own.

    Returns:
        A report dict: per-class cell and curve counts, the periods and phases
        drawn, the noise scale owned per class, and the measured peak travel
        per channel per class. :func:`validate_idle` reads the peaks.
    """
    classes = {}
    moved = []
    curves_written = keys_written = 0

    for cls, spec in IDLE_SPECS.items():
        # One texture per class, so the noise scale is set once rather than per
        # cell. This module owns it rather than trusting the asset module's
        # default: every _add_membrane_deformation call reassigns it, so a
        # rebuild anywhere in the project reverts the class's morphology unless
        # the idle layer re-applies it. See validate_idle's hazard probe.
        texture = bpy.data.textures.get(spec["texture"])
        if texture is not None:
            texture.noise_scale = spec["noise_scale"]

        periods, phases, class_curves, class_keys = [], [], 0, 0
        for index, root, membrane, nucleus in _discover(cls, collection):
            # Idempotent by construction: the project has been bitten by
            # stacked F-curves once, and this is the documented cure.
            ut.clear_animation(membrane)
            ut.clear_animation(nucleus)

            period, phase = _period_and_phase(index, cls)
            start = frame_start + phase
            periods.append(period)
            phases.append(phase)
            base = spec["deform_strength"]
            rng = random.Random("{}:{}".format(cls, index))

            written = _key(
                membrane, MEMBRANE_STRENGTH_PATH,
                [(start, base),
                 (start + period // 2,
                  base + spec["membrane_amplitude"] * motion_scale),
                 (start + period, base)],
                lambda value: setattr(membrane.modifiers[MEMBRANE_MODIFIER],
                                      "strength", value),
                (0,),
            )
            moved.append((cls, "surface", written))
            class_curves += len(written)
            class_keys += sum(len(curve.keyframe_points) for curve in written)

            # The nucleus drifts about its authored off-centre rest pose, not
            # about the cell centre -- an unkeyed nucleus would jump to centre
            # the moment the keys were written.
            written = _key(
                nucleus, "location",
                _closed_path(nucleus.location.copy(), *_path_plane(rng),
                             spec["nucleus_amplitude"] * motion_scale,
                             start, period),
                lambda value: setattr(nucleus, "location", value),
                (0, 1, 2),
            )
            moved.append((cls, "nucleus", written))
            class_curves += len(written)
            class_keys += sum(len(curve.keyframe_points) for curve in written)

            if spec["drifts"]:
                ut.clear_animation(root)
                written = _key(
                    root, "location",
                    _closed_path(root.location.copy(), *_path_plane(rng),
                                 DRIFT_AMPLITUDE_UM * motion_scale, start, period),
                    lambda value: setattr(root, "location", value),
                    (0, 1, 2),
                )
                moved.append((cls, "drift", written))
                class_curves += len(written)
                class_keys += sum(len(curve.keyframe_points) for curve in written)

        curves_written += class_curves
        keys_written += class_keys
        classes[cls] = {
            "cells": len(periods),
            "fcurves": class_curves,
            "keyframes": class_keys,
            "periods": periods,
            "phases": phases,
        }

    peaks = {cls: {"surface": 0.0, "nucleus": 0.0, "drift": 0.0} for cls in IDLE_SPECS}
    for cls, channel, curves in moved:
        peaks[cls][channel] = max(peaks[cls][channel],
                                  _peak_travel(curves, frame_start, frame_end))

    return {
        "classes": classes,
        "fcurves": curves_written,
        "keyframes": keys_written,
        "motion_scale": motion_scale,
        "frame_range": [frame_start, frame_end],
        "noise_scale": {cls: spec["noise_scale"] for cls, spec in IDLE_SPECS.items()},
        "peak_speed": {cls: peaks[cls]["surface"] for cls in IDLE_SPECS},
        "peak_speed_by_channel": peaks,
    }


def set_idle_motion(scale: float, collection=None) -> float:
    """Set the global amplitude multiplier and re-key. Returns the new scale.

    The accessibility control. Re-keys rather than rescales the existing curves
    so one number is the only thing that decides how loud the idle layer is.
    """
    global IDLE_MOTION_SCALE
    IDLE_MOTION_SCALE = float(scale)
    animate_idle(motion_scale=IDLE_MOTION_SCALE, collection=collection)
    return IDLE_MOTION_SCALE


def clear_idle(collection=None) -> int:
    """Remove every idle keyframe. Returns the number of objects cleared.

    Pause does not need this -- :func:`set_idle_motion` with ``0.0`` writes flat
    curves and keeps the timeline structure. This is the blunt instrument, for
    handing the scene back to a state with no idle layer at all.
    """
    cleared = 0
    for cls, _index, root, membrane, nucleus in _discover_all(collection):
        targets = (membrane, nucleus, root) if IDLE_SPECS[cls]["drifts"] \
            else (membrane, nucleus)
        for obj in targets:
            ut.clear_animation(obj)
            cleared += 1
    return cleared


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


def _peak_travel(curves: list, frame_start: int, frame_end: int) -> float:
    """Largest travel in any single frame across *curves*, in micrometres.

    Sampled off the evaluated F-curves -- CYCLES repeat included -- rather than
    off the amplitude constants, so a regression arriving through the period
    pool or the phase offsets is measured rather than missed. A one-F-curve
    channel reduces to the absolute difference; several, as a location's three
    axes, are combined as a vector, because a three-axis drift is one movement
    and no viewer would ever see three separate ones.
    """
    peak = 0.0
    previous = [curve.evaluate(frame_start) for curve in curves]
    for frame in range(frame_start + 1, frame_end + 2):
        current = [curve.evaluate(frame) for curve in curves]
        peak = max(peak, math.sqrt(sum((now - was) ** 2
                                       for now, was in zip(current, previous))))
        previous = current
    return peak


# ---------------------------------------------------------------------------
# Test collection
# ---------------------------------------------------------------------------

#: Scratch collection, nested under 10_DEBUG so "never ship the debug tree" is
#: structural rather than something someone has to remember at export time.
TEST_COLLECTION = "TEST_IdleCells"

#: Population sizes. Four of each tissue class so the rate comparison is made
#: across several periods rather than one, and two immune cells of deliberately
#: different radii, which is the hardest case for finding a nucleus by size.
TEST_COUNTS = (4, 4, 2)

#: A noise scale no class uses, written before the hazard probe builds a cell.
#: Two of the three classes declare the same value as their asset module's own
#: default, so a plain before/after comparison would read "nothing changed"
#: for them and the probe would pass vacuously.
NOISE_SCALE_SENTINEL = 0.123


def build_test_cells(collection=None) -> list:
    """Build the validation population into :data:`TEST_COLLECTION`.

    The scene ships no cells of its own, so the validator builds its own. Four
    healthy, four tumour, two immune, spread far enough apart not to intersect.
    Returns the per-cell dicts the asset modules hand back.
    """
    coll = collection or ut.get_or_create_collection(
        TEST_COLLECTION, ut.resolve_collection("10_DEBUG"))
    for spec in IDLE_SPECS.values():
        for prefix in spec["prefix"]:
            ut.clear_collection(coll, prefix=prefix)

    healthy_count, tumor_count, immune_count = TEST_COUNTS
    built = []
    for i in range(healthy_count):
        built.append(asset_healthy_cell.create_healthy_cell(
            location=((i - (healthy_count - 1) / 2.0) * 30.0, 22.0, 0.0),
            seed=1000 + i, index=i + 1, collection=coll))
    for i in range(tumor_count):
        built.append(asset_tumor_cell.create_tumor_cell(
            location=((i - (tumor_count - 1) / 2.0) * 30.0, -22.0, 0.0),
            morphology_seed=2000 + i, index=i + 1, collection=coll))
    for i, factory in enumerate(
            (asset_immune_cells.create_tcell, asset_immune_cells.create_macrophage),
            start=1):
        built.append(factory(
            location=((i - (immune_count + 1) / 2.0) * 40.0, 62.0, 0.0),
            seed=3000 + i, index=i, collection=coll))
    return built


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _idle_fcurves(collection=None) -> list:
    """Every F-curve this module owns across the scene, paired with its object."""
    out = []
    for cls, _index, root, membrane, nucleus in _discover_all(collection):
        for obj in (membrane, nucleus, root) if IDLE_SPECS[cls]["drifts"] \
                else (membrane, nucleus):
            out.extend((obj, curve) for curve in _fcurves(obj))
    return out


def _object_count() -> int:
    return len(bpy.data.objects)


def validate_idle(collection=None) -> dict:
    """Build a test population, key the idle layer onto it, and check it.

    Every check is a measurement of the built result, not a restatement of how
    :func:`animate_idle` was written. Returns ``{"ok", "failures", "checks"}``.
    """
    coll = collection or ut.get_or_create_collection(
        TEST_COLLECTION, ut.resolve_collection("10_DEBUG"))
    build_test_cells(coll)
    failures = []

    # --- it adds keys, not objects ---------------------------------------
    # Check 7 of the design: this is an animation feature and must not quietly
    # become a geometry feature.
    objects_before = _object_count()
    report = animate_idle(collection=coll)
    if _object_count() != objects_before:
        failures.append("animating changed the object count: {} then {}".format(
            objects_before, _object_count()))

    classes = report["classes"]
    checks = {
        "cells": sum(entry["cells"] for entry in classes.values()),
        "fcurves": report["fcurves"],
        "keyframes": report["keyframes"],
        "periods": sorted({p for e in classes.values() for p in e["periods"]}),
        "healthy_peak_speed": report["peak_speed"]["healthy"],
        "tumor_peak_speed": report["peak_speed"]["tumor"],
        "immune_peak_speed": report["peak_speed"]["immune"],
        "noise_scale": report["noise_scale"],
    }
    peaks = report["peak_speed_by_channel"]

    for cls in IDLE_SPECS:
        if classes[cls]["cells"] == 0:
            failures.append("{}: no cells found; nothing was animated".format(cls))

    # --- 1. the loop is seamless -----------------------------------------
    # Sampled off the evaluated curves, so a period that does not divide the
    # loop is caught here rather than showing up as a visible jump.
    curves = _idle_fcurves(coll)
    if not curves:
        failures.append("no idle F-curves were written")
    for obj, curve in curves:
        first = curve.evaluate(1)
        seam = curve.evaluate(LOOP_FRAMES + 1)
        if abs(first - seam) > 1e-4:
            failures.append(
                "{}: {} does not close the loop ({} at frame 1, {} at frame {})".format(
                    obj.name, curve.data_path, first, seam, LOOP_FRAMES + 1))
            break

    # --- 2. tumour is not faster than healthy ----------------------------
    # The governing constraint, measured per class off the evaluated membrane
    # curves. Peak *surface* travel is the quantity the claim is about: what an
    # audience reads as how fast a cell moves is the silhouette. The nucleus is
    # an interior channel and is reported alongside, not substituted for it --
    # the brief gives tumour a deliberately larger nucleus amplitude, so a
    # guard on the interior would contradict the brief's own numbers rather
    # than enforce the science.
    healthy_peak = peaks["healthy"]["surface"]
    tumor_peak = peaks["tumor"]["surface"]
    if tumor_peak > healthy_peak:
        failures.append(
            "tumour membrane peak speed {:.6f} um/frame exceeds healthy's {:.6f}; "
            "the animation would imply that cancer cells move faster, which is "
            "false -- express the difference as morphology, not rate".format(
                tumor_peak, healthy_peak))

    # --- 3. reduced motion is quieter, pause is flat ---------------------
    quiet = animate_idle(motion_scale=0.3, collection=coll)["peak_speed_by_channel"]
    for cls in IDLE_SPECS:
        for channel in ("surface", "nucleus", "drift"):
            if quiet[cls][channel] > peaks[cls][channel] + 1e-9:
                failures.append("{} {} at motion_scale=0.3 is louder than at 1.0: "
                                "{:.6f} vs {:.6f}".format(
                                    cls, channel, quiet[cls][channel],
                                    peaks[cls][channel]))
    if set_idle_motion(0.0, coll) != 0.0:
        failures.append("set_idle_motion(0.0) did not return the new scale")
    for obj, curve in _idle_fcurves(coll):
        if abs(curve.evaluate(1) - curve.evaluate(LOOP_FRAMES // 2)) > 1e-9:
            failures.append("{}: {} is not flat at motion_scale=0.0".format(
                obj.name, curve.data_path))
            break
    set_idle_motion(1.0, coll)
    report = animate_idle(collection=coll)
    peaks = report["peak_speed_by_channel"]
    checks["healthy_peak_speed"] = report["peak_speed"]["healthy"]
    checks["tumor_peak_speed"] = report["peak_speed"]["tumor"]
    checks["immune_peak_speed"] = report["peak_speed"]["immune"]

    # --- 4. periods divide the loop --------------------------------------
    for cls, entry in classes.items():
        for period in entry["periods"]:
            if LOOP_FRAMES % period != 0:
                failures.append("{}: period {} does not divide {}".format(
                    cls, period, LOOP_FRAMES))
            if period not in PERIOD_DIVISORS:
                failures.append("{}: period {} is not a declared divisor".format(
                    cls, period))
    for entry in IDLE_SPECS.values():
        for period in entry["period_pool"]:
            if period not in PERIOD_DIVISORS or LOOP_FRAMES % period:
                failures.append("period pool contains {}; it is not an exact "
                                "divisor of {}".format(period, LOOP_FRAMES))

    # --- 5. the population is not synchronised ---------------------------
    all_periods = {p for e in classes.values() for p in e["periods"]}
    all_phases = {p for e in classes.values() for p in e["phases"]}
    if len(all_periods) < 2:
        failures.append("every cell shares one period ({}); the population moves "
                        "in lockstep".format(sorted(all_periods)))
    if len(all_phases) < 2:
        failures.append("every cell shares one phase ({}); the population pulses "
                        "together".format(sorted(all_phases)))

    # --- 6. rebuild is idempotent ----------------------------------------
    # Snapshot, re-run, compare. Running the script twice is the actual
    # scenario, and ut.clear_animation inside animate_idle is what stops the
    # two runs stacking into one set of curves.
    before = [(obj.name, curve.data_path, curve.array_index, len(curve.keyframe_points))
              for obj, curve in _idle_fcurves(coll)]
    animate_idle(collection=coll)
    after = [(obj.name, curve.data_path, curve.array_index, len(curve.keyframe_points))
             for obj, curve in _idle_fcurves(coll)]
    if len(before) != len(after):
        failures.append("rebuild is not idempotent: {} F-curves then {}".format(
            len(before), len(after)))
    if sorted(before) != sorted(after):
        failures.append("rebuild changed the F-curve set, not just its length")

    # --- interpolation and looping are as specified ----------------------
    for obj, curve in _idle_fcurves(coll):
        if len(curve.modifiers) != 1 or curve.modifiers[0].type != "CYCLES":
            failures.append("{}: {} has {} modifiers, expected one CYCLES".format(
                obj.name, curve.data_path, len(curve.modifiers)))
        for point in curve.keyframe_points:
            if point.interpolation != "BEZIER" \
                    or point.handle_left_type != "AUTO_CLAMPED" \
                    or point.handle_right_type != "AUTO_CLAMPED":
                failures.append("{}: {} has a key that is not BEZIER/AUTO_CLAMPED; "
                                "a bezier overshoot pushes the surface outside its "
                                "authored bounds".format(obj.name, curve.data_path))
                break

    # --- 7. pausing keeps the timeline, resuming is one call -------------
    if clear_idle(coll) == 0:
        failures.append("clear_idle cleared nothing; the layer is not reachable")
    if _idle_fcurves(coll):
        failures.append("clear_idle left F-curves behind")
    animate_idle(collection=coll)
    if not _idle_fcurves(coll):
        failures.append("re-enabling after clear_idle did not restore the layer")

    # --- the noise scale this module owns --------------------------------
    # Asserted directly rather than inferred from a speed: a spatial frequency
    # is a shape parameter, and no rate measurement can see it. Between them
    # the two checks cover both halves of "morphology, never rate".
    #
    # 1e-6, not 1e-9: noise_scale is a float32 property, so 0.35 reads back as
    # 0.34999999 and an exact comparison would fail on its own arithmetic.
    for cls, spec in IDLE_SPECS.items():
        texture = bpy.data.textures.get(spec["texture"])
        measured = texture.noise_scale if texture is not None else None
        if measured is None:
            failures.append("{}: shared texture {} is missing".format(
                cls, spec["texture"]))
        elif abs(measured - spec["noise_scale"]) > 1e-6:
            failures.append("{}: noise_scale is {}, expected {}; a rebuild reverted "
                            "the class's morphology".format(
                                cls, measured, spec["noise_scale"]))
    if peaks["tumor"]["surface"] <= 0.0 or peaks["healthy"]["surface"] <= 0.0:
        failures.append("membrane surfaces did not move at all; the layer is inert")

    # --- the hazard, exercised rather than described ---------------------
    # Every asset module's _add_membrane_deformation reassigns noise_scale, so
    # building any cell takes the class's morphology back to the asset default.
    # Proved live with a sentinel, because for the two classes whose spec value
    # already equals the asset default a plain before/after comparison would
    # read as "no hazard" when the assignment did happen.
    for cls, spec in IDLE_SPECS.items():
        texture = bpy.data.textures[spec["texture"]]
        texture.noise_scale = NOISE_SCALE_SENTINEL
        if cls == "tumor":
            asset_tumor_cell.create_tumor_cell(
                location=(0.0, 0.0, 0.0), morphology_seed=99, index=99, collection=coll)
        elif cls == "healthy":
            asset_healthy_cell.create_healthy_cell(
                location=(0.0, 0.0, 0.0), seed=99, index=99, collection=coll)
        else:
            asset_immune_cells.create_tcell(
                location=(0.0, 0.0, 0.0), seed=99, index=99, collection=coll)
        if abs(texture.noise_scale - NOISE_SCALE_SENTINEL) < 1e-6:
            failures.append("{}: building a cell left noise_scale at the sentinel; the "
                            "asset module no longer reassigns it, so this probe is not "
                            "testing what it claims".format(cls))
        animate_idle(collection=coll)
        if abs(texture.noise_scale - spec["noise_scale"]) > 1e-6:
            failures.append("{}: animate_idle did not re-own noise_scale after a "
                            "rebuild ({}); any later build silently reverts the "
                            "class's morphology".format(cls, texture.noise_scale))

    # --- the micrometre convention ---------------------------------------
    if abs(bpy.context.scene.unit_settings.scale_length - ut.SCENE_SCALE_LENGTH) > 1e-12:
        failures.append("scene scale_length is not 1e-6; micrometre convention broken")

    # Leave a populated, animating population behind for inspection.
    build_test_cells(coll)
    final = animate_idle(collection=coll)

    return {
        "ok": not failures,
        "failures": failures,
        "checks": dict(checks, keyframes=final["keyframes"],
                       peak_speed_by_channel=peaks),
    }


def main() -> None:
    """Build the test population, validate the idle layer, and report.

    Establishes the micrometre unit convention first: every amplitude here is
    in micrometres and is meaningless without it.
    """
    ut.setup_units()
    ut.ensure_project_collections()
    report = validate_idle()

    print("[idle] checks: {}".format(report["checks"]))
    if report["ok"]:
        print("[idle] VALIDATION PASSED")
    else:
        for failure in report["failures"]:
            print("[idle] FAIL: {}".format(failure))
        raise SystemExit("[idle] VALIDATION FAILED ({} problems)".format(
            len(report["failures"])))


if __name__ == "__main__":
    main()
