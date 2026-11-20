"""Koordinattransformationer till intern standard (SWEREF 99 18 00 / EPSG:3011)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, overload

EPSG_INTERN = 3011
EPSG_LANTMATERIET = 3006
HOJDSYSTEM = "RH 2000"


class KoordinatFel(Exception):
    """Fel vid koordinathantering eller saknat transformeringsbibliotek."""


def _validera_epsg(fran_epsg: int) -> int:
    """Validerar att EPSG-koden är ett positivt heltal (ej bool)."""
    if isinstance(fran_epsg, bool) or not isinstance(fran_epsg, int) or fran_epsg <= 0:
        raise KoordinatFel(f"Ogiltig EPSG-kod: {fran_epsg!r}. Måste vara ett positivt heltal.")
    return fran_epsg


def _identitetstransformation(x: Any, y: Any) -> tuple[Any, Any]:
    """Returnerar koordinater oförändrade när källsystemet redan är EPSG:3011."""
    return x, y


def skapa_transformator_till_3011(fran_epsg: int) -> Callable[[Any, Any], tuple[Any, Any]]:
    """Skapar en funktion `(x, y) -> (e_3011, n_3011)` till EPSG:3011.

    Om `fran_epsg == 3011` returneras en identitetstransformation direkt utan att
    `pyproj` behöver importeras. För övriga EPSG-koder importeras `pyproj` latent.

    Raises:
        KoordinatFel: Om `fran_epsg` är ogiltig eller `pyproj` saknas.
    """
    epsg = _validera_epsg(fran_epsg)
    if epsg == EPSG_INTERN:
        return _identitetstransformation

    try:
        from pyproj import Transformer
    except ImportError as exc:
        raise KoordinatFel(
            "Paketet 'pyproj' krävs för koordinattransformation från "
            f"EPSG:{epsg} till EPSG:{EPSG_INTERN}."
        ) from exc

    transformer = Transformer.from_crs(
        f"EPSG:{epsg}",
        f"EPSG:{EPSG_INTERN}",
        always_xy=True,
    )

    def _transformera(x: Any, y: Any) -> tuple[Any, Any]:
        e_3011, n_3011 = transformer.transform(x, y)
        return e_3011, n_3011

    return _transformera


@overload
def transformera_till_3011(x: float, y: float, fran_epsg: int) -> tuple[float, float]: ...


@overload
def transformera_till_3011(x: Any, y: Any, fran_epsg: int) -> tuple[Any, Any]: ...


def transformera_till_3011(x: Any, y: Any, fran_epsg: int) -> tuple[Any, Any]:
    """Transformerar koordinater `(x, y)` från `fran_epsg` till SWEREF 99 18 00 (EPSG:3011).

    För `fran_epsg == 3011` returneras koordinaterna direkt utan onödig transformering.
    För övriga koordinatsystem används `pyproj.Transformer(..., always_xy=True)`.
    """
    transformator = skapa_transformator_till_3011(fran_epsg)
    return transformator(x, y)
