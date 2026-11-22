"""Koordinattransformationer för alla svenska SWEREF 99-zoner (EPSG:3006–3018).

Standard i befintliga Stockholmsprojekt är SWEREF 99 18 00 (EPSG:3011), men
`skapa_transformator`, `transformera` och `valj_sweref99_zon` stöder alla 13
officiella SWEREF 99-zoner så att hela Sverige (t.ex. Göteborg EPSG:3007,
Malmö EPSG:3008, Stockholm EPSG:3011, nationellt EPSG:3006) får exakt metrisk skala.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, overload

EPSG_INTERN = 3011
EPSG_LANTMATERIET = 3006
HOJDSYSTEM = "RH 2000"


@dataclass(frozen=True)
class Sweref99Zon:
    """Parametrar för en officiell SWEREF 99-zon (GRS 80, Gauss–Krüger)."""

    epsg: int
    namn: str
    mittmeridian_grader: float
    skalfaktor: float
    falsk_ost_m: float
    lat_min_grader: float
    lat_max_grader: float


# Alla 13 officiella svenska SWEREF 99-zoner (Lantmäteriet)
SWEREF99_ZONER: dict[int, Sweref99Zon] = {
    3006: Sweref99Zon(3006, "SWEREF 99 TM", 15.0, 0.9996, 500_000.0, 55.0, 69.5),
    3007: Sweref99Zon(3007, "SWEREF 99 12 00", 12.0, 1.0, 150_000.0, 55.0, 61.5),
    3008: Sweref99Zon(3008, "SWEREF 99 13 30", 13.5, 1.0, 150_000.0, 55.0, 61.5),
    3009: Sweref99Zon(3009, "SWEREF 99 15 00", 15.0, 1.0, 150_000.0, 55.0, 61.5),
    3010: Sweref99Zon(3010, "SWEREF 99 16 30", 16.5, 1.0, 150_000.0, 55.0, 61.5),
    3011: Sweref99Zon(3011, "SWEREF 99 18 00", 18.0, 1.0, 150_000.0, 55.0, 61.5),
    3012: Sweref99Zon(3012, "SWEREF 99 14 15", 14.25, 1.0, 150_000.0, 61.5, 69.5),
    3013: Sweref99Zon(3013, "SWEREF 99 15 45", 15.75, 1.0, 150_000.0, 61.5, 69.5),
    3014: Sweref99Zon(3014, "SWEREF 99 17 15", 17.25, 1.0, 150_000.0, 61.5, 69.5),
    3015: Sweref99Zon(3015, "SWEREF 99 18 45", 18.75, 1.0, 150_000.0, 61.5, 69.5),
    3016: Sweref99Zon(3016, "SWEREF 99 20 15", 20.25, 1.0, 150_000.0, 61.5, 69.5),
    3017: Sweref99Zon(3017, "SWEREF 99 21 45", 21.75, 1.0, 150_000.0, 61.5, 69.5),
    3018: Sweref99Zon(3018, "SWEREF 99 23 15", 23.25, 1.0, 150_000.0, 61.5, 69.5),
}


def valj_sweref99_zon(lon_grader: float, lat_grader: float) -> Sweref99Zon:
    """Väljer den lokala SWEREF 99-zon (EPSG:3007–3018) som har närmast mittmeridian."""
    kandidater = [
        z
        for epsg, z in SWEREF99_ZONER.items()
        if epsg != EPSG_LANTMATERIET and z.lat_min_grader <= lat_grader <= z.lat_max_grader
    ]
    if not kandidater:
        # Utanför Sveriges latitudband: välj närmaste lokala zon i longitud
        kandidater = [z for epsg, z in SWEREF99_ZONER.items() if epsg != EPSG_LANTMATERIET]
    return min(kandidater, key=lambda z: abs(z.mittmeridian_grader - lon_grader))


class KoordinatFel(Exception):
    """Fel vid koordinathantering eller saknat transformeringsbibliotek."""


def _validera_epsg(fran_epsg: int) -> int:
    """Validerar att EPSG-koden är ett positivt heltal (ej bool)."""
    if isinstance(fran_epsg, bool) or not isinstance(fran_epsg, int) or fran_epsg <= 0:
        raise KoordinatFel(f"Ogiltig EPSG-kod: {fran_epsg!r}. Måste vara ett positivt heltal.")
    return fran_epsg


def _identitetstransformation(x: Any, y: Any) -> tuple[Any, Any]:
    """Returnerar koordinater oförändrade när käll- och målsystem är identiska."""
    return x, y


@lru_cache(maxsize=64)
def _cachad_pyproj_transformer(fran_epsg: int, till_epsg: int) -> Any:
    try:
        from pyproj import Transformer
    except ImportError as exc:
        raise KoordinatFel(
            "Paketet 'pyproj' krävs för koordinattransformation från "
            f"EPSG:{fran_epsg} till EPSG:{till_epsg}."
        ) from exc
    return Transformer.from_crs(f"EPSG:{fran_epsg}", f"EPSG:{till_epsg}", always_xy=True)


def skapa_transformator(
    fran_epsg: int, till_epsg: int = EPSG_INTERN
) -> Callable[[Any, Any], tuple[Any, Any]]:
    """Skapar en cachad transformator `(x, y) -> (e, n)` mellan valfria EPSG-koder."""
    f_epsg = _validera_epsg(fran_epsg)
    t_epsg = _validera_epsg(till_epsg)
    if f_epsg == t_epsg:
        return _identitetstransformation
    transformer = _cachad_pyproj_transformer(f_epsg, t_epsg)

    def _transformera(x: Any, y: Any) -> tuple[Any, Any]:
        e_ut, n_ut = transformer.transform(x, y)
        return e_ut, n_ut

    return _transformera


def skapa_transformator_till_3011(fran_epsg: int) -> Callable[[Any, Any], tuple[Any, Any]]:
    """Skapar en funktion `(x, y) -> (e_3011, n_3011)` till EPSG:3011."""
    return skapa_transformator(fran_epsg, EPSG_INTERN)


@overload
def transformera(
    x: float, y: float, fran_epsg: int, till_epsg: int = EPSG_INTERN
) -> tuple[float, float]: ...


@overload
def transformera(
    x: Any, y: Any, fran_epsg: int, till_epsg: int = EPSG_INTERN
) -> tuple[Any, Any]: ...


def transformera(
    x: Any, y: Any, fran_epsg: int, till_epsg: int = EPSG_INTERN
) -> tuple[Any, Any]:
    """Transformerar koordinater `(x, y)` från `fran_epsg` till `till_epsg` (standard 3011)."""
    return skapa_transformator(fran_epsg, till_epsg)(x, y)


@overload
def transformera_till_3011(x: float, y: float, fran_epsg: int) -> tuple[float, float]: ...


@overload
def transformera_till_3011(x: Any, y: Any, fran_epsg: int) -> tuple[Any, Any]: ...


def transformera_till_3011(x: Any, y: Any, fran_epsg: int) -> tuple[Any, Any]:
    """Transformerar koordinater `(x, y)` från `fran_epsg` till SWEREF 99 18 00 (EPSG:3011)."""
    return transformera(x, y, fran_epsg, EPSG_INTERN)
