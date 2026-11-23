# ruff: noqa: E501
"""Generisk WFS-hämtning för kommunernas öppna data (GeoJSON i kommunens koordinatsystem).

Allt styrs av kommunfilen: bas-URL, tillåtna värdnamn (https och värden ska vara eller sluta på ett av
dem), lager. Inga nycklar skickas: det här är öppna tjänster. Felen är skrivna för den som kör kommandot
och nämner värden men aldrig hela adressen. Hämtaren (`hamta_bytes`) kan bytas i tester.

Formatet (lagernamn, axelordning, `outputFormat=application/json`) är ANTAGET och aldrig provat mot
Göteborgs tjänst; kontrollerna nedan (koordinaterna ska ligga i området, svaret ska vara GeoJSON, inte
en undantagsrapport) gör att ett fel syns som ett fel i stället för som en tom karta.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from gemensamt.hash import sha256_bytes

Hamtare = Callable[[str], bytes]


class KommunKallaFel(Exception):
    """En kommunal källa svarar inte, svarar med fel eller ger oväntat innehåll."""


def kontrollera_url(url: str, tillatna_varddomaner: Sequence[str]) -> str:
    """Nekar allt utom https till en tillåten värd. Returnerar värdnamnet."""
    d = urllib.parse.urlsplit(url)
    host = (d.hostname or "").lower()
    if d.scheme != "https" or d.username or d.password:
        raise KommunKallaFel(f"Endast https utan användaruppgifter tillåts för {host or 'adressen'}.")
    if not any(host == v or host.endswith("." + v) for v in (x.lower() for x in tillatna_varddomaner)):
        raise KommunKallaFel(
            f"Värden {host!r} är inte tillåten i kommunfilen (tillatna_varddomaner: "
            f"{', '.join(tillatna_varddomaner)})."
        )
    return host


def standard_hamtare(timeout_s: float = 60.0) -> Hamtare:
    def hamta(url: str) -> bytes:
        host = urllib.parse.urlsplit(url).hostname or "?"
        req = urllib.request.Request(url, headers={"User-Agent": "Floden/1.0 (open-data-client)"})
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - https, tillåten värd
                return bytes(resp.read())
        except urllib.error.HTTPError as exc:
            raise KommunKallaFel(
                f"{host} svarade med HTTP {exc.code}. Tjänsten kan vara nere eller adressen ändrad; "
                "försök igen senare."
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise KommunKallaFel(
                f"{host} svarar inte ({type(exc).__name__}). Kontrollera nätet och försök igen."
            ) from exc

    return hamta


def _undantag(ra: bytes) -> str | None:
    text = ra[:4000].decode("utf-8", errors="replace")
    if "ExceptionReport" in text or "ServiceException" in text:
        m = re.search(r"<(?:\w+:)?ExceptionText>([^<]{1,200})", text)
        return m.group(1).strip() if m else "tjänsten svarade med en undantagsrapport"
    return None


def lista_lager(
    wfs_url: str, tillatna: Sequence[str], *, hamta_bytes: Hamtare | None = None
) -> list[str]:
    """Lagernamnen ur GetCapabilities (FeatureType/Name)."""
    kontrollera_url(wfs_url, tillatna)
    url = wfs_url + ("&" if "?" in wfs_url else "?") + urllib.parse.urlencode(
        {"service": "WFS", "version": "2.0.0", "request": "GetCapabilities"}
    )
    ra = (hamta_bytes or standard_hamtare())(url)
    fel = _undantag(ra)
    if fel:
        raise KommunKallaFel(f"GetCapabilities gav fel: {fel}")
    text = ra.decode("utf-8", errors="replace")
    namn: list[str] = []
    for block in re.findall(r"<(?:\w+:)?FeatureType\b.*?</(?:\w+:)?FeatureType>", text, flags=re.S):
        m = re.search(r"<(?:\w+:)?Name>([^<]+)</", block)
        if m:
            namn.append(m.group(1).strip())
    if not namn:
        raise KommunKallaFel("GetCapabilities innehöll inga lager (FeatureType).")
    return namn


def valj_lager(monster: str, lager: Sequence[str]) -> str:
    """Det enda lager som matchar `monster`; annars ett fel som listar kandidaterna."""
    traffar = [x for x in lager if re.search(monster, x)]
    if len(traffar) == 1:
        return traffar[0]
    if not traffar:
        raise KommunKallaFel(
            f"Inget lager matchar '{monster}'. Lager i tjänsten: {', '.join(lager[:30])}"
            f"{' ...' if len(lager) > 30 else ''}. Ange ett med --byggnadslager."
        )
    raise KommunKallaFel(
        f"Flera lager matchar '{monster}': {', '.join(traffar)}. Ange ett med --byggnadslager."
    )


def hamta_geojson(
    wfs_url: str,
    lager: str,
    bbox: tuple[float, float, float, float],
    epsg: int,
    tillatna: Sequence[str],
    *,
    max_objekt: int = 5000,
    hamta_bytes: Hamtare | None = None,
) -> dict[str, Any]:
    """Ett lager som GeoJSON ur bboxen (i `epsg`). Fel om svaret inte är GeoJSON eller om gränsen nåtts."""
    kontrollera_url(wfs_url, tillatna)
    if not lager.strip():
        raise KommunKallaFel("Lagernamnet är tomt.")
    e0, n0, e1, n1 = bbox
    url = wfs_url + ("&" if "?" in wfs_url else "?") + urllib.parse.urlencode(
        {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": lager,
            "srsName": f"EPSG:{epsg}",
            "bbox": f"{e0},{n0},{e1},{n1},EPSG:{epsg}",
            "outputFormat": "application/json",
            "count": str(int(max_objekt)),
        }
    )
    ra = (hamta_bytes or standard_hamtare())(url)
    fel = _undantag(ra)
    if fel:
        raise KommunKallaFel(
            f"Tjänsten avvisade lagret '{lager}': {fel}. Lagernamnet kan vara fel: "
            "floden projekt kommun-lager <kommun> listar lagren."
        )
    try:
        data = json.loads(ra.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KommunKallaFel(f"Svaret för lagret '{lager}' var inte giltig JSON.") from exc
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise KommunKallaFel(f"Svaret för lagret '{lager}' saknar FeatureCollection.")
    antal = len(data.get("features", []))
    if antal >= max_objekt:
        raise KommunKallaFel(
            f"Lagret '{lager}' gav {antal} objekt, gränsen är {max_objekt}: svaret kan vara "
            "avkortat. Minska området eller höj max_objekt i kommunfilen."
        )
    data["_sha256"] = sha256_bytes(ra)
    return data
