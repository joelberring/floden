"""Delmodell 1 i Nord2000: plan mark med en marktyp, med eller utan refraktion.

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.10, ekv. (115)–(123), med rättelsen i
TC-101327 (2019) avsnitt 2.6: 10·lg ska stå först i högerledet i ekv. (120) och (121).

Två räknevägar med samma formler:

* Homogen atmosfär utan fluktuationer (A = B = s_A = s_B = 0): räta strålar, som tidigare.
* Annars: frekvensberoende ekvivalent profil (CalcEqSSPGround, ekv. 115), krökta strålar
  för medelrefraktion och övre refraktion (ekv. 116–117), koherens med F_Δτ (ekv. 118) och
  skuggzonsgrenen (ekv. 121–122) när mottagaren ligger i skuggzon.

Val som texten lämnar öppna står i `docs/buller/logg/P-R2.md` under "Tolkningar".
"""

from __future__ import annotations

import cmath
import math
from typing import Literal

import numpy as np

from buller.nord2000.atmosfar import HomogenAtmosfar, som_atmosfar
from buller.nord2000.frekvenser import vagtal_per_m
from buller.nord2000.geometri import MINSTA_HOJD_M, stralar_plan_mark
from buller.nord2000.koherens import (
    koherens_bandmedelvarde,
    koherens_fluktuerande_refraktion,
    koherens_markrahet,
    koherens_turbulens,
    transversell_separation_m,
)
from buller.nord2000.mark import inkoherent_reflektion, markimpedans, sfarisk_reflektion
from buller.nord2000.refraktion import Atmosfar, EkvivalentProfil, ovre_atmosfar
from buller.nord2000.refraktion_mark import (
    Skuggzonslasning,
    calc_eq_ssp_mark,
    kilavstand_skuggzon_m,
    skuggzonsavskarmning_db,
)
from buller.nord2000.stralar_krokta import (
    SKUGGZONSMARGINAL,
    StralarKroktPlanMark,
    Uppatmetod,
    skuggzonsavstand_m,
    stralar_krokt_plan_mark,
)
from buller.typer import BullerFel, Flyttal

# Formen på temperaturtermen i F_c (ekv. 113): "kvadrat" är C_T²/T² (AV 1849/00 ekv. 141 och
# förvalet, se `koherens.koherens_turbulens`), "tryckt" är C_T²/T som AV 1106/07 är tryckt.
Ct2Form = Literal["kvadrat", "tryckt"]


def _ct2_for_koherens(ct2: float, medeltemperatur_c: float, ct2_form: str) -> float:
    """C_T² så som den ska ges till `koherens_turbulens` (som räknar C_T²/T²).

    Formen "tryckt" (C_T²/T) fås genom att ge C_T²·T, eftersom (C_T²·T)/T² = C_T²/T.
    """
    if ct2_form == "kvadrat":
        return ct2
    if ct2_form == "tryckt":
        return ct2 * (medeltemperatur_c + 273.15)
    raise BullerFel(f"Okänd form för C_T²-termen: {ct2_form!r} (kvadrat eller tryckt).")


def koherens_plan_mark(
    frekvens_hz: float,
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    rahet_m: float,
    atmosfar: HomogenAtmosfar | Atmosfar,
    koherens_spridningszon: float = 1.0,
    ct2_form: Ct2Form = "kvadrat",
) -> float:
    """Koherenskoefficienten F(f) för plan mark i homogen atmosfär, AV 1106/07 ekv. (118)–(119).

    I homogen atmosfär utan fluktuationer är Δτ₊ = Δτ, så F_Δτ = 1 (ekv. 112); t_mean = t₀ och
    c̄ = c₀ = C. `koherens_spridningszon` är F_s (avsnitt 5.19), 1 utan spridningszon. Med
    refraktion eller fluktuationer gäller `koherens_med_refraktion`; då ges `BullerFel` här.
    """
    atm = som_atmosfar(atmosfar)
    if atm.har_refraktion or atm.har_fluktuerande_refraktion:
        raise BullerFel(
            "koherens_plan_mark gäller homogen atmosfär; med refraktion beror koherensen på "
            "strålarna i varje band (koherens_med_refraktion)."
        )
    c = atm.c_m_s
    stralar = stralar_plan_mark(avstand_m, kallhojd_m, mottagarhojd_m, c)
    dtau = stralar.gangtidsskillnad_s
    return (
        koherens_bandmedelvarde(frekvens_hz, dtau)
        * koherens_fluktuerande_refraktion(frekvens_hz, dtau, dtau)
        * koherens_turbulens(
            frekvens_hz,
            atm.cv2,
            _ct2_for_koherens(atm.ct2, atm.temperatur_luft_c, ct2_form),
            atm.temperatur_luft_c,
            c,
            transversell_separation_m(kallhojd_m, mottagarhojd_m),
            avstand_m,
        )
        * koherens_markrahet(vagtal_per_m(frekvens_hz, c), stralar.strykvinkel_rad, rahet_m)
        * koherens_spridningszon
    )


