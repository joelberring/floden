"""Körning och rapportering av Flodens testsvit (`docs/VALIDERING.md`)."""

from __future__ import annotations

import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
