"""Enhetstester och analytisk verifiering för `webb.sektion` (SCALGO-liknande sektioner)."""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass

import numpy as np
import pytest

from webb.sektion import (
    EPSG,
    HOJDSYSTEM,
    KONTRAKT,
    RasterLager,
    berakna_sektion,
    sampla_polylinje,
    validera_sektion_punkter,
)


@dataclass(frozen=True)
class _FakeGeo:
    x_min: float
    y_max: float
    pixelstorlek_x: float
    pixelstorlek_y: float


def _lutande_terrang(
    *,
    x_min: float = 150_000.0,
    y_max: float = 6_580_100.0,
    bredd: int = 200,
    hojd: int = 200,
    px: float = 0.5,
    z0: float = 10.0,
    lutning_e: float = 0.05,
    lutning_n: float = -0.02,
) -> RasterLager:
    """Analytiskt plan: z(e, n) = z0 + lutning_e*(e_c - x_min) + lutning_n*(y_max - n_c)."""
    kol = np.arange(bredd, dtype=np.float64)
    rad = np.arange(hojd, dtype=np.float64)
    e_c = x_min + (kol + 0.5) * px
    n_c = y_max - (rad + 0.5) * px
    zz = z0 + lutning_e * (e_c[None, :] - x_min) + lutning_n * (y_max - n_c[:, None])
    return RasterLager(
        raster=zz.astype(np.float32),
        x_min=x_min,
        y_max=y_max,
        pixelstorlek_x=px,
        pixelstorlek_y=px,
    )


def test_validera_sektion_punkter_giltiga_och_fel() -> None:
    p = validera_sektion_punkter([[150_000, 6_580_000], [150_050.5, 6_580_000]])
    assert p == [(150_000.0, 6_580_000.0), (150_050.5, 6_580_000.0)]

    # För få / för många punkter
    with pytest.raises(ValueError):
        validera_sektion_punkter([[150_000, 6_580_000]])
    with pytest.raises(ValueError):
        validera_sektion_punkter([[150_000 + i, 6_580_000] for i in range(101)])

    # Fel typ / bool / NaN / Inf
    with pytest.raises(ValueError):
        validera_sektion_punkter("inte en lista")
    with pytest.raises(ValueError):
        validera_sektion_punkter([[True, 6_580_000], [150_010, 6_580_000]])
    with pytest.raises(ValueError):
        validera_sektion_punkter([[math.nan, 6_580_000], [150_010, 6_580_000]])
    with pytest.raises(ValueError):
        validera_sektion_punkter([[150_000, math.inf], [150_010, 6_580_000]])

    # Längd == 0 eller > 50_000 m
    with pytest.raises(ValueError):
        validera_sektion_punkter([[150_000, 6_580_000], [150_000, 6_580_000]])
    with pytest.raises(ValueError):
        validera_sektion_punkter([[150_000, 6_580_000], [200_001, 6_580_000]])


def test_sampla_polylinje_start_slut_och_max_punkter() -> None:
    # 2-punktslinje 10 m, steg 0.5 m -> 21 punkter
    avst, e, n, eff = sampla_polylinje(
        [[150_000.0, 6_580_000.0], [150_010.0, 6_580_000.0]],
        steg_m=0.5,
        max_punkter=300,
    )
    assert len(avst) == 21
    assert avst[0] == 0.0
    assert avst[-1] == pytest.approx(10.0)
    assert e[0] == pytest.approx(150_000.0)
    assert e[-1] == pytest.approx(150_010.0)
    assert eff == pytest.approx(0.5)

    # Polylinje (L-form: 30 m öst + 40 m norr = 70 m)
    avst_p, e_p, n_p, _ = sampla_polylinje(
        [[150_000.0, 6_580_000.0], [150_030.0, 6_580_000.0], [150_030.0, 6_580_040.0]],
        steg_m=1.0,
    )
    assert avst_p[0] == 0.0
    assert avst_p[-1] == pytest.approx(70.0)
    assert e_p[30] == pytest.approx(150_030.0)
    assert n_p[30] == pytest.approx(6_580_000.0)
    assert e_p[-1] == pytest.approx(150_030.0)
    assert n_p[-1] == pytest.approx(6_580_040.0)

    # Lång linje begränsas till max_punkter = 300
    avst_l, _, _, eff_l = sampla_polylinje(
        [[150_000.0, 6_580_000.0], [151_000.0, 6_580_000.0]],
        steg_m=None,
        max_punkter=300,
        pixelstorlek_m=0.5,
    )
    assert len(avst_l) <= 300
    assert avst_l[0] == 0.0
    assert avst_l[-1] == pytest.approx(1000.0)
    assert eff_l >= 1000.0 / 299.0 - 1e-9

    with pytest.raises(ValueError):
        sampla_polylinje([[150_000.0, 6_580_000.0], [150_010.0, 6_580_000.0]], steg_m=-1.0)


