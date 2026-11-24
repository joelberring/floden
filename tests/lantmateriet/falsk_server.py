"""Falsk Lantmäteriet: token, STAC (hojd, bild, vektor) och filnedladdning på 127.0.0.1.

Ingen riktig förfrågan till Lantmäteriet görs i testerna. Servern modellerar det protokoll som
O-Tid (MIT, joelberring/otid @ 4a446af) använder: OAuth2 client credentials, `POST /search` med
WGS 84-bbox, nedladdning med Bearer.
"""

# ruff: noqa: E501
from __future__ import annotations

import base64
import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

KLIENT_ID = "test-klient-id-4711"
HEMLIGHET = "test-hemlighet-svart-häst-0815"
TOKEN = "tok-abc123-hemlig"


@dataclass
class Logg:
    begaranden: list[dict[str, Any]] = field(default_factory=list)
    token_anrop: int = 0
    token_nr: int = 0
    giltiga_tokens: set[str] = field(default_factory=set)


class FalskLm:
    def __init__(self) -> None:
        self.logg = Logg()
        self.filer: dict[str, bytes] = {}  # sökväg -> innehåll
        self.sidor: list[list[dict[str, Any]]] = []  # STAC-träffar per sida (hojd)
        self.bild_features: list[dict[str, Any]] = []
        self.vektor_features: list[dict[str, Any]] = []
        self.samlingar: dict[str, list[dict[str, str]]] = {
            "hojd": [
                {"id": "markhojdmodell", "title": "Markhöjdmodell"},
                {"id": "laserdata-skog", "title": "Laserdata skog"},
            ],
            "bild": [
                {"id": "ortofoto-historiska", "title": "Ortofoto historiska"},
                {"id": "ortofoto", "title": "Ortofoto"},
            ],
            "vektor": [
                {"id": "byggnad-inspire", "title": "Byggnad INSPIRE"},
                {"id": "byggnad", "title": "Byggnad"},
            ],
        }
        self.token_expires_in = 3600
        self.token_status = 200
        self.sok_status_foerst: list[int] = []  # statuskoder att svara med före riktigt svar
        self.retry_after: str | None = None
        self.fil_status_foerst: list[int] = []
        self.omdirigera: dict[str, str] = {}
        self.cirkel = False  # nästa-länken pekar alltid på samma sida
        self.avvisa_alla = False  # 403 på alla sök/fil-anrop
        self.avvisa_tjanster: set[str] = set()  # "hojd", "bild", "vektor": 403 för en produkts tjänst
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self._httpd.server_address[1]
        self.bas = f"http://127.0.0.1:{self.port}"
        self._tr = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self) -> FalskLm:
        self._tr.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def tjanster(self) -> Any:
        from lantmateriet.klient import Tjanster

        return Tjanster(
            token=f"{self.bas}/oauth2/token",
            hojd=f"{self.bas}/stac-hojd/v1",
            bild=f"{self.bas}/stac-bild/v1",
            vektor=f"{self.bas}/stac-vektor/v1",
        )

    def antal(self, metod: str, prefix: str) -> int:
        return sum(
            1 for b in self.logg.begaranden if b["metod"] == metod and b["path"].startswith(prefix)
        )

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:  # tyst
                pass

            def _svar(
                self,
                status: int,
                kropp: bytes = b"",
                typ: str = "application/json",
                extra: dict[str, str] | None = None,
            ) -> None:
                self.send_response(status)
                self.send_header("Content-Type", typ)
                self.send_header("Content-Length", str(len(kropp)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(kropp)

            def _las_kropp(self) -> bytes:
                n = int(self.headers.get("Content-Length", "0") or 0)
                return self.rfile.read(n) if n else b""

            def _logga(self, kropp: bytes) -> None:
                server.logg.begaranden.append(
                    {
                        "metod": self.command,
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "range": self.headers.get("Range"),
                        "kropp": kropp,
                    }
                )

            def _auth_ok(self) -> bool:
                a = self.headers.get("Authorization") or ""
                return a.startswith("Bearer ") and a[7:] in server.logg.giltiga_tokens

            def do_POST(self) -> None:
                kropp = self._las_kropp()
                self._logga(kropp)
                if self.path == "/oauth2/token":
                    server.logg.token_anrop += 1
                    forvantat = base64.b64encode(f"{KLIENT_ID}:{HEMLIGHET}".encode()).decode()
                    if (
                        server.token_status != 200
                        or self.headers.get("Authorization") != f"Basic {forvantat}"
                        or kropp != b"grant_type=client_credentials"
                    ):
                        self._svar(401, b'{"error":"invalid_client"}')
                        return
                    server.logg.token_nr += 1
                    tok = f"{TOKEN}-{server.logg.token_nr}"
                    server.logg.giltiga_tokens.add(tok)
                    self._svar(
                        200,
                        json.dumps(
                            {
                                "access_token": tok,
                                "token_type": "Bearer",
                                "expires_in": server.token_expires_in,
                            }
                        ).encode(),
                    )
                    return
                if not self._auth_ok():
                    self._svar(401)
                    return
                if server.avvisa_alla:
                    self._svar(403)
                    return
                if server.sok_status_foerst:
                    st = server.sok_status_foerst.pop(0)
                    extra = {"Retry-After": server.retry_after} if server.retry_after else None
                    self._svar(st, b"{}", extra=extra)
                    return
                if any(self.path == f"/stac-{n}/v1/search" for n in server.avvisa_tjanster):
                    self._svar(403)
                    return
                for nyckel, plats in (
                    ("hojd", "/stac-hojd/v1/search"),
                    ("bild", "/stac-bild/v1/search"),
                    ("vektor", "/stac-vektor/v1/search"),
                ):
                    if self.path == plats:
                        self._stac(nyckel, json.loads(kropp))
                        return
                self._svar(404)

            def _stac(self, nyckel: str, body: dict[str, Any]) -> None:
                if nyckel == "hojd":
                    sida = body.get("token", "1")
                    nr = int(sida)
                    feats = server.sidor[nr - 1]
                    links: list[dict[str, Any]] = []
                    if nr < len(server.sidor) or server.cirkel:
                        links.append(
                            {
                                "rel": "next",
                                "method": "POST",
                                "href": f"{server.bas}/stac-hojd/v1/search",
                                "body": {"token": str(nr if server.cirkel else nr + 1)},
                                "merge": True,
                            }
                        )
                    self._svar(
                        200,
                        json.dumps(
                            {"type": "FeatureCollection", "features": feats, "links": links}
                        ).encode(),
                    )
                    return
                andra = server.bild_features if nyckel == "bild" else server.vektor_features
                self._svar(
                    200,
                    json.dumps(
                        {"type": "FeatureCollection", "features": andra, "links": []}
                    ).encode(),
                )

            def do_GET(self) -> None:
                self._logga(b"")
                p = self.path.split("?")[0]
                if p in server.omdirigera:
                    self.send_response(302)
                    self.send_header("Location", server.omdirigera[p])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if p.startswith("/lagring/"):  # mål för omdirigering: kräver INGA nycklar
                    inn = server.filer.get(p)
                    if inn is None:
                        self._svar(404)
                        return
                    self._svar(200, inn, "application/octet-stream")
                    return
                if not self._auth_ok():
                    self._svar(401)
                    return
                if server.avvisa_alla:
                    self._svar(403)
                    return
                for nyckel in ("hojd", "bild", "vektor"):
                    if p == f"/stac-{nyckel}/v1/collections":
                        if nyckel in server.avvisa_tjanster:
                            self._svar(403)
                            return
                        self._svar(
                            200, json.dumps({"collections": server.samlingar[nyckel]}).encode()
                        )
                        return
                if p.startswith("/files/"):
                    if server.fil_status_foerst:
                        st = server.fil_status_foerst.pop(0)
                        extra = {"Retry-After": "0"}
                        self._svar(st, b"", extra=extra)
                        return
                    inn = server.filer.get(p)
                    if inn is None:
                        self._svar(404)
                        return
                    rng = self.headers.get("Range")
                    if rng and rng.startswith("bytes="):
                        start = int(rng[6:].split("-")[0])
                        if start >= len(inn):
                            self._svar(416)
                            return
                        d = inn[start:]
                        self._svar(
                            206,
                            d,
                            "application/octet-stream",
                            {"Content-Range": f"bytes {start}-{len(inn) - 1}/{len(inn)}"},
                        )
                        return
                    self._svar(200, inn, "application/octet-stream")
                    return
                self._svar(404)

        return H
