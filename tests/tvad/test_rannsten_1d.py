"""Tester för 1D-rännstenslänk kopplad till 2D (`tvad.rannsten_1d`), inkl. A5b."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from testsvit.k1_io import BrytlinjePost, GeoTransformInfo, las_gpkg_tabell
from tvad.rannsten_1d import (
    Kopplad1D2DResultat,
    diskretisera_rannsten_1d,
    exportera_rannsten_flode_geojson,
    exportera_rannsten_flode_gpkg,
    kor_kopplad_1d_2d,
)


def bygg_a5b_sned_gata(
    vinkel_grader: float,
    *,
    langd_m: float = 100.0,
    korbana_bredd_m: float = 3.0,
    gangbana_bredd_m: float = 4.0,
    lutning_langs: float = 0.01,
    tvarfall: float = 0.025,
    kantsten_hojd_m: float = 0.12,
    infart_s0_m: float = 60.0,
    infart_s1_m: float = 64.0,
    infart_hojd_m: float = 0.02,
    cell_m: float = 0.5,
) -> tuple[
    np.ndarray[tuple[int, int], np.dtype[np.float32]],
    np.ndarray[tuple[int, int], np.dtype[np.uint8]],
    np.ndarray[tuple[int, int], np.dtype[np.bool_]],
    GeoTransformInfo,
    list[BrytlinjePost],
]:
    """Bygger syntetisk 100 m lutande gata i godtycklig vinkel (0°/20°/45°) med 3D-kantsten.

    Körbana (bredd 6 m, tvärfall 2,5 % mot rännstenen) på vänster sida om kantstenen,
    gångbana/gård (bredd 5 m, lägre lutning bort från gatan) på höger sida, inramad av
    byggnader i det omgivande rutnätet så att avrinningsarean är konstant oavsett vinkel.
    """
    theta = math.radians(vinkel_grader)
    tx, ty = math.cos(theta), math.sin(theta)
    nx_v, ny_v = -ty, tx  # vänsternormal = körbanesidan

    # Beräkna tillräckligt stort rutnät med marginal runt hela gatan
    halv_s = 0.5 * langd_m + 10.0
    halv_t = max(korbana_bredd_m, gangbana_bredd_m) + 10.0
    span_x = abs(halv_s * tx) + abs(halv_t * ty)
    span_y = abs(halv_s * ty) + abs(halv_t * tx)
    bredd = int(math.ceil(2.0 * span_x / cell_m)) + 4
    hojd = int(math.ceil(2.0 * span_y / cell_m)) + 4

    cx = 150100.0
    cy = 6580100.0
    geo = GeoTransformInfo(
        x_min=cx - 0.5 * bredd * cell_m,
        y_max=cy + 0.5 * hojd * cell_m,
        pixelstorlek_x=cell_m,
        pixelstorlek_y=cell_m,
        bredd=bredd,
        hojd=hojd,
        epsg=3011,
        dtype="float32",
    )

    # Startpunkt (s = 0) och slutpunkt (s = langd_m) längs kantstenslinjen
    e_start = cx - 0.5 * langd_m * tx
    n_start = cy - 0.5 * langd_m * ty

    rr, cc = np.mgrid[0:hojd, 0:bredd]
    ee = geo.x_min + (cc + 0.5) * cell_m
    nn = geo.y_max - (rr + 0.5) * cell_m
    s_coord = (ee - e_start) * tx + (nn - n_start) * ty
    d_coord = (ee - e_start) * nx_v + (nn - n_start) * ny_v  # >0 = körbana, <0 = gångbana

    markslag = np.full((hojd, bredd), 9, dtype=np.uint8)  # byggnad utanför gatukorridoren
    i_langd = (s_coord >= 0.0) & (s_coord <= langd_m)
    pa_korbana = i_langd & (d_coord > 0.0) & (d_coord <= korbana_bredd_m)
    pa_gangbana = i_langd & (d_coord <= 0.0) & (d_coord >= -gangbana_bredd_m)
    markslag[pa_korbana] = 1  # hårdgjort (körbana)
    markslag[pa_gangbana] = 2  # grönyta/gångbana

    # Höjdfält: körbanan lutar längs gatan (1 %) och tvärs in mot rännstenen (2,5 %)
    z_rannsten = 20.0 - lutning_langs * s_coord
    mark_z = np.full((hojd, bredd), 25.0, dtype=np.float32)
    mark_z[pa_korbana] = (z_rannsten[pa_korbana] + tvarfall * d_coord[pa_korbana]).astype(
        np.float32
    )
    # Gångbanan/gården ligger något lägre bort från kantstenen så överfall vid infart rinner bort
    mark_z[pa_gangbana] = (z_rannsten[pa_gangbana] - 0.02 + 0.01 * d_coord[pa_gangbana]).astype(
        np.float32
    )

    # Regn enbart i övre halvan av körbanan (s in [2, 35] m) så vattnet måste färdas
    # längs rännstenen ned till den sänkta infarten vid s = 60..64 m -> mäter gångtid tydligt!
    regn_mask = pa_korbana & (s_coord >= 2.0) & (s_coord <= 35.0)

    def _pt(s_m: float) -> tuple[float, float]:
        return (e_start + s_m * tx, n_start + s_m * ty)

    def _zb(s_m: float) -> float:
        return 20.0 - lutning_langs * s_m

    brytlinjer = [
        BrytlinjePost(
            typ="kantsten",
            hojd_m=kantsten_hojd_m,
            koordinater=(_pt(0.0), _pt(infart_s0_m)),
            z_kron_rh2000=(_zb(0.0) + kantsten_hojd_m, _zb(infart_s0_m) + kantsten_hojd_m),
            z_botten_rh2000=(_zb(0.0), _zb(infart_s0_m)),
            tvarfall=tvarfall,
            kalla="baskarta_3d",
        ),
        BrytlinjePost(
            typ="sankt_kantsten",
            hojd_m=infart_hojd_m,
            koordinater=(_pt(infart_s0_m), _pt(infart_s1_m)),
            z_kron_rh2000=(_zb(infart_s0_m) + infart_hojd_m, _zb(infart_s1_m) + infart_hojd_m),
            z_botten_rh2000=(_zb(infart_s0_m), _zb(infart_s1_m)),
            tvarfall=tvarfall,
            kalla="baskarta_3d",
        ),
        BrytlinjePost(
            typ="kantsten",
            hojd_m=kantsten_hojd_m,
            koordinater=(_pt(infart_s1_m), _pt(langd_m)),
            z_kron_rh2000=(_zb(infart_s1_m) + kantsten_hojd_m, _zb(langd_m) + kantsten_hojd_m),
            z_botten_rh2000=(_zb(infart_s1_m), _zb(langd_m)),
            tvarfall=tvarfall,
            kalla="baskarta_3d",
        ),
    ]
    return mark_z, markslag, regn_mask, geo, brytlinjer


def kor_a5b_vinkel(vinkel_grader: float) -> tuple[Kopplad1D2DResultat, float, float, float]:
    """Kör A5b för given vinkel och returnerar (resultat, gångtid_s, maxdjup_1d_m, infart_andel)."""
    mark_z, markslag, regn_mask, geo, brytlinjer = bygg_a5b_sned_gata(vinkel_grader)
    lank = diskretisera_rannsten_1d(
        brytlinjer, mark_z, markslag, geo, ds_mal_m=2.0, korbana_sida="vanster"
    )
    # Normalisera regnintensiteten till exakt 6,0 m³ totalt inflöde (99 m² * 60,6 mm) oavsett
    # hur många helceller den vridna rektangeln råkar täcka i 2D-rutnätet.
    regn_area_m2 = float(np.count_nonzero(regn_mask)) * float(
        geo.pixelstorlek_x * geo.pixelstorlek_y
    )
    intens_mm_h = (6.0 / regn_area_m2) * (3_600_000.0 / 90.0)
    res = kor_kopplad_1d_2d(
        mark_z,
        markslag,
        geo,
        [0.0, 90.0],
        [intens_mm_h, intens_mm_h],
        lank,
        brytlinjer=brytlinjer,
        slut_tid_s=180.0,
        oppna_rander=False,
        regn_mask=regn_mask,
        ankomst_tr_djup_m=0.02,
    )
    infart_idx = [i for i, s in enumerate(lank.segment) if s.ar_sankt]
    gangtid_s = float(np.nanmin(res.ankomst_tid_1d_s[infart_idx]))
    maxdjup_m = float(np.max(res.max_djup_1d_m))
    infart_andel = res.overfall_sankt_m3 / max(res.total_regn_m3, 1e-12)
    return res, gangtid_s, maxdjup_m, infart_andel


def test_a5b_rotationsinvarians_0_20_45_och_volymbalans(tmp_path: Path) -> None:
    """A5b: 0°, 20° och 45° ska överensstämma inom < 5 % med volymfel < 0,1 %."""
    utfall: dict[float, tuple[Kopplad1D2DResultat, float, float, float]] = {}
    for vinkel in (0.0, 20.0, 45.0):
        res, gangtid, maxdjup, andel = kor_a5b_vinkel(vinkel)
        assert res.volymfel_rel < 1e-3, f"Volymfel {res.volymfel_rel:.4%} vid {vinkel}°"
        # Inget vatten får spilla över den 12 cm höga kantstenen, bara via den sänkta infarten!
        assert res.overfall_hog_m3 < 1e-6, f"Överfall över hög kantsten vid {vinkel}°"
        assert res.overfall_sankt_m3 > 0.5, f"Inget överfall via infarten vid {vinkel}°"
        assert math.isfinite(gangtid) and gangtid > 10.0
        utfall[vinkel] = (res, gangtid, maxdjup, andel)

    _, t0, h0, a0 = utfall[0.0]
    for vinkel in (20.0, 45.0):
        _, tv, hv, av = utfall[vinkel]
        rel_t = abs(tv - t0) / t0
        rel_h = abs(hv - h0) / h0
        rel_a = abs(av - a0) / a0
        assert rel_t < 0.05, f"Gångtid avviker {rel_t:.2%} vid {vinkel}° ({tv:.2f} vs {t0:.2f} s)"
        assert rel_h < 0.05, f"Maxdjup avviker {rel_h:.2%} vid {vinkel}° ({hv:.4f} vs {h0:.4f} m)"
        assert (
            rel_a < 0.05
        ), f"Infartsandel avviker {rel_a:.2%} vid {vinkel}° ({av:.4f} vs {a0:.4f})"

    # Verifiera export av släta 3D-vektorlinjer (GeoJSON + GeoPackage)
    res_20 = utfall[20.0][0]
    gj_fil = exportera_rannsten_flode_geojson(res_20, tmp_path / "rannsten_flode.geojson")
    gj = json.loads(gj_fil.read_text(encoding="utf-8"))
    assert len(gj["features"]) == res_20.lank.antal

    gpkg_fil = exportera_rannsten_flode_gpkg(res_20, tmp_path / "rannsten_flode.gpkg")
    srs_id, rader = las_gpkg_tabell(gpkg_fil, "rannsten_flode")
    assert srs_id == 3011
    assert len(rader) == res_20.lank.antal


def test_a5_och_a6_med_1d_rannsten_stannar_och_lamnar_vid_infart() -> None:
    """Verifierar A5 (hel 12 cm kantsten: 0 överfall till gård) och A6 (sänkt infart)."""
    # A5-motsvarighet: hel 12 cm kantsten utan sänkt infart (infart_hojd_m = 0.12)
    mz5, ms5, rm5, geo5, br5 = bygg_a5b_sned_gata(
        0.0, infart_hojd_m=0.12, langd_m=60.0, infart_s0_m=30.0, infart_s1_m=34.0
    )
    # Markera alla segment som icke-sänkta kantstenar
    br5_hel = [
        BrytlinjePost(
            typ="kantsten",
            hojd_m=0.12,
            koordinater=p.koordinater,
            z_kron_rh2000=p.z_kron_rh2000,
            z_botten_rh2000=p.z_botten_rh2000,
            tvarfall=p.tvarfall,
            kalla="baskarta_3d",
        )
        for p in br5
    ]
    lank5 = diskretisera_rannsten_1d(br5_hel, mz5, ms5, geo5, ds_mal_m=2.0, korbana_sida="vanster")
    res5 = kor_kopplad_1d_2d(
        mz5,
        ms5,
        geo5,
        [0.0, 60.0],
        [1800.0, 1800.0],
        lank5,
        brytlinjer=br5_hel,
        slut_tid_s=100.0,
        oppna_rander=False,
        regn_mask=rm5,
    )
    assert res5.volymfel_rel < 1e-3
    assert float(np.sum(res5.overfall_gangbana_m3)) == 0.0
    assert float(np.max(res5.max_djup_1d_m)) > 0.02

    # A6-motsvarighet: samma gata men med 4 m sänkt infart (+0,02 m) -> lämnar exakt vid infarten
    mz6, ms6, rm6, geo6, br6 = bygg_a5b_sned_gata(
        0.0, infart_hojd_m=0.02, langd_m=60.0, infart_s0_m=30.0, infart_s1_m=34.0
    )
    lank6 = diskretisera_rannsten_1d(br6, mz6, ms6, geo6, ds_mal_m=2.0, korbana_sida="vanster")
    res6 = kor_kopplad_1d_2d(
        mz6,
        ms6,
        geo6,
        [0.0, 60.0],
        [1800.0, 1800.0],
        lank6,
        brytlinjer=br6,
        slut_tid_s=100.0,
        oppna_rander=False,
        regn_mask=rm6,
    )
    assert res6.volymfel_rel < 1e-3
    assert res6.overfall_hog_m3 == 0.0
    assert res6.overfall_sankt_m3 > 0.2
