"""Före/efter-simulering, entréanalys, fördröjningsvolym och alternativjämförelse (T04)."""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from dynamik.handelsestyrd import kor_dynamik_handelsestyrd
from dynamik.nodmodell import (
    DynamikResultat,
    EntrePunkt,
    applicera_initial_forlust,
    skapa_regn_intervall,
    utvardera_entreer,
)
from gemensamt.manifest import FilPost, RunManifest, skapa_manifest, skriv_manifest
from lapp.kontrakt import Gatusektion, LappFel, ProjektLapp
from lapp.lokal_omrakning import LokalOmrakningResultat, uppdatera_sankgraf_lokalt
from lapp.tillampa import LappTillampning, tillampa_lapp
from sankgraf.hierarki import bygg_sankgraf
from sankgraf.typer import MARKSLAG_BYGGNAD, SankGraf
from testsvit.k1_io import BrytlinjePost, OppningPost

_STANDARD_DJUP_TOL_M = 1e-4


@dataclass(frozen=True)
class EntreForandring:
    """Förändring i maxdjup och tid till gränsdjup för en grannentré."""

    entre_id: str
    gransdjup_m: float
    fore_max_djup_m: float
    efter_max_djup_m: float
    diff_max_djup_m: float
    fore_tid_till_gransdjup_s: float | None
    efter_tid_till_gransdjup_s: float | None
    ny_drabbad: bool
    forsamrad: bool


@dataclass(frozen=True)
class ProjektEntreUtvardering:
    """Utvärdering av en ny byggnads entré i projektlappen mot `entre_rh2000`."""

    byggnad_id: str
    entre_rh2000: float
    max_vattenniva_rh2000: float
    marginal_m: float
    overstiger_entre: bool


@dataclass(frozen=True)
class ForeEfterResultat:
    """Samlat resultat från en före/efter-körning för en projektlapp."""

    lapp_id: str
    tillampning: LappTillampning
    efter_graf: SankGraf
    lokal_omrakning: LokalOmrakningResultat | None
    fore: DynamikResultat
    efter: DynamikResultat
    diff_max_djup_m: NDArray[np.float32]
    diff_tid_till_0_1m_s: NDArray[np.float32]
    grann_entre_forandringar: tuple[EntreForandring, ...]
    nya_drabbade_entreer: tuple[EntreForandring, ...]
    forsamrade_entreer: tuple[EntreForandring, ...]
    projekt_entreer: tuple[ProjektEntreUtvardering, ...]
    max_okning_djup_granne_m: float
    grann_forsamring_volym_m3: float
    fordrojningsvolym_m3: float
    kortid_s: float
    manifest: RunManifest


@dataclass(frozen=True)
class AlternativSammanfattning:
    """Kompakt sammanfattning för sid-vid-sida-jämförelse av flera projektalternativ."""

    lapp_id: str
    beskrivning: str
    max_okning_djup_granne_m: float
    grann_forsamring_volym_m3: float
    fordrojningsvolym_m3: float
    antal_nya_drabbade_entreer: int
    antal_forsamrade_entreer: int
    antal_ateranvanda_noder: int
    antal_omraknade_celler: int
    kortid_s: float
    resultat: ForeEfterResultat


