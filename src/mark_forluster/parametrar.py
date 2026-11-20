"""Markslagskoder (K1/v1) och Green–Ampt-parametrar för svenska jordarter."""

from __future__ import annotations

import math
from dataclasses import dataclass

# Markslagskoder enligt kontrakt K1/v1 i docs/DATAKONTRAKT.md
OKANT = 0
HARDGJORT = 1
TAK = 2
GRONT_TAK = 3
GRAS = 4
SKOG = 5
BERG = 6
GRUS = 7
VATTEN = 8
BYGGNAD = 9

GILTIGA_MARKSLAG: frozenset[int] = frozenset(
    {OKANT, HARDGJORT, TAK, GRONT_TAK, GRAS, SKOG, BERG, GRUS, VATTEN, BYGGNAD}
)

MARKSLAG_NAMN: dict[int, str] = {
    OKANT: "okant",
    HARDGJORT: "hardgjort",
    TAK: "tak",
    GRONT_TAK: "gront_tak",
    GRAS: "gras",
    SKOG: "skog",
    BERG: "berg",
    GRUS: "grus",
    VATTEN: "vatten",
    BYGGNAD: "byggnad",
}

# Jordartskoder (SGU-klasser) för rasterrepresentation
JORDART_KOD_BERG = 1
JORDART_KOD_MORAN = 2
JORDART_KOD_SAND = 3
JORDART_KOD_SILT = 4
JORDART_KOD_LERA = 5
JORDART_KOD_FYLLNING = 6
JORDART_KOD_TORV = 7

JORDART_NAMN_TILL_KOD: dict[str, int] = {
    "berg": JORDART_KOD_BERG,
    "moran": JORDART_KOD_MORAN,
    "sand": JORDART_KOD_SAND,
    "silt": JORDART_KOD_SILT,
    "lera": JORDART_KOD_LERA,
    "fyllning": JORDART_KOD_FYLLNING,
    "torv": JORDART_KOD_TORV,
}

JORDART_KOD_TILL_NAMN: dict[int, str] = {
    kod: namn for namn, kod in JORDART_NAMN_TILL_KOD.items()
}

# Reduktionsfaktorer vid tjäle (frusen mark: kraftigt nedsatt genomsläpplighet)
TJALE_KSAT_FAKTOR = 0.05
TJALE_PORVOLYM_FAKTOR = 0.25


class ForlustFel(Exception):
    """Fel vid validering eller beräkning av infiltration, grundvatten eller brunnar."""


@dataclass(frozen=True)
class JordartParametrar:
    """Green–Ampt-parametrar för en jordartsklass.

    Enheter: `ksat_mm_h` i mm/h, `psi_m` i meter, `porositet` och fukthalter i m³/m³.
    """

    namn: str
    ksat_mm_h: float
    psi_m: float
    porositet: float
    fukthalt_normal: float
    fukthalt_hog: float

    def __post_init__(self) -> None:
        if not isinstance(self.namn, str) or not self.namn.strip():
            raise ForlustFel("Jordartens namn får inte vara tomt.")
        for falt, varde in (
            ("ksat_mm_h", self.ksat_mm_h),
            ("psi_m", self.psi_m),
            ("porositet", self.porositet),
            ("fukthalt_normal", self.fukthalt_normal),
            ("fukthalt_hog", self.fukthalt_hog),
        ):
            if not math.isfinite(varde):
                raise ForlustFel(f"Parametern '{falt}' för {self.namn} måste vara ändlig.")
        if self.ksat_mm_h < 0.0:
            raise ForlustFel(f"ksat_mm_h för {self.namn} måste vara >= 0.")
        if self.psi_m < 0.0:
            raise ForlustFel(f"psi_m för {self.namn} måste vara >= 0.")
        if not (0.0 < self.porositet <= 1.0):
            raise ForlustFel(f"porositet för {self.namn} måste ligga i (0, 1].")
        if not (0.0 <= self.fukthalt_normal <= self.fukthalt_hog <= self.porositet):
            raise ForlustFel(
                f"Fukthalter för {self.namn} måste uppfylla "
                "0 <= fukthalt_normal <= fukthalt_hog <= porositet."
            )

    def delta_theta(self, *, hog_markfukt: bool = False) -> float:
        """Tillgängligt fuktunderskott (theta_s - theta_i) i m³/m³."""
        fukt = self.fukthalt_hog if hog_markfukt else self.fukthalt_normal
        return max(0.0, self.porositet - fukt)


