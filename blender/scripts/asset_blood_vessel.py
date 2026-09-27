"""Blood vessel asset: VESSEL_001.

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

Not yet implemented. This file fixes the module's public API.
"""

from __future__ import annotations

import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utilities as ut  # noqa: E402,F401

COLLECTION = "05_BLOOD_VESSELS"
PREFIX = "VESSEL"

#: Reference dimensions in micrometres == Blender units here.
#: 5-10 um capillary diameter means a 2.5 um radius.
CAPILLARY_RADIUS_UM = 2.5
VENULE_RADIUS_UM = 15.0
#: Wall thickness for the endothelial lining.
WALL_THICKNESS_UM = 0.8


def build_vessel(index: int = 1, radius_um: float = CAPILLARY_RADIUS_UM,
                 length_um: float = 200.0, location=(0.0, 0.0, 0.0),
                 parent=None, branch_count: int = 0):
    """Create one vessel segment and return it.

    Build the wall as a tube rather than a solid cylinder: the lumen has to be
    genuinely hollow or anything shown travelling through the vessel will
    intersect solid geometry and read as clipping through it.

    Args:
        index: 1-based ordinal, used for the object name.
        radius_um: Outer radius, i.e. lumen radius plus wall.
        length_um: Segment length.
        location: World-space location of one end.
        parent: Optional parent object.
        branch_count: Number of child segments to branch off, for a tree.

    Returns:
        The vessel object. Branches, if any, are parented to it.
    """
    raise NotImplementedError(
        "asset_blood_vessel.build_vessel: not implemented yet. Hollow tube; "
        "apply MAT_Vessel_Wall and MAT_Vessel_Lumen. Tumour vasculature should "
        "be irregular, not a clean cylinder."
    )


def build_vessel_network(branches: int = 6, parent=None, seed: int = 0):
    """Build a branching vessel tree, sharing one mesh datablock per radius class."""
    raise NotImplementedError(
        "asset_blood_vessel.build_vessel_network: depends on build_vessel."
    )


def main() -> None:
    build_vessel()


if __name__ == "__main__":
    main()
