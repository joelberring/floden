"""Inkrementell / lokal omräkning av sänkgraf för projektlappar (T04, Q4)."""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from lapp.kontrakt import LappFel
from sankgraf.flodesvagar import (
    MANNING_N_PER_MARKSLAG,
    MIN_LUTNING,
    STANDARD_REFERENSDJUP_M,
    manning_hastighet_m_s,
)
from sankgraf.hierarki import (
    _BOTTEN_MARKOR,
    _SADEL_TIE_TOL_M,
    _applicera_oppningar,
    _berakna_kantbarriarer,
    _instangda_etiketter,
    berakna_kurvor_for_celler,
    bygg_sankgraf,
)
from sankgraf.typer import (
    BYGGNAD_NOD_ID,
    HAV_NOD_ID,
    MARKSLAG_BYGGNAD,
    FlodesVag,
    SankGraf,
    SankNod,
)
from testsvit.k1_io import BrytlinjePost, GeoTransformInfo, OppningPost


@dataclass(frozen=True)
class LokalOmrakningResultat:
    """Resultat från inkrementell / lokal omräkning av en `SankGraf`."""

    ny_graf: SankGraf
    berorda_bas_noder: set[int]
    antal_ateranvanda_noder: int
    antal_omraknade_celler: int
    kortid_s: float


def _utvidga_4grannar(mask: NDArray[np.bool_]) -> NDArray[np.bool_]:
    utvidgad = np.array(mask, dtype=bool, copy=True)
    utvidgad[1:, :] |= mask[:-1, :]
    utvidgad[:-1, :] |= mask[1:, :]
    utvidgad[:, 1:] |= mask[:, :-1]
    utvidgad[:, :-1] |= mask[:, 1:]
    return np.asarray(utvidgad, dtype=np.bool_)


def _bygg_nod_grannskap(bas_graf: SankGraf) -> dict[int, set[int]]:
    """Bygger oriktad komponentgraf över trädlänkar (`forald_id`, `barn_id`) och överfall."""
    grannar: dict[int, set[int]] = {nid: set() for nid in bas_graf.noder}
    for nid, nod in bas_graf.noder.items():
        if nod.forald_id is not None and nod.forald_id in grannar:
            grannar[nid].add(nod.forald_id)
            grannar[nod.forald_id].add(nid)
        for barn_id in nod.barn_id:
            if barn_id in grannar:
                grannar[nid].add(barn_id)
                grannar[barn_id].add(nid)
        if nod.ut_till_id is not None and nod.ut_till_id in grannar:
            grannar[nid].add(nod.ut_till_id)
            grannar[nod.ut_till_id].add(nid)
        ut_lov = bas_graf.ut_lov_id.get(nid)
        if ut_lov is not None and ut_lov in grannar:
            grannar[nid].add(ut_lov)
            grannar[ut_lov].add(nid)
    return grannar


def _komponenter_i_graf(
    bas_graf: SankGraf,
) -> tuple[dict[int, int], dict[int, set[int]], dict[int, float]]:
    """Partitionerar `bas_graf.noder` i hydrologiskt slutna komponenter samt rotöverfall."""
    grannar = _bygg_nod_grannskap(bas_graf)
    nod_till_komp: dict[int, int] = {}
    komp_till_noder: dict[int, set[int]] = {}
    komp_id = 0

    for start_nid in sorted(bas_graf.noder):
        if start_nid in nod_till_komp:
            continue
        komp_noder: set[int] = set()
        ko: deque[int] = deque([start_nid])
        nod_till_komp[start_nid] = komp_id
        while ko:
            curr = ko.popleft()
            komp_noder.add(curr)
            for nxt in grannar[curr]:
                if nxt not in nod_till_komp:
                    nod_till_komp[nxt] = komp_id
                    ko.append(nxt)
        komp_till_noder[komp_id] = komp_noder
        komp_id += 1

    # Varje nod blir HAV-ansluten i Kruskal vid sitt eget sammanslagningsträds rots överfallsnivå
    rot_overfall: dict[int, float] = {}
    for nid in bas_graf.noder:
        curr = nid
        while True:
            forald = bas_graf.noder[curr].forald_id
            if forald is None or forald not in bas_graf.noder:
                break
            curr = forald
        rot_overfall[nid] = float(bas_graf.noder[curr].overfall_rh2000)

    return nod_till_komp, komp_till_noder, rot_overfall


def hitta_berorda_noder(
    bas_graf: SankGraf,
    andrad_mask: NDArray[np.bool_],
) -> set[int]:
    """Hittar alla noder i `bas_graf` vars avrinningsområde eller träd berörs av `andrad_mask`.

    Fångar:
    - alla lövnoder som överlappar `andrad_mask` eller dess 4-grannar (för sadeländringar),
    - hela deras sammanslagningsträd (`forald_id`, `barn_id`),
    - samt transitivt kopplade nedströms- och uppströmsnoder via `ut_till_id` / `ut_lov_id`.
    """
    if andrad_mask.shape != bas_graf.cell_till_nod.shape:
        raise LappFel(
            f"andrad_mask har form {andrad_mask.shape}, förväntade {bas_graf.cell_till_nod.shape}."
        )
    if not bas_graf.noder or not np.any(andrad_mask):
        return set()

    utvidgad = _utvidga_4grannar(andrad_mask)
    raw_ids = np.unique(bas_graf.cell_till_nod[utvidgad])
    start_noder = [int(nid) for nid in raw_ids if int(nid) in bas_graf.noder]
    if not start_noder:
        return set()

    grannar = _bygg_nod_grannskap(bas_graf)
    berorda: set[int] = set(start_noder)
    ko: deque[int] = deque(start_noder)
    while ko:
        curr = ko.popleft()
        for nxt in grannar[curr]:
            if nxt not in berorda:
                berorda.add(nxt)
                ko.append(nxt)
    return berorda


