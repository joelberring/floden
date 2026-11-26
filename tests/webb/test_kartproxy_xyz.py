# SPDX-License-Identifier: GPL-3.0-or-later
"""Enhets- och säkerhetstester (SSRF, DNS-pinning, redirects, TTL-cache) för `kartproxy_xyz.py`."""

from __future__ import annotations

import socket
import ssl
from email.message import Message
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from webb.kartproxy_xyz import (
    KartproxyFel,
    KartproxyXyz,
    PinnadHTTPSConnection,
    PlattKalla,
    ar_publik_ip,
    skapa_osm_kalla,
    validera_och_los_upp_dns,
    validera_plattkoordinater,
)


def _fake_svar(
    status: int = 200,
    kropp: bytes = b"\x89PNG\r\n\x1a\nMOCKTILE",
    content_type: str = "image/png",
    *,
    location: str | None = None,
    content_length: int | None = None,
) -> Any:
    hdrs = Message()
    hdrs["Content-Type"] = content_type
    if location is not None:
        hdrs["Location"] = location
    if content_length is not None:
        hdrs["Content-Length"] = str(content_length)
    svar = MagicMock()
    svar.status = status
    svar.headers = hdrs
    svar.getheader = lambda k, d=None: hdrs.get(k, d)
    svar.read = lambda n=-1: kropp if n < 0 else kropp[:n]
    return svar


def _dns_post(ip: str, port: int = 443) -> tuple[Any, ...]:
    familj = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return (familj, socket.SOCK_STREAM, 6, "", (ip, port))


# ------------------------------------------------------------------ 1. Plattkoordinater & allowlist


@pytest.mark.parametrize(
    ("z", "x", "y"),
    [
        (0, 0, 0),
        (1, 1, 1),
        (12, 2252, 1205),
        (20, (1 << 20) - 1, (1 << 20) - 1),
    ],
)
def test_plattkoordinater_giltiga(z: int, x: int, y: int) -> None:
    assert validera_plattkoordinater(z, x, y) == (z, x, y)


@pytest.mark.parametrize(
    ("z", "x", "y"),
    [
        (-1, 0, 0),
        (21, 0, 0),
        (0, 1, 0),
        (0, 0, 1),
        (3, -1, 0),
        (3, 8, 0),
        (3, 0, 8),
        (True, 0, 0),
        (2, False, 0),
    ],
)
def test_plattkoordinater_ogiltiga_nekas(z: Any, x: Any, y: Any) -> None:
    with pytest.raises(KartproxyFel) as exc:
        validera_plattkoordinater(z, x, y)
    assert exc.value.status_kod == 400


def test_kalla_allowlist_nekar_http_credentials_ip_och_okanda_vardar() -> None:
    # Endast https:// tillåts.
    with pytest.raises(KartproxyFel):
        PlattKalla(
            id="osm-http",
            url_mall="http://tile.openstreetmap.org/{z}/{x}/{y}.png",
            tillatna_vardar=frozenset({"tile.openstreetmap.org"}),
        )

    # Värd utanför allowlist nekas.
    with pytest.raises(KartproxyFel):
        PlattKalla(
            id="fel-vard",
            url_mall="https://evil.example/{z}/{x}/{y}.png",
            tillatna_vardar=frozenset({"tile.openstreetmap.org"}),
        )

    # Användaruppgifter i URL nekas (skyddar mot user@host-förvirring).
    with pytest.raises(KartproxyFel):
        PlattKalla(
            id="med-creds",
            url_mall="https://user:pass@tile.openstreetmap.org/{z}/{x}/{y}.png",
            tillatna_vardar=frozenset({"tile.openstreetmap.org"}),
        )

    # Rå IP-literal i allowlist nekas.
    with pytest.raises(KartproxyFel):
        PlattKalla(
            id="ra-ip",
            url_mall="https://93.184.216.34/{z}/{x}/{y}.png",
            tillatna_vardar=frozenset({"93.184.216.34"}),
        )

    # Okänd platshållare eller saknade z/x/y nekas.
    with pytest.raises(KartproxyFel):
        PlattKalla(
            id="okand-ph",
            url_mall="https://tile.openstreetmap.org/{z}/{x}/{y}/{hem}.png",
            tillatna_vardar=frozenset({"tile.openstreetmap.org"}),
        )


