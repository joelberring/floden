# ruff: noqa: E501
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
from typing import Any

import numpy as np
from numpy.typing import NDArray

from kommuner.filpaket import vsizip, zip_medlemmar
from kommuner.wfs import KommunKallaFel
from markmodell.rutnat import Ruta

FYLL_MAX_M = 150.0
"""Längsta avstånd (m) från giltiga celler som ett sammanhängande hål i höjdmodellen får fyllas."""

NAN_VARNING_TROSKEL_ANDEL = 0.20
"""Förvald tröskel (andel av rutans celler) då hög andel NaN flaggas och varnas i manifestet."""

# Klasskoder för cellvis diagnos av höjdmodellens NaN-luckor
LUCK_GILTIG = 0
LUCK_UTANFOR_PLATTOR = 1
LUCK_VATTENYTA = 2
LUCK_STORT_HAL = 3
LUCK_SMAHAL_FYLLT = 4
LUCK_SMAHAL_EJ_FYLLT = 5


@dataclass(frozen=True)
class HojdLuckDiagnos:
    """Deterministisk diagnos av NaN-luckor för en höjdmodellruta."""

    ruta: str
    totalt_celler: int
    nan_fore_celler: int
    nan_efter_celler: int
    utanfor_plattor_celler: int
    vattenyta_celler: int
    stort_hal_celler: int
    smahal_fyllda_celler: int
    smahal_ej_fyllda_celler: int
    max_hal_avstand_m: float
    fyll_max_m: float = FYLL_MAX_M
    nan_troskel_andel: float = NAN_VARNING_TROSKEL_ANDEL

    @property
    def nan_fore_andel(self) -> float:
        return self.nan_fore_celler / self.totalt_celler if self.totalt_celler > 0 else 0.0

    @property
    def nan_efter_andel(self) -> float:
        return self.nan_efter_celler / self.totalt_celler if self.totalt_celler > 0 else 0.0

    @property
    def over_troskel(self) -> bool:
        return self.nan_efter_andel > self.nan_troskel_andel

    @property
    def huvudsaklig_orsak(self) -> str:
        """Tydlig orsakskod för kvarvarande (eller ifyllda) luckor i rutan."""
        if self.nan_efter_celler == 0:
            return "fyllda_smahal" if self.smahal_fyllda_celler > 0 else "inga_luckor"
        orsaker = [
            ("utanfor_plattor", self.utanfor_plattor_celler),
            ("vattenyta", self.vattenyta_celler),
            ("stort_hal", self.stort_hal_celler),
            ("smahal_ej_fyllt", self.smahal_ej_fyllda_celler),
        ]
        orsaker.sort(key=lambda x: (-x[1], x[0]))
        return orsaker[0][0]

    def till_dict(self) -> dict[str, Any]:
        return {
            "ruta": self.ruta,
            "totalt_celler": self.totalt_celler,
            "nan_fore_andel": round(self.nan_fore_andel, 6),
            "nan_efter_andel": round(self.nan_efter_andel, 6),
            "nan_fore_procent": round(100.0 * self.nan_fore_andel, 2),
            "nan_efter_procent": round(100.0 * self.nan_efter_andel, 2),
            "utanfor_plattor_celler": self.utanfor_plattor_celler,
            "vattenyta_celler": self.vattenyta_celler,
            "stort_hal_celler": self.stort_hal_celler,
            "smahal_fyllda_celler": self.smahal_fyllda_celler,
            "smahal_ej_fyllda_celler": self.smahal_ej_fyllda_celler,
            "max_hal_avstand_m": round(self.max_hal_avstand_m, 2),
            "huvudsaklig_orsak": self.huvudsaklig_orsak,
            "over_troskel": self.over_troskel,
        }


