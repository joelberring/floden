"""Takhöjd ur källan under eller nära marken (z = 0 i en baskarta) ignoreras med varning."""

import numpy as np

from markmodell.byggnadsregister import ByggnadsKandidat, hojdsatt_byggnader
from markmodell.rutnat import Ruta


def _kand(tak: float) -> ByggnadsKandidat:
    return ByggnadsKandidat(
        polygon=((20.0, 20.0), (40.0, 20.0), (40.0, 40.0), (20.0, 40.0)),
        kalla="test",
        tak_rh2000=tak,
    )


def test_tak_under_mark_ignoreras_och_ger_standardhojd() -> None:
    ruta = Ruta(0.0, 0.0, 60.0, 0.0, 1.0)
    z = np.full(ruta.form_rc, 7.0, dtype=np.float32)
    poster, varn = hojdsatt_byggnader([_kand(0.0)], z, ruta)
    (p,) = poster
    assert p.hojdkalla == "standard" and p.hojd_m > 0
    assert any("ignorerad" in v for v in varn)
    poster, varn = hojdsatt_byggnader([_kand(19.0)], z, ruta)
    assert poster[0].hojdkalla == "kalla_tak" and abs(poster[0].hojd_m - 12.0) < 1e-6
