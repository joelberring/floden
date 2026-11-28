# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: E501
"""Deterministiska nätverksfria tester för Göteborgs öppna luft-CSV och trafikmängdskatalog."""

from __future__ import annotations

from pathlib import Path

import pytest

from data_inlasning.goteborg import (
    GOTEBORG_LUFT_CSV_URL,
    GoteborgDataFel,
    hamta_goteborg_luft_csv,
)
from data_inlasning.goteborg_trafik import (
    GoteborgTrafikFel,
    bygg_goteborg_gata_url,
    hamta_goteborg_gata_trafikmangder,
    kontrollera_trafikmangd_url,
    senaste_per_delstracka,
    skriv_kommunal_trafik_csv,
    tolka_goteborg_trafikmangd_html,
)
from data_inlasning.smhi_luft_strang import (
    SmhiLuftStrangFel,
    hamta_goteborg_luft_bakgrund,
    las_goteborg_luft_csv_text,
)
from data_inlasning.trafikverket import TrafikLank, las_kommunal_trafik_fil

_SYN_GBG_LUFT_CSV = """\
Date,Time,Femman_Temp,Femman_NO2,Femman_O3,Femman_PM10,Femman_PM25,HagaNorra_NO2
2024-06-01,01:00+01:00,15.0,10.0,55.0,12.0,6.0,22.0
2025-01-01,01:00+01:00,2.0,14.0,42.0,18.0,9.0,28.0
2025-01-01,02:00+01:00,2.1,16.0,46.0,22.0,11.0,32.0
2025-01-02,01:00+01:00,1.5,-0.02,50.0,30.0,13.0,
2025-01-02,02:00+01:00,1.6,20.0,,10.0,7.0,24.0
"""

_SYN_GBG_TRAFIK_HTML = """\
<!DOCTYPE html>
<html><body><table>
<tr><th rowspan="2">Delsträcka</th><th rowspan="2">År</th><th colspan="4">ÅMVD (bilar/dygn)</th><th colspan="3">MAXTIM EM</th><th colspan="3">HASTIGHET</th></tr>
<tr><td>Totalt</td><td></td><td>Tung</td><td>%</td><td>Mot C</td><td>Från C</td><td>Summa</td><td>Skyltad</td><td>Median</td><td>85%</td></tr>
<tr class="delstracka"><th>Wavrinskys Plats &#8211; Doktor Fries Torg</th><td>2018</td><td>2 500</td><td></td><td>190</td><td>8</td><td>110</td><td>140</td><td></td><td></td><td>48</td><td>50</td></tr>
<tr><th></th><td>2016</td><td>2 700</td><td></td><td>170</td><td>6</td><td>130</td><td>170</td><td></td><td>50</td><td>49</td><td>52</td></tr>
<tr class="delstracka"><th>Doktor Forselius Gata &#8211; Doktor Westrings Gata</th><td>2018</td><td>4 900</td><td>MC</td><td>500</td><td>10</td><td>200</td><td>180</td><td>380</td><td>50</td><td>37</td><td>39</td></tr>
</table></body></html>
"""

# Haga i EPSG:3011 (~2 km från Femman) och Stockholm (~400 km från Femman).
_KARNA_HAGA_3011 = (-210_030.0, 6_413_610.0, -209_630.0, 6_414_000.0)
_KARNA_STHLM_3011 = (153_000.0, 6_580_000.0, 153_500.0, 6_580_500.0)


def test_las_goteborg_luft_csv_filtrerar_ar_och_negativ_nolldrift() -> None:
    res = las_goteborg_luft_csv_text(_SYN_GBG_LUFT_CSV, ar=2025, avstand_km=1.4)
    assert res.tillganglig is True
    assert res.station_id == "GM5"
    assert res.station_namn == "Femman"
    assert res.station_typ == "urban_bakgrund"
    assert res.avstand_km == pytest.approx(1.4)
    assert res.period_start == "2025-01-01"
    assert res.period_slut == "2025-01-02"
    assert any("ovaliderad" in v.lower() for v in res.varningar)

    # NO2 2025: 14, 16, (-0.02 bortfiltrerad!), 20 -> medel 50/3 = 16.667
    no2 = res.amnen["NO2"]
    assert no2.antal_timmar == 3
    assert no2.bortfiltrerade == 1
    assert no2.arsmedel_ug_m3 == pytest.approx(16.667, abs=1e-3)

    # PM10 2025: 18, 22, 30, 10 -> medel 20.0
    pm10 = res.amnen["PM10"]
    assert pm10.antal_timmar == 4
    assert pm10.arsmedel_ug_m3 == pytest.approx(20.0)
    assert res.amnen["O3"].antal_timmar == 3
    assert res.amnen["PM2.5"].antal_timmar == 4


