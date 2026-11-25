"""Tester för täta 3D-kantbarriärer utan diagonalt trappstegsläckage (`tvad.lokal_troghet`)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from testsvit.k1_io import BrytlinjePost, GeoTransformInfo
from tvad.lokal_troghet import kor_2d_lokal_troghet


def _bygg_delad_bassang(
    vinkel_grader: float,
    kantsten_hojd_m: float = 0.12,
) -> tuple[
    np.ndarray[tuple[int, int], np.dtype[np.float32]],
    np.ndarray[tuple[int, int], np.dtype[np.uint8]],
    GeoTransformInfo,
    BrytlinjePost,
    np.ndarray[tuple[int, int], np.dtype[np.bool_]],
    np.ndarray[tuple[int, int], np.dtype[np.bool_]],
]:
    """Plan sluten bassäng delad i två halvor av en sned 3D-kantsten genom mitten.

    Vänster sida (norr/väst om linjen) har lägre marknivå (10,00 m) med lokal nederbörd,
    eller högre vattenstånd än höger sida (9,90 m), så minsta läckage genom barriären
    skulle omedelbart synas på den lägre sidan.
    """
    h, b = 26, 26
    dx = 1.0
    geo = GeoTransformInfo(
        x_min=150000.0,
        y_max=6580000.0 + h * dx,
        pixelstorlek_x=dx,
        pixelstorlek_y=dx,
        bredd=b,
        hojd=h,
        epsg=3011,
        dtype="float32",
    )
    # Centrum i mitten av bassängen (exakt på cellcentrum för att provocera 45°-hörnsnitt)
    cx = geo.x_min + (b // 2 + 0.5) * dx
    cy = geo.y_max - (h // 2 + 0.5) * dx
    theta = math.radians(vinkel_grader)
    tx, ty = math.cos(theta), math.sin(theta)
    # Enhetsnormal åt "vänster" (ena sidan)
    nx_n, ny_n = -ty, tx

    # 3D-kantsten som sträcker sig långt utanför bassängens kanter
    p0 = (cx - 30.0 * tx, cy - 30.0 * ty)
    p1 = (cx + 30.0 * tx, cy + 30.0 * ty)
    z_kron = 10.0 + kantsten_hojd_m
    bryt = BrytlinjePost(
        typ="kantsten",
        hojd_m=kantsten_hojd_m,
        koordinater=(p0, p1),
        z_kron_rh2000=(z_kron, z_kron),
        kalla="baskarta_3d",
    )

    rr, cc = np.mgrid[0:h, 0:b]
    ee = geo.x_min + (cc + 0.5) * dx
    nn = geo.y_max - (rr + 0.5) * dx
    signed_dist = (ee - cx) * nx_n + (nn - cy) * ny_n

    # Byggnad runt hela ytterkanten (tät ram) så inget vatten försvinner över randen
    markslag = np.full((h, b), 1, dtype=np.uint8)  # hårdgjort
    markslag[0, :] = 9
    markslag[-1, :] = 9
    markslag[:, 0] = 9
    markslag[:, -1] = 9

    mark_z = np.full((h, b), 10.0, dtype=np.float32)
    inre = markslag != 9
    sida_v = inre & (signed_dist > 1e-6)
    sida_h = inre & (signed_dist < -1e-6)
    # Sänk mottagarsidan (höger) 0,05 m så att eventuellt läckage drivs starkt över kanten
    mark_z[sida_h] = 9.95
    # Celler exakt på linjen tillhör vänster sida (på 10,00 m)
    return mark_z, markslag, geo, bryt, sida_v, sida_h


@pytest.mark.parametrize("vinkel", [20.0, 45.0])
def test_sned_kantsten_noll_diagonalt_lackage_och_overfall(vinkel: float) -> None:
    """20° och 45° 3D-kantsten (0,12 m): 0,0 m³ läckage vid 0,08 m, jämnt överfall vid 0,15 m."""
    mark_z, markslag, geo, bryt, sida_v, sida_h = _bygg_delad_bassang(vinkel, kantsten_hojd_m=0.12)

    # Fall 1: 0,08 m vatten på vänster sida (under 0,12 m kantsten) -> noll läckage till höger!
    # Vi låter det regna 80 mm jämnt på hela bassängen MEN höjer höger sida? Nej, ännu renare:
    # sätt höger sida till mark=10,00 m och sänk vänster sida? Vi vill ha vatten BARA på vänster.
    # Med konstant regn på hela bassängen + en 0,08 m djup ficka på vänster sida skulle regn
    # hamna på höger sida också. I stället lägger vi en 0,08 m vattenpelare på vänster sida
    # genom att markera höger sida... Nej, kor_2d_lokal_troghet startar torrt och tar regn!
    # Hur får vi vatten bara på vänster sida? Med mark_z[sida_v] = 10.00, och ett takvatten
    # eller enklare: lägg ett sluttande plan på vänster sida? Nej, med regn på hela ytan:
    # vänta – om vi använder `mark_z`: på höger sida är mark_z = 9.95, på vänster = 10.00.
    # Om vi har 0,08 m regn på hela ytan kommer höger sida ha 0,08 m (vattenyta 10,03) och
    # vänster sida 0,08 m (vattenyta 10,08). Skillnad 0,05 m i vattenyta, men BÅDA < 10,12 m!
    # Då måste volymen på höger sida vara EXAKT lika med regnvolymen som föll på höger sida
    # (0,000000 m³ utbyte över kantstenen)!
    regn_80mm_mm_h = 80.0 * 36.0  # 80 mm på 100 s (= 2880 mm/h)
    res_under = kor_2d_lokal_troghet(
        mark_z,
        markslag,
        geo,
        [0.0, 100.0],
        [regn_80mm_mm_h, regn_80mm_mm_h],
        slut_tid_s=140.0,
        oppna_rander=False,
        brytlinjer=[bryt],
    )
    assert res_under.volymfel_rel < 1e-3

    # Volymen på höger sida ska vara exakt det regn som föll på höger sida (0,0 m³ läckage!)
    cell_area = float(geo.pixelstorlek_x * geo.pixelstorlek_y)
    forvantad_v_h = 0.08 * float(np.count_nonzero(sida_h)) * cell_area
    faktisk_v_h = float(np.sum(res_under.slut_djup_m[sida_h].astype(np.float64))) * cell_area
    lackage_m3 = abs(faktisk_v_h - forvantad_v_h)
    assert lackage_m3 < 1e-6, f"Diagonalt läckage vid {vinkel}°: {lackage_m3:.6e} m³"

    # Fall 2: 0,15 m vatten (över 0,12 m kantsten -> vattennivå 10,15 > 10,12 på vänster sida)
    # -> vatten spiller över till höger sida (vattennivå 10,10) med volymfel < 0,1 %!
    regn_150mm_mm_h = 150.0 * 36.0  # 150 mm på 100 s
    res_over = kor_2d_lokal_troghet(
        mark_z,
        markslag,
        geo,
        [0.0, 100.0],
        [regn_150mm_mm_h, regn_150mm_mm_h],
        slut_tid_s=160.0,
        oppna_rander=False,
        brytlinjer=[bryt],
    )
    assert res_over.volymfel_rel < 1e-3
    forvantad_v_h_over = 0.15 * float(np.count_nonzero(sida_h)) * cell_area
    faktisk_v_h_over = float(np.sum(res_over.slut_djup_m[sida_h].astype(np.float64))) * cell_area
    overfall_m3 = faktisk_v_h_over - forvantad_v_h_over
    assert overfall_m3 > 1.0, (
        f"Förväntade tydligt överfall över {vinkel}° kantsten vid 0,15 m, fick {overfall_m3:.3f} m³"
    )
