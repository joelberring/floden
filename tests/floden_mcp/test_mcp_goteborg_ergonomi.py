# ruff: noqa: E501
"""Tester för PR-3: MCP-ergonomi, enheter (`belagg.py`), K38-säker `rapport_export_hamta`,
projekt-bbox som standardutsnitt och `markslag == 8`-vattenmask i `karta_bild`."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import numpy as np
import pytest
from mcp import Client
from PIL import Image
from starlette.testclient import TestClient

from floden_mcp import server as server_mod
from floden_mcp.belagg import enhet_for
from floden_mcp.dataklass import DataKlass, Policy
from floden_mcp.http import KontoSkydd
from floden_mcp.karna_api import MARKSLAG_VATTEN
from floden_mcp.server import bygg_server
from rapport.figurer import FARG_VATTEN, FARG_VATTEN_KANT
from rapport.referensflode import gardens_mottagare, pastaenden
from rapport.skissrapport import RapportTjanst
from tests.demovarld.test_demo import Demo
from tests.floden_mcp.test_mcp import Mcp
from tests.floden_mcp.test_mcp_http import BO, Stack, _initiera, anropa_verktyg
from tests.floden_mcp.test_oppenhet import _kopiera_korning
from tests.floden_mcp.test_v1_mcp_sakerhet import _skriv_projekt, _token
from tests.konto.conftest import ANNA, Miljo
from tests.konto.test_projekt_per_anvandare_hjalp import setup_projekt
from testsvit.k1_io import las_geotiff, skriv_geotiff


@pytest.fixture(scope="module")
def mcp_ergo(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Mcp]:
    rot = tmp_path_factory.mktemp("mcp_ergonomi")
    m = Mcp(demo, rot)
    m.app.rapportmapp = rot / "rapporter"
    m.server = bygg_server(m.tjanst, logg=m.logg, miljo={"FLODEN_MCP_MODELL": "testmodell"})
    yield m


def _exportera_oppen_demorapport(mcp: Mcp) -> tuple[str, dict[str, Any], dict[str, Any]]:
    r = mcp.ok(
        "rapport_skapa",
        {"korningar": ["demo-vatten"], "mottagare": [gardens_mottagare()]},
    )
    rid = str(r["rapport_id"])
    for kw in pastaenden():
        arg: dict[str, Any] = {"rapport_id": rid, "text_med_platshallare": kw["text"]}
        arg.update({k: v for k, v in kw.items() if k != "text"})
        res = mcp.anropa("pastaende_lagg_till", arg)
        assert not res.is_error, res.content[0].text
    assert mcp.ok("rapport_kontrollera", {"rapport_id": rid})["godkand"]
    pdf_exp = mcp.ok("rapport_exportera", {"rapport_id": rid, "format": "pdf"})
    docx_exp = mcp.ok("rapport_exportera", {"rapport_id": rid, "format": "word"})
    return rid, pdf_exp, docx_exp


# ------------------------------------------------------------------ P0-3b: BestallningFel


def test_bestallning_skapa_fangar_bestallningfel_rent_som_fel_indata(mcp_ergo: Mcp) -> None:
    """P0-3b: Ogiltig beställning ger ett rent `ToolError` med `[floden]fel_indata`, aldrig ohanterat serverfel."""
    for argument, forvantad_text in (
        ({"syfte": "", "amnen": ["skyfall"]}, "Ange syftet"),
        ({"syfte": "Test", "amnen": ["okant_amne"]}, "Okänt ämne"),
        (
            {
                "syfte": "Test av ogiltigt scenarionamn",
                "amnen": ["skyfall"],
                "scenario": {"fore": "bas", "efter": ["ogiltigt namn!"]},
            },
            "scenario.efter",
        ),
    ):
        r = mcp_ergo.anropa("bestallning_skapa", argument)
        assert r.is_error, argument
        text = str(r.content[0].text)  # type: ignore[union-attr]
        assert "[floden]fel_indata" in text, text
        assert forvantad_text in text, text
        assert "BestallningFel" not in text, text
        sista = mcp_ergo.logg.las(verktyg="bestallning_skapa")[-1]
        assert sista["utfall"] == "fel"
        assert sista["fel"] != "BestallningFel"
        assert "[floden]fel_indata" in str(sista["fel"])

    # Även okänt bestallning_id i rapport_skapa utan korningar/amne ger rent fel_indata.
    r_bad = mcp_ergo.anropa("rapport_skapa", {"bestallning_id": "inte-ett-id"})
    assert r_bad.is_error
    assert "[floden]fel_indata" in str(r_bad.content[0].text)  # type: ignore[union-attr]
    assert mcp_ergo.logg.verifiera()[0]


# ------------------------------------------------------------------ P1-2: belagg.enhet_for


@pytest.mark.parametrize(
    "nyckel",
    [
        "svf_min",
        "dagsljus_svf_min",
        "svf_marken_min",
        "vsc_min",
        "pc_min",
        "x_min",
        "y_min",
        "z_min",
        "tau_min",
    ],
)
def test_enhet_for_ger_inte_minuter_for_minimivarden(nyckel: str) -> None:
    """P1-2: Minimimått som `svf_min` och `vsc_min` får aldrig enheten `'min'`."""
    assert enhet_for(nyckel) is None


@pytest.mark.parametrize(
    ("nyckel", "forvantad"),
    [
        ("varaktighet_min", "min"),
        ("varaktigheter_min", "min"),
        ("steg_min", "min"),
        ("tidssteg_min", "min"),
        ("insatstidsmal_min", "min"),
        ("tid_min", "min"),
        ("slut_tid_min", "min"),
        ("topp_tid_min", "min"),
        ("max_djup_m", "m"),
        ("regn_mm", "mm"),
        ("avtappning_mm_h", "mm/h"),
        ("kortid_s", "s"),
        ("yta_okning_m2", "m²"),
        ("aterkomsttid_ar", "år"),
    ],
)
def test_enhet_for_bevarar_tidsnycklar_i_minuter_och_ovriga_enheter(
    nyckel: str, forvantad: str
) -> None:
    assert enhet_for(nyckel) == forvantad


def test_resultat_sammanfattning_har_ingen_min_enhet_for_svf_min(mcp_ergo: Mcp) -> None:
    s_vatten = mcp_ergo.ok("resultat_sammanfattning", {"korning_id": "demo-vatten"})
    assert s_vatten["matvarden"][0]["enheter"].get("varaktighet_min") == "min"
    s_dag = mcp_ergo.ok("resultat_sammanfattning", {"korning_id": "demo-dagsljus"})
    for tab in s_dag["matvarden"]:
        assert tab["enheter"].get("svf_min") is None
        assert tab["enheter"].get("vsc_min") is None


# ------------------------------------------------------------------ P2-1a: kor analyser-dokumentation & hint


def test_kor_dokumenterar_analyser_och_ger_tydlig_hint_vid_okand_analys(mcp_ergo: Mcp) -> None:
    async def _hamta_kor() -> Any:
        async with Client(mcp_ergo.server) as c:
            tools = await c.list_tools()
            return next(t for t in tools.tools if t.name == "kor")

    kor_tool = anyio.run(_hamta_kor)
    beskr = str(kor_tool.description or "")
    assert "dagsljus.sol" in beskr and "vatten.skyfall" in beskr
    analyser_prop = kor_tool.input_schema["properties"]["analyser"]
    assert "dagsljus.sol" in str(analyser_prop.get("description", ""))

    r = mcp_ergo.anropa("kor", {"lage_idag": True, "analyser": ["skyfall"]})
    assert r.is_error
    text = str(r.content[0].text)  # type: ignore[union-attr]
    assert "Okänd analys 'skyfall'" in text
    assert "vatten.skyfall" in text and "dagsljus.sol" in text


# ------------------------------------------------------------------ P1-3: projekt-bbox & vattenmask i karta_bild


def test_karta_bild_zoomar_till_projektets_avgransning_som_standard(
    demo: Demo, tmp_path: Path
) -> None:
    """P1-3b: Utan `utsnitt` beskär `karta_bild` till projektets `avgransning_3011` (~10 % marginal),
    medan uttryckligt `utsnitt` alltid respekteras och `demokvarteret` behåller hela utbredningen."""
    mcp = Mcp(demo, tmp_path / "m")
    projektrot = tmp_path / "projekt"
    mcp.app.projektmapp = projektrot
    mcp.app.fragor.projektrot = projektrot
    mcp.app.kartverktyg.projektrot = projektrot
    mcp.server = bygg_server(mcp.tjanst, policy=Policy(), logg=mcp.logg, miljo={})

    # Referens: full utbredning på demo-vatten (demokvarteret saknar avgransning_3011).
    full_res = mcp.anropa("karta_bild", {"korning_id": "demo-vatten", "analys": "skyfall", "typ": "bas"})
    assert not full_res.is_error
    rx0, ry0, rx1, ry1 = full_res.structured_content["kartutsnitt"]
    rw, rh = rx1 - rx0, ry1 - ry0

    # Skapa ett öppet projekt med en tydligt mindre avgransning_3011 inne i rastret.
    pid = "p-gbg-utsnitt-001"
    avgr = [rx0 + 0.30 * rw, ry0 + 0.25 * rh, rx0 + 0.60 * rw, ry0 + 0.65 * rh]
    pmapp = _skriv_projekt(
        projektrot,
        "gbg-haga-utsnitt",
        pid,
        oppen=True,
        fraga_namn="gbg-skyfall",
        bas=demo.varld / "vatten" / "bas",
    )
    pdoc = json.loads((pmapp / "projekt.json").read_text(encoding="utf-8"))
    pdoc["avgransning_3011"] = avgr
    (pmapp / "projekt.json").write_text(json.dumps(pdoc), encoding="utf-8")

    # Kopiera demo-vatten och koppla körningen till projektet.
    kid = "k-gbg-utsnitt-test"
    ny = demo.rot / "korningar" / kid
    shutil.copytree(demo.rot / "korningar" / "demo-vatten", ny)
    try:
        kdoc = json.loads((ny / "korning.json").read_text(encoding="utf-8"))
        kdoc["korning_id"] = kid
        kdoc["projekt_id"] = pid
        (ny / "korning.json").write_text(json.dumps(kdoc), encoding="utf-8")

        # 1. Utan utsnitt -> fokus på avgransning_3011 (+ ~10 % marginal), tydligt mindre än hela lagret.
        auto = mcp.anropa("karta_bild", {"korning_id": kid, "analys": "skyfall", "typ": "bas"})
        assert not auto.is_error, auto.content[0].text
        ux0, uy0, ux1, uy1 = auto.structured_content["kartutsnitt"]
        px = float(auto.structured_content["cellstorlek_m"])
        assert ux0 <= avgr[0] and uy0 <= avgr[1] and ux1 >= avgr[2] and uy1 >= avgr[3]
        assert (ux1 - ux0) < rw - 2 * px
        assert (uy1 - uy0) < rh - 2 * px
        aw, ah = avgr[2] - avgr[0], avgr[3] - avgr[1]
        assert ux0 == pytest.approx(max(rx0, avgr[0] - 0.10 * aw), abs=px)
        assert ux1 == pytest.approx(min(rx1, avgr[2] + 0.10 * aw), abs=px)
        assert uy0 == pytest.approx(max(ry0, avgr[1] - 0.10 * ah), abs=px)
        assert uy1 == pytest.approx(min(ry1, avgr[3] + 0.10 * ah), abs=px)

        # 2. Med uttryckligt utsnitt -> anroparens utsnitt gäller direkt.
        eget = [rx0 + 0.05 * rw, ry0 + 0.05 * rh, rx0 + 0.25 * rw, ry0 + 0.25 * rh]
        exp = mcp.anropa(
            "karta_bild",
            {"korning_id": kid, "analys": "skyfall", "typ": "bas", "utsnitt": eget},
        )
        assert not exp.is_error, exp.content[0].text
        ex0, ey0, ex1, ey1 = exp.structured_content["kartutsnitt"]
        assert ex0 == pytest.approx(eget[0], abs=px) and ex1 == pytest.approx(eget[2], abs=px)
        assert ey0 == pytest.approx(eget[1], abs=px) and ey1 == pytest.approx(eget[3], abs=px)
    finally:
        shutil.rmtree(ny, ignore_errors=True)


def test_karta_bild_maskar_permanent_vatten_ur_markslag_8(demo: Demo, tmp_path: Path) -> None:
    """P1-3a: Skyfallskartor (bas och skillnad) laddar `markslag.tif` (`markslag == 8`) från K1-basen
    och ritar permanent vatten i `FARG_VATTEN` med teckenförklaringen `'Vattenyta'`."""
    mcp = Mcp(demo, tmp_path / "m")
    # Skapa en öppen kopia av K1-basen där ett tydligt band har markslag == 8 (vattenyta).
    k1_kopia = tmp_path / "oppen_k1_vatten"
    shutil.copytree(demo.varld / "vatten" / "bas", k1_kopia)
    (k1_kopia / "oppenhet.toml").write_text(
        'kontrakt = "floden-oppenhet/v1"\noppen = true\nlicens = "CC0-1.0"\nkalla = "test"\n',
        encoding="utf-8",
    )
    markslag_filer = sorted(k1_kopia.rglob("markslag.tif"))
    assert markslag_filer
    for mf in markslag_filer:
        arr, geo = las_geotiff(mf)
        ny_arr = np.asarray(arr, dtype=np.uint8).copy()
        h, b = ny_arr.shape
        ny_arr[h // 4 : h // 2, b // 4 : b // 2] = MARKSLAG_VATTEN
        skriv_geotiff(
            mf,
            ny_arr,
            x_min=geo.x_min,
            y_max=geo.y_max,
            pixelstorlek=geo.pixelstorlek_x,
        )

    kid = "k-vattenmask-test"
    ny = demo.rot / "korningar" / kid
    shutil.copytree(demo.rot / "korningar" / "demo-vatten", ny)
    fraga_fil = demo.fragor / f"{kid}.json"
    try:
        fraga = {
            "kontrakt": "K20/v1",
            "fraga_id": kid,
            "beskrivning": "Test vattenmask",
            "scenarier": {"fore": {"bas": str(k1_kopia)}, "efter": {"bas": str(k1_kopia)}},
            "analyser": [
                {
                    "id": "skyfall",
                    "losare": "vatten.statisk_fyllning",
                    "parametrar": {
                        "idf": "stockholmsregn-2024",
                        "aterkomsttid_ar": 100,
                        "varaktighet_min": 360,
                        "klimatfaktor": 1.4,
                        "regntyp": "block",
                        "takvatten": True,
                    },
                }
            ],
        }
        fraga_fil.write_text(json.dumps(fraga), encoding="utf-8")
        run = ny / "resultat" / "run.json"
        manifest = json.loads(run.read_text(encoding="utf-8"))
        for post in manifest["indata"]:
            if post["roll"] == "fraga":
                post["fil"] = str(fraga_fil)
        run.write_text(json.dumps(manifest), encoding="utf-8")
        kdoc = json.loads((ny / "korning.json").read_text(encoding="utf-8"))
        kdoc["korning_id"] = kid
        kdoc["ref"] = kid
        (ny / "korning.json").write_text(json.dumps(kdoc), encoding="utf-8")

        vatten_rgb = np.array(FARG_VATTEN, dtype=np.uint8)
        kant_rgb = np.array(FARG_VATTEN_KANT, dtype=np.uint8)
        for typ in ("bas", "skillnad"):
            r = mcp.anropa("karta_bild", {"korning_id": kid, "analys": "skyfall", "typ": typ})
            assert not r.is_error, r.content[0].text
            bild = next(c for c in r.content if c.type == "image")
            png = base64.b64decode(bild.data)
            with Image.open(io.BytesIO(png)) as img:
                arr_img = np.asarray(img.convert("RGB"))
            assert int(np.all(arr_img == vatten_rgb, axis=-1).sum()) > 500, typ
            assert int(np.all(arr_img == kant_rgb, axis=-1).sum()) > 20, typ
    finally:
        shutil.rmtree(ny, ignore_errors=True)
        fraga_fil.unlink(missing_ok=True)


# ------------------------------------------------------------------ P1-5: rapport_export_hamta


def test_rapport_export_hamta_returnerar_exakt_base64_och_sha256_utan_binardata_i_korlogg(
    mcp_ergo: Mcp,
) -> None:
    """P1-5: Öppen rapport hämtas byteidentiskt för både PDF och Word utan binärinnehåll i körloggen."""
    rid, pdf_exp, docx_exp = _exportera_oppen_demorapport(mcp_ergo)

    for fmt, exp, mime, andelse in (
        ("pdf", pdf_exp, "application/pdf", ".pdf"),
        (
            "word",
            docx_exp,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".docx",
        ),
    ):
        svar = mcp_ergo.ok("rapport_export_hamta", {"rapport_id": rid, "format": fmt})
        assert svar["dataklass"] == "oppet"
        assert svar["rapport_id"] == rid
        assert svar["format"] == fmt
        assert svar["filnamn"] == f"{rid}{andelse}"
        assert "/" not in svar["filnamn"] and "\\" not in svar["filnamn"]
        assert svar["mime_type"] == mime
        assert svar["sha256"] == exp["sha256"]
        assert svar["storlek_byte"] == exp["storlek_byte"]
        ra = base64.b64decode(svar["innehall_base64"])
        assert ra == Path(exp["fil"]).read_bytes()
        assert hashlib.sha256(ra).hexdigest() == svar["sha256"]

        # Körloggen har hashkedjad rad för anropet, men inget binärt/base64-innehåll.
        loggtext = mcp_ergo.logg.sokvag.read_text(encoding="utf-8")
        assert svar["innehall_base64"][:40] not in loggtext

    rader = [r for r in mcp_ergo.logg.las(verktyg="rapport_export_hamta") if rid in r["rapport_id"]]
    assert len(rader) == 2
    assert all(r["utfall"] == "ok" and r["dataklass"] == "oppet" for r in rader)
    assert mcp_ergo.logg.verifiera()[0]


def test_rapport_export_hamta_nekar_ogiltigt_format_path_traversal_symlink_tamper_och_storlek(
    demo: Demo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-5 säkerhet: fail-closed på ogiltigt format, path traversal, symlänk, ändrad SHA-256 och >20 MB."""
    mcp = Mcp(demo, tmp_path / "m")
    mcp.app.rapportmapp = tmp_path / "rapporter"
    mcp.server = bygg_server(mcp.tjanst, policy=Policy(), logg=mcp.logg, miljo={})
    rid, pdf_exp, _ = _exportera_oppen_demorapport(mcp)
    rt = RapportTjanst(mcp.tjanst, rapportmapp=mcp.app.rapportmapp)

    # 1. Ogiltigt format nekas direkt.
    for daligt_fmt in ("html", "../pdf", "PDF", ""):
        r_fmt = mcp.anropa("rapport_export_hamta", {"rapport_id": rid, "format": daligt_fmt})
        assert r_fmt.is_error and "[floden]fel_indata" in str(r_fmt.content[0].text), daligt_fmt

    # 2. Storlekstak (testas med litet tak via monkeypatch).
    monkeypatch.setattr(server_mod, "MAX_EXPORT_HAMTA_BYTE", 1024)
    r_stor = mcp.anropa("rapport_export_hamta", {"rapport_id": rid, "format": "pdf"})
    assert r_stor.is_error and "[floden]for_stor" in str(r_stor.content[0].text)
    monkeypatch.undo()

    # 3. Path traversal i exportmetadata nekas utan att läsa utanför export/.
    orig = rt.arkiv.las(rid)
    hemlig = tmp_path / "hemlig.pdf"
    hemlig.write_bytes(b"%PDF-1.4 HEMLIGT_UTANFOR_EXPORT")
    hemlig_sha = hashlib.sha256(hemlig.read_bytes()).hexdigest()
    for ond_sokvag in ("../rapport.json", str(hemlig), "export/../../hemlig.pdf", "export\\a.pdf"):
        rt.arkiv.spara(
            orig.med(
                exporter=(
                    {"format": "pdf", "fil": ond_sokvag, "sha256": hemlig_sha, "tid": "2026-11-27T12:00:00+00:00"},
                )
            )
        )
        r_tr = mcp.anropa("rapport_export_hamta", {"rapport_id": rid, "format": "pdf"})
        assert r_tr.is_error and "[floden]nekad_sokvag" in str(r_tr.content[0].text), ond_sokvag
        assert "HEMLIGT_UTANFOR_EXPORT" not in str(r_tr.content)
    rt.arkiv.spara(orig)

    # 4. Symlänk i export/-mappen nekas även om SHA-256 stämmer.
    pdf_fil = Path(pdf_exp["fil"])
    pdf_bytes = pdf_fil.read_bytes()
    pdf_fil.unlink()
    os.symlink(hemlig, pdf_fil)
    rt.arkiv.spara(
        orig.med(
            exporter=(
                {
                    "format": "pdf",
                    "fil": f"export/{rid}.pdf",
                    "sha256": hemlig_sha,
                    "tid": "2026-11-27T12:00:00+00:00",
                },
            )
        )
    )
    r_sym = mcp.anropa("rapport_export_hamta", {"rapport_id": rid, "format": "pdf"})
    assert r_sym.is_error and "[floden]nekad_sokvag" in str(r_sym.content[0].text)
    assert "HEMLIGT_UTANFOR_EXPORT" not in str(r_sym.content)

    # Återställ vanlig fil och manipulera 1 byte -> SHA-256-avvikelse nekas fail-closed.
    pdf_fil.unlink()
    pdf_fil.write_bytes(pdf_bytes + b"X")
    rt.arkiv.spara(orig)
    r_sha = mcp.anropa("rapport_export_hamta", {"rapport_id": rid, "format": "pdf"})
    assert r_sha.is_error and "[floden]ogiltig" in str(r_sha.content[0].text)
    assert mcp.logg.verifiera()[0]


