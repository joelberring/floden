"""Plangeometri, profilsnitt och beräkning för RL 18-18 kurvig väg (kapitel 3, N18K1–N18K8).

Källa: Referencelaboratoriet RL 18-18 kapitel 3 ("Test Cases for a Curved Road", s. 10–12, 16)
samt bladet `Coordinates` i `TestCurvedRoad_20181101.xls`.

- Två körfält à 5 m bredd (mittlinjer på ±2,5 m från vägmitt), färdriktning söder → norr.
- Trafik: 10 000 fordon/dygn jämnt fördelade på de två körfälten (90 % kat. 1 i 90 km/h,
  5 % kat. 2 i 70 km/h, 5 % kat. 3 med 5 axlar i 70 km/h).
- Källpunktsavstånd längs körfält: 10 m (pos 5), 25 m (pos 3), 50 m (övriga); väglängd
  40 × kortaste avstånd till mottagaren (±20 × d_min längs körfältet, RL 18-18 s. 16).
- Vind: meteorologisk vindriktning φ_u = 315° från norr (blåser mot 135° SO), så u = +3 m/s
  ger medvind i pos 1, 3, 4 och motvind i pos 2, 5 (RL 18-18 s. 12).
- Tjock skärm: byggnad med lodräta hårda väggar (4-1, 5-1) eller gräsklädd vall (4-2, 5-2).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal

import numpy as np

from buller.emission.direktivitet import Kallriktning
from buller.emission.modell import Trafikflode, Vagforhallanden
from buller.nord2000.reflektion import (
    VertikalReflektor,
    linje_korsar_segment,
    reflektionskorrektion_db,
    reflektionspunkt_plan,
)
from buller.nord2000.terrang_forenkling import REGLER_TC_101327, forenkla_terrang
from buller.testfall.excelimport import Celler, _som_tal
from buller.testfall.korare import Emissionsmodell, EmissionsmodellMedDirektivitet
from buller.typer import ANTAL_BAND, TERSBAND_HZ, BullerFel, Flyttal, P2PKarna, Terrangprofil, Vader

TjockSkarmTyp = Literal["byggnad", "vall"]

SIGMA_VAG_KNSM4 = 20000.0  # impedansklass G, RL 18-18 s. 12
SIGMA_MARK_KNSM4 = 200.0  # impedansklass D, RL 18-18 s. 12
KORFALTSBREDD_M = 5.0
AXELHALVA_M = 1.0  # axelbredd 2,0 m, RL 18-18 s. 10
TUNN_SKARM_HALVBRED_M = 0.01
VAG_SLANT_LUTNING = 1.0  # 45° slänt från vägkanten, RL 18-18 s. 10


@dataclass(frozen=True)
class Punkt3D:
    x_m: float
    y_m: float
    z_m: float


@dataclass(frozen=True)
class MottagarPunkt:
    position: str
    x_m: float
    y_m: float
    z_m: float
    z_mark_m: float

    @property
    def hojd_over_mark_m(self) -> float:
        return self.z_m - self.z_mark_m


@dataclass(frozen=True)
class KurvigVagGeometri:
    """Inläst plangeometri ur bladet `Coordinates` i TestCurvedRoad."""

    vagmitt: tuple[Punkt3D, ...]
    tunn_skarm: tuple[Punkt3D, ...]
    tjock_skarm_topp: tuple[Punkt3D, ...]
    tjock_skarm_bas: tuple[Punkt3D, ...]
    hojdkurvor: tuple[tuple[Punkt3D, ...], ...]
    mottagare: dict[str, MottagarPunkt]


@dataclass(frozen=True)
class KallpunktPlan:
    """En källpunkt på ett körfält i plan, med segmentlängd och färdriktning."""

    x_m: float
    y_m: float
    z_m: float
    langd_m: float
    tangent_x: float
    tangent_y: float
    avstand_till_vagkant_m: float = KORFALTSBREDD_M * 0.5 - AXELHALVA_M


def _las_punktblock(celler: Celler, startrad: int) -> tuple[list[Punkt3D], int]:
    """Läser sammanhängande (X, Y, Z)-rader från `startrad`; returnerar (punkter, nästa rad)."""
    punkter: list[Punkt3D] = []
    r = startrad
    while r < len(celler):
        rad = celler[r]
        if len(rad) >= 3:
            xs, ys, zs = _som_tal(rad[0]), _som_tal(rad[1]), _som_tal(rad[2])
            if xs is not None and ys is not None and zs is not None:
                punkter.append(Punkt3D(float(xs), float(ys), float(zs)))
                r += 1
                continue
        if punkter:
            break
        r += 1
    return punkter, r


def las_kurvig_vag_geometri(celler: Celler) -> KurvigVagGeometri:
    """Tolkar bladet `Coordinates` i `TestCurvedRoad_20181101.xls`."""

    def hitta(rubrik: str) -> int:
        nyckel = rubrik.lower()
        for r, rad in enumerate(celler):
            if rad and nyckel in rad[0].strip().lower():
                return r
        raise BullerFel(f"Bladet Coordinates saknar rubriken '{rubrik}'.")

    vagmitt, _ = _las_punktblock(celler, hitta("road centreline") + 1)
    tunn, _ = _las_punktblock(celler, hitta("thin screen") + 1)
    tjock_topp, _ = _las_punktblock(celler, hitta("thick screen coordinates") + 1)
    tjock_bas, _ = _las_punktblock(celler, hitta("thick screen base") + 1)

    r_terrang = hitta("terrain coordinates") + 1
    r_mottagare = hitta("receiver coordinates")
    hojdkurvor: list[tuple[Punkt3D, ...]] = []
    r = r_terrang
    while r < r_mottagare:
        rad = celler[r]
        if len(rad) >= 3 and _som_tal(rad[0]) is not None:
            kurva, r = _las_punktblock(celler, r)
            if len(kurva) >= 2:
                hojdkurvor.append(tuple(kurva))
        else:
            r += 1

    mottagare: dict[str, MottagarPunkt] = {}
    for row in celler[r_mottagare + 1 :]:
        if len(row) < 5:
            continue
        x, y, z, zm = (_som_tal(row[i]) for i in range(4))
        etikett = row[4].strip()
        if x is None or y is None or z is None or zm is None or not etikett:
            continue
        pos = etikett.lower().replace("pos", "").strip()
        mottagare[pos] = MottagarPunkt(pos, float(x), float(y), float(z), float(zm))

    if len(vagmitt) < 2 or not mottagare:
        raise BullerFel("Bladet Coordinates saknar vägmittlinje eller mottagare.")
    return KurvigVagGeometri(
        vagmitt=tuple(vagmitt),
        tunn_skarm=tuple(tunn),
        tjock_skarm_topp=tuple(tjock_topp),
        tjock_skarm_bas=tuple(tjock_bas),
        hojdkurvor=tuple(hojdkurvor),
        mottagare=mottagare,
    )


def _korsning_segment(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    cx: float,
    cy: float,
    dx: float,
    dy: float,
) -> float | None:
    """Returnerar parameter t ∈ (0, 1) längs AB där AB korsar segmentet CD, annars None."""
    rx, ry = bx - ax, by - ay
    sx, sy = dx - cx, dy - cy
    det = rx * sy - ry * sx
    if abs(det) < 1.0e-12:
        return None
    qpx, qpy = cx - ax, cy - ay
    t = (qpx * sy - qpy * sx) / det
    u = (qpx * ry - qpy * rx) / det
    if 0.0 < t < 1.0 and 0.0 <= u <= 1.0:
        return t
    return None


def _forskjut_polylinje(punkter: tuple[Punkt3D, ...], sidoforskjutning_m: float) -> list[Punkt3D]:
    """Parallellförskjuter en 2D-polylinje åt höger (positivt) i färdriktningen."""
    ut: list[Punkt3D] = []
    n = len(punkter)
    for i, p in enumerate(punkter):
        if i == 0:
            dx, dy = punkter[1].x_m - p.x_m, punkter[1].y_m - p.y_m
        elif i == n - 1:
            dx, dy = p.x_m - punkter[i - 1].x_m, p.y_m - punkter[i - 1].y_m
        else:
            dx1, dy1 = p.x_m - punkter[i - 1].x_m, p.y_m - punkter[i - 1].y_m
            l1 = math.hypot(dx1, dy1)
            dx2, dy2 = punkter[i + 1].x_m - p.x_m, punkter[i + 1].y_m - p.y_m
            l2 = math.hypot(dx2, dy2)
            dx, dy = dx1 / l1 + dx2 / l2, dy1 / l1 + dy2 / l2
        norm = math.hypot(dx, dy)
        nx, ny = dy / norm, -dx / norm  # höger normal i (x öst, y nord)
        ut.append(Punkt3D(p.x_m + sidoforskjutning_m * nx, p.y_m + sidoforskjutning_m * ny, p.z_m))
    return ut


def kallpunkter_langs_korfalt(
    vagmitt: tuple[Punkt3D, ...],
    mottagare: MottagarPunkt,
    sidoforskjutning_m: float,
    steg_m: float,
    *,
    axel_forskjutning_m: float = AXELHALVA_M,
) -> list[KallpunktPlan]:
    """Delar ett körfält i källpunkter med avståndet `steg_m` inom ±20 × d_min (RL 18-18 s. 16)."""
    linje = _forskjut_polylinje(vagmitt, sidoforskjutning_m)
    # Beräkna båglängder och hitta närmaste punkt till mottagaren.
    s_kum = [0.0]
    for i in range(len(linje) - 1):
        s_kum.append(
            s_kum[-1] + math.hypot(linje[i + 1].x_m - linje[i].x_m, linje[i + 1].y_m - linje[i].y_m)
        )
    basta_d = math.inf
    basta_s = 0.0
    for i in range(len(linje) - 1):
        ax, ay = linje[i].x_m, linje[i].y_m
        bx, by = linje[i + 1].x_m, linje[i + 1].y_m
        seg_l = s_kum[i + 1] - s_kum[i]
        if seg_l <= 0.0:
            continue
        t = ((mottagare.x_m - ax) * (bx - ax) + (mottagare.y_m - ay) * (by - ay)) / (seg_l * seg_l)
        t_k = min(1.0, max(0.0, t))
        px, py = ax + t_k * (bx - ax), ay + t_k * (by - ay)
        d = math.hypot(mottagare.x_m - px, mottagare.y_m - py)
        if d < basta_d:
            basta_d = d
            basta_s = s_kum[i] + t_k * seg_l

    halv_langd = 20.0 * basta_d  # 40 × kortaste avstånd, RL 18-18 s. 16
    s_min = max(0.0, basta_s - halv_langd)
    s_max = min(s_kum[-1], basta_s + halv_langd)
    if s_max <= s_min:
        return []

    n_steg = max(1, int(round((s_max - s_min) / steg_m)))
    ds = (s_max - s_min) / n_steg
    punkter: list[KallpunktPlan] = []
    seg_idx = 0
    for k in range(n_steg):
        s_mitt = s_min + (k + 0.5) * ds
        while seg_idx + 1 < len(s_kum) - 1 and s_kum[seg_idx + 1] < s_mitt:
            seg_idx += 1
        a, b = linje[seg_idx], linje[seg_idx + 1]
        seg_l = s_kum[seg_idx + 1] - s_kum[seg_idx]
        t = 0.0 if seg_l <= 0.0 else (s_mitt - s_kum[seg_idx]) / seg_l
        tx, ty = (b.x_m - a.x_m) / seg_l, (b.y_m - a.y_m) / seg_l
        x = a.x_m + t * (b.x_m - a.x_m)
        y = a.y_m + t * (b.y_m - a.y_m)
        z = a.z_m + t * (b.z_m - a.z_m)
        # Flytta källan med halva axelbredden mot mottagaren vinkelrätt mot körfältet.
        nx, ny = ty, -tx  # höger normal
        tecken = 1.0 if (mottagare.x_m - x) * nx + (mottagare.y_m - y) * ny >= 0.0 else -1.0
        # Avstånd vinkelrätt från källan till vägkanten på mottagarsidan (vägbredd 2 × 5 m).
        avstand_kant = max(
            0.0, KORFALTSBREDD_M - (tecken * sidoforskjutning_m + axel_forskjutning_m)
        )
        punkter.append(
            KallpunktPlan(
                x_m=x + tecken * axel_forskjutning_m * nx,
                y_m=y + tecken * axel_forskjutning_m * ny,
                z_m=z,
                langd_m=ds,
                tangent_x=tx,
                tangent_y=ty,
                avstand_till_vagkant_m=avstand_kant,
            )
        )
    return punkter


def _korsningar_med_polylinje(
    sx: float, sy: float, rx: float, ry: float, poly: tuple[Punkt3D, ...]
) -> list[tuple[float, float]]:
    """Returnerar (t, z) för alla korsningar mellan strålen S→R och en 3D-polylinje."""
    traffar: list[tuple[float, float]] = []
    for i in range(len(poly) - 1):
        a, b = poly[i], poly[i + 1]
        t = _korsning_segment(sx, sy, rx, ry, a.x_m, a.y_m, b.x_m, b.y_m)
        if t is not None:
            traffar.append((t, 0.5 * (a.z_m + b.z_m)))
    return traffar


def profil_for_strale(
    geo: KurvigVagGeometri,
    kalla: KallpunktPlan,
    mottagare: MottagarPunkt,
    kalla_hojd_m: float,
    *,
    tjock_skarm_typ: TjockSkarmTyp = "byggnad",
    forenkla: bool = True,
) -> Terrangprofil:
    """Bygger en 2D-`Terrangprofil` längs strålen från `kalla` till `mottagare`."""
    dist = math.hypot(mottagare.x_m - kalla.x_m, mottagare.y_m - kalla.y_m)
    if dist <= 0.0:
        raise BullerFel("Källa och mottagare sammanfaller.")

    # 1. Samla markhöjdspunkter från höjdkurvorna.
    mark_punkter: list[tuple[float, float]] = [(0.0, kalla.z_m), (dist, mottagare.z_mark_m)]
    for kurva in geo.hojdkurvor:
        for t, z in _korsningar_med_polylinje(
            kalla.x_m, kalla.y_m, mottagare.x_m, mottagare.y_m, kurva
        ):
            mark_punkter.append((t * dist, z))
    mark_punkter.sort(key=lambda p: p[0])

    # Rensa nära sammanfallande d-värden.
    rensade: list[tuple[float, float]] = [mark_punkter[0]]
    for d, z in mark_punkter[1:]:
        if d - rensade[-1][0] > 1.0e-3:
            rensade.append((d, z))
    if dist - rensade[-1][0] > 1.0e-3:
        rensade.append((dist, mottagare.z_mark_m))
    else:
        rensade[-1] = (dist, mottagare.z_mark_m)

    d_mark = np.array([p[0] for p in rensade], dtype=np.float64)
    z_mark = np.array([p[1] for p in rensade], dtype=np.float64)

    def markhojd(d_val: float) -> float:
        return float(np.interp(d_val, d_mark, z_mark))

    # 2. Bestäm vägkantens läge längs strålen (plan, hård vägyta σ = 20000 fram till vägkant).
    vagkant_d = min(kalla.avstand_till_vagkant_m, dist * 0.5)
    ux = (mottagare.x_m - kalla.x_m) / dist
    uy = (mottagare.y_m - kalla.y_m) / dist
    sin_vinkel = abs(ux * kalla.tangent_y - uy * kalla.tangent_x)
    if sin_vinkel > 1.0e-3:
        vagkant_d = min(kalla.avstand_till_vagkant_m / sin_vinkel, dist * 0.5)

    # 3. Lägg in vägkant (plan vägyta vid kalla.z_m) och skärmkorsningar.
    punkter: list[tuple[float, float]] = [
        p for p in rensade if p[0] <= 1.0e-6 or p[0] >= vagkant_d - 1.0e-3
    ]
    harda_intervall: list[tuple[float, float]] = [(0.0, vagkant_d)]
    if vagkant_d > 1.0e-3 and vagkant_d < dist - 1.0e-3:
        punkter.append((vagkant_d, kalla.z_m))

    # Tunn skärm (3 m hög, lodrät, hård).
    for t, z_topp in _korsningar_med_polylinje(
        kalla.x_m, kalla.y_m, mottagare.x_m, mottagare.y_m, geo.tunn_skarm
    ):
        d_s = t * dist
        if TUNN_SKARM_HALVBRED_M < d_s < dist - TUNN_SKARM_HALVBRED_M:
            z0 = markhojd(d_s)
            punkter.extend(
                [
                    (d_s - TUNN_SKARM_HALVBRED_M, z0),
                    (d_s, z_topp),
                    (d_s + TUNN_SKARM_HALVBRED_M, z0),
                ]
            )
            harda_intervall.append((d_s - TUNN_SKARM_HALVBRED_M, d_s + TUNN_SKARM_HALVBRED_M))

    # Tjock skärm (byggnad eller vall).
    topp_traff = sorted(
        t * dist
        for t, _ in _korsningar_med_polylinje(
            kalla.x_m, kalla.y_m, mottagare.x_m, mottagare.y_m, geo.tjock_skarm_topp
        )
    )
    if len(topp_traff) >= 2:
        d_t1, d_t2 = topp_traff[0], topp_traff[-1]
        z_topp = geo.tjock_skarm_topp[0].z_m
        if tjock_skarm_typ == "byggnad":
            d_b1 = max(1.0e-3, d_t1 - TUNN_SKARM_HALVBRED_M)
            d_b2 = min(dist - 1.0e-3, d_t2 + TUNN_SKARM_HALVBRED_M)
            punkter = [p for p in punkter if not (d_b1 <= p[0] <= d_b2)]
            punkter.extend(
                [
                    (d_b1, markhojd(d_b1)),
                    (d_t1, z_topp),
                    (d_t2, z_topp),
                    (d_b2, markhojd(d_b2)),
                ]
            )
            harda_intervall.append((d_b1, d_b2))
        else:
            bas_traff = sorted(
                t * dist
                for t, _ in _korsningar_med_polylinje(
                    kalla.x_m, kalla.y_m, mottagare.x_m, mottagare.y_m, geo.tjock_skarm_bas
                )
            )
            d_b1 = bas_traff[0] if bas_traff else max(1.0e-3, d_t1 - 6.0)
            d_b2 = bas_traff[-1] if len(bas_traff) >= 2 else min(dist - 1.0e-3, d_t2 + 6.0)
            d_b1 = min(d_b1, d_t1 - 1.0e-2)
            d_b2 = max(d_b2, d_t2 + 1.0e-2)
            punkter = [p for p in punkter if not (d_b1 <= p[0] <= d_b2)]
            punkter.extend(
                [
                    (d_b1, markhojd(d_b1)),
                    (d_t1, z_topp),
                    (d_t2, z_topp),
                    (d_b2, markhojd(d_b2)),
                ]
            )

    punkter.sort(key=lambda p: p[0])
    sluta: list[tuple[float, float]] = [punkter[0]]
    for d, z in punkter[1:]:
        if d - sluta[-1][0] > 1.0e-4:
            sluta.append((d, z))
    x_arr = np.array([p[0] for p in sluta], dtype=np.float64)
    z_arr = np.array([p[1] for p in sluta], dtype=np.float64)
    sigma_arr = np.full(x_arr.shape[0] - 1, SIGMA_MARK_KNSM4, dtype=np.float64)
    for i in range(x_arr.shape[0] - 1):
        mitt = 0.5 * (float(x_arr[i]) + float(x_arr[i + 1]))
        if any(a <= mitt <= b for a, b in harda_intervall):
            sigma_arr[i] = SIGMA_VAG_KNSM4
    profil = Terrangprofil(
        x_m=x_arr,
        z_m=z_arr,
        flodesresistivitet_kNsm4=sigma_arr,
        rahet_m=np.zeros_like(sigma_arr),
        kalla_hojd_m=kalla_hojd_m,
        mottagare_hojd_m=mottagare.hojd_over_mark_m,
    )
    if forenkla and profil.x_m.shape[0] > 12:
        profil = forenkla_terrang(profil, REGLER_TC_101327)
    return profil


def vindkomponent_plan_m_s(
    vind_m_s: float, vindriktning_grader_fran_norr: float, dx_m: float, dy_m: float
) -> float:
    """Vindkomponent längs utbredningsvektorn (dx, dy) för meteorologisk vindriktning φ_u.

    RL 18-18 s. 12: φ_u = 315° (från NV, blåser mot 135° SO) ger medvind (+u) mot SO
    (pos 1, 3, 4) och motvind (−u) mot NV (pos 2, 5).
    """
    dist = math.hypot(dx_m, dy_m)
    if dist <= 0.0 or vind_m_s == 0.0:
        return 0.0
    blaser_mot_rad = math.radians((vindriktning_grader_fran_norr + 180.0) % 360.0)
    wx = math.sin(blaser_mot_rad)
    wy = math.cos(blaser_mot_rad)
    return vind_m_s * ((dx_m / dist) * wx + (dy_m / dist) * wy)


def steglangd_for_position_m(position: str) -> float:
    """Källpunktsavstånd längs körfält enligt RL 18-18 s. 10."""
    bas = position.split("-")[0]
    if bas == "5":
        return 10.0
    if bas == "3":
        return 25.0
    return 50.0


def reflektorer_for_kurvig_vag(
    geo: KurvigVagGeometri, tjock_skarm_typ: TjockSkarmTyp = "byggnad"
) -> list[VertikalReflektor]:
    """Bygger vertikala reflektorer för tunn och (vid byggnad) tjock skärm (RL 18-18 s. 10)."""
    reflektorer: list[VertikalReflektor] = []
    for i in range(len(geo.tunn_skarm) - 1):
        a, b = geo.tunn_skarm[i], geo.tunn_skarm[i + 1]
        if math.hypot(b.x_m - a.x_m, b.y_m - a.y_m) > 1.0e-3:
            reflektorer.append(
                VertikalReflektor(
                    a.x_m, a.y_m, b.x_m, b.y_m, 0.0, a.z_m, reflektionsfaktor_e=1.0, ensidig=False
                )
            )
    if tjock_skarm_typ == "byggnad":
        for i in range(len(geo.tjock_skarm_topp) - 1):
            a, b = geo.tjock_skarm_topp[i], geo.tjock_skarm_topp[i + 1]
            if math.hypot(b.x_m - a.x_m, b.y_m - a.y_m) > 1.0e-3:
                reflektorer.append(
                    VertikalReflektor(
                        a.x_m,
                        a.y_m,
                        b.x_m,
                        b.y_m,
                        0.0,
                        a.z_m,
                        reflektionsfaktor_e=1.0,
                        ensidig=False,
                    )
                )
    return reflektorer


def berakna_kurvig_vag(
    karna: P2PKarna,
    emission: Emissionsmodell | EmissionsmodellMedDirektivitet,
    geo: KurvigVagGeometri,
    position: str,
    vader: Vader,
    a_vagning_db: Flyttal,
    *,
    vindriktning_grader_fran_norr: float = 315.0,
    tjock_skarm_typ: TjockSkarmTyp = "byggnad",
    forhallanden: Vagforhallanden | None = None,
    direktivitet: bool = True,
    inkludera_reflektioner: bool = True,
) -> tuple[float, Flyttal]:
    """Beräknar (LAeq_24h, Leq_24h-spektrum) för ett kurvig-väg-fall i RL 18-18 kap. 3."""
    pos_nyckel = position.split("-")[0]
    if pos_nyckel not in geo.mottagare:
        raise BullerFel(f"Okänd mottagarposition '{position}' i kurvig väg.")
    mottagare = geo.mottagare[pos_nyckel]
    steg_m = steglangd_for_position_m(position)

    # 10 000 fordon/dygn jämnt fördelade på två körfält (5 000 per körfält).
    trafik_per_korfalt = [
        Trafikflode(1, 4500.0, 90.0),
        Trafikflode(2, 250.0, 70.0),
        Trafikflode(3, 250.0, 70.0),
    ]
    period_s = 24.0 * 3600.0
    hojder = emission.delkallhojder_m
    riktad = emission if isinstance(emission, EmissionsmodellMedDirektivitet) else None
    if not direktivitet or (riktad is not None and not riktad.stoder_direktivitet):
        riktad = None
    reflektorer = reflektorer_for_kurvig_vag(geo, tjock_skarm_typ) if inkludera_reflektioner else []
    frekv = np.asarray(TERSBAND_HZ, dtype=np.float64)
    c0_m_s = 20.05 * math.sqrt(vader.temperatur_c + 273.15)

    energi = np.zeros(ANTAL_BAND, dtype=np.float64)
    for sido in (-KORFALTSBREDD_M * 0.5, KORFALTSBREDD_M * 0.5):
        kallpunkter = kallpunkter_langs_korfalt(geo.vagmitt, mottagare, sido, steg_m)
        for kp in kallpunkter:
            dx = mottagare.x_m - kp.x_m
            dy = mottagare.y_m - kp.y_m
            dist = math.hypot(dx, dy)
            v_komp = vindkomponent_plan_m_s(vader.vind_m_s, vindriktning_grader_fran_norr, dx, dy)
            vader_lokal = replace(vader, vind_m_s=v_komp)
            profiler = [
                profil_for_strale(geo, kp, mottagare, h, tjock_skarm_typ=tjock_skarm_typ)
                for h in hojder
            ]
            if riktad is not None:
                cos_phi = min(1.0, max(-1.0, (dx * kp.tangent_x + dy * kp.tangent_y) / dist))
                phi_rad = math.acos(cos_phi)
                psi = tuple(
                    math.atan2(
                        (float(p.z_m[-1]) + p.mottagare_hojd_m)
                        - (float(p.z_m[0]) + p.kalla_hojd_m),
                        float(p.x_m[-1] - p.x_m[0]),
                    )
                    for p in profiler
                )
                effekt_db = riktad.ljudeffekt_db(
                    trafik_per_korfalt,
                    kp.langd_m,
                    period_s,
                    hojder,
                    forhallanden,
                    Kallriktning(phi_rad, psi),
                )
            else:
                effekt_db = emission.ljudeffekt_db(
                    trafik_per_korfalt, kp.langd_m, period_s, hojder, forhallanden
                )
            for j, profil in enumerate(profiler):
                h_db = np.asarray(karna.overforing_db(profil, vader_lokal), dtype=np.float64)
                energi += np.power(10.0, (effekt_db[j, :] + h_db) / 10.0)

            # 1:a ordningens reflektioner mot tunn skärm och byggnadsfasader (AV 1106/07 5.20).
            for ref_idx, ref in enumerate(reflektorer):
                rg = reflektionspunkt_plan(kp.x_m, kp.y_m, mottagare.x_m, mottagare.y_m, ref)
                if rg is None:
                    continue
                # Blockering av annan vägg på samma eller annan reflektor.
                blockerad = False
                for j_idx, annan in enumerate(reflektorer):
                    if j_idx == ref_idx:
                        continue
                    if linje_korsar_segment(
                        kp.x_m,
                        kp.y_m,
                        rg.o_x_m,
                        rg.o_y_m,
                        annan.x1_m,
                        annan.y1_m,
                        annan.x2_m,
                        annan.y2_m,
                    ) or linje_korsar_segment(
                        rg.o_x_m,
                        rg.o_y_m,
                        mottagare.x_m,
                        mottagare.y_m,
                        annan.x1_m,
                        annan.y1_m,
                        annan.x2_m,
                        annan.y2_m,
                    ):
                        blockerad = True
                        break
                if blockerad:
                    continue
                d_tot = rg.d1_m + rg.d2_m
                # Längdviktad vind längs S→O och O→R (AV 1106/07 ekv. 295).
                v1 = vindkomponent_plan_m_s(
                    vader.vind_m_s,
                    vindriktning_grader_fran_norr,
                    rg.o_x_m - kp.x_m,
                    rg.o_y_m - kp.y_m,
                )
                v2 = vindkomponent_plan_m_s(
                    vader.vind_m_s,
                    vindriktning_grader_fran_norr,
                    mottagare.x_m - rg.o_x_m,
                    mottagare.y_m - rg.o_y_m,
                )
                vader_ref = replace(vader, vind_m_s=(v1 * rg.d1_m + v2 * rg.d2_m) / d_tot)
                # Direktivitet i riktning från källan mot reflexpunkten O.
                if riktad is not None:
                    ox_d, oy_d = rg.o_x_m - kp.x_m, rg.o_y_m - kp.y_m
                    cos_phi_r = min(
                        1.0, max(-1.0, (ox_d * kp.tangent_x + oy_d * kp.tangent_y) / rg.d1_m)
                    )
                    phi_r = math.acos(cos_phi_r)
                    psi_r = tuple(
                        math.atan2(
                            (mottagare.z_mark_m + mottagare.hojd_over_mark_m) - (kp.z_m + h),
                            d_tot,
                        )
                        for h in hojder
                    )
                    eff_ref = riktad.ljudeffekt_db(
                        trafik_per_korfalt,
                        kp.langd_m,
                        period_s,
                        hojder,
                        forhallanden,
                        Kallriktning(phi_r, psi_r),
                    )
                else:
                    eff_ref = effekt_db
                # Utfälld 2D-profil över plan mark (vägyta + gräsmark).
                x_r = np.array([0.0, d_tot], dtype=np.float64)
                z_r = np.array([kp.z_m, mottagare.z_mark_m], dtype=np.float64)
                sig_r = np.array([SIGMA_MARK_KNSM4], dtype=np.float64)
                rah_r = np.array([0.0], dtype=np.float64)
                for j, h in enumerate(hojder):
                    dlr = reflektionskorrektion_db(
                        frekv,
                        c0_m_s,
                        rg,
                        ref,
                        kp.z_m + h,
                        mottagare.z_mark_m + mottagare.hojd_over_mark_m,
                    )
                    if not bool(np.any(np.isfinite(dlr))):
                        continue
                    prof_r = Terrangprofil(
                        x_m=x_r,
                        z_m=z_r,
                        flodesresistivitet_kNsm4=sig_r,
                        rahet_m=rah_r,
                        kalla_hojd_m=h,
                        mottagare_hojd_m=mottagare.hojd_over_mark_m,
                    )
                    h_r = np.asarray(karna.overforing_db(prof_r, vader_ref), dtype=np.float64)
                    with np.errstate(over="ignore"):
                        energi += np.power(10.0, (eff_ref[j, :] + h_r + dlr) / 10.0)

    with np.errstate(divide="ignore"):
        leq_db = np.asarray(10.0 * np.log10(energi), dtype=np.float64)
    laeq_db = float(10.0 * np.log10(np.sum(np.power(10.0, (leq_db + a_vagning_db) / 10.0))))
    return laeq_db, leq_db
