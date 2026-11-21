"""Lagerprofiler: versionsbara filer som mappar baskartans lager till Flodens roller och markslag.

Idén kommer från O-Tids lagermappningsprofiler (ADR-0219 K6b,
`packages/application/src/map-layer-profiles.ts`, MIT, © 2026 Joel Berring), där en profil sparas
per konto i databasen. I Floden är profilen i stället en fil i repot (receptprincipen i AGENTS.md):
profilens sha256 skrivs i markmodellens `recept.json`, så samma profil ger samma markmodell och en
ändrad profil syns i receptet.

Formatet (TOML):

    namn = "stockholm_baskarta"
    version = 1
    [[linjer]]                       # lagerprefix -> roll och standardmått (som LagerMappning)
    prefix = "KANTSTEN"
    roll = "kantsten"
    standard_m = 0.12
    [[ytor]]                         # skraffyrlager -> markslagskod enligt K1
    prefix = "..."
    markslag = 1
"""

from __future__ import annotations

import math
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gemensamt.hash import sha256_bytes
from markmodell.grundkarta import GrundkartaFel, LagerMappning

STANDARD_LAGERPROFIL_SOKVAG: Path = (
    Path(__file__).resolve().parents[2] / "data" / "lagerprofiler" / "stockholm_baskarta.toml"
)
"""Stockholms baskarteprofil i repot (samma linjeregler som `STANDARD_STOCKHOLM_LAGERMAPPNING`)."""

GILTIGA_MARKSLAG_YTA: frozenset[int] = frozenset(range(1, 10))
"""Markslagskoder enligt K1 som en yta får mappas till (0 = okänt används inte i en regel)."""


@dataclass(frozen=True)
class YtRegel:
    """Mappning från skraffyr-/ytlager (prefix, skiftlägesokänsligt) till markslagskod enligt K1."""

    prefix: str
    markslag: int

    def __post_init__(self) -> None:
        if not isinstance(self.prefix, str) or not self.prefix.strip():
            raise GrundkartaFel("Lagerprefix i YtRegel får inte vara tomt.")
        object.__setattr__(self, "prefix", self.prefix.strip().upper())
        if (
            isinstance(self.markslag, bool)
            or not isinstance(self.markslag, int)
            or self.markslag not in GILTIGA_MARKSLAG_YTA
        ):
            raise GrundkartaFel(
                f"Ogiltig markslagskod {self.markslag!r} för ytlager '{self.prefix}' "
                "(giltiga: 1–9)."
            )

    def matchar(self, lagernamn: str) -> bool:
        """Returnerar True om lagernamnet matchar prefixet (skiftlägesokänsligt)."""
        return lagernamn.strip().upper().startswith(self.prefix)


@dataclass(frozen=True)
class Lagerprofil:
    """En inläst och validerad lagerprofil med kontrollsumma."""

    namn: str
    version: int
    linjeregler: tuple[LagerMappning, ...]
    ytregler: tuple[YtRegel, ...]
    sha256: str
    kalla: str

    def som_lagermappning(self) -> dict[str, tuple[str, float]]:
        """Linjereglerna som dict, samma form som `STANDARD_STOCKHOLM_LAGERMAPPNING`."""
        return {r.prefix: (r.roll, r.standard_m) for r in self.linjeregler}

    def matcha_yta(self, lagernamn: str) -> YtRegel | None:
        """Ytregeln för ett lager: exakt träff först, sedan längsta prefix."""
        norm = lagernamn.strip().upper()
        for regel in self.ytregler:
            if norm == regel.prefix:
                return regel
        for regel in self.ytregler:
            if regel.matchar(norm):
                return regel
        return None

    def recept_post(self) -> dict[str, str | int]:
        """Posten som skrivs i markmodellens `recept.json`."""
        return {
            "roll": "lagerprofil",
            "namn": self.namn,
            "version": self.version,
            "fil": self.kalla,
            "sha256": self.sha256,
        }


def _krav_str(tabell: Mapping[str, Any], nyckel: str, kontext: str) -> str:
    varde = tabell.get(nyckel)
    if not isinstance(varde, str) or not varde.strip():
        raise GrundkartaFel(f"{kontext}: '{nyckel}' måste vara en icke-tom sträng.")
    return varde