def test_wmts_mall_och_subdomaner(tmp_path: Path) -> None:
    kalla = PlattKalla(
        id="wmts-test",
        url_mall="https://{s}.tiles.example.test/wmts?TileMatrix={TileMatrix}&TileCol={TileCol}&TileRow={TileRow}",
        tillatna_vardar=frozenset({"a.tiles.example.test", "b.tiles.example.test"}),
        subdomaner=("a", "b"),
    )
    proxy = KartproxyXyz([kalla], tmp_path / "cache")
    url0 = proxy.bygg_platt_url("wmts-test", 5, 10, 12)
    url1 = proxy.bygg_platt_url("wmts-test", 5, 11, 12)
    assert url0 == "https://a.tiles.example.test/wmts?TileMatrix=5&TileCol=10&TileRow=12"
    assert url1 == "https://b.tiles.example.test/wmts?TileMatrix=5&TileCol=11&TileRow=12"


# ------------------------------------------------------------------ 2. SSRF: IP-filter & DNS


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "127.255.255.254",
        "10.0.0.1",
        "10.255.255.254",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.0.1",
        "192.168.1.42",
        "169.254.169.254",
        "169.254.0.1",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "255.255.255.255",
        "::1",
        "::",
        "fc00::1",
        "fd12:3456:789a::1",
        "fe80::1",
        "fe80::1%eth0",
        "ff02::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "::ffff:172.16.0.1",
        "::ffff:192.168.1.1",
        "::ffff:169.254.169.254",
        "::ffff:93.184.216.34",
        "64:ff9b::127.0.0.1",
        "2002:7f00:0001::",
        "2001:0:4136:e378::1",
    ],
)
def test_ssrf_blockerar_samtliga_icke_publika_ipv4_ipv6_och_mapped(ip: str) -> None:
    ok, orsak = ar_publik_ip(ip)
    assert ok is False, f"Förväntade blockering av {ip}"
    assert orsak

    with pytest.raises(KartproxyFel) as exc:
        validera_och_los_upp_dns(
            "https://tile.openstreetmap.org/1/0/0.png",
            tillatna_vardar=frozenset({"tile.openstreetmap.org"}),
            dns_uppslag=lambda host, port: [_dns_post(ip, port)],
        )
    assert exc.value.status_kod == 400


@pytest.mark.parametrize(
    "ip",
    [
        "93.184.216.34",
        "1.1.1.1",
        "2606:2800:220:1:248:1893:25c8:1946",
    ],
)
def test_publika_ipv4_och_ipv6_godkanns(ip: str) -> None:
    ok, _ = ar_publik_ip(ip)
    assert ok is True
    _, vard, pinnad, port, alla = validera_och_los_upp_dns(
        "https://tile.openstreetmap.org/1/0/0.png",
        tillatna_vardar=frozenset({"tile.openstreetmap.org"}),
        dns_uppslag=lambda host, port: [_dns_post(ip, port)],
    )
    assert vard == "tile.openstreetmap.org"
    assert pinnad == ip
    assert port == 443
    assert alla == (ip,)


def test_blandat_dns_svar_publik_plus_privat_blockeras_helt() -> None:
    # Även om första A-posten är publik måste HELA uppslagningen nekas om någon IP är privat!
    with pytest.raises(KartproxyFel) as exc:
        validera_och_los_upp_dns(
            "https://tile.openstreetmap.org/1/0/0.png",
            tillatna_vardar=frozenset({"tile.openstreetmap.org"}),
            dns_uppslag=lambda host, port: [
                _dns_post("93.184.216.34", port),
                _dns_post("10.0.0.5", port),
            ],
        )
    assert exc.value.status_kod == 400


# ------------------------------------------------------------------ 3. DNS-pinning & rebinding


