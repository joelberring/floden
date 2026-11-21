"""Deterministisk kumulativ översvämningsrisk över återkomsttid och tidshorisont."""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from dynamik.handelsestyrd import (
    GrafStrukturCache,
    _kor_handelsestyrd_karna,
    bygg_graf_struktur_cache,
)
from dynamik.nodmodell import (
    DynamikFel,
    DynamikResultat,
    EntrePunkt,
    NodTidsserie,
    applicera_initial_forlust,
    berakna_raster_och_ankomsttider,
    skapa_regn_intervall,
)
from gemensamt.manifest import FilPost, RunManifest, skapa_manifest
from gemensamt.regn import (
    STANDARD_ATERKOMSTTIDER_AR,
    STANDARD_CDS_TOPPFAKTOR,
    STANDARD_TIDSSTEG_MIN,
    STANDARD_VARAKTIGHET_MIN,
    IdfTabell,
    RegnFel,
    RegnSerieK4,
    generera_blockregn,
    generera_cds_regn,
)
from mark_forluster.grundvatten import uppskatta_dtw_fran_terrang
from mark_forluster.koppling import berakna_effektivt_regn_k5
from mark_forluster.parametrar import ForlustFel
from markmodell.hinder_oppningar import VATTEN
from sankgraf.typer import MARKSLAG_BYGGNAD, SankGraf

__all__ = [
    "AterkomstStegResultat",
    "EntreKumulativRisk",
    "KumulativRiskResultat",
    "NodKumulativRisk",
    "berakna_kumulativ_oversvamningsrisk",
]


@dataclass(frozen=True)
class AterkomstStegResultat:
    """Resultat för en enskild återkomsttid i riskstegen."""

    aterkomsttid_ar: float
    regn: RegnSerieK4
    dynamik: DynamikResultat
    max_djup_m: float
    area_over_grans_m2: float


@dataclass(frozen=True)
class NodKumulativRisk:
    """Kumulativ översvämningsrisk för en enskild nod i sänkgrafen."""

    nod_id: int
    kritisk_aterkomsttid_ar: float
    overskriden_vid_min_t: bool
    arlig_overskridande_sannolikhet: float
    kumulativ_sannolikhet: float
    max_djup_vid_tmax_m: float


@dataclass(frozen=True)
class EntreKumulativRisk:
    """Kumulativ översvämningsrisk för en specifik entrépunkt och dess gränsdjup."""

    entre_id: str
    gransdjup_m: float
    kritisk_aterkomsttid_ar: float
    overskriden_vid_min_t: bool
    arlig_overskridande_sannolikhet: float
    kumulativ_sannolikhet: float
    max_djup_vid_tmax_m: float


def _json_tal(varde: float) -> float | None:
    """Konverterar ett flyttal till JSON-säkert tal (`None` om icke-ändligt)."""
    val = float(varde)
    return val if math.isfinite(val) else None


