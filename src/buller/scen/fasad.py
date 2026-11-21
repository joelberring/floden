"""Fasadpunkter: `typer.Mottagare` längs byggnadsfasader, per våning.

Höjder för våningarna är parametrar utan standardvärden. Vilka höjder som ska gälla i
Sverige hör till anvisningarna för Nord2000 (se `docs/buller/kallor.md`) och sätts av B9.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from buller.scen.bullerscen import Bullerscen
from buller.typer import BullerFel, Mottagare


@dataclass(frozen=True)
class Fasadpunkter:
    """Fasadpunkter och redovisning av lägen som inte gav någon punkt."""

    mottagare: tuple[Mottagare, ...]
    antal_i_annan_byggnad: int  # punktläget hamnade inuti en byggnad (hopbyggda hus, inre hörn)
    antal_utanfor_raster: int  # punktläget ligger utanför terrängrastret
    antal_under_forsta_vaning: int  # fasaden är lägre än första våningens höjd


def fasadnormal_deg(normal_e: float, normal_n: float) -> float:
    """Kompassriktning för en normal: 0 = norr, medurs, i [0, 360)."""
    return math.degrees(math.atan2(normal_e, normal_n)) % 360.0


def fasadpunkter(
    scen: Bullerscen,
    *,
    avstand_fran_fasad_m: float,
    punktavstand_m: float,
    forsta_vaning_hojd_m: float,
    vaningshojd_m: float,
    max_vaningar: int | None = None,
    min_fasadlangd_m: float = 0.0,
    forsta_id: int = 0,
) -> Fasadpunkter:
    """Lägger mottagare framför varje fasad, på `avstand_fran_fasad_m` längs normalen.

    Varje väggsegment delas i `ceil(längd / punktavstand_m)` lika delar med en punkt mitt i
    varje. Våning k (1, 2, …) får höjden `forsta_vaning_hojd_m + (k - 1) · vaningshojd_m`
    över marken i punkten, så länge höjden ligger under byggnadens tak. Punkter vars läge
    hamnar inuti en byggnad eller utanför rastret hoppas över och räknas i resultatet.
    Id löper från `forsta_id` i ordningen (byggnad, vägg, punkt, våning).
    """
    for namn, varde in (
        ("avstand_fran_fasad_m", avstand_fran_fasad_m),
        ("punktavstand_m", punktavstand_m),
        ("forsta_vaning_hojd_m", forsta_vaning_hojd_m),
        ("vaningshojd_m", vaningshojd_m),
    ):
        if not (math.isfinite(varde) and varde > 0.0):
            raise BullerFel(f"{namn} ska vara > 0 m, fick {varde}.")
    if max_vaningar is not None and max_vaningar < 1:
        raise BullerFel(f"max_vaningar ska vara minst 1, fick {max_vaningar}.")
    if not (math.isfinite(min_fasadlangd_m) and min_fasadlangd_m >= 0.0):
        raise BullerFel(f"min_fasadlangd_m får inte vara negativ, fick {min_fasadlangd_m}.")

    vaggar = scen.vaggar
    urval = np.flatnonzero(vaggar.langd_m >= min_fasadlangd_m)
    antal_per_vagg = np.maximum(1, np.ceil(vaggar.langd_m[urval] / punktavstand_m)).astype(np.int64)
    vagg = np.repeat(urval, antal_per_vagg)
    start = np.cumsum(antal_per_vagg) - antal_per_vagg
    lopnr = np.arange(vagg.shape[0], dtype=np.int64) - np.repeat(start, antal_per_vagg)
    andel = (lopnr + 0.5) / np.repeat(antal_per_vagg, antal_per_vagg)
    e_m = (
        vaggar.a_e_m[vagg]
        + (vaggar.b_e_m[vagg] - vaggar.a_e_m[vagg]) * andel
        + vaggar.normal_e[vagg] * avstand_fran_fasad_m
    )
    n_m = (
        vaggar.a_n_m[vagg]
        + (vaggar.b_n_m[vagg] - vaggar.a_n_m[vagg]) * andel
        + vaggar.normal_n[vagg] * avstand_fran_fasad_m
    )
    inom = scen.inom_raster(e_m, n_m)
    i_byggnad = scen.i_byggnad(e_m, n_m)
    giltig = inom & ~i_byggnad
    mark_m = np.full(e_m.shape[0], np.nan)
    mark_m[giltig] = scen.markhojd_m(e_m[giltig], n_m[giltig])

    mottagare: list[Mottagare] = []
    antal_laga = 0
    for k in np.flatnonzero(giltig):
        v = int(vagg[k])
        fri_hojd_m = float(vaggar.tak_rh2000_m[v] - mark_m[k])
        vaning = 1
        hojd_m = forsta_vaning_hojd_m
        if hojd_m >= fri_hojd_m:
            antal_laga += 1
        while hojd_m < fri_hojd_m and (max_vaningar is None or vaning <= max_vaningar):
            mottagare.append(
                Mottagare(
                    id=forsta_id + len(mottagare),
                    e_m=float(e_m[k]),
                    n_m=float(n_m[k]),
                    hojd_over_mark_m=hojd_m,
                    byggnad_id=scen.byggnader[int(vaggar.byggnad_index[v])].id,
                    vaning=vaning,
                    fasadnormal_deg=fasadnormal_deg(
                        float(vaggar.normal_e[v]), float(vaggar.normal_n[v])
                    ),
                )
            )
            vaning += 1
            hojd_m = forsta_vaning_hojd_m + (vaning - 1) * vaningshojd_m
    return Fasadpunkter(
        mottagare=tuple(mottagare),
        antal_i_annan_byggnad=int(np.count_nonzero(inom & i_byggnad)),
        antal_utanfor_raster=int(np.count_nonzero(~inom)),
        antal_under_forsta_vaning=antal_laga,
    )
