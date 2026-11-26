# SPDX-License-Identifier: GPL-3.0-or-later
"""Enhetstester för U18: deterministisk SMHI `metobs` CSV/JSON-inläsare, vindros och UTCI."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from geografi.smhi_vader import (
    PARAM_GLOBALSTRALNING,
    PARAM_RELATIV_FUKTIGHET,
    PARAM_TEMPERATUR,
    PARAM_VINDHASTIGHET,
    PARAM_VINDRIKTNING,
    SEKTORNAMN_16,
    STRIKT_GODKANDA_KVALITETER,
    SmhiVaderFel,
    berakna_varmebolja_utci,
    berakna_vindros_16,
    bygg_vindkomfort_underlag,
    las_smhi_csv_fil,
    las_smhi_csv_text,
    las_smhi_fil,
    las_smhi_json_fil,
    las_smhi_json_text,
    riktning_till_sektor16,
)
from luft.vindbibliotek import Vaderserie
from mikroklimat.utci import berakna_utci


def _epoch_ms(iso_utc: str) -> int:
    dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


# ------------------------------------------------------------------ 1. CSV/JSON & kvalitetsflaggor


def test_las_smhi_csv_kvalitetsflaggor_och_bortfiltrerade_utan_tyst_fel(tmp_path: Path) -> None:
    csv_text = (
        "\ufeffStationsnamn;Stationsnummer;Mätstationsgrupp\n"
        "Stockholm-Observatoriekullen A;98210;SMHI\n"
        "Parameternamn;Beskrivning;Enhet\n"
        "Lufttemperatur;momentanvärde, 1 gång/tim;degree celsius\n"
        "\n"
        "Datum;Tid (UTC);Lufttemperatur;Kvalitet;;Tidsutsnitt:\n"
        "2024-07-15;10:00:00;24,5;G;;Kvalitetskontrollerade historiska data\n"
        "2024-07-15;11:00:00;26.0;Y;;\n"
        "2024-07-15;12:00:00;99.0;O;;\n"
        "2024-07-15;13:00:00;18.0;R;;\n"
        "2024-07-15;14:00:00;;G;;\n"
        "2024-07-15;15:00:00;120.0;G;;\n"
    )
    fil = tmp_path / "smhi_temp.csv"
    fil.write_text(csv_text, encoding="utf-8")

    # Standard godkänner G och Y: 2 godkända, 4 bortfiltrerade (O, R, tomt värde, intervallfel).
    serie = las_smhi_csv_fil(fil)
    assert serie.station_id == "98210"
    assert serie.station_namn == "Stockholm-Observatoriekullen A"
    assert serie.parameter_id == PARAM_TEMPERATUR
    assert serie.antal_totalt == 6
    assert serie.antal_godkanda == 2
    assert serie.bortfiltrerade == 4
    assert [o.varde for o in serie.observationer] == [24.5, 26.0]
    assert serie.bortfiltrerade_per_orsak == {
        "intervallfel": 1,
        "kvalitetsflagga:O": 1,
        "kvalitetsflagga:R": 1,
        "ogiltigt_varde": 1,
    }
    assert serie.flaggor_fordelning == {"G": 3, "O": 1, "R": 1, "Y": 1}

    # Strikt läge (bara G): Y filtreras också bort explicit.
    strikt = las_smhi_csv_text(csv_text, godkanda_flaggor=STRIKT_GODKANDA_KVALITETER)
    assert strikt.antal_godkanda == 1
    assert strikt.bortfiltrerade == 5
    assert strikt.bortfiltrerade_per_orsak["kvalitetsflagga:Y"] == 1


def test_las_smhi_json_och_autodetektering_fil(tmp_path: Path) -> None:
    payload = {
        "station": {"key": "71420", "name": "Göteborg A", "height": 5.0},
        "parameter": {"key": "11", "name": "Global Irradians (svenska stationer)", "unit": "W/m2"},
        "value": [
            {"to": _epoch_ms("2024-07-15T11:00:00Z"), "value": "680.5", "quality": "G"},
            {"to": _epoch_ms("2024-07-15T12:00:00Z"), "value": "740.0", "quality": "Y"},
            {"to": _epoch_ms("2024-07-15T13:00:00Z"), "value": "800.0", "quality": "O"},
            {"to": _epoch_ms("2024-07-15T14:00:00Z"), "value": "-5.0", "quality": "G"},
        ],
    }
    fil = tmp_path / "smhi_stralning.json"
    fil.write_text(json.dumps(payload), encoding="utf-8")

    serie = las_smhi_fil(fil)
    assert serie.station_id == "71420"
    assert serie.station_namn == "Göteborg A"
    assert serie.parameter_id == PARAM_GLOBALSTRALNING
    assert serie.antal_totalt == 4
    assert serie.antal_godkanda == 2
    assert serie.bortfiltrerade == 2
    assert serie.bortfiltrerade_per_orsak == {"intervallfel": 1, "kvalitetsflagga:O": 1}
    assert [o.varde for o in serie.observationer] == [680.5, 740.0]


# -------------------------------------------------------------- 2. Handräknad 16-sektors vindros


@pytest.mark.parametrize(
    ("grad", "forvantat"),
    [
        (0.0, "N"),
        (360.0, "N"),
        (348.75, "N"),
        (11.24, "N"),
        (11.25, "NNO"),
        (22.5, "NNO"),
        (90.0, "O"),
        (180.0, "S"),
        (225.0, "SV"),
        (270.0, "V"),
        (337.5, "NNV"),
    ],
)
def test_riktning_till_sektor16_granser(grad: float, forvantat: str) -> None:
    idx = riktning_till_sektor16(grad)
    assert SEKTORNAMN_16[idx] == forvantat


def test_handraknad_vindros_16_summa_100_och_vindkomfort_kompatibel() -> None:
    # 10 giltiga timmar + 1 underkänd (flagga O) + 1 oparad timme (bara hastighet).
    # Av de 10 giltiga:
    # - 2 svag vind (< 0.5 m/s): 0.0 m/s @ 0°, 0.3 m/s @ 180° -> 20 % svag vind.
    # - 4 i sektor V (270°, index 12): 2.0, 4.0, 6.0, 8.0 m/s -> 40 % av alla, 50 % av vind.
    #   medel = 5.0 m/s, p95 = np.percentile([2,4,6,8], 95) = 7.7 m/s.
    # - 3 i sektor N (0°/355°/10°, index 0): 3.0, 6.0, 9.0 m/s -> 30 % av alla, 37.5 % av vind.
    #   medel = 6.0 m/s, p95 = np.percentile([3,6,9], 95) = 8.7 m/s.
    # - 1 i sektor O (90°, index 4): 5.0 m/s -> 10 % av alla, 12.5 % av vind.
    #   medel = 5.0 m/s, p95 = 5.0 m/s.
    rader_u = [
        ("2024-07-01", "00:00:00", "0.0", "G"),
        ("2024-07-01", "01:00:00", "0.3", "G"),
        ("2024-07-01", "02:00:00", "2.0", "G"),
        ("2024-07-01", "03:00:00", "4.0", "G"),
        ("2024-07-01", "04:00:00", "6.0", "Y"),
        ("2024-07-01", "05:00:00", "8.0", "G"),
        ("2024-07-01", "06:00:00", "3.0", "G"),
        ("2024-07-01", "07:00:00", "6.0", "G"),
        ("2024-07-01", "08:00:00", "9.0", "G"),
        ("2024-07-01", "09:00:00", "5.0", "G"),
        ("2024-07-01", "10:00:00", "25.0", "O"),  # underkänd kvalitetsflagga
        ("2024-07-01", "11:00:00", "4.0", "G"),  # oparad (saknar riktning)
    ]
    rader_d = [
        ("2024-07-01", "00:00:00", "0", "G"),
        ("2024-07-01", "01:00:00", "180", "G"),
        ("2024-07-01", "02:00:00", "270", "G"),
        ("2024-07-01", "03:00:00", "265", "G"),
        ("2024-07-01", "04:00:00", "275", "Y"),
        ("2024-07-01", "05:00:00", "270", "G"),
        ("2024-07-01", "06:00:00", "0", "G"),
        ("2024-07-01", "07:00:00", "355", "G"),
        ("2024-07-01", "08:00:00", "10", "G"),
        ("2024-07-01", "09:00:00", "90", "G"),
        ("2024-07-01", "10:00:00", "90", "G"),
    ]

    csv_u = "Datum;Tid (UTC);Vindhastighet;Kvalitet\n" + "\n".join(";".join(r) for r in rader_u)
    csv_d = "Datum;Tid (UTC);Vindriktning;Kvalitet\n" + "\n".join(";".join(r) for r in rader_d)
    serie_u = las_smhi_csv_text(csv_u, parameter_id=PARAM_VINDHASTIGHET)
    serie_d = las_smhi_csv_text(csv_d, parameter_id=PARAM_VINDRIKTNING)

    ros = berakna_vindros_16(serie_u, serie_d, svag_vind_grans_m_s=0.5)
    assert len(ros.sektorer) == 16
    assert ros.antal_giltiga == 10
    assert ros.antal_svag_vind == 2
    assert ros.andel_svag_vind_pct == pytest.approx(20.0)
    # Vindrossumma (16 sektorer + svag vind) = exakt 100.0 %!
    assert sum(s.andel_pct for s in ros.sektorer) + ros.andel_svag_vind_pct == pytest.approx(100.0)
    assert ros.summa_andel_pct == pytest.approx(100.0)
    assert sum(s.andel_av_vind_pct for s in ros.sektorer) == pytest.approx(100.0)

    sektor_n = ros.sektorer[0]
    sektor_o = ros.sektorer[4]
    sektor_v = ros.sektorer[12]
    assert sektor_v.namn == "V" and sektor_v.centrum_grad == 270.0
    assert sektor_v.antal == 4
    assert sektor_v.andel_pct == pytest.approx(40.0)
    assert sektor_v.andel_av_vind_pct == pytest.approx(50.0)
    assert sektor_v.medelvind_m_s == pytest.approx(5.0)
    assert sektor_v.p95_vind_m_s == pytest.approx(7.7)

    assert sektor_n.namn == "N" and sektor_n.antal == 3
    assert sektor_n.andel_pct == pytest.approx(30.0)
    assert sektor_n.medelvind_m_s == pytest.approx(6.0)
    assert sektor_n.p95_vind_m_s == pytest.approx(8.7)

    assert sektor_o.namn == "O" and sektor_o.antal == 1
    assert sektor_o.andel_pct == pytest.approx(10.0)
    assert sektor_o.medelvind_m_s == pytest.approx(5.0)

    # Tomma sektorer har antal=0 och medelvind/p95 = None (aldrig påhittat tal).
    assert ros.sektorer[1].antal == 0
    assert ros.sektorer[1].medelvind_m_s is None
    assert ros.sektorer[1].p95_vind_m_s is None

    # Bortfiltrerade räknar både kvalitetsflaggan O (1) och oparade timmar (2: kl 10 & 11).
    assert ros.bortfiltrerade == 3
    assert ros.bortfiltrerade_per_orsak["vindhastighet:kvalitetsflagga:O"] == 1
    assert ros.bortfiltrerade_per_orsak["oparad_timme"] == 2

    # Utan svag vind-tröskel (0.0 m/s) summerar de 16 sektorerna ensamma till exakt 100.0 %.
    ros_alla = berakna_vindros_16(serie_u, serie_d, svag_vind_grans_m_s=0.0)
    assert ros_alla.andel_svag_vind_pct == 0.0
    assert sum(s.andel_pct for s in ros_alla.sektorer) == pytest.approx(100.0)

    # Underlaget kan laddas rakt in i `luft.vindbibliotek.Vaderserie` för `luft.vindkomfort`.
    underlag = bygg_vindkomfort_underlag(serie_u, serie_d)
    vs = Vaderserie(
        tid=underlag["tid"],
        vindriktning_grad=underlag["vindriktning_grad"],
        vindhastighet_m_s=underlag["vindhastighet_m_s"],
        giltig=underlag["giltig"],
    )
    assert int(vs.giltig.sum()) == 10
    assert underlag["antal_ogiltiga"] == 2  # timme 10 (underkänd) och timme 11 (oparad)


# -------------------------------------------------------- 3. Handräknade värmeböljepercentiler


def test_handraknad_varmebolja_p95_p99_och_sammanhorande_utci() -> None:
    # 21 jämnt fördelade sommartimmar i juli: T = 15.0, 16.0, ..., 35.0 °C (steg 1.0 °C).
    # Exakta percentiler (linjär interpolation på index 0..20):
    # - p95 = index 0.95 * 20 = 19.0 -> exakt 34.0 °C (timmar med T >= 34: T=34 och T=35).
    # - p99 = index 0.99 * 20 = 19.8 -> exakt 34.8 °C (timmar med T >= 34.8: bara T=35).
    # Dessutom lägger vi in 2 vintertimmar (januari, -5 °C och +40 °C) som ska filtreras bort
    # av sommarfiltret, plus 1 ogiltig sommarobservation med kvalitetsflagga O (+50 °C).
    temp_poster: list[dict[str, object]] = [
        {"date": _epoch_ms("2024-01-15T12:00:00Z"), "value": "-5.0", "quality": "G"},
        {"date": _epoch_ms("2024-01-15T13:00:00Z"), "value": "40.0", "quality": "G"},
        {"date": _epoch_ms("2024-07-02T12:00:00Z"), "value": "50.0", "quality": "O"},
    ]
    rh_poster: list[dict[str, object]] = []
    vind_poster: list[dict[str, object]] = []
    stral_poster: list[dict[str, object]] = []

    for i in range(21):
        ts_ms = _epoch_ms(f"2024-07-01T{i:02d}:00:00Z")
        t_val = 15.0 + float(i)
        temp_poster.append({"date": ts_ms, "value": f"{t_val:.1f}", "quality": "G"})
        # Sätt tydliga sammanhörande RH/vind/strålning för de hetaste timmarna:
        # i=19 (T=34): RH=40 %, vind=2.0 m/s, strålning=700 W/m²
        # i=20 (T=35): RH=30 %, vind=1.0 m/s, strålning=800 W/m²
        if i == 19:
            rh_v, u_v, g_v = 40.0, 2.0, 700.0
        elif i == 20:
            rh_v, u_v, g_v = 30.0, 1.0, 800.0
        else:
            rh_v, u_v, g_v = 65.0, 4.5, 300.0
        rh_poster.append({"date": ts_ms, "value": f"{rh_v:.1f}", "quality": "G"})
        vind_poster.append({"date": ts_ms, "value": f"{u_v:.1f}", "quality": "G"})
        stral_poster.append({"to": ts_ms, "value": f"{g_v:.1f}", "quality": "G"})

    s_temp = las_smhi_json_text(
        json.dumps(
            {
                "station": {"key": "98210", "name": "Stockholm A"},
                "parameter": {"key": "1", "name": "Lufttemperatur", "unit": "degree celsius"},
                "value": temp_poster,
            }
        )
    )
    s_rh = las_smhi_json_text(
        json.dumps({"parameter": {"key": "6"}, "value": rh_poster}),
        parameter_id=PARAM_RELATIV_FUKTIGHET,
    )
    s_vind = las_smhi_json_text(
        json.dumps({"parameter": {"key": "4"}, "value": vind_poster}),
        parameter_id=PARAM_VINDHASTIGHET,
    )
    s_stral = las_smhi_json_text(
        json.dumps({"parameter": {"key": "11"}, "value": stral_poster}),
        parameter_id=PARAM_GLOBALSTRALNING,
    )

    stats = berakna_varmebolja_utci(
        s_temp,
        relativ_fuktighet=s_rh,
        vindhastighet=s_vind,
        globalstralning=s_stral,
    )
    assert stats.antal_timmar_analyserade == 21
    assert stats.bortfiltrerade == 1  # flagga O i temperaturserien
    assert stats.t_medel_c == pytest.approx(25.0)
    assert stats.t_p95_c == pytest.approx(34.0)
    assert stats.t_p99_c == pytest.approx(34.8)
    assert stats.t_max_c == pytest.approx(35.0)

    # p95-urvalet (T >= 34.0): 2 timmar -> median RH = (40+30)/2 = 35 %, vind = 1.5 m/s, G = 750.
    assert stats.scenario_p95.antal_timmar_i_urval == 2
    assert stats.scenario_p95.rh_pct == pytest.approx(35.0)
    assert stats.scenario_p95.vind_10m_m_s == pytest.approx(1.5)
    assert stats.scenario_p95.globalstralning_w_m2 == pytest.approx(750.0)

    # p99-urvalet (T >= 34.8): 1 timme (T=35) -> RH = 30 %, vind = 1.0 m/s, G = 800.
    assert stats.scenario_p99.antal_timmar_i_urval == 1
    assert stats.scenario_p99.rh_pct == pytest.approx(30.0)
    assert stats.scenario_p99.vind_10m_m_s == pytest.approx(1.0)
    assert stats.scenario_p99.globalstralning_w_m2 == pytest.approx(800.0)

    # Kontrollera att utdata fungerar direkt med Flodens verkliga UTCI-fysikkärna.
    assert stats.scenario_p95.rh_pct is not None
    assert stats.scenario_p95.vind_10m_m_s is not None
    utci_res = berakna_utci(
        t_luft_c=stats.scenario_p95.t_luft_c,
        t_mrt_c=stats.scenario_p95.t_luft_c + 20.0,
        vind_10m_m_s=stats.scenario_p95.vind_10m_m_s,
        relativ_fuktighet_pct=stats.scenario_p95.rh_pct,
    )
    assert np.isfinite(utci_res.utci_c) and float(utci_res.utci_c) > 35.0


def test_varmebolja_saknad_rh_och_vind_hittar_aldrig_pa_tal() -> None:
    csv_t = (
        "Datum;Tid (UTC);Lufttemperatur;Kvalitet\n"
        "2024-07-10;12:00:00;29.0;G\n"
        "2024-07-10;13:00:00;31.0;G\n"
    )
    s_temp = las_smhi_csv_text(csv_t, parameter_id=PARAM_TEMPERATUR)
    stats = berakna_varmebolja_utci(s_temp)
    assert stats.scenario_p95.rh_pct is None
    assert stats.scenario_p95.vind_10m_m_s is None
    assert stats.scenario_p95.globalstralning_w_m2 is None
    assert stats.scenario_p95.antal_rh_matchade == 0


def test_felhantering_tomma_och_ogiltiga_filer(tmp_path: Path) -> None:
    with pytest.raises(SmhiVaderFel):
        las_smhi_csv_text("")
    with pytest.raises(SmhiVaderFel):
        las_smhi_csv_text("Rubrik1;Rubrik2\n1;2\n")
    with pytest.raises(SmhiVaderFel):
        las_smhi_json_text("{}")
    with pytest.raises(SmhiVaderFel):
        las_smhi_json_text(json.dumps({"value": []}))
    with pytest.raises(SmhiVaderFel):
        las_smhi_json_fil(tmp_path / "finns_inte.json")

    # Endast underkända rader (flagga O) -> serie med 0 godkända, analys ger tydligt SmhiVaderFel.
    bara_o = las_smhi_csv_text(
        "Datum;Tid (UTC);Lufttemperatur;Kvalitet\n2024-07-01;12:00:00;30.0;O\n"
    )
    assert bara_o.antal_godkanda == 0
    assert bara_o.bortfiltrerade == 1
    with pytest.raises(SmhiVaderFel):
        berakna_varmebolja_utci(bara_o)
