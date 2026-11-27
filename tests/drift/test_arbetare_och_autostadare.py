# SPDX-License-Identifier: GPL-3.0-or-later
"""Tester för S-O5: enhetlig GPU/CPU-arbetare (`floden drift arbetare`),
in-container auto-diskstädare (`AutoDiskStadare`), `floden drift stada-disk`,
Compose-förval och driftmatrisen i `docs/DRIFT.md`.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from drift import (
    ArbetareAvbrutenFel,
    ArbetareFel,
    AutoDiskStadare,
    AutoStadareFel,
    JobbKo,
    JobbKontext,
    JobbKorUtfall,
    JobbPost,
    kor_arbetare,
    las_auto_stadare_konfig,
    los_enhet,
    los_ko_databas,
    sanera_feltext,
    standard_arbetare_id,
    starta_auto_diskstadare_om_aktiv,
)
from floden.cli import main
from gemensamt.lokal_diskstadare import DiskStadaResultat, LokalDiskKonfig
from testsvit.k1_io import skriv_geotiff

REPO = Path(__file__).resolve().parents[2]


def _skapa_ko_med_kvot(tmp_path: Path, namn: str = "ko.sqlite") -> JobbKo:
    ko = JobbKo(tmp_path / namn)
    ko.satt_kvot("org-1", max_aktiva_jobb=20, manadsbudget_sek=1000.0)
    return ko


def _skapa_minimal_k1_mapp(mapp: Path) -> Path:
    """Skapar en minimal syntetisk 6x6 K1-mapp (snabb för 2D-simulering)."""
    mapp.mkdir(parents=True, exist_ok=True)
    z = np.full((6, 6), 10.0, dtype=np.float32)
    z[2:4, 2:4] = 9.5
    slag = np.full((6, 6), 1, dtype=np.uint8)
    skriv_geotiff(mapp / "mark.tif", z, x_min=100000.0, y_max=6500012.0, pixelstorlek=2.0)
    skriv_geotiff(mapp / "markslag.tif", slag, x_min=100000.0, y_max=6500012.0, pixelstorlek=2.0)
    (mapp / "regn.csv").write_text(
        "tid_s,intensitet_mm_h\n0,30.0\n60,30.0\n120,0.0\n",
        encoding="utf-8",
    )
    return mapp


def _gor_gammal(sokvag: Path, alder_s: float) -> None:
    ts = time.time() - alder_s
    if sokvag.is_dir():
        for fil in sokvag.rglob("*"):
            os.utime(fil, (ts, ts))
    os.utime(sokvag, (ts, ts))


# ------------------------------------------------------------------ Enhetlig arbetarloop (S5k)


def test_arbetare_en_gang_lyckat_jobb_och_tom_ko(tmp_path: Path) -> None:
    ko = _skapa_ko_med_kvot(tmp_path)
    j1 = ko.lagg_till("org-1", "k-1", "p-1", "2d", {"steg": 1}, 10.0)
    j2 = ko.lagg_till("org-1", "k-1", "p-1", "2d", {"steg": 2}, 10.0)

    korda: list[str] = []

    def exek(jobb: JobbPost, ctx: JobbKontext) -> JobbKorUtfall:
        korda.append(jobb.jobb_id)
        assert ctx.enhet == "cpu"
        assert ctx.lagring_typ == "lokal"
        return JobbKorUtfall(
            faktisk_kostnad_sek=3.25,
            resultat_meta={"steg": jobb.nyttolast["steg"], "enhet": ctx.enhet},
        )

    res = kor_arbetare(
        ko,
        arbetare_id="arb-eng",
        enhet="cpu",
        exekverare=exek,
        en_gang=True,
        installera_signaler=False,
    )
    assert res.antal_behandlade == 1
    assert res.antal_klara == 1
    assert res.antal_misslyckade == 0
    assert res.avslutningsorsak == "en_gang"
    assert korda == [j1.jobb_id]

    post1 = ko.hamta_jobb(j1.jobb_id)
    post2 = ko.hamta_jobb(j2.jobb_id)
    assert post1 is not None and post1.status == "klar"
    assert post1.faktisk_kostnad_sek == pytest.approx(3.25)
    assert post1.resultat_meta == {"steg": 1, "enhet": "cpu"}
    assert post2 is not None and post2.status == "ko"

    # Slutför j2 och testa sedan --en-gang på tom kö (ska avsluta direkt utan väntan).
    res2 = kor_arbetare(
        ko,
        arbetare_id="arb-eng",
        enhet="cpu",
        exekverare=exek,
        en_gang=True,
        installera_signaler=False,
    )
    assert res2.antal_klara == 1
    assert res2.avslutningsorsak == "en_gang"

    res_tom = kor_arbetare(
        ko,
        arbetare_id="arb-eng",
        enhet="cpu",
        exekverare=exek,
        en_gang=True,
        intervall_s=60.0,
        installera_signaler=False,
    )
    assert res_tom.antal_behandlade == 0
    assert res_tom.avslutningsorsak == "tom_ko"


def test_arbetare_giftigt_jobb_sanerar_pii_och_fortsatter_till_nasta(tmp_path: Path) -> None:
    ko = _skapa_ko_med_kvot(tmp_path)
    j_gift = ko.lagg_till("org-1", "k-1", "p-1", "2d", {"giftigt": True}, 5.0, prioritet=10)
    j_ok = ko.lagg_till("org-1", "k-1", "p-1", "2d", {"giftigt": False}, 5.0, prioritet=1)

    def exek(jobb: JobbPost, _ctx: JobbKontext) -> dict[str, Any]:
        if jobb.nyttolast.get("giftigt"):
            raise RuntimeError(
                "Krasch för anna.andersson@kommun.se med "
                "secret_key=MY_SUPER_SECRET Bearer tok12345 fl_mcp_hemligtoken"
            )
        return {"ok": True}

    res = kor_arbetare(
        ko,
        arbetare_id="arb-gift",
        enhet="cpu",
        exekverare=exek,
        max_jobb=2,
        installera_signaler=False,
    )
    assert res.antal_behandlade == 2
    assert res.antal_misslyckade == 1
    assert res.antal_klara == 1
    assert res.avslutningsorsak == "max_jobb"

    post_gift = ko.hamta_jobb(j_gift.jobb_id)
    assert post_gift is not None
    assert post_gift.status == "misslyckad"
    assert post_gift.feltext is not None
    assert "anna.andersson@kommun.se" not in post_gift.feltext
    assert "MY_SUPER_SECRET" not in post_gift.feltext
    assert "tok12345" not in post_gift.feltext
    assert "fl_mcp_hemligtoken" not in post_gift.feltext
    assert "[redakterad-epost]" in post_gift.feltext

    post_ok = ko.hamta_jobb(j_ok.jobb_id)
    assert post_ok is not None
    assert post_ok.status == "klar"
    assert post_ok.resultat_meta == {"ok": True}


def test_arbetare_lease_heartbeat_fornyar_och_lacker_inga_tradar(tmp_path: Path) -> None:
    ko = _skapa_ko_med_kvot(tmp_path)
    ko.lagg_till("org-1", "k-1", "p-1", "2d", {"langt": True}, 5.0)
    observerade_utgar: list[float] = []

    def langsam_exek(jobb: JobbPost, _ctx: JobbKontext) -> dict[str, Any]:
        for _ in range(4):
            post = ko.hamta_jobb(jobb.jobb_id)
            assert post is not None and post.lease_utgar_utc is not None
            observerade_utgar.append(post.lease_utgar_utc)
            time.sleep(0.08)
        return {"klart": True}

    res = kor_arbetare(
        ko,
        arbetare_id="arb-hb",
        enhet="cpu",
        exekverare=langsam_exek,
        lease_s=2,
        heartbeat_intervall_s=0.05,
        en_gang=True,
        installera_signaler=False,
    )
    assert res.antal_klara == 1
    assert res.antal_heartbeats >= 2
    assert max(observerade_utgar) > observerade_utgar[0]

    # Kontrollera 0 läckta heartbeat-trådar efter avslut.
    kvar_hb = [t for t in threading.enumerate() if t.name.startswith("floden-hb-")]
    assert kvar_hb == []


def test_arbetare_graceful_shutdown_slapper_lease_och_tar_inga_nya_jobb(tmp_path: Path) -> None:
    ko = _skapa_ko_med_kvot(tmp_path)
    j1 = ko.lagg_till("org-1", "k-1", "p-1", "2d", {"nr": 1}, 5.0, prioritet=10, max_forsok=3)
    j2 = ko.lagg_till("org-1", "k-1", "p-1", "2d", {"nr": 2}, 5.0, prioritet=1, max_forsok=3)

    stopp = threading.Event()
    startat = threading.Event()

    def avbrytbar_exek(_jobb: JobbPost, ctx: JobbKontext) -> dict[str, Any]:
        startat.set()
        if ctx.stopp_event.wait(timeout=3.0):
            raise ArbetareAvbrutenFel("Stänger ner rent")
        return {"klar": True}

    resultat_box: list[Any] = []

    def _kor() -> None:
        resultat_box.append(
            kor_arbetare(
                ko,
                arbetare_id="arb-stopp",
                enhet="cpu",
                exekverare=avbrytbar_exek,
                stopp_event=stopp,
                installera_signaler=False,
            )
        )

    trad = threading.Thread(target=_kor)
    trad.start()
    assert startat.wait(timeout=2.0)
    t_stopp = time.monotonic()
    stopp.set()
    trad.join(timeout=3.0)
    assert not trad.is_alive()
    assert (time.monotonic() - t_stopp) < 1.5

    res = resultat_box[0]
    assert res.antal_avbrutna == 1
    assert res.antal_klara == 0
    assert res.avslutningsorsak == "stoppad"

    # Leasen för j1 ska vara släppt omedelbart (status 'ko', inte fast i 'pagaende'!)
    p1 = ko.hamta_jobb(j1.jobb_id)
    p2 = ko.hamta_jobb(j2.jobb_id)
    assert p1 is not None and p1.status == "ko"
    assert p1.arbetare_id is None
    assert p1.lease_utgar_utc is None
    assert p2 is not None and p2.status == "ko"

    # En ny arbetare ska omedelbart kunna plocka upp j1 och slutföra det.
    res_ny = kor_arbetare(
        ko,
        arbetare_id="arb-ny",
        enhet="cpu",
        exekverare=lambda _j, _c: {"aterhamtad": True},
        en_gang=True,
        installera_signaler=False,
    )
    assert res_ny.antal_klara == 1
    p1_efter = ko.hamta_jobb(j1.jobb_id)
    assert p1_efter is not None and p1_efter.status == "klar"


def test_arbetare_sigterm_aterstaller_signalhanterare(tmp_path: Path) -> None:
    ko = _skapa_ko_med_kvot(tmp_path)
    tidigare_term = signal.getsignal(signal.SIGTERM)
    tidigare_int = signal.getsignal(signal.SIGINT)

    def skicka_term() -> None:
        time.sleep(0.08)
        os.kill(os.getpid(), signal.SIGTERM)

    trigger = threading.Thread(target=skicka_term, daemon=True)
    trigger.start()
    res = kor_arbetare(
        ko,
        arbetare_id="arb-sig",
        enhet="cpu",
        intervall_s=5.0,
        installera_signaler=True,
    )
    trigger.join(timeout=2.0)

    assert res.avslutningsorsak == "stoppad"
    assert signal.getsignal(signal.SIGTERM) is tidigare_term
    assert signal.getsignal(signal.SIGINT) is tidigare_int


def test_arbetare_enhet_auto_faller_till_cpu_och_cuda_fail_fast(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import gemensamt.gpu as gpu_mod

    monkeypatch.setattr(gpu_mod, "_hamta_cupy", lambda: None)

    assert los_enhet("auto") == "cpu"
    assert los_enhet("cpu") == "cpu"
    with pytest.raises(ArbetareFel, match="CUDA-GPU begärdes"):
        los_enhet("cuda")

    # CLI med --enhet auto ska lyckas och rapportera cpu.
    ko_rot = tmp_path / "ko"
    kod_auto = main(
        [
            "drift",
            "arbetare",
            "--ko-rot",
            str(ko_rot),
            "--enhet",
            "auto",
            "--en-gang",
            "--json",
        ]
    )
    assert kod_auto == 0
    svar_auto = json.loads(capsys.readouterr().out)
    assert svar_auto["enhet"] == "cpu"
    assert svar_auto["lagring_typ"] == "lokal"
    assert svar_auto["avslutningsorsak"] == "tom_ko"

    # CLI med --enhet cuda utan CuPy ska ge fail-fast (exitkod 2 + tydligt svenskt fel).
    kod_cuda = main(
        [
            "drift",
            "arbetare",
            "--ko-rot",
            str(ko_rot),
            "--enhet",
            "cuda",
            "--en-gang",
        ]
    )
    assert kod_cuda == 2
    err = capsys.readouterr().err
    assert "CUDA" in err
    assert "CuPy" in err


def test_arbetare_enhet_auto_valjer_cuda_nar_cupy_finns(monkeypatch: pytest.MonkeyPatch) -> None:
    import gemensamt.gpu as gpu_mod

    falsk_cupy = types.SimpleNamespace(name="fake-cupy")
    monkeypatch.setattr(gpu_mod, "_hamta_cupy", lambda: falsk_cupy)
    assert los_enhet("auto") == "cuda"
    assert los_enhet("cuda") == "cuda"
    assert los_enhet("cpu") == "cpu"


def test_arbetare_cli_en_gang_kor_riktigt_2d_jobb_via_standard_exekverare(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    k1 = _skapa_minimal_k1_mapp(tmp_path / "k1_mini")
    ko_rot = tmp_path / "drift_ko"
    db_fil = los_ko_databas(ko_rot)
    ko = JobbKo(db_fil)
    ko.satt_kvot("org-1", max_aktiva_jobb=5, manadsbudget_sek=100.0)
    ut_mapp = tmp_path / "k6_ut"
    jobb = ko.lagg_till(
        "org-1",
        "k-1",
        "p-1",
        "2d",
        {
            "k1_mapp": str(k1),
            "ut_mapp": str(ut_mapp),
            "slut_tid_s": 120.0,
        },
        2.0,
    )

    kod = main(
        [
            "drift",
            "arbetare",
            "--ko-rot",
            str(ko_rot),
            "--lagring",
            "lokal",
            "--lagring-rot",
            str(tmp_path / "lagring"),
            "--enhet",
            "cpu",
            "--en-gang",
            "--json",
        ]
    )
    assert kod == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["antal_klara"] == 1
    assert payload["antal_misslyckade"] == 0
    assert payload["avslutningsorsak"] == "en_gang"

    slut = ko.hamta_jobb(jobb.jobb_id)
    assert slut is not None and slut.status == "klar"
    assert slut.resultat_meta is not None
    assert slut.resultat_meta["enhet"] == "cpu"
    assert (ut_mapp / "max_djup.tif").is_file()
    assert (ut_mapp / "run.json").is_file()


def test_arbetare_s3_utan_konfig_fail_fast_och_hjalpare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for var in (
        "FLODEN_S3_TYP",
        "FLODEN_S3_ENDPOINT",
        "FLODEN_S3_BUCKET",
        "FLODEN_S3_ACCESS_KEY",
        "FLODEN_S3_SECRET_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    kod = main(
        [
            "drift",
            "arbetare",
            "--ko-rot",
            str(tmp_path / "ko"),
            "--konfig",
            str(tmp_path / "saknas.toml"),
            "--lagring",
            "s3",
            "--enhet",
            "cpu",
            "--en-gang",
        ]
    )
    assert kod == 2
    assert "Fel i driftarbetare:" in capsys.readouterr().err

    assert "@" not in standard_arbetare_id(hostname="user@host.local", pid=42)
    assert standard_arbetare_id(hostname="---", pid=99) == "arbetare-99"
    assert los_ko_databas(None, miljo={"FLODEN_JOBBKO_ROT": str(tmp_path / "e")}) == (
        tmp_path / "e" / "jobbko.sqlite"
    )
    assert los_ko_databas(tmp_path / "egen.sqlite") == tmp_path / "egen.sqlite"
    assert "[redakterad-epost]" in sanera_feltext("fel för test@exempel.se")


# ------------------------------------------------------------------ AutoDiskStadare


def test_auto_stadare_avstangd_som_standard_och_validerar_env(tmp_path: Path) -> None:
    assert starta_auto_diskstadare_om_aktiv(miljo={}) is None
    assert starta_auto_diskstadare_om_aktiv(miljo={"FLODEN_AUTO_STADA_DISK": "0"}) is None

    konf = las_auto_stadare_konfig(miljo={"FLODEN_AUTO_STADA_DISK": "0"}, cache_rot=tmp_path)
    assert konf.aktiv is False
    assert konf.intervall_timmar == 24.0
    assert konf.vid_start is False

    with pytest.raises(AutoStadareFel):
        las_auto_stadare_konfig(
            miljo={"FLODEN_AUTO_STADA_DISK": "kanske"},
            cache_rot=tmp_path,
        )
    with pytest.raises(AutoStadareFel):
        las_auto_stadare_konfig(
            miljo={"FLODEN_AUTO_STADA_DISK": "1", "FLODEN_AUTO_STADA_INTERVALL_TIMMAR": "0"},
            cache_rot=tmp_path,
        )
    with pytest.raises(AutoStadareFel):
        las_auto_stadare_konfig(
            miljo={"FLODEN_AUTO_STADA_DISK": "1", "FLODEN_AUTO_STADA_INTERVALL_TIMMAR": "-2"},
            cache_rot=tmp_path,
        )


def test_auto_stadare_periodisk_stadning_skyddar_aktiv_och_raderar_gammal(tmp_path: Path) -> None:
    cache_rot = tmp_path / "cache"
    korningar = cache_rot / "webb" / "korningar"

    # Gammal avslutad körning (30 dagar gammal) -> ska raderas.
    gammal = korningar / "k-gammal"
    gammal.mkdir(parents=True)
    (gammal / "korning.json").write_text(
        json.dumps({"korning_id": "k-gammal", "status": "klar"}), encoding="utf-8"
    )
    (gammal / "data.bin").write_bytes(b"x" * 4096)
    _gor_gammal(gammal, 30 * 86400)

    # Aktiv pågående körning (även med gammal mtime!) -> måste skyddas.
    aktiv = korningar / "k-aktiv"
    aktiv.mkdir(parents=True)
    (aktiv / "korning.json").write_text(
        json.dumps({"korning_id": "k-aktiv", "status": "kor"}), encoding="utf-8"
    )
    (aktiv / "data.bin").write_bytes(b"y" * 4096)
    _gor_gammal(aktiv, 30 * 86400)

    # Gammal övergiven tempfil -> ska raderas.
    tmp_fil = cache_rot / ".floden_overgiven.tmp"
    tmp_fil.write_bytes(b"z" * 1024)
    _gor_gammal(tmp_fil, 3600)

    disk_konfig = LokalDiskKonfig(
        cache_rot=cache_rot,
        max_gb=1.0,
        max_alder_dagar=14.0,
        grace_minuter=5.0,
    )
    svep_klart = threading.Event()

    stadare = AutoDiskStadare(
        disk_konfig,
        intervall_s=0.05,
        vid_start=True,
        start_fordrojning_s=0.0,
        pa_resultat=lambda _r: svep_klart.set(),
    )
    try:
        assert stadare.starta() is True
        assert stadare.starta() is False  # idempotent
        assert svep_klart.wait(timeout=2.0)
        assert stadare.antal_svep >= 1
        assert stadare.antal_fel == 0
        assert stadare.senaste_resultat is not None
        assert stadare.senaste_resultat.antal_raderade >= 2
    finally:
        stadare.stoppa()

    assert not gammal.exists()
    assert not tmp_fil.exists()
    assert aktiv.is_dir()
    assert (aktiv / "data.bin").is_file()


def test_auto_stadare_fel_isolering_och_omedelbar_stoppa(tmp_path: Path) -> None:
    disk_konfig = LokalDiskKonfig(cache_rot=tmp_path / "cache")
    anrop = {"n": 0}
    klart_efter_fel = threading.Event()

    def flakig_stadare(_k: LokalDiskKonfig) -> DiskStadaResultat:
        anrop["n"] += 1
        if anrop["n"] == 1:
            raise OSError("Tillfälligt I/O-fel för användare anna@kommun.se")
        klart_efter_fel.set()
        return DiskStadaResultat(
            torrkor=False,
            fore_bytes=0,
            efter_bytes=0,
            frigjorda_bytes=0,
            antal_raderade=0,
            antal_skyddade=0,
            per_kategori={},
            varningar=(),
        )

    stadare = AutoDiskStadare(
        disk_konfig,
        intervall_s=0.04,
        vid_start=True,
        start_fordrojning_s=0.0,
        stadare_fn=flakig_stadare,
    )
    try:
        stadare.starta()
        assert klart_efter_fel.wait(timeout=2.0)
        assert stadare.ar_igang is True
        assert stadare.antal_fel == 1
        assert stadare.antal_svep >= 1
    finally:
        stadare.stoppa()

    # Direkt test av att fel loggas PII-fritt i senaste_fel.
    stadare_fel = AutoDiskStadare(
        disk_konfig,
        stadare_fn=lambda _k: (_ for _ in ()).throw(OSError("fel anna@kommun.se")),
    )
    assert stadare_fel.kor_ett_svep() is None
    assert stadare_fel.senaste_fel is not None
    assert "anna@kommun.se" not in stadare_fel.senaste_fel
    assert "[redakterad-epost]" in stadare_fel.senaste_fel

    # Långt intervall: stoppa() ska vakna på threading.Event och returnera < 0.2 s!
    lang = AutoDiskStadare(disk_konfig, intervall_s=3600.0, vid_start=False)
    lang.starta()
    assert lang.ar_igang is True
    t0 = time.monotonic()
    lang.stoppa()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.2
    assert lang.ar_igang is False


def test_webb_cli_startar_och_stoppar_auto_stadare_enligt_miljo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    konfig = tmp_path / "config.toml"
    konfig.write_text('[sokvagar]\ncache = "cache"\n', encoding="utf-8")

    # 1) Avstängd (FLODEN_AUTO_STADA_DISK=0): ingen auto-städtråd under uvicorn.run.
    monkeypatch.setenv("FLODEN_AUTO_STADA_DISK", "0")
    observerat_av: dict[str, bool] = {}

    def falsk_run_av(_app: Any, **_kw: Any) -> None:
        observerat_av["har_trad"] = any(
            t.name == "floden-auto-diskstadare" for t in threading.enumerate()
        )

    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=falsk_run_av))
    kod_av = main(
        [
            "webb",
            "--ingen-bygg",
            "--server",
            "asgi",
            "--ingen-mcp",
            "--konfig",
            str(konfig),
        ]
    )
    assert kod_av == 0
    assert observerat_av["har_trad"] is False

    # 2) Aktiv (FLODEN_AUTO_STADA_DISK=1): tråd igång under servern, stoppad i finally.
    monkeypatch.setenv("FLODEN_AUTO_STADA_DISK", "1")
    monkeypatch.setenv("FLODEN_AUTO_STADA_INTERVALL_TIMMAR", "12")
    observerat_pa: dict[str, bool] = {}

    def falsk_run_pa(_app: Any, **_kw: Any) -> None:
        observerat_pa["har_trad"] = any(
            t.name == "floden-auto-diskstadare" for t in threading.enumerate()
        )

    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=falsk_run_pa))
    kod_pa = main(
        [
            "webb",
            "--ingen-bygg",
            "--server",
            "asgi",
            "--ingen-mcp",
            "--konfig",
            str(konfig),
        ]
    )
    assert kod_pa == 0
    assert observerat_pa["har_trad"] is True
    assert not any(t.name == "floden-auto-diskstadare" for t in threading.enumerate())


# ------------------------------------------------------------------ CLI, Compose och docs/DRIFT.md


def test_cli_drift_help_och_stada_disk_torrkor_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for argv in (
        ["drift", "--help"],
        ["drift", "arbetare", "--help"],
        ["drift", "stada-disk", "--help"],
    ):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 0
        ut = capsys.readouterr().out
        assert len(ut) > 20

    cache_rot = tmp_path / "cache"
    tmp_fil = cache_rot / ".floden_test.tmp"
    cache_rot.mkdir(parents=True)
    tmp_fil.write_bytes(b"a" * 2048)
    _gor_gammal(tmp_fil, 3600)

    kod = main(
        [
            "drift",
            "stada-disk",
            "--konfig",
            str(tmp_path / "saknas.toml"),
            "--cache-rot",
            str(cache_rot),
            "--max-gb",
            "5",
            "--max-alder-dagar",
            "7",
            "--torrkor",
            "--json",
        ]
    )
    assert kod == 0
    data = json.loads(capsys.readouterr().out)
    assert data["torrkor"] is True
    assert data["antal_raderade"] >= 1
    assert "disk" in data and data["disk"]["ledig_gb"] > 0.0
    # Torrkor får aldrig radera filen.
    assert tmp_fil.is_file()


def test_docker_compose_och_drift_matris_innehall() -> None:
    compose = (REPO / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert 'FLODEN_AUTO_STADA_DISK: "${FLODEN_AUTO_STADA_DISK:-1}"' in compose
    assert 'FLODEN_AUTO_STADA_INTERVALL_TIMMAR: "${FLODEN_AUTO_STADA_INTERVALL_TIMMAR:-24}"' in (
        compose
    )

    drift = (REPO / "docs" / "DRIFT.md").read_text(encoding="utf-8")
    for nyckel in (
        "Profil A",
        "Profil B",
        "Profil C",
        "FLODEN_EXEKUTOR=process",
        "FLODEN_JOBB_MAX_RAM_MB=2200",
        "FLODEN_JOBB_MAX_TID_S=300",
        "FLODEN_MAX_SAMTIDIGA_BERAKNINGAR=2",
        "FLODEN_LOKAL_DISK_MAX_GB=12",
        "FLODEN_AUTO_STADA_DISK=1",
        "FLODEN_MAX_SANKGRAF_CELLER=4500000",
        "FLODEN_MAX_DAGSLJUS_CELLER=4500000",
        "FLODEN_MAX_EKOLOGI_CELLER=1000000",
        "FLODEN_MAX_PNG_PIXLAR=2048",
        "data/forberaknat/",
        "floden drift arbetare --lagring s3 --enhet cuda",
        "floden drift stada-disk",
        "/api/halsa",
        "minne_rss_mb",
        "minne_grans_mb",
        "disk_ledig_gb",
        "exekutor",
        "507",
        "503",
    ):
        assert nyckel in drift, f"Saknas i docs/DRIFT.md: {nyckel}"
