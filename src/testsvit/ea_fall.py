"""Environment Agency 2D-benchmarkfall för skyfall (Neelz & Pender 2013, EA SC120002) (T09c).

Implementerar de tre skyfallsrelevanta fallen ur Environment Agencys benchmarksvit
*Benchmarking the Latest Generation of 2D Hydraulic Modelling Packages*
(Neelz & Pender 2013, Environment Agency Report SC120002):

- **EA1 (`ea_test1_sanka_over_troskel`)** – Test 1: *Flooding a disconnected water body*.
  Två sänkor skilda av en tröskel på 10,25 m och en djupare frånkopplad sänka på 9,70 m
  (Manning n = 0,03). Kontrollerar tröskelöverströmning, massbalans (< 0,1 %), jämn
  vattenyta vid jämvikt (std < 1 cm) samt överensstämmelse mot analytiskt jämviktsfacit.
- **EA2 (`ea_test2_sanknat_4x4`)** – Test 2: *Filling of floodplain depressions*.
  4×4-rutnät av 16 förbundna sänkor (0,5 m djupa med släta åsar, lägre sadelpunkter i x-
  och y-led samt svag generell lutning NV→SO, Manning n = 0,03). Jämför full 2D mot
  hybrid 2D + sänkgraf och verifierar kaskadordning (NV fylls först → SO), MAE, IoU,
  volymkonservering och uppsnabbning.
- **EA8A (`ea_test8a_urbant_regn`)** – Test 8A: *Urban rainfall & street flow*.
  Sluttande urbant gatunät omgivet av vattentäta byggnadskvarter (`MARKSLAG_BYGGNAD`),
  intensiv kort regnskur (400 mm/h i 3 min = 20 mm + efterförlopp, Manning n = 0,02).
  Jämför full 2D mot subgrid-/hybrid-2D och rapporterar volymfel, maxdjup-MAE, IoU,
  maxhastighet, max fara (`h*v`) och uppsnabbning.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from sankgraf.hierarki import bygg_sankgraf
from sankgraf.typer import MARKSLAG_BYGGNAD, MARKSLAG_HARDGJORT
from testsvit.k1_io import EPSG_SWEREF99_1800, GeoTransformInfo
from tvad.hybrid import jamfor_hybrid_mot_full2d, kor_hybrid_2d_graf
from tvad.lokal_troghet import kor_2d_lokal_troghet

EA_KALLA_BAS = "Neelz & Pender (2013), Environment Agency Report SC120002"
VOLYMFEL_GRANS_REL = 0.001  # < 0,1 % enligt docs/VALIDERING.md

_SWEREF_X0 = 150000.0
_SWEREF_Y0 = 6580000.0


class EaBenchmarkFel(Exception):
    """Fel vid uppbyggnad eller körning av Environment Agency-benchmarkfallen."""


@dataclass(frozen=True)
class EaBenchmarkResultat:
    """Sammanfattande valideringsresultat för ett Environment Agency 2D-testfall."""

    fall_id: str
    namn: str
    godkand: bool
    volymfel_rel: float
    max_djup_m: float
    jamforelse_mae_m: float
    iou: float
    kortid_2d_s: float
    kortid_hybrid_s: float | None
    uppsnabbning: float | None
    ea_referens: str
    kommentar: str

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar resultatet till en JSON-kompatibel ordbok."""
        return {
            "fall_id": self.fall_id,
            "namn": self.namn,
            "godkand": self.godkand,
            "volymfel_rel": self.volymfel_rel,
            "max_djup_m": self.max_djup_m,
            "jamforelse_mae_m": self.jamforelse_mae_m,
            "iou": self.iou,
            "kortid_2d_s": self.kortid_2d_s,
            "kortid_hybrid_s": self.kortid_hybrid_s,
            "uppsnabbning": self.uppsnabbning,
            "ea_referens": self.ea_referens,
            "kommentar": self.kommentar,
        }


@dataclass(frozen=True)
class EaTest1Geometri:
    """Terräng, regnserie och analytiskt jämviktsfacit för EA Test 1."""

    mark_z: NDArray[np.float32]
    markslag: NDArray[np.uint8]
    geo: GeoTransformInfo
    regn_tid_s: tuple[float, ...]
    regn_intensitet_mm_h: tuple[float, ...]
    slut_tid_s: float
    manning_n: float
    troskel_kolumn: int
    troskel_rh2000: float
    sanka_botten_rh2000: float
    sanka_mask: NDArray[np.bool_]
    facit_sanka_niva_rh2000: float
    facit_jamvikt_djup_m: NDArray[np.float64]


@dataclass(frozen=True)
class EaTest2Geometri:
    """Terräng, regnserie och sänkcentrum för EA Test 2 (4×4 förbundna sänkor)."""

    mark_z: NDArray[np.float32]
    markslag: NDArray[np.uint8]
    geo: GeoTransformInfo
    regn_tid_s: tuple[float, ...]
    regn_intensitet_mm_h: tuple[float, ...]
    slut_tid_s: float
    manning_n: float
    celler_per_sanka: int
    apron_kolumner: int
    sankcentrum: tuple[tuple[tuple[int, int], ...], ...]


