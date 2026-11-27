# ruff: noqa: E501
"""Nätverksfria enhets- och säkerhetstester för `data_inlasning.skyddad_natur` (NV WFS, Skogsstyrelsen REST, VMI)."""

from __future__ import annotations

import json
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from data_inlasning.skyddad_natur import (
    N2K_LAGER,
    NV_SKYDDSFORMER,
    SkyddadNaturFel,
    hamta_skogsstyrelsen_naturvarden,
    hamta_skyddad_natur_wfs,
    hamta_vmi_lan_zip,
)
from data_inlasning.vmi import rastrera_vmi
from gemensamt.koordinater import transformera

# Testkärna i centrala Göteborg (SWEREF 99 18 00 / EPSG:3011)
KARNA_3011 = (-210_000.0, 6_413_500.0, -209_500.0, 6_414_000.0)


def _poly_3011_till_epsg(coords_3011: list[tuple[float, float]], mal_epsg: int) -> list[list[float]]:
    ut = []
    for e, n in coords_3011:
        x, y = transformera(e, n, 3011, mal_epsg)
        ut.append([round(x, 6), round(y, 6)])
    return ut


def test_nv_wfs_geojson_omprojicerar_och_raknar_overlapp_och_avstand() -> None:
    # Polygon 1 överlappar kärnan med 100 x 100 m = 10 000 m²
    p1_3011 = [
        (-210_000.0, 6_413_500.0),
        (-209_900.0, 6_413_500.0),
        (-209_900.0, 6_413_600.0),
        (-210_000.0, 6_413_600.0),
        (-210_000.0, 6_413_500.0),
    ]
    # Polygon 2 ligger exakt 200 m öster om kärnan (-209 500 -> -209 300)
    p2_3011 = [
        (-209_300.0, 6_413_500.0),
        (-209_200.0, 6_413_500.0),
        (-209_200.0, 6_413_600.0),
        (-209_300.0, 6_413_600.0),
        (-209_300.0, 6_413_500.0),
    ]

    anropade_lager: list[str] = []

    def falsk_hamtare(url: str) -> bytes:
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        tn = qs["typeNames"][0]
        anropade_lager.append(tn)
        if tn == NV_SKYDDSFORMER["NR"][0]:
            fc = {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": "EPSG:3006"}},
                "features": [
                    {
                        "type": "Feature",
                        "id": "nr-1",
                        "properties": {
                            "nvrid": "2001234",
                            "namn": "Änggårdsbergen",
                            "beslutsdatum": "1975-06-12",
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [_poly_3011_till_epsg(p1_3011, 3006)],
                        },
                    }
                ],
            }
            return json.dumps(fc).encode("utf-8")
        if tn == N2K_LAGER:
            fc = {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": "EPSG:3006"}},
                "features": [
                    {
                        "type": "Feature",
                        "id": "n2k-1",
                        "properties": {"siteCode": "SE0520001", "siteName": "Delsjöområdet"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [_poly_3011_till_epsg(p2_3011, 3006)],
                        },
                    }
                ],
            }
            return json.dumps(fc).encode("utf-8")
        return json.dumps({"type": "FeatureCollection", "features": []}).encode("utf-8")

    res = hamta_skyddad_natur_wfs(KARNA_3011, buffert_m=1000.0, hamtare=falsk_hamtare)
    # 8 NVR-lager + 1 Natura 2000-lager = 9 anrop
    assert len(anropade_lager) == 9
    assert len(res.omraden) == 2
    assert len(res.kallor_sha256) == 9

    nr = res.omraden[0]
    assert nr.skyddsform == "NR"
    assert nr.namn == "Änggårdsbergen"
    assert nr.overlappar_karna is True
    assert nr.minsta_avstand_m == pytest.approx(0.0, abs=1e-3)
    assert nr.overlapp_karna_m2 == pytest.approx(10_000.0, rel=0.01)

    n2k = res.omraden[1]
    assert n2k.skyddsform == "Natura2000"
    assert n2k.namn == "Delsjöområdet"
    assert n2k.overlappar_karna is False
    assert n2k.overlapp_karna_m2 == 0.0
    assert n2k.minsta_avstand_m == pytest.approx(200.0, abs=1.0)


