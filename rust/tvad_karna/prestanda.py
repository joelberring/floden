"""Prestanda: Python-lösaren mot Rust-kärnan på en syntetisk stad (1 km²).

Kör: `PYTHONPATH=src python3 rust/tvad_karna/prestanda.py [simulerad_tid_s]`.
Terrängen: svagt lutande yta med kvarter (byggnader, markslag 9) och gator; 60 mm/h regn.
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np

from testsvit.k1_io import EPSG_SWEREF99_1800, GeoTransformInfo
from tvad.lokal_troghet import kor_2d_lokal_troghet
from tvad.rust_backend import kor_2d_lokal_troghet_rust


def stad(cell_m: float, sida_m: float = 1000.0) -> tuple[np.ndarray, np.ndarray, GeoTransformInfo]:
    n = int(round(sida_m / cell_m))
    geo = GeoTransformInfo(bredd=n, hojd=n, x_min=150000.0, y_max=6580000.0,
                           pixelstorlek_x=cell_m, pixelstorlek_y=cell_m,
                           epsg=EPSG_SWEREF99_1800, dtype="float32")
    y, x = np.mgrid[0:n, 0:n] * cell_m
    rng = np.random.default_rng(7)
    # Lutning 1 % mot sydost, mjuka svackor och lite brus (deterministiskt)
    z = 30.0 - 0.01 * (x + y) / 2 + 0.8 * np.sin(x / 90.0) * np.cos(y / 110.0)
    z = z + rng.normal(0, 0.02, z.shape)
    slag = np.ones((n, n), dtype=np.uint8)
    kvarter = (x % 100.0 > 20.0) & (y % 100.0 > 20.0)  # 20 m gator mellan kvarteren
    hus = kvarter & ((x % 100.0) < 90.0) & ((y % 100.0) < 90.0) & (
        ((x % 100.0) < 40.0) | ((y % 100.0) < 40.0))  # vinkelhus, gård mot sydost
    slag[hus] = 9
    slag[kvarter & ~hus] = 4  # gårdar: gräs
    return z.astype(np.float32), slag, geo


def main() -> None:
    sim_s = float(sys.argv[1]) if len(sys.argv) > 1 else 600.0
    celler = [float(c) for c in sys.argv[2].split(",")] if len(sys.argv) > 2 else [2.0, 1.0, 0.5]
    regn_t, regn_i = [0.0, 3600.0], [60.0, 60.0]
    print(f"Simulerad tid {sim_s:.0f} s, 60 mm/h; {os.cpu_count()} logiska kärnor.\n")
    print("| Rutnät | Celler | Steg | Python (s) | Rust 1 tråd (s) | Rust 2 trådar (s) | "
          "Rust 4 trådar (s) | Uppsnabbning 1 / 2 trådar | max\\|Δ\\| maxdjup (m) |")
    print("|---|---|---|---|---|---|---|---|---|")
    for cell in celler:
        z, slag, geo = stad(cell)
        kw = dict(slut_tid_s=sim_s)
        hoppa_py = cell < 1.0 and os.environ.get("MED_PY_05") is None
        if hoppa_py:
            py = None
            t_py = float("nan")
        else:
            t0 = time.perf_counter()
            py = kor_2d_lokal_troghet(z, slag, geo, regn_t, regn_i, **kw)
            t_py = time.perf_counter() - t0
        tider = {}
        rs = None
        for tr in (1, 2, 4):
            t0 = time.perf_counter()
            rs = kor_2d_lokal_troghet_rust(z, slag, geo, regn_t, regn_i, tradar=tr, **kw)
            tider[tr] = time.perf_counter() - t0
        assert rs is not None
        d = (float(np.max(np.abs(py.max_djup_m.astype(np.float64) - rs.max_djup_m)))
             if py is not None else float("nan"))
        print(f"| {cell:g} m ({z.shape[0]}²) | {z.size} | {rs.antal_tidssteg} | {t_py:.1f} | "
              f"{tider[1]:.1f} | {tider[2]:.1f} | {tider[4]:.1f} | "
              f"{t_py / tider[1]:.1f}× / {t_py / tider[2]:.1f}× | {d:.1e} |", flush=True)


if __name__ == "__main__":
    main()
