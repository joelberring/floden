# ruff: noqa: E501
"""End-to-end regressionstester för B12: rapportkedjan i steg 3 för läget idag + rapport.figurer."""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

import docx
import pytest
from PIL import Image

from rapport.figurer import FARG_BAKGRUND
from tests.demovarld.test_demo import Demo
from tests.konto.conftest import ANNA, Miljo, Webblasare
from webb.api import FlodenApiApp

REPO = Path(__file__).resolve().parents[2]
URSPRUNG = {"Origin": "https://floden.test"}
DEMO = "demokvarteret"


class _IsoleradVarld:
    """Egen tom korningsmapp (utan förkörda demokörningar) så läget idag prövas helt fristående."""

    def __init__(self, demo: Demo, miljo: Miljo, rot: Path) -> None:
        self.rot = rot
        self.app = FlodenApiApp(
            testsvit_rot=REPO / "testsvit",
            konfig_fil=REPO / "config.toml",
            fragemappar=[demo.fragor],
            cachemapp=demo.rot / "cache",
            korningsmapp=rot / "korningar",
            kartverktygsmapp=rot / "kartverktyg",
            datamappar=[rot / "data"],
            rapportmapp=rot / "rapporter",
            konto=miljo.adapter,
        )
        self.app.fragor.demomapp = demo.varld
        org = miljo.register.skapa_organisation("Exempelkommun")
        miljo.register.lagg_till_medlem(miljo.register.forbered_konto("anna@example.test"), org, None, "test")
        self.anna = miljo.webblasare()
        assert self.anna.logga_in(miljo.idp, ANNA)[0] == 302

    def http(
        self, w: Webblasare, metod: str, sokvag: str, kropp: Any = None
    ) -> tuple[int, dict[str, str], bytes]:
        rub: dict[str, str] = {"Cookie": "; ".join(f"{k}={v}" for k, v in w.kakor.items())}
        if metod != "GET":
            rub.update(URSPRUNG)
            rub.update(w.csrf_headers())
        data = b"" if kropp is None else json.dumps(kropp).encode()
        status, hs, k = self.app.hantera_http(metod, sokvag, rub, lambda: data)
        return status, {n.lower(): v for n, v in hs}, k

    def json(self, w: Webblasare, metod: str, sokvag: str, kropp: Any = None) -> tuple[int, dict[str, Any]]:
        s, _, k = self.http(w, metod, sokvag, kropp)
        return s, json.loads(k)

    def vanta_korning(self, kid: str, max_s: float = 600.0) -> None:
        t0 = time.monotonic()
        while time.monotonic() - t0 < max_s:
            st = self.app.fragor.korning_status(kid)["status"]
            if st in ("klar", "fel"):
                assert st == "klar", self.app.fragor.korning_status(kid)
                return
            time.sleep(0.25)
        raise AssertionError(f"Körning {kid} blev aldrig klar")

    def vanta_jobb(self, jobb: dict[str, Any], max_s: float = 600.0) -> dict[str, Any]:
        t0 = time.monotonic()
        while time.monotonic() - t0 < max_s:
            s, d = self.json(self.anna, "GET", jobb["status_url"])
            assert s == 200, d
            if d["status"] in ("klar", "fel"):
                return d
            time.sleep(0.25)
        raise AssertionError("Rapportjobbet blev aldrig klart")


@pytest.fixture
def varld(demo: Demo, miljo: Miljo, tmp_path: Path) -> _IsoleradVarld:
    return _IsoleradVarld(demo, miljo, tmp_path)


