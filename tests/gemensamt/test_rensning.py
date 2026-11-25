"""Tester för 3-lagers rensning: `gemensamt.rensning`, `gemensamt.lokal_cache` och CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from floden.cli import main as cli_main
from gemensamt.lokal_cache import (
    BYTES_PER_MB,
    LokalCacheFel,
    las_lokal_cache_konfig,
    stada_lokal_cache,
)
from gemensamt.objektlagring import S3Lagring
from gemensamt.rensning import (
    RensningFel,
    installera_livscykelregler,
    skapa_livscykelregler,
    temporar_rutkatalog,
)


class _MockLifecycleKlient:
    def __init__(self) -> None:
        self.anrop: list[tuple[str, dict[str, Any]]] = []

    def put_bucket_lifecycle_configuration(
        self,
        Bucket: str,
        LifecycleConfiguration: dict[str, Any],
    ) -> dict[str, Any]:
        self.anrop.append((Bucket, LifecycleConfiguration))
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


def test_skapa_och_installera_livscykelregler() -> None:
    regler = skapa_livscykelregler(radata_dagar=2, skiss_dagar=14, multipart_dagar=1)
    rules = {r["ID"]: r for r in regler["Rules"]}
    assert set(rules) == {
        "floden-tmp-radata-ttl",
        "floden-tmp-skiss-ttl",
        "floden-abort-incomplete-multipart",
    }
    assert rules["floden-tmp-radata-ttl"]["Filter"] == {"Prefix": "tmp/radata/"}
    assert rules["floden-tmp-radata-ttl"]["Expiration"] == {"Days": 2}
    assert rules["floden-tmp-skiss-ttl"]["Filter"] == {"Prefix": "tmp/skiss/"}
    assert rules["floden-tmp-skiss-ttl"]["Expiration"] == {"Days": 14}
    assert rules["floden-abort-incomplete-multipart"]["AbortIncompleteMultipartUpload"] == {
        "DaysAfterInitiation": 1
    }

    with pytest.raises(RensningFel):
        skapa_livscykelregler(radata_dagar=0)

    klient = _MockLifecycleKlient()
    lagring = S3Lagring(
        endpoint_url="https://fsn1.your-objectstorage.com",
        bucket="floden-oppen-eu",
        access_key="ak",
        secret_key="sk",
        klient=klient,
    )
    installerade = installera_livscykelregler(lagring, radata_dagar=3, skiss_dagar=10)
    assert len(klient.anrop) == 1
    assert klient.anrop[0][0] == "floden-oppen-eu"
    assert klient.anrop[0][1] == installerade


def test_temporar_rutkatalog_rensas_aven_vid_fel(tmp_path: Path) -> None:
    skapad: Path | None = None
    with (
        pytest.raises(RuntimeError, match="simulera fel"),
        temporar_rutkatalog("mm_1_2", rot=tmp_path) as mapp,
    ):
        skapad = mapp
        (mapp / "raw.laz").write_bytes(b"LAZ-DATA")
        raise RuntimeError("simulera fel")
    assert skapad is not None
    assert not skapad.exists()

    # behall=True behåller katalogen för felsökning
    with temporar_rutkatalog("mm_1_2", rot=tmp_path, behall=True) as behallen:
        (behallen / "raw.laz").write_bytes(b"LAZ-DATA")
    assert behallen.is_dir()


def test_stada_lokal_cache_alder_och_torrkor(tmp_path: Path) -> None:
    cache = tmp_path / "s3_lru"
    (cache / "ruta_a").mkdir(parents=True)
    (cache / "ruta_b").mkdir(parents=True)

    gammal = cache / "ruta_a" / "mark.tif"
    farsk = cache / "ruta_b" / "mark.tif"
    gammal.write_bytes(b"A" * int(2 * BYTES_PER_MB))
    farsk.write_bytes(b"B" * int(1 * BYTES_PER_MB))

    nu = 1_800_000_000.0
    gammal_ts = nu - 20 * 86400.0  # 20 dagar gammal (> 14 dagar)
    farsk_ts = nu - 2 * 86400.0  # 2 dagar gammal
    os.utime(gammal, (gammal_ts, gammal_ts))
    os.utime(farsk, (farsk_ts, farsk_ts))

    # Torrkor raderar noll filer men rapporterar exakt 2.0 MB
    torr = stada_lokal_cache(cache, max_gb=10.0, max_alder_dagar=14, torrkor=True, nu_ts=nu)
    assert torr.torrkor is True
    assert torr.antal_raderade == 1
    assert torr.antal_raderade_alder == 1
    assert torr.antal_raderade_lru == 0
    assert pytest.approx(torr.frigjorda_mb, rel=1e-6) == 2.0
    assert gammal.is_file()
    assert farsk.is_file()

    # Skarp körning raderar gammal fil och tom undermapp, behåller färsk
    skarp = stada_lokal_cache(cache, max_gb=10.0, max_alder_dagar=14, torrkor=False, nu_ts=nu)
    assert skarp.antal_raderade == 1
    assert not gammal.exists()
    assert not (cache / "ruta_a").exists()
    assert farsk.is_file()


def test_stada_lokal_cache_lru_eviction(tmp_path: Path) -> None:
    cache = tmp_path / "s3_lru"
    cache.mkdir()
    nu = 1_800_000_000.0

    # 5 filer à 1 MB = 5 MB; max_gb = 3 MB -> 0.85 * 3 = 2.55 MB mål -> raderar 3 äldsta
    storlek_1mb = int(BYTES_PER_MB)
    filer: list[Path] = []
    for i in range(5):
        f = cache / f"ruta_{i}.tif"
        f.write_bytes(bytes([i]) * storlek_1mb)
        ts = nu - (10 - i) * 3600.0  # ruta_0 äldst, ruta_4 nyast
        os.utime(f, (ts, ts))
        filer.append(f)

    max_gb = 3.0 / 1024.0  # 3 MB i GB
    res = stada_lokal_cache(cache, max_gb=max_gb, max_alder_dagar=14, torrkor=False, nu_ts=nu)
    assert res.antal_raderade_alder == 0
    assert res.antal_raderade_lru == 3
    assert set(res.raderade_filer) == {"ruta_0.tif", "ruta_1.tif", "ruta_2.tif"}
    assert not filer[0].exists()
    assert not filer[1].exists()
    assert not filer[2].exists()
    assert filer[3].is_file()
    assert filer[4].is_file()
    assert pytest.approx(res.kvarvarande_mb, rel=1e-6) == 2.0


def test_stada_lokal_cache_foljer_inte_symlankar(tmp_path: Path) -> None:
    utanfor = tmp_path / "utanfor.txt"
    utanfor.write_bytes(b"VIKTIGT" * 1000)
    cache = tmp_path / "s3_lru"
    cache.mkdir()
    lank = cache / "lank.tif"
    lank.symlink_to(utanfor)

    nu = 1_800_000_000.0
    os.utime(utanfor, (nu - 30 * 86400.0, nu - 30 * 86400.0))

    res = stada_lokal_cache(cache, max_gb=0.000001, max_alder_dagar=1, torrkor=False, nu_ts=nu)
    assert res.antal_raderade == 0
    assert utanfor.is_file()
    assert lank.is_symlink()

    # Symlänkad cachemapp vägras
    lank_mapp = tmp_path / "lank_cache"
    lank_mapp.symlink_to(cache)
    with pytest.raises(LokalCacheFel, match="symlänk"):
        stada_lokal_cache(lank_mapp)


def test_cli_lagring_stada_torrkor_och_konfig(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    fil = cache / "gammal.tif"
    fil.write_bytes(b"X" * int(BYTES_PER_MB))
    os.utime(fil, (1000.0, 1000.0))

    kod = cli_main(
        [
            "lagring",
            "stada",
            "--mapp",
            str(cache),
            "--max-gb",
            "1.0",
            "--max-alder-dagar",
            "1",
            "--torrkor",
            "--json",
        ]
    )
    assert kod == 0
    ut = json.loads(capsys.readouterr().out)
    assert ut["torrkor"] is True
    assert ut["antal_raderade"] == 1
    assert pytest.approx(ut["frigjorda_mb"], rel=1e-3) == 1.0
    assert fil.is_file()

    ck = las_lokal_cache_konfig(
        {"lagring": {"lokal_cache": {"mapp": "data/c", "max_gb": 5.0, "max_alder_dagar": 7}}},
        miljo={},
    )
    assert ck.mapp == Path("data/c")
    assert ck.max_gb == 5.0
    assert ck.max_alder_dagar == 7.0