@dataclass(frozen=True)
class EaTest8AGeometri:
    """Urban gatu- och kvartersgeometri samt regnserie för EA Test 8A."""

    mark_z: NDArray[np.float32]
    markslag: NDArray[np.uint8]
    geo: GeoTransformInfo
    regn_tid_s: tuple[float, ...]
    regn_intensitet_mm_h: tuple[float, ...]
    slut_tid_s: float
    manning_n: float
    byggnad_mask: NDArray[np.bool_]


def _berakna_iou(
    mask_a: NDArray[np.bool_],
    mask_b: NDArray[np.bool_],
) -> float:
    """Beräknar Intersection-over-Union (IoU) för två booleska våtmasker."""
    n_union = int(np.count_nonzero(mask_a | mask_b))
    if n_union == 0:
        return 1.0
    n_snitt = int(np.count_nonzero(mask_a & mask_b))
    return float(n_snitt) / float(n_union)


def ea_test1_sanka_over_troskel(
    *,
    pixelstorlek_m: float = 10.0,
    langd_m: float = 700.0,
    bredd_m: float = 100.0,
    troskel_rh2000: float = 10.25,
    sanka_botten_rh2000: float = 9.70,
    uppstroms_botten_rh2000: float = 10.00,
    facit_sanka_niva_rh2000: float = 10.05,
    regn_varaktighet_s: float = 300.0,
    slut_tid_s: float = 3600.0,
    manning_n: float = 0.03,
) -> EaTest1Geometri:
    """Bygger klassisk EA Test 1-geometri: två sänkor skilda av en tröskel på 10,25 m.

    Geometrin följer EA SC120002 Test 1 (700 m × 100 m, tröskelkrön 10,25 m,
    frånkopplad bakre sänka 9,70 m, Manning n = 0,03). Regnvolymen beräknas exakt
    så att uppströmsbassängen stiger över tröskeln (~10,35 m toppnivå) och vid
    jämvikt lämnar exakt vattennivån `facit_sanka_niva_rh2000` i den bakre sänkan.
    """
    if not math.isfinite(pixelstorlek_m) or pixelstorlek_m <= 0.0:
        raise EaBenchmarkFel(f"pixelstorlek_m måste vara > 0, fick {pixelstorlek_m}.")
    if langd_m < 200.0 or bredd_m < pixelstorlek_m:
        raise EaBenchmarkFel(f"Ogiltig domänstorlek: {langd_m} m × {bredd_m} m.")
    if not (sanka_botten_rh2000 < facit_sanka_niva_rh2000 < troskel_rh2000):
        raise EaBenchmarkFel(
            "Krav: sanka_botten_rh2000 < facit_sanka_niva_rh2000 < troskel_rh2000."
        )
    if not (uppstroms_botten_rh2000 < troskel_rh2000):
        raise EaBenchmarkFel("Krav: uppstroms_botten_rh2000 < troskel_rh2000.")
    if regn_varaktighet_s <= 0.0 or slut_tid_s <= regn_varaktighet_s:
        raise EaBenchmarkFel("Krav: 0 < regn_varaktighet_s < slut_tid_s.")
    if manning_n <= 0.0:
        raise EaBenchmarkFel(f"manning_n måste vara > 0, fick {manning_n}.")

    nx = max(14, round(langd_m / pixelstorlek_m))
    ny = max(2, round(bredd_m / pixelstorlek_m))
    x_centrum = (np.arange(nx, dtype=np.float64) + 0.5) * pixelstorlek_m

    # Zoner proportionella mot klassisk 700 m-profil:
    # [0, 150 m): tillrinningsramp 11,20 -> 10,30 m
    # [150, 250 m): uppströmsbassäng 10,00 m
    # [250, 350 m): tröskelbank upp till krön 10,25 m vid ~295 m och ned till 9,70 m
    # [350, 700 m]: plan frånkopplad bakre sänka 9,70 m
    x_ramp_slut = 150.0 * (langd_m / 700.0)
    x_upp_slut = 250.0 * (langd_m / 700.0)
    x_troskel = 295.0 * (langd_m / 700.0)
    x_sanka_start = 350.0 * (langd_m / 700.0)

    c_troskel = int(np.argmin(np.abs(x_centrum - x_troskel)))
    x_k = float(x_centrum[c_troskel])

    profil = np.full(nx, sanka_botten_rh2000, dtype=np.float64)
    for col in range(nx):
        xc = float(x_centrum[col])
        if xc < x_ramp_slut:
            andel = xc / max(x_ramp_slut, 1e-9)
            profil[col] = 11.20 - (11.20 - (troskel_rh2000 + 0.05)) * andel
        elif xc < x_upp_slut:
            profil[col] = uppstroms_botten_rh2000
        elif col < c_troskel:
            andel = (xc - x_upp_slut) / max(x_k - x_upp_slut, 1e-9)
            profil[col] = (
                uppstroms_botten_rh2000
                + (troskel_rh2000 - uppstroms_botten_rh2000) * andel
            )
        elif col == c_troskel:
            profil[col] = troskel_rh2000
        elif xc < x_sanka_start:
            andel = (xc - x_k) / max(x_sanka_start - x_k, 1e-9)
            profil[col] = troskel_rh2000 - (troskel_rh2000 - sanka_botten_rh2000) * andel
        else:
            profil[col] = sanka_botten_rh2000

    mark_z = np.broadcast_to(profil.astype(np.float32), (ny, nx)).copy()
    markslag = np.full((ny, nx), MARKSLAG_HARDGJORT, dtype=np.uint8)
    geo = GeoTransformInfo(
        bredd=nx,
        hojd=ny,
        x_min=_SWEREF_X0,
        y_max=_SWEREF_Y0,
        pixelstorlek_x=pixelstorlek_m,
        pixelstorlek_y=pixelstorlek_m,
        epsg=EPSG_SWEREF99_1800,
        dtype="float32",
    )

    z64 = mark_z.astype(np.float64)
    cell_area_m2 = pixelstorlek_m * pixelstorlek_m
    total_area_m2 = float(nx * ny) * cell_area_m2

    # Exakt analytiskt jämviktsdjup (uppströms fyllt till tröskeln, nedströms till facitnivån)
    facit_djup = np.zeros((ny, nx), dtype=np.float64)
    facit_djup[:, :c_troskel] = np.maximum(0.0, troskel_rh2000 - z64[:, :c_troskel])
    facit_djup[:, c_troskel + 1 :] = np.maximum(
        0.0, facit_sanka_niva_rh2000 - z64[:, c_troskel + 1 :]
    )

    v_upp_max_m3 = float(np.sum(facit_djup[:, :c_troskel])) * cell_area_m2
    v_ned_facit_m3 = float(np.sum(facit_djup[:, c_troskel + 1 :])) * cell_area_m2
    regn_tot_m = (v_upp_max_m3 + v_ned_facit_m3) / total_area_m2

    upp_area_m2 = float(c_troskel * ny) * cell_area_m2
    if regn_tot_m * upp_area_m2 <= v_upp_max_m3:
        raise EaBenchmarkFel(
            "Uppströmsvolymen räcker inte för överströmning över tröskeln med valda nivåer."
        )

    regn_mm_h = (regn_tot_m * 1000.0) / (regn_varaktighet_s / 3600.0)
    sanka_mask = np.zeros((ny, nx), dtype=np.bool_)
    sanka_mask[:, x_centrum >= x_sanka_start] = True

    return EaTest1Geometri(
        mark_z=mark_z,
        markslag=markslag,
        geo=geo,
        regn_tid_s=(0.0, regn_varaktighet_s, regn_varaktighet_s, slut_tid_s),
        regn_intensitet_mm_h=(regn_mm_h, regn_mm_h, 0.0, 0.0),
        slut_tid_s=slut_tid_s,
        manning_n=manning_n,
        troskel_kolumn=c_troskel,
        troskel_rh2000=troskel_rh2000,
        sanka_botten_rh2000=sanka_botten_rh2000,
        sanka_mask=sanka_mask,
        facit_sanka_niva_rh2000=facit_sanka_niva_rh2000,
        facit_jamvikt_djup_m=facit_djup,
    )


