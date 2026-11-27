# ruff: noqa: E501
"""Tester för beskärning av dagsljusmosaiken till påverkansradien (`ruta.py`, `floden_losare.py`)."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import dagsljus.floden_losare as fl_mod
import dagsljus.ruta as ruta_mod
from dagsljus.exempelkvarter import bygg_ruta, byggnads_id, rektangel
from dagsljus.floden_losare import (
    _mosaik,
    rensa_horisont_cache,
)
from dagsljus.horisont import horisont_riktning_ruta, riktningar_rad
from dagsljus.ruta import (
    RutaFel,
    bygg_mosaik,
    fonster_koordinater,
    uppskatta_mosaik_form,
)
from karna.geometri import las_ytmodell
from karna.korplan import kor_utredning
from karna.paverkan import dagsljus_paverkansradie_m
from karna.resultatcache import ResultatCache
from karna.utredning import fraga_fran_dict
from markmodell.byggnadsregister import ByggnadsKandidat
from markmodell.pipeline import bygg_markmodell_ruta
from markmodell.rutnat import Ruta as MarkRuta
from testsvit.k1_io import las_geotiff

_E0 = 150_000.0
_N0 = 6_576_000.0


def _skapa_5x5_rutnat(
    rot: Path,
    *,
    sida_m: float,
    overlapp_m: float = 0.0,
    upplosning_m: float = 1.0,
    kandidater: tuple[ByggnadsKandidat, ...] = (),
) -> tuple[Path, dict[tuple[int, int], Path]]:
    """Bygger ett 5×5-rutnät av K1 v2-rutor kring mittpunkten `(0, 0)` (index `-2..+2`)."""
    rutor: dict[tuple[int, int], Path] = {}
    for ix in range(-2, 3):
        for iy in range(-2, 3):
            e_min = _E0 + ix * sida_m
            n_min = _N0 + iy * sida_m
            ruta = MarkRuta(
                e_min_m=e_min,
                n_min_m=n_min,
                storlek_m=sida_m,
                overlapp_m=overlapp_m,
                upplosning_m=upplosning_m,
            )
            res = bygg_markmodell_ruta(
                ruta,
                rot,
                grund_z=np.zeros(ruta.form_rc, dtype=np.float32),
                byggnadskandidater=kandidater,
                byggnadshojd_fran_laser=False,
                kodversion="test-mosaik-klipp/1",
                start_tid="2026-11-27T00:00:00+01:00",
            )
            rutor[(ix, iy)] = Path(res.katalog)
    return rutor[(0, 0)], rutor


def _svf_och_trunk(
    mosaik: ruta_mod.Mosaik, radie_m: float, antal_k: int = 32
) -> tuple[np.ndarray[Any, np.dtype[np.float64]], np.ndarray[Any, np.dtype[np.int32]]]:
    r0, r1, c0, c1 = mosaik.fonster
    summa = np.zeros((r1 - r0, c1 - c0), dtype=np.float64)
    trunk = np.zeros((r1 - r0, c1 - c0), dtype=np.int32)
    for phi in riktningar_rad(antal_k):
        h, tr = horisont_riktning_ruta(mosaik.falt, float(phi), radie_m, fonster=mosaik.fonster)
        summa += np.sin(h.astype(np.float64)) ** 2
        trunk += tr
    return 1.0 - summa / antal_k, trunk


def test_5x5_500m_mosaik_klipps_under_4_5m_och_passerar_celltak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5×5 rutor à 500 m (2500×2500 = 6,25M celler) med radie 685,8 m beskärs till ~3,50M < 4,5M."""
    rensa_horisont_cache()
    radie_m = dagsljus_paverkansradie_m(60.0, 5.0)
    assert radie_m == pytest.approx(685.803135, rel=1e-5)

    # Placera ett par byggnader i mittrutan och i inre + yttre granne.
    hus = (
        rektangel(_E0 + 180.0, _E0 + 240.0, _N0 + 180.0, _N0 + 240.0, 22.0, "mitt"),
        rektangel(_E0 + 620.0, _E0 + 680.0, _N0 + 200.0, _N0 + 260.0, 35.0, "ost_inre"),
        rektangel(_E0 + 1080.0, _E0 + 1140.0, _N0 + 200.0, _N0 + 260.0, 48.0, "ost_yttre"),
    )
    bas_rot = tmp_path / "gbg_5x5"
    egen, rutor = _skapa_5x5_rutnat(bas_rot, sida_m=500.0, kandidater=hus)
    alla_grannar = tuple(p for k, p in sorted(rutor.items()) if k != (0, 0))
    # Exakt goteborg-test@v5-topologi: 21 rutor (5×5 minus de 4 hörnrutorna på 707,1 m > 687,2 m).
    horn = {(-2, -2), (-2, 2), (2, -2), (2, 2)}
    grannar_21 = tuple(p for k, p in sorted(rutor.items()) if k != (0, 0) and k not in horn)
    assert len(grannar_21) == 20

    # Obeskuren mosaik är 2500×2500 = 6 250 000 celler (> 4 500 000).
    assert uppskatta_mosaik_form(egen, alla_grannar, 1.0) == (2500, 2500)
    assert uppskatta_mosaik_form(egen, grannar_21, 1.0) == (2500, 2500)

    # Beskuren mosaik med radie 685,8 m: 500 + 2 * ceil(685,8) = 1872 -> 3 504 384 < 4 500 000!
    for gr in (alla_grannar, grannar_21):
        h_k, b_k = uppskatta_mosaik_form(egen, gr, 1.0, radie_m=radie_m)
        assert (h_k, b_k) == (1872, 1872)
        assert h_k * b_k == 3_504_384 < 4_500_000

    # Bygg klippt mosaik på 21-rutstopologin (goteborg-test@v5): inga saknade celler inom radien!
    m21 = bygg_mosaik(egen, grannar_21, 1.0, "medel", radie_m=radie_m)
    assert m21.falt.z_m.shape == (1872, 1872)
    assert m21.falt.z_m.size < 4_500_000
    assert m21.fonster == (686, 1186, 686, 1186)
    assert fonster_koordinater(m21) == pytest.approx((_E0, _N0 + 500.0))
    assert m21.saknade_celler == 0
    assert bool(np.isfinite(m21.falt.z_m).all())
    z_egen, _ = las_ytmodell(egen)
    np.testing.assert_array_equal(m21.falt.z_m[686:1186, 686:1186], z_egen.astype(np.float64))

    # End-to-end genom `kor_utredning` med produktionsgränsen FLODEN_MAX_DAGSLJUS_CELLER=4500000.
    monkeypatch.setenv("FLODEN_MAX_DAGSLJUS_CELLER", "4500000")
    monkeypatch.setenv("FLODEN_MAX_HORISONT_CACHE_MB", "0")
    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "gbg-5x5-klipp",
            "scenarier": {"bas": {"bas": str(bas_rot)}},
            "analyser": [
                {
                    "id": "svf",
                    "losare": "dagsljus.svf_mark",
                    "fokus": [_E0 + 100.0, _N0 + 100.0, _E0 + 400.0, _N0 + 400.0],
                    "parametrar": {
                        "hojd_max_m": 60.0,
                        "h_min_grader": 5.0,
                        "azimutriktningar": 8,
                    },
                },
                {
                    "id": "sol",
                    "losare": "dagsljus.soltimmar_mark",
                    "fokus": [_E0 + 100.0, _N0 + 100.0, _E0 + 400.0, _N0 + 400.0],
                    "parametrar": {
                        "hojd_max_m": 60.0,
                        "h_min_grader": 5.0,
                        "azimutriktningar": 8,
                        "datum": ["2026-03-20"],
                        "steg_min": 60,
                    },
                },
            ],
        },
        tmp_path,
    )
    res = kor_utredning(fraga, ResultatCache(tmp_path / "cache"), tmp_path / "ut")
    jobb = {j["analys"]: j for j in res.sammanstallning["jobb"]}
    assert set(jobb) == {"svf", "sol"}
    assert jobb["svf"]["matvarden"]["celler"] == 250_000
    assert 0.0 < jobb["svf"]["matvarden"]["svf_medel"] < 1.0
    assert jobb["sol"]["matvarden"]["soltimmar_max_h"] > 0.0
    for j in jobb.values():
        assert not any("saknar höjddata" in v for v in j["varningar"]), j["varningar"]
    rensa_horisont_cache()


