# SPDX-License-Identifier: GPL-3.0-or-later
"""Klient och tolk för SMHI Öppna data (MetObs v1.0, CC BY 4.0).

Hämtar och tolkar timvisa meteorologiska observationer direkt till Flodens
datatyper `luft.vindbibliotek.Vaderserie` (vindros/spridning) och
`luft.emission.VaderserieTimme` (NORTRIP vägdamm + snö/smältning).

Standardparametrar i SMHI MetObs v1.0:
- 1: Lufttemperatur, momentanvärde 1 gång/tim (°C)
- 3: Vindriktning, medelvärde 10 min, 1 gång/tim (grader)
- 4: Vindhastighet, medelvärde 10 min, 1 gång/tim (m/s)
- 6: Relativ luftfuktighet, momentanvärde 1 gång/tim (%)
- 7: Nederbördsmängd, summa 1 timme (mm)
- 9: Lufttryck reducerat havsytans nivå, 1 gång/tim (hPa)
"""

from __future__ import annotations

import json
import math
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from luft.emission import VaderserieTimme
from luft.vindbibliotek import Vaderserie

SMHI_METOBS_BAS_URL = "https://opendata-download-metobs.smhi.se/api/version/1.0"

PARAM_TEMPERATUR = 1
PARAM_VINDRIKTNING = 3
PARAM_VINDHASTIGHET = 4
PARAM_RELATIV_FUKTIGHET = 6
PARAM_NEDERBORD = 7
PARAM_LUFTTRYCK = 9

# Kvalitetsflaggor i SMHI MetObs: G = granskad/godkänd, Y = misstänkt/preliminär, R = felaktig.
GILTIGA_KVALITETER = frozenset({"G", "Y"})


class SmhiFel(Exception):
    """Fel vid hämtning eller tolkning av SMHI MetObs-data."""


@dataclass(frozen=True)
class SmhiStation:
    """Metadata för en SMHI MetObs-station."""

    id: str
    namn: str
    lat_grader: float
    lon_grader: float
    hojd_m: float
    aktiv: bool


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * r * math.asin(min(1.0, math.sqrt(a)))