def test_dns_pinning_skyddar_mot_rebinding_och_skickar_sni_samt_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Testa PinnadHTTPSConnection.connect() direkt: TCP mot pinnad IP, TLS SNI mot värdnamn.
    tcp_mal: list[tuple[str, int]] = []
    sni_mal: list[str | None] = []

    def fake_create_conn(
        addr: tuple[str, int], timeout: Any = None, source_address: Any = None
    ) -> Any:
        tcp_mal.append(addr)
        return MagicMock()

    fake_ctx = MagicMock(spec=ssl.SSLContext)

    def fake_wrap(sock: Any, server_hostname: str | None = None) -> Any:
        sni_mal.append(server_hostname)
        return sock

    fake_ctx.wrap_socket.side_effect = fake_wrap
    monkeypatch.setattr(socket, "create_connection", fake_create_conn)

    conn = PinnadHTTPSConnection(
        "93.184.216.34", 443, sni_host="tile.openstreetmap.org", context=fake_ctx
    )
    conn.connect()
    assert tcp_mal == [("93.184.216.34", 443)]
    assert sni_mal == ["tile.openstreetmap.org"]

    # Testa DNS-rebinding genom KartproxyXyz: DNS returnerar publik IP gång 1 och metadata gång 2!
    dns_anrop: list[tuple[str, int]] = []

    def rebinding_dns(host: str, port: int) -> list[Any]:
        dns_anrop.append((host, port))
        ip = "93.184.216.34" if len(dns_anrop) == 1 else "169.254.169.254"
        return [_dns_post(ip, port)]

    anslutningar: list[tuple[str, int, str, str, dict[str, str]]] = []

    class FakeConn:
        def __init__(self, pinned_ip: str, port: int, sni_host: str, timeout: float) -> None:
            self._ip = pinned_ip
            self._port = port
            self._sni = sni_host

        def request(self, method: str, url: str, headers: Any = None) -> None:
            anslutningar.append((self._ip, self._port, self._sni, url, dict(headers or {})))

        def getresponse(self) -> Any:
            return _fake_svar(200, b"\x89PNG\r\n\x1a\nTILE", "image/png; charset=binary")

        def close(self) -> None:
            pass

    proxy = KartproxyXyz(
        [skapa_osm_kalla()],
        tmp_path / "cache",
        dns_uppslag=rebinding_dns,
        anslutningsfabrik=FakeConn,
    )
    svar = proxy.hamta_platta("osm", 10, 560, 300)
    assert svar.ur_cache is False
    assert svar.content_type == "image/png"
    assert svar.data == b"\x89PNG\r\n\x1a\nTILE"
    # DNS löstes EXAKT EN GÅNG -> rebinding mot 169.254.169.254 sker aldrig.
    assert dns_anrop == [("tile.openstreetmap.org", 443)]
    assert len(anslutningar) == 1
    ip, port, sni, sokvag, hdrs = anslutningar[0]
    assert ip == "93.184.216.34"
    assert port == 443
    assert sni == "tile.openstreetmap.org"
    assert sokvag == "/10/560/300.png"
    assert hdrs["Host"] == "tile.openstreetmap.org"


# ------------------------------------------------------------------ 4. Omdirigeringar (redirects)


def test_omdirigering_till_forbjuden_vard_http_och_privat_ip_blockeras(tmp_path: Path) -> None:
    kalla = PlattKalla(
        id="cdn",
        url_mall="https://tile.example.test/{z}/{x}/{y}.png",
        tillatna_vardar=frozenset({"tile.example.test", "cdn2.example.test"}),
        max_omdirigeringar=2,
    )

    def gor_proxy(location: str, dns_karta: dict[str, str]) -> KartproxyXyz:
        def dns(host: str, port: int) -> list[Any]:
            return [_dns_post(dns_karta[host], port)]

        class RedirConn:
            def __init__(self, pinned_ip: str, port: int, sni_host: str, timeout: float) -> None:
                self._sni = sni_host

            def request(self, method: str, url: str, headers: Any = None) -> None:
                pass

            def getresponse(self) -> Any:
                if self._sni == "tile.example.test":
                    return _fake_svar(302, b"", "text/plain", location=location)
                return _fake_svar(200, b"WEBP", "image/webp")

            def close(self) -> None:
                pass

        return KartproxyXyz([kalla], tmp_path / "c", dns_uppslag=dns, anslutningsfabrik=RedirConn)

    # 1) Öppen redirect till värd utanför allowlist -> 400.
    p1 = gor_proxy("https://attacker.evil/x.png", {"tile.example.test": "93.184.216.34"})
    with pytest.raises(KartproxyFel) as e1:
        p1.hamta_platta("cdn", 2, 1, 1)
    assert e1.value.status_kod == 400

    # 2) Nedgradering till http:// -> 400.
    p2 = gor_proxy("http://tile.example.test/2/1/1.png", {"tile.example.test": "93.184.216.34"})
    with pytest.raises(KartproxyFel) as e2:
        p2.hamta_platta("cdn", 2, 1, 1)
    assert e2.value.status_kod == 400

    # 3) Redirect till allowlistad värd vars DNS pekar på metadata-IP 169.254.169.254 -> 400.
    p3 = gor_proxy(
        "https://cdn2.example.test/2/1/1.webp",
        {"tile.example.test": "93.184.216.34", "cdn2.example.test": "169.254.169.254"},
    )
    with pytest.raises(KartproxyFel) as e3:
        p3.hamta_platta("cdn", 2, 1, 1)
    assert e3.value.status_kod == 400

    # 4) Legitim HTTPS-redirect inom allowlist till publik IP -> OK!
    p4 = gor_proxy(
        "https://cdn2.example.test/2/1/1.webp",
        {"tile.example.test": "93.184.216.34", "cdn2.example.test": "1.1.1.1"},
    )
    svar = p4.hamta_platta("cdn", 2, 1, 1)
    assert svar.data == b"WEBP"
    assert svar.content_type == "image/webp"