def test_5x5_500m_med_25m_overlapp_klipps_till_3_69m_under_4_5m(tmp_path: Path) -> None:
    """5×5 rutor med 500 m + 25 m överlapp (2550×2550 = 6,50M) beskärs till 1922×1922 = 3,69M."""
    radie_m = dagsljus_paverkansradie_m(60.0, 5.0)
    egen, rutor = _skapa_5x5_rutnat(tmp_path / "ovl", sida_m=500.0, overlapp_m=25.0)
    grannar = tuple(p for k, p in sorted(rutor.items()) if k != (0, 0))
    assert uppskatta_mosaik_form(egen, grannar, 1.0) == (2550, 2550)
    h_k, b_k = uppskatta_mosaik_form(egen, grannar, 1.0, radie_m=radie_m)
    assert (h_k, b_k) == (1922, 1922)
    assert h_k * b_k == 3_694_084 < 4_500_000


def test_klippt_mosaik_ger_noll_avvikelse_i_svf_soltimmar_horisont_och_vsc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Klippt mosaik ger 0.0 max avvikelse mot full obeskuren mosaik i SVF, soltimmar, horisont och VSC."""
    rensa_horisont_cache()
    monkeypatch.setenv("FLODEN_MAX_HORISONT_CACHE_MB", "0")

    # 5×5 rutor à 40 m (totalt 200×200 = 40 000 celler).
    # hojd_max_m=10, h_min_grader=10 -> radie_m = 56,71 m -> marginal = 57 -> klippt 154×154.
    par_bas = {"hojd_max_m": 10.0, "h_min_grader": 10.0, "azimutriktningar": 32}
    radie_m = dagsljus_paverkansradie_m(10.0, 10.0)
    assert math.ceil(radie_m) == 57

    hus = (
        # Hus i egen ruta (0, 0): [0, 40] × [0, 40]
        rektangel(_E0 + 10.0, _E0 + 22.0, _N0 + 10.0, _N0 + 22.0, 12.0, "mitt_hus"),
        # Hus i egen ruta helt intill östra och norra kanten (testar gränsnära VSC-punkter)
        rektangel(_E0 + 34.0, _E0 + 40.0, _N0 + 26.0, _N0 + 38.0, 9.0, "kant_hus"),
        # Hinder i inre grannar (inom radien 56,71 m)
        rektangel(_E0 + 50.0, _E0 + 62.0, _N0 + 12.0, _N0 + 28.0, 15.0, "ost_inre"),
        rektangel(_E0 - 30.0, _E0 - 15.0, _N0 + 15.0, _N0 + 30.0, 11.0, "vast_inre"),
        # Hinder som korsar klippgränsen vid E0 + 40 + 57 = E0 + 97 (delvis innanför, delvis utanför)
        rektangel(_E0 + 92.0, _E0 + 104.0, _N0 + 10.0, _N0 + 24.0, 16.0, "grans_hinder"),
        # Standardhöjdsbyggnad INNANFÖR klippt utsträckning (ska ingå och flaggas!)
        rektangel(_E0 + 12.0, _E0 + 20.0, _N0 - 50.0, _N0 - 40.0, None, "std_inne"),
        # Standardhöjdsbyggnad HELT UTANFÖR klippt utsträckning (y < N0 - 57, dvs. i [-80, -64])
        rektangel(_E0 + 10.0, _E0 + 20.0, _N0 - 76.0, _N0 - 66.0, None, "std_ute"),
        # Vanligt högt hus helt utanför klippt utsträckning (x > E0 + 97)
        rektangel(_E0 + 104.0, _E0 + 116.0, _N0 + 10.0, _N0 + 25.0, 30.0, "torn_ute"),
    )
    bas_rot = tmp_path / "bas"
    egen, rutor = _skapa_5x5_rutnat(bas_rot, sida_m=40.0, kandidater=hus)
    grannar = tuple(p for k, p in sorted(rutor.items()) if k != (0, 0))

    # 1. Direkt mosaikjämförelse (standardbyggnader, fönster, form).
    m_full = bygg_mosaik(egen, grannar, 1.0, "medel")
    m_klipp = bygg_mosaik(egen, grannar, 1.0, "medel", radie_m=radie_m)
    assert m_full.falt.z_m.shape == (200, 200)
    assert m_klipp.falt.z_m.shape == (154, 154)
    assert m_klipp.fonster == (57, 97, 57, 97)
    assert fonster_koordinater(m_klipp) == fonster_koordinater(m_full) == (_E0, _N0 + 40.0)
    assert byggnads_id("std_inne") in m_klipp.standard_byggnader
    assert byggnads_id("std_ute") not in m_klipp.standard_byggnader
    assert byggnads_id("std_ute") in m_full.standard_byggnader

    # 2. Kör full uppsättning lösare (SVF, soltimmar, horisont, VSC med anpassat utanfor_m)
    # både klippt (standard) och med obeskuren referensmosaik.
    fasad_csv = tmp_path / "fasader.csv"
    with fasad_csv.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["fasad_id", "x_m", "y_m", "z_m", "normal_azimut_grader"])
        w.writerow(["mitt_syd", _E0 + 16.0, _N0 + 10.0, 2.0, 180.0])
        w.writerow(["mitt_ost", _E0 + 22.0, _N0 + 16.0, 2.0, 90.0])
        # Fasadpunkt exakt på rutans östra kant, normal rakt österut (förskjuts ut i grannen!)
        w.writerow(["kant_ost", _E0 + 40.0, _N0 + 32.0, 2.0, 90.0])

    fokus = [_E0 + 10.0, _N0 + 10.0, _E0 + 30.0, _N0 + 30.0]
    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "klipp-vs-full",
            "scenarier": {"bas": {"bas": str(bas_rot)}},
            "analyser": [
                {"id": "hor", "losare": "dagsljus.horisont", "fokus": fokus, "parametrar": par_bas},
                {"id": "svf", "losare": "dagsljus.svf_mark", "fokus": fokus, "parametrar": par_bas},
                {
                    "id": "sol",
                    "losare": "dagsljus.soltimmar_mark",
                    "fokus": fokus,
                    "parametrar": {**par_bas, "datum": ["2026-03-20"], "steg_min": 20},
                },
                {
                    "id": "vsc",
                    "losare": "dagsljus.vsc_fasad",
                    "fokus": fokus,
                    "parametrar": {**par_bas, "halvplansriktningar": 32, "utanfor_m": 1.5},
                    "indata": {"fasadpunkter": str(fasad_csv)},
                },
            ],
        },
        tmp_path,
    )

    ut_klipp = tmp_path / "ut_klipp"
    kor_utredning(fraga, ResultatCache(tmp_path / "c_klipp"), ut_klipp)

    # Referenskörning utan mosaikbeskärning (radie_m=None).
    orig_uppskatta = fl_mod.uppskatta_mosaik_form
    orig_bygg = fl_mod.bygg_mosaik
    monkeypatch.setattr(
        fl_mod,
        "uppskatta_mosaik_form",
        lambda e, g, c, radie_m=None: orig_uppskatta(e, g, c, radie_m=None),
    )
    monkeypatch.setattr(
        fl_mod,
        "bygg_mosaik",
        lambda e, g, c, m, *, vegetation=False, radie_m=None: orig_bygg(
            e, g, c, m, vegetation=vegetation, radie_m=None
        ),
    )
    ut_full = tmp_path / "ut_full"
    kor_utredning(fraga, ResultatCache(tmp_path / "c_full"), ut_full)

    rid = egen.name
    # Exakt 0.0 avvikelse på SVF, soltimmar, horisont, trunkering och standardhöjdsflagga!
    for aid, filer in (
        ("svf", ("svf.tif", "trunkering.tif", "standardhojd.tif")),
        ("sol", ("soltimmar.tif", "trunkering.tif", "standardhojd.tif")),
        ("hor", ("trunkering.tif", "standardhojd.tif")),
    ):
        for fil in filer:
            a_k, _ = las_geotiff(ut_klipp / aid / "bas" / rid / fil)
            a_f, _ = las_geotiff(ut_full / aid / "bas" / rid / fil)
            max_diff = float(np.max(np.abs(a_k.astype(np.float64) - a_f.astype(np.float64))))
            assert max_diff == 0.0, f"{aid}/{fil}: max_diff={max_diff}"

    h_k = np.load(ut_klipp / "hor" / "bas" / rid / "horisont.npy")
    h_f = np.load(ut_full / "hor" / "bas" / rid / "horisont.npy")
    np.testing.assert_array_equal(h_k, h_f)

    # VSC fasad (inklusive kant_ost med utanfor_m=1.5): exakt 0.0 avvikelse!
    with (ut_klipp / "vsc" / "bas" / rid / "vsc.csv").open(encoding="utf-8") as fh:
        vsc_k = {r["fasad_id"]: r for r in csv.DictReader(fh)}
    with (ut_full / "vsc" / "bas" / rid / "vsc.csv").open(encoding="utf-8") as fh:
        vsc_f = {r["fasad_id"]: r for r in csv.DictReader(fh)}
    assert set(vsc_k) == set(vsc_f) == {"mitt_syd", "mitt_ost", "kant_ost"}
    for fid in vsc_k:
        assert vsc_k[fid]["status"] == vsc_f[fid]["status"] == "ok"
        assert vsc_k[fid]["trunkerade_riktningar"] == vsc_f[fid]["trunkerade_riktningar"]
        assert vsc_k[fid]["standardhojd"] == vsc_f[fid]["standardhojd"]
        assert float(vsc_k[fid]["vsc_procent"]) - float(vsc_f[fid]["vsc_procent"]) == 0.0


def test_radie_mindre_an_ruta_och_noll_radie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Radie mindre än rutstorleken klipper inre grannar och hoppar över yttre rutor helt."""
    hus = (
        rektangel(_E0 + 15.0, _E0 + 25.0, _N0 + 15.0, _N0 + 25.0, 8.0, "mitt"),
        rektangel(_E0 + 44.0, _E0 + 52.0, _N0 + 15.0, _N0 + 25.0, 12.0, "nara_ost"),
        rektangel(_E0 + 85.0, _E0 + 95.0, _N0 + 15.0, _N0 + 25.0, 40.0, "langt_ost"),
    )
    egen, rutor = _skapa_5x5_rutnat(tmp_path / "liten_radie", sida_m=40.0, kandidater=hus)
    grannar = tuple(p for k, p in sorted(rutor.items()) if k != (0, 0))

    # Spåra vilka rutor som faktiskt läses från disk vid radie_m=10.0 (< 40 m rutstorlek).
    lasta: list[Path] = []
    orig_lasa = ruta_mod._lasa_tile  # noqa: SLF001

    def _spion(mapp: Path, vegetation: bool, **kw: Any) -> Any:
        lasta.append(mapp)
        return orig_lasa(mapp, vegetation, **kw)

    monkeypatch.setattr(ruta_mod, "_lasa_tile", _spion)

    assert uppskatta_mosaik_form(egen, grannar, 1.0, radie_m=10.0) == (60, 60)
    m_klipp = bygg_mosaik(egen, grannar, 1.0, "medel", radie_m=10.0)
    assert m_klipp.falt.z_m.shape == (60, 60)
    assert m_klipp.fonster == (10, 50, 10, 50)
    # Bara 3×3 inre rutor (9 st) ska läsas; yttre 16 rutor hoppas över helt!
    assert len(lasta) == 9
    assert egen in lasta

    # 0.0 diff i SVF och trunkering mot full 200×200-mosaik.
    m_full = bygg_mosaik(egen, grannar, 1.0, "medel")
    svf_k, tr_k = _svf_och_trunk(m_klipp, radie_m=10.0)
    svf_f, tr_f = _svf_och_trunk(m_full, radie_m=10.0)
    assert float(np.max(np.abs(svf_k - svf_f))) == 0.0
    np.testing.assert_array_equal(tr_k, tr_f)

    # radie_m = 0.0 -> bara egen ruta laddas.
    lasta.clear()
    assert uppskatta_mosaik_form(egen, grannar, 1.0, radie_m=0.0) == (40, 40)
    m0 = bygg_mosaik(egen, grannar, 1.0, "medel", radie_m=0.0)
    assert m0.falt.z_m.shape == (40, 40)
    assert m0.fonster == (0, 40, 0, 40)
    assert lasta == [egen]


