"""Offline end-to-end-test av 2×2-rututsnitt i Göteborg (Våg 2).

Verifierar hela kedjan utan nätverk, riktiga S3-nycklar eller CUDA:
1. Rutgenerering för ett 2×2-utsnitt i Göteborg (SWEREF 99 18 00, EPSG:3011, 500×500 m + överlapp).
2. Realistiska syntetiska K1-rutor med byggnader och 3D-kantstenar (`brytlinjer.gpkg` med Z).
3. `kor_batch_rutor` med `skapa_k1_rutkorare` (`vatten2d` + `dagsljus`) mot mockad `S3Lagring`.
4. ZSTD/DEFLATE-komprimerade COG-filer + giltiga K0-manifest (`run.json`) under
   `geografi/goteborg/<ruta>/`, volymfel < 1e-3 och omedelbar rensning av tempkataloger (lager 1).
5. Återupptagning: en andra körning hoppar över samtliga 4 rutor (0 omberäkningar).
6. S3 Lifecycle Policy mot mockad S3 (lager 2).
7. Nedladdning till lokal LRU-cache (`ladda_ner_fil`), läsning via `las_geotiff` samt
   `stada_lokal_cache` med både `--torrkor` och skarp LRU-eviction (lager 3).
"""

from __future__ import annotations

import json
import os
import sqlite3
import struct
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import rasterio

from gemensamt.cog import valj_cog_komprimering
from gemensamt.hash import sha256_fil
from gemensamt.lokal_cache import BYTES_PER_GB, stada_lokal_cache
from gemensamt.manifest import las_manifest
from gemensamt.objektlagring import S3Lagring, geografi_nyckel
from gemensamt.rensning import installera_livscykelregler
from karna.batch import (
    BatchKonfig,
    RutUtdata,
    generera_rutor_for_omrade,
    kor_batch_rutor,
    skapa_k1_rutkorare,
)
from markmodell.rutnat import Ruta
from testsvit.k1_io import (
    EPSG_SWEREF99_1800,
    WKT_EPSG_3011,
    WKT_EPSG_4326,
    las_geotiff,
    skriv_geotiff,
)

# Göteborg (Haga/Linné) ungefärligt utsnitt i SWEREF 99 18 00 (EPSG:3011):
# 1000×1000 m som täcker exakt 2×2 standardrutor (500×500 m).
_GBG_E0 = -210000.0
_GBG_N0 = 6402000.0


class _MockS3Fel(Exception):
    def __init__(self, code: str, status: int = 404) -> None:
        super().__init__(f"Mock S3 error: {code}")
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }


class _MockS3Klient:
    """In-memory S3-klient för offline E2E-verifiering utan boto3 eller nätverk."""

    def __init__(self) -> None:
        self.objekt: dict[tuple[str, str], tuple[bytes, dict[str, str]]] = {}
        self.livscykel: dict[str, dict[str, Any]] = {}

    def upload_file(
        self,
        Filename: str,
        Bucket: str,
        Key: str,
        ExtraArgs: dict[str, Any] | None = None,
    ) -> None:
        data = Path(Filename).read_bytes()
        meta = dict((ExtraArgs or {}).get("Metadata", {}))
        self.objekt[(Bucket, Key)] = (data, meta)

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        if (Bucket, Key) not in self.objekt:
            raise _MockS3Fel("404", 404)
        data, meta = self.objekt[(Bucket, Key)]
        return {"ContentLength": len(data), "Metadata": dict(meta)}

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:
        if (Bucket, Key) not in self.objekt:
            raise _MockS3Fel("NoSuchKey", 404)
        data, _ = self.objekt[(Bucket, Key)]
        Path(Filename).write_bytes(data)

    def list_objects_v2(
        self,
        Bucket: str,
        Prefix: str = "",
        ContinuationToken: str | None = None,
    ) -> dict[str, Any]:
        nycklar = sorted(k for (b, k) in self.objekt if b == Bucket and k.startswith(Prefix))
        start = int(ContinuationToken) if ContinuationToken is not None else 0
        sida = nycklar[start : start + 50]
        nasta = start + len(sida)
        svar: dict[str, Any] = {
            "Contents": [{"Key": k} for k in sida],
            "IsTruncated": nasta < len(nycklar),
        }
        if nasta < len(nycklar):
            svar["NextContinuationToken"] = str(nasta)
        return svar

    def delete_objects(self, Bucket: str, Delete: dict[str, Any]) -> dict[str, Any]:
        raderade: list[dict[str, str]] = []
        for post in Delete.get("Objects", []):
            k = str(post["Key"])
            if (Bucket, k) in self.objekt:
                del self.objekt[(Bucket, k)]
                raderade.append({"Key": k})
        return {"Deleted": raderade}

    def generate_presigned_url(
        self,
        ClientMethod: str,
        Params: dict[str, Any],
        ExpiresIn: int,
    ) -> str:
        return (
            f"https://fsn1.your-objectstorage.com/{Params['Bucket']}/{Params['Key']}"
            f"?X-Amz-Expires={ExpiresIn}"
        )

    def put_bucket_lifecycle_configuration(
        self,
        Bucket: str,
        LifecycleConfiguration: dict[str, Any],
    ) -> dict[str, Any]:
        self.livscykel[Bucket] = LifecycleConfiguration
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