def _hitta_uppstroms_hav_celler(
    bas_z_flat: NDArray[np.float64],
    bas_ct_flat: NDArray[np.int64],
    bas_aktiv_flat: NDArray[np.bool_],
    hojd: int,
    bredd: int,
    start_mask: NDArray[np.bool_],
) -> NDArray[np.bool_]:
    """Hittar alla `HAV_NOD_ID`-celler som kan ha runnit genom `start_mask` (inkl. platåer)."""
    hav_besokt = np.zeros(hojd * bredd, dtype=bool)
    ko: deque[int] = deque()

    for idx_np in np.flatnonzero(start_mask.ravel()):
        idx = int(idx_np)
        if not bas_aktiv_flat[idx]:
            continue
        r, c = divmod(idx, bredd)
        z_u = float(bas_z_flat[idx])
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < hojd and 0 <= nc < bredd:
                nxt = nr * bredd + nc
                if (
                    bas_aktiv_flat[nxt]
                    and int(bas_ct_flat[nxt]) == HAV_NOD_ID
                    and not hav_besokt[nxt]
                    and float(bas_z_flat[nxt]) >= z_u
                ):
                    hav_besokt[nxt] = True
                    ko.append(nxt)

    while ko:
        curr = ko.popleft()
        cr, cc = divmod(curr, bredd)
        z_c = float(bas_z_flat[curr])
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = cr + dr, cc + dc
            if 0 <= nr < hojd and 0 <= nc < bredd:
                nxt = nr * bredd + nc
                if (
                    bas_aktiv_flat[nxt]
                    and int(bas_ct_flat[nxt]) == HAV_NOD_ID
                    and not hav_besokt[nxt]
                    and float(bas_z_flat[nxt]) >= z_c
                ):
                    hav_besokt[nxt] = True
                    ko.append(nxt)

    return np.asarray(hav_besokt.reshape((hojd, bredd)), dtype=np.bool_)


def _bygg_lokala_flodesvagar(
    z_flat: NDArray[np.float64],
    slag_flat: NDArray[np.uint8],
    flow_to_flat: NDArray[np.int64],
    ny_ct_flat: NDArray[np.int64],
    omrakna_indices: NDArray[np.intp],
    geo: GeoTransformInfo,
    *,
    start_vag_id: int,
    referensdjup_m: float,
) -> list[FlodesVag]:
    """Bygger representativa flödesvägar (en längsta väg per ny lövnod) i det omräknade området."""
    bredd = geo.bredd
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)

    # Välj högsta startcell per ny lövnod inom omrakna_indices
    basta_per_nod: dict[int, tuple[float, int]] = {}
    for u_np in omrakna_indices:
        u = int(u_np)
        nid = int(ny_ct_flat[u])
        if nid < 0 or int(flow_to_flat[u]) < 0:
            continue
        zu = float(z_flat[u])
        tidigare = basta_per_nod.get(nid)
        if tidigare is None or zu > tidigare[0]:
            basta_per_nod[nid] = (zu, u)

    vagar: list[FlodesVag] = []
    vag_id = start_vag_id
    for nid in sorted(basta_per_nod):
        _, start_u = basta_per_nod[nid]
        punkter: list[tuple[float, float]] = []
        langd_m = 0.0
        tid_s = 0.0
        curr = start_u
        sedda: set[int] = set()
        while curr >= 0 and curr not in sedda:
            sedda.add(curr)
            cr, cc = divmod(curr, bredd)
            punkter.append((geo.x_min + (cc + 0.5) * dx, geo.y_max - (cr + 0.5) * dy))
            nxt = int(flow_to_flat[curr])
            if nxt < 0:
                break
            nr, nc = divmod(nxt, bredd)
            ds = math.hypot((nc - cc) * dx, (nr - cr) * dy)
            lutning = max((float(z_flat[curr]) - float(z_flat[nxt])) / ds, MIN_LUTNING)
            n_val = MANNING_N_PER_MARKSLAG.get(int(slag_flat[curr]), 0.030)
            v_hast = manning_hastighet_m_s(lutning, n_val, referensdjup_m)
            langd_m += ds
            tid_s += ds / v_hast
            curr = nxt

        if len(punkter) >= 2 and langd_m > 0.0:
            z_start = float(z_flat[start_u])
            z_slut = float(z_flat[curr]) if curr >= 0 else z_start
            medel_lutning = max((z_start - z_slut) / langd_m, MIN_LUTNING)
            vagar.append(
                FlodesVag(
                    id=vag_id,
                    nod_id=nid,
                    koordinater=tuple(punkter),
                    langd_m=langd_m,
                    lutning=medel_lutning,
                    gangtid_s=tid_s,
                    referensdjup_m=float(referensdjup_m),
                )
            )
            vag_id += 1
    return vagar


