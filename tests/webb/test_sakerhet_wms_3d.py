# ruff: noqa: E501
"""Webbsäkerhet (P0-6): DNS-pinning i WMS-proxyn (`kartverktyg.py`) och 3D-behörighet (`vy3d.py`)."""

from __future__ import annotations

import http.client
import socket
import ssl
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from webb.fel import FlodenWebbFel
from webb.kartverktyg import MAX_WMS_OMDIRIGERINGAR, KartverktygTjanst, _PinnadHTTPSConnection
from webb.vy3d import Vy3dTjanst

# ------------------------------------------------------------------ 1. WMS-proxy: DNS-pinning & SSRF


def _fake_kv(tmp_path: Path, *, tillat_loopback: bool = False) -> KartverktygTjanst:
    fragor = SimpleNamespace(fragemappar=[], projektrot=tmp_path)
    return KartverktygTjanst(
        fragor=fragor,  # type: ignore[arg-type]
        mapp=tmp_path / "kv",
        tillat_loopback=tillat_loopback,
    )


def _fake_svar(
    status: int = 200,
    kropp: bytes = b"ok",
    content_type: str = "image/png",
    location: str | None = None,
) -> Any:
    hdrs = Message()
    hdrs["Content-Type"] = content_type
    if location is not None:
        hdrs["Location"] = location
    svar = MagicMock()
    svar.status = status
    svar.headers = hdrs
    svar.getheader = lambda k, d=None: hdrs.get(k, d)
    svar.read = lambda n=-1: kropp if n < 0 else kropp[:n]
    return svar


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "::1",
        "::ffff:127.0.0.1",
        "10.0.0.5",
        "172.16.0.1",
        "192.168.1.10",
        "169.254.169.254",
        "::ffff:169.254.169.254",
        "fe80::1",
        "0.0.0.0",
    ],
)
def test_wms_nekar_loopback_privat_link_local_och_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ip: str
) -> None:
    kv = _fake_kv(tmp_path, tillat_loopback=False)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, **kw: [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))],
    )
    with pytest.raises(FlodenWebbFel) as exc:
        kv._kontrollera_och_los_upp("https://wms.example.test/ows")
    assert exc.value.status_kod == 400


def test_wms_http_dns_pinning_skyddar_mot_rebinding_och_skickar_host_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kv = _fake_kv(tmp_path, tillat_loopback=False)
    anrop_dns: list[tuple[str, int]] = []

    def rebinding_dns(host: str, port: int, **kw: Any) -> list[Any]:
        anrop_dns.append((host, port))
        # Första uppslagningen ger publik IP; en eventuell andra uppslagning hade pekat på metadata!
        ip = "93.184.216.34" if len(anrop_dns) == 1 else "169.254.169.254"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(socket, "getaddrinfo", rebinding_dns)

    anslutna: list[tuple[str, int, str, dict[str, str]]] = []

    class FakeHTTPConn:
        def __init__(self, host: str, port: int, timeout: float = 10.0) -> None:
            self.host = host
            self.port = port

        def request(self, metod: str, sokvag: str, headers: dict[str, str] | None = None) -> None:
            anslutna.append((self.host, self.port, sokvag, dict(headers or {})))

        def getresponse(self) -> Any:
            return _fake_svar(200, b"PNGDATA", "image/png")

        def close(self) -> None:
            pass

    monkeypatch.setattr(http.client, "HTTPConnection", FakeHTTPConn)

    data, typ = kv._hamta("http://wms.example.test:8080/ows?SERVICE=WMS", 1024)
    assert data == b"PNGDATA" and typ == "image/png"
    # DNS löstes exakt en gång — rebinding till 169.254.169.254 sker aldrig.
    assert anrop_dns == [("wms.example.test", 8080)]
    # Anslutningen gick direkt mot pinnad IP med korrekt Host-header.
    assert len(anslutna) == 1
    assert anslutna[0][0] == "93.184.216.34"
    assert anslutna[0][1] == 8080
    assert anslutna[0][3]["Host"] == "wms.example.test:8080"


