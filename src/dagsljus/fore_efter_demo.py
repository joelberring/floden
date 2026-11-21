"""`floden dagsljus demo --fore-efter`: skuggstudie före/efter på ett syntetiskt K1 v2-kvarter.

Bygger kvarteret (`dagsljus.exempelkvarter`), kör exempelfrågan (K20) genom kärnan och ritar
skillnadskartor: soltimmar (efter minus före) på mark och tak, och VSC (procentenheter) på
fasadpunkter ur K11. Bilden får ett K0-manifest.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from dagsljus.cli import _farg, _typsnitt
from dagsljus.exempelkvarter import DEMO_E_M, DEMO_N_M, DEMO_SIDA_M, bygg_demokvarter
from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost, skapa_manifest, skriv_manifest
from karna.geometri import las_ytmodell
from karna.korplan import kor_utredning, planera
from karna.resultatcache import ResultatCache
from karna.utredning import las_fraga
from testsvit.k1_io import las_geotiff

PNG_NAMN = "dagsljus-fore-efter.png"
SCENARIER = ("hojd", "nytt_hus")


def _sy_raster(mappar: list[Path], fil: str, x0: float, y1: float, n: int) -> NDArray[np.float64]:
    ark = np.full((n, n), np.nan)
    for m in mappar:
        a, geo = las_geotiff(m / fil)
        j0, i0 = round(geo.x_min - x0), round(y1 - geo.y_max)
        ark[i0 : i0 + a.shape[0], j0 : j0 + a.shape[1]] = a
    if np.isnan(ark).any():
        raise ValueError(f"{fil}: rutorna täcker inte hela kvarteret")
    return ark


def _farg_div(v: NDArray[np.float64], gr: float) -> NDArray[np.uint8]:
    """Divergerande skala: blå (negativ) - vit (0) - röd (positiv), mättad vid +-gr."""
    t = np.clip(v / gr, -1.0, 1.0)[..., None]
    vit = np.array([255.0, 255.0, 255.0])
    bla = np.array([33.0, 102.0, 172.0])
    rod = np.array([178.0, 24.0, 43.0])
    return np.asarray(np.where(t < 0, vit + (bla - vit) * (-t), vit + (rod - vit) * t), np.uint8)


def _vsc_punkter(mappar: list[Path]) -> dict[str, tuple[float, float, float | None, str]]:
    ut: dict[str, tuple[float, float, float | None, str]] = {}
    for m in mappar:
        with (m / "vsc.csv").open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                v = float(r["vsc_procent"]) if r["vsc_procent"] else None
                ut[r["fasad_id"]] = (float(r["x_m"]), float(r["y_m"]), v, r["byggnad_id"])
    return ut


def _rita(
    sokvag: Path,
    bas_sol: NDArray[np.float64],
    diff_sol: dict[str, NDArray[np.float64]],
    fotavtryck: dict[str, NDArray[np.bool_]],
    dvsc: dict[str, list[tuple[float, float, float]]],
    hr: dict[str, float],
    vr: dict[str, float],
) -> None:
    from PIL import Image, ImageDraw

    skala, marg, topp = 2, 20, 36
    n = bas_sol.shape[0]
    b = n * skala
    kol = 3
    bild = Image.new("RGB", (kol * (b + marg) + marg + 70, 2 * (b + topp + 40) + marg), "white")
    rita = ImageDraw.Draw(bild)
    font, aao = _typsnitt()

    def text(xy: tuple[int, int], s: str) -> None:
        if not aao:
            s = s.translate(str.maketrans("åäöÅÄÖ", "aaoAAO"))
        rita.text(xy, s, fill=(0, 0, 0), font=font)

    def panel(k: int, rad: int, titel: str, rgb: NDArray[np.uint8], bygg: NDArray[np.bool_]) -> Any:
        x0, y0 = marg + k * (b + marg), marg + rad * (b + topp + 40)
        rgb = rgb.copy()
        rgb[bygg] = (70, 70, 70)
        kuv = Image.fromarray(rgb).resize((b, b), Image.Resampling.NEAREST)
        bild.paste(kuv, (x0, y0 + topp))
        text((x0, y0 + 8), titel)
        return x0, y0 + topp

    bas_bygg = fotavtryck["bas"]
    x0, y0 = panel(0, 0, "Soltimmar före (vårdagjämning), h", _farg(bas_sol, 0.0, 12.0), bas_bygg)
    text((x0, y0 + b + 6), "Grå = byggnad. Norr uppåt.")
    for k, sc in enumerate(SCENARIER, start=1):
        titel = {"hojd": "Höjt hus", "nytt_hus": "Nytt hus"}[sc]
        px, py = panel(
            k,
            0,
            f"{titel}: soltimmar efter minus före, h",
            _farg_div(diff_sol[sc], hr[sc]),
            fotavtryck[sc],
        )
        text((px, py + b + 6), f"Blå = färre soltimmar (skala +-{hr[sc]:g} h)")
        px, py = panel(
            k,
            1,
            f"{titel}: VSC efter minus före, %-enheter",
            np.full((n, n, 3), 235, np.uint8),
            fotavtryck[sc],
        )
        for x, y, d in dvsc[sc]:
            cx, cy = px + (x - DEMO_E_M) * skala, py + (DEMO_N_M - y) * skala
            c = tuple(int(v) for v in _farg_div(np.array([d]), vr[sc])[0])
            rita.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=c, outline=(0, 0, 0))
        text((px, py + b + 6), f"Punkter = K11-fasadpunkter (skala +-{vr[sc]:g})")
    sokvag.parent.mkdir(parents=True, exist_ok=True)
    bild.save(sokvag)


def kor_fore_efter(args: argparse.Namespace) -> int:
    """Bygger kvarteret, kör frågan och skriver PNG + K0-manifest i `args.ut`."""
    data = Path(args.data)
    bygg_demokvarter(data)
    fraga = las_fraga(args.fraga)
    cache = ResultatCache(args.cache)
    plan = planera(fraga, cache)
    res = kor_utredning(fraga, cache, args.resultat, plan=plan)
    ut = Path(res.utmapp)
    x0, y1, n = DEMO_E_M, DEMO_N_M, int(DEMO_SIDA_M)
    sol = {
        s: _sy_raster(sorted((ut / "sol" / s).glob("mm_*")), "soltimmar.tif", x0, y1, n)
        for s in ("bas", *SCENARIER)
    }
    fot = {}
    dsm_per: dict[str, NDArray[np.float64]] = {}
    for s in ("bas", *SCENARIER):
        dsm = np.full((n, n), np.nan)
        for r in plan.scenarier[s].rutor:
            z, geo = las_ytmodell(r.mapp)
            j0, i0 = (
                round((geo.x_min - x0) / geo.pixelstorlek_x),
                round((y1 - geo.y_max) / geo.pixelstorlek_x),
            )
            dsm[i0 : i0 + z.shape[0], j0 : j0 + z.shape[1]] = z  # 1 m-rutor: en cell = 1 m
        dsm_per[s] = dsm
        fot[s] = dsm > 0.01
    diff = {s: sol[s] - sol["bas"] for s in SCENARIER}
    vsc0 = _vsc_punkter(sorted((ut / "vsc" / "bas").glob("mm_*")))
    dvsc: dict[str, list[tuple[float, float, float]]] = {}
    matv: dict[str, Any] = {"soltimmar_bas_medel_h": float(sol["bas"].mean())}
    for s in SCENARIER:
        v1 = _vsc_punkter(sorted((ut / "vsc" / s).glob("mm_*")))
        gemensamma = [
            k for k in v1 if k in vsc0 and v1[k][2] is not None and vsc0[k][2] is not None
        ]
        dvsc[s] = [
            (v1[k][0], v1[k][1], float(v1[k][2] or 0) - float(vsc0[k][2] or 0)) for k in gemensamma
        ]
        fler = (diff[s] > 1e-9) & (dsm_per[s] == dsm_per["bas"])  # ändrade celler byter yta
        matv.update(
            {
                f"{s}_soltimmar_diff_min_h": float(diff[s].min()),
                f"{s}_celler_med_fler_soltimmar_utanfor_fotavtryck": int(fler.sum()),
                f"{s}_celler_forandrade": int(np.count_nonzero(diff[s])),
                f"{s}_vsc_diff_min_procentenheter": min(d for _, _, d in dvsc[s]),
                f"{s}_vsc_diff_max_procentenheter": max(d for _, _, d in dvsc[s]),
                f"{s}_vsc_punkter_jamforda": len(gemensamma),
            }
        )
    hr = {s: max(1.0, float(np.ceil(-diff[s].min()))) for s in SCENARIER}
    vr = {s: max(1.0, float(np.ceil(max(abs(d) for _, _, d in dvsc[s])))) for s in SCENARIER}
    png = Path(args.ut) / PNG_NAMN
    _rita(png, sol["bas"], diff, fot, dvsc, hr, vr)
    varn = sorted({v for j in res.sammanstallning["jobb"] for v in j["varningar"]})
    matv.update({k: v for k, v in res.sammanstallning["sammanfattning"].items()})
    indata = [FilPost("fraga", str(args.fraga), sha256_fil(Path(args.fraga)))]
    for sc in SCENARIER:
        k9 = data / f"k9_{sc}.json"
        indata.append(FilPost(f"k9_{sc}", k9.name, sha256_fil(k9)))
    m = skapa_manifest(
        "dagsljus-fore-efter",
        indata=indata,
        parametrar={
            "fraga": fraga.fraga_id,
            "cellstorlek_m": 1.0,
            "kvarter_sida_m": DEMO_SIDA_M,
            "anm": "Syntetiskt kvarter på platt mark (K1 v2); nycklar i sammanstallning.json.",
        },
        utdata=[FilPost("fore_efter", png.name, sha256_fil(png))],
        matvarden=matv,
        varningar=varn,
    )
    skriv_manifest(m, Path(args.ut) / "dagsljus-fore-efter.run.json")
    print(json.dumps(matv, indent=2, ensure_ascii=False))
    print(f"Skrev {png} och {Path(args.ut) / 'dagsljus-fore-efter.run.json'}")
    return 0
