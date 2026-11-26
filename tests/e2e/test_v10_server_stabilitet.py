"""Integrerad stress- och feltillstånds-E2E för serverstabilitet på pressad VPS (Våg 9–10).

Verifierar skarpt end-to-end mot riktig Våg 9–10-kod (0 nätverksanrop, 100 % deterministisk,
alla interna geometrier i SWEREF 99 18 00 / EPSG:3011 och RH 2000):

1. **OOM / SIGKILL & RSS-vakt i barnprocess (`subproc_kor` + `FragaTjanst` + `/api/halsa`):**
   - Barnprocess dödad av `SIGKILL` (`-9`) samt barnprocess som spränger låg
     `FLODEN_JOBB_MAX_RAM_MB` ger tydliga svenska minnesfel och klarspråksförklaring.
   - Webbserverns `/api/halsa` svarar fortsatt `200 OK` med giltig numerisk telemetri
     (`minne_rss_mb`, `disk_ledig_gb`, `exekutor == "process"`) och 0 PII.
   - Nästa friska K20-jobb i samma `FragaTjanst` lyckas normalt med exakt volymbalans.
2. **Timeout-kill (hel processgrupp) & zombie-fri `FragaTjanst.stang()`:**
   - Hängande barnprocess som även startar ett barnbarn dödas helt (hela processgruppen)
     över kort `FLODEN_JOBB_MAX_TID_S` med tydligt svenskt timeout-meddelande.
   - `FragaTjanst.stang()` under aktiv barnprocess + köat jobb avslutar hela processgruppen
     utan att hänga, lämnar 0 zombies och markerar aktiva/köade jobb som avbrutna.
3. **Låg ledig disk (`507`) → `stada_lokal_disk` → återhämtning:**
   - Pre-flight diskvakt (`FLODEN_MIN_LEDIG_DISK_MB`) ger HTTP `507` utan halvskrivna filer.
   - `stada_lokal_disk` över blandad VPS-katalog rensar gammal cache ned till ≤ 85 % medan
     pågående `kor`/`ko`-körningar, `demo.json`, `lage-idag.json`, `scenarier.json`,
     `data/konto`, `data/lokal` och symlänksmål utanför `cache_rot` förblir 100 % orörda.
   - Efter frigjort diskutrymme lyckas nästa körning i samma `FlodenApiApp`.
4. **ASGI-samtidighetstak (`FLODEN_MAX_SAMTIDIGA_BERAKNINGAR=2`) under belastning:**
   - Riktig `FlodenAsgiApp` + `FlodenApiApp` släpper in 2 samtidiga tunga POST-anrop och
     avvisar 3:e (och 4:e) med HTTP `503` + `Retry-After`, medan `/api/halsa` svarar `200 OK`
     direkt och `lifespan.shutdown` stänger rent.
5. **Skadad / gallrad `run.json` i äldre körning:**
   - Borttagen samt trasig/trunkerad `run.json` i avslutad körning (både `fragor.resultat`
     och `kartverktyg.status` / `status_lage_idag` via Python och HTTP) ger aldrig
     ohanterad `FileNotFoundError` eller HTTP 500.
6. **Tvärs-modul RAM-budget & fysikinvarianter:**
   - Vektoriserad `int32` `bygg_sankgraf` på icke-trivial terräng (platta + 2 sänkor + sadel +
     byggnad) med exakt volymbevarande (`volymfel_rel < 1e-9`) och kontrollerad `tracemalloc`-topp.
   - Hårda bytebudgetar i `_HORISONT_CACHE`, `VarmestressLagerCache` och `BullerLagerCache`
     överskrids aldrig vid upprepade insättningar, överskrivningar eller överstora objekt.
   - `ekologi.krets.los_kretspar` på `170×170` (> `STANDARD_CG_TROSKEL = 25 000`) löses rent
     med Jacobi-CG (utan `spsolve`) med strömbalans `< 1e-6`, och `FLODEN_MAX_EKOLOGI_CELLER`
     ger fail-fast `EkologiFel`.
"""

from __future__ import annotations

import asyncio
import gc
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import tracemalloc
from collections import namedtuple
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import dagsljus.floden_losare as fl_mod
from dagsljus.floden_losare import (
    _HorisontCachePost,
    horisont_cache_bytes,
    max_horisont_cache_bytes,
    rensa_horisont_cache,
)
from dagsljus.horisont import Hojdfalt
from ekologi import EkologiFel, MotstandsYta, los_kretspar
from ekologi import krets as krets_mod
from ekologi.krets import STANDARD_CG_TROSKEL
from gemensamt.hash import sha256_fil
from gemensamt.lokal_cache import BYTES_PER_GB, BYTES_PER_MB
from gemensamt.lokal_diskstadare import (
    LokalDiskKonfig,
    LokalDiskStadareFel,
    stada_lokal_disk,
)
from geografi.modell import KONTRAKT_GEOGRAFI, lager_mapp, lagra, skriv_manifest
from markmodell.byggnadsregister import ByggnadsKandidat
from markmodell.hinder_oppningar import HARDGJORT
from markmodell.pipeline import bygg_markmodell_ruta
from markmodell.rutnat import Ruta
from sankgraf.fyllning import statisk_fyllning
from sankgraf.hierarki import _berakna_avrinningsomraden, bygg_sankgraf
from sankgraf.typer import MARKSLAG_BYGGNAD
from testsvit.k1_io import GeoTransformInfo
from webb import subproc_kor
from webb.api import FlodenApiApp
from webb.asgi import FlodenAsgiApp
from webb.cad_buller_api import BullerLagerCache
from webb.fragor import FragaTjanst, KoFullFel
from webb.gyf_utci import VarmestressLagerCache, max_webb_png_cache_bytes
from webb.projektkalla import KONTRAKT_PROJEKT
from webb.resultatlager import Utbredning

REPO = Path(__file__).resolve().parents[2]
_DiskUsage = namedtuple("_DiskUsage", ("total", "used", "free"))
_MB = int(BYTES_PER_MB)
_NU_TS = 1_800_000_000.0
_UTB = Utbredning(x_min=150_000.0, y_min=6_580_000.0, x_max=150_100.0, y_max=6_580_100.0)


# ---------------------------------------------------------------------------
# Hjälpfunktioner
# ---------------------------------------------------------------------------


