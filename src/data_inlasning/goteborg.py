"""Öppna grunddata och testområde för Göteborgs Stad (SWEREF 99 12 00, EPSG:3007).

Göteborgs Stad publicerar öppen baskarta, 3D-stadsmodell och öppna timvisa luftmätdata
(Miljöförvaltningen: Femman, Haga, Gårda) utan avgift, vilket gör staden väl lämpad som
öppen test- och demokommun för Flodens hela kedja (vatten, dagsljus, luft och buller).

All hämtning stöder lokal filcache och injicerbar `hamta_bytes` för 100 % deterministiska
enhetstester utan nätverk.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from gemensamt.hash import sha256_bytes
from gemensamt.koordinater import transformera

EPSG_WGS84 = 4326
EPSG_GOTEBORG = 3007  # SWEREF 99 12 00
SMHI_STATION_GOTEBORG_A = 71420

TILLATNA_GOTEBORG_VARDAR: tuple[str, ...] = (
    "data.goteborg.se",
    "opengeodata.goteborg.se",
    "geoserver.goteborg.se",
    "catalog.goteborg.se",
)

STANDARD_WFS_BAS_URL = "https://opengeodata.goteborg.se/services/baskarta/wfs"


class GoteborgDataFel(Exception):
    """Fel vid hämtning eller tolkning av öppna data från Göteborgs Stad."""


@dataclass(frozen=True)
class GoteborgTestomrade:
    """Definition av ett öppet test- och demoområde i Göteborg (EPSG:3007)."""

    namn: str
    beskrivning: str
    epsg: int
    bbox_3007: tuple[float, float, float, float]  # (e_min, n_min, e_max, n_max)
    smhi_station_id: int
    luft_station: str
    lagerprofil_rel: str = "data/lagerprofiler/goteborg_baskarta.toml"

    def bbox_wgs84(self) -> tuple[float, float, float, float]:
        """Returnerar (lon_min, lat_min, lon_max, lat_max) i WGS 84."""
        e0, n0, e1, n1 = self.bbox_3007
        lon0, lat0 = transformera(e0, n0, self.epsg, EPSG_WGS84)
        lon1, lat1 = transformera(e1, n1, self.epsg, EPSG_WGS84)
        return (min(lon0, lon1), min(lat0, lat1), max(lon0, lon1), max(lat0, lat1))


# Centralt öppet testkvarter (Haga / Linné, ca 500 x 500 m, EPSG:3007, nära Haga luftstation)
GOTEBORG_HAGA_TESTOMRADE = GoteborgTestomrade(
    namn="goteborg_haga",
    beskrivning=(
        "Haga / Linnéstaden (500 x 500 m, EPSG:3007) med öppen baskarta och mätstation Haga"
    ),
    epsg=EPSG_GOTEBORG,
    bbox_3007=(147400.0, 6397500.0, 147900.0, 6398000.0),
    smhi_station_id=SMHI_STATION_GOTEBORG_A,
    luft_station="Haga",
)


@dataclass(frozen=True)
class GoteborgLuftserie:
    """Tolkad timvis mätserie från Göteborgs Miljöförvaltnings öppna luftdata."""

    station: str
    tider_iso: tuple[str, ...]
    pm10_ug_m3: NDArray[np.float64]
    pm25_ug_m3: NDArray[np.float64]
    no2_ug_m3: NDArray[np.float64]


def _kontrollera_goteborg_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise GoteborgDataFel(f"Endast HTTPS tillåts för Göteborgs öppna tjänster, fick {url!r}.")
    host = (parsed.hostname or "").lower()
    if not (
        host in TILLATNA_GOTEBORG_VARDAR
        or host.endswith(".goteborg.se")
    ):
        raise GoteborgDataFel(f"Värden {host!r} hör inte till tillåtna goteborg.se-domäner.")


def _standard_hamta_bytes(url: str, timeout_s: float = 20.0) -> bytes:
    _kontrollera_goteborg_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "Floden/1.0 (open-data-client)"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - HTTPS + goteborg.se
        return bytes(resp.read())


def hamta_goteborg_wfs_geojson(
    lager: str,
    bbox_3007: tuple[float, float, float, float],
    *,
    wfs_bas_url: str = STANDARD_WFS_BAS_URL,
    cache_mapp: str | Path | None = None,
    hamta_bytes: Callable[[str], bytes] | None = None,
    max_objekt: int = 5000,
) -> dict[str, Any]:
    """Hämtar ett GeoJSON-lager från Göteborgs öppna WFS (EPSG:3007) med lokal filcache."""
    if not lager.strip():
        raise GoteborgDataFel("lagernamn får inte vara tomt.")
    _kontrollera_goteborg_url(wfs_bas_url)
    e0, n0, e1, n1 = bbox_3007
    if e0 >= e1 or n0 >= n1:
        raise GoteborgDataFel(f"Ogiltig bbox_3007: {bbox_3007}.")

    params = urllib.parse.urlencode(
        {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": lager,
            "srsName": f"EPSG:{EPSG_GOTEBORG}",
            "bbox": f"{e0},{n0},{e1},{n1},EPSG:{EPSG_GOTEBORG}",
            "outputFormat": "application/json",
            "count": str(int(max_objekt)),
        }
    )
    url = f"{wfs_bas_url}?{params}"

    cache_fil: Path | None = None
    if cache_mapp is not None:
        cm = Path(cache_mapp)
        cm.mkdir(parents=True, exist_ok=True)
        nyckel = sha256_bytes(url.encode("utf-8"))[:16]
        cache_fil = cm / f"gbg_wfs_{nyckel}.geojson"
        if cache_fil.is_file():
            data = json.loads(cache_fil.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data

    hamtare = hamta_bytes if hamta_bytes is not None else _standard_hamta_bytes
    ra = hamtare(url)
    try:
        data = json.loads(ra.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GoteborgDataFel(f"WFS-svaret för {lager!r} var inte giltig JSON.") from exc
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise GoteborgDataFel(f"WFS-svaret för {lager!r} saknar FeatureCollection.")

    if cache_fil is not None:
        cache_fil.write_bytes(ra)
    return data


def tolka_goteborg_luft_json(
    ra_data: dict[str, Any] | list[dict[str, Any]],
    *,
    station: str = "Haga",
) -> GoteborgLuftserie:
    """Tolkar öppna timvisa mätdata (PM10, PM2.5, NO2 i µg/m³) från Göteborgs Miljöförvaltning."""
    poster = (
        ra_data.get("values", ra_data.get("data", []))
        if isinstance(ra_data, dict)
        else ra_data
    )
    if not isinstance(poster, list):
        raise GoteborgDataFel("Luftdata måste vara en lista med timposter.")

    tider: list[str] = []
    pm10: list[float] = []
    pm25: list[float] = []
    no2: list[float] = []

    def _tal(post: dict[str, Any], *nycklar: str) -> float:
        for nyckel in nycklar:
            v = post.get(nyckel)
            if v is not None and v != "":
                try:
                    f = float(v)
                    return f if np.isfinite(f) else float("nan")
                except (TypeError, ValueError):
                    return float("nan")
        return float("nan")

    for post in poster:
        if not isinstance(post, dict):
            continue
        tid = str(post.get("time") or post.get("tid") or post.get("timestamp") or "").strip()
        if not tid:
            continue
        tider.append(tid)
        pm10.append(_tal(post, "pm10", "PM10", "pm10_ug_m3"))
        pm25.append(_tal(post, "pm25", "PM2.5", "pm25_ug_m3"))
        no2.append(_tal(post, "no2", "NO2", "no2_ug_m3"))

    return GoteborgLuftserie(
        station=station,
        tider_iso=tuple(tider),
        pm10_ug_m3=np.asarray(pm10, dtype=np.float64),
        pm25_ug_m3=np.asarray(pm25, dtype=np.float64),
        no2_ug_m3=np.asarray(no2, dtype=np.float64),
    )
