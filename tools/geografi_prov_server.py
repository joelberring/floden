# ruff: noqa: E501
"""Startar en lokal Floden-webb med ett SYNTETISKT geografiprojekt (påhittad geografi `goteborg-test`, falsk Lantmäteri- och WFS-värld).

Används av `tools/ritverktyg_prov.py --geografi` för att prova flödet mot ett projekt i en geografi (inte demokvarteret) i webbläsaren:
rita, spara, välj frågor, kör, resultat. Allt är påhittat (samma värld som `tests/kommuner/test_skapa.py`), inget nät och inga riktiga data;
inloggningen är av, så projektet nås med sitt mappnamn (`haga-prov`). Kräver att `tests/` ligger bredvid `src/` i repot.

    python tools/geografi_prov_server.py [--port 8796] [--rot /tmp/floden-geoprov] [--klientmapp webb/dist]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "src"), str(REPO)]

PROJEKT_NAMN = "Haga prov"


def bygg(rot: Path) -> str:
    """Bygger världen (geografin v1) och ett projekt i den; returnerar projektets id (mappnamnet)."""
    from geografi.modell import STANDARD_GEOROT, las_manifest
    from kommuner.projekt import skapa_projekt
    from tests.kommuner.test_skapa import bygg_varld, mappa
    from tests.lantmateriet.falsk_server import FalskLm

    rot.mkdir(parents=True, exist_ok=True)
    server = FalskLm().start()
    try:
        v = bygg_varld(server, rot)
        mappa(v)
        manifest, _ = las_manifest(rot / "geo", "goteborg-test@v1")
        k = manifest["karna_3011"]
        avgr = (k[0] + 15.0, k[1] + 15.0, k[0] + 75.0, k[1] + 75.0)
        res = skapa_projekt(
            v["kommun"], ref="goteborg-test@v1", avgransning=avgr, namn=PROJEKT_NAMN, konto_id=None,
            geo_rot=rot / "geo", projektrot=rot / "projekt", konfig={}, konfig_fil=rot / "config.toml",
            skriv=lambda _: None,
        )  # fmt: skip
    finally:
        server.stop()
    # Webbservern letar efter geografier och projekt på standardplatserna relativt config.toml.
    for ny, gammal in ((STANDARD_GEOROT, "geo"), ("data/lokal/projekt", "projekt")):
        mal = rot / ny
        mal.parent.mkdir(parents=True, exist_ok=True)
        if not mal.exists():
            mal.symlink_to(rot / gammal, target_is_directory=True)
    return str(getattr(res, "id", None) or getattr(res, "projekt_id", None) or "haga-prov")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8796)
    ap.add_argument("--rot", default="/tmp/floden-geoprov")
    ap.add_argument("--klientmapp", default=str(REPO / "webb" / "dist"))
    a = ap.parse_args()
    rot = Path(a.rot)
    if not (rot / "projekt" / "haga-prov" / "projekt.json").is_file():
        print(f"Bygger den påhittade geografin och projektet i {rot} (tar en stund) ...", flush=True)
        pid = bygg(rot)
    else:
        pid = "haga-prov"
    from webb.api import skapa_http_server

    server = skapa_http_server(
        "127.0.0.1", a.port, testsvit_rot=REPO / "testsvit", konfig_fil=rot / "config.toml",
        fragemappar=[rot / "fragor"], cachemapp=rot / "cache", korningsmapp=rot / "korningar",
        klientmapp=a.klientmapp,
    )  # fmt: skip
    print(f"Syntetiskt geografiprojekt '{pid}': http://127.0.0.1:{a.port}/#/p/{pid}/forslag", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
