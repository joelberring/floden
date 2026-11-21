# SPDX-License-Identifier: MIT
"""`floden luft …` – kommandon för vindbibliotek, halter och en syntetisk demo.

    floden luft vind  --byggnader hojd.tif --ut bibliotek/ [--riktningar 12 --dx 2 --backend cupy]
    floden luft halt  --bibliotek bibliotek/ --byggnader hojd.tif --vagar vagar.geojson
                      --vader vader.csv --ut resultat/ [--no2 ...]
    floden luft demo  --ut demo/   (syntetiskt kvarter, kör hela kedjan på CPU)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from luft.backend import BACKENDS
from luft.export import Luftresultat, exportera
from luft.geometri import RasterRuta
from luft.kedja import (
    Domanspec,
    Haltresultat,
    berakna_halter,
    bygg_vindbibliotek,
    kanslighet_for_receptor,
)
from luft.kemi import no2_fotostationar
from luft.lbm import LbmParametrar
from luft.transport import Amne
from luft.typer import FloatArray, Inflode, LuftFel
from luft.vindbibliotek import Vaderserie, Vindbibliotek
from luft.vindkomfort import (
    KRITERIER,
    Komfortresultat,
    berakna_komfort,
    jamfor,
    omrakna_vindhastighet,
)

TESTVARDE_MARKNING = "TESTVÄRDE – syntetiska indata, inte för skarpa slutsatser"


def registrera(subparsers: Any) -> None:
    """Lägger till `luft` med underkommandon i Flodens huvudparser."""
    p = subparsers.add_parser("luft", help="3D-vind, spridning av gaser och partiklar (MIT).")
    sub = p.add_subparsers(dest="luft_kommando")

    def gemensamma_doman(q: argparse.ArgumentParser) -> None:
        q.add_argument("--byggnader", required=True, help="GeoTIFF: byggnadshöjd över mark (m).")
        q.add_argument("--vegetation", help="GeoTIFF: vegetationshöjd (m), valfri.")
        q.add_argument("--dx", type=float, default=2.0, help="Cellstorlek i 3D (m).")
        q.add_argument("--hojd-doman", type=float, default=100.0, help="Domänhöjd (m).")
        q.add_argument("--buffert", type=float, default=100.0, help="Buffert runt området (m).")

    v = sub.add_parser("vind", help="Bygg vindbibliotek (en LES per riktning).")
    gemensamma_doman(v)
    v.add_argument("--ut", required=True)
    v.add_argument("--riktningar", type=int, default=12, help="Antal jämnt fördelade riktningar.")
    v.add_argument("--u-ref", type=float, default=5.0)
    v.add_argument("--z-ref", type=float, default=10.0)
    v.add_argument("--z0", type=float, default=0.5)
    v.add_argument("--genomstromningar", type=float, default=2.0)
    v.add_argument("--backend", choices=BACKENDS, default="numpy")

    h = sub.add_parser("halt", help="Halter ur vindbibliotek, linjekällor och väderserie.")
    gemensamma_doman(h)
    h.add_argument("--bibliotek", required=True)
    h.add_argument("--vagar", required=True, 
                   help="Linjer (GeoJSON/GPKG) med fältet emission_g_m_s.")
    h.add_argument("--vader", required=True, help="CSV: tid,vindriktning_grad,vindhastighet_m_s.")
    h.add_argument("--amne", default="NOx")
    h.add_argument("--deposition", type=float, default=0.0, help="m/s")
    h.add_argument("--sedimentation", type=float, default=0.0, help="m/s")
    h.add_argument("--hojd", type=float, default=2.0, help="Utvärderingshöjd (m).")
    h.add_argument("--grans", type=float, help="Gränsvärde (µg/m³) för timmar över.")
    h.add_argument("--percentil", type=float, action="append", default=[])
    h.add_argument("--receptor", type=float, nargs=2, metavar=("X", "Y"))
    h.add_argument("--interpolera", action="store_true")
    h.add_argument("--ut", required=True)

    k = sub.add_parser("komfort", help="Vindkomfort och vindfara på gånghöjd (NEN 8100/Lawson).")
    gemensamma_doman(k)
    k.add_argument("--bibliotek", required=True, help="Vindbibliotek för förslaget (efter).")
    k.add_argument("--fore", help="Vindbibliotek för nuläget, för före/efter-jämförelse.")
    k.add_argument("--vader", required=True,
                   help="CSV med referensvind vid inflödets z_ref/z0 (se --omrakna).")
    k.add_argument("--kriterium", choices=sorted(KRITERIER), default="nen8100")
    k.add_argument("--toppfaktor", type=float, help="Toppfaktor g för GEM (Lawson).")
    k.add_argument("--omrakna", type=float, nargs=3, metavar=("Z_STATION", "Z0_STATION", "BLAND"),
                   help="Räkna om stationsvind till inflödets z_ref/z0 via blandningshöjd.")
    k.add_argument("--ut", required=True)

    kd = sub.add_parser("komfortdemo", help="Höghus före/efter i syntetiskt kvarter (CPU).")
    kd.add_argument("--ut", required=True)
    kd.add_argument("--riktningar", type=int, default=8)
    kd.add_argument("--dx", type=float, default=4.0)
    kd.add_argument("--genomstromningar", type=float, default=1.5)
    kd.add_argument("--hushojd", type=float, default=75.0)
    kd.add_argument("--backend", choices=BACKENDS, default="numpy")

    d = sub.add_parser("demo", help="Syntetiskt kvarter genom hela kedjan (CPU).")
    d.add_argument("--ut", required=True)
    d.add_argument("--riktningar", type=int, default=4)
    d.add_argument("--dx", type=float, default=4.0)
    d.add_argument("--genomstromningar", type=float, default=1.5)
    d.add_argument("--backend", choices=BACKENDS, default="numpy")


def _las_raster(sokvag: str) -> tuple[FloatArray, float, float, float]:
    import rasterio

    with rasterio.open(sokvag) as ds:
        if ds.crs is None or ds.crs.to_epsg() != 3011:
            raise LuftFel(f"{sokvag}: rastret måste vara i EPSG:3011.")
        t = ds.transform
        if abs(t.b) > 0 or abs(t.d) > 0 or abs(abs(t.a) - abs(t.e)) > 1e-9:
            raise LuftFel(f"{sokvag}: rastret måste vara nordorienterat med kvadratiska celler.")
        a = ds.read(1).astype(np.float64)
        if ds.nodata is not None:
            a[a == ds.nodata] = 0.0
        return np.nan_to_num(a), float(t.a), float(t.c), float(t.f)


def _spec(args: argparse.Namespace) -> Domanspec:
    hojd, rdx, ox, oy = _las_raster(args.byggnader)
    veg = _las_raster(args.vegetation)[0] if args.vegetation else None
    return Domanspec(hojd, rdx, ox, oy, args.dx, args.hojd_doman, args.buffert, 2 * args.buffert,
                     args.buffert, veg)


def _riktningar(n: int) -> list[float]:
    if n < 1:
        raise LuftFel("--riktningar måste vara minst 1.")
    return [i * 360.0 / n for i in range(n)]


def _logg(s: str) -> None:
    print(s, flush=True)


def _kor_vind(args: argparse.Namespace) -> int:
    spec = _spec(args)
    param = LbmParametrar(upprampning_genomstromningar=args.genomstromningar,
                          medel_genomstromningar=args.genomstromningar)
    bib = bygg_vindbibliotek(spec, _riktningar(args.riktningar),
                             Inflode(args.u_ref, args.z_ref, args.z0), param, args.backend, _logg)
    bib.spara(args.ut)
    print(f"Vindbibliotek sparat i {args.ut} ({len(bib)} riktningar).")
    return 0


def _las_linjer(sokvag: str) -> tuple[list[FloatArray], list[float]]:
    import geopandas as gpd

    df = gpd.read_file(sokvag)
    if df.crs is None or df.crs.to_epsg() != 3011:
        df = df.to_crs(3011)
    if "emission_g_m_s" not in df.columns:
        raise LuftFel(f"{sokvag}: fältet emission_g_m_s saknas.")
    linjer: list[FloatArray] = []
    emission: list[float] = []
    for geom, e in zip(df.geometry, df["emission_g_m_s"], strict=True):
        delar = getattr(geom, "geoms", [geom])
        for g in delar:
            linjer.append(np.asarray(g.coords, dtype=np.float64)[:, :2])
            emission.append(float(e))
    return linjer, emission


def _exportera_halt(
    ut: Path, amne: Amne, res: Haltresultat, grans_ug: float | None, percentiler: list[float],
    kanslighet: dict[str, FloatArray], backend: str, parametrar: dict[str, Any],
    indata: list[tuple[str, str | Path]], varningar: list[str], start: str,
) -> Path:
    raster: dict[str, FloatArray] = {"arsmedel": res.statistik.arsmedel()}
    for p in percentiler:
        raster[f"p{p:g}".replace(".", "_")] = res.statistik.percentil(p)
    matvarden = dict(res.matvarden)
    if grans_ug is not None:
        timmar = res.statistik.antal_timmar_over(grans_ug * 1e-6)
        matvarden["max_timmar_over_grans"] = float(np.nanmax(timmar))
        np.save(ut / f"timmar_over_{grans_ug:g}ug.npy", timmar)
    matvarden["max_arsmedel_ug_m3"] = float(np.nanmax(raster["arsmedel"]) * 1e6)
    lr = Luftresultat(amne=amne.namn, ruta=res.ruta, raster_g_m3=raster,
                      kanslighet_g_m3_per_g_s=kanslighet)
    manifest, _ = exportera(ut, lr, backend=backend, parametrar=parametrar, indata=indata,
                            matvarden=matvarden, varningar=varningar, start=start)
    return manifest


def _kor_halt(args: argparse.Namespace) -> int:
    start = datetime.now().astimezone().isoformat()
    spec = _spec(args)
    bib = Vindbibliotek.ladda(args.bibliotek)
    linjer, emission = _las_linjer(args.vagar)
    vader = Vaderserie.las_csv(args.vader)
    amne = Amne(args.amne, deposition_m_s=args.deposition, sedimentation_m_s=args.sedimentation)
    res = berakna_halter(bib, amne, linjer, emission, vader, spec.utbredning, spec.raster_dx_m,
                         utvardering_hojd_m=args.hojd, interpolera=args.interpolera)
    kansl: dict[str, FloatArray] = {}
    if args.receptor:
        kansl["receptor"], _ = kanslighet_for_receptor(
            bib, amne, (args.receptor[0], args.receptor[1]), args.hojd, res.fordelning,
            spec.utbredning, spec.raster_dx_m)
    ut = Path(args.ut)
    ut.mkdir(parents=True, exist_ok=True)
    varningar = [v for rp in res.kallrapporter for v in rp.varningar]
    m = _exportera_halt(ut, amne, res, args.grans, args.percentil, kansl, "numpy",
                        {"amne": amne.namn, "hojd_m": args.hojd},
                        [("byggnader", args.byggnader), ("vagar", args.vagar),
                         ("vader", args.vader)], varningar, start)
    print(f"Resultat skrivet i {ut} ({m.name}).")
    return 0


# ---------------------------------------------------------------- demo


def syntetiskt_kvarter(raster_dx_m: float = 2.0) -> tuple[FloatArray, float, float]:
    """Ett påhittat stadsområde: rutnät av kvarter med gårdar och ett höghus.

    Returnerar (höjdraster, origo_x, origo_y). Koordinaterna ligger i Stockholm
    (EPSG:3011) men geometrin är påhittad.
    """
    n = int(round(240 / raster_dx_m))
    h = np.zeros((n, n))
    s = raster_dx_m
    for bx in (20, 100, 180):
        for by in (20, 100, 180):
            r0, r1 = int(by / s), int((by + 50) / s)
            c0, c1 = int(bx / s), int((bx + 50) / s)
            h[r0:r1, c0:c1] = 18.0
            g = int(12 / s)
            h[r0 + g:r1 - g, c0 + g:c1 - g] = 0.0  # gård
    h[int(100 / s):int(130 / s), int(100 / s):int(130 / s)] = 45.0  # höghus
    return h, 152_000.0, 6_577_240.0


def _syntetisk_vader(antal: int = 8760, seed: int = 1) -> Vaderserie:
    rng = np.random.default_rng(seed)
    riktning = (225.0 + rng.normal(0.0, 60.0, antal)) % 360.0  # dominerande sydvästlig
    fart = rng.weibull(2.0, antal) * 4.5
    return Vaderserie.skapa(riktning, fart)


def _rita_png(sokvag: Path, falt: FloatArray, hojd: FloatArray, ruta: RasterRuta, titel: str,
              enhet: str, linjer: list[FloatArray], punkt: tuple[float, float] | None,
              log: bool = False, divergerande: bool = False) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x0, y0, x1, y1 = ruta.utbredning
    from matplotlib.colors import LogNorm

    fig, ax = plt.subplots(figsize=(7, 6), dpi=130)
    norm = None
    if log:
        positiva = falt[np.isfinite(falt) & (falt > 0)]
        if positiva.size:
            norm = LogNorm(vmin=float(np.percentile(positiva, 50)), vmax=float(positiva.max()))
    cmap = "magma_r"
    vmin = vmax = None
    if divergerande:
        cmap = "RdBu_r"
        m = float(np.nanmax(np.abs(falt))) if np.isfinite(falt).any() else 1.0
        vmin, vmax = -m, m
    im = ax.imshow(falt, extent=(x0, x1, y0, y1), cmap=cmap, interpolation="nearest",
                   norm=norm, vmin=vmin, vmax=vmax)
    hx = x0 + (np.arange(hojd.shape[1]) + 0.5) * (x1 - x0) / hojd.shape[1]
    hy = y1 - (np.arange(hojd.shape[0]) + 0.5) * (y1 - y0) / hojd.shape[0]
    ax.contour(hx, hy, (hojd > 0).astype(float), levels=[0.5], colors="#333333",
               linewidths=0.8)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    for ln in linjer:
        ax.plot(ln[:, 0], ln[:, 1], color="#1f77b4", lw=1.2, alpha=0.7)
    if punkt is not None:
        ax.plot(*punkt, marker="o", color="#00a050", ms=6)
    fig.colorbar(im, ax=ax, label=enhet, shrink=0.8)
    ax.set_title(titel, fontsize=10)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.text(0.01, 0.01, TESTVARDE_MARKNING, fontsize=7, color="#a00000")
    fig.tight_layout()
    fig.savefig(sokvag)
    plt.close(fig)


def _kor_demo(args: argparse.Namespace) -> int:
    start = datetime.now().astimezone().isoformat()
    t0 = time.perf_counter()
    ut = Path(args.ut)
    ut.mkdir(parents=True, exist_ok=True)
    rdx = 2.0
    hojd, ox, oy = syntetiskt_kvarter(rdx)
    spec = Domanspec(hojd, rdx, ox, oy, args.dx, 80.0, 60.0, 120.0, 40.0)
    param = LbmParametrar(upprampning_genomstromningar=args.genomstromningar,
                          medel_genomstromningar=args.genomstromningar)
    bib_mapp = ut / "vindbibliotek"
    if (bib_mapp / "bibliotek.json").exists():
        bib = Vindbibliotek.ladda(bib_mapp)
        print(f"Återanvänder vindbiblioteket i {bib_mapp}.")
    else:
        bib = bygg_vindbibliotek(spec, _riktningar(args.riktningar), Inflode(5.0, 10.0, 0.5),
                                 param, args.backend, _logg)
        bib.spara(bib_mapp)
    t_vind = time.perf_counter() - t0

    # två gator genom området (TESTVÄRDEN för emission)
    linjer = [np.array([[ox, oy - 85.0], [ox + 240.0, oy - 85.0]]),
              np.array([[ox + 165.0, oy], [ox + 165.0, oy - 240.0]])]
    # ≈ 20 000 resp. 10 000 fordon/dygn à ~0,6 g NOx/km – TESTVÄRDE (g/(m·s))
    emission = [1.4e-4, 0.7e-4]
    vader = _syntetisk_vader()
    nox = Amne("NOx")
    t1 = time.perf_counter()
    res = berakna_halter(bib, nox, linjer, emission, vader, spec.utbredning, rdx)
    arsmedel_ug = res.statistik.arsmedel() * 1e6
    receptor = (ox + 140.0, oy - 70.0)
    kansl, _ = kanslighet_for_receptor(bib, nox, receptor, 2.0, res.fordelning, spec.utbredning,
                                       rdx)
    t_halt = time.perf_counter() - t1
    luft = np.isfinite(arsmedel_ug)
    no2 = np.full_like(arsmedel_ug, np.nan)
    no2[luft] = no2_fotostationar(arsmedel_ug[luft], 15.0, 50.0, 0.1, 5e-3, 4.4e-4)  # TESTVÄRDEN

    _rita_png(ut / "nox_arsmedel.png", arsmedel_ug, hojd, res.ruta,
              "NOx-tillskott, årsmedel 2 m (µg/m³)", "µg/m³", linjer, None)
    _rita_png(ut / "no2_arsmedel.png", no2, hojd, res.ruta,
              "NO₂ inkl. bakgrund, fotostationär (µg/m³)", "µg/m³", linjer, None)
    _rita_png(ut / "kanslighet_receptor.png", kansl * 1e6, hojd, res.ruta,
              "Känslighet (adjungerad): årsmedel i receptorn per g/s utsläpp",
              "(µg/m³)/(g/s)", linjer, receptor, log=True)
    vind0 = bib.for_riktning(bib.riktningar_grad[0])
    vfart = np.sqrt(np.sum(vind0.u_m_s**2, axis=0))
    from luft.geometri import nivaskiva_till_raster

    skiva, ruta = nivaskiva_till_raster(vind0.grid, vfart, 2.0, rdx, spec.utbredning)
    _rita_png(ut / f"vind_{bib.riktningar_grad[0]:g}.png", skiva, hojd, ruta,
              f"Vindhastighet 2 m, vind från {bib.riktningar_grad[0]:g}°, u_ref 5 m/s",
              "m/s", [], None)

    matvarden = {
        "tid_vindbibliotek_s": round(t_vind, 2),
        "tid_halter_och_adjungerad_s": round(t_halt, 2),
        "mlups_per_riktning": [bib.for_riktning(r).metadata.get("mlups")
                               for r in bib.riktningar_grad],
        "max_no2_ug_m3": float(np.nanmax(no2)),
    }
    res.matvarden.update(matvarden)
    m = _exportera_halt(ut, nox, res, 40.0, [98.0], {"receptor": kansl}, args.backend,
                        {"demo": True, "markning": TESTVARDE_MARKNING, "dx_m": args.dx,
                         "riktningar": list(bib.riktningar_grad)}, [], [TESTVARDE_MARKNING],
                        start)
    (ut / "sammanfattning.json").write_text(
        json.dumps(res.matvarden, indent=2, ensure_ascii=False, default=float), encoding="utf-8")
    print(json.dumps(res.matvarden, indent=2, ensure_ascii=False, default=float))
    print(f"Demo klar i {ut} ({m.name}).")
    return 0


# ---------------------------------------------------------------- vindkomfort


def _rita_klasser(sokvag: Path, klass: FloatArray, namn: tuple[str, ...], hojd: FloatArray,
                  ruta: RasterRuta, titel: str, farger: list[str]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from matplotlib.patches import Patch

    x0, y0, x1, y1 = ruta.utbredning
    cmap = ListedColormap(farger[: len(namn)])
    norm = BoundaryNorm(np.arange(-0.5, len(namn) + 0.5), cmap.N)
    fig, ax = plt.subplots(figsize=(7, 6), dpi=130)
    ax.imshow(klass, extent=(x0, x1, y0, y1), cmap=cmap, norm=norm, interpolation="nearest")
    hx = x0 + (np.arange(hojd.shape[1]) + 0.5) * (x1 - x0) / hojd.shape[1]
    hy = y1 - (np.arange(hojd.shape[0]) + 0.5) * (y1 - y0) / hojd.shape[0]
    ax.contour(hx, hy, (hojd > 0).astype(float), levels=[0.5], colors="#333333", linewidths=0.8)
    ax.legend(handles=[Patch(color=farger[i], label=n) for i, n in enumerate(namn)],
              loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8, frameon=False)
    ax.set_title(titel, fontsize=10)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.text(0.01, 0.01, TESTVARDE_MARKNING, fontsize=7, color="#a00000")
    fig.tight_layout()
    fig.savefig(sokvag)
    plt.close(fig)


KLASSFARGER = ["#1a9850", "#91cf60", "#fee08b", "#fc8d59", "#d73027"]
FARAFARGER = ["#f7f7f7", "#fdae61", "#a50026"]


def _skriv_komfort(ut: Path, res: Komfortresultat, prefix: str) -> list[Path]:
    from luft.export import skriv_geotiff_float

    filer = [
        skriv_geotiff_float(ut / f"{prefix}komfortklass.tif", res.komfortklass, res.ruta,
                            "Komfortklass (index i kriteriets klasslista)", "klass"),
        skriv_geotiff_float(ut / f"{prefix}faraklass.tif", res.faraklass, res.ruta,
                            "Faraklass (index i kriteriets klasslista)", "klass"),
    ]
    for v, andel in res.andel_komfort.items():
        filer.append(skriv_geotiff_float(ut / f"{prefix}andel_over_{v:g}ms.tif", andel * 100,
                                         res.ruta, f"Andel timmar över {v:g} m/s", "%"))
    return filer


def _komfort_manifest(ut: Path, filer: list[Path], parametrar: dict[str, Any],
                      matvarden: dict[str, Any], varningar: list[str], start: str) -> Path:
    from gemensamt.hash import sha256_fil
    from gemensamt.manifest import FilPost, skapa_manifest, skriv_manifest

    m = skapa_manifest(
        "luft-vindkomfort",
        parametrar=parametrar,
        utdata=[FilPost("komfort", f.name, sha256_fil(f)) for f in filer],
        matvarden=matvarden,
        varningar=varningar,
        start=start,
    )
    return skriv_manifest(m, ut / "run.json")


def _vader_for_komfort(args: argparse.Namespace, bib: Vindbibliotek) -> Vaderserie:
    vader = Vaderserie.las_csv(args.vader)
    if args.omrakna:
        inf = bib.for_riktning(bib.riktningar_grad[0]).inflode
        u = omrakna_vindhastighet(vader.vindhastighet_m_s, args.omrakna[0], args.omrakna[1],
                                  inf.z_ref_m, inf.z0_m, args.omrakna[2])
        vader = Vaderserie(vader.tid, vader.vindriktning_grad, u, vader.giltig)
    return vader


def _kor_komfort(args: argparse.Namespace) -> int:
    start = datetime.now().astimezone().isoformat()
    spec = _spec(args)
    bib = Vindbibliotek.ladda(args.bibliotek)
    vader = _vader_for_komfort(args, bib)
    ut = Path(args.ut)
    ut.mkdir(parents=True, exist_ok=True)
    efter = berakna_komfort(bib, vader, spec.utbredning, spec.raster_dx_m, args.kriterium,
                            args.toppfaktor)
    filer = _skriv_komfort(ut, efter, "")
    matvarden: dict[str, Any] = {}
    varningar = list(efter.varningar)
    if args.fore:
        fore = berakna_komfort(Vindbibliotek.ladda(args.fore), vader, spec.utbredning,
                               spec.raster_dx_m, args.kriterium, args.toppfaktor)
        filer += _skriv_komfort(ut, fore, "fore_")
        d = jamfor(fore, efter)
        from luft.export import skriv_geotiff_float

        filer.append(skriv_geotiff_float(ut / "klass_skillnad.tif", d["klass_skillnad"],
                                         efter.ruta, "Komfortklass efter − före", "klass"))
        matvarden.update({k: v for k, v in d.items() if isinstance(v, float)})
        varningar += fore.varningar
    m = _komfort_manifest(ut, filer, {"kriterium": efter.kriterium.namn,
                                      "kalla": efter.kriterium.kalla,
                                      "toppfaktor": args.toppfaktor}, matvarden,
                          sorted(set(varningar)), start)
    print(f"Vindkomfort skriven i {ut} ({m.name}).")
    return 0


def _kor_komfortdemo(args: argparse.Namespace) -> int:
    start = datetime.now().astimezone().isoformat()
    ut = Path(args.ut)
    ut.mkdir(parents=True, exist_ok=True)
    rdx = 2.0
    hojd_fore, ox, oy = syntetiskt_kvarter(rdx)
    s = rdx
    hojd_fore[int(100 / s):int(130 / s), int(100 / s):int(130 / s)] = 18.0  # inget höghus
    hojd_efter = hojd_fore.copy()
    hojd_efter[int(100 / s):int(130 / s), int(100 / s):int(130 / s)] = args.hushojd
    param = LbmParametrar(upprampning_genomstromningar=args.genomstromningar,
                          medel_genomstromningar=args.genomstromningar)
    hojd_doman = max(80.0, 2.0 * args.hushojd)
    bibliotek: dict[str, Vindbibliotek] = {}
    for namn, h in (("fore", hojd_fore), ("efter", hojd_efter)):
        mapp = ut / f"vindbibliotek_{namn}"
        if (mapp / "bibliotek.json").exists():
            bibliotek[namn] = Vindbibliotek.ladda(mapp)
            continue
        spec = Domanspec(h, rdx, ox, oy, args.dx, hojd_doman, 80.0, 160.0, 60.0)
        bibliotek[namn] = bygg_vindbibliotek(spec, _riktningar(args.riktningar),
                                             Inflode(5.0, 10.0, 0.5), param, args.backend, _logg)
        bibliotek[namn].spara(mapp)
    utb = Domanspec(hojd_fore, rdx, ox, oy, args.dx, hojd_doman, 0, 0, 0).utbredning
    # syntetisk referensvind (TESTVÄRDE), dominerande sydväst
    rng = np.random.default_rng(7)
    n = 8760
    vader = Vaderserie.skapa((225.0 + rng.normal(0.0, 60.0, n)) % 360.0,
                             rng.weibull(2.0, n) * 6.0)
    res = {k: berakna_komfort(b, vader, utb, rdx, "nen8100") for k, b in bibliotek.items()}
    d = jamfor(res["fore"], res["efter"])
    k = res["efter"].kriterium
    for namn, r in res.items():
        _rita_klasser(ut / f"komfort_{namn}.png", r.komfortklass, k.komfort_klasser,
                      hojd_fore if namn == "fore" else hojd_efter, r.ruta,
                      f"NEN 8100 komfortklass, {'med höghus' if namn == 'efter' else 'nuläge'}",
                      KLASSFARGER)
        _rita_klasser(ut / f"fara_{namn}.png", r.faraklass, k.fara_klasser,
                      hojd_fore if namn == "fore" else hojd_efter, r.ruta,
                      f"NEN 8100 vindfara, {namn}", FARAFARGER)
    skillnad = np.asarray(d["andel_skillnad"], dtype=np.float64) * 100
    _rita_png(ut / "andel_over_5ms_skillnad.png", skillnad,
              hojd_efter, res["efter"].ruta, "Ändring i andel timmar > 5 m/s (procentenheter)",
              "procentenheter", [], None, divergerande=True)
    filer = _skriv_komfort(ut, res["efter"], "") + _skriv_komfort(ut, res["fore"], "fore_")
    matvarden = {kk: v for kk, v in d.items() if isinstance(v, float)}
    _komfort_manifest(ut, filer, {"demo": True, "markning": TESTVARDE_MARKNING,
                                  "hushojd_m": args.hushojd, "dx_m": args.dx,
                                  "riktningar": args.riktningar}, matvarden,
                      [TESTVARDE_MARKNING] + res["efter"].varningar, start)
    print(json.dumps(matvarden, indent=2, ensure_ascii=False))
    print(f"Komfortdemo klar i {ut}.")
    return 0


def kor(args: argparse.Namespace) -> int:
    """Kör ett `floden luft`-underkommando."""
    try:
        if args.luft_kommando == "vind":
            return _kor_vind(args)
        if args.luft_kommando == "halt":
            return _kor_halt(args)
        if args.luft_kommando == "demo":
            return _kor_demo(args)
        if args.luft_kommando == "komfort":
            return _kor_komfort(args)
        if args.luft_kommando == "komfortdemo":
            return _kor_komfortdemo(args)
    except LuftFel as exc:
        print(f"Fel i luftkörningen: {exc}", file=sys.stderr)
        return 1
    print("Ange underkommando: vind, halt, komfort, demo eller komfortdemo.", file=sys.stderr)
    return 2