def test_delvis_saknade_rutor_och_varning(tmp_path: Path) -> None:
    """Saknade celler inom påverkansradien rapporteras; oåtkomliga hörnrutor bortom radien inte."""
    hus = (rektangel(_E0 + 10.0, _E0 + 20.0, _N0 + 10.0, _N0 + 20.0, 10.0, "mitt"),)
    egen, rutor = _skapa_5x5_rutnat(tmp_path / "saknade", sida_m=40.0, kandidater=hus)
    horn = {(-2, -2), (-2, 2), (2, -2), (2, 2)}

    # 1. De 4 hörnrutorna ligger på avstånd sqrt(40² + 40²) = 56,57 m > radie_m = 50,0 m:
    #    saknade_celler måste vara 0 (inga falska varningar) men fältet helt ändligt!
    gr_utan_horn = tuple(p for k, p in sorted(rutor.items()) if k != (0, 0) and k not in horn)
    m_utan_horn = bygg_mosaik(egen, gr_utan_horn, 1.0, "medel", radie_m=50.0)
    assert m_utan_horn.falt.z_m.shape == (140, 140)
    assert m_utan_horn.saknade_celler == 0
    assert bool(np.isfinite(m_utan_horn.falt.z_m).all())

    # 2. Saknad västlig ruta (-2, 0) ligger på avstånd 40 m <= 50,0 m (med (-2, -1) och (-2, 1) kvar):
    #    10×40 = 400 celler i den västliga remsan ligger inom radien -> saknade_celler == 400!
    gr_utan_vast = tuple(
        p for k, p in sorted(rutor.items()) if k != (0, 0) and k not in (horn | {(-2, 0)})
    )
    m_utan_vast = bygg_mosaik(egen, gr_utan_vast, 1.0, "medel", radie_m=50.0)
    assert m_utan_vast.saknade_celler == 400
    m_utan_vast_full = bygg_mosaik(egen, gr_utan_vast, 1.0, "medel")
    svf_k, tr_k = _svf_och_trunk(m_utan_vast, radie_m=50.0)
    svf_f, tr_f = _svf_och_trunk(m_utan_vast_full, radie_m=50.0)
    assert float(np.max(np.abs(svf_k - svf_f))) == 0.0
    np.testing.assert_array_equal(tr_k, tr_f)

    # 3. Hela västsidan saknas: klippningen skär mot befintlig rutunion så c0 == 0 och trunkering matchar.
    gr_bara_ost = tuple(p for k, p in sorted(rutor.items()) if k != (0, 0) and k[0] >= 0)
    m_ost_k = bygg_mosaik(egen, gr_bara_ost, 1.0, "medel", radie_m=50.0)
    m_ost_f = bygg_mosaik(egen, gr_bara_ost, 1.0, "medel")
    assert m_ost_k.fonster[2] == 0
    assert m_ost_k.falt.z_m.shape == (140, 90)
    svf_ok, tr_ok = _svf_och_trunk(m_ost_k, radie_m=50.0)
    svf_of, tr_of = _svf_och_trunk(m_ost_f, radie_m=50.0)
    assert float(np.max(np.abs(svf_ok - svf_of))) == 0.0
    np.testing.assert_array_equal(tr_ok, tr_of)

    # 4. Avlägsen isolerad granne bortom radie_m (med tomt glapp emellan) utelämnas utan tomremsa.
    avlagset = bygg_ruta(tmp_path / "isol", _E0 + 300.0, _N0, 40.0, [])
    assert uppskatta_mosaik_form(egen, (avlagset,), 1.0, radie_m=50.0) == (40, 40)
    m_isol = bygg_mosaik(egen, (avlagset,), 1.0, "medel", radie_m=50.0)
    assert m_isol.falt.z_m.shape == (40, 40)
    assert m_isol.saknade_celler == 0


