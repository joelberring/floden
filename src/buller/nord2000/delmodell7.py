"""Delmodell 7 i Nord2000: turbulensspridning in i skuggan bakom en skärm.

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.16, ekv. (271)–(277), tabell 6 och 7, figur 26
och 27. TC-101327 (2019) ändrar ingenting i avsnitt 5.16. Tabellerna är desamma som bilaga G
(tabell G1 och G2) i AV 1849/00, och AV 1849/00 avsnitt 4.9 (ekv. 164–167) har använts för att
avgöra tryckfelen i ekv. (272)–(273), se `docs/buller/logg/P-7.md`.

Delmodellen ger nivån ΔL₇ av det ljud som turbulensen sprider ned bakom skärmen, relativt
frifältsnivån. Den läggs energimässigt (⊕) till skärmmodellernas resultat i ekv. (332).
Utan bidrag (ingen turbulens, eller skärmkanten på eller under linjen källa–mottagare) är
ΔL₇ = −∞ dB, vilket är neutralt i energisumman: `energisumma_db(x, -inf) == x`.

Modulen väljer inte skärmkant. Kantens läge är indata (avsnitt 5.21 hör till ett annat paket),
liksom markfaktorn p_G ur delmodell 4–6.

Enheter: C_v² i m^(4/3)/s², C_T² i K²/m^(2/3), längder i meter, frekvens i hertz. Uttrycken
(272)–(273) är normerade: 40 är meter, 2000 är hertz, tabellerna gäller enhetsstyrka i
SI-enheter, och f₀ i ekv. (272) innehåller en underförstådd längd på 1 m.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from buller.nord2000.frekvenser import heaviside, ljudhastighet_m_s
from buller.nord2000.geometri import langd, normlinje
from buller.typer import BullerFel, Flyttal

# AV 1106/07 avsnitt 5.16, ekv. (271): effektiv turbulensstyrka = 10 gånger den verkliga.
FORSTARKNING_TURBULENSSTYRKA = 10.0
# Normeringar i ekv. (272)–(273).
NORMERINGSAVSTAND_M = 40.0
NORMERINGSFREKVENS_HZ = 2000.0

# Ingångar i tabell 6 och 7: kolumner 40·R₂/R₁, rader 40·h_e/R₁.
TABELL_R2_KVOT: tuple[float, ...] = (10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0)
TABELL_HE_KVOT: tuple[float, ...] = (5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0)

Tabell = tuple[tuple[float, ...], ...]

# AV 1106/07 tabell 6 (= AV 1849/00 tabell G1): normerad spridd nivå L_ws0 från vindturbulens, dB.
LWS0_DB: Tabell = (
    (-41.6, -33.9, -30.2, -27.9, -26.2, -24.9, -23.9, -23.1, -22.4, -21.8),
    (-49.9, -44.0, -39.5, -36.7, -34.7, -33.1, -31.8, -30.9, -30.1, -29.3),
    (-52.1, -48.9, -45.8, -42.9, -40.7, -39.1, -37.7, -36.6, -35.5, -34.7),
    (-53.8, -51.0, -48.8, -46.8, -45.0, -43.5, -42.1, -40.9, -39.9, -39.1),
    (-55.4, -52.4, -50.4, -48.8, -47.5, -46.2, -45.1, -44.0, -43.1, -42.2),
    (-57.0, -53.8, -51.5, -50.6, -48.9, -47.8, -46.8, -45.9, -45.2, -44.4),
    (-58.6, -55.1, -52.7, -51.1, -49.8, -48.8, -47.9, -47.1, -46.4, -45.7),
    (-59.9, -56.5, -53.9, -52.1, -50.7, -49.6, -48.7, -48.0, -47.3, -46.6),
)

# AV 1106/07 tabell 7 (= AV 1849/00 tabell G2): normerad spridd nivå L_ts0 från
# temperaturturbulens, dB.
LTS0_DB: Tabell = (
    (-44.0, -39.1, -36.0, -34.0, -32.5, -31.3, -30.4, -29.6, -28.9, -28.3),
    (-47.4, -44.7, -42.4, -40.5, -39.1, -37.9, -36.9, -36.0, -35.3, -34.7),
    (-48.9, -46.7, -45.1, -43.6, -42.4, -41.4, -40.5, -39.7, -39.0, -38.4),
    (-50.2, -48.0, -46.4, -45.2, -44.1, -43.2, -42.4, -41.7, -41.1, -40.5),
    (-51.4, -49.0, -47.4, -46.2, -45.2, -44.3, -43.6, -42.9, -42.3, -41.8),
    (-52.5, -50.0, -48.3, -47.0, -46.0, -45.1, -44.4, -43.7, -43.2, -42.6),
    (-53.6, -51.0, -49.2, -47.8, -46.7, -45.8, -45.0, -44.4, -43.8, -43.3),
    (-54.6, -52.0, -50.0, -48.5, -47.4, -46.4, -45.6, -44.9, -44.3, -43.8),
)

INGET_BIDRAG_DB = -math.inf


@dataclass(frozen=True)
class Spridningsgeometri:
    """Geometrin i AV 1106/07 figur 26: R₁, R₂ längs linjen källa–mottagare och höjden h_e.

    `he_m` är skärmkantens teckensatta avstånd till linjen (positivt ovanför). `r1_m` och
    `r2_m` är avstånden från källan respektive mottagaren till kantens fotpunkt på linjen.
    """

    r1_m: float
    r2_m: float
    he_m: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.r1_m) and math.isfinite(self.r2_m) and math.isfinite(self.he_m)):
            raise BullerFel("Delmodell 7: R₁, R₂ och h_e ska vara ändliga tal.")
        if self.r1_m <= 0.0 or self.r2_m <= 0.0:
            raise BullerFel(
                "Delmodell 7: skärmkantens fotpunkt ska ligga mellan källa och mottagare "
                "(R₁ > 0 och R₂ > 0)."
            )

    @property
    def skarmad(self) -> bool:
        """Sann när kanten ligger ovanför linjen källa–mottagare (annars inget bidrag)."""
        return self.he_m > 0.0

    def omvand(self) -> Spridningsgeometri:
        """Samma geometri med källa och mottagare ombytta."""
        return Spridningsgeometri(self.r2_m, self.r1_m, self.he_m)


def energisumma_db(niva1_db: float, niva2_db: float) -> float:
    """Symbolen ⊕: inkoherent summa av två nivåer, AV 1106/07 avsnitt 3 (teckenförklaringen).

    Används i ekv. (274) och (332). −∞ dB är neutralt element.
    """
    if niva1_db == -math.inf and niva2_db == -math.inf:
        return -math.inf
    storst = max(niva1_db, niva2_db)
    minst = min(niva1_db, niva2_db)
    return storst + 10.0 * math.log10(1.0 + 10.0 ** ((minst - storst) / 10.0))


def spridningsgeometri(
    x_s_m: float, z_s_m: float, x_r_m: float, z_r_m: float, x_t_m: float, z_t_m: float
) -> Spridningsgeometri:
    """R₁, R₂ och h_e för en skärmkant T, AV 1106/07 figur 26 och ekv. (275), rad 5–7.

    S och R är källa och mottagare (i brytande atmosfär de korrigerade lägena S′ och R′ ur
    `korrigerad_kalla_och_mottagare`). Kanten projiceras vinkelrätt på linjen S–R med
    NormLine; R₁ och R₂ mäts till fotpunkten. Kräver x_R > x_S och att fotpunkten ligger
    mellan S och R.
    """
    if not x_r_m > x_s_m:
        raise BullerFel("Delmodell 7: mottagaren ska ligga efter källan (x_R > x_S).")
    x_fot, z_fot, he = normlinje(x_s_m, z_s_m, x_r_m, z_r_m, x_t_m, z_t_m)
    r_sr = langd(x_s_m, z_s_m, x_r_m, z_r_m)
    lage = ((x_fot - x_s_m) * (x_r_m - x_s_m) + (z_fot - z_s_m) * (z_r_m - z_s_m)) / r_sr**2
    if not 0.0 < lage < 1.0:
        raise BullerFel(
            "Delmodell 7: skärmkantens fotpunkt på linjen källa–mottagare ligger inte mellan "
            "källa och mottagare."
        )
    return Spridningsgeometri(
        r1_m=langd(x_s_m, z_s_m, x_fot, z_fot),
        r2_m=langd(x_r_m, z_r_m, x_fot, z_fot),
        he_m=he,
    )


def korrigerad_kalla_och_mottagare(
    x_t_kalla_m: float,
    z_t_kalla_m: float,
    x_t_mottagare_m: float,
    z_t_mottagare_m: float,
    r_s_m: float,
    r_r_m: float,
    theta1_rad: float,
    theta2_rad: float,
    delta_theta_s_rad: float,
    delta_theta_r_rad: float,
) -> tuple[float, float, float, float]:
    """Refraktionskorrigerade lägen S′ och R′, AV 1106/07 ekv. (275) och (276), rad 1–4.

    S′ läggs på avståndet R_S från kanten närmast källan i riktningen θ₁ + Δθ_S, och R′ på
    avståndet R_R från kanten närmast mottagaren i riktningen θ₂ + Δθ_R. R_S, R_R, Δθ_S och
    Δθ_R kommer ur DirectRay (ekv. 161), θ₁ och θ₂ ur ekv. (162); vinklarna räknas från lodlinjen
    nedåt. För en kant anges samma punkt två gånger. I homogen atmosfär (Δθ = 0, R_S = |ST|,
    R_R = |TR|) blir S′ = S och R′ = R. Returnerar (x′_S, z′_S, x′_R, z′_R).
    """
    if r_s_m <= 0.0 or r_r_m <= 0.0:
        raise BullerFel("Delmodell 7: R_S och R_R ska vara större än 0.")
    vinkel_s = theta1_rad + delta_theta_s_rad
    vinkel_r = theta2_rad + delta_theta_r_rad
    return (
        x_t_kalla_m - r_s_m * math.sin(vinkel_s),
        z_t_kalla_m - r_s_m * math.cos(vinkel_s),
        x_t_mottagare_m + r_r_m * math.sin(vinkel_r),
        z_t_mottagare_m - r_r_m * math.cos(vinkel_r),
    )


def spridningsgeometri_tva_kanter(
    x_s_m: float,
    z_s_m: float,
    x_r_m: float,
    z_r_m: float,
    x_t1_m: float,
    z_t1_m: float,
    x_t2_m: float,
    z_t2_m: float,
) -> Spridningsgeometri:
    """Ekvivalent skärmtopp för två kanter, AV 1106/07 figur 27, ekv. (276) rad 5–9 och (277).

    T₁ är kanten närmast källan och T₂ den närmast mottagaren (två skärmar eller en skärm med
    två kanter). Den ekvivalenta toppen T är skärningen mellan linjerna S–T₁ och R–T₂:
    R₂ = R/(1 + (h_e2/h_e1)·(R′₁/R′₂)), R₁ = R − R₂, h_e = h_e1·R₁/R′₁.

    Om någon av kanterna ligger på eller under linjen S–R returneras h_e = 0 (inget bidrag);
    ekv. (277) är då inte definierad, se tolkningarna i `docs/buller/logg/P-7.md`.
    """
    kant1 = spridningsgeometri(x_s_m, z_s_m, x_r_m, z_r_m, x_t1_m, z_t1_m)
    kant2 = spridningsgeometri(x_s_m, z_s_m, x_r_m, z_r_m, x_t2_m, z_t2_m)
    r = langd(x_s_m, z_s_m, x_r_m, z_r_m)
    r1_prim = kant1.r1_m
    r2_prim = kant2.r2_m
    if r1_prim + r2_prim > r * (1.0 + 1.0e-12):
        raise BullerFel("Delmodell 7: T₁ ska ligga närmare källan än T₂ längs linjen S–R.")
    if not (kant1.skarmad and kant2.skarmad):
        return Spridningsgeometri(r1_m=r1_prim, r2_m=r2_prim, he_m=0.0)
    r2 = r / (1.0 + (kant2.he_m / kant1.he_m) * (r1_prim / r2_prim))
    r1 = r - r2
    return Spridningsgeometri(r1_m=r1, r2_m=r2, he_m=kant1.he_m * r1 / r1_prim)


def _lage_i_tabell(varde: float, ingangar: tuple[float, ...]) -> tuple[int, float]:
    """Index för nedre tabellingång och andel mot nästa; utanför tabellen används randvärdet."""
    if varde <= ingangar[0]:
        return 0, 0.0
    if varde >= ingangar[-1]:
        return len(ingangar) - 2, 1.0
    i = 0
    while varde >= ingangar[i + 1]:
        i += 1
    return i, (varde - ingangar[i]) / (ingangar[i + 1] - ingangar[i])


def normerad_spridningsniva_db(tabell: Tabell, r1_m: float, r2_m: float, he_m: float) -> float:
    """L_ws0 eller L_ts0 ur tabell 6 eller 7, AV 1106/07 avsnitt 5.16.

    Linjär interpolation i båda led med ingångarna 40·R₂/R₁ och 40·h_e/R₁. Utanför tabellen
    används närmaste tabellingång (avsnitt 5.16; AV 1849/00 bilaga G).
    """
    if r1_m <= 0.0 or r2_m <= 0.0 or he_m <= 0.0:
        raise BullerFel("Delmodell 7: tabell 6 och 7 kräver R₁ > 0, R₂ > 0 och h_e > 0.")
    kolumn, andel_kolumn = _lage_i_tabell(NORMERINGSAVSTAND_M * r2_m / r1_m, TABELL_R2_KVOT)
    rad, andel_rad = _lage_i_tabell(NORMERINGSAVSTAND_M * he_m / r1_m, TABELL_HE_KVOT)
    ovre = (1.0 - andel_kolumn) * tabell[rad][kolumn] + andel_kolumn * tabell[rad][kolumn + 1]
    nedre = (1.0 - andel_kolumn) * tabell[rad + 1][kolumn] + andel_kolumn * tabell[rad + 1][
        kolumn + 1
    ]
    return (1.0 - andel_rad) * ovre + andel_rad * nedre


def normerad_spridningsniva_bada_hall_db(tabell: Tabell, geo: Spridningsgeometri) -> float:
    """L′_ws0 eller L′_ts0, AV 1106/07 ekv. (272) och (273), första raden.

    Tabellerna är inte reciproka. Därför räknas L₀(R₁, R₂, h_e) + 10·lg(R₁/40) även med källa
    och mottagare ombytta, och det största värdet används.
    """
    framat = normerad_spridningsniva_db(tabell, geo.r1_m, geo.r2_m, geo.he_m) + 10.0 * math.log10(
        geo.r1_m / NORMERINGSAVSTAND_M
    )
    bakat = normerad_spridningsniva_db(tabell, geo.r2_m, geo.r1_m, geo.he_m) + 10.0 * math.log10(
        geo.r2_m / NORMERINGSAVSTAND_M
    )
    return max(framat, bakat)


def spridningsvinkel_rad(geo: Spridningsgeometri) -> float:
    """θ = π − arctan(R₁/h_e) − arctan(R₂/h_e), AV 1106/07 ekv. (272).

    Räknas som arctan(h_e/R₁) + arctan(h_e/R₂), vilket är samma vinkel för h_e > 0 utan
    utsläckning när h_e är litet.
    """
    if not geo.skarmad:
        raise BullerFel("Delmodell 7: spridningsvinkeln kräver h_e > 0.")
    return math.atan2(geo.he_m, geo.r1_m) + math.atan2(geo.he_m, geo.r2_m)


def gransfrekvens_hz(geo: Spridningsgeometri, medeltemperatur_c: float) -> float:
    """f₀ = Coft(t_mean)/(2·sin(θ/2)), AV 1106/07 ekv. (272).

    Uttrycket har enheten m/s; talvärdet används som hertz (underförstådd längd 1 m).
    """
    return ljudhastighet_m_s(medeltemperatur_c) / (2.0 * math.sin(0.5 * spridningsvinkel_rad(geo)))


def markkorrektion_csr(markfaktor_pg: float) -> float:
    """C_SR = p_G² för p_G > 1, annars 1, AV 1106/07 ekv. (272).

    p_G är markeffektdelen av skärmmodellen (produkten över |p̂_G|^(w′w′) i ekv. 188 för en
    kant, motsvarande för två kanter). C_SR begränsas nedåt till 1.
    """
    if not markfaktor_pg >= 0.0 or not math.isfinite(markfaktor_pg):
        raise BullerFel("Delmodell 7: markfaktorn p_G ska vara ett ändligt tal ≥ 0.")
    return markfaktor_pg * markfaktor_pg if markfaktor_pg > 1.0 else 1.0


def lagfrekvenskorrektion_db(frekvens_hz: float, gransfrekvens_f0_hz: float) -> float:
    """15·H(f₀ − f)·lg(f/f₀) + 15·H(0,5·f₀ − f)·lg(f/(0,5·f₀)), AV 1106/07 ekv. (272)–(273)."""
    f0 = gransfrekvens_f0_hz
    return 15.0 * heaviside(f0 - frekvens_hz) * math.log10(frekvens_hz / f0) + 15.0 * heaviside(
        0.5 * f0 - frekvens_hz
    ) * math.log10(frekvens_hz / (0.5 * f0))


def _spridd_niva_db(
    tabell: Tabell,
    frekvens_hz: float,
    geo: Spridningsgeometri,
    turbulensstyrka: float,
    medeltemperatur_c: float,
    markfaktor_pg: float,
) -> float:
    """Gemensam form för ΔL_ws (ekv. 272) och ΔL_ts (ekv. 273) i AV 1106/07."""
    if turbulensstyrka == 0.0 or not geo.skarmad:
        return INGET_BIDRAG_DB
    effektiv_styrka = FORSTARKNING_TURBULENSSTYRKA * turbulensstyrka  # ekv. (271)
    return (
        normerad_spridningsniva_bada_hall_db(tabell, geo)
        + 10.0 * math.log10(markkorrektion_csr(markfaktor_pg))
        + 10.0 / 3.0 * math.log10(frekvens_hz / NORMERINGSFREKVENS_HZ)
        + 10.0 * math.log10(effektiv_styrka)
        + lagfrekvenskorrektion_db(frekvens_hz, gransfrekvens_hz(geo, medeltemperatur_c))
    )


def _kontrollera_indata(frekvens_hz: float, cv2: float, ct2: float) -> None:
    if not frekvens_hz > 0.0 or not math.isfinite(frekvens_hz):
        raise BullerFel("Delmodell 7: frekvensen ska vara större än 0.")
    if not (cv2 >= 0.0 and ct2 >= 0.0 and math.isfinite(cv2) and math.isfinite(ct2)):
        raise BullerFel("Delmodell 7: C_v² och C_T² ska vara ändliga och inte negativa.")


def vindspridning_db(
    frekvens_hz: float,
    geo: Spridningsgeometri,
    cv2: float,
    medeltemperatur_c: float,
    markfaktor_pg: float = 1.0,
) -> float:
    """ΔL_ws: spritt ljud från vindturbulens relativt frifält, AV 1106/07 ekv. (271)–(272).

    ΔL_ws = L′_ws0 + 10·lg C_SR + (10/3)·lg(f/2000) + 10·lg(10·C_v²) + lågfrekvenstermerna.
    −∞ när C_v² = 0 eller h_e ≤ 0.
    """
    _kontrollera_indata(frekvens_hz, cv2, 0.0)
    return _spridd_niva_db(LWS0_DB, frekvens_hz, geo, cv2, medeltemperatur_c, markfaktor_pg)


def temperaturspridning_db(
    frekvens_hz: float,
    geo: Spridningsgeometri,
    ct2: float,
    medeltemperatur_c: float,
    markfaktor_pg: float = 1.0,
) -> float:
    """ΔL_ts: spritt ljud från temperaturturbulens, AV 1106/07 ekv. (271) och (273).

    Samma form som ekv. (272) med tabell 7 och C_T². I ekv. (273) står L_ts0 utan primtecken
    i andra raden; L′_ts0 från första raden används (se loggen). −∞ när C_T² = 0 eller h_e ≤ 0.
    """
    _kontrollera_indata(frekvens_hz, 0.0, ct2)
    return _spridd_niva_db(LTS0_DB, frekvens_hz, geo, ct2, medeltemperatur_c, markfaktor_pg)


def turbulensspridning_db(
    frekvens_hz: float,
    geo: Spridningsgeometri,
    cv2: float,
    ct2: float,
    medeltemperatur_c: float,
    markfaktor_pg: float = 1.0,
) -> float:
    """ΔL₇ = ΔL_ws ⊕ ΔL_ts för en frekvens, AV 1106/07 avsnitt 5.16, ekv. (274).

    `geo` kommer ur `spridningsgeometri` (en kant, delmodell 7,1) eller
    `spridningsgeometri_tva_kanter` (delmodell 7,2). `medeltemperatur_c` är t_mean.
    `markfaktor_pg` är p_G ur den skärmmodell som bidraget läggs till; 1 betyder utan mark.
    Returnerar −∞ dB när bidrag saknas (C_v² = C_T² = 0, eller kanten inte ovanför linjen S–R).
    """
    _kontrollera_indata(frekvens_hz, cv2, ct2)
    return energisumma_db(
        vindspridning_db(frekvens_hz, geo, cv2, medeltemperatur_c, markfaktor_pg),
        temperaturspridning_db(frekvens_hz, geo, ct2, medeltemperatur_c, markfaktor_pg),
    )


def turbulensspridning_band_db(
    frekvenser_hz: Flyttal,
    geo: Spridningsgeometri,
    cv2: float,
    ct2: float,
    medeltemperatur_c: float,
    markfaktor_pg: Flyttal | None = None,
) -> Flyttal:
    """ΔL₇ per tersband, AV 1106/07 avsnitt 5.16, ekv. (271)–(274).

    `markfaktor_pg` har ett värde per frekvens (p_G beror på frekvensen); `None` betyder
    p_G = 1 i alla band, alltså C_SR = 1.
    """
    n = frekvenser_hz.shape[0]
    if markfaktor_pg is not None and markfaktor_pg.shape != (n,):
        raise BullerFel("Delmodell 7: markfaktorn p_G ska ha ett värde per frekvens.")
    resultat = np.empty(n, dtype=np.float64)
    for i in range(n):
        p_g = 1.0 if markfaktor_pg is None else float(markfaktor_pg[i])
        resultat[i] = turbulensspridning_db(
            float(frekvenser_hz[i]), geo, cv2, ct2, medeltemperatur_c, p_g
        )
    return resultat


def turbulensspridning_en_kant_db(
    frekvenser_hz: Flyttal,
    x_s_m: float,
    z_s_m: float,
    x_r_m: float,
    z_r_m: float,
    x_t_m: float,
    z_t_m: float,
    cv2: float,
    ct2: float,
    medeltemperatur_c: float,
    markfaktor_pg: Flyttal | None = None,
) -> Flyttal:
    """Delmodell 7,1: ΔL₇ per band för källa S, mottagare R och skärmkant T.

    AV 1106/07 avsnitt 5.16, figur 26 och ekv. (271)–(274), icke-brytande atmosfär. För
    brytande atmosfär ersätts S och R med S′ och R′ ur `korrigerad_kalla_och_mottagare`
    (ekv. 275).
    """
    geo = spridningsgeometri(x_s_m, z_s_m, x_r_m, z_r_m, x_t_m, z_t_m)
    return turbulensspridning_band_db(
        frekvenser_hz, geo, cv2, ct2, medeltemperatur_c, markfaktor_pg
    )


def turbulensspridning_tva_kanter_db(
    frekvenser_hz: Flyttal,
    x_s_m: float,
    z_s_m: float,
    x_r_m: float,
    z_r_m: float,
    x_t1_m: float,
    z_t1_m: float,
    x_t2_m: float,
    z_t2_m: float,
    cv2: float,
    ct2: float,
    medeltemperatur_c: float,
    markfaktor_pg: Flyttal | None = None,
) -> Flyttal:
    """Delmodell 7,2: ΔL₇ per band för två kanter T₁ och T₂ via en ekvivalent skärmtopp.

    AV 1106/07 avsnitt 5.16, figur 27 och ekv. (276)–(277) följt av ekv. (271)–(274).
    `markfaktor_pg` är här markeffektdelen av tvåkantsmodellen (delmodell 5 eller 6).
    """
    geo = spridningsgeometri_tva_kanter(x_s_m, z_s_m, x_r_m, z_r_m, x_t1_m, z_t1_m, x_t2_m, z_t2_m)
    return turbulensspridning_band_db(
        frekvenser_hz, geo, cv2, ct2, medeltemperatur_c, markfaktor_pg
    )
