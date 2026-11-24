# ruff: noqa: E501
"""Skissunderlag luft (R2): LU-frågorna, belägg ur `luft.mkn`, referensflöde, kontroll (MKN-påståenden) och export.

Serierna är påhittade och har känt svar (`demovarld.luftserier`): före ligger helt under högsta antalet dygn
(6 till 20 av 35), efter korsar det (21 till 46). Talen jämförs med samma källa som MCP-servern (`FlodenTjanst`)."""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client

from demovarld.luftserier import dygnsmedel_ug_m3, timserie_csv
from rapport.belaggskalla import BelaggFel
from rapport.bestallning import skapa_bestallning
from rapport.fragekatalog import (
    FLODEN_FORMAGA,
    Fragekatalog,
    formagetabell_markdown,
    fraga_med_formaga,
)
from rapport.luftbelagg import intervallrelation, luftlage
from rapport.pastaende import PastaendeFel, siffror_utanfor_platshallare
from rapport.referensflode_luft import kontekst_fran_rapport, pastaenden
from rapport.skissexport import ExportStoppad
from rapport.skisskontroll import _lufttext, luftfel
from rapport.skissrapport import Rapport, RapportFel
from tests.demovarld.test_demo import Demo
from tests.floden_mcp.test_mcp import Mcp
from tests.rapport.test_skissrapport import Miljo, _norm, _text_docx, _text_pdf

REPO = Path(__file__).resolve().parents[2]
LU = tuple(f"LU-{i:02d}" for i in range(1, 16))
SYFTE = "Skissa hur PM10 förhåller sig till dygnsnormen före och efter ett förslag, som intervall"


@pytest.fixture(scope="module")
def m(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Miljo]:
    yield Miljo(demo, tmp_path_factory.mktemp("luft_rapport"))


def _rapport(m: Miljo, fragor: list[str] | None = None, **over: Any) -> Rapport:
    best = m.rt.bestallning_skapa(syfte=SYFTE, amnen=["luft"], projekt="demokvarteret", fragor=fragor or [])
    r = m.rt.skapa(projekt="demokvarteret", korningar=["demo-luft"], ai_stod=False, bestallning=best, **over)
    for kw in pastaenden(kontekst_fran_rapport(m.rt, r), r.fragor):
        m.rt.lagg_till_pastaende(r.rapport_id, forfattare="referensflode", **kw)
    return m.rt.hamta(r.rapport_id)


@pytest.fixture(scope="module")
def r(m: Miljo) -> Rapport:
    return _rapport(m)


# ------------------------------------------------------------------ serierna och frågorna


def test_serierna_har_kant_svar() -> None:
    for lage, (hog, alla) in (("fore", (6, 20)), ("efter", (21, 46))):
        d = dygnsmedel_ug_m3(lage)
        assert len(d) == 365
        assert sum(1 for v in d if 12 + 0.64 * v > 50) == hog
        assert sum(1 for v in d if 12 + 1.5625 * v > 50) == alla
    rader = list(csv.DictReader(timserie_csv("fore").splitlines()))
    assert len(rader) == 8760 and min(float(x["lokal_ug_m3"]) for x in rader) >= 0
    assert timserie_csv("fore") == timserie_csv("fore") != timserie_csv("efter")


def test_lu_ar_ett_omrade_i_huvudkatalogen() -> None:
    alla = Fragekatalog.las_alla()
    assert [f.id for f in alla.for_amne("luft")] == list(LU)
    for f in alla.for_amne("luft"):
        assert f.omrade == "luft" and f.kallor and all(k in alla.kallor for k in f.kallor), f.id
    b = skapa_bestallning(alla, syfte=SYFTE, amnen=["luft"], projekt="demokvarteret")
    assert b.urval == LU and b.rapportamne == "luft"


