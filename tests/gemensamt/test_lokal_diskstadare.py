"""Hårda skydds-, budget-, symlänks- och kraschtester för `gemensamt.lokal_diskstadare` & CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from floden.cli import main as cli_main
from gemensamt.lokal_cache import BYTES_PER_GB, BYTES_PER_MB
from gemensamt.lokal_diskstadare import (
    LokalDiskKonfig,
    LokalDiskStadareFel,
    disk_utrymme_status,
    las_lokal_disk_konfig,
    samla_skyddade_korningar,
    stada_lokal_disk,
)

REPO = Path(__file__).resolve().parents[2]
NU = 1_800_000_000.0
MB = int(BYTES_PER_MB)


def _satt_tid_rekursivt(sokvag: Path, ts: float) -> None:
    """Sätter `atime`/`mtime` på en fil eller på alla filer och kataloger under en katalog."""
    if sokvag.is_symlink():
        return
    if sokvag.is_file():
        os.utime(sokvag, (ts, ts))
        return
    for rot_str, mappar, filer in os.walk(sokvag, topdown=False, followlinks=False):
        rot = Path(rot_str)
        for f in filer:
            fp = rot / f
            if not fp.is_symlink():
                os.utime(fp, (ts, ts))
        for m in mappar:
            mp = rot / m
            if not mp.is_symlink():
                os.utime(mp, (ts, ts))
    os.utime(sokvag, (ts, ts))


def _skapa_korning(
    cache_rot: Path,
    korning_id: str,
    *,
    status: str = "klar",
    storlek_bytes: int = MB,
    ts: float,
) -> Path:
    mapp = cache_rot / "webb" / "korningar" / korning_id
    (mapp / "resultat").mkdir(parents=True, exist_ok=True)
    (mapp / "korning.json").write_text(
        json.dumps({"korning_id": korning_id, "status": status}, ensure_ascii=False),
        encoding="utf-8",
    )
    (mapp / "resultat" / "djup.bin").write_bytes(b"D" * storlek_bytes)
    _satt_tid_rekursivt(mapp, ts)
    return mapp


def _skapa_k22_post(
    cache_rot: Path,
    slag: str,
    nyckel_hex: str,
    *,
    klar: bool = True,
    storlek_bytes: int = MB,
    ts: float,
) -> Path:
    nyckel = (nyckel_hex * 64)[:64]
    mapp = cache_rot / "karna" / slag / nyckel[:2] / nyckel
    (mapp / "utdata").mkdir(parents=True, exist_ok=True)
    (mapp / "utdata" / "raster.bin").write_bytes(b"K" * storlek_bytes)
    if klar:
        (mapp / ".klar").write_text('{"kontrakt":"K22/v1"}', encoding="utf-8")
    _satt_tid_rekursivt(mapp, ts)
    return mapp


def test_skyddade_korningar_bevaras_alltid_trots_alder_och_liten_budget(tmp_path: Path) -> None:
    """Pågående, köad, demo-, lage-idag- och scenarier-körning skyddas alltid (även 30 d gamla)."""
    cache_rot = tmp_path / "cache"
    projekt_rot = tmp_path / "lokal"
    demo_rot = tmp_path / "demo"
    gammal_ts = NU - 30.0 * 86400.0

    # Skapa skyddade körningar (samtliga 30 dagar gamla och större än budgeten!)
    m_kor = _skapa_korning(cache_rot, "run-pagaende", status="kor", storlek_bytes=MB, ts=gammal_ts)
    m_ko = _skapa_korning(cache_rot, "run-koad", status="ko", storlek_bytes=MB, ts=gammal_ts)
    m_demo = _skapa_korning(cache_rot, "run-demo", status="klar", storlek_bytes=MB, ts=gammal_ts)
    m_lage = _skapa_korning(cache_rot, "run-lage", status="klar", storlek_bytes=MB, ts=gammal_ts)
    m_scen = _skapa_korning(cache_rot, "run-scen", status="klar", storlek_bytes=MB, ts=gammal_ts)
    m_kv = _skapa_korning(cache_rot, "run-kv-scen", status="klar", storlek_bytes=MB, ts=gammal_ts)
    # Oskyddad gammal körning som SKA raderas
    m_gammal = _skapa_korning(
        cache_rot, "run-gammal-fri", status="klar", storlek_bytes=MB, ts=gammal_ts
    )

    # demo_rot/varld/demo.json
    (demo_rot / "varld").mkdir(parents=True)
    (demo_rot / "varld" / "demo.json").write_text(
        json.dumps({"delar": {"vatten": {"korning_id": "run-demo"}}}),
        encoding="utf-8",
    )

    # projekt_rot/projekt/p1/lage-idag.json + scenarier.json + kartverktyg/p1/scenarier/f1.json
    p1 = projekt_rot / "projekt" / "p1"
    p1.mkdir(parents=True)
    (p1 / "projekt.json").write_text('{"projekt_id":"p1"}', encoding="utf-8")
    (p1 / "lage-idag.json").write_text(
        json.dumps({"lage": "idag", "korningar": {"vatten": {"korning_id": "run-lage"}}}),
        encoding="utf-8",
    )
    (p1 / "scenarier.json").write_text(
        json.dumps(
            {"scenarier": [{"slug": "s1", "korningar": {"sol": {"korning_id": "run-scen"}}}]}
        ),
        encoding="utf-8",
    )
    kv_sc = projekt_rot / "kartverktyg" / "p1" / "scenarier"
    kv_sc.mkdir(parents=True)
    k50_fil = kv_sc / "forslag-a.json"
    k50_fil.write_text(
        json.dumps(
            {
                "format": "floden-kartverktyg/v1",
                "ritlager": {"kontrakt": "floden-ritlager/v1", "lager": []},
                "korningar": {"vatten": {"korning_id": "run-kv-scen"}},
            }
        ),
        encoding="utf-8",
    )

    # Lägg också en konto-katalog bredvid för att verifiera att den aldrig rörs
    konto_fil = tmp_path / "konto" / "konto.sqlite"
    konto_fil.parent.mkdir(parents=True)
    konto_fil.write_bytes(b"SQLITE-DATA")

    konfig = LokalDiskKonfig(
        cache_rot=cache_rot,
        projekt_rot=projekt_rot,
        demo_rot=demo_rot,
        max_gb=0.0001,  # extremt liten budget (~100 KB)
        max_alder_dagar=14.0,
        grace_minuter=30.0,
    )

    skyddade = samla_skyddade_korningar(konfig, nu_ts=NU)
    forvantade = {"run-pagaende", "run-koad", "run-demo", "run-lage", "run-scen", "run-kv-scen"}
    assert forvantade <= skyddade
    assert "run-gammal-fri" not in skyddade

    res = stada_lokal_disk(konfig, torrkor=False, nu_ts=NU)
    assert not m_gammal.exists()
    for skyddad_mapp in (m_kor, m_ko, m_demo, m_lage, m_scen, m_kv):
        assert skyddad_mapp.is_dir(), f"Skyddad körning raderades: {skyddad_mapp.name}"
    assert res.per_kategori["korningar"]["antal_raderade"] == 1
    assert res.per_kategori["korningar"]["antal_skyddade"] == 6
    assert (p1 / "projekt.json").is_file()
    assert (p1 / "scenarier.json").is_file()
    assert k50_fil.is_file()
    assert konto_fil.is_file()


def test_ny_katalog_inom_grace_minuter_raderas_aldrig(tmp_path: Path) -> None:
    """Filer och mappar ändrade inom `grace_minuter` skyddas i samtliga spår."""
    cache_rot = tmp_path / "cache"
    farsk_ts = NU - 10.0 * 60.0  # 10 min gammal (< 30 min grace)

    # Spår 1: nya tempfiler/mappar
    ny_tmp_mapp = cache_rot / ".floden_ny_tmp"
    ny_tmp_mapp.mkdir(parents=True)
    (ny_tmp_mapp / "del.bin").write_bytes(b"T" * MB)
    _satt_tid_rekursivt(ny_tmp_mapp, farsk_ts)

    ny_tmp_fil = cache_rot / "tmp_skrivning.dat"
    ny_tmp_fil.write_bytes(b"U" * MB)
    _satt_tid_rekursivt(ny_tmp_fil, farsk_ts)

    # Spår 2: ny gammal-version-mapp i webb/lager
    ny_v8 = cache_rot / "webb" / "lager" / "v8"
    ny_v8.mkdir(parents=True)
    (ny_v8 / "bild.png").write_bytes(b"P" * MB)
    _satt_tid_rekursivt(ny_v8, farsk_ts)
    v12 = cache_rot / "webb" / "lager" / "v12"
    v12.mkdir(parents=True)
    (v12 / "ny.png").write_bytes(b"Q" * MB)
    _satt_tid_rekursivt(v12, farsk_ts)

    # Spår 3: ny avslutad körning (även med status="fel")
    ny_korning = _skapa_korning(
        cache_rot, "run-ny-fel", status="fel", storlek_bytes=MB, ts=farsk_ts
    )

    # Spår 4: ny trasig K22-post utan .klar (pågående skrivning) och ny komplett post
    ny_k22_trasig = _skapa_k22_post(
        cache_rot, "jobb", "a", klar=False, storlek_bytes=MB, ts=farsk_ts
    )
    ny_k22_klar = _skapa_k22_post(
        cache_rot, "scenarier", "b", klar=True, storlek_bytes=MB, ts=farsk_ts
    )

    konfig = LokalDiskKonfig(
        cache_rot=cache_rot,
        max_gb=0.0001,
        max_alder_dagar=1.0,
        grace_minuter=30.0,
    )
    res = stada_lokal_disk(konfig, torrkor=False, nu_ts=NU)

    assert res.antal_raderade == 0
    assert res.frigjorda_bytes == 0
    assert res.efter_bytes == res.fore_bytes
    assert res.antal_skyddade >= 6
    assert ny_tmp_mapp.is_dir()
    assert ny_tmp_fil.is_file()
    assert ny_v8.is_dir()
    assert ny_korning.is_dir()
    assert ny_k22_trasig.is_dir()
    assert ny_k22_klar.is_dir()


def test_alla_fyra_spar_och_lru_rensas_exakt_ned_till_85_procent(tmp_path: Path) -> None:
    """Temp, gamla webb/lager/v8, körningar, K22 och s3_lru rensas ned till <= 85 % av max_gb."""
    cache_rot = tmp_path / "cache"
    gammal_tmp_ts = NU - 2.0 * 3600.0  # 2 h (> 30 min grace)
    gammal_alder_ts = NU - 20.0 * 86400.0  # 20 d (> 14 d max_alder)

    # Spår 1: övergivna tempkataloger och tempfiler
    tmp_mapp = cache_rot / ".floden_tmp_krasch"
    tmp_mapp.mkdir(parents=True)
    (tmp_mapp / "skrap.bin").write_bytes(b"T" * MB)
    _satt_tid_rekursivt(tmp_mapp, gammal_tmp_ts)

    tmp_fil = cache_rot / "tmp_gammal.part"
    tmp_fil.write_bytes(b"U" * MB)
    _satt_tid_rekursivt(tmp_fil, gammal_tmp_ts)

    # Spår 2: gammal webb/lager/v8 + aktuell v12 med 2 filer (1 MB vardera)
    v8 = cache_rot / "webb" / "lager" / "v8"
    v8.mkdir(parents=True)
    (v8 / "gammal.png").write_bytes(b"8" * MB)
    _satt_tid_rekursivt(v8, gammal_tmp_ts)

    v12 = cache_rot / "webb" / "lager" / "v12"
    v12.mkdir(parents=True)
    v12_aldst = v12 / "a_aldst.png"
    v12_nyare = v12 / "b_nyare.png"
    v12_aldst.write_bytes(b"1" * MB)
    v12_nyare.write_bytes(b"2" * MB)
    _satt_tid_rekursivt(v12_aldst, NU - 5.0 * 86400.0)
    _satt_tid_rekursivt(v12_nyare, NU - 1.0 * 86400.0)

    # Spår 3: 1 åldersutgången körning + 3 yngre körningar (för LRU)
    k_gammal = _skapa_korning(
        cache_rot, "run-20d", status="klar", storlek_bytes=MB, ts=gammal_alder_ts
    )
    k_lru_0 = _skapa_korning(
        cache_rot, "run-lru-0", status="fel", storlek_bytes=MB, ts=NU - 8.0 * 86400.0
    )
    k_lru_1 = _skapa_korning(
        cache_rot, "run-lru-1", status="klar", storlek_bytes=MB, ts=NU - 4.0 * 86400.0
    )
    k_lru_2 = _skapa_korning(
        cache_rot, "run-lru-2", status="klar", storlek_bytes=MB, ts=NU - 2.0 * 86400.0
    )

    # Spår 4: 1 trasig K22 utan .klar + 1 avbruten K22-tempmapp + 1 åldersutgången + 3 yngre K22
    k22_trasig = _skapa_k22_post(
        cache_rot, "jobb", "1", klar=False, storlek_bytes=MB, ts=gammal_tmp_ts
    )
    k22_tmp = cache_rot / "karna" / "jobb" / "ab" / ".abcdef012345-abandoned"
    k22_tmp.mkdir(parents=True)
    (k22_tmp / "del.bin").write_bytes(b"Q" * MB)
    _satt_tid_rekursivt(k22_tmp, gammal_tmp_ts)
    k22_gammal = _skapa_k22_post(
        cache_rot, "jobb", "2", klar=True, storlek_bytes=MB, ts=gammal_alder_ts
    )
    k22_lru_0 = _skapa_k22_post(
        cache_rot, "jobb", "3", klar=True, storlek_bytes=MB, ts=NU - 7.0 * 86400.0
    )
    k22_lru_1 = _skapa_k22_post(
        cache_rot, "scenarier", "4", klar=True, storlek_bytes=MB, ts=NU - 3.0 * 86400.0
    )
    k22_lru_2 = _skapa_k22_post(
        cache_rot, "jobb", "5", klar=True, storlek_bytes=MB, ts=NU - 1.0 * 86400.0
    )

    # s3_lru med 1 gammal fil (raderas av ålder)
    s3_dir = cache_rot / "s3_lru"
    s3_dir.mkdir(parents=True)
    s3_gammal = s3_dir / "ruta.tif"
    s3_gammal.write_bytes(b"S" * MB)
    _satt_tid_rekursivt(s3_gammal, gammal_alder_ts)

    # Budget: 3 MB -> 85 % mål = 2,55 MB (efter fas 1 finns 8 MB kvar -> LRU ska ner <= 2,55 MB)
    max_gb = 3.0 / 1024.0
    max_bytes = int(max_gb * BYTES_PER_GB)
    mal_bytes = int(max_gb * 0.85 * BYTES_PER_GB)

    konfig = LokalDiskKonfig(
        cache_rot=cache_rot,
        max_gb=max_gb,
        mal_andel=0.85,
        max_alder_dagar=14.0,
        grace_minuter=30.0,
    )

    # Torrkor först: noll filer raderade, men exakt samma prognos som skarp körning
    torr = stada_lokal_disk(konfig, torrkor=True, nu_ts=NU)
    assert torr.torrkor is True
    assert torr.efter_bytes <= mal_bytes
    assert torr.fore_bytes - torr.frigjorda_bytes == torr.efter_bytes
    assert tmp_mapp.is_dir() and v8.is_dir() and k_gammal.is_dir() and k22_trasig.is_dir()
    assert k22_tmp.is_dir() and s3_gammal.is_file()

    # Skarp körning
    skarp = stada_lokal_disk(konfig, torrkor=False, nu_ts=NU)
    assert skarp.torrkor is False
    assert skarp.fore_bytes == torr.fore_bytes
    assert skarp.frigjorda_bytes == torr.frigjorda_bytes
    assert skarp.efter_bytes == torr.efter_bytes
    assert skarp.antal_raderade == torr.antal_raderade
    assert skarp.per_kategori == torr.per_kategori
    assert skarp.efter_bytes <= int(max_bytes * 0.85)
    assert skarp.efter_bytes == sum(
        p.lstat().st_size for p in cache_rot.rglob("*") if p.is_file() and not p.is_symlink()
    )

    # Verifiera att fas-1-objekten försvann
    assert not tmp_mapp.exists()
    assert not tmp_fil.exists()
    assert not k22_tmp.exists()
    assert not v8.exists()
    assert not k_gammal.exists()
    assert not k22_trasig.exists()
    assert not k22_gammal.exists()
    assert not s3_gammal.exists()

    # Verifiera att LRU-eviction rensade i rätt ordning och lämnade nyaste K22-poster kvar
    assert not v12_aldst.exists()
    assert not v12_nyare.exists()
    assert not k_lru_0.exists()
    assert not k_lru_1.exists()
    assert not k_lru_2.exists()
    assert not k22_lru_0.exists()
    assert k22_lru_1.is_dir()
    assert k22_lru_2.is_dir()
    assert skarp.per_kategori["temp"]["antal_raderade"] == 3
    assert skarp.per_kategori["s3_lru"]["antal_raderade"] == 1


def test_symlank_utanfor_cache_rot_foljs_och_raderas_aldrig(tmp_path: Path) -> None:
    """Symlänkar som pekar utanför `cache_rot` följs och raderas aldrig."""
    utanfor_dir = tmp_path / "hemligt_utanfor"
    utanfor_dir.mkdir()
    utanfor_fil = utanfor_dir / "viktigt.txt"
    utanfor_fil.write_bytes(b"ALDRIG-RADERA" * 200)
    gammal_ts = NU - 30.0 * 86400.0
    _satt_tid_rekursivt(utanfor_dir, gammal_ts)

    cache_rot = tmp_path / "cache"
    (cache_rot / "webb" / "korningar").mkdir(parents=True)
    (cache_rot / "webb" / "lager" / "v12").mkdir(parents=True)
    (cache_rot / "karna" / "jobb" / "ab").mkdir(parents=True)

    # 1. Symlänkad tempfil och symlänkad tempmapp
    lank_tmp_fil = cache_rot / ".floden_escape"
    lank_tmp_fil.symlink_to(utanfor_fil)
    lank_tmp_mapp = cache_rot / "tmp_escape_dir"
    lank_tmp_mapp.symlink_to(utanfor_dir)

    # 2. Symlänkad körning + symlänk inuti en riktig gammal körning
    lank_korning = cache_rot / "webb" / "korningar" / "escape-run"
    lank_korning.symlink_to(utanfor_dir)
    riktig_korning = _skapa_korning(
        cache_rot, "old-real-run", status="klar", storlek_bytes=MB, ts=gammal_ts
    )
    inre_lank = riktig_korning / "resultat" / "escape.tif"
    inre_lank.symlink_to(utanfor_fil)

    # 3. Symlänkad gammal lagerversion + symlänkad K22-post
    lank_v8 = cache_rot / "webb" / "lager" / "v8"
    lank_v8.symlink_to(utanfor_dir)
    lank_k22 = cache_rot / "karna" / "jobb" / "ab" / ("a" * 64)
    lank_k22.symlink_to(utanfor_dir)

    konfig = LokalDiskKonfig(
        cache_rot=cache_rot,
        max_gb=0.0001,
        max_alder_dagar=1.0,
        grace_minuter=30.0,
        aktuell_lager_version="v12",
    )
    res = stada_lokal_disk(konfig, torrkor=False, nu_ts=NU)

    # Samtliga målfiler utanför cache_rot är 100 % intakta
    assert utanfor_dir.is_dir()
    assert utanfor_fil.read_bytes() == b"ALDRIG-RADERA" * 200
    # Själva symlänkarna följdes inte och raderades inte
    assert lank_tmp_fil.is_symlink()
    assert lank_tmp_mapp.is_symlink()
    assert lank_korning.is_symlink()
    assert inre_lank.is_symlink()
    assert lank_v8.is_symlink()
    assert lank_k22.is_symlink()
    # Vanlig fil inne i den gamla riktiga körningen städades bort
    assert not (riktig_korning / "resultat" / "djup.bin").exists()
    assert len(res.varningar) >= 1

    # Symlänkad cache_rot vägras alltid
    lank_rot = tmp_path / "lank_cache_rot"
    lank_rot.symlink_to(cache_rot)
    with pytest.raises(LokalDiskStadareFel, match="symlänk"):
        stada_lokal_disk(LokalDiskKonfig(cache_rot=lank_rot))

    # Skyddade kataloger som konto/lokal vägras som cache_rot
    konto_mapp = tmp_path / "konto"
    konto_mapp.mkdir()
    with pytest.raises(LokalDiskStadareFel, match="skyddad"):
        stada_lokal_disk(LokalDiskKonfig(cache_rot=konto_mapp))


def test_trasig_korning_json_kraschar_inte_och_hanteras_sakert(tmp_path: Path) -> None:
    """Korrupt JSON i `korning.json` kraschar inte, loggas i `varningar` och respekterar grace."""
    cache_rot = tmp_path / "cache"
    projekt_rot = tmp_path / "lokal"

    # 1. Korrupt korning.json inom grace (10 min) -> måste lämnas helt orörd
    farsk_korrupt = cache_rot / "webb" / "korningar" / "run-korrupt-ny"
    farsk_korrupt.mkdir(parents=True)
    (farsk_korrupt / "korning.json").write_text("{trasig json", encoding="utf-8")
    (farsk_korrupt / "data.bin").write_bytes(b"X" * MB)
    _satt_tid_rekursivt(farsk_korrupt, NU - 10.0 * 60.0)

    # 2. Korrupt korning.json som är 20 dagar gammal och oskyddad -> hanteras säkert (rensas)
    gammal_korrupt = cache_rot / "webb" / "korningar" / "run-korrupt-gammal"
    gammal_korrupt.mkdir(parents=True)
    (gammal_korrupt / "korning.json").write_text("not-json!!!", encoding="utf-8")
    (gammal_korrupt / "data.bin").write_bytes(b"Y" * MB)
    _satt_tid_rekursivt(gammal_korrupt, NU - 20.0 * 86400.0)

    # 3. Korrupt korning.json som är 20 dagar gammal MEN refererad i lage-idag.json -> skyddad!
    ref_korrupt = cache_rot / "webb" / "korningar" / "run-korrupt-ref"
    ref_korrupt.mkdir(parents=True)
    (ref_korrupt / "korning.json").write_text("[1, 2, 3]", encoding="utf-8")
    (ref_korrupt / "data.bin").write_bytes(b"Z" * MB)
    _satt_tid_rekursivt(ref_korrupt, NU - 20.0 * 86400.0)

    p1 = projekt_rot / "p1"
    p1.mkdir(parents=True)
    (p1 / "lage-idag.json").write_text(
        json.dumps({"korningar": {"vatten": {"korning_id": "run-korrupt-ref"}}}),
        encoding="utf-8",
    )

    konfig = LokalDiskKonfig(
        cache_rot=cache_rot,
        projekt_rot=projekt_rot,
        max_gb=10.0,
        max_alder_dagar=14.0,
        grace_minuter=30.0,
    )
    res = stada_lokal_disk(konfig, torrkor=False, nu_ts=NU)

    assert farsk_korrupt.is_dir()
    assert ref_korrupt.is_dir()
    assert not gammal_korrupt.exists()
    assert any("run-korrupt-ny" in v for v in res.varningar)
    assert any("run-korrupt-gammal" in v for v in res.varningar)
    assert any("run-korrupt-ref" in v for v in res.varningar)


def test_miljovariabler_validering_och_disk_utrymme_status(tmp_path: Path) -> None:
    """Miljövariabler `FLODEN_LOKAL_DISK_*` och PII-fri `disk_utrymme_status`."""
    cache_rot = tmp_path / "cache"
    cache_rot.mkdir()
    (cache_rot / "prov.bin").write_bytes(b"A" * (2 * MB))

    konfig = LokalDiskKonfig.fran_miljo(
        cache_rot,
        miljo={
            "FLODEN_LOKAL_DISK_MAX_GB": "8.5",
            "FLODEN_LOKAL_DISK_MAX_ALDER_DAGAR": "7",
            "FLODEN_LOKAL_DISK_GRACE_MIN": "45",
        },
    )
    assert konfig.cache_rot == cache_rot
    assert konfig.max_gb == 8.5
    assert konfig.max_alder_dagar == 7.0
    assert konfig.grace_minuter == 45.0

    with pytest.raises(LokalDiskStadareFel):
        las_lokal_disk_konfig(miljo={"FLODEN_LOKAL_DISK_MAX_GB": "ogiltigt"})
    with pytest.raises(LokalDiskStadareFel):
        LokalDiskKonfig(cache_rot=cache_rot, max_gb=0.0)
    with pytest.raises(LokalDiskStadareFel):
        LokalDiskKonfig(cache_rot=cache_rot, mal_andel=1.5)
    with pytest.raises(LokalDiskStadareFel):
        LokalDiskKonfig(cache_rot=cache_rot, grace_minuter=-1.0)
    with pytest.raises(LokalDiskStadareFel):
        LokalDiskKonfig(cache_rot=cache_rot, aktuell_lager_version="inte-version")

    status = disk_utrymme_status(cache_rot)
    assert set(status) == {"ledig_gb", "total_gb", "anvand_pct", "cache_gb"}
    assert status["total_gb"] > 0.0
    assert status["ledig_gb"] >= 0.0
    assert 0.0 <= status["anvand_pct"] <= 100.0
    assert pytest.approx(status["cache_gb"], rel=1e-6) == 2.0 / 1024.0

    # Ej skapad cache_rot fungerar utan fel och ger cache_gb == 0.0
    tom_status = disk_utrymme_status(tmp_path / "finns_inte_an")
    assert tom_status["cache_gb"] == 0.0
    assert tom_status["total_gb"] > 0.0


def test_cli_lagring_stada_alla_alias_och_systemd_service(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI `floden lagring stada --alla`, alias `stada-cache` och systemd-tjänst."""
    cache_rot = tmp_path / "cache"
    projekt_rot = tmp_path / "lokal"
    demo_rot = tmp_path / "demo"
    gammal_ts = 1_000_000.0  # mycket gammal (> 14 d)

    m_gammal = _skapa_korning(cache_rot, "cli-old", status="klar", storlek_bytes=MB, ts=gammal_ts)
    m_demo = _skapa_korning(cache_rot, "cli-demo", status="klar", storlek_bytes=MB, ts=gammal_ts)
    demo_rot.mkdir(parents=True)
    (demo_rot / "demo.json").write_text(
        json.dumps({"delar": {"vatten": {"korning_id": "cli-demo"}}}),
        encoding="utf-8",
    )
    projekt_rot.mkdir(parents=True)

    # 1. Alias `stada-cache --alla --torrkor --json`
    kod_torr = cli_main(
        [
            "lagring",
            "stada-cache",
            "--alla",
            "--cache-rot",
            str(cache_rot),
            "--projekt-rot",
            str(projekt_rot),
            "--demo-rot",
            str(demo_rot),
            "--max-gb",
            "10",
            "--max-alder-dagar",
            "14",
            "--torrkor",
            "--json",
        ]
    )
    assert kod_torr == 0
    ut_torr = json.loads(capsys.readouterr().out)
    assert ut_torr["torrkor"] is True
    assert ut_torr["antal_raderade"] == 1
    assert ut_torr["antal_skyddade"] == 1
    assert ut_torr["frigjorda_bytes"] >= MB
    assert set(ut_torr["disk"]) == {"ledig_gb", "total_gb", "anvand_pct", "cache_gb"}
    assert m_gammal.is_dir()
    assert m_demo.is_dir()

    # 2. Skarp körning via `stada --alla` (textutmatning)
    kod_skarp = cli_main(
        [
            "lagring",
            "stada",
            "--alla",
            "--cache-rot",
            str(cache_rot),
            "--projekt-rot",
            str(projekt_rot),
            "--demo-rot",
            str(demo_rot),
            "--max-gb",
            "10",
            "--max-alder-dagar",
            "14",
        ]
    )
    assert kod_skarp == 0
    ut_skarp = capsys.readouterr().out
    assert "Raderade 1 poster" in ut_skarp
    assert not m_gammal.exists()
    assert m_demo.is_dir()

    # 3. Verifiera deploy/floden-cache-stada.service
    service = (REPO / "deploy" / "floden-cache-stada.service").read_text(encoding="utf-8")
    rader = [r.strip() for r in service.splitlines() if r.strip() and not r.strip().startswith("#")]
    assert "WorkingDirectory=/srv/floden" in rader
    assert any(r.startswith("ExecStart=") and "--alla" in r for r in rader)


