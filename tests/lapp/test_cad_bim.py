"""Tester för CAD/BIM-interoperabilitet (Uppdrag 17): GPKG, DXF, LandXML 1.2, IFC & 3D-mesh."""

from __future__ import annotations

import json
import math
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from rasterio.transform import Affine
from shapely.geometry import LineString, Point, Polygon

from lapp.cad_bim import (
    CRS_EPSG_3011,
    CadBimFel,
    DxfFace3D,
    K50Brytlinje,
    K50Byggnad,
    K50Markyta,
    K50Modell,
    K50Trad,
    exportera_3d_glb,
    exportera_3d_obj,
    exportera_k50_till_dxf,
    exportera_k50_till_gpkg,
    exportera_landxml_tin,
    importera_dxf_till_k50,
    importera_gpkg_till_k50,
    importera_ifc_byggnader,
)
from lapp.landxml import las_landxml_tin, rastrera_landxml_tin
from testsvit.k1_io import EPSG_SWEREF99_1800, GeoTransformInfo


def _exempel_k50_modell() -> K50Modell:
    bygg = K50Byggnad(
        objekt_id="hus_nord",
        fotavtryck=Polygon(
            [
                (150100.0, 6580200.0),
                (150120.0, 6580200.0),
                (150120.0, 6580210.0),
                (150100.0, 6580210.0),
            ]
        ),
        byggnadshojd_m=14.5,
        z_bas_rh2000_m=12.25,
        takform="sadeltak",
        lager_namn="byggnader",
        namn="Kvarteret Eken 1",
    )
    mark = K50Markyta(
        objekt_id="gard_syd",
        geometri=Polygon(
            [
                (150100.0, 6580180.0),
                (150120.0, 6580180.0),
                (150120.0, 6580198.0),
                (150100.0, 6580198.0),
            ]
        ),
        niva_rh2000_m=11.8,
        markslag=4,
        roll="mark",
        lager_namn="markytor",
    )
    bryt = K50Brytlinje(
        objekt_id="kantsten_1",
        geometri=LineString(
            [
                (150095.0, 6580199.0, 11.95),
                (150110.0, 6580199.0, 12.10),
                (150125.0, 6580199.0, 12.40),
            ]
        ),
        typ="kantsten",
        hojd_m=0.12,
        lager_namn="brytlinjer",
    )
    trad = K50Trad(
        objekt_id="ek_01",
        position=Point(150105.5, 6580190.25, 11.82),
        z_mark_rh2000_m=11.82,
        krondiameter_m=8.5,
        tradhojd_m=16.0,
        tradslag="ek",
        lager_namn="trad",
    )
    return K50Modell(
        byggnader=(bygg,),
        markytor=(mark,),
        brytlinjer=(bryt,),
        trad=(trad,),
    )


# ---------------------------------------------------------------------------
# Spår 1: GeoPackage (.gpkg)
# ---------------------------------------------------------------------------


