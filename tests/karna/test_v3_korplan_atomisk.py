"""Tester för atomisk skrivning av `run.json` i `src/karna/korplan.py` (Våg 3 / #85)."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import numpy as np
import pytest

from gemensamt.manifest import FilPost, ManifestFel, las_manifest, skapa_manifest
from karna.korplan import (
    _kopiera_atomiskt,
    _skriv_manifest_atomiskt,
    kor_utredning,
)
from karna.resultatcache import ResultatCache
from karna.utredning import fraga_fran_dict
from testsvit.k1_io import skriv_geotiff


def _skapa_minimal_k1_ruta(mapp: Path, n: int = 16) -> None:
    mapp.mkdir(parents=True, exist_ok=True)
    x = np.arange(n, dtype=np.float32)
    z = 10.0 + 0.05 * x[None, :] * np.ones((n, 1), dtype=np.float32)
    kw = {"x_min": 1000.0, "y_max": 2000.0, "pixelstorlek": 1.0}
    skriv_geotiff(mapp / "mark.tif", z, **kw)
    skriv_geotiff(mapp / "osakerhet.tif", np.full((n, n), 0.05, dtype=np.float32), **kw)
    skriv_geotiff(mapp / "markslag.tif", np.full((n, n), 4, dtype=np.uint8), **kw)
    (mapp / "recept.json").write_text(json.dumps({"ruta_id": mapp.name}), encoding="utf-8")


def test_samtidig_lasning_ser_aldrig_halvskriven_run_json(tmp_path: Path) -> None:
    """Samtida läsare under upprepad atomisk skrivning och kopiering ser alltid giltig JSON."""
    run_fil = tmp_path / "run.json"
    kalla_fil = tmp_path / "kalla_run.json"
    forsta = skapa_manifest(
        modul="karna",
        kodversion="test1234",
        parametrar={"fraga_id": "init", "varv": 0},
        utdata=[FilPost(roll="sammanstallning", fil="sammanstallning.json", sha256="a" * 64)],
    )
    _skriv_manifest_atomiskt(forsta, run_fil)
    _skriv_manifest_atomiskt(forsta, kalla_fil)

    stopp = threading.Event()
    lasfel: list[Exception] = []
    antal_lasningar = 0

    def _lasare() -> None:
        nonlocal antal_lasningar
        while not stopp.is_set():
            try:
                for fil in tmp_path.rglob("run.json"):
                    manifest = las_manifest(fil)
                    assert manifest.modul == "karna"
                    antal_lasningar += 1
            except Exception as exc:  # noqa: BLE001
                lasfel.append(exc)
                return

    trad = threading.Thread(target=_lasare, daemon=True)
    trad.start()
    try:
        for i in range(1, 80):
            m = skapa_manifest(
                modul="karna",
                kodversion="test1234",
                parametrar={"fraga_id": f"fraga-{i}", "varv": i, "utfyllnad": "x" * 2048},
                utdata=[
                    FilPost(roll="sammanstallning", fil="sammanstallning.json", sha256="b" * 64)
                ],
            )
            if i % 2 == 0:
                _skriv_manifest_atomiskt(m, run_fil)
            else:
                _skriv_manifest_atomiskt(m, kalla_fil)
                _kopiera_atomiskt(kalla_fil, run_fil)
    finally:
        stopp.set()
        trad.join(timeout=5.0)

    assert not lasfel, f"Samtidig läsare såg trasig/halvskriven run.json: {lasfel}"
    assert antal_lasningar > 0
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomisk_skrivning_stader_tempfil_och_bevarar_gammal_vid_fel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Om `os.replace` fallerar lämnas inga `.tmp`-filer kvar och föregående `run.json` är orörd."""
    run_fil = tmp_path / "run.json"
    ursprung = skapa_manifest(modul="karna", kodversion="orig", parametrar={"fraga_id": "v1"})
    _skriv_manifest_atomiskt(ursprung, run_fil)

    def _trasig_replace(_src: str | os.PathLike[str], _dst: str | os.PathLike[str]) -> None:
        raise OSError("simulerat diskfel vid replace")

    monkeypatch.setattr(os, "replace", _trasig_replace)
    ny = skapa_manifest(modul="karna", kodversion="ny", parametrar={"fraga_id": "v2"})
    with pytest.raises(OSError, match="simulerat diskfel"):
        _skriv_manifest_atomiskt(ny, run_fil)

    assert las_manifest(run_fil).parametrar["fraga_id"] == "v1"
    assert [p.name for p in tmp_path.iterdir()] == ["run.json"]


def test_kor_utredning_skriver_run_json_atomiskt_utan_kvarlamnade_tempfiler(tmp_path: Path) -> None:
    """`kor_utredning` lämnar giltiga `run.json` utan tempfiler under samtida `rglob`-läsning."""
    k1 = tmp_path / "k1"
    _skapa_minimal_k1_ruta(k1)
    cache = ResultatCache(tmp_path / "cache")
    utmapp = tmp_path / "ut"

    fraga = fraga_fran_dict(
        {
            "kontrakt": "K20/v1",
            "fraga_id": "atomisk-test",
            "scenarier": {"fore": {"bas": str(k1)}},
            "analyser": [{"id": "lutning", "losare": "geometri.lutning"}],
        },
        tmp_path,
    )

    stopp = threading.Event()
    lasfel: list[Exception] = []
    antal_lasta = 0

    def _oppenhet_lik_lasare() -> None:
        nonlocal antal_lasta
        while not stopp.is_set():
            if not utmapp.exists():
                continue
            try:
                for fil in list(utmapp.rglob("run.json")):
                    try:
                        m = las_manifest(fil)
                        assert m.kontrakt == "K0/v1"
                        antal_lasta += 1
                    except ManifestFel as exc:
                        # Tillåtet enbart om hela utmappen raderades mitt under `shutil.rmtree`;
                        # JSONDecodeError / tomt eller halvt manifest måste alltid fälla testet.
                        if isinstance(exc.__cause__, FileNotFoundError):
                            continue
                        raise
            except Exception as exc:  # noqa: BLE001
                lasfel.append(exc)
                return

    trad = threading.Thread(target=_oppenhet_lik_lasare, daemon=True)
    trad.start()
    try:
        for _ in range(3):
            res = kor_utredning(fraga, cache, utmapp)
            assert (res.utmapp / "run.json").is_file()
    finally:
        stopp.set()
        trad.join(timeout=5.0)

    assert not lasfel, f"Samtidig läsning under kor_utredning misslyckades: {lasfel}"
    assert antal_lasta > 0

    hittade_run = sorted(utmapp.rglob("run.json"))
    assert len(hittade_run) >= 2
    for run_fil in hittade_run:
        m = las_manifest(run_fil)
        assert m.kontrakt == "K0/v1"

    # Inga kvarlämnade tempfiler någonstans under utmapp eller cache
    assert list(utmapp.rglob("*.tmp")) == []
    assert list((tmp_path / "cache").rglob("*.tmp")) == []