def test_analytisk_lutning_exakt_inom_1e4() -> None:
    """Känd analytisk lutning: närmaste-cell-avläsning ska stämma på < 1e-4 m."""
    x_min, y_max, px = 150_000.0, 6_580_100.0, 0.5
    terrang = _lutande_terrang(
        x_min=x_min,
        y_max=y_max,
        bredd=200,
        hojd=200,
        px=px,
        z0=12.0,
        lutning_e=0.04,
        lutning_n=-0.01,
    )
    # Linje genom cellcentra: y = y_max - 10.25 (rad 20), x från +0.25 till +50.25 (steg 0.5)
    svar = berakna_sektion(
        [[x_min + 0.25, y_max - 10.25], [x_min + 50.25, y_max - 10.25]],
        (terrang.raster, _FakeGeo(x_min, y_max, px, px)),
        steg_m=0.5,
    )
    assert svar["kontrakt"] == KONTRAKT
    assert svar["epsg"] == EPSG
    assert svar["hojdsystem"] == HOJDSYSTEM
    assert svar["har_efter"] is False
    assert len(svar["avstand_m"]) == 101

    for e_i, n_i, mark_i in zip(svar["e"], svar["n"], svar["mark_fore_m"], strict=True):
        assert mark_i is not None
        kol = math.floor((e_i - x_min) / px)
        rad = math.floor((y_max - n_i) / px)
        forvantad = 12.0 + 0.04 * ((kol + 0.5) * px) + (-0.01) * ((rad + 0.5) * px)
        assert abs(mark_i - forvantad) < 1e-4

    samm = svar["sammanfattning"]
    assert samm["langd_m"] == pytest.approx(50.0)
    assert samm["antal_punkter"] == 101
    assert samm["mark_fore_min_m"] == pytest.approx(svar["mark_fore_m"][0], abs=1e-4)
    assert samm["mark_fore_max_m"] == pytest.approx(svar["mark_fore_m"][-1], abs=1e-4)
    assert samm["max_djup_fore_m"] is None


def test_overlappande_rutor_djupast_inne() -> None:
    """Vid två överlappande rutor ska cellen som ligger djupast inne i sin ruta väljas."""
    px = 1.0
    # Ruta A: x in [0, 10], värde 10.0; Ruta B: x in [6, 16], värde 20.0.
    # Överlapp [6, 10]: vid x=6.5 är A 3.5 m från kant, B 0.5 m -> A vinner (10.0).
    # Vid x=9.5 är A 0.5 m från kant, B 3.5 m -> B vinner (20.0).
    ruta_a = RasterLager(np.full((10, 10), 10.0, dtype=np.float32), 0.0, 10.0, px, px)
    ruta_b = RasterLager(np.full((10, 10), 20.0, dtype=np.float32), 6.0, 10.0, px, px)

    svar = berakna_sektion(
        [[6.5, 5.5], [9.5, 5.5]],
        [ruta_a, ruta_b],
        steg_m=1.0,
    )
    # x = 6.5 -> A (10.0), x = 7.5 -> A (10.0), x = 8.5 -> B (20.0), x = 9.5 -> B (20.0)
    assert svar["mark_fore_m"] == [10.0, 10.0, 20.0, 20.0]