def test_gpkg_roundtrip_epsg3011_och_reprojektion_3006_till_3011(tmp_path: Path) -> None:
    """GPKG round-trip i EPSG:3011 samt reprojektion EPSG:3006/4326 -> EPSG:3011 (< 1e-3 m)."""
    modell = _exempel_k50_modell()

    # 1. Direkt round-trip i EPSG:3011
    fil_3011 = exportera_k50_till_gpkg(modell, tmp_path / "k50_3011.gpkg", crs=CRS_EPSG_3011)
    inlast_3011 = importera_gpkg_till_k50(fil_3011)
    assert inlast_3011.epsg == 3011
    assert inlast_3011.kalla_crs == "EPSG:3011"
    assert len(inlast_3011.byggnader) == 1
    assert len(inlast_3011.markytor) == 1
    assert len(inlast_3011.brytlinjer) == 1
    assert len(inlast_3011.trad) == 1

    b_ut = inlast_3011.byggnader[0]
    assert b_ut.objekt_id == "hus_nord"
    assert abs(b_ut.byggnadshojd_m - 14.5) < 1e-9
    assert abs(b_ut.z_bas_rh2000_m - 12.25) < 1e-9
    assert b_ut.takform == "sadeltak"
    assert b_ut.namn == "Kvarteret Eken 1"
    assert abs(b_ut.area_m2 - 200.0) < 1e-6

    br_ut = inlast_3011.brytlinjer[0]
    assert br_ut.har_z
    z_orig = [c[2] for c in modell.brytlinjer[0].geometri.coords]
    z_rt = [c[2] for c in br_ut.geometri.coords]
    assert max(abs(a - b) for a, b in zip(z_orig, z_rt, strict=True)) < 1e-9

    # 2. Export i EPSG:3006 (SWEREF 99 TM) -> automatisk reprojektion tillbaka till EPSG:3011
    fil_3006 = exportera_k50_till_gpkg(modell, tmp_path / "k50_3006.gpkg", crs="EPSG:3006")
    inlast_fran_3006 = importera_gpkg_till_k50(fil_3006)
    assert inlast_fran_3006.epsg == 3011
    assert inlast_fran_3006.kalla_crs == "EPSG:3006"

    # Verifiera horisontell precision < 1e-3 m över alla hörn och orörda Z-höjder
    c_orig = list(modell.byggnader[0].fotavtryck.exterior.coords)
    c_reproj = list(inlast_fran_3006.byggnader[0].fotavtryck.exterior.coords)
    max_xy_fel_m = max(
        math.hypot(a[0] - b[0], a[1] - b[1])
        for a, b in zip(c_orig, c_reproj, strict=True)
    )
    assert max_xy_fel_m < 1e-3

    # 3D-brytlinje behåller Z exakt efter horisontell reprojektion 3006 -> 3011
    br_3006 = inlast_fran_3006.brytlinjer[0]
    assert br_3006.har_z
    max_br_xy_fel = max(
        math.hypot(a[0] - b[0], a[1] - b[1])
        for a, b in zip(
            modell.brytlinjer[0].geometri.coords, br_3006.geometri.coords, strict=True
        )
    )
    max_br_z_fel = max(
        abs(a[2] - b[2])
        for a, b in zip(
            modell.brytlinjer[0].geometri.coords, br_3006.geometri.coords, strict=True
        )
    )
    assert max_br_xy_fel < 1e-3
    assert max_br_z_fel < 1e-9

    # Trädposition + attribut
    t_3006 = inlast_fran_3006.trad[0]
    assert math.hypot(t_3006.position.x - 150105.5, t_3006.position.y - 6580190.25) < 1e-3
    assert t_3006.z_mark_rh2000_m is not None
    assert abs(t_3006.z_mark_rh2000_m - 11.82) < 1e-9
    assert t_3006.krondiameter_m == pytest.approx(8.5)
    assert t_3006.tradhojd_m == pytest.approx(16.0)
    assert t_3006.tradslag == "ek"

    # 3. Export i EPSG:4326 (WGS 84) -> automatisk reprojektion tillbaka till EPSG:3011
    fil_4326 = exportera_k50_till_gpkg(modell, tmp_path / "k50_4326.gpkg", crs="EPSG:4326")
    inlast_fran_4326 = importera_gpkg_till_k50(fil_4326)
    assert inlast_fran_4326.epsg == 3011
    assert inlast_fran_4326.kalla_crs == "EPSG:4326"
    t_4326 = inlast_fran_4326.trad[0]
    assert math.hypot(t_4326.position.x - 150105.5, t_4326.position.y - 6580190.25) < 1e-2

    # K50 ritlager-serialisering
    k50_dict = inlast_fran_3006.till_k50_ritlager_dict()
    assert k50_dict["kontrakt"] == "floden-ritlager/v1"
    assert len(k50_dict["objekt"]) == 3  # byggnad + mark + brytlinje


def test_gpkg_saknad_crs_kastar_valueerror(tmp_path: Path) -> None:
    """GeoPackage utan explicit CRS ska kasta ValueError/CadBimFel — aldrig tyst antagande."""
    fil = tmp_path / "utan_crs.gpkg"
    gdf = gpd.GeoDataFrame(
        {"objekt_id": ["hus_1"], "byggnadshojd_m": [10.0]},
        geometry=[Polygon([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])],
        crs=None,
    )
    with pytest.warns(UserWarning, match="crs"):
        gdf.to_file(fil, layer="byggnader", driver="GPKG")

    with pytest.raises(ValueError, match="saknar CRS"):
        importera_gpkg_till_k50(fil)


# ---------------------------------------------------------------------------
# Spår 2: DXF (.dxf ASCII R12–2018)
# ---------------------------------------------------------------------------