def koherens_med_refraktion(
    frekvens_hz: float,
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    rahet_m: float,
    atmosfar: Atmosfar,
    gangtidsskillnad_s: float,
    gangtidsskillnad_ovre_s: float,
    medelljudhastighet_m_s: float,
    strykvinkel_rad: float,
    ljudhastighet_mark_m_s: float,
    koherens_spridningszon: float = 1.0,
    ct2_form: Ct2Form = "kvadrat",
) -> float:
    """Koherenskoefficienten F(f) med refraktion, AV 1106/07 avsnitt 5.10, ekv. (118)–(119).

    F = F_f(f, Δτ)·F_Δτ(f, Δτ, Δτ₊)·F_c(f, C_v², C_T², t_mean, c̄, ρ, d)·F_r(k₀, ψ_G, r)·F_s.
    Δτ och Δτ₊ hör till medelrefraktion (A, B) och övre refraktion (A₊, B₊, ekv. 10), c̄ kommer
    från DirectRay (ekv. 116), ψ_G från ReflectedRay (0 i skuggzonen) och k₀ = 2πf/c₀ med c₀
    ur CalcEqSSP (ekv. 12–14). t_mean sätts till lufttemperaturen (se loggen).
    """
    return (
        koherens_bandmedelvarde(frekvens_hz, gangtidsskillnad_s)
        * koherens_fluktuerande_refraktion(frekvens_hz, gangtidsskillnad_s, gangtidsskillnad_ovre_s)
        * koherens_turbulens(
            frekvens_hz,
            atmosfar.cv2,
            _ct2_for_koherens(atmosfar.ct2, atmosfar.temperatur_luft_c, ct2_form),
            atmosfar.temperatur_luft_c,
            medelljudhastighet_m_s,
            transversell_separation_m(kallhojd_m, mottagarhojd_m),
            avstand_m,
        )
        * koherens_markrahet(
            vagtal_per_m(frekvens_hz, ljudhastighet_mark_m_s), strykvinkel_rad, rahet_m
        )
        * koherens_spridningszon
    )


def markeffekt_plan_mark_db(
    frekvenser_hz: Flyttal,
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    flodesresistivitet_kNsm4: float,
    rahet_m: float,
    atmosfar: HomogenAtmosfar | Atmosfar,
    uppat: Uppatmetod = "exakt",
    ct2_form: Ct2Form = "kvadrat",
    skuggzon: Skuggzonslasning | None = None,
) -> Flyttal:
    """Funktionen SubModel1: markeffekten ΔL_flat per tersband, AV 1106/07 ekv. (115)–(123).

    Utanför skuggzon (ekv. 120, TC-101327 avsnitt 2.6):
    ΔL = 10·lg(|1 + F·(R₁/R₂)·exp(j·2πf·Δτ)·Q|² + (1 − F²)·((R₁/R₂)·ℜ_i)²), där Q är den
    sfäriska reflektionskoefficienten (ekv. 58) och ℜ_i den inkoherenta (ekv. 75). I skuggzon
    gäller ekv. (121)–(122), se `_markeffekt_med_refraktion_db`.

    `avstand_m` mäts längs marken och höjderna vinkelrätt mot den; höjder under 0,01 m
    ersätts med 0,01 m (avsnitt 5.4.1). `uppat` väljer hur strålar räknas vid uppåtbrytning
    (`stralar_krokta`; förval `"exakt"`, avgjort av testfall, se `docs/buller/logg/P-S1.md`),
    `ct2_form` formen på temperaturtermen i F_c och `skuggzon` hur
    skuggzonsavsnitten läses (`refraktion_mark.Skuggzonslasning`; `None` ger förvalen).
    """
    if avstand_m <= 0.0:
        raise BullerFel("Avståndet längs marken ska vara större än 0.")
    if rahet_m < 0.0:
        raise BullerFel("Markens råhet får inte vara negativ.")
    atm = som_atmosfar(atmosfar)
    h_s = max(kallhojd_m, MINSTA_HOJD_M)
    h_r = max(mottagarhojd_m, MINSTA_HOJD_M)
    if atm.har_refraktion or atm.har_fluktuerande_refraktion:
        return _markeffekt_med_refraktion_db(
            frekvenser_hz,
            avstand_m,
            h_s,
            h_r,
            flodesresistivitet_kNsm4,
            rahet_m,
            atm,
            uppat,
            ct2_form,
            Skuggzonslasning() if skuggzon is None else skuggzon,
        )
    stralar = stralar_plan_mark(avstand_m, h_s, h_r, atm.c_m_s)
    kvot = stralar.r1_m / stralar.r2_m
    resultat = np.empty(frekvenser_hz.shape[0], dtype=np.float64)
    for i, f_np in enumerate(frekvenser_hz):
        f = float(f_np)
        z = markimpedans(f, flodesresistivitet_kNsm4)
        q = sfarisk_reflektion(f, stralar.tau2_s, stralar.strykvinkel_rad, z)
        koherens = koherens_plan_mark(f, avstand_m, h_s, h_r, rahet_m, atm, ct2_form=ct2_form)
        fas = cmath.exp(1j * 2.0 * math.pi * f * stralar.gangtidsskillnad_s)
        koherent = abs(1.0 + koherens * kvot * fas * q) ** 2
        inkoherent = (1.0 - koherens**2) * (kvot * inkoherent_reflektion(z)) ** 2
        resultat[i] = 10.0 * math.log10(koherent + inkoherent)
    return resultat