@pytest.mark.parametrize("metod", ["medel", "max", "punkt"])
def test_cellstorlek_ej_1m_och_omsampling(tmp_path: Path, metod: ruta_mod.Omsampling) -> None:
    """Beskärning med `cellstorlek_m != 1.0` (faktor 2) är bitidentisk med obeskuren mosaik."""
    hus = (
        rektangel(_E0 + 8.0, _E0 + 18.0, _N0 + 8.0, _N0 + 18.0, 12.0, "mitt"),
        rektangel(_E0 + 48.0, _E0 + 62.0, _N0 + 10.0, _N0 + 26.0, 18.0, "ost"),
        rektangel(_E0 - 52.0, _E0 - 42.0, _N0 + 12.0, _N0 + 24.0, 14.0, "vast"),
    )
    egen, rutor = _skapa_5x5_rutnat(tmp_path / f"cell2_{metod}", sida_m=40.0, kandidater=hus)
    grannar = tuple(p for k, p in sorted(rutor.items()) if k != (0, 0))

    # cell_m = 2.0 (faktor = 2), radie_m = 51.0 -> ceil(51 / 2) = 26 grova celler per sida.
    # egen = 20×20 grova celler -> klippt = 72×72 grova celler (vs 100×100 obeskuren).
    radie_m = 51.0
    assert uppskatta_mosaik_form(egen, grannar, 2.0) == (100, 100)
    assert uppskatta_mosaik_form(egen, grannar, 2.0, radie_m=radie_m) == (72, 72)

    m_k = bygg_mosaik(egen, grannar, 2.0, metod, radie_m=radie_m)
    m_f = bygg_mosaik(egen, grannar, 2.0, metod)
    assert m_k.falt.z_m.shape == (72, 72)
    assert m_k.fonster == (26, 46, 26, 46)
    assert fonster_koordinater(m_k) == fonster_koordinater(m_f) == (_E0, _N0 + 40.0)

    svf_k, tr_k = _svf_och_trunk(m_k, radie_m=radie_m)
    svf_f, tr_f = _svf_och_trunk(m_f, radie_m=radie_m)
    assert float(np.max(np.abs(svf_k - svf_f))) == 0.0
    np.testing.assert_array_equal(tr_k, tr_f)


