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
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
from shapely.geometry import box, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform
from shapely.ops import unary_union

from gemensamt.gpu import berakningsplats, hamta_backend
from gemensamt.hash import sha256_fil
from gemensamt.koordinater import transformera
from gemensamt.manifest import FilPost, ManifestFel, las_manifest, skapa_manifest, skriv_manifest
from markmodell.rutnat import Ruta, skapa_rutnat

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
    nyckeldata = {
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


def _skriv_status_atomiskt(status: BatchStatus, utdata_rot: Path) -> Path:
    utdata_rot.mkdir(parents=True, exist_ok=True)
    mal = utdata_rot / "batch_status.json"
    tmp = utdata_rot / f".batch_status.{os.getpid()}.tmp"
    tmp.write_text(
        json.dumps(status.till_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, mal)
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


def _ersatt_katalog_atomiskt(tmp_mapp: Path, mal_mapp: Path) -> None:
    """Flyttar `tmp_mapp` till `mal_mapp` så `mal_mapp` aldrig hamnar i halvfärdigt skick."""
    gammal: Path | None = None
    if mal_mapp.exists():
        gammal = mal_mapp.parent / f".old_{mal_mapp.name}_{os.getpid()}"
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


def kor_batch_rutor(
    rutor: Sequence[Ruta],
    konfig: BatchKonfig,
    *,
    rutkorare: RutKorare | None = None,
    miljo: Mapping[str, str] | None = None,
    avbryt_vid_fel: bool = False,
) -> BatchStatus:
    """Kör förberäkning över en sekvens av rutor med återupptagning och atomisk skrivning.

    - Kontrollerar alltid dataskyddsspärren (`kontrollera_dataskydd`) före start.
    - Hoppar över rutor vars `run.json` redan matchar indata, parametrar och beräkningsplats.
    - Skriver varje ruta till en temporär katalog och byter in den atomiskt först när hela
      rutan och dess `run.json` (K0) är klara.
    - Uppdaterar `batch_status.json` atomiskt efter varje ruta.
    """
    kontrollera_dataskydd(konfig.dataklass, miljo=miljo)
    _xp, enhet_namn = hamta_backend(konfig.enhet)
    dtyp = np.float32 if konfig.precision == "f32" else np.float64
    plats = berakningsplats(enhet_namn, dtyp)

    konfig.utdata_rot.mkdir(parents=True, exist_ok=True)
    indata_poster = [
        FilPost(roll=f"indata_{i}", fil=str(p), sha256=sha256_fil(p))
        for i, p in enumerate(konfig.indata_filer)
        if p.is_file()
    ]

    korare = rutkorare or _standard_rutkorare
    klara: list[str] = []
    hoppade: list[str] = []
    felade: dict[str, str] = {}
    tider_nyberaknade: list[float] = []
    t_batch_start = time.monotonic()
    totalt = len(rutor)

    def _bygg_status() -> BatchStatus:
        bearbetade = len(klara) + len(hoppade) + len(felade)
        aterstaende = max(0, totalt - bearbetade)
        snitt = (
            sum(tider_nyberaknade) / len(tider_nyberaknade) if tider_nyberaknade else 0.0
        )
        return BatchStatus(
            totalt_rutor=totalt,
            klara=tuple(klara),
            hoppade=tuple(hoppade),
            felade=dict(felade),
            aterstaende=aterstaende,
            snittid_s=snitt,
            estimerad_aterstaende_s=snitt * aterstaende,
            total_tid_s=time.monotonic() - t_batch_start,
            berakningsplats=plats,
            moduler=konfig.moduler,
            dataklass=konfig.dataklass,
            uppdaterad=datetime.now().astimezone().isoformat(timespec="seconds"),
        )

    for idx, ruta in enumerate(rutor):
        rutamapp = konfig.utdata_rot / ruta.namn
        nyckel = berakna_batch_nyckel(ruta, konfig, indata_poster, plats)

        if ruta_ar_klar(rutamapp, nyckel):
            hoppade.append(ruta.namn)
            _skriv_status_atomiskt(_bygg_status(), konfig.utdata_rot)
            continue

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
            )
            skriv_manifest(manifest, tmp_mapp / "run.json")
            _ersatt_katalog_atomiskt(tmp_mapp, rutamapp)
            tider_nyberaknade.append(time.monotonic() - t0)
            klara.append(ruta.namn)
        except Exception as exc:  # noqa: BLE001 - logga rutfelet och fortsätt om avbryt_vid_fel=False
            shutil.rmtree(tmp_mapp, ignore_errors=True)
            felade[ruta.namn] = f"{type(exc).__name__}: {exc}"
            _skriv_status_atomiskt(_bygg_status(), konfig.utdata_rot)
            if avbryt_vid_fel:
                raise
        else:
            _skriv_status_atomiskt(_bygg_status(), konfig.utdata_rot)

    slutstatus = _bygg_status()
    _skriv_status_atomiskt(slutstatus, konfig.utdata_rot)
    return slutstatus
