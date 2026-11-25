"""Tester för GPU-vägen (CuPy / mockad backend) i Vatten 2D (`tvad.lokal_troghet`)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from gemensamt import gpu
from testsvit.ea_fall import ea_test1_sanka_over_troskel, ea_test8a_urbant_regn
from testsvit.k1_io import GeoTransformInfo
from tvad.lokal_troghet import TvadFel, kor_2d_lokal_troghet, skriv_tvad_resultat_k6


class _MockGpuBackend:
    """Delegerar alla NumPy-operationer men är inte `np`, så GPU-kodvägen körs i CI utan CUDA."""

    def __init__(self) -> None:
        self.synk_anrop = 0
        self.cuda = SimpleNamespace(
            Stream=SimpleNamespace(
                null=SimpleNamespace(synchronize=self._synka),
            ),
        )

    def _synka(self) -> None:
        self.synk_anrop += 1

    def __getattr__(self, namn: str) -> Any:
        return getattr(np, namn)


def _enkel_sanka() -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any], GeoTransformInfo]:
    """Syntetisk skål (A1-liknande) för snabb paritetskontroll."""
    h, b = 24, 24
    yy, xx = np.mgrid[0:h, 0:b]
    r2 = (yy - 11.5) ** 2 + (xx - 11.5) ** 2
    z = (10.0 + 0.015 * r2).astype(np.float32)
    markslag = np.full((h, b), 4, dtype=np.uint8)
    markslag[5:8, 5:8] = 9  # byggnad
    geo = GeoTransformInfo(
        x_min=100000.0,
        y_max=6500000.0,
        pixelstorlek_x=2.0,
        pixelstorlek_y=2.0,
        bredd=b,
        hojd=h,
        epsg=3011,
        dtype="float32",
    )
    return z, markslag, geo


def _lutande_plan() -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any], GeoTransformInfo]:
    """A4-liknande lutande plan med öppna ränder."""
    h, b = 16, 30
    x = np.arange(b, dtype=np.float32)
    z = np.broadcast_to((12.0 - 0.05 * x)[None, :], (h, b)).copy()
    markslag = np.full((h, b), 2, dtype=np.uint8)
    geo = GeoTransformInfo(
        x_min=100000.0,
        y_max=6500000.0,
        pixelstorlek_x=2.0,
        pixelstorlek_y=2.0,
        bredd=b,
        hojd=h,
        epsg=3011,
        dtype="float32",
    )
    return z, markslag, geo


def test_mockad_gpu_f64_paritet_sanka_och_lutande_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mock_xp = _MockGpuBackend()
    monkeypatch.setattr(gpu, "_hamta_cupy", lambda: mock_xp)

    for z, ms, geo in (_enkel_sanka(), _lutande_plan()):
        ref = kor_2d_lokal_troghet(
            z, ms, geo, [0.0, 120.0], [60.0, 60.0], slut_tid_s=180.0, enhet="cpu"
        )
        res = kor_2d_lokal_troghet(
            z, ms, geo, [0.0, 120.0], [60.0, 60.0], slut_tid_s=180.0, enhet="cuda", precision="f64"
        )
        assert res.berakningsplats == "cuda_f64"
        assert mock_xp.synk_anrop > 0
        diff = float(
            np.max(np.abs(res.max_djup_m.astype(np.float64) - ref.max_djup_m.astype(np.float64)))
        )
        assert diff < 1e-6  # float32-utdata från exakt samma f64-beräkning
        assert abs(res.lagrat_m3 - ref.lagrat_m3) < 1e-10
        assert res.volymfel_rel < 1e-10

    # Verifiera att berakningsplats skrivs till run.json (K0)
    z, ms, geo = _enkel_sanka()
    res = kor_2d_lokal_troghet(
        z, ms, geo, [0.0, 60.0], [60.0, 60.0], enhet="cuda", precision="f64"
    )
    filer = skriv_tvad_resultat_k6(res, geo, tmp_path / "ut")
    run = json.loads(filer["run"].read_text(encoding="utf-8"))
    assert run["parametrar"]["berakningsplats"] == "cuda_f64"


def test_mockad_gpu_f32_skiss_noggrannhet(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_xp = _MockGpuBackend()
    monkeypatch.setattr(gpu, "_hamta_cupy", lambda: mock_xp)

    z, ms, geo = _enkel_sanka()
    ref = kor_2d_lokal_troghet(
        z, ms, geo, [0.0, 120.0], [60.0, 60.0], slut_tid_s=150.0, enhet="cpu"
    )
    res_f32 = kor_2d_lokal_troghet(
        z, ms, geo, [0.0, 120.0], [60.0, 60.0], slut_tid_s=150.0, enhet="cuda", precision="f32"
    )
    assert res_f32.berakningsplats == "cuda_f32"
    mae = float(np.mean(np.abs(res_f32.max_djup_m - ref.max_djup_m)))
    assert mae < 1e-3
    assert res_f32.volymfel_rel < 1e-3


def test_gpu_subgrid_kastar_tydligt_fel_vid_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_xp = _MockGpuBackend()
    monkeypatch.setattr(gpu, "_hamta_cupy", lambda: mock_xp)
    z, ms, geo = _enkel_sanka()
    with pytest.raises(TvadFel, match="subgrid_faktor"):
        kor_2d_lokal_troghet(
            z, ms, geo, [0.0, 60.0], [60.0, 60.0], subgrid_faktor=2, enhet="cuda"
        )


def test_mockad_gpu_ea1_och_ea8a(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_xp = _MockGpuBackend()
    monkeypatch.setattr(gpu, "_hamta_cupy", lambda: mock_xp)

    ea1 = ea_test1_sanka_over_troskel()
    ref1 = kor_2d_lokal_troghet(
        ea1.mark_z,
        ea1.markslag,
        ea1.geo,
        ea1.regn_tid_s,
        ea1.regn_intensitet_mm_h,
        slut_tid_s=60.0,
        enhet="cpu",
    )
    gpu1 = kor_2d_lokal_troghet(
        ea1.mark_z,
        ea1.markslag,
        ea1.geo,
        ea1.regn_tid_s,
        ea1.regn_intensitet_mm_h,
        slut_tid_s=60.0,
        enhet="cuda",
        precision="f64",
    )
    assert float(np.max(np.abs(gpu1.max_djup_m - ref1.max_djup_m))) < 1e-6
    assert gpu1.volymfel_rel < 1e-10

    ea8a = ea_test8a_urbant_regn()
    ref8 = kor_2d_lokal_troghet(
        ea8a.mark_z,
        ea8a.markslag,
        ea8a.geo,
        ea8a.regn_tid_s,
        ea8a.regn_intensitet_mm_h,
        slut_tid_s=60.0,
        enhet="cpu",
    )
    gpu8 = kor_2d_lokal_troghet(
        ea8a.mark_z,
        ea8a.markslag,
        ea8a.geo,
        ea8a.regn_tid_s,
        ea8a.regn_intensitet_mm_h,
        slut_tid_s=60.0,
        enhet="cuda",
        precision="f64",
    )
    assert float(np.max(np.abs(gpu8.max_djup_m - ref8.max_djup_m))) < 1e-6
    assert gpu8.volymfel_rel < 1e-10


@pytest.mark.skipif(not gpu.cuda_tillganglig(), reason="CUDA saknas i denna miljö")
def test_riktig_cuda_paritet_f64_och_f32() -> None:
    z, ms, geo = _enkel_sanka()
    ref = kor_2d_lokal_troghet(
        z, ms, geo, [0.0, 120.0], [60.0, 60.0], slut_tid_s=180.0, enhet="cpu"
    )
    res64 = kor_2d_lokal_troghet(
        z, ms, geo, [0.0, 120.0], [60.0, 60.0], slut_tid_s=180.0, enhet="cuda", precision="f64"
    )
    assert res64.berakningsplats == "cuda_f64"
    diff64 = float(
        np.max(np.abs(res64.max_djup_m.astype(np.float64) - ref.max_djup_m.astype(np.float64)))
    )
    assert diff64 < 1e-6
    assert abs(res64.lagrat_m3 - ref.lagrat_m3) < 1e-10
    assert res64.volymfel_rel < 1e-10

    res32 = kor_2d_lokal_troghet(
        z, ms, geo, [0.0, 120.0], [60.0, 60.0], slut_tid_s=180.0, enhet="cuda", precision="f32"
    )
    assert res32.berakningsplats == "cuda_f32"
    assert float(np.mean(np.abs(res32.max_djup_m - ref.max_djup_m))) < 1e-3
