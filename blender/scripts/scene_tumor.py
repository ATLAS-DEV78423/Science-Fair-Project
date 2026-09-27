"""Build the base scene: collections, controllers, cameras, lighting, render settings.

This is the project's entry point. Running it rebuilds the whole scaffold from
source, which is why the ``.blend`` never has to be hand-edited -- if the file
gets into a bad state, delete it and re-run.

Run from Blender's text editor, or headless:

    blender --background --python blender/scripts/scene_tumor.py

Or, to rebuild and save in one step:

    blender --background --python blender/scripts/scene_tumor.py -- --save

Safe to re-run: every operation is idempotent and scoped to its own collection.
"""

from __future__ import annotations

import os
import sys

import bpy

# Allow `import utilities` whether we were launched by Blender's --python,
# exec'd from the text editor, or imported by another script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utilities as ut  # noqa: E402

# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------
# Scene is modelled at 1 Blender unit == 1 micrometre with
# unit_settings.scale_length = 1e-6, so a ~20 um tumour cell is ~20 BU and a
# ~200 nm virion is ~0.2 BU. Blender attenuates light over raw BU, not scaled
# BU, so light power has to be scaled by (distance / reference)^2 to match.
# These numbers were calibrated against a cell-sized test sphere rendered from
# CAMERA_Master, and land it mid-tone rather than clipping to white. Re-tune
# them together if you move the lights.

KEY_LIGHT_WATTS = 3.5e5
FILL_LIGHT_WATTS = 9.0e4
RIM_LIGHT_WATTS = 2.2e5

#: name -> (focal_length_mm, location, aim_at)
CAMERAS = {
    "CAMERA_Master": (35.0, (0.0, -200.0, 60.0), (0.0, 0.0, 0.0)),
    "CAMERA_Macro": (50.0, (60.0, -120.0, 30.0), (0.0, 0.0, 0.0)),
    "CAMERA_Cell": (85.0, (25.0, -55.0, 12.0), (0.0, 0.0, 0.0)),
    "CAMERA_Virus": (100.0, (2.0, -14.0, 3.0), (0.0, 0.0, 0.0)),
}

#: Blender's default clip_start of 0.1 would clip a 0.2 BU virion entirely.
MICRON_CLIP_START = 0.01

#: name -> (type, watts, colour, size, location)
LIGHTS = {
    "LIGHT_Key": ("AREA", KEY_LIGHT_WATTS, (0.95, 0.97, 1.00), 60.0, (-80.0, -120.0, 90.0)),
    "LIGHT_Fill": ("AREA", FILL_LIGHT_WATTS, (0.55, 0.68, 0.85), 100.0, (120.0, -80.0, 20.0)),
    "LIGHT_Rim": ("AREA", RIM_LIGHT_WATTS, (0.65, 0.82, 0.92), 40.0, (30.0, 130.0, 60.0)),
}

#: Very dark desaturated blue-grey. Not pure black: pure black reads as an
#: empty void, this reads as depth, which is the microscopy look we want.
WORLD_NAME = "WORLD_Master"
WORLD_COLOR = (0.020, 0.025, 0.035, 1.0)
WORLD_STRENGTH = 1.0

RENDER_RESOLUTION = (1920, 1080)
FPS = 24
FRAME_RANGE = (1, 240)
SAMPLES_PREVIEW = 64
SAMPLES_FINAL = 256


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def build_collections():
    """Create the 11 project collections in canonical order."""
    return ut.ensure_project_collections()


def build_controllers():
    """Create CTRL_Master and the four domain controllers parented beneath it.

    Each domain controller lives inside the collection it controls, so the
    collection remains self-contained for export and duplication.
    """
    controllers = {}
    for name, coll_name in ut.CONTROLLERS.items():
        coll = ut.resolve_collection(coll_name)
        parent = None if name == ut.ROOT_CONTROLLER else controllers.get(ut.ROOT_CONTROLLER)
        controllers[name] = ut.new_empty(
            name, coll, location=(0.0, 0.0, 0.0), parent=parent, size=4.0
        )
    return controllers


def build_cameras(ctrl_camera):
    """Create the four unkeyed cameras, parented to CTRL_Camera."""
    coll = ut.resolve_collection("08_CAMERAS")
    made = []
    for name, (focal, location, aim) in CAMERAS.items():
        cam = ut.get_or_create_object(
            name, coll, lambda n: bpy.data.cameras.new(n),
            location=location, parent=ctrl_camera,
        )
        cam.data.lens = focal
        cam.data.clip_start = MICRON_CLIP_START
        ut.aim_at(cam, aim)
        made.append(cam)
    bpy.context.scene.camera = bpy.data.objects["CAMERA_Master"]
    return made


def build_lighting():
    """Three-point rig. Ambient comes from the World, not a lamp.

    A world node setup is the correct way to do global fill and exports far
    more cleanly than a giant shadow-casting area light would.
    """
    coll = ut.resolve_collection("09_LIGHTING")
    made = []
    for name, (kind, watts, colour, size, location) in LIGHTS.items():
        light = ut.get_or_create_object(
            name, coll, lambda n, k=kind: bpy.data.lights.new(n, type=k),
            location=location,
        )
        light.data.energy = watts
        light.data.color = colour
        if kind == "AREA":
            light.data.size = size
        ut.aim_at(light, (0.0, 0.0, 0.0))
        made.append(light)
    return made


