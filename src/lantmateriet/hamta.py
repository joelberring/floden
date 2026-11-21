"""Hämta laser, ortofoto och byggnader för ett provområde och skriv ett manifest.

Protokoll och logik porterade från O-Tid (MIT, joelberring/otid @ 4a446af); se `klient.py` och
`stac.py`. Allt som hämtas hamnar i en lokal datamapp (aldrig i repot) och beskrivs i
`lantmateriet_manifest.json` med sha256 per fil. Manifestet är deterministiskt (inga tider,
inga nycklar) så att dess sha256 kan ligga i ett recept.

Idempotent: en fil hoppas över om den finns med samma url och storlek och samma ändringstid som
senast (annars räknas sha256 om och jämförs); en delvis hämtad fil (`.part`) återupptas.
"""

from __future__ import annotations

import json
import logging
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import shapely
from shapely.geometry.base import BaseGeometry

from data_inlasning.cache import DataInlasningFel
from gemensamt.hash import sha256_fil
from lantmateriet.klient import ATTRIBUTION, LantmaterietFel, LmKlient
from lantmateriet.omrade import Omrade
from lantmateriet.stac import (
    ROLL_BYGGNADER,
    ROLL_LASER,
    ROLL_ORTO,
    StacObjekt,
    geometri_till_wgs84,
    lista_samlingar,
    nyaste_per_ruta,
    sok,
    tacka_nyast_forst,
    valj_samling,
    wgs84_bbox,
)

logg = logging.getLogger("floden.lantmateriet")

MANIFEST_NAMN = "lantmateriet_manifest.json"
TILLSTAND_NAMN = ".lantmateriet_tillstand.json"
MANIFEST_KONTRAKT = "LM-hamtning/v1"
KLIPPT_BYGGNADER = "byggnader_klippt.gpkg"

VILLKOR = {
    ROLL_LASER: (
        "Laserdata Nedladdning, skog: öppna data, CC0 enligt Lantmäteriets produktsida "
        "(O-Tids dokumentation anger CC BY 4.0). Ange ©Lantmäteriet. Punktmoln kan innehålla "
        "personuppgifter enligt villkoren. Kontrollera användarvillkoren."
    ),
    ROLL_ORTO: "Ortofoto Nedladdning: kontrollera användarvillkoren; ange ©Lantmäteriet.",
    ROLL_BYGGNADER: "Byggnad (vektor): kontrollera användarvillkoren; ange ©Lantmäteriet.",
}


@dataclass(frozen=True)
class HamtadFil:
    roll: str
    samling: str
    item_id: str
    fil: str  # relativt datamappen, med snedstreck
    url: str  # utan fråga och användaruppgifter
    storlek: int
    sha256: str
    tidpunkt: str | None
    epsg: int | None
    bbox_wgs84: tuple[float, float, float, float] | None

    def till_dict(self) -> dict[str, Any]:
        return {
            "roll": self.roll,
            "samling": self.samling,
            "item_id": self.item_id,
            "fil": self.fil,
            "url": self.url,
            "storlek": self.storlek,
            "sha256": self.sha256,
            "tidpunkt": self.tidpunkt,
            "epsg": self.epsg,
            "bbox_wgs84": list(self.bbox_wgs84) if self.bbox_wgs84 else None,
        }


@dataclass
class HamtResultat:
    manifest: Path
    manifest_sha256: str
    filer: list[HamtadFil]
    hamtade: int = 0
    hoppade: int = 0
    varningar: list[str] = field(default_factory=list)
    byggnader_klippt: Path | None = None


def _filnamn(obj: StacObjekt) -> str:
    namn = PurePosixPath(urlsplit(obj.href).path).name
    if not namn or namn in (".", ".."):
        raise LantmaterietFel(f"Kan inte härleda ett filnamn ur adressen för {obj.id}.")
    return namn


def _utan_fraga(url: str) -> str:
    d = urlsplit(url)
    port = f":{d.port}" if d.port else ""
    return f"{d.scheme}://{d.hostname}{port}{d.path}"


