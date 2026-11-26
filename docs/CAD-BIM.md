# CAD/BIM-interoperabilitet (GeoPackage, DXF, LandXML 1.2, IFC & 3D-mesh)

Modulen [`src/lapp/cad_bim.py`](../src/lapp/cad_bim.py) ger deterministisk in- och utmatning mellan vanliga CAD-, BIM- och GIS-verktyg (QGIS, AutoCAD, Civil 3D, SCALGO, Revit, Rhino, Blender) och Flodens interna K50-modell i **SWEREF 99 18 00 (EPSG:3011)** och höjdsystem **RH 2000**.

Modulen använder enbart Flodens befintliga kärnberoenden (`stdlib`, `numpy`, `shapely`, `geopandas`, `pyogrio`, `pyproj`, `gemensamt`) utan extra tredjepartspaket och håller alla paket- och domängränser intakta.

---

## Koordinatsystem, höjdsystem och felhantering

- **Internt koordinatsystem:** Samtliga `K50Modell`-objekt, exporterade LandXML-ytor och 3D-mesher lagras i **SWEREF 99 18 00 (`EPSG:3011`)** med höjder i **RH 2000** och SI-enheter (`_m`, `_m2`, `_rh2000_m`).
- **Inga tysta CRS-antaganden:**
  - `importera_gpkg_till_k50` **kräver** explicit definierad CRS på varje GeoPackage-lager (inklusive kontroll mot odefinierat GPKG `srs_id` `0`/`-1`). Saknas CRS kastas `CadBimFel` (subklass av `ValueError`).
  - GeoPackage-lager i annat koordinatsystem (t.ex. `EPSG:3006` SWEREF 99 TM eller `EPSG:4326` WGS 84) reprojiceras automatiskt horisontellt till `EPSG:3011` via `pyproj` medan 3D Z-höjder i RH 2000 bevaras orörda.
  - För format utan standardiserad CRS-header (DXF och IFC) anges `kalla_epsg` (förval `3011`) samt valfritt geodetiskt projektnollpunktsoffset `(ursprung_e_m, ursprung_n_m, ursprung_z_m)` för lokala BIM-modeller.
- **Inga tysta datatapp:** Ej stödda DXF-entiteter och ej stödda IFC-representationer räknas explicit i `ohamtade_entiteter` respektive `ostodda_representationer` och rapporteras i `varningar`.

---

## Formatstöd

| Format | Import | Export | Stödda objekt / detaljer |
| --- | --- | --- | --- |
| **GeoPackage (`.gpkg`)** | `importera_gpkg_till_k50` | `exportera_k50_till_gpkg` | Flerlagers GPKG (`byggnader`, `markytor`, `brytlinjer`, `trad`) med 3D Z-koordinater, `byggnadshojd_m`, `z_bas_rh2000_m`, `takform`, `niva_rh2000_m`, `markslag`, automatisk reprojektion till EPSG:3011 och strikt CRS-kontroll. |
| **DXF ASCII (`.dxf` R12–2018)** | `importera_dxf_till_k50` | `exportera_k50_till_dxf` | Ren Python-parser/skrivare för `LWPOLYLINE` (inkl. sluten flagga, elevation `38` och thickness `39`), `POLYLINE`/`VERTEX` (2D & 3D), `LINE` (3D), `3DFACE` (triangel/fyrhörning) och `POINT`. Bevarar lagernamn och Z-höjder, skalar `$INSUNITS` till meter och ignorerar `BLOCKS`-definitioner. |
| **LandXML 1.2 (`.xml`)** | [`las_landxml_tin`](../src/lapp/landxml.py) | `exportera_landxml_tin` | Standard LandXML 1.2 med `<Units><Metric/>`, `<CoordinateSystem epsgCode="3011"/>`, `<Surfaces><Surface><Definition surfType="TIN"><Pnts>` + `<Faces>` (2 moturs trianglar per giltig cellkvadrat, hoppar över `NaN`-celler explicit) samt valfria `<Breaklines><Breakline><PntList3D>`. Direkt kompatibel med Flodens `las_landxml_tin`, Civil 3D och SCALGO. |
| **IFC STEP-SPF (`.ifc` ISO 10303-21)** | `importera_ifc_byggnader` | – | Ren Python STEP-parser: `IFCSIUNIT` (mm/cm/m → meter) och `IFCCONVERSIONBASEDUNIT`, rekursiv `IFCSITE`/`IFCBUILDING`/`IFCLOCALPLACEMENT` + `IFCAXIS2PLACEMENT3D` (3D-translation och rotation enligt `IfcBuildAxes`), `IFCEXTRUDEDAREASOLID` med `IFCRECTANGLEPROFILEDEF`, `IFCARBITRARYCLOSEDPROFILEDEF`, `IFCARBITRARYPROFILEDEFWITHVOIDS` och `IFCPOLYLINE`/`IFCCARTESIANPOINT`. |
| **3D-mesh (`.obj`, `.glb` glTF 2.0)** | – | `exportera_3d_obj`, `exportera_3d_glb` | Terräng-TIN + vattentätt extruderade byggnadsvolymer (inkl. konkava polygoner och slutna kvartersgårdar med hål). Centreras i `float64` kring `(ursprung_e_m, ursprung_n_m, ursprung_z_m)` före export för att helt undvika `float32`-koordinatjitter i Revit, Rhino och Blender. |