def test_wms_https_dns_pinning_ansluter_till_ip_med_sni_och_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kv = _fake_kv(tmp_path, tillat_loopback=False)
    anrop_dns: list[str] = []

    def fake_dns(host: str, port: int, **kw: Any) -> list[Any]:
        anrop_dns.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_dns)

    # Kontrollera _PinnadHTTPSConnection.connect() direkt: TCP mot pinnad IP, SNI mot ursprunglig host.
    tcp_mal: list[tuple[str, int]] = []
    sni_mal: list[str | None] = []

    def fake_create_conn(addr: tuple[str, int], timeout: Any = None, source_address: Any = None) -> Any:
        tcp_mal.append(addr)
        return MagicMock()

    fake_ctx = MagicMock(spec=ssl.SSLContext)

    def fake_wrap(sock: Any, server_hostname: str | None = None) -> Any:
        sni_mal.append(server_hostname)
        return sock

    fake_ctx.wrap_socket.side_effect = fake_wrap
    monkeypatch.setattr(socket, "create_connection", fake_create_conn)

    conn = _PinnadHTTPSConnection(
        "93.184.216.34", 443, sni_host="wms.example.test", context=fake_ctx
    )
    conn.connect()
    assert tcp_mal == [("93.184.216.34", 443)]
    assert sni_mal == ["wms.example.test"]

    # Kontrollera också att _hamta instansierar _PinnadHTTPSConnection med pinnad IP och sni_host.
    skapade: list[tuple[str, int, str, dict[str, str]]] = []

    class FakePinnadHTTPS:
        def __init__(
            self, pinned_ip: str, port: int, *, sni_host: str, timeout: float = 10.0
        ) -> None:
            self._ip = pinned_ip
            self._port = port
            self._sni = sni_host

        def request(self, metod: str, sokvag: str, headers: dict[str, str] | None = None) -> None:
            skapade.append((self._ip, self._port, self._sni, dict(headers or {})))

        def getresponse(self) -> Any:
            return _fake_svar(200, b"OK", "image/png")

        def close(self) -> None:
            pass

    monkeypatch.setattr("webb.kartverktyg._PinnadHTTPSConnection", FakePinnadHTTPS)
    data, _ = kv._hamta("https://wms.example.test/ows", 1024)
    assert data == b"OK"
    assert anrop_dns == ["wms.example.test"]
    assert skapade == [("93.184.216.34", 443, "wms.example.test", {"Host": "wms.example.test", "User-Agent": "Floden-kartverktyg"})]


def test_wms_omdirigering_till_metadata_nekas_och_loop_stoppas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kv = _fake_kv(tmp_path, tillat_loopback=False)

    def fake_dns(host: str, port: int, **kw: Any) -> list[Any]:
        ip = "169.254.169.254" if host == "intern.test" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_dns)

    class RedirectToMeta:
        def __init__(self, host: str, port: int, timeout: float = 10.0) -> None:
            pass

        def request(self, metod: str, sokvag: str, headers: dict[str, str] | None = None) -> None:
            pass

        def getresponse(self) -> Any:
            return _fake_svar(302, b"", "text/plain", location="http://intern.test/latest/meta-data/")

        def close(self) -> None:
            pass

    monkeypatch.setattr(http.client, "HTTPConnection", RedirectToMeta)
    with pytest.raises(FlodenWebbFel) as exc:
        kv._hamta("http://wms.example.test/start", 1024)
    assert exc.value.status_kod == 400

    # Oändlig omdirigeringsloop stoppas efter MAX_WMS_OMDIRIGERINGAR.
    antal = 0

    class RedirectLoop:
        def __init__(self, host: str, port: int, timeout: float = 10.0) -> None:
            pass

        def request(self, metod: str, sokvag: str, headers: dict[str, str] | None = None) -> None:
            nonlocal antal
            antal += 1

        def getresponse(self) -> Any:
            return _fake_svar(302, b"", "text/plain", location="http://wms.example.test/loop")

        def close(self) -> None:
            pass

    monkeypatch.setattr(http.client, "HTTPConnection", RedirectLoop)
    with pytest.raises(FlodenWebbFel) as exc2:
        kv._hamta("http://wms.example.test/loop", 1024)
    assert exc2.value.status_kod == 502
    assert antal == MAX_WMS_OMDIRIGERINGAR + 1


# ------------------------------------------------------------------ 2. Vy3dTjanst: projektbehörighet & cacheisolering