def _krav_tal(tabell: Mapping[str, Any], nyckel: str, kontext: str) -> float:
    varde = tabell.get(nyckel)
    if isinstance(varde, bool) or not isinstance(varde, int | float) or not math.isfinite(varde):
        raise GrundkartaFel(f"{kontext}: '{nyckel}' måste vara ett ändligt tal.")
    return float(varde)


def _krav_lista(data: Mapping[str, Any], nyckel: str) -> Sequence[Mapping[str, Any]]:
    varde = data.get(nyckel, [])
    if not isinstance(varde, list) or not all(isinstance(v, dict) for v in varde):
        raise GrundkartaFel(
            f"Lagerprofil: '{nyckel}' måste vara en lista av tabeller ([[{nyckel}]])."
        )
    return varde


def tolka_lagerprofil(data: bytes, *, kalla: str = "<bytes>") -> Lagerprofil:
    """Tolkar och validerar en TOML-profil; okända nycklar och dubbletter ger fel."""
    try:
        innehall = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise GrundkartaFel(f"Lagerprofilen {kalla} går inte att läsa som TOML: {exc}") from exc

    okanda = set(innehall) - {"namn", "version", "beskrivning", "linjer", "ytor"}
    if okanda:
        raise GrundkartaFel(f"Lagerprofilen {kalla} har okända nycklar: {sorted(okanda)}.")
    namn = _krav_str(innehall, "namn", f"Lagerprofil {kalla}")
    version = innehall.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise GrundkartaFel(f"Lagerprofil {kalla}: 'version' måste vara ett heltal >= 1.")

    linjeregler: list[LagerMappning] = []
    for idx, rad in enumerate(_krav_lista(innehall, "linjer")):
        kontext = f"Lagerprofil {kalla}, linjer[{idx}]"
        extra = set(rad) - {"prefix", "roll", "standard_m"}
        if extra:
            raise GrundkartaFel(f"{kontext}: okända nycklar {sorted(extra)}.")
        linjeregler.append(
            LagerMappning(
                prefix=_krav_str(rad, "prefix", kontext),
                roll=_krav_str(rad, "roll", kontext),
                standard_m=_krav_tal(rad, "standard_m", kontext),
            )
        )
    ytregler: list[YtRegel] = []
    for idx, rad in enumerate(_krav_lista(innehall, "ytor")):
        kontext = f"Lagerprofil {kalla}, ytor[{idx}]"
        extra = set(rad) - {"prefix", "markslag"}
        if extra:
            raise GrundkartaFel(f"{kontext}: okända nycklar {sorted(extra)}.")
        markslag: Any = rad.get("markslag")  # typen valideras i YtRegel
        ytregler.append(YtRegel(prefix=_krav_str(rad, "prefix", kontext), markslag=markslag))

    for typ, prefixen in (
        ("linjer", [r.prefix for r in linjeregler]),
        ("ytor", [r.prefix for r in ytregler]),
    ):
        dubbletter = sorted({p for p in prefixen if prefixen.count(p) > 1})
        if dubbletter:
            raise GrundkartaFel(f"Lagerprofil {kalla}: dubbla prefix i {typ}: {dubbletter}.")
    krock = sorted({r.prefix for r in linjeregler} & {r.prefix for r in ytregler})
    if krock:
        raise GrundkartaFel(f"Lagerprofil {kalla}: prefix både som linje och yta: {krock}.")

    def sortering(prefix: str) -> tuple[int, str]:
        return (-len(prefix), prefix)  # längsta prefix först, som _bygg_lagerregler

    return Lagerprofil(
        namn=namn,
        version=version,
        linjeregler=tuple(sorted(linjeregler, key=lambda r: sortering(r.prefix))),
        ytregler=tuple(sorted(ytregler, key=lambda r: sortering(r.prefix))),
        sha256=sha256_bytes(data),
        kalla=kalla,
    )


def las_lagerprofil(sokvag: str | Path = STANDARD_LAGERPROFIL_SOKVAG) -> Lagerprofil:
    """Läser en lagerprofil från fil (förval: Stockholms baskarta i repot)."""
    fil = Path(sokvag)
    if not fil.is_file():
        raise GrundkartaFel(f"Lagerprofilen hittades inte: {fil}")
    return tolka_lagerprofil(fil.read_bytes(), kalla=fil.name)
