"""Demovärldens geometri: terräng med lutning, gator med kantsten, kvarter, gårdar och träd.

Allt är syntetiskt och byggs med markmodellens egna byggare (`markmodell.pipeline`, K1 v2). Ingen
slumpning: samma parametrar ger samma rutor, samma recept och samma cachenycklar.

Världen är 700 x 700 m i SWEREF 99 18 00 (platsen styr bara solläget, nära Stockholms stadshus).
Gatunätet har en gata var 150:e m (12 m breda: 6,5 m körbana, 2,75 m gångbana på vardera sidan,
kantsten 0,12 m), fyra kvarter i vardera riktningen. Varje kvarter är ett slutet kvarter med
lameller (13 m djupa, 12 till 21 m höga) runt en gård; en port går genom södra lamellen. Marken
lutar mot söder och öster (0,4 respektive 0,15 procent). Två lågpunkter ger blåytor: en skål i den
sydvästra gården och en sänka i gatan x = 350 m. Trädrader längs gatan x = 350 m.

Fokusområdet (där frågorna ställs och resultaten visas) är de fyra mittkvarteren, 300 x 300 m.
Världen omkring är marginal så att dagsljusets påverkansradie ryms (`fokus` + radie <= världen);
se `exempel/fragor/demo_dagsljus.toml`.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from karna.scenario import Andring
from markmodell.brytlinjer import Brytlinje
from markmodell.byggnadsregister import ByggnadsKandidat, byggnad_id_fran_kalla
from markmodell.hinder_oppningar import GRAS, HARDGJORT, Oppning
from markmodell.pipeline import bygg_markmodell_ruta
from markmodell.rutnat import Ruta

KALLA = "demovarld"
KODVERSION = "demovarld/2"
START_TID = "2026-11-21T00:00:00+01:00"  # fast: samma värld ger samma recept (sha256)

GATA_BREDD_M = 12.0
KORBANA_HALV_M = 3.25
KANTSTEN_HOJD_M = 0.12
KORBANA_TVARFALL = 0.025
LAMELL_DJUP_M = 13.0
PORT_BREDD_M = 4.0
HOJDER_M = (12.0, 15.0, 18.0, 21.0)

LAGT_HUS = "lagt_hus"
LAGT_HUS_HOJD_M = 4.0
NYTT_HUS = "nytt_hus"
NYTT_HUS_HOJD_M = 18.0
HOJD_EFTER_M = 15.0


@dataclass(frozen=True)
class DemoVarld:
    """Parametrar för demovärlden. Alla mått i meter, relativt världens sydvästra hörn."""

    e0_m: float = 151_000.0
    n0_m: float = 6_580_000.0
    sida_m: float = 700.0
    rut_m: float = 100.0
    upplosning_m: float = 1.0
    gatucentra_m: tuple[float, ...] = (50.0, 200.0, 350.0, 500.0, 650.0)
    fokus_m: tuple[float, float, float, float] = (200.0, 200.0, 500.0, 500.0)

    @property
    def vegetation_upplosning_m(self) -> float:
        """Vegetationens rutnät är minst 1 m och en heltalsmultipel av markrutnätet."""
        return max(1.0, self.upplosning_m)

    @property
    def fokus_3011(self) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = self.fokus_m
        return (self.e0_m + x0, self.n0_m + y0, self.e0_m + x1, self.n0_m + y1)

    def kvarter(self) -> list[tuple[int, int, float, float, float, float]]:
        """(i, j, x0, x1, y0, y1) för varje kvarter mellan två gator i båda riktningarna."""
        c = self.gatucentra_m
        h = GATA_BREDD_M / 2.0
        return [
            (i, j, c[i] + h, c[i + 1] - h, c[j] + h, c[j + 1] - h)
            for i in range(len(c) - 1)
            for j in range(len(c) - 1)
        ]


# --- Terräng ------------------------------------------------------------------------------


def terrang_z(v: DemoVarld, e: NDArray[np.float64], n: NDArray[np.float64]) -> NDArray[np.float64]:
    """Markhöjd (RH 2000) i punkterna (e, n), före byggnader men med körbanans tvärfall."""
    x, y = e - v.e0_m, n - v.n0_m
    z = 13.5 + 0.004 * (y - 350.0) - 0.0015 * (x - 350.0)
    # Skål i den sydvästra gården (kvarter 1,1): 0,5 m djup, radie ca 16 m.
    r2 = ((x - 255.0) ** 2 + (y - 260.0) ** 2) / 16.0**2
    z = z - 0.5 * np.exp(-r2)
    # Körbanans tvärfall: krön i mitten, 0 vid rännstenen. Inte i korsningar.
    c = np.asarray(v.gatucentra_m)
    dx = np.min(np.abs(x[..., None] - c), axis=-1)
    dy = np.min(np.abs(y[..., None] - c), axis=-1)
    i_ns = (dx < KORBANA_HALV_M) & (dy >= GATA_BREDD_M / 2.0)
    i_ew = (dy < KORBANA_HALV_M) & (dx >= GATA_BREDD_M / 2.0)
    z = z + np.where(i_ns, KORBANA_TVARFALL * (KORBANA_HALV_M - dx), 0.0)
    z = z + np.where(i_ew, KORBANA_TVARFALL * (KORBANA_HALV_M - dy), 0.0)
    # Sänka i gatan x = 350: 0,3 m, längsgående utsträckning ca 25 m, bara i körbanan.
    sag = 0.3 * np.exp(-(((y - 255.0) / 22.0) ** 2)) * np.exp(-((np.abs(x - 350.0) / 4.5) ** 4))
    return np.asarray(z - sag, dtype=np.float64)


def markslag_grund(v: DemoVarld, ruta: Ruta) -> NDArray[np.uint8]:
    """Gator och gångbanor hårdgjorda, resten gräs (gårdar, parkmark i kanten)."""
    e, n = ruta.cell_centra_en()
    ee, nn = np.meshgrid(e, n)
    x, y = ee - v.e0_m, nn - v.n0_m
    c = np.asarray(v.gatucentra_m)
    dx = np.min(np.abs(x[..., None] - c), axis=-1)
    dy = np.min(np.abs(y[..., None] - c), axis=-1)
    gata = (dx < GATA_BREDD_M / 2.0) | (dy < GATA_BREDD_M / 2.0)
    return np.where(gata, HARDGJORT, GRAS).astype(np.uint8)


# --- Byggnader, kantsten, portar, träd ----------------------------------------------------


def _rekt(x0: float, x1: float, y0: float, y1: float) -> tuple[tuple[float, float], ...]:
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def byggnads_id(kalla_id: str) -> str:
    """`byggnad_id` i registret för en byggnad skapad med källid `kalla_id`."""
    return byggnad_id_fran_kalla(KALLA, kalla_id)


def _byggnad(
    v: DemoVarld, x0: float, x1: float, y0: float, y1: float, hojd_m: float, kalla_id: str
) -> ByggnadsKandidat:
    e, n = v.e0_m, v.n0_m
    return ByggnadsKandidat(
        polygon=tuple((e + x, n + y) for x, y in _rekt(x0, x1, y0, y1)),
        kalla=KALLA,
        kalla_id=kalla_id,
        hojd_m=hojd_m,
    )


def byggnader(v: DemoVarld) -> tuple[ByggnadsKandidat, ...]:
    """Slutna kvarter med lameller runt en gård (kvarter 2,2 har ingen norrlamell: öppen gård)
    och ett lågt hus (4 m) i den nordöstra gården."""
    ut: list[ByggnadsKandidat] = []
    d = LAMELL_DJUP_M
    for i, j, x0, x1, y0, y1 in v.kvarter():
        namn = f"kv_{i}_{j}"
        h = [HOJDER_M[(3 * i + 5 * j + s) % 4] for s in range(4)]
        ut.append(_byggnad(v, x0, x1, y0, y0 + d, h[0], f"{namn}_s"))
        if (i, j) != (2, 2):
            ut.append(_byggnad(v, x0, x1, y1 - d, y1, h[1], f"{namn}_n"))
        ut.append(_byggnad(v, x0, x0 + d, y0 + d, y1 - d, h[2], f"{namn}_v"))
        ut.append(_byggnad(v, x1 - d, x1, y0 + d, y1 - d, h[3], f"{namn}_o"))
    ut.append(_byggnad(v, 395.0, 410.0, 400.0, 412.0, LAGT_HUS_HOJD_M, LAGT_HUS))
    return tuple(ut)


def nytt_hus_polygon(v: DemoVarld) -> tuple[tuple[float, float], ...]:
    """Det nya huset (14 x 40 m): en gårdsbyggnad i den sydvästra gården, byggd ut från södra
    lamellen. Den spärrar gårdens avrinning österut mot porten."""
    return tuple((v.e0_m + x, v.n0_m + y) for x, y in _rekt(296.0, 310.0, 219.0, 259.0))


def kantsten(v: DemoVarld) -> tuple[Brytlinje, ...]:
    """Kantsten på båda sidor om varje gata, mellan korsningarna (öppet i korsningarna)."""
    c = v.gatucentra_m
    h = GATA_BREDD_M / 2.0
    ut: list[Brytlinje] = []
    for gc in c:
        for sida in (-KORBANA_HALV_M, KORBANA_HALV_M):
            for k in range(len(c) - 1):
                a, b = c[k] + h, c[k + 1] - h
                ut.append(
                    Brytlinje(
                        "kantsten",
                        KANTSTEN_HOJD_M,
                        ((v.e0_m + gc + sida, v.n0_m + a), (v.e0_m + gc + sida, v.n0_m + b)),
                    )
                )
                ut.append(
                    Brytlinje(
                        "kantsten",
                        KANTSTEN_HOJD_M,
                        ((v.e0_m + a, v.n0_m + gc + sida), (v.e0_m + b, v.n0_m + gc + sida)),
                    )
                )
    return tuple(ut)


def portar(v: DemoVarld) -> tuple[Oppning, ...]:
    """En port (4 m) genom södra lamellen, vid östra änden, i varje kvarter, på markens höjd.
    Gården dräneras genom porten; utan den vore varje gård en sluten skål."""
    ut: list[Oppning] = []
    for _i, _j, _x0, x1, y0, _y1 in v.kvarter():
        xm = x1 - 20.0
        e = np.array([v.e0_m + xm])
        n = np.array([v.n0_m + y0 + LAMELL_DJUP_M / 2.0])
        botten = float(terrang_z(v, e, n)[0])
        ut.append(
            Oppning(
                "port",
                PORT_BREDD_M,
                botten,
                KALLA,
                (
                    (v.e0_m + xm, v.n0_m + y0 - 1.0),
                    (v.e0_m + xm, v.n0_m + y0 + LAMELL_DJUP_M + 1.0),
                ),
            )
        )
    return tuple(ut)


def _trad(v: DemoVarld) -> list[tuple[float, float, float, float]]:
    """(x, y, kronradie, hojd) för träden: rader på båda gångbanorna längs gatan x = 350 m
    och några grupper i gårdarna."""
    ut: list[tuple[float, float, float, float]] = []
    for sida in (-4.6, 4.6):
        for k in range(8):
            ut.append((350.0 + sida, 214.0 + 15.0 * k, 3.0, 9.0 + (k % 3)))
    for i, j, x0, x1, y0, y1 in v.kvarter():
        if not (1 <= i <= 2 and 1 <= j <= 2):
            continue
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        for dx, dy in ((-30.0, 18.0), (-22.0, 24.0), (28.0, -26.0), (36.0, 14.0)):
            ut.append((cx + dx, cy + dy, 3.5, 11.0))
    return ut


def vegetation_hojd(v: DemoVarld, ruta: Ruta) -> NDArray[np.float32]:
    """Vegetationshöjd över mark på vegetationsrutnätet (träden som halvklot-liknande kronor)."""
    ruta1 = Ruta(
        ruta.e_min_m, ruta.n_min_m, ruta.storlek_m, ruta.overlapp_m, v.vegetation_upplosning_m
    )
    e, n = ruta1.cell_centra_en()
    ee, nn = np.meshgrid(e, n)
    x, y = ee - v.e0_m, nn - v.n0_m
    ut = np.zeros(ruta1.form_rc, dtype=np.float32)
    for tx, ty, rr, hh in _trad(v):
        r2 = ((x - tx) ** 2 + (y - ty) ** 2) / rr**2
        ut = np.maximum(ut, np.where(r2 < 1.0, hh * np.sqrt(np.clip(1.0 - r2, 0.0, 1.0)), 0.0))
    return np.asarray(ut, dtype=np.float32)


# --- K1 v2-rutor och K9-scenarier ---------------------------------------------------------


def bygg_ruta(v: DemoVarld, ut: Path, e_min_m: float, n_min_m: float, storlek_m: float) -> Path:
    """En K1 v2-ruta (utan överlapp) i `ut`/`mm_E_N`; returnerar rutans katalog."""
    ruta = Ruta(e_min_m, n_min_m, storlek_m, 0.0, v.upplosning_m)
    e, n = ruta.cell_centra_en()
    ee, nn = np.meshgrid(e, n)
    ut.mkdir(parents=True, exist_ok=True)
    res = bygg_markmodell_ruta(
        ruta,
        ut,
        grund_z=terrang_z(v, ee, nn).astype(np.float32),
        markslag_grund=markslag_grund(v, ruta),
        brytlinjer=kantsten(v),
        byggnadskandidater=byggnader(v),
        oppningar=portar(v),
        byggnadshojd_fran_laser=False,
        vegetation_hojd_m=vegetation_hojd(v, ruta),
        vegetation_upplosning_m=v.vegetation_upplosning_m,
        kodversion=KODVERSION,
        start_tid=START_TID,
    )
    return Path(res.katalog)


def bygg_rutnat_dagsljus(v: DemoVarld, ut: Path) -> Path:
    """Hela världen som `sida/rut` x `sida/rut` rutor i `ut`; en befintlig `ut` rensas."""
    if ut.exists():
        shutil.rmtree(ut)
    ut.mkdir(parents=True)
    antal = round(v.sida_m / v.rut_m)
    for i in range(antal):
        for j in range(antal):
            bygg_ruta(v, ut, v.e0_m + i * v.rut_m, v.n0_m + j * v.rut_m, v.rut_m)
    return ut


def bygg_ruta_vatten(v: DemoVarld, ut: Path) -> Path:
    """Fokusområdet som en enda ruta (vattenlösaren tar en K1-ruta); `ut` rensas."""
    if ut.exists():
        shutil.rmtree(ut)
    ut.mkdir(parents=True)
    e0, n0, e1, _ = v.fokus_3011
    bygg_ruta(v, ut, e0, n0, e1 - e0)
    return ut


def andring_hojd() -> Andring:
    return Andring(
        "byggnad_hojd",
        "hojd_lagt_hus",
        None,
        {"byggnads_id": byggnads_id(LAGT_HUS), "hojd_m": HOJD_EFTER_M},
    )


def andring_nytt_hus(v: DemoVarld) -> Andring:
    ring = [[e, n] for e, n in nytt_hus_polygon(v)]
    ring.append(ring[0])
    return Andring(
        "byggnad_ny",
        NYTT_HUS,
        {"type": "Polygon", "coordinates": [ring]},
        {"hojd_m": NYTT_HUS_HOJD_M},
    )


def avstand_till_kant(v: DemoVarld) -> float:
    """Minsta avstånd (m) från fokusområdet till världens kant."""
    x0, y0, x1, y1 = v.fokus_m
    return min(x0, y0, v.sida_m - x1, v.sida_m - y1)


# --- Syntetiskt NMD-lager för ekologidemon -----------------------------------------------

NMD_TALLSKOG = 111
NMD_ADELLOVSKOG = 116
NMD_OVRIG_OPPEN = 42
NMD_VAG = 53
NMD_HUS = 51
NMD_CELL_M = 10.0
GARD_DELNING_M = 133.0
"""Varje gård delas längs en diagonal (summan av avstånden från kvarterets sydvästra hörn är
`GARD_DELNING_M`): sydvästra delen tallskog, nordöstra ädellöv (omvänt i varannan gård).
Påhittad markbeläggning, inte en tolkning av verkliga data."""


def bygg_nmd(v: DemoVarld) -> tuple[NDArray[np.uint8], float, float, float]:
    """Påhittat NMD-lager (klasskoder som i NMD-basskiktet) över hela världen på 10 m-celler.

    Gator får 53, byggnader 51, gårdarna tallskog (111) och ädellövskog (116) i två delar och
    världens marginal övrig öppen mark (42). Returnerar (klasser, x_min, y_max, cell) i EPSG:3011.
    Syntetiskt: samma parametrar ger samma raster. Det är inte NMD och ska inte tolkas som det.
    """
    from rasterio.features import rasterize
    from rasterio.transform import from_origin

    cell = NMD_CELL_M
    n = round(v.sida_m / cell)
    e = v.e0_m + (np.arange(n) + 0.5) * cell
    nn = v.n0_m + v.sida_m - (np.arange(n) + 0.5) * cell
    ee, nr = np.meshgrid(e, nn)
    x, y = ee - v.e0_m, nr - v.n0_m
    c = np.asarray(v.gatucentra_m)
    dx = np.min(np.abs(x[..., None] - c), axis=-1)
    dy = np.min(np.abs(y[..., None] - c), axis=-1)
    gata = (dx < GATA_BREDD_M / 2.0) | (dy < GATA_BREDD_M / 2.0)
    klasser = np.full((n, n), NMD_OVRIG_OPPEN, dtype=np.uint8)
    for i, j, x0, x1, y0, y1 in v.kvarter():
        i_kv = (x >= x0) & (x < x1) & (y >= y0) & (y < y1)
        vast = (i + j) % 2 == 0
        delning = (x - x0) + (y - y0) < GARD_DELNING_M
        kod = np.where(delning == vast, NMD_TALLSKOG, NMD_ADELLOVSKOG).astype(np.uint8)
        klasser = np.where(i_kv, kod, klasser)
    klasser = np.where(gata, np.uint8(NMD_VAG), klasser)
    transform = from_origin(v.e0_m, v.n0_m + v.sida_m, cell, cell)
    ringar = [
        {"type": "Polygon", "coordinates": [[list(p) for p in (*b.polygon, b.polygon[0])]]}
        for b in byggnader(v)
        if b.kalla_id != LAGT_HUS
    ]
    hus = rasterize(((r, 1) for r in ringar), out_shape=(n, n), transform=transform, fill=0,
                    dtype="uint8")
    klasser = np.where(hus > 0, np.uint8(NMD_HUS), klasser).astype(np.uint8)
    return klasser, v.e0_m, v.n0_m + v.sida_m, cell
