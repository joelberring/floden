# SPDX-License-Identifier: MIT
"""Tester för `luft.vindbibliotek`: KL1-lagring, väderserier, fördelning och statistik."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from luft.geometri import voxelisera
from luft.typer import Inflode, LuftFel, Vindfalt
from luft.vindbibliotek import (
    BIBLIOTEK_JSON,
    Haltstatistik,
    Vaderserie,
    Vindbibliotek,
    fordela,
    ladda_vindfalt,
    spara_vindfalt,
)

U_REF = 5.0


def _vindfalt(theta: float, seed: int, u_ref: float = U_REF) -> Vindfalt:
    raster = np.zeros((5, 7))
    raster[1:3, 2:4] = 6.0
    grid = voxelisera(
        raster,
        2.0,
        16.0,
        raster_dx_m=2.0,
        raster_origo_x_m=150_000.0,
        raster_origo_y_m=6_580_000.0,
        vegetation_hojd_m=np.where(raster == 0, 3.0, 0.0),
        lad_m2_m3=0.7,
        vindriktning_grad=theta,
        buffert_uppstroms_m=4.0,
        buffert_nedstroms_m=8.0,
        buffert_sidor_m=2.0,
    )
    rng = np.random.default_rng(seed)
    u = rng.normal(size=(3, *grid.form)).astype(np.float32)
    u[:, grid.fast] = 0.0
    nu_t = rng.uniform(0, 0.3, size=grid.form)
    return Vindfalt(
        grid=grid,
        inflode=Inflode(u_ref_m_s=u_ref, z0_m=0.3),
        u_m_s=u,
        nu_t_m2_s=nu_t,
        medelperiod_s=1234.5,
        metadata={"lbm_parametrar": {"u_lattice": 0.08}, "notering": "handbyggt testfält"},
    )


def _lika(a: Vindfalt, b: Vindfalt) -> None:
    assert a.u_m_s.dtype == b.u_m_s.dtype and np.array_equal(a.u_m_s, b.u_m_s)
    assert np.array_equal(a.nu_t_m2_s, b.nu_t_m2_s)
    assert np.array_equal(a.grid.fast, b.grid.fast)
    assert np.array_equal(a.grid.lad_m2_m3, b.grid.lad_m2_m3)
    for attr in (
        "dx_m",
        "vindriktning_grad",
        "origo_x_m",
        "origo_y_m",
        "origo_z_m",
        "rotation_centrum_x_m",
        "rotation_centrum_y_m",
    ):
        assert getattr(a.grid, attr) == getattr(b.grid, attr)
    assert a.inflode == b.inflode
    assert a.medelperiod_s == b.medelperiod_s
    assert a.metadata == b.metadata


def test_vindfalt_rundresa_bitexakt(tmp_path: Path) -> None:
    f = _vindfalt(33.0, 1)
    fil = spara_vindfalt(f, tmp_path / "vind.npz")
    _lika(f, ladda_vindfalt(fil))


def test_bibliotek_rundresa_och_validering(tmp_path: Path) -> None:
    falt = tuple(_vindfalt(t, s) for s, t in enumerate((270.0, 0.0, 90.0, 180.0)))
    bib = Vindbibliotek(falt=falt, kallgeometri_sha256="abc")
    assert bib.riktningar_grad == (0.0, 90.0, 180.0, 270.0)
    bib.spara(tmp_path / "bib")
    index = json.loads((tmp_path / "bib" / BIBLIOTEK_JSON).read_text(encoding="utf-8"))
    assert index["kontrakt"] == "KL1/v1" and index["u_ref_m_s"] == U_REF
    assert len(index["geometri_sha256"]) == 64
    laddat = Vindbibliotek.ladda(tmp_path / "bib")
    assert laddat.riktningar_grad == bib.riktningar_grad
    assert laddat.kallgeometri_sha256 == "abc"
    for a, b in zip(bib.falt, laddat.falt, strict=True):
        _lika(a, b)

    # fel kontraktsversion
    index["kontrakt"] = "KL1/v2"
    (tmp_path / "bib" / BIBLIOTEK_JSON).write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(LuftFel, match="kontrakt"):
        Vindbibliotek.ladda(tmp_path / "bib")
    # ändrad fil
    index["kontrakt"] = "KL1/v1"
    (tmp_path / "bib" / BIBLIOTEK_JSON).write_text(json.dumps(index), encoding="utf-8")
    spara_vindfalt(_vindfalt(0.0, 99), tmp_path / "bib" / index["filer"][0]["fil"])
    with pytest.raises(LuftFel, match="sha256"):
        Vindbibliotek.ladda(tmp_path / "bib")


def test_bibliotek_kraver_samma_u_ref() -> None:
    with pytest.raises(LuftFel, match="u_ref"):
        Vindbibliotek(falt=(_vindfalt(0.0, 1), _vindfalt(90.0, 2, u_ref=3.0)))
    with pytest.raises(LuftFel):
        Vindbibliotek(falt=(_vindfalt(0.0, 1), _vindfalt(360.0, 2)))


def test_vaderserie_csv(tmp_path: Path) -> None:
    fil = tmp_path / "vader.csv"
    fil.write_text(
        "tid,vindriktning_grad,vindhastighet_m_s\n"
        "2025-01-01T00:00,270,4.0\n"
        "2025-01-01T01:00,,3.0\n"
        "2025-01-01T02:00,10,0.2\n"
        "2025-01-01T03:00,355,nan\n",
        encoding="utf-8",
    )
    v = Vaderserie.las_csv(fil)
    assert v.tid[0] == "2025-01-01T00:00"
    assert v.giltig.tolist() == [True, False, True, False]
    f = fordela(v, [0.0, 90.0, 180.0, 270.0], U_REF, u_min_m_s=0.5)
    assert f.antal_modellerade == 1 and f.antal_svag_vind == 1 and f.antal_ogiltiga == 2
    assert f.andel_svag_vind == 0.5
    assert f.sektor_index[0, 0] == 3 and f.skalfaktor[0] == U_REF / 4.0
    fil.write_text("tid,riktning,u\n1,2,3\n", encoding="utf-8")
    with pytest.raises(LuftFel, match="saknar kolumnen"):
        Vaderserie.las_csv(fil)


def test_fordela_narmast_och_interpolation() -> None:
    v = Vaderserie.skapa([0.0, 44.0, 46.0, 350.0, 315.0, 100.0], [5.0] * 6)
    rikt = [0.0, 90.0, 180.0, 270.0]
    f = fordela(v, rikt, U_REF)
    # 315° ligger lika långt från 270° och 0°: den först listade (0°) väljs
    assert f.sektor_index[:, 0].tolist() == [0, 0, 1, 0, 0, 1]
    fi = fordela(v, rikt, U_REF, interpolera=True)
    # 44°: mellan 0 (vikt 46/90) och 90 (44/90)
    assert fi.sektor_index[1].tolist() == [0, 1]
    assert fi.vikt[1, 0] == pytest.approx(46 / 90, rel=1e-15)
    # 350°: mellan 270 och 0 (över nollan)
    assert fi.sektor_index[3].tolist() == [3, 0]
    assert fi.vikt[3, 1] == pytest.approx(80 / 90, rel=1e-15)
    # exakt på en sektor: hela vikten där
    assert fi.vikt[0, 0] == 1.0 and fi.sektor_index[0, 0] == 0
    assert np.allclose(fi.vikt.sum(axis=1), 1.0)


# ---------------------------------------------------------------------------
# Statistik mot bruteforce
# ---------------------------------------------------------------------------


def _syntetiskt(n_timmar: int, seed: int) -> tuple[Vaderserie, list[np.ndarray], list[float]]:
    rng = np.random.default_rng(seed)
    rikt = [0.0, 30.0, 60.0, 90.0, 120.0, 150.0, 180.0, 210.0, 240.0, 270.0, 300.0, 330.0]
    r = rng.uniform(0, 360, n_timmar)
    u = rng.weibull(2.0, n_timmar) * 4.0  # en del under 0,5 m/s
    giltig = rng.uniform(size=n_timmar) > 0.02
    v = Vaderserie.skapa(r, u, giltig=giltig)
    halter = [rng.gamma(2.0, 1e-6, size=(9, 11)) for _ in rikt]
    for h in halter:
        h[0, 0] = np.nan  # t.ex. fast cell
    halter[5][4, 7] = np.nan  # NaN i bara en riktning
    halter[3][2, 2] = 0.0
    return v, halter, rikt


def _bruteforce(fd, halter: list[np.ndarray]) -> np.ndarray:
    """Alla modellerade timmar explicit: (H, *form)."""
    c = np.stack(halter)
    m = fd.modellerad
    idx = fd.sektor_index[m]
    w = fd.vikt[m]
    f = fd.skalfaktor[m]
    timmar = []
    for h in range(f.size):
        timmar.append(f[h] * (w[h, 0] * c[idx[h, 0]] + w[h, 1] * c[idx[h, 1]]))
    return np.stack(timmar)


def _rel(a: np.ndarray, b: np.ndarray) -> float:
    assert np.array_equal(np.isnan(a), np.isnan(b))
    ok = ~np.isnan(a)
    return float(np.max(np.abs(a[ok] - b[ok]) / np.maximum(np.abs(b[ok]), 1e-300)))


@pytest.mark.parametrize("interpolera", [False, True])
def test_statistik_mot_bruteforce(interpolera: bool) -> None:
    v, halter, rikt = _syntetiskt(3000, 7)
    fd = fordela(v, rikt, U_REF, u_min_m_s=0.5, interpolera=interpolera)
    assert fd.antal_svag_vind > 0 and fd.antal_ogiltiga > 0
    assert fd.antal_modellerade + fd.antal_svag_vind + fd.antal_ogiltiga == 3000
    stat = Haltstatistik(halter, fd, block_bytes=8 * 3000 * 7)  # 7 celler per block
    alla = _bruteforce(fd, halter)

    medel = stat.arsmedel()
    assert _rel(medel, np.mean(alla, axis=0)) < 1e-12

    for p in (50.0, 90.0, 98.0, 99.8, 100.0):
        assert _rel(stat.percentil(p), np.percentile(alla, p, axis=0)) < 1e-12
    flera = stat.percentil([10.0, 98.0])
    assert flera.shape == (2, 9, 11)

    for grans in (1e-6, 5e-6, 2e-5, float(np.nanmedian(alla))):
        antal = stat.antal_timmar_over(grans)
        brute = np.sum(alla > grans, axis=0).astype(float)
        brute[np.isnan(alla).any(axis=0)] = np.nan
        assert np.array_equal(antal, brute, equal_nan=True), grans

    assert stat.andel_svag_vind == fd.antal_svag_vind / (3000 - fd.antal_ogiltiga)


def test_antal_over_exakt_pa_granslinjen() -> None:
    """Gräns exakt lika med en timhalt: strikt '>' ska räknas som bruteforce."""
    v = Vaderserie.skapa([0.0, 0.0, 0.0, 90.0], [1.0, 2.0, 4.0, 5.0])
    halter = [np.array([[2.0e-6, 0.0]]), np.array([[1.0e-6, 3.0e-6]])]
    fd = fordela(v, [0.0, 90.0], U_REF)
    stat = Haltstatistik(halter, fd)
    alla = _bruteforce(fd, halter)
    for grans in np.unique(alla):
        assert np.array_equal(stat.antal_timmar_over(float(grans)), np.sum(alla > grans, axis=0))


def test_svag_vind_raknas_separat() -> None:
    v = Vaderserie.skapa([270.0, 270.0, 270.0], [0.1, 5.0, 2.5])
    fd = fordela(v, [270.0], U_REF, u_min_m_s=0.5)
    stat = Haltstatistik([np.array([1e-6])], fd)
    assert stat.antal_modellerade_timmar == 2
    assert stat.arsmedel()[0] == pytest.approx(1e-6 * (1.0 + 2.0) / 2, rel=1e-15)
    assert fd.andel_svag_vind == pytest.approx(1 / 3)
    alla_svaga = fordela(Vaderserie.skapa([0.0], [0.0]), [0.0], U_REF)
    with pytest.raises(LuftFel, match="Inga modellerade"):
        Haltstatistik([np.array([1.0])], alla_svaga)


def test_statistik_validerar_indata() -> None:
    fd = fordela(Vaderserie.skapa([0.0], [3.0]), [0.0, 90.0], U_REF)
    with pytest.raises(LuftFel):
        Haltstatistik([np.zeros(3)], fd)
    with pytest.raises(LuftFel):
        Haltstatistik([np.zeros(3), np.zeros(4)], fd)
    with pytest.raises(LuftFel, match="negativa"):
        Haltstatistik([np.zeros(3), -np.ones(3)], fd)
