# ruff: noqa: E501
"""Stockholm: byggnader ur en LOKAL baskartefil som inte är öppna data (`kalla = "lokal_fil"`).

SYNTETISKT: lagernamn, koordinater, höjder och filnamn är påhittade (ingen riktig baskarta, inga riktiga
lagernamn). Testet visar att kedjan hänger ihop och att allt som härleds ur filen är stängt för MCP och aldrig
nämner filens namn eller lagernamn i utskrift, varningar eller manifest.
"""

from __future__ import annotations

import json
import tomllib
import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import pytest
import shapely

from floden_mcp.oppenhet import Oppenhet
from geografi.mappa import mappa_geografi
from geografi.modell import las_manifest
from kommuner.baskarta import byggnader_ur_lokala_filer
from kommuner.konfig import KommunKonfigFel, las_kommun, tolka_kommun
from kommuner.projekt import skapa_projekt
from kommuner.skapa import SkapaFel, beraekna_yta
from kommuner.wfs import KommunKallaFel
from lantmateriet.klient import LmKlient
from lantmateriet.nycklar import Nycklar
from lantmateriet.stac import wgs84_bbox
from markmodell.lagerprofil import las_lagerprofil
from tests.kommuner.test_skapa import skriv_laser
from tests.lantmateriet.conftest import lm  # noqa: F401
from tests.lantmateriet.falsk_server import HEMLIGHET, KLIENT_ID, FalskLm
from tests.lantmateriet.test_hamta import _feat

REPO = Path(__file__).resolve().parents[2]
PROFIL = las_lagerprofil(REPO / "data" / "lagerprofiler" / "stockholm_baskarta.toml")

# Påhittade lagernamn efter Stockholms grammatik (profilens byggnadsregel) och påhittat filnamn.
YTA = "ByggnadTestgyBBefintlig"
LINJE = "ByggnadTestglBBefintlig"
SKYMD = "ByggnadSkymtgyBSkymd"
ANNAT = "MarkanlaggningTestgyBBefintlig"
HEMLIG_FIL = "hemlig-baskarta-TESTNAMN.zip"
BBOX = (1000.0, 2000.0, 1200.0, 2200.0)


def kvadrat(x: float, y: float, b: float = 10.0, h: float | None = None) -> list[list[float]]:
    return [[x, y], [x + b, y], [x + b, y + b], [x, y + b]] if h is None else [[x, y, h], [x + b, y, h], [x + b, y + b, h], [x, y + b, h]]


def lagerrad(handtag: int, namn: str) -> dict[str, Any]:
    return {"object": "LAYER", "handle": [0, 1, handtag], "name": namn}


def hatch(handtag: int, lager: int, ringar: list[list[list[float]]]) -> dict[str, Any]:
    """Skraffyr med polylinjebanor (en bana per ring)."""
    return {
        "entity": "HATCH", "handle": [0, 1, handtag], "entmode": 2, "layer": [5, 1, lager, lager],
        "paths": [
            {"flag": 2, "polyline_paths": [{"point": p[:2], "bulge": 0.0} for p in r]} for r in ringar
        ],
    }  # fmt: skip


def polylinje(handtag: int, lager: int, punkter: list[list[float]], z: float) -> dict[str, Any]:
    return {
        "entity": "LWPOLYLINE", "handle": [0, 1, handtag], "entmode": 2, "layer": [5, 1, lager, lager],
        "flag": 1, "points": [p[:2] for p in punkter], "bulges": [], "elevation": z,
    }  # fmt: skip