def test_hamta_goteborg_luft_bakgrund_haversine_cache_och_avstandssparr(tmp_path: Path) -> None:
    anrop: list[str] = []

    def _fejk_hamtare(url: str) -> bytes:
        anrop.append(url)
        return _SYN_GBG_LUFT_CSV.encode("utf-8")

    # 1. Stockholm ligger långt utanför 30 km -> ska aldrig hämta eller använda Göteborgs luft!
    sthlm = hamta_goteborg_luft_bakgrund(
        _KARNA_STHLM_3011, ar=2025, cache_mapp=tmp_path, hamtare=_fejk_hamtare
    )
    assert sthlm.tillganglig is False
    assert anrop == []

    # 2. Haga ligger ~1–2 km från Femman -> hämtar en gång och använder SHA-256-cache nästa gång.
    r1 = hamta_goteborg_luft_bakgrund(
        _KARNA_HAGA_3011, ar=2025, cache_mapp=tmp_path, hamtare=_fejk_hamtare
    )
    r2 = hamta_goteborg_luft_bakgrund(
        _KARNA_HAGA_3011, ar=2025, cache_mapp=tmp_path, hamtare=_fejk_hamtare
    )
    assert len(anrop) == 1
    assert anrop[0] == GOTEBORG_LUFT_CSV_URL
    assert r1.tillganglig is True
    assert r1.avstand_km is not None and 0.5 < r1.avstand_km < 4.0
    assert r1.sha256 == r2.sha256
    assert r1.amnen["NO2"].arsmedel_ug_m3 == r2.amnen["NO2"].arsmedel_ug_m3

    # 3. SSRF-skydd mot obehörig värd i hamta_goteborg_luft_csv.
    with pytest.raises(GoteborgDataFel, match="goteborg.se"):
        hamta_goteborg_luft_csv(url="https://example.com/luft.csv")

    # 4. Nätverksfel faller tillbaka graciöst utan påhittade värden.
    def _trasig(_url: str) -> bytes:
        raise OSError("timeout")

    fel_res = hamta_goteborg_luft_bakgrund(_KARNA_HAGA_3011, ar=2025, hamtare=_trasig)
    assert fel_res.tillganglig is False
    assert fel_res.amnen == {}
    assert fel_res.varningar

    with pytest.raises(SmhiLuftStrangFel):
        las_goteborg_luft_csv_text("")