# Standardtabell för SGU:s jordartsklasser.
# Källor:
# - Rawls, Brakensiek & Miller (1983), "Green-Ampt Infiltration Parameters from Soils Data",
#   Journal of Hydraulic Engineering 109(1), 62–70 (ksat, sughöjd psi, effektiv porositet).
# - Svenskt Vatten P110 (2016), "Avledning av dag-, drän- och spillvatten" (avrinnings- och
#   infiltrationskarakteristik för urbana och naturliga ytor i Sverige).
STANDARD_JORDARTER: dict[str, JordartParametrar] = {
    # Urberg / sprickfattigt berg (Rawls/P110: mycket låg konduktivitet och porositet)
    "berg": JordartParametrar(
        namn="berg",
        ksat_mm_h=0.1,
        psi_m=0.05,
        porositet=0.02,
        fukthalt_normal=0.01,
        fukthalt_hog=0.018,
    ),
    # Morän: typisk svensk blandkornig (sandig-siltig) morän ~ Rawls sandy loam / loam
    "moran": JordartParametrar(
        namn="moran",
        ksat_mm_h=6.5,
        psi_m=0.17,
        porositet=0.36,
        fukthalt_normal=0.18,
        fukthalt_hog=0.30,
    ),
    # Sand / isälvsmaterial ~ Rawls sand / loamy sand
    "sand": JordartParametrar(
        namn="sand",
        ksat_mm_h=60.0,
        psi_m=0.06,
        porositet=0.43,
        fukthalt_normal=0.10,
        fukthalt_hog=0.25,
    ),
    # Silt / finmo ~ Rawls silt loam
    "silt": JordartParametrar(
        namn="silt",
        ksat_mm_h=6.8,
        psi_m=0.17,
        porositet=0.48,
        fukthalt_normal=0.22,
        fukthalt_hog=0.38,
    ),
    # Lera (glacial/postglacial lera) ~ Rawls clay
    "lera": JordartParametrar(
        namn="lera",
        ksat_mm_h=0.6,
        psi_m=0.32,
        porositet=0.45,
        fukthalt_normal=0.32,
        fukthalt_hog=0.42,
    ),
    # Fyllning (urban fyllnadsjord / schaktmassor, måttligt packad ~ P110 / sandy loam)
    "fyllning": JordartParametrar(
        namn="fyllning",
        ksat_mm_h=12.0,
        psi_m=0.11,
        porositet=0.38,
        fukthalt_normal=0.16,
        fukthalt_hog=0.30,
    ),
    # Torv (organogen jord med hög porositet och måttlig konduktivitet)
    "torv": JordartParametrar(
        namn="torv",
        ksat_mm_h=15.0,
        psi_m=0.12,
        porositet=0.80,
        fukthalt_normal=0.50,
        fukthalt_hog=0.72,
    ),
}

# Konstruerat substrat för gröna tak (P110: väl dränerande lättjord/pimpsten)
GRONT_TAK_SUBSTRAT = JordartParametrar(
    namn="gront_tak_substrat",
    ksat_mm_h=25.0,
    psi_m=0.08,
    porositet=0.40,
    fukthalt_normal=0.15,
    fukthalt_hog=0.30,
)


@dataclass(frozen=True)
class EffektivMarkParametrar:
    """Fysikaliska infiltrationsparametrar för en `(markslag, jordart)`-kombination."""

    ksat_mm_h: float
    psi_m: float
    delta_theta: float
    initial_faktor: float
    porositet: float


def normalisera_jordart(jordart: str | int) -> str:
    """Normaliserar jordartsklass (sträng eller kod) och kastar `ForlustFel` vid okänd klass."""
    if isinstance(jordart, int) and not isinstance(jordart, bool):
        if jordart not in JORDART_KOD_TILL_NAMN:
            tillatna = ", ".join(f"{k}={v}" for k, v in sorted(JORDART_KOD_TILL_NAMN.items()))
            raise ForlustFel(f"Okänd jordartskod {jordart}. Giltiga koder: {tillatna}.")
        return JORDART_KOD_TILL_NAMN[jordart]
    if isinstance(jordart, str):
        norm = jordart.strip().lower().replace("ä", "a")
        if norm not in STANDARD_JORDARTER:
            tillatna_str = ", ".join(sorted(STANDARD_JORDARTER))
            raise ForlustFel(f"Okänd jordartsklass '{jordart}'. Giltiga: {tillatna_str}.")
        return norm
    raise ForlustFel(f"Ogiltig typ för jordart: {type(jordart).__name__}.")


