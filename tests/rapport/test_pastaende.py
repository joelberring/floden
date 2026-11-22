"""K32: platshållare, talformat, statusregler och frågekatalogen (utan körningar)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rapport.fragekatalog import FLODEN_FORMAGA, Fragekatalog, formagetabell_markdown
from rapport.pastaende import (
    Belagg,
    Pastaende,
    PastaendeFel,
    formatera_tal,
    fyll_text,
    las_platshallare,
    minsta_steg,
    siffror_utanfor_platshallare,
    standardsteg,
    statusfel,
    textfel,
)
from rapport.skisskontroll import mattfel, scenariofel

REPO = Path(__file__).resolve().parents[2]
NB = " "


def belagg(adress: str = "jamforelse.j.yta_okning_m2", **over: object) -> Belagg:
    d: dict[str, object] = {
        "adress": adress,
        "korning_id": "k",
        "fil": "jamforelser/j/diff.tif",
        "fil_sha256": "a" * 64,
        "manifest": "run.json",
        "manifest_sha256": "b" * 64,
        "analys": "skyfall",
        "losare": "vatten.statisk_fyllning",
        "scenarier": ("fore", "efter"),
        "mottagare": "hela beräkningsområdet",
        "namnare": "mark",
        "matt": "max_djup:yta_okning_m2",
        "storhet": "skillnad i vattendjup över markytan",
        "enhet": "m²",
        "tidsdefinition": "läget vid regnets slut",
        "statistik": "yta på mark där värdet ökat mer än tröskeln",
        "varde": 3258.0,
        "upplosning": 1.0,
        "jamforbarhet": "c" * 64,
    }
    d.update(over)
    return Belagg(**d)  # type: ignore[arg-type]


def pastaende(**over: object) -> Pastaende:
    b = belagg()
    d: dict[str, object] = {
        "pastaende_id": "p-001",
        "fraga_id": "SK-06",
        "status": "visat",
        "text": "Djupare på {{matt:" + b.adress + "}} mark.",
        "belagg": (b,),
        "fordjupning": "ja",
    }
    d.update(over)
    return Pastaende(**d)  # type: ignore[arg-type]


# ------------------------------------------------------------------ talformat och platshållare


def test_svensk_talform_med_enhet_och_precision() -> None:
    assert formatera_tal(3258.0, "m²", 10) == f"3260{NB}m²"
    assert formatera_tal(12345.6, "m³", 10) == f"12{NB}350{NB}m³"
    assert formatera_tal(0.1590404, "m", 0.01) == f"0,16{NB}m"
    assert formatera_tal(-0.1590404, "m", 0.01) == f"−0,16{NB}m"
    assert formatera_tal(-0.001, "m", 0.01) == f"0,00{NB}m"  # inget minustecken på noll
    assert formatera_tal(0.16, "m", 0.01, tecken=True) == f"+0,16{NB}m"
    assert formatera_tal(1.34e-14, "andel", 0.01, exponent=True) == "1,3 × 10⁻¹⁴"
    assert formatera_tal(1.4, None, 0.1) == "1,4"


def test_standardprecision_foljer_underlaget() -> None:
    assert standardsteg("m²", 3258.0, 1.0) == 10.0
    assert standardsteg("m²", 300.0, 4.0) == 5.0  # 2 m-celler: aldrig finare än 4 m²
    assert standardsteg("m", 0.4, 0.001) == 0.01
    assert minsta_steg(4.0) == 5.0
    assert minsta_steg(0.001) == 0.001


def test_platshallare_fylls_ur_belagget() -> None:
    b = belagg()
    assert fyll_text("Yta {{matt:" + b.adress + "|avrundning=100}}.", {b.adress: b}) == (
        f"Yta 3300{NB}m²."
    )
    assert fyll_text("{{matt:" + b.adress + "|enhet=nej}}", {b.adress: b}) == "3260"


@pytest.mark.parametrize(
    ("text", "fel"),
    [
        ("{{matt:jamforelse.j.yta_okning_m2|avrundning=0,1}}", "finare än underlaget"),
        ("{{matt:jamforelse.j.yta_okning_m2|avrundning=3}}", "1, 2 eller 5"),
        ("{{matt:jamforelse.j.yta_okning_m2|enhet=m³}}", "enheten 'm³'"),
        ("{{matt:jamforelse.j.yta_okning_m2|farg=rod}}", "Okänd option"),
        ("{{tal:jamforelse.j.yta_okning_m2}}", "Okänd platshållartyp"),
        ("{{matt:jamforelse.annan.max_okning}}", "pekar inte på"),
        ("{{matt:jamforelse.j.yta_okning_m2", "trasig platshållare"),
    ],
)
def test_ogiltiga_platshallare_ger_begripligt_fel(text: str, fel: str) -> None:
    b = belagg()
    with pytest.raises(PastaendeFel, match=fel):
        fyll_text(text, {b.adress: b})


def test_siffror_utanfor_platshallare_men_inte_beteckningar() -> None:
    assert siffror_utanfor_platshallare("Vattnet blir 20 cm djupare.") == ["20"]
    assert siffror_utanfor_platshallare("Djupet är 0,16 m.") == ["0,16"]
    text = (
        "SK-06 enligt Stockholmsregn 2024 (Tabell 7), RH 2000, EPSG:3011 och K7: "
        "{{matt:jamforelse.j.max_okning}}."
    )
    assert siffror_utanfor_platshallare(text) == []
    assert len(las_platshallare(text)) == 1


# ------------------------------------------------------------------ K32 rundtur och statusregler


def test_k32_rundtur() -> None:
    p = pastaende(saknat_underlag=("x",), status="underlag_saknas")
    d = p.till_dict()
    assert d["kontrakt"] == "K32/v1"
    assert Pastaende.fran_dict(d) == p
    with pytest.raises(PastaendeFel, match="K32/v1"):
        Pastaende.fran_dict({**d, "kontrakt": "K32/v0"})


@pytest.mark.parametrize(
    ("over", "fel"),
    [
        ({"belagg": (), "text": "Djupare."}, "kräver minst ett belägg"),
        ({"status": "villkorat"}, "uttrycklig förutsättning"),
        ({"status": "villkorat", "forutsattning": "x"}, "uttrycklig förutsättning"),
        ({"status": "ej_tillampligt", "fordjupning": "nej"}, "saklig motivering"),
        ({"status": "underlag_saknas"}, "saknade underlaget"),
        (
            {"status": "underlag_saknas", "saknat_underlag": ("x",), "fordjupning": "nej"},
            "kan sållningen inte",
        ),
        ({"status": "avvikelse", "fordjupning": "nej"}, "behöver fördjupas"),
        ({"status": "visat", "fordjupning": "kan_inte_avgoras"}, "motsäger"),
        ({"status": "klart"}, "okänd status"),
        ({"fordjupning": "kanske"}, "okänt fördjupningsbehov"),
    ],
)
def test_statusregler_negativa(over: dict[str, object], fel: str) -> None:
    assert any(fel in f for f in statusfel(pastaende(**over))), statusfel(pastaende(**over))


def test_statusregler_positiva() -> None:
    assert statusfel(pastaende()) == []
    assert (
        statusfel(
            pastaende(
                status="villkorat", forutsattning="Beställaren väljer regn.", ansvarig_funktion="X"
            )
        )
        == []
    )
    assert statusfel(pastaende(status="ej_tillampligt", fordjupning="nej", motivering="m")) == []


def test_textregler_siffror_och_saknade_belagg() -> None:
    assert any("'20'" in f for f in textfel(pastaende(text="Ökar 20 cm {{matt:x}}.")))
    assert any("saknar belägg" in f for f in textfel(pastaende(text="{{matt:annan.a.b}}")))
    assert any(
        "inte i 'motivering'" in f
        for f in textfel(pastaende(motivering="{{matt:jamforelse.j.yta_okning_m2}}"))
    )


def test_matt_och_scenarioregler() -> None:
    b = belagg(storhet="vattendjup över markytan", statistik="största värde")
    p = pastaende(belagg=(b,), text="Vattennivån blir {{matt:" + b.adress + "}}.")
    assert any("inte en vattennivå" in f for f in mattfel(p))
    p = pastaende(belagg=(b,), text="I medel blir det {{matt:" + b.adress + "}}.")
    assert any("medelvärde" in f for f in mattfel(p))
    ej = belagg(jamforbarhet="EJ_JAMFORBAR:fore=aaa/efter=bbb")
    from rapport.skissrapport import Rapport

    r = Rapport.__new__(Rapport)
    object.__setattr__(r, "niva", 1)
    assert any("olika analysparametrar" in f for f in scenariofel(pastaende(belagg=(ej,)), r))
    b2 = belagg("matvarde.skyfall.efter.lagrat_m3", jamforbarhet="d" * 64)
    p2 = pastaende(belagg=(belagg(), b2))
    assert any("olika analysparametrar" in f for f in scenariofel(p2, r))


# ------------------------------------------------------------------ frågekatalogen


def test_fragekatalogen_och_filter() -> None:
    k = Fragekatalog.las()
    assert len(k.fragor) == 75
    sk = k.sok(omrade="skyfall")
    assert [f.id for f in sk] == [f"SK-{i:02d}" for i in range(1, 16)]
    plan = k.sok(omrade="skyfall", villkor=["planförslag"])
    assert {"SK-03", "SK-06"} <= {f.id for f in plan}  # 'Alla områden' gäller alltid
    assert "SK-08" not in {f.id for f in plan}
    assert set(FLODEN_FORMAGA) == {f.id for f in sk}


def test_fragemappningen_i_rapporter_md_stammer_med_koden() -> None:
    doc = (REPO / "docs" / "RAPPORTER.md").read_text(encoding="utf-8")
    assert formagetabell_markdown() in doc
    # ingen fråga har förvald status 'visat' utan att Floden kan ge belägg för den
    for fid, fm in FLODEN_FORMAGA.items():
        if fm.forval_status == "visat":
            assert fm.adresser, fid
