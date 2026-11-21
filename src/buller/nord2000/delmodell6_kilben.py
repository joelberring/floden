"""Kilytornas reflektionskoefficienter för två skärmar: FORCE TC-101327 bilaga G.4.

Källa: FORCE TC-101327 (2019) avsnitt 2.16.4 (bilaga G.4 till AV 1106/07), ekv. (427)–(439),
med ekv. (422)–(425) ur bilaga G.3 för den relativa skärmbredden. Varje formel är avläst från
sidbild (s. 18–20), se `docs/buller/logg/P-6.md`.

Fyra kilytor: källsidan och mottagarsidan på den första skärmen (närmast källan) och på den
andra. Ytterytorna (Q_avS,1 och Q_avR,2) räknas som för en skärm med en kant (bilaga G.2,
`delmodell4_kilben.kilben`). Ytorna mot mitten (Q_avR,1 och Q_avS,2) räknas på samma sätt men
med terrängen mellan skärmtopparna, så att den andra skärmtoppen tar markpunktens plats. De
två mittytorna modifieras sedan till Q′_avR,1 och Q′_avS,2 (ekv. 438–439) med parametern r_QM
(ekv. 437), som för över mot tvåkantsfallet när terrängen mellan topparna är nästan rak och
skärmarna står tätt.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from buller.nord2000.delmodell4_kilben import Kilben, kilben, medelreflektion_kilben
from buller.nord2000.geometri import langd
from buller.nord2000.terrang import Terranggeometri
from buller.nord2000.terrang_skarmar import Skarmform, vertikalt_avstand_m
from buller.typer import BullerFel, Flyttal

# TC-101327 bilaga G.3, ekv. (425): gränserna för den relativa skärmbredden r₁₂.
R12_SMAL = 0.2
R12_BRED = 0.8
# TC-101327 bilaga G.4, ekv. (436): Δl′₀ = √(1 + tan²θ₀) − 1 med θ₀ = 5°.
THETA0_GRADER = 5.0
DELTA_L0_NORMERAD = math.sqrt(1.0 + math.tan(math.radians(THETA0_GRADER)) ** 2) - 1.0
# TC-101327 bilaga G.4, ekv. (438)–(439): Q_hard = 1 och faktorn 0,5 framför den.
Q_HARD = 1.0
FAKTOR_HARD_MITTYTA = 0.5


def kontrollera_skarmformer(geo: Terranggeometri, forsta: Skarmform, andra: Skarmform) -> None:
    """Kontrollerar att två skärmformer följer på varandra, AV 1106/07 avsnitt 5.15.2.

    Den första skärmen (W₁₁, T₁, W₁₂) ligger närmast källan och den andra (W₂₁, T₂, W₂₂)
    närmast mottagaren; W₁₂ får sammanfalla med W₂₁ men formerna får inte gå om varandra.
    """
    n = geo.x_m.shape[0]
    for form in (forsta, andra):
        if not 0 <= form.index_w1 < form.index_kant < form.index_w2 <= n - 1:
            raise BullerFel("En skärmform kräver index W₁ < T < W₂ inom terrängprofilen.")
    if not forsta.index_w2 <= andra.index_w1:
        raise BullerFel(
            "Delmodell 6 kräver två skilda skärmformer i ordning från källan: den första "
            "skärmens W₁₂ får inte ligga efter den andra skärmens W₂₁."
        )


def overgang_skarmbredd(d12_m: float, d_sgrg_m: float) -> float:
    """r_QM,2edge ur den relativa skärmbredden r₁₂ = d₁₂/d_SGRG, TC-101327 ekv. (422)–(425).

    1 för r₁₂ ≤ 0,2, 0 för r₁₂ ≥ 0,8 och linjärt däremellan. d₁₂ = |T₁T₂| och d_SGRG är
    avståndet mellan markpunkterna under källan och mottagaren.
    """
    if not d12_m > 0.0 or not d_sgrg_m > 0.0:
        raise BullerFel("r₁₂ kräver avstånd större än 0 (TC-101327 ekv. 422–424).")
    r12 = d12_m / d_sgrg_m
    if r12 <= R12_SMAL:
        return 1.0
    if r12 >= R12_BRED:
        return 0.0
    return 1.0 - (r12 - R12_SMAL) / (R12_BRED - R12_SMAL)


def normerad_omvag(
    x_t1_m: float,
    z_t1_m: float,
    x_t2_m: float,
    z_t2_m: float,
    x_w12_m: float,
    z_w12_m: float,
    x_w21_m: float,
    z_w21_m: float,
) -> float:
    """Δl′ = Δl/l_T1T2 för vägen T₁–W₁₂–W₂₁–T₂, TC-101327 bilaga G.4, ekv. (427)–(434).

    Δh₁ och Δh₂ är kilpunkternas lodräta avstånd under linjen T₁T₂ (−VertDist, ekv. 427–428),
    satta till 0 när punkten ligger över linjen. Längderna räknas i ett system där linjen
    T₁T₂ är vågrät: l_T1T2 = |x_T2 − x_T1| (ekv. 429) och punkterna (x_W, Δh) (ekv. 430–432).
    """
    dh1 = max(-vertikalt_avstand_m(x_t1_m, z_t1_m, x_t2_m, z_t2_m, x_w12_m, z_w12_m), 0.0)
    dh2 = max(-vertikalt_avstand_m(x_t1_m, z_t1_m, x_t2_m, z_t2_m, x_w21_m, z_w21_m), 0.0)
    l_t1t2 = langd(x_t1_m, 0.0, x_t2_m, 0.0)  # ekv. (429)
    if not l_t1t2 > 0.0:
        raise BullerFel("Skärmtopparna T₁ och T₂ ska ha olika x (TC-101327 ekv. 429).")
    l_t1w12 = langd(x_t1_m, 0.0, x_w12_m, dh1)  # ekv. (430)
    l_w12w21 = langd(x_w12_m, dh1, x_w21_m, dh2)  # ekv. (431)
    l_w21t2 = langd(x_w21_m, dh2, x_t2_m, 0.0)  # ekv. (432)
    omvag = l_t1w12 + l_w12w21 + l_w21t2 - l_t1t2  # ekv. (433)
    return omvag / l_t1t2  # ekv. (434)


def overgang_form(normerad_omvag_dl: float) -> float:
    """r_QMtr, TC-101327 bilaga G.4, ekv. (435): 1 − Δl′/Δl′₀ under Δl′₀, annars 0."""
    if normerad_omvag_dl >= DELTA_L0_NORMERAD:
        return 0.0
    return 1.0 - normerad_omvag_dl / DELTA_L0_NORMERAD


def overgangsparameter_rqm(geo: Terranggeometri, forsta: Skarmform, andra: Skarmform) -> float:
    """r_QM = r_QMtr·r_QM,2edge för två skärmar, TC-101327 bilaga G.4, ekv. (437).

    S_G och R_G i ekv. (423) är terrängprofilens första och sista punkt.
    """
    kontrollera_skarmformer(geo, forsta, andra)
    x_t1, z_t1 = float(geo.x_m[forsta.index_kant]), float(geo.z_m[forsta.index_kant])
    x_t2, z_t2 = float(geo.x_m[andra.index_kant]), float(geo.z_m[andra.index_kant])
    dl = normerad_omvag(
        x_t1,
        z_t1,
        x_t2,
        z_t2,
        float(geo.x_m[forsta.index_w2]),
        float(geo.z_m[forsta.index_w2]),
        float(geo.x_m[andra.index_w1]),
        float(geo.z_m[andra.index_w1]),
    )
    d12 = langd(x_t1, z_t1, x_t2, z_t2)  # ekv. (422)
    d_sgrg = langd(
        float(geo.x_m[0]), float(geo.z_m[0]), float(geo.x_m[-1]), float(geo.z_m[-1])
    )  # ekv. (423)
    return overgang_form(dl) * overgang_skarmbredd(d12, d_sgrg)


def modifierad_mittreflektion(q_av: float, r_qm: float) -> float:
    """Q′_avR,1 eller Q′_avS,2 = (1 − r_QM)·Q_av + 0,5·r_QM·Q_hard, TC-101327 ekv. (438)–(439)."""
    if not 0.0 <= r_qm <= 1.0:
        raise BullerFel("r_QM ska ligga i [0, 1] (TC-101327 ekv. 437).")
    return (1.0 - r_qm) * q_av + FAKTOR_HARD_MITTYTA * r_qm * Q_HARD


def kilben_tva_skarmar(
    geo: Terranggeometri, forsta: Skarmform, andra: Skarmform
) -> tuple[Kilben, Kilben, Kilben, Kilben]:
    """Kilbenen för de fyra kilytorna, TC-101327 bilaga G.4 (med G.2, ekv. 406–420).

    Ordning: (första skärmens källsida, första skärmens mottagarsida, andra skärmens källsida,
    andra skärmens mottagarsida). Ytterbenen går till profilens första respektive sista punkt
    (S_G, R_G). Benen mot mitten omfattar terrängen mellan T₁ och T₂, så att den andra toppen
    är benets slutpunkt. Källsidor speglas i x som i `delmodell4.kilben_ur_terrang`.
    """
    kontrollera_skarmformer(geo, forsta, andra)
    t1, t2 = forsta.index_kant, andra.index_kant
    n = geo.x_m.shape[0]
    x, z, sigma = geo.x_m, geo.z_m, geo.flodesresistivitet_kNsm4

    def framat(start: int, slut: int, kilsegment: int) -> Kilben:
        return kilben(
            [float(v) for v in x[start : slut + 1]],
            [float(v) for v in z[start : slut + 1]],
            [float(v) for v in sigma[start:slut]],
            kilsegment,
        )

    def bakat(start: int, slut: int, kilsegment: int) -> Kilben:
        """Benet från punkt `start` bakåt till punkt `slut` (slut < start), speglat i x."""
        index = range(start, slut - 1, -1)
        return kilben(
            [-float(x[i]) for i in index],
            [float(z[i]) for i in index],
            [float(sigma[i]) for i in range(start - 1, slut - 1, -1)],
            kilsegment,
        )

    return (
        bakat(t1, 0, t1 - forsta.index_w1),
        framat(t1, t2, forsta.index_w2 - t1),
        bakat(t2, t1, t2 - andra.index_w1),
        framat(t2, n - 1, andra.index_w2 - t2),
    )


@dataclass(frozen=True)
class KilytorTvaSkarmar:
    """Reflektionskoefficienterna för de fyra kilytorna, ett värde per frekvens."""

    q_1_kalla: Flyttal  # Q_avS,1
    q_1_mottagare: Flyttal  # Q′_avR,1, ekv. (438)
    q_2_kalla: Flyttal  # Q′_avS,2, ekv. (439)
    q_2_mottagare: Flyttal  # Q_avR,2
    q_1_mottagare_omodifierad: Flyttal  # Q_avR,1
    q_2_kalla_omodifierad: Flyttal  # Q_avS,2
    r_qm: float  # ekv. (437)


def _givet(varde: Flyttal | float | None, antal: int, namn: str) -> Flyttal | None:
    if varde is None:
        return None
    falt = np.asarray(varde, dtype=np.float64)
    if falt.ndim == 0:
        return np.full(antal, float(falt), dtype=np.float64)
    if falt.shape != (antal,):
        raise BullerFel(f"{namn} ska vara ett tal eller ha ett värde per frekvens ({antal}).")
    return falt.astype(np.float64)


def kilytornas_reflektion_tva_skarmar(
    frekvenser_hz: Flyttal,
    geo: Terranggeometri,
    forsta: Skarmform,
    andra: Skarmform,
    ljudhastighet_m_s: float,
    *,
    q_1_kalla: Flyttal | float | None = None,
    q_1_mottagare: Flyttal | float | None = None,
    q_2_kalla: Flyttal | float | None = None,
    q_2_mottagare: Flyttal | float | None = None,
) -> KilytorTvaSkarmar:
    """Q_avS,1, Q′_avR,1, Q′_avS,2 och Q_avR,2 per frekvens, TC-101327 bilaga G.4.

    Q_av = Σ w_j·Q_e(f, ψ_G, l, σ_j) för varje kilben (ekv. 421); mittytorna modifieras med
    r_QM (ekv. 437–439). Ett givet värde ersätter Q_av för ytan (bilaga G.5: Q_e = √ρ_E för en
    byggd yta); för mittytorna är det givna värdet det omodifierade Q_av, och modifieringen
    görs därefter.
    """
    frekvenser = np.asarray(frekvenser_hz, dtype=np.float64)
    if frekvenser.ndim != 1 or frekvenser.shape[0] == 0 or not bool(np.all(frekvenser > 0.0)):
        raise BullerFel("Frekvenserna ska vara en icke-tom vektor med värden större än 0 Hz.")
    antal = frekvenser.shape[0]
    ben = kilben_tva_skarmar(geo, forsta, andra)
    givna = (
        _givet(q_1_kalla, antal, "q_1_kalla"),
        _givet(q_1_mottagare, antal, "q_1_mottagare"),
        _givet(q_2_kalla, antal, "q_2_kalla"),
        _givet(q_2_mottagare, antal, "q_2_mottagare"),
    )
    q_av: list[Flyttal] = []
    for kilbenet, givet in zip(ben, givna, strict=True):
        if givet is not None:
            q_av.append(givet)
            continue
        q_av.append(
            np.array(
                [medelreflektion_kilben(float(f), kilbenet, ljudhastighet_m_s) for f in frekvenser],
                dtype=np.float64,
            )
        )
    r_qm = overgangsparameter_rqm(geo, forsta, andra)
    return KilytorTvaSkarmar(
        q_1_kalla=q_av[0],
        q_1_mottagare=np.array([modifierad_mittreflektion(float(q), r_qm) for q in q_av[1]]),
        q_2_kalla=np.array([modifierad_mittreflektion(float(q), r_qm) for q in q_av[2]]),
        q_2_mottagare=q_av[3],
        q_1_mottagare_omodifierad=q_av[1],
        q_2_kalla_omodifierad=q_av[2],
        r_qm=r_qm,
    )
