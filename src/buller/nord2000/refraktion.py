"""Refraktion i Nord2000: väder → ljudhastighetsprofil → ekvivalent linjär profil.

Källor (varje formel avläst från sidbild):

* DELTA AV 1106/07 (rev. 2014), avsnitt 5.3.2 (ekv. 2–3), 5.4.2 (ekv. 10), 5.5.2 (ekv. 15–21)
  och bilaga F (ekv. 401–403).
* FORCE TC-101327 (2019), avsnitt 2.1: ξ begränsas nedåt till −0,2.
* DELTA AV 1851/00 (rev. 2006), avsnitt 3, ekv. (26)–(27): A ur vindkomponenten och B ur
  temperaturgradienten. AV 1106/07 tar A och B som indata och saknar omräkningen.
* DELTA AV 1849/00 (rev. 2006), avsnitt 4.5.3.2: de övre värdena räknas för u + 1,7·σ_u och
  dt/dz + 1,7·σ_dt/dz. Tillsammans med ekv. (10) i AV 1106/07 ger det s_A och s_B.

Strålarna i den ekvivalenta profilen finns i `stralar_krokta.py`. Val och misstänkta tryckfel
står i `docs/buller/logg/P-R1.md` under "Tolkningar".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from buller.nord2000.frekvenser import ljudhastighet_m_s
from buller.typer import BullerFel, Vader

# AV 1106/07 avsnitt 5.3.2: under denna råhetslängd rekommenderas värdet självt.
MINSTA_RAHETSLANGD_M = 0.001
# AV 1106/07 ekv. (10) och AV 1849/00 avsnitt 4.5.3.2: övre värde = medel + 1,7 standardavvikelser.
FAKTOR_OVRE_VARDE = 1.7
# AV 1106/07 avsnitt 5.5.2: h_S och h_R får inte understiga h_min = 5·z₀ i ekv. (18)–(20).
FAKTOR_MINSTA_PROFILHOJD = 5.0
# AV 1106/07 avsnitt 5.5.2: vid h_S = h_R används h_S − 0,005 m och h_R + 0,005 m i ekv. (18).
HALV_HOJDSKILLNAD_LIKA_HOJDER_M = 0.005
# AV 1106/07 avsnitt 5.5.2: |ξ| under detta ger ξ = 0 och c̄ = c₀ = C.
MINSTA_RELATIV_GRADIENT_PER_M = 1.0e-6
# TC-101327 avsnitt 2.1: ξ begränsas nedåt till detta värde.
LAGSTA_RELATIV_GRADIENT_PER_M = -0.2


@dataclass(frozen=True)
class Atmosfar:
    """Väderparametrarna i AV 1106/07 avsnitt 5.3.2.

    Den effektiva ljudhastigheten är c(z) = A·ln(z/z₀ + 1) + B·z + C (ekv. 2) med
    C = Coft(t₀) (ekv. 3). s_A och s_B är standardavvikelserna för A och B.
    """

    a_m_s: float
    b_per_s: float
    s_a_m_s: float
    s_b_per_s: float
    z0_m: float
    temperatur_mark_c: float  # t₀
    temperatur_luft_c: float  # t_air, för luftabsorptionen
    relativ_fuktighet_procent: float
    lufttryck_kpa: float
    cv2: float
    ct2: float

    def __post_init__(self) -> None:
        if not self.z0_m > 0.0:
            raise BullerFel("Råhetslängden z0 ska vara större än 0.")
        if self.s_a_m_s < 0.0 or self.s_b_per_s < 0.0:
            raise BullerFel("Standardavvikelserna s_A och s_B får inte vara negativa.")
        if self.cv2 < 0.0 or self.ct2 < 0.0:
            raise BullerFel("Turbulensparametrarna Cv² och CT² får inte vara negativa.")

    @property
    def c_m_s(self) -> float:
        """C = Coft(t₀): ljudhastigheten vid marken, AV 1106/07 avsnitt 5.3.2, ekv. (3)."""
        return ljudhastighet_m_s(self.temperatur_mark_c)

    @property
    def z0_effektiv_m(self) -> float:
        """z₀ som används i strålgången: minst 0,001 m (AV 1106/07 avsnitt 5.3.2)."""
        return max(self.z0_m, MINSTA_RAHETSLANGD_M)

    @property
    def har_refraktion(self) -> bool:
        """Sant när A eller B är skild från 0."""
        return self.a_m_s != 0.0 or self.b_per_s != 0.0

    @property
    def har_fluktuerande_refraktion(self) -> bool:
        """Sant när s_A > 0 eller s_B > 0 (villkoret för F_Δτ i AV 1106/07 avsnitt 5.9)."""
        return self.s_a_m_s > 0.0 or self.s_b_per_s > 0.0


def profilkoefficient_a_m_s(vind_m_s: float, zu_m: float, z0_m: float) -> float:
    """A ur vindkomponenten u i utbredningsriktningen på höjden z_u, AV 1851/00 ekv. (26).

    A = u(z_u)/ln(z_u/z₀ + 1). Formeln gäller när den logaritmiska delen av profilen bara
    bestäms av vinden.
    """
    if not zu_m > 0.0 or not z0_m > 0.0:
        raise BullerFel("Vindens mäthöjd och råhetslängden ska vara större än 0.")
    return vind_m_s / math.log(zu_m / z0_m + 1.0)


def profilkoefficient_b_per_s(dt_dz_k_m: float, temperatur_c: float) -> float:
    """B ur en konstant temperaturgradient, AV 1851/00 ekv. (27).

    B = dt/dz · 10,025/√(t + 273,15), alltså derivatan av Coft (AV 1106/07 ekv. 335) gånger
    temperaturgradienten.
    """
    return dt_dz_k_m * 10.025 / math.sqrt(temperatur_c + 273.15)


def atmosfar_ur_vader(vader: Vader) -> Atmosfar:
    """Översätter `buller.typer.Vader` till metodens väderparametrar.

    A och B: AV 1851/00 ekv. (26)–(27). s_A och s_B: ingen av rapporterna ger en formel, men
    AV 1849/00 avsnitt 4.5.3.2 anger att de övre gångtiderna räknas för u₊ = u + 1,7·σ_u och
    dt/dz₊ = dt/dz + 1,7·σ_dt/dz. Insatt i ekv. (26)–(27), som är linjära i u och dt/dz, och
    jämfört med A₊ = A + 1,7·s_A, B₊ = B + 1,7·s_B (AV 1106/07 ekv. 10) följer

        s_A = σ_u/ln(z_u/z₀ + 1),    s_B = σ_dt/dz · 10,025/√(t + 273,15).

    t₀ = t_air = `temperatur_c` (samma val som i den homogena kärnan, se tolkningar). I
    ekv. (26) används samma z₀ som i ekv. (2), alltså minst 0,001 m.
    """
    if vader.std_vind_m_s < 0.0 or vader.std_dt_dz_k_m < 0.0:
        raise BullerFel("Standardavvikelserna för vind och temperaturgradient får inte vara < 0.")
    if not vader.z0_m > 0.0:
        raise BullerFel("Råhetslängden z0 ska vara större än 0.")
    z0 = max(vader.z0_m, MINSTA_RAHETSLANGD_M)
    return Atmosfar(
        a_m_s=profilkoefficient_a_m_s(vader.vind_m_s, vader.zu_m, z0),
        b_per_s=profilkoefficient_b_per_s(vader.dt_dz_k_m, vader.temperatur_c),
        s_a_m_s=profilkoefficient_a_m_s(vader.std_vind_m_s, vader.zu_m, z0),
        s_b_per_s=profilkoefficient_b_per_s(vader.std_dt_dz_k_m, vader.temperatur_c),
        z0_m=vader.z0_m,
        temperatur_mark_c=vader.temperatur_c,
        temperatur_luft_c=vader.temperatur_c,
        relativ_fuktighet_procent=vader.relativ_fuktighet_procent,
        lufttryck_kpa=vader.lufttryck_kpa,
        cv2=vader.cv2,
        ct2=vader.ct2,
    )


def ovre_atmosfar(atmosfar: Atmosfar) -> Atmosfar:
    """Atmosfären med A₊ = A + 1,7·s_A och B₊ = B + 1,7·s_B, AV 1106/07 avsnitt 5.4.2, ekv. (10).

    Används för Δτ₊ i koherensfaktorn F_Δτ (ekv. 112). s_A och s_B behålls oförändrade.
    """
    return replace(
        atmosfar,
        a_m_s=atmosfar.a_m_s + FAKTOR_OVRE_VARDE * atmosfar.s_a_m_s,
        b_per_s=atmosfar.b_per_s + FAKTOR_OVRE_VARDE * atmosfar.s_b_per_s,
    )


def ljudhastighetsprofil_m_s(hojd_m: float, atmosfar: Atmosfar) -> float:
    """c(z) = A·ln(z/z₀ + 1) + B·z + C, AV 1106/07 avsnitt 5.3.2, ekv. (2)."""
    if hojd_m < 0.0:
        raise BullerFel("Ljudhastighetsprofilen är definierad för höjder ≥ 0 över marken.")
    return (
        atmosfar.a_m_s * math.log(hojd_m / atmosfar.z0_effektiv_m + 1.0)
        + atmosfar.b_per_s * hojd_m
        + atmosfar.c_m_s
    )


def medelljudhastighet_m_s(hojd1_m: float, hojd2_m: float, atmosfar: Atmosfar) -> float:
    """c̄ mellan två höjder, AV 1106/07 ekv. (19) löst enligt bilaga F, ekv. (401)–(403).

    Den linjära termen räknas som B·(h₁ + h₂)/2, vilket är vad integralen i ekv. (402) ger. I
    ekv. (403) är den tryckt som B/2·(h_R − h_S); se tolkningar i paketets logg.
    """
    if hojd1_m == hojd2_m:
        raise BullerFel("Medelljudhastigheten enligt ekv. (19) kräver två olika höjder.")
    z0 = atmosfar.z0_effektiv_m
    u1 = hojd1_m / z0 + 1.0
    u2 = hojd2_m / z0 + 1.0
    logaritmisk = (
        atmosfar.a_m_s
        * z0
        / (hojd2_m - hojd1_m)
        * (u2 * (math.log(u2) - 1.0) - u1 * (math.log(u1) - 1.0))
    )
    return logaritmisk + 0.5 * atmosfar.b_per_s * (hojd1_m + hojd2_m) + atmosfar.c_m_s


@dataclass(frozen=True)
class EkvivalentProfil:
    """Ekvivalent linjär ljudhastighetsprofil c_e(z) = c₀·(1 + ξ·z), AV 1106/07 ekv. (16)."""

    xi_per_m: float  # ξ, relativ ljudhastighetsgradient, ekv. (17)
    c0_m_s: float  # c₀, ljudhastighet på höjden 0
    c_medel_m_s: float  # c̄, medelljudhastighet mellan h_S och h_R, ekv. (19)

    def ljudhastighet_m_s(self, hojd_m: float) -> float:
        """c_e(z) = c₀·(1 + ξ·z), AV 1106/07 ekv. (16)."""
        return self.c0_m_s * (1.0 + self.xi_per_m * hojd_m)


def profilhojder_m(kallhojd_m: float, mottagarhojd_m: float, z0_m: float) -> tuple[float, float]:
    """Höjderna som används i ekv. (18)–(20): minst h_min = 5·z₀ (AV 1106/07 avsnitt 5.5.2)."""
    if kallhojd_m < 0.0 or mottagarhojd_m < 0.0:
        raise BullerFel("Höjd över mark får inte vara negativ.")
    h_min = FAKTOR_MINSTA_PROFILHOJD * max(z0_m, MINSTA_RAHETSLANGD_M)
    return max(kallhojd_m, h_min), max(mottagarhojd_m, h_min)


def _gradienthojder_m(
    kallhojd_m: float, mottagarhojd_m: float, atmosfar: Atmosfar
) -> tuple[float, float]:
    """Höjderna i ekv. (18)–(19): som `profilhojder_m`, och ±0,005 m om de är lika."""
    h_s, h_r = profilhojder_m(kallhojd_m, mottagarhojd_m, atmosfar.z0_m)
    if h_s == h_r:
        return h_s - HALV_HOJDSKILLNAD_LIKA_HOJDER_M, h_r + HALV_HOJDSKILLNAD_LIKA_HOJDER_M
    return h_s, h_r


def ljudhastighetsgradient_per_s(
    kallhojd_m: float, mottagarhojd_m: float, atmosfar: Atmosfar
) -> float:
    """Δc/Δz: medelgradienten mellan h_S och h_R, AV 1106/07 avsnitt 5.5.2, ekv. (18).

    Höjderna begränsas först nedåt till 5·z₀. Är de sedan lika används h_S − 0,005 m och
    h_R + 0,005 m, som rapporten anger. Med ekv. (2) insatt i ekv. (18) tar C ut sig:

        Δc/Δz = A·ln((h_R + z₀)/(h_S + z₀))/(h_R − h_S) + B,

    och så räknas den, för att skillnaden c(h_R) − c(h_S) inte ska tappa siffror när höjderna
    ligger nära varandra.
    """
    h_s, h_r = _gradienthojder_m(kallhojd_m, mottagarhojd_m, atmosfar)
    logaritmskillnad = math.log1p((h_r - h_s) / (h_s + atmosfar.z0_effektiv_m))
    return atmosfar.a_m_s * logaritmskillnad / (h_r - h_s) + atmosfar.b_per_s


def ekvivalent_profil_ur_gradient(
    gradient_per_s: float,
    c_medel_m_s: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    atmosfar: Atmosfar,
) -> EkvivalentProfil:
    """c₀ och ξ ur en given gradient Δc/Δz och c̄, AV 1106/07 ekv. (20) och (17), TC 2.1.

    Fristående steg så att CalcEqSSPGround (avsnitt 5.5.3) kan anropa det med den modifierade
    gradienten Δc'/Δz i ekv. (23). Stegen är:

    1. c₀ = c̄ − Δc/Δz·(h_S + h_R)/2 (ekv. 20) och ξ = (Δc/Δz)/c₀ (ekv. 17), med höjderna
       begränsade nedåt till 5·z₀.
    2. TC-101327 avsnitt 2.1: är ξ < −0,2 sätts ξ = −0,2 och c₀ räknas om ur ekv. (20) med
       Δc/Δz = −0,2·c₀, alltså c₀ = c̄/(1 − 0,1·(h_S + h_R)).
    3. Är |ξ| < 10⁻⁶ sätts ξ = 0 och c̄ = c₀ = C.
    """
    h_s, h_r = profilhojder_m(kallhojd_m, mottagarhojd_m, atmosfar.z0_m)
    hojdsumma = h_s + h_r
    c0 = c_medel_m_s - gradient_per_s * 0.5 * hojdsumma
    if not c0 > 0.0:
        raise BullerFel(
            "Den ekvivalenta linjära profilen ger c0 ≤ 0 (ekv. 20): gradienten är för stark för "
            "de givna höjderna."
        )
    xi = gradient_per_s / c0
    if xi < LAGSTA_RELATIV_GRADIENT_PER_M:
        namnare = 1.0 + LAGSTA_RELATIV_GRADIENT_PER_M * 0.5 * hojdsumma
        if not namnare > 0.0:
            raise BullerFel(
                "TC-101327 avsnitt 2.1 (ξ = −0,2) ger c0 ≤ 0 när h_S + h_R ≥ 10 m; fallet "
                "täcks inte."
            )
        xi = LAGSTA_RELATIV_GRADIENT_PER_M
        c0 = c_medel_m_s / namnare
    if abs(xi) < MINSTA_RELATIV_GRADIENT_PER_M:
        return EkvivalentProfil(xi_per_m=0.0, c0_m_s=atmosfar.c_m_s, c_medel_m_s=atmosfar.c_m_s)
    return EkvivalentProfil(xi_per_m=xi, c0_m_s=c0, c_medel_m_s=c_medel_m_s)


def calc_eq_ssp(kallhojd_m: float, mottagarhojd_m: float, atmosfar: Atmosfar) -> EkvivalentProfil:
    """Funktionen CalcEqSSP, AV 1106/07 avsnitt 5.5.2, ekv. (15)–(21), med TC-101327 avsnitt 2.1.

    Ger (ξ, c₀, c̄) för plan mark ur h_S, h_R, z₀, A, B och C. Gradienten är medelgradienten
    mellan höjderna (ekv. 18), c̄ medelvärdet av c(z) mellan dem (ekv. 19, bilaga F) och den
    linjära profilen går genom c̄ på medelhöjden (ekv. 20). Vid lika höjder används samma
    modifierade höjder (±0,005 m) i ekv. (19) som i ekv. (18); medelhöjden är oförändrad.

    För banor över skärmar ersätts en av höjderna med h″_SCR (ekv. 22); det gör anroparen.
    """
    gradient = ljudhastighetsgradient_per_s(kallhojd_m, mottagarhojd_m, atmosfar)
    h_s, h_r = _gradienthojder_m(kallhojd_m, mottagarhojd_m, atmosfar)
    c_medel = medelljudhastighet_m_s(h_s, h_r, atmosfar)
    return ekvivalent_profil_ur_gradient(gradient, c_medel, kallhojd_m, mottagarhojd_m, atmosfar)
