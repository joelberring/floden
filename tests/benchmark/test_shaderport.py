"""Skuggarna har aldrig körts på riktigt grafikkort. Det här testet kör deras *logik*.

Dagsljusskuggaren `dagsljus_horisont.wgsl` är översatt rad för rad till Python (f64) och jämförs
med serverns facit (`benchmark.provfall.dagsljus_facit`) på ett litet fält. Stämmer de inte är
antingen skuggaren eller facit fel, och det vet vi innan någon sätter sig vid ett grafikkort.
f32-avrundningen och GPU:ns `pow`/`atan` provas inte här; det gör webbläsarprovet.

Vattenskuggaren provas på samma sätt: serverns schema (`vatten_schema`) körs i f64 och i f32 och
ska hålla massbalansen; punktformeln i `vatten_2d.wgsl` finns redan i tests/test_wgsl_referens.py.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
import pytest

from benchmark.provfall import (
    dagsljus_facit,
    vatten_indata_lutande_plan,
    vatten_massbalans_rel,
    vatten_schema,
)

SHADER = Path(__file__).resolve().parents[2] / "webb" / "src" / "gpu" / "shaders"
TWO_PI = 2.0 * math.pi


def _konstant(kod: str, namn: str) -> float:
    m = re.search(rf"const {namn}: f32 = ([0-9.e+-]+);", kod)
    assert m, namn
    return float(m.group(1))


def _horisont_riktning(
    z: np.ndarray,
    cell: float,
    rmax: float,
    r0: int,
    c0: int,
    phi: float,
    axel_noll: float,
    t_min: float,
) -> float:
    """Översättning av `horisont_riktning` i dagsljus_horisont.wgsl."""
    ny, nx = z.shape
    z0 = z[r0, c0]
    ex = math.sin(phi)
    ey = -math.cos(phi)
    if abs(ex) < axel_noll:
        ex = 0.0
    if abs(ey) < axel_noll:
        ey = 0.0
    sx = 1 if ex >= 0.0 else -1
    sy = 1 if ey >= 0.0 else -1
    tdx = 1.0 / abs(ex) if ex != 0.0 else 1e30
    tdy = 1.0 / abs(ey) if ey != 0.0 else 1e30
    tmx = 0.5 * tdx if ex != 0.0 else 1e30
    tmy = 0.5 * tdy if ey != 0.0 else 1e30
    cj, ci = c0, r0
    max_lutning = 0.0
    max_steg = int(2.0 * rmax) + 4
    for _ in range(max_steg):
        t = min(tmx, tmy)
        if t > rmax:
            break
        steg_x = tmx <= tmy + 1e-5
        steg_y = tmy <= tmx + 1e-5
        if steg_x:
            cj += sx
            tmx += tdx
        if steg_y:
            ci += sy
            tmy += tdy
        if cj < 0 or cj >= nx or ci < 0 or ci >= ny:
            break
        dz = z[ci, cj] - z0
        if dz > 0.0:
            max_lutning = max(max_lutning, dz / (max(t, t_min) * cell))
    return math.atan(max_lutning)


def _skugga_dagsljus(
    z: np.ndarray,
    cell: float,
    rmax: float,
    k: int,
    sol_h: np.ndarray,
    sol_az: np.ndarray,
    steg_h: float,
) -> tuple[np.ndarray, np.ndarray]:
    kod = (SHADER / "dagsljus_horisont.wgsl").read_text(encoding="utf-8")
    axel_noll = _konstant(kod, "AXEL_NOLL")
    t_min = _konstant(kod, "T_MIN")
    ny, nx = z.shape
    svf = np.zeros((ny, nx))
    sol = np.zeros((ny, nx))
    for r in range(ny):
        for c in range(nx):
            s = 0.0
            for i in range(k):
                h = _horisont_riktning(z, cell, rmax, r, c, TWO_PI * i / k, axel_noll, t_min)
                s += math.cos(h) ** 2
            svf[r, c] = s / k
            n = 0
            for hs, az in zip(sol_h, sol_az, strict=True):
                if hs > 0.0 and hs > _horisont_riktning(z, cell, rmax, r, c, az, axel_noll, t_min):
                    n += 1
            sol[r, c] = n * steg_h
    return svf, sol


def _litet_falt() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    z = np.zeros((30, 34))
    z[5:12, 4:20] = 12.0
    z[18:26, 22:30] = 20.0
    z[14:17, 8:11] = 6.0
    sol_h = np.radians([-5.0, 3.0, 9.0, 17.0, 26.0, 33.0, 18.0, 8.0, -2.0])
    sol_az = np.radians([60.0, 75.0, 98.0, 131.0, 180.0, 221.0, 255.0, 281.0, 300.0])
    return z, sol_h, sol_az


def test_dagsljusskuggaren_ar_samma_beraknning_som_serverns_facit() -> None:
    z, sol_h, sol_az = _litet_falt()
    cell, rmax, k, steg_h = 1.0, 14.0, 16, 0.5
    svf_s, sol_s = _skugga_dagsljus(z, cell, rmax, k, sol_h, sol_az, steg_h)
    svf_f, sol_f = dagsljus_facit(z, cell, rmax, k, sol_h, sol_az, steg_h)
    # Samma algoritm i f64: skillnaden är avrundningen av facits f32-lagrade horisontvinkel.
    assert np.abs(svf_s - svf_f).max() < 1e-6
    assert np.abs(sol_s - sol_f).max() <= steg_h  # högst en solposition i någon cell vid en kant
    assert np.mean(sol_s == sol_f) > 0.995
    assert 0.0 < svf_f.min() < 1.0 and sol_f.min() < sol_f.max()


def test_dagsljusets_facit_pa_fri_plan_ar_svf_1_och_alla_solpositioner() -> None:
    z = np.zeros((16, 16))
    sol_h = np.radians([-3.0, 10.0, 25.0])
    svf, sol = dagsljus_facit(z, 1.0, 8.0, 8, sol_h, np.radians([10.0, 90.0, 200.0]), 1.0)
    assert np.allclose(svf, 1.0)
    assert np.allclose(sol, 2.0)  # två av tre positioner är över horisonten


def test_dagsljusskuggarens_uniform_och_binding_matchar_klientens_packning() -> None:
    kod = (SHADER / "dagsljus_horisont.wgsl").read_text(encoding="utf-8")
    struct = re.search(r"struct Params \{(.*?)\};", kod, re.S)
    assert struct
    falt = re.findall(r"(\w+):\s*(u32|f32)", struct.group(1))
    # Fyra u32 följda av fyra f32 = 32 byte, som packaDagsljusParams skriver.
    assert [t for _, t in falt] == ["u32"] * 4 + ["f32"] * 4
    assert [n for n, _ in falt[:4]] == ["nx", "ny", "n_riktningar", "n_solsteg"]


def test_vattenschemat_haller_massan_och_f32_ligger_nara_f64() -> None:
    ind = vatten_indata_lutande_plan()
    maxd64, slut64, vol64 = vatten_schema(ind, np.float64)
    maxd32, slut32, vol32 = vatten_schema(ind, np.float32)
    assert abs(vatten_massbalans_rel(ind, vol64)) < 1e-12
    assert abs(vatten_massbalans_rel(ind, vol32)) < 1e-5
    assert maxd64.max() > 0.05  # provet mäter något: vatten samlas i sänkan
    assert np.abs(maxd32 - maxd64).max() < 1e-4
    assert np.abs(slut32 - slut64).max() < 1e-4
    # Byggnadsceller (aktiv = 0) förblir torra.
    assert maxd64[ind.aktiv == 0].max() == 0.0


def test_vattenschemats_regn_och_steg_matchar_skuggarens_uniform() -> None:
    kod = (SHADER / "vatten_2d.wgsl").read_text(encoding="utf-8")
    struct = re.search(r"struct VattenParams \{(.*?)\};", kod, re.S)
    assert struct
    assert re.findall(r"(\w+):\s*(u32|f32)", struct.group(1)) == [
        ("nx", "u32"),
        ("ny", "u32"),
        ("dx", "f32"),
        ("dy", "f32"),
        ("dt", "f32"),
        ("theta", "f32"),
        ("min_djup", "f32"),
        ("regn_m", "f32"),
    ]
    ind = vatten_indata_lutande_plan()
    assert ind.dt == 0.25
    assert sum(a for a, _ in ind.steg) == 1000
    assert ind.steg[0][1] == pytest.approx(120e-3 / 3600 * 0.25)