def _las_tillstand(ut: Path) -> dict[str, Any]:
    p = ut / TILLSTAND_NAMN
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def _skriv_tillstand(ut: Path, t: dict[str, Any]) -> None:
    (ut / TILLSTAND_NAMN).write_text(
        json.dumps(t, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )


def _hamta_en(
    klient: LmKlient,
    obj: StacObjekt,
    ut: Path,
    tillstand: dict[str, Any],
    skriv: Callable[[str], None],
) -> tuple[HamtadFil, bool]:
    """Hämtar (eller hoppar över) en fil. Returnerar posten och om den hämtades nu."""
    rel = f"{obj.roll}/{_filnamn(obj)}"
    mal = ut / rel
    url = _utan_fraga(obj.href)
    t = tillstand.get(rel)
    sha: str | None = None
    if mal.is_file():
        st = mal.stat()
        if isinstance(t, dict) and t.get("url") == url and t.get("storlek") == st.st_size:
            if t.get("mtime_ns") == st.st_mtime_ns:
                sha = str(t["sha256"])
            else:
                ny = sha256_fil(mal)
                if ny == t.get("sha256"):
                    sha = ny
        elif obj.storlek is not None and st.st_size == obj.storlek:
            sha = sha256_fil(mal)  # fil på plats utan tillstånd, rätt storlek: anta den
    nyss = sha is None
    if sha is None:
        skriv(f"Hämtar {rel}" + (f" ({obj.storlek / 1e6:.1f} MB)" if obj.storlek else ""))
        sha, storlek = klient.hamta_fil(obj.href, mal, forvantad_storlek=obj.storlek)
    else:
        skriv(f"Hoppar över {rel} (finns, samma storlek och sha256)")
    st = mal.stat()
    tillstand[rel] = {
        "url": url,
        "storlek": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "sha256": sha,
    }
    return (
        HamtadFil(
            roll=obj.roll,
            samling=obj.samling,
            item_id=obj.id,
            fil=rel,
            url=url,
            storlek=st.st_size,
            sha256=sha,
            tidpunkt=obj.tidpunkt,
            epsg=obj.epsg,
            bbox_wgs84=obj.bbox_wgs84,
        ),
        nyss,
    )


def skriv_manifest(
    ut: Path,
    omrade: Omrade,
    marginal_m: float,
    samlingar: dict[str, str],
    filer: list[HamtadFil],
) -> tuple[Path, str]:
    """Skriver det deterministiska manifestet; returnerar sökväg och dess sha256."""
    data = {
        "kontrakt": MANIFEST_KONTRAKT,
        "kalla": "Lantmäteriet (stac-hojd, stac-bild, stac-vektor)",
        "attribution": "©Lantmäteriet",
        "protokoll": ATTRIBUTION,
        "villkor": {r: VILLKOR[r] for r in sorted({f.roll for f in filer})},
        "omrade": {
            "fil": omrade.fil.name,
            "sha256": omrade.sha256,
            "bbox_epsg3011": list(omrade.bbox),
            "bbox_wgs84": list(wgs84_bbox(omrade.polygon)),
            "marginal_m": marginal_m,
        },
        "samlingar": dict(sorted(samlingar.items())),
        "filer": [f.till_dict() for f in sorted(filer, key=lambda x: (x.roll, x.fil))],
    }
    p = ut / MANIFEST_NAMN
    p.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return p, sha256_fil(p)


def las_manifest_lm(ut: Path) -> dict[str, Any]:
    p = ut / MANIFEST_NAMN
    if not p.is_file():
        raise DataInlasningFel(
            f"{p} finns inte. Hämta först: floden data lantmateriet --omrade ... --ut {ut}"
        )
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("kontrakt") != MANIFEST_KONTRAKT:
        raise DataInlasningFel(f"{p} är inte ett manifest av typen {MANIFEST_KONTRAKT}.")
    return data


def hamta_omrade(
    klient: LmKlient,
    omrade: Omrade,
    ut: str | Path,
    *,
    laser: bool = True,
    orto: bool = False,
    byggnader: bool = False,
    laser_samling: str | None = None,
    orto_samling: str | None = None,
    byggnads_samling: str | None = None,
    byggnads_lager: str | None = None,
    marginal_m: float = 50.0,
    skriv: Callable[[str], None] = print,
) -> HamtResultat:
    """Söker och hämtar valda produkter för området. Se modulens text."""
    if not (laser or orto or byggnader):
        raise DataInlasningFel("Välj minst en produkt: --laser, --orto eller --byggnader.")
    mapp = Path(ut)
    mapp.mkdir(parents=True, exist_ok=True)
    sokyta = omrade.polygon.buffer(marginal_m)
    bbox = wgs84_bbox(sokyta)
    sokyta_wgs = geometri_till_wgs84(sokyta)
    tillstand = _las_tillstand(mapp)
    filer: list[HamtadFil] = []
    samlingar: dict[str, str] = {}
    res = HamtResultat(manifest=mapp / MANIFEST_NAMN, manifest_sha256="", filer=filer)

    plan: list[tuple[str, str, str | None]] = []
    if laser:
        plan.append((ROLL_LASER, klient.tjanster.hojd, laser_samling))
    if orto:
        plan.append((ROLL_ORTO, klient.tjanster.bild, orto_samling))
    if byggnader:
        plan.append((ROLL_BYGGNADER, klient.tjanster.vektor, byggnads_samling))

    for roll, bas, tvingad in plan:
        samling = valj_samling(lista_samlingar(klient, bas), roll, tvingad=tvingad)
        samlingar[roll] = samling
        skriv(f"{roll}: samling '{samling}'")
        träffar = list(sok(klient, bas, bbox, samling, roll))
        valda = _valj(roll, träffar, sokyta_wgs)
        if not valda:
            raise LantmaterietFel(
                f"Inga {roll}-filer i samlingen '{samling}' för området. Är området i Sverige, "
                "och är produkten beställd för kontot?"
            )
        skriv(f"{roll}: {len(träffar)} träffar, {len(valda)} filer väljs")
        for obj in valda:
            post, nyss = _hamta_en(klient, obj, mapp, tillstand, skriv)
            filer.append(post)
            if nyss:
                res.hamtade += 1
            else:
                res.hoppade += 1
            _skriv_tillstand(mapp, tillstand)

    if byggnader:
        res.byggnader_klippt = klipp_byggnader(
            mapp,
            [f for f in filer if f.roll == ROLL_BYGGNADER],
            omrade.polygon,
            marginal_m=marginal_m,
            lager=byggnads_lager,
            varningar=res.varningar,
        )
    res.manifest, res.manifest_sha256 = skriv_manifest(mapp, omrade, marginal_m, samlingar, filer)
    return res


def _valj(roll: str, traffar: list[StacObjekt], yta_wgs: BaseGeometry) -> list[StacObjekt]:
    if roll == ROLL_LASER:
        return nyaste_per_ruta(traffar)
    if roll == ROLL_ORTO:
        return tacka_nyast_forst(traffar, yta_wgs)
    # Byggnader: kommunfiler, högst tre som O-Tid (EXTRA_LIMIT), nyaste först per id
    unika = {o.id: o for o in traffar}
    return sorted(unika.values(), key=lambda o: o.id)[:3]


def klipp_byggnader(
    mapp: Path,
    zippar: list[HamtadFil],
    polygon: BaseGeometry,
    *,
    marginal_m: float,
    lager: str | None,
    varningar: list[str],
) -> Path | None:
    """Packar upp GeoPackage ur zip-filerna och klipper byggnaderna till området (EPSG:3011).

    Skriver `byggnader/byggnader_klippt.gpkg` (lager `byggnader`). Idempotent: görs inte om
    när zip-filernas sha256 och området är desamma som senast.
    """
    import geopandas as gpd
    import pandas as pd
    import pyogrio

    utmapp = mapp / "byggnader"
    mal = utmapp / KLIPPT_BYGGNADER
    nyckel = json.dumps(
        {
            "zip": sorted(z.sha256 for z in zippar),
            "yta": shapely.to_wkt(polygon, rounding_precision=2),
            "marginal": marginal_m,
            "lager": lager,
        },
        sort_keys=True,
    )
    nyckelfil = utmapp / ".klippt_nyckel"
    if mal.is_file() and nyckelfil.is_file() and nyckelfil.read_text(encoding="utf-8") == nyckel:
        return mal
    ram: list[Any] = []
    yta = polygon.buffer(marginal_m)
    for z in zippar:
        zpath = mapp / z.fil
        packa = utmapp / zpath.stem
        if packa.exists():
            shutil.rmtree(packa)
        packa.mkdir(parents=True)
        with zipfile.ZipFile(zpath) as zf:
            for info in zf.infolist():
                namn = PurePosixPath(info.filename)
                if namn.is_absolute() or ".." in namn.parts:
                    raise LantmaterietFel(f"Osäker sökväg i zip-filen: {info.filename}")
                if info.filename.lower().endswith(".gpkg"):
                    zf.extract(info, packa)
        for gpkg in sorted(packa.rglob("*.gpkg")):
            lagerlista = pyogrio.list_layers(gpkg)
            poly = [
                (str(n), str(g))
                for n, g in lagerlista
                if g is not None and "polygon" in str(g).lower()
            ]
            if lager:
                val = [n for n, _ in poly if n == lager]
                if not val:
                    raise DataInlasningFel(
                        f"Lagret '{lager}' finns inte i {gpkg.name}. Polygonlager: "
                        f"{', '.join(n for n, _ in poly) or '(inga)'}."
                    )
            else:
                val = [n for n, _ in poly if "byggnad" in n.lower()]
                if len(val) != 1:
                    raise DataInlasningFel(
                        f"Kan inte välja byggnadslager i {gpkg.name} (hittade {len(val)}). "
                        f"Polygonlager: {', '.join(n for n, _ in poly) or '(inga)'}. "
                        "Ange --byggnads-lager."
                    )
            info_l = pyogrio.read_info(gpkg, layer=val[0])
            crs = info_l.get("crs")
            epsg = (
                int(str(crs).split(":")[-1])
                if crs and str(crs).upper().startswith("EPSG:")
                else 3006
            )
            yta_fil = (
                shapely.box(*yta.bounds)
                if epsg == 3011
                else shapely.box(*geometri_till_epsg(yta, 3011, epsg).bounds)
            )
            gdf = gpd.read_file(gpkg, layer=val[0], bbox=tuple(yta_fil.bounds))
            if gdf.crs is None:
                gdf = gdf.set_crs(epsg=epsg)
            if gdf.crs.to_epsg() != 3011:
                gdf = gdf.to_crs(epsg=3011)
            gdf = gdf[gdf.geometry.intersects(yta)]
            if len(gdf):
                ram.append(gdf)
    if not ram:
        varningar.append("Inga byggnader i området i de hämtade byggnadsfilerna.")
        return None
    ihop = gpd.GeoDataFrame(pd.concat(ram, ignore_index=True), crs="EPSG:3011")
    mal.parent.mkdir(parents=True, exist_ok=True)
    mal.unlink(missing_ok=True)
    ihop.to_file(mal, layer="byggnader", driver="GPKG")
    nyckelfil.write_text(nyckel, encoding="utf-8")
    return mal


def geometri_till_epsg(g: BaseGeometry, fran: int, till: int) -> BaseGeometry:
    import numpy as np
    from pyproj import Transformer

    t = Transformer.from_crs(fran, till, always_xy=True)
    return shapely.transform(g, lambda xy: np.column_stack(t.transform(xy[:, 0], xy[:, 1])))
