"""Metodregistret, metodtabellen i docs/MATEMATIK.md och datafilen till webbsidan ska hänga ihop."""

from __future__ import annotations

import re

from gemensamt import metodregister as mr

# Metodstatus-poster utan egen matematisk metod i registret.
UTAN_METOD = {"geometri.lutning"}


def _reg() -> tuple[dict, dict]:
    return mr.las_register(), mr.las_metodstatus()


def test_registret_har_inga_fel() -> None:
    reg, ms = _reg()
    assert mr.kontrollera(reg, ms) == []


def test_alla_domaner_och_fem_fragor_finns() -> None:
    reg, _ = _reg()
    assert list(reg["doman"]) == ["vatten", "dagsljus", "luft", "buller", "ekologi"]
    assert {m["doman"] for m in reg["metod"]} == set(reg["doman"])
    assert [q["id"] for q in reg["fraga"]] == ["Q1", "Q2", "Q3", "Q4", "Q5"]


def test_varje_metodstatuspost_ar_tackt_av_en_metod() -> None:
    reg, ms = _reg()
    tackta = {n for m in reg["metod"] for n in mr.metodstatus_nycklar(m)}
    saknas = set(ms) - {"defaults"} - tackta - UTAN_METOD
    assert saknas == set(), f"Metodstatus utan metod i registret: {sorted(saknas)}"


def test_status_kommer_ur_metodstatus_och_ingen_ar_rapport() -> None:
    reg, ms = _reg()
    for m in reg["metod"]:
        assert "status" not in m, "klassningen sätts bara i data/mcp/metodstatus.toml"
        assert mr.status_for(m, ms) in {"skiss", "rapport"}
    # En klassning i metodstatus slår igenom i genererade data.
    ms2 = {**ms, "vatten.dynamik": {"status": "rapport"}}
    dyn = next(m for m in reg["metod"] if m["id"] == "vatten.dynamik")
    assert mr.status_for(dyn, ms2) == "rapport"
    assert mr.status_for(dyn, ms) == ms["vatten.dynamik"]["status"]


def test_ocksa_nycklar_kraver_alla_rapport() -> None:
    reg, ms = _reg()
    dag = next(m for m in reg["metod"] if m["id"] == "dagsljus.horisont_svf_vsc")
    ms2 = {**ms, "dagsljus.horisont": {"status": "rapport"}}
    assert mr.status_for(dag, ms2) == "skiss"


def test_markdown_ar_genererad_ur_registret() -> None:
    reg, ms = _reg()
    ny = mr.md_med_tabell(mr.MATEMATIK_MD.read_text(encoding="utf-8"), mr.markdown_block(reg, ms))
    assert ny == mr.MATEMATIK_MD.read_text(
        encoding="utf-8"
    ), "docs/MATEMATIK.md är inte i takt med registret: kör python tools/generera_matematik.py"


def test_webbdata_ar_genererad_ur_registret() -> None:
    reg, ms = _reg()
    assert mr.TS_UT.read_text(encoding="utf-8") == mr.ts_innehall(
        reg, ms
    ), "webb/src/matematik/data.generated.ts är inte i takt: kör tools/generera_matematik.py"


def test_dokumentet_har_formler_i_latex_och_fragorna_q1_till_q5() -> None:
    reg, _ = _reg()
    text = mr.MATEMATIK_MD.read_text(encoding="utf-8")
    for m in reg["metod"]:
        for f in m["formel"]:
            assert f"$$ {f['latex']} $$" in text, m["id"]
    for q in reg["fraga"]:
        assert re.search(rf"^### {q['id']} – ", text, re.M), q["id"]
    assert "tools/exportera_q2_underlag.py" in text
    assert "K35" in text


def test_dokumentet_namnger_inte_forskningsparten() -> None:
    text = mr.MATEMATIK_MD.read_text(encoding="utf-8")
    assert "Samuel" not in text
    assert "person X" not in text
    assert "forskningspart" in text


def test_sokvagar_i_posterna_tolkas() -> None:
    post = "src/dagsljus/intervall.py (horisont_1d_konvext_holje)"
    assert mr.sokvag_i(post) == "src/dagsljus/intervall.py"
    assert mr.sokvag_i("testsvit grupp E (`floden testsvit --grupp E`)") is None
    assert mr.sokvag_i("K35") is None


def test_kanda_avvikelser_finns_i_register_och_dokument() -> None:
    reg, ms = _reg()
    avv = reg.get("avvikelse", [])
    assert len(avv) >= 6
    assert any(a["id"] == "avv.buller.medvind_dal" for a in avv)
    webb = mr.data_for_webben(reg, ms)
    assert len(webb["avvikelser"]) == len(avv)
    text = mr.MATEMATIK_MD.read_text(encoding="utf-8")
    assert "## Kända avvikelser och begränsningar" in text
    for a in avv:
        assert a["titel"] in text
