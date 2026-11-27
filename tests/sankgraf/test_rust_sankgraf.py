"""Tester för Rust-kärnan `rust/sankgraf_karna` och Python-bryggan `sankgraf.rust_brygga`.

Täcker:
1. Binärsökning, `FLODEN_SANKGRAF_RUST=0|1|auto`, `FLODEN_SANKGRAF_RUST_MIN_CELLER`,
   felhantering (saknad binär med tvingat läge, icke-noll returkod, timeout, korrupt/trunkerad
   binärfil) och garanterad tempkatalogstädning i `finally`.
2. Bitidentisk paritet mellan `sankgraf_karna` och Python-referensen i `sankgraf.hierarki`
   på kantfall, platåer, sluttningar, NaN-hål, smala rännor, dubbla sadlar, 2D/3D-brytlinjer,
   öppningar, slutsänkor, A1–A9 samt 500x500 kuperad och platårik terräng.
   I CI (`CI=true` eller `FLODEN_KRAV_RUST_SANKGRAF=1`) krävs binären så paritetstesterna
   aldrig tyst hoppas över.
"""

from __future__ import annotations

import math
import os
import stat
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path
from typing import Any
from unittest import mock

import numpy as np
import pytest
from numpy.typing import NDArray
from scipy.ndimage import gaussian_filter

from sankgraf import rust_brygga
from sankgraf.fyllning import statisk_fyllning
from sankgraf.hierarki import (
    _applicera_oppningar,
    _berakna_avrinningsomraden,
    _berakna_kantbarriarer,
    _bygg_granspar,
    bygg_sankgraf,
)
from sankgraf.rust_brygga import (
    RUST_MIN_CELLER_STANDARD,
    SankGrafRustFel,
    hitta_sankgraf_karna,
    kor_avrinningsomraden_rust,
    kor_granspar_rust,
    kor_sankgraf_karna_rust,
    rust_lage,
    rust_min_celler,
    valj_sankgraf_karna,
)
from sankgraf.typer import (
    MARKSLAG_BYGGNAD,
    SankGraf,
    las_brytlinjer_fran_gpkg,
    las_oppningar_fran_gpkg,
)
from testsvit.k1_io import BrytlinjePost, GeoTransformInfo, OppningPost, las_geotiff

REPO_ROT = Path(__file__).resolve().parents[2]
TESTSVIT_ROT = REPO_ROT / "testsvit"


def _geo(hojd: int, bredd: int, dx: float = 0.5) -> GeoTransformInfo:
    return GeoTransformInfo(
        bredd=bredd,
        hojd=hojd,
        x_min=150000.0,
        y_max=6580000.0,
        pixelstorlek_x=dx,
        pixelstorlek_y=dx,
        epsg=3011,
        dtype="float32",
    )


def _gor_korbar(fil: Path) -> Path:
    fil.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fil.chmod(fil.stat().st_mode | stat.S_IXUSR)
    return fil


def _krav_eller_skip_rust_binar() -> Path:
    krav = (
        os.environ.get("FLODEN_KRAV_RUST_SANKGRAF", "").strip().lower() in ("1", "true", "ja")
        or os.environ.get("CI", "").strip().lower() == "true"
    )
    try:
        return hitta_sankgraf_karna()
    except SankGrafRustFel:
        if krav:
            pytest.fail(
                "sankgraf_karna saknas trots CI / FLODEN_KRAV_RUST_SANKGRAF=1. "
                "Paritetstesterna får inte hoppas över tyst."
            )
        pytest.skip("sankgraf_karna är inte byggd lokalt")


def _jamfor_sankgraf_exakt(py_graf: SankGraf, rs_graf: SankGraf) -> None:
    np.testing.assert_array_equal(py_graf.cell_till_nod, rs_graf.cell_till_nod)
    assert py_graf.cell_till_nod.dtype == np.int64
    assert rs_graf.cell_till_nod.dtype == np.int64
    assert sorted(py_graf.noder.keys()) == sorted(rs_graf.noder.keys())
    assert py_graf.ut_lov_id == rs_graf.ut_lov_id
    assert py_graf.overfall_celler == rs_graf.overfall_celler
    assert len(py_graf.flodesvagar) == len(rs_graf.flodesvagar)
    for fv_p, fv_r in zip(py_graf.flodesvagar, rs_graf.flodesvagar, strict=True):
        assert fv_p == fv_r
    for nid, n_p in py_graf.noder.items():
        n_r = rs_graf.noder[nid]
        assert n_p == n_r


