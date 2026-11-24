"""Skarvkontrollen (R3): syntetiska markmodeller med känt svar."""

from __future__ import annotations

import numpy as np
import pytest
from numpy.typing import NDArray

from sankgraf.skarvkontroll import (
    SkarvKontrollFel,
    avstand_till_kant_m,
    kontrollera_skarvar,
)
from testsvit.k1_io import GeoTransformInfo

DX = 0.5
H = B = 160  # 80 x 80 m


def _geo() -> GeoTransformInfo:
    return GeoTransformInfo(
        bredd=B, hojd=H, x_min=0.0, y_max=H * DX, pixelstorlek_x=DX, pixelstorlek_y=DX,
        epsg=3011, dtype="float32",
    )


def _bas() -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Svag dalgång som rinner söderut (rad 0 = norr): 2 % fall, tvärfall 0,002 m/m²."""
    r, k = np.mgrid[0:H, 0:B]
    y = r * DX
    x = (k + 0.5) * DX
    z = 20.0 - 0.02 * y + 0.002 * (x - 40.0) ** 2
    return z, y, x


def _lapp() -> NDArray[np.bool_]:
    m = np.zeros((H, B), dtype=bool)
    m[60:100, 8:152] = True  # 20 m i fallriktningen, 72 m bred: spänner över dalgången
    return m


def _kor(efter: NDArray[np.float64], **kw: float):  # type: ignore[no-untyped-def]
    z0, _, _ = _bas()
    return kontrollera_skarvar(
        z0.astype(np.float32), efter.astype(np.float32),
        np.full((H, B), 4, np.uint8), np.full((H, B), 0.05, np.float32), _geo(), _lapp(), **kw,
    )


def test_bas_ger_inga_fynd() -> None:
    z0, _, _ = _bas()
    res = _kor(z0)
    assert res.fynd == ()
    assert res.varningar() == []


def test_hard_platea_stanger_dalgangen_ger_varning() -> None:
    """Upphöjd platå med hård kant tvärs över dalgången: vattnet dämms upp norr om den."""
    z0, _, _ = _bas()
    lapp = _lapp()
    z1 = z0.copy()
    niva = float(z0[59, 80]) + 0.6  # 0,6 m över dalgångens botten vid norra kanten
    z1[lapp] = niva
    res = _kor(z1)
    assert len(res.fynd) == 1
    f = res.fynd[0]
    assert f.typ == "ny"
    assert f.storsta_djup_m == pytest.approx(0.6, abs=0.05)
    assert f.volym_m3 > 5.0
    assert f.avstand_till_kant_m <= 1.0
    assert f.y_m > (H - 60) * DX - 2.0  # norr om platåns norrkant (rad 60 -> y = 50)
    assert "Ny sänka" in res.varningar()[0]
    assert "m³" in res.varningar()[0]
    assert res.som_dict()["kontrakt"] == "floden-skarvkontroll/v1"


def test_utjamnad_skarv_ger_ingen_varning() -> None:
    """Samma yta, men ytan följer dalgångens fall och höjs mjukt: ingen damm."""
    z0, y, _ = _bas()
    lapp = _lapp()
    # Höjningen växer från 0 (norra kanten) till 0,2 m i söder, mindre än dalgångens fall,
    # och klingar sedan ut över en slänt.
    rad = np.arange(H)[:, None] * np.ones((1, B))
    t = np.clip((rad - 60) / 40.0, 0.0, 1.0)
    z1 = z0 + 0.2 * t
    z1[~lapp] = z0[~lapp]
    # slänt: höjningen ned till noll över 8 m (16 celler) söder om ytan, nedåt i flödesriktningen
    for s in range(1, 17):
        z1[99 + s, :] = np.where(lapp[99, :], z0[99 + s, :] + 0.2 * (1 - s / 17), z1[99 + s, :])
    res = _kor(z1)
    assert res.fynd == ()


def test_djupare_sanka_raknas() -> None:
    """En befintlig sänka i bandet som blir djupare efter ändringen redovisas som djupare."""
    z0, _, _ = _bas()
    z0 = z0.copy()
    z0[56:60, 78:84] -= 0.15  # liten grop vid norra kanten
    z1 = z0.copy()
    z1[56:60, 78:84] -= 0.25  # blir djupare
    res = kontrollera_skarvar(
        z0.astype(np.float32), z1.astype(np.float32), np.full((H, B), 4, np.uint8),
        np.full((H, B), 0.05, np.float32), _geo(), _lapp(),
    )
    assert [f.typ for f in res.fynd] == ["djupare"]
    assert res.fynd[0].djup_fore_m == pytest.approx(0.15, abs=0.03)
    assert res.fynd[0].tillkommande_volym_m3 > 0.5


def test_sanka_utanfor_bandet_hamnar_bland_ovriga() -> None:
    z0, _, _ = _bas()
    z1 = z0.copy()
    z1[10:14, 70:76] -= 0.3  # långt från lappen (rad 60 -> 25 m bort)
    res = _kor(z1, band_m=10.0)
    assert res.fynd == ()
    assert res.ovriga_nya_antal == 1
    assert "ligger inte vid skarven" in res.varningar()[0]


def test_fel_form_och_tom_lapp() -> None:
    z0, _, _ = _bas()
    with pytest.raises(SkarvKontrollFel):
        kontrollera_skarvar(
            z0.astype(np.float32), z0.astype(np.float32), np.full((H, B), 4, np.uint8),
            np.full((H, B), 0.05, np.float32), _geo(), np.zeros((H, B), bool),
        )
    with pytest.raises(SkarvKontrollFel):
        _kor(z0, band_m=0.0)


def test_avstand_till_kant() -> None:
    d = avstand_till_kant_m(_lapp(), DX, DX)
    assert d[80, 80] == pytest.approx(10.0, abs=0.6)  # mitt i ytan (20 m bred i fallriktningen)
    assert d[50, 80] == pytest.approx(5.0, abs=0.6)  # 10 rader = 5 m norr om kanten


def test_deterministisk() -> None:
    z0, _, _ = _bas()
    z1 = z0.copy()
    z1[_lapp()] = float(z0[59, 80]) + 0.6
    assert _kor(z1).som_dict() == _kor(z1).som_dict()