def test_dxf_roundtrip_lwpolyline_line_3dface_point_och_okand_entitet(tmp_path: Path) -> None:
    """DXF round-trip bevarar lager och Z (< 1e-6 m) och räknar okända entiteter explicit."""
    modell = K50Modell(
        byggnader=(
            K50Byggnad(
                objekt_id="b1",
                fotavtryck=Polygon(
                    [
                        (150000.0, 6580000.0),
                        (150012.0, 6580000.0),
                        (150012.0, 6580008.0),
                        (150000.0, 6580008.0),
                    ]
                ),
                byggnadshojd_m=11.25,
                z_bas_rh2000_m=15.125,
                lager_namn="ARK_BYGGNAD",
            ),
        ),
        markytor=(
            K50Markyta(
                objekt_id="m1",
                geometri=Polygon(
                    [
                        (150020.0, 6580000.0),
                        (150030.0, 6580000.0),
                        (150030.0, 6580010.0),
                        (150020.0, 6580010.0),
                    ]
                ),
                niva_rh2000_m=14.375,
                lager_namn="MARK_PLAN",
            ),
        ),
        brytlinjer=(
            # 2-punkts 3D-linje -> exporteras som DXF LINE
            K50Brytlinje(
                objekt_id="l_line",
                geometri=LineString(
                    [
                        (150000.0, 6580015.0, 14.123456),
                        (150020.0, 6580015.0, 13.654321),
                    ]
                ),
                lager_namn="BRYT_LINE",
            ),
            # 3-punkts varierande Z -> exporteras som 3D POLYLINE/VERTEX
            K50Brytlinje(
                objekt_id="l_poly3d",
                geometri=LineString(
                    [
                        (150000.0, 6580020.0, 15.0),
                        (150010.0, 6580022.0, 15.75),
                        (150020.0, 6580020.0, 16.25),
                    ]
                ),
                lager_namn="BRYT_POLY3D",
            ),
        ),
        trad=(
            K50Trad(
                objekt_id="p1",
                position=Point(150025.5, 6580018.25, 14.987654),
                lager_namn="HOJDPUNKT",
            ),
        ),
    )

    face_tri = DxfFace3D(
        objekt_id="f1",
        lager_namn="TIN_YTA",
        horn_m=(
            (150040.0, 6580000.0, 10.123456),
            (150050.0, 6580000.0, 11.234567),
            (150045.0, 6580010.0, 12.345678),
        ),
        geometri=Polygon(
            [
                (150040.0, 6580000.0, 10.123456),
                (150050.0, 6580000.0, 11.234567),
                (150045.0, 6580010.0, 12.345678),
            ]
        ),
    )

    dxf_fil = exportera_k50_till_dxf(modell, tmp_path / "modell.dxf", extra_3dfaces=(face_tri,))
    res = importera_dxf_till_k50(dxf_fil)

    assert res.ohamtade_entiteter == {}
    assert res.antal_entiteter_lasta == 6
    assert set(res.lagernamn) == {
        "ARK_BYGGNAD",
        "MARK_PLAN",
        "BRYT_LINE",
        "BRYT_POLY3D",
        "HOJDPUNKT",
        "TIN_YTA",
    }

    # LWPOLYLINE byggnad med elevation + thickness
    assert len(res.modell.byggnader) == 1
    b_rt = res.modell.byggnader[0]
    assert b_rt.lager_namn == "ARK_BYGGNAD"
    assert abs(b_rt.z_bas_rh2000_m - 15.125) < 1e-6
    assert abs(b_rt.byggnadshojd_m - 11.25) < 1e-6
    assert abs(b_rt.area_m2 - 96.0) < 1e-6

    # LWPOLYLINE markyta med elevation
    assert len(res.modell.markytor) == 1
    m_rt = res.modell.markytor[0]
    assert m_rt.lager_namn == "MARK_PLAN"
    assert m_rt.niva_rh2000_m is not None
    assert abs(m_rt.niva_rh2000_m - 14.375) < 1e-6

    # 3D LINE + 3D POLYLINE/VERTEX Z-bevarande (< 1e-6 m)
    assert len(res.modell.brytlinjer) == 2
    by_lager = {br.lager_namn: br for br in res.modell.brytlinjer}
    line_coords = list(by_lager["BRYT_LINE"].geometri.coords)
    assert abs(line_coords[0][2] - 14.123456) < 1e-6
    assert abs(line_coords[1][2] - 13.654321) < 1e-6

    poly3d_coords = list(by_lager["BRYT_POLY3D"].geometri.coords)
    assert len(poly3d_coords) == 3
    assert abs(poly3d_coords[0][2] - 15.0) < 1e-6
    assert abs(poly3d_coords[1][2] - 15.75) < 1e-6
    assert abs(poly3d_coords[2][2] - 16.25) < 1e-6

    # POINT Z-bevarande (< 1e-6 m)
    assert len(res.modell.trad) == 1
    p_rt = res.modell.trad[0]
    assert p_rt.lager_namn == "HOJDPUNKT"
    assert p_rt.position.has_z
    assert abs(float(p_rt.position.z) - 14.987654) < 1e-6

    # 3DFACE Z-bevarande (< 1e-6 m)
    assert len(res.faces_3d) == 1
    f_rt = res.faces_3d[0]
    assert f_rt.lager_namn == "TIN_YTA"
    assert len(f_rt.horn_m) == 3
    for h_orig, h_rt in zip(face_tri.horn_m, f_rt.horn_m, strict=True):
        assert abs(h_orig[0] - h_rt[0]) < 1e-6
        assert abs(h_orig[1] - h_rt[1]) < 1e-6
        assert abs(h_orig[2] - h_rt[2]) < 1e-6

    # Injicera okända entiteter (CIRCLE, SPLINE x2, MTEXT) + en BLOCKS-sektion som ska ignoreras
    extra_text = dxf_fil.read_text(encoding="utf-8")
    okanda_snippet = "\n".join(
        [
            "0", "CIRCLE", "8", "SYM", "10", "150000.0", "20", "6580000.0", "40", "2.0",
            "0", "SPLINE", "8", "KURVA", "70", "8",
            "0", "SPLINE", "8", "KURVA", "70", "8",
            "0", "MTEXT", "8", "TEXT", "1", "Anteckning",
            "0", "ENDSEC",
        ]
    )
    med_okanda = extra_text.replace("0\nENDSEC\n0\nEOF", okanda_snippet + "\n0\nEOF")
    res_okand = importera_dxf_till_k50(med_okanda)
    assert res_okand.ohamtade_entiteter == {"CIRCLE": 1, "MTEXT": 1, "SPLINE": 2}
    assert any("SPLINE: 2" in v and "CIRCLE: 1" in v for v in res_okand.varningar)


