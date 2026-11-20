"""Persistensfiltrering av sänkgraf under osäkerhet (T02 steg 6, A9)."""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

from sankgraf.hierarki import berakna_kurvor_for_celler
from sankgraf.typer import (
    HAV_NOD_ID,
    FlodesVag,
    SankGraf,
    SankGrafFel,
    SankNod,
)


def _los_ompekning(start: int, ompekning: dict[int, int]) -> int:
    """Följer kedjade lövompekningar till slutlig kvarvarande nod eller HAV_NOD_ID."""
    curr = start
    sedda: set[int] = set()
    while curr in ompekning and ompekning[curr] != curr:
        if curr in sedda:
            return HAV_NOD_ID
        sedda.add(curr)
        curr = ompekning[curr]
    return curr


def _djupaste_lov(
    nod_id: int,
    barn: dict[int, list[int]],
    botten: dict[int, float],
) -> int:
    """Returnerar det djupaste lövet (lägst botten) i delträdet under `nod_id`."""
    curr = nod_id
    while barn[curr]:
        curr = min(barn[curr], key=lambda b: (botten[b], b))
    return curr


def _lov_i_deltrad_set(nod_id: int, barn: dict[int, list[int]]) -> set[int]:
    resultat: set[int] = set()
    stack = [nod_id]
    while stack:
        curr = stack.pop()
        if not barn[curr]:
            resultat.add(curr)
        else:
            stack.extend(barn[curr])
    return resultat


