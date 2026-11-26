"""Tester för automatisk Rust-dispatch i `tvad.lokal_troghet.kor_2d_lokal_troghet`."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest import mock

import numpy as np
from numpy.typing import NDArray

from testsvit.k1_io import BrytlinjePost, GeoTransformInfo
from tvad import rust_backend
from tvad.kalltermer import Avtappning, Kalltermer
from tvad.lokal_troghet import (
    MARKSLAG_VATTEN,
    TvadFel,
    TvadResultat,
    kor_2d_lokal_troghet,
)


def _liten_sanka() -> tuple[NDArray[np.float32], NDArray[np.uint8], GeoTransformInfo]:
    """Deterministisk 12×12 m (1 m celler) skål med känt djup- och massbalansfacit."""
    h, b = 12, 12
    z = np.full((h, b), 10.5, dtype=np.float32)
    z[4:8, 4:8] = np.float32(10.0)
    markslag = np.full((h, b), 1, dtype=np.uint8)
    geo = GeoTransformInfo(
        bredd=b,
        hojd=h,
        x_min=150000.0,
        y_max=6580012.0,
        pixelstorlek_x=1.0,
        pixelstorlek_y=1.0,
        epsg=3011,
        dtype="float32",
    )
    return z, markslag, geo


def test_auto_dispatch_anropar_rust_nar_kriterier_uppfylls() -> None:
    """När kriterier uppfylls och binären finns delegeras till `kor_2d_lokal_troghet_rust`."""
    z, markslag, geo = _liten_sanka()
    rt, ri = [0.0, 60.0], [180.0, 180.0]

    # Referensresultat direkt från Python-lösaren (bitidentiskt facit)
    py_facit = kor_2d_lokal_troghet(z, markslag, geo, rt, ri, anvand_rust=False)
    assert py_facit.anvande_rust is False

    anrop: list[dict[str, Any]] = []

    def falsk_rust(*args: Any, **kwargs: Any) -> TvadResultat:
        anrop.append({"args": args, "kwargs": kwargs})
        return kor_2d_lokal_troghet(
            args[0],
            args[1],
            args[2],
            args[3],
            args[4],
            slut_tid_s=kwargs["slut_tid_s"],
            cfl=kwargs["cfl"],
            min_djup_m=kwargs["min_djup_m"],
            gles_front=kwargs["gles_front"],
            subgrid_faktor=kwargs["subgrid_faktor"],
            oppna_rander=kwargs["oppna_rander"],
            manning_tabell=kwargs["manning_tabell"],
            dt_max_s=kwargs["dt_max_s"],
            z_nivaer=kwargs["z_nivaer"],
            theta=kwargs["theta"],
            takvatten=kwargs["takvatten"],
            anvand_rust=False,
        )

    with (
        mock.patch.object(rust_backend, "hitta_tvad_karna", return_value=Path("/mock/tvad_karna")),
        mock.patch.object(rust_backend, "kor_2d_lokal_troghet_rust", side_effect=falsk_rust),
    ):
        res = kor_2d_lokal_troghet(z, markslag, geo, rt, ri)

    assert len(anrop) == 1
    assert anrop[0]["kwargs"]["binar"] == Path("/mock/tvad_karna")
    assert res.anvande_rust is True
    np.testing.assert_array_equal(res.max_djup_m, py_facit.max_djup_m)
    np.testing.assert_array_equal(res.max_hastighet_m_s, py_facit.max_hastighet_m_s)
    assert res.volymfel_rel < 1e-12


def test_fallback_till_python_nar_binar_saknas() -> None:
    """Om `hitta_tvad_karna` kastar `TvadFel` faller lösaren tillbaka till Python utan fel."""
    z, markslag, geo = _liten_sanka()
    with (
        mock.patch.object(rust_backend, "hitta_tvad_karna", side_effect=TvadFel("saknas")),
        mock.patch.object(rust_backend, "kor_2d_lokal_troghet_rust") as m_rust,
    ):
        res = kor_2d_lokal_troghet(z, markslag, geo, [0.0, 30.0], [120.0, 120.0])

    m_rust.assert_not_called()
    assert res.anvande_rust is False
    assert res.volymfel_rel < 1e-12
    assert float(np.max(res.max_djup_m)) > 0.0


def test_anvand_rust_false_gar_alltid_till_python() -> None:
    """`anvand_rust=False` ska aldrig anropa Rust-kärnan även om binären finns."""
    z, markslag, geo = _liten_sanka()
    with (
        mock.patch.object(rust_backend, "hitta_tvad_karna", return_value=Path("/mock/tvad_karna")),
        mock.patch.object(rust_backend, "kor_2d_lokal_troghet_rust") as m_rust,
    ):
        res = kor_2d_lokal_troghet(
            z, markslag, geo, [0.0, 30.0], [120.0, 120.0], anvand_rust=False
        )

    m_rust.assert_not_called()
    assert res.anvande_rust is False
    assert res.volymfel_rel < 1e-12


def test_fallback_vid_ostodda_kriterier() -> None:
    """Subgrid > 1, källtermer, brytlinjer, vattennivå, f32 och vatten-markslag använder Python."""
    z, markslag, geo = _liten_sanka()
    rt, ri = [0.0, 30.0], [120.0, 120.0]

    bryt = [
        BrytlinjePost(
            typ="kantsten",
            hojd_m=0.12,
            koordinater=((150002.0, 6580006.0), (150010.0, 6580006.0)),
        )
    ]
    avt = Kalltermer(avtappning=Avtappning(np.full_like(z, 10.0, dtype=np.float64)))

    with (
        mock.patch.object(rust_backend, "hitta_tvad_karna", return_value=Path("/mock/tvad_karna")),
        mock.patch.object(rust_backend, "kor_2d_lokal_troghet_rust") as m_rust,
    ):
        r_sub = kor_2d_lokal_troghet(z, markslag, geo, rt, ri, subgrid_faktor=2)
        r_kal = kor_2d_lokal_troghet(z, markslag, geo, rt, ri, kalltermer=avt)
        r_bryt = kor_2d_lokal_troghet(z, markslag, geo, rt, ri, brytlinjer=bryt)
        r_vn = kor_2d_lokal_troghet(z, markslag, geo, rt, ri, vattenniva_rh2000_m=10.1)
        r_f32 = kor_2d_lokal_troghet(z, markslag, geo, rt, ri, precision="f32")

        slag_vatten = markslag.copy()
        slag_vatten[0, 0] = MARKSLAG_VATTEN
        r_vat = kor_2d_lokal_troghet(z, slag_vatten, geo, rt, ri)

    m_rust.assert_not_called()
    for res in (r_sub, r_kal, r_bryt, r_vn, r_f32, r_vat):
        assert res.anvande_rust is False
        assert res.volymfel_rel < 1e-4
