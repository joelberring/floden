"""Regressionstester för `geografi.forberaknad_lager`: TTL-negativcache, trådsäkerhet & konfig."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import geografi.forberaknad_lager as fl_mod
from gemensamt.lokal_cache import StadaResultat, stada_lokal_cache
from gemensamt.manifest import skapa_manifest, skriv_manifest
from gemensamt.objektlagring import LokalLagring, ObjektLagring, geografi_nyckel
from geografi.forberaknad_lager import (
    STANDARD_NEGATIV_CACHE_TTL_S,
    ForberaknadKommunLager,
    ForberaknadLagerFel,
    forberaknad_lager_fran_miljo,
)


class _SpionLagring(ObjektLagring):
    """Trådsäker spion-wrapper runt `ObjektLagring` med valfri fördröjning i nedladdning."""

    def __init__(self, inre: ObjektLagring, *, nedladdning_paus_s: float = 0.0) -> None:
        self._inre = inre
        self._lock = threading.Lock()
        self._paus_s = nedladdning_paus_s
        self.antal_finns = 0
        self.antal_ladda_ner = 0

    def finns(self, nyckel: str) -> bool:
        with self._lock:
            self.antal_finns += 1
        return self._inre.finns(nyckel)

    def ladda_upp_fil(
        self,
        lokal_fil: str | Path,
        nyckel: str,
        *,
        sha256: str | None = None,
    ) -> str:
        return self._inre.ladda_upp_fil(lokal_fil, nyckel, sha256=sha256)

    def ladda_ner_fil(self, nyckel: str, lokal_fil: str | Path) -> Path:
        if self._paus_s > 0.0:
            time.sleep(self._paus_s)
        with self._lock:
            self.antal_ladda_ner += 1
        return self._inre.ladda_ner_fil(nyckel, lokal_fil)

    def radera_prefix(self, prefix: str) -> int:
        return self._inre.radera_prefix(prefix)

    def lista_prefix(self, prefix: str = "") -> list[str]:
        return self._inre.lista_prefix(prefix)

    def presigned_url(self, nyckel: str, giltig_s: int = 3600) -> str:
        return self._inre.presigned_url(nyckel, giltig_s=giltig_s)


def _ladda_upp_ruta(
    lagring: LokalLagring,
    tmp_path: Path,
    kommun: str,
    rut_id: str,
    filer: dict[str, bytes],
) -> None:
    staging = tmp_path / "staging" / kommun / rut_id
    staging.mkdir(parents=True, exist_ok=True)
    run_fil = staging / "run.json"
    skriv_manifest(
        skapa_manifest(modul="forberakna", parametrar={"dataklass": "oppen", "ruta_id": rut_id}),
        run_fil,
    )
    lagring.ladda_upp_fil(run_fil, geografi_nyckel(kommun, rut_id, "run.json"))
    for namn, innehall in filer.items():
        f = staging / namn
        f.write_bytes(innehall)
        lagring.ladda_upp_fil(f, geografi_nyckel(kommun, rut_id, namn))


def test_negativ_cache_respekterar_ttl_och_nyuppladdad_ruta_blir_synlig(tmp_path: Path) -> None:
    """Negativcache cachar saknade nycklar inom TTL men släpper igenom nyuppladdade rutor efter."""
    lokal = LokalLagring(tmp_path / "bucket")
    spion = _SpionLagring(lokal)

    tid_s = [1000.0]

    def _klocka() -> float:
        return tid_s[0]

    lager = ForberaknadKommunLager(
        spion,
        tmp_path / "cache",
        max_gb=1.0,
        negativ_cache_ttl_s=300.0,
        klocka=_klocka,
    )
    assert lager.negativ_cache_ttl_s == 300.0

    # 1. Rutan saknas initialt -> ForberaknadLagerFel, nycklarna negativcachas
    with pytest.raises(ForberaknadLagerFel):
        lager.hamta_ruta("nacka", "mm_150000_6580000", filer=("mark.tif",))
    finns_efter_forsta = spion.antal_finns
    assert finns_efter_forsta == 2  # run.json + mark.tif

    # 2. Ladda upp rutan till S3 medan negativcache fortfarande är aktiv (t = 1150 < 1300)
    _ladda_upp_ruta(lokal, tmp_path, "nacka", "mm_150000_6580000", {"mark.tif": b"MARK-V1"})
    tid_s[0] = 1150.0
    with pytest.raises(ForberaknadLagerFel):
        lager.hamta_ruta("nacka", "mm_150000_6580000", filer=("mark.tif",))
    # 0 nya S3-anrop inom TTL
    assert spion.antal_finns == finns_efter_forsta

    # 3. När TTL (300 s) har löpt ut (t = 1300.0) ska nyuppladdad ruta hittas direkt!
    tid_s[0] = 1300.0
    mapp = lager.hamta_ruta("nacka", "mm_150000_6580000", filer=("mark.tif",))
    assert (mapp / "run.json").is_file()
    assert (mapp / "mark.tif").read_bytes() == b"MARK-V1"
    assert spion.antal_finns == finns_efter_forsta + 2

    # 4. Delvis saknad fil: be om extra fil som ännu saknas -> negativcachas inom TTL, syns efter
    with pytest.raises(ForberaknadLagerFel):
        lager.hamta_ruta("nacka", "mm_150000_6580000", filer=("max_djup.tif",))
    finns_fore_extra = spion.antal_finns

    extra = tmp_path / "extra_max_djup.tif"
    extra.write_bytes(b"DJUP-V1")
    lokal.ladda_upp_fil(extra, geografi_nyckel("nacka", "mm_150000_6580000", "max_djup.tif"))

    # Inom TTL -> snabb lokal väg ser negativcache (0 S3-anrop)
    tid_s[0] = 1400.0
    lager.hamta_ruta("nacka", "mm_150000_6580000", filer=("mark.tif", "max_djup.tif"))
    assert spion.antal_finns == finns_fore_extra
    assert not (mapp / "max_djup.tif").exists()

    # Efter TTL -> extra fil laddas ner och syns i cachen
    tid_s[0] = 1601.0
    lager.hamta_ruta("nacka", "mm_150000_6580000", filer=("mark.tif", "max_djup.tif"))
    assert (mapp / "max_djup.tif").read_bytes() == b"DJUP-V1"


def test_negativ_cache_ttl_noll_och_validering(tmp_path: Path) -> None:
    lokal = LokalLagring(tmp_path / "bucket")
    spion = _SpionLagring(lokal)

    lager = ForberaknadKommunLager(spion, tmp_path / "cache", negativ_cache_ttl_s=0.0)
    with pytest.raises(ForberaknadLagerFel):
        lager.hamta_ruta("nacka", "mm_150000_6580000", filer=("mark.tif",))
    assert spion.antal_finns == 2

    _ladda_upp_ruta(lokal, tmp_path, "nacka", "mm_150000_6580000", {"mark.tif": b"DIREKT"})
    mapp = lager.hamta_ruta("nacka", "mm_150000_6580000", filer=("mark.tif",))
    assert (mapp / "mark.tif").read_bytes() == b"DIREKT"

    with pytest.raises(ForberaknadLagerFel, match="negativ_cache_ttl_s"):
        ForberaknadKommunLager(spion, tmp_path / "cache2", negativ_cache_ttl_s=-5.0)


def test_cache_rot_none_respekterar_konfig_och_miljovariabler(tmp_path: Path) -> None:
    """`cache_rot=None` i `ForberaknadKommunLager` och `forberaknad_lager_fran_miljo` följer env."""
    lokal = LokalLagring(tmp_path / "bucket")

    # 1. Direkt konstruktor med cache_rot=None och FLODEN_LOKAL_CACHE_*
    env_mapp = tmp_path / "env_cache"
    lager_env = ForberaknadKommunLager(
        lokal,
        cache_rot=None,
        miljo={
            "FLODEN_LOKAL_CACHE_MAPP": str(env_mapp),
            "FLODEN_LOKAL_CACHE_MAX_GB": "4.5",
            "FLODEN_LOKAL_CACHE_MAX_ALDER_DAGAR": "7.0",
        },
    )
    assert lager_env.cache_rot == env_mapp
    assert lager_env.max_gb == 4.5
    assert lager_env.max_alder_dagar == 7.0
    assert lager_env.negativ_cache_ttl_s == STANDARD_NEGATIV_CACHE_TTL_S

    # 2. Relativ konfig-sökväg med bas_rot
    konfig = {
        "lagring": {
            "typ": "s3",
            "endpoint": "https://fsn1.your-objectstorage.com",
            "bucket": "floden-oppen-eu",
            "access_key": "ak",
            "secret_key": "sk",
            "lokal_cache": {
                "mapp": "egendefinierad/lru",
                "max_gb": 2.5,
                "max_alder_dagar": 5.0,
            },
        }
    }
    lager_rel = ForberaknadKommunLager(
        lokal,
        cache_rot=None,
        konfig=konfig,
        miljo={},
        bas_rot=tmp_path,
    )
    assert lager_rel.cache_rot == tmp_path / "egendefinierad/lru"
    assert lager_rel.max_gb == 2.5
    assert lager_rel.max_alder_dagar == 5.0

    # 3. `forberaknad_lager_fran_miljo` med cache_rot=None + bas_rot + miljö-override
    lager_miljo_rel = forberaknad_lager_fran_miljo(
        cache_rot=None,
        konfig=konfig,
        miljo={},
        bas_rot=tmp_path,
        klient=MagicMock(),
    )
    assert lager_miljo_rel is not None
    assert lager_miljo_rel.cache_rot == tmp_path / "egendefinierad/lru"
    assert lager_miljo_rel.max_gb == 2.5

    abs_mapp = tmp_path / "absolut_cache"
    lager_miljo_abs = forberaknad_lager_fran_miljo(
        cache_rot=None,
        konfig=konfig,
        miljo={"FLODEN_LOKAL_CACHE_MAPP": str(abs_mapp)},
        bas_rot=tmp_path / "annan_rot",
        klient=MagicMock(),
    )
    assert lager_miljo_abs is not None
    assert lager_miljo_abs.cache_rot == abs_mapp


def test_tradsakerhet_samtidiga_hamtningar_och_laser_stadning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Samtidiga `hamta_ruta`-anrop krockar aldrig i negativcache eller `stada_lokal_cache`."""
    lokal = LokalLagring(tmp_path / "bucket")
    rutor = [f"mm_{150000 + i * 500}_6580000" for i in range(4)]
    for rut_id in rutor:
        _ladda_upp_ruta(
            lokal,
            tmp_path,
            "nacka",
            rut_id,
            {"mark.tif": f"DATA:{rut_id}:".encode() + b"Z" * 4096},
        )

    spion = _SpionLagring(lokal, nedladdning_paus_s=0.002)

    # Kontrollera att stada_lokal_cache aldrig körs parallellt av två trådar
    stadning_lock = threading.Lock()
    aktiva_stadningar = 0
    max_samtidiga_stadningar = 0
    antal_stadningar = 0

    def _spion_stada(*args: Any, **kwargs: Any) -> StadaResultat:
        nonlocal aktiva_stadningar, max_samtidiga_stadningar, antal_stadningar
        with stadning_lock:
            aktiva_stadningar += 1
            antal_stadningar += 1
            max_samtidiga_stadningar = max(max_samtidiga_stadningar, aktiva_stadningar)
        try:
            time.sleep(0.002)
            return stada_lokal_cache(*args, **kwargs)
        finally:
            with stadning_lock:
                aktiva_stadningar -= 1

    monkeypatch.setattr(fl_mod, "stada_lokal_cache", _spion_stada)

    lager = ForberaknadKommunLager(
        spion,
        tmp_path / "cache",
        max_gb=12_000 / (1024**3),  # Tvinga LRU-städning under samtidiga hämtningar
        negativ_cache_ttl_s=60.0,
    )

    barriar = threading.Barrier(8)

    def _arbetare(idx: int) -> int:
        barriar.wait()
        lyckade = 0
        for runda in range(4):
            rut_id = rutor[(idx + runda) % len(rutor)]
            mapp = lager.hamta_ruta("nacka", rut_id, filer=("mark.tif",))
            assert mapp.name == rut_id
            lyckade += 1
            # Blanda även in saknade rutor som skriver/läser `_saknade_nycklar`
            with pytest.raises(ForberaknadLagerFel):
                lager.hamta_ruta("nacka", f"mm_999{idx * 500}_6599000", filer=("mark.tif",))
        return lyckade

    with ThreadPoolExecutor(max_workers=8) as pool:
        resultat = list(pool.map(_arbetare, range(8)))

    assert resultat == [4] * 8
    assert antal_stadningar >= 1
    assert max_samtidiga_stadningar == 1
    # Inga kvarlämnade atomiska tempfiler efter samtidig körning
    assert not list((tmp_path / "cache").rglob(".floden_*"))
