"""Instängda områden: ett markområde som byggnader helt omger saknar väg ut (K2-kontraktet)."""

from __future__ import annotations

import numpy as np
import pytest
from numpy.typing import NDArray

from sankgraf.hierarki import bygg_sankgraf, hitta_instangda_omraden
from sankgraf.typer import MARKSLAG_BYGGNAD, InstangtOmradeFel, SankGrafFel
from testsvit.k1_io import GeoTransformInfo

N = 20


def _varld(oppning: bool) -> tuple[NDArray[np.float32], NDArray[np.uint8], GeoTransformInfo]:
    """Svagt lutande mark med en 6x6-gård (sänka) omgiven av byggnader (rad/kolumn 5..12)."""
    jj = np.arange(N, dtype=np.float32)[None, :].repeat(N, axis=0)
    z = (10.0 - 0.01 * jj).astype(np.float32)
    z[7:11, 7:11] -= 0.5  # gårdens sänka
    slag = np.zeros((N, N), dtype=np.uint8)
    slag[5:13, 5:13] = MARKSLAG_BYGGNAD
    slag[6:12, 6:12] = 0  # gården: 6x6 celler
    if oppning:
        slag[5, 8] = 0  # lucka i norra huset: gatan förblir öppen
    geo = GeoTransformInfo(
        bredd=N, hojd=N, x_min=0.0, y_max=float(N), pixelstorlek_x=1.0, pixelstorlek_y=1.0,
        epsg=3011, dtype="float32",
    )
    return z, slag, geo


def test_hitta_instangda_omraden_ger_storlek_och_cell() -> None:
    aktiv = np.ones((10, 10), dtype=bool)
    aktiv[2:7, 2:7] = False
    aktiv[3:6, 3:6] = True  # 3x3 = 9 instängda celler innanför en ring av falska celler
    assert hitta_instangda_omraden(aktiv) == [(9, 3, 3)]
    aktiv[4, 2] = True  # lucka i ringen (4-grannar): inte längre instängt
    aktiv[4, 3] = True
    assert hitta_instangda_omraden(aktiv) == []


def test_helt_omsluten_garden_ger_tydligt_specifikt_fel() -> None:
    z, slag, geo = _varld(oppning=False)
    osak = np.full((N, N), 0.05, dtype=np.float32)
    with pytest.raises(InstangtOmradeFel) as ex:
        bygg_sankgraf(z, osak, slag, geo)
    assert ex.value.antal_celler == 36  # 6x6 gård
    assert isinstance(ex.value, SankGrafFel)  # äldre kod som fångar SankGrafFel fungerar som förut
    assert "saknar utloppssadel" in str(ex.value)


def test_lucka_i_ringen_ger_en_giltig_graf_med_garden_som_sanka() -> None:
    z, slag, geo = _varld(oppning=True)
    osak = np.full((N, N), 0.05, dtype=np.float32)
    graf = bygg_sankgraf(z, osak, slag, geo)
    # Gårdens enda utlopp är luckan på rad 5, kolumn 8 (z = 10,00 - 0,01 * 8 = 9,92 m).
    garden = [n for n in graf.noder.values() if n.nod_id >= 0]
    assert len(garden) == 1
    assert garden[0].overfall_rh2000 == pytest.approx(9.92, abs=1e-3)