def test_formagetabellen_stammer_med_koden_och_dokumentet() -> None:
    assert set(LU) <= set(FLODEN_FORMAGA)
    doc = (REPO / "docs" / "RAPPORTER.md").read_text(encoding="utf-8")
    assert formagetabell_markdown("luft") in doc
    assert {f for f in LU if FLODEN_FORMAGA[f].forval_status == "visat"} == {"LU-08"}
    markning = {f: FLODEN_FORMAGA[f].markning for f in LU}
    assert markning["LU-08"] == "visat"
    assert {f for f, v in markning.items() if v == "ej_tillampligt"} == {"LU-01", "LU-13"}
    assert {f for f, v in markning.items() if v == "delvis"} == {"LU-02", "LU-03", "LU-05", "LU-10", "LU-11", "LU-15"}
    assert {f for f, v in markning.items() if v == "underlag_saknas"} == {"LU-04", "LU-06", "LU-07", "LU-09", "LU-12", "LU-14"}
    for f in LU:  # visat och delvis anger vilka adresser som ger belägg; frågorna ändras inte
        fm = fraga_med_formaga(Fragekatalog.las_alla().hamta(f))["floden"]
        assert fm is not None and (fm["adresser"] or fm["niva"] == "saknas"), f
    assert Fragekatalog.las_alla().hamta("LU-08").fraga == "Finns rätt tidsstatistik för varje jämförelse?"


# ------------------------------------------------------------------ belägg


def test_belagg_ar_samma_tal_som_korningens_manifest(m: Miljo) -> None:
    k = m.rt.kalla
    for analys, (lag, hog) in (("mkn_fore", (6, 20)), ("mkn_efter", (21, 46))):
        man = json.loads((k.utmapp("demo-luft") / analys / "bas" / "run.json").read_text(encoding="utf-8"))
        for namn in ("lagst_antal_dygn", "hogst_antal_dygn", "arsmedel_lagst_ug_m3", "p90_4_hogst_ug_m3", "bakgrund_ug_m3"):
            b = k.hamta(f"matvarde.{analys}.bas.{namn}", "demo-luft", {})
            assert b.varde == man["matvarden"][namn] and b.losare == "luft.mkn" and b.upplosning > 0
            assert b.tidsdefinition and b.statistik and b.namnare.startswith("den givna timserien")
        assert (man["matvarden"]["lagst_antal_dygn"], man["matvarden"]["hogst_antal_dygn"]) == (lag, hog)
    assert k.hamta("matvarde.mkn_fore.bas.bakgrund_ug_m3", "demo-luft", {}).enhet == "µg/m³"
    assert k.hamta("matvarde.mkn_fore.bas.bakgrund_ug_m3", "demo-luft", {}).statistik.startswith("parameter (indata)")


def test_intervallrelation_ur_belagg(m: Miljo) -> None:
    def bel(analys: str, *namn: str) -> list[Any]:
        return [m.rt.kalla.hamta(f"matvarde.{analys}.bas.{n}", "demo-luft", {}) for n in namn]

    tre = ("lagst_antal_dygn", "hogst_antal_dygn", "max_dygn_ar_antal_dygn")
    assert intervallrelation(bel("mkn_fore", *tre)) == {("mkn_fore", "bas"): "under"}
    assert intervallrelation(bel("mkn_efter", *tre)) == {("mkn_efter", "bas"): "korsar"}
    assert intervallrelation(bel("mkn_efter", *tre[:2])) == {("mkn_efter", "bas"): None}
    assert luftlage("mkn_fore") == "före" and luftlage("mkn_efter") == "efter" and luftlage("gata_a") == "gata a"


@pytest.mark.parametrize(
    ("adress", "text"),
    [
        ("matvarde.mkn_fore.bas.klarar_garanterat", "måttkatalog"),
        ("matvarde.mkn_fore.bas.kortid_s", "måttkatalog"),
        ("matvarde.mkn_fore.saknas.lagst_antal_dygn", "finns inte"),
        ("matvarde.mkn_saknas.bas.lagst_antal_dygn", "finns inte"),
    ],
)
def test_felaktiga_adresser_vagras(m: Miljo, adress: str, text: str) -> None:
    with pytest.raises(BelaggFel, match=text):
        m.rt.kalla.hamta(adress, "demo-luft", {})


