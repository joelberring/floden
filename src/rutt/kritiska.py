"""Kritiska gatuavsnitt (girig sökning) och ensemblesannolikhet (T07 steg 5–6, K8)."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from rutt.parametrar import (
    RuttParametrarFel,
    RuttParametrarK7,
    berakna_hastighetsfaktor,
    validera_parametrar,
)
from rutt.vagnat import VagNat, VagNatFel, effektivt_vagdjup_m


@dataclass(frozen=True)
class KritisktAvsnitt:
    """Ett kritiskt gatuavsnitt i K8-lagret `kritiska_avsnitt.gpkg`."""

    kant_id: str
    vinst: int
    koordinater: tuple[tuple[float, float], ...]
    djup_m: float
    normal_hastighet_km_h: float


def _nabara_noder_bfs(
    grannar: Mapping[str, list[str]],
    start_noder: Sequence[str],
    redan_nabara: set[str] | None = None,
) -> set[str]:
    """Flerkällors BFS över farbara kanter. Hoppar över `redan_nabara` om angivet."""
    besokta: set[str] = set()
    ko: deque[str] = deque()
    for s in start_noder:
        if redan_nabara is not None and s in redan_nabara:
            continue
        if s not in besokta:
            besokta.add(s)
            ko.append(s)
    while ko:
        u = ko.popleft()
        for v in grannar.get(u, ()):
            if v in besokta or (redan_nabara is not None and v in redan_nabara):
                continue
            besokta.add(v)
            ko.append(v)
    return besokta


def hitta_kritiska_avsnitt(
    vagnat: VagNat,
    stationer: Mapping[str, str],
    adresser: Mapping[str, str],
    t_s: float,
    kant_djup: Mapping[str, float],
    parametrar: RuttParametrarK7,
    fordon_typ: str,
    *,
    max_avsnitt: int = 5,
    skarp: bool = True,
    tillat_testvarden: bool = False,
    vattenniva: Mapping[str, float] | None = None,
) -> list[KritisktAvsnitt]:
    """Identifierar de mest kritiska gatuavsnitten genom girig sökning (T07 steg 5).

    I varje steg väljs det enskilda gatuavsnitt som ger störst `vinst`
    (antal adresser som går från onåbara till nåbara) om avsnittet hålls
    öppet (`djup = 0.0`, och eventuell vattennivå tas bort på kanten), tills
    `max_avsnitt` uppnåtts eller ingen ytterligare vinst > 0 finns.

    Endast ofarbara snittkanter (med ena änden nåbar och andra onåbar) kan ge
    vinst > 0; i oriktade vägnät utvärderas varje kandidat i O(1) via
    sammanhängande komponenter i stället för att köra Dijkstra per kandidat.
    """
    validera_parametrar(
        parametrar,
        skarp=skarp,
        tillat_testvarden=tillat_testvarden,
    )
    if fordon_typ not in parametrar.fordon:
        raise RuttParametrarFel(f"Okänd fordonstyp '{fordon_typ}' i K7-parametrar.")
    if max_avsnitt <= 0:
        return []

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

    fordon = parametrar.fordon[fordon_typ]
    gransdjup = fordon.gransdjup_m
    if gransdjup is None:
        raise RuttParametrarFel(f"Fordon '{fordon_typ}' saknar gransdjup_m.")
    if berakna_hastighetsfaktor(fordon, 0.0) <= 0.0:
        return []

    # Adresser per vägnätsnod för O(1)-räkning av nyvunna adresser
    adr_per_nod: dict[str, int] = {}
    for adr_nod in adresser.values():
        adr_per_nod[adr_nod] = adr_per_nod.get(adr_nod, 0) + 1

    # Bygg farbar grannlista och lista på ofarbara kandidatkanter i deterministisk ordning
    grannar: dict[str, list[str]] = {nod_id: [] for nod_id in vagnat.noder}
    kvarvarande: list[str] = []
    har_enkelriktad = False

    for kant_id in sorted(vagnat.kanter):
        kant = vagnat.kanter[kant_id]
        if kant.enkelriktad:
            har_enkelriktad = True
        eff = effektivt_vagdjup_m(
            kant,
            kant_djup.get(kant_id, 0.0),
            vattenniva.get(kant_id) if vattenniva is not None else None,
        )
        if berakna_hastighetsfaktor(fordon, eff) > 0.0:
            grannar[kant.fran_nod].append(kant.till_nod)
            if not kant.enkelriktad:
                grannar[kant.till_nod].append(kant.fran_nod)
        else:
            kvarvarande.append(kant_id)

    start_noder = list(stationer.values())
    nabara_noder = _nabara_noder_bfs(grannar, start_noder)
    valda: list[KritisktAvsnitt] = []

    for _ in range(max_avsnitt):
        if not kvarvarande:
            break

        basta_kant: str | None = None
        basta_vinst = 0

        if not har_enkelriktad:
            # Oriktad graf: beräkna sammanhängande komponenter av onåbara noder en gång
            komp_id: dict[str, int] = {}
            komp_vinst: list[int] = []
            for nod_id in vagnat.noder:
                if nod_id in nabara_noder or nod_id in komp_id:
                    continue
                cid = len(komp_vinst)
                noder_i_komp = _nabara_noder_bfs(grannar, (nod_id,), redan_nabara=nabara_noder)
                vinst_komp = 0
                for n in noder_i_komp:
                    komp_id[n] = cid
                    vinst_komp += adr_per_nod.get(n, 0)
                komp_vinst.append(vinst_komp)

            for kant_id in kvarvarande:
                kant = vagnat.kanter[kant_id]
                u_in = kant.fran_nod in nabara_noder
                v_in = kant.till_nod in nabara_noder
                if u_in == v_in:
                    continue
                dst = kant.till_nod if u_in else kant.fran_nod
                vinst = komp_vinst[komp_id[dst]]
                if vinst > basta_vinst:
                    basta_vinst = vinst
                    basta_kant = kant_id
        else:
            # Riktad graf: cacha onåbar del-BFS per mål-nod
            vinst_cache: dict[str, int] = {}
            for kant_id in kvarvarande:
                kant = vagnat.kanter[kant_id]
                mal_nod: str | None = None
                if kant.fran_nod in nabara_noder and kant.till_nod not in nabara_noder:
                    mal_nod = kant.till_nod
                elif (
                    not kant.enkelriktad
                    and kant.till_nod in nabara_noder
                    and kant.fran_nod not in nabara_noder
                ):
                    mal_nod = kant.fran_nod
                if mal_nod is None:
                    continue
                if mal_nod not in vinst_cache:
                    ny_komp = _nabara_noder_bfs(grannar, (mal_nod,), redan_nabara=nabara_noder)
                    vinst_cache[mal_nod] = sum(adr_per_nod.get(n, 0) for n in ny_komp)
                vinst = vinst_cache[mal_nod]
                if vinst > basta_vinst:
                    basta_vinst = vinst
                    basta_kant = kant_id

        if basta_kant is None or basta_vinst <= 0:
            break

        kant = vagnat.kanter[basta_kant]
        ursprungligt_eff = effektivt_vagdjup_m(
            kant,
            kant_djup.get(basta_kant, 0.0),
            vattenniva.get(basta_kant) if vattenniva is not None else None,
        )
        valda.append(
            KritisktAvsnitt(
                kant_id=basta_kant,
                vinst=basta_vinst,
                koordinater=vagnat.kant_koordinater(basta_kant),
                djup_m=float(ursprungligt_eff),
                normal_hastighet_km_h=float(kant.normal_hastighet_km_h),
            )
        )
        # Öppna vald kant och expandera nåbara noder inkrementellt
        grannar[kant.fran_nod].append(kant.till_nod)
        if not kant.enkelriktad:
            grannar[kant.till_nod].append(kant.fran_nod)
        ny_start = [
            n for n in (kant.fran_nod, kant.till_nod) if n not in nabara_noder
        ]
        nabara_noder |= _nabara_noder_bfs(grannar, ny_start, redan_nabara=nabara_noder)
        kvarvarande.remove(basta_kant)

    return valda


def berakna_ofarbar_sannolikhet(
    kant_djup_ensemble: Sequence[Mapping[str, float]],
    gransdjup_m: float,
    *,
    vagnat: VagNat | None = None,
    vattenniva_ensemble: Sequence[Mapping[str, float]] | None = None,
) -> dict[str, float]:
    """Beräknar sannolikhet `[0.0, 1.0]` per gatuavsnitt att bli ofarbart över en ensemble.

    En kant räknas som ofarbar i ett scenario när effektivt vattendjup >= `gransdjup_m`.
    """
    if not math.isfinite(gransdjup_m) or gransdjup_m <= 0.0:
        raise RuttParametrarFel(f"Ogiltigt gransdjup_m: {gransdjup_m}")
    if not kant_djup_ensemble:
        raise RuttParametrarFel("Ensemblen får inte vara tom.")
    if vattenniva_ensemble is not None and len(vattenniva_ensemble) != len(kant_djup_ensemble):
        raise RuttParametrarFel(
            "vattenniva_ensemble måste ha samma längd som kant_djup_ensemble."
        )

    alla_kanter: set[str] = (
        set(vagnat.kanter.keys()) if vagnat is not None else set()
    )
    for scenario in kant_djup_ensemble:
        alla_kanter.update(scenario.keys())

    antal_scenarier = len(kant_djup_ensemble)
    ofarbar_antal: dict[str, int] = {kid: 0 for kid in sorted(alla_kanter)}

    for idx, scenario in enumerate(kant_djup_ensemble):
        niva_map = vattenniva_ensemble[idx] if vattenniva_ensemble is not None else None
        for kant_id in alla_kanter:
            mark_d = float(scenario.get(kant_id, 0.0))
            if vagnat is not None and kant_id in vagnat.kanter:
                eff_d = effektivt_vagdjup_m(
                    vagnat.kanter[kant_id],
                    mark_d,
                    niva_map.get(kant_id) if niva_map is not None else None,
                )
            else:
                eff_d = max(0.0, mark_d)
            if eff_d >= gransdjup_m:
                ofarbar_antal[kant_id] += 1

    return {
        kant_id: float(antal) / float(antal_scenarier)
        for kant_id, antal in ofarbar_antal.items()
    }
