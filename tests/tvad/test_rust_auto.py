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
    RUST_MIN_CELLER,
    TvadFel,
    TvadResultat,
    kor_2d_lokal_troghet,
)


def _sanka(hojd: int = 12, bredd: int = 12) -> tuple[
    NDArray[np.float32], NDArray[np.uint8], GeoTransformInfo
]:
    """Deterministisk skål med givna dimensioner och känt massbalansfacit."""
    z = np.full((hojd, bredd), 10.5, dtype=np.float32)
    r0, r1 = hojd // 3, max(hojd // 3 + 2, 2 * hojd // 3)
    c0, c1 = bredd // 3, max(bredd // 3 + 2, 2 * bredd // 3)
    z[r0:r1, c0:c1] = np.float32(10.0)
    markslag = np.full((hojd, bredd), 1, dtype=np.uint8)
    geo = GeoTransformInfo(
        bredd=bredd,
        hojd=hojd,
        x_min=150000.0,
        y_max=6580000.0 + float(hojd),
        pixelstorlek_x=1.0,
        pixelstorlek_y=1.0,
        epsg=3011,
        dtype="float32",
    )
    return z, markslag, geo


def _falsk_rust_wrapper(anrop: list[dict[str, Any]]) -> Any:
    def _falsk(*args: Any, **kwargs: Any) -> TvadResultat:
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

    return _falsk


def test_anvand_rust_true_delegerar_oavsett_storlek() -> None:
    """`anvand_rust=True` delegerar till `kor_2d_lokal_troghet_rust` även på små rutnät."""
    z, markslag, geo = _sanka(12, 12)
    rt, ri = [0.0, 60.0], [180.0, 180.0]

    py_facit = kor_2d_lokal_troghet(z, markslag, geo, rt, ri, anvand_rust=False)
    assert py_facit.anvande_rust is False

    anrop: list[dict[str, Any]] = []
    with (
        mock.patch.object(rust_backend, "hitta_tvad_karna", return_value=Path("/mock/tvad_karna")),
        mock.patch.object(
            rust_backend, "kor_2d_lokal_troghet_rust", side_effect=_falsk_rust_wrapper(anrop)
        ),
    ):
        res = kor_2d_lokal_troghet(z, markslag, geo, rt, ri, anvand_rust=True)

    assert len(anrop) == 1
    assert anrop[0]["kwargs"]["binar"] == Path("/mock/tvad_karna")
    assert res.anvande_rust is True
    np.testing.assert_array_equal(res.max_djup_m, py_facit.max_djup_m)
    np.testing.assert_array_equal(res.max_hastighet_m_s, py_facit.max_hastighet_m_s)
    assert res.volymfel_rel < 1e-12


def test_auto_none_respekterar_rust_min_celler() -> None:
    """`anvand_rust=None` (standard) använder Python under `RUST_MIN_CELLER` och Rust över."""
    rt, ri = [0.0, 5.0], [120.0, 120.0]

    # Under tröskeln (144 < RUST_MIN_CELLER): Python utan Rust-anrop
    z_liten, ms_liten, geo_liten = _sanka(12, 12)
    assert z_liten.size < RUST_MIN_CELLER
    with (
        mock.patch.object(rust_backend, "hitta_tvad_karna", return_value=Path("/mock/tvad_karna")),
        mock.patch.object(rust_backend, "kor_2d_lokal_troghet_rust") as m_rust,
    ):
        res_liten = kor_2d_lokal_troghet(z_liten, ms_liten, geo_liten, rt, ri)
    m_rust.assert_not_called()
    assert res_liten.anvande_rust is False

    # Över tröskeln (>= RUST_MIN_CELLER): automatisk Rust-dispatch!
    sida = int(np.ceil(np.sqrt(RUST_MIN_CELLER)))
    z_stor, ms_stor, geo_stor = _sanka(sida, sida)
    assert z_stor.size >= RUST_MIN_CELLER
    anrop: list[dict[str, Any]] = []
    with (
        mock.patch.object(rust_backend, "hitta_tvad_karna", return_value=Path("/mock/tvad_karna")),
        mock.patch.object(
            rust_backend, "kor_2d_lokal_troghet_rust", side_effect=_falsk_rust_wrapper(anrop)
        ),
    ):
        res_stor = kor_2d_lokal_troghet(z_stor, ms_stor, geo_stor, rt, ri)
    assert len(anrop) == 1
    assert res_stor.anvande_rust is True