# ------------------------------------------------------------------ referensflödet


def test_rapporten_har_alla_lu_fragor_ingen_karta_och_katalogens_status(r: Rapport) -> None:
    assert r.fragor == LU and r.amne == "luft" and r.kartor == ()
    for p in r.pastaenden:
        assert p.status == FLODEN_FORMAGA[p.fraga_id].forval_status, p.fraga_id
    assert {k["analys"] for k in r.korningar[0]["analyser"]} == {"mkn_fore", "mkn_efter"}
    assert r.korningar[0]["jamforelser"] == []
    assert [x["losare"] for x in r.metoder] == ["luft.mkn"] and r.metoder[0]["status"] == "skiss"


def test_antagandena_star_i_rapporten(r: Rapport) -> None:
    ant = " ".join(r.korningar[0]["antaganden"])
    for fras in (
        "räknats fram utanför Floden",
        "Bakgrunden är ett antagande",
        "inte en statistisk konfidens",
        "två separata serier",
        "Läget före: PM10, bakgrund 12 µg/m³",
        "Läget efter: PM10, bakgrund 12 µg/m³",
    ):
        assert fras in ant, fras


def test_varje_tal_i_rapporten_har_belagg(r: Rapport) -> None:
    for p in r.pastaenden:
        assert not siffror_utanfor_platshallare(p.text), p.pastaende_id
        adresser = {b.adress for b in p.belagg}
        platser = set(re.findall(r"\{\{matt:([^}|]+)", p.text))
        assert platser <= adresser, p.pastaende_id
        assert "{{" not in p.fylld_text()
        if p.status == "visat":
            assert p.belagg
        rest = re.sub(r"\{\{[^}]*\}\}", "", p.text)
        rest = re.sub(r"\bPM10\b|\bPM2,5\b", "", rest)
        assert not re.search(r"\d", rest), (p.pastaende_id, rest)


def test_texten_sager_det_den_ska_och_inte_mer(r: Rapport) -> None:
    lu08 = _norm(next(p for p in r.pastaenden if p.fraga_id == "LU-08").fylld_text())
    assert "mellan 6 dygn och 20 dygn" in lu08 and "mellan 21 dygn och 46 dygn" in lu08
    assert "Högsta tillåtna antal är 35 dygn" in lu08
    assert "ligger helt under eller på högsta antalet dygn" in lu08
    assert "varken säga att normen klaras eller att den överskrids" in lu08
    alla = " ".join(p.fylld_text() for p in r.pastaenden)
    for forbjudet in ("klarar MKN", "klarar normen", "överskrider normen", "konfidensintervall", "uppfyller"):
        assert forbjudet not in alla


# ------------------------------------------------------------------ kontroll och export


def test_kontrollen_godkanner_och_exporten_ger_word_och_pdf(m: Miljo, r: Rapport) -> None:
    k = m.rt.kontrollera(r.rapport_id)
    assert k["godkand"], k
    word = _norm(_text_docx(m.rt.exportera(r.rapport_id, "word")))
    antal, sidor = _text_pdf(m.rt.exportera(r.rapport_id, "pdf"))
    assert antal >= 6
    for text in (word, _norm(" ".join(sidor))):
        assert "Skissunderlag luft" in text and "demo-luft" in text
        assert "räknats fram utanför Floden" in text and "Bakgrunden är ett antagande" in text
        assert "inte en statistisk konfidens" in text and "Metoden är en skiss" in text
        assert "Det finns ingen karta" in text and "Bilaga 1" in text
        assert "skiss – inte granskad för formell utredning" in text


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


