# SPDX-License-Identifier: GPL-3.0-or-later
"""SSRF-säker WMTS/XYZ-bakgrundsplattproxy med DNS-pinning och atomisk diskcache.

Följer samma etablerade DNS-pinning-mönster som `src/webb/kartverktyg.py`:
- Strikt validering av plattkoordinater (`0 <= z <= 20`, `0 <= x, y < 2**z`).
- Strikt allowlist av källmallar och HTTPS-värdnamn (endast `https://`, aldrig inloggningsuppgifter
  eller styrtecken i URL).
- En enda DNS-uppslagning per anrop där **samtliga** returnerade IP-adresser måste vara globalt
  publika (blockerar IPv4 loopback/private/link-local/CGNAT/multicast/reserved/unspecified,
  IPv6 `::1`/`fc00::/7`/`fe80::/10`/`ff00::/8`/`::`/NAT64/6to4/Teredo samt **alla** IPv4-mapped
  IPv6-adresser `::ffff:0:0/96`).
- Ansluter direkt mot den validerade, pinnade IP-adressen med ursprungligt värdnamn i TLS SNI och
  `Host`-header (skyddar mot DNS-rebinding).
- Omdirigeringar re-valideras fullständigt (HTTPS + värd-allowlist + DNS-kontroll + hopptak),
  så öppna omdirigeringar och omdirigeringar mot förbjudna IP:n alltid blockeras.
- Storlekstak (`max_bytes`, förval 2 MiB), timeout, strikt `Content-Type`-kontroll
  (`image/png`, `image/jpeg`, `image/webp`) och atomisk SHA-256-nycklad diskcache med TTL.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import tempfile
import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from webb.fel import FlodenWebbFel

MIN_ZOOM: Final[int] = 0
MAX_ZOOM: Final[int] = 20
STANDARD_MAX_BYTES: Final[int] = 2 * 1024 * 1024  # 2 MiB
STANDARD_TIMEOUT_S: Final[float] = 10.0
STANDARD_CACHE_TTL_S: Final[float] = 86_400.0  # 24 h
STANDARD_MAX_OMDIRIGERINGAR: Final[int] = 2
STANDARD_ANVANDARAGENT: Final[str] = "Floden-kartproxy-xyz/1.0"

TILLATNA_INNEHALLSTYPER: Final[frozenset[str]] = frozenset(
    {"image/png", "image/jpeg", "image/webp"}
)

_KALLA_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")
_PLATSHALLARE_RE: Final[re.Pattern[str]] = re.compile(r"\{([A-Za-z0-9_]+)\}")
_STYRTECKEN_RE: Final[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f\s]")
_VARDNAMN_RE: Final[re.Pattern[str]] = re.compile(
    r"^[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?)*$"
)

_TILLATNA_PLATSHALLARE: Final[frozenset[str]] = frozenset(
    {"z", "x", "y", "s", "TileMatrix", "TileCol", "TileRow"}
)

# Explicita förbjudna nätverk utöver Pythons `is_global`/`is_private`/`is_loopback`/`is_link_local`.
_FORBJUDNA_IPV4_NAT: Final[tuple[ipaddress.IPv4Network, ...]] = (
    ipaddress.IPv4Network("0.0.0.0/8"),
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("100.64.0.0/10"),  # CGNAT (RFC 6598)
    ipaddress.IPv4Network("127.0.0.0/8"),
    ipaddress.IPv4Network("169.254.0.0/16"),  # Link-local + molnmetadata
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.0.0.0/24"),
    ipaddress.IPv4Network("192.0.2.0/24"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("198.18.0.0/15"),
    ipaddress.IPv4Network("198.51.100.0/24"),
    ipaddress.IPv4Network("203.0.113.0/24"),
    ipaddress.IPv4Network("224.0.0.0/4"),  # Multicast
    ipaddress.IPv4Network("240.0.0.0/4"),  # Reserverad + broadcast
)

_FORBJUDNA_IPV6_NAT: Final[tuple[ipaddress.IPv6Network, ...]] = (
    ipaddress.IPv6Network("::/128"),
    ipaddress.IPv6Network("::1/128"),
    ipaddress.IPv6Network("::ffff:0:0/96"),  # IPv4-mapped IPv6
    ipaddress.IPv6Network("64:ff9b::/96"),  # NAT64 (RFC 6052)
    ipaddress.IPv6Network("64:ff9b:1::/48"),  # Lokal NAT64 (RFC 8215)
    ipaddress.IPv6Network("100::/64"),  # Discard
    ipaddress.IPv6Network("2001::/32"),  # Teredo
    ipaddress.IPv6Network("2001:db8::/32"),  # Dokumentation
    ipaddress.IPv6Network("2002::/16"),  # 6to4
    ipaddress.IPv6Network("fc00::/7"),  # ULA (privat IPv6)
    ipaddress.IPv6Network("fe80::/10"),  # Link-local IPv6
    ipaddress.IPv6Network("ff00::/8"),  # Multicast IPv6
)

DnsUppslagFn = Callable[[str, int], Sequence[Any]]


class HttpsAnslutningProtokoll(Protocol):
    """Minsta gränssnitt för pinnad HTTPS-anslutning (möjliggör mockad transport i tester)."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] = ...,
    ) -> None: ...

    def getresponse(self) -> Any: ...

    def close(self) -> None: ...


