"""Referenskärnan för Nord2000:s spridningsmodell, punkt till punkt (ofullständig).

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.2 (ekv. 1) och 5.22 (ekv. 329–332), med
ändringarna i TC-101327 (2019).

Täckning: ΔL_d, ΔL_a (delmodell 9) och ΔL_t sammansatt enligt ekv. (332) av delmodell 1–8
(`sammansattning.py`): plan mark med en eller flera marktyper, icke-plan terräng (delmodell 3
med r_flat), en skärm med en eller två kanter och två skärmar (delmodell 4–6 med r_scr1,
r_scr2, r_scr12), turbulensspridning bakom skärm (delmodell 7) och flera markreflexer
(delmodell 8), med eller utan refraktion och fluktuerande refraktion.

Inte täckt: spridningszoner (delmodell 10), reflexer (delmodell 11) och ändliga skärmar
(avsnitt 5.21.6). Fall där en delmodell inte går att räkna ger `BullerFel` med delmodellens
namn, t.ex. uppåtbrytning över icke-plan terräng där delmodell 3 kräver en kil som inte går
att bilda. Läget mot testfallen står i `docs/buller/logg/P-S2.md`; se också `B4.md`,
`P-R2.md`, `P-S1.md` och `docs/buller/B4-uppdelning.md`.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

import numpy as np

from buller.nord2000 import delmodell8, delmodell10
from buller.nord2000.delmodell1 import Ct2Form
from buller.nord2000.delmodell2 import Fresnelzonsval, Skuggzonsvikter
from buller.nord2000.delmodell3 import Fresnelzonsregel, Overgangshojd
from buller.nord2000.delmodell4 import Viktregel
from buller.nord2000.delmodell6 import Vinkelregel
from buller.nord2000.delmodell10 import CirkelStrale, RatStrale, Spridningszon, Stralbana
from buller.nord2000.frekvenser import mittfrekvenser_hz
from buller.nord2000.luftabsorption import luftabsorption_db, rentonsdampning_db_per_m
from buller.nord2000.refraktion import atmosfar_ur_vader, calc_eq_ssp
from buller.nord2000.refraktion_mark import Skuggzonslasning
from buller.nord2000.sammansattning import (
    Profilgeometri,
    Sammansattningsval,
    Terrangeffekt,
    forberakna_profilgeometri,
    markeffekt_plan_modell_db,
    marksegment_over_plan_mark,
    terrangeffekt,
)
from buller.nord2000.stralar_krokta import Uppatmetod
from buller.typer import BullerFel, Flyttal, Terrangprofil, Vader

__all__ = [
    "Nord2000Delresultat",
    "Nord2000Karna",
    "markeffekt_plan_modell_db",
    "marksegment_over_plan_mark",
]

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
    spridningszon_db: Flyttal | None = field(default=None)  # ΔL_s (delmodell 10); None utan skog
    # Alla led i ekv. (332) före ⊕ ΔL₈: r_scr1, r_scr2, r_scr12, r_flat och delmodellernas ΔL.
    terrangeffekt: Terrangeffekt | None = field(default=None, repr=False, compare=False)

    @property
    def overforing_db(self) -> Flyttal:
        """L_R − L_W = ΔL_d + ΔL_a + ΔL_t + ΔL_s (AV 1106/07 ekv. 329)."""
        summa: Flyttal = self.spridning_db + self.luftabsorption_db + self.terrang_db
        if self.spridningszon_db is not None:
            summa = summa + self.spridningszon_db
        return summa


@lru_cache(maxsize=64)
def _rentonsdampning_vektor(
    frekvenser: tuple[float, ...],
    lufttemperatur_c: float,
    relativ_fuktighet_procent: float,
    lufttryck_kpa: float,
) -> Flyttal:
    return np.array(
        [
            rentonsdampning_db_per_m(f, lufttemperatur_c, relativ_fuktighet_procent, lufttryck_kpa)
            for f in frekvenser
        ],
        dtype=np.float64,
    )


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
    alpha = _rentonsdampning_vektor(
        tuple(float(f) for f in frekvenser_hz),
        lufttemperatur_c,
        relativ_fuktighet_procent,
        lufttryck_kpa,
    )
    a0 = np.minimum(alpha * vagstracka_m, STORSTA_A0_BANDFORMEL_DB)
    # Skalar ** matchar luftabsorption_db bit-för-bit även på x86_64 (undviker 1 ULP SIMD-diff).
    ut: Flyttal = np.array(
        [-v * (_BANDFORMEL_A - _BANDFORMEL_B * v) ** _BANDFORMEL_EXPONENT for v in a0.tolist()],
        dtype=np.float64,
    )
    return ut


def sfarisk_spridning_db(avstand_m: float) -> float:
    """ΔL_d = −10·lg(4πR²), AV 1106/07 avsnitt 5.22, ekv. (330)."""
    return -10.0 * math.log10(4.0 * math.pi * avstand_m**2)


class Nord2000Karna:
    """Punkt-till-punkt-kärna enligt Nord2000 (uppfyller `buller.typer.P2PKarna`).

    `nord2000_konform` är falsk: kärnan täcker inte hela metoden och har inte passerat
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
    * `viktregel`: normeringen av Fresnelzonsvikterna i delmodell 4–6 (ekv. 187, 220, 269),
      `"av1849"` (förval) eller `"tryckt"`; se `delmodell4.normerade_vikter`.
    * `vinkelregel`: mot vilken punkt θ_1R och θ_2S mäts i delmodell 6, `"avsnitt_5_7_3"`
      (förval) eller `"ekv_227_228"`; se `delmodell6.Vinkelregel`.
    * `fresnelzon_delmodell3`: Fresnelzonen i delmodell 3, `"klippt"` (förval) eller
      `"oklippt"`; `overgangshojd`: höjderna i ekv. (140) för övergångssegment, `"prim"`
      (förval, som tryckt) eller `"bis"` (AV 1849/00). Inget av de fyra är avgjort av
      testfall; utfallet för alla läsningar står i `docs/buller/logg/P-S2.md`.
    * `delmodell8`: flera markreflexer vid stark nedåtbrytning (avsnitt 5.17). När N i
      ekv. (279) är större än 4 läggs ΔL₈ energimässigt till ΔL_t (ekv. 332): `"hog"` (förval)
      är högfrekvenslösningen ΔL_multi,hf som metoden rekommenderar för normalt bruk, `"lag"`
      lågfrekvenslösningen ΔL_multi,lf. Med `"utelamna"` räknas ΔL_t utan ΔL₈; det är avsett
      för utredning och resultatet är då inte metodens.
    """

    namn: str = "nord2000-referens"
    version: str = "0.4.0"
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
        viktregel: Viktregel = "av1849",
        vinkelregel: Vinkelregel = "avsnitt_5_7_3",
        fresnelzon_delmodell3: Fresnelzonsregel = "klippt",
        overgangshojd: Overgangshojd = "prim",
    ) -> None:
        if overgangshojd not in ("prim", "bis"):
            raise BullerFel(
                f"Okänt val för höjderna i ekv. (140): {overgangshojd!r} (prim eller bis)."
            )
        self.overgangshojd: Overgangshojd = overgangshojd
        if viktregel not in ("av1849", "tryckt"):
            raise BullerFel(f"Okänd viktregel: {viktregel!r} (av1849 eller tryckt).")
        self.viktregel: Viktregel = viktregel
        if vinkelregel not in ("avsnitt_5_7_3", "ekv_227_228"):
            raise BullerFel(
                f"Okänd vinkelregel: {vinkelregel!r} (avsnitt_5_7_3 eller ekv_227_228)."
            )
        self.vinkelregel: Vinkelregel = vinkelregel
        if fresnelzon_delmodell3 not in ("klippt", "oklippt"):
            raise BullerFel(
                f"Okänt val för Fresnelzonen i delmodell 3: {fresnelzon_delmodell3!r} "
                "(klippt eller oklippt)."
            )
        self.fresnelzon_delmodell3: Fresnelzonsregel = fresnelzon_delmodell3
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
        # Begränsad LRU-cache för väderoberoende profilgeometri (delas mellan väderklasser).
        self._geo_cache: OrderedDict[
            tuple[bytes, bytes, bytes, bytes, float, float], Profilgeometri
        ] = OrderedDict()
        self._geo_cache_max = 256

    def _profilgeometri(self, profil: Terrangprofil) -> Profilgeometri:
        nyckel = (
            np.ascontiguousarray(profil.x_m, dtype=np.float64).tobytes(),
            np.ascontiguousarray(profil.z_m, dtype=np.float64).tobytes(),
            np.ascontiguousarray(profil.flodesresistivitet_kNsm4, dtype=np.float64).tobytes(),
            np.ascontiguousarray(profil.rahet_m, dtype=np.float64).tobytes(),
            float(profil.kalla_hojd_m),
            float(profil.mottagare_hojd_m),
        )
        pg = self._geo_cache.get(nyckel)
        if pg is not None:
            self._geo_cache.move_to_end(nyckel)
            return pg
        pg = forberakna_profilgeometri(profil)
        self._geo_cache[nyckel] = pg
        if len(self._geo_cache) > self._geo_cache_max:
            self._geo_cache.popitem(last=False)
        return pg

    def val(self) -> Sammansattningsval:
        """Kärnans val i den form sammansättningen tar dem."""
        return Sammansattningsval(
            uppat=self.uppat,
            ct2_form=self.ct2_form,
            fresnelzon=self.fresnelzon,
            skuggzonsvikter=self.skuggzonsvikter,
            skuggzon=self.skuggzon,
            fresnelzon_delmodell3=self.fresnelzon_delmodell3,
            overgangshojd=self.overgangshojd,
            viktregel=self.viktregel,
            vinkelregel=self.vinkelregel,
        )

    def berakna(
        self,
        profil: Terrangprofil,
        vader: Vader,
        *,
        spridningszon: Spridningszon | None = None,
    ) -> Nord2000Delresultat:
        """Räknar alla utbredningseffekter i ekv. (329) för en profil och ett väder.

        Vädret översätts med `refraktion.atmosfar_ur_vader` (A, B, s_A, s_B). ΔL_t sätts
        samman av delmodell 1–7 enligt avsnitt 5.21–5.22 (`sammansattning.terrangeffekt`):
        terrängtolkningen med r_scr1, r_scr2 och r_scr12, övergången r_flat mellan plan mark
        och delmodell 3, och skärmdelmodellerna 4–6 med turbulensspridningen (delmodell 7).
        ΔL_d och ΔL_a räknas med R_SR (ekv. 8), även med refraktion och för skärmade banor
        (delmodell 4–6 är normerade mot fritt fält på avståndet R_SR, ekv. 163). ΔL₈
        (delmodell 8) läggs till ΔL_t med ⊕ när N > 4 (ekv. 332). Om `spridningszon` ges
        räknas delmodell 10 (AV 1106/07 avsnitt 5.19): F_s går in i terrängens koherens och
        ΔL_s läggs till ekv. (329).
        """
        atmosfar = atmosfar_ur_vader(vader)
        frekvenser = mittfrekvenser_hz()
        pg = self._profilgeometri(profil)
        geo = pg.geo
        koherens_s: Flyttal | None = None
        spridningszon_db: Flyttal | None = None
        if spridningszon is not None:
            strale: Stralbana
            if atmosfar.har_refraktion:
                eq_ssp = calc_eq_ssp(geo.h_s_m, geo.h_r_m, atmosfar)
                strale = CirkelStrale(geo.x_s_m, geo.z_s_m, geo.x_r_m, geo.z_r_m, eq_ssp.xi_per_m)
            else:
                strale = RatStrale(geo.x_s_m, geo.z_s_m, geo.x_r_m, geo.z_r_m)
            sz_res = delmodell10.spridningszon(frekvenser, strale, spridningszon, atmosfar.c_m_s)
            koherens_s = sz_res.koherens
            spridningszon_db = sz_res.dampning_db
        effekt = terrangeffekt(
            frekvenser,
            profil,
            atmosfar,
            self.val(),
            profilgeometri=pg,
            koherens_spridningszon=koherens_s,
        )
        terrang = effekt.delta_l_db
        delmodell = effekt.beteckning
        if spridningszon is not None:
            delmodell = f"{delmodell}+10"
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
            spridningszon_db=spridningszon_db,
            terrangeffekt=effekt,
        )

    def overforing_db(
        self,
        profil: Terrangprofil,
        vader: Vader,
        *,
        spridningszon: Spridningszon | None = None,
    ) -> Flyttal:
        """L_p − L_W per tersband, form (ANTAL_BAND,), AV 1106/07 ekv. (329)."""
        return self.berakna(profil, vader, spridningszon=spridningszon).overforing_db