def _utvardera_projekt_entreer(
    tillampning: LappTillampning,
    efter_graf: SankGraf,
    efter: DynamikResultat,
) -> tuple[ProjektEntreUtvardering, ...]:
    geo = efter_graf.geo
    ut: list[ProjektEntreUtvardering] = []
    for entre in tillampning.registrerade_entreer:
        e_m, n_m = entre.entre_punkt
        kol = math.floor((e_m - geo.x_min) / geo.pixelstorlek_x)
        rad = math.floor((geo.y_max - n_m) / geo.pixelstorlek_y)
        if not (0 <= rad < geo.hojd and 0 <= kol < geo.bredd):
            raise LappFel(
                f"Byggnadsentré '{entre.byggnad_id}' ({e_m}, {n_m}) ligger utanför rutnätet."
            )

        # Om entrépunkten hamnar på en byggnadscell kontrolleras närmaste aktiva grannceller
        kandidater: list[tuple[int, int]] = [(rad, kol)]
        if int(efter_graf.markslag[rad, kol]) == MARKSLAG_BYGGNAD:
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nr, nc = rad + dr, kol + dc
                if (
                    0 <= nr < geo.hojd
                    and 0 <= nc < geo.bredd
                    and int(efter_graf.markslag[nr, nc]) != MARKSLAG_BYGGNAD
                ):
                    kandidater.append((nr, nc))

        max_wse = -math.inf
        for r, c in kandidater:
            if int(efter_graf.markslag[r, c]) == MARKSLAG_BYGGNAD:
                continue
            wse = float(efter_graf.mark_z[r, c]) + float(efter.max_djup_m[r, c])
            if wse > max_wse:
                max_wse = wse
        if not math.isfinite(max_wse):
            max_wse = float(entre.entre_rh2000)

        marginal = float(entre.entre_rh2000) - max_wse
        ut.append(
            ProjektEntreUtvardering(
                byggnad_id=entre.byggnad_id,
                entre_rh2000=float(entre.entre_rh2000),
                max_vattenniva_rh2000=max_wse,
                marginal_m=marginal,
                overstiger_entre=bool(max_wse > float(entre.entre_rh2000) + _STANDARD_DJUP_TOL_M),
            )
        )
    return tuple(ut)


def _rot_for_nod(graf: SankGraf, nid: int) -> int:
    curr = nid
    while True:
        forald = graf.noder[curr].forald_id
        if forald is None or forald not in graf.noder:
            return curr
        curr = forald


def _berakna_fordrojningsvolym(
    efter_graf: SankGraf,
    efter: DynamikResultat,
    forsamrad_mask: NDArray[np.bool_],
    grann_forsamring_volym_m3: float,
    netto_regn_m: float,
    avrinning_faktor: float,
    brunnar_avdrag_m3_s: float,
) -> float:
    """Beräknar erforderlig fördröjningsvolym (m³) inom lappen för nollnedgång hos grannar.

    Inkluderar både den lagrade volymökningen hos drabbade grannar (`grann_forsamring_volym_m3`)
    och eventuellt extra överfall som passerat ut ur en överfull grannsänka.
    """
    if grann_forsamring_volym_m3 <= 0.0 or not np.any(forsamrad_mask) or not efter_graf.noder:
        return 0.0

    raw_lov = np.unique(efter_graf.cell_till_nod[forsamrad_mask])
    forsamrade_lov = [int(nid) for nid in raw_lov if int(nid) in efter_graf.noder]
    if not forsamrade_lov:
        return grann_forsamring_volym_m3

    w_rotter: set[int] = {_rot_for_nod(efter_graf, lid) for lid in forsamrade_lov}

    # Kontrollera om någon av de försämrade grannrötterna faktiskt svämmade över
    nagon_svammade_over = any(
        efter.noder[rid].t_overfall_s is not None for rid in w_rotter if rid in efter.noder
    )
    if not nagon_svammade_over or brunnar_avdrag_m3_s > 0.0:
        return grann_forsamring_volym_m3

    # Bygg bakåtgående överfallsgraf mellan rötter för att samla uppströms slutna delnätet U
    uppstroms_rotter: dict[int, set[int]] = {
        nid: set() for nid, nod in efter_graf.noder.items() if nod.forald_id is None
    }
    for rot_id in uppstroms_rotter:
        mal = efter_graf.ut_lov_id.get(rot_id)
        if mal is None:
            mal = efter_graf.noder[rot_id].ut_till_id
        if mal is not None and mal in efter_graf.noder:
            mal_rot = _rot_for_nod(efter_graf, mal)
            if mal_rot in uppstroms_rotter and mal_rot != rot_id:
                uppstroms_rotter[mal_rot].add(rot_id)

    u_rotter: set[int] = set(w_rotter)
    ko: deque[int] = deque(w_rotter)
    while ko:
        curr = ko.popleft()
        for src in uppstroms_rotter.get(curr, set()):
            if src not in u_rotter:
                u_rotter.add(src)
                ko.append(src)

    # Massbalans över det uppströms slutna delnätet U:
    # allt regn i U som inte ligger lagrat i U vid regnslut har svämmat över ut ur W.
    eff_regn_m = float(netto_regn_m) * float(avrinning_faktor)
    regn_vol_u = 0.0
    lagrat_u = 0.0
    for rid in u_rotter:
        regn_vol_u += efter_graf.total_avrinningsyta_m2(rid) * eff_regn_m
        if rid in efter.noder and efter.noder[rid].volym_m3:
            lagrat_u += float(efter.noder[rid].volym_m3[-1])

    extra_overfall_m3 = max(0.0, regn_vol_u - lagrat_u)
    return grann_forsamring_volym_m3 + extra_overfall_m3