def test_nv_wfs_hanterar_gml_xml_med_nord_ost_axelordning_och_json_fallback() -> None:
    p1_3011 = [
        (-209_900.0, 6_413_600.0),
        (-209_800.0, 6_413_600.0),
        (-209_800.0, 6_413_700.0),
        (-209_900.0, 6_413_700.0),
        (-209_900.0, 6_413_600.0),
    ]
    p1_3006 = _poly_3011_till_epsg(p1_3011, 3006)
    # INSPIRE GML använder ofta (N, E) i urn:ogc:def:crs:EPSG::3006
    poslist_ne = " ".join(f"{y:.2f} {x:.2f}" for x, y in p1_3006)
    gml = f"""<?xml version="1.0" encoding="UTF-8"?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"
  xmlns:gml="http://www.opengis.net/gml/3.2"
  xmlns:ps="http://inspire.ec.europa.eu/schemas/ps/4.0"
  xmlns:gn="http://inspire.ec.europa.eu/schemas/gn/4.0"
  xmlns:base="http://inspire.ec.europa.eu/schemas/base/3.3">
  <wfs:member>
    <ps:ProtectedSite gml:id="PS.NR.99">
      <ps:inspireID><base:Identifier><base:localId>NV-GML-99</base:localId></base:Identifier></ps:inspireID>
      <ps:legalFoundationDate>1999-03-01T00:00:00Z</ps:legalFoundationDate>
      <ps:siteName><gn:GeographicalName><gn:spelling><gn:SpellingOfName><gn:text>Slottsskogen NR</gn:text></gn:SpellingOfName></gn:spelling></gn:GeographicalName></ps:siteName>
      <ps:geometry>
        <gml:MultiSurface srsName="urn:ogc:def:crs:EPSG::3006">
          <gml:surfaceMember>
            <gml:Polygon>
              <gml:exterior><gml:LinearRing><gml:posList srsDimension="2">{poslist_ne}</gml:posList></gml:LinearRing></gml:exterior>
            </gml:Polygon>
          </gml:surfaceMember>
        </gml:MultiSurface>
      </ps:geometry>
    </ps:ProtectedSite>
  </wfs:member>
</wfs:FeatureCollection>""".encode()

    exception_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/1.1">
  <ows:Exception exceptionCode="InvalidParameterValue">
    <ows:ExceptionText>outputFormat application/json not supported</ows:ExceptionText>
  </ows:Exception>
