"""Delmodell 8 i Nord2000: flera markreflexer vid stark nedåtbrytning, och strålens höjd.

Källor (varje formel avläst från sidbild):

* DELTA AV 1106/07 (rev. 2014), avsnitt 5.5.7 (RayCurvature, ekv. 54–56), 5.17 (delmodell 8,
  ekv. 278–285) och 5.23.9 (HeightOfCircularRay, ekv. 355–368), samt anropen i ekv. (292)
  (spridningszon) och (298) (reflex).
* FORCE TC-101327 (2019) ändrar inget i dessa avsnitt (genomsökt).
* DELTA AV 1851/00 (rev. 2006), avsnitt 6.13 och bilaga B, som bakgrund (samma formler,
  ekv. 54–67 och B1–B5) och för att avgöra ett tryckfel i ekv. (285).

Modulen har tre delar:

1. **Strålens krökning och höjd.** `stralkrokning` (RayCurvature) ger krökningsradien R_A,B
   för log-lin-profilen och den relativa gradienten ξ_ray; `hojd_cirkelstrale`
   (HeightOfCircularRay) ger höjden på en cirkelstråle mellan två punkter.
2. **Gränssnitt till andra delmodeller.** `Cirkelstrale` uppfyller protokollet `Stralbana` i
   `delmodell10.py` (ekv. 292), och `reflexgeometri_krokt_strale` ger `Reflexgeometri` till
   `delmodell11.py` (ekv. 298).
3. **Delmodell 8.** Antalet strålar N (ekv. 279), strålarnas ordning (ekv. 280–281), bidraget
   ΔL₈ = ΔL_multi (ekv. 282–283), ekvivalent A′ när B ≠ 0 (ekv. 284) och h_max för icke-plan
   terräng (ekv. 285).

Bidraget är en nivå relativt fritt fält och läggs energimässigt (⊕) till ΔL_t i ekv. (332).
"Inget bidrag" är −∞ dB, som är neutralt i ⊕ (samma val som i delmodell 7).

Val och misstänkta tryckfel står i `docs/buller/logg/P-8.md` under "Tolkningar".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from buller.nord2000.delmodell10 import Stralpunkt
from buller.nord2000.delmodell11 import STORSTA_STRALLUTNING, Reflexgeometri
from buller.nord2000.geometri import normlinje
from buller.nord2000.mark import markimpedans, planvagsreflektion
from buller.nord2000.refraktion import Atmosfar
from buller.nord2000.terrang import terranggeometri
from buller.typer import BullerFel, Flyttal, Terrangprofil

# AV 1106/07 avsnitt 5.17 (texten efter ekv. 279): för N under 4 bortses från extra strålar.
MINSTA_ANTAL_STRALAR = 4.0
# AV 1106/07 ekv. (282): summan över hela strålar börjar på stråle nummer M = 5.
FORSTA_EXTRA_STRALE = 5
# AV 1106/07 ekv. (281): fyra strålar per ordning.
STRALAR_PER_ORDNING = 4
# AV 1106/07 avsnitt 5.17 (texten före ekv. 283): ρ_ray = 0,00001.
STRALRORSANDEL = 1.0e-5
# AV 1106/07 ekv. (283): faktorn 10^(−0,1) i A_ray(n).
STRALRORSFAKTOR = 10.0**-0.1

Summering = Literal["hog", "lag"]

_SERIEGRANS = 1.0e-3
# Hur långt utanför [0, d] en punkt får ligga (avrundning hos anroparen), relativt d.
_RELATIV_KANTTOLERANS = 1.0e-9


def _asin_genom_x(x: float) -> float:
    """arcsin(x)/x med gränsvärdet 1 i x = 0 (Maclaurinserie för små |x|)."""
    if abs(x) < _SERIEGRANS:
        x2 = x * x
        return 1.0 + x2 / 6.0 + 3.0 * x2 * x2 / 40.0
    return math.asin(x) / x


# --------------------------------------------------------------------------------------------
# RayCurvature (avsnitt 5.5.7)
# --------------------------------------------------------------------------------------------


def krokningsradie_m(avstand_m: float, a_m_s: float, b_per_s: float, c_m_s: float) -> float:
    """R_A,B: strålens krökningsradie i log-lin-profilen, AV 1106/07 avsnitt 5.5.7, ekv. (54).

    R_A = sign(A)·(d/8)·√(2πC/|A|), R_B = sign(B)·√((C/|B|)² + (d/2)²) och
    1/R_A,B = 1/R_A + 1/R_B. Positiv radie betyder nedåtbrytning, negativ uppåtbrytning.

    Ekv. (54) är odefinierad för A = 0 eller B = 0. Här räknas krökningen 1/R, så att en
    koefficient som är 0 bidrar med krökningen 0 (gränsvärdet av ekv. 54). Är summan 0
    (ingen refraktion, eller delar som tar ut varandra) returneras +∞: rät stråle.
    """
    if not avstand_m > 0.0:
        raise BullerFel("Det horisontella avståndet ska vara större än 0.")
    if not c_m_s > 0.0:
        raise BullerFel("Ljudhastigheten C ska vara större än 0.")
    krokning = 0.0
    if a_m_s != 0.0:
        radie_a = avstand_m / 8.0 * math.sqrt(2.0 * math.pi * c_m_s / abs(a_m_s))
        krokning += math.copysign(1.0 / radie_a, a_m_s)
    if b_per_s != 0.0:
        radie_b = math.hypot(c_m_s / abs(b_per_s), 0.5 * avstand_m)
        krokning += math.copysign(1.0 / radie_b, b_per_s)
    if krokning == 0.0:
        return math.inf
    return 1.0 / krokning


@dataclass(frozen=True)
class Stralkrokning:
    """Utdata från RayCurvature, AV 1106/07 ekv. (56)."""

    radie_m: float  # R_A,B; +∞ för rät stråle, negativ vid uppåtbrytning
    xi_ray_per_m: float  # ξ_ray, ekv. (55); 0 för rät stråle
    startvinkel_rad: float  # ψ_S: strålens vinkel mot horisontalplanet i källan, ekv. (55)


def stralkrokning(
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    a_m_s: float,
    b_per_s: float,
    c_m_s: float,
) -> Stralkrokning:
    """Funktionen RayCurvature, AV 1106/07 avsnitt 5.5.7, ekv. (54)–(56).

    ψ_S = arcsin(√(d² + (h_R − h_S)²)/(2·R_A,B)) + arctan((h_R − h_S)/d) och
    ξ_ray = 1/(R_A,B·cos ψ_S) (ekv. 55). Höjderna ingår bara genom skillnaden h_R − h_S, så
    de får anges i vilket gemensamt höjdsystem som helst. För R_A,B = ∞ är ξ_ray = 0 och ψ_S
    den räta linjens lutning.

    Går ingen cirkel med radien |R_A,B| genom båda punkterna (kordan längre än diametern) ges
    `BullerFel`.
    """
    radie = krokningsradie_m(avstand_m, a_m_s, b_per_s, c_m_s)
    hojdskillnad = mottagarhojd_m - kallhojd_m
    lutning = math.atan(hojdskillnad / avstand_m)
    if math.isinf(radie):
        return Stralkrokning(radie_m=math.inf, xi_ray_per_m=0.0, startvinkel_rad=lutning)
    sinus = math.hypot(avstand_m, hojdskillnad) / (2.0 * radie)
    if abs(sinus) > 1.0:
        raise BullerFel(
            "RayCurvature: avståndet mellan källa och mottagare är större än strålcirkelns "
            f"diameter (R_A,B = {radie:.1f} m, ekv. 55); fallet täcks inte."
        )
    startvinkel = math.asin(sinus) + lutning
    cosinus = math.cos(startvinkel)
    if not cosinus > 0.0:
        raise BullerFel("RayCurvature: strålen startar lodrätt (cos ψ_S ≤ 0, ekv. 55).")
    return Stralkrokning(
        radie_m=radie, xi_ray_per_m=1.0 / (radie * cosinus), startvinkel_rad=startvinkel
    )


def stralkrokning_ur_atmosfar(
    avstand_m: float, kallhojd_m: float, mottagarhojd_m: float, atmosfar: Atmosfar
) -> Stralkrokning:
    """RayCurvature med A, B och C tagna ur `Atmosfar` (AV 1106/07 ekv. 56)."""
    return stralkrokning(
        avstand_m, kallhojd_m, mottagarhojd_m, atmosfar.a_m_s, atmosfar.b_per_s, atmosfar.c_m_s
    )


# --------------------------------------------------------------------------------------------
# HeightOfCircularRay (avsnitt 5.23.9)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cirkelstralepunkt:
    """Utdata från HeightOfCircularRay, AV 1106/07 ekv. (355)."""

    hojd_m: float  # h₀
    vag_fran_start_m: float  # R₁: strålens längd från startpunkten till P₀
    vag_till_slut_m: float  # R₂: strålens längd från P₀ till slutpunkten


def hojd_cirkelstrale(
    avstand_m: float,
    hojd_start_m: float,
    hojd_slut_m: float,
    xi_per_m: float,
    avstand_punkt_m: float,
) -> Cirkelstralepunkt:
    """Funktionen HeightOfCircularRay, AV 1106/07 avsnitt 5.23.9, ekv. (355)–(368).

    Strålen går från (0, h₁) till (d, h₂) och har en krökning som svarar mot den relativa
    gradienten ξ; funktionen ger höjden h₀ på avståndet d₀ från starten och strålens längder
    R₁ och R₂ från P₀ till start- och slutpunkten.

    Normeringen i ekv. (356)–(361) lägger den nedre punkten i origo för ξ > 0 och speglar
    strålen i horisontalplanet för ξ < 0. Med tan ψ_L enligt ekv. (362) är cirkeln den med
    medelpunkt (x₀, z₀) = (tan ψ_L/ξ′, h₁′ − 1/ξ′) och radie R_c = 1/(ξ′·cos ψ_L)
    (ekv. 363–365), och höjden är Sign(ξ)·(R_c·sin θ + z₀) (ekv. 366–367).

    Räkneform. Ekv. (363)–(367) är skillnaden mellan två tal av storleken 1/ξ′ och är
    odefinierade för ξ = 0. Samma cirkel skrivs här som

        h₀′ = h₁′ + g/(1 + √(1 + ξ′·g)),    g = d₀′·(2·tan ψ_L − ξ′·d₀′),

    vilket följer av R_c² = x₀² + 1/ξ′² och saknar utsläckningen. För ξ = 0 ger uttrycket
    den räta linjen mellan punkterna (fallet räknas i grenen ξ > 0; ekv. 358–361 nämner det
    inte). R₁ och R₂ (ekv. 368) räknas som r·arcsin(x)/x med x = r/(2·R_c). Testerna jämför
    med ekv. (362)–(368) bokstavligt.

    Höjden kan bli negativ vid uppåtbrytning (strålen går då under höjdsystemets nollnivå);
    det är anroparens sak att tolka.
    """
    if not avstand_m > 0.0:
        raise BullerFel("Det horisontella avståndet ska vara större än 0.")
    if not math.isfinite(xi_per_m):
        raise BullerFel("Den relativa gradienten ξ ska vara ett ändligt tal.")
    d = avstand_m
    tolerans = _RELATIV_KANTTOLERANS * d
    if avstand_punkt_m < -tolerans or avstand_punkt_m > d + tolerans:
        raise BullerFel("HeightOfCircularRay: punkten ska ligga mellan strålens ändpunkter.")
    d0 = min(max(avstand_punkt_m, 0.0), d)

    xi = abs(xi_per_m)  # ekv. (356)
    hojdskillnad = abs(hojd_start_m - hojd_slut_m)  # ekv. (357)
    if xi_per_m >= 0.0:
        tecken = 1.0
        if hojd_start_m > hojd_slut_m:
            h_norm, d0_norm = hojd_slut_m, d - d0  # ekv. (358)
        else:
            h_norm, d0_norm = hojd_start_m, d0  # ekv. (359)
    else:
        tecken = -1.0
        if hojd_start_m >= hojd_slut_m:
            h_norm, d0_norm = -hojd_start_m, d0  # ekv. (360)
        else:
            h_norm, d0_norm = -hojd_slut_m, d - d0  # ekv. (361)

    tan_psi = 0.5 * xi * d + hojdskillnad * (2.0 + xi * hojdskillnad) / (2.0 * d)  # ekv. (362)
    g = d0_norm * (2.0 * tan_psi - xi * d0_norm)
    rot = math.sqrt(max(1.0 + xi * g, 0.0))
    h0 = tecken * (h_norm + g / (1.0 + rot))  # lika med ekv. (363)–(367)

    cos_psi = 1.0 / math.sqrt(1.0 + tan_psi * tan_psi)
    halv_krokning = 0.5 * xi * cos_psi  # 1/(2·R_c), ekv. (365)
    langder: list[float] = []
    for korda in (
        math.hypot(d0, h0 - hojd_start_m),
        math.hypot(d - d0, hojd_slut_m - h0),
    ):  # r₁ och r₂ i ekv. (368)
        x = korda * halv_krokning
        if x > 1.0 + 1.0e-9:
            raise BullerFel("HeightOfCircularRay: kordan är längre än cirkelns diameter.")
        langder.append(korda * _asin_genom_x(min(x, 1.0)))  # ekv. (368)
    return Cirkelstralepunkt(hojd_m=h0, vag_fran_start_m=langder[0], vag_till_slut_m=langder[1])


# --------------------------------------------------------------------------------------------
# Gränssnitt till delmodell 10 (ekv. 292) och delmodell 11 (ekv. 298)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cirkelstrale:
    """Cirkelstråle från källan till mottagaren, AV 1106/07 avsnitt 5.19, ekv. (292).

    Uppfyller protokollet `Stralbana` i `delmodell10.py`: `punkt(x)` ger z_ray(x), R₁(x) och
    R₂(x) ur HeightOfCircularRay(x_R − x_S, h_S, h_R, ξ_ray, x − x_S). Höjderna anges i samma
    höjdsystem som zonens överkant (t.ex. RH 2000); strålens form beror bara på
    höjdskillnaden. `xi_ray_per_m` kommer ur RayCurvature; 0 ger den räta strålen.
    """

    x_kalla_m: float
    z_kalla_m: float
    x_mottagare_m: float
    z_mottagare_m: float
    xi_ray_per_m: float

    def __post_init__(self) -> None:
        if not self.x_kalla_m < self.x_mottagare_m:
            raise BullerFel("Mottagaren ska ligga på större x än källan.")
        if not math.isfinite(self.xi_ray_per_m):
            raise BullerFel("ξ_ray ska vara ett ändligt tal.")

    @property
    def x_start_m(self) -> float:
        return self.x_kalla_m

    @property
    def x_slut_m(self) -> float:
        return self.x_mottagare_m

    def punkt(self, x_m: float) -> Stralpunkt:
        """z_ray(x), R₁(x) och R₂(x), AV 1106/07 ekv. (292), andra raden."""
        p = hojd_cirkelstrale(
            self.x_mottagare_m - self.x_kalla_m,
            self.z_kalla_m,
            self.z_mottagare_m,
            self.xi_ray_per_m,
            x_m - self.x_kalla_m,
        )
        return Stralpunkt(z_m=p.hojd_m, r1_m=p.vag_fran_start_m, r2_m=p.vag_till_slut_m)

    def hojd_m(self, x_m: float) -> float:
        """Strålens höjd z_ray(x)."""
        return self.punkt(x_m).z_m


def cirkelstrale_ur_atmosfar(
    x_kalla_m: float,
    z_kalla_m: float,
    x_mottagare_m: float,
    z_mottagare_m: float,
    atmosfar: Atmosfar,
) -> Cirkelstrale:
    """Strålbanan i AV 1106/07 ekv. (292): RayCurvature följd av HeightOfCircularRay.

    ξ_ray = RayCurvature(A, B, C, x_R − x_S, h_S, h_R). Utan refraktion (A = B = 0) blir
    strålen rät och lika med `delmodell10.RatStrale`.
    """
    if not x_kalla_m < x_mottagare_m:
        raise BullerFel("Mottagaren ska ligga på större x än källan.")
    krokning = stralkrokning_ur_atmosfar(
        x_mottagare_m - x_kalla_m, z_kalla_m, z_mottagare_m, atmosfar
    )
    return Cirkelstrale(
        x_kalla_m=x_kalla_m,
        z_kalla_m=z_kalla_m,
        x_mottagare_m=x_mottagare_m,
        z_mottagare_m=z_mottagare_m,
        xi_ray_per_m=krokning.xi_ray_per_m,
    )


def reflexgeometri_krokt_strale(
    d1_m: float,
    d2_m: float,
    z_kalla_m: float,
    z_mottagare_m: float,
    theta_r_rad: float,
    atmosfar: Atmosfar,
    *,
    storsta_lutning: float = STORSTA_STRALLUTNING,
) -> Reflexgeometri:
    """z_O, R₁ och R₂ i reflexpunkten med refraktion, AV 1106/07 avsnitt 5.20, ekv. (298).

    ξ_ray = RayCurvature(A, B, C, d, h_S, h_R) och (h_O, R₁, R₂) =
    HeightOfCircularRay(d, h_S, h_R, ξ_ray, d₁) med d = d₁ + d₂ längs den utvikta banan.
    Motsvarar `delmodell11.reflexgeometri_rak_strale` och ger samma värden för A = B = 0.
    Höjderna anges i samma höjdsystem som reflektorns kanter. `atmosfar` ska för en reflexbana
    ha A och B vägda enligt ekv. (295) (`delmodell11.vagda_vaderkoefficienter`); det gör
    anroparen.

    Samma gräns för banans lutning som i den räta varianten (`storsta_lutning`).
    """
    if not (math.isfinite(d1_m) and math.isfinite(d2_m)) or d1_m <= 0.0 or d2_m <= 0.0:
        raise BullerFel("Avstånden d1 och d2 till reflexpunkten ska vara större än 0.")
    d = d1_m + d2_m
    hojdskillnad = z_mottagare_m - z_kalla_m
    if abs(hojdskillnad) > storsta_lutning * d:
        raise BullerFel(
            f"Reflexbanan lutar {abs(hojdskillnad) / d:.3f} (höjdskillnad/sträcka), mer än "
            f"{storsta_lutning:.3f}: Fresnelzonen för en reflex är i Nord2000 bara beskriven "
            "för en stråle som kan räknas som vågrät (AV 1106/07 avsnitt 5.20)."
        )
    krokning = stralkrokning_ur_atmosfar(d, z_kalla_m, z_mottagare_m, atmosfar)
    p = hojd_cirkelstrale(d, z_kalla_m, z_mottagare_m, krokning.xi_ray_per_m, d1_m)
    return Reflexgeometri(
        r1_m=p.vag_fran_start_m,
        r2_m=p.vag_till_slut_m,
        theta_r_rad=theta_r_rad,
        z_o_m=p.hojd_m,
    )


# --------------------------------------------------------------------------------------------
# Delmodell 8: antalet strålar (ekv. 279–281, 284–285)
# --------------------------------------------------------------------------------------------


def ekvivalent_a_m_s(avstand_m: float, atmosfar: Atmosfar) -> float:
    """A′: A i den rent logaritmiska profil som ger samma R_A,B, AV 1106/07 ekv. (284).

    A′ = π·C·d²/(32·R_A,B²). För B = 0 är A′ = A (ekv. 54 insatt), och då returneras A.
    Delmodell 8 gäller stark nedåtbrytning; ekv. (284) beror på R_A,B² och skiljer inte
    nedåt- från uppåtbrytning. För R_A,B ≤ 0 eller oändlig returneras därför 0 (ingen
    nedåtbrytning, N = 0); se tolkningar i loggen.
    """
    radie = krokningsradie_m(avstand_m, atmosfar.a_m_s, atmosfar.b_per_s, atmosfar.c_m_s)
    if radie <= 0.0 or math.isinf(radie):
        return 0.0
    if atmosfar.b_per_s == 0.0:
        return atmosfar.a_m_s
    return math.pi * atmosfar.c_m_s * avstand_m**2 / (32.0 * radie**2)


def antal_stralar(avstand_m: float, storsta_hojd_m: float, a_m_s: float, c_m_s: float) -> float:
    """N: ungefärligt antal strålar (decimaltal), AV 1106/07 avsnitt 5.17, ekv. (279).

    N = (4·d_base/h_max)·√(A/(2πC)). `a_m_s` är A eller A′ (ekv. 284) och ska vara ≥ 0;
    A = 0 ger N = 0.
    """
    if not avstand_m > 0.0 or not storsta_hojd_m > 0.0 or not c_m_s > 0.0:
        raise BullerFel("Ekv. (279) kräver d_base, h_max och C större än 0.")
    if a_m_s < 0.0:
        raise BullerFel("Ekv. (279) gäller nedåtbrytning: A (eller A′) får inte vara negativ.")
    return 4.0 * avstand_m / storsta_hojd_m * math.sqrt(a_m_s / (2.0 * math.pi * c_m_s))


def stralordning(stralnummer: int) -> int:
    """n: antalet markreflexer för stråle nummer M, AV 1106/07 ekv. (281): Int((M − 1)/4) + 1."""
    if stralnummer < 1:
        raise BullerFel("Strålnumret M ska vara minst 1.")
    return (stralnummer - 1) // STRALAR_PER_ORDNING + 1


def storsta_ordning(antal: float) -> int:
    """n_max = Int((N − 1)/4) + 1, AV 1106/07 ekv. (281). Avsedd för N ≥ 1."""
    if antal < 1.0:
        raise BullerFel("n_max i ekv. (281) är avsedd för N ≥ 1.")
    return int((antal - 1.0) / STRALAR_PER_ORDNING) + 1


def storsta_hojd_m(profil: Terrangprofil) -> float:
    """h_max i ekv. (279) för allmän terräng, AV 1106/07 avsnitt 5.17, ekv. (285).

    h_max = Max(h_S, h_R, h_base,m, h_S − h̄_base, h_R − h̄_base, h_base,m − h̄_base), där
    höjderna räknas från terrängens baslinje (avsnitt 5.4.1), h_base,m är den högsta av de
    inre terrängpunkterna (i = 2 … N_ts; skärmar är terrängpunkter) och h̄_base terrängens
    medelavvikelse från baslinjen. För plan mark är h_base = 0 och h_max = Max(h_S, h_R) som
    i ekv. (279).

    h̄_base är tryckt med (h_base(i + 1) − h_base(i))/2; här används summan, alltså
    trapetsmedelvärdet, som är vad texten ("average deviation") och AV 1851/00 avsnitt 6.13
    beskriver. Se tolkningar i loggen. h_base räknas för indataprofilens punkter, med
    P_S och P_R (ekv. 4) som baslinjens ändar, så att vikterna summerar till 1.
    """
    geo = terranggeometri(profil)  # källans och mottagarens läge, med minsta höjd 0,01 m
    x = [float(v) for v in profil.x_m]
    z = [float(v) for v in profil.z_m]
    x_ps, z_ps, x_pr, z_pr = x[0], z[0], x[-1], z[-1]
    _, _, h_s = normlinje(x_ps, z_ps, x_pr, z_pr, geo.x_s_m, geo.z_s_m)
    _, _, h_r = normlinje(x_ps, z_ps, x_pr, z_pr, geo.x_r_m, geo.z_r_m)
    h_bas = [normlinje(x_ps, z_ps, x_pr, z_pr, xi, zi)[2] for xi, zi in zip(x, z, strict=True)]
    medel = 0.0  # h̄_base
    for i in range(len(x) - 1):
        medel += 0.5 * (h_bas[i + 1] + h_bas[i]) * (x[i + 1] - x[i]) / (x_pr - x_ps)
    hojder = [h_s, h_r, h_s - medel, h_r - medel]
    if len(x) > 2:
        hogsta_inre = max(h_bas[1:-1])  # h_base,m
        hojder += [hogsta_inre, hogsta_inre - medel]
    h_max = max(hojder)
    if not h_max > 0.0:
        raise BullerFel("h_max i ekv. (285) blev inte större än 0.")
    return h_max


# --------------------------------------------------------------------------------------------
# Delmodell 8: strålarnas bidrag (ekv. 278, 282–283)
# --------------------------------------------------------------------------------------------


def _ljudhastighet_log_m_s(hojd_m: float, a_m_s: float, c_m_s: float, z0_m: float) -> float:
    """c(z) = A·ln(z/z₀ + 1) + C, AV 1106/07 avsnitt 5.17, ekv. (278)."""
    return a_m_s * math.log(hojd_m / z0_m + 1.0) + c_m_s


def stralhojd_m(ordning: int, avstand_m: float, a_m_s: float, c_m_s: float) -> float:
    """h_max(n) = (d_base/n)·√(A/(2πC)): högsta höjden för en stråle av ordning n, ekv. (283)."""
    if ordning < 1:
        raise BullerFel("Ordningen n ska vara minst 1.")
    return avstand_m / ordning * math.sqrt(a_m_s / (2.0 * math.pi * c_m_s))


def strykvinkel_rad(hojd_m: float, a_m_s: float, c_m_s: float, z0_m: float) -> float:
    """ψ_G = arccos(C/c(h)): strålens vinkel mot marken när dess högsta höjd är h, ekv. (283)."""
    return math.acos(c_m_s / _ljudhastighet_log_m_s(hojd_m, a_m_s, c_m_s, z0_m))


def stralrorsarea(ordning: int, avstand_m: float, a_m_s: float, c_m_s: float, z0_m: float) -> float:
    """A_ray(n), AV 1106/07 avsnitt 5.17, ekv. (283).

    A_ray(n) = 10^(−0,1)·√(|ψ_G(n) − ψ′_G(n)|/(ρ_ray·sin ψ_G(n))), där ψ_G hör till höjden
    h_max(n) och ψ′_G till (1 + ρ_ray)·h_max(n), med ρ_ray = 0,00001. Storheten multipliceras
    med reflektionskoefficienten och är strålens tryckamplitud relativt fritt fält.
    """
    if not a_m_s > 0.0:
        raise BullerFel("A_ray i ekv. (283) kräver A > 0 (nedåtbrytning).")
    hojd = stralhojd_m(ordning, avstand_m, a_m_s, c_m_s)
    psi = strykvinkel_rad(hojd, a_m_s, c_m_s, z0_m)
    psi_prim = strykvinkel_rad((1.0 + STRALRORSANDEL) * hojd, a_m_s, c_m_s, z0_m)
    return STRALRORSFAKTOR * math.sqrt(abs(psi - psi_prim) / (STRALRORSANDEL * math.sin(psi)))


@dataclass(frozen=True)
class Markstracka:
    """En delsträcka av marken med en flödesresistivitet, för R(n) i ekv. (283)."""

    andel: float  # (x_{i+1} − x_i)/(x_RGv − x_SGv)
    flodesresistivitet_kNsm4: float


def markstrackor(profil: Terrangprofil) -> tuple[Markstracka, ...]:
    """Vikterna (x_{i+1} − x_i)/(x_RGv − x_SGv) per terrängsegment, AV 1106/07 ekv. (283).

    x_SGv och x_RGv är markpunkterna lodrätt under källa och mottagare, alltså profilens
    första och sista punkt (ekv. 4), så andelarna summerar till 1.
    """
    x = profil.x_m
    langd = float(x[-1]) - float(x[0])
    return tuple(
        Markstracka(
            andel=(float(x[i + 1]) - float(x[i])) / langd,
            flodesresistivitet_kNsm4=float(profil.flodesresistivitet_kNsm4[i]),
        )
        for i in range(x.shape[0] - 1)
    )


def medelreflektion(
    frekvens_hz: float, strykvinkel: float, strackor: tuple[Markstracka, ...]
) -> float:
    """R(n) = Σ |R̂_p(f, ψ_G(n), Ẑ_G(f))|·(x_{i+1} − x_i)/(x_RGv − x_SGv), ekv. (283).

    Längdvägt medelvärde av planvågsreflektionskoefficientens belopp (ekv. 77) över
    terrängsegmenten, med impedansen ur ekv. (57).
    """
    summa = 0.0
    for stracka in strackor:
        impedans = markimpedans(frekvens_hz, stracka.flodesresistivitet_kNsm4)
        summa += abs(planvagsreflektion(impedans, strykvinkel)) * stracka.andel
    return summa


def _stralar_per_ordning(heltalsdel: int) -> dict[int, int]:
    """Antalet hela strålar M = 5 … N_i av varje ordning n (ekv. 281–282)."""
    antal: dict[int, int] = {}
    for m in range(FORSTA_EXTRA_STRALE, heltalsdel + 1):
        n = stralordning(m)
        antal[n] = antal.get(n, 0) + 1
    return antal


def bidrag_flera_markreflexer_db(
    frekvenser_hz: Flyttal,
    antal: float,
    avstand_m: float,
    a_m_s: float,
    c_m_s: float,
    z0_m: float,
    strackor: tuple[Markstracka, ...],
    summering: Summering = "hog",
) -> Flyttal:
    """ΔL_multi per frekvens, AV 1106/07 avsnitt 5.17, ekv. (280)–(283).

    N delas i heltalsdelen N_i och resten ΔN (ekv. 280). Med t(n) = R(n)ⁿ·A_ray(n) är

        ΔL_multi,hf = 10·lg((ΔN·t(n_max))² + Σ_{M=5}^{N_i} t(n)²)      (`"hog"`, inkoherent),
        ΔL_multi,lf = 20·lg(ΔN·t(n_max) + Σ_{M=5}^{N_i} t(n))          (`"lag"`, koherent),

    där n = Int((M − 1)/4) + 1 och n_max = Int((N − 1)/4) + 1 (ekv. 281). Metoden
    rekommenderar högfrekvenslösningen för normalt bruk och den är förval; lågfrekvenslösningen
    ger högre värden och är avsedd för fall där låga frekvenser är viktiga.

    För N ≤ 4 är bidraget −∞ dB (inget bidrag). Vid N = 4 är ΔN = 0 och summan tom, så
    bidraget växer kontinuerligt från noll energi när N passerar tröskeln.
    """
    if summering not in ("hog", "lag"):
        raise BullerFel(f"Okänd summering för delmodell 8: {summering!r} (hog eller lag).")
    resultat = np.full(frekvenser_hz.shape, -math.inf, dtype=np.float64)
    if not antal > MINSTA_ANTAL_STRALAR:
        return resultat
    if not z0_m > 0.0:
        raise BullerFel("Råhetslängden z0 i ekv. (278) ska vara större än 0.")
    heltalsdel = math.floor(antal)
    rest = antal - heltalsdel  # ΔN, ekv. (280)
    n_max = storsta_ordning(antal)
    hela = _stralar_per_ordning(heltalsdel)

    ordningar = sorted(set(hela) | {n_max})
    area = {n: stralrorsarea(n, avstand_m, a_m_s, c_m_s, z0_m) for n in ordningar}
    vinkel = {
        n: strykvinkel_rad(stralhojd_m(n, avstand_m, a_m_s, c_m_s), a_m_s, c_m_s, z0_m)
        for n in ordningar
    }
    for i, f_np in enumerate(frekvenser_hz):
        f = float(f_np)
        term = {n: medelreflektion(f, vinkel[n], strackor) ** n * area[n] for n in ordningar}
        if summering == "hog":
            energi = (rest * term[n_max]) ** 2 + sum(k * term[n] ** 2 for n, k in hela.items())
            if energi > 0.0:
                resultat[i] = 10.0 * math.log10(energi)
        else:
            amplitud = rest * term[n_max] + sum(k * term[n] for n, k in hela.items())
            if amplitud > 0.0:
                resultat[i] = 20.0 * math.log10(amplitud)
    return resultat


@dataclass(frozen=True)
class FleraMarkreflexer:
    """Delmodell 8 för en bana: N och ΔL₈ per band."""

    antal_stralar: float  # N, ekv. (279)
    storsta_hojd_m: float  # h_max, ekv. (279) eller (285)
    a_ekvivalent_m_s: float  # A eller A′ (ekv. 284); 0 utan nedåtbrytning
    delta_l_db: Flyttal  # ΔL₈ = ΔL_multi, form (antal band,); −∞ = inget bidrag

    @property
    def bidrar(self) -> bool:
        """Sant när N > 4 (AV 1106/07 avsnitt 5.17)."""
        return self.antal_stralar > MINSTA_ANTAL_STRALAR


def flera_markreflexer(
    frekvenser_hz: Flyttal,
    profil: Terrangprofil,
    atmosfar: Atmosfar,
    summering: Summering = "hog",
) -> FleraMarkreflexer:
    """Delmodell 8 för en terrängprofil: ΔL₈ i AV 1106/07 ekv. (332), avsnitt 5.17.

    Stegen: A′ ur RayCurvature över d_base (ekv. 284; A när B = 0), h_max ur ekv. (285),
    N ur ekv. (279) och, när N > 4, ΔL_multi ur ekv. (280)–(283) med c(z) enligt ekv. (278)
    (A′ i stället för A). Utan nedåtbrytning är N = 0 och bidraget −∞ dB i alla band.
    Medelvärdena A och B används; de övre värdena A₊, B₊ (ekv. 10) hör inte hit.
    """
    geo = terranggeometri(profil)
    avstand = geo.d_bas_tot_m
    a_ekv = ekvivalent_a_m_s(avstand, atmosfar)
    h_max = storsta_hojd_m(profil)
    antal = antal_stralar(avstand, h_max, a_ekv, atmosfar.c_m_s)
    delta_l = bidrag_flera_markreflexer_db(
        frekvenser_hz,
        antal,
        avstand,
        a_ekv,
        atmosfar.c_m_s,
        atmosfar.z0_effektiv_m,
        markstrackor(profil),
        summering,
    )
    return FleraMarkreflexer(
        antal_stralar=antal, storsta_hojd_m=h_max, a_ekvivalent_m_s=a_ekv, delta_l_db=delta_l
    )


def energisumma_db(niva1_db: Flyttal, niva2_db: Flyttal) -> Flyttal:
    """Symbolen ⊕ per band: 10·lg(10^(L₁/10) + 10^(L₂/10)), AV 1106/07 avsnitt 4.1.

    Används för ΔL_t ⊕ ΔL₈ i ekv. (332). −∞ dB är neutralt element.
    """
    k = math.log(10.0) / 10.0
    summa: Flyttal = np.logaddexp(k * niva1_db, k * niva2_db) / k
    return summa
