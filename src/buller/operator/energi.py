"""Numeriskt stabila energisummor i decibel (log-sum-exp, float64)."""

from __future__ import annotations

import numpy as np

from buller.typer import Flyttal


def energisumma_db(niva_db: Flyttal, axel: int | tuple[int, ...]) -> Flyttal:
    """Energisumma `10·lg Σ 10^(L/10)` längs `axel`, stabil för stora och små nivåer.

    Största termen bryts ut före exponentieringen. En summa av enbart -inf ger -inf.
    """
    topp = np.max(niva_db, axis=axel, keepdims=True)
    topp_andlig = np.where(np.isfinite(topp), topp, 0.0)
    summa = np.sum(np.power(10.0, (niva_db - topp_andlig) / 10.0), axis=axel, keepdims=True)
    with np.errstate(divide="ignore"):
        resultat = topp_andlig + 10.0 * np.log10(summa)
    return np.asarray(np.squeeze(resultat, axis=axel), dtype=np.float64)


def db_till_energi(niva_db: Flyttal) -> Flyttal:
    """`10^(L/10)`. -inf ger 0."""
    return np.asarray(np.power(10.0, niva_db / 10.0), dtype=np.float64)
