"""Hårda krasch-, minnes-, timeout-, nedstängnings- och samtidighetstester för S-O1.

Verifierar:
1. Riktig K20-körning i barnprocess (`FLODEN_EXEKUTOR=process`) skriver resultat + `run.json`
   och sätter trådtak för BLAS/OpenMP (aldrig `RLIMIT_AS`).
2. Barnprocess som överskrider `max_ram_mb` dödas av aktiv RSS-vakt (eller kernel OOM `-9`/`137`),
   ger tydligt svenskt felmeddelande + `fel_forklaring` i `korning.json`, medan webbprocessen lever
   vidare och `/api/halsa` svarar `200 OK`.
3. Barnprocess som överskrider `max_tid_s` dödas rent med `SubprocTimeoutFel` och frigör köplatsen.
4. `FragaTjanst.stang()` under pågående barnprocess avslutar barnet rent utan zombie-process.
5. Pre-flight diskvakt i `starta_korning()` avvisar med HTTP `507` utan att skriva halva filer.
6. `/api/halsa` exponerar `minne_rss_mb > 0`, `minne_grans_mb`, `disk_ledig_gb >= 0`, `exekutor`
   helt utan PII.
7. `FLODEN_MAX_SAMTIDIGA_BERAKNINGAR=2` i ASGI (och `floden webb --server asgi`) begränsar tunga
   samtidiga POST-anrop och ger `503 + Retry-After` på det tredje.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import types
from collections import namedtuple
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from webb import subproc_kor
from webb.api import FlodenApiApp
from webb.asgi import FlodenAsgiApp
from webb.felforklaring import forklara_fel
from webb.fragor import FragaTjanst
from webb.subproc_kor import (
    BARN_TRAD_ENV,
    SubprocMinneFel,
    bygg_barnmiljo,
    cgroup_minne_grans_mb,
    overvaka_barnprocess,
)

REPO = Path(__file__).resolve().parents[2]
_DiskUsage = namedtuple("_DiskUsage", ("total", "used", "free"))


def _json_svar(app: FlodenApiApp, metod: str, sokvag: str) -> tuple[int, dict[str, Any]]:
    status, _, kropp = app.hantera_forfragan(metod, sokvag)
    return status, json.loads(kropp.decode("utf-8"))


def _vanta_slut(app: FlodenApiApp, korning_id: str, *, sekunder: float = 60.0) -> dict[str, Any]:
    slut = time.monotonic() + sekunder
    while time.monotonic() < slut:
        status, data = _json_svar(app, "GET", f"/api/korningar/{korning_id}")
        assert status == 200, data
        if data["status"] in ("klar", "fel"):
            return data
        time.sleep(0.05)
    raise AssertionError(f"Körning {korning_id} avslutades inte inom {sekunder} s.")


async def _anropa_asgi(
    asgi_app: FlodenAsgiApp,
    metod: str,
    path: str,
    *,
    chunks: Sequence[bytes] = (b"",),
) -> tuple[int, dict[str, str], bytes]:
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": metod,
        "path": path,
        "query_string": b"",
        "headers": [],
    }
    chunk_iter = iter(chunks)
    skickade: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        try:
            del_b = next(chunk_iter)
            return {"type": "http.request", "body": del_b, "more_body": True}
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}

    async def send(meddelande: Mapping[str, Any]) -> None:
        skickade.append(dict(meddelande))

    await asgi_app(scope, receive, send)
    start = next(m for m in skickade if m["type"] == "http.response.start")
    kropp = b"".join(m.get("body", b"") for m in skickade if m["type"] == "http.response.body")
    ut_headers = {
        k.decode("latin-1").lower(): v.decode("latin-1") for k, v in start.get("headers", ())
    }
    return int(start["status"]), ut_headers, kropp


def test_barnmiljo_satter_openblas_och_openmp_till_en_trad() -> None:
    """Barnprocessens miljö tvingar BLAS/OpenMP till 1 tråd för att hindra RAM-explosion."""
    miljo = bygg_barnmiljo()
    for nyckel, forvantat in BARN_TRAD_ENV.items():
        assert miljo[nyckel] == forvantat
    assert miljo["OPENBLAS_NUM_THREADS"] == "1"
    assert miljo["OMP_NUM_THREADS"] == "1"
    assert miljo["MKL_NUM_THREADS"] == "1"
    assert miljo["NUMEXPR_NUM_THREADS"] == "1"


def test_riktig_k20_korning_i_barnprocess_skriver_resultat_och_run_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Riktig K20-körning (`vatten_a1_uppfyllnad`) med `FLODEN_EXEKUTOR=process` går igenom."""
    monkeypatch.setenv("FLODEN_EXEKUTOR", "process")
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[REPO / "exempel" / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    try:
        assert app.fragor.exekutor_lage == "process"
        status, start = _json_svar(app, "POST", "/api/fragor/vatten_a1_uppfyllnad/kor")
        assert status == 202, start
        kid = str(start["korning_id"])

        slut = _vanta_slut(app, kid, sekunder=90.0)
        assert slut["status"] == "klar", slut
        assert slut["fel"] is None
        assert slut["framsteg"] == {"klara": 2, "totalt": 2}

        kmapp = tmp_path / "korningar" / kid
        utmapp = kmapp / "resultat"
        assert (utmapp / "sammanstallning.json").is_file()
        assert (utmapp / "run.json").is_file()
        assert (kmapp / "framsteg.json").is_file()
        assert list(kmapp.glob("*.tmp")) == []
        assert list(kmapp.glob(".subproc_spec_*")) == []

        framsteg = json.loads((kmapp / "framsteg.json").read_text(encoding="utf-8"))
        assert framsteg["status"] == "klar"
        assert framsteg["klara"] == 2 and framsteg["totalt"] == 2
        assert framsteg["pid"] != os.getpid()

        res_status, resultat = _json_svar(app, "GET", f"/api/korningar/{kid}/resultat")
        assert res_status == 200, resultat
        varden = {x["scenario"]: x["varden"] for x in resultat["tabeller"][0]["rader"]}
        assert varden["fore"]["volymfel_rel"] < 1e-9
        assert varden["efter"]["max_djup_m"] > 0.0
    finally:
        app.stang()


def test_rss_vakt_dodar_minneshungrig_barnprocess_och_webbprocessen_lever_vidare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Riktig barnprocess som allokerar > 64 MB dödas av RSS-vakten; `/api/halsa` ger 200 OK."""
    monkeypatch.setenv("FLODEN_EXEKUTOR", "process")
    monkeypatch.setenv("FLODEN_JOBB_MAX_RAM_MB", "64")
    monkeypatch.setenv("FLODEN_JOBB_MAX_TID_S", "30")

    kod = (
        "import time\n"
        "block = bytearray(b'\\x01') * (130 * 1024 * 1024)\n"
        "time.sleep(15)\n"
        "assert len(block) > 0\n"
    )
    monkeypatch.setattr(
        subproc_kor, "_bygg_barnkommando", lambda _spec: [sys.executable, "-c", kod]
    )

    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[REPO / "exempel" / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    try:
        st, start = _json_svar(app, "POST", "/api/fragor/vatten_a1_uppfyllnad/kor")
        assert st == 202, start
        kid = str(start["korning_id"])

        slut = _vanta_slut(app, kid, sekunder=20.0)
        assert slut["status"] == "fel", slut
        assert slut["fel"] is not None
        assert "SubprocMinneFel" in slut["fel"]
        assert "mer minne än serverns gräns" in slut["fel"]
        assert slut["fel_forklaring"] is not None
        assert "arbetsminne" in slut["fel_forklaring"].lower()

        korning_json = json.loads(
            (tmp_path / "korningar" / kid / "korning.json").read_text(encoding="utf-8")
        )
        assert korning_json["status"] == "fel"
        assert "SubprocMinneFel" in korning_json["fel"]
        assert app.fragor._subproc.aktiva_pids() == []  # noqa: SLF001

        # Föräldraprocessen lever vidare ostört och /api/halsa svarar 200 OK.
        h_st, halsa = _json_svar(app, "GET", "/api/halsa")
        assert h_st == 200
        assert halsa["status"] == "ok"
        assert halsa["ko"]["pagaende"] == 0
    finally:
        app.stang()


def test_oom_sigkill_och_137_ger_tydligt_minnesfel() -> None:
    """Om kernel OOM-dödar barnprocessen (`-9` eller `137`) kastas `SubprocMinneFel` på svenska."""
    with pytest.raises(SubprocMinneFel) as exc_kill:
        overvaka_barnprocess(
            [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"],
            max_ram_mb=512.0,
            max_tid_s=10.0,
        )
    assert "mer minne än serverns gräns" in str(exc_kill.value)

    with pytest.raises(SubprocMinneFel) as exc_137:
        overvaka_barnprocess(
            [sys.executable, "-c", "import sys; sys.exit(137)"],
            max_ram_mb=512.0,
            max_tid_s=10.0,
        )
    assert "mer minne än serverns gräns" in str(exc_137.value)


def test_timeout_dodar_hangande_barnprocess_och_frigor_koplats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Barnprocess som sover längre än `max_tid_s=1.0` dödas rent och frigör köplatsen."""
    monkeypatch.setenv("FLODEN_EXEKUTOR", "process")
    monkeypatch.setenv("FLODEN_JOBB_MAX_TID_S", "1.0")
    monkeypatch.setenv("FLODEN_JOBB_MAX_RAM_MB", "1024")

    ursprunglig_bygg = subproc_kor._bygg_barnkommando  # noqa: SLF001
    monkeypatch.setattr(
        subproc_kor,
        "_bygg_barnkommando",
        lambda _spec: [sys.executable, "-c", "import time; time.sleep(30)"],
    )

    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[REPO / "exempel" / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    try:
        t0 = time.monotonic()
        st, start = _json_svar(app, "POST", "/api/fragor/vatten_a1_uppfyllnad/kor")
        assert st == 202, start
        slut = _vanta_slut(app, str(start["korning_id"]), sekunder=10.0)
        kortid = time.monotonic() - t0

        assert kortid < 8.0, f"Timeout dröjde för länge ({kortid:.2f} s)"
        assert slut["status"] == "fel", slut
        assert "SubprocTimeoutFel" in (slut["fel"] or "")
        assert "tidsgräns" in (slut["fel"] or "").lower()
        assert slut["fel_forklaring"] is not None
        assert app.fragor.ko_status()["pagaende"] == 0
        assert app.fragor._subproc.aktiva_pids() == []  # noqa: SLF001

        # Köplatsen är fri: en ny körning kan startas direkt efter timeout.
        monkeypatch.setattr(subproc_kor, "_bygg_barnkommando", ursprunglig_bygg)
        app.fragor.jobb_max_tid_s = 60.0
        st2, start2 = _json_svar(app, "POST", "/api/fragor/vatten_a1_uppfyllnad/kor")
        assert st2 == 202, start2
        assert start2["ateranvand"] is False
        slut2 = _vanta_slut(app, str(start2["korning_id"]), sekunder=60.0)
        assert slut2["status"] == "klar", slut2
    finally:
        app.stang()


def test_stang_avslutar_pagaende_barnprocess_rent_utan_zombie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`FragaTjanst.stang()` under aktiv barnprocess avslutar den direkt utan zombie."""
    monkeypatch.setenv("FLODEN_EXEKUTOR", "process")
    monkeypatch.setenv("FLODEN_JOBB_MAX_TID_S", "60")

    fanga_proc: list[subprocess.Popen[bytes]] = []
    startad = threading.Event()
    ursprunglig_popen = subproc_kor.subprocess.Popen

    def _spion_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        proc: subprocess.Popen[bytes] = ursprunglig_popen(*args, **kwargs)
        fanga_proc.append(proc)
        startad.set()
        return proc

    monkeypatch.setattr(
        subproc_kor,
        "_bygg_barnkommando",
        lambda _spec: [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    monkeypatch.setattr(subproc_kor.subprocess, "Popen", _spion_popen)

    tjanst = FragaTjanst(
        rot=REPO,
        fragemappar=[REPO / "exempel" / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    start = tjanst.starta_korning("vatten_a1_uppfyllnad")
    kid = str(start["korning_id"])
    assert startad.wait(timeout=10.0), "Barnprocessen startade aldrig."
    assert len(fanga_proc) == 1
    proc = fanga_proc[0]
    assert proc.poll() is None

    t0 = time.monotonic()
    tjanst.stang(vanta=True)
    dt = time.monotonic() - t0

    assert dt < 5.0, f"stang() hängde i {dt:.2f} s"
    assert proc.poll() is not None, "Barnprocessen lever fortfarande (zombie/läcka)!"
    assert tjanst._subproc.aktiva_pids() == []  # noqa: SLF001
    with pytest.raises(ProcessLookupError):
        os.kill(proc.pid, 0)

    status = tjanst.korning_status(kid)
    assert status["status"] == "fel"
    assert "stoppades" in (status["fel"] or "").lower()


def test_preflight_diskvakt_ger_507_utan_att_skriva_halva_filer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """För lite ledig disk avvisas direkt i `starta_korning()` med HTTP 507 utan diskskrivning."""
    monkeypatch.setenv("FLODEN_MIN_LEDIG_DISK_MB", "500")
    korningsmapp = tmp_path / "korningar"
    cachemapp = tmp_path / "cache"
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[REPO / "exempel" / "fragor"],
        cachemapp=cachemapp,
        korningsmapp=korningsmapp,
    )
    try:
        # Simulera bara 100 MB ledig disk (< 500 MB).
        monkeypatch.setattr(
            shutil,
            "disk_usage",
            lambda _path: _DiskUsage(
                total=10 * 1024**3,
                used=10 * 1024**3 - 100 * 1024**2,
                free=100 * 1024**2,
            ),
        )
        status, svar = _json_svar(app, "POST", "/api/fragor/vatten_a1_uppfyllnad/kor")
        assert status == 507, svar
        assert svar["status"] == 507
        assert "diskutrymme" in svar["fel"].lower()
        assert not korningsmapp.exists()
        assert not cachemapp.exists()
        assert app.fragor.ko_status()["koade"] == 0
        assert app.fragor.ko_status()["pagaende"] == 0
    finally:
        app.stang()


def test_halsa_innehaller_driftsmatt_utan_pii(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/api/halsa` returnerar `minne_rss_mb > 0`, `disk_ledig_gb >= 0`, `exekutor` och 0 PII."""
    monkeypatch.delenv("FLODEN_EXEKUTOR", raising=False)
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        cachemapp=tmp_path / "hemlig_anvandare_cache",
        korningsmapp=tmp_path / "korningar",
    )
    try:
        status, _, kropp = app.hantera_forfragan("GET", "/api/halsa")
        assert status == 200
        text = kropp.decode("utf-8")
        data = json.loads(text)

        assert data["status"] == "ok"
        assert isinstance(data["minne_rss_mb"], int | float)
        assert data["minne_rss_mb"] > 0.0
        assert data["minne_grans_mb"] is None or (
            isinstance(data["minne_grans_mb"], int | float) and data["minne_grans_mb"] > 0.0
        )
        assert isinstance(data["disk_ledig_gb"], int | float)
        assert data["disk_ledig_gb"] >= 0.0
        assert data["exekutor"] == "trad"

        # 0 PII / inga interna sökvägar eller användarnamn i /api/halsa
        assert str(tmp_path) not in text
        assert "hemlig_anvandare_cache" not in text
        assert str(Path.home()) not in text

        # Växling till process-exekutor syns direkt
        monkeypatch.setenv("FLODEN_EXEKUTOR", "process")
        assert app.halsa()["exekutor"] == "process"
    finally:
        app.stang()

    # Unit-test av cgroup v2-tolkning (3 GiB samt "max")
    v2_3g = tmp_path / "mem_3g"
    v2_3g.write_text(f"{3 * 1024**3}\n", encoding="utf-8")
    assert cgroup_minne_grans_mb(v2_fil=v2_3g, v1_fil=tmp_path / "saknas") == 3072.0

    v2_max = tmp_path / "mem_max"
    v2_max.write_text("max\n", encoding="utf-8")
    assert cgroup_minne_grans_mb(v2_fil=v2_max, v1_fil=tmp_path / "saknas") is None


def test_asgi_floden_max_samtidiga_berakningar_2_ger_503_pa_tredje(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`FLODEN_MAX_SAMTIDIGA_BERAKNINGAR=2` tillåter 2 samtidiga tunga POST och ger 503 på nr 3."""
    monkeypatch.setenv("FLODEN_MAX_SAMTIDIGA_BERAKNINGAR", "2")

    class _ParallellSpy:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self.aktiva = 0
            self.tva_inne = threading.Event()
            self.slapp = threading.Event()

        def hantera_http(
            self,
            metod: str,
            sokvag: str,
            rubriker: Mapping[str, str],
            las_kropp: Callable[[], bytes],
        ) -> tuple[int, list[tuple[str, str]], bytes]:
            _ = las_kropp()
            if metod == "POST":
                with self._lock:
                    self.aktiva += 1
                    if self.aktiva >= 2:
                        self.tva_inne.set()
                try:
                    self.slapp.wait(timeout=10.0)
                finally:
                    with self._lock:
                        self.aktiva -= 1
            return 200, [("Content-Type", "application/json; charset=utf-8")], b'{"ok":true}'

    spy = _ParallellSpy()
    asgi_app = FlodenAsgiApp(spy, ko_timeout_s=0.05)  # type: ignore[arg-type]
    assert asgi_app.max_samtidiga_berakningar == 2

    async def _kor() -> None:
        p1 = asyncio.create_task(_anropa_asgi(asgi_app, "POST", "/api/kor", chunks=(b"{}",)))
        p2 = asyncio.create_task(_anropa_asgi(asgi_app, "POST", "/api/kor", chunks=(b"{}",)))
        deadline = time.monotonic() + 5.0
        try:
            while not spy.tva_inne.is_set():
                assert time.monotonic() < deadline, "Två samtidiga tunga POST kom inte in i tid"
                await asyncio.sleep(0.01)

            # GET blockeras inte
            g_st, _, _ = await _anropa_asgi(asgi_app, "GET", "/api/halsa")
            assert g_st == 200

            # Tredje samtidiga tunga POST avvisas med 503 + Retry-After
            p3_st, p3_h, p3_kropp = await _anropa_asgi(
                asgi_app, "POST", "/api/kor", chunks=(b"{}",)
            )
            assert p3_st == 503
            assert p3_h["retry-after"] == "5"
            assert json.loads(p3_kropp)["status_kod"] == 503
        finally:
            spy.slapp.set()

        r1, r2 = await asyncio.gather(p1, p2)
        assert r1[0] == 200 and r2[0] == 200

    asyncio.run(_kor())


def test_cli_webb_asgi_laser_floden_max_samtidiga_berakningar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`floden webb --server asgi` utan `--max-samtidiga` använder miljövariabeln."""
    from floden.cli import main

    monkeypatch.setenv("FLODEN_MAX_SAMTIDIGA_BERAKNINGAR", "2")
    anrop: dict[str, Any] = {}
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        types.SimpleNamespace(run=lambda app, **kw: anrop.update(app=app, **kw)),
    )
    kod = main(["webb", "--ingen-bygg", "--server", "asgi", "--ingen-mcp"])
    assert kod == 0
    assert isinstance(anrop["app"], FlodenAsgiApp)
    assert anrop["app"].max_samtidiga_berakningar == 2


def test_felforklaring_och_docker_compose_cx23_forval() -> None:
    """Klarspråksförklaringar täcker minne/timeout/celltak och Compose sätter CX23-förval."""
    for feltext in (
        "SubprocMinneFel: Beräkningen krävde mer minne än serverns gräns",
        "MemoryError: unable to allocate array",
        "SubprocTimeoutFel: Beräkningen överskred serverns tidsgräns (300 s)",
        "LosarFel: överstiger serverns minnesgräns (FLODEN_MAX_SANKGRAF_CELLER)",
    ):
        forkl = forklara_fel(feltext)
        assert forkl is not None and len(forkl) > 20, feltext

    compose = (REPO / "docker-compose.prod.yml").read_text(encoding="utf-8")
    for rad in (
        "FLODEN_EXEKUTOR: ${FLODEN_EXEKUTOR:-process}",
        "FLODEN_MAX_SAMTIDIGA_BERAKNINGAR: ${FLODEN_MAX_SAMTIDIGA_BERAKNINGAR:-2}",
        "FLODEN_JOBB_MAX_RAM_MB: ${FLODEN_JOBB_MAX_RAM_MB:-2200}",
        "FLODEN_JOBB_MAX_TID_S: ${FLODEN_JOBB_MAX_TID_S:-300}",
        "FLODEN_MIN_LEDIG_DISK_MB: ${FLODEN_MIN_LEDIG_DISK_MB:-500}",
        "OPENBLAS_NUM_THREADS: ${OPENBLAS_NUM_THREADS:-1}",
        "OMP_NUM_THREADS: ${OMP_NUM_THREADS:-1}",
        "MKL_NUM_THREADS: ${MKL_NUM_THREADS:-1}",
    ):
        assert rad in compose, f"Saknas i docker-compose.prod.yml: {rad}"