@dataclass(frozen=True)
class KumulativRiskResultat:
    """Samlat deterministiskt resultat för kumulativ översvämningsrisk."""

    gransdjup_m: float
    max_aterkomsttid_ar: float
    aterkomsttider_ar: tuple[float, ...]
    klimatfaktor: float
    tidshorisont_ar: float
    varaktighet_min: float
    regntyp: str
    statistikkalla: str
    kritisk_aterkomsttid_ar: NDArray[np.float32]
    overskriden_vid_min_t: NDArray[np.bool_]
    arlig_overskridande_sannolikhet: NDArray[np.float32]
    kumulativ_sannolikhet: NDArray[np.float32]
    forvantat_arligt_djup_m: NDArray[np.float32]
    forvantat_arligt_overdjup_m: NDArray[np.float32]
    max_djup_vid_tmax_m: NDArray[np.float32]
    steg: tuple[AterkomstStegResultat, ...]
    noder: dict[int, NodKumulativRisk]
    entreer: tuple[EntreKumulativRisk, ...]
    area_over_grans_m2: float
    andel_over_grans: float
    max_arlig_sannolikhet: float
    max_kumulativ_sannolikhet: float
    min_kritisk_aterkomsttid_ar: float
    max_forvantat_arligt_djup_m: float
    max_volymfel_rel: float
    kortid_s: float
    manifest: RunManifest

    def sammanfattning_dict(self) -> dict[str, Any]:
        """Returnerar ett JSON-serialiserbart lexikon med parametrar, nyckeltal och detaljer."""
        max_djup_tmax = (
            float(np.max(self.max_djup_vid_tmax_m)) if self.max_djup_vid_tmax_m.size > 0 else 0.0
        )
        return {
            "parametrar": {
                "gransdjup_m": float(self.gransdjup_m),
                "max_aterkomsttid_ar": float(self.max_aterkomsttid_ar),
                "aterkomsttider_ar": [float(t) for t in self.aterkomsttider_ar],
                "klimatfaktor": float(self.klimatfaktor),
                "tidshorisont_ar": float(self.tidshorisont_ar),
                "varaktighet_min": float(self.varaktighet_min),
                "regntyp": self.regntyp,
                "statistikkalla": self.statistikkalla,
            },
            "nyckeltal": {
                "area_over_grans_m2": float(self.area_over_grans_m2),
                "andel_over_grans": float(self.andel_over_grans),
                "max_arlig_sannolikhet": float(self.max_arlig_sannolikhet),
                "max_kumulativ_sannolikhet": float(self.max_kumulativ_sannolikhet),
                "min_kritisk_aterkomsttid_ar": _json_tal(self.min_kritisk_aterkomsttid_ar),
                "max_forvantat_arligt_djup_m": float(self.max_forvantat_arligt_djup_m),
                "max_djup_vid_tmax_m": max_djup_tmax,
                "max_volymfel_rel": float(self.max_volymfel_rel),
                "kortid_s": float(self.kortid_s),
            },
            "steg": [
                {
                    "aterkomsttid_ar": float(s.aterkomsttid_ar),
                    "regn_id": s.regn.regn_id,
                    "total_djup_mm": float(s.regn.total_djup_mm),
                    "max_intensitet_mm_h": float(s.regn.max_intensitet_mm_h),
                    "max_djup_m": float(s.max_djup_m),
                    "area_over_grans_m2": float(s.area_over_grans_m2),
                    "volymfel_rel": float(s.dynamik.volymfel_rel),
                    "kortid_s": float(s.dynamik.kortid_s),
                }
                for s in self.steg
            ],
            "noder": [
                {
                    "nod_id": int(nid),
                    "kritisk_aterkomsttid_ar": _json_tal(self.noder[nid].kritisk_aterkomsttid_ar),
                    "overskriden_vid_min_t": bool(self.noder[nid].overskriden_vid_min_t),
                    "arlig_overskridande_sannolikhet": float(
                        self.noder[nid].arlig_overskridande_sannolikhet
                    ),
                    "kumulativ_sannolikhet": float(self.noder[nid].kumulativ_sannolikhet),
                    "max_djup_vid_tmax_m": float(self.noder[nid].max_djup_vid_tmax_m),
                }
                for nid in sorted(self.noder)
            ],
            "entreer": [
                {
                    "entre_id": e.entre_id,
                    "gransdjup_m": float(e.gransdjup_m),
                    "kritisk_aterkomsttid_ar": _json_tal(e.kritisk_aterkomsttid_ar),
                    "overskriden_vid_min_t": bool(e.overskriden_vid_min_t),
                    "arlig_overskridande_sannolikhet": float(e.arlig_overskridande_sannolikhet),
                    "kumulativ_sannolikhet": float(e.kumulativ_sannolikhet),
                    "max_djup_vid_tmax_m": float(e.max_djup_vid_tmax_m),
                }
                for e in self.entreer
            ],
            "manifest": self.manifest.till_dict(),
        }


def _validera_positivt_tal(varde: float, namn: str) -> float:
    """Validerar att `varde` är ett ändligt tal strikt större än 0 (ej bool)."""
    if isinstance(varde, bool) or not isinstance(varde, int | float):
        raise DynamikFel(f"{namn} måste vara ett tal, fick {varde!r}.")
    val = float(varde)
    if not math.isfinite(val) or val <= 0.0:
        raise DynamikFel(f"{namn} måste vara ändligt och > 0, fick {val}.")
    return val


def _validera_icke_negativt_tal(varde: float, namn: str) -> float:
    """Validerar att `varde` är ett ändligt tal >= 0 (ej bool)."""
    if isinstance(varde, bool) or not isinstance(varde, int | float):
        raise DynamikFel(f"{namn} måste vara ett tal, fick {varde!r}.")
    val = float(varde)
    if not math.isfinite(val) or val < 0.0:
        raise DynamikFel(f"{namn} måste vara ändligt och >= 0, fick {val}.")
    return val