def test_steg3_utan_korningar_hanvisar_till_steg2_och_skapar_sedan_laget_idag_rapport_med_kartor(
    varld: _IsoleradVarld,
) -> None:
    """B12 E2E: utan körning hänvisas till steg 2; efter läget idag-körning skapas skyfall- och
    dagsljusrapporter utan 409, med minst en kartfigur per rapport ritad av rapport.figurer."""
    url = f"/api/projekt/{DEMO}/rapporter"

    # 1. Utan någon körning alls i projektet: förklaring och 409 pekar på steg 2 (ej steg 3).
    s0, moj = varld.json(varld.anna, "GET", url)
    assert s0 == 200
    per0 = {a["id"]: a for a in moj["amnen"]}
    assert per0["skyfall"]["kan_skapas"] is False
    assert "steg 2" in (per0["skyfall"]["forklaring"] or "")
    assert "steg 3" not in (per0["skyfall"]["forklaring"] or "")

    s_tom, fel_tom = varld.json(varld.anna, "POST", url, {"amne": "skyfall"})
    assert s_tom == 409
    assert "steg 2" in fel_tom["fel"] and "steg 3" not in fel_tom["fel"]

    # 2. Kör enbart läget idag (skyfall och dagsljus) – inga jämförelser, inget ritförslag.
    kor = varld.app.kartverktyg.kor_lage_idag(DEMO, ["vatten.skyfall", "dagsljus.sol"])
    kids = {f["mall"]: f["korning"]["korning_id"] for f in kor["fragor"]}
    for kid in kids.values():
        varld.vanta_korning(kid)

    s1, moj1 = varld.json(varld.anna, "GET", url)
    assert s1 == 200
    per1 = {a["id"]: a for a in moj1["amnen"]}
    assert per1["skyfall"]["kan_skapas"] is True
    assert per1["skyfall"]["forval_slag"] == "lage_idag"
    assert per1["skyfall"]["forval_korning"] == kids["vatten"]
    assert per1["dagsljus"]["kan_skapas"] is True
    assert per1["dagsljus"]["forval_slag"] == "lage_idag"
    assert per1["dagsljus"]["forval_korning"] == kids["dagsljus"]

    # 3. Skapa skyfallsrapport på läget idag utan att ange korning_id (förval) -> 202 (aldrig 409).
    s_sk, jobb_sk = varld.json(varld.anna, "POST", url, {"amne": "skyfall"})
    assert s_sk == 202, jobb_sk
    assert jobb_sk["korning_id"] == kids["vatten"]
    assert jobb_sk["underlag"]["slag"] == "lage_idag"
    klar_sk = varld.vanta_jobb(jobb_sk)
    assert klar_sk["status"] == "klar", klar_sk
    assert klar_sk["kontroll"] == {"godkand": True, "antal_fel": 0, "exporterad": True}

    rap_sk = json.loads(
        (varld.rot / "rapporter" / klar_sk["rapport_id"] / "rapport.json").read_text(encoding="utf-8")
    )
    assert len(rap_sk["kartor"]) >= 1, "Läget idag-rapporten får aldrig ha 0 kartor (SK-12)"
    k_sk = rap_sk["kartor"][0]
    assert k_sk["typ"] == "bas"
    assert k_sk["meta"]["kartmotor"] == "rapport.figurer"
    assert k_sk["meta"]["figurstil"] == "skyfall_djup"
    assert k_sk["meta"]["norr"] == "uppat" and float(k_sk["meta"]["skalstock_m"]) > 0
    png_sk = varld.rot / "rapporter" / klar_sk["rapport_id"] / k_sk["fil"]
    with Image.open(png_sk) as img:
        assert img.format == "PNG"
        assert img.getpixel((2, 2)) == FARG_BAKGRUND

    # Verifiera exporterade PDF- och Word-filer (inkl. inbäddad kartfigur i Word)
    s_pdf, _, pdf_bytes = varld.http(varld.anna, "GET", klar_sk["lankar"]["pdf"])
    s_docx, _, docx_bytes = varld.http(varld.anna, "GET", klar_sk["lankar"]["word"])
    assert s_pdf == 200 and pdf_bytes.startswith(b"%PDF")
    assert s_docx == 200 and docx_bytes.startswith(b"PK")
    assert len(docx.Document(io.BytesIO(docx_bytes)).inline_shapes) >= 1

    # 4. Skapa dagsljusrapport på läget idag med explicit korning_id -> 202, minst en solkarta via figurer.py.
    s_dl, jobb_dl = varld.json(
        varld.anna, "POST", url, {"amne": "dagsljus", "korning_id": kids["dagsljus"]}
    )
    assert s_dl == 202, jobb_dl
    klar_dl = varld.vanta_jobb(jobb_dl)
    assert klar_dl["status"] == "klar", klar_dl
    assert klar_dl["kontroll"] == {"godkand": True, "antal_fel": 0, "exporterad": True}

    rap_dl = json.loads(
        (varld.rot / "rapporter" / klar_dl["rapport_id"] / "rapport.json").read_text(encoding="utf-8")
    )
    assert len(rap_dl["kartor"]) >= 1, "Dagsljus läget idag-rapporten ska ha minst en solkarta (SO-15)"
    k_dl = rap_dl["kartor"][0]
    assert k_dl["meta"]["kartmotor"] == "rapport.figurer"
    assert k_dl["meta"]["figurstil"] == "dagsljus_soltimmar"
    assert "0,02 m" not in k_dl["bildtext"]
    png_dl = varld.rot / "rapporter" / klar_dl["rapport_id"] / k_dl["fil"]
    with Image.open(png_dl) as img:
        assert img.getpixel((2, 2)) == FARG_BAKGRUND
