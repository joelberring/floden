"""Takvatten (Joels beslut 2026-11-22): fördelningsregler med kända svar och volymbalans."""

from __future__ import annotations

import math

import numpy as np
import pytest
from numpy.typing import NDArray

from dynamik.handelsestyrd import kor_dynamik_handelsestyrd
from sankgraf.fyllning import statisk_fyllning
from sankgraf.hierarki import bygg_sankgraf
from sankgraf.takvatten import (
    REGEL_PLATT,
    REGEL_PULPET,
    REGEL_RING,
    REGEL_SADEL,
    TakPolygon,
    TakvattenFel,
    bygg_takvatten,
    med_takvatten,
    tolka_takform,
)
from sankgraf.typer import SankGraf
from testsvit.k1_io import GeoTransformInfo
from tvad.lokal_troghet import kor_2d_lokal_troghet

DX = 0.25


def _geo(h: int, w: int) -> GeoTransformInfo:
    return GeoTransformInfo(
        bredd=w, hojd=h, x_min=0.0, y_max=h * DX, pixelstorlek_x=DX, pixelstorlek_y=DX,
        epsg=3011, dtype="float32",
    )


def _rektangel(h: int = 60, w: int = 80) -> NDArray[np.uint8]:
    s = np.ones((h, w), np.uint8)
    s[18:42, 16:64] = 9  # 12 x 6 m, långsidor i norr och söder
    return s


def _poly_rektangel(h: int = 60) -> tuple[tuple[float, float], ...]:
    y_max = h * DX
    return ((4.0, y_max - 10.5), (16.0, y_max - 10.5), (16.0, y_max - 4.5), (4.0, y_max - 4.5))


def _sidor(e: NDArray[np.float64]) -> dict[str, float]:
    return {
        "norr": float(e[17].sum()), "soder": float(e[42].sum()),
        "vast": float(e[:, 15].sum()), "ost": float(e[:, 64].sum()),
    }


def test_tolka_takform() -> None:
    assert tolka_takform(None) == ("okand", False)
    assert tolka_takform("Platt") == ("platt", False)
    assert tolka_takform("brutet") == ("sadel", False)
    assert tolka_takform("mansard") == ("sadel", False)
    assert tolka_takform("skillion") == ("pulpet", False)
    assert tolka_takform("kupol") == ("okand", True)


def test_okand_takform_ger_halva_taket_till_varje_langsida() -> None:
    s = _rektangel()
    t = bygg_takvatten(np.full(s.shape, 10.0), s, _geo(*s.shape))
    assert t.byggnader[0].regel == REGEL_SADEL
    sid = _sidor(t.extra_area_m2)
    assert sid == pytest.approx({"norr": 36.0, "soder": 36.0, "vast": 0.0, "ost": 0.0}, abs=1e-12)
    # Jämnt längs fasaden: varje cell utanför långsidan får takdjupets halva (3 m × 0,25 m).
    assert np.allclose(t.extra_area_m2[17, 16:64], 0.75)
    assert t.extra_area_m2.sum() == pytest.approx(72.0, abs=1e-12)


def test_platt_tak_fordelas_efter_fasadlangd() -> None:
    s = _rektangel()
    t = bygg_takvatten(
        np.full(s.shape, 10.0), s, _geo(*s.shape),
        byggnader=[TakPolygon("b1", _poly_rektangel(), takform="platt")],
    )
    assert t.byggnader[0].regel == REGEL_PLATT and t.byggnader[0].kalla == "register"
    sid = _sidor(t.extra_area_m2)
    # 72 m² på omkretsen 36 m: långsidorna 12/36, gavlarna 6/36.
    assert sid == pytest.approx({"norr": 24.0, "soder": 24.0, "vast": 12.0, "ost": 12.0}, abs=1e-9)


def test_pulpet_med_riktning_ger_allt_till_lagre_sidan() -> None:
    s = _rektangel()
    t = bygg_takvatten(
        np.full(s.shape, 10.0), s, _geo(*s.shape),
        byggnader=[TakPolygon("b1", _poly_rektangel(), takform="pulpet",
                              takfall_azimut_grader=180.0)],
    )
    assert t.byggnader[0].regel == REGEL_PULPET
    assert _sidor(t.extra_area_m2)["soder"] == pytest.approx(72.0, abs=1e-9)


