"""Parametrisk rekonstruktion av gatuprofiler från punktmoln (T01b).

Rekonstruerar körbana (bombering eller enkelsidigt tvärfall), rännstenar,
kantstenar (inklusive sänkta kantstenar vid infarter) och gångbanor med:
1. Huber-robust tvärsektionspassning (IRLS) mot parkerade bilar och felpunkter.
2. Bandad tridiagonal utjämning längs gatan i O(M) tid (Thomas-algoritmen)
   med exakt O(M) posterior standardavvikelse.
3. Identifiering av rännstensbrunnar i lokala lågpunkter längs rännstenarna.
4. Rastrering in i K1-markmodellens 0,25 m-rutnät samt kantstensbrytlinjer.
5. Tvärsnittsbild i PNG med råpunkter, anpassad profil och osäkerhetsband.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw

from markmodell.brytlinjer import Brytlinje
from markmodell.hinder_oppningar import HARDGJORT
from markmodell.rutnat import Ruta
from testsvit.k1_io import BrytlinjePost, GeoTransformInfo

# Stockholms typvärden när punktmolnsdata saknas eller inte räcker (T01b steg 6)
TYP_KANTSTEN_HOJD_M: float = 0.12
TYP_TVARFALL: float = 0.025
TYP_GANGBANA_LUTNING: float = 0.02
TYP_OSAKERHET_Z_M: float = 0.10
MIN_OSAKERHET_Z_M: float = 0.005

_SIDA_ALIAS: dict[str, str] = {
    "vanster": "vanster",
    "vänster": "vanster",
    "hoger": "hoger",
    "höger": "hoger",
    "bada": "bada",
    "båda": "bada",
}
GILTIGA_SIDOR: frozenset[str] = frozenset({"vanster", "hoger", "bada"})


class GatuProfilFel(Exception):
    """Fel vid validering, passning, utjämning eller rastrering av gatuprofiler."""


def _normalisera_sida(sida: str, *, tillat_bada: bool = True) -> str:
    if not isinstance(sida, str):
        raise GatuProfilFel(f"Sida måste vara en sträng, fick {type(sida).__name__}.")
    kanonisk = _SIDA_ALIAS.get(sida.strip().lower())
    if kanonisk is None or (not tillat_bada and kanonisk == "bada"):
        giltiga = "vanster, hoger, bada" if tillat_bada else "vanster, hoger"
        raise GatuProfilFel(f"Ogiltig sida '{sida}'. Giltiga värden: {giltiga}.")
    return kanonisk


@dataclass(frozen=True)
class InfartSankning:
    """Sänkt kantsten vid infart eller övergångsställe längs en gatukorridor."""

    s_start_m: float
    s_slut_m: float
    sida: str
    kantsten_hojd_m: float = 0.02

    def __post_init__(self) -> None:
        kanonisk = _normalisera_sida(self.sida, tillat_bada=True)
        object.__setattr__(self, "sida", kanonisk)

        s0 = float(self.s_start_m)
        s1 = float(self.s_slut_m)
        hojd = float(self.kantsten_hojd_m)
        if not (math.isfinite(s0) and math.isfinite(s1) and math.isfinite(hojd)):
            raise GatuProfilFel("InfartSankning måste ha ändliga mätvärden.")
        if s0 < 0.0 or s1 <= s0:
            raise GatuProfilFel(
                f"Ogiltigt längdintervall för infart: [{s0}, {s1}] (kräver 0 <= s_start < s_slut)."
            )
        if hojd < 0.0:
            raise GatuProfilFel(f"Kantstenshöjd vid infart måste vara >= 0, fick {hojd} m.")
        object.__setattr__(self, "s_start_m", s0)
        object.__setattr__(self, "s_slut_m", s1)
        object.__setattr__(self, "kantsten_hojd_m", hojd)

    def galler_sida(self, sida: str) -> bool:
        """Returnerar True om infarten påverkar angiven sida (`vanster` eller `hoger`)."""
        return self.sida in {sida, "bada"}


@dataclass(frozen=True)
class GatuKorridor:
    """Gatukorridor definierad kring en mittlinje i SWEREF 99 18 00 (EPSG:3011)."""

    gata_id: str
    mittlinje: tuple[tuple[float, float], ...]
    korbana_halvbredd_m: float = 3.25
    gangbana_bredd_m: float = 2.75
    bombering: bool = True
    infarter: tuple[InfartSankning, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.gata_id, str) or not self.gata_id.strip():
            raise GatuProfilFel("gata_id måste vara en icke-tom sträng.")
        if len(self.mittlinje) < 2:
            raise GatuProfilFel("Mittlinjen måste innehålla minst två punkter.")

        rensade: list[tuple[float, float]] = []
        for punkt in self.mittlinje:
            e_m = float(punkt[0])
            n_m = float(punkt[1])
            if not (math.isfinite(e_m) and math.isfinite(n_m)):
                raise GatuProfilFel(f"Ogiltig koordinat i mittlinje: {punkt!r}")
            rensade.append((e_m, n_m))

        tot_langd = 0.0
        for idx in range(len(rensade) - 1):
            de = rensade[idx + 1][0] - rensade[idx][0]
            dn = rensade[idx + 1][1] - rensade[idx][1]
            tot_langd += math.hypot(de, dn)
        if tot_langd <= 0.0:
            raise GatuProfilFel("Mittlinjens totala längd måste vara strikt positiv.")

        w_k = float(self.korbana_halvbredd_m)
        w_g = float(self.gangbana_bredd_m)
        if not math.isfinite(w_k) or w_k <= 0.0:
            raise GatuProfilFel(f"korbana_halvbredd_m måste vara > 0, fick {w_k}.")
        if not math.isfinite(w_g) or w_g < 0.0:
            raise GatuProfilFel(f"gangbana_bredd_m måste vara >= 0, fick {w_g}.")

        object.__setattr__(self, "mittlinje", tuple(rensade))
        object.__setattr__(self, "korbana_halvbredd_m", w_k)
        object.__setattr__(self, "gangbana_bredd_m", w_g)
        object.__setattr__(self, "infarter", tuple(self.infarter))

    @property
    def total_halvbredd_m(self) -> float:
        """Halv korridorbredd inklusive körbana och gångbana (m)."""
        return self.korbana_halvbredd_m + self.gangbana_bredd_m

    @property
    def langd_m(self) -> float:
        """Mittlinjens totala båglängd (m)."""
        tot = 0.0
        for idx in range(len(self.mittlinje) - 1):
            de = self.mittlinje[idx + 1][0] - self.mittlinje[idx][0]
            dn = self.mittlinje[idx + 1][1] - self.mittlinje[idx][1]
            tot += math.hypot(de, dn)
        return tot


@dataclass(frozen=True)
class SektionsProfil:
    """Rekonstruerade tvärsnittsparametrar vid längdmätning `s_m` längs gatan."""

    s_m: float
    nock_z_rh2000: float
    tvarfall_vanster: float
    tvarfall_hoger: float
    kantsten_vanster_m: float
    kantsten_hoger_m: float
    gangbana_lutning_vanster: float
    gangbana_lutning_hoger: float
    osakerhet_z_m: float
    antal_punkter: int
    ar_typvarde: bool

    def rannsten_vanster_rh2000(self, korbana_halvbredd_m: float) -> float:
        """Höjd i vänster rännsten vid `d = -korbana_halvbredd_m` (RH 2000, m)."""
        return self.nock_z_rh2000 - self.tvarfall_vanster * korbana_halvbredd_m

    def rannsten_hoger_rh2000(self, korbana_halvbredd_m: float) -> float:
        """Höjd i höger rännsten vid `d = +korbana_halvbredd_m` (RH 2000, m)."""
        return self.nock_z_rh2000 - self.tvarfall_hoger * korbana_halvbredd_m


@dataclass(frozen=True)
class RannstensBrunn:
    """Rännstensbrunn placerad i en lokal lågpunkt längs vänster eller höger rännsten."""

    s_m: float
    sida: str
    e_m: float
    n_m: float
    botten_rh2000: float

    def __post_init__(self) -> None:
        kanonisk = _normalisera_sida(self.sida, tillat_bada=False)
        object.__setattr__(self, "sida", kanonisk)


@dataclass(frozen=True)
class GatuProfilResultat:
    """Resultat från komplett parametrisk gaturekonstruktion (T01b)."""

    korridor: GatuKorridor
    sektioner: list[SektionsProfil]
    brunnar: list[RannstensBrunn]
    kantstens_brytlinjer: list[Brytlinje]


@dataclass(frozen=True)
class _MittlinjeGeometri:
    """Förberäknade segment för snabb projicering och koordinatutvärdering längs mittlinje."""

    start_e: NDArray[np.float64]
    start_n: NDArray[np.float64]
    tang_e: NDArray[np.float64]
    tang_n: NDArray[np.float64]
    hoger_e: NDArray[np.float64]
    hoger_n: NDArray[np.float64]
    seg_langd: NDArray[np.float64]
    kum_s: NDArray[np.float64]
    total_langd: float


def _bygg_mittlinje_geometri(mittlinje: Sequence[tuple[float, float]]) -> _MittlinjeGeometri:
    se_list: list[float] = []
    sn_list: list[float] = []
    te_list: list[float] = []
    tn_list: list[float] = []
    he_list: list[float] = []
    hn_list: list[float] = []
    l_list: list[float] = []
    kum_list: list[float] = [0.0]

    ack_s = 0.0
    for idx in range(len(mittlinje) - 1):
        e0, n0 = mittlinje[idx]
        e1, n1 = mittlinje[idx + 1]
        de = e1 - e0
        dn = n1 - n0
        seg_l = math.hypot(de, dn)
        if seg_l <= 1e-12:
            continue
        te = de / seg_l
        tn = dn / seg_l
        # Högernormal (d > 0) då man ser framåt längs ökande s: (tn, -te)
        he = tn
        hn = -te
        se_list.append(e0)
        sn_list.append(n0)
        te_list.append(te)
        tn_list.append(tn)
        he_list.append(he)
        hn_list.append(hn)
        l_list.append(seg_l)
        ack_s += seg_l
        kum_list.append(ack_s)

    if not l_list:
        raise GatuProfilFel("Mittlinjen saknar segment med positiv längd.")

    return _MittlinjeGeometri(
        start_e=np.asarray(se_list, dtype=np.float64),
        start_n=np.asarray(sn_list, dtype=np.float64),
        tang_e=np.asarray(te_list, dtype=np.float64),
        tang_n=np.asarray(tn_list, dtype=np.float64),
        hoger_e=np.asarray(he_list, dtype=np.float64),
        hoger_n=np.asarray(hn_list, dtype=np.float64),
        seg_langd=np.asarray(l_list, dtype=np.float64),
        kum_s=np.asarray(kum_list, dtype=np.float64),
        total_langd=ack_s,
    )


def _koordinat_vid_sd(
    geom: _MittlinjeGeometri,
    s_m: float,
    d_m: float,
) -> tuple[float, float]:
    """Returnerar `(e_m, n_m)` i EPSG:3011 vid längdmätning `s_m` och tvärkoordinat `d_m`."""
    s_clamped = min(max(float(s_m), 0.0), geom.total_langd)
    n_seg = int(geom.seg_langd.size)
    seg_idx = int(np.searchsorted(geom.kum_s, s_clamped, side="right")) - 1
    seg_idx = max(0, min(seg_idx, n_seg - 1))

    u = s_clamped - float(geom.kum_s[seg_idx])
    ce = float(geom.start_e[seg_idx]) + u * float(geom.tang_e[seg_idx])
    cn = float(geom.start_n[seg_idx]) + u * float(geom.tang_n[seg_idx])

    # Vid inre brytpunkt: bisektrisnormal för sammanhängande kantstenslinje
    he = float(geom.hoger_e[seg_idx])
    hn = float(geom.hoger_n[seg_idx])
    if seg_idx > 0 and u <= 1e-9:
        avg_e = 0.5 * (float(geom.hoger_e[seg_idx - 1]) + he)
        avg_n = 0.5 * (float(geom.hoger_n[seg_idx - 1]) + hn)
        norm = math.hypot(avg_e, avg_n)
        if norm > 1e-9:
            he, hn = avg_e / norm, avg_n / norm
    elif seg_idx < n_seg - 1 and abs(u - float(geom.seg_langd[seg_idx])) <= 1e-9:
        avg_e = 0.5 * (he + float(geom.hoger_e[seg_idx + 1]))
        avg_n = 0.5 * (hn + float(geom.hoger_n[seg_idx + 1]))
        norm = math.hypot(avg_e, avg_n)
        if norm > 1e-9:
            he, hn = avg_e / norm, avg_n / norm

    return ce + d_m * he, cn + d_m * hn


def _projicera_pa_mittlinje(
    geom: _MittlinjeGeometri,
    e_arr: NDArray[np.float64],
    n_arr: NDArray[np.float64],
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.bool_]]:
    """Projicerar `(e_arr, n_arr)` till `(s_m, d_m, inom_langd)` längs mittlinjen.

    `d_m` är vinkelrätt avstånd mot mittlinjen (negativt = vänster, positivt = höger).
    `inom_langd` är True för punkter som projicerar inom `[0, total_langd]` utan att
    ligga bortom gatans start- eller slutsnitt.
    """
    form = e_arr.shape
    ef = e_arr.ravel()
    nf = n_arr.ravel()
    n_pts = int(ef.size)
    if n_pts == 0:
        return (
            np.empty(form, dtype=np.float64),
            np.empty(form, dtype=np.float64),
            np.empty(form, dtype=np.bool_),
        )

    basta_dist2 = np.full(n_pts, math.inf, dtype=np.float64)
    basta_s = np.zeros(n_pts, dtype=np.float64)
    basta_d = np.zeros(n_pts, dtype=np.float64)
    basta_inom = np.zeros(n_pts, dtype=np.bool_)

    n_seg = int(geom.seg_langd.size)
    for k in range(n_seg):
        de = ef - float(geom.start_e[k])
        dn = nf - float(geom.start_n[k])
        te = float(geom.tang_e[k])
        tn = float(geom.tang_n[k])
        he = float(geom.hoger_e[k])
        hn = float(geom.hoger_n[k])
        seg_l = float(geom.seg_langd[k])
        s0 = float(geom.kum_s[k])

        u_raw = de * te + dn * tn
        u_c = np.clip(u_raw, 0.0, seg_l)
        qe = float(geom.start_e[k]) + u_c * te
        qn = float(geom.start_n[k]) + u_c * tn
        re = ef - qe
        rn = nf - qn
        dist2 = re * re + rn * rn

        battre = dist2 < basta_dist2
        if not np.any(battre):
            continue

        # Tecken på d från högernormalen, magnitud = exakt euklidiskt avstånd
        proj_hoger = re * he + rn * hn
        tecken = np.where(proj_hoger >= 0.0, 1.0, -1.0)
        d_k = tecken * np.sqrt(dist2)
        s_k = s0 + u_c

        # Utanför start- eller slutsnitt räknas punkten inte som inom gatukorridoren
        inom_k = np.ones(n_pts, dtype=np.bool_)
        if k == 0:
            inom_k &= u_raw >= -1e-9
        if k == n_seg - 1:
            inom_k &= u_raw <= seg_l + 1e-9

        basta_dist2 = np.where(battre, dist2, basta_dist2)
        basta_s = np.where(battre, s_k, basta_s)
        basta_d = np.where(battre, d_k, basta_d)
        basta_inom = np.where(battre, inom_k, basta_inom)

    return (
        np.asarray(basta_s.reshape(form), dtype=np.float64),
        np.asarray(basta_d.reshape(form), dtype=np.float64),
        np.asarray(basta_inom.reshape(form), dtype=np.bool_),
    )


def utvardera_sektionsprofil(
    profil: SektionsProfil,
    d_m: Sequence[float] | NDArray[np.float64],
    korbana_halvbredd_m: float,
) -> NDArray[np.float64]:
    """Utvärderar den styckvis linjära gatuprofilens höjd (RH 2000, m) vid tvärkoordinater `d_m`."""
    d_arr = np.asarray(d_m, dtype=np.float64)
    w_k = float(korbana_halvbredd_m)
    z0 = profil.nock_z_rh2000
    z_rv = z0 - profil.tvarfall_vanster * w_k
    z_rh = z0 - profil.tvarfall_hoger * w_k

    z_korbana = np.where(
        d_arr < 0.0,
        z0 + profil.tvarfall_vanster * d_arr,
        z0 - profil.tvarfall_hoger * d_arr,
    )
    z_gv = z_rv + profil.kantsten_vanster_m + profil.gangbana_lutning_vanster * (-d_arr - w_k)
    z_gh = z_rh + profil.kantsten_hoger_m + profil.gangbana_lutning_hoger * (d_arr - w_k)

    z_ut = np.where(d_arr < -w_k, z_gv, np.where(d_arr > w_k, z_gh, z_korbana))
    return np.asarray(z_ut, dtype=np.float64)


def _designmatris_och_init(
    d_fit: NDArray[np.float64],
    z_fit: NDArray[np.float64],
    *,
    w_k: float,
    bombering: bool,
    gv_ok: bool,
    gh_ok: bool,
) -> tuple[NDArray[np.float64], NDArray[np.float64], list[str]]:
    """Bygger linjär designmatris `A` och outlier-robust startgissning `theta0`."""
    n = int(d_fit.size)
    ar_kv = (d_fit >= -w_k) & (d_fit < 0.0)
    ar_kh = (d_fit >= 0.0) & (d_fit <= w_k)
    ar_gv = d_fit < -w_k
    ar_gh = d_fit > w_k

    kol_namn: list[str] = ["nock"]
    kolumner: list[NDArray[np.float64]] = [np.ones(n, dtype=np.float64)]

    if bombering:
        kol_namn.extend(["m_v", "m_h"])
        col_mv = np.where(ar_kv, d_fit, np.where(ar_gv, -w_k, 0.0))
        col_mh = np.where(ar_kh, -d_fit, np.where(ar_gh, -w_k, 0.0))
        kolumner.extend([col_mv, col_mh])
    else:
        kol_namn.append("m_plan")
        # Enkelsidigt tvärfall: z(d) = nock - m_plan * d på körbanan; vid kanter +-w_k
        col_m = np.where(ar_gv, w_k, np.where(ar_gh, -w_k, -d_fit))
        kolumner.append(col_m)

    if gv_ok:
        kol_namn.extend(["h_v", "g_v"])
        kolumner.append(np.where(ar_gv, 1.0, 0.0))
        kolumner.append(np.where(ar_gv, -d_fit - w_k, 0.0))

    if gh_ok:
        kol_namn.extend(["h_h", "g_h"])
        kolumner.append(np.where(ar_gh, 1.0, 0.0))
        kolumner.append(np.where(ar_gh, d_fit - w_k, 0.0))

    a_mat = np.column_stack(kolumner)

    # Outlier-robust startgissning från zonmedianer (tål upp till ~50 % parkerade bilar)
    ar_korbana = ar_kv | ar_kh
    nara_mitt = ar_korbana & (np.abs(d_fit) <= 0.5 * w_k)
    z0_init = (
        float(np.median(z_fit[nara_mitt]))
        if np.any(nara_mitt)
        else float(np.median(z_fit[ar_korbana])) + TYP_TVARFALL * 0.5 * w_k
    )

    theta0_lista: list[float] = [z0_init]
    if bombering:
        theta0_lista.extend([TYP_TVARFALL, TYP_TVARFALL])
        z_rv_init = z0_init - TYP_TVARFALL * w_k
        z_rh_init = z0_init - TYP_TVARFALL * w_k
    else:
        theta0_lista.append(0.0)
        z_rv_init = z0_init
        z_rh_init = z0_init

    if gv_ok:
        d_gv = -d_fit[ar_gv] - w_k
        h_v_init = float(np.median(z_fit[ar_gv] - TYP_GANGBANA_LUTNING * d_gv)) - z_rv_init
        theta0_lista.extend([min(max(h_v_init, 0.0), 0.50), TYP_GANGBANA_LUTNING])

    if gh_ok:
        d_gh = d_fit[ar_gh] - w_k
        h_h_init = float(np.median(z_fit[ar_gh] - TYP_GANGBANA_LUTNING * d_gh)) - z_rh_init
        theta0_lista.extend([min(max(h_h_init, 0.0), 0.50), TYP_GANGBANA_LUTNING])

    return np.asarray(a_mat, dtype=np.float64), np.asarray(theta0_lista, dtype=np.float64), kol_namn


def passa_tvarsnitt_huber(
    d_m: Sequence[float] | NDArray[np.float64],
    z_m: Sequence[float] | NDArray[np.float64],
    *,
    s_m: float = 0.0,
    korbana_halvbredd_m: float = 3.25,
    gangbana_bredd_m: float = 2.75,
    bombering: bool = True,
    huber_delta_m: float = 0.015,
    max_iter: int = 50,
    tol: float = 1e-7,
    min_punkter_korbana_sida: int = 2,
    min_punkter_gangbana: int = 2,
    fallback_nock_z_rh2000: float = 0.0,
) -> SektionsProfil:
    """Passar en styckvis linjär gatuprofil i en tvärsektion med Huber IRLS.

    Modellen består av körbana (bomberad kring `d = 0` eller enkelsidigt tvärfall),
    höjdsprång vid kantstenarna (`|d| = korbana_halvbredd_m`) samt gångbanelutning
    för `|d| > korbana_halvbredd_m`.

    Om för få markpunkter finns på körbana eller gångbana faller funktionen tillbaka
    till Stockholms typvärden (`kantsten = 0,12 m`, `tvärfall = 0,025`,
    `gångbanelutning = 0,02`) och markerar `ar_typvarde = True` med höjd osäkerhet.
    """
    w_k = float(korbana_halvbredd_m)
    w_g = float(gangbana_bredd_m)
    delta = float(huber_delta_m)
    if not math.isfinite(w_k) or w_k <= 0.0:
        raise GatuProfilFel(f"korbana_halvbredd_m måste vara > 0, fick {w_k}.")
    if not math.isfinite(w_g) or w_g < 0.0:
        raise GatuProfilFel(f"gangbana_bredd_m måste vara >= 0, fick {w_g}.")
    if not math.isfinite(delta) or delta <= 0.0:
        raise GatuProfilFel(f"huber_delta_m måste vara > 0, fick {delta}.")

    d_in = np.asarray(d_m, dtype=np.float64).ravel()
    z_in = np.asarray(z_m, dtype=np.float64).ravel()
    if d_in.shape != z_in.shape:
        raise GatuProfilFel(f"d_m {d_in.shape} och z_m {z_in.shape} måste ha samma längd.")

    tot_w = w_k + w_g
    giltig = np.isfinite(d_in) & np.isfinite(z_in) & (np.abs(d_in) <= tot_w + 1e-9)
    d = d_in[giltig]
    z = z_in[giltig]
    n_pts = int(d.size)

    def _typ_profil(nock_z: float, antal: int) -> SektionsProfil:
        tv_v = TYP_TVARFALL if bombering else -TYP_TVARFALL
        return SektionsProfil(
            s_m=float(s_m),
            nock_z_rh2000=float(nock_z),
            tvarfall_vanster=tv_v,
            tvarfall_hoger=TYP_TVARFALL,
            kantsten_vanster_m=TYP_KANTSTEN_HOJD_M,
            kantsten_hoger_m=TYP_KANTSTEN_HOJD_M,
            gangbana_lutning_vanster=TYP_GANGBANA_LUTNING,
            gangbana_lutning_hoger=TYP_GANGBANA_LUTNING,
            osakerhet_z_m=TYP_OSAKERHET_Z_M,
            antal_punkter=antal,
            ar_typvarde=True,
        )

    if n_pts == 0:
        return _typ_profil(fallback_nock_z_rh2000, 0)

    mask_kv = (d >= -w_k) & (d < 0.0)
    mask_kh = (d >= 0.0) & (d <= w_k)
    mask_k = mask_kv | mask_kh
    mask_gv = d < -w_k
    mask_gh = d > w_k

    def _har_spridning(m: NDArray[np.bool_], min_antal: int, min_span_m: float) -> bool:
        cnt = int(np.count_nonzero(m))
        if cnt < min_antal:
            return False
        vals = d[m]
        return bool(float(np.max(vals)) - float(np.min(vals)) >= min_span_m)

    korbana_ok = (
        _har_spridning(mask_kv, min_punkter_korbana_sida, 0.15)
        and _har_spridning(mask_kh, min_punkter_korbana_sida, 0.15)
        if bombering
        else _har_spridning(mask_k, 2 * min_punkter_korbana_sida, 0.30)
    )

    if not korbana_ok:
        # Förankra nockhöjden robust mot befintliga punkter med hjälp av typprofilen
        ref_typ = _typ_profil(0.0, n_pts)
        z_offset = utvardera_sektionsprofil(ref_typ, d, w_k)
        nock_est = float(np.median(z - z_offset))
        return _typ_profil(nock_est, n_pts)

    gv_ok = w_g > 0.0 and _har_spridning(mask_gv, min_punkter_gangbana, 0.10)
    gh_ok = w_g > 0.0 and _har_spridning(mask_gh, min_punkter_gangbana, 0.10)
    ar_typvarde = (w_g > 0.0) and (not gv_ok or not gh_ok)

    # Ta med enbart de zoner som har tillräckligt data i IRLS-passningen
    fit_mask = mask_k.copy()
    if gv_ok:
        fit_mask |= mask_gv
    if gh_ok:
        fit_mask |= mask_gh

    d_fit = d[fit_mask]
    z_fit = z[fit_mask]

    a_mat, theta, kol_namn = _designmatris_och_init(
        d_fit,
        z_fit,
        w_k=w_k,
        bombering=bombering,
        gv_ok=gv_ok,
        gh_ok=gh_ok,
    )

    # Huber IRLS (globalt konvex målfunktion)
    for _ in range(max_iter):
        res = z_fit - a_mat @ theta
        abs_res = np.abs(res)
        vikter = np.where(abs_res <= delta, 1.0, delta / np.maximum(abs_res, 1e-12))
        rot_v = np.sqrt(vikter)
        a_vikt = a_mat * rot_v[:, np.newaxis]
        z_vikt = z_fit * rot_v
        lstsq_res = np.linalg.lstsq(a_vikt, z_vikt, rcond=None)
        theta_ny = np.asarray(lstsq_res[0], dtype=np.float64)
        diff = float(np.max(np.abs(theta_ny - theta)))
        theta = theta_ny
        if diff < tol:
            break

    param = {namn: float(val) for namn, val in zip(kol_namn, theta, strict=True)}
    nock_z = param["nock"]
    if bombering:
        tvarfall_v = param["m_v"]
        tvarfall_h = param["m_h"]
    else:
        tvarfall_h = param["m_plan"]
        tvarfall_v = -param["m_plan"]

    kantsten_v = max(0.0, param["h_v"]) if gv_ok else TYP_KANTSTEN_HOJD_M
    kantsten_h = max(0.0, param["h_h"]) if gh_ok else TYP_KANTSTEN_HOJD_M
    gangbana_v = param["g_v"] if gv_ok else TYP_GANGBANA_LUTNING
    gangbana_h = param["g_h"] if gh_ok else TYP_GANGBANA_LUTNING

    # Skatta robust höjdosäkerhet från slutliga residualer (MAD)
    slut_res = z_fit - a_mat @ theta
    med_res = float(np.median(slut_res))
    mad = float(np.median(np.abs(slut_res - med_res)))
    sigma_mad = 1.4826 * mad
    osakerhet = max(MIN_OSAKERHET_Z_M, sigma_mad)
    if ar_typvarde:
        osakerhet = max(osakerhet, TYP_OSAKERHET_Z_M)

    return SektionsProfil(
        s_m=float(s_m),
        nock_z_rh2000=nock_z,
        tvarfall_vanster=tvarfall_v,
        tvarfall_hoger=tvarfall_h,
        kantsten_vanster_m=kantsten_v,
        kantsten_hoger_m=kantsten_h,
        gangbana_lutning_vanster=gangbana_v,
        gangbana_lutning_hoger=gangbana_h,
        osakerhet_z_m=osakerhet,
        antal_punkter=n_pts,
        ar_typvarde=ar_typvarde,
    )


def utjamna_profil_bandad(
    y: Sequence[float] | NDArray[np.float64],
    vikter: Sequence[float] | NDArray[np.float64],
    jamnhet_lambda: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Löser det bandade (tridiagonala) utjämningsproblemet i exakt O(M) tid.

    Minimerar:
        sum_i w_i * (x_i - y_i)^2 + lambda * sum_{i=0}^{M-2} (x_{i+1} - x_i)^2

    Använder Thomas-algoritmen för exakt lösning `x` samt framåt-/bakåtsvep av
    Schur-komplement för exakt posterior standardavvikelse `sqrt((T^-1)_{ii})` i O(M) tid.

    Returnerar `(x_utjamnad, posterior_std)`.
    """
    y_arr = np.asarray(y, dtype=np.float64).ravel()
    w_arr = np.asarray(vikter, dtype=np.float64).ravel()
    lam = float(jamnhet_lambda)

    if y_arr.shape != w_arr.shape:
        raise GatuProfilFel(f"y {y_arr.shape} och vikter {w_arr.shape} måste ha samma längd.")
    if not math.isfinite(lam) or lam < 0.0:
        raise GatuProfilFel(f"jamnhet_lambda måste vara >= 0 och ändlig, fick {lam}.")
    if y_arr.size > 0 and (not np.all(np.isfinite(y_arr)) or not np.all(np.isfinite(w_arr))):
        raise GatuProfilFel("y och vikter får inte innehålla NaN eller Inf.")
    if np.any(w_arr < 0.0):
        raise GatuProfilFel("vikter får inte vara negativa.")

    m = int(y_arr.size)
    if m == 0:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)

    if m == 1:
        std0 = 1.0 / math.sqrt(float(w_arr[0])) if float(w_arr[0]) > 0.0 else math.inf
        return y_arr.copy(), np.asarray([std0], dtype=np.float64)

    if float(np.sum(w_arr)) == 0.0:
        return y_arr.copy(), np.full(m, math.inf, dtype=np.float64)

    if lam == 0.0:
        std_arr = np.where(w_arr > 0.0, 1.0 / np.sqrt(np.maximum(w_arr, 1e-300)), math.inf)
        return y_arr.copy(), np.asarray(std_arr, dtype=np.float64)

    # Bygg huvuddiagonal d_i för det symmetriska tridiagonala systemet T x = w * y
    diag = np.empty(m, dtype=np.float64)
    diag[0] = float(w_arr[0]) + lam
    if m > 2:
        diag[1 : m - 1] = w_arr[1 : m - 1] + 2.0 * lam
    diag[m - 1] = float(w_arr[m - 1]) + lam

    rhs = w_arr * y_arr
    lam2 = lam * lam

    # Framåtsvep (Thomas-algoritmen) + framåt-pivoter p_i för exakt diag(T^-1)
    p_fram = np.empty(m, dtype=np.float64)
    c_prim = np.empty(m - 1, dtype=np.float64)
    b_prim = np.empty(m, dtype=np.float64)

    p0 = float(diag[0])
    p_fram[0] = p0
    c_prim[0] = -lam / p0
    b_prim[0] = float(rhs[0]) / p0

    for i in range(1, m):
        p_i = float(diag[i]) + lam * float(c_prim[i - 1])
        p_fram[i] = p_i
        if i < m - 1:
            c_prim[i] = -lam / p_i
        b_prim[i] = (float(rhs[i]) + lam * float(b_prim[i - 1])) / p_i

    # Bakåtsubstitution för x
    x_ut = np.empty(m, dtype=np.float64)
    x_ut[m - 1] = float(b_prim[m - 1])
    for i in range(m - 2, -1, -1):
        x_ut[i] = float(b_prim[i]) - float(c_prim[i]) * float(x_ut[i + 1])

    # Bakåt-pivoter q_i -> exakt (T^-1)_{ii} = 1 / (p_i + q_i - diag_i) i O(M)
    q_bak = np.empty(m, dtype=np.float64)
    q_bak[m - 1] = float(diag[m - 1])
    for i in range(m - 2, -1, -1):
        q_bak[i] = float(diag[i]) - lam2 / float(q_bak[i + 1])

    schur = p_fram + q_bak - diag
    posterior_std = np.sqrt(1.0 / np.maximum(schur, 1e-300))

    return np.asarray(x_ut, dtype=np.float64), np.asarray(posterior_std, dtype=np.float64)


