"""Om Floden: datafilen, OM-FLODEN.md och webbsidans data hänger ihop och håller vad de lovar."""

from __future__ import annotations

import copy

from gemensamt import metodregister as mr
from gemensamt import om_floden as om


def _alla() -> tuple[dict, dict, dict]:
    return om.las_om(), mr.las_register(), mr.las_metodstatus()


def test_datafilen_har_inga_fel() -> None:
    innehall, reg, ms = _alla()
    assert om.kontrollera(innehall, reg, ms) == []


def test_markdown_och_webbdata_ar_genererade_ur_datafilen() -> None:
    innehall, reg, ms = _alla()
    assert om.OM_MD.read_text(encoding="utf-8") == om.markdown(innehall, reg, ms), (
        "docs/OM-FLODEN.md är inte i takt: kör python tools/generera_om.py"
    )
    assert om.TS_UT.read_text(encoding="utf-8") == om.ts_innehall(innehall, reg, ms), (
        "webb/src/om/data.generated.ts är inte i takt: kör python tools/generera_om.py"
    )


def test_fem_amnen_och_metodstatus_ur_registret() -> None:
    innehall, reg, ms = _alla()
    d = om.data_for_webben(innehall, reg, ms)
    assert [a["id"] for a in d["amnen"]] == list(om.AMNEN)
    for a in d["amnen"]:
        assert a["metoder"], a["id"]
        for m in a["metoder"]:
            assert m["status"] in {"skiss", "rapport"}
    # Statusen sätts aldrig i datafilen; ingen metod är rapport än.
    assert "metodstatus" not in innehall["om"]
    assert d["metodAntal"]["rapport"] == 0
    assert d["metodAntal"]["skiss"] == d["metodAntal"]["alla"]


def test_uppmatta_tal_stammer_mot_benchmarkfilerna_och_faktorerna_raknas_om() -> None:
    innehall, _, _ = _alla()
    by = {u["id"]: u for u in innehall["uppmatt"]}
    lag, hog = om.faktor_for(by["webgpu_dagsljus"])  # type: ignore[misc]
    assert lag == hog and round(lag) == 79
    lag, hog = om.faktor_for(by["webgpu_vatten"])  # type: ignore[misc]
    assert round(lag) == 25
    assert om.faktor_for(by["hybrid_mot_2d"]) == (1.2, 1.8)
    lag, _ = om.faktor_for(by["lokal_omrakning"])  # type: ignore[misc]
    assert abs(lag - 9.28) < 0.02  # STATUS.md anger 9,28 (av ej avrundade tider)


def test_ett_felaktigt_tal_fangas() -> None:
    innehall, reg, ms = _alla()
    fel_a = copy.deepcopy(innehall)
    fel_a["kortid"][0]["varde_s"] = 9.99
    assert any("körtid ea1" in f for f in om.kontrollera(fel_a, reg, ms))
    fel_b = copy.deepcopy(innehall)
    fel_b["uppmatt"][0]["b_varde"] = 1.0
    assert any("webgpu_dagsljus" in f for f in om.kontrollera(fel_b, reg, ms))
    fel_c = copy.deepcopy(innehall)
    fel_c["amne"][4]["testat"][0]["belagg"] = "text som inte står i STATUS.md xyz"
    assert any("beläggstexten" in f for f in om.kontrollera(fel_c, reg, ms))


def test_ingen_jamforelse_med_en_produkt_utan_matning() -> None:
    innehall, reg, ms = _alla()
    d = copy.deepcopy(innehall)
    d["skillnad"][0]["text"] = "Floden är tre gånger snabbare än SoundPLAN."
    assert any("jämförelseord" in f for f in om.kontrollera(d, reg, ms))
    # Tillverkarnas egna uppgifter och förklaringen att det inte görs får innehålla orden.
    text = om.OM_MD.read_text(encoding="utf-8")
    assert "Det finns ingen jämförande mätning av samma fall" in text
    assert "Floden har inte kört något av verktygen" in text


def test_varje_andra_verktyg_har_lank_och_ingen_uppgift_saknas() -> None:
    innehall, reg, ms = _alla()
    verktyg = " ".join(x["verktyg"] for x in innehall["andra"])
    for namn in (
        "SCALGO",
        "MIKE 21",
        "InfoWorks",
        "ClimateStudio",
        "Accelerad",
        "SoundPLAN",
        "CadnaA",
        "Circuitscape",
        "Linkage Mapper",
        "Conefor",
    ):
        assert namn in verktyg, namn
    d = copy.deepcopy(innehall)
    d["andra"][0]["lankar"] = []
    assert any("länk till källan saknas" in f for f in om.kontrollera(d, reg, ms))


def test_planen_har_5_till_10_punkter_ur_plan_md_och_ingen_persondata() -> None:
    innehall, reg, ms = _alla()
    assert 5 <= len(innehall["plan"]) <= 10
    d = copy.deepcopy(innehall)
    d["plan"][0]["uppdrag"] = "ZZ99"
    assert any("ZZ99" in f for f in om.kontrollera(d, reg, ms))
    text = om.OM_MD.read_text(encoding="utf-8")
    assert "Samuel" not in text
    d2 = copy.deepcopy(innehall)
    d2["skillnad"][0]["text"] = "Kontakta Samuel."
    assert any("personuppgift" in f for f in om.kontrollera(d2, reg, ms))


def test_svensk_sifferform() -> None:
    assert om.tal_sv(4242.646, 1) == "4 242,6"
    assert om.tal_sv(0.55, 2) == "0,55"
    assert om.tal_sv(-0.16, 2) == "−0,16"