def kor_fore_efter(
    bas_graf: SankGraf,
    bas_mark_z: NDArray[np.float32],
    bas_osakerhet_z: NDArray[np.float32],
    bas_markslag: NDArray[np.uint8],
    lapp: ProjektLapp,
    regn_tid_s: Sequence[float],
    regn_intensitet_mm_h: Sequence[float],
    *,
    bas_recept_sha256: str,
    grann_entreer: Sequence[EntrePunkt] = (),
    bas_brytlinjer: Sequence[BrytlinjePost] = (),
    bas_oppningar: Sequence[OppningPost] = (),
    sektioner: Mapping[str, Gatusektion] | None = None,
    lokal: bool = True,
    slut_tid_s: float | None = None,
    avrinning_faktor: float = 1.0,
    initial_forlust_mm: float = 0.0,
    brunnar_avdrag_m3_s: float = 0.0,
    djup_tolerans_m: float = _STANDARD_DJUP_TOL_M,
    fore_resultat: DynamikResultat | None = None,
    ut_mapp: str | Path | None = None,
    kodversion: str | None = None,
    start: str | None = None,
) -> ForeEfterResultat:
    """Kör dynamisk fyllning (T03) före och efter en projektlapp och beräknar differenser."""
    t_wall_start = time.perf_counter()

    tillampning = tillampa_lapp(
        bas_mark_z,
        bas_osakerhet_z,
        bas_markslag,
        bas_graf.geo,
        lapp,
        bas_recept_sha256=bas_recept_sha256,
        bas_brytlinjer=bas_brytlinjer,
        bas_oppningar=bas_oppningar,
        sektioner=sektioner,
    )

    # Notera: kantstenssprång från parametriska gator ligger redan i `tillampning.mark_z`
    # som ensidigt steg (gångbana högre än rännsten), så endast `bas_brytlinjer` skickas
    # som symmetriska väggbarriärer till sänkgrafen.
    lokal_res: LokalOmrakningResultat | None
    if lokal:
        lokal_res = uppdatera_sankgraf_lokalt(
            bas_graf,
            tillampning.mark_z,
            tillampning.osakerhet_z,
            tillampning.markslag,
            tillampning.andrad_mask,
            brytlinjer=tillampning.bas_brytlinjer,
            oppningar=tillampning.oppningar,
        )
        efter_graf = lokal_res.ny_graf
    else:
        lokal_res = None
        efter_graf = bygg_sankgraf(
            tillampning.mark_z,
            tillampning.osakerhet_z,
            tillampning.markslag,
            bas_graf.geo,
            brytlinjer=tillampning.bas_brytlinjer,
            oppningar=tillampning.oppningar,
        )

    fore = (
        fore_resultat
        if fore_resultat is not None
        else kor_dynamik_handelsestyrd(
            bas_graf,
            regn_tid_s,
            regn_intensitet_mm_h,
            slut_tid_s=slut_tid_s,
            avrinning_faktor=avrinning_faktor,
            initial_forlust_mm=initial_forlust_mm,
            brunnar_avdrag_m3_s=brunnar_avdrag_m3_s,
        )
    )
    efter = kor_dynamik_handelsestyrd(
        efter_graf,
        regn_tid_s,
        regn_intensitet_mm_h,
        slut_tid_s=slut_tid_s,
        avrinning_faktor=avrinning_faktor,
        initial_forlust_mm=initial_forlust_mm,
        brunnar_avdrag_m3_s=brunnar_avdrag_m3_s,
    )

    diff_djup_f64 = np.asarray(efter.max_djup_m, dtype=np.float64) - np.asarray(
        fore.max_djup_m, dtype=np.float64
    )
    diff_max_djup = np.asarray(diff_djup_f64, dtype=np.float32)
    diff_tid_01 = np.asarray(
        np.asarray(efter.tid_till_0_1m_s, dtype=np.float64)
        - np.asarray(fore.tid_till_0_1m_s, dtype=np.float64),
        dtype=np.float32,
    )

    # Utvärdera grannentréer före och efter
    fore_entreer = utvardera_entreer(fore, bas_graf, grann_entreer) if grann_entreer else []
    efter_entreer = utvardera_entreer(efter, efter_graf, grann_entreer) if grann_entreer else []
    grann_forandringar: list[EntreForandring] = []
    for ep, f_res, e_res in zip(grann_entreer, fore_entreer, efter_entreer, strict=True):
        diff_e = float(e_res.max_djup_m) - float(f_res.max_djup_m)
        ny_drabbad = bool(
            f_res.max_djup_m < ep.gransdjup_m - djup_tolerans_m
            and e_res.max_djup_m >= ep.gransdjup_m - djup_tolerans_m
        )
        forsamrad = bool(diff_e > djup_tolerans_m)
        grann_forandringar.append(
            EntreForandring(
                entre_id=ep.entre_id,
                gransdjup_m=float(ep.gransdjup_m),
                fore_max_djup_m=float(f_res.max_djup_m),
                efter_max_djup_m=float(e_res.max_djup_m),
                diff_max_djup_m=diff_e,
                fore_tid_till_gransdjup_s=f_res.tid_till_gransdjup_s,
                efter_tid_till_gransdjup_s=e_res.tid_till_gransdjup_s,
                ny_drabbad=ny_drabbad,
                forsamrad=forsamrad,
            )
        )

    nya_drabbade = tuple(e for e in grann_forandringar if e.ny_drabbad)
    forsamrade = tuple(e for e in grann_forandringar if e.forsamrad)
    projekt_entreer = _utvardera_projekt_entreer(tillampning, efter_graf, efter)

    # Grannpåverkan utanför projektlappen
    aktiv_fore = (bas_graf.markslag != MARKSLAG_BYGGNAD) & np.isfinite(bas_graf.mark_z)
    aktiv_efter = (efter_graf.markslag != MARKSLAG_BYGGNAD) & np.isfinite(efter_graf.mark_z)
    grann_mask = np.asarray(
        ~tillampning.projekt_mask & ~tillampning.andrad_mask & aktiv_fore & aktiv_efter,
        dtype=np.bool_,
    )
    forsamrad_mask = np.asarray(grann_mask & (diff_djup_f64 > djup_tolerans_m), dtype=np.bool_)

    cell_area_m2 = float(bas_graf.geo.pixelstorlek_x) * float(bas_graf.geo.pixelstorlek_y)
    if np.any(forsamrad_mask):
        max_okning_granne = float(np.max(diff_djup_f64[forsamrad_mask]))
        grann_forsamring_vol = float(np.sum(diff_djup_f64[forsamrad_mask]) * cell_area_m2)
    else:
        max_okning_granne = 0.0
        grann_forsamring_vol = 0.0

    raw_iv, _ = skapa_regn_intervall(regn_tid_s, regn_intensitet_mm_h, slut_tid_s=slut_tid_s)
    _, netto_regn_m = applicera_initial_forlust(raw_iv, initial_forlust_mm)
    fordrojningsvolym = _berakna_fordrojningsvolym(
        efter_graf,
        efter,
        forsamrad_mask,
        grann_forsamring_vol,
        netto_regn_m,
        avrinning_faktor,
        brunnar_avdrag_m3_s,
    )

    kortid_s = time.perf_counter() - t_wall_start
    ateranvanda = lokal_res.antal_ateranvanda_noder if lokal_res is not None else 0
    omraknade_celler = (
        lokal_res.antal_omraknade_celler
        if lokal_res is not None
        else bas_graf.geo.hojd * bas_graf.geo.bredd
    )

    manifest = skapa_manifest(
        modul="lapp",
        indata=[
            FilPost(roll="bas_recept", fil="recept.json", sha256=bas_recept_sha256),
            FilPost(roll="projektlapp", fil=f"{lapp.lapp_id}.json", sha256=lapp.lapp_sha256()),
        ],
        parametrar={
            "lapp_id": lapp.lapp_id,
            "nytt_recept_sha256": tillampning.nytt_recept_sha256,
            "lokal_omrakning": lokal,
            "avrinning_faktor": float(avrinning_faktor),
            "initial_forlust_mm": float(initial_forlust_mm),
            "brunnar_avdrag_m3_s": float(brunnar_avdrag_m3_s),
            "netto_regn_m": float(netto_regn_m),
            "djup_tolerans_m": float(djup_tolerans_m),
        },
        utdata=[],
        matvarden={
            "kortid_s": float(kortid_s),
            "max_okning_djup_granne_m": float(max_okning_granne),
            "grann_forsamring_volym_m3": float(grann_forsamring_vol),
            "fordrojningsvolym_m3": float(fordrojningsvolym),
            "antal_nya_drabbade_entreer": len(nya_drabbade),
            "antal_forsamrade_entreer": len(forsamrade),
            "antal_ateranvanda_noder": int(ateranvanda),
            "antal_omraknade_celler": int(omraknade_celler),
            "fore_volymfel_rel": float(fore.volymfel_rel),
            "efter_volymfel_rel": float(efter.volymfel_rel),
        },
        kodversion=kodversion,
        start=start,
    )

    if ut_mapp is not None:
        skriv_manifest(manifest, Path(ut_mapp) / "run.json")

    return ForeEfterResultat(
        lapp_id=lapp.lapp_id,
        tillampning=tillampning,
        efter_graf=efter_graf,
        lokal_omrakning=lokal_res,
        fore=fore,
        efter=efter,
        diff_max_djup_m=diff_max_djup,
        diff_tid_till_0_1m_s=diff_tid_01,
        grann_entre_forandringar=tuple(grann_forandringar),
        nya_drabbade_entreer=nya_drabbade,
        forsamrade_entreer=forsamrade,
        projekt_entreer=projekt_entreer,
        max_okning_djup_granne_m=max_okning_granne,
        grann_forsamring_volym_m3=grann_forsamring_vol,
        fordrojningsvolym_m3=fordrojningsvolym,
        kortid_s=kortid_s,
        manifest=manifest,
    )


