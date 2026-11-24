"""Diffraktion vid en kil med ändlig ytimpedans i Nord2000 (pwedge, Dwedge, pwedge0, Dwedge0).

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.7.1 (ekv. 78–91), 5.7.2 (ekv. 92–94) och 5.7.5
(ekv. 105–107), hjälpfunktionerna H (5.23.8, ekv. 354) och Sign (5.23.17, ekv. 388), samt
FORCE TC-101327 (2019) avsnitt 2.3. Dubbel diffraktion (5.7.3–5.7.4) ingår inte.

Storheter som i figur 9: kilens topp T, kilvinkeln β mätt utanför kilen från mottagarsidans
yta till källsidans yta, diffraktionsvinklarna θ_S och θ_R mätta från mottagarsidans yta,
sträckorna R_S (S–T) och R_R (T–R) med gångtiderna τ_S och τ_R, samt hela sträckan ℓ och
gångtiden τ över toppen. Trycket är normerat så att fritt fält på avståndet R är
exp(jωτ)/R; tidskonventionen är densamma som i `buller.nord2000.mark`.

Tre val avviker från den tryckta texten och står under "Tolkningar" i
`docs/buller/logg/P-K1.md`:

* Ekv. (80) är tryckt med Q₂ = Q_S och Q₃ = Q_R, och villkoren före ekv. (89) och (90) är
  tryckta med θ₃ respektive θ₂. Här används Q₂ = Q_R, Q₃ = Q_S och villkoren θ₂ < π för
  ekv. (89) och θ₃ < π för ekv. (90), som i AV 1849/00 bilaga H. Det är också det enda som
  stämmer med ekv. (89)–(90) själva och som ger ett kontinuerligt fält över reflexgränserna.
* Regeln om ε = 10⁻⁸ (sist i avsnitt 5.7.1) tillämpas på θ_n innan både diffraktionstermen
  och villkoren θ_n < π räknas.
* β = π tillåts som gränsvärde, fast texten anger β > π.
"""

from __future__ import annotations

import cmath
import math
from collections.abc import Callable
from dataclasses import dataclass

from buller.nord2000.frekvenser import heaviside
from buller.nord2000.mark import sfarisk_reflektion
from buller.typer import BullerFel

EPSILON_RAD = 1.0e-8  # AV 1106/07 avsnitt 5.7.1, stycket efter tabell 5
_VINKELTOLERANS_RAD = 1.0e-12  # avrundning i vinklar som anroparen räknat fram
_PI_DIV_SQRT2_EXP_I_PI4 = (math.pi / math.sqrt(2.0)) * cmath.exp(1j * math.pi / 4.0)

# AV 1106/07 tabell 4: koefficienter a₀ … a₁₂ i polynomet för f(x), x < 5 (ekv. 85).
_F_KOEFFICIENTER: tuple[float, ...] = (
    0.49997531354311,
    0.00185249867385,
    -0.80731059547652,
    1.15348730691625,
    -0.89550049255859,
    0.44933436012454,
    -0.15130803310630,
    0.03357197760359,
    -0.00447236493671,
    0.00023357512010,
    0.00002262763737,
    -0.00000418231569,
    0.00000019048125,
)

# AV 1106/07 tabell 5: koefficienter a₀ … a₁₂ i polynomet för g(x), x < 5 (ekv. 86).
_G_KOEFFICIENTER: tuple[float, ...] = (
    0.50002414586702,
    -1.00151717179967,
    0.80070190014386,
    -0.06004025873978,
    -0.50298686904881,
    0.55984929401694,
    -0.33675804584105,
    0.13198388204736,
    -0.03513592318103,
    0.00631958394266,
    -0.00073624261723,
    0.00005018358067,
    -0.00000151974284,
)