def hamta_jordart(
    jordart: str | int,
    tabell: dict[str, JordartParametrar] | None = None,
) -> JordartParametrar:
    """Hämtar `JordartParametrar` för given jordartsklass."""
    namn = normalisera_jordart(jordart)
    kalla = STANDARD_JORDARTER if tabell is None else tabell
    if namn not in kalla:
        raise ForlustFel(f"Jordartsklass '{namn}' saknas i parametertabellen.")
    return kalla[namn]


def validera_markslag(markslag: int) -> int:
    """Validerar en K1-markslagskod (`1..9`); okänd kod `0` eller utanför intervallet ger fel."""
    if not isinstance(markslag, int) or isinstance(markslag, bool):
        raise ForlustFel(f"Markslag måste vara ett heltal, fick {type(markslag).__name__}.")
    if markslag not in GILTIGA_MARKSLAG:
        raise ForlustFel(f"Ogiltig markslagskod {markslag}. Giltiga enligt K1/v1: 0..9.")
    if markslag == OKANT:
        raise ForlustFel("Okänt markslag (kod 0) saknar infiltrationsparametrar.")
    return markslag


def effektiv_markparametrar(
    markslag: int,
    jordart: str | int,
    *,
    hog_markfukt: bool = False,
    tjale: bool = False,
    tabell: dict[str, JordartParametrar] | None = None,
) -> EffektivMarkParametrar:
    """Kopplar K1-markslag och SGU-jordart till effektiva Green–Ampt-parametrar.

    Regler:
    - Hårdgjort (1), tak (2), byggnad (9): 0 infiltration, full initial ytmagasinering.
    - Vatten (8): 0 infiltration, 0 initial ytmagasinering (allt regn blir direkt avrinning).
    - Berg i dagen (6): mycket låg infiltration enligt `berg`, oberoende av djupjordart.
    - Grönt tak (3): konstruerat substrat (oberoende av underliggande jordart).
    - Gräs (4): jordartens egna parametrar.
    - Skog (5): makroporer/förna ger 1,5x Ksat och 1,5x initial interception.
    - Grus (7): genomsläppligt gruslager (minst 1,5x jordartens Ksat, dock minst 15 mm/h).
    - Tjäle (`tjale=True`): reducerar konduktivitet med `TJALE_KSAT_FAKTOR` och fuktunderskott
      med `TJALE_PORVOLYM_FAKTOR` på alla infiltrerande ytor.
    """
    kod = validera_markslag(markslag)
    jord = hamta_jordart(jordart, tabell=tabell)

    if kod in (HARDGJORT, TAK, BYGGNAD):
        return EffektivMarkParametrar(
            ksat_mm_h=0.0,
            psi_m=0.0,
            delta_theta=0.0,
            initial_faktor=1.0,
            porositet=0.0,
        )
    if kod == VATTEN:
        return EffektivMarkParametrar(
            ksat_mm_h=0.0,
            psi_m=0.0,
            delta_theta=0.0,
            initial_faktor=0.0,
            porositet=0.0,
        )

    if kod == BERG:
        bas = hamta_jordart("berg", tabell=tabell)
        ksat = bas.ksat_mm_h
        psi = bas.psi_m
        dtheta = bas.delta_theta(hog_markfukt=hog_markfukt)
        porositet = bas.porositet
        init_f = 1.0
    elif kod == GRONT_TAK:
        bas = GRONT_TAK_SUBSTRAT
        ksat = bas.ksat_mm_h
        psi = bas.psi_m
        dtheta = bas.delta_theta(hog_markfukt=hog_markfukt)
        porositet = bas.porositet
        init_f = 1.0
    elif kod == GRAS:
        ksat = jord.ksat_mm_h
        psi = jord.psi_m
        dtheta = jord.delta_theta(hog_markfukt=hog_markfukt)
        porositet = jord.porositet
        init_f = 1.0
    elif kod == SKOG:
        ksat = jord.ksat_mm_h * 1.5
        psi = jord.psi_m
        dtheta = jord.delta_theta(hog_markfukt=hog_markfukt)
        porositet = jord.porositet
        init_f = 1.5
    elif kod == GRUS:
        ksat = max(jord.ksat_mm_h * 1.5, 15.0)
        psi = min(jord.psi_m, 0.08)
        dtheta = jord.delta_theta(hog_markfukt=hog_markfukt)
        porositet = jord.porositet
        init_f = 1.0
    else:
        raise ForlustFel(f"Ohanterad markslagskod {kod}.")

    if tjale:
        ksat *= TJALE_KSAT_FAKTOR
        dtheta *= TJALE_PORVOLYM_FAKTOR

    return EffektivMarkParametrar(
        ksat_mm_h=ksat,
        psi_m=psi,
        delta_theta=dtheta,
        initial_faktor=init_f,
        porositet=porositet,
    )
