"""Delmodell 10 i Nord2000: spridningszoner (skog).

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.3.3 (indata) och 5.19, ekv. (288)–(294), tabell 8
och 9, samt avsnitt 5.22, ekv. (333). FORCE TC-101327 (2019) ändrar inget i dessa avsnitt.
Tabellerna ligger i `data/spridningszon_v1.toml` med dokument, tabell och sida som källa.

Modulen ger två storheter per tersband:

* ΔL_s, utbredningseffekten av spridningszonen (ekv. 291 och 333), en egen term i ekv. (329),
* F_s, koherenskoefficienten (ekv. 288) som går in i terrängmodellernas F (ekv. 110).

Strålens höjd genom zonen är indata i form av en `Stralbana` (ekv. 292). Här finns bara den
räta strålen (`RatStrale`, homogen atmosfär); en cirkelbåge eller en bana över skärmkanter
kan läggas till senare utan att något annat i modulen ändras.

Täckning: en typ av spridningszon längs banan (lika n_Q, h, α och a i alla bälten), som i
AV 1106/07. Utanför tabell 9:s område (h' utanför 0,01–1, α utanför 0–0,4, R' över 10) ger
modulen `BullerFel`; valen i övrigt står i `docs/buller/logg/P-10.md` under "Tolkningar".
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from buller.nord2000.frekvenser import vaglangd_m, vagtal_per_m
from buller.nord2000.fresnel import calc_fzd
from buller.typer import BullerFel, Flyttal

DATAFIL = Path(__file__).parent / "data" / "spridningszon_v1.toml"

# AV 1106/07 ekv. (291): faktorn 1,25 och den nedre gränsen −15 dB. I AV 1849/00 ekv. (173) heter
# faktorn k_p och är 1,25 för skog; AV 1106/07 har bara skog och har skrivit in talet i formeln.
PROPORTIONALITET_KP = 1.25
LAGSTA_DAMPNING_DB = -15.0
# AV 1106/07 ekv. (289): T = Min((R_sc·n_Q/1,75)², 1).
_T_NORMERING = 1.75
# AV 1106/07 avsnitt 5.19 (texten vid ekv. 293): Fresnelformen räknas med F_λ = 1/8.
_FRESNELANDEL = 1.0 / 8.0
# AV 1106/07 avsnitt 5.19 (texten efter ekv. 293): 100 lika breda remsor i trapetsregeln.
ANTAL_REMSOR = 100
_BISEKTIONSSTEG = 60


# --------------------------------------------------------------------------------------------
# Tabeller
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Spridningstabeller:
    """Tabell 8 och 9 i AV 1106/07 avsnitt 5.19, inlästa ur datafilen.

    `dl_db[i, j, k]` är ΔL(h', α, R') för `r_norm[i]`, `h_norm[j]` och `alfa[k]`.
    """

    dataversion: str
    ka: Flyttal  # form (8,), tabell 8
    kf: Flyttal  # form (8,)
    r_norm: Flyttal  # form (12,), R' i tabell 9
    h_norm: Flyttal  # form (3,), h'
    alfa: Flyttal  # form (3,), α
    dl_db: Flyttal  # form (12, 3, 3)


def _tallista(varde: Any, namn: str, fil: Path) -> list[float]:
    """En lista av tal ur datafilen. Tomma eller icke-numeriska värden ger `BullerFel`."""
    if not isinstance(varde, list) or not varde:
        raise BullerFel(f"Spridningszonsdata {fil}: {namn} saknas eller är tom.")
    for element in varde:
        if isinstance(element, bool) or not isinstance(element, int | float):
            raise BullerFel(
                f"Spridningszonsdata {fil}: {namn} innehåller värdet {element!r}, som inte är "
                "ett tal. Ett tabellvärde som inte bekräftats ur källan får inte användas."
            )
        if not math.isfinite(float(element)):
            raise BullerFel(f"Spridningszonsdata {fil}: {namn} innehåller ett icke-ändligt tal.")
    return [float(element) for element in varde]


def _strikt_vaxande(varden: list[float]) -> bool:
    return all(b > a for a, b in zip(varden[:-1], varden[1:], strict=True))


def _icke_avtagande(varden: list[float]) -> bool:
    return all(b >= a for a, b in zip(varden[:-1], varden[1:], strict=True))


def las_spridningstabeller(fil: str | Path = DATAFIL) -> Spridningstabeller:
    """Läser tabell 8 och 9 (AV 1106/07 avsnitt 5.19) ur datafilen och kontrollerar formen.

    Ett saknat, tomt eller icke-numeriskt värde ger `BullerFel`, liksom fel antal värden eller
    koordinataxlar som inte är strikt växande. Inget värde fylls i eller gissas.
    """
    sokvag = Path(fil)
    try:
        data: dict[str, Any] = tomllib.loads(sokvag.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise BullerFel(f"Kunde inte läsa spridningszonsdata {sokvag}: {exc}") from exc
    meta = data.get("meta")
    tabell8 = data.get("tabell8")
    tabell9 = data.get("tabell9")
    if not isinstance(meta, dict) or not isinstance(tabell8, dict) or not isinstance(tabell9, dict):
        raise BullerFel(f"Spridningszonsdata {sokvag}: [meta], [tabell8] eller [tabell9] saknas.")
    if not isinstance(meta.get("dataversion"), str):
        raise BullerFel(f"Spridningszonsdata {sokvag}: [meta] saknar dataversion.")
    for namn, tabell in (("tabell8", tabell8), ("tabell9", tabell9)):
        if not isinstance(tabell.get("kalla"), str) or not tabell["kalla"]:
            raise BullerFel(f"Spridningszonsdata {sokvag}: [{namn}] saknar källhänvisning.")

    ka = _tallista(tabell8.get("ka"), "tabell8.ka", sokvag)
    kf = _tallista(tabell8.get("kf"), "tabell8.kf", sokvag)
    if len(ka) != len(kf) or len(ka) < 2 or not _strikt_vaxande(ka):
        raise BullerFel(f"Spridningszonsdata {sokvag}: tabell 8 har fel form.")
    if any(not 0.0 <= v <= 1.0 for v in kf) or not _icke_avtagande(kf):
        raise BullerFel(f"Spridningszonsdata {sokvag}: k_f ska ligga i [0, 1] och inte avta.")

    r_norm = _tallista(tabell9.get("r_norm"), "tabell9.r_norm", sokvag)
    h_norm = _tallista(tabell9.get("h_norm"), "tabell9.h_norm", sokvag)
    alfa = _tallista(tabell9.get("alfa"), "tabell9.alfa", sokvag)
    for namn, axel in (("r_norm", r_norm), ("h_norm", h_norm), ("alfa", alfa)):
        if len(axel) < 3 or not _strikt_vaxande(axel):
            raise BullerFel(
                f"Spridningszonsdata {sokvag}: tabell9.{namn} ska ha minst tre strikt växande "
                "värden."
            )
    if r_norm[0] <= 0.0 or h_norm[0] <= 0.0:
        raise BullerFel(f"Spridningszonsdata {sokvag}: R' och h' i tabell 9 ska vara positiva.")
    rader = tabell9.get("dl_db")
    if not isinstance(rader, list) or len(rader) != len(r_norm):
        raise BullerFel(
            f"Spridningszonsdata {sokvag}: tabell9.dl_db ska ha en rad per R' ({len(r_norm)})."
        )
    kolumner = len(h_norm) * len(alfa)
    dl = np.empty((len(r_norm), len(h_norm), len(alfa)), dtype=np.float64)
    for i, rad in enumerate(rader):
        varden = _tallista(rad, f"tabell9.dl_db[{i}]", sokvag)
        if len(varden) != kolumner:
            raise BullerFel(
                f"Spridningszonsdata {sokvag}: tabell9.dl_db[{i}] har {len(varden)} värden, "
                f"ska ha {kolumner}."
            )
        dl[i] = np.asarray(varden, dtype=np.float64).reshape(len(h_norm), len(alfa))
    return Spridningstabeller(
        dataversion=meta["dataversion"],
        ka=np.asarray(ka, dtype=np.float64),
        kf=np.asarray(kf, dtype=np.float64),
        r_norm=np.asarray(r_norm, dtype=np.float64),
        h_norm=np.asarray(h_norm, dtype=np.float64),
        alfa=np.asarray(alfa, dtype=np.float64),
        dl_db=dl,
    )


@lru_cache(maxsize=1)
def standardtabeller() -> Spridningstabeller:
    """Tabellerna ur `DATAFIL`, inlästa en gång per process."""
    return las_spridningstabeller(DATAFIL)


# --------------------------------------------------------------------------------------------
# Tabell 8, ekv. (288)–(290)
# --------------------------------------------------------------------------------------------


def stamtathet_nq_per_m(stamradie_m: float, tathet_per_m2: float) -> float:
    """n_Q = 2·a·n″ för skog, AV 1106/07 avsnitt 5.19, ekv. (290).

    `stamradie_m` är medelstamradien a och `tathet_per_m2` antalet stammar per kvadratmeter n″.
    """
    if stamradie_m <= 0.0 or tathet_per_m2 < 0.0:
        raise BullerFel("Stamradien ska vara större än 0 och stamtätheten får inte vara negativ.")
    return 2.0 * stamradie_m * tathet_per_m2


def frekvensvikt_kf(ka: float, tabeller: Spridningstabeller | None = None) -> float:
    """Frekvensvikten k_f för skog, AV 1106/07 avsnitt 5.19, tabell 8.

    Linjär interpolation i ka mellan tabellvärdena, som avsnittet föreskriver. Över tabellens
    sista värde (ka = 20, k_f = 1,00) hålls k_f = 1 (se loggen, "Tolkningar").
    """
    if not ka >= 0.0:
        raise BullerFel("ka (vågtal gånger stamradie) får inte vara negativt.")
    tab = tabeller if tabeller is not None else standardtabeller()
    if ka >= float(tab.ka[-1]):
        return float(tab.kf[-1])
    return float(np.interp(ka, tab.ka, tab.kf))


def genomslag_t(stracka_i_zon_m: float, nq_per_m: float) -> float:
    """Variabeln T = Min((R_sc·n_Q/1,75)², 1), AV 1106/07 avsnitt 5.19, ekv. (289)."""
    if stracka_i_zon_m < 0.0 or nq_per_m < 0.0:
        raise BullerFel("Sträckan i spridningszonen och n_Q får inte vara negativa.")
    return min((stracka_i_zon_m * nq_per_m / _T_NORMERING) ** 2, 1.0)


def koherens_spridningszon(kf: float, t: float) -> float:
    """Koherenskoefficienten F_s = 1 − k_f·T, AV 1106/07 avsnitt 5.19, ekv. (288)."""
    return 1.0 - kf * t


# --------------------------------------------------------------------------------------------
# Tabell 9 och ekv. (291)
# --------------------------------------------------------------------------------------------


def naturlig_kubisk_spline(x: Flyttal, y: Flyttal, xq: float) -> float:
    """Värdet i `xq` av den naturliga kubiska splinen genom punkterna (x, y).

    Detta är inte en formel ur AV 1106/07: avsnitt 5.19 säger bara "cubic interpolation". Den
    naturliga splinen (andraderivatan noll i ändpunkterna) är vald för att den är entydigt
    bestämd även med tabell 9:s tre värden i h' och α. `xq` ska ligga inom [x[0], x[-1]].
    """
    n = x.shape[0]
    if n < 3 or y.shape != (n,):
        raise BullerFel("Splinen kräver minst tre punkter och lika långa x och y.")
    if not float(x[0]) <= xq <= float(x[-1]):
        raise BullerFel("Splinen extrapolerar inte: värdet ligger utanför tabellen.")
    h = np.diff(x)
    matris = np.zeros((n, n), dtype=np.float64)
    hogerled = np.zeros(n, dtype=np.float64)
    matris[0, 0] = 1.0
    matris[n - 1, n - 1] = 1.0
    for i in range(1, n - 1):
        matris[i, i - 1] = h[i - 1]
        matris[i, i] = 2.0 * (h[i - 1] + h[i])
        matris[i, i + 1] = h[i]
        hogerled[i] = 6.0 * ((y[i + 1] - y[i]) / h[i] - (y[i] - y[i - 1]) / h[i - 1])
    m = np.linalg.solve(matris, hogerled)  # andraderivator i knutpunkterna
    i = int(np.searchsorted(x, xq, side="right")) - 1
    i = min(max(i, 0), n - 2)
    hi = float(h[i])
    t = float(x[i + 1]) - xq
    u = xq - float(x[i])
    if u == 0.0:
        return float(y[i])
    if t == 0.0:
        return float(y[i + 1])
    return float(
        (m[i] * t**3 + m[i + 1] * u**3) / (6.0 * hi)
        + (y[i] / hi - m[i] * hi / 6.0) * t
        + (y[i + 1] / hi - m[i + 1] * hi / 6.0) * u
    )


def tabellvarde_dl_db(
    h_norm: float, alfa: float, r_norm: float, tabeller: Spridningstabeller | None = None
) -> float:
    """ΔL(h', α, R') ur tabell 9, AV 1106/07 avsnitt 5.19, med kubisk interpolation.

    Naturlig kubisk spline längs en axel i taget: i R' (linjär skala, 12 knutpunkter), i α
    (linjär, 3) och i lg h' (3; knutpunkterna 0,01, 0,1 och 1 är jämnt fördelade i lg h').
    Eftersom splinen är linjär i tabellvärdena spelar axlarnas ordning ingen roll, och
    knutpunkterna återges exakt. Utanför tabellen ges `BullerFel`: metoden anger ingen
    extrapolation.
    """
    tab = tabeller if tabeller is not None else standardtabeller()
    if not float(tab.h_norm[0]) <= h_norm <= float(tab.h_norm[-1]):
        raise BullerFel(
            f"Normerad höjd h' = n_Q·h = {h_norm:.4g} ligger utanför tabell 9 "
            f"({float(tab.h_norm[0]):g}–{float(tab.h_norm[-1]):g}); AV 1106/07 anger ingen "
            "extrapolation."
        )
    if not float(tab.alfa[0]) <= alfa <= float(tab.alfa[-1]):
        raise BullerFel(
            f"Absorptionskoefficienten α = {alfa:.4g} ligger utanför tabell 9 "
            f"({float(tab.alfa[0]):g}–{float(tab.alfa[-1]):g})."
        )
    if not float(tab.r_norm[0]) <= r_norm <= float(tab.r_norm[-1]):
        raise BullerFel(
            f"Normerad sträcka R' = n_Q·R_sc = {r_norm:.4g} ligger utanför tabell 9 "
            f"({float(tab.r_norm[0]):g}–{float(tab.r_norm[-1]):g})."
        )
    antal_h = tab.h_norm.shape[0]
    antal_alfa = tab.alfa.shape[0]
    per_h = np.empty(antal_h, dtype=np.float64)
    for j in range(antal_h):
        per_alfa = np.empty(antal_alfa, dtype=np.float64)
        for k in range(antal_alfa):
            per_alfa[k] = naturlig_kubisk_spline(tab.r_norm, tab.dl_db[:, j, k], r_norm)
        per_h[j] = naturlig_kubisk_spline(tab.alfa, per_alfa, alfa)
    lg_h: Flyttal = np.log10(tab.h_norm)
    return naturlig_kubisk_spline(lg_h, per_h, math.log10(h_norm))


def nivakorrektion_ae_db(
    h_norm: float, alfa: float, r_norm: float, tabeller: Spridningstabeller | None = None
) -> float:
    """A_e(R_sc) = ΔL(h', α, R') + 20·lg(8·R'), AV 1106/07 avsnitt 5.19, ekv. (291).

    Under tabell 9:s minsta R' (0,0625) hålls A_e lika med värdet i tabellens första rad
    (−0,02 dB), se loggen under "Tolkningar"; där är T ≤ 0,0013, så bidraget till ΔL_s är
    under 10⁻⁴ dB. `r_norm` ska vara större än 0.
    """
    if r_norm <= 0.0:
        raise BullerFel("A_e är bara definierad för R' > 0.")
    tab = tabeller if tabeller is not None else standardtabeller()
    r = max(r_norm, float(tab.r_norm[0]))
    return tabellvarde_dl_db(h_norm, alfa, r, tab) + 20.0 * math.log10(8.0 * r)


def spridningseffekt_db(kf: float, t: float, ae_db: float) -> float:
    """ΔL_10 = Max(1,25·k_f·T·A_e(R_sc), −15), AV 1106/07 avsnitt 5.19, ekv. (291)."""
    return max(PROPORTIONALITET_KP * kf * t * ae_db, LAGSTA_DAMPNING_DB)


# --------------------------------------------------------------------------------------------
# Indata: zon och stråle (avsnitt 5.3.3 och ekv. 292)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Zonbalte:
    """Ett bälte i spridningszonen, AV 1106/07 avsnitt 5.3.3 (x_i,1 och x_i,2) och figur 30.

    `x_start_m` och `x_slut_m` är i terrängprofilens x-koordinat. `topp_z_m` är zonens överkant
    z_sc i samma höjdsystem som strålen (normalt markens höjd plus trädhöjden). En zon vars
    överkant varierar längs banan beskrivs med flera bälten intill varandra. `y_min_m` och
    `y_max_m` är zonens utsträckning tvärs banan, räknat från strålen (y = 0); utelämnade
    betyder obegränsad bredd.
    """

    x_start_m: float
    x_slut_m: float
    topp_z_m: float
    y_min_m: float = -math.inf
    y_max_m: float = math.inf

    def __post_init__(self) -> None:
        if not (math.isfinite(self.x_start_m) and math.isfinite(self.x_slut_m)):
            raise BullerFel("Bältets gränser i x ska vara ändliga.")
        if not self.x_start_m < self.x_slut_m:
            raise BullerFel("Ett bälte ska ha x_start_m < x_slut_m.")
        if not math.isfinite(self.topp_z_m):
            raise BullerFel("Bältets överkant ska vara ett ändligt tal.")
        if not self.y_min_m < self.y_max_m:
            raise BullerFel("Ett bälte ska ha y_min_m < y_max_m.")


@dataclass(frozen=True)
class Spridningszon:
    """Spridningszonens variabler, AV 1106/07 avsnitt 5.3.3 och 5.19.

    Alla bälten har samma egenskaper; blandade zontyper ingår inte i AV 1106/07 (avsnitt 5.19,
    andra stycket). `nq_per_m` är n_Q (för skog 2·a·n″, ekv. 290), `medelhojd_m` är h (träden
    medelhöjd, ger h' = n_Q·h), `absorption` är α och `stamradie_m` är a (ger ka i tabell 8).
    """

    balten: tuple[Zonbalte, ...]
    nq_per_m: float
    medelhojd_m: float
    absorption: float
    stamradie_m: float

    def __post_init__(self) -> None:
        if not self.balten:
            raise BullerFel("En spridningszon ska ha minst ett bälte.")
        for foregaende, nasta in zip(self.balten[:-1], self.balten[1:], strict=True):
            if nasta.x_start_m < foregaende.x_slut_m:
                raise BullerFel(
                    "Bältena ska stå i ordning längs banan och får inte överlappa "
                    "(AV 1106/07 avsnitt 5.3.3)."
                )
        if not (self.nq_per_m > 0.0 and math.isfinite(self.nq_per_m)):
            raise BullerFel("n_Q ska vara större än 0.")
        if not (self.medelhojd_m > 0.0 and math.isfinite(self.medelhojd_m)):
            raise BullerFel("Trädens medelhöjd ska vara större än 0.")
        if not 0.0 <= self.absorption <= 1.0:
            raise BullerFel("Absorptionskoefficienten ska ligga mellan 0 och 1.")
        if not (self.stamradie_m > 0.0 and math.isfinite(self.stamradie_m)):
            raise BullerFel("Medelstamradien ska vara större än 0.")

    @property
    def normerad_hojd(self) -> float:
        """h' = n_Q·h, AV 1106/07 avsnitt 5.19 (texten efter ekv. 291)."""
        return self.nq_per_m * self.medelhojd_m


@dataclass(frozen=True)
class Stralpunkt:
    """Strålens höjd z_ray(x) och väglängderna R₁(x) till källan och R₂(x) till mottagaren."""

    z_m: float
    r1_m: float
    r2_m: float


class Stralbana(Protocol):
    """Den stråle som R_sc mäts längs, AV 1106/07 avsnitt 5.19, ekv. (292).

    `punkt(x)` ska vara definierad för x_start_m ≤ x ≤ x_slut_m (källans och mottagarens
    x-koordinat i terrängprofilen), med z i samma höjdsystem som `Zonbalte.topp_z_m` och R₁
    växande med x. En krökt stråle (RayCurvature och HeightOfCircularRay) eller en bana över
    skärmkanter (figur 29) uppfyller samma gränssnitt.
    """

    @property
    def x_start_m(self) -> float: ...

    @property
    def x_slut_m(self) -> float: ...

    def punkt(self, x_m: float) -> Stralpunkt: ...


@dataclass(frozen=True)
class RatStrale:
    """Rät stråle från källan till mottagaren (homogen atmosfär), figur 28 och 30.

    Gränsvärdet ξ → 0 av HeightOfCircularRay i ekv. (292), på samma sätt som räta strålar
    används i övriga kärnan (se `docs/buller/tolkningar.md`).
    """

    x_kalla_m: float
    z_kalla_m: float
    x_mottagare_m: float
    z_mottagare_m: float

    def __post_init__(self) -> None:
        if not self.x_kalla_m < self.x_mottagare_m:
            raise BullerFel("Mottagaren ska ligga på större x än källan.")

    @property
    def x_start_m(self) -> float:
        return self.x_kalla_m

    @property
    def x_slut_m(self) -> float:
        return self.x_mottagare_m

    @property
    def langd_m(self) -> float:
        return math.hypot(self.x_mottagare_m - self.x_kalla_m, self.z_mottagare_m - self.z_kalla_m)

    def punkt(self, x_m: float) -> Stralpunkt:
        andel = (x_m - self.x_kalla_m) / (self.x_mottagare_m - self.x_kalla_m)
        r1 = andel * self.langd_m
        return Stralpunkt(
            z_m=self.z_kalla_m + andel * (self.z_mottagare_m - self.z_kalla_m),
            r1_m=r1,
            r2_m=self.langd_m - r1,
        )


# --------------------------------------------------------------------------------------------
# Sträckan i zonen och begränsad höjd (ekv. 292–294)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Zonsnitt:
    """En sträcka x₁–x₂ där strålen går inne i ett bälte (under dess överkant)."""

    x1_m: float
    x2_m: float
    balte: Zonbalte


def _rot(strale: Stralbana, topp_z_m: float, x_under: float, x_over: float) -> float:
    """Punkten där strålen korsar bältets överkant, med intervallhalvering."""
    for _ in range(_BISEKTIONSSTEG):
        mitt = 0.5 * (x_under + x_over)
        if strale.punkt(mitt).z_m < topp_z_m:
            x_under = mitt
        else:
            x_over = mitt
    return 0.5 * (x_under + x_over)


def zonsnitt(strale: Stralbana, balten: tuple[Zonbalte, ...]) -> list[Zonsnitt]:
    """Skärningen mellan strålen och bältena: x₁ och x₂ i AV 1106/07 ekv. (294).

    Strålen är inne i ett bälte där x ligger inom bältet och z_ray(x) < z_sc. Ligger zonen
    under strålen finns ingen skärning och R_sc blir noll (avsnitt 5.19, stycket efter
    tabell 9). Korsningar med överkanten söks i `ANTAL_REMSOR` lika breda remsor per bälte och
    förfinas med intervallhalvering; en stråle som dyker under överkanten och upp igen inom en
    och samma remsa upptäcks alltså inte.
    """
    snitt: list[Zonsnitt] = []
    for balte in balten:
        xa = max(balte.x_start_m, strale.x_start_m)
        xb = min(balte.x_slut_m, strale.x_slut_m)
        if not xa < xb:
            continue
        x = [xa + (xb - xa) * i / ANTAL_REMSOR for i in range(ANTAL_REMSOR + 1)]
        inne = [strale.punkt(xi).z_m < balte.topp_z_m for xi in x]
        start: float | None = xa if inne[0] else None
        for i in range(ANTAL_REMSOR):
            if inne[i] == inne[i + 1]:
                continue
            if inne[i]:
                slut = _rot(strale, balte.topp_z_m, x[i], x[i + 1])
                if start is not None and slut > start:
                    snitt.append(Zonsnitt(start, slut, balte))
                start = None
            else:
                start = _rot(strale, balte.topp_z_m, x[i + 1], x[i])
        if start is not None and xb > start:
            snitt.append(Zonsnitt(start, xb, balte))
    return snitt


def stracka_i_zon_m(strale: Stralbana, snitt: list[Zonsnitt]) -> float:
    """R_sc = ΣR_sc,i: strålens väglängd inne i bältena, AV 1106/07 avsnitt 5.19, figur 28."""
    return sum(strale.punkt(s.x2_m).r1_m - strale.punkt(s.x1_m).r1_m for s in snitt)


def fresnelradie_m(stralpunkt: Stralpunkt, vaglangd: float) -> float:
    """r_Fz(f, x) = CalcFZd(R₁(x), R₂(x), π/2, λ/8), AV 1106/07 avsnitt 5.19, ekv. (293)."""
    return calc_fzd(stralpunkt.r1_m, stralpunkt.r2_m, math.pi / 2.0, _FRESNELANDEL * vaglangd)


def _trapets(varden: list[float], steg: float) -> float:
    return steg * (0.5 * varden[0] + sum(varden[1:-1]) + 0.5 * varden[-1])


def volymkvot(strale: Stralbana, snitt: list[Zonsnitt], vaglangd: float) -> float:
    """Kvoten V_sc/V_Fz i AV 1106/07 avsnitt 5.19, ekv. (294).

    V_Fz = ∫ 2·r_Fz² dx är volymen av den förenklade halva Fresnelformen över strålen
    (figur 31) och V_sc = ∫∫ Δz_sc dy dx den del av den som ligger i spridningszonen, där
    Δz_sc = z_sc − z_ray begränsat till [0, r_Fz]. Integralen i x tas över `snitt` med
    trapetsregeln och `ANTAL_REMSOR` remsor per snitt. Integralen i y räknas exakt, eftersom
    bältets överkant är konstant tvärs banan inom [y_min, y_max]: Δz_sc gånger den del av
    [−r_Fz, r_Fz] som ligger i bältet.
    """
    v_fz = 0.0
    v_sc = 0.0
    for s in snitt:
        steg = (s.x2_m - s.x1_m) / ANTAL_REMSOR
        fz: list[float] = []
        sc: list[float] = []
        for i in range(ANTAL_REMSOR + 1):
            p = strale.punkt(s.x1_m + i * steg)
            r = fresnelradie_m(p, vaglangd)
            dz = min(max(s.balte.topp_z_m - p.z_m, 0.0), r)
            bredd = max(min(r, s.balte.y_max_m) - max(-r, s.balte.y_min_m), 0.0)
            fz.append(2.0 * r * r)
            sc.append(dz * bredd)
        v_fz += _trapets(fz, steg)
        v_sc += _trapets(sc, steg)
    if v_fz <= 0.0:
        return 0.0
    return v_sc / v_fz


def modifierad_stracka_m(
    strale: Stralbana, snitt: list[Zonsnitt], frekvens_hz: float, ljudhastighet_m_s: float
) -> float:
    """R′_sc = R_sc·V_sc/V_Fz, AV 1106/07 avsnitt 5.19, ekv. (294).

    Ersätter R_sc i ekv. (289) och (291) och i R′ = n_Q·R_sc. Våglängden är λ = c/f med
    ljudhastigheten vid marken (ekv. 13).
    """
    r_sc = stracka_i_zon_m(strale, snitt)
    if r_sc <= 0.0:
        return 0.0
    return r_sc * volymkvot(strale, snitt, vaglangd_m(frekvens_hz, ljudhastighet_m_s))


# --------------------------------------------------------------------------------------------
# Sammansatt: ΔL_s och F_s per tersband
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Spridningszonresultat:
    """Delmodell 10 per tersband: ΔL_s (ekv. 333) och F_s (ekv. 288)."""

    dampning_db: Flyttal  # ΔL_s, form (antal band,)
    koherens: Flyttal  # F_s i [0, 1], form (antal band,)
    stracka_m: float  # R_sc, strålens väglängd i zonen
    modifierad_stracka_m: Flyttal  # R′_sc per band (begränsad höjd och bredd)


def spridningszon(
    frekvenser_hz: Flyttal,
    strale: Stralbana,
    zon: Spridningszon | None,
    ljudhastighet_m_s: float,
    tabeller: Spridningstabeller | None = None,
) -> Spridningszonresultat:
    """Delmodell 10: ΔL_s och F_s per tersband, AV 1106/07 avsnitt 5.19 och ekv. (333).

    Utan zon (`None`), eller när strålen inte går genom något bälte, är ΔL_s = 0 och F_s = 1.
    Annars, per band: R′_sc ur ekv. (292)–(294), T ur ekv. (289), k_f ur tabell 8 med
    ka = k·a (k enligt ekv. 14), F_s ur ekv. (288) och ΔL_s ur ekv. (291) med tabell 9.
    `ljudhastighet_m_s` är ljudhastigheten vid marken.
    """
    antal = frekvenser_hz.shape[0]
    dampning = np.zeros(antal, dtype=np.float64)
    koherens = np.ones(antal, dtype=np.float64)
    modifierad = np.zeros(antal, dtype=np.float64)
    if ljudhastighet_m_s <= 0.0:
        raise BullerFel("Ljudhastigheten ska vara större än 0.")
    if zon is None:
        return Spridningszonresultat(dampning, koherens, 0.0, modifierad)
    snitt = zonsnitt(strale, zon.balten)
    r_sc = stracka_i_zon_m(strale, snitt)
    if r_sc <= 0.0:
        return Spridningszonresultat(dampning, koherens, 0.0, modifierad)
    tab = tabeller if tabeller is not None else standardtabeller()
    h_norm = zon.normerad_hojd
    # Kontrollerar h' och α mot tabell 9 en gång, så att felet inte beror på frekvensen.
    tabellvarde_dl_db(h_norm, zon.absorption, float(tab.r_norm[0]), tab)
    for i, f_np in enumerate(frekvenser_hz):
        f = float(f_np)
        if f <= 0.0:
            raise BullerFel("Frekvensen ska vara större än 0 Hz.")
        r_mod = r_sc * volymkvot(strale, snitt, vaglangd_m(f, ljudhastighet_m_s))
        modifierad[i] = r_mod
        if r_mod <= 0.0:
            continue
        kf = frekvensvikt_kf(vagtal_per_m(f, ljudhastighet_m_s) * zon.stamradie_m, tab)
        t = genomslag_t(r_mod, zon.nq_per_m)
        koherens[i] = koherens_spridningszon(kf, t)
        if kf * t > 0.0:
            ae = nivakorrektion_ae_db(h_norm, zon.absorption, zon.nq_per_m * r_mod, tab)
            dampning[i] = spridningseffekt_db(kf, t, ae)
    return Spridningszonresultat(dampning, koherens, r_sc, modifierad)