</ows:ExceptionReport>"""

    anrop = 0

    def falsk_hamtare(url: str) -> bytes:
        nonlocal anrop
        anrop += 1
        if "outputFormat=application%2Fjson" in url:
            return exception_xml
        return gml

    res = hamta_skyddad_natur_wfs(
        KARNA_3011,
        skyddsformer=("NR",),
        inkludera_natura2000=False,
        hamtare=falsk_hamtare,
    )
    assert anrop == 2  # JSON avvisades -> föll tillbaka till GML
    assert len(res.omraden) == 1
    o = res.omraden[0]
    assert o.omrade_id == "NV-GML-99"
    assert o.namn == "Slottsskogen NR"
    assert o.beslutsdatum == "1999-03-01"
    assert o.overlappar_karna is True
    assert o.overlapp_karna_m2 == pytest.approx(10_000.0, rel=0.01)


def test_nv_wfs_avvisar_xxe_och_ssrf_vardar() -> None:
    xxe = b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><wfs:FeatureCollection/>'
    with pytest.raises(SkyddadNaturFel, match="DOCTYPE|ENTITY"):
        hamta_skyddad_natur_wfs(
            KARNA_3011, skyddsformer=("NR",), inkludera_natura2000=False, hamtare=lambda _u: xxe
        )

    for ond_url in (
        "http://geodata.naturvardsverket.se/inspire/ps-nvr/ows",
        "https://evil.example.com/inspire/ps-nvr/ows",
        "https://user:pass@geodata.naturvardsverket.se/inspire/ps-nvr/ows",
    ):
        with pytest.raises(SkyddadNaturFel):
            hamta_skyddad_natur_wfs(KARNA_3011, nvr_wfs_url=ond_url, hamtare=lambda _u: b"{}")


def test_skogsstyrelsen_rest_paginering_omprojicering_och_ssrf() -> None:
    p1_wgs = _poly_3011_till_epsg(
        [
            (-209_950.0, 6_413_550.0),
            (-209_900.0, 6_413_550.0),
            (-209_900.0, 6_413_600.0),
            (-209_950.0, 6_413_600.0),
            (-209_950.0, 6_413_550.0),
        ],
        4326,
    )
    p2_wgs = _poly_3011_till_epsg(
        [
            (-209_350.0, 6_413_550.0),
            (-209_300.0, 6_413_550.0),
            (-209_300.0, 6_413_600.0),
            (-209_350.0, 6_413_600.0),
            (-209_350.0, 6_413_550.0),
        ],
        4326,
    )

    offsets: list[int] = []

    def falsk_hamtare(url: str, _headers: Any = None) -> bytes:
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        off = int(qs["resultOffset"][0])
        offsets.append(off)
        if off == 0:
            return json.dumps(
                {
                    "type": "FeatureCollection",
                    "exceededTransferLimit": True,
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {
                                "Beteckn": "N 1001-2010",
                                "Biotop1": "Ädellövskog",
                                "Fastighetsagare": "HEMLIG PERSON",
                            },
                            "geometry": {"type": "Polygon", "coordinates": [p1_wgs]},
                        }
                    ],
                }
            ).encode("utf-8")
        return json.dumps(
            {
                "type": "FeatureCollection",
                "exceededTransferLimit": False,
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"Beteckn": "N 1002-2015", "Biotop1": "Barrnaturskog"},
                        "geometry": {"type": "Polygon", "coordinates": [p2_wgs]},
                    }
                ],
            }
        ).encode("utf-8")

    res = hamta_skogsstyrelsen_naturvarden(
        KARNA_3011,
        buffert_m=1000.0,
        tjanster=(
            ("nyckelbiotop", "Nyckelbiotop", "Geodataportal/GeodataportalVisaNyckelbiotop/MapServer/0/query"),
        ),
        sidstorlek=1,
        hamtare=falsk_hamtare,
    )
    assert offsets == [0, 1]
    assert len(res.objekt) == 2
    assert res.objekt[0].objekt_id == "N 1001-2010"
    assert res.objekt[0].overlappar_karna is True
    assert res.objekt[0].overlapp_karna_m2 == pytest.approx(2500.0, rel=0.02)
    assert res.objekt[1].overlappar_karna is False
    assert res.objekt[1].minsta_avstand_m == pytest.approx(150.0, abs=2.0)
    # Inga ägarfält i utdata
    assert "HEMLIG PERSON" not in json.dumps(res.objekt[0].som_geojson_feature())

    with pytest.raises(SkyddadNaturFel):
        hamta_skogsstyrelsen_naturvarden(
            KARNA_3011,
            bas_url="https://ond.example.se/arcgis/rest/services",
            hamtare=lambda _u: b"{}",
        )


def test_vmi_lan_zip_cache_verifiering_zip_slip_och_rastrera_vmi(tmp_path: Path) -> None:
    # Skapa en riktig minimal GPKG i en ZIP som simulerar Naturvårdsverkets läns-ZIP
    gpkg_src = tmp_path / "src_vmi.gpkg"
    poly = Polygon(
        [
            (-209_900.0, 6_413_600.0),
            (-209_700.0, 6_413_600.0),
            (-209_700.0, 6_413_800.0),
            (-209_900.0, 6_413_800.0),
            (-209_900.0, 6_413_600.0),
        ]
    )
    gpd.GeoDataFrame({"objekt": ["vmi_1"]}, geometry=[poly], crs=3011).to_file(gpkg_src, driver="GPKG")

    zip_buf_fil = tmp_path / "vmi_O.zip"
    with zipfile.ZipFile(zip_buf_fil, "w") as zf:
        zf.write(gpkg_src, arcname="5_GIS_skikt/VMI_O_Goteborg_Bohus.gpkg")
    zip_bytes = zip_buf_fil.read_bytes()

    anrop_urls: list[str] = []

    def falsk_hamtare(url: str) -> bytes:
        anrop_urls.append(url)
        if url.endswith("/O_Goteborg_Bohus/"):
            return b'<html><a href="5_GIS_skikt/">5_GIS_skikt/</a></html>'
        if url.endswith("/5_GIS_skikt/"):
            return b'<html><a href="VMI_O_GIS_skikt.zip">VMI_O_GIS_skikt.zip</a></html>'
        if url.endswith("VMI_O_GIS_skikt.zip"):
            return zip_bytes
        raise AssertionError(f"Oväntad URL: {url}")

    cache = tmp_path / "cache"
    p1 = hamta_vmi_lan_zip("O_Goteborg_Bohus", cache, hamtare=falsk_hamtare)
    assert p1.fran_cache is False
    assert p1.zip_fil.is_file()
    assert p1.gis_fil is not None and p1.gis_fil.is_file()
    assert len(p1.sha256) == 64

    # Klar för befintliga data_inlasning.vmi.rastrera_vmi
    lager = rastrera_vmi(p1.gis_fil, KARNA_3011, buffert_m=100.0, cell_m=10.0)
    assert int(lager.klasser.sum()) > 0

    # Andra anropet träffar SHA-256-cachen (ZIP hämtas inte på nytt)
    n_fore = len(anrop_urls)
    p2 = hamta_vmi_lan_zip("O_Goteborg_Bohus", cache, hamtare=falsk_hamtare)
    assert p2.fran_cache is True
    assert p2.sha256 == p1.sha256
    assert not any(u.endswith(".zip") for u in anrop_urls[n_fore:])

    # Manipulerad cachefil måste stoppas
    p1.zip_fil.write_bytes(b"korrupt")
    with pytest.raises(SkyddadNaturFel, match="SHA-256"):
        hamta_vmi_lan_zip("O_Goteborg_Bohus", cache, hamtare=falsk_hamtare)

    # Zip-slip måste avvisas
    ond_zip = tmp_path / "ond.zip"
    with zipfile.ZipFile(ond_zip, "w") as zf:
        zf.writestr("../../pwned.txt", "nej")
    ond_bytes = ond_zip.read_bytes()
    with pytest.raises(SkyddadNaturFel, match="zip-slip"):
        hamta_vmi_lan_zip(
            "AB_Stockholm",
            tmp_path / "cache2",
            filnamn="VMI_AB.zip",
            hamtare=lambda _u: ond_bytes,
        )

    # Path traversal i länskatalog avvisas
    with pytest.raises(SkyddadNaturFel, match="länskatalog"):
        hamta_vmi_lan_zip("../etc", cache, hamtare=falsk_hamtare)