def kor_ea_test1(
    *,
    pixelstorlek_m: float = 10.0,
    slut_tid_s: float = 3600.0,
) -> EaBenchmarkResultat:
    """Kör EA Test 1 (sänka över tröskel) med 2D lokal tröghet mot analytiskt facit."""
    geom = ea_test1_sanka_over_troskel(
        pixelstorlek_m=pixelstorlek_m,
        slut_tid_s=slut_tid_s,
    )
    manning = {MARKSLAG_HARDGJORT: geom.manning_n, 0: geom.manning_n}
    res = kor_2d_lokal_troghet(
        geom.mark_z,
        geom.markslag,
        geom.geo,
        geom.regn_tid_s,
        geom.regn_intensitet_mm_h,
        slut_tid_s=geom.slut_tid_s,
        oppna_rander=False,
        manning_tabell=manning,
    )

    zs_slut = geom.mark_z.astype(np.float64) + res.slut_djup_m.astype(np.float64)
    zs_sanka = zs_slut[geom.sanka_mask]
    sanka_niva_medel = float(np.mean(zs_sanka))
    sanka_niva_std_m = float(np.std(zs_sanka))
    sanka_mae_m = float(np.mean(np.abs(zs_sanka - geom.facit_sanka_niva_rh2000)))
    troskel_max_djup_m = float(np.max(res.max_djup_m[:, geom.troskel_kolumn]))
    troskel_topp_rh2000 = geom.troskel_rh2000 + troskel_max_djup_m

    iou = _berakna_iou(
        res.slut_djup_m.astype(np.float64) > 0.10,
        geom.facit_jamvikt_djup_m > 0.10,
    )
    max_djup_m = float(np.max(res.max_djup_m))

    godkand = (
        res.volymfel_rel < VOLYMFEL_GRANS_REL
        and troskel_max_djup_m > 0.05
        and sanka_niva_std_m < 0.01
        and sanka_mae_m < 0.015
        and iou >= 0.90
    )

    ea_ref = (
        f"{EA_KALLA_BAS}, Test 1 (Flooding a disconnected water body; "
        "referensintervall TUFLOW/LISFLOOD-FP/MIKE 21/InfoWorks ICM/JFLOW+: "
        "plan slutnivå i bakre sänka med spridning ~±1–3 cm, volymfel < 0,1 %)"
    )
    kommentar = (
        f"Sänknivå={sanka_niva_medel:.4f} m (facit={geom.facit_sanka_niva_rh2000:.4f} m, "
        f"MAE={sanka_mae_m * 100.0:.2f} cm, std={sanka_niva_std_m * 100.0:.3f} cm); "
        f"tröskeltopp={troskel_topp_rh2000:.3f} m över {geom.troskel_rh2000:.2f} m"
    )
    return EaBenchmarkResultat(
        fall_id="EA1",
        namn="EA Test 1 – Sänka över tröskel",
        godkand=godkand,
        volymfel_rel=float(res.volymfel_rel),
        max_djup_m=max_djup_m,
        jamforelse_mae_m=sanka_mae_m,
        iou=iou,
        kortid_2d_s=float(res.kortid_s),
        kortid_hybrid_s=None,
        uppsnabbning=None,
        ea_referens=ea_ref,
        kommentar=kommentar,
    )


