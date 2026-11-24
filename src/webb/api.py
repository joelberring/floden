"""Lokalt HTTP/JSON-API för Flodens webbklient (T10)."""

from __future__ import annotations

import argparse
import base64
import json
import logging
import math
import mimetypes
import os
import tempfile
import threading
import time
import tomllib
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from buller.optimering import (
    STANDARD_RIKTVARDE_DB,
    BullerOptimeringFel,
    detektera_akustiska_bifurkationer,
    valj_bullerskarmar_k,
)
from dagsljus.horisont import HorisontFel
from dagsljus.intervall import horisont_1d_konvext_holje, kor_monoton_svf_intervall
from dynamik.ensemble import EnsembleResultat, EnsembleScenario, kor_ensemble
from dynamik.handelsestyrd import kor_dynamik_handelsestyrd
from dynamik.intervall import kor_monoton_intervallanalys
from dynamik.nodmodell import DynamikFel, DynamikResultat, EntrePunkt
from dynamik.risk import berakna_kumulativ_oversvamningsrisk
from gemensamt.hash import sha256_bytes, sha256_fil
from gemensamt.konfig import las_konfig
from gemensamt.koordinater import EPSG_INTERN, HOJDSYSTEM
from gemensamt.manifest import KONTRAKT_K0, FilPost, RunManifest, skapa_manifest
from gemensamt.regn import (
    KONTRAKT_K4,
    STANDARD_ATERKOMSTTIDER_AR,
    STANDARD_KLIMATFAKTORER,
    STANDARD_VARAKTIGHET_MIN,
    RegnFel,
    lista_regnforval,
    skapa_regn_fran_spec,
)
from geografi.modell import STANDARD_GEOROT
from konto import Forfragan, KontoAdapter, KontoKonfig, las_konto_konfig
from lapp.experiment import (
    ExperimentAtgarder,
    ExperimentFel,
    ExperimentResultat,
    GrundmodellRattning,
    RattningResultat,
    tillampa_experiment,
    tillampa_grundmodell_rattning,
)
from lapp.fore_efter import ForeEfterResultat, kor_fore_efter
from lapp.kontrakt import (
    KONTRAKT_K3,
    Gatusektion,
    HojdYta,
    LappByggnad,
    LappFel,
    LappGata,
    LappMagasin,
    LappMarkslag,
    ProjektLapp,
    las_gatusektioner_fran_konfig,
)
from luft.kanslighet import kor_monoton_mkn_intervall
from luft.typer import LuftFel
from mark_forluster.koppling import berakna_effektivt_regn_k5
from mark_forluster.parametrar import ForlustFel
from rapport.export import RapportExportFel, generera_rapport
from rapport.kartor import rendera_djupkarta_png
from rapport.kontroll import RapportKontrollFel
from rapport.underlag import (
    GILTIGA_RAPPORTTYPER,
    KartReferens,
    RapportUnderlag,
    RapportUnderlagFel,
    TabellData,
)
from rutt.kritiska import valj_kritisk_insats_k
from rutt.parametrar import (
    FordonParametrar,
    RuttParametrarFel,
    RuttParametrarK7,
)
from rutt.risk import berakna_vag_kumulativ_risk
from rutt.vagnat import VagKant, VagNat, VagNatFel, VagNod
from sankgraf.bifurkation import (
    STANDARD_K_SIGMA,
    STANDARD_REFERENSREGN_MM,
    detektera_bifurkationer,
)
from sankgraf.fyllning import statisk_fyllning
from sankgraf.hierarki import bygg_sankgraf
from sankgraf.takvatten import med_takvatten_graf
from sankgraf.typer import (
    SankGraf,
    SankGrafFel,
    las_brytlinjer_fran_gpkg,
    las_oppningar_fran_gpkg,
)
from testsvit.k1_io import (
    BrytlinjePost,
    GeoTransformInfo,
    K1FormatFel,
    OppningPost,
    las_geotiff,
    las_regn_csv,
)
from testsvit.korare import Testfall, TestsvitFel, hitta_testfall
from tvad.felindikator import berakna_2d_felindikator
from webb.fel import FlodenWebbFel
from webb.fragor import FragaTjanst
from webb.kartverktyg import LAGE_IDAG_PREFIX, KartverktygTjanst
from webb.moduler import lista_moduler
from webb.projekt import PROJEKT_ID as PROJEKT_ID_DEMO
from webb.projekt import ProjektTjanst
from webb.projektskapa import ProjektSkapaTjanst
from webb.prov import PREFIX as PROV_PREFIX
from webb.prov import prov_svar
from webb.rapportjobb import RapportJobbTjanst
from webb.rendering import (
    DEFAULT_KALLOR,
    RenderingFel,
    bbox_3011_till_wgs84,
    brytlinjer_till_geojson,
    byggnader_till_geojson,
    koordinat_3011_till_wgs84,
    rendera_diff_png_base64,
    rendera_djup_png_base64,
    rendera_hillshade_png_base64,
    rendera_risk_png_base64,
    sankgraf_flodesvagar_till_geojson,
    sankgraf_noder_till_geojson,
)
from webb.vy3d import Vy3dTjanst

API_VERSION = "0.1.0"
_ENSEMBLE_FAKTORER: tuple[tuple[str, float, float], ...] = (
    ("p10-låg", 0.85, 2.0),
    ("p25-måttlig", 0.925, 1.0),
    ("p50-median", 1.0, 0.0),
    ("p75-hög", 1.075, 0.0),
    ("p90-extrem", 1.15, 0.0),
)


@dataclass(frozen=True)
class LaddatOmrade:
    """Inläst K1-område redo för lagervisning och simulering."""

    omrade_id: str
    fall: Testfall
    mark_z: NDArray[np.float32]
    osakerhet_z: NDArray[np.float32]
    markslag: NDArray[np.uint8]
    geo: GeoTransformInfo
    brytlinjer: tuple[BrytlinjePost, ...]
    oppningar: tuple[OppningPost, ...]
    regn_tid_s: tuple[float, ...]
    regn_intensitet_mm_h: tuple[float, ...]
    bas_recept_sha256: str
    kallor: tuple[str, ...]
    bas_graf: SankGraf


@dataclass(frozen=True)
class _K1Markmodell:
    """Det bifurkationsanalysen behöver ur en K1-mapp: raster, geo och sänkgraf (med takvatten)."""

    mark_z: NDArray[np.float32]
    osakerhet_z: NDArray[np.float32]
    markslag: NDArray[np.uint8]
    geo: GeoTransformInfo
    brytlinjer: tuple[BrytlinjePost, ...]
    oppningar: tuple[OppningPost, ...]
    bas_graf: SankGraf


def _las_k1_markmodell(indata: Path, *, takvatten_mapp: Path | None = None) -> _K1Markmodell:
    """Läser en K1-mapp (`mark.tif`, `osakerhet.tif`, `markslag.tif`, valfria gpkg) och bygger
    sänkgrafen. Med `takvatten_mapp` (K1 v2-ruta) tas byggnaderna ur rutans `byggnader.gpkg`, som i
    vattenlösaren (`sankgraf.fyllning.kor_statisk_k1_mapp`); annars ur grafens markslag."""
    mark_raw, geo = las_geotiff(indata / "mark.tif")
    osak_raw, _ = las_geotiff(indata / "osakerhet.tif")
    slag_raw, _ = las_geotiff(indata / "markslag.tif")
    mark_z = np.asarray(mark_raw, dtype=np.float32)
    osak_z = np.asarray(osak_raw, dtype=np.float32)
    markslag = np.asarray(slag_raw, dtype=np.uint8)
    bryt_fil = indata / "brytlinjer.gpkg"
    brytlinjer = tuple(las_brytlinjer_fran_gpkg(bryt_fil)) if bryt_fil.is_file() else ()
    opp_fil = indata / "oppningar.gpkg"
    oppningar = tuple(las_oppningar_fran_gpkg(opp_fil)) if opp_fil.is_file() else ()
    graf = bygg_sankgraf(
        mark_z, osak_z, markslag, geo, brytlinjer=brytlinjer, oppningar=oppningar
    )
    return _K1Markmodell(
        mark_z=mark_z,
        osakerhet_z=osak_z,
        markslag=markslag,
        geo=geo,
        brytlinjer=brytlinjer,
        oppningar=oppningar,
        bas_graf=med_takvatten_graf(graf, takvatten_mapp),
    )


def _tolka_tal(payload: Mapping[str, Any], namn: str, standard: float) -> float:
    """Ett valfritt tal ur JSON-kroppen: ändligt och minst 0, annars 400 med ett svenskt fel.

    Text och sant/falskt godtas inte som tal (`"2"` och `true` är fel, inte 2 och 1), och
    `NaN`/`Infinity`, som Pythons JSON-läsare släpper igenom, avvisas."""
    if namn not in payload or payload[namn] is None:
        return standard
    varde = payload[namn]
    if isinstance(varde, bool) or not isinstance(varde, int | float):
        raise FlodenWebbFel(f"Fältet '{namn}' ska vara ett tal.")
    try:
        tal = float(varde)
    except OverflowError as exc:
        raise FlodenWebbFel(f"Fältet '{namn}' är för stort för att vara ett tal.") from exc
    if not math.isfinite(tal):
        raise FlodenWebbFel(f"Fältet '{namn}' ska vara ett ändligt tal, inte NaN eller oändligt.")
    if tal < 0.0:
        raise FlodenWebbFel(f"Fältet '{namn}' får inte vara negativt (fick {tal:g}).")
    return tal


def _tolka_heltal(payload: Mapping[str, Any], namn: str, standard: int, lo: int, hi: int) -> int:
    """Ett valfritt heltal i [lo, hi] ur JSON-kroppen (som `_tolka_tal`, men utan decimaler)."""
    v = _tolka_tal(payload, namn, float(standard))
    if v != int(v) or not lo <= v <= hi:
        raise FlodenWebbFel(f"Fältet '{namn}' ska vara ett heltal mellan {lo} och {hi}.")
    return int(v)


def _json_svar(status_kod: int, payload: Mapping[str, Any]) -> tuple[int, dict[str, str], bytes]:
    kropp = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": str(len(kropp)),
        "Cache-Control": "no-store",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }
    return status_kod, headers, kropp


def _skapa_ensemble_scenarier(
    regn_tid_s: tuple[float, ...],
    regn_intensitet_mm_h: tuple[float, ...],
) -> list[EnsembleScenario]:
    scenarier: list[EnsembleScenario] = []
    for namn, faktor, initial_mm in _ENSEMBLE_FAKTORER:
        skalad = [float(v) * faktor for v in regn_intensitet_mm_h]
        scenarier.append(
            EnsembleScenario(
                scenario_id=namn,
                regn_tid_s=regn_tid_s,
                regn_intensitet_mm_h=skalad,
                avrinning_faktor=1.0,
                initial_forlust_mm=initial_mm,
            )
        )
    return scenarier


def _raster_statistik(djup_m: NDArray[np.floating[Any]], geo: GeoTransformInfo) -> dict[str, float]:
    arr = np.asarray(djup_m, dtype=np.float64)
    giltig = np.isfinite(arr)
    if not np.any(giltig):
        return {"max_djup_m": 0.0, "medel_djup_m": 0.0, "oversvammad_area_m2": 0.0}
    cell_area = float(geo.pixelstorlek_x) * float(geo.pixelstorlek_y)
    oversvammade = int(np.sum(giltig & (arr > 0.01)))
    return {
        "max_djup_m": float(np.max(arr[giltig])),
        "medel_djup_m": float(np.mean(arr[giltig])),
        "oversvammad_area_m2": float(oversvammade) * cell_area,
    }


def _tolka_regnserie(
    kropp: Mapping[str, Any],
    standard_tid: tuple[float, ...],
    standard_intensitet: tuple[float, ...],
) -> tuple[tuple[float, ...], tuple[float, ...], dict[str, Any]]:
    """Tolkar regn från förval (`regn_id`), återkomsttid, explicit serie eller blockregn."""
    raw_regn_id = kropp.get("regn_id")
    raw_aterkomst = kropp.get("aterkomsttid_ar")
    if (isinstance(raw_regn_id, str) and raw_regn_id.strip()) or raw_aterkomst is not None:
        regn_id_val = (
            raw_regn_id.strip() if isinstance(raw_regn_id, str) and raw_regn_id.strip() else None
        )
        aterkomst_val = float(raw_aterkomst) if raw_aterkomst is not None else None
        raw_kf = kropp.get("klimatfaktor")
        kf_val = float(raw_kf) if raw_kf is not None else 1.0
        raw_var_min = kropp.get("varaktighet_min")
        var_min_val = float(raw_var_min) if raw_var_min is not None else STANDARD_VARAKTIGHET_MIN
        raw_typ = kropp.get("regntyp") or kropp.get("typ")
        typ_val = str(raw_typ).strip().lower() if raw_typ is not None else "cds"
        try:
            serie = skapa_regn_fran_spec(
                regn_id=regn_id_val,
                aterkomsttid_ar=aterkomst_val,
                klimatfaktor=kf_val,
                varaktighet_min=var_min_val,
                typ=typ_val,
            )
        except RegnFel as exc:
            raise FlodenWebbFel(f"Ogiltig regnspecifikation: {exc}") from exc
        meta: dict[str, Any] = {
            "regn_id": serie.regn_id,
            "namn": serie.namn,
            "total_djup_mm": float(serie.total_djup_mm),
            "max_intensitet_mm_h": float(serie.max_intensitet_mm_h),
            "klimatfaktor": float(serie.klimatfaktor),
            "aterkomsttid_ar": (
                float(serie.aterkomsttid_ar) if serie.aterkomsttid_ar is not None else None
            ),
            "statistikkalla": serie.statistikkalla,
        }
        return serie.tid_s, serie.intensitet_mm_h, meta

    if "regn_tid_s" in kropp or "regn_intensitet_mm_h" in kropp:
        raw_t = kropp.get("regn_tid_s")
        raw_i = kropp.get("regn_intensitet_mm_h")
        if not isinstance(raw_t, list) or not isinstance(raw_i, list):
            raise FlodenWebbFel("'regn_tid_s' och 'regn_intensitet_mm_h' måste vara listor.")
        if len(raw_t) != len(raw_i) or len(raw_t) < 2:
            raise FlodenWebbFel("Regntidsserien måste ha minst 2 tidpunkter och lika längd.")
        tider = tuple(float(x) for x in raw_t)
        intensiteter = tuple(float(x) for x in raw_i)
        return tider, intensiteter, {}

    has_i = "intensitet_mm_h" in kropp and kropp["intensitet_mm_h"] is not None
    has_d = "varaktighet_s" in kropp and kropp["varaktighet_s"] is not None
    if not has_i and not has_d:
        return standard_tid, standard_intensitet, {}

    intensitet = float(kropp["intensitet_mm_h"]) if has_i else float(standard_intensitet[0])
    varaktighet = (
        float(kropp["varaktighet_s"]) if has_d else float(standard_tid[-1] - standard_tid[0])
    )
    if not math.isfinite(intensitet) or intensitet < 0.0:
        raise FlodenWebbFel(f"Ogiltig intensitet_mm_h: {intensitet!r} (måste vara >= 0).")
    if not math.isfinite(varaktighet) or varaktighet <= 0.0:
        raise FlodenWebbFel(f"Ogiltig varaktighet_s: {varaktighet!r} (måste vara > 0).")
    return (0.0, varaktighet), (intensitet, intensitet), {}


def _tolka_forluster(
    kropp: Mapping[str, Any],
) -> tuple[str | None, str, bool, float]:
    """Tolkar valfria T06 markförlustparametrar (`forluster` eller `jordart`)."""
    raw_forl = kropp.get("forluster")
    if raw_forl is None:
        raw_jord = kropp.get("jordart")
        if not isinstance(raw_jord, str) or not raw_jord.strip():
            return None, "normal", False, 2.0
        return (
            raw_jord.strip(),
            str(kropp.get("grundvatten", "normal")).strip().lower(),
            bool(kropp.get("tjale", False)),
            float(kropp.get("initial_mm", 2.0)),
        )

    if not isinstance(raw_forl, Mapping):
        raise FlodenWebbFel("'forluster' måste vara ett JSON-objekt.")
    if raw_forl.get("aktivera") is False:
        return None, "normal", False, 2.0
    raw_jord = raw_forl.get("jordart")
    if not isinstance(raw_jord, str) or not raw_jord.strip():
        return None, "normal", False, 2.0
    return (
        raw_jord.strip(),
        str(raw_forl.get("grundvatten", "normal")).strip().lower(),
        bool(raw_forl.get("tjale", False)),
        float(raw_forl.get("initial_mm", 2.0)),
    )


def _bygg_projektlapp_fran_payload(
    kropp: Mapping[str, Any],
    bas_recept_sha256: str,
) -> ProjektLapp | None:
    """Bygger en K3/v1 `ProjektLapp` från JSON-kropp, eller `None` om ingen lapp skickats."""
    raw_lapp = kropp.get("lapp")
    lapp_data: Mapping[str, Any] = raw_lapp if isinstance(raw_lapp, Mapping) else kropp

    hojdytor: list[HojdYta] = []
    landxml_raw = lapp_data.get("landxml")
    if isinstance(landxml_raw, str) and landxml_raw.strip():
        ordning = str(lapp_data.get("koordinat_ordning", "NEZ"))
        hojdytor.append(
            HojdYta(
                yta_id="webb-landxml-1",
                landxml=landxml_raw,
                koordinat_ordning=ordning,
            )
        )

    for idx, item in enumerate(lapp_data.get("hojdytor", []) or []):
        if isinstance(item, Mapping):
            kopia = dict(item)
            kopia.setdefault("yta_id", f"yta-{idx + 1}")
            hojdytor.append(HojdYta.fran_dict(kopia))
        else:
            raise FlodenWebbFel("Poster i 'hojdytor' måste vara JSON-objekt.")

    gator: list[LappGata] = []
    for idx, item in enumerate(lapp_data.get("gator", []) or []):
        if not isinstance(item, Mapping):
            raise FlodenWebbFel("Poster i 'gator' måste vara JSON-objekt.")
        kopia = dict(item)
        kopia.setdefault("gata_id", f"gata-{idx + 1}")
        kopia.setdefault("sektion", "standard-12m")
        gator.append(LappGata.fran_dict(kopia))

    byggnader: list[LappByggnad] = []
    for idx, item in enumerate(lapp_data.get("byggnader", []) or []):
        if not isinstance(item, Mapping):
            raise FlodenWebbFel("Poster i 'byggnader' måste vara JSON-objekt.")
        kopia = dict(item)
        kopia.setdefault("byggnad_id", f"byggnad-{idx + 1}")
        if (
            "entre_punkt" not in kopia
            and isinstance(kopia.get("polygon"), list)
            and kopia["polygon"]
        ):
            pts = kopia["polygon"]
            mean_e = sum(float(p[0]) for p in pts) / len(pts)
            mean_n = sum(float(p[1]) for p in pts) / len(pts)
            kopia["entre_punkt"] = [mean_e, mean_n]
        byggnader.append(LappByggnad.fran_dict(kopia))

    magasin: list[LappMagasin] = []
    for idx, item in enumerate(lapp_data.get("magasin", []) or []):
        if not isinstance(item, Mapping):
            raise FlodenWebbFel("Poster i 'magasin' måste vara JSON-objekt.")
        kopia = dict(item)
        kopia.setdefault("magasin_id", f"magasin-{idx + 1}")
        magasin.append(LappMagasin.fran_dict(kopia))

    markslag_andringar: list[LappMarkslag] = []
    for idx, item in enumerate(lapp_data.get("markslag", []) or []):
        if not isinstance(item, Mapping):
            raise FlodenWebbFel("Poster i 'markslag' måste vara JSON-objekt.")
        kopia = dict(item)
        kopia.setdefault("omrade_id", f"markslag-{idx + 1}")
        markslag_andringar.append(LappMarkslag.fran_dict(kopia))

    if not (hojdytor or gator or byggnader or magasin or markslag_andringar):
        return None

    lapp_id = str(lapp_data.get("lapp_id") or "webb-lapp-1")
    return ProjektLapp(
        lapp_id=lapp_id,
        bas_recept=(bas_recept_sha256,),
        hojdytor=tuple(hojdytor),
        byggnader=tuple(byggnader),
        gator=tuple(gator),
        magasin=tuple(magasin),
        markslag_andringar=tuple(markslag_andringar),
        beskrivning=str(lapp_data.get("beskrivning", "Skapad via Flodens webbklient")),
    )