@dataclass(frozen=True)
class Kil:
    """Indata till kildiffraktionen, AV 1106/07 avsnitt 5.7.1 (punktlistan och figur 9).

    Normalt är τ = τ_S + τ_R och ℓ = R_S + R_R; för tjock skärm och två skärmar (5.7.3–5.7.4)
    anges de var för sig. Vinklarna får ligga utanför 0 ≤ θ_R ≤ θ_S ≤ β ≤ 2π i de fall som
    schemat sist i avsnitt 5.7.1 täcker (spegelkälla eller spegelmottagare inne i kilen);
    funktionerna i modulen tillämpar schemat själva.
    """

    beta_rad: float
    theta_s_rad: float
    theta_r_rad: float
    gangtid_s: float  # τ
    gangtid_kalla_s: float  # τ_S
    gangtid_mottagare_s: float  # τ_R
    vag_m: float  # ℓ
    vag_kalla_m: float  # R_S
    vag_mottagare_m: float  # R_R

    def __post_init__(self) -> None:
        varden = (
            self.beta_rad,
            self.theta_s_rad,
            self.theta_r_rad,
            self.gangtid_s,
            self.gangtid_kalla_s,
            self.gangtid_mottagare_s,
            self.vag_m,
            self.vag_kalla_m,
            self.vag_mottagare_m,
        )
        if not all(math.isfinite(v) for v in varden):
            raise BullerFel("Kilens vinklar, gångtider och sträckor ska vara ändliga tal.")
        positiva = varden[3:]
        if not all(v > 0.0 for v in positiva):
            raise BullerFel("Kilens gångtider och sträckor ska vara större än 0.")


def kil_rata_stralar(
    beta_rad: float,
    theta_s_rad: float,
    theta_r_rad: float,
    vag_kalla_m: float,
    vag_mottagare_m: float,
    ljudhastighet_m_s: float,
) -> Kil:
    """Kil i homogen atmosfär: τ_S = R_S/c, τ_R = R_R/c, ℓ = R_S + R_R och τ = ℓ/c.

    Det är indata enligt AV 1106/07 avsnitt 5.7.1 med räta strålar (figur 9) och den normala
    definitionen τ = τ_S + τ_R, ℓ = R_S + R_R.
    """
    if not ljudhastighet_m_s > 0.0:
        raise BullerFel("Ljudhastigheten ska vara större än 0 m/s.")
    vag_m = vag_kalla_m + vag_mottagare_m
    return Kil(
        beta_rad=beta_rad,
        theta_s_rad=theta_s_rad,
        theta_r_rad=theta_r_rad,
        gangtid_s=vag_m / ljudhastighet_m_s,
        gangtid_kalla_s=vag_kalla_m / ljudhastighet_m_s,
        gangtid_mottagare_s=vag_mottagare_m / ljudhastighet_m_s,
        vag_m=vag_m,
        vag_kalla_m=vag_kalla_m,
        vag_mottagare_m=vag_mottagare_m,
    )


@dataclass(frozen=True)
class Kiltryck:
    """Tryckets delar enligt AV 1106/07 ekv. (87)–(90): diffrakterat, direkt och reflekterat."""

    diffrakterat: complex  # p_diffr, ekv. (78) eller (105)
    direkt: complex  # exp(jωτ₁)/R₁ när θ₁ < π, annars 0
    reflekterat: complex  # sista termen i ekv. (89) eller (90), annars 0

    @property
    def totalt(self) -> complex:
        """p enligt ekv. (87), (88), (89) eller (90)."""
        return self.diffrakterat + self.direkt + self.reflekterat


def tecken(x: float) -> float:
    """Funktionen Sign, AV 1106/07 avsnitt 5.23.17, ekv. (388)."""
    if x > 0.0:
        return 1.0
    if x < 0.0:
        return -1.0
    return 0.0


def _polynom(koefficienter: tuple[float, ...], x: float) -> float:
    """Σ aₙ·xⁿ räknad med Horners schema."""
    summa = 0.0
    for a in reversed(koefficienter):
        summa = summa * x + a
    return summa


