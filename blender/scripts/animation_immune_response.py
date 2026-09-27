"""Animation: the anti-tumour immune response.

Beat
----
The viral wave provits an immune response, and this is the beat that explains
why oncolytic virotherapy is a *therapy* and not just a lytic virus. It also
explains T-VEC's GM-CSF transgene: the virus is partly doing the recruiting.

* **Natural killer cells** arrive first. They kill tumour cells directly,
  without needing the virus to have entered them at all, so they act on cells
  the virus has not infected.
* **Dendritic cells** take up antigen from dead tumour cells. T-VEC's GM-CSF
  is meant to draw these in specifically.
* **T cells** are primed by the dendritic cells and then kill tumour cells
  that are presenting viral antigen.

Beat structure
--------------
Recruitment should read as a converging swarm -- immune cells arriving from
outside the frame along the vasculature -- because that is where they
physically come from. It gives the beat a direction, and it ties
:mod:`asset_blood_vessel` into the sequence instead of leaving the vessels
as set dressing.

Keep the two killing mechanisms visually distinct. NK and T-cell killing look
similar in silhouette; separating them by timing and by which cell they act on
communicates more than any amount of colour difference would.

Frames 480-600 of the master sequence.

Not yet implemented. This file fixes the module's public API.
"""

from __future__ import annotations

import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utilities as ut  # noqa: E402,F401

FRAME_START = 480
FRAME_END = 600


def animate_immune_response(immune_ctrl=None, tumor_ctrl=None,
                            frame_start: int = FRAME_START,
                            frame_end: int = FRAME_END, seed: int = 0):
    """Animate immune recruitment and the two distinct killing mechanisms.

    Args:
        immune_ctrl: Controller for the immune cells. Defaults to ``CTRL_Immune``.
        tumor_ctrl: Controller for the affected tumour cells. Defaults to ``CTRL_Tumor``.
        frame_start: First frame of the beat.
        frame_end: Last frame of the beat.
        seed: RNG seed, so recruitment is reproducible across runs.

    Returns:
        The list of objects that received keyframes.
    """
    raise NotImplementedError(
        "animation_immune_response: not implemented yet. Needs "
        "build_immune_cell from asset_immune_cells. Stage NK arrival, then "
        "dendritic uptake, then T-cell priming and killing."
    )


def main() -> None:
    animate_immune_response()


if __name__ == "__main__":
    main()
