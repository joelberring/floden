"""Ekologi (P1-4): rivna hus rensas ur NMD 51 och dPC-tabell per fläck exponeras i ekologi.json."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from data_inlasning.nmd import NmdLager, skriv_nmd
from ekologi.natverk import (
    NMD_BYGGNAD,
    NMD_EXPLOATERAD_MARK,
    NMD_OPPEN_MARK_MED_VEG,
    NMD_OPPEN_MARK_UTAN_VEG,
    rensa_rivna_hus_i_nmd,
)
from karna.analyslosare import LosarKontext
from karna.ekologi_losare import Konnektivitet
from karna.rutscenario import RutScenario, _las_ruta
from markmodell.byggnadsregister import ByggnadsPost, skriv_byggnader_gpkg
from testsvit.k1_io import skriv_geotiff


def test_rensa_rivna_hus_i_nmd_ersatter_bara_rivna_inom_k1_enligt_markslag() -> None:
    # NMD 6 × 6 celler (10 m/cell) över x=[0..60], y=[0..60].
    # K1 täcker mitten 4 × 4 celler: x=[10..50], y=[10..50].
    nmd = np.full((6, 6), 42, dtype=np.uint8)
    # Byggnader i NMD:
    nmd[0, 0] = NMD_BYGGNAD  # utanför K1 (kontextbuffert) -> ska behållas som 51
    nmd[1, 1] = NMD_BYGGNAD  # i K1, kvarvarande byggnad (K1=9) -> ska behållas som 51
    nmd[1, 2] = NMD_BYGGNAD  # i K1, riven -> gräs (K1=4) -> NMD 42
    nmd[2, 1] = NMD_BYGGNAD  # i K1, riven -> grus (K1=7) -> NMD 41
    nmd[2, 2] = NMD_BYGGNAD  # i K1, riven -> hårdgjort (K1=1) -> NMD 52

    k1 = np.full((4, 4), 4, dtype=np.uint8)
    k1[0, 0] = 9  # motsvarar nmd[1, 1]: byggnad finns kvar
    k1[0, 1] = 4  # motsvarar nmd[1, 2]: riven till gräs
    k1[1, 0] = 7  # motsvarar nmd[2, 1]: riven till grus
    k1[1, 1] = 1  # motsvarar nmd[2, 2]: riven till hårdgjort

    ut, rensade = rensa_rivna_hus_i_nmd(
        nmd,
        x_min_m=0.0,
        y_max_m=60.0,
        cell_m=10.0,
        k1_markslag=k1,
        k1_x_min_m=10.0,
        k1_y_max_m=50.0,
        k1_cell_m=10.0,
    )
    assert rensade == 3
    assert ut[0, 0] == NMD_BYGGNAD  # buffert oförändrad
    assert ut[1, 1] == NMD_BYGGNAD  # kvarvarande hus oförändrat
    assert ut[1, 2] == NMD_OPPEN_MARK_MED_VEG
    assert ut[2, 1] == NMD_OPPEN_MARK_UTAN_VEG
    assert ut[2, 2] == NMD_EXPLOATERAD_MARK


def _skapa_k1_ruta(mapp: Path, *, med_barriarhus: bool) -> Path:
    """K1 v2-ruta 200 × 200 m (10 m celler) med eller utan ett barriärhus i mitten."""
    mapp.mkdir(parents=True, exist_ok=True)
    cell = 10.0
    x_min, y_max = 150000.0, 6580200.0
    n = 20
    terrang = np.full((n, n), 10.0, dtype=np.float32)
    mark = terrang.copy()
    markslag = np.full((n, n), 4, dtype=np.uint8)  # gräs
    poster: list[ByggnadsPost] = []
    if med_barriarhus:
        # Barriärbyggnad i kolumner 9..10 (20 m bred, hela höjden rader 0..19).
        mark[:, 9:11] = 22.0
        markslag[:, 9:11] = 9
        x0, x1 = x_min + 9 * cell, x_min + 11 * cell
        y0, y1 = y_max - n * cell, y_max
        poster.append(
            ByggnadsPost(
                byggnad_id="barriar_1",
                polygon=((x0, y0), (x1, y0), (x1, y1), (x0, y1)),
                takhojd_rh2000=22.0,
                markhojd_rh2000=10.0,
                hojd_m=12.0,
                kalla="syntetisk",
                hojdkalla="laser_klass6",
                osakerhet_hojd_m=0.2,
            )
        )
    skriv_geotiff(mapp / "mark.tif", mark, x_min=x_min, y_max=y_max, pixelstorlek=cell)
    skriv_geotiff(mapp / "terrang.tif", terrang, x_min=x_min, y_max=y_max, pixelstorlek=cell)
    skriv_geotiff(mapp / "markslag.tif", markslag, x_min=x_min, y_max=y_max, pixelstorlek=cell)
    skriv_geotiff(
        mapp / "osakerhet.tif",
        np.full((n, n), 0.1, dtype=np.float32),
        x_min=x_min,
        y_max=y_max,
        pixelstorlek=cell,
    )
    bbox = (x_min, y_max - n * cell, x_min + n * cell, y_max)
    skriv_byggnader_gpkg(mapp / "byggnader.gpkg", poster, bbox)
    (mapp / "recept.json").write_text(
        json.dumps({"format": "k1/v2", "k1_version": 2}) + "\n", encoding="utf-8"
    )
    return mapp


def test_ekologi_losare_rensar_rivna_hus_i_nmd_och_exponerar_dpc_tabell(tmp_path: Path) -> None:
    # Syntetiskt NMD-lager 20 × 20 celler (10 m/cell = 200 × 200 m):
    # - Vänster barrskog (kol 0..6, 140 × 70 m = 0,98 ha >= 0,5 ha min_area)
    # - Höger barrskog (kol 13..19, 100 × 70 m = 0,70 ha >= 0,5 ha min_area)
    # - Mitten (kol 9..10): byggnadsbarriär i NMD (kod 51, motstånd 100)
    nmd = np.full((20, 20), 42, dtype=np.uint8)
    nmd[3:17, 0:7] = 111  # stor barrskogsfläck (~0,98 ha)
    nmd[5:15, 13:20] = 111  # mindre barrskogsfläck (~0,70 ha)
    nmd[:, 9:11] = NMD_BYGGNAD  # barriärhus i NMD

    nmd_fil = skriv_nmd(NmdLager(nmd, 150000.0, 6580200.0, 10.0), tmp_path / "nmd.tif")

    ruta_fore = _las_ruta(_skapa_k1_ruta(tmp_path / "fore", med_barriarhus=True), "r0")
    ruta_efter = _las_ruta(_skapa_k1_ruta(tmp_path / "efter", med_barriarhus=False), "r0")

    losare = Konnektivitet()
    param = losare.validera_parametrar({"natverk": "barrskog-nacka", "cellstorlek_m": 10.0})

    ut_fore = tmp_path / "ut_fore"
    ut_efter = tmp_path / "ut_efter"
    ut_fore.mkdir()
    ut_efter.mkdir()

    res_fore = losare.kor(
        LosarKontext(ut_fore, RutScenario("fore", "kf", (ruta_fore,)), param, {"nmd": nmd_fil}, {})
    )
    res_efter = losare.kor(
        LosarKontext(
            ut_efter, RutScenario("efter", "ke", (ruta_efter,)), param, {"nmd": nmd_fil}, {}
        )
    )

    # Rivning av barriärhuset (NMD 51 -> NMD 42, motstånd 100 -> 20) ska öka konnektiviteten (ECA)!
    assert res_efter.matvarden["eca_m2"] > res_fore.matvarden["eca_m2"]

    dok_fore = json.loads((ut_fore / "ekologi.json").read_text(encoding="utf-8"))
    dok_efter = json.loads((ut_efter / "ekologi.json").read_text(encoding="utf-8"))

    assert dok_fore["natverk"]["rivna_hus_rensade_celler"] == 0
    assert dok_efter["natverk"]["rivna_hus_rensade_celler"] == 40  # 20 × 2 celler rensade
    assert any("rensades från NMD 51" in v for v in dok_efter["varningar"])

    # dPC-tabell per fläck: rätt kolumner, sorterad fallande på dpc_procent, bakåtkompatibelt.
    tabell = dok_efter["natverk"]["dpc_tabell"]
    assert len(tabell) == dok_efter["antal_flackar"] == 2
    assert set(tabell[0]) == {
        "flack_id",
        "area_ha",
        "dpc_procent",
        "dpc_intra",
        "dpc_flux",
        "dpc_connector",
    }
    assert tabell[0]["dpc_procent"] >= tabell[1]["dpc_procent"]
    for rad, orig in zip(tabell, dok_efter["dPC"], strict=True):
        assert rad["flack_id"] == orig["flack_id"]
        assert rad["area_ha"] == pytest.approx(orig["area_m2"] / 10_000.0, abs=1e-6)
        assert rad["dpc_procent"] == pytest.approx(orig["dPC_procent"], abs=1e-9)
        assert rad["dpc_intra"] + rad["dpc_flux"] + rad["dpc_connector"] == pytest.approx(
            rad["dpc_procent"], abs=1e-6
        )
