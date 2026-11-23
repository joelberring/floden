"""Monoton intervallanalys i grafdynamiken (Steg 2).

På delträd utan topologisk bifurkation är fyllning i sänkgrafen ett monotont
dynamiskt system: mer regn / lägre initialförlust / lägre avtappning kan aldrig
minska vattennivån h_u(t) eller volymen V_u(t). Två extremkörningar ger då en
garanterad inneslutning [h_min(t), h_max(t)] utan Monte Carlo-stickprov.

När en uppströms sadel är topologiskt instabil (en `BifurkationsPunkt` från
`sankgraf.bifurkation`) flaggas berörda noder med `monoton_garanterad = False`,
och en styrd gren-analys kör de alternativa utloppsgrenarna för topp-N kritiska
bifurkationer och tar unionen av intervallen.

**Status:** Inkopplad i webb-API:t (`POST /api/analys/intervall`,
`webb.api.FlodenApiApp.kor_intervall_analys`) med projektbehörighetskontroll och testad i
`tests/dynamik/test_intervall.py` samt `tests/webb/test_analys_steg_a.py`.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np

from dynamik.handelsestyrd import kor_dynamik_handelsestyrd
from dynamik.nodmodell import (
    DynamikFel,
    DynamikResultat,
    EntrePunkt,
    utvardera_entreer,
)
from sankgraf.bifurkation import BifurkationsPunkt
from sankgraf.typer import SankGraf

STANDARD_MAX_BIFURKATIONSGRENAR = 5


@dataclass(frozen=True)
class NodIntervall:
    """Inneslutet tids- och maxdjupintervall för en sänknod."""

    nod_id: int
    min_niva_rh2000_t: tuple[float, ...]
    max_niva_rh2000_t: tuple[float, ...]
    min_volym_m3_t: tuple[float, ...]
    max_volym_m3_t: tuple[float, ...]
    min_maxdjup_m: float
    max_maxdjup_m: float
    monoton_garanterad: bool
    bifurkationspaverkad: bool
    paverkande_bifurkationer: tuple[int, ...] = ()


@dataclass(frozen=True)
class EntreIntervall:
    """Maxdjupintervall och garanti-flagga för en kritisk entré."""

    entre_id: str
    e_m: float
    n_m: float
    gransdjup_m: float
    min_maxdjup_m: float
    max_maxdjup_m: float
    overstiger_grans_garanterat: bool
    kan_overstiga_grans: bool
    monoton_garanterad: bool


@dataclass(frozen=True)
class IntervallResultat:
    """Samlade intervallgränser över sänkgrafen med garanti-flaggor."""

    tider_s: tuple[float, ...]
    noder: dict[int, NodIntervall]
    entreer: tuple[EntreIntervall, ...]
    klimatfaktor_intervall: tuple[float, float]
    initial_forlust_mm_intervall: tuple[float, float]
    avtappning_mm_h_intervall: tuple[float, float]
    antal_korningar: int
    antal_bifurkationsgrenar: int
    max_volymfel_rel: float
    kortid_s: float


def _validera_intervall(namn: str, iv: tuple[float, float]) -> tuple[float, float]:
    lo, hi = float(iv[0]), float(iv[1])
    if not (math.isfinite(lo) and math.isfinite(hi)):
        raise DynamikFel(f"{namn} måste bestå av ändliga tal, fick {iv}.")
    if lo < 0.0 or hi < 0.0:
        raise DynamikFel(f"{namn} får inte vara negativt, fick {iv}.")
    if lo > hi:
        raise DynamikFel(f"{namn}: undre gräns ({lo}) > övre gräns ({hi}).")
    return lo, hi


def _nedstroms_noder(graf: SankGraf, start_id: int | None) -> set[int]:
    """Alla noder som nås nedströms från `start_id` via `ut_till_id` och `forald_id`."""
    if start_id is None or start_id not in graf.noder:
        return set()
    besokta: set[int] = set()
    stack = [start_id]
    while stack:
        akt = stack.pop()
        if akt in besokta or akt not in graf.noder:
            continue
        besokta.add(akt)
        nod = graf.noder[akt]
        if nod.ut_till_id is not None and nod.ut_till_id in graf.noder:
            stack.append(nod.ut_till_id)
        if nod.forald_id is not None and nod.forald_id in graf.noder:
            stack.append(nod.forald_id)
        for barn in nod.barn_id:
            if barn in graf.noder:
                stack.append(barn)
    return besokta


def _kartlagg_bifurkationspaverkan(
    graf: SankGraf,
    bifurkationer: Sequence[BifurkationsPunkt],
) -> dict[int, list[int]]:
    """För varje nod: vilka instabila bifurkationer som kan ändra dess inflöde/utlopp."""
    paverkan: dict[int, list[int]] = {nid: [] for nid in graf.noder}
    for b in bifurkationer:
        if not b.instabil or b.nod_id not in graf.noder:
            continue
        berorda: set[int] = {b.nod_id}
        berorda.update(_nedstroms_noder(graf, b.primar_ut_till_id))
        berorda.update(_nedstroms_noder(graf, b.alternativ_ut_till_id))
        for nid in sorted(berorda):
            if nid in paverkan and b.nod_id not in paverkan[nid]:
                paverkan[nid].append(b.nod_id)
    return paverkan


def _skapar_cykel(graf: SankGraf, nod_id: int, nytt_ut_id: int | None) -> bool:
    """Kontrollerar om ändring av `nod_id.ut_till_id = nytt_ut_id` skapar en riktad cykel."""
    if nytt_ut_id is None or nytt_ut_id not in graf.noder:
        return False
    akt: int | None = nytt_ut_id
    besokta: set[int] = set()
    while akt is not None and akt in graf.noder:
        if akt == nod_id:
            return True
        if akt in besokta:
            return True
        besokta.add(akt)
        akt = graf.noder[akt].ut_till_id
    return False


def _graf_med_alternativ_gren(graf: SankGraf, bif: BifurkationsPunkt) -> SankGraf | None:
    """Skapar en lättviktskopia av `graf` där `bif.nod_id` spiller mot sitt alternativa utlopp."""
    if bif.nod_id not in graf.noder:
        return None
    if _skapar_cykel(graf, bif.nod_id, bif.alternativ_ut_till_id):
        return None
    nya_noder = dict(graf.noder)
    gammal = nya_noder[bif.nod_id]
    nya_noder[bif.nod_id] = replace(gammal, ut_till_id=bif.alternativ_ut_till_id)
    nya_ut_lov = dict(graf.ut_lov_id)
    nya_ut_lov[bif.nod_id] = bif.alternativ_ut_till_id
    return replace(graf, noder=nya_noder, ut_lov_id=nya_ut_lov)


def kor_monoton_intervallanalys(
    graf: SankGraf,
    regn_tid_s: Sequence[float],
    regn_intensitet_mm_h: Sequence[float],
    *,
    klimatfaktor_intervall: tuple[float, float] = (1.0, 1.0),
    avrinning_faktor_intervall: tuple[float, float] = (1.0, 1.0),
    initial_forlust_mm_intervall: tuple[float, float] = (0.0, 0.0),
    avtappning_mm_h_intervall: tuple[float, float] = (0.0, 0.0),
    bifurkationer: Sequence[BifurkationsPunkt] | None = None,
    max_bifurkationsgrenar: int = STANDARD_MAX_BIFURKATIONSGRENAR,
    entreer: Sequence[EntrePunkt] | None = None,
    slut_tid_s: float | None = None,
    utdata_tider_s: Sequence[float] | None = None,
) -> IntervallResultat:
    """Beräknar deterministiska nedre och övre vattenståndsintervall i grafdynamiken.

    Kör två extremkörningar genom `kor_dynamik_handelsestyrd`:
    - Undre extrem: min klimat-/avrinningsfaktor, max initialförlust och avtappning.
    - Övre extrem: max klimat-/avrinningsfaktor, min initialförlust och avtappning.

    För noder som påverkas av en instabil `BifurkationsPunkt` sätts
    `monoton_garanterad = False`, och upp till `max_bifurkationsgrenar` alternativa
    utloppsgrenar körs så att unionen av intervallen täcker båda utloppsfallen.
    """
    t0 = time.perf_counter()
    c_lo, c_hi = _validera_intervall("klimatfaktor_intervall", klimatfaktor_intervall)
    a_lo, a_hi = _validera_intervall("avrinning_faktor_intervall", avrinning_faktor_intervall)
    f_lo, f_hi = _validera_intervall("initial_forlust_mm_intervall", initial_forlust_mm_intervall)
    d_lo, d_hi = _validera_intervall("avtappning_mm_h_intervall", avtappning_mm_h_intervall)
    if max_bifurkationsgrenar < 0:
        raise DynamikFel(f"max_bifurkationsgrenar måste vara >= 0, fick {max_bifurkationsgrenar}.")

    bas_intens = [float(x) for x in regn_intensitet_mm_h]
    intens_lo = [x * c_lo for x in bas_intens]
    intens_hi = [x * c_hi for x in bas_intens]

    def kor_extrem(g: SankGraf, tider: Sequence[float] | None) -> tuple[
        DynamikResultat, DynamikResultat
    ]:
        res_lo = kor_dynamik_handelsestyrd(
            g,
            regn_tid_s,
            intens_lo,
            slut_tid_s=slut_tid_s,
            utdata_tider_s=tider,
            avrinning_faktor=a_lo,
            initial_forlust_mm=f_hi,
            avtappning_mm_h=d_hi if d_hi > 0.0 else None,
        )
        # Använd exakt samma utdatatider i övre extremen så tidsserierna är punktvis jämförbara
        gemensamma_tider = res_lo.tider_s if tider is None else tider
        res_hi = kor_dynamik_handelsestyrd(
            g,
            regn_tid_s,
            intens_hi,
            slut_tid_s=slut_tid_s,
            utdata_tider_s=gemensamma_tider,
            avrinning_faktor=a_hi,
            initial_forlust_mm=f_lo,
            avtappning_mm_h=d_lo if d_lo > 0.0 else None,
        )
        return res_lo, res_hi

    prim_lo, prim_hi = kor_extrem(graf, utdata_tider_s)
    tider_ut = tuple(float(t) for t in prim_lo.tider_s)
    alla_korningar: list[DynamikResultat] = [prim_lo, prim_hi]

    instabila = [b for b in (bifurkationer or ()) if b.instabil]
    paverkan = _kartlagg_bifurkationspaverkan(graf, instabila)

    antal_grenar = 0
    for bif in instabila[:max_bifurkationsgrenar]:
        alt_graf = _graf_med_alternativ_gren(graf, bif)
        if alt_graf is None:
            continue
        alt_lo, alt_hi = kor_extrem(alt_graf, tider_ut)
        alla_korningar.extend((alt_lo, alt_hi))
        antal_grenar += 1

    # Punktvis union över alla körningar
    noder_ut: dict[int, NodIntervall] = {}
    for nid in sorted(graf.noder):
        botten = float(graf.noder[nid].botten_rh2000)
        niva_mat = np.array(
            [r.noder[nid].niva_rh2000 for r in alla_korningar], dtype=np.float64
        )
        vol_mat = np.array(
            [r.noder[nid].volym_m3 for r in alla_korningar], dtype=np.float64
        )
        min_niva_t = tuple(float(x) for x in np.min(niva_mat, axis=0))
        max_niva_t = tuple(float(x) for x in np.max(niva_mat, axis=0))
        min_vol_t = tuple(float(x) for x in np.min(vol_mat, axis=0))
        max_vol_t = tuple(float(x) for x in np.max(vol_mat, axis=0))

        # Maxdjup per körning: max(0, max_t h_u(t) - botten)
        max_djup_per_korning = [
            max(0.0, float(np.max(r.noder[nid].niva_rh2000)) - botten)
            for r in alla_korningar
        ]
        paverkande = tuple(paverkan.get(nid, ()))
        bif_paverkad = len(paverkande) > 0
        noder_ut[nid] = NodIntervall(
            nod_id=nid,
            min_niva_rh2000_t=min_niva_t,
            max_niva_rh2000_t=max_niva_t,
            min_volym_m3_t=min_vol_t,
            max_volym_m3_t=max_vol_t,
            min_maxdjup_m=min(max_djup_per_korning),
            max_maxdjup_m=max(max_djup_per_korning),
            monoton_garanterad=not bif_paverkad,
            bifurkationspaverkad=bif_paverkad,
            paverkande_bifurkationer=paverkande,
        )

    entreer_ut: list[EntreIntervall] = []
    if entreer:
        per_korning = [utvardera_entreer(r, graf, entreer) for r in alla_korningar]
        for idx, e in enumerate(entreer):
            djup_vals = [float(pk[idx].max_djup_m) for pk in per_korning]
            kol = int(math.floor((e.e_m - graf.geo.x_min) / graf.geo.pixelstorlek_x))
            rad = int(math.floor((graf.geo.y_max - e.n_m) / graf.geo.pixelstorlek_y))
            monoton = True
            if 0 <= rad < graf.cell_till_nod.shape[0] and 0 <= kol < graf.cell_till_nod.shape[1]:
                cell_nid = int(graf.cell_till_nod[rad, kol])
                if cell_nid in noder_ut:
                    monoton = noder_ut[cell_nid].monoton_garanterad
            entreer_ut.append(
                EntreIntervall(
                    entre_id=e.entre_id,
                    e_m=float(e.e_m),
                    n_m=float(e.n_m),
                    gransdjup_m=float(e.gransdjup_m),
                    min_maxdjup_m=min(djup_vals),
                    max_maxdjup_m=max(djup_vals),
                    overstiger_grans_garanterat=min(djup_vals) >= float(e.gransdjup_m),
                    kan_overstiga_grans=max(djup_vals) >= float(e.gransdjup_m),
                    monoton_garanterad=monoton,
                )
            )

    max_volfel = max(float(r.volymfel_rel) for r in alla_korningar)
    return IntervallResultat(
        tider_s=tider_ut,
        noder=noder_ut,
        entreer=tuple(entreer_ut),
        klimatfaktor_intervall=(c_lo, c_hi),
        initial_forlust_mm_intervall=(f_lo, f_hi),
        avtappning_mm_h_intervall=(d_lo, d_hi),
        antal_korningar=len(alla_korningar),
        antal_bifurkationsgrenar=antal_grenar,
        max_volymfel_rel=max_volfel,
        kortid_s=time.perf_counter() - t0,
    )