def _lagg(m: Miljo, text: str, belagg: list[str], fraga: str = "LU-08", **kw: Any) -> Any:
    rr = m.rt.skapa(
        projekt="demokvarteret", korningar=["demo-luft"],
        bestallning=m.rt.bestallning_skapa(syfte=SYFTE, amnen=["luft"], projekt="demokvarteret", fragor=[fraga]),
    )  # fmt: skip
    return m.rt.lagg_till_pastaende(
        rr.rapport_id, forfattare="ai", fraga_id=fraga, status="visat", fordjupning="ja", text=text, belagg=belagg, **kw
    )


def _adr(analys: str, *namn: str) -> list[str]:
    return [f"matvarde.{analys}.bas.{n}" for n in namn]


TRE = ("lagst_antal_dygn", "hogst_antal_dygn", "max_dygn_ar_antal_dygn")


@pytest.mark.parametrize(
    "text",
    [
        "MKN klaras.",
        "Normen klaras inte.",
        "MKN överskrids.",
        "Miljökvalitetsnormen överskrids i läget efter.",
        "Dygnsnormen klarar vi i läget efter.",
        "Gränsvärdet överskrider förslaget.",
    ],
)
def test_mkn_pastaende_stoppas_nar_intervallet_korsar_gransen(m: Miljo, text: str) -> None:
    belagg = _adr("mkn_efter", *TRE)
    full = f"{text} Antalet dygn är {{{{matt:{belagg[0]}}}}} till {{{{matt:{belagg[1]}}}}} (intervallet)."
    with pytest.raises(PastaendeFel, match="korsar gränsen"):
        _lagg(m, full, belagg)


def test_mkn_pastaende_kraver_belagg_for_intervallet(m: Miljo) -> None:
    belagg = _adr("mkn_fore", "lagst_antal_dygn", "hogst_antal_dygn")
    with pytest.raises(PastaendeFel, match="visar inte intervallet mot högsta antalet dygn"):
        _lagg(m, f"MKN klaras i intervallet {{{{matt:{belagg[0]}}}}} till {{{{matt:{belagg[1]}}}}}.", belagg)


def test_mkn_pastaende_med_helt_under_godtas_bara_med_intervall_och_ratt_riktning(m: Miljo) -> None:
    belagg = _adr("mkn_fore", *TRE)
    pl = " ".join(f"{{{{matt:{b}}}}}" for b in belagg)
    ok = _lagg(m, f"MKN klaras i hela intervallet (givet bakgrunden): {pl}.", belagg)
    assert ok.status == "visat"
    with pytest.raises(PastaendeFel, match="utan att säga att det gäller i hela intervallet"):
        _lagg(m, f"MKN klaras: {pl}.", belagg)
    with pytest.raises(PastaendeFel, match="motsatsen"):
        _lagg(m, f"MKN överskrids i intervallet: {pl}.", belagg)


def test_mkn_pastaende_med_helt_over_godtas_for_overskridande(m: Miljo) -> None:
    # konstruerat belägg: höj 'max_dygn_ar_antal_dygn' är inte möjligt, så använd efter med lägre gräns via direkt kontroll
    belagg = _adr("mkn_efter", *TRE)
    rr = _rapport(m, fragor=["LU-08"])
    p = next(x for x in rr.pastaenden)
    from dataclasses import replace

    b = [replace(x, varde=(10.0 if x.adress.endswith("max_dygn_ar_antal_dygn") else x.varde)) for x in m.rt.hamta(rr.rapport_id).pastaenden[0].belagg if x.adress in belagg]
    p2 = replace(p, belagg=tuple(b), text="MKN överskrids i hela intervallet.")
    assert luftfel(p2, p2.text) == []
    p3 = replace(p, belagg=tuple(b), text="MKN klaras i hela intervallet.")
    assert any("motsatsen" in f for f in luftfel(p3, p3.text))


