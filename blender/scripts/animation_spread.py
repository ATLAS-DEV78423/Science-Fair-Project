"""Animation: spread to neighbouring tumour cells.

Beat
----
Virions released by the dead cell infect adjacent cells, and the cycle repeats.
This is the step that turns a single infection into a treatment effect, and it
is where the project's instancing pays off: the spread is naturally many
virions moving to many cells, which is expensive as unique geometry and nearly
free as linked instances.

Beat structure
--------------
Use staggered, seeded offsets per virion rather than identical motion, or the
whole swarm reads as one object. ``ut.instance_linked`` gives every virion its
own transform, so a seeded per-instance delay is enough.

Note on the ECM: in a real tumour, dense stroma physically limits how far the
virus spreads, so the spread radius should be visibly bounded by the matrix
from :mod:`asset_ecm` rather than running to infinity.

Frames 360-480 of the master sequence.

Not yet implemented. This file fixes the module's public API.
"""

from __future__ import annotations

import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utilities as ut  # noqa: E402,F401

FRAME_START = 360
FRAME_END = 480


def animate_spread(virus_ctrl=None, tumor_ctrl=None, infection_count: int = 5,
                   frame_start: int = FRAME_START, frame_end: int = FRAME_END,
                   seed: int = 0):
    """Animate virions from a dead cell infecting neighbouring cells.

    Args:
        virus_ctrl: Controller for the virions. Defaults to ``CTRL_Virus``.
        tumor_ctrl: Controller for the newly infected cells. Defaults to ``CTRL_Tumor``.
        infection_count: How many neighbouring cells to infect.
        frame_start: First frame of the beat.
        frame_end: Last frame of the beat.
        seed: RNG seed, so the swarm is reproducible across runs.

    Returns:
        The list of objects that received keyframes.
    """
    raise NotImplementedError(
        "animation_spread: not implemented yet. Requires build_tumor_cluster "
        "from asset_tumor_cell to have real neighbouring cells to infect. "
        "Bound the spread radius against the ECM."
    )


def main() -> None:
    animate_spread()


if __name__ == "__main__":
    main()