def test_dxf_binar_avvisas_och_insunits_mm_skalas() -> None:
    """Binär DXF ska ge CadBimFel; $INSUNITS=4 (mm) ska skalas exakt med 0.001 till meter."""
    with pytest.raises(CadBimFel, match="Binär DXF"):
        importera_dxf_till_k50("AutoCAD Binary DXF\r\n\x1a\x00")

    dxf_mm = "\n".join(
        [
            "0", "SECTION", "2", "HEADER",
            "9", "$INSUNITS", "70", "4",
            "0", "ENDSEC",
            "0", "SECTION", "2", "ENTITIES",
            "0", "POINT", "8", "P", "10", "150000000.0", "20", "6580000000.0", "30", "12500.0",
            "0", "ENDSEC",
            "0", "EOF",
        ]
    )
    res = importera_dxf_till_k50(dxf_mm)
    assert res.enhetsskalfaktor_m == pytest.approx(0.001)
    assert abs(res.modell.trad[0].position.x - 150000.0) < 1e-6
    assert abs(res.modell.trad[0].position.y - 6580000.0) < 1e-6
    assert abs(float(res.modell.trad[0].position.z) - 12.5) < 1e-6


# ---------------------------------------------------------------------------
# Spår 3: LandXML 1.2 TIN & brytlinjer
# ---------------------------------------------------------------------------


def test_landxml_12_export_och_aterlasning_med_nan_och_brytlinje(tmp_path: Path) -> None:
    """LandXML 1.2 export -> återläsning ger exakt Z (< 1e-6 m) och hoppar över NaN."""
    geo = GeoTransformInfo(
        bredd=5,
        hojd=5,
        x_min=150000.0,
        y_max=6580010.0,
        pixelstorlek_x=2.0,
        pixelstorlek_y=2.0,
        epsg=EPSG_SWEREF99_1800,
        dtype="float32",
    )
    # Exakt lutande plan: z(E, N) = 10.0 + 0.12 * (E - x_min) - 0.07 * (y_max - N)
    e_vek = geo.x_min + (np.arange(geo.bredd, dtype=np.float64) + 0.5) * geo.pixelstorlek_x
    n_vek = geo.y_max - (np.arange(geo.hojd, dtype=np.float64) + 0.5) * geo.pixelstorlek_y
    ee, nn = np.meshgrid(e_vek, n_vek)
    z_raster = 10.0 + 0.12 * (ee - geo.x_min) - 0.07 * (geo.y_max - nn)
    # Sätt mittcellen (2, 2) till NaN
    z_raster[2, 2] = np.nan

    brytlinje = K50Brytlinje(
        objekt_id="mur_ost",
        geometri=LineString([(150002.0, 6580008.0, 10.5), (150008.0, 6580008.0, 11.2)]),
        typ="mur",
        hojd_m=0.5,
    )

    xml_fil = tmp_path / "terrang.xml"
    exp = exportera_landxml_tin(
        z_raster,
        geo,
        xml_fil,
        brytlinjer=[brytlinje],
        yta_namn="test-terrang",
    )

    # 5x5 rutnät -> 4x4=16 kvadrater; (2,2)=NaN berör 4 -> 12 giltiga kvadrater = 24 trianglar
    assert exp.antal_nan_celler == 1
    assert exp.antal_hoppade_kvadrater == 4
    assert exp.antal_trianglar == 24
    assert exp.antal_punkter == 24  # Alla 24 giltiga noder ingår, NaN-noden utelämnas
    assert exp.antal_brytlinjer == 1

    # Verifiera XML-struktur (<Units><Metric>, <CoordinateSystem epsgCode="3011">, <Breaklines>)
    rot = ET.fromstring(xml_fil.read_text(encoding="utf-8"))
    ns = {"lx": "http://www.landxml.org/schema/LandXML-1.2"}
    metric_el = rot.find("lx:Units/lx:Metric", ns)
    assert metric_el is not None
    assert metric_el.attrib["linearUnit"] == "meter"
    crs_el = rot.find("lx:CoordinateSystem", ns)
    assert crs_el is not None
    assert crs_el.attrib["epsgCode"] == "3011"
    brk_el = rot.find("lx:Surfaces/lx:Surface/lx:Definition/lx:Breaklines/lx:Breakline", ns)
    assert brk_el is not None
    assert brk_el.attrib["name"] == "mur_ost"

    # Läs tillbaka med Flodens befintliga LandXML-inläsare!
    yta = las_landxml_tin(xml_fil)
    assert yta.namn == "test-terrang"
    assert yta.antal_punkter == 24
    assert yta.antal_trianglar == 24

    # Verifiera exakt Z i varje TIN-hörnpunkt (< 1e-6 m)
    for e_m, n_m, z_m in yta.punkter.values():
        facit_z = 10.0 + 0.12 * (e_m - geo.x_min) - 0.07 * (geo.y_max - n_m)
        assert abs(z_m - facit_z) < 1e-6

    # Återrastrera med barycentrisk TIN-rasterisering: NaN-cellen ska förbli NaN, övriga exakta!
    z_rt, tackt = rastrera_landxml_tin(yta, geo)
    assert not bool(tackt[2, 2])
    assert math.isnan(float(z_rt[2, 2]))
    assert int(np.sum(tackt)) == 24

    giltig_mask = np.isfinite(z_raster)
    max_fel_m = float(np.max(np.abs(z_rt[giltig_mask].astype(np.float64) - z_raster[giltig_mask])))
    assert max_fel_m < 1e-6

    # Verifiera att rasterio.transform.Affine också accepteras (och fel EPSG avvisas)
    aff = Affine(2.0, 0.0, 150000.0, 0.0, -2.0, 6580010.0)
    exp_aff = exportera_landxml_tin(z_raster, aff, tmp_path / "terrang_aff.xml")
    assert exp_aff.antal_trianglar == 24

    with pytest.raises(CadBimFel, match="EPSG:3011"):
        exportera_landxml_tin(z_raster, aff, tmp_path / "fel.xml", epsg=3006)


