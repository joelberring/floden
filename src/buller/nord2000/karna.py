"""Referenskärnan för Nord2000:s spridningsmodell, punkt till punkt (ofullständig).

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.2 (ekv. 1) och 5.22 (ekv. 329–332), med
ändringarna i TC-101327 (2019).

Täckning i detta skede: plan terräng (alla terrängpunkter på en rät linje) med en eller
flera marktyper, alltså delmodell 1, 2, 8 och 9, med eller utan refraktion (vind,
temperaturgradient och deras fluktuationer, inklusive skuggzon vid uppåtbrytning och flera
markreflexer vid stark nedåtbrytning). Allt annat ger `BullerFel`; se
`docs/buller/logg/B4.md`, `docs/buller/logg/P-R2.md`, `docs/buller/logg/P-S1.md` och
`docs/buller/B4-uppdelning.md`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from buller.nord2000 import delmodell8
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
from buller.nord2000.luftabsorption import luftabsorption_db, rentonsdampning_db_per_m
from buller.nord2000.refraktion import Atmosfar, atmosfar_ur_vader
from buller.nord2000.refraktion_mark import Skuggzonslasning
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

# Delmodell 8 (AV 1106/07 avsnitt 5.17): "hog" är högfrekvenslösningen ΔL_multi,hf (inkoherent
# summa, metodens rekommendation för normalt bruk), "lag" lågfrekvenslösningen ΔL_multi,lf och
# "utelamna" räknar utan ΔL₈ (för utredning; resultatet är då inte metodens).
Delmodell8Val = Literal["hog", "lag", "utelamna"]

# Luftabsorptionens bandformel (ekv. 287) utanför det område där den avtar med avståndet:
# "monoton" (förval) håller kvar formelns största dämpning, "tryckt" räknar formeln som den
# står (och ger `BullerFel` där den inte är definierad). Se `luftabsorption_monoton_db`.
Luftabsorptionsval = Literal["monoton", "tryckt"]

# AV 1106/07 ekv. (287): ΔL₉ = −A₀·(1,0053255 − 0,00122622·A₀)^1,6.
_BANDFORMEL_A = 1.0053255
_BANDFORMEL_B = 0.00122622
_BANDFORMEL_EXPONENT = 1.6
# Där har A₀·(a − b·A₀)^1,6 sitt maximum: derivatan (a − b·A₀)^0,6·(a − 2,6·b·A₀) är noll.
STORSTA_A0_BANDFORMEL_DB = _BANDFORMEL_A / ((1.0 + _BANDFORMEL_EXPONENT) * _BANDFORMEL_B)


@dataclass(frozen=True)
class Nord2000Delresultat:
    """Utbredningseffekterna i AV 1106/07 ekv. (329), per tersband i dB."""

    frekvenser_hz: Flyttal
    spridning_db: float  # ΔL_d
    luftabsorption_db: Flyttal  # ΔL_a
    terrang_db: Flyttal  # ΔL_t, inklusive ⊕ ΔL₈ (ekv. 332)
    delmodell: str
    antal_stralar: float = 0.0  # N i ekv. (279); delmodell 8 bidrar när N > 4
    flera_markreflexer_db: Flyttal | None = field(default=None)  # ΔL₈; None när N ≤ 4

    @property
    def overforing_db(self) -> Flyttal:
        """L_R − L_W = ΔL_d + ΔL_a + ΔL_t (ΔL_s = ΔL_r = 0 i detta skede)."""
        summa: Flyttal = self.spridning_db + self.luftabsorption_db + self.terrang_db
        return summa


def luftabsorption_monoton_db(
    frekvenser_hz: Flyttal,
    vagstracka_m: float,
    lufttemperatur_c: float,
    relativ_fuktighet_procent: float,
    lufttryck_kpa: float,
) -> Flyttal:
    """ΔL_a per tersband: AV 1106/07 ekv. (286)–(287), monoton i avståndet.

    Ekv. (287), ΔL₉ = −A₀·(1,0053255 − 0,00122622·A₀)^1,6 med A₀ = α(f₀)·R, ger en dämpning
    som växer med A₀ upp till A₀ = 1,0053255/(2,6·0,00122622) ≈ 315,3 dB (dämpning 146,2 dB)
    och sedan *avtar* mot 0 vid A₀ ≈ 820 dB, där formeln upphör att vara definierad. Metoden
    anger inget giltighetsområde (AV 1849/00 avsnitt 3 visar formeln upp till A₀ = 100 dB).
    För en lång rak väg ligger de yttersta källpunkterna i det avtagande området: på 100 m
    från vägen har källpunkten i 89° (5,5 km bort) A₀ = 796 dB vid 10 kHz, och formeln ger då
    2,9 dB dämpning, mindre än för källpunkten mitt framför mottagaren (13,6 dB).

    Här hålls dämpningen kvar på formelns största värde för A₀ över maximum, så att den
    aldrig avtar med avståndet. För A₀ under maximum är resultatet identiskt med
    `luftabsorption.luftabsorption_db`. Valet är inte textens (texten täcker inte fallet); det
    är avgjort av testfallen, se `docs/buller/logg/P-S1.md`. Att i stället använda
    rentonsdämpningen −A₀ över maximum ger samma testfallsresultat inom 0,001 dB.
    """
    if vagstracka_m < 0.0:
        raise BullerFel("Vägsträckan får inte vara negativ.")
    resultat = np.empty(frekvenser_hz.shape[0], dtype=np.float64)
    for i, f in enumerate(frekvenser_hz):
        a0 = (
            rentonsdampning_db_per_m(
                float(f), lufttemperatur_c, relativ_fuktighet_procent, lufttryck_kpa
            )
            * vagstracka_m
        )
        a0 = min(a0, STORSTA_A0_BANDFORMEL_DB)
        resultat[i] = -a0 * (_BANDFORMEL_A - _BANDFORMEL_B * a0) ** _BANDFORMEL_EXPONENT
    return resultat


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
    uppat: Uppatmetod = "exakt",
    ct2_form: Ct2Form = "kvadrat",
    fresnelzon: Fresnelzonsval = "klippt",
    skuggzonsvikter: Skuggzonsvikter = "ovre_band",
    skuggzon: Skuggzonslasning | None = None,
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
                skuggzon,
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
            skuggzon,
        ),
        "2",
    )


class Nord2000Karna:
    """Punkt-till-punkt-kärna enligt Nord2000 (uppfyller `buller.typer.P2PKarna`).

    `nord2000_konform` är falsk: kärnan täcker bara en del av metoden och har inte passerat
    testsviten. Fall utanför täckningen ger `BullerFel`, aldrig ett närmevärde.

    Val där metodtexten medger flera läsningar kan ställas in. Förvalen är de läsningar som
    texten stöder bäst eller som de officiella testfallen pekar ut; de senare är märkta
    "avgjord av testfall" i `docs/buller/logg/P-S1.md`, med siffror för alla läsningar (regeln
    står sist i `docs/buller/tolkningar.md`). Se också `P-R1.md`, `P-7.md` och `P-R2.md`.

    * `uppat`: strålar vid uppåtbrytning, `"exakt"` (den verkliga uppåtkrökta bågen; förval,
      avgjort av testfall) eller `"bokstavlig"` (|ξ′|, AV 1106/07 avsnitt 5.5.4).
    * `ct2_form`: temperaturtermen i F_c (ekv. 113), `"kvadrat"` (C_T²/T²) eller `"tryckt"`
      (C_T²/T).
    * `fresnelzon`: Fresnelzonen i delmodell 2, `"klippt"` (begränsad till sträckan
      källa–mottagare, avsnitt 5.8; förval, avgjort av testfall) eller `"oklippt"`
      (ekv. 349–351 som tryckta).
    * `skuggzonsvikter`: delmodell 2 i skuggzon, `"ovre_band"` (w_L till och med 6,3 kHz, w_H
      i 8 och 10 kHz; förval, avgjort av testfall), `"lag"` (w_L i alla band) eller `"hog"`
      (w_H i alla band); texten avgör inte, se `delmodell2.markeffekt_blandad_mark_db`.
    * `skuggzon`: läsningen av skuggzonsavsnitten (`refraktion_mark.Skuggzonslasning`), bland
      annat om ΔL_SZ läggs till i alla band när den frekvensoberoende profilen ger skuggzon
      (förval, avgjort av testfall).
    * `luftabsorption`: `"monoton"` (förval, avgjort av testfall) håller kvar den största
      dämpning ekv. (287) kan ge när A₀ är större än där formeln har sitt maximum;
      `"tryckt"` räknar formeln som den står. Se `luftabsorption_monoton_db`.
    * `delmodell8`: flera markreflexer vid stark nedåtbrytning (avsnitt 5.17). När N i
      ekv. (279) är större än 4 läggs ΔL₈ energimässigt till ΔL_t (ekv. 332): `"hog"` (förval)
      är högfrekvenslösningen ΔL_multi,hf som metoden rekommenderar för normalt bruk, `"lag"`
      lågfrekvenslösningen ΔL_multi,lf. Med `"utelamna"` räknas ΔL_t utan ΔL₈; det är avsett
      för utredning och resultatet är då inte metodens.
    """

    namn: str = "nord2000-referens"
    version: str = "0.3.0"
    nord2000_konform: bool = False

    def __init__(
        self,
        uppat: Uppatmetod = "exakt",
        ct2_form: Ct2Form = "kvadrat",
        delmodell8: Delmodell8Val = "hog",
        fresnelzon: Fresnelzonsval = "klippt",
        skuggzonsvikter: Skuggzonsvikter = "ovre_band",
        skuggzon: Skuggzonslasning | None = None,
        luftabsorption: Luftabsorptionsval = "monoton",
    ) -> None:
        if luftabsorption not in ("monoton", "tryckt"):
            raise BullerFel(
                f"Okänt val för luftabsorptionen: {luftabsorption!r} (monoton eller tryckt)."
            )
        self.luftabsorption: Luftabsorptionsval = luftabsorption
        self.skuggzon: Skuggzonslasning = Skuggzonslasning() if skuggzon is None else skuggzon
        if skuggzonsvikter not in ("hog", "lag", "ovre_band"):
            raise BullerFel(
                f"Okänt val för vikterna i skuggzon: {skuggzonsvikter!r} "
                "(hog, lag eller ovre_band)."
            )
        self.skuggzonsvikter: Skuggzonsvikter = skuggzonsvikter
        if fresnelzon not in ("oklippt", "klippt"):
            raise BullerFel(f"Okänt val för Fresnelzonen: {fresnelzon!r} (oklippt eller klippt).")
        self.fresnelzon: Fresnelzonsval = fresnelzon
        if delmodell8 not in ("hog", "lag", "utelamna"):
            raise BullerFel(f"Okänt val för delmodell 8: {delmodell8!r} (hog, lag eller utelamna).")
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
        ΔL_d och ΔL_a räknas med R_SR (ekv. 8), även med refraktion. ΔL₈ (delmodell 8) läggs
        till ΔL_t med ⊕ när N > 4 (ekv. 332).
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
            self.skuggzon,
        )
        # Delmodell 8, ekv. (332): ΔL_t = (…) ⊕ ΔL₈, ytterst och per band. A och B är
        # medelvärdena (inte A₊, B₊). För N ≤ 4 är ΔL₈ = −∞ dB och ändrar ingenting.
        antal_stralar = 0.0
        flera_markreflexer_db: Flyttal | None = None
        if self.delmodell8 != "utelamna" and atmosfar.har_refraktion:
            dl8 = delmodell8.flera_markreflexer(frekvenser, profil, atmosfar, self.delmodell8)
            antal_stralar = dl8.antal_stralar
            if dl8.bidrar:
                flera_markreflexer_db = dl8.delta_l_db
                terrang = delmodell8.energisumma_db(terrang, dl8.delta_l_db)
        return Nord2000Delresultat(
            frekvenser_hz=frekvenser,
            spridning_db=sfarisk_spridning_db(geo.r_sr_m),
            luftabsorption_db=(
                luftabsorption_monoton_db if self.luftabsorption == "monoton" else luftabsorption_db
            )(
                frekvenser,
                geo.r_sr_m,
                atmosfar.temperatur_luft_c,
                atmosfar.relativ_fuktighet_procent,
                atmosfar.lufttryck_kpa,
            ),
            terrang_db=terrang,
            delmodell=delmodell,
            antal_stralar=antal_stralar,
            flera_markreflexer_db=flera_markreflexer_db,
        )

    def overforing_db(self, profil: Terrangprofil, vader: Vader) -> Flyttal:
        """L_p − L_W per tersband, form (ANTAL_BAND,), AV 1106/07 ekv. (329)."""
        return self.berakna(profil, vader).overforing_db
