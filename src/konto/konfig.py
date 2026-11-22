"""Konfiguration för kontosystemet (`[konto]` i `config.toml` / `config.local.toml`).

Logik porterad från O-Tid (MIT, joelberring/otid @ 4a446af), avskalad: inga Eventor-, tävlings-
eller klubbinställningar. Hemligheter läses ur miljön (`FLODEN_KONTO_<LEVERANTÖR>_KLIENT_HEMLIGHET`)
eller `config.local.toml`, aldrig ur `config.toml` i git.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

INLOGGNING_AV = "av"
INLOGGNING_PA = "pa"
_LOKALA_VARDAR = frozenset({"localhost", "127.0.0.1", "::1"})


class KontoKonfigFel(Exception):
    """Ogiltig eller ofullständig kontokonfiguration."""


@dataclass(frozen=True)
class LeverantorInst:
    """En leverantör som den står i konfigurationen (innan den byggs till en `Leverantor`)."""

    namn: str
    typ: str
    aktiv: bool
    klient_id: str
    hemlighet: str = field(repr=False)
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KontoKonfig:
    """Kontosystemets inställningar. `inloggning = "av"` är standard (lokal körning, tester)."""

    inloggning: str = INLOGGNING_AV
    databas: Path = Path("data/konto/konto.sqlite")
    offentlig_url: str = "http://localhost:8765"
    cookie_secure: bool = True
    sessionstimmar: float = 12.0
    nya_konton: str = "inbjudan"
    oppna_sokvagar: tuple[str, ...] = ("/api/halsa",)
    leverantorer: Mapping[str, LeverantorInst] = field(default_factory=dict)

    @property
    def aktiv(self) -> bool:
        return self.inloggning == INLOGGNING_PA

    @property
    def origin(self) -> str:
        delar = urlsplit(self.offentlig_url)
        return f"{delar.scheme}://{delar.netloc}"

    def redirect_uri(self, leverantor: str) -> str:
        """Exakt den adress som ska stå i leverantörens klientregistrering."""
        return f"{self.origin}/konto/callback/{leverantor}"


def _env_namn(leverantor: str, falt: str) -> str:
    return f"FLODEN_KONTO_{leverantor.upper().replace('-', '_')}_{falt}"


def _text(tabell: Mapping[str, Any], nyckel: str, standard: str) -> str:
    varde = tabell.get(nyckel, standard)
    if not isinstance(varde, str):
        raise KontoKonfigFel(f"[konto].{nyckel} måste vara text")
    return varde


def las_konto_konfig(
    konfig: Mapping[str, Any] | None, miljo: Mapping[str, str] | None = None
) -> KontoKonfig:
    """Bygger `KontoKonfig` ur den sammanslagna konfigurationen (`gemensamt.konfig.las_konfig`).

    Saknas `[konto]` är inloggningen av. Vid `inloggning = "pa"` valideras allt strikt så att
    servern hellre vägrar starta än kör med en halv inloggning.
    """
    miljo = os.environ if miljo is None else miljo
    tabell = (konfig or {}).get("konto", {})
    if not isinstance(tabell, Mapping):
        raise KontoKonfigFel("[konto] måste vara en tabell")
    inloggning = _text(tabell, "inloggning", INLOGGNING_AV)
    if inloggning not in (INLOGGNING_AV, INLOGGNING_PA):
        raise KontoKonfigFel('[konto].inloggning måste vara "av" eller "pa"')
    nya_konton = _text(tabell, "nya_konton", "inbjudan")
    if nya_konton not in ("inbjudan", "oppen"):
        raise KontoKonfigFel('[konto].nya_konton måste vara "inbjudan" eller "oppen"')
    timmar = tabell.get("sessionstimmar", 12.0)
    if isinstance(timmar, bool) or not isinstance(timmar, int | float) or not 0 < timmar <= 720:
        raise KontoKonfigFel("[konto].sessionstimmar måste vara mellan 0 och 720")
    secure = tabell.get("cookie_secure", True)
    if not isinstance(secure, bool):
        raise KontoKonfigFel("[konto].cookie_secure måste vara sant eller falskt")
    oppna = tabell.get("oppna_sokvagar", ["/api/halsa"])
    if not isinstance(oppna, list) or not all(isinstance(s, str) for s in oppna):
        raise KontoKonfigFel("[konto].oppna_sokvagar måste vara en lista med text")

    leverantorer: dict[str, LeverantorInst] = {}
    rad = tabell.get("leverantorer", {})
    if not isinstance(rad, Mapping):
        raise KontoKonfigFel("[konto.leverantorer] måste vara en tabell")
    for namn, inst in rad.items():
        if not isinstance(inst, Mapping) or not isinstance(namn, str):
            raise KontoKonfigFel(f"[konto.leverantorer.{namn}] måste vara en tabell")
        if not namn.replace("-", "").replace("_", "").isalnum() or namn != namn.lower():
            raise KontoKonfigFel(f"Leverantörsnamnet {namn!r} får bara ha a-z, 0-9, - och _")
        extra = {
            k: v for k, v in inst.items() if k not in ("aktiv", "klient_id", "klient_hemlighet")
        }
        leverantorer[namn] = LeverantorInst(
            namn=namn,
            typ=str(inst.get("typ", namn if namn in ("google", "entra") else "oidc")),
            aktiv=inst.get("aktiv", False) is True,
            klient_id=(
                miljo.get(_env_namn(namn, "KLIENT_ID")) or str(inst.get("klient_id", ""))
            ),
            hemlighet=(
                miljo.get(_env_namn(namn, "KLIENT_HEMLIGHET"))
                or str(inst.get("klient_hemlighet", ""))
            ),
            extra=extra,
        )

    resultat = KontoKonfig(
        inloggning=inloggning,
        databas=Path(_text(tabell, "databas", "data/konto/konto.sqlite")),
        offentlig_url=_text(tabell, "offentlig_url", "http://localhost:8765").rstrip("/"),
        cookie_secure=secure,
        sessionstimmar=float(timmar),
        nya_konton=nya_konton,
        oppna_sokvagar=tuple(oppna),
        leverantorer=leverantorer,
    )
    if resultat.aktiv:
        _validera_aktiv(resultat)
    return resultat


def _validera_aktiv(konf: KontoKonfig) -> None:
    delar = urlsplit(konf.offentlig_url)
    lokal = (delar.hostname or "") in _LOKALA_VARDAR
    if delar.scheme not in ("http", "https") or not delar.netloc or delar.path not in ("", "/"):
        raise KontoKonfigFel("[konto].offentlig_url måste vara en adress utan sökväg")
    if delar.scheme == "http" and not lokal:
        raise KontoKonfigFel("[konto].offentlig_url måste vara https utom på localhost")
    if not konf.cookie_secure and not lokal:
        raise KontoKonfigFel("[konto].cookie_secure = false är bara tillåtet på localhost")
    aktiva = [lev for lev in konf.leverantorer.values() if lev.aktiv]
    if not aktiva:
        raise KontoKonfigFel("Inloggning är på men ingen leverantör är aktiv")
    for lev in aktiva:
        if not lev.klient_id or not lev.hemlighet:
            raise KontoKonfigFel(
                f"Leverantören {lev.namn} saknar klient_id eller hemlighet "
                f"(sätt {_env_namn(lev.namn, 'KLIENT_ID')} och "
                f"{_env_namn(lev.namn, 'KLIENT_HEMLIGHET')} eller config.local.toml)"
            )
