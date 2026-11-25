"""Tester för automatisk 3D-kantstens- och 1D-rännstenskoppling i `vatten.losare_2d`."""

from __future__ import annotations

import json
from pathlib import Path

from sankgraf.typer import las_brytlinjer_fran_gpkg
from vatten.blaytor import k1_filer
from vatten.losare_2d import _kor_2d


def test_las_brytlinjer_3d_och_losare_2d_auto_rannsten(tmp_path: Path) -> None:
    """Verifierar att K1 3D-brytlinjer läses med Z och automatiskt exporterar rannsten_flode."""
    a5b_indata = Path("testsvit/A5b_sned_gata_3d/indata")
    bryt = las_brytlinjer_fran_gpkg(a5b_indata / "brytlinjer.gpkg")
    assert len(bryt) == 3
    assert bryt[0].z_kron_rh2000 is not None
    assert bryt[1].typ == "sankt_kantsten"
    assert bryt[0].kalla == "baskarta_3d"
    assert bryt[0].osakerhet_m == 0.02

    indata = k1_filer(a5b_indata)
    ut_auto = tmp_path / "ut_auto"
    mat = _kor_2d(
        indata,
        {
            "regn_tid_s": [0.0, 30.0],
            "regn_mm_h": [120.0, 120.0],
            "slut_tid_s": 40.0,
            "rannsten_1d": "auto",
        },
        ut_auto,
    )
    assert mat["rannsten_1d_aktiv"] is True
    assert mat["rannsten_1d_fil"] == "rannsten_flode.geojson"
    assert mat["rannsten_1d_volymfel_rel"] < 1e-3
    gj_fil = ut_auto / "rannsten_flode.geojson"
    assert gj_fil.is_file()
    gj = json.loads(gj_fil.read_text(encoding="utf-8"))
    assert len(gj["features"]) == mat["rannsten_1d_segment"]

    # Med rannsten_1d=False ska ingen 1D-rännstensfil skrivas
    ut_av = tmp_path / "ut_av"
    mat_av = _kor_2d(
        indata,
        {
            "regn_tid_s": [0.0, 30.0],
            "regn_mm_h": [120.0, 120.0],
            "slut_tid_s": 40.0,
            "rannsten_1d": False,
        },
        ut_av,
    )
    assert mat_av["rannsten_1d_aktiv"] is False
    assert not (ut_av / "rannsten_flode.geojson").exists()