def filtrera_persistens(graf: SankGraf, faktor: float = 2.0) -> SankGraf:
    """Filtrerar bort grunda brussänkor med `persistens_m < faktor * osakerhet_m`.

    Använder Elder Rule (iterativ borttagning av den grundaste lövnoden först):
    - När en grund lövnod har en systernod i en förälder kollapsar föräldern in i
      den djupare systern, som ärver förälderns överfallsnivå och fulla lagring.
    - När en grund rotnod saknar förälder absorberas dess avrinningsyta av
      mottagande nod `ut_till_id` (eller ytterranden `HAV_NOD_ID`).

    Muterar aldrig den inkommande grafen utan returnerar en ny filtrerad `SankGraf`.
    """
    if not math.isfinite(faktor) or faktor < 0.0:
        raise SankGrafFel(f"Persistensfaktorn måste vara ändlig och >= 0, fick {faktor}.")

    if not graf.noder:
        return SankGraf(
            noder={},
            cell_till_nod=graf.cell_till_nod.copy(),
            flodesvagar=list(graf.flodesvagar),
            geo=graf.geo,
            filtrerad=True,
            persistens_faktor=float(faktor),
            mark_z=graf.mark_z.copy(),
            markslag=graf.markslag.copy(),
            ut_lov_id={},
            overfall_celler={},
        )

    # Kopiera grafens topologi till muterbara arbetsstrukturer
    aktiva: set[int] = set(graf.noder.keys())
    forald: dict[int, int | None] = {nid: n.forald_id for nid, n in graf.noder.items()}
    barn: dict[int, list[int]] = {nid: list(n.barn_id) for nid, n in graf.noder.items()}
    overfall_z: dict[int, float] = {nid: n.overfall_rh2000 for nid, n in graf.noder.items()}
    overfall_xy: dict[int, tuple[float, float]] = {
        nid: (n.overfall_x, n.overfall_y) for nid, n in graf.noder.items()
    }
    osakerhet: dict[int, float] = {nid: n.osakerhet_m for nid, n in graf.noder.items()}
    ut_till: dict[int, int | None] = {nid: n.ut_till_id for nid, n in graf.noder.items()}
    ut_lov: dict[int, int | None] = {
        nid: graf.ut_lov_id.get(nid, n.ut_till_id) for nid, n in graf.noder.items()
    }
    overfall_celler: dict[int, tuple[tuple[int, int], ...]] = {
        nid: graf.overfall_celler.get(nid, ()) for nid, n in graf.noder.items()
    }
    botten: dict[int, float] = {nid: n.botten_rh2000 for nid, n in graf.noder.items()}

    # Spårar var varje ursprunglig lövnods celler hamnar efter filtrering
    ursprungliga_lov = [nid for nid, n in graf.noder.items() if n.ar_lov]
    ompekning_lov: dict[int, int] = {lid: lid for lid in ursprungliga_lov}

    while True:
        kandidat: tuple[float, int] | None = None
        for nid in sorted(aktiva):
            if barn[nid]:
                continue
            pers = overfall_z[nid] - botten[nid]
            troskel = faktor * osakerhet[nid]
            if pers < troskel and (kandidat is None or (pers, nid) < kandidat):
                kandidat = (pers, nid)

        if kandidat is None:
            break

        _, x = kandidat
        p = forald[x]

        if p is not None:
            syskon = [c for c in barn[p] if c != x]
            if len(syskon) != 1:
                raise SankGrafFel(f"Föräldern {p} förväntades ha exakt en syster till {x}.")
            s = syskon[0]

            # Bestäm mottagande löv inuti systern s för x:s celler
            if not barn[s]:
                mal_lov = s
            else:
                lov_i_s = _lov_i_deltrad_set(s, barn)
                ut_x = ut_lov.get(x)
                resolved_ut = _los_ompekning(ut_x, ompekning_lov) if ut_x is not None else -1
                mal_lov = resolved_ut if resolved_ut in lov_i_s else _djupaste_lov(s, barn, botten)

            ompekning_lov[x] = mal_lov

            # Systern s ersätter föräldern p i trädet (Elder Rule)
            gp = forald[p]
            forald[s] = gp
            overfall_z[s] = overfall_z[p]
            overfall_xy[s] = overfall_xy[p]
            osakerhet[s] = osakerhet[p]
            ut_till[s] = ut_till[p]
            ut_lov[s] = ut_lov[p]
            overfall_celler[s] = overfall_celler[p]
            botten[s] = min(botten[s], botten[x])

            if gp is not None:
                barn[gp] = [s if c == p else c for c in barn[gp]]
                for c in barn[gp]:
                    if c != s and ut_till.get(c) == p:
                        ut_till[c] = s

            for k in aktiva:
                if k in (x, p):
                    continue
                if ut_till.get(k) in (x, p):
                    ut_till[k] = s if k != s else None
                if ut_lov.get(k) == x:
                    ut_lov[k] = mal_lov if k != s else ut_lov[s]

            aktiva.remove(x)
            aktiva.remove(p)
        else:
            # Rotlöv utan förälder: dess avrinningsyta rinner vidare till ut_till[x] (eller HAV)
            mal_nod = ut_till[x]
            raw_lov = ut_lov.get(x, mal_nod)
            if mal_nod is None or raw_lov is None:
                mal_lov = HAV_NOD_ID
            else:
                resolved = _los_ompekning(raw_lov, ompekning_lov)
                if resolved in aktiva and not barn[resolved]:
                    mal_lov = resolved
                elif mal_nod in aktiva:
                    mal_lov = _djupaste_lov(mal_nod, barn, botten)
                else:
                    mal_lov = HAV_NOD_ID

            ompekning_lov[x] = mal_lov
            for k in aktiva:
                if k == x:
                    continue
                if ut_till.get(k) == x:
                    ut_till[k] = mal_nod if mal_nod != k else None
                if ut_lov.get(k) == x:
                    ut_lov[k] = None if mal_lov == HAV_NOD_ID else mal_lov

            aktiva.remove(x)

    # Slutlig lövompekning för alla ursprungliga lövnoder
    slut_karta: dict[int, int] = {
        lid: _los_ompekning(lid, ompekning_lov) for lid in ursprungliga_lov
    }

    ny_cell_till_nod = graf.cell_till_nod.copy()
    if slut_karta:
        flat_ct = ny_cell_till_nod.ravel()
        for gammalt_id, nytt_id in slut_karta.items():
            if gammalt_id != nytt_id:
                flat_ct[flat_ct == gammalt_id] = nytt_id

    # Samla cellhöjder per kvarvarande lövnod och räkna om exakta kurvor nedifrån och upp
    z_flat = np.asarray(graf.mark_z, dtype=np.float64).ravel()
    ct_flat = ny_cell_till_nod.ravel()
    cell_area_m2 = float(graf.geo.pixelstorlek_x) * float(graf.geo.pixelstorlek_y)

    lov_mask = ct_flat >= 0
    lov_ids_flat = ct_flat[lov_mask]
    lov_z_flat = z_flat[lov_mask]

    celler_per_lov: dict[int, NDArray[np.float64]] = {}
    if lov_ids_flat.size > 0:
        ordn = np.argsort(lov_ids_flat, kind="mergesort")
        s_ids = lov_ids_flat[ordn]
        s_z = lov_z_flat[ordn]
        u_ids, s_idx, cnts = np.unique(s_ids, return_index=True, return_counts=True)
        for lid_np, st_np, c_np in zip(u_ids, s_idx, cnts, strict=True):
            st = int(st_np)
            celler_per_lov[int(lid_np)] = s_z[st : st + int(c_np)]

    # Topologisk ordning nedifrån och upp (barn före förälder)
    ordnade_noder: list[int] = []
    besokta: set[int] = set()

    def _postorder(nid: int) -> None:
        if nid in besokta or nid not in aktiva:
            return
        besokta.add(nid)
        for b_id in barn[nid]:
            _postorder(b_id)
        ordnade_noder.append(nid)

    for nid in sorted(aktiva):
        _postorder(nid)

    nya_noder: dict[int, SankNod] = {}
    lov_under: dict[int, list[int]] = {}
    nya_ut_lov: dict[int, int | None] = {}
    nya_overfall_celler: dict[int, tuple[tuple[int, int], ...]] = {}

    for nid in ordnade_noder:
        b_tup = tuple(barn[nid])
        if not b_tup:
            lov_under[nid] = [nid]
            z_arr = celler_per_lov.get(nid, np.empty(0, dtype=np.float64))
            avrinning_m2 = float(z_arr.size) * cell_area_m2
        else:
            alla_lov: list[int] = []
            for b_id in b_tup:
                alla_lov.extend(lov_under[b_id])
            lov_under[nid] = alla_lov
            delar = [celler_per_lov[lid] for lid in alla_lov if lid in celler_per_lov]
            z_arr = (
                np.asarray(np.concatenate(delar), dtype=np.float64)
                if delar
                else np.empty(0, dtype=np.float64)
            )
            avrinning_m2 = 0.0

        ov_z = overfall_z[nid]
        vol_kurva, yt_kurva, bot_z, cell_antal = berakna_kurvor_for_celler(
            z_arr, ov_z, cell_area_m2
        )
        ox, oy = overfall_xy[nid]

        ut_t = ut_till[nid]
        if ut_t is not None and ut_t not in aktiva:
            ut_t = None

        raw_ul = ut_lov.get(nid)
        ul_val: int | None
        if raw_ul is None or raw_ul < 0:
            ul_val = None
        else:
            res_ul = _los_ompekning(raw_ul, ompekning_lov)
            ul_val = res_ul if res_ul in aktiva else None

        nya_ut_lov[nid] = ul_val
        nya_overfall_celler[nid] = overfall_celler.get(nid, ())
        nya_noder[nid] = SankNod(
            nod_id=nid,
            forald_id=forald[nid],
            barn_id=b_tup,
            overfall_rh2000=ov_z,
            overfall_x=ox,
            overfall_y=oy,
            ut_till_id=ut_t,
            volymkurva=vol_kurva,
            ytkurva=yt_kurva,
            avrinningsyta_m2=avrinning_m2,
            persistens_m=max(0.0, ov_z - bot_z),
            osakerhet_m=osakerhet[nid],
            botten_rh2000=bot_z,
            cell_antal=cell_antal,
        )

    # Uppdatera flödesvägarnas mottagande nod_id utan att mutera ursprungliga objekt
    nya_flodesvagar = [
        FlodesVag(
            id=vag.id,
            nod_id=slut_karta.get(vag.nod_id, vag.nod_id),
            koordinater=vag.koordinater,
            langd_m=vag.langd_m,
            lutning=vag.lutning,
            gangtid_s=vag.gangtid_s,
            referensdjup_m=vag.referensdjup_m,
        )
        for vag in graf.flodesvagar
    ]

    return SankGraf(
        noder=nya_noder,
        cell_till_nod=ny_cell_till_nod,
        flodesvagar=nya_flodesvagar,
        geo=graf.geo,
        filtrerad=True,
        persistens_faktor=float(faktor),
        mark_z=graf.mark_z.copy(),
        markslag=graf.markslag.copy(),
        ut_lov_id=nya_ut_lov,
        overfall_celler=nya_overfall_celler,
    )