def test_rapport_export_hamta_k38_fail_closed_for_stangt_och_icke_oppet_projekt(
    demo: Demo, tmp_path: Path
) -> None:
    """P1-5 K38 fail-closed: nekas för stängt projekt, stängd körning, dataklass != 'oppet',
    samt geografi med `oppen_for_mcp = false` (även för en intern klient)."""
    projektrot = tmp_path / "projekt"
    geomapp = tmp_path / "geografier"
    stangt_pid = "1" * 32
    geo_stangd_pid = "2" * 32
    internt_pid = "3" * 32
    okant_pid = "4" * 32

    _skriv_projekt(
        projektrot, "sthlm-stangt", stangt_pid, oppen=False, fraga_namn="s1", bas=demo.varld / "vatten" / "bas"
    )
    pm_geo = _skriv_projekt(
        projektrot, "gbg-stangd-geo", geo_stangd_pid, oppen=True, fraga_namn="g1", bas=demo.varld / "vatten" / "bas"
    )
    pdoc = json.loads((pm_geo / "projekt.json").read_text(encoding="utf-8"))
    pdoc["geografi"] = "stangd-geo@v1"
    (pm_geo / "projekt.json").write_text(json.dumps(pdoc), encoding="utf-8")
    gdir = geomapp / "stangd-geo" / "v1"
    gdir.mkdir(parents=True)
    (gdir / "manifest.json").write_text(
        json.dumps({"kontrakt": "floden-geografi/v1", "id": "stangd-geo", "version": "v1", "oppen_for_mcp": False}),
        encoding="utf-8",
    )

    _skriv_projekt(
        projektrot, "proj-internt", internt_pid, oppen=True, fraga_namn="i1", bas=demo.varld / "vatten" / "bas"
    )

    policy = Policy(
        standard=frozenset({DataKlass.OPPET}),
        per_klient={"intern-klient": frozenset({DataKlass.OPPET, DataKlass.INTERNT})},
        projektklasser={
            "demokvarteret": DataKlass.OPPET,
            internt_pid: DataKlass.INTERNT,
            okant_pid: DataKlass.OPPET,
        },
    )
    mcp = Mcp(demo, tmp_path / "m", policy=policy)
    mcp.app.rapportmapp = tmp_path / "rapporter"
    mcp.app.projektmapp = projektrot
    mcp.app.fragor.projektrot = projektrot
    mcp.app.kartverktyg.projektrot = projektrot
    mcp.app.kartverktyg.geomapp = geomapp
    mcp.server = bygg_server(mcp.tjanst, policy=policy, logg=mcp.logg, miljo={})

    rid_bas, pdf_exp, _ = _exportera_oppen_demorapport(mcp)
    rt = RapportTjanst(mcp.tjanst, rapportmapp=mcp.app.rapportmapp, policy=policy)
    bas_obj = rt.arkiv.las(rid_bas)
    bas_pdf = Path(pdf_exp["fil"]).read_bytes()

    def _klona_rapport(ny_rid: str, **andringar: Any) -> str:
        ny_r = bas_obj.med(
            rapport_id=ny_rid,
            exporter=(
                {
                    "format": "pdf",
                    "fil": f"export/{ny_rid}.pdf",
                    "sha256": pdf_exp["sha256"],
                    "tid": "2026-11-27T12:00:00+00:00",
                },
            ),
            **andringar,
        )
        rt.arkiv.spara(ny_r)
        ut = rt.arkiv.rapportmapp(ny_rid) / "export" / f"{ny_rid}.pdf"
        ut.parent.mkdir(parents=True, exist_ok=True)
        ut.write_bytes(bas_pdf)
        return ny_rid

    stangd_bas = tmp_path / "data" / "lokal" / "baskarta_hemlig" / "k1"
    stangd_bas.mkdir(parents=True)
    _kopiera_korning(demo, mcp, "stangd-export-k1", stangd_bas)

    r_stangt_proj = _klona_rapport("r-111100001111", projekt=stangt_pid)
    r_geo_stangd = _klona_rapport("r-222200002222", projekt=geo_stangd_pid)
    r_internt_proj = _klona_rapport("r-333300003333", projekt=internt_pid, dataklass="internt")
    r_okant_proj = _klona_rapport("r-444400004444", projekt=okant_pid)
    r_stangd_korning = _klona_rapport(
        "r-555500005555",
        korningar=tuple({**k, "korning_id": "stangd-export-k1"} for k in bas_obj.korningar),
    )
    r_intern_rapport = _klona_rapport("r-666600006666", dataklass="internt")

    for rid in (
        r_stangt_proj,
        r_geo_stangd,
        r_internt_proj,
        r_okant_proj,
        r_stangd_korning,
        r_intern_rapport,
    ):
        # Även intern klient med utökad policy nekas fail-closed!
        r = mcp.anropa("rapport_export_hamta", {"rapport_id": rid, "format": "pdf"}, klient="intern-klient")
        assert r.is_error, rid
        assert r.structured_content is None
        assert "innehall_base64" not in str(r.content)

    assert mcp.logg.verifiera()[0]


