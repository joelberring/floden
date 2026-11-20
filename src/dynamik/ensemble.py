"""Ensemblekörning och osäkerhetspercentiler (P10/P50/P90) enligt K6/v1 (T03 steg 6–7)."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from dynamik.handelsestyrd import bygg_graf_struktur_cache, kor_handelsestyrd_kardinal
from dynamik.nodmodell import KONTRAKT_K6, DynamikFel
from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost, skapa_manifest, skriv_manifest
from sankgraf.typer import MARKSLAG_BYGGNAD, SankGraf
from testsvit.k1_io import skriv_geotiff

_CELL_BLOCK_STORLEK = 4096


@dataclass(frozen=True)
class EnsembleScenario:
    """Ett enskilt scenario i en dynamisk ensemble (regn, förluster, brunnar)."""

    scenario_id: str
    regn_tid_s: Sequence[float]
    regn_intensitet_mm_h: Sequence[float]
    avrinning_faktor: float = 1.0
    initial_forlust_mm: float = 0.0
    brunnar_avdrag_m3_s: float = 0.0


@dataclass(frozen=True)
class ScenarioSammanfattning:
    """Nyckeltal och volymkontroll för ett kört ensemblescenario."""

    scenario_id: str
    in_m3: float
    lagrat_m3: float
    ut_m3: float
    volymfel_rel: float
    max_djup_m: float
    overfallsordning: tuple[int, ...]
    kortid_s: float


@dataclass
class EnsembleResultat:
    """Samlade cellvisa och nodvisa percentiler samt prestandamått för en ensemble."""

    percentiler: tuple[float, ...]
    max_djup_percentiler_m: dict[float, NDArray[np.float32]]
    nod_max_niva_percentiler: dict[float, dict[int, float]]
    nod_max_volym_percentiler: dict[float, dict[int, float]]
    scenarier: list[ScenarioSammanfattning]
    max_volymfel_rel: float
    total_kortid_s: float
    medel_kortid_per_scenario_s: float


def _formatera_percentil_filnamn(p: float) -> str:
    avrundad = round(p)
    if abs(p - avrundad) <= 1e-9:
        return f"p{int(avrundad)}_max_djup.tif"
    etikett = f"{p:g}".replace(".", "_")
    return f"p{etikett}_max_djup.tif"


def kor_ensemble(
    graf: SankGraf,
    scenarier: Sequence[EnsembleScenario],
    *,
    percentiler: Sequence[float] = (10.0, 50.0, 90.0),
) -> EnsembleResultat:
    """Kör samtliga ensemblescenarier med den händelsestyrda lösaren och beräknar percentiler."""
    t_wall_start = time.perf_counter()

    if not scenarier:
        raise DynamikFel("scenarier får inte vara tom vid ensemblekörning.")
    if not percentiler:
        raise DynamikFel("percentiler får inte vara tom vid ensemblekörning.")

    pct_tuple = tuple(float(p) for p in percentiler)
    for p in pct_tuple:
        if not math.isfinite(p) or p < 0.0 or p > 100.0:
            raise DynamikFel(f"Percentil måste ligga i [0, 100], fick {p}.")

    n_scen = len(scenarier)
    cache = bygg_graf_struktur_cache(graf)
    sorterade_noder = cache.sorterade_noder
    lov_noder = cache.lov_noder
    n_noder = len(sorterade_noder)
    n_lov = len(lov_noder)

    hojd, bredd = graf.mark_z.shape
    z64 = np.asarray(graf.mark_z, dtype=np.float64)
    aktiv_mask = np.asarray(
        (graf.markslag != MARKSLAG_BYGGNAD) & np.isfinite(z64), dtype=np.bool_
    )
    ct = graf.cell_till_nod

    # Förberäkna varje lövs minsta markhöjd (för snabbt scenario-maxdjup) och cellindex
    lov_cell_idx: list[NDArray[np.intp]] = []
    lov_z_varden: list[NDArray[np.float64]] = []
    lov_min_z: list[float] = []
    ct_flat = ct.ravel()
    z_flat = z64.ravel()
    aktiv_flat = aktiv_mask.ravel()

    for lid in lov_noder:
        idx = np.flatnonzero(aktiv_flat & (ct_flat == lid))
        lov_cell_idx.append(idx)
        if idx.size > 0:
            z_sub = z_flat[idx]
            lov_z_varden.append(z_sub)
            lov_min_z.append(float(np.min(z_sub)))
        else:
            lov_z_varden.append(np.empty(0, dtype=np.float64))
            lov_min_z.append(math.inf)

    lov_niva_matris = np.full((n_scen, n_lov), -math.inf, dtype=np.float64)
    nod_niva_matris = np.zeros((n_scen, n_noder), dtype=np.float64)
    nod_volym_matris = np.zeros((n_scen, n_noder), dtype=np.float64)

    sammanfattningar: list[ScenarioSammanfattning] = []
    max_volymfel = 0.0

    for s_idx, scen in enumerate(scenarier):
        kardinal = kor_handelsestyrd_kardinal(
            graf,
            scen.regn_tid_s,
            scen.regn_intensitet_mm_h,
            cache=cache,
            avrinning_faktor=scen.avrinning_faktor,
            initial_forlust_mm=scen.initial_forlust_mm,
            brunnar_avdrag_m3_s=scen.brunnar_avdrag_m3_s,
        )

        scen_max_djup = 0.0
        for l_idx, lid in enumerate(lov_noder):
            if kardinal.lov_har_vatten.get(lid, False):
                w_val = kardinal.lov_max_niva_rh2000[lid]
                lov_niva_matris[s_idx, l_idx] = w_val
                djup_l = w_val - lov_min_z[l_idx]
                if djup_l > scen_max_djup:
                    scen_max_djup = djup_l

        for n_idx, nid in enumerate(sorterade_noder):
            nod_niva_matris[s_idx, n_idx] = kardinal.nod_max_niva_rh2000[nid]
            nod_volym_matris[s_idx, n_idx] = kardinal.nod_max_volym_m3[nid]

        if kardinal.volymfel_rel > max_volymfel:
            max_volymfel = kardinal.volymfel_rel

        sammanfattningar.append(
            ScenarioSammanfattning(
                scenario_id=scen.scenario_id,
                in_m3=kardinal.in_m3,
                lagrat_m3=kardinal.lagrat_m3,
                ut_m3=kardinal.ut_m3,
                volymfel_rel=kardinal.volymfel_rel,
                max_djup_m=scen_max_djup,
                overfallsordning=kardinal.overfallsordning,
                kortid_s=kardinal.kortid_s,
            )
        )

    # Cellvisa percentiler av max_djup_m (blockvis över varje lövs celler för lågt minne)
    pct_arr = np.asarray(pct_tuple, dtype=np.float64)
    n_pct = len(pct_tuple)
    djup_flat_per_pct = [np.zeros(hojd * bredd, dtype=np.float32) for _ in range(n_pct)]

    for l_idx in range(n_lov):
        idx = lov_cell_idx[l_idx]
        if idx.size == 0:
            continue
        w_kol = lov_niva_matris[:, l_idx]
        if not np.any(np.isfinite(w_kol)):
            continue
        z_sub = lov_z_varden[l_idx]
        for start_i in range(0, idx.size, _CELL_BLOCK_STORLEK):
            slut_i = min(idx.size, start_i + _CELL_BLOCK_STORLEK)
            idx_blk = idx[start_i:slut_i]
            z_blk = z_sub[start_i:slut_i]
            djup_blk = np.maximum(0.0, w_kol[:, None] - z_blk[None, :])
            pct_blk = np.percentile(djup_blk, pct_arr, axis=0)
            for p_i in range(n_pct):
                djup_flat_per_pct[p_i][idx_blk] = np.asarray(pct_blk[p_i], dtype=np.float32)

    max_djup_pct: dict[float, NDArray[np.float32]] = {
        pct_tuple[p_i]: djup_flat_per_pct[p_i].reshape((hojd, bredd))
        for p_i in range(n_pct)
    }

    # Nodvisa percentiler
    nod_niva_pct: dict[float, dict[int, float]] = {p: {} for p in pct_tuple}
    nod_vol_pct: dict[float, dict[int, float]] = {p: {} for p in pct_tuple}
    if n_noder > 0:
        niva_p_mat = np.percentile(nod_niva_matris, pct_arr, axis=0)
        vol_p_mat = np.percentile(nod_volym_matris, pct_arr, axis=0)
        for p_i, p_val in enumerate(pct_tuple):
            for n_idx, nid in enumerate(sorterade_noder):
                nod_niva_pct[p_val][nid] = float(niva_p_mat[p_i, n_idx])
                nod_vol_pct[p_val][nid] = float(vol_p_mat[p_i, n_idx])

    total_kortid = time.perf_counter() - t_wall_start
    return EnsembleResultat(
        percentiler=pct_tuple,
        max_djup_percentiler_m=max_djup_pct,
        nod_max_niva_percentiler=nod_niva_pct,
        nod_max_volym_percentiler=nod_vol_pct,
        scenarier=sammanfattningar,
        max_volymfel_rel=max_volymfel,
        total_kortid_s=total_kortid,
        medel_kortid_per_scenario_s=total_kortid / n_scen,
    )


def skriv_ensemble_k6(
    resultat: EnsembleResultat,
    graf: SankGraf,
    ut_mapp: str | Path,
    *,
    indata: Sequence[FilPost] | None = None,
    parametrar: Mapping[str, Any] | None = None,
    kodversion: str | None = None,
    start: str | None = None,
) -> dict[str, Path]:
    """Skriver ensemblepercentiler till `osakerhet/p10_max_djup.tif` m.fl. samt `run.json` (K6)."""
    mal = Path(ut_mapp)
    osak_mapp = mal / "osakerhet"
    osak_mapp.mkdir(parents=True, exist_ok=True)
    geo = graf.geo

    filer: dict[str, Path] = {}
    utdata_poster: list[FilPost] = []

    for p_val in resultat.percentiler:
        filnamn = _formatera_percentil_filnamn(p_val)
        roll = filnamn.removesuffix(".tif")
        arr = resultat.max_djup_percentiler_m[p_val]
        fil = skriv_geotiff(
            osak_mapp / filnamn,
            np.asarray(arr, dtype=np.float32),
            x_min=geo.x_min,
            y_max=geo.y_max,
            pixelstorlek=geo.pixelstorlek_x,
            epsg=geo.epsg,
        )
        rel_fil = f"osakerhet/{fil.name}"
        filer[roll] = fil
        utdata_poster.append(FilPost(roll=roll, fil=rel_fil, sha256=sha256_fil(fil)))

    sammanfattning_fil = osak_mapp / "ensemble_sammanfattning.json"
    payload = {
        "kontrakt": KONTRAKT_K6,
        "antal_scenarier": len(resultat.scenarier),
        "percentiler": list(resultat.percentiler),
        "max_volymfel_rel": float(resultat.max_volymfel_rel),
        "total_kortid_s": float(resultat.total_kortid_s),
        "medel_kortid_per_scenario_s": float(resultat.medel_kortid_per_scenario_s),
        "nod_max_niva_percentiler": {
            str(p): {str(nid): float(v) for nid, v in noder.items()}
            for p, noder in resultat.nod_max_niva_percentiler.items()
        },
        "nod_max_volym_percentiler": {
            str(p): {str(nid): float(v) for nid, v in noder.items()}
            for p, noder in resultat.nod_max_volym_percentiler.items()
        },
        "scenarier": [
            {
                "scenario_id": s.scenario_id,
                "in_m3": float(s.in_m3),
                "lagrat_m3": float(s.lagrat_m3),
                "ut_m3": float(s.ut_m3),
                "volymfel_rel": float(s.volymfel_rel),
                "max_djup_m": float(s.max_djup_m),
                "overfallsordning": [int(nid) for nid in s.overfallsordning],
                "kortid_s": float(s.kortid_s),
            }
            for s in resultat.scenarier
        ],
    }
    sammanfattning_fil.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    filer["ensemble_sammanfattning"] = sammanfattning_fil
    utdata_poster.append(
        FilPost(
            roll="ensemble_sammanfattning",
            fil=f"osakerhet/{sammanfattning_fil.name}",
            sha256=sha256_fil(sammanfattning_fil),
        )
    )

    param_dict: dict[str, Any] = {
        "kontrakt_ut": KONTRAKT_K6,
        "niva": "graf",
        "lage": "ensemble",
        "percentiler": list(resultat.percentiler),
        "antal_scenarier": len(resultat.scenarier),
        "epsg": geo.epsg,
    }
    if parametrar is not None:
        param_dict.update(parametrar)

    manifest = skapa_manifest(
        modul="dynamik",
        indata=list(indata) if indata is not None else [],
        parametrar=param_dict,
        utdata=utdata_poster,
        matvarden={
            "antal_scenarier": len(resultat.scenarier),
            "max_volymfel_rel": float(resultat.max_volymfel_rel),
            "total_kortid_s": float(resultat.total_kortid_s),
            "medel_kortid_per_scenario_s": float(resultat.medel_kortid_per_scenario_s),
        },
        kodversion=kodversion,
        start=start,
    )
    run_fil = skriv_manifest(manifest, mal / "run.json")
    filer["run"] = run_fil
    return filer
