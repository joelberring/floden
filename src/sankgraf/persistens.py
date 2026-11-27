"""Persistensfiltrering av sänkgraf under osäkerhet (T02 steg 6, A9)."""

from __future__ import annotations

import heapq
import math
from collections.abc import Mapping

import numpy as np

from sankgraf.hierarki import _bygg_nodkurvor_nedifran_upp
from sankgraf.typer import (
    HAV_NOD_ID,
    TYP_SLUTSANKA,
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


def _ar_i_deltrad(nod_id: int, rot_id: int, forald: Mapping[int, int | None]) -> bool:
    """Returnerar True om `nod_id` ligger i delträdet under `rot_id` (O(träddjup))."""
    curr: int | None = nod_id
    while curr is not None:
        if curr == rot_id:
            return True
        curr = forald[curr]
    return False


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
            takvatten=graf.takvatten,
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
    # Slutsänkor (K2/v2): typ och hinder följer rotens plats i trädet, och en slutsänka filtreras
    # aldrig bort som rotlöv (vattnet har ingen mottagare; det skulle försvinna ur modellen).
    typ: dict[int, str] = {nid: n.typ for nid, n in graf.noder.items()}
    hinder: dict[int, float | None] = {nid: n.hinder_rh2000 for nid, n in graf.noder.items()}

    # Spårar var varje ursprunglig lövnods celler hamnar efter filtrering
    ursprungliga_lov = [nid for nid, n in graf.noder.items() if n.ar_lov]
    ompekning_lov: dict[int, int] = {lid: lid for lid in ursprungliga_lov}

    # Omvända index för ut_till / ut_lov samt min-heap för Elder Rule i O(N log N)
    in_ut_till: dict[int, set[int]] = {}
    in_ut_lov: dict[int, set[int]] = {}
    for nid in aktiva:
        t_val = ut_till[nid]
        if t_val is not None:
            in_ut_till.setdefault(t_val, set()).add(nid)
        ul_val_init = ut_lov[nid]
        if ul_val_init is not None:
            in_ut_lov.setdefault(ul_val_init, set()).add(nid)

    def _satt_ut_till(k: int, nytt: int | None) -> None:
        gammalt = ut_till[k]
        if gammalt == nytt:
            return
        if gammalt is not None:
            s_set = in_ut_till.get(gammalt)
            if s_set is not None:
                s_set.discard(k)
        ut_till[k] = nytt
        if nytt is not None:
            in_ut_till.setdefault(nytt, set()).add(k)

    def _satt_ut_lov(k: int, nytt: int | None) -> None:
        gammalt = ut_lov[k]
        if gammalt == nytt:
            return
        if gammalt is not None:
            s_set = in_ut_lov.get(gammalt)
            if s_set is not None:
                s_set.discard(k)
        ut_lov[k] = nytt
        if nytt is not None:
            in_ut_lov.setdefault(nytt, set()).add(k)

    kandidater: list[tuple[float, int]] = []
    for nid in sorted(aktiva):
        if barn[nid] or typ[nid] == TYP_SLUTSANKA:
            continue
        pers_init = overfall_z[nid] - botten[nid]
        if pers_init < faktor * osakerhet[nid]:
            heapq.heappush(kandidater, (pers_init, nid))

    while kandidater:
        pers_kand, x = heapq.heappop(kandidater)
        if x not in aktiva or barn[x] or typ[x] == TYP_SLUTSANKA:
            continue
        akt_pers = overfall_z[x] - botten[x]
        if akt_pers != pers_kand or akt_pers >= faktor * osakerhet[x]:
            continue

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
                ut_x = ut_lov.get(x)
                resolved_ut = _los_ompekning(ut_x, ompekning_lov) if ut_x is not None else -1
                if (
                    resolved_ut in aktiva
                    and not barn[resolved_ut]
                    and _ar_i_deltrad(resolved_ut, s, forald)
                ):
                    mal_lov = resolved_ut
                else:
                    mal_lov = _djupaste_lov(s, barn, botten)

            ompekning_lov[x] = mal_lov

            # Systern s ersätter föräldern p i trädet (Elder Rule)
            gp = forald[p]
            forald[s] = gp
            overfall_z[s] = overfall_z[p]
            overfall_xy[s] = overfall_xy[p]
            osakerhet[s] = osakerhet[p]
            _satt_ut_till(s, ut_till[p])
            _satt_ut_lov(s, ut_lov[p])
            overfall_celler[s] = overfall_celler[p]
            typ[s] = typ[p]
            hinder[s] = hinder[p]
            botten[s] = min(botten[s], botten[x])

            if gp is not None:
                barn[gp] = [s if c == p else c for c in barn[gp]]
                for c in barn[gp]:
                    if c != s and ut_till.get(c) == p:
                        _satt_ut_till(c, s)

            for k in list(in_ut_till.get(x, ())) + list(in_ut_till.get(p, ())):
                if k in (x, p) or k not in aktiva:
                    continue
                _satt_ut_till(k, s if k != s else None)
            for k in list(in_ut_lov.get(x, ())):
                if k in (x, p) or k not in aktiva:
                    continue
                _satt_ut_lov(k, mal_lov if k != s else ut_lov[s])

            _satt_ut_till(x, None)
            _satt_ut_till(p, None)
            _satt_ut_lov(x, None)
            _satt_ut_lov(p, None)
            in_ut_till.pop(x, None)
            in_ut_till.pop(p, None)
            in_ut_lov.pop(x, None)
            in_ut_lov.pop(p, None)
            aktiva.remove(x)
            aktiva.remove(p)

            if not barn[s] and typ[s] != TYP_SLUTSANKA:
                pers_s = overfall_z[s] - botten[s]
                if pers_s < faktor * osakerhet[s]:
                    heapq.heappush(kandidater, (pers_s, s))
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
            ny_ul = None if mal_lov == HAV_NOD_ID else mal_lov
            for k in list(in_ut_till.get(x, ())):
                if k == x or k not in aktiva:
                    continue
                _satt_ut_till(k, mal_nod if mal_nod != k else None)
            for k in list(in_ut_lov.get(x, ())):
                if k == x or k not in aktiva:
                    continue
                _satt_ut_lov(k, ny_ul)

            _satt_ut_till(x, None)
            _satt_ut_lov(x, None)
            in_ut_till.pop(x, None)
            in_ut_lov.pop(x, None)
            aktiva.remove(x)

    # Slutlig lövompekning för alla ursprungliga lövnoder
    slut_karta: dict[int, int] = {
        lid: _los_ompekning(lid, ompekning_lov) for lid in ursprungliga_lov
    }

    ny_cell_till_nod = graf.cell_till_nod.copy()
    if slut_karta:
        flat_ct = ny_cell_till_nod.ravel()
        max_lid = max(slut_karta)
        lut = np.arange(max_lid + 1, dtype=np.int64)
        andrad = False
        for gammalt_id, nytt_id in slut_karta.items():
            if gammalt_id != nytt_id:
                lut[gammalt_id] = nytt_id
                andrad = True
        if andrad:
            mask_om = (flat_ct >= 0) & (flat_ct <= max_lid)
            flat_ct[mask_om] = lut[flat_ct[mask_om]]

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

    # Räkna om exakta kurvor nedifrån och upp i O(N log H) tid och O(N) minne
    z_flat = graf.mark_z.ravel()
    ct_flat = ny_cell_till_nod.ravel()
    cell_area_m2 = float(graf.geo.pixelstorlek_x) * float(graf.geo.pixelstorlek_y)

    lov_mask = ct_flat >= 0
    lov_ids_flat = ct_flat[lov_mask]
    lov_z_flat = z_flat[lov_mask]
    del lov_mask

    kurv_data = _bygg_nodkurvor_nedifran_upp(
        ordnade_noder,
        forald,
        barn,
        overfall_z,
        lov_ids_flat,
        lov_z_flat,
        cell_area_m2,
    )
    del lov_ids_flat, lov_z_flat

    nya_noder: dict[int, SankNod] = {}
    nya_ut_lov: dict[int, int | None] = {}
    nya_overfall_celler: dict[int, tuple[tuple[int, int], ...]] = {}

    for nid in ordnade_noder:
        b_tup = tuple(barn[nid])
        ov_z = overfall_z[nid]
        vol_kurva, yt_kurva, bot_z, cell_antal, avrinning_m2 = kurv_data.pop(nid)
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
            typ=typ[nid],
            hinder_rh2000=hinder[nid],
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
        takvatten=graf.takvatten,
    )
