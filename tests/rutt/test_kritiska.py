"""Tester för submodulär k-optimering med CELF i ruttanalysen (`src/rutt/kritiska.py`)."""

from __future__ import annotations

import itertools
import math
import time
from collections import deque
from pathlib import Path

from rutt import (
    VagKant,
    VagNat,
    VagNod,
    las_testparametrar_fran_konfig,
    valj_kritisk_insats_k,
)

REPO_ROT = Path(__file__).resolve().parents[2]
CONFIG_TOML = REPO_ROT / "config.toml"


def _nod(nod_id: str, e: float, n: float) -> VagNod:
    return VagNod(nod_id=nod_id, e_m=e, n_m=n, z_rh2000=10.0)


def _kant(kant_id: str, fran: str, till: str, langd_m: float = 50.0) -> VagKant:
    return VagKant(
        kant_id=kant_id,
        fran_nod=fran,
        till_nod=till,
        langd_m=langd_m,
        normal_hastighet_km_h=30.0,
    )


def test_celf_undviker_dubbelrakning_vid_parallella_infarter() -> None:
    """Två parallella gator till samma 20-kluster + en gata till 15-kluster -> totalt 35."""
    param = las_testparametrar_fran_konfig(CONFIG_TOML)
    noder = {
        "S": _nod("S", 0.0, 0.0),
        "C20": _nod("C20", 100.0, 0.0),
        "C15": _nod("C15", 0.0, 100.0),
    }
    kanter = {
        "e_20_a": _kant("e_20_a", "S", "C20"),
        "e_20_b": _kant("e_20_b", "S", "C20"),
        "e_15": _kant("e_15", "S", "C15"),
    }
    vagnat = VagNat(noder=noder, kanter=kanter)
    stationer = {"st1": "S"}
    adresser = {f"a_{i}": "C20" for i in range(20)} | {f"b_{i}": "C15" for i in range(15)}
    kant_djup = {"e_20_a": 0.6, "e_20_b": 0.6, "e_15": 0.6}

    valda = valj_kritisk_insats_k(
        vagnat,
        stationer,
        adresser,
        0.0,
        kant_djup,
        param,
        "personbil",
        k=2,
        skarp=False,
        tillat_testvarden=True,
    )
    assert len(valda) == 2
    assert valda[0].kant_id in ("e_20_a", "e_20_b")
    assert valda[0].marginalvinst == 20.0
    assert valda[0].kumulativ_vinst == 20.0
    assert valda[0].vinst_ensam == 20.0

    # Andra steget ska välja e_15 (marginalvinst 15) i stället för andra parallella gatan (0)
    assert valda[1].kant_id == "e_15"
    assert valda[1].marginalvinst == 15.0
    assert valda[1].kumulativ_vinst == 35.0
    assert valda[1].vinst_ensam == 15.0

    # Viktade samhällsviktiga mål: sjukhus på C15 med vikt 100 prioriterar e_15 först
    vikter = {"b_0": 100.0}
    valda_vikt = valj_kritisk_insats_k(
        vagnat,
        stationer,
        adresser,
        0.0,
        kant_djup,
        param,
        "personbil",
        k=2,
        adress_vikter=vikter,
        skarp=False,
        tillat_testvarden=True,
    )
    assert valda_vikt[0].kant_id == "e_15"
    assert valda_vikt[0].marginalvinst == 114.0
    assert valda_vikt[1].kumulativ_vinst == 134.0


