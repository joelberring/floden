"""Solposition (NOAA:s lågprecisionsalgoritm, Meeus kap. 25) för UTC-tid och SWEREF 99 18 00.

Alla vinklar i modulens publika gränssnitt anges i radianer om inte namnet säger grader.
Solhöjden är den geometriska (utan refraktion) om inte `refraktion=True` anges.
Azimut räknas från norr, medurs (N=0, Ö=pi/2, S=pi, V=3pi/2), samma konvention som
horisontprofilerna i `dagsljus.horisont`.

Noggrannhet (uppmätt mot NREL SPA via pvlib, se `docs/DAGSLJUS.md` och
`tests/dagsljus/test_solposition.py`): ett fåtal hundradels grad 1900–2100.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

import numpy as np
from numpy.typing import NDArray

from gemensamt.koordinater import EPSG_INTERN, KoordinatFel

_JD_UNIX_EPOK = 2440587.5
_JD_J2000 = 2451545.0

Falt = NDArray[np.float64]


class SolFel(Exception):
    """Fel i solpositionsberäkning (ogiltig tid eller position)."""


def julianskt_dygn(tid_utc: NDArray[np.datetime64]) -> Falt:
    """Julianskt dygn (UT) för `datetime64`-tider tolkade som UTC."""
    t = np.asarray(tid_utc)
    if not np.issubdtype(t.dtype, np.datetime64):
        raise SolFel(f"Tiden måste vara numpy datetime64 (UTC), fick dtype {t.dtype}.")
    sekunder = (t.astype("datetime64[ms]") - np.datetime64(0, "ms")).astype(np.int64) / 1000.0
    return np.asarray(sekunder / 86400.0 + _JD_UNIX_EPOK, dtype=np.float64)


def deklination_och_tidsekvation(
    jd: Falt,
) -> tuple[Falt, Falt]:
    """Solens deklination (rad) och tidsekvationen (minuter) för julianskt dygn."""
    t = (jd - _JD_J2000) / 36525.0
    l0 = np.radians((280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0)
    m = np.radians(357.52911 + t * (35999.05029 - 0.0001537 * t))
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    c = (
        np.sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + np.sin(2 * m) * (0.019993 - 0.000101 * t)
        + np.sin(3 * m) * 0.000289
    )
    sann_lon = np.degrees(l0) + c
    omega = np.radians(125.04 - 1934.136 * t)
    skenbar_lon = np.radians(sann_lon - 0.00569 - 0.00478 * np.sin(omega))
    eps0 = 23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
    eps = np.radians(eps0 + 0.00256 * np.cos(omega))
    dekl = np.arcsin(np.sin(eps) * np.sin(skenbar_lon))
    y = np.tan(eps / 2.0) ** 2
    tidsekv_min = 4.0 * np.degrees(
        y * np.sin(2 * l0)
        - 2.0 * e * np.sin(m)
        + 4.0 * e * y * np.sin(m) * np.cos(2 * l0)
        - 0.5 * y * y * np.sin(4 * l0)
        - 1.25 * e * e * np.sin(2 * m)
    )
    return np.asarray(dekl, dtype=np.float64), np.asarray(tidsekv_min, dtype=np.float64)


def solposition(
    tid_utc: NDArray[np.datetime64],
    lat_grader: float,
    lon_grader: float,
    *,
    refraktion: bool = False,
) -> tuple[Falt, Falt]:
    """Solhöjd och azimut (båda rad) för UTC-tider på en plats (WGS84-grader, öst positivt).

    Returnerar `(hojd_rad, azimut_rad)`. Azimut från norr, medurs, i [0, 2pi).
    """
    if not (math.isfinite(lat_grader) and -90.0 < lat_grader < 90.0):
        raise SolFel(f"Latitud måste ligga mellan -90 och 90 grader, fick {lat_grader}.")
    if not (math.isfinite(lon_grader) and -180.0 <= lon_grader <= 360.0):
        raise SolFel(f"Longitud ogiltig: {lon_grader}.")
    jd = julianskt_dygn(tid_utc)
    dekl, tidsekv_min = deklination_och_tidsekvation(jd)
    # UTC-minut på dygnet (JD börjar vid middag, därav +0,5).
    utc_min = ((jd + 0.5) % 1.0) * 1440.0
    sann_soltid_min = (utc_min + tidsekv_min + 4.0 * lon_grader) % 1440.0
    timvinkel = np.radians(sann_soltid_min / 4.0 - 180.0)
    lat = math.radians(lat_grader)
    sin_h = math.sin(lat) * np.sin(dekl) + math.cos(lat) * np.cos(dekl) * np.cos(timvinkel)
    hojd = np.arcsin(np.clip(sin_h, -1.0, 1.0))
    # Azimut från söder, positivt västerut; +pi ger norr-baserad medurs azimut.
    az = np.arctan2(
        np.sin(timvinkel),
        np.cos(timvinkel) * math.sin(lat) - np.tan(dekl) * math.cos(lat),
    )
    azimut = (az + math.pi) % (2.0 * math.pi)
    if refraktion:
        hojd = hojd + _refraktion_rad(hojd)
    return np.asarray(hojd, dtype=np.float64), np.asarray(azimut, dtype=np.float64)


def _refraktion_rad(hojd_rad: Falt) -> Falt:
    """Atmosfärisk refraktion (Saemundsson, 1010 hPa, 10 °C), rad. Noll under -1 grad."""
    h = np.degrees(hojd_rad)
    ok = h > -1.0
    hh = np.where(ok, h, 0.0)
    bagminuter = 1.02 / np.tan(np.radians(hh + 10.3 / (hh + 5.11)))
    return np.asarray(np.where(ok, np.radians(bagminuter / 60.0), 0.0), dtype=np.float64)


@lru_cache(maxsize=1)
def _transformer_3011_till_wgs84() -> Any:
    try:
        from pyproj import Transformer
    except ImportError as exc:  # pragma: no cover - pyproj är ett beroende i pyproject.toml
        raise KoordinatFel("Paketet 'pyproj' krävs för att få lat/lon ur EPSG:3011.") from exc
    return Transformer.from_crs(f"EPSG:{EPSG_INTERN}", "EPSG:4326", always_xy=True)


def lat_lon_fran_3011(e_m: float, n_m: float) -> tuple[float, float]:
    """(lat, lon) i WGS84-grader för en punkt i EPSG:3011 (SWEREF 99 18 00, e/n i meter)."""
    omv = _transformer_3011_till_wgs84()
    lon, lat = omv.transform(e_m, n_m)
    return float(lat), float(lon)


def dygnsfonster_utc(datum: str, lon_grader: float, steg_min: float) -> NDArray[np.datetime64]:
    """Tidpunkter (mittpunkter av steg) för ett lokalt medelsoltidsdygn på `datum` (ÅÅÅÅ-MM-DD).

    Dygnet börjar vid lokal medelsolmidnatt (00:00 UTC minus lon/15 h), så att inget
    dagsljus delas av en dygnsgräns. Antalet steg är 1440/steg_min och måste vara heltal.
    """
    if not (math.isfinite(lon_grader) and -180.0 <= lon_grader <= 180.0):
        raise SolFel(f"lon_grader måste ligga i [-180, 180], fick {lon_grader}.")
    if not (math.isfinite(steg_min) and steg_min > 0):
        raise SolFel(f"steg_min måste vara > 0, fick {steg_min}.")
    antal = 1440.0 / steg_min
    if abs(antal - round(antal)) > 1e-9:
        raise SolFel(f"1440 måste vara delbart med steg_min={steg_min}.")
    try:
        start = np.datetime64(datum, "s")
    except ValueError as exc:
        raise SolFel(f"Ogiltigt datum '{datum}', förväntade ÅÅÅÅ-MM-DD.") from exc
    start_s = start.astype(np.int64) - int(round(lon_grader / 15.0 * 3600.0))
    offs = (np.arange(int(round(antal))) + 0.5) * steg_min * 60.0
    sek = (start_s + np.round(offs)).astype(np.int64)
    return np.asarray(np.datetime64(0, "s") + sek.astype("timedelta64[s]"))
