"""Tester för P0-2: utlopps-/vattennivårandvillkor för vattenytor (markslag == 8) i 2D-lösaren."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from gemensamt import gpu
from testsvit.k1_io import GeoTransformInfo, skriv_geotiff
from tvad.lokal_troghet import MARKSLAG_VATTEN, TvadFel, kor_2d_lokal_troghet
from vatten.blaytor import BlaytorFel
from vatten.losare import registrera_bas
from vatten.losare_2d import _kor_2d, nod_2d


class _MockGpuBackend:
    """Kör `_kor_lokal_troghet_xp` på CPU via NumPy för verifiering utan CUDA."""

    def __init__(self) -> None:
        self.cuda = SimpleNamespace(
            Stream=SimpleNamespace(null=SimpleNamespace(synchronize=lambda: None))
        )

    def __getattr__(self, namn: str) -> Any:
        return getattr(np, namn)


def _lutande_gata_mot_kanal(
    *, kanal_markslag: int = MARKSLAG_VATTEN
) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any], GeoTransformInfo]:
    """Syntetisk lutande gata (kol 0..23, z = 4.0 -> 1.7 m) mot kanal (kol 24..29, z = 1.0 m).

    Stängda sidoränder (`oppna_rander=False`) så allt utflöde sker via vattenytans randvillkor.
    """
    h, b = 12, 30
    z = np.zeros((h, b), dtype=np.float32)
    for c in range(24):
        z[:, c] = np.float32(4.0 - 0.1 * c)  # lutar jämnt ned mot kanalen (1.7 m vid kol 23)
    z[:, 24:] = np.float32(1.0)  # kanalens vattenyta vid +1.0 m RH 2000
    markslag = np.full((h, b), 1, dtype=np.uint8)  # hårdgjort
    markslag[:, 24:] = np.uint8(kanal_markslag)
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
    return z, markslag, geo


def test_lutande_gata_draneras_ut_i_kanal_utan_att_staplas() -> None:
    """Regnvatten som når kanalen (markslag == 8) dräneras till ut_m3 i stället för att staplas."""
    z_v, ms_v, geo = _lutande_gata_mot_kanal(kanal_markslag=MARKSLAG_VATTEN)
    z_h, ms_h, _ = _lutande_gata_mot_kanal(kanal_markslag=1)

    res_kanal = kor_2d_lokal_troghet(
        z_v, ms_v, geo, [0.0, 120.0], [90.0, 90.0], slut_tid_s=300.0, oppna_rander=False
    )
    res_grop = kor_2d_lokal_troghet(
        z_h, ms_h, geo, [0.0, 120.0], [90.0, 90.0], slut_tid_s=300.0, oppna_rander=False
    )

    # Utan vattenyterandvillkor staplas allt regn i den stängda kanalen (ut_m3 == 0).
    assert res_grop.ut_m3 == 0.0
    assert float(np.max(res_grop.slut_djup_m[:, 24:])) > 0.01

    # Med vattenyterandvillkor (markslag == 8) dräneras både direktregn och gatuavrinning ut!
    assert float(np.max(res_kanal.max_djup_m[:, 24:])) == 0.0
    assert float(np.max(res_kanal.slut_djup_m[:, 24:])) == 0.0
    assert res_kanal.ut_m3 > 0.85 * res_kanal.in_m3  # nästan allt har runnit ned i kanalen
    assert res_kanal.lagrat_m3 < res_grop.lagrat_m3 * 0.2

    # Exakt massbalans (<< 1e-3)
    assert res_kanal.volymfel_rel < 1e-10
    assert float(res_kanal.volymbalans()["volymfel_rel"]) < 1e-10


def test_hogvatten_i_kanal_dammer_lag_kaj_med_exakt_massbalans() -> None:
    """Med vattenniva_rh2000_m > z_bed dämmer kanalen en låg kaj och bevarar massbalansen."""
    h, b = 10, 24
    z = np.full((h, b), 2.5, dtype=np.float32)
    z[:, 12:18] = np.float32(1.2)  # låg kaj (+1.2 m RH 2000)
    z[:, 18:] = np.float32(0.8)  # kanal/hav (+0.8 m normalnivå)
    markslag = np.full((h, b), 1, dtype=np.uint8)
    markslag[:, 18:] = np.uint8(MARKSLAG_VATTEN)
    geo = GeoTransformInfo(
        x_min=150000.0, y_max=6400000.0, pixelstorlek_x=2.0, pixelstorlek_y=2.0,
        bredd=b, hojd=h, epsg=3011, dtype="float32",
    )

    # Högvattenstånd +1.6 m RH 2000 utan regn -> vattnet ska flöda in över den låga kajen (+1.2 m)
    res = kor_2d_lokal_troghet(
        z, ms := markslag, geo, [0.0, 120.0], [0.0, 0.0],
        oppna_rander=False, vattenniva_rh2000_m=1.6,
    )
    assert ms[0, 18] == MARKSLAG_VATTEN
    assert float(np.mean(res.slut_djup_m[:, 18:])) == pytest.approx(0.8, abs=1e-5)
    assert float(np.mean(res.slut_djup_m[:, 13:17])) == pytest.approx(0.4, abs=0.05)
    assert float(np.max(res.max_djup_m[:, :10])) == 0.0  # högt land (+2.5 m) förblir torrt
    assert res.volymfel_rel < 1e-10


def test_xp_vag_och_cpu_vag_ger_paritet_och_massbalans(monkeypatch: pytest.MonkeyPatch) -> None:
    """Både CPU- och xp-vägen (f64 och f32) bevarar massbalansen med markslag == 8."""
    mock_xp = _MockGpuBackend()
    monkeypatch.setattr(gpu, "_hamta_cupy", lambda: mock_xp)

    z, ms, geo = _lutande_gata_mot_kanal()
    for vniva in (None, 1.5):
        ref = kor_2d_lokal_troghet(
            z, ms, geo, [0.0, 90.0], [80.0, 80.0], slut_tid_s=150.0,
            oppna_rander=False, enhet="cpu", vattenniva_rh2000_m=vniva,
        )
        res_f64 = kor_2d_lokal_troghet(
            z, ms, geo, [0.0, 90.0], [80.0, 80.0], slut_tid_s=150.0,
            oppna_rander=False, enhet="cuda", precision="f64", vattenniva_rh2000_m=vniva,
        )
        res_f32 = kor_2d_lokal_troghet(
            z, ms, geo, [0.0, 90.0], [80.0, 80.0], slut_tid_s=150.0,
            oppna_rander=False, enhet="cuda", precision="f32", vattenniva_rh2000_m=vniva,
        )

        assert float(np.max(np.abs(res_f64.max_djup_m - ref.max_djup_m))) < 1e-6
        assert abs(res_f64.ut_m3 - ref.ut_m3) < 1e-9
        assert res_f64.volymfel_rel < 1e-10
        assert res_f32.volymfel_rel < 1e-3
        assert float(np.mean(np.abs(res_f32.max_djup_m - ref.max_djup_m))) < 1e-3


def test_subgrid_och_bakatkompatibilitet_utan_markslag_8() -> None:
    """Subgrid stöder markslag == 8; domäner utan markslag == 8 förblir 100 % oförändrade."""
    z, ms, geo = _lutande_gata_mot_kanal()
    res_sub = kor_2d_lokal_troghet(
        z, ms, geo, [0.0, 60.0], [90.0, 90.0], slut_tid_s=120.0,
        oppna_rander=False, subgrid_faktor=2,
    )
    assert res_sub.ut_m3 > 0.0
    assert res_sub.volymfel_rel < 1e-10

    # Utan markslag == 8: helt oförändrat oavsett vattenniva_rh2000_m
    ms_utan = np.full_like(ms, 1)
    r0 = kor_2d_lokal_troghet(z, ms_utan, geo, [0.0, 60.0], [60.0, 60.0])
    r1 = kor_2d_lokal_troghet(z, ms_utan, geo, [0.0, 60.0], [60.0, 60.0], vattenniva_rh2000_m=2.0)
    assert np.array_equal(r0.max_djup_m, r1.max_djup_m)
    assert r0.ut_m3 == r1.ut_m3 and r0.lagrat_m3 == r1.lagrat_m3

    with pytest.raises(TvadFel, match="vattenniva_rh2000_m"):
        kor_2d_lokal_troghet(
            z, ms, geo, [0.0, 60.0], [60.0, 60.0], vattenniva_rh2000_m=float("nan")
        )


def test_losare_2d_vidarebefordrar_och_validerar_vattenniva(tmp_path: Path) -> None:
    """`vatten.2d` (`losare_2d.py`) stöder `vattenniva_rh2000_m` och avvisar ogiltiga värden."""
    from karna.scenario import Planscenario

    z, ms, geo = _lutande_gata_mot_kanal()
    k1 = tmp_path / "k1"
    skriv_geotiff(
        k1 / "mark.tif", z, x_min=geo.x_min, y_max=geo.y_max, pixelstorlek=geo.pixelstorlek_x
    )
    skriv_geotiff(
        k1 / "markslag.tif", ms, x_min=geo.x_min, y_max=geo.y_max, pixelstorlek=geo.pixelstorlek_x
    )
    skriv_geotiff(
        k1 / "osakerhet.tif",
        np.full_like(z, 0.05),
        x_min=geo.x_min,
        y_max=geo.y_max,
        pixelstorlek=geo.pixelstorlek_x,
    )
    bas_id = "test_vattenyta_bas_" + tmp_path.name
    registrera_bas(bas_id, k1)
    scen = Planscenario(scenario_id="s", bas_recept=(bas_id,), andringar=())

    nod = nod_2d(
        scen,
        {
            "regn_tid_s": [0.0, 60.0],
            "regn_mm_h": [90.0, 90.0],
            "vattenniva_rh2000_m": 1.0,
            "takvatten": False,
        },
    )
    indata_filer = {k: v for k, v in nod.indata.items() if isinstance(v, Path)}
    mat = _kor_2d(indata_filer, nod.parametrar, tmp_path / "ut")
    assert mat["ut_m3"] > 0.0
    assert mat["volymfel_rel"] < 1e-10

    with pytest.raises(BlaytorFel, match="vattenniva_rh2000_m"):
        nod_2d(
            scen,
            {
                "regn_tid_s": [0.0, 60.0],
                "regn_mm_h": [90.0, 90.0],
                "vattenniva_rh2000_m": float("inf"),
            },
        )