def fresnel_f(x: float) -> float:
    """Hjälpfunktionen f(x) för x ≥ 0, AV 1106/07 avsnitt 5.7.1, ekv. (85) och tabell 4."""
    if x < 0.0:
        raise BullerFel("f(x) är bara definierad för x ≥ 0 (ekv. 84 anropar med |B|).")
    if x >= 5.0:
        return 1.0 / (math.pi * x)
    return (
        (
            (
                (
                    (
                        (
                            (
                                (
                                    (
                                        (
                                            (0.00000019048125 * x - 0.00000418231569) * x
                                            + 0.00002262763737
                                        )
                                        * x
                                        + 0.00023357512010
                                    )
                                    * x
                                    - 0.00447236493671
                                )
                                * x
                                + 0.03357197760359
                            )
                            * x
                            - 0.15130803310630
                        )
                        * x
                        + 0.44933436012454
                    )
                    * x
                    - 0.89550049255859
                )
                * x
                + 1.15348730691625
            )
            * x
            - 0.80731059547652
        )
        * x
        + 0.00185249867385
    ) * x + 0.49997531354311


def fresnel_g(x: float) -> float:
    """Hjälpfunktionen g(x) för x ≥ 0, AV 1106/07 avsnitt 5.7.1, ekv. (86) och tabell 5."""
    if x < 0.0:
        raise BullerFel("g(x) är bara definierad för x ≥ 0 (ekv. 84 anropar med |B|).")
    if x >= 5.0:
        return 1.0 / (math.pi**2 * x**3)
    return (
        (
            (
                (
                    (
                        (
                            (
                                (
                                    (
                                        (
                                            (-0.00000151974284 * x + 0.00005018358067) * x
                                            - 0.00073624261723
                                        )
                                        * x
                                        + 0.00631958394266
                                    )
                                    * x
                                    - 0.03513592318103
                                )
                                * x
                                + 0.13198388204736
                            )
                            * x
                            - 0.33675804584105
                        )
                        * x
                        + 0.55984929401694
                    )
                    * x
                    - 0.50298686904881
                )
                * x
                - 0.06004025873978
            )
            * x
            + 0.80070190014386
        )
        * x
        - 1.00151717179967
    ) * x + 0.50002414586702


def modifierade_vinklar(
    theta_s_rad: float, theta_r_rad: float, beta_rad: float
) -> tuple[float, float, float]:
    """Schemat för vinklar utanför 0 ≤ θ_R ≤ θ_S ≤ β ≤ 2π, sist i AV 1106/07 avsnitt 5.7.1.

    Returnerar (θ'_S, θ'_R, β'). Schemat täcker en mottagare under mottagarsidans yta
    (θ_R < 0) och en källa under källsidans yta (θ_S > β). Det som återstår utanför
    förutsättningen efter schemat (θ_R > θ_S, eller en kilvinkel under π) ger `BullerFel`.
    """
    tva_pi = 2.0 * math.pi
    theta_s = theta_s_rad
    theta_r = theta_r_rad
    beta = beta_rad
    if beta > tva_pi + _VINKELTOLERANS_RAD:
        raise BullerFel(f"Kilvinkeln β = {beta} rad är större än 2π.")
    beta = min(beta, tva_pi)
    if beta < math.pi - _VINKELTOLERANS_RAD:
        raise BullerFel(
            f"Kilvinkeln β = {beta} rad är mindre än π: lösningen i AV 1106/07 avsnitt 5.7.1 "
            "gäller bara för β > π."
        )
    beta = max(beta, math.pi)
    if theta_r < 0.0:
        if theta_r > beta - tva_pi:
            theta_s = theta_s - theta_r
            beta = beta - theta_r
        else:
            theta_s = tva_pi - (beta - theta_s)
            beta = tva_pi
        theta_r = 0.0
    if theta_s >= tva_pi:
        theta_s = tva_pi
        beta = tva_pi
    elif theta_s > beta:
        beta = theta_s
    if theta_r > theta_s:
        raise BullerFel(
            f"θ_R = {theta_r} rad är större än θ_S = {theta_s} rad: AV 1106/07 avsnitt 5.7.1 "
            "förutsätter θ_R ≤ θ_S (källa och mottagare på var sin sida om kilens topp)."
        )
    return theta_s, theta_r, beta