def test_omdirigeringsloop_och_max_omdirigeringar_noll(tmp_path: Path) -> None:
    anrop = 0

    class LoopConn:
        def __init__(self, pinned_ip: str, port: int, sni_host: str, timeout: float) -> None:
            pass

        def request(self, method: str, url: str, headers: Any = None) -> None:
            nonlocal anrop
            anrop += 1

        def getresponse(self) -> Any:
            return _fake_svar(302, b"", "text/plain", location="https://tile.openstreetmap.org/0/0/0.png")

        def close(self) -> None:
            pass

    kalla_loop = PlattKalla(
        id="osm",
        url_mall="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        tillatna_vardar=frozenset({"tile.openstreetmap.org"}),
        max_omdirigeringar=2,
    )
    proxy = KartproxyXyz(
        [kalla_loop],
        tmp_path / "c1",
        dns_uppslag=lambda h, p: [_dns_post("93.184.216.34", p)],
        anslutningsfabrik=LoopConn,
    )
    with pytest.raises(KartproxyFel) as exc:
        proxy.hamta_platta("osm", 0, 0, 0)
    assert exc.value.status_kod == 502
    assert anrop == 3

    kalla_ingen = PlattKalla(
        id="osm0",
        url_mall="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        tillatna_vardar=frozenset({"tile.openstreetmap.org"}),
        max_omdirigeringar=0,
    )
    proxy0 = KartproxyXyz(
        [kalla_ingen],
        tmp_path / "c2",
        dns_uppslag=lambda h, p: [_dns_post("93.184.216.34", p)],
        anslutningsfabrik=LoopConn,
    )
    with pytest.raises(KartproxyFel) as exc0:
        proxy0.hamta_platta("osm0", 0, 0, 0)
    assert exc0.value.status_kod == 400


# ------------------------------------------------------------------ 5. Storlekstak & Content-Type


@pytest.mark.parametrize(
    "content_type",
    ["text/html", "image/svg+xml", "application/json", "application/octet-stream"],
)
def test_otillaten_content_type_nekas(tmp_path: Path, content_type: str) -> None:
    class BadTypeConn:
        def __init__(self, *args: Any) -> None:
            pass

        def request(self, method: str, url: str, headers: Any = None) -> None:
            pass

        def getresponse(self) -> Any:
            return _fake_svar(200, b"<svg/>", content_type)

        def close(self) -> None:
            pass

    proxy = KartproxyXyz(
        [skapa_osm_kalla()],
        tmp_path / "cache",
        dns_uppslag=lambda h, p: [_dns_post("93.184.216.34", p)],
        anslutningsfabrik=BadTypeConn,
    )
    with pytest.raises(KartproxyFel) as exc:
        proxy.hamta_platta("osm", 1, 0, 0)
    assert exc.value.status_kod == 502


