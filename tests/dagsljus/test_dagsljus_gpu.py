"""Tester för GPU-vägen (CuPy / mockad backend) i Dagsljus (`dagsljus.horisont`)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from dagsljus.horisont import Hojdfalt, horisont_riktning_ruta, horisont_ruta_iter
from dagsljus.matt import SoltimmarAckumulator, svf_fran_horisont
from dagsljus.solposition import dygnsfonster_utc, solposition
from gemensamt import gpu


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


def _syntetisk_kvarter_scen() -> Hojdfalt:
    """Syntetiskt kvarter med två byggnader och en öppen gård (40×40 m, 1 m celler)."""
    z = np.full((40, 40), 10.0, dtype=np.float64)
    z[8:18, 6:16] = 28.0  # 18 m hög byggnad i NV
    z[22:34, 24:35] = 22.0  # 12 m hög byggnad i SO
    z[18:22, 16:24] = 10.5  # låg upphöjning i mitten
    return Hojdfalt(z_m=z, cell_m=1.0, x_min_m=150000.0, y_max_m=6580000.0)


def test_mockad_gpu_horisont_svf_och_soltimmar_paritet(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_xp = _MockGpuBackend()
    monkeypatch.setattr(gpu, "_hamta_cupy", lambda: mock_xp)

    falt = _syntetisk_kvarter_scen()
    fonster = (5, 35, 5, 35)
    antal_k = 36
    radie = 30.0

    # Enskild riktning: CPU vs mockad GPU
    h_cpu, tr_cpu = horisont_riktning_ruta(falt, 0.75, radie, fonster=fonster, enhet="cpu")
    h_gpu, tr_gpu = horisont_riktning_ruta(falt, 0.75, radie, fonster=fonster, enhet="cuda")
    assert mock_xp.synk_anrop > 0
    assert np.array_equal(tr_cpu, tr_gpu)
    assert float(np.max(np.abs(h_cpu - h_gpu))) < 1e-6

    # Full riktningssvepning (horisont_ruta_iter): SVF och soltimmar
    h_kub_cpu = np.empty((antal_k, 30, 30), dtype=np.float32)
    h_kub_gpu = np.empty((antal_k, 30, 30), dtype=np.float32)

    tider = dygnsfonster_utc("2026-03-20", 18.0686, 15.0)
    sol_h, sol_az = solposition(tider, 59.3293, 18.0686)
    ack_cpu = SoltimmarAckumulator(antal_k, sol_h, sol_az, 15.0 / 60.0, (30, 30))
    ack_gpu = SoltimmarAckumulator(antal_k, sol_h, sol_az, 15.0 / 60.0, (30, 30))

    for k, _phi, h, _tr in horisont_ruta_iter(falt, antal_k, radie, fonster=fonster, enhet="cpu"):
        h_kub_cpu[k] = h
        ack_cpu.mata(k, h)

    for k, _phi, h, _tr in horisont_ruta_iter(falt, antal_k, radie, fonster=fonster, enhet="cuda"):
        h_kub_gpu[k] = h
        ack_gpu.mata(k, h)

    assert float(np.max(np.abs(h_kub_cpu - h_kub_gpu))) < 1e-6
    svf_cpu = svf_fran_horisont(h_kub_cpu, axel=0)
    svf_gpu = svf_fran_horisont(h_kub_gpu, axel=0)
    assert float(np.max(np.abs(svf_cpu - svf_gpu))) < 1e-6

    sol_cpu = ack_cpu.timmar()
    sol_gpu = ack_gpu.timmar()
    assert float(np.max(np.abs(sol_cpu - sol_gpu))) == 0.0


def test_enhet_auto_faller_tillbaka_till_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu, "_hamta_cupy", lambda: None)
    falt = _syntetisk_kvarter_scen()
    h_cpu, tr_cpu = horisont_riktning_ruta(falt, 1.2, 25.0, enhet="cpu")
    h_auto, tr_auto = horisont_riktning_ruta(falt, 1.2, 25.0, enhet="auto")
    assert np.array_equal(h_cpu, h_auto)
    assert np.array_equal(tr_cpu, tr_auto)


@pytest.mark.skipif(not gpu.cuda_tillganglig(), reason="CUDA saknas i denna miljö")
def test_riktig_cuda_dagsljus_paritet() -> None:
    falt = _syntetisk_kvarter_scen()
    h_cpu, tr_cpu = horisont_riktning_ruta(falt, 0.75, 30.0, enhet="cpu")
    h_gpu, tr_gpu = horisont_riktning_ruta(falt, 0.75, 30.0, enhet="cuda")
    assert np.array_equal(tr_cpu, tr_gpu)
    assert float(np.max(np.abs(h_cpu - h_gpu))) < 1e-5
