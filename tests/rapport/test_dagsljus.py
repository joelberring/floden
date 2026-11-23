# ruff: noqa: E501
"""Skissunderlag dagsljus och sol (R2): beställning (K30), frågekatalogens DL/SO-tabell,
referensflöde över demons dagsljuskörning, kontroll och export. Talen jämförs med samma källa som
MCP-servern (`FlodenTjanst`)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from rapport.bestallning import Bestallning, BestallningFel, skapa_bestallning
from rapport.fragekatalog import (
    FLODEN_FORMAGA,
    Fragekatalog,
    formagetabell_markdown,
    fraga_med_formaga,
)
from rapport.pastaende import PastaendeFel
from rapport.referensflode import gardens_mottagare
from rapport.referensflode_dagsljus import kontekst_fran_rapport, pastaenden
from rapport.skissexport import ExportStoppad
from rapport.skissrapport import Rapport, RapportFel
from tests.demovarld.test_demo import Demo
from tests.rapport.test_skissrapport import Miljo, _norm, _text_docx, _text_pdf

REPO = Path(__file__).resolve().parents[2]
DLSO = tuple(f"DL-{i:02d}" for i in range(1, 16)) + tuple(f"SO-{i:02d}" for i in range(1, 16))
SYFTE = "Skissa hur ett höjt hus och ett nytt hus påverkar sol på marken och dagsljus vid fasad"


@pytest.fixture(scope="module")
def m(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Miljo]:
    yield Miljo(demo, tmp_path_factory.mktemp("dagsljus"))


def _rapport(m: Miljo, *, alla: bool = False, roll: str = "planhandlaggare", **over: Any) -> Rapport:
    best = m.rt.bestallning_skapa(
        syfte=SYFTE,
        amnen=["dagsljus", "sol"],
        projekt="demokvarteret",
        mottagare_roll=roll,
        bara_med_underlag=not alla,
        scenario={"fore": "bas", "efter": ["hojd", "nytt_hus"]},
    )
    r = m.rt.skapa(
        projekt="demokvarteret",
        korningar=["demo-dagsljus"],
        mottagare=[gardens_mottagare()],
        ai_stod=False,
        bestallning=best,
        **over,
    )
    for kw in pastaenden(kontekst_fran_rapport(m.rt, r), r.fragor):
        m.rt.lagg_till_pastaende(r.rapport_id, forfattare="referensflode", **kw)
    return m.rt.hamta(r.rapport_id)


@pytest.fixture(scope="module")
def alla(m: Miljo) -> Rapport:
    return _rapport(m, alla=True)


@pytest.fixture(scope="module")
def exporterad(m: Miljo) -> dict[str, Any]:
    r = _rapport(m)
    return {
        "rapport": r,
        "word": m.rt.exportera(r.rapport_id, "word"),
        "pdf": m.rt.exportera(r.rapport_id, "pdf"),
    }


# ------------------------------------------------------------------ frågekatalogen


def test_dl_och_so_ar_kartlagda_och_tabellen_stammer_med_koden() -> None:
    k = Fragekatalog.las()
    assert {f.id for f in k.for_amne("dagsljus")} == set(DLSO)
    assert set(DLSO) <= set(FLODEN_FORMAGA)
    doc = (REPO / "docs" / "RAPPORTER.md").read_text(encoding="utf-8")
    assert formagetabell_markdown("dagsljus") in doc
    for f in DLSO:
        fm = FLODEN_FORMAGA[f]
        assert fm.markning in {"visat", "delvis", "underlag_saknas", "ej_tillampligt"}
        # inget 'visat' i demon utan belägg; underlag saknas anger vad som saknas
        if fm.forval_status == "visat":
            assert fm.adresser, f
        if fm.forval_status == "underlag_saknas":
            assert fm.saknat, f
    markning = {f: FLODEN_FORMAGA[f].markning for f in DLSO}
    assert {f for f, v in markning.items() if v == "visat"} == {"DL-10", "DL-15", "SO-09", "SO-15"}
    assert {f for f, v in markning.items() if v == "ej_tillampligt"} == {"DL-01", "DL-09", "DL-14"}


def test_fraga_med_formaga_ger_markning_och_adresser() -> None:
    f = Fragekatalog.las().hamta("DL-10")
    d = fraga_med_formaga(f)
    assert d["floden"]["markning"] == "visat" and d["floden"]["adresser"]
    assert fraga_med_formaga(Fragekatalog.las().hamta("LU-01"))["floden"] is None


# ------------------------------------------------------------------ beställning (K30)


def test_bestallningens_urval_och_rundtur(m: Miljo) -> None:
    k = Fragekatalog.las()
    b = skapa_bestallning(k, syfte=SYFTE, amnen=["dagsljus", "sol"], projekt="demokvarteret")
    assert set(b.urval) <= set(DLSO) and b.utelamnade == 30 - len(b.urval)
    med = skapa_bestallning(
        k, syfte=SYFTE, amnen=["dagsljus", "sol"], projekt="demokvarteret", bara_med_underlag=True
    )
    assert len(med.urval) == 15 and all(FLODEN_FORMAGA[f].niva != "saknas" for f in med.urval)
    egen = skapa_bestallning(
        k, syfte=SYFTE, amnen=["dagsljus"], projekt="demokvarteret", fragor=["DL-10", "DL-02"]
    )
    assert egen.urval == ("DL-10", "DL-02")
    assert Bestallning.fran_dict(json.loads(json.dumps(med.till_dict()))) == med
    sparad = m.rt.bestallning_skapa(syfte=SYFTE, amnen=["sol"], projekt="demokvarteret")
    assert m.rt.bestallning_hamta(sparad.bestallning_id) == sparad


@pytest.mark.parametrize(
    ("kw", "text"),
    [
        ({"syfte": " "}, "syftet"),
        ({"syfte": "x", "skede": "slutgiltig"}, "skede"),
        ({"syfte": "x", "mottagare_roll": "chef"}, "mottagare"),
        ({"syfte": "x", "amnen": ["skyfall", "dagsljus"]}, "olika rapporter"),
        ({"syfte": "x", "amnen": ["dagsljus"], "fragor": ["SK-01"]}, "hör inte till"),
        ({"syfte": "Mejla till a.b@example.se", "amnen": ["dagsljus"]}, "e-post"),
    ],
)
def test_bestallningen_vagrar(kw: dict[str, Any], text: str) -> None:
    arg = {"amnen": ["dagsljus"], "projekt": "demokvarteret", **kw}
    with pytest.raises(BestallningFel, match=text):
        skapa_bestallning(Fragekatalog.las(), **arg)


def test_planunderlag_och_fel_projekt_vagras_vid_skapande(m: Miljo) -> None:
    pu = m.rt.bestallning_skapa(
        syfte=SYFTE, amnen=["dagsljus"], projekt="demokvarteret", skede="planunderlag"
    )
    with pytest.raises(RapportFel, match="R3"):
        m.rt.skapa(projekt="demokvarteret", korningar=["demo-dagsljus"], bestallning=pu)
    b = m.rt.bestallning_skapa(syfte=SYFTE, amnen=["dagsljus"], projekt="demokvarteret")
    with pytest.raises(RapportFel, match="ämnet"):
        m.rt.skapa(projekt="demokvarteret", amne="skyfall", korningar=["demo-vatten"], bestallning=b)
    with pytest.raises(RapportFel, match="beställningen"):
        m.rt.skapa(
            projekt="demokvarteret", korningar=["demo-dagsljus"], bestallning=b, fragor=["DL-10"]
        )


def test_rollen_styr_ingress_och_urval(m: Miljo) -> None:
    plan = _rapport(m, roll="planhandlaggare")
    bygg = _rapport(m, roll="byggaktor")
    from rapport.amnestext import ingress

    assert plan.bestallning and plan.bestallning.mottagare_roll == "planhandlaggare"
    assert bygg.bestallning and bygg.bestallning.mottagare_roll == "byggaktor"
    assert ingress(plan, True, True) != ingress(bygg, True, True)
    assert "planhandläggare" in ingress(plan, True, True)


# ------------------------------------------------------------------ referensflödet


def test_alla_trettio_fragor_far_den_status_som_katalogen_anger(alla: Rapport) -> None:
    assert alla.fragor == DLSO
    for p in alla.pastaenden:
        assert p.status == FLODEN_FORMAGA[p.fraga_id].forval_status, p.fraga_id


def test_demons_dagsljusmatt_ar_samma_tal_som_floden_tjanst(
    alla: Rapport, m: Miljo
) -> None:
    per = {b.adress: b for p in alla.pastaenden for b in p.belagg}
    # soltimmar på mark, före och efter (takmasken används): hela området och gården
    for adress in ("omrade.hela_omradet.sol.bas.soltimmar.medel", "omrade.garden.sol.bas.soltimmar.medel"):
        assert adress in per and per[adress].namnare.startswith("mark")
    # förlorad markyta över en timme: samma mått som MCP:s jämförelse (via FragaTjanst)
    yt = [a for a in per if "yta_minskning_over_1h_m2" in a]
    assert yt and all(per[a].varde >= 0 and per[a].enhet == "m²" for a in yt)
    # VSC: antal under BRE:s gräns och relativt 0,8 räknas av Floden ur vsc.csv
    antal = per["fasad.vsc.bas.antal_punkter"].varde
    assert antal > 0 and per["fasad.vsc.bas.antal_punkter"].enhet == "st"
    assert per["fasad.vsc.bas.antal_under_grans"].varde <= antal
    assert per["fasad.vsc.grans_procent"].varde == 27.0
    assert per["fasad.vsc.kvot"].varde == 0.8
    # parametrar är indata, inte mätvärden: de har egen nämnare 'parameter'
    assert per["fasad.vsc.grans_procent"].namnare.startswith("parameter")


def test_rapportens_tal_kommer_bara_ur_platshallare(alla: Rapport) -> None:
    for p in alla.pastaenden:
        assert "{{" not in p.fylld_text()
    texter = " ".join(p.fylld_text() for p in alla.pastaenden)
    assert "uppfyller" not in texter.lower()


def test_kartorna_for_dagsljus_foljer_skyfallmonstret(alla: Rapport) -> None:
    assert [k.typ for k in alla.kartor].count("skillnad") == 2
    assert {k.scenario for k in alla.kartor} == {"bas", "hojd", "nytt_hus"}
    for k in alla.kartor:
        assert k.png_sha256 and k.bildtext


# ------------------------------------------------------------------ export


def test_export_word_och_pdf_med_sbk_profil(exporterad: dict[str, Any]) -> None:
    r: Rapport = exporterad["rapport"]
    word = _norm(_text_docx(exporterad["word"]))
    antal_sidor, sidor = _text_pdf(exporterad["pdf"])
    assert antal_sidor >= 8
    pdf = _norm(" ".join(sidor))
    for text in (word, pdf):
        assert "Skissunderlag dagsljus och sol" in text
        assert "demo-dagsljus" in text
        assert "Bilaga 1" in text and "Bilaga 3" in text
        for engelska in ("scenario", "Stadsbyggnadskontoret bedömer"):
            assert engelska not in text.replace("beräkningsfall", "")
    assert r.installningar.sprakprofil == "sbk"
    # samma tal i Word och PDF: fylld text för varje påstående finns i båda
    for p in r.pastaenden[:5]:
        assert _norm(p.fylld_text())[:60] in word


def test_neutral_profil_fungerar_for_dagsljus(m: Miljo) -> None:
    r = _rapport(m, installningar={"sprakprofil": "neutral", "numrerade_rubriker": True})
    assert r.installningar.sprakprofil == "neutral"
    assert m.rt.kontrollera(r.rapport_id)["godkand"]
    assert m.rt.exportera(r.rapport_id, "pdf").is_file()


def test_inplanterat_fel_stoppar_exporten(m: Miljo) -> None:
    r = _rapport(m)
    fil = m.json_fil(r)
    d = json.loads(fil.read_text(encoding="utf-8"))
    b = next(x for x in d["pastaenden"] if x["belagg"])["belagg"][0]
    b["varde"] = b["varde"] * 1.1
    fil.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ExportStoppad):
        m.rt.exportera(r.rapport_id, "pdf")
    assert not m.rt.kontrollera(r.rapport_id)["godkand"]


@pytest.mark.parametrize(
    ("fraga", "text", "fel"),
    [
        ("DL-10", "Dagsljusfaktorn i rummen är hög.", "dagsljusfaktor"),
        ("DL-10", "Grannens fönster får mindre ljus.", "fönster"),
        ("SO-09", "Marken har sammanhängande sol i fyra timmar.", "summa"),
    ],
)
def test_dagsljusfel_vagras(m: Miljo, fraga: str, text: str, fel: str) -> None:
    best = m.rt.bestallning_skapa(
        syfte=SYFTE, amnen=["dagsljus", "sol"], projekt="demokvarteret", fragor=[fraga]
    )
    r = m.rt.skapa(
        projekt="demokvarteret",
        korningar=["demo-dagsljus"],
        mottagare=[gardens_mottagare()],
        bestallning=best,
    )
    kw = next(
        k for k in pastaenden(kontekst_fran_rapport(m.rt, r), r.fragor) if k["fraga_id"] == fraga
    )
    kw = {**kw, "text": f"{text} {{{{matt:{kw['belagg'][0]}}}}}"}
    with pytest.raises(PastaendeFel, match=fel):
        m.rt.lagg_till_pastaende(r.rapport_id, forfattare="ai", **kw)


def test_sammanfattningsmeningen_hoppar_over_nollor() -> None:
    from rapport.skissmall import sammanfattningsmening

    namn = "dagsljus- och solfrågorna"
    assert (
        sammanfattningsmening(15, {"ja": 15, "kan_inte_avgoras": 0, "nej": 0}, namn)
        == "Av de 15 dagsljus- och solfrågorna behöver alla utredas vidare."
    )
    assert (
        sammanfattningsmening(15, {"ja": 9, "kan_inte_avgoras": 3, "nej": 3}, namn)
        == "Av de 15 dagsljus- och solfrågorna behöver 9 utredas vidare, 3 kan inte bedömas "
        "med nuvarande underlag och 3 behöver ingen fördjupning i detta skede."
    )
    assert (
        sammanfattningsmening(15, {"ja": 12, "kan_inte_avgoras": 0, "nej": 3}, namn)
        == "Av de 15 dagsljus- och solfrågorna behöver 12 utredas vidare och 3 behöver ingen "
        "fördjupning i detta skede."
    )
