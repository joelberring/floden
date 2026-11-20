"""Körmanifest enligt kontrakt K0/v1 (`docs/DATAKONTRAKT.md`)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

KONTRAKT_K0 = "K0/v1"


class ManifestFel(Exception):
    """Fel vid skapande, läsning eller validering av körmanifest."""


@dataclass(frozen=True)
class FilPost:
    """Beskriver en in- eller utdatafil i körmanifestet."""

    roll: str
    fil: str
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.roll, str) or not self.roll.strip():
            raise ManifestFel("FilPost.roll måste vara en icke-tom sträng.")
        if not isinstance(self.fil, str) or not self.fil.strip():
            raise ManifestFel("FilPost.fil måste vara en icke-tom sträng.")
        if not isinstance(self.sha256, str) or not self.sha256.strip():
            raise ManifestFel("FilPost.sha256 måste vara en icke-tom sträng.")

    def till_dict(self) -> dict[str, str]:
        """Konverterar posten till JSON-serialiserbart lexikon."""
        return {"roll": self.roll, "fil": self.fil, "sha256": self.sha256}

    @classmethod
    def fran_dict(cls, data: Any, sammanhang: str = "filpost") -> FilPost:
        """Skapar en `FilPost` från ett lexikon och validerar obligatoriska fält."""
        if not isinstance(data, dict):
            raise ManifestFel(f"{sammanhang} måste vara ett JSON-objekt.")
        for falt in ("roll", "fil", "sha256"):
            if falt not in data:
                raise ManifestFel(f"{sammanhang} saknar obligatoriskt fält '{falt}'.")
        return cls(
            roll=data["roll"],
            fil=data["fil"],
            sha256=data["sha256"],
        )


@dataclass
class RunManifest:
    """Körmanifest (`run.json`) enligt kontrakt K0/v1."""

    modul: str
    kodversion: str
    start: str
    indata: list[FilPost] = field(default_factory=list)
    parametrar: dict[str, Any] = field(default_factory=dict)
    utdata: list[FilPost] = field(default_factory=list)
    matvarden: dict[str, Any] = field(default_factory=dict)
    varningar: list[str] = field(default_factory=list)
    kontrakt: str = KONTRAKT_K0

    def __post_init__(self) -> None:
        self.validera()

    def validera(self) -> None:
        """Validerar att manifestet följer K0/v1 utan tysta fel."""
        if self.kontrakt != KONTRAKT_K0:
            raise ManifestFel(
                f"Ogiltigt kontrakt '{self.kontrakt}', förväntade '{KONTRAKT_K0}'."
            )
        for namnfalt, varde in (
            ("modul", self.modul),
            ("kodversion", self.kodversion),
            ("start", self.start),
        ):
            if not isinstance(varde, str) or not varde.strip():
                raise ManifestFel(f"Fältet '{namnfalt}' måste vara en icke-tom sträng.")

        if not isinstance(self.indata, list) or not all(
            isinstance(p, FilPost) for p in self.indata
        ):
            raise ManifestFel("Fältet 'indata' måste vara en lista av FilPost.")
        if not isinstance(self.utdata, list) or not all(
            isinstance(p, FilPost) for p in self.utdata
        ):
            raise ManifestFel("Fältet 'utdata' måste vara en lista av FilPost.")
        if not isinstance(self.parametrar, dict):
            raise ManifestFel("Fältet 'parametrar' måste vara ett objekt.")
        if not isinstance(self.matvarden, dict):
            raise ManifestFel("Fältet 'matvarden' måste vara ett objekt.")
        if not isinstance(self.varningar, list) or not all(
            isinstance(v, str) for v in self.varningar
        ):
            raise ManifestFel("Fältet 'varningar' måste vara en lista av strängar.")

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar manifestet till ett lexikon i K0/v1-ordning."""
        self.validera()
        return {
            "kontrakt": self.kontrakt,
            "modul": self.modul,
            "kodversion": self.kodversion,
            "start": self.start,
            "indata": [post.till_dict() for post in self.indata],
            "parametrar": self.parametrar,
            "utdata": [post.till_dict() for post in self.utdata],
            "matvarden": self.matvarden,
            "varningar": list(self.varningar),
        }

    @classmethod
    def fran_dict(cls, data: Any) -> RunManifest:
        """Läser och validerar ett `RunManifest` från ett deserialiserat JSON-objekt."""
        if not isinstance(data, dict):
            raise ManifestFel("Manifestet måste vara ett JSON-objekt.")

        obligatoriska = (
            "kontrakt",
            "modul",
            "kodversion",
            "start",
            "indata",
            "parametrar",
            "utdata",
            "matvarden",
            "varningar",
        )
        saknade = [falt for falt in obligatoriska if falt not in data]
        if saknade:
            raise ManifestFel(
                f"Manifestet saknar obligatoriska K0/v1-fält: {', '.join(saknade)}"
            )

        if data["kontrakt"] != KONTRAKT_K0:
            raise ManifestFel(
                f"Ogiltigt kontrakt '{data['kontrakt']}', förväntade '{KONTRAKT_K0}'."
            )

        raw_indata = data["indata"]
        raw_utdata = data["utdata"]
        if not isinstance(raw_indata, list):
            raise ManifestFel("Fältet 'indata' måste vara en lista.")
        if not isinstance(raw_utdata, list):
            raise ManifestFel("Fältet 'utdata' måste vara en lista.")

        indata = [
            FilPost.fran_dict(post, sammanhang=f"indata[{idx}]")
            for idx, post in enumerate(raw_indata)
        ]
        utdata = [
            FilPost.fran_dict(post, sammanhang=f"utdata[{idx}]")
            for idx, post in enumerate(raw_utdata)
        ]

        return cls(
            kontrakt=data["kontrakt"],
            modul=data["modul"],
            kodversion=data["kodversion"],
            start=data["start"],
            indata=indata,
            parametrar=data["parametrar"],
            utdata=utdata,
            matvarden=data["matvarden"],
            varningar=data["varningar"],
        )


