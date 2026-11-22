"""Tester för submodulär bullerskärms-CELF och akustisk skuggzonsdetektor (`buller.optimering`)."""

from __future__ import annotations

import itertools
import math

import numpy as np
import pytest

from buller.optimering import (
    detektera_akustiska_bifurkationer,
    valj_bullerskarmar_k,
)


def test_bullerskarm_celf_undviker_dubbelrakning_och_matchar_brute_force() -> None:
    """CELF undviker överlappande skärmar och uppfyller >= (1 - 1/e) mot exakt brute-force."""
    # 6 mottagare över riktvärdet 55 dBA
    bas = np.array([65.0, 64.0, 63.0, 61.0, 60.0, 58.0], dtype=np.float64)
    skarmar = {
        "s_norr_1": np.array([10.0, 9.0, 0.0, 0.0, 0.0, 0.0]),  # täcker mottagare 0–1
        "s_norr_2": np.array([9.5, 8.5, 0.0, 0.0, 0.0, 0.0]),   # överlappande med s_norr_1
        "s_mitt": np.array([0.0, 0.0, 8.0, 6.0, 0.0, 0.0]),     # mottagare 2–3
        "s_soder": np.array([0.0, 0.0, 0.0, 0.0, 5.0, 3.0]),    # mottagare 4–5
        "s_svag": np.array([2.0, 2.0, 2.0, 2.0, 2.0, 2.0]),
    }

    valda = valj_bullerskarmar_k(bas, skarmar, k=3, riktvarde_db=55.0, mal="overskridande_db")
    assert len(valda) == 3
    # Ska välja s_norr_1, s_mitt och s_soder (inte s_norr_2!)
    assert [v.skarm_id for v in valda] == ["s_norr_1", "s_mitt", "s_soder"]
    assert valda[-1].kumulativ_vinst == pytest.approx(10 + 9 + 8 + 6 + 5 + 3, rel=1e-12)
    assert sum(v.nya_godkanda for v in valda) == 6

    # Jämför mot exakt brute-force över alla C(5, 3) delmängder
    over = np.maximum(0.0, bas - 55.0)
    opt = max(
        float(np.sum(np.minimum(over, np.maximum.reduce([skarmar[s] for s in sub]))))
        for sub in itertools.combinations(sorted(skarmar), 3)
    )
    assert valda[-1].kumulativ_vinst >= (1.0 - 1.0 / math.e) * opt - 1e-9
    assert valda[-1].kumulativ_vinst == pytest.approx(opt, rel=1e-12)

    # Testa även mal="antal_godkanda"
    valda_ant = valj_bullerskarmar_k(bas, skarmar, k=2, riktvarde_db=55.0, mal="antal_godkanda")
    assert len(valda_ant) == 2
    assert valda_ant[-1].kumulativ_vinst == 4.0


def test_akustisk_skuggzonsbifurkation() -> None:
    """Nära mottagare förblir i direkt sikt (monoton), fjärran mottagare korsar skuggzonsgräns."""
    # h_s = 0.5 m, h_r = 2.0 m => sqrt(1) + sqrt(4) = 3.0 => g_krit = -340 * 9 / d² = -3060 / d²
    # Vid d = 50 m: g_krit = -1.224 s⁻¹ (utanför [-0.05, 0.05] -> ingen skuggzon)
    # Vid d = 300 m: g_krit = -0.034 s⁻¹ (inom [-0.05, 0.05] -> skuggzonsbifurkation!)
    res = detektera_akustiska_bifurkationer(
        np.array([50.0, 300.0]),
        h_kalla_m=0.5,
        h_mottagare_m=2.0,
        dc_dz_intervall=(-0.05, 0.05),
    )
    assert res[0].skuggzon_bifurkation is False
    assert res[0].monoton_garanterad is True
    assert res[1].skuggzon_bifurkation is True
    assert res[1].monoton_garanterad is False
    assert res[1].kritisk_dc_dz_per_s == pytest.approx(-3060.0 / 90000.0, rel=1e-9)
