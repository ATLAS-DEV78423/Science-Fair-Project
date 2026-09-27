"""Animation: viral replication inside an infected tumour cell.

Beat
----
Once the viral genome reaches the nucleus it is replicated and packaged: new
capsids are assembled around fresh genome, envelopes are acquired, and virion
numbers climb. This is the step T-VEC is engineered to confine to dividing
tumour cells, and the step that turns one infected cell into a factory.

Beat structure
--------------
Replication is a multiplication, not a single event, so the clearest way to
show it is an exponential: a handful of virions, then a swarm. Animate scale
or instance count, not hundreds of hand-placed objects -- ``ut.instance_grid``
with a growing visible count reads as replication and costs almost nothing.

Frames 120-240 of the master sequence.

Not yet implemented. This file fixes the module's public API.
"""

from __future__ import annotations

import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utilities as ut  # noqa: E402,F401

FRAME_START = 120
FRAME_END = 240


def animate_replication(tumor_ctrl=None, virus_ctrl=None,
                        frame_start: int = FRAME_START, frame_end: int = FRAME_END):
    """Animate virion replication inside one infected cell.

    Args:
        tumor_ctrl: Controller for the host cell. Defaults to ``CTRL_Tumor``.
        virus_ctrl: Controller for the virions. Defaults to ``CTRL_Virus``.
        frame_start: First frame of the beat.
        frame_end: Last frame of the beat.

    Returns:
        The list of objects that received keyframes.
    """
    raise NotImplementedError(
        "animation_replication: not implemented yet. Show growth as an "
        "increasing visible instance count rather than as unique geometry. "
        "Call ut.clear_animation on each target first."
    )


def main() -> None:
    animate_replication()


if __name__ == "__main__":
    main()
