"""Kilytornas reflektionskoefficienter i skärmdelmodellerna: FORCE TC-101327 bilaga G.1, G.2, G.5.

Källa: FORCE TC-101327 (2019) avsnitt 2.3, 2.8 och 2.16 (bilaga G till AV 1106/07):

* G.1 (ekv. 404–405): den reella ekvivalenta reflektionskoefficienten Q_e med nedre gräns −0,369.
* G.2 (ekv. 406–421): det vägda medelvärdet Q_avR (och Q_avS) för en skärm med en kant.
* G.5 (ekv. 440): Q_e ur en energireflektionskoefficient (valfri förenkling för byggda ytor).

Bilagan ersätter AV 1106/07 avsnitt 5.13.2:s val Z_S = Z_iSCR,T−1 och Z_R = Z_iSCR,T (impedansen
hos segmentet närmast kanten på var sida) och därmed Q₂–Q₄ enligt ekv. (80) i `pwedge`.
Markreflexerna före och efter skärmen (Q̂₁, Q̂₂ i ekv. 169 och 173) ändras inte.

Bilagan beskriver mottagarsidan (T, W₂, R_G) och säger att källsidan räknas på samma sätt. Här
ges kilbenet som en polylinje som börjar i T och går utåt; källsidan speglas av anroparen
(`delmodell4.kilben_ur_terrang`). Storheter som bilagan lämnar odefinierade (α, h_RG, θ i
ekv. 416, "highest numbered row") är tolkade enligt `docs/buller/logg/P-4.md`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from buller.nord2000.fresnel import calc_fzd
from buller.nord2000.geometri import langd, normlinje
from buller.nord2000.mark import markimpedans, sfarisk_reflektion
from buller.typer import BullerFel

# TC-101327 bilaga G.1, stycket efter ekv. (405): nedre gräns för Q_e.
EKVIVALENT_REFLEKTION_MIN = -0.369
# TC-101327 bilaga G.2, ekv. (418): F·λ med F = 1/8, 250 Hz och 340 m/s.
F_LAMBDA_KILBEN_M = 0.17


def ekvivalent_reell_reflektion(
    frekvens_hz: float,
    strykvinkel_rad: float,
    avstand_m: float,
    flodesresistivitet_kNsm4: float,
    ljudhastighet_m_s: float,
) -> float:
    """Q_e(f, ψ_G, R, Z_G), TC-101327 bilaga G.1, ekv. (404)–(405), med nedre gräns −0,369.

    Q_e = |1 + Q_c| − 1, där Q_c är den sfäriska reflektionskoefficienten (AV 1106/07 ekv. 58)
    för strykvinkeln ψ_G och avståndet R mellan skärmtopp och mottagare (eller källa).
    Gångtiden i ekv. (58)–(60) är R/c. Oändlig flödesresistivitet (hård yta) ger Q_e = 1.
    """
    if not avstand_m > 0.0 or not ljudhastighet_m_s > 0.0:
        raise BullerFel("Q_e kräver avstånd och ljudhastighet större än 0.")
    if math.isinf(flodesresistivitet_kNsm4) and flodesresistivitet_kNsm4 > 0.0:
        return 1.0
    impedans = markimpedans(frekvens_hz, flodesresistivitet_kNsm4)
    q_c = sfarisk_reflektion(frekvens_hz, avstand_m / ljudhastighet_m_s, strykvinkel_rad, impedans)
    return max(abs(1.0 + q_c) - 1.0, EKVIVALENT_REFLEKTION_MIN)  # ekv. (405)


def ekvivalent_reflektion_ur_energireflektion(energireflektion: float) -> float:
    """Q_e = √ρ_E = √(1 − α), TC-101327 bilaga G.5, ekv. (440).

    Valfri förenkling när kilytan bara är känd genom sin energireflektionskoefficient ρ_E
    (byggda, brant stående ytor). Bilagan avråder från den för naturlig mark.
    """
    if not 0.0 <= energireflektion <= 1.0:
        raise BullerFel("Energireflektionskoefficienten ska ligga i [0, 1].")
    return math.sqrt(energireflektion)


@dataclass(frozen=True)
class Kilbensrad:
    """En rad i impedansprofilen, TC-101327 tabell G.1 och "Table 2"."""

    flodesresistivitet_kNsm4: float  # σ_i
    skalad_langd_m: float  # l'_i
    slut_fran_topp_m: float  # d_i: avstånd från T till radens slut längs det förlängda kilbenet


@dataclass(frozen=True)
class Kilben:
    """Geometri och impedansvikter för ett kilben, TC-101327 bilaga G.2 (figur G.1).

    `impedansvikter` är tabellen "Table 3": par (σ_j, w_j) med Σ w_j = 1.
    """

    d_w_m: float  # |TW|, ekv. (406)
    d_t_m: float  # ekv. (410)
    h_g_m: float  # h_RG: markpunktens avstånd till kilytans förlängning (positivt på luftsidan)
    alfa_rad: float  # vinkeln α i W mellan kilytans förlängning och linjen W–G
    strykvinkel_rad: float  # ψ_G, ekv. (417)
    avstand_m: float  # l_TRG = r_R, ekv. (416)
    d_fz_m: float  # ekv. (414)
    rader: tuple[Kilbensrad, ...]  # efter avkortning vid d_FZ
    impedansvikter: tuple[tuple[float, float], ...]


def kilben(
    x_m: Sequence[float],
    z_m: Sequence[float],
    flodesresistivitet_kNsm4: Sequence[float],
    antal_kilsegment: int,
) -> Kilben:
    """Impedansprofil och vikter för ett kilben, TC-101327 bilaga G.2, ekv. (406)–(420).

    `x_m`, `z_m` är terrängpunkterna från skärmtoppen T (index 0) över skärmformens slut W
    (index `antal_kilsegment`) till markpunkten G (sista punkten; R_G eller S_G), i en ordning
    där x växer (källsidan speglas av anroparen). `flodesresistivitet_kNsm4[i]` hör till
    segmentet mellan punkt i och i + 1.

    Segmenten i kilformen skalas med r_w = d_w/l_w (ekv. 406–409) och terrängens segment med
    r_t = d_t/l_t (ekv. 410–413), så att de avbildas på det förlängda kilbenet T–G′. Profilen
    kortas vid d_FZ = CalcFZd(0, l_TRG, ψ_G, 0,17) (ekv. 414–418) och vikterna är
    w_i = l'_i/Σl' (ekv. 419–420), summerade per flödesresistivitet.

    Tolkningar (bilagan definierar inte storheterna i text): α är vinkeln i W mellan kilytans
    förlängning och W→G, positiv när G ligger på luftsidan; h_RG är G:s vinkelräta avstånd till
    linjen genom T och W med samma tecken; villkoret "θ ≥ 0" i ekv. (416) läses som h_RG ≥ 0;
    raden som kortas är den första vars slut når d_FZ.
    """
    n = len(x_m)
    if len(z_m) != n or len(flodesresistivitet_kNsm4) != n - 1 or n < 2:
        raise BullerFel("Kilbenet kräver minst två punkter och en flödesresistivitet per segment.")
    if not 1 <= antal_kilsegment <= n - 1:
        raise BullerFel("Antalet segment i kilformen ska vara minst 1 och högst alla segment.")
    langder = [langd(x_m[i], z_m[i], x_m[i + 1], z_m[i + 1]) for i in range(n - 1)]
    if not all(ell > 0.0 for ell in langder):
        raise BullerFel("Kilbenets segment ska ha längd större än 0.")
    x_t, z_t = x_m[0], z_m[0]
    x_w, z_w = x_m[antal_kilsegment], z_m[antal_kilsegment]
    x_g, z_g = x_m[n - 1], z_m[n - 1]

    d_w = langd(x_t, z_t, x_w, z_w)  # ekv. (406)
    l_w = sum(langder[:antal_kilsegment])  # ekv. (407)
    r_w = d_w / l_w  # ekv. (408)
    skalade = [r_w * ell for ell in langder[:antal_kilsegment]]  # ekv. (409)

    l_t = sum(langder[antal_kilsegment:])  # ekv. (411)
    if l_t > 0.0:
        x_fot, z_fot, h_g = normlinje(x_t, z_t, x_w, z_w, x_g, z_g)
        # Läget längs kilytans förlängning, räknat från W: |WG|·cos α.
        langs = ((x_fot - x_w) * (x_w - x_t) + (z_fot - z_w) * (z_w - z_t)) / d_w
        alfa = math.atan2(h_g, langs)
        if alfa >= math.pi / 2.0:  # ekv. (410)
            d_t = 0.0
        elif alfa > 0.0:
            d_t = langs
        else:
            d_t = langd(x_w, z_w, x_g, z_g)
        r_t = d_t / l_t  # ekv. (412)
        skalade += [r_t * ell for ell in langder[antal_kilsegment:]]  # ekv. (413)
    else:
        h_g, alfa, d_t = 0.0, 0.0, 0.0

    langs_tot = d_w + d_t
    if h_g >= 0.0:
        avstand = math.hypot(langs_tot, h_g)  # ekv. (416)
        strykvinkel = math.atan(h_g / langs_tot)  # ekv. (417)
    else:
        avstand = langs_tot
        strykvinkel = 0.0
    d_fz = calc_fzd(0.0, avstand, strykvinkel, F_LAMBDA_KILBEN_M)  # ekv. (414)–(415), (418)

    rader: list[Kilbensrad] = []
    slut = 0.0
    for sigma, l_prim in zip(flodesresistivitet_kNsm4, skalade, strict=True):
        start = slut
        slut = start + l_prim
        if d_fz <= slut and d_fz < langs_tot:  # "Table 2": raden kortas, resten stryks
            rader.append(Kilbensrad(float(sigma), d_fz - start, d_fz))
            break
        rader.append(Kilbensrad(float(sigma), l_prim, slut))

    l_tot = sum(rad.skalad_langd_m for rad in rader)  # ekv. (419)
    if not l_tot > 0.0:
        raise BullerFel("Kilbenets skalade längd blev inte större än 0 (TC-101327 ekv. 419).")
    vikter: dict[float, float] = {}
    for rad in rader:  # ekv. (420) och sammanslagningen till "Table 3"
        sigma = rad.flodesresistivitet_kNsm4
        vikter[sigma] = vikter.get(sigma, 0.0) + rad.skalad_langd_m / l_tot
    return Kilben(
        d_w_m=d_w,
        d_t_m=d_t,
        h_g_m=h_g,
        alfa_rad=alfa,
        strykvinkel_rad=strykvinkel,
        avstand_m=avstand,
        d_fz_m=d_fz,
        rader=tuple(rader),
        impedansvikter=tuple(vikter.items()),
    )


def medelreflektion_kilben(frekvens_hz: float, ben: Kilben, ljudhastighet_m_s: float) -> float:
    """Q_avR(f) eller Q_avS(f) = Σ w_j·Q_e(f, ψ_G, l_TRG, σ_j), TC-101327 bilaga G.2, ekv. (421)."""
    return sum(
        vikt
        * ekvivalent_reell_reflektion(
            frekvens_hz, ben.strykvinkel_rad, ben.avstand_m, sigma, ljudhastighet_m_s
        )
        for sigma, vikt in ben.impedansvikter
    )