def _jamfor_steg_bitidentiskt(
    z: NDArray[np.float32] | NDArray[np.float64],
    aktiv: NDArray[np.bool_],
    kantbarriarer: dict[tuple[int, int], float],
    binar: Path,
) -> None:
    f_py, c_py, l_py = _berakna_avrinningsomraden(z, aktiv, kantbarriarer)  # type: ignore[arg-type]
    f_rs, c_rs, l_rs = kor_avrinningsomraden_rust(z, aktiv, kantbarriarer, binar=binar)
    assert l_rs == l_py
    assert f_rs.dtype == np.int32
    assert c_rs.dtype == np.int32
    np.testing.assert_array_equal(f_rs, f_py)
    np.testing.assert_array_equal(c_rs, c_py)

    gp_py = _bygg_granspar(c_py, aktiv, z, kantbarriarer)  # type: ignore[arg-type]
    gp_rs = kor_granspar_rust(c_py, aktiv, z, kantbarriarer, binar=binar)
    for idx, (a, b) in enumerate(zip(gp_py, gp_rs, strict=True)):
        np.testing.assert_array_equal(b, a, err_msg=f"gränspar-array #{idx} skiljer sig")

    f_k, c_k, l_k, gp_k = kor_sankgraf_karna_rust(z, aktiv, kantbarriarer, binar=binar)
    assert l_k == l_py
    np.testing.assert_array_equal(f_k, f_py)
    np.testing.assert_array_equal(c_k, c_py)
    if l_py > 0:
        for idx, (a, b) in enumerate(zip(gp_py, gp_k, strict=True)):
            np.testing.assert_array_equal(b, a, err_msg=f"komplett gränspar #{idx} skiljer sig")
    else:
        assert gp_k[0].size == 0


# ---------------------------------------------------------------------------
# 1. Enhetstester för brygga, miljövariabler, felhantering och tempstädning
# ---------------------------------------------------------------------------


def test_hitta_sankgraf_karna_prioritetsordning(tmp_path: Path) -> None:
    b_arg = _gor_korbar(tmp_path / "arg_bin")
    b_env = _gor_korbar(tmp_path / "env_bin")
    b_sys = _gor_korbar(tmp_path / "sys_bin")
    b_repo = _gor_korbar(tmp_path / "repo_bin")
    b_path = _gor_korbar(tmp_path / "path_bin")

    with (
        mock.patch.dict(os.environ, {"FLODEN_SANKGRAF_KARNA": str(b_env)}),
        mock.patch.object(rust_brygga, "_SYSTEM_BINAR", b_sys),
        mock.patch.object(rust_brygga, "_REPO_BINAR", b_repo),
        mock.patch.object(rust_brygga.shutil, "which", return_value=str(b_path)),
    ):
        assert hitta_sankgraf_karna(b_arg) == b_arg
        assert hitta_sankgraf_karna() == b_env

    with (
        mock.patch.dict(os.environ, {"FLODEN_SANKGRAF_KARNA": ""}),
        mock.patch.object(rust_brygga, "_SYSTEM_BINAR", b_sys),
        mock.patch.object(rust_brygga, "_REPO_BINAR", b_repo),
        mock.patch.object(rust_brygga.shutil, "which", return_value=str(b_path)),
    ):
        assert hitta_sankgraf_karna() == b_sys

    with (
        mock.patch.dict(os.environ, {"FLODEN_SANKGRAF_KARNA": ""}),
        mock.patch.object(rust_brygga, "_SYSTEM_BINAR", tmp_path / "saknas_sys"),
        mock.patch.object(rust_brygga, "_REPO_BINAR", b_repo),
        mock.patch.object(rust_brygga.shutil, "which", return_value=str(b_path)),
    ):
        assert hitta_sankgraf_karna() == b_repo

    with (
        mock.patch.dict(os.environ, {"FLODEN_SANKGRAF_KARNA": ""}),
        mock.patch.object(rust_brygga, "_SYSTEM_BINAR", tmp_path / "saknas_sys"),
        mock.patch.object(rust_brygga, "_REPO_BINAR", tmp_path / "saknas_repo"),
        mock.patch.object(rust_brygga.shutil, "which", return_value=str(b_path)),
    ):
        assert hitta_sankgraf_karna() == b_path


def test_hitta_sankgraf_karna_saknas_ger_tydligt_fel() -> None:
    with (
        mock.patch.dict(os.environ, {"FLODEN_SANKGRAF_KARNA": ""}),
        mock.patch.object(rust_brygga, "_SYSTEM_BINAR", Path("/finns/inte/sys")),
        mock.patch.object(rust_brygga, "_REPO_BINAR", Path("/finns/inte/repo")),
        mock.patch.object(rust_brygga.shutil, "which", return_value=None),
        pytest.raises(SankGrafRustFel) as exc_info,
    ):
        hitta_sankgraf_karna("/finns/inte/arg")
    assert isinstance(exc_info.value, RuntimeError)
    assert "cargo build" in str(exc_info.value)
    assert "FLODEN_SANKGRAF_KARNA" in str(exc_info.value)