def ea_test2_sanknat_4x4(
    *,
    celler_per_sanka: int = 16,
    pixelstorlek_m: float = 5.0,
    sankdjup_m: float = 0.50,
    lutning_per_sanka_m: float = 0.12,
    regn_mm_h: float = 360.0,
    regn_varaktighet_s: float = 270.0,
    slut_tid_s: float = 1500.0,
    manning_n: float = 0.03,
) -> EaTest2Geometri:
    """Bygger klassisk EA Test 2-geometri: 4×4-rutnät av 16 förbundna 0,5 m-sänkor.

    Släta kosinusformade sänkor ger exakt 0,5 m djup med lägre sadelpunkter i x- och
    y-led än hörnryggarna och en svag generell lutning från NV mot SO. En tillrinningsyta
    väster om NV-sänkan `(0, 0)` leder inrinningsvattnet in i `(0, 0)` så att en tydlig
    kaskad NV → SO uppstår under likformigt regn (Manning n = 0,03).
    """
    if celler_per_sanka < 6 or celler_per_sanka % 2 != 0:
        raise EaBenchmarkFel(
            f"celler_per_sanka måste vara ett jämnt heltal >= 6, fick {celler_per_sanka}."
        )
    if not math.isfinite(pixelstorlek_m) or pixelstorlek_m <= 0.0:
        raise EaBenchmarkFel(f"pixelstorlek_m måste vara > 0, fick {pixelstorlek_m}.")
    if sankdjup_m <= 0.0:
        raise EaBenchmarkFel(f"sankdjup_m måste vara > 0, fick {sankdjup_m}.")
    if regn_mm_h <= 0.0 or regn_varaktighet_s <= 0.0 or slut_tid_s <= regn_varaktighet_s:
        raise EaBenchmarkFel("Ogiltiga regn- eller tidsparametrar för EA Test 2.")
    if manning_n <= 0.0:
        raise EaBenchmarkFel(f"manning_n måste vara > 0, fick {manning_n}.")

    n_grid = 4 * celler_per_sanka
    apron_kolumner = 3 * celler_per_sanka  # Jämnt delbart med subgrid_faktor=2
    ny = n_grid
    nx = n_grid + apron_kolumner

    mark_z = np.zeros((ny, nx), dtype=np.float32)
    markslag = np.full((ny, nx), MARKSLAG_HARDGJORT, dtype=np.uint8)

    u_1d = (np.arange(n_grid, dtype=np.float64) + 0.5) / float(celler_per_sanka)
    v_1d = (np.arange(n_grid, dtype=np.float64) + 0.5) / float(celler_per_sanka)
    uu, vv = np.meshgrid(u_1d, v_1d)

    amplitud = 0.5 * sankdjup_m
    z_4x4 = (
        10.0
        - lutning_per_sanka_m * (uu + vv)
        + amplitud * (np.cos(2.0 * np.pi * uu) + np.cos(2.0 * np.pi * vv) + 2.0)
    )
    mark_z[:, apron_kolumner:] = z_4x4.astype(np.float32)

    # Tillrinningsyta väster om 4×4-rutnätet som styr allt tillskottsflöde in i NV-sänkan (0, 0)
    r_utlopp = 0.5 * float(celler_per_sanka)
    cc_ap, rr_ap = np.meshgrid(
        np.arange(apron_kolumner, dtype=np.float64),
        np.arange(ny, dtype=np.float64),
    )
    dr = np.abs((rr_ap + 0.5) - r_utlopp)
    dc = float(apron_kolumner) - cc_ap
    z_apron = 10.65 + 0.025 * dc + 0.025 * dr
    mark_z[:, :apron_kolumner] = z_apron.astype(np.float32)

    # Tät skiljevägg söder om NV-sänkan så tillrinningsytan enbart matar (0, 0)
    markslag[celler_per_sanka:, apron_kolumner - 1] = np.uint8(MARKSLAG_BYGGNAD)
    mark_z[celler_per_sanka:, apron_kolumner - 1] = np.float32(14.0)

    geo = GeoTransformInfo(
        bredd=nx,
        hojd=ny,
        x_min=_SWEREF_X0,
        y_max=_SWEREF_Y0,
        pixelstorlek_x=pixelstorlek_m,
        pixelstorlek_y=pixelstorlek_m,
        epsg=EPSG_SWEREF99_1800,
        dtype="float32",
    )

    halv = celler_per_sanka // 2
    centrum: tuple[tuple[tuple[int, int], ...], ...] = tuple(
        tuple(
            (i * celler_per_sanka + halv, apron_kolumner + j * celler_per_sanka + halv)
            for j in range(4)
        )
        for i in range(4)
    )

    return EaTest2Geometri(
        mark_z=mark_z,
        markslag=markslag,
        geo=geo,
        regn_tid_s=(0.0, regn_varaktighet_s, regn_varaktighet_s, slut_tid_s),
        regn_intensitet_mm_h=(regn_mm_h, regn_mm_h, 0.0, 0.0),
        slut_tid_s=slut_tid_s,
        manning_n=manning_n,
        celler_per_sanka=celler_per_sanka,
        apron_kolumner=apron_kolumner,
        sankcentrum=centrum,
    )


