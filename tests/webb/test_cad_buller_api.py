# ruff: noqa: E501, F811
"""Deterministiska API-, fysik-, RBAC-, B11- och ASGI-tester för CAD/BIM (U17) & Buller-skiss (U13)."""

from __future__ import annotations

import base64
import json
import struct
import urllib.parse
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pytest
from PIL import Image
from shapely.geometry import LineString, Point, Polygon

from buller.losare_k21 import NORD2000_KONFORM
from gemensamt.hash import sha256_fil
from geografi.modell import KONTRAKT_GEOGRAFI, lagra, skriv_manifest
from karna.geometri.rasterfil import ruta_fran_geo
from karna.scenario import Andring, Planscenario, skriv_scenario
from lapp.cad_bim import (
    K50Brytlinje,
    K50Byggnad,
    K50Markyta,
    K50Modell,
    K50Trad,
    exportera_k50_till_dxf,
    exportera_k50_till_gpkg,
    importera_dxf_till_k50,
    importera_gpkg_till_k50,
)
from lapp.landxml import las_landxml_tin
from markmodell.byggnadsregister import ByggnadsPost, skriv_byggnader_gpkg
from markmodell.hinder_oppningar import brann_in_byggnader
from tests.konto.conftest import ANNA, ORIGIN, Miljo, Webblasare
from tests.konto.conftest import miljo as konto_miljo  # noqa: F401
from testsvit.k1_io import GeoTransformInfo, skriv_geotiff
from webb.api import FlodenApiApp
from webb.asgi import FlodenAsgiApp
from webb.cad_buller_api import MAX_CAD_IMPORT_BYTES
from webb.fragor import LAGER_VERSION
from webb.projektkalla import KONTRAKT_PROJEKT
from webb.resultatlager import (
    MASKADE_ROLLER,
    PALETTER,
    etikett_och_enhet,
    farg_for_roll,
    takkalla_for_roll,
)

E0 = 150000.0
N0 = 6580000.0

BO = {"sub": "sub-bo-v8", "email": "bo@example.test", "email_verified": True, "name": "Bo Läsare"}
DI = {"sub": "sub-di-v8", "email": "di@example.test", "email_verified": True, "name": "Di Utomstående"}


