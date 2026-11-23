# ruff: noqa: E501
"""`floden geografi mappa` och `floden projekt skapa` mot en falsk Lantmäteriserver och ett falskt WFS (inget nät).

SYNTETISKT: laserpunkterna, byggnaderna och WFS-svaren är påhittade för testet. Formatet på Göteborgs
tjänst är antaget och aldrig bekräftat; testet visar att kedjan hänger ihop (nyckel, hämtning, K1, projekt
i registret, körningar, öppenhet), inte att Göteborgs verkliga data ser ut så.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import shapely
from pyproj import Transformer

from data_inlasning.lantmateriet import skriv_syntetisk_las
from floden_mcp.oppenhet import Oppenhet
from geografi.mappa import mappa_geografi
from geografi.modell import las_manifest
from kommuner.konfig import KommunKonfig, las_kommun, tolka_kommun
from kommuner.projekt import kor_projekt, skapa_projekt, skapa_scenario_hus
from kommuner.skapa import SkapaFel, beraekna_yta
from kommuner.wfs import KommunKallaFel
from konto.databas import Databas
from konto.register import Register
from lantmateriet.klient import LmKlient
from lantmateriet.nycklar import NyckelFel, Nycklar
from lantmateriet.stac import wgs84_bbox
from tests.lantmateriet.falsk_server import HEMLIGHET, KLIENT_ID, FalskLm
from tests.lantmateriet.test_hamta import _feat

REPO = Path(__file__).resolve().parents[2]
KAPABILITIES = (
    b'<WFS_Capabilities><FeatureTypeList>'
    b'<FeatureType><Name>gbg:trad</Name></FeatureType>'
    b'<FeatureType><Name>gbg:byggnad_yta</Name></FeatureType>'
    b'</FeatureTypeList></WFS_Capabilities>'
)


def liten_kommun() -> KommunKonfig:
    """Göteborgs fil med ett litet område (100 m) och litet sammanhang, så att testet går fort."""
    with (REPO / "data/kommuner/goteborg.toml").open("rb") as fh:
        d = tomllib.load(fh)
    d["omraden"]["haga"]["bbox"] = [147450.0, 6397550.0, 147550.0, 6397650.0]
    d["lantmateriet"]["kontext_m"] = 20.0
    d["dagsljus"]["azimutriktningar"] = 32
    d["dagsljus"]["hojd_max_m"] = 3
    d["geografier"]["goteborg-test"]["max_buffert_m"] = 400.0
    d["lantmateriet"]["upplosning_m"] = 2.0
    d["dagsljus"]["cellstorlek_m"] = 2
    return tolka_kommun(d)


def skriv_laser(mapp: Path, yta: tuple[float, float, float, float]) -> Path:
    """Skålformad terräng (lågpunkt i mitten); två 'hus' (klass 1 = oklassat, 8 m högre)."""
    e0, n0, e1, n1 = yta
    e1d, n1d = np.meshgrid(np.arange(e0 - 230, e1 + 230, 1.0), np.arange(n0 - 230, n1 + 230, 1.0))
    e, n = e1d.ravel(), n1d.ravel()
    mx, my = (e0 + e1) / 2, (n0 + n1) / 2
    r = ((e - mx) ** 2 + (n - my) ** 2) ** 0.5
    # Skål med krön på 100 m: marken stiger från mitten till kröncirkeln och faller utanför den.
    z = np.where(r <= 100.0, 10.0 + 0.05 * r, 15.0 - 0.05 * (r - 100.0))
    hus = ((e > mx - 40) & (e < mx - 20) & (n > my - 10) & (n < my + 10)) | (
        (e > mx + 20) & (e < mx + 40) & (n > my + 10) & (n < my + 30)
    )
    klass = np.where(hus, 1, 2).astype(np.uint8)
    z = np.where(hus, z + 8.0, z)
    e6, n6 = Transformer.from_crs(3011, 3006, always_xy=True).transform(e, n)
    fil = mapp / "24E001_gbg.las"
    skriv_syntetisk_las(
        fil, np.asarray(e6), np.asarray(n6), z, klass, np.full(e.shape, 500, dtype=np.uint16)
    )
    return fil


def wfs_svar(bbox_3007: tuple[float, float, float, float]) -> bytes:
    """Två byggnader i EPSG:3007 med ett personnära attribut (adress) som inte får sparas."""
    mx = (bbox_3007[0] + bbox_3007[2]) / 2
    my = (bbox_3007[1] + bbox_3007[3]) / 2

    def ruta(x: float, y: float, b: float, h: float) -> list[list[list[float]]]:
        return [[[x, y], [x + b, y], [x + b, y + h], [x, y + h], [x, y]]]

    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": ruta(mx - 25, my - 8, 14, 16)},
                "properties": {"OBJECTID": 11, "Hojd": 14.5, "Adress": "Storgatan 1 (hemlig)"},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": ruta(mx + 12, my + 12, 14, 16)},
                "properties": {"OBJECTID": 12, "Adress": "Storgatan 2 (hemlig)"},
            },
        ],
    }
    return json.dumps(fc).encode()


class Falskt:
    """Falsk WFS-hämtare som noterar alla adresser."""

    def __init__(self, kommun: KommunKonfig) -> None:
        self.adresser: list[str] = []
        self.kommun = kommun
        self.fel: Exception | None = None
        self.kropp: bytes | None = None

    def __call__(self, url: str) -> bytes:
        self.adresser.append(url)
        if self.fel is not None:
            raise self.fel
        if "GetCapabilities" in url:
            return KAPABILITIES
        if self.kropp is not None:
            return self.kropp
        return wfs_svar(self.kommun.omrade(None).bbox)


def bygg_varld(lm: FalskLm, rot: Path) -> dict[str, Any]:
    kommun = liten_kommun()
    yta = beraekna_yta(kommun, kommun.omrade(None))
    laser = skriv_laser(rot, yta.berakning_3011)
    lm.filer[f"/files/{laser.name}"] = laser.read_bytes()
    bb = wgs84_bbox(shapely.box(*yta.berakning_3011).buffer(900))
    lm.sidor = [[_feat(lm, "g1", bb, "2023-05-01T00:00:00Z", laser.name, "application/octet-stream")]]
    (rot / "config.toml").write_text(
        '[webb]\ncachemapp = "cache"\nkorningsmapp = "korningar"\ndemomapp = "demo"\n'
        'fragemappar = []\n',
        encoding="utf-8",
    )
    return {"kommun": kommun, "yta": yta, "rot": rot, "lm": lm}


@pytest.fixture
def varld(lm: FalskLm, tmp_path: Path) -> dict[str, Any]:
    return bygg_varld(lm, tmp_path)


def mappa(v: dict[str, Any], **kw: Any) -> Any:
    kommun = v["kommun"]
    lm: FalskLm = v["lm"]
    falsk = kw.pop("wfs", None) or Falskt(kommun)
    utskrift: list[str] = kw.pop("utskrift", [])
    return mappa_geografi(
        kommun,
        "goteborg-test",
        konto_id=kw.pop("konto_id", None),
        geo_rot=v["rot"] / "geo",
        konfig=kw.pop("konfig", {}),
        konfig_fil=v["rot"] / "config.toml",
        skriv=utskrift.append,
        hamta_bytes=falsk,
        nycklar_fran=kw.pop("nycklar_fran", lambda _k: Nycklar(id=KLIENT_ID, hemlighet=HEMLIGHET)),
        klient_fabrik=lambda n: LmKlient(n, lm.tjanster(), timeout_s=10.0),
        **kw,
    )


@pytest.fixture(scope="module")
def geo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    """Geografin mappad en gång (tar en stund) och delad av testerna som bara läser den."""
    server = FalskLm().start()
    try:
        v = bygg_varld(server, tmp_path_factory.mktemp("geo"))
        ut: list[str] = []
        res = mappa(v, utskrift=ut)
        res_v2 = mappa(v, version="v2", villkor_kontrollerade=True)
        yield {**v, "res": res, "res_v2": res_v2, "utskrift": ut}
    finally:
        server.stop()


def lager_dir(v: dict[str, Any], d: dict[str, Any], namn: str) -> Path:
    return v["rot"] / "geo" / "_lager" / d["lager"][namn]["sha256"]


def test_mappa_ger_manifest_k46_med_buffert_och_lager(geo: dict[str, Any]) -> None:
    res = geo["res"]
    assert res.nytt and res.ref == "goteborg-test@v1"
    d, sha = las_manifest(geo["rot"] / "geo", "goteborg-test@v1")
    assert sha == res.manifest_sha256 and d["kontrakt"] == "floden-geografi/v1"
    k = d["koordinater"]
    assert k["publicering_epsg"] == 3007 and k["intern_epsg"] == 3011 and 0.14 < k["skalfel_langd_procent"] < 0.18
    # Bufferten är den större av dagsljus (3 m / tan 5 grader = 34 m) och skyfall (tillrinning uppströms).
    b = d["buffert"]
    assert 33 < b["dagsljus_paverkansradie_m"] < 35
    assert b["skyfall_tillrinning_krav_m"] > b["dagsljus_paverkansradie_m"]
    assert not b["tillrinning_nar_kanten"] and b["vald_m"] >= b["skyfall_tillrinning_krav_m"]
    # Lager: K1-rutnät och rådata, sha256 per fil, och manifestet nämner ingen nyckel eller adress.
    assert d["lager"]["k1_rutnat"]["rutor"] and d["lager"]["k1_rutnat"]["filer"]
    assert any(f["fil"].endswith("mark.tif") for f in d["lager"]["k1_rutnat"]["filer"])
    text = (geo["rot"] / "geo/goteborg-test/v1/manifest.json").read_text(encoding="utf-8") + "\n".join(geo["utskrift"])
    assert HEMLIGHET not in text and KLIENT_ID not in text and "hemlig" not in text
    assert d["forberakningar"]["terrang_karna"]["relief_m"] > 0.5
    assert "Buffert (dagsljus)" in text and "Tillrinning uppströms kärnan" in text
    # Dataminimering: kommunens adresser kom aldrig vidare.
    import geopandas as gpd

    gdf = gpd.read_file(lager_dir(geo, d, "radata") / "kommun/byggnader.gpkg")
    assert len(gdf) == 2 and "adress" not in {c.lower() for c in gdf.columns}


def test_mappa_ar_idempotent(geo: dict[str, Any]) -> None:
    anrop = geo["lm"].antal("GET", "/stac")
    res2 = mappa(geo)
    assert not res2.nytt and res2.manifest_sha256 == geo["res"].manifest_sha256
    assert geo["lm"].antal("GET", "/stac") == anrop  # inget hämtades igen


def test_ny_version_rorer_inte_den_gamla_och_lager_kontrolleras(geo: dict[str, Any]) -> None:
    d1, sha1 = las_manifest(geo["rot"] / "geo", "goteborg-test@v1")
    d2, _ = las_manifest(geo["rot"] / "geo", geo["res_v2"].ref)
    assert d2["version"] == "v2" and sha1 == geo["res"].manifest_sha256
    # En omhämtning ger egna lager (källornas sha256 ingår i markmodellens recept): v1 ligger kvar orörd.
    assert (geo["rot"] / "geo" / "_lager" / d1["lager"]["k1_rutnat"]["sha256"]).is_dir()
    # Ändras en fil i ett lager upptäcks det när manifestet läses.
    kopia = geo["rot"] / "geo" / "_lager" / d2["lager"]["k1_rutnat"]["sha256"]
    mark = next(kopia.glob("mm_*/mark.tif"))
    orig = mark.read_bytes()
    mark.write_bytes(orig + b"x")
    try:
        with pytest.raises(Exception, match="ändrats"):
            las_manifest(geo["rot"] / "geo", "goteborg-test@v2")
    finally:
        mark.write_bytes(orig)
    las_manifest(geo["rot"] / "geo", "goteborg-test@v2")


def test_oppenhet_ar_stangd_tills_villkoren_kontrollerats(geo: dict[str, Any]) -> None:
    d, _ = las_manifest(geo["rot"] / "geo", geo["res"].ref)
    spar = Oppenhet(geo["rot"], korningsmapp=geo["rot"] / "korningar")
    ra, k1 = lager_dir(geo, d, "radata"), lager_dir(geo, d, "k1_rutnat")
    rat = tomllib.loads((ra / "oppenhet.toml").read_text(encoding="utf-8"))
    assert rat["oppen"] is False and rat["licens"] == "CC0-1.0"
    assert not geo["res"].oppen_for_mcp and not spar.bedom(k1).oppen and not spar.bedom(ra).oppen
    # Version med villkoren kontrollerade: härlett öppet, rådata fortfarande stängd.
    d2, _ = las_manifest(geo["rot"] / "geo", geo["res_v2"].ref)
    assert geo["res_v2"].oppen_for_mcp
    assert spar.bedom(lager_dir(geo, d2, "k1_rutnat")).oppen
    assert not spar.bedom(lager_dir(geo, d2, "radata")).oppen


def konfig_pa() -> dict[str, Any]:
    return {"konto": {"inloggning": "pa"}}


def test_mappa_kraver_superadmin_nar_inloggning_ar_pa(varld: dict[str, Any]) -> None:
    reg = Register(Databas(varld["rot"] / "konto.sqlite"))
    org = reg.skapa_organisation("Test")
    vanlig = reg.forbered_konto("anna@example.test")
    reg.lagg_till_medlem(vanlig, org, None, "test")
    reg.gor_organisationsadmin(vanlig, org)
    with pytest.raises(SkapaFel, match="--konto"):
        mappa(varld, konfig=konfig_pa(), register=reg)
    with pytest.raises(SkapaFel, match="superadmin"):
        mappa(varld, konto_id=vanlig, konfig=konfig_pa(), register=reg)  # organisationsadmin räcker inte
    with pytest.raises(SkapaFel, match="superadmin"):
        mappa(varld, konto_id="finns-inte", konfig=konfig_pa(), register=reg)
    assert varld["lm"].antal("GET", "/stac") == 0  # inget hämtades innan kontrollen föll
    # Superadmin passerar kontrollen och stoppas först av den saknade nyckeln (inget annat hämtades).
    sa = reg.forbered_konto("sa@example.test")
    reg.ge_superadmin(sa)

    def ingen(_k: str | None) -> Nycklar:
        raise NyckelFel("nyckel saknas")

    with pytest.raises(NyckelFel, match="nyckel saknas"):
        mappa(varld, konto_id=sa, konfig=konfig_pa(), register=reg, nycklar_fran=ingen)


def test_mappa_kraver_superadmin_aven_for_organisationens_agare(varld: dict[str, Any]) -> None:
    """Ägare och organisationsadmin har stora rättigheter i organisationen men får inte mappa."""
    reg = Register(Databas(varld["rot"] / "konto.sqlite"))
    agare = reg.forbered_konto("agare@example.test")
    org = reg.skapa_organisation("Floden", agare=agare)
    admin = reg.forbered_konto("admin@example.test")
    reg.gor_organisationsadmin(admin, org)
    for konto in (agare, admin):
        with pytest.raises(SkapaFel, match="superadmin"):
            mappa(varld, konto_id=konto, konfig=konfig_pa(), register=reg)
    assert varld["lm"].antal("GET", "/stac") == 0  # inget hämtades


def test_saknad_nyckel_stoppar_fore_hamtning(varld: dict[str, Any]) -> None:
    def ingen(_k: str | None) -> Nycklar:
        raise NyckelFel("Kontot har ingen Lantmäteri-nyckel. Lägg in den på sidan Nycklar i webben.")

    with pytest.raises(NyckelFel, match="sidan Nycklar"):
        mappa(varld, nycklar_fran=ingen)
    assert varld["lm"].antal("GET", "/stac") == 0
    assert not (varld["rot"] / "geo" / "goteborg-test" / "v1" / "manifest.json").exists()


def test_kalla_som_inte_svarar_ger_begripligt_fel(varld: dict[str, Any]) -> None:
    falsk = Falskt(varld["kommun"])
    falsk.fel = KommunKallaFel("opengeodata.goteborg.se svarar inte (TimeoutError). Försök igen.")
    with pytest.raises(KommunKallaFel, match="svarar inte"):
        mappa(varld, wfs=falsk)
    assert not (varld["rot"] / "geo" / "goteborg-test" / "v1" / "manifest.json").exists()


def test_fel_koordinater_i_wfs_ger_fel_inte_tom_karta(varld: dict[str, Any]) -> None:
    falsk = Falskt(varld["kommun"])
    falsk.kropp = json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [[[11.9, 57.7], [11.91, 57.7], [11.91, 57.71], [11.9, 57.7]]]},
                    "properties": {},
                }
            ],
        }
    ).encode()
    with pytest.raises(KommunKallaFel, match="grader"):
        mappa(varld, wfs=falsk)


def kor_projekt_skapa(g: dict[str, Any], avgr: tuple[float, float, float, float], namn: str = "Test", **kw: Any) -> Any:
    return skapa_projekt(
        g["kommun"], ref="goteborg-test@v1", avgransning=avgr, namn=namn, konto_id=kw.pop("konto_id", None),
        geo_rot=g["rot"] / "geo", projektrot=g["rot"] / "projekt", konfig=kw.pop("konfig", {}),
        konfig_fil=g["rot"] / "config.toml", skriv=lambda _: None, **kw,
    )  # fmt: skip


def karna_bbox(g: dict[str, Any]) -> tuple[float, float, float, float]:
    d, _ = las_manifest(g["rot"] / "geo", "goteborg-test@v1")
    return tuple(d["karna_3011"])  # type: ignore[return-value]


def test_projekt_i_geografin_laser_versionen_och_varnar_utanfor_karnan(geo: dict[str, Any]) -> None:
    k = karna_bbox(geo)
    inne = (k[0] + 10, k[1] + 10, k[2] - 10, k[3] - 10)
    res = kor_projekt_skapa(geo, inne)
    p = json.loads((res.mapp / "projekt.json").read_text(encoding="utf-8"))
    assert p["geografi"] == "goteborg-test@v1" and p["geografi_manifest_sha256"] == geo["res"].manifest_sha256
    assert not [v for v in res.varningar if "giltiga kärna" in v]
    assert len(list((res.mapp / "k1_vatten").glob("mm_*/mark.tif"))) == 1
    # Delvis utanför kärnan: varning, inget fel.
    ut = (k[0] - 20, k[1] - 20, k[0] + 40, k[1] + 40)
    res2 = kor_projekt_skapa(geo, ut, namn="Kant")
    assert any("giltiga kärna" in v for v in res2.varningar)
    # Utanför geografins yttre gräns: fel (där finns ingen data).
    d, _ = las_manifest(geo["rot"] / "geo", "goteborg-test@v1")
    g = d["gransen_3011"]
    with pytest.raises(SkapaFel, match="utanför geografin"):
        kor_projekt_skapa(geo, (g[2] + 10, g[3] + 10, g[2] + 50, g[3] + 50), namn="Ute")
    # Okänd version av geografin.
    with pytest.raises(SkapaFel, match="finns inte"):
        skapa_projekt(
            geo["kommun"], ref="goteborg-test@v9", avgransning=inne, namn="X", konto_id=None,
            geo_rot=geo["rot"] / "geo", projektrot=geo["rot"] / "projekt", konfig={},
            konfig_fil=geo["rot"] / "config.toml", skriv=lambda _: None,
        )  # fmt: skip


def test_projekt_registreras_med_skaparen_som_utredare(geo: dict[str, Any]) -> None:
    reg = Register(Databas(geo["rot"] / "konto.sqlite"))
    org = reg.skapa_organisation("Test")
    konto = reg.forbered_konto("anna@example.test")
    reg.lagg_till_medlem(konto, org, None, "test")
    k = karna_bbox(geo)
    avgr = (k[0] + 10, k[1] + 10, k[2] - 10, k[3] - 10)
    with pytest.raises(SkapaFel, match="--konto"):
        kor_projekt_skapa(geo, avgr, namn="Reg", konfig=konfig_pa(), register=reg)
    res = kor_projekt_skapa(geo, avgr, namn="Reg", konto_id=konto, konfig=konfig_pa(), register=reg)
    assert res.registrerat
    rid = json.loads((res.mapp / "projekt.json").read_text(encoding="utf-8"))["register_id"]
    assert reg.projektroll(konto, rid) == "utredare"
    res2 = kor_projekt_skapa(geo, avgr, namn="Reg", konto_id=konto, konfig=konfig_pa(), register=reg)
    assert not res2.nytt
    assert json.loads((res.mapp / "projekt.json").read_text(encoding="utf-8"))["register_id"] == rid


def test_scenario_hus_diffas_mot_geografin_och_koravr_i_bada_lagena(geo: dict[str, Any]) -> None:
    from karna.utredning import las_fraga

    k = karna_bbox(geo)
    cx, cy = (k[0] + k[2]) / 2, (k[1] + k[3]) / 2
    avgr = (cx - 40, cy - 40, cx + 40, cy + 40)
    res = kor_projekt_skapa(geo, avgr, namn="Haga prov")
    sc = skapa_scenario_hus(
        res.mapp, kommun=geo["kommun"], geo_rot=geo["rot"] / "geo", namn="Nytt hus",
        centrum=(cx, cy + 25), bredd_m=12, djup_m=10, hojd_m=20, status="planerat", skriv=lambda _: None,
    )  # fmt: skip
    assert sc["status"] == "planerat" and sc["diff"] == {"tillagda": ["hus-nytt-hus"], "borttagna": [], "andrade": []}
    assert 100 < sc["hus"][0]["yta_m2"] < 150
    # Okänd status och hus utanför området avvisas med en text, inte ett stackspår.
    with pytest.raises(SkapaFel, match="status"):
        skapa_scenario_hus(res.mapp, kommun=geo["kommun"], geo_rot=geo["rot"] / "geo", namn="A", centrum=(cx, cy), bredd_m=5, djup_m=5, hojd_m=9, status="beslutat", skriv=lambda _: None)  # fmt: skip
    with pytest.raises(SkapaFel, match="utanför"):
        skapa_scenario_hus(res.mapp, kommun=geo["kommun"], geo_rot=geo["rot"] / "geo", namn="B", centrum=(cx + 5000, cy), bredd_m=5, djup_m=5, hojd_m=9, skriv=lambda _: None)  # fmt: skip
    # Frågefilerna tolkas av kärnan och har nuläget och scenariot som jämförelse.
    for namn in ("skyfall", "dagsljus"):
        f = las_fraga(res.mapp / "fragor" / f"{namn}.toml")
        assert set(f.scenarier) == {"bas", "nytt-hus"} and f.jamforelser
    # Körning: skyfall och dagsljus för nuläget och scenariot.
    kor = kor_projekt(res.mapp, rot=geo["rot"], skriv=lambda _: None)
    assert {k_: v["status"] for k_, v in kor.items()} == {"skyfall": "klar", "dagsljus": "klar"}


def test_kommunfilen_ar_konfiguration_och_stangs_strikt() -> None:
    k = las_kommun("goteborg", rot=REPO)
    assert k.publicering_epsg == 3007 and k.omrade(None).id == "haga"
    # Samma område som data_inlasning.goteborg föreslår.
    from data_inlasning.goteborg import GOTEBORG_HAGA_TESTOMRADE

    assert k.omrade("haga").bbox == GOTEBORG_HAGA_TESTOMRADE.bbox_3007
    with (REPO / "data/kommuner/goteborg.toml").open("rb") as fh:
        d = tomllib.load(fh)
    d["byggnader"]["wfs_urll"] = "x"
    with pytest.raises(Exception, match="okända nycklar"):
        tolka_kommun(d)
    d2 = tomllib.loads((REPO / "data/kommuner/goteborg.toml").read_text(encoding="utf-8"))
    d2["intern_epsg"] = 3007
    with pytest.raises(Exception, match="3011"):
        tolka_kommun(d2)
    # Dagsljusets cellstorlek ska passa markmodellens upplösning (annars faller körningen först vid körning).
    d3 = tomllib.loads((REPO / "data/kommuner/goteborg.toml").read_text(encoding="utf-8"))
    d3["lantmateriet"]["upplosning_m"] = 2.0
    d3["dagsljus"]["cellstorlek_m"] = 3
    with pytest.raises(Exception, match="heltalsmultipel"):
        tolka_kommun(d3)