def _mock_vy3d(tmp_path: Path) -> tuple[Vy3dTjanst, set[str]]:
    """Bygger en lättviktig mockad Vy3dTjanst med två projekt (`proj-a`, `proj-b`) och varsin körning."""
    tillatna_projekt = {"proj-a", "proj-b"}

    # Skapa ett litet marklager på disk för korning-b (ägs av proj-b).
    k_b_mapp = tmp_path / "korningar" / "korning-b"
    (k_b_mapp / "webb").mkdir(parents=True)
    np.save(k_b_mapp / "webb" / "sol.npy", np.full((16, 16), 5.0, dtype=np.float32))

    korning_b = SimpleNamespace(
        korning_id="korning-b",
        projekt_id="proj-b",
        mapp=k_b_mapp,
        slut="2026-11-25T10:00:00Z",
    )
    sammanstallning_b = {"scenarier": ["bas"], "jobb": []}
    lager_b = [
        {
            "id": "sol",
            "roll": "soltimmar",
            "typ": "bas",
            "analys": "sol",
            "scenario": "bas",
            "enhet": "h",
            "pixelstorlek_m": 1.0,
            "utbredning": {"x_min": 0.0, "y_min": 0.0, "x_max": 16.0, "y_max": 16.0},
        }
    ]

    fragor = MagicMock()
    fragor.korningsmapp = tmp_path / "korningar"
    fragor.demo.return_value = {"finns": False, "delar": {}}
    fragor.korning_eller_none.side_effect = lambda kid: korning_b if kid == "korning-b" else None
    fragor._hamta.side_effect = lambda kid: (
        korning_b if kid == "korning-b" else (_ for _ in ()).throw(FlodenWebbFel(f"Okänd körning '{kid}'.", status_kod=404))
    )
    fragor._sammanstallning.return_value = sammanstallning_b
    fragor._lager.return_value = (lager_b, [])

    kartverktyg = MagicMock()

    def kravs_projekt(projekt: str) -> dict[str, Any]:
        if projekt not in tillatna_projekt:
            raise FlodenWebbFel(f"Okänt projekt '{projekt}'.", status_kod=404)
        return {}

    kartverktyg._kravs_projekt.side_effect = kravs_projekt

    tjanst = Vy3dTjanst(fragor, kartverktyg, cachemapp=tmp_path / "vy3d_cache")
    # Lägg in ett mockat _Terrang för båda projekten i cachen så vi kan testa cache-bypass.
    mock_terrang = MagicMock()
    mock_terrang.nyckel.return_value = "nyckel-delad"
    mock_terrang.utbredning = (0.0, 0.0, 128.0, 128.0)
    mock_terrang.hojdspann.return_value = (0.0, 10.0)
    mock_terrang.ruta.return_value = np.full((129, 129), 5.0, dtype=np.float64)
    tjanst._terrang_per_projekt["proj-a"] = mock_terrang
    tjanst._terrang_per_projekt["proj-b"] = mock_terrang
    return tjanst, tillatna_projekt


def test_vy3d_mark_ruta_och_korningsinfo_nekar_fel_projekt_aven_efter_cache(tmp_path: Path) -> None:
    tjanst, _ = _mock_vy3d(tmp_path)

    # Ägaren proj-b får ut data och fyller både minnes- och diskcachen.
    data_b = tjanst.mark_ruta("proj-b", "korning-b", "sol", 0, 0, 0)
    assert data_b[:4] == b"FL3D"
    assert tjanst.korningsinfo("proj-b", "korning-b")["korning"] == "korning-b"

    # proj-a får ALDRIG läsa proj-b:s cachade markruta eller körningsinfo!
    with pytest.raises(FlodenWebbFel) as exc1:
        tjanst.mark_ruta("proj-a", "korning-b", "sol", 0, 0, 0)
    assert exc1.value.status_kod == 404

    with pytest.raises(FlodenWebbFel) as exc2:
        tjanst.korningsinfo("proj-a", "korning-b")
    assert exc2.value.status_kod == 404

    # Okänt projekt nekas också.
    with pytest.raises(FlodenWebbFel) as exc3:
        tjanst.mark_ruta("okant", "korning-b", "sol", 0, 0, 0)
    assert exc3.value.status_kod == 404


def test_vy3d_byggnader_och_fasad_nekar_korsprojekt_korning(tmp_path: Path) -> None:
    tjanst, _ = _mock_vy3d(tmp_path)

    with pytest.raises(FlodenWebbFel) as exc1:
        tjanst.byggnader("proj-a", "korning-b", "bas")
    assert exc1.value.status_kod == 404

    with pytest.raises(FlodenWebbFel) as exc2:
        tjanst.fasad("proj-a", "korning-b", "bas", "vsc")
    assert exc2.value.status_kod == 404


def test_vy3d_terrang_och_terrang_ruta_kontrollerar_projekt_fore_cache_vid_aterkallad_atkomst(
    tmp_path: Path,
) -> None:
    tjanst, tillatna = _mock_vy3d(tmp_path)

    # Värm cachen för proj-a.
    ruta_bytes = tjanst.terrang_ruta("proj-a", 0, 0, 0)
    assert ruta_bytes[:4] == b"FL3D"
    assert tjanst.statistik()["beraknade"] == 1

    # Återkalla/ta bort åtkomsten till proj-a: cachat svar får INTE lämnas ut!
    tillatna.remove("proj-a")
    with pytest.raises(FlodenWebbFel) as exc1:
        tjanst.terrang("proj-a")
    assert exc1.value.status_kod == 404

    with pytest.raises(FlodenWebbFel) as exc2:
        tjanst.terrang_ruta("proj-a", 0, 0, 0)
    assert exc2.value.status_kod == 404
    # Cachen ska inte ha träffats vid det nekade anropet.
    assert tjanst.statistik()["ur_cache"] == 0
