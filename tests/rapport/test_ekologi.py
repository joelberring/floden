# ruff: noqa: E501
"""Skissunderlag ekologi (R2): EK-frågorna, belägg för ECA/PC/nätverksparametrar, referensflöde, kontroll och export.

Talen jämförs med samma källa som MCP-servern (`FlodenTjanst`); inga tal får nå rapporten utan belägg."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from rapport.belaggskalla import BelaggFel
from rapport.bestallning import skapa_bestallning
from rapport.fragekatalog import (
    FLODEN_FORMAGA,
    Fragekatalog,
    formagetabell_markdown,
    fraga_med_formaga,
)
from rapport.pastaende import PastaendeFel, siffror_utanfor_platshallare
from rapport.referensflode_ekologi import kontekst_fran_rapport, pastaenden
from rapport.skissexport import ExportStoppad
from rapport.skissrapport import Rapport, RapportFel
from tests.demovarld.test_demo import Demo
from tests.rapport.test_skissrapport import Miljo, _norm, _text_docx, _text_pdf

REPO = Path(__file__).resolve().parents[2]
EK = tuple(f"EK-{i:02d}" for i in range(1, 13))
SYFTE = "Skissa hur ett nytt hus påverkar ett skogsspridningssamband för barrskogsmesar"


@pytest.fixture(scope="module")
def m(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Miljo]:
    yield Miljo(demo, tmp_path_factory.mktemp("ekologi"))


def _rapport(m: Miljo, **over: Any) -> Rapport:
    best = m.rt.bestallning_skapa(
        syfte=SYFTE, amnen=["ekologi"], projekt="demokvarteret",
        scenario={"fore": "fore", "efter": ["efter"]},
    )  # fmt: skip
    r = m.rt.skapa(projekt="demokvarteret", korningar=["demo-ekologi"], ai_stod=False, bestallning=best, **over)
    for kw in pastaenden(kontekst_fran_rapport(m.rt, r), r.fragor):
        m.rt.lagg_till_pastaende(r.rapport_id, forfattare="referensflode", **kw)
    return m.rt.hamta(r.rapport_id)


@pytest.fixture(scope="module")
def r(m: Miljo) -> Rapport:
    return _rapport(m)


# ------------------------------------------------------------------ frågor


def test_ek_ar_ett_eget_omrade_och_den_granskade_katalogen_ar_oforandrad() -> None:
    granskad = Fragekatalog.las()
    assert len(granskad.fragor) == 75 and not any(f.id.startswith("EK") for f in granskad.fragor)
    alla = Fragekatalog.las_alla()
    assert [f.id for f in alla.for_amne("ekologi")] == list(EK)
    assert alla.fragor[:75] == granskad.fragor
    for f in alla.for_amne("ekologi"):
        assert f.omrade == "ekologi" and f.kallor and all(k in alla.kallor for k in f.kallor), f.id
        assert all(alla.kallor[k]["titel"] for k in f.kallor)


def test_formagetabellen_stammer_med_koden_och_dokumentet() -> None:
    assert set(EK) <= set(FLODEN_FORMAGA)
    assert formagetabell_markdown("ekologi") in (REPO / "docs" / "RAPPORTER.md").read_text(encoding="utf-8")
    assert {f for f in EK if FLODEN_FORMAGA[f].forval_status == "visat"} == {"EK-02", "EK-04", "EK-05"}
    assert fraga_med_formaga(Fragekatalog.las_alla().hamta("EK-05"))["floden"]["adresser"]


def test_bestallning_for_ekologi() -> None:
    b = skapa_bestallning(Fragekatalog.las_alla(), syfte=SYFTE, amnen=["ekologi"], projekt="demokvarteret")
    assert b.urval == EK and b.rapportamne == "ekologi"


# ------------------------------------------------------------------ belägg


def test_belagg_ar_samma_tal_som_korningens_manifest(m: Miljo) -> None:
    k = m.rt.kalla
    kod = {"fore": "fore", "efter": "efter"}
    for s in kod:
        man = json.loads((k.utmapp("demo-ekologi") / "konnektivitet" / s / "run.json").read_text(encoding="utf-8"))
        for namn, adress in (("eca_m2", "eca_m2"), ("pc", "pc"), ("antal_flackar", "antal_flackar"), ("eca_min_m2", "eca_min_m2")):
            b = k.hamta(f"matvarde.konnektivitet.{s}.{adress}", "demo-ekologi", {})
            assert b.varde == man["matvarden"][namn]
            assert b.losare == "ekologi.konnektivitet" and b.upplosning > 0
    fore = k.hamta("matvarde.konnektivitet.fore.eca_m2", "demo-ekologi", {})
    assert fore.enhet == "m²" and "ECA" in fore.storhet and fore.tidsdefinition


def test_skillnaden_ar_differens_av_tva_belagg_med_gemensam_nyckel(m: Miljo) -> None:
    k = m.rt.kalla
    f = k.hamta("matvarde.konnektivitet.fore.eca_m2", "demo-ekologi", {})
    e = k.hamta("matvarde.konnektivitet.efter.eca_m2", "demo-ekologi", {})
    d = k.hamta("ekojamforelse.konnektivitet.fore.efter.delta_eca_m2", "demo-ekologi", {})
    assert d.varde == pytest.approx(e.varde - f.varde) and d.enhet == "m²"
    assert d.scenarier == ("fore", "efter") and d.fil_sha256 and d.fil_sha256 != f.fil_sha256
    pr = k.hamta("ekojamforelse.konnektivitet.fore.efter.delta_eca_procent", "demo-ekologi", {})
    assert pr.varde == pytest.approx((e.varde - f.varde) / f.varde * 100) and pr.enhet == "%"
    n = k.hamta("ekojamforelse.konnektivitet.fore.efter.delta_antal_flackar", "demo-ekologi", {})
    assert n.varde == 0 and n.enhet is None


def test_natverksparametrar_ar_parametrar_med_egen_namnare(m: Miljo) -> None:
    k = m.rt.kalla
    d50 = k.hamta("ekonatverk.konnektivitet.fore.d50_m", "demo-ekologi", {})
    tr = k.hamta("ekonatverk.konnektivitet.fore.troskel_m", "demo-ekologi", {})
    assert d50.varde == tr.varde / 2 and "antagande" in d50.storhet
    assert d50.statistik.startswith("parameter (indata)") and d50.namnare.startswith("parameter")
    assert k.hamta("ekonatverk.konnektivitet.fore.min_area_m2", "demo-ekologi", {}).enhet == "m²"


@pytest.mark.parametrize(
    ("adress", "text"),
    [
        ("ekojamforelse.konnektivitet.fore.efter.delta_okand", "jämförelsemått"),
        ("ekojamforelse.konnektivitet.fore.saknas.delta_eca_m2", "finns inte"),
        ("ekonatverk.konnektivitet.fore.hastighet", "nätverksparameter"),
        ("ekonatverk.konnektivitet.fore", "ska vara"),
        ("matvarde.konnektivitet.fore.kortid_s", "måttkatalog"),
    ],
)
def test_felaktiga_adresser_vagras(m: Miljo, adress: str, text: str) -> None:
    with pytest.raises(BelaggFel, match=text):
        m.rt.kalla.hamta(adress, "demo-ekologi", {})


# ------------------------------------------------------------------ referensflödet


def test_alla_tolv_fragor_far_katalogens_status(r: Rapport) -> None:
    assert r.fragor == EK and r.amne == "ekologi"
    for p in r.pastaenden:
        assert p.status == FLODEN_FORMAGA[p.fraga_id].forval_status, p.fraga_id


def test_varje_tal_i_rapporten_har_belagg(r: Rapport) -> None:
    for p in r.pastaenden:
        assert not siffror_utanfor_platshallare(p.text), p.pastaende_id
        adresser = {b.adress for b in p.belagg}
        platser = set(re.findall(r"\{\{matt:([^}|]+)", p.text))
        assert platser <= adresser, p.pastaende_id
        assert "{{" not in p.fylld_text()
        if p.status == "visat":
            assert p.belagg
    # siffror i den ifyllda texten kommer bara ur platshållarna: ta bort dem och inga siffror återstår
    for p in r.pastaenden:
        rest = re.sub(r"\{\{[^}]*\}\}", "", p.text)
        rest = re.sub(r"\bd50\b|\bEK-\d\d\b", "", rest)
        assert not re.search(r"\d", rest), (p.pastaende_id, rest)


def test_texten_har_signalart_kalla_etikett_och_antagande(r: Rapport, m: Miljo) -> None:
    ek02 = next(p for p in r.pastaenden if p.fraga_id == "EK-02").fylld_text()
    assert "Signalart: tofsmes och talltita" in ek02 and "Källa: Bovin" in ek02
    alla = " ".join(p.fylld_text() for p in r.pastaenden).lower()
    assert "potentiell livsmiljö, okänd kvalitet" in alla
    ant = " ".join(r.korningar[0]["antaganden"])
    assert "p saknas i källan; d50 = tröskel/2" in ant and "Källa: Bovin 2020" in ant
    assert "potentiell livsmiljö, okänd kvalitet" in ant


def test_kartorna_ar_stromtathet_fore_efter_och_skillnad(r: Rapport) -> None:
    assert sorted(k.typ for k in r.kartor) == ["bas", "scenario", "skillnad"]
    for k in r.kartor:
        assert k.meta["enhet"] == "normaliserad strömtäthet (0-1)" and k.meta["enhet"] in k.bildtext
        assert "potentiell livsmiljö, okänd kvalitet" in k.bildtext.lower()


def test_kontrollen_godkanner_och_exporten_ger_word_och_pdf(m: Miljo, r: Rapport) -> None:
    k = m.rt.kontrollera(r.rapport_id)
    assert k["godkand"], k
    word = _norm(_text_docx(m.rt.exportera(r.rapport_id, "word")))
    antal, sidor = _text_pdf(m.rt.exportera(r.rapport_id, "pdf"))
    assert antal >= 8
    for text in (word, _norm(" ".join(sidor))):
        assert "Skissunderlag ekologi" in text and "demo-ekologi" in text
        assert "p saknas i källan; d50 = tröskel/2" in text
        assert "potentiell livsmiljö, okänd kvalitet" in text.lower()
        assert "Bilaga 1" in text


def test_neutral_profil_fungerar(m: Miljo) -> None:
    rr = _rapport(m, installningar={"sprakprofil": "neutral", "numrerade_rubriker": True})
    assert m.rt.kontrollera(rr.rapport_id)["godkand"]
    assert m.rt.exportera(rr.rapport_id, "pdf").is_file()


def test_inplanterat_fel_stoppar_exporten(m: Miljo) -> None:
    rr = _rapport(m)
    fil = m.json_fil(rr)
    d = json.loads(fil.read_text(encoding="utf-8"))
    b = next(x for x in d["pastaenden"] if x["belagg"])["belagg"][0]
    b["varde"] = b["varde"] * 1.1
    fil.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ExportStoppad):
        m.rt.exportera(rr.rapport_id, "pdf")


@pytest.mark.parametrize(
    ("text", "fel"),
    [
        ("Arten förekommer i fläckarna.", "förekomst"),
        ("Livsmiljön har hög kvalitet.", "livsmiljö"),
        ("Sambandet är säkrat.", "förekomst"),
    ],
)
def test_ekologifel_vagras(m: Miljo, text: str, fel: str) -> None:
    best = m.rt.bestallning_skapa(syfte=SYFTE, amnen=["ekologi"], projekt="demokvarteret", fragor=["EK-05"])
    rr = m.rt.skapa(projekt="demokvarteret", korningar=["demo-ekologi"], bestallning=best)
    kw = next(k for k in pastaenden(kontekst_fran_rapport(m.rt, rr), rr.fragor) if k["fraga_id"] == "EK-05")
    kw = {**kw, "text": f"{text} {{{{matt:{kw['belagg'][0]}}}}}"}
    with pytest.raises(PastaendeFel, match=fel):
        m.rt.lagg_till_pastaende(rr.rapport_id, forfattare="ai", **kw)


def test_tal_utan_platshallare_vagras(m: Miljo) -> None:
    best = m.rt.bestallning_skapa(syfte=SYFTE, amnen=["ekologi"], projekt="demokvarteret", fragor=["EK-05"])
    rr = m.rt.skapa(projekt="demokvarteret", korningar=["demo-ekologi"], bestallning=best)
    kw = next(k for k in pastaenden(kontekst_fran_rapport(m.rt, rr), rr.fragor) if k["fraga_id"] == "EK-05")
    with pytest.raises(PastaendeFel, match="belägg"):
        m.rt.lagg_till_pastaende(rr.rapport_id, forfattare="ai", **{**kw, "text": kw["text"] + " ECA är 25000 m²."})


def test_luft_och_buller_har_ingen_skissrapport(m: Miljo) -> None:
    for amne, text in (("buller", "Nord2000"), ("luft", "belägg")):
        with pytest.raises(RapportFel, match=text):
            m.rt.skapa(projekt="demokvarteret", amne=amne, korningar=["demo-ekologi"])