def test_rapport_export_hamta_kraver_minst_lasare_i_rapportens_oppna_projekt(
    demo: Demo, miljo: Miljo, tmp_path: Path
) -> None:
    """P1-5 RBAC: `lasare` i rapportens öppna projekt tillåts; användare utan roll i projektet nekas."""
    ids = setup_projekt(miljo)  # Anna: lasare i anna_projekt; Bo: utredare i bo_projekt
    ta, tb = _token(miljo, ANNA), _token(miljo, BO)

    projektrot = tmp_path / "projekt"
    _skriv_projekt(
        projektrot,
        "anna-oppet-exp",
        ids.anna_projekt,
        oppen=True,
        fraga_namn="anna-skyfall",
        bas=demo.varld / "vatten" / "bas",
    )

    policy = Policy(projektklasser={"demokvarteret": DataKlass.OPPET})
    stack = Stack(demo, miljo, tmp_path, behorighet=True, policy=policy)
    stack.mcp.app.rapportmapp = tmp_path / "rapporter"
    stack.mcp.app.projektmapp = projektrot
    stack.mcp.app.fragor.projektrot = projektrot
    stack.mcp.app.kartverktyg.projektrot = projektrot
    stack.server = bygg_server(
        stack.mcp.tjanst,
        policy=policy,
        logg=stack.logg,
        miljo={},
        behorighet=miljo.adapter.har_roll,
    )
    skydd = KontoSkydd(stack.server.streamable_http_app(host="0.0.0.0"), miljo.adapter)  # noqa: S104

    rt = RapportTjanst(stack.mcp.tjanst, rapportmapp=stack.mcp.app.rapportmapp, policy=policy)
    r_anna = rt.skapa(
        projekt=ids.anna_projekt,
        amne="skyfall",
        korningar=["demo-vatten"],
        mottagare=[gardens_mottagare()],
    )
    for kw in pastaenden():
        rt.lagg_till_pastaende(
            r_anna.rapport_id,
            fraga_id=kw["fraga_id"],
            status=kw["status"],
            text=kw["text"],
            belagg=kw.get("belagg", ()),
            fordjupning=kw["fordjupning"],
            mottagare=kw.get("mottagare"),
            forutsattning=kw.get("forutsattning"),
            ansvarig_funktion=kw.get("ansvarig_funktion"),
            motivering=kw.get("motivering"),
            saknat_underlag=kw.get("saknat_underlag", ()),
        )
    pdf_fil = rt.exportera(r_anna.rapport_id, "pdf")
    forvantad_sha = hashlib.sha256(pdf_fil.read_bytes()).hexdigest()

    with TestClient(skydd, base_url="http://floden.test") as klient:
        a = _initiera(klient, {"Authorization": f"Bearer {ta}"})
        b = _initiera(klient, {"Authorization": f"Bearer {tb}"})

        # Anna har lasare i sitt öppna projekt -> tillåts hämta PDF:en!
        fel_a, text_a, svar_a = anropa_verktyg(
            klient, a, "rapport_export_hamta", {"rapport_id": r_anna.rapport_id, "format": "pdf"}
        )
        assert not fel_a, text_a
        assert svar_a["dataklass"] == "oppet"
        assert svar_a["sha256"] == forvantad_sha
        assert base64.b64decode(svar_a["innehall_base64"]) == pdf_fil.read_bytes()

        # Bo har ingen roll i Annas projekt -> nekas som okänd rapport!
        fel_b, text_b, _ = anropa_verktyg(
            klient, b, "rapport_export_hamta", {"rapport_id": r_anna.rapport_id, "format": "pdf"}
        )
        assert fel_b and text_b == f"Okänd rapport '{r_anna.rapport_id}'."

    assert stack.logg.verifiera()[0]
