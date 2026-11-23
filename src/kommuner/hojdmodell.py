"""Kommunens höjdmodell (GeoTIFF-plattor i ZIP-paket) som markhöjd för K1-rutor.

Göteborgs höjdmodell 2022: marknivå (DTM), Float32, 0,5 m, plattor om 1 x 1 km i paket om 10 x 10 km,
EPSG:3007, RH 2000. Plattorna läses direkt ur ZIP-filerna (GDAL /vsizip/), bara de som berör rutan, och
sampas om till rutans raster i EPSG:3011 (bilinjärt). Höjder är oförändrade: bara plan-koordinaterna
transformeras, så skalfelet 3007/3011 ändrar inte z.

Höjdmodellen är en markmodell (ingen DSM): byggnadshöjd kommer ur baskartans taklinjer (se
`kommuner.baskarta`), inte ur den här filen.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from kommuner.filpaket import vsizip, zip_medlemmar
from kommuner.wfs import KommunKallaFel
from markmodell.rutnat import Ruta


@dataclass(frozen=True)
class Platta:
    paket: Path
    medlem: str | None  # None: paket är själv en GeoTIFF
    bounds: tuple[float, float, float, float]  # i plattans eget system
    epsg: int

    @property
    def sokvag(self) -> str:
        return str(self.paket) if self.medlem is None else vsizip(self.paket, self.medlem)


class Hojdmodell:
    """Index över alla GeoTIFF-plattor i en eller flera ZIP-paket."""

    def __init__(self, paket: list[Path], *, epsg_antagen: int) -> None:
        import rasterio

        self.plattor: list[Platta] = []
        for p in paket:
            if p.suffix.lower() in (".tif", ".tiff"):
                kandidater: list[tuple[Path, str | None]] = [(p, None)]
            else:
                kandidater = [(p, m) for m in zip_medlemmar(p, (".tif", ".tiff"))]
            for fil, m in kandidater:
                sokv = str(fil) if m is None else vsizip(fil, m)
                try:
                    with rasterio.open(sokv) as r:
                        epsg = r.crs.to_epsg() if r.crs else epsg_antagen
                        self.plattor.append(Platta(fil, m, tuple(r.bounds), int(epsg or epsg_antagen)))
                except rasterio.errors.RasterioIOError:
                    continue  # ingen läsbar GeoTIFF (t.ex. mask): hoppas över
        if not self.plattor:
            raise KommunKallaFel(
                "Inga GeoTIFF-plattor hittades i höjdmodellens paket. Är det rätt filer "
                "(höjdmodellens ZIP-paket, inte index.shp)?"
            )
        self.epsg_antagen = epsg_antagen

    def tackning(self, bbox_3011: tuple[float, float, float, float]) -> list[Platta]:
        from rasterio.warp import transform_bounds

        ut = []
        for pl in self.plattor:
            e0, n0, e1, n1 = transform_bounds(3011, pl.epsg, *bbox_3011)
            b = pl.bounds
            if not (b[2] < e0 or b[0] > e1 or b[3] < n0 or b[1] > n1):
                ut.append(pl)
        return ut

    def grund_z(self, ruta: Ruta) -> tuple[NDArray[np.float32], list[str]]:
        """Markhöjd (RH 2000, m) för rutans yttre raster; NaN där plattor saknas. Plus varningar."""
        import rasterio
        from affine import Affine
        from rasterio.enums import Resampling
        from rasterio.warp import reproject

        bbox = ruta.yttre_bbox
        plattor = self.tackning(bbox)
        if not plattor:
            raise KommunKallaFel(
                f"Höjdmodellen täcker inte {ruta.namn} (E {bbox[0]:.0f}-{bbox[2]:.0f}, N {bbox[1]:.0f}-{bbox[3]:.0f}, "
                "EPSG:3011). Fler paket kan behövas (--hojdmodell-url/--hojdmodell-fil)."
            )
        a = ruta.affine_parametrar
        dst_transform = Affine(*a)
        ut = np.full(ruta.form_rc, np.nan, dtype=np.float32)
        for pl in sorted(plattor, key=lambda p: (p.bounds[1], p.bounds[0])):
            buf = np.full(ruta.form_rc, np.nan, dtype=np.float32)
            with rasterio.open(pl.sokvag) as r:
                reproject(
                    source=rasterio.band(r, 1),
                    destination=buf,
                    dst_transform=dst_transform,
                    dst_crs="EPSG:3011",
                    src_nodata=r.nodata,
                    dst_nodata=float("nan"),
                    resampling=Resampling.bilinear,
                )
            tom = np.isnan(ut)
            ut[tom] = buf[tom]
        varn: list[str] = []
        saknas = int(np.isnan(ut).sum())
        if saknas:
            varn.append(
                f"{ruta.namn}: höjdmodellen saknar värde i {100.0 * saknas / ut.size:.1f} % av cellerna "
                "(utanför plattorna eller vatten/nodata); de är NaN i markmodellen."
            )
        if saknas == ut.size:
            raise KommunKallaFel(f"Höjdmodellen gav inga värden alls för {ruta.namn}.")
        return ut, varn


def hojdmodell_for(paket: list[Path], epsg: int) -> Hojdmodell:
    return Hojdmodell(paket, epsg_antagen=epsg)



def skriv_klippt_hojd(hoj: Hojdmodell, extent_3011: tuple[float, float, float, float], upplosning_m: float, ut: Path) -> list[str]:
    """Klipper höjdmodellen till `extent_3011` (EPSG:3011, `upplosning_m`) och skriver en GeoTIFF.

    Den klippta filen följer med geografins rådata så att projekt kan beskäras utan nytt nedladdat paket.
    """
    import math

    import rasterio
    from rasterio.transform import from_origin

    e0, n0, e1, n1 = extent_3011
    storlek = math.ceil(max(e1 - e0, n1 - n0) / upplosning_m) * upplosning_m
    ruta = Ruta(float(e0), float(n0), storlek, 0.0, upplosning_m)
    z, varn = hoj.grund_z(ruta)
    ut.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        ut, "w", driver="GTiff", height=z.shape[0], width=z.shape[1], count=1, dtype="float32",
        crs="EPSG:3011", transform=from_origin(e0, n0 + storlek, upplosning_m, upplosning_m),
        nodata=float("nan"), compress="deflate",
    ) as d:  # fmt: skip
        d.write(z, 1)
    return varn
