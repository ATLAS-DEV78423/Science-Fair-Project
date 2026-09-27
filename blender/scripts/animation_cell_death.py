"""Animation: tumour cell death following replication.

Beat
----
The infected cell fails and releases its newly made virions, which is how the
infection reaches neighbouring cells. The cell is also the source of the
tumour antigens that the immune response later reacts to, so dying cells and
the antigen signal are the same event seen two ways.

Beat structure
--------------
Rupture is a hard geometric change -- swelling, then a break, then dispersal.
Resist the temptation to fade it out: a cell that dissolves reads as a
cross-dissolve, not as lysis. Scale the cell up slightly, then let the virions
leave it on their own paths, so the dispersal is the visible cause of spread.

Frames 240-360 of the master sequence.

Not yet implemented. This file fixes the module's public API.
"""

from __future__ import annotations

import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utilities as ut  # noqa: E402,F401

FRAME_START = 240
FRAME_END = 360


def animate_cell_death(tumor_ctrl=None, virus_ctrl=None,
                       frame_start: int = FRAME_START, frame_end: int = FRAME_END):
    """Animate one tumour cell dying and releasing virions.

    Args:
        tumor_ctrl: Controller for the dying cell. Defaults to ``CTRL_Tumor``.
        virus_ctrl: Controller for the released virions. Defaults to ``CTRL_Virus``.
        frame_start: First frame of the beat.
        frame_end: Last frame of the beat.

    Returns:
        The list of objects that received keyframes.
    """
    raise NotImplementedError(
        "animation_cell_death: not implemented yet. Key the cell swell and "
        "rupture, then hand the virions off to animation_spread rather than "
        "animating dispersal here."
    )


def main() -> None:
    animate_cell_death()


if __name__ == "__main__":
    main()
