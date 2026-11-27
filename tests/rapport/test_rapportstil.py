# ruff: noqa: E501
"""Rapportens inställningar och språkprofil: förval (sbk-ton, inga nummer, svensk sifferform),
det gamla beteendet via inställningar, byte-identiska filer, inga lokala sökvägar i filer eller
loggar, språktest och `config.local.toml [rapport]`."""

from __future__ import annotations

import json
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from rapport.installningar import (
    InstallningsFel,
    RapportInstallningar,
    lokala_forval,
    tillampa,
)
from rapport.pastaende import text_utan_platshallare
from rapport.referensflode import pastaenden
from rapport.skissexport import ExportStoppad, skriv_pdf, skriv_word
from rapport.skissmall import bygg_dokument, dokumentets_text, huvudtext
from rapport.skissrapport import Rapport, RapportFel
from rapport.sprak import ENGELSKA_ORD, SBK_FORBJUDET, datum_text
from tests.demovarld.test_demo import Demo
from tests.rapport.test_skissrapport import Miljo, _forvanska_varde, _text_docx, _text_pdf

SM = " "


@pytest.fixture(scope="module")
def m(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Miljo]:
    yield Miljo(demo, tmp_path_factory.mktemp("rapportstil"))


def _full(m: Miljo, **over: Any) -> Rapport:
    r = m.ny_rapport(**over)
    for kw in pastaenden(sprakprofil=r.installningar.sprakprofil):
        m.rt.lagg_till_pastaende(r.rapport_id, forfattare="referensflode", **kw)
    return m.rt.hamta(r.rapport_id)


@pytest.fixture(scope="module")
def forval(m: Miljo) -> dict[str, Any]:
    r = _full(m)
    word = m.rt.exportera(r.rapport_id, "word")
    pdf = m.rt.exportera(r.rapport_id, "pdf")
    return {"rapport": m.rt.hamta(r.rapport_id), "word": word, "pdf": pdf}


# ------------------------------------------------------------------ förval


def test_forvalen_lagras_i_k33(forval: dict[str, Any]) -> None:
    r: Rapport = forval["rapport"]
    d = r.till_dict()["installningar"]
    assert d == {
        "sprakprofil": "sbk", "numrerade_rubriker": False, "numrerade_figurer": False,
        "avsandare": "Stadsbyggnadskontoret", "diarienummer": "", "handlaggare": "",
        "accentfarg": "000000", "logotyp": None, "typsnitt_brodtext": None, "typsnitt_rubriker": None,
        "separat_bilaga": False,
    }  # fmt: skip
    assert Rapport.fran_dict(json.loads(json.dumps(r.till_dict()))) == r


def test_aldre_rapport_utan_installningar_lases_med_forval(forval: dict[str, Any]) -> None:
    d = forval["rapport"].till_dict()
    del d["installningar"]
    assert Rapport.fran_dict(d).installningar == RapportInstallningar()


def test_forval_inga_nummer_sbk_ton_och_svensk_sifferform(forval: dict[str, Any]) -> None:
    r: Rapport = forval["rapport"]
    text = _text_docx(forval["word"])
    rader = text.splitlines()
    for rubrik in (
        "Sammanfattning",
        "Uppdrag, plats och beräkningsfall",
        "Kartor och resultat",
        "Åtgärder, villkor och vad som saknas inför nästa skede",
        "Metod, begränsningar och frågekatalogsöversikt",
    ):
        assert rubrik in rader
    assert not re.search(r"^\d+ (Sammanfattning|Kartor och resultat)", text, re.M)
    assert "Figur 1" not in text and "figur 1" not in text
    for bilaga in (
        "Teknisk spårbarhetsbilaga",
        "Bilaga 1 Belägg",
        "Bilaga 2 Körlogg",
        "Bilaga 3 Manifest",
        "Bilaga 4 Kontroll inför export",
    ):
        assert bilaga in rader
    assert "Bilaga A" not in text
    # sbk-ton: deterministisk metodnotis utan rå AI-jargong; körnings-id i teknisk bilaga
    assert "Underlaget är framtaget med Floden (deterministiska fysikberäkningar)." in text
    assert "utan AI-stöd" in text
    assert "AI:n har valt körningar och skrivit text" not in text
    assert "Kartorna nedan visar" in text
    # svensk sifferform: smalt hårt mellanslag i tusental, decimalkomma, datum i ord
    sk03 = next(p for p in r.pastaenden if p.fraga_id == "SK-03").fylld_text()
    assert re.search(rf"\d{SM}\d{{3}} m³", sk03), sk03
    assert re.search(r"\d,\d\d m\b", text)
    assert datum_text(r.skapad, True) in text and re.fullmatch(
        r"\d{1,2} [a-z]+ \d{4}", datum_text(r.skapad, True)
    )
    sidor, texter = _text_pdf(forval["pdf"])
    for i, t in enumerate(texter, start=1):
        assert f"Sida {i} ({sidor})" in t
    assert "Stadsbyggnadskontoret" in texter[0] and "Skissunderlag" in texter[0]