def _theta_n(theta_s: float, theta_r: float, beta: float) -> tuple[float, float, float, float]:
    """θ₁ … θ₄ enligt AV 1106/07 ekv. (79), med ε-regeln sist i avsnitt 5.7.1.

    När |θ_n − π| < ε dras ε från θ_n. Samma justerade värde används sedan i ekv. (82) och i
    villkoren θ_n < π för ekv. (88)–(90), så att en geometri exakt på en skugg- eller
    reflexgräns behandlas som strax innanför den belysta sidan.
    """
    vinklar = (
        theta_s - theta_r,
        theta_s + theta_r,
        2.0 * beta - (theta_s + theta_r),
        2.0 * beta - (theta_s - theta_r),
    )
    justerade = tuple(v - EPSILON_RAD if abs(v - math.pi) < EPSILON_RAD else v for v in vinklar)
    return justerade[0], justerade[1], justerade[2], justerade[3]


def vinkelfunktion_a(theta_n_rad: float, beta_rad: float) -> float:
    """A(θ_n) enligt AV 1106/07 ekv. (82), med kilindex ν = π/β."""
    nu = math.pi / beta_rad
    return nu / 2.0 * (-beta_rad - math.pi + theta_n_rad) + math.pi * heaviside(
        math.pi - theta_n_rad
    )


def _a_ganger_e(a: float, nu: float, omega: float, kil: Kil) -> complex:
    """Produkten A(θ_n)·E_ν(A(θ_n)) i AV 1106/07 ekv. (78), med E_ν ur ekv. (81), (83), (84).

    Kvoten sin|A|/|A| i ekv. (81) är obestämd i A = 0 (θ_n = β + π). Där har produkten
    A·sin|A|/|A| = sin A gränsvärdet 0, och det är produkten som räknas här.
    """
    tau_s = kil.gangtid_kalla_s
    tau_r = kil.gangtid_mottagare_s
    tau = kil.gangtid_s
    cos_a = math.cos(a)
    tidsfaktor = 2.0 * tau_s * tau_r / (tau * tau) + 0.5
    rot_nu = math.sqrt(nu * nu + tidsfaktor * cos_a * cos_a)
    b = math.sqrt(4.0 * omega * tau_s * tau_r / (math.pi * tau)) * cos_a / rot_nu  # ekv. (83)
    abs_b = abs(b)
    a_d = tecken(b) * complex(fresnel_f(abs_b), -fresnel_g(abs_b))  # ekv. (84)
    return _PI_DIV_SQRT2_EXP_I_PI4 * (math.sin(a) * nu / rot_nu) * a_d  # A·E_ν, ekv. (81)


def _cosinussats(a: float, b: float, vinkel_rad: float) -> float:
    """√(a² + b² − 2ab·cos θ), formen i AV 1106/07 ekv. (88)–(90) för R_n och τ_n."""
    return math.sqrt(max(0.0, a * a + b * b - 2.0 * a * b * math.cos(vinkel_rad)))


def _ytreflektion(
    frekvens_hz: float, gangtid_s: float, strykvinkel_rad: float, impedans: complex
) -> complex:
    """Q(f, τ, ψ_G, Z) enligt AV 1106/07 avsnitt 5.6.3; Z = ∞ (hård yta) ger Q = 1."""
    if cmath.isinf(impedans):
        return 1.0 + 0.0j
    if cmath.isnan(impedans) or impedans == 0.0:
        raise BullerFel("Kilytans impedans ska vara ett tal skilt från 0 (eller oändlig).")
    return sfarisk_reflektion(frekvens_hz, gangtid_s, strykvinkel_rad, impedans)


Reflexkoefficient = Callable[[str, float, float], complex]
"""Q för den speglade strålen i ekv. (89)–(90): (yta "S" eller "R", τ_n, ψ_G) → Q."""


