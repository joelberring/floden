"""Tester för bigraderad bifurkationsdetektor (`src/sankgraf/bifurkation.py`)."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from gemensamt.koordinater import EPSG_INTERN
from sankgraf.bifurkation import (
    detektera_bifurkationer,
    exportera_bifiltrering_json,
    skriv_bifurkationer_gpkg,
    skriv_bifurkationer_json,
)
from sankgraf.hierarki import bygg_sankgraf
from testsvit.k1_io import GeoTransformInfo


def _tva_utlopp_terrang(
    delta_z_m: float,
    sigma_m: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, GeoTransformInfo]:
    """Syntetisk terräng: mittersta sänkan (z=10.0) har två konkurrerande sadlar.

    - Vänster sadel mot bassäng V (z=9.0): höjd 10.50 m (primärt utlopp)
    - Höger sadel mot bassäng H (z=9.0): höjd 10.50 + delta_z_m (alternativt utlopp)
    Ytterramen är 12.0 m förutom öppningar ut från V och H (9.5 m) så båda är egna sänkor.
    """
    h, w = 9, 15
    z = np.full((h, w), 12.0, dtype=np.float32)

    # Bassäng V (kol 1..3, rad 2..6), botten 9.0, utlopp mot vänster kant vid (4, 0) = 9.5
    z[2:7, 1:4] = 9.0
    z[4, 0] = 9.5

    # Mittsänka M (kol 5..9, rad 2..6), botten 10.0
    z[2:7, 5:10] = 10.0

    # Bassäng H (kol 11..13, rad 2..6), botten 9.0, utlopp mot höger kant vid (4, 14) = 9.5
    z[2:7, 11:14] = 9.0
    z[4, 14] = 9.5

    # Sadel M -> V vid (4, 4): 10.50 m
    z[4, 4] = 10.50
    # Sadel M -> H vid (4, 10): 10.50 + delta_z_m
    z[4, 10] = np.float32(10.50 + delta_z_m)

    osak = np.full((h, w), sigma_m, dtype=np.float32)
    markslag = np.ones((h, w), dtype=np.uint8)
    geo = GeoTransformInfo(
        bredd=w,
        hojd=h,
        x_min=150000.0,
        y_max=6580000.0 + float(h),
        pixelstorlek_x=1.0,
        pixelstorlek_y=1.0,
        epsg=EPSG_INTERN,
        dtype="float32",
    )
    return z, osak, markslag, geo


def test_instabil_sanka_flaggas_med_bada_utloppen() -> None:
    """Δz = 0,04 m och σ = 0,05 m (k = 2) ska flaggas som topologiskt instabil."""
    z, osak, markslag, geo = _tva_utlopp_terrang(delta_z_m=0.04, sigma_m=0.05)
    graf = bygg_sankgraf(z, osak, markslag, geo)

    mitt_id = int(graf.cell_till_nod[4, 7])
    vanster_id = int(graf.cell_till_nod[4, 2])
    hoger_id = int(graf.cell_till_nod[4, 12])
    assert len({mitt_id, vanster_id, hoger_id}) == 3

    punkter = detektera_bifurkationer(graf, osakerhet_m=osak, k_sigma=2.0, referensregn_mm=100.0)
    mitt_punkter = [p for p in punkter if p.nod_id == mitt_id]
    assert len(mitt_punkter) == 1
    p = mitt_punkter[0]
    assert p.instabil is True
    assert p.primar_ut_till_id == vanster_id
    assert p.alternativ_ut_till_id == hoger_id
    assert p.marginal_m == pytest.approx(0.04, abs=1e-4)
    assert p.osakerhet_komb_m == pytest.approx(math.hypot(0.05, 0.05), rel=1e-4)
    assert p.sakerhetsmarginal == pytest.approx(0.04 / math.hypot(0.05, 0.05), rel=1e-3)
    assert p.paverkad_avrinningsyta_m2 >= 25.0
    assert p.paverkad_volym_m3 == pytest.approx(p.paverkad_avrinningsyta_m2 * 0.1, rel=1e-9)


def test_stabil_sanka_flaggas_inte() -> None:
    """Δz = 0,50 m och σ = 0,02 m (k = 2) ska inte flaggas som instabil."""
    z, osak, markslag, geo = _tva_utlopp_terrang(delta_z_m=0.50, sigma_m=0.02)
    graf = bygg_sankgraf(z, osak, markslag, geo)
    mitt_id = int(graf.cell_till_nod[4, 7])

    instabila = detektera_bifurkationer(graf, osakerhet_m=osak, k_sigma=2.0)
    assert all(p.nod_id != mitt_id for p in instabila)

    alla = detektera_bifurkationer(graf, osakerhet_m=osak, k_sigma=2.0, inkludera_stabila=True)
    mitt = next(p for p in alla if p.nod_id == mitt_id)
    assert mitt.instabil is False
    assert mitt.marginal_m == pytest.approx(0.50, abs=1e-4)
    assert mitt.sakerhetsmarginal > 15.0


def test_bifurkation_determinism_och_export(tmp_path: Path) -> None:
    """Två körningar ger exakt samma utdata, och JSON/GPKG/bifiltrering kan exporteras."""
    z, osak, markslag, geo = _tva_utlopp_terrang(delta_z_m=0.04, sigma_m=0.05)
    graf = bygg_sankgraf(z, osak, markslag, geo)

    p1 = detektera_bifurkationer(graf, osakerhet_m=osak, k_sigma=2.0)
    p2 = detektera_bifurkationer(graf, osakerhet_m=osak, k_sigma=2.0)
    assert p1 == p2
    assert len(p1) >= 1

    # JSON-sammanfattning
    json_fil = skriv_bifurkationer_json(p1, tmp_path / "bifurkationer.json")
    data = json.loads(json_fil.read_text(encoding="utf-8"))
    assert data["format"] == "floden-bifurkationer/v1"
    assert data["antal_instabila"] == len(p1)
    assert len(data["punkter"]) == len(p1)

    # GeoPackage
    gpkg_fil = skriv_bifurkationer_gpkg(p1, tmp_path / "bifurkationer.gpkg")
    con = sqlite3.connect(str(gpkg_fil))
    try:
        rader = con.execute("SELECT nod_id, marginal_m, instabil FROM bifurkationer").fetchall()
    finally:
        con.close()
    assert len(rader) == len(p1)
    assert all(r[2] == 1 for r in rader)

    # Bigraderad export utan GIS-beroenden (för Q2 / forskningsparten)
    bifilt_fil = exportera_bifiltrering_json(graf, tmp_path / "bifiltrering.json", osakerhet_m=osak)
    bf = json.loads(bifilt_fil.read_text(encoding="utf-8"))
    assert bf["format"] == "floden-bifiltrering/v1"
    assert bf["antal_noder"] == len(graf.noder)
    assert bf["antal_sadelkanter"] >= 3