def _bygg_aterkomsttidsstege(
    max_aterkomsttid_ar: float,
    aterkomsttider_ar: Sequence[float] | None,
) -> tuple[float, ...]:
    """Konstruerar och validerar den stigande återkomsttidsstegen `<= max_aterkomsttid_ar`."""
    if aterkomsttider_ar is None:
        valda = [float(t) for t in STANDARD_ATERKOMSTTIDER_AR if float(t) <= max_aterkomsttid_ar]
        if not valda:
            return (max_aterkomsttid_ar,)
        if not any(
            math.isclose(t, max_aterkomsttid_ar, rel_tol=1e-12, abs_tol=1e-12) for t in valda
        ):
            valda.append(max_aterkomsttid_ar)
        return tuple(sorted(valda))

    validerade = [
        _validera_positivt_tal(t, f"aterkomsttider_ar[{idx}]")
        for idx, t in enumerate(aterkomsttider_ar)
    ]
    filtrerade = sorted({t for t in validerade if t <= max_aterkomsttid_ar})
    if not filtrerade:
        raise DynamikFel(
            "Ingen återkomsttid i aterkomsttider_ar uppfyller "
            f"<= max_aterkomsttid_ar ({max_aterkomsttid_ar})."
        )
    return tuple(filtrerade)


def _validera_entre_index(
    graf: SankGraf,
    entreer: Sequence[EntrePunkt] | None,
) -> list[tuple[EntrePunkt, int, int]]:
    """Validerar entrépunkter och mappar dem till `(entre, rad, kol)` i rutnätet."""
    if not entreer:
        return []
    geo = graf.geo
    ut: list[tuple[EntrePunkt, int, int]] = []
    for entre in entreer:
        if not (
            math.isfinite(entre.e_m)
            and math.isfinite(entre.n_m)
            and math.isfinite(entre.gransdjup_m)
        ):
            raise DynamikFel(f"Entré '{entre.entre_id}' har icke-ändliga koordinater/gränsdjup.")
        if entre.gransdjup_m <= 0.0:
            raise DynamikFel(f"Entré '{entre.entre_id}' måste ha gransdjup_m > 0.")

        kol = math.floor((entre.e_m - geo.x_min) / geo.pixelstorlek_x)
        rad = math.floor((geo.y_max - entre.n_m) / geo.pixelstorlek_y)
        if not (0 <= rad < geo.hojd and 0 <= kol < geo.bredd):
            raise DynamikFel(
                f"Entré '{entre.entre_id}' ({entre.e_m}, {entre.n_m}) ligger utanför rutnätet."
            )
        ut.append((entre, rad, kol))
    return ut


def _generera_regn_for_steg(
    aterkomsttid_ar: float,
    *,
    regntyp: str,
    klimatfaktor: float,
    varaktighet_min: float,
    tidssteg_min: float,
    toppfaktor: float,
    idf_tabell: IdfTabell | None,
) -> RegnSerieK4:
    """Genererar deterministisk K4-regnserie för ett återkomststeg."""
    try:
        if regntyp == "cds":
            return generera_cds_regn(
                aterkomsttid_ar,
                klimatfaktor=klimatfaktor,
                varaktighet_min=varaktighet_min,
                tidssteg_min=tidssteg_min,
                toppfaktor=toppfaktor,
                idf_tabell=idf_tabell,
            )
        return generera_blockregn(
            aterkomsttid_ar,
            klimatfaktor=klimatfaktor,
            varaktighet_min=varaktighet_min,
            idf_tabell=idf_tabell,
        )
    except RegnFel as exc:
        raise DynamikFel(
            f"Kunde inte generera {regntyp}-regn för återkomsttid {aterkomsttid_ar} år: {exc}"
        ) from exc


