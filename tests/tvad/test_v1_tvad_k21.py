"""Deterministiska regressionstester för Våg 1: K-B1 (1D-rännsten) & K-B7 (2D volymbalans/f32)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from gemensamt import gpu
from karna.korplan import kor_utredning
from karna.resultatcache import ResultatCache
from karna.utredning import fraga_fran_dict
from markmodell.brytlinjer import Brytlinje
from markmodell.k1_skrivare import skriv_brytlinjer_gpkg
from markmodell.rutnat import Ruta
from sankgraf.typer import MARKSLAG_BYGGNAD, las_brytlinjer_fran_gpkg
from testsvit.k1_io import BrytlinjePost, GeoTransformInfo
from tvad.kalltermer import Avtappning, Kalltermer
from tvad.lokal_troghet import (
    MARKSLAG_VATTEN,
    kor_2d_lokal_troghet,
    skriv_tvad_resultat_k6,
)
from tvad.rannsten_1d import (
    RannstenFel,
    diskretisera_rannsten_1d,
    kor_kopplad_1d_2d,
)
from vatten.blaytor import k1_filer
from vatten.losare_2d import _kor_2d


class _MockGpuBackend:
    """Kör `_kor_lokal_troghet_xp` på CPU via NumPy utan krav på CUDA."""

    def __init__(self) -> None:
        self.cuda = SimpleNamespace(
            Stream=SimpleNamespace(null=SimpleNamespace(synchronize=lambda: None))
        )

    def __getattr__(self, namn: str) -> Any:
        return getattr(np, namn)


def _tva_skilda_kantstenar_med_vagg() -> tuple[
    np.ndarray[tuple[int, int], np.dtype[np.float32]],
    np.ndarray[tuple[int, int], np.dtype[np.uint8]],
    np.ndarray[tuple[int, int], np.dtype[np.bool_]],
    GeoTransformInfo,
    list[BrytlinjePost],
]:
    """Två skilda 12 m kantstenar på var sin sida om en 8 m byggnadsvägg (~26 m isär).

    Norra kantstenen (rad 8) har körbana norr om sig (rader 2..7) och sluttar österut.
    Mellan rader 16..24 står en 8 m bred ogenomtränglig byggnadsvägg.
    Södra kantstenen (rad 34) har körbana norr om sig (rader 28..33) och sluttar österut.
    Regn faller ENBART norr om byggnadsväggen!
    """
    h, b = 42, 24
    dx = 1.0
    x_min, y_max = 150000.0, 6580042.0
    geo = GeoTransformInfo(
        x_min=x_min,
        y_max=y_max,
        pixelstorlek_x=dx,
        pixelstorlek_y=dx,
        bredd=b,
        hojd=h,
        epsg=3011,
        dtype="float32",
    )
    xx = np.broadcast_to(np.arange(b, dtype=np.float64)[None, :], (h, b))
    z = (15.0 - 0.02 * xx).astype(np.float32).copy()
    markslag = np.full((h, b), 1, dtype=np.uint8)
    # 8 m bred ogenomtränglig byggnadsvägg tvärs över hela domänen (rader 16..23)
    markslag[16:24, :] = MARKSLAG_BYGGNAD
    z[16:24, :] = np.float32(25.0)

    y_norr = y_max - 8.0
    y_soder = y_max - 34.0
    x0, x1 = x_min + 4.0, x_min + 16.0
    z_n0, z_n1 = 15.0 - 0.02 * 4.0, 15.0 - 0.02 * 16.0
    brytlinjer = [
        BrytlinjePost(
            typ="kantsten",
            hojd_m=0.12,
            koordinater=((x0, y_norr), (x1, y_norr)),
            z_kron_rh2000=(z_n0 + 0.12, z_n1 + 0.12),
            z_botten_rh2000=(z_n0, z_n1),
            tvarfall=0.025,
            kalla="baskarta_3d",
        ),
        BrytlinjePost(
            typ="kantsten",
            hojd_m=0.12,
            koordinater=((x0, y_soder), (x1, y_soder)),
            z_kron_rh2000=(z_n0 + 0.12, z_n1 + 0.12),
            z_botten_rh2000=(z_n0, z_n1),
            tvarfall=0.025,
            kalla="baskarta_3d",
        ),
    ]
    regn_norr = np.zeros((h, b), dtype=np.bool_)
    regn_norr[2:8, 4:16] = True
    return z, markslag, regn_norr, geo, brytlinjer


# --- K-B1: Kedjegränser, utlopp per kedja, kor_rc=None, GPKG z_botten, varningar -------------


def test_kb1_tva_skilda_kantstenar_noll_lackage_och_utlopp_per_kedja() -> None:
    """Två skilda kantstenar med 8 m vägg emellan ger exakt 0,0 m läckage till torra kedjan,
    och `oppen_nedstroms=True` dränerar kedja 0 trots att den inte är sist i arrayen."""
    z, ms, regn_norr, geo, bryt = _tva_skilda_kantstenar_med_vagg()
    lank_stangd = diskretisera_rannsten_1d(
        bryt, z, ms, geo, ds_mal_m=2.0, korbana_sida="vanster", oppen_nedstroms=False
    )
    assert lank_stangd.antal_kedjor == 2
    norr_idx = [s.index for s in lank_stangd.segment if s.kedje_id == 0]
    soder_idx = [s.index for s in lank_stangd.segment if s.kedje_id == 1]
    assert len(norr_idx) == 6 and len(soder_idx) == 6
    par = lank_stangd.effektiva_anslutna_par()
    assert (norr_idx[-1], soder_idx[0]) not in par

    res_stangd = kor_kopplad_1d_2d(
        z,
        ms,
        geo,
        [0.0, 60.0],
        [360.0, 360.0],
        lank_stangd,
        brytlinjer=bryt,
        slut_tid_s=120.0,
        oppna_rander=False,
        regn_mask=regn_norr,
    )
    assert res_stangd.volymfel_rel < 1e-11
    assert float(res_stangd.volymbalans()["volymfel_rel"]) < 1e-11
    # Norra rännstenen ska ha tydligt flöde/djup, södra EXAKT 0,0 m (ingen teleportering!)
    assert float(np.max(res_stangd.max_djup_1d_m[norr_idx])) > 0.02
    assert float(np.max(res_stangd.max_djup_1d_m[soder_idx])) == 0.0
    assert float(np.max(res_stangd.slut_djup_1d_m[soder_idx])) == 0.0
    assert float(np.max(res_stangd.max_djup_2d_m[24:, :])) == 0.0
    assert res_stangd.utflode_1d_m3 == 0.0

    # Med oppen_nedstroms=True ska KEDJA 0 (inte bara sista segmentet!) dränera ut nedströms
    lank_oppen = diskretisera_rannsten_1d(
        bryt, z, ms, geo, ds_mal_m=2.0, korbana_sida="vanster", oppen_nedstroms=True
    )
    res_oppen = kor_kopplad_1d_2d(
        z,
        ms,
        geo,
        [0.0, 60.0],
        [360.0, 360.0],
        lank_oppen,
        brytlinjer=bryt,
        slut_tid_s=120.0,
        oppna_rander=False,
        regn_mask=regn_norr,
    )
    assert res_oppen.volymfel_rel < 1e-11
    assert res_oppen.utflode_1d_m3 > 0.05
    assert float(np.max(res_oppen.max_djup_1d_m[soder_idx])) == 0.0
    assert float(np.max(res_oppen.slut_djup_1d_m[soder_idx])) == 0.0


def test_kb1_kor_rc_none_ger_aldrig_tyst_zb_noll() -> None:
    """Saknad aktiv körbanecell utan `z_botten_rh2000` får aldrig tyst `zb = 0.0` m RH 2000."""
    h, b = 12, 16
    x_min, y_max = 150000.0, 6580012.0
    geo = GeoTransformInfo(
        x_min=x_min,
        y_max=y_max,
        pixelstorlek_x=1.0,
        pixelstorlek_y=1.0,
        bredd=b,
        hojd=h,
        epsg=3011,
        dtype="float32",
    )
    z = np.full((h, b), 10.0, dtype=np.float32)
    ms = np.full((h, b), 1, dtype=np.uint8)
    # Byggnad på körbanesidan i kol 6..9 (rader 0..5)
    ms[0:6, 6:10] = MARKSLAG_BYGGNAD
    z[0:6, 6:10] = np.float32(20.0)

    # 1. Helt ogiltig kantsten utanför rutnätet utan z_botten -> tydligt RannstenFel
    utanfor = [
        BrytlinjePost(
            typ="kantsten",
            hojd_m=0.12,
            koordinater=((x_min + 2.0, y_max + 5.0), (x_min + 12.0, y_max + 5.0)),
        )
    ]
    with pytest.raises(RannstenFel):
        diskretisera_rannsten_1d(utanfor, z, ms, geo, ds_mal_m=2.0, korbana_sida="vanster")

    # 2. Delvis över byggnad (kol 6..10 saknar aktiv kor_rc på vänster/norra sidan):
    #    hoppas över och delar kedjan i 2 skilda kedjor, aldrig zb = 0.0!
    delvis = [
        BrytlinjePost(
            typ="kantsten",
            hojd_m=0.12,
            koordinater=((x_min + 1.0, y_max - 6.0), (x_min + 15.0, y_max - 6.0)),
        )
    ]
    lank = diskretisera_rannsten_1d(delvis, z, ms, geo, ds_mal_m=2.0, korbana_sida="vanster")
    assert lank.hoppade_segment > 0
    assert lank.antal_kedjor == 2
    assert all(abs(seg.z_botten_m - 10.0) < 1e-6 for seg in lank.segment)


def test_kb1_gpkg_z_botten_rh2000_per_nod_rundtur(tmp_path: Path) -> None:
    """`skriv_brytlinjer_gpkg` + `las_brytlinjer_fran_gpkg` bevarar per-nod `z_botten_rh2000`."""
    ruta = Ruta(
        e_min_m=150000.0, n_min_m=6580000.0, storlek_m=40.0, overlapp_m=0.0, upplosning_m=1.0
    )
    linjer = [
        Brytlinje(
            typ="kantsten",
            hojd_m=0.12,
            punkter=((150002.0, 6580010.0), (150012.0, 6580010.0), (150022.0, 6580010.0)),
            z_kron_rh2000=(10.12, 9.62, 9.12),
            z_botten_rh2000=(10.0, 9.5, 9.0),
            tvarfall=0.025,
            kalla="baskarta_3d",
            osakerhet_m=0.02,
        ),
        Brytlinje(
            typ="mur",
            hojd_m=1.0,
            punkter=((150005.0, 6580025.0), (150015.0, 6580025.0)),
            kalla="typvarde",
        ),
    ]
    gpkg = skriv_brytlinjer_gpkg(tmp_path / "brytlinjer.gpkg", linjer, ruta)
    inlast = las_brytlinjer_fran_gpkg(gpkg)
    assert len(inlast) == 2
    assert inlast[0].z_kron_rh2000 == pytest.approx((10.12, 9.62, 9.12))
    assert inlast[0].z_botten_rh2000 == pytest.approx((10.0, 9.5, 9.0))
    assert inlast[1].z_botten_rh2000 is None


def test_kb1_varningar_vid_1d_fallback_enhet_precision_rust(tmp_path: Path) -> None:
    """Kopplad 1D/2D varnar explicit när `enhet`/`precision`/`anvand_rust` faller tillbaka."""
    a5b_indata = Path("testsvit/A5b_sned_gata_3d/indata").resolve()

    # 1. K21 `vatten.2d` (`karna.inbyggda.TvadLokalTroghet`)
    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "kb1-varning",
            "scenarier": {"bas": {"bas": str(a5b_indata)}},
            "analyser": [
                {
                    "id": "tvad",
                    "losare": "vatten.2d",
                    "parametrar": {
                        "regn_tid_s": [0.0, 20.0],
                        "regn_mm_h": [120.0, 120.0],
                        "slut_tid_s": 25.0,
                        "takvatten": False,
                        "rannsten_1d": "auto",
                        "enhet": "auto",
                        "precision": "f32",
                        "anvand_rust": True,
                    },
                }
            ],
        },
        tmp_path,
    )
    kor_utredning(fraga, ResultatCache(tmp_path / "cache"), tmp_path / "ut")
    run_k21 = json.loads((tmp_path / "ut" / "tvad" / "bas" / "run.json").read_text("utf-8"))
    varn_k21 = " | ".join(run_k21["varningar"])
    assert "enhet='auto'" in varn_k21
    assert "precision='f32'" in varn_k21
    assert "anvand_rust=true" in varn_k21

    # 2. `vatten.losare_2d._kor_2d`
    ut_k10 = tmp_path / "ut_k10"
    mat = _kor_2d(
        k1_filer(a5b_indata),
        {
            "regn_tid_s": [0.0, 20.0],
            "regn_mm_h": [120.0, 120.0],
            "slut_tid_s": 25.0,
            "takvatten": False,
            "rannsten_1d": "auto",
            "enhet": "auto",
            "precision": "f32",
        },
        ut_k10,
    )
    assert "varningar" in mat
    varn_k10 = " | ".join(mat["varningar"])
    assert "enhet='auto'" in varn_k10
    assert "precision='f32'" in varn_k10
    run_k10 = json.loads((ut_k10 / "run.json").read_text("utf-8"))
    assert len(run_k10["varningar"]) >= 2


# --- K-B7: 2D volymbalans() vid fast vattenniva_rh2000_m, xp initial max_djup, f32-varning ---


def _kaj_och_kanal() -> tuple[
    np.ndarray[tuple[int, int], np.dtype[np.float32]],
    np.ndarray[tuple[int, int], np.dtype[np.uint8]],
    GeoTransformInfo,
]:
    h, b = 10, 20
    z = np.full((h, b), 2.5, dtype=np.float32)
    z[:, 10:15] = np.float32(1.2)
    z[:, 15:] = np.float32(0.8)
    ms = np.full((h, b), 1, dtype=np.uint8)
    ms[:, 15:] = np.uint8(MARKSLAG_VATTEN)
    geo = GeoTransformInfo(
        x_min=150000.0,
        y_max=6400000.0,
        pixelstorlek_x=2.0,
        pixelstorlek_y=2.0,
        bredd=b,
        hojd=h,
        epsg=3011,
        dtype="float32",
    )
    return z, ms, geo


def test_kb7_volymbalans_med_fast_vattenniva_och_initialt_max_djup_xp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`volymbalans()` inkluderar rand-/initialvatten (`volymfel_rel < 1e-6`), och xp-vägen
    initierar `max_djup` med initialdjupet även vid 0 tidssteg."""
    z, ms, geo = _kaj_och_kanal()

    # 1. Fast vattennivå + litet regn på CPU: volymbalans() ska matcha res.volymfel_rel << 1e-6
    res_cpu = kor_2d_lokal_troghet(
        z,
        ms,
        geo,
        [0.0, 60.0],
        [5.0, 5.0],
        oppna_rander=False,
        vattenniva_rh2000_m=1.6,
    )
    bal_cpu = res_cpu.volymbalans()
    assert res_cpu.volymfel_rel < 1e-10
    assert float(bal_cpu["rand_in_m3"]) > 100.0
    assert float(bal_cpu["volymfel_rel"]) < 1e-10

    # 2. Mockad GPU xp-väg med 0 tidssteg (t_start == t_slut): max_djup ska ändå vara 0,8 m
    mock_xp = _MockGpuBackend()
    monkeypatch.setattr(gpu, "_hamta_cupy", lambda: mock_xp)

    res_xp_0 = kor_2d_lokal_troghet(
        z,
        ms,
        geo,
        [0.0, 0.0],
        [0.0, 0.0],
        slut_tid_s=0.0,
        oppna_rander=False,
        enhet="cuda",
        precision="f64",
        vattenniva_rh2000_m=1.6,
    )
    assert res_xp_0.antal_tidssteg == 0
    assert float(np.mean(res_xp_0.max_djup_m[:, 15:])) == pytest.approx(0.8, abs=1e-5)
    assert float(res_xp_0.volymbalans()["volymfel_rel"]) < 1e-10

    # 3. Mockad GPU xp-väg med flöde + regn: volymbalans() < 1e-6
    res_xp = kor_2d_lokal_troghet(
        z,
        ms,
        geo,
        [0.0, 60.0],
        [5.0, 5.0],
        oppna_rander=False,
        enhet="cuda",
        precision="f64",
        vattenniva_rh2000_m=1.6,
    )
    assert float(res_xp.volymbalans()["volymfel_rel"]) < 1e-10