def _skriv_goteborg_2x2_geojson(sokvag: Path) -> Path:
    """Skriver en 1000×1000 m polygon i SWEREF 99 18 00 (EPSG:3011) över Göteborg (2×2 rutor)."""
    poly = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"namn": "goteborg_haga_2x2"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [_GBG_E0 + 10.0, _GBG_N0 + 10.0],
                            [_GBG_E0 + 990.0, _GBG_N0 + 10.0],
                            [_GBG_E0 + 990.0, _GBG_N0 + 990.0],
                            [_GBG_E0 + 10.0, _GBG_N0 + 990.0],
                            [_GBG_E0 + 10.0, _GBG_N0 + 10.0],
                        ]
                    ],
                },
            }
        ],
    }
    sokvag.write_text(json.dumps(poly), encoding="utf-8")
    return sokvag


def _packa_gp_wkb_linestring_z(
    punkter_xyz: Sequence[tuple[float, float, float]],
    srs_id: int = EPSG_SWEREF99_1800,
) -> bytes:
    """Packar en 3D LineStringZ (ISO WKB 1002) med GeoPackage Binary Header."""
    gp_header = struct.pack("<2sBBi", b"GP", 0, 1, srs_id)
    buf = bytearray(gp_header)
    buf.extend(struct.pack("<BII", 1, 1002, len(punkter_xyz)))
    for x, y, z in punkter_xyz:
        buf.extend(struct.pack("<ddd", float(x), float(y), float(z)))
    return bytes(buf)