def klassificera_och_fyll_luckor(
    z_ra: NDArray[np.float32],
    tackt: NDArray[np.bool_],
    *,
    pixelstorlek_m: float,
    ruta_namn: str = "",
    vatten_mask: NDArray[np.bool_] | None = None,
    fyll_max_m: float = FYLL_MAX_M,
    fyll_smahal: bool = True,
    nan_varning_andel: float = NAN_VARNING_TROSKEL_ANDEL,
) -> tuple[NDArray[np.float32], NDArray[np.uint8], HojdLuckDiagnos, list[str]]:
    """Klassificerar NaN-celler rent vektoriserat (NumPy/SciPy) och fyller bara småhål.

    Klasser:
    - `LUCK_UTANFOR_PLATTOR`: utanför plattornas täckning (fylls aldrig).
    - `LUCK_VATTENYTA`: känd vattenyta enligt `vatten_mask` (fylls aldrig).
    - `LUCK_STORT_HAL`: sammanhängande NaN-hål inom plattorna där största avståndet till närmaste
      giltiga cell överstiger `fyll_max_m` (fylls aldrig — undviker artificiella trappsteg över
      stora vattenytor och saknade laserpartier).
    - `LUCK_SMAHAL_FYLLT` / `LUCK_SMAHAL_EJ_FYLLT`: små hål inom `fyll_max_m` (t.ex. under
      byggnader) som får interpoleras från omgivande giltiga celler när `fyll_smahal=True`.
    """
    ut = np.array(z_ra, dtype=np.float32, copy=True)
    tackt_b = np.asarray(tackt, dtype=bool)
    if ut.shape != tackt_b.shape:
        raise KommunKallaFel(f"z_ra {ut.shape} och tackt {tackt_b.shape} måste ha samma form.")
    if vatten_mask is not None and np.asarray(vatten_mask).shape != ut.shape:
        raise KommunKallaFel(
            f"vatten_mask {np.asarray(vatten_mask).shape} matchar inte z_ra {ut.shape}."
        )
    steg = max(abs(float(pixelstorlek_m)), 1e-9)
    klass = np.zeros(ut.shape, dtype=np.uint8)
    nan_ra = np.isnan(ut)
    totalt = int(ut.size)
    nan_fore = int(np.count_nonzero(nan_ra))

    if nan_fore == 0:
        diagnos = HojdLuckDiagnos(
            ruta=ruta_namn,
            totalt_celler=totalt,
            nan_fore_celler=0,
            nan_efter_celler=0,
            utanfor_plattor_celler=0,
            vattenyta_celler=0,
            stort_hal_celler=0,
            smahal_fyllda_celler=0,
            smahal_ej_fyllda_celler=0,
            max_hal_avstand_m=0.0,
            fyll_max_m=fyll_max_m,
            nan_troskel_andel=nan_varning_andel,
        )
        return ut, klass, diagnos, []

    utanfor = nan_ra & (~tackt_b)
    klass[utanfor] = np.uint8(LUCK_UTANFOR_PLATTOR)

    if vatten_mask is not None:
        vatten_nan = nan_ra & tackt_b & np.asarray(vatten_mask, dtype=bool)
    else:
        vatten_nan = np.zeros(ut.shape, dtype=bool)
    klass[vatten_nan] = np.uint8(LUCK_VATTENYTA)

    kand_hal = nan_ra & tackt_b & (~vatten_nan)
    har_giltiga = bool(nan_fore < totalt)
    stort_hal = np.zeros(ut.shape, dtype=bool)
    smahal = np.zeros(ut.shape, dtype=bool)
    max_hal_avstand_m = 0.0

    if bool(np.any(kand_hal)):
        if not har_giltiga:
            stort_hal = kand_hal
            max_hal_avstand_m = float(max(ut.shape) * steg)
        else:
            from scipy import ndimage

            dist_m = np.asarray(
                ndimage.distance_transform_edt(nan_ra, sampling=(steg, steg)),
                dtype=np.float64,
            )
            max_hal_avstand_m = float(np.max(dist_m[kand_hal]))
            komp, n_komp = ndimage.label(kand_hal)
            if n_komp > 0:
                komp_max = np.atleast_1d(
                    np.asarray(
                        ndimage.maximum(dist_m, komp, index=np.arange(1, n_komp + 1)),
                        dtype=np.float64,
                    )
                )
                ar_stort = np.zeros(n_komp + 1, dtype=bool)
                ar_stort[1:] = komp_max > float(fyll_max_m)
                stort_hal = ar_stort[komp]
                smahal = kand_hal & (~stort_hal)

    klass[stort_hal] = np.uint8(LUCK_STORT_HAL)

    if fyll_smahal and bool(np.any(smahal)) and har_giltiga:
        from rasterio.fill import fillnodata

        fylld = fillnodata(
            np.where(nan_ra, -9999.0, ut).astype(np.float32),
            mask=(~nan_ra).astype(np.uint8),
            max_search_distance=max(1.0, float(fyll_max_m) / steg + 2.0),
            smoothing_iterations=0,
        )
        ifylld = smahal & np.isfinite(fylld) & (fylld > -9000.0)
        ut = np.where(ifylld, fylld, ut).astype(np.float32)
        smahal_ej_fyllt = smahal & (~ifylld)
        klass[ifylld] = np.uint8(LUCK_SMAHAL_FYLLT)
        klass[smahal_ej_fyllt] = np.uint8(LUCK_SMAHAL_EJ_FYLLT)
    else:
        ifylld = np.zeros(ut.shape, dtype=bool)
        smahal_ej_fyllt = smahal
        klass[smahal_ej_fyllt] = np.uint8(LUCK_SMAHAL_EJ_FYLLT)

    n_utanfor = int(np.count_nonzero(utanfor))
    n_vatten = int(np.count_nonzero(vatten_nan))
    n_stort = int(np.count_nonzero(stort_hal))
    n_fyllt = int(np.count_nonzero(ifylld))
    n_ej_fyllt = int(np.count_nonzero(smahal_ej_fyllt))
    nan_efter = int(np.count_nonzero(np.isnan(ut)))

    diagnos = HojdLuckDiagnos(
        ruta=ruta_namn,
        totalt_celler=totalt,
        nan_fore_celler=nan_fore,
        nan_efter_celler=nan_efter,
        utanfor_plattor_celler=n_utanfor,
        vattenyta_celler=n_vatten,
        stort_hal_celler=n_stort,
        smahal_fyllda_celler=n_fyllt,
        smahal_ej_fyllda_celler=n_ej_fyllt,
        max_hal_avstand_m=max_hal_avstand_m,
        fyll_max_m=fyll_max_m,
        nan_troskel_andel=nan_varning_andel,
    )

    prefix = f"{ruta_namn}: " if ruta_namn else ""
    varn: list[str] = []
    if n_fyllt > 0:
        varn.append(
            f"{prefix}{100.0 * n_fyllt / totalt:.1f} % av cellerna saknade värde i "
            "höjdmodellen (småhål, t.ex. under byggnader) och fick interpolerad markhöjd från kanterna."
        )
    if nan_efter > 0:
        delar: list[str] = []
        if n_utanfor > 0:
            delar.append(f"utanför plattornas täckning: {100.0 * n_utanfor / totalt:.1f} %")
        if n_vatten > 0:
            delar.append(f"vattenyta: {100.0 * n_vatten / totalt:.1f} %")
        if n_stort > 0:
            delar.append(f"stort hål (>{fyll_max_m:g} m): {100.0 * n_stort / totalt:.1f} %")
        if n_ej_fyllt > 0:
            delar.append(f"småhål ej ifyllda: {100.0 * n_ej_fyllt / totalt:.1f} %")
        orsak_text = ", ".join(delar) if delar else "okänd orsak"
        varn.append(
            f"{prefix}höjdmodellen saknar värde i {100.0 * nan_efter / totalt:.1f} % av cellerna "
            f"({orsak_text}); de är NaN i markmodellen."
        )
    if diagnos.over_troskel:
        varn.append(
            f"{prefix}hög andel NaN i höjdmodellen ({100.0 * diagnos.nan_efter_andel:.1f} % > "
            f"tröskel {100.0 * nan_varning_andel:.0f} %, orsak: {diagnos.huvudsaklig_orsak})."
        )
    return ut, klass, diagnos, varn


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

    def __init__(
        self,
        paket: list[Path],
        *,
        epsg_antagen: int,
        fyll_smahal: bool = True,
        fyll_max_m: float = FYLL_MAX_M,
        nan_varning_andel: float = NAN_VARNING_TROSKEL_ANDEL,
    ) -> None:
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
        self.fyll_smahal = fyll_smahal
        self.fyll_max_m = float(fyll_max_m)
        self.nan_varning_andel = float(nan_varning_andel)

    def tackning(self, bbox_3011: tuple[float, float, float, float]) -> list[Platta]:
        from rasterio.warp import transform_bounds

        ut = []
        for pl in self.plattor:
            e0, n0, e1, n1 = transform_bounds(3011, pl.epsg, *bbox_3011)
            b = pl.bounds
            if not (b[2] < e0 or b[0] > e1 or b[3] < n0 or b[1] > n1):
                ut.append(pl)
        return ut

    def grund_z_med_diagnos(
        self,
        ruta: Ruta,
        *,
        vatten_mask: NDArray[np.bool_] | None = None,
        fyll_max_m: float | None = None,
        fyll_smahal: bool | None = None,
        nan_varning_andel: float | None = None,
    ) -> tuple[NDArray[np.float32], list[str], HojdLuckDiagnos]:
        """Markhöjd (RH 2000, m), varningar samt klassificerad luckdiagnos för `ruta`."""
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
        tackt = np.zeros(ruta.form_rc, dtype=bool)  # celler inom någon plattas yttergräns (oavsett nodata)
        for pl in sorted(plattor, key=lambda p: (p.bounds[1], p.bounds[0])):
            buf = np.full(ruta.form_rc, np.nan, dtype=np.float32)
            ettor = np.zeros(ruta.form_rc, dtype=np.float32)
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
                reproject(
                    source=np.ones((r.height, r.width), dtype=np.float32),
                    destination=ettor,
                    src_transform=r.transform,
                    src_crs=r.crs if r.crs is not None else f"EPSG:{pl.epsg}",
                    dst_transform=dst_transform,
                    dst_crs="EPSG:3011",
                    src_nodata=0.0,
                    dst_nodata=0.0,
                    resampling=Resampling.nearest,
                )
            tackt |= ettor > 0.5
            tom = np.isnan(ut)
            ut[tom] = buf[tom]

        ut, _, diagnos, varn = klassificera_och_fyll_luckor(
            ut,
            tackt,
            pixelstorlek_m=max(abs(float(a[0])), 1e-9),
            ruta_namn=ruta.namn,
            vatten_mask=vatten_mask,
            fyll_max_m=self.fyll_max_m if fyll_max_m is None else float(fyll_max_m),
            fyll_smahal=self.fyll_smahal if fyll_smahal is None else bool(fyll_smahal),
            nan_varning_andel=(
                self.nan_varning_andel if nan_varning_andel is None else float(nan_varning_andel)
            ),
        )
        if diagnos.nan_efter_celler == ut.size:
            raise KommunKallaFel(f"Höjdmodellen gav inga värden alls för {ruta.namn}.")
        return ut, varn, diagnos

    def grund_z(
        self,
        ruta: Ruta,
        *,
        vatten_mask: NDArray[np.bool_] | None = None,
    ) -> tuple[NDArray[np.float32], list[str]]:
        """Markhöjd (RH 2000, m) för rutans yttre raster; NaN där plattor/stora hål saknas. Plus varningar."""
        ut, varn, _ = self.grund_z_med_diagnos(ruta, vatten_mask=vatten_mask)
        return ut, varn