---

## Axelkonventioner och georeferens vid 3D-export

Stora koordinatvärden i SWEREF 99 (`N ≈ 6 580 000 m`) ger ungefär `0,5 m` kvantiseringsfel i `float32` (som används i glTF/WebGL och många 3D-program). Därför subtraheras alltid `(ursprung_e_m, ursprung_n_m, ursprung_z_m)` i `float64` innan `float32`-bufferter skrivs:

- **Wavefront `.obj`** (Z-upp, högerhänt):
  - `(x, y, z) = (E - ursprung_e_m, N - ursprung_n_m, Z_RH2000 - ursprung_z_m)`
  - Georeferensen skrivs i kommentarhuvudet: `# CRS: EPSG:3011`, `# Hojdsystem: RH 2000`, `# Ursprung_EPSG3011_E_m`, `# Ursprung_EPSG3011_N_m`, `# Ursprung_RH2000_Z_m`.
- **Binär glTF 2.0 `.glb`** (Y-upp, högerhänt enligt glTF 2.0 §3.4):
  - `(x_glTF, y_glTF, z_glTF) = (E - ursprung_e_m, Z_RH2000 - ursprung_z_m, -(N - ursprung_n_m))`
  - Transformationen har determinant `+1` och bevarar högerhänt orientering och moturs framsidor.
  - Exakt georeferens lagras i `asset.extras` (`crs`, `crs_namn`, `epsg`, `hojdsystem`, `ursprung_e_m`, `ursprung_n_m`, `ursprung_z_m`, `axlar`).
  - Binärfilen följer glTF 2.0: 12-bytes header (`0x46546C67`, version `2`), 4-bytes-justerad JSON-chunk (paddad med `0x20`) och 4-bytes-justerad BIN-chunk (paddad med `0x00`).

---

## Begränsningar