def test_miljovariabler_lage_och_troskel() -> None:
    for varde, forvantat in (
        ("", "auto"),
        ("auto", "auto"),
        ("AUTO", "auto"),
        ("1", "alltid"),
        ("pa", "alltid"),
        ("på", "alltid"),
        ("alltid", "alltid"),
        ("0", "av"),
        ("av", "av"),
        ("python", "av"),
    ):
        with mock.patch.dict(os.environ, {"FLODEN_SANKGRAF_RUST": varde}):
            assert rust_lage() == forvantat

    with (
        mock.patch.dict(os.environ, {"FLODEN_SANKGRAF_RUST": "kanske"}),
        pytest.raises(ValueError, match="FLODEN_SANKGRAF_RUST"),
    ):
        rust_lage()

    with mock.patch.dict(os.environ, {"FLODEN_SANKGRAF_RUST_MIN_CELLER": ""}):
        assert rust_min_celler() == RUST_MIN_CELLER_STANDARD
    with mock.patch.dict(os.environ, {"FLODEN_SANKGRAF_RUST_MIN_CELLER": "5000"}):
        assert rust_min_celler() == 5000
    with (
        mock.patch.dict(os.environ, {"FLODEN_SANKGRAF_RUST_MIN_CELLER": "-1"}),
        pytest.raises(ValueError),
    ):
        rust_min_celler()
    with (
        mock.patch.dict(os.environ, {"FLODEN_SANKGRAF_RUST_MIN_CELLER": "ej_tal"}),
        pytest.raises(ValueError),
    ):
        rust_min_celler()


def test_valj_sankgraf_karna_auto_tvingad_och_av(tmp_path: Path) -> None:
    binar = _gor_korbar(tmp_path / "sankgraf_karna")

    # auto under tröskel -> None trots att binären finns
    with (
        mock.patch.dict(
            os.environ,
            {
                "FLODEN_SANKGRAF_RUST": "auto",
                "FLODEN_SANKGRAF_RUST_MIN_CELLER": "100000",
                "FLODEN_SANKGRAF_KARNA": str(binar),
            },
        ),
    ):
        assert valj_sankgraf_karna(99_999) is None
        assert valj_sankgraf_karna(100_000) == binar

    # auto över tröskel utan binär -> mjuk fallback till Python (None)
    with (
        mock.patch.dict(os.environ, {"FLODEN_SANKGRAF_RUST": "auto", "FLODEN_SANKGRAF_KARNA": ""}),
        mock.patch.object(rust_brygga, "_SYSTEM_BINAR", tmp_path / "saknas1"),
        mock.patch.object(rust_brygga, "_REPO_BINAR", tmp_path / "saknas2"),
        mock.patch.object(rust_brygga.shutil, "which", return_value=None),
    ):
        assert valj_sankgraf_karna(500_000) is None

    # tvingad (=1 / pa / alltid) utan binär -> TYDLIGT fel (ingen tyst fallback!)
    for tvingad in ("1", "pa", "alltid"):
        with (
            mock.patch.dict(
                os.environ, {"FLODEN_SANKGRAF_RUST": tvingad, "FLODEN_SANKGRAF_KARNA": ""}
            ),
            mock.patch.object(rust_brygga, "_SYSTEM_BINAR", tmp_path / "saknas1"),
            mock.patch.object(rust_brygga, "_REPO_BINAR", tmp_path / "saknas2"),
            mock.patch.object(rust_brygga.shutil, "which", return_value=None),
            pytest.raises(RuntimeError),
        ):
            valj_sankgraf_karna(16)

    # 0 / av / python -> alltid None även vid stort rutnät och existerande binär
    for av in ("0", "av", "python"):
        with mock.patch.dict(
            os.environ, {"FLODEN_SANKGRAF_RUST": av, "FLODEN_SANKGRAF_KARNA": str(binar)}
        ):
            assert valj_sankgraf_karna(5_000_000) is None

    # Explicit anvand_rust-argument vinner över miljövariabeln
    with mock.patch.dict(
        os.environ, {"FLODEN_SANKGRAF_RUST": "0", "FLODEN_SANKGRAF_KARNA": str(binar)}
    ):
        assert valj_sankgraf_karna(16, anvand_rust=True) == binar
    with mock.patch.dict(
        os.environ, {"FLODEN_SANKGRAF_RUST": "1", "FLODEN_SANKGRAF_KARNA": str(binar)}
    ):
        assert valj_sankgraf_karna(500_000, anvand_rust=False) is None


