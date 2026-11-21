"""Inkrementell omräkning: en ändrad yta ogiltigförklarar bara de par vars banor berörs."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from buller.operator.granssnitt import Korridorindex, Parberaknare, Rektangel
from buller.operator.klustring import _kontrollera_parsvar
from buller.operator.lager import Overforingslager
from buller.typer import ANTAL_BAND, BullerFel


@dataclass(frozen=True)
class InkrementellStatistik:
    """Vad en inkrementell omräkning gjorde."""

    antal_mojliga_par: int
    antal_berorda_par: int  # enligt korridorindexet, bland lagrets mottagare och källor
    antal_omraknade_par: int
    antal_hoppade_restpar: int  # berörda men inte beräknade (ligger kvar som rest)
    antal_okanda_par: int  # par från indexet vars id inte finns i lagret
    antal_par_fore: int
    antal_par_efter: int
    antal_parberakningsanrop: int

    @property
    def andel_omraknade_par(self) -> float:
        """Andel av alla par (mottagare × källa) som räknades om."""
        if self.antal_mojliga_par == 0:
            return 0.0
        return self.antal_omraknade_par / self.antal_mojliga_par


def rakna_om_inkrementellt(
    lager: Overforingslager,
    andrad_yta: Rektangel,
    korridorindex: Korridorindex,
    parberaknare: Parberaknare,
) -> tuple[Overforingslager, InkrementellStatistik]:
    """Räknar om de par som berörs av en ändring inom `andrad_yta` och ger ett nytt lager.

    `parberaknare` ska räkna på scenen *efter* ändringen. `korridorindex` ska ge alla par
    vars banor före eller efter ändringen kan beröras; flyttas en byggnad ska `andrad_yta`
    täcka både det gamla och det nya läget (`Rektangel.union`).

    Berörda par som redan är beräknade (lagrade eller utan bana) räknas om. Par som ligger
    som rest i ett klustrat lager räknas inte om: deras övre gräns beror inte på bebyggelsen
    och gäller fortfarande. Däremot kan den exakt beräknade energin ha ändrats, så felgränsen
    per mottagare ska läsas om med `Overforingslager.felgrans_db` efteråt.

    Det nya lagret får ny identitet om något värde ändrades.
    """
    berorda = korridorindex.berorda_par(
        andrad_yta.e_min_m, andrad_yta.n_min_m, andrad_yta.e_max_m, andrad_yta.n_max_m
    )
    n_m, n_k, n_v = lager.antal_mottagare, lager.antal_kallor, lager.antal_vaderklasser
    antal_okanda = 0
    antal_hoppade = 0
    nya_m: list[np.ndarray[tuple[int], np.dtype[np.int64]]] = []
    nya_k: list[np.ndarray[tuple[int], np.dtype[np.int64]]] = []
    nya_h: list[np.ndarray[tuple[int, int, int], np.dtype[np.float64]]] = []
    antal_anrop = 0
    antal_kanda = 0

    if berorda:
        par = np.asarray(sorted(berorda), dtype=np.int64).reshape(-1, 2)
        mottagarordning = np.argsort(lager.mottagare_id, kind="stable")
        kallordning = np.argsort(lager.kalla_id, kind="stable")
        plats_m = np.searchsorted(lager.mottagare_id[mottagarordning], par[:, 0])
        plats_k = np.searchsorted(lager.kalla_id[kallordning], par[:, 1])
        kand = (plats_m < n_m) & (plats_k < n_k)
        m_index = mottagarordning[np.where(kand, plats_m, 0)] if n_m else plats_m
        k_index = kallordning[np.where(kand, plats_k, 0)] if n_k else plats_k
        if n_m and n_k:
            kand &= (lager.mottagare_id[m_index] == par[:, 0]) & (
                lager.kalla_id[k_index] == par[:, 1]
            )
        antal_okanda = int(np.sum(~kand))
        m_index, k_index = m_index[kand], k_index[kand]
        antal_kanda = int(m_index.shape[0])
        ar_rest = lager.ar_rest(m_index, k_index)
        antal_hoppade = int(np.sum(ar_rest))
        m_index, k_index = m_index[~ar_rest], k_index[~ar_rest]
        sortering = np.lexsort((k_index, m_index))
        m_index, k_index = m_index[sortering], k_index[sortering]
        unika_m, start = np.unique(m_index, return_index=True)
        slut = np.append(start[1:], m_index.shape[0])
        for m, a, b in zip(unika_m.tolist(), start.tolist(), slut.tolist(), strict=True):
            kallor = k_index[a:b]
            h_db = _kontrollera_parsvar(parberaknare(m, kallor.tolist()), b - a, n_v)
            antal_anrop += 1
            nya_m.append(np.full(b - a, m, dtype=np.int64))
            nya_k.append(kallor)
            nya_h.append(h_db)

    if not nya_m:
        return lager, InkrementellStatistik(
            antal_mojliga_par=n_m * n_k,
            antal_berorda_par=antal_kanda,
            antal_omraknade_par=0,
            antal_hoppade_restpar=antal_hoppade,
            antal_okanda_par=antal_okanda,
            antal_par_fore=lager.antal_par,
            antal_par_efter=lager.antal_par,
            antal_parberakningsanrop=0,
        )

    om_m = np.concatenate(nya_m)
    om_k = np.concatenate(nya_k)
    om_h = np.concatenate(nya_h, axis=0)
    if om_h.shape[1:] != (n_v, ANTAL_BAND):
        raise BullerFel("Omräknade par har fel form.")
    gammal_nyckel = lager.par_mottagare_index * n_k + lager.par_kalla_index
    om_nyckel = om_m * n_k + om_k
    plats = np.searchsorted(gammal_nyckel, om_nyckel)
    plats_i_lager = np.minimum(plats, max(lager.antal_par - 1, 0))
    fanns = (
        gammal_nyckel[plats_i_lager] == om_nyckel
        if lager.antal_par
        else np.zeros(om_nyckel.shape, dtype=np.bool_)
    )
    har_bana = np.any(om_h != -np.inf, axis=(1, 2))
    if bool(np.all(fanns)) and bool(np.all(har_bana)):
        # Vanligaste fallet: samma uppsättning par, bara nya värden. En kopia, inga sorteringar.
        par_m, par_k = lager.par_mottagare_index, lager.par_kalla_index
        h_db = np.array(lager.h_db)
        h_db[plats] = om_h
    else:
        behall = np.ones(lager.antal_par, dtype=np.bool_)
        behall[plats_i_lager[fanns]] = False
        par_m = np.concatenate([lager.par_mottagare_index[behall], om_m])
        par_k = np.concatenate([lager.par_kalla_index[behall], om_k])
        h_db = np.concatenate([lager.h_db[behall], om_h], axis=0)
    nytt = Overforingslager.skapa(
        mottagare_id=lager.mottagare_id,
        mottagare_e_m=lager.mottagare_e_m,
        mottagare_n_m=lager.mottagare_n_m,
        kalla_id=lager.kalla_id,
        kalla_e_m=lager.kalla_e_m,
        kalla_n_m=lager.kalla_n_m,
        vaderklass_vikter=lager.vaderklass_vikter,
        par_mottagare_index=par_m,
        par_kalla_index=par_k,
        h_db=h_db,
        kall_ordning=lager.kall_ordning,
        rest_mottagare_index=lager.rest_mottagare_index,
        rest_start=lager.rest_start,
        rest_slut=lager.rest_slut,
        rest_grans_db=lager.rest_grans_db,
    )
    return nytt, InkrementellStatistik(
        antal_mojliga_par=n_m * n_k,
        antal_berorda_par=antal_kanda,
        antal_omraknade_par=int(om_m.shape[0]),
        antal_hoppade_restpar=antal_hoppade,
        antal_okanda_par=antal_okanda,
        antal_par_fore=lager.antal_par,
        antal_par_efter=nytt.antal_par,
        antal_parberakningsanrop=antal_anrop,
    )
