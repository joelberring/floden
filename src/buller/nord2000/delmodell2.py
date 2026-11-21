"""Delmodell 2 i Nord2000: plan mark med flera marktyper (homogen atmosfär).

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.11, ekv. (124)–(133).

En marktyp är en kombination av flödesresistivitet σ och råhet r. Markeffekten räknas med
delmodell 1 för varje marktyp som om hela marken hade den typen, och resultaten vägs ihop med
modifierade Fresnelzonsvikter (F_λ = 1/4).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from buller.nord2000.atmosfar import HomogenAtmosfar
from buller.nord2000.delmodell1 import markeffekt_plan_mark_db
from buller.nord2000.fasdiff import fasdifferensfrekvens_hz
from buller.nord2000.frekvenser import vaglangd_m
from buller.nord2000.fresnel import fresnelzonsvikt, fresnelzonsvikt_modifierad
from buller.nord2000.geometri import MINSTA_HOJD_M
from buller.nord2000.mark import markimpedans
from buller.typer import BullerFel, Flyttal

F_LAMBDA_DELMODELL2 = 0.25  # AV 1106/07 avsnitt 5.8 och 5.11


@dataclass(frozen=True)
class Marksegment:
    """Marksegment på plan mark: avstånd längs marken från källan till början och slut."""

    d_start_m: float
    d_slut_m: float
    flodesresistivitet_kNsm4: float
    rahet_m: float


Marktyp = tuple[float, float]  # (flödesresistivitet i kNs/m⁴, råhet i m)


def marktyper(segment: Sequence[Marksegment]) -> list[Marktyp]:
    """De olika kombinationerna av σ och r i den ordning de först förekommer (avsnitt 5.11)."""
    typer: list[Marktyp] = []
    for s in segment:
        typ = (s.flodesresistivitet_kNsm4, s.rahet_m)
        if typ not in typer:
            typer.append(typ)
    return typer


def _r_bis(r: float) -> float:
    """Polynomet r''_ii i AV 1106/07 ekv. (128)."""
    return 8.78 * r**5 - 21.95 * r**4 + 21.76 * r**3 - 10.69 * r**2 + 3.1 * r


def _r_h(tan_psi: float) -> float:
    """Övergångsfaktorn r_h i AV 1106/07 ekv. (128)."""
    if tan_psi >= 0.04:
        return 1.0
    if tan_psi > 0.005:
        return math.log10(200.0 * tan_psi) / math.log10(8.0)
    return 0.0


def overgangsfrekvenser_hz(
    frekvenser_hz: Flyttal,
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    minsta_flodesresistivitet_kNsm4: float,
    ljudhastighet_mark_m_s: float,
) -> tuple[float, float]:
    """Frekvenserna f_L och f_H för övergången mellan vikterna, AV 1106/07 ekv. (130)–(132).

    f_L hör till fasskillnaden Δα_L = π − (1,9483·ln(h_min) + 18,052)·tan ψ_G och f_H till π,
    båda räknade med impedansen för profilens minsta flödesresistivitet. f_L begränsas uppåt
    till 0,8·f_H.
    """
    impedanser = [markimpedans(float(f), minsta_flodesresistivitet_kNsm4) for f in frekvenser_hz]
    tan_psi = (kallhojd_m + mottagarhojd_m) / avstand_m
    h_min = max(min(kallhojd_m, mottagarhojd_m), 0.01)
    alfa_l = math.pi - (1.9483 * math.log(h_min) + 18.052) * tan_psi
    f_l = fasdifferensfrekvens_hz(
        frekvenser_hz,
        avstand_m,
        kallhojd_m,
        mottagarhojd_m,
        impedanser,
        ljudhastighet_mark_m_s,
        alfa_l,
    )
    f_h = fasdifferensfrekvens_hz(
        frekvenser_hz,
        avstand_m,
        kallhojd_m,
        mottagarhojd_m,
        impedanser,
        ljudhastighet_mark_m_s,
        math.pi,
    )
    if f_l > 0.8 * f_h:
        f_l = 0.8 * f_h
    return f_l, f_h


def modifierade_vikter(
    frekvens_hz: float,
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    segment: Sequence[Marksegment],
    typer: Sequence[Marktyp],
    ljudhastighet_mark_m_s: float,
    f_l_hz: float,
    f_h_hz: float,
) -> list[float]:
    """Modifierade Fresnelzonsvikter w'_ii,ir(f) per marktyp, AV 1106/07 ekv. (125)–(129)."""
    f_lambda = F_LAMBDA_DELMODELL2 * vaglangd_m(frekvens_hz, ljudhastighet_mark_m_s)
    w_lag = [0.0] * len(typer)  # w_ii,ir,L, ekv. (127)
    r_typ = [0.0] * len(typer)  # r_ii,ir, ekv. (127)
    for s in segment:
        if s.d_slut_m <= s.d_start_m:
            continue  # avsnitt 5.11: segment utan utsträckning längs marken tas inte med
        nr = typer.index((s.flodesresistivitet_kNsm4, s.rahet_m))
        argument = (avstand_m, kallhojd_m, mottagarhojd_m, s.d_start_m, s.d_slut_m, f_lambda)
        w_lag[nr] += fresnelzonsvikt(*argument)
        r_typ[nr] += fresnelzonsvikt_modifierad(*argument)

    # Ekv. (128): summera över råhet för varje flödesresistivitet (index ii).
    resistiviteter = sorted({t[0] for t in typer})
    r_ii = {
        sigma: sum(r_typ[k] for k, t in enumerate(typer) if t[0] == sigma)
        for sigma in resistiviteter
    }
    r_bis = {sigma: _r_bis(r_ii[sigma]) for sigma in resistiviteter}
    summa_r_bis = sum(r_bis.values())
    r_h = _r_h((kallhojd_m + mottagarhojd_m) / avstand_m)
    vikter: list[float] = []
    for k, (sigma, _) in enumerate(typer):
        if r_ii[sigma] > 0.0 and summa_r_bis > 0.0:
            r_prim = r_bis[sigma] / summa_r_bis * r_typ[k] / r_ii[sigma]
        else:
            r_prim = 0.0
        w_hog = (r_typ[k] - r_prim) * r_h + r_prim
        # Ekv. (129): interpolation i logaritmisk frekvens mellan f_L och f_H.
        if frekvens_hz <= f_l_hz:
            vikter.append(w_lag[k])
        elif frekvens_hz >= f_h_hz:
            vikter.append(w_hog)
        else:
            andel = (math.log10(f_h_hz) - math.log10(frekvens_hz)) / (
                math.log10(f_h_hz) - math.log10(f_l_hz)
            )
            vikter.append(andel * (w_lag[k] - w_hog) + w_hog)
    return vikter


