# ruff: noqa: E501
"""Avrinningsnätet: ett träd med rätt ordning och tillrinning, inga parallella stumpar på sluttningar."""

from __future__ import annotations

import numpy as np

from sankgraf.avrinningsnat import Stracka, avrinningsnat_ur_falt


def _kor(z: np.ndarray, min_yta: float = 100.0, aktiv: np.ndarray | None = None) -> list[Stracka]:
    akt = np.ones(z.shape, dtype=bool) if aktiv is None else aktiv
    return avrinningsnat_ur_falt(
        z, akt, None, x_min=0.0, y_max=float(z.shape[0]), pixel_m=1.0, min_yta_m2=min_yta
    )


def _dal(h: int = 80, b: int = 41) -> np.ndarray:
    r, c = np.mgrid[0:h, 0:b].astype(float)
    return 0.05 * np.abs(c - b // 2) + 0.02 * (h - r)  # V-dal som lutar mot söder (nedre raden)


def test_v_dal_ger_ett_stråk_langs_dalbotten() -> None:
    z = _dal()
    v = _kor(z, 200.0)
    assert len(v) == 1
    s = v[0]
    assert s.ordning == 1
    assert abs(s.koordinater[0][0] - 20.5) < 1.5 and abs(s.koordinater[-1][0] - 20.5) < 1.5
    # dalbotten tar emot hela bredden (41 celler) över dalens längd
    assert s.tillrinning_m2 > 41 * 60


def _avst_till_linjer(h: int, b: int, linjer: list[list[tuple[float, float]]]) -> np.ndarray:
    r, c = np.mgrid[0:h, 0:b].astype(float)
    d = np.full((h, b), np.inf)
    for lin in linjer:
        for (r0, c0), (r1, c1) in zip(lin[:-1], lin[1:], strict=True):
            vr, vc = r1 - r0, c1 - c0
            t = np.clip(((r - r0) * vr + (c - c0) * vc) / (vr * vr + vc * vc), 0.0, 1.0)
            d = np.minimum(d, np.hypot(r - (r0 + t * vr), c - (c0 + t * vc)))
    return d


def test_tre_nivaer_ger_strahler_tre_med_summerad_tillrinning() -> None:
    h, b = 140, 81
    linjer = [
        [(0, 10), (50, 20)], [(0, 30), (50, 20)], [(0, 50), (50, 60)], [(0, 70), (50, 60)],
        [(50, 20), (90, 40)], [(50, 60), (90, 40)], [(90, 40), (139, 40)],
    ]  # fmt: skip
    r = np.mgrid[0:h, 0:b][0].astype(float)
    z = 0.1 * _avst_till_linjer(h, b, linjer) + 0.05 * (h - r)
    v = _kor(z, 150.0)
    assert max(s.ordning for s in v) == 3
    assert sorted({s.ordning for s in v}) == [1, 2, 3]
    huvud = max(v, key=lambda s: s.tillrinning_m2)
    assert huvud.ordning == 3
    assert huvud.tillrinning_m2 > 0.75 * 81 * 140 * 0.9  # nästan hela området når huvudstråket


def test_platt_svagt_lutande_yta_ger_inga_parallella_stumpar() -> None:
    r, c = np.mgrid[0:120, 0:120].astype(float)
    z = 0.002 * (120 - r)
    assert _kor(z, 200.0) == []
    z2 = 0.002 * (120 - r) + 0.0005 * c  # snett
    assert _kor(z2, 200.0) == []


def test_plan_yta_med_sanka_ger_ingen_stump() -> None:
    z = np.full((60, 60), 10.0)
    z[25:35, 25:35] = 9.0
    assert _kor(z, 200.0) == []


def test_byggnader_ar_hinder_och_tak_ger_extra_yta() -> None:
    z = _dal(60, 41)
    akt = np.ones(z.shape, dtype=bool)
    akt[10:20, 5:15] = False
    extra = np.zeros(z.shape)
    extra[30, 20] = 400.0
    v1 = avrinningsnat_ur_falt(z, akt, None, x_min=0.0, y_max=60.0, pixel_m=1.0, min_yta_m2=100.0)
    v2 = avrinningsnat_ur_falt(z, akt, extra, x_min=0.0, y_max=60.0, pixel_m=1.0, min_yta_m2=100.0)
    assert max(s.tillrinning_m2 for s in v2) > max(s.tillrinning_m2 for s in v1) + 300
