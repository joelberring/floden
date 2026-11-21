"""HTTP-hämtning för OIDC (discovery, JWKS, kodutbyte) bakom ett utbytbart gränssnitt."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any, Protocol

MAX_SVAR_BYTE = 1_000_000


class HamtaFel(Exception):
    """Nätverks- eller protokollfel. Meddelandet innehåller aldrig svarskroppen."""


class Hamtare(Protocol):
    def hamta_json(self, url: str) -> dict[str, Any]: ...

    def skicka_formular(self, url: str, falt: Mapping[str, str]) -> dict[str, Any]: ...


def _kontrollera_https(url: str) -> None:
    if urllib.parse.urlsplit(url).scheme != "https":
        raise HamtaFel("Endast https tillåts mot inloggningsleverantören")


class UrllibHamtare:
    """Standardimplementation med stdlib: https, tidsgräns och storleksgräns."""

    def __init__(self, tidsgrans_s: float = 10.0) -> None:
        self._tidsgrans_s = tidsgrans_s

    def _oppna(self, begaran: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(begaran, timeout=self._tidsgrans_s) as svar:  # noqa: S310
                data = svar.read(MAX_SVAR_BYTE + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HamtaFel(f"Anropet misslyckades ({type(exc).__name__})") from None
        if len(data) > MAX_SVAR_BYTE:
            raise HamtaFel("Svaret är för stort")
        try:
            tolkat = json.loads(data)
        except ValueError:
            raise HamtaFel("Svaret är inte JSON") from None
        if not isinstance(tolkat, dict):
            raise HamtaFel("Svaret är inte ett JSON-objekt")
        return tolkat

    def hamta_json(self, url: str) -> dict[str, Any]:
        _kontrollera_https(url)
        return self._oppna(urllib.request.Request(url, headers={"Accept": "application/json"}))

    def skicka_formular(self, url: str, falt: Mapping[str, str]) -> dict[str, Any]:
        _kontrollera_https(url)
        kropp = urllib.parse.urlencode(dict(falt)).encode("ascii")
        return self._oppna(
            urllib.request.Request(
                url,
                data=kropp,
                method="POST",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
            )
        )
