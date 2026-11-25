"""Gemensam array-backend (NumPy på CPU / valfri CuPy på CUDA-GPU).

`cupy` är aldrig ett hårt krav: när CUDA eller CuPy saknas faller `enhet="auto"` rent
tillbaka till `numpy`. När användaren uttryckligen kräver `enhet="cuda"` utan tillgänglig
GPU kastas alltid `GpuFel` (inga tysta fallbacks).
"""

from __future__ import annotations

import importlib
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

Enhet = Literal["auto", "cpu", "cuda"]
BackendNamn = Literal["cpu", "cuda"]
_GILTIGA_ENHETER = frozenset({"auto", "cpu", "cuda"})


class GpuFel(Exception):
    """Fel vid val eller initiering av GPU-backend."""


def _hamta_cupy() -> Any | None:
    """Returnerar `cupy`-modulen om den finns och minst en CUDA-enhet är tillgänglig."""
    try:
        cp = importlib.import_module("cupy")
    except Exception:
        return None
    try:
        antal = int(cp.cuda.runtime.getDeviceCount())
    except Exception:
        return None
    return cp if antal > 0 else None


def cuda_tillganglig() -> bool:
    """Returnerar True om CuPy och minst en CUDA-GPU är tillgängliga."""
    return _hamta_cupy() is not None


def hamta_backend(enhet: str = "auto") -> tuple[Any, BackendNamn]:
    """Returnerar `(xp, namn)` där `xp` är `cupy` eller `numpy` och `namn` är `'cuda'`/`'cpu'`.

    Args:
        enhet: `'auto'` (CUDA om tillgänglig, annars CPU), `'cpu'` (alltid NumPy),
            eller `'cuda'` (kräver CUDA, annars `GpuFel`).
    """
    if enhet not in _GILTIGA_ENHETER:
        raise GpuFel(f"Ogiltig enhet '{enhet}'. Förväntade 'auto', 'cpu' eller 'cuda'.")
    if enhet == "cpu":
        return np, "cpu"
    cp = _hamta_cupy()
    if cp is not None:
        return cp, "cuda"
    if enhet == "cuda":
        raise GpuFel(
            "CUDA-GPU begärdes (enhet='cuda'), men CuPy eller fungerande CUDA-enhet saknas. "
            "Använd enhet='cpu' eller enhet='auto' för CPU-körning."
        )
    return np, "cpu"


def ar_gpu_backend(xp: Any) -> bool:
    """Returnerar True om `xp` inte är `numpy` (t.ex. `cupy` eller mockad GPU-backend)."""
    return xp is not np


def berakningsplats(enhet_namn: BackendNamn, dtyp: Any = np.float64) -> str:
    """Returnerar K0-etikett för beräkningsplats (`cpu_f64`, `cuda_f64`, `cuda_f32`, ...)."""
    dt = np.dtype(dtyp)
    prec = "f32" if dt == np.float32 else "f64" if dt == np.float64 else str(dt)
    return f"{enhet_namn}_{prec}"


def till_enhet(arr: Any, xp: Any, *, dtype: Any = None) -> Any:
    """Flyttar array till angiven backend `xp` (och valfri `dtype`)."""
    if dtype is not None:
        return xp.asarray(arr, dtype=dtype)
    return xp.asarray(arr)


def till_numpy(arr: Any) -> NDArray[Any]:
    """Konverterar en array från CuPy/mock-backend eller NumPy till `numpy.ndarray`."""
    get_fn = getattr(arr, "get", None)
    if callable(get_fn):
        return np.asarray(get_fn())
    return np.asarray(arr)


def synkronisera(xp: Any) -> None:
    """Synkroniserar GPU-strömmen för korrekt tidtagning (no-op för NumPy)."""
    cuda = getattr(xp, "cuda", None)
    if cuda is None:
        return
    stream_cls = getattr(cuda, "Stream", None)
    if stream_cls is None:
        return
    null_stream = getattr(stream_cls, "null", None)
    if null_stream is None:
        return
    sync = getattr(null_stream, "synchronize", None)
    if callable(sync):
        sync()


def tillgangligt_vram_mb() -> float | None:
    """Returnerar fritt GPU-minne (MB) om CUDA finns tillgängligt, annars `None`."""
    cp = _hamta_cupy()
    if cp is None:
        return None
    try:
        fritt_bytes, _totalt_bytes = cp.cuda.Device().mem_info
        return float(fritt_bytes) / (1024.0 * 1024.0)
    except Exception:
        return None