def test_word_har_rubrikformat_och_svenska(forval: dict[str, Any]) -> None:
    import docx

    d = docx.Document(str(forval["word"]))
    rubriker = {p.text: p.style.name for p in d.paragraphs if p.style.name.startswith("Heading")}
    assert rubriker["Sammanfattning"] == "Heading 1"
    assert rubriker["Resultat per fråga"] == "Heading 2"
    assert rubriker["Bilaga 1 Belägg"] == "Heading 1"
    with zipfile.ZipFile(forval["word"]) as z:
        stilar = z.read("word/styles.xml").decode()
        footer = "".join(z.read(n).decode() for n in z.namelist() if "footer" in n)
    assert 'w:lang w:val="sv-SE"' in stilar
    assert "NUMPAGES" in footer and "PAGE" in footer
    assert d.core_properties.language == "sv-SE"


def test_bildtexter_utan_nummer_och_scenarionamn_i_klarsprak(
    forval: dict[str, Any], m: Miljo
) -> None:
    from rapport.skissmall import Figur, FigurPar

    r: Rapport = forval["rapport"]
    dok = bygg_dokument(r, m.rt)
    figurer: list[Figur] = []
    for b in dok.block:
        if isinstance(b, FigurPar):
            figurer.extend([b.vanster, b.hoger])
        elif isinstance(b, Figur):
            figurer.append(b)
    assert len(figurer) >= 3
    assert figurer[0].bildtext.startswith("Största vattendjup på marken före förslaget, i meter")
    assert figurer[2].bildtext.startswith(
        "Skillnad i största vattendjup på marken, efter minus före"
    )
    assert all("scenario" not in f.bildtext and "Figur" not in f.bildtext for f in figurer)
    assert r.kartor[0].meta["rubrik"] == "Största vattendjup före förslaget"


# ------------------------------------------------------------------ gamla beteendet


def test_gamla_beteendet_nas_med_installningar(m: Miljo) -> None:
    r = _full(
        m,
        installningar={
            "sprakprofil": "neutral",
            "numrerade_rubriker": True,
            "numrerade_figurer": True,
        },
    )
    assert m.rt.kontrollera(r.rapport_id)["godkand"]
    text = dokumentets_text(bygg_dokument(m.rt.hamta(r.rapport_id), m.rt))
    for s in ("1 Sammanfattning", "2 Uppdrag och beräkningsfall", "3 Resultat",
              "5 Metod, osäkerhet och metodnotis", "Bilaga A Belägg", "Bilaga B Körlogg",
              "Figur 1. Största vattendjup över mark, före (scenario fore)", "Status: Visat",
              "Påståenden per fråga",
              "Underlaget har tagits fram med Floden utan AI-stöd"):  # fmt: skip
        assert s in text, s
    # numreringen går att slå på var för sig, med sbk-ton
    m.rt.andra_installningar(r.rapport_id, {"sprakprofil": "sbk", "numrerade_figurer": False})
    r2 = m.rt.hamta(r.rapport_id)
    t2 = dokumentets_text(bygg_dokument(r2, m.rt))
    assert "1 Sammanfattning" in t2 and "Figur 1" not in t2 and "Bilaga 1 Belägg" in t2
    assert r2.kontroll is None and r2.handelser[-1]["handelse"] == "installningar_andrade"
    assert "numrerade_figurer" in r2.handelser[-1]["detalj"]


# ------------------------------------------------------------------ samma fil, inga sökvägar