def hamta_kodversion(repo_rot: str | Path | None = None) -> str:
    """Hämtar aktuell git-revision (kort SHA + `-smutsig` vid osparade ändringar).

    Om git inte är tillgängligt eller repot saknar commits returneras `"okand"`.
    """
    cwd = str(repo_rot) if repo_rot is not None else None
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        sha = rev.stdout.strip()
        if not sha:
            return "okand"
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        if status.stdout.strip():
            return f"{sha}-smutsig"
        return sha
    except (OSError, subprocess.CalledProcessError):
        return "okand"


def skapa_manifest(
    modul: str,
    *,
    indata: list[FilPost] | None = None,
    parametrar: dict[str, Any] | None = None,
    utdata: list[FilPost] | None = None,
    matvarden: dict[str, Any] | None = None,
    varningar: list[str] | None = None,
    kodversion: str | None = None,
    start: str | None = None,
    repo_rot: str | Path | None = None,
) -> RunManifest:
    """Hjälpfunktion för att skapa ett komplett K0/v1-manifest."""
    start_tid = start or datetime.now().astimezone().isoformat(timespec="seconds")
    version = kodversion if kodversion is not None else hamta_kodversion(repo_rot=repo_rot)
    return RunManifest(
        kontrakt=KONTRAKT_K0,
        modul=modul,
        kodversion=version,
        start=start_tid,
        indata=list(indata) if indata is not None else [],
        parametrar=dict(parametrar) if parametrar is not None else {},
        utdata=list(utdata) if utdata is not None else [],
        matvarden=dict(matvarden) if matvarden is not None else {},
        varningar=list(varningar) if varningar is not None else [],
    )


def skriv_manifest(manifest: RunManifest, sokvag: str | Path) -> Path:
    """Skriver ett validerat `RunManifest` som JSON till `sokvag`."""
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    innehall = json.dumps(manifest.till_dict(), indent=2, ensure_ascii=False) + "\n"
    mal.write_text(innehall, encoding="utf-8")
    return mal


def las_manifest(sokvag: str | Path) -> RunManifest:
    """Läser och validerar ett `RunManifest` från JSON-fil."""
    kalla = Path(sokvag)
    try:
        text = kalla.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestFel(f"Kunde inte läsa manifestfilen {kalla}: {exc}") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestFel(f"Ogiltig JSON i manifestfilen {kalla}: {exc}") from exc

    return RunManifest.fran_dict(data)