# ---------------------------------------------------------------------------
# Spår 4: IFC (.ifc STEP-SPF ISO 10303-21)
# ---------------------------------------------------------------------------


def test_ifc_step_spf_mm_nastlad_90_grader_rektangel_och_l_profil() -> None:
    """IFC STEP-SPF med MILLI (mm), nästlad 90°-rotation, rektangel + L-profil + ostödd BREP."""
    # Site-placering: (+10 m, +20 m, +5 m), identitetsrotation
    # Building relativt Site: (+2 m, +3 m, +1 m), +90° moturs kring Z (RefDirection=(0,1,0))
    # => Total byggnadsram: origo (12, 23, 6), x_hat=(0,1,0), y_hat=(-1,0,0), z_hat=(0,0,1)
    #    dvs lokal (px, py, pz) -> värld (12 - py, 23 + px, 6 + pz) + ursprung!
    ifc_text = """ISO-10303-21;
HEADER;
/* Syntetisk IFC4-modell i millimeter för deterministiskt geometritest */
FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');
FILE_NAME('testbyggnader.ifc','2026-01-01T00:00:00',(''),(''),'Floden','','');
FILE_SCHEMA(('IFC4'));
ENDSEC;
DATA;
#10=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);
#11=IFCUNITASSIGNMENT((#10));
#12=IFCPROJECT('0Proj1',$,'Testprojekt',$,$,$,$,$,#11);

#20=IFCCARTESIANPOINT((10000.,20000.,5000.));
#21=IFCDIRECTION((0.,0.,1.));
#22=IFCDIRECTION((1.,0.,0.));
#23=IFCAXIS2PLACEMENT3D(#20,#21,#22);
#24=IFCLOCALPLACEMENT($,#23);
#25=IFCSITE('0Site1',$,'Tomt',$,$,#24,$,$,.ELEMENT.,$,$,$,$,$);

#30=IFCCARTESIANPOINT((2000.,3000.,1000.));
#31=IFCDIRECTION((0.,0.,1.));
#32=IFCDIRECTION((0.,1.,0.));
#33=IFCAXIS2PLACEMENT3D(#30,#31,#32);
#34=IFCLOCALPLACEMENT(#24,#33);

/* Byggnad 1: IFCRECTANGLEPROFILEDEF 10 m x 4 m (area 40 m²), djup 12 m, z_bas = 6 + 0.5 = 6.5 m */
#40=IFCRECTANGLEPROFILEDEF(.AREA.,'RektProfil',$,10000.,4000.);
#41=IFCCARTESIANPOINT((0.,0.,500.));
#42=IFCAXIS2PLACEMENT3D(#41,$,$);
#43=IFCDIRECTION((0.,0.,1.));
#44=IFCEXTRUDEDAREASOLID(#40,#42,#43,12000.);
#45=IFCSHAPEREPRESENTATION($,'Body','SweptSolid',(#44));
#46=IFCPRODUCTDEFINITIONSHAPE($,$,(#45));
#47=IFCBUILDING('0Bldg1',$,'Hus Rektangel',$,$,#34,#46,$,.ELEMENT.,$,$,$);

/* Byggnad 2: IFCARBITRARYCLOSEDPROFILEDEF L-form i mm:
   (0,0)-(10,0)-(10,4)-(4,4)-(4,8)-(0,8)-(0,0) => area 56 m², djup 9 m, z_bas 6 m */
#50=IFCCARTESIANPOINT((0.,0.));
#51=IFCCARTESIANPOINT((10000.,0.));
#52=IFCCARTESIANPOINT((10000.,4000.));
#53=IFCCARTESIANPOINT((4000.,4000.));
#54=IFCCARTESIANPOINT((4000.,8000.));
#55=IFCCARTESIANPOINT((0.,8000.));
#56=IFCPOLYLINE((#50,#51,#52,#53,#54,#55,#50));
#57=IFCARBITRARYCLOSEDPROFILEDEF(.AREA.,'LProfil',#56);
#58=IFCAXIS2PLACEMENT3D(#50,$,$);
#59=IFCEXTRUDEDAREASOLID(#57,#58,#43,9000.);
#60=IFCSHAPEREPRESENTATION($,'Body','SweptSolid',(#59));
#61=IFCPRODUCTDEFINITIONSHAPE($,$,(#60));
#62=IFCBUILDINGELEMENTPROXY('0ProxyL',$,'Hus L-form',$,$,#34,#61,$,$);

/* Byggnad 3: ej stödd representation IFCFACETEDBREP -> ska rapporteras i varningar */
#70=IFCFACETEDBREP($);
#71=IFCSHAPEREPRESENTATION($,'Body','Brep',(#70));
#72=IFCPRODUCTDEFINITIONSHAPE($,$,(#71));
#73=IFCBUILDINGELEMENTPROXY('0ProxyBrep',$,'Hus Brep',$,$,#34,#72,$,$);
ENDSEC;
END-ISO-10303-21;
"""
    ursprung_e = 150000.0
    ursprung_n = 6580000.0
    ursprung_z = 10.0

    res = importera_ifc_byggnader(
        ifc_text,
        ursprung_e_m=ursprung_e,
        ursprung_n_m=ursprung_n,
        ursprung_z_m=ursprung_z,
    )

    assert res.langd_skala_till_m == pytest.approx(0.001)
    assert res.langdenhet == "mm"
    assert len(res.byggnader) == 2
    assert res.ostodda_representationer == {"IFCFACETEDBREP": 1}
    assert any("IFCFACETEDBREP" in v for v in res.varningar)

    by_namn = {b.namn: b for b in res.byggnader}

    # Kontrollera Byggnad 1 (rektangel roterad 90° CCW):
    # Lokala hörn (-5,-2)..(5,2) -> (12 - py, 23 + px) => E in [10, 14], N in [18, 28]
    b_rekt = by_namn["Hus Rektangel"]
    assert abs(b_rekt.area_m2 - 40.0) < 1e-6
    assert abs(b_rekt.z_bas_rh2000_m - (ursprung_z + 6.5)) < 1e-6
    assert abs(b_rekt.byggnadshojd_m - 12.0) < 1e-6
    min_e, min_n, max_e, max_n = b_rekt.fotavtryck.bounds
    assert abs(min_e - (ursprung_e + 10.0)) < 1e-6
    assert abs(max_e - (ursprung_e + 14.0)) < 1e-6
    assert abs(min_n - (ursprung_n + 18.0)) < 1e-6
    assert abs(max_n - (ursprung_n + 28.0)) < 1e-6

    # Kontrollera Byggnad 2 (L-form roterad 90° CCW):
    # Lokal E in [0,10], N in [0,8] -> värld E in [4,12], N in [23,33]
    b_l = by_namn["Hus L-form"]
    assert abs(b_l.area_m2 - 56.0) < 1e-6
    assert abs(b_l.z_bas_rh2000_m - (ursprung_z + 6.0)) < 1e-6
    assert abs(b_l.byggnadshojd_m - 9.0) < 1e-6
    l_min_e, l_min_n, l_max_e, l_max_n = b_l.fotavtryck.bounds
    assert abs(l_min_e - (ursprung_e + 4.0)) < 1e-6
    assert abs(l_max_e - (ursprung_e + 12.0)) < 1e-6
    assert abs(l_min_n - (ursprung_n + 23.0)) < 1e-6
    assert abs(l_max_n - (ursprung_n + 33.0)) < 1e-6
    # Lokal (px=7, py=6) i utskuret innerhörn -> värld (6, 30) ska ligga UTANFÖR
    assert not b_l.fotavtryck.contains(Point(ursprung_e + 6.0, ursprung_n + 30.0))
    # Lokal (px=2, py=6) inuti L-benet -> värld (6, 25) ska ligga INNANFÖR
    assert b_l.fotavtryck.contains(Point(ursprung_e + 6.0, ursprung_n + 25.0))