def _skriv_brytlinjer_3d_gpkg(
    sokvag: Path,
    kantstenar: Sequence[tuple[str, float, Sequence[tuple[float, float, float]], float]],
) -> Path:
    """Skriver `brytlinjer.gpkg` med 3D-kantstenar (LineStringZ + 3D-kolumner enligt K1)."""
    sokvag.parent.mkdir(parents=True, exist_ok=True)
    if sokvag.exists():
        sokvag.unlink()

    xs = [x for _, _, pts, _ in kantstenar for x, _, _ in pts]
    ys = [y for _, _, pts, _ in kantstenar for _, y, _ in pts]
    min_x, min_y, max_x, max_y = min(xs), min(ys), max(xs), max(ys)

    conn = sqlite3.connect(str(sokvag))
    try:
        conn.execute("PRAGMA application_id = 1196444487")
        conn.execute("PRAGMA user_version = 10200")
        conn.execute(
            """
            CREATE TABLE gpkg_spatial_ref_sys (
                srs_name TEXT NOT NULL,
                srs_id INTEGER NOT NULL PRIMARY KEY,
                organization TEXT NOT NULL,
                organization_coordsys_id INTEGER NOT NULL,
                definition TEXT NOT NULL,
                description TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE gpkg_contents (
                table_name TEXT NOT NULL PRIMARY KEY,
                data_type TEXT NOT NULL,
                identifier TEXT UNIQUE,
                description TEXT DEFAULT '',
                last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                min_x DOUBLE,
                min_y DOUBLE,
                max_x DOUBLE,
                max_y DOUBLE,
                srs_id INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE gpkg_geometry_columns (
                table_name TEXT NOT NULL,
                column_name TEXT NOT NULL,
                geometry_type_name TEXT NOT NULL,
                srs_id INTEGER NOT NULL,
                z TINYINT NOT NULL,
                m TINYINT NOT NULL,
                CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name)
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO gpkg_spatial_ref_sys (
                srs_name, srs_id, organization, organization_coordsys_id, definition, description
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("Undefined cartesian SRS", -1, "NONE", -1, "undefined", "Undefined"),
                ("Undefined geographic SRS", 0, "NONE", 0, "undefined", "Undefined"),
                ("WGS 84 geodetic", 4326, "EPSG", 4326, WKT_EPSG_4326, "WGS 84"),
                (
                    "SWEREF99 18 00",
                    EPSG_SWEREF99_1800,
                    "EPSG",
                    EPSG_SWEREF99_1800,
                    WKT_EPSG_3011,
                    "SWEREF 99 18 00",
                ),
            ],
        )
        conn.execute(
            """
            CREATE TABLE brytlinjer (
                fid INTEGER PRIMARY KEY AUTOINCREMENT,
                geom BLOB,
                typ TEXT NOT NULL,
                hojd_m REAL NOT NULL,
                tvarfall REAL,
                kalla TEXT,
                osakerhet_m REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO gpkg_contents (
                table_name, data_type, identifier, description,
                last_change, min_x, min_y, max_x, max_y, srs_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "brytlinjer",
                "features",
                "brytlinjer",
                "3D-kantstenar enligt Floden K1",
                "2026-11-25T00:00:00.000Z",
                min_x,
                min_y,
                max_x,
                max_y,
                EPSG_SWEREF99_1800,
            ),
        )
        conn.execute(
            """
            INSERT INTO gpkg_geometry_columns (
                table_name, column_name, geometry_type_name, srs_id, z, m
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("brytlinjer", "geom", "LINESTRING", EPSG_SWEREF99_1800, 1, 0),
        )
        for typ, hojd_m, punkter_xyz, tvarfall in kantstenar:
            wkb = _packa_gp_wkb_linestring_z(punkter_xyz, EPSG_SWEREF99_1800)
            conn.execute(
                """
                INSERT INTO brytlinjer (geom, typ, hojd_m, tvarfall, kalla, osakerhet_m)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    sqlite3.Binary(wkb),
                    typ,
                    float(hojd_m),
                    float(tvarfall),
                    "goteborg_baskarta_syntetisk",
                    0.03,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return sokvag


def _bygg_syntetisk_k1_ruta(k1_rot: Path, ruta: Ruta, idx: int) -> Path:
    """Bygger en realistisk syntetisk K1-ruta med lågpunkt, byggnadskvarter och 3D-kantsten."""
    rader, kolumner = ruta.form_rc
    e_min, _, _, n_max = ruta.yttre_bbox
    dx = ruta.upplosning_m

    yy, xx = np.mgrid[0:rader, 0:kolumner]
    x_rel = (xx - kolumner / 2.0) * dx
    y_rel = (yy - rader / 2.0) * dx

    # Svag dalgång + central lågpunkt (sänka) + unik topografisk lutning per ruta
    bas_z = 12.0 + 0.4 * idx
    mark_z = (
        bas_z
        + 0.002 * x_rel
        + 0.0015 * y_rel
        + 0.000015 * (x_rel**2 + y_rel**2)
        - 0.35 * np.exp(-((x_rel + 40.0) ** 2 + (y_rel - 20.0) ** 2) / (2.0 * 65.0**2))
    ).astype(np.float32)

    # Markslag: grönyta (4), asfalterad gata genom rutan (1), byggnadskvarter (9)
    markslag = np.full((rader, kolumner), 4, dtype=np.uint8)
    gata_r0 = rader // 2 - max(1, int(round(10.0 / dx)))
    gata_r1 = rader // 2 + max(1, int(round(10.0 / dx)))
    markslag[gata_r0:gata_r1, :] = 1

    # Två huskroppar (14 m och 18 m höga) för realistisk 2D-avrinning och dagsljus-SVF < 1
    b1_r0, b1_r1 = int(rader * 0.18), int(rader * 0.34)
    b1_c0, b1_c1 = int(kolumner * 0.18), int(kolumner * 0.36)
    b2_r0, b2_r1 = int(rader * 0.64), int(rader * 0.80)
    b2_c0, b2_c1 = int(kolumner * 0.58), int(kolumner * 0.78)
    markslag[b1_r0:b1_r1, b1_c0:b1_c1] = 9
    markslag[b2_r0:b2_r1, b2_c0:b2_c1] = 9
    mark_z[b1_r0:b1_r1, b1_c0:b1_c1] += 14.0
    mark_z[b2_r0:b2_r1, b2_c0:b2_c1] += 18.0

    osakerhet = np.full((rader, kolumner), 0.05, dtype=np.float32)

    ruta_mapp = k1_rot / ruta.namn
    skriv_geotiff(ruta_mapp / "mark.tif", mark_z, x_min=e_min, y_max=n_max, pixelstorlek=dx)
    skriv_geotiff(
        ruta_mapp / "osakerhet.tif", osakerhet, x_min=e_min, y_max=n_max, pixelstorlek=dx
    )
    skriv_geotiff(
        ruta_mapp / "markslag.tif", markslag, x_min=e_min, y_max=n_max, pixelstorlek=dx
    )

    # 3D-kantstenar längs gatans norra och södra kant i rutan (LineStringZ)
    e0_inre, n0_inre, e1_inre, n1_inre = ruta.inre_bbox
    n_mitt = 0.5 * (n0_inre + n1_inre)
    kantstenar = [
        (
            "kantsten",
            0.14,
            [
                (e0_inre + 40.0, n_mitt + 10.0, bas_z + 0.14),
                (e0_inre + 250.0, n_mitt + 10.0, bas_z + 0.12),
                (e1_inre - 40.0, n_mitt + 10.0, bas_z + 0.10),
            ],
            0.025,
        ),
        (
            "kantsten",
            0.14,
            [
                (e0_inre + 40.0, n_mitt - 10.0, bas_z + 0.14),
                (e0_inre + 250.0, n_mitt - 10.0, bas_z + 0.12),
                (e1_inre - 40.0, n_mitt - 10.0, bas_z + 0.10),
            ],
            0.025,
        ),
    ]
    _skriv_brytlinjer_3d_gpkg(ruta_mapp / "brytlinjer.gpkg", kantstenar)
    return ruta_mapp


def test_goteborg_2x2_batch_s3_och_tre_rensningslager_e2e(tmp_path: Path) -> None:
    """Komplett offline E2E-test för 2×2-rutor i Göteborg: batch, S3, COG, K0 och lager 1–3."""
    omrade_fil = _skriv_goteborg_2x2_geojson(tmp_path / "goteborg_2x2.geojson")
    rutor = generera_rutor_for_omrade(
        omrade_fil,
        rutstorlek_m=500.0,
        overlapp_m=25.0,
        upplosning_m=5.0,
    )
    assert len(rutor) == 4
    assert [r.namn for r in rutor] == [
        "mm_-209500_6402000",
        "mm_-209500_6402500",
        "mm_-210000_6402000",
        "mm_-210000_6402500",
    ]

    # Skapa syntetiska K1-rutor med byggnader och 3D-kantstenar
    k1_rot = tmp_path / "k1_goteborg"
    for idx, ruta in enumerate(rutor):
        _bygg_syntetisk_k1_ruta(k1_rot, ruta, idx)

    # Mockad S3Lagring (helt offline, inga riktiga nycklar)
    mock_klient = _MockS3Klient()
    lagring = S3Lagring(
        endpoint_url="https://fsn1.your-objectstorage.com",
        bucket="floden-oppen-eu",
        access_key="test-ak-offline",
        secret_key="test-sk-offline",
        region="fsn1",
        klient=mock_klient,
    )

    # Lager 2: installera och verifiera S3 Lifecycle Policy på mål-bucketen
    installerade_regler = installera_livscykelregler(
        lagring,
        radata_dagar=2,
        skiss_dagar=14,
        multipart_dagar=1,
    )
    assert "floden-oppen-eu" in mock_klient.livscykel
    assert mock_klient.livscykel["floden-oppen-eu"] == installerade_regler
    regel_idn = {r["ID"]: r for r in installerade_regler["Rules"]}
    assert set(regel_idn) == {
        "floden-tmp-radata-ttl",
        "floden-tmp-skiss-ttl",
        "floden-abort-incomplete-multipart",
    }
    assert regel_idn["floden-tmp-radata-ttl"]["Filter"] == {"Prefix": "tmp/radata/"}
    assert regel_idn["floden-tmp-skiss-ttl"]["Filter"] == {"Prefix": "tmp/skiss/"}

    # Riktig K1-rutkörare (`vatten2d` + `dagsljus`) insvept för att verifiera lager 1-rensning
    bas_korare = skapa_k1_rutkorare(
        k1_rot,
        regn_tid_s=(0.0, 300.0),
        regn_mm_h=(60.0, 60.0),
        slut_tid_s=300.0,
        azimutriktningar=18,
        dagsljus_radie_m=100.0,
    )
    observerade_tmpmappar: list[Path] = []
    anropade_rutor: list[str] = []

    def spion_korare(
        ruta: Ruta, arbetsmapp: Path, konfig: BatchKonfig, plats: str
    ) -> RutUtdata:
        # Lager 1: föregående rutas temporära arbetskatalog ska redan vara raderad
        for tidigare in observerade_tmpmappar:
            assert not tidigare.exists(), f"Lager 1 brast: {tidigare} fanns kvar vid nästa ruta"
        observerade_tmpmappar.append(arbetsmapp)
        anropade_rutor.append(ruta.namn)
        # Simulera temporär rå-LAZ i arbetskatalogen (får varken laddas upp eller bli kvar)
        (arbetsmapp / "radata.laz").write_bytes(b"SIMULERAD-RA-LAZ" * 64)
        return bas_korare(ruta, arbetsmapp, konfig, plats)

    utdata_rot = tmp_path / "batch_status"
    konfig = BatchKonfig(
        moduler=("vatten2d", "dagsljus"),
        utdata_rot=utdata_rot,
        enhet="cpu",
        precision="f64",
        dataklass="oppen",
        rutstorlek_m=500.0,
        overlapp_m=25.0,
        upplosning_m=5.0,
        kommun="goteborg",
        lagring_typ="s3",
        behall_radata=False,
        indata_filer=(omrade_fil,),
    )

    # Pass 1: kör alla 4 rutor
    status1 = kor_batch_rutor(
        rutor,
        konfig,
        rutkorare=spion_korare,
        objektlagring=lagring,
        avbryt_vid_fel=True,
    )
    assert len(status1.klara) == 4
    assert len(status1.hoppade) == 0
    assert len(status1.felade) == 0
    assert status1.aterstaende == 0
    assert anropade_rutor == [r.namn for r in rutor]

    # Lager 1: samtliga 4 temporära arbetskataloger raderades direkt efter respektive ruta
    assert len(observerade_tmpmappar) == 4
    assert all(not mapp.exists() for mapp in observerade_tmpmappar)

    # Verifiera S3-nycklar, COG-komprimering, K0-manifest och volymbalans för alla 4 rutor
    forvantad_komp = valj_cog_komprimering()
    assert forvantad_komp in ("ZSTD", "DEFLATE")

    lokal_cache = tmp_path / "s3_lru_cache"
    nedladdade_coger: list[Path] = []
    for ruta in rutor:
        # Temporär rå-LAZ laddas aldrig upp till S3
        assert not lagring.finns(geografi_nyckel("goteborg", ruta.namn, "radata.laz"))

        run_nyckel = geografi_nyckel("goteborg", ruta.namn, "run.json")
        assert lagring.finns(run_nyckel)

        lokal_run = lokal_cache / ruta.namn / "run.json"
        lagring.ladda_ner_fil(run_nyckel, lokal_run)
        manifest = las_manifest(lokal_run)

        assert manifest.kontrakt == "K0/v1"
        assert manifest.modul == "forberakna"
        assert manifest.parametrar["kommun"] == "goteborg"
        assert manifest.parametrar["ruta_id"] == ruta.namn
        assert manifest.parametrar["berakningsplats"] == "cpu_f64"
        assert manifest.matvarden["vatten2d_volymfel_rel"] < 1e-3
        assert manifest.matvarden["vatten2d_max_djup_m"] > 0.0
        svf_min = manifest.matvarden["dagsljus_svf_min"]
        svf_medel = manifest.matvarden["dagsljus_svf_medel"]
        assert 0.0 < svf_min < svf_medel < 1.0

        # Alla utdatafiler i K0-manifestet finns i S3 och har matchande SHA-256
        ut_roller = {p.roll: p for p in manifest.utdata}
        assert "vatten2d_max_djup" in ut_roller
        assert "dagsljus_svf" in ut_roller

        for roll in ("vatten2d_max_djup", "dagsljus_svf"):
            post = ut_roller[roll]
            s3_nyckel = geografi_nyckel("goteborg", ruta.namn, post.fil)
            assert lagring.finns(s3_nyckel)

            lokal_fil = lokal_cache / ruta.namn / post.fil
            lagring.ladda_ner_fil(s3_nyckel, lokal_fil)
            assert sha256_fil(lokal_fil) == post.sha256
            nedladdade_coger.append(lokal_fil)

            # Verifiera COG-komprimering (ZSTD/DEFLATE), prediktor och tiling
            with rasterio.open(lokal_fil) as ds:
                assert str(ds.profile["compress"]).upper() == forvantad_komp
                assert ds.profile["tiled"] is True
                assert ds.tags(ns="IMAGE_STRUCTURE").get("PREDICTOR") == "3"
                assert ds.crs is not None
                assert ds.crs.to_epsg() == EPSG_SWEREF99_1800

            # Verifiera läsning via Flodens `las_geotiff`
            arr, geo = las_geotiff(lokal_fil)
            assert arr.shape == ruta.form_rc
            assert geo.epsg == EPSG_SWEREF99_1800
            assert np.all(np.isfinite(arr))
            if roll == "vatten2d_max_djup":
                assert float(np.min(arr)) >= 0.0
                assert float(np.max(arr)) > 0.0
            else:
                assert 0.0 <= float(np.min(arr)) < float(np.max(arr)) <= 1.0

    # Återupptagning: en andra körning hoppar över samtliga 4 rutor (0 omberäkningar)
    anropade_rutor.clear()
    status2 = kor_batch_rutor(
        rutor,
        konfig,
        rutkorare=spion_korare,
        objektlagring=lagring,
        avbryt_vid_fel=True,
    )
    assert len(status2.hoppade) == 4
    assert len(status2.klara) == 0
    assert len(status2.felade) == 0
    assert anropade_rutor == []

    # Lager 3: testa lokal LRU-cache-städning (`--torrkor` och skarp LRU-eviction)
    # Sätt tydlig åldersordning på alla filer i cachen (ruta 0 äldst, ruta 3 nyast)
    nu_ts = 1_800_000_000.0
    alla_cachefiler = sorted(p for p in lokal_cache.rglob("*") if p.is_file())
    assert len(alla_cachefiler) == 12  # 4 rutor × (run.json + max_djup.tif + svf.tif)
    for i, fil in enumerate(alla_cachefiler):
        ts = nu_ts - (len(alla_cachefiler) - i) * 3600.0
        os.utime(fil, (ts, ts))

    total_bytes = sum(f.stat().st_size for f in alla_cachefiler)
    # Sätt max_gb till hälften av total storlek så LRU-eviction måste rensa äldsta filerna
    max_gb_liten = (total_bytes * 0.5) / BYTES_PER_GB

    torr = stada_lokal_cache(
        lokal_cache,
        max_gb=max_gb_liten,
        max_alder_dagar=14.0,
        torrkor=True,
        nu_ts=nu_ts,
    )
    assert torr.torrkor is True
    assert torr.antal_raderade_alder == 0
    assert torr.antal_raderade_lru > 0
    assert torr.frigjorda_bytes > 0
    # Torrkörning får inte radera någon fil på disk
    assert all(f.is_file() for f in alla_cachefiler)

    skarp = stada_lokal_cache(
        lokal_cache,
        max_gb=max_gb_liten,
        max_alder_dagar=14.0,
        torrkor=False,
        nu_ts=nu_ts,
    )
    assert skarp.torrkor is False
    assert skarp.antal_raderade == torr.antal_raderade
    assert skarp.antal_raderade_lru == torr.antal_raderade_lru
    assert skarp.kvarvarande_bytes <= int(0.85 * max_gb_liten * BYTES_PER_GB)
    # Äldsta filen ska vara borta, nyaste COG-filen ska vara kvar och läsbar
    assert not alla_cachefiler[0].exists()
    assert alla_cachefiler[-1].is_file()
    sista_cog = nedladdade_coger[-1]
    assert sista_cog.is_file()
    arr_efter, _ = las_geotiff(sista_cog)
    assert arr_efter.shape == rutor[-1].form_rc