def _kopiera_entreer_fran_payload(kropp: Mapping[str, Any]) -> list[EntrePunkt]:
    raw = kropp.get("grann_entreer")
    if not raw:
        return []
    if not isinstance(raw, list):
        raise FlodenWebbFel("'grann_entreer' måste vara en lista.")
    ut: list[EntrePunkt] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise FlodenWebbFel("Varje post i 'grann_entreer' måste vara ett JSON-objekt.")
        for falt in ("e_m", "n_m"):
            if falt not in item:
                raise FlodenWebbFel(f"grann_entreer[{idx}] saknar '{falt}'.")
        ut.append(
            EntrePunkt(
                entre_id=str(item.get("entre_id", f"grann-entre-{idx + 1}")),
                e_m=float(item["e_m"]),
                n_m=float(item["n_m"]),
                gransdjup_m=float(item.get("gransdjup_m", 0.10)),
            )
        )
    return ut


def _ensemble_payload(
    ens: EnsembleResultat,
    geo: GeoTransformInfo,
    *,
    max_skala_m: float = 0.5,
) -> dict[str, Any]:
    p10 = ens.max_djup_percentiler_m[10.0]
    p50 = ens.max_djup_percentiler_m[50.0]
    p90 = ens.max_djup_percentiler_m[90.0]
    return {
        "percentiler": [10, 50, 90],
        "antal_scenarier": len(ens.scenarier),
        "max_volymfel_rel": float(ens.max_volymfel_rel),
        "total_kortid_s": float(ens.total_kortid_s),
        "p10": {
            **rendera_djup_png_base64(p10, max_skala_m=max_skala_m),
            **_raster_statistik(p10, geo),
        },
        "p50": {
            **rendera_djup_png_base64(p50, max_skala_m=max_skala_m),
            **_raster_statistik(p50, geo),
        },
        "p90": {
            **rendera_djup_png_base64(p90, max_skala_m=max_skala_m),
            **_raster_statistik(p90, geo),
        },
    }


STANDARD_FRAGEMAPP = "exempel/fragor"
STANDARD_CACHEMAPP = "data/cache/karna"
STANDARD_KORNINGSMAPP = "data/cache/webb/korningar"
STANDARD_DEMOMAPP = "data/demo/varld"
STANDARD_KLIENTMAPP = "webb/dist"
STANDARD_KARTVERKTYGSMAPP = "data/lokal/kartverktyg"
STANDARD_DATAMAPPAR = ["data/lokal"]


STANDARD_RAPPORTMAPP = "data/lokal/rapporter"
STANDARD_PROJEKTMAPP = "data/lokal/projekt"
"""Kommunprojekten (`floden projekt skapa`); deras `fragor/` syns i frågelistan."""


def _rapportmapp(konfig_fil: Path, rapportmapp: str | Path | None) -> Path:
    """Rapportmappen: argument > `[webb] rapportmapp` > data/lokal/rapporter (från repots rot)."""
    rot = konfig_fil.resolve().parent
    if rapportmapp is None:
        webb: dict[str, Any] = {}
        if konfig_fil.is_file():
            with konfig_fil.open("rb") as fh:
                webb = dict(tomllib.load(fh).get("webb", {}))
        rapportmapp = webb.get("rapportmapp", STANDARD_RAPPORTMAPP)
    p = Path(rapportmapp)
    return p if p.is_absolute() else rot / p


def _skapa_fragetjanst(
    konfig_fil: Path,
    fragemappar: Sequence[str | Path] | None,
    cachemapp: str | Path | None,
    korningsmapp: str | Path | None,
    projektmapp: str | Path | None = None,
) -> FragaTjanst:
    """Sökvägar: argument > miljövariabeln `FLODEN_FRAGEMAPPAR` > `[webb]` i konfigurationen.

    Relativa sökvägar räknas från konfigurationsfilens katalog (repots rot).
    """
    rot = konfig_fil.resolve().parent
    webb: dict[str, Any] = {}
    if konfig_fil.is_file():
        with konfig_fil.open("rb") as fh:
            webb = dict(tomllib.load(fh).get("webb", {}))
    if fragemappar is None:
        miljo = os.environ.get("FLODEN_FRAGEMAPPAR")
        if miljo:
            fragemappar = [m for m in miljo.split(os.pathsep) if m]
        else:
            fragemappar = list(webb.get("fragemappar", [STANDARD_FRAGEMAPP]))

    def abs_(p: str | Path) -> Path:
        pp = Path(p)
        return pp if pp.is_absolute() else rot / pp

    return FragaTjanst(
        rot=rot,
        fragemappar=[abs_(m) for m in fragemappar],
        cachemapp=abs_(cachemapp or webb.get("cachemapp", STANDARD_CACHEMAPP)),
        korningsmapp=abs_(korningsmapp or webb.get("korningsmapp", STANDARD_KORNINGSMAPP)),
        demomapp=abs_(webb.get("demomapp", STANDARD_DEMOMAPP)),
        projektrot=abs_(projektmapp or STANDARD_PROJEKTMAPP),
    )


def _skapa_kartverktyg(
    konfig_fil: Path,
    fragor: FragaTjanst,
    mapp: str | Path | None,
    datamappar: Sequence[str | Path] | None,
    tillat_loopback: bool,
) -> KartverktygTjanst:
    """Kartverktyget: lokal mapp för scenarier och ritade frågor (inte i git) och datamappar."""
    rot = konfig_fil.resolve().parent
    webb: dict[str, Any] = {}
    if konfig_fil.is_file():
        with konfig_fil.open("rb") as fh:
            webb = dict(tomllib.load(fh).get("webb", {}))

    def abs_(p: str | Path) -> Path:
        pp = Path(p).expanduser()
        return pp if pp.is_absolute() else rot / pp

    if datamappar is None:
        miljo = os.environ.get("FLODEN_DATAMAPPAR")
        datamappar = (
            [m for m in miljo.split(os.pathsep) if m]
            if miljo
            else list(webb.get("datamappar", STANDARD_DATAMAPPAR))
        )
    return KartverktygTjanst(
        fragor,
        abs_(mapp or webb.get("kartverktygsmapp", STANDARD_KARTVERKTYGSMAPP)),
        datamappar=[abs_(m) for m in datamappar],
        tillat_loopback=tillat_loopback,
    )


def _binart_svar(
    status_kod: int, innehall: bytes, content_type: str
) -> tuple[int, dict[str, str], bytes]:
    return (
        status_kod,
        {
            "Content-Type": content_type,
            "Content-Length": str(len(innehall)),
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "*",
        },
        innehall,
    )