def markeffekt_blandad_mark_db(
    frekvenser_hz: Flyttal,
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    segment: Sequence[Marksegment],
    atmosfar: HomogenAtmosfar,
) -> Flyttal:
    """Markeffekten ΔL_2 per tersband, AV 1106/07 avsnitt 5.11, ekv. (124).

    ΔL_2(f) = Σ w'_ii,ir(f)·ΔL_ii,ir(f), där ΔL_ii,ir är delmodell 1 för marktypen (σ_ii, r_ir).
    Höjder under 0,01 m ersätts med 0,01 m (avsnitt 5.4.1).
    """
    if not segment:
        raise BullerFel("Delmodell 2 kräver minst ett marksegment.")
    h_s = max(kallhojd_m, MINSTA_HOJD_M)
    h_r = max(mottagarhojd_m, MINSTA_HOJD_M)
    c0 = atmosfar.ljudhastighet_m_s
    typer = marktyper(segment)
    effekter = [
        markeffekt_plan_mark_db(frekvenser_hz, avstand_m, h_s, h_r, sigma, rahet, atmosfar)
        for sigma, rahet in typer
    ]
    f_l, f_h = overgangsfrekvenser_hz(
        frekvenser_hz, avstand_m, h_s, h_r, min(t[0] for t in typer), c0
    )
    resultat = np.zeros(frekvenser_hz.shape[0], dtype=np.float64)
    for i, f in enumerate(frekvenser_hz):
        vikter = modifierade_vikter(float(f), avstand_m, h_s, h_r, segment, typer, c0, f_l, f_h)
        resultat[i] = sum(w * float(effekt[i]) for w, effekt in zip(vikter, effekter, strict=True))
    return resultat
