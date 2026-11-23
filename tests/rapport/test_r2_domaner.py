"""Tester för R2-måttkatalog och deterministiska fält-/intervallbilagor (`rapport.r2_domaner`)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from buller.optimering import valj_bullerskarmar_k
from luft.kanslighet import kor_monoton_mkn_intervall
from rapport.r2_domaner import (
    MATTKATALOG_R2,
    bygg_bullerskarm_tabell,
    bygg_faltmatningstabell,
    bygg_mkn_intervallrad,
    formatera_svenskt_tal,
    lista_matt_for_doman,
    skapa_r2_fleromans_sammanfattning,
)
from sankgraf.bifurkation import BifurkationsPunkt


def test_r2_mattkatalog_tacker_alla_fyra_domaner() -> None:
    """Måttkatalogen för R2 ska ha enhets- och avrundningsspecar för alla 4 fysikdomäner."""
    for doman in ("skyfall", "dagsljus", "luft", "buller"):
        lista = lista_matt_for_doman(doman)
        assert len(lista) >= 2
        assert all(m.min_steg > 0.0 and m.enhet for m in lista)
    assert "dagsljus.vsc_procent" in MATTKATALOG_R2
    assert "luft.pm10_dygn_over_50" in MATTKATALOG_R2
    assert "buller.ekvivalent_niva_dba" in MATTKATALOG_R2


def test_faltmatningstabell_mkn_rad_och_bullertabell() -> None:
    """Deterministiska bilagetabeller ska använda svensk sifferform och rätt utfall."""
    assert formatera_svenskt_tal(1234.5, 1) == "1\u202f234,5"

    p = BifurkationsPunkt(
        nod_id=7,
        primar_ut_till_id=2,
        alternativ_ut_till_id=5,
        overfall_hojd_m=10.50,
        alternativ_hojd_m=10.54,
        marginal_m=0.04,
        osakerhet_komb_m=0.0707,
        sakerhetsmarginal=0.57,
        instabil=True,
        overfall_x=151200.4,
        overfall_y=6580300.2,
        alternativ_x=151210.0,
        alternativ_y=6580300.0,
        paverkad_avrinningsyta_m2=1200.0,
        paverkad_volym_m3=120.0,
    )
    tab = bygg_faltmatningstabell([p])
    assert tab["antal_instabila"] == 1
    assert "10,50" in tab["markdown"]
    assert "0,04" in tab["markdown"]

    # MKN-intervall som passerar 35 dygn/år -> osaker_mkn_grans == True
    lokal = np.full(365 * 24, 38.0, dtype=np.float64)
    iv = kor_monoton_mkn_intervall(
        lokal,
        bakgrund_ug_m3=12.0,
        emissionsfaktor_intervall=(0.8, 1.2),
        depositionsfaktor_intervall=(1.0, 1.0),
    )
    mkn_rad = bygg_mkn_intervallrad("Skolgård norr", iv)
    assert mkn_rad["osaker_mkn_grans"] is True
    assert "fördjupad utredning" in mkn_rad["status_text"]

    # Bullerskärmstabell
    bas = np.array([62.0, 60.0, 58.0])
    val = valj_bullerskarmar_k(
        bas,
        {"S1": np.array([8.0, 6.0, 0.0]), "S2": np.array([0.0, 0.0, 4.0])},
        k=2,
        riktvarde_db=55.0,
    )
    btab = bygg_bullerskarm_tabell(val, riktvarde_db=55.0)
    assert btab["antal_skarmar"] == 2
    assert "| 1 | S1 |" in btab["markdown"]


def test_skapa_r2_fleromans_sammanfattning_alla_fyra_domaner() -> None:
    """Samlad R2-sammanfattning ska inkludera exakt de domäner som har beräknade indata."""
    p = BifurkationsPunkt(
        nod_id=3,
        primar_ut_till_id=1,
        alternativ_ut_till_id=2,
        overfall_hojd_m=12.10,
        alternativ_hojd_m=12.13,
        marginal_m=0.03,
        osakerhet_komb_m=0.07,
        sakerhetsmarginal=0.43,
        instabil=True,
        overfall_x=151000.0,
        overfall_y=6580000.0,
        alternativ_x=151010.0,
        alternativ_y=6580000.0,
        paverkad_avrinningsyta_m2=800.0,
        paverkad_volym_m3=80.0,
    )
    iv = kor_monoton_mkn_intervall(
        np.full(365 * 24, 20.0, dtype=np.float64),
        bakgrund_ug_m3=10.0,
        emissionsfaktor_intervall=(0.9, 1.1),
        depositionsfaktor_intervall=(1.0, 1.0),
    )
    val = valj_bullerskarmar_k(
        np.array([61.0, 59.0]),
        {"Skärm A": np.array([7.0, 5.0])},
        k=1,
        riktvarde_db=55.0,
    )
    sam = skapa_r2_fleromans_sammanfattning(
        "Demokvarteret",
        bifurkationer=[p],
        andel_2d_yta=0.25,
        svf_intervall=(0.42, 0.51),
        mkn_intervall=[("Gård syd", iv)],
        bullerskarmar=val,
    )
    assert sam["format"] == "floden-r2-sammanfattning/v1"
    assert set(sam["domaner"].keys()) == {"skyfall", "dagsljus", "luft", "buller"}
    assert "25,0 %" in sam["markdown"]
    assert "0,42–0,51" in sam["markdown"]
    assert "Gård syd" in sam["markdown"]
    assert "| 1 | Skärm A |" in sam["markdown"]


def test_exportera_q2_underlag_pa_a9(tmp_path: Path) -> None:
    """Q2-exportverktyget ska producera giltiga JSON- och GeoPackage-filer för A9."""
    tools_rot = Path(__file__).resolve().parents[2] / "tools"
    if str(tools_rot) not in sys.path:
        sys.path.insert(0, str(tools_rot))
    from exportera_q2_underlag import exportera_q2_underlag

    res = exportera_q2_underlag(
        Path("testsvit/A9_brus_persistens/indata"),
        tmp_path / "q2_ut",
    )
    assert res["antal_noder"] >= 1
    bif_path = Path(res["bifiltrering_json"])
    assert bif_path.is_file()
    assert Path(res["bifurkationer_json"]).is_file()
    assert Path(res["bifurkationer_gpkg"]).is_file()
    bif_data = json.loads(bif_path.read_text(encoding="utf-8"))
    assert bif_data["format"] == "floden-bifiltrering/v1"
    assert bif_data["antal_noder"] == res["antal_noder"]
