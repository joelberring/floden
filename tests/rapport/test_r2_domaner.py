"""Tester för R2-måttkatalog och deterministiska fält-/intervallbilagor (`rapport.r2_domaner`)."""

from __future__ import annotations

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