def test_s3_lru_bidrar_till_total_malbudget_vid_overskridande(tmp_path: Path) -> None:
    """När övriga spår är uttömda hjälper `s3_lru` till att sänka totalen till <= 85 %."""
    cache_rot = tmp_path / "cache"
    s3_dir = cache_rot / "s3_lru"
    s3_dir.mkdir(parents=True)

    # 2 MB skyddad körning + 3 st 1 MB-filer i s3_lru (totalt 5 MB); max = 4 MB -> mål 3,4 MB
    _skapa_korning(cache_rot, "aktiv", status="kor", storlek_bytes=2 * MB, ts=NU - 5.0 * 86400.0)
    s3_filer: list[Path] = []
    for i in range(3):
        f = s3_dir / f"tile_{i}.tif"
        f.write_bytes(bytes([i + 1]) * MB)
        _satt_tid_rekursivt(f, NU - (5 - i) * 86400.0)
        s3_filer.append(f)

    max_gb = 4.0 / 1024.0
    konfig = LokalDiskKonfig(
        cache_rot=cache_rot,
        max_gb=max_gb,
        mal_andel=0.85,
        max_alder_dagar=14.0,
        grace_minuter=30.0,
    )
    torr = stada_lokal_disk(konfig, torrkor=True, nu_ts=NU)
    skarp = stada_lokal_disk(konfig, torrkor=False, nu_ts=NU)

    assert torr.efter_bytes == skarp.efter_bytes
    assert skarp.efter_bytes <= int(max_gb * 0.85 * BYTES_PER_GB)
    assert skarp.per_kategori["s3_lru"]["antal_raderade"] == 2
    assert not s3_filer[0].exists()
    assert not s3_filer[1].exists()
    assert s3_filer[2].is_file()

