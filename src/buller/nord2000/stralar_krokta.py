"""Cirkelstrålar i en linjär ljudhastighetsprofil: direkt och markreflekterad stråle i Nord2000.

Källor (varje formel avläst från sidbild):

* DELTA AV 1106/07 (rev. 2014), avsnitt 5.5.4 (DirectRay, ekv. 29–44), 5.5.5 (ReflectedRay,
  ekv. 45–50) och 5.5.6 (TravelTimeDiff, ekv. 51–53).
* FORCE TC-101327 (2019), avsnitt 2.2: ny ekv. (43) för avståndet d_SZ till skuggzonen.
* DELTA AV 1851/00 (rev. 2006), avsnitt 2.1–2.3, som bakgrund (samma formler, ekv. 4–24).

Profilen är c_e(z) = c₀·(1 + ξ·z) (ekv. 16). En stråle i den är en cirkelbåge vars medelpunkt
ligger på höjden −1/ξ, där c_e = 0.

**Räkneform.** Ekv. (34)–(40) är skillnader mellan nästan lika tal när ξ är liten och kan
inte användas för ξ = 0. Här räknas samma cirkelbåge med uttryck som är algebraiskt lika men
saknar utsläckningen och har gränsvärdet rät stråle för ξ → 0 (härledning i `direkt_strale`).
Testerna jämför mot ekv. (34)–(41) bokstavligt. Regeln ξ = 10⁻¹⁰ för homogen atmosfär och
regeln Δz ≥ 0,01 i avsnitt 5.5.4 behövs därför inte och tillämpas inte.

**Uppåtbrytning (ξ < 0).** Rapporten föreskriver att ekv. (32)–(41) räknas med |ξ′| och att Δθ
byter tecken (ekv. 42). Det är en spegling som inte är exakt: gångtiden räknas då i en profil
där ljudhastigheten ökar uppåt från den nedre punkten. Samma ekvationer med ξ′ med tecken
beskriver den verkliga, uppåtkrökta bågen exakt. Båda finns, valda med `uppat`:

* `"bokstavlig"` (förval): |ξ′| och ekv. (42), som rapporten anger.
* `"exakt"`: ξ′ med tecken i ekv. (32)–(37) och ξ i stället för ξ′ i ekv. (43).

Skillnaden och skälen står i `docs/buller/logg/P-R1.md`. För ξ ≥ 0 är de två lika.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from buller.typer import BullerFel

Uppatmetod = Literal["bokstavlig", "exakt"]

# AV 1106/07 avsnitt 5.5.4–5.5.6: skuggzon antas när d > 0,95·d_SZ.
SKUGGZONSMARGINAL = 0.95

_SERIEGRANS = 1.0e-3


def _asin_genom_x(x: float) -> float:
    """arcsin(x)/x med gränsvärdet 1 i x = 0 (Maclaurinserie för små |x|)."""
    if abs(x) < _SERIEGRANS:
        x2 = x * x
        return 1.0 + x2 / 6.0 + 3.0 * x2 * x2 / 40.0
    return math.asin(x) / x


def _atanh_genom_x(x: float) -> float:
    """artanh(x)/x med gränsvärdet 1 i x = 0 (Maclaurinserie för små |x|)."""
    if abs(x) < _SERIEGRANS:
        x2 = x * x
        return 1.0 + x2 / 3.0 + x2 * x2 / 5.0
    return math.atanh(x) / x


def _kontrollera_uppat(uppat: str) -> None:
    if uppat not in ("bokstavlig", "exakt"):
        raise BullerFel(f"Okänd metod för uppåtbrytning: {uppat!r} (bokstavlig eller exakt).")


def _kontrollera_profil(xi_per_m: float, c0_m_s: float, hogsta_hojd_m: float) -> None:
    """Profilen c₀·(1 + ξ·z) ska vara positiv upp till den högsta punkten på strålen."""
    if not c0_m_s > 0.0 or not math.isfinite(xi_per_m):
        raise BullerFel("Ljudhastigheten c0 ska vara större än 0 och ξ ändlig.")
    if not 1.0 + xi_per_m * hogsta_hojd_m > 0.0:
        raise BullerFel(
            "Den linjära profilen ger ljudhastighet ≤ 0 på strålens höjd (1 + ξ·z ≤ 0): "
            "fallet täcks inte av cirkelstrålarna i AV 1106/07 avsnitt 5.5."
        )


@dataclass(frozen=True)
class Cirkelbage:
    """En cirkelstråle från en nedre punkt L till en övre punkt U (AV 1106/07 figur 7)."""

    gangtid_s: float  # τ
    vagstracka_m: float  # R
    vinkel_nedre_rad: float  # ψ_L: strålens vinkel mot horisontalplanet i L, ekv. (32)
    vinkelandring_rad: float  # ψ_L − arctan(Δz/d), ekv. (41), med tecken efter den räknade bågen


def cirkelbage(
    avstand_m: float, hojdskillnad_m: float, xi_nedre_per_m: float, c_nedre_m_s: float
) -> Cirkelbage:
    """Cirkelbågen mellan L och U, AV 1106/07 avsnitt 5.5.4, ekv. (32)–(41).

    `xi_nedre_per_m` är ξ′ och `c_nedre_m_s` är c₀′, båda hänförda till den nedre punkten L
    (ekv. 30–31); `hojdskillnad_m` är Δz = z_U − z_L ≥ 0. Tecknet på ξ′ följs: positivt ger en
    nedåtkrökt båge, negativt en uppåtkrökt.

    Räkneform (se modulens docstring). Med t = tan ψ_L enligt ekv. (32) har cirkeln radien
    1/(|ξ′|·cos ψ_L). Kordan L–U har längden ℓ = √(d² + Δz²) och lutningen α = arctan(Δz/d).
    Vinkeln mellan korda och tangent är Δθ = ψ_L − α i båda ändar, och ℓ = 2·radie·sin Δθ, så

        x = sin Δθ = ℓ·ξ′·cos ψ_L / 2,
        R = 2·Δθ·radie = ℓ·arcsin(x)/x                         (lika med ekv. 34 och 39),
        τ = [artanh(sin ψ_L) − artanh(sin θ_U)] / (ξ′·c₀′)      (lika med ekv. 35–37 och 40),

    där θ_U är strålens vinkel i U och sin θ_U = sin ψ_L − ξ′·d·cos ψ_L (negativ efter bågens
    högsta punkt, vilket ger fallet d > d_m i ekv. 38–40 utan särskild gren). Skillnaden av
    artanh skrivs som artanh(y) med y = ξ′·d·cos ψ_L / (1 − sin ψ_L·sin θ_U). För ξ′ = 0 blir
    R = ℓ och τ = ℓ/c₀′.
    """
    if not avstand_m > 0.0:
        raise BullerFel("Det horisontella avståndet ska vara större än 0.")
    if hojdskillnad_m < 0.0:
        raise BullerFel("Δz = z_U − z_L får inte vara negativ: L ska vara den nedre punkten.")
    if not c_nedre_m_s > 0.0:
        raise BullerFel("Ljudhastigheten i den nedre punkten ska vara större än 0.")
    d = avstand_m
    dz = hojdskillnad_m
    xi = xi_nedre_per_m
    if not 1.0 + xi * dz > 0.0:
        raise BullerFel("Den linjära profilen ger ljudhastighet ≤ 0 i den övre punkten.")

    tan_psi = 0.5 * xi * d + dz * (2.0 + xi * dz) / (2.0 * d)  # ekv. (32)
    cos_psi = 1.0 / math.sqrt(1.0 + tan_psi * tan_psi)
    sin_psi = tan_psi * cos_psi
    korda = math.hypot(d, dz)

    x = 0.5 * korda * xi * cos_psi
    if abs(x) > 1.0:
        raise BullerFel("Ingen cirkelbåge förbinder punkterna (|sin Δθ| > 1).")
    sin_theta_u = sin_psi - xi * d * cos_psi
    y = xi * d * cos_psi / (1.0 - sin_psi * sin_theta_u)
    if not abs(y) < 1.0:
        raise BullerFel("Gångtiden längs cirkelbågen är inte ändlig (strålen blir lodrät).")
    return Cirkelbage(
        gangtid_s=d * cos_psi / (c_nedre_m_s * (1.0 - sin_psi * sin_theta_u)) * _atanh_genom_x(y),
        vagstracka_m=korda * _asin_genom_x(x),
        vinkel_nedre_rad=math.atan(tan_psi),
        vinkelandring_rad=math.asin(x),
    )


def skuggzonsavstand_m(
    kallhojd_m: float,
    mottagarhojd_m: float,
    xi_per_m: float,
    uppat: Uppatmetod = "bokstavlig",
) -> float:
    """d_SZ: avståndet där strålen stryker marken, AV 1106/07 ekv. (43) enligt TC-101327 2.2.

    d_SZ = √(z_S·(2/|ξ′| − z_S)) + √(z_R·(2/|ξ′| − z_R)); en negativ parentes ger roten 0
    (TC 2.2). För ξ ≥ 0 är d_SZ oändligt (avsnitt 5.5.4).

    `"bokstavlig"`: ξ′ = ξ/(1 + ξ·z_L) enligt ekv. (30), med z_L den lägre av höjderna.
    `"exakt"`: ξ i stället för ξ′. En stråle som tangerar marken är en cirkel med radien 1/|ξ|
    (medelpunkten ligger där c_e = 0), och uttrycket är då summan av de två horisontella
    avstånden från tangeringspunkten.
    """
    _kontrollera_uppat(uppat)
    if kallhojd_m < 0.0 or mottagarhojd_m < 0.0:
        raise BullerFel("Höjd över mark får inte vara negativ.")
    if xi_per_m >= 0.0:
        return math.inf
    xi = xi_per_m
    if uppat == "bokstavlig":
        z_l = min(kallhojd_m, mottagarhojd_m)
        if not 1.0 + xi * z_l > 0.0:
            raise BullerFel("Den linjära profilen ger ljudhastighet ≤ 0 i den nedre punkten.")
        xi = xi / (1.0 + xi * z_l)  # ekv. (30)
    diameter = 2.0 / abs(xi)
    return math.sqrt(kallhojd_m * max(diameter - kallhojd_m, 0.0)) + math.sqrt(
        mottagarhojd_m * max(diameter - mottagarhojd_m, 0.0)
    )


@dataclass(frozen=True)
class DirektStrale:
    """Strålvariabler för den direkta strålen, AV 1106/07 avsnitt 5.5.1 och ekv. (44)."""

    avstand_m: float  # d
    gangtid_s: float  # τ
    vagstracka_m: float  # R
    vinkelandring_rad: float  # Δθ: > 0 vid nedåtbrytning, < 0 vid uppåtbrytning (ekv. 41–42)
    skuggzonsavstand_m: float  # d_SZ, oändligt för ξ ≥ 0

    @property
    def i_skuggzon(self) -> bool:
        """Sant när d > 0,95·d_SZ (AV 1106/07 avsnitt 5.5.4)."""
        return self.avstand_m > SKUGGZONSMARGINAL * self.skuggzonsavstand_m

    @property
    def medelljudhastighet_m_s(self) -> float:
        """R/τ: medelljudhastigheten längs strålen.

        Ekv. (44) anger c̄ som utdata från DirectRay utan att definiera den i avsnitt 5.5.4;
        se tolkningar i paketets logg.
        """
        return self.vagstracka_m / self.gangtid_s


def direkt_strale(
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    xi_per_m: float,
    c0_m_s: float,
    uppat: Uppatmetod = "bokstavlig",
) -> DirektStrale:
    """Funktionen DirectRay, AV 1106/07 avsnitt 5.5.4, ekv. (29)–(44), med TC-101327 2.2.

    Strålen går mellan höjderna `kallhojd_m` och `mottagarhojd_m` (källa, mottagare eller
    skärmkant) på det horisontella avståndet d i profilen c₀·(1 + ξ·z). Den lägre punkten är L
    och profilen hänförs dit: ξ′ = ξ/(1 + ξ·z_L), c₀′ = c₀·(1 + ξ·z_L) (ekv. 30–31).

    ξ < 0: `"bokstavlig"` räknar med |ξ′| och byter tecken på Δθ (ekv. 42); `"exakt"` räknar
    den uppåtkrökta bågen med ξ′ med tecken. Strålen räknas även i skuggzonen (den behövs i
    ekv. 121); se `i_skuggzon`. I läget `"exakt"` går den då delvis under markplanet.
    """
    _kontrollera_uppat(uppat)
    if kallhojd_m < 0.0 or mottagarhojd_m < 0.0:
        raise BullerFel("Höjd över mark får inte vara negativ.")
    z_l = min(kallhojd_m, mottagarhojd_m)
    z_u = max(kallhojd_m, mottagarhojd_m)
    _kontrollera_profil(xi_per_m, c0_m_s, z_u)
    xi_nedre = xi_per_m / (1.0 + xi_per_m * z_l)  # ekv. (30)
    c_nedre = c0_m_s * (1.0 + xi_per_m * z_l)  # ekv. (31)
    if xi_per_m < 0.0 and uppat == "bokstavlig":
        bage = cirkelbage(avstand_m, z_u - z_l, abs(xi_nedre), c_nedre)
        vinkelandring = -bage.vinkelandring_rad  # ekv. (42)
    else:
        bage = cirkelbage(avstand_m, z_u - z_l, xi_nedre, c_nedre)
        vinkelandring = bage.vinkelandring_rad
    return DirektStrale(
        avstand_m=avstand_m,
        gangtid_s=bage.gangtid_s,
        vagstracka_m=bage.vagstracka_m,
        vinkelandring_rad=vinkelandring,
        skuggzonsavstand_m=skuggzonsavstand_m(kallhojd_m, mottagarhojd_m, xi_per_m, uppat),
    )


def _kubisk(u: float, xi: float, p: float, q: float) -> float:
    return (2.0 * xi * u * u + p) * u + q


def _nollstalle(xi: float, p: float, q: float, u_a: float, u_b: float) -> float:
    """Nollstället till 2ξu³ + pu + q i [u_a, u_b] med säkrad Newton–Raphson + halvering."""
    two_xi = 2.0 * xi
    six_xi = 6.0 * xi
    f_a = (two_xi * u_a * u_a + p) * u_a + q
    f_b = (two_xi * u_b * u_b + p) * u_b + q
    if f_a == 0.0:
        return u_a
    if f_b == 0.0:
        return u_b
    if (f_a > 0.0) == (f_b > 0.0):
        raise BullerFel("Reflexpunktens ekvation (49) saknar teckenbyte i sökintervallet.")
    fa_pos = f_a > 0.0
    u = 0.5 * (u_a + u_b)
    for _ in range(100):
        f_u = (two_xi * u * u + p) * u + q
        if f_u == 0.0:
            return u
        if (f_u > 0.0) == fa_pos:
            u_a = u
        else:
            u_b = u
        # Säkrad Newton-steg inom (u_a, u_b), annars intervallhalvering
        df = six_xi * u * u + p
        if df != 0.0:
            u_ny = u - f_u / df
            if u_a < u_ny < u_b and abs(u_ny - u) <= 0.5 * (u_b - u_a):
                if abs(u_ny - u) <= 1e-15 * max(1.0, abs(u_ny)):
                    return u_ny
                u = u_ny
                continue
        u_m = 0.5 * (u_a + u_b)
        if u_m in (u_a, u_b):
            return u_m
        u = u_m
    return 0.5 * (u_a + u_b)


def reflexpunkter_m(
    avstand_m: float, kallhojd_m: float, mottagarhojd_m: float, xi_per_m: float
) -> tuple[float, ...]:
    """Alla lösningar d_refl till ekv. (49) i intervallet [0, d], i växande ordning.

    AV 1106/07 avsnitt 5.5.5, ekv. (49): 2x³ − 3dx² + (b_R² + b_S² + d²)x − b_S²d = 0 med
    b² = z·(2 + ξz)/ξ. Ekvationen multipliceras här med ξ (så att den även gäller för ξ = 0) och
    skrivs om med u = x − d/2, vilket tar bort andragradstermen:

        2ξu³ + (β_S + β_R − ξd²/2)·u + d·(β_R − β_S)/2 = 0,    β = z·(2 + ξz).

    Vänsterledet är negativt i x = 0 och positivt i x = d, så det finns alltid minst en lösning
    mellan källa och mottagare. Intervallet delas vid derivatans nollställen och varje del med
    teckenbyte löses med intervallhalvering. ξ används med tecken; för ξ < 0 finns då precis en
    lösning i intervallet (ekvationens två övriga rötter ligger utanför).
    """
    if not avstand_m > 0.0:
        raise BullerFel("Det horisontella avståndet ska vara större än 0.")
    if not kallhojd_m > 0.0 or not mottagarhojd_m > 0.0:
        raise BullerFel("Reflexpunkten kräver källa och mottagare över marken (höjd > 0).")
    d = avstand_m
    xi = xi_per_m
    beta_s = kallhojd_m * (2.0 + xi * kallhojd_m)
    beta_r = mottagarhojd_m * (2.0 + xi * mottagarhojd_m)
    if not beta_s > 0.0 or not beta_r > 0.0:
        raise BullerFel(
            "Källa eller mottagare ligger över 2/|ξ| vid uppåtbrytning: ingen markreflekterad "
            "stråle finns (skuggzon)."
        )
    p = beta_s + beta_r - 0.5 * xi * d * d
    q = 0.5 * d * (beta_r - beta_s)
    halv = 0.5 * d

    granser = [-halv, halv]
    if xi != 0.0 and -p / (6.0 * xi) > 0.0:
        u_extrem = math.sqrt(-p / (6.0 * xi))
        if u_extrem < halv:
            granser = [-halv, -u_extrem, u_extrem, halv]

    rotter: list[float] = []
    for u_a, u_b in zip(granser[:-1], granser[1:], strict=True):
        f_a = _kubisk(u_a, xi, p, q)
        f_b = _kubisk(u_b, xi, p, q)
        if f_a == 0.0 or f_b == 0.0 or (f_a > 0.0) != (f_b > 0.0):
            rot = _nollstalle(xi, p, q, u_a, u_b) + halv
            if not rotter or rot > rotter[-1]:
                rotter.append(rot)
    if not rotter:
        raise BullerFel(
            "Reflexpunktens ekvation (49) gav ingen lösning mellan källa och mottagare."
        )
    return tuple(rotter)


def reflexpunkt_m(
    avstand_m: float, kallhojd_m: float, mottagarhojd_m: float, xi_per_m: float
) -> float:
    """d_refl: avståndet från källan till reflexpunkten, AV 1106/07 avsnitt 5.5.5, ekv. (49).

    Finns flera lösningar väljs den närmast källan när h_S < h_R och den närmast mottagaren när
    h_S > h_R (avsnitt 5.5.5). För h_S = h_R anger rapporten inget val; då används d/2, som
    alltid är en lösning och den enda som är oförändrad när källa och mottagare byter plats.
    """
    rotter = reflexpunkter_m(avstand_m, kallhojd_m, mottagarhojd_m, xi_per_m)
    if kallhojd_m < mottagarhojd_m:
        return rotter[0]
    if kallhojd_m > mottagarhojd_m:
        return rotter[-1]
    return 0.5 * avstand_m


@dataclass(frozen=True)
class ReflekteradStrale:
    """Strålvariabler för den markreflekterade strålen, AV 1106/07 avsnitt 5.5.1 och ekv. (50)."""

    gangtid_s: float  # τ = τ_S + τ_R
    vagstracka_m: float  # R = R_S + R_R
    vagstracka_kalla_m: float  # R_S
    vagstracka_mottagare_m: float  # R_R
    gangtid_kalla_s: float  # τ_S
    gangtid_mottagare_s: float  # τ_R
    vinkelandring_kalla_rad: float  # Δθ_S, ekv. (47)
    vinkelandring_mottagare_rad: float  # Δθ_R, ekv. (48)
    strykvinkel_rad: float  # ψ_G
    d_refl_m: float  # avstånd från källan till reflexpunkten


def reflekterad_strale(
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    xi_per_m: float,
    c0_m_s: float,
    uppat: Uppatmetod = "bokstavlig",
) -> ReflekteradStrale:
    """Funktionen ReflectedRay, AV 1106/07 avsnitt 5.5.5, ekv. (45)–(50).

    Reflexpunkten P bestäms med ekv. (49). Strålens två delar räknas var för sig från P (på
    marken, z_L = 0) till källan respektive mottagaren med DirectRay (ekv. 45–46). Δθ_S och
    Δθ_R följer av ekv. (47)–(48).

    Strykvinkeln ψ_G är ψ_L i P enligt ekv. (32) med ξ med tecken. Rapporten anger att ψ_L är
    densamma för de två delarna; det gäller för ekv. (32) med ξ med tecken (det är villkoret som
    ekv. 49 uttrycker) men inte med |ξ|. Därför räknas ψ_G så i båda lägena, medan R_S, R_R, τ_S
    och τ_R i läget `"bokstavlig"` räknas med |ξ| när ξ < 0.

    I skuggzonen (ξ < 0 och d > 0,95·d_SZ) ska strålen inte bestämmas: `BullerFel`.
    """
    _kontrollera_uppat(uppat)
    if not kallhojd_m > 0.0 or not mottagarhojd_m > 0.0:
        raise BullerFel("Den reflekterade strålen kräver källa och mottagare över marken.")
    _kontrollera_profil(xi_per_m, c0_m_s, max(kallhojd_m, mottagarhojd_m))
    d_sz = skuggzonsavstand_m(kallhojd_m, mottagarhojd_m, xi_per_m, uppat)
    if avstand_m > SKUGGZONSMARGINAL * d_sz:
        raise BullerFel(
            "Mottagaren ligger i skuggzonen (d > 0,95·d_SZ): den reflekterade strålen ska inte "
            "bestämmas (AV 1106/07 avsnitt 5.5.5)."
        )
    d_s = reflexpunkt_m(avstand_m, kallhojd_m, mottagarhojd_m, xi_per_m)
    d_r = avstand_m - d_s
    if not d_s > 0.0 or not d_r > 0.0:
        raise BullerFel("Reflexpunkten sammanfaller med källans eller mottagarens fotpunkt.")

    xi_bage = abs(xi_per_m) if uppat == "bokstavlig" else xi_per_m
    del_s = cirkelbage(d_s, kallhojd_m, xi_bage, c0_m_s)  # ekv. (45)
    del_r = cirkelbage(d_r, mottagarhojd_m, xi_bage, c0_m_s)  # ekv. (46)

    # ψ_G = ψ_L i P, ekv. (32) med ξ med tecken; medelvärdet av de två delarnas (lika) värden.
    tan_psi_s = 0.5 * xi_per_m * d_s + kallhojd_m * (2.0 + xi_per_m * kallhojd_m) / (2.0 * d_s)
    tan_psi_r = 0.5 * xi_per_m * d_r + mottagarhojd_m * (2.0 + xi_per_m * mottagarhojd_m) / (
        2.0 * d_r
    )
    strykvinkel = math.atan(0.5 * (tan_psi_s + tan_psi_r))
    rat_vinkel = math.atan((kallhojd_m + mottagarhojd_m) / avstand_m)
    return ReflekteradStrale(
        gangtid_s=del_s.gangtid_s + del_r.gangtid_s,
        vagstracka_m=del_s.vagstracka_m + del_r.vagstracka_m,
        vagstracka_kalla_m=del_s.vagstracka_m,
        vagstracka_mottagare_m=del_r.vagstracka_m,
        gangtid_kalla_s=del_s.gangtid_s,
        gangtid_mottagare_s=del_r.gangtid_s,
        vinkelandring_kalla_rad=strykvinkel
        + rat_vinkel
        - 2.0 * math.atan(kallhojd_m / d_s),  # ekv. (47)
        vinkelandring_mottagare_rad=strykvinkel
        + rat_vinkel
        - 2.0 * math.atan(mottagarhojd_m / d_r),  # ekv. (48)
        strykvinkel_rad=strykvinkel,
        d_refl_m=d_s,
    )


def storsta_gangtidsskillnad_s(
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    skuggzonsavstand_m: float,
    ljudhastighet_mark_m_s: float,
) -> float:
    """Δτ₀: övre gräns för Δτ vid uppåtbrytning, AV 1106/07 avsnitt 5.5.6, ekv. (52).

    Δτ₀ = (1 − (d/d_SZ)²)·(√(d² + (h_S + h_R)²) − √(d² + (h_S − h_R)²))/C, där C är
    ljudhastigheten vid marken (ekv. 3). Skillnaden mellan rötterna räknas som
    4·h_S·h_R/(summan av rötterna), vilket är samma tal utan utsläckning.
    """
    if not ljudhastighet_mark_m_s > 0.0 or not skuggzonsavstand_m > 0.0:
        raise BullerFel("Ljudhastigheten vid marken och d_SZ ska vara större än 0.")
    r2 = math.hypot(avstand_m, kallhojd_m + mottagarhojd_m)
    r1 = math.hypot(avstand_m, kallhojd_m - mottagarhojd_m)
    vagskillnad = 4.0 * kallhojd_m * mottagarhojd_m / (r1 + r2)
    return (1.0 - (avstand_m / skuggzonsavstand_m) ** 2) * vagskillnad / ljudhastighet_mark_m_s


def gangtidsskillnad_s(
    gangtid_reflekterad_s: float,
    gangtid_direkt_s: float,
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    xi_per_m: float,
    skuggzonsavstand_m: float,
    ljudhastighet_mark_m_s: float,
) -> float:
    """Funktionen TravelTimeDiff, AV 1106/07 avsnitt 5.5.6, ekv. (51)–(53).

    Δτ = τ₂ − τ₁ (ekv. 51). För ξ < 0 begränsas Δτ uppåt till Δτ₀ (ekv. 52), och i skuggzonen
    (d > 0,95·d_SZ) är Δτ = 0. `skuggzonsavstand_m` är d_SZ från DirectRay.
    """
    if xi_per_m < 0.0 and avstand_m > SKUGGZONSMARGINAL * skuggzonsavstand_m:
        return 0.0
    skillnad = gangtid_reflekterad_s - gangtid_direkt_s  # ekv. (51)
    if xi_per_m < 0.0:
        grans = storsta_gangtidsskillnad_s(
            avstand_m, kallhojd_m, mottagarhojd_m, skuggzonsavstand_m, ljudhastighet_mark_m_s
        )
        if skillnad > grans:
            return grans
    return skillnad


@dataclass(frozen=True)
class StralarKroktPlanMark:
    """Direkt och reflekterad stråle över plan mark i en linjär profil, samt Δτ.

    `reflekterad` är `None` när mottagaren ligger i skuggzonen (ξ < 0 och d > 0,95·d_SZ).
    """

    direkt: DirektStrale
    reflekterad: ReflekteradStrale | None
    gangtidsskillnad_s: float  # Δτ enligt TravelTimeDiff; 0 i skuggzonen

    @property
    def i_skuggzon(self) -> bool:
        return self.reflekterad is None


def stralar_krokt_plan_mark(
    avstand_m: float,
    kallhojd_m: float,
    mottagarhojd_m: float,
    xi_per_m: float,
    c0_m_s: float,
    ljudhastighet_mark_m_s: float,
    uppat: Uppatmetod = "bokstavlig",
) -> StralarKroktPlanMark:
    """DirectRay, ReflectedRay och TravelTimeDiff i följd, som i AV 1106/07 ekv. (116)–(117).

    `xi_per_m` och `c0_m_s` är den ekvivalenta profilen (CalcEqSSP eller CalcEqSSPGround) och
    `ljudhastighet_mark_m_s` är C i ekv. (52). Motsvarar `geometri.stralar_plan_mark` och ger
    samma strålar för ξ = 0.
    """
    direkt = direkt_strale(avstand_m, kallhojd_m, mottagarhojd_m, xi_per_m, c0_m_s, uppat)
    if xi_per_m < 0.0 and direkt.i_skuggzon:
        return StralarKroktPlanMark(direkt=direkt, reflekterad=None, gangtidsskillnad_s=0.0)
    reflekterad = reflekterad_strale(avstand_m, kallhojd_m, mottagarhojd_m, xi_per_m, c0_m_s, uppat)
    skillnad = gangtidsskillnad_s(
        reflekterad.gangtid_s,
        direkt.gangtid_s,
        avstand_m,
        kallhojd_m,
        mottagarhojd_m,
        xi_per_m,
        direkt.skuggzonsavstand_m,
        ljudhastighet_mark_m_s,
    )
    return StralarKroktPlanMark(direkt=direkt, reflekterad=reflekterad, gangtidsskillnad_s=skillnad)