def _kontrollera_nv_so_kaskad(
    max_djup_m: NDArray[np.float32],
    tid_till_0_2m_s: NDArray[np.float32],
    centrum: tuple[tuple[tuple[int, int], ...], ...],
) -> tuple[bool, float, float, float]:
    """Verifierar att NV-sänkan (0, 0) fylls först och kaskadar mot SO (3, 3)."""
    djup = [
        [float(max_djup_m[centrum[i][j][0], centrum[i][j][1]]) for j in range(4)]
        for i in range(4)
    ]
    tider = [
        [float(tid_till_0_2m_s[centrum[i][j][0], centrum[i][j][1]]) for j in range(4)]
        for i in range(4)
    ]

    t_nv = tider[0][0]
    ovriga_tider = [tider[i][j] for i in range(4) for j in range(4) if (i, j) != (0, 0)]
    nv_forst = math.isfinite(t_nv) and all(
        (not math.isfinite(t_ov)) or (t_nv < t_ov) for t_ov in ovriga_tider
    )

    h_nv = djup[0][0]
    h_ring1 = 0.5 * (djup[0][1] + djup[1][0])
    h_ring2 = (djup[0][2] + djup[1][1] + djup[2][0]) / 3.0
    h_so = djup[3][3]

    kaskad_ok = (
        nv_forst
        and h_nv > djup[0][1]
        and h_nv > djup[1][0]
        and min(djup[0][1], djup[1][0]) > djup[1][1]
        and h_ring2 > h_so
    )
    return kaskad_ok, h_nv, h_ring1, h_so


