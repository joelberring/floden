# ruff: noqa: E501
"""Skissrapport (K33) över demons vattenkörning: referensflöde, kontroll med inplanterade fel och
export till Word och PDF. Talen jämförs med samma källa som MCP-servern (`FlodenTjanst`)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from floden_mcp.dataklass import DataKlass, Policy
from floden_mcp.karna_api import FlodenTjanst
from rapport.pastaende import PastaendeFel, formatera_tal, standardsteg
from rapport.referensflode import JFR, gardens_mottagare, pastaenden
from rapport.skissexport import ExportStoppad
from rapport.skissrapport import KONTRAKT_K33, Rapport, RapportFel, RapportTjanst
from tests.demovarld.test_demo import Demo
from webb.api import FlodenApiApp

REPO = Path(__file__).resolve().parents[2]
NB = " "


class Miljo:
    def __init__(self, demo: Demo, rot: Path) -> None:
        self.app = FlodenApiApp(
            testsvit_rot=REPO / "testsvit",
            konfig_fil=REPO / "config.toml",
            fragemappar=[demo.fragor],
            cachemapp=demo.rot / "cache",
            korningsmapp=demo.rot / "korningar",
            kartverktygsmapp=rot / "kartverktyg",
            datamappar=[rot / "data"],
            rapportmapp=rot / "rapporter",
        )
        self.app.fragor.demomapp = demo.varld
        self.tjanst = FlodenTjanst(self.app, rot=REPO, mcp_mapp=rot / "mcp")
        self.rt = RapportTjanst(self.tjanst, lokala_forval={})

    def ny_rapport(self, **over: Any) -> Rapport:
        arg: dict[str, Any] = {
            "projekt": "demokvarteret",
            "amne": "skyfall",
            "korningar": ["demo-vatten"],
            "mottagare": [gardens_mottagare()],
            "ai_stod": False,
            **over,
        }
        return self.rt.skapa(**arg)

    def full_rapport(self) -> Rapport:
        r = self.ny_rapport()
        for kw in pastaenden():
            self.rt.lagg_till_pastaende(r.rapport_id, forfattare="referensflode", **kw)
        return self.rt.hamta(r.rapport_id)

    def json_fil(self, r: Rapport) -> Path:
        return self.rt.arkiv.rapportmapp(r.rapport_id) / "rapport.json"

    def andra(self, r: Rapport, fn: Any) -> None:
        f = self.json_fil(r)
        d = json.loads(f.read_text(encoding="utf-8"))
        fn(d)
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def fel(self, r: Rapport, punkt: str) -> list[str]:
        k = self.rt.kontrollera(r.rapport_id)
        p = next(x for x in k["punkter"] if x["id"] == punkt)
        return list(p["fel"])


@pytest.fixture(scope="module")
def m(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Miljo]:
    yield Miljo(demo, tmp_path_factory.mktemp("rapport"))


@pytest.fixture(scope="module")
def exporterad(m: Miljo) -> dict[str, Any]:
    r = m.full_rapport()
    word = m.rt.exportera(r.rapport_id, "word")
    pdf = m.rt.exportera(r.rapport_id, "pdf")
    return {"rapport": m.rt.hamta(r.rapport_id), "word": word, "pdf": pdf}


def _text_docx(fil: Path) -> str:
    import docx

    d = docx.Document(str(fil))
    delar = [p.text for p in d.paragraphs]
    for t in d.tables:
        for rad in t.rows:
            delar += [c.text for c in rad.cells]
    return "\n".join(delar)


def _text_pdf(fil: Path) -> tuple[int, list[str]]:
    import pypdf

    r = pypdf.PdfReader(str(fil))
    return len(r.pages), [p.extract_text() or "" for p in r.pages]


def _norm(s: str) -> str:
    return " ".join(s.replace(NB, " ").replace("\u202f", " ").split())


# ------------------------------------------------------------------ K33 och belägg


def test_k33_rundtur_och_belagg_ur_samma_kalla_som_mcp(exporterad: dict[str, Any], m: Miljo) -> None:
    r: Rapport = exporterad["rapport"]
    d = r.till_dict()
    assert d["kontrakt"] == KONTRAKT_K33
    assert Rapport.fran_dict(json.loads(json.dumps(d))) == r
    assert r.fragor == tuple(f"SK-{i:02d}" for i in range(1, 16))
    assert {k.typ for k in r.kartor} == {"bas", "scenario", "skillnad"}
    assert r.metoder[0]["losare"] == "vatten.statisk_fyllning"
    assert r.metoder[0]["status"] == "skiss"
    # SK-06: talen är exakt MCP:s jämförelse på mark (takmask), före avrundning
    jamf = next(j for j in m.tjanst.jamfor("demo-vatten")["jamforelser"] if j["namn"] == JFR)
    p = next(p for p in r.pastaenden if p.fraga_id == "SK-06")
    per = p.belagg_per_adress
    for matt in ("yta_okning_m2", "max_okning", "yta_minskning_m2"):
        b = per[f"jamforelse.{JFR}.{matt}"]
        assert b.varde == jamf[matt]["varde"]
        assert b.namnare.startswith("mark")
        assert b.fil.endswith("diff.tif") and b.fil_sha256 and len(b.manifest_sha256) == 64
    assert jamf["tak_maskat"] is True


def test_referensflodet_ger_godkand_kontroll(exporterad: dict[str, Any]) -> None:
    k = exporterad["rapport"].kontroll
    assert k["godkand"], k
    assert [p["id"] for p in k["punkter"]] == [
        "sparbarhet", "scenario", "mottagare", "matt", "osakerhet", "karta", "status",
        "dataskydd", "metodstatus",
    ]  # fmt: skip


def test_word_innehaller_ratt_tal(exporterad: dict[str, Any], m: Miljo) -> None:
    text = _norm(_text_docx(exporterad["word"]))
    jamf = next(j for j in m.tjanst.jamfor("demo-vatten")["jamforelser"] if j["namn"] == JFR)
    v = jamf["yta_okning_m2"]["varde"]
    forvantat = formatera_tal(v, "m²", standardsteg("m²", v, 4.0))
    assert _norm(forvantat) in text
    assert _norm(formatera_tal(jamf["max_okning"]["varde"], "m", 0.01)) in text
    for p in exporterad["rapport"].pastaenden:
        assert _norm(p.fylld_text()) in text
    for rubrik in ("Sammanfattning", "Bilaga 1 Belägg", "Bilaga 2 Körlogg", "Bilaga 3 Manifest"):
        assert rubrik in text
    assert "vatten.statisk_fyllning" in text and "skiss" in text
    import docx

    assert len(docx.Document(str(exporterad["word"])).inline_shapes) == 3


def test_pdf_har_sidor_och_text(exporterad: dict[str, Any]) -> None:
    sidor, texter = _text_pdf(exporterad["pdf"])
    allt = _norm(" ".join(texter))
    assert 6 <= sidor <= 12
    for i, t in enumerate(texter, start=1):
        assert f"Sida {i} ({sidor})" in t
    forsta_bilaga = next(i for i, t in enumerate(texter) if "Bilaga 1 Belägg" in t)
    # smal textspalt (svensk rapportlayout) ger något fler sidor än full bredd
    assert forsta_bilaga <= 7, "skissunderlaget ska vara högst sju sidor före bilagorna"
    assert "Skissunderlag skyfall" in allt
    for p in exporterad["rapport"].pastaenden:
        # pdf-texten bryter rader; jämför första tolv orden
        ord_ = _norm(p.fylld_text()).replace("⁻", "−").split()[:6]
        assert " ".join(ord_) in allt, ord_


def test_export_ar_reproducerbar(m: Miljo, exporterad: dict[str, Any]) -> None:
    r: Rapport = exporterad["rapport"]
    from rapport.skissexport import skriv_pdf, skriv_word
    from rapport.skissmall import bygg_dokument

    mapp = m.rt.arkiv.rapportmapp(r.rapport_id)
    dok = bygg_dokument(r, m.rt)
    a, b = mapp / "a.pdf", mapp / "b.pdf"
    skriv_pdf(dok, a, mapp)
    skriv_pdf(dok, b, mapp)
    assert a.read_bytes() == b.read_bytes()
    c, d = mapp / "c.docx", mapp / "d.docx"
    skriv_word(dok, c, mapp)
    skriv_word(dok, d, mapp)
    assert c.read_bytes() == d.read_bytes()


# ------------------------------------------------------------------ påståenden vägras


@pytest.mark.parametrize(
    ("kw", "fel"),
    [
        (
            {"fraga_id": "SK-06", "status": "visat", "fordjupning": "ja",
             "text": "Vattnet blir 20 cm djupare.", "belagg": []},
            "kommer inte ur något belägg",
        ),
        (
            {"fraga_id": "SK-06", "status": "visat", "fordjupning": "ja",
             "text": "Okänd {{matt:jamforelse.finns_inte.max_okning}}.",
             "belagg": ["jamforelse.finns_inte.max_okning"]},
            "Belägget vägras",
        ),
        (
            {"fraga_id": "SK-12", "status": "visat", "fordjupning": "ja",
             "text": "Vattennivån blir {{matt:matvarde.skyfall.efter.max_djup_m}}.",
             "belagg": ["matvarde.skyfall.efter.max_djup_m"]},
            "inte en vattennivå",
        ),
        (
            {"fraga_id": "SK-06", "status": "visat", "fordjupning": "ja",
             "text": f"Ytan är {{{{matt:jamforelse.{JFR}.yta_okning_m2}}}} och volymen "
             "{{matt:matvarde.skyfall.efter.lagrat_m3}}.",
             "belagg": [f"jamforelse.{JFR}.yta_okning_m2", "matvarde.skyfall.efter.lagrat_m3"]},
            "olika nämnare",
        ),
        (
            {"fraga_id": "SK-06", "status": "visat", "fordjupning": "nej",
             "text": f"Förslaget uppfyller kravet: {{{{matt:jamforelse.{JFR}.max_okning}}}}.",
             "belagg": [f"jamforelse.{JFR}.max_okning"]},
            "bedömning mot krav",
        ),
        (
            {"fraga_id": "SK-06", "status": "visat", "fordjupning": "ja",
             "text": f"Ökning {{{{matt:jamforelse.{JFR}.max_okning|avrundning=0,0001}}}}.",
             "belagg": [f"jamforelse.{JFR}.max_okning"]},
            "finare än underlaget",
        ),
        (
            {"fraga_id": "SK-06", "status": "visat", "fordjupning": "ja",
             "text": f"Ökningen är verifierad: {{{{matt:jamforelse.{JFR}.max_okning}}}}.",
             "belagg": [f"jamforelse.{JFR}.max_okning"]},
            "skiss",
        ),
        (
            {"fraga_id": "LU-01", "status": "ej_tillampligt", "fordjupning": "nej",
             "text": "Luft.", "motivering": "m"},
            "ingår inte i rapporten",
        ),
        (
            {"fraga_id": "SK-07", "status": "visat", "fordjupning": "ja",
             "text": "Max {{matt:omrade.okand.skyfall.efter.max_djup.max}}.",
             "belagg": ["omrade.okand.skyfall.efter.max_djup.max"]},
            "Mottagaren 'okand' finns inte",
        ),
        (
            {"fraga_id": "SK-02", "status": "villkorat", "fordjupning": "ja",
             "text": "Regnet {{matt:matvarde.skyfall.efter.regn_mm}}.",
             "belagg": ["matvarde.skyfall.efter.regn_mm"], "forutsattning": "Beställaren väljer."},
            "ansvarar",
        ),
    ],
)
def test_pastaenden_utan_giltiga_belagg_vagras(m: Miljo, kw: dict[str, Any], fel: str) -> None:
    r = m.ny_rapport()
    with pytest.raises(PastaendeFel, match=fel):
        m.rt.lagg_till_pastaende(r.rapport_id, **kw)
    assert m.rt.hamta(r.rapport_id).pastaenden == ()


# ------------------------------------------------------------------ inplanterade fel stoppar exporten


def _forvanska_varde(d: dict[str, Any]) -> None:
    b = next(p for p in d["pastaenden"] if p["fraga_id"] == "SK-06")["belagg"][0]
    b["varde"] = b["varde"] * 1.1


def test_inplanterat_fel_i_ett_tal_stoppar_exporten(m: Miljo) -> None:
    r = m.full_rapport()
    m.andra(r, _forvanska_varde)
    exportmapp = m.rt.arkiv.rapportmapp(r.rapport_id) / "export"
    with pytest.raises(ExportStoppad) as ei:
        m.rt.exportera(r.rapport_id, "pdf")
    text = str(ei.value)
    assert "Exporten stoppades" in text
    assert "stämmer inte med körningen demo-vatten" in text
    assert "Spåra tal och figurer: FEL" in text
    assert not exportmapp.exists() or not any(exportmapp.iterdir())
    lagrad = m.rt.hamta(r.rapport_id).kontroll
    assert lagrad is not None and lagrad["godkand"] is False


def test_egen_siffra_inplanterad_i_texten_stoppas(m: Miljo) -> None:
    r = m.full_rapport()

    def fn(d: dict[str, Any]) -> None:
        d["pastaenden"][0]["text"] += " Djupet ökar med 15 cm."

    m.andra(r, fn)
    assert any("'15'" in f for f in m.fel(r, "sparbarhet"))


def test_andrad_karta_och_kartregler_stoppas(m: Miljo) -> None:
    r = m.full_rapport()
    fil = m.rt.arkiv.rapportmapp(r.rapport_id) / r.kartor[0].fil
    fil.write_bytes(fil.read_bytes() + b"x")
    assert any("filen har ändrats" in f for f in m.fel(r, "sparbarhet"))

    def fn(d: dict[str, Any]) -> None:
        d["kartor"][0]["bildtext"] = "Karta."
        d["kartor"][1]["meta"]["skala"] = {"min": 0.0, "max": 9.0}
        d["kartor"][2]["meta"]["norr"] = "nedat"

    m.andra(r, fn)
    fel = " ".join(m.fel(r, "karta"))
    assert "tomvärdet" in fel and "olika färgskalor" in fel and "norr" in fel


def test_fraga_utan_pastaende_och_statusregel_stoppas(m: Miljo) -> None:
    r = m.full_rapport()
    sista = r.pastaenden[-1].pastaende_id
    m.rt.ta_bort_pastaende(r.rapport_id, sista)

    def fn(d: dict[str, Any]) -> None:
        p = next(p for p in d["pastaenden"] if p["fraga_id"] == "SK-09")
        p["motivering"] = None

    m.andra(r, fn)
    fel = " ".join(m.fel(r, "status"))
    assert "SK-15 saknar påstående" in fel
    assert "saklig motivering" in fel


def test_mottagare_och_matt_inplanterade_stoppas(m: Miljo) -> None:
    r = m.full_rapport()

    def fn(d: dict[str, Any]) -> None:
        p = next(p for p in d["pastaenden"] if p["fraga_id"] == "SK-01")
        p["belagg"][1]["namnare"] = "markceller i mottagaren garden"
        p["text"] = p["text"].replace("största vattendjupet", "högsta vattennivån")

    m.andra(r, fn)
    assert any("olika nämnare" in f for f in m.fel(r, "mottagare"))
    assert any("vattennivå" in f for f in m.fel(r, "matt"))


def test_dataklass_och_metodstatus_stoppas(m: Miljo) -> None:
    r = m.full_rapport()

    def fn(d: dict[str, Any]) -> None:
        d["metoder"][0]["status"] = "rapport"

    m.andra(r, fn)
    assert any("valideringsstatus har ändrats" in f for f in m.fel(r, "metodstatus"))
    intern = RapportTjanst(
        m.tjanst,
        policy=Policy(projektklasser={"demokvarteret": DataKlass.INTERNT}),
    )
    k = intern.kontrollera(r.rapport_id)
    fel = " ".join(next(p for p in k["punkter"] if p["id"] == "dataskydd")["fel"])
    assert "dataklassen 'internt'" in fel
    with pytest.raises(ExportStoppad):
        intern.exportera(r.rapport_id, "word")


def test_scenario_med_olika_parametrar_stoppas(m: Miljo, monkeypatch: pytest.MonkeyPatch) -> None:
    r = m.full_rapport()
    orig = m.rt.kalla.jobbparametrar

    def andrad(kid: str, analys: str, scen: str) -> dict[str, Any]:
        p = orig(kid, analys, scen)
        return {**p, "klimatfaktor": 1.25} if scen == "efter" else p

    monkeypatch.setattr(m.rt.kalla, "jobbparametrar", andrad)
    fel = " ".join(m.fel(r, "scenario"))
    assert "olika analysparametrar" in fel


def test_skapa_valideras(m: Miljo) -> None:
    with pytest.raises(RapportFel, match="Bara nivå 1"):
        m.ny_rapport(niva=2)
    with pytest.raises(RapportFel, match="ingen skissrapport"):
        m.ny_rapport(amne="luft")
    with pytest.raises(RapportFel, match="ingen vattenanalys"):
        m.ny_rapport(korningar=["demo-dagsljus"])
    with pytest.raises(RapportFel, match="hör inte till ämnet"):
        m.ny_rapport(fragor=["LU-01"])


# ------------------------------------------------------------------ webben


def test_webben_listar_rapporter_och_ger_filer(m: Miljo, exporterad: dict[str, Any]) -> None:
    rid = exporterad["rapport"].rapport_id
    status, _h, kropp = m.app.hantera_forfragan("GET", "/api/projekt/demokvarteret")
    assert status == 200
    rader = {x["rapport_id"]: x for x in json.loads(kropp)["rapporter"]}
    assert rader[rid]["kontroll"]["godkand"] is True
    assert rader[rid]["filer"] == {"word": f"{rid}.docx", "pdf": f"{rid}.pdf"}
    status, h, kropp = m.app.hantera_forfragan(
        "GET", f"/api/projekt/demokvarteret/rapporter/{rid}/fil/pdf"
    )
    assert status == 200 and h["Content-Type"] == "application/pdf" and kropp[:5] == b"%PDF-"
    assert "attachment" in h["Content-Disposition"]
    assert m.app.hantera_forfragan("GET", f"/api/projekt/demokvarteret/rapporter/{rid}/fil/xls")[0] == 404
    assert m.app.hantera_forfragan("GET", "/api/projekt/demokvarteret/rapporter/r-000000000000/fil/pdf")[0] == 404

