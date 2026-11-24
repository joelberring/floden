"""Hämtning med manifest, idempotens, återupptagning, omdirigeringar och byggnadsklippning."""

# ruff: noqa: E501
from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import pytest
import shapely

from data_inlasning.cache import DataInlasningFel
from lantmateriet.hamta import MANIFEST_NAMN, TILLSTAND_NAMN, hamta_omrade, las_manifest_lm
from lantmateriet.klient import LantmaterietFel, LmKlient
from lantmateriet.omrade import las_omrade, rutor_for_omrade
from lantmateriet.stac import wgs84_bbox
from tests.lantmateriet.conftest import Klocka, sha
from tests.lantmateriet.falsk_server import HEMLIGHET, KLIENT_ID, FalskLm

E0, N0 = 151000.0, 6580000.0


def skriv_omrade(mapp: Path, storlek: float = 200.0, epsg: int = 3011) -> Path:
    pol = shapely.box(E0, N0, E0 + storlek, N0 + storlek)
    gdf = gpd.GeoDataFrame({"namn": ["prov1"]}, geometry=[pol], crs="EPSG:3011")
    if epsg != 3011:
        gdf = gdf.to_crs(epsg=epsg)
    mapp.mkdir(parents=True, exist_ok=True)
    p = mapp / "prov1.gpkg"
    gdf.to_file(p, driver="GPKG")
    return p


def _feat(
    lm: FalskLm,
    fid: str,
    bbox: tuple[float, ...],
    dt: str,
    namn: str,
    typ: str,
    samling: str = "laserdata-skog",
) -> dict[str, Any]:
    return {
        "type": "Feature",
        "id": fid,
        "collection": samling,
        "bbox": list(bbox),
        "properties": {"datetime": dt, "proj:epsg": 3006},
        "assets": {"data": {"href": f"{lm.bas}/files/{namn}", "type": typ}},
    }


def fyll_laser(lm: FalskLm, bb: tuple[float, ...]) -> dict[str, bytes]:
    """Två skanningar av samma ruta (2019, 2023) och en ruta långt bort; nyaste ska väljas."""
    innehall = {
        "ny.laz": b"LASF-ny-" + bytes(range(256)) * 40,
        "gammal.laz": b"LASF-gammal-" + bytes(range(256)) * 10,
    }
    for namn, b in innehall.items():
        lm.filer[f"/files/{namn}"] = b
    lm.sidor = [
        [_feat(lm, "gammal", bb, "2019-05-01T00:00:00Z", "gammal.laz", "application/vnd.laszip")],
        [_feat(lm, "ny", bb, "2023-05-01T00:00:00Z", "ny.laz", "application/vnd.laszip")],
    ]
    return innehall


def test_hamta_laser_manifest_idempotens(lm: FalskLm, klient: LmKlient, tmp_path: Path) -> None:
    om = las_omrade(skriv_omrade(tmp_path / "in"))
    bb = wgs84_bbox(om.polygon.buffer(500))
    innehall = fyll_laser(lm, bb)
    ut = tmp_path / "data"
    skrivet: list[str] = []
    r1 = hamta_omrade(klient, om, ut, laser=True, skriv=skrivet.append)
    assert (r1.hamtade, r1.hoppade) == (1, 0)
    assert [f.item_id for f in r1.filer] == ["ny"]  # nyaste skanningen per ruta
    f = r1.filer[0]
    assert f.sha256 == sha(innehall["ny.laz"]) and f.storlek == len(innehall["ny.laz"])
    assert (ut / "laser" / "ny.laz").read_bytes() == innehall["ny.laz"]
    assert not list(ut.rglob("*.part"))

    man = las_manifest_lm(ut)
    assert man["kontrakt"] == "LM-hamtning/v1" and man["attribution"] == "©Lantmäteriet"
    assert "O-Tid (MIT, joelberring/otid @ 4a446af)" in man["protokoll"]
    assert man["samlingar"] == {"laser": "laserdata-skog"}
    assert man["omrade"]["sha256"] == om.sha256 and man["filer"][0]["sha256"] == f.sha256
    assert "CC0" in man["villkor"]["laser"]
    assert man["filer"][0]["url"] == f"{lm.bas}/files/ny.laz"
    manifest_bytes = (ut / MANIFEST_NAMN).read_bytes()
    assert sha(manifest_bytes) == r1.manifest_sha256

    # Idempotent: ingen ny nedladdning, manifestet är byte för byte detsamma.
    hamtningar = lm.antal("GET", "/files/")
    r2 = hamta_omrade(klient, om, ut, laser=True, skriv=skrivet.append)
    assert (r2.hamtade, r2.hoppade) == (0, 1)
    assert lm.antal("GET", "/files/") == hamtningar
    assert (ut / MANIFEST_NAMN).read_bytes() == manifest_bytes
    assert r2.manifest_sha256 == r1.manifest_sha256

    # En ändrad fil upptäcks (samma storlek, annat innehåll): hämtas om.
    sökväg = ut / "laser" / "ny.laz"
    sökväg.write_bytes(b"X" * len(innehall["ny.laz"]))
    r3 = hamta_omrade(klient, om, ut, laser=True, skriv=skrivet.append)
    assert r3.hamtade == 1 and sökväg.read_bytes() == innehall["ny.laz"]
    assert r3.manifest_sha256 == r1.manifest_sha256


