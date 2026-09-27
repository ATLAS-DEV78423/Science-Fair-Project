"""Shared helpers for the oncolytic virotherapy visualization.

Every asset and animation builder routes through this module so that naming,
collection scope and instancing behave identically across the project.

Scope rule -- enforced here, relied on everywhere:

    A builder may only create or modify objects inside the collection it
    targets. It never touches another collection, and never deletes anything
    without an explicit name-prefix match.

That rule is what makes a rebuild safe. Re-running ``scene_tumor.py`` or any
``asset_*.py`` can only ever affect its own subtree of the scene graph.

Unit convention
---------------
1 Blender unit == 1 micrometre. The scene sets ``unit_settings.scale_length``
to 1e-6 so Blender's UI reports real biological sizes: a ~20 um tumour cell
is ~20 BU, a ~200 nm virion is ~0.2 BU. See ``SCENE_SCALE_LENGTH``.

Note for the web pipeline: glTF is defined in metres. When exporting, apply a
1e-6 scale to the root node so a 20 BU cell lands at 2e-5 m in three.js.
"""

from __future__ import annotations

import math

import bpy
from mathutils import Vector

# ---------------------------------------------------------------------------
# Project constants
# ---------------------------------------------------------------------------

#: Canonical collection order. Every one of these is created by the builder.
COLLECTION_NAMES = (
    "00_MASTER",
    "01_TUMOR",
    "02_HEALTHY_TISSUE",
    "03_VIRUSES",
    "04_IMMUNE_SYSTEM",
    "05_BLOOD_VESSELS",
    "06_EXTRACELLULAR_MATRIX",
    "07_EFFECTS",
    "08_CAMERAS",
    "09_LIGHTING",
    "10_DEBUG",
)

#: Metres per Blender unit. 1 BU == 1 um.
SCENE_SCALE_LENGTH = 1e-6

#: Controller objects, mapped to the collection that owns them.
#:
#: A domain controller lives *inside* the collection it controls rather than in
#: 00_MASTER, so the collection stays self-contained: duplicating or exporting
#: 03_VIRUSES brings CTRL_Virus with it. 00_MASTER keeps only the root.
CONTROLLERS = {
    "CTRL_Master": "00_MASTER",
    "CTRL_Tumor": "01_TUMOR",
    "CTRL_Virus": "03_VIRUSES",
    "CTRL_Immune": "04_IMMUNE_SYSTEM",
    "CTRL_Camera": "08_CAMERAS",
}

#: ROOT_CONTROLLER is the parent of every other controller.
ROOT_CONTROLLER = "CTRL_Master"

#: The three objects Blender creates in a factory startup file. Removed on a
#: full rebuild so they do not linger as Sphere.001 / Cube.032 style leftovers.
STARTUP_OBJECTS = ("Cube", "Light", "Camera")


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


def obj_name(prefix: str, index: int) -> str:
    """Return the project's zero-padded object name, e.g. ``CELL_TUMOR_001``.

    Args:
        prefix: Semantic prefix such as ``CELL_TUMOR`` or ``VIRUS_HSV1``.
        index: 1-based ordinal. Values below 1 are clamped to 1.
    """
    return "{}_{:03d}".format(prefix, max(1, int(index)))


def collection_for(name: str):
    """Return the collection called *name*, or None if it does not exist."""
    return bpy.data.collections.get(name)


def resolve_collection(name: str):
    """Return the collection called *name*, creating it under the scene if needed."""
    return get_or_create_collection(name)


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------


def get_or_create_collection(name: str, parent=None):
    """Idempotently fetch or create a collection linked under *parent*.

    Re-running a builder never duplicates a collection, which is what makes
    the whole project safe to regenerate at any time.

    Args:
        name: Collection name, e.g. ``03_VIRUSES``.
        parent: Parent collection. Defaults to the scene's master collection.

    Returns:
        The collection datablock.
    """
    existing = bpy.data.collections.get(name)
    if existing is not None:
        return existing

    coll = bpy.data.collections.new(name)
    if parent is None:
        parent = bpy.context.scene.collection
    parent.children.link(coll)
    return coll


def ensure_project_collections(parent=None):
    """Create every collection in :data:`COLLECTION_NAMES` in canonical order."""
    return [get_or_create_collection(n, parent) for n in COLLECTION_NAMES]


def link_object(obj, collection) -> None:
    """Link *obj* into *collection* only, never into another collection.

    A freshly created object may already be linked (e.g. from an operator), so
    existing links are cleared first to keep the scope rule intact.
    """
    for coll in list(obj.users_collection):
        coll.objects.unlink(obj)
    collection.objects.link(obj)


def clear_collection(collection, prefix: str | None = None) -> int:
    """Delete objects from *collection*, optionally only those matching *prefix*.

    This is the only deletion path in the project, and it is deliberately
    narrow: pass *prefix* whenever a builder knows which of its own objects it
    owns, so a rebuild can never take out hand-placed work.

    Args:
        collection: The collection to clear.
        prefix: If given, only objects whose name starts with it are removed.

    Returns:
        Number of objects removed.
    """
    removed = 0
    for obj in list(collection.objects):
        if prefix is not None and not obj.name.startswith(prefix):
            continue
        bpy.data.objects.remove(obj, do_unlink=True)
        removed += 1
    return removed