@pytest.mark.parametrize(
    "text",
    [
        "Det går inte att säga om MKN klaras.",
        "Intervallet korsar gränsen, så underlaget kan varken säga att normen klaras eller att den överskrids.",
        "Frågan om miljökvalitetsnormen överskrids behöver utredas vidare.",
        "Intervallet ligger helt under högsta antalet dygn.",
        "Haltbidraget är en given serie och inte beräknat av Floden.",
        "Spridningen är beräknad i ett annat verktyg.",
    ],
)
def test_forbehall_och_beskrivning_ar_inga_paastaenden(m: Miljo, text: str) -> None:
    belagg = _adr("mkn_efter", *TRE)
    rr = _rapport(m, fragor=["LU-08"])
    p = rr.pastaenden[0]
    from dataclasses import replace

    assert luftfel(replace(p, text=text), text) == []
    _ = belagg


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("Floden har beräknat halten.", "given timserie"),
        ("Spridningen är beräknad med Floden.", "given timserie"),
        ("Intervallet är ett konfidensintervall.", "inte en statistisk konfidens"),
        ("Årsmedelnormen klaras.", "årsmedel"),
    ],
)
def test_luftfel_for_beraknad_halt_konfidens_och_arsmedel(m: Miljo, text: str, match: str) -> None:
    belagg = _adr("mkn_fore", *TRE)
    with pytest.raises(PastaendeFel, match=match):
        _lagg(m, f"{text} {{{{matt:{belagg[0]}}}}}.", belagg)


def test_fordjupat_dokumentkrav_for_luft(r: Rapport) -> None:
    assert _lufttext(r, "") != []
    saknas = " ".join(_lufttext(r, "Metoden är en skiss."))
    assert "räknats fram utanför Floden" in saknas and "två separata serier" in saknas


def test_luft_i_andra_amnen_och_scenario_vagras(m: Miljo) -> None:
    best = m.rt.bestallning_skapa(syfte=SYFTE, amnen=["luft"], projekt="demokvarteret", scenario={"fore": "bas", "efter": ["x"]})
    with pytest.raises(RapportFel, match="inte som scenarier"):
        m.rt.skapa(projekt="demokvarteret", korningar=["demo-luft"], bestallning=best)
    with pytest.raises(RapportFel, match="ingen analys med lösaren luft.mkn"):
        m.rt.skapa(projekt="demokvarteret", amne="luft", korningar=["demo-ekologi"])


# ------------------------------------------------------------------ en serie och olika antaganden


