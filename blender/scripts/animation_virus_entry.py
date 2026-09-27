"""Animation: virus entry into a tumour cell.

Beat
----
The virion reaches the cell surface, attaches via its envelope glycoproteins,
the envelope fuses with the cell membrane, and the capsid is delivered into
the cytoplasm. The naked capsid then migrates to the nucleus.

Drive
-----
Animated through ``CTRL_Virus`` and ``CTRL_Tumor`` where possible, not by
keyframing individual meshes. The controller empties exist precisely so the
web viewer receives a small clean node graph to drive rather than hundreds of
animated meshes. A mesh that genuinely needs its own keys (an unzipping
envelope, say) is the exception, not the rule.

Frames 1-120 of the master sequence.

Not yet implemented. This file fixes the module's public API.
"""

from __future__ import annotations

import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utilities as ut  # noqa: E402,F401

#: Frame window this beat occupies in the master sequence.
FRAME_START = 1
FRAME_END = 120


def animate_virus_entry(virus_ctrl=None, tumor_ctrl=None,
                        frame_start: int = FRAME_START, frame_end: int = FRAME_END):
    """Animate one virion entering one tumour cell.

    Args:
        virus_ctrl: Controller to animate. Defaults to ``CTRL_Virus``.
        tumor_ctrl: Controller to animate. Defaults to ``CTRL_Tumor``.
        frame_start: First frame of the beat.
        frame_end: Last frame of the beat.

    Returns:
        The list of objects that received keyframes.
    """
    raise NotImplementedError(
        "animation_virus_entry: not implemented yet. Call "
        "ut.clear_animation on each target first, then key CTRL_Virus along an "
        "approach path, through contact, and into the cell. Add the "
        "envelope-fusion beat as a shape key on the virion."
    )


def main() -> None:
    animate_virus_entry()


if __name__ == "__main__":
    main()
