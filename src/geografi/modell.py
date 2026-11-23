# ruff: noqa: E501
"""Geografi som objekt: manifest (K46) och innehållsadresserat lager.

Lagret ligger i `<geo-rot>/_lager/<sha256>/` där sha256 är trädhashen över lagrets filer (relativ sökväg
och sha256 per fil, sorterade; `oppenhet.toml`, körloggar (`run.json`) och dolda filer räknas inte). Två versioner av en geografi
som delar ett lager (samma innehåll) pekar på samma mapp och lagrar det bara en gång. Versionens manifest
`<geo-rot>/<id>/<version>/manifest.json` har inga tider, inga nycklar och inga sökvägar utanför geo-roten,
så att samma mappning ger samma manifest byte för byte.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gemensamt.hash import sha256_bytes, sha256_fil

KONTRAKT_GEOGRAFI = "floden-geografi/v1"
MANIFEST_NAMN = "manifest.json"
LAGERMAPP = "_lager"
STANDARD_GEOROT = "data/lokal/geo"
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
_VERSION_RE = re.compile(r"^v[1-9][0-9]*$")


class GeografiFel(Exception):
    """Geografin saknas, är ogiltig eller manifestet stämmer inte med lagren."""


def tolka_ref(ref: str) -> tuple[str, str]:
    """`goteborg-test@v1` -> (`goteborg-test`, `v1`)."""
    id_, _, version = ref.partition("@")
    if not _ID_RE.match(id_) or not _VERSION_RE.match(version):
        raise GeografiFel(f"Ogiltig geografireferens '{ref}'. Formatet är <id>@v<nummer>, t.ex. goteborg-test@v1.")
    return id_, version


OMATADE_FILNAMN = frozenset({"oppenhet.toml", "run.json"})
"""Filer som inte ingår i trädhashen: öppenhetsfilen (ändras utan att innehållet ändras) och körloggen `run.json` (K0:
starttid och körtid, så samma markmodell skulle annars få olika hash varje gång; datafilernas sha256 står i den och
ingår i trädhashen via filerna själva)."""


def trad_filer(mapp: Path) -> list[tuple[str, str]]:
    """(relativ sökväg, sha256) för alla filer, sorterade; hoppar över dolda filer, oppenhet.toml och run.json."""
    ut: list[tuple[str, str]] = []
    for f in sorted(mapp.rglob("*")):
        rel = f.relative_to(mapp)
        if not f.is_file() or f.is_symlink() or any(d.startswith(".") for d in rel.parts) or f.name in OMATADE_FILNAMN:
            continue
        ut.append((rel.as_posix(), sha256_fil(f)))
    return ut


def trad_hash(filer: list[tuple[str, str]]) -> str:
    return sha256_bytes("\n".join(f"{sha}  {rel}" for rel, sha in filer).encode("utf-8"))


@dataclass(frozen=True)
class Lager:
    sha256: str
    mapp: Path
    filer: list[tuple[str, str]]
    nytt: bool  # False: fanns redan i lagret (delas med en annan version)


def lagra(geo_rot: Path, kalla: Path) -> Lager:
    """Flyttar `kalla` till lagret under dess trädhash. Finns innehållet redan tas `kalla` bort."""
    filer = trad_filer(kalla)
    if not filer:
        raise GeografiFel(f"Lagret {kalla.name} är tomt.")
    sha = trad_hash(filer)
    mal = geo_rot / LAGERMAPP / sha
    if mal.is_dir():
        shutil.rmtree(kalla)
        return Lager(sha, mal, filer, nytt=False)
    mal.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(kalla), str(mal))
    return Lager(sha, mal, filer, nytt=True)


def lager_mapp(geo_rot: Path, sha: str) -> Path:
    p = geo_rot / LAGERMAPP / sha
    if not p.is_dir():
        raise GeografiFel(f"Lagret {sha[:12]} saknas i {LAGERMAPP}. Mappa geografin igen.")
    return p


def versionsmapp(geo_rot: Path, id_: str, version: str) -> Path:
    return geo_rot / id_ / version


def skriv_manifest(geo_rot: Path, manifest: dict[str, Any]) -> tuple[Path, str]:
    """Skriver manifestet (K46) och returnerar (fil, sha256 av filen)."""
    if manifest.get("kontrakt") != KONTRAKT_GEOGRAFI:
        raise GeografiFel("Manifestet har fel kontrakt.")
    mapp = versionsmapp(geo_rot, str(manifest["id"]), str(manifest["version"]))
    mapp.mkdir(parents=True, exist_ok=True)
    fil = mapp / MANIFEST_NAMN
    fil.write_text(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return fil, sha256_fil(fil)


def las_manifest(geo_rot: Path, ref: str, *, verifiera: bool = True) -> tuple[dict[str, Any], str]:
    """Manifestet för `id@version` och dess sha256. Med `verifiera` kontrolleras att lagren finns och
    att varje fil har sin sha256 (en ändrad eller saknad fil ger `GeografiFel`)."""
    id_, version = tolka_ref(ref)
    fil = versionsmapp(geo_rot, id_, version) / MANIFEST_NAMN
    if not fil.is_file():
        raise GeografiFel(
            f"Geografin {ref} finns inte i {geo_rot.name}. Mappa den med: floden geografi mappa {id_}"
        )
    try:
        d = json.loads(fil.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise GeografiFel(f"Manifestet för {ref} går inte att läsa.") from exc
    if not isinstance(d, dict) or d.get("kontrakt") != KONTRAKT_GEOGRAFI:
        raise GeografiFel(f"Manifestet för {ref} är inte {KONTRAKT_GEOGRAFI}.")
    if verifiera:
        for namn, lager in d.get("lager", {}).items():
            mapp = lager_mapp(geo_rot, lager["sha256"])
            if trad_hash(trad_filer(mapp)) != lager["sha256"]:
                raise GeografiFel(f"Lagret '{namn}' i {ref} har ändrats efter mappningen (sha256 stämmer inte).")
    return d, sha256_fil(fil)
