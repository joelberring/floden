"""`vatten.statisk_fyllning` på en sluten gård (A10): resultat, varning och flaggraster (K2/v2)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from karna.korplan import kor_utredning
from karna.resultatcache import ResultatCache
from karna.utredning import KorbarFraga, fraga_fran_dict
from sankgraf.typer import SLUTSANKA_VARNING
from testsvit.k1_io import las_geotiff

REPO = Path(__file__).resolve().parents[2]
A10 = REPO / "testsvit" / "A10_sluten_gard" / "indata"
A1 = REPO / "testsvit" / "A1_enkel_sanka" / "indata"


def _fraga(bas: Path) -> KorbarFraga:
    return fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "sluten",
            "scenarier": {"bas": {"bas": str(bas)}},
            "analyser": [
                {
                    "id": "skyfall",
                    "losare": "vatten.statisk_fyllning",
                    "parametrar": {"regn_mm": 50.0},
                }
            ],
        },
        REPO,
    )


def test_sluten_gard_ger_resultat_med_varning_och_flaggraster(tmp_path: Path) -> None:
    res = kor_utredning(_fraga(A10), ResultatCache(tmp_path / "cache"), tmp_path / "ut")
    (jobb,) = res.sammanstallning["jobb"]
    m = jobb["matvarden"]
    assert m["antal_slutsankor"] == 1
    assert m["slutsanka_area_m2"] == pytest.approx(100.0)
    assert m["slutsanka_lagrat_m3"] == pytest.approx(5.0, rel=1e-9)  # 50 mm på 100 m²
    assert m["slutsanka_max_djup_m"] == pytest.approx(0.05, abs=1e-6)
    assert m["slutsanka_over_hinder_m3"] == 0.0
    assert SLUTSANKA_VARNING in jobb["varningar"]
    flagga, _ = las_geotiff(tmp_path / "ut" / "skyfall" / "bas" / "slutsanka.tif")
    assert int(np.sum(flagga == 1.0)) == 1600


def test_vanligt_fall_far_inget_flaggraster_och_ingen_varning(tmp_path: Path) -> None:
    res = kor_utredning(_fraga(A1), ResultatCache(tmp_path / "cache"), tmp_path / "ut")
    (jobb,) = res.sammanstallning["jobb"]
    assert "antal_slutsankor" not in jobb["matvarden"]
    assert jobb["varningar"] == []
    assert not (tmp_path / "ut" / "skyfall" / "bas" / "slutsanka.tif").exists()
