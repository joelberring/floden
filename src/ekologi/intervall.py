"""Monotona osäkerhetsintervall och CELF-kompensationsoptimering för ekologi (`src/ekologi/`)."""

from __future__ import annotations

import heapq
import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray

from ekologi.konnektivitet import berakna_konnektivitet
from ekologi.motstand import bygg_motstandsyta
from ekologi.typer import (
    ArtProfil,
    EkologiAtgard,
    EkologiAtgardVal,
    EkologiFel,
    EkologiIntervallResultat,
    KallbaseratIntervall,
)


def kor_monoton_ekologi_intervall(
    markslag: NDArray[np.uint8] | NDArray[np.integer[Any]],
    profil: ArtProfil,
    *,
    cell_m: float,
    motstand_faktor_intervall: tuple[float, float] | None = None,
    spridning_faktor_intervall: tuple[float, float] = (0.8, 1.25),
    kronhojd_m: NDArray[np.float64] | NDArray[np.float32] | None = None,
) -> EkologiIntervallResultat:
    """Beräknar monotont garanterat intervall `[eca_min, eca_max]` med exakt 2 körningar.

    Eftersom `ECA` och `PC` är strikt antitona i motståndet `R` och strikt monotona i
    spridningsavståndet `d_disp` ger `(R_max, d_min)` en garanterad undre gräns och
    `(R_min, d_max)` en garanterad övre gräns utan Monte Carlo.
    """
    r_iv = (
        profil.motstand_osakerhet_faktor
        if motstand_faktor_intervall is None
        else motstand_faktor_intervall
    )
    r_lo, r_hi = float(r_iv[0]), float(r_iv[1])
    d_lo, d_hi = float(spridning_faktor_intervall[0]), float(spridning_faktor_intervall[1])
    if not (math.isfinite(r_lo) and math.isfinite(r_hi) and 0.0 < r_lo <= r_hi):
        raise EkologiFel(f"Ogiltigt motstand_faktor_intervall: {r_iv}.")
    if not (math.isfinite(d_lo) and math.isfinite(d_hi) and 0.0 < d_lo <= d_hi):
        raise EkologiFel(f"Ogiltigt spridning_faktor_intervall: {spridning_faktor_intervall}.")

    res_lo = berakna_konnektivitet(
        markslag,
        profil,
        cell_m=cell_m,
        kronhojd_m=kronhojd_m,
        motstand_faktor=r_hi,
        spridning_faktor=d_lo,
    )
    res_hi = berakna_konnektivitet(
        markslag,
        profil,
        cell_m=cell_m,
        kronhojd_m=kronhojd_m,
        motstand_faktor=r_lo,
        spridning_faktor=d_hi,
    )
    return EkologiIntervallResultat(
        profil_id=profil.profil_id,
        eca_min_m2=res_lo.eca_m2,
        eca_max_m2=res_hi.eca_m2,
        pc_min=res_lo.pc,
        pc_max=res_hi.pc,
        motstand_faktor_intervall=(r_lo, r_hi),
        spridning_faktor_intervall=(d_lo, d_hi),
    )


