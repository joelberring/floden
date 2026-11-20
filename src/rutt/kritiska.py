"""Kritiska gatuavsnitt (girig sökning) och ensemblesannolikhet (T07 steg 5–6, K8)."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from rutt.dijkstra import tidsberoende_dijkstra
from rutt.parametrar import RuttParametrarFel, RuttParametrarK7, validera_parametrar
from rutt.vagnat import VagNat, VagNatFel, effektivt_vagdjup_m


@dataclass(frozen=True)
class KritisktAvsnitt:
    """Ett kritiskt gatuavsnitt i K8-lagret `kritiska_avsnitt.gpkg`."""

    kant_id: str
    vinst: int
    koordinater: tuple[tuple[float, float], ...]
    djup_m: float
    normal_hastighet_km_h: float


def _nabor_adresser(
    vagnat: VagNat,
    stationer: Mapping[str, str],
    adresser: Mapping[str, str],
    t_s: float,
    kant_djup: Mapping[str, float],
    parametrar: RuttParametrarK7,
    fordon_typ: str,
    vattenniva: Mapping[str, float] | None,
) -> set[str]:
    fordon = parametrar.fordon[fordon_typ]
    djup_serie: dict[float, Mapping[str, float]] = {float(t_s): kant_djup}
    niva_serie: dict[float, Mapping[str, float]] | None = (
        {float(t_s): vattenniva} if vattenniva is not None else None
    )

    nabara: set[str] = set()
    ankomster: dict[str, dict[str, float]] = {
        st_id: tidsberoende_dijkstra(
            vagnat,
            st_nod,
            float(t_s),
            djup_serie,
            fordon,
            vattenniva_per_tid=niva_serie,
        )
        for st_id, st_nod in stationer.items()
    }
    for adr_id, adr_nod in adresser.items():
        if any(math.isfinite(ankomster[st_id][adr_nod]) for st_id in stationer):
            nabara.add(adr_id)
    return nabara


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

    aktuellt_djup: dict[str, float] = dict(kant_djup)
    aktuell_niva: dict[str, float] | None = (
        dict(vattenniva) if vattenniva is not None else None
    )

    bas_nabara = _nabor_adresser(
        vagnat,
        stationer,
        adresser,
        t_s,
        aktuellt_djup,
        parametrar,
        fordon_typ,
        aktuell_niva,
    )

    # Kandidatkanter är alla kanter vars effektiva djup > 0 eller >= gränsdjup
    kandidater: list[str] = []
    for kant_id in sorted(vagnat.kanter):
        kant = vagnat.kanter[kant_id]
        eff = effektivt_vagdjup_m(
            kant,
            aktuellt_djup.get(kant_id, 0.0),
            aktuell_niva.get(kant_id) if aktuell_niva is not None else None,
        )
        if eff > 0.0 or eff >= gransdjup:
            kandidater.append(kant_id)

    valda: list[KritisktAvsnitt] = []
    kvarvarande = list(kandidater)

    for _ in range(max_avsnitt):
        basta_kant: str | None = None
        basta_vinst = 0
        basta_nabara: set[str] | None = None

        for kant_id in kvarvarande:
            test_djup = dict(aktuellt_djup)
            test_djup[kant_id] = 0.0
            test_niva = dict(aktuell_niva) if aktuell_niva is not None else None
            if test_niva is not None:
                test_niva.pop(kant_id, None)

            nya_nabara = _nabor_adresser(
                vagnat,
                stationer,
                adresser,
                t_s,
                test_djup,
                parametrar,
                fordon_typ,
                test_niva,
            )
            vinst = len(nya_nabara - bas_nabara)
            if vinst > basta_vinst:
                basta_vinst = vinst
                basta_kant = kant_id
                basta_nabara = nya_nabara

        if basta_kant is None or basta_vinst <= 0 or basta_nabara is None:
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
        aktuellt_djup[basta_kant] = 0.0
        if aktuell_niva is not None:
            aktuell_niva.pop(basta_kant, None)
        bas_nabara = basta_nabara
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
