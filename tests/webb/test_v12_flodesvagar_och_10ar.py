"""Tester för fixarna av flödesvägar, sänkor, vektorisolering och 10-årsregn (Våg 12).

Verifierar:
1. `analysvektor.sankor_geojson`: `Counter`-optimeringen och topp-`MAX_SANKOR`-förfiltreringen ger
   identiska fält (`antal_totalt`, `visade`, `dolda`, `delsankor`, sorteringsordning) även vid
   många sänkor (> `MAX_SANKOR`).
2. `FragaTjanst.vektor(..., "flodesvagar")`: hoppar över `bygg_sankgraf`, sparar bascachen
   `avrinningsnat.<scenario>.v3.npz` och återanvänder den vid reglageändringar (`min_yta_m2`).
3. Processisolerad vektorberäkning (`exekutor="process"`): kör både `flodesvagar` och `sankor`
   i kortlivad barnprocess (`kor_vektor_i_barnprocess`), och omvandlar RAM-/timeout-fel till
   tydligt HTTP 503 utan att påverka servern.
4. Finkorniga lås: `sankor` och `flodesvagar` blockerar inte varandra eller `lager_png`.
5. `_hitta_pagande` jämför `plan_nyckel`: ny körning med `10-årsregn` på samma frågereferens
   återanvänder aldrig en pågående `Standard`-körning.
6. `FlodenAsgiApp`: vektorsemaforen skyddar `to_thread`-poolen så att `POST .../lage-idag/kor`
   (10-årsregn) och `GET /api/halsa` svarar direkt även under tunga vektoranrop.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import numpy as np
import pytest

from sankgraf.hierarki import bygg_sankgraf
from testsvit.k1_io import GeoTransformInfo, skriv_geotiff
from webb import analysvektor
from webb.asgi import FlodenAsgiApp
from webb.fel import FlodenWebbFel
from webb.fragor import FragaTjanst, Korning
from webb.subproc_kor import SubprocMinneFel, SubprocTimeoutFel


def _skriv_syntetisk_k1(mapp: Path, *, hojd: int = 40, bredd: int = 40) -> None:
    """Skriver en minimal K1-ruta med en tydlig V-dal och en sänka."""
    mapp.mkdir(parents=True, exist_ok=True)
    yy, xx = np.mgrid[0:hojd, 0:bredd].astype(np.float32)
    z = (10.0 + 0.05 * np.abs(xx - bredd / 2.0) + 0.02 * (hojd - yy)).astype(np.float32)
    # Tydlig sänka i övre vänstra delen
    z[8:14, 6:12] = np.float32(9.2)
    osak = np.full((hojd, bredd), 0.05, dtype=np.float32)
    slag = np.full((hojd, bredd), 1, dtype=np.uint8)
    skriv_geotiff(mapp / "mark.tif", z, x_min=150000.0, y_max=6580000.0, pixelstorlek=1.0)
    skriv_geotiff(mapp / "osakerhet.tif", osak, x_min=150000.0, y_max=6580000.0, pixelstorlek=1.0)
    skriv_geotiff(mapp / "markslag.tif", slag, x_min=150000.0, y_max=6580000.0, pixelstorlek=1.0)


def _bygg_vatten_korning(tmp_path: Path, *, exekutor: str = "trad") -> tuple[FragaTjanst, str]:
    """Skapar en komplett syntetisk K20-vattenkörning med en K1-ruta och `run.json`."""
    k1 = tmp_path / "bas" / "r1"
    _skriv_syntetisk_k1(k1)
    fragor_dir = tmp_path / "fragor"
    fragor_dir.mkdir(parents=True, exist_ok=True)
    fraga_fil = fragor_dir / "lage-idag-proj-vatten.json"
    fraga_fil.write_text(
        json.dumps(
            {
                "kontrakt": "K20/v1",
                "fraga_id": "lage-idag-proj-vatten",
                "beskrivning": "Test",
                "scenarier": {"fore": {"bas": str(tmp_path / "bas")}},
                "analyser": [
                    {
                        "id": "skyfall",
                        "losare": "vatten.statisk_fyllning",
                        "scenarier": ["fore"],
                        "parametrar": {"regn_mm": 50.0, "takvatten": False},
                    }
                ],
                "jamforelser": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    tjanst = FragaTjanst(
        rot=tmp_path,
        fragemappar=[fragor_dir],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
        exekutor=exekutor,
    )
    status, _ = tjanst.kor_synkront("lage-idag-proj-vatten", "k-v12-test")
    assert status["status"] == "klar"
    return tjanst, "k-v12-test"


def test_sankor_geojson_manga_sankor_bevarar_antal_och_ordning() -> None:
    """Topp-`MAX_SANKOR` och `Counter` ger exakt `antal_totalt`, `dolda` och `visade`."""
    # Skapa ett 90x90-rutnät med 22x22 = 484 distinkta sänkor (> MAX_SANKOR = 400).
    h, b = 90, 90
    z = np.full((h, b), 10.0, dtype=np.float32)
    for r in range(2, h - 2, 4):
        for c in range(2, b - 2, 4):
            # Variera djupet så kapaciteten blir strikt olika
            djup = 0.2 + 0.001 * (r * b + c) / 100.0
            z[r, c] = np.float32(10.0 - djup)
    osak = np.full((h, b), 0.01, dtype=np.float32)
    slag = np.full((h, b), 1, dtype=np.uint8)
    geo = GeoTransformInfo(
        bredd=b,
        hojd=h,
        x_min=0.0,
        y_max=float(h),
        pixelstorlek_x=1.0,
        pixelstorlek_y=1.0,
        epsg=3011,
        dtype="float32",
    )
    graf = bygg_sankgraf(z, osak, slag, geo, anvand_rust=False)
    res = analysvektor.sankor_geojson(graf, min_volym_m3=0.0, min_djup_m=0.05)
    assert res["meta"]["antal_totalt"] > analysvektor.MAX_SANKOR
    assert res["meta"]["visade"] == analysvektor.MAX_SANKOR
    assert len(res["features"]) == analysvektor.MAX_SANKOR
    assert res["meta"]["dolda"] == 0
    kap = [f["properties"]["kapacitet_m3"] for f in res["features"]]
    assert kap == sorted(kap, reverse=True)
    assert all(f["properties"]["delsankor"] >= 1 for f in res["features"])

    # Hög tröskel döljer allt utan att krascha
    tom = analysvektor.sankor_geojson(graf, min_volym_m3=1e6, min_djup_m=0.05)
    assert tom["features"] == []
    assert tom["meta"]["visade"] == 0
    assert tom["meta"]["dolda"] == res["meta"]["antal_totalt"]
    assert tom["meta"]["antal_totalt"] == res["meta"]["antal_totalt"]


def test_flodesvagar_hoppar_over_sankgraf_och_ateranvander_bas_npz(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`flodesvagar` hoppar över `bygg_sankgraf` och återanvänder `.npz` vid reglagebyte."""
    tjanst, kid = _bygg_vatten_korning(tmp_path, exekutor="trad")
    try:
        # Förbjud `bygg_sankgraf` under `flodesvagar`
        def _forbjuden(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("flodesvagar får inte bygga hela SankGraf")

        monkeypatch.setattr("sankgraf.hierarki.bygg_sankgraf", _forbjuden)

        v500 = tjanst.vektor(kid, "fore", "flodesvagar", min_yta_m2=100.0)
        assert v500["slag"] == "flodesvagar"
        assert len(v500["features"]) > 0
        bas_npz = tmp_path / "korningar" / kid / "webb" / "avrinningsnat.fore.v3.npz"
        assert bas_npz.is_file()

        # Andra reglageläget (50 m²) läser ur `bas_npz` utan att köra `berakna_avrinningsfalt`
        with mock.patch(
            "sankgraf.avrinningsnat.berakna_avrinningsfalt",
            side_effect=AssertionError("Borde ha använt avrinningsnat.fore.v3.npz"),
        ):
            v50 = tjanst.vektor(kid, "fore", "flodesvagar", min_yta_m2=50.0)
        assert v50["meta"]["min_yta_m2"] == 50.0
        assert len(v50["features"]) >= len(v500["features"])
    finally:
        tjanst.stang()


def test_vektor_i_barnprocess_och_tydligt_503_vid_minnes_eller_tidsfel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I `process`-läge körs `flodesvagar` och `sankor` i barnprocess; RAM/timeout ger 503."""
    tjanst, kid = _bygg_vatten_korning(tmp_path, exekutor="process")
    try:
        v_flode = tjanst.vektor(kid, "fore", "flodesvagar", min_yta_m2=100.0)
        assert v_flode["slag"] == "flodesvagar"
        assert len(v_flode["features"]) > 0
        assert (tmp_path / "korningar" / kid / "webb" / "avrinningsnat.fore.v3.npz").is_file()

        v_sank = tjanst.vektor(kid, "fore", "sankor", min_volym_m3=0.1, min_djup_m=0.05)
        assert v_sank["slag"] == "sankor"
        assert v_sank["meta"]["antal_totalt"] >= 1

        # RAM-gräns / timeout i barnprocess ska ge tydligt HTTP 503-fel (aldrig tyst eller 500)
        monkeypatch.setattr(
            "webb.fragor.kor_vektor_i_barnprocess",
            mock.Mock(side_effect=SubprocMinneFel("Överskred RAM-gränsen.")),
        )
        with pytest.raises(FlodenWebbFel) as exc_ram:
            tjanst.vektor(kid, "fore", "sankor", min_volym_m3=0.5, min_djup_m=0.05)
        assert exc_ram.value.status_kod == 503

        monkeypatch.setattr(
            "webb.fragor.kor_vektor_i_barnprocess",
            mock.Mock(side_effect=SubprocTimeoutFel("Överskred tidsgränsen.")),
        )
        with pytest.raises(FlodenWebbFel) as exc_tid:
            tjanst.vektor(kid, "fore", "sankor", min_volym_m3=0.7, min_djup_m=0.05)
        assert exc_tid.value.status_kod == 503
    finally:
        tjanst.stang()


def test_finkorniga_vektorlas_blockerar_inte_varandra(tmp_path: Path) -> None:
    """`sankor`, `flodesvagar` och körningens eget lås ska vara separata lås."""
    tjanst = FragaTjanst(
        rot=tmp_path,
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    try:
        l_kor = tjanst._las_for_korning("k1")  # noqa: SLF001
        l_sank = tjanst._las_for_nyckel("vektor:k1:fore:sankor")  # noqa: SLF001
        l_flode = tjanst._las_for_nyckel("vektor:k1:fore:flodesvagar")  # noqa: SLF001
        assert l_kor is not l_sank
        assert l_sank is not l_flode
        assert l_kor is not l_flode
        tjanst.glom_korningar(["k1"])
        assert "k1" not in tjanst._korningslas  # noqa: SLF001
        assert "vektor:k1:fore:sankor" not in tjanst._korningslas  # noqa: SLF001
        assert "vektor:k1:fore:flodesvagar" not in tjanst._korningslas  # noqa: SLF001
    finally:
        tjanst.stang()


def test_hitta_pagande_skiljer_pa_plan_nyckel_vid_byte_till_10_arsregn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Annan `plan_nyckel` (t.ex. 10-årsregn) får inte återanvända pågående Standard-körning."""
    tjanst = FragaTjanst(
        rot=tmp_path,
        fragemappar=[tmp_path / "fragor"],
        cachemapp=tmp_path / "cache",
        korningsmapp=tmp_path / "korningar",
    )
    slapp = threading.Event()
    plan_nr = 0

    monkeypatch.setattr(
        FragaTjanst,
        "_las",
        lambda self, ref: (tmp_path / f"{ref}.json", SimpleNamespace(fraga_id=ref)),
    )

    def _planera_sekvens(self: FragaTjanst, fraga: Any) -> Any:
        nonlocal plan_nr
        plan_nr += 1
        nyckel = ("a" * 64) if plan_nr <= 2 else ("b" * 64)
        return SimpleNamespace(jobb=[SimpleNamespace(nyckel=nyckel)])

    def _kor_vanta(self: FragaTjanst, k: Korning, fraga: Any, plan: Any) -> None:
        with self._lock:  # noqa: SLF001
            k.status = "kor"
            self._skriv_status(k)  # noqa: SLF001
        slapp.wait(timeout=5.0)
        with self._lock:  # noqa: SLF001
            k.status = "klar"
            self._skriv_status(k)  # noqa: SLF001

    monkeypatch.setattr(FragaTjanst, "_planera", _planera_sekvens)
    monkeypatch.setattr(FragaTjanst, "_kor", _kor_vanta)

    try:
        s1 = tjanst.starta_korning("lage-idag-p-vatten", "proj-1")
        assert s1["ateranvand"] is False
        # Samma plan_nyckel -> återanvänds
        s2 = tjanst.starta_korning("lage-idag-p-vatten", "proj-1")
        assert s2["ateranvand"] is True
        assert s2["korning_id"] == s1["korning_id"]
        # Annan plan_nyckel (10-årsregn) -> ny körning skapas i kön
        s3 = tjanst.starta_korning("lage-idag-p-vatten", "proj-1")
        assert s3["ateranvand"] is False
        assert s3["korning_id"] != s1["korning_id"]
    finally:
        slapp.set()
        tjanst.stang()


def test_asgi_vektor_semafor_svalter_aldrig_lage_idag_kor_eller_halsa() -> None:
    """Vektorsemaforen hindrar `GET /vektor` från att svälta `POST .../kor` & `/api/halsa`."""
    vektor_inne = threading.Event()
    vektor_antal = 0
    vektor_lock = threading.Lock()
    slapp_vektor = threading.Event()

    class _FakeApi:
        def hantera_http(
            self,
            metod: str,
            sokvag: str,
            rubriker: dict[str, str],
            hamta_kropp: Any,
        ) -> tuple[int, list[tuple[str, str]], bytes]:
            nonlocal vektor_antal
            if "/vektor/" in sokvag:
                with vektor_lock:
                    vektor_antal += 1
                    if vektor_antal >= 2:
                        vektor_inne.set()
                slapp_vektor.wait(timeout=5.0)
                return 200, [("Content-Type", "application/json")], b'{"features":[]}\n'
            if sokvag.endswith("/lage-idag/kor"):
                return 202, [("Content-Type", "application/json")], b'{"lage":"idag","fragor":[]}\n'
            return 200, [("Content-Type", "application/json")], b'{"status":"ok"}\n'

    asgi_app = FlodenAsgiApp(
        _FakeApi(),  # type: ignore[arg-type]
        max_samtidiga_berakningar=2,
        ko_timeout_s=0.1,
    )

    async def _anrop(metod: str, sokvag: str, kropp: bytes = b"") -> tuple[int, dict[str, str]]:
        skickat = False

        async def receive() -> dict[str, Any]:
            nonlocal skickat
            if skickat:
                return {"type": "http.request", "body": b"", "more_body": False}
            skickat = True
            return {"type": "http.request", "body": kropp, "more_body": False}

        status_ut = 0
        rubriker_ut: dict[str, str] = {}

        async def send(msg: dict[str, Any]) -> None:
            nonlocal status_ut
            if msg["type"] == "http.response.start":
                status_ut = int(msg["status"])
                for k, v in msg["headers"]:
                    rubriker_ut[k.decode("latin-1").lower()] = v.decode("latin-1")

        scope = {
            "type": "http",
            "method": metod,
            "path": sokvag,
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
        await asgi_app(scope, receive, send)
        return status_ut, rubriker_ut

    async def _kor_scenario() -> None:
        # Starta 2 tunga vektoranrop (fyller vektorsemaforen med 2 platser)
        v1 = asyncio.create_task(_anrop("GET", "/api/korningar/k1/vektor/fore/sankor"))
        v2 = asyncio.create_task(_anrop("GET", "/api/korningar/k1/vektor/fore/flodesvagar"))
        await asyncio.to_thread(vektor_inne.wait, 3.0)
        assert vektor_inne.is_set()

        # Medan vektoranrop blockerar ska POST /lage-idag/kor (10-årsregn) och /api/halsa svara!
        t0 = time.monotonic()
        st_kor, _ = await _anrop(
            "POST", "/api/kartverktyg/p1/lage-idag/kor", b'{"analyser":["vatten.skyfall"]}'
        )
        st_halsa, _ = await _anrop("GET", "/api/halsa")
        dt = time.monotonic() - t0
        assert st_kor == 202
        assert st_halsa == 200
        assert dt < 1.0, f"POST /lage-idag/kor eller /api/halsa blockerades ({dt:.2f}s)"

        # Ett 3:e samtidigt vektoranrop ska få 503 + Retry-After i stället för att svälta poolen
        v3 = asyncio.create_task(_anrop("GET", "/api/korningar/k1/vektor/fore/flodesvagar"))
        st3, hdr3 = await v3
        assert st3 == 503
        assert hdr3.get("retry-after") == "2"

        slapp_vektor.set()
        st1, _ = await v1
        st2, _ = await v2
        assert st1 == 200 and st2 == 200

    asyncio.run(_kor_scenario())