def test_kb7_precision_f32_fallback_varnar_explicit(tmp_path: Path) -> None:
    """`precision='f32'` med `subgrid_faktor > 1` eller `kalltermer` varnar explicit (ej tyst)."""
    z, ms, geo = _kaj_och_kanal()
    ms_land = np.ones_like(ms)

    with pytest.warns(UserWarning, match="f32"):
        res_sub = kor_2d_lokal_troghet(
            z,
            ms_land,
            geo,
            [0.0, 30.0],
            [60.0, 60.0],
            subgrid_faktor=2,
            precision="f32",
        )
    assert res_sub.berakningsplats == "cpu_f64"
    assert len(res_sub.varningar) == 1
    assert "subgrid_faktor=2" in res_sub.varningar[0]

    kallt = Kalltermer(avtappning=Avtappning.likformig(z.shape, 10.0))
    with pytest.warns(UserWarning, match="kalltermer"):
        res_kt = kor_2d_lokal_troghet(
            z,
            ms_land,
            geo,
            [0.0, 30.0],
            [60.0, 60.0],
            kalltermer=kallt,
            precision="f32",
        )
    assert res_kt.berakningsplats == "cpu_f64"
    assert any("kalltermer" in v for v in res_kt.varningar)

    filer = skriv_tvad_resultat_k6(res_kt, geo, tmp_path / "k6_varn")
    run = json.loads(filer["run"].read_text(encoding="utf-8"))
    assert any("f32" in v for v in run["varningar"])