def purge_orphans() -> None:
    """Drop zero-user datablocks left behind by :func:`clear_collection`.

    Called once at the end of a full rebuild, not on every single deletion --
    purging is a whole-file operation and doing it per-object is wasteful.
    """
    bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)


# ---------------------------------------------------------------------------
# Objects
# ---------------------------------------------------------------------------


def get_or_create_object(name, collection, data_factory, location=(0.0, 0.0, 0.0),
                         parent=None):
    """Idempotently fetch or create an object *and* its data datablock.

    This is the single creation path for every generated object, and it exists
    because ``bpy.data.objects.new()`` is not idempotent: calling it twice
    yields ``LIGHT_Key`` and ``LIGHT_Key.001``, and the stale original keeps
    contributing to the render. Re-running a builder would then silently
    accumulate duplicates and never actually apply a changed setting.

    Reusing the object by name also preserves its identity, so keyframes and
    parent relationships survive a rebuild.

    Args:
        name: Explicit object name. The identity key.
        collection: Owning collection.
        data_factory: Callable taking *name* and returning a new datablock
            (light, camera, mesh, ...). Only called when the object is absent,
            so the data datablock is likewise reused across runs.
        location: World-space location.
        parent: Optional parent object.

    Returns:
        The existing or newly created object.
    """
    obj = bpy.data.objects.get(name)
    if obj is None:
        obj = bpy.data.objects.new(name, data_factory(name))
    link_object(obj, collection)
    obj.location = location
    if parent is not None:
        set_parent(obj, parent)
    return obj


def new_empty(name, collection, location=(0.0, 0.0, 0.0), parent=None,
              size: float = 2.0, display: str = "PLAIN_AXES"):
    """Idempotently create a control empty.

    Args:
        name: Explicit object name, e.g. ``CTRL_Virus``.
        collection: Owning collection.
        location: World-space location.
        parent: Optional parent object.
        size: Empty display size, for clickability in the viewport.
        display: ``empty_display_type`` enum.
    """
    empty = get_or_create_object(name, collection, lambda n: None,
                                 location=location, parent=parent)
    empty.empty_display_type = display
    empty.empty_display_size = size
    return empty


def set_parent(obj, parent) -> None:
    """Parent *obj* to *parent* keeping its current world transform.

    Uses the parent-inverse matrix rather than assuming an identity parent, so
    re-parenting an already-positioned object does not make it jump.
    """
    obj.parent = parent
    obj.matrix_parent_inverse = parent.matrix_world.inverted()


def instance_linked(source, name, collection, location=(0.0, 0.0, 0.0),
                    rotation=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0), parent=None):
    """Create an object that *shares* the mesh datablock of *source*.

    This is the instancing path the project uses instead of duplicating
    geometry. Five hundred virions cost one mesh datablock, not five hundred.

    Gotcha, and it is the whole point: because the data is shared, editing the
    source mesh in Edit Mode updates every instance at once. To give one
    instance its own geometry, make it single-user first
    (``Object > Relations > Make Single User``).

    Args:
        source: Object whose mesh data should be shared. Not modified.
        name: Explicit name for the new object.
        collection: Owning collection.
        location: World-space location.
        rotation: Euler rotation, radians.
        scale: Per-axis scale.
        parent: Optional parent object.

    Returns:
        The new object.
    """
    if source.data is None:
        raise ValueError("instance_linked: source {!r} has no data".format(source.name))

    obj = bpy.data.objects.new(name, source.data)
    obj.location = location
    obj.rotation_euler = rotation
    obj.scale = scale
    link_object(obj, collection)
    if parent is not None:
        set_parent(obj, parent)
    return obj


def instance_grid(source, prefix, collection, count, origin=(0.0, 0.0, 0.0),
                  spacing=(10.0, 10.0, 10.0), jitter: float = 0.0,
                  start_index: int = 1, parent=None, seed: int = 0):
    """Lay out *count* linked instances of *source* on a jittered grid.

    Cheap, repeatable way to stage a crowd for composition and performance
    tests. Deterministic via *seed*, so the same call always produces the same
    layout -- important when a render has to be reproduced later.

    Args:
        source: Object whose mesh data is shared by every instance.
        prefix: Name prefix; instances are named ``<prefix>_001``, ``_002``, ...
        collection: Owning collection.
        count: Number of instances.
        origin: World-space location of the first instance.
        spacing: Grid pitch in Blender units (micrometres).
        jitter: Maximum random offset per axis, as a fraction of *spacing*.
        start_index: First ordinal in the name.
        parent: Optional parent object.
        seed: RNG seed for reproducible jitter.

    Returns:
        The list of created objects.
    """
    import random

    rng = random.Random(seed)
    made = []
    for i in range(count):
        location = (
            origin[0] + spacing[0] * i,
            origin[1] + rng.uniform(-jitter, jitter) * spacing[1],
            origin[2] + rng.uniform(-jitter, jitter) * spacing[2],
        )
        made.append(
            instance_linked(
                source,
                obj_name(prefix, start_index + i),
                collection,
                location=location,
                parent=parent,
            )
        )
    return made


