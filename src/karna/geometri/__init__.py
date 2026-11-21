"""Gemensam geometri för alla domäner (K1 v2): byggnader, terräng, ytmodell, vegetation, markklass.

Läs-API: `las_byggnader`, `las_terrang`, `las_ytmodell`, `las_vegetation`, `las_mark`.
Fasadpunkter (K11): `fasadpunkter`. Markklasser per domän: `las_markklasser`.
K9-ändringar på v2-rutor: `karna.geometri.tillampa`.
"""

from __future__ import annotations

from karna.geometri.fasad import KONTRAKT_K11, Fasadpunkter, fasadpunkter
from karna.geometri.lasa import (
    k1_version,
    las_byggnader,
    las_mark,
    las_markslag,
    las_osakerhet,
    las_terrang,
    las_vegetation,
    las_ytmodell,
    ytmodell_fran,
)
from karna.geometri.markklass import Markklasstabell, las_markklasser
from karna.geometri.rasterfil import GeometriFel

__all__ = [
    "KONTRAKT_K11",
    "Fasadpunkter",
    "GeometriFel",
    "Markklasstabell",
    "fasadpunkter",
    "k1_version",
    "las_byggnader",
    "las_mark",
    "las_markklasser",
    "las_markslag",
    "las_osakerhet",
    "las_terrang",
    "las_vegetation",
    "las_ytmodell",
    "ytmodell_fran",
]
