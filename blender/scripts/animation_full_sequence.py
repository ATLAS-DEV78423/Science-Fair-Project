"""Animation: the full master sequence.

Beat
----
Orchestrates the five beats in order and switches the active camera per beat,
so a single render produces the whole story.

    entry        frames    1-120   virus reaches and enters the cell
    replication  frames  120-240   virions multiply inside it
    cell death   frames  240-360   the cell ruptures and releases them
    spread       frames  360-480   neighbouring cells are infected
    immune       frames  480-600   the anti-tumour immune response

This script owns the scene frame range and the camera cuts; the individual
beat scripts own nothing but their own keyframes. That split is deliberate --
it means a single beat can be re-rendered in isolation without rebuilding the
whole timeline.

Camera plan
-----------
The four cameras are built for exactly this, one per scale:

    CAMERA_Virus   the entry and replication beats, where the virion is 0.2 um
    CAMERA_Cell    replication through immune response
    CAMERA_Macro   spread, to hold several cells and the surrounding matrix
    CAMERA_Master  the establishing shot, open and close

Cuts are markers, not keyframes on the camera object, so the web viewer can
seek the sequence without evaluating the camera's fcurves.

Not yet implemented. This file fixes the module's public API.
"""

from __future__ import annotations

import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utilities as ut  # noqa: E402,F401

#: The beats, in order. ``camera`` names one of the four built by
#: :mod:`scene_tumor`; ``module`` is the script that owns the beat's keyframes.
BEATS = (
    ("entry", 1, 120, "CAMERA_Virus", "animation_virus_entry"),
    ("replication", 120, 240, "CAMERA_Cell", "animation_replication"),
    ("cell_death", 240, 360, "CAMERA_Cell", "animation_cell_death"),
    ("spread", 360, 480, "CAMERA_Macro", "animation_spread"),
    ("immune", 480, 600, "CAMERA_Master", "animation_immune_response"),
)

FRAME_START = BEATS[0][1]
FRAME_END = BEATS[-1][2]


def add_camera_cuts() -> int:
    """Add a timeline marker at each beat boundary and return the marker count.

    Markers rather than camera keyframes: the web viewer reads markers to build
    a chapter list, and a marker costs nothing to seek past.
    """
    for name, start, _end, _cam, _mod in BEATS:
        bpy.context.scene.timeline_markers.new(name, frame=start)
    return len(BEATS)


def build_full_sequence(frame_start: int = FRAME_START, frame_end: int = FRAME_END):
    """Extend the scene frame range and run every beat in order.

    Extending the frame range is this script's job and not the individual
    beats': the individual beats are written to fit their window regardless of
    what the scene is currently set to, so they stay reusable outside the
    master sequence.

    Args:
        frame_start: First frame of the sequence.
        frame_end: Last frame of the sequence.

    Returns:
        A summary dict of what was built.
    """
    raise NotImplementedError(
        "animation_full_sequence: not implemented yet. Requires all five beat "
        "scripts, which in turn require the six asset scripts. Set "
        "scene.frame_end before running, then call each beat in order and add "
        "the camera cuts."
    )


def main() -> None:
    build_full_sequence()


if __name__ == "__main__":
    main()
