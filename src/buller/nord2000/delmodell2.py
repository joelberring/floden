"""Delmodell 2 i Nord2000: plan mark med flera marktyper, med eller utan refraktion.

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.11, ekv. (124)–(133).

En marktyp är en kombination av flödesresistivitet σ och råhet r. Markeffekten räknas med
delmodell 1 för varje marktyp som om hela marken hade den typen, och resultaten vägs ihop med
modifierade Fresnelzonsvikter (F_λ = 1/4).

Med refraktion räknas vikterna med FresnelZoneW och FresnelZoneWm för krökta strålar
(avsnitt 5.23.6–5.23.7, ekv. 346–348), och f_L, f_H, Δα_L och r_h med de modifierade
storheterna (d, h_S, h_R med båge över, och ψ_G) ur den frekvensoberoende profilen.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np

from buller.nord2000.atmosfar import HomogenAtmosfar, som_atmosfar
from buller.nord2000.delmodell1 import Ct2Form, markeffekt_plan_mark_db
from buller.nord2000.fasdiff import fasdifferensfrekvens_hz
from buller.nord2000.frekvenser import vaglangd_m
from buller.nord2000.fresnel import (
    Reflexgeometri,
    fresnelzon_langs_mark,
    fresnelzon_langs_mark_refraktion,
    fresnelzonsandel,
    klippt_fresnelzon,
    reflexgeometri,
)
from buller.nord2000.geometri import MINSTA_HOJD_M
from buller.nord2000.mark import markimpedans
from buller.nord2000.refraktion import Atmosfar, calc_eq_ssp
from buller.nord2000.refraktion_mark import Skuggzonslasning
from buller.nord2000.stralar_krokta import Uppatmetod
from buller.typer import BullerFel, Flyttal

F_LAMBDA_DELMODELL2 = 0.25  # AV 1106/07 avsnitt 5.8 och 5.11
HOGSTA_FREKVENS_FASDIFF_HZ = 100_000.0  # AV 1106/07 avsnitt 5.23.14: extrapolation högst hit

# Fresnelzonens utsträckning: begränsad till sträckan källa–mottagare enligt avsnitt 5.8
# ("klippt", förval sedan P-S1) eller ekv. (349)–(351) som tryckta ("oklippt").
Fresnelzonsval = Literal["oklippt", "klippt"]
# Vikterna i skuggzonen, där PhaseDiffFreq inte har något bestämt svar (se docstring till
# `markeffekt_blandad_mark_db`): "hog" ger högfrekvensvikterna i alla band, "lag" lågfrekvens-
# vikterna i alla band och "ovre_band" lågfrekvensvikterna till och med 6,3 kHz.
Skuggzonsvikter = Literal["hog", "lag", "ovre_band"]


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
    tan_strykvinkel: float | None = None,
) -> tuple[float, float]:
    """Frekvenserna f_L och f_H för övergången mellan vikterna, AV 1106/07 ekv. (130)–(132).

    f_L hör till fasskillnaden Δα_L = π − (1,9483·ln(h_min) + 18,052)·tan ψ_G och f_H till π,
    båda räknade med impedansen för profilens minsta flödesresistivitet. f_L begränsas uppåt
    till 0,8·f_H.

    Med refraktion är avstånd och höjder de modifierade (ekv. 348) och `tan_strykvinkel` är
    tan ψ_G för den krökta strålen. Utan `tan_strykvinkel` används (h_S + h_R)/d (rät stråle).
    """
    impedanser = [markimpedans(float(f), minsta_flodesresistivitet_kNsm4) for f in frekvenser_hz]
    tan_psi = (
        (kallhojd_m + mottagarhojd_m) / avstand_m if tan_strykvinkel is None else tan_strykvinkel
    )
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
    geometri: Reflexgeometri | None = None,
    klippt: bool = False,
) -> list[float]:
    """Modifierade Fresnelzonsvikter w'_ii,ir(f) per marktyp, AV 1106/07 ekv. (125)–(129).

    Utan `geometri` räknas zonen med räta strålar. Med `geometri` (reflexgeometrin för krökta
    strålar ur `fresnel.reflexgeometri`) räknas zonen enligt ekv. (346)–(350) och r_h i
    ekv. (128) med tan ψ_G för den krökta strålen (0 i skuggzonen). `klippt` begränsar zonen
    till sträckan mellan källa och mottagare (avsnitt 5.8, se `fresnel.klippt_fresnelzon`).
    """
    f_lambda = F_LAMBDA_DELMODELL2 * vaglangd_m(frekvens_hz, ljudhastighet_mark_m_s)
    w_lag = [0.0] * len(typer)  # w_ii,ir,L, ekv. (127)
    r_typ = [0.0] * len(typer)  # r_ii,ir, ekv. (127)
    if geometri is None:
        zon = fresnelzon_langs_mark(avstand_m, kallhojd_m, mottagarhojd_m, f_lambda)
    else:
        zon = fresnelzon_langs_mark_refraktion(geometri, f_lambda)
    if klippt:
        zon = klippt_fresnelzon(zon, avstand_m)
    for s in segment:
        if s.d_slut_m <= s.d_start_m:
            continue  # avsnitt 5.11: segment utan utsträckning längs marken tas inte med
        nr = typer.index((s.flodesresistivitet_kNsm4, s.rahet_m))
        # FresnelZoneW (ekv. 351) och FresnelZoneWm (ekv. 353) för segmentet.
        w_lag[nr] += fresnelzonsandel(s.d_start_m, s.d_slut_m, zon.d1_fz_m, zon.d2_fz_m)
        r_typ[nr] += 0.5 * (
            fresnelzonsandel(s.d_start_m, s.d_slut_m, zon.d1_fz_m, zon.d_refl_m)
            + fresnelzonsandel(s.d_start_m, s.d_slut_m, zon.d_refl_m, zon.d2_fz_m)
        )

    # Ekv. (128): summera över råhet för varje flödesresistivitet (index ii).
    resistiviteter = sorted({t[0] for t in typer})
    r_ii = {
        sigma: sum(r_typ[k] for k, t in enumerate(typer) if t[0] == sigma)
        for sigma in resistiviteter
    }
    r_bis = {sigma: _r_bis(r_ii[sigma]) for sigma in resistiviteter}
    summa_r_bis = sum(r_bis.values())
    r_h = _r_h(
        (kallhojd_m + mottagarhojd_m) / avstand_m
        if geometri is None
        else math.tan(geometri.strykvinkel_rad)
    )
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
        elif frekvens_hz >= f_h_hz or f_l_hz <= 0.0:
            # f_L = 0 uppstår när ekv. (381) ger ett tal under flyttalens minsta (mycket negativ
            # Δα_L vid brant strykvinkel); gränsvärdet av ekv. (129) för f_L → 0 är w_H.
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
    atmosfar: HomogenAtmosfar | Atmosfar,
    uppat: Uppatmetod = "exakt",
    ct2_form: Ct2Form = "kvadrat",
    fresnelzon: Fresnelzonsval = "klippt",
    skuggzonsvikter: Skuggzonsvikter = "ovre_band",
    skuggzon: Skuggzonslasning | None = None,
    koherens_spridningszon: Flyttal | None = None,
) -> Flyttal:
    """Markeffekten ΔL_2 per tersband, AV 1106/07 avsnitt 5.11, ekv. (124).

    ΔL_2(f) = Σ w'_ii,ir(f)·ΔL_ii,ir(f), där ΔL_ii,ir är delmodell 1 för marktypen (σ_ii, r_ir).
    Höjder under 0,01 m ersätts med 0,01 m (avsnitt 5.4.1).

    Med refraktion (ξ ≠ 0 i den frekvensoberoende profilen ur CalcEqSSP) räknas vikterna med
    krökta strålar: zonen kring den verkliga reflexpunkten (ekv. 346–350), f_L och f_H med de
    modifierade storheterna (ekv. 130–131), Δα_L och r_h med ψ_G.

    I skuggzonen är ψ_G = 0 och de modifierade höjderna 0 (ekv. 347–348). Fasskillnaden i
    PhaseDiffFreq (ekv. 379) är då π i alla band, lika med det sökta värdet för både f_L och
    f_H, och avsnitt 5.23.14 säger inte vad frekvensen då är. `skuggzonsvikter` väljer:

    * `"ovre_band"` (förval): f_H = det näst högsta bandets frekvens (8 kHz) och
      f_L = 0,8·f_H. Det är vad den linjära extrapolationen "ur värdena vid 8 och 10 kHz"
      (avsnitt 5.23.14) ger när den förankras i 8 kHz och det sökta värdet är lika med
      Δα(8 kHz): tillägget (Δα − Δα(8 kHz))·(f₂ − f₁)/(Δα(10 kHz) − Δα(8 kHz)) har täljaren 0.
      Banden till och med 6,3 kHz får w_L (ekv. 127), 8 och 10 kHz får w_H.
    * `"lag"`: f_L och f_H över alla band (gränsvärdet av extrapolationen över 10 kHz, som
      stannar vid 100 kHz). Alla band får w_L.
    * `"hog"`: f_H = lägsta bandets frekvens (gränsvärdet av ekv. 381 när det sökta
      värdet går mot Δα(25 Hz) underifrån) och f_L = 0,8·f_H. Alla band får w_H, som med
      r_h = 0 är r′ i ekv. (128).

    Texten avgör inte. Förvalet är avgjort av testfallen (blad 10, 15 och 23; P-R2:s förval
    var `"hog"`): `"hog"` avviker med upp till 3,5 dB i tersband, och `"lag"` och
    `"ovre_band"` skiljer sig bara i 8 och 10 kHz, där `"ovre_band"` ligger inom 0,05 dB och
    `"lag"` 0,14–0,41 dB för lågt. Se `docs/buller/logg/P-S1.md`.

    `fresnelzon`: `"klippt"` (förval) begränsar zonen till sträckan källa–mottagare, som
    avsnitt 5.8 föreskriver i ord; `"oklippt"` är ekv. (349)–(351) som de är tryckta, utan
    begränsning. Förvalet var `"oklippt"` till och med P-R2 och är ändrat av testfallen
    (banden 25–630 Hz i alla blad på plan mark), se loggen.

    `skuggzon` (`refraktion_mark.Skuggzonslasning`, `None` = förvalen) förs vidare till
    delmodell 1 och väljer R₀ i ekv. (347).
    """
    if fresnelzon not in ("oklippt", "klippt"):
        raise BullerFel(f"Okänt val för Fresnelzonen: {fresnelzon!r} (oklippt eller klippt).")
    if skuggzonsvikter not in ("hog", "lag", "ovre_band"):
        raise BullerFel(
            f"Okänt val för vikterna i skuggzon: {skuggzonsvikter!r} (hog, lag eller ovre_band)."
        )
    if not segment:
        raise BullerFel("Delmodell 2 kräver minst ett marksegment.")
    atm = som_atmosfar(atmosfar)
    lasning = Skuggzonslasning() if skuggzon is None else skuggzon
    h_s = max(kallhojd_m, MINSTA_HOJD_M)
    h_r = max(mottagarhojd_m, MINSTA_HOJD_M)
    typer = marktyper(segment)
    effekter = [
        markeffekt_plan_mark_db(
            frekvenser_hz,
            avstand_m,
            h_s,
            h_r,
            sigma,
            rahet,
            atm,
            uppat,
            ct2_form,
            lasning,
            koherens_spridningszon=koherens_spridningszon,
        )
        for sigma, rahet in typer
    ]
    minsta_sigma = min(t[0] for t in typer)
    profil = calc_eq_ssp(h_s, h_r, atm) if atm.har_refraktion else None
    geometri: Reflexgeometri | None
    if profil is None or profil.xi_per_m == 0.0:
        c0 = atm.c_m_s
        geometri = None
        f_l, f_h = overgangsfrekvenser_hz(frekvenser_hz, avstand_m, h_s, h_r, minsta_sigma, c0)
    else:
        c0 = profil.c0_m_s
        geometri = reflexgeometri(avstand_m, h_s, h_r, profil.xi_per_m, c0, uppat, lasning.radie)
        if geometri.i_skuggzon and skuggzonsvikter == "hog":
            f_h = float(frekvenser_hz[0])
            f_l = 0.8 * f_h
        elif geometri.i_skuggzon and skuggzonsvikter == "ovre_band":
            f_h = float(frekvenser_hz[-2])
            f_l = 0.8 * f_h
        elif geometri.i_skuggzon:
            f_h = HOGSTA_FREKVENS_FASDIFF_HZ
            f_l = 0.8 * f_h
        else:
            f_l, f_h = overgangsfrekvenser_hz(
                frekvenser_hz,
                geometri.avstand_modifierat_m,
                geometri.kallhojd_modifierad_m,
                geometri.mottagarhojd_modifierad_m,
                minsta_sigma,
                c0,
                math.tan(geometri.strykvinkel_rad),
            )
    resultat = np.zeros(frekvenser_hz.shape[0], dtype=np.float64)
    for i, f in enumerate(frekvenser_hz):
        vikter = modifierade_vikter(
            float(f),
            avstand_m,
            h_s,
            h_r,
            segment,
            typer,
            c0,
            f_l,
            f_h,
            geometri,
            fresnelzon == "klippt",
        )
        resultat[i] = sum(w * float(effekt[i]) for w, effekt in zip(vikter, effekter, strict=True))
    return resultat