def _utjamna_med_linjar_trend(
    s_arr: NDArray[np.float64],
    y_arr: NDArray[np.float64],
    w_arr: NDArray[np.float64],
    jamnhet_lambda: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Utjämnar längdprofil (t.ex. nockhöjd) runt viktad linjär trend utan ändeffekt."""
    aktiva = w_arr > 0.0
    n_aktiva = int(np.count_nonzero(aktiva))
    s_akt = s_arr[aktiva]
    if n_aktiva < 2 or (float(np.max(s_akt)) - float(np.min(s_akt))) <= 1e-9:
        return utjamna_profil_bandad(y_arr, w_arr, jamnhet_lambda)

    s_a = s_akt
    y_a = y_arr[aktiva]
    w_a = w_arr[aktiva]
    w_sum = float(np.sum(w_a))
    s_medel = float(np.sum(w_a * s_a)) / w_sum
    y_medel = float(np.sum(w_a * y_a)) / w_sum
    ds = s_a - s_medel
    namnare = float(np.sum(w_a * ds * ds))
    lutning = float(np.sum(w_a * ds * (y_a - y_medel))) / namnare if namnare > 1e-12 else 0.0

    trend = y_medel + lutning * (s_arr - s_medel)
    res_ut, post_std = utjamna_profil_bandad(y_arr - trend, w_arr, jamnhet_lambda)
    return np.asarray(trend + res_ut, dtype=np.float64), post_std


def hitta_rannstens_lagpunkter(
    korridor: GatuKorridor,
    sektioner: Sequence[SektionsProfil],
    *,
    min_djup_m: float = 0.005,
    min_avstand_m: float = 5.0,
) -> list[RannstensBrunn]:
    """Identifierar lokala lågpunkter längs vänster och höger rännsten (T01b steg 8).

    Används när ledningsdata för rännstensbrunnar saknas.
    """
    m = len(sektioner)
    if m < 3:
        return []

    geom = _bygg_mittlinje_geometri(korridor.mittlinje)
    w_k = korridor.korbana_halvbredd_m
    brunnar: list[RannstensBrunn] = []

    for sida, d_sida in (("vanster", -w_k), ("hoger", w_k)):
        z_vals = (
            [s.rannsten_vanster_rh2000(w_k) for s in sektioner]
            if sida == "vanster"
            else [s.rannsten_hoger_rh2000(w_k) for s in sektioner]
        )

        kandidater: list[tuple[float, float]] = []  # (s_m, botten_z)
        idx = 0
        while idx < m:
            slut = idx
            while slut + 1 < m and abs(z_vals[slut + 1] - z_vals[idx]) <= 1e-9:
                slut += 1

            # Inre platå [idx, slut] är ett lokalt minimum om båda sidor stiger
            if (
                idx > 0
                and slut < m - 1
                and z_vals[idx - 1] > z_vals[idx]
                and z_vals[slut + 1] > z_vals[slut]
            ):
                botten = z_vals[idx]
                topp_v = botten
                for k in range(idx - 1, -1, -1):
                    if z_vals[k] < botten:
                        break
                    if z_vals[k] > topp_v:
                        topp_v = z_vals[k]

                topp_h = botten
                for k in range(slut + 1, m):
                    if z_vals[k] < botten:
                        break
                    if z_vals[k] > topp_h:
                        topp_h = z_vals[k]

                djup = min(topp_v, topp_h) - botten
                if djup >= min_djup_m:
                    s_mitt = 0.5 * (sektioner[idx].s_m + sektioner[slut].s_m)
                    kandidater.append((s_mitt, botten))

            idx = slut + 1

        # Filtrera för tätt liggande lågpunkter (behåll djupaste inom `min_avstand_m`)
        kandidater.sort(key=lambda item: (item[1], item[0]))
        valda: list[tuple[float, float]] = []
        for s_kand, z_kand in kandidater:
            if all(abs(s_kand - s_bef) >= min_avstand_m for s_bef, _ in valda):
                valda.append((s_kand, z_kand))

        for s_vald, z_vald in valda:
            e_m, n_m = _koordinat_vid_sd(geom, s_vald, d_sida)
            brunnar.append(
                RannstensBrunn(
                    s_m=s_vald,
                    sida=sida,
                    e_m=e_m,
                    n_m=n_m,
                    botten_rh2000=z_vald,
                )
            )

    brunnar.sort(key=lambda b: (b.s_m, b.sida))
    return brunnar


def _bygg_kantstens_brytlinjer(
    korridor: GatuKorridor,
    sektioner: Sequence[SektionsProfil],
    normal_kantsten_v: NDArray[np.float64],
    normal_kantsten_h: NDArray[np.float64],
) -> list[Brytlinje]:
    """Genererar `Brytlinje`-segment längs vänster och höger kantsten (inkl. infarter)."""
    if not sektioner:
        return []

    geom = _bygg_mittlinje_geometri(korridor.mittlinje)
    tot_l = geom.total_langd
    w_k = korridor.korbana_halvbredd_m
    s_sek = np.asarray([s.s_m for s in sektioner], dtype=np.float64)

    linjer: list[Brytlinje] = []
    for sida, d_sida, norm_h in (
        ("vanster", -w_k, normal_kantsten_v),
        ("hoger", w_k, normal_kantsten_h),
    ):
        sid_infarter = [inf for inf in korridor.infarter if inf.galler_sida(sida)]
        bryt_s: set[float] = {0.0, tot_l}
        for inf in sid_infarter:
            s0 = min(max(inf.s_start_m, 0.0), tot_l)
            s1 = min(max(inf.s_slut_m, 0.0), tot_l)
            if s1 > s0:
                bryt_s.add(s0)
                bryt_s.add(s1)

        sorterade_s = sorted(bryt_s)
        for idx in range(len(sorterade_s) - 1):
            sa = sorterade_s[idx]
            sb = sorterade_s[idx + 1]
            if sb - sa <= 1e-6:
                continue
            s_mitt = 0.5 * (sa + sb)

            # Kontrollera om intervallet ligger i en infartssänkning
            aktiva_inf = [
                inf.kantsten_hojd_m
                for inf in sid_infarter
                if inf.s_start_m <= s_mitt <= inf.s_slut_m
            ]
            if aktiva_inf:
                hojd_m = min(aktiva_inf)
            else:
                i_mask = (s_sek >= sa - 1e-9) & (s_sek <= sb + 1e-9)
                hojd_raw = (
                    float(np.median(norm_h[i_mask]))
                    if np.any(i_mask)
                    else float(np.interp(s_mitt, s_sek, norm_h))
                )
                hojd_m = max(0.0, hojd_raw)

            # Samla segmentets hörnpunkter (inkl. ev. inre brytpunkter på mittlinjen)
            prov_s = [sa]
            for v_s in geom.kum_s[1:-1]:
                vf = float(v_s)
                if sa + 1e-6 < vf < sb - 1e-6:
                    prov_s.append(vf)
            prov_s.append(sb)

            punkter = tuple(_koordinat_vid_sd(geom, ps, d_sida) for ps in prov_s)
            linjer.append(Brytlinje(typ="kantsten", hojd_m=hojd_m, punkter=punkter))

    return linjer


def brytlinjer_till_poster(linjer: Sequence[Brytlinje]) -> list[BrytlinjePost]:
    """Konverterar `Brytlinje`-objekt till K1/T02 `BrytlinjePost`."""
    return [
        BrytlinjePost(typ=b.typ, hojd_m=b.hojd_m, koordinater=b.punkter) for b in linjer
    ]


def rekonstruera_gatuprofil(
    korridor: GatuKorridor,
    punkter_e: Sequence[float] | NDArray[np.float64],
    punkter_n: Sequence[float] | NDArray[np.float64],
    punkter_z: Sequence[float] | NDArray[np.float64],
    *,
    sektionsavstand_m: float = 1.5,
    skivbredd_m: float | None = None,
    huber_delta_m: float = 0.015,
    jamnhet_nock_lambda: float = 0.5,
    jamnhet_form_lambda: float = 5.0,
    fallback_nock_z_rh2000: float = 0.0,
    min_punkter_korbana_sida: int = 2,
    min_punkter_gangbana: int = 2,
) -> GatuProfilResultat:
    """Rekonstruerar komplett parametrisk gatuprofil längs en gatukorridor.

    Steg:
    1. Delar upp mittlinjen i tvärsektioner med avstånd ~`sektionsavstand_m` (1–2 m).
    2. Passar varje sektion med Huber IRLS (`passa_tvarsnitt_huber`).
    3. Utjämnar profilparametrarna längs gatan med bandad O(M) Thomas-utjämnare
       (`utjamna_profil_bandad`) samtidigt som skarpa infartssänkningar bevaras.
    4. Identifierar rännstensbrunnar i lokala lågpunkter och skapar kantstensbrytlinjer.
    """
    if not math.isfinite(sektionsavstand_m) or sektionsavstand_m <= 0.0:
        raise GatuProfilFel(f"sektionsavstand_m måste vara > 0, fick {sektionsavstand_m}.")
    skiva = max(1.0, sektionsavstand_m) if skivbredd_m is None else float(skivbredd_m)
    if not math.isfinite(skiva) or skiva <= 0.0:
        raise GatuProfilFel(f"skivbredd_m måste vara > 0, fick {skiva}.")

    pe = np.asarray(punkter_e, dtype=np.float64).ravel()
    pn = np.asarray(punkter_n, dtype=np.float64).ravel()
    pz = np.asarray(punkter_z, dtype=np.float64).ravel()
    if not (pe.shape == pn.shape == pz.shape):
        raise GatuProfilFel(
            f"Punktmolnsvektorerna måste ha samma längd: E={pe.shape}, N={pn.shape}, Z={pz.shape}."
        )

    geom = _bygg_mittlinje_geometri(korridor.mittlinje)
    tot_l = geom.total_langd
    n_intervall = max(1, round(tot_l / sektionsavstand_m))
    s_stationer = np.linspace(0.0, tot_l, n_intervall + 1, dtype=np.float64)
    m = int(s_stationer.size)

    # Filtrera och projicera laserpunkter till korridorens (s, d)-system
    giltiga = np.isfinite(pe) & np.isfinite(pn) & np.isfinite(pz)
    pe_g, pn_g, pz_g = pe[giltiga], pn[giltiga], pz[giltiga]

    halv_skiva = 0.5 * skiva
    if pe_g.size > 0:
        p_s, p_d, p_inom = _projicera_pa_mittlinje(geom, pe_g, pn_g)
        korr_mask = p_inom & (np.abs(p_d) <= korridor.total_halvbredd_m + 1e-9)
        s_korr = p_s[korr_mask]
        d_korr = p_d[korr_mask]
        z_korr = pz_g[korr_mask]
    else:
        s_korr = np.empty(0, dtype=np.float64)
        d_korr = np.empty(0, dtype=np.float64)
        z_korr = np.empty(0, dtype=np.float64)

    # Sortera på s för snabb O(log N) sektionsskivning
    if s_korr.size > 0:
        ordn = np.argsort(s_korr)
        s_korr = s_korr[ordn]
        d_korr = d_korr[ordn]
        z_korr = z_korr[ordn]

    raa_sektioner: list[SektionsProfil] = []
    for s_val in s_stationer:
        sf = float(s_val)
        i0 = int(np.searchsorted(s_korr, sf - halv_skiva, side="left"))
        i1 = int(np.searchsorted(s_korr, sf + halv_skiva, side="right"))
        sek = passa_tvarsnitt_huber(
            d_korr[i0:i1],
            z_korr[i0:i1],
            s_m=sf,
            korbana_halvbredd_m=korridor.korbana_halvbredd_m,
            gangbana_bredd_m=korridor.gangbana_bredd_m,
            bombering=korridor.bombering,
            huber_delta_m=huber_delta_m,
            min_punkter_korbana_sida=min_punkter_korbana_sida,
            min_punkter_gangbana=min_punkter_gangbana,
            fallback_nock_z_rh2000=fallback_nock_z_rh2000,
        )
        raa_sektioner.append(sek)

    # Samla parametervektorer och observationsvikter för längsgående utjämning
    nock_y = np.asarray([s.nock_z_rh2000 for s in raa_sektioner], dtype=np.float64)
    tv_v_y = np.asarray([s.tvarfall_vanster for s in raa_sektioner], dtype=np.float64)
    tv_h_y = np.asarray([s.tvarfall_hoger for s in raa_sektioner], dtype=np.float64)
    kv_y = np.asarray([s.kantsten_vanster_m for s in raa_sektioner], dtype=np.float64)
    kh_y = np.asarray([s.kantsten_hoger_m for s in raa_sektioner], dtype=np.float64)
    gv_y = np.asarray([s.gangbana_lutning_vanster for s in raa_sektioner], dtype=np.float64)
    gh_y = np.asarray([s.gangbana_lutning_hoger for s in raa_sektioner], dtype=np.float64)
    osak_raa = np.asarray([s.osakerhet_z_m for s in raa_sektioner], dtype=np.float64)

    w_nock = np.zeros(m, dtype=np.float64)
    w_form = np.zeros(m, dtype=np.float64)
    for i, sek in enumerate(raa_sektioner):
        inv_var = 1.0 / (sek.osakerhet_z_m * sek.osakerhet_z_m)
        if not sek.ar_typvarde:
            w_nock[i] = inv_var
            w_form[i] = inv_var
        elif sek.antal_punkter > 0:
            w_nock[i] = 0.25 * inv_var
            w_form[i] = 0.05 * inv_var
        else:
            w_nock[i] = 0.0
            w_form[i] = 0.0

    # Nedvikta kantstensobservationer inom infartssänkningar under utjämning av normal kantsten
    w_kv = w_form.copy()
    w_kh = w_form.copy()
    for i, s_val in enumerate(s_stationer):
        sf = float(s_val)
        for inf in korridor.infarter:
            if inf.s_start_m <= sf <= inf.s_slut_m:
                if inf.galler_sida("vanster"):
                    w_kv[i] = 0.0
                if inf.galler_sida("hoger"):
                    w_kh[i] = 0.0

    positiva_w = w_nock[w_nock > 0.0]
    skala_w = float(np.median(positiva_w)) if positiva_w.size > 0 else 1.0
    lam_nock_eff = max(0.0, float(jamnhet_nock_lambda)) * skala_w
    lam_form_eff = max(0.0, float(jamnhet_form_lambda)) * skala_w

    nock_ut, nock_std = _utjamna_med_linjar_trend(s_stationer, nock_y, w_nock, lam_nock_eff)
    tv_v_ut, _ = utjamna_profil_bandad(tv_v_y, w_form, lam_form_eff)
    tv_h_ut, _ = utjamna_profil_bandad(tv_h_y, w_form, lam_form_eff)
    kv_norm_raw, _ = utjamna_profil_bandad(kv_y, w_kv, lam_form_eff)
    kh_norm_raw, _ = utjamna_profil_bandad(kh_y, w_kh, lam_form_eff)
    gv_ut, _ = utjamna_profil_bandad(gv_y, w_form, lam_form_eff)
    gh_ut, _ = utjamna_profil_bandad(gh_y, w_form, lam_form_eff)

    kv_norm = np.asarray(np.maximum(0.0, kv_norm_raw), dtype=np.float64)
    kh_norm = np.asarray(np.maximum(0.0, kh_norm_raw), dtype=np.float64)

    # Applicera skarpa infartssänkningar EFTER utjämningen så de aldrig smetas ut
    kv_slut = kv_norm.copy()
    kh_slut = kh_norm.copy()
    for i, s_val in enumerate(s_stationer):
        sf = float(s_val)
        for inf in korridor.infarter:
            if inf.s_start_m <= sf <= inf.s_slut_m:
                if inf.galler_sida("vanster"):
                    kv_slut[i] = min(float(kv_slut[i]), inf.kantsten_hojd_m)
                if inf.galler_sida("hoger"):
                    kh_slut[i] = min(float(kh_slut[i]), inf.kantsten_hojd_m)

    sektioner: list[SektionsProfil] = []
    for i, sek in enumerate(raa_sektioner):
        std_i = float(nock_std[i])
        if not math.isfinite(std_i):
            osak_i = float(osak_raa[i])
        elif sek.ar_typvarde:
            osak_i = max(std_i, TYP_OSAKERHET_Z_M if sek.antal_punkter == 0 else 0.05)
        else:
            osak_i = max(MIN_OSAKERHET_Z_M, std_i)

        sektioner.append(
            SektionsProfil(
                s_m=sek.s_m,
                nock_z_rh2000=float(nock_ut[i]),
                tvarfall_vanster=float(tv_v_ut[i]),
                tvarfall_hoger=float(tv_h_ut[i]),
                kantsten_vanster_m=float(kv_slut[i]),
                kantsten_hoger_m=float(kh_slut[i]),
                gangbana_lutning_vanster=float(gv_ut[i]),
                gangbana_lutning_hoger=float(gh_ut[i]),
                osakerhet_z_m=osak_i,
                antal_punkter=sek.antal_punkter,
                ar_typvarde=sek.ar_typvarde,
            )
        )

    brunnar = hitta_rannstens_lagpunkter(korridor, sektioner)
    kantstens_brytlinjer = _bygg_kantstens_brytlinjer(korridor, sektioner, kv_norm, kh_norm)

    return GatuProfilResultat(
        korridor=korridor,
        sektioner=sektioner,
        brunnar=brunnar,
        kantstens_brytlinjer=kantstens_brytlinjer,
    )


def _hamta_raster_geometri(
    geo: GeoTransformInfo | Ruta,
) -> tuple[int, int, float, float, float, float, NDArray[np.float64], NDArray[np.float64]]:
    if isinstance(geo, Ruta):
        rader, kolumner = geo.form_rc
        e_min, _, _, n_max = geo.yttre_bbox
        dx = float(geo.upplosning_m)
        dy = float(geo.upplosning_m)
        e_vek, n_vek = geo.cell_centra_en()
        return rader, kolumner, e_min, n_max, dx, dy, e_vek, n_vek

    if isinstance(geo, GeoTransformInfo):
        rader = int(geo.hojd)
        kolumner = int(geo.bredd)
        dx = float(geo.pixelstorlek_x)
        dy = float(geo.pixelstorlek_y)
        e_min = float(geo.x_min)
        n_max = float(geo.y_max)
        e_vek = e_min + (np.arange(kolumner, dtype=np.float64) + 0.5) * dx
        n_vek = n_max - (np.arange(rader, dtype=np.float64) + 0.5) * dy
        return rader, kolumner, e_min, n_max, dx, dy, e_vek, n_vek

    raise GatuProfilFel(f"Stöder endast GeoTransformInfo eller Ruta, fick {type(geo).__name__}.")


def rastrera_gatuprofil(
    mark_z: NDArray[np.float32],
    osakerhet_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
    geo: GeoTransformInfo | Ruta,
    resultat: GatuProfilResultat,
) -> tuple[NDArray[np.float32], NDArray[np.float32], NDArray[np.uint8]]:
    """Rastrerar en rekonstruerad gatuprofil in i `(mark_z, osakerhet_z, markslag)`.

    Endast celler inom gatukorridoren uppdateras (`markslag = HARDGJORT`); alla
    celler utanför korridoren lämnas helt oförändrade. Skarpa infartssänkningar
    appliceras direkt på cellernas exakta `s`-koordinat.
    """
    rader, kolumner, e_min, n_max, dx, dy, e_vek, n_vek = _hamta_raster_geometri(geo)
    forv_form = (rader, kolumner)
    if mark_z.shape != forv_form or osakerhet_z.shape != forv_form or markslag.shape != forv_form:
        raise GatuProfilFel(
            f"Rasterformer mark={mark_z.shape}, osakerhet={osakerhet_z.shape}, "
            f"markslag={markslag.shape} matchar inte geo {forv_form}."
        )

    ny_mark = np.array(mark_z, dtype=np.float32, copy=True)
    ny_osak = np.array(osakerhet_z, dtype=np.float32, copy=True)
    ny_slag = np.array(markslag, dtype=np.uint8, copy=True)

    if not resultat.sektioner:
        return ny_mark, ny_osak, ny_slag

    korridor = resultat.korridor
    geom = _bygg_mittlinje_geometri(korridor.mittlinje)
    tot_w = korridor.total_halvbredd_m
    w_k = korridor.korbana_halvbredd_m

    # Begränsa beräkningen till gatukorridorens bounding box i rastret
    e_punkter = [p[0] for p in korridor.mittlinje]
    n_punkter = [p[1] for p in korridor.mittlinje]
    marginal = tot_w + max(dx, dy)
    k_emin = min(e_punkter) - marginal
    k_emax = max(e_punkter) + marginal
    k_nmin = min(n_punkter) - marginal
    k_nmax = max(n_punkter) + marginal

    kol0 = max(0, math.floor((k_emin - e_min) / dx))
    kol1 = min(kolumner, math.ceil((k_emax - e_min) / dx))
    rad0 = max(0, math.floor((n_max - k_nmax) / dy))
    rad1 = min(rader, math.ceil((n_max - k_nmin) / dy))
    if kol1 <= kol0 or rad1 <= rad0:
        return ny_mark, ny_osak, ny_slag

    sub_e, sub_n = np.meshgrid(e_vek[kol0:kol1], n_vek[rad0:rad1])
    s_grid, d_grid, inom_langd = _projicera_pa_mittlinje(geom, sub_e, sub_n)
    inom_korr = inom_langd & (np.abs(d_grid) <= tot_w + 1e-9)
    if not np.any(inom_korr):
        return ny_mark, ny_osak, ny_slag

    s_aktiv = s_grid[inom_korr]
    d_aktiv = d_grid[inom_korr]

    sek = resultat.sektioner
    s_sek = np.asarray([p.s_m for p in sek], dtype=np.float64)
    nock_sek = np.asarray([p.nock_z_rh2000 for p in sek], dtype=np.float64)
    tv_v_sek = np.asarray([p.tvarfall_vanster for p in sek], dtype=np.float64)
    tv_h_sek = np.asarray([p.tvarfall_hoger for p in sek], dtype=np.float64)
    kv_sek = np.asarray([p.kantsten_vanster_m for p in sek], dtype=np.float64)
    kh_sek = np.asarray([p.kantsten_hoger_m for p in sek], dtype=np.float64)
    gv_sek = np.asarray([p.gangbana_lutning_vanster for p in sek], dtype=np.float64)
    gh_sek = np.asarray([p.gangbana_lutning_hoger for p in sek], dtype=np.float64)
    osak_sek = np.asarray([p.osakerhet_z_m for p in sek], dtype=np.float64)

    nock_a = np.asarray(np.interp(s_aktiv, s_sek, nock_sek), dtype=np.float64)
    tv_v_a = np.asarray(np.interp(s_aktiv, s_sek, tv_v_sek), dtype=np.float64)
    tv_h_a = np.asarray(np.interp(s_aktiv, s_sek, tv_h_sek), dtype=np.float64)
    gv_a = np.asarray(np.interp(s_aktiv, s_sek, gv_sek), dtype=np.float64)
    gh_a = np.asarray(np.interp(s_aktiv, s_sek, gh_sek), dtype=np.float64)
    osak_a = np.asarray(np.interp(s_aktiv, s_sek, osak_sek), dtype=np.float64)

    # Interpolera normal kantstenshöjd utanför infarter och applicera infarter skarpt
    ej_inf_v = np.ones(len(sek), dtype=np.bool_)
    ej_inf_h = np.ones(len(sek), dtype=np.bool_)
    for i, s_val in enumerate(s_sek):
        sf = float(s_val)
        for inf in korridor.infarter:
            if inf.s_start_m <= sf <= inf.s_slut_m:
                if inf.galler_sida("vanster"):
                    ej_inf_v[i] = False
                if inf.galler_sida("hoger"):
                    ej_inf_h[i] = False

    kv_a = np.asarray(
        np.interp(s_aktiv, s_sek[ej_inf_v], kv_sek[ej_inf_v])
        if np.any(ej_inf_v)
        else np.interp(s_aktiv, s_sek, kv_sek),
        dtype=np.float64,
    )
    kh_a = np.asarray(
        np.interp(s_aktiv, s_sek[ej_inf_h], kh_sek[ej_inf_h])
        if np.any(ej_inf_h)
        else np.interp(s_aktiv, s_sek, kh_sek),
        dtype=np.float64,
    )

    for inf in korridor.infarter:
        i_mask = (s_aktiv >= inf.s_start_m) & (s_aktiv <= inf.s_slut_m)
        if inf.galler_sida("vanster"):
            kv_a = np.asarray(
                np.where(i_mask, np.minimum(kv_a, inf.kantsten_hojd_m), kv_a),
                dtype=np.float64,
            )
        if inf.galler_sida("hoger"):
            kh_a = np.asarray(
                np.where(i_mask, np.minimum(kh_a, inf.kantsten_hojd_m), kh_a),
                dtype=np.float64,
            )

    z_rv_a = nock_a - tv_v_a * w_k
    z_rh_a = nock_a - tv_h_a * w_k

    z_korbana = np.where(d_aktiv < 0.0, nock_a + tv_v_a * d_aktiv, nock_a - tv_h_a * d_aktiv)
    z_gv = z_rv_a + kv_a + gv_a * (-d_aktiv - w_k)
    z_gh = z_rh_a + kh_a + gh_a * (d_aktiv - w_k)
    z_aktiv = np.where(d_aktiv < -w_k, z_gv, np.where(d_aktiv > w_k, z_gh, z_korbana))

    mark_sub = ny_mark[rad0:rad1, kol0:kol1]
    osak_sub = ny_osak[rad0:rad1, kol0:kol1]
    slag_sub = ny_slag[rad0:rad1, kol0:kol1]

    mark_sub[inom_korr] = z_aktiv.astype(np.float32)
    osak_sub[inom_korr] = osak_a.astype(np.float32)
    slag_sub[inom_korr] = np.uint8(HARDGJORT)

    return ny_mark, ny_osak, ny_slag


def rita_tvarsnitt_png(
    profil: SektionsProfil,
    korbana_halvbredd_m: float,
    gangbana_bredd_m: float,
    ut_fil: str | Path,
    *,
    punkter_d_m: Sequence[float] | NDArray[np.float64] = (),
    punkter_z_m: Sequence[float] | NDArray[np.float64] = (),
    huber_delta_m: float = 0.015,
    bredd_px: int = 900,
    hojd_px: int = 480,
    titel: str | None = None,
) -> Path:
    """Ritar en PNG-tvärsnittsbild med råa laserpunkter, parametrisk profil och osäkerhetsband."""
    w_k = float(korbana_halvbredd_m)
    w_g = float(gangbana_bredd_m)
    tot_w = w_k + w_g
    if w_k <= 0.0 or w_g < 0.0:
        raise GatuProfilFel("Ogiltiga bredder för tvärsnittsbild.")

    pd = np.asarray(punkter_d_m, dtype=np.float64).ravel()
    pz = np.asarray(punkter_z_m, dtype=np.float64).ravel()
    if pd.shape != pz.shape:
        raise GatuProfilFel("punkter_d_m och punkter_z_m måste ha samma längd.")
    giltiga = np.isfinite(pd) & np.isfinite(pz) & (np.abs(pd) <= tot_w + 0.5)
    pd = pd[giltiga]
    pz = pz[giltiga]

    # Segment för skarp kantstensritning (vänster gångbana, körbana, höger gångbana)
    eps = 1e-6
    d_noder = (
        [-w_k, 0.0, w_k]
        if w_g == 0.0
        else [-tot_w, -w_k - eps, -w_k + eps, 0.0, w_k - eps, w_k + eps, tot_w]
    )
    d_prof = np.asarray(d_noder, dtype=np.float64)
    z_prof = utvardera_sektionsprofil(profil, d_prof, w_k)
    osak = max(MIN_OSAKERHET_Z_M, float(profil.osakerhet_z_m))

    # Dela upp råpunkter i inliers och outliers för tydlig visualisering
    outlier_grans = max(3.0 * float(huber_delta_m), 3.0 * osak, 0.08)
    if pd.size > 0:
        z_mod_p = utvardera_sektionsprofil(profil, pd, w_k)
        ar_outlier = np.abs(pz - z_mod_p) > outlier_grans
    else:
        ar_outlier = np.empty(0, dtype=np.bool_)

    # Robust y-skala så parkerade bilar (+1,5 m) inte trycker ihop 12 cm kantsten
    z_band_min = float(np.min(z_prof)) - osak
    z_band_max = float(np.max(z_prof)) + osak
    if pd.size > 0 and np.any(~ar_outlier):
        z_inl = pz[~ar_outlier]
        z_min = min(z_band_min, float(np.min(z_inl)))
        z_max = max(z_band_max, float(np.max(z_inl)))
    else:
        z_min = z_band_min
        z_max = z_band_max

    z_span = max(0.25, z_max - z_min)
    z_min -= 0.15 * z_span
    z_max += 0.25 * z_span

    marg_v = 72
    marg_h = 28
    marg_t = 52
    marg_b = 58
    plott_w = max(200, int(bredd_px) - marg_v - marg_h)
    plott_h = max(160, int(hojd_px) - marg_t - marg_b)

    duk = Image.new("RGB", (int(bredd_px), int(hojd_px)), (250, 250, 250))
    draw = ImageDraw.Draw(duk)

    x0, y0 = marg_v, marg_t
    x1, y1 = marg_v + plott_w, marg_t + plott_h
    draw.rectangle([x0, y0, x1, y1], fill=(255, 255, 255), outline=(120, 120, 120))

    d_min = -tot_w - 0.25
    d_max = tot_w + 0.25

    def _till_px(dv: float, zv: float) -> tuple[int, int]:
        px = int(round(x0 + ((dv - d_min) / (d_max - d_min)) * plott_w))
        py = int(round(y1 - ((zv - z_min) / (z_max - z_min)) * plott_h))
        return px, py

    # Rutnät i höjdled och sidled
    for steg in range(5):
        frac = steg / 4.0
        zv = z_min + frac * (z_max - z_min)
        _, py = _till_px(0.0, zv)
        draw.line([(x0, py), (x1, py)], fill=(235, 235, 235), width=1)
        draw.text((8, py - 6), f"{zv:.2f} m", fill=(70, 70, 70))

    d_mark = [-tot_w, -w_k, 0.0, w_k, tot_w] if w_g > 0.0 else [-w_k, 0.0, w_k]
    for dm in d_mark:
        px, _ = _till_px(dm, z_min)
        farg = (195, 195, 210) if abs(abs(dm) - w_k) < 1e-6 else (230, 230, 230)
        draw.line([(px, y0), (px, y1)], fill=farg, width=1)
        draw.text((px - 16, y1 + 8), f"{dm:+.1f}m", fill=(70, 70, 70))

    # Osäkerhetsband (ritat per delsegment så kantstenssprånget blir skarpt)
    del_intervall: list[tuple[float, float]] = [(-w_k + eps, w_k - eps)]
    if w_g > 0.0:
        del_intervall.insert(0, (-tot_w, -w_k - eps))
        del_intervall.append((w_k + eps, tot_w))

    for da, db in del_intervall:
        d_seg = np.linspace(da, db, 25, dtype=np.float64)
        z_seg = utvardera_sektionsprofil(profil, d_seg, w_k)
        ovre = [_till_px(float(dv), float(zv + osak)) for dv, zv in zip(d_seg, z_seg, strict=True)]
        undre = [
            _till_px(float(dv), float(zv - osak))
            for dv, zv in zip(d_seg[::-1], z_seg[::-1], strict=True)
        ]
        draw.polygon(ovre + undre, fill=(210, 228, 248))

    # Råa laserpunkter (inliers = gråblå, outliers/bilar = röd; klippta högst upp om utanför y-axel)
    antal_outliers = int(np.count_nonzero(ar_outlier))
    for dv, zv, out_flag in zip(pd, pz, ar_outlier, strict=True):
        df = float(dv)
        zf = float(zv)
        klippt = zf > z_max
        z_rita = min(max(zf, z_min), z_max)
        px, py = _till_px(df, z_rita)
        if bool(out_flag):
            farg = (210, 45, 45)
            if klippt:
                draw.polygon([(px, y0 + 2), (px - 4, y0 + 10), (px + 4, y0 + 10)], fill=farg)
            else:
                draw.ellipse([px - 3, py - 3, px + 3, py + 3], fill=farg)
        else:
            draw.ellipse([px - 2, py - 2, px + 2, py + 2], fill=(60, 80, 95))

    # Parametrisk profilkurva
    prof_pts = [_till_px(float(dv), float(zv)) for dv, zv in zip(d_prof, z_prof, strict=True)]
    if len(prof_pts) >= 2:
        draw.line(prof_pts, fill=(18, 68, 160), width=3)

    # Rubrik och faktarad
    rubrik = (
        titel
        if titel is not None
        else f"Gatuprofil tvarsnitt s={profil.s_m:.1f} m "
        f"(kantsten V={profil.kantsten_vanster_m * 100:.1f} cm, "
        f"H={profil.kantsten_hoger_m * 100:.1f} cm, "
        f"tvarfall={profil.tvarfall_hoger * 100:.2f} %)"
    )
    draw.text((x0, 10), rubrik, fill=(20, 20, 20))
    status = "TYPVARDE" if profil.ar_typvarde else "ANPASSAD"
    fakta = (
        f"Status: {status} | sigma_z={profil.osakerhet_z_m * 100:.1f} cm | "
        f"punkter={profil.antal_punkter} | outliers={antal_outliers} (rod)"
    )
    draw.text((x0, 28), fakta, fill=(60, 60, 60))
    draw.text((x0, y1 + 30), "d [m] (vanster < 0 < hoger)", fill=(40, 40, 40))

    mal = Path(ut_fil)
    mal.parent.mkdir(parents=True, exist_ok=True)
    duk.save(mal, format="PNG")
    return mal