def test_pulpet_utan_riktning_raknas_som_okand_och_flaggas() -> None:
    s = _rektangel()
    t = bygg_takvatten(
        np.full(s.shape, 10.0), s, _geo(*s.shape),
        byggnader=[TakPolygon("b1", _poly_rektangel(), takform="pulpet")],
    )
    assert t.byggnader[0].regel == REGEL_SADEL
    assert t.flaggor["takform_ersatt"] == 1
    assert _sidor(t.extra_area_m2)["norr"] == pytest.approx(36.0, abs=1e-9)


def test_l_form_far_fyra_takfotslinjer_efter_langd() -> None:
    s = np.ones((100, 100), np.uint8)
    s[10:34, 10:90] = 9  # vinge 20 x 6 m
    s[10:90, 10:34] = 9  # vinge 6 x 20 m (hörnet delat)
    t = bygg_takvatten(np.full(s.shape, 10.0), s, _geo(*s.shape))
    b = t.byggnader[0]
    assert b.regel == REGEL_SADEL
    andelar = sorted((round(li["langd_m"], 6), li["andel"]) for li in b.linjer if li["andel"] > 0)
    # Två yttre långsidor (20 m) och två inre (14 m); gavlarna (6 m) får inget.
    assert [a for a, _ in andelar] == [14.0, 14.0, 20.0, 20.0]
    assert [v for _, v in andelar] == pytest.approx([14 / 68, 14 / 68, 20 / 68, 20 / 68])
    tak = float(np.count_nonzero(s == 9)) * DX * DX
    e = t.extra_area_m2
    assert float(e[:, 90].sum()) == 0.0 and float(e[90, :].sum()) == 0.0  # gavlar
    assert float(e[9, :].sum()) == pytest.approx(tak * 20 / 68)  # yttre norra långsidan
    assert e.sum() == pytest.approx(tak)


def test_ring_ger_halva_taket_till_garden() -> None:
    s = np.ones((88, 88), np.uint8)
    s[8:80, 8:80] = 9
    s[24:64, 24:64] = 1
    t = bygg_takvatten(np.full(s.shape, 10.0), s, _geo(*s.shape))
    assert t.byggnader[0].regel == REGEL_RING
    gard = np.zeros(s.shape, bool)
    gard[24:64, 24:64] = True
    assert t.extra_area_m2[gard].sum() == pytest.approx(112.0, abs=1e-9)
    assert t.extra_area_m2[~gard].sum() == pytest.approx(112.0, abs=1e-9)


def test_vriden_rektangel_ur_registret() -> None:
    h = w = 120
    s = np.ones((h, w), np.uint8)
    c, v = (15.0, 15.0), math.radians(30.0)
    ux, uy, vx, vy = math.cos(v), math.sin(v), -math.sin(v), math.cos(v)
    poly = tuple(
        (c[0] + a * 8 * ux + b * 3 * vx, c[1] + a * 8 * uy + b * 3 * vy)
        for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))
    )
    xs = (np.arange(w) + 0.5) * DX
    ys = h * DX - (np.arange(h) + 0.5) * DX
    xx, yy = np.meshgrid(xs, ys)
    lok_a = (xx - c[0]) * ux + (yy - c[1]) * uy
    lok_b = (xx - c[0]) * vx + (yy - c[1]) * vy
    s[(np.abs(lok_a) < 8) & (np.abs(lok_b) < 3)] = 9
    t = bygg_takvatten(np.full(s.shape, 10.0), s, _geo(h, w),
                       byggnader=[TakPolygon("v", poly)])
    b = t.byggnader[0]
    assert b.regel == REGEL_SADEL
    assert sorted(li["langd_m"] for li in b.linjer if li["andel"] > 0) == pytest.approx([16, 16])
    e = t.extra_area_m2
    har = e > 0
    # Vattnet hamnar utanför långsidorna (|b| strax över 3 m), inte vid gavlarna.
    assert np.all(np.abs(lok_b[har]) > 3.0) and np.all(np.abs(lok_b[har]) < 3.0 + 3 * DX)
    assert e[lok_b > 0].sum() == pytest.approx(e[lok_b < 0].sum(), rel=1e-12)
    assert e.sum() == pytest.approx(np.count_nonzero(s == 9) * DX * DX)


