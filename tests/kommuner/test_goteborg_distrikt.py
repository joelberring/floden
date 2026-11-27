# ruff: noqa: E501
"""Tester för Göteborgs jämförelseområden (`goteborg.toml`) och vattenyte-diagnostik (P1-7)."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pytest

from geografi.planera import bedom_kommun_dataklass, planera_geografi
from kommuner.konfig import hitta_kommun_for_geografi, las_kommun
from kommuner.vattenytor import (
    VattenKomponentDiagnos,
    vattenytor_for_rutor,
    vattenytor_for_rutor_med_diagnos,
)
from markmodell.rutnat import Ruta

REPO = Path(__file__).resolve().parents[2]
E0, N0 = 150000.0, 6400000.0  # EPSG:3011


def test_goteborg_toml_laddar_haga_lindholmen_och_guldheden() -> None:
    k = las_kommun("goteborg", rot=REPO)
    assert k.id == "goteborg"
    assert k.publicering_epsg == 3007
    assert k.standard_omrade == "haga"

    # Befintligt Haga ska vara helt intakt
    assert k.omraden["haga"].bbox == (147400.0, 6397500.0, 147900.0, 6398000.0)
    assert k.geografier["goteborg-test"].omrade == "haga"
    assert k.geografier["goteborg-test"].max_buffert_m == 1500.0

    # Nya distrikt: Lindholmen (500x500 m) och Guldheden (1000x1000 m)
    lind = k.omraden["lindholmen"]
    assert lind.bbox == (146100.0, 6398500.0, 146600.0, 6399000.0)
    assert lind.sida_m == pytest.approx(500.0)

    guld = k.omraden["guldheden"]
    assert guld.bbox == (147800.0, 6395900.0, 148800.0, 6396900.0)
    assert guld.sida_m == pytest.approx(1000.0)

    # Geografidefinitioner hittas via geografi-uppslag
    for gid, omr_id in (
        ("goteborg-test", "haga"),
        ("goteborg-lindholmen", "lindholmen"),
        ("goteborg-guldheden", "guldheden"),
    ):
        assert gid in k.geografier
        assert k.geografier[gid].omrade == omr_id
        assert k.geografier[gid].max_buffert_m == 1500.0
        assert hitta_kommun_for_geografi(gid, rot=REPO).id == "goteborg"


def test_goteborg_distrikt_ar_oppna_och_kan_planeras() -> None:
    k = las_kommun("goteborg", rot=REPO)
    dataklass, stangd_baskarta, _, _ = bedom_kommun_dataklass(k)
    assert dataklass == "oppen"
    assert stangd_baskarta is False

    for omr_id in ("haga", "lindholmen", "guldheden"):
        omr = k.omraden[omr_id]
        plan = planera_geografi(
            k,
            omr.bbox,
            avgransning_epsg=k.publicering_epsg,
            krav_oppen=True,
        )
        d = plan.till_dict()
        assert d["kommun_id"] == "goteborg"
        assert d["dataklass"] == "oppen"
        assert d["antal_karnrutor"] >= 1
        assert d["antal_rutor_totalt"] >= d["antal_karnrutor"]


def test_vattenytor_avvisar_korrupt_strandlinje_outlier_men_bevarar_bergsdamm(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """P1-7: korrupt z=70,8 m på lågland avvisas; giltig bergsdamm vid 70,8 m på 74 m-berg bevaras."""
    ruta = Ruta(E0, N0, 120.0, 0.0, 1.0)
    z = np.full(ruta.form_rc, 3.0, dtype=np.float32)

    # 1) Normal kanal i väster: botten -4 m, strand +0,5 m
    z[:, 10:25] = -4.0

    # 2) Giltig bergsdamm i öster: bergplatå +74 m, dammbotten +68 m, strand +70,8 m
    z[15:45, 75:105] = 74.0
    z[20:40, 80:100] = 68.0

    # 3) Korrupt strandlinje-outlier i mitten-söder på lågland (+3 m) med z=70,8 m
    linjer = [
        np.array([[E0 + 10.0, N0, 0.5], [E0 + 10.0, N0 + 120.0, 0.5]], dtype=np.float64),
        np.array([[E0 + 25.0, N0, 0.5], [E0 + 25.0, N0 + 120.0, 0.5]], dtype=np.float64),
        # Giltig bergsdamm på +70,8 m (rad 20..40 => N = N0+80..N0+100)
        np.array(
            [
                [E0 + 80.0, N0 + 80.0, 70.8],
                [E0 + 100.0, N0 + 80.0, 70.8],
                [E0 + 100.0, N0 + 100.0, 70.8],
                [E0 + 80.0, N0 + 100.0, 70.8],
                [E0 + 80.0, N0 + 80.0, 70.8],
            ],
            dtype=np.float64,
        ),
        # Korrupt outlier-strandlinje på +70,8 m mitt på +3 m-lågland (N = N0+15..N0+30)
        np.array([[E0 + 55.0, N0 + 15.0, 70.8], [E0 + 65.0, N0 + 15.0, 70.8]], dtype=np.float64),
    ]

    with caplog.at_level(logging.INFO, logger="kommuner.vattenytor"):
        res, diag = vattenytor_for_rutor_med_diagnos(
            [(ruta, z)],
            linjer,
            marginal_m=0.2,
            max_avstand_m=2.0,
        )

    mask, niva = res[0]
    assert len(diag) == 3
    godkanda = [d for d in diag if d.status == "godkand"]
    avvisade = [d for d in diag if d.status == "avvisad_outlier"]
    assert len(godkanda) == 2
    assert len(avvisade) == 1

    # Kanalen godkänd vid ~0,5 m
    assert mask[:, 12:23].all()
    assert np.allclose(niva[:, 12:23], 0.5)

    # Giltig bergsdamm godkänd vid 70,8 m (omgivande berg är 74 m, överhöjd -3,2 m)
    assert mask[22:38, 82:98].all()
    assert np.allclose(niva[22:38, 82:98], 70.8)
    bergsdamm = next(d for d in godkanda if d.niva_median_m > 50.0)
    assert bergsdamm.omgivning_z_median_m == pytest.approx(74.0)
    assert bergsdamm.overhojd_over_omgivning_m == pytest.approx(70.8 - 74.0, abs=0.1)

    # Outliern på lågland avvisades och skapade ingen vattenyta där
    assert not mask[95:110, 50:70].any()
    assert avvisade[0].niva_median_m == pytest.approx(70.8)
    assert avvisade[0].overhojd_over_omgivning_m is not None
    assert avvisade[0].overhojd_over_omgivning_m > 60.0
    assert any("Avvisar misstänkt strandlinje-outlier" in r.message for r in caplog.records)
    assert any("Vattenyta på hög nivå" in r.message for r in caplog.records)


def test_vattenytor_diagnostik_parameter_och_inaktiverad_grans() -> None:
    """`diagnostik`-listan fylls även via `vattenytor_for_rutor`, och `None` stänger av avvisning."""
    ruta = Ruta(E0, N0, 40.0, 0.0, 1.0)
    z = np.full(ruta.form_rc, 2.0, dtype=np.float32)
    linjer = [np.array([[E0 + 15.0, N0 + 20.0, 80.0], [E0 + 25.0, N0 + 20.0, 80.0]], dtype=np.float64)]

    diag_avvisad: list[VattenKomponentDiagnos] = []
    res_avvisad = vattenytor_for_rutor(
        [(ruta, z)], linjer, marginal_m=0.2, max_avstand_m=2.0, diagnostik=diag_avvisad
    )
    assert not res_avvisad[0][0].any()
    assert len(diag_avvisad) == 1 and diag_avvisad[0].status == "avvisad_outlier"

    diag_tillaten: list[VattenKomponentDiagnos] = []
    res_tillaten = vattenytor_for_rutor(
        [(ruta, z)],
        linjer,
        marginal_m=0.2,
        max_avstand_m=2.0,
        max_overhojd_over_omgivning_m=None,
        diagnostik=diag_tillaten,
    )
    assert res_tillaten[0][0].all()
    assert len(diag_tillaten) == 1 and diag_tillaten[0].status == "godkand"
