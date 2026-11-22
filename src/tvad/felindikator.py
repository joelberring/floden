"""Automatisk a posteriori 2D-felindikator för sänkgraf vs lokal tröghet (Steg 3, Q3).

Avgör deterministiskt ur fysikaliska storheter *var* grafantagandet ("plan vattenyta
i varje sänka") inte håller och där 2D-lösaren (`tvad.hybrid` / `tvad.lokal_troghet`)
bör slås på:

1. **Terränglutning / skiktflödesindikator:** lutande mark med grunt vatten ger
   skiktflöde snarare än plan vattenyta.
2. **Uppskattat lokalt Froudetal vid överfall:** `Fr = u / sqrt(g * h)` ur
   Manning-normalflöde vid överfallskanten.
3. **Genomströmningskvot:** kvot mellan tillrinnande volym och nodens lagringsvolym
   när noden svämmar över ("genomspolningsnod" har inte horisontell vattenyta).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from dynamik.nodmodell import DynamikResultat
from sankgraf.typer import (
    HAV_NOD_ID,
    MARKSLAG_BYGGNAD,
    SankGraf,
    StatiskFyllningResultat,
)
from testsvit.k1_io import GeoTransformInfo
from tvad.lokal_troghet import GRAVITATION_M_S2, TvadFel

STANDARD_LUTNING_TROSKEL = 0.015
STANDARD_FROUDE_TROSKEL = 0.30
STANDARD_GENOMSTROMNING_TROSKEL = 4.0
STANDARD_POANG_TROSKEL = 1.0
_MANNING_REF = 0.015


@dataclass(frozen=True)
class NodFelindikator:
    """Dimensionslös a posteriori felindikator för en enskild nod i sänkgrafen."""

    nod_id: int
    lutning_median: float
    lutning_indikator: float
    froude_overfall: float
    froude_indikator: float
    genomstromningskvot: float
    genomstromning_indikator: float
    poang: float
    kraver_2d: bool
    orsak: str

    def till_dict(self) -> dict[str, Any]:
        return {
            "nod_id": int(self.nod_id),
            "lutning_median": float(self.lutning_median),
            "lutning_indikator": float(self.lutning_indikator),
            "froude_overfall": float(self.froude_overfall),
            "froude_indikator": float(self.froude_indikator),
            "genomstromningskvot": (
                float(self.genomstromningskvot) if math.isfinite(self.genomstromningskvot) else None
            ),
            "genomstromning_indikator": (
                float(self.genomstromning_indikator)
                if math.isfinite(self.genomstromning_indikator)
                else None
            ),
            "poang": float(self.poang) if math.isfinite(self.poang) else None,
            "kraver_2d": bool(self.kraver_2d),
            "orsak": self.orsak,
        }


@dataclass(frozen=True)
class FelindikatorResultat:
    """Samlad a posteriori bedömning av var 2D-lösaren bör aktiveras."""

    nod_indikator: dict[int, NodFelindikator]
    rekommenderad_2d_mask: NDArray[np.bool_]
    andel_2d_yta: float
    antal_2d_celler: int
    antal_aktiva_celler: int
    antal_noder_2d: int
    antal_noder_totalt: int


def _berakna_lutningsraster(
    mark_z: NDArray[np.float64],
    aktiv: NDArray[np.bool_],
    dx: float,
    dy: float,
) -> NDArray[np.float64]:
    """Lokal terränglutning |∇z| (m/m) på aktiva markceller (ignorerar byggnader/NaN)."""
    h, w = mark_z.shape
    gx = np.zeros((h, w), dtype=np.float64)
    gy = np.zeros((h, w), dtype=np.float64)

    if w >= 2:
        dif_x = np.abs(mark_z[:, 1:] - mark_z[:, :-1]) / dx
        gilt_x = aktiv[:, 1:] & aktiv[:, :-1]
        dif_x = np.where(gilt_x, dif_x, 0.0)
        gx[:, :-1] = np.maximum(gx[:, :-1], dif_x)
        gx[:, 1:] = np.maximum(gx[:, 1:], dif_x)

    if h >= 2:
        dif_y = np.abs(mark_z[1:, :] - mark_z[:-1, :]) / dy
        gilt_y = aktiv[1:, :] & aktiv[:-1, :]
        dif_y = np.where(gilt_y, dif_y, 0.0)
        gy[:-1, :] = np.maximum(gy[:-1, :], dif_y)
        gy[1:, :] = np.maximum(gy[1:, :], dif_y)

    return np.hypot(gx, gy)


def _uppstroms_area_m2(graf: SankGraf) -> dict[int, float]:
    """Total uppströms avrinningsyta (m²) som når varje nod (egna löv + uppströms överfall)."""
    in_kanter: dict[int, list[int]] = {nid: [] for nid in graf.noder}
    for nid, nod in graf.noder.items():
        if nod.ut_till_id is not None and nod.ut_till_id in in_kanter:
            in_kanter[nod.ut_till_id].append(nid)
    lov_cache = {nid: set(graf.lov_under_nod(nid)) for nid in graf.noder}
    ut: dict[int, float] = {}
    for nid in sorted(graf.noder):
        besokta: set[int] = set()
        stack = [nid]
        lov_summa: set[int] = set()
        while stack:
            akt = stack.pop()
            if akt in besokta:
                continue
            besokta.add(akt)
            lov_summa.update(lov_cache[akt])
            stack.extend(in_kanter.get(akt, ()))
        ut[nid] = sum(float(graf.noder[lid].avrinningsyta_m2) for lid in lov_summa)
    return ut


def berakna_2d_felindikator(
    mark_z: NDArray[np.float32] | NDArray[np.float64],
    markslag: NDArray[np.uint8],
    geo: GeoTransformInfo,
    graf: SankGraf,
    graf_resultat: DynamikResultat | StatiskFyllningResultat | None = None,
    *,
    topp_regn_mm_h: float | None = None,
    varaktighet_s: float = 3600.0,
    lutning_troskel: float = STANDARD_LUTNING_TROSKEL,
    froude_troskel: float = STANDARD_FROUDE_TROSKEL,
    genomstromning_troskel: float = STANDARD_GENOMSTROMNING_TROSKEL,
    poang_troskel: float = STANDARD_POANG_TROSKEL,
) -> FelindikatorResultat:
    """Beräknar fysikalisk a posteriori 2D-felindikator per nod och cell.

    Returnerar `FelindikatorResultat` med poäng/orsak per nod samt `rekommenderad_2d_mask`
    på K1-gittret för celler som bör lösas i 2D-hybrid.
    """
    if mark_z.ndim != 2 or markslag.ndim != 2 or mark_z.shape != markslag.shape:
        raise TvadFel("mark_z och markslag måste vara 2D-matriser med samma form.")
    if mark_z.shape != graf.cell_till_nod.shape:
        raise TvadFel(
            f"mark_z har form {mark_z.shape}, grafen {graf.cell_till_nod.shape}."
        )
    if lutning_troskel <= 0.0 or froude_troskel <= 0.0 or genomstromning_troskel <= 0.0:
        raise TvadFel("Tröskelvärdena måste vara > 0.")

    z64 = np.asarray(mark_z, dtype=np.float64)
    aktiv = np.asarray((markslag != MARKSLAG_BYGGNAD) & np.isfinite(z64), dtype=np.bool_)
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    cell_area = dx * dy

    lutning = _berakna_lutningsraster(z64, aktiv, dx, dy)
    uppstroms_area = _uppstroms_area_m2(graf)

    # Uppskatta effektiv regnintensitet (m/s) och total regnhöjd (m)
    aktiv_area_m2 = float(np.count_nonzero(aktiv)) * cell_area
    if topp_regn_mm_h is not None:
        i_topp_m_s = max(0.0, float(topp_regn_mm_h)) / 3.6e6
        total_regn_m = i_topp_m_s * max(1.0, float(varaktighet_s))
    elif isinstance(graf_resultat, DynamikResultat):
        dt_tot = (
            max(1.0, float(graf_resultat.tider_s[-1] - graf_resultat.tider_s[0]))
            if len(graf_resultat.tider_s) >= 2
            else max(1.0, float(varaktighet_s))
        )
        total_regn_m = (
            float(graf_resultat.in_m3) / aktiv_area_m2 if aktiv_area_m2 > 0.0 else 0.0
        )
        i_topp_m_s = total_regn_m / dt_tot
    elif isinstance(graf_resultat, StatiskFyllningResultat):
        total_regn_m = (
            float(graf_resultat.in_m3) / aktiv_area_m2 if aktiv_area_m2 > 0.0 else 0.0
        )
        i_topp_m_s = total_regn_m / max(1.0, float(varaktighet_s))
    else:
        i_topp_m_s = 72.0 / 3.6e6
        total_regn_m = i_topp_m_s * max(1.0, float(varaktighet_s))

    nod_indikator: dict[int, NodFelindikator] = {}
    rekommenderad_mask = np.zeros(z64.shape, dtype=np.bool_)

    for nid in sorted(graf.noder):
        nod = graf.noder[nid]
        lov_ids = graf.lov_under_nod(nid)
        cell_mask = aktiv & np.isin(graf.cell_till_nod, lov_ids)
        bassin_mask = cell_mask & (z64 < float(nod.overfall_rh2000))

        # Terränglutning i bassängbotten (eller hela noden om ingen bassäng under överfall)
        lutn_urval = lutning[bassin_mask] if np.any(bassin_mask) else lutning[cell_mask]
        lutn_med = float(np.median(lutn_urval)) if lutn_urval.size > 0 else 0.0

        # Maxdjup och överfallsstatus ur grafresultat
        har_overfall = False
        max_djup_nod = float(nod.persistens_m)
        if isinstance(graf_resultat, DynamikResultat) and nid in graf_resultat.noder:
            serie = graf_resultat.noder[nid]
            max_niva = float(np.max(serie.niva_rh2000)) if serie.niva_rh2000 else nod.botten_rh2000
            max_djup_nod = max(0.0, max_niva - float(nod.botten_rh2000))
            har_overfall = serie.t_overfall_s is not None
        elif (
            isinstance(graf_resultat, StatiskFyllningResultat)
            and nid in graf_resultat.nod_niva_rh2000
        ):
            max_niva = float(graf_resultat.nod_niva_rh2000[nid])
            max_djup_nod = max(0.0, max_niva - float(nod.botten_rh2000))
            v_nod = float(graf_resultat.nod_volym_m3.get(nid, 0.0))
            har_overfall = (
                nod.max_volym_m3 <= 1e-9 and total_regn_m > 0.0
            ) or v_nod >= 0.999 * nod.max_volym_m3

        # 1. Lutnings-/skiktflödesindikator: brant lutning + grunt vatten => hög indikator
        damping = 1.0 + (max_djup_nod / 0.10) ** 2
        lutn_ind = (lutn_med / lutning_troskel) / damping

        # 2. Lokalt Froudetal vid överfall (Manning normalflöde)
        area_upp = uppstroms_area[nid]
        if har_overfall and i_topp_m_s > 0.0 and area_upp > 0.0:
            q_topp = i_topp_m_s * area_upp
            n_ov_celler = max(1, len(graf.overfall_celler.get(nid, ())))
            bredd_ov = float(n_ov_celler) * min(dx, dy)
            q_enhet = q_topp / max(bredd_ov, 1e-6)
            s_lok = max(lutn_med, 1e-4)
            h_n = (q_enhet * _MANNING_REF / math.sqrt(s_lok)) ** 0.6
            if h_n > 1e-6:
                u_n = q_enhet / h_n
                fr = u_n / math.sqrt(GRAVITATION_M_S2 * h_n)
            else:
                fr = 0.0
        else:
            fr = 0.0
        fr_ind = fr / froude_troskel

        # 3. Genomströmningskvot (tillrinning / lagringsvolym vid överfall)
        v_in = total_regn_m * area_upp
        v_max = float(nod.max_volym_m3)
        if not har_overfall or v_in <= 0.0:
            genom_kvot = 0.0
            genom_ind = 0.0
        elif v_max <= 1e-9:
            genom_kvot = math.inf
            genom_ind = math.inf
        else:
            genom_kvot = v_in / v_max
            genom_ind = genom_kvot / genomstromning_troskel

        poang = max(lutn_ind, fr_ind, genom_ind)
        kraver_2d = poang >= poang_troskel

        if not kraver_2d:
            orsak = "Plan vattenyta tillräcklig"
        elif fr_ind >= lutn_ind and fr_ind >= genom_ind:
            orsak = f"Högt lokalt Froudetal vid överfall (Fr={fr:.2f})"
        elif genom_ind >= lutn_ind:
            orsak = f"Hög genomströmningskvot (V_in/V_max={genom_kvot:.1f})"
        else:
            orsak = f"Skiktflöde på lutande terräng (lutning={lutn_med:.3f})"

        nod_indikator[nid] = NodFelindikator(
            nod_id=nid,
            lutning_median=lutn_med,
            lutning_indikator=lutn_ind,
            froude_overfall=fr,
            froude_indikator=fr_ind,
            genomstromningskvot=genom_kvot,
            genomstromning_indikator=genom_ind,
            poang=poang,
            kraver_2d=kraver_2d,
            orsak=orsak,
        )

        if kraver_2d and nod.ar_lov:
            rekommenderad_mask |= cell_mask

    # Celler som dränerar direkt ut mot randen (HAV_NOD_ID, t.ex. A4 lutande plan):
    # aktivera 2D längs lutande HAV-stråk när HAV-ytan utgör ett sammanhängande
    # avrinningsområde (>= 5 % av aktiv yta) eller tar emot överfall från en 2D-nod.
    ar_hav = aktiv & (graf.cell_till_nod == HAV_NOD_ID)
    antal_aktiva = int(np.count_nonzero(aktiv))
    har_hav_overfall = any(
        ind.kraver_2d and graf.noder[nid].ut_till_id is None
        for nid, ind in nod_indikator.items()
    )
    hav_andel = float(np.count_nonzero(ar_hav)) / float(antal_aktiva) if antal_aktiva > 0 else 0.0
    if np.any(ar_hav) and (har_hav_overfall or hav_andel >= 0.05):
        lutning_hav = _berakna_lutningsraster(z64, ar_hav, dx, dy)
        hav_mask = ar_hav & (lutning_hav >= lutning_troskel)
        rekommenderad_mask |= hav_mask

    antal_2d = int(np.count_nonzero(rekommenderad_mask))
    andel_2d = float(antal_2d) / float(antal_aktiva) if antal_aktiva > 0 else 0.0
    antal_noder_2d = sum(1 for ind in nod_indikator.values() if ind.kraver_2d)

    return FelindikatorResultat(
        nod_indikator=nod_indikator,
        rekommenderad_2d_mask=rekommenderad_mask,
        andel_2d_yta=andel_2d,
        antal_2d_celler=antal_2d,
        antal_aktiva_celler=antal_aktiva,
        antal_noder_2d=antal_noder_2d,
        antal_noder_totalt=len(nod_indikator),
    )