def test_brandvagg_och_andel_till_ledning() -> None:
    s = np.ones((60, 80), np.uint8)
    s[18:30, 16:64] = 9  # norra huset 12 x 3 m
    s[30:42, 16:64] = 9  # södra huset, vägg i vägg längs långsidan
    y = 60 * DX
    norr = ((4.0, y - 7.5), (16.0, y - 7.5), (16.0, y - 4.5), (4.0, y - 4.5))
    soder = ((4.0, y - 10.5), (16.0, y - 10.5), (16.0, y - 7.5), (4.0, y - 7.5))
    t = bygg_takvatten(
        np.full(s.shape, 10.0), s, _geo(*s.shape), andel_ledning=0.25,
        byggnader=[TakPolygon("a_norr", norr), TakPolygon("b_soder", soder)],
    )
    e = t.extra_area_m2
    # Mot brandväggen finns ingen mark: varje hus avvattnas helt till sin fria långsida.
    assert float(e[17].sum()) == pytest.approx(36.0 * 0.75)
    assert float(e[42].sum()) == pytest.approx(36.0 * 0.75)
    assert t.ledning_area_m2 == pytest.approx(72.0 * 0.25)
    assert t.flaggor["fasadlinjer_utan_mark"] == 2


def test_inget_takvatten_i_portar() -> None:
    s = _rektangel()
    oppning = np.zeros(s.shape, bool)
    oppning[42, 30:40] = True  # en "port" i markcellerna söder om huset
    t = bygg_takvatten(np.full(s.shape, 10.0), s, _geo(*s.shape), oppning_mask=oppning)
    assert float(t.extra_area_m2[oppning].sum()) == 0.0
    assert t.flaggor["prov_i_oppning_undvikna"] > 0
    assert float(t.extra_area_m2[42].sum()) == pytest.approx(36.0)


def test_fasad_utanfor_rutan_ar_utflode() -> None:
    s = np.ones((40, 60), np.uint8)
    s[0:24, 10:58] = 9  # norra långsidan ligger på rutans kant
    t = bygg_takvatten(np.full(s.shape, 10.0), s, _geo(*s.shape))
    assert t.ut_area_m2 == pytest.approx(36.0)
    assert t.extra_area_m2.sum() == pytest.approx(36.0)


def test_tak_utan_vag_till_mark_ger_fel() -> None:
    s = np.ones((20, 20), np.uint8)
    s[5:15, 5:15] = 9
    z = np.full(s.shape, np.nan)  # ingen markcell har höjd, och taket når inte rutans kant
    z[s == 9] = 20.0
    with pytest.raises(TakvattenFel, match="ingen väg till mark"):
        bygg_takvatten(z, s, _geo(20, 20))


def test_fordelningen_maste_passa_rastret() -> None:
    s = _rektangel()
    t = bygg_takvatten(np.full(s.shape, 10.0), s, _geo(*s.shape))
    with pytest.raises(TakvattenFel):
        t.kontrollera_mot(np.ones(s.shape, np.uint8), np.full(s.shape, 10.0))