def test_felhantering_och_tempstadning_i_finally(tmp_path: Path) -> None:
    binar = _gor_korbar(tmp_path / "sankgraf_karna")
    z = np.zeros((4, 4), dtype=np.float32)
    aktiv = np.ones((4, 4), dtype=np.bool_)

    skapade: list[Path] = []
    orig_mkdtemp = tempfile.mkdtemp

    def spara_mkdtemp(*args: Any, **kwargs: Any) -> str:
        d = orig_mkdtemp(*args, **kwargs)
        skapade.append(Path(d))
        return d

    # 1. Icke-noll returkod ger RuntimeError med stderr och städar tempkatalog
    with (
        mock.patch.object(rust_brygga.tempfile, "mkdtemp", side_effect=spara_mkdtemp),
        mock.patch.object(
            rust_brygga.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(["x"], 2, "", "simulerat fel i rust\n"),
        ),
        pytest.raises(RuntimeError, match="simulerat fel i rust"),
    ):
        kor_sankgraf_karna_rust(z, aktiv, {}, binar=binar)
    assert len(skapade) == 1
    assert not skapade[-1].exists()

    # 2. Timeout ger RuntimeError och städar tempkatalog
    with (
        mock.patch.object(rust_brygga.tempfile, "mkdtemp", side_effect=spara_mkdtemp),
        mock.patch.object(
            rust_brygga.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["x"], 0.1),
        ),
        pytest.raises(RuntimeError, match="tidsgränsen"),
    ):
        kor_sankgraf_karna_rust(z, aktiv, {}, binar=binar, timeout_s=0.1)
    assert len(skapade) == 2
    assert not skapade[-1].exists()

    # 3. KeyboardInterrupt propagerar men städar tempkatalog i finally
    with (
        mock.patch.object(rust_brygga.tempfile, "mkdtemp", side_effect=spara_mkdtemp),
        mock.patch.object(rust_brygga.subprocess, "run", side_effect=KeyboardInterrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        kor_sankgraf_karna_rust(z, aktiv, {}, binar=binar)
    assert len(skapade) == 3
    assert not skapade[-1].exists()


def test_korrupt_och_trunkerad_utdata_avvisas(tmp_path: Path) -> None:
    ut = tmp_path / "ut.flsg"
    # Saknad fil
    with pytest.raises(SankGrafRustFel, match="ingen utdatafil"):
        rust_brygga._las_utdata(ut, 1, 2, 2)

    # Trunkerad header
    ut.write_bytes(b"FLSG_UT\x00kort")
    with pytest.raises(SankGrafRustFel, match="Trunkerad"):
        rust_brygga._las_utdata(ut, 1, 2, 2)

    # Giltig AVRINNING-fil för 2x2 (4 celler -> 32 payload-byte)
    flow = np.array([-1, -1, -1, -1], dtype=np.dtype("<i4"))
    ctn = np.array([-1, -1, -1, -1], dtype=np.dtype("<i4"))
    payload = flow.tobytes() + ctn.tobytes()
    crc = zlib.crc32(payload) & 0xFFFFFFFF

    def _packa(
        magic: bytes = b"FLSG_UT\x00",
        version: int = 1,
        lage: int = 1,
        h: int = 2,
        b: int = 2,
        lov: int = 0,
        c: int = crc,
        plen: int = len(payload),
    ) -> bytes:
        return struct.pack("<8sIIIIIIQQQQ", magic, version, lage, h, b, lov, c, 0, 0, plen, 0)

    # Korrupt CRC (bitfel i payload)
    trasig_payload = bytearray(payload)
    trasig_payload[0] ^= 0x01
    ut.write_bytes(_packa() + bytes(trasig_payload))
    with pytest.raises(SankGrafRustFel, match="CRC-32"):
        rust_brygga._las_utdata(ut, 1, 2, 2)

    # Trunkerad payload
    ut.write_bytes(_packa() + payload[:-4])
    with pytest.raises(SankGrafRustFel, match="trunkerad"):
        rust_brygga._las_utdata(ut, 1, 2, 2)

    # Fel magisk signatur
    ut.write_bytes(_packa(magic=b"TRASIG!!\x00") + payload)
    with pytest.raises(SankGrafRustFel, match="magisk"):
        rust_brygga._las_utdata(ut, 1, 2, 2)

    # Fel dimensioner
    ut.write_bytes(_packa(h=3, b=2, plen=48) + payload + b"\x00" * 16)
    with pytest.raises(SankGrafRustFel, match="Dimensioner"):
        rust_brygga._las_utdata(ut, 1, 2, 2)


def test_bygg_sankgraf_delegerar_en_gang_med_mock(tmp_path: Path) -> None:
    binar = _gor_korbar(tmp_path / "sankgraf_karna")
    z = np.full((10, 10), 12.0, dtype=np.float32)
    z[3:7, 3:7] = np.float32(10.0)
    osak = np.full((10, 10), 0.02, dtype=np.float32)
    slag = np.full((10, 10), 1, dtype=np.uint8)
    geo = _geo(10, 10)

    py_graf = bygg_sankgraf(z, osak, slag, geo, anvand_rust=False)
    anrop: list[Path] = []

    def falsk_rust(
        eff_z: NDArray[np.float32],
        aktiv: NDArray[np.bool_],
        barriarer: dict[tuple[int, int], float],
        *,
        binar: Path,
    ) -> tuple[NDArray[np.int32], NDArray[np.int32], int, rust_brygga.GransparArrayer]:
        anrop.append(binar)
        f, c, lov = _berakna_avrinningsomraden(eff_z, aktiv, barriarer)
        gp = _bygg_granspar(c, aktiv, eff_z, barriarer)
        return (
            np.asarray(f, dtype=np.int32),
            np.asarray(c, dtype=np.int32),
            lov,
            (
                gp[0],
                np.asarray(gp[1], dtype=np.int32),
                np.asarray(gp[2], dtype=np.int32),
                gp[3],
                gp[4],
                np.asarray(gp[5], dtype=np.int32),
                np.asarray(gp[6], dtype=np.int32),
                np.asarray(gp[7], dtype=np.int32),
                np.asarray(gp[8], dtype=np.int32),
            ),
        )

    with (
        mock.patch("sankgraf.hierarki.valj_sankgraf_karna", return_value=binar),
        mock.patch("sankgraf.hierarki.kor_sankgraf_karna_rust", side_effect=falsk_rust),
    ):
        rs_graf = bygg_sankgraf(z, osak, slag, geo, anvand_rust=True)

    assert anrop == [binar]
    _jamfor_sankgraf_exakt(py_graf, rs_graf)


# ---------------------------------------------------------------------------
# 2. Skarpa paritetstester Rust vs Python (krävs i CI, skippas bara lokalt utan binär)
# ---------------------------------------------------------------------------


def test_rust_paritet_kantfall_och_specialfall() -> None:
    binar = _krav_eller_skip_rust_binar()

    # 1x1, 1x7, 7x1, 2x2
    for hojd, bredd in ((1, 1), (1, 7), (7, 1), (2, 2)):
        z = np.arange(hojd * bredd, dtype=np.float32).reshape((hojd, bredd)) + np.float32(10.0)
        aktiv = np.ones((hojd, bredd), dtype=np.bool_)
        _jamfor_steg_bitidentiskt(z, aktiv, {}, binar)

    # 1x7 med alternerande toppar/dalar (tie-breaking V vs Ö)
    z_alt = np.array([[10.0, 11.0, 10.0, 11.0, 10.0, 12.0, 10.0]], dtype=np.float32)
    _jamfor_steg_bitidentiskt(z_alt, np.ones_like(z_alt, dtype=np.bool_), {}, binar)

    # Helt platt öppen vs helt platt omgiven av byggnad (instängd lövsänka)
    z_platt = np.full((12, 12), 10.0, dtype=np.float32)
    _jamfor_steg_bitidentiskt(z_platt, np.ones((12, 12), dtype=np.bool_), {}, binar)
    aktiv_sluten = np.zeros((12, 12), dtype=np.bool_)
    aktiv_sluten[2:10, 2:10] = True
    _jamfor_steg_bitidentiskt(z_platt, aktiv_sluten, {}, binar)

    # Helt NaN / helt inaktiv
    z_nan = np.full((6, 6), np.nan, dtype=np.float32)
    _jamfor_steg_bitidentiskt(z_nan, np.zeros((6, 6), dtype=np.bool_), {}, binar)

    # NaN-hål mitt i sluttande terräng + byggnadsbarriärer
    ii, jj = np.meshgrid(np.arange(16), np.arange(16), indexing="ij")
    z_hal = (10.0 + 0.1 * np.hypot(ii - 8.0, jj - 8.0)).astype(np.float32)
    z_hal[5:7, 7:10] = np.nan
    aktiv_hal = np.isfinite(z_hal)
    aktiv_hal[10, 3:13] = False
    _jamfor_steg_bitidentiskt(z_hal, aktiv_hal, {}, binar)

    # Float64-indata + sub-1e-9 sadeltolerans i gränspar
    z_f64 = np.array([[10.0, 10.0], [10.0, 10.0]], dtype=np.float64)
    catch_f64 = np.array([[0, 1], [0, 1]], dtype=np.int32)
    aktiv_f64 = np.ones((2, 2), dtype=np.bool_)
    barriarer_tie = {(0, 1): 10.5 + 5e-10, (2, 3): 10.5}
    gp_py = _bygg_granspar(catch_f64, aktiv_f64, z_f64, barriarer_tie)  # type: ignore[arg-type]
    gp_rs = kor_granspar_rust(catch_f64, aktiv_f64, z_f64, barriarer_tie, binar=binar)
    for a, b in zip(gp_py, gp_rs, strict=True):
        np.testing.assert_array_equal(b, a)


def test_rust_paritet_komplex_terrang_platta_och_barriarer() -> None:
    binar = _krav_eller_skip_rust_binar()
    hojd, bredd = 52, 64
    dx = 0.5
    geo = _geo(hojd, bredd, dx=dx)

    # Terräng med multipla sänkor, breda platta sadlar, smal 1-cells ränna,
    # diagonal barriär, NaN-hål, instängd innergård (slutsänka), 2D/3D-brytlinjer och öppning
    z = np.full((hojd, bredd), 12.0, dtype=np.float32)
    osak = np.full((hojd, bredd), 0.03, dtype=np.float32)
    slag = np.full((hojd, bredd), 1, dtype=np.uint8)

    # Sänka 1 och 2 med likahöjds-sadel (dubbla/flera sadelkanter)
    z[8:20, 6:18] = np.float32(10.0)
    z[8:20, 22:34] = np.float32(10.2)
    z[11:17, 18:22] = np.float32(10.80)
    # Sänka 3 med smal 1-cellsbred ränna som dränerar del av platå till sänka 3
    z[26:38, 12:30] = np.float32(10.4)
    z[20:26, 15:19] = np.float32(11.10)
    z[38:44, 20] = np.float32(10.9)  # 1-cellsbred ränna
    z[44:49, 15:26] = np.float32(10.9)  # platå kopplad via smal ränna
    # Utlopp mot västra randen
    z[12:16, 0:6] = np.float32(11.40)

    # Diagonal 1-cellsvägg av byggnader (4-grannskap ska inte läcka diagonalt)
    for k in range(8):
        slag[28 + k, 36 + k] = MARKSLAG_BYGGNAD
        z[28 + k, 36 + k] = np.float32(15.0)

    # NaN-hål i aktiv mark vid randen (inte omslutet så inget InstangtOmradeFel)
    z[0:3, 56:60] = np.nan

    # Instängd innergård (slutsänka) omgiven av byggnad (takhöjd 15.0)
    slag[10:26, 42:56] = MARKSLAG_BYGGNAD
    z[10:26, 42:56] = np.float32(15.0)
    slag[13:23, 45:53] = 1
    z[13:23, 45:53] = np.float32(10.5)
    z[16:19, 47:50] = np.float32(10.1)

    b2d = BrytlinjePost(
        typ="kantsten",
        hojd_m=0.25,
        koordinater=(
            (geo.x_min + 20.0 * dx, geo.y_max - 4.0 * dx),
            (geo.x_min + 24.0 * dx, geo.y_max - 36.0 * dx),
        ),
    )
    b3d = BrytlinjePost(
        typ="mur",
        hojd_m=0.0,
        koordinater=(
            (geo.x_min + 35.0 * dx, geo.y_max - 4.0 * dx),
            (geo.x_min + 35.0 * dx, geo.y_max - 30.0 * dx),
        ),
        z_kron_rh2000=(11.35, 11.55),
    )
    opp = OppningPost(
        typ="trumma",
        bredd_m=1.0,
        botten_rh2000=10.35,
        kalla="syntetisk",
        koordinater=(
            (geo.x_min + 12.0 * dx, geo.y_max - 20.0 * dx),
            (geo.x_min + 12.0 * dx, geo.y_max - 27.0 * dx),
        ),
    )

    eff_z, eff_slag = _applicera_oppningar(z, slag, geo, (opp,))
    aktiv = np.asarray((eff_slag != MARKSLAG_BYGGNAD) & np.isfinite(eff_z), dtype=np.bool_)
    barriarer = _berakna_kantbarriarer(
        np.asarray(eff_z, dtype=np.float64).ravel(), aktiv.ravel(), geo, (b2d, b3d)
    )
    _jamfor_steg_bitidentiskt(eff_z, aktiv, barriarer, binar)

    py_graf = bygg_sankgraf(
        z, osak, slag, geo, brytlinjer=(b2d, b3d), oppningar=(opp,), anvand_rust=False
    )
    rs_graf = bygg_sankgraf(
        z, osak, slag, geo, brytlinjer=(b2d, b3d), oppningar=(opp,), anvand_rust=True
    )
    _jamfor_sankgraf_exakt(py_graf, rs_graf)
    assert len(rs_graf.slutsankor()) == 1

    fyll_py = statisk_fyllning(py_graf, 0.25)
    fyll_rs = statisk_fyllning(rs_graf, 0.25)
    np.testing.assert_array_equal(fyll_rs.djup_m, fyll_py.djup_m)
    assert fyll_rs.volymfel_rel < 1e-9


def test_rust_paritet_500x500_kuperad_och_platarik() -> None:
    binar = _krav_eller_skip_rust_binar()
    n = 500
    rng = np.random.default_rng(42)
    y = np.linspace(0.0, 12.0 * math.pi, n, dtype=np.float32)
    x = np.linspace(0.0, 12.0 * math.pi, n, dtype=np.float32)
    yy, xx = np.meshgrid(y, x, indexing="ij")
    brus = gaussian_filter(rng.normal(0.0, 1.0, size=(n, n)), sigma=4.0).astype(np.float32)
    z_kup = np.round(
        10.0
        + 0.5 * np.sin(xx) * np.cos(yy)
        + 0.25 * np.sin(2.3 * xx + 0.7) * np.sin(1.9 * yy)
        + 1.5 * brus,
        2,
    ).astype(np.float32)
    z_platt = np.round(10.0 + 0.4 * np.sin(xx * 0.5) * np.cos(yy * 0.5), 1).astype(np.float32)
    aktiv = np.ones((n, n), dtype=np.bool_)

    _jamfor_steg_bitidentiskt(z_kup, aktiv, {}, binar)
    _jamfor_steg_bitidentiskt(z_platt, aktiv, {}, binar)


def test_rust_paritet_a_fall_testsvit() -> None:
    _krav_eller_skip_rust_binar()
    a_mappar = sorted(
        p
        for p in TESTSVIT_ROT.iterdir()
        if p.is_dir() and p.name.startswith("A") and (p / "indata" / "mark.tif").is_file()
    )
    assert len(a_mappar) >= 13
    for mapp in a_mappar:
        indata = mapp / "indata"
        mark, geo = las_geotiff(indata / "mark.tif")
        osak, _ = las_geotiff(indata / "osakerhet.tif")
        slag, _ = las_geotiff(indata / "markslag.tif")
        mark_f32 = np.asarray(mark, dtype=np.float32)
        osak_f32 = np.asarray(osak, dtype=np.float32)
        slag_u8 = np.asarray(slag, dtype=np.uint8)
        bryt_fil = indata / "brytlinjer.gpkg"
        opp_fil = indata / "oppningar.gpkg"
        bryt = las_brytlinjer_fran_gpkg(bryt_fil) if bryt_fil.is_file() else ()
        opp = las_oppningar_fran_gpkg(opp_fil) if opp_fil.is_file() else ()

        py_graf = bygg_sankgraf(
            mark_f32, osak_f32, slag_u8, geo, brytlinjer=bryt, oppningar=opp, anvand_rust=False
        )
        rs_graf = bygg_sankgraf(
            mark_f32, osak_f32, slag_u8, geo, brytlinjer=bryt, oppningar=opp, anvand_rust=True
        )
        _jamfor_sankgraf_exakt(py_graf, rs_graf)

        fyll_rs = statisk_fyllning(rs_graf, 0.15)
        assert fyll_rs.volymfel_rel < 1e-6, f"{mapp.name}: volymfel {fyll_rs.volymfel_rel}"


def test_avrinningsnat_utdata_validering_crc_och_trunkering(tmp_path: Path) -> None:
    """`_las_avrinningsnat_utdata` avvisar korrupt CRC, fel läge och trunkerad fil."""
    flow = np.array([-1, -1, -1, -1], dtype=np.dtype("<i4"))
    ordn = np.array([0, 1, 2, 3], dtype=np.dtype("<i4"))
    tillr = np.array([1.0, 1.0, 1.0, 1.0], dtype=np.dtype("<f8"))
    payload = flow.tobytes() + ordn.tobytes() + tillr.tobytes()
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    hdr = rust_brygga._HEADER_UT_STRUCT.pack(  # noqa: SLF001
        rust_brygga._MAGIC_UT,  # noqa: SLF001
        rust_brygga._FORMAT_VERSION,  # noqa: SLF001
        rust_brygga._LAGE_AVRINNINGSNAT_TILLRINNING,  # noqa: SLF001
        2,
        2,
        0,
        crc,
        4,
        0,
        len(payload),
        0,
    )
    ok_fil = tmp_path / "ok.flsg"
    ok_fil.write_bytes(hdr + payload)
    f_ut, o_ut, t_ut = rust_brygga._las_avrinningsnat_utdata(  # noqa: SLF001
        ok_fil, rust_brygga._LAGE_AVRINNINGSNAT_TILLRINNING, 2, 2  # noqa: SLF001
    )
    np.testing.assert_array_equal(f_ut, flow)
    np.testing.assert_array_equal(o_ut, ordn)
    assert t_ut is not None
    np.testing.assert_array_equal(t_ut, tillr)

    # Korrupt byte i payload -> CRC-fel
    bad_crc = tmp_path / "bad_crc.flsg"
    raw = bytearray(hdr + payload)
    raw[-1] ^= 0x01
    bad_crc.write_bytes(bytes(raw))
    with pytest.raises(SankGrafRustFel, match="CRC-32"):
        rust_brygga._las_avrinningsnat_utdata(  # noqa: SLF001
            bad_crc, rust_brygga._LAGE_AVRINNINGSNAT_TILLRINNING, 2, 2  # noqa: SLF001
        )

    # Fel läge i header
    with pytest.raises(SankGrafRustFel, match="Fel körläge"):
        rust_brygga._las_avrinningsnat_utdata(  # noqa: SLF001
            ok_fil, rust_brygga._LAGE_AVRINNINGSNAT, 2, 2  # noqa: SLF001
        )


def test_rust_paritet_avrinningsnat_och_tillrinning() -> None:
    """Bitidentisk paritet mellan Rust (`Lage::Avrinningsnat*`) och Python `avrinningsnat.py`."""
    binar = _krav_eller_skip_rust_binar()
    from sankgraf.avrinningsnat import (
        _fyll_och_riktning_python,
        avrinningsnat_ur_falt,
        tillrinning,
    )
    from sankgraf.rust_brygga import (
        kor_avrinningsnat_rust,
        kor_avrinningsnat_tillrinning_rust,
    )

    rng = np.random.default_rng(123)
    # 1. Kuperad + platå + byggnader + slutet område (inre gård) + takvatten
    h, b = 120, 100
    yy, xx = np.mgrid[0:h, 0:b].astype(np.float64)
    z = (
        10.0
        + 0.04 * np.abs(xx - b / 2.0)
        + 0.02 * (h - yy)
        + 0.35 * np.sin(xx / 7.0) * np.cos(yy / 9.0)
        + 0.15 * gaussian_filter(rng.normal(0.0, 1.0, size=(h, b)), sigma=2.5)
    )
    # Platt platå med svacka
    z[20:40, 20:40] = 10.5
    z[28:33, 28:33] = 9.8
    aktiv = np.ones((h, b), dtype=np.bool_)
    # Byggnadsmur runt sluten gård (60..75, 55..70)
    aktiv[60:76, 55:71] = False
    aktiv[63:73, 58:68] = True
    z[67, 62] = 7.2  # lägsta cell i sluten gård
    extra = np.zeros((h, b), dtype=np.float64)
    extra[59, 60] = 350.0
    extra[40, 50] = 500.0

    flow_py, ordn_py = _fyll_och_riktning_python(z, aktiv)
    flow_rs, ordn_rs = kor_avrinningsnat_rust(z, aktiv, binar=binar)
    np.testing.assert_array_equal(flow_rs, flow_py)
    np.testing.assert_array_equal(ordn_rs, ordn_py)

    egen = np.where(aktiv, 1.0, 0.0) + np.where(aktiv, extra, 0.0)
    a_py = tillrinning(flow_py, ordn_py, egen)
    flow_rs2, ordn_rs2, a_rs = kor_avrinningsnat_tillrinning_rust(z, aktiv, egen, binar=binar)
    np.testing.assert_array_equal(flow_rs2, flow_py)
    np.testing.assert_array_equal(ordn_rs2, ordn_py)
    np.testing.assert_allclose(a_rs, a_py, rtol=0.0, atol=1e-12)

    str_py = avrinningsnat_ur_falt(
        z,
        aktiv,
        extra,
        x_min=150000.0,
        y_max=6580000.0,
        pixel_m=1.0,
        min_yta_m2=150.0,
        anvand_rust=False,
    )
    str_rs = avrinningsnat_ur_falt(
        z,
        aktiv,
        extra,
        x_min=150000.0,
        y_max=6580000.0,
        pixel_m=1.0,
        min_yta_m2=150.0,
        anvand_rust=True,
    )
    assert str_rs == str_py
    assert len(str_rs) > 0