@pytest.fixture(scope="module")
def egna(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    rot = tmp_path_factory.mktemp("luft_egna")
    serie = rot / "fore.csv"
    serie.write_text(timserie_csv("fore"), encoding="utf-8")
    bas = (demo.varld / "vatten" / "bas").as_posix()

    def fraga(fid: str, bg: tuple[float, ...]) -> None:
        analyser = "".join(
            f'\n[[analyser]]\nid = "{n}"\nlosare = "luft.mkn"\nparametrar = {{ bakgrund_ug_m3 = {b} }}\n'
            f'indata = {{ timserie = "{serie.as_posix()}" }}\n'
            for n, b in zip(("mkn_fore", "mkn_efter")[: len(bg)], bg, strict=True)
        )
        (demo.fragor / f"test_{fid.replace('-', '_')}.toml").write_text(
            f'kontrakt = "K20/v1"\nfraga_id = "{fid}"\nbeskrivning = "Påhittad serie."\n\n[scenarier.bas]\nbas = "{bas}"\n{analyser}',
            encoding="utf-8",
        )
        demo.tjanst.kor_synkront(f"test_{fid.replace('-', '_')}", fid)

    fraga("test-luft-en", (12.0,))
    fraga("test-luft-olika", (12.0, 20.0))
    yield {"en": "test-luft-en", "olika": "test-luft-olika"}


def test_en_serie_ger_rapport_utan_fore_efter(m: Miljo, egna: dict[str, str]) -> None:
    best = m.rt.bestallning_skapa(syfte=SYFTE, amnen=["luft"], projekt="demokvarteret")
    rr = m.rt.skapa(projekt="demokvarteret", korningar=[egna["en"]], ai_stod=False, bestallning=best)
    for kw in pastaenden(kontekst_fran_rapport(m.rt, rr), rr.fragor):
        m.rt.lagg_till_pastaende(rr.rapport_id, forfattare="referensflode", **kw)
    rr = m.rt.hamta(rr.rapport_id)
    ant = " ".join(rr.korningar[0]["antaganden"])
    assert "Före och efter förslaget kan inte jämföras: det kräver två serier" in ant and "två separata serier" not in ant
    k = m.rt.kontrollera(rr.rapport_id)
    assert k["godkand"], k
    lu03 = next(p for p in rr.pastaenden if p.fraga_id == "LU-03").fylld_text()
    assert "Underlaget har en serie" in lu03 and "kräver två serier" in lu03


def test_olika_bakgrund_far_inte_jamforas_i_samma_pastaende(m: Miljo, egna: dict[str, str]) -> None:
    best = m.rt.bestallning_skapa(syfte=SYFTE, amnen=["luft"], projekt="demokvarteret", fragor=["LU-03"])
    rr = m.rt.skapa(projekt="demokvarteret", korningar=[egna["olika"]], ai_stod=False, bestallning=best)
    assert any("olika bakgrund" in a for a in rr.korningar[0]["antaganden"])
    with pytest.raises(PastaendeFel, match="olika analysparametrar"):
        for kw in pastaenden(kontekst_fran_rapport(m.rt, rr), rr.fragor):
            m.rt.lagg_till_pastaende(rr.rapport_id, forfattare="referensflode", **kw)


# ------------------------------------------------------------------ MCP


def test_luft_via_mcp_och_prompten(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> None:
    rot = tmp_path_factory.mktemp("mcp_luft")
    mcp = Mcp(demo, rot)
    mcp.app.rapportmapp = rot / "rapporter"
    from floden_mcp.server import bygg_server

    mcp.server = bygg_server(mcp.tjanst, logg=mcp.logg, miljo={"FLODEN_MCP_MODELL": "testmodell"})
    f = mcp.ok("fragekatalog_sok", {"omrade": "luft"})
    assert f["antal"] == 15
    lu08 = next(x for x in f["fragor"] if x["id"] == "LU-08")
    assert lu08["floden"]["markning"] == "visat" and lu08["floden"]["adresser"]
    assert {x["floden"]["markning"] for x in f["fragor"]} == {"visat", "delvis", "underlag_saknas", "ej_tillampligt"}
    best = mcp.ok("bestallning_skapa", {"syfte": SYFTE, "amnen": ["luft"]})
    r = mcp.ok("rapport_skapa", {"korningar": ["demo-luft"], "bestallning_id": best["bestallning_id"]})
    rid = r["rapport_id"]
    assert len(r["fragor"]) == 15 and r["kartor"] == []
    assert "matvarde.mkn_efter.bas.hogst_antal_dygn" in r["belagg_adresser"]
    # korsande intervall: ett påstående om att normen klaras stoppas av verktyget
    nej = mcp.anropa(
        "pastaende_lagg_till",
        {
            "rapport_id": rid, "fraga_id": "LU-08", "status": "visat", "fordjupning": "ja",
            "text_med_platshallare": "MKN klaras i intervallet {{matt:matvarde.mkn_efter.bas.lagst_antal_dygn}} till {{matt:matvarde.mkn_efter.bas.hogst_antal_dygn}} dygn.",
            "belagg": ["matvarde.mkn_efter.bas.lagst_antal_dygn", "matvarde.mkn_efter.bas.hogst_antal_dygn", "matvarde.mkn_efter.bas.max_dygn_ar_antal_dygn"],
        },
    )  # fmt: skip
    assert nej.is_error and "korsar gränsen" in nej.content[0].text

    async def _prompt() -> Any:
        async with Client(mcp.server) as c:
            return await c.get_prompt("skissunderlag_luft", {})

    p = anyio.run(_prompt).messages[0].content.text
    assert "luft.mkn" in p and "korsar" in p and "{{matt:<adress>}}" in p
