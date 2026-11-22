# SPDX-License-Identifier: MIT
"""Hela luftkedjan: geometri → vindbibliotek (LES) → spridning → årsstatistik.

Två steg med olika kostnad:

1. `bygg_vindbibliotek` – en LES per vindriktning. Dyrt (GPU på server),
   görs en gång per geometri och sparas enligt KL1.
2. `berakna_halter` – en linjär spridningsoperator per riktning, alla källor
   samtidigt, sedan viktning med väderdata. Billigt, görs om för varje
   utsläppsscenario.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from luft.geometri import (
    Kallrapport,
    RasterRuta,
    linjekalla_med_rapport,
    nivaskiva_till_raster,
    voxelisera,
)
from luft.lbm import LbmParametrar, kor_les
from luft.snabbvind import kor_snabbvind
from luft.transport import Amne, TransportOperator
from luft.typer import FloatArray, Inflode, LuftFel
from luft.vindbibliotek import Fordelning, Haltstatistik, Vaderserie, Vindbibliotek, fordela


@dataclass(frozen=True)
class Domanspec:
    """Område och upplösning för en luftkörning."""

    hojd_over_mark_m: FloatArray  # 2D byggnadshöjd över mark, nordorienterat raster
    raster_dx_m: float
    raster_origo_x_m: float  # västra kanten
    raster_origo_y_m: float  # norra kanten
    dx_m: float  # cellstorlek i 3D-rutnätet
    hojd_doman_m: float
    buffert_uppstroms_m: float
    buffert_nedstroms_m: float
    buffert_sidor_m: float
    vegetation_hojd_m: FloatArray | None = None
    # Bladytetäthet i vegetationsceller: ett tal, eller ett raster (samma form som höjdrastret).
    lad_m2_m3: FloatArray | float = 1.0

    @property
    def utbredning(self) -> tuple[float, float, float, float]:
        rader, kolumner = self.hojd_over_mark_m.shape
        return (
            self.raster_origo_x_m,
            self.raster_origo_y_m - rader * self.raster_dx_m,
            self.raster_origo_x_m + kolumner * self.raster_dx_m,
            self.raster_origo_y_m,
        )


def bygg_vindbibliotek(
    spec: Domanspec,
    riktningar_grad: Sequence[float],
    inflode: Inflode,
    param: LbmParametrar = LbmParametrar(),  # noqa: B008 - fryst dataklass
    backend: str = "numpy",
    logg: Callable[[str], None] | None = None,
    *,
    metod: str = "lbm",
) -> Vindbibliotek:
    """Kör en LES (``metod="lbm"``) eller snabb Poisson-skissvind (``metod="snabbvind"``)
    per riktning och samlar fälten i ett vindbibliotek."""
    if len(riktningar_grad) == 0:
        raise LuftFel("Minst en vindriktning krävs.")
    if metod not in ("lbm", "snabbvind"):
        raise LuftFel(f"Okänd vindmetod: {metod!r} (välj 'lbm' eller 'snabbvind').")
    falt = []
    for r in riktningar_grad:
        grid = voxelisera(
            spec.hojd_over_mark_m,
            spec.dx_m,
            spec.hojd_doman_m,
            raster_dx_m=spec.raster_dx_m,
            raster_origo_x_m=spec.raster_origo_x_m,
            raster_origo_y_m=spec.raster_origo_y_m,
            buffert_uppstroms_m=spec.buffert_uppstroms_m,
            buffert_nedstroms_m=spec.buffert_nedstroms_m,
            buffert_sidor_m=spec.buffert_sidor_m,
            vegetation_hojd_m=spec.vegetation_hojd_m,
            lad_m2_m3=spec.lad_m2_m3,
            vindriktning_grad=float(r),
        )
        if logg is not None:
            logg(f"Vindriktning {r:g}°: rutnät {grid.form}, {int(grid.luft.sum())} luftceller")
        if metod == "snabbvind":
            falt.append(kor_snabbvind(grid, inflode, cd_vegetation=param.cd_vegetation))
        else:
            falt.append(kor_les(grid, inflode, param, backend=backend, logg=logg))
    return Vindbibliotek(tuple(falt))


@dataclass
class Haltresultat:
    """Halter på utvärderingshöjden, per riktning och som årsstatistik."""

    ruta: RasterRuta
    halter_per_riktning_g_m3: list[FloatArray]
    fordelning: Fordelning
    statistik: Haltstatistik
    kallrapporter: list[Kallrapport]
    massbalans: list[dict[str, float]]
    matvarden: dict[str, Any] = field(default_factory=dict)


def berakna_halter(
    bibliotek: Vindbibliotek,
    amne: Amne,
    linjer_xy_m: Sequence[FloatArray],
    emission_g_m_s: Sequence[float],
    vader: Vaderserie,
    utbredning: tuple[float, float, float, float],
    raster_dx_m: float,
    *,
    kallhojd_m: float = 0.5,
    utvardering_hojd_m: float = 2.0,
    u_min_m_s: float = 0.5,
    interpolera: bool = False,
    schema: str = "uppstroms",
) -> Haltresultat:
    """Halt från linjekällor för alla riktningar i biblioteket, viktat med väderserien.

    Varje riktning har sitt eget roterade rutnät. Halten löses där (en operator,
    alla källor), samplas på utvärderingshöjden och läggs på ett gemensamt
    nordorienterat raster så att statistiken kan räknas cellvis.
    """
    halter: list[FloatArray] = []
    rapporter: list[Kallrapport] = []
    balanser: list[dict[str, float]] = []
    ruta: RasterRuta | None = None
    for r in bibliotek.riktningar_grad:
        vind = bibliotek.for_riktning(r)
        kallor, rapport = linjekalla_med_rapport(vind.grid, linjer_xy_m, emission_g_m_s, kallhojd_m)
        op = TransportOperator(vind, amne, schema=schema)
        c = op.los(kallor)
        balans = op.massbalans(kallor, c)
        if not balans["rel_fel"] < 1e-6:
            raise LuftFel(f"Massbalansen stämmer inte för riktning {r:g}°: {balans}")
        skiva, ruta = nivaskiva_till_raster(
            vind.grid, c, utvardering_hojd_m, raster_dx_m, utbredning, metod="bilinjar"
        )
        halter.append(skiva)
        rapporter.append(rapport)
        balanser.append(balans)
    assert ruta is not None
    fordelning = fordela(
        vader, bibliotek.riktningar_grad, bibliotek.u_ref_m_s, u_min_m_s, interpolera
    )
    statistik = Haltstatistik(halter, fordelning)
    matvarden = {
        "max_rel_massbalansfel": max(b["rel_fel"] for b in balanser),
        "utslapp_inne_g_s": [rp.inne_g_s for rp in rapporter],
        "utslapp_utanfor_g_s": [rp.utanfor_g_s for rp in rapporter],
        "vader": fordelning.sammanfattning(),
    }
    if math.isnan(fordelning.andel_svag_vind):
        raise LuftFel("Väderserien saknar giltiga timmar.")
    return Haltresultat(ruta, halter, fordelning, statistik, rapporter, balanser, matvarden)


def kanslighet_for_receptor(
    bibliotek: Vindbibliotek,
    amne: Amne,
    receptor_xy_m: tuple[float, float],
    receptor_hojd_m: float,
    fordelning: Fordelning,
    utbredning: tuple[float, float, float, float],
    raster_dx_m: float,
    kallhojd_m: float = 0.5,
) -> tuple[FloatArray, RasterRuta]:
    """Årsmedelvärdesviktad känslighetskarta för en receptorpunkt (adjungerad lösning).

    Resultat: årsmedelhalt i receptorn (g/m³) per g/s utsläpp i varje rastercell
    på källhöjden. Ett enda adjungerat system per riktning ersätter en körning per
    möjlig källa.
    """
    from luft.geometri import fran_sweref

    n = fordelning.antal_modellerade
    if n == 0:
        raise LuftFel("Inga modellerade timmar i väderfördelningen.")
    summa: FloatArray | None = None
    ruta: RasterRuta | None = None
    for s, r in enumerate(fordelning.riktningar_grad):
        mask = fordelning.modellerad[:, None] & (fordelning.sektor_index == s)
        vikt = float(np.sum((fordelning.vikt * fordelning.skalfaktor[:, None])[mask])) / n
        vind = bibliotek.for_riktning(r)
        i, j = fran_sweref(vind.grid, receptor_xy_m[0], receptor_xy_m[1])
        ii, jj = int(math.floor(float(i) + 0.5)), int(math.floor(float(j) + 0.5))
        kk = int(receptor_hojd_m // vind.grid.dx_m)
        nx, ny, nz = vind.grid.form
        if not (0 <= ii < nx and 0 <= jj < ny and 0 <= kk < nz) or vind.grid.fast[ii, jj, kk]:
            raise LuftFel(f"Receptorn ligger utanför rutnätet eller i en byggnad ({r:g}°).")
        w = np.zeros(vind.grid.form)
        w[ii, jj, kk] = 1.0
        y = TransportOperator(vind, amne).los_adjungerad(w)
        skiva, ruta = nivaskiva_till_raster(vind.grid, y, kallhojd_m, raster_dx_m, utbredning)
        bidrag = np.nan_to_num(skiva) * vikt
        summa = bidrag if summa is None else summa + bidrag
    assert summa is not None and ruta is not None
    return summa, ruta
