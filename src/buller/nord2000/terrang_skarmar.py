"""Terrängtolkning i Nord2000: skärmform, sekundär skärm, sekundär kant och övergångsparametrar.

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.21.1 (ekv. 300–306, skärmformen W₁–W₂),
5.21.2 (ekv. 308–310), 5.21.3 (ekv. 311–315), 5.23.2 (Convex, ekv. 336) och 5.23.18
(VertDist, ekv. 389–390), med ändringen i FORCE TC-101327 (2019) avsnitt 2.11.

Modulen bygger på `terrang.py` (källa, mottagare, baslinje, primär kant T₁) och lägger till
resten av avsnitt 5.21.1–5.21.3. Den är uppdelad i två steg så att kärnan kan lägga
refraktionen emellan:

1. `valj_skarmkanter(geo)` – ren geometri med räta strålar: T₁, primärskärmens form, T₂ med
   form och T₁₂. Kanterna väljs alltid ur Δℓ₀ (ekv. 300), aldrig ur krökta strålar.
2. `tolka_terrang(geo, kanter, frekvenser, c, ...)` – övergångsparametrarna r_scr1, r_scr2 och
   r_scr12 per frekvens och vilka skärmdelmodeller (4, 5, 6) som behövs.

KROK FÖR REFRAKTION. Gångvägsskillnaden Δℓ med krökta strålar (ekv. 301 för T₁, ekv. 307 för
T₂ och motsvarande för T₁₂) bygger på strålvariabler ur delmodell 4–6 och räknas inte här.
Alla funktioner som behöver den tar i stället ett nyckelordsargument
`gangvagsskillnad_refraktion_m`: `None` betyder uttryckligen homogen atmosfär (Δℓ = Δℓ₀, se
`docs/buller/tolkningar.md`), annars det Δℓ som delmodellen har räknat. Kriteriet använder
Δℓ' = max(Δℓ, Δℓ₀).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from buller.nord2000.frekvenser import heaviside
from buller.nord2000.fresnel import calc_fzd
from buller.nord2000.geometri import langd, normlinje
from buller.nord2000.terrang import Skarmkant, Terranggeometri, primar_skarmkant
from buller.typer import BullerFel, Flyttal

KONVEX_MARGINAL_M = 0.0001  # AV 1106/07 avsnitt 5.23.2, ekv. (336)

# Värdet som kärnan lämnar i kroken när atmosfären är homogen.
HOMOGEN_ATMOSFAR: None = None


# --------------------------------------------------------------------------- hjälpfunktioner


def vertikalt_avstand_m(x1: float, z1: float, x2: float, z2: float, x: float, z: float) -> float:
    """Funktionen VertDist, AV 1106/07 avsnitt 5.23.18, ekv. (389)–(390).

    Punkten (x, z):s teckensatta lodräta höjd över linjen genom P1 och P2 (positiv ovanför).
    """
    if x2 == x1:
        raise BullerFel("VertDist kräver att linjens två punkter har olika x.")
    return z - z1 - (z2 - z1) * (x - x1) / (x2 - x1)


def konvex(geo: Terranggeometri, index: int) -> bool:
    """Funktionen Convex(i), AV 1106/07 avsnitt 5.23.2, ekv. (336).

    Sann när segmenten som möts i terrängpunkt `index` bildar en konvex form: punkten ligger
    minst 0,0001 m över linjen mellan sina grannar (H(0) = 1, så gränsen själv räknas som
    konvex). Rak eller konkav brytning ger falskt. Definierad för inre punkter.
    """
    n = geo.x_m.shape[0]
    if not 0 < index < n - 1:
        raise BullerFel(
            f"Convex är bara definierad för inre terrängpunkter (1–{n - 2}), fick {index}."
        )
    dz = vertikalt_avstand_m(
        float(geo.x_m[index - 1]),
        float(geo.z_m[index - 1]),
        float(geo.x_m[index + 1]),
        float(geo.z_m[index + 1]),
        float(geo.x_m[index]),
        float(geo.z_m[index]),
    )
    return heaviside(dz - KONVEX_MARGINAL_M) == 1.0


def gangvagsskillnad_mellan_m(
    x_a_m: float, z_a_m: float, x_b_m: float, z_b_m: float, x_m: float, z_m: float
) -> float:
    """Δℓ₀ för punkten P mellan två ändpunkter A och B, AV 1106/07 ekv. (300).

    Samma uttryck som `terrang.gangvagsskillnad_m` men med valfria ändpunkter, vilket avsnitt
    5.21.2 kräver när S eller R ersätts med T₁ eller T₁'. |AP| + |BP| − |AB| med positivt
    tecken när P ligger på eller över linjen AB och negativt under (teckenregeln i
    `docs/buller/tolkningar.md`, ekv. 300).
    """
    if x_b_m == x_a_m:
        raise BullerFel("Δℓ₀ kräver att siktlinjens ändpunkter har olika x.")
    z_sikt = (z_b_m - z_a_m) * (x_m - x_a_m) / (x_b_m - x_a_m) + z_a_m
    omvag = langd(x_a_m, z_a_m, x_m, z_m) + langd(x_b_m, z_b_m, x_m, z_m)
    omvag -= langd(x_a_m, z_a_m, x_b_m, z_b_m)
    return omvag if z_m - z_sikt >= 0.0 else -omvag


# --------------------------------------------------------------------------- skärmform


@dataclass(frozen=True)
class Skarmform:
    """Skärmens form: terrängpunkterna W₁ … T … W₂, AV 1106/07 avsnitt 5.21.1 (sista stycket).

    Alla tre är index i `Terranggeometri.x_m`. Formen omfattar punkterna `index_w1` till och
    med `index_w2`.
    """

    index_w1: int
    index_kant: int
    index_w2: int

    @property
    def antal_punkter(self) -> int:
        """Antal terrängpunkter i formen, W₁ och W₂ inräknade."""
        return self.index_w2 - self.index_w1 + 1

    def innehaller(self, index: int) -> bool:
        """Sann när terrängpunkten hör till formen (W₁ och W₂ inräknade)."""
        return self.index_w1 <= index <= self.index_w2


def skarmform(geo: Terranggeometri, index_kant: int) -> Skarmform:
    """Skärmens start W₁ och slut W₂ kring kanten, AV 1106/07 avsnitt 5.21.1 och 5.21.2.

    W₁ är den icke-konvexa punkt som ligger närmast kanten på källsidan och W₂ den närmaste på
    mottagarsidan (Convex, ekv. 336). Profilens första och sista punkt saknar två grannar och
    räknas här som icke-konvexa, så att formen alltid slutar inom profilen.
    """
    n = geo.x_m.shape[0]
    if not 0 < index_kant < n - 1:
        raise BullerFel("En skärmkant ska vara en inre terrängpunkt.")
    w1 = index_kant - 1
    while w1 > 0 and konvex(geo, w1):
        w1 -= 1
    w2 = index_kant + 1
    while w2 < n - 1 and konvex(geo, w2):
        w2 += 1
    return Skarmform(index_w1=w1, index_kant=index_kant, index_w2=w2)


# --------------------------------------------------------------------------- val av kanter


def ersattningspunkt_t1(geo: Terranggeometri, primar: Skarmkant) -> tuple[float, float]:
    """Punkten som ersätter S eller R när sekundära kanter söks, AV 1106/07 avsnitt 5.21.2.

    T₁ själv när Δℓ₀(T₁) ≥ 0, annars T₁' på siktlinjen SR lodrätt över T₁ (figur 33).
    Villkoret gäller Δℓ₀ enligt TC-101327 avsnitt 2.11.
    """
    x_t1 = float(geo.x_m[primar.index])
    if primar.gangvagsskillnad_m >= 0.0:
        return x_t1, float(geo.z_m[primar.index])
    z_sikt = (geo.z_r_m - geo.z_s_m) * (x_t1 - geo.x_s_m) / (geo.x_r_m - geo.x_s_m) + geo.z_s_m
    return x_t1, z_sikt


def gangvagsskillnad_sekundar_m(geo: Terranggeometri, primar: Skarmkant, index: int) -> float:
    """Δℓ₀ för en punkt vid sidan av T₁, AV 1106/07 avsnitt 5.21.2 (stycket efter figur 33).

    På källsidan av T₁ ersätts R med T₁ (eller T₁'), på mottagarsidan ersätts S.
    """
    if index == primar.index:
        raise BullerFel("Sekundär gångvägsskillnad är inte definierad för T₁ själv.")
    x_e, z_e = ersattningspunkt_t1(geo, primar)
    x, z = float(geo.x_m[index]), float(geo.z_m[index])
    if index < primar.index:
        return gangvagsskillnad_mellan_m(geo.x_s_m, geo.z_s_m, x_e, z_e, x, z)
    return gangvagsskillnad_mellan_m(x_e, z_e, geo.x_r_m, geo.z_r_m, x, z)


def _basta_kant(geo: Terranggeometri, primar: Skarmkant, kandidater: list[int]) -> Skarmkant | None:
    """Kandidaten över baslinjen med störst sekundärt Δℓ₀ (lägst index vid lika värde)."""
    basta: Skarmkant | None = None
    for i in kandidater:
        if float(geo.h_bas_m[i]) <= 0.0:
            continue
        dl = gangvagsskillnad_sekundar_m(geo, primar, i)
        if basta is None or dl > basta.gangvagsskillnad_m:
            basta = Skarmkant(
                index=i,
                gangvagsskillnad_m=dl,
                hojd_over_baslinje_m=float(geo.h_bas_m[i]),
                d_bas_m=float(geo.d_bas_m[i]),
            )
    return basta


def sekundar_skarmkant(
    geo: Terranggeometri, primar: Skarmkant, primar_form: Skarmform
) -> Skarmkant | None:
    """Väljer T₂, den verksammaste kanten i en sekundär skärm, AV 1106/07 avsnitt 5.21.2.

    Kandidater är alla inre terrängpunkter över baslinjen som inte hör till primärskärmens
    form. Den med störst Δℓ₀ (räknad mot T₁ eller T₁') väljs. `gangvagsskillnad_m` i resultatet
    är detta Δℓ₀. `None` när ingen punkt kommer i fråga.
    """
    n = geo.x_m.shape[0]
    kandidater = [i for i in range(1, n - 1) if not primar_form.innehaller(i)]
    return _basta_kant(geo, primar, kandidater)


def sekundar_kant(
    geo: Terranggeometri, primar: Skarmkant, primar_form: Skarmform
) -> Skarmkant | None:
    """Väljer T₁₂, primärskärmens sekundära kant, AV 1106/07 avsnitt 5.21.3.

    Kandidater är formens punkter utom W₁, W₂ och T₁; Δℓ₀ räknas som för den sekundära
    skärmen. En form med tre punkter har ingen sekundär kant. Punkter på eller under baslinjen
    tas inte med (samma regel som för T₁ och T₂; för en sådan punkt blir r_λ12 ändå 0).
    """
    kandidater = [
        i for i in range(primar_form.index_w1 + 1, primar_form.index_w2) if i != primar.index
    ]
    return _basta_kant(geo, primar, kandidater)


@dataclass(frozen=True)
class Skarmkanter:
    """Kanterna som terrängen erbjuder, valda med räta strålar (avsnitt 5.21.1–5.21.3).

    Detta är kandidater: om de också är verksamma avgörs av övergångsparametrarna i
    `tolka_terrang`. `sekundar` och `sekundar_kant` har Δℓ₀ räknat mot T₁ eller T₁'.
    """

    primar: Skarmkant | None
    primar_form: Skarmform | None
    sekundar: Skarmkant | None
    sekundar_form: Skarmform | None
    sekundar_kant: Skarmkant | None


def valj_skarmkanter(geo: Terranggeometri) -> Skarmkanter:
    """T₁ med form, T₂ med form och T₁₂ ur terrängen, AV 1106/07 avsnitt 5.21.1–5.21.3.

    Ren geometri i homogen atmosfär. T₁ kommer från `terrang.primar_skarmkant`.
    """
    primar = primar_skarmkant(geo)
    if primar is None:
        return Skarmkanter(None, None, None, None, None)
    form = skarmform(geo, primar.index)
    sekundar = sekundar_skarmkant(geo, primar, form)
    return Skarmkanter(
        primar=primar,
        primar_form=form,
        sekundar=sekundar,
        sekundar_form=None if sekundar is None else skarmform(geo, sekundar.index),
        sekundar_kant=sekundar_kant(geo, primar, form),
    )


# --------------------------------------------------------------------------- övergångar


def gangvagsskillnad_prim_m(
    gangvagsskillnad_0_m: float, gangvagsskillnad_refraktion_m: float | None
) -> float:
    """Δℓ' = max(Δℓ, Δℓ₀), AV 1106/07 avsnitt 5.21.1 (kriterium 1).

    `gangvagsskillnad_refraktion_m` är Δℓ med krökta strålar (ekv. 301 eller 307), som räknas
    i skärmdelmodellerna. `None` anger homogen atmosfär, där Δℓ = Δℓ₀.
    """
    if gangvagsskillnad_refraktion_m is None:
        return gangvagsskillnad_0_m
    if not math.isfinite(gangvagsskillnad_refraktion_m):
        raise BullerFel("Gångvägsskillnaden med refraktion är inte ett ändligt tal.")
    return max(gangvagsskillnad_refraktion_m, gangvagsskillnad_0_m)


def overgang_gangvag(gangvagsskillnad_prim_m: float, vaglangd_m: float) -> float:
    """r_Δℓ(λ), AV 1106/07 avsnitt 5.21.1, ekv. (302). Argumentet är Δℓ'."""
    kvot = gangvagsskillnad_prim_m / vaglangd_m
    if kvot >= 0.0:
        return 1.0
    if kvot > -0.133:
        return 1.0 - math.sqrt(7.5 * abs(kvot))
    return 0.0


def overgang_hojd(hojd_m: float, vaglangd_m: float) -> float:
    """r_λ(λ), AV 1106/07 avsnitt 5.21.1, ekv. (303). `hojd_m` är h_SCR (eller h'_SCR12)."""
    kvot = hojd_m / vaglangd_m
    if kvot >= 0.3:
        return 1.0
    if kvot > 0.1:
        return (kvot - 0.1) / 0.2
    return 0.0


def overgang_fresnelzon(hojd_m: float, d1_m: float, d2_m: float, vaglangd_m: float) -> float:
    """r_Fz(λ), AV 1106/07 avsnitt 5.21.1, ekv. (304), med h_Fz ur CalcFZd (ekv. 304, 313).

    `d1_m` och `d2_m` är avstånden längs linjen från dess ändpunkter till kantens fotpunkt:
    d_bas(i) och d_bas − d_bas(i) för T₁, T₂ och T₁₂, eller d₁ och d₂ i ekv. (313).
    """
    h_fz = calc_fzd(d1_m, d2_m, math.pi / 2.0, 0.5 * vaglangd_m)
    if h_fz <= 0.0:
        raise BullerFel("Fresnelzonens halva bredd blev inte positiv (ekv. 304).")
    kvot = hojd_m / h_fz
    if kvot >= 0.082:
        return 1.0
    if kvot > 0.026:
        return (kvot - 0.026) / 0.056
    return 0.0


def overgang_kantavstand(d12_m: float, vaglangd_m: float) -> float:
    """r_w(λ), AV 1106/07 avsnitt 5.21.2, ekv. (308). `d12_m` är avståndet mellan kanterna."""
    if d12_m >= vaglangd_m:
        return 1.0
    if d12_m > 0.3 * vaglangd_m:
        return (d12_m / vaglangd_m - 0.3) / 0.7
    return 0.0


def _vaglangd_m(frekvens_hz: float, ljudhastighet_m_s: float) -> float:
    if frekvens_hz <= 0.0 or ljudhastighet_m_s <= 0.0:
        raise BullerFel("Frekvens och ljudhastighet ska vara större än 0.")
    return ljudhastighet_m_s / frekvens_hz


def _tre_kriterier(
    kant: Skarmkant,
    d_bas_tot_m: float,
    vaglangd_m: float,
    gangvagsskillnad_refraktion_m: float | None,
) -> float:
    """r_Δℓ·r_λ·r_Fz för en kant med h_SCR = h_bas(i) och d_bas(i), ekv. (302)–(305)."""
    dl_prim = gangvagsskillnad_prim_m(kant.gangvagsskillnad_m, gangvagsskillnad_refraktion_m)
    return (
        overgang_gangvag(dl_prim, vaglangd_m)
        * overgang_hojd(kant.hojd_over_baslinje_m, vaglangd_m)
        * overgang_fresnelzon(
            kant.hojd_over_baslinje_m, kant.d_bas_m, d_bas_tot_m - kant.d_bas_m, vaglangd_m
        )
    )


def skarmparameter_rscr1_allman(
    frekvens_hz: float,
    kant: Skarmkant,
    d_bas_tot_m: float,
    ljudhastighet_m_s: float,
    *,
    gangvagsskillnad_refraktion_m: float | None,
) -> float:
    """r_scr1(f) med krok för refraktion, AV 1106/07 avsnitt 5.21.1, ekv. (302)–(305).

    Med `gangvagsskillnad_refraktion_m=None` är resultatet detsamma som
    `terrang.skarmparameter_rscr1`. Annars ska argumentet vara Δℓ enligt ekv. (301), räknat
    med R_S, R_R, θ_S, θ_R ur delmodell 4 (ekv. 161–162).
    """
    vaglangd = _vaglangd_m(frekvens_hz, ljudhastighet_m_s)
    return _tre_kriterier(kant, d_bas_tot_m, vaglangd, gangvagsskillnad_refraktion_m)


def skarmparameter_rscr2(
    frekvens_hz: float,
    geo: Terranggeometri,
    primar: Skarmkant,
    sekundar: Skarmkant,
    ljudhastighet_m_s: float,
    *,
    gangvagsskillnad_refraktion_m: float | None,
) -> float:
    """r_scr2(f) för sekundärskärmen, AV 1106/07 avsnitt 5.21.2, ekv. (302)–(304), (308)–(309).

    `sekundar` kommer från `sekundar_skarmkant`. r_Δℓ2, r_λ2 och r_Fz2 räknas som för T₁ men för
    T₂; r_w ur avståndet d₁₂ mellan T₁ och T₂. `gangvagsskillnad_refraktion_m` är Δℓ enligt
    ekv. (307) ur delmodell 6 (ekv. 226–228), eller `None` i homogen atmosfär.
    """
    vaglangd = _vaglangd_m(frekvens_hz, ljudhastighet_m_s)
    d12 = langd(
        float(geo.x_m[primar.index]),
        float(geo.z_m[primar.index]),
        float(geo.x_m[sekundar.index]),
        float(geo.z_m[sekundar.index]),
    )
    return _tre_kriterier(
        sekundar, geo.d_bas_tot_m, vaglangd, gangvagsskillnad_refraktion_m
    ) * overgang_kantavstand(d12, vaglangd)


@dataclass(frozen=True)
class Sekundarkantgeometri:
    """Hjälppunkterna i AV 1106/07 figur 34 och ekv. (311)–(313).

    P₀ ligger på förlängningen av T₁₂ → W på samma avstånd från T₁₂ som S (eller R). T₁₂' är
    T₁₂:s projektion på linjen P₀T₁ och h'_SCR12 dess avstånd till linjen, positivt när T₁₂
    ligger över linjen.
    """

    x0_m: float
    z0_m: float
    x_fot_m: float
    z_fot_m: float
    hojd_m: float  # h'_SCR12
    d1_m: float  # |P₀T₁₂'|
    d2_m: float  # |T₁T₁₂'|


def sekundarkantgeometri(
    geo: Terranggeometri, primar: Skarmkant, primar_form: Skarmform, kant12: Skarmkant
) -> Sekundarkantgeometri:
    """P₀, T₁₂', h'_SCR12, d₁ och d₂, AV 1106/07 avsnitt 5.21.3, ekv. (311)–(313).

    För T₁₂ på källsidan av T₁ används S och W₁, på mottagarsidan R och W₂. NormLine i
    ekv. (312) är tryckt med linjen riktad från P₀ mot T₁; på mottagarsidan pekar den åt
    vänster och skulle ge omvänt tecken, så linjen riktas där från T₁ mot P₀ och h'_SCR12 blir
    positivt över linjen på båda sidor (se loggen P-T, Tolkningar).
    """
    if not primar_form.index_w1 < kant12.index < primar_form.index_w2:
        raise BullerFel("Den sekundära kanten ska ligga inne i primärskärmens form.")
    if kant12.index == primar.index:
        raise BullerFel("Den sekundära kanten kan inte vara T₁.")
    x_t1, z_t1 = float(geo.x_m[primar.index]), float(geo.z_m[primar.index])
    x_12, z_12 = float(geo.x_m[kant12.index]), float(geo.z_m[kant12.index])
    kallsida = kant12.index < primar.index
    w = primar_form.index_w1 if kallsida else primar_form.index_w2
    x_w, z_w = float(geo.x_m[w]), float(geo.z_m[w])
    x_e, z_e = (geo.x_s_m, geo.z_s_m) if kallsida else (geo.x_r_m, geo.z_r_m)
    skala = langd(x_12, z_12, x_e, z_e) / langd(x_12, z_12, x_w, z_w)  # ekv. (311)
    x0 = x_12 + skala * (x_w - x_12)
    z0 = z_12 + skala * (z_w - z_12)
    if kallsida:
        x_fot, z_fot, hojd = normlinje(x0, z0, x_t1, z_t1, x_12, z_12)  # ekv. (312)
    else:
        x_fot, z_fot, hojd = normlinje(x_t1, z_t1, x0, z0, x_12, z_12)
    return Sekundarkantgeometri(
        x0_m=x0,
        z0_m=z0,
        x_fot_m=x_fot,
        z_fot_m=z_fot,
        hojd_m=hojd,
        d1_m=langd(x0, z0, x_fot, z_fot),  # ekv. (313)
        d2_m=langd(x_t1, z_t1, x_fot, z_fot),
    )


def skarmparameter_rscr12(
    frekvens_hz: float,
    geo: Terranggeometri,
    primar: Skarmkant,
    primar_form: Skarmform,
    kant12: Skarmkant,
    ljudhastighet_m_s: float,
    *,
    gangvagsskillnad_refraktion_m: float | None,
) -> float:
    """r_scr12(f) för den sekundära kanten, AV 1106/07 avsnitt 5.21.3, ekv. (311)–(314).

    Sex faktorer: r_Δℓ12, r_λ12, r_Fz12 och r_w12 som för den sekundära skärmen men för T₁₂,
    samt r'_λ12 och r'_Fz12 ur höjden h'_SCR12 (ekv. 303–304 med ekv. 312–313).
    `gangvagsskillnad_refraktion_m` är Δℓ för T₁₂ med krökta strålar ur delmodell 5, eller
    `None` i homogen atmosfär.
    """
    vaglangd = _vaglangd_m(frekvens_hz, ljudhastighet_m_s)
    d12 = langd(
        float(geo.x_m[primar.index]),
        float(geo.z_m[primar.index]),
        float(geo.x_m[kant12.index]),
        float(geo.z_m[kant12.index]),
    )
    g = sekundarkantgeometri(geo, primar, primar_form, kant12)
    return (
        _tre_kriterier(kant12, geo.d_bas_tot_m, vaglangd, gangvagsskillnad_refraktion_m)
        * overgang_kantavstand(d12, vaglangd)
        * overgang_hojd(g.hojd_m, vaglangd)
        * overgang_fresnelzon(g.hojd_m, g.d1_m, g.d2_m, vaglangd)
    )


# --------------------------------------------------------------------------- sammanställning

INGEN_SKARM = "ingen skärm"
EN_SKARM_EN_KANT = "en skärm, en kant"
EN_SKARM_TVA_KANTER = "en skärm, två kanter"
TVA_SKARMAR = "två skärmar"


@dataclass(frozen=True)
class Terrangtolkning:
    """Resultatet av avsnitt 5.21.1–5.21.3 för en profil: verksamma kanter och övergångar.

    Parametrarna har ett värde per frekvens. `sekundar` och `sekundar_form` är satta bara när
    r_scr2 > 0 i något band, `sekundar_kant` bara när r_scr12 > 0 i något band; `primar` och
    `primar_form` bara när r_scr1 > 0 i något band. `delmodeller` är de skärmdelmodeller
    (4, 5, 6) som behövs i ekv. (310) och (315); tom när ingen skärm är verksam.
    """

    frekvenser_hz: Flyttal
    primar: Skarmkant | None
    primar_form: Skarmform | None
    r_scr1: Flyttal
    sekundar: Skarmkant | None
    sekundar_form: Skarmform | None
    r_scr2: Flyttal
    sekundar_kant: Skarmkant | None
    r_scr12: Flyttal
    delmodeller: tuple[int, ...]

    @property
    def skarmtyp(self) -> str:
        """Den mest sammansatta skärmtyp som är verksam i något band."""
        if 6 in self.delmodeller:
            return TVA_SKARMAR
        if 5 in self.delmodeller:
            return EN_SKARM_TVA_KANTER
        if 4 in self.delmodeller:
            return EN_SKARM_EN_KANT
        return INGEN_SKARM


def _per_frekvens(varde: float | Flyttal | None, antal: int, namn: str) -> list[float | None]:
    """Krokens värde utbrett till ett per frekvens."""
    if varde is None:
        return [None] * antal
    falt = np.asarray(varde, dtype=np.float64)
    if falt.ndim == 0:
        return [float(falt)] * antal
    if falt.shape != (antal,):
        raise BullerFel(f"{namn} ska vara ett tal eller ha ett värde per frekvens ({antal}).")
    return [float(v) for v in falt]


def tolka_terrang(
    geo: Terranggeometri,
    kanter: Skarmkanter,
    frekvenser_hz: Flyttal,
    ljudhastighet_m_s: float,
    *,
    gangvagsskillnad_t1_m: float | Flyttal | None,
    gangvagsskillnad_t2_m: float | Flyttal | None,
    gangvagsskillnad_t12_m: float | Flyttal | None,
) -> Terrangtolkning:
    """r_scr1, r_scr2, r_scr12 och skärmtypen för en profil, AV 1106/07 avsnitt 5.21.1–5.21.3.

    Ordningen följer texten: (1) r_scr1 för T₁ (ekv. 305); är den 0 i alla band finns ingen
    skärm. (2) Annars r_scr2 för T₂ (ekv. 309). (3) Är r_scr2 = 1 i alla band räcker delmodell 6;
    annars söks den sekundära kanten och r_scr12 (ekv. 314) räknas.

    De tre nyckelordsargumenten är kroken för refraktion: Δℓ med krökta strålar för respektive
    kant (ekv. 301, 307 och motsvarande för T₁₂), ett tal eller ett värde per frekvens. `None`
    anger homogen atmosfär (Δℓ = Δℓ₀). `kanter` ska komma från `valj_skarmkanter(geo)`.
    """
    frekvenser = np.asarray(frekvenser_hz, dtype=np.float64)
    antal = frekvenser.shape[0]
    noll = np.zeros(antal, dtype=np.float64)
    c = ljudhastighet_m_s
    primar, form = kanter.primar, kanter.primar_form
    if primar is None or form is None:
        return Terrangtolkning(frekvenser, None, None, noll, None, None, noll, None, noll, ())

    dl1 = _per_frekvens(gangvagsskillnad_t1_m, antal, "gangvagsskillnad_t1_m")
    r1 = np.array(
        [
            skarmparameter_rscr1_allman(
                float(f), primar, geo.d_bas_tot_m, c, gangvagsskillnad_refraktion_m=dl
            )
            for f, dl in zip(frekvenser, dl1, strict=True)
        ],
        dtype=np.float64,
    )
    if not bool(np.any(r1 > 0.0)):
        return Terrangtolkning(frekvenser, None, None, r1, None, None, noll, None, noll, ())

    r2 = noll
    sekundar = kanter.sekundar
    if sekundar is not None:
        dl2 = _per_frekvens(gangvagsskillnad_t2_m, antal, "gangvagsskillnad_t2_m")
        r2 = np.array(
            [
                skarmparameter_rscr2(
                    float(f), geo, primar, sekundar, c, gangvagsskillnad_refraktion_m=dl
                )
                for f, dl in zip(frekvenser, dl2, strict=True)
            ],
            dtype=np.float64,
        )
    tva_skarmar = bool(np.any(r2 > 0.0))
    enbart_tva_skarmar = bool(np.all(r2 == 1.0))

    r12 = noll
    kant12 = kanter.sekundar_kant
    if kant12 is not None and not enbart_tva_skarmar:
        dl12 = _per_frekvens(gangvagsskillnad_t12_m, antal, "gangvagsskillnad_t12_m")
        r12 = np.array(
            [
                skarmparameter_rscr12(
                    float(f), geo, primar, form, kant12, c, gangvagsskillnad_refraktion_m=dl
                )
                for f, dl in zip(frekvenser, dl12, strict=True)
            ],
            dtype=np.float64,
        )
    tva_kanter = bool(np.any(r12 > 0.0))

    delmodeller: list[int] = []
    if not enbart_tva_skarmar:
        if bool(np.any(r12 < 1.0)):
            delmodeller.append(4)
        if tva_kanter:
            delmodeller.append(5)
    if tva_skarmar:
        delmodeller.append(6)
    return Terrangtolkning(
        frekvenser_hz=frekvenser,
        primar=primar,
        primar_form=form,
        r_scr1=r1,
        sekundar=sekundar if tva_skarmar else None,
        sekundar_form=kanter.sekundar_form if tva_skarmar else None,
        r_scr2=r2,
        sekundar_kant=kant12 if tva_kanter else None,
        r_scr12=r12,
        delmodeller=tuple(delmodeller),
    )


def tolka_terrang_homogen(
    geo: Terranggeometri, frekvenser_hz: Flyttal, ljudhastighet_m_s: float
) -> Terrangtolkning:
    """Hela avsnitt 5.21.1–5.21.3 i homogen atmosfär (Δℓ = Δℓ₀ för alla kanter)."""
    return tolka_terrang(
        geo,
        valj_skarmkanter(geo),
        frekvenser_hz,
        ljudhastighet_m_s,
        gangvagsskillnad_t1_m=HOMOGEN_ATMOSFAR,
        gangvagsskillnad_t2_m=HOMOGEN_ATMOSFAR,
        gangvagsskillnad_t12_m=HOMOGEN_ATMOSFAR,
    )