def jamfor_alternativ(
    bas_graf: SankGraf,
    bas_mark_z: NDArray[np.float32],
    bas_osakerhet_z: NDArray[np.float32],
    bas_markslag: NDArray[np.uint8],
    alternativ: Sequence[ProjektLapp],
    regn_tid_s: Sequence[float],
    regn_intensitet_mm_h: Sequence[float],
    *,
    bas_recept_sha256: str,
    grann_entreer: Sequence[EntrePunkt] = (),
    bas_brytlinjer: Sequence[BrytlinjePost] = (),
    bas_oppningar: Sequence[OppningPost] = (),
    sektioner: Mapping[str, Gatusektion] | None = None,
    lokal: bool = True,
    slut_tid_s: float | None = None,
    avrinning_faktor: float = 1.0,
    initial_forlust_mm: float = 0.0,
    brunnar_avdrag_m3_s: float = 0.0,
    djup_tolerans_m: float = _STANDARD_DJUP_TOL_M,
) -> list[AlternativSammanfattning]:
    """Kör flera projektlappar sida vid sida mot samma basfall och regnscenario."""
    if not alternativ:
        raise LappFel("jamfor_alternativ kräver minst ett projektalternativ.")

    # Basfallet räknas exakt en gång och delas mellan alla alternativ
    fore_delat = kor_dynamik_handelsestyrd(
        bas_graf,
        regn_tid_s,
        regn_intensitet_mm_h,
        slut_tid_s=slut_tid_s,
        avrinning_faktor=avrinning_faktor,
        initial_forlust_mm=initial_forlust_mm,
        brunnar_avdrag_m3_s=brunnar_avdrag_m3_s,
    )

    sammanfattningar: list[AlternativSammanfattning] = []
    for lapp in alternativ:
        res = kor_fore_efter(
            bas_graf,
            bas_mark_z,
            bas_osakerhet_z,
            bas_markslag,
            lapp,
            regn_tid_s,
            regn_intensitet_mm_h,
            bas_recept_sha256=bas_recept_sha256,
            grann_entreer=grann_entreer,
            bas_brytlinjer=bas_brytlinjer,
            bas_oppningar=bas_oppningar,
            sektioner=sektioner,
            lokal=lokal,
            slut_tid_s=slut_tid_s,
            avrinning_faktor=avrinning_faktor,
            initial_forlust_mm=initial_forlust_mm,
            brunnar_avdrag_m3_s=brunnar_avdrag_m3_s,
            djup_tolerans_m=djup_tolerans_m,
            fore_resultat=fore_delat,
        )
        ateranvanda = (
            res.lokal_omrakning.antal_ateranvanda_noder if res.lokal_omrakning is not None else 0
        )
        omraknade = (
            res.lokal_omrakning.antal_omraknade_celler
            if res.lokal_omrakning is not None
            else bas_graf.geo.hojd * bas_graf.geo.bredd
        )
        sammanfattningar.append(
            AlternativSammanfattning(
                lapp_id=lapp.lapp_id,
                beskrivning=lapp.beskrivning,
                max_okning_djup_granne_m=res.max_okning_djup_granne_m,
                grann_forsamring_volym_m3=res.grann_forsamring_volym_m3,
                fordrojningsvolym_m3=res.fordrojningsvolym_m3,
                antal_nya_drabbade_entreer=len(res.nya_drabbade_entreer),
                antal_forsamrade_entreer=len(res.forsamrade_entreer),
                antal_ateranvanda_noder=ateranvanda,
                antal_omraknade_celler=omraknade,
                kortid_s=res.kortid_s,
                resultat=res,
            )
        )
    return sammanfattningar


def formatera_alternativ_tabell(
    sammanfattningar: Sequence[AlternativSammanfattning],
) -> str:
    """Formaterar en läsbar Markdown-tabell för sid-vid-sida-jämförelse av alternativ."""
    rader = [
        "| Alternativ | Max djupökning granne (m) | Grannförsämring (m³) | "
        "Behövlig fördröjning (m³) | Nya drabbade entréer | Återanvända noder | Körtid (s) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for s in sammanfattningar:
        rader.append(
            f"| {s.lapp_id} | {s.max_okning_djup_granne_m:.4f} | "
            f"{s.grann_forsamring_volym_m3:.2f} | {s.fordrojningsvolym_m3:.2f} | "
            f"{s.antal_nya_drabbade_entreer} | {s.antal_ateranvanda_noder} | {s.kortid_s:.3f} |"
        )
    return "\n".join(rader)