def hojdmodell_for(paket: list[Path], epsg: int) -> Hojdmodell:
    return Hojdmodell(paket, epsg_antagen=epsg)


def skriv_klippt_hojd(
    hoj: Hojdmodell,
    extent_3011: tuple[float, float, float, float],
    upplosning_m: float,
    ut: Path,
    *,
    nan_varning_andel: float | None = None,
) -> list[str]:
    """Klipper höjdmodellen till `extent_3011` (EPSG:3011, `upplosning_m`) och skriver en GeoTIFF.

    Den klippta filen följer med geografins rådata så att projekt kan beskäras utan nytt nedladdat paket.
    """
    import math

    import rasterio
    from rasterio.transform import from_origin

    e0, n0, e1, n1 = extent_3011
    storlek = math.ceil(max(e1 - e0, n1 - n0) / upplosning_m) * upplosning_m
    ruta = Ruta(float(e0), float(n0), storlek, 0.0, upplosning_m)
    z, varn, _ = hoj.grund_z_med_diagnos(ruta, nan_varning_andel=nan_varning_andel)
    ut.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        ut, "w", driver="GTiff", height=z.shape[0], width=z.shape[1], count=1, dtype="float32",
        crs="EPSG:3011", transform=from_origin(e0, n0 + storlek, upplosning_m, upplosning_m),
        nodata=float("nan"), compress="deflate",
    ) as d:  # fmt: skip
        d.write(z, 1)
    return varn