def _kiltryck(
    frekvens_hz: float,
    kil: Kil,
    q_ytor: tuple[complex, complex] | None,
    reflex: Reflexkoefficient | None,
) -> Kiltryck:
    """Gemensam räknegång för AV 1106/07 ekv. (78)–(90) och (105).

    `q_ytor` är (Q_S, Q_R) i diffraktionstermen; `None` ger den icke-reflekterande kilen
    (bara n = 1, ekv. 105). `reflex` ger Q för den speglade strålen i ekv. (89)–(90).
    """
    if not (frekvens_hz > 0.0 and math.isfinite(frekvens_hz)):
        raise BullerFel("Frekvensen ska vara ändlig och större än 0 Hz.")
    theta_s, theta_r, beta = modifierade_vinklar(kil.theta_s_rad, kil.theta_r_rad, kil.beta_rad)
    theta_1, theta_2, theta_3, theta_4 = _theta_n(theta_s, theta_r, beta)
    nu = math.pi / beta
    omega = 2.0 * math.pi * frekvens_hz
    r_s = kil.vag_kalla_m
    r_r = kil.vag_mottagare_m
    tau_s = kil.gangtid_kalla_s
    tau_r = kil.gangtid_mottagare_s

    if q_ytor is None:
        termer: tuple[tuple[complex, float], ...] = ((1.0 + 0.0j, theta_1),)  # ekv. (105)
    else:
        q_s, q_r = q_ytor
        ojusterad_2 = theta_s + theta_r
        ojusterad_3 = 2.0 * beta - ojusterad_2
        if abs(ojusterad_2 - math.pi) < EPSILON_RAD and abs(ojusterad_3 - math.pi) < EPSILON_RAD:
            raise BullerFel(
                "Kilvinkeln är π och θ_S + θ_R = π: de två reflexgränserna θ₂ = π och θ₃ = π "
                "sammanfaller, och ε-regeln i AV 1106/07 avsnitt 5.7.1 skulle föra båda termerna "
                "till samma sida. Lösningen gäller bara för β > π; plan mark räknas med "
                "delmodell 1 eller 2."
            )
        # Ekv. (79)–(80) med Q₂ = Q_R och Q₃ = Q_S (se modulens docstring).
        termer = ((1.0 + 0.0j, theta_1), (q_r, theta_2), (q_s, theta_3), (q_s * q_r, theta_4))
    summa = 0.0j
    for q_n, theta_n in termer:
        summa += q_n * _a_ganger_e(vinkelfunktion_a(theta_n, beta), nu, omega, kil)
    diffrakterat = -summa / math.pi * cmath.exp(1j * omega * kil.gangtid_s) / kil.vag_m  # (78)

    direkt = 0.0j
    reflekterat = 0.0j
    if theta_1 < math.pi:  # ekv. (88)
        r_1 = _cosinussats(r_s, r_r, theta_1)
        tau_1 = _cosinussats(tau_s, tau_r, theta_1)
        if r_1 <= 0.0:
            raise BullerFel("Källa och mottagare sammanfaller (R₁ = 0 i ekv. 88).")
        direkt = cmath.exp(1j * omega * tau_1) / r_1
    if reflex is not None and theta_2 < math.pi:  # ekv. (89): spegling i mottagarsidans yta
        r_2 = _cosinussats(r_s, r_r, theta_2)
        tau_2 = _cosinussats(tau_s, tau_r, theta_2)
        if r_2 <= 0.0:
            raise BullerFel("Källan sammanfaller med spegelmottagaren (R₂ = 0 i ekv. 89).")
        sinus = (r_s * math.sin(theta_s) + r_r * math.sin(theta_r)) / r_2
        psi = math.asin(min(1.0, max(0.0, sinus)))
        reflekterat = reflex("R", tau_2, psi) * cmath.exp(1j * omega * tau_2) / r_2
    elif reflex is not None and theta_3 < math.pi:  # ekv. (90): spegling i källsidans yta
        r_3 = _cosinussats(r_s, r_r, theta_3)
        tau_3 = _cosinussats(tau_s, tau_r, theta_3)
        if r_3 <= 0.0:
            raise BullerFel("Spegelkällan sammanfaller med mottagaren (R₃ = 0 i ekv. 90).")
        sinus = (r_s * math.sin(beta - theta_s) + r_r * math.sin(beta - theta_r)) / r_3
        psi = math.asin(min(1.0, max(0.0, sinus)))
        reflekterat = reflex("S", tau_3, psi) * cmath.exp(1j * omega * tau_3) / r_3
    return Kiltryck(diffrakterat=diffrakterat, direkt=direkt, reflekterat=reflekterat)