def _skapa_k1_v2_ruta_med_hus(
    mapp: Path,
    *,
    sida_m: float = 60.0,
    pixel_m: float = 5.0,
) -> tuple[Path, str]:
    """Lokal, snabb K1 v2-ruta (60×60 m, 5 m celler) med ett hus i östra halvan."""
    mapp.mkdir(parents=True, exist_ok=True)
    n = round(sida_m / pixel_m)
    x_min = E0
    y_max = N0 + sida_m
    terrang = np.full((n, n), 10.0, dtype=np.float32)
    markslag = np.full((n, n), 4, dtype=np.uint8)
    osak = np.full((n, n), 0.05, dtype=np.float32)

    hus = ByggnadsPost(
        byggnad_id="b_v8_hus_1",
        polygon=(
            (E0 + 35.0, N0 + 20.0),
            (E0 + 50.0, N0 + 20.0),
            (E0 + 50.0, N0 + 40.0),
            (E0 + 35.0, N0 + 40.0),
        ),
        hal=(),
        takhojd_rh2000=22.0,
        markhojd_rh2000=10.0,
        hojd_m=12.0,
        kalla="test",
    )
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
    mark, markslag = brann_in_byggnader(
        terrang,
        markslag,
        ruta_fran_geo(geo),
        [hus.till_byggnad()],
    )
    skriv_geotiff(mapp / "terrang.tif", terrang, x_min=x_min, y_max=y_max, pixelstorlek=pixel_m)
    skriv_geotiff(
        mapp / "mark.tif",
        np.asarray(mark, dtype=np.float32),
        x_min=x_min,
        y_max=y_max,
        pixelstorlek=pixel_m,
    )
    skriv_geotiff(mapp / "markslag.tif", markslag, x_min=x_min, y_max=y_max, pixelstorlek=pixel_m)
    skriv_geotiff(mapp / "osakerhet.tif", osak, x_min=x_min, y_max=y_max, pixelstorlek=pixel_m)
    skriv_byggnader_gpkg(
        mapp / "byggnader.gpkg",
        (hus,),
        bbox_fallback=(x_min, N0, x_min + sida_m, y_max),
    )
    recept = {
        "kontrakt": "K1/v2",
        "ruta_id": "mm_150000_6580000",
        "epsg": 3011,
        "hojdsystem": "RH 2000",
        "installningar": {"k1_version": 2, "upplosning_m": pixel_m},
    }
    rec_fil = mapp / "recept.json"
    rec_fil.write_text(json.dumps(recept, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return mapp, sha256_fil(rec_fil)


class _SyntetiskGeoV8:
    """Lokal syntetisk geografi + registrerat projekt med Anna (utredare), Bo (lasare), Di (outsider)."""

    def __init__(self, rot: Path, miljo: Miljo) -> None:
        self.rot = rot
        self.miljo = miljo
        self.reg = miljo.register
        self.geo_rot = rot / "geo"
        self.proj_rot = rot / "projekt"

        k1_tmp = rot / "bygge_k1"
        ruta_tmp = k1_tmp / "mm_150000_6580000"
        _skapa_k1_v2_ruta_med_hus(ruta_tmp, sida_m=60.0, pixel_m=5.0)
        self.x0, self.y0 = E0, N0
        self.x1, self.y1 = E0 + 60.0, N0 + 60.0

        k1_lager = lagra(self.geo_rot, k1_tmp)
        _, manifest_sha = skriv_manifest(
            self.geo_rot,
            {
                "kontrakt": KONTRAKT_GEOGRAFI,
                "id": "syntgeo-v8",
                "version": "v1",
                "kommun": "stockholm",
                "karna_3011": [self.x0, self.y0, self.x1, self.y1],
                "gransen_3011": [self.x0, self.y0, self.x1, self.y1],
                "lager": {"k1_rutnat": {"sha256": k1_lager.sha256}},
                "kallor": {
                    "hojd": {"licens": "CC0-1.0", "kalla": "Syntetisk"},
                    "byggnader": {"licens": "CC0-1.0", "kalla": "Syntetisk"},
                },
                "oppen_for_mcp": True,
            },
        )

        org1 = self.reg.skapa_organisation("Exempelkommun")
        org2 = self.reg.skapa_organisation("Grannkommun")
        anna = self.reg.forbered_konto("anna@example.test")
        self.reg.gor_organisationsadmin(anna, org1)
        bo = self.reg.forbered_konto("bo@example.test")
        self.reg.lagg_till_medlem(bo, org1, None, "test")
        di = self.reg.forbered_konto("di@example.test")
        self.reg.gor_organisationsadmin(di, org2)

        self.projekt_id, _ = self.reg.registrera_projekt_for_konto(anna, org1, "Kvarteret V8")
        self.reg.bjud_in(anna, self.projekt_id, "bo@example.test", "lasare")

        self.pmapp = self.proj_rot / "kvarteret-v8"
        k1_vatten = self.pmapp / "k1_vatten"
        v_ruta, rec_sha = _skapa_k1_v2_ruta_med_hus(
            k1_vatten / "mm_150000_6580000", sida_m=60.0, pixel_m=5.0
        )
        (self.pmapp / "oppenhet.toml").write_text(
            'kontrakt = "floden-oppenhet/v1"\noppen = true\nlicens = "CC0-1.0"\n',
            encoding="utf-8",
        )
        (self.pmapp / "projekt.json").write_text(
            json.dumps(
                {
                    "kontrakt": KONTRAKT_PROJEKT,
                    "projekt_id": "kvarteret-v8",
                    "register_id": self.projekt_id,
                    "namn": "Kvarteret V8",
                    "kommun": "stockholm",
                    "geografi": "syntgeo-v8@v1",
                    "geografi_manifest_sha256": manifest_sha,
                    "avgransning_3011": [self.x0, self.y0, self.x1, self.y1],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        # Scenario 1: 4 m bullerskärm (K9 brytlinje_ny mur) mellan väg (E+4) och mottagare/hus
        k9_skarm = self.pmapp / "k9" / "bullerskarm_vatten.json"
        skriv_scenario(
            Planscenario(
                "bullerskarm",
                (rec_sha,),
                (
                    Andring.fran_dict(
                        {
                            "typ": "brytlinje_ny",
                            "andring_id": "mur_skarm_1",
                            "geometri": {
                                "type": "LineString",
                                "coordinates": [
                                    [E0 + 14.0, N0 + 6.0],
                                    [E0 + 14.0, N0 + 54.0],
                                ],
                            },
                            "egenskaper": {"typ": "mur", "hojd_m": 4.0},
                        }
                    ),
                ),
                beskrivning="4 m bullerskärm mot väg i väster",
            ),
            k9_skarm,
        )

        # Scenario 2: nytt hus för CAD-export med scenario
        k9_hus = self.pmapp / "k9" / "extra-hus_vatten.json"
        skriv_scenario(
            Planscenario(
                "extra-hus",
                (rec_sha,),
                (
                    Andring.fran_dict(
                        {
                            "typ": "byggnad_ny",
                            "andring_id": "b_v8_extra",
                            "geometri": {
                                "type": "Polygon",
                                "coordinates": [
                                    [
                                        [E0 + 15.0, N0 + 10.0],
                                        [E0 + 25.0, N0 + 10.0],
                                        [E0 + 25.0, N0 + 20.0],
                                        [E0 + 15.0, N0 + 20.0],
                                        [E0 + 15.0, N0 + 10.0],
                                    ]
                                ],
                            },
                            "egenskaper": {"hojd_m": 15.0},
                        }
                    ),
                ),
                beskrivning="Extra byggnad för CAD-export",
            ),
            k9_hus,
        )

        (self.pmapp / "scenarier.json").write_text(
            json.dumps(
                {
                    "kontrakt": "floden-scenarier/v1",
                    "scenarier": [
                        {
                            "slug": "bullerskarm",
                            "namn": "Bullerskärm 4 m",
                            "status": "forslag",
                            "k9_vatten": "k9/bullerskarm_vatten.json",
                        },
                        {
                            "slug": "extra-hus",
                            "namn": "Extra hus",
                            "status": "forslag",
                            "k9_vatten": "k9/extra-hus_vatten.json",
                        },
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        fdir = self.pmapp / "fragor"
        fdir.mkdir(parents=True, exist_ok=True)
        (fdir / "skyfall.toml").write_text(
            'kontrakt = "K20/v1"\n'
            'fraga_id = "kvarteret-v8-skyfall"\n'
            'beskrivning = "Skyfall Kvarteret V8"\n\n'
            '[scenarier.bas]\nbas = "../k1_vatten"\n\n'
            '[[analyser]]\nid = "skyfall"\nlosare = "vatten.statisk_fyllning"\n'
            "parametrar = { regn_mm = 40.0 }\n",
            encoding="utf-8",
        )

        self.webb: dict[str, Webblasare] = {}
        for namn, claims in (("anna", ANNA), ("bo", BO), ("di", DI)):
            w = miljo.webblasare()
            assert w.logga_in(miljo.idp, claims)[0] == 302
            self.webb[namn] = w

        self.konfig = rot / "config.toml"
        self.konfig.write_text(
            '[webb]\nfragemappar = ["fragor"]\ncachemapp = "cache"\nkorningsmapp = "korningar"\n',
            encoding="utf-8",
        )
        tom_testsvit = rot / "tom-testsvit"
        tom_testsvit.mkdir(exist_ok=True)
        self.app = FlodenApiApp(
            testsvit_rot=tom_testsvit,
            konfig_fil=self.konfig,
            konto=miljo.adapter,
            geomapp=self.geo_rot,
            projektmapp=self.proj_rot,
            kartverktygsmapp=rot / "kartverktyg",
        )
        assert v_ruta.is_dir()

    def http_raw(
        self,
        vem: str | None,
        metod: str,
        sokvag: str,
        kropp: bytes | Mapping[str, Any] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        rub: dict[str, str] = {}
        if vem is not None:
            w = self.webb[vem]
            rub["Cookie"] = "; ".join(f"{k}={v}" for k, v in w.kakor.items())
            if metod != "GET":
                rub["Origin"] = ORIGIN
                rub.update(w.csrf_headers())
        if kropp is None:
            data = b""
        elif isinstance(kropp, bytes):
            data = kropp
        else:
            data = json.dumps(kropp).encode("utf-8")
        status, headers, body = self.app.hantera_http(metod, sokvag, rub, lambda: data)
        return status, {k.lower(): v for k, v in headers}, body

    def http(
        self,
        vem: str | None,
        metod: str,
        sokvag: str,
        kropp: bytes | Mapping[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        status, _, body = self.http_raw(vem, metod, sokvag, kropp)
        return status, json.loads(body.decode("utf-8")) if body else {}

    def projektfiler_snapshot(self) -> dict[str, tuple[int, int]]:
        ut: dict[str, tuple[int, int]] = {}
        for p in sorted(self.pmapp.rglob("*")):
            if p.is_file():
                st = p.stat()
                ut[p.relative_to(self.pmapp).as_posix()] = (st.st_size, st.st_mtime_ns)
        return ut


def _exempel_ifc_mm_text() -> str:
    """Minimal giltig ISO-10303-21 IFC-fil i millimeter med en 20×10×14 m byggnadsvolym."""
    return (
        "ISO-10303-21;\n"
        "HEADER;\n"
        "FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');\n"
        "FILE_NAME('kvarter.ifc','2026-11-26T12:00:00',('Floden'),('Floden'),'','','');\n"
        "FILE_SCHEMA(('IFC4'));\n"
        "ENDSEC;\n"
        "DATA;\n"
        "#1=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);\n"
        "#2=IFCUNITASSIGNMENT((#1));\n"
        "#3=IFCPROJECT('0Proj',$,'Test',$,$,$,$,$,#2);\n"
        "#10=IFCCARTESIANPOINT((10000.,5000.,2000.));\n"
        "#11=IFCAXIS2PLACEMENT3D(#10,$,$);\n"
        "#12=IFCLOCALPLACEMENT($,#11);\n"
        "#20=IFCRECTANGLEPROFILEDEF(.AREA.,'Rect',$,20000.,10000.);\n"
        "#21=IFCDIRECTION((0.,0.,1.));\n"
        "#22=IFCEXTRUDEDAREASOLID(#20,$,#21,14000.);\n"
        "#23=IFCSHAPEREPRESENTATION($,'Body','SweptSolid',(#22));\n"
        "#24=IFCPRODUCTDEFINITIONSHAPE($,$,(#23));\n"
        "#25=IFCBUILDING('1Bldg',$,'Kvarteret_IFC',$,$,#12,#24,$,$,$,$,$);\n"
        "ENDSEC;\n"
        "END-ISO-10303-21;\n"
    )


# ------------------------------------------------------------------ 1. CAD/BIM Import (DXF, IFC, GPKG, 400/413/RBAC)


def test_cad_import_dxf_ifc_gpkg_validering_och_rbac(
    tmp_path: Path, konto_miljo: Miljo
) -> None:
    geo = _SyntetiskGeoV8(tmp_path, konto_miljo)
    pid = geo.projekt_id
    import_url = f"/api/projekt/{pid}/cad/importera"

    # Bygg riktig DXF (med byggnad, 3D-brytlinje, träd + en ej stödd SPLINE)
    modell = K50Modell(
        byggnader=(
            K50Byggnad(
                objekt_id="dxf_hus_1",
                fotavtryck=Polygon(
                    [
                        (E0 + 10.0, N0 + 10.0),
                        (E0 + 22.0, N0 + 10.0),
                        (E0 + 22.0, N0 + 20.0),
                        (E0 + 10.0, N0 + 20.0),
                    ]
                ),
                byggnadshojd_m=11.5,
                z_bas_rh2000_m=10.2,
                lager_namn="byggnader",
            ),
        ),
        markytor=(
            K50Markyta(
                objekt_id="gard_1",
                geometri=Polygon(
                    [
                        (E0 + 25.0, N0 + 10.0),
                        (E0 + 35.0, N0 + 10.0),
                        (E0 + 35.0, N0 + 20.0),
                        (E0 + 25.0, N0 + 20.0),
                    ]
                ),
                niva_rh2000_m=9.8,
                roll="mark",
                lager_namn="markytor",
            ),
        ),
        brytlinjer=(
            K50Brytlinje(
                objekt_id="kant_1",
                geometri=LineString([(E0 + 5.0, N0 + 5.0, 10.1), (E0 + 55.0, N0 + 5.0, 10.6)]),
                typ="kantsten",
                lager_namn="brytlinjer",
            ),
        ),
        trad=(K50Trad(objekt_id="trad_1", position=Point(E0 + 30.0, N0 + 30.0, 10.0)),),
    )
    dxf_fil = exportera_k50_till_dxf(modell, tmp_path / "exempel.dxf")
    # Infoga en ej stödd SPLINE före ENDSEC/EOF för att verifiera ohamtade_entiteter
    dxf_text = dxf_fil.read_text(encoding="utf-8").replace(
        "0\nENDSEC\n0\nEOF",
        "0\nSPLINE\n8\n0\n0\nENDSEC\n0\nEOF",
    )

    # 1. RBAC: oinloggad 401, lasare (bo) 403, outsider (di) 403, okänt projekt 404
    st_401, _, _ = geo.http_raw(None, "POST", import_url, {"filnamn": "a.dxf", "innehall_text": dxf_text})
    assert st_401 == 401
    st_bo, _ = geo.http("bo", "POST", import_url, {"filnamn": "a.dxf", "innehall_text": dxf_text})
    assert st_bo == 403
    st_di, _ = geo.http("di", "POST", import_url, {"filnamn": "a.dxf", "innehall_text": dxf_text})
    assert st_di == 403
    st_404, _ = geo.http(
        "anna",
        "POST",
        "/api/projekt/okant-projekt-404/cad/importera",
        {"filnamn": "a.dxf", "innehall_text": dxf_text},
    )
    assert st_404 == 404

    # 2. DXF-import (base64 + utredare Anna -> 200)
    st_dxf, dxf_svar = geo.http(
        "anna",
        "POST",
        import_url,
        {
            "filnamn": "kvarter.dxf",
            "innehall_base64": base64.b64encode(dxf_text.encode("utf-8")).decode("ascii"),
        },
    )
    assert st_dxf == 200, dxf_svar
    assert dxf_svar["koordinatsystem"] == "EPSG:3011"
    assert dxf_svar["antal_objekt"] == 4
    assert dxf_svar["antal_per_typ"]["byggnader"] == 1
    assert dxf_svar["antal_per_typ"]["brytlinjer"] == 1
    assert dxf_svar["ohamtade_entiteter"].get("SPLINE") == 1
    assert any("SPLINE" in v for v in dxf_svar["varningar"])
    assert dxf_svar["geojson"]["type"] == "FeatureCollection"
    assert len(dxf_svar["features"]) == 4
    assert dxf_svar["ritlager"]["kontrakt"] == "floden-ritlager/v1"

    # Rå uppladdning med ?filnamn=... fungerar också
    st_ra, ra_svar = geo.http(
        "anna",
        "POST",
        f"{import_url}?filnamn=ra_upload.dxf",
        dxf_text.encode("utf-8"),
    )
    assert st_ra == 200, ra_svar
    assert ra_svar["antal_objekt"] == 4

    # 3. IFC-import med lokalt ursprung (mm -> m + translation till EPSG:3011 / RH 2000)
    st_ifc, ifc_svar = geo.http(
        "anna",
        "POST",
        import_url,
        {
            "filnamn": "volym.ifc",
            "innehall_base64": base64.b64encode(_exempel_ifc_mm_text().encode("utf-8")).decode("ascii"),
            "ursprung_e_m": E0,
            "ursprung_n_m": N0,
            "ursprung_z_m": 10.0,
        },
    )
    assert st_ifc == 200, ifc_svar
    assert ifc_svar["antal_per_typ"]["byggnader"] == 1
    assert ifc_svar["metadata"]["langdenhet"] == "mm"
    b_feat = ifc_svar["features"][0]
    assert b_feat["properties"]["roll"] == "byggnad"
    assert b_feat["properties"]["byggnadshojd_m"] == pytest.approx(14.0)
    assert b_feat["properties"]["z_bas_rh2000_m"] == pytest.approx(12.0)
    xs = [c[0] for c in b_feat["geometry"]["coordinates"][0]]
    ys = [c[1] for c in b_feat["geometry"]["coordinates"][0]]
    assert min(xs) == pytest.approx(E0) and max(xs) == pytest.approx(E0 + 20.0)
    assert min(ys) == pytest.approx(N0) and max(ys) == pytest.approx(N0 + 10.0)

    # 4. GPKG-import (EPSG:3006 -> automatisk reprojektion till EPSG:3011)
    gpkg_3006 = exportera_k50_till_gpkg(modell, tmp_path / "k50_3006.gpkg", crs="EPSG:3006")
    st_gpkg, gpkg_svar = geo.http(
        "anna",
        "POST",
        import_url,
        {
            "filnamn": "leverans.gpkg",
            "innehall_base64": base64.b64encode(gpkg_3006.read_bytes()).decode("ascii"),
        },
    )
    assert st_gpkg == 200, gpkg_svar
    assert gpkg_svar["kalla_crs"] == "EPSG:3006"
    assert gpkg_svar["koordinatsystem"] == "EPSG:3011"
    assert gpkg_svar["antal_objekt"] == 4

    # 5. GPKG utan CRS -> alltid tydlig HTTP 400 (aldrig gissning!)
    gpkg_utan_crs = tmp_path / "utan_crs.gpkg"
    gdf_no_crs = gpd.GeoDataFrame(
        {"objekt_id": ["x1"], "byggnadshojd_m": [8.0]},
        geometry=[Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])],
        crs=None,
    )
    gdf_no_crs.to_file(gpkg_utan_crs, layer="byggnader", driver="GPKG")
    st_nocrs, fel_nocrs = geo.http(
        "anna",
        "POST",
        import_url,
        {
            "filnamn": "utan_crs.gpkg",
            "innehall_base64": base64.b64encode(gpkg_utan_crs.read_bytes()).decode("ascii"),
        },
    )
    assert st_nocrs == 400, fel_nocrs
    assert "CRS" in fel_nocrs["fel"]

    # 5b. LandXML utan <CoordinateSystem> -> tydlig HTTP 400 (aldrig gissat CRS!)
    xml_utan_crs = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<LandXML version="1.2"><Surfaces><Surface name="t"><Definition surfType="TIN">'
        "<Pnts><P id='1'>0 0 1</P><P id='2'>10 0 1</P><P id='3'>0 10 1</P></Pnts>"
        "<Faces><F>1 2 3</F></Faces></Definition></Surface></Surfaces></LandXML>"
    )
    st_xml_nocrs, fel_xml_nocrs = geo.http(
        "anna",
        "POST",
        import_url,
        {"filnamn": "utan_crs.xml", "innehall_text": xml_utan_crs},
    )
    assert st_xml_nocrs == 400, fel_xml_nocrs
    assert "CoordinateSystem" in fel_xml_nocrs["fel"]

    # 6. Ogiltigt format, dålig base64 och storleksgräns (> 25 MB -> 413)
    assert geo.http("anna", "POST", import_url, {"filnamn": "modell.dwg", "innehall_text": "abc"})[0] == 400
    assert geo.http("anna", "POST", import_url, {"filnamn": "plan.dxf", "innehall_base64": "!!!inte-b64!!!"})[0] == 400
    over_grans = b"0" * (MAX_CAD_IMPORT_BYTES + 64)
    st_413, _, _ = geo.http_raw("anna", "POST", f"{import_url}?filnamn=stor.dxf", over_grans)
    assert st_413 == 413


# ------------------------------------------------------------------ 2. CAD/BIM Export (alla 5 format + GLB-magic + RBAC)


def test_cad_export_alla_fem_format_glb_magic_scenario_och_rbac(
    tmp_path: Path, konto_miljo: Miljo
) -> None:
    geo = _SyntetiskGeoV8(tmp_path, konto_miljo)
    pid = geo.projekt_id
    fore_filer = geo.projektfiler_snapshot()

    # RBAC: oinloggad 401, outsider 403 (även blankstegspaddad), okänt projekt 404, lasare (bo) 200
    assert geo.http_raw(None, "GET", f"/api/projekt/{pid}/cad/exportera?format=gpkg")[0] == 401
    assert geo.http_raw("di", "GET", f"/api/projekt/{pid}/cad/exportera?format=gpkg")[0] == 403
    assert (
        geo.http_raw(
            "di", "GET", f"/api/projekt/{urllib.parse.quote(' ' + pid + ' ')}/cad/exportera?format=gpkg"
        )[0]
        == 403
    )
    assert geo.http_raw("bo", "GET", "/api/projekt/okant-projekt-404/cad/exportera?format=gpkg")[0] == 404
    assert geo.http_raw("bo", "GET", f"/api/projekt/{pid}/cad/exportera?format=stl")[0] == 400
    assert (
        geo.http_raw("bo", "GET", f"/api/projekt/{pid}/cad/exportera?format=gpkg&scenario=finns-inte")[0]
        == 404
    )

    # 1. GPKG (lasare Bo får 200)
    st_g, hdr_g, data_g = geo.http_raw("bo", "GET", f"/api/projekt/{pid}/cad/exportera?format=gpkg")
    assert st_g == 200
    assert "geopackage" in hdr_g["content-type"]
    assert ".gpkg" in hdr_g["content-disposition"]
    assert data_g.startswith(b"SQLite format 3\x00")
    g_fil = tmp_path / "ut.gpkg"
    g_fil.write_bytes(data_g)
    m_gpkg = importera_gpkg_till_k50(g_fil)
    assert m_gpkg.epsg == 3011 and len(m_gpkg.byggnader) == 1

    # 2. DXF (med scenario `extra-hus` -> 2 byggnader!)
    st_d, hdr_d, data_d = geo.http_raw(
        "bo", "GET", f"/api/projekt/{pid}/cad/exportera?format=dxf&scenario=extra-hus"
    )
    assert st_d == 200
    assert "dxf" in hdr_d["content-type"]
    assert ".dxf" in hdr_d["content-disposition"]
    dxf_in = importera_dxf_till_k50(data_d.decode("utf-8"))
    assert len(dxf_in.modell.byggnader) == 2

    # 3. LandXML 1.2 (.xml) -> läsbar med las_landxml_tin OCH rundresa via CAD-import!
    st_x, hdr_x, data_x = geo.http_raw("bo", "GET", f"/api/projekt/{pid}/cad/exportera?format=landxml")
    assert st_x == 200
    assert "xml" in hdr_x["content-type"]
    assert ".xml" in hdr_x["content-disposition"]
    yta = las_landxml_tin(data_x.decode("utf-8"))
    assert yta.antal_punkter > 0 and yta.antal_trianglar > 0
    st_imp_xml, imp_xml = geo.http(
        "anna",
        "POST",
        f"/api/projekt/{pid}/cad/importera",
        {"filnamn": "mark.xml", "innehall_text": data_x.decode("utf-8")},
    )
    assert st_imp_xml == 200, imp_xml
    assert imp_xml["antal_per_typ"]["markytor"] >= 1

    # 4. Wavefront OBJ (.obj)
    st_o, hdr_o, data_o = geo.http_raw("bo", "GET", f"/api/projekt/{pid}/cad/exportera?format=obj")
    assert st_o == 200
    assert "model/obj" in hdr_o["content-type"]
    assert ".obj" in hdr_o["content-disposition"]
    obj_txt = data_o.decode("utf-8")
    assert "EPSG:3011" in obj_txt and "\no terrang\n" in obj_txt and "\no byggnader\n" in obj_txt

    # 5. Binär glTF 2.0 (.glb) med strikt `glTF`-magic & JSON-header-kontroll
    st_b, hdr_b, data_b = geo.http_raw("bo", "GET", f"/api/projekt/{pid}/cad/exportera?format=glb")
    assert st_b == 200
    assert hdr_b["content-type"] == "model/gltf-binary"
    assert ".glb" in hdr_b["content-disposition"]
    assert data_b[:4] == b"glTF"
    magic, version, total_len = struct.unpack("<III", data_b[:12])
    assert magic == 0x46546C67 and version == 2 and total_len == len(data_b)
    json_len, json_type = struct.unpack("<II", data_b[12:20])
    assert json_type == 0x4E4F534A
    gltf_doc = json.loads(data_b[20 : 20 + json_len].decode("utf-8"))
    assert gltf_doc["asset"]["version"] == "2.0"
    assert gltf_doc["asset"]["extras"]["epsg"] == 3011

    # Projektmappen på disk är helt oförändrad efter alla exporter
    assert geo.projektfiler_snapshot() == fore_filer


# ------------------------------------------------------------------ 3. Buller-skissanalys (U13) + skärmdämpning + PNG + RBAC


def test_buller_skiss_k21_skarmdampning_takmaskad_png_validering_och_rbac(
    tmp_path: Path, konto_miljo: Miljo
) -> None:
    geo = _SyntetiskGeoV8(tmp_path, konto_miljo)
    pid = geo.projekt_id
    fore_filer = geo.projektfiler_snapshot()

    vag = [
        {
            "id": "vag_vast",
            "koordinater": [[E0 + 4.0, N0 + 4.0], [E0 + 4.0, N0 + 56.0]],
            "adt_fordon_dygn": 10000.0,
            "hastighet_km_h": 50.0,
            "andel_tung_pct": 10.0,
        }
    ]
    bas_payload = {
        "omrade_id": pid,
        "vagsegment": vag,
        "mottagarhojd_m": 2.0,
        "raster_cell_m": 10.0,
        "segment_langd_m": 20.0,
        "fasad_steg_m": 15.0,
        "max_vaningar": 2,
        "reflexordning": 0,
    }

    # 1. RBAC: oinloggad 401, lasare (bo) 403 på POST, outsider (di) 403 (även med blanksteg/projekt_id), okänt projekt 404
    assert geo.http(None, "POST", "/api/analys/buller", bas_payload)[0] == 401
    assert geo.http("bo", "POST", "/api/analys/buller", bas_payload)[0] == 403
    assert geo.http("di", "POST", "/api/analys/buller", bas_payload)[0] == 403
    assert geo.http("di", "POST", "/api/analys/buller", {**bas_payload, "omrade_id": f" {pid} "})[0] == 403
    assert (
        geo.http("di", "POST", "/api/analys/buller", {"projekt_id": pid, "vagsegment": vag})[0] == 403
    )
    assert geo.http("anna", "POST", "/api/analys/buller", {**bas_payload, "omrade_id": "okant-404"})[0] == 404

    # 2. Saknad/ogiltig trafik eller försök till rapportläge -> tydlig HTTP 400 (aldrig påhittad trafik!)
    assert geo.http("anna", "POST", "/api/analys/buller", {"omrade_id": pid})[0] == 400
    assert geo.http("anna", "POST", "/api/analys/buller", {"omrade_id": pid, "vagsegment": []})[0] == 400
    assert (
        geo.http(
            "anna",
            "POST",
            "/api/analys/buller",
            {
                "omrade_id": pid,
                "vagsegment": [
                    {
                        "koordinater": [[E0 + 4.0, N0 + 4.0], [E0 + 4.0, N0 + 56.0]],
                        "adt_fordon_dygn": -50.0,
                        "hastighet_km_h": 50.0,
                        "andel_tung_pct": 10.0,
                    }
                ],
            },
        )[0]
        == 400
    )
    assert geo.http("anna", "POST", "/api/analys/buller", {**bas_payload, "lage": "rapport"})[0] == 400

    # 3. Verklig buller-skissanalys med före/efter 4 m bullerskärm (K9 scenario `bullerskarm`)
    st_ok, svar = geo.http(
        "anna",
        "POST",
        "/api/analys/buller",
        {**bas_payload, "scenario": "bullerskarm"},
    )
    assert st_ok == 200, svar
    assert NORD2000_KONFORM is False
    assert svar["nord2000_konform"] is False
    assert svar["metodstatus"] == "skiss"
    assert svar["rapport_sparrad"] is True
    assert svar["sammanfattning"]["nord2000_konform"] is False
    assert svar["sammanfattning"]["metodstatus"] == "skiss"
    assert svar["sammanfattning"]["rapport_sparrad"] is True
    assert svar["fore"]["laeq_max_dba"] >= svar["fore"]["laeq_medel_dba"] > 45.0
    assert svar["efter"] is not None
    assert svar["efter"]["antal_bullerskarmar"] == 1
    assert svar["jamforelse"] is not None
    assert svar["jamforelse"]["max_dampning_dba"] > 5.0
    assert svar["jamforelse"]["delta_laeq_medel_dba"] < -1.0
    assert svar["fasad_buller"]["type"] == "FeatureCollection"
    assert len(svar["fasad_buller"]["features"]) > 0
    assert svar["fasad_buller"]["properties"]["nord2000_konform"] is False

    # 4. Takmaskad raster-PNG för `ekvivalent_ljudniva_dba` + PNG-RBAC (`lasare` 200, outsider 403, okänt projekt 404)
    for nyckel in (
        "ekvivalent_ljudniva_dba_fore",
        "ekvivalent_ljudniva_dba_efter",
        "ekvivalent_ljudniva_dba_skillnad",
    ):
        lag = svar["lager"][nyckel]
        assert lag is not None
        assert lag["roll"] == "ekvivalent_ljudniva_dba"
        assert lag["enhet"] == "dB(A)"
        assert lag["tak_maskat"] is True
        png_url = lag["png"]
        assert png_url.startswith(f"/api/kartverktyg/{pid}/buller/")

        # `lasare` (Bo) får 200 och verifierat takmaskad PNG
        st_png_bo, hdr_png, png_b = geo.http_raw("bo", "GET", png_url)
        assert st_png_bo == 200
        assert png_b.startswith(b"\x89PNG\r\n\x1a\n")
        assert "x-floden-utbredning" in hdr_png
        rgba = np.asarray(Image.open(BytesIO(png_b)).convert("RGBA"))
        assert int(np.count_nonzero(rgba[..., 3] == 0)) > 0  # tak/byggnad maskad
        assert int(np.count_nonzero(rgba[..., 3] == 255)) > 0  # mark synlig

        # Oinloggad 401, outsider (Di) 403, okänt projekt 404
        assert geo.http_raw(None, "GET", png_url)[0] == 401
        assert geo.http_raw("di", "GET", png_url)[0] == 403
        assert (
            geo.http_raw("bo", "GET", f"/api/kartverktyg/okant-404/buller/{lag['id']}.png")[0] == 404
        )

    # Projektmappen är helt orörd på disk
    assert geo.projektfiler_snapshot() == fore_filer


# ------------------------------------------------------------------ 4. Resultatlager, LAGER_VERSION, B11 & ASGI-semafor


def test_resultatlager_roller_och_lager_version_for_buller() -> None:
    assert "ekvivalent_ljudniva_dba" in MASKADE_ROLLER
    assert etikett_och_enhet("ekvivalent_ljudniva_dba") == ("Ekvivalent ljudnivå", "dB(A)")
    assert farg_for_roll("ekvivalent_ljudniva_dba") in PALETTER
    assert takkalla_for_roll("ekvivalent_ljudniva_dba") == "fotavtryck"
    assert LAGER_VERSION >= 12


def test_b11_sektion_nekar_ekvivalent_ljudniva_dba_med_400(
    tmp_path: Path, konto_miljo: Miljo
) -> None:
    """B11-invariant: `sektion` acceptera ENDAST `max_djup` och nekar `ekvivalent_ljudniva_dba` med 400."""
    geo = _SyntetiskGeoV8(tmp_path, konto_miljo)
    pid = geo.projekt_id

    kid = "k-b11-buller-test"
    kmapp = geo.app.fragor.korningsmapp / kid
    utmapp = kmapp / "resultat"
    webbmapp = kmapp / "webb"
    utmapp.mkdir(parents=True)
    webbmapp.mkdir(parents=True)

    ny, nx = 12, 12
    u = {"x_min": E0, "y_min": N0, "x_max": E0 + 60.0, "y_max": N0 + 60.0}
    arr_djup = np.full((ny, nx), 0.12, dtype=np.float32)
    arr_buller = np.full((ny, nx), 58.0, dtype=np.float32)
    np.save(webbmapp / "skyfall.bas.max_djup.npy", arr_djup)
    np.save(webbmapp / "buller.bas.ekvivalent_ljudniva_dba.npy", arr_buller)

    skriv_geotiff(utmapp / "dummy.tif", arr_djup, x_min=u["x_min"], y_max=u["y_max"], pixelstorlek=5.0)
    (kmapp / "korning.json").write_text(
        json.dumps(
            {
                "korning_id": kid,
                "ref": "kvarteret-v8-skyfall",
                "fraga_id": "kvarteret-v8-skyfall",
                "fraga_fil": str(geo.pmapp / "fragor" / "skyfall.toml"),
                "status": "klar",
                "skapad": "2026-11-26T12:00:00+00:00",
                "start": "2026-11-26T12:00:00+00:00",
                "slut": "2026-11-26T12:00:01+00:00",
                "fel": None,
                "jobb_totalt": 1,
                "projekt_id": pid,
            }
        ),
        encoding="utf-8",
    )
    (utmapp / "sammanstallning.json").write_text(
        json.dumps(
            {
                "fraga_id": "kvarteret-v8-skyfall",
                "beskrivning": "B11 buller test",
                "sammanfattning": "",
                "scenarier": {"bas": {"nyckel": "bas", "rutor": []}},
                "analyser": [],
                "jobb": [],
                "jamforelser": [],
                "total_tid_s": 0.1,
            }
        ),
        encoding="utf-8",
    )

    def _lager_post(lid: str, roll: str, enhet: str) -> dict[str, Any]:
        return {
            "id": lid,
            "namn": lid,
            "etikett": roll,
            "analys": "test",
            "scenario": "bas",
            "roll": roll,
            "typ": "bas",
            "primar": roll == "max_djup",
            "doman": "vatten" if roll == "max_djup" else "buller",
            "enhet": enhet,
            "farg": "vatten" if roll == "max_djup" else "fara",
            "skala": {"min": 0.0, "max": 80.0},
            "teckenforklaring": {
                "typ": "kontinuerlig",
                "min": 0.0,
                "max": 80.0,
                "stopp": ["#000000", "#ffffff"],
            },
            "utbredning": u,
            "synlig_utbredning": u,
            "pixelstorlek_m": 5.0,
            "bredd_px": nx,
            "hojd_px": ny,
            "nedsampling": 1,
            "png": f"/api/korningar/{kid}/lager/{lid}.png",
            "png_med_tak": None,
            "tak_maskat": True,
            "tak_celler": 0,
            "tak_yta_m2": 0.0,
            "geotiff": [],
        }

    (webbmapp / "lager.json").write_text(
        json.dumps(
            {
                "version": LAGER_VERSION,
                "lager": [
                    _lager_post("skyfall.bas.max_djup", "max_djup", "m"),
                    _lager_post(
                        "buller.bas.ekvivalent_ljudniva_dba", "ekvivalent_ljudniva_dba", "dB(A)"
                    ),
                ],
                "varningar": [],
            }
        ),
        encoding="utf-8",
    )
    (webbmapp / "byggnader.json").write_text(
        json.dumps({"version": LAGER_VERSION, "byggnader": None, "varning": None}),
        encoding="utf-8",
    )

    linje = [[E0 + 5.0, N0 + 30.0], [E0 + 55.0, N0 + 30.0]]

    # max_djup accepteras (200)
    st_ok, sek_ok = geo.http(
        "anna",
        "POST",
        f"/api/kartverktyg/{pid}/sektion",
        {"punkter": linje, "korning_id": kid, "fore_lager": "skyfall.bas.max_djup"},
    )
    assert st_ok == 200, sek_ok
    assert sek_ok["kontrakt"] == "floden-sektion/v1"

    # ekvivalent_ljudniva_dba nekas ALLTID med HTTP 400 (både fore_lager och efter_lager)
    st_f, fel_f = geo.http(
        "anna",
        "POST",
        f"/api/kartverktyg/{pid}/sektion",
        {
            "punkter": linje,
            "korning_id": kid,
            "fore_lager": "buller.bas.ekvivalent_ljudniva_dba",
        },
    )
    assert st_f == 400, fel_f
    assert "max_djup" in fel_f["fel"]

    st_e, fel_e = geo.http(
        "anna",
        "POST",
        f"/api/kartverktyg/{pid}/sektion",
        {
            "punkter": linje,
            "korning_id": kid,
            "fore_lager": "skyfall.bas.max_djup",
            "efter_lager": "buller.bas.ekvivalent_ljudniva_dba",
        },
    )
    assert st_e == 400, fel_e
    assert "max_djup" in fel_e["fel"]


def test_asgi_semafor_skyddar_buller_post_utan_att_svalta_cad_export() -> None:
    assert FlodenAsgiApp._ar_tung_berakning("POST", "/api/analys/buller") is True
    assert FlodenAsgiApp._ar_tung_berakning("GET", "/api/projekt/demokvarteret/cad/exportera") is False
    assert (
        FlodenAsgiApp._ar_tung_berakning("GET", "/api/kartverktyg/demokvarteret/buller/bl_123.png")
        is False
    )
    assert FlodenAsgiApp._ar_tung_berakning("POST", "/api/projekt/demokvarteret/cad/importera") is False


def test_buller_skiss_tat_kvartersstad_under_20s_och_600mb(
    tmp_path: Path, konto_miljo: Miljo
) -> None:
    """Tät kvartersstad (160+ hus + murar i 500×500 m K1-ruta): standard POST /api/analys/buller
    med `karna='nord2000'` ska köra på < 20 s och < 600 MB RSS med fokusbeskärning, reflexordning=0
    och bevarade skiss-invarianter."""
    import os
    import resource
    import shutil
    import sys
    import time

    rss_div = 1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0
    tidsfaktor = max(1.0, float(os.environ.get("FLODEN_TIDSFAKTOR", "1")))
    max_tid_s = 20.0 * tidsfaktor

    sida_m = 500.0
    pixel_m = 5.0
    n = round(sida_m / pixel_m)
    k1_tmp = tmp_path / "bygge_tat_k1"
    ruta_tmp = k1_tmp / "mm_150000_6580000"
    ruta_tmp.mkdir(parents=True)

    terrang = np.full((n, n), 12.0, dtype=np.float32)
    markslag = np.full((n, n), 4, dtype=np.uint8)
    osak = np.full((n, n), 0.05, dtype=np.float32)

    hus: list[ByggnadsPost] = []
    n_x, n_y = 13, 14
    dx = (sida_m - 40.0) / n_x
    dy = (sida_m - 40.0) / n_y
    w, h = min(14.0, dx * 0.65), min(14.0, dy * 0.65)
    for ix in range(n_x):
        for iy in range(n_y):
            cx = E0 + 20.0 + ix * dx
            cy = N0 + 20.0 + iy * dy
            if abs((cx + w / 2.0) - (E0 + 250.0)) < 14.0:
                continue
            hus.append(
                ByggnadsPost(
                    byggnad_id=f"b_tat_{len(hus) + 1}",
                    polygon=((cx, cy), (cx + w, cy), (cx + w, cy + h), (cx, cy + h)),
                    hal=(),
                    takhojd_rh2000=24.0,
                    markhojd_rh2000=12.0,
                    hojd_m=12.0,
                    kalla="test",
                )
            )
    assert len(hus) >= 150

    geo_info = GeoTransformInfo(
        bredd=n,
        hojd=n,
        x_min=E0,
        y_max=N0 + sida_m,
        pixelstorlek_x=pixel_m,
        pixelstorlek_y=pixel_m,
        epsg=3011,
        dtype="float32",
    )
    mark, markslag = brann_in_byggnader(
        terrang, markslag, ruta_fran_geo(geo_info), [b.till_byggnad() for b in hus]
    )
    skriv_geotiff(ruta_tmp / "terrang.tif", terrang, x_min=E0, y_max=N0 + sida_m, pixelstorlek=pixel_m)
    skriv_geotiff(
        ruta_tmp / "mark.tif",
        np.asarray(mark, dtype=np.float32),
        x_min=E0,
        y_max=N0 + sida_m,
        pixelstorlek=pixel_m,
    )
    skriv_geotiff(ruta_tmp / "markslag.tif", markslag, x_min=E0, y_max=N0 + sida_m, pixelstorlek=pixel_m)
    skriv_geotiff(ruta_tmp / "osakerhet.tif", osak, x_min=E0, y_max=N0 + sida_m, pixelstorlek=pixel_m)
    skriv_byggnader_gpkg(
        ruta_tmp / "byggnader.gpkg",
        tuple(hus),
        bbox_fallback=(E0, N0, E0 + sida_m, N0 + sida_m),
    )
    murar = [
        {
            "typ": "mur",
            "hojd_m": 2.5,
            "geometry": LineString(
                [(E0 + 238.0, N0 + 20.0 + i * 22.0), (E0 + 238.0, N0 + 36.0 + i * 22.0)]
            ),
        }
        for i in range(20)
    ]
    gpd.GeoDataFrame(murar, crs="EPSG:3011").to_file(
        ruta_tmp / "brytlinjer.gpkg", layer="brytlinjer", driver="GPKG"
    )
    (ruta_tmp / "recept.json").write_text(
        json.dumps(
            {
                "kontrakt": "K1/v2",
                "ruta_id": "mm_150000_6580000",
                "epsg": 3011,
                "hojdsystem": "RH 2000",
                "installningar": {"k1_version": 2, "upplosning_m": pixel_m},
            }
        ),
        encoding="utf-8",
    )

    proj_rot = tmp_path / "projekt_tat"
    pmapp = proj_rot / "haga-tat"
    pmapp.mkdir(parents=True)
    shutil.copytree(k1_tmp, pmapp / "k1_vatten")

    geo_rot = tmp_path / "geo_tat"
    k1_lager = lagra(geo_rot, k1_tmp)
    _, manifest_sha = skriv_manifest(
        geo_rot,
        {
            "kontrakt": KONTRAKT_GEOGRAFI,
            "id": "syntgeo-tat",
            "version": "v1",
            "kommun": "goteborg",
            "karna_3011": [E0, N0, E0 + sida_m, N0 + sida_m],
            "gransen_3011": [E0, N0, E0 + sida_m, N0 + sida_m],
            "lager": {"k1_rutnat": {"sha256": k1_lager.sha256}},
            "kallor": {
                "hojd": {"licens": "CC0-1.0", "kalla": "Syntetisk"},
                "byggnader": {"licens": "CC0-1.0", "kalla": "Syntetisk"},
            },
            "oppen_for_mcp": True,
        },
    )

    org = konto_miljo.register.skapa_organisation("Göteborg Test")
    anna = konto_miljo.register.forbered_konto("anna@example.test")
    konto_miljo.register.gor_organisationsadmin(anna, org)
    pid, _ = konto_miljo.register.registrera_projekt_for_konto(anna, org, "Haga Tät")

    avgr = [E0 + 150.0, N0 + 150.0, E0 + 350.0, N0 + 350.0]
    (pmapp / "oppenhet.toml").write_text(
        'kontrakt = "floden-oppenhet/v1"\noppen = true\nlicens = "CC0-1.0"\n',
        encoding="utf-8",
    )
    (pmapp / "projekt.json").write_text(
        json.dumps(
            {
                "kontrakt": KONTRAKT_PROJEKT,
                "projekt_id": "haga-tat",
                "register_id": pid,
                "namn": "Haga Tät",
                "kommun": "goteborg",
                "geografi": "syntgeo-tat@v1",
                "geografi_manifest_sha256": manifest_sha,
                "avgransning_3011": avgr,
            }
        ),
        encoding="utf-8",
    )
    fdir = pmapp / "fragor"
    fdir.mkdir()
    (fdir / "skyfall.toml").write_text(
        'kontrakt = "K20/v1"\nfraga_id = "haga-tat-skyfall"\nbeskrivning = "Skyfall"\n\n'
        '[scenarier.bas]\nbas = "../k1_vatten"\n\n'
        '[[analyser]]\nid = "skyfall"\nlosare = "vatten.statisk_fyllning"\n'
        "parametrar = { regn_mm = 40.0 }\n",
        encoding="utf-8",
    )

    konfig = tmp_path / "config_tat.toml"
    konfig.write_text(
        '[webb]\nfragemappar = ["fragor"]\ncachemapp = "cache"\nkorningsmapp = "korningar"\n',
        encoding="utf-8",
    )
    tom_ts = tmp_path / "tom_ts_tat"
    tom_ts.mkdir()
    app = FlodenApiApp(
        testsvit_rot=tom_ts,
        konfig_fil=konfig,
        konto=konto_miljo.adapter,
        geomapp=geo_rot,
        projektmapp=proj_rot,
        kartverktygsmapp=tmp_path / "kv_tat",
    )
    w = konto_miljo.webblasare()
    assert w.logga_in(konto_miljo.idp, ANNA)[0] == 302
    rub = {
        "Cookie": "; ".join(f"{k}={v}" for k, v in w.kakor.items()),
        "Origin": ORIGIN,
        **w.csrf_headers(),
    }

    # Standard Steg 2-anrop UTAN uttrycklig reflexordning, fasad_steg_m eller max_vaningar.
    payload = {
        "omrade_id": pid,
        "mottagarhojd_m": 2.0,
        "vagsegment": [
            {
                "id": f"vag_{k + 1}",
                "koordinater": [
                    [E0 + 250.0, avgr[1] + k * 50.0],
                    [E0 + 250.0, avgr[1] + (k + 1) * 50.0],
                ],
                "adt_fordon_dygn": 12000.0,
                "hastighet_km_h": 50.0,
                "andel_tung_pct": 8.0,
            }
            for k in range(4)
        ],
    }
    raw_body = json.dumps(payload).encode("utf-8")
    rss_fore_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / rss_div
    t0 = time.process_time()
    status, _, body = app.hantera_http("POST", "/api/analys/buller", rub, lambda: raw_body)
    dt = time.process_time() - t0
    rss_efter_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / rss_div
    delta_rss_mb = max(0.0, rss_efter_mb - rss_fore_mb)

    assert status == 200, body.decode("utf-8", errors="replace")
    svar = json.loads(body.decode("utf-8"))
    assert dt < max_tid_s, f"Tät urban buller-skiss tog {dt:.2f} s CPU-tid (> {max_tid_s:g} s)"
    assert delta_rss_mb < 600.0, f"RSS-ökning blev {delta_rss_mb:.1f} MB (> 600 MB)"
    assert NORD2000_KONFORM is False
    assert svar["nord2000_konform"] is False
    assert svar["metodstatus"] == "skiss"
    assert svar["rapport_sparrad"] is True
    assert svar["sammanfattning"]["karna"]["namn"] == "nord2000-referens"
    assert svar["sammanfattning"]["reflexordning"] == 0
    assert svar["sammanfattning"]["laeq_max_dba"] > 50.0
    assert len(svar["fasad_buller"]["features"]) > 0
    varn_text = "\n".join(svar["varningar"])
    assert "reflexordning=0" in varn_text
    assert "beskars till projektets fokusområde" in varn_text
    assert svar["png"] and svar["png"].startswith(f"/api/kartverktyg/{pid}/buller/")
