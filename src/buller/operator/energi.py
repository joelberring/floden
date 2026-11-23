"""Numeriskt stabila energisummor i decibel (log-sum-exp, float64)."""

from __future__ import annotations

from collections.abc import Callable

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


def pruna_med_energibudget(
    ovre_grans_db: Flyttal,
    berakna_par: Callable[[int], float],
    max_fel_db: float = 0.05,
) -> tuple[float, int, float]:
    """Summerar källbidrag i fallande ordning av övre gräns och avbryter när resten är försumbar.

    Varje kandidat `i` har en känd övre gräns `ovre_grans_db[i]` (t.ex. fritt fält + 6 dB
    hård mark). Kandidaterna utvärderas i fallande ordning av övre gräns; så snart
    summans relativa övre fel från *alla* återstående kandidater tillsammans understiger
    `max_fel_db` avbryts loopen.

    Returnerar `(total_db, antal_beraknade, garanterat_maxfel_db)`. Den sanna summan ligger
    matematiskt garanterat i `[total_db, total_db + garanterat_maxfel_db]`.

    **Fristående:** funktionen är testad (`tests/test_fysik_numerik_fixar.py`) men inte inkopplad i
    något användarflöde; ingen annan modul i `src` anropar den (se `STATUS.md`, 2026-11-23).
    """
    ovre = np.asarray(ovre_grans_db, dtype=np.float64)
    n = int(ovre.size)
    if n == 0:
        return float("-inf"), 0, 0.0
    if max_fel_db <= 0.0:
        raise ValueError("max_fel_db måste vara > 0.")

    ordning = np.argsort(ovre)[::-1]
    ovre_sort = ovre[ordning]
    # Suffix-energisumma av de övre gränserna i log-skala (numeriskt stabil)
    skala = float(ovre_sort[0])
    if not np.isfinite(skala):
        return float("-inf"), 0, 0.0
    ovre_energi = np.where(
        np.isfinite(ovre_sort), np.power(10.0, (ovre_sort - skala) / 10.0), 0.0
    )
    kvar_energi = np.cumsum(ovre_energi[::-1])[::-1]

    # Gränskvot: 10*log10(1 + E_kvar / E_nu) <= max_fel_db  <=>  E_kvar <= E_nu * (10^(eps/10) - 1)
    kvot_grans = float(10.0 ** (max_fel_db / 10.0) - 1.0)
    ack_energi = 0.0
    beraknade = 0
    rest_energi = float(kvar_energi[0])
    for pos in range(n):
        rest_energi = float(kvar_energi[pos])
        if ack_energi > 0.0 and rest_energi <= ack_energi * kvot_grans:
            break
        val_db = float(berakna_par(int(ordning[pos])))
        beraknade += 1
        if np.isfinite(val_db):
            ack_energi += float(10.0 ** ((val_db - skala) / 10.0))
        rest_energi = float(kvar_energi[pos + 1]) if pos + 1 < n else 0.0

    if ack_energi <= 0.0:
        return float("-inf"), beraknade, 0.0
    total_db = skala + 10.0 * float(np.log10(ack_energi))
    maxfel_db = 10.0 * float(np.log10(1.0 + rest_energi / ack_energi)) if rest_energi > 0.0 else 0.0
    return total_db, beraknade, maxfel_db