def build_world(scene):
    """Ambient lighting as a dark world background.

    Note: no ``use_nodes = True`` here. Worlds get a node tree on creation in
    Blender 5.x, and that setter is deprecated for removal in 6.0.
    """
    world = bpy.data.worlds.get(WORLD_NAME) or bpy.data.worlds.new(WORLD_NAME)
    background = None
    for node in world.node_tree.nodes:
        if node.type == "BACKGROUND":
            background = node
            break
    if background is None:
        raise RuntimeError("build_world: no Background node in {!r}".format(WORLD_NAME))
    background.inputs["Color"].default_value = WORLD_COLOR
    background.inputs["Strength"].default_value = WORLD_STRENGTH
    scene.world = world
    return world


def set_engine(name: str = "cycles", scene=None):
    """Switch render engine.

    Cycles is the default: it is the physically based path this project is
    specified against. EEVEE is here for fast iteration and for matching what
    a WebGL viewer will roughly show.

        set_engine("cycles")   # final stills, turntables
        set_engine("eevee")    # fast previews

    Note: Blender's static RNA introspection does not list addon-registered
    engines, so the valid values are not discoverable that way in a script.
    """
    scene = scene or bpy.context.scene
    if name.lower() in {"cycles", "cy"}:
        scene.render.engine = "CYCLES"
    elif name.lower() in {"eevee", "eevee_next", "next"}:
        scene.render.engine = "BLENDER_EEVEE"
        eevee = scene.eevee
        eevee.use_raytracing = True
        eevee.use_shadows = True
        eevee.taa_render_samples = SAMPLES_FINAL
    else:
        raise ValueError("set_engine: unknown engine {!r}".format(name))
    return scene.render.engine


def setup_render_settings(scene=None, samples: int = SAMPLES_PREVIEW):
    """Cycles + AgX + OpenImageDenoise, tuned for a dark scientific look.

    AgX is load-bearing rather than cosmetic: it rolls off highlights instead
    of clipping them, which is what stops bright virus particles against the
    dark background from blowing out into neon.
    """
    scene = scene or bpy.context.scene
    set_engine("cycles", scene)

    scene.render.resolution_x, scene.render.resolution_y = RENDER_RESOLUTION
    scene.render.resolution_percentage = 100
    scene.render.fps = FPS
    scene.frame_start, scene.frame_end = FRAME_RANGE
    scene.render.film_transparent = False

    cycles = scene.cycles
    cycles.samples = samples
    cycles.use_adaptive_sampling = True
    cycles.adaptive_threshold = 0.01
    cycles.use_denoising = True
    cycles.denoiser = "OPENIMAGEDENOISE"
    cycles.max_bounces = 8
    cycles.diffuse_bounces = 4
    cycles.glossy_bounces = 4
    cycles.transmission_bounces = 8
    cycles.volume_bounces = 0  # no volumetrics yet; see README

    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "None"
    return scene


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build(reset: bool = True, samples: int = SAMPLES_PREVIEW) -> dict:
    """Build the whole scaffold. Idempotent; safe to re-run at any time.

    Args:
        reset: Remove the factory startup Cube/Light/Camera and the stray
            default ``Collection`` first. Only affects startup leftovers.
        samples: Cycles sample count.

    Returns:
        A summary dict, handy for headless runs and for the MCP tools.
    """
    scene = bpy.context.scene
    ut.setup_units(scene)

    removed = ut.remove_startup_objects() if reset else []

    collections = build_collections()
    controllers = build_controllers()
    cameras = build_cameras(controllers["CTRL_Camera"])
    lights = build_lighting()
    build_world(scene)
    setup_render_settings(scene, samples=samples)
    ut.purge_orphans()

    return {
        "removed_startup": removed,
        "collections": [c.name for c in collections],
        "controllers": list(controllers),
        "cameras": [c.name for c in cameras],
        "lights": [l.name for l in lights],
        "engine": scene.render.engine,
        "scene_camera": scene.camera.name if scene.camera else None,
        "unit_scale_length": scene.unit_settings.scale_length,
        "view_transform": scene.view_settings.view_transform,
    }


def selftest() -> dict:
    """Assert that :func:`build` is genuinely idempotent, then report.

    Guards a bug this project already hit once: ``bpy.data.objects.new()`` is
    not idempotent, so a re-run used to leave ``LIGHT_Key.001`` and
    ``CAMERA_Master.001`` behind, with the stale originals still lighting the
    scene. Changed settings silently failed to apply.

    Run: ``blender --background --python scene_tumor.py -- --selftest``
    """
    build()
    first = {o.name for o in bpy.data.objects}
    first_mats = {m.name for m in bpy.data.materials}

    build()
    second = {o.name for o in bpy.data.objects}

    duplicated = sorted(n for n in second if n[-4:-3] == "." and n[-3:].isdigit())
    orphans = sorted(first - second)

    assert not duplicated, "build is not idempotent, created: {}".format(duplicated)
    assert not orphans, "build deleted objects it had made: {}".format(orphans)
    assert first_mats == {m.name for m in bpy.data.materials}, "material set changed"

    return {
        "objects": len(second),
        "collections": len(bpy.data.collections),
        "duplicated": duplicated,
        "ok": True,
    }


def main() -> None:
    """Build, then honour ``--save`` / ``--selftest`` command-line flags."""
    if "--selftest" in sys.argv:
        print("[scene_tumor] selftest: {}".format(selftest()))
        return

    summary = build()
    print("[scene_tumor] built: {}".format(summary))

    if "--save" in sys.argv:
        blend_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scenes",
            "virotherapy_main.blend",
        )
        os.makedirs(os.path.dirname(blend_path), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=blend_path)
        print("[scene_tumor] saved: {}".format(blend_path))


if __name__ == "__main__":
    main()