def test_sanka_vattenniva_takmask_byggnader_och_oversvammad_bredd() -> None:
    """Verifierar känd vattennivå i sänka, takmaskning, byggnad med hål och översvämmad bredd."""
    px = 1.0
    x_min, y_max = 150_000.0, 6_580_020.0
    h, b = 20, 40

    # Markhöjd: sänka mellan kol 10..29 (djupast vid kol 15..24 = 10.0 m, omgivning 11.0 m)
    mark_fore = np.full((h, b), 11.0, dtype=np.float32)
    mark_fore[:, 10:30] = 10.15  # 0.05 m djupt under W=10.20 (>= 2 cm, < 10 cm)
    mark_fore[:, 15:25] = 10.00  # 0.20 m djupt under W=10.20 (>= 10 cm)

    vatten_niva = 10.20
    djup_fore = np.maximum(0.0, vatten_niva - mark_fore).astype(np.float32)

    # Takmask över kol 20..24 (byggnad i vattnet -> vatten ska maskas till None)
    tak_fore = np.zeros((h, b), dtype=bool)
    tak_fore[:, 20:25] = True

    # Efter-scenario: höjd mark + djupare vatten
    mark_efter = mark_fore.copy()
    mark_efter[:, 10:15] = 10.05
    djup_efter = np.maximum(0.0, 10.30 - mark_efter).astype(np.float32)

    # Byggnad med innergård (hål) över [x_min+20, x_min+25]
    byggnader_fore = [
        {
            "ring": [
                [x_min + 20.0, y_max - 15.0],
                [x_min + 25.0, y_max - 15.0],
                [x_min + 25.0, y_max - 5.0],
                [x_min + 20.0, y_max - 5.0],
                [x_min + 20.0, y_max - 15.0],
            ],
            "hal": [
                [
                    [x_min + 22.0, y_max - 11.0],
                    [x_min + 23.0, y_max - 11.0],
                    [x_min + 23.0, y_max - 9.0],
                    [x_min + 22.0, y_max - 9.0],
                    [x_min + 22.0, y_max - 11.0],
                ]
            ],
            "hojd_m": 14.0,
            "takhojd_rh2000": 24.5,
        }
    ]
    # Byggnad efter utan explicit takhojd_rh2000 (ska falla tillbaka på mark + hojd_m)
    byggnader_efter = [
        {
            "ring": [
                [x_min + 2.0, y_max - 15.0],
                [x_min + 6.0, y_max - 15.0],
                [x_min + 6.0, y_max - 5.0],
                [x_min + 2.0, y_max - 5.0],
                [x_min + 2.0, y_max - 15.0],
            ],
            "hal": [],
            "hojd_m": 12.0,
            "takhojd_rh2000": None,
        }
    ]

    # Sektion längs rad 10 (y = y_max - 10.5) från kol 0.5 till kol 39.5 (39 m, 40 punkter)
    svar = berakna_sektion(
        [[x_min + 0.5, y_max - 10.5], [x_min + 39.5, y_max - 10.5]],
        RasterLager(mark_fore, x_min, y_max, px, px),
        terrang_efter=RasterLager(mark_efter, x_min, y_max, px, px),
        djup_fore=RasterLager(djup_fore, x_min, y_max, px, px),
        tak_fore=tak_fore,
        djup_efter=RasterLager(djup_efter, x_min, y_max, px, px),
        tak_efter=tak_fore,
        byggnader_fore=byggnader_fore,
        byggnader_efter=byggnader_efter,
        steg_m=1.0,
    )

    assert svar["har_efter"] is True

    # Torr punkt (kol 0): djup = 0.0, vattenyta = None
    assert svar["vatten_djup_fore_m"][0] == 0.0
    assert svar["vatten_yta_fore_m"][0] is None

    # Sänka utanför tak (kol 16): mark = 10.0, djup = 0.20, vattenyta = 10.20 exakt
    assert svar["mark_fore_m"][16] == pytest.approx(10.0, abs=1e-4)
    assert svar["vatten_djup_fore_m"][16] == pytest.approx(0.20, abs=1e-4)
    assert svar["vatten_yta_fore_m"][16] == pytest.approx(10.20, abs=1e-4)

    # Takmaskade celler (kol 20..24): inget vatten genom byggnad!
    for k in range(20, 25):
        assert svar["vatten_djup_fore_m"][k] is None
        assert svar["vatten_yta_fore_m"][k] is None

    # Byggnad före med hål: kol 20,21,23,24 inne -> True & tak 24.5; kol 22 i hålet -> False & None
    assert svar["byggnad_fore"][21] is True
    assert svar["takhojd_fore_m"][21] == pytest.approx(24.5, abs=1e-4)
    assert svar["byggnad_fore"][22] is False
    assert svar["takhojd_fore_m"][22] is None

    # Byggnad efter med fallback mark (11.0) + hojd_m (12.0) = 23.0
    assert svar["byggnad_efter"][3] is True
    assert svar["takhojd_efter_m"][3] == pytest.approx(23.0, abs=1e-4)

    # Översvämmad bredd före (tak maskar kol 20..24):
    # >= 0.02 m: kol 10..19 (10 st) + kol 25..29 (5 st) = 15 m (varje inre punkt vikt 1.0 m)
    # >= 0.10 m: kol 15..19 (5 st) = 5 m
    samm = svar["sammanfattning"]
    assert samm["max_djup_fore_m"] == pytest.approx(0.20, abs=1e-4)
    assert samm["max_djup_efter_m"] == pytest.approx(0.30, abs=1e-4)
    assert samm["oversvammad_bredd_002m_fore_m"] == pytest.approx(15.0, abs=1e-3)
    assert samm["oversvammad_bredd_010m_fore_m"] == pytest.approx(5.0, abs=1e-3)
    # Efter har djup >= 0.10 m i alla 15 omaskade sänkceller (kol 10..19 + 25..29)
    assert samm["oversvammad_bredd_010m_efter_m"] == pytest.approx(15.0, abs=1e-3)