def valj_basta_ekologi_atgarder_k(
    markslag: NDArray[np.uint8] | NDArray[np.integer[Any]],
    profil: ArtProfil,
    atgarder: Sequence[EkologiAtgard],
    *,
    cell_m: float,
    k: int = 5,
) -> list[EkologiAtgardVal]:
    """Väljer topp-k kompensationsåtgärder med CELF (lazy greedy) som maximerar ΔECA (m²)."""
    if k <= 0 or not atgarder:
        return []
    ms_bas = np.asarray(markslag)
    bas_motstand = bygg_motstandsyta(ms_bas, profil, cell_m=cell_m).motstand
    hab_kod = min(profil.habitat_markslag)

    for atg in atgarder:
        if atg.mask.shape != ms_bas.shape:
            raise EkologiFel(
                f"Åtgärd '{atg.atgard_id}' har form {atg.mask.shape}, förväntade {ms_bas.shape}."
            )
        if not (math.isfinite(atg.nytt_motstand) and atg.nytt_motstand >= 1.0):
            raise EkologiFel(f"Åtgärd '{atg.atgard_id}' har ogiltigt motstånd {atg.nytt_motstand}.")

    def _utvardera_eca(valda_lista: Sequence[EkologiAtgard]) -> float:
        ms_nu = ms_bas.copy()
        r_nu = bas_motstand.copy()
        for a in valda_lista:
            if a.gor_till_habitat:
                ms_nu[a.mask] = hab_kod
            r_nu = np.where(a.mask, np.minimum(r_nu, float(a.nytt_motstand)), r_nu)
        # Skicka modifierat motstånd via extra_motstand=None och direkt konnektivitet via r_nu
        from ekologi.habitat import extrahera_habitatflackar
        from ekologi.konnektivitet import berakna_kostnadsavstand, berakna_pc_eca
        from ekologi.typer import MotstandsYta

        hab = extrahera_habitatflackar(ms_nu, profil, cell_m=cell_m)
        g_nu = np.where(np.isfinite(r_nu), 1.0 / r_nu, 0.0)
        myta = MotstandsYta(
            motstand=r_nu,
            konduktans=g_nu,
            cell_m=float(cell_m),
            profil_id=profil.profil_id,
        )
        d_mat = berakna_kostnadsavstand(myta, hab)
        areor = np.array([f.effektiv_area_m2 for f in hab.flackar], dtype=np.float64)
        _, eca_val, _, _ = berakna_pc_eca(
            areor, d_mat, profil.spridningsavstand_m, hab.analys_area_m2
        )
        return eca_val

    eca_bas = _utvardera_eca(())
    eca_nu = eca_bas

    # CELF-kö: (-marginalvinst, atgard_id, steg_utvarderad, index)
    ko: list[tuple[float, str, int, int]] = []
    vinst_ensam: dict[str, float] = {}
    for idx, atg in enumerate(atgarder):
        v = max(0.0, _utvardera_eca((atg,)) - eca_bas)
        vinst_ensam[atg.atgard_id] = v
        heapq.heappush(ko, (-v, atg.atgard_id, 0, idx))

    valda_atg: list[EkologiAtgard] = []
    resultat: list[EkologiAtgardVal] = []
    kumulativ = 0.0

    for steg in range(1, min(k, len(atgarder)) + 1):
        while ko:
            neg_v, aid, utv_steg, idx = heapq.heappop(ko)
            if utv_steg == steg - 1:
                marg = -neg_v
                if marg <= 0.0:
                    return resultat
                valda_atg.append(atgarder[idx])
                eca_nu += marg
                kumulativ += marg
                resultat.append(
                    EkologiAtgardVal(
                        atgard_id=aid,
                        marginal_eca_vinst_m2=marg,
                        kumulativ_eca_vinst_m2=kumulativ,
                        vinst_ensam_m2=vinst_ensam[aid],
                    )
                )
                break
            ny_eca = _utvardera_eca((*valda_atg, atgarder[idx]))
            ny_marg = max(0.0, ny_eca - eca_nu)
            heapq.heappush(ko, (-ny_marg, aid, steg - 1, idx))

    return resultat


def kallbaserat_intervall(
    natverk_id: str, resultat_per_scenario: Mapping[str, Any]
) -> KallbaseratIntervall:
    """Lägsta och högsta ECA och PC mellan scenarier/källor för samma nätverk.

    `resultat_per_scenario` är scenario-id till `KonnektivitetsResultat`. Ersätter intervallet med
    faktorerna x0,7/x1,4. Med ett enda scenario är intervallet en punkt (`ett_scenario`), eftersom
    inget källbaserat intervall då finns. Monotonin (G6) gäller: för scenarier som bara skiljer i
    d50 är ECA och PC icke-avtagande i d50, så min och max ligger vid minsta respektive största d50.
    """
    if not resultat_per_scenario:
        raise EkologiFel("Inga scenarier att bilda ett källbaserat intervall av.")
    ids = tuple(resultat_per_scenario)
    eca = tuple(float(resultat_per_scenario[i].eca_m2) for i in ids)
    pc = tuple(float(resultat_per_scenario[i].pc) for i in ids)
    return KallbaseratIntervall(
        natverk_id=natverk_id,
        scenario_ids=ids,
        eca_per_scenario_m2=eca,
        pc_per_scenario=pc,
        eca_min_m2=min(eca),
        eca_max_m2=max(eca),
        pc_min=min(pc),
        pc_max=max(pc),
        ett_scenario=len(ids) == 1,
    )