def clear_animation(target) -> None:
    """Remove any animation already on *target* so a re-run cannot stack keys.

    Without this, running an animation script twice leaves both sets of
    keyframes and the interpolation gets progressively wrong. Every animation
    script calls this before inserting its own.
    """
    target.animation_data_clear()


def aim_at(obj, target=(0.0, 0.0, 0.0)) -> None:
    """Point *obj*'s -Z axis at *target*. Works for cameras and spot lights."""
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------


def _set_input(node, socket: str, value) -> bool:
    """Assign *value* to *node.inputs[socket]* if that socket exists.

    Principled BSDF socket names shifted across Blender versions ('Subsurface'
    became 'Subsurface Weight', 'Emission' became 'Emission Color', ...). Going
    through this helper keeps asset scripts working across versions instead of
    raising on a rename.
    """
    if socket in node.inputs:
        node.inputs[socket].default_value = value
        return True
    return False


def principled_material(name, base_color=(0.5, 0.5, 0.5, 1.0), roughness: float = 0.5,
                        metallic: float = 0.0, emission=None,
                        emission_strength: float = 0.0, subsurface: float = 0.0,
                        subsurface_radius=(1.0, 0.2, 0.1), ior: float = 1.45,
                        alpha: float = 1.0):
    """Create or fetch a glTF-friendly Principled BSDF material.

    Only stock Principled inputs are used, with no procedural texture nodes.
    That is deliberate: the web viewer consumes a glTF export, and every
    procedural node in this graph is one the exporter cannot carry across.
    Add texture detail as image textures, not as node setups.

    Args:
        name: Explicit material name, e.g. ``MAT_Tumor_Cytoplasm``.
        base_color: RGBA linear base colour.
        roughness: 0.0 mirror .. 1.0 matte.
        metallic: 0.0 dielectric .. 1.0 metal.
        emission: RGBA emission colour. None leaves emission off.
        emission_strength: Emission Strength, only applied if *emission* is set.
        subsurface: Subsurface Weight. Non-zero gives cells a soft, wet look
            that a plain diffuse sphere cannot.
        subsurface_radius: Subsurface Radius, the classic (1.0, 0.2, 0.1)
            red-dominant scatter.
        ior: Index of refraction.
        alpha: 1.0 opaque, below 1.0 transparent.

    Returns:
        The material datablock.
    """
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat

    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is None:
        # Defensive: fall back to whichever node is the Principled one.
        for node in mat.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                bsdf = node
                break
    if bsdf is None:
        raise RuntimeError("principled_material: no Principled BSDF in {!r}".format(name))

    _set_input(bsdf, "Base Color", base_color)
    _set_input(bsdf, "Roughness", roughness)
    _set_input(bsdf, "Metallic", metallic)
    _set_input(bsdf, "IOR", ior)
    _set_input(bsdf, "Alpha", alpha)
    _set_input(bsdf, "Subsurface Weight", subsurface)
    _set_input(bsdf, "Subsurface Radius", subsurface_radius)
    if emission is not None:
        _set_input(bsdf, "Emission Color", emission)
        _set_input(bsdf, "Emission Strength", emission_strength)

    # Keep the viewport preview consistent with the render.
    mat.diffuse_color = base_color
    return mat


def assign_material(obj, material) -> None:
    """Give *obj* exactly one material slot holding *material*.

    Existing slots are cleared so a re-run does not stack duplicate slots.
    """
    obj.data.materials.clear()
    obj.data.materials.append(material)


def assign_materials(obj, materials) -> None:
    """Give *obj* one slot per material, in order (for multi-material meshes)."""
    obj.data.materials.clear()
    for mat in materials:
        obj.data.materials.append(mat)


# ---------------------------------------------------------------------------
# Project bootstrap helpers
# ---------------------------------------------------------------------------


def remove_startup_objects() -> list:
    """Delete the factory Cube / Light / Camera, if present.

    Only used on a deliberate full rebuild, so that a generated scene does not
    ship three unnamed leftovers sitting in a stray ``Collection``.
    """
    removed = []
    for name in STARTUP_OBJECTS:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
            removed.append(name)

    # Drop the default "Collection" if the startup objects were all it held.
    default = bpy.data.collections.get("Collection")
    if default is not None and not default.objects and not default.children:
        scene_root = bpy.context.scene.collection
        if default.name in scene_root.children:
            scene_root.children.unlink(default)
        bpy.data.collections.remove(default)
    return removed


def setup_units(scene=None) -> None:
    """Set the scene to report micrometres, matching the 1 BU == 1 um convention."""
    scene = scene or bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = SCENE_SCALE_LENGTH
    scene.unit_settings.length_unit = "MICROMETERS"
