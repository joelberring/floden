"""Hämta (falsk server) och bygg K1 v2-rutor av det: recept pekar på manifestets sha256."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import shapely
from numpy.typing import NDArray

from data_inlasning.cache import DataInlasningFel
from data_inlasning.lantmateriet import skriv_syntetisk_las
from data_inlasning.lasclip import hitta_lasclip
from gemensamt.hash import sha256_fil
from lantmateriet.bygg import SAMMANFATTNING, bygg_omrade, markera_tak
from lantmateriet.hamta import MANIFEST_NAMN, hamta_omrade
from lantmateriet.klient import LmKlient
from lantmateriet.omrade import las_omrade
from lantmateriet.stac import wgs84_bbox
from markmodell.byggnadsregister import ByggnadsKandidat
from markmodell.recept import las_recept
from tests.lantmateriet.falsk_server import FalskLm
from tests.lantmateriet.test_hamta import E0, N0, _feat, skriv_omrade, zip_med_gpkg


def _lasclip_finns() -> bool:
    try:
        hitta_lasclip()
    except DataInlasningFel:
        return False
    return True


def terrang(e: NDArray[np.float64], n: NDArray[np.float64]) -> NDArray[np.float64]:
    return 20.0 + 0.02 * (e - E0)


def skriv_laser(mapp: Path) -> Path:
    """Markpunkter (klass 2) var 1,5 m utom under husen, och tak (klass 1) 8 m över marken."""
    from pyproj import Transformer

    e1, n1 = np.meshgrid(np.arange(E0 - 60, E0 + 260, 1.5), np.arange(N0 - 60, N0 + 260, 1.5))
    e, n = e1.ravel(), n1.ravel()
    z = terrang(e, n)
    inne = ((e > E0 + 20) & (e < E0 + 30) & (n > N0 + 20) & (n < N0 + 30)) | (
        (e > E0 + 120) & (e < E0 + 130) & (n > N0 + 60) & (n < N0 + 70)
    )
    klass = np.where(inne, 1, 2).astype(np.uint8)
    z = np.where(inne, z + 8.0, z)
    e6, n6 = Transformer.from_crs(3011, 3006, always_xy=True).transform(e, n)
    fil = mapp / "24E001_test.las"
    skriv_syntetisk_las(
        fil, np.asarray(e6), np.asarray(n6), z, klass, np.full(e.shape, 500, dtype=np.uint16)
    )
    return fil


def forbered(lm: FalskLm, klient: LmKlient, tmp: Path, filtyp: str = "las") -> tuple[Path, Path]:
    omr = skriv_omrade(tmp / "in", storlek=200.0)
    om = las_omrade(omr)
    laser = skriv_laser(tmp)
    lm.filer[f"/files/{laser.name}"] = laser.read_bytes()
    bb = wgs84_bbox(om.polygon.buffer(500))
    lm.sidor = [
        [
            _feat(
                lm,
                "t1",
                bb,
                "2023-05-01T00:00:00Z",
                laser.name,
                "application/vnd.laszip" if filtyp == "laz" else "application/octet-stream",
            )
        ]
    ]
    lm.filer["/files/byggnad_kn.zip"] = zip_med_gpkg(tmp)
    lm.vektor_features = [
        _feat(lm, "kn", bb, "2025-01-01T00:00:00Z", "byggnad_kn.zip", "application/zip", "byggnad")
    ]
    data = tmp / "data"
    hamta_omrade(klient, om, data, laser=True, byggnader=True, skriv=lambda _: None)
    return omr, data


def test_markera_tak_andrar_bara_oklassade_i_polygonen() -> None:
    kand = ByggnadsKandidat(polygon=((0, 0), (10, 0), (10, 10), (0, 10)), kalla="t")
    e = np.array([5.0, 5.0, 50.0, 5.0])
    n = np.array([5.0, 5.0, 50.0, 5.0])
    k = np.array([1, 2, 1, 7], dtype=np.uint8)
    ut, antal = markera_tak(e, n, k, [kand])
    assert antal == 1 and list(ut) == [6, 2, 1, 7]
    assert list(k) == [1, 2, 1, 7]  # indata orörd


def test_hamta_och_bygg_k1_v2_med_recept_mot_manifest(
    lm: FalskLm, klient: LmKlient, tmp_path: Path
) -> None:
    omr, data = forbered(lm, klient, tmp_path)
    om = las_omrade(omr)
    ut = tmp_path / "k1"
    utskrift: list[str] = []
    res = bygg_omrade(om, data, ut, ruta_storlek_m=100.0, upplosning_m=1.0, skriv=utskrift.append)
    assert sorted(res.rutor) == [
        "mm_151000_6580000",
        "mm_151000_6580100",
        "mm_151100_6580000",
        "mm_151100_6580100",
    ]
    manifest_sha = sha256_fil(data / MANIFEST_NAMN)
    for namn in res.rutor:
        kat = ut / namn
        for fil in (
            "mark.tif",
            "terrang.tif",
            "byggnader.gpkg",
            "recept.json",
            "run.json",
            "osakerhet.tif",
        ):
            assert (kat / fil).is_file(), (namn, fil)
        rec = las_recept(kat / "recept.json")
        assert rec.kontrakt == "K1/v2"
        roller = {(k.roll, k.sha256) for k in rec.kallor}
        assert ("lantmateriet_manifest", manifest_sha) in roller
        assert ("laser_laz", sha256_fil(data / "laser" / "24E001_test.las")) in roller
        assert ("omrade", om.sha256) in roller
    # Hus h1 (151020-151030, 6580020-6580030) ligger i mm_151000_6580000: höjden ur "klass 6" = 8 m
    hus = gpd.read_file(ut / "mm_151000_6580000" / "byggnader.gpkg")
    assert len(hus) == 1
    assert hus["hojd_m"].iloc[0] == pytest.approx(8.0, abs=0.3)
    assert hus["hojdkalla"].iloc[0] == "laser_klass6"
    sam = json.loads((ut / SAMMANFATTNING).read_text(encoding="utf-8"))
    assert sam["manifest_sha256"] == manifest_sha
    assert any("saknar klass 6" in v for v in sam["varningar"])
    # Marken lutar 0,02: terrang.tif ska stämma mot facit (z = 20 + 0,02 (E - E0)) i en mätpunkt.
    import rasterio

    with rasterio.open(ut / "mm_151100_6580100" / "terrang.tif") as src:
        arr = src.read(1)
        rad, kol = src.index(151150.5, 6580150.5)
        assert float(arr[rad, kol]) == pytest.approx(20.0 + 0.02 * 150.5, abs=0.05)


def test_bygg_ar_idempotent_och_tvinga_bygger_om(
    lm: FalskLm, klient: LmKlient, tmp_path: Path
) -> None:
    omr, data = forbered(lm, klient, tmp_path)
    om = las_omrade(omr)
    ut = tmp_path / "k1"
    bygg_omrade(om, data, ut, ruta_storlek_m=100.0, skriv=lambda _: None)
    mark = (ut / "mm_151000_6580000" / "mark.tif").stat().st_mtime_ns
    r2 = bygg_omrade(om, data, ut, ruta_storlek_m=100.0, skriv=lambda _: None)
    assert len(r2.hoppade) == 4
    assert (ut / "mm_151000_6580000" / "mark.tif").stat().st_mtime_ns == mark
    # ändrad parameter -> andra receptet -> bygger om
    r3 = bygg_omrade(om, data, ut, ruta_storlek_m=100.0, upplosning_m=2.0, skriv=lambda _: None)
    assert r3.hoppade == []
    r4 = bygg_omrade(
        om, data, ut, ruta_storlek_m=100.0, upplosning_m=2.0, tvinga=True, skriv=lambda _: None
    )
    assert r4.hoppade == []


def test_utan_byggnader_varnar_och_flagga_byggnader_fil(
    lm: FalskLm, klient: LmKlient, tmp_path: Path
) -> None:
    omr = skriv_omrade(tmp_path / "in", storlek=200.0)
    om = las_omrade(omr)
    laser = skriv_laser(tmp_path)
    lm.filer[f"/files/{laser.name}"] = laser.read_bytes()
    bb = wgs84_bbox(om.polygon.buffer(500))
    lm.sidor = [
        [_feat(lm, "t1", bb, "2023-05-01T00:00:00Z", laser.name, "application/octet-stream")]
    ]
    data = tmp_path / "data"
    hamta_omrade(klient, om, data, laser=True, skriv=lambda _: None)
    r = bygg_omrade(om, data, tmp_path / "a", ruta_storlek_m=100.0, skriv=lambda _: None)
    assert any("Inga byggnader" in v for v in r.varningar)
    hus = gpd.GeoDataFrame(
        {"byggnad_id": ["x1"]},
        geometry=[shapely.box(E0 + 20, N0 + 20, E0 + 30, N0 + 30)],
        crs="EPSG:3011",
    )
    fil = tmp_path / "osm.gpkg"
    hus.to_file(fil, driver="GPKG")
    r2 = bygg_omrade(
        om, data, tmp_path / "b", byggnader_fil=fil, ruta_storlek_m=100.0, skriv=lambda _: None
    )
    assert not any("Inga byggnader" in v for v in r2.varningar)
    g = gpd.read_file(tmp_path / "b" / "mm_151000_6580000" / "byggnader.gpkg")
    assert len(g) == 1 and g["hojd_m"].iloc[0] == pytest.approx(8.0, abs=0.3)


def test_saknad_fil_och_ingen_laser_ger_tydliga_fel(
    lm: FalskLm, klient: LmKlient, tmp_path: Path
) -> None:
    omr, data = forbered(lm, klient, tmp_path)
    om = las_omrade(omr)
    (data / "laser" / "24E001_test.las").unlink()
    with pytest.raises(DataInlasningFel, match="saknas"):
        bygg_omrade(om, data, tmp_path / "k1", ruta_storlek_m=100.0, skriv=lambda _: None)
    with pytest.raises(DataInlasningFel, match="floden data lantmateriet"):
        bygg_omrade(om, tmp_path / "tom", tmp_path / "k1", skriv=lambda _: None)


def test_laz_utan_lasclip_ger_instruktion(
    lm: FalskLm, klient: LmKlient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    omr, data = forbered(lm, klient, tmp_path)
    man = json.loads((data / MANIFEST_NAMN).read_text(encoding="utf-8"))
    post = next(f for f in man["filer"] if f["roll"] == "laser")
    post["fil"] = "laser/x.laz"
    (data / "laser" / "x.laz").write_bytes(b"x")
    post["storlek"] = 1
    post["sha256"] = sha256_fil(data / "laser" / "x.laz")
    (data / MANIFEST_NAMN).write_text(json.dumps(man), encoding="utf-8")
    monkeypatch.delenv("FLODEN_LASCLIP", raising=False)
    monkeypatch.setattr("data_inlasning.lasclip._REPO_BINAR", tmp_path / "finns-inte")
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(DataInlasningFel, match="cargo build --release"):
        bygg_omrade(
            las_omrade(omr), data, tmp_path / "k1", ruta_storlek_m=100.0, skriv=lambda _: None
        )


@pytest.mark.skipif(not _lasclip_finns(), reason="lasclip är inte byggd (cargo build --release)")
def test_riktig_laz_via_lasclip(lm: FalskLm, klient: LmKlient, tmp_path: Path) -> None:
    omr = skriv_omrade(tmp_path / "in", storlek=200.0)
    las = skriv_laser(tmp_path)
    laz = tmp_path / "24E001_test.laz"
    program = hitta_lasclip()
    stor = ["-1e9", "-1e9", "1e9", "1e9"]
    subprocess.run([str(program), str(las), str(laz), *stor], check=True, capture_output=True)
    assert laz.stat().st_size < las.stat().st_size  # komprimerad
    om = las_omrade(omr)
    lm.filer[f"/files/{laz.name}"] = laz.read_bytes()
    bb = wgs84_bbox(om.polygon.buffer(500))
    lm.sidor = [[_feat(lm, "t1", bb, "2023-05-01T00:00:00Z", laz.name, "application/vnd.laszip")]]
    data = tmp_path / "data"
    hamta_omrade(klient, om, data, laser=True, skriv=lambda _: None)
    res = bygg_omrade(om, data, tmp_path / "k1", ruta_storlek_m=100.0, skriv=lambda _: None)
    assert len(res.rutor) == 4
    import rasterio

    with rasterio.open(tmp_path / "k1" / "mm_151100_6580100" / "terrang.tif") as src:
        rad, kol = src.index(151150.5, 6580150.5)
        assert float(src.read(1)[rad, kol]) == pytest.approx(20.0 + 0.02 * 150.5, abs=0.05)


def test_en_ruta_for_vatten_tackar_hela_omradet(
    lm: FalskLm, klient: LmKlient, tmp_path: Path
) -> None:
    omr, data = forbered(lm, klient, tmp_path)
    om = las_omrade(omr)
    res = bygg_omrade(
        om, data, tmp_path / "en", en_ruta=True, upplosning_m=1.0, skriv=lambda _: None
    )
    assert res.rutor == ["mm_151000_6580000"]
    import rasterio

    with rasterio.open(tmp_path / "en" / "mm_151000_6580000" / "mark.tif") as src:
        assert (src.width, src.height) == (200, 200)  # området 200 x 200 m, 1 m-celler
        assert src.bounds.left == 151000.0 and src.bounds.top == 6580200.0
