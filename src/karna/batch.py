"""Återupptagbar rut-kö för förberäkning av stora områden (kommuner / stadsdelar).

Delar upp ett område (GeoJSON eller GeoPackage) i standardiserade 500×500 m-rutor
(SWEREF 99 18 00, EPSG:3011) med överlapp, kör valda moduler per ruta med atomisk
skrivning och K0-manifest (`run.json`), och hoppar över redan färdigberäknade rutor
vid återstart.

Dataskyddsspärr (`AGENTS.md`): om `COLAB_RELEASE_TAG` (eller annan känd molnmiljö)
är satt vägras all körning när `dataklass != "oppen"`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from shapely.geometry import box, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform
from shapely.ops import unary_union

from gemensamt.gpu import berakningsplats, hamta_backend
from gemensamt.hash import sha256_fil
from gemensamt.koordinater import transformera
from gemensamt.manifest import (
    FilPost,
    ManifestFel,
    hamta_kodversion,
    las_manifest,
    skapa_manifest,
    skriv_manifest,
)
from gemensamt.objektlagring import ObjektLagring, ObjektLagringFel, geografi_nyckel
from gemensamt.rensning import temporar_rutkatalog
from markmodell.rutnat import Ruta, skapa_rutnat
from testsvit.k1_io import GeoTransformInfo

DATAKLASSER: tuple[str, ...] = ("oppen", "intern", "skyddad")
MODULER_CPU: frozenset[str] = frozenset({"markmodell", "sankgraf"})
MODULER_GPU: frozenset[str] = frozenset({"vatten2d", "dagsljus", "luft", "ekologi"})
TILLATNA_MODULER: frozenset[str] = MODULER_CPU | MODULER_GPU
MOLN_MILJOVARIABLER: tuple[str, ...] = (
    "COLAB_RELEASE_TAG",
    "COLAB_GPU",
    "KAGGLE_KERNEL_RUN_TYPE",
)

Dataklass = Literal["oppen", "intern", "skyddad"]
Precision = Literal["f64", "f32"]


class BatchFel(Exception):
    """Fel vid förberäkning, dataskyddskontroll eller rutgenerering."""


@dataclass(frozen=True)
class BatchKonfig:
    """Konfiguration för en återupptagbar batchkörning."""

    moduler: tuple[str, ...]
    utdata_rot: Path
    enhet: str = "auto"
    precision: Precision = "f64"
    dataklass: Dataklass = "oppen"
    rutstorlek_m: float = 500.0
    overlapp_m: float = 25.0
    upplosning_m: float = 1.0
    max_arbetare: int = 1
    kommun: str = "standard"
    lagring_typ: Literal["lokal", "s3"] = "lokal"
    behall_radata: bool = False
    parametrar: Mapping[str, Any] = field(default_factory=dict)
    indata_filer: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if not self.moduler:
            raise BatchFel("Minst en modul måste anges för förberäkning.")
        okanda = sorted(set(self.moduler) - TILLATNA_MODULER)
        if okanda:
            raise BatchFel(
                f"Okända moduler {okanda}. Tillåtna: {sorted(TILLATNA_MODULER)}."
            )
        if self.dataklass not in DATAKLASSER:
            raise BatchFel(f"Ogiltig dataklass {self.dataklass!r}; välj bland {DATAKLASSER}.")
        if self.precision not in ("f64", "f32"):
            raise BatchFel(f"Ogiltig precision {self.precision!r}; välj 'f64' eller 'f32'.")
        if self.lagring_typ not in ("lokal", "s3"):
            raise BatchFel(f"Ogiltig lagring_typ {self.lagring_typ!r}; välj 'lokal' eller 's3'.")
        if self.max_arbetare < 1:
            raise BatchFel(f"max_arbetare måste vara >= 1, fick {self.max_arbetare}.")


@dataclass(frozen=True)
class RutUtdata:
    """Resultat från körning av en enskild ruta."""

    utdata_filer: tuple[FilPost, ...] = ()
    matvarden: Mapping[str, Any] = field(default_factory=dict)
    varningar: tuple[str, ...] = ()


@dataclass(frozen=True)
class BatchStatus:
    """Samlad status för en pågående eller avslutad batchkörning (`batch_status.json`)."""

    totalt_rutor: int
    klara: tuple[str, ...]
    hoppade: tuple[str, ...]
    felade: Mapping[str, str]
    aterstaende: int
    snittid_s: float
    estimerad_aterstaende_s: float
    total_tid_s: float
    berakningsplats: str
    moduler: tuple[str, ...]
    dataklass: str
    uppdaterad: str

    def till_dict(self) -> dict[str, Any]:
        return {
            "totalt_rutor": self.totalt_rutor,
            "klara": list(self.klara),
            "hoppade": list(self.hoppade),
            "felade": dict(self.felade),
            "aterstaende": self.aterstaende,
            "snittid_s": round(self.snittid_s, 4),
            "estimerad_aterstaende_s": round(self.estimerad_aterstaende_s, 2),
            "total_tid_s": round(self.total_tid_s, 4),
            "berakningsplats": self.berakningsplats,
            "moduler": list(self.moduler),
            "dataklass": self.dataklass,
            "uppdaterad": self.uppdaterad,
        }


RutKorare = Callable[[Ruta, Path, BatchKonfig, str], RutUtdata]


def ar_molnmiljo(miljo: Mapping[str, str] | None = None) -> bool:
    """Returnerar True om processen körs i Colab eller annan känd molnnotebook-miljö."""
    env = os.environ if miljo is None else miljo
    return any(bool(env.get(namn, "").strip()) for namn in MOLN_MILJOVARIABLER)


def kontrollera_dataskydd(
    dataklass: str,
    *,
    miljo: Mapping[str, str] | None = None,
) -> None:
    """Dataskyddsspärr enligt `AGENTS.md`: vägrar icke-öppen data i moln-/Colab-miljö."""
    if dataklass not in DATAKLASSER:
        raise BatchFel(f"Ogiltig dataklass {dataklass!r}; tillåtna värden är {DATAKLASSER}.")
    if ar_molnmiljo(miljo) and dataklass != "oppen":
        raise BatchFel(
            f"Dataskyddsspärr: dataklass='{dataklass}' får inte köras i moln-/Colab-miljö "
            "(COLAB_RELEASE_TAG / molnvariabel är satt). Endast öppen data (--dataklass oppen) "
            "får förberäknas i molnet enligt AGENTS.md."
        )


def _extrahera_epsg_ur_geojson(data: Mapping[str, Any], standard_epsg: int = 3011) -> int:
    crs = data.get("crs")
    if isinstance(crs, Mapping):
        props = crs.get("properties")
        if isinstance(props, Mapping):
            namn = str(props.get("name", ""))
            for del_str in namn.replace(":", " ").replace("/", " ").split():
                if del_str.isdigit():
                    kod = int(del_str)
                    if kod in (*range(3006, 3019), 4326):
                        return kod
    return standard_epsg


def _transformera_geometri_till_3011(geom: BaseGeometry, fran_epsg: int) -> BaseGeometry:
    if fran_epsg == 3011:
        return geom

    def _fn(x: Any, y: Any, z: Any = None) -> tuple[Any, ...]:
        if np.ndim(x) == 0:
            ex, ny = transformera(float(x), float(y), fran_epsg, 3011)
            return (ex, ny) if z is None else (ex, ny, z)
        xa = np.asarray(x, dtype=np.float64)
        ya = np.asarray(y, dtype=np.float64)
        ut_x = np.empty_like(xa)
        ut_y = np.empty_like(ya)
        for idx in np.ndindex(xa.shape):
            ut_x[idx], ut_y[idx] = transformera(float(xa[idx]), float(ya[idx]), fran_epsg, 3011)
        return (ut_x, ut_y) if z is None else (ut_x, ut_y, z)

    return shapely_transform(_fn, geom)


def las_omradespolygon(omrade_fil: str | Path, *, standard_epsg: int = 3011) -> BaseGeometry:
    """Läser ett områdes polygon(er) från GeoJSON eller GeoPackage i EPSG:3011."""
    sokvag = Path(omrade_fil)
    if not sokvag.is_file():
        raise BatchFel(f"Områdesfilen finns inte: {sokvag}")

    suffix = sokvag.suffix.lower()
    if suffix in (".geojson", ".json"):
        try:
            data = json.loads(sokvag.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BatchFel(f"Kunde inte läsa GeoJSON-filen {sokvag}: {exc}") from exc
        epsg = _extrahera_epsg_ur_geojson(data, standard_epsg=standard_epsg)
        geometrier: list[BaseGeometry] = []
        typ = data.get("type")
        if typ == "FeatureCollection":
            for feat in data.get("features", []):
                g = feat.get("geometry") if isinstance(feat, Mapping) else None
                if g is not None:
                    geometrier.append(shape(g))
        elif typ == "Feature":
            if data.get("geometry") is not None:
                geometrier.append(shape(data["geometry"]))
        else:
            geometrier.append(shape(data))
        if not geometrier:
            raise BatchFel(f"Inga geometrier hittades i {sokvag}.")
        sammanlagd = unary_union(geometrier)
        return _transformera_geometri_till_3011(sammanlagd, epsg)

    if suffix == ".gpkg":
        try:
            import geopandas as gpd
        except ImportError as exc:
            raise BatchFel(
                f"GeoPackage ({sokvag.name}) kräver geopandas/pyogrio, som inte är installerat. "
                "Konvertera till GeoJSON eller installera geopandas."
            ) from exc
        gdf = gpd.read_file(sokvag)
        if gdf.empty:
            raise BatchFel(f"GeoPackage-filen {sokvag} innehåller inga geometrier.")
        if gdf.crs is not None:
            gdf = gdf.to_crs(epsg=3011)
        return unary_union(list(gdf.geometry))

    raise BatchFel(
        f"Stöder inte filformatet '{suffix}' för område; använd .geojson, .json eller .gpkg."
    )


def generera_rutor_for_omrade(
    omrade_fil: str | Path,
    *,
    rutstorlek_m: float = 500.0,
    overlapp_m: float = 25.0,
    upplosning_m: float = 1.0,
    standard_epsg: int = 3011,
) -> list[Ruta]:
    """Delar upp området i deterministiska 500×500 m-rutor som skär områdespolygonen."""
    poly = las_omradespolygon(omrade_fil, standard_epsg=standard_epsg)
    if poly.is_empty:
        raise BatchFel(f"Områdespolygonen i {omrade_fil} är tom.")
    bbox = tuple(float(v) for v in poly.bounds)
    kandidater = skapa_rutnat(
        (bbox[0], bbox[1], bbox[2], bbox[3]),
        storlek_m=rutstorlek_m,
        overlapp_m=overlapp_m,
        upplosning_m=upplosning_m,
    )
    rutor = [r for r in kandidater if box(*r.inre_bbox).intersects(poly)]
    rutor.sort(key=lambda r: r.namn)
    return rutor


def berakna_batch_nyckel(
    ruta: Ruta,
    konfig: BatchKonfig,
    indata_poster: Sequence[FilPost],
    plats: str,
) -> str:
    """Deterministisk SHA-256-nyckel för cachning och återupptagning av en ruta."""
    nyckeldata: dict[str, Any] = {
        "ruta": ruta.namn,
        "inre_bbox": list(ruta.inre_bbox),
        "overlapp_m": ruta.overlapp_m,
        "upplosning_m": ruta.upplosning_m,
        "moduler": list(konfig.moduler),
        "precision": konfig.precision,
        "berakningsplats": plats,
        "parametrar": dict(konfig.parametrar),
        "indata": [p.till_dict() for p in indata_poster],
    }
    if konfig.upplosning_m != ruta.upplosning_m:
        nyckeldata["konfig_upplosning_m"] = konfig.upplosning_m
    text = json.dumps(nyckeldata, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ruta_ar_klar(rutamapp: Path, forvantad_nyckel: str) -> bool:
    """Returnerar True om rutan har ett giltigt `run.json` med matchande nyckel och alla filer."""
    manifest_fil = rutamapp / "run.json"
    if not manifest_fil.is_file():
        return False
    try:
        manifest = las_manifest(manifest_fil)
    except ManifestFel:
        return False
    if manifest.parametrar.get("batch_nyckel") != forvantad_nyckel:
        return False
    return all((rutamapp / post.fil).is_file() for post in manifest.utdata)


def ruta_ar_klar_i_objektlagring(
    objektlagring: ObjektLagring,
    kommun: str,
    ruta_namn: str,
    forvantad_nyckel: str,
) -> bool:
    """Returnerar True om rutan har ett giltigt `run.json` i objektlagring med matchande nyckel.

    Verifierar både `parametrar["batch_nyckel"] == forvantad_nyckel` och att samtliga utdatafiler
    i K0-manifestet finns i objektlagringen (motsvarar lokal `ruta_ar_klar`).
    """
    run_nyckel = geografi_nyckel(kommun, ruta_namn, "run.json")
    if not objektlagring.finns(run_nyckel):
        return False
    with tempfile.TemporaryDirectory(prefix="floden_s3check_") as tmp_dir:
        lokal_run = Path(tmp_dir) / "run.json"
        objektlagring.ladda_ner_fil(run_nyckel, lokal_run)
        try:
            manifest = las_manifest(lokal_run)
        except ManifestFel:
            return False
    if manifest.parametrar.get("batch_nyckel") != forvantad_nyckel:
        return False
    return all(
        objektlagring.finns(geografi_nyckel(kommun, ruta_namn, post.fil))
        for post in manifest.utdata
    )


def _skriv_status_atomiskt(status: BatchStatus, utdata_rot: Path) -> Path:
    utdata_rot.mkdir(parents=True, exist_ok=True)
    mal = utdata_rot / "batch_status.json"
    innehall = json.dumps(status.till_dict(), indent=2, ensure_ascii=False) + "\n"
    fd, tmp_namn = tempfile.mkstemp(prefix=".batch_status.", suffix=".tmp", dir=str(utdata_rot))
    tmp = Path(tmp_namn)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(innehall)
        os.replace(tmp, mal)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return mal


def _standard_rutkorare(
    ruta: Ruta,
    arbetsmapp: Path,
    konfig: BatchKonfig,
    plats: str,
) -> RutUtdata:
    """Standardkörare som sparar rutans metadata när ingen specifik domän-pipeline injicerats."""
    info_fil = arbetsmapp / "ruta_info.json"
    info = {
        "ruta_id": ruta.namn,
        "inre_bbox_3011": list(ruta.inre_bbox),
        "yttre_bbox_3011": list(ruta.yttre_bbox),
        "upplosning_m": ruta.upplosning_m,
        "moduler": list(konfig.moduler),
        "berakningsplats": plats,
        "dataklass": konfig.dataklass,
    }
    info_fil.write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    post = FilPost(roll="ruta_info", fil="ruta_info.json", sha256=sha256_fil(info_fil))
    return RutUtdata(
        utdata_filer=(post,),
        matvarden={"ruta_bredd_m": ruta.storlek_m, "berakningsplats": plats},
    )


def _las_dagsljus_dsm(
    k1_mapp: Path,
    ruta_namn: str,
    *,
    k1_v1: Any | None = None,
    vegetation: bool = False,
) -> tuple[NDArray[np.float64], GeoTransformInfo, int]:
    """Läser ytmodell (DSM) för batch-dagsljus: K1 v2 via `las_ytmodell`, K1 v1 via `mark.tif`."""
    from karna.geometri import GeometriFel, k1_version, las_mark, las_ytmodell
    from markmodell.byggnadsregister import ByggnadsregisterFel
    from markmodell.hinder_oppningar import HinderOppningFel
    from testsvit.k1_io import K1FormatFel

    try:
        version = k1_version(k1_mapp)
    except GeometriFel as exc:
        raise BatchFel(f"Ogiltig K1-ruta {ruta_namn} för dagsljus: {exc}") from exc

    if version == 2:
        try:
            dsm_raw, geo = las_ytmodell(k1_mapp, vegetation=vegetation)
        except (GeometriFel, ByggnadsregisterFel, HinderOppningFel, K1FormatFel) as exc:
            raise BatchFel(
                f"Kunde inte läsa K1 v2-ytmodell (DSM) för {ruta_namn}: {exc}"
            ) from exc
    elif k1_v1 is not None:
        dsm_raw, geo = k1_v1.mark_z, k1_v1.geo
    else:
        try:
            dsm_raw, geo = las_mark(k1_mapp)
        except (GeometriFel, K1FormatFel) as exc:
            raise BatchFel(f"Kunde inte läsa K1 v1 mark.tif för {ruta_namn}: {exc}") from exc

    if abs(float(geo.pixelstorlek_x) - float(geo.pixelstorlek_y)) > 1e-6:
        raise BatchFel(
            f"Dagsljus för {ruta_namn}: icke-kvadratiska celler stöds inte "
            f"({geo.pixelstorlek_x}×{geo.pixelstorlek_y} m)."
        )
    return np.asarray(dsm_raw, dtype=np.float64), geo, version


def _berakna_svf_med_nan_hantering(
    dsm_z: NDArray[np.float64],
    geo: GeoTransformInfo,
    ruta_namn: str,
    *,
    azimutriktningar: int,
    radie_m: float,
    enhet: str,
) -> tuple[NDArray[np.float32], dict[str, Any], tuple[str, ...]]:
    """Beräknar SVF från DSM med deterministisk NaN-maskering utan `HorisontFel`-krasch."""
    from dagsljus.horisont import Hojdfalt, horisont_ruta_iter

    giltig = np.isfinite(dsm_z)
    ogiltig = ~giltig
    antal_totalt = int(dsm_z.size)
    antal_ogiltiga = int(np.count_nonzero(ogiltig))
    if antal_totalt == 0 or antal_ogiltiga == antal_totalt:
        raise BatchFel(
            f"Dagsljus för {ruta_namn}: 100 % av cellerna ({antal_ogiltiga}/{antal_totalt}) "
            "saknar ändlig höjddata (NaN/inf); kan inte beräkna SVF."
        )

    if antal_ogiltiga > 0:
        z_min = float(np.min(dsm_z[giltig]))
        z_fylld = np.array(dsm_z, dtype=np.float64, copy=True)
        # Fyll lokalt under lägsta giltiga cell så saknade celler aldrig skapar falska skuggor
        z_fylld[ogiltig] = z_min - 1000.0
    else:
        z_fylld = dsm_z

    falt = Hojdfalt(
        z_m=z_fylld,
        cell_m=float(geo.pixelstorlek_x),
        x_min_m=float(geo.x_min),
        y_max_m=float(geo.y_max),
    )
    summa = np.zeros(dsm_z.shape, dtype=np.float64)
    for _k, _phi, h_rad, _tr in horisont_ruta_iter(
        falt, azimutriktningar, radie_m, enhet=enhet
    ):
        summa += np.sin(h_rad.astype(np.float64)) ** 2
    svf = np.clip(1.0 - summa / azimutriktningar, 0.0, 1.0).astype(np.float32)

    varningar: list[str] = []
    if antal_ogiltiga > 0:
        svf[ogiltig] = np.float32(np.nan)
        andel_pct = 100.0 * antal_ogiltiga / antal_totalt
        varningar.append(
            f"Dagsljus ({ruta_namn}): {antal_ogiltiga} av {antal_totalt} celler "
            f"({andel_pct:.1f} %) i höjdfältet har NaN/icke-ändliga värden; lokalt maskerade "
            "vid horisontberäkningen (antas inte skymma) och bevarade som NaN i svf.tif."
        )

    svf_giltig = svf[giltig]
    matvarden: dict[str, Any] = {
        "dagsljus_svf_medel": float(np.mean(svf_giltig)),
        "dagsljus_svf_min": float(np.min(svf_giltig)),
        "dagsljus_nan_celler": antal_ogiltiga,
        "dagsljus_nan_andel": round(antal_ogiltiga / antal_totalt, 6),
    }
    return svf, matvarden, tuple(varningar)


def skapa_k1_rutkorare(
    k1_rot: str | Path,
    *,
    regn_tid_s: Sequence[float] = (0.0, 600.0),
    regn_mm_h: Sequence[float] = (60.0, 60.0),
    slut_tid_s: float | None = None,
    azimutriktningar: int = 36,
    dagsljus_radie_m: float = 100.0,
    vegetation: bool = False,
) -> RutKorare:
    """Skapar en rutkörare som kör riktiga K1->vatten2d och K1->dagsljus per ruta.

    För varje ruta `mm_<E>_<N>` förväntas en K1-katalog `<k1_rot>/<ruta.namn>/`.
    - `vatten2d` läser `mark.tif`, `osakerhet.tif` och `markslag.tif` (samt valfria
      `brytlinjer.gpkg` / `oppningar.gpkg`).
    - `dagsljus` läser K1 v2-ytmodellen (DSM: `terrang.tif` + inbrända `byggnader.gpkg` via
      `karna.geometri.las_ytmodell`) när rutan är K1 v2, och faller tillbaka på `mark.tif`
      för bakåtkompatibla K1 v1-rutor. `NaN`-celler maskeras lokalt vid horisontberäkningen,
      bevaras som `NaN` i `svf.tif` och flaggas som varning i manifestet (100 % `NaN` ger fel).
    """
    rot = Path(k1_rot)

    def _kor(ruta: Ruta, arbetsmapp: Path, konfig: BatchKonfig, plats: str) -> RutUtdata:
        from gemensamt.cog import skriv_komprimerad_cog
        from tvad.lokal_troghet import kor_2d_lokal_troghet, skriv_tvad_resultat_k6
        from vatten.blaytor import BlaytorFel, k1_filer, las_k1

        k1_mapp = rot / ruta.namn
        if not k1_mapp.is_dir():
            raise BatchFel(f"K1-rutkatalog saknas för {ruta.namn}: {k1_mapp}")

        ut_poster: list[FilPost] = []
        matvarden: dict[str, Any] = {"berakningsplats": plats}
        varningar: list[str] = []
        k1: Any | None = None

        if "vatten2d" in konfig.moduler:
            try:
                k1 = las_k1(k1_filer(k1_mapp))
            except BlaytorFel as exc:
                raise BatchFel(f"Kunde inte läsa K1-ruta {ruta.namn}: {exc}") from exc
            v_mapp = arbetsmapp / "vatten2d"
            res_2d = kor_2d_lokal_troghet(
                k1.mark_z,
                k1.markslag,
                k1.geo,
                regn_tid_s,
                regn_mm_h,
                slut_tid_s=slut_tid_s,
                brytlinjer=k1.brytlinjer,
                enhet=konfig.enhet,
                precision=konfig.precision,
            )
            filer_k6 = skriv_tvad_resultat_k6(res_2d, k1.geo, v_mapp)
            for roll, p in sorted(filer_k6.items()):
                rel = str(p.relative_to(arbetsmapp))
                ut_poster.append(FilPost(roll=f"vatten2d_{roll}", fil=rel, sha256=sha256_fil(p)))
            matvarden["vatten2d_max_djup_m"] = float(np.nanmax(res_2d.max_djup_m))
            matvarden["vatten2d_volymfel_rel"] = float(res_2d.volymfel_rel)
            matvarden["vatten2d_berakningsplats"] = res_2d.berakningsplats

        if "dagsljus" in konfig.moduler:
            d_mapp = arbetsmapp / "dagsljus"
            d_mapp.mkdir(parents=True, exist_ok=True)
            dsm_z, dsm_geo, k1_ver = _las_dagsljus_dsm(
                k1_mapp, ruta.namn, k1_v1=k1, vegetation=vegetation
            )
            svf, svf_matvarden, svf_varningar = _berakna_svf_med_nan_hantering(
                dsm_z,
                dsm_geo,
                ruta.namn,
                azimutriktningar=azimutriktningar,
                radie_m=dagsljus_radie_m,
                enhet=konfig.enhet,
            )
            svf_fil = skriv_komprimerad_cog(
                d_mapp / "svf.tif",
                svf,
                x_min=float(dsm_geo.x_min),
                y_max=float(dsm_geo.y_max),
                pixelstorlek=float(dsm_geo.pixelstorlek_x),
                nodata=float("nan"),
            )
            ut_poster.append(
                FilPost(
                    roll="dagsljus_svf",
                    fil=str(svf_fil.relative_to(arbetsmapp)),
                    sha256=sha256_fil(svf_fil),
                )
            )
            matvarden.update(svf_matvarden)
            matvarden["dagsljus_k1_version"] = k1_ver
            matvarden["dagsljus_dsm_kalla"] = (
                "k1_v2_ytmodell" if k1_ver == 2 else "k1_v1_mark"
            )
            varningar.extend(svf_varningar)

        return RutUtdata(
            utdata_filer=tuple(ut_poster),
            matvarden=matvarden,
            varningar=tuple(varningar),
        )

    return _kor


_S3_K1_TILLATNA_FILER: frozenset[str] = frozenset(
    {
        "mark.tif",
        "terrang.tif",
        "markslag.tif",
        "osakerhet.tif",
        "byggnader.gpkg",
        "recept.json",
        "brytlinjer.gpkg",
        "oppningar.gpkg",
        "vegetation.tif",
    }
)
_S3_K1_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
_S3_K1_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_S3_K1_FORBJUDNA_SEGMENT: frozenset[str] = frozenset(
    {"intern", "skyddad", "dataklass=intern", "dataklass=skyddad"}
)


def skapa_s3_k1_rutkorare(
    objektlagring: ObjektLagring,
    kommun: str,
    *,
    regn_tid_s: Sequence[float] = (0.0, 600.0),
    regn_mm_h: Sequence[float] = (60.0, 60.0),
    slut_tid_s: float | None = None,
    azimutriktningar: int = 36,
    dagsljus_radie_m: float = 100.0,
    vegetation: bool = False,
    tmp_rot: str | Path | None = None,
) -> RutKorare:
    """Skapar en rutkörare som hämtar öppna K1-rutor från objektlagring och kör vatten2d/dagsljus.

    För varje ruta `mm_<E>_<N>`:
    1. Laddar ner `geografi/<kommun>/<rut_id>/k1/k1_manifest.json` till en isolerad temporär mapp.
    2. Verifierar `dataklass == "oppen"`, matchande `kommun`/`rut_id`, vitlistade filnamn samt
       SHA-256 på samtliga K1-filer före beräkning (fail-closed mot icke-öppen eller korrupt data).
    3. Kör `skapa_k1_rutkorare` på den temporära K1-mappen så att `vatten2d/max_djup.tif`,
       `dagsljus/svf.tif` m.fl. skrivs till `arbetsmapp` (laddas upp till S3 av `kor_batch_rutor`).
    4. Raderar alltid den temporära K1-katalogen i `finally` (även vid fel).
    """
    ren_kommun = kommun.strip()
    if (
        not ren_kommun
        or "/" in ren_kommun
        or "\\" in ren_kommun
        or ".." in ren_kommun
        or not _S3_K1_SEGMENT_RE.match(ren_kommun)
        or ren_kommun.lower() in _S3_K1_FORBJUDNA_SEGMENT
    ):
        raise BatchFel(f"Ogiltig kommun för S3 K1-rutkörare: {kommun!r}.")

    def _kor(ruta: Ruta, arbetsmapp: Path, konfig: BatchKonfig, plats: str) -> RutUtdata:
        kontrollera_dataskydd(konfig.dataklass)
        if konfig.dataklass != "oppen":
            raise BatchFel(
                f"Dataskyddsspärr: S3 K1-rutkörare tillåter endast dataklass='oppen', "
                f"fick {konfig.dataklass!r}."
            )
        ren_ruta = ruta.namn.strip()
        if not _S3_K1_SEGMENT_RE.match(ren_ruta) or ".." in ren_ruta:
            raise BatchFel(f"Ogiltigt rut-id för S3 K1-hämtning: {ruta.namn!r}.")

        bas_str: str | None = None
        if tmp_rot is not None:
            bas_p = Path(tmp_rot)
            bas_p.mkdir(parents=True, exist_ok=True)
            bas_str = str(bas_p)
        k1_tmp_rot = Path(tempfile.mkdtemp(prefix=f"floden_s3k1_{ren_ruta}_", dir=bas_str))
        try:
            k1_mapp = k1_tmp_rot / ren_ruta
            k1_mapp.mkdir(parents=True, exist_ok=True)
            manifest_nyckel = geografi_nyckel(ren_kommun, ren_ruta, "k1/k1_manifest.json")
            if not objektlagring.finns(manifest_nyckel):
                raise BatchFel(
                    f"K1-manifest saknas i objektlagring för {ren_kommun}/{ren_ruta}: "
                    f"{manifest_nyckel}"
                )
            manifest_fil = k1_mapp / "k1_manifest.json"
            try:
                objektlagring.ladda_ner_fil(manifest_nyckel, manifest_fil)
                manifest_data = json.loads(manifest_fil.read_text(encoding="utf-8"))
            except (ObjektLagringFel, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BatchFel(
                    f"Kunde inte läsa K1-manifest {manifest_nyckel}: {exc}"
                ) from exc

            if not isinstance(manifest_data, Mapping):
                raise BatchFel(f"Ogiltigt K1-manifest (ej JSON-objekt): {manifest_nyckel}")

            dataklass = manifest_data.get("dataklass")
            if str(dataklass).strip().lower() != "oppen":
                raise BatchFel(
                    f"Dataskyddsspärr: K1-manifest för {ren_kommun}/{ren_ruta} har "
                    f"dataklass={dataklass!r}; endast 'oppen' tillåts."
                )

            man_kommun = manifest_data.get("kommun")
            if man_kommun is not None and str(man_kommun).strip() != ren_kommun:
                raise BatchFel(
                    f"K1-manifestets kommun ({man_kommun!r}) stämmer inte med förväntad "
                    f"kommun ({ren_kommun!r})."
                )
            man_ruta = manifest_data.get("rut_id")
            if man_ruta is not None and str(man_ruta).strip() != ren_ruta:
                raise BatchFel(
                    f"K1-manifestets rut_id ({man_ruta!r}) stämmer inte med förväntad "
                    f"ruta ({ren_ruta!r})."
                )

            filer_raw = manifest_data.get("filer")
            if not isinstance(filer_raw, list) or not filer_raw:
                raise BatchFel(f"K1-manifestet saknar fillista: {manifest_nyckel}")

            k1_mapp_abs = k1_mapp.resolve()
            nedladdade_namn: set[str] = set()
            for post in filer_raw:
                if not isinstance(post, Mapping):
                    raise BatchFel(f"Ogiltig filpost i K1-manifest {manifest_nyckel}.")
                filnamn = post.get("fil")
                forvantad_sha = post.get("sha256")
                if not isinstance(filnamn, str) or filnamn not in _S3_K1_TILLATNA_FILER:
                    raise BatchFel(
                        f"Otillåtet filnamn i K1-manifest {manifest_nyckel}: {filnamn!r}"
                    )
                if not isinstance(forvantad_sha, str) or not _S3_K1_SHA256_RE.match(
                    forvantad_sha.strip()
                ):
                    raise BatchFel(
                        f"Ogiltig SHA-256 i K1-manifest {manifest_nyckel} för {filnamn!r}."
                    )
                mal_fil = (k1_mapp / filnamn).resolve()
                if not mal_fil.is_relative_to(k1_mapp_abs):
                    raise BatchFel(f"Säkerhetsfel: ogiltig målsökväg för {filnamn!r}.")
                fil_nyckel = geografi_nyckel(ren_kommun, ren_ruta, f"k1/{filnamn}")
                try:
                    objektlagring.ladda_ner_fil(fil_nyckel, mal_fil)
                except ObjektLagringFel as exc:
                    raise BatchFel(
                        f"Kunde inte hämta K1-fil {fil_nyckel} från objektlagring: {exc}"
                    ) from exc
                faktisk_sha = sha256_fil(mal_fil).lower()
                if faktisk_sha != forvantad_sha.strip().lower():
                    raise BatchFel(
                        f"SHA-256-avvikelse för K1-fil {fil_nyckel}: "
                        f"förväntade {forvantad_sha[:12]}…, fick {faktisk_sha[:12]}…."
                    )
                nedladdade_namn.add(filnamn)

            if "mark.tif" not in nedladdade_namn:
                raise BatchFel(
                    f"K1-manifestet för {ren_kommun}/{ren_ruta} saknar obligatorisk 'mark.tif'."
                )

            lokal_korare = skapa_k1_rutkorare(
                k1_tmp_rot,
                regn_tid_s=regn_tid_s,
                regn_mm_h=regn_mm_h,
                slut_tid_s=slut_tid_s,
                azimutriktningar=azimutriktningar,
                dagsljus_radie_m=dagsljus_radie_m,
                vegetation=vegetation,
            )
            ut = lokal_korare(ruta, arbetsmapp, konfig, plats)
            matvarden = dict(ut.matvarden)
            matvarden["k1_kalla"] = "s3"
            matvarden["k1_s3_manifest_sha256"] = sha256_fil(manifest_fil)
            return RutUtdata(
                utdata_filer=ut.utdata_filer,
                matvarden=matvarden,
                varningar=ut.varningar,
            )
        finally:
            shutil.rmtree(k1_tmp_rot, ignore_errors=True)

    return _kor


def _ersatt_katalog_atomiskt(tmp_mapp: Path, mal_mapp: Path) -> None:
    """Flyttar `tmp_mapp` till `mal_mapp` så `mal_mapp` aldrig hamnar i halvfärdigt skick."""
    gammal: Path | None = None
    if mal_mapp.exists():
        gammal = mal_mapp.parent / f".old_{mal_mapp.name}_{os.getpid()}_{threading.get_ident()}"
        if gammal.exists():
            shutil.rmtree(gammal)
        os.replace(mal_mapp, gammal)
    try:
        os.replace(tmp_mapp, mal_mapp)
    except Exception:
        if gammal is not None and gammal.exists() and not mal_mapp.exists():
            os.replace(gammal, mal_mapp)
        raise
    finally:
        if gammal is not None and gammal.exists():
            shutil.rmtree(gammal, ignore_errors=True)


def _ladda_upp_ruta_till_objektlagring(
    objektlagring: ObjektLagring,
    kommun: str,
    ruta_namn: str,
    arbetsmapp: Path,
    utdata_poster: Sequence[FilPost],
) -> None:
    """Laddar upp alla rutfiler verifierat med SHA-256 och avslutar med `run.json` sist."""
    uppladdade_rel: set[str] = set()
    for post in utdata_poster:
        lokal_fil = arbetsmapp / post.fil
        nyckel = geografi_nyckel(kommun, ruta_namn, post.fil)
        objektlagring.ladda_upp_fil(lokal_fil, nyckel, sha256=post.sha256)
        uppladdade_rel.add(post.fil)

    recept_fil = arbetsmapp / "recept.json"
    if recept_fil.is_file() and "recept.json" not in uppladdade_rel:
        objektlagring.ladda_upp_fil(
            recept_fil,
            geografi_nyckel(kommun, ruta_namn, "recept.json"),
            sha256=sha256_fil(recept_fil),
        )

    run_fil = arbetsmapp / "run.json"
    objektlagring.ladda_upp_fil(
        run_fil,
        geografi_nyckel(kommun, ruta_namn, "run.json"),
        sha256=sha256_fil(run_fil),
    )


@dataclass(frozen=True)
class _RutUtfall:
    """Internt utfall för en enskild ruta i `kor_batch_rutor`."""

    idx: int
    ruta_namn: str
    status: Literal["klar", "hoppad", "fel"]
    kortid_s: float = 0.0
    felmeddelande: str | None = None
    undantag: Exception | None = None


def kor_batch_rutor(
    rutor: Sequence[Ruta],
    konfig: BatchKonfig,
    *,
    rutkorare: RutKorare | None = None,
    objektlagring: ObjektLagring | None = None,
    miljo: Mapping[str, str] | None = None,
    avbryt_vid_fel: bool = False,
) -> BatchStatus:
    """Kör förberäkning över en sekvens av rutor med återupptagning och atomisk skrivning.

    - Kontrollerar alltid dataskyddsspärren (`kontrollera_dataskydd`) före start.
    - Hoppar över rutor vars `run.json` redan finns i `objektlagring` (om angiven) eller lokalt
      med matchande indata, parametrar och beräkningsplats.
    - Vid `objektlagring`: kör rutan i en isolerad temporär katalog (`temporar_rutkatalog`),
      laddar upp och verifierar alla färdiga filer + `run.json`, och raderar temporär rådata
      direkt i `finally` (om inte `konfig.behall_radata=True`).
    - När `konfig.max_arbetare <= 1`: ren sekventiell körning utan extra trådar.
    - När `konfig.max_arbetare > 1`: parallell exekvering via `ThreadPoolExecutor` med
      trådsäker statusuppdatering och deterministisk rutordning i returnerad `BatchStatus`.
    - Uppdaterar `batch_status.json` atomiskt efter varje ruta.
    """
    kontrollera_dataskydd(konfig.dataklass, miljo=miljo)
    _xp, enhet_namn = hamta_backend(konfig.enhet)
    dtyp = np.float32 if konfig.precision == "f32" else np.float64
    plats = berakningsplats(enhet_namn, dtyp)
    kodversion = hamta_kodversion()

    konfig.utdata_rot.mkdir(parents=True, exist_ok=True)
    indata_poster = [
        FilPost(roll=f"indata_{i}", fil=str(p), sha256=sha256_fil(p))
        for i, p in enumerate(konfig.indata_filer)
        if p.is_file()
    ]

    korare = rutkorare or _standard_rutkorare
    klara_med_idx: list[tuple[int, str]] = []
    hoppade_med_idx: list[tuple[int, str]] = []
    felade_med_idx: list[tuple[int, str, str]] = []
    tider_nyberaknade: list[float] = []
    t_batch_start = time.monotonic()
    totalt = len(rutor)
    effektiva_arbetare = max(1, min(konfig.max_arbetare, totalt)) if totalt > 0 else 1
    status_lock = threading.Lock()
    avbryt_event = threading.Event()

    def _bygg_status_olast() -> BatchStatus:
        bearbetade = len(klara_med_idx) + len(hoppade_med_idx) + len(felade_med_idx)
        aterstaende = max(0, totalt - bearbetade)
        snitt = (
            sum(tider_nyberaknade) / len(tider_nyberaknade) if tider_nyberaknade else 0.0
        )
        klara_ord = tuple(namn for _, namn in sorted(klara_med_idx, key=lambda x: x[0]))
        hoppade_ord = tuple(namn for _, namn in sorted(hoppade_med_idx, key=lambda x: x[0]))
        felade_ord = {
            namn: msg for _, namn, msg in sorted(felade_med_idx, key=lambda x: x[0])
        }
        return BatchStatus(
            totalt_rutor=totalt,
            klara=klara_ord,
            hoppade=hoppade_ord,
            felade=felade_ord,
            aterstaende=aterstaende,
            snittid_s=snitt,
            estimerad_aterstaende_s=(snitt * aterstaende) / effektiva_arbetare,
            total_tid_s=time.monotonic() - t_batch_start,
            berakningsplats=plats,
            moduler=konfig.moduler,
            dataklass=konfig.dataklass,
            uppdaterad=datetime.now().astimezone().isoformat(timespec="seconds"),
        )

    def _registrera_utfall(utfall: _RutUtfall) -> None:
        with status_lock:
            if utfall.status == "klar":
                klara_med_idx.append((utfall.idx, utfall.ruta_namn))
                tider_nyberaknade.append(utfall.kortid_s)
            elif utfall.status == "hoppad":
                hoppade_med_idx.append((utfall.idx, utfall.ruta_namn))
            else:
                felade_med_idx.append(
                    (utfall.idx, utfall.ruta_namn, utfall.felmeddelande or "Okänt fel")
                )
            _skriv_status_atomiskt(_bygg_status_olast(), konfig.utdata_rot)

    def _kor_en_ruta(idx: int, ruta: Ruta) -> _RutUtfall | None:
        if avbryt_vid_fel and avbryt_event.is_set():
            return None

        rutamapp = konfig.utdata_rot / ruta.namn
        nyckel = berakna_batch_nyckel(ruta, konfig, indata_poster, plats)

        if objektlagring is not None:
            try:
                if ruta_ar_klar_i_objektlagring(objektlagring, konfig.kommun, ruta.namn, nyckel):
                    return _RutUtfall(idx=idx, ruta_namn=ruta.namn, status="hoppad")

                t0 = time.monotonic()
                with temporar_rutkatalog(ruta.namn, behall=konfig.behall_radata) as tmp_mapp:
                    ut = korare(ruta, tmp_mapp, konfig, plats)
                    manifest = skapa_manifest(
                        modul="forberakna",
                        indata=indata_poster,
                        parametrar={
                            "ruta_id": ruta.namn,
                            "kommun": konfig.kommun,
                            "moduler": list(konfig.moduler),
                            "enhet": konfig.enhet,
                            "precision": konfig.precision,
                            "berakningsplats": plats,
                            "dataklass": konfig.dataklass,
                            "batch_nyckel": nyckel,
                            **dict(konfig.parametrar),
                        },
                        utdata=list(ut.utdata_filer),
                        matvarden={
                            "kortid_s": round(time.monotonic() - t0, 4),
                            **dict(ut.matvarden),
                        },
                        varningar=list(ut.varningar),
                        kodversion=kodversion,
                    )
                    skriv_manifest(manifest, tmp_mapp / "run.json")
                    _ladda_upp_ruta_till_objektlagring(
                        objektlagring,
                        konfig.kommun,
                        ruta.namn,
                        tmp_mapp,
                        ut.utdata_filer,
                    )
                return _RutUtfall(
                    idx=idx,
                    ruta_namn=ruta.namn,
                    status="klar",
                    kortid_s=time.monotonic() - t0,
                )
            except Exception as exc:  # noqa: BLE001 - logga rutfelet och fortsätt om avbryt_vid_fel=False
                return _RutUtfall(
                    idx=idx,
                    ruta_namn=ruta.namn,
                    status="fel",
                    felmeddelande=f"{type(exc).__name__}: {exc}",
                    undantag=exc,
                )

        if ruta_ar_klar(rutamapp, nyckel):
            return _RutUtfall(idx=idx, ruta_namn=ruta.namn, status="hoppad")

        tmp_mapp = konfig.utdata_rot / f".tmp_{ruta.namn}_{os.getpid()}_{idx}"
        if tmp_mapp.exists():
            shutil.rmtree(tmp_mapp)
        tmp_mapp.mkdir(parents=True, exist_ok=True)

        t0 = time.monotonic()
        try:
            ut = korare(ruta, tmp_mapp, konfig, plats)
            manifest = skapa_manifest(
                modul="forberakna",
                indata=indata_poster,
                parametrar={
                    "ruta_id": ruta.namn,
                    "moduler": list(konfig.moduler),
                    "enhet": konfig.enhet,
                    "precision": konfig.precision,
                    "berakningsplats": plats,
                    "dataklass": konfig.dataklass,
                    "batch_nyckel": nyckel,
                    **dict(konfig.parametrar),
                },
                utdata=list(ut.utdata_filer),
                matvarden={
                    "kortid_s": round(time.monotonic() - t0, 4),
                    **dict(ut.matvarden),
                },
                varningar=list(ut.varningar),
                kodversion=kodversion,
            )
            skriv_manifest(manifest, tmp_mapp / "run.json")
            _ersatt_katalog_atomiskt(tmp_mapp, rutamapp)
            return _RutUtfall(
                idx=idx,
                ruta_namn=ruta.namn,
                status="klar",
                kortid_s=time.monotonic() - t0,
            )
        except Exception as exc:  # noqa: BLE001 - logga rutfelet och fortsätt om avbryt_vid_fel=False
            if not konfig.behall_radata:
                shutil.rmtree(tmp_mapp, ignore_errors=True)
            return _RutUtfall(
                idx=idx,
                ruta_namn=ruta.namn,
                status="fel",
                felmeddelande=f"{type(exc).__name__}: {exc}",
                undantag=exc,
            )

    if konfig.max_arbetare <= 1 or totalt <= 1:
        for idx, ruta in enumerate(rutor):
            utfall = _kor_en_ruta(idx, ruta)
            if utfall is None:
                continue
            _registrera_utfall(utfall)
            if utfall.undantag is not None and avbryt_vid_fel:
                raise utfall.undantag
    else:
        def _arbetare(idx: int, ruta: Ruta) -> _RutUtfall | None:
            utfall = _kor_en_ruta(idx, ruta)
            if utfall is None:
                return None
            _registrera_utfall(utfall)
            if utfall.undantag is not None and avbryt_vid_fel:
                avbryt_event.set()
            return utfall

        forsta_fel: tuple[int, Exception] | None = None
        with ThreadPoolExecutor(
            max_workers=effektiva_arbetare,
            thread_name_prefix="floden-batch",
        ) as pool:
            futures = [pool.submit(_arbetare, idx, ruta) for idx, ruta in enumerate(rutor)]
            for fut in as_completed(futures):
                if fut.cancelled():
                    continue
                utfall = fut.result()
                if utfall is not None and utfall.undantag is not None and avbryt_vid_fel:
                    avbryt_event.set()
                    if forsta_fel is None or utfall.idx < forsta_fel[0]:
                        forsta_fel = (utfall.idx, utfall.undantag)
                    for annan in futures:
                        annan.cancel()
        if forsta_fel is not None:
            raise forsta_fel[1]

    with status_lock:
        slutstatus = _bygg_status_olast()
        _skriv_status_atomiskt(slutstatus, konfig.utdata_rot)
    return slutstatus
