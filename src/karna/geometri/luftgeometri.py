"""Luftens geometri ur K1 v2: `Ytmosaik` → `luft.kedja.Domanspec` (och voxelrutnät).

Licensgräns: den här filen ligger i kärnan (GPL v3) och importerar luftdelen (MIT) men aldrig
tvärtom. `src/luft/` känner inte till K1; den tar höjdraster. Här översätts K1 v2 (terräng,
byggnadsregister, vegetation, markslag) till de rastren, så att luften räknar på samma geometri som
övriga domäner. Se `docs/LUFT.md` avsnitt "Gemensam geometri".

Vad som förs över till luften:

- Byggnader: höjd över terrängen per cell (`las_ytmodell` utan vegetation minus `las_terrang`);
taket är
  platt på `takhojd_rh2000` (K1 v2). Luftens voxelrutnät har plan mark (`luft.geometri.voxelisera`),
  så terrängens form förs inte över; på sluttande mark får en byggnad höjd över sin lokala mark.
- Vegetation: `vegetation.tif` (höjd över mark) som drag; bladytetäthet per markslag ur
  `data/markklasser.toml` (`luft.lad_m2_m3`). Vegetation av som standard (ingen tyst skugga/drag).
- Råhetslängd: `luft.z0_m` per markslag; `markens_z0_m` ger den effektiva markråheten (logaritmiskt
  medel över markceller utanför byggnader), tänkt som `z0_vagg_m` för väggmodellen. Inflödets z0
  (uppströms råhet) är en parameter, inte en markslagsegenskap.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from karna.geometri.lasa import RutaLike
from karna.geometri.markklass import Markklasstabell, las_markklasser
from karna.geometri.mosaik import Reduktion, Ytmosaik, bygg_ytmosaik
from karna.geometri.rasterfil import GeometriFel
from luft.kedja import Domanspec


@dataclass(frozen=True)
class LuftGeometri:
    """Luftens indata ur K1 v2, med redovisning."""

    spec: Domanspec
    mosaik: Ytmosaik
    z0_markyta_m: float | None
    okant_markslag_celler: int
    varningar: tuple[str, ...]
    matvarden: dict[str, float]


def lad_fran_markslag(
    markslag: NDArray[np.uint8], tabell: Markklasstabell
) -> tuple[NDArray[np.float64], int]:
    """Bladytetäthet per cell ur markklasstabellen. Kod 0 (okänt) får 0 och räknas (flaggas)."""
    ut = np.zeros(markslag.shape, dtype=np.float64)
    for kod in range(1, 10):
        ut[markslag == kod] = float(tabell.egenskaper_for(kod, "luft")["lad_m2_m3"])
    return ut, int(np.count_nonzero(markslag == 0))


def markens_z0_m(
    markslag: NDArray[np.uint8],
    byggnadshojd_m: NDArray[np.float64],
    tabell: Markklasstabell,
) -> float | None:
    """Effektiv råhetslängd för marken: exp(medel av ln z0) över celler utanför byggnader.

    Celler med markslag 0 (okänt) utelämnas. `None` om inga celler återstår.
    """
    fri = (byggnadshojd_m <= 0.0) & (markslag != 0)
    if not bool(fri.any()):
        return None
    lut = np.full(256, np.nan)
    for kod in range(1, 10):
        lut[kod] = float(tabell.egenskaper_for(kod, "luft")["z0_m"])
    z0 = lut[markslag[fri]]
    return float(math.exp(float(np.mean(np.log(z0)))))


def luft_geometri(
    rutor: Sequence[RutaLike],
    *,
    dx_m: float,
    raster_cell_m: float | None = None,
    hojd_doman_m: float | None = None,
    buffert_uppstroms_m: float = 80.0,
    buffert_nedstroms_m: float = 160.0,
    buffert_sidor_m: float = 60.0,
    omsampling: Reduktion = "medel",
    vegetation: bool = False,
    tillat_saknad_vegetation: bool = False,
    markklasser: Markklasstabell | None = None,
) -> LuftGeometri:
    """Bygger `Domanspec` ur K1 v2-rutor.

    `dx_m` är voxelrutnätets cellstorlek; höjdrastret läggs på `raster_cell_m` (standard `dx_m`),
    en heltalsmultipel av K1:s 0,25 m, med `omsampling` (`medel` bevarar byggnadsvolymen, `max` är
    konservativt för vindhinder). `hojd_doman_m` utelämnat: 2 x högsta byggnad/vegetation, minst
    sex celler. Saknade celler (rutor som inte ingår) räknas som tom mark och redovisas.
    """
    tabell = markklasser if markklasser is not None else las_markklasser()
    cell = float(dx_m if raster_cell_m is None else raster_cell_m)
    m = bygg_ytmosaik(
        rutor,
        cell,
        omsampling=omsampling,
        vegetation=vegetation,
        tillat_saknad_vegetation=tillat_saknad_vegetation,
    )
    hogsta = float(m.byggnadshojd_m.max())
    hogsta_veg = 0.0 if m.vegetation_hojd_m is None else float(m.vegetation_hojd_m.max())
    if hojd_doman_m is None:
        hojd_doman_m = max(2.0 * max(hogsta, hogsta_veg), 6.0 * dx_m)
        hojd_doman_m = math.ceil(hojd_doman_m / dx_m) * dx_m
    lad_raster, okant = lad_fran_markslag(m.markslag, tabell)
    veg = m.vegetation_hojd_m
    spec = Domanspec(
        hojd_over_mark_m=np.asarray(m.byggnadshojd_m, dtype=np.float64),
        raster_dx_m=m.cell_m,
        raster_origo_x_m=m.x_min_m,
        raster_origo_y_m=m.y_max_m,
        dx_m=float(dx_m),
        hojd_doman_m=float(hojd_doman_m),
        buffert_uppstroms_m=buffert_uppstroms_m,
        buffert_nedstroms_m=buffert_nedstroms_m,
        buffert_sidor_m=buffert_sidor_m,
        vegetation_hojd_m=None if veg is None else np.asarray(veg, dtype=np.float64),
        lad_m2_m3=lad_raster,
    )
    varningar: list[str] = []
    if m.saknade_celler:
        varningar.append(
            f"{m.saknade_celler} rutnätsceller täcks inte av någon ruta och räknas som tom mark"
        )
    if okant and veg is not None:
        varningar.append(
            f"{okant} celler har okänt markslag (kod 0): ingen vegetation antas där "
            "(flaggat, inte data)"
        )
    if float(np.ptp(m.terrang_rh2000_m)) > 0.5 * dx_m:
        varningar.append(
            f"terrängen varierar {float(np.ptp(m.terrang_rh2000_m)):.1f} m i området men luftens "
            "rutnät har plan mark: byggnadshöjderna gäller över lokal mark"
        )
    matvarden = {
        "antal_rutor": float(m.antal_rutor),
        "byggnadsceller": float(np.count_nonzero(m.byggnadshojd_m > 0)),
        "hogsta_byggnad_m": hogsta,
        "hojd_doman_m": float(hojd_doman_m),
        "saknade_celler": float(m.saknade_celler),
        "okant_markslag_celler": float(okant),
        "terrang_variation_m": float(np.ptp(m.terrang_rh2000_m)),
    }
    return LuftGeometri(
        spec=spec,
        mosaik=m,
        z0_markyta_m=markens_z0_m(m.markslag, m.byggnadshojd_m, tabell),
        okant_markslag_celler=okant,
        varningar=tuple(varningar),
        matvarden=matvarden,
    )


__all__ = [
    "GeometriFel",
    "LuftGeometri",
    "lad_fran_markslag",
    "luft_geometri",
    "markens_z0_m",
]
