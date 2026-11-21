"""Jämför Rust-kärnan med Python-lösaren (facit) på A1, A4, EA1, EA2 och EA8A.

Kör: `PYTHONPATH=src python3 rust/tvad_karna/validering.py` (binären måste vara byggd).
Skriver en tabell i Markdown.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from sankgraf.typer import MARKSLAG_HARDGJORT
from testsvit.ea_fall import (
    ea_test1_sanka_over_troskel,
    ea_test2_sanknat_4x4,
    ea_test8a_urbant_regn,
)
from testsvit.k1_io import las_geotiff, las_regn_csv
from tvad.lokal_troghet import TvadResultat, kor_2d_lokal_troghet
from tvad.rust_backend import kor_2d_lokal_troghet_rust

ROT = Path(__file__).resolve().parents[2]


def a_fall(namn: str) -> tuple[Any, ...]:
    mapp = ROT / "testsvit" / namn / "indata"
    mark, geo = las_geotiff(mapp / "mark.tif")
    slag, _ = las_geotiff(mapp / "markslag.tif")
    return np.asarray(mark, np.float32), np.asarray(slag, np.uint8), geo


def fall() -> list[tuple[str, Callable[[Callable[..., TvadResultat]], TvadResultat]]]:
    ut: list[tuple[str, Callable[[Callable[..., TvadResultat]], TvadResultat]]] = []
    m1, s1, g1 = a_fall("A1_enkel_sanka")
    rt1, ri1 = [0.0, 120.0, 121.0, 180.0], [360.0, 360.0, 0.0, 0.0]
    ut.append(("A1 gles", lambda f: f(m1, s1, g1, rt1, ri1, gles_front=True)))
    ut.append(("A1 tät", lambda f: f(m1, s1, g1, rt1, ri1, gles_front=False)))
    m4, s4, g4 = a_fall("A4_lutande_plan")
    regn = las_regn_csv(ROT / "testsvit" / "A4_lutande_plan" / "indata" / "regn.csv")
    rt4, ri4 = [p[0] for p in regn], [p[1] for p in regn]
    ut.append(("A4 öppna ränder", lambda f: f(m4, s4, g4, rt4, ri4, slut_tid_s=180.0)))

    e1 = ea_test1_sanka_over_troskel(slut_tid_s=3600.0)
    man1 = {MARKSLAG_HARDGJORT: e1.manning_n, 0: e1.manning_n}
    ut.append(("EA1", lambda f: f(e1.mark_z, e1.markslag, e1.geo, e1.regn_tid_s,
                                  e1.regn_intensitet_mm_h, slut_tid_s=e1.slut_tid_s,
                                  oppna_rander=False, manning_tabell=man1)))
    e2 = ea_test2_sanknat_4x4(slut_tid_s=1500.0)
    man2 = {MARKSLAG_HARDGJORT: e2.manning_n, 0: e2.manning_n}
    ut.append(("EA2", lambda f: f(e2.mark_z, e2.markslag, e2.geo, e2.regn_tid_s,
                                  e2.regn_intensitet_mm_h, slut_tid_s=e2.slut_tid_s,
                                  oppna_rander=False, manning_tabell=man2)))
    e8 = ea_test8a_urbant_regn(slut_tid_s=420.0)
    man8 = {MARKSLAG_HARDGJORT: e8.manning_n, 0: e8.manning_n}
    ut.append(("EA8A", lambda f: f(e8.mark_z, e8.markslag, e8.geo, e8.regn_tid_s,
                                   e8.regn_intensitet_mm_h, slut_tid_s=e8.slut_tid_s,
                                   oppna_rander=False, manning_tabell=man8, min_djup_m=2e-3)))
    return ut


def maxdiff(a: np.ndarray, b: np.ndarray) -> float:
    a64, b64 = a.astype(np.float64), b.astype(np.float64)
    if not np.array_equal(np.isnan(a64), np.isnan(b64)):
        return float("inf")
    ok = ~np.isnan(a64)
    return float(np.max(np.abs(a64[ok] - b64[ok]))) if ok.any() else 0.0


def main() -> int:
    print("| Fall | Celler | Steg Py/Rs | max\\|Δ\\| maxdjup | MAE maxdjup | "
          "max\\|Δ\\| övriga raster | lagrat Py / Rs (m³) | volymfel Py / Rs | tid Py / Rs (s) |")
    print("|---|---|---|---|---|---|---|---|---|")
    fel = 0
    for namn, kor in fall():
        t0 = time.perf_counter()
        py = kor(kor_2d_lokal_troghet)
        t1 = time.perf_counter()
        rs = kor(kor_2d_lokal_troghet_rust)
        t2 = time.perf_counter()
        d_max = maxdiff(py.max_djup_m, rs.max_djup_m)
        mae = float(np.mean(np.abs(py.max_djup_m.astype(np.float64) - rs.max_djup_m)))
        ovr = max(maxdiff(getattr(py, k), getattr(rs, k)) for k in
                  ("slut_djup_m", "max_hastighet_m_s", "fara_m2_s", "tid_till_0_1m_s",
                   "tid_till_0_2m_s"))
        if d_max >= 1e-6 or ovr >= 1e-6 or rs.volymfel_rel >= 1e-12:
            fel += 1
        print(f"| {namn} | {py.max_djup_m.size} | {py.antal_tidssteg} / {rs.antal_tidssteg} | "
              f"{d_max:.1e} | {mae:.1e} | {ovr:.1e} | {py.lagrat_m3:.6f} / {rs.lagrat_m3:.6f} | "
              f"{py.volymfel_rel:.1e} / {rs.volymfel_rel:.1e} | {t1 - t0:.2f} / {t2 - t1:.2f} |")
        identiskt = (py.in_m3 == rs.in_m3 and py.ut_m3 == rs.ut_m3
                     and py.lagrat_m3 == rs.lagrat_m3)
        print(f"<!-- {namn}: in/ut/lagrat bitidentiska: {identiskt}; "
              f"aktiv andel Py {py.aktiv_cell_andel!r} Rs {rs.aktiv_cell_andel!r} -->")
    return 1 if fel else 0


if __name__ == "__main__":
    sys.exit(main())