def uppdatera_sankgraf_lokalt(
    bas_graf: SankGraf,
    ny_mark_z: NDArray[np.float32],
    ny_osakerhet_z: NDArray[np.float32],
    ny_markslag: NDArray[np.uint8],
    andrad_mask: NDArray[np.bool_],
    *,
    brytlinjer: Sequence[BrytlinjePost] = (),
    oppningar: Sequence[OppningPost] = (),
    referensdjup_m: float = STANDARD_REFERENSDJUP_M,
) -> LokalOmrakningResultat:
    """Uppdaterar `bas_graf` lokalt genom att endast räkna om berörda delträd och nedströms.

    Alla oberoende, oberörda komponenter i `bas_graf` återanvänds direkt utan omräkning.
    Om flödesriktningar eller sadeltrösklar vid delområdets rand visar att en bevarad
    komponent ändå påverkas, expanderas det berörda delområdet automatiskt till fixpunkt så
    att resultatet blir matematiskt identiskt med en fullständig `bygg_sankgraf`.
    """
    t_wall_start = time.perf_counter()
    geo = bas_graf.geo
    hojd, bredd = geo.hojd, geo.bredd

    if (
        ny_mark_z.shape != (hojd, bredd)
        or ny_osakerhet_z.shape != (hojd, bredd)
        or ny_markslag.shape != (hojd, bredd)
        or andrad_mask.shape != (hojd, bredd)
    ):
        raise LappFel(
            f"Oförenliga rasterformer mot bas_graf ({hojd}x{bredd}): "
            f"mark={ny_mark_z.shape}, osakerhet={ny_osakerhet_z.shape}, "
            f"markslag={ny_markslag.shape}, andrad_mask={andrad_mask.shape}."
        )

    eff_z, eff_slag = _applicera_oppningar(ny_mark_z, ny_markslag, geo, oppningar)
    z_lika = (eff_z == bas_graf.mark_z) | (np.isnan(eff_z) & np.isnan(bas_graf.mark_z))
    slag_lika = eff_slag == bas_graf.markslag
    eff_andrad = np.asarray(andrad_mask | ~(z_lika & slag_lika), dtype=np.bool_)

    if not np.any(eff_andrad):
        ny_kopia = SankGraf(
            noder=dict(bas_graf.noder),
            cell_till_nod=np.array(bas_graf.cell_till_nod, dtype=np.int64, copy=True),
            flodesvagar=list(bas_graf.flodesvagar),
            geo=geo,
            filtrerad=bas_graf.filtrerad,
            persistens_faktor=bas_graf.persistens_faktor,
            mark_z=eff_z,
            markslag=eff_slag,
            ut_lov_id=dict(bas_graf.ut_lov_id),
            overfall_celler=dict(bas_graf.overfall_celler),
        )
        return LokalOmrakningResultat(
            ny_graf=ny_kopia,
            berorda_bas_noder=set(),
            antal_ateranvanda_noder=len(bas_graf.noder),
            antal_omraknade_celler=0,
            kortid_s=time.perf_counter() - t_wall_start,
        )

    # Om basgrafen saknar noder alls, kör direkt full byggning
    if not bas_graf.noder:
        full_graf = bygg_sankgraf(
            ny_mark_z,
            ny_osakerhet_z,
            ny_markslag,
            geo,
            brytlinjer=brytlinjer,
            oppningar=oppningar,
            referensdjup_m=referensdjup_m,
        )
        return LokalOmrakningResultat(
            ny_graf=full_graf,
            berorda_bas_noder=set(),
            antal_ateranvanda_noder=0,
            antal_omraknade_celler=hojd * bredd,
            kortid_s=time.perf_counter() - t_wall_start,
        )

    n_celler = hojd * bredd
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    cell_area_m2 = dx * dy

    aktiv = np.asarray((eff_slag != MARKSLAG_BYGGNAD) & np.isfinite(eff_z), dtype=np.bool_)
    z_flat = np.asarray(eff_z, dtype=np.float64).ravel()
    osak_flat = np.asarray(ny_osakerhet_z, dtype=np.float64).ravel()
    slag_flat = eff_slag.ravel()
    aktiv_flat = aktiv.ravel()

    bas_z_flat = np.asarray(bas_graf.mark_z, dtype=np.float64).ravel()
    bas_ct_2d = bas_graf.cell_till_nod
    bas_ct_flat = bas_ct_2d.ravel()
    bas_aktiv_flat = np.asarray(
        (bas_graf.markslag != MARKSLAG_BYGGNAD) & np.isfinite(bas_graf.mark_z), dtype=np.bool_
    ).ravel()

    kantbarriarer = _berakna_kantbarriarer(z_flat, aktiv_flat, geo, brytlinjer)
    nod_till_komp, komp_till_noder, rot_overfall = _komponenter_i_graf(bas_graf)

    berorda_noder = hitta_berorda_noder(bas_graf, eff_andrad)

    # Slutsänkor (K2/v2): om ändringen rör en slutsänka i basen eller gränsar till ett instängt
    # område i den nya marken räknas hela grafen om med `bygg_sankgraf`. Resultatet är då per
    # definition detsamma som en fullständig byggning (slutsänkornas hinder och ringar är
    # globala egenskaper som den lokala sadelsökningen inte ser). Övriga ändringar återanvänder
    # basens noder, även bevarade slutsänkor, som förut.
    slut_bas = {n.nod_id for n in bas_graf.slutsankor()}
    ny_etiketter, ny_instangda = _instangda_etiketter(aktiv)
    ny_instangd_vid_andring = bool(ny_instangda) and bool(
        np.any(
            np.isin(ny_etiketter, np.asarray(ny_instangda, dtype=np.int32))
            & _utvidga_4grannar(eff_andrad)
        )
    )

    def _full_omrakning() -> LokalOmrakningResultat:
        full_graf = bygg_sankgraf(
            ny_mark_z,
            ny_osakerhet_z,
            ny_markslag,
            geo,
            brytlinjer=brytlinjer,
            oppningar=oppningar,
            referensdjup_m=referensdjup_m,
        )
        return LokalOmrakningResultat(
            ny_graf=full_graf,
            berorda_bas_noder=set(bas_graf.noder.keys()),
            antal_ateranvanda_noder=0,
            antal_omraknade_celler=n_celler,
            kortid_s=time.perf_counter() - t_wall_start,
        )

    if ny_instangd_vid_andring or (berorda_noder & slut_bas):
        return _full_omrakning()
    # Säkerställ att alla 4-grannar till eff_andrad som är HAV också ingår i initialt delområde
    utvidgad_andrad = _utvidga_4grannar(eff_andrad)
    uppstroms_hav = _hitta_uppstroms_hav_celler(
        bas_z_flat, bas_ct_flat, bas_aktiv_flat, hojd, bredd, eff_andrad
    )

    def _ar_rand(u_idx: int) -> bool:
        r, c = divmod(u_idx, bredd)
        return bool(r == 0 or r == hojd - 1 or c == 0 or c == bredd - 1)

    def _tillganglig_platt_kant(u_idx: int, v_idx: int, z_niv: float) -> bool:
        if not kantbarriarer:
            return True
        nyckel = (u_idx, v_idx) if u_idx < v_idx else (v_idx, u_idx)
        return kantbarriarer.get(nyckel, z_niv) <= z_niv

    def _sadel_cell(u: int, v: int) -> int:
        if v < 0:
            return u
        zu = float(z_flat[u])
        zv = float(z_flat[v])
        if zu > zv:
            return u
        if zv > zu:
            return v
        return min(u, v)

    extra_hav_mask = np.zeros((hojd, bredd), dtype=bool)

    while True:
        bevarade_noder = set(bas_graf.noder.keys()) - berorda_noder
        if not bevarade_noder or (berorda_noder & slut_bas):
            return _full_omrakning()

        berorda_lov = [nid for nid in berorda_noder if bas_graf.noder[nid].ar_lov]
        omrakna_mask = np.array(eff_andrad | uppstroms_hav | extra_hav_mask, dtype=bool, copy=True)
        # Lägg till direkta HAV-grannar till ändrade celler
        omrakna_mask |= utvidgad_andrad & (bas_ct_2d == HAV_NOD_ID)
        if berorda_lov:
            berorda_lov_arr = np.asarray(berorda_lov, dtype=np.int64)
            omrakna_mask |= np.isin(bas_ct_2d, berorda_lov_arr)

        omrakna_flat = omrakna_mask.ravel()
        omrakna_indices = np.flatnonzero(omrakna_flat)

        # 1. Lokal brantaste lutning på omrakna_indices (kontrollerar gränsvillkor mot bevarade)
        flow_to_lokal = np.full(n_celler, BYGGNAD_NOD_ID, dtype=np.int64)
        expanderade_komp: set[int] = set()

        for u_np in omrakna_indices:
            u = int(u_np)
            if not aktiv_flat[u]:
                continue
            ur, uc = divmod(u, bredd)
            zu = float(z_flat[u])
            min_z = zu
            mal = BYGGNAD_NOD_ID
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                vr, vc = ur + dr, uc + dc
                if 0 <= vr < hojd and 0 <= vc < bredd:
                    v = vr * bredd + vc
                    if not aktiv_flat[v]:
                        continue
                    if kantbarriarer:
                        nyckel = (u, v) if u < v else (v, u)
                        if nyckel in kantbarriarer and kantbarriarer[nyckel] > zu:
                            continue
                    zv = float(z_flat[v])
                    if zv < min_z:
                        min_z = zv
                        mal = v
            if mal >= 0 and not omrakna_flat[mal]:
                bas_mal_nod = int(bas_ct_flat[mal])
                if bas_mal_nod == HAV_NOD_ID:
                    flow_to_lokal[u] = HAV_NOD_ID
                elif bas_mal_nod in bevarade_noder:
                    expanderade_komp.add(nod_till_komp[bas_mal_nod])
                else:
                    flow_to_lokal[u] = HAV_NOD_ID
            else:
                flow_to_lokal[u] = mal

        if expanderade_komp:
            for cid in expanderade_komp:
                berorda_noder |= komp_till_noder[cid]
            continue

        # 2. Lokala likahöjdsplatåer och nya lövsänkor inom omrakna_indices
        lokal_ct = np.full(n_celler, BYGGNAD_NOD_ID, dtype=np.int64)
        besokt_platt = np.zeros(n_celler, dtype=bool)
        antal_lokala_lov = 0
        nya_hav_platt_fro: list[tuple[int, float]] = []

        for start_np in omrakna_indices:
            start_u = int(start_np)
            if not aktiv_flat[start_u]:
                continue
            ft_s = int(flow_to_lokal[start_u])
            if ft_s == HAV_NOD_ID:
                lokal_ct[start_u] = HAV_NOD_ID
                continue
            if ft_s >= 0 or besokt_platt[start_u]:
                continue

            z_f = float(z_flat[start_u])
            komponent: list[int] = []
            utlopp: list[int] = []
            ko: deque[int] = deque([start_u])
            besokt_platt[start_u] = True

            while ko:
                curr = ko.popleft()
                komponent.append(curr)
                curr_har_utlopp = (
                    int(flow_to_lokal[curr]) >= 0
                    or int(flow_to_lokal[curr]) == HAV_NOD_ID
                    or _ar_rand(curr)
                )

                cr, cc = divmod(curr, bredd)
                for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nr, nc = cr + dr, cc + dc
                    if 0 <= nr < hojd and 0 <= nc < bredd:
                        nxt = nr * bredd + nc
                        if (
                            aktiv_flat[nxt]
                            and float(z_flat[nxt]) == z_f
                            and _tillganglig_platt_kant(curr, nxt, z_f)
                        ):
                            if omrakna_flat[nxt]:
                                if not besokt_platt[nxt]:
                                    besokt_platt[nxt] = True
                                    ko.append(nxt)
                            else:
                                bas_nxt_nod = int(bas_ct_flat[nxt])
                                if bas_nxt_nod == HAV_NOD_ID:
                                    # Likahöjdsplatån sträcker sig in i frysta HAV-celler:
                                    # inkludera hela HAV-platån för exakt multi-source BFS-delare.
                                    nya_hav_platt_fro.append((nxt, z_f))
                                elif bas_nxt_nod in bevarade_noder:
                                    expanderade_komp.add(nod_till_komp[bas_nxt_nod])

                if curr_har_utlopp:
                    utlopp.append(curr)

            if expanderade_komp or nya_hav_platt_fro:
                break

            if not utlopp:
                lov_id = antal_lokala_lov
                antal_lokala_lov += 1
                for cell in komponent:
                    lokal_ct[cell] = lov_id
                    flow_to_lokal[cell] = _BOTTEN_MARKOR
            else:
                utlopp.sort()
                bfs_ko: deque[int] = deque()
                tilldelad: set[int] = set(utlopp)
                komp_set = set(komponent)
                for u_ut in utlopp:
                    if int(flow_to_lokal[u_ut]) < 0:
                        flow_to_lokal[u_ut] = HAV_NOD_ID
                        lokal_ct[u_ut] = HAV_NOD_ID
                    bfs_ko.append(u_ut)

                while bfs_ko:
                    curr = bfs_ko.popleft()
                    cr, cc = divmod(curr, bredd)
                    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        nr, nc = cr + dr, cc + dc
                        if 0 <= nr < hojd and 0 <= nc < bredd:
                            nxt = nr * bredd + nc
                            if (
                                nxt in komp_set
                                and nxt not in tilldelad
                                and _tillganglig_platt_kant(curr, nxt, z_f)
                            ):
                                tilldelad.add(nxt)
                                flow_to_lokal[nxt] = curr
                                bfs_ko.append(nxt)

        if expanderade_komp or nya_hav_platt_fro:
            for cid in expanderade_komp:
                berorda_noder |= komp_till_noder[cid]
            if nya_hav_platt_fro:
                extra_flat = extra_hav_mask.ravel()
                for fro_cell, z_niva in nya_hav_platt_fro:
                    if extra_flat[fro_cell]:
                        continue
                    platt_ko: deque[int] = deque([fro_cell])
                    extra_flat[fro_cell] = True
                    while platt_ko:
                        pc = platt_ko.popleft()
                        pr, pcol = divmod(pc, bredd)
                        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                            nr, nc = pr + dr, pcol + dc
                            if 0 <= nr < hojd and 0 <= nc < bredd:
                                nxt = nr * bredd + nc
                                if (
                                    aktiv_flat[nxt]
                                    and not extra_flat[nxt]
                                    and not omrakna_flat[nxt]
                                    and int(bas_ct_flat[nxt]) == HAV_NOD_ID
                                    and float(z_flat[nxt]) == z_niva
                                    and _tillganglig_platt_kant(pc, nxt, z_niva)
                                ):
                                    extra_flat[nxt] = True
                                    platt_ko.append(nxt)
                extra_hav_mask = np.asarray(extra_flat.reshape((hojd, bredd)), dtype=np.bool_)
            continue

        # 3. Vägkompression inom omrakna_indices
        for u_np in omrakna_indices:
            u = int(u_np)
            if not aktiv_flat[u] or int(lokal_ct[u]) != BYGGNAD_NOD_ID:
                continue
            kedja: list[int] = []
            curr = u
            while curr >= 0 and int(lokal_ct[curr]) == BYGGNAD_NOD_ID:
                kedja.append(curr)
                nxt = int(flow_to_lokal[curr])
                if nxt == HAV_NOD_ID:
                    lokal_ct[curr] = HAV_NOD_ID
                    kedja.pop()
                    break
                curr = nxt
            mal_nod = int(lokal_ct[curr]) if curr >= 0 else HAV_NOD_ID
            for cell in kedja:
                lokal_ct[cell] = mal_nod

        # 4. Samla lokala sadelkanter + kontrollera sadelgräns mot bevarade komponenter
        par_kanter: dict[tuple[int, int], tuple[float, list[tuple[int, int, int, int]]]] = {}
        bevarat_lov_till_ext: dict[int, int] = {}
        ext_till_bevarat_lov: dict[int, int] = {}

        def _ext_id_for_bevarat_lov(
            bevarat_lov: int,
            *,
            _antal_lokala: int = antal_lokala_lov,
            _b_till_ext: dict[int, int] = bevarat_lov_till_ext,
            _ext_till_b: dict[int, int] = ext_till_bevarat_lov,
        ) -> int:
            bef = _b_till_ext.get(bevarat_lov)
            if bef is not None:
                return bef
            ny_ext = _antal_lokala + len(_b_till_ext)
            _b_till_ext[bevarat_lov] = ny_ext
            _ext_till_b[ny_ext] = bevarat_lov
            return ny_ext

        def _registrera_kant(
            u: int,
            v: int,
            cu: int,
            cv: int,
            w: float,
            *,
            _kanter: dict[tuple[int, int], tuple[float, list[tuple[int, int, int, int]]]] = (
                par_kanter
            ),
        ) -> None:
            nyckel = (cu, cv) if cu < cv else (cv, cu)
            bef = _kanter.get(nyckel)
            if bef is None or w < bef[0] - _SADEL_TIE_TOL_M:
                _kanter[nyckel] = (w, [(u, v, cu, cv)])
            elif abs(w - bef[0]) <= _SADEL_TIE_TOL_M:
                bef[1].append((u, v, cu, cv))

        if antal_lokala_lov > 0:
            for u_np in omrakna_indices:
                u = int(u_np)
                if not aktiv_flat[u]:
                    continue
                cu = int(lokal_ct[u])
                ur, uc = divmod(u, bredd)

                if cu >= 0 and _ar_rand(u):
                    _registrera_kant(u, -1, cu, HAV_NOD_ID, float(z_flat[u]))

                for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    vr, vc = ur + dr, uc + dc
                    if not (0 <= vr < hojd and 0 <= vc < bredd):
                        continue
                    v = vr * bredd + vc
                    if not aktiv_flat[v]:
                        continue
                    kant_nyckel = (u, v) if u < v else (v, u)
                    w = kantbarriarer.get(kant_nyckel, max(float(z_flat[u]), float(z_flat[v])))

                    if omrakna_flat[v]:
                        if u < v:
                            cv = int(lokal_ct[v])
                            if cu != cv:
                                _registrera_kant(u, v, cu, cv, w)
                    elif cu >= 0:
                        bas_v_nod = int(bas_ct_flat[v])
                        if bas_v_nod == HAV_NOD_ID:
                            _registrera_kant(u, v, cu, HAV_NOD_ID, w)
                        elif bas_v_nod in bevarade_noder:
                            if w <= rot_overfall[bas_v_nod] + _SADEL_TIE_TOL_M:
                                expanderade_komp.add(nod_till_komp[bas_v_nod])
                            else:
                                ext_v = _ext_id_for_bevarat_lov(bas_v_nod)
                                _registrera_kant(u, v, cu, ext_v, w)

        if expanderade_komp:
            for cid in expanderade_komp:
                berorda_noder |= komp_till_noder[cid]
            continue

        # Fixpunkt nådd! Bygg lokalt Kruskal-träd och slå ihop med bevarade noder.
        break

    # 5. Bygg nya noder i det lokala delområdet (med unika ID ovanför alla bevarade noder)
    id_offset = max(bevarade_noder, default=-1) + 1
    nya_noder: dict[int, SankNod] = {nid: bas_graf.noder[nid] for nid in bevarade_noder}
    ny_ut_lov_id: dict[int, int | None] = {
        nid: bas_graf.ut_lov_id.get(nid) for nid in bevarade_noder
    }
    ny_overfall_celler: dict[int, tuple[tuple[int, int], ...]] = {
        nid: bas_graf.overfall_celler[nid]
        for nid in bevarade_noder
        if nid in bas_graf.overfall_celler
    }

    ny_ct_flat = np.array(bas_ct_flat, dtype=np.int64, copy=True)
    for u_np in omrakna_indices:
        u = int(u_np)
        if not aktiv_flat[u]:
            ny_ct_flat[u] = BYGGNAD_NOD_ID
        else:
            lid = int(lokal_ct[u])
            ny_ct_flat[u] = (lid + id_offset) if lid >= 0 else HAV_NOD_ID

    if antal_lokala_lov > 0:
        n_ext = len(ext_till_bevarat_lov)
        tot_uf = 1 + antal_lokala_lov + n_ext
        uf_parent = list(range(tot_uf))
        ar_hav = [False] * tot_uf
        ar_hav[0] = True
        for ext_idx in range(n_ext):
            ar_hav[1 + antal_lokala_lov + ext_idx] = True
        topp_nod: list[int] = [HAV_NOD_ID] + list(range(antal_lokala_lov + n_ext))

        def _uf_hitta(x: int) -> int:
            while uf_parent[x] != x:
                uf_parent[x] = uf_parent[uf_parent[x]]
                x = uf_parent[x]
            return x

        forald_karta: dict[int, int | None] = {i: None for i in range(antal_lokala_lov)}
        barn_karta: dict[int, tuple[int, ...]] = {i: () for i in range(antal_lokala_lov)}
        overfall_z_karta: dict[int, float] = {}
        overfall_xy_karta: dict[int, tuple[float, float]] = {}
        osakerhet_karta: dict[int, float] = {}
        ut_till_karta: dict[int, int | None] = {i: None for i in range(antal_lokala_lov)}
        ut_lov_karta: dict[int, int | None] = {i: None for i in range(antal_lokala_lov)}
        lokal_overfall_celler: dict[int, tuple[tuple[int, int], ...]] = {}

        sorterade_par = sorted(
            (
                (w_min, nyckel[0], nyckel[1], kanter)
                for nyckel, (w_min, kanter) in par_kanter.items()
            ),
            key=lambda item: (item[0], item[1], item[2]),
        )

        nasta_lokal_id = antal_lokala_lov

        for w_min, c_min, c_max, kanter in sorterade_par:
            ra = _uf_hitta(c_min + 1)
            rb = _uf_hitta(c_max + 1)
            if ra == rb:
                continue

            kanter.sort(key=lambda e: (e[0], e[1]))
            u_rep, v_rep, cu_rep, cv_rep = kanter[len(kanter) // 2]
            sc_idx = _sadel_cell(u_rep, v_rep)
            sr, sc = divmod(sc_idx, bredd)
            sadel_x = geo.x_min + (sc + 0.5) * dx
            sadel_y = geo.y_max - (sr + 0.5) * dy
            sadel_osak = (
                float(osak_flat[sc_idx]) if math.isfinite(float(osak_flat[sc_idx])) else 0.0
            )

            sadel_rc_lista: list[tuple[int, int]] = []
            sedda_rc: set[tuple[int, int]] = set()
            for eu, ev, _, _ in kanter:
                rc = divmod(_sadel_cell(eu, ev), bredd)
                if rc not in sedda_rc:
                    sedda_rc.add(rc)
                    sadel_rc_lista.append(rc)
            sadel_rc_tup = tuple(sadel_rc_lista)

            if ar_hav[ra] and ar_hav[rb]:
                continue

            if ar_hav[ra] or ar_hav[rb]:
                if ar_hav[ra]:
                    r_int = rb
                    andra_lov = cu_rep if _uf_hitta(cu_rep + 1) == ra else cv_rep
                else:
                    r_int = ra
                    andra_lov = cu_rep if _uf_hitta(cu_rep + 1) == rb else cv_rep

                nod_id = topp_nod[r_int]
                if andra_lov == HAV_NOD_ID:
                    ut_target: int | None = None
                elif andra_lov in ext_till_bevarat_lov:
                    # Överfall direkt till en bevarad lövnod med sitt verkliga nod_id
                    ut_target = ext_till_bevarat_lov[andra_lov]
                else:
                    ut_target = int(andra_lov) + id_offset

                overfall_z_karta[nod_id] = float(w_min)
                overfall_xy_karta[nod_id] = (sadel_x, sadel_y)
                osakerhet_karta[nod_id] = sadel_osak
                forald_karta[nod_id] = None
                ut_till_karta[nod_id] = ut_target
                ut_lov_karta[nod_id] = ut_target
                lokal_overfall_celler[nod_id] = sadel_rc_tup

                uf_parent[r_int] = 0
                ar_hav[r_int] = True
            else:
                a_id = topp_nod[ra]
                b_id = topp_nod[rb]
                lov_a = cu_rep if _uf_hitta(cu_rep + 1) == ra else cv_rep
                lov_b = cv_rep if _uf_hitta(cu_rep + 1) == ra else cu_rep
                if a_id > b_id:
                    a_id, b_id = b_id, a_id
                    lov_a, lov_b = lov_b, lov_a
                    ra, rb = rb, ra

                p_id = nasta_lokal_id
                nasta_lokal_id += 1
                forald_karta[p_id] = None
                barn_karta[p_id] = (a_id, b_id)
                ut_till_karta[p_id] = None
                ut_lov_karta[p_id] = None

                forald_karta[a_id] = p_id
                forald_karta[b_id] = p_id
                overfall_z_karta[a_id] = float(w_min)
                overfall_z_karta[b_id] = float(w_min)
                overfall_xy_karta[a_id] = (sadel_x, sadel_y)
                overfall_xy_karta[b_id] = (sadel_x, sadel_y)
                osakerhet_karta[a_id] = sadel_osak
                osakerhet_karta[b_id] = sadel_osak
                ut_till_karta[a_id] = b_id + id_offset
                ut_till_karta[b_id] = a_id + id_offset
                ut_lov_karta[a_id] = int(lov_b) + id_offset
                ut_lov_karta[b_id] = int(lov_a) + id_offset
                lokal_overfall_celler[a_id] = sadel_rc_tup
                lokal_overfall_celler[b_id] = sadel_rc_tup

                uf_parent[rb] = ra
                topp_nod[ra] = p_id

        # Samla cellhöjder per ny lövsänka
        celler_per_lov_lista: dict[int, list[float]] = {i: [] for i in range(antal_lokala_lov)}
        for u_np in omrakna_indices:
            u = int(u_np)
            lid = int(lokal_ct[u])
            if lid >= 0:
                celler_per_lov_lista[lid].append(float(z_flat[u]))
        celler_per_lov: dict[int, NDArray[np.float64]] = {
            lid: np.asarray(vals, dtype=np.float64)
            for lid, vals in celler_per_lov_lista.items()
        }

        lov_i_deltrad: dict[int, list[int]] = {}
        for lid in range(nasta_lokal_id):
            barn = barn_karta[lid]
            if not barn:
                lov_i_deltrad[lid] = [lid]
                z_arr = celler_per_lov.get(lid, np.empty(0, dtype=np.float64))
                avrinning_m2 = float(z_arr.size) * cell_area_m2
            else:
                alla_lov: list[int] = []
                for b_id in barn:
                    alla_lov.extend(lov_i_deltrad[b_id])
                lov_i_deltrad[lid] = alla_lov
                z_delar = [celler_per_lov[x] for x in alla_lov if celler_per_lov[x].size > 0]
                z_arr = (
                    np.asarray(np.concatenate(z_delar), dtype=np.float64)
                    if z_delar
                    else np.empty(0, dtype=np.float64)
                )
                avrinning_m2 = 0.0

            overfall_z = overfall_z_karta.get(lid)
            if overfall_z is None:
                # Skydd: instängda områden vid ändringen går till full omräkning ovan.
                raise LappFel(f"Lokal nod {lid} saknar utloppssadel mot randen.")

            vol_kurva, yt_kurva, botten_z, cell_antal = berakna_kurvor_for_celler(
                z_arr, overfall_z, cell_area_m2
            )
            ox, oy = overfall_xy_karta[lid]
            global_id = lid + id_offset
            forald_lokal = forald_karta[lid]
            global_forald = (forald_lokal + id_offset) if forald_lokal is not None else None
            global_barn = tuple(b + id_offset for b in barn)

            nya_noder[global_id] = SankNod(
                nod_id=global_id,
                forald_id=global_forald,
                barn_id=global_barn,
                overfall_rh2000=overfall_z,
                overfall_x=ox,
                overfall_y=oy,
                ut_till_id=ut_till_karta[lid],
                volymkurva=vol_kurva,
                ytkurva=yt_kurva,
                avrinningsyta_m2=avrinning_m2,
                persistens_m=max(0.0, overfall_z - botten_z),
                osakerhet_m=osakerhet_karta[lid],
                botten_rh2000=botten_z,
                cell_antal=cell_antal,
            )
            ny_ut_lov_id[global_id] = ut_lov_karta[lid]
            if lid in lokal_overfall_celler:
                ny_overfall_celler[global_id] = lokal_overfall_celler[lid]

    # Bevara flödesvägar för oförändrade noder + lägg till nya för det omräknade delområdet
    bevarade_vagar = [
        vag for vag in bas_graf.flodesvagar if vag.nod_id in bevarade_noder
    ]
    nya_lokala_vagar = _bygg_lokala_flodesvagar(
        z_flat,
        slag_flat,
        flow_to_lokal,
        ny_ct_flat,
        omrakna_indices,
        geo,
        start_vag_id=len(bevarade_vagar),
        referensdjup_m=referensdjup_m,
    )
    alla_vagar: list[FlodesVag] = []
    for idx, vag in enumerate(bevarade_vagar + nya_lokala_vagar):
        alla_vagar.append(
            FlodesVag(
                id=idx,
                nod_id=vag.nod_id,
                koordinater=vag.koordinater,
                langd_m=vag.langd_m,
                lutning=vag.lutning,
                gangtid_s=vag.gangtid_s,
                referensdjup_m=vag.referensdjup_m,
            )
        )

    ny_graf = SankGraf(
        noder=nya_noder,
        cell_till_nod=np.asarray(ny_ct_flat.reshape((hojd, bredd)), dtype=np.int64),
        flodesvagar=alla_vagar,
        geo=geo,
        filtrerad=False,
        persistens_faktor=None,
        mark_z=eff_z,
        markslag=eff_slag,
        ut_lov_id=ny_ut_lov_id,
        overfall_celler=ny_overfall_celler,
    )

    return LokalOmrakningResultat(
        ny_graf=ny_graf,
        berorda_bas_noder=berorda_noder,
        antal_ateranvanda_noder=len(bevarade_noder),
        antal_omraknade_celler=int(omrakna_indices.size),
        kortid_s=time.perf_counter() - t_wall_start,
    )
