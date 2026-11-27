"""Regressionstester för `buller.losare_k21` (inkl. Etapp 1A `bryt_mur_*`-överlapp på Haga)."""

from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.geometry import LineString

from buller.losare_k21 import (
    METODSTATUS,
    NORD2000_KONFORM,
    RAPPORT_SPARRAD,
    BullerNord2000,
    _rensa_overlappande_byggnader,
    _skarm_till_byggnader,
    _SkarmLinje,
)
from buller.scen.bullerscen import Bullerscen
from karna.analyslosare import LosarKontext
from karna.geometri.rasterfil import ruta_fran_geo
from karna.rutscenario import Ruta, RutScenario
from markmodell.byggnadsregister import ByggnadsPost, skriv_byggnader_gpkg
from markmodell.hinder_oppningar import brann_in_byggnader
from testsvit.k1_io import GeoTransformInfo, las_geotiff, skriv_geotiff

E0 = 150000.0
N0 = 6580000.0


def _gpkg_line_blob(koord: list[tuple[float, float]]) -> bytes:
    linje = LineString(koord)
    wkb = bytes(shapely.to_wkb(linje, byte_order=1))
    x0, y0, x1, y1 = linje.bounds
    return struct.pack("<2sBBi4d", b"GP", 0, 0x03, 3011, x0, x1, y0, y1) + wkb


