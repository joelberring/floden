"""`floden dagsljus ...`: demo på syntetiskt kvarter och mätning av prestanda.

floden dagsljus demo  [--ut docs/bilder] [--riktningar 180] [--datum 2026-03-20]
floden dagsljus demo --fore-efter [--ut docs/bilder]   (skuggstudie före/efter, K1 v2 + K9 + K20)
floden dagsljus bench [--storlek-m 1000] [--cell-m 1.0] [--riktningar 64] [--punkter 20000]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from dagsljus.horisont import (
    Hojdfalt,
    HorisontFel,
    MaxPyramid,
    horisont_mipmap,
    horisont_riktning_ruta,
    riktningar_rad,
)
from dagsljus.matt import SoltimmarAckumulator
from dagsljus.solposition import dygnsfonster_utc, lat_lon_fran_3011, solposition

# Stockholms stadshus ligger i närheten; kvarteret är syntetiskt och ligger här bara för solläget.
DEMO_E_M = 151_000.0
DEMO_N_M = 6_580_800.0


def registrera(subparsers: Any) -> None:
    """Lägger till `dagsljus` med underkommandon i Flodens huvudparser."""
    p = subparsers.add_parser(
        "dagsljus", help="Dagsljus: horisontprofiler, soltimmar, SVF och VSC (demo, mätning)."
    )
    sub = p.add_subparsers(dest="dagsljus_kommando")

    d = sub.add_parser("demo", help="Syntetiskt kvarter: soltimmar och SVF som PNG + K0-manifest.")
    d.add_argument("--ut", default="docs/bilder", help="Utmapp (standard: docs/bilder).")
    d.add_argument("--riktningar", type=int, default=180, help="Azimutriktningar K.")
    d.add_argument("--datum", default="2026-03-20", help="Datum (vårdagjämning).")
    d.add_argument("--steg-min", type=float, default=10.0, help="Tidssteg i minuter.")
    d.add_argument(
        "--fore-efter",
        action="store_true",
        help="Bygg ett K1 v2-kvarter, kör exempelfrågan (K20) för bas och två K9-scenarier och "
        "rita skillnadskartor i soltimmar och VSC. Övriga flaggor gäller då inte (frågefilen "
        "styr).",
    )
    d.add_argument("--fraga", default="exempel/fragor/dagsljus_fore_efter.toml")
    d.add_argument("--data", default="data/cache/exempel/dagsljus-kvarter")
    d.add_argument("--cache", default="data/cache/karna")
    d.add_argument("--resultat", default="resultat/dagsljus-fore-efter")

    b = sub.add_parser("bench", help="Mät prestanda (celler/s, punkter·riktningar/s).")
    b.add_argument("--storlek-m", type=float, default=1000.0, help="Sidlängd på beräkningsytan.")
    b.add_argument("--cell-m", type=float, default=1.0)
    b.add_argument("--riktningar", type=int, default=64)
    b.add_argument("--hojd-max-m", type=float, default=60.0)
    b.add_argument("--h-min-grader", type=float, default=5.0)
    b.add_argument(
        "--riktningar-matade",
        type=int,
        default=2,
        help="Antal av K riktningar som körs och tidtas (resten extrapoleras ej).",
    )
    b.add_argument("--punkter", type=int, default=20000, help="Fasadpunkter för mipmap-mätningen.")
    b.add_argument("--halvplansriktningar", type=int, default=32)
    b.add_argument("--ut", default=None, help="Skriv mätvärdena som JSON hit.")


def kvarter(sida_m: float = 300.0, cell_m: float = 1.0) -> NDArray[np.float64]:
    """Syntetiskt kvarter (m, relativt mark): 2 x 2 slutna kvarter om 120 m med 14 m djupa,
    18 m höga huskroppar, gator om 20 m, ett 45 m torn i ett gårdsrum och ett 4 m lågt hus."""
    n = int(round(sida_m / cell_m))
    z = np.zeros((n, n))

    def box(x0: float, x1: float, y0: float, y1: float, h: float) -> None:
        """Rektangel i meter från sydvästra hörnet (y uppåt = norr)."""
        j0, j1 = int(round(x0 / cell_m)), int(round(x1 / cell_m))
        i0, i1 = int(round((sida_m - y1) / cell_m)), int(round((sida_m - y0) / cell_m))
        z[i0:i1, j0:j1] = np.maximum(z[i0:i1, j0:j1], h)

    for bx in (20.0, 160.0):
        for by in (20.0, 160.0):
            for x0, x1, y0, y1 in (
                (bx, bx + 120, by, by + 14),
                (bx, bx + 120, by + 106, by + 120),
                (bx, bx + 14, by, by + 120),
                (bx + 106, bx + 120, by, by + 120),
            ):
                box(x0, x1, y0, y1, 18.0)
    box(60.0, 80.0, 60.0, 80.0, 45.0)  # torn i det sydvästra gårdsrummet
    box(200.0, 215.0, 200.0, 212.0, 4.0)  # lågt hus i det nordöstra
    return z


def _farg(v: NDArray[np.float64], lo: float, hi: float) -> NDArray[np.uint8]:
    """Sekventiell färgskala (mörk -> ljus gul), RGB."""
    stopp = np.array(
        [[26, 35, 126], [21, 101, 192], [0, 151, 167], [67, 160, 71], [253, 216, 53]], dtype=float
    )
    t = np.clip((v - lo) / (hi - lo), 0.0, 1.0) * (len(stopp) - 1)
    i0 = np.minimum(np.floor(t).astype(int), len(stopp) - 2)
    f = (t - i0)[..., None]
    return np.asarray(stopp[i0] * (1 - f) + stopp[i0 + 1] * f, dtype=np.uint8)


def _typsnitt() -> tuple[Any, bool]:
    """(typsnitt, stöder_aao). Faller tillbaka på PIL:s standardtypsnitt utan å/ä/ö."""
    from PIL import ImageFont

    for namn in ("DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(namn, 14), True
        except OSError:
            continue
    return ImageFont.load_default(), False


def _skriv_png(
    sokvag: Path,
    paneler: list[tuple[str, NDArray[np.float64], float, float, str]],
    bygg: NDArray[np.bool_],
    skala: int = 3,
) -> None:
    from PIL import Image, ImageDraw

    h, b = bygg.shape
    marg, topp, bar = 20, 40, 70
    w_tot = len(paneler) * (b * skala + bar + marg) + marg
    h_tot = topp + h * skala + 70
    bild = Image.new("RGB", (w_tot, h_tot), (255, 255, 255))
    rita = ImageDraw.Draw(bild)
    font, aao = _typsnitt()

    def text(xy: tuple[int, int], s: str, fill: tuple[int, int, int]) -> None:
        if not aao:
            s = s.translate(str.maketrans("åäöÅÄÖ", "aaoAAO"))
        rita.text(xy, s, fill=fill, font=font)

    for k, (titel, data, lo, hi, enhet) in enumerate(paneler):
        x0 = marg + k * (b * skala + bar + marg)
        rgb = _farg(data, lo, hi)
        rgb[bygg] = (70, 70, 70)
        kuv = Image.fromarray(rgb).resize((b * skala, h * skala), Image.Resampling.NEAREST)
        bild.paste(kuv, (x0, topp))
        text((x0, 12), titel, (0, 0, 0))
        for i in range(h * skala):  # färgfält
            c = _farg(np.array([hi - (hi - lo) * i / (h * skala - 1)]), lo, hi)[0]
            rita.rectangle(
                [x0 + b * skala + 12, topp + i, x0 + b * skala + 26, topp + i],
                fill=tuple(int(v) for v in c),
            )
        text((x0 + b * skala + 30, topp), f"{hi:g} {enhet}", (0, 0, 0))
        text((x0 + b * skala + 30, topp + h * skala - 16), f"{lo:g}", (0, 0, 0))
        text(
            (x0, topp + h * skala + 8),
            "Grå = byggnad (DSM). Norr uppåt. Syntetiskt kvarter.",
            (60, 60, 60),
        )
    sokvag.parent.mkdir(parents=True, exist_ok=True)
    bild.save(sokvag)


def _kor_demo(args: argparse.Namespace) -> int:
    from gemensamt.hash import sha256_fil
    from gemensamt.manifest import FilPost, skapa_manifest, skriv_manifest

    K = int(args.riktningar)
    cell = 1.0
    sida = 300.0
    hojd_max, h_min = 45.0, 5.0
    radie = hojd_max / math.tan(math.radians(h_min))
    pad = int(math.ceil(radie / cell)) + 1
    kv = kvarter(sida, cell)
    n = kv.shape[0]
    z = np.zeros((n + 2 * pad, n + 2 * pad))
    z[pad : pad + n, pad : pad + n] = kv
    falt = Hojdfalt(z, cell, DEMO_E_M - pad * cell, DEMO_N_M + pad * cell)
    lat, lon = lat_lon_fran_3011(DEMO_E_M + sida / 2, DEMO_N_M - sida / 2)
    tider = dygnsfonster_utc(args.datum, lon, float(args.steg_min))
    sol_h, sol_az = solposition(tider, lat, lon)
    fonster = (pad, pad + n, pad, pad + n)
    ack = SoltimmarAckumulator(K, sol_h, sol_az, float(args.steg_min) / 60.0, (n, n))
    summa = np.zeros((n, n))
    t0 = time.perf_counter()
    for k, phi in enumerate(riktningar_rad(K)):
        h, _ = horisont_riktning_ruta(falt, float(phi), radie, fonster=fonster)
        ack.mata(k, h)
        summa += np.sin(h.astype(np.float64)) ** 2
    kortid = time.perf_counter() - t0
    timmar = ack.timmar()
    svf = 1.0 - summa / K
    bygg = kv > 0.0
    ut = Path(args.ut)
    png = ut / "dagsljus-demo-soltimmar-svf.png"
    dag = float(np.count_nonzero(sol_h > 0) * float(args.steg_min) / 60.0)
    _skriv_png(
        png,
        [
            (f"Soltimmar {args.datum} (dygnets längd {dag:.1f} h)", timmar, 0.0, 12.0, "h"),
            ("Himmelsexponering SVF (horisontell yta)", svf, 0.0, 1.0, ""),
        ],
        bygg,
    )
    gm = ~bygg
    matvarden = {
        "soltimmar_marken_min_h": float(timmar[gm].min()),
        "soltimmar_marken_medel_h": float(timmar[gm].mean()),
        "soltimmar_marken_max_h": float(timmar[gm].max()),
        "svf_marken_min": float(svf[gm].min()),
        "svf_marken_medel": float(svf[gm].mean()),
        "dygnets_langd_h": dag,
        "kortid_s": round(kortid, 2),
    }
    m = skapa_manifest(
        "dagsljus-demo",
        parametrar={
            "azimutriktningar": K,
            "datum": args.datum,
            "steg_min": float(args.steg_min),
            "hojd_max_m": hojd_max,
            "h_min_grader": h_min,
            "max_avstand_m": radie,
            "cellstorlek_m": cell,
            "kvarter_sida_m": sida,
            "anm": "Syntetiskt kvarter på platt mark; inget finns bortom kvarteret.",
        },
        utdata=[FilPost("soltimmar_svf", png.name, sha256_fil(png))],
        matvarden=matvarden,
        varningar=[],
    )
    skriv_manifest(m, ut / "dagsljus-demo.run.json")
    print(json.dumps(matvarden, indent=2, ensure_ascii=False))
    print(f"Skrev {png} och {ut / 'dagsljus-demo.run.json'}")
    return 0


def _kor_bench(args: argparse.Namespace) -> int:
    cell = float(args.cell_m)
    radie = float(args.hojd_max_m) / math.tan(math.radians(float(args.h_min_grader)))
    n = int(round(float(args.storlek_m) / cell))
    pad = int(math.ceil(radie / cell)) + 1
    rng = np.random.default_rng(0)
    z = np.zeros((n + 2 * pad, n + 2 * pad))
    # Slumpmässiga huskroppar över hela fältet (tidtagningen beror inte på höjdvärdena).
    antal = int(z.size / (60.0 / cell) ** 2)
    for _ in range(antal):
        i, j = int(rng.integers(0, z.shape[0] - 20)), int(rng.integers(0, z.shape[1] - 20))
        z[i : i + int(rng.integers(5, 20)), j : j + int(rng.integers(5, 20))] = rng.uniform(6, 40)
    falt = Hojdfalt(z, cell, 0.0, z.shape[0] * cell)
    fonster = (pad, pad + n, pad, pad + n)
    antal_k = int(args.riktningar_matade)
    phis = riktningar_rad(int(args.riktningar))
    steg = []
    t0 = time.perf_counter()
    for phi in phis[:antal_k]:
        h, _ = horisont_riktning_ruta(falt, float(phi), radie, fonster=fonster)
        steg.append(h.shape)
    t_grid = time.perf_counter() - t0
    celler = n * n
    res: dict[str, Any] = {
        "yta_m": float(args.storlek_m),
        "cell_m": cell,
        "celler": celler,
        "faltets_celler": int(z.size),
        "max_avstand_m": radie,
        "riktningar_K": int(args.riktningar),
        "riktningar_tidtagna": antal_k,
        "skiva_s_per_riktning": t_grid / antal_k,
        "skiva_celler_riktningar_per_s": celler * antal_k / t_grid,
        "skiva_s_for_K_riktningar_uppskattat": t_grid / antal_k * int(args.riktningar),
    }
    # Mipmap på slumpmässiga fasadpunkter (inom fönstret) och halvplansriktningar.
    p = int(args.punkter)
    if p > 0:
        kf = int(args.halvplansriktningar)
        x = rng.uniform(pad * cell, (pad + n) * cell, p)
        y = rng.uniform(pad * cell, (pad + n) * cell, p)
        oi = np.floor((falt.y_max_m - y) / cell).astype(int)
        oj = np.floor(x / cell).astype(int)
        z0 = z[oi, oj] + 1.5
        phi = rng.uniform(0, 2 * math.pi, (p, 1)) + (np.arange(kf)[None, :] - kf / 2) * math.pi / kf
        t0 = time.perf_counter()
        pyr = MaxPyramid.bygg(falt)
        t_pyr = time.perf_counter() - t0
        t0 = time.perf_counter()
        horisont_mipmap(
            falt,
            np.repeat(x, kf),
            np.repeat(y, kf),
            np.repeat(z0, kf),
            phi.ravel(),
            radie,
            pyramid=pyr,
        )
        t_mm = time.perf_counter() - t0
        res.update(
            {
                "mipmap_punkter": p,
                "mipmap_riktningar_per_punkt": kf,
                "mipmap_pyramid_s": t_pyr,
                "mipmap_s": t_mm,
                "mipmap_punkter_riktningar_per_s": p * kf / t_mm,
            }
        )
    print(json.dumps(res, indent=2))
    if args.ut:
        Path(args.ut).parent.mkdir(parents=True, exist_ok=True)
        Path(args.ut).write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    return 0


def kor(args: argparse.Namespace) -> int:
    """Kör ett `floden dagsljus`-underkommando."""
    try:
        if args.dagsljus_kommando == "demo":
            if args.fore_efter:
                from dagsljus.fore_efter_demo import kor_fore_efter

                return kor_fore_efter(args)
            return _kor_demo(args)
        if args.dagsljus_kommando == "bench":
            return _kor_bench(args)
    except (HorisontFel, ValueError) as exc:
        print(f"Fel i dagsljusberäkningen: {exc}", file=sys.stderr)
        return 1
    print("Ange ett underkommando: demo eller bench (se --help).", file=sys.stderr)
    return 2
