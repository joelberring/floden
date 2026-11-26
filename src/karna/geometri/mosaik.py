"""Mosaik av K1 v2-rutor på ett grövre rutnät: terräng, byggnadshöjd, vegetation och markslag.

Gemensam inläsning för domäner som räknar på ett eget, grövre rutnät än markmodellens 0,25 m
(luft: voxelrutnät). Rutorna läggs ut på ett gemensamt rutnät med 0,25 m-celler (rutor överlappar
25 m; den sist inlästa vinner, och överlappet är lika i alla rutor) och reduceras sedan med en
heltalsfaktor. Rutnätets ursprung ligger på en multipel av `cell_m` i EPSG:3011, så att före- och
efterscenarier ger samma rutnät.

Reduktion: höjder med `medel` (bevarar volym) eller `max`; markslag med den vanligaste koden i
blocket
(lika antal: lägsta kod). Celler utan någon ruta räknas i `saknade_celler` och får höjd 0 och
markslag 0.

K1 v1-rutor (fast hinderhöjd) vägras av `las_ytmodell`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from karna.geometri.lasa import (
    RutaLike,
    las_markslag,
    las_terrang,
    las_vegetation,
    las_ytmodell,
    vegetation_pa_marknat,
)
from karna.geometri.rasterfil import GeometriFel

Reduktion = Literal["medel", "max"]
_TOL = 1e-6


@dataclass(frozen=True)
class Ytmosaik:
    """Rutornas geometri på ett rutnät med cellstorleken `cell_m` (rad 0 = norr)."""

    cell_m: float
    x_min_m: float  # västra kanten
    y_max_m: float  # norra kanten
    terrang_rh2000_m: NDArray[np.float64]
    byggnadshojd_m: NDArray[np.float64]  # takhöjd över terräng i byggnadsceller, annars 0
    vegetation_hojd_m: NDArray[np.float64] | None
    markslag: NDArray[np.uint8]
    saknade_celler: int
    antal_rutor: int
    pixelstorlek_m: float
    icke_andliga_celler: int = 0
    varningar: tuple[str, ...] = ()

    @property
    def form(self) -> tuple[int, int]:
        return int(self.markslag.shape[0]), int(self.markslag.shape[1])

    @property
    def utbredning(self) -> tuple[float, float, float, float]:
        """(x_min, y_min, x_max, y_max)."""
        r, k = self.form
        return (
            self.x_min_m,
            self.y_max_m - r * self.cell_m,
            self.x_min_m + k * self.cell_m,
            self.y_max_m,
        )


def _reducera(a: NDArray[np.float64], f: int, metod: Reduktion) -> NDArray[np.float64]:
    if f == 1:
        return a.copy()
    r, k = a.shape
    blk = a.reshape(r // f, f, k // f, f)
    andlig = np.isfinite(blk)
    antal = andlig.sum(axis=(1, 3))
    har_data = antal > 0
    ut = np.full((r // f, k // f), np.nan, dtype=np.float64)
    if not bool(har_data.any()):
        return ut
    if metod == "max":
        fylld = np.where(andlig, blk, -np.inf)
        ut[har_data] = fylld.max(axis=(1, 3))[har_data]
    else:
        summa = np.where(andlig, blk, 0.0).sum(axis=(1, 3))
        ut[har_data] = summa[har_data] / antal[har_data]
    return ut


def _reducera_markslag(a: NDArray[np.uint8], f: int) -> NDArray[np.uint8]:
    if f == 1:
        return a
    r, k = a.shape
    blk = a.reshape(r // f, f, k // f, f)
    antal = np.stack([(blk == kod).sum(axis=(1, 3)) for kod in range(10)], axis=0)
    return np.asarray(antal.argmax(axis=0), dtype=np.uint8)


def bygg_ytmosaik(
    rutor: Sequence[RutaLike],
    cell_m: float,
    *,
    omsampling: Reduktion = "medel",
    vegetation: bool = False,
    tillat_saknad_vegetation: bool = False,
) -> Ytmosaik:
    """Lägger ut `rutor` (K1 v2) på ett rutnät med cellstorleken `cell_m`.

    `cell_m` ska vara en heltalsmultipel av rutornas pixelstorlek. `byggnadshojd_m` är
    ytmodellens höjd över terrängen i byggnadsceller (`las_ytmodell` utan vegetation minus
    `las_terrang`); vegetationen läggs separat (`vegetation=True`, höjd över mark).
    """
    if not rutor:
        raise GeometriFel("Inga rutor att bygga en mosaik av.")
    if omsampling not in ("medel", "max"):
        raise GeometriFel(f"omsampling ska vara 'medel' eller 'max', fick {omsampling!r}.")
    delar = []
    for ruta in rutor:
        terr, geo = las_terrang(ruta)
        dsm, _ = las_ytmodell(ruta, vegetation=False)
        slag, _ = las_markslag(ruta)
        veg = None
        if vegetation:
            v = las_vegetation(ruta)
            if v is None:
                if not tillat_saknad_vegetation:
                    raise GeometriFel(
                        "En ruta saknar vegetation.tif; ange tillat_saknad_vegetation=True för "
                        "att anta ingen vegetation (en antagen nolla, inte en mätning)."
                    )
            else:
                veg = vegetation_pa_marknat(v[0], v[1], geo)
        delar.append((geo, terr, dsm, slag, veg))
    px = delar[0][0].pixelstorlek_x
    for geo, *_ in delar:
        if abs(geo.pixelstorlek_x - px) > _TOL or abs(geo.pixelstorlek_y - px) > _TOL:
            raise GeometriFel("Rutorna har olika eller icke-kvadratiska celler.")
    f_flyt = cell_m / px
    f = round(f_flyt)
    if f < 1 or abs(f_flyt - f) > _TOL:
        raise GeometriFel(f"cell_m={cell_m} måste vara en heltalsmultipel av pixelstorleken {px}.")

    x0 = math.floor(min(g.x_min for g, *_ in delar) / cell_m + _TOL) * cell_m
    x1 = math.ceil(max(g.x_max for g, *_ in delar) / cell_m - _TOL) * cell_m
    y0 = math.floor(min(g.y_min for g, *_ in delar) / cell_m + _TOL) * cell_m
    y1 = math.ceil(max(g.y_max for g, *_ in delar) / cell_m - _TOL) * cell_m
    bredd = round((x1 - x0) / px)
    hojd = round((y1 - y0) / px)
    terr_c = np.full((hojd, bredd), np.nan, dtype=np.float64)
    bygg_c = np.full((hojd, bredd), np.nan, dtype=np.float64)
    veg_c = np.full((hojd, bredd), np.nan, dtype=np.float64) if vegetation else None
    slag_c = np.zeros((hojd, bredd), dtype=np.uint8)
    taget = np.zeros((hojd, bredd), dtype=bool)
    giltig_terr = np.zeros((hojd, bredd), dtype=bool)
    giltig_bygg = np.zeros((hojd, bredd), dtype=bool)
    giltig_veg = np.zeros((hojd, bredd), dtype=bool) if vegetation else None
    for geo, terr, dsm, slag, veg in delar:
        j0f, i0f = (geo.x_min - x0) / px, (y1 - geo.y_max) / px
        j0, i0 = round(j0f), round(i0f)
        if abs(j0f - j0) > 1e-4 or abs(i0f - i0) > 1e-4:
            raise GeometriFel(
                "En ruta ligger inte på mosaikens cellraster (förskjutning ej heltal)."
            )
        h, b = terr.shape
        sl = (slice(i0, i0 + h), slice(j0, j0 + b))
        terr64 = np.asarray(terr, dtype=np.float64)
        dsm64 = np.asarray(dsm, dtype=np.float64)
        ok_t = np.isfinite(terr64)
        ok_b = ok_t & np.isfinite(dsm64)
        terr_sub = terr_c[sl]
        terr_sub[ok_t] = terr64[ok_t]
        bygg_sub = bygg_c[sl]
        bygg_sub[ok_b] = np.maximum(dsm64[ok_b] - terr64[ok_b], 0.0)
        slag_sub = slag_c[sl]
        slag_sub[ok_b] = slag[ok_b]
        taget[sl] = True
        gt_sub = giltig_terr[sl]
        gt_sub[ok_t] = True
        gb_sub = giltig_bygg[sl]
        gb_sub[ok_b] = True
        if veg_c is not None and giltig_veg is not None:
            veg_sub = veg_c[sl]
            gv_sub = giltig_veg[sl]
            if veg is None:
                veg_sub[ok_b] = 0.0
                gv_sub[ok_b] = True
            else:
                veg64 = np.asarray(veg, dtype=np.float64)
                ok_v = np.isfinite(veg64)
                veg_sub[ok_v] = np.maximum(veg64[ok_v], 0.0)
                gv_sub[ok_v] = True
    if not bool(giltig_terr.any()) or not bool(giltig_bygg.any()):
        raise GeometriFel(
            "Mosaiken saknar ändliga höjddata (alla celler i terräng eller ytmodell är NaN/Inf)."
        )
    ej_tackt_pix = ~taget
    ej_andlig_pix = taget & (~giltig_terr | ~giltig_bygg)
    if giltig_veg is not None:
        ej_andlig_pix = ej_andlig_pix | (taget & ~giltig_veg)
    ej_tackta = ej_tackt_pix.reshape(hojd // f, f, bredd // f, f).any(axis=(1, 3))
    icke_andliga = ej_andlig_pix.reshape(hojd // f, f, bredd // f, f).any(axis=(1, 3))
    saknade = ej_tackta | icke_andliga

    terr_red = _reducera(terr_c, f, "medel")
    bygg_red = _reducera(bygg_c, f, omsampling)
    veg_red = None if veg_c is None else _reducera(veg_c, f, omsampling)

    # Helt saknade block (inga ändliga pixlar): fyll terräng med områdets kända medel och
    # byggnad/vegetation med 0; varje berörd cell flaggas uttryckligen i saknade_celler/varningar.
    terr_tom = ~np.isfinite(terr_red)
    if bool(terr_tom.any()):
        terr_red[terr_tom] = float(np.nanmean(terr_red))
    bygg_tom = ~np.isfinite(bygg_red)
    if bool(bygg_tom.any()):
        bygg_red[bygg_tom] = 0.0
    if veg_red is not None:
        veg_tom = ~np.isfinite(veg_red)
        if bool(veg_tom.any()):
            veg_red[veg_tom] = 0.0

    varningar: list[str] = []
    n_ej_andliga = int(icke_andliga.sum())
    if n_ej_andliga > 0:
        n_pix = int(ej_andlig_pix.sum())
        andel = 100.0 * float(n_pix) / max(1.0, float(taget.sum()))
        varningar.append(
            f"{n_ej_andliga} rutnätsceller ({n_pix} pixlar, {andel:.1f} %) har icke-ändliga "
            "höjddata (NaN/Inf): maskerade vid omsampling och helt saknade celler räknas som "
            "tom mark (flaggat, inte data)"
        )
    return Ytmosaik(
        cell_m=float(cell_m),
        x_min_m=float(x0),
        y_max_m=float(y1),
        terrang_rh2000_m=terr_red,
        byggnadshojd_m=bygg_red,
        vegetation_hojd_m=veg_red,
        markslag=_reducera_markslag(slag_c, f),
        saknade_celler=int(saknade.sum()),
        antal_rutor=len(delar),
        pixelstorlek_m=float(px),
        icke_andliga_celler=n_ej_andliga,
        varningar=tuple(varningar),
    )
