"""Tumour cell asset: CELL_TUMOR_001 and its nucleus.

Scientific reference
--------------------
Malignant cells read as visually distinct from healthy tissue on four axes,
and the visualization depends on those axes being visible at a glance:

* **Size** -- typically 15-25 um against 10-20 um for a healthy epithelial cell.
* **Nuclear-to-cytoplasmic ratio** -- markedly raised. The nucleus is both
  larger and more dominant, often filling most of the cell volume.
* **Shape** -- loss of contact inhibition, so cells are rounded and piled up
  rather than flat and tiled.
* **Nucleoli** -- enlarged and often multiple.

This is the cell T-VEC selectively infects, so it is the primary subject of
the whole animation sequence.

Not yet implemented. This file fixes the module's public API so other scripts
can import it and the signature does not drift later.
"""

from __future__ import annotations

import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utilities as ut  # noqa: E402,F401

#: Collection this asset writes to. The scope rule in utilities.py guarantees
#: a rebuild here cannot touch any other collection.
COLLECTION = "01_TUMOR"

#: Object-name prefixes: CELL_TUMOR -> CELL_TUMOR_001
PREFIX = "CELL_TUMOR"
PREFIX_NUCLEUS = "CELL_TUMOR_NUCLEUS"

#: Reference dimensions in micrometres, which is also Blender units here.
RADIUS_UM = 12.0
NUCLEUS_RADIUS_UM = 7.0


def build_tumor_cell(index: int = 1, location=(0.0, 0.0, 0.0), parent=None,
                     create_nucleus: bool = True):
    """Create one tumour cell and return ``(cell, nucleus)``.

    The nucleus is parented to the cell so that moving or animating the cell
    carries its nucleus along, and so that an animation can additionally
    scale the nucleus independently for a mitotic beat.

    Args:
        index: 1-based ordinal, used for the object name.
        location: World-space location of the cell centre.
        parent: Optional parent, normally ``CTRL_Tumor``.
        create_nucleus: Set False for a cell whose nucleus is authored
            separately.

    Returns:
        ``(cell, nucleus_or_None)``.
    """
    raise NotImplementedError(
        "asset_tumor_cell.build_tumor_cell: not implemented yet. "
        "Build the membrane, cytoplasm and nucleus shells; apply "
        "MAT_Tumor_Membrane / MAT_Tumor_Cytoplasm / MAT_Tumor_Nucleus; "
        "register CTRL_Tumor as the parent."
    )


def build_tumor_cluster(count: int = 8, spacing: float = 22.0, parent=None, seed: int = 0):
    """Create a packed cluster of tumour cells, all sharing one mesh.

    Uses ``ut.instance_linked`` so the cluster costs a single mesh datablock
    rather than *count* meshes. Returns the list of cell objects.
    """
    raise NotImplementedError(
        "asset_tumor_cell.build_tumor_cluster: depends on build_tumor_cell."
    )


def main() -> None:
    build_tumor_cell()


if __name__ == "__main__":
    main()
