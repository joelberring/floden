# ruff: noqa: E501
"""Gemensamma delar för geografier och projekt per kommun: yta och skalfel, WFS till GeoPackage i
EPSG:3011, öppenhetsfiler (K38), frågefiler (K20) och regn ur kommunfilen.

Nycklar, personuppgifter och data som inte är öppna skrivs aldrig till utskrift, loggar eller
beskrivningsfiler.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gemensamt.koordinater import transformera
from kommuner.konfig import KommunKonfig, Testomrade
from kommuner.wfs import KommunKallaFel

EPSG_INTERN = 3011
OPPENHET = "oppenhet.toml"


class SkapaFel(Exception):
    """Något i skapandet gick inte att slutföra. Texten säger vad som ska göras."""


Skriv = Callable[[str], None]


# ------------------------------------------------------------------------------------ geometri


def skalfel_langd(epsg_fran: int, e: float, n: float, epsg_till: int = EPSG_INTERN) -> float:
    """Förhållandet mellan längder i `epsg_till` och i `epsg_fran` vid punkten (e, n): 1,0016
    betyder att en sträcka mäts 0,16 % längre i EPSG:3011 än i kommunens eget system. Mäts på två
    korta sträckor (öst och nord), medelvärde."""
    a = transformera(e, n, epsg_fran, epsg_till)
    l_ost = math.dist(a, transformera(e + 1000.0, n, epsg_fran, epsg_till)) / 1000.0
    l_nord = math.dist(a, transformera(e, n + 1000.0, epsg_fran, epsg_till)) / 1000.0
    return (l_ost + l_nord) / 2.0


@dataclass(frozen=True)
class Projektyta:
    """Studieområdet och beräkningsområdet i EPSG:3011 (kvadrater, meter)."""

    mitt_3011: tuple[float, float]
    studie_3011: tuple[float, float, float, float]
    berakning_3011: tuple[float, float, float, float]
    skalfaktor_langd: float

    @property
    def skalfel_procent(self) -> float:
        return (self.skalfaktor_langd - 1.0) * 100.0


def beraekna_yta(kommun: KommunKonfig, omrade: Testomrade) -> Projektyta:
    """Kvadrat i EPSG:3011 runt områdets mittpunkt. Sidan är områdets längsta sida i meter (nominellt
    i kommunens system; skillnaden är skalfelet, som redovisas)."""
    e0, n0, e1, n1 = omrade.bbox
    mitt = transformera((e0 + e1) / 2, (n0 + n1) / 2, kommun.publicering_epsg, EPSG_INTERN)
    h = omrade.sida_m / 2.0
    studie = (mitt[0] - h, mitt[1] - h, mitt[0] + h, mitt[1] + h)
    k = kommun.lantmateriet.kontext_m
    # Heltalsmeter så att K1-rutan hamnar på hela meter (stabil receptnyckel vid omkörning).
    ber = (
        math.floor(studie[0] - k),
        math.floor(studie[1] - k),
        math.ceil(studie[2] + k),
        math.ceil(studie[3] + k),
    )
    return Projektyta(
        mitt_3011=mitt,
        studie_3011=studie,
        berakning_3011=(float(ber[0]), float(ber[1]), float(ber[2]), float(ber[3])),
        skalfaktor_langd=skalfel_langd(kommun.publicering_epsg, *((e0 + e1) / 2, (n0 + n1) / 2)),
    )


def skriv_omrade_gpkg(bbox_3011: tuple[float, float, float, float], fil: Path) -> Path:
    import geopandas as gpd
    import shapely

    fil.parent.mkdir(parents=True, exist_ok=True)
    gdf = gpd.GeoDataFrame({"namn": ["beraknings_omrade"]}, geometry=[shapely.box(*bbox_3011)], crs=3011)
    if fil.exists():
        fil.unlink()
    gdf.to_file(fil, driver="GPKG")
    return fil


# ------------------------------------------------------------------------------ WFS -> GeoPackage


def _alla_koordinater(geom: Mapping[str, Any]) -> list[tuple[float, float]]:
    ut: list[tuple[float, float]] = []

    def rek(c: Any) -> None:
        if isinstance(c, list) and c and isinstance(c[0], int | float):
            ut.append((float(c[0]), float(c[1])))
        elif isinstance(c, list):
            for x in c:
                rek(x)

    rek(geom.get("coordinates"))
    return ut


def _epsg_ur_geojson(fc: Mapping[str, Any], antagen: int) -> int:
    """EPSG ur FeatureCollection:s `crs`-medlem om det finns, annars det vi bad om (`srsName`)."""
    crs = fc.get("crs")
    namn = crs.get("properties", {}).get("name", "") if isinstance(crs, Mapping) else ""
    m = re.search(r"EPSG:+(\d{4,5})", str(namn))
    return int(m.group(1)) if m else antagen


def geojson_till_gpkg(
    fc: Mapping[str, Any],
    *,
    epsg_antagen: int,
    ut: Path,
    forvantad_bbox: tuple[float, float, float, float],
    falt: Mapping[str, tuple[str, ...]],
    lagernamn: str,
    kravs_objekt: bool = True,
) -> tuple[Path, int, list[str]]:
    """Skriver polygoner (och bara de) som GeoPackage i EPSG:3011. Returnerar (fil, antal, varningar).

    - Koordinatsystemet är det `crs`-medlemmet säger, annars `epsg_antagen`. Koordinater som ser ut som
      grader eller som ligger långt från området ger ett fel (axelordning eller fel system).
    - Bara de attribut som `falt` pekar ut sparas, under Floden-fältens namn. Övriga kastas
      (dataminimering: adresser och fastighetsbeteckningar behövs inte).
    - Objekt utanför `forvantad_bbox` (i kommunens system, 200 m marginal) tas inte med.
    """
    import geopandas as gpd
    from shapely.geometry import shape

    epsg = _epsg_ur_geojson(fc, epsg_antagen)
    e0, n0, e1, n1 = forvantad_bbox
    marg = 200.0
    varningar: list[str] = []
    rader: list[dict[str, Any]] = []
    geom_lista = []
    kontrollerade = 0
    for f in fc.get("features", []):
        g = f.get("geometry") if isinstance(f, Mapping) else None
        if not isinstance(g, Mapping) or g.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        pkt = _alla_koordinater(g)
        if not pkt:
            continue
        kontrollerade += 1
        x, y = pkt[0]
        if abs(x) <= 180.0 and abs(y) <= 90.0:
            raise KommunKallaFel(
                f"Lagret '{lagernamn}': koordinaterna ser ut som grader, inte meter i EPSG:{epsg}."
            )
        if not (e0 - marg <= x <= e1 + marg and n0 - marg <= y <= n1 + marg):
            if e0 - marg <= y <= e1 + marg and n0 - marg <= x <= n1 + marg:
                raise KommunKallaFel(
                    f"Lagret '{lagernamn}': nord och öst verkar vara omkastade (axelordning i WFS)."
                )
            continue
        props = {str(k).lower(): v for k, v in (f.get("properties") or {}).items()}
        rad: dict[str, Any] = {}
        for floden_falt, kandidater in falt.items():
            for kand in kandidater:
                v = props.get(kand.lower())
                if v is not None and v != "":
                    rad[floden_falt] = v
                    break
        rader.append(rad)
        geom_lista.append(shape(g))
    if not geom_lista:
        if kravs_objekt:
            raise KommunKallaFel(
                f"Lagret '{lagernamn}' gav inga polygoner i området. Fel lager eller fel område? "
                "Kontrollera lagernamnet med: floden projekt kommun-lager <kommun>."
            )
        varningar.append(f"Lagret '{lagernamn}' gav inga polygoner i området.")
    gdf = gpd.GeoDataFrame(rader, geometry=geom_lista, crs=epsg)
    if epsg != EPSG_INTERN:
        gdf = gdf.to_crs(epsg=EPSG_INTERN)
    ut.parent.mkdir(parents=True, exist_ok=True)
    if ut.exists():
        ut.unlink()
    if len(gdf):
        gdf.to_file(ut, driver="GPKG")
    if kontrollerade and len(geom_lista) < kontrollerade:
        varningar.append(
            f"{kontrollerade - len(geom_lista)} objekt i '{lagernamn}' låg utanför området och togs inte med."
        )
    return ut, len(geom_lista), varningar


# ------------------------------------------------------------------------------------ öppenhet


def skriv_oppenhet_toml(
    mapp: Path, *, oppen: bool, licens: str, kalla: str, anledning: str
) -> Path:
    """`oppenhet.toml` (K38). `licens` skrivs alltid så att märkningen syns även när mappen är stängd."""
    text = (
        'kontrakt = "floden-oppenhet/v1"\n'
        f"oppen = {'true' if oppen else 'false'}\n"
        f"licens = {json.dumps(licens, ensure_ascii=False)}\n"
        f"kalla = {json.dumps(kalla, ensure_ascii=False)}\n"
        f"anledning = {json.dumps(anledning, ensure_ascii=False)}\n"
    )
    mapp.mkdir(parents=True, exist_ok=True)
    fil = mapp / OPPENHET
    if not fil.is_file() or fil.read_text(encoding="utf-8") != text:
        fil.write_text(text, encoding="utf-8")
    return fil


def _strangaste_licens(licenser: list[str]) -> str:
    """En gemensam licens för härledd data: gäller bara om alla källor har samma; annars den första
    i en fast ordning av hur strikta de är (CC-BY-SA, CC-BY, ODbL, sedan CC0)."""
    if "intern" in licenser:
        return "intern"  # data som inte är öppna: aldrig en öppen licens för det härledda
    ordning = ["CC-BY-SA-4.0", "ODbL-1.0", "CC-BY-4.0", "ODC-By-1.0", "CC0-1.0", "PDDL-1.0"]
    for lic in ordning:
        if lic in licenser:
            return lic
    return licenser[0]


# --------------------------------------------------------------------------------------- fråga


def _toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def regn_for_kommun(kommun: KommunKonfig) -> tuple[float, str, float]:
    """(regn_mm, text, varaktighet_min) ur kommunfilens `[skyfall] regn_id` (inbyggt regnförval)."""
    from gemensamt.regn import RegnFel, hamta_regnforval

    try:
        f = hamta_regnforval(str(kommun.skyfall["regn_id"]))
        serie = f.generera()
    except RegnFel as exc:
        raise SkapaFel(f"[skyfall] regn_id i kommunfilen: {exc}") from exc
    text = (
        f"{f.statistikkalla}, {f.aterkomsttid_ar:g} år, {f.varaktighet_min:g} min, "
        f"klimatfaktor {f.klimatfaktor:g} = {serie.total_djup_mm:.1f} mm"
    )
    return float(serie.total_djup_mm), text, float(f.varaktighet_min)


# --------------------------------------------------------------------------------- huvudflöde


def terrangstatistik_mosaik(
    z: Any, geo: tuple[float, float, float], karna: tuple[float, float, float, float]
) -> dict[str, Any]:
    """Höjdspann och lägsta punkt i kärnan ur höjdmodellens mosaik `z` med `geo = (x_min, y_max, pixel)`."""
    import numpy as np

    x0, y1, px = geo
    h, b = z.shape
    c0, c1 = max(0, int((karna[0] - x0) // px)), min(b, int((karna[2] - x0) // px) + 1)
    r0, r1 = max(0, int((y1 - karna[3]) // px)), min(h, int((y1 - karna[1]) // px) + 1)
    sub = np.asarray(z[r0:r1, c0:c1], dtype=np.float64)
    ok = np.isfinite(sub)
    if not ok.any():
        return {"fel": "markmodellen har inga giltiga höjder i kärnan"}
    i_min = int(np.argmin(np.where(ok, sub, np.inf)))
    r, c = divmod(i_min, sub.shape[1])
    return {
        "z_min_m": round(float(sub[ok].min()), 2),
        "z_max_m": round(float(sub[ok].max()), 2),
        "relief_m": round(float(sub[ok].max() - sub[ok].min()), 2),
        "lagpunkt_e_m": round(x0 + (c0 + c + 0.5) * px, 1),
        "lagpunkt_n_m": round(y1 - (r0 + r + 0.5) * px, 1),
        "celler": int(ok.sum()),
        "enhet": "m (RH 2000), E/N i EPSG:3011",
    }


def terrangstatistik(k1_ruta: Path) -> dict[str, Any]:
    """Höjdspann och lägsta punkt för en hel K1-ruta (mark.tif)."""
    from testsvit.k1_io import las_geotiff

    z, g = las_geotiff(k1_ruta / "mark.tif")
    import numpy as np

    arr = np.where(np.asarray(z, dtype=np.float64) > -9000.0, np.asarray(z, dtype=np.float64), np.nan)
    return terrangstatistik_mosaik(arr, (g.x_min, g.y_max, g.pixelstorlek_x), (g.x_min, g.y_max - g.hojd * g.pixelstorlek_x, g.x_min + g.bredd * g.pixelstorlek_x, g.y_max))


def _oppna_register(konfig: Mapping[str, Any], rot: Path) -> Any:
    from dataclasses import replace

    from konto import Databas, Register, las_konto_konfig

    try:
        kk = las_konto_konfig(konfig, os.environ)
    except Exception as exc:  # noqa: BLE001 - texten har inga hemligheter
        raise SkapaFel(f"Kontokonfigurationen går inte att läsa: {exc}") from exc
    if not kk.databas.is_absolute():
        kk = replace(kk, databas=rot / kk.databas)
    return Register(Databas(kk.databas))


def _valj_organisation(reg: Any, konto_id: str, organisation_id: str | None) -> str:
    if organisation_id:
        return organisation_id
    orgar = reg.organisationer_for_konto(konto_id)
    if len(orgar) == 1:
        return str(orgar[0])
    if not orgar:
        raise SkapaFel(
            "Kontot är inte medlem i någon organisation. Skapa en och gör kontot till admin, "
            "eller ange --organisation <id> (se docs/DRIFT.md, 'Göteborg: provområdet')."
        )
    raise SkapaFel(
        f"Kontot är medlem i {len(orgar)} organisationer: ange en med --organisation <id>. "
        f"Id:n: {', '.join(orgar)}."
    )




def bbox_till_kommun(
    kommun_epsg: int, bbox_3011: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    """Omslutande rektangel i kommunens system för en bbox i EPSG:3011 (hörnen räknas om)."""
    e0, n0, e1, n1 = bbox_3011
    h = [transformera(x, y, EPSG_INTERN, kommun_epsg) for x, y in ((e0, n0), (e0, n1), (e1, n0), (e1, n1))]
    return (min(p[0] for p in h), min(p[1] for p in h), max(p[0] for p in h), max(p[1] for p in h))
