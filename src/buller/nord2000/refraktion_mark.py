"""Refraktion i plan mark-modellen: frekvensberoende profil, strålhöjd och skuggzonsavskärmning.

Källor (varje formel avläst från sidbild):

* DELTA AV 1106/07 (rev. 2014), avsnitt 5.5.3 (CalcEqSSPGround, ekv. 23–28), 5.23.9
  (HeightOfCircularRay, ekv. 355–368) och 5.23.16 (ShadowZoneShielding, ekv. 384–387).
* DELTA AV 1106/07, avsnitt 5.5.7 (ekv. 54) och 5.17 (ekv. 279 och 284): antalet strålar N,
  som avgör om delmodell 8 (flera markreflexer) bidrar. Delmodell 8 själv ingår inte här.
* DELTA AV 1851/00 (rev. 2006), avsnitt 4 (ekv. 39–42), för det AV 1106/07 lämnar oklart i
  skuggzonen: var kilens topp ligger (ekv. 41) och att nivån är 20·lg av trycket (ekv. 42).

Strålarna själva finns i `stralar_krokta.py` och väder → profil i `refraktion.py` (P-R1);
kildiffraktionen i `kildiffraktion.py` (P-K1). Val och misstänkta tryckfel står i
`docs/buller/logg/P-R2.md` under "Tolkningar".
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from buller.nord2000.fasdiff import fasdifferensfrekvens_hz
from buller.nord2000.kildiffraktion import Kil, kiltryck_icke_reflekterande
from buller.nord2000.refraktion import (
    HALV_HOJDSKILLNAD_LIKA_HOJDER_M,
    Atmosfar,
    EkvivalentProfil,
    ekvivalent_profil_ur_gradient,
    ljudhastighetsgradient_per_s,
    ljudhastighetsprofil_m_s,
    medelljudhastighet_m_s,
    profilhojder_m,
)
from buller.typer import BullerFel, Flyttal

# AV 1106/07 avsnitt 5.5.3: mjuk mark har flödesresistivitet under 10 000 000 Pa·s/m².
MJUK_MARK_GRANS_KNSM4 = 10_000.0
# AV 1106/07 avsnitt 5.5.3: i ekv. (24)–(25) används högst d = 400 m och minst h = 0,5 m.
STORSTA_AVSTAND_FASDIFF_M = 400.0
MINSTA_HOJD_FASDIFF_M = 0.5
# AV 1106/07 avsnitt 5.5.3: Δc₁₀ är skillnaden i ljudhastighet mellan 10 m höjd och marken.
HOJD_DELTA_C10_M = 10.0
# AV 1106/07 ekv. (385): ξ_SZ växer linjärt i lg f från 0 vid 20 Hz till ξ vid 2000 Hz.
SKUGGZON_NEDRE_FREKVENS_HZ = 20.0
SKUGGZON_OVRE_FREKVENS_HZ = 2000.0


@dataclass(frozen=True)
class ProfilerMark:
    """Utdata från CalcEqSSPGround, AV 1106/07 ekv. (28).

    `oberoende` är (ξ, c₀, c̄) ur CalcEqSSP (ekv. 21) och `per_frekvens` är (ξ(f), c₀(f)) för
    markeffekten. `f_l_hz` och `f_h_hz` är `None` när profilen inte beror av frekvensen (hård
    mark eller ξ = 0).
    """

    oberoende: EkvivalentProfil
    per_frekvens: tuple[EkvivalentProfil, ...]
    f_l_hz: float | None
    f_h_hz: float | None


def overgangsfrekvenser_refraktion_hz(
    frekvenser_hz: Flyttal,
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    impedanser: Sequence[complex],
    ljudhastighet_mark_m_s: float,
    delta_c10_m_s: float,
) -> tuple[float, float]:
    """f_L och f_H för den frekvensberoende profilen, AV 1106/07 ekv. (24)–(27).

    f_π och f_2π är frekvenserna där fasskillnaden mellan direkt och reflekterat ljud är π
    och 2π (PhaseDiffFreq, avsnitt 5.23.14), räknade med d ≤ 400 m och h_S, h_R ≥ 0,5 m.
    f_L = f_π för Δc₁₀ ≤ 1, f_π·(43 − 3·Δc₁₀)/40 för 1 < Δc₁₀ < 5 och 0,7·f_π för Δc₁₀ ≥ 5
    (ekv. 26); f_H = max(√(f_L·f_2π), 1,25·f_L) (ekv. 27).
    """
    d = min(avstand_m, STORSTA_AVSTAND_FASDIFF_M)
    h_s = max(kallhojd_m, MINSTA_HOJD_FASDIFF_M)
    h_r = max(mottagarhojd_m, MINSTA_HOJD_FASDIFF_M)
    f_pi = fasdifferensfrekvens_hz(
        frekvenser_hz, d, h_s, h_r, impedanser, ljudhastighet_mark_m_s, math.pi
    )  # ekv. (24)
    f_2pi = fasdifferensfrekvens_hz(
        frekvenser_hz, d, h_s, h_r, impedanser, ljudhastighet_mark_m_s, 2.0 * math.pi
    )  # ekv. (25)
    if delta_c10_m_s <= 1.0:
        f_l = f_pi
    elif delta_c10_m_s < 5.0:
        f_l = f_pi * (43.0 - 3.0 * delta_c10_m_s) / 40.0
    else:
        f_l = 0.7 * f_pi
    return f_l, max(math.sqrt(f_l * f_2pi), 1.25 * f_l)


def gradientfaktor(frekvens_hz: float, f_l_hz: float, f_h_hz: float) -> float:
    """Faktorn framför Δc/Δz i AV 1106/07 ekv. (23): 0 under f_L, 1 över f_H.

    Mellan f_L och f_H är den (lg f − lg f_L)/(lg f_H − lg f_L) (avsnitt 5.5.3).
    """
    if frekvens_hz <= f_l_hz:
        return 0.0
    if frekvens_hz >= f_h_hz:
        return 1.0
    return (math.log10(frekvens_hz) - math.log10(f_l_hz)) / (
        math.log10(f_h_hz) - math.log10(f_l_hz)
    )


def calc_eq_ssp_mark(
    frekvenser_hz: Flyttal,
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    flodesresistivitet_kNsm4: float,
    impedanser: Sequence[complex],
    atmosfar: Atmosfar,
) -> ProfilerMark:
    """Funktionen CalcEqSSPGround, AV 1106/07 avsnitt 5.5.3, ekv. (23)–(28).

    För hård mark (σ ≥ 10 000 kNs/m⁴) och när CalcEqSSP ger ξ = 0 är profilen densamma i alla
    band. För mjuk mark används Δc'/Δz = faktor·Δc/Δz (ekv. 23) i stället för Δc/Δz när ξ och
    c₀ bestäms enligt avsnitt 5.5.2 (ekv. 17 och 20, TC-101327 avsnitt 2.1, gränsen 10⁻⁶).
    c̄ är densamma i alla band. c₀ i ekv. (24)–(25) är c₀ i den frekvensoberoende profilen.

    `impedanser[i]` är markimpedansen vid `frekvenser_hz[i]`; `avstand_m` är d i ekv. (24)–(25).
    """
    antal = frekvenser_hz.shape[0]
    if len(impedanser) != antal:
        raise BullerFel("CalcEqSSPGround kräver en impedans per frekvens.")
    gradient = ljudhastighetsgradient_per_s(kallhojd_m, mottagarhojd_m, atmosfar)  # ekv. (18)
    h_s, h_r = profilhojder_m(kallhojd_m, mottagarhojd_m, atmosfar.z0_m)
    if h_s == h_r:  # samma regel som i CalcEqSSP (P-R1, tolkning 10)
        h_s -= HALV_HOJDSKILLNAD_LIKA_HOJDER_M
        h_r += HALV_HOJDSKILLNAD_LIKA_HOJDER_M
    c_medel = medelljudhastighet_m_s(h_s, h_r, atmosfar)  # ekv. (19)
    oberoende = ekvivalent_profil_ur_gradient(
        gradient, c_medel, kallhojd_m, mottagarhojd_m, atmosfar
    )  # ekv. (21)
    if oberoende.xi_per_m == 0.0 or flodesresistivitet_kNsm4 >= MJUK_MARK_GRANS_KNSM4:
        return ProfilerMark(oberoende, (oberoende,) * antal, None, None)

    delta_c10 = ljudhastighetsprofil_m_s(HOJD_DELTA_C10_M, atmosfar) - atmosfar.c_m_s
    f_l, f_h = overgangsfrekvenser_refraktion_hz(
        frekvenser_hz,
        avstand_m,
        kallhojd_m,
        mottagarhojd_m,
        impedanser,
        oberoende.c0_m_s,
        delta_c10,
    )
    profiler: list[EkvivalentProfil] = []
    for f in frekvenser_hz:
        faktor = gradientfaktor(float(f), f_l, f_h)
        if faktor == 1.0:
            profiler.append(oberoende)
        else:
            profiler.append(
                ekvivalent_profil_ur_gradient(
                    faktor * gradient, c_medel, kallhojd_m, mottagarhojd_m, atmosfar
                )
            )
    return ProfilerMark(oberoende, tuple(profiler), f_l, f_h)


def hojd_cirkelstrale(
    avstand_m: float, hojd1_m: float, hojd2_m: float, xi_per_m: float, d0_m: float
) -> tuple[float, float, float]:
    """Funktionen HeightOfCircularRay, AV 1106/07 avsnitt 5.23.9, ekv. (355)–(368).

    Strålen går från (0, h₁) till (d, h₂) med krökning efter den relativa gradienten ξ.
    Returnerar (h₀, R₁, R₂): strålens höjd på avståndet d₀ från startpunkten och båglängderna
    från den punkten till start- och slutpunkten. Formlerna följs bokstavligt: ξ′ = |ξ|
    (ekv. 356), och för ξ < 0 speglas höjderna (ekv. 360–361, 367). För ξ < 0 kan h₀ bli
    negativ (strålen under markplanet); det är så funktionen används i ekv. (386).

    ξ = 0 täcks inte av ekv. (363)–(365) (division med ξ′); då returneras den räta linjen, som
    är formlernas gränsvärde.
    """
    if not avstand_m > 0.0:
        raise BullerFel("Det horisontella avståndet ska vara större än 0.")
    d = avstand_m
    if xi_per_m == 0.0:
        h0 = hojd1_m + (hojd2_m - hojd1_m) * d0_m / d
        return h0, math.hypot(d0_m, h0 - hojd1_m), math.hypot(d - d0_m, hojd2_m - h0)

    xi = abs(xi_per_m)  # ekv. (356)
    dh = abs(hojd1_m - hojd2_m)  # ekv. (357)
    if xi_per_m > 0.0:
        if hojd1_m > hojd2_m:
            h1, d0 = hojd2_m, d - d0_m  # ekv. (358)
        else:
            h1, d0 = hojd1_m, d0_m  # ekv. (359)
    elif hojd1_m >= hojd2_m:
        h1, d0 = -hojd1_m, d0_m  # ekv. (360)
    else:
        h1, d0 = -hojd2_m, d - d0_m  # ekv. (361)

    tan_psi = 0.5 * xi * d + dh * (2.0 + xi * dh) / (2.0 * d)  # ekv. (362)
    x0 = tan_psi / xi  # ekv. (363)
    z0 = h1 - 1.0 / xi  # ekv. (364)
    radie = math.sqrt(1.0 + tan_psi * tan_psi) / xi  # ekv. (365): 1/(ξ′·cos ψ_L)
    kvot = (d0 - x0) / radie
    if abs(kvot) > 1.0:
        raise BullerFel("HeightOfCircularRay: punkten ligger utanför strålens cirkel (ekv. 366).")
    theta = math.acos(kvot)  # ekv. (366)
    tecken = 1.0 if xi_per_m > 0.0 else -1.0
    h0 = tecken * (radie * math.sin(theta) + z0)  # ekv. (367)
    korda1 = math.hypot(d0_m, h0 - hojd1_m)
    korda2 = math.hypot(d - d0_m, hojd2_m - h0)
    if korda1 > 2.0 * radie or korda2 > 2.0 * radie:
        raise BullerFel("HeightOfCircularRay: kordan är längre än cirkelns diameter (ekv. 368).")
    return (
        h0,
        2.0 * radie * math.asin(korda1 / (2.0 * radie)),
        2.0 * radie * math.asin(korda2 / (2.0 * radie)),
    )  # ekv. (368)


def kilavstand_skuggzon_m(avstand_m: float, kallhojd_m: float, mottagarhojd_m: float) -> float:
    """Avståndet från källan till den ekvivalenta kilens topp, AV 1851/00 avsnitt 4, ekv. (41).

    d·√h_S/(√h_S + √h_R): ungefär där en stråle som stryker marken har sin lägsta punkt.
    AV 1106/07 avsnitt 5.23.16 betecknar samma sträcka d_SZ men hänvisar till avsnitt 5.5.4,
    där d_SZ är avståndet källa–mottagare vid skuggzonens gräns; se tolkningar i loggen.
    """
    if not avstand_m > 0.0 or not kallhojd_m > 0.0 or not mottagarhojd_m > 0.0:
        raise BullerFel("Skuggzonskilen kräver avstånd och höjder större än 0.")
    rot_s = math.sqrt(kallhojd_m)
    return avstand_m * rot_s / (rot_s + math.sqrt(mottagarhojd_m))


def gradient_skuggzon_per_m(frekvens_hz: float, xi_per_m: float) -> float:
    """ξ_SZ(f), AV 1106/07 avsnitt 5.23.16, ekv. (385).

    ξ·(lg f − lg 20)/(lg 2000 − lg 20) under 2000 Hz, annars ξ.
    """
    if frekvens_hz >= SKUGGZON_OVRE_FREKVENS_HZ:
        return xi_per_m
    return (
        xi_per_m
        * (math.log10(frekvens_hz) - math.log10(SKUGGZON_NEDRE_FREKVENS_HZ))
        / (math.log10(SKUGGZON_OVRE_FREKVENS_HZ) - math.log10(SKUGGZON_NEDRE_FREKVENS_HZ))
    )


def skuggzonshojd_m(
    frekvens_hz: float,
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    xi_per_m: float,
    kilavstand_m: float,
) -> float:
    """h_SZ(f): hur långt under marken strålen går vid kilens topp, AV 1106/07 ekv. (386).

    HeightOfCircularRay ger strålens höjd över marken; h_SZ är enligt figur 42 djupet under
    marken, alltså höjden med omvänt tecken. Går strålen över marken blir h_SZ negativ.
    """
    xi_sz = gradient_skuggzon_per_m(frekvens_hz, xi_per_m)
    h0, _, _ = hojd_cirkelstrale(avstand_m, kallhojd_m, mottagarhojd_m, xi_sz, kilavstand_m)
    return -h0


def skuggzonsavskarmning_db(
    frekvens_hz: float,
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    xi_per_m: float,
    c0_m_s: float,
    kilavstand_m: float,
) -> float:
    """Funktionen ShadowZoneShielding: ΔL_SZ(f), AV 1106/07 avsnitt 5.23.16, ekv. (384)–(387).

    Källa och mottagare läggs på ytorna av en kil med höjden h_SZ(f) och toppen på avståndet
    `kilavstand_m` från källan (figur 41): θ_S = β = π + arctan(h_SZ/d_SZ) + arctan(h_SZ/(d −
    d_SZ)), θ_R = 0, R_S och R_R längs kilytorna, τ = R/c₀. `xi_per_m` och `c0_m_s` är den
    frekvensoberoende profilen (avsnitt 5.10).

    Val (se loggen): kilen är icke-reflekterande (pwedge0, avsnitt 5.7.5; ekv. 387 saknar
    impedanser), nivån är 20·lg(2·R_SR·|p|) med R_SR = d (AV 1851/00 ekv. 42; ekv. 387 är tryckt
    med 10·lg), och ΔL_SZ = 0 när h_SZ ≤ 0 (strålen når inte ned till marken vid kilens topp;
    en kil med β < π är inte definierad).
    """
    if not 0.0 < kilavstand_m < avstand_m:
        raise BullerFel("Skuggzonskilens topp ska ligga mellan källa och mottagare.")
    if not c0_m_s > 0.0:
        raise BullerFel("Ljudhastigheten c0 ska vara större än 0.")
    h_sz = skuggzonshojd_m(
        frekvens_hz, avstand_m, kallhojd_m, mottagarhojd_m, xi_per_m, kilavstand_m
    )
    if h_sz <= 0.0:
        return 0.0
    d_r = avstand_m - kilavstand_m
    theta_s = math.pi + math.atan(h_sz / kilavstand_m) + math.atan(h_sz / d_r)
    r_s = math.hypot(h_sz, kilavstand_m)
    r_r = math.hypot(h_sz, d_r)
    ell = r_s + r_r
    kil = Kil(
        beta_rad=theta_s,
        theta_s_rad=theta_s,
        theta_r_rad=0.0,
        gangtid_s=ell / c0_m_s,
        gangtid_kalla_s=r_s / c0_m_s,
        gangtid_mottagare_s=r_r / c0_m_s,
        vag_m=ell,
        vag_kalla_m=r_s,
        vag_mottagare_m=r_r,
    )
    tryck = abs(kiltryck_icke_reflekterande(frekvens_hz, kil))
    return 20.0 * math.log10(2.0 * avstand_m * tryck)


def krokningsradie_m(avstand_m: float, atmosfar: Atmosfar) -> float:
    """R_A,B: strålens krökningsradie i en log-lin-profil, AV 1106/07 avsnitt 5.5.7, ekv. (54).

    R_A = sign(A)·(d/8)·√(2πC/|A|), R_B = sign(B)·√((C/|B|)² + (d/2)²) och
    1/R_A,B = 1/R_A + 1/R_B. Positiv radie är nedåtbrytning. Utan refraktion (A = B = 0) eller
    när de två delarna tar ut varandra returneras oändligheten (rät stråle).
    """
    if not avstand_m > 0.0:
        raise BullerFel("Det horisontella avståndet ska vara större än 0.")
    c = atmosfar.c_m_s
    a = atmosfar.a_m_s
    b = atmosfar.b_per_s
    krokning = 0.0
    if a != 0.0:
        krokning += math.copysign(
            1.0 / (avstand_m / 8.0 * math.sqrt(2.0 * math.pi * c / abs(a))), a
        )
    if b != 0.0:
        krokning += math.copysign(1.0 / math.hypot(c / abs(b), 0.5 * avstand_m), b)
    return math.inf if krokning == 0.0 else 1.0 / krokning


def antal_stralar_flera_markreflexer(
    avstand_m: float, storsta_hojd_m: float, atmosfar: Atmosfar
) -> float:
    """N: ungefärligt antal strålar vid flera markreflexer, AV 1106/07 avsnitt 5.17, ekv. (279).

    N = (4·d/h_max)·√(A′/(2πC)) med A′ = π·C·d²/(32·R_A,B²) (ekv. 284; för B = 0 är A′ = A).
    För N under 4 bidrar delmodell 8 inte. Delmodell 8 gäller nedåtbrytning; för R_A,B ≤ 0
    eller oändlig (uppåtbrytning eller ingen refraktion) returneras 0, eftersom ekv. (284)
    med R² annars skulle ge samma N för uppåt- som för nedåtbrytning (se loggen).
    """
    if not storsta_hojd_m > 0.0:
        raise BullerFel("h_max i ekv. (279) ska vara större än 0.")
    radie = krokningsradie_m(avstand_m, atmosfar)
    if radie <= 0.0 or math.isinf(radie):
        return 0.0
    c = atmosfar.c_m_s
    a_ekvivalent = math.pi * c * avstand_m**2 / (32.0 * radie**2)
    return 4.0 * avstand_m / storsta_hojd_m * math.sqrt(a_ekvivalent / (2.0 * math.pi * c))