def test_ifc_cm_enhet_och_cirkular_placering_avvisas() -> None:
    """CENTI skalas med 0.01 och cirkulär IFCLOCALPLACEMENT ska kasta CadBimFel."""
    ifc_cm = """ISO-10303-21;
HEADER;
ENDSEC;
DATA;
#1=IFCSIUNIT(*,.LENGTHUNIT.,.CENTI.,.METRE.);
#2=IFCAXIS2PLACEMENT3D($,$,$);
#3=IFCLOCALPLACEMENT($,#2);
#5=IFCRECTANGLEPROFILEDEF(.AREA.,$,$,600.,400.);
#6=IFCDIRECTION((0.,0.,1.));
#7=IFCEXTRUDEDAREASOLID(#5,#2,#6,800.);
#8=IFCSHAPEREPRESENTATION($,'Body','SweptSolid',(#7));
#9=IFCPRODUCTDEFINITIONSHAPE($,$,(#8));
#10=IFCBUILDING('0CM',$,'Hus CM',$,$,#3,#9,$,.ELEMENT.,$,$,$);
ENDSEC;
END-ISO-10303-21;
"""
    res_cm = importera_ifc_byggnader(ifc_cm)
    assert res_cm.langd_skala_till_m == pytest.approx(0.01)
    assert res_cm.langdenhet == "cm"
    assert abs(res_cm.byggnader[0].area_m2 - 24.0) < 1e-6
    assert abs(res_cm.byggnader[0].byggnadshojd_m - 8.0) < 1e-6

    ifc_cirk = """ISO-10303-21;
HEADER;
ENDSEC;
DATA;
#1=IFCSIUNIT(*,.LENGTHUNIT.,.CENTI.,.METRE.);
#2=IFCAXIS2PLACEMENT3D($,$,$);
#3=IFCLOCALPLACEMENT(#4,#2);
#4=IFCLOCALPLACEMENT(#3,#2);
#5=IFCRECTANGLEPROFILEDEF(.AREA.,$,$,100.,100.);
#6=IFCDIRECTION((0.,0.,1.));
#7=IFCEXTRUDEDAREASOLID(#5,#2,#6,500.);
#8=IFCSHAPEREPRESENTATION($,'Body','SweptSolid',(#7));
#9=IFCPRODUCTDEFINITIONSHAPE($,$,(#8));
#10=IFCBUILDING('0C',$,'Cirk',$,$,#3,#9,$,.ELEMENT.,$,$,$);
ENDSEC;
END-ISO-10303-21;
"""
    with pytest.raises(CadBimFel, match="Cirkulär IFCLOCALPLACEMENT"):
        importera_ifc_byggnader(ifc_cirk)


