"""Tester för 3D-baskarta och sammanslagning med DEM (`markmodell.baskarta_3d`)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from markmodell.baskarta_3d import (
    BASKARTA_3D_OSAKERHET_M,
    Linje3D,
    kombinera_3d_baskarta_med_dem,
    las_3d_baskarta_gpkg,
    skriv_3d_baskarta_gpkg,
    skriv_baskarta_3d_k1,
)
from markmodell.rutnat import Ruta
from testsvit.k1_io import GeoTransformInfo, las_gpkg_tabell


def test_syntetisk_3d_gata_med_sankt_infart_och_grov_dem(tmp_path: Path) -> None:
    """Sned 3D-gata (20°) med 12 cm kantsten och 4 m sänkt infart (+2 cm) mot 1 m DEM."""
    theta = math.radians(20.0)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    e0, n0 = 150010.0, 6580020.0
    lutning = 0.01  # 1 % längslutning

    def _punkt(s_m: float, dz: float) -> tuple[float, float, float]:
        e = e0 + s_m * cos_t
        n = n0 + s_m * sin_t
        z_gata = 20.0 - lutning * s_m
        return (e, n, z_gata + dz)

    # 3D-GeoPackage: kantsten 0..20 m (+0,12), infart 20..24 m (+0,02), kantsten 24..40 m
    linjer_in = [
        Linje3D(typ="kantsten", punkter_3d=(_punkt(0.0, 0.12), _punkt(20.0, 0.12))),
        Linje3D(typ="sankt_kantsten", punkter_3d=(_punkt(20.0, 0.02), _punkt(24.0, 0.02))),
        Linje3D(typ="kantsten", punkter_3d=(_punkt(24.0, 0.12), _punkt(40.0, 0.12))),
        # En 2D-linje utan Z för att verifiera att kalla="typvarde" och förhöjd osäkerhet sätts
        Linje3D(
            typ="kantsten", punkter_3d=((150010.0, 6580045.0, None), (150030.0, 6580045.0, None))
        ),
    ]
    gpkg_in = skriv_3d_baskarta_gpkg(tmp_path / "baskarta_3d.gpkg", linjer_in)

    # Läs tillbaka 3D-baskartan
    inlast = las_3d_baskarta_gpkg(gpkg_in)
    assert len(inlast) == 4
    assert inlast[0].har_z and inlast[1].typ == "sankt_kantsten"
    assert not inlast[3].har_z

    # Grov 1 m DEM (sluttande plan längs gatan, utan kantstenar)
    hojd, bredd = 60, 60
    geo = GeoTransformInfo(
        x_min=150000.0,
        y_max=6580060.0,
        pixelstorlek_x=1.0,
        pixelstorlek_y=1.0,
        bredd=bredd,
        hojd=hojd,
        epsg=3011,
        dtype="float32",
    )
    rr, cc = np.mgrid[0:hojd, 0:bredd]
    ee = geo.x_min + (cc + 0.5) * geo.pixelstorlek_x
    nn = geo.y_max - (rr + 0.5) * geo.pixelstorlek_y
    s_proj = (ee - e0) * cos_t + (nn - n0) * sin_t
    mark_z = (20.0 - lutning * s_proj).astype(np.float32)

    res = kombinera_3d_baskarta_med_dem(inlast, mark_z, geo)
    assert res.antal_3d_linjer == 3
    assert res.antal_typvarde_linjer == 1
    assert int(np.count_nonzero(res.foradlad_mask)) > 20
    assert 0.08 < res.medel_skillnad_m < 0.12
    assert abs(res.max_skillnad_m - 0.12) < 0.01

    # Kontrollera exakta 3D-höjder och typvärdesmarkering
    k1, infart, k2, k_2d = res.brytlinjer
    assert k1.typ == "kantsten" and abs(k1.hojd_m - 0.12) < 1e-3
    assert k1.kalla == "baskarta_3d" and k1.osakerhet_m == BASKARTA_3D_OSAKERHET_M
    assert k1.z_kron_rh2000 is not None
    assert abs(k1.z_kron_rh2000[0] - 20.12) < 1e-6
    assert abs(k1.z_kron_rh2000[1] - 19.92) < 1e-6

    assert infart.typ == "sankt_kantsten" and abs(infart.hojd_m - 0.02) < 1e-3
    assert infart.z_kron_rh2000 is not None
    assert abs(infart.z_kron_rh2000[0] - 19.82) < 1e-6

    assert k2.typ == "kantsten" and abs(k2.hojd_m - 0.12) < 1e-3
    assert k_2d.kalla == "typvarde" and k_2d.osakerhet_m == 0.10
    assert res.varningar

    # Skriv K1 brytlinjer.gpkg + run.json och verifiera bakåtkompatibilitet
    ruta = Ruta(
        e_min_m=150000.0, n_min_m=6580000.0, storlek_m=60.0, overlapp_m=0.0, upplosning_m=1.0
    )
    filer = skriv_baskarta_3d_k1(tmp_path / "k1_ut", res, ruta, indata_filer=(gpkg_in,))
    srs_id, rader = las_gpkg_tabell(filer["brytlinjer"], "brytlinjer")
    assert srs_id == 3011
    assert len(rader) == 4
    assert rader[1]["typ"] == "sankt_kantsten"
    assert rader[3]["kalla"] == "typvarde"

    run = json.loads(filer["run"].read_text(encoding="utf-8"))
    assert run["matvarden"]["dem_max_skillnad_m"] > 0.10
    assert run["matvarden"]["foradlade_celler"] > 20
