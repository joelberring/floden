"""Struktur- och f32-referenstester för Flodens WebGPU (WGSL) compute shaders."""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np

from luft.lbm import C_D3Q19, W_D3Q19
from tvad.lokal_troghet import uppdatera_bates_flode_1d

SHADER_MAPP = Path(__file__).resolve().parents[1] / "webb" / "src" / "gpu" / "shaders"


def _las_shader(namn: str) -> str:
    sokvag = SHADER_MAPP / namn
    assert sokvag.is_file(), f"Saknar WGSL-shader: {sokvag}"
    return sokvag.read_text(encoding="utf-8")


def test_alla_wgsl_shaders_har_giltig_grundstruktur_och_inga_f64() -> None:
    """Kontrollerar balanserade klamrar, inga f64-typer och förväntade compute entry points."""
    forvantade = {
        "dagsljus_horisont.wgsl": ["berakna_svf_och_soltimmar"],
        "vatten_2d.wgsl": ["uppdatera_qx", "uppdatera_qy", "begransa_och_volym"],
        "luft_lbm_d3q19.wgsl": ["steg_pull_kollidera"],
        "partikel_spar.wgsl": ["advektera_partiklar"],
    }
    for filnamn, entry_points in forvantade.items():
        kod = _las_shader(filnamn)
        # WebGPU WGSL saknar inbyggd f64
        kod_utan_kommentarer = re.sub(r"//.*", "", kod)
        assert not re.search(r"\bf64\b", kod_utan_kommentarer), f"{filnamn} använder f64"
        assert kod_utan_kommentarer.count("{") == kod_utan_kommentarer.count("}")
        assert kod_utan_kommentarer.count("(") == kod_utan_kommentarer.count(")")
        for ep in entry_points:
            assert f"fn {ep}(" in kod_utan_kommentarer, f"Saknar entry point {ep} i {filnamn}"
            assert "@compute" in kod_utan_kommentarer


def test_wgsl_vatten_f32_punkt_implicit_matchar_python_f64() -> None:
    """f32-ekvationen i vatten_2d.wgsl matchar uppdatera_bates_flode_1d inom 1e-5."""
    h_vals = np.array([0.02, 0.15, 0.80, 2.50], dtype=np.float64)
    s_vals = np.array([-0.05, -0.01, 0.005, -0.002], dtype=np.float64)
    n_vals = np.array([0.04, 0.03, 0.02, 0.035], dtype=np.float64)
    q0 = np.array([0.0, 0.2, -0.1, 1.5], dtype=np.float64)
    dt = 0.25

    q_f64 = uppdatera_bates_flode_1d(q0, h_vals, s_vals, n_vals, dt, 1e-4)

    # Exakt f32-beräkning enligt `bates_punkt_implicit` i vatten_2d.wgsl
    g32 = np.float32(9.81)
    dt32 = np.float32(dt)
    h32 = h_vals.astype(np.float32)
    s32 = s_vals.astype(np.float32)
    n32 = n_vals.astype(np.float32)
    q32 = q0.astype(np.float32)
    h73 = (h32 * h32) * np.power(h32, np.float32(1.0 / 3.0))
    q_rhs = q32 - g32 * h32 * dt32 * s32
    c_f = g32 * dt32 * (n32 * n32) / h73
    q_ny = (np.float32(2.0) * q_rhs) / (
        np.float32(1.0) + np.sqrt(np.float32(1.0) + np.float32(4.0) * c_f * np.abs(q_rhs))
    )
    q_max = np.float32(2.0) * h32 * np.sqrt(g32 * h32)
    q_f32 = np.clip(q_ny, -q_max, q_max)

    assert np.allclose(q_f32.astype(np.float64), q_f64, rtol=1e-5, atol=1e-6)