# ---------------------------------------------------------------------------
# Spår 5: 3D-mesh-export (.obj och binär .glb glTF 2.0)
# ---------------------------------------------------------------------------


def test_3d_obj_och_binar_glb_gltf2_integritet_och_triangelantal(tmp_path: Path) -> None:
    """Verifierar OBJ-huvud + binär GLB-header (magic/version/chunks/4B-align/extras/trianglar)."""
    geo = GeoTransformInfo(
        bredd=4,
        hojd=4,
        x_min=150000.0,
        y_max=6580008.0,
        pixelstorlek_x=2.0,
        pixelstorlek_y=2.0,
        epsg=EPSG_SWEREF99_1800,
        dtype="float32",
    )
    z_raster = np.full((4, 4), 12.0, dtype=np.float64)
    # 3x3=9 kvadrater minus 1 hörnkvadrat = 8 kvadrater -> 16 terrängtrianglar
    z_raster[0, 0] = np.nan

    # Rektangulärt hus (4 hörn -> 8 vägg + 2 tak + 2 botten = 12 trianglar)
    hus_rekt = K50Byggnad(
        objekt_id="hus_rekt",
        fotavtryck=Polygon(
            [
                (150001.0, 6580001.0),
                (150004.0, 6580001.0),
                (150004.0, 6580004.0),
                (150001.0, 6580004.0),
            ]
        ),
        byggnadshojd_m=10.0,
        z_bas_rh2000_m=12.0,
    )
    # Konkavt L-format hus (6 hörn -> 12 vägg + 4 tak + 4 botten = 20 trianglar)
    hus_l = K50Byggnad(
        objekt_id="hus_l",
        fotavtryck=Polygon(
            [
                (150005.0, 6580001.0),
                (150008.0, 6580001.0),
                (150008.0, 6580002.5),
                (150006.5, 6580002.5),
                (150006.5, 6580004.0),
                (150005.0, 6580004.0),
            ]
        ),
        byggnadshojd_m=8.0,
        z_bas_rh2000_m=12.0,
    )

    e0, n0, z0 = 150004.0, 6580004.0, 12.0
    forvantade_terrang_tri = 16
    forvantade_bygg_tri = 12 + 20
    forvantade_tot_tri = forvantade_terrang_tri + forvantade_bygg_tri

    # 1. Wavefront OBJ
    obj_fil = tmp_path / "scen.obj"
    res_obj = exportera_3d_obj(
        obj_fil,
        hojd_raster_m=z_raster,
        transform=geo,
        byggnader=[hus_rekt, hus_l],
        ursprung_e_m=e0,
        ursprung_n_m=n0,
        ursprung_z_m=z0,
    )
    assert res_obj.antal_terrang_trianglar == forvantade_terrang_tri
    assert res_obj.antal_byggnad_trianglar == forvantade_bygg_tri
    assert res_obj.antal_trianglar == forvantade_tot_tri

    obj_text = obj_fil.read_text(encoding="utf-8")
    assert "# CRS: EPSG:3011" in obj_text
    assert f"# Ursprung_EPSG3011_E_m: {e0:.6f}" in obj_text
    assert f"# Ursprung_EPSG3011_N_m: {n0:.6f}" in obj_text
    assert f"# Ursprung_RH2000_Z_m: {z0:.6f}" in obj_text
    obj_faces = [r for r in obj_text.splitlines() if r.startswith("f ")]
    obj_verts = [r for r in obj_text.splitlines() if r.startswith("v ")]
    assert len(obj_faces) == forvantade_tot_tri
    assert len(obj_verts) == res_obj.antal_horn

    # Kontrollera att koordinaterna är centrerade nära origo (inget 6.58M-jitter)
    for vr in obj_verts:
        _, sx, sy, sz = vr.split()
        assert abs(float(sx)) < 50.0
        assert abs(float(sy)) < 50.0
        assert abs(float(sz)) < 50.0

    # 2. Binär glTF 2.0 (.glb)
    glb_fil = tmp_path / "scen.glb"
    res_glb = exportera_3d_glb(
        glb_fil,
        hojd_raster_m=z_raster,
        transform=geo,
        byggnader=[hus_rekt, hus_l],
        ursprung_e_m=e0,
        ursprung_n_m=n0,
        ursprung_z_m=z0,
    )
    assert res_glb.antal_terrang_trianglar == forvantade_terrang_tri
    assert res_glb.antal_byggnad_trianglar == forvantade_bygg_tri
    assert res_glb.antal_trianglar == forvantade_tot_tri

    raw = glb_fil.read_bytes()
    assert len(raw) == res_glb.filstorlek_bytes
    assert len(raw) % 4 == 0

    # GLB 12-byte header
    magic, version, total_len = struct.unpack_from("<III", raw, 0)
    assert magic == 0x46546C67  # b"glTF"
    assert version == 2
    assert total_len == len(raw)

    # Chunk 0: JSON
    json_len, json_type = struct.unpack_from("<II", raw, 12)
    assert json_type == 0x4E4F534A  # b"JSON"
    assert json_len % 4 == 0
    json_raw = raw[20 : 20 + json_len]
    gltf = json.loads(json_raw.decode("utf-8"))

    # Chunk 1: BIN
    bin_header_off = 20 + json_len
    bin_len, bin_type = struct.unpack_from("<II", raw, bin_header_off)
    assert bin_type == 0x004E4942  # b"BIN\x00"
    assert bin_len % 4 == 0
    bin_data = raw[bin_header_off + 8 : bin_header_off + 8 + bin_len]
    assert len(bin_data) == bin_len
    assert bin_header_off + 8 + bin_len == total_len
    assert gltf["buffers"][0]["byteLength"] == bin_len

    # Verifiera asset.extras med exakt EPSG:3011-ursprung
    assert gltf["asset"]["version"] == "2.0"
    extras = gltf["asset"]["extras"]
    assert extras["crs"] == "EPSG:3011"
    assert extras["epsg"] == 3011
    assert extras["hojdsystem"] == "RH 2000"
    assert extras["ursprung_e_m"] == pytest.approx(e0)
    assert extras["ursprung_n_m"] == pytest.approx(n0)
    assert extras["ursprung_z_m"] == pytest.approx(z0)

    # Verifiera 4-bytes alignment på varje bufferView samt uppackade trianglar & Y-upp-höjder
    for bv in gltf["bufferViews"]:
        assert bv["byteOffset"] % 4 == 0
        assert bv["byteOffset"] + bv["byteLength"] <= bin_len

    summa_tri = 0
    max_local_y = -math.inf
    for mesh in gltf["meshes"]:
        prim = mesh["primitives"][0]
        acc_idx = gltf["accessors"][prim["indices"]]
        acc_pos = gltf["accessors"][prim["attributes"]["POSITION"]]
        assert "min" in acc_pos and "max" in acc_pos
        assert acc_idx["count"] % 3 == 0
        summa_tri += int(acc_idx["count"]) // 3

        bv_pos = gltf["bufferViews"][acc_pos["bufferView"]]
        pos_arr = np.frombuffer(
            bin_data,
            dtype="<f4",
            count=int(acc_pos["count"]) * 3,
            offset=int(bv_pos["byteOffset"]),
        ).reshape(-1, 3)
        # Alla lokala koordinater ska vara nära 0 (inget float32-jitter)
        assert float(np.max(np.abs(pos_arr))) < 50.0
        max_local_y = max(max_local_y, float(np.max(pos_arr[:, 1])))

    assert summa_tri == forvantade_tot_tri
    # Högsta byggnadstaket är z_bas(12) + hojd(10) = 22 m => lokal Y = 22 - 12 = 10.0 m
    assert abs(max_local_y - 10.0) < 1e-5


