"""Deterministisk skrivning av `.npz` (samma innehåll ger samma byte och samma sha256)."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

_FAST_TID = (1980, 1, 1, 0, 0, 0)


def skriv_npz_deterministiskt(sokvag: str | Path, arrayer: Mapping[str, NDArray[Any]]) -> Path:
    """Skriver arrayerna i given ordning som okomprimerad `.npz` utan tidsstämplar.

    `numpy.savez` lägger in aktuell tid i zip-huvudet och ger därför olika kontrollsumma för
    samma innehåll. Här är tid och filrättigheter fasta. Filen läses med `numpy.load`.
    """
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(mal, mode="w", compression=zipfile.ZIP_STORED) as arkiv:
        for namn, array in arrayer.items():
            buffert = io.BytesIO()
            np.lib.format.write_array(buffert, np.asarray(array, order="C"), allow_pickle=False)
            post = zipfile.ZipInfo(filename=f"{namn}.npy", date_time=_FAST_TID)
            post.compress_type = zipfile.ZIP_STORED
            post.external_attr = 0o644 << 16
            arkiv.writestr(post, buffert.getvalue())
    return mal