def test_aterupptagning_med_range(lm: FalskLm, klient: LmKlient, tmp_path: Path) -> None:
    om = las_omrade(skriv_omrade(tmp_path / "in"))
    innehall = fyll_laser(lm, wgs84_bbox(om.polygon.buffer(500)))
    ut = tmp_path / "data"
    (ut / "laser").mkdir(parents=True)
    delvis = innehall["ny.laz"][:5000]
    (ut / "laser" / "ny.laz.part").write_bytes(delvis)
    r = hamta_omrade(klient, om, ut, laser=True, skriv=lambda _: None)
    assert r.filer[0].sha256 == sha(innehall["ny.laz"])
    fil_anrop = [b for b in lm.logg.begaranden if b["path"].startswith("/files/")]
    assert fil_anrop[0]["range"] == "bytes=5000-"
    assert not (ut / "laser" / "ny.laz.part").exists()


def test_429_pa_nedladdning_vantas_ut(
    lm: FalskLm, klient: LmKlient, klocka: Klocka, tmp_path: Path
) -> None:
    om = las_omrade(skriv_omrade(tmp_path / "in"))
    innehall = fyll_laser(lm, wgs84_bbox(om.polygon.buffer(500)))
    lm.fil_status_foerst = [429]
    r = hamta_omrade(klient, om, tmp_path / "d", laser=True, skriv=lambda _: None)
    assert r.filer[0].sha256 == sha(innehall["ny.laz"])
    assert klocka.somn == [0.0]  # Retry-After: 0


def test_omdirigering_inom_lantmateriet_behaller_nycklar_men_inte_till_annan_adress(
    lm: FalskLm, klient: LmKlient, tmp_path: Path
) -> None:
    lm.filer["/files/riktig.bin"] = b"data" * 100
    lm.omdirigera["/files/start.bin"] = "/files/riktig.bin"  # samma värd: tillåtet
    h, n = klient.hamta_fil(f"{lm.bas}/files/start.bin", tmp_path / "a.bin")
    assert n == 400 and h == sha(b"data" * 100)
    assert all(b["auth"] for b in lm.logg.begaranden if b["path"].startswith("/files/"))
    # Till en annan värd utan https: nekas, och inget anrop görs dit.
    lm.omdirigera["/files/ut.bin"] = f"http://localhost:{lm.port}/lagring/x.bin"
    with pytest.raises(LantmaterietFel, match="https"):
        klient.hamta_fil(f"{lm.bas}/files/ut.bin", tmp_path / "b.bin")
    assert lm.antal("GET", "/lagring/") == 0
    # Adresser som inte är Lantmäteriets får aldrig nycklar.
    with pytest.raises(LantmaterietFel, match="inte Lantmäteriets"):
        klient.hamta_fil("https://example.com/x.laz", tmp_path / "c.bin")