class FlodenApiApp:
    """Testbar applikationsrouter för Flodens lokala webb-API (T10)."""

    def __init__(
        self,
        *,
        testsvit_rot: str | Path | None = None,
        konfig_fil: str | Path = "config.toml",
        fragemappar: Sequence[str | Path] | None = None,
        cachemapp: str | Path | None = None,
        korningsmapp: str | Path | None = None,
        konto: KontoAdapter | None = None,
        klientmapp: str | Path | None = None,
        kartverktygsmapp: str | Path | None = None,
        datamappar: Sequence[str | Path] | None = None,
        tillat_loopback_underlag: bool = False,
        rapportmapp: str | Path | None = None,
        geomapp: str | Path | None = None,
        projektmapp: str | Path | None = None,
    ) -> None:
        self.rapportmapp = _rapportmapp(Path(konfig_fil), rapportmapp)
        _rot = Path(konfig_fil).resolve().parent

        def _abs(p: str | Path) -> Path:
            return Path(p) if Path(p).is_absolute() else _rot / p

        self.geomapp = _abs(geomapp or STANDARD_GEOROT)
        self.projektmapp = _abs(projektmapp or STANDARD_PROJEKTMAPP)
        self._projektskapa: ProjektSkapaTjanst | None = None
        self._rapportjobb: RapportJobbTjanst | None = None
        self._kartkonfig = (kartverktygsmapp, datamappar, tillat_loopback_underlag)
        self._kartverktyg: KartverktygTjanst | None = None
        self._vy3d: Vy3dTjanst | None = None
        self.konfig_fil = Path(konfig_fil)
        self.testsvit_rot = _testsvitrot(self.konfig_fil, testsvit_rot)
        self.konto = konto if konto is not None else _skapa_konto(self.konfig_fil)
        self.klientmapp = _klientmapp(self.konfig_fil, klientmapp)
        self._fragekonfig = (fragemappar, cachemapp, korningsmapp)
        self._fragor: FragaTjanst | None = None
        self._lock = threading.Lock()
        self._omrade_cache: dict[str, LaddatOmrade] = {}
        self._projektmodell_cache: dict[tuple[str, str, int], _K1Markmodell] = {}
        self._sektioner_cache: dict[str, Gatusektion] | None = None

    @property
    def fragor(self) -> FragaTjanst:
        """Tjänsten för K20-frågor. Skapas vid första användning (inga kataloger vid start)."""
        with self._lock:
            if self._fragor is None:
                self._fragor = _skapa_fragetjanst(
                    self.konfig_fil, *self._fragekonfig, projektmapp=self.projektmapp
                )
            return self._fragor

    @property
    def kartverktyg(self) -> KartverktygTjanst:
        """Kartverktyget (rita planförslag). Skapas vid första användning."""
        fragor = self.fragor
        with self._lock:
            if self._kartverktyg is None:
                self._kartverktyg = _skapa_kartverktyg(
                    self.konfig_fil, fragor, *self._kartkonfig
                )
            return self._kartverktyg

    @property
    def vy3d(self) -> Vy3dTjanst:
        """3D-vyns buffertar (terräng, byggnader, resultat). Skapas vid första användning."""
        kv = self.kartverktyg
        fragor = self.fragor
        with self._lock:
            if self._vy3d is None:
                self._vy3d = Vy3dTjanst(fragor, kv)
            return self._vy3d

    @property
    def projekt_skapa(self) -> ProjektSkapaTjanst:
        """Geografier och nya projekt (`/api/geografier`, `POST /api/projekt`, MCP)."""
        with self._lock:
            if self._projektskapa is None:
                self._projektskapa = ProjektSkapaTjanst(
                    self, geomapp=self.geomapp, projektmapp=self.projektmapp
                )
            return self._projektskapa

    @property
    def rapportjobb(self) -> RapportJobbTjanst:
        """Rapporter som skapas i webben, som jobb med status (K48)."""
        with self._lock:
            if self._rapportjobb is None:
                self._rapportjobb = RapportJobbTjanst(self)
            return self._rapportjobb

    def rapporter_for(self, projekt_id: str) -> list[dict[str, Any]]:
        """Projektets rapporter (R1) med kontrollens utfall och nedladdningsbara filer."""
        from rapport.skissrapport import RapportArkiv, sammanfattning

        arkiv = RapportArkiv(self.rapportmapp)
        return [sammanfattning(r, arkiv) for r in arkiv.lista(projekt_id)]

    def rapport_innehall(self, projekt_id: str, rapport_id: str) -> dict[str, Any]:
        """En rapports rubriker, påståenden och kontrollpunkter (förhandsvisning i webben)."""
        from rapport.skissrapport import RapportArkiv, RapportFel
        from webb.rapportvy import rapport_innehall

        arkiv = RapportArkiv(self.rapportmapp)
        try:
            r = arkiv.las(rapport_id)
        except RapportFel as exc:
            raise FlodenWebbFel(str(exc), status_kod=404) from exc
        if r.projekt != projekt_id:
            raise FlodenWebbFel("Rapporten hör inte till projektet.", status_kod=404)
        return rapport_innehall(r, arkiv)

    def fragekatalog(self, query: Mapping[str, list[str]]) -> dict[str, Any]:
        """Svar för `GET /api/fragekatalog`: katalogens frågor med vad Floden kan belägga (R2).

        Filter (valfria): `omrade`, `villkor` (kommaseparerat), `text`, `ids` (kommaseparerat)."""
        from rapport.fragekatalog import Fragekatalog, FragekatalogFel, fraga_med_formaga

        fil = self.konfig_fil.resolve().parent / "docs" / "utredningar-stockholm" / "fragor.json"
        try:
            katalog = Fragekatalog.las_alla(fil) if fil.is_file() else Fragekatalog.las_alla()
        except FragekatalogFel as exc:
            raise FlodenWebbFel(str(exc), status_kod=500) from exc

        def lista(namn: str) -> list[str] | None:
            v = [x.strip() for d in query.get(namn, []) for x in d.split(",") if x.strip()]
            return v or None

        text = (query.get("text") or [""])[0]
        fragor = katalog.sok(
            omrade=(query.get("omrade") or [""])[0] or None,
            villkor=lista("villkor"),
            text=text or None,
            ids=lista("ids"),
        )
        return {
            "antal": len(fragor),
            "granskningsdatum": katalog.granskningsdatum,
            "fragor": [fraga_med_formaga(f) for f in fragor],
            "anmarkning": "Katalogen är redaktionellt underlag sammanställt med AI-stöd och ska "
            "granskas; val av regel och bedömning avgörs per ärende.",
        }

    def rapportfil(
        self, projekt_id: str, rapport_id: str, fmt: str
    ) -> tuple[int, dict[str, str], bytes]:
        """Den senast exporterade Word- eller PDF-filen (oförändrad sedan exporten)."""
        from rapport.skissrapport import RapportArkiv, RapportFel

        typer = {
            "word": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "pdf": "application/pdf",
        }
        if fmt not in typer:
            raise FlodenWebbFel("Formatet ska vara 'word' eller 'pdf'.", status_kod=404)
        arkiv = RapportArkiv(self.rapportmapp)
        try:
            r = arkiv.las(rapport_id)
        except RapportFel as exc:
            raise FlodenWebbFel(str(exc), status_kod=404) from exc
        if r.projekt != projekt_id:
            raise FlodenWebbFel("Rapporten hör inte till projektet.", status_kod=404)
        fil = arkiv.exportfil(r, fmt)
        if fil is None:
            raise FlodenWebbFel(
                f"Rapporten har ingen giltig {fmt}-export (exportera efter en godkänd kontroll).",
                status_kod=404,
            )
        status, headers, kropp = _binart_svar(200, fil.read_bytes(), typer[fmt])
        headers["Content-Disposition"] = f'attachment; filename="{fil.name}"'
        return status, headers, kropp

    @property
    def projekt(self) -> ProjektTjanst:
        """Demokvarteret som projekt (ur demokörningarna)."""
        return ProjektTjanst(self.fragor)

    def _demo_moduler(self) -> set[str]:
        """Domäner (modulnamn) som är förkörda i demon, ur demons översikt och frågornas lösare."""
        demo = self.fragor.demo()
        if not demo.get("finns"):
            return set()
        ut: set[str] = set()
        for ref, del_ in demo.get("delar", {}).items():
            if not isinstance(del_, dict) or "korning_id" not in del_:
                continue
            try:
                fraga = self.fragor.hamta_fraga(ref)
            except FlodenWebbFel:
                continue
            ut.update(a["doman"] for a in fraga.get("analyser", []) if a.get("doman"))
        return ut

    def hamta_sektioner(self) -> dict[str, Gatusektion]:
        """Läser gatusektioner från `config.toml` med trådsäker cache."""
        with self._lock:
            if self._sektioner_cache is None:
                if self.konfig_fil.is_file():
                    self._sektioner_cache = las_gatusektioner_fran_konfig(self.konfig_fil)
                else:
                    self._sektioner_cache = {}
            return self._sektioner_cache

    def _upptack_omraden(self) -> dict[str, Testfall]:
        try:
            fall_lista = hitta_testfall(self.testsvit_rot)
        except TestsvitFel as exc:
            raise FlodenWebbFel(f"Kunde inte läsa testsviten: {exc}", status_kod=500) from exc

        tillgangliga: dict[str, Testfall] = {}
        for fall in fall_lista:
            mark_fil = fall.mapp / "indata" / "mark.tif"
            if mark_fil.is_file():
                tillgangliga[fall.mapp.name] = fall
        return tillgangliga

    def ladda_omrade(self, omrade_id: str) -> LaddatOmrade:
        """Läser in och cachar K1-markmodell och sänkgraf för ett område."""
        with self._lock:
            if omrade_id in self._omrade_cache:
                return self._omrade_cache[omrade_id]

        katalog = self._upptack_omraden()
        fall = katalog.get(omrade_id)
        if fall is None:
            for kandidat in katalog.values():
                if kandidat.id.upper() == omrade_id.upper():
                    fall = kandidat
                    omrade_id = kandidat.mapp.name
                    break
        if fall is None:
            raise FlodenWebbFel(
                f"Okänt område '{omrade_id}'. Tillgängliga: {', '.join(sorted(katalog))}.",
                status_kod=404,
            )

        indata = fall.mapp / "indata"
        try:
            modell = _las_k1_markmodell(indata)
            regn_rader = las_regn_csv(indata / "regn.csv")
            regn_tid = tuple(float(r[0]) for r in regn_rader)
            regn_int = tuple(float(r[1]) for r in regn_rader)

            recept_fil = indata / "recept.json"
            if recept_fil.is_file():
                bas_sha = sha256_fil(recept_fil)
            else:
                bas_sha = sha256_bytes(omrade_id.encode("utf-8"))
        except (K1FormatFel, SankGrafFel, OSError) as exc:
            raise FlodenWebbFel(
                f"Kunde inte ladda område '{omrade_id}': {exc}",
                status_kod=500,
            ) from exc

        laddat = LaddatOmrade(
            omrade_id=omrade_id,
            fall=fall,
            mark_z=modell.mark_z,
            osakerhet_z=modell.osakerhet_z,
            markslag=modell.markslag,
            geo=modell.geo,
            brytlinjer=modell.brytlinjer,
            oppningar=modell.oppningar,
            regn_tid_s=regn_tid,
            regn_intensitet_mm_h=regn_int,
            bas_recept_sha256=bas_sha,
            kallor=DEFAULT_KALLOR,
            bas_graf=modell.bas_graf,
        )
        with self._lock:
            self._omrade_cache[omrade_id] = laddat
        return laddat

    def ladda_projektmodell(self, projekt_id: str) -> _K1Markmodell:
        """Projektets egen markmodell (K1-mappen), cachad tills filen ändras.

        Oberoende av testsviten. Okänt projekt eller saknad markmodell ger 404; behörigheten
        kontrolleras av anroparen (`hantera_http`)."""
        mapp = self.projekt.markmodell_mapp(projekt_id)
        nyckel = (projekt_id, str(mapp), (mapp / "mark.tif").stat().st_mtime_ns)
        with self._lock:
            cachad = self._projektmodell_cache.get(nyckel)
        if cachad is not None:
            return cachad
        try:
            modell = _las_k1_markmodell(mapp, takvatten_mapp=mapp)
        except (K1FormatFel, SankGrafFel, OSError) as exc:
            raise FlodenWebbFel(
                f"Kunde inte ladda markmodellen för projektet '{projekt_id}': {exc}",
                status_kod=500,
            ) from exc
        with self._lock:
            self._projektmodell_cache = {
                k: v for k, v in self._projektmodell_cache.items() if k[0] != projekt_id
            }
            self._projektmodell_cache[nyckel] = modell
        return modell

    def halsa(self) -> dict[str, Any]:
        """Svar för `GET /api/halsa`."""
        return {
            "status": "ok",
            "version": API_VERSION,
            # Commiten avbildningen byggdes från (Dockerfile ARG FLODEN_COMMIT), annars null.
            "commit": os.environ.get("FLODEN_COMMIT") or None,
            "kontrakt": {
                "manifest": KONTRAKT_K0,
                "lapp": KONTRAKT_K3,
                "resultat": "K6/v1",
            },
            "epsg": EPSG_INTERN,
            "hojdsystem": HOJDSYSTEM,
            "kallor": list(DEFAULT_KALLOR),
            "gatusektioner": [sek.till_dict() for _, sek in sorted(self.hamta_sektioner().items())],
        }

    def lista_omraden(self) -> dict[str, Any]:
        """Svar för `GET /api/omraden`."""
        katalog = self._upptack_omraden()
        poster: list[dict[str, Any]] = []
        for omrade_id, fall in sorted(katalog.items()):
            indata = fall.mapp / "indata"
            _, geo = las_geotiff(indata / "mark.tif")
            regn = las_regn_csv(indata / "regn.csv")
            bbox_3011 = [
                float(geo.x_min),
                float(geo.y_min),
                float(geo.x_max),
                float(geo.y_max),
            ]
            bbox_wgs84 = list(bbox_3011_till_wgs84(geo.x_min, geo.y_min, geo.x_max, geo.y_max))
            poster.append(
                {
                    "omrade_id": omrade_id,
                    "kod": fall.id,
                    "namn": fall.namn,
                    "grupp": fall.grupp,
                    "beskrivning": fall.beskrivning,
                    "bredd_px": int(geo.bredd),
                    "hojd_px": int(geo.hojd),
                    "upplosning_m": float(geo.pixelstorlek_x),
                    "bbox_3011": bbox_3011,
                    "bbox_wgs84": bbox_wgs84,
                    "standard_regn": {
                        "intensitet_mm_h": float(regn[0][1]),
                        "varaktighet_s": float(regn[-1][0] - regn[0][0]),
                    },
                    "kallor": list(DEFAULT_KALLOR),
                }
            )
        return {
            "epsg": EPSG_INTERN,
            "hojdsystem": HOJDSYSTEM,
            "omraden": poster,
        }

    def hamta_omrade_lager(self, omrade_id: str) -> dict[str, Any]:
        """Svar för `GET /api/omraden/<omrade_id>/lager`."""
        omrade = self.ladda_omrade(omrade_id)
        geo = omrade.geo

        hillshade = rendera_hillshade_png_base64(
            omrade.mark_z,
            omrade.markslag,
            pixelstorlek_m=geo.pixelstorlek_x,
        )
        bas_dyn = kor_dynamik_handelsestyrd(
            omrade.bas_graf,
            omrade.regn_tid_s,
            omrade.regn_intensitet_mm_h,
        )
        scenarier = _skapa_ensemble_scenarier(omrade.regn_tid_s, omrade.regn_intensitet_mm_h)
        bas_ens = kor_ensemble(omrade.bas_graf, scenarier, percentiler=(10.0, 50.0, 90.0))

        osak_giltig = omrade.osakerhet_z[np.isfinite(omrade.osakerhet_z)]
        osak_medel = float(np.mean(osak_giltig)) if osak_giltig.size > 0 else 0.0
        osak_max = float(np.max(osak_giltig)) if osak_giltig.size > 0 else 0.0

        bbox_3011 = [float(geo.x_min), float(geo.y_min), float(geo.x_max), float(geo.y_max)]
        bbox_wgs84 = list(bbox_3011_till_wgs84(geo.x_min, geo.y_min, geo.x_max, geo.y_max))
        max_skala = max(0.35, float(np.max(bas_ens.max_djup_percentiler_m[90.0])))

        return {
            "omrade_id": omrade.omrade_id,
            "kod": omrade.fall.id,
            "namn": omrade.fall.namn,
            "beskrivning": omrade.fall.beskrivning,
            "epsg": EPSG_INTERN,
            "hojdsystem": HOJDSYSTEM,
            "bas_recept_sha256": omrade.bas_recept_sha256,
            "upplosning_m": float(geo.pixelstorlek_x),
            "bredd_px": int(geo.bredd),
            "hojd_px": int(geo.hojd),
            "bbox_3011": bbox_3011,
            "bbox_wgs84": bbox_wgs84,
            "standard_regn": {
                "tid_s": list(omrade.regn_tid_s),
                "intensitet_mm_h": list(omrade.regn_intensitet_mm_h),
                "intensitet_mm_h_medel": float(omrade.regn_intensitet_mm_h[0]),
                "varaktighet_s": float(omrade.regn_tid_s[-1] - omrade.regn_tid_s[0]),
            },
            "hillshade": hillshade,
            "bas_max_djup": {
                **rendera_djup_png_base64(bas_dyn.max_djup_m, max_skala_m=max_skala),
                **_raster_statistik(bas_dyn.max_djup_m, geo),
            },
            "sankor": sankgraf_noder_till_geojson(omrade.bas_graf),
            "flodesvagar": sankgraf_flodesvagar_till_geojson(omrade.bas_graf),
            "brytlinjer": brytlinjer_till_geojson(omrade.brytlinjer),
            "byggnader": byggnader_till_geojson(omrade.markslag, geo),
            "osakerhet": {
                "mark_osakerhet_medel_m": osak_medel,
                "mark_osakerhet_max_m": osak_max,
                **_ensemble_payload(bas_ens, geo, max_skala_m=max_skala),
            },
            "gatusektioner": [sek.till_dict() for _, sek in sorted(self.hamta_sektioner().items())],
            "kallor": list(omrade.kallor),
        }

    def kor_bifurkation_analys(self, kropp_bytes: bytes) -> dict[str, Any]:
        """Svar för `POST /api/analys/bifurkationer`: bifurkationspunkter + 2D-felindikator."""
        if not kropp_bytes.strip():
            raise FlodenWebbFel("Tom förfrågningskropp; JSON-objekt med 'omrade_id' krävs.")
        try:
            payload = json.loads(kropp_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise FlodenWebbFel(f"Ogiltig JSON i förfrågan: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise FlodenWebbFel("Förfrågningskroppen måste vara ett JSON-objekt.")
        omrade_id = payload.get("omrade_id")
        if not isinstance(omrade_id, str) or not omrade_id.strip():
            raise FlodenWebbFel("Obligatoriskt fält 'omrade_id' saknas eller är tomt.")

        k_sigma = _tolka_tal(payload, "k_sigma", STANDARD_K_SIGMA)
        referensregn_mm = _tolka_tal(payload, "referensregn_mm", STANDARD_REFERENSREGN_MM)
        # Ett projekt (t.ex. demokvarteret) läses ur sin egen markmodell; övriga id är testsviten.
        if self.projekt.ar_projekt(omrade_id):
            omrade: _K1Markmodell | LaddatOmrade = self.ladda_projektmodell(omrade_id)
        else:
            omrade = self.ladda_omrade(omrade_id)

        punkter = detektera_bifurkationer(
            omrade.bas_graf,
            osakerhet_m=omrade.osakerhet_z,
            k_sigma=k_sigma,
            referensregn_mm=referensregn_mm,
        )
        stat = statisk_fyllning(omrade.bas_graf, regn_m=referensregn_mm / 1000.0)
        ind = berakna_2d_felindikator(
            omrade.mark_z,
            omrade.markslag,
            omrade.geo,
            omrade.bas_graf,
            stat,
            topp_regn_mm_h=referensregn_mm,
        )
        return {
            "format": "floden-bifurkation-felindikator/v1",
            "omrade_id": omrade_id,
            "k_sigma": k_sigma,
            "referensregn_mm": referensregn_mm,
            "antal_instabila": len(punkter),
            "punkter": [p.till_dict() for p in punkter],
            "felindikator": {
                "andel_2d_yta": ind.andel_2d_yta,
                "antal_2d_celler": ind.antal_2d_celler,
                "antal_aktiva_celler": ind.antal_aktiva_celler,
                "antal_noder_2d": ind.antal_noder_2d,
                "antal_noder_totalt": ind.antal_noder_totalt,
            },
        }

    def _tolka_intervall(
        self,
        payload: Mapping[str, Any],
        namn: str,
        standard: tuple[float, float],
    ) -> tuple[float, float]:
        """Tolkar ett `[lo, hi]`-intervall ur JSON-kroppen (ändliga tal, 0 <= lo <= hi)."""
        if namn not in payload or payload[namn] is None:
            return standard
        ra = payload[namn]
        if not isinstance(ra, list | tuple) or len(ra) != 2:
            raise FlodenWebbFel(f"Fältet '{namn}' ska vara en lista med två tal [min, max].")
        lo_raw, hi_raw = ra[0], ra[1]
        if isinstance(lo_raw, bool) or isinstance(hi_raw, bool):
            raise FlodenWebbFel(f"Fältet '{namn}' ska innehålla tal, inte sant/falskt.")
        if not isinstance(lo_raw, int | float) or not isinstance(hi_raw, int | float):
            raise FlodenWebbFel(f"Fältet '{namn}' ska innehålla två tal [min, max].")
        lo, hi = float(lo_raw), float(hi_raw)
        if not (math.isfinite(lo) and math.isfinite(hi)):
            raise FlodenWebbFel(f"Fältet '{namn}' ska bara innehålla ändliga tal.")
        if lo < 0.0 or hi < lo:
            raise FlodenWebbFel(f"Fältet '{namn}' måste uppfylla 0 <= min <= max.")
        return (lo, hi)

    def _ladda_analys_omrade(self, omrade_id: str) -> _K1Markmodell | LaddatOmrade:
        if self.projekt.ar_projekt(omrade_id):
            return self.ladda_projektmodell(omrade_id)
        return self.ladda_omrade(omrade_id)

    def kor_intervall_analys(self, kropp_bytes: bytes) -> dict[str, Any]:
        """Svar för `POST /api/analys/intervall`: monoton vattenintervallanalys med grenunion."""
        if not kropp_bytes.strip():
            raise FlodenWebbFel("Tom förfrågningskropp; JSON-objekt med 'omrade_id' krävs.")
        try:
            payload = json.loads(kropp_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise FlodenWebbFel(f"Ogiltig JSON i förfrågan: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise FlodenWebbFel("Förfrågningskroppen måste vara ett JSON-objekt.")
        omrade_id = payload.get("omrade_id")
        if not isinstance(omrade_id, str) or not omrade_id.strip():
            raise FlodenWebbFel("Obligatoriskt fält 'omrade_id' saknas eller är tomt.")

        klimat_iv = self._tolka_intervall(payload, "klimatfaktor_intervall", (1.0, 1.25))
        forlust_iv = self._tolka_intervall(payload, "initial_forlust_mm_intervall", (0.0, 2.0))
        avtapp_iv = self._tolka_intervall(payload, "avtappning_mm_h_intervall", (0.0, 0.0))
        k_sigma = _tolka_tal(payload, "k_sigma", STANDARD_K_SIGMA)
        referensregn_mm = _tolka_tal(payload, "referensregn_mm", STANDARD_REFERENSREGN_MM)
        if referensregn_mm <= 0.0:
            raise FlodenWebbFel("Fältet 'referensregn_mm' måste vara > 0.")

        omrade = self._ladda_analys_omrade(omrade_id)
        if isinstance(omrade, LaddatOmrade):
            regn_tid_s = omrade.regn_tid_s
            regn_intens = omrade.regn_intensitet_mm_h
        else:
            regn_tid_s = (0.0, 3600.0)
            regn_intens = (referensregn_mm, 0.0)

        punkter = detektera_bifurkationer(
            omrade.bas_graf,
            osakerhet_m=omrade.osakerhet_z,
            k_sigma=k_sigma,
            referensregn_mm=referensregn_mm,
        )
        try:
            iv = kor_monoton_intervallanalys(
                omrade.bas_graf,
                regn_tid_s,
                regn_intens,
                klimatfaktor_intervall=klimat_iv,
                initial_forlust_mm_intervall=forlust_iv,
                avtappning_mm_h_intervall=avtapp_iv,
                bifurkationer=punkter,
            )
        except DynamikFel as exc:
            raise FlodenWebbFel(f"Intervallanalysfel: {exc}", status_kod=400) from exc

        noder_ut = [
            {
                "nod_id": int(n.nod_id),
                "min_maxdjup_m": float(n.min_maxdjup_m),
                "max_maxdjup_m": float(n.max_maxdjup_m),
                "monoton_garanterad": bool(n.monoton_garanterad),
                "bifurkationspaverkad": bool(n.bifurkationspaverkad),
                "paverkande_bifurkationer": [int(b) for b in n.paverkande_bifurkationer],
            }
            for n in sorted(iv.noder.values(), key=lambda x: x.max_maxdjup_m, reverse=True)
        ]
        antal_monoton = sum(1 for n in iv.noder.values() if n.monoton_garanterad)
        antal_bif = sum(1 for n in iv.noder.values() if n.bifurkationspaverkad)
        storsta_min = max((n.min_maxdjup_m for n in iv.noder.values()), default=0.0)
        storsta_max = max((n.max_maxdjup_m for n in iv.noder.values()), default=0.0)
        return {
            "format": "floden-vatten-intervall/v1",
            "omrade_id": omrade_id,
            "klimatfaktor_intervall": list(iv.klimatfaktor_intervall),
            "initial_forlust_mm_intervall": list(iv.initial_forlust_mm_intervall),
            "avtappning_mm_h_intervall": list(iv.avtappning_mm_h_intervall),
            "antal_korningar": int(iv.antal_korningar),
            "antal_bifurkationsgrenar": int(iv.antal_bifurkationsgrenar),
            "max_volymfel_rel": float(iv.max_volymfel_rel),
            "kortid_s": float(iv.kortid_s),
            "sammanfattning": {
                "antal_noder": len(noder_ut),
                "antal_monoton_garanterad": antal_monoton,
                "antal_bifurkationspaverkade": antal_bif,
                "storsta_min_djup_m": float(storsta_min),
                "storsta_max_djup_m": float(storsta_max),
            },
            "noder": noder_ut[:50],
        }

    def kor_svf_intervall_analys(self, kropp_bytes: bytes) -> dict[str, Any]:
        """Svar för `POST /api/analys/svf-intervall`: monotont SVF-intervall + 1D-horisontprofil."""
        if not kropp_bytes.strip():
            raise FlodenWebbFel("Tom förfrågningskropp; JSON-objekt med 'omrade_id' krävs.")
        try:
            payload = json.loads(kropp_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise FlodenWebbFel(f"Ogiltig JSON i förfrågan: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise FlodenWebbFel("Förfrågningskroppen måste vara ett JSON-objekt.")
        omrade_id = payload.get("omrade_id")
        if not isinstance(omrade_id, str) or not omrade_id.strip():
            raise FlodenWebbFel("Obligatoriskt fält 'omrade_id' saknas eller är tomt.")

        osakerhet_m = _tolka_tal(payload, "osakerhet_byggnad_m", 1.0)
        antal_riktningar_raw = payload.get("antal_riktningar", 16)
        if isinstance(antal_riktningar_raw, bool) or not isinstance(antal_riktningar_raw, int):
            raise FlodenWebbFel("Fältet 'antal_riktningar' ska vara ett heltal (>= 4).")
        if antal_riktningar_raw < 4 or antal_riktningar_raw > 180:
            raise FlodenWebbFel("Fältet 'antal_riktningar' måste vara mellan 4 och 180.")
        troskel_raw = payload.get("svf_troskel")
        svf_troskel: float | None = None
        if troskel_raw is not None:
            svf_troskel = _tolka_tal(payload, "svf_troskel", 0.5)
            if svf_troskel > 1.0:
                raise FlodenWebbFel("Fältet 'svf_troskel' måste ligga i [0, 1].")

        omrade = self._ladda_analys_omrade(omrade_id)
        mark_ra = np.asarray(omrade.mark_z, dtype=np.float64)
        bygg_mask = np.asarray(omrade.markslag, dtype=np.uint8) == 2
        giltig_mark = np.isfinite(mark_ra) & (~bygg_mask)
        bas_z = float(np.median(mark_ra[giltig_mark])) if np.any(giltig_mark) else 0.0
        mark_ren = np.where(np.isfinite(mark_ra) & (~bygg_mask), mark_ra, bas_z)
        bygg_hojd = np.where(
            bygg_mask & np.isfinite(mark_ra),
            np.maximum(mark_ra - bas_z, 0.0),
            0.0,
        )
        if np.any(bygg_mask) and float(np.max(bygg_hojd)) <= 0.0:
            bygg_hojd = np.where(bygg_mask, 10.0, 0.0)

        cell_m = float(omrade.geo.pixelstorlek_x)
        try:
            svf_iv = kor_monoton_svf_intervall(
                mark_ren,
                bygg_hojd,
                osakerhet_m,
                cell_m=cell_m,
                x_min_m=float(omrade.geo.x_min),
                y_max_m=float(omrade.geo.y_max),
                antal_riktningar=antal_riktningar_raw,
                svf_troskel=svf_troskel,
            )
            mitt_rad = (mark_ren + bygg_hojd)[mark_ren.shape[0] // 2, :]
            horisont_ost = horisont_1d_konvext_holje(mitt_rad, cell_m, riktning=1)
        except HorisontFel as exc:
            raise FlodenWebbFel(f"Dagsljusintervallfel: {exc}", status_kod=400) from exc

        mark_celler = ~bygg_mask
        svf_min_medel = (
            float(np.mean(svf_iv.svf_min[mark_celler])) if np.any(mark_celler) else 0.0
        )
        svf_max_medel = (
            float(np.mean(svf_iv.svf_max[mark_celler])) if np.any(mark_celler) else 0.0
        )
        return {
            "format": "floden-dagsljus-svf-intervall/v1",
            "omrade_id": omrade_id,
            "osakerhet_byggnad_m": float(osakerhet_m),
            "antal_riktningar": int(antal_riktningar_raw),
            "svf_min_medel": svf_min_medel,
            "svf_max_medel": svf_max_medel,
            "bredd_max": float(svf_iv.bredd_max),
            "antal_osakra_celler": int(svf_iv.antal_osakra_celler),
            "mittlinje_max_horisont_rad": float(np.max(horisont_ost))
            if horisont_ost.size > 0
            else 0.0,
        }

    def kor_ekologi_analys(self, kropp_bytes: bytes) -> dict[str, Any]:
        """Svar för `POST /api/analys/ekologi`: ECA och PC före/efter (skiss), flaskhalsar (K45)."""
        from ekologi import STANDARD_ARTPROFILER, alla_natverk, korbara_natverk
        from webb.ekologi_analys import ekologi_svar

        if not kropp_bytes.strip():
            raise FlodenWebbFel("Tom förfrågningskropp; JSON-objekt med 'omrade_id' krävs.")
        try:
            payload = json.loads(kropp_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise FlodenWebbFel(f"Ogiltig JSON i förfrågan: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise FlodenWebbFel("Förfrågningskroppen måste vara ett JSON-objekt.")
        omrade_id = payload.get("omrade_id")
        if not isinstance(omrade_id, str) or not omrade_id.strip():
            raise FlodenWebbFel("Obligatoriskt fält 'omrade_id' saknas eller är tomt.")
        if not self.projekt.ar_projekt(omrade_id):
            raise FlodenWebbFel(f"Okänt projekt '{omrade_id}'.", status_kod=404)
        natverk = payload.get("natverk")
        artprofil = payload.get("artprofil")
        if natverk is not None and artprofil is not None:
            raise FlodenWebbFel("Ange bara en av fälten 'natverk' och 'artprofil'.")
        over: dict[str, Any] = {}
        extra_varning: list[str] = []
        if natverk is not None:
            alla = alla_natverk()
            if not isinstance(natverk, str) or natverk not in alla:
                raise FlodenWebbFel(
                    "Fältet 'natverk' ska vara ett av: "
                    f"{', '.join(sorted(korbara_natverk('nmd')))}."
                )
            nv = alla[natverk]
            if not nv.korbar or nv.omfattning != "nmd":
                orsak = nv.korbar_orsak or "nätverket körs bara lokalt (stadens biotopdatabas)"
                raise FlodenWebbFel(f"Nätverket {natverk} går inte att köra på servern: {orsak}.")
            over = {"natverk": natverk, "artprofil": None}
        elif artprofil is not None:
            if not isinstance(artprofil, str) or artprofil not in STANDARD_ARTPROFILER:
                raise FlodenWebbFel(
                    f"Fältet 'artprofil' ska vara en av: {', '.join(sorted(STANDARD_ARTPROFILER))}."
                )
            over = {"artprofil": artprofil, "natverk": None}
            extra_varning.append(
                "Artprofiler är föråldrade (skiss utan källor); välj ett habitatnätverk "
                "(fältet 'natverk')."
            )
        scenario = payload.get("scenario")
        if scenario is not None and (not isinstance(scenario, str) or not scenario.strip()):
            raise FlodenWebbFel("Fältet 'scenario' ska vara ett sparat scenarios id (text).")

        max_flackar = _tolka_heltal(payload, "max_flackar", 40, 2, 200)
        antal_flaskhalsar = _tolka_heltal(payload, "antal_flaskhalsar", 5, 1, 20)
        cell_m = _tolka_tal(payload, "cellstorlek_m", 4.0)
        if not 0.25 <= cell_m <= 100.0:
            raise FlodenWebbFel("Fältet 'cellstorlek_m' ska ligga mellan 0,25 och 100 m.")
        avstand_m = _tolka_tal(payload, "flaskhals_avstand_m", 20.0)
        if avstand_m < cell_m:
            raise FlodenWebbFel("Fältet 'flaskhals_avstand_m' ska vara minst cellstorleken.")
        if "cellstorlek_m" in payload or over.get("artprofil") is not None:
            over["cellstorlek_m"] = cell_m
        over["max_flackar"] = max_flackar
        try:
            k = self.kartverktyg.kor_ekologi(
                omrade_id,
                scenario.strip() if isinstance(scenario, str) else None,
                over,
            )
        except FlodenWebbFel as exc:
            if exc.status_kod == 500 and "LosarFel" in str(exc):
                raise FlodenWebbFel(str(exc), status_kod=400) from exc
            raise
        return ekologi_svar(
            self.fragor,
            korning_id=k["korning_id"],
            analys=k["analys"],
            omrade_id=omrade_id,
            scenario=scenario if isinstance(scenario, str) else None,
            antal_flaskhalsar=antal_flaskhalsar,
            flaskhals_avstand_m=avstand_m,
            max_flackar=max_flackar,
            extra_varningar=extra_varning,
        )

    def ekologi_natverk_lista(self) -> dict[str, Any]:
        """Svar för `GET /api/ekologi/natverk`: habitatnätverken (K47) och kommunernas förval."""
        from ekologi import alla_natverk, kommunforval

        return {
            "format": "floden-ekologi-natverk/v1",
            "metodstatus": "skiss",
            "metodnotering": (
                "Parametrar källbelagda (kommunala modellval), biologisk säkerhet låg."
            ),
            "natverk": [n.katalogpost() for n in alla_natverk().values()],
            "kommuner": [
                {
                    "kommun_id": k.kommun_id,
                    "namn": k.namn,
                    "forval_server": list(k.forval_server),
                    "forval_lokalt": list(k.forval_lokalt),
                    "kvalitetskallor": dict(k.kvalitetskallor),
                    "anmarkning": k.anmarkning,
                }
                for k in kommunforval().values()
            ],
        }

    def kor_buller_optimering_analys(self, kropp_bytes: bytes) -> dict[str, Any]:
        """Svar för `POST /api/analys/buller-skarmar`: CELF-skärmval + skuggzonsdetektor."""
        if not kropp_bytes.strip():
            raise FlodenWebbFel("Tom förfrågningskropp; JSON-objekt krävs.")
        try:
            payload = json.loads(kropp_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise FlodenWebbFel(f"Ogiltig JSON i förfrågan: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise FlodenWebbFel("Förfrågningskroppen måste vara ett JSON-objekt.")

        bas_raw = payload.get("bas_niva_db")
        if not isinstance(bas_raw, list) or not (1 <= len(bas_raw) <= 5000):
            raise FlodenWebbFel("Fältet 'bas_niva_db' ska vara en lista med 1–5000 tal.")
        if any(isinstance(v, bool) or not isinstance(v, int | float) for v in bas_raw):
            raise FlodenWebbFel("Fältet 'bas_niva_db' får bara innehålla tal.")
        bas_arr = np.asarray(bas_raw, dtype=np.float64)
        if not np.all(np.isfinite(bas_arr)):
            raise FlodenWebbFel("Fältet 'bas_niva_db' får inte innehålla NaN eller oändligt.")

        skarmar_raw = payload.get("skarm_reduktion_db")
        if not isinstance(skarmar_raw, Mapping) or len(skarmar_raw) > 200:
            raise FlodenWebbFel(
                "Fältet 'skarm_reduktion_db' ska vara ett objekt (max 200 skärmar)."
            )
        skarm_map: dict[str, NDArray[np.float64]] = {}
        for sid, lista in skarmar_raw.items():
            if not isinstance(sid, str) or not isinstance(lista, list):
                raise FlodenWebbFel("Varje post i 'skarm_reduktion_db' ska vara namn -> tal-lista.")
            if any(isinstance(v, bool) or not isinstance(v, int | float) for v in lista):
                raise FlodenWebbFel(f"Skärm '{sid}' innehåller ogiltiga värden.")
            arr = np.asarray(lista, dtype=np.float64)
            if not np.all(np.isfinite(arr)):
                raise FlodenWebbFel(f"Skärm '{sid}' innehåller NaN eller oändligt.")
            skarm_map[sid] = arr

        k_raw = payload.get("k", 5)
        if isinstance(k_raw, bool) or not isinstance(k_raw, int) or not (1 <= k_raw <= 50):
            raise FlodenWebbFel("Fältet 'k' ska vara ett heltal mellan 1 och 50.")
        riktvarde_db = _tolka_tal(payload, "riktvarde_db", STANDARD_RIKTVARDE_DB)
        mal = str(payload.get("mal", "overskridande_db"))

        try:
            valda = valj_bullerskarmar_k(
                bas_arr,
                skarm_map,
                k=k_raw,
                riktvarde_db=riktvarde_db,
                mal=mal,
            )
            bif_ut: list[dict[str, Any]] | None = None
            avstand_raw = payload.get("avstand_m")
            if avstand_raw is not None:
                if not isinstance(avstand_raw, list) or not (1 <= len(avstand_raw) <= 5000):
                    raise FlodenWebbFel("Fältet 'avstand_m' ska vara en lista med 1–5000 tal.")
                if any(isinstance(v, bool) or not isinstance(v, int | float) for v in avstand_raw):
                    raise FlodenWebbFel("Fältet 'avstand_m' får bara innehålla tal.")
                h_kalla = _tolka_tal(payload, "h_kalla_m", 0.5)
                h_mott = _tolka_tal(payload, "h_mottagare_m", 4.0)
                dc_raw = payload.get("dc_dz_intervall", [-0.05, 0.05])
                if not isinstance(dc_raw, list | tuple) or len(dc_raw) != 2:
                    raise FlodenWebbFel("Fältet 'dc_dz_intervall' ska vara [min, max].")
                if any(isinstance(v, bool) or not isinstance(v, int | float) for v in dc_raw):
                    raise FlodenWebbFel("Fältet 'dc_dz_intervall' ska innehålla två tal.")
                bifs = detektera_akustiska_bifurkationer(
                    np.asarray(avstand_raw, dtype=np.float64),
                    h_kalla,
                    h_mott,
                    dc_dz_intervall=(float(dc_raw[0]), float(dc_raw[1])),
                )
                bif_ut = [
                    {
                        "mottagare_idx": b.mottagare_idx,
                        "avstand_m": b.avstand_m,
                        "kritisk_dc_dz_per_s": b.kritisk_dc_dz_per_s,
                        "skuggzon_bifurkation": b.skuggzon_bifurkation,
                        "monoton_garanterad": b.monoton_garanterad,
                    }
                    for b in bifs
                ]
        except BullerOptimeringFel as exc:
            raise FlodenWebbFel(f"Bulleroptimeringsfel: {exc}", status_kod=400) from exc

        svar: dict[str, Any] = {
            "format": "floden-buller-optimering/v1",
            "riktvarde_db": float(riktvarde_db),
            "mal": mal,
            "valda_skarmar": [
                {
                    "skarm_id": v.skarm_id,
                    "marginal_vinst": float(v.marginal_vinst),
                    "kumulativ_vinst": float(v.kumulativ_vinst),
                    "vinst_ensam": float(v.vinst_ensam),
                    "nya_godkanda": int(v.nya_godkanda),
                }
                for v in valda
            ],
        }
        if bif_ut is not None:
            svar["akustiska_bifurkationer"] = bif_ut
        return svar

    def kor_luft_mkn_analys(self, kropp_bytes: bytes) -> dict[str, Any]:
        """Svar för `POST /api/analys/luft-mkn`: monotont MKN-dygnsintervall (PM10)."""
        if not kropp_bytes.strip():
            raise FlodenWebbFel("Tom förfrågningskropp; JSON-objekt krävs.")
        try:
            payload = json.loads(kropp_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise FlodenWebbFel(f"Ogiltig JSON i förfrågan: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise FlodenWebbFel("Förfrågningskroppen måste vara ett JSON-objekt.")

        serie_raw = payload.get("timvis_lokal_ug_m3")
        if not isinstance(serie_raw, list) or not (24 <= len(serie_raw) <= 8784):
            raise FlodenWebbFel(
                "Fältet 'timvis_lokal_ug_m3' ska vara en lista med 24–8784 timvärden."
            )
        if any(isinstance(v, bool) or not isinstance(v, int | float) for v in serie_raw):
            raise FlodenWebbFel("Fältet 'timvis_lokal_ug_m3' får bara innehålla tal.")
        serie_arr = np.asarray(serie_raw, dtype=np.float64)
        if not np.all(np.isfinite(serie_arr)):
            raise FlodenWebbFel("Fältet 'timvis_lokal_ug_m3' får inte innehålla NaN/oändligt.")

        bakgrund = _tolka_tal(payload, "bakgrund_ug_m3", 0.0)
        em_iv = self._tolka_intervall(payload, "emissionsfaktor_intervall", (0.8, 1.25))
        dep_iv = self._tolka_intervall(payload, "depositionsfaktor_intervall", (0.8, 1.25))
        grans = _tolka_tal(payload, "grans_dygn_ug_m3", 50.0)
        max_dygn_raw = payload.get("max_dygn_ar", 35)
        if isinstance(max_dygn_raw, bool) or not isinstance(max_dygn_raw, int) or max_dygn_raw < 0:
            raise FlodenWebbFel("Fältet 'max_dygn_ar' ska vara ett heltal >= 0.")

        try:
            iv = kor_monoton_mkn_intervall(
                serie_arr,
                bakgrund_ug_m3=bakgrund,
                emissionsfaktor_intervall=em_iv,
                depositionsfaktor_intervall=dep_iv,
                grans_dygn_ug_m3=grans,
                max_dygn_ar=max_dygn_raw,
            )
        except LuftFel as exc:
            raise FlodenWebbFel(f"MKN-intervallfel: {exc}", status_kod=400) from exc

        return {
            "format": "floden-luft-mkn-intervall/v1",
            "min_dygn_over_grans": int(iv.min_dygn_over_grans),
            "max_dygn_over_grans": int(iv.max_dygn_over_grans),
            "min_arsmedel_ug_m3": float(iv.min_arsmedel_ug_m3),
            "max_arsmedel_ug_m3": float(iv.max_arsmedel_ug_m3),
            "min_percentil_90_4_ug_m3": float(iv.min_percentil_90_4_ug_m3),
            "max_percentil_90_4_ug_m3": float(iv.max_percentil_90_4_ug_m3),
            "klarar_mkn_garanterat": bool(iv.klarar_mkn_garanterat),
            "bryter_mkn_garanterat": bool(iv.bryter_mkn_garanterat),
            "osaker_mkn_grans": bool(iv.osaker_mkn_grans),
            "emissionsfaktor_intervall": list(iv.emissionsfaktor_intervall),
            "depositionsfaktor_intervall": list(iv.depositionsfaktor_intervall),
        }

    def kor_simulering(self, kropp_bytes: bytes) -> dict[str, Any]:
        """Svar för `POST /api/kor`: kör före/efter + ensemble och returnerar manifest."""
        t_start = time.perf_counter()
        if not kropp_bytes.strip():
            raise FlodenWebbFel("Tom förfrågningskropp; JSON-objekt med 'omrade_id' krävs.")
        try:
            payload = json.loads(kropp_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FlodenWebbFel(f"Ogiltig JSON i förfrågan: {exc}") from exc

        if not isinstance(payload, Mapping):
            raise FlodenWebbFel("Förfrågningskroppen måste vara ett JSON-objekt.")

        omrade_id = payload.get("omrade_id")
        if not isinstance(omrade_id, str) or not omrade_id.strip():
            raise FlodenWebbFel("Obligatoriskt fält 'omrade_id' saknas eller är tomt.")

        omrade = self.ladda_omrade(omrade_id)
        regn_tid, regn_int, regn_meta = _tolka_regnserie(
            payload, omrade.regn_tid_s, omrade.regn_intensitet_mm_h
        )
        raw_rattning = payload.get("grundmodell_rattning")
        raw_experiment = payload.get("experiment")
        rattning: GrundmodellRattning | None = None
        experiment: ExperimentAtgarder | None = None
        ratt_res: RattningResultat | None = None
        exp_res: ExperimentResultat | None = None

        bas_mark_z = omrade.mark_z
        bas_osak_z = omrade.osakerhet_z
        bas_markslag = omrade.markslag
        bas_graf = omrade.bas_graf
        try:
            if raw_rattning is not None:
                rattning = GrundmodellRattning.fran_dict(raw_rattning)
                if rattning.har_aktiva():
                    ratt_res = tillampa_grundmodell_rattning(
                        bas_mark_z,
                        bas_osak_z,
                        bas_markslag,
                        omrade.geo,
                        rattning,
                    )
                    bas_mark_z = ratt_res.mark_z
                    bas_osak_z = ratt_res.osakerhet_z
                    bas_markslag = ratt_res.markslag
                    bas_graf = bygg_sankgraf(
                        bas_mark_z,
                        bas_osak_z,
                        bas_markslag,
                        omrade.geo,
                        brytlinjer=omrade.brytlinjer,
                        oppningar=omrade.oppningar,
                    )
                    bas_graf = med_takvatten_graf(bas_graf)
            if raw_experiment is not None:
                experiment = ExperimentAtgarder.fran_dict(raw_experiment)
        except (ExperimentFel, SankGrafFel) as exc:
            raise FlodenWebbFel(f"Ogiltig rättning eller experimentåtgärd: {exc}") from exc

        jordart_val, gw_val, tjale_val, init_mm_val = _tolka_forluster(payload)
        forluster_meta: dict[str, Any] | None = None
        if jordart_val is not None:
            try:
                koppl = berakna_effektivt_regn_k5(
                    bas_markslag,
                    regn_tid,
                    regn_int,
                    jordart=jordart_val,
                    grundvatten=gw_val,
                    tjale=tjale_val,
                    initial_mm=init_mm_val,
                    pixelstorlek_m=float(omrade.geo.pixelstorlek_x),
                    mark_z=bas_mark_z,
                    regn_ref=str(regn_meta.get("regn_id", "webb_regn")),
                )
            except ForlustFel as exc:
                raise FlodenWebbFel(f"Ogiltiga markförlustparametrar: {exc}") from exc
            regn_tid = koppl.effektiv_regn_tid_s
            regn_int = koppl.effektiv_regn_intensitet_mm_h
            forluster_meta = koppl.till_dict()
        grann_entreer = _kopiera_entreer_fran_payload(payload)

        try:
            lapp = _bygg_projektlapp_fran_payload(payload, omrade.bas_recept_sha256)
            sektioner = self.hamta_sektioner()

            fore_dyn: DynamikResultat = kor_dynamik_handelsestyrd(
                bas_graf,
                regn_tid,
                regn_int,
            )

            fe_res: ForeEfterResultat | None = None
            if lapp is not None:
                fe_res = kor_fore_efter(
                    bas_graf,
                    bas_mark_z,
                    bas_osak_z,
                    bas_markslag,
                    lapp,
                    regn_tid,
                    regn_int,
                    bas_recept_sha256=omrade.bas_recept_sha256,
                    grann_entreer=grann_entreer,
                    bas_brytlinjer=omrade.brytlinjer,
                    bas_oppningar=omrade.oppningar,
                    sektioner=sektioner if sektioner else None,
                    lokal=True,
                    fore_resultat=fore_dyn,
                )
                efter_dyn = fe_res.efter
                efter_graf = fe_res.efter_graf
                diff_arr = fe_res.diff_max_djup_m
                efter_mark_z = fe_res.tillampning.mark_z
                efter_osak_z = fe_res.tillampning.osakerhet_z
                efter_markslag = fe_res.tillampning.markslag
                efter_brytlinjer = fe_res.tillampning.alla_brytlinjer
                fordrojningsvolym = float(fe_res.fordrojningsvolym_m3)
                grann_forsamring = float(fe_res.grann_forsamring_volym_m3)
                max_okning_granne = float(fe_res.max_okning_djup_granne_m)
                nya_drabbade = [
                    {
                        "entre_id": e.entre_id,
                        "fore_max_djup_m": float(e.fore_max_djup_m),
                        "efter_max_djup_m": float(e.efter_max_djup_m),
                        "diff_max_djup_m": float(e.diff_max_djup_m),
                    }
                    for e in fe_res.nya_drabbade_entreer
                ]
                projekt_entreer = [
                    {
                        "byggnad_id": p.byggnad_id,
                        "entre_rh2000": float(p.entre_rh2000),
                        "max_vattenniva_rh2000": float(p.max_vattenniva_rh2000),
                        "marginal_m": float(p.marginal_m),
                        "overstiger_entre": bool(p.overstiger_entre),
                    }
                    for p in fe_res.projekt_entreer
                ]
            else:
                efter_dyn = fore_dyn
                efter_graf = bas_graf
                diff_arr = np.zeros_like(fore_dyn.max_djup_m, dtype=np.float32)
                efter_mark_z = bas_mark_z
                efter_osak_z = bas_osak_z
                efter_markslag = bas_markslag
                efter_brytlinjer = omrade.brytlinjer
                fordrojningsvolym = 0.0
                grann_forsamring = 0.0
                max_okning_granne = 0.0
                nya_drabbade = []
                projekt_entreer = []

            if experiment is not None and experiment.har_aktiva():
                exp_res = tillampa_experiment(
                    efter_mark_z,
                    efter_osak_z,
                    efter_markslag,
                    omrade.geo,
                    experiment,
                )
                efter_mark_z = exp_res.mark_z
                efter_osak_z = exp_res.osakerhet_z
                efter_markslag = exp_res.markslag
                efter_graf = bygg_sankgraf(
                    efter_mark_z,
                    efter_osak_z,
                    efter_markslag,
                    omrade.geo,
                    brytlinjer=efter_brytlinjer,
                    oppningar=omrade.oppningar,
                )
                efter_graf = med_takvatten_graf(efter_graf)
                efter_dyn = kor_dynamik_handelsestyrd(efter_graf, regn_tid, regn_int)
                diff_arr = (efter_dyn.max_djup_m - fore_dyn.max_djup_m).astype(np.float32)
                cell_area = float(omrade.geo.pixelstorlek_x) * float(omrade.geo.pixelstorlek_y)
                grann_forsamring = float(np.sum(np.maximum(diff_arr, 0.0))) * cell_area
                max_okning_granne = (
                    float(max(0.0, float(np.max(diff_arr)))) if diff_arr.size > 0 else 0.0
                )

            scenarier = _skapa_ensemble_scenarier(regn_tid, regn_int)
            ens_res = kor_ensemble(efter_graf, scenarier, percentiler=(10.0, 50.0, 90.0))
        except (LappFel, ExperimentFel, DynamikFel, SankGrafFel, RenderingFel, ValueError) as exc:
            raise FlodenWebbFel(f"Simuleringsfel: {exc}", status_kod=400) from exc

        kortid_s = time.perf_counter() - t_start
        max_skala = max(
            0.35,
            float(np.max(fore_dyn.max_djup_m)),
            float(np.max(efter_dyn.max_djup_m)),
            float(np.max(ens_res.max_djup_percentiler_m[90.0])),
        )
        max_diff_abs = max(0.15, float(np.max(np.abs(diff_arr))))

        manifest: RunManifest
        if fe_res is not None and exp_res is None and ratt_res is None:
            manifest = fe_res.manifest
        else:
            manifest_params: dict[str, Any] = {
                "omrade_id": omrade.omrade_id,
                "regn_tid_s": list(regn_tid),
                "regn_intensitet_mm_h": list(regn_int),
                "epsg": EPSG_INTERN,
            }
            if ratt_res is not None:
                manifest_params["grundmodell_rattning_sha256"] = ratt_res.rattning_sha256
            if exp_res is not None:
                manifest_params["experiment_sha256"] = exp_res.experiment_sha256
            manifest = skapa_manifest(
                modul="webb",
                indata=[
                    FilPost(
                        roll="bas_recept",
                        fil=f"{omrade.omrade_id}/indata/recept.json",
                        sha256=omrade.bas_recept_sha256,
                    )
                ],
                parametrar=manifest_params,
                utdata=[],
                matvarden={
                    "kortid_s": float(kortid_s),
                    "fore_volymfel_rel": float(fore_dyn.volymfel_rel),
                    "efter_volymfel_rel": float(efter_dyn.volymfel_rel),
                },
            )

        rattning_payload: dict[str, Any] | None = None
        if ratt_res is not None and rattning is not None:
            rattning_payload = {
                "rattning_id": rattning.rattning_id,
                "antal_aktiva_rattningar": int(ratt_res.antal_aktiva_rattningar),
                "antal_andrade_celler": int(ratt_res.antal_andrade_celler),
                "rattning_sha256": ratt_res.rattning_sha256,
            }

        experiment_payload: dict[str, Any] | None = None
        if exp_res is not None and experiment is not None:
            experiment_payload = {
                "experiment_id": experiment.experiment_id,
                "antal_aktiva_atgarder": int(exp_res.antal_aktiva_atgarder),
                "antal_andrade_celler": int(exp_res.antal_andrade_celler),
                "schakt_volym_m3": float(exp_res.schakt_volym_m3),
                "fyll_volym_m3": float(exp_res.fyll_volym_m3),
                "experiment_sha256": exp_res.experiment_sha256,
            }

        geo = omrade.geo
        bbox_3011 = [float(geo.x_min), float(geo.y_min), float(geo.x_max), float(geo.y_max)]
        bbox_wgs84 = list(bbox_3011_till_wgs84(geo.x_min, geo.y_min, geo.x_max, geo.y_max))

        return {
            "omrade_id": omrade.omrade_id,
            "lapp_id": lapp.lapp_id if lapp is not None else None,
            "har_lapp": lapp is not None or exp_res is not None,
            "epsg": EPSG_INTERN,
            "hojdsystem": HOJDSYSTEM,
            "bbox_3011": bbox_3011,
            "bbox_wgs84": bbox_wgs84,
            "regn": {
                "tid_s": list(regn_tid),
                "intensitet_mm_h": list(regn_int),
                **regn_meta,
            },
            "fore": {
                **rendera_djup_png_base64(fore_dyn.max_djup_m, max_skala_m=max_skala),
                **_raster_statistik(fore_dyn.max_djup_m, geo),
                "in_m3": float(fore_dyn.in_m3),
                "lagrat_m3": float(fore_dyn.lagrat_m3),
                "ut_m3": float(fore_dyn.ut_m3),
                "volymfel_rel": float(fore_dyn.volymfel_rel),
            },
            "efter": {
                **rendera_djup_png_base64(efter_dyn.max_djup_m, max_skala_m=max_skala),
                **_raster_statistik(efter_dyn.max_djup_m, geo),
                "in_m3": float(efter_dyn.in_m3),
                "lagrat_m3": float(efter_dyn.lagrat_m3),
                "ut_m3": float(efter_dyn.ut_m3),
                "volymfel_rel": float(efter_dyn.volymfel_rel),
            },
            "differens": {
                **rendera_diff_png_base64(diff_arr, max_abs_m=max_diff_abs),
                "max_okning_djup_granne_m": max_okning_granne,
            },
            "efter_hillshade": rendera_hillshade_png_base64(
                efter_mark_z,
                efter_markslag,
                pixelstorlek_m=geo.pixelstorlek_x,
            ),
            "efter_lager": {
                "sankor": sankgraf_noder_till_geojson(efter_graf),
                "flodesvagar": sankgraf_flodesvagar_till_geojson(efter_graf),
                "brytlinjer": brytlinjer_till_geojson(efter_brytlinjer),
                "byggnader": byggnader_till_geojson(efter_markslag, geo),
            },
            "osakerhet": _ensemble_payload(ens_res, geo, max_skala_m=max_skala),
            "fordrojningsvolym_m3": fordrojningsvolym,
            "grann_forsamring_volym_m3": grann_forsamring,
            "max_okning_djup_granne_m": max_okning_granne,
            "nya_drabbade_entreer": nya_drabbade,
            "projekt_entreer": projekt_entreer,
            "forluster": forluster_meta,
            "grundmodell_rattning": rattning_payload,
            "experiment": experiment_payload,
            "kortid_s": float(kortid_s),
            "manifest": manifest.till_dict(),
            "kallor": list(omrade.kallor),
        }

    def lista_regn(self, *, region: str | None = None) -> dict[str, Any]:
        """Svar för `GET /api/regn`: listar svenska och stockholmska regnförval (K4/v1)."""
        try:
            forval = lista_regnforval(region=region)
        except RegnFel as exc:
            raise FlodenWebbFel(str(exc), status_kod=400) from exc
        return {
            "kontrakt": KONTRAKT_K4,
            "standard_aterkomsttider_ar": list(STANDARD_ATERKOMSTTIDER_AR),
            "standard_klimatfaktorer": list(STANDARD_KLIMATFAKTORER),
            "forval": [item.till_dict() for item in forval],
        }

    def kor_kumulativ_risk(self, kropp_bytes: bytes) -> dict[str, Any]:
        """Svar för `POST /api/risk`: beräknar kumulativ översvämningsrisk upp till T_max."""
        t_start = time.perf_counter()
        if not kropp_bytes.strip():
            raise FlodenWebbFel("Tom förfrågningskropp; JSON-objekt med 'omrade_id' krävs.")
        try:
            payload = json.loads(kropp_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FlodenWebbFel(f"Ogiltig JSON i förfrågan: {exc}") from exc

        if not isinstance(payload, Mapping):
            raise FlodenWebbFel("Förfrågningskroppen måste vara ett JSON-objekt.")

        omrade_id = payload.get("omrade_id")
        if not isinstance(omrade_id, str) or not omrade_id.strip():
            raise FlodenWebbFel("Obligatoriskt fält 'omrade_id' saknas eller är tomt.")

        if "gransdjup_m" not in payload or payload["gransdjup_m"] is None:
            raise FlodenWebbFel("Obligatoriskt fält 'gransdjup_m' saknas.")

        omrade = self.ladda_omrade(omrade_id)
        try:
            gransdjup_m = float(payload["gransdjup_m"])
            max_aterkomsttid_ar = float(payload.get("max_aterkomsttid_ar", 100.0))
            klimatfaktor = float(payload.get("klimatfaktor", 1.24))
            tidshorisont_ar = float(payload.get("tidshorisont_ar", 50.0))
            varaktighet_min = float(payload.get("varaktighet_min", STANDARD_VARAKTIGHET_MIN))
            regntyp = str(payload.get("regntyp", "cds")).strip().lower()
            jordart_val, gw_val, tjale_val, init_mm_val = _tolka_forluster(payload)
            raw_tider = payload.get("aterkomsttider_ar")
            aterkomsttider: list[float] | None = None
            if raw_tider is not None:
                if not isinstance(raw_tider, list):
                    raise FlodenWebbFel("'aterkomsttider_ar' måste vara en lista av tal.")
                aterkomsttider = [float(x) for x in raw_tider]
        except (TypeError, ValueError) as exc:
            raise FlodenWebbFel(f"Ogiltig riskparameter: {exc}") from exc

        try:
            risk = berakna_kumulativ_oversvamningsrisk(
                omrade.bas_graf,
                gransdjup_m=gransdjup_m,
                max_aterkomsttid_ar=max_aterkomsttid_ar,
                aterkomsttider_ar=aterkomsttider,
                klimatfaktor=klimatfaktor,
                varaktighet_min=varaktighet_min,
                regntyp=regntyp,
                tidshorisont_ar=tidshorisont_ar,
                infiltration_jordart=jordart_val,
                grundvatten=gw_val,
                tjale=tjale_val,
                infiltration_initial_mm=init_mm_val,
                indata=[
                    FilPost(
                        roll="bas_recept",
                        fil=f"{omrade.omrade_id}/indata/recept.json",
                        sha256=omrade.bas_recept_sha256,
                    )
                ],
            )
        except (DynamikFel, RegnFel, ForlustFel, ValueError) as exc:
            raise FlodenWebbFel(f"Riskberäkningsfel: {exc}", status_kod=400) from exc

        kortid_s = time.perf_counter() - t_start
        geo = omrade.geo
        bbox_3011 = [float(geo.x_min), float(geo.y_min), float(geo.x_max), float(geo.y_max)]
        bbox_wgs84 = list(bbox_3011_till_wgs84(geo.x_min, geo.y_min, geo.x_max, geo.y_max))
        max_skala = max(0.35, float(np.max(risk.max_djup_vid_tmax_m)))
        min_t_json = (
            float(risk.min_kritisk_aterkomsttid_ar)
            if math.isfinite(risk.min_kritisk_aterkomsttid_ar)
            else None
        )

        return {
            "omrade_id": omrade.omrade_id,
            "epsg": EPSG_INTERN,
            "hojdsystem": HOJDSYSTEM,
            "bbox_3011": bbox_3011,
            "bbox_wgs84": bbox_wgs84,
            "gransdjup_m": float(risk.gransdjup_m),
            "max_aterkomsttid_ar": float(risk.max_aterkomsttid_ar),
            "aterkomsttider_ar": [float(t) for t in risk.aterkomsttider_ar],
            "klimatfaktor": float(risk.klimatfaktor),
            "tidshorisont_ar": float(risk.tidshorisont_ar),
            "varaktighet_min": float(risk.varaktighet_min),
            "regntyp": risk.regntyp,
            "statistikkalla": risk.statistikkalla,
            "kumulativ_risk_karta": rendera_risk_png_base64(risk.kumulativ_sannolikhet),
            "max_djup_vid_tmax": {
                **rendera_djup_png_base64(risk.max_djup_vid_tmax_m, max_skala_m=max_skala),
                **_raster_statistik(risk.max_djup_vid_tmax_m, geo),
            },
            "sammanfattning": {
                "area_over_grans_m2": float(risk.area_over_grans_m2),
                "andel_over_grans": float(risk.andel_over_grans),
                "max_arlig_sannolikhet": float(risk.max_arlig_sannolikhet),
                "max_kumulativ_sannolikhet": float(risk.max_kumulativ_sannolikhet),
                "min_kritisk_aterkomsttid_ar": min_t_json,
                "max_forvantat_arligt_djup_m": float(risk.max_forvantat_arligt_djup_m),
                "max_volymfel_rel": float(risk.max_volymfel_rel),
                "antal_aterkomsttider": len(risk.steg),
            },
            "steg": [
                {
                    "aterkomsttid_ar": float(s.aterkomsttid_ar),
                    "total_djup_mm": float(s.regn.total_djup_mm),
                    "max_djup_m": float(s.max_djup_m),
                    "area_over_grans_m2": float(s.area_over_grans_m2),
                }
                for s in risk.steg
            ],
            "kortid_s": float(kortid_s),
            "manifest": risk.manifest.till_dict(),
            "kallor": list(omrade.kallor),
        }

    def generera_webb_rapport(self, kropp_bytes: bytes) -> dict[str, Any]:
        """Svar för `POST /api/rapport`: kör simulering och exporterar verifierad T08-rapport."""
        t_start = time.perf_counter()
        if not kropp_bytes.strip():
            raise FlodenWebbFel("Tom förfrågningskropp; JSON-objekt med 'omrade_id' krävs.")
        try:
            payload = json.loads(kropp_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FlodenWebbFel(f"Ogiltig JSON i förfrågan: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise FlodenWebbFel("Förfrågningskroppen måste vara ett JSON-objekt.")

        omrade_id = payload.get("omrade_id")
        if not isinstance(omrade_id, str) or not omrade_id.strip():
            raise FlodenWebbFel("Obligatoriskt fält 'omrade_id' saknas eller är tomt.")

        alias_map: dict[str, str] = {
            "skyfall_oversikt": "skyfallsutredning",
            "detaljplan_fore_efter": "planbeskrivning",
            "fastighetsagare": "alternativjamforelse",
            "raddningstjanst": "ruttanalys",
            "klimatanpassning": "grundvatten_vattenbalans",
        }
        raw_typ = str(payload.get("rapporttyp", "skyfall_oversikt")).strip().lower()
        kanonisk_typ = alias_map.get(raw_typ, raw_typ)
        if kanonisk_typ not in GILTIGA_RAPPORTTYPER:
            raise FlodenWebbFel(f"Ogiltig rapporttyp '{raw_typ}'.")

        fmt = str(payload.get("format", "html")).strip().lower()
        mime_map = {
            "html": "text/html; charset=utf-8",
            "md": "text/markdown; charset=utf-8",
            "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        }
        if fmt not in mime_map:
            raise FlodenWebbFel(f"Ogiltigt exportformat '{fmt}'. Giltiga: html, md, docx.")

        omrade = self.ladda_omrade(omrade_id)
        regn_tid, regn_int, regn_meta = _tolka_regnserie(
            payload, omrade.regn_tid_s, omrade.regn_intensitet_mm_h
        )
        jordart_val, gw_val, tjale_val, init_mm_val = _tolka_forluster(payload)
        if jordart_val is not None:
            try:
                koppl = berakna_effektivt_regn_k5(
                    omrade.markslag,
                    regn_tid,
                    regn_int,
                    jordart=jordart_val,
                    grundvatten=gw_val,
                    tjale=tjale_val,
                    initial_mm=init_mm_val,
                    pixelstorlek_m=float(omrade.geo.pixelstorlek_x),
                    mark_z=omrade.mark_z,
                    regn_ref=str(regn_meta.get("regn_id", "webb_regn")),
                )
            except ForlustFel as exc:
                raise FlodenWebbFel(f"Ogiltiga markförlustparametrar: {exc}") from exc
            regn_tid = koppl.effektiv_regn_tid_s
            regn_int = koppl.effektiv_regn_intensitet_mm_h

        dyn = kor_dynamik_handelsestyrd(omrade.bas_graf, regn_tid, regn_int)
        stats = _raster_statistik(dyn.max_djup_m, omrade.geo)
        titel = str(payload.get("titel") or f"Skyfallsrapport – {omrade.fall.namn}")

        manifest = skapa_manifest(
            modul="webb_rapport",
            indata=[
                FilPost(
                    roll="bas_recept",
                    fil=f"{omrade.omrade_id}/indata/recept.json",
                    sha256=omrade.bas_recept_sha256,
                )
            ],
            parametrar={
                "omrade_id": omrade.omrade_id,
                "rapporttyp": kanonisk_typ,
                "format": fmt,
                "epsg": EPSG_INTERN,
            },
            utdata=[],
            matvarden={
                "max_djup_m": round(stats["max_djup_m"], 4),
                "oversvammad_area_m2": round(stats["oversvammad_area_m2"], 2),
                "volymfel_rel": float(dyn.volymfel_rel),
            },
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_rot = Path(tmp_dir)
            karta_fil = tmp_rot / "max_djup.png"
            kart_titel = f"Maxdjup - {omrade.fall.namn}".replace("\u2013", "-").replace(
                "\u2014", "-"
            )
            rendera_djupkarta_png(
                dyn.max_djup_m,
                karta_fil,
                titel=kart_titel,
                kalla=" / ".join(omrade.kallor),
            )
            underlag = RapportUnderlag(
                rapport_typ=kanonisk_typ,
                titel=titel,
                omrade=omrade.fall.namn,
                manifest=manifest,
                metod=(
                    "Deterministisk händelsestyrd sänkgrafmodell (T02/T03) i SWEREF 99 18 00 "
                    "(EPSG:3011) och RH 2000 med automatisk talkontroll (T08)."
                ),
                osakerhet_text=(
                    "Osäkerhet omfattar markmodellens höjdosäkerhet samt regnets "
                    "intensitets- och infiltrationsantaganden."
                ),
                kalla_angivelser=tuple(omrade.kallor),
                tal={
                    "max_djup_m": round(stats["max_djup_m"], 4),
                    "medel_djup_m": round(stats["medel_djup_m"], 4),
                    "oversvammad_area_m2": round(stats["oversvammad_area_m2"], 2),
                    "in_m3": round(float(dyn.in_m3), 3),
                    "lagrat_m3": round(float(dyn.lagrat_m3), 3),
                    "ut_m3": round(float(dyn.ut_m3), 3),
                },
                tabeller=[
                    TabellData(
                        titel="Sammanfattning vattenbalans och djup",
                        kolumner=["Storhet", "Värde", "Enhet"],
                        rader=[
                            ["Maxdjup", round(stats["max_djup_m"], 4), "m"],
                            ["Översvämmad yta", round(stats["oversvammad_area_m2"], 2), "m²"],
                            ["Inkommande volym", round(float(dyn.in_m3), 3), "m³"],
                            ["Lagrad volym", round(float(dyn.lagrat_m3), 3), "m³"],
                            ["Utflöde", round(float(dyn.ut_m3), 3), "m³"],
                        ],
                    )
                ],
                kartor=[
                    KartReferens(
                        kart_id="max_djup",
                        titel=f"Maximalt vattendjup – {omrade.fall.namn}",
                        fil="max_djup.png",
                        kalla=" / ".join(omrade.kallor),
                    )
                ],
            )
            try:
                rapp_res = generera_rapport(
                    underlag,
                    tmp_rot / "ut",
                    kart_rot=tmp_rot,
                    format_lista=(fmt,),
                )
            except (RapportUnderlagFel, RapportKontrollFel, RapportExportFel) as exc:
                raise FlodenWebbFel(f"Rapportgenerering/talkontroll stoppad: {exc}") from exc

            fil_sokvag = rapp_res.filer[fmt]
            data_bytes = fil_sokvag.read_bytes()
            if fmt == "html" and karta_fil.is_file():
                karta_b64 = base64.b64encode(karta_fil.read_bytes()).decode("ascii")
                html_text = data_bytes.decode("utf-8").replace(
                    'src="max_djup.png"',
                    f'src="data:image/png;base64,{karta_b64}"',
                )
                data_bytes = html_text.encode("utf-8")

        kortid_s = time.perf_counter() - t_start
        return {
            "omrade_id": omrade.omrade_id,
            "rapporttyp": kanonisk_typ,
            "format": fmt,
            "filnamn": f"floden_{omrade.fall.id.lower()}_{kanonisk_typ}.{fmt}",
            "mime_typ": mime_map[fmt],
            "innehall_base64": base64.b64encode(data_bytes).decode("ascii"),
            "storlek_bytes": len(data_bytes),
            "kontroll_godkand": bool(rapp_res.kontroll.godkand),
            "antal_kontrollerade_tal": len(rapp_res.kontroll.granskade_tal),
            "varningar": list(rapp_res.kontroll.avvikelser),
            "kortid_s": float(kortid_s),
            "manifest": manifest.till_dict(),
            "kallor": list(omrade.kallor),
        }

    def kor_rutt_analys(self, kropp_bytes: bytes) -> dict[str, Any]:
        """Svar för `POST /api/rutt`: kumulativ framkomlighetsrisk för räddningstjänst (T07)."""
        t_start = time.perf_counter()
        if not kropp_bytes.strip():
            raise FlodenWebbFel("Tom förfrågningskropp; JSON-objekt med 'omrade_id' krävs.")
        try:
            payload = json.loads(kropp_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FlodenWebbFel(f"Ogiltig JSON i förfrågan: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise FlodenWebbFel("Förfrågningskroppen måste vara ett JSON-objekt.")

        omrade_id = payload.get("omrade_id")
        if not isinstance(omrade_id, str) or not omrade_id.strip():
            raise FlodenWebbFel("Obligatoriskt fält 'omrade_id' saknas eller är tomt.")

        tillat_test = bool(payload.get("tillat_testvarden", False))
        if not tillat_test:
            raise FlodenWebbFel(
                "Skarpa räddningstjänstgränser (K7) är inte fastställda för detta testområde. "
                "Bekräfta 'tillat_testvarden: true' för att köra med testvärden."
            )
        if "gransdjup_m" not in payload or payload["gransdjup_m"] is None:
            raise FlodenWebbFel("Obligatoriskt fält 'gransdjup_m' saknas.")

        fordon_typ = str(payload.get("fordon_typ", "slacksbil")).strip().lower()
        if fordon_typ not in ("slacksbil", "ambulans", "storskalig"):
            raise FlodenWebbFel(
                f"Ogiltig fordon_typ '{fordon_typ}'. Giltiga: slacksbil, ambulans, storskalig."
            )

        try:
            gransdjup_m = float(payload["gransdjup_m"])
            max_aterkomsttid_ar = float(payload.get("max_aterkomsttid_ar", 100.0))
            klimatfaktor = float(payload.get("klimatfaktor", 1.24))
            tidshorisont_ar = float(payload.get("tidshorisont_ar", 50.0))
        except (TypeError, ValueError) as exc:
            raise FlodenWebbFel(f"Ogiltig ruttparameter: {exc}") from exc
        if not math.isfinite(gransdjup_m) or gransdjup_m <= 0.0:
            raise FlodenWebbFel(f"Ogiltigt gransdjup_m: {gransdjup_m} (måste vara > 0).")
        antal_insatser_raw = payload.get("antal_insatser", 0)
        if isinstance(antal_insatser_raw, bool) or not isinstance(antal_insatser_raw, int):
            raise FlodenWebbFel("Fältet 'antal_insatser' ska vara ett heltal (>= 0).")
        if antal_insatser_raw < 0 or antal_insatser_raw > 50:
            raise FlodenWebbFel("Fältet 'antal_insatser' måste vara mellan 0 och 50.")

        omrade = self.ladda_omrade(omrade_id)
        geo = omrade.geo
        # Bygg ett deterministiskt syntetiskt gatunät (3 horisontella + 3 vertikala stråk)
        # över områdets bbox så ruttanalys kan utvärderas även på syntetiska K1-områden.
        xs = [geo.x_min + (geo.x_max - geo.x_min) * f for f in (0.15, 0.50, 0.85)]
        ys = [geo.y_min + (geo.y_max - geo.y_min) * f for f in (0.20, 0.50, 0.80)]
        noder: list[VagNod] = []
        hojd_m, bredd_m = omrade.mark_z.shape
        for yi, y_val in enumerate(ys):
            for xi, x_val in enumerate(xs):
                kol = max(
                    0,
                    min(bredd_m - 1, int(math.floor((x_val - geo.x_min) / geo.pixelstorlek_x))),
                )
                rad = max(
                    0,
                    min(hojd_m - 1, int(math.floor((geo.y_max - y_val) / geo.pixelstorlek_y))),
                )
                z_val = float(omrade.mark_z[rad, kol])
                if not math.isfinite(z_val):
                    z_val = 0.0
                noder.append(
                    VagNod(
                        nod_id=f"n_{xi}_{yi}",
                        e_m=float(x_val),
                        n_m=float(y_val),
                        z_rh2000=z_val,
                    )
                )
        kanter: list[VagKant] = []
        for yi in range(3):
            for xi in range(2):
                a = f"n_{xi}_{yi}"
                b = f"n_{xi + 1}_{yi}"
                langd = float(xs[xi + 1] - xs[xi])
                kanter.append(
                    VagKant(
                        kant_id=f"gata_h_{xi}_{yi}",
                        fran_nod=a,
                        till_nod=b,
                        langd_m=langd,
                        normal_hastighet_km_h=30.0,
                        typ="mark",
                    )
                )
        for xi in range(3):
            for yi in range(2):
                a = f"n_{xi}_{yi}"
                b = f"n_{xi}_{yi + 1}"
                langd = float(ys[yi + 1] - ys[yi])
                kanter.append(
                    VagKant(
                        kant_id=f"gata_v_{xi}_{yi}",
                        fran_nod=a,
                        till_nod=b,
                        langd_m=langd,
                        normal_hastighet_km_h=30.0,
                        typ="mark",
                    )
                )
        vagnat = VagNat(
            noder={n.nod_id: n for n in noder},
            kanter={k.kant_id: k for k in kanter},
        )

        try:
            risk = berakna_kumulativ_oversvamningsrisk(
                omrade.bas_graf,
                gransdjup_m=gransdjup_m,
                max_aterkomsttid_ar=max_aterkomsttid_ar,
                klimatfaktor=klimatfaktor,
                tidshorisont_ar=tidshorisont_ar,
            )
            kant_djup_per_t: dict[float, dict[str, float]] = {}
            for steg in risk.steg:
                djup_arr = np.asarray(steg.dynamik.max_djup_m, dtype=np.float64)
                hojd, bredd = djup_arr.shape
                steg_map: dict[str, float] = {}
                for kant in kanter:
                    (e0, n0), (e1, n1) = vagnat.kant_koordinater(kant.kant_id)
                    ts = np.linspace(0.0, 1.0, 15)
                    es = e0 + ts * (e1 - e0)
                    ns = n0 + ts * (n1 - n0)
                    kols = np.clip(
                        np.floor((es - geo.x_min) / geo.pixelstorlek_x).astype(np.int64),
                        0,
                        bredd - 1,
                    )
                    rads = np.clip(
                        np.floor((geo.y_max - ns) / geo.pixelstorlek_y).astype(np.int64),
                        0,
                        hojd - 1,
                    )
                    steg_map[kant.kant_id] = float(np.max(djup_arr[rads, kols]))
                kant_djup_per_t[float(steg.aterkomsttid_ar)] = steg_map

            fordon_alias = {
                "slacksbil": "tung_raddningsbil",
                "storskalig": "tung_raddningsbil",
                "ambulans": "ambulans",
                "personbil": "personbil",
                "tung_raddningsbil": "tung_raddningsbil",
            }
            k7_fordon_typ = fordon_alias[fordon_typ]
            fordon_spec = {
                namn: FordonParametrar(
                    gransdjup_m=gransdjup_m,
                    hastighet_per_djup=((0.0, 1.0), (gransdjup_m * 0.5, 0.5), (gransdjup_m, 0.0)),
                )
                for namn in ("personbil", "ambulans", "tung_raddningsbil")
            }
            k7 = RuttParametrarK7(
                fordon=fordon_spec,
                entre_gransdjup_m=0.10,
                gangavstand_max_m=50.0,
                gangvag_djup_max_m=0.25,
                gangvag_fara_max_m2_s=0.5,
                insatstidsmal_min={
                    "sjukhus": 10.0,
                    "vardboende": 10.0,
                    "skola": 10.0,
                    "forskola": 10.0,
                },
                ar_testvarde=True,
            )
            vag_risk = berakna_vag_kumulativ_risk(
                vagnat,
                kant_djup_per_t,
                k7,
                k7_fordon_typ,
                max_aterkomsttid_ar=max_aterkomsttid_ar,
                tidshorisont_ar=tidshorisont_ar,
                skarp=False,
                tillat_testvarden=True,
            )
            rekommenderade_insatser: list[dict[str, Any]] | None = None
            if antal_insatser_raw > 0 and kant_djup_per_t:
                varsta_t = max(kant_djup_per_t)
                stationer = {"station_0": "n_0_0"}
                adresser = {
                    f"adress_{nid}": nid for nid in sorted(vagnat.noder) if nid != "n_0_0"
                }
                valda = valj_kritisk_insats_k(
                    vagnat,
                    stationer,
                    adresser,
                    t_s=0.0,
                    kant_djup=kant_djup_per_t[varsta_t],
                    parametrar=k7,
                    fordon_typ=k7_fordon_typ,
                    k=antal_insatser_raw,
                    skarp=False,
                    tillat_testvarden=True,
                )
                rekommenderade_insatser = [
                    {
                        "kant_id": post.kant_id,
                        "marginalvinst": float(post.marginalvinst),
                        "kumulativ_vinst": float(post.kumulativ_vinst),
                        "vinst_ensam": float(post.vinst_ensam),
                    }
                    for post in valda
                ]
        except (DynamikFel, RegnFel, RuttParametrarFel, VagNatFel, ValueError) as exc:
            raise FlodenWebbFel(f"Ruttanalysfel: {exc}", status_kod=400) from exc

        features: list[dict[str, Any]] = []
        kanter_ut: list[dict[str, Any]] = []
        for kid in sorted(vag_risk.kanter):
            kr = vag_risk.kanter[kid]
            krit_json = (
                float(kr.kritisk_aterkomsttid_ar)
                if math.isfinite(kr.kritisk_aterkomsttid_ar)
                else None
            )
            kanter_ut.append(
                {
                    "kant_id": kr.kant_id,
                    "kritisk_aterkomsttid_ar": krit_json,
                    "arlig_overskridande_sannolikhet": float(kr.arlig_overskridande_sannolikhet),
                    "kumulativ_sannolikhet": float(kr.kumulativ_sannolikhet),
                    "max_djup_vid_tmax_m": float(kr.max_djup_vid_tmax_m),
                }
            )
            wgs_coords = [list(koordinat_3011_till_wgs84(e, n)) for e, n in kr.koordinater]
            features.append(
                {
                    "type": "Feature",
                    "id": kr.kant_id,
                    "geometry": {"type": "LineString", "coordinates": wgs_coords},
                    "properties": {
                        "kant_id": kr.kant_id,
                        "kritisk_aterkomsttid_ar": krit_json,
                        "kumulativ_sannolikhet": float(kr.kumulativ_sannolikhet),
                        "max_djup_vid_tmax_m": float(kr.max_djup_vid_tmax_m),
                    },
                }
            )

        kortid_s = time.perf_counter() - t_start
        manifest = skapa_manifest(
            modul="webb_rutt",
            indata=[
                FilPost(
                    roll="bas_recept",
                    fil=f"{omrade.omrade_id}/indata/recept.json",
                    sha256=omrade.bas_recept_sha256,
                )
            ],
            parametrar={
                "omrade_id": omrade.omrade_id,
                "fordon_typ": fordon_typ,
                "gransdjup_m": gransdjup_m,
                "max_aterkomsttid_ar": max_aterkomsttid_ar,
                "klimatfaktor": klimatfaktor,
                "tidshorisont_ar": tidshorisont_ar,
                "ar_testvarden": True,
            },
            utdata=[],
            matvarden={
                "antal_kanter_med_risk": int(vag_risk.antal_kanter_med_risk),
                "max_kumulativ_kant_sannolikhet": float(vag_risk.max_kumulativ_kant_sannolikhet),
            },
            varningar=["Testvärden och syntetiskt gatunät används (ej skarpa K7/NVDB-data)."],
        )

        svar_rutt: dict[str, Any] = {
            "omrade_id": omrade.omrade_id,
            "fordon_typ": fordon_typ,
            "gransdjup_m": float(gransdjup_m),
            "max_aterkomsttid_ar": float(max_aterkomsttid_ar),
            "klimatfaktor": float(klimatfaktor),
            "tidshorisont_ar": float(tidshorisont_ar),
            "testvarden_anvanda": True,
            "varning": (
                "Beräkningen använder användarangivna testvärden och syntetiskt rutnät "
                "(ej fastställda K7-gränser från räddningstjänst eller NVDB)."
            ),
            "antal_kanter_totalt": len(vag_risk.kanter),
            "antal_kanter_med_risk": int(vag_risk.antal_kanter_med_risk),
            "max_kumulativ_kant_sannolikhet": float(vag_risk.max_kumulativ_kant_sannolikhet),
            "kanter": kanter_ut,
            "vagnat_geojson": {"type": "FeatureCollection", "features": features},
            "kortid_s": float(kortid_s),
            "manifest": manifest.till_dict(),
            "kallor": list(omrade.kallor),
        }
        if rekommenderade_insatser is not None:
            svar_rutt["rekommenderade_insatser"] = rekommenderade_insatser
        return svar_rutt

    def projekt_for_start(self, ref: str, kropp: bytes) -> str | None:
        """Projektet en körning av frågan `ref` startas i: `projekt_id` i begäran, annars
        demokvarteret när frågan hör till demon, annars None (inget projekt)."""
        if kropp.strip():
            try:
                payload = json.loads(kropp.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError) as exc:
                raise FlodenWebbFel(f"Ogiltig JSON i förfrågan: {exc}") from exc
            valt = payload.get("projekt_id") if isinstance(payload, Mapping) else None
            if valt is not None:
                if not isinstance(valt, str) or not valt.strip():
                    raise FlodenWebbFel("Fältet 'projekt_id' ska vara en text.")
                return valt
        demo = self.fragor.demo()
        delar = demo.get("delar", {}) if demo.get("finns") else {}
        if ref in delar:
            return PROJEKT_ID_DEMO
        return None

    def _hantera_fragor(
        self, verb: str, sokvag: str, kropp: bytes = b""
    ) -> tuple[int, dict[str, str], bytes]:
        delar = [urllib.parse.unquote(d) for d in sokvag.split("/") if d][2:]
        if verb == "GET" and not delar:
            return _json_svar(200, self.fragor.lista_fragor())
        if verb == "GET" and len(delar) == 1:
            return _json_svar(200, self.fragor.hamta_fraga(delar[0]))
        if verb == "POST" and len(delar) == 2 and delar[1] == "plan":
            return _json_svar(200, self.fragor.planera_fraga(delar[0]))
        if verb == "POST" and len(delar) == 2 and delar[1] == "kor":
            svar = self.fragor.starta_korning(delar[0], self.projekt_for_start(delar[0], kropp))
            return _json_svar(202, svar)
        return _json_svar(404, {"fel": f"Okänd endpoint: {verb} {sokvag}", "status": 404})

    def _hantera_kartverktyg(
        self,
        verb: str,
        sokvag: str,
        kropp: bytes,
        query: Mapping[str, list[str]],
    ) -> tuple[int, dict[str, str], bytes]:
        """`/api/kartverktyg/...`: underlag (WMS, ortofoto) och ritade planförslag."""
        delar = [urllib.parse.unquote(d) for d in sokvag.split("/") if d][2:]
        kv = self.kartverktyg

        def json_kropp() -> dict[str, Any]:
            if not kropp:
                return {}
            try:
                d = json.loads(kropp.decode("utf-8"))
            except ValueError as exc:
                raise FlodenWebbFel("Anropet är inte giltig JSON.", status_kod=400) from exc
            if not isinstance(d, dict):
                raise FlodenWebbFel("Anropet måste vara ett JSON-objekt.", status_kod=400)
            return d

        if delar[:1] == ["wms"]:
            if verb == "POST" and delar == ["wms", "lager"]:
                return _json_svar(200, kv.wms_lager(json_kropp()))
            if verb == "GET" and delar == ["wms", "bild"]:
                data, typ = kv.wms_bild(query)
                return _binart_svar(200, data, typ)
            return _json_svar(404, {"fel": f"Okänd endpoint: {verb} {sokvag}", "status": 404})
        if not delar:
            return _json_svar(404, {"fel": f"Okänd endpoint: {verb} {sokvag}", "status": 404})
        projekt, rest = delar[0], delar[1:]
        if verb == "GET" and not rest:
            return _json_svar(200, kv.projektinfo(projekt))
        if verb == "GET" and rest == ["ortofoto.png"]:
            png, extra = kv.ortofoto_png(projekt)
            status, huvud, ut = _binart_svar(200, png, "image/png")
            return status, {**huvud, **extra}, ut
        if verb == "GET" and len(rest) == 2 and rest[0] == "geometri" and rest[1].endswith(".png"):
            png, extra = kv.geometri_png(projekt, rest[1].removesuffix(".png"))
            status, huvud, ut = _binart_svar(200, png, "image/png")
            return status, {**huvud, **extra}, ut
        if verb == "POST" and rest == ["validera"]:
            return _json_svar(200, kv.validera(projekt, json_kropp()))
        if rest[:1] == ["lage-idag"]:
            # Läget idag (U5a): bara `fore`, inget förslag. Samma roll som övriga körningar (utredare för POST).
            kropp_l = json_kropp() if verb == "POST" else {}
            valda_l = [str(x) for x in kropp_l.get("analyser", [])]
            avt_l = kropp_l.get("avtappning_mm_h")
            if verb == "POST" and rest == ["lage-idag", "plan"]:
                return _json_svar(200, kv.planera_lage_idag(projekt, valda_l, avt_l))
            if verb == "POST" and rest == ["lage-idag", "kor"]:
                return _json_svar(202, kv.kor_lage_idag(projekt, valda_l, avt_l))
            if verb == "GET" and rest == ["lage-idag", "status"]:
                return _json_svar(200, kv.status_lage_idag(projekt))
            return _json_svar(404, {"fel": f"Okänd endpoint: {verb} {sokvag}", "status": 404})
        if verb == "GET" and rest == ["scenarier"]:
            return _json_svar(200, kv.lista_scenarier(projekt))
        if verb == "POST" and rest == ["scenarier"]:
            return _json_svar(200, kv.spara_scenario(projekt, None, json_kropp()))
        if len(rest) >= 2 and rest[0] == "scenarier":
            namn = rest[1]
            tail = rest[2:]
            if verb == "GET" and not tail:
                return _json_svar(200, kv.hamta_scenario(projekt, namn))
            if verb == "POST" and not tail:
                return _json_svar(200, kv.spara_scenario(projekt, namn, json_kropp()))
            if verb == "POST" and tail == ["byt-namn"]:
                nytt_namn = str(json_kropp().get("namn", ""))
                return _json_svar(200, kv.byt_namn(projekt, namn, nytt_namn))
            if verb == "POST" and tail == ["ta-bort"]:
                return _json_svar(200, kv.ta_bort_scenario(projekt, namn))
            kropp_d = json_kropp() if verb == "POST" else {}
            valda = [str(x) for x in kropp_d.get("analyser", [])]
            avt = kropp_d.get("avtappning_mm_h")
            if verb == "POST" and tail == ["plan"]:
                return _json_svar(200, kv.planera(projekt, namn, valda, avt))
            if verb == "POST" and tail == ["kor"]:
                return _json_svar(202, kv.kor(projekt, namn, valda, avt))
            if verb == "GET" and tail == ["status"]:
                return _json_svar(200, kv.status(projekt, namn))
        return _json_svar(404, {"fel": f"Okänd endpoint: {verb} {sokvag}", "status": 404})

    def _hantera_3d(
        self, verb: str, sokvag: str, query: Mapping[str, list[str]]
    ) -> tuple[int, dict[str, str], bytes]:
        """`/api/3d/<projekt>/...`: terräng, byggnader, resultat och fasadpunkter som buffertar."""
        delar = [urllib.parse.unquote(d) for d in sokvag.split("/") if d][2:]
        ej = _json_svar(404, {"fel": f"Okänd endpoint: {verb} {sokvag}", "status": 404})
        if verb != "GET" or not delar:
            return ej
        projekt, rest = delar[0], delar[1:]
        v = self.vy3d

        def heltal(text: str, namn: str) -> int:
            try:
                return int(text)
            except ValueError as exc:
                raise FlodenWebbFel(f"{namn} måste vara ett heltal.", status_kod=400) from exc

        def buffert(data: bytes) -> tuple[int, dict[str, str], bytes]:
            status, huvud, ut = _binart_svar(200, data, "application/octet-stream")
            huvud["Cache-Control"] = "private, max-age=300"
            return status, huvud, ut

        def q(namn: str) -> str | None:
            v = query.get(namn)
            return v[0] if v else None

        if not rest:
            return _json_svar(200, v.info(projekt))
        if rest[0] == "terrang" and len(rest) == 4:
            return buffert(
                v.terrang_ruta(projekt, *(heltal(x, "Rutindex") for x in rest[1:]))
            )
        if rest[0] == "byggnader" and len(rest) == 1:
            return buffert(v.byggnader(projekt, q("korning"), q("scenario")))
        if rest[0] == "mark" and len(rest) == 6:
            lod, tx, ty = (heltal(x, "Rutindex") for x in rest[3:])
            return buffert(v.mark_ruta(projekt, rest[1], rest[2], lod, tx, ty))
        if rest[0] == "fasad" and len(rest) == 3:
            return buffert(v.fasad(projekt, rest[1], rest[2], q("analys") or "vsc", q("jamfor")))
        if rest[0] == "korning" and len(rest) == 2:
            return _json_svar(200, v.korningsinfo(projekt, rest[1]))
        return ej

    def _hantera_korningar(
        self, verb: str, sokvag: str, query: Mapping[str, list[str]] | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        delar = [urllib.parse.unquote(d) for d in sokvag.split("/") if d][2:]
        query = query or {}
        if verb == "GET" and not delar:
            return _json_svar(200, self.fragor.lista_korningar())
        if verb == "GET" and len(delar) == 1:
            return _json_svar(200, self.fragor.korning_status(delar[0]))
        if verb == "GET" and len(delar) == 2 and delar[1] == "resultat":
            return _json_svar(200, self.fragor.resultat(delar[0]))
        if verb == "GET" and len(delar) == 2 and delar[1] == "marksiffror":
            return _json_svar(
                200, self.projekt.marksiffror_for(delar[0], query.get("lager", [""])[0])
            )
        if verb == "GET" and len(delar) == 2 and delar[1] == "avlasning":
            try:
                e = float(query.get("e", [""])[0])
                n = float(query.get("n", [""])[0])
            except ValueError as exc:
                raise FlodenWebbFel("Parametrarna e och n måste vara tal.", status_kod=400) from exc
            if not (math.isfinite(e) and math.isfinite(n)):
                raise FlodenWebbFel("Parametrarna e och n måste vara ändliga.", status_kod=400)
            lager = query.get("lager", [""])[0]
            return _json_svar(200, self.fragor.avlasning(delar[0], lager, e, n))
        if verb == "GET" and len(delar) == 3 and delar[1] == "lager" and delar[2].endswith(".png"):
            png = self.fragor.lager_png(delar[0], delar[2].removesuffix(".png"))
            return _binart_svar(200, png, "image/png")
        if verb == "GET" and len(delar) >= 3 and delar[1] == "fil":
            innehall, typ = self.fragor.fil(delar[0], "/".join(delar[2:]))
            return _binart_svar(200, innehall, typ)
        if verb == "GET" and len(delar) >= 3 and delar[1] == "tabell":
            return _json_svar(200, self.fragor.tabell(delar[0], "/".join(delar[2:])))
        return _json_svar(404, {"fel": f"Okänd endpoint: {verb} {sokvag}", "status": 404})

    def projekt_for_korning(self, korning_id: str) -> str | None:
        """Projektet en körning hör till, eller None (testsvit, äldre körningar).

        Körningens eget `projekt_id` (`korning.json`) gäller först. Demokörningarna som
        `demo.json` pekar ut hör till demokvarteret. Annat finns inte att gå på: K0-manifestet
        (`run.json`) har ingen projektuppgift, och registrets projekt har inga körningar än."""
        k = self.fragor.korning_eller_none(korning_id)
        if k is not None and k.projekt_id:
            return k.projekt_id
        demo = self.fragor.demo()
        if demo.get("finns"):
            for post in demo.get("delar", {}).values():
                if isinstance(post, Mapping) and post.get("korning_id") == korning_id:
                    return PROJEKT_ID_DEMO
        return None

    def korningsunderlag(self, korning_id: str) -> dict[str, Any]:
        """Vad en körning är för slags underlag (K48, additivt).

        `slag` är `forslag` (körd för ett ritat förslag; `scenario` har då `slug` och `namn` när
        kopplingen finns kvar), `lage_idag` (projektets befintliga läge utan förslag, U5a; räknas som projektets egen
        körning), `egen` (annan körning i projektet, till exempel via MCP), `demo`
        (demokörningen som följer med projektet, inget eget förslag) eller `okand`."""
        k = self.fragor.korning_eller_none(korning_id)
        projekt = self.projekt_for_korning(korning_id)
        if k is None or projekt is None:
            return {"slag": "okand", "scenario": None}
        sc = self.kartverktyg.scenarier_per_korning(projekt).get(korning_id)
        if sc is not None:
            return {"slag": "forslag", "scenario": sc}
        if str(k.fraga_id).startswith(LAGE_IDAG_PREFIX):
            return {"slag": "lage_idag", "scenario": None}
        if str(k.fraga_id).startswith("rita-"):
            return {"slag": "forslag", "scenario": None}
        if not k.projekt_id:
            return {"slag": "demo", "scenario": None}
        return {"slag": "egen", "scenario": None}

    def konto_far_se_korning(self, konto_id: str | None, korning_id: str) -> bool:
        """Får kontot läsa körningen? Inloggning av: alltid. På: minst `lasare` i körningens
        projekt (demokvarteret följer `demoprojekt_roll`); körning utan projekt bara för
        superadmin; utan konto aldrig. Samma regel för webben och MCP-verktygen."""
        if not self.konto.aktiv:
            return True
        if konto_id is None:
            return False
        projekt_id = self.projekt_for_korning(korning_id)
        if projekt_id is None:
            return self.konto.ar_superadmin(konto_id)
        return self.konto.har_roll(konto_id, projekt_id, "lasare")

    def korningar_for_konto(self, konto_id: str | None) -> dict[str, Any]:
        """`GET /api/korningar` och MCP `korningar_lista`: bara körningar kontot får se."""
        alla = self.fragor.lista_korningar()
        if not self.konto.aktiv:
            return alla
        return {
            "korningar": [
                k for k in alla["korningar"] if self.konto_far_se_korning(konto_id, k["korning_id"])
            ]
        }

    def projekt_lista_for(self, konto_id: str | None) -> dict[str, Any]:
        """Projekten ett konto har tillgång till (`GET /api/projekt` och MCP `projekt_lista`).

        Inloggning av (lokalt): demokvarteret om det är byggt, som förut. Inloggning på:
        demokvarteret (om `demoprojekt_roll` inte är `av`) och projekten där kontot har en
        effektiv roll (uttrycklig, eller ur organisationsrollen: `Register.effektiv_projektroll`,
        fältet `kalla` säger vilket); inget annat, och ingenting alls utan konto. Projekt ur
        registret har ännu inga beräkningar (`finns` är falskt) tills de kopplas till körningar."""
        demo: list[dict[str, Any]] = self.projekt.lista()["projekt"]
        if not self.konto.aktiv:
            return {"projekt": demo}
        if konto_id is None:
            return {"projekt": []}
        demo_roll = self.konto.demoroll()
        ut: list[dict[str, Any]] = (
            [{**p, "roll": demo_roll, "finns": True} for p in demo] if demo_roll else []
        )
        for p in self.konto.projekt_for_konto(konto_id):
            ut.append(
                {
                    "id": p["id"],
                    "namn": p["namn"],
                    "undertitel": "Projekt",
                    "paahittat": False,
                    "beskrivning": "",
                    "roll": p["roll"],
                    "kalla": p["kalla"],
                    "organisation_synlig": p["organisation_synlig"],
                    "finns": False,
                }
            )
        return {"projekt": ut}

    def _projekt_svar_for_konto(
        self, sokvag: str, konto_id: str
    ) -> tuple[int, list[tuple[str, str]], bytes] | None:
        """`GET /api/projekt` (kontots projekt) och `GET /api/projekt/<id>` för ett projekt ur
        registret. Demokvarteret och allt annat lämnas åt `hantera_forfragan` (None)."""
        delar = [urllib.parse.unquote(d) for d in sokvag.split("/") if d]
        if delar[:2] != ["api", "projekt"] or len(delar) > 3:
            return None
        if len(delar) == 2:
            svar = self.projekt_lista_for(konto_id)
        else:
            lista = self.projekt_lista_for(konto_id)["projekt"]
            rad = next((p for p in lista if p["id"] == delar[2]), None)
            if rad is None or rad.get("finns"):
                return None
            svar = {**rad, "beskrivning": "Projektet har inga beräkningar än."}
        status, headers, kropp = _json_svar(200, svar)
        ut = [(k, v) for k, v in headers.items() if not k.lower().startswith("access-control-")]
        return status, ut, kropp

    def hantera_forfragan(
        self,
        metod: str,
        sokvag: str,
        kropp: bytes = b"",
    ) -> tuple[int, dict[str, str], bytes]:
        """Hanterar en HTTP-förfrågan och returnerar `(status, headers, json_bytes)`."""
        verb = metod.upper().strip()
        if verb == "OPTIONS":
            return _json_svar(200, {"status": "ok"})

        parsed = urllib.parse.urlsplit(sokvag)
        ren_sokvag = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query)

        try:
            if verb == "GET" and ren_sokvag == "/api/halsa":
                return _json_svar(200, self.halsa())

            if verb == "GET" and ren_sokvag == "/api/ekologi/natverk":
                return _json_svar(200, self.ekologi_natverk_lista())

            if verb == "GET" and ren_sokvag == "/api/regn":
                region_param = query.get("region", [None])[0]
                return _json_svar(200, self.lista_regn(region=region_param))

            if verb == "GET" and ren_sokvag == "/api/fragekatalog":
                return _json_svar(200, self.fragekatalog(query))

            if verb == "GET" and ren_sokvag == "/api/moduler":
                return _json_svar(200, lista_moduler(self._demo_moduler()))

            if ren_sokvag == "/api/projekt" or ren_sokvag.startswith("/api/projekt/"):
                delar_p = [urllib.parse.unquote(d) for d in ren_sokvag.split("/") if d][2:]
                if verb == "POST" and len(delar_p) == 2 and delar_p[1] == "rapporter":
                    return _json_svar(202, self.rapportjobb.starta(delar_p[0], kropp))
                if verb != "GET":
                    return _json_svar(405, {"fel": "Metoden är inte tillåten.", "status": 405})
                delar = [urllib.parse.unquote(d) for d in ren_sokvag.split("/") if d][2:]
                if not delar:
                    return _json_svar(200, self.projekt.lista())
                if len(delar) == 1:
                    svar = self.projekt.hamta(delar[0])
                    svar["rapporter"] = self.rapporter_for(delar[0])
                    return _json_svar(200, svar)
                if len(delar) == 2 and delar[1] == "rapporter":
                    return _json_svar(
                        200,
                        {
                            "rapporter": self.rapporter_for(delar[0]),
                            **self.rapportjobb.mojligheter(delar[0]),
                        },
                    )
                if len(delar) == 4 and delar[1] == "rapporter" and delar[2] == "jobb":
                    return _json_svar(200, self.rapportjobb.status(delar[0], delar[3]))
                if len(delar) == 3 and delar[1] == "rapporter":
                    return _json_svar(200, self.rapport_innehall(delar[0], delar[2]))
                if len(delar) == 5 and delar[1] == "rapporter" and delar[3] == "fil":
                    return self.rapportfil(delar[0], delar[2], delar[4])

            if ren_sokvag == "/api/fragor" or ren_sokvag.startswith("/api/fragor/"):
                return self._hantera_fragor(verb, ren_sokvag, kropp)

            if ren_sokvag.startswith("/api/kartverktyg/"):
                return self._hantera_kartverktyg(verb, ren_sokvag, kropp, query)

            if ren_sokvag.startswith("/api/3d/"):
                return self._hantera_3d(verb, ren_sokvag, query)

            if verb == "GET" and ren_sokvag == "/api/demo":
                return _json_svar(200, self.fragor.demo())

            if ren_sokvag == "/api/korningar" or ren_sokvag.startswith("/api/korningar/"):
                return self._hantera_korningar(verb, ren_sokvag, query)

            if verb == "GET" and ren_sokvag == "/api/omraden":
                return _json_svar(200, self.lista_omraden())

            if verb == "GET" and ren_sokvag.startswith("/api/omraden/"):
                delar = [d for d in ren_sokvag.split("/") if d]
                if (
                    len(delar) == 4
                    and delar[0] == "api"
                    and delar[1] == "omraden"
                    and delar[3] == "lager"
                ):
                    omrade_id = urllib.parse.unquote(delar[2])
                    return _json_svar(200, self.hamta_omrade_lager(omrade_id))

            if verb == "POST" and ren_sokvag == "/api/kor":
                return _json_svar(200, self.kor_simulering(kropp))

            if verb == "POST" and ren_sokvag == "/api/risk":
                return _json_svar(200, self.kor_kumulativ_risk(kropp))

            if verb == "POST" and ren_sokvag == "/api/rapport":
                return _json_svar(200, self.generera_webb_rapport(kropp))

            if verb == "POST" and ren_sokvag == "/api/rutt":
                return _json_svar(200, self.kor_rutt_analys(kropp))

            if verb == "POST" and ren_sokvag == "/api/analys/bifurkationer":
                return _json_svar(200, self.kor_bifurkation_analys(kropp))

            if verb == "POST" and ren_sokvag == "/api/analys/intervall":
                return _json_svar(200, self.kor_intervall_analys(kropp))

            if verb == "POST" and ren_sokvag == "/api/analys/svf-intervall":
                return _json_svar(200, self.kor_svf_intervall_analys(kropp))

            if verb == "POST" and ren_sokvag == "/api/analys/ekologi":
                return _json_svar(200, self.kor_ekologi_analys(kropp))

            if verb == "POST" and ren_sokvag == "/api/analys/buller-skarmar":
                return _json_svar(200, self.kor_buller_optimering_analys(kropp))

            if verb == "POST" and ren_sokvag == "/api/analys/luft-mkn":
                return _json_svar(200, self.kor_luft_mkn_analys(kropp))

            if ren_sokvag.startswith(PROV_PREFIX):
                status, svar = prov_svar(verb, ren_sokvag)
                return _json_svar(status, svar)

            return _json_svar(
                404,
                {
                    "fel": f"Okänd endpoint: {verb} {ren_sokvag}",
                    "status": 404,
                },
            )
        except FlodenWebbFel as exc:
            return _json_svar(
                exc.status_kod,
                {
                    "fel": str(exc),
                    "status": exc.status_kod,
                },
            )
        except Exception:  # noqa: BLE001 - returnera tydligt API-fel utan tyst krasch
            _logg.exception("Oväntat fel i webb-API (%s %s)", verb, ren_sokvag)
            return _json_svar(
                500,
                {
                    "fel": "Internt serverfel.",
                    "status": 500,
                },
            )

    def hantera_http(
        self,
        metod: str,
        ra_sokvag: str,
        rubriker: Mapping[str, str],
        las_kropp: Callable[[], bytes],
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        """Hela HTTP-vägen: konto, statiska klientfiler, inloggningskontroll och API.

        Ordningen följer `docs/KONTO.md` avsnitt 8: `/konto/*` först, sedan klientens statiska
        filer (de innehåller ingen data, så att inloggningssidan kan visas), sedan
        `kraver_inloggning` (och `kraver_projektbehorighet` för projekt-endpoints) **före** att
        kroppen läses, och sist API:t. Med inloggning på skickas ingen
        `Access-Control-Allow-Origin`: klient och API nås från samma ursprung. `las_kropp` anropas
        högst en gång och bara när kroppen behövs.
        """
        verb = metod.upper()
        req = Forfragan.fran_ra(verb, ra_sokvag, rubriker)
        if self.konto.ager_sokvag(req.sokvag):
            try:
                kropp_in = las_kropp() if verb in ("POST", "PUT", "PATCH", "DELETE") else b""
            except FlodenWebbFel as exc:
                status, headers, kropp = _json_svar(
                    exc.status_kod, {"fel": str(exc), "status": exc.status_kod}
                )
                return status, list(headers.items()), kropp
            return self.konto.hantera_konto(verb, req.sokvag, req.query, req.headers, kropp_in)
        if verb in ("GET", "HEAD") and not req.sokvag.startswith("/api"):
            statisk = self._statisk(req.sokvag, verb == "HEAD")
            if statisk is not None:
                return statisk
        projekt_krav = _projekt_krav(verb, req.sokvag) if self.konto.aktiv else None
        if projekt_krav is not None:
            kontroll = self.konto.kraver_projektbehorighet(
                req, projekt_krav[0], minst=projekt_krav[1]
            )
        else:
            kontroll = self.konto.kraver_inloggning(req)
        if not kontroll.tillaten:
            assert kontroll.svar is not None
            return kontroll.svar
        # WebGPU-provet (src/webb/prov.py) är bara för superadmin när inloggning är på.
        if (
            self.konto.aktiv
            and req.sokvag.startswith(PROV_PREFIX)
            and (kontroll.inloggad is None or not kontroll.inloggad.superadmin)
        ):
            status, headers, kropp = _json_svar(
                403, {"fel": "Provsidorna är bara för superadmin.", "status": 403}
            )
            return status, list(headers.items()), kropp
        nytt = self._hantera_skapa_projekt(verb, req.sokvag, kontroll, las_kropp)
        if nytt is not None:
            return nytt
        if verb == "GET" and self.konto.aktiv and kontroll.inloggad is not None:
            eget = self._projekt_svar_for_konto(req.sokvag, kontroll.inloggad.konto_id)
            if eget is not None:
                return eget
        try:
            kropp_in = las_kropp() if verb == "POST" else b""
        except FlodenWebbFel as exc:
            status, headers, kropp = _json_svar(
                exc.status_kod, {"fel": str(exc), "status": exc.status_kod}
            )
            return status, list(headers.items()), kropp
        if self.konto.aktiv and (req.sokvag.rstrip("/") + "/").startswith("/api/korningar/"):
            delar = [urllib.parse.unquote(d) for d in req.sokvag.split("/") if d]
            konto_id = kontroll.inloggad.konto_id if kontroll.inloggad is not None else None
            if len(delar) >= 3 and not self.konto_far_se_korning(konto_id, delar[2]):
                # 404 i stället för 403: ett körnings-id får inte gå att pröva sig fram till.
                return _utan_cors(
                    _json_svar(404, {"fel": f"Okänd körning '{delar[2]}'.", "status": 404})
                )
        if (
            self.konto.aktiv
            and verb == "GET"
            and (req.sokvag.rstrip("/") or "/") == "/api/korningar"
        ):
            konto_id = kontroll.inloggad.konto_id if kontroll.inloggad is not None else None
            return _utan_cors(_json_svar(200, self.korningar_for_konto(konto_id)))
        if self.konto.aktiv and verb == "POST":
            start = [urllib.parse.unquote(d) for d in req.sokvag.split("/") if d]
            if len(start) == 4 and start[:2] == ["api", "fragor"] and start[3] == "kor":
                konto_id = kontroll.inloggad.konto_id if kontroll.inloggad is not None else None
                try:
                    projekt_id = self.projekt_for_start(start[2], kropp_in)
                except FlodenWebbFel as exc:
                    return _utan_cors(
                        _json_svar(exc.status_kod, {"fel": str(exc), "status": exc.status_kod})
                    )
                # Utredare i projektet krävs. Utan projekt (en fråga som inte hör till något) får
                # bara superadmin starta; körningen blir då en körning utan projekt.
                ok = konto_id is not None and (
                    self.konto.har_roll(konto_id, projekt_id, "utredare")
                    if projekt_id is not None
                    else self.konto.ar_superadmin(konto_id)
                )
                if not ok:
                    return _utan_cors(
                        _json_svar(
                            403,
                            {
                                "fel": "Saknar behörighet att starta körningar i projektet "
                                "(ange 'projekt_id' där du är utredare).",
                                "status": 403,
                            },
                        )
                    )
        analys_projekt = self._projekt_for_analys(verb, req.sokvag, kropp_in)
        if analys_projekt is not None and self.konto.aktiv:
            if (req.sokvag.rstrip("/") or "/") in BERAKNINGS_SOKVAGAR:
                # Beräkningarna (`/api/kor|risk|rutt`) sparar inget: läsare räcker. Saknad roll
                # ger 404, som för körningar: ett projekt-id får inte gå att pröva sig fram till.
                konto_id = kontroll.inloggad.konto_id if kontroll.inloggad is not None else None
                if konto_id is None or not self.konto.har_roll(konto_id, analys_projekt, "lasare"):
                    return _utan_cors(
                        _json_svar(404, {"fel": f"Okänt område '{analys_projekt}'.", "status": 404})
                    )
            else:
                # Som projektets övriga API: en uttrycklig projektroll krävs, inte bara inloggning.
                kontroll = self.konto.kraver_projektbehorighet(req, analys_projekt, minst="lasare")
                if not kontroll.tillaten:
                    assert kontroll.svar is not None
                    return kontroll.svar
        status, headers, kropp = self.hantera_forfragan(verb, ra_sokvag, kropp_in)
        ut = list(headers.items())
        if self.konto.aktiv:
            ut = [(k, v) for k, v in ut if not k.lower().startswith("access-control-")]
        return status, ut, kropp

    def _hantera_skapa_projekt(
        self, verb: str, sokvag: str, kontroll: Any, las_kropp: Callable[[], bytes]
    ) -> tuple[int, list[tuple[str, str]], bytes] | None:
        """`GET /api/geografier` och `POST /api/projekt` (rollerna avgör vem som får)."""
        ren = sokvag.rstrip("/") or "/"
        traff = (verb == "GET" and ren == "/api/geografier") or (
            verb == "POST" and ren == "/api/projekt"
        )
        if not traff:
            return None
        try:
            if not self.konto.aktiv or kontroll.inloggad is None:
                return _utan_cors(
                    _json_svar(
                        403,
                        {
                            "fel": "Att skapa projekt kräver inloggning (lokalt: floden projekt skapa).",  # noqa: E501
                            "status": 403,
                        },
                    )
                )
            konto_id = kontroll.inloggad.konto_id
            if verb == "GET":
                return _utan_cors(_json_svar(200, self.projekt_skapa.geografier(konto_id)))
            try:
                payload = json.loads(las_kropp().decode("utf-8") or "null")
            except (UnicodeDecodeError, ValueError, RecursionError) as exc:
                raise FlodenWebbFel("Kroppen ska vara JSON.", status_kod=400) from exc
            if not isinstance(payload, Mapping):
                raise FlodenWebbFel("Kroppen ska vara ett JSON-objekt.", status_kod=400)
            return _utan_cors(_json_svar(201, self.projekt_skapa.skapa(konto_id, payload)))
        except FlodenWebbFel as exc:
            svar = {"fel": str(exc), "status": exc.status_kod}
            return _utan_cors(_json_svar(exc.status_kod, svar))

    def _projekt_for_analys(self, verb: str, sokvag: str, kropp: bytes) -> str | None:
        """Projektet en analys (`POST /api/analys/...`) gäller, om `omrade_id` är ett projekt.

        Analyserna har projektet i kroppen, inte i sökvägen, så `_projekt_krav` når dem inte.
        Kroppen som inte går att tolka lämnas åt endpointen att avvisa (400)."""
        if verb != "POST" or (sokvag.rstrip("/") or "/") not in (
            "/api/analys/bifurkationer",
            "/api/analys/intervall",
            "/api/analys/svf-intervall",
            "/api/analys/ekologi",
            *BERAKNINGS_SOKVAGAR,
        ):
            return None
        try:
            payload = json.loads(kropp.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            return None
        omrade_id = payload.get("omrade_id") if isinstance(payload, Mapping) else None
        if isinstance(omrade_id, str) and (
            self.projekt.ar_projekt(omrade_id) or self._registrerat_projekt(omrade_id)
        ):
            return omrade_id
        return None

    def _registrerat_projekt(self, projekt_id: str) -> bool:
        """Finns `projekt_id` i kontoregistret? (Då har det roller, det är inget öppet område.)"""
        register = self.konto.register
        return register is not None and bool(register.projekt_finns(projekt_id))

    def _statisk(
        self, sokvag: str, bara_huvud: bool
    ) -> tuple[int, list[tuple[str, str]], bytes] | None:
        """En fil ur den byggda klienten (`webb/dist`), eller None om sökvägen inte är en."""
        rot = self.klientmapp
        if rot is None:
            return None
        rel = urllib.parse.unquote(sokvag).lstrip("/") or "index.html"
        fil = (rot / rel).resolve()
        if not fil.is_relative_to(rot.resolve()) or not fil.is_file():
            return None
        innehall = fil.read_bytes()
        typ = _FILTYPER.get(fil.suffix.lower()) or mimetypes.guess_type(fil.name)[0]
        rubriker = [
            ("Content-Type", typ or "application/octet-stream"),
            ("Content-Length", str(len(innehall))),
            ("Cache-Control", "no-cache"),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "same-origin"),
        ]
        return 200, rubriker, b"" if bara_huvud else innehall


_logg = logging.getLogger(__name__)


BERAKNINGS_SOKVAGAR = ("/api/kor", "/api/risk", "/api/rutt")
"""Beräkningar som tar `omrade_id` i kroppen och inte sparar något: projektroll `lasare` räcker."""


def _utan_cors(
    svar: tuple[int, dict[str, str], bytes],
) -> tuple[int, list[tuple[str, str]], bytes]:
    """Svaret som rubriklista utan `Access-Control-*` (med inloggning på: samma ursprung)."""
    status, headers, kropp = svar
    ut = [(k, v) for k, v in headers.items() if not k.lower().startswith("access-control-")]
    return status, ut, kropp


def _projekt_krav(verb: str, sokvag: str) -> tuple[str, str] | None:
    """Returnerar `(projekt_id, minsta_roll)` för projektbundna API-sökvägar, annars None."""
    delar = [urllib.parse.unquote(d) for d in sokvag.split("/") if d]
    if len(delar) < 3 or delar[0] != "api":
        return None
    grupp, projekt_id = delar[1], delar[2]
    if grupp == "projekt":
        # Skapa rapport (POST) kräver utredare; läsa räcker med läsare.
        return projekt_id, "lasare" if verb in ("GET", "HEAD", "OPTIONS") else "utredare"
    if grupp == "3d":
        return projekt_id, "lasare"
    if grupp == "kartverktyg" and projekt_id != "wms":
        minst = "lasare" if verb in ("GET", "HEAD", "OPTIONS") else "utredare"
        return projekt_id, minst
    return None


_FILTYPER = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".woff2": "font/woff2",
    ".map": "application/json; charset=utf-8",
}


def _skapa_konto(konfig_fil: Path) -> KontoAdapter:
    """Kontosystemet ur `[konto]` i konfigurationen. Utan konfigurationsfil: inloggning av."""
    if not konfig_fil.is_file():
        return KontoAdapter(KontoKonfig())
    lokal = konfig_fil.resolve().parent / "config.local.toml"
    konfig = las_konfig(konfig_fil, lokal)
    konto_konf = las_konto_konfig(konfig)
    if konto_konf.aktiv and not konto_konf.databas.is_absolute():
        konto_konf = replace(konto_konf, databas=konfig_fil.resolve().parent / konto_konf.databas)
    return KontoAdapter(konto_konf)


REPO_ROT = Path(__file__).resolve().parents[2]
"""Repots rot: `src/webb/api.py` ligger två nivåer ner. I Docker är det `/app`."""


def _testsvitrot(konfig_fil: Path, testsvit_rot: str | Path | None) -> Path:
    """Testsvitens rot. Utan eget val: `testsvit` bredvid konfigurationsfilen, annars i repots rot.

    Aldrig relativt arbetskatalogen, så att servern hittar sina områden oavsett var den startas
    (som `klientmapp`). Ett uttryckligt värde används som det är."""
    if testsvit_rot is not None:
        return Path(testsvit_rot)
    rot = konfig_fil.resolve().parent if konfig_fil.is_file() else REPO_ROT
    return rot / "testsvit"


def _klientmapp(konfig_fil: Path, klientmapp: str | Path | None) -> Path | None:
    """Mappen med den byggda klienten, om `index.html` finns där (annars serveras ingen klient)."""
    rot = konfig_fil.resolve().parent
    val: str | Path = klientmapp if klientmapp is not None else STANDARD_KLIENTMAPP
    if klientmapp is None and konfig_fil.is_file():
        with konfig_fil.open("rb") as fh:
            val = tomllib.load(fh).get("webb", {}).get("klientmapp", STANDARD_KLIENTMAPP)
    mapp = Path(val)
    mapp = mapp if mapp.is_absolute() else rot / mapp
    return mapp if (mapp / "index.html").is_file() else None


def skapa_http_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    testsvit_rot: str | Path | None = None,
    konfig_fil: str | Path = "config.toml",
    fragemappar: Sequence[str | Path] | None = None,
    cachemapp: str | Path | None = None,
    korningsmapp: str | Path | None = None,
    klientmapp: str | Path | None = None,
    konto: KontoAdapter | None = None,
) -> ThreadingHTTPServer:
    """Skapar en lokal `ThreadingHTTPServer` kopplad till `FlodenApiApp`.

    Klienten (`webb/dist`, om den är byggd) och API:t serveras från samma port; inloggningen styrs
    av `[konto]` i konfigurationen (standard `inloggning = "av"`, oförändrat beteende)."""
    app = FlodenApiApp(
        testsvit_rot=testsvit_rot,
        konfig_fil=konfig_fil,
        fragemappar=fragemappar,
        cachemapp=cachemapp,
        korningsmapp=korningsmapp,
        konto=konto,
        klientmapp=klientmapp,
    )

    max_bytes = 25 * 1024 * 1024

    class _FlodenHandler(BaseHTTPRequestHandler):
        def _skicka(
            self, status: int, headers: Sequence[tuple[str, str]], kropp: bytes, huvud: bool = False
        ) -> None:
            self.send_response(status)
            for nyckel, varde in headers:
                self.send_header(nyckel, varde)
            if not any(n.lower() == "content-length" for n, _ in headers):
                self.send_header("Content-Length", str(len(kropp)))
            self.end_headers()
            if not huvud:
                self.wfile.write(kropp)

        def _las_kropp(self) -> bytes:
            raw_len = self.headers.get("Content-Length", "0") or "0"
            try:
                langd = int(raw_len)
            except ValueError as exc:
                raise FlodenWebbFel(f"Ogiltig Content-Length: {raw_len!r}", status_kod=400) from exc
            if langd < 0 or langd > max_bytes:
                raise FlodenWebbFel(
                    f"Förfrågningskropp överstiger maxgränsen ({max_bytes} byte).", status_kod=413
                )
            return self.rfile.read(langd) if langd > 0 else b""

        def _hantera(self, metod: str) -> None:
            rubriker: dict[str, str] = {}
            for k, v in self.headers.items():
                kl = k.lower()
                if kl in rubriker:
                    sep = "; " if kl == "cookie" else ", "
                    rubriker[kl] = f"{rubriker[kl]}{sep}{v}"
                else:
                    rubriker[kl] = v
            status, headers, kropp = app.hantera_http(
                metod, self.path, rubriker, self._las_kropp
            )
            self._skicka(status, headers, kropp, huvud=metod == "HEAD")

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._hantera("OPTIONS")

        def do_HEAD(self) -> None:  # noqa: N802
            self._hantera("HEAD")

        def do_GET(self) -> None:  # noqa: N802
            self._hantera("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._hantera("POST")

        def log_message(self, format: str, *args: Any) -> None:
            return

    return ThreadingHTTPServer((host, port), _FlodenHandler)


def main() -> None:
    """Startar Flodens lokala webb-API-server på http://127.0.0.1:8765 (utan inloggning)."""
    ap = argparse.ArgumentParser(prog="python -m webb.api", description=main.__doc__)
    ap.add_argument("--host", default="127.0.0.1", help="Standard 127.0.0.1 (bara den här datorn)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument(
        "--fragemapp",
        action="append",
        default=None,
        help="Mapp med K20-frågor (upprepa för flera). Standard: [webb] i config.toml.",
    )
    args = ap.parse_args()
    server = skapa_http_server(args.host, args.port, fragemappar=args.fragemapp)
    raw_host = server.server_address[0]
    host = raw_host.decode("utf-8") if isinstance(raw_host, bytes | bytearray) else str(raw_host)
    port = server.server_address[1]
    print(f"Floden API lyssnar på http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