def _json_svar(
    app: FlodenApiApp,
    metod: str,
    sokvag: str,
    kropp: Mapping[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = b"" if kropp is None else json.dumps(kropp).encode("utf-8")
    status, _, ut = app.hantera_forfragan(metod, sokvag, data)
    return status, json.loads(ut.decode("utf-8")) if ut else {}


def _tidsfaktor() -> float:
    """Skalningsfaktor för väggklockegränser på långsam CI-maskin (`FLODEN_TIDSFAKTOR`)."""
    raw = os.environ.get("FLODEN_TIDSFAKTOR", "1")
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 1.0


def _vanta_slut(app: FlodenApiApp, korning_id: str, *, sekunder: float = 45.0) -> dict[str, Any]:
    slut = time.monotonic() + sekunder * _tidsfaktor()
    while time.monotonic() < slut:
        status, data = _json_svar(app, "GET", f"/api/korningar/{korning_id}")
        assert status == 200, data
        if data["status"] in ("klar", "fel"):
            return data
        time.sleep(0.05)
    raise AssertionError(f"Körning {korning_id} avslutades inte inom {sekunder} s.")


def _vanta_pagaende_noll(app: FlodenApiApp, *, sekunder: float = 3.0) -> None:
    """Väntar tills arbetstrådens `finally`-block i `_kor_fran_ko` nollställt `ko_status()`."""
    slut = time.monotonic() + sekunder * _tidsfaktor()
    while time.monotonic() < slut:
        if app.fragor.ko_status()["pagaende"] == 0:
            return
        time.sleep(0.01)
    assert app.fragor.ko_status()["pagaende"] == 0


def _pid_ar_dod(pid: int, *, vanta_s: float = 2.0) -> bool:
    """Sant om `pid` har avslutats (inte lever och inte ligger kvar som zombie)."""
    slut = time.monotonic() + vanta_s
    while time.monotonic() < slut:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        # På vissa POSIX-system kan en zombie fortfarande svara på kill(pid, 0) kortvarigt.
        ut = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            timeout=1.0,
        )
        stat_str = ut.stdout.strip()
        if not stat_str:
            return True
        if "Z" in stat_str:
            return False
        time.sleep(0.05)
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True


def _satt_tid_rekursivt(sokvag: Path, ts: float) -> None:
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


def _skapa_korning_pa_disk(
    cache_rot: Path,
    korning_id: str,
    *,
    status: str,
    storlek_bytes: int,
    ts: float,
) -> Path:
    mapp = cache_rot / "webb" / "korningar" / korning_id
    (mapp / "resultat").mkdir(parents=True, exist_ok=True)
    (mapp / "korning.json").write_text(
        json.dumps({"korning_id": korning_id, "status": status}, ensure_ascii=False),
        encoding="utf-8",
    )
    (mapp / "resultat" / "data.bin").write_bytes(b"X" * storlek_bytes)
    _satt_tid_rekursivt(mapp, ts)
    return mapp


def _skapa_k22_post(
    cache_rot: Path,
    slag: str,
    hex_tecken: str,
    *,
    klar: bool,
    storlek_bytes: int,
    ts: float,
) -> Path:
    nyckel = (hex_tecken * 64)[:64]
    mapp = cache_rot / "karna" / slag / nyckel[:2] / nyckel
    (mapp / "utdata").mkdir(parents=True, exist_ok=True)
    (mapp / "utdata" / "raster.bin").write_bytes(b"K" * storlek_bytes)
    if klar:
        (mapp / ".klar").write_text('{"kontrakt":"K22/v1"}', encoding="utf-8")
    _satt_tid_rekursivt(mapp, ts)
    return mapp


async def _anropa_asgi(
    asgi_app: FlodenAsgiApp,
    metod: str,
    path: str,
    *,
    kropp: bytes = b"",
    headers: Sequence[tuple[bytes, bytes]] = (),
) -> tuple[int, dict[str, str], bytes]:
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": metod,
        "path": path,
        "query_string": b"",
        "headers": list(headers),
    }
    skickad_kropp = False
    skickade: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal skickad_kropp
        if not skickad_kropp:
            skickad_kropp = True
            return {"type": "http.request", "body": kropp, "more_body": False}
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(meddelande: Mapping[str, Any]) -> None:
        skickade.append(dict(meddelande))

    await asgi_app(scope, receive, send)
    start = next(m for m in skickade if m["type"] == "http.response.start")
    ut_kropp = b"".join(m.get("body", b"") for m in skickade if m["type"] == "http.response.body")
    ut_headers = {
        k.decode("latin-1").lower(): v.decode("latin-1") for k, v in start.get("headers", ())
    }
    return int(start["status"]), ut_headers, ut_kropp