def kiltryck_impedans_delar(
    frekvens_hz: float, kil: Kil, impedans_kalla: complex, impedans_mottagare: complex
) -> Kiltryck:
    """Funktionen pwedge uppdelad i sina termer, AV 1106/07 avsnitt 5.7.1, ekv. (78)–(91).

    `impedans_kalla` (Z_S) och `impedans_mottagare` (Z_R) är de normaliserade impedanserna för
    kilens yta mot källan respektive mottagaren; `math.inf` betyder hård yta (avsnitt 5.7.4).
    Q_S och Q_R i diffraktionstermen räknas enligt ekv. (80): sfärisk reflektionskoefficient
    med gångtiden τ_S + τ_R och strykvinkeln min(β − θ_S, π/2) respektive min(θ_R, π/2). Enligt
    TC-101327 avsnitt 2.3 gäller den definitionen efter 2018 bara delmodell 3; delmodell 4–6
    ska använda `kiltryck_reflektionskoefficienter_delar`.
    """
    theta_s, theta_r, beta = modifierade_vinklar(kil.theta_s_rad, kil.theta_r_rad, kil.beta_rad)
    summatid = kil.gangtid_kalla_s + kil.gangtid_mottagare_s
    q_s = _ytreflektion(frekvens_hz, summatid, min(beta - theta_s, math.pi / 2.0), impedans_kalla)
    q_r = _ytreflektion(frekvens_hz, summatid, min(theta_r, math.pi / 2.0), impedans_mottagare)

    def reflex(yta: str, gangtid_s: float, strykvinkel_rad: float) -> complex:
        impedans = impedans_kalla if yta == "S" else impedans_mottagare
        return _ytreflektion(frekvens_hz, gangtid_s, strykvinkel_rad, impedans)

    return _kiltryck(frekvens_hz, kil, (q_s, q_r), reflex)


def kiltryck_impedans(
    frekvens_hz: float, kil: Kil, impedans_kalla: complex, impedans_mottagare: complex
) -> complex:
    """Funktionen pwedge, AV 1106/07 avsnitt 5.7.1, ekv. (91): trycket p vid mottagaren."""
    return kiltryck_impedans_delar(frekvens_hz, kil, impedans_kalla, impedans_mottagare).totalt


def kiltryck_reflektionskoefficienter_delar(
    frekvens_hz: float,
    kil: Kil,
    q_kalla: complex,
    q_mottagare: complex,
    q_reflex_kalla: complex | None = None,
    q_reflex_mottagare: complex | None = None,
) -> Kiltryck:
    """pwedge med givna reflektionskoefficienter för kilytorna, TC-101327 avsnitt 2.3.

    Efter 2018 års revision ska kilytornas koefficienter i delmodell 4–6 inte räknas ur en
    impedans enligt AV 1106/07 ekv. (80) utan ges som vägda medelvärden Q_avS och Q_avR av den
    reella ekvivalenta koefficienten (TC-101327 bilaga G; räknas i delmodellpaketen). Här går
    `q_kalla` in som Q_S och `q_mottagare` som Q_R i ekv. (78): Q₂ = Q_R, Q₃ = Q_S och
    Q₄ = Q_S·Q_R. Övriga ekvationer (79), (81)–(90) är oförändrade.

    TC-101327 säger inte vilken koefficient den speglade strålen i ekv. (89)–(90) ska ha när
    kilytorna beskrivs så. Om `q_reflex_kalla` och `q_reflex_mottagare` utelämnas används
    samma värden som i diffraktionstermen, vilket håller fältet kontinuerligt över
    reflexgränserna (se loggen, öppen fråga till P-4).
    """
    reflex_s = q_kalla if q_reflex_kalla is None else q_reflex_kalla
    reflex_r = q_mottagare if q_reflex_mottagare is None else q_reflex_mottagare
    for q in (q_kalla, q_mottagare, reflex_s, reflex_r):
        if not cmath.isfinite(q):
            raise BullerFel("Reflektionskoefficienterna för kilytorna ska vara ändliga tal.")

    def reflex(yta: str, gangtid_s: float, strykvinkel_rad: float) -> complex:
        return reflex_s if yta == "S" else reflex_r

    return _kiltryck(frekvens_hz, kil, (q_kalla, q_mottagare), reflex)


