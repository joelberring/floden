"""Enhetstester för Flodens enhetliga rapportfigur-motor (rapport.figurer + kartor)."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from rapport.figurer import (
    FARG_BAKGRUND,
    FARG_BYGGNAD,
    FARG_BYGGNAD_KANT,
    FARG_POLYGON,
    FIGURSTILAR,
    FigurFel,
    _berakna_skalstock_m,
    figurstil_for_lager,
    hamta_figurstil,
    klassificera_raster,
    rendera_kartfigur,
    spara_kartfigur,
)
from rapport.kartor import RapportKartaFel, rendera_diffkarta_png, rendera_djupkarta_png
from testsvit.k1_io import GeoTransformInfo

ALLA_STILNAMN = (
    "skyfall_djup",
    "skyfall_ankomsttid",
    "skyfall_fara",
    "skyfall_skillnad",
    "dagsljus_soltimmar",
    "dagsljus_svf",
    "ekologi_stromtathet",
    "luft_vindkomfort",
    "buller_dba",
)


def _geo(hojd: int = 80, bredd: int = 100, cell_m: float = 2.0) -> GeoTransformInfo:
    return GeoTransformInfo(
        bredd=bredd,
        hojd=hojd,
        x_min=152000.0,
        y_max=6578000.0,
        pixelstorlek_x=cell_m,
        pixelstorlek_y=cell_m,
        epsg=3011,
        dtype="float32",
    )


def _projektpolygon(geo: GeoTransformInfo) -> list[tuple[float, float]]:
    w = geo.bredd * geo.pixelstorlek_x
    h = geo.hojd * geo.pixelstorlek_y
    return [
        (geo.x_min + 0.20 * w, geo.y_max - 0.20 * h),
        (geo.x_min + 0.80 * w, geo.y_max - 0.22 * h),
        (geo.x_min + 0.78 * w, geo.y_max - 0.78 * h),
        (geo.x_min + 0.22 * w, geo.y_max - 0.80 * h),
    ]


def test_alla_nio_domanstilar_finns_och_kan_renderas(tmp_path: Path) -> None:
    geo = _geo(40, 50, cell_m=2.0)
    poly = _projektpolygon(geo)
    bygg = np.zeros((40, 50), dtype=bool)
    bygg[10:18, 12:22] = True

    for namn in ALLA_STILNAMN:
        assert namn in FIGURSTILAR
        stil = hamta_figurstil(namn)
        assert stil.klasser
        assert stil.enhet

        if namn == "skyfall_skillnad":
            raster = np.linspace(-0.4, 0.4, 40 * 50, dtype=np.float64).reshape((40, 50))
        elif namn == "buller_dba":
            raster = np.linspace(42.0, 72.0, 40 * 50, dtype=np.float64).reshape((40, 50))
        elif namn == "dagsljus_svf":
            raster = np.linspace(0.0, 1.0, 40 * 50, dtype=np.float64).reshape((40, 50))
        elif namn == "dagsljus_soltimmar":
            raster = np.linspace(0.0, 10.0, 40 * 50, dtype=np.float64).reshape((40, 50))
        elif namn == "skyfall_ankomsttid":
            raster = np.linspace(0.0, 3600.0, 40 * 50, dtype=np.float64).reshape((40, 50))
        elif namn == "luft_vindkomfort":
            raster = (np.arange(40 * 50, dtype=np.float64) % 5).reshape((40, 50))
        else:
            raster = np.linspace(0.0, 1.2, 40 * 50, dtype=np.float64).reshape((40, 50))

        ut = spara_kartfigur(
            raster,
            namn,
            tmp_path / f"{namn}.png",
            titel=f"Testfigur {namn}",
            undertitel="Scenario: läget idag · 100-årsregn",
            geo=geo,
            byggnad_mask=bygg,
            projektpolygon_3011=poly,
            run_hash="abcdef1234567890",
            kalla="©Lantmäteriet / Floden",
        )
        assert ut.is_file()
        with Image.open(ut) as img:
            assert img.format == "PNG"
            assert img.mode == "RGB"
            assert img.width >= 600
            assert img.height >= 450
            # Varm linnebakgrund i yttre hörn
            assert img.getpixel((2, 2)) == FARG_BAKGRUND


def test_klassificering_av_kanda_varden_och_byggnadsmask() -> None:
    stil = hamta_figurstil("skyfall_djup")
    raster = np.array(
        [
            [np.nan, 0.02, 0.10],
            [0.20, 0.40, 0.80],
        ],
        dtype=np.float64,
    )
    rgb = klassificera_raster(raster, stil)
    assert tuple(rgb[0, 0]) == stil.tomvarde_farg
    assert tuple(rgb[0, 1]) == stil.tomvarde_farg
    assert tuple(rgb[0, 2]) == stil.klasser[0].farg
    assert tuple(rgb[1, 0]) == stil.klasser[1].farg
    assert tuple(rgb[1, 1]) == stil.klasser[2].farg
    assert tuple(rgb[1, 2]) == stil.klasser[3].farg

    # Byggnadsmask ger byggnadsfärg i kärnan och konturfärg på ytterkanten
    bygg = np.zeros((5, 5), dtype=bool)
    bygg[1:4, 1:4] = True
    rgb_bygg = klassificera_raster(np.zeros((5, 5), dtype=np.float64), stil, byggnad_mask=bygg)
    assert tuple(rgb_bygg[2, 2]) == FARG_BYGGNAD
    assert tuple(rgb_bygg[1, 1]) == FARG_BYGGNAD_KANT


def test_projektpolygon_syns_pa_kartan_och_bottenlegend_fungerar() -> None:
    geo = _geo(60, 60, cell_m=2.0)
    raster = np.full((60, 60), 0.20, dtype=np.float64)
    poly = _projektpolygon(geo)

    img = rendera_kartfigur(
        raster,
        "skyfall_djup",
        titel="Skyfall med projektpolygon",
        undertitel="Förslag A",
        geo=geo,
        projektpolygon_3011=poly,
        legend_placering="botten",
        anmarkning="Statisk fyllning utan infiltration.",
    )
    arr = np.asarray(img)
    # Projektpolygonens skogsgröna konturfärg (#2F4A2C) måste finnas i bilden
    match = np.all(arr == np.array(FARG_POLYGON, dtype=np.uint8), axis=-1)
    assert int(match.sum()) > 100


def test_automatisk_skalstock_ger_avrundade_meter() -> None:
    for cell_m, bredd in ((1.0, 200), (2.0, 150), (5.0, 120), (0.5, 100)):
        geo = _geo(80, bredd, cell_m=cell_m)
        langd_m, px = _berakna_skalstock_m(geo, vis_w=640)
        assert langd_m in (2, 4, 5, 10, 20, 25, 40, 50, 100, 200, 250, 400, 500, 1000, 2000, 5000)
        assert 24 <= px <= 220


def test_deterministisk_byte_identisk_png_och_snabb_rendering(tmp_path: Path) -> None:
    geo = _geo(80, 100, cell_m=2.0)
    raster = np.linspace(0.0, 0.9, 80 * 100, dtype=np.float64).reshape((80, 100))
    poly = _projektpolygon(geo)

    # Uppvärmning av typsnittscache
    f1 = spara_kartfigur(
        raster,
        "skyfall_djup",
        tmp_path / "a.png",
        titel="Determinismtest",
        undertitel="100-årsregn",
        geo=geo,
        projektpolygon_3011=poly,
        run_hash="deadbeef1234",
    )
    t0 = time.perf_counter()
    f2 = spara_kartfigur(
        raster,
        "skyfall_djup",
        tmp_path / "b.png",
        titel="Determinismtest",
        undertitel="100-årsregn",
        geo=geo,
        projektpolygon_3011=poly,
        run_hash="deadbeef1234",
    )
    tid_ms = (time.perf_counter() - t0) * 1000.0

    h1 = hashlib.sha256(f1.read_bytes()).hexdigest()
    h2 = hashlib.sha256(f2.read_bytes()).hexdigest()
    assert h1 == h2
    assert tid_ms < 250.0  # Normalt ~20-50 ms, generös marginal för CI


def test_valideringsfel_ger_tydliga_undantag() -> None:
    with pytest.raises(FigurFel, match="Okänd figurstil"):
        hamta_figurstil("finns_inte")

    with pytest.raises(FigurFel, match="2D"):
        rendera_kartfigur(np.zeros((4,), dtype=np.float64), "skyfall_djup", titel="Fel")

    with pytest.raises(FigurFel, match="geo"):
        rendera_kartfigur(
            np.zeros((10, 10), dtype=np.float64),
            "skyfall_djup",
            titel="Fel",
            projektpolygon_3011=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)],
        )

    geo = _geo(10, 10)
    with pytest.raises(FigurFel, match="matchar inte"):
        rendera_kartfigur(
            np.zeros((8, 10), dtype=np.float64),
            "skyfall_djup",
            titel="Fel",
            geo=geo,
        )

    with pytest.raises(FigurFel, match="byggnad_mask"):
        rendera_kartfigur(
            np.zeros((10, 10), dtype=np.float64),
            "skyfall_djup",
            titel="Fel",
            byggnad_mask=np.zeros((5, 5), dtype=bool),
        )


def test_kartor_inkoppling_med_geo_och_polygon(tmp_path: Path) -> None:
    geo = _geo(30, 40, cell_m=2.0)
    poly = _projektpolygon(geo)
    djup = np.linspace(0.0, 0.8, 30 * 40, dtype=np.float64).reshape((30, 40))
    diff = np.linspace(-0.3, 0.3, 30 * 40, dtype=np.float64).reshape((30, 40))

    f_djup = rendera_djupkarta_png(
        djup,
        tmp_path / "djup.png",
        titel="Maximalt vattendjup",
        geo=geo,
        projektpolygon_3011=poly,
        run_hash="1234567890ab",
    )
    f_diff = rendera_diffkarta_png(
        diff,
        tmp_path / "diff.png",
        titel="Skillnad före/efter",
        max_abs_m=0.4,
        geo=geo,
        projektpolygon_3011=poly,
    )
    assert f_djup.is_file() and f_diff.is_file()

    with pytest.raises(RapportKartaFel):
        rendera_diffkarta_png(diff, tmp_path / "fel.png", titel="Fel", max_abs_m=0.0)


def test_dagsljus_svf_klassificerar_verklig_himmelsandel_0_till_1() -> None:
    """B12: svf.tif är himmelsandel 0,0–1,0 (ej 0–100); 0.2, 0.5, 0.85 ska få distinkta färger."""
    stil = hamta_figurstil("dagsljus_svf")
    assert "0–1" in stil.enhet or "andel" in stil.enhet
    assert stil.enhet != "%"
    assert max(k.min_varde or 0.0 for k in stil.klasser) <= 1.0

    raster = np.array([[np.nan, 0.10, 0.20], [0.50, 0.70, 0.85]], dtype=np.float64)
    rgb = klassificera_raster(raster, stil)

    assert tuple(rgb[0, 0]) == stil.tomvarde_farg
    assert tuple(rgb[0, 1]) == stil.klasser[0].farg
    assert tuple(rgb[0, 2]) == stil.klasser[1].farg
    assert tuple(rgb[1, 0]) == stil.klasser[2].farg
    assert tuple(rgb[1, 1]) == stil.klasser[3].farg
    assert tuple(rgb[1, 2]) == stil.klasser[4].farg
    # Kontrollera att 0.2, 0.5 och 0.85 verkligen hamnar i tre helt skilda klasser
    farger_urval = {tuple(rgb[0, 2]), tuple(rgb[1, 0]), tuple(rgb[1, 2])}
    assert len(farger_urval) == 3


def test_skyfall_ankomsttid_klassificerar_verkliga_sekunder() -> None:
    """B12: tid_till_0_1m.tif / ankomsttid.tif lagrar sekunder s (ej 0–40 min)."""
    stil = hamta_figurstil("skyfall_ankomsttid")
    assert stil.enhet == "s"
    assert all("s" in k.etikett for k in stil.klasser)

    raster = np.array([[np.nan, 120.0, 600.0], [1800.0, 3600.0, 0.0]], dtype=np.float64)
    rgb = klassificera_raster(raster, stil)

    assert tuple(rgb[0, 0]) == stil.tomvarde_farg
    assert tuple(rgb[0, 1]) == stil.klasser[0].farg  # 120 s (0–600 s)
    assert tuple(rgb[0, 2]) == stil.klasser[1].farg  # 600 s (600–1200 s)
    assert tuple(rgb[1, 0]) == stil.klasser[2].farg  # 1800 s (1200–2400 s)
    assert tuple(rgb[1, 1]) == stil.klasser[3].farg  # 3600 s (>= 2400 s)
    assert tuple(rgb[1, 2]) == stil.klasser[0].farg  # 0 s
    # Alla fyra exempelvärden 120 s, 600 s, 1800 s, 3600 s ska ha unika färger
    farger_urval = {tuple(rgb[0, 1]), tuple(rgb[0, 2]), tuple(rgb[1, 0]), tuple(rgb[1, 1])}
    assert len(farger_urval) == 4


def test_luft_vindkomfort_klassificerar_nen8100_klasser_utan_ms() -> None:
    """B12: komfortklass.tif är NEN 8100-klassindex 0..4 (A–E) från luft.vindkomfort, ej m/s."""
    from luft.vindkomfort import KRITERIER, _klassa_komfort

    stil = hamta_figurstil("luft_vindkomfort")
    assert stil.enhet == "klass"
    assert "m/s" not in stil.enhet
    assert "m/s" not in stil.storhet
    assert all("m/s" not in k.etikett for k in stil.klasser)

    # Använd Flodens faktiska NEN 8100-klassificerare för att verifiera exakt 1-till-1-matchning
    andel = np.array([[0.01, 0.03, 0.07], [0.15, 0.30, np.nan]], dtype=np.float64)
    komfort_raster = _klassa_komfort(KRITERIER["nen8100"], {5.0: andel})
    np.testing.assert_array_equal(komfort_raster[0], [0.0, 1.0, 2.0])
    np.testing.assert_array_equal(komfort_raster[1, :2], [3.0, 4.0])
    assert np.isnan(komfort_raster[1, 2])

    rgb = klassificera_raster(komfort_raster, stil)
    assert tuple(rgb[0, 0]) == stil.klasser[0].farg  # A (0)
    assert tuple(rgb[0, 1]) == stil.klasser[1].farg  # B (1)
    assert tuple(rgb[0, 2]) == stil.klasser[2].farg  # C (2)
    assert tuple(rgb[1, 0]) == stil.klasser[3].farg  # D (3)
    assert tuple(rgb[1, 1]) == stil.klasser[4].farg  # E (4)
    assert tuple(rgb[1, 2]) == stil.tomvarde_farg  # NaN (byggnad)
    alla_klassfarger = {
        tuple(rgb[0, 0]),
        tuple(rgb[0, 1]),
        tuple(rgb[0, 2]),
        tuple(rgb[1, 0]),
        tuple(rgb[1, 1]),
    }
    assert len(alla_klassfarger) == 5


def test_figurstil_for_lager_matchar_domaner_och_saknar_hardskriven_djuptext() -> None:
    """B12: figurstil_for_lager mappar kända roller + skillnad utan fel enhet/djuptext."""
    assert figurstil_for_lager({"roll": "max_djup", "typ": "bas"}).namn == "skyfall_djup"
    assert figurstil_for_lager({"roll": "svf", "typ": "bas"}).namn == "dagsljus_svf"
    assert figurstil_for_lager({"roll": "tid_till_0_1m", "typ": "bas"}).namn == "skyfall_ankomsttid"
    assert figurstil_for_lager({"roll": "ankomsttid", "typ": "bas"}).namn == "skyfall_ankomsttid"
    assert figurstil_for_lager({"roll": "komfortklass", "typ": "bas"}).namn == "luft_vindkomfort"
    assert figurstil_for_lager({"roll": "soltimmar", "typ": "bas"}).namn == "dagsljus_soltimmar"
    assert figurstil_for_lager({"roll": "stromtathet", "typ": "bas"}).namn == "ekologi_stromtathet"

    sol_diff = figurstil_for_lager(
        {
            "roll": "soltimmar",
            "doman": "dagsljus",
            "etikett": "Soltimmar på marken",
            "enhet": "h",
            "typ": "skillnad",
            "farg": "skillnad",
            "skala": {"min": 0.0, "max": 4.0},
            "teckenforklaring": {"typ": "divergerande", "max": 4.0, "troskel": 0.25},
        }
    )
    assert sol_diff.enhet == "h"
    assert "0,02 m" not in sol_diff.tomvarde_etikett
    assert all("0,02 m" not in k.etikett for k in sol_diff.klasser)
    assert any("h" in k.etikett for k in sol_diff.klasser)
