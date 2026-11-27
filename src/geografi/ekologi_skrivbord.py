# ruff: noqa: E501
"""Ekologiskt skrivbordsunderlag i K46-geografier (`lager.skyddad_natur`, `lager.artfynd_publika`)
samt projektbundet fältlager för ekologens fältinventerade NVI-objekt och skyddsvärda träd.

Additivt och 100 % valfritt i K46 (`floden-geografi/v1`): befintliga geografier utan lagren
förblir fullt giltiga. Alla lager filer SHA-256-verifieras och följer K38 (`oppenhet.toml`),
OSL 20 kap. 1 § (aldrig koordinater för skyddsklassade arter) och 0 PII.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from shapely.geometry import Point, box, mapping, shape

from data_inlasning.artdatabanken import (
    MAX_TILLATEN_OSAKERHET_M,
    OSL_VARNING,
    RODLISTEKATEGORIER,
    ArtfyndResultat,
)
from data_inlasning.skyddad_natur import (
    SkogligtNaturvarde,
    SkyddatOmrade,
)
from gemensamt.hash import sha256_bytes, sha256_fil
from geografi.markunderlag import _geografins_manifest
from geografi.modell import GeografiFel, lager_mapp, lagra
from kommuner.skapa import skriv_oppenhet_toml

EPSG_INTERN: Final[int] = 3011
FORMAT_SKYDDAD_NATUR: Final[str] = "floden-ekologi-skyddad-natur/v1"
FORMAT_ARTFYND_PUBLIKA: Final[str] = "floden-ekologi-artfynd-publika/v1"
FORMAT_FALTLAGER: Final[str] = "floden-ekologi-faltlager/v1"

SKYDD_GPKG: Final[str] = "skyddade_omraden.gpkg"
SKOG_GPKG: Final[str] = "skogliga_varden.gpkg"
SKRIVBORD_SAMMANFATTNING_FIL: Final[str] = "skrivbord_sammanfattning.json"
PUBLIKA_FYND_FIL: Final[str] = "publika_fynd.geojson"
ARTLISTA_FIL: Final[str] = "artlista.json"

FALTLAGER_MAPP: Final[str] = "ekologi_faltlager"
FALTLAGER_GEOJSON: Final[str] = "faltlager.geojson"
FALTLAGER_META: Final[str] = "faltlager.json"
MAX_FALTLAGER_BYTES: Final[int] = 10 * 1024 * 1024  # 10 MB
TILLATNA_FALT_FILANDELSER: Final[frozenset[str]] = frozenset({".gpkg", ".geojson", ".json"})
SPARADE_FALT_KOLUMNER: Final[tuple[str, ...]] = (
    "objekt_typ",
    "nvi_klass",
    "biotop",
    "tradslag",
    "stamomkrets_cm",
)

_EPOST_RE: Final[re.Pattern[str]] = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PERSONNR_RE: Final[re.Pattern[str]] = re.compile(r"\b(?:19|20)?\d{6}[-+]\d{4}\b")


@dataclass(frozen=True)
class EkologiSkrivbordUnderlag:
    """Verifierad skrivbordsöversikt ur en K46-geografi (`lager.skyddad_natur`, `lager.artfynd_publika`, VMI, träd)."""

    ref: str
    manifest_sha256: str
    karna_3011: tuple[float, float, float, float] | None
    skyddad_natur_sha256: str | None
    artfynd_sha256: str | None
    vmi_sha256: str | None
    trad_sha256: str | None
    sammanfattning: dict[str, Any]
    skyddade_omraden_geojson: dict[str, Any]
    skogliga_varden_geojson: dict[str, Any]
    publika_fynd_geojson: dict[str, Any]
    artlista: dict[str, Any] | None
    faltchecklista: tuple[dict[str, str], ...]
    osl_varning: str = OSL_VARNING

    def som_dict(self) -> dict[str, Any]:
        return {
            "geografi": self.ref,
            "manifest_sha256": self.manifest_sha256,
            "karna_3011": list(self.karna_3011) if self.karna_3011 is not None else None,
            "lager_sha256": {
                "skyddad_natur": self.skyddad_natur_sha256,
                "artfynd_publika": self.artfynd_sha256,
                "vmi": self.vmi_sha256,
                "tradpunkter": self.trad_sha256,
            },
            "sammanfattning": self.sammanfattning,
            "skyddade_omraden": self.skyddade_omraden_geojson,
            "skogliga_varden": self.skogliga_varden_geojson,
            "publika_fynd": self.publika_fynd_geojson,
            "artlista": self.artlista,
            "faltchecklista": list(self.faltchecklista),
            "osl_varning": self.osl_varning,
        }


@dataclass(frozen=True)
class EkologiFaltlagerPost:
    """Verifierat, dataminimerat projektfältlager (NVI-polygoner och/eller skyddsvärda trädpunkter)."""

    filnamn: str
    kallfil_sha256: str
    geojson_sha256: str
    antal_objekt: int
    antal_polygoner: int
    antal_punkter: int
    per_nvi_klass: Mapping[str, int]
    geojson: dict[str, Any]
    varningar: tuple[str, ...] = ()

    def som_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT_FALTLAGER,
            "epsg": EPSG_INTERN,
            "filnamn": self.filnamn,
            "kallfil_sha256": self.kallfil_sha256,
            "geojson_sha256": self.geojson_sha256,
            "antal_objekt": self.antal_objekt,
            "antal_polygoner": self.antal_polygoner,
            "antal_punkter": self.antal_punkter,
            "per_nvi_klass": dict(self.per_nvi_klass),
            "sparade_falt": list(SPARADE_FALT_KOLUMNER),
            "geojson": self.geojson,
            "varningar": list(self.varningar),
        }


# ------------------------------------------------------------------ hjälpare


def _atomisk_skriv_text(fil: Path, text: str) -> None:
    fil.parent.mkdir(parents=True, exist_ok=True)
    tmp = fil.with_name(f"{fil.name}.tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, fil)


def _skriv_gpkg(rader: list[dict[str, Any]], geometrier: list[Any], kolumner: Sequence[str], ut: Path) -> None:
    import geopandas as gpd

    ut.parent.mkdir(parents=True, exist_ok=True)
    if ut.exists():
        ut.unlink()
    if rader:
        gdf = gpd.GeoDataFrame(rader, geometry=geometrier, crs=EPSG_INTERN)
    else:
        data: dict[str, list[Any]] = {k: [] for k in kolumner}
        gdf = gpd.GeoDataFrame(data, geometry=gpd.GeoSeries([], crs=EPSG_INTERN), crs=EPSG_INTERN)
    gdf.to_file(ut, driver="GPKG")


def _sammanfatta_skydd(omraden: Sequence[SkyddatOmrade]) -> dict[str, Any]:
    per_form: dict[str, int] = {}
    for o in omraden:
        per_form[o.skyddsform] = per_form.get(o.skyddsform, 0) + 1
    narmaste = omraden[0] if omraden else None
    return {
        "antal": len(omraden),
        "antal_overlappar_karna": sum(1 for o in omraden if o.overlappar_karna),
        "overlapp_karna_m2": round(sum(o.overlapp_karna_m2 for o in omraden), 2),
        "minsta_avstand_m": round(min(o.minsta_avstand_m for o in omraden), 2) if omraden else None,
        "per_skyddsform": dict(sorted(per_form.items())),
        "antal_natura2000": per_form.get("Natura2000", 0),
        "narmaste": (
            {
                "omrade_id": narmaste.omrade_id,
                "namn": narmaste.namn,
                "skyddsform": narmaste.skyddsform,
                "skyddsform_namn": narmaste.skyddsform_namn,
                "minsta_avstand_m": round(narmaste.minsta_avstand_m, 2),
                "overlappar_karna": narmaste.overlappar_karna,
            }
            if narmaste is not None
            else None
        ),
    }


def _sammanfatta_skog(objekt: Sequence[SkogligtNaturvarde]) -> dict[str, Any]:
    per_kat: dict[str, int] = {}
    for o in objekt:
        per_kat[o.kategori] = per_kat.get(o.kategori, 0) + 1
    narmaste = objekt[0] if objekt else None
    return {
        "antal": len(objekt),
        "antal_overlappar_karna": sum(1 for o in objekt if o.overlappar_karna),
        "overlapp_karna_m2": round(sum(o.overlapp_karna_m2 for o in objekt), 2),
        "minsta_avstand_m": round(min(o.minsta_avstand_m for o in objekt), 2) if objekt else None,
        "per_kategori": dict(sorted(per_kat.items())),
        "antal_nyckelbiotoper": per_kat.get("nyckelbiotop", 0) + per_kat.get("skogsbruk_nyckelbiotop", 0),
        "antal_naturvarden": per_kat.get("naturvarde", 0),
        "antal_sumpskogar": per_kat.get("sumpskog", 0),
        "antal_biotopskydd": per_kat.get("biotopskydd", 0),
        "antal_naturvardsavtal": per_kat.get("naturvardsavtal", 0),
        "narmaste": (
            {
                "objekt_id": narmaste.objekt_id,
                "namn": narmaste.namn,
                "kategori": narmaste.kategori,
                "kategori_namn": narmaste.kategori_namn,
                "minsta_avstand_m": round(narmaste.minsta_avstand_m, 2),
                "overlappar_karna": narmaste.overlappar_karna,
            }
            if narmaste is not None
            else None
        ),
    }


# ------------------------------------------------------------------ bygg K46-lager


def bygg_skyddad_natur_lager(
    geo_rot: Path,
    bygge: Path,
    karna_3011: tuple[float, float, float, float],
    *,
    skyddade_omraden: Sequence[SkyddatOmrade] = (),
    skogliga_varden: Sequence[SkogligtNaturvarde] = (),
    vmi_sammanfattning: Mapping[str, Any] | None = None,
    buffert_m: float = 1000.0,
    kallor_sha256: Mapping[str, str] | None = None,
    villkor_kontrollerade: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Bygger additivt K46-lager `lager.skyddad_natur` och lagrar innehållsadresserat med K38 `oppenhet.toml`."""
    mapp = bygge / "skyddad_natur"
    if mapp.exists():
        shutil.rmtree(mapp)
    mapp.mkdir(parents=True, exist_ok=True)

    skydd_sort = sorted(
        skyddade_omraden,
        key=lambda o: (round(o.minsta_avstand_m, 3), o.skyddsform, o.namn, o.omrade_id),
    )
    skog_sort = sorted(
        skogliga_varden,
        key=lambda o: (round(o.minsta_avstand_m, 3), o.kategori, o.namn, o.objekt_id),
    )

    skydd_kol = (
        "omrade_id",
        "namn",
        "skyddsform",
        "skyddsform_namn",
        "lager",
        "beslutsdatum",
        "kalla",
        "area_m2",
        "overlapp_karna_m2",
        "overlappar_karna",
        "minsta_avstand_m",
    )
    skydd_rader = [
        {
            "omrade_id": o.omrade_id,
            "namn": o.namn,
            "skyddsform": o.skyddsform,
            "skyddsform_namn": o.skyddsform_namn,
            "lager": o.lager,
            "beslutsdatum": o.beslutsdatum or "",
            "kalla": o.kalla,
            "area_m2": round(o.area_m2, 2),
            "overlapp_karna_m2": round(o.overlapp_karna_m2, 2),
            "overlappar_karna": bool(o.overlappar_karna),
            "minsta_avstand_m": round(o.minsta_avstand_m, 2),
        }
        for o in skydd_sort
    ]
    _skriv_gpkg(skydd_rader, [o.geometri_3011 for o in skydd_sort], skydd_kol, mapp / SKYDD_GPKG)

    skog_kol = (
        "objekt_id",
        "kategori",
        "kategori_namn",
        "namn",
        "biotoptyp",
        "beslutsdatum",
        "tjanst",
        "kalla",
        "area_m2",
        "overlapp_karna_m2",
        "overlappar_karna",
        "minsta_avstand_m",
    )
    skog_rader = [
        {
            "objekt_id": o.objekt_id,
            "kategori": o.kategori,
            "kategori_namn": o.kategori_namn,
            "namn": o.namn,
            "biotoptyp": o.biotoptyp or "",
            "beslutsdatum": o.beslutsdatum or "",
            "tjanst": o.tjanst,
            "kalla": o.kalla,
            "area_m2": round(o.area_m2, 2),
            "overlapp_karna_m2": round(o.overlapp_karna_m2, 2),
            "overlappar_karna": bool(o.overlappar_karna),
            "minsta_avstand_m": round(o.minsta_avstand_m, 2),
        }
        for o in skog_sort
    ]
    _skriv_gpkg(skog_rader, [o.geometri_3011 for o in skog_sort], skog_kol, mapp / SKOG_GPKG)

    vmi_del: dict[str, Any] = {
        "tillganglig": bool(vmi_sammanfattning and vmi_sammanfattning.get("tillganglig", True)),
        "antal_polygoner": int((vmi_sammanfattning or {}).get("antal_polygoner", 0)),
        "overlapp_karna_m2": round(float((vmi_sammanfattning or {}).get("overlapp_karna_m2", 0.0)), 2),
        "minsta_avstand_m": (
            None
            if (vmi_sammanfattning or {}).get("minsta_avstand_m") is None
            else round(float((vmi_sammanfattning or {})["minsta_avstand_m"]), 2)
        ),
        "alder_text": str(
            (vmi_sammanfattning or {}).get(
                "alder_text",
                "Äldre inventering (1981–2005); visar känd våtmark som kvalitetsinformation, inte aktuell utbredning.",
            )
        ),
    }

    kalla_text = "Naturvårdsverket (Skyddad natur, Natura 2000, VMI) och Skogsstyrelsen (CC0)"
    sammanfattning: dict[str, Any] = {
        "format": FORMAT_SKYDDAD_NATUR,
        "epsg": EPSG_INTERN,
        "karna_3011": [float(v) for v in karna_3011],
        "buffert_m": float(buffert_m),
        "skyddade_omraden": _sammanfatta_skydd(skydd_sort),
        "skogliga_varden": _sammanfatta_skog(skog_sort),
        "vmi": vmi_del,
        "kallor_sha256": dict(sorted((kallor_sha256 or {}).items())),
        "licens": "CC0",
        "kalla": kalla_text,
    }
    (mapp / SKRIVBORD_SAMMANFATTNING_FIL).write_text(
        json.dumps(sammanfattning, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    lg = lagra(geo_rot, mapp)
    skriv_oppenhet_toml(
        lg.mapp,
        oppen=villkor_kontrollerade,
        licens="CC0",
        kalla=kalla_text,
        anledning="Naturvårdsverket (Skyddad natur/N2000/VMI) och Skogsstyrelsen är CC0: öppen."
        if villkor_kontrollerade
        else "Villkor ej bekräftade: stängt tills --villkor-kontrollerade.",
    )
    lager_post = {
        "sha256": lg.sha256,
        "buffert_m": float(buffert_m),
        "antal_skyddade_omraden": len(skydd_sort),
        "antal_skogliga_varden": len(skog_sort),
        "filer": [{"fil": f, "sha256": s} for f, s in lg.filer],
    }
    kalla_post = {
        "licens": "CC0",
        "kalla": sammanfattning["kalla"],
    }
    return lager_post, kalla_post, []


def bygg_artfynd_publika_lager(
    geo_rot: Path,
    bygge: Path,
    karna_3011: tuple[float, float, float, float],
    *,
    resultat: ArtfyndResultat,
    villkor_kontrollerade: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Bygger additivt K46-lager `lager.artfynd_publika` med 0 PII och 0 skyddsklassade koordinater."""
    mapp = bygge / "artfynd_publika"
    if mapp.exists():
        shutil.rmtree(mapp)
    mapp.mkdir(parents=True, exist_ok=True)

    # Extra försvarslinje: avvisa ovillkorligt alla poster med osäkerhet >= 5000 m.
    features: list[dict[str, Any]] = []
    extra_filtrerade = 0
    for f in resultat.fynd:
        if f.osakerhet_m is not None and f.osakerhet_m >= MAX_TILLATEN_OSAKERHET_M:
            extra_filtrerade += 1
            continue
        features.append(f.som_geojson_feature())

    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": f"EPSG:{EPSG_INTERN}"}},
        "features": features,
    }
    (mapp / PUBLIKA_FYND_FIL).write_text(
        json.dumps(fc, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    antal_skyddade = resultat.antal_skyddsklassade_filtrerade + extra_filtrerade
    artlista_dok = {
        "format": FORMAT_ARTFYND_PUBLIKA,
        "epsg": EPSG_INTERN,
        "karna_3011": [float(v) for v in karna_3011],
        "buffert_m": float(resultat.buffert_m),
        "period_ar": [resultat.start_ar, resultat.slut_ar],
        "antal_publika_fynd": len(features),
        "antal_unika_arter": len(resultat.artlista),
        "antal_rodlistade_arter": sum(
            1 for a in resultat.artlista if a.rodlistekategori in RODLISTEKATEGORIER
        ),
        "antal_fridlysta_arter": sum(1 for a in resultat.artlista if a.fridlyst),
        "antal_skyddsklassade_filtrerade": antal_skyddade,
        "osl_begransning": True,
        "osl_varning": OSL_VARNING,
        "arter": [a.som_dict() for a in resultat.artlista],
        "kalla_sha256": resultat.kalla_sha256,
        "licens": resultat.licens,
        "kalla": resultat.kalla,
    }
    (mapp / ARTLISTA_FIL).write_text(
        json.dumps(artlista_dok, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    lg = lagra(geo_rot, mapp)
    skriv_oppenhet_toml(
        lg.mapp,
        oppen=villkor_kontrollerade,
        licens=resultat.licens,
        kalla=resultat.kalla,
        anledning="SLU Artdatabanken SOS (publika fynd): villkoren kontrollerade (--villkor-kontrollerade)."
        if villkor_kontrollerade
        else "SLU Artdatabanken SOS: stängt för MCP tills --villkor-kontrollerade.",
    )
    lager_post = {
        "sha256": lg.sha256,
        "buffert_m": float(resultat.buffert_m),
        "antal_publika_fynd": len(features),
        "antal_unika_arter": len(resultat.artlista),
        "antal_skyddsklassade_filtrerade": antal_skyddade,
        "osl_begransning": True,
        "filer": [{"fil": f, "sha256": s} for f, s in lg.filer],
    }
    kalla_post = {
        "licens": resultat.licens,
        "kalla": resultat.kalla,
        "osl_varning": OSL_VARNING,
    }
    varningar = list(resultat.varningar)
    return lager_post, kalla_post, varningar


# ------------------------------------------------------------------ läs K46 skrivbordsunderlag


def _gpkg_till_geojson(fil: Path, karna_box: Any | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    import geopandas as gpd

    if not fil.is_file():
        return {"type": "FeatureCollection", "features": []}, []
    gdf = gpd.read_file(fil)
    if gdf.crs is not None and gdf.crs.to_epsg() != EPSG_INTERN:
        gdf = gdf.to_crs(epsg=EPSG_INTERN)
    features: list[dict[str, Any]] = []
    props_lista: list[dict[str, Any]] = []
    for idx, rad in gdf.iterrows():
        geom = rad.geometry
        if geom is None or geom.is_empty:
            continue
        props = {
            str(k): (None if (isinstance(v, float) and math.isnan(v)) else v)
            for k, v in rad.items()
            if k != "geometry"
        }
        if karna_box is not None:
            overlappar = bool(geom.intersects(karna_box))
            ov_m2 = float(geom.intersection(karna_box).area) if (overlappar and geom.area > 0) else 0.0
            avst = 0.0 if overlappar else float(geom.distance(karna_box))
            props["overlappar_karna"] = overlappar
            props["overlapp_karna_m2"] = round(ov_m2, 2)
            props["minsta_avstand_m"] = round(avst, 2)
        features.append(
            {
                "type": "Feature",
                "id": str(props.get("omrade_id") or props.get("objekt_id") or idx),
                "properties": props,
                "geometry": mapping(geom),
            }
        )
        props_lista.append(props)
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": f"EPSG:{EPSG_INTERN}"}},
        "features": features,
    }, props_lista


def bygg_nvi_faltchecklista(sammanfattning: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    """Deterministisk checklista inför fältinventering (NVI) härledd ur skrivbordsunderlaget."""
    rader: list[dict[str, str]] = []
    skydd = sammanfattning.get("skyddade_omraden") or {}
    skog = sammanfattning.get("skogliga_varden") or {}
    vmi = sammanfattning.get("vmi") or {}
    art = sammanfattning.get("artfynd_publika") or {}

    # 1. Formellt områdesskydd / Natura 2000
    n_skydd = int(skydd.get("antal") or 0)
    ov_skydd = int(skydd.get("antal_overlappar_karna") or 0)
    avst_skydd = skydd.get("minsta_avstand_m")
    if not sammanfattning.get("skyddad_natur_tillganglig"):
        rader.append(
            {
                "moment": "Formellt områdesskydd & Natura 2000",
                "prioritet": "Kräver kontroll",
                "underlag": "Lager ej mappat i geografin",
                "atgard": "Kontrollera Naturvårdsverkets Skyddad natur (NR, NP, N2000, biotopskydd) mot plangräns och påverkansområde.",
            }
        )
    elif ov_skydd > 0:
        rader.append(
            {
                "moment": "Formellt områdesskydd & Natura 2000",
                "prioritet": "Hög",
                "underlag": f"{ov_skydd} skyddat område överlappar planområdet (totalt {n_skydd} inom buffert)",
                "atgard": "Pröva föreskrifter, dispens- eller tillståndskrav (7 kap. miljöbalken) med Länsstyrelsen före planläggning.",
            }
        )
    elif n_skydd > 0:
        avst_txt = f"{round(float(avst_skydd))} m" if avst_skydd is not None else "inom buffert"
        rader.append(
            {
                "moment": "Formellt områdesskydd & Natura 2000",
                "prioritet": "Medel",
                "underlag": f"{n_skydd} skyddade områden inom buffert (närmaste {avst_txt})",
                "atgard": "Bedöm indirekt påverkan (hydrologi, spridning, slitage, skuggning) på angränsande skyddade områden.",
            }
        )
    else:
        rader.append(
            {
                "moment": "Formellt områdesskydd & Natura 2000",
                "prioritet": "Normal",
                "underlag": "0 registrerade skyddade områden inom sökbufferten",
                "atgard": "Stäm av lokala biotopskydd, strandskydd och kommunala naturvårdsprogram vid planstart.",
            }
        )

    # 2. Skogliga naturvärden (nyckelbiotoper, sumpskogar, biotopskydd)
    n_skog = int(skog.get("antal") or 0)
    ov_skog = int(skog.get("antal_overlappar_karna") or 0)
    n_nb = int(skog.get("antal_nyckelbiotoper") or 0)
    n_sump = int(skog.get("antal_sumpskogar") or 0)
    if ov_skog > 0 or n_nb > 0:
        rader.append(
            {
                "moment": "Skogliga naturvärden (nyckelbiotop, sumpskog, trädlager)",
                "prioritet": "Hög" if ov_skog > 0 else "Medel",
                "underlag": f"{n_skog} objekt inom buffert ({n_nb} nyckelbiotoper, {n_sump} sumpskogar; {ov_skog} överlappar kärnan)",
                "atgard": "Utför fältinventering enligt SIS SS 199000 (NVI), kartera skyddsvärda träd, död ved och skogskontinuitet.",
            }
        )
    else:
        rader.append(
            {
                "moment": "Skogliga naturvärden & värdefulla träd",
                "prioritet": "Medel",
                "underlag": "Inga registrerade Skogsstyrelsen-objekt i bufferten (NMD visar endast potentiell livsmiljö)",
                "atgard": "Inventera skyddsvärda träd (grova lövträd, hålträd), bryn och park-/skogsstrukturer i fält (NVI).",
            }
        )

    # 3. VMI / Våtmarker
    vmi_ok = bool(vmi.get("tillganglig"))
    vmi_ov = float(vmi.get("overlapp_karna_m2") or 0.0)
    if vmi_ok and vmi_ov > 0.0:
        rader.append(
            {
                "moment": "Våtmarker (VMI 1981–2005)",
                "prioritet": "Hög",
                "underlag": f"Känd VMI-våtmark överlappar kärnan ({round(vmi_ov)} m²; äldre inventering)",
                "atgard": "Verifiera aktuell våtmarksstatus, hydrologi och eventuella markavvattningsförbud i fält.",
            }
        )
    else:
        rader.append(
            {
                "moment": "Våtmarker & småvatten (VMI)",
                "prioritet": "Normal",
                "underlag": "Ingen överlappande VMI-våtmark i skrivbordsunderlaget"
                if vmi_ok
                else "VMI-lager saknas i geografin",
                "atgard": "Kontrollera småvatten, diken och groddjursmiljöer (generellt biotopskydd) under fältbesök.",
            }
        )

    # 4. Publika rödlistade/fridlysta fynd + obligatorisk OSL-kontroll
    art_ok = bool(sammanfattning.get("artfynd_tillganglig"))
    n_arter = int(art.get("antal_unika_arter") or 0)
    n_skydd_art = int(art.get("antal_skyddsklassade_filtrerade") or 0)
    if art_ok:
        rader.append(
            {
                "moment": "Artskydd & rödlistade arter (SLU SOS + OSL 20 kap. 1 §)",
                "prioritet": "Hög" if (n_arter > 0 or n_skydd_art > 0) else "Medel",
                "underlag": (
                    f"{n_arter} unika rödlistade/fridlysta arter i publika fynd; "
                    f"{n_skydd_art} skyddsklassade/diffuserade poster filtrerade utan koordinater"
                ),
                "atgard": (
                    "Beställ sekretessprövat utdrag för skyddsklassade arter via behörig ekolog/Länsstyrelsen "
                    "och rikta artinventering (fågel, fladdermöss, groddjur, insekter, flora) efter säsong."
                ),
            }
        )
    else:
        rader.append(
            {
                "moment": "Artskydd & rödlistade arter (OSL 20 kap. 1 §)",
                "prioritet": "Kräver kontroll",
                "underlag": "Publika artfynd ej hämtade (API-nyckel saknas eller lager ej mappat)",
                "atgard": (
                    "Gör slagning i SLU Artdatabanken och kontrollera sekretessprövade skyddsklassade arter "
                    "via behörig ekolog eller Länsstyrelsen före artskyddsbedömning."
                ),
            }
        )

    return tuple(rader)


def las_ekologi_skrivbord(
    manifest_fil: str | Path,
    karna_3011: tuple[float, float, float, float] | None = None,
) -> EkologiSkrivbordUnderlag:
    """Läser och SHA-256-verifierar ekologiska skrivbordslager ur ett K46-geografimanifest."""
    manifest, msha, geo_rot = _geografins_manifest(Path(manifest_fil))
    ref = f"{manifest['id']}@{manifest['version']}"
    lager = manifest.get("lager", {})

    karna_box = box(*karna_3011) if karna_3011 is not None else None
    skydd_sha: str | None = None
    art_sha: str | None = None
    vmi_sha: str | None = str(lager["vmi"]["sha256"]) if "vmi" in lager else None
    trad_sha: str | None = str(lager["tradpunkter"]["sha256"]) if "tradpunkter" in lager else None

    skydd_fc: dict[str, Any] = {"type": "FeatureCollection", "features": []}
    skog_fc: dict[str, Any] = {"type": "FeatureCollection", "features": []}
    fynd_fc: dict[str, Any] = {"type": "FeatureCollection", "features": []}
    artlista: dict[str, Any] | None = None

    sammanfattning: dict[str, Any] = {
        "skyddad_natur_tillganglig": False,
        "artfynd_tillganglig": False,
        "vmi_tillganglig": vmi_sha is not None,
        "tradpunkter_tillganglig": trad_sha is not None,
        "skyddade_omraden": {
            "antal": 0,
            "antal_overlappar_karna": 0,
            "overlapp_karna_m2": 0.0,
            "minsta_avstand_m": None,
            "per_skyddsform": {},
            "antal_natura2000": 0,
            "narmaste": None,
        },
        "skogliga_varden": {
            "antal": 0,
            "antal_overlappar_karna": 0,
            "overlapp_karna_m2": 0.0,
            "minsta_avstand_m": None,
            "per_kategori": {},
            "antal_nyckelbiotoper": 0,
            "antal_naturvarden": 0,
            "antal_sumpskogar": 0,
            "antal_biotopskydd": 0,
            "antal_naturvardsavtal": 0,
            "narmaste": None,
        },
        "vmi": {
            "tillganglig": vmi_sha is not None,
            "antal_polygoner": 0,
            "overlapp_karna_m2": 0.0,
            "minsta_avstand_m": None,
        },
        "artfynd_publika": None,
        "osl_varning": OSL_VARNING,
    }

    if "skyddad_natur" in lager:
        skydd_sha = str(lager["skyddad_natur"]["sha256"])
        smapp = lager_mapp(geo_rot, skydd_sha)
        sam_fil = smapp / SKRIVBORD_SAMMANFATTNING_FIL
        if not sam_fil.is_file():
            raise GeografiFel(f"Lagret skyddad_natur i {ref} saknar {SKRIVBORD_SAMMANFATTNING_FIL}.")
        lagrad = json.loads(sam_fil.read_text(encoding="utf-8"))
        skydd_fc, skydd_props = _gpkg_till_geojson(smapp / SKYDD_GPKG, karna_box)
        skog_fc, skog_props = _gpkg_till_geojson(smapp / SKOG_GPKG, karna_box)
        sammanfattning["skyddad_natur_tillganglig"] = True
        sammanfattning["vmi"] = dict(lagrad.get("vmi") or sammanfattning["vmi"])
        if vmi_sha is not None:
            sammanfattning["vmi"]["tillganglig"] = True

        if karna_box is None:
            sammanfattning["skyddade_omraden"] = dict(lagrad.get("skyddade_omraden") or {})
            sammanfattning["skogliga_varden"] = dict(lagrad.get("skogliga_varden") or {})
        else:
            # Räkna om sammanfattningen exakt mot angiven projektkärna
            per_form: dict[str, int] = {}
            for p in skydd_props:
                sf = str(p.get("skyddsform") or "")
                per_form[sf] = per_form.get(sf, 0) + 1
            skydd_props.sort(key=lambda p: (float(p.get("minsta_avstand_m", 0.0)), str(p.get("namn", ""))))
            sammanfattning["skyddade_omraden"] = {
                "antal": len(skydd_props),
                "antal_overlappar_karna": sum(1 for p in skydd_props if p.get("overlappar_karna")),
                "overlapp_karna_m2": round(sum(float(p.get("overlapp_karna_m2") or 0.0) for p in skydd_props), 2),
                "minsta_avstand_m": (
                    round(min(float(p["minsta_avstand_m"]) for p in skydd_props), 2) if skydd_props else None
                ),
                "per_skyddsform": dict(sorted(per_form.items())),
                "antal_natura2000": per_form.get("Natura2000", 0),
                "narmaste": (
                    {
                        "omrade_id": skydd_props[0].get("omrade_id"),
                        "namn": skydd_props[0].get("namn"),
                        "skyddsform": skydd_props[0].get("skyddsform"),
                        "skyddsform_namn": skydd_props[0].get("skyddsform_namn"),
                        "minsta_avstand_m": skydd_props[0].get("minsta_avstand_m"),
                        "overlappar_karna": bool(skydd_props[0].get("overlappar_karna")),
                    }
                    if skydd_props
                    else None
                ),
            }
            per_kat: dict[str, int] = {}
            for p in skog_props:
                kat = str(p.get("kategori") or "")
                per_kat[kat] = per_kat.get(kat, 0) + 1
            skog_props.sort(key=lambda p: (float(p.get("minsta_avstand_m", 0.0)), str(p.get("namn", ""))))
            sammanfattning["skogliga_varden"] = {
                "antal": len(skog_props),
                "antal_overlappar_karna": sum(1 for p in skog_props if p.get("overlappar_karna")),
                "overlapp_karna_m2": round(sum(float(p.get("overlapp_karna_m2") or 0.0) for p in skog_props), 2),
                "minsta_avstand_m": (
                    round(min(float(p["minsta_avstand_m"]) for p in skog_props), 2) if skog_props else None
                ),
                "per_kategori": dict(sorted(per_kat.items())),
                "antal_nyckelbiotoper": per_kat.get("nyckelbiotop", 0) + per_kat.get("skogsbruk_nyckelbiotop", 0),
                "antal_naturvarden": per_kat.get("naturvarde", 0),
                "antal_sumpskogar": per_kat.get("sumpskog", 0),
                "antal_biotopskydd": per_kat.get("biotopskydd", 0),
                "antal_naturvardsavtal": per_kat.get("naturvardsavtal", 0),
                "narmaste": (
                    {
                        "objekt_id": skog_props[0].get("objekt_id"),
                        "namn": skog_props[0].get("namn"),
                        "kategori": skog_props[0].get("kategori"),
                        "kategori_namn": skog_props[0].get("kategori_namn"),
                        "minsta_avstand_m": skog_props[0].get("minsta_avstand_m"),
                        "overlappar_karna": bool(skog_props[0].get("overlappar_karna")),
                    }
                    if skog_props
                    else None
                ),
            }

    if "artfynd_publika" in lager:
        art_sha = str(lager["artfynd_publika"]["sha256"])
        amapp = lager_mapp(geo_rot, art_sha)
        al_fil = amapp / ARTLISTA_FIL
        fynd_fil = amapp / PUBLIKA_FYND_FIL
        if not al_fil.is_file() or not fynd_fil.is_file():
            raise GeografiFel(f"Lagret artfynd_publika i {ref} saknar {ARTLISTA_FIL} eller {PUBLIKA_FYND_FIL}.")
        artlista_dok: dict[str, Any] = json.loads(al_fil.read_text(encoding="utf-8"))
        artlista = artlista_dok
        fynd_fc = json.loads(fynd_fil.read_text(encoding="utf-8"))
        sammanfattning["artfynd_tillganglig"] = True
        sammanfattning["artfynd_publika"] = {
            "antal_publika_fynd": int(artlista_dok.get("antal_publika_fynd", 0)),
            "antal_unika_arter": int(artlista_dok.get("antal_unika_arter", 0)),
            "antal_rodlistade_arter": int(artlista_dok.get("antal_rodlistade_arter", 0)),
            "antal_fridlysta_arter": int(artlista_dok.get("antal_fridlysta_arter", 0)),
            "antal_skyddsklassade_filtrerade": int(artlista_dok.get("antal_skyddsklassade_filtrerade", 0)),
            "period_ar": artlista_dok.get("period_ar"),
            "osl_begransning": True,
            "osl_varning": OSL_VARNING,
        }

    checklista = bygg_nvi_faltchecklista(sammanfattning)
    return EkologiSkrivbordUnderlag(
        ref=ref,
        manifest_sha256=msha,
        karna_3011=karna_3011,
        skyddad_natur_sha256=skydd_sha,
        artfynd_sha256=art_sha,
        vmi_sha256=vmi_sha,
        trad_sha256=trad_sha,
        sammanfattning=sammanfattning,
        skyddade_omraden_geojson=skydd_fc,
        skogliga_varden_geojson=skog_fc,
        publika_fynd_geojson=fynd_fc,
        artlista=artlista,
        faltchecklista=checklista,
        osl_varning=OSL_VARNING,
    )


# ------------------------------------------------------------------ Ekologens fältlager (projektlager, 0 PII)


def _sanera_kort_falt(varde: Any, max_len: int = 80) -> str | None:
    if varde is None or isinstance(varde, bool):
        return None
    s = " ".join(str(varde).split()).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    if _EPOST_RE.search(s) or _PERSONNR_RE.search(s):
        return None
    return s[:max_len]


def _tolka_nvi_klass(varde: Any) -> int | None:
    if varde is None or isinstance(varde, bool):
        return None
    if isinstance(varde, int | float):
        if math.isfinite(float(varde)) and float(varde) == int(varde) and 1 <= int(varde) <= 4:
            return int(varde)
        return None
    m = re.search(r"\b([1-4])\b", str(varde))
    return int(m.group(1)) if m else None


def _tolka_positivt_tal(varde: Any) -> float | None:
    if varde is None or isinstance(varde, bool):
        return None
    try:
        x = float(str(varde).replace(",", ".").strip()) if isinstance(varde, str) else float(varde)
    except (TypeError, ValueError):
        return None
    return round(x, 2) if (math.isfinite(x) and x > 0.0) else None


def _hitta_varde(props: Mapping[str, Any], alias: Sequence[str]) -> Any:
    lag = {str(k).lower(): v for k, v in props.items()}
    for a in alias:
        if a in lag and lag[a] is not None:
            return lag[a]
    return None


def importera_ekologi_faltlager(
    projekt_mapp: str | Path,
    filnamn: str,
    ra_bytes: bytes,
) -> EkologiFaltlagerPost:
    """Importerar ekologens egna fältinventerade NVI-polygoner (klass 1–4) eller skyddsvärda trädpunkter.

    Krav: GeoPackage/GeoJSON i känd CRS, max 10 MB, 0 PII (alla kolumner utom geometri,
    `objekt_typ`, `nvi_klass`, `biotop`, `tradslag`, `stamomkrets_cm` kastas ovillkorligt).
    Omprojicerar till EPSG:3011 och sparar atomiskt i projektet med SHA-256.
    """
    import geopandas as gpd

    if not isinstance(filnamn, str) or not filnamn.strip():
        raise GeografiFel("Filnamn för fältlager saknas.")
    rent_namn = Path(filnamn).name
    if rent_namn != filnamn or ".." in filnamn or "/" in filnamn or "\\" in filnamn:
        raise GeografiFel(f"Ogiltigt filnamn för fältlager: {filnamn!r}.")
    ext = Path(rent_namn).suffix.lower()
    if ext not in TILLATNA_FALT_FILANDELSER:
        raise GeografiFel(
            f"Formatet {ext!r} stöds inte för fältlager. Använd GeoPackage (.gpkg) eller GeoJSON (.geojson/.json)."
        )
    if not isinstance(ra_bytes, bytes) or len(ra_bytes) == 0:
        raise GeografiFel("Fältlagerfilen är tom.")
    if len(ra_bytes) > MAX_FALTLAGER_BYTES:
        raise GeografiFel(
            f"Fältlagerfilen är {len(ra_bytes)} byte, över maxgränsen {MAX_FALTLAGER_BYTES} byte (10 MB)."
        )

    kall_sha = sha256_bytes(ra_bytes)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_fil = Path(tmpdir) / f"indata{ext}"
        tmp_fil.write_bytes(ra_bytes)
        try:
            gdf = gpd.read_file(tmp_fil)
        except Exception as exc:  # noqa: BLE001
            raise GeografiFel(f"Fältlagerfilen {rent_namn} kunde inte läsas: {exc}") from exc

    if gdf.crs is None:
        raise GeografiFel(f"Fältlagerfilen {rent_namn} saknar känt koordinatsystem (CRS).")
    try:
        gdf = gdf.to_crs(epsg=EPSG_INTERN)
    except Exception as exc:  # noqa: BLE001
        raise GeografiFel(f"Fältlagerfilen {rent_namn} kunde inte omprojiceras till EPSG:3011: {exc}") from exc

    features: list[dict[str, Any]] = []
    varningar: list[str] = []
    antal_poly = 0
    antal_pkt = 0
    antal_avvisade = 0
    per_klass: dict[str, int] = {}

    for _, rad in gdf.iterrows():
        geom = rad.geometry
        if geom is None or geom.is_empty:
            antal_avvisade += 1
            continue
        gtyp = geom.geom_type
        if gtyp not in ("Polygon", "MultiPolygon", "Point", "MultiPoint"):
            antal_avvisade += 1
            continue
        bounds = geom.bounds
        if not all(math.isfinite(v) for v in bounds):
            antal_avvisade += 1
            continue

        props_in = {str(k): v for k, v in rad.items() if k != "geometry"}
        is_poly = gtyp in ("Polygon", "MultiPolygon")
        ra_typ = _sanera_kort_falt(_hitta_varde(props_in, ("objekt_typ", "typ", "type", "kategori")), 40)
        if ra_typ and ra_typ.lower() in ("naturvardesobjekt", "nvi", "polygon", "biotop"):
            obj_typ = "naturvardesobjekt"
        elif ra_typ and ra_typ.lower() in ("skyddsvart_trad", "trad", "träd", "skyddsvärt träd", "punkt"):
            obj_typ = "skyddsvart_trad"
        else:
            obj_typ = "naturvardesobjekt" if is_poly else "skyddsvart_trad"

        nvi_klass = _tolka_nvi_klass(_hitta_varde(props_in, ("nvi_klass", "naturvardesklass", "klass", "nvi")))
        biotop = _sanera_kort_falt(_hitta_varde(props_in, ("biotop", "biotoptyp", "naturtyp")))
        tradslag = _sanera_kort_falt(_hitta_varde(props_in, ("tradslag", "trädslag", "art", "species")))
        stamomkrets = _tolka_positivt_tal(
            _hitta_varde(props_in, ("stamomkrets_cm", "stamomkrets", "omkrets_cm", "omkrets"))
        )

        if is_poly:
            antal_poly += 1
        else:
            antal_pkt += 1
        if nvi_klass is not None:
            kn = str(nvi_klass)
            per_klass[kn] = per_klass.get(kn, 0) + 1

        # Strikt allowlist — 0 PII, inga övriga kolumner sparas någonsin!
        clean_props: dict[str, Any] = {
            "objekt_typ": obj_typ,
            "nvi_klass": nvi_klass,
            "biotop": biotop,
            "tradslag": tradslag,
            "stamomkrets_cm": stamomkrets,
        }
        features.append(
            {
                "type": "Feature",
                "properties": clean_props,
                "geometry": mapping(geom),
            }
        )

    if not features:
        raise GeografiFel(
            f"Fältlagerfilen {rent_namn} innehåller inga giltiga polygon- eller punktobjekt."
        )
    if antal_avvisade > 0:
        varningar.append(f"{antal_avvisade} objekt med ogiltig eller ej stödd geometri ignorerades.")

    # Deterministisk sortering av features
    features.sort(
        key=lambda f: (
            str(f["properties"]["objekt_typ"]),
            int(f["properties"]["nvi_klass"] or 99),
            str(f["properties"]["biotop"] or f["properties"]["tradslag"] or ""),
            tuple(round(v, 3) for v in shape(f["geometry"]).bounds),
        )
    )
    geojson_dok = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": f"EPSG:{EPSG_INTERN}"}},
        "features": features,
    }
    geojson_text = json.dumps(geojson_dok, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    geojson_sha = sha256_bytes(geojson_text.encode("utf-8"))

    mal_mapp = Path(projekt_mapp) / FALTLAGER_MAPP
    _atomisk_skriv_text(mal_mapp / FALTLAGER_GEOJSON, geojson_text)
    meta = {
        "format": FORMAT_FALTLAGER,
        "epsg": EPSG_INTERN,
        "filnamn": rent_namn,
        "kallfil_sha256": kall_sha,
        "geojson_sha256": geojson_sha,
        "antal_objekt": len(features),
        "antal_polygoner": antal_poly,
        "antal_punkter": antal_pkt,
        "per_nvi_klass": dict(sorted(per_klass.items())),
        "sparade_falt": list(SPARADE_FALT_KOLUMNER),
        "varningar": varningar,
    }
    _atomisk_skriv_text(
        mal_mapp / FALTLAGER_META,
        json.dumps(meta, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )
    return EkologiFaltlagerPost(
        filnamn=rent_namn,
        kallfil_sha256=kall_sha,
        geojson_sha256=geojson_sha,
        antal_objekt=len(features),
        antal_polygoner=antal_poly,
        antal_punkter=antal_pkt,
        per_nvi_klass=dict(sorted(per_klass.items())),
        geojson=geojson_dok,
        varningar=tuple(varningar),
    )


def las_ekologi_faltlager(projekt_mapp: str | Path) -> EkologiFaltlagerPost | None:
    """Läser och SHA-256-verifierar projektets sparade fältlager (None om inget importerats)."""
    mal_mapp = Path(projekt_mapp) / FALTLAGER_MAPP
    meta_fil = mal_mapp / FALTLAGER_META
    geo_fil = mal_mapp / FALTLAGER_GEOJSON
    if not meta_fil.exists() and not geo_fil.exists():
        return None
    if not meta_fil.is_file() or not geo_fil.is_file():
        raise GeografiFel("Projektets ekologifältlager är ofullständigt (saknar metadata eller GeoJSON).")
    try:
        meta = json.loads(meta_fil.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GeografiFel(f"Projektets fältlager-metadata går inte att läsa: {exc}") from exc
    if not isinstance(meta, dict) or meta.get("format") != FORMAT_FALTLAGER:
        raise GeografiFel("Ogiltigt format för projektets ekologifältlager.")
    forvantad = str(meta.get("geojson_sha256") or "")
    faktisk = sha256_fil(geo_fil)
    if not forvantad or faktisk != forvantad:
        raise GeografiFel(
            f"Projektets ekologifältlager har ändrats (SHA-256 {faktisk[:12]} != {forvantad[:12]})."
        )
    geojson_dok = json.loads(geo_fil.read_text(encoding="utf-8"))
    return EkologiFaltlagerPost(
        filnamn=str(meta.get("filnamn") or ""),
        kallfil_sha256=str(meta.get("kallfil_sha256") or ""),
        geojson_sha256=faktisk,
        antal_objekt=int(meta.get("antal_objekt") or 0),
        antal_polygoner=int(meta.get("antal_polygoner") or 0),
        antal_punkter=int(meta.get("antal_punkter") or 0),
        per_nvi_klass={str(k): int(v) for k, v in (meta.get("per_nvi_klass") or {}).items()},
        geojson=geojson_dok,
        varningar=tuple(str(v) for v in (meta.get("varningar") or [])),
    )


# Kompatibilitetsexport av Point för ev. anropare
_ = Point

__all__ = [
    "ARTLISTA_FIL",
    "FALTLAGER_GEOJSON",
    "FALTLAGER_MAPP",
    "FALTLAGER_META",
    "FORMAT_ARTFYND_PUBLIKA",
    "FORMAT_FALTLAGER",
    "FORMAT_SKYDDAD_NATUR",
    "MAX_FALTLAGER_BYTES",
    "PUBLIKA_FYND_FIL",
    "SKOG_GPKG",
    "SKRIVBORD_SAMMANFATTNING_FIL",
    "SKYDD_GPKG",
    "SPARADE_FALT_KOLUMNER",
    "EkologiFaltlagerPost",
    "EkologiSkrivbordUnderlag",
    "bygg_artfynd_publika_lager",
    "bygg_nvi_faltchecklista",
    "bygg_skyddad_natur_lager",
    "importera_ekologi_faltlager",
    "las_ekologi_faltlager",
    "las_ekologi_skrivbord",
]