def _kor_dynamik_med_cache(
    graf: SankGraf,
    cache: GrafStrukturCache,
    regn: RegnSerieK4,
    *,
    slut_tid_s: float,
    avrinning_faktor: float,
    initial_forlust_mm: float,
    brunnar_avdrag_m3_s: float,
    eff_tid_s: Sequence[float] | None = None,
    eff_intensitet_mm_h: Sequence[float] | None = None,
) -> DynamikResultat:
    """Kör exakt händelsestyrd dynamik och återanvänder förberäknad `GrafStrukturCache`."""
    t_wall_start = time.perf_counter()

    tid_kalla = eff_tid_s if eff_tid_s is not None else regn.tid_s
    intens_kalla = eff_intensitet_mm_h if eff_intensitet_mm_h is not None else regn.intensitet_mm_h
    raw_iv, _ = skapa_regn_intervall(tid_kalla, intens_kalla, slut_tid_s=slut_tid_s)
    netto_iv, netto_regn_m = applicera_initial_forlust(raw_iv, initial_forlust_mm)

    kardinal, tider_hist, nod_niva_hist, nod_volym_hist = _kor_handelsestyrd_karna(
        graf,
        netto_iv,
        float(tid_kalla[0]),
        netto_regn_m,
        cache=cache,
        avrinning_faktor=avrinning_faktor,
        brunnar_avdrag_m3_s=brunnar_avdrag_m3_s,
        spara_tidsserie=True,
        utdata_tider_s=None,
    )

    lov_niva_matris: dict[int, NDArray[np.float64]] = {
        lid: np.asarray(nod_niva_hist[lid], dtype=np.float64) for lid in cache.lov_noder
    }
    max_djup, tid_01, tid_02 = berakna_raster_och_ankomsttider(
        graf, tider_hist, lov_niva_matris, kardinal.lov_har_vatten
    )

    tider_tuple = tuple(tider_hist)
    noder_ut: dict[int, NodTidsserie] = {
        nid: NodTidsserie(
            nod_id=nid,
            tider_s=tider_tuple,
            niva_rh2000=tuple(nod_niva_hist[nid]),
            volym_m3=tuple(nod_volym_hist[nid]),
            t_overfall_s=kardinal.t_overfall_s[nid],
            t_sammanslagning_s=kardinal.t_sammanslagning_s[nid],
        )
        for nid in cache.sorterade_noder
    }

    kortid = time.perf_counter() - t_wall_start
    return DynamikResultat(
        tider_s=tider_tuple,
        noder=noder_ut,
        max_djup_m=max_djup,
        tid_till_0_1m_s=tid_01,
        tid_till_0_2m_s=tid_02,
        overfallsordning=list(kardinal.overfallsordning),
        in_m3=kardinal.in_m3,
        lagrat_m3=kardinal.lagrat_m3,
        ut_m3=kardinal.ut_m3,
        volymfel_rel=kardinal.volymfel_rel,
        kortid_s=kortid,
        metod="handelsestyrd",
    )


def _berakna_skalar_risk(
    djup_serie: Sequence[float],
    aterkomsttider_ar: Sequence[float],
    *,
    gransdjup_m: float,
    max_aterkomsttid_ar: float,
    tidshorisont_ar: float,
) -> tuple[float, bool, float, float]:
    """Beräknar `(T*, overskriden_vid_min_t, p_ar, p_kum)` för en 1D-djupvektor."""
    t_krit = math.inf
    vid_min_t = False

    if djup_serie[0] >= gransdjup_m:
        t_krit = float(aterkomsttider_ar[0])
        vid_min_t = True
    else:
        for idx in range(1, len(djup_serie)):
            h1 = float(djup_serie[idx])
            if h1 >= gransdjup_m:
                h0 = float(djup_serie[idx - 1])
                t0 = float(aterkomsttider_ar[idx - 1])
                t1 = float(aterkomsttider_ar[idx])
                alpha = (gransdjup_m - h0) / (h1 - h0)
                t_val = math.exp(math.log(t0) + alpha * (math.log(t1) - math.log(t0)))
                t_krit = min(t1, max(t0, t_val))
                break

    if math.isfinite(t_krit) and 0.0 < t_krit <= max_aterkomsttid_ar:
        p_ar = min(1.0, max(0.0, 1.0 / t_krit))
        p_kum = min(1.0, max(0.0, 1.0 - math.pow(1.0 - p_ar, tidshorisont_ar)))
    else:
        p_ar = 0.0
        p_kum = 0.0

    return t_krit, vid_min_t, p_ar, p_kum


