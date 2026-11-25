"""Enhetstester för `gemensamt.gpu` (valfri GPU-backend utan hårt CuPy-beroende)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from gemensamt import gpu


class _MockGpuArray:
    """Minimal mock för CuPy-array med `.get()`."""

    def __init__(self, data: np.ndarray[Any, Any]) -> None:
        self._data = data

    def get(self) -> np.ndarray[Any, Any]:
        return self._data


class _MockCupy:
    """Minimal mock för CuPy-modul i CI utan GPU."""

    def __init__(self) -> None:
        self.synkad = False
        self.cuda = SimpleNamespace(
            Stream=SimpleNamespace(
                null=SimpleNamespace(synchronize=self._synka),
            ),
            Device=lambda: SimpleNamespace(mem_info=(4 * 1024 * 1024 * 1024, 8 * 1024**3)),
        )

    def _synka(self) -> None:
        self.synkad = True

    def asarray(self, arr: Any, dtype: Any = None) -> _MockGpuArray:
        return _MockGpuArray(np.asarray(arr, dtype=dtype))


def test_hamta_backend_cpu_ger_numpy() -> None:
    xp, namn = gpu.hamta_backend("cpu")
    assert xp is np
    assert namn == "cpu"
    assert not gpu.ar_gpu_backend(xp)


def test_hamta_backend_auto_faller_tillbaka_utan_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu, "_hamta_cupy", lambda: None)
    xp, namn = gpu.hamta_backend("auto")
    assert xp is np
    assert namn == "cpu"
    assert not gpu.cuda_tillganglig()
    assert gpu.tillgangligt_vram_mb() is None


def test_hamta_backend_cuda_utan_gpu_kastar_gpufel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu, "_hamta_cupy", lambda: None)
    with pytest.raises(gpu.GpuFel, match="CUDA-GPU begärdes"):
        gpu.hamta_backend("cuda")


def test_hamta_backend_ogiltig_enhet_kastar_gpufel() -> None:
    with pytest.raises(gpu.GpuFel, match="Ogiltig enhet"):
        gpu.hamta_backend("tpu")


def test_hamta_backend_mockad_cuda_och_hjalpfunktioner(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_cp = _MockCupy()
    monkeypatch.setattr(gpu, "_hamta_cupy", lambda: mock_cp)

    assert gpu.cuda_tillganglig()
    xp, namn = gpu.hamta_backend("auto")
    assert xp is mock_cp
    assert namn == "cuda"
    assert gpu.ar_gpu_backend(xp)

    xp2, namn2 = gpu.hamta_backend("cuda")
    assert xp2 is mock_cp
    assert namn2 == "cuda"

    a = np.array([1.0, 2.0, 3.0], dtype=np.float64)
    a_gpu = gpu.till_enhet(a, xp, dtype=np.float32)
    assert isinstance(a_gpu, _MockGpuArray)
    a_tilbaka = gpu.till_numpy(a_gpu)
    assert a_tilbaka.dtype == np.float32
    np.testing.assert_allclose(a_tilbaka, [1.0, 2.0, 3.0])

    gpu.synkronisera(xp)
    assert mock_cp.synkad

    vram = gpu.tillgangligt_vram_mb()
    assert vram == pytest.approx(4096.0)


def test_berakningsplats_etiketter() -> None:
    assert gpu.berakningsplats("cpu", np.float64) == "cpu_f64"
    assert gpu.berakningsplats("cuda", np.float64) == "cuda_f64"
    assert gpu.berakningsplats("cuda", np.float32) == "cuda_f32"