def test_celf_mot_exakt_brute_force_k3() -> None:
    """Jämför CELF mot exakt brute-force på litet nät (<= 12 kandidatkanter, k = 3)."""
    param = las_testparametrar_fran_konfig(CONFIG_TOML)
    # Station S och 6 klusternoder med 10 ofarbara kandidatkanter (inkl. redundanta broar)
    noder = {"S": _nod("S", 0.0, 0.0)}
    kluster_storlek = {"K1": 8, "K2": 6, "K3": 5, "K4": 4, "K5": 3, "K6": 2}
    for i, kn in enumerate(kluster_storlek, start=1):
        noder[kn] = _nod(kn, float(i * 50), float((i % 3) * 40))

    kanter_spec = [
        ("e1", "S", "K1"),
        ("e2", "S", "K1"),  # redundant till K1
        ("e3", "S", "K2"),
        ("e4", "K1", "K4"),  # kedjad bakom K1
        ("e5", "S", "K3"),
        ("e6", "S", "K3"),  # redundant till K3
        ("e7", "K2", "K5"),  # kedjad bakom K2
        ("e8", "S", "K4"),
        ("e9", "S", "K5"),
        ("e10", "S", "K6"),
    ]
    kanter = {kid: _kant(kid, u, v) for kid, u, v in kanter_spec}
    vagnat = VagNat(noder=noder, kanter=kanter)
    stationer = {"st1": "S"}
    adresser: dict[str, str] = {}
    for kn, antal in kluster_storlek.items():
        for j in range(antal):
            adresser[f"{kn}_{j}"] = kn
    kant_djup = {kid: 0.6 for kid in kanter}

    k = 3
    valda = valj_kritisk_insats_k(
        vagnat,
        stationer,
        adresser,
        0.0,
        kant_djup,
        param,
        "personbil",
        k=k,
        skarp=False,
        tillat_testvarden=True,
    )
    celf_vinst = valda[-1].kumulativ_vinst

    # Exakt brute-force över alla C(10, 3) = 120 delmängder
    def eval_subset(oppna: tuple[str, ...]) -> int:
        adj: dict[str, list[str]] = {nid: [] for nid in noder}
        for kid in oppna:
            c = kanter[kid]
            adj[c.fran_nod].append(c.till_nod)
            adj[c.till_nod].append(c.fran_nod)
        vis = {"S"}
        q = deque(["S"])
        while q:
            u = q.popleft()
            for v in adj[u]:
                if v not in vis:
                    vis.add(v)
                    q.append(v)
        return sum(1 for adr_nod in adresser.values() if adr_nod in vis)

    opt = max(eval_subset(sub) for sub in itertools.combinations(sorted(kanter), k))
    assert celf_vinst >= (1.0 - 1.0 / math.e) * opt - 1e-9
    assert celf_vinst == float(opt)


def test_celf_prestanda_400_noder_760_kanter() -> None:
    """Prestandakrav: 20x20-nät (400 noder, 760 kanter), k = 5 under 0,05 s."""
    param = las_testparametrar_fran_konfig(CONFIG_TOML)
    n_sida = 20
    noder: dict[str, VagNod] = {}
    kanter: dict[str, VagKant] = {}
    kant_djup: dict[str, float] = {}

    for r in range(n_sida):
        for c in range(n_sida):
            nid = f"n_{r}_{c}"
            noder[nid] = _nod(nid, float(c * 25), float(r * 25))

    for r in range(n_sida):
        for c in range(n_sida):
            u = f"n_{r}_{c}"
            if c + 1 < n_sida:
                v = f"n_{r}_{c + 1}"
                kid = f"h_{r}_{c}"
                kanter[kid] = _kant(kid, u, v, langd_m=25.0)
                # Skapa översvämmade snitt längs jämna kolumner
                kant_djup[kid] = 0.6 if c in (4, 9, 14) else 0.0
            if r + 1 < n_sida:
                v = f"n_{r + 1}_{c}"
                kid = f"v_{r}_{c}"
                kanter[kid] = _kant(kid, u, v, langd_m=25.0)
                kant_djup[kid] = 0.6 if r in (5, 11, 16) else 0.0

    assert len(noder) == 400
    assert len(kanter) == 760

    stationer = {"st0": "n_0_0"}
    adresser = {f"adr_{r}_{c}": f"n_{r}_{c}" for r in range(n_sida) for c in range(n_sida)}
    vagnat = VagNat(noder=noder, kanter=kanter)

    t0 = time.perf_counter()
    valda = valj_kritisk_insats_k(
        vagnat,
        stationer,
        adresser,
        0.0,
        kant_djup,
        param,
        "personbil",
        k=5,
        skarp=False,
        tillat_testvarden=True,
    )
    dt_s = time.perf_counter() - t0

    assert len(valda) == 5
    assert dt_s < 0.05, f"CELF tog {dt_s:.4f} s (gräns 0.05 s)"
    assert all(valda[i].marginalvinst >= valda[i + 1].marginalvinst for i in range(4))