def test_3d_kvartersgard_med_hal_bevarar_exakt_takarea(tmp_path: Path) -> None:
    """Sluten kvartersgård med innergård (hål i polygonen) ska trianguleras utan överlapp."""
    yttre = [
        (150000.0, 6580000.0),
        (150020.0, 6580000.0),
        (150020.0, 6580020.0),
        (150000.0, 6580020.0),
    ]
    gard = [
        (150006.0, 6580006.0),
        (150014.0, 6580006.0),
        (150014.0, 6580014.0),
        (150006.0, 6580014.0),
    ]
    kvarter = K50Byggnad(
        objekt_id="kvartersgard",
        fotavtryck=Polygon(yttre, holes=[gard]),
        byggnadshojd_m=15.0,
        z_bas_rh2000_m=10.0,
    )
    assert abs(kvarter.area_m2 - (400.0 - 64.0)) < 1e-6

    obj_fil = tmp_path / "kvarter.obj"
    res = exportera_3d_obj(obj_fil, byggnader=[kvarter], inkludera_botten=False)
    assert res.antal_byggnad_trianglar > 0

    # Summera arean av taktrianglarna (alla hörn på z_tak = 15.0 m relativt ursprung_z = 10.0 m)
    verts: list[tuple[float, float, float]] = []
    tak_area_m2 = 0.0
    for rad in obj_fil.read_text(encoding="utf-8").splitlines():
        if rad.startswith("v "):
            _, sx, sy, sz = rad.split()
            verts.append((float(sx), float(sy), float(sz)))
        elif rad.startswith("f "):
            delar = rad.split()[1:]
            idx = [int(d.split("/")[0]) - 1 for d in delar]
            p0, p1, p2 = verts[idx[0]], verts[idx[1]], verts[idx[2]]
            if abs(p0[2] - 15.0) < 1e-4 and abs(p1[2] - 15.0) < 1e-4 and abs(p2[2] - 15.0) < 1e-4:
                tak_area_m2 += 0.5 * abs(
                    (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p1[1] - p0[1]) * (p2[0] - p0[0])
                )

    assert abs(tak_area_m2 - 336.0) < 1e-4