def test_inga_nycklar_i_filer_eller_loggar(
    lm: FalskLm, klient: LmKlient, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    om = las_omrade(skriv_omrade(tmp_path / "in"))
    fyll_laser(lm, wgs84_bbox(om.polygon.buffer(500)))
    ut = tmp_path / "data"
    utskrift: list[str] = []
    hamta_omrade(klient, om, ut, laser=True, skriv=utskrift.append)
    hemligheter = [KLIENT_ID, HEMLIGHET, "tok-abc123", "Bearer", "Basic "]
    alla = "\n".join(utskrift) + caplog.text
    for p in ut.rglob("*"):
        if p.is_file() and p.suffix in (".json", ".part", ""):
            alla += p.read_text(encoding="utf-8", errors="ignore") if p.stat().st_size < 1e6 else ""
    for h in hemligheter:
        assert h not in alla, h
    assert (ut / TILLSTAND_NAMN).is_file()


def test_inga_traffar_ger_tydligt_fel(lm: FalskLm, klient: LmKlient, tmp_path: Path) -> None:
    om = las_omrade(skriv_omrade(tmp_path / "in"))
    lm.sidor = [[]]
    with pytest.raises(LantmaterietFel, match="Inga laser-filer"):
        hamta_omrade(klient, om, tmp_path / "d", laser=True, skriv=lambda _: None)


def test_ingen_produkt_vald(klient: LmKlient, tmp_path: Path) -> None:
    om = las_omrade(skriv_omrade(tmp_path / "in"))
    with pytest.raises(DataInlasningFel, match="minst en produkt"):
        hamta_omrade(klient, om, tmp_path / "d", laser=False, skriv=lambda _: None)


# -- ortofoto och byggnader ---------------------------------------------------------------------


def _tiff_bytes() -> bytes:
    import numpy as np
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.transform import from_origin

    arr = np.full((3, 20, 20), 100, dtype="uint8")
    with MemoryFile() as mf:
        with mf.open(
            driver="GTiff",
            width=20,
            height=20,
            count=3,
            dtype="uint8",
            crs="EPSG:3006",
            transform=from_origin(670000.0, 6582000.0, 5.0, 5.0),
        ) as dst:
            dst.write(arr)
        _ = rasterio
        return bytes(mf.read())


def test_orto_valjer_nyaste_som_tacker(lm: FalskLm, klient: LmKlient, tmp_path: Path) -> None:
    om = las_omrade(skriv_omrade(tmp_path / "in"))
    bb = wgs84_bbox(om.polygon.buffer(500))
    tif = _tiff_bytes()
    lm.filer["/files/o2025.tif"] = tif
    lm.filer["/files/o2020.tif"] = tif + b"0"
    lm.bild_features = [
        _feat(lm, "o2025", bb, "2025-06-01T00:00:00Z", "o2025.tif", "image/tiff", "ortofoto"),
        _feat(lm, "o2020", bb, "2020-06-01T00:00:00Z", "o2020.tif", "image/tiff", "ortofoto"),
    ]
    r = hamta_omrade(klient, om, tmp_path / "d", laser=False, orto=True, skriv=lambda _: None)
    assert [f.item_id for f in r.filer] == ["o2025"]  # 2025 täcker allt: 2020 behövs inte
    assert r.filer[0].roll == "orto" and r.filer[0].fil == "orto/o2025.tif"


def zip_med_gpkg(tmp: Path, epsg: int = 3006) -> bytes:
    """Byggnadsprodukten som en zip med en GeoPackage: två hus i området, ett långt bort."""
    from pyproj import Transformer

    t = Transformer.from_crs(3011, epsg, always_xy=True)

    def hus(e: float, n: float, b: float = 10.0) -> shapely.Polygon:
        pts = [(e, n), (e + b, n), (e + b, n + b), (e, n + b)]
        return shapely.Polygon([t.transform(x, y) for x, y in pts])

    gdf = gpd.GeoDataFrame(
        {
            "objektidentitet": ["h1", "h2", "borta"],
            "vaningar": [4, 3, 2],
            "hojd_m": [12.0, 9.5, 6.0],
            # Påhittade attribut som inte får sparas (dataminimering):
            "adress": ["Provgatan 1", "Provgatan 2", "Provgatan 3"],
            "fastighetsbeteckning": ["Prov 1:1", "Prov 1:2", "Prov 1:3"],
        },
        geometry=[hus(E0 + 20, N0 + 20), hus(E0 + 120, N0 + 60), hus(E0 + 5000, N0 + 5000)],
        crs=f"EPSG:{epsg}",
    )
    g = tmp / "kommun.gpkg"
    gdf.to_file(g, layer="byggnad_yta", driver="GPKG")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.write(g, "kommun/byggnad_kn0180.gpkg")
    return buf.getvalue()


def test_byggnader_zip_klipps_till_omradet(lm: FalskLm, klient: LmKlient, tmp_path: Path) -> None:
    om = las_omrade(skriv_omrade(tmp_path / "in"))
    bb = wgs84_bbox(om.polygon.buffer(500))
    lm.filer["/files/byggnad_kn0180.zip"] = zip_med_gpkg(tmp_path)
    lm.vektor_features = [
        _feat(
            lm,
            "kn0180",
            bb,
            "2025-01-01T00:00:00Z",
            "byggnad_kn0180.zip",
            "application/zip",
            "byggnad",
        )
    ]
    ut = tmp_path / "data"
    r = hamta_omrade(klient, om, ut, laser=False, byggnader=True, skriv=lambda _: None)
    assert r.byggnader_klippt is not None
    gdf = gpd.read_file(r.byggnader_klippt)
    assert len(gdf) == 2  # "borta" klipptes bort
    # Dataminimering: bara geometri, höjd och våningar (plus ett löpnummer), inga adresser eller id:n.
    assert sorted(gdf.columns) == ["byggnad_id", "geometry", "hojd_m", "vaningar"]
    assert sorted(gdf["vaningar"]) == [3, 4]
    assert sorted(gdf["byggnad_id"]) == ["lm000001", "lm000002"]
    assert not (ut / "byggnader" / "byggnad_kn0180").exists()  # uppackade originalet städas bort
    assert "Provgatan" not in r.byggnader_klippt.read_bytes().decode("latin-1")
    assert "Prov 1:" not in r.byggnader_klippt.read_bytes().decode("latin-1")
    assert gdf.crs.to_epsg() == 3011
    assert abs(gdf.geometry.area.iloc[0] - 100.0) < 0.5  # 10 x 10 m, tillbaka i EPSG:3011
    # Andra körningen: samma resultat och ingen omklippning (nyckel oförändrad)
    stamp = r.byggnader_klippt.stat().st_mtime_ns
    r2 = hamta_omrade(klient, om, ut, laser=False, byggnader=True, skriv=lambda _: None)
    assert r2.byggnader_klippt is not None and r2.byggnader_klippt.stat().st_mtime_ns == stamp
    assert r2.manifest_sha256 == r.manifest_sha256


def test_byggnader_flera_lager_kraver_val(lm: FalskLm, klient: LmKlient, tmp_path: Path) -> None:
    om = las_omrade(skriv_omrade(tmp_path / "in"))
    g = tmp_path / "k.gpkg"
    pol = shapely.box(0, 0, 1, 1)
    for lager in ("byggnad_yta", "byggnad_andra"):
        gpd.GeoDataFrame({"a": [1]}, geometry=[pol], crs="EPSG:3006").to_file(
            g, layer=lager, driver="GPKG"
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.write(g, "k.gpkg")
    lm.filer["/files/k.zip"] = buf.getvalue()
    bb = wgs84_bbox(om.polygon.buffer(500))
    lm.vektor_features = [
        _feat(lm, "k", bb, "2025-01-01T00:00:00Z", "k.zip", "application/zip", "byggnad")
    ]
    with pytest.raises(DataInlasningFel, match="--byggnads-lager"):
        hamta_omrade(klient, om, tmp_path / "d", laser=False, byggnader=True, skriv=lambda _: None)


def test_zip_med_osaker_sokvag_nekas(lm: FalskLm, klient: LmKlient, tmp_path: Path) -> None:
    om = las_omrade(skriv_omrade(tmp_path / "in"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../utanfor.gpkg", b"x")
    lm.filer["/files/ond.zip"] = buf.getvalue()
    bb = wgs84_bbox(om.polygon.buffer(500))
    lm.vektor_features = [
        _feat(lm, "ond", bb, "2025-01-01T00:00:00Z", "ond.zip", "application/zip", "byggnad")
    ]
    with pytest.raises(LantmaterietFel, match="Osäker sökväg"):
        hamta_omrade(klient, om, tmp_path / "d", laser=False, byggnader=True, skriv=lambda _: None)


# -- området ------------------------------------------------------------------------------------


def test_omrade_andra_epsg_raknas_om_och_rutor(tmp_path: Path) -> None:
    p = skriv_omrade(tmp_path, storlek=700.0, epsg=3006)
    om = las_omrade(p)
    assert om.bbox == pytest.approx((E0, N0, E0 + 700, N0 + 700), abs=0.01)
    rutor = rutor_for_omrade(om.polygon, 500.0)
    # E 151 000-151 700, N 6 580 000-6 580 700 på nät om 500 m: 2 x 2 rutor
    # (avrundningen vid omräkningen via 3006 ger inga extra rutor)
    assert rutor == [
        (151000.0, 6580000.0),
        (151000.0, 6580500.0),
        (151500.0, 6580000.0),
        (151500.0, 6580500.0),
    ]
    with pytest.raises(DataInlasningFel, match="mer än gränsen"):
        las_omrade(p, max_km2=0.1)
    with pytest.raises(DataInlasningFel, match="finns inte"):
        las_omrade(tmp_path / "nej.gpkg")


def test_omrade_utan_crs_nekas(tmp_path: Path) -> None:
    import warnings

    gdf = gpd.GeoDataFrame({"a": [1]}, geometry=[shapely.box(0, 0, 10, 10)], crs=None)
    p = tmp_path / "utan.gpkg"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gdf.to_file(p, driver="GPKG")
    with pytest.raises(DataInlasningFel, match="EPSG:3011"):
        las_omrade(p)
