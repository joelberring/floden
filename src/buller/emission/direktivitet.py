"""Källmodellens direktivitet (Nord2000 Road): tillägg till L_W per delkälla och tersband.

Underlag: SP Rapport 2006:12 avsnitt 2.5 (s. 13–16) och sammanfattningen (s. 6–7).

- Ekvation (2.9): ΔL(f, φ, ψ) = ΔL_H(f, φ) + ΔL_V(f, ψ), relativt den passageintegrerade
  ekvivalenta ljudeffektnivån. Vinklarna definieras i figur 2.2: ψ är vinkeln mellan
  horisontalplanet och riktningen från källan mot mottagaren, φ vinkeln i horisontalplanet
  mellan fordonets färdriktning och den riktningen.
- Ekvation (2.12a–b): horisontell direktivitet för den lägsta delkällan (0,01 m),
  (k0 + k1·|cos φ|)·√cos ψ i banden 800–6300 Hz och noll i övriga band.
- Ekvation (2.10c): ingen horisontell direktivitet för delkällan på 0,30 m.
- Ekvation (2.11): horisontell direktivitet för delkällan på 0,75 m, ett tredjegradspolynom i
  (π/2 − φ) gånger √cos ψ, lika i alla band.
- Tabell 2.4: vertikal direktivitet ΔL_V(ψ) per delkällhöjd och grupp av tre tersband.

Faktorn är √cos ψ (kvadratroten), avläst ur sidbilden; `pdftotext` tappar rottecknet.
Alla koefficienter läses ur datafilen (`[direktivitet]`), inget tal står i koden.

Hur riktningen bestäms hör till den som anropar. AV 1849/00 (avsnitt om punktkällor) anger
att L_W för en riktad källa är ljudeffektnivån plus direktivitetskorrektionen i
utbredningsvägens riktning, och AV 1106/07 bilaga D att den vertikala utstrålningsvinkeln
tas som riktningen från källan till mottagaren (utan korrektion för refraktion).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from buller.typer import ANTAL_BAND, TERSBAND_HZ, BullerFel, Flyttal

_HOJDTOLERANS_M = 1.0e-9
_VINKELTOLERANS_RAD = 1.0e-12


@dataclass(frozen=True)
class Kallriktning:
    """Riktningen från en källpunkt mot mottagaren (SP 2006:12 figur 2.2).

    `hojdvinkel_rad` är ett tal (samma ψ för alla delkällor) eller ett värde per delkälla, i
    samma ordning som raderna i det resultat som begärs.
    """

    vinkel_mot_fardriktning_rad: float  # φ, 0 = rakt framför fordonet, π = rakt bakom
    hojdvinkel_rad: float | tuple[float, ...] = 0.0  # ψ, positiv när mottagaren ligger högre

    def __post_init__(self) -> None:
        fi = self.vinkel_mot_fardriktning_rad
        if not math.isfinite(fi) or not -_VINKELTOLERANS_RAD <= fi <= math.pi + _VINKELTOLERANS_RAD:
            raise BullerFel("vinkel_mot_fardriktning_rad (φ) ska ligga mellan 0 och π.")
        psi_alla = (
            self.hojdvinkel_rad
            if isinstance(self.hojdvinkel_rad, tuple)
            else (self.hojdvinkel_rad,)
        )
        for psi in psi_alla:
            if not math.isfinite(psi) or abs(psi) > math.pi / 2.0 + _VINKELTOLERANS_RAD:
                raise BullerFel("hojdvinkel_rad (ψ) ska ligga mellan −π/2 och π/2.")

    def hojdvinklar_rad(self, antal_delkallor: int) -> tuple[float, ...]:
        """ψ per delkälla. Ett ensamt tal gäller alla delkällor."""
        if not isinstance(self.hojdvinkel_rad, tuple):
            return (float(self.hojdvinkel_rad),) * antal_delkallor
        if len(self.hojdvinkel_rad) != antal_delkallor:
            raise BullerFel(
                f"hojdvinkel_rad har {len(self.hojdvinkel_rad)} värden men resultatet har "
                f"{antal_delkallor} delkällor."
            )
        return self.hojdvinkel_rad


def _h_abs_cos(koefficienter_db: Sequence[float], fi_rad: float) -> float:
    """k0 + k1·|cos φ|: formen i SP 2006:12 ekv. (2.10b) och (2.12b), före faktorn √cos ψ."""
    k0, k1 = koefficienter_db
    return k0 + k1 * abs(math.cos(fi_rad))


def _h_polynom(koefficienter_db: Sequence[float], fi_rad: float) -> float:
    """Σ c_n·(π/2 − φ)ⁿ med φ i radianer: formen i SP 2006:12 ekv. (2.11), före √cos ψ."""
    x = math.pi / 2.0 - fi_rad
    return float(sum(c * x**n for n, c in enumerate(koefficienter_db)))


# Horisontella funktionsformer; värdet multipliceras med √cos ψ. Antal koefficienter per form.
HORISONTELLA_FORMTYPER: Mapping[str, tuple[Callable[[Sequence[float], float], float], int]] = {
    "k0_plus_k1_abs_cos_fi": (_h_abs_cos, 2),
    "polynom_i_pi_halva_minus_fi": (_h_polynom, 0),  # 0 = valfritt antal, stigande potens
}

# Vertikala funktionsformer i SP 2006:12 tabell 2.4: k gånger en funktion av ψ.
VERTIKALA_FORMTYPER: Mapping[str, Callable[[float], float]] = {
    "noll": lambda psi: 0.0,
    "k_sin_psi": math.sin,
    "k_ett_minus_cos2_psi": lambda psi: 1.0 - math.cos(psi) ** 2,
    "k_ett_minus_cos_psi": lambda psi: 1.0 - math.cos(psi),
}


@dataclass(frozen=True)
class Horisontellpost:
    """Horisontell direktivitet för en delkälla."""

    hojd_m: float
    formtyp: str
    koefficienter_db: tuple[float, ...]
    lagsta_frekvens_hz: float | None  # None = alla band
    hogsta_frekvens_hz: float | None
    bullertyp: str  # den bullertyp som texten knyter direktiviteten till
    kalla: str

    def bandmask(self) -> Flyttal:
        """1 i de band där direktiviteten gäller och 0 i övriga, form (ANTAL_BAND,)."""
        lagst = -math.inf if self.lagsta_frekvens_hz is None else self.lagsta_frekvens_hz
        hogst = math.inf if self.hogsta_frekvens_hz is None else self.hogsta_frekvens_hz
        return np.asarray([1.0 if lagst <= f <= hogst else 0.0 for f in TERSBAND_HZ])

    def varde_db(self, fi_rad: float, psi_rad: float) -> Flyttal:
        """ΔL_H per tersband för riktningen (φ, ψ), form (ANTAL_BAND,)."""
        funktion, _ = HORISONTELLA_FORMTYPER[self.formtyp]
        varde = funktion(self.koefficienter_db, fi_rad) * math.sqrt(max(math.cos(psi_rad), 0.0))
        return self.bandmask() * varde


@dataclass(frozen=True)
class Vertikalpost:
    """Vertikal direktivitet för en delkälla: en funktionsform och en koefficient per band."""

    hojd_m: float
    formtyp_per_band: tuple[str, ...]  # längd ANTAL_BAND
    k_db_per_band: tuple[float, ...]  # längd ANTAL_BAND

    def varde_db(self, psi_rad: float) -> Flyttal:
        """ΔL_V per tersband för höjdvinkeln ψ, form (ANTAL_BAND,)."""
        return np.asarray(
            [
                k * VERTIKALA_FORMTYPER[form](psi_rad) if form != "noll" else 0.0
                for form, k in zip(self.formtyp_per_band, self.k_db_per_band, strict=True)
            ],
            dtype=np.float64,
        )


@dataclass(frozen=True)
class Direktivitetsdata:
    """Inläst direktivitet ur datafilens `[direktivitet]`."""

    kalla: str
    horisontell: tuple[Horisontellpost, ...]
    vertikal: tuple[Vertikalpost, ...]
    vertikal_kalla: str
    vertikal_bandgrupper_hz: tuple[tuple[float, ...], ...]
    # Tabellen som den står i källan, per delkälla: (höjd, formtyp per grupp, k per grupp).
    vertikal_tabell: tuple[tuple[float, tuple[str, ...], tuple[float, ...]], ...]

    def horisontell_for_hojd(self, hojd_m: float) -> Horisontellpost | None:
        for post in self.horisontell:
            if abs(post.hojd_m - hojd_m) <= _HOJDTOLERANS_M:
                return post
        return None

    def vertikal_for_hojd(self, hojd_m: float) -> Vertikalpost:
        for post in self.vertikal:
            if abs(post.hojd_m - hojd_m) <= _HOJDTOLERANS_M:
                return post
        raise BullerFel(f"Vertikal direktivitet saknas för delkällhöjden {hojd_m:g} m.")

    def horisontell_db(self, hojd_m: float, fi_rad: float, psi_rad: float) -> Flyttal:
        """ΔL_H(f, φ) för delkällan på höjden `hojd_m` (noll om källan saknar post)."""
        post = self.horisontell_for_hojd(hojd_m)
        return np.zeros(ANTAL_BAND) if post is None else post.varde_db(fi_rad, psi_rad)

    def vertikal_db(self, hojd_m: float, psi_rad: float) -> Flyttal:
        """ΔL_V(f, ψ) för delkällan på höjden `hojd_m`, SP 2006:12 tabell 2.4."""
        return self.vertikal_for_hojd(hojd_m).varde_db(psi_rad)


def _tal(varde: Any, namn: str, fil: Path) -> float:
    if isinstance(varde, bool) or not isinstance(varde, int | float) or not math.isfinite(varde):
        raise BullerFel(f"{fil}: {namn} ska vara ett ändligt tal.")
    return float(varde)


def _tallista(varde: Any, namn: str, fil: Path) -> tuple[float, ...]:
    if not isinstance(varde, list) or not varde:
        raise BullerFel(f"{fil}: {namn} ska vara en lista med tal.")
    return tuple(_tal(v, namn, fil) for v in varde)


def _las_horisontell(
    poster: Any, hojder_m: Sequence[float], bullertyper: Sequence[str], fil: Path
) -> tuple[Horisontellpost, ...]:
    if not isinstance(poster, list):
        raise BullerFel(f"{fil}: [[direktivitet.horisontell]] ska vara en lista.")
    resultat: list[Horisontellpost] = []
    for nr, post in enumerate(poster):
        namn = f"direktivitet.horisontell[{nr}]"
        if not isinstance(post, dict):
            raise BullerFel(f"{fil}: {namn} ska vara en tabell.")
        hojd = _tal(post.get("hojd_m"), f"{namn}.hojd_m", fil)
        if not any(abs(hojd - h) <= _HOJDTOLERANS_M for h in hojder_m):
            raise BullerFel(f"{fil}: {namn}.hojd_m = {hojd:g} är ingen delkällhöjd.")
        if any(abs(hojd - p.hojd_m) <= _HOJDTOLERANS_M for p in resultat):
            raise BullerFel(f"{fil}: {namn}: delkällhöjden {hojd:g} m står två gånger.")
        formtyp = post.get("formtyp")
        if not isinstance(formtyp, str) or formtyp not in HORISONTELLA_FORMTYPER:
            raise BullerFel(
                f"{fil}: {namn}.formtyp '{formtyp}' är okänd. "
                f"Kända: {', '.join(HORISONTELLA_FORMTYPER)}."
            )
        koefficienter = _tallista(post.get("koefficienter_db"), f"{namn}.koefficienter_db", fil)
        antal = HORISONTELLA_FORMTYPER[formtyp][1]
        if antal and len(koefficienter) != antal:
            raise BullerFel(f"{fil}: {namn}.koefficienter_db ska ha {antal} värden.")
        granser: list[float | None] = []
        for falt in ("lagsta_frekvens_hz", "hogsta_frekvens_hz"):
            if falt in post:
                grans = _tal(post[falt], f"{namn}.{falt}", fil)
                if grans not in TERSBAND_HZ:
                    raise BullerFel(f"{fil}: {namn}.{falt} = {grans:g} är inget tersband.")
                granser.append(grans)
            else:
                granser.append(None)
        bullertyp = post.get("bullertyp")
        if not isinstance(bullertyp, str) or bullertyp not in bullertyper:
            raise BullerFel(f"{fil}: {namn}.bullertyp ska vara en av {', '.join(bullertyper)}.")
        resultat.append(
            Horisontellpost(
                hojd_m=hojd,
                formtyp=formtyp,
                koefficienter_db=koefficienter,
                lagsta_frekvens_hz=granser[0],
                hogsta_frekvens_hz=granser[1],
                bullertyp=bullertyp,
                kalla=str(post.get("kalla", "")),
            )
        )
    return tuple(resultat)


def _las_vertikal(
    avsnitt: Any, hojder_m: Sequence[float], fil: Path
) -> tuple[
    tuple[Vertikalpost, ...],
    tuple[tuple[float, ...], ...],
    tuple[tuple[float, tuple[str, ...], tuple[float, ...]], ...],
]:
    if not isinstance(avsnitt, dict):
        raise BullerFel(f"{fil}: [direktivitet.vertikal] saknas.")
    grupper_ra = avsnitt.get("bandgrupper_hz")
    if not isinstance(grupper_ra, list) or not grupper_ra:
        raise BullerFel(f"{fil}: direktivitet.vertikal.bandgrupper_hz ska vara en lista.")
    grupper = tuple(_tallista(g, "direktivitet.vertikal.bandgrupper_hz", fil) for g in grupper_ra)
    band_till_grupp: dict[int, int] = {}
    for nr, grupp in enumerate(grupper):
        for frekvens in grupp:
            if frekvens not in TERSBAND_HZ:
                raise BullerFel(f"{fil}: bandgrupper_hz: {frekvens:g} Hz är inget tersband.")
            band = TERSBAND_HZ.index(frekvens)
            if band in band_till_grupp:
                raise BullerFel(f"{fil}: bandgrupper_hz: {frekvens:g} Hz står två gånger.")
            band_till_grupp[band] = nr
    delkallor = avsnitt.get("delkallor")
    if not isinstance(delkallor, list):
        raise BullerFel(f"{fil}: [[direktivitet.vertikal.delkallor]] ska vara en lista.")
    poster: list[Vertikalpost] = []
    tabell: list[tuple[float, tuple[str, ...], tuple[float, ...]]] = []
    for nr, post in enumerate(delkallor):
        namn = f"direktivitet.vertikal.delkallor[{nr}]"
        if not isinstance(post, dict):
            raise BullerFel(f"{fil}: {namn} ska vara en tabell.")
        hojd = _tal(post.get("hojd_m"), f"{namn}.hojd_m", fil)
        former = post.get("formtyp")
        if not isinstance(former, list) or len(former) != len(grupper):
            raise BullerFel(f"{fil}: {namn}.formtyp ska ha ett värde per bandgrupp.")
        okanda = [f for f in former if f not in VERTIKALA_FORMTYPER]
        if okanda:
            raise BullerFel(
                f"{fil}: {namn}.formtyp '{okanda[0]}' är okänd. "
                f"Kända: {', '.join(VERTIKALA_FORMTYPER)}."
            )
        k_db = _tallista(post.get("k_db"), f"{namn}.k_db", fil)
        if len(k_db) != len(grupper):
            raise BullerFel(f"{fil}: {namn}.k_db ska ha ett värde per bandgrupp.")
        if any((f == "noll") != (k == 0.0) for f, k in zip(former, k_db, strict=True)):
            raise BullerFel(f"{fil}: {namn}: formtyp \"noll\" och k_db = 0 ska stå ihop.")
        tabell.append((hojd, tuple(former), k_db))
        poster.append(
            Vertikalpost(
                hojd_m=hojd,
                formtyp_per_band=tuple(
                    former[band_till_grupp[b]] if b in band_till_grupp else "noll"
                    for b in range(ANTAL_BAND)
                ),
                k_db_per_band=tuple(
                    k_db[band_till_grupp[b]] if b in band_till_grupp else 0.0
                    for b in range(ANTAL_BAND)
                ),
            )
        )
    lasta = sorted(p.hojd_m for p in poster)
    if len(lasta) != len(hojder_m) or any(
        abs(a - b) > _HOJDTOLERANS_M for a, b in zip(lasta, sorted(hojder_m), strict=True)
    ):
        raise BullerFel(
            f"{fil}: [[direktivitet.vertikal.delkallor]] ska ha precis en post per delkällhöjd."
        )
    return tuple(poster), grupper, tuple(tabell)


def las_direktivitet(
    avsnitt: Any, hojder_m: Sequence[float], bullertyper: Sequence[str], fil: Path
) -> Direktivitetsdata | None:
    """Tolkar datafilens `[direktivitet]`. Saknas avsnittet blir svaret `None`.

    Ett avsnitt som finns men är felaktigt ifyllt (okänd formtyp, band som inte är tersband,
    delkällhöjd som saknas) ger `BullerFel` direkt.
    """
    if avsnitt is None:
        return None
    if not isinstance(avsnitt, dict):
        raise BullerFel(f"{fil}: [direktivitet] ska vara en tabell.")
    vertikal_ra = avsnitt.get("vertikal")
    vertikal, grupper, tabell = _las_vertikal(vertikal_ra, hojder_m, fil)
    return Direktivitetsdata(
        kalla=str(avsnitt.get("kalla", "")),
        horisontell=_las_horisontell(avsnitt.get("horisontell", []), hojder_m, bullertyper, fil),
        vertikal=vertikal,
        vertikal_kalla=str(vertikal_ra.get("kalla", "")) if isinstance(vertikal_ra, dict) else "",
        vertikal_bandgrupper_hz=grupper,
        vertikal_tabell=tabell,
    )