AnslutningsFabrik = Callable[[str, int, str, float], HttpsAnslutningProtokoll]


class KartproxyFel(FlodenWebbFel):
    """Fel i WMTS/XYZ-plattproxyn (SSRF-blockering, ogiltiga koordinater, fel Content-Type)."""


class PinnadHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS-anslutning som kopplar TCP mot validerad IP och verifierar TLS mot `sni_host`."""

    def __init__(
        self,
        pinned_ip: str,
        port: int,
        *,
        sni_host: str,
        timeout: float = STANDARD_TIMEOUT_S,
        context: ssl.SSLContext | None = None,
    ) -> None:
        ssl_ctx = context or ssl.create_default_context()
        super().__init__(
            pinned_ip,
            port=port,
            timeout=timeout,
            context=ssl_ctx,
        )
        self._pinned_ip = pinned_ip
        self._sni_host = sni_host
        self._ssl_ctx = ssl_ctx

    def connect(self) -> None:
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        try:
            self.sock = self._ssl_ctx.wrap_socket(sock, server_hostname=self._sni_host)
        except BaseException:
            sock.close()
            raise


def _normalisera_vardnamn(vard: str) -> str:
    norm = vard.strip().lower()
    if not norm or _STYRTECKEN_RE.search(norm) or norm.endswith("."):
        raise KartproxyFel(f"Ogiltigt värdnamn {vard!r}.", status_kod=400)
    # Råa IP-literaler tillåts inte i allowlist — endast verifierade DNS-värdnamn.
    try:
        ipaddress.ip_address(norm.strip("[]"))
    except ValueError:
        pass
    else:
        raise KartproxyFel(
            f"Rå IP-adress {vard!r} tillåts inte i värd-allowlist; ange DNS-värdnamn.",
            status_kod=400,
        )
    if not _VARDNAMN_RE.match(norm):
        raise KartproxyFel(f"Ogiltigt värdnamn {vard!r}.", status_kod=400)
    return norm


@dataclass(frozen=True)
class PlattKalla:
    """Konfigurerad och allowlistad WMTS/XYZ-plattkälla (endast `https://`)."""

    id: str
    url_mall: str
    tillatna_vardar: frozenset[str]
    tillatna_portar: frozenset[int] = frozenset({443})
    subdomaner: tuple[str, ...] = ()
    cache_ttl_sekunder: float = STANDARD_CACHE_TTL_S
    max_bytes: int = STANDARD_MAX_BYTES
    timeout_sekunder: float = STANDARD_TIMEOUT_S
    max_omdirigeringar: int = STANDARD_MAX_OMDIRIGERINGAR
    max_zoom: int = MAX_ZOOM
    anvandaragent: str = STANDARD_ANVANDARAGENT

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _KALLA_ID_RE.match(self.id):
            raise KartproxyFel(f"Ogiltigt käll-id {self.id!r}.", status_kod=400)
        if not isinstance(self.url_mall, str) or not self.url_mall.strip():
            raise KartproxyFel("url_mall måste vara en icke-tom HTTPS-URL-mall.", status_kod=400)
        if _STYRTECKEN_RE.search(self.url_mall):
            raise KartproxyFel(
                "url_mall får inte innehålla blanksteg eller styrtecken.", status_kod=400
            )
        if not self.tillatna_vardar:
            raise KartproxyFel(
                "tillatna_vardar måste innehålla minst ett värdnamn.", status_kod=400
            )
        norm_vardar = frozenset(_normalisera_vardnamn(v) for v in self.tillatna_vardar)
        object.__setattr__(self, "tillatna_vardar", norm_vardar)

        if not self.tillatna_portar or any(
            isinstance(p, bool) or not isinstance(p, int) or not (1 <= p <= 65535)
            for p in self.tillatna_portar
        ):
            raise KartproxyFel(
                "tillatna_portar måste innehålla giltiga TCP-portar.", status_kod=400
            )
        if isinstance(self.max_zoom, bool) or not (MIN_ZOOM <= self.max_zoom <= MAX_ZOOM):
            raise KartproxyFel(
                f"max_zoom måste ligga i [{MIN_ZOOM}, {MAX_ZOOM}].", status_kod=400
            )
        if isinstance(self.max_bytes, bool) or self.max_bytes <= 0:
            raise KartproxyFel("max_bytes måste vara > 0.", status_kod=400)
        if not math.isfinite(self.timeout_sekunder) or self.timeout_sekunder <= 0.0:
            raise KartproxyFel("timeout_sekunder måste vara > 0.", status_kod=400)
        if not math.isfinite(self.cache_ttl_sekunder) or self.cache_ttl_sekunder < 0.0:
            raise KartproxyFel("cache_ttl_sekunder måste vara >= 0.", status_kod=400)
        if isinstance(self.max_omdirigeringar, bool) or self.max_omdirigeringar < 0:
            raise KartproxyFel("max_omdirigeringar måste vara >= 0.", status_kod=400)

        hittade = set(_PLATSHALLARE_RE.findall(self.url_mall))
        okanda = hittade - _TILLATNA_PLATSHALLARE
        if okanda:
            raise KartproxyFel(
                f"Okända platshållare i url_mall: {sorted(okanda)}.", status_kod=400
            )
        har_xyz = {"z", "x", "y"}.issubset(hittade)
        har_wmts = {"TileMatrix", "TileCol", "TileRow"}.issubset(hittade)
        if not (har_xyz or har_wmts):
            raise KartproxyFel(
                "url_mall måste innehålla {z}/{x}/{y} eller {TileMatrix}/{TileCol}/{TileRow}.",
                status_kod=400,
            )
        if "s" in hittade:
            if not self.subdomaner:
                raise KartproxyFel(
                    "url_mall använder {s} men subdomaner är tom.", status_kod=400
                )
            for sd in self.subdomaner:
                if not re.match(r"^[a-z0-9\-]{1,16}$", sd):
                    raise KartproxyFel(f"Ogiltig subdomän {sd!r}.", status_kod=400)

        # Validera mallen med provkoordinater (0, 0, 0) för samtliga subdomäner.
        prov_subdomaner = self.subdomaner if "s" in hittade else ("",)
        for sd in prov_subdomaner:
            prov_url = _formatera_mall(self.url_mall, 0, 0, 0, sd)
            validera_platt_url(
                prov_url,
                tillatna_vardar=self.tillatna_vardar,
                tillatna_portar=self.tillatna_portar,
            )


@dataclass(frozen=True)
class PlattSvar:
    """Hämtad kartplatta (antingen från uppströms eller ur diskcachen)."""

    data: bytes
    content_type: str
    ur_cache: bool
    kalla_id: str
    z: int
    x: int
    y: int
    sha256: str
    etagg: str


def skapa_osm_kalla(
    *,
    cache_ttl_sekunder: float = STANDARD_CACHE_TTL_S,
    max_bytes: int = STANDARD_MAX_BYTES,
    timeout_sekunder: float = STANDARD_TIMEOUT_S,
) -> PlattKalla:
    """Skapar en förkonfigurerad HTTPS-källa för OpenStreetMap XYZ-bakgrundsplattor."""
    return PlattKalla(
        id="osm",
        url_mall="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        tillatna_vardar=frozenset({"tile.openstreetmap.org"}),
        cache_ttl_sekunder=cache_ttl_sekunder,
        max_bytes=max_bytes,
        timeout_sekunder=timeout_sekunder,
    )


def validera_plattkoordinater(
    z: int,
    x: int,
    y: int,
    *,
    max_z: int = MAX_ZOOM,
) -> tuple[int, int, int]:
    """Validerar XYZ/WMTS-plattkoordinater (`0 <= z <= max_z`, `0 <= x, y < 2**z`)."""
    for namn, varde in (("z", z), ("x", x), ("y", y)):
        if isinstance(varde, bool) or not isinstance(varde, int):
            raise KartproxyFel(
                f"Plattkoordinaten '{namn}' måste vara ett heltal.", status_kod=400
            )
    if not (MIN_ZOOM <= z <= max_z):
        raise KartproxyFel(
            f"Zoomnivån z={z} ligger utanför tillåtet intervall [{MIN_ZOOM}, {max_z}].",
            status_kod=400,
        )
    max_index = (1 << z) - 1
    if not (0 <= x <= max_index) or not (0 <= y <= max_index):
        raise KartproxyFel(
            f"Plattkoordinaterna (x={x}, y={y}) ligger utanför [0, {max_index}] vid z={z}.",
            status_kod=400,
        )
    return z, x, y


def ar_publik_ip(ip_str: str) -> tuple[bool, str]:
    """Avgör om en IP-adress (IPv4 eller IPv6) är strikt globalt publik.

    Returnerar `(True, "")` endast om adressen är publik; annars `(False, orsak)`.
    Blockerar loopback, RFC1918, CGNAT, link-local/metadata, multicast, reserverat,
    unspecified, IPv6 ULA (`fc00::/7`), link-local (`fe80::/10`), NAT64, 6to4, Teredo
    samt samtliga IPv4-mapped IPv6-adresser (`::ffff:0:0/96`).
    """
    if not isinstance(ip_str, str) or not ip_str.strip():
        return False, "tom IP-adress"
    if "%" in ip_str:
        return False, "IPv6-zonidentifierare är inte tillåten"
    try:
        ip = ipaddress.ip_address(ip_str.strip())
    except ValueError:
        return False, f"ogiltig IP-adress {ip_str!r}"

    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return False, f"IPv4-mappad IPv6-adress ({ip}) är blockerad"
        if ip.sixtofour is not None:
            return False, f"6to4-adress ({ip}) är blockerad"
        if ip.teredo is not None:
            return False, f"Teredo-adress ({ip}) är blockerad"
        for nat in _FORBJUDNA_IPV6_NAT:
            if ip in nat:
                return False, f"IPv6-adress i förbjudet nät {nat}"
    else:
        for nat4 in _FORBJUDNA_IPV4_NAT:
            if ip in nat4:
                return False, f"IPv4-adress i förbjudet nät {nat4}"

    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    ):
        return False, f"icke-publik IP-adress ({ip})"
    return True, ""


def validera_platt_url(
    url: str,
    *,
    tillatna_vardar: frozenset[str],
    tillatna_portar: frozenset[int] = frozenset({443}),
) -> tuple[urllib.parse.SplitResult, str, int]:
    """Validerar att URL är strikt `https://` mot allowlistad värd och port (inga credentials)."""
    if not isinstance(url, str) or not url:
        raise KartproxyFel("URL får inte vara tom.", status_kod=400)
    if _STYRTECKEN_RE.search(url):
        raise KartproxyFel("URL får inte innehålla blanksteg eller styrtecken.", status_kod=400)
    delar = urllib.parse.urlsplit(url)
    if delar.scheme.lower() != "https":
        raise KartproxyFel("Endast https:// tillåts i plattproxyn.", status_kod=400)
    if delar.username is not None or delar.password is not None:
        raise KartproxyFel("Användaruppgifter i URL är inte tillåtna.", status_kod=400)
    if not delar.hostname:
        raise KartproxyFel("URL saknar värdnamn.", status_kod=400)
    if delar.fragment:
        raise KartproxyFel("URL-fragment är inte tillåtna.", status_kod=400)
    try:
        port = delar.port if delar.port is not None else 443
    except ValueError as exc:
        raise KartproxyFel(f"Ogiltig port i URL: {exc}", status_kod=400) from exc

    vard = _normalisera_vardnamn(delar.hostname)
    if vard not in tillatna_vardar:
        raise KartproxyFel(
            f"Värden {vard!r} finns inte i tillåten allowlist.", status_kod=400
        )
    if port not in tillatna_portar:
        raise KartproxyFel(
            f"Port {port} är inte tillåten för plattproxyn.", status_kod=400
        )
    return delar, vard, port


def _standard_dns_uppslag(host: str, port: int) -> Sequence[Any]:
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


def validera_och_los_upp_dns(
    url: str,
    *,
    tillatna_vardar: frozenset[str],
    tillatna_portar: frozenset[int] = frozenset({443}),
    dns_uppslag: DnsUppslagFn | None = None,
) -> tuple[urllib.parse.SplitResult, str, str, int, tuple[str, ...]]:
    """Validerar URL, gör en enda DNS-uppslagning och kräver att SAMTLIGA IP:n är publika.

    Returnerar `(delar, normaliserad_vard, pinnad_ip, port, alla_ip_adresser)`.
    """
    delar, vard, port = validera_platt_url(
        url, tillatna_vardar=tillatna_vardar, tillatna_portar=tillatna_portar
    )
    uppslag = dns_uppslag if dns_uppslag is not None else _standard_dns_uppslag
    try:
        traffar = uppslag(vard, port)
    except OSError as exc:
        raise KartproxyFel(
            f"DNS-uppslagning misslyckades för {vard}: {exc}", status_kod=502
        ) from exc
    if not traffar:
        raise KartproxyFel(f"DNS gav inga IP-adresser för {vard}.", status_kod=502)

    godkanda: list[str] = []
    for post in traffar:
        try:
            ip_str = str(post[4][0])
        except (IndexError, TypeError) as exc:
            raise KartproxyFel(f"Ogiltigt DNS-svar för {vard}.", status_kod=502) from exc
        ok, orsak = ar_publik_ip(ip_str)
        if not ok:
            raise KartproxyFel(
                f"SSRF-skydd: blockerad adress för {vard} ({orsak}).", status_kod=400
            )
        if ip_str not in godkanda:
            godkanda.append(ip_str)

    if not godkanda:
        raise KartproxyFel(f"Inga godkända publika IP-adresser för {vard}.", status_kod=502)
    return delar, vard, godkanda[0], port, tuple(godkanda)


def _formatera_mall(mall: str, z: int, x: int, y: int, subdoman: str) -> str:
    ersattningar = {
        "z": str(z),
        "x": str(x),
        "y": str(y),
        "TileMatrix": str(z),
        "TileCol": str(x),
        "TileRow": str(y),
        "s": subdoman,
    }
    return _PLATSHALLARE_RE.sub(lambda m: ersattningar[m.group(1)], mall)


def _host_header(vard: str, port: int) -> str:
    return vard if port == 443 else f"{vard}:{port}"


def _standard_anslutning(
    pinned_ip: str, port: int, sni_host: str, timeout_s: float
) -> HttpsAnslutningProtokoll:
    return PinnadHTTPSConnection(pinned_ip, port, sni_host=sni_host, timeout=timeout_s)


def _skriv_atomiskt_bytes(mal: Path, data: bytes) -> None:
    mal.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_namn = tempfile.mkstemp(prefix=".tmp-", dir=mal.parent)
    tmp_sokvag = Path(tmp_namn)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_sokvag, mal)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_sokvag.unlink(missing_ok=True)
        raise


class KartproxyXyz:
    """SSRF-säker bakgrundsplattproxy med DNS-pinning, storlekstak och atomisk TTL-diskcache."""

    def __init__(
        self,
        kallor: Mapping[str, PlattKalla] | Sequence[PlattKalla],
        cache_mapp: str | Path,
        *,
        klocka: Callable[[], float] | None = None,
        dns_uppslag: DnsUppslagFn | None = None,
        anslutningsfabrik: AnslutningsFabrik | None = None,
    ) -> None:
        if isinstance(kallor, Mapping):
            self._kallor: dict[str, PlattKalla] = dict(kallor)
        else:
            self._kallor = {k.id: k for k in kallor}
        if not self._kallor:
            raise KartproxyFel("Minst en tillåten plattkälla måste konfigureras.", status_kod=400)
        self.cache_mapp = Path(cache_mapp)
        self.cache_mapp.mkdir(parents=True, exist_ok=True)
        self._klocka: Callable[[], float] = klocka if klocka is not None else time.time
        self._dns_uppslag = dns_uppslag
        self._anslutningsfabrik: AnslutningsFabrik = (
            anslutningsfabrik if anslutningsfabrik is not None else _standard_anslutning
        )
        self._lock = threading.Lock()
        self._stat = {"cache_traffar": 0, "cache_missar": 0, "uppstroms_hamtningar": 0, "fel": 0}

    @property
    def kalla_idn(self) -> tuple[str, ...]:
        return tuple(sorted(self._kallor))

    def _kalla(self, kalla_id: str) -> PlattKalla:
        kalla = self._kallor.get(kalla_id)
        if kalla is None:
            raise KartproxyFel(f"Okänd eller ej tillåten plattkälla {kalla_id!r}.", status_kod=404)
        return kalla

    def bygg_platt_url(self, kalla_id: str, z: int, x: int, y: int) -> str:
        """Validerar källa och koordinater och returnerar den exakta tillåtna HTTPS-URL:en."""
        kalla = self._kalla(kalla_id)
        vz, vx, vy = validera_plattkoordinater(z, x, y, max_z=kalla.max_zoom)
        subdoman = (
            kalla.subdomaner[(vx + vy) % len(kalla.subdomaner)] if kalla.subdomaner else ""
        )
        url = _formatera_mall(kalla.url_mall, vz, vx, vy, subdoman)
        validera_platt_url(
            url,
            tillatna_vardar=kalla.tillatna_vardar,
            tillatna_portar=kalla.tillatna_portar,
        )
        return url

    def _cache_sokvagar(self, kalla_id: str, url: str, z: int, x: int, y: int) -> tuple[Path, Path]:
        nyckel = hashlib.sha256(f"{kalla_id}\0{url}\0{z}/{x}/{y}".encode()).hexdigest()
        undermapp = self.cache_mapp / nyckel[:2]
        return undermapp / f"{nyckel}.tile", undermapp / f"{nyckel}.meta.json"

    def _las_cache(
        self, kalla: PlattKalla, url: str, z: int, x: int, y: int
    ) -> PlattSvar | None:
        if kalla.cache_ttl_sekunder <= 0.0:
            return None
        tile_fil, meta_fil = self._cache_sokvagar(kalla.id, url, z, x, y)
        if not (tile_fil.is_file() and meta_fil.is_file()):
            return None
        try:
            meta = json.loads(meta_fil.read_text(encoding="utf-8"))
            utgar_utc = float(meta["utgar_utc"])
            if float(self._klocka()) >= utgar_utc:
                return None
            content_type = str(meta["content_type"]).lower()
            if content_type not in TILLATNA_INNEHALLSTYPER:
                return None
            forvantad_sha = str(meta["sha256"])
            data = tile_fil.read_bytes()
            if not data or len(data) > kalla.max_bytes:
                return None
            faktisk_sha = hashlib.sha256(data).hexdigest()
            if faktisk_sha != forvantad_sha:
                return None
            return PlattSvar(
                data=data,
                content_type=content_type,
                ur_cache=True,
                kalla_id=kalla.id,
                z=z,
                x=x,
                y=y,
                sha256=faktisk_sha,
                etagg=f'"{faktisk_sha[:32]}"',
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _spara_cache(
        self,
        kalla: PlattKalla,
        url: str,
        z: int,
        x: int,
        y: int,
        data: bytes,
        content_type: str,
        sha256_hex: str,
    ) -> None:
        if kalla.cache_ttl_sekunder <= 0.0:
            return
        tile_fil, meta_fil = self._cache_sokvagar(kalla.id, url, z, x, y)
        nu = float(self._klocka())
        meta = {
            "kalla_id": kalla.id,
            "z": z,
            "x": x,
            "y": y,
            "content_type": content_type,
            "storlek_bytes": len(data),
            "sha256": sha256_hex,
            "sparad_utc": nu,
            "ttl_sekunder": kalla.cache_ttl_sekunder,
            "utgar_utc": nu + kalla.cache_ttl_sekunder,
        }
        meta_bytes = (json.dumps(meta, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        # Skriv datan atomiskt FÖRST, metadata EFTER, så en läsare aldrig ser halvskriven cache.
        _skriv_atomiskt_bytes(tile_fil, data)
        _skriv_atomiskt_bytes(meta_fil, meta_bytes)

    def _hamta_uppstroms(self, kalla: PlattKalla, url: str) -> tuple[bytes, str]:
        aktuell = url
        for _ in range(kalla.max_omdirigeringar + 1):
            delar, vard, pinnad_ip, port, _ = validera_och_los_upp_dns(
                aktuell,
                tillatna_vardar=kalla.tillatna_vardar,
                tillatna_portar=kalla.tillatna_portar,
                dns_uppslag=self._dns_uppslag,
            )
            sokvag = delar.path or "/"
            if delar.query:
                sokvag = f"{sokvag}?{delar.query}"
            headers = {
                "Host": _host_header(vard, port),
                "User-Agent": kalla.anvandaragent,
                "Accept": "image/webp,image/png,image/jpeg",
            }
            conn = self._anslutningsfabrik(pinnad_ip, port, vard, kalla.timeout_sekunder)
            try:
                conn.request("GET", sokvag, headers=headers)
                svar = conn.getresponse()
                status = int(svar.status)
                if status in (301, 302, 303, 307, 308):
                    plats = svar.getheader("Location")
                    svar.read(0)
                    if kalla.max_omdirigeringar == 0:
                        raise KartproxyFel(
                            "Omdirigeringar är inte tillåtna för denna plattkälla.",
                            status_kod=400,
                        )
                    if not plats:
                        raise KartproxyFel(
                            f"Uppströms svarade {status} utan Location-header.",
                            status_kod=502,
                        )
                    ny_url = urllib.parse.urljoin(aktuell, str(plats).strip())
                    # Kontrollera omdirigeringsmålet direkt mot HTTPS + värd-allowlist.
                    validera_platt_url(
                        ny_url,
                        tillatna_vardar=kalla.tillatna_vardar,
                        tillatna_portar=kalla.tillatna_portar,
                    )
                    aktuell = ny_url
                    continue
                if status == 404:
                    raise KartproxyFel("Kartplattan saknas uppströms (404).", status_kod=404)
                if status != 200:
                    raise KartproxyFel(
                        f"Plattservern svarade med oväntad status {status}.", status_kod=502
                    )

                cl_raw = svar.getheader("Content-Length")
                if cl_raw is not None:
                    try:
                        cl = int(str(cl_raw).strip())
                    except ValueError:
                        cl = -1
                    if cl > kalla.max_bytes:
                        raise KartproxyFel(
                            f"Kartplattan överskrider storlekstaket ({cl} > {kalla.max_bytes} B).",
                            status_kod=502,
                        )

                innehallstyp = str(svar.headers.get_content_type()).strip().lower()
                if innehallstyp not in TILLATNA_INNEHALLSTYPER:
                    raise KartproxyFel(
                        f"Otillåten Content-Type {innehallstyp!r} från plattservern.",
                        status_kod=502,
                    )

                data: bytes = svar.read(kalla.max_bytes + 1)
                if len(data) > kalla.max_bytes:
                    raise KartproxyFel(
                        f"Kartplattan överskrider storlekstaket ({kalla.max_bytes} B).",
                        status_kod=502,
                    )
                if not data:
                    raise KartproxyFel("Plattservern returnerade tomt svar.", status_kod=502)
                return data, innehallstyp
            except KartproxyFel:
                raise
            except (http.client.HTTPException, OSError, ssl.SSLError, ValueError) as exc:
                raise KartproxyFel(
                    f"Kunde inte hämta kartplatta från {vard}: {exc}", status_kod=502
                ) from exc
            finally:
                conn.close()

        raise KartproxyFel("För många omdirigeringar från plattservern.", status_kod=502)

    def hamta_platta(self, kalla_id: str, z: int, x: int, y: int) -> PlattSvar:
        """Hämtar en kartplatta från diskcachen (om giltig TTL) eller SSRF-säkert uppströms."""
        kalla = self._kalla(kalla_id)
        url = self.bygg_platt_url(kalla_id, z, x, y)

        cachat = self._las_cache(kalla, url, z, x, y)
        if cachat is not None:
            with self._lock:
                self._stat["cache_traffar"] += 1
            return cachat

        with self._lock:
            self._stat["cache_missar"] += 1

        try:
            data, content_type = self._hamta_uppstroms(kalla, url)
        except Exception:
            with self._lock:
                self._stat["fel"] += 1
            raise

        sha256_hex = hashlib.sha256(data).hexdigest()
        self._spara_cache(kalla, url, z, x, y, data, content_type, sha256_hex)
        with self._lock:
            self._stat["uppstroms_hamtningar"] += 1
        return PlattSvar(
            data=data,
            content_type=content_type,
            ur_cache=False,
            kalla_id=kalla.id,
            z=z,
            x=x,
            y=y,
            sha256=sha256_hex,
            etagg=f'"{sha256_hex[:32]}"',
        )

    def rensa_utgangna(self) -> int:
        """Rensar utgångna eller trasiga cacheposter på disk. Returnerar antal rensade poster."""
        nu = float(self._klocka())
        rensade = 0
        for meta_fil in sorted(self.cache_mapp.rglob("*.meta.json")):
            tile_fil = meta_fil.with_name(meta_fil.name.removesuffix(".meta.json") + ".tile")
            utgangen = False
            try:
                meta = json.loads(meta_fil.read_text(encoding="utf-8"))
                utgangen = nu >= float(meta["utgar_utc"]) or not tile_fil.is_file()
            except (OSError, ValueError, KeyError, TypeError):
                utgangen = True
            if utgangen:
                meta_fil.unlink(missing_ok=True)
                tile_fil.unlink(missing_ok=True)
                rensade += 1
        return rensade

    def statistik(self) -> dict[str, int]:
        """Returnerar en kopia av driftstatistiken för proxyn."""
        with self._lock:
            return dict(self._stat)
