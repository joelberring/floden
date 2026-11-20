"""Automatisk talkontroll, kartkontroll och slutsatskontroll för rapporter (T08)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from rapport.underlag import RapportUnderlag

# Matchar heltal eller decimaltal (med punkt eller komma), t.ex. 0,87, -12.4, 500
_TAL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?\d+(?:[.,]\d+)?(?![A-Za-z0-9_])"
)
_MD_BILD_RE: Final[re.Pattern[str]] = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_KARTA_REF_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)^\s*Karta\s*:\s*([^\s,;\)]+)"
)

_TILLATNA_FASTA_BETECKNINGAR: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bEPSG\s*:\s*3011\b", re.IGNORECASE),
    re.compile(r"\bSWEREF\s*99\s*18\s*00\b", re.IGNORECASE),
    re.compile(r"\bRH\s*2000\b", re.IGNORECASE),
    re.compile(r"\bK[0-8]/v\d+\b", re.IGNORECASE),
    re.compile(r"\bP(?:10|50|90)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:19|20)\d{2}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?\b"
    ),
    re.compile(r"\b(?=[0-9a-f]*[a-f])[0-9a-f]{7,64}\b", re.IGNORECASE),  # git-SHA / sha256-hex
)

_RUBRIK_NUMMER_RE: Final[re.Pattern[str]] = re.compile(r"(?m)^(#{1,6}\s+)\d+(?:\.\d+)*\.?\s+")
_KODSPAN_RE: Final[re.Pattern[str]] = re.compile(r"`[^`\n]+`")
_AR_I_START_RE: Final[re.Pattern[str]] = re.compile(r"^((?:19|20)\d{2})\b")

_POSITIVA_GRANS_PASTAENDEN: Final[tuple[str, ...]] = (
    "alla gränsvärden klaras",
    "samtliga gränsvärden klaras",
    "alla gränsvärden uppfylls",
    "samtliga gränsvärden uppfylls",
    "inga överskridanden",
    "inget gränsvärde överskrids",
    "inga gränsvärden överskrids",
    "alla kontroller är godkända",
    "samtliga kontroller är godkända",
)

_NEGATIVA_GRANS_PASTAENDEN: Final[tuple[str, ...]] = (
    "gränsvärde överskrids",
    "gränsvärden överskrids",
    "inte godkänd",
    "ej godkänd",
    "klaras inte",
    "uppfylls inte",
)


class RapportKontrollFel(Exception):
    """Kastas när talkontroll, kartkontroll eller slutsatskontroll misslyckas."""

    def __init__(self, avvikelser: list[str]) -> None:
        self.avvikelser = list(avvikelser)
        detalj = "\n- ".join(self.avvikelser)
        super().__init__(f"Rapportkontrollen underkände rapporten:\n- {detalj}")


@dataclass(frozen=True)
class KontrollResultat:
    """Sammanfattning av genomförd automatisk rapportkontroll."""

    godkand: bool
    granskade_tal: list[str] = field(default_factory=list)
    granskade_kartor: list[str] = field(default_factory=list)
    avvikelser: list[str] = field(default_factory=list)


def _samla_godkanda_tal(underlag: RapportUnderlag) -> list[float]:
    godkanda: list[float] = []

    def _lagg_till(varde: object) -> None:
        if isinstance(varde, int | float) and not isinstance(varde, bool):
            fv = float(varde)
            if math.isfinite(fv):
                godkanda.append(fv)

    for varde in underlag.tal.values():
        _lagg_till(varde)

    for tabell in underlag.tabeller:
        for rad in tabell.rader:
            for cell in rad:
                _lagg_till(cell)

    for gv in underlag.gransvarden:
        _lagg_till(gv.uppmatt)
        _lagg_till(gv.grans)

    for varde in underlag.manifest.matvarden.values():
        _lagg_till(varde)

    return godkanda


def _ar_tillatet_artal_fran_start(token: str, underlag: RapportUnderlag) -> bool:
    """Tillåter exakt 4-siffrigt årtal hämtat ur `manifest.start`."""
    match = _AR_I_START_RE.match(underlag.manifest.start.strip())
    if match is None:
        return False
    return token == match.group(1)


def _rensa_vitlistade_segment(text: str, underlag: RapportUnderlag) -> str:
    rensad = _RUBRIK_NUMMER_RE.sub(r"\1", text)

    def _rensa_kodspan(match: re.Match[str]) -> str:
        span = match.group(0)
        inre = span[1:-1].strip()
        if inre in (underlag.korning_id, underlag.manifest.kodversion, underlag.manifest.kontrakt):
            return " "
        return span

    rensad = _KODSPAN_RE.sub(_rensa_kodspan, rensad)
    if underlag.korning_id:
        rensad = rensad.replace(underlag.korning_id, " ")
    if underlag.manifest.kodversion and underlag.manifest.kodversion != "okand":
        rensad = rensad.replace(underlag.manifest.kodversion, " ")
    if underlag.manifest.start:
        rensad = rensad.replace(underlag.manifest.start, " ")

    for monster in _TILLATNA_FASTA_BETECKNINGAR:
        rensad = monster.sub(" ", rensad)
    return rensad


def _tolerans_for_taltext(taltext: str) -> float:
    normaliserad = taltext.replace(",", ".")
    if "." in normaliserad:
        decimaler = len(normaliserad.split(".", 1)[1])
        return 0.5 * (10.0 ** (-decimaler)) + 1e-9
    return 1e-9


def _matchar_godkant_tal(taltext: str, godkanda_tal: list[float]) -> bool:
    normaliserad = taltext.replace(",", ".")
    try:
        varde = float(normaliserad)
    except ValueError:
        return False
    tol = _tolerans_for_taltext(taltext)
    return any(abs(varde - godkant) <= tol for godkant in godkanda_tal)


def _kontrollera_tal(text: str, underlag: RapportUnderlag) -> tuple[list[str], list[str]]:
    rensad = _rensa_vitlistade_segment(text, underlag)
    godkanda = _samla_godkanda_tal(underlag)
    granskade: list[str] = []
    avvikelser: list[str] = []

    for match in _TAL_RE.finditer(rensad):
        taltext = match.group(0)
        granskade.append(taltext)
        if _matchar_godkant_tal(taltext, godkanda):
            continue
        if _ar_tillatet_artal_fran_start(taltext, underlag):
            continue
        avvikelser.append(
            f"Otillåtet eller okänt tal '{taltext}' i rapporttexten "
            "(saknas i underlag.tal, tabeller, gränsvärden och manifest.matvarden)."
        )
    return granskade, avvikelser


def _kontrollera_kartor(
    text: str,
    underlag: RapportUnderlag,
    *,
    kart_rot: str | Path | None = None,
) -> tuple[list[str], list[str]]:
    deklarerade_filer = {karta.fil: karta for karta in underlag.kartor}
    deklarerade_ider = {karta.kart_id: karta for karta in underlag.kartor}
    namnda_referenser: list[str] = []
    avvikelser: list[str] = []

    for match in _MD_BILD_RE.finditer(text):
        namnda_referenser.append(match.group(1).strip())
    for match in _KARTA_REF_RE.finditer(text):
        namnda_referenser.append(match.group(1).strip())

    rot_path = Path(kart_rot) if kart_rot is not None else None
    granskade: list[str] = []
    for ref in namnda_referenser:
        granskade.append(ref)
        karta = deklarerade_filer.get(ref) or deklarerade_ider.get(ref)
        if karta is None:
            avvikelser.append(
                f"Kartreferens '{ref}' i rapporttexten är inte deklarerad i underlag.kartor."
            )
            continue
        fil_path = Path(karta.fil)
        if not fil_path.is_absolute() and rot_path is not None:
            fil_path = rot_path / fil_path
        if not fil_path.is_file():
            avvikelser.append(
                f"Kartfil saknas på disk för '{karta.kart_id}': {fil_path}"
            )

    return granskade, avvikelser


def _kontrollera_slutsatser(text: str, underlag: RapportUnderlag) -> list[str]:
    if not underlag.gransvarden:
        return []

    gemen_text = text.lower()
    har_overskridande = any(not gv.godkand for gv in underlag.gransvarden)
    alla_godkanda = all(gv.godkand for gv in underlag.gransvarden)
    avvikelser: list[str] = []

    if har_overskridande:
        for fras in _POSITIVA_GRANS_PASTAENDEN:
            if fras in gemen_text:
                avvikelser.append(
                    f"Motstridig slutsats: texten påstår '{fras}' trots att "
                    "minst ett gränsvärde i underlag.gransvarden har godkand=False."
                )

    if alla_godkanda:
        for fras in _NEGATIVA_GRANS_PASTAENDEN:
            if fras in gemen_text:
                avvikelser.append(
                    f"Motstridig slutsats: texten påstår '{fras}' trots att "
                    "samtliga gränsvärden i underlag.gransvarden har godkand=True."
                )

    return avvikelser


def kontrollera_rapport(
    text: str,
    underlag: RapportUnderlag,
    *,
    kart_rot: str | Path | None = None,
) -> KontrollResultat:
    """Kontrollerar tal, kartreferenser och slutsatser mot rapportunderlaget.

    Kastar `RapportKontrollFel` vid minsta avvikelse.
    """
    if not isinstance(text, str) or not text.strip():
        raise RapportKontrollFel(["Rapporttexten är tom."])

    granskade_tal, tal_avvikelser = _kontrollera_tal(text, underlag)
    granskade_kartor, kart_avvikelser = _kontrollera_kartor(
        text, underlag, kart_rot=kart_rot
    )
    slutsats_avvikelser = _kontrollera_slutsatser(text, underlag)

    avvikelser = [*tal_avvikelser, *kart_avvikelser, *slutsats_avvikelser]
    if avvikelser:
        raise RapportKontrollFel(avvikelser)

    return KontrollResultat(
        godkand=True,
        granskade_tal=granskade_tal,
        granskade_kartor=granskade_kartor,
        avvikelser=[],
    )