def test_goteborg_trafikmangd_html_tolkning_csv_och_rundresa(tmp_path: Path) -> None:
    # SSRF-spärr
    with pytest.raises(GoteborgTrafikFel):
        kontrollera_trafikmangd_url("http://trafikmangder.stadsbyggnad.goteborg.se/1970-2018/D/x.html")
    with pytest.raises(GoteborgTrafikFel):
        kontrollera_trafikmangd_url("https://example.com/x.html")

    url = bygg_goteborg_gata_url("Sprängkullsgatan")
    assert url.endswith("/S/Spr%E4ngkullsgatan.html")

    matningar = tolka_goteborg_trafikmangd_html(
        _SYN_GBG_TRAFIK_HTML, gatunamn="Doktor Allards Gata", kalla_url=url
    )
    assert len(matningar) == 3
    senaste = senaste_per_delstracka(matningar)
    assert len(senaste) == 2

    m_dr = next(m for m in senaste if "Wavrinskys" in m.delstracka)
    assert m_dr.ar == 2018
    assert m_dr.amvd_totalt == pytest.approx(2500.0)
    assert m_dr.amvd_tung == pytest.approx(190.0)
    assert m_dr.andel_tung_pct == pytest.approx(8.0)
    assert m_dr.skyltad_km_h is None  # aldrig påhittad!
    assert m_dr.median_km_h == pytest.approx(48.0)
    assert m_dr.p85_km_h == pytest.approx(50.0)

    m_mc = next(m for m in senaste if "Forselius" in m.delstracka)
    assert m_mc.riktning == "MC"
    assert m_mc.skyltad_km_h == pytest.approx(50.0)

    # Cachad hämtning + export till CSV som går rakt in i trafikverket.las_kommunal_trafik_csv!
    anrop = 0

    def _fejk_html(_u: str) -> bytes:
        nonlocal anrop
        anrop += 1
        return _SYN_GBG_TRAFIK_HTML.encode("latin-1")

    h1, sha1 = hamta_goteborg_gata_trafikmangder(
        "Doktor Allards Gata", cache_mapp=tmp_path / "cache", hamtare=_fejk_html
    )
    h2, sha2 = hamta_goteborg_gata_trafikmangder(
        "Doktor Allards Gata", cache_mapp=tmp_path / "cache", hamtare=_fejk_html
    )
    assert anrop == 1
    assert sha1 == sha2
    assert len(h1) == len(h2) == 3

    csv_fil = skriv_kommunal_trafik_csv(h1, tmp_path / "gbg_amvd.csv")
    lank_dr = TrafikLank(
        lank_id="gbg_dr_allard_1",
        koordinater_3011=((-209_350.0, 6_412_350.0), (-209_250.0, 6_412_350.0)),
        gatunamn="Doktor Allards Gata",
        vagnummer=None,
        vaghallare="kommunal",
        hastighet_km_h=None,
        korfalt=None,
        adt_fordon_dygn=None,
        andel_tung_pct=None,
        matar=None,
        adt_kalla=None,
        osaker=False,
    )
    matchade = las_kommunal_trafik_fil(csv_fil, vagnat_lankar=[lank_dr])
    assert len(matchade) == 1
    assert matchade[0].adt_fordon_dygn in (2500.0, 4900.0)
    assert matchade[0].osaker is True
    assert matchade[0].adt_kalla is not None and "ÅMVD" in matchade[0].adt_kalla


def test_goteborg_referens_trafikmangder_csv_ar_giltig_och_innehaller_inga_pahitt() -> None:
    import csv

    ref_csv = Path(__file__).resolve().parents[2] / "data" / "referens" / "goteborg_trafikmangder_amvd.csv"
    assert ref_csv.is_file(), f"Saknar {ref_csv}"
    with ref_csv.open("r", encoding="utf-8", newline="") as fh:
        rader = list(csv.DictReader(fh))
    assert len(rader) >= 15
    assert all(r["matt_typ"] == "ÅMVD" and r["osaker"] == "true" for r in rader)
    assert all("ÅMVD" in r["adt_kalla"] for r in rader)
    gator = {r["gatunamn"] for r in rader}
    assert {"Sprängkullsgatan", "Nya Allén", "Lindholmsallén", "Doktor Allards Gata", "Guldhedsgatan"} <= gator

    # Rundresa in i las_kommunal_trafik_fil mot syntetiska väglänkar
    lankar = [
        TrafikLank(
            lank_id=f"lk_{i}",
            koordinater_3011=((-209_500.0 + i * 10, 6_413_000.0), (-209_450.0 + i * 10, 6_413_000.0)),
            gatunamn=namn,
            vagnummer=None,
            vaghallare="kommunal",
            hastighet_km_h=None,
            korfalt=None,
            adt_fordon_dygn=None,
            andel_tung_pct=None,
            matar=None,
            adt_kalla=None,
            osaker=False,
        )
        for i, namn in enumerate(("Sprängkullsgatan", "Lindholmsallén", "Doktor Allards Gata", "Guldhedsgatan"))
    ]
    berikade = las_kommunal_trafik_fil(ref_csv, vagnat_lankar=lankar)
    assert len(berikade) == 4
    assert all(lk.adt_fordon_dygn is not None and lk.adt_fordon_dygn > 0 for lk in berikade)
    assert all(lk.osaker is True for lk in berikade)
