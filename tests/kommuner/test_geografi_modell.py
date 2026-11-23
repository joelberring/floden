# ruff: noqa: E501
"""Geografins manifest (K46), innehållsadresserat lager och tillrinningen (inga data, bara syntetiska filer)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from geografi.modell import (
    GeografiFel,
    lager_mapp,
    lagra,
    las_manifest,
    skriv_manifest,
    tolka_ref,
    trad_filer,
)
from geografi.tillrinning import nedsampla, tillrinning_uppstroms


def _lager(mapp: Path, innehall: dict[str, bytes]) -> Path:
    for namn, data in innehall.items():
        f = mapp / namn
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    return mapp


def _manifest(id_: str, version: str, k1: str, ra: str) -> dict[str, object]:
    return {
        "kontrakt": "floden-geografi/v1", "id": id_, "version": version,
        "lager": {"k1_rutnat": {"sha256": k1}, "radata": {"sha256": ra}},
    }  # fmt: skip


def test_lager_med_samma_innehall_lagras_en_gang_och_delas_mellan_versioner(tmp_path: Path) -> None:
    geo = tmp_path / "geo"
    a = lagra(geo, _lager(tmp_path / "a", {"mm_1_2/mark.tif": b"abc", "mm_1_2/run.json": b'{"start": 1}'}))
    # Samma markmodell en gång till: annan körtid i run.json, samma innehåll i övrigt.
    b = lagra(geo, _lager(tmp_path / "b", {"mm_1_2/mark.tif": b"abc", "mm_1_2/run.json": b'{"start": 2}'}))
    assert a.nytt and not b.nytt and a.sha256 == b.sha256
    c = lagra(geo, _lager(tmp_path / "c", {"mm_1_2/mark.tif": b"abd"}))
    assert c.sha256 != a.sha256
    assert sorted(p.name for p in (geo / "_lager").iterdir()) == sorted([a.sha256, c.sha256])
    # Två versioner pekar på samma lager och bägge går att läsa.
    ra = lagra(geo, _lager(tmp_path / "r", {"x.gpkg": b"1"}))
    skriv_manifest(geo, _manifest("gt", "v1", a.sha256, ra.sha256))
    skriv_manifest(geo, _manifest("gt", "v2", a.sha256, ra.sha256))
    d1, s1 = las_manifest(geo, "gt@v1")
    d2, s2 = las_manifest(geo, "gt@v2")
    assert d1["lager"] == d2["lager"] and s1 != s2
    assert len(list((geo / "_lager").iterdir())) == 3


def test_manifestet_ar_byte_for_byte_lika_for_samma_innehall(tmp_path: Path) -> None:
    geo = tmp_path / "geo"
    m = _manifest("gt", "v1", "0" * 64, "1" * 64)
    f1, s1 = skriv_manifest(geo, m)
    t1 = f1.read_bytes()
    f2, s2 = skriv_manifest(geo, dict(reversed(list(m.items()))))
    assert s1 == s2 and t1 == f2.read_bytes()
    assert b"\\\\" not in t1


def test_andrad_fil_upptacks_och_saknat_lager_ger_begripligt_fel(tmp_path: Path) -> None:
    geo = tmp_path / "geo"
    a = lagra(geo, _lager(tmp_path / "a", {"f.bin": b"1"}))
    skriv_manifest(geo, _manifest("gt", "v1", a.sha256, a.sha256))
    las_manifest(geo, "gt@v1")
    (lager_mapp(geo, a.sha256) / "f.bin").write_bytes(b"2")
    with pytest.raises(GeografiFel, match="ändrats"):
        las_manifest(geo, "gt@v1")
    with pytest.raises(GeografiFel, match="floden geografi mappa gt"):
        las_manifest(geo, "gt@v9")


def test_ref_tolkas_strikt() -> None:
    assert tolka_ref("goteborg-test@v1") == ("goteborg-test", "v1")
    for fel in ("goteborg-test", "x@1", "../x@v1", "A@v1", "g@v0", "g@v1/../x"):
        with pytest.raises(GeografiFel):
            tolka_ref(fel)


def test_dolda_filer_oppenhet_och_korlogg_ingar_inte_i_hashen(tmp_path: Path) -> None:
    m = _lager(tmp_path / "m", {"a.tif": b"1", ".dolt": b"x", "oppenhet.toml": b"y", "mm/run.json": b"z", "mm/recept.json": b"r"})
    assert [f for f, _ in trad_filer(m)] == ["a.tif", "mm/recept.json"]


def _skal(n: int = 60, kron: float = 24.0) -> np.ndarray:
    """Skål: marken stiger från mitten till ett krön på radien `kron` och faller utanför."""
    y, x = np.mgrid[0:n, 0:n]
    r = np.hypot(x - n / 2, y - n / 2)
    return np.where(r <= kron, 10.0 + 0.5 * r, 10.0 + 0.5 * kron - 0.5 * (r - kron))


def test_tillrinning_stannar_vid_kroen_och_nar_inte_kanten() -> None:
    z = _skal()
    karna = np.zeros(z.shape, dtype=bool)
    karna[28:32, 28:32] = True
    t = tillrinning_uppstroms(z, karna)
    assert not t.nar_kanten
    # Hela skålen innanför kröet rinner mot kärnan: ungefär cirkelns yta (pi * 24^2 = 1810 celler).
    assert 1500 < t.antal_celler < 2100
    y, x = np.mgrid[0:60, 0:60]
    assert not t.maske[np.hypot(x - 30, y - 30) > 27].any()


def test_tillrinning_nar_kanten_nar_omradet_ar_for_litet() -> None:
    z = _skal(n=40)  # kröet (radie 24) ligger utanför rutan
    karna = np.zeros(z.shape, dtype=bool)
    karna[18:22, 18:22] = True
    assert tillrinning_uppstroms(z, karna).nar_kanten


def test_tillrinning_slutning_ger_uppstroms_och_inte_nedstroms() -> None:
    y, x = np.mgrid[0:50, 0:50]
    z = 100.0 - 1.0 * y  # lutar mot söder (hög norr)
    karna = np.zeros(z.shape, dtype=bool)
    karna[25:27, 20:30] = True
    t = tillrinning_uppstroms(z, karna)
    assert t.maske[5, 25] and not t.maske[45, 25]  # norr om kärnan ingår, söder om inte
    assert t.nar_kanten  # lutningen fortsätter till rutans norra kant


def test_nedsampling_tar_medelvarde_och_ignorerar_saknade_varden() -> None:
    z = np.array([[1.0, 3.0, np.nan, np.nan], [5.0, 7.0, np.nan, np.nan]])
    n = nedsampla(z, 2)
    assert n.shape == (1, 2) and n[0, 0] == 4.0 and np.isnan(n[0, 1])