def _stralar(
    avstand_m: float,
    h_s: float,
    h_r: float,
    profil: EkvivalentProfil,
    atm: Atmosfar,
    uppat: Uppatmetod,
    minne: dict[tuple[float, float], StralarKroktPlanMark],
) -> StralarKroktPlanMark:
    """DirectRay, ReflectedRay och TravelTimeDiff (ekv. 116–117) för en profil, med minne.

    Många band delar profil (alla över f_H och alla under f_L), så strålarna sparas per (ξ, c₀).
    """
    nyckel = (profil.xi_per_m, profil.c0_m_s)
    if nyckel not in minne:
        minne[nyckel] = stralar_krokt_plan_mark(
            avstand_m, h_s, h_r, profil.xi_per_m, profil.c0_m_s, atm.c_m_s, uppat
        )
    return minne[nyckel]


def _i_skuggzon(
    avstand_m: float, h_s: float, h_r: float, xi_per_m: float, uppat: Uppatmetod
) -> bool:
    """ξ < 0 och d > 0,95·d_SZ (AV 1106/07 avsnitt 5.10 och 5.5.4)."""
    if xi_per_m >= 0.0:
        return False
    d_sz = skuggzonsavstand_m(h_s, h_r, xi_per_m, uppat)
    return avstand_m > SKUGGZONSMARGINAL * d_sz


