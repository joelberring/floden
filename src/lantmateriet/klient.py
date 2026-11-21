"""HTTP-klient mot Lantmäteriets API (OAuth2-token, återförsök, nedladdning med återupptagning).

Protokoll och logik porterade från O-Tid (MIT, joelberring/otid @ 4a446af):
`packages/application/src/map-project-lantmateriet.ts` (token, adresser, tillåtna värdar) och
`map-project-laser.ts` (STAC-anrop, nedladdning av laserfil, omdirigeringar).

Skillnader mot O-Tid: ingen databas (nycklarna kommer ur `nycklar.py`), `urllib` i stället för
`fetch`, 429 väntas ut (`Retry-After`), och nedladdningen återupptas med `Range`.

Protokollet är inte provat mot den riktiga tjänsten av O-Tid (det står i dess kod, "provas")
och inte heller här: alla tester går mot en falsk server. Adresserna kan ändras med `Tjanster`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.client import HTTPResponse
from pathlib import Path
from typing import Any

from lantmateriet.nycklar import NyckelFel, Nycklar

logg = logging.getLogger("floden.lantmateriet")

ATTRIBUTION = "Protokoll och logik porterade från O-Tid (MIT, joelberring/otid @ 4a446af)"
MAX_JSON_BYTES = 10 * 1024 * 1024  # som O-Tid (MAX_SEARCH_BYTES)
MAX_OMDIRIGERINGAR = 4  # som O-Tid
MAX_VANTAN_S = 120.0


class LantmaterietFel(Exception):
    """Ett anrop till Lantmäteriet gick inte (nät, format, 5xx)."""


class StrypningsFel(LantmaterietFel):
    """Lantmäteriet svarade 429 även efter att vi väntat."""


@dataclass(frozen=True)
class Tjanster:
    """Adresserna (som O-Tids `OTID_LM_*`); standard är Lantmäteriets."""

    token: str = "https://apimanager.lantmateriet.se/oauth2/token"
    hojd: str = "https://api.lantmateriet.se/stac-hojd/v1"
    bild: str = "https://api.lantmateriet.se/stac-bild/v1"
    vektor: str = "https://api.lantmateriet.se/stac-vektor/v1"

    @classmethod
    def fran_miljo(cls, miljo: Mapping[str, str] | None = None) -> Tjanster:
        """Standardadresser, åsidosatta av `LANTMATERIET_TOKEN_URL`, `LANTMATERIET_STAC_HOJD_URL`,
        `LANTMATERIET_STAC_BILD_URL` och `LANTMATERIET_STAC_VEKTOR_URL` (som O-Tids `OTID_LM_*`)."""
        env = os.environ if miljo is None else miljo
        d = cls()
        return cls(
            token=env.get("LANTMATERIET_TOKEN_URL", d.token).strip() or d.token,
            hojd=env.get("LANTMATERIET_STAC_HOJD_URL", d.hojd).strip().rstrip("/") or d.hojd,
            bild=env.get("LANTMATERIET_STAC_BILD_URL", d.bild).strip().rstrip("/") or d.bild,
            vektor=env.get("LANTMATERIET_STAC_VEKTOR_URL", d.vektor).strip().rstrip("/")
            or d.vektor,
        )

    def varddatorer(self) -> frozenset[str]:
        return frozenset(
            urllib.parse.urlsplit(u).netloc for u in (self.token, self.hojd, self.bild, self.vektor)
        )


STANDARD_TJANSTER = Tjanster()


class _IngenOmdirigering(urllib.request.HTTPRedirectHandler):
    """Omdirigeringar hanteras för hand så att nycklar bara följer med till tillåtna värdar."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_IngenOmdirigering)


@dataclass(frozen=True)
class Svar:
    status: int
    rubriker: dict[str, str]
    kropp: bytes


