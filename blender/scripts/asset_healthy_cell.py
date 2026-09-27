"""Healthy tissue cell asset: CELL_HEALTHY_001.

Scientific reference
--------------------
The healthy counterpart to :mod:`asset_tumor_cell`, and the contrast is the
whole point -- a viewer has to be able to tell at a glance which cells the
virus is sparing.

* **Size** -- 10-20 um, at the smaller end of the tumour range.
* **Shape** -- flat, well-spread, regular. Healthy epithelial cells maintain
  contact inhibition and tile into a sheet.
* **Nucleus** -- small, regular, one per cell, low nuclear-to-cytoplasmic ratio.
* **Appearance** -- uniform and ordered, the opposite of the piled-up
  disorganisation that reads as malignancy.

Healthy cells are functionally non-permissive to T-VEC: the virus is engineered
to divide only in dividing tumour cells, so normal tissue largely escapes both
infection and the cell death that follows it.

Not yet implemented. This file fixes the module's public API.
"""

from __future__ import annotations

import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utilities as ut  # noqa: E402,F401

COLLECTION = "02_HEALTHY_TISSUE"
PREFIX = "CELL_HEALTHY"

#: Reference dimensions in micrometres == Blender units here.
RADIUS_UM = 10.0
NUCLEUS_RADIUS_UM = 4.5


def build_healthy_cell(index: int = 1, location=(0.0, 0.0, 0.0), parent=None):
    """Create one healthy cell and return it.

    Args:
        index: 1-based ordinal, used for the object name.
        location: World-space location of the cell centre.
        parent: Optional parent object. There is deliberately no
            ``CTRL_Healthy``; healthy tissue is static context, so it has no
            dedicated controller.

    Returns:
        The cell object.
    """
    raise NotImplementedError(
        "asset_healthy_cell.build_healthy_cell: not implemented yet. "
        "Flatter and more regular than build_tumor_cell, with a small nucleus; "
        "apply MAT_Healthy_Cytoplasm / MAT_Healthy_Nucleus."
    )


def build_healthy_sheet(rows: int = 6, columns: int = 6, spacing: float = 21.0,
                        parent=None):
    """Tile healthy cells into an epithelial sheet, sharing one mesh.

    Demonstrates the instancing path: a sheet of 36 cells is still one mesh
    datablock and 36 objects.
    """
    raise NotImplementedError(
        "asset_healthy_cell.build_healthy_sheet: depends on build_healthy_cell."
    )


def main() -> None:
    build_healthy_cell()


if __name__ == "__main__":
    main()
