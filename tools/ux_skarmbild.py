# ruff: noqa: E501
"""Skärmbilder till UX-genomgången (docs/UX-GENOMGANG.md): alla vyer, bred 1440x900 och smal 390x844, till docs/bilder/ux/.

Förutsätter `floden demo` och `floden webb --port 8791` (inloggning av) och byggd webb. Nycklar och Administration kräver
inloggning: kontosvaren (`/konto/...`) **svaras av webbläsarens rutt med påhittade värden** (påhittat konto, ingenting sparas).

    python tools/ux_skarmbild.py [--url http://127.0.0.1:8791] [--chrome /usr/bin/google-chrome] [--ut docs/bilder/ux]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import Route, sync_playwright

UT = Path(__file__).resolve().parents[1] / "docs" / "bilder" / "ux"
STORLEKAR = [("", 1440, 900, False), ("-smal", 390, 844, True)]
VYER = [
    ("01-projektlista", "/", 6000, False),
    ("02-nytt-projekt", "/nytt", 3000, False),
    ("02c-nytt-saknar-roll", "/nytt", 3000, True),  # påhittat: inloggad utan rätt att skapa projekt (U4)
    ("02d-nytt-natverksfel", "/nytt", 3000, True),  # påhittat: servern nås inte (U4)
    ("03-forslag", "/p/demokvarteret/forslag", 6000, False),
    ("04-fragor", "/p/demokvarteret/fragor", 4000, True),
    ("04b-fragor-vy", "/p/demokvarteret/fragor", 4000, False),  # bara fönstret: visar den fasta handlingsraden (U4)
    ("05-resultat", "/p/demokvarteret/resultat", 6000, False),
    ("06-rapport", "/p/demokvarteret/rapport", 3000, False),
    ("07-nycklar", "/nycklar", 2500, True),
    ("08-administration", "/administration", 2500, True),
    ("09-matematik", "/matematik", 3000, False),
    ("10-webgpu", "/prov/webgpu", 2500, False),
    ("11-projektsida", "/projekt/demokvarteret", 6000, False),  # omdirigeras till /p/demokvarteret/forslag (U3)
    ("12-utredningsfragor", "/fragor", 3000, False),  # frågekatalogen (U3)
    ("13-om", "/om", 3000, False),
]
ORG = {"id": "o-1", "namn": "Påhittad förvaltning", "din_roll": "organisationsadmin", "far_byta_namn": True,
       "roller": ["medlem", "ledare", "organisationsadmin", "agare"], "tillatna_roller": ["medlem", "ledare"]}  # fmt: skip
SVAR: dict[str, object] = {
    "/konto/jag": {
        "inloggning": "pa",
        "konto": {
            "namn": "Pia Planarkitekt",
            "epost": "pia@example.org",
            "superadmin": False,
            "admin": True,
        },
        "medlemskap": [
            {
                "organisation": "Påhittad förvaltning",
                "forvaltning": None,
                "roll": "organisationsadmin",
            }
        ],
        "projekt": [{"id": "demokvarteret", "namn": "Demokvarteret", "roll": "utredare"}],
    },  # fmt: skip
    "/konto/nycklar": {
        "paslagen": True,
        "orsak": None,
        "text": None,
        "typer": [
            {
                "typ": "lantmateriet",
                "namn": "Lantmäteriet",
                "beskrivning": "Nyckel och hemlighet för Lantmäteriets öppna data.",
                "falt": [
                    {"id": "klient_id", "namn": "Klient-id", "krav": True, "val": []},
                    {"id": "hemlighet", "namn": "Hemlighet", "krav": True, "val": []},
                ],
                "sparad": None,
                "sparad_datum": None,
                "slutar_pa": None,
                "produkter": [
                    {
                        "roll": "laser",
                        "namn": "Laserdata Nedladdning, skog",
                        "anvands_till": "Höjdmodell",
                        "behovs": True,
                        "anmarkning": "",
                    }
                ],
            },
            {
                "typ": "anthropic",
                "namn": "Anthropic",
                "beskrivning": "Nyckel för AI-stödet i rapporter.",
                "falt": [{"id": "api_nyckel", "namn": "API-nyckel", "krav": True, "val": []}],
                "sparad": 1764000000,
                "sparad_datum": "2026-11-23",
                "slutar_pa": "ab12",
            },
        ],
    },  # fmt: skip
    "/konto/mcp-token": {"tokens": [], "max": 5, "mcp_url": "https://floden.example.org/mcp"},
    "/konto/oauth": {"anslutningar": []},
    "/konto/admin/projekt": {
        "projekt": [
            {
                "id": "demokvarteret",
                "namn": "Demokvarteret",
                "organisation_id": "o-1",
                "organisation": "Påhittad förvaltning",
                "medlemmar": 2,
                "oppna_inbjudningar": 1,
                "organisation_synlig": False,
                "kan_andra_delning": True,
            }
        ]
    },  # fmt: skip
    "/konto/admin/projekt/demokvarteret": {
        "projekt": {
            "id": "demokvarteret",
            "namn": "Demokvarteret",
            "organisation_id": "o-1",
            "organisation": "Påhittad förvaltning",
            "medlemmar": 2,
            "oppna_inbjudningar": 1,
            "organisation_synlig": False,
            "kan_andra_delning": True,
        },
        "roller": ["lasare", "skribent", "utredare"],
        "medlemmar": [
            {
                "konto_id": "k1",
                "namn": "Pia Planarkitekt",
                "epost": "pia@example.org",
                "roll": "utredare",
                "kalla": "projekt",
                "sparrad": False,
            },
            {
                "konto_id": "k2",
                "namn": "Olle Handläggare",
                "epost": "olle@example.org",
                "roll": "lasare",
                "kalla": "projekt",
                "sparrad": False,
            },
        ],
        "inbjudningar": [
            {
                "id": "i1",
                "epost": "ny@example.org",
                "roll": "lasare",
                "skapad": 1764000000,
                "giltig_till": 1765000000,
                "status": "oppen",
            }
        ],
    },  # fmt: skip
    "/konto/admin/organisationer": {"organisationer": [ORG]},
    "/konto/admin/organisationer/o-1": {
        "organisation": ORG,
        "roller": ORG["roller"],
        "medlemmar": [
            {
                "konto_id": "k1",
                "namn": "Pia Planarkitekt",
                "epost": "pia@example.org",
                "roll": "organisationsadmin",
                "kalla": "org",
                "sparrad": False,
            }
        ],
        "inbjudningar": [],
    },  # fmt: skip
}


# Påhittade svar för tillstånden i Nytt projekt (webbläsarens rutt; ingenting skickas till servern).
EXTRA: dict[str, dict[str, tuple[int, object]]] = {
    "02c-nytt-saknar-roll": {
        "/api/geografier": (403, {"fel": "Du får inte skapa projekt.", "status": 403}),
    },
    "02d-nytt-natverksfel": {"/api/geografier": (0, {})},
}


def _extra(namn: str, route: Route) -> None:
    sokvag = "/" + route.request.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
    status, kropp = EXTRA[namn].get(sokvag, (-1, None))
    if status == 0:
        route.abort()
    elif status > 0:
        route.fulfill(status=status, content_type="application/json", body=json.dumps(kropp))
    else:
        _svara(route) if sokvag.startswith("/konto/") else route.continue_()


def _svara(route: Route) -> None:
    sokvag = route.request.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
    nyckel = "/" + sokvag
    if nyckel in SVAR:
        route.fulfill(status=200, content_type="application/json", body=json.dumps(SVAR[nyckel]))
    else:
        route.continue_()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8791")
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    ap.add_argument("--ut", default=str(UT))
    ap.add_argument("--bara", default="", help="del av filnamnet, t.ex. 04")
    a = ap.parse_args()
    ut = Path(a.ut)
    ut.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        wl = p.chromium.launch(
            executable_path=a.chrome,
            args=[
                "--no-sandbox",
                "--use-gl=angle",
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
            ],
        )
        for namn, adress, vanta, inloggad in VYER:
            if a.bara and a.bara not in namn:
                continue
            for suffix, bredd, hojd, hel in STORLEKAR:
                sida = wl.new_page(viewport={"width": bredd, "height": hojd})
                fel: list[str] = []
                sida.on("pageerror", lambda e, f=fel: f.append(str(e)))
                if namn in EXTRA:
                    sida.route("**/konto/**", lambda r, _q, n=namn: _extra(n, r))
                    sida.route("**/api/geografier", lambda r, _q, n=namn: _extra(n, r))
                elif inloggad:
                    sida.route("**/konto/**", _svara)
                sida.goto(f"{a.url}/#{adress}")
                sida.wait_for_timeout(vanta)
                sida.screenshot(path=str(ut / f"{namn}{suffix}.png"), full_page=hel and not namn.endswith("-vy"))
                print(f"{namn}{suffix}.png", "sidfel:" + "; ".join(fel) if fel else "")
                sida.close()
        wl.close()


if __name__ == "__main__":
    main()
