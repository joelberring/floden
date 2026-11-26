# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministisk inläsare och analys av SMHI Öppna data (`metobs`) för vindros och UTCI.

Läser lokala SMHI Open Data `metobs`-filer eller textsträngar i officiellt CSV- eller JSON-format
(aldrig live-nätverksanrop, aldrig påhittade mätdata) för standardparametrarna:
- `1`: Lufttemperatur, momentanvärde 1 gång/tim (°C)
- `3`: Vindriktning, medelvärde 10 min, 1 gång/tim (grader, 0–360)
- `4`: Vindhastighet, medelvärde 10 min, 1 gång/tim (m/s)
- `6`: Relativ luftfuktighet, momentanvärde 1 gång/tim (%)
- `11`: Global irradians (svenska stationer), medelvärde 1 timme (W/m²)

Kvalitetsflaggor (`G`/`Y`/`O`/`R` m.fl.) hanteras explicit: endast observationer med godkända
flaggor (förval `G` och `Y`) används i beräkningar, medan underkända flaggor, saknade värden och
intervallbrott räknas transparent i `bortfiltrerade` och `bortfiltrerade_per_orsak`.
"""

from __future__ import annotations

import csv
import io
import json
import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
from numpy.typing import NDArray

PARAM_TEMPERATUR: Final[int] = 1
PARAM_VINDRIKTNING: Final[int] = 3
PARAM_VINDHASTIGHET: Final[int] = 4
PARAM_RELATIV_FUKTIGHET: Final[int] = 6
PARAM_GLOBALSTRALNING: Final[int] = 11

STANDARD_GODKANDA_KVALITETER: Final[frozenset[str]] = frozenset({"G", "Y"})
STRIKT_GODKANDA_KVALITETER: Final[frozenset[str]] = frozenset({"G"})

SEKTORNAMN_16: Final[tuple[str, ...]] = (
    "N",
    "NNO",
    "NO",
    "ONO",
    "O",
    "OSO",
    "SO",
    "SSO",
    "S",
    "SSV",
    "SV",
    "VSV",
    "V",
    "VNV",
    "NV",
    "NNV",
)

STANDARD_SVAG_VIND_GRANS_M_S: Final[float] = 0.5
STANDARD_SOMMARMANADER: Final[frozenset[int]] = frozenset({6, 7, 8})

_FYSISKA_GRANSER: Final[dict[int, tuple[float, float]]] = {
    PARAM_TEMPERATUR: (-70.0, 60.0),
    PARAM_VINDRIKTNING: (0.0, 360.0),
    PARAM_VINDHASTIGHET: (0.0, 120.0),
    PARAM_RELATIV_FUKTIGHET: (0.0, 100.0),
    PARAM_GLOBALSTRALNING: (0.0, 1500.0),
}


class SmhiVaderFel(ValueError):
    """Fel vid inläsning, validering eller analys av SMHI `metobs`-data."""


@dataclass(frozen=True)
class SmhiObservation:
    """En enskild kvalitetsgodkänd SMHI-observation i UTC."""

    tid_utc: str
    epoch_s: int
    varde: float
    kvalitet: str


@dataclass(frozen=True)
class SmhiParameterSerie:
    """Inläst SMHI-tidsserie med fullständig kvalitets- och filtreringsredovisning."""

    parameter_id: int | None
    parameter_namn: str
    enhet: str
    station_id: str | None
    station_namn: str | None
    observationer: tuple[SmhiObservation, ...]
    antal_totalt: int
    antal_godkanda: int
    bortfiltrerade: int
    bortfiltrerade_per_orsak: dict[str, int]
    flaggor_fordelning: dict[str, int]
    godkanda_flaggor: frozenset[str]

    def per_timme(self) -> dict[int, SmhiObservation]:
        """Mappar observationer till hel UTC-epochtimme (`round(epoch_s / 3600)`)."""
        ut: dict[int, SmhiObservation] = {}
        for obs in self.observationer:
            timme_h = int(round(obs.epoch_s / 3600.0))
            ut[timme_h] = obs
        return ut


@dataclass(frozen=True)
class VindrosSektor:
    """Statistik för en av de 16 kompasssektorerna (22,5° bredd)."""

    sektor_index: int
    namn: str
    centrum_grad: float
    antal: int
    andel_pct: float
    andel_av_vind_pct: float
    medelvind_m_s: float | None
    p95_vind_m_s: float | None

    def till_dict(self) -> dict[str, Any]:
        return {
            "sektor_index": self.sektor_index,
            "namn": self.namn,
            "centrum_grad": self.centrum_grad,
            "antal": self.antal,
            "andel_pct": self.andel_pct,
            "andel_av_vind_pct": self.andel_av_vind_pct,
            "medelvind_m_s": self.medelvind_m_s,
            "p95_vind_m_s": self.p95_vind_m_s,
        }


@dataclass(frozen=True)
class Vindros16:
    """Deterministisk 16-sektors vindros redo för `luft.vindkomfort`."""

    sektorer: tuple[VindrosSektor, ...]
    antal_giltiga: int
    antal_svag_vind: int
    andel_svag_vind_pct: float
    svag_vind_grans_m_s: float
    summa_andel_pct: float
    medelvind_m_s: float
    p95_vind_m_s: float
    bortfiltrerade: int
    bortfiltrerade_per_orsak: dict[str, int]
    station_id: str | None
    station_namn: str | None
    referenshojd_m: float = 10.0

    def till_dict(self) -> dict[str, Any]:
        return {
            "station_id": self.station_id,
            "station_namn": self.station_namn,
            "referenshojd_m": self.referenshojd_m,
            "antal_giltiga": self.antal_giltiga,
            "antal_svag_vind": self.antal_svag_vind,
            "andel_svag_vind_pct": self.andel_svag_vind_pct,
            "svag_vind_grans_m_s": self.svag_vind_grans_m_s,
            "summa_andel_pct": self.summa_andel_pct,
            "medelvind_m_s": self.medelvind_m_s,
            "p95_vind_m_s": self.p95_vind_m_s,
            "bortfiltrerade": self.bortfiltrerade,
            "bortfiltrerade_per_orsak": dict(self.bortfiltrerade_per_orsak),
            "sektorer": [s.till_dict() for s in self.sektorer],
        }


@dataclass(frozen=True)
class UtciVaderScenario:
    """Värmeböljescenario (t.ex. p95 eller p99) för direkt indata till UTCI-analys."""

    etikett: str
    percentil: float
    t_luft_c: float
    rh_pct: float | None
    vind_10m_m_s: float | None
    globalstralning_w_m2: float | None
    antal_timmar_i_urval: int
    antal_rh_matchade: int
    antal_vind_matchade: int
    antal_stralning_matchade: int

    def till_dict(self) -> dict[str, Any]:
        return {
            "etikett": self.etikett,
            "percentil": self.percentil,
            "t_luft_c": self.t_luft_c,
            "rh_pct": self.rh_pct,
            "vind_10m_m_s": self.vind_10m_m_s,
            "globalstralning_w_m2": self.globalstralning_w_m2,
            "antal_timmar_i_urval": self.antal_timmar_i_urval,
            "antal_rh_matchade": self.antal_rh_matchade,
            "antal_vind_matchade": self.antal_vind_matchade,
            "antal_stralning_matchade": self.antal_stralning_matchade,
        }


@dataclass(frozen=True)
class VarmeboljaStatistik:
    """Sommar- och värmeböljepercentiler (p95/p99) med sammanhörande RH och vind."""

    sommarmanader: tuple[int, ...]
    antal_timmar_analyserade: int
    bortfiltrerade: int
    bortfiltrerade_per_serie: dict[str, int]
    t_medel_c: float
    t_p95_c: float
    t_p99_c: float
    t_max_c: float
    scenario_p95: UtciVaderScenario
    scenario_p99: UtciVaderScenario
    station_id: str | None
    station_namn: str | None

    def till_dict(self) -> dict[str, Any]:
        return {
            "station_id": self.station_id,
            "station_namn": self.station_namn,
            "sommarmanader": list(self.sommarmanader),
            "antal_timmar_analyserade": self.antal_timmar_analyserade,
            "bortfiltrerade": self.bortfiltrerade,
            "bortfiltrerade_per_serie": dict(self.bortfiltrerade_per_serie),
            "t_medel_c": self.t_medel_c,
            "t_p95_c": self.t_p95_c,
            "t_p99_c": self.t_p99_c,
            "t_max_c": self.t_max_c,
            "scenario_p95": self.scenario_p95.till_dict(),
            "scenario_p99": self.scenario_p99.till_dict(),
        }


def _normalisera_godkanda_flaggor(flaggor: frozenset[str]) -> frozenset[str]:
    norm = frozenset(f.strip().upper() for f in flaggor if f.strip())
    if not norm:
        raise SmhiVaderFel("godkanda_flaggor måste innehålla minst en kvalitetsflagga.")
    return norm


def _gissa_parameter_id(namn: str) -> int | None:
    low = namn.strip().lower()
    if "temperatur" in low:
        return PARAM_TEMPERATUR
    if "vindriktning" in low:
        return PARAM_VINDRIKTNING
    if "vindhastighet" in low:
        return PARAM_VINDHASTIGHET
    if "luftfuktighet" in low or "fuktighet" in low:
        return PARAM_RELATIV_FUKTIGHET
    if "irradians" in low or "globalstr" in low:
        return PARAM_GLOBALSTRALNING
    return None


def _tolka_flyttal(ra: Any) -> float | None:
    if ra is None or isinstance(ra, bool):
        return None
    if isinstance(ra, int | float):
        v = float(ra)
        return v if math.isfinite(v) else None
    text = str(ra).strip()
    if not text:
        return None
    try:
        v = float(text.replace(",", "."))
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _tolka_tid_csv(datum_str: str, tid_str: str | None) -> tuple[str, int] | None:
    d_ren = datum_str.strip()
    if not d_ren:
        return None
    t_ren = (tid_str or "").strip()
    kombinerad = f"{d_ren}T{t_ren}" if t_ren else d_ren
    if kombinerad.endswith("Z"):
        kombinerad = kombinerad[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(kombinerad)
    except ValueError:
        return None
    dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    epoch_s = int(round(dt.timestamp()))
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ"), epoch_s


def _tolka_tid_json(post: Mapping[str, Any]) -> tuple[str, int] | None:
    ra_ms = post.get("date")
    if ra_ms is None:
        ra_ms = post.get("to")
    if ra_ms is None:
        return None
    if isinstance(ra_ms, int | float) and not isinstance(ra_ms, bool):
        if not math.isfinite(float(ra_ms)):
            return None
        epoch_s = int(round(float(ra_ms) / 1000.0))
        dt = datetime.fromtimestamp(epoch_s, tz=UTC)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ"), epoch_s
    if isinstance(ra_ms, str):
        text = ra_ms.strip()
        if not text:
            return None
        try:
            ms_val = float(text)
        except ValueError:
            return _tolka_tid_csv(text, None)
        if not math.isfinite(ms_val):
            return None
        epoch_s = int(round(ms_val / 1000.0))
        dt = datetime.fromtimestamp(epoch_s, tz=UTC)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ"), epoch_s
    return None


def _kontrollera_fysiskt_intervall(parameter_id: int | None, varde: float) -> bool:
    if parameter_id is None:
        return True
    granser = _FYSISKA_GRANSER.get(parameter_id)
    if granser is None:
        return True
    lo, hi = granser
    return lo <= varde <= hi


def las_smhi_csv_text(
    text: str,
    *,
    parameter_id: int | None = None,
    godkanda_flaggor: frozenset[str] = STANDARD_GODKANDA_KVALITETER,
) -> SmhiParameterSerie:
    """Tolkar SMHI Open Data `metobs` CSV (semikolon- eller kommaseparerad) från en sträng."""
    if not isinstance(text, str) or not text.strip():
        raise SmhiVaderFel("SMHI CSV-innehållet är tomt.")
    tillatna = _normalisera_godkanda_flaggor(godkanda_flaggor)
    ren_text = text.lstrip("\ufeff")
    rader_ra = ren_text.splitlines()

    # Extrahera ev. SMHI-metadata från inledningen och hitta rubrikraden (`Datum;...;Kvalitet`).
    station_namn: str | None = None
    station_id: str | None = None
    param_namn = ""
    enhet = ""
    rubrik_index: int | None = None
    avgransare = ";"

    for idx, rad in enumerate(rader_ra):
        strip = rad.strip()
        if not strip:
            continue
        for cand_sep in (";", ","):
            delar = [d.strip() for d in next(csv.reader([rad], delimiter=cand_sep))]
            low = [d.lower() for d in delar]
            if any(k.startswith("datum") or k.startswith("tid") for k in low) and any(
                k == "kvalitet" for k in low
            ):
                rubrik_index = idx
                avgransare = cand_sep
                break
        if rubrik_index is not None:
            break
        # Läs ev. metadatarader i SMHI:s standardhuvud (Stationsnamn;Stationsnummer;...)
        if ";" in rad:
            celler = [c.strip() for c in rad.split(";")]
            if celler[0].lower() == "stationsnamn" and idx + 1 < len(rader_ra):
                nasta = [c.strip() for c in rader_ra[idx + 1].split(";")]
                if nasta and nasta[0]:
                    station_namn = nasta[0]
                if len(nasta) > 1 and nasta[1]:
                    station_id = nasta[1]
            elif celler[0].lower() == "parameternamn" and idx + 1 < len(rader_ra):
                nasta = [c.strip() for c in rader_ra[idx + 1].split(";")]
                if nasta and nasta[0]:
                    param_namn = nasta[0]
                if len(nasta) > 2 and nasta[2]:
                    enhet = nasta[2]

    if rubrik_index is None:
        raise SmhiVaderFel(
            "Hittade ingen giltig SMHI CSV-rubrikrad med kolumnerna Datum/Tid och Kvalitet."
        )

    lasare = csv.reader(io.StringIO("\n".join(rader_ra[rubrik_index:])), delimiter=avgransare)
    rubrik = [k.strip() for k in next(lasare)]
    rubrik_low = [k.lower() for k in rubrik]

    idx_datum = next((i for i, k in enumerate(rubrik_low) if k.startswith("datum")), None)
    idx_tid = next(
        (i for i, k in enumerate(rubrik_low) if k.startswith("tid") and i != idx_datum), None
    )
    idx_kval = next((i for i, k in enumerate(rubrik_low) if k == "kvalitet"), None)
    if idx_kval is None or (idx_datum is None and idx_tid is None):
        raise SmhiVaderFel("SMHI CSV saknar nödvändiga kolumner (Datum/Tid och Kvalitet).")

    # Värdekolumnen ligger mellan datum/tid och kvalitet (typiskt kolumn 2).
    anvanda = {i for i in (idx_datum, idx_tid, idx_kval) if i is not None}
    varde_kandidater = [i for i in range(idx_kval) if i not in anvanda and rubrik[i] != ""]
    if not varde_kandidater:
        raise SmhiVaderFel("SMHI CSV saknar mätvärdeskolumn före Kvalitet.")
    idx_varde = varde_kandidater[0]
    if not param_namn:
        param_namn = rubrik[idx_varde]

    eff_param_id = parameter_id if parameter_id is not None else _gissa_parameter_id(param_namn)

    obs_lista: list[SmhiObservation] = []
    orsaker: Counter[str] = Counter()
    flaggor: Counter[str] = Counter()
    totalt = 0

    for rad_celler in lasare:
        if not rad_celler or not any(c.strip() for c in rad_celler):
            continue
        # Ignorera ev. helt tomma datum-/tidceller som bara har kommentartext i kolumn 5+.
        datum_cell = (
            rad_celler[idx_datum].strip()
            if idx_datum is not None and idx_datum < len(rad_celler)
            else ""
        )
        tid_cell = (
            rad_celler[idx_tid].strip()
            if idx_tid is not None and idx_tid < len(rad_celler)
            else ""
        )
        if not datum_cell and not tid_cell:
            continue

        totalt += 1
        kval_ra = rad_celler[idx_kval].strip().upper() if idx_kval < len(rad_celler) else ""
        flaggor[kval_ra or "<saknas>"] += 1

        if kval_ra not in tillatna:
            orsaker[f"kvalitetsflagga:{kval_ra or '<saknas>'}"] += 1
            continue

        tid_tolkad = (
            _tolka_tid_csv(datum_cell, tid_cell)
            if idx_datum is not None
            else _tolka_tid_csv(tid_cell, None)
        )
        if tid_tolkad is None:
            orsaker["ogiltig_tid"] += 1
            continue

        varde_ra = rad_celler[idx_varde] if idx_varde < len(rad_celler) else ""
        varde = _tolka_flyttal(varde_ra)
        if varde is None:
            orsaker["ogiltigt_varde"] += 1
            continue
        if not _kontrollera_fysiskt_intervall(eff_param_id, varde):
            orsaker["intervallfel"] += 1
            continue

        tid_utc, epoch_s = tid_tolkad
        obs_lista.append(
            SmhiObservation(tid_utc=tid_utc, epoch_s=epoch_s, varde=varde, kvalitet=kval_ra)
        )

    if totalt == 0:
        raise SmhiVaderFel("SMHI CSV innehåller inga datarader.")

    obs_lista.sort(key=lambda o: o.epoch_s)
    bortfiltrerade = totalt - len(obs_lista)
    return SmhiParameterSerie(
        parameter_id=eff_param_id,
        parameter_namn=param_namn,
        enhet=enhet,
        station_id=station_id,
        station_namn=station_namn,
        observationer=tuple(obs_lista),
        antal_totalt=totalt,
        antal_godkanda=len(obs_lista),
        bortfiltrerade=bortfiltrerade,
        bortfiltrerade_per_orsak=dict(sorted(orsaker.items())),
        flaggor_fordelning=dict(sorted(flaggor.items())),
        godkanda_flaggor=tillatna,
    )


def las_smhi_csv_fil(
    sokvag: str | Path,
    *,
    parameter_id: int | None = None,
    godkanda_flaggor: frozenset[str] = STANDARD_GODKANDA_KVALITETER,
) -> SmhiParameterSerie:
    """Läser SMHI Open Data `metobs` CSV från en lokal fil."""
    fil = Path(sokvag)
    try:
        text = fil.read_text(encoding="utf-8")
    except OSError as exc:
        raise SmhiVaderFel(f"Kunde inte läsa SMHI CSV-fil {fil}: {exc}") from exc
    return las_smhi_csv_text(text, parameter_id=parameter_id, godkanda_flaggor=godkanda_flaggor)


def las_smhi_json_text(
    text: str,
    *,
    parameter_id: int | None = None,
    godkanda_flaggor: frozenset[str] = STANDARD_GODKANDA_KVALITETER,
) -> SmhiParameterSerie:
    """Tolkar SMHI Open Data `metobs` v1.0 JSON från en sträng."""
    if not isinstance(text, str) or not text.strip():
        raise SmhiVaderFel("SMHI JSON-innehållet är tomt.")
    tillatna = _normalisera_godkanda_flaggor(godkanda_flaggor)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SmhiVaderFel(f"Ogiltig SMHI JSON: {exc}") from exc
    if not isinstance(data, Mapping):
        raise SmhiVaderFel("SMHI JSON måste ha ett rotobjekt (dict).")

    varden = data.get("value")
    if not isinstance(varden, list):
        raise SmhiVaderFel("SMHI JSON saknar listfältet 'value'.")

    station_obj = data.get("station")
    station_id: str | None = None
    station_namn: str | None = None
    if isinstance(station_obj, Mapping):
        raw_sid = station_obj.get("key") or station_obj.get("id")
        if raw_sid is not None and str(raw_sid).strip():
            station_id = str(raw_sid).strip()
        raw_snamn = station_obj.get("name")
        if raw_snamn is not None and str(raw_snamn).strip():
            station_namn = str(raw_snamn).strip()

    param_obj = data.get("parameter")
    param_namn = ""
    enhet = ""
    param_id_fran_json: int | None = None
    if isinstance(param_obj, Mapping):
        param_namn = str(param_obj.get("name", "")).strip()
        enhet = str(param_obj.get("unit", "")).strip()
        raw_pkey = param_obj.get("key")
        if raw_pkey is not None:
            try:
                param_id_fran_json = int(str(raw_pkey).strip())
            except ValueError:
                param_id_fran_json = None

    if parameter_id is not None:
        eff_param_id: int | None = parameter_id
    elif param_id_fran_json is not None:
        eff_param_id = param_id_fran_json
    else:
        eff_param_id = _gissa_parameter_id(param_namn)

    obs_lista: list[SmhiObservation] = []
    orsaker: Counter[str] = Counter()
    flaggor: Counter[str] = Counter()
    totalt = 0

    for post in varden:
        totalt += 1
        if not isinstance(post, Mapping):
            orsaker["ogiltig_post"] += 1
            flaggor["<saknas>"] += 1
            continue
        kval_ra = str(post.get("quality", "")).strip().upper()
        flaggor[kval_ra or "<saknas>"] += 1
        if kval_ra not in tillatna:
            orsaker[f"kvalitetsflagga:{kval_ra or '<saknas>'}"] += 1
            continue

        tid_tolkad = _tolka_tid_json(post)
        if tid_tolkad is None:
            orsaker["ogiltig_tid"] += 1
            continue

        varde = _tolka_flyttal(post.get("value"))
        if varde is None:
            orsaker["ogiltigt_varde"] += 1
            continue
        if not _kontrollera_fysiskt_intervall(eff_param_id, varde):
            orsaker["intervallfel"] += 1
            continue

        tid_utc, epoch_s = tid_tolkad
        obs_lista.append(
            SmhiObservation(tid_utc=tid_utc, epoch_s=epoch_s, varde=varde, kvalitet=kval_ra)
        )

    if totalt == 0:
        raise SmhiVaderFel("SMHI JSON innehåller inga observationer i 'value'.")

    obs_lista.sort(key=lambda o: o.epoch_s)
    bortfiltrerade = totalt - len(obs_lista)
    return SmhiParameterSerie(
        parameter_id=eff_param_id,
        parameter_namn=param_namn,
        enhet=enhet,
        station_id=station_id,
        station_namn=station_namn,
        observationer=tuple(obs_lista),
        antal_totalt=totalt,
        antal_godkanda=len(obs_lista),
        bortfiltrerade=bortfiltrerade,
        bortfiltrerade_per_orsak=dict(sorted(orsaker.items())),
        flaggor_fordelning=dict(sorted(flaggor.items())),
        godkanda_flaggor=tillatna,
    )


def las_smhi_json_fil(
    sokvag: str | Path,
    *,
    parameter_id: int | None = None,
    godkanda_flaggor: frozenset[str] = STANDARD_GODKANDA_KVALITETER,
) -> SmhiParameterSerie:
    """Läser SMHI Open Data `metobs` v1.0 JSON från en lokal fil."""
    fil = Path(sokvag)
    try:
        text = fil.read_text(encoding="utf-8")
    except OSError as exc:
        raise SmhiVaderFel(f"Kunde inte läsa SMHI JSON-fil {fil}: {exc}") from exc
    return las_smhi_json_text(text, parameter_id=parameter_id, godkanda_flaggor=godkanda_flaggor)


def las_smhi_fil(
    sokvag: str | Path,
    *,
    parameter_id: int | None = None,
    godkanda_flaggor: frozenset[str] = STANDARD_GODKANDA_KVALITETER,
) -> SmhiParameterSerie:
    """Läser lokal SMHI-fil i JSON- eller CSV-format (väljs via filändelse eller innehåll)."""
    fil = Path(sokvag)
    try:
        text = fil.read_text(encoding="utf-8")
    except OSError as exc:
        raise SmhiVaderFel(f"Kunde inte läsa SMHI-fil {fil}: {exc}") from exc
    andelse = fil.suffix.lower()
    if andelse == ".json" or text.lstrip("\ufeff \t\r\n").startswith("{"):
        return las_smhi_json_text(
            text, parameter_id=parameter_id, godkanda_flaggor=godkanda_flaggor
        )
    return las_smhi_csv_text(text, parameter_id=parameter_id, godkanda_flaggor=godkanda_flaggor)


def riktning_till_sektor16(riktning_grad: float) -> int:
    """Mappar vindriktning i grader `[0, 360]` till 16-sektorsindex `0..15` (`0 = N`)."""
    if not math.isfinite(riktning_grad):
        raise SmhiVaderFel(f"Ogiltig vindriktning {riktning_grad}.")
    norm = float(riktning_grad) % 360.0
    return int(math.floor((norm + 11.25) / 22.5)) % 16


def berakna_vindros_16(
    vindhastighet: SmhiParameterSerie,
    vindriktning: SmhiParameterSerie,
    *,
    svag_vind_grans_m_s: float = STANDARD_SVAG_VIND_GRANS_M_S,
    referenshojd_m: float = 10.0,
) -> Vindros16:
    """Beräknar deterministisk 16-sektors vindros ur parade SMHI-timserier för `luft.vindkomfort`.

    Timmar med `u < svag_vind_grans_m_s` räknas i `andel_svag_vind_pct` (så att
    `sum(s.andel_pct) + andel_svag_vind_pct == 100.0`), medan `andel_av_vind_pct` summerar
    till `100.0 %` över icke-svaga timmar. Oparade eller bortfiltrerade observationer räknas
    transparent i `bortfiltrerade`.
    """
    if not math.isfinite(svag_vind_grans_m_s) or svag_vind_grans_m_s < 0.0:
        raise SmhiVaderFel("svag_vind_grans_m_s måste vara >= 0.")
    if not math.isfinite(referenshojd_m) or referenshojd_m <= 0.0:
        raise SmhiVaderFel("referenshojd_m måste vara > 0.")

    u_map = vindhastighet.per_timme()
    d_map = vindriktning.per_timme()
    gemensamma = sorted(set(u_map) & set(d_map))
    oparade = len(set(u_map) ^ set(d_map))

    orsaker: Counter[str] = Counter()
    for nyckel, antal in vindhastighet.bortfiltrerade_per_orsak.items():
        orsaker[f"vindhastighet:{nyckel}"] += antal
    for nyckel, antal in vindriktning.bortfiltrerade_per_orsak.items():
        orsaker[f"vindriktning:{nyckel}"] += antal
    if oparade > 0:
        orsaker["oparad_timme"] += oparade

    giltiga_u: list[float] = []
    sektor_hastigheter: list[list[float]] = [[] for _ in range(16)]
    antal_svag = 0

    for timme_h in gemensamma:
        u = u_map[timme_h].varde
        d = d_map[timme_h].varde
        if u < 0.0 or not (0.0 <= d <= 360.0):
            orsaker["intervallfel_par"] += 1
            continue
        giltiga_u.append(u)
        if u < svag_vind_grans_m_s:
            antal_svag += 1
        else:
            sektor_hastigheter[riktning_till_sektor16(d)].append(u)

    antal_giltiga = len(giltiga_u)
    if antal_giltiga == 0:
        raise SmhiVaderFel(
            "Inga giltiga parade vindobservationer att beräkna vindros från."
        )

    antal_med_vind = antal_giltiga - antal_svag
    alla_u_arr = np.asarray(giltiga_u, dtype=np.float64)
    andel_svag_pct = (100.0 * antal_svag) / antal_giltiga

    sektorer: list[VindrosSektor] = []
    for idx, namn in enumerate(SEKTORNAMN_16):
        u_s = sektor_hastigheter[idx]
        n_s = len(u_s)
        andel_pct = (100.0 * n_s) / antal_giltiga
        andel_av_vind = (100.0 * n_s) / antal_med_vind if antal_med_vind > 0 else 0.0
        if n_s > 0:
            arr_s = np.asarray(u_s, dtype=np.float64)
            medel_s: float | None = float(np.mean(arr_s))
            p95_s: float | None = float(np.percentile(arr_s, 95.0))
        else:
            medel_s = None
            p95_s = None
        sektorer.append(
            VindrosSektor(
                sektor_index=idx,
                namn=namn,
                centrum_grad=idx * 22.5,
                antal=n_s,
                andel_pct=andel_pct,
                andel_av_vind_pct=andel_av_vind,
                medelvind_m_s=medel_s,
                p95_vind_m_s=p95_s,
            )
        )

    summa_pct = sum(s.andel_pct for s in sektorer) + andel_svag_pct
    totalt_bortfiltrerade = int(sum(orsaker.values()))
    station_id = vindhastighet.station_id or vindriktning.station_id
    station_namn = vindhastighet.station_namn or vindriktning.station_namn

    return Vindros16(
        sektorer=tuple(sektorer),
        antal_giltiga=antal_giltiga,
        antal_svag_vind=antal_svag,
        andel_svag_vind_pct=andel_svag_pct,
        svag_vind_grans_m_s=float(svag_vind_grans_m_s),
        summa_andel_pct=summa_pct,
        medelvind_m_s=float(np.mean(alla_u_arr)),
        p95_vind_m_s=float(np.percentile(alla_u_arr, 95.0)),
        bortfiltrerade=totalt_bortfiltrerade,
        bortfiltrerade_per_orsak=dict(sorted(orsaker.items())),
        station_id=station_id,
        station_namn=station_namn,
        referenshojd_m=float(referenshojd_m),
    )


def bygg_vindkomfort_underlag(
    vindhastighet: SmhiParameterSerie,
    vindriktning: SmhiParameterSerie,
) -> dict[str, Any]:
    """Bygger tidsjusterade fält (`tid`, `vindriktning_grad`, `vindhastighet_m_s`, `giltig`)
    för direkt inmatning till `luft.vindbibliotek.Vaderserie(...)` och `luft.vindkomfort`.
    """
    u_map = vindhastighet.per_timme()
    d_map = vindriktning.per_timme()
    alla_h = sorted(set(u_map) | set(d_map))
    if not alla_h:
        raise SmhiVaderFel("Inga vindobservationer att bygga vindkomfortunderlag från.")

    h0, h1 = alla_h[0], alla_h[-1]
    n = h1 - h0 + 1
    tid: list[str] = []
    rikt: NDArray[np.float64] = np.full(n, np.nan, dtype=np.float64)
    hast: NDArray[np.float64] = np.full(n, np.nan, dtype=np.float64)
    giltig: NDArray[np.bool_] = np.zeros(n, dtype=np.bool_)

    for i in range(n):
        th = h0 + i
        dt = datetime.fromtimestamp(th * 3600, tz=UTC)
        tid.append(dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
        u_obs = u_map.get(th)
        d_obs = d_map.get(th)
        if (
            u_obs is not None
            and d_obs is not None
            and u_obs.varde >= 0.0
            and 0.0 <= d_obs.varde <= 360.0
        ):
            hast[i] = u_obs.varde
            rikt[i] = d_obs.varde % 360.0
            giltig[i] = True

    return {
        "tid": tuple(tid),
        "vindriktning_grad": rikt,
        "vindhastighet_m_s": hast,
        "giltig": giltig,
        "antal_giltiga": int(giltig.sum()),
        "antal_ogiltiga": int((~giltig).sum()),
    }


def _bygg_utci_scenario(
    etikett: str,
    percentil: float,
    t_grans_c: float,
    urval_timmar: list[tuple[int, float]],
    rh_map: Mapping[int, SmhiObservation],
    vind_map: Mapping[int, SmhiObservation],
    stralning_map: Mapping[int, SmhiObservation],
) -> UtciVaderScenario:
    # Samtliga timmar på eller över percentiltemperaturen (garanterat >= 1 timme).
    matchade = [th for th, temp in urval_timmar if temp >= t_grans_c]
    rh_v = [rh_map[th].varde for th in matchade if th in rh_map]
    vind_v = [vind_map[th].varde for th in matchade if th in vind_map]
    stral_v = [stralning_map[th].varde for th in matchade if th in stralning_map]

    return UtciVaderScenario(
        etikett=etikett,
        percentil=percentil,
        t_luft_c=t_grans_c,
        rh_pct=float(np.median(np.asarray(rh_v, dtype=np.float64))) if rh_v else None,
        vind_10m_m_s=float(np.median(np.asarray(vind_v, dtype=np.float64))) if vind_v else None,
        globalstralning_w_m2=(
            float(np.median(np.asarray(stral_v, dtype=np.float64))) if stral_v else None
        ),
        antal_timmar_i_urval=len(matchade),
        antal_rh_matchade=len(rh_v),
        antal_vind_matchade=len(vind_v),
        antal_stralning_matchade=len(stral_v),
    )


def berakna_varmebolja_utci(
    temperatur: SmhiParameterSerie,
    *,
    relativ_fuktighet: SmhiParameterSerie | None = None,
    vindhastighet: SmhiParameterSerie | None = None,
    globalstralning: SmhiParameterSerie | None = None,
    sommarmanader: frozenset[int] | None = STANDARD_SOMMARMANADER,
    dagtid_utc: tuple[int, int] | None = None,
) -> VarmeboljaStatistik:
    """Beräknar sommar-/värmeböljepercentiler (`p95`/`p99` °C) samt sammanhörande RH och vind.

    För varje percentil (`p95`, `p99`) tas alla analyserade timmar med `T >= T_percentil` och
    medianvärdet av samtida relativ luftfuktighet (%), vindhastighet på 10 m (m/s) och eventuell
    globalstrålning (W/m²) redovisas. Saknas samtida observationer returneras `None` (inga
    påhittade standardvärden).
    """
    if sommarmanader is not None:
        if not sommarmanader or any(m < 1 or m > 12 for m in sommarmanader):
            raise SmhiVaderFel("sommarmanader måste innehålla månadsnummer i 1..12.")
        manader_tuple = tuple(sorted(sommarmanader))
    else:
        manader_tuple = tuple(range(1, 13))

    if dagtid_utc is not None:
        h_start, h_slut = dagtid_utc
        if not (0 <= h_start <= 23 and 0 <= h_slut <= 23 and h_start <= h_slut):
            raise SmhiVaderFel("dagtid_utc måste vara (start_h, slut_h) i 0..23 med start <= slut.")

    t_map = temperatur.per_timme()
    rh_map = relativ_fuktighet.per_timme() if relativ_fuktighet is not None else {}
    vind_map = vindhastighet.per_timme() if vindhastighet is not None else {}
    stral_map = globalstralning.per_timme() if globalstralning is not None else {}

    urval: list[tuple[int, float]] = []
    for timme_h in sorted(t_map):
        dt = datetime.fromtimestamp(timme_h * 3600, tz=UTC)
        if sommarmanader is not None and dt.month not in sommarmanader:
            continue
        if dagtid_utc is not None and not (dagtid_utc[0] <= dt.hour <= dagtid_utc[1]):
            continue
        urval.append((timme_h, t_map[timme_h].varde))

    if not urval:
        raise SmhiVaderFel(
            "Inga kvalitetsgodkända temperaturobservationer matchar valt sommar-/tidsurval."
        )

    temps = np.asarray([t for _, t in urval], dtype=np.float64)
    t_medel = float(np.mean(temps))
    t_p95 = float(np.percentile(temps, 95.0))
    t_p99 = float(np.percentile(temps, 99.0))
    t_max = float(np.max(temps))

    sc_p95 = _bygg_utci_scenario("p95", 95.0, t_p95, urval, rh_map, vind_map, stral_map)
    sc_p99 = _bygg_utci_scenario("p99", 99.0, t_p99, urval, rh_map, vind_map, stral_map)

    bort_per_serie: dict[str, int] = {"temperatur": temperatur.bortfiltrerade}
    if relativ_fuktighet is not None:
        bort_per_serie["relativ_fuktighet"] = relativ_fuktighet.bortfiltrerade
    if vindhastighet is not None:
        bort_per_serie["vindhastighet"] = vindhastighet.bortfiltrerade
    if globalstralning is not None:
        bort_per_serie["globalstralning"] = globalstralning.bortfiltrerade

    return VarmeboljaStatistik(
        sommarmanader=manader_tuple,
        antal_timmar_analyserade=len(urval),
        bortfiltrerade=sum(bort_per_serie.values()),
        bortfiltrerade_per_serie=bort_per_serie,
        t_medel_c=t_medel,
        t_p95_c=t_p95,
        t_p99_c=t_p99,
        t_max_c=t_max,
        scenario_p95=sc_p95,
        scenario_p99=sc_p99,
        station_id=temperatur.station_id,
        station_namn=temperatur.station_namn,
    )
