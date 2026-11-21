"""Gemensamt läs-API för K1-rutor (v1 och v2).

Alla funktioner tar en ruta som katalog (`str`/`Path`) eller ett objekt med attributet `mapp`
(t.ex. `karna.rutscenario.Ruta`). Höjder är RH 2000, koordinater EPSG:3011.

- `las_byggnader` – byggnadsregistret (`byggnader.gpkg`).
- `las_terrang` – marken utan byggnader (`terrang.tif`).
- `las_ytmodell` – DSM: terräng, byggnadstak (platta på `takhojd_rh2000`) och valfritt vegetation.
  Till skillnad från `mark.tif` brinner inga portar/trummor in: en port genom ett hus är ingen
  öppning i dagsljus eller buller.
- `las_vegetation` – vegetationshöjd över mark (grövre rutnät än marken), eller `None`.

En v1-ruta (inga `byggnader.gpkg`/`terrang.tif`) läses fortfarande: `las_mark`, `las_markslag`,
`las_osakerhet` fungerar alltid. Är v1-rutan helt utan byggnadsceller (markslag 9) är marken
lika med `mark.tif` och registret tomt; annars ger `las_terrang`/`las_byggnader` ett tydligt
fel i stället för en gissning.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from karna.geometri.rasterfil import (
    GeometriFel,
    las_float_raster,
    las_raster,
    ruta_fran_geo,
)
from markmodell.byggnadsregister import ByggnadsPost, las_byggnader_gpkg
from markmodell.hinder_oppningar import BYGGNAD, brann_in_byggnader
from testsvit.k1_io import GeoTransformInfo

K1_FILER_V2: tuple[str, ...] = ("terrang.tif", "byggnader.gpkg")


class _HarMapp(Protocol):
    mapp: Path


RutaLike = str | Path | _HarMapp


def ruta_mapp(ruta: RutaLike) -> Path:
    if isinstance(ruta, str | Path):
        return Path(ruta)
    return Path(ruta.mapp)


def k1_version(ruta: RutaLike) -> int:
    """1 för en v1-ruta, 2 om `byggnader.gpkg` finns. `byggnader.gpkg` utan `terrang.tif` är fel."""
    mapp = ruta_mapp(ruta)
    if (mapp / "byggnader.gpkg").is_file():
        if not (mapp / "terrang.tif").is_file():
            raise GeometriFel(f"{mapp}: byggnader.gpkg utan terrang.tif (trasig K1 v2).")
        return 2
    return 1


def las_mark(ruta: RutaLike) -> tuple[NDArray[np.float32], GeoTransformInfo]:
    """`mark.tif`: marken inklusive byggnader som hinder."""
    return las_float_raster(ruta_mapp(ruta) / "mark.tif")


def las_osakerhet(ruta: RutaLike) -> tuple[NDArray[np.float32], GeoTransformInfo]:
    return las_float_raster(ruta_mapp(ruta) / "osakerhet.tif")


def las_markslag(ruta: RutaLike) -> tuple[NDArray[np.uint8], GeoTransformInfo]:
    data, geo = las_raster(ruta_mapp(ruta) / "markslag.tif")
    return np.asarray(data, dtype=np.uint8), geo


def _v1_utan_byggnader(mapp: Path) -> bool:
    slag, _ = las_raster(mapp / "markslag.tif")
    return not bool(np.any(slag == BYGGNAD))


def _kravs_v2(mapp: Path, vad: str) -> None:
    if not _v1_utan_byggnader(mapp):
        raise GeometriFel(
            f"{mapp.name}: K1 v1 saknar {vad} och rutan har byggnadsceller. "
            "Bygg om rutan med K1 v2 (markmodellen skriver terrang.tif och byggnader.gpkg)."
        )


def las_byggnader(ruta: RutaLike) -> tuple[ByggnadsPost, ...]:
    """Byggnadsregistret sorterat på `byggnad_id`. Tomt för en v1-ruta utan byggnadsceller."""
    mapp = ruta_mapp(ruta)
    if k1_version(mapp) == 2:
        return las_byggnader_gpkg(mapp / "byggnader.gpkg")
    _kravs_v2(mapp, "byggnadsregister")
    return ()


def las_terrang(ruta: RutaLike) -> tuple[NDArray[np.float32], GeoTransformInfo]:
    """`terrang.tif`: marken utan byggnader (med kantstenar och andra brytlinjer)."""
    mapp = ruta_mapp(ruta)
    if k1_version(mapp) == 2:
        return las_float_raster(mapp / "terrang.tif")
    _kravs_v2(mapp, "terrang.tif")
    return las_float_raster(mapp / "mark.tif")


def las_vegetation(ruta: RutaLike) -> tuple[NDArray[np.float32], GeoTransformInfo] | None:
    """Vegetationshöjd över mark (m, 0 = ingen) på vegetationsrutnätet; `None` om filen saknas."""
    fil = ruta_mapp(ruta) / "vegetation.tif"
    if not fil.is_file():
        return None
    return las_float_raster(fil)


def vegetation_pa_marknat(
    veg: NDArray[np.float32], veg_geo: GeoTransformInfo, mark_geo: GeoTransformInfo
) -> NDArray[np.float32]:
    """Uppsamlar vegetationsrastret (närmaste granne) till markrutnätet."""
    f = round(veg_geo.pixelstorlek_x / mark_geo.pixelstorlek_x)
    if (
        f < 1
        or abs(f * mark_geo.pixelstorlek_x - veg_geo.pixelstorlek_x) > 1e-9
        or veg.shape[0] * f != mark_geo.hojd
        or veg.shape[1] * f != mark_geo.bredd
        or abs(veg_geo.x_min - mark_geo.x_min) > 1e-6
        or abs(veg_geo.y_max - mark_geo.y_max) > 1e-6
    ):
        raise GeometriFel("Vegetationsrastret passar inte markrutnätet (utsträckning/upplösning).")
    return np.asarray(np.repeat(np.repeat(veg, f, axis=0), f, axis=1), dtype=np.float32)


def ytmodell_fran(
    terrang: NDArray[np.float32],
    geo: GeoTransformInfo,
    byggnader: tuple[ByggnadsPost, ...],
    vegetation_markrutnat: NDArray[np.float32] | None = None,
) -> NDArray[np.float32]:
    """DSM = max(terräng, byggnadstak i polygonerna, terräng + vegetationshöjd)."""
    mm = ruta_fran_geo(geo)
    dsm, _ = brann_in_byggnader(
        terrang,
        np.zeros(terrang.shape, dtype=np.uint8),
        mm,
        [b.till_byggnad() for b in byggnader],
    )
    if vegetation_markrutnat is not None:
        dsm = np.maximum(dsm, terrang + np.maximum(vegetation_markrutnat, np.float32(0.0)))
    return np.asarray(dsm, dtype=np.float32)


def las_ytmodell(
    ruta: RutaLike,
    *,
    vegetation: bool = False,
    tillat_saknad_vegetation: bool = False,
    utan_hojdkalla: frozenset[str] = frozenset(),
) -> tuple[NDArray[np.float32], GeoTransformInfo]:
    """Ytmodell (DSM) på markrutnätet. Med `vegetation=True` ingår `vegetation.tif`.

    Saknas vegetationsfilen ger `vegetation=True` fel, om inte `tillat_saknad_vegetation`
    (då antas ingen vegetation, vilket är en antagen nolla och inte en mätning).

    `utan_hojdkalla` utelämnar byggnader vars `hojdkalla` finns i mängden (t.ex. `{"standard"}`
    för en känslighetskörning utan byggnader med gissad höjd). Standard: alla byggnader ingår.
    """
    terrang, geo = las_terrang(ruta)
    byggnader = tuple(b for b in las_byggnader(ruta) if b.hojdkalla not in utan_hojdkalla)
    veg_mark: NDArray[np.float32] | None = None
    if vegetation:
        veg = las_vegetation(ruta)
        if veg is None:
            if not tillat_saknad_vegetation:
                raise GeometriFel(
                    f"{ruta_mapp(ruta).name} saknar vegetation.tif; "
                    "ange tillat_saknad_vegetation=True för att anta ingen vegetation."
                )
        else:
            veg_mark = vegetation_pa_marknat(veg[0], veg[1], geo)
    return ytmodell_fran(terrang, geo, byggnader, veg_mark), geo


def alla_byggnader(rutor: list[Any] | tuple[Any, ...]) -> dict[str, ByggnadsPost]:
    """Register för flera rutor; samma `byggnad_id` slås ihop (första rutan i listan vinner)."""
    ut: dict[str, ByggnadsPost] = {}
    for r in rutor:
        for b in las_byggnader(r):
            ut.setdefault(b.byggnad_id, b)
    return ut
