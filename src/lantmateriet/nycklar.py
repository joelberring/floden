"""Nycklar till Lantmäteriets API: bara ur miljön eller `config.local.toml`, aldrig i repot.

Protokoll och logik porterade från O-Tid (MIT, joelberring/otid @ 4a446af):
`packages/application/src/map-project-lantmateriet.ts` och `map-source-credentials.ts`.

Två metoder, som i O-Tid:

- `oauth2` (förval): API-portalens nyckel och hemlighet (`apimanager.lantmateriet.se`), OAuth2
  client credentials. Organisationens systemkonto.
- `basic`: Geotorgets användarnamn och lösenord (privatperson), skickas som HTTP Basic.

Miljövariabler (vinner över filen): `LANTMATERIET_KLIENT_ID`, `LANTMATERIET_KLIENT_HEMLIGHET`,
`LANTMATERIET_METOD`. Fil: `[lantmateriet]` med `klient_id`, `klient_hemlighet`, `metod` i
`config.local.toml` (ignorerad av git). Nycklarna skrivs aldrig i loggar, manifest eller
felmeddelanden: `Nycklar` har en `repr` som döljer dem.

Med `[konto] inloggning = "pa"` (servern) gäller i stället **användarens egen nyckel**, den som hen
lagt in på sidan Nycklar (`konto.nycklar`, krypterad i kontodatabasen): `las_nycklar_for_konto`.
Servernyckeln i miljön eller `config.local.toml` används då aldrig, så att en användares hämtning
inte går på någon annans kvot. Med inloggningen av (lokalt) gäller allt ovan som förut.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from gemensamt.konfig import KonfigFel, las_konfig

METODER = ("oauth2", "basic")
_REPO_ROT = Path(__file__).resolve().parents[2]


class NyckelFel(Exception):
    """Nycklar saknas eller Lantmäteriet godtog dem inte (401/403)."""


@dataclass(frozen=True)
class Nycklar:
    """Inloggningsuppgifter till Lantmäteriet; visas aldrig i text (`Nycklar(metod=...)`)."""

    id: str = field(repr=False)
    hemlighet: str = field(repr=False)
    metod: str = "oauth2"

    def __post_init__(self) -> None:
        if self.metod not in METODER:
            raise NyckelFel(f"Okänd metod '{self.metod}', välj {' eller '.join(METODER)}.")
        if not self.id.strip() or not self.hemlighet.strip():
            raise NyckelFel("Nyckel och hemlighet får inte vara tomma.")

    def __repr__(self) -> str:
        return f"Nycklar(metod={self.metod!r}, id=<dold>, hemlighet=<dold>)"

    __str__ = __repr__

    def basic_rubrik(self) -> str:
        """Värdet till `Authorization: Basic ...` (används för token-anropet eller direkt)."""
        rå = f"{self.id}:{self.hemlighet}".encode()
        return "Basic " + base64.b64encode(rå).decode("ascii")

    def hemligheter(self) -> tuple[str, ...]:
        """Strängar som aldrig får förekomma i loggar eller filer (till tester och rensning)."""
        return (self.id, self.hemlighet, self.basic_rubrik().removeprefix("Basic "))


def _lokal_konfig_och_rot() -> tuple[dict[str, Any], Path | None]:
    for rot in (Path.cwd(), _REPO_ROT):
        if (rot / "config.local.toml").is_file() or (rot / "config.toml").is_file():
            try:
                return las_konfig(rot / "config.toml", rot / "config.local.toml"), rot
            except KonfigFel as exc:
                raise NyckelFel(str(exc)) from exc
    return {}, None


def _lokal_konfig() -> dict[str, Any]:
    """`config.toml` + `config.local.toml` från arbetsmappen, annars från repots rot."""
    return _lokal_konfig_och_rot()[0]


def las_nycklar(
    konfig: Mapping[str, Any] | None = None,
    miljo: Mapping[str, str] | None = None,
) -> Nycklar:
    """Nycklar ur miljön, annars `[lantmateriet]` i konfigurationen (saknas de: `NyckelFel`)."""
    env = os.environ if miljo is None else miljo
    avsnitt: Mapping[str, Any] = {}
    kfg = _lokal_konfig() if konfig is None else konfig
    rå = kfg.get("lantmateriet", {})
    if isinstance(rå, Mapping):
        avsnitt = rå
    klient_id = env.get("LANTMATERIET_KLIENT_ID") or str(avsnitt.get("klient_id", "") or "")
    hemlighet = env.get("LANTMATERIET_KLIENT_HEMLIGHET") or str(
        avsnitt.get("klient_hemlighet", "") or ""
    )
    metod = (env.get("LANTMATERIET_METOD") or str(avsnitt.get("metod", "") or "oauth2")).lower()
    if not klient_id.strip() or not hemlighet.strip():
        raise NyckelFel(
            "Nycklar till Lantmäteriet saknas. Lägg dem i miljön "
            "(LANTMATERIET_KLIENT_ID, LANTMATERIET_KLIENT_HEMLIGHET) eller under [lantmateriet] "
            "(klient_id, klient_hemlighet) i config.local.toml. Se docs/DATA.md, avsnittet "
            "'Hämta öppna data från Lantmäteriet'."
        )
    return Nycklar(id=klient_id.strip(), hemlighet=hemlighet.strip(), metod=metod)


class NyckelKalla(Protocol):
    """Det `konto.nycklar.NyckelForrad` ger: kontots sparade nyckel som klartext."""

    def las(self, konto_id: str, typ_id: str) -> dict[str, str] | None: ...


def inloggning_pa(konfig: Mapping[str, Any]) -> bool:
    """Är `[konto] inloggning = "pa"`? (Ingen import av kontosystemet när den är av.)"""
    konto = konfig.get("konto", {})
    return isinstance(konto, Mapping) and konto.get("inloggning") == "pa"


def _oppna_forrad(
    konfig: Mapping[str, Any], rot: Path | None, miljo: Mapping[str, str] | None
) -> NyckelKalla:
    from dataclasses import replace

    from konto import Databas, Register, las_konto_konfig
    from konto.nycklar import NyckelForrad

    try:
        konto_konf = las_konto_konfig(konfig, miljo)
    except Exception as exc:  # noqa: BLE001 - KontoKonfigFel; texten har inga hemligheter
        raise NyckelFel(f"Kontokonfigurationen går inte att läsa: {exc}") from exc
    sokvag = konto_konf.databas
    if not sokvag.is_absolute() and rot is not None:
        konto_konf = replace(konto_konf, databas=rot / sokvag)
    return NyckelForrad(Register(Databas(konto_konf.databas)), miljo=miljo)


def las_nycklar_for_konto(
    konto_id: str | None = None,
    *,
    konfig: Mapping[str, Any] | None = None,
    miljo: Mapping[str, str] | None = None,
    forrad: NyckelKalla | None = None,
) -> Nycklar:
    """Nycklarna för en hämtning. Inloggning av: `las_nycklar` (miljön eller `config.local.toml`).
    Inloggning på: kontots egen sparade Lantmäteri-nyckel och ingen annan; saknas den eller
    kontot, eller är nyckelhanteringen avstängd på servern, blir det ett `NyckelFel` utan
    nyckelvärden i texten."""
    rot: Path | None = None
    kfg: Mapping[str, Any]
    if konfig is None:
        kfg, rot = _lokal_konfig_och_rot()
    else:
        kfg = konfig
    if not inloggning_pa(kfg):
        return las_nycklar(kfg, miljo)
    if not konto_id:
        raise NyckelFel(
            "Inloggning är på: ange vilket konto som hämtar (--konto <konto-id>). "
            "Då används kontots egen nyckel (sidan Nycklar), aldrig servernyckeln."
        )
    kalla = forrad if forrad is not None else _oppna_forrad(kfg, rot, miljo)
    try:
        varden = kalla.las(konto_id, "lantmateriet")
    except Exception as exc:  # noqa: BLE001 - NyckelForradFel: texten är fri från nyckelvärden
        raise NyckelFel(str(getattr(exc, "text", "Nyckeln går inte att läsa."))) from exc
    if varden is None:
        raise NyckelFel(
            "Kontot har ingen Lantmäteri-nyckel. Lägg in den på sidan Nycklar i webben."
        )
    return Nycklar(
        id=varden.get("klient_id", ""),
        hemlighet=varden.get("klient_hemlighet", ""),
        metod=varden.get("metod", "oauth2"),
    )