def dwg_json(hus: list[tuple[float, float, float | None]], extra: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    objekt: list[dict[str, Any]] = [lagerrad(40, YTA), lagerrad(41, LINJE), lagerrad(42, SKYMD), lagerrad(43, ANNAT)]
    for i, (x, y, z) in enumerate(hus):
        objekt.append(hatch(100 + i, 40, [kvadrat(x, y)]))
        if z is not None:
            objekt.append(polylinje(200 + i, 41, kvadrat(x, y), z))
    objekt.append(hatch(300, 42, [kvadrat(1100, 2100)]))  # skymt: ska hoppas över
    objekt.append(hatch(301, 43, [kvadrat(1150, 2150)]))  # inte byggnad
    objekt.extend(extra or [])
    return {"OBJECTS": objekt}


def skriv_zip(mapp: Path, namn: str, d: dict[str, Any]) -> Path:
    fil = mapp / namn
    with zipfile.ZipFile(fil, "w") as z:
        z.writestr("kartblad-1.json", json.dumps(d))
    return fil


def kor(filer: list[Path], ut: Path, **kw: Any) -> tuple[Path, int, list[str]]:
    return byggnader_ur_lokala_filer(
        filer, bbox_publicering=BBOX, epsg_publicering=3011, lagerprofil=PROFIL, ut=ut / "byggnader.gpkg", **kw
    )


# ---------------------------------------------------------------------------------- byggnadsläsaren


def test_zip_med_dwgread_json_ger_byggnader_med_z_och_hoppar_over_skymt_och_annat(tmp_path: Path) -> None:
    fil = skriv_zip(tmp_path, HEMLIG_FIL, dwg_json([(1010, 2010, 30.5), (1050, 2050, 41.0), (1080, 2080, None)]))
    ut, antal, varn = kor([fil], tmp_path)
    gdf = gpd.read_file(ut).sort_values("takhojd_rh2000", na_position="last")
    assert antal == 3 and list(gdf.columns) == ["byggnad_id", "takhojd_rh2000", "geometry"]
    assert gdf["takhojd_rh2000"].dropna().tolist() == pytest.approx([30.5, 41.0])
    assert gdf["takhojd_rh2000"].isna().sum() == 1
    assert gdf.crs.to_epsg() == 3011
    text = " ".join(varn)
    assert "OKONTROLLERAT" in text and "1 av 3" in text  # höjden är okontrollerad och ett hus saknar z
    for hemligt in (YTA, LINJE, SKYMD, ANNAT, HEMLIG_FIL, "TESTNAMN"):
        assert hemligt not in text


def test_z_tillagg_och_hojd_fran_laser(tmp_path: Path) -> None:
    fil = skriv_zip(tmp_path, HEMLIG_FIL, dwg_json([(1010, 2010, 30.0)]))
    _, _, _ = kor([fil], tmp_path, z_tillagg_m=-0.5)
    assert float(gpd.read_file(tmp_path / "byggnader.gpkg")["takhojd_rh2000"].iloc[0]) == pytest.approx(29.5)
    _, antal, varn = kor([fil], tmp_path, hojd_fran="laser")
    g = gpd.read_file(tmp_path / "byggnader.gpkg")
    assert antal == 1 and g["takhojd_rh2000"].isna().all()
    assert any("laser" in v for v in varn)
    with pytest.raises(KommunKallaFel, match="hojd_fran"):
        kor([fil], tmp_path, hojd_fran="gissa")


def test_orimlig_z_ignoreras_med_varning(tmp_path: Path) -> None:
    fil = skriv_zip(tmp_path, HEMLIG_FIL, dwg_json([(1010, 2010, 9999.0)]))
    _, _, varn = kor([fil], tmp_path)
    assert gpd.read_file(tmp_path / "byggnader.gpkg")["takhojd_rh2000"].isna().all()
    assert any("utanför" in v for v in varn)


def test_flera_filer_overlappande_hus_raknas_en_gang_och_klipps_till_omradet(tmp_path: Path) -> None:
    a = skriv_zip(tmp_path, "a.zip", dwg_json([(1010, 2010, 30.0), (1100, 2100, 31.0)]))
    (tmp_path / "b.json").write_text(json.dumps(dwg_json([(1100, 2100, 31.0), (1190, 2190, 32.0), (5000, 5000, 33.0)])), encoding="utf-8")
    _, antal, _ = kor([a, tmp_path / "b.json"], tmp_path)
    assert antal == 3  # (1010), (1100) en gång, (1190); huset vid 5000 ligger utanför området


def test_innergard_ger_hal_och_kontur_utan_yta_blir_hus(tmp_path: Path) -> None:
    d = dwg_json([], extra=[hatch(400, 40, [kvadrat(1010, 2010, 40.0), kvadrat(1020, 2020, 10.0)]), polylinje(401, 41, kvadrat(1100, 2010), 25.0)])
    ut, antal, varn = kor([skriv_zip(tmp_path, "c.zip", d)], tmp_path)
    g = gpd.read_file(ut)
    assert antal == 2
    ring = g[g.geometry.area > 1000].geometry.iloc[0]
    assert ring.area == pytest.approx(1600.0 - 100.0) and len(ring.interiors) == 1
    assert any("utan yta" in v for v in varn)


def test_lager_monster_ersatter_profilen(tmp_path: Path) -> None:
    d = dwg_json([(1010, 2010, 30.0)])
    _, antal, _ = kor([skriv_zip(tmp_path, "d.zip", d)], tmp_path, lager_monster="Markanlaggning")
    assert antal == 1  # bara det andra lagrets yta (1150, 2150)
    assert gpd.read_file(tmp_path / "byggnader.gpkg").geometry.iloc[0].bounds[0] == pytest.approx(1150.0)


def test_felmeddelanden_namnger_varken_fil_eller_lager(tmp_path: Path) -> None:
    trasig = tmp_path / HEMLIG_FIL
    trasig.write_bytes(b"inte en zip")
    with pytest.raises(KommunKallaFel) as e:
        kor([trasig], tmp_path)
    assert "TESTNAMN" not in str(e.value) and "baskartefil 1" in str(e.value)
    saknas = tmp_path / "finns-inte-TESTNAMN.zip"
    with pytest.raises(KommunKallaFel) as e2:
        kor([saknas], tmp_path)
    assert "TESTNAMN" not in str(e2.value)
    tom = skriv_zip(tmp_path, "tom.zip", {"OBJECTS": [lagerrad(40, ANNAT)]})
    with pytest.raises(KommunKallaFel) as e3:
        kor([tom], tmp_path)
    assert "Inga byggnader" in str(e3.value) and ANNAT not in str(e3.value)
    text = tmp_path / "x.txt"
    text.write_text("x")
    with pytest.raises(KommunKallaFel, match=r"\.zip, \.dwg eller \.json"):
        kor([text], tmp_path)


# ---------------------------------------------------------------------------------- kommunfilen


def test_stockholms_kommunfil_ar_provomrade_med_intern_licens_och_lokal_fil() -> None:
    k = las_kommun("stockholm")
    assert k.publicering_epsg == 3011 and k.byggnader.kalla == "lokal_fil"
    assert k.byggnader.licens == "intern" and k.byggnader.lokal is not None
    assert k.byggnader.lokal.lagerprofil == "stockholm_baskarta" and k.byggnader.lokal.hojd_fran == "takkontur_z"
    assert k.hojd.kalla == "lantmateriet" and not k.hojd.reserv_lantmateriet and k.lantmateriet.produkter == ("laser",)
    assert k.filpaket is None
    o = k.omrade(None)
    assert "PROVOMRÅDE" in o.namn and 480 <= o.sida_m <= 520
    assert "stockholm-test" in k.geografier and k.geografier["stockholm-test"].omrade == "mariatorget"
    assert (REPO / "data" / "lagerprofiler" / f"{k.byggnader.lokal.lagerprofil}.toml").is_file()


def _stockholm_dict() -> dict[str, Any]:
    with (REPO / "data/kommuner/stockholm.toml").open("rb") as fh:
        return tomllib.load(fh)


def test_lokal_fil_kraver_intern_licens_och_lokal_tabell() -> None:
    d = _stockholm_dict()
    d["byggnader"]["licens"] = "CC0-1.0"
    with pytest.raises(KommunKonfigFel, match="intern"):
        tolka_kommun(d)
    d = _stockholm_dict()
    del d["byggnader"]["lokal"]
    with pytest.raises(KommunKonfigFel, match="byggnader.lokal"):
        tolka_kommun(d)
    d = _stockholm_dict()
    d["byggnader"]["lokal"]["hojd_fran"] = "gissa"
    with pytest.raises(KommunKonfigFel, match="hojd_fran"):
        tolka_kommun(d)
    d = _stockholm_dict()
    d["byggnader"]["lokal"]["okand"] = 1
    with pytest.raises(KommunKonfigFel, match="okända"):
        tolka_kommun(d)
    d = las_kommun("goteborg")
    assert d.byggnader.lokal is None


# ---------------------------------------------------------------------------------- hela mappningen


def stockholm_varld(lm: FalskLm, rot: Path) -> dict[str, Any]:  # noqa: F811
    d = _stockholm_dict()
    d["omraden"]["mariatorget"]["bbox"] = [153600.0, 6577950.0, 153700.0, 6578050.0]
    d["lantmateriet"].update(kontext_m=20.0, upplosning_m=2.0)
    d["dagsljus"].update(azimutriktningar=32, hojd_max_m=3, cellstorlek_m=2)
    d["geografier"]["stockholm-test"]["max_buffert_m"] = 400.0
    kommun = tolka_kommun(d)
    yta = beraekna_yta(kommun, kommun.omrade(None))
    laser = skriv_laser(rot, yta.berakning_3011)
    lm.filer[f"/files/{laser.name}"] = laser.read_bytes()
    bb = wgs84_bbox(shapely.box(*yta.berakning_3011).buffer(900))
    lm.sidor = [[_feat(lm, "g1", bb, "2023-05-01T00:00:00Z", laser.name, "application/octet-stream")]]
    (rot / "config.toml").write_text(
        '[webb]\ncachemapp = "cache"\nkorningsmapp = "korningar"\ndemomapp = "demo"\nfragemappar = []\n', encoding="utf-8"
    )
    mx, my = (153600.0 + 153700.0) / 2, (6577950.0 + 6578050.0) / 2
    # Samma lägen som lasernas två "hus" (skriv_laser), med baskartans z (påhittad, 8 m över lasermarken).
    hus = [(mx - 40, my - 10, 20.0, 20.0, 26.0), (mx + 20, my + 10, 20.0, 20.0, 27.0)]
    objekt: list[dict[str, Any]] = [lagerrad(40, YTA), lagerrad(41, LINJE)]
    for i, (x, y, b, _h, z) in enumerate(hus):
        objekt.append(hatch(100 + i, 40, [kvadrat(x, y, b)]))
        objekt.append(polylinje(200 + i, 41, kvadrat(x, y, b), z))
    fil = skriv_zip(rot, HEMLIG_FIL, {"OBJECTS": objekt})
    return {"kommun": kommun, "rot": rot, "lm": lm, "fil": fil}


def mappa_stockholm(v: dict[str, Any], **kw: Any) -> Any:
    server: FalskLm = v["lm"]
    utskrift: list[str] = kw.pop("utskrift", [])
    return mappa_geografi(
        v["kommun"], "stockholm-test", konto_id=None, geo_rot=v["rot"] / "geo", konfig={},
        konfig_fil=v["rot"] / "config.toml", skriv=utskrift.append,
        nycklar_fran=lambda _k: Nycklar(id=KLIENT_ID, hemlighet=HEMLIGHET),
        klient_fabrik=lambda n: LmKlient(n, server.tjanster(), timeout_s=10.0), **kw,
    )  # fmt: skip


def alla_texter(rot: Path) -> str:
    """Allt som skrivits under geo/ som text (manifest, oppenhet.toml, kallor.json ...)."""
    delar = []
    for f in (rot / "geo").rglob("*"):
        if f.is_file() and f.suffix in {".json", ".toml"}:
            delar.append(f.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(delar)


@pytest.fixture(scope="module")
def sthlm(tmp_path_factory: pytest.TempPathFactory) -> Any:
    server = FalskLm().start()
    try:
        rot = tmp_path_factory.mktemp("sthlm")
        v = stockholm_varld(server, rot)
        ut: list[str] = []
        # --villkor-kontrollerade ska INTE öppna något här.
        res = mappa_stockholm(v, baskarta_filer=[v["fil"]], villkor_kontrollerade=True, utskrift=ut)
        yield {**v, "res": res, "utskrift": ut}
    finally:
        server.stop()


def test_mappningen_ger_byggnader_ur_lokal_fil_med_okontrollerad_hojd(sthlm: dict[str, Any]) -> None:
    res = sthlm["res"]
    d, _ = las_manifest(sthlm["rot"] / "geo", res.ref)
    assert d["parametrar"]["byggnadskalla"] == "lokal_fil" and d["parametrar"]["hojdkalla"] == "lantmateriet"
    b = d["kallor"]["byggnader"]
    assert b["kalla"] == "lokal_fil" and b["licens"] == "intern" and b["stangd"] is True and b["antal_filer"] == 1
    assert any("OKONTROLLERAT" in v for v in d["varningar"])
    ra = sthlm["rot"] / "geo" / "_lager" / d["lager"]["radata"]["sha256"]
    g = gpd.read_file(ra / "kommun" / "byggnader.gpkg")
    assert len(g) == 2 and sorted(g["takhojd_rh2000"]) == pytest.approx([26.0, 27.0])
    kallor = json.loads((ra / "filpaket" / "kallor.json").read_text(encoding="utf-8"))
    assert len(kallor["baskarta_lokal"][0]["sha256"]) == 64 and set(kallor["baskarta_lokal"][0]) == {"nr", "sha256"}
    assert len(list((sthlm["rot"] / "geo" / "_lager" / d["lager"]["k1_rutnat"]["sha256"]).glob("mm_*"))) >= 1


def test_allt_harlett_ur_baskartan_ar_stangt_aven_med_villkor_kontrollerade(sthlm: dict[str, Any]) -> None:
    res = sthlm["res"]
    assert res.oppen_for_mcp is False
    d, _ = las_manifest(sthlm["rot"] / "geo", res.ref)
    assert d["oppen_for_mcp"] is False
    spar = Oppenhet(sthlm["rot"], korningsmapp=sthlm["rot"] / "korningar")
    lager = sthlm["rot"] / "geo" / "_lager"
    for mapp in (
        lager / d["lager"]["radata"]["sha256"], lager / d["lager"]["k1_rutnat"]["sha256"],
        sthlm["rot"] / "geo" / "stockholm-test" / "v1", lager / d["lager"]["radata"]["sha256"] / "kommun" / "byggnader.gpkg",
    ):  # fmt: skip
        assert not spar.bedom(mapp).oppen, mapp
        meta = tomllib.loads((mapp if mapp.is_dir() else mapp.parent).joinpath("oppenhet.toml").read_text(encoding="utf-8")) if (mapp.is_dir() and (mapp / "oppenhet.toml").is_file()) else None
        if meta is not None:
            assert meta["oppen"] is False and meta["licens"] == "intern"
    with pytest.raises(Exception, match="inte öppna"):
        spar.kontrollera_sokvag(lager / d["lager"]["k1_rutnat"]["sha256"])


def test_inget_filnamn_lagernamn_eller_sokvag_i_utskrift_manifest_eller_metadata(sthlm: dict[str, Any]) -> None:
    text = "\n".join(sthlm["utskrift"]) + alla_texter(sthlm["rot"])
    for hemligt in (HEMLIG_FIL, "TESTNAMN", YTA, LINJE, str(sthlm["rot"]), "Byggnad" + "Test"):
        assert hemligt not in text, hemligt
    assert any("förblir stängda" in r for r in sthlm["utskrift"])  # --villkor-kontrollerade nekades uttryckligen


def test_projekt_i_stangd_geografi_ar_stangt(sthlm: dict[str, Any]) -> None:
    d, _ = las_manifest(sthlm["rot"] / "geo", sthlm["res"].ref)
    k = d["karna_3011"]
    avgr = (k[0] + 20.0, k[1] + 20.0, k[2] - 20.0, k[3] - 20.0)
    res = skapa_projekt(
        sthlm["kommun"], ref=sthlm["res"].ref, avgransning=avgr, namn="Prov", konto_id=None,
        geo_rot=sthlm["rot"] / "geo", projektrot=sthlm["rot"] / "projekt", konfig={},
        konfig_fil=sthlm["rot"] / "config.toml", skriv=lambda _: None,
    )  # fmt: skip
    meta = tomllib.loads((res.mapp / "oppenhet.toml").read_text(encoding="utf-8"))
    assert meta["oppen"] is False and meta["licens"] == "intern"
    assert not Oppenhet(sthlm["rot"]).bedom(res.mapp).oppen


def test_fel_utan_fil_eller_med_fel_kalla_innan_nagot_hamtas(lm: FalskLm, tmp_path: Path) -> None:  # noqa: F811
    v = stockholm_varld(lm, tmp_path)
    with pytest.raises(SkapaFel, match="--baskarta-fil"):
        mappa_stockholm(v)
    with pytest.raises(SkapaFel, match="finns inte") as e:
        mappa_stockholm(v, baskarta_filer=[tmp_path / "saknas-TESTNAMN.zip"])
    assert "TESTNAMN" not in str(e.value)
    with pytest.raises(SkapaFel, match="hör till byggnader ur en lokal fil"):
        mappa_stockholm(v, baskarta_filer=[v["fil"]], byggnader_kalla="wfs")
    assert lm.antal("GET", "/stac") == 0  # felen kom före all hämtning
