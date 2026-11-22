"""Graf mot 2D i en sluten gård (slutsänka, K2/v2).

2D-lösaren (lokal tröghet, Python och Rust) behandlar byggnader som ogenomträngliga väggar och
löser en sluten gård utan särfall: vattnet samlas. Sänkgrafen (statisk fyllning) ska ge samma
volym i gården och samma vattenyta, med 2D-lösarens kvarvarande film (`min_djup_m`) som enda
skillnad. Uppmätt (2026-11-22): volym 3,6 m³ i båda, största skillnad i djup 0,14 mm.
"""

from __future__ import annotations

import numpy as np
import pytest
from numpy.typing import NDArray

from sankgraf.fyllning import statisk_fyllning
from sankgraf.hierarki import bygg_sankgraf
from tests.sankgraf.test_instangt_omrade import _varld
from testsvit.k1_io import GeoTransformInfo
from tvad.lokal_troghet import TvadFel, kor_2d_lokal_troghet


def _fall() -> tuple[
    NDArray[np.float32], NDArray[np.uint8], GeoTransformInfo, NDArray[np.bool_], NDArray[np.float64]
]:
    z, slag, geo = _varld(oppning=False)
    graf = bygg_sankgraf(z, np.full(z.shape, 0.05, dtype=np.float32), slag, geo)
    mask = graf.slutsanka_mask()
    djup_graf = np.asarray(statisk_fyllning(graf, 0.10).djup_m, dtype=np.float64)
    return z, slag, geo, mask, djup_graf


def test_graf_mot_2d_i_sluten_gard_med_sluttande_golv() -> None:
    z, slag, geo, mask, djup_graf = _fall()
    # 100 mm på 10 min (600 mm/h), sedan 20 min utan regn så att vattnet hinner lägga sig.
    res = kor_2d_lokal_troghet(z, slag, geo, [0.0, 600.0], [600.0, 600.0], slut_tid_s=1800.0)
    d2 = np.asarray(res.slut_djup_m, dtype=np.float64)
    assert res.volymfel_rel < 1e-12
    # Inget läcker genom byggnaderna: gårdens 36 m² × 0,1 m ligger kvar.
    assert float(d2[mask].sum()) == pytest.approx(3.6, rel=1e-6)
    assert float(djup_graf[mask].sum()) == pytest.approx(3.6, rel=1e-6)
    skillnad = np.abs(d2[mask] - djup_graf[mask])
    assert float(skillnad.max()) < 1e-3  # 2D:s film på sluttningen (min_djup_m = 0,1 mm)
    assert float(np.max(d2[mask])) == pytest.approx(float(np.max(djup_graf[mask])), abs=1e-3)


def test_rust_karnan_ger_samma_som_python_i_sluten_gard() -> None:
    from tvad.rust_backend import hitta_tvad_karna, kor_2d_lokal_troghet_rust

    try:
        hitta_tvad_karna()
    except TvadFel:
        pytest.skip("rust/tvad_karna är inte byggd")
    z, slag, geo, _, _ = _fall()
    py = kor_2d_lokal_troghet(z, slag, geo, [0.0, 600.0], [600.0, 600.0], slut_tid_s=1200.0)
    rs = kor_2d_lokal_troghet_rust(z, slag, geo, [0.0, 600.0], [600.0, 600.0], slut_tid_s=1200.0)
    assert float(np.max(np.abs(rs.slut_djup_m - py.slut_djup_m))) == 0.0
    assert float(np.max(np.abs(rs.max_djup_m - py.max_djup_m))) == 0.0