def test_storlekstak_bade_header_och_kropp(tmp_path: Path) -> None:
    kalla = skapa_osm_kalla(max_bytes=16)

    # 1) Content-Length > max_bytes avvisas direkt.
    class BigHeaderConn:
        def __init__(self, *args: Any) -> None:
            pass

        def request(self, method: str, url: str, headers: Any = None) -> None:
            pass

        def getresponse(self) -> Any:
            return _fake_svar(200, b"x" * 16, "image/png", content_length=17)

        def close(self) -> None:
            pass

    p1 = KartproxyXyz(
        [kalla],
        tmp_path / "c1",
        dns_uppslag=lambda h, p: [_dns_post("93.184.216.34", p)],
        anslutningsfabrik=BigHeaderConn,
    )
    with pytest.raises(KartproxyFel) as e1:
        p1.hamta_platta("osm", 0, 0, 0)
    assert e1.value.status_kod == 502

    # 2) Saknad Content-Length men kroppen är 17 B (> 16 B) -> avvisas!
    class BigBodyConn:
        def __init__(self, *args: Any) -> None:
            pass

        def request(self, method: str, url: str, headers: Any = None) -> None:
            pass

        def getresponse(self) -> Any:
            return _fake_svar(200, b"x" * 17, "image/jpeg")

        def close(self) -> None:
            pass

    p2 = KartproxyXyz(
        [kalla],
        tmp_path / "c2",
        dns_uppslag=lambda h, p: [_dns_post("93.184.216.34", p)],
        anslutningsfabrik=BigBodyConn,
    )
    with pytest.raises(KartproxyFel) as e2:
        p2.hamta_platta("osm", 0, 0, 0)
    assert e2.value.status_kod == 502


# ------------------------------------------------------------------ 6. Atomisk diskcache med TTL


def test_atomisk_diskcache_traff_ttl_korruption_och_rensning(tmp_path: Path) -> None:
    tid = [1_700_000_000.0]
    hamtningar = 0
    dns_antal = 0

    def fake_dns(host: str, port: int) -> list[Any]:
        nonlocal dns_antal
        dns_antal += 1
        return [_dns_post("93.184.216.34", port)]

    class CountingConn:
        def __init__(self, *args: Any) -> None:
            pass

        def request(self, method: str, url: str, headers: Any = None) -> None:
            nonlocal hamtningar
            hamtningar += 1

        def getresponse(self) -> Any:
            return _fake_svar(200, f"JPEG-V{hamtningar}".encode(), "image/jpeg")

        def close(self) -> None:
            pass

    cache_mapp = tmp_path / "tile_cache"
    proxy = KartproxyXyz(
        [skapa_osm_kalla(cache_ttl_sekunder=60.0)],
        cache_mapp,
        klocka=lambda: tid[0],
        dns_uppslag=fake_dns,
        anslutningsfabrik=CountingConn,
    )

    # 1) Första hämtningen = cache-miss, anropar uppströms och sparar atomiskt utan .tmp-rester.
    s1 = proxy.hamta_platta("osm", 5, 16, 10)
    assert s1.ur_cache is False
    assert s1.data == b"JPEG-V1"
    assert s1.content_type == "image/jpeg"
    assert hamtningar == 1 and dns_antal == 1
    assert list(cache_mapp.rglob(".tmp-*")) == []

    # 2) Andra hämtningen inom TTL = cache-träff (0 nya DNS- eller HTTP-anrop).
    tid[0] += 30.0
    s2 = proxy.hamta_platta("osm", 5, 16, 10)
    assert s2.ur_cache is True
    assert s2.data == b"JPEG-V1"
    assert s2.sha256 == s1.sha256
    assert hamtningar == 1 and dns_antal == 1

    # 3) Skadad cachefil på disk (ändrade bytes -> sha256-fel) triggar säker omhämtning.
    tile_filer = list(cache_mapp.rglob("*.tile"))
    assert len(tile_filer) == 1
    tile_filer[0].write_bytes(b"KORRUPT")
    s3 = proxy.hamta_platta("osm", 5, 16, 10)
    assert s3.ur_cache is False
    assert s3.data == b"JPEG-V2"
    assert hamtningar == 2

    # 4) Efter TTL-utgång -> omhämtning och `rensa_utgangna()` städar utgångna poster.
    tid[0] += 61.0
    assert proxy.rensa_utgangna() == 1
    s4 = proxy.hamta_platta("osm", 5, 16, 10)
    assert s4.ur_cache is False
    assert s4.data == b"JPEG-V3"
    stat_dict = proxy.statistik()
    assert stat_dict["cache_traffar"] == 1
    assert stat_dict["uppstroms_hamtningar"] == 3