def _skriv_brytlinjer_gpkg(
    fil: Path, poster: list[tuple[str, float, list[tuple[float, float]]]]
) -> None:
    if fil.exists():
        fil.unlink()
    conn = sqlite3.connect(str(fil))
    try:
        conn.execute("PRAGMA application_id = 1196444487;")
        conn.execute("PRAGMA user_version = 10300;")
        conn.executescript(
            """
            CREATE TABLE gpkg_geometry_columns (
                table_name TEXT NOT NULL,
                column_name TEXT NOT NULL,
                geometry_type_name TEXT NOT NULL,
                srs_id INTEGER NOT NULL,
                z TINYINT NOT NULL,
                m TINYINT NOT NULL
            );
            CREATE TABLE brytlinjer (
                fid INTEGER PRIMARY KEY AUTOINCREMENT,
                geom BLOB NOT NULL,
                typ TEXT NOT NULL,
                hojd_m REAL NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO gpkg_geometry_columns "
            "VALUES ('brytlinjer', 'geom', 'LINESTRING', 3011, 0, 0)"
        )
        for typ, hojd_m, koord in poster:
            conn.execute(
                "INSERT INTO brytlinjer (geom, typ, hojd_m) VALUES (?, ?, ?)",
                (_gpkg_line_blob(koord), typ, float(hojd_m)),
            )
        conn.commit()
    finally:
        conn.close()


def _skapa_k1_v2_ruta_med_murar(
    mapp: Path,
    *,
    byggnader: tuple[ByggnadsPost, ...],
    murar: list[tuple[str, float, list[tuple[float, float]]]],
    sida_m: float = 60.0,
    pixel_m: float = 5.0,
) -> Path:
    mapp.mkdir(parents=True, exist_ok=True)
    n = round(sida_m / pixel_m)
    x_min = E0
    y_max = N0 + sida_m
    terrang = np.full((n, n), 10.0, dtype=np.float32)
    markslag = np.full((n, n), 4, dtype=np.uint8)
    osak = np.full((n, n), 0.05, dtype=np.float32)
    geo = GeoTransformInfo(
        bredd=n,
        hojd=n,
        x_min=x_min,
        y_max=y_max,
        pixelstorlek_x=pixel_m,
        pixelstorlek_y=pixel_m,
        epsg=3011,
        dtype="float32",
    )
    if byggnader:
        mark, markslag = brann_in_byggnader(
            terrang, markslag, ruta_fran_geo(geo), [b.till_byggnad() for b in byggnader]
        )
        mark = np.asarray(mark, dtype=np.float32)
    else:
        mark = terrang.copy()

    skriv_geotiff(mapp / "terrang.tif", terrang, x_min=x_min, y_max=y_max, pixelstorlek=pixel_m)
    skriv_geotiff(mapp / "mark.tif", mark, x_min=x_min, y_max=y_max, pixelstorlek=pixel_m)
    skriv_geotiff(mapp / "markslag.tif", markslag, x_min=x_min, y_max=y_max, pixelstorlek=pixel_m)
    skriv_geotiff(mapp / "osakerhet.tif", osak, x_min=x_min, y_max=y_max, pixelstorlek=pixel_m)
    skriv_byggnader_gpkg(
        mapp / "byggnader.gpkg",
        byggnader,
        bbox_fallback=(x_min, N0, x_min + sida_m, y_max),
    )
    _skriv_brytlinjer_gpkg(mapp / "brytlinjer.gpkg", murar)
    (mapp / "recept.json").write_text(
        json.dumps({"kontrakt": "K1/v2", "ruta_id": "mm_haga_mur", "epsg": 3011}),
        encoding="utf-8",
    )
    return mapp


def test_overlappande_och_slutna_bryt_mur_polygoner_ger_noll_overlapp_och_bevarar_skarmning(
    tmp_path: Path,
) -> None:
    """Regressionstest för Haga `bryt_mur_*`-överlapp: korsande, parallella och slutna gårdsmurar
    intill/runt byggnader ska passera `Bullerscen` utan fel och ge fysisk skärmdämpning."""
    hus = ByggnadsPost(
        byggnad_id="haga_hus_1",
        polygon=(
            (E0 + 30.0, N0 + 22.0),
            (E0 + 42.0, N0 + 22.0),
            (E0 + 42.0, N0 + 38.0),
            (E0 + 30.0, N0 + 38.0),
        ),
        hal=(),
        takhojd_rh2000=22.0,
        markhojd_rh2000=10.0,
        hojd_m=12.0,
        kalla="test",
    )

    # Sammansatt Haga-liknande uppsättning murar i `brytlinjer.gpkg`:
    # 1. Sluten gårdsmur (ringpolygon med hål!) runt huset och en inre mur
    # 2. Två korsande murar (X-korsning) framför huset
    # 3. Parallell tät mur 5 cm bredvid huvudmuren (överlappande 20 cm-buffertar)
    # 4. Mur som nuddar/skär in i husets vägg
    murar: list[tuple[str, float, list[tuple[float, float]]]] = [
        (
            "mur",
            2.5,
            [
                (E0 + 24.0, N0 + 16.0),
                (E0 + 48.0, N0 + 16.0),
                (E0 + 48.0, N0 + 44.0),
                (E0 + 24.0, N0 + 44.0),
                (E0 + 24.0, N0 + 16.0),
            ],
        ),
        ("mur", 4.0, [(E0 + 14.0, N0 + 8.0), (E0 + 14.0, N0 + 52.0)]),
        ("stodmur", 2.0, [(E0 + 14.05, N0 + 12.0), (E0 + 14.05, N0 + 48.0)]),
        ("mur", 3.0, [(E0 + 10.0, N0 + 30.0), (E0 + 35.0, N0 + 30.0)]),
        ("mur", 2.0, [(E0 + 27.0, N0 + 20.0), (E0 + 27.0, N0 + 40.0)]),
    ]

    ruta_mapp = _skapa_k1_v2_ruta_med_murar(
        tmp_path / "k1_haga_murar",
        byggnader=(hus,),
        murar=murar,
    )
    ruta_obj = Ruta(
        ruta_id="mm_haga_mur",
        mapp=ruta_mapp,
        hash="a" * 64,
        x_min=E0,
        y_min=N0,
        x_max=E0 + 60.0,
        y_max=N0 + 60.0,
    )
    scen = RutScenario(id="haga-murar", nyckel="b" * 64, rutor=(ruta_obj,))

    losare = BullerNord2000()
    utmapp = tmp_path / "ut_murar"
    utmapp.mkdir()
    kontext = LosarKontext(
        utmapp=utmapp,
        scenario=scen,
        parametrar={
            "vagsegment": [
                {
                    "id": "sprangkullsgatan",
                    "koordinater": [[E0 + 4.0, N0 + 4.0], [E0 + 4.0, N0 + 56.0]],
                    "adt_fordon_dygn": 10000.0,
                    "hastighet_km_h": 50.0,
                    "andel_tung_pct": 10.0,
                }
            ],
            "mottagarhojd_m": 2.0,
            "raster_cell_m": 5.0,
            "segment_langd_m": 15.0,
            "fasad_steg_m": 10.0,
            "max_vaningar": 1,
            "reflexordning": 0,
            # Dessutom en explicit bullerskärm som korsar brytlinjemurarna!
            "bullerskarmar": [
                {
                    "id": "extra_skarm",
                    "koordinater": [[E0 + 12.0, N0 + 20.0], [E0 + 22.0, N0 + 20.0]],
                    "hojd_m": 3.5,
                }
            ],
        },
        indata={},
        beroenden={},
    )

    losare.kor(kontext)

    sam: dict[str, Any] = json.loads(
        (utmapp / "buller_sammanfattning.json").read_text(encoding="utf-8")
    )
    assert sam["laeq_max_dba"] is not None
    assert sam["antal_bullerskarmar"] >= 3
    # Hårda invarianter får aldrig rubbas.
    assert NORD2000_KONFORM is False
    assert RAPPORT_SPARRAD is True
    assert METODSTATUS == "skiss"
    assert sam["nord2000_konform"] is False
    assert sam["rapport_sparrad"] is True
    assert sam["metodstatus"] == "skiss"

    # Jämför ljudnivå direkt bakom 4 m-muren (E+20, N+32) med ljudnivå framför muren (E+7, N+32).
    arr, geo = las_geotiff(utmapp / "ekvivalent_ljudniva_dba.tif")
    def _sampla(e: float, n: float) -> float:
        k = int(np.floor((e - geo.x_min) / geo.pixelstorlek_x))
        r = int(np.floor((geo.y_max - n) / geo.pixelstorlek_y))
        return float(arr[r, k])

    framfor = _sampla(E0 + 7.5, N0 + 32.5)
    bakom = _sampla(E0 + 20.5, N0 + 32.5)
    assert np.isfinite(framfor) and np.isfinite(bakom)
    assert bakom < framfor - 8.0


def test_skarm_till_byggnader_hojdforetrade_och_noll_overlapp_i_bullerscen() -> None:
    """Direkttest: `_skarm_till_byggnader` + rensning ger höjd-företräde och 0 överlapp."""
    terrang = np.full((12, 12), 10.0, dtype=np.float64)
    markslag = np.full((12, 12), 4, dtype=np.uint8)
    skarmar = [
        _SkarmLinje(
            "bryt_mur_lag", ((E0 + 15.0, N0 + 10.0), (E0 + 15.0, N0 + 40.0)), 1.5, 0.2, 1.0
        ),
        _SkarmLinje(
            "bryt_mur_hog", ((E0 + 15.05, N0 + 10.0), (E0 + 15.05, N0 + 40.0)), 4.0, 0.2, 1.0
        ),
        _SkarmLinje(
            "bryt_mur_ring",
            (
                (E0 + 25.0, N0 + 15.0),
                (E0 + 40.0, N0 + 15.0),
                (E0 + 40.0, N0 + 35.0),
                (E0 + 25.0, N0 + 35.0),
                (E0 + 25.0, N0 + 15.0),
            ),
            2.5,
            0.2,
            1.0,
        ),
        _SkarmLinje(
            "bryt_mur_inre", ((E0 + 30.0, N0 + 20.0), (E0 + 35.0, N0 + 30.0)), 2.0, 0.2, 1.0
        ),
    ]

    hinder, _ = _skarm_till_byggnader(skarmar, terrang, E0, N0 + 60.0, 5.0, ())
    skarm_ids = frozenset(h.id for h in hinder)
    rensade, _ = _rensa_overlappande_byggnader(hinder, skarm_ids=skarm_ids)

    # Den höga muren (4 m -> tak 14 m) ska finnas kvar hel med företräde över den låga (1,5 m).
    hog = [h for h in rensade if h.id.startswith("bryt_mur_hog")]
    assert hog and hog[0].tak_rh2000_m == 14.0
    # Den inre muren inuti gårdsmuren får inte ha svalts av ett igenfyllt hål.
    assert any(h.id.startswith("bryt_mur_inre") for h in rensade)

    # Strikt `Bullerscen` accepterar samtliga hinder utan överlappsfel.
    scen = Bullerscen(
        terrang_rh2000_m=terrang,
        markslag=markslag,
        e_min_m=E0,
        n_max_m=N0 + 60.0,
        cell_m=5.0,
        byggnader=tuple(rensade),
    )
    assert len(scen.byggnader) == len(rensade)
