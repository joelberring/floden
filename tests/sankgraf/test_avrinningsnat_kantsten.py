"""Tester för kantstensmedvetna och kartografiskt mjuka flödesvägar (Etapp 1B / PR-B).

Verifierar:
1. Bitidentisk paritet mellan Python och Rust (`sankgraf_karna`) med kantbarriärer på:
   (a) A5-kantsten (`testsvit/A5_gata_kantsten` + lutande gata med 0 läckage in på lägre gård),
   (b) A6 sänkt kantsten/infart (`testsvit/A6_sankt_kantsten_infart`, utflöde genom infarten),
   (c) 20°-roterad (`testsvit/A5b_sned_gata_3d` + syntetisk 20°-gata) och 45°-roterad kantsten
       (0 diagonalt läckage!).
2. Rännstenssnäppning: stråk parallella med kantsten (`<= 1,5 m`, `|cos θ| >= 0,7`) projiceras till
   `0,35 m` på flödessidan och märks `typ == "rannsten"`; fria stråk blir `typ == "markstrak"`.
3. Längdviktad Taubin-utjämning med låsta noder och kollisionsvakt:
   - Källpunkt, sammanflöden (`inflod >= 2`) och utlopp ligger kvar exakt på sina cellcentra.
   - Ingen punkt eller linjesegment hamnar i en inaktiv byggnadscell eller korsar en kantsten/mur.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import pytest

from sankgraf.avrinningsnat import (
    _fyll_och_riktning_python,
    _segment_korsar,
    _segment_korsar_strikt,
    avrinningsnat_ur_falt,
    berakna_avrinningsfalt,
    fyll_och_riktning,
    strackor_fran_falt,
)
from sankgraf.hierarki import _berakna_kantbarriarer
from sankgraf.rust_brygga import SankGrafRustFel, hitta_sankgraf_karna
from sankgraf.typer import MARKSLAG_BYGGNAD, las_brytlinjer_fran_gpkg
from testsvit.k1_io import BrytlinjePost, GeoTransformInfo, las_geotiff

REPO_ROT = Path(__file__).resolve().parents[2]
TESTSVIT_ROT = REPO_ROT / "testsvit"


def _krav_eller_skip_rust_binar() -> Path:
    krav = (
        os.environ.get("FLODEN_KRAV_RUST_SANKGRAF", "").strip().lower() in ("1", "true", "ja")
        or os.environ.get("CI", "").strip().lower() == "true"
    )
    try:
        return hitta_sankgraf_karna()
    except SankGrafRustFel:
        if krav:
            pytest.fail("sankgraf_karna saknas trots CI / FLODEN_KRAV_RUST_SANKGRAF=1.")
        pytest.skip("sankgraf_karna är inte byggd lokalt")


def _geo(
    hojd: int,
    bredd: int,
    dx: float = 0.25,
    x0: float = 150000.0,
    y1: float = 6580000.0,
) -> GeoTransformInfo:
    return GeoTransformInfo(
        bredd=bredd,
        hojd=hojd,
        x_min=x0,
        y_max=y1,
        pixelstorlek_x=dx,
        pixelstorlek_y=dx,
        epsg=3011,
        dtype="float64",
    )


def _lutande_gata_med_kantsten(
    *,
    hojd: int = 60,
    bredd: int = 80,
    dx: float = 0.25,
) -> tuple[np.ndarray, np.ndarray, GeoTransformInfo, tuple[BrytlinjePost, ...], int]:
    """Krönt gata (r=12..35) med längslutning österut och 12 cm sydlig kantsten mot lägre gård."""
    geo = _geo(hojd, bredd, dx=dx)
    r, c = np.mgrid[0:hojd, 0:bredd].astype(np.float64)
    rad_kant = 35  # sista gaturaden (rännstenen); r >= 36 är lägre södra gården
    z = np.empty((hojd, bredd), dtype=np.float64)
    # Norra slänten lutar mot rännstenen vid r=35; längslutning mot öster (ökande c)
    gata_mask = r <= rad_kant
    z[gata_mask] = 10.0 + 0.015 * (rad_kant - r[gata_mask]) + 0.004 * (bredd - 1 - c[gata_mask])
    # Södra gården är klart lägre (9.4 m) så utan kantsten läcker allt rakt söderut
    z[~gata_mask] = 9.4 - 0.01 * (r[~gata_mask] - (rad_kant + 1))

    y_kant = geo.y_max - (rad_kant + 1) * dx
    kantsten = BrytlinjePost(
        typ="kantsten",
        hojd_m=0.12,
        koordinater=((geo.x_min, y_kant), (geo.x_max, y_kant)),
    )
    aktiv = np.ones((hojd, bredd), dtype=np.bool_)
    return z, aktiv, geo, (kantsten,), rad_kant


def _roterad_gata_med_kantsten(
    vinkel_deg: float,
    *,
    hojd: int = 80,
    bredd: int = 80,
    dx: float = 0.25,
    offset_m: float = 0.0,
) -> tuple[
    np.ndarray,
    np.ndarray,
    GeoTransformInfo,
    tuple[BrytlinjePost, ...],
    np.ndarray,
    np.ndarray,
]:
    """Lutande gata roterad `vinkel_deg` med 12 cm kantsten mot en lägre gård på sydöstra sidan."""
    geo = _geo(hojd, bredd, dx=dx)
    rr, cc = np.mgrid[0:hojd, 0:bredd].astype(np.float64)
    xs = geo.x_min + (cc + 0.5) * dx
    ys = geo.y_max - (rr + 0.5) * dx
    cx = geo.x_min + 0.5 * bredd * dx
    cy = geo.y_max - 0.5 * hojd * dx + offset_m

    rad = math.radians(vinkel_deg)
    tx, ty = math.cos(rad), math.sin(rad)
    # Normal pekande mot gatusidan ("norr/uppåt" relativt gatan)
    nx, ny = -ty, tx

    # Längskoordinat `s` (ökar i flödesriktning) och tvärkoordinat `d` (>0 gata, <0 lägre gård)
    s = (xs - cx) * tx + (ys - cy) * ty
    d = (xs - cx) * nx + (ys - cy) * ny

    ar_gata = d > 1e-6
    ar_gard = d < -1e-6
    z = np.where(
        d >= 0.0,
        10.2 + 0.025 * np.maximum(d, 0.0) - 0.015 * s,
        9.4 + 0.015 * d - 0.005 * s,
    )
    l_halv = max(bredd, hojd) * dx
    kantsten = BrytlinjePost(
        typ="kantsten",
        hojd_m=0.12,
        koordinater=(
            (cx - l_halv * tx, cy - l_halv * ty),
            (cx + l_halv * tx, cy + l_halv * ty),
        ),
    )
    aktiv = np.ones((hojd, bredd), dtype=np.bool_)
    return z, aktiv, geo, (kantsten,), ar_gata, ar_gard


def test_a5_lutande_gata_noll_lackage_och_bitidentisk_rust_paritet() -> None:
    """(a) A5-kantsten: 0 läckage in på lägre gård och bitidentisk Python vs Rust."""
    _krav_eller_skip_rust_binar()
    z, aktiv, geo, bryt, rad_kant = _lutande_gata_med_kantsten()
    h, b = z.shape
    barriarer = _berakna_kantbarriarer(z.ravel(), aktiv.ravel(), geo, bryt)
    assert len(barriarer) == b

    # Utan kantsten läcker rännstenen rakt in på den lägre gården
    flow_utan, _ = fyll_och_riktning(z, aktiv, anvand_rust=False)
    lack_utan = sum(
        1 for c in range(1, b - 1) if (int(flow_utan[rad_kant * b + c]) // b) > rad_kant
    )
    assert lack_utan == b - 2

    # Med kantsten: 0 läckage till södra gården, allt rinner österut längs rännstenen!
    flow_py, ordn_py = fyll_och_riktning(z, aktiv, kantbarriarer=barriarer, anvand_rust=False)
    flow_rs, ordn_rs = fyll_och_riktning(z, aktiv, kantbarriarer=barriarer, anvand_rust=True)
    np.testing.assert_array_equal(flow_rs, flow_py)
    np.testing.assert_array_equal(ordn_rs, ordn_py)

    for c in range(1, b - 1):
        mal = int(flow_py[rad_kant * b + c])
        assert mal >= 0
        mr, mc = divmod(mal, b)
        assert mr == rad_kant, f"kol {c} läckte över kantsten till rad {mr}"
        assert mc == c + 1, f"kol {c} borde följa rännstenen österut"

    # Även verkliga testsvit/A5_gata_kantsten ska vara 100 % bitidentisk Python vs Rust
    a5_ind = TESTSVIT_ROT / "A5_gata_kantsten" / "indata"
    z_a5, geo_a5 = las_geotiff(a5_ind / "mark.tif")
    slag_a5, _ = las_geotiff(a5_ind / "markslag.tif")
    bryt_a5 = las_brytlinjer_fran_gpkg(a5_ind / "brytlinjer.gpkg")
    z_a5_f64 = np.asarray(z_a5, dtype=np.float64)
    akt_a5 = np.asarray(slag_a5 != MARKSLAG_BYGGNAD, dtype=np.bool_) & np.isfinite(z_a5_f64)
    f_py, o_py, a_py = berakna_avrinningsfalt(
        z_a5_f64,
        akt_a5,
        None,
        pixel_m=float(geo_a5.pixelstorlek_x),
        x_min=geo_a5.x_min,
        y_max=geo_a5.y_max,
        brytlinjer=bryt_a5,
        anvand_rust=False,
    )
    f_rs, o_rs, a_rs = berakna_avrinningsfalt(
        z_a5_f64,
        akt_a5,
        None,
        pixel_m=float(geo_a5.pixelstorlek_x),
        x_min=geo_a5.x_min,
        y_max=geo_a5.y_max,
        brytlinjer=bryt_a5,
        anvand_rust=True,
    )
    np.testing.assert_array_equal(f_rs, f_py)
    np.testing.assert_array_equal(o_rs, o_py)
    np.testing.assert_allclose(a_rs, a_py, rtol=0.0, atol=1e-12)


def test_a6_sankt_kantsten_infart_bryter_ut_exakt_genom_infarten() -> None:
    """(b) A6 sänkt kantsten/infart: utflöde exakt genom [150508.5, 150511.5], Python == Rust."""
    _krav_eller_skip_rust_binar()
    a6_ind = TESTSVIT_ROT / "A6_sankt_kantsten_infart" / "indata"
    z_a6, geo_a6 = las_geotiff(a6_ind / "mark.tif")
    slag_a6, _ = las_geotiff(a6_ind / "markslag.tif")
    bryt_a6 = las_brytlinjer_fran_gpkg(a6_ind / "brytlinjer.gpkg")
    z_f64 = np.asarray(z_a6, dtype=np.float64)
    akt = np.asarray(slag_a6 != MARKSLAG_BYGGNAD, dtype=np.bool_) & np.isfinite(z_f64)
    h, b = z_f64.shape

    f_py, o_py, a_py = berakna_avrinningsfalt(
        z_f64,
        akt,
        None,
        pixel_m=float(geo_a6.pixelstorlek_x),
        x_min=geo_a6.x_min,
        y_max=geo_a6.y_max,
        brytlinjer=bryt_a6,
        anvand_rust=False,
    )
    f_rs, o_rs, a_rs = berakna_avrinningsfalt(
        z_f64,
        akt,
        None,
        pixel_m=float(geo_a6.pixelstorlek_x),
        x_min=geo_a6.x_min,
        y_max=geo_a6.y_max,
        brytlinjer=bryt_a6,
        anvand_rust=True,
    )
    np.testing.assert_array_equal(f_rs, f_py)
    np.testing.assert_array_equal(o_rs, o_py)
    np.testing.assert_allclose(a_rs, a_py, rtol=0.0, atol=1e-12)

    # Varje inre rännstenscell längs södra kantstenen (rad 43, kol 2..77) måste korsa till rad 44
    # inuti den sänkta infarten [150508.5, 150511.5]
    utbrotts_x: list[float] = []
    for start_c in range(2, b - 2):
        curr = 43 * b + start_c
        for _ in range(200):
            nxt = int(f_py[curr])
            assert nxt >= 0, f"kol {start_c} nådde inte infarten"
            nr, nc = divmod(nxt, b)
            if nr >= 44:
                x_hit = geo_a6.x_min + (nc + 0.5) * geo_a6.pixelstorlek_x
                assert 150508.5 <= x_hit <= 150511.5, (
                    f"kol {start_c} läckte över kantsten vid x={x_hit:.2f} utanför infarten"
                )
                utbrotts_x.append(x_hit)
                break
            curr = nxt
    assert len(utbrotts_x) == b - 4


@pytest.mark.parametrize(
    ("vinkel_deg", "offset_m"),
    [(20.0, 0.0), (45.0, 0.0), (45.0, 0.07)],
)
def test_roterad_kantsten_20_och_45_grader_noll_diagonalt_lackage(
    vinkel_deg: float, offset_m: float
) -> None:
    """(c) 20°- och 45°-roterad kantsten: 0 diagonalt läckage och bitidentisk Python vs Rust."""
    _krav_eller_skip_rust_binar()
    z, aktiv, geo, bryt, ar_gata, ar_gard = _roterad_gata_med_kantsten(
        vinkel_deg, offset_m=offset_m
    )
    h, b = z.shape

    f_py, o_py, a_py = berakna_avrinningsfalt(
        z,
        aktiv,
        None,
        pixel_m=float(geo.pixelstorlek_x),
        x_min=geo.x_min,
        y_max=geo.y_max,
        brytlinjer=bryt,
        anvand_rust=False,
    )
    f_rs, o_rs, a_rs = berakna_avrinningsfalt(
        z,
        aktiv,
        None,
        pixel_m=float(geo.pixelstorlek_x),
        x_min=geo.x_min,
        y_max=geo.y_max,
        brytlinjer=bryt,
        anvand_rust=True,
    )
    np.testing.assert_array_equal(f_rs, f_py)
    np.testing.assert_array_equal(o_rs, o_py)
    np.testing.assert_allclose(a_rs, a_py, rtol=0.0, atol=1e-12)

    # Kontrollera att INTE EN ENDA inre cell strikt på gatusidan rinner (ortogonalt eller diagonalt)
    # över till gårdssidan!
    for r in range(1, h - 1):
        for c in range(1, b - 1):
            if ar_gata[r, c]:
                mal = int(f_py[r * b + c])
                assert mal >= 0
                mr, mc = divmod(mal, b)
                assert not ar_gard[mr, mc], (
                    f"{vinkel_deg}° (off={offset_m}): gatucell ({r},{c}) läckte till ({mr},{mc})"
                )


def test_a5b_sned_gata_3d_bitidentisk_rust_paritet() -> None:
    """Verkliga testsvit/A5b_sned_gata_3d (20° 3D-kantsten + infart): bitidentisk Python vs Rust."""
    _krav_eller_skip_rust_binar()
    a5b_ind = TESTSVIT_ROT / "A5b_sned_gata_3d" / "indata"
    z_raw, geo = las_geotiff(a5b_ind / "mark.tif")
    slag_raw, _ = las_geotiff(a5b_ind / "markslag.tif")
    bryt = las_brytlinjer_fran_gpkg(a5b_ind / "brytlinjer.gpkg")
    z = np.asarray(z_raw, dtype=np.float64)
    aktiv = np.asarray(slag_raw != MARKSLAG_BYGGNAD, dtype=np.bool_) & np.isfinite(z)

    f_py, o_py, a_py = berakna_avrinningsfalt(
        z,
        aktiv,
        None,
        pixel_m=float(geo.pixelstorlek_x),
        x_min=geo.x_min,
        y_max=geo.y_max,
        brytlinjer=bryt,
        anvand_rust=False,
    )
    f_rs, o_rs, a_rs = berakna_avrinningsfalt(
        z,
        aktiv,
        None,
        pixel_m=float(geo.pixelstorlek_x),
        x_min=geo.x_min,
        y_max=geo.y_max,
        brytlinjer=bryt,
        anvand_rust=True,
    )
    np.testing.assert_array_equal(f_rs, f_py)
    np.testing.assert_array_equal(o_rs, o_py)
    np.testing.assert_allclose(a_rs, a_py, rtol=0.0, atol=1e-12)


def test_rannstenssnappning_och_typ_markning_pa_lutande_gata() -> None:
    """Stråk parallella längs kantsten projiceras till 0,35 m på flödessidan och blir `rannsten`."""
    z, aktiv, geo, bryt, rad_kant = _lutande_gata_med_kantsten(hojd=60, bredd=120, dx=0.25)
    y_kant = geo.y_max - (rad_kant + 1) * geo.pixelstorlek_x

    strackor = avrinningsnat_ur_falt(
        z,
        aktiv,
        None,
        x_min=geo.x_min,
        y_max=geo.y_max,
        pixel_m=float(geo.pixelstorlek_x),
        min_yta_m2=25.0,
        brytlinjer=bryt,
        anvand_rust=False,
    )
    rannstenar = [s for s in strackor if s.typ == "rannsten"]
    assert rannstenar, "Förväntade minst en sträcka märkt typ='rannsten'"
    huvud = max(rannstenar, key=lambda s: s.tillrinning_m2)
    assert huvud.langd_m > 10.0

    # Alla inre punkter ska ligga på körbanesidan (y > y_kant) nära 0,35 m offset!
    inre_y = [py for _, py in huvud.koordinater[1:-1]]
    assert inre_y, "Förväntade inre punkter längs rännstenen"
    for py in inre_y:
        assert py > y_kant, "Snäppad rännsten får aldrig hamna på gårdssidan av kantstenen"
        assert abs((py - y_kant) - 0.35) < 0.05, (
            f"Förväntade ~0,35 m offset från kantsten, fick {py - y_kant:.3f} m"
        )

    # Utan kantsten blir stråket `markstrak`
    z_dal = 0.05 * np.abs(np.arange(41)[None, :] - 20.0) + 0.02 * (80 - np.arange(80)[:, None])
    str_mark = avrinningsnat_ur_falt(
        z_dal.astype(np.float64),
        np.ones(z_dal.shape, dtype=np.bool_),
        None,
        x_min=150000.0,
        y_max=6580080.0,
        pixel_m=1.0,
        min_yta_m2=150.0,
        brytlinjer=(),
        anvand_rust=False,
    )
    assert str_mark and all(s.typ == "markstrak" for s in str_mark)


def test_taubin_kollisionsvakt_korsar_aldrig_byggnad_eller_mur_och_laser_sammanfloden() -> None:
    """Utjämning + förenkling får ALDRIG dra en flödeslinje genom en byggnad eller över en mur."""
    h, b = 70, 70
    dx = 0.5
    geo = _geo(h, b, dx=dx)
    r, c = np.mgrid[0:h, 0:b].astype(np.float64)
    # V-dal mot kolumn 35, lutande söderut, med en utstickande byggnad som tvingar en skarp krök!
    z = 10.0 + 0.06 * np.abs(c - 35.0) + 0.025 * (h - r)
    aktiv = np.ones((h, b), dtype=np.bool_)
    aktiv[25:42, 32:44] = False  # byggnad rakt över dalbotten -> tvingar 90° kringgång västerut

    # Lägg dessutom en mur precis väster om flödesvägen (vid kolumn 29) som barriär
    x_mur = geo.x_min + 29.0 * dx
    mur = BrytlinjePost(
        typ="mur",
        hojd_m=1.0,
        koordinater=((x_mur, geo.y_max - 15.0 * dx), (x_mur, geo.y_max - 55.0 * dx)),
    )

    flow, ordn, a = berakna_avrinningsfalt(
        z,
        aktiv,
        None,
        pixel_m=dx,
        x_min=geo.x_min,
        y_max=geo.y_max,
        brytlinjer=(mur,),
        anvand_rust=False,
    )
    strackor = strackor_fran_falt(
        z,
        flow,
        ordn,
        a,
        x_min=geo.x_min,
        y_max=geo.y_max,
        pixel_m=dx,
        min_yta_m2=40.0,
        brytlinjer=(mur,),
        aktiv=aktiv,
    )
    assert len(strackor) >= 1

    mx0, my0 = mur.koordinater[0]
    mx1, my1 = mur.koordinater[1]

    for s in strackor:
        # 1. Ingen punkt eller tätt samplad delpunkt får ligga i en inaktiv byggnadscell
        #    och inget segment får korsa murbarriären (varken inre skärning eller avskärning).
        for (ax, ay), (bx, by) in zip(s.koordinater[:-1], s.koordinater[1:], strict=True):
            assert not _segment_korsar_strikt(ax, ay, bx, by, mx0, my0, mx1, my1), (
                f"Sträcka {s.id} korsade murbarriären!"
            )
            # Inget segment får heller skära murkroppen (0,01 m innanför ändpunkterna)
            assert not _segment_korsar(ax, ay, bx, by, mx0, my0 - 0.01, mx1, my1 + 0.01), (
                f"Sträcka {s.id} klippte murkroppen!"
            )
            dist = math.hypot(bx - ax, by - ay)
            n_steg = max(4, int(math.ceil(dist / (0.1 * dx))))
            for k in range(n_steg + 1):
                t = k / n_steg
                px = ax + t * (bx - ax)
                py = ay + t * (by - ay)
                cf = (px - geo.x_min) / dx
                rf = (geo.y_max - py) / dx
                if abs(cf - round(cf)) <= 1e-7 or abs(rf - round(rf)) <= 1e-7:
                    continue
                col = math.floor(cf)
                row = math.floor(rf)
                assert 0 <= row < h and 0 <= col < b
                assert aktiv[row, col], (
                    f"Sträcka {s.id} skar in i inaktiv byggnadscell ({row}, {col})!"
                )

    # 2. Låsta sammanflödesnoder: alla sträckor med samma slut-/startpunkt möts exakt på cellcentrum
    startpunkter = {s.koordinater[0] for s in strackor}
    sammanfloden = [s.koordinater[-1] for s in strackor if s.koordinater[-1] in startpunkter]
    for sx, sy in sammanfloden:
        cf = (sx - geo.x_min) / dx
        rf = (geo.y_max - sy) / dx
        assert abs((cf - math.floor(cf)) - 0.5) < 1e-9
        assert abs((rf - math.floor(rf)) - 0.5) < 1e-9


def test_tom_kantbarriar_ar_bitidentisk_med_tidigare_python_och_rust() -> None:
    """Utan barriärer ska `fyll_och_riktning` (Python & Rust) matcha exact legacy-beteende."""
    _krav_eller_skip_rust_binar()
    rng = np.random.default_rng(7)
    z = 10.0 + 0.05 * rng.normal(size=(35, 35))
    aktiv = np.ones((35, 35), dtype=np.bool_)
    aktiv[10:15, 10:15] = False

    f0, o0 = _fyll_och_riktning_python(z, aktiv)
    f1, o1 = fyll_och_riktning(z, aktiv, kantbarriarer={}, anvand_rust=False)
    f2, o2 = fyll_och_riktning(z, aktiv, kantbarriarer={}, anvand_rust=True)
    np.testing.assert_array_equal(f1, f0)
    np.testing.assert_array_equal(o1, o0)
    np.testing.assert_array_equal(f2, f0)
    np.testing.assert_array_equal(o2, o0)
