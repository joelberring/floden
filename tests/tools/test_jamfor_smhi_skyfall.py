# ruff: noqa: E501
"""Tester för T09b CLI (`tools/jamfor_smhi_skyfall.py`) och `data/referens/goteborg_checkpoints.json`."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
from pyproj import CRS, Transformer
from rasterio.transform import from_origin

from benchmark.format import kontrollera
from tools.jamfor_smhi_skyfall import main

REPO = Path(__file__).resolve().parents[2]
CHECKPOINTS_FIL = REPO / "data" / "referens" / "goteborg_checkpoints.json"


def _skriv_geotiff(
    sokvag: Path,
    data: np.ndarray[Any, Any],
    *,
    x0: float,
    y1: float,
    cell_m: float,
    epsg: int = 3011,
    nodata: float | int | None = None,
) -> Path:
    transform = from_origin(x0, y1, cell_m, cell_m)
    with rasterio.open(
        sokvag,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype=data.dtype,
        crs=f"EPSG:{epsg}",
        transform=transform,
        nodata=nodata,
    ) as dst:
        dst.write(data, 1)
    return sokvag


def test_raster_jamforelse_exakta_matt_omprojicering_och_vattenmask(tmp_path: Path) -> None:
    """Verifierar exakta MAE/RMSE/Bias/IoU-värden, automatisk omprojicering (3006->3011) och vattenmask."""
    x0, y1, cell_m = -211000.0, 6415000.0, 10.0
    h, w = 20, 20

    # Floden-raster i EPSG:3011:
    # - kol 0..3: markslag 8 (vatten, ska exkluderas trots stort fel = 5.0 m)
    # - kol 4..11: djup 0.30 m (ref = 0.20 m => diff = +0.10 m, båda >= 0.10 m => TP)
    # - kol 12..15: djup 0.20 m (ref = 0.00 m => diff = +0.20 m => FP vid 0.10 m)
    # - kol 16..19: djup 0.00 m (ref = 0.00 m => diff = 0.00 m => TN)
    floden = np.zeros((h, w), dtype=np.float32)
    floden[:, 0:4] = 5.0
    floden[:, 4:12] = 0.30
    floden[:, 12:16] = 0.20

    ref_3011 = np.zeros((h, w), dtype=np.float32)
    ref_3011[:, 4:12] = 0.20

    markslag = np.full((h, w), 1, dtype=np.uint8)
    markslag[:, 0:4] = 8  # vatten

    f_fil = _skriv_geotiff(tmp_path / "floden.tif", floden, x0=x0, y1=y1, cell_m=cell_m, epsg=3011)
    r_fil = _skriv_geotiff(tmp_path / "ref.tif", ref_3011, x0=x0, y1=y1, cell_m=cell_m, epsg=3011)
    m_fil = _skriv_geotiff(tmp_path / "markslag.tif", markslag, x0=x0, y1=y1, cell_m=cell_m, epsg=3011)
    ut_json = tmp_path / "ut.json"
    ut_md = tmp_path / "ut.md"

    rc = main(
        [
            "--floden-raster",
            str(f_fil),
            "--ref-raster",
            str(r_fil),
            "--mask-vatten",
            str(m_fil),
            "--troskel-m",
            "0.10",
            "--ut-json",
            str(ut_json),
            "--ut-md",
            str(ut_md),
        ]
    )
    assert rc == 0
    assert ut_json.is_file() and ut_md.is_file()

    dok = json.loads(ut_json.read_text(encoding="utf-8"))
    kontrollera(dok)  # K37-validering!

    rj = dok["delar"]["skyfall"]["fall"][0]["detaljer"]["raster_jamforelse"]
    # 20 rader x 16 ej-vattenkolumner = 320 jämförda celler, 80 maskerade vattenceller
    assert rj["antal_celler_jamforda"] == 320
    assert rj["maskerade_vattenceller"] == 80

    # 8 kol med diff 0.10, 4 kol med diff 0.20, 4 kol med diff 0.00
    forvantad_mae = (8 * 0.10 + 4 * 0.20 + 4 * 0.00) / 16.0  # 0.10 m
    forvantad_rmse = math.sqrt((8 * 0.01 + 4 * 0.04) / 16.0)  # sqrt(0.015)
    assert rj["mae_m"] == pytest.approx(forvantad_mae, abs=1e-6)
    assert rj["rmse_m"] == pytest.approx(forvantad_rmse, abs=1e-6)
    assert rj["bias_m"] == pytest.approx(forvantad_mae, abs=1e-6)

    # TP = 160, FP = 80, FN = 0 => IoU = 160/240 = 2/3, precision = 2/3, recall = 1.0
    assert rj["iou_vat"] == pytest.approx(2.0 / 3.0, abs=1e-6)
    assert rj["precision_vat"] == pytest.approx(2.0 / 3.0, abs=1e-6)
    assert rj["recall_vat"] == pytest.approx(1.0, abs=1e-6)
    assert rj["vat_yta_floden_m2"] == pytest.approx(240 * 100.0)
    assert rj["vat_yta_ref_m2"] == pytest.approx(160 * 100.0)
    assert [t["troskel_m"] for t in rj["trosklar"]] == [0.05, 0.1, 0.3, 0.5]

    # Kontrollera SHA-256 proveniens
    sha_map = dok["delar"]["skyfall"]["fall"][0]["detaljer"]["proveniens"]["indata_sha256"]
    assert set(sha_map) == {"floden_raster", "ref_raster", "mask_vatten"}
    assert all(len(v["sha256"]) == 64 for v in sha_map.values())


def test_raster_jamforelse_omprojicerar_fran_epsg3006_med_skala(tmp_path: Path) -> None:
    """Konstant referensraster i EPSG:3006 omprojiceras automatiskt till EPSG:3011 med --skala-m."""
    x0, y1, cell_m = -211000.0, 6415000.0, 2.0
    floden = np.full((50, 50), 0.35, dtype=np.float32)
    f_fil = _skriv_geotiff(tmp_path / "f.tif", floden, x0=x0, y1=y1, cell_m=cell_m, epsg=3011)

    # Skapa ett referensraster i EPSG:3006 som täcker hela Floden-området med konstant djup 0.25 m
    tr = Transformer.from_crs(CRS.from_epsg(3011), CRS.from_epsg(3006), always_xy=True)
    cx, cy = tr.transform(x0 + 50.0, y1 - 50.0)
    ref_3006 = np.full((40, 40), 0.25, dtype=np.float32)
    r_fil = _skriv_geotiff(
        tmp_path / "ref_3006.tif",
        ref_3006,
        x0=float(cx) - 200.0,
        y1=float(cy) + 200.0,
        cell_m=10.0,
        epsg=3006,
    )
    ut_json = tmp_path / "skala.json"

    rc = main(
        [
            "--floden-raster",
            str(f_fil),
            "--ref-raster",
            str(r_fil),
            "--skala-m",
            "10.0",
            "--ut-json",
            str(ut_json),
        ]
    )
    assert rc == 0
    dok = json.loads(ut_json.read_text(encoding="utf-8"))
    kontrollera(dok)
    rj = dok["delar"]["skyfall"]["fall"][0]["detaljer"]["raster_jamforelse"]
    assert rj["cellstorlek_m"] == pytest.approx(10.0)
    assert rj["antal_celler_jamforda"] == 100
    assert rj["mae_m"] == pytest.approx(0.10, abs=1e-5)
    assert rj["iou_vat"] == pytest.approx(1.0, abs=1e-6)


def test_kontrollpunkter_mot_riktiga_goteborg_checkpoints_json(tmp_path: Path) -> None:
    """Verifierar `data/referens/goteborg_checkpoints.json` (0 PII, giltig schema) och punktutvärdering."""
    assert CHECKPOINTS_FIL.is_file()
    cp_data = json.loads(CHECKPOINTS_FIL.read_text(encoding="utf-8"))
    assert cp_data["schema_version"] == "floden-referens-punkter/v1"
    assert cp_data["smhi_skyfallskartering_2026"]["modell"] == "LISFLOOD-FP v8.2.1"
    lager_par = {
        (lager["id_lagpunkter"], lager["id_ytavrinning"])
        for lager in cp_data["lsto_wms"]["lager_goteborg"]
    }
    assert lager_par == {(15, 16), (30, 31), (39, 40), (48, 49)}

    # Skapa ett syntetiskt EPSG:3011-raster som täcker Lindholmen (omvandla bbox från EPSG:3007)
    tr = Transformer.from_crs(CRS.from_epsg(3007), CRS.from_epsg(3011), always_xy=True)
    e_min, n_min = tr.transform(146050.0, 6398450.0)
    e_max, n_max = tr.transform(146650.0, 6399050.0)
    cell_m = 2.0
    w = int(math.ceil((e_max - e_min) / cell_m)) + 4
    h = int(math.ceil((n_max - n_min) / cell_m)) + 4
    x0, y1 = float(e_min) - 4.0, float(n_max) + 4.0
    arr = np.zeros((h, w), dtype=np.float32)

    # Sätt exakta värden på Lindholmens 3 kontrollpunkter inom respektive intervall
    for p, varde in (
        ("lindholmen_utvecklingsgatan", 0.25),
        ("lindholmen_planomrade_max", 0.35),
        ("lindholmen_amfiteater_lagpunkt", 0.12),
    ):
        post = next(x for x in cp_data["punkter"] if x["id"] == p)
        px_e, px_n = tr.transform(post["e_m"], post["n_m"])
        col = int(math.floor((px_e - x0) / cell_m))
        row = int(math.floor((y1 - px_n) / cell_m))
        arr[row, col] = varde
        # Lägg en lite djupare granne i 3x3 för att verifiera att 3x3 max skiljer sig från exakt cell
        arr[row, col + 1] = varde + 0.04

    f_fil = _skriv_geotiff(tmp_path / "lindholmen.tif", arr, x0=x0, y1=y1, cell_m=cell_m, epsg=3011)
    ut_json = tmp_path / "lindholmen_t09b.json"
    ut_md = tmp_path / "lindholmen_t09b.md"

    rc = main(
        [
            "--floden-raster",
            str(f_fil),
            "--ref-punkter",
            str(CHECKPOINTS_FIL),
            "--omrade",
            "lindholmen",
            "--krav-godkand",
            "--ut-json",
            str(ut_json),
            "--ut-md",
            str(ut_md),
        ]
    )
    assert rc == 0
    dok = json.loads(ut_json.read_text(encoding="utf-8"))
    kontrollera(dok)

    pj = dok["delar"]["skyfall"]["fall"][0]["detaljer"]["punkt_jamforelse"]
    assert pj["antal_punkter_totalt"] == 3
    assert pj["antal_djup_utvarderade"] == 3
    assert pj["antal_inom_intervall_cell"] == 3
    utv = next(p for p in pj["punkter"] if p["id"] == "lindholmen_utvecklingsgatan")
    assert utv["floden_cell_m"] == pytest.approx(0.25, abs=1e-5)
    assert utv["floden_max_3x3_m"] == pytest.approx(0.29, abs=1e-5)
    assert utv["inom_intervall"] is True
    assert "lindholmen_utvecklingsgatan" in ut_md.read_text(encoding="utf-8")


def test_cli_felkoder_och_krav_godkand(tmp_path: Path) -> None:
    f_fil = _skriv_geotiff(
        tmp_path / "tom.tif", np.zeros((10, 10), dtype=np.float32), x0=0.0, y1=100.0, cell_m=10.0
    )
    # Saknar både --ref-raster och --ref-punkter => exit 2
    assert main(["--floden-raster", str(f_fil)]) == 2
    # Saknad fil => exit 2
    assert main(["--floden-raster", str(tmp_path / "finns_inte.tif"), "--ref-punkter", str(CHECKPOINTS_FIL)]) == 2