def _resurser(tmp: Path) -> dict[str, str]:
    import reportlab
    from PIL import Image

    mapp = tmp / "lokal_hemlig_mapp"
    mapp.mkdir(exist_ok=True)
    Image.new("RGB", (240, 80), (31, 78, 121)).save(mapp / "logotyp.png")
    fonts = Path(reportlab.__file__).parent / "fonts"
    (mapp / "brod.ttf").write_bytes((fonts / "Vera.ttf").read_bytes())
    (mapp / "rubrik.ttf").write_bytes((fonts / "VeraBd.ttf").read_bytes())
    return {
        "logotyp": str(mapp / "logotyp.png"),
        "typsnitt_brodtext": str(mapp / "brod.ttf"),
        "typsnitt_rubriker": str(mapp / "rubrik.ttf"),
    }


def test_byte_identisk_fil_och_inga_lokala_sokvagar(m: Miljo, tmp_path: Path) -> None:
    filer = _resurser(tmp_path)
    r = _full(
        m,
        installningar={
            **filer,
            "diarienummer": "SBK 2026-01234",
            "handlaggare": "Anges av beställaren",
            "accentfarg": "#005a70",
        },
    )
    rid = r.rapport_id
    inst = r.installningar
    assert inst.accentfarg == "005A70"
    assert (
        inst.typsnitt_brodtext is not None
        and inst.typsnitt_brodtext.familj == "Bitstream Vera Sans"
    )
    a = m.rt.exportera(rid, "pdf")
    a_bytes = a.read_bytes()
    w = m.rt.exportera(rid, "word")
    w_bytes = w.read_bytes()

    # samma rapport och inställningar ger samma fil
    r = m.rt.hamta(rid)
    mapp = m.rt.arkiv.rapportmapp(rid)
    dok = bygg_dokument(r, m.rt, med_resurser=True)
    for skriv, namn in ((skriv_pdf, "x.pdf"), (skriv_word, "x.docx")):
        skriv(dok, mapp / ("1" + namn), mapp)
        skriv(dok, mapp / ("2" + namn), mapp)
        assert (mapp / ("1" + namn)).read_bytes() == (mapp / ("2" + namn)).read_bytes()

    # typsnittet syns i metadata (PDF, Word och exportposten); sökvägen syns ingenstans
    import pypdf

    meta = pypdf.PdfReader(str(a)).metadata
    assert inst.typsnitt_brodtext.sha256 in str(meta.get("/Keywords"))
    assert "Bitstream Vera Sans" in str(meta.get("/Keywords"))
    post = next(e for e in r.exporter if e["format"] == "pdf")
    assert {t["roll"] for t in post["typsnitt"]} == {"typsnitt_brodtext", "typsnitt_rubriker"}
    hemligt = ("lokal_hemlig_mapp", str(tmp_path))
    rapport_json = (mapp / "rapport.json").read_text(encoding="utf-8")
    with zipfile.ZipFile(w) as z:
        word_xml = "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())
    for h in hemligt:
        assert h not in rapport_json
        assert h.encode() not in a_bytes and h.encode() not in w_bytes
        assert h not in word_xml
    assert "SBK 2026-01234" in _text_docx(w)
    with zipfile.ZipFile(w) as z:
        assert any(n.startswith("word/media/") for n in z.namelist())  # logotypen


def test_saknad_resurs_ger_tydligt_fel_utan_sokvag(m: Miljo, tmp_path: Path) -> None:
    filer = _resurser(tmp_path)
    r = _full(m, installningar={"logotyp": filer["logotyp"]})
    for f in (m.rt.resursmapp(r.rapport_id)).iterdir():
        f.unlink()
    with pytest.raises(RapportFel, match="logotypen") as ei:
        m.rt.exportera(r.rapport_id, "pdf")
    assert str(tmp_path) not in str(ei.value)
    with pytest.raises(RapportFel, match="TTF eller OTF") as ei:
        m.rt.andra_installningar(r.rapport_id, {"typsnitt_brodtext": filer["logotyp"]})
    assert "lokal_hemlig_mapp" not in str(ei.value)
    trasig = tmp_path / "lokal_hemlig_mapp" / "trasig.otf"
    trasig.write_bytes(b"inte ett typsnitt")
    with pytest.raises(RapportFel, match="kunde inte läsas") as ei:
        m.rt.andra_installningar(r.rapport_id, {"typsnitt_rubriker": str(trasig)})
    assert "lokal_hemlig_mapp" not in str(ei.value)


