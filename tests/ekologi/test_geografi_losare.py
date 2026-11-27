# ruff: noqa: E501
"""Lösaren `ekologi.konnektivitet` läser NMD och VMI ur projektets geografi (K46, `lager.nmd` och
`lager.vmi`): kontextbufferten klipps ut, K9 läggs ovanpå, och indatarollen `nmd` finns kvar."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from tests.demovarld.test_demo import Demo

from data_inlasning.nmd import NmdLager, skriv_nmd
from geografi.markunderlag import klipp_lager, las_markunderlag
from geografi.modell import KONTRAKT_GEOGRAFI, GeografiFel, lagra, skriv_manifest
from testsvit.k1_io import skriv_geotiff


@pytest.fixture(scope="module")
def demo() -> Iterator[Demo]:
    rot = Path(tempfile.mkdtemp())
    try:
        yield Demo(rot)
    finally:
        shutil.rmtree(rot, ignore_errors=True)


def _geografi(rot: Path, nmd_fil: Path | None, *, vmi: NmdLager | None = None, buffert_m: float = 200.0) -> Path:
    """Skriver en liten geografi `testgeo@v1` med NMD (och valfritt VMI) som lager; returnerar manifestet."""
    geo = rot / "geo"
    lager: dict[str, Any] = {}
    if nmd_fil is not None:
        mapp = rot / "bygge_nmd"
        mapp.mkdir()
        shutil.copy(nmd_fil, mapp / "nmd.tif")
        shutil.copy(nmd_fil.with_suffix(".json"), mapp / "nmd.json")
        lg = lagra(geo, mapp)
        lager["nmd"] = {"sha256": lg.sha256, "buffert_m": buffert_m, "cell_m": 10.0}
    if vmi is not None:
        mapp = rot / "bygge_vmi"
        skriv_geotiff(mapp / "vmi.tif", vmi.klasser, x_min=vmi.x_min_m, y_max=vmi.y_max_m, pixelstorlek=vmi.cell_m)
        lg = lagra(geo, mapp)
        lager["vmi"] = {"sha256": lg.sha256, "buffert_m": buffert_m, "cell_m": 10.0, "alder": "äldre"}
    fil, _ = skriv_manifest(geo, {"kontrakt": KONTRAKT_GEOGRAFI, "id": "testgeo", "version": "v1", "lager": lager, "kallor": {"nmd": {"licens": "CC0", "kalla": "Testlager (påhittat)"}}})
    return fil


def _fraga(demo: Demo, namn: str, indata: str, parametrar: str = 'natverk = "barrskog-nacka"') -> str:
    text = (demo.fragor / "demo_ekologi.toml").read_text(encoding="utf-8")
    ny = re.sub(
        r'parametrar = \{ natverk = "barrskog-nacka" \}\nindata = \{ nmd = "[^"]*" \}',
        f"parametrar = {{ {parametrar} }}\nindata = {{ {indata} }}",
        text,
    ).replace('fraga_id = "demo-ekologi"', f'fraga_id = "{namn}"')
    assert ny != text
    (demo.fragor / f"{namn}.toml").write_text(ny, encoding="utf-8")
    return namn


def _tabell(demo: Demo, korning: str) -> dict[str, dict[str, Any]]:
    res = demo.tjanst.resultat(korning)
    tab = next(t for t in res["tabeller"] if t["losare"] == "ekologi.konnektivitet")
    return {r["scenario"]: r["varden"] for r in tab["rader"]}


def test_geografi_ger_samma_resultat_som_indatarollen_nmd_och_k9_laggs_ovanpa(demo: Demo, tmp_path: Path) -> None:
    manifest = _geografi(tmp_path, demo.varld / "ekologi" / "nmd.tif")
    namn = _fraga(demo, "geo-ekologi", f'geografi = "{manifest.as_posix()}"')
    status, _ = demo.tjanst.kor_synkront(namn, namn)
    assert status["status"] == "klar"
    via_geo, via_nmd = _tabell(demo, namn), _tabell(demo, "demo-ekologi")
    for sc in ("fore", "efter"):
        for nyckel in ("eca_m2", "eca_min_m2", "eca_max_m2", "pc", "antal_flackar", "total_habitat_area_m2"):
            assert via_geo[sc][nyckel] == pytest.approx(via_nmd[sc][nyckel], rel=1e-12), (sc, nyckel)
    # K9 ovanpå geografins NMD: det nya huset ger lägre ECA än nuläget.
    assert via_geo["efter"]["eca_m2"] < via_geo["fore"]["eca_m2"]
    # Proveniens i ekologi.json: geografin, manifestets sha256 och lagrets sha256, ingen sökväg.
    mapp = demo.tjanst.korningsmapp / namn / "resultat" / "konnektivitet" / "efter"
    d = json.loads((mapp / "ekologi.json").read_text(encoding="utf-8"))
    kr = d["natverk"]["klassraster"]
    assert kr["roll"] == "geografi" and kr["markunderlag"]["geografi"] == "testgeo@v1"
    assert len(kr["markunderlag"]["manifest_sha256"]) == 64 and len(kr["markunderlag"]["nmd_lager_sha256"]) == 64
    assert str(tmp_path) not in json.dumps(d)
    # Geografins NMD-lager är mappat med kortare buffert än nätverkets behov: det står som varning.
    assert any("buffert" in v and "kontextbehov" in v for v in d["varningar"])
    # Samma frågefil igen ger cacheträff (manifestets sha256 ingår i nyckeln).
    _status, oforandrad = demo.tjanst.kor_synkront(namn, namn)
    assert oforandrad


def test_indatarollen_nmd_finns_kvar_och_bada_samtidigt_nekas(demo: Demo, tmp_path: Path) -> None:
    assert _tabell(demo, "demo-ekologi")["fore"]["eca_m2"] > 0  # rollen nmd fungerar som förut
    manifest = _geografi(tmp_path, demo.varld / "ekologi" / "nmd.tif")
    namn = _fraga(
        demo, "bada-ekologi",
        f'nmd = "{(demo.varld / "ekologi" / "nmd.tif").as_posix()}", geografi = "{manifest.as_posix()}"',
    )  # fmt: skip
    with pytest.raises(Exception, match="inte båda"):
        demo.tjanst.kor_synkront(namn, namn)


def test_geografi_utan_nmd_lager_ger_tydligt_fel(demo: Demo, tmp_path: Path) -> None:
    manifest = _geografi(tmp_path, None)
    namn = _fraga(demo, "utan-nmd-ekologi", f'geografi = "{manifest.as_posix()}"')
    with pytest.raises(Exception, match="inget NMD-lager"):
        demo.tjanst.kor_synkront(namn, namn)


def test_andrat_lager_i_geografin_upptacks(demo: Demo, tmp_path: Path) -> None:
    manifest = _geografi(tmp_path, demo.varld / "ekologi" / "nmd.tif")
    d = json.loads(manifest.read_text(encoding="utf-8"))
    (tmp_path / "geo" / "_lager" / d["lager"]["nmd"]["sha256"] / "nmd.tif").write_bytes(b"andrad")
    with pytest.raises(GeografiFel, match="nmd"):
        las_markunderlag(manifest, (151_000.0, 6_580_000.0, 151_100.0, 6_580_100.0), 3000.0)


def test_kontextbufferten_klipps_ur_lagret_pa_lagrets_rutnat(tmp_path: Path) -> None:
    # Lager på 8 x 8 km (800 x 800 celler); kärna 700 m, nätverkets längsta tröskel 3 000 m -> buffert 3 000 m.
    rng = np.random.default_rng(7)
    klasser = rng.choice(np.array([42, 111, 116, 53], dtype=np.uint8), size=(800, 800))
    lager = NmdLager(klasser, 147_000.0, 6_584_000.0, 10.0)
    nmd = tmp_path / "nmd.tif"
    skriv_nmd(lager, nmd)
    manifest = _geografi(tmp_path, nmd, buffert_m=3000.0)
    mu = las_markunderlag(manifest, (150_995.0, 6_579_995.0, 151_705.0, 6_580_705.0), 3000.0)
    assert mu.klippt_buffert_m == 3000.0 and mu.lager_buffert_m == 3000.0 and not mu.varningar
    e0, n0, e1, n1 = mu.klippt_utsnitt
    assert (e0, e1) == (147_990.0, 154_710.0) and (n0, n1) == (6_576_990.0, 6_583_710.0)
    assert mu.nmd.klasser.shape == (672, 672)
    r0, k0 = round((6_584_000.0 - n1) / 10), round((e0 - 147_000.0) / 10)
    assert np.array_equal(mu.nmd.klasser, klasser[r0 : r0 + 672, k0 : k0 + 672])
    assert mu.nmd.x_min_m == e0 and mu.nmd.y_max_m == n1
    # Buffert 2 km-golvet: tröskel 500 ger 2 000 m.
    mu2 = las_markunderlag(manifest, (150_995.0, 6_579_995.0, 151_705.0, 6_580_705.0), 500.0)
    assert mu2.klippt_buffert_m == 2000.0 and mu2.nmd.klasser.shape == (472, 472)
    # Utsnitt utanför lagret: fel. Delvis utanför: begränsat till lagret med varning.
    with pytest.raises(Exception, match="utanför"):
        klipp_lager(lager, (0.0, 0.0, 10.0, 10.0))
    kant = las_markunderlag(manifest, (146_900.0, 6_579_995.0, 147_500.0, 6_580_705.0), 3000.0)
    assert kant.nmd.x_min_m == 147_000.0 and any("täcker inte hela" in v for v in kant.varningar)


def test_vmi_klipps_pa_samma_utsnitt_och_krav_pa_lager(tmp_path: Path) -> None:
    klasser = np.full((600, 600), 111, dtype=np.uint8)
    nmd = tmp_path / "nmd.tif"
    skriv_nmd(NmdLager(klasser, 148_000.0, 6_583_000.0, 10.0), nmd)
    vmask = np.zeros((600, 600), dtype=np.uint8)
    vmask[300:310, 300:310] = 1
    manifest = _geografi(tmp_path, nmd, vmi=NmdLager(vmask, 148_000.0, 6_583_000.0, 10.0), buffert_m=3000.0)
    karna = (150_900.0, 6_579_900.0, 151_000.0, 6_580_000.0)
    mu = las_markunderlag(manifest, karna, 3000.0, med_vmi=True)
    assert mu.vmi is not None and mu.vmi.klasser.shape == mu.nmd.klasser.shape
    assert mu.vmi.x_min_m == mu.nmd.x_min_m and int(mu.vmi.klasser.sum()) == 100
    assert mu.vmi_sha256 is not None
    (tmp_path / "b").mkdir()
    utan_vmi = _geografi(tmp_path / "b", nmd, buffert_m=3000.0)
    with pytest.raises(GeografiFel, match="inget VMI-lager"):
        las_markunderlag(utan_vmi, karna, 3000.0, med_vmi=True)


def test_auto_omsampling_vid_stor_nmd_kontextbuffert(demo: Demo, tmp_path: Path) -> None:
    """Stor 3 km-buffrad NMD (> max_celler vid 10 m) omsamplas deterministiskt när cellstorlek_m ej angivits,
    medan en uttryckligt begärd för fin cellstorlek_m fortfarande ger tydligt fel."""
    # 651 x 651 celler vid 10 m (423 801 > 250 000), precis som 500 m-kärna + 3 km NMD-buffert i Göteborg.
    klasser = np.full((651, 651), 42, dtype=np.uint8)
    klasser[200:220, 200:220] = 111  # 4 ha barrskog
    klasser[420:440, 420:440] = 111  # 4 ha barrskog
    nmd = tmp_path / "stor_nmd.tif"
    skriv_nmd(NmdLager(klasser, 148_000.0, 6_583_510.0, 10.0), nmd)
    manifest = _geografi(tmp_path, nmd, buffert_m=3000.0)

    # Utan cellstorlek_m: deterministisk auto-omsampling till 20 m (326 x 326 = 106 276 <= 250 000) + varning.
    namn_auto = _fraga(demo, "geo-ekologi-auto-cell", f'geografi = "{manifest.as_posix()}"')
    status, _ = demo.tjanst.kor_synkront(namn_auto, namn_auto)
    assert status["status"] == "klar"
    tab = _tabell(demo, namn_auto)
    assert tab["fore"]["cellstorlek_m"] == 20.0
    assert tab["fore"]["antal_flackar"] == 2
    dok = json.loads(
        (demo.tjanst.korningsmapp / namn_auto / "resultat" / "konnektivitet" / "fore" / "ekologi.json").read_text(
            encoding="utf-8"
        )
    )
    assert any("omsamplades deterministiskt till 20 m" in v for v in dok["varningar"])

    # Med uttrycklig cellstorlek_m = 10.0: ingen tyst omsampling — tydligt max_celler-fel kvarstår!
    namn_fel = _fraga(
        demo,
        "geo-ekologi-expl-10m",
        f'geografi = "{manifest.as_posix()}"',
        parametrar='natverk = "barrskog-nacka", cellstorlek_m = 10.0',
    )
    with pytest.raises(Exception, match="max_celler=250000"):
        demo.tjanst.kor_synkront(namn_fel, namn_fel)
