# SPDX-License-Identifier: MIT
"""Val av beräkningsbackend: NumPy (CPU, standard) eller CuPy (CUDA-GPU på server).

Lösarna skrivs mot ett array-API `xp` som är `numpy` eller `cupy`. Samma kod
körs då på laptopen i tester och på GPU-servern för skarpa körningar.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

import numpy as np

from luft.typer import LuftFel

BACKENDS = ("numpy", "cupy")


def hamta_backend(namn: str = "numpy") -> ModuleType:
    """Returnerar array-modulen för backend `namn`.

    Ger ett tydligt fel om CuPy efterfrågas men saknas, i stället för att tyst
    falla tillbaka på CPU (en serverkörning ska inte råka ta tio gånger längre).
    """
    if namn not in BACKENDS:
        raise LuftFel(f"Okänd backend '{namn}'. Välj en av {BACKENDS}.")
    if namn == "numpy":
        return np
    try:
        return importlib.import_module("cupy")
    except ImportError as exc:  # pragma: no cover - beror på servern
        raise LuftFel(
            "Backend 'cupy' kräver paketet cupy och en CUDA-GPU (pip install cupy-cuda12x)."
        ) from exc


def till_numpy(a: Any) -> np.ndarray[Any, Any]:
    """Flyttar en array (NumPy eller CuPy) till värdminnet som NumPy-array."""
    if isinstance(a, np.ndarray):
        return a
    get = getattr(a, "get", None)
    if callable(get):
        return np.asarray(get())
    return np.asarray(a)
