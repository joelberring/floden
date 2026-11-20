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


def _validera_syntetiskt_fall(fall: Testfall) -> str:
    """Validerar K1-indata, K4-regn, recept-sha256, storlek < 1 MB och facit."""
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

    regn = las_regn_csv(indata_mapp / "regn.csv")
    facit = json.loads((facit_mapp / "facit.json").read_text(encoding="utf-8"))
    facit_typ = str(facit.get("typ", "analytisk"))

    # Kopplingspunkt för T02/T03: här anropas sänkgraf + hydraulik när modulerna är klara.
    return (
        f"K1+facit OK ({mark.shape[1]}x{mark.shape[0]}, {total_bytes // 1024} KB, "
        f"{len(regn)} regnpunkter, facit={facit_typ}; väntar på T02/T03)"
    )


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
            kommentar = _validera_syntetiskt_fall(fall)
            kortid_s = time.perf_counter() - start
            return FallResultat(
                fall=fall,
                utfall="GODKAND",
                kortid_s=kortid_s,
                volymfel_rel=None,
                maxdjupsfel_m=None,
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
