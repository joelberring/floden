"""Brunnar (sänkor) och ledningskapacitet per delområde (syntetiska eller lokala nät)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from mark_forluster.parametrar import ForlustFel

# Standardkapaciteter per brunnstyp (m³/s) baserade på Svenskt Vatten P110
STANDARD_BRUNNSKAPACITET_M3_S: dict[str, float] = {
    "ranni_standard": 0.015,
    "kupolbrunn": 0.025,
    "linjedranage": 0.040,
    "brunn_stor": 0.050,
}

GILTIGA_BRUNNSTYPER: frozenset[str] = frozenset(STANDARD_BRUNNSKAPACITET_M3_S)


@dataclass(frozen=True)
class Brunn:
    """En dagvattenbrunn i SWEREF 99 18 00 (EPSG:3011)."""

    brunn_id: str
    e_m: float
    n_m: float
    typ: str = "ranni_standard"
    kapacitet_m3_s: float | None = None
    delomrade_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.brunn_id, str) or not self.brunn_id.strip():
            raise ForlustFel("brunn_id får inte vara tomt.")
        if not math.isfinite(self.e_m) or not math.isfinite(self.n_m):
            raise ForlustFel(f"Ogiltiga koordinater för brunn '{self.brunn_id}'.")

        norm_typ = self.typ.strip().lower()
        if self.kapacitet_m3_s is None:
            if norm_typ not in STANDARD_BRUNNSKAPACITET_M3_S:
                tillatna = ", ".join(sorted(STANDARD_BRUNNSKAPACITET_M3_S))
                raise ForlustFel(
                    f"Okänd brunnstyp '{self.typ}' för brunn '{self.brunn_id}'. "
                    f"Giltiga: {tillatna} (eller ange explicit kapacitet_m3_s)."
                )
            kap = STANDARD_BRUNNSKAPACITET_M3_S[norm_typ]
        else:
            kap = float(self.kapacitet_m3_s)
            if not math.isfinite(kap) or kap < 0.0:
                raise ForlustFel(
                    f"kapacitet_m3_s för brunn '{self.brunn_id}' måste vara >= 0, fick {kap}."
                )

        if self.delomrade_id is not None and not self.delomrade_id.strip():
            raise ForlustFel("delomrade_id får inte vara en tom sträng (använd None i stället).")

        object.__setattr__(self, "typ", norm_typ)
        object.__setattr__(self, "kapacitet_m3_s", kap)


@dataclass(frozen=True)
class DelomradeKapacitet:
    """Maximal samlad avledningskapacitet i ledningsnätet för ett delområde."""

    delomrade_id: str
    max_flode_m3_s: float

    def __post_init__(self) -> None:
        if not isinstance(self.delomrade_id, str) or not self.delomrade_id.strip():
            raise ForlustFel("delomrade_id får inte vara tomt.")
        if not math.isfinite(self.max_flode_m3_s) or self.max_flode_m3_s < 0.0:
            raise ForlustFel(
                f"max_flode_m3_s för '{self.delomrade_id}' måste vara >= 0, "
                f"fick {self.max_flode_m3_s}."
            )


@dataclass(frozen=True)
class BrunnsResultat:
    """Beräknad effektiv avbördning per brunn och totalt bortledd volym under regnet."""

    effektiv_kapacitet_m3_s: dict[str, float]
    bortledd_volym_m3: dict[str, float]
    total_kapacitet_m3_s: float
    total_bortledd_volym_m3: float
    igensatta_andel: float
    ledningar_fulla: bool
    delomrade_utnyttjat_m3_s: dict[str, float]


def berakna_brunnsavbordning(
    brunnar: Sequence[Brunn],
    varaktighet_s: float,
    *,
    igensatta_andel: float = 0.0,
    ledningar_fulla: bool = False,
    delomraden: Sequence[DelomradeKapacitet] | None = None,
) -> BrunnsResultat:
    """Beräknar effektiv brunnskapacitet och avledd volym under en regnvaraktighet.

    Regler:
    - `igensatta_andel` i `[0, 1]` skalar ned varje brunns inloppskapacitet med
      `(1 - igensatta_andel)`.
    - `ledningar_fulla=True` innebär att ledningsnätet är helt fyllt från start
      och inte kan ta emot nytt dagvatten (effektiv kapacitet = 0 m³/s).
    - Om `delomraden` anges begränsas summan av effektiva brunnsflöden inom varje
      delområde proportionellt till `max_flode_m3_s`.
    """
    if not math.isfinite(varaktighet_s) or varaktighet_s < 0.0:
        raise ForlustFel(f"varaktighet_s måste vara >= 0, fick {varaktighet_s}.")
    if not math.isfinite(igensatta_andel) or not (0.0 <= igensatta_andel <= 1.0):
        raise ForlustFel(
            f"igensatta_andel måste ligga i [0, 1], fick {igensatta_andel}."
        )

    unika_id: set[str] = set()
    for brunn in brunnar:
        if brunn.brunn_id in unika_id:
            raise ForlustFel(f"Dubblett av brunn_id '{brunn.brunn_id}'.")
        unika_id.add(brunn.brunn_id)

    delomrade_tak: dict[str, float] = {}
    if delomraden is not None:
        for d in delomraden:
            if d.delomrade_id in delomrade_tak:
                raise ForlustFel(f"Dubblett av delomrade_id '{d.delomrade_id}'.")
            delomrade_tak[d.delomrade_id] = float(d.max_flode_m3_s)

    oppningsfaktor = 0.0 if ledningar_fulla else 1.0 - float(igensatta_andel)

    # Steg 1: preliminär kapacitet per brunn efter igensättning / fulla ledningar
    preliminar: dict[str, float] = {}
    summa_per_delomrade: dict[str, float] = {}
    for brunn in brunnar:
        bas_kap = 0.0 if brunn.kapacitet_m3_s is None else float(brunn.kapacitet_m3_s)
        kap = bas_kap * oppningsfaktor
        preliminar[brunn.brunn_id] = kap
        if brunn.delomrade_id is not None:
            summa_per_delomrade[brunn.delomrade_id] = (
                summa_per_delomrade.get(brunn.delomrade_id, 0.0) + kap
            )

    # Steg 2: skala ned proportionellt om ett delområdes ledningstak överskrids
    skalning_per_delomrade: dict[str, float] = {}
    for omr_id, summa_kap in summa_per_delomrade.items():
        skalning_per_delomrade[omr_id] = (
            (0.0 if summa_kap <= 0.0 else delomrade_tak[omr_id] / summa_kap)
            if omr_id in delomrade_tak and summa_kap > delomrade_tak[omr_id]
            else 1.0
        )

    effektiv: dict[str, float] = {}
    volym: dict[str, float] = {}
    utnyttjat: dict[str, float] = {}
    for brunn in brunnar:
        faktor = (
            skalning_per_delomrade[brunn.delomrade_id]
            if brunn.delomrade_id is not None
            else 1.0
        )
        kap = preliminar[brunn.brunn_id] * faktor
        effektiv[brunn.brunn_id] = kap
        volym[brunn.brunn_id] = kap * float(varaktighet_s)
        if brunn.delomrade_id is not None:
            utnyttjat[brunn.delomrade_id] = utnyttjat.get(brunn.delomrade_id, 0.0) + kap

    return BrunnsResultat(
        effektiv_kapacitet_m3_s=effektiv,
        bortledd_volym_m3=volym,
        total_kapacitet_m3_s=sum(effektiv.values()),
        total_bortledd_volym_m3=sum(volym.values()),
        igensatta_andel=float(igensatta_andel),
        ledningar_fulla=bool(ledningar_fulla),
        delomrade_utnyttjat_m3_s=utnyttjat,
    )