def _markeffekt_med_refraktion_db(
    frekvenser_hz: Flyttal,
    avstand_m: float,
    h_s: float,
    h_r: float,
    flodesresistivitet_kNsm4: float,
    rahet_m: float,
    atm: Atmosfar,
    uppat: Uppatmetod,
    ct2_form: Ct2Form,
    skuggzon: Skuggzonslasning,
) -> Flyttal:
    """Delmodell 1 med refraktion eller fluktuerande refraktion, AV 1106/07 ekv. (115)–(122).

    Per band: profilen (ξ(f), c₀(f)) och (ξ₊(f), c₀₊(f)) ur CalcEqSSPGround (ekv. 115),
    strålarna och Δτ, Δτ₊ (ekv. 116–117), F (ekv. 118) och sedan

    * utanför skuggzon ekv. (120),
    * i skuggzon ekv. (121):
      ΔL = 10·lg(|1 + F·Q(f, τ₁, 0, Z)|² + (1 − F²)·ℜ_i²) + ΔL_SZ(f), med ΔL_SZ ur
      ShadowZoneShielding (ekv. 122) räknad med den frekvensoberoende profilen.

    Skuggzon är ξ < 0 och d > 0,95·d_SZ. `skuggzon.avgorande` väljer med vilken profil
    villkoret prövas: bandets ξ(f) för markeffektens form och den frekvensoberoende profilen
    för ΔL_SZ (`"delat"`, förval), bandets ξ(f) för allt (`"per_band"`), eller den
    frekvensoberoende profilen för allt (`"oberoende"`; då gäller ekv. 121 med ψ_G = 0 och
    Δτ = 0 i alla band, också dem där ξ(f) = 0, och samma sak görs för den övre profilen).
    I ett band som självt ligger i skuggzon är Δτ = 0 (avsnitt 5.5.6) och ψ_G = 0.

    Med `"delat"` är alltså ΔL(f) = [ekv. 120, eller första termen i ekv. 121 när bandet
    självt ligger i skuggzon] + ΔL_SZ(f), där ΔL_SZ läggs till i alla band när den
    frekvensoberoende profilen ger skuggzon. ΔL_SZ tonas ut mot låga frekvenser av ξ_SZ(f)
    (ekv. 385) och är 0 när strålen med ξ_SZ(f) inte når ned till marken.

    Övriga val (se `Skuggzonslasning` och loggen): Q i ekv. (121) räknas med gångtiden τ₁
    längs den direkta strålen och ψ_G = 0 (AV 1851/00 ekv. 39; ekv. 121 är tryckt med
    Q(f, 0, 0, Z)); negativ Δτ, som kan uppstå i läget `bokstavlig`, sätts till 0
    (avsnitt 5.9.2 anger Δτ ≥ 0); kilens topp i ShadowZoneShielding läggs enligt AV 1851/00
    ekv. (41). Med kilens topp på d_SZ enligt ekv. (43) är ΔL_SZ = 0 så länge d ≤ d_SZ
    (strålen når då inte marken, och kilen i ekv. 387 är inte definierad).
    """
    impedanser = [markimpedans(float(f), flodesresistivitet_kNsm4) for f in frekvenser_hz]
    profiler = calc_eq_ssp_mark(
        frekvenser_hz, avstand_m, h_s, h_r, flodesresistivitet_kNsm4, impedanser, atm
    )
    fluktuerar = atm.har_fluktuerande_refraktion
    profiler_ovre = (
        calc_eq_ssp_mark(
            frekvenser_hz,
            avstand_m,
            h_s,
            h_r,
            flodesresistivitet_kNsm4,
            impedanser,
            ovre_atmosfar(atm),
        )
        if fluktuerar
        else profiler
    )
    c0_mark = profiler.oberoende.c0_m_s
    xi_oberoende = profiler.oberoende.xi_per_m
    en_gang = skuggzon.avgorande == "oberoende"
    skugga_oberoende = skuggzon.avgorande != "per_band" and _i_skuggzon(
        avstand_m, h_s, h_r, xi_oberoende, uppat
    )
    skugga_alla = en_gang and skugga_oberoende
    skugga_alla_ovre = en_gang and _i_skuggzon(
        avstand_m, h_s, h_r, profiler_ovre.oberoende.xi_per_m, uppat
    )
    minne: dict[tuple[float, float], StralarKroktPlanMark] = {}
    resultat = np.empty(frekvenser_hz.shape[0], dtype=np.float64)
    for i, f_np in enumerate(frekvenser_hz):
        f = float(f_np)
        z = impedanser[i]
        stralar = _stralar(avstand_m, h_s, h_r, profiler.per_frekvens[i], atm, uppat, minne)
        reflekterad = None if skugga_alla else stralar.reflekterad
        dtau = 0.0 if reflekterad is None else max(stralar.gangtidsskillnad_s, 0.0)
        if fluktuerar:
            ovre = _stralar(avstand_m, h_s, h_r, profiler_ovre.per_frekvens[i], atm, uppat, minne)
            dtau_ovre = (
                0.0
                if skugga_alla_ovre or ovre.reflekterad is None
                else max(ovre.gangtidsskillnad_s, 0.0)
            )
        else:
            dtau_ovre = dtau
        koherens = koherens_med_refraktion(
            f,
            avstand_m,
            h_s,
            h_r,
            rahet_m,
            atm,
            dtau,
            dtau_ovre,
            stralar.direkt.medelljudhastighet_m_s,
            0.0 if reflekterad is None else reflekterad.strykvinkel_rad,
            c0_mark,
            ct2_form=ct2_form,
        )
        r_i = inkoherent_reflektion(z)
        if reflekterad is None:  # markeffekten i skuggzon, första termen i ekv. (121)
            gangtid = stralar.direkt.gangtid_s if skuggzon.q_gangtid == "direkt" else 0.0
            q = sfarisk_reflektion(f, gangtid, 0.0, z)
            mark = abs(1.0 + koherens * q) ** 2 + (1.0 - koherens**2) * r_i**2
        else:  # ekv. (120)
            kvot = stralar.direkt.vagstracka_m / reflekterad.vagstracka_m
            q = sfarisk_reflektion(f, reflekterad.gangtid_s, reflekterad.strykvinkel_rad, z)
            fas = cmath.exp(1j * 2.0 * math.pi * f * dtau)
            mark = (
                abs(1.0 + koherens * kvot * fas * q) ** 2 + (1.0 - koherens**2) * (kvot * r_i) ** 2
            )
        resultat[i] = 10.0 * math.log10(mark)
        if reflekterad is None or skugga_oberoende:  # ΔL_SZ, ekv. (122)
            if skuggzon.kiltopp == "av1851":
                kiltopp: float | None = kilavstand_skuggzon_m(avstand_m, h_s, h_r)
            else:
                d_sz = (
                    stralar.direkt.skuggzonsavstand_m
                    if skuggzon.avgorande == "per_band"
                    else skuggzonsavstand_m(h_s, h_r, xi_oberoende, uppat)
                )
                kiltopp = d_sz if d_sz < avstand_m else None
            if kiltopp is not None:
                resultat[i] += skuggzonsavskarmning_db(
                    f, avstand_m, h_s, h_r, xi_oberoende, c0_mark, kiltopp, skuggzon.niva
                )
    return resultat
