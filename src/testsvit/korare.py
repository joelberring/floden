"""Körning och rapportering av Flodens testsvit (`docs/VALIDERING.md`)."""

from __future__ import annotations

import json
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from gemensamt.hash import sha256_fil
from testsvit.k1_io import (
    EPSG_SWEREF99_1800,
    PIXELSTORLEK_M,
    las_geotiff,
    las_gpkg_tabell,
    las_regn_csv,
)

MAX_FALLSTORLEK_BYTES = 1024 * 1024  # 1 MB


class TestsvitFel(Exception):
    """Fel vid inläsning eller körning av testsviten."""


@dataclass(frozen=True)
class Testfall:
    """Metadata för ett testfall inläst från `fall.toml`."""

    id: str
    namn: str
    grupp: str
    typ: str
    beskrivning: str
    status: str
    mapp: Path


@dataclass(frozen=True)
class FallResultat:
    """Utfall och mätvärden för ett kört testfall."""

    fall: Testfall
    utfall: str  # "GODKAND", "SKELETT", "UNDERKAND"
    kortid_s: float
    volymfel_rel: float | None = None
    maxdjupsfel_m: float | None = None
    kommentar: str = ""

    @property
    def godkand(self) -> bool:
        return self.utfall in {"GODKAND", "SKELETT"}