def test_inplanterat_fel_stoppar_fortfarande_med_forvalen(m: Miljo) -> None:
    r = _full(m)
    assert r.installningar.sbk
    m.andra(r, _forvanska_varde)
    with pytest.raises(ExportStoppad, match="stämmer inte med körningen demo-vatten"):
        m.rt.exportera(r.rapport_id, "word")


def test_kontrollen_kanner_igen_tal_i_svensk_sifferform(m: Miljo) -> None:
    r = _full(m)

    def fn(d: dict[str, Any]) -> None:
        d["pastaenden"][2]["text"] += f" Utflödet är 7{SM}430 m³."

    m.andra(r, fn)
    fel = " ".join(m.fel(r, "sparbarhet"))
    assert "'7'" in fel and "'430'" in fel


def test_bildtext_i_dokumentet_kontrolleras(m: Miljo) -> None:
    r = _full(m)

    def fn(d: dict[str, Any]) -> None:
        d["kartor"][0]["scenario"] = "alternativ_b"

    m.andra(r, fn)
    assert any("alternativ_b" in f or "alternativ b" in f for f in m.fel(r, "karta"))


# ------------------------------------------------------------------ språktest


def test_sbk_lopttext_saknar_forbjudna_formuleringar(forval: dict[str, Any], m: Miljo) -> None:
    dok = bygg_dokument(forval["rapport"], m.rt)
    lop = huvudtext(dok)
    texter = [lop] + [
        " ".join(
            filter(
                None,
                (
                    text_utan_platshallare(kw["text"]),
                    kw.get("motivering"),
                    kw.get("forutsattning"),
                    *kw.get("saknat_underlag", []),
                ),
            )
        )
        for kw in pastaenden(sprakprofil="sbk")
    ]
    for t in texter:
        lag = t.lower()
        for fras in SBK_FORBJUDET:
            assert fras not in lag, fras
        for ord_ in ENGELSKA_ORD:
            assert not re.search(rf"(?<![\w.-]){re.escape(ord_)}(?![\w-])", lag), (ord_, t[:200])
        assert "bedömer" not in lag
        assert not re.search(r"\b(status|scenario)\s*:", lag)
        assert "_" not in t, "variabelnamn i löptexten"


def test_neutral_profil_har_kvar_tonen_fran_r1() -> None:
    texter = " ".join(kw["text"] for kw in pastaenden(sprakprofil="neutral"))
    assert "Ledningsnätet antas fullt under skyfallet" in texter


# ------------------------------------------------------------------ inställningar och config.local.toml


def test_lokala_forval_ur_config_local(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("[sokvagar]\n", encoding="utf-8")
    (tmp_path / "config.local.toml").write_text(
        '[rapport]\nsprakprofil = "neutral"\nnumrerade_rubriker = true\navsandare = ""\n'
        'diarienummer = "Dnr 1"\nlogotyp = "lokal/logotyp.png"\n',
        encoding="utf-8",
    )
    f = lokala_forval(tmp_path)
    assert f["sprakprofil"] == "neutral" and f["numrerade_rubriker"] is True
    assert f["avsandare"] == "" and f["logotyp"] == str(tmp_path / "lokal" / "logotyp.png")
    (tmp_path / "config.local.toml").write_text("[rapport]\nokand = 1\n", encoding="utf-8")
    with pytest.raises(InstallningsFel, match="Okänd inställning"):
        lokala_forval(tmp_path)


def test_installningar_valideras(tmp_path: Path) -> None:
    bas = RapportInstallningar()
    assert tillampa(bas, {"avsandare": ""}, tmp_path).avsandare == ""
    assert tillampa(bas, {"accentfarg": "#00ff00"}, tmp_path).accentfarg == "00FF00"
    for fel in (
        {"sprakprofil": "engelska"},
        {"accentfarg": "blå"},
        {"numrerade_figurer": "kanske"},
    ):
        with pytest.raises(InstallningsFel):
            tillampa(bas, fel, tmp_path)
    with pytest.raises(InstallningsFel, match="finns inte"):
        tillampa(bas, {"logotyp": str(tmp_path / "saknas.png")}, tmp_path)
