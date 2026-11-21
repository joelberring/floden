"""Token, sidhantering, 401/403/429 och nyckelsäkerhet mot en falsk Lantmäteriet (inget nät)."""

from __future__ import annotations

import logging
from typing import Any

import pytest
import shapely

from lantmateriet.klient import LantmaterietFel, LmKlient, StrypningsFel
from lantmateriet.nycklar import NyckelFel, Nycklar, las_nycklar
from lantmateriet.stac import (
    ROLL_LASER,
    lista_samlingar,
    nyaste_per_ruta,
    sok,
    tolka_objekt,
    valj_samling,
    wgs84_bbox,
)
from tests.lantmateriet.conftest import Klocka
from tests.lantmateriet.falsk_server import HEMLIGHET, KLIENT_ID, FalskLm


def _feat(fid: str, bbox: list[float], dt: str, namn: str | None = None) -> dict[str, Any]:
    return {
        "type": "Feature",
        "id": fid,
        "collection": "laserdata-skog",
        "bbox": bbox,
        "properties": {"datetime": dt, "proj:epsg": 3006},
        "assets": {
            "data": {
                "href": f"http://127.0.0.1/files/{namn or fid}.laz",
                "type": "application/vnd.laszip",
                "file:size": 1234,
            }
        },
    }


BB1 = [18.0, 59.3, 18.02, 59.31]
BB2 = [18.02, 59.3, 18.04, 59.31]


# -- bbox-transform -----------------------------------------------------------------------------


def test_bbox_centralmeridian_och_kant() -> None:
    # E = 150 000 ligger exakt på SWEREF 99 18 00:s mittmeridian (18,0 grader öst).
    v, s, o, n = wgs84_bbox((150000.0, 6580000.0, 151000.0, 6581000.0))
    assert v == pytest.approx(18.0, abs=1e-9)
    # En km öster vid 59,3 grader nord är cirka 1/(111,32 * cos 59,3) = 0,0176 grader.
    assert o - v == pytest.approx(0.0176, rel=0.02)
    assert n - s == pytest.approx(1000.0 / 111_200.0, rel=0.02)
    assert 59.33 < s < 59.34  # N 6 580 000 ligger vid cirka 59,335 grader nord


def test_bbox_stockholm_mot_sweref99tm() -> None:
    # Samma punkt via två oberoende vägar: EPSG:3011 -> 4326 mot EPSG:3011 -> 3006 -> 4326.
    from pyproj import Transformer

    e, n = 151000.0, 6580000.0
    e6, n6 = Transformer.from_crs(3011, 3006, always_xy=True).transform(e, n)
    lon, lat = Transformer.from_crs(3006, 4326, always_xy=True).transform(e6, n6)
    v, s, o, nn = wgs84_bbox((e - 1, n - 1, e + 1, n + 1))
    assert (v + o) / 2 == pytest.approx(lon, abs=2e-7)
    assert (s + nn) / 2 == pytest.approx(lat, abs=2e-7)
    # Stadshuset ligger cirka vid 18,054 öst och 59,327 nord; kvarteret E 151 000 ligger öster om
    # mittmeridianen och 1,7 km söder om 59,335: rimliga gränser fångar förväxlade axlar (lat/lon).
    assert 18.0 < v < 18.06
    assert 59.3 < s < 59.4


def test_bbox_tackar_polygonen() -> None:
    poly = shapely.Polygon([(150000, 6580000), (152000, 6580000), (151000, 6582000)])
    v, s, o, n = wgs84_bbox(poly)
    # Hela polygonens hörn ligger inom bbox
    from pyproj import Transformer

    t = Transformer.from_crs(3011, 4326, always_xy=True)
    for x, y in poly.exterior.coords:
        lon, lat = t.transform(x, y)
        assert v <= lon <= o and s <= lat <= n


# -- nycklar --------------------------------------------------------------------------------------


def test_nycklar_ur_miljo_och_fil_och_saknade() -> None:
    n = las_nycklar(
        konfig={}, miljo={"LANTMATERIET_KLIENT_ID": "a", "LANTMATERIET_KLIENT_HEMLIGHET": "b"}
    )
    assert n.metod == "oauth2" and n.id == "a"
    n2 = las_nycklar(
        konfig={"lantmateriet": {"klient_id": "x", "klient_hemlighet": "y", "metod": "basic"}},
        miljo={},
    )
    assert n2.metod == "basic" and n2.hemlighet == "y"
    with pytest.raises(NyckelFel, match="config.local.toml"):
        las_nycklar(konfig={}, miljo={})
    with pytest.raises(NyckelFel):
        Nycklar(id="a", hemlighet="b", metod="ftp")