def test_finare_rattpixel_0_5m_till_1_0m_klipps_exakt(tmp_path: Path) -> None:
    """0,5 m K1-rutor omsamplade till `cellstorlek_m = 1.0` beskärs exakt på cellrutnätet."""
    hus = (
        rektangel(_E0 + 6.0, _E0 + 14.0, _N0 + 6.0, _N0 + 14.0, 10.0, "mitt"),
        rektangel(_E0 + 26.0, _E0 + 34.0, _N0 + 6.0, _N0 + 14.0, 16.0, "ost"),
    )
    egen, rutor = _skapa_5x5_rutnat(
        tmp_path / "p05", sida_m=20.0, upplosning_m=0.5, kandidater=hus
    )
    grannar = tuple(p for k, p in sorted(rutor.items()) if k != (0, 0))
    radie_m = 25.0
    assert uppskatta_mosaik_form(egen, grannar, 1.0, radie_m=radie_m) == (70, 70)
    m_k = bygg_mosaik(egen, grannar, 1.0, "max", radie_m=radie_m)
    m_f = bygg_mosaik(egen, grannar, 1.0, "max")
    svf_k, tr_k = _svf_och_trunk(m_k, radie_m=radie_m)
    svf_f, tr_f = _svf_och_trunk(m_f, radie_m=radie_m)
    assert float(np.max(np.abs(svf_k - svf_f))) == 0.0
    np.testing.assert_array_equal(tr_k, tr_f)


