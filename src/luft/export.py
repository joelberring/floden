# SPDX-License-Identifier: MIT
"""Utdata för luftdelen enligt kontrakt KL2/v1 (`docs/LUFT.md` 4).

| Fil | Innehåll |
| --- | --- |
| `halt_<amne>_<statistik>.tif` | halt på angiven höjd (µg/m³), EPSG:3011, float32, nodata NaN |
| `halt_<amne>_3d.npz` | 3D-fältet (g/m³) i det roterade rutnätet + `meta` (JSON) |
| `kanslighet_<receptor>.tif` | adjungerad karta, (µg/m³)/(g/s), EPSG:3011 |
| `run.json` | K0-manifest, `modul = "luft"` |

Internt räknas halter i g/m³; omvandling till µg/m³ (×1e6) sker bara här.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost, RunManifest, skapa_manifest, skriv_manifest
from luft.backend import BACKENDS
from luft.geometri import EPSG_SWEREF99_1800, RasterRuta, grid_meta
from luft.typer import KONTRAKT_KL1, LuftFel, Voxelgrid

KONTRAKT_KL2 = "KL2/v1"
G_TILL_UG = 1.0e6

FloatArray = npt.NDArray[np.float64]

_SAKERT_NAMN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")


def _kontrollera_namn(typ: str, namn: str) -> str:
    if not _SAKERT_NAMN.match(namn):
        raise LuftFel(f"Ogiltigt {typ}namn '{namn}' (tillåtet: A–Z, a–z, 0–9, _ . -).")
    return namn


def halt_filnamn(amne: str, statistik: str) -> str:
    """`halt_<amne>_<statistik>.tif`."""
    return (
        f"halt_{_kontrollera_namn('ämnes', amne)}_{_kontrollera_namn('statistik', statistik)}.tif"
    )


def halt_3d_filnamn(amne: str) -> str:
    """`halt_<amne>_3d.npz`."""
    return f"halt_{_kontrollera_namn('ämnes', amne)}_3d.npz"


def kanslighet_filnamn(receptor: str) -> str:
    """`kanslighet_<receptor>.tif`."""
    return f"kanslighet_{_kontrollera_namn('receptor', receptor)}.tif"


def skriv_geotiff_float(
    sokvag: str | Path,
    matris: npt.ArrayLike,
    ruta: RasterRuta,
    beskrivning: str | None = None,
    enhet: str | None = None,
) -> Path:
    """Skriver ett float32-raster i EPSG:3011 (nodata NaN), samma profil som K1-skrivaren."""
    import rasterio
    from rasterio.transform import Affine

    arr = np.asarray(matris, dtype=np.float32)
    if arr.shape != ruta.form_rc:
        raise LuftFel(f"Rasterform {arr.shape} matchar inte rutan {ruta.form_rc}.")
    if np.any(np.isinf(arr)):
        raise LuftFel("Rastret innehåller Inf (överflöd vid omvandling till float32?).")
    if ruta.epsg != EPSG_SWEREF99_1800:
        raise LuftFel(f"Luftresultat skrivs i EPSG:{EPSG_SWEREF99_1800}, rutan har {ruta.epsg}.")
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    rader, kolumner = ruta.form_rc
    profil: dict[str, object] = {
        "driver": "GTiff",
        "height": rader,
        "width": kolumner,
        "count": 1,
        "dtype": "float32",
        "crs": f"EPSG:{EPSG_SWEREF99_1800}",
        "transform": Affine(*ruta.gdal_transform()),
        "nodata": float("nan"),
        "compress": "deflate",
    }
    if rader >= 16 and kolumner >= 16:
        profil["tiled"] = True
        profil["blockxsize"] = 256 if kolumner >= 256 else 16 * (kolumner // 16)
        profil["blockysize"] = 256 if rader >= 256 else 16 * (rader // 16)
    with rasterio.open(mal, "w", **profil) as dst:
        dst.write(arr, 1)
        taggar: dict[str, str] = {"kontrakt": KONTRAKT_KL2}
        if enhet is not None:
            taggar["enhet"] = enhet
            dst.set_band_unit(1, enhet)
        if beskrivning is not None:
            taggar["beskrivning"] = beskrivning
            dst.set_band_description(1, beskrivning)
        dst.update_tags(**taggar)
    return mal


def skriv_halt_geotiff(
    sokvag: str | Path, halt_g_m3: npt.ArrayLike, ruta: RasterRuta, beskrivning: str | None = None
) -> Path:
    """2D-halt i g/m³ → GeoTIFF i µg/m³."""
    arr = np.asarray(halt_g_m3, dtype=np.float64)
    if np.any(arr[~np.isnan(arr)] < 0):
        raise LuftFel("Negativa halter kan inte exporteras.")
    return skriv_geotiff_float(sokvag, arr * G_TILL_UG, ruta, beskrivning, enhet="ug/m3")


def skriv_kanslighet_geotiff(
    sokvag: str | Path,
    kanslighet_g_m3_per_g_s: npt.ArrayLike,
    ruta: RasterRuta,
    beskrivning: str | None = None,
) -> Path:
    """Känslighetskarta (g/m³ per g/s) → GeoTIFF i (µg/m³)/(g/s)."""
    arr = np.asarray(kanslighet_g_m3_per_g_s, dtype=np.float64)
    return skriv_geotiff_float(sokvag, arr * G_TILL_UG, ruta, beskrivning, enhet="(ug/m3)/(g/s)")


def skriv_halt_3d(sokvag: str | Path, halt_g_m3: npt.ArrayLike, grid: Voxelgrid, amne: str) -> Path:
    """3D-halt (nx, ny, nz) i g/m³ i det roterade rutnätet → npz med `halt_g_m3`, `fast`, `meta`."""
    arr = np.asarray(halt_g_m3, dtype=np.float64)
    if arr.shape != grid.form:
        raise LuftFel(f"halt_g_m3 ska ha formen {grid.form}, fick {arr.shape}.")
    meta = {
        "kontrakt": KONTRAKT_KL2,
        "kontrakt_geometri": KONTRAKT_KL1,
        "amne": amne,
        "enhet": "g/m3",
        **grid_meta(grid),
    }
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    with mal.open("wb") as fh:
        np.savez_compressed(
            fh, halt_g_m3=arr, fast=grid.fast, meta=np.array(json.dumps(meta, ensure_ascii=False))
        )
    return mal


@dataclass
class Luftresultat:
    """Allt som ska skrivas för en körning (en eller flera statistikmått för ett ämne)."""

    amne: str
    ruta: RasterRuta
    raster_g_m3: dict[str, FloatArray] = field(default_factory=dict)  # statistik → 2D
    halt_3d_g_m3: FloatArray | None = None
    grid: Voxelgrid | None = None
    kanslighet_g_m3_per_g_s: dict[str, FloatArray] = field(default_factory=dict)  # receptor → 2D


def exportera(
    mapp: str | Path,
    resultat: Luftresultat,
    *,
    backend: str,
    parametrar: Mapping[str, Any] | None = None,
    indata: Sequence[tuple[str, str | Path]] = (),
    matvarden: Mapping[str, Any] | None = None,
    varningar: Sequence[str] = (),
    kodversion: str | None = None,
    start: str | None = None,
) -> tuple[Path, RunManifest]:
    """Skriver KL2-filerna och `run.json` (K0) i `mapp`.

    Args:
        backend: "numpy" eller "cupy"; skrivs i parametrarna.
        indata: (roll, sökväg) – filerna kontrollsummeras.
        parametrar: övriga körparametrar (läggs under ``parametrar``).

    Returns:
        (sökväg till run.json, manifestet).
    """
    if backend not in BACKENDS:
        raise LuftFel(f"Okänd backend '{backend}'.")
    rot = Path(mapp)
    rot.mkdir(parents=True, exist_ok=True)
    utdata: list[FilPost] = []

    def lagg_till(roll: str, fil: Path) -> None:
        utdata.append(FilPost(roll=roll, fil=fil.name, sha256=sha256_fil(fil)))

    for statistik, raster in resultat.raster_g_m3.items():
        fil = skriv_halt_geotiff(
            rot / halt_filnamn(resultat.amne, statistik),
            raster,
            resultat.ruta,
            beskrivning=f"{resultat.amne} {statistik}",
        )
        lagg_till(f"halt_{statistik}", fil)
    if resultat.halt_3d_g_m3 is not None:
        if resultat.grid is None:
            raise LuftFel("halt_3d_g_m3 kräver grid för att kunna exporteras.")
        fil = skriv_halt_3d(
            rot / halt_3d_filnamn(resultat.amne),
            resultat.halt_3d_g_m3,
            resultat.grid,
            resultat.amne,
        )
        lagg_till("halt_3d", fil)
    for receptor, karta in resultat.kanslighet_g_m3_per_g_s.items():
        fil = skriv_kanslighet_geotiff(
            rot / kanslighet_filnamn(receptor),
            karta,
            resultat.ruta,
            beskrivning=f"kanslighet {receptor}",
        )
        lagg_till(f"kanslighet_{receptor}", fil)

    in_poster = [FilPost(roll=roll, fil=str(Path(s)), sha256=sha256_fil(s)) for roll, s in indata]
    alla_parametrar: dict[str, Any] = {
        **(dict(parametrar) if parametrar else {}),
        "backend": backend,
        "amne": resultat.amne,
        "kontrakt": {"KL1": KONTRAKT_KL1, "KL2": KONTRAKT_KL2},
        "raster": {
            "epsg": resultat.ruta.epsg,
            "origo_x_m": resultat.ruta.origo_x_m,
            "origo_y_m": resultat.ruta.origo_y_m,
            "dx_m": resultat.ruta.dx_m,
            "rader": resultat.ruta.rader,
            "kolumner": resultat.ruta.kolumner,
        },
    }
    manifest = skapa_manifest(
        "luft",
        indata=in_poster,
        parametrar=alla_parametrar,
        utdata=utdata,
        matvarden=dict(matvarden) if matvarden else {},
        varningar=list(varningar),
        kodversion=kodversion,
        start=start,
    )
    try:
        json.dumps(manifest.till_dict(), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise LuftFel(f"Manifestet går inte att skriva som strikt JSON: {exc}") from exc
    sokvag = skriv_manifest(manifest, rot / "run.json")
    return sokvag, manifest