- **DXF:** Endast **ASCII DXF (R12–2018)** stöds; binär DXF avvisas med `CadBimFel`. Cirkelbågar (`bulge` gruppkod `42` på `LWPOLYLINE`) linjäriseras till kordor med varning. Entiteter utanför `LWPOLYLINE`, `POLYLINE`/`VERTEX`, `LINE`, `3DFACE` och `POINT` (t.ex. `SPLINE`, `CIRCLE`, `HATCH`, `INSERT`-blockinstanser) räknas i `ohamtade_entiteter` och hoppas över.
- **IFC:** Endast **STEP-SPF (ISO 10303-21)** och extruderade volymer (`IFCEXTRUDEDAREASOLID`) med polygon- eller rektangelprofiler stöds i denna version. Facetterade eller booleska representationer (`IFCFACETEDBREP`, `IFCTRIANGULATEDFACESET`, `IFCBOOLEANCLIPPINGRESULT`) och `IFCMAPPEDITEM` rapporteras explicit i `ostodda_representationer` och `varningar`. Lutande extruderingar projiceras till konvext 2D-fotavtryck med varning.
- **LandXML:** Rasterexporten triangulerar cellkvadrater där samtliga fyra hörn är ändliga (`NaN`-celler hoppas över). Befintliga brytlinjer skrivs som `<Breaklines>` men om-triangulerar inte TIN-nätet (ingen constrained Delaunay i v1).
- **K50-ritlager:** `K50Modell.till_k50_ritlager_dict()` översätter byggnader, markytor och brytlinjer till [`floden-ritlager/v1`](DATAKONTRAKT.md); träd (`K50Trad`) lagras separat eftersom K50:s `punkt`-roll är reserverad.

---

## Exempel i Python

```python
from pathlib import Path
import numpy as np
from shapely.geometry import LineString, Polygon

from lapp.cad_bim import (
    K50Brytlinje,
    K50Byggnad,
    K50Modell,
    exportera_3d_glb,
    exportera_k50_till_dxf,
    exportera_k50_till_gpkg,
    exportera_landxml_tin,
    importera_dxf_till_k50,
    importera_gpkg_till_k50,
    importera_ifc_byggnader,
)
from lapp.landxml import las_landxml_tin

# 1. GeoPackage med automatisk reprojektion EPSG:3006 -> EPSG:3011
hus = K50Byggnad(
    objekt_id="kvarter_a",
    fotavtryck=Polygon([
        (150100.0, 6580200.0),
        (150120.0, 6580200.0),
        (150120.0, 6580212.0),
        (150100.0, 6580212.0),
    ]),
    byggnadshojd_m=15.0,
    z_bas_rh2000_m=12.5,
    takform="sadeltak",
)
modell = K50Modell(byggnader=(hus,))
exportera_k50_till_gpkg(modell, Path("ut/forslag_3006.gpkg"), crs="EPSG:3006")
inlast = importera_gpkg_till_k50(Path("ut/forslag_3006.gpkg"))  # -> EPSG:3011

# 2. DXF export & import med Z-bevarande
exportera_k50_till_dxf(inlast, Path("ut/forslag.dxf"))
dxf_res = importera_dxf_till_k50(Path("ut/forslag.dxf"))

# 3. LandXML 1.2 TIN-export -> läsbar av Flodens egen inläsare
hojd_m = np.full((10, 10), 12.5, dtype=np.float32)
transform = (1.0, 0.0, 150095.0, 0.0, -1.0, 6580218.0)  # (dx, 0, x_min, 0, -dy, y_max)
exportera_landxml_tin(hojd_m, transform, Path("ut/mark.xml"))
tin = las_landxml_tin(Path("ut/mark.xml"))

# 4. IFC STEP-SPF byggnadsimport med lokal georeferens
ifc_res = importera_ifc_byggnader(
    Path("indata/byggnad.ifc"),
    ursprung_e_m=150100.0,
    ursprung_n_m=6580200.0,
    ursprung_z_m=12.5,
)

# 5. Binär glTF 2.0 (.glb) för Revit/Rhino/Blender utan float32-jitter
exportera_3d_glb(
    Path("ut/forslag.glb"),
    hojd_raster_m=hojd_m,
    transform=transform,
    byggnader=inlast.byggnader,
)
```

Se även [`tests/lapp/test_cad_bim.py`](../tests/lapp/test_cad_bim.py) för samtliga geometriska verifieringsfall, [`docs/DATAKONTRAKT.md`](DATAKONTRAKT.md) för K50-specifikationen, [`docs/DATA.md`](DATA.md) för indata och [`docs/WEBB3D.md`](WEBB3D.md) för 3D-vyn i webben.