def test_ogiltig_radie_ger_rutafel(tmp_path: Path) -> None:
    egen = bygg_ruta(tmp_path / "r", _E0, _N0, 20.0, [])
    for ogiltig in (-1.0, float("nan"), float("inf"), True):
        with pytest.raises(RutaFel, match="radie_m"):
            uppskatta_mosaik_form(egen, (), 1.0, radie_m=ogiltig)  # type: ignore[arg-type]
        with pytest.raises(RutaFel, match="radie_m"):
            bygg_mosaik(egen, (), 1.0, "medel", radie_m=ogiltig)  # type: ignore[arg-type]


def test_mosaik_klippradie_med_och_utan_utanfor_m() -> None:
    """`_mosaik` ger extra marginal för fasadförskjutning och samma fönster för delad horisontcache."""
    p_mark = {"hojd_max_m": 60.0, "h_min_grader": 5.0, "cellstorlek_m": 1.0}
    p_fasad = {**p_mark, "utanfor_m": 0.5}
    r_mark = fl_mod._mosaik_klippradie_m(p_mark)  # noqa: SLF001
    r_fasad = fl_mod._mosaik_klippradie_m(p_fasad)  # noqa: SLF001
    assert r_mark == dagsljus_paverkansradie_m(60.0, 5.0)
    assert r_fasad > r_mark + 1.5
    assert _mosaik is fl_mod._mosaik  # noqa: SLF001
