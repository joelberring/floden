"""Enhetstester för permanent vattenyta (`vatten_mask`, `markslag == 8`) i `rapport.figurer`."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from rapport.figurer import (
    FARG_BYGGNAD,
    FARG_BYGGNAD_KANT,
    FARG_KARTYTA,
    FARG_VATTEN,
    FARG_VATTEN_KANT,
    FIGURSTILAR,
    FigurFel,
    hamta_figurstil,
    klassificera_raster,
    rendera_kartfigur,
    spara_kartfigur,
)
from testsvit.k1_io import GeoTransformInfo


def _geo(hojd: int = 40, bredd: int = 50, cell_m: float = 2.0) -> GeoTransformInfo:
    return GeoTransformInfo(
        bredd=bredd,
        hojd=hojd,
        x_min=-210000.0,
        y_max=6414000.0,
        pixelstorlek_x=cell_m,
        pixelstorlek_y=cell_m,
        epsg=3011,
        dtype="float32",
    )


def test_vatten_mask_ger_lugn_vattenfarg_istallet_for_djupklasser() -> None:
    """Permanent vatten (`markslag == 8`) får `FARG_VATTEN`, inte skyfallsklassens gröna ränder."""
    for stilnamn, varde in (("skyfall_djup", 0.25), ("skyfall_skillnad", 0.15)):
        stil = hamta_figurstil(stilnamn)
        klassfarger = {k.farg for k in stil.klasser} | {
            stil.tomvarde_farg,
            FARG_KARTYTA,
            FARG_BYGGNAD,
        }
        assert FARG_VATTEN not in klassfarger

        raster = np.full((8, 8), varde, dtype=np.float64)
        vatten = np.zeros((8, 8), dtype=bool)
        vatten[2:6, 1:5] = True

        rgb = klassificera_raster(raster, stil, vatten_mask=vatten)
        assert np.all(rgb[vatten] == np.array(FARG_VATTEN, dtype=np.uint8))
        # Landceller utanför vattenmasken klassificeras fortfarande enligt stilens skala.
        assert tuple(rgb[0, 0]) != FARG_VATTEN
        assert tuple(rgb[0, 0]) in {k.farg for k in stil.klasser}


def test_byggnad_mask_gar_fore_vatten_mask_och_fel_form_ger_figurfel() -> None:
    stil = FIGURSTILAR["skyfall_djup"]
    raster = np.full((6, 6), 0.40, dtype=np.float64)
    vatten = np.ones((6, 6), dtype=bool)
    bygg = np.zeros((6, 6), dtype=bool)
    bygg[1:5, 1:5] = True

    rgb = klassificera_raster(raster, stil, byggnad_mask=bygg, vatten_mask=vatten)
    assert tuple(rgb[0, 0]) == FARG_VATTEN
    assert tuple(rgb[2, 2]) == FARG_BYGGNAD
    assert tuple(rgb[1, 1]) == FARG_BYGGNAD_KANT

    with pytest.raises(FigurFel, match="vatten_mask"):
        klassificera_raster(raster, stil, vatten_mask=np.zeros((3, 3), dtype=bool))


def test_vatten_mask_none_och_tom_mask_bevarar_befintligt_beteende_bitidentiskt(
    tmp_path: Path,
) -> None:
    geo = _geo(30, 40, cell_m=2.0)
    raster = np.linspace(0.0, 0.7, 30 * 40, dtype=np.float64).reshape((30, 40))

    rgb_default = klassificera_raster(raster, FIGURSTILAR["skyfall_djup"])
    rgb_none = klassificera_raster(raster, FIGURSTILAR["skyfall_djup"], vatten_mask=None)
    rgb_tom = klassificera_raster(
        raster, FIGURSTILAR["skyfall_djup"], vatten_mask=np.zeros((30, 40), dtype=bool)
    )
    np.testing.assert_array_equal(rgb_default, rgb_none)
    np.testing.assert_array_equal(rgb_default, rgb_tom)

    p1 = spara_kartfigur(
        raster,
        "skyfall_djup",
        tmp_path / "utan.png",
        titel="Skyfall utan vatten",
        geo=geo,
    )
    p2 = spara_kartfigur(
        raster,
        "skyfall_djup",
        tmp_path / "tom_mask.png",
        titel="Skyfall utan vatten",
        geo=geo,
        vatten_mask=np.zeros((30, 40), dtype=bool),
    )
    h1 = hashlib.sha256(p1.read_bytes()).hexdigest()
    h2 = hashlib.sha256(p2.read_bytes()).hexdigest()
    assert h1 == h2


@pytest.mark.parametrize("placering", ("sida", "botten"))
def test_rendera_kartfigur_visar_vattenyta_i_karta_och_teckenforklaring(
    placering: str, tmp_path: Path
) -> None:
    geo = _geo(40, 50, cell_m=2.0)
    raster = np.full((40, 50), 0.22, dtype=np.float64)
    vatten = np.zeros((40, 50), dtype=bool)
    vatten[8:20, 10:35] = True

    img_utan = rendera_kartfigur(
        raster,
        "skyfall_djup",
        titel="Skyfall med kanal",
        geo=geo,
        karta_max_px=360,
        legend_placering=placering,  # type: ignore[arg-type]
    )
    img_med = rendera_kartfigur(
        raster,
        "skyfall_djup",
        titel="Skyfall med kanal",
        geo=geo,
        vatten_mask=vatten,
        karta_max_px=360,
        legend_placering=placering,  # type: ignore[arg-type]
    )

    arr_utan = np.asarray(img_utan)
    arr_med = np.asarray(img_med)
    vatten_rgb = np.array(FARG_VATTEN, dtype=np.uint8)
    kant_rgb = np.array(FARG_VATTEN_KANT, dtype=np.uint8)

    assert int(np.all(arr_utan == vatten_rgb, axis=-1).sum()) == 0
    assert int(np.all(arr_med == vatten_rgb, axis=-1).sum()) > 100
    # Teckenförklaringens swatch för "Vattenyta" har både FARG_VATTEN och ramen FARG_VATTEN_KANT.
    assert int(np.all(arr_med == kant_rgb, axis=-1).sum()) > 20

    # Kontrollera också att swatch-färgen syns i själva legenddelen (höger eller botten).
    legend_utsnitt = arr_med[:, 400:, :] if placering == "sida" else arr_med[360:, :, :]
    assert int(np.all(legend_utsnitt == vatten_rgb, axis=-1).sum()) > 100
    assert int(np.all(legend_utsnitt == kant_rgb, axis=-1).sum()) > 20

    ut_png = spara_kartfigur(
        raster,
        "skyfall_djup",
        tmp_path / f"vatten_{placering}.png",
        titel="Skyfall med kanal",
        geo=geo,
        vatten_mask=vatten,
        karta_max_px=360,
        legend_placering=placering,  # type: ignore[arg-type]
    )
    assert ut_png.is_file() and ut_png.stat().st_size > 2000