def _berakna_raster_risk(
    djup_stack: NDArray[np.floating[Any]],
    aterkomsttider_ar: NDArray[np.floating[Any]],
    *,
    gransdjup_m: float,
    max_aterkomsttid_ar: float,
    tidshorisont_ar: float,
) -> tuple[
    NDArray[np.float64],
    NDArray[np.bool_],
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
]:
    """Vektoriserad beräkning i float64 av kritisk T*, sannolikheter och EAD-raster."""
    djup64 = np.asarray(djup_stack, dtype=np.float64)
    t_arr = np.asarray(aterkomsttider_ar, dtype=np.float64)
    ln_t = np.log(t_arr)

    n_steg, hojd, bredd = djup64.shape
    t_krit = np.full((hojd, bredd), np.inf, dtype=np.float64)
    vid_min_t = np.asarray(djup64[0] >= gransdjup_m, dtype=np.bool_)
    t_krit[vid_min_t] = t_arr[0]
    hittad = vid_min_t.copy()

    for idx in range(1, n_steg):
        ny = (~hittad) & (djup64[idx] >= gransdjup_m)
        if np.any(ny):
            h0 = djup64[idx - 1][ny]
            h1 = djup64[idx][ny]
            alpha = (gransdjup_m - h0) / (h1 - h0)
            t_val = np.exp(ln_t[idx - 1] + alpha * (ln_t[idx] - ln_t[idx - 1]))
            t_krit[ny] = np.clip(t_val, t_arr[idx - 1], t_arr[idx])
            hittad |= ny

    giltig_t = np.isfinite(t_krit) & (t_krit > 0.0) & (t_krit <= max_aterkomsttid_ar)
    p_ar = np.zeros((hojd, bredd), dtype=np.float64)
    if np.any(giltig_t):
        p_ar[giltig_t] = np.clip(1.0 / t_krit[giltig_t], 0.0, 1.0)

    p_kum = np.zeros((hojd, bredd), dtype=np.float64)
    positiv_p = p_ar > 0.0
    if np.any(positiv_p):
        p_kum[positiv_p] = np.clip(
            1.0 - np.power(1.0 - p_ar[positiv_p], tidshorisont_ar),
            0.0,
            1.0,
        )

    if n_steg < 2:
        ead_djup: NDArray[np.float64] = np.zeros((hojd, bredd), dtype=np.float64)
        ead_overdjup: NDArray[np.float64] = np.zeros((hojd, bredd), dtype=np.float64)
    else:
        p_stigande = (1.0 / t_arr)[::-1]
        djup_stigande = djup64[::-1]
        dp = np.diff(p_stigande)[:, None, None]
        ead_djup = np.asarray(
            np.sum(0.5 * (djup_stigande[:-1] + djup_stigande[1:]) * dp, axis=0),
            dtype=np.float64,
        )
        over_stigande = np.maximum(0.0, djup_stigande - gransdjup_m)
        ead_overdjup = np.asarray(
            np.sum(0.5 * (over_stigande[:-1] + over_stigande[1:]) * dp, axis=0),
            dtype=np.float64,
        )

    return t_krit, vid_min_t, p_ar, p_kum, ead_djup, ead_overdjup


def _nod_max_djup_per_steg(
    graf: SankGraf,
    cache: GrafStrukturCache,
    dynamik: DynamikResultat,
) -> dict[int, float]:
    """Beräknar maximalt vattendjup (m) i varje nod för ett kört återkomststeg."""
    lov_djup: dict[int, float] = {}
    for lid in cache.lov_noder:
        serie = dynamik.noder.get(lid)
        if serie is None or not serie.volym_m3 or max(serie.volym_m3) <= 0.0:
            lov_djup[lid] = 0.0
        else:
            botten = float(graf.noder[lid].botten_rh2000)
            lov_djup[lid] = max(0.0, max(serie.niva_rh2000) - botten)

    nod_djup: dict[int, float] = {}
    for nid in cache.sorterade_noder:
        lov_barn = cache.lov_under[nid]
        nod_djup[nid] = max((lov_djup[lid] for lid in lov_barn), default=0.0)
    return nod_djup