def kor_ea_test2(
    *,
    celler_per_sanka: int = 16,
    pixelstorlek_m: float = 5.0,
    subgrid_faktor: int = 2,
    nod_persistens_grans_m: float = 0.15,
    slut_tid_s: float = 1500.0,
) -> EaBenchmarkResultat:
    """Kör EA Test 2 (4×4 sänknät) med både full 2D och hybrid 2D + sänkgraf."""
    geom = ea_test2_sanknat_4x4(
        celler_per_sanka=celler_per_sanka,
        pixelstorlek_m=pixelstorlek_m,
        slut_tid_s=slut_tid_s,
    )
    manning = {MARKSLAG_HARDGJORT: geom.manning_n, 0: geom.manning_n}
    graf = bygg_sankgraf(
        geom.mark_z,
        np.zeros_like(geom.mark_z),
        geom.markslag,
        geom.geo,
    )
    antal_lov = len(graf.lovnoder())

    res_2d = kor_2d_lokal_troghet(
        geom.mark_z,
        geom.markslag,
        geom.geo,
        geom.regn_tid_s,
        geom.regn_intensitet_mm_h,
        slut_tid_s=geom.slut_tid_s,
        oppna_rander=False,
        manning_tabell=manning,
    )
    res_hyb = kor_hybrid_2d_graf(
        geom.mark_z,
        geom.markslag,
        geom.geo,
        geom.regn_tid_s,
        geom.regn_intensitet_mm_h,
        graf=graf,
        nod_persistens_grans_m=nod_persistens_grans_m,
        slut_tid_s=geom.slut_tid_s,
        oppna_rander=False,
        manning_tabell=manning,
        subgrid_faktor=subgrid_faktor,
    )

    jamf = jamfor_hybrid_mot_full2d(res_2d, res_hyb)
    kaskad_ok, h_nv, h_ring1, h_so = _kontrollera_nv_so_kaskad(
        res_2d.max_djup_m,
        res_2d.tid_till_0_2m_s,
        geom.sankcentrum,
    )
    volymfel_rel = max(jamf.volymfel_full2d_rel, jamf.volymfel_hybrid_rel)
    max_djup_m = float(np.max(res_2d.max_djup_m))

    godkand = (
        antal_lov == 16
        and volymfel_rel < VOLYMFEL_GRANS_REL
        and kaskad_ok
        and jamf.maxdjupsfel_mae_m < 0.05
        and jamf.traffad_yta_iou >= 0.80
    )

    ea_ref = (
        f"{EA_KALLA_BAS}, Test 2 (Filling of floodplain depressions; "
        "4×4 sänkor à 0,5 m djup, n = 0,03; etablerade 2D-modeller som TUFLOW/LISFLOOD-FP/"
        "MIKE 21/InfoWorks ICM visar kaskadfyllning NV→SO med slutsänkdjup inom ~±2–5 cm)"
    )
    kommentar = (
        f"16 lövsänkor (hittade={antal_lov}), NV→SO kaskad={'OK' if kaskad_ok else 'FEL'} "
        f"(NV={h_nv:.2f} m → ring1={h_ring1:.2f} m → SO={h_so:.2f} m), "
        f"hybrid vs 2D: MAE={jamf.maxdjupsfel_mae_m:.4f} m, "
        f"maxdiff={jamf.maxdjupsfel_max_m:.4f} m, IoU={jamf.traffad_yta_iou:.3f}, "
        f"uppsnabbning={jamf.speedup_faktor:.2f}x"
    )
    return EaBenchmarkResultat(
        fall_id="EA2",
        namn="EA Test 2 – Sänknät 4×4 (hybrid vs full 2D)",
        godkand=godkand,
        volymfel_rel=volymfel_rel,
        max_djup_m=max_djup_m,
        jamforelse_mae_m=jamf.maxdjupsfel_mae_m,
        iou=jamf.traffad_yta_iou,
        kortid_2d_s=jamf.kortid_full2d_s,
        kortid_hybrid_s=jamf.kortid_hybrid_s,
        uppsnabbning=jamf.speedup_faktor,
        ea_referens=ea_ref,
        kommentar=kommentar,
    )


