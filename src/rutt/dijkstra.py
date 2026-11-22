"""Tidsberoende FIFO-Dijkstra för insatstider (T07 steg 2–3, K7/K8)."""

from __future__ import annotations

import bisect
import heapq
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from rutt.parametrar import (
    FordonParametrar,
    RuttParametrarFel,
    RuttParametrarK7,
    berakna_hastighetsfaktor,
    validera_parametrar,
)
from rutt.vagnat import VagKant, VagNat, VagNatFel, effektivt_vagdjup_m


@dataclass(frozen=True)
class InsatstidPost:
    """En rad i K8-tabellen `insatstider.parquet`."""

    adress_id: str
    station_id: str
    t_s: float
    insatstid_s: float
    torr_insatstid_s: float

    def till_dict(self) -> dict[str, str | float]:
        """Konverterar posten till K8/v1-lexikon."""
        return {
            "adress_id": self.adress_id,
            "station_id": self.station_id,
            "t_s": float(self.t_s),
            "insatstid_s": float(self.insatstid_s),
            "torr_insatstid_s": float(self.torr_insatstid_s),
        }


def _interpolera_tidserie(
    tidpunkter: Sequence[float],
    varden_per_tid: Mapping[float, Mapping[str, float]],
    kant_id: str,
    tid_s: float,
    saknat_varde: float | None = None,
) -> float | None:
    """Slår upp eller linjärinterpolerar ett kantvärde vid `tid_s`.

    När `saknat_varde` anges (t.ex. `0.0` för markdjup på en torr kant som utelämnats ur
    en gles ordbok) används det i stället för att hoppa direkt till grannpunktens värde.
    """
    if not tidpunkter:
        return saknat_varde
    if tid_s <= tidpunkter[0]:
        return varden_per_tid[tidpunkter[0]].get(kant_id, saknat_varde)
    if tid_s >= tidpunkter[-1]:
        return varden_per_tid[tidpunkter[-1]].get(kant_id, saknat_varde)

    hi = bisect.bisect_right(tidpunkter, tid_s)
    t0 = tidpunkter[hi - 1]
    t1 = tidpunkter[hi]
    if t0 == tid_s:
        return varden_per_tid[t0].get(kant_id, saknat_varde)

    v0 = varden_per_tid[t0].get(kant_id, saknat_varde)
    v1 = varden_per_tid[t1].get(kant_id, saknat_varde)
    if v0 is None:
        return v1
    if v1 is None or t1 <= t0:
        return v0
    andel = (tid_s - t0) / (t1 - t0)
    return float(v0 + andel * (v1 - v0))


def _kant_restid_s(
    kant: VagKant,
    tid_s: float,
    sorterade_tider: Sequence[float],
    kant_djup_per_tid: Mapping[float, Mapping[str, float]],
    fordon: FordonParametrar,
    vattenniva_per_tid: Mapping[float, Mapping[str, float]] | None,
    niva_tider: Sequence[float],
) -> float | None:
    """Beräknar restid (s) på `kant` vid avresetid `tid_s`, eller `None` om ofarbar."""
    mark_djup = _interpolera_tidserie(
        sorterade_tider, kant_djup_per_tid, kant.kant_id, tid_s, saknat_varde=0.0
    )
    eff_mark_djup = 0.0 if mark_djup is None else float(mark_djup)

    vattenniva = (
        _interpolera_tidserie(niva_tider, vattenniva_per_tid, kant.kant_id, tid_s)
        if vattenniva_per_tid is not None
        else None
    )
    eff_djup = effektivt_vagdjup_m(kant, eff_mark_djup, vattenniva)
    faktor = berakna_hastighetsfaktor(fordon, eff_djup)
    if faktor <= 0.0:
        return None
    return float(kant.langd_m) / (kant.normal_hastighet_m_s * faktor)