async def _kor_asgi_lifespan(asgi_app: FlodenAsgiApp) -> list[str]:
    halsa_events: list[str] = []
    steg = iter(({"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}))

    async def receive() -> dict[str, Any]:
        return next(steg)

    async def send(meddelande: Mapping[str, Any]) -> None:
        halsa_events.append(str(meddelande["type"]))

    await asgi_app({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)
    return halsa_events


def _syntetisk_horisont_post(tagg: str, *, sid_px: int, antal_k: int) -> _HorisontCachePost:
    z = np.zeros((sid_px, sid_px), dtype=np.float64)
    falt = Hojdfalt(z, 1.0, 150_000.0, 6_576_000.0 + sid_px)
    h_rad = np.zeros((antal_k, sid_px, sid_px), dtype=np.float32)
    trunk = np.zeros((sid_px, sid_px), dtype=np.int32)
    return _HorisontCachePost(
        dsm_sha256=f"sha-{tagg}",
        falt=falt,
        fonster=(0, sid_px, 0, sid_px),
        antal_riktningar=antal_k,
        max_avstand_m=100.0,
        h_rad=h_rad,
        trunk=trunk,
        trunk_riktningar=frozenset(range(antal_k)),
        giltiga_riktningar=frozenset(range(antal_k)),
        falt_utan_standard=None,
        h0_rad=None,
    )


def _bygg_snabbt_geografiprojekt(rot: Path, projekt_id: str) -> tuple[Path, Path, float, float]:
    """Bygger ett litet 48×48 m (2 m cell = 576 celler) K1 v2-geografiprojekt i EPSG:3011."""
    e0, n0 = 150_200.0, 6_580_200.0
    storlek_m, dx = 48.0, 2.0
    ruta = Ruta(e_min_m=e0, n_min_m=n0, storlek_m=storlek_m, overlapp_m=0.0, upplosning_m=dx)
    ex, ny = ruta.cell_centra_en()
    gx, gy = np.meshgrid(ex, ny)

    z = (11.20 + 0.015 * np.hypot(gx - (e0 + 24.0), gy - (n0 + 24.0))).astype(np.float32)
    z[(gx >= e0 + 18.0) & (gx <= e0 + 30.0) & (gy >= n0 + 18.0) & (gy <= n0 + 30.0)] = np.float32(
        10.40
    )
    slag = np.full(ruta.form_rc, HARDGJORT, dtype=np.uint8)
    hus = ByggnadsKandidat(
        byggnad_id="hus_stress_e2e",
        polygon=(
            (e0 + 34.0, n0 + 8.0),
            (e0 + 42.0, n0 + 8.0),
            (e0 + 42.0, n0 + 18.0),
            (e0 + 34.0, n0 + 18.0),
        ),
        tak_rh2000=23.0,
        hojd_m=11.0,
        kalla="syntetisk_v11_stress",
    )

    k1_mapp = rot / "bygge_k1"
    bygg_markmodell_ruta(
        ruta,
        k1_mapp,
        grund_z=z,
        markslag_grund=slag,
        byggnadskandidater=(hus,),
        byggnadshojd_fran_laser=False,
        kodversion="v11-stress",
        start_tid="2026-11-26T12:00:00+00:00",
    )
    geo_rot = rot / "geo"
    proj_rot = rot / "projekt"
    k1_lager = lagra(geo_rot, k1_mapp)
    k1_lagrad = lager_mapp(geo_rot, k1_lager.sha256)
    _, manifest_sha = skriv_manifest(
        geo_rot,
        {
            "kontrakt": KONTRAKT_GEOGRAFI,
            "id": f"geo-{projekt_id}",
            "version": "v1",
            "kommun": "stockholm",
            "karna_3011": [e0, n0, e0 + storlek_m, n0 + storlek_m],
            "gransen_3011": [e0, n0, e0 + storlek_m, n0 + storlek_m],
            "lager": {"k1_rutnat": {"sha256": k1_lager.sha256}},
            "kallor": {
                "hojd": {"licens": "CC0-1.0", "kalla": "Syntetisk v11"},
                "byggnader": {"licens": "CC0-1.0", "kalla": "Syntetisk v11"},
            },
            "oppen_for_mcp": True,
        },
    )

    pmapp = proj_rot / projekt_id
    fdir = pmapp / "fragor"
    fdir.mkdir(parents=True)
    (pmapp / "oppenhet.toml").write_text(
        'kontrakt = "floden-oppenhet/v1"\noppen = true\nlicens = "CC0-1.0"\n',
        encoding="utf-8",
    )
    (pmapp / "projekt.json").write_text(
        json.dumps(
            {
                "kontrakt": KONTRAKT_PROJEKT,
                "projekt_id": projekt_id,
                "namn": "Stresskvarteret",
                "kommun": "stockholm",
                "geografi": f"geo-{projekt_id}@v1",
                "geografi_manifest_sha256": manifest_sha,
                "avgransning_3011": [e0, n0, e0 + storlek_m, n0 + storlek_m],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (fdir / "skyfall.toml").write_text(
        'kontrakt = "K20/v1"\n'
        f'fraga_id = "{projekt_id}-skyfall"\n'
        'beskrivning = "Skyfall stressprojekt"\n\n'
        f'[scenarier.bas]\nbas = "{k1_lagrad.as_posix()}"\n\n'
        '[[analyser]]\nid = "skyfall"\nlosare = "vatten.statisk_fyllning"\n'
        "parametrar = { regn_mm = 30.0 }\n",
        encoding="utf-8",
    )
    return geo_rot, proj_rot, e0, n0


# ---------------------------------------------------------------------------
# 1. OOM / SIGKILL + RSS-vakt i barnprocess → /api/halsa 200 → återhämtning
# ---------------------------------------------------------------------------


def test_e2e_oom_sigkill_och_ramvakt_i_barnprocess_och_aterhamtning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SIGKILL-död och RSS-vaktsdöd i barnprocess påverkar aldrig webbprocessen;
    `/api/halsa` förblir `200 OK` (0 PII) och nästa riktiga K20-jobb lyckas i samma tjänst."""
    monkeypatch.setenv("FLODEN_EXEKUTOR", "process")
    monkeypatch.setenv("FLODEN_JOBB_MAX_RAM_MB", "64")
    monkeypatch.setenv("FLODEN_JOBB_MAX_TID_S", "30")

    ursprunglig_bygg = subproc_kor._bygg_barnkommando  # noqa: SLF001
    hemlig_cache = tmp_path / "privat_kund_cache_pii_test"
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[REPO / "exempel" / "fragor"],
        cachemapp=hemlig_cache,
        korningsmapp=tmp_path / "korningar",
    )
    try:
        assert app.fragor.exekutor_lage == "process"

        # Del A: Barnprocessen dör direkt med SIGKILL (simulerar Linux kernel OOM-killer -9).
        monkeypatch.setattr(
            subproc_kor,
            "_bygg_barnkommando",
            lambda _spec: [
                sys.executable,
                "-c",
                "import os, signal; os.kill(os.getpid(), signal.SIGKILL)",
            ],
        )
        st_a, start_a = _json_svar(app, "POST", "/api/fragor/vatten_a1_uppfyllnad/kor")
        assert st_a == 202, start_a
        kid_a = str(start_a["korning_id"])

        slut_a = _vanta_slut(app, kid_a, sekunder=15.0)
        assert slut_a["status"] == "fel", slut_a
        assert "SubprocMinneFel" in (slut_a["fel"] or "")
        assert "mer minne än serverns gräns" in (slut_a["fel"] or "")
        assert slut_a["fel_forklaring"] is not None
        assert "arbetsminne" in slut_a["fel_forklaring"].lower()
        assert app.fragor._subproc.aktiva_pids() == []  # noqa: SLF001

        # Del B: Barnprocess allokerar > 64 MB och dödas aktivt av RSS-vakten.
        rss_kod = (
            "import time\n"
            "buf = bytearray(b'\\x01') * (135 * 1024 * 1024)\n"
            "time.sleep(15)\n"
            "assert len(buf) > 0\n"
        )
        monkeypatch.setattr(
            subproc_kor, "_bygg_barnkommando", lambda _spec: [sys.executable, "-c", rss_kod]
        )
        st_b, start_b = _json_svar(app, "POST", "/api/fragor/vatten_a1_uppfyllnad/kor")
        assert st_b == 202, start_b
        kid_b = str(start_b["korning_id"])

        slut_b = _vanta_slut(app, kid_b, sekunder=20.0)
        assert slut_b["status"] == "fel", slut_b
        assert "SubprocMinneFel" in (slut_b["fel"] or "")
        assert "mer minne än serverns gräns" in (slut_b["fel"] or "")
        assert slut_b["fel_forklaring"] is not None
        assert "arbetsminne" in slut_b["fel_forklaring"].lower()
        _vanta_pagaende_noll(app)
        assert app.fragor._subproc.aktiva_pids() == []  # noqa: SLF001

        # /api/halsa svarar fortsatt 200 OK med giltig numerisk driftsdata och 0 PII.
        h_st, _, h_raw = app.hantera_forfragan("GET", "/api/halsa")
        assert h_st == 200
        h_text = h_raw.decode("utf-8")
        halsa = json.loads(h_text)
        assert halsa["status"] == "ok"
        assert halsa["exekutor"] == "process"
        assert isinstance(halsa["minne_rss_mb"], int | float) and halsa["minne_rss_mb"] > 0.0
        assert isinstance(halsa["disk_ledig_gb"], int | float) and halsa["disk_ledig_gb"] >= 0.0
        assert halsa["ko"]["pagaende"] == 0 and halsa["ko"]["koade"] == 0
        assert str(tmp_path) not in h_text
        assert "privat_kund_cache_pii_test" not in h_text
        assert str(Path.home()) not in h_text

        # Del C: Återhämtning — nästa friska K20-jobb i SAMMA FragaTjanst lyckas normalt!
        monkeypatch.setattr(subproc_kor, "_bygg_barnkommando", ursprunglig_bygg)
        app.fragor.jobb_max_ram_mb = 2048.0
        app.fragor.jobb_max_tid_s = 60.0

        st_c, start_c = _json_svar(app, "POST", "/api/fragor/vatten_a1_uppfyllnad/kor")
        assert st_c == 202, start_c
        assert start_c["ateranvand"] is False
        kid_c = str(start_c["korning_id"])

        slut_c = _vanta_slut(app, kid_c, sekunder=60.0)
        assert slut_c["status"] == "klar", slut_c
        assert slut_c["fel"] is None
        assert slut_c["framsteg"] == {"klara": 2, "totalt": 2}

        framsteg = json.loads(
            (tmp_path / "korningar" / kid_c / "framsteg.json").read_text(encoding="utf-8")
        )
        assert framsteg["pid"] != os.getpid()

        res_st, res = _json_svar(app, "GET", f"/api/korningar/{kid_c}/resultat")
        assert res_st == 200, res
        varden = {x["scenario"]: x["varden"] for x in res["tabeller"][0]["rader"]}
        assert varden["fore"]["volymfel_rel"] < 1e-9
        assert varden["efter"]["max_djup_m"] > 0.0
    finally:
        app.stang()


# ---------------------------------------------------------------------------
# 2. Timeout-kill (hel processgrupp) & zombie-fri FragaTjanst.stang()
# ---------------------------------------------------------------------------


def test_e2e_timeout_kill_processgrupp_och_zombiefri_stang(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Timeout dödar hela processgruppen (inkl. barnbarn) rent; `stang()` under aktiv
    körning + köat jobb avslutar direkt utan zombies och lämnar inga kvarvarande processer."""
    monkeypatch.setenv("FLODEN_EXEKUTOR", "process")
    monkeypatch.setenv("FLODEN_JOBB_MAX_TID_S", "1.2")
    monkeypatch.setenv("FLODEN_JOBB_MAX_RAM_MB", "1024")

    # Del A: Timeout-kill av barn + barnbarn i samma processgrupp.
    pid_fil_a = tmp_path / "pids_timeout.json"
    hang_med_barnbarn = (
        "import json, os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "barn = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "mal = Path(sys.argv[1])\n"
        "tmp = mal.with_suffix('.tmp')\n"
        "tmp.write_text(json.dumps({'barn': os.getpid(), 'barnbarn': barn.pid}))\n"
        "os.replace(tmp, mal)\n"
        "while True:\n"
        "    time.sleep(0.2)\n"
    )
    monkeypatch.setattr(
        subproc_kor,
        "_bygg_barnkommando",
        lambda _spec: [sys.executable, "-c", hang_med_barnbarn, str(pid_fil_a)],
    )

    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[REPO / "exempel" / "fragor"],
        cachemapp=tmp_path / "cache_a",
        korningsmapp=tmp_path / "korningar_a",
    )
    try:
        t0 = time.monotonic()
        st, start = _json_svar(app, "POST", "/api/fragor/vatten_a1_uppfyllnad/kor")
        assert st == 202, start
        slut = _vanta_slut(app, str(start["korning_id"]), sekunder=10.0)
        dt = time.monotonic() - t0

        assert dt < 6.0 * _tidsfaktor(), f"Timeout dröjde för länge ({dt:.2f} s)"
        assert slut["status"] == "fel", slut
        assert "SubprocTimeoutFel" in (slut["fel"] or "")
        assert "tidsgräns" in (slut["fel"] or "").lower()
        assert slut["fel_forklaring"] is not None
        assert "tidsgräns" in slut["fel_forklaring"].lower()
        _vanta_pagaende_noll(app)
        assert app.fragor.ko_status()["pagaende"] == 0
        assert app.fragor._subproc.aktiva_pids() == []  # noqa: SLF001

        assert pid_fil_a.is_file(), "Barnprocessen hann inte skriva PID-fil innan timeout."
        pids_a = json.loads(pid_fil_a.read_text(encoding="utf-8"))
        assert _pid_ar_dod(int(pids_a["barn"])), f"Barn {pids_a['barn']} lever/zombie!"
        assert _pid_ar_dod(int(pids_a["barnbarn"])), f"Barnbarn {pids_a['barnbarn']} lever!"
    finally:
        app.stang()

    # Del B: `FragaTjanst.stang()` under aktiv körning (barn + barnbarn) + ett köat jobb.
    fragemapp_b = tmp_path / "fragor_b"
    fragemapp_b.mkdir()
    a1_abs = (REPO / "testsvit" / "A1_enkel_sanka" / "indata").as_posix()
    for namn, fid in (("fraga_ett", "stress-stang-1"), ("fraga_tva", "stress-stang-2")):
        (fragemapp_b / f"{namn}.toml").write_text(
            'kontrakt = "K20/v1"\n'
            f'fraga_id = "{fid}"\n'
            'beskrivning = "Stresstest nedstängning"\n\n'
            f'[scenarier.fore]\nbas = "{a1_abs}"\n\n'
            '[[analyser]]\nid = "skyfall"\nlosare = "vatten.statisk_fyllning"\n'
            "parametrar = { regn_mm = 50.0 }\n",
            encoding="utf-8",
        )

    pid_fil_b = tmp_path / "pids_stang.json"
    monkeypatch.setenv("FLODEN_JOBB_MAX_TID_S", "60")
    monkeypatch.setattr(
        subproc_kor,
        "_bygg_barnkommando",
        lambda _spec: [sys.executable, "-c", hang_med_barnbarn, str(pid_fil_b)],
    )

    tjanst = FragaTjanst(
        rot=REPO,
        fragemappar=[fragemapp_b],
        cachemapp=tmp_path / "cache_b",
        korningsmapp=tmp_path / "korningar_b",
    )
    try:
        s1 = tjanst.starta_korning("fraga_ett")
        kid1 = str(s1["korning_id"])

        # Vänta tills barn + barnbarn är uppe och registrerade
        deadline = time.monotonic() + 10.0
        while not pid_fil_b.is_file() or not tjanst._subproc.aktiva_pids():  # noqa: SLF001
            assert time.monotonic() < deadline, "Barnprocessen startade inte i tid inför stang()."
            time.sleep(0.02)

        # Köa ett andra jobb medan det första kör
        s2 = tjanst.starta_korning("fraga_tva")
        kid2 = str(s2["korning_id"])
        assert s2["status"] == "ko"
        assert tjanst.ko_status()["koade"] == 1

        pids_b = json.loads(pid_fil_b.read_text(encoding="utf-8"))

        t_stang0 = time.monotonic()
        tjanst.stang(vanta=True)
        dt_stang = time.monotonic() - t_stang0

        assert dt_stang < 5.0 * _tidsfaktor(), f"FragaTjanst.stang() hängde i {dt_stang:.2f} s"
        assert tjanst._subproc.aktiva_pids() == []  # noqa: SLF001
        assert _pid_ar_dod(int(pids_b["barn"])), (
            f"Barn {pids_b['barn']} lever/zombie efter stang()!"
        )
        assert _pid_ar_dod(int(pids_b["barnbarn"])), (
            f"Barnbarn {pids_b['barnbarn']} lever efter stang()!"
        )

        st1 = tjanst.korning_status(kid1)
        st2 = tjanst.korning_status(kid2)
        assert st1["status"] == "fel" and "stoppades" in (st1["fel"] or "").lower()
        assert st2["status"] == "fel" and "stoppades" in (st2["fel"] or "").lower()

        # Idempotent stang() och nya jobb avvisas med 503 KoFullFel.
        tjanst.stang(vanta=True)
        with pytest.raises(KoFullFel) as exc_ko:
            tjanst.starta_korning("fraga_ett")
        assert exc_ko.value.status_kod == 503
    finally:
        tjanst.stang(vanta=True)


# ---------------------------------------------------------------------------
# 3. Låg ledig disk (507) → stada_lokal_disk → återhämtning
# ---------------------------------------------------------------------------


def test_e2e_lag_disk_507_sedan_stada_lokal_disk_och_aterhamtning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-flight diskvakt ger 507 utan halvskrivna filer; `stada_lokal_disk` rensar gammal
    cache ned till <= 85 % utan att röra skyddade objekt/symlänksmål; sedan lyckas nytt jobb."""
    data_rot = tmp_path / "data"
    cache_rot = data_rot / "cache"
    karna_cache = cache_rot / "karna"
    korningsmapp = cache_rot / "webb" / "korningar"
    projekt_rot = data_rot / "lokal"
    demo_rot = data_rot / "demo"
    konto_rot = data_rot / "konto"
    utanfor_rot = tmp_path / "utanfor_cache_skyddat"

    monkeypatch.setenv("FLODEN_EXEKUTOR", "process")
    monkeypatch.setenv("FLODEN_MIN_LEDIG_DISK_MB", "500")
    monkeypatch.setenv("FLODEN_JOBB_MAX_TID_S", "60")

    ledig_disk_mb = {"varde": 120.0}  # Under 500 MB-gränsen

    def _simulerad_disk_usage(_sokvag: Any) -> Any:
        total = 40 * 1024**3
        free = int(ledig_disk_mb["varde"] * _MB)
        return _DiskUsage(total=total, used=total - free, free=free)

    monkeypatch.setattr(shutil, "disk_usage", _simulerad_disk_usage)

    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[REPO / "exempel" / "fragor"],
        cachemapp=karna_cache,
        korningsmapp=korningsmapp,
    )
    try:
        # Steg 1: Provocera pre-flight diskvakt -> HTTP 507 utan halvskrivna filer.
        st_507, svar_507 = _json_svar(app, "POST", "/api/fragor/vatten_a1_uppfyllnad/kor")
        assert st_507 == 507, svar_507
        assert svar_507["status"] == 507
        assert "diskutrymme" in svar_507["fel"].lower()
        assert not korningsmapp.exists()
        assert not karna_cache.exists()
        assert app.fragor.ko_status()["koade"] == 0
        assert app.fragor.ko_status()["pagaende"] == 0

        # Steg 2: Bygg blandad VPS-katalog med både städbara och skyddade objekt + symlänkar.
        gammal_ts = _NU_TS - 20.0 * 86400.0  # > 14 dagar
        lru_ts_1 = _NU_TS - 5.0 * 86400.0
        lru_ts_2 = _NU_TS - 4.0 * 86400.0
        lru_ts_3 = _NU_TS - 3.0 * 86400.0
        farsk_ts = _NU_TS - 5.0 * 60.0  # inom grace_minuter=30

        # Städbara objekt
        m_gammal = _skapa_korning_pa_disk(
            cache_rot, "run-gammal-klar", status="klar", storlek_bytes=2 * _MB, ts=gammal_ts
        )
        m_lru1 = _skapa_korning_pa_disk(
            cache_rot, "run-lru-1", status="klar", storlek_bytes=2 * _MB, ts=lru_ts_1
        )
        m_lru2 = _skapa_korning_pa_disk(
            cache_rot, "run-lru-2", status="klar", storlek_bytes=2 * _MB, ts=lru_ts_2
        )
        _skapa_korning_pa_disk(
            cache_rot, "run-lru-3", status="klar", storlek_bytes=2 * _MB, ts=lru_ts_3
        )
        lager_v1 = cache_rot / "webb" / "lager" / "v1"
        lager_v1.mkdir(parents=True)
        gammal_v1_png = lager_v1 / "gammal.png"
        gammal_v1_png.write_bytes(b"P" * _MB)
        _satt_tid_rekursivt(lager_v1, gammal_ts)

        lager_v12 = cache_rot / "webb" / "lager" / "v12"
        lager_v12.mkdir(parents=True)
        aktuell_v12_png = lager_v12 / "aktuell.png"
        aktuell_v12_png.write_bytes(b"A" * (200 * 1024))
        _satt_tid_rekursivt(lager_v12, farsk_ts)

        m_trasig_k22 = _skapa_k22_post(
            cache_rot, "jobb", "a", klar=False, storlek_bytes=_MB, ts=gammal_ts
        )
        tmp_skrap = cache_rot / ".floden_overgiven_temp"
        tmp_skrap.write_bytes(b"T" * (512 * 1024))
        _satt_tid_rekursivt(tmp_skrap, gammal_ts)

        # Skyddade objekt (även med gammal mtime = 20 dagar!)
        m_kor = _skapa_korning_pa_disk(
            cache_rot, "run-pagaende-kor", status="kor", storlek_bytes=_MB, ts=gammal_ts
        )
        m_ko = _skapa_korning_pa_disk(
            cache_rot, "run-koad-ko", status="ko", storlek_bytes=512 * 1024, ts=gammal_ts
        )
        m_demo = _skapa_korning_pa_disk(
            cache_rot, "run-demo-ref", status="klar", storlek_bytes=512 * 1024, ts=gammal_ts
        )
        m_lage = _skapa_korning_pa_disk(
            cache_rot, "run-lage-idag", status="klar", storlek_bytes=512 * 1024, ts=gammal_ts
        )
        m_scen = _skapa_korning_pa_disk(
            cache_rot, "run-scen-ref", status="klar", storlek_bytes=512 * 1024, ts=gammal_ts
        )

        # demo.json, lage-idag.json, scenarier.json, data/konto, data/lokal
        (demo_rot / "varld").mkdir(parents=True)
        demo_json = demo_rot / "varld" / "demo.json"
        demo_json.write_text(
            json.dumps({"delar": {"vatten": {"korning_id": "run-demo-ref"}}}), encoding="utf-8"
        )

        p1 = projekt_rot / "projekt" / "p1"
        p1.mkdir(parents=True)
        proj_json = p1 / "projekt.json"
        proj_json.write_text('{"projekt_id":"p1","namn":"Kvarter"}', encoding="utf-8")
        lage_json = p1 / "lage-idag.json"
        lage_json.write_text(
            json.dumps({"lage": "idag", "korningar": {"vatten": {"korning_id": "run-lage-idag"}}}),
            encoding="utf-8",
        )
        scen_json = p1 / "scenarier.json"
        scen_json.write_text(
            json.dumps({"scenarier": [{"slug": "f1", "korning_ids": ["run-scen-ref"]}]}),
            encoding="utf-8",
        )

        konto_rot.mkdir(parents=True)
        konto_db = konto_rot / "konto.sqlite"
        konto_db.write_bytes(b"SQLITE_SKYDDAD_KONTO_DATA")

        # Symlänkar ut ur cache_rot -> målfilen utanför MÅSTE förbli 100 % orörd!
        utanfor_rot.mkdir(parents=True)
        utanfor_fil = utanfor_rot / "viktig_utanfor.txt"
        utanfor_fil.write_text("ORORD_UTANFOR_CACHE_ROT_V11", encoding="utf-8")
        (cache_rot / "webb" / "korningar" / "run-symlank-ut").symlink_to(utanfor_rot)
        (cache_rot / ".floden_symlank_ut").symlink_to(utanfor_fil)
        (lager_v1 / "symlank_ut.txt").symlink_to(utanfor_fil)

        skyddade_filer = [
            m_kor / "resultat" / "data.bin",
            m_ko / "resultat" / "data.bin",
            m_demo / "resultat" / "data.bin",
            m_lage / "resultat" / "data.bin",
            m_scen / "resultat" / "data.bin",
            aktuell_v12_png,
            demo_json,
            proj_json,
            lage_json,
            scen_json,
            konto_db,
            utanfor_fil,
        ]
        sha_fore = {f: sha256_fil(f) for f in skyddade_filer}

        # Säkerhetskontroll: vägra städa skyddade kataloger data/konto och data/lokal direkt.
        with pytest.raises(LokalDiskStadareFel):
            stada_lokal_disk(LokalDiskKonfig(cache_rot=konto_rot))
        with pytest.raises(LokalDiskStadareFel):
            stada_lokal_disk(LokalDiskKonfig(cache_rot=projekt_rot))

        # Budget 6 MB, mål 85 % (= 5,1 MB). Skyddade objekt i cache_rot tar ~3,2 MB.
        max_gb = 6.0 / 1024.0
        konfig = LokalDiskKonfig(
            cache_rot=cache_rot,
            projekt_rot=projekt_rot,
            demo_rot=demo_rot,
            max_gb=max_gb,
            mal_andel=0.85,
            max_alder_dagar=14.0,
            grace_minuter=30.0,
            aktuell_lager_version="v12",
        )
        mal_bytes = int(max_gb * 0.85 * BYTES_PER_GB)

        # Torrkor först -> 0 förändringar på disk.
        torr = stada_lokal_disk(konfig, torrkor=True, nu_ts=_NU_TS)
        assert torr.torrkor is True
        assert torr.frigjorda_bytes > 0
        assert m_gammal.exists() and gammal_v1_png.exists() and m_trasig_k22.exists()

        # Skarp städning!
        utfall = stada_lokal_disk(konfig, torrkor=False, nu_ts=_NU_TS)
        assert utfall.torrkor is False
        assert utfall.frigjorda_bytes > 0
        assert utfall.antal_raderade >= 5
        assert utfall.efter_bytes <= mal_bytes, (
            f"Efter städning kvarstår {utfall.efter_bytes} B > 85%-mål {mal_bytes} B"
        )
        # 0 PII / sökvägar i PII-fri driftsrapport
        utfall_json = json.dumps(utfall.som_dict(), ensure_ascii=False)
        assert str(tmp_path) not in utfall_json

        # Verifiera att gamla/trasiga objekt rensades
        assert not m_gammal.exists()
        assert not m_lru1.exists()
        assert not m_lru2.exists()
        assert not gammal_v1_png.exists()
        assert not m_trasig_k22.exists()
        assert not tmp_skrap.exists()

        # Verifiera att ALLA skyddade objekt + symlänksmål är 100 % byte-identiska!
        for fil, forvantad_sha in sha_fore.items():
            assert fil.is_file(), f"Skyddad fil raderades: {fil}"
            assert sha256_fil(fil) == forvantad_sha

        # Steg 3: Diskutrymme återhämtat -> ny riktig körning i samma FlodenApiApp lyckas!
        ledig_disk_mb["varde"] = 2048.0
        st_ok, start_ok = _json_svar(app, "POST", "/api/fragor/vatten_a1_uppfyllnad/kor")
        assert st_ok == 202, start_ok
        slut_ok = _vanta_slut(app, str(start_ok["korning_id"]), sekunder=60.0)
        assert slut_ok["status"] == "klar", slut_ok
    finally:
        app.stang()


# ---------------------------------------------------------------------------
# 4. ASGI-samtidighetstak (FLODEN_MAX_SAMTIDIGA_BERAKNINGAR=2) under belastning
# ---------------------------------------------------------------------------


def test_e2e_asgi_samtidighetstak_2_ger_503_och_halsa_200(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Riktig `FlodenAsgiApp` + `FlodenApiApp` med `FLODEN_MAX_SAMTIDIGA_BERAKNINGAR=2`:
    3:e och 4:e samtidiga tunga POST får `503 + Retry-After`, medan `/api/halsa` ger `200 OK`."""
    monkeypatch.setenv("FLODEN_MAX_SAMTIDIGA_BERAKNINGAR", "2")
    monkeypatch.setenv("FLODEN_EXEKUTOR", "process")

    api_app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[REPO / "exempel" / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    orig_hantera_http = api_app.hantera_http
    lock = threading.Lock()
    aktiva_post = 0
    tva_inne = threading.Event()
    slapp_barriar = threading.Event()

    def _synkad_hantera_http(
        metod: str,
        sokvag: str,
        rubriker: Mapping[str, str],
        las_kropp: Callable[[], bytes],
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        nonlocal aktiva_post
        if metod == "POST" and not slapp_barriar.is_set():
            with lock:
                aktiva_post += 1
                if aktiva_post >= 2:
                    tva_inne.set()
            try:
                slapp_barriar.wait(timeout=10.0)
            finally:
                with lock:
                    aktiva_post -= 1
        return orig_hantera_http(metod, sokvag, rubriker, las_kropp)

    monkeypatch.setattr(api_app, "hantera_http", _synkad_hantera_http)
    asgi_app = FlodenAsgiApp(api_app, ko_timeout_s=0.08)
    assert asgi_app.max_samtidiga_berakningar == 2

    async def _kor_belastning() -> None:
        # 2 samtidiga tunga förfrågningar fyller semaforen (tak = 2)
        t1 = asyncio.create_task(_anropa_asgi(asgi_app, "POST", "/api/kor", kropp=b"{}"))
        t2 = asyncio.create_task(_anropa_asgi(asgi_app, "POST", "/api/analys/sankor", kropp=b"{}"))
        deadline = time.monotonic() + 5.0
        try:
            while not tva_inne.is_set():
                assert time.monotonic() < deadline, "Två samtidiga tunga POST kom inte in i tid."
                await asyncio.sleep(0.01)

            # /api/halsa blockeras inte av semaforen och svarar 200 OK direkt!
            t_h0 = time.monotonic()
            h_st, _, h_kropp = await _anropa_asgi(asgi_app, "GET", "/api/halsa")
            dt_h = time.monotonic() - t_h0
            assert h_st == 200
            assert dt_h < 1.5 * _tidsfaktor(), (
                f"/api/halsa tog för lång tid under belastning ({dt_h:.2f} s)"
            )
            h_data = json.loads(h_kropp.decode("utf-8"))
            assert h_data["status"] == "ok"
            assert h_data["exekutor"] == "process"
            assert isinstance(h_data["minne_rss_mb"], int | float) and h_data["minne_rss_mb"] > 0.0
            assert isinstance(h_data["disk_ledig_gb"], int | float) and h_data["disk_ledig_gb"] >= 0

            # 3:e och 4:e samtidiga tunga POST ska avvisas med HTTP 503 + Retry-After
            p3_st, p3_hdrs, p3_kropp = await _anropa_asgi(
                asgi_app, "POST", "/api/fragor/vatten_a1_uppfyllnad/kor", kropp=b"{}"
            )
            assert p3_st == 503, (p3_st, p3_kropp)
            assert "retry-after" in p3_hdrs and int(p3_hdrs["retry-after"]) > 0
            assert json.loads(p3_kropp.decode("utf-8"))["status_kod"] == 503

            p4_st, p4_hdrs, _ = await _anropa_asgi(
                asgi_app, "POST", "/api/analys/intervall", kropp=b"{}"
            )
            assert p4_st == 503
            assert "retry-after" in p4_hdrs
        finally:
            slapp_barriar.set()

        r1, r2 = await asyncio.gather(t1, t2)
        # Båda gick igenom semaforen till riktiga FlodenApiApp (aldrig 503)
        assert r1[0] != 503 and r2[0] != 503

        # När semaforen frigjorts släpps nästa tunga POST åter igenom (ej 503)
        efter_st, _, _ = await _anropa_asgi(asgi_app, "POST", "/api/kor", kropp=b"{}")
        assert efter_st != 503

        # Lifespan startup + shutdown stänger appen kontrollerat
        events = await _kor_asgi_lifespan(asgi_app)
        assert events == ["lifespan.startup.complete", "lifespan.shutdown.complete"]

    try:
        asyncio.run(_kor_belastning())
    finally:
        api_app.stang()


# ---------------------------------------------------------------------------
# 5. Skadad / gallrad run.json i äldre körning → aldrig FileNotFoundError / 500
# ---------------------------------------------------------------------------


def test_e2e_skadad_eller_gallrad_run_json_ger_aldrig_500(tmp_path: Path) -> None:
    """Gallrad eller korrumperad `run.json` i en avslutad körning (både scenario och läget idag)
    hanteras mjukt med varning i `fragor.resultat(...)` och `kartverktyg.status(...)` utan 500."""
    projekt_id = "kvarter-v11-runjson"
    geo_rot, proj_rot, e0, n0 = _bygg_snabbt_geografiprojekt(tmp_path, projekt_id)

    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        kartverktygsmapp=tmp_path / "kartverktyg",
        geomapp=geo_rot,
        projektmapp=proj_rot,
        datamappar=[tmp_path],
    )
    bas_url = f"/api/kartverktyg/{projekt_id}"
    try:
        # 1. Kör "läget idag" + ett sparat scenario hela vägen till status == "klar".
        st_lage, kor_lage = _json_svar(
            app,
            "POST",
            f"{bas_url}/lage-idag/kor",
            {"analyser": ["vatten.skyfall"], "regn": {"regn_mm": 30}},
        )
        assert st_lage == 202, kor_lage
        kid_lage = str(kor_lage["fragor"][0]["korning"]["korning_id"])
        assert _vanta_slut(app, kid_lage)["status"] == "klar"

        ring = [
            [e0 + 10.0, n0 + 10.0],
            [e0 + 16.0, n0 + 10.0],
            [e0 + 16.0, n0 + 16.0],
            [e0 + 10.0, n0 + 16.0],
            [e0 + 10.0, n0 + 10.0],
        ]
        st_spara, scen = _json_svar(
            app,
            "POST",
            f"{bas_url}/scenarier",
            {
                "namn": "Testförslag",
                "andringar": [
                    {
                        "typ": "byggnad_ny",
                        "andring_id": "hus_ny_1",
                        "geometri": {"type": "Polygon", "coordinates": [ring]},
                        "egenskaper": {"hojd_m": 12},
                    }
                ],
            },
        )
        assert st_spara == 200, scen
        scen_slug = str(scen["slug"])

        st_scen, kor_scen = _json_svar(
            app,
            "POST",
            f"{bas_url}/scenarier/{scen_slug}/kor",
            {"analyser": ["vatten.skyfall"], "regn": {"regn_mm": 30}},
        )
        assert st_scen == 202, kor_scen
        kid_scen = str(kor_scen["fragor"][0]["korning"]["korning_id"])
        assert _vanta_slut(app, kid_scen)["status"] == "klar"

        # Hitta jobbens run.json-filer under resultat/ (under <analys>/<scenario>/run.json)
        jobb_runs_lage = [
            p
            for p in (tmp_path / "korningar" / kid_lage / "resultat").rglob("run.json")
            if p.parent != (tmp_path / "korningar" / kid_lage / "resultat")
        ]
        jobb_runs_scen = [
            p
            for p in (tmp_path / "korningar" / kid_scen / "resultat").rglob("run.json")
            if p.parent != (tmp_path / "korningar" / kid_scen / "resultat")
        ]
        assert len(jobb_runs_lage) >= 1 and len(jobb_runs_scen) >= 1

        # Fall A: Gallra (radera) alla jobb-run.json + rensa webb-lager-cachen så disk läses om.
        for run_fil in (*jobb_runs_lage, *jobb_runs_scen):
            run_fil.unlink()
        for kid in (kid_lage, kid_scen):
            shutil.rmtree(tmp_path / "korningar" / kid / "webb", ignore_errors=True)

        # Python-API + HTTP för fragor.resultat och kartverktyg.status / status_lage_idag -> 200!
        res_del = app.fragor.resultat(kid_scen)
        assert any("run.json" in str(v.get("text", "")) for v in res_del["varningar"])
        http_res_st, http_res = _json_svar(app, "GET", f"/api/korningar/{kid_scen}/resultat")
        assert http_res_st == 200, http_res

        kv_st_del = app.kartverktyg.status(projekt_id, scen_slug)
        assert kv_st_del["slug"] == scen_slug
        http_kv_st, _ = _json_svar(app, "GET", f"{bas_url}/scenarier/{scen_slug}/status")
        assert http_kv_st == 200

        kv_lage_del = app.kartverktyg.status_lage_idag(projekt_id)
        assert kv_lage_del["lage"] == "idag"
        http_lage_st, _ = _json_svar(app, "GET", f"{bas_url}/lage-idag/status")
        assert http_lage_st == 200

        # Även vektor-endpoint som kräver run.json ska ge kontrollerad 409 (aldrig 500!)
        vec_st, _ = _json_svar(app, "GET", f"/api/korningar/{kid_scen}/vektor/fore/sankor")
        assert vec_st == 409

        # Fall B: Korrumpera (trunkerad/trasig JSON) i alla jobb-run.json och testa på nytt.
        for run_fil in (*jobb_runs_lage, *jobb_runs_scen):
            run_fil.write_text('{"kontrakt": "K0/v1", "utdata": [trasig', encoding="utf-8")
        for kid in (kid_lage, kid_scen):
            shutil.rmtree(tmp_path / "korningar" / kid / "webb", ignore_errors=True)

        res_korrupt = app.fragor.resultat(kid_scen)
        assert any("run.json" in str(v.get("text", "")) for v in res_korrupt["varningar"])
        assert _json_svar(app, "GET", f"/api/korningar/{kid_scen}/resultat")[0] == 200
        assert app.kartverktyg.status(projekt_id, scen_slug)["slug"] == scen_slug
        assert _json_svar(app, "GET", f"{bas_url}/scenarier/{scen_slug}/status")[0] == 200
        assert app.kartverktyg.status_lage_idag(projekt_id)["lage"] == "idag"
        assert _json_svar(app, "GET", f"{bas_url}/lage-idag/status")[0] == 200
    finally:
        app.stang()


# ---------------------------------------------------------------------------
# 6. Tvärs-modul RAM-budget & fysikinvarianter (SankGraf, Cacher, Ekologi CG)
# ---------------------------------------------------------------------------


def test_e2e_tvars_modul_ram_budget_och_fysikinvarianter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifierar vektoriserad int32-SankGraf (platta + 2 sänkor, volymbevarande),
    hårda bytebudgetar i `_HORISONT_CACHE`, `VarmestressLagerCache` och `BullerLagerCache`,
    samt Jacobi-CG i `ekologi.krets.los_kretspar` (> CG-tröskel) och `FLODEN_MAX_EKOLOGI_CELLER`."""
    # --- 6A: Vektoriserad int32 SankGraf på 120×120 terräng (platta + 2 sänkor + sadel + hus) ---
    hojd, bredd = 120, 120
    dx = 0.5
    geo = GeoTransformInfo(
        bredd=bredd,
        hojd=hojd,
        x_min=150_000.0,
        y_max=6_580_000.0,
        pixelstorlek_x=dx,
        pixelstorlek_y=dx,
        epsg=3011,
        dtype="float32",
    )
    z = np.full((hojd, bredd), 12.0, dtype=np.float32)  # bred platta på 12,0 m
    osak = np.full((hojd, bredd), 0.02, dtype=np.float32)
    slag = np.full((hojd, bredd), 1, dtype=np.uint8)

    # Sänka 1 (djupare, botten 10,0 m) och Sänka 2 (botten 10,35 m) med platt sadel (11,10 m)
    z[20:52, 16:48] = np.float32(10.6)
    z[28:44, 24:40] = np.float32(10.0)
    z[20:52, 62:96] = np.float32(10.8)
    z[28:44, 70:88] = np.float32(10.35)
    z[32:40, 48:62] = np.float32(11.10)  # sadel mellan sänka 1 och sänka 2
    z[34:38, 0:16] = np.float32(11.50)  # utlopp till västra randen

    # Byggnad på plattan (får aldrig samla markdjup)
    slag[74:98, 50:76] = MARKSLAG_BYGGNAD
    z[74:98, 50:76] = np.float32(18.0)

    aktiv = np.asarray((slag != MARKSLAG_BYGGNAD) & np.isfinite(z), dtype=np.bool_)
    flow_i32, ctn_i32, _ = _berakna_avrinningsomraden(z, aktiv, {})
    assert flow_i32.dtype == np.int32
    assert ctn_i32.dtype == np.int32

    gc.collect()
    tracemalloc.start(1)
    try:
        graf = bygg_sankgraf(z, osak, slag, geo)
        _, peak_sankgraf_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(graf.lovnoder()) >= 2
    assert peak_sankgraf_bytes < 40 * _MB

    tidigare_volym = -1.0
    tidigare_max_djup = -1.0
    for regn_m in (0.05, 0.25, 0.80):
        res = statisk_fyllning(graf, regn_m)
        assert res.volymfel_rel < 1e-9, f"Volymfel vid {regn_m} m: {res.volymfel_rel}"
        assert float(np.max(res.djup_m[slag == MARKSLAG_BYGGNAD])) == 0.0
        volym = float(np.sum(res.djup_m) * dx * dx)
        max_djup = float(np.max(res.djup_m))
        assert volym >= tidigare_volym - 1e-9
        assert max_djup >= tidigare_max_djup - 1e-9
        tidigare_volym = volym
        tidigare_max_djup = max_djup

    # --- 6B: Hårda RAM-budgetar i _HORISONT_CACHE, VarmestressLagerCache och BullerLagerCache ---
    rensa_horisont_cache()
    try:
        monkeypatch.setenv("FLODEN_MAX_HORISONT_CACHE_MB", "0.5")  # 524 288 B
        hor_budget = max_horisont_cache_bytes()
        assert hor_budget == 512 * 1024

        # Varje post: 48×48, K=16 -> (2304*8) + (16*2304*4) + (2304*4) = 175 104 B (~171 KiB)
        for i in range(20):
            post = _syntetisk_horisont_post(f"p{i}", sid_px=48, antal_k=16)
            fl_mod._spara_horisont_i_cache(  # noqa: SLF001
                (f"k{i}",), (f"r{i}",), (f"g{i}",), post, ar_andrad_fran_bas=False
            )
            assert horisont_cache_bytes() <= hor_budget

        # Överstor post (> 512 KiB) får aldrig spräcka budgeten eller cachas
        stor_post = _syntetisk_horisont_post("overstor", sid_px=64, antal_k=64)
        assert fl_mod._horisont_post_bytes(stor_post) > hor_budget  # noqa: SLF001
        fl_mod._spara_horisont_i_cache(  # noqa: SLF001
            ("overstor",), ("r_stor",), ("g_stor",), stor_post, ar_andrad_fran_bas=False
        )
        assert fl_mod._hamta_exakt_horisont(("overstor",)) is None  # noqa: SLF001
        assert horisont_cache_bytes() <= hor_budget
    finally:
        rensa_horisont_cache()

    monkeypatch.setenv("FLODEN_MAX_WEBB_PNG_CACHE_MB", "0.002")  # 2 097 B
    png_budget = max_webb_png_cache_bytes()
    for cache_cls in (VarmestressLagerCache, BullerLagerCache):
        cache = cache_cls(max_poster=64)
        for i in range(30):
            proj = f"proj-{i % 3}"
            bild = bytes([65 + (i % 26)]) * (400 + (i % 5) * 80)
            cache.satt(proj, f"lager_{i}", bild, _UTB)
            assert cache.antal_bytes <= png_budget
        # Överskrivning och överstor PNG (> budget) får aldrig spräcka budgeten
        cache.satt("proj-0", "lager_27", b"Z" * 900, _UTB)
        assert cache.antal_bytes <= png_budget
        cache.satt("proj-0", "jatte", b"J" * (png_budget + 500), _UTB)
        assert cache.hamta("proj-0", "jatte") is None
        assert cache.antal_bytes <= png_budget
        # Projektisolering: proj-99 ser aldrig andra projekts nycklar
        assert cache.hamta("proj-99", "lager_27") is None

    # --- 6C: Ekologi Jacobi-CG (> STANDARD_CG_TROSKEL) & FLODEN_MAX_EKOLOGI_CELLER ---
    monkeypatch.delenv("FLODEN_MAX_EKOLOGI_CELLER", raising=False)

    # Korskontroll: tvingad Jacobi-CG matchar exakt direkt spsolve inom < 1e-6 på 32×40-rutnät.
    rng_ref = np.random.default_rng(1337)
    r_ref = np.exp(rng_ref.uniform(0.0, math.log(25.0), size=(32, 40))).astype(np.float64)
    r_ref[:, 20] = math.inf
    r_ref[8:14, 20] = 4.0
    g_ref = np.where(np.isfinite(r_ref), 1.0 / r_ref, 0.0)
    myta_ref = MotstandsYta(motstand=r_ref, konduktans=g_ref, cell_m=10.0, profil_id="stress_ref")
    ma_ref = np.zeros((32, 40), dtype=np.bool_)
    mb_ref = np.zeros((32, 40), dtype=np.bool_)
    ma_ref[6:26, 0:2] = True
    mb_ref[6:26, 38:40] = True
    res_sps_ref = los_kretspar(myta_ref, ma_ref, mb_ref, grannskap=4, cg_troskel=100_000)
    res_cg_ref = los_kretspar(myta_ref, ma_ref, mb_ref, grannskap=4, cg_troskel=10)
    assert res_cg_ref.strombalans_fel_rel < 1e-6
    assert res_cg_ref.effektivt_motstand == pytest.approx(res_sps_ref.effektivt_motstand, rel=1e-6)

    # Skarp körning över STANDARD_CG_TROSKEL (28 900 > 25 000) med förbjuden spsolve.
    ny_ek, nx_ek = 170, 170  # 28 900 celler > STANDARD_CG_TROSKEL (25 000)
    assert ny_ek * nx_ek > STANDARD_CG_TROSKEL
    rng = np.random.default_rng(2026)
    r_arr = np.exp(rng.uniform(0.0, math.log(40.0), size=(ny_ek, nx_ek))).astype(np.float64)
    mitt = nx_ek // 2
    r_arr[:, mitt] = math.inf
    r_arr[40:54, mitt] = 3.0
    r_arr[116:130, mitt] = 7.0
    g_arr = np.where(np.isfinite(r_arr), 1.0 / r_arr, 0.0)
    myta = MotstandsYta(motstand=r_arr, konduktans=g_arr, cell_m=10.0, profil_id="stress_cg")
    mask_a = np.zeros((ny_ek, nx_ek), dtype=np.bool_)
    mask_b = np.zeros((ny_ek, nx_ek), dtype=np.bool_)
    mask_a[30:140, 0:2] = True
    mask_b[30:140, nx_ek - 2 : nx_ek] = True

    def _forbjuden_spsolve(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("spsolve får inte anropas på 170×170 (> STANDARD_CG_TROSKEL).")

    monkeypatch.setattr(krets_mod, "spsolve", _forbjuden_spsolve)
    res_cg = los_kretspar(myta, mask_a, mask_b, grannskap=4)
    assert res_cg.sammankopplad is True
    assert math.isfinite(res_cg.effektivt_motstand) and res_cg.effektivt_motstand > 0.0
    assert res_cg.strombalans_fel_rel < 1e-6
    assert float(np.min(res_cg.potential)) >= 0.0 and float(np.max(res_cg.potential)) <= 1.0
    assert float(np.min(res_cg.stromtathet)) >= 0.0 and float(np.max(res_cg.stromtathet)) <= 1.0
    assert float(np.max(res_cg.kanslighet_cell)) <= 0.0

    # Fail-fast celltak via FLODEN_MAX_EKOLOGI_CELLER (och ogiltigt värde -> aldrig tyst fel)
    monkeypatch.setenv("FLODEN_MAX_EKOLOGI_CELLER", "1000")
    with pytest.raises(EkologiFel, match="FLODEN_MAX_EKOLOGI_CELLER"):
        los_kretspar(myta, mask_a, mask_b, grannskap=4)

    monkeypatch.setenv("FLODEN_MAX_EKOLOGI_CELLER", "-50")
    with pytest.raises(EkologiFel, match="FLODEN_MAX_EKOLOGI_CELLER"):
        los_kretspar(myta, mask_a, mask_b, grannskap=4)
