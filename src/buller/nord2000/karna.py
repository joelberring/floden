"""Referenskärnan för Nord2000:s spridningsmodell, punkt till punkt (ofullständig).

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.2 (ekv. 1) och 5.22 (ekv. 329–332), med
ändringarna i TC-101327 (2019).

Täckning i detta skede: homogen atmosfär utan fluktuerande refraktion, plan terräng (alla
terrängpunkter på en rät linje) med en eller flera marktyper, alltså delmodell 1, 2 och 9.
Allt annat ger `BullerFel`; se `docs/buller/logg/B4.md` och `docs/buller/B4-uppdelning.md`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from buller.nord2000.atmosfar import HomogenAtmosfar, homogen_atmosfar
from buller.nord2000.delmodell1 import markeffekt_plan_mark_db
from buller.nord2000.delmodell2 import Marksegment, markeffekt_blandad_mark_db, marktyper
from buller.nord2000.frekvenser import mittfrekvenser_hz
from buller.nord2000.luftabsorption import luftabsorption_db
from buller.nord2000.terrang import (
    GeometriOverPlanMark,
    Terranggeometri,
    ekvivalent_plan_mark,
    geometri_over_plan_mark,
    primar_skarmkant,
    skarmparameter_rscr1,
    terranggeometri,
)
from buller.typer import BullerFel, Flyttal, Terrangprofil, Vader

# Största vertikala avvikelse från den ekvivalenta plana marken för att terrängen ska räknas
# som plan i den här ofullständiga kärnan. Värdet är ett numeriskt skydd mot avrundning, inte
# en del av metoden: metodens egen övergång (r_flat, ekv. 317–318) kräver delmodell 3.
PLAN_TOLERANS_M = 1.0e-6


@dataclass(frozen=True)
class Nord2000Delresultat:
    """Utbredningseffekterna i AV 1106/07 ekv. (329), per tersband i dB."""

    frekvenser_hz: Flyttal
    spridning_db: float  # ΔL_d
    luftabsorption_db: Flyttal  # ΔL_a
    terrang_db: Flyttal  # ΔL_t
    delmodell: str

    @property
    def overforing_db(self) -> Flyttal:
        """L_R − L_W = ΔL_d + ΔL_a + ΔL_t (ΔL_s = ΔL_r = 0 i detta skede)."""
        summa: Flyttal = self.spridning_db + self.luftabsorption_db + self.terrang_db
        return summa


def sfarisk_spridning_db(avstand_m: float) -> float:
    """ΔL_d = −10·lg(4πR²), AV 1106/07 avsnitt 5.22, ekv. (330)."""
    return -10.0 * math.log10(4.0 * math.pi * avstand_m**2)


def marksegment_over_plan_mark(
    geo: Terranggeometri, over_plan: GeometriOverPlanMark
) -> list[Marksegment]:
    """Terrängsegmenten projicerade på den ekvivalenta plana marken, AV 1106/07 ekv. (133)."""
    avstand = [
        over_plan.avstand_langs_mark_m(float(geo.x_m[i]), float(geo.z_m[i]))
        for i in range(geo.x_m.shape[0])
    ]
    return [
        Marksegment(
            d_start_m=avstand[i],
            d_slut_m=avstand[i + 1],
            flodesresistivitet_kNsm4=float(geo.flodesresistivitet_kNsm4[i]),
            rahet_m=float(geo.rahet_m[i]),
        )
        for i in range(geo.x_m.shape[0] - 1)
    ]


def markeffekt_plan_modell_db(
    frekvenser_hz: Flyttal,
    over_plan: GeometriOverPlanMark,
    segment: list[Marksegment],
    atmosfar: HomogenAtmosfar,
) -> tuple[Flyttal, str]:
    """ΔL_flat: delmodell 1 vid en marktyp, annars delmodell 2 (AV 1106/07 ekv. 332)."""
    typer = marktyper(segment)
    if len(typer) == 1:
        sigma, rahet = typer[0]
        return (
            markeffekt_plan_mark_db(
                frekvenser_hz,
                over_plan.d_e_m,
                over_plan.h_se_m,
                over_plan.h_re_m,
                sigma,
                rahet,
                atmosfar,
            ),
            "1",
        )
    return (
        markeffekt_blandad_mark_db(
            frekvenser_hz, over_plan.d_e_m, over_plan.h_se_m, over_plan.h_re_m, segment, atmosfar
        ),
        "2",
    )


class Nord2000Karna:
    """Punkt-till-punkt-kärna enligt Nord2000 (uppfyller `buller.typer.P2PKarna`).

    `nord2000_konform` är falsk: kärnan täcker bara en del av metoden och har inte passerat
    testsviten. Fall utanför täckningen ger `BullerFel`, aldrig ett närmevärde.
    """

    namn: str = "nord2000-referens"
    version: str = "0.1.0"
    nord2000_konform: bool = False

    def berakna(self, profil: Terrangprofil, vader: Vader) -> Nord2000Delresultat:
        """Räknar alla utbredningseffekter i ekv. (329) för en profil och ett väder."""
        atmosfar = homogen_atmosfar(vader)
        c = atmosfar.ljudhastighet_m_s
        frekvenser = mittfrekvenser_hz()
        geo = terranggeometri(profil)

        kant = primar_skarmkant(geo)
        if kant is not None and any(
            skarmparameter_rscr1(float(f), kant, geo.d_bas_tot_m, c) > 0.0 for f in frekvenser
        ):
            raise BullerFel(
                "Nord2000Karna täcker ännu inte skärmar: terrängpunkt "
                f"{kant.index} (x = {float(geo.x_m[kant.index]):.2f} m) ger r_scr1 > 0 "
                "(AV 1106/07 avsnitt 5.21.1), och delmodell 4–7 är inte implementerade."
            )

        plan = ekvivalent_plan_mark(geo.x_m, geo.z_m)
        storsta_avvikelse = float(np.max(np.abs(geo.z_m - plan.alfa_m - plan.beta * geo.x_m)))
        if storsta_avvikelse > PLAN_TOLERANS_M:
            raise BullerFel(
                "Nord2000Karna täcker ännu inte icke-plan terräng: profilen avviker "
                f"{storsta_avvikelse:.3g} m från plan mark, och delmodell 3 samt övergången "
                "r_flat (AV 1106/07 avsnitt 5.12 och 5.21.4) är inte implementerade."
            )

        over_plan = geometri_over_plan_mark(geo, plan)
        segment = marksegment_over_plan_mark(geo, over_plan)
        terrang, delmodell = markeffekt_plan_modell_db(frekvenser, over_plan, segment, atmosfar)
        return Nord2000Delresultat(
            frekvenser_hz=frekvenser,
            spridning_db=sfarisk_spridning_db(geo.r_sr_m),
            luftabsorption_db=luftabsorption_db(
                frekvenser,
                geo.r_sr_m,
                atmosfar.temperatur_c,
                atmosfar.relativ_fuktighet_procent,
                atmosfar.lufttryck_kpa,
            ),
            terrang_db=terrang,
            delmodell=delmodell,
        )

    def overforing_db(self, profil: Terrangprofil, vader: Vader) -> Flyttal:
        """L_p − L_W per tersband, form (ANTAL_BAND,), AV 1106/07 ekv. (329)."""
        return self.berakna(profil, vader).overforing_db
