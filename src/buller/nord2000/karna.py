"""Referenskärnan för Nord2000:s spridningsmodell, punkt till punkt (ofullständig).

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.2 (ekv. 1) och 5.22 (ekv. 329–332), med
ändringarna i TC-101327 (2019).

Täckning i detta skede: plan terräng (alla terrängpunkter på en rät linje) med en eller
flera marktyper, alltså delmodell 1, 2 och 9, med eller utan refraktion (vind,
temperaturgradient och deras fluktuationer, inklusive skuggzon vid uppåtbrytning).
Allt annat ger `BullerFel`; se `docs/buller/logg/B4.md`, `docs/buller/logg/P-R2.md` och
`docs/buller/B4-uppdelning.md`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from buller.nord2000.atmosfar import HomogenAtmosfar
from buller.nord2000.delmodell1 import Ct2Form, markeffekt_plan_mark_db
from buller.nord2000.delmodell2 import (
    Fresnelzonsval,
    Marksegment,
    Skuggzonsvikter,
    markeffekt_blandad_mark_db,
    marktyper,
)
from buller.nord2000.frekvenser import mittfrekvenser_hz
from buller.nord2000.luftabsorption import luftabsorption_db
from buller.nord2000.refraktion import Atmosfar, atmosfar_ur_vader
from buller.nord2000.refraktion_mark import antal_stralar_flera_markreflexer
from buller.nord2000.stralar_krokta import Uppatmetod
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

# AV 1106/07 avsnitt 5.17: under N = 4 bidrar delmodell 8 (flera markreflexer) inte.
MINSTA_N_DELMODELL8 = 4.0

Delmodell8Val = Literal["vagra", "utelamna"]


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
    atmosfar: HomogenAtmosfar | Atmosfar,
    uppat: Uppatmetod = "bokstavlig",
    ct2_form: Ct2Form = "kvadrat",
    fresnelzon: Fresnelzonsval = "oklippt",
    skuggzonsvikter: Skuggzonsvikter = "hog",
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
                uppat,
                ct2_form,
            ),
            "1",
        )
    return (
        markeffekt_blandad_mark_db(
            frekvenser_hz,
            over_plan.d_e_m,
            over_plan.h_se_m,
            over_plan.h_re_m,
            segment,
            atmosfar,
            uppat,
            ct2_form,
            fresnelzon,
            skuggzonsvikter,
        ),
        "2",
    )


class Nord2000Karna:
    """Punkt-till-punkt-kärna enligt Nord2000 (uppfyller `buller.typer.P2PKarna`).

    `nord2000_konform` är falsk: kärnan täcker bara en del av metoden och har inte passerat
    testsviten. Fall utanför täckningen ger `BullerFel`, aldrig ett närmevärde.

    Två val där metodtexten medger två läsningar kan ställas in (förvalen är de läsningar
    texten stöder bäst; se `docs/buller/logg/P-R1.md`, `P-7.md` och `P-R2.md`):

    * `uppat`: strålar vid uppåtbrytning, `"bokstavlig"` (|ξ′|, AV 1106/07 avsnitt 5.5.4) eller
      `"exakt"` (den verkliga uppåtkrökta bågen).
    * `ct2_form`: temperaturtermen i F_c (ekv. 113), `"kvadrat"` (C_T²/T²) eller `"tryckt"`
      (C_T²/T).

    * `fresnelzon`: Fresnelzonen i delmodell 2, `"oklippt"` (ekv. 349–351 som tryckta) eller
      `"klippt"` (begränsad till sträckan källa–mottagare, avsnitt 5.8).

    * `skuggzonsvikter`: delmodell 2 i skuggzon, `"hog"` (w_H i alla band) eller `"lag"` (w_L i
      alla band); texten avgör inte, se `delmodell2.markeffekt_blandad_mark_db`.

    Delmodell 8 (flera markreflexer vid stark nedåtbrytning, avsnitt 5.17) ingår inte. När
    N i ekv. (279) är större än 4 skulle den bidra, och då ger kärnan `BullerFel`
    (`delmodell8="vagra"`, förval). Med `delmodell8="utelamna"` räknas ΔL_t ändå, utan ΔL₈;
    det är avsett för utredning och resultatet är då inte metodens (för lågt).
    """

    namn: str = "nord2000-referens"
    version: str = "0.2.0"
    nord2000_konform: bool = False

    def __init__(
        self,
        uppat: Uppatmetod = "bokstavlig",
        ct2_form: Ct2Form = "kvadrat",
        delmodell8: Delmodell8Val = "vagra",
        fresnelzon: Fresnelzonsval = "oklippt",
        skuggzonsvikter: Skuggzonsvikter = "hog",
    ) -> None:
        if skuggzonsvikter not in ("hog", "lag"):
            raise BullerFel(
                f"Okänt val för vikterna i skuggzon: {skuggzonsvikter!r} (hog eller lag)."
            )
        self.skuggzonsvikter: Skuggzonsvikter = skuggzonsvikter
        if fresnelzon not in ("oklippt", "klippt"):
            raise BullerFel(f"Okänt val för Fresnelzonen: {fresnelzon!r} (oklippt eller klippt).")
        self.fresnelzon: Fresnelzonsval = fresnelzon
        if delmodell8 not in ("vagra", "utelamna"):
            raise BullerFel(f"Okänt val för delmodell 8: {delmodell8!r} (vagra eller utelamna).")
        self.delmodell8: Delmodell8Val = delmodell8
        if uppat not in ("bokstavlig", "exakt"):
            raise BullerFel(f"Okänd metod för uppåtbrytning: {uppat!r} (bokstavlig eller exakt).")
        if ct2_form not in ("kvadrat", "tryckt"):
            raise BullerFel(f"Okänd form för C_T²-termen: {ct2_form!r} (kvadrat eller tryckt).")
        self.uppat: Uppatmetod = uppat
        self.ct2_form: Ct2Form = ct2_form

    def berakna(self, profil: Terrangprofil, vader: Vader) -> Nord2000Delresultat:
        """Räknar alla utbredningseffekter i ekv. (329) för en profil och ett väder.

        Vädret översätts med `refraktion.atmosfar_ur_vader` (A, B, s_A, s_B). Skärmkontrollen
        (r_scr1) räknas med Δℓ₀ och C som i homogen atmosfär; ekv. (301) behövs först när
        skärmar med refraktion ingår, och sådana profiler ger ändå `BullerFel` här.
        ΔL_d och ΔL_a räknas med R_SR (ekv. 8), även med refraktion.
        """
        atmosfar = atmosfar_ur_vader(vader)
        c = atmosfar.c_m_s
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
        antal_stralar = antal_stralar_flera_markreflexer(
            geo.d_bas_tot_m, max(over_plan.h_se_m, over_plan.h_re_m), atmosfar
        )
        if antal_stralar > MINSTA_N_DELMODELL8 and self.delmodell8 == "vagra":
            raise BullerFel(
                "Nord2000Karna täcker ännu inte flera markreflexer: N = "
                f"{antal_stralar:.1f} > 4 i AV 1106/07 ekv. (279) (nedåtbrytning över "
                f"{geo.d_bas_tot_m:.0f} m), och delmodell 8 (avsnitt 5.17) är inte implementerad."
            )
        segment = marksegment_over_plan_mark(geo, over_plan)
        terrang, delmodell = markeffekt_plan_modell_db(
            frekvenser,
            over_plan,
            segment,
            atmosfar,
            self.uppat,
            self.ct2_form,
            self.fresnelzon,
            self.skuggzonsvikter,
        )
        return Nord2000Delresultat(
            frekvenser_hz=frekvenser,
            spridning_db=sfarisk_spridning_db(geo.r_sr_m),
            luftabsorption_db=luftabsorption_db(
                frekvenser,
                geo.r_sr_m,
                atmosfar.temperatur_luft_c,
                atmosfar.relativ_fuktighet_procent,
                atmosfar.lufttryck_kpa,
            ),
            terrang_db=terrang,
            delmodell=delmodell,
        )

    def overforing_db(self, profil: Terrangprofil, vader: Vader) -> Flyttal:
        """L_p − L_W per tersband, form (ANTAL_BAND,), AV 1106/07 ekv. (329)."""
        return self.berakna(profil, vader).overforing_db
