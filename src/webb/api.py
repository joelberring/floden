"""Lokalt HTTP/JSON-API för Flodens webbklient (T10)."""

from __future__ import annotations

import argparse
import base64
import json
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

from dynamik.ensemble import EnsembleResultat, EnsembleScenario, kor_ensemble
from dynamik.handelsestyrd import kor_dynamik_handelsestyrd
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
from rutt.parametrar import (
    FordonParametrar,
    RuttParametrarFel,
    RuttParametrarK7,
)
from rutt.risk import berakna_vag_kumulativ_risk
from rutt.vagnat import VagKant, VagNat, VagNatFel, VagNod
from sankgraf.hierarki import bygg_sankgraf
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
from webb.fel import FlodenWebbFel
from webb.fragor import FragaTjanst
from webb.kartverktyg import KartverktygTjanst
from webb.moduler import lista_moduler
from webb.projekt import ProjektTjanst
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


def _skapa_fragetjanst(
    konfig_fil: Path,
    fragemappar: Sequence[str | Path] | None,
    cachemapp: str | Path | None,
    korningsmapp: str | Path | None,
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
        testsvit_rot: str | Path = "testsvit",
        konfig_fil: str | Path = "config.toml",
        fragemappar: Sequence[str | Path] | None = None,
        cachemapp: str | Path | None = None,
        korningsmapp: str | Path | None = None,
        konto: KontoAdapter | None = None,
        klientmapp: str | Path | None = None,
        kartverktygsmapp: str | Path | None = None,
        datamappar: Sequence[str | Path] | None = None,
        tillat_loopback_underlag: bool = False,
    ) -> None:
        self._kartkonfig = (kartverktygsmapp, datamappar, tillat_loopback_underlag)
        self._kartverktyg: KartverktygTjanst | None = None
        self.testsvit_rot = Path(testsvit_rot)
        self.konfig_fil = Path(konfig_fil)
        self.konto = konto if konto is not None else _skapa_konto(self.konfig_fil)
        self.klientmapp = _klientmapp(self.konfig_fil, klientmapp)
        self._fragekonfig = (fragemappar, cachemapp, korningsmapp)
        self._fragor: FragaTjanst | None = None
        self._lock = threading.Lock()
        self._omrade_cache: dict[str, LaddatOmrade] = {}
        self._sektioner_cache: dict[str, Gatusektion] | None = None

    @property
    def fragor(self) -> FragaTjanst:
        """Tjänsten för K20-frågor. Skapas vid första användning (inga kataloger vid start)."""
        with self._lock:
            if self._fragor is None:
                self._fragor = _skapa_fragetjanst(self.konfig_fil, *self._fragekonfig)
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

            regn_fil = indata / "regn.csv"
            regn_rader = las_regn_csv(regn_fil)
            regn_tid = tuple(float(r[0]) for r in regn_rader)
            regn_int = tuple(float(r[1]) for r in regn_rader)

            recept_fil = indata / "recept.json"
            if recept_fil.is_file():
                bas_sha = sha256_fil(recept_fil)
            else:
                bas_sha = sha256_bytes(omrade_id.encode("utf-8"))

            bas_graf = bygg_sankgraf(
                mark_z,
                osak_z,
                markslag,
                geo,
                brytlinjer=brytlinjer,
                oppningar=oppningar,
            )
        except (K1FormatFel, SankGrafFel, OSError) as exc:
            raise FlodenWebbFel(
                f"Kunde inte ladda område '{omrade_id}': {exc}",
                status_kod=500,
            ) from exc

        laddat = LaddatOmrade(
            omrade_id=omrade_id,
            fall=fall,
            mark_z=mark_z,
            osakerhet_z=osak_z,
            markslag=markslag,
            geo=geo,
            brytlinjer=brytlinjer,
            oppningar=oppningar,
            regn_tid_s=regn_tid,
            regn_intensitet_mm_h=regn_int,
            bas_recept_sha256=bas_sha,
            kallor=DEFAULT_KALLOR,
            bas_graf=bas_graf,
        )
        with self._lock:
            self._omrade_cache[omrade_id] = laddat
        return laddat

    def halsa(self) -> dict[str, Any]:
        """Svar för `GET /api/halsa`."""
        return {
            "status": "ok",
            "version": API_VERSION,
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

        return {
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

    def _hantera_fragor(self, verb: str, sokvag: str) -> tuple[int, dict[str, str], bytes]:
        delar = [urllib.parse.unquote(d) for d in sokvag.split("/") if d][2:]
        if verb == "GET" and not delar:
            return _json_svar(200, self.fragor.lista_fragor())
        if verb == "GET" and len(delar) == 1:
            return _json_svar(200, self.fragor.hamta_fraga(delar[0]))
        if verb == "POST" and len(delar) == 2 and delar[1] == "plan":
            return _json_svar(200, self.fragor.planera_fraga(delar[0]))
        if verb == "POST" and len(delar) == 2 and delar[1] == "kor":
            svar = self.fragor.starta_korning(delar[0])
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
            valda = [str(x) for x in json_kropp().get("analyser", [])] if verb == "POST" else []
            if verb == "POST" and tail == ["plan"]:
                return _json_svar(200, kv.planera(projekt, namn, valda))
            if verb == "POST" and tail == ["kor"]:
                return _json_svar(202, kv.kor(projekt, namn, valda))
            if verb == "GET" and tail == ["status"]:
                return _json_svar(200, kv.status(projekt, namn))
        return _json_svar(404, {"fel": f"Okänd endpoint: {verb} {sokvag}", "status": 404})

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

            if verb == "GET" and ren_sokvag == "/api/regn":
                region_param = query.get("region", [None])[0]
                return _json_svar(200, self.lista_regn(region=region_param))

            if verb == "GET" and ren_sokvag == "/api/moduler":
                return _json_svar(200, lista_moduler(self._demo_moduler()))

            if ren_sokvag == "/api/projekt" or ren_sokvag.startswith("/api/projekt/"):
                if verb != "GET":
                    return _json_svar(405, {"fel": "Metoden är inte tillåten.", "status": 405})
                delar = [urllib.parse.unquote(d) for d in ren_sokvag.split("/") if d][2:]
                if not delar:
                    return _json_svar(200, self.projekt.lista())
                if len(delar) == 1:
                    return _json_svar(200, self.projekt.hamta(delar[0]))

            if ren_sokvag == "/api/fragor" or ren_sokvag.startswith("/api/fragor/"):
                return self._hantera_fragor(verb, ren_sokvag)

            if ren_sokvag.startswith("/api/kartverktyg/"):
                return self._hantera_kartverktyg(verb, ren_sokvag, kropp, query)

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
        except Exception as exc:  # noqa: BLE001 - returnera tydligt API-fel utan tyst krasch
            return _json_svar(
                500,
                {
                    "fel": f"Internt serverfel: {exc}",
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
        `kraver_inloggning` **före** att kroppen läses, och sist API:t. Med inloggning på skickas
        ingen `Access-Control-Allow-Origin`: klient och API nås från samma ursprung.
        `las_kropp` anropas högst en gång och bara när kroppen behövs.
        """
        verb = metod.upper()
        req = Forfragan.fran_ra(verb, ra_sokvag, rubriker)
        if self.konto.ager_sokvag(req.sokvag):
            kropp_in = las_kropp() if verb in ("POST", "PUT", "PATCH", "DELETE") else b""
            return self.konto.hantera_konto(verb, req.sokvag, req.query, req.headers, kropp_in)
        if verb in ("GET", "HEAD") and not req.sokvag.startswith("/api"):
            statisk = self._statisk(req.sokvag, verb == "HEAD")
            if statisk is not None:
                return statisk
        kontroll = self.konto.kraver_inloggning(req)
        if not kontroll.tillaten:
            assert kontroll.svar is not None
            return kontroll.svar
        try:
            kropp_in = las_kropp() if verb == "POST" else b""
        except FlodenWebbFel as exc:
            status, headers, kropp = _json_svar(
                exc.status_kod, {"fel": str(exc), "status": exc.status_kod}
            )
            return status, list(headers.items()), kropp
        status, headers, kropp = self.hantera_forfragan(verb, ra_sokvag, kropp_in)
        ut = list(headers.items())
        if self.konto.aktiv:
            ut = [(k, v) for k, v in ut if not k.lower().startswith("access-control-")]
        return status, ut, kropp

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


_FILTYPER = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
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
    testsvit_rot: str | Path = "testsvit",
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
            status, headers, kropp = app.hantera_http(
                metod, self.path, dict(self.headers.items()), self._las_kropp
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
