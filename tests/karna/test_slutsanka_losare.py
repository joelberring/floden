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


def _fraga(bas: Path, **extra: object) -> KorbarFraga:
    return fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "sluten",
            "scenarier": {"bas": {"bas": str(bas)}},
            "analyser": [
                {
                    "id": "skyfall",
                    "losare": "vatten.statisk_fyllning",
                    "parametrar": {"regn_mm": 50.0, **extra},
                }
            ],
        },
        REPO,
    )


def test_sluten_gard_ger_resultat_med_varning_och_flaggraster(tmp_path: Path) -> None:
    # Som A10: taken räknas inte (takvatten = false), djup = regnet på gården.
    res = kor_utredning(
        _fraga(A10, takvatten=False), ResultatCache(tmp_path / "cache"), tmp_path / "ut"
    )
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


def test_sluten_gard_med_takvatten_far_halva_taket(tmp_path: Path) -> None:
    # Standard (takvatten på): ringhuset ger gården hälften av takets regn (A12): 10,6 m³.
    res = kor_utredning(_fraga(A10), ResultatCache(tmp_path / "cache"), tmp_path / "ut")
    (jobb,) = res.sammanstallning["jobb"]
    m = jobb["matvarden"]
    assert m["slutsanka_lagrat_m3"] == pytest.approx(5.0 + 0.5 * 224.0 * 0.05, rel=1e-12)
    assert m["slutsanka_max_djup_m"] == pytest.approx(0.106, abs=1e-6)
    assert m["takvatten"] is True and m["takvatten_regler"] == {"ring_hälften_in_hälften_ut": 1}
    assert m["avtappning_mm_h"] == 0.0
    assert m["avtappning_kalla"] == "standard: 0 mm/h (ledningsnätet antas fullt)"
    assert m["volymbalans"]["regn_totalt_m3"] == pytest.approx((324.0 + 160.0) * 0.05)
    assert m["volymbalans"]["volymfel_rel"] < 1e-12


def test_vanligt_fall_far_inget_flaggraster_och_ingen_varning(tmp_path: Path) -> None:
    res = kor_utredning(_fraga(A1), ResultatCache(tmp_path / "cache"), tmp_path / "ut")
    (jobb,) = res.sammanstallning["jobb"]
    assert "antal_slutsankor" not in jobb["matvarden"]
    assert jobb["varningar"] == []
    assert not (tmp_path / "ut" / "skyfall" / "bas" / "slutsanka.tif").exists()