def kiltryck_reflektionskoefficienter(
    frekvens_hz: float,
    kil: Kil,
    q_kalla: complex,
    q_mottagare: complex,
    q_reflex_kalla: complex | None = None,
    q_reflex_mottagare: complex | None = None,
) -> complex:
    """Trycket p ur `kiltryck_reflektionskoefficienter_delar` (pwedge enligt TC-101327 2.3)."""
    return kiltryck_reflektionskoefficienter_delar(
        frekvens_hz, kil, q_kalla, q_mottagare, q_reflex_kalla, q_reflex_mottagare
    ).totalt


def kiltryck_icke_reflekterande_delar(frekvens_hz: float, kil: Kil) -> Kiltryck:
    """Funktionen pwedge0 uppdelad i sina termer, AV 1106/07 avsnitt 5.7.5, ekv. (105)–(106).

    Diffraktionstermen har bara n = 1 (ekv. 105) och bidraget från spegling i en kilyta
    (ekv. 89–90) utgår. Den direkta strålen läggs till när θ₁ < π (ekv. 88).
    """
    return _kiltryck(frekvens_hz, kil, None, None)


def kiltryck_icke_reflekterande(frekvens_hz: float, kil: Kil) -> complex:
    """Funktionen pwedge0, AV 1106/07 avsnitt 5.7.5, ekv. (106): trycket p vid mottagaren."""
    return kiltryck_icke_reflekterande_delar(frekvens_hz, kil).totalt


def _diffraktionskoefficient(frekvens_hz: float, kil: Kil, tryck: complex) -> complex:
    """D = ℓ·p/exp(jωτ), AV 1106/07 avsnitt 5.7.2, ekv. (92)–(93)."""
    omega = 2.0 * math.pi * frekvens_hz
    return kil.vag_m * tryck * cmath.exp(-1j * omega * kil.gangtid_s)


def kildiffraktionskoefficient_impedans(
    frekvens_hz: float, kil: Kil, impedans_kalla: complex, impedans_mottagare: complex
) -> complex:
    """Funktionen Dwedge, AV 1106/07 avsnitt 5.7.2, ekv. (93)–(94), med Q enligt ekv. (80)."""
    tryck = kiltryck_impedans(frekvens_hz, kil, impedans_kalla, impedans_mottagare)
    return _diffraktionskoefficient(frekvens_hz, kil, tryck)


def kildiffraktionskoefficient_reflektionskoefficienter(
    frekvens_hz: float,
    kil: Kil,
    q_kalla: complex,
    q_mottagare: complex,
    q_reflex_kalla: complex | None = None,
    q_reflex_mottagare: complex | None = None,
) -> complex:
    """Dwedge (AV 1106/07 ekv. 93–94) med givna koefficienter enligt TC-101327 avsnitt 2.3."""
    tryck = kiltryck_reflektionskoefficienter(
        frekvens_hz, kil, q_kalla, q_mottagare, q_reflex_kalla, q_reflex_mottagare
    )
    return _diffraktionskoefficient(frekvens_hz, kil, tryck)


def kildiffraktionskoefficient_icke_reflekterande(frekvens_hz: float, kil: Kil) -> complex:
    """Funktionen Dwedge0, AV 1106/07 avsnitt 5.7.5, ekv. (107).

    Rapporten ger ingen egen formel för Dwedge0; den räknas som D i ekv. (92)–(93) med p ur
    pwedge0.
    """
    return _diffraktionskoefficient(frekvens_hz, kil, kiltryck_icke_reflekterande(frekvens_hz, kil))
