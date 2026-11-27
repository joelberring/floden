# ruff: noqa: E501
"""Belägg ur Flodens körningar: en enda källa för rapportens tal (R1).

Rapporten räknar ingenting. Ett belägg hämtas med en **adress** ur samma fasad som MCP-servern och
webben använder (`floden_mcp.karna_api.FlodenTjanst`):

| Adress | Källa | Exempel |
| --- | --- | --- |
| `jamforelse.<namn>.<mått>` | `FlodenTjanst.jamfor` = `FragaTjanst.marksiffror` (mark; takmasken som webben) | `jamforelse.skyfall_max_djup_efter_minus_fore.yta_okning_m2` |
| `matvarde.<analys>.<scenario>[.<ruta>].<namn>` | jobbets K0-manifest via `resultat_sammanfattning` | `matvarde.skyfall.efter.lagrat_m3` |
| `omrade.<mottagare>.<lager_id>.<statistik>` | `FlodenTjanst.resultat_varde` med rapportens polygon (tak uteslutna) | `omrade.garden.skyfall.efter.max_djup.max` |

Mått i jämförelser: `max_okning`, `max_minskning`, `yta_okning_m2`, `yta_minskning_m2`.
Statistik i område: `min`, `medel`, `median`, `max`, `yta_okning_m2`, `yta_minskning_m2`.

Ett mått får bara användas om dess storhet, tidsdefinition och statistik är kända
(`MATTKATALOG`). Annars vägras belägget: ett maxdjup ska inte kunna läsas som en vattennivå.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

import numpy as np
from numpy.typing import NDArray

from floden_mcp.belagg import enhet_for
from floden_mcp.karna_api import FlodenFel, FlodenTjanst
from floden_mcp.korlogg import hash_av
from gemensamt.hash import sha256_fil
from rapport.pastaende import Belagg, PastaendeFel
from testsvit.k1_io import GeoTransformInfo


class BelaggFel(PastaendeFel):
    """Belägget kan inte hämtas ur någon körning."""


@dataclass(frozen=True)
class Matt:
    """Vad ett mått är: storhet, tidsdefinition, statistik och upplösning (för precisionen)."""

    storhet: str
    tidsdefinition: str
    statistik: str
    upplosning: float | None = None
    """None = cellens yta (ytmått) eller ur lagret."""
    enhet: str | None = None
    """None = härleds ur måttnamnet via `enhet_for`."""


_STATISK_TID = "läget vid regnets slut (statisk fyllning, inget förlopp)"
_DYNAMIK_TID = "högsta värde per cell under hela händelsen"
_REGN = "regnets parameter (indata, ingen statistik)"
REGN_PARAMETRAR: Final[frozenset[str]] = frozenset(
    {"regn_mm", "varaktighet_min", "aterkomsttid_ar", "klimatfaktor"}
)

VATTEN_MATVARDEN: Final[dict[str, Matt]] = {
    "max_djup_m": Matt(
        "största vattendjup över markytan",
        "",
        "största värde bland beräkningsområdets celler",
        0.001,
    ),
    "lagrat_m3": Matt("stående vattenvolym i sänkor", "", "summa över beräkningsområdet", 1.0),
    "ut_m3": Matt(
        "volym som rinner ut över beräkningsområdets kant",
        "summa under regnet",
        "summa över beräkningsområdets kant",
        1.0,
    ),
    "regn_mm": Matt("regnmängd", "under regnets varaktighet", _REGN, 0.1),
    "aterkomsttid_ar": Matt("regnets återkomsttid", "", _REGN, 1.0),
    "varaktighet_min": Matt("regnets varaktighet", "", _REGN, 1.0),
    "klimatfaktor": Matt("klimatfaktor på hela regnkurvan", "", _REGN, 0.01),
    "avtappning_mm_h": Matt(
        "avtappning till ledningsnätet", "under regnet", "parameter, lika i hela området", 0.1
    ),
    "takvatten_andel_ledning": Matt(
        "andel av takvattnet som går till ledningsnätet", "", "parameter", 0.01
    ),
    "volymfel_rel": Matt(
        "volymbalansens relativa fel (regn mot lagrat, utflöde och förluster)",
        "hela körningen",
        "en siffra per körning",
        1e-16,
    ),
    "takvatten_takyta_m2": Matt(
        "takyta vars regn leds till marken", "", "summa över byggnaderna", 1.0
    ),
    "yta_over_0_1m_m2": Matt(
        "markyta med minst 0,10 m vattendjup (märkbart vatten)",
        "",
        "summa över markceller med djup ≥ 0,10 m (tak undantagna)",
        None,
        enhet="m²",
    ),
    "yta_over_0_2m_m2": Matt(
        "markyta med minst 0,20 m vattendjup (framkomlighetsgräns räddningstjänst)",
        "",
        "summa över markceller med djup ≥ 0,20 m (tak undantagna)",
        None,
        enhet="m²",
    ),
    "yta_over_0_3m_m2": Matt(
        "markyta med minst 0,30 m vattendjup (risk för byggnadsskada)",
        "",
        "summa över markceller med djup ≥ 0,30 m (tak undantagna)",
        None,
        enhet="m²",
    ),
}

SKYFALL_TROSKEL_M: Final[dict[str, float]] = {
    "yta_over_0_1m_m2": 0.10,
    "yta_over_0_2m_m2": 0.20,
    "yta_over_0_3m_m2": 0.30,
}


@dataclass(frozen=True)
class SkyfallsTroskelytor:
    """Deterministiska skyfallsmått per scenario ur takmaskat ``max_djup.tif``."""

    scenario: str
    max_djup_m: float
    yta_over_0_1m_m2: float
    yta_over_0_2m_m2: float
    yta_over_0_3m_m2: float
    cellstorlek_m: float
    fil: str
    fil_sha256: str | None


@dataclass(frozen=True)
class Lagpunkt:
    """Deterministiskt detekterad lågpunkt (lokalt maximum i ``max_djup.tif``)."""

    id: str
    e_m: float
    n_m: float
    djup_m: float
    rad: int
    kol: int
    markhojd_rh2000_m: float | None = None
    vattenniva_rh2000_m: float | None = None


def berakna_skyfallstrosklar(
    djup: NDArray[np.floating[Any]],
    cellstorlek_m: float,
    *,
    tak_mask: NDArray[np.bool_] | NDArray[np.integer[Any]] | None = None,
    projekt_mask: NDArray[np.bool_] | NDArray[np.integer[Any]] | None = None,
) -> dict[str, float]:
    """Beräknar deterministiska markytor (m²) med djup ≥ 0,10, 0,20 och 0,30 m (tak undantagna)."""
    arr = np.asarray(djup, dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError(f"djup måste vara 2D, fick {arr.ndim}D.")
    if not (cellstorlek_m > 0.0 and math.isfinite(cellstorlek_m)):
        raise ValueError("cellstorlek_m måste vara ett positivt ändligt tal.")
    giltig = np.isfinite(arr)
    if tak_mask is not None:
        tmask = np.asarray(tak_mask, dtype=bool)
        if tmask.shape != arr.shape:
            raise ValueError(f"tak_mask har form {tmask.shape}, väntade {arr.shape}.")
        giltig &= ~tmask
    if projekt_mask is not None:
        pmask = np.asarray(projekt_mask, dtype=bool)
        if pmask.shape != arr.shape:
            raise ValueError(f"projekt_mask har form {pmask.shape}, väntade {arr.shape}.")
        giltig &= pmask
    cell_m2 = float(cellstorlek_m) ** 2
    ut: dict[str, float] = {}
    for nyckel, grans_m in SKYFALL_TROSKEL_M.items():
        ut[nyckel] = float(np.count_nonzero(giltig & (arr >= grans_m))) * cell_m2
    ut["max_djup_m"] = float(np.max(arr[giltig])) if bool(np.any(giltig)) else 0.0
    return ut


def detektera_lagpunkter(
    djup: NDArray[np.floating[Any]],
    geo: GeoTransformInfo,
    *,
    tak_mask: NDArray[np.bool_] | NDArray[np.integer[Any]] | None = None,
    vatten_mask: NDArray[np.bool_] | NDArray[np.integer[Any]] | None = None,
    projekt_mask: NDArray[np.bool_] | NDArray[np.integer[Any]] | None = None,
    mark_z: NDArray[np.floating[Any]] | None = None,
    max_antal: int = 3,
    min_djup_m: float = 0.15,
    min_avstand_m: float = 30.0,
) -> list[Lagpunkt]:
    """Detekterar deterministiskt upp till ``max_antal`` lokala maxima i ``djup``.

    Krav: takmaskade (och fria från permanent vatten), inom ``projekt_mask`` om angiven,
    djup ≥ ``min_djup_m`` (standard 0,15 m) och euklidiskt avstånd ≥ ``min_avstand_m``
    (standard 30 m) mellan valda punkter. Returnerar ``P1``, ``P2``, ``P3`` i fallande djup.
    """
    arr = np.asarray(djup, dtype=np.float64)
    if arr.ndim != 2 or arr.size == 0:
        return []
    hojd, bredd = arr.shape
    if geo.hojd != hojd or geo.bredd != bredd:
        raise ValueError(
            f"GeoTransformInfo ({geo.hojd}×{geo.bredd}) matchar inte djup ({hojd}×{bredd})."
        )
    giltig = np.isfinite(arr) & (arr >= float(min_djup_m))
    if tak_mask is not None:
        giltig &= ~np.asarray(tak_mask, dtype=bool)
    if vatten_mask is not None:
        giltig &= ~np.asarray(vatten_mask, dtype=bool)
    if projekt_mask is not None:
        giltig &= np.asarray(projekt_mask, dtype=bool)
    if not bool(np.any(giltig)):
        return []

    # Lokalt maximum (icke-strikt ≥ mot 8-grannar så plana sänkbottnar inte tappas bort).
    fyll = np.where(giltig, arr, -np.inf)
    pad = np.pad(fyll, 1, mode="constant", constant_values=-np.inf)
    grann_max = np.full_like(fyll, -np.inf)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            grann_max = np.maximum(grann_max, pad[1 + dr : 1 + dr + hojd, 1 + dc : 1 + dc + bredd])
    lokal_max = giltig & (arr >= grann_max)
    rader, kolumner = np.nonzero(lokal_max)
    if rader.size == 0:
        return []

    # Deterministisk sortering: djupast först, sedan rad och kolumn.
    djup_kand = arr[rader, kolumner]
    ordning = sorted(
        range(int(rader.size)),
        key=lambda i: (-float(djup_kand[i]), int(rader[i]), int(kolumner[i])),
    )
    mark_arr = (
        np.asarray(mark_z, dtype=np.float64)
        if mark_z is not None and np.asarray(mark_z).shape == arr.shape
        else None
    )
    valda: list[Lagpunkt] = []
    min_avst_kv = float(min_avstand_m) ** 2
    for idx in ordning:
        if len(valda) >= max_antal:
            break
        r = int(rader[idx])
        c = int(kolumner[idx])
        e = float(geo.x_min) + (c + 0.5) * float(geo.pixelstorlek_x)
        n = float(geo.y_max) - (r + 0.5) * float(geo.pixelstorlek_y)
        if any((e - p.e_m) ** 2 + (n - p.n_m) ** 2 < min_avst_kv for p in valda):
            continue
        d_m = float(arr[r, c])
        mz: float | None = None
        vn: float | None = None
        if mark_arr is not None and math.isfinite(float(mark_arr[r, c])):
            mz = float(mark_arr[r, c])
            vn = mz + d_m
        valda.append(
            Lagpunkt(
                id=f"P{len(valda) + 1}",
                e_m=e,
                n_m=n,
                djup_m=d_m,
                rad=r,
                kol=c,
                markhojd_rh2000_m=mz,
                vattenniva_rh2000_m=vn,
            )
        )
    return valda

TVAD_MATVARDEN: Final[dict[str, Matt]] = {
    **VATTEN_MATVARDEN,
    "max_hastighet_m_s": Matt(
        "största vattenhastighet över markytan",
        _DYNAMIK_TID,
        "största värde bland beräkningsområdets celler",
        0.01,
        enhet="m/s",
    ),
    "max_fara_m2_s": Matt(
        "största översvämningsfara (djup × hastighet)",
        _DYNAMIK_TID,
        "största värde bland beräkningsområdets celler",
        0.01,
        enhet="m²/s",
    ),
    "in_m3": Matt(
        "total tillförd vattenvolym (regn)",
        "summa under hela händelsen",
        "summa över beräkningsområdet",
        1.0,
    ),
    "avtappat_m3": Matt(
        "volym som avtappats till ledningsnätet",
        "summa under hela händelsen",
        "summa över beräkningsområdet",
        1.0,
    ),
    "infiltrerat_m3": Matt(
        "volym som infiltrerat i marken",
        "summa under hela händelsen",
        "summa över beräkningsområdet",
        1.0,
    ),
    "initial_forlust_m3": Matt(
        "volym i initial förlust (ytmagasinering)",
        "summa under hela händelsen",
        "summa över beräkningsområdet",
        1.0,
    ),
    "massbalans_fel": Matt(
        "volymbalansens relativa fel (regn mot lagrat, utflöde och förluster)",
        "hela körningen",
        "en siffra per körning",
        1e-16,
        enhet="andel",
    ),
    "antal_tidssteg": Matt(
        "antal beräkningstidssteg",
        "hela körningen",
        "en siffra per körning",
        1.0,
        enhet="st",
    ),
}

MATTKATALOG: Final[dict[tuple[str, str], Matt]] = {
    ("vatten.statisk_fyllning", "max_djup"): Matt(
        "vattendjup över markytan", _STATISK_TID, "per cell", 0.001
    ),
    ("vatten.dynamik", "max_djup"): Matt(
        "vattendjup över markytan", _DYNAMIK_TID, "per cell", 0.001
    ),
    ("vatten.2d", "max_djup"): Matt(
        "vattendjup över markytan", _DYNAMIK_TID, "per cell", 0.001
    ),
    ("vatten.2d", "hastighet"): Matt(
        "vattenhastighet över markytan", _DYNAMIK_TID, "per cell", 0.01
    ),
    ("vatten.2d", "ankomsttid"): Matt(
        "ankomsttid till 0,1 m vattendjup",
        "tid från regnstart till tröskeldjup",
        "per cell",
        1.0,
    ),
    **{
        ("vatten.statisk_fyllning", f"matvarde:{n}"): Matt(
            m.storhet, m.tidsdefinition or _STATISK_TID, m.statistik, m.upplosning, m.enhet
        )
        for n, m in VATTEN_MATVARDEN.items()
    },
    **{
        ("vatten.dynamik", f"matvarde:{n}"): Matt(
            m.storhet, m.tidsdefinition or _DYNAMIK_TID, m.statistik, m.upplosning, m.enhet
        )
        for n, m in VATTEN_MATVARDEN.items()
    },
    **{
        ("vatten.2d", f"matvarde:{n}"): Matt(
            m.storhet, m.tidsdefinition or _DYNAMIK_TID, m.statistik, m.upplosning, m.enhet
        )
        for n, m in TVAD_MATVARDEN.items()
    },
}

_SOL_TID = "dygnssumma (timmar per dygn), medel över de valda datumen"
DAGSLJUS_MATT: Final[dict[tuple[str, str], Matt]] = {
    # 0,1 h: tidssteget (10 min = 0,17 h) och metodens medelfel (0,02-0,08 h, DAGSLJUS.md avsnitt 6)
    ("dagsljus.soltimmar_mark", "soltimmar"): Matt(
        "direkt solljus på markytan", _SOL_TID, "per cell", 0.1
    ),
    ("dagsljus.svf_mark", "svf"): Matt(
        "himmelsandel (SVF) på markytan",
        "ingen tidsdimension (isotrop himmel, andel av hela himlen)",
        "per cell",
        0.01,
    ),
}
_SOLLAGE_TID = "ingen tidsdimension (en gång per ruta)"
DAGSLJUS_MATT.update(
    {
        ("dagsljus.soltimmar_mark", "matvarde:lat_grader"): Matt(
            "latitud för solläget", _SOLLAGE_TID, "rutans mittpunkt", 0.01
        ),
        ("dagsljus.soltimmar_mark", "matvarde:lon_grader"): Matt(
            "longitud för solläget", _SOLLAGE_TID, "rutans mittpunkt", 0.01
        ),
        ("dagsljus.soltimmar_mark", "matvarde:datum"): Matt(
            "antal datum som soltimmarna är medelvärde över", _SOL_TID, "parameter (indata)", 1.0
        ),
    }
)
MATTKATALOG.update(DAGSLJUS_MATT)

_EKO_TID = "ingen tidsdimension (statisk landskapsmodell: ett läge, inget förlopp)"
_EKO_STAT = "en siffra per körning, huvudscenariots spridningströskel"
_EKO_INT = "{} mellan nätverkets tröskelscenarier (källbaserat intervall)"
EKOLOGI_MATT: Final[dict[tuple[str, str], Matt]] = {
    ("ekologi.konnektivitet", "matvarde:eca_m2"): Matt(
        "ekvivalent sammanhängande habitatarea (ECA) för potentiell livsmiljö", _EKO_TID, _EKO_STAT, 1.0
    ),
    ("ekologi.konnektivitet", "matvarde:eca_min_m2"): Matt(
        "ekvivalent sammanhängande habitatarea (ECA) för potentiell livsmiljö, lägsta värde",
        _EKO_TID, _EKO_INT.format("lägsta värde"), 1.0,
    ),
    ("ekologi.konnektivitet", "matvarde:eca_max_m2"): Matt(
        "ekvivalent sammanhängande habitatarea (ECA) för potentiell livsmiljö, högsta värde",
        _EKO_TID, _EKO_INT.format("högsta värde"), 1.0,
    ),
    ("ekologi.konnektivitet", "matvarde:pc"): Matt(
        "konnektivitetsindex PC (sannolikhet att två slumpvis valda punkter i habitatet kan nå varandra)",
        _EKO_TID, _EKO_STAT, 1e-6,
    ),
    ("ekologi.konnektivitet", "matvarde:pc_min"): Matt(
        "konnektivitetsindex PC, lägsta värde", _EKO_TID, _EKO_INT.format("lägsta värde"), 1e-6
    ),
    ("ekologi.konnektivitet", "matvarde:pc_max"): Matt(
        "konnektivitetsindex PC, högsta värde", _EKO_TID, _EKO_INT.format("högsta värde"), 1e-6
    ),
    ("ekologi.konnektivitet", "matvarde:antal_flackar"): Matt(
        "antal habitatfläckar (minst minsta area)", _EKO_TID, _EKO_STAT, 1.0
    ),
    ("ekologi.konnektivitet", "matvarde:total_habitat_area_m2"): Matt(
        "total area potentiell livsmiljö (habitatfläckar)", _EKO_TID, _EKO_STAT, 1.0
    ),
    ("ekologi.konnektivitet", "matvarde:cellstorlek_m"): Matt(
        "rastrets cellstorlek", _EKO_TID, "parameter (indata)", 0.1
    ),
    ("ekologi.konnektivitet", "stromtathet"): Matt(
        "strömtäthet (normaliserad 0-1, flaskhalsar)", _EKO_TID, "per cell", 0.001
    ),
}
MATTKATALOG.update(EKOLOGI_MATT)

_MKN_INT = "{} i faktorintervallet för källa och deposition (känslighet, inte statistisk konfidens)"
_MKN_DYGN = "kalenderdygn (medel av 24 timmar), alla hela dygn i den givna timserien"
_MKN_ARS = "medel över alla timmar i den givna timserien (ett år om serien är ett helt år)"
LUFT_MATT: Final[dict[tuple[str, str], Matt]] = {
    ("luft.mkn", "matvarde:lagst_antal_dygn"): Matt(
        "antal dygn med PM10-halt över dygnsgränsen (halt = bakgrund + lokalt bidrag)",
        _MKN_DYGN, _MKN_INT.format("lägsta värde"), 1.0,
    ),
    ("luft.mkn", "matvarde:hogst_antal_dygn"): Matt(
        "antal dygn med PM10-halt över dygnsgränsen (halt = bakgrund + lokalt bidrag)",
        _MKN_DYGN, _MKN_INT.format("högsta värde"), 1.0,
    ),
    ("luft.mkn", "matvarde:arsmedel_lagst_ug_m3"): Matt(
        "PM10-årsmedelhalt (bakgrund + lokalt bidrag)", _MKN_ARS, _MKN_INT.format("lägsta värde"), 0.1
    ),
    ("luft.mkn", "matvarde:arsmedel_hogst_ug_m3"): Matt(
        "PM10-årsmedelhalt (bakgrund + lokalt bidrag)", _MKN_ARS, _MKN_INT.format("högsta värde"), 0.1
    ),
    ("luft.mkn", "matvarde:p90_4_lagst_ug_m3"): Matt(
        "90,4-percentilen av PM10-dygnsvärdena (motsvarar 35:e högsta dygnet i ett helt år)",
        _MKN_DYGN, _MKN_INT.format("lägsta värde"), 0.1,
    ),
    ("luft.mkn", "matvarde:p90_4_hogst_ug_m3"): Matt(
        "90,4-percentilen av PM10-dygnsvärdena (motsvarar 35:e högsta dygnet i ett helt år)",
        _MKN_DYGN, _MKN_INT.format("högsta värde"), 0.1,
    ),
    ("luft.mkn", "matvarde:bakgrund_ug_m3"): Matt(
        "PM10-bakgrundshalt som lagts till det lokala bidraget", "konstant över hela serien",
        "parameter (indata)", 0.1,
    ),
    ("luft.mkn", "matvarde:grans_dygn_ug_m3"): Matt(
        "dygnsgräns för PM10", _MKN_DYGN, "parameter (indata)", 1.0
    ),
    ("luft.mkn", "matvarde:max_dygn_ar_antal_dygn"): Matt(
        "högsta tillåtna antal dygn över dygnsgränsen per år", "ett kalenderår", "parameter (indata)", 1.0
    ),
    ("luft.mkn", "matvarde:antal_hela_dygn"): Matt(
        "antal hela dygn i timserien", "givna timserien", "antal", 1.0
    ),
    ("luft.mkn", "matvarde:emissionsfaktor_lagst"): Matt(
        "faktor på källstyrkan (lokalt bidrag), lägsta värde", "ingen tidsdimension",
        "parameter (indata)", 0.01,
    ),
    ("luft.mkn", "matvarde:emissionsfaktor_hogst"): Matt(
        "faktor på källstyrkan (lokalt bidrag), högsta värde", "ingen tidsdimension",
        "parameter (indata)", 0.01,
    ),
    ("luft.mkn", "matvarde:depositionsfaktor_lagst"): Matt(
        "faktor på deposition och utspädning, lägsta värde", "ingen tidsdimension",
        "parameter (indata)", 0.01,
    ),
    ("luft.mkn", "matvarde:depositionsfaktor_hogst"): Matt(
        "faktor på deposition och utspädning, högsta värde", "ingen tidsdimension",
        "parameter (indata)", 0.01,
    ),
}
MATTKATALOG.update(LUFT_MATT)

JAMFORELSEMATT: Final[dict[str, str]] = {
    "max_okning": "största ökning i en cell på mark (efter minus före)",
    "max_minskning": "största minskning i en cell på mark (före minus efter)",
    "yta_okning_m2": "yta på mark där värdet ökat mer än tröskeln",
    "yta_minskning_m2": "yta på mark där värdet minskat mer än tröskeln",
}
_YTA_OVER_RE: Final[re.Pattern[str]] = re.compile(
    r"^yta_(minskning|okning)_over_(\d+(?:_\d+)?)h_m2$"
)
"""Jämförelsemått `yta_minskning_over_<X>h_m2`: yta på mark som förlorat (eller fått) mer än X
timmar; X som heltal eller med `_` som decimaltecken (`0_5`)."""
OMRADESSTATISTIK: Final[tuple[str, ...]] = (
    "min",
    "medel",
    "median",
    "max",
    "yta_okning_m2",
    "yta_minskning_m2",
    "yta_m2",
)
HELA_OMRADET_NAMN = "hela_omradet"
"""Reserverad mottagare: lagrets hela utbredning (mark, tak undantagna)."""
_IGNORERA_PARAM: Final[frozenset[str]] = frozenset({"nyckel"})
HELA_OMRADET_MARK = "mark i hela beräkningsområdet; byggnadernas tak undantagna"
HELA_OMRADET = "hela beräkningsområdet (en siffra per körning)"
INOM_AVGRANSNINGEN = "inom projektets avgränsning (polygon)"
"""Ersätter "hela beräkningsområdet" när projektet har `avgransning_polygon_3011` (K48): talen räknas då på celler
vars mittpunkt är inne i polygonen."""
LUFT_NAMNARE = "den givna timserien (en punkt eller receptor; platsen anges inte i körningen)"
"""Nämnaren för `luft.mkn`: lösaren har ingen geometri, bara en given serie per analys."""


class Mottagare(Protocol):
    @property
    def namn(self) -> str: ...
    @property
    def polygon(self) -> Sequence[Sequence[float]]: ...
    @property
    def beskrivning(self) -> str: ...


@dataclass(frozen=True)
class _HelaOmradet:
    polygon: Sequence[Sequence[float]]
    namn: str = HELA_OMRADET_NAMN
    beskrivning: str = "hela beräkningsområdet"


class BelaggKalla(Protocol):
    """Det rapporten behöver av Floden: belägg, körningsfakta, kartor och metodstatus."""

    def hamta(self, adress: str, korning_id: str, mottagare: Mapping[str, Mottagare]) -> Belagg: ...


def hela_omradet_text(text: str, avgransad: bool) -> str:
    """`text` oförändrad, eller med "hela beräkningsområdet" ersatt av "inom projektets avgränsning (polygon)"."""
    if not avgransad:
        return text
    return text.replace("mark i hela beräkningsområdet", f"mark {INOM_AVGRANSNINGEN}").replace(
        "hela beräkningsområdet", INOM_AVGRANSNINGEN
    )


def _sv(v: float) -> str:
    return f"{v:g}".replace(".", ",")


class FlodenBelaggKalla:
    """Belägg ur `FlodenTjanst` (samma tal som webben och MCP-servern)."""

    def __init__(self, tjanst: FlodenTjanst) -> None:
        self.tjanst = tjanst

    # ------------------------------------------------------------------ hjälp

    def utmapp(self, korning_id: str) -> Path:
        try:
            k = self.tjanst.fragor._hamta(korning_id)  # noqa: SLF001 - samma körningsregister
        except Exception as exc:  # FlodenWebbFel: okänd körning
            raise BelaggFel(f"Körningen '{korning_id}' finns inte.") from exc
        if k.status != "klar":
            raise BelaggFel(f"Körningen '{korning_id}' är inte klar (status {k.status}).")
        return Path(k.utmapp)

    def har_avgransning(self, korning_id: str) -> bool:
        """Har körningens projekt en avgränsningspolygon (`avgransning_polygon_3011`)? Då räknas mark inom den."""
        try:
            return bool(self.tjanst.fragor.projekt_avgransning(korning_id) is not None)
        except Exception:  # noqa: BLE001 - utan uppgift gäller texten som förut
            return False

    def _manifest(self, korning_id: str, rel: str) -> tuple[dict[str, Any], str]:
        fil = self.utmapp(korning_id) / rel
        if not fil.is_file():
            raise BelaggFel(f"K0-manifestet {rel} saknas i körningen '{korning_id}'.")
        return json.loads(fil.read_text(encoding="utf-8")), sha256_fil(fil)

    def _resultat(self, korning_id: str) -> dict[str, Any]:
        try:
            return self.tjanst._resultat(korning_id)  # noqa: SLF001
        except FlodenFel as exc:
            raise BelaggFel(str(exc)) from exc

    def _losare(self, korning_id: str, analys: str) -> str:
        res = self._resultat(korning_id)
        for t in res["tabeller"]:
            if t["analys"] == analys:
                return str(t["losare"])
        analyser = sorted({t["analys"] for t in res["tabeller"]})
        raise BelaggFel(
            f"Analysen '{analys}' finns inte i körningen '{korning_id}'. Analyser: "
            + ", ".join(analyser)
            + "."
        )

    def jobbparametrar(self, korning_id: str, analys: str, scenario: str) -> dict[str, Any]:
        """Analysens parametrar för ett scenario ur jobbets K0-manifest (utan jobbnyckeln)."""
        rel = f"{analys}/{scenario}/run.json"
        fil = self.utmapp(korning_id) / rel
        if not fil.is_file():
            # rutvisa jobb: ta första rutans manifest (alla rutor har samma parametrar)
            kandidater = sorted((self.utmapp(korning_id) / analys / scenario).glob("*/run.json"))
            if not kandidater:
                raise BelaggFel(f"Inget K0-manifest för {analys}/{scenario} i '{korning_id}'.")
            fil = kandidater[0]
        p = dict(json.loads(fil.read_text(encoding="utf-8")).get("parametrar", {}))
        return {k: v for k, v in p.items() if k not in _IGNORERA_PARAM}

    def _jamforbarhet(self, korning_id: str, analys: str, scenarier: Sequence[str]) -> str:
        hashar = [hash_av(self.jobbparametrar(korning_id, analys, s)) for s in scenarier]
        if len(set(hashar)) == 1:
            return hashar[0]
        return "EJ_JAMFORBAR:" + "/".join(
            f"{s}={h[:12]}" for s, h in zip(scenarier, hashar, strict=True)
        )

    def regn_text(self, korning_id: str, analys: str, scenario: str) -> str | None:
        p = self.jobbparametrar(korning_id, analys, scenario)
        if "aterkomsttid_ar" not in p:
            return None
        delar = [
            str(p.get("idf_rapport") or p.get("idf") or "regn"),
            f"återkomsttid {_sv(float(p['aterkomsttid_ar']))} år",
        ]
        if p.get("varaktighet_min") is not None:
            delar.append(f"varaktighet {_sv(float(p['varaktighet_min']))} min")
        if p.get("klimatfaktor") is not None:
            delar.append(f"klimatfaktor {_sv(float(p['klimatfaktor']))}")
        if p.get("regntyp"):
            delar.append(f"{p['regntyp']}regn")
        return ", ".join(delar)

    @staticmethod
    def _matt(losare: str, nyckel: str, adress: str) -> Matt:
        m = MATTKATALOG.get((losare, nyckel))
        if m is None:
            raise BelaggFel(
                f"Måttet i '{adress}' ({losare}, {nyckel}) har ingen känd tidsdefinition och "
                "statistik i rapportens måttkatalog, så det kan inte användas i en rapport "
                "(risk att t.ex. ett maxdjup läses som vattennivå)."
            )
        return m

    # ------------------------------------------------------------------ hämta

    def hamta(self, adress: str, korning_id: str, mottagare: Mapping[str, Mottagare]) -> Belagg:
        delar = adress.strip().split(".")
        if len(delar) < 3:
            raise BelaggFel(
                f"Adressen '{adress}' är ofullständig. Former: jamforelse.<namn>.<mått>, "
                "matvarde.<analys>.<scenario>.<namn>, omrade.<mottagare>.<lager_id>.<statistik>."
            )
        typ = delar[0]
        if typ == "jamforelse":
            return self._jamforelse(adress, korning_id, ".".join(delar[1:-1]), delar[-1])
        if typ == "matvarde":
            if len(delar) not in (4, 5):
                raise BelaggFel(
                    f"Adressen '{adress}' ska vara matvarde.<analys>.<scenario>[.<ruta>].<namn>."
                )
            ruta = delar[3] if len(delar) == 5 else None
            return self._matvarde(adress, korning_id, delar[1], delar[2], ruta, delar[-1])
        if typ == "omrade":
            if len(delar) < 5:
                raise BelaggFel(
                    f"Adressen '{adress}' ska vara omrade.<mottagare>.<lager_id>.<statistik>."
                )
            return self._omrade(
                adress, korning_id, delar[1], ".".join(delar[2:-1]), delar[-1], mottagare
            )
        if typ in ("fasad", "fasadjamforelse"):
            from rapport.fasadbelagg import fasad_belagg

            return fasad_belagg(self, adress, korning_id, delar)
        if typ in ("ekojamforelse", "ekonatverk", "ekoskrivbord"):
            from rapport.ekologibelagg import ekologi_belagg

            return ekologi_belagg(self, adress, korning_id, delar)
        if typ in ("gyf", "utci"):
            from rapport.gyf_utci_belagg import gyf_utci_belagg

            return gyf_utci_belagg(self, adress, korning_id, delar)
        raise BelaggFel(
            f"Okänd beläggstyp '{typ}' i '{adress}'. Tillåtna: jamforelse, matvarde, omrade, "
            "fasad, fasadjamforelse, ekojamforelse, ekonatverk, ekoskrivbord, gyf, utci."
        )

    def _jamforelse(self, adress: str, korning_id: str, namn: str, matt: str) -> Belagg:
        yta_over = _YTA_OVER_RE.match(matt)
        if matt not in JAMFORELSEMATT and yta_over is None:
            raise BelaggFel(
                f"Okänt jämförelsemått '{matt}' i '{adress}'. Tillåtna: "
                + ", ".join(JAMFORELSEMATT)
                + ", yta_minskning_over_<X>h_m2 och yta_okning_over_<X>h_m2 (soltimmar)."
            )
        try:
            svar = self.tjanst.jamfor(korning_id)
        except FlodenFel as exc:
            raise BelaggFel(str(exc)) from exc
        post = next((j for j in svar["jamforelser"] if j["namn"] == namn), None)
        if post is None:
            namnen = ", ".join(j["namn"] for j in svar["jamforelser"])
            raise BelaggFel(
                f"Jämförelsen '{namn}' finns inte i körningen '{korning_id}'. Jämförelser: {namnen}."
            )
        if not post["yta_mark_enbart"] or not post["tak_maskat"]:
            raise BelaggFel(
                f"Jämförelsen '{namn}' saknar takmask (markraster), så talen räknar byggnadernas "
                "tak som mark. Sådana tal används inte i rapporter."
            )
        losare = self._losare(korning_id, post["analys"])
        grund = self._matt(losare, post["utdata"], adress)
        res = self._resultat(korning_id)
        avgransad = self.har_avgransning(korning_id)
        lager = next(la for la in res["lager"] if la["id"] == f"skillnad.{namn}")
        px = float(lager["pixelstorlek_m"])
        upplosning = px * px if matt.startswith("yta") else float(grund.upplosning or 0.001)
        if yta_over is not None:
            if lager["enhet"] != "h":
                raise BelaggFel(
                    f"Måttet '{matt}' gäller timmar (soltimmar); lagret '{lager['id']}' har "
                    f"enheten '{lager['enhet']}'."
                )
            x = float(yta_over.group(2).replace("_", "."))
            riktn = yta_over.group(1)
            try:
                yta = self.tjanst.fragor.markyta_over(korning_id, lager, x, riktn)
            except Exception as exc:  # FlodenWebbFel
                raise BelaggFel(str(exc)) from exc
            if yta is None:
                raise BelaggFel(f"Lagret '{lager['id']}' har inga markceller med värde.")
            varde = yta
            enhet = "m²"
            ord_ = "minskat" if riktn == "minskning" else "ökat"
            statistik = f"yta på mark där värdet {ord_} mer än {_sv(x)} {lager['enhet']}"
        else:
            varde = float(post[matt]["varde"])
            enhet = post[matt]["enhet"]
            tros = _sv(float(post["troskel"]))
            statistik = JAMFORELSEMATT[matt].replace(
                "tröskeln", f"tröskeln {tros} {lager['enhet']}"
            )
        manifest, msha = self._manifest(korning_id, "run.json")
        rasterfil = str(post["rasterfil"])
        fil = self.utmapp(korning_id) / rasterfil
        return Belagg(
            adress=adress,
            korning_id=korning_id,
            fil=rasterfil,
            fil_sha256=sha256_fil(fil) if fil.is_file() else None,
            manifest="run.json",
            manifest_sha256=msha,
            analys=str(post["analys"]),
            losare=losare,
            scenarier=(str(post["fore"]), str(post["efter"])),
            mottagare=hela_omradet_text("hela beräkningsområdet", avgransad),
            namnare=hela_omradet_text(HELA_OMRADET_MARK, avgransad),
            matt=f"{post['utdata']}:{matt}",
            storhet=f"skillnad i {grund.storhet}",
            enhet=enhet,
            tidsdefinition=grund.tidsdefinition,
            statistik=statistik,
            varde=varde,
            upplosning=upplosning,
            jamforbarhet=self._jamforbarhet(
                korning_id, str(post["analys"]), (str(post["fore"]), str(post["efter"]))
            ),
            regn=self.regn_text(korning_id, str(post["analys"]), str(post["efter"])),
        )

    def _matvarde(
        self,
        adress: str,
        korning_id: str,
        analys: str,
        scenario: str,
        ruta: str | None,
        namn: str,
    ) -> Belagg:
        losare = self._losare(korning_id, analys)
        grund = self._matt(losare, f"matvarde:{namn}", adress)
        try:
            svar = self.tjanst.resultat_sammanfattning(
                korning_id, analys=analys, scenario=scenario, ruta=ruta, max_rader=500
            )
        except FlodenFel as exc:
            raise BelaggFel(str(exc)) from exc
        rader = [r for t in svar["matvarden"] for r in t["rader"]]
        if not rader:
            raise BelaggFel(
                f"Scenariot '{scenario}' finns inte för analysen '{analys}' i '{korning_id}'."
            )
        if len(rader) > 1 and ruta is None:
            raise BelaggFel(
                f"Analysen '{analys}' har flera rutor i scenariot '{scenario}'; ange rutan: "
                f"matvarde.{analys}.{scenario}.<ruta>.{namn}."
            )
        rad = rader[0]
        rel = str(rad["belagg"]["fil"])
        manifest_dok, msha = self._manifest(korning_id, rel)
        raw_varde: Any = rad["varden"].get(namn)
        fil_rel = rel
        fil_sha: str | None = msha
        upplosning_override: float | None = None
        namnare_override: str | None = None
        if (
            (raw_varde is None or isinstance(raw_varde, bool) or not isinstance(raw_varde, int | float))
            and namn in SKYFALL_TROSKEL_M
        ):
            trosklar = self.skyfall_trosklar(korning_id, analys, scenario)
            if trosklar is None:
                raise BelaggFel(
                    f"Mätvärdet '{namn}' kräver max_djup-raster för {analys}/{scenario} i '{korning_id}'."
                )
            raw_varde = getattr(trosklar, namn)
            fil_rel = trosklar.fil
            fil_sha = trosklar.fil_sha256
            upplosning_override = trosklar.cellstorlek_m ** 2
            namnare_override = hela_omradet_text(HELA_OMRADET_MARK, self.har_avgransning(korning_id))
        if (
            (raw_varde is None or isinstance(raw_varde, bool) or not isinstance(raw_varde, int | float))
            and namn in REGN_PARAMETRAR
        ):
            par = manifest_dok.get("parametrar", {})
            if isinstance(par, Mapping):
                raw_varde = par.get(namn)
        if (
            raw_varde is None
            or isinstance(raw_varde, bool)
            or not isinstance(raw_varde, int | float)
        ):
            raise BelaggFel(f"Mätvärdet '{namn}' finns inte som tal i {analys}/{scenario}.")
        return Belagg(
            adress=adress,
            korning_id=korning_id,
            fil=fil_rel,
            fil_sha256=fil_sha,
            manifest=rel,
            manifest_sha256=msha,
            analys=analys,
            losare=losare,
            scenarier=(scenario,),
            mottagare=f"scenario {scenario}" + (f", ruta {ruta}" if ruta else ""),
            namnare=(
                namnare_override
                if namnare_override is not None
                else (LUFT_NAMNARE if losare == "luft.mkn" else HELA_OMRADET)
            ),
            matt=f"matvarden:{namn}",
            storhet=grund.storhet,
            enhet=grund.enhet if grund.enhet is not None else enhet_for(namn),
            tidsdefinition=grund.tidsdefinition,
            statistik=grund.statistik,
            varde=float(raw_varde),
            upplosning=(
                upplosning_override
                if upplosning_override is not None
                else float(grund.upplosning or 0.0)
            ),
            jamforbarhet=self._jamforbarhet(korning_id, analys, (scenario,)),
            regn=self.regn_text(korning_id, analys, scenario),
        )

    def _max_djup_data(
        self, korning_id: str, analys: str, scenario: str
    ) -> tuple[
        NDArray[np.float64],
        NDArray[np.bool_] | None,
        NDArray[np.bool_] | None,
        GeoTransformInfo,
        dict[str, Any],
        str,
        str | None,
    ] | None:
        """Hämtar takmaskat max_djup-raster + projektmask + GeoTransformInfo + källfil/sha256."""
        try:
            res = self._resultat(korning_id)
        except BelaggFel:
            return None
        lager_id = f"{analys}.{scenario}.max_djup"
        la = next((x for x in res.get("lager", []) if x.get("id") == lager_id), None)
        if la is None:
            return None
        fragor = getattr(self.tjanst, "fragor", None)
        if fragor is None or not hasattr(fragor, "lager_raster"):
            return None
        try:
            data = fragor.lager_raster(korning_id, lager_id)
        except Exception:  # noqa: BLE001
            return None
        raster = np.asarray(data["raster"], dtype=np.float64)
        tak_raw = data.get("tak")
        tak = None if tak_raw is None else np.asarray(tak_raw, dtype=bool)
        h, b = raster.shape
        px = float(la["pixelstorlek_m"])
        u = la["utbredning"]
        geo = GeoTransformInfo(
            bredd=int(b),
            hojd=int(h),
            x_min=float(u["x_min"]),
            y_max=float(u["y_max"]),
            pixelstorlek_x=px,
            pixelstorlek_y=px,
            epsg=3011,
            dtype="float32",
        )
        projekt_mask: NDArray[np.bool_] | None = None
        if hasattr(fragor, "_hamta") and hasattr(fragor, "_inne_i_projektet"):
            try:
                k_obj = fragor._hamta(korning_id)  # noqa: SLF001
                inne = fragor._inne_i_projektet(k_obj, la, h, b)  # noqa: SLF001
                if inne is not None:
                    projekt_mask = np.asarray(inne, dtype=bool)
            except Exception:  # noqa: BLE001
                projekt_mask = None
        utmapp = self.utmapp(korning_id)
        enkel_tif = utmapp / analys / scenario / "max_djup.tif"
        if enkel_tif.is_file():
            fil_rel = f"{analys}/{scenario}/max_djup.tif"
            fil_sha: str | None = sha256_fil(enkel_tif)
        else:
            tifs = sorted((utmapp / analys / scenario).glob("*/max_djup.tif"))
            if tifs:
                fil_rel = f"{analys}/{scenario}/*/max_djup.tif"
                fil_sha = hash_av([(p.relative_to(utmapp).as_posix(), sha256_fil(p)) for p in tifs])
            else:
                fil_rel = lager_id
                fil_sha = None
        return raster, tak, projekt_mask, geo, dict(la), fil_rel, fil_sha

    def har_max_djup_raster(self, korning_id: str, analys: str, scenario: str) -> bool:
        """Sant om körningen har ett läsbart max_djup-raster för analys/scenario."""
        return self._max_djup_data(korning_id, analys, scenario) is not None

    def skyfall_trosklar(
        self, korning_id: str, analys: str, scenario: str
    ) -> SkyfallsTroskelytor | None:
        """Deterministiska tröskelytor (≥ 10, 20, 30 cm) ur takmaskat max_djup.tif."""
        pack = self._max_djup_data(korning_id, analys, scenario)
        if pack is None:
            return None
        raster, tak, projekt_mask, geo, _la, fil_rel, fil_sha = pack
        m = berakna_skyfallstrosklar(
            raster,
            geo.pixelstorlek_x,
            tak_mask=tak,
            projekt_mask=projekt_mask,
        )
        return SkyfallsTroskelytor(
            scenario=scenario,
            max_djup_m=m["max_djup_m"],
            yta_over_0_1m_m2=m["yta_over_0_1m_m2"],
            yta_over_0_2m_m2=m["yta_over_0_2m_m2"],
            yta_over_0_3m_m2=m["yta_over_0_3m_m2"],
            cellstorlek_m=geo.pixelstorlek_x,
            fil=fil_rel,
            fil_sha256=fil_sha,
        )

    def skyfall_lagpunkter(
        self,
        korning_id: str,
        analys: str,
        scenario: str,
        *,
        mark_z: NDArray[np.floating[Any]] | None = None,
        max_antal: int = 3,
        min_djup_m: float = 0.15,
        min_avstand_m: float = 30.0,
    ) -> list[Lagpunkt]:
        """Deterministiskt detekterade lågpunkter (P1–P3) ur takmaskat max_djup.tif."""
        pack = self._max_djup_data(korning_id, analys, scenario)
        if pack is None:
            return []
        raster, tak, projekt_mask, geo, la, _fil_rel, _fil_sha = pack
        vatten_mask: NDArray[np.bool_] | None = None
        vm_fn = getattr(self.tjanst, "_vatten_mask_for_lager", None)
        if callable(vm_fn):
            try:
                vm = vm_fn(korning_id, la, (raster.shape[0], raster.shape[1]))
                if vm is not None:
                    vatten_mask = np.asarray(vm, dtype=bool)
            except Exception:  # noqa: BLE001
                vatten_mask = None
        return detektera_lagpunkter(
            raster,
            geo,
            tak_mask=tak,
            vatten_mask=vatten_mask,
            projekt_mask=projekt_mask,
            mark_z=mark_z,
            max_antal=max_antal,
            min_djup_m=min_djup_m,
            min_avstand_m=min_avstand_m,
        )

    def _omrade(
        self,
        adress: str,
        korning_id: str,
        mottagarnamn: str,
        lager_id: str,
        stat: str,
        mottagare: Mapping[str, Mottagare],
    ) -> Belagg:
        if stat not in OMRADESSTATISTIK:
            raise BelaggFel(
                f"Okänd statistik '{stat}' i '{adress}'. Tillåtna: {', '.join(OMRADESSTATISTIK)}."
            )
        m = mottagare.get(mottagarnamn)
        hela = m is None and mottagarnamn == HELA_OMRADET_NAMN
        if hela:
            res0 = self._resultat(korning_id)
            la0 = next((x for x in res0["lager"] if x["id"] == lager_id), None)
            if la0 is None:
                raise BelaggFel(f"Lagret '{lager_id}' finns inte i '{korning_id}'.")
            u = la0["utbredning"]
            x0, x1, y0, y1 = (float(u[k]) for k in ("x_min", "x_max", "y_min", "y_max"))
            m = _HelaOmradet(((x0, y0), (x1, y0), (x1, y1), (x0, y1)))
        if m is None:
            raise BelaggFel(
                f"Mottagaren '{mottagarnamn}' finns inte i rapporten. Lägg till den (namn och "
                "polygon) när rapporten skapas."
            )
        res = self._resultat(korning_id)
        avgransad = hela and self.har_avgransning(korning_id)
        la = next((x for x in res["lager"] if x["id"] == lager_id), None)
        if la is None:
            raise BelaggFel(
                f"Lagret '{lager_id}' finns inte i '{korning_id}'. Lager: "
                + ", ".join(x["id"] for x in res["lager"])
                + "."
            )
        try:
            svar = self.tjanst.resultat_varde(
                korning_id,
                analys=la["analys"],
                roll=la["roll"],
                omrade=[list(p) for p in m.polygon],
            )
        except FlodenFel as exc:
            raise BelaggFel(str(exc)) from exc
        st = next(s for s in svar["statistik"] if s["lager_id"] == lager_id)
        v: Any
        if stat == "yta_m2":
            n = int(st["antal_celler_med_varde"])
            v = (
                {"varde": n * float(st["cellstorlek_m"]) ** 2, "enhet": "m²", "belagg": {"fil": ""}}
                if n > 0
                else None
            )
        else:
            v = st.get(stat)
        if v is None:
            raise BelaggFel(f"Mottagaren '{mottagarnamn}' har inga markceller med värde i lagret.")
        losare = self._losare(korning_id, la["analys"])
        grund = self._matt(losare, la["roll"], adress)
        px = float(st["cellstorlek_m"])
        scen = (la["fore"], la["efter"]) if la["typ"] == "skillnad" else (la["scenario"],)
        fil = str(v["belagg"]["fil"])
        _m, msha = self._manifest(korning_id, "run.json")
        stat_text = {
            "min": "minsta värde",
            "medel": "medelvärde",
            "median": "median",
            "max": "största värde",
            "yta_okning_m2": "yta där värdet ökat mer än tröskeln",
            "yta_minskning_m2": "yta där värdet minskat mer än tröskeln",
            "yta_m2": "markyta med värde",
        }[stat]
        return Belagg(
            adress=adress,
            korning_id=korning_id,
            fil=fil or str(la["id"]),
            fil_sha256=None,
            manifest="run.json",
            manifest_sha256=msha,
            analys=str(la["analys"]),
            losare=losare,
            scenarier=tuple(str(s) for s in scen),
            mottagare=hela_omradet_text("hela beräkningsområdet", avgransad)
            if hela
            else f"mottagare {mottagarnamn}",
            namnare=hela_omradet_text(HELA_OMRADET_MARK, avgransad)
            if hela
            else f"markceller i mottagaren {mottagarnamn} ({m.beskrivning}); tak undantagna",
            matt=f"{la['roll']}:{stat}",
            storhet=("skillnad i " if la["typ"] == "skillnad" else "") + grund.storhet,
            enhet=v["enhet"],
            tidsdefinition=grund.tidsdefinition,
            statistik=f"{stat_text} över {st['antal_celler_med_varde']} celler",
            varde=float(v["varde"]),
            upplosning=px * px if stat.startswith("yta") else float(grund.upplosning or 0.001),
            jamforbarhet=self._jamforbarhet(korning_id, str(la["analys"]), scen),
            regn=self.regn_text(korning_id, str(la["analys"]), scen[-1]),
        )


__all__ = [
    "DAGSLJUS_MATT",
    "EKOLOGI_MATT",
    "LUFT_MATT",
    "HELA_OMRADET",
    "INOM_AVGRANSNINGEN",
    "hela_omradet_text",
    "HELA_OMRADET_NAMN",
    "HELA_OMRADET_MARK",
    "JAMFORELSEMATT",
    "MATTKATALOG",
    "REGN_PARAMETRAR",
    "TVAD_MATVARDEN",
    "VATTEN_MATVARDEN",
    "OMRADESSTATISTIK",
    "SKYFALL_TROSKEL_M",
    "BelaggFel",
    "BelaggKalla",
    "FlodenBelaggKalla",
    "Lagpunkt",
    "Matt",
    "Mottagare",
    "SkyfallsTroskelytor",
    "berakna_skyfallstrosklar",
    "detektera_lagpunkter",
]
