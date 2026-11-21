"""Toleransprofiler: versionerade TOML-filer som skiljer skiss från rapport (KB3)."""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from buller.typer import BullerFel
from gemensamt.hash import sha256_fil

Lage = Literal["skiss", "rapport"]

SKISSMARKNING = "Skiss – ej för beslut"
# KB3/v1: i rapportläget är klustring bara tillåten med felgräns högst 0,1 dB.
RAPPORT_MAX_FELGRANS_DB = 0.1
PROFILKATALOG = Path(__file__).resolve().parent / "profiler"

_TILLATNA_NYCKLAR: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    # sektion: (obligatoriska, frivilliga)
    "profil": (frozenset({"namn", "version", "lage", "markning"}), frozenset()),
    "klustring": (frozenset({"felgrans_db", "kontrollmottagare_antal"}), frozenset()),
    "banor": (frozenset({"reflexordning"}), frozenset({"maxradie_m"})),
}


@dataclass(frozen=True)
class Toleransprofil:
    """Inställningar för ett läge. `sha256` är kontrollsumman för profilfilen."""

    namn: str
    version: str
    lage: Lage
    markning: str
    klustring_felgrans_db: float
    kontrollmottagare_antal: int
    maxradie_m: float | None
    reflexordning: int
    sha256: str

    def __post_init__(self) -> None:
        if self.lage not in ("skiss", "rapport"):
            raise BullerFel(f"Okänt läge '{self.lage}' i toleransprofilen.")
        if not self.namn.strip() or not self.version.strip():
            raise BullerFel("Toleransprofilen saknar namn eller version.")
        if not math.isfinite(self.klustring_felgrans_db) or self.klustring_felgrans_db < 0.0:
            raise BullerFel("klustring.felgrans_db ska vara ändlig och >= 0.")
        if self.kontrollmottagare_antal < 0:
            raise BullerFel("klustring.kontrollmottagare_antal ska vara >= 0.")
        if self.maxradie_m is not None and not (
            math.isfinite(self.maxradie_m) and self.maxradie_m > 0.0
        ):
            raise BullerFel("banor.maxradie_m ska vara ändlig och > 0 (eller utelämnas).")
        if self.reflexordning < 0:
            raise BullerFel("banor.reflexordning ska vara >= 0.")
        if self.lage == "skiss" and self.markning != SKISSMARKNING:
            raise BullerFel(f"En skissprofil måste ha märkningen '{SKISSMARKNING}'.")
        if self.lage == "rapport":
            if self.markning == SKISSMARKNING or not self.markning.strip():
                raise BullerFel("En rapportprofil ska ha en egen, icke-tom märkning.")
            if self.klustring_felgrans_db > RAPPORT_MAX_FELGRANS_DB:
                raise BullerFel(
                    f"Rapportläget tillåter klustring bara med felgräns <= "
                    f"{RAPPORT_MAX_FELGRANS_DB} dB (KB3), profilen anger "
                    f"{self.klustring_felgrans_db} dB."
                )

    def till_manifest(self) -> dict[str, Any]:
        """Profilens innehåll för `parametrar.toleransprofil` i körmanifestet (KB4)."""
        return {
            "namn": self.namn,
            "version": self.version,
            "sha256": self.sha256,
            "klustring_felgrans_db": self.klustring_felgrans_db,
            "kontrollmottagare_antal": self.kontrollmottagare_antal,
            "maxradie_m": self.maxradie_m,
            "reflexordning": self.reflexordning,
        }


def _hamta(sektion: dict[str, Any], nyckel: str, typ: type | tuple[type, ...], var: str) -> Any:
    varde = sektion[nyckel]
    if isinstance(varde, bool) or not isinstance(varde, typ):
        raise BullerFel(f"{var}: '{nyckel}' har fel typ ({type(varde).__name__}).")
    return varde


def las_profil(sokvag: str | Path) -> Toleransprofil:
    """Läser och validerar en toleransprofil. Okända eller saknade nycklar ger `BullerFel`."""
    kalla = Path(sokvag)
    try:
        with kalla.open("rb") as fil:
            data = tomllib.load(fil)
    except (OSError, tomllib.TOMLDecodeError) as fel:
        raise BullerFel(f"Kunde inte läsa toleransprofilen {kalla}: {fel}") from fel

    okanda_sektioner = sorted(set(data) - set(_TILLATNA_NYCKLAR))
    if okanda_sektioner:
        raise BullerFel(f"{kalla}: okända sektioner: {', '.join(okanda_sektioner)}.")
    for sektion, (obligatoriska, frivilliga) in _TILLATNA_NYCKLAR.items():
        innehall = data.get(sektion)
        if not isinstance(innehall, dict):
            raise BullerFel(f"{kalla}: sektionen [{sektion}] saknas.")
        saknade = sorted(obligatoriska - set(innehall))
        okanda = sorted(set(innehall) - obligatoriska - frivilliga)
        if saknade or okanda:
            raise BullerFel(
                f"{kalla}: [{sektion}] saknar {saknade or 'inget'} och har okända nycklar "
                f"{okanda or 'inga'}."
            )

    var = str(kalla)
    profil, klustring, banor = data["profil"], data["klustring"], data["banor"]
    maxradie = _hamta(banor, "maxradie_m", (int, float), var) if "maxradie_m" in banor else None
    return Toleransprofil(
        namn=_hamta(profil, "namn", str, var),
        version=_hamta(profil, "version", str, var),
        lage=cast(Lage, _hamta(profil, "lage", str, var)),
        markning=_hamta(profil, "markning", str, var),
        klustring_felgrans_db=float(_hamta(klustring, "felgrans_db", (int, float), var)),
        kontrollmottagare_antal=_hamta(klustring, "kontrollmottagare_antal", int, var),
        maxradie_m=None if maxradie is None else float(maxradie),
        reflexordning=_hamta(banor, "reflexordning", int, var),
        sha256=sha256_fil(kalla),
    )


def las_inbyggd_profil(namn: Lage) -> Toleransprofil:
    """Läser `skiss` eller `rapport` ur `src/buller/lagen/profiler/`."""
    if namn not in ("skiss", "rapport"):
        raise BullerFel(f"Ingen inbyggd toleransprofil heter '{namn}'.")
    return las_profil(PROFILKATALOG / f"{namn}.toml")