def tidsberoende_dijkstra(
    vagnat: VagNat,
    start_nod: str,
    start_tid_s: float,
    kant_djup_per_tid: Mapping[float, Mapping[str, float]],
    fordon: FordonParametrar,
    *,
    vattenniva_per_tid: Mapping[float, Mapping[str, float]] | None = None,
) -> dict[str, float]:
    """Kör tidsberoende FIFO-Dijkstra och returnerar ankomsttid (s) per nod.

    För varje nod returneras den tidigaste ankomsttiden `t_ankomst >= start_tid_s`.
    Restiden till en adress från `start_nod` vid `start_tid_s` är
    `ankomst[mal_nod] - start_tid_s` (eller `math.inf` om ej nåbar).
    """
    if start_nod not in vagnat.noder:
        raise VagNatFel(f"Okänd startnod '{start_nod}' i vägnätet.")
    if not math.isfinite(start_tid_s):
        raise VagNatFel(f"Ogiltig starttid: {start_tid_s}.")

    sorterade_tider = sorted(float(t) for t in kant_djup_per_tid)
    normaliserat_djup: dict[float, Mapping[str, float]] = {
        float(t): v for t, v in kant_djup_per_tid.items()
    }
    niva_tider = sorted(float(t) for t in vattenniva_per_tid) if vattenniva_per_tid else []
    normaliserad_niva: dict[float, Mapping[str, float]] | None = (
        {float(t): v for t, v in vattenniva_per_tid.items()}
        if vattenniva_per_tid is not None
        else None
    )

    ankomst: dict[str, float] = {nod_id: math.inf for nod_id in vagnat.noder}
    ankomst[start_nod] = float(start_tid_s)
    ko: list[tuple[float, str]] = [(float(start_tid_s), start_nod)]

    while ko:
        nu_tid, nod_id = heapq.heappop(ko)
        if nu_tid > ankomst[nod_id]:
            continue

        for ut in vagnat.grannlista(nod_id):
            restid = _kant_restid_s(
                ut.kant,
                nu_tid,
                sorterade_tider,
                normaliserat_djup,
                fordon,
                normaliserad_niva,
                niva_tider,
            )
            if restid is None:
                continue
            ny_tid = nu_tid + restid
            if ny_tid < ankomst[ut.mal_nod]:
                ankomst[ut.mal_nod] = ny_tid
                heapq.heappush(ko, (ny_tid, ut.mal_nod))

    return ankomst


def berakna_insatstider(
    vagnat: VagNat,
    stationer: Mapping[str, str],
    adresser: Mapping[str, str],
    tider_s: Sequence[float],
    kant_djup_per_tid: Mapping[float, Mapping[str, float]],
    parametrar: RuttParametrarK7,
    fordon_typ: str,
    *,
    skarp: bool = True,
    tillat_testvarden: bool = False,
    vattenniva_per_tid: Mapping[float, Mapping[str, float]] | None = None,
) -> list[InsatstidPost]:
    """Beräknar torr och tidsberoende insatstid för varje `(adress, station, tid)`.

    Validerar K7-parametrarna via `validera_parametrar` före beräkning så att
    inga `null`-värden eller testvärden tillåts passera vid skarp körning.
    """
    validera_parametrar(
        parametrar,
        skarp=skarp,
        tillat_testvarden=tillat_testvarden,
    )
    if fordon_typ not in parametrar.fordon:
        raise RuttParametrarFel(f"Okänd fordonstyp '{fordon_typ}' i K7-parametrar.")
    fordon = parametrar.fordon[fordon_typ]

    for station_id, nod_id in stationer.items():
        if nod_id not in vagnat.noder:
            raise VagNatFel(
                f"Station '{station_id}' refererar okänd nod '{nod_id}' i vägnätet."
            )
    for adress_id, nod_id in adresser.items():
        if nod_id not in vagnat.noder:
            raise VagNatFel(
                f"Adress '{adress_id}' refererar okänd nod '{nod_id}' i vägnätet."
            )

    # Torr referenskörning (vattendjup = 0.0 överallt, oberoende av avresetid)
    torr_djup: dict[float, dict[str, float]] = {0.0: {}}
    torra_ankomster: dict[str, dict[str, float]] = {
        station_id: tidsberoende_dijkstra(
            vagnat,
            station_nod,
            0.0,
            torr_djup,
            fordon,
            vattenniva_per_tid=None,
        )
        for station_id, station_nod in stationer.items()
    }

    poster: list[InsatstidPost] = []
    for t_s in sorted(float(t) for t in tider_s):
        ankomster_per_station = {
            station_id: tidsberoende_dijkstra(
                vagnat,
                station_nod,
                t_s,
                kant_djup_per_tid,
                fordon,
                vattenniva_per_tid=vattenniva_per_tid,
            )
            for station_id, station_nod in stationer.items()
        }
        for adress_id, adress_nod in sorted(adresser.items()):
            for station_id in sorted(stationer):
                ank_tid = ankomster_per_station[station_id][adress_nod]
                insats_s = ank_tid - t_s if math.isfinite(ank_tid) else math.inf
                torr_s = torra_ankomster[station_id][adress_nod]
                poster.append(
                    InsatstidPost(
                        adress_id=adress_id,
                        station_id=station_id,
                        t_s=t_s,
                        insatstid_s=insats_s,
                        torr_insatstid_s=torr_s,
                    )
                )

    return poster