def tolka_stationer(data: Mapping[str, Any]) -> list[SmhiStation]:
    """Tolkar stationslistan från `/parameter/{id}.json`."""
    ra = data.get("station")
    if not isinstance(ra, list):
        raise SmhiFel("SMHI-svar saknar fältet 'station'.")
    ut: list[SmhiStation] = []
    for post in ra:
        if not isinstance(post, Mapping):
            continue
        try:
            ut.append(
                SmhiStation(
                    id=str(post["id"]),
                    namn=str(post.get("name", "")),
                    lat_grader=float(post["latitude"]),
                    lon_grader=float(post["longitude"]),
                    hojd_m=float(post.get("height", 0.0)),
                    aktiv=bool(post.get("active", False)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return ut


def hitta_narmaste_station(
    lat_grader: float,
    lon_grader: float,
    stationer: Sequence[SmhiStation],
    *,
    bara_aktiva: bool = True,
) -> tuple[SmhiStation, float]:
    """Hittar närmaste SMHI-station till `(lat_grader, lon_grader)`. Returnerar `(station, km)`."""
    kandidater = [s for s in stationer if s.aktiv] if bara_aktiva else list(stationer)
    if not kandidater:
        raise SmhiFel("Inga matchande SMHI-stationer tillgängliga.")
    bast: SmhiStation | None = None
    bast_km = math.inf
    for st in kandidater:
        d = _haversine_km(lat_grader, lon_grader, st.lat_grader, st.lon_grader)
        if d < bast_km:
            bast_km = d
            bast = st
    assert bast is not None
    return bast, bast_km


def tolka_smhi_timdata(
    data: Mapping[str, Any],
    *,
    tillatna_kvaliteter: frozenset[str] = GILTIGA_KVALITETER,
) -> dict[int, float]:
    """Tolkar SMHI MetObs JSON-data till `{timme_epoch_h: flyttalsvarde}`.

    Poster med ogiltig kvalitetsflagga eller icke-numeriskt värde filtreras bort.
    Tidpunkten avrundas till närmaste hela UTC-timme (epoch-timme = `round(ms / 3_600_000)`).
    """
    varden = data.get("value")
    if not isinstance(varden, list):
        raise SmhiFel("SMHI-svar saknar fältet 'value'.")
    ut: dict[int, float] = {}
    for post in varden:
        if not isinstance(post, Mapping):
            continue
        kval = str(post.get("quality", "G"))
        if kval not in tillatna_kvaliteter:
            continue
        ms = post.get("date")
        if ms is None:
            # Nederbörd (param 7) använder ibland 'to' (periodens slut) i stället för 'date'.
            ms = post.get("to")
        if ms is None:
            continue
        try:
            tim_h = int(round(float(ms) / 3_600_000.0))
            val = float(post["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(val):
            ut[tim_h] = val
    return ut


def bygg_vaderserie_fran_smhi(
    vindhastighet_per_h: Mapping[int, float],
    vindriktning_per_h: Mapping[int, float],
) -> Vaderserie:
    """Bygger `luft.vindbibliotek.Vaderserie` på en gemensam sammanhängande timaxel."""
    alla_h = sorted(set(vindhastighet_per_h) | set(vindriktning_per_h))
    if not alla_h:
        raise SmhiFel("Inga vindobservationer att bygga Vaderserie av.")
    h0, h1 = alla_h[0], alla_h[-1]
    n = h1 - h0 + 1
    tid: list[str] = []
    hast = np.full(n, np.nan, dtype=np.float64)
    rikt = np.full(n, np.nan, dtype=np.float64)
    giltig = np.zeros(n, dtype=np.bool_)
    for i in range(n):
        th = h0 + i
        dt = datetime.fromtimestamp(th * 3600, tz=UTC)
        tid.append(dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
        u = vindhastighet_per_h.get(th)
        d = vindriktning_per_h.get(th)
        if u is not None and d is not None and u >= 0.0:
            hast[i] = u
            rikt[i] = d % 360.0
            giltig[i] = True
    return Vaderserie(
        tid=tuple(tid),
        vindriktning_grad=rikt,
        vindhastighet_m_s=hast,
        giltig=giltig,
    )


def bygg_nortrip_vader_fran_smhi(
    temperatur_per_h: Mapping[int, float],
    nederbord_per_h: Mapping[int, float],
    fuktighet_per_h: Mapping[int, float],
    vindhastighet_per_h: Mapping[int, float],
    *,
    lufttryck_hpa_per_h: Mapping[int, float] | None = None,
    vindhojd_m: float = 10.0,
    temperaturhojd_m: float = 2.0,
) -> VaderserieTimme:
    """Bygger en luckfri `VaderserieTimme` för NORTRIP (`luft.emission.berakna_emission`).

    Korta luckor (saknade timmar i temperatur, fuktighet, vind eller tryck) linjärinterpoleras
    mellan närmaste giltiga observationer; saknad nederbörd sätts till `0.0 mm/h`.
    """
    gemensam = (
        set(temperatur_per_h) & set(fuktighet_per_h) & set(vindhastighet_per_h)
    )
    if not gemensam:
        raise SmhiFel("Saknar överlappande timmar mellan temperatur, fuktighet och vind.")
    h0, h1 = min(gemensam), max(gemensam)
    n = h1 - h0 + 1
    t_ax = np.arange(n, dtype=np.float64)

    def _fyll_serie(karta: Mapping[int, float], namn: str) -> np.ndarray[Any, np.dtype[np.float64]]:
        xs = np.array([h - h0 for h in sorted(karta) if h0 <= h <= h1], dtype=np.float64)
        ys = np.array([karta[h] for h in sorted(karta) if h0 <= h <= h1], dtype=np.float64)
        if xs.size == 0:
            raise SmhiFel(f"Inga giltiga observationer för {namn} i tidsintervallet.")
        return np.asarray(np.interp(t_ax, xs, ys), dtype=np.float64)

    temp = _fyll_serie(temperatur_per_h, "temperatur")
    rh = np.clip(_fyll_serie(fuktighet_per_h, "relativ fuktighet"), 0.0, 100.0)
    vind = np.maximum(0.0, _fyll_serie(vindhastighet_per_h, "vindhastighet"))
    ned = np.array([max(0.0, nederbord_per_h.get(h0 + i, 0.0)) for i in range(n)], dtype=np.float64)

    tryck_hpa: np.ndarray[Any, np.dtype[np.float64]] | None = None
    if lufttryck_hpa_per_h:
        tryck_hpa = _fyll_serie(lufttryck_hpa_per_h, "lufttryck")

    start_dt = datetime.fromtimestamp(h0 * 3600, tz=UTC).replace(tzinfo=None)
    return VaderserieTimme(
        start=start_dt.strftime("%Y-%m-%dT%H:%M"),
        nederbord_mm_h=ned,
        temperatur_c=temp,
        relativ_fuktighet_procent=rh,
        vindhastighet_m_s=vind,
        vindhojd_m=vindhojd_m,
        temperaturhojd_m=temperaturhojd_m,
        lufttryck_hpa=tryck_hpa,
    )


def hamta_smhi_json(
    relativ_sokvag: str,
    *,
    bas_url: str = SMHI_METOBS_BAS_URL,
    cache_mapp: Path | None = None,
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    """Hämtar ett JSON-dokument från SMHI MetObs med valfri lokal filcache."""
    sokvag = relativ_sokvag.lstrip("/")
    cache_fil: Path | None = None
    if cache_mapp is not None:
        saker_namn = sokvag.replace("/", "_")
        cache_fil = cache_mapp / saker_namn
        if cache_fil.is_file():
            return dict(json.loads(cache_fil.read_text(encoding="utf-8")))

    url = f"{bas_url.rstrip('/')}/{sokvag}"
    if not url.startswith("https://"):
        raise SmhiFel(f"Endast HTTPS tillåts mot SMHI, fick {url!r}.")
    req = urllib.request.Request(url, headers={"User-Agent": "Floden/1.0 (SMHI MetObs)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as svar:
            ra = svar.read().decode("utf-8")
    except Exception as exc:
        raise SmhiFel(f"Kunde inte hämta {url}: {exc}") from exc
    data = dict(json.loads(ra))
    if cache_fil is not None:
        cache_fil.parent.mkdir(parents=True, exist_ok=True)
        cache_fil.write_text(ra, encoding="utf-8")
    return data


def hamta_smhi_parameter(
    parameter_id: int,
    station_id: str,
    *,
    period: str = "latest-months",
    cache_mapp: Path | None = None,
    timeout_s: float = 15.0,
) -> dict[int, float]:
    """Hämtar och tolkar en timvis SMHI MetObs-parameter för en station."""
    data = hamta_smhi_json(
        f"parameter/{int(parameter_id)}/station/{station_id}/period/{period}/data.json",
        cache_mapp=cache_mapp,
        timeout_s=timeout_s,
    )
    return tolka_smhi_timdata(data)