def _graf_med_trag(platt: bool) -> tuple[SankGraf, dict[str, NDArray[np.bool_]]]:
    """Hus i tråg (som A13) med polygon ur registret."""
    from testsvit.syntetiska import A13_TRAG, _fall_a13_hus_i_trag

    spec = _fall_a13_hus_i_trag()
    geo = GeoTransformInfo(
        bredd=spec.mark.shape[1], hojd=spec.mark.shape[0], x_min=spec.x_min, y_max=spec.y_max,
        pixelstorlek_x=DX, pixelstorlek_y=DX, epsg=3011, dtype="float32",
    )
    graf = bygg_sankgraf(spec.mark, spec.osakerhet, spec.markslag, geo)
    x0, y0 = spec.x_min, spec.y_max
    poly = ((x0 + 3, y0 - 10), (x0 + 15, y0 - 10), (x0 + 15, y0 - 4), (x0 + 3, y0 - 4))
    tak = bygg_takvatten(graf.mark_z, graf.markslag, geo, byggnader=[
        TakPolygon("hus", poly, takform="platt" if platt else None)
    ])
    masker = {}
    for sida, (rr, cc) in A13_TRAG.items():
        m = np.zeros(spec.mark.shape, bool)
        m[rr, cc] = True
        masker[sida] = m
    ut: SankGraf = med_takvatten(graf, tak)
    return ut, masker


@pytest.mark.parametrize(("platt", "andelar"), [
    (False, {"norr": 0.5, "soder": 0.5, "vast": 0.0, "ost": 0.0}),
    (True, {"norr": 1 / 3, "soder": 1 / 3, "vast": 1 / 6, "ost": 1 / 6}),
])
def test_trag_statisk_och_dynamik_far_takvattnet_exakt(
    platt: bool, andelar: dict[str, float]
) -> None:
    graf, masker = _graf_med_trag(platt)
    regn_m = 0.05
    res = statisk_fyllning(graf, regn_m)
    dyn = kor_dynamik_handelsestyrd(graf, [0.0, 600.0], [300.0, 300.0], slut_tid_s=1800.0)
    for sida, m in masker.items():
        (nid,) = {int(x) for x in np.unique(graf.cell_till_nod[m])}
        facit = (graf.noder[nid].avrinningsyta_m2 + 72.0 * andelar[sida]) * regn_m
        assert float(res.djup_m[m].sum()) * DX * DX == pytest.approx(facit, rel=1e-6)
        assert dyn.noder[nid].volym_m3[-1] == pytest.approx(facit, rel=1e-12)
    for b in (res.volymbalans(), dyn.volymbalans()):
        assert b["regn_totalt_m3"] == pytest.approx(18.0 * 14.0 * regn_m)
        assert b["volymfel_rel"] < 1e-12


def test_2d_volymbalans_och_takvatten_av() -> None:
    graf, masker = _graf_med_trag(False)
    r = kor_2d_lokal_troghet(
        graf.mark_z, graf.markslag, graf.geo, [0.0, 600.0], [300.0, 300.0],
        slut_tid_s=1200.0, takvatten=graf.takvatten,
    )
    b = r.volymbalans()
    assert b["regn_totalt_m3"] == pytest.approx(12.6)
    assert b["volymfel_rel"] < 1e-12
    av = kor_2d_lokal_troghet(
        graf.mark_z, graf.markslag, graf.geo, [0.0, 600.0], [300.0, 300.0], slut_tid_s=1200.0,
    )
    b_av = av.volymbalans()
    assert b_av["tak_ej_raknat_m3"] == pytest.approx(3.6)
    assert b_av["volymfel_rel"] < 1e-12


def test_rust_ger_samma_som_python_med_takvatten() -> None:
    from tvad.lokal_troghet import TvadFel
    from tvad.rust_backend import hitta_tvad_karna, kor_2d_lokal_troghet_rust

    try:
        hitta_tvad_karna()
    except TvadFel:
        pytest.skip("tvad_karna är inte byggd")
    graf, _ = _graf_med_trag(False)
    arg = (graf.mark_z, graf.markslag, graf.geo, [0.0, 600.0], [300.0, 300.0])
    p = kor_2d_lokal_troghet(*arg, slut_tid_s=900.0, takvatten=graf.takvatten)
    r = kor_2d_lokal_troghet_rust(*arg, slut_tid_s=900.0, takvatten=graf.takvatten)
    assert float(np.max(np.abs(p.max_djup_m - r.max_djup_m))) == 0.0
    assert float(np.max(np.abs(p.slut_djup_m - r.slut_djup_m))) == 0.0
    assert r.in_m3 == pytest.approx(p.in_m3, rel=1e-13)
    assert r.volymbalans()["volymfel_rel"] < 1e-12