def test_nycklar_visas_aldrig_i_text() -> None:
    n = Nycklar(id=KLIENT_ID, hemlighet=HEMLIGHET)
    for text in (repr(n), str(n), f"{n}", f"{n!r}"):
        assert KLIENT_ID not in text and HEMLIGHET not in text


# -- token ----------------------------------------------------------------------------------------


def test_token_cachas_och_fornyas(lm: FalskLm, klient: LmKlient, klocka: Klocka) -> None:
    lm.token_expires_in = 100
    lista_samlingar(klient, lm.tjanster().hojd)
    lista_samlingar(klient, lm.tjanster().hojd)
    assert lm.logg.token_anrop == 1  # cachat
    klocka.t += 80  # kvar 20 s < marginalen 30 s: förnyas
    lista_samlingar(klient, lm.tjanster().hojd)
    assert lm.logg.token_anrop == 2


def test_utgangen_token_ger_ett_nytt_och_ett_forsok(lm: FalskLm, klient: LmKlient) -> None:
    lista_samlingar(klient, lm.tjanster().hojd)
    lm.logg.giltiga_tokens.clear()  # servern återkallar tokenet
    assert lista_samlingar(klient, lm.tjanster().hojd)  # klienten hämtar ett nytt och försöker igen
    assert lm.logg.token_anrop == 2


def test_fel_nycklar_ger_nyckelfel(lm: FalskLm, klocka: Klocka) -> None:
    k = LmKlient(
        Nycklar(id=KLIENT_ID, hemlighet="fel"), lm.tjanster(), sov=klocka.sov, nu=klocka.nu
    )
    with pytest.raises(NyckelFel, match="401"):
        lista_samlingar(k, lm.tjanster().hojd)


def test_403_pa_sokning_nämner_geotorget(lm: FalskLm, klient: LmKlient) -> None:
    lm.avvisa_alla = True
    with pytest.raises(NyckelFel, match="Geotorget"):
        list(
            sok(klient, lm.tjanster().hojd, (18.0, 59.3, 18.1, 59.4), "laserdata-skog", ROLL_LASER)
        )


def test_basic_metod_skickar_basic(lm: FalskLm, klocka: Klocka) -> None:
    k = LmKlient(
        Nycklar(id=KLIENT_ID, hemlighet=HEMLIGHET, metod="basic"), lm.tjanster(), sov=klocka.sov
    )
    with pytest.raises(NyckelFel):  # falska servern vill ha Bearer; poängen är rubriken
        lista_samlingar(k, lm.tjanster().hojd)
    assert lm.logg.token_anrop == 0
    assert lm.logg.begaranden[0]["auth"].startswith("Basic ")


# -- 429 ------------------------------------------------------------------------------------------


def test_429_vantar_retry_after_och_lyckas(lm: FalskLm, klient: LmKlient, klocka: Klocka) -> None:
    lm.sidor = [[_feat("a", BB1, "2023-05-01T00:00:00Z")]]
    lm.sok_status_foerst = [429, 429]
    lm.retry_after = "7"
    träffar = list(
        sok(klient, lm.tjanster().hojd, (18.0, 59.3, 18.1, 59.4), "laserdata-skog", ROLL_LASER)
    )
    assert [t.id for t in träffar] == ["a"]
    assert klocka.somn == [7.0, 7.0]


def test_429_hela_tiden_ger_strypningsfel(lm: FalskLm, klient: LmKlient, klocka: Klocka) -> None:
    lm.sidor = [[]]
    lm.sok_status_foerst = [429] * 10
    with pytest.raises(StrypningsFel, match="429"):
        list(
            sok(klient, lm.tjanster().hojd, (18.0, 59.3, 18.1, 59.4), "laserdata-skog", ROLL_LASER)
        )
    assert len(klocka.somn) == 3  # max_forsok = 3


def test_503_ger_ett_lantmaterietfel_efter_forsok(lm: FalskLm, klient: LmKlient) -> None:
    lm.sidor = [[]]
    lm.sok_status_foerst = [503] * 10
    with pytest.raises(LantmaterietFel, match="503"):
        list(
            sok(klient, lm.tjanster().hojd, (18.0, 59.3, 18.1, 59.4), "laserdata-skog", ROLL_LASER)
        )


# -- STAC -----------------------------------------------------------------------------------------


