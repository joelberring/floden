"""Serverrenderad kartografi med fast färgskala och källtext (T08).

Delegerar till den enhetliga rapportfigur-motorn i ``rapport.figurer`` (ren
Pillow + NumPy med Gelasio och Liberation Sans, norrpil, skalstock,
teckenförklaring och spårbarhetsfot) samtidigt som den tidigare API-signaturen
för ``rendera_djupkarta_png`` och ``rendera_diffkarta_png`` bevaras.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from rapport.figurer import (
    FARG_KARTYTA,
    FargKlass,
    FigurFel,
    FigurStil,
    hex_till_rgb,
    rendera_kartfigur,
)
from testsvit.k1_io import GeoTransformInfo


class RapportKartaFel(Exception):
    """Fel vid validering eller rendering av rapportkarta."""


def _validera_raster(raster: NDArray[np.floating[Any]], namn: str) -> NDArray[np.float64]:
    arr = np.asarray(raster, dtype=np.float64)
    if arr.ndim != 2:
        raise RapportKartaFel(f"{namn} måste vara ett 2D-raster, fick {arr.ndim}D.")
    if arr.shape[0] < 1 or arr.shape[1] < 1:
        raise RapportKartaFel(f"{namn} får inte ha tomma dimensioner: {arr.shape}.")
    return arr


def _diff_stil(max_abs_m: float) -> FigurStil:
    """Bygger en symmetrisk skillnadsskala för godtyckligt ``max_abs_m``."""
    if abs(max_abs_m - 0.5) < 1e-9:
        from rapport.figurer import FIGURSTILAR

        return FIGURSTILAR["skyfall_skillnad"]

    g1 = max_abs_m * 0.04
    g2 = max_abs_m * 0.25
    g3 = max_abs_m * 0.60

    def _f(v: float) -> str:
        return f"{v:.2f}".replace(".", ",")

    return FigurStil(
        namn="skyfall_skillnad",
        doman="skyfall",
        storhet="Skillnad i vattendjup",
        enhet="m",
        tomvarde_farg=FARG_KARTYTA,
        tomvarde_etikett="Inget värde",
        klasser=(
            FargKlass(None, -g3, hex_till_rgb("#234B66"), f"≤ −{_f(g3)} m (minskning)"),
            FargKlass(-g3, -g2, hex_till_rgb("#4A7D8C"), f"−{_f(g3)} – −{_f(g2)} m"),
            FargKlass(-g2, -g1, hex_till_rgb("#8FB9AC"), f"−{_f(g2)} – −{_f(g1)} m"),
            FargKlass(-g1, g1, FARG_KARTYTA, f"−{_f(g1)} – +{_f(g1)} m (oförändrat)"),
            FargKlass(g1, g2, hex_till_rgb("#D6B463"), f"+{_f(g1)} – +{_f(g2)} m"),
            FargKlass(g2, g3, hex_till_rgb("#B86A4B"), f"+{_f(g2)} – +{_f(g3)} m"),
            FargKlass(g3, None, hex_till_rgb("#8C3B2B"), f"≥ +{_f(g3)} m (ökning)"),
        ),
    )


def rendera_djupkarta_png(
    djup_m: NDArray[np.floating[Any]],
    ut_fil: str | Path,
    *,
    titel: str,
    kalla: str = "©Lantmäteriet / Floden",
    undertitel: str = "",
    metodstatus: str = "Skissunderlag",
    geo: GeoTransformInfo | None = None,
    byggnad_mask: NDArray[np.bool_] | NDArray[np.integer[Any]] | None = None,
    projektpolygon_3011: Sequence[Sequence[float]] | None = None,
    run_hash: str | None = None,
    anmarkning: str | None = None,
) -> Path:
    """Renderar maximalt vattendjup till PNG med enhetlig kartfigur, teckenförklaring och fot."""
    arr = _validera_raster(djup_m, "djup_m")
    mal = Path(ut_fil)
    try:
        rendera_kartfigur(
            arr,
            "skyfall_djup",
            titel=titel,
            undertitel=undertitel,
            metodstatus=metodstatus,
            geo=geo,
            byggnad_mask=byggnad_mask,
            projektpolygon_3011=projektpolygon_3011,
            ut_fil=mal,
            run_hash=run_hash,
            anmarkning=anmarkning,
            kalla=kalla,
        )
    except FigurFel as exc:
        raise RapportKartaFel(str(exc)) from exc
    return mal


def rendera_diffkarta_png(
    diff_m: NDArray[np.floating[Any]],
    ut_fil: str | Path,
    *,
    titel: str,
    kalla: str = "©Lantmäteriet / Floden",
    max_abs_m: float = 0.5,
    undertitel: str = "",
    metodstatus: str = "Skissunderlag",
    geo: GeoTransformInfo | None = None,
    byggnad_mask: NDArray[np.bool_] | NDArray[np.integer[Any]] | None = None,
    projektpolygon_3011: Sequence[Sequence[float]] | None = None,
    run_hash: str | None = None,
    anmarkning: str | None = None,
) -> Path:
    """Renderar differenskarta före/efter till PNG med divergerande skala och teckenförklaring."""
    if max_abs_m <= 0.0:
        raise RapportKartaFel("max_abs_m måste vara > 0.")
    arr = _validera_raster(diff_m, "diff_m")
    mal = Path(ut_fil)
    try:
        rendera_kartfigur(
            arr,
            _diff_stil(float(max_abs_m)),
            titel=titel,
            undertitel=undertitel,
            metodstatus=metodstatus,
            geo=geo,
            byggnad_mask=byggnad_mask,
            projektpolygon_3011=projektpolygon_3011,
            ut_fil=mal,
            run_hash=run_hash,
            anmarkning=anmarkning,
            kalla=kalla,
        )
    except FigurFel as exc:
        raise RapportKartaFel(str(exc)) from exc
    return mal
