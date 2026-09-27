"""HSV-1 virion asset: VIRUS_HSV1_001.

Scientific reference
--------------------
Real-world anchor for the whole project is **T-VEC (talimogene
laherparepvec)**, an engineered replication-competent oncolytic HSV-1. The
virion is built from four concentric parts, outermost first:

* **Envelope** -- lipid bilayer studded with glycoproteins, the visible spikes.
  These are what attach to cell-surface receptors and determine which cells
  the virus can infect.
* **Tegument** -- protein layer between envelope and capsid. Delivers the
  viral machinery on entry and is largely left behind in the cytoplasm.
* **Capsid** -- icosahedral protein shell.
* **Core** -- packaged double-stranded DNA.

Roughly 200 nm across overall, so ~0.2 Blender units in this project -- small
enough that a macro camera is mandatory and near-clip planes matter.

T-VEC specifics worth showing if the model goes that far: it carries
GM-CSF (to stimulate local dendritic cells) and lacZ (a visible reporter), and
its thymidine kinase gene is deleted. None of that is modelled yet.

Not yet implemented. This file fixes the module's public API.
"""

from __future__ import annotations

import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utilities as ut  # noqa: E402,F401

COLLECTION = "03_VIRUSES"
PREFIX = "VIRUS_HSV1"

#: Reference dimensions in micrometres == Blender units here.
#: Overall virion diameter is ~0.2 um; these are the concentric radii.
RADIUS_UM = 0.10
TEGUMENT_RADIUS_UM = 0.082
CAPSID_RADIUS_UM = 0.062
CORE_RADIUS_UM = 0.034


def build_virion(index: int = 1, location=(0.0, 0.0, 0.0), parent=None):
    """Create one virion and return it.

    Args:
        index: 1-based ordinal, used for the object name.
        location: World-space location of the virion centre.
        parent: Optional parent, normally ``CTRL_Virus``.

    Returns:
        The virion object, parented so the envelope, tegument, capsid and core
        move together.
    """
    raise NotImplementedError(
        "asset_virus_hsv1.build_virion: not implemented yet. Four concentric "
        "shells at the radii above; apply MAT_Virus_Envelope, "
        "MAT_Virus_Tegument, MAT_Virus_Capsid, MAT_Virus_Core. At 0.2 um "
        "across, keep subdivision low and check clip_start on the camera."
    )


def build_virion_population(count: int = 200, radius_um: float = 60.0,
                            parent=None, seed: int = 0):
    """Scatter *count* virions through a volume, sharing one mesh.

    All instances share the virion mesh, so a few hundred particles cost one
    datablock. The ``07_EFFECTS`` collection is the right home for the
    "cloud of virions in the bloodstream" beat rather than ``03_VIRUSES``.
    """
    raise NotImplementedError(
        "asset_virus_hsv1.build_virion_population: depends on build_virion."
    )


def main() -> None:
    build_virion()


if __name__ == "__main__":
    main()