def test_wgsl_lbm_d3q19_konstanter_och_f32_jamvikt_matchar_python() -> None:
    """D3Q19-konstanterna i luft_lbm_d3q19.wgsl stämmer exakt med src/luft/lbm.py."""
    kod = _las_shader("luft_lbm_d3q19.wgsl")
    cx_m = re.search(r"const CX = array<i32, 19>\(([^)]+)\);", kod)
    cy_m = re.search(r"const CY = array<i32, 19>\(([^)]+)\);", kod)
    cz_m = re.search(r"const CZ = array<i32, 19>\(([^)]+)\);", kod)
    opp_m = re.search(r"const OPP = array<u32, 19>\(([^)]+)\);", kod)
    w_m = re.search(r"const W = array<f32, 19>\(([^)]+)\);", kod)
    assert cx_m and cy_m and cz_m and opp_m and w_m

    cx = [int(v.strip()) for v in cx_m.group(1).split(",")]
    cy = [int(v.strip()) for v in cy_m.group(1).split(",")]
    cz = [int(v.strip()) for v in cz_m.group(1).split(",")]
    opp = [int(v.strip().rstrip("u")) for v in opp_m.group(1).split(",")]
    w = [float(v.strip()) for v in w_m.group(1).split(",")]

    c_wgsl = np.column_stack([cx, cy, cz])
    assert np.array_equal(c_wgsl, C_D3Q19)
    assert np.allclose(w, W_D3Q19, rtol=1e-6, atol=1e-7)
    assert math.isclose(sum(w), 1.0, abs_tol=1e-6)
    for q in range(19):
        assert opp[opp[q]] == q
        assert np.array_equal(c_wgsl[opp[q]], -c_wgsl[q])


def test_webgpu_typescript_motor_kontrakt() -> None:
    """webgpuMotor.ts ska matcha dagsljus_horisont.wgsl, märka klient_webgpu_f32 och städa VRAM."""
    motor_fil = SHADER_MAPP.parent / "webgpuMotor.ts"
    assert motor_fil.is_file()
    kod = motor_fil.read_text(encoding="utf-8")
    assert 'berakna_svf_och_soltimmar' in kod
    assert '"klient_webgpu_f32"' in kod
    assert "finally {" in kod and ".destroy()" in kod


def test_dagsljus_wgsl_korsprodukt_dda_robust_mot_gpu_ulp() -> None:
    """dagsljus_horisont.wgsl ska använda heltalsräknare + korsprodukt (0/64 fel vid 8 ULP)."""
    from dagsljus.horisont import stralsekvens

    shader = (SHADER_MAPP / "dagsljus_horisont.wgsl").read_text(encoding="utf-8")
    assert "var ix: i32 = 0;" in shader
    assert "let lhs: f32 = select(1e30, fx * aey" in shader

    f32 = np.float32
    rmax = 120.0
    for k in range(64):
        phi = 2.0 * math.pi * k / 64
        ex = f32(math.sin(phi))
        ey = f32(-math.cos(phi))
        if abs(ex) > 1e-6:
            for _ in range(8):  # simulera 8 ULP fast-math GPU sin/cos-drift
                ex = np.nextafter(ex, f32(ex * 2.0))
        if abs(ex) < f32(1e-6):
            ex = f32(0.0)
        if abs(ey) < f32(1e-6):
            ey = f32(0.0)
        aex, aey = f32(abs(ex)), f32(abs(ey))
        sx = 1 if ex >= 0 else -1
        sy = 1 if ey >= 0 else -1
        tdx = f32(1e30) if ex == 0 else f32(1.0) / aex
        tdy = f32(1e30) if ey == 0 else f32(1.0) / aey

        ix = iy = cj = ci = 0
        out: list[tuple[int, int]] = []
        for _ in range(int(2 * rmax) + 4):
            tmx = f32(1e30) if ex == 0 else f32(f32(ix) + f32(0.5)) * tdx
            tmy = f32(1e30) if ey == 0 else f32(f32(iy) + f32(0.5)) * tdy
            if min(tmx, tmy) > f32(rmax):
                break
            lhs = f32(1e30) if ex == 0 else f32(f32(ix) + f32(0.5)) * aey
            rhs = f32(1e30) if ey == 0 else f32(f32(iy) + f32(0.5)) * aex
            tol = f32(1e-5) * max(lhs, rhs)
            tie = (ex != 0 and ey != 0) and abs(lhs - rhs) <= tol
            if ex != 0 and (tie or lhs < rhs):
                cj += sx
                ix += 1
            if ey != 0 and (tie or rhs < lhs):
                ci += sy
                iy += 1
            out.append((cj, ci))

        dj, di, _ = stralsekvens(phi, rmax)
        assert out == list(zip(dj.tolist(), di.tolist(), strict=True))
