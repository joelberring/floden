# SPDX-License-Identifier: MIT
"""Explicit SHA-256-verifierad inläsare för vägtrafikens emissionsfaktorer (CSV/TOML).

Följer val **3A** i `api_data_och_ekologi_plan.md`:
1. Användaren eller kommunen kan ladda en egen lokal emissionsfaktortabell (CSV eller TOML)
   med verifierad SHA-256-kontrollsumma och fullständig källspårning.
2. Floden erbjuder ett uttryckligt `skiss`-märkt publikt referensschablonset i
   `data/luft/skiss_schablon_emission.toml` (`ar_schablon = true`, `metodstatus = "skiss"`,
   tydlig varningstext) — som **aldrig** används tyst utan aktivt val
   (`anvand_skiss_schablon=True`).

Modulen ligger i `src/luft/` (MIT-licens) och använder enbart Python-standardbiblioteket
samt `luft.typer.LuftFel` (noll GPL-importer).
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from luft.typer import LuftFel

FORMAT_EMISSIONSFAKTORER: Final[str] = "floden-luft-emissionsfaktorer/v1"
STANDARD_SKISS_SCHABLON_FIL: Final[Path] = (
    Path(__file__).resolve().parents[2] / "data" / "luft" / "skiss_schablon_emission.toml"
)

_FORDONSKLASS_ALIAS: Final[dict[str, str]] = {
    "latt": "latt",
    "lätt": "latt",
    "light": "latt",
    "personbil": "latt",
    "l": "latt",
    "tung": "tung",
    "heavy": "tung",
    "lastbil": "tung",
    "t": "tung",
}


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalisera_fordonsklass(ra: Any) -> str:
    if not isinstance(ra, str) or not ra.strip():
        raise LuftFel(f"Ogiltig fordonsklass {ra!r}: ange 'lätt' eller 'tung'.")
    nyckel = ra.strip().lower()
    norm = _FORDONSKLASS_ALIAS.get(nyckel)
    if norm is None:
        raise LuftFel(
            f"Okänd fordonsklass {ra!r}: tillåtna värden är 'lätt'/'latt' eller 'tung'."
        )
    return norm


def _tolka_flyttal(
    ra: Any,
    namn: str,
    *,
    min_v: float = 0.0,
    max_v: float | None = None,
    strikt_min: bool = False,
) -> float:
    if ra is None or isinstance(ra, bool):
        raise LuftFel(f"Fältet '{namn}' måste vara ett ändligt tal, fick {ra!r}.")
    if isinstance(ra, int | float):
        v = float(ra)
    elif isinstance(ra, str):
        text = ra.strip().replace(",", ".")
        if not text:
            raise LuftFel(f"Fältet '{namn}' får inte vara tomt.")
        try:
            v = float(text)
        except ValueError as exc:
            raise LuftFel(f"Fältet '{namn}' måste vara ett tal, fick {ra!r}.") from exc
    else:
        raise LuftFel(f"Fältet '{namn}' måste vara ett tal, fick {ra!r}.")
    if not math.isfinite(v):
        raise LuftFel(f"Fältet '{namn}' måste vara ändligt, fick {ra!r}.")
    if (strikt_min and v <= min_v) or (not strikt_min and v < min_v):
        op = ">" if strikt_min else ">="
        raise LuftFel(f"Fältet '{namn}' måste uppfylla {op} {min_v:g}, fick {v:g}.")
    if max_v is not None and v > max_v:
        raise LuftFel(f"Fältet '{namn}' måste uppfylla <= {max_v:g}, fick {v:g}.")
    return v


def _tolka_ar(ra: Any) -> int:
    if isinstance(ra, bool) or ra is None:
        raise LuftFel(f"Fältet 'ar' måste vara ett giltigt årtal, fick {ra!r}.")
    if isinstance(ra, int):
        ar = ra
    elif isinstance(ra, str) and ra.strip():
        try:
            ar = int(ra.strip())
        except ValueError as exc:
            raise LuftFel(f"Fältet 'ar' måste vara ett heltal, fick {ra!r}.") from exc
    else:
        raise LuftFel(f"Fältet 'ar' måste vara ett heltal, fick {ra!r}.")
    if not (1990 <= ar <= 2100):
        raise LuftFel(f"Fältet 'ar' måste ligga i [1990, 2100], fick {ar}.")
    return ar


@dataclass(frozen=True)
class EmissionsFaktorRad:
    """En rad med emissionsfaktorer för en fordonsklass och skyltad/genomsnittlig hastighet."""

    fordonsklass: str  # "latt" eller "tung"
    hastighet_km_h: float
    nox_g_km: float
    pm10_avgas_g_km: float
    pm10_slitage_g_km: float
    direkt_no2_andel: float
    kalla: str
    ar: int

    def __post_init__(self) -> None:
        norm = _normalisera_fordonsklass(self.fordonsklass)
        object.__setattr__(self, "fordonsklass", norm)
        _tolka_flyttal(
            self.hastighet_km_h, "hastighet_km_h", min_v=0.0, max_v=150.0, strikt_min=True
        )
        _tolka_flyttal(self.nox_g_km, "nox_g_km", min_v=0.0)
        _tolka_flyttal(self.pm10_avgas_g_km, "pm10_avgas_g_km", min_v=0.0)
        _tolka_flyttal(self.pm10_slitage_g_km, "pm10_slitage_g_km", min_v=0.0)
        _tolka_flyttal(self.direkt_no2_andel, "direkt_no2_andel", min_v=0.0, max_v=1.0)
        if not isinstance(self.kalla, str) or not self.kalla.strip():
            raise LuftFel("EmissionsFaktorRad.kalla får inte vara tom.")
        _tolka_ar(self.ar)

    @property
    def pm10_total_g_km(self) -> float:
        return self.pm10_avgas_g_km + self.pm10_slitage_g_km

    def till_dict(self) -> dict[str, Any]:
        return {
            "fordonsklass": self.fordonsklass,
            "hastighet_km_h": self.hastighet_km_h,
            "nox_g_km": self.nox_g_km,
            "pm10_avgas_g_km": self.pm10_avgas_g_km,
            "pm10_slitage_g_km": self.pm10_slitage_g_km,
            "pm10_total_g_km": round(self.pm10_total_g_km, 6),
            "direkt_no2_andel": self.direkt_no2_andel,
            "kalla": self.kalla,
            "ar": self.ar,
        }


@dataclass(frozen=True)
class EmissionsFaktorTabell:
    """SHA-256-verifierad emissionsfaktortabell för lätta och tunga fordon."""

    rader: tuple[EmissionsFaktorRad, ...]
    sha256: str
    kalla: str
    ar: int | None
    ar_schablon: bool
    metodstatus: str
    varning: str | None = None
    filnamn: str | None = None

    def __post_init__(self) -> None:
        if not self.rader:
            raise LuftFel("Emissionsfaktortabellen saknar rader.")
        klasser = {r.fordonsklass for r in self.rader}
        for krav in ("latt", "tung"):
            if krav not in klasser:
                raise LuftFel(
                    f"Emissionsfaktortabellen måste innehålla rader för både 'lätt' och 'tung' "
                    f"(saknar '{krav}')."
                )
        if self.ar_schablon:
            if self.metodstatus != "skiss":
                raise LuftFel("Publikt schablonset måste alltid ha metodstatus='skiss'.")
            if not self.varning or not self.varning.strip():
                raise LuftFel("Publikt schablonset måste ha en uttrycklig varningstext.")

    def hamta_faktor(self, fordonsklass: str, hastighet_km_h: float) -> EmissionsFaktorRad:
        """Slår upp eller linjärinterpolerar emissionsfaktorer för `(fordonsklass, hastighet_km_h)`.

        Utanför tabellens lägsta/högsta hastighet kläms värdet deterministiskt till närmaste
        ändpunkt (extrapoleras aldrig blint).
        """
        klass = _normalisera_fordonsklass(fordonsklass)
        v = _tolka_flyttal(
            hastighet_km_h, "hastighet_km_h", min_v=0.0, max_v=150.0, strikt_min=True
        )
        kandidater = sorted(
            (r for r in self.rader if r.fordonsklass == klass),
            key=lambda r: r.hastighet_km_h,
        )
        if not kandidater:
            raise LuftFel(f"Tabellen saknar rader för fordonsklass '{klass}'.")
        if v <= kandidater[0].hastighet_km_h:
            return kandidater[0]
        if v >= kandidater[-1].hastighet_km_h:
            return kandidater[-1]

        for a, b in zip(kandidater, kandidater[1:], strict=False):
            if abs(v - a.hastighet_km_h) <= 1e-9:
                return a
            if abs(v - b.hastighet_km_h) <= 1e-9:
                return b
            if a.hastighet_km_h < v < b.hastighet_km_h:
                t = (v - a.hastighet_km_h) / (b.hastighet_km_h - a.hastighet_km_h)
                return EmissionsFaktorRad(
                    fordonsklass=klass,
                    hastighet_km_h=v,
                    nox_g_km=a.nox_g_km + t * (b.nox_g_km - a.nox_g_km),
                    pm10_avgas_g_km=a.pm10_avgas_g_km + t * (b.pm10_avgas_g_km - a.pm10_avgas_g_km),
                    pm10_slitage_g_km=(
                        a.pm10_slitage_g_km + t * (b.pm10_slitage_g_km - a.pm10_slitage_g_km)
                    ),
                    direkt_no2_andel=(
                        a.direkt_no2_andel + t * (b.direkt_no2_andel - a.direkt_no2_andel)
                    ),
                    kalla=a.kalla if a.kalla == b.kalla else f"{a.kalla} / {b.kalla}",
                    ar=max(a.ar, b.ar),
                )
        return kandidater[-1]

    def till_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT_EMISSIONSFAKTORER,
            "sha256": self.sha256,
            "kalla": self.kalla,
            "ar": self.ar,
            "ar_schablon": self.ar_schablon,
            "metodstatus": self.metodstatus,
            "varning": self.varning,
            "filnamn": self.filnamn,
            "antal_rader": len(self.rader),
            "rader": [r.till_dict() for r in self.rader],
        }


def _verifiera_sha256(faktisk: str, forvantad: str | None, sammanhang: str) -> None:
    if forvantad is None:
        return
    ren = forvantad.strip().lower()
    if faktisk.lower() != ren:
        raise LuftFel(
            f"SHA-256-verifiering misslyckades för {sammanhang}: "
            f"förväntade {ren}, fick {faktisk}."
        )


def _bygg_rad_fran_mapping(
    post: Mapping[str, Any],
    *,
    standard_kalla: str | None = None,
    standard_ar: int | None = None,
) -> EmissionsFaktorRad:
    klass_ra = (
        post.get("fordonsklass")
        if post.get("fordonsklass") is not None
        else post.get("klass")
        if post.get("klass") is not None
        else post.get("typ")
    )
    kalla_ra = post.get("kalla") or post.get("källa") or standard_kalla
    if not isinstance(kalla_ra, str) or not kalla_ra.strip():
        raise LuftFel("Varje emissionsfaktorrad måste ha en källangivelse ('kalla').")
    ar_ra = post.get("ar") if post.get("ar") is not None else post.get("år", standard_ar)
    return EmissionsFaktorRad(
        fordonsklass=_normalisera_fordonsklass(klass_ra),
        hastighet_km_h=_tolka_flyttal(
            post.get("hastighet_km_h"), "hastighet_km_h", min_v=0.0, max_v=150.0, strikt_min=True
        ),
        nox_g_km=_tolka_flyttal(post.get("nox_g_km"), "nox_g_km", min_v=0.0),
        pm10_avgas_g_km=_tolka_flyttal(post.get("pm10_avgas_g_km"), "pm10_avgas_g_km", min_v=0.0),
        pm10_slitage_g_km=_tolka_flyttal(
            post.get("pm10_slitage_g_km"), "pm10_slitage_g_km", min_v=0.0
        ),
        direkt_no2_andel=_tolka_flyttal(
            post.get("direkt_no2_andel"), "direkt_no2_andel", min_v=0.0, max_v=1.0
        ),
        kalla=kalla_ra.strip(),
        ar=_tolka_ar(ar_ra),
    )


def las_emissionsfaktorer_csv(
    text_eller_fil: str | bytes | Path,
    *,
    forvantad_sha256: str | None = None,
    filnamn: str | None = None,
) -> EmissionsFaktorTabell:
    """Läser och SHA-256-verifierar en lokal CSV-tabell med emissionsfaktorer."""
    eff_filnamn = filnamn
    if isinstance(text_eller_fil, Path):
        if not text_eller_fil.is_file():
            raise LuftFel(f"Emissionsfaktorfilen '{text_eller_fil}' finns inte.")
        ra_bytes = text_eller_fil.read_bytes()
        eff_filnamn = eff_filnamn or text_eller_fil.name
    elif isinstance(text_eller_fil, bytes):
        ra_bytes = text_eller_fil
    elif isinstance(text_eller_fil, str):
        ra_bytes = text_eller_fil.encode("utf-8")
    else:
        raise LuftFel(f"Ogiltig indatatyp för CSV: {type(text_eller_fil).__name__}.")

    sha = _sha256_hex(ra_bytes)
    _verifiera_sha256(sha, forvantad_sha256, eff_filnamn or "emissionsfaktorer (CSV)")

    try:
        text = ra_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LuftFel(f"Emissionsfaktor-CSV måste vara UTF-8-kodad: {exc}") from exc

    rader_ra = [r for r in text.splitlines() if r.strip() and not r.lstrip().startswith("#")]
    if len(rader_ra) < 2:
        raise LuftFel("Emissionsfaktor-CSV måste ha en rubrikrad och minst två datarader.")

    rubrik_rad = rader_ra[0]
    avgransare = ";" if rubrik_rad.count(";") >= rubrik_rad.count(",") else ","
    lasare = csv.DictReader(io.StringIO("\n".join(rader_ra)), delimiter=avgransare)
    if not lasare.fieldnames:
        raise LuftFel("Emissionsfaktor-CSV saknar rubrikkolumner.")

    rader: list[EmissionsFaktorRad] = []
    for rad in lasare:
        norm_rad: dict[str, Any] = {
            (k.strip().lower() if k is not None else ""): (v.strip() if isinstance(v, str) else v)
            for k, v in rad.items()
        }
        rader.append(_bygg_rad_fran_mapping(norm_rad))

    rader.sort(key=lambda r: (r.fordonsklass, r.hastighet_km_h))
    kallor = sorted({r.kalla for r in rader})
    ar_max = max(r.ar for r in rader)
    return EmissionsFaktorTabell(
        rader=tuple(rader),
        sha256=sha,
        kalla="; ".join(kallor),
        ar=ar_max,
        ar_schablon=False,
        metodstatus="skiss",
        varning=None,
        filnamn=eff_filnamn,
    )


def las_emissionsfaktorer_toml(
    text_eller_fil: str | bytes | Path,
    *,
    forvantad_sha256: str | None = None,
    filnamn: str | None = None,
) -> EmissionsFaktorTabell:
    """Läser och SHA-256-verifierar en lokal TOML-tabell med emissionsfaktorer."""
    eff_filnamn = filnamn
    if isinstance(text_eller_fil, Path):
        if not text_eller_fil.is_file():
            raise LuftFel(f"Emissionsfaktorfilen '{text_eller_fil}' finns inte.")
        ra_bytes = text_eller_fil.read_bytes()
        eff_filnamn = eff_filnamn or text_eller_fil.name
    elif isinstance(text_eller_fil, bytes):
        ra_bytes = text_eller_fil
    elif isinstance(text_eller_fil, str):
        ra_bytes = text_eller_fil.encode("utf-8")
    else:
        raise LuftFel(f"Ogiltig indatatyp för TOML: {type(text_eller_fil).__name__}.")

    sha = _sha256_hex(ra_bytes)
    _verifiera_sha256(sha, forvantad_sha256, eff_filnamn or "emissionsfaktorer (TOML)")

    try:
        dok = tomllib.loads(ra_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise LuftFel(f"Ogiltig TOML i emissionsfaktortabell: {exc}") from exc

    ra_rader = dok.get("faktorer") or dok.get("rader")
    if not isinstance(ra_rader, Sequence) or isinstance(ra_rader, str | bytes) or not ra_rader:
        raise LuftFel("Emissionsfaktor-TOML saknar tabellistan `[[faktorer]]`.")

    topp_kalla = str(dok.get("kalla", "")).strip() or None
    topp_ar_ra = dok.get("ar")
    topp_ar = _tolka_ar(topp_ar_ra) if topp_ar_ra is not None else None
    ar_schablon = bool(dok.get("ar_schablon", False))
    metodstatus = str(dok.get("metodstatus", "skiss")).strip() or "skiss"
    varning_ra = dok.get("varning")
    varning = (
        str(varning_ra).strip()
        if isinstance(varning_ra, str) and varning_ra.strip()
        else None
    )

    rader: list[EmissionsFaktorRad] = []
    for post in ra_rader:
        if not isinstance(post, Mapping):
            raise LuftFel("Varje post i `[[faktorer]]` måste vara ett TOML-objekt.")
        rader.append(
            _bygg_rad_fran_mapping(post, standard_kalla=topp_kalla, standard_ar=topp_ar)
        )

    rader.sort(key=lambda r: (r.fordonsklass, r.hastighet_km_h))
    kalla = topp_kalla or "; ".join(sorted({r.kalla for r in rader}))
    ar_val = topp_ar if topp_ar is not None else max(r.ar for r in rader)
    return EmissionsFaktorTabell(
        rader=tuple(rader),
        sha256=sha,
        kalla=kalla,
        ar=ar_val,
        ar_schablon=ar_schablon,
        metodstatus=metodstatus,
        varning=varning,
        filnamn=eff_filnamn,
    )


def las_emissionsfaktorer(
    fil: str | Path,
    *,
    forvantad_sha256: str | None = None,
) -> EmissionsFaktorTabell:
    """Läser en lokal emissionsfaktortabell (`.toml` eller `.csv`) med SHA-256-verifiering."""
    p = Path(fil)
    if p.suffix.lower() == ".toml":
        return las_emissionsfaktorer_toml(p, forvantad_sha256=forvantad_sha256)
    if p.suffix.lower() == ".csv":
        return las_emissionsfaktorer_csv(p, forvantad_sha256=forvantad_sha256)
    raise LuftFel(
        f"Okänt filformat '{p.suffix}' för emissionsfaktorer: ange .csv eller .toml."
    )


def ladda_skiss_schablon(
    *,
    anvand_skiss_schablon: bool = False,
    fil: Path | None = None,
) -> EmissionsFaktorTabell:
    """Laddar det publika `skiss`-märkta schablonsetet (kräver `anvand_skiss_schablon=True`).

    Får aldrig användas tyst som standard (val 3A i `api_data_och_ekologi_plan.md`).
    """
    if anvand_skiss_schablon is not True:
        raise LuftFel(
            "Publikt skiss-schablonset används aldrig tyst som standard. "
            "Ladda en lokal emissionsfaktortabell (CSV/TOML) eller välj uttryckligen "
            "anvand_skiss_schablon=True."
        )
    sokvag = fil if fil is not None else STANDARD_SKISS_SCHABLON_FIL
    tabell = las_emissionsfaktorer_toml(sokvag)
    if not tabell.ar_schablon or tabell.metodstatus != "skiss" or not tabell.varning:
        raise LuftFel(
            f"Schablonfilen '{sokvag}' måste vara märkt med ar_schablon=true, "
            "metodstatus='skiss' och en icke-tom varning."
        )
    return tabell


def hamta_emissionsfaktorer(
    *,
    fil: str | Path | None = None,
    csv_text: str | None = None,
    toml_text: str | None = None,
    forvantad_sha256: str | None = None,
    anvand_skiss_schablon: bool = False,
) -> EmissionsFaktorTabell:
    """Sammanhållen inläsare: lokal tabell (fil/CSV/TOML) eller aktivt valt `skiss`-schablonset."""
    if fil is not None:
        return las_emissionsfaktorer(fil, forvantad_sha256=forvantad_sha256)
    if csv_text is not None:
        return las_emissionsfaktorer_csv(csv_text, forvantad_sha256=forvantad_sha256)
    if toml_text is not None:
        return las_emissionsfaktorer_toml(toml_text, forvantad_sha256=forvantad_sha256)
    return ladda_skiss_schablon(anvand_skiss_schablon=anvand_skiss_schablon)


__all__ = [
    "FORMAT_EMISSIONSFAKTORER",
    "STANDARD_SKISS_SCHABLON_FIL",
    "EmissionsFaktorRad",
    "EmissionsFaktorTabell",
    "hamta_emissionsfaktorer",
    "ladda_skiss_schablon",
    "las_emissionsfaktorer",
    "las_emissionsfaktorer_csv",
    "las_emissionsfaktorer_toml",
]
