"""Immune cell assets: T-cell, NK cell, dendritic cell.

Scientific reference
--------------------
Three distinct immune roles, and the visualization should not blur them:

* **Dendritic cell** (``IMMUNE_DENDRITIC_001``) -- the antigen-presenting
  cell. It takes up viral material from a dead tumour cell, migrates to
  lymph nodes, and primes naive T cells. T-VEC is engineered to express
  GM-CSF partly to recruit these.
* **T cell** (``IMMUNE_TCELL_001``) -- the adaptive response. Cytotoxic
  T cells recognise tumour cells presenting viral antigen and kill them.
  Roughly 7-10 um, small and round, the smallest of the three.
* **Natural killer cell** (``IMMUNE_NK_001``) -- the innate response. Kills
  tumour cells without needing prior sensitisation, and is part of the
  anti-tumour effect that does not depend on the virus entering the cell.
  Larger than a T cell, with characteristic cytoplasmic granules.

These are all white-blood-cell scale, so they sit between virus and tumour
cell in size. Shared geometry means the three can be built from one base
mesh with different proportions and materials.

Not yet implemented. This file fixes the module's public API.
"""

from __future__ import annotations

import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utilities as ut  # noqa: E402,F401

COLLECTION = "04_IMMUNE_SYSTEM"

#: Object-name prefixes, one per cell type.
PREFIX_TCELL = "IMMUNE_TCELL"
PREFIX_NK = "IMMUNE_NK"
PREFIX_DENDRITIC = "IMMUNE_DENDRITIC"

#: Reference radii in micrometres == Blender units here.
RADIUS_TCELL_UM = 4.0
RADIUS_NK_UM = 5.5
#: Dendritic cells are the largest and by far the most irregular, with long
#: dendritic processes -- that shape is what makes them recognisable.
RADIUS_DENDRITIC_UM = 7.0


def build_immune_cell(cell_type: str = "tcell", index: int = 1,
                      location=(0.0, 0.0, 0.0), parent=None):
    """Create one immune cell and return it.

    Args:
        cell_type: One of ``"tcell"``, ``"nk"``, ``"dendritic"``.
        index: 1-based ordinal, used for the object name.
        location: World-space location of the cell centre.
        parent: Optional parent, normally ``CTRL_Immune``.

    Returns:
        The immune cell object.
    """
    raise NotImplementedError(
        "asset_immune_cells.build_immune_cell: not implemented yet. "
        "Apply MAT_Immune_Membrane plus per-type cytoplasm. The dendritic "
        "cell needs visible processes; NK cells need granules."
    )


def build_immune_population(cell_type: str = "tcell", count: int = 24,
                            radius_um: float = 80.0, parent=None, seed: int = 0):
    """Scatter *count* immune cells through a spherical volume.

    Seeded, so the same call always produces the same arrangement and a render
    stays reproducible.
    """
    raise NotImplementedError(
        "asset_immune_cells.build_immune_population: depends on build_immune_cell."
    )


def main() -> None:
    build_immune_cell("tcell")


if __name__ == "__main__":
    main()
