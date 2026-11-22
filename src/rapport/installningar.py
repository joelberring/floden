"""Rapportens inställningar: språkprofil, numrering, avsändare, logotyp, typsnitt och accentfärg.

Inställningarna lagras i rapporten (K33, fältet `installningar`), så att samma rapport alltid ger
samma fil. Fältet är valfritt: en äldre rapport utan fältet får förvalen.

**Filer (logotyp och typsnitt)** pekas ut med en lokal sökväg (från MCP, `tools/rapport_demo.py`,
Python eller `config.local.toml [rapport]`). Floden läser filen, kontrollerar den och lägger en
kopia med kontrollsumman som namn i rapportens mapp (`resurser/<sha256>.<ändelse>`, utanför git).
Rapporten lagrar bara kontrollsumman (sha256), filtyp och typsnittets familjenamn – aldrig
sökvägen – och sökvägen skrivs aldrig i felmeddelanden, loggar eller den exporterade filen.
Stadens logotyp och typsnittet Stockholm Type är skyddade och checkas aldrig in.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

from gemensamt.hash import sha256_fil

MAX_RESURS_BYTES: Final[int] = 10 * 1024 * 1024

SPRAKPROFILER: Final[tuple[str, ...]] = ("sbk", "neutral")
STANDARD_AVSANDARE: Final[str] = "Stadsbyggnadskontoret"
STANDARD_ACCENT: Final[str] = "000000"
FILROLLER: Final[dict[str, str]] = {
    "logotyp": "logotypen",
    "typsnitt_brodtext": "typsnittet för brödtext",
    "typsnitt_rubriker": "typsnittet för rubriker",
}
_TEXTFALT: Final[tuple[str, ...]] = ("avsandare", "diarienummer", "handlaggare")
_BOOLFALT: Final[tuple[str, ...]] = ("numrerade_rubriker", "numrerade_figurer")
NYCKLAR: Final[tuple[str, ...]] = (
    "sprakprofil",
    *_BOOLFALT,
    *_TEXTFALT,
    "accentfarg",
    *FILROLLER,
)
_BILD: Final[tuple[str, ...]] = (".png", ".jpg", ".jpeg")
_TYPSNITT: Final[tuple[str, ...]] = (".ttf", ".otf")


class InstallningsFel(ValueError):
    """En inställning är ogiltig. Meddelandet innehåller aldrig en sökväg."""


@dataclass(frozen=True)
class Resurs:
    """En lokal fil som rapporten använder (logotyp eller typsnitt), känd bara genom sin sha256."""

    sha256: str
    filtyp: str
    familj: str | None = None
    """Typsnittets familjenamn (ur filens namntabell); None för logotypen."""

    @property
    def filnamn(self) -> str:
        return f"{self.sha256}{self.filtyp}"

    def till_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"sha256": self.sha256, "filtyp": self.filtyp}
        if self.familj is not None:
            d["familj"] = self.familj
        return d

    @classmethod
    def fran_dict(cls, d: Mapping[str, Any]) -> Resurs:
        sha = str(d.get("sha256", ""))
        typ = str(d.get("filtyp", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", sha) or typ not in (*_BILD, *_TYPSNITT):
            raise InstallningsFel("En resurs i inställningarna saknar sha256 eller filtyp.")
        familj = d.get("familj")
        return cls(sha, typ, None if familj is None else str(familj))


@dataclass(frozen=True)
class RapportInstallningar:
    """Inställningar för språk och layout (lagras i K33). Förvalen gäller när fältet saknas."""

    sprakprofil: str = "sbk"
    numrerade_rubriker: bool = False
    numrerade_figurer: bool = False
    avsandare: str = STANDARD_AVSANDARE
    diarienummer: str = ""
    handlaggare: str = ""
    accentfarg: str = STANDARD_ACCENT
    logotyp: Resurs | None = None
    typsnitt_brodtext: Resurs | None = None
    typsnitt_rubriker: Resurs | None = None

    def __post_init__(self) -> None:
        if self.sprakprofil not in SPRAKPROFILER:
            raise InstallningsFel(
                f"sprakprofil '{self.sprakprofil}' finns inte. "
                f"Välj {' eller '.join(SPRAKPROFILER)}."
            )
        if not re.fullmatch(r"[0-9A-F]{6}", self.accentfarg):
            raise InstallningsFel(
                f"accentfarg '{self.accentfarg}' ska vara en hexfärg med sex tecken, t.ex. "
                f"'#{STANDARD_ACCENT}'."
            )
        for namn in _TEXTFALT:
            v = getattr(self, namn)
            if len(v) > 200 or "\n" in v:
                raise InstallningsFel(f"{namn} får vara högst 200 tecken på en rad.")

    @property
    def sbk(self) -> bool:
        return self.sprakprofil == "sbk"

    def till_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "sprakprofil": self.sprakprofil,
            "numrerade_rubriker": self.numrerade_rubriker,
            "numrerade_figurer": self.numrerade_figurer,
            "avsandare": self.avsandare,
            "diarienummer": self.diarienummer,
            "handlaggare": self.handlaggare,
            "accentfarg": self.accentfarg,
        }
        for roll in FILROLLER:
            r: Resurs | None = getattr(self, roll)
            d[roll] = None if r is None else r.till_dict()
        return d

    @classmethod
    def fran_dict(cls, d: Mapping[str, Any] | None) -> RapportInstallningar:
        """Läser fältet ur K33. `None` (äldre rapport) ger förvalen."""
        if d is None:
            return cls()
        if not isinstance(d, Mapping):
            raise InstallningsFel("installningar ska vara ett objekt.")
        kw: dict[str, Any] = {}
        if "sprakprofil" in d:
            kw["sprakprofil"] = str(d["sprakprofil"])
        for namn in _BOOLFALT:
            if namn in d:
                kw[namn] = _bool(d[namn], namn)
        for namn in _TEXTFALT:
            if namn in d and d[namn] is not None:
                kw[namn] = str(d[namn]).strip()
        if d.get("accentfarg"):
            kw["accentfarg"] = _hexfarg(str(d["accentfarg"]))
        for roll in FILROLLER:
            if d.get(roll):
                kw[roll] = Resurs.fran_dict(d[roll])
        return cls(**kw)

    def med(self, **andringar: Any) -> RapportInstallningar:
        return replace(self, **andringar)

    def resurser(self) -> dict[str, Resurs]:
        return {roll: r for roll in FILROLLER if (r := getattr(self, roll)) is not None}

    def beskrivning(self) -> str:
        """Kort, utan sökvägar (för händelseloggen)."""
        delar = [f"{k}={v}" for k, v in self.till_dict().items() if k not in FILROLLER]
        for roll, r in self.resurser().items():
            delar.append(f"{roll}=sha256:{r.sha256[:12]}")
        return ", ".join(delar)


def _bool(v: Any, namn: str) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("ja", "true", "1", "sant"):
        return True
    if isinstance(v, str) and v.strip().lower() in ("nej", "false", "0", "falskt"):
        return False
    raise InstallningsFel(f"{namn} ska vara sant eller falskt.")


def _hexfarg(s: str) -> str:
    h = s.strip().lstrip("#").upper()
    if not re.fullmatch(r"[0-9A-F]{6}", h):
        raise InstallningsFel(
            f"accentfarg '{s}' ska vara en hexfärg med sex tecken, t.ex. '#{STANDARD_ACCENT}'."
        )
    return h


def typsnittsfamilj(fil: Path) -> str:
    """Typsnittets familjenamn ur sfnt-filens namntabell (namn-id 16, annars 1).

    Läser tabellen direkt (TTF och OTF har samma behållare), utan extra beroenden."""
    import struct

    try:
        data = fil.read_bytes()
        version, antal = struct.unpack_from(">IH", data, 0)
        if version not in (0x00010000, 0x4F54544F, 0x74727565):  # TrueType, 'OTTO', 'true'
            raise ValueError("inte sfnt")
        start = None
        for i in range(antal):
            tagg, _sum, off, _len = struct.unpack_from(">4sIII", data, 12 + 16 * i)
            if tagg == b"name":
                start = off
        if start is None:
            raise ValueError("ingen namntabell")
        _fmt, n, strang = struct.unpack_from(">HHH", data, start)
        namn: dict[tuple[int, int], str] = {}
        for i in range(n):
            plattform, kod, _sprak, nid, langd, off = struct.unpack_from(
                ">HHHHHH", data, start + 6 + 12 * i
            )
            if nid not in (1, 16):
                continue
            raa = data[start + strang + off : start + strang + off + langd]
            if plattform in (0, 3):
                text = raa.decode("utf-16-be", errors="replace")
            elif plattform == 1 and kod == 0:
                text = raa.decode("mac_roman", errors="replace")
            else:
                continue
            namn.setdefault((nid, 0 if plattform == 3 else 1), text.strip())
    except (OSError, ValueError, struct.error) as exc:
        raise InstallningsFel("Typsnittsfilen kunde inte läsas (inte TTF eller OTF).") from exc
    for nyckel in ((16, 0), (16, 1), (1, 0), (1, 1)):
        if namn.get(nyckel):
            return namn[nyckel]
    raise InstallningsFel("Typsnittsfilen saknar familjenamn.")


def _las_in_fil(
    roll: str,
    sokvag: str | Path,
    resursmapp: Path,
    *,
    tillatna_rotter: Sequence[Path] | None = None,
) -> Resurs:
    """Kontrollerar filen, kopierar den till `resursmapp/<sha256><ändelse>` och ger dess Resurs."""
    vad = FILROLLER[roll]
    fil = Path(str(sokvag)).expanduser().resolve()
    if tillatna_rotter is not None and not any(
        fil.is_relative_to(Path(rot).expanduser().resolve()) for rot in tillatna_rotter
    ):
        raise InstallningsFel(f"Filen för {vad} ligger utanför tillåtna mappar.")
    if not fil.is_file():
        raise InstallningsFel(f"Filen för {vad} finns inte eller kan inte läsas.")
    if fil.stat().st_size > MAX_RESURS_BYTES:
        raise InstallningsFel(
            f"Filen för {vad} är för stor (max {MAX_RESURS_BYTES // (1024 * 1024)} MB)."
        )
    typ = fil.suffix.lower()
    familj: str | None = None
    if roll == "logotyp":
        if typ not in _BILD:
            raise InstallningsFel("Logotypen ska vara en PNG- eller JPEG-fil.")
        from PIL import Image, UnidentifiedImageError

        try:
            with Image.open(fil) as im:
                im.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise InstallningsFel("Logotypen är ingen läsbar bild.") from exc
        typ = ".jpg" if typ == ".jpeg" else typ
    else:
        if typ not in _TYPSNITT:
            raise InstallningsFel(f"Filen för {vad} ska vara TTF eller OTF.")
        familj = typsnittsfamilj(fil)
    sha = sha256_fil(fil)
    resursmapp.mkdir(parents=True, exist_ok=True)
    mal = resursmapp / f"{sha}{typ}"
    if not mal.is_file() or sha256_fil(mal) != sha:
        with tempfile.NamedTemporaryFile(
            dir=resursmapp,
            prefix=f".{sha[:12]}-",
            suffix=f"{typ}.tmp",
            delete=False,
        ) as tf:
            tmp = Path(tf.name)
        try:
            shutil.copyfile(fil, tmp)
            tmp.replace(mal)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    return Resurs(sha, typ, familj)


def tillampa(
    bas: RapportInstallningar,
    andringar: Mapping[str, Any] | None,
    resursmapp: Path,
    *,
    tillatna_rotter: Sequence[Path] | None = None,
) -> RapportInstallningar:
    """Ny uppsättning inställningar: `bas` med `andringar`.

    Filroller (`logotyp`, `typsnitt_brodtext`, `typsnitt_rubriker`) anges som sökväg; tom sträng
    eller None tar bort filen. Okända nycklar vägras.
    """
    if not andringar:
        return bas
    okanda = sorted(set(andringar) - set(NYCKLAR))
    if okanda:
        raise InstallningsFel(
            f"Okänd inställning {', '.join(okanda)}. Tillåtna: {', '.join(NYCKLAR)}."
        )
    kw: dict[str, Any] = {}
    for k, v in andringar.items():
        if k in FILROLLER:
            kw[k] = (
                None
                if v in (None, "")
                else _las_in_fil(k, v, resursmapp, tillatna_rotter=tillatna_rotter)
            )
        elif k in _BOOLFALT:
            kw[k] = _bool(v, k)
        elif k == "accentfarg":
            kw[k] = STANDARD_ACCENT if v in (None, "") else _hexfarg(str(v))
        elif k == "sprakprofil":
            kw[k] = str(v).strip().lower()
        else:
            kw[k] = "" if v is None else str(v).strip()
    return bas.med(**kw)


def lokala_forval(rot: Path) -> dict[str, Any]:
    """`[rapport]` ur `config.toml` och `config.local.toml` i `rot` (tomt om inget finns)."""
    from gemensamt.konfig import KonfigFel, las_konfig

    bas = rot / "config.toml"
    if not bas.is_file():
        return {}
    try:
        konfig = las_konfig(bas, rot / "config.local.toml")
    except KonfigFel as exc:
        raise InstallningsFel("config.local.toml kunde inte läsas.") from exc
    sektion = konfig.get("rapport", {})
    if not isinstance(sektion, Mapping):
        raise InstallningsFel("[rapport] i config.local.toml ska vara en tabell.")
    ut: dict[str, Any] = {}
    for k, v in sektion.items():
        if k not in NYCKLAR:
            raise InstallningsFel(
                f"Okänd inställning '{k}' i [rapport]. Tillåtna: {', '.join(NYCKLAR)}."
            )
        if k in FILROLLER and isinstance(v, str) and v and not Path(v).expanduser().is_absolute():
            v = str(rot / v)
        ut[k] = v
    return ut


def hitta_resurs(resurs: Resurs, resursmapp: Path, forval: Mapping[str, Any]) -> Path | None:
    """Rapportens kopia av filen, eller en fil i de lokala förvalen med samma sha256."""
    kopia = resursmapp / resurs.filnamn
    if kopia.is_file() and sha256_fil(kopia) == resurs.sha256:
        return kopia
    for roll in FILROLLER:
        v = forval.get(roll)
        if isinstance(v, str) and v:
            p = Path(v).expanduser()
            if p.is_file() and sha256_fil(p) == resurs.sha256:
                resursmapp.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, kopia)
                return kopia
    return None


__all__ = [
    "FILROLLER",
    "NYCKLAR",
    "SPRAKPROFILER",
    "STANDARD_ACCENT",
    "STANDARD_AVSANDARE",
    "InstallningsFel",
    "RapportInstallningar",
    "Resurs",
    "hitta_resurs",
    "lokala_forval",
    "tillampa",
    "typsnittsfamilj",
]