def las_testfall(fall_mapp: Path) -> Testfall:
    """Läser `fall.toml` i en testfallsmapp och validerar obligatoriska fält."""
    toml_fil = fall_mapp / "fall.toml"
    if not toml_fil.is_file():
        raise TestsvitFel(f"Testfall saknar fall.toml: {fall_mapp}")

    try:
        data: dict[str, Any] = tomllib.loads(toml_fil.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise TestsvitFel(f"Kunde inte läsa {toml_fil}: {exc}") from exc

    obligatoriska = ("id", "namn", "grupp", "typ", "beskrivning", "status")
    saknade = [falt for falt in obligatoriska if falt not in data]
    if saknade:
        raise TestsvitFel(f"{toml_fil} saknar obligatoriska fält: {', '.join(saknade)}")

    return Testfall(
        id=str(data["id"]),
        namn=str(data["namn"]),
        grupp=str(data["grupp"]).upper(),
        typ=str(data["typ"]),
        beskrivning=str(data["beskrivning"]),
        status=str(data["status"]),
        mapp=fall_mapp,
    )


def hitta_testfall(rot: str | Path = "testsvit", grupp: str | None = None) -> list[Testfall]:
    """Hittar alla testfall under `rot`, valfritt filtrerat på grupp (t.ex. `A`)."""
    rot_sokvag = Path(rot)
    if not rot_sokvag.is_dir():
        raise TestsvitFel(f"Testsvitens rotmapp saknas: {rot_sokvag}")

    grupp_filter = grupp.upper() if grupp is not None else None
    fall_lista: list[Testfall] = []
    for post in sorted(rot_sokvag.iterdir()):
        if not post.is_dir():
            continue
        if not (post / "fall.toml").exists():
            continue
        fall = las_testfall(post)
        if grupp_filter is not None and fall.grupp != grupp_filter:
            continue
        fall_lista.append(fall)
    return fall_lista


def _mapp_storlek_bytes(mapp: Path) -> int:
    return sum(p.stat().st_size for p in mapp.rglob("*") if p.is_file())


def _berakna_statiskt_maxdjupsfel(
    fall_id: str,
    indata_mapp: Path,
    facit: dict[str, Any],
) -> tuple[float, float | None, str]:
    """Kör T02 statisk fyllning och beräknar volymfel och maxdjupsfel mot facit."""
    from sankgraf.fyllning import kor_statisk_k1_mapp, statisk_fyllning

    persistens_faktor = 2.0 if fall_id == "A9" else None
    graf, res_regn = kor_statisk_k1_mapp(indata_mapp, persistens_faktor=persistens_faktor)
    volymfel_rel = res_regn.volymfel_rel

    if volymfel_rel >= 0.001:
        raise TestsvitFel(
            f"Volymfel {volymfel_rel:.4%} överskrider valideringsgränsen 0,1 %"
        )

    maxdjupsfel_m: float | None = None
    detalj = f"T02 statisk: {len(graf.noder)} noder"

    if fall_id == "A1":
        res_full = statisk_fyllning(graf, 0.30)
        volymfel_rel = max(volymfel_rel, res_full.volymfel_rel)
        maxdjupsfel_m = abs(float(np.max(res_full.djup_m)) - float(facit["max_djup_m"]))
        detalj = f"T02: V={res_full.lagrat_m3:.2f}/{float(facit['slutvolym_m3']):.2f} m³"
    elif fall_id == "A2":
        res_full = statisk_fyllning(graf, 0.20)
        volymfel_rel = max(volymfel_rel, res_full.volymfel_rel)
        lov = sorted(graf.lovnoder(), key=lambda n: n.botten_rh2000)
        if len(lov) != 2:
            raise TestsvitFel(f"A2 förväntade 2 lövnoder, fick {len(lov)}")
        nedre, ovre = lov[0], lov[1]
        fel_ovre = abs(
            res_full.nod_niva_rh2000[ovre.nod_id] - float(facit["ovre"]["overfall_rh2000"])
        )
        fel_nedre = abs(
            res_full.nod_niva_rh2000[nedre.nod_id] - float(facit["nedre"]["overfall_rh2000"])
        )
        maxdjupsfel_m = max(fel_ovre, fel_nedre)
        detalj = f"T02: 2 sänkor i serie, V={res_full.lagrat_m3:.2f} m³"
    elif fall_id == "A3":
        res_full = statisk_fyllning(graf, 0.30)
        volymfel_rel = max(volymfel_rel, res_full.volymfel_rel)
        maxdjupsfel_m = abs(float(np.max(res_full.djup_m)) - float(facit["max_djup_m"]))
        slut_v = float(facit["slutvolym_m3"])
        detalj = f"T02: sammanslagning, V={res_full.lagrat_m3:.2f}/{slut_v:.2f} m³"
    elif fall_id in {"A4", "A7", "A8"}:
        maxdjupsfel_m = abs(float(np.max(res_regn.djup_m)) - 0.0)
        detalj = f"T02: fri avrinning (max_djup={float(np.max(res_regn.djup_m)):.4f} m)"
    elif fall_id == "A5":
        max_syd = float(np.max(res_regn.djup_m[44:, :]))
        max_rannsten = float(np.max(res_regn.djup_m[16:44, :]))
        maxdjupsfel_m = max_syd
        detalj = f"T02: rännsten={max_rannsten:.3f} m, gård={max_syd:.4f} m"
    elif fall_id == "A6":
        res_over = statisk_fyllning(graf, 0.08)
        volymfel_rel = max(volymfel_rel, res_over.volymfel_rel)
        if not res_over.utflodes_celler:
            raise TestsvitFel("A6 gav inget utflöde vid regn över infartsnivå")
        maxdjupsfel_m = 0.0
        detalj = f"T02: {len(res_over.utflodes_celler)} utflödesceller vid infart"
    elif fall_id == "A9":
        graf_ofilt, _ = kor_statisk_k1_mapp(indata_mapp, persistens_faktor=None)
        if len(graf_ofilt.lovnoder()) <= 5:
            raise TestsvitFel(
                f"A9 ofiltrerad graf hade bara {len(graf_ofilt.lovnoder())} löv (förväntade > 5)"
            )
        res_full = statisk_fyllning(graf, 0.25)
        volymfel_rel = max(volymfel_rel, res_full.volymfel_rel)
        if len(graf.noder) != int(facit["antal_verkliga_sankor"]):
            raise TestsvitFel(
                f"A9 persistensfilter gav {len(graf.noder)} noder, "
                f"förväntade {facit['antal_verkliga_sankor']}"
            )
        verklig = graf.lovnoder()[0]
        maxdjupsfel_m = abs(verklig.botten_rh2000 - float(facit["verklig_sanka_botten_rh2000"]))
        detalj = (
            f"T02: {len(graf_ofilt.noder)} -> 1 sänka "
            f"(djup={float(np.max(res_full.djup_m)):.3f} m)"
        )

    if maxdjupsfel_m is not None and maxdjupsfel_m >= 0.01:
        raise TestsvitFel(
            f"Maxdjupsfel {maxdjupsfel_m:.4f} m överskrider A-gränsen 0,01 m"
        )

    return volymfel_rel, maxdjupsfel_m, detalj


def _validera_syntetiskt_fall(fall: Testfall) -> tuple[float, float | None, str]:
    """Validerar K1-indata, K4-regn, recept-sha256, storlek < 1 MB och kör T02 statisk fysik."""
    indata_mapp = fall.mapp / "indata"
    facit_mapp = fall.mapp / "facit"

    obligatoriska_indata = (
        "mark.tif",
        "osakerhet.tif",
        "markslag.tif",
        "brytlinjer.gpkg",
        "oppningar.gpkg",
        "recept.json",
        "regn.csv",
    )
    for filnamn in obligatoriska_indata:
        if not (indata_mapp / filnamn).is_file():
            raise TestsvitFel(f"Saknad K1/K4-fil: indata/{filnamn}")

    for facit_filnamn in ("facit.json", "oversikt.png"):
        if not (facit_mapp / facit_filnamn).is_file():
            raise TestsvitFel(f"Saknad facit-fil: facit/{facit_filnamn}")

    total_bytes = _mapp_storlek_bytes(fall.mapp)
    if total_bytes >= MAX_FALLSTORLEK_BYTES:
        raise TestsvitFel(
            f"Fallmappen överskrider 1 MB ({total_bytes} bytes >= {MAX_FALLSTORLEK_BYTES})"
        )

    recept = json.loads((indata_mapp / "recept.json").read_text(encoding="utf-8"))
    if recept.get("kontrakt") != "K1/v1" or recept.get("epsg") != EPSG_SWEREF99_1800:
        raise TestsvitFel("Ogiltig kontrakt/EPSG i recept.json")
    kallor = recept.get("kallor", {})
    for roll in ("mark", "osakerhet", "markslag", "brytlinjer", "oppningar", "regn"):
        if roll not in kallor:
            raise TestsvitFel(f"recept.json saknar källpost '{roll}'")
        post = kallor[roll]
        fil_sokvag = indata_mapp / post["fil"]
        faktisk_sha = sha256_fil(fil_sokvag)
        if faktisk_sha != post["sha256"]:
            raise TestsvitFel(f"SHA-256-avvikelse för {post['fil']}")

    mark, mark_info = las_geotiff(indata_mapp / "mark.tif")
    osakerhet, osak_info = las_geotiff(indata_mapp / "osakerhet.tif")
    markslag, slag_info = las_geotiff(indata_mapp / "markslag.tif")

    for namn, info in (("mark", mark_info), ("osakerhet", osak_info), ("markslag", slag_info)):
        if info.epsg != EPSG_SWEREF99_1800:
            raise TestsvitFel(f"{namn}.tif har EPSG:{info.epsg}, förväntade 3011")
        if info.pixelstorlek_x != PIXELSTORLEK_M or info.pixelstorlek_y != PIXELSTORLEK_M:
            raise TestsvitFel(f"{namn}.tif har fel upplösning")

    if mark.shape != osakerhet.shape or mark.shape != markslag.shape:
        raise TestsvitFel("Rasterdimensioner skiljer sig mellan K1-rastrerna")
    if not np.all(np.isfinite(mark)) or not np.all(np.isfinite(osakerhet)):
        raise TestsvitFel("mark.tif eller osakerhet.tif innehåller NaN/Inf")

    bryt_srs, _ = las_gpkg_tabell(indata_mapp / "brytlinjer.gpkg", "brytlinjer")
    opp_srs, _ = las_gpkg_tabell(indata_mapp / "oppningar.gpkg", "oppningar")
    if bryt_srs != EPSG_SWEREF99_1800 or opp_srs != EPSG_SWEREF99_1800:
        raise TestsvitFel("GeoPackage saknar EPSG:3011")

    las_regn_csv(indata_mapp / "regn.csv")
    facit = json.loads((facit_mapp / "facit.json").read_text(encoding="utf-8"))
    if not isinstance(facit, dict):
        raise TestsvitFel("facit.json måste vara ett JSON-objekt")

    volymfel_rel, maxdjupsfel_m, detalj = _berakna_statiskt_maxdjupsfel(
        fall.id, indata_mapp, facit
    )
    kommentar = f"K1 OK ({mark.shape[1]}x{mark.shape[0]}, {total_bytes // 1024} KB); {detalj}"
    return volymfel_rel, maxdjupsfel_m, kommentar


def kor_fall(fall: Testfall) -> FallResultat:
    """Kör ett enskilt testfall och returnerar dess mätvärden."""
    start = time.perf_counter()

    if fall.typ == "skelett" or fall.status == "skelett":
        indata_ok = (fall.mapp / "indata").is_dir()
        facit_ok = (fall.mapp / "facit").is_dir()
        kortid_s = time.perf_counter() - start
        if not (indata_ok and facit_ok):
            saknade_mappar = [
                namn for namn, ok in (("indata", indata_ok), ("facit", facit_ok)) if not ok
            ]
            return FallResultat(
                fall=fall,
                utfall="UNDERKAND",
                kortid_s=kortid_s,
                kommentar=f"Skelettfall saknar mapp(ar): {', '.join(saknade_mappar)}",
            )
        return FallResultat(
            fall=fall,
            utfall="SKELETT",
            kortid_s=kortid_s,
            kommentar="Skelettfall verifierat (struktur OK, inga beräkningssteg ännu)",
        )

    if fall.typ == "syntetisk":
        try:
            volymfel_rel, maxdjupsfel_m, kommentar = _validera_syntetiskt_fall(fall)
            kortid_s = time.perf_counter() - start
            return FallResultat(
                fall=fall,
                utfall="GODKAND",
                kortid_s=kortid_s,
                volymfel_rel=volymfel_rel,
                maxdjupsfel_m=maxdjupsfel_m,
                kommentar=kommentar,
            )
        except TestsvitFel as exc:
            kortid_s = time.perf_counter() - start
            return FallResultat(
                fall=fall,
                utfall="UNDERKAND",
                kortid_s=kortid_s,
                kommentar=str(exc),
            )

    kortid_s = time.perf_counter() - start
    return FallResultat(
        fall=fall,
        utfall="UNDERKAND",
        kortid_s=kortid_s,
        kommentar=f"Okänd eller ej implementerad falltyp '{fall.typ}'",
    )


def formatera_tabell(resultat: list[FallResultat]) -> str:
    """Formaterar körresultaten som en läsbar Markdown-tabell."""
    rader = [
        "| ID | Grupp | Namn | Status | Utfall | Volymfel | Maxdjupsfel | Körtid (s) | Kommentar |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in resultat:
        volym = f"{r.volymfel_rel:.4%}" if r.volymfel_rel is not None else "–"
        djup = f"{r.maxdjupsfel_m:.4f} m" if r.maxdjupsfel_m is not None else "–"
        rader.append(
            f"| {r.fall.id} | {r.fall.grupp} | {r.fall.namn} | {r.fall.status} "
            f"| {r.utfall} | {volym} | {djup} | {r.kortid_s:.4f} | {r.kommentar} |"
        )
    return "\n".join(rader)


def kor_testsvit(
    rot: str | Path = "testsvit",
    grupp: str | None = None,
) -> tuple[list[FallResultat], str]:
    """Hittar och kör testfall och returnerar `(resultatlista, formaterad_tabell)`."""
    fall_lista = hitta_testfall(rot=rot, grupp=grupp)
    resultat = [kor_fall(fall) for fall in fall_lista]
    tabell = formatera_tabell(resultat)
    return resultat, tabell