def test_prestanda_300_punkter_under_50ms() -> None:
    """Prestandabudget: 300 punkter över 400x400 raster + 200 byggnader ska ta < 50 ms."""
    px = 0.5
    x_min, y_max = 150_000.0, 6_580_200.0
    terrang = _lutande_terrang(x_min=x_min, y_max=y_max, bredd=400, hojd=400, px=px)
    djup = RasterLager(np.full((400, 400), 0.15, dtype=np.float32), x_min, y_max, px, px)
    tak = np.zeros((400, 400), dtype=bool)
    byggnader = [
        {
            "ring": [
                [x_min + (i % 20) * 10.0, y_max - (i // 20) * 15.0 - 8.0],
                [x_min + (i % 20) * 10.0 + 6.0, y_max - (i // 20) * 15.0 - 8.0],
                [x_min + (i % 20) * 10.0 + 6.0, y_max - (i // 20) * 15.0 - 2.0],
                [x_min + (i % 20) * 10.0, y_max - (i // 20) * 15.0 - 2.0],
                [x_min + (i % 20) * 10.0, y_max - (i // 20) * 15.0 - 8.0],
            ],
            "hal": [],
            "hojd_m": 15.0,
            "takhojd_rh2000": 27.0,
        }
        for i in range(200)
    ]

    # Uppvärmning (import/JIT-fritt, men undviker kall cache)
    berakna_sektion(
        [[x_min + 1.0, y_max - 5.0], [x_min + 195.0, y_max - 150.0]],
        terrang,
        djup_fore=djup,
        tak_fore=tak,
        byggnader_fore=byggnader,
        max_punkter=300,
    )

    t0 = time.perf_counter()
    svar = berakna_sektion(
        [[x_min + 1.0, y_max - 5.0], [x_min + 195.0, y_max - 150.0]],
        terrang,
        djup_fore=djup,
        tak_fore=tak,
        byggnader_fore=byggnader,
        max_punkter=300,
    )
    tid_ms = (time.perf_counter() - t0) * 1000.0
    assert svar["sammanfattning"]["antal_punkter"] == 300
    grans_ms = 50.0 * float(os.environ.get("FLODEN_TIDSFAKTOR", "1"))
    assert tid_ms < grans_ms, f"Sektionsberäkning tog {tid_ms:.1f} ms (budget < {grans_ms:.0f} ms)"