def ea_test8a_urbant_regn(
    *,
    pixelstorlek_m: float = 2.0,
    bredd_m: float = 160.0,
    hojd_m: float = 128.0,
    regn_mm_h: float = 400.0,
    regn_varaktighet_s: float = 180.0,
    slut_tid_s: float = 420.0,
    manning_n: float = 0.02,
) -> EaTest8AGeometri:
    """Bygger EA Test 8A-typisk urban gatustruktur med byggnadskvarter och intensivt regn.

    Sluttande asfaltgator (Manning n = 0,02) och korsningar mellan sex vattentäta
    byggnadskvarter (`MARKSLAG_BYGGNAD`) som leder avrinningen från en kort intensiv
    regnskur (400 mm/h i 3 min = 20 mm) ned till två urbana lågpunkter.
    """
    if not math.isfinite(pixelstorlek_m) or pixelstorlek_m <= 0.0:
        raise EaBenchmarkFel(f"pixelstorlek_m måste vara > 0, fick {pixelstorlek_m}.")
    if bredd_m < 40.0 or hojd_m < 40.0:
        raise EaBenchmarkFel(f"Ogiltig urban domänstorlek: {bredd_m} m × {hojd_m} m.")
    if regn_mm_h <= 0.0 or regn_varaktighet_s <= 0.0 or slut_tid_s <= regn_varaktighet_s:
        raise EaBenchmarkFel("Ogiltiga regn- eller tidsparametrar för EA Test 8A.")
    if manning_n <= 0.0:
        raise EaBenchmarkFel(f"manning_n måste vara > 0, fick {manning_n}.")

    nx = max(16, round(bredd_m / pixelstorlek_m))
    ny = max(16, round(hojd_m / pixelstorlek_m))
    if nx % 2 != 0:
        nx += 1
    if ny % 2 != 0:
        ny += 1

    x_m = (np.arange(nx, dtype=np.float64) + 0.5) * pixelstorlek_m
    y_m = (np.arange(ny, dtype=np.float64) + 0.5) * pixelstorlek_m
    xx_m, yy_m = np.meshgrid(x_m, y_m)

    # Generell stadslutning NV→SO (~1,5 % i x-led, ~0,8 % i y-led) + två gatulågpunkter
    skala_x = bredd_m / 160.0
    skala_y = hojd_m / 128.0
    z_f = 15.0 - 0.015 * xx_m - 0.008 * yy_m
    dist1_kv = (xx_m - 64.0 * skala_x) ** 2 + (yy_m - 60.0 * skala_y) ** 2
    dist2_kv = (xx_m - 136.0 * skala_x) ** 2 + (yy_m - 108.0 * skala_y) ** 2
    z_f -= 0.50 * np.exp(-dist1_kv / ((14.0 * skala_x) ** 2))
    z_f -= 0.65 * np.exp(-dist2_kv / ((16.0 * skala_x) ** 2))

    mark_z = z_f.astype(np.float32)
    markslag = np.full((ny, nx), MARKSLAG_HARDGJORT, dtype=np.uint8)
    byggnad_mask = np.zeros((ny, nx), dtype=np.bool_)

    # Sex vattentäta byggnadskvarter definierade i fysiska meter (upplösningsinvariant)
    y_band = ((12.0 * skala_y, 48.0 * skala_y), (68.0 * skala_y, 104.0 * skala_y))
    x_band = (
        (12.0 * skala_x, 52.0 * skala_x),
        (72.0 * skala_x, 112.0 * skala_x),
        (124.0 * skala_x, 144.0 * skala_x),
    )
    for y0, y1 in y_band:
        for x0, x1 in x_band:
            kvarter = (yy_m >= y0) & (yy_m < y1) & (xx_m >= x0) & (xx_m < x1)
            byggnad_mask |= kvarter

    markslag[byggnad_mask] = np.uint8(MARKSLAG_BYGGNAD)
    mark_z[byggnad_mask] += np.float32(4.0)

    geo = GeoTransformInfo(
        bredd=nx,
        hojd=ny,
        x_min=_SWEREF_X0,
        y_max=_SWEREF_Y0,
        pixelstorlek_x=pixelstorlek_m,
        pixelstorlek_y=pixelstorlek_m,
        epsg=EPSG_SWEREF99_1800,
        dtype="float32",
    )

    return EaTest8AGeometri(
        mark_z=mark_z,
        markslag=markslag,
        geo=geo,
        regn_tid_s=(0.0, regn_varaktighet_s, regn_varaktighet_s, slut_tid_s),
        regn_intensitet_mm_h=(regn_mm_h, regn_mm_h, 0.0, 0.0),
        slut_tid_s=slut_tid_s,
        manning_n=manning_n,
        byggnad_mask=byggnad_mask,
    )