def test_sidhantering_flera_sidor_och_merge(lm: FalskLm, klient: LmKlient) -> None:
    lm.sidor = [
        [_feat("a", BB1, "2019-05-01T00:00:00Z"), _feat("b", BB2, "2019-05-01T00:00:00Z")],
        [_feat("c", BB1, "2023-05-01T00:00:00Z")],
        [_feat("d", [18.04, 59.3, 18.06, 59.31], "2023-05-01T00:00:00Z")],
    ]
    bbox = (18.0, 59.3, 18.1, 59.4)
    träffar = list(sok(klient, lm.tjanster().hojd, bbox, "laserdata-skog", ROLL_LASER))
    assert [t.id for t in träffar] == ["a", "b", "c", "d"]
    sökningar = [b for b in lm.logg.begaranden if b["path"] == "/stac-hojd/v1/search"]
    assert len(sökningar) == 3
    import json

    forsta = json.loads(sökningar[0]["kropp"])
    andra = json.loads(sökningar[1]["kropp"])
    assert forsta["bbox"] == list(bbox)  # väst, syd, öst, nord i grader (WGS 84)
    assert forsta["collections"] == ["laserdata-skog"] and forsta["limit"] == 200
    assert andra["token"] == "2" and andra["bbox"] == list(bbox)  # merge: bbox följer med
    assert nyaste_per_ruta(träffar)[0].id in {"b", "c", "d"}
    assert {t.id for t in nyaste_per_ruta(träffar)} == {"b", "c", "d"}  # c (2023) slår a (2019)


def test_samma_sida_igen_loopar_inte(lm: FalskLm, klient: LmKlient) -> None:
    lm.sidor = [[_feat("a", BB1, "2023-05-01T00:00:00Z")]]
    lm.cirkel = True  # nästa-länken pekar alltid på samma sida
    träffar = list(
        sok(klient, lm.tjanster().hojd, (18.0, 59.3, 18.1, 59.4), "laserdata-skog", ROLL_LASER)
    )
    assert len(träffar) == 2  # första sidan och sida "1" en gång; sedan stoppas loopen
    assert lm.antal("POST", "/stac-hojd/v1/search") == 2


def test_samlingsval() -> None:
    sam = [
        {"id": "markhojdmodell", "title": "Markhöjdmodell"},
        {"id": "laserdata-skog", "title": "Laserdata skog"},
    ]
    assert valj_samling(sam, ROLL_LASER) == "laserdata-skog"
    assert valj_samling(sam, ROLL_LASER, tvingad="markhojdmodell") == "markhojdmodell"
    with pytest.raises(LantmaterietFel, match="finns inte"):
        valj_samling(sam, ROLL_LASER, tvingad="nej")
    with pytest.raises(LantmaterietFel, match="markhojdmodell"):
        valj_samling(sam[:1], ROLL_LASER)
    bild = [{"id": "ortofoto-historiska", "title": ""}, {"id": "ortofoto", "title": "Ortofoto"}]
    assert valj_samling(bild, "orto") == "ortofoto"
    vek = [{"id": "byggnad-inspire", "title": ""}, {"id": "byggnad", "title": "Byggnad"}]
    assert valj_samling(vek, "byggnader") == "byggnad"


def test_tolka_objekt_kraver_las_tillgang() -> None:
    f = _feat("x", BB1, "2023-05-01T00:00:00Z")
    o = tolka_objekt(f, ROLL_LASER, "s")
    assert o is not None and o.epsg == 3006 and o.storlek == 1234
    f["assets"] = {"preview": {"href": "http://x/y.png", "type": "image/png"}}
    assert tolka_objekt(f, ROLL_LASER, "s") is None


# -- nyckelsäkerhet ----------------------------------------------------------------


def test_nycklar_hamnar_aldrig_i_loggar_eller_fel(
    lm: FalskLm, klocka: Klocka, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    n = Nycklar(id=KLIENT_ID, hemlighet=HEMLIGHET)
    k = LmKlient(n, lm.tjanster(), sov=klocka.sov, nu=klocka.nu, max_forsok=1)
    lm.sidor = [[_feat("a", BB1, "2023-05-01T00:00:00Z")]]
    lm.sok_status_foerst = [429, 429, 429]
    meddelanden: list[str] = []
    try:
        list(sok(k, lm.tjanster().hojd, (18.0, 59.3, 18.1, 59.4), "laserdata-skog", ROLL_LASER))
    except LantmaterietFel as exc:
        meddelanden.append(str(exc))
    lm.avvisa_alla = True
    try:
        lista_samlingar(k, lm.tjanster().hojd)
    except NyckelFel as exc:
        meddelanden.append(str(exc))
    text = "\n".join(meddelanden) + "\n" + caplog.text
    for hemlig in n.hemligheter():
        assert hemlig not in text
