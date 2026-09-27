"""Extracellular matrix asset: ECM_FIBER_001.

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

**This is the one asset in the project that is genuinely a large number of
objects.** A convincing matrix is hundreds of fibres, which is exactly why
``ut.instance_linked`` matters: a single fibre mesh instanced several hundred
times, with per-instance rotation, is one datablock and a few hundred cheap
objects. Do not build this as a merged mesh -- it has to stay editable per
fibre, and a merged blob cannot be animated.

Not yet implemented. This file fixes the module's public API.
"""

from __future__ import annotations

import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utilities as ut  # noqa: E402,F401

COLLECTION = "06_EXTRACELLULAR_MATRIX"
PREFIX = "ECM_FIBER"

#: Reference dimensions in micrometres == Blender units here.
FIBER_RADIUS_UM = 0.06
FIBER_LENGTH_UM = 180.0


def build_fiber(index: int = 1, length_um: float = FIBER_LENGTH_UM,
                radius_um: float = FIBER_RADIUS_UM, location=(0.0, 0.0, 0.0),
                parent=None):
    """Create one collagen fibre and return it.

    Build as a curve with a small bevel depth, or a thin tube mesh. A curve
    is the better choice: fibres are long and their length is a parameter, and
    a curve with bevel_depth handles that without a remesh.

    Args:
        index: 1-based ordinal, used for the object name.
        length_um: Fibre length.
        radius_um: Fibre radius.
        location: World-space location of one end.
        parent: Optional parent object.

    Returns:
        The fibre object.
    """
    raise NotImplementedError(
        "asset_ecm.build_fiber: not implemented yet. Curve with bevel_depth = "
        "radius_um; apply MAT_ECM_Collagen."
    )


def build_matrix(fiber_count: int = 300, extent_um: float = 200.0,
                 parent=None, seed: int = 0):
    """Scatter *fiber_count* fibres through a box, sharing one source mesh.

    The reference implementation of why this project instances rather than
    duplicates. Seeded so the arrangement is reproducible.

    Returns:
        The list of fibre objects.
    """
    raise NotImplementedError(
        "asset_ecm.build_matrix: depends on build_fiber. Use ut.instance_linked "
        "for every fibre after the first."
    )


def main() -> None:
    build_matrix()


if __name__ == "__main__":
    main()