def berakna_kumulativ_oversvamningsrisk(
    graf: SankGraf,
    gransdjup_m: float = 0.10,
    max_aterkomsttid_ar: float = 100.0,
    *,
    klimatfaktor: float = 1.0,
    tidshorisont_ar: float = 50.0,
    aterkomsttider_ar: Sequence[float] | None = None,
    varaktighet_min: float = STANDARD_VARAKTIGHET_MIN,
    tidssteg_min: float = STANDARD_TIDSSTEG_MIN,
    regntyp: str = "cds",
    toppfaktor: float = STANDARD_CDS_TOPPFAKTOR,
    idf_tabell: IdfTabell | None = None,
    entreer: Sequence[EntrePunkt] | None = None,
    avrinning_faktor: float = 1.0,
    initial_forlust_mm: float = 0.0,
    brunnar_avdrag_m3_s: float = 0.0,
    efterforlopp_s: float = 0.0,
    infiltration_jordart: str | None = None,
    grundvatten: str = "normal",
    tjale: bool = False,
    infiltration_initial_mm: float = 2.0,
    indata: Sequence[FilPost] | None = None,
    kodversion: str | None = None,
    start: str | None = None,
) -> KumulativRiskResultat:
    """Beräknar deterministisk kumulativ översvämningsrisk upp till gränsdjup och max T.

    Utvärderar en stigande återkomsttidsstege `<= max_aterkomsttid_ar` med exakt
    händelsestyrd fyllningsdynamik, log-interpolerar kritisk återkomsttid `T*` där
    vattendjupet når `gransdjup_m`, och beräknar årlig (`1/T*`) samt kumulativ
    överskridandesannolikhet `1 - (1 - 1/T*)^N` över `tidshorisont_ar` år samt
    förväntat årligt djup och överdjup via trapetsintegration i sannolikhet `p = 1/T`.
    """
    t_wall_start = time.perf_counter()

    grans = _validera_positivt_tal(gransdjup_m, "gransdjup_m")
    max_t = _validera_positivt_tal(max_aterkomsttid_ar, "max_aterkomsttid_ar")
    kf = _validera_positivt_tal(klimatfaktor, "klimatfaktor")
    horisont = _validera_positivt_tal(tidshorisont_ar, "tidshorisont_ar")
    varakt = _validera_positivt_tal(varaktighet_min, "varaktighet_min")
    dt_min = _validera_positivt_tal(tidssteg_min, "tidssteg_min")
    tf = _validera_positivt_tal(toppfaktor, "toppfaktor")
    if tf >= 1.0:
        raise DynamikFel(f"toppfaktor måste ligga strikt i (0, 1), fick {tf}.")
    if regntyp not in ("cds", "block"):
        raise DynamikFel(f"Ogiltig regntyp '{regntyp}', måste vara 'cds' eller 'block'.")

    avr = _validera_icke_negativt_tal(avrinning_faktor, "avrinning_faktor")
    init_forlust = _validera_icke_negativt_tal(initial_forlust_mm, "initial_forlust_mm")
    brunnar = _validera_icke_negativt_tal(brunnar_avdrag_m3_s, "brunnar_avdrag_m3_s")
    efterforlopp = _validera_icke_negativt_tal(efterforlopp_s, "efterforlopp_s")

    t_stege = _bygg_aterkomsttidsstege(max_t, aterkomsttider_ar)
    entre_index = _validera_entre_index(graf, entreer)

    cache = bygg_graf_struktur_cache(graf)

    z64 = np.asarray(graf.mark_z, dtype=np.float64)
    aktiv_mask = np.asarray(
        (graf.markslag != MARKSLAG_BYGGNAD) & np.isfinite(z64),
        dtype=np.bool_,
    )
    cell_area_m2 = float(graf.geo.pixelstorlek_x) * float(graf.geo.pixelstorlek_y)
    aktiv_cell_area_m2 = float(np.sum(aktiv_mask)) * cell_area_m2

    dtw_arr: NDArray[np.float32] | None = None
    if infiltration_jordart is not None:
        z_arr = np.asarray(graf.mark_z, dtype=np.float32)
        giltig_z = np.isfinite(z_arr)
        if np.any(giltig_z):
            vatten_mask = np.asarray(graf.markslag == VATTEN, dtype=np.bool_)
            if not np.any(vatten_mask):
                z_min = float(np.min(z_arr[giltig_z]))
                vatten_mask = np.asarray(giltig_z & (z_arr <= z_min + 0.05), dtype=np.bool_)
            z_ren = np.where(
                giltig_z, z_arr, float(np.median(z_arr[giltig_z]))
            ).astype(np.float32)
            dtw_arr = uppskatta_dtw_fran_terrang(
                z_ren,
                vatten_mask,
                pixelstorlek_m=float(graf.geo.pixelstorlek_x),
            )

    steg_lista: list[AterkomstStegResultat] = []
    djup_lager: list[NDArray[np.float64]] = []
    nod_djup_per_steg: list[dict[int, float]] = []

    for t_ar in t_stege:
        regn = _generera_regn_for_steg(
            t_ar,
            regntyp=regntyp,
            klimatfaktor=kf,
            varaktighet_min=varakt,
            tidssteg_min=dt_min,
            toppfaktor=tf,
            idf_tabell=idf_tabell,
        )
        eff_tid: Sequence[float] | None = None
        eff_int: Sequence[float] | None = None
        steg_init = init_forlust
        if infiltration_jordart is not None:
            try:
                koppl = berakna_effektivt_regn_k5(
                    graf.markslag,
                    regn.tid_s,
                    regn.intensitet_mm_h,
                    jordart=infiltration_jordart,
                    grundvatten=grundvatten,
                    tjale=tjale,
                    initial_mm=infiltration_initial_mm,
                    pixelstorlek_m=float(graf.geo.pixelstorlek_x),
                    mark_z=graf.mark_z,
                    dtw_m=dtw_arr,
                    regn_ref=regn.regn_id,
                )
            except ForlustFel as exc:
                raise DynamikFel(f"Ogiltig markförlustparameter: {exc}") from exc
            eff_tid = koppl.effektiv_regn_tid_s
            eff_int = koppl.effektiv_regn_intensitet_mm_h
            steg_init = 0.0

        slut_tid_s = float(regn.tid_s[-1]) + efterforlopp
        dynamik = _kor_dynamik_med_cache(
            graf,
            cache,
            regn,
            slut_tid_s=slut_tid_s,
            avrinning_faktor=avr,
            initial_forlust_mm=steg_init,
            brunnar_avdrag_m3_s=brunnar,
            eff_tid_s=eff_tid,
            eff_intensitet_mm_h=eff_int,
        )
        djup64 = np.asarray(dynamik.max_djup_m, dtype=np.float64)
        djup_lager.append(djup64)

        steg_max_djup = float(np.max(djup64)) if djup64.size > 0 else 0.0
        steg_area_over = float(np.sum(aktiv_mask & (djup64 >= grans))) * cell_area_m2

        steg_lista.append(
            AterkomstStegResultat(
                aterkomsttid_ar=t_ar,
                regn=regn,
                dynamik=dynamik,
                max_djup_m=steg_max_djup,
                area_over_grans_m2=steg_area_over,
            )
        )
        nod_djup_per_steg.append(_nod_max_djup_per_steg(graf, cache, dynamik))

    djup_stack = np.stack(djup_lager, axis=0)
    t_arr = np.asarray(t_stege, dtype=np.float64)

    t_krit64, vid_min_t, p_ar64, p_kum64, ead_djup64, ead_over64 = _berakna_raster_risk(
        djup_stack,
        t_arr,
        gransdjup_m=grans,
        max_aterkomsttid_ar=max_t,
        tidshorisont_ar=horisont,
    )

    # Nodvis kumulativ risk
    noder_risk: dict[int, NodKumulativRisk] = {}
    for nid in cache.sorterade_noder:
        h_nod = [steg_nod[nid] for steg_nod in nod_djup_per_steg]
        nod_t_krit, nod_vid_min, nod_p_ar, nod_p_kum = _berakna_skalar_risk(
            h_nod,
            t_stege,
            gransdjup_m=grans,
            max_aterkomsttid_ar=max_t,
            tidshorisont_ar=horisont,
        )
        noder_risk[nid] = NodKumulativRisk(
            nod_id=nid,
            kritisk_aterkomsttid_ar=nod_t_krit,
            overskriden_vid_min_t=nod_vid_min,
            arlig_overskridande_sannolikhet=nod_p_ar,
            kumulativ_sannolikhet=nod_p_kum,
            max_djup_vid_tmax_m=h_nod[-1],
        )

    # Entrévis kumulativ risk (varje entré med sitt eget gransdjup_m)
    entre_risk_lista: list[EntreKumulativRisk] = []
    for entre, rad, kol in entre_index:
        h_entre = [float(djup_lager[idx][rad, kol]) for idx in range(len(t_stege))]
        e_grans = float(entre.gransdjup_m)
        e_t_krit, e_vid_min, e_p_ar, e_p_kum = _berakna_skalar_risk(
            h_entre,
            t_stege,
            gransdjup_m=e_grans,
            max_aterkomsttid_ar=max_t,
            tidshorisont_ar=horisont,
        )
        entre_risk_lista.append(
            EntreKumulativRisk(
                entre_id=entre.entre_id,
                gransdjup_m=e_grans,
                kritisk_aterkomsttid_ar=e_t_krit,
                overskriden_vid_min_t=e_vid_min,
                arlig_overskridande_sannolikhet=e_p_ar,
                kumulativ_sannolikhet=e_p_kum,
                max_djup_vid_tmax_m=h_entre[-1],
            )
        )

    over_grans_mask = aktiv_mask & (p_ar64 > 0.0)
    area_over_grans_m2 = float(np.sum(over_grans_mask)) * cell_area_m2
    andel_over_grans = (
        area_over_grans_m2 / aktiv_cell_area_m2 if aktiv_cell_area_m2 > 0.0 else 0.0
    )
    max_arlig_sann = float(np.max(p_ar64)) if p_ar64.size > 0 else 0.0
    max_kum_sann = float(np.max(p_kum64)) if p_kum64.size > 0 else 0.0
    min_krit_t = float(np.min(t_krit64)) if t_krit64.size > 0 else math.inf
    max_ead_djup = float(np.max(ead_djup64)) if ead_djup64.size > 0 else 0.0
    max_volymfel = max((s.dynamik.volymfel_rel for s in steg_lista), default=0.0)

    max_djup_tmax = np.asarray(steg_lista[-1].dynamik.max_djup_m, dtype=np.float32).copy()
    statistikkalla = steg_lista[0].regn.statistikkalla
    kortid_s = time.perf_counter() - t_wall_start

    manifest = skapa_manifest(
        modul="dynamik",
        indata=list(indata) if indata is not None else [],
        parametrar={
            "lage": "kumulativ_risk",
            "gransdjup_m": grans,
            "max_aterkomsttid_ar": max_t,
            "aterkomsttider_ar": list(t_stege),
            "klimatfaktor": kf,
            "tidshorisont_ar": horisont,
            "varaktighet_min": varakt,
            "tidssteg_min": dt_min,
            "regntyp": regntyp,
            "toppfaktor": tf if regntyp == "cds" else None,
            "statistikkalla": statistikkalla,
            "avrinning_faktor": avr,
            "initial_forlust_mm": init_forlust,
            "brunnar_avdrag_m3_s": brunnar,
            "efterforlopp_s": efterforlopp,
            "infiltration_jordart": infiltration_jordart,
            "grundvatten": grundvatten,
            "tjale": bool(tjale),
            "infiltration_initial_mm": float(infiltration_initial_mm),
            "epsg": graf.geo.epsg,
        },
        utdata=[],
        matvarden={
            "area_over_grans_m2": area_over_grans_m2,
            "andel_over_grans": andel_over_grans,
            "max_arlig_sannolikhet": max_arlig_sann,
            "max_kumulativ_sannolikhet": max_kum_sann,
            "min_kritisk_aterkomsttid_ar": _json_tal(min_krit_t),
            "max_forvantat_arligt_djup_m": max_ead_djup,
            "max_djup_vid_tmax_m": (
                float(np.max(max_djup_tmax)) if max_djup_tmax.size > 0 else 0.0
            ),
            "max_volymfel_rel": max_volymfel,
            "antal_aterkomststeg": len(steg_lista),
            "antal_noder": len(noder_risk),
            "antal_entreer": len(entre_risk_lista),
            "kortid_s": kortid_s,
        },
        kodversion=kodversion,
        start=start,
    )

    return KumulativRiskResultat(
        gransdjup_m=grans,
        max_aterkomsttid_ar=max_t,
        aterkomsttider_ar=t_stege,
        klimatfaktor=kf,
        tidshorisont_ar=horisont,
        varaktighet_min=varakt,
        regntyp=regntyp,
        statistikkalla=statistikkalla,
        kritisk_aterkomsttid_ar=np.asarray(t_krit64, dtype=np.float32),
        overskriden_vid_min_t=vid_min_t,
        arlig_overskridande_sannolikhet=np.asarray(p_ar64, dtype=np.float32),
        kumulativ_sannolikhet=np.asarray(p_kum64, dtype=np.float32),
        forvantat_arligt_djup_m=np.asarray(ead_djup64, dtype=np.float32),
        forvantat_arligt_overdjup_m=np.asarray(ead_over64, dtype=np.float32),
        max_djup_vid_tmax_m=max_djup_tmax,
        steg=tuple(steg_lista),
        noder=noder_risk,
        entreer=tuple(entre_risk_lista),
        area_over_grans_m2=area_over_grans_m2,
        andel_over_grans=andel_over_grans,
        max_arlig_sannolikhet=max_arlig_sann,
        max_kumulativ_sannolikhet=max_kum_sann,
        min_kritisk_aterkomsttid_ar=min_krit_t,
        max_forvantat_arligt_djup_m=max_ead_djup,
        max_volymfel_rel=max_volymfel,
        kortid_s=kortid_s,
        manifest=manifest,
    )