class LmKlient:
    """Inloggad klient. `sov` och `nu` kan bytas i tester."""

    def __init__(
        self,
        nycklar: Nycklar,
        tjanster: Tjanster = STANDARD_TJANSTER,
        *,
        sov: Callable[[float], None] = time.sleep,
        nu: Callable[[], float] = time.monotonic,
        max_forsok: int = 4,
        timeout_s: float = 30.0,
    ) -> None:
        self._nycklar = nycklar
        self.tjanster = tjanster
        self._sov = sov
        self._nu = nu
        self._max_forsok = max_forsok
        self._timeout_s = timeout_s
        self._token: tuple[str, float] | None = None
        self._tillatna = tjanster.varddatorer()

    # -- adresser -------------------------------------------------------------------------

    def tillaten_adress(self, url: str) -> bool:
        """Nycklar skickas bara till Lantmäteriets värdar (eller de konfigurerade, i tester)."""
        d = urllib.parse.urlsplit(url)
        if d.username or d.password or d.scheme not in ("https", "http"):
            return False
        host = d.hostname or ""
        if d.netloc in self._tillatna:
            return True
        return d.scheme == "https" and (
            host == "lantmateriet.se" or host.endswith(".lantmateriet.se")
        )

    # -- inloggning -----------------------------------------------------------------------

    def _auktorisering(self) -> str:
        if self._nycklar.metod == "basic":
            return self._nycklar.basic_rubrik()
        if self._token is not None and self._token[1] > self._nu() + 30.0:
            return "Bearer " + self._token[0]
        svar = self._skicka(
            "POST",
            self.tjanster.token,
            {
                "Authorization": self._nycklar.basic_rubrik(),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            b"grant_type=client_credentials",
            med_nycklar=False,
        )
        try:
            data = json.loads(svar.kropp.decode("utf-8"))
            token = data["access_token"]
            if not isinstance(token, str) or not token:
                raise KeyError("access_token")
            sek = data.get("expires_in")
            giltig = float(sek) if isinstance(sek, int | float) and sek > 0 else 3600.0
        except (ValueError, KeyError, TypeError) as exc:
            raise LantmaterietFel("Tokensvaret från Lantmäteriet saknar access_token.") from exc
        self._token = (token, self._nu() + giltig)
        return "Bearer " + token

    def _glom_token(self) -> None:
        self._token = None

    # -- grundanrop -----------------------------------------------------------------------

    def _skicka(
        self,
        metod: str,
        url: str,
        rubriker: dict[str, str],
        kropp: bytes | None = None,
        *,
        med_nycklar: bool = True,
    ) -> Svar:
        """Ett anrop med väntan vid 429/5xx och nytt token vid 401; läser hela (korta) svaret."""
        if not self.tillaten_adress(url) and med_nycklar:
            raise LantmaterietFel(
                f"Adressen är inte Lantmäteriets och nås inte med nycklar: {_kort(url)}"
            )
        fornyat = False
        for forsok in range(self._max_forsok + 1):
            h = dict(rubriker)
            if med_nycklar:
                h["Authorization"] = self._auktorisering()
            try:
                resp = self._oppna(metod, url, h, kropp)
            except urllib.error.URLError as exc:
                if forsok < self._max_forsok:
                    self._sov(min(2.0**forsok, MAX_VANTAN_S))
                    continue
                raise LantmaterietFel(f"Nätverksfel mot {_kort(url)}: {exc.reason}") from None
            try:
                status = resp.status
                svarh = {k.lower(): v for k, v in resp.getheaders()}
                längd = int(svarh.get("content-length", "0") or 0)
                if längd > MAX_JSON_BYTES:
                    raise LantmaterietFel(f"Svaret från {_kort(url)} är för stort ({längd} byte).")
                data = resp.read(MAX_JSON_BYTES + 1)
            finally:
                resp.close()
            if len(data) > MAX_JSON_BYTES:
                raise LantmaterietFel(f"Svaret från {_kort(url)} är för stort.")
            if status == 401 and med_nycklar and not fornyat and self._nycklar.metod == "oauth2":
                self._glom_token()
                fornyat = True
                continue
            if status in (401, 403):
                raise NyckelFel(_nyckelmeddelande(status))
            if status == 429 or status in (502, 503, 504):
                if forsok < self._max_forsok:
                    self._sov(_vantetid(svarh.get("retry-after"), forsok))
                    continue
                if status == 429:
                    raise StrypningsFel(
                        "Lantmäteriet strypte anropen (429) även efter väntan. Vänta en stund och "
                        "kör kommandot igen; redan hämtade filer hoppas över."
                    )
                raise LantmaterietFel(f"Lantmäteriet svarade {status} för {_kort(url)}.")
            if status >= 400:
                raise LantmaterietFel(f"Lantmäteriet svarade {status} för {_kort(url)}.")
            return Svar(status, svarh, data)
        raise LantmaterietFel("Oväntat läge i anropsloopen.")  # pragma: no cover

    def _oppna(
        self, metod: str, url: str, rubriker: dict[str, str], kropp: bytes | None
    ) -> HTTPResponse:
        req = urllib.request.Request(url, data=kropp, headers=rubriker, method=metod)
        req.add_header("User-Agent", "floden-lantmateriet/0.1")
        try:
            return _OPENER.open(req, timeout=self._timeout_s)  # type: ignore[no-any-return]
        except urllib.error.HTTPError as exc:
            return exc  # type: ignore[return-value]  # HTTPError är ett svar med status

    # -- JSON -----------------------------------------------------------------------------

    def hamta_json(self, url: str) -> dict[str, Any]:
        svar = self._skicka("GET", url, {"Accept": "application/geo+json, application/json"})
        return _json(svar, url)

    def post_json(self, url: str, kropp: dict[str, Any]) -> dict[str, Any]:
        svar = self._skicka(
            "POST",
            url,
            {
                "Accept": "application/geo+json, application/json",
                "Content-Type": "application/json",
            },
            json.dumps(kropp).encode("utf-8"),
        )
        return _json(svar, url)

    # -- nedladdning ----------------------------------------------------------------------

    def hamta_fil(
        self, url: str, mal: Path, *, forvantad_storlek: int | None = None
    ) -> tuple[str, int]:
        """Hämtar `url` till `mal` (via `mal.part`, återupptas med Range).

        Returnerar (sha256, antal byte).

        Omdirigeringar följs för hand (högst 4). Nycklar skickas bara till tillåtna värdar; en
        omdirigering till en annan https-värd (t.ex. en signerad lagringsadress) följs utan nycklar.
        """
        mal.parent.mkdir(parents=True, exist_ok=True)
        delfil = mal.with_name(mal.name + ".part")
        for forsok in range(self._max_forsok + 1):
            start = delfil.stat().st_size if delfil.exists() else 0
            try:
                resultat = self._ladda_en_gang(url, delfil, start)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if forsok < self._max_forsok:
                    self._sov(min(2.0**forsok, MAX_VANTAN_S))
                    continue
                raise LantmaterietFel(f"Nedladdningen av {_kort(url)} avbröts: {exc}") from None
            if resultat == "klar":
                break
            if resultat == "ny":
                continue
            if resultat == "vanta":
                if forsok < self._max_forsok:
                    continue
                raise StrypningsFel(
                    "Lantmäteriet strypte nedladdningen (429). Kör kommandot igen om en stund; "
                    "delvis hämtade filer återupptas."
                )
        else:
            raise LantmaterietFel(f"Nedladdningen av {_kort(url)} blev inte klar.")
        storlek = delfil.stat().st_size
        if forvantad_storlek is not None and storlek != forvantad_storlek:
            logg.warning(
                "Storleken %d avviker från STAC:s file:size %d för %s",
                storlek,
                forvantad_storlek,
                _kort(url),
            )
        h = hashlib.sha256()
        with delfil.open("rb") as fh:
            while block := fh.read(1024 * 1024):
                h.update(block)
        os.replace(delfil, mal)
        return h.hexdigest(), storlek

    def _ladda_en_gang(self, url: str, delfil: Path, start: int) -> str:
        aktuell = url
        med_nycklar = True
        fornyat = False
        for _ in range(MAX_OMDIRIGERINGAR + 2):
            if med_nycklar and not self.tillaten_adress(aktuell):
                raise LantmaterietFel(
                    f"Adressen är inte Lantmäteriets och nås inte med nycklar: {_kort(aktuell)}"
                )
            h: dict[str, str] = {}
            if med_nycklar:
                h["Authorization"] = self._auktorisering()
            if start > 0:
                h["Range"] = f"bytes={start}-"
            resp = self._oppna("GET", aktuell, h, None)
            try:
                status = resp.status
                svarh = {k.lower(): v for k, v in resp.getheaders()}
                if 300 <= status < 400:
                    plats = svarh.get("location")
                    if not plats:
                        raise LantmaterietFel("Omdirigering utan adress.")
                    aktuell = urllib.parse.urljoin(aktuell, plats)
                    if not self.tillaten_adress(aktuell):
                        if urllib.parse.urlsplit(aktuell).scheme != "https":
                            raise LantmaterietFel("Omdirigering till en adress utan https nekas.")
                        med_nycklar = False
                    continue
                if (
                    status == 401
                    and med_nycklar
                    and self._nycklar.metod == "oauth2"
                    and not fornyat
                ):
                    self._glom_token()
                    fornyat = True
                    continue
                if status in (401, 403):
                    raise NyckelFel(_nyckelmeddelande(status))
                if status == 429 or status in (502, 503, 504):
                    self._sov(_vantetid(svarh.get("retry-after"), 0))
                    return "vanta"
                if status == 416 and start > 0:
                    delfil.unlink(missing_ok=True)  # delfilen räckte inte som start; börja om
                    return "ny"
                if status not in (200, 206):
                    raise LantmaterietFel(f"Lantmäteriet svarade {status} för {_kort(aktuell)}.")
                lage = "ab"
                if status == 200:
                    lage = "wb"  # servern ignorerade Range: börja om
                elif not svarh.get("content-range", "").startswith(f"bytes {start}-"):
                    raise LantmaterietFel("Oväntat Content-Range vid återupptagning.")
                with delfil.open(lage) as ut:
                    while block := resp.read(1024 * 1024):
                        ut.write(block)
                return "klar"
            finally:
                resp.close()
        raise LantmaterietFel("För många omdirigeringar.")


def _json(svar: Svar, url: str) -> dict[str, Any]:
    try:
        data = json.loads(svar.kropp.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LantmaterietFel(f"Svaret från {_kort(url)} är inte JSON.") from exc
    if not isinstance(data, dict):
        raise LantmaterietFel(f"Svaret från {_kort(url)} är inte ett JSON-objekt.")
    return data


def _kort(url: str) -> str:
    """Adress utan fråga och användaruppgifter, för meddelanden och loggar."""
    d = urllib.parse.urlsplit(url)
    port = f":{d.port}" if d.port else ""
    return f"{d.scheme}://{d.hostname}{port}{d.path}"


def _vantetid(retry_after: str | None, forsok: int) -> float:
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), MAX_VANTAN_S))
        except ValueError:
            pass
    return min(2.0 ** (forsok + 1), MAX_VANTAN_S)


def _nyckelmeddelande(status: int) -> str:
    return (
        f"Lantmäteriet sa nej ({status}). Antingen stämmer inte nycklarna, eller så är produkten "
        "inte beställd för kontot på Geotorget (Laserdata Nedladdning skog, Ortofoto Nedladdning, "
        "Byggnad): Lantmäteriet svarar likadant i båda fallen. Kontrollera båda."
    )
