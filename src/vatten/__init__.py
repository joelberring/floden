"""Vattenmodulen (skyfall) ovanpå kärnan: blåytor vid X mm, scenario → lapp, lösare i grafen.

Importen anmäler analystyperna `blaytor_mm`, `maxdjup_2d`, `fara_2d` och `tid_till_djup`
till kärnans frågelager.
"""

from __future__ import annotations

from vatten.blaytor import (
    Blaytemodell,
    BlaytorFel,
    BlaytorResultat,
    blaytor_vid_mm,
    k1_filer,
    las_k1,
    skriv_blaytor,
)
from vatten.losare import (
    LOSARE_BLAYTOR,
    LOSARE_BLAYTOR_SCENARIO,
    LOSARE_SANKGRAF,
    blaytor_nod,
    registrera,
    registrera_bas,
    registrera_scenarier,
)
from vatten.losare_2d import LOSARE_2D, nod_2d, registrera_2d
from vatten.scenario_lapp import ScenarioLappFel, scenario_till_lapp, tillampa_scenario

registrera()
registrera_2d()

__all__ = [
    "LOSARE_2D",
    "LOSARE_BLAYTOR",
    "LOSARE_BLAYTOR_SCENARIO",
    "LOSARE_SANKGRAF",
    "BlaytorFel",
    "BlaytorResultat",
    "Blaytemodell",
    "ScenarioLappFel",
    "blaytor_nod",
    "blaytor_vid_mm",
    "nod_2d",
    "registrera_2d",
    "k1_filer",
    "las_k1",
    "registrera",
    "registrera_bas",
    "registrera_scenarier",
    "scenario_till_lapp",
    "skriv_blaytor",
    "tillampa_scenario",
]