def kor_ea_test8a(
    *,
    pixelstorlek_m: float = 2.0,
    subgrid_faktor: int = 2,
    anvand_hybrid: bool = True,
    slut_tid_s: float = 420.0,
    min_djup_m: float = 2e-3,
) -> EaBenchmarkResultat:
    """Kör EA Test 8A (urbant regn & gatuströmning): full 2D mot hybrid-/subgrid-2D."""
    geom = ea_test8a_urbant_regn(
        pixelstorlek_m=pixelstorlek_m,
        slut_tid_s=slut_tid_s,
    )
    manning = {MARKSLAG_HARDGJORT: geom.manning_n, 0: geom.manning_n}

    res_2d = kor_2d_lokal_troghet(
        geom.mark_z,
        geom.markslag,
        geom.geo,
        geom.regn_tid_s,
        geom.regn_intensitet_mm_h,
        slut_tid_s=geom.slut_tid_s,
        oppna_rander=False,
        manning_tabell=manning,
        min_djup_m=min_djup_m,
    )

    if anvand_hybrid:
        graf = bygg_sankgraf(
            geom.mark_z,
            np.zeros_like(geom.mark_z),
            geom.markslag,
            geom.geo,
        )
        res_jamf = kor_hybrid_2d_graf(
            geom.mark_z,
            geom.markslag,
            geom.geo,
            geom.regn_tid_s,
            geom.regn_intensitet_mm_h,
            graf=graf,
            nod_persistens_grans_m=0.15,
            slut_tid_s=geom.slut_tid_s,
            oppna_rander=False,
            manning_tabell=manning,
            min_djup_m=min_djup_m,
            subgrid_faktor=subgrid_faktor,
        )
        metod_etikett = f"hybrid+subgrid(f={subgrid_faktor})"
    else:
        res_jamf = kor_2d_lokal_troghet(
            geom.mark_z,
            geom.markslag,
            geom.geo,
            geom.regn_tid_s,
            geom.regn_intensitet_mm_h,
            slut_tid_s=geom.slut_tid_s,
            oppna_rander=False,
            manning_tabell=manning,
            min_djup_m=min_djup_m,
            subgrid_faktor=subgrid_faktor,
        )
        metod_etikett = f"subgrid-2D(f={subgrid_faktor})"

    jamf = jamfor_hybrid_mot_full2d(res_2d, res_jamf)
    volymfel_rel = max(jamf.volymfel_full2d_rel, jamf.volymfel_hybrid_rel)

    byggnad_torr_2d = float(np.max(res_2d.max_djup_m[geom.byggnad_mask])) == 0.0
    byggnad_torr_jamf = float(np.max(res_jamf.max_djup_m[geom.byggnad_mask])) == 0.0
    byggnader_tata = byggnad_torr_2d and byggnad_torr_jamf

    max_djup_m = float(np.max(res_2d.max_djup_m))
    max_hastighet_m_s = float(np.max(res_2d.max_hastighet_m_s))
    max_fara_m2_s = float(np.max(res_2d.fara_m2_s))

    godkand = (
        volymfel_rel < VOLYMFEL_GRANS_REL
        and byggnader_tata
        and max_djup_m > 0.20
        and math.isfinite(max_hastighet_m_s)
        and max_hastighet_m_s > 0.20
        and math.isfinite(max_fara_m2_s)
        and max_fara_m2_s > 0.01
        and jamf.maxdjupsfel_mae_m < 0.05
        and jamf.traffad_yta_iou >= 0.85
    )

    ea_ref = (
        f"{EA_KALLA_BAS}, Test 8A (Urban rainfall & street flow, Glasgow-typisk regnpuls "
        "400 mm/h i 3 min = 20 mm, asfalt n = 0,02; referensmodeller TUFLOW/LISFLOOD-FP/"
        "InfoWorks ICM/MIKE 21 visar typisk modellskillnad ~2–5 cm i gatulågpunkter)"
    )
    kommentar = (
        f"{metod_etikett} vs full 2D: MAE={jamf.maxdjupsfel_mae_m:.4f} m, "
        f"IoU={jamf.traffad_yta_iou:.3f}, uppsnabbning={jamf.speedup_faktor:.2f}x; "
        f"v_max={max_hastighet_m_s:.2f} m/s, fara_max={max_fara_m2_s:.3f} m²/s, "
        f"byggnader täta={'JA' if byggnader_tata else 'NEJ'}"
    )
    return EaBenchmarkResultat(
        fall_id="EA8A",
        namn="EA Test 8A – Urbant regn och gatuströmning",
        godkand=godkand,
        volymfel_rel=volymfel_rel,
        max_djup_m=max_djup_m,
        jamforelse_mae_m=jamf.maxdjupsfel_mae_m,
        iou=jamf.traffad_yta_iou,
        kortid_2d_s=jamf.kortid_full2d_s,
        kortid_hybrid_s=jamf.kortid_hybrid_s,
        uppsnabbning=jamf.speedup_faktor,
        ea_referens=ea_ref,
        kommentar=kommentar,
    )


def formatera_ea_tabell(resultat: Sequence[EaBenchmarkResultat]) -> str:
    """Formaterar EA-benchmarkresultaten som en läsbar Markdown-tabell."""
    rader = [
        "| Fall | Namn | Utfall | Volymfel | Maxdjup | MAE | IoU "
        "| 2D (s) | Hybrid (s) | Uppsnabbning | Kommentar |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in resultat:
        utfall = "GODKAND" if r.godkand else "UNDERKAND"
        hyb_str = f"{r.kortid_hybrid_s:.3f}" if r.kortid_hybrid_s is not None else "–"
        upp_str = f"{r.uppsnabbning:.2f}x" if r.uppsnabbning is not None else "–"
        rader.append(
            f"| {r.fall_id} | {r.namn} | {utfall} | {r.volymfel_rel:.4%} "
            f"| {r.max_djup_m:.3f} m | {r.jamforelse_mae_m:.4f} m | {r.iou:.3f} "
            f"| {r.kortid_2d_s:.3f} | {hyb_str} | {upp_str} | {r.kommentar} |"
        )
    return "\n".join(rader)


def kor_alla_ea_testfall(
    *,
    ea1_pixelstorlek_m: float = 10.0,
    ea2_celler_per_sanka: int = 16,
    ea2_pixelstorlek_m: float = 5.0,
    ea8a_pixelstorlek_m: float = 2.0,
) -> tuple[list[EaBenchmarkResultat], str]:
    """Kör samtliga tre skyfallsrelevanta EA-benchmarkfall och returnerar resultat + tabell."""
    resultat = [
        kor_ea_test1(pixelstorlek_m=ea1_pixelstorlek_m),
        kor_ea_test2(
            celler_per_sanka=ea2_celler_per_sanka,
            pixelstorlek_m=ea2_pixelstorlek_m,
        ),
        kor_ea_test8a(pixelstorlek_m=ea8a_pixelstorlek_m),
    ]
    return resultat, formatera_ea_tabell(resultat)
