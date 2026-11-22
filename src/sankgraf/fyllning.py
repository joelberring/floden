"""Statisk volymbevarande Fill–Spill–Merge-fyllning på sänkgraf (T02 steg 7)."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from sankgraf.flodesvagar import STANDARD_REFERENSDJUP_M
from sankgraf.hierarki import _applicera_oppningar, bygg_sankgraf, hitta_instangda_omraden
from sankgraf.persistens import filtrera_persistens
from sankgraf.typer import (
    HAV_NOD_ID,
    MARKSLAG_BYGGNAD,
    SankGraf,
    SankGrafFel,
    SlutsankaResultat,
    StatiskFyllningResultat,
    las_brytlinjer_fran_gpkg,
    las_oppningar_fran_gpkg,
)
from testsvit.k1_io import (
    BrytlinjePost,
    OppningPost,
    las_geotiff,
    las_regn_csv,
)

_VOLYM_TOL_M3 = 1e-12


def berakna_total_regn_m(tidsserie: Sequence[tuple[float, float]]) -> float:
    """Integrerar en K4-regnserie `(tid_s, intensitet_mm_h)` till total nederbörd i meter."""
    if len(tidsserie) < 2:
        raise SankGrafFel("Regntidsserien måste innehålla minst två tidpunkter.")

    total_m = 0.0
    for idx in range(len(tidsserie) - 1):
        t0, i0 = float(tidsserie[idx][0]), float(tidsserie[idx][1])
        t1, i1 = float(tidsserie[idx + 1][0]), float(tidsserie[idx + 1][1])
        if not (
            math.isfinite(t0)
            and math.isfinite(t1)
            and math.isfinite(i0)
            and math.isfinite(i1)
        ):
            raise SankGrafFel("Regntidsserien innehåller icke-ändliga värden.")
        if t1 < t0:
            raise SankGrafFel(f"Regntidsserien är inte sorterad i tid: {t1} < {t0}.")
        if i0 < 0.0 or i1 < 0.0:
            raise SankGrafFel("Regnintensitet får inte vara negativ.")
        dt_h = (t1 - t0) / 3600.0
        medel_mm_h = 0.5 * (i0 + i1)
        total_m += (medel_mm_h * dt_h) / 1000.0
    return total_m


def _djupaste_lov_i_graf(graf: SankGraf, nod_id: int) -> int:
    curr = nod_id
    while graf.noder[curr].barn_id:
        curr = min(
            graf.noder[curr].barn_id,
            key=lambda b: (graf.noder[b].botten_rh2000, b),
        )
    return curr


def statisk_fyllning(
    graf: SankGraf,
    regn_m: float | NDArray[np.float64],
) -> StatiskFyllningResultat:
    """Kör exakt volymbevarande Fill–Spill–Merge-fyllning för givet regn (meter).

    Args:
        graf: Byggd (ofiltrerad eller persistensfiltrerad) `SankGraf`.
        regn_m: Total regnmängd i meter, antingen skalär eller 2D-raster med samma
            form som `graf.mark_z`.

    Returns:
        `StatiskFyllningResultat` med djup, vattennivåer, per-nod-nivåer och volymkontroll.
    """
    hojd, bredd = graf.mark_z.shape
    z64 = np.asarray(graf.mark_z, dtype=np.float64)
    aktiv = np.asarray((graf.markslag != MARKSLAG_BYGGNAD) & np.isfinite(z64), dtype=np.bool_)

    if isinstance(regn_m, np.ndarray):
        regn_arr = np.asarray(regn_m, dtype=np.float64)
        if regn_arr.shape != (hojd, bredd):
            raise SankGrafFel(
                f"Regnraster har form {regn_arr.shape}, förväntade {(hojd, bredd)}."
            )
        if not np.all(np.isfinite(regn_arr[aktiv])) or np.any(regn_arr[aktiv] < 0.0):
            raise SankGrafFel("Regnraster innehåller NaN/Inf eller negativa värden.")
    else:
        regn_val = float(regn_m)
        if not math.isfinite(regn_val) or regn_val < 0.0:
            raise SankGrafFel(f"Regnmängd måste vara ändlig och >= 0, fick {regn_val}.")
        regn_arr = np.full((hojd, bredd), regn_val, dtype=np.float64)

    cell_area_m2 = float(graf.geo.pixelstorlek_x) * float(graf.geo.pixelstorlek_y)
    cell_vol_m3 = np.where(aktiv, regn_arr * cell_area_m2, 0.0)
    in_m3 = float(np.sum(cell_vol_m3))

    if not graf.noder or in_m3 == 0.0:
        noll_djup = np.zeros((hojd, bredd), dtype=np.float32)
        wse = np.asarray(graf.mark_z, dtype=np.float32).copy()
        nod_niva = {nid: n.botten_rh2000 for nid, n in graf.noder.items()}
        nod_vol = {nid: 0.0 for nid in graf.noder}
        ut_val = in_m3 if not graf.noder else 0.0
        vol_fel = abs(in_m3 - ut_val) / in_m3 if in_m3 > 0.0 else 0.0
        return StatiskFyllningResultat(
            djup_m=noll_djup,
            vattenniva_rh2000=wse,
            nod_niva_rh2000=nod_niva,
            nod_volym_m3=nod_vol,
            in_m3=in_m3,
            lagrat_m3=0.0,
            ut_m3=ut_val,
            volymfel_rel=vol_fel,
            utflodes_celler=[],
        )

    # Initiera tillrinning per lövnod samt direkt randavrinning (HAV_NOD_ID)
    ct = graf.cell_till_nod
    ut_m3 = float(np.sum(cell_vol_m3[aktiv & (ct == HAV_NOD_ID)]))

    volym: dict[int, float] = {nid: 0.0 for nid in graf.noder}
    max_volym: dict[int, float] = {nid: n.max_volym_m3 for nid, n in graf.noder.items()}
    aktiv_nod: dict[int, bool] = {nid: n.ar_lov for nid, n in graf.noder.items()}
    mergad: dict[int, bool] = {nid: False for nid in graf.noder}

    lov_mask = aktiv & (ct >= 0)
    if np.any(lov_mask):
        lov_ids = ct[lov_mask]
        lov_vols = cell_vol_m3[lov_mask]
        unika_ids, inv_idx = np.unique(lov_ids, return_inverse=True)
        summerade = np.bincount(np.asarray(inv_idx, dtype=np.intp).ravel(), weights=lov_vols)
        for nid_np, v_np in zip(unika_ids, summerade, strict=True):
            nid = int(nid_np)
            if nid in volym:
                volym[nid] = float(v_np)
            else:
                ut_m3 += float(v_np)

    def _aktiv_forfader(nid: int) -> int:
        curr = nid
        while mergad[curr]:
            forald = graf.noder[curr].forald_id
            if forald is None:
                break
            curr = forald
        return curr

    def _hitta_mottagare(kalla_id: int) -> int | None:
        raw_lov = graf.ut_lov_id.get(kalla_id)
        if raw_lov is not None and raw_lov in graf.noder:
            return _aktiv_forfader(raw_lov)
        ut_id = graf.noder[kalla_id].ut_till_id
        if ut_id is None or ut_id not in graf.noder:
            return None
        if aktiv_nod[ut_id]:
            return ut_id
        if mergad[ut_id]:
            return _aktiv_forfader(ut_id)
        lov_mal = _djupaste_lov_i_graf(graf, ut_id)
        return _aktiv_forfader(lov_mal)

    # Kö av noder som överstiger (eller når) sin maxvolym
    ko: deque[int] = deque()
    i_ko: set[int] = set()

    def _ko_lagg_till(nid: int) -> None:
        if nid not in i_ko:
            i_ko.add(nid)
            ko.append(nid)

    for nid in sorted(graf.noder):
        if aktiv_nod[nid] and volym[nid] > max_volym[nid] + _VOLYM_TOL_M3:
            _ko_lagg_till(nid)

    utflodes_sedda: set[tuple[int, int]] = set()
    utflodes_celler: list[tuple[int, int]] = []
    over_hinder: dict[int, float] = {}

    max_steg = max(1000, len(graf.noder) * 50)
    steg = 0
    while ko:
        steg += 1
        if steg > max_steg:
            raise SankGrafFel("Fill–Spill–Merge konvergerade inte inom förväntat antal steg.")

        nid = ko.popleft()
        i_ko.discard(nid)
        if not aktiv_nod[nid]:
            continue

        nod = graf.noder[nid]
        v_max = max_volym[nid]
        overskott = volym[nid] - v_max

        if nod.forald_id is None:
            if overskott <= _VOLYM_TOL_M3:
                continue
            # Rotnod: svämmar över till nedströms nod eller ut ur området (HAV).
            # En slutsänka (K2/v2) "svämmar över" bara när den nått sitt hinder.
            volym[nid] = v_max
            mottagare = _hitta_mottagare(nid)
            if mottagare is None:
                ut_m3 += overskott
                if nod.ar_slutsanka:
                    over_hinder[nid] = over_hinder.get(nid, 0.0) + overskott
                for rc in graf.overfall_celler.get(nid, ()):
                    if rc not in utflodes_sedda:
                        utflodes_sedda.add(rc)
                        utflodes_celler.append(rc)
            else:
                volym[mottagare] += overskott
                if volym[mottagare] > max_volym[mottagare] + _VOLYM_TOL_M3:
                    _ko_lagg_till(mottagare)
        else:
            p_id = nod.forald_id
            p_nod = graf.noder[p_id]
            syskon = [c for c in p_nod.barn_id if c != nid]
            if len(syskon) != 1:
                raise SankGrafFel(f"Föräldernod {p_id} har oväntade barn: {p_nod.barn_id}.")
            s_id = syskon[0]

            if aktiv_nod[s_id]:
                kan_slas_ihop = (
                    volym[nid] >= v_max - _VOLYM_TOL_M3
                    and volym[s_id] + max(0.0, overskott) >= max_volym[s_id] - _VOLYM_TOL_M3
                )
                if kan_slas_ihop:
                    total_v = volym[nid] + volym[s_id]
                    mergad[nid] = True
                    mergad[s_id] = True
                    aktiv_nod[nid] = False
                    aktiv_nod[s_id] = False
                    volym[nid] = v_max
                    volym[s_id] = max_volym[s_id]

                    aktiv_nod[p_id] = True
                    volym[p_id] = total_v
                    gp_id = p_nod.forald_id
                    if volym[p_id] > max_volym[p_id] + _VOLYM_TOL_M3 or (
                        gp_id is not None and volym[p_id] >= max_volym[p_id] - _VOLYM_TOL_M3
                    ):
                        _ko_lagg_till(p_id)
                elif overskott > _VOLYM_TOL_M3:
                    volym[nid] = v_max
                    volym[s_id] += overskott
            elif overskott > _VOLYM_TOL_M3:
                # Systern är en sammansatt nod vars barn ännu inte slagits ihop:
                # överskottet rinner ned i mottagande löv inuti systerns delträd.
                volym[nid] = v_max
                mottagare = _hitta_mottagare(nid)
                if mottagare is None:
                    ut_m3 += overskott
                else:
                    volym[mottagare] += overskott
                    if volym[mottagare] > max_volym[mottagare] + _VOLYM_TOL_M3:
                        _ko_lagg_till(mottagare)

    # Beräkna exakt vattennivå per aktiv nod (och ärv ned till sammanslagna barn)
    aktiv_niva: dict[int, float] = {}
    for nid, ar_aktiv in aktiv_nod.items():
        if ar_aktiv:
            nod = graf.noder[nid]
            v = volym[nid]
            aktiv_niva[nid] = nod.niva_vid_volym(v) if v > 0.0 else nod.botten_rh2000

    nod_niva_rh2000: dict[int, float] = {}
    nod_volym_m3: dict[int, float] = {}
    for nid in sorted(graf.noder):
        if aktiv_nod[nid]:
            nod_niva_rh2000[nid] = aktiv_niva[nid]
            nod_volym_m3[nid] = volym[nid]
        elif mergad[nid]:
            ctrl = _aktiv_forfader(nid)
            nod_niva_rh2000[nid] = aktiv_niva[ctrl]
            nod_volym_m3[nid] = max_volym[nid]
        else:
            # Inaktiv sammansatt förälder (barn ej sammanslagna ännu)
            barn_nivaer = [
                aktiv_niva[_aktiv_forfader(b)]
                for b in graf.noder[nid].barn_id
                if _aktiv_forfader(b) in aktiv_niva
            ]
            nod_niva_rh2000[nid] = (
                max(barn_nivaer) if barn_nivaer else graf.noder[nid].botten_rh2000
            )
            nod_volym_m3[nid] = sum(
                volym[_aktiv_forfader(lid)]
                for lid in {
                    _aktiv_forfader(lov) for lov in graf.lov_under_nod(nid)
                }
            )

    # Mappa varje cell till sin styrande aktiva nods vattennivå
    max_nod_id = max(graf.noder)
    lov_till_niva = np.full(max_nod_id + 1, -math.inf, dtype=np.float64)
    for nid, nod in graf.noder.items():
        if nod.ar_lov:
            ctrl = _aktiv_forfader(nid)
            if volym[ctrl] > 0.0:
                lov_till_niva[nid] = aktiv_niva[ctrl]

    djup_f64 = np.zeros((hojd, bredd), dtype=np.float64)
    wse_f64 = z64.copy()

    if np.any(lov_mask):
        cell_niva = lov_till_niva[ct[lov_mask]]
        cell_djup = np.maximum(0.0, cell_niva - z64[lov_mask])
        djup_f64[lov_mask] = cell_djup
        wse_f64[lov_mask] = np.where(cell_djup > 0.0, cell_niva, z64[lov_mask])

    lagrat_m3 = float(np.sum(djup_f64) * cell_area_m2)
    volymfel_rel = abs(in_m3 - ut_m3 - lagrat_m3) / in_m3 if in_m3 > 0.0 else 0.0
    slutsankor, slut_mask = slutsanka_sammanstallning(
        graf, djup_f64, nod_niva_rh2000, over_hinder
    )

    return StatiskFyllningResultat(
        djup_m=np.asarray(djup_f64, dtype=np.float32),
        vattenniva_rh2000=np.asarray(wse_f64, dtype=np.float32),
        nod_niva_rh2000=nod_niva_rh2000,
        nod_volym_m3=nod_volym_m3,
        in_m3=in_m3,
        lagrat_m3=lagrat_m3,
        ut_m3=ut_m3,
        volymfel_rel=volymfel_rel,
        utflodes_celler=utflodes_celler,
        slutsankor=slutsankor,
        slutsanka_mask=slut_mask,
    )


def slutsanka_sammanstallning(
    graf: SankGraf,
    djup_m: NDArray[np.float64] | NDArray[np.float32],
    nod_niva_rh2000: dict[int, float],
    over_hinder_m3: dict[int, float],
) -> tuple[tuple[SlutsankaResultat, ...], NDArray[np.bool_] | None]:
    """Per slutsänka: area, nivå, största djup, lagrad volym och vatten över hindret.

    Returnerar också cellmasken för slutsänkorna (None om grafen saknar slutsänkor, så att
    grafer utan slutsänkor ger samma resultat som före K2/v2).
    """
    rotter = graf.slutsankor()
    if not rotter:
        return (), None
    cell_area_m2 = float(graf.geo.pixelstorlek_x) * float(graf.geo.pixelstorlek_y)
    max_id = max(graf.noder)
    lov_till_rot = np.full(max_id + 1, -1, dtype=np.int64)
    for r in rotter:
        lov_till_rot[np.asarray(graf.lov_under_nod(r.nod_id), dtype=np.int64)] = r.nod_id
    ct = graf.cell_till_nod
    rot_per_cell = np.full(ct.shape, -1, dtype=np.int64)
    giltig = ct >= 0
    rot_per_cell[giltig] = lov_till_rot[ct[giltig]]
    mask = rot_per_cell >= 0
    djup = np.asarray(djup_m, dtype=np.float64)
    ids = rot_per_cell[mask]
    antal = np.bincount(ids, minlength=max_id + 1)
    volym = np.bincount(ids, weights=djup[mask], minlength=max_id + 1) * cell_area_m2
    maxdjup = np.zeros(max_id + 1, dtype=np.float64)
    np.maximum.at(maxdjup, ids, djup[mask])
    ut = tuple(
        SlutsankaResultat(
            nod_id=r.nod_id,
            cell_antal=int(antal[r.nod_id]),
            area_m2=float(antal[r.nod_id]) * cell_area_m2,
            hinder_rh2000=float(r.hinder_rh2000 if r.hinder_rh2000 is not None else math.nan),
            niva_rh2000=float(nod_niva_rh2000.get(r.nod_id, r.botten_rh2000)),
            max_djup_m=float(maxdjup[r.nod_id]),
            volym_m3=float(volym[r.nod_id]),
            over_hinder_m3=float(over_hinder_m3.get(r.nod_id, 0.0)),
        )
        for r in rotter
    )
    return ut, np.asarray(mask, dtype=np.bool_)


def instangda_omraden_i_k1_mapp(
    k1_indata_mapp: Path | str,
) -> list[tuple[int, int, int]]:
    """Instängda markområden i en K1-ruta (efter öppningar), utan att bygga sänkgrafen.

    Samma regel som `bygg_sankgraf` (se `InstangtOmradeFel`).
    Returnerar (antal celler, rad, kolumn).
    """
    mapp = Path(k1_indata_mapp)
    mark_raw, geo = las_geotiff(mapp / "mark.tif")
    slag_raw, _ = las_geotiff(mapp / "markslag.tif")
    opp_fil = mapp / "oppningar.gpkg"
    oppningar = las_oppningar_fran_gpkg(opp_fil) if opp_fil.is_file() else []
    eff_z, eff_slag = _applicera_oppningar(
        np.asarray(mark_raw, dtype=np.float32), np.asarray(slag_raw, dtype=np.uint8), geo, oppningar
    )
    aktiv = np.asarray((eff_slag != MARKSLAG_BYGGNAD) & np.isfinite(eff_z), dtype=np.bool_)
    return hitta_instangda_omraden(aktiv)


def kor_statisk_k1_mapp(
    k1_indata_mapp: Path | str,
    regn_csv: Path | str | None = None,
    regn_m: float | None = None,
    persistens_faktor: float | None = None,
    *,
    applicera_brytlinjer: bool = True,
    applicera_oppningar: bool = True,
    referensdjup_m: float = STANDARD_REFERENSDJUP_M,
) -> tuple[SankGraf, StatiskFyllningResultat]:
    """Läser en K1-indatamapp + K4 `regn.csv`, bygger sänkgraf och kör statisk fyllning."""
    mapp = Path(k1_indata_mapp)
    if not mapp.is_dir():
        raise SankGrafFel(f"K1-indatamapp saknas: {mapp}")

    mark_raw, geo = las_geotiff(mapp / "mark.tif")
    osak_raw, _ = las_geotiff(mapp / "osakerhet.tif")
    slag_raw, _ = las_geotiff(mapp / "markslag.tif")

    mark_z = np.asarray(mark_raw, dtype=np.float32)
    osakerhet_z = np.asarray(osak_raw, dtype=np.float32)
    markslag = np.asarray(slag_raw, dtype=np.uint8)

    brytlinjer: list[BrytlinjePost] = []
    bryt_fil = mapp / "brytlinjer.gpkg"
    if applicera_brytlinjer and bryt_fil.is_file():
        brytlinjer = las_brytlinjer_fran_gpkg(bryt_fil)

    oppningar: list[OppningPost] = []
    opp_fil = mapp / "oppningar.gpkg"
    if applicera_oppningar and opp_fil.is_file():
        oppningar = las_oppningar_fran_gpkg(opp_fil)

    if regn_m is not None:
        total_regn_m = float(regn_m)
    else:
        regn_fil = Path(regn_csv) if regn_csv is not None else (mapp / "regn.csv")
        tidsserie = las_regn_csv(regn_fil)
        total_regn_m = berakna_total_regn_m(tidsserie)

    graf = bygg_sankgraf(
        mark_z,
        osakerhet_z,
        markslag,
        geo,
        brytlinjer=brytlinjer,
        oppningar=oppningar,
        referensdjup_m=referensdjup_m,
    )
    if persistens_faktor is not None:
        graf = filtrera_persistens(graf, faktor=persistens_faktor)

    resultat = statisk_fyllning(graf, total_regn_m)
    return graf, resultat