def test_vattenniva_utan_markslag_8_valjer_samma_backend() -> None:
    """Utan `MARKSLAG_VATTEN` väljer körning med/utan `vattenniva_rh2000_m` exakt samma backend."""
    z, markslag, geo = _sanka(12, 12)
    rt, ri = [0.0, 30.0], [120.0, 120.0]
    anrop: list[dict[str, Any]] = []
    with (
        mock.patch.object(rust_backend, "hitta_tvad_karna", return_value=Path("/mock/tvad_karna")),
        mock.patch.object(
            rust_backend, "kor_2d_lokal_troghet_rust", side_effect=_falsk_rust_wrapper(anrop)
        ),
    ):
        r0 = kor_2d_lokal_troghet(z, markslag, geo, rt, ri, anvand_rust=True)
        r1 = kor_2d_lokal_troghet(
            z, markslag, geo, rt, ri, vattenniva_rh2000_m=2.0, anvand_rust=True
        )
    assert len(anrop) == 2
    assert r0.anvande_rust is True and r1.anvande_rust is True
    np.testing.assert_array_equal(r0.max_djup_m, r1.max_djup_m)
    assert r0.ut_m3 == r1.ut_m3 and r0.lagrat_m3 == r1.lagrat_m3


def test_fallback_till_python_nar_binar_saknas() -> None:
    """Om `hitta_tvad_karna` kastar `TvadFel` faller lösaren tillbaka till Python utan fel."""
    z, markslag, geo = _sanka(12, 12)
    with (
        mock.patch.object(rust_backend, "hitta_tvad_karna", side_effect=TvadFel("saknas")),
        mock.patch.object(rust_backend, "kor_2d_lokal_troghet_rust") as m_rust,
    ):
        res = kor_2d_lokal_troghet(
            z, markslag, geo, [0.0, 30.0], [120.0, 120.0], anvand_rust=True
        )

    m_rust.assert_not_called()
    assert res.anvande_rust is False
    assert res.volymfel_rel < 1e-12
    assert float(np.max(res.max_djup_m)) > 0.0


def test_anvand_rust_false_gar_alltid_till_python() -> None:
    """`anvand_rust=False` ska aldrig anropa Rust-kärnan även på stora rutnät."""
    sida = int(np.ceil(np.sqrt(RUST_MIN_CELLER)))
    z, markslag, geo = _sanka(sida, sida)
    with (
        mock.patch.object(rust_backend, "hitta_tvad_karna", return_value=Path("/mock/tvad_karna")),
        mock.patch.object(rust_backend, "kor_2d_lokal_troghet_rust") as m_rust,
    ):
        res = kor_2d_lokal_troghet(
            z, markslag, geo, [0.0, 2.0], [60.0, 60.0], anvand_rust=False
        )

    m_rust.assert_not_called()
    assert res.anvande_rust is False
    assert res.volymfel_rel < 1e-12


def test_fallback_vid_ostodda_kriterier() -> None:
    """Subgrid > 1, källtermer, brytlinjer, f32 och vatten-markslag använder Python."""
    z, markslag, geo = _sanka(12, 12)
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
        r_sub = kor_2d_lokal_troghet(z, markslag, geo, rt, ri, subgrid_faktor=2, anvand_rust=True)
        r_kal = kor_2d_lokal_troghet(z, markslag, geo, rt, ri, kalltermer=avt, anvand_rust=True)
        r_bryt = kor_2d_lokal_troghet(z, markslag, geo, rt, ri, brytlinjer=bryt, anvand_rust=True)
        r_f32 = kor_2d_lokal_troghet(z, markslag, geo, rt, ri, precision="f32", anvand_rust=True)

        slag_vatten = markslag.copy()
        slag_vatten[0, 0] = MARKSLAG_VATTEN
        r_vat = kor_2d_lokal_troghet(
            z, slag_vatten, geo, rt, ri, vattenniva_rh2000_m=10.1, anvand_rust=True
        )
        r_vat0 = kor_2d_lokal_troghet(z, slag_vatten, geo, rt, ri, anvand_rust=True)

    m_rust.assert_not_called()
    for res in (r_sub, r_kal, r_bryt, r_f32, r_vat, r_vat0):
        assert res.anvande_rust is False
        assert res.volymfel_rel < 1e-4
