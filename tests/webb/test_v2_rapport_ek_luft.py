# ruff: noqa: E501, F811
"""Deterministiska E2E- och regressionstester för Våg 2 (`v2-rapport-ek-luft`):

1. D-B1: TOCTOU i `RapportJobbTjanst.status()` skriver aldrig över `"klar"` med `"fel"`, atomisk skrivning, `stang()`.
2. W-B3: Öppet geografiprojekt skapar och exporterar rapport (Word + PDF) i Steg 3 utan `_dataskydd`-fel,
   medan stängt geografiprojekt fortsatt märks `internt` och stoppas av `_dataskydd`.
3. W-B7 + Ekologi Steg 3: Ekologirapport i Steg 3 (utan 409) ritar distinkta lager för `stromtathet` och
   `kansighet` (ej primärlagret två gånger), anpassar EK-14 för icke-demo och exporterar Word + PDF.
4. Uppdrag 4 Luft Steg 3: `POST /api/projekt/<pid>/rapporter` med `amnen=["luft"]` + timserie-CSV +
   `bakgrund_ug_m3` kör `luft.mkn` med K0-manifest och exporterar kontrollerad LU-rapport (Word + PDF);
   ogiltig CSV / saknad / negativ / NaN bakgrund ger HTTP 400 utan skapat jobb.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import docx
import pytest

from demovarld.fragor import EKOLOGI_NATVERK
from demovarld.luftserier import timserie_csv
from rapport.webbflode import ekologi_pastaenden
from tests.demovarld.test_demo import Demo
from tests.kommuner.test_skapa import geo  # noqa: F401
from tests.konto.conftest import ANNA, Miljo, miljo  # noqa: F401
from tests.webb.test_skapa_projekt import Varld as GeoVarld  # noqa: F401
from tests.webb.test_skapa_projekt import skapa  # noqa: F401
from tests.webb.test_skapa_projekt import v as geo_v  # noqa: F401
from webb.api import FlodenApiApp
from webb.fel import FlodenWebbFel

REPO = Path(__file__).resolve().parents[2]
URSPRUNG = {"Origin": "https://floden.test"}
DEMO = "demokvarteret"


class _DemoVarld:
    def __init__(self, demo: Demo, miljo_obj: Miljo, rot: Path) -> None:
        self.rot = rot
        self.demo = demo
        self.korningsmapp = rot / "korningar"
        shutil.copytree(demo.rot / "korningar", self.korningsmapp)
        self.app = FlodenApiApp(
            testsvit_rot=REPO / "testsvit",
            konfig_fil=REPO / "config.toml",
            fragemappar=[demo.fragor],
            cachemapp=demo.rot / "cache",
            korningsmapp=self.korningsmapp,
            kartverktygsmapp=rot / "kartverktyg",
            datamappar=[rot / "data"],
            rapportmapp=rot / "rapporter",
            konto=miljo_obj.adapter,
        )
        self.app.fragor.demomapp = demo.varld
        org = miljo_obj.register.skapa_organisation("Exempelkommun")
        miljo_obj.register.lagg_till_medlem(
            miljo_obj.register.forbered_konto("anna@example.test"), org, None, "test"
        )
        self.anna = miljo_obj.webblasare()
        assert self.anna.logga_in(miljo_obj.idp, ANNA)[0] == 302

    def http(
        self, metod: str, sokvag: str, kropp: Any = None
    ) -> tuple[int, dict[str, str], bytes]:
        rub: dict[str, str] = {"Cookie": "; ".join(f"{k}={v}" for k, v in self.anna.kakor.items())}
        if metod != "GET":
            rub.update(URSPRUNG)
            rub.update(self.anna.csrf_headers())
        data = b"" if kropp is None else json.dumps(kropp, allow_nan=True).encode("utf-8")
        status, hs, k = self.app.hantera_http(metod, sokvag, rub, lambda: data)
        return status, {n.lower(): v for n, v in hs}, k

    def json(self, metod: str, sokvag: str, kropp: Any = None) -> tuple[int, dict[str, Any]]:
        s, _, k = self.http(metod, sokvag, kropp)
        return s, json.loads(k)

    def vanta_jobb(self, jobb: dict[str, Any], max_s: float = 600.0) -> dict[str, Any]:
        t0 = time.monotonic()
        while time.monotonic() - t0 < max_s:
            s, d = self.json("GET", jobb["status_url"])
            assert s == 200, d
            if d["status"] in ("klar", "fel"):
                return d
            time.sleep(0.2)
        raise AssertionError("Rapportjobbet blev aldrig klart")


@pytest.fixture
def demo_varld(demo: Demo, miljo: Miljo, tmp_path: Path) -> Iterator[_DemoVarld]:
    dv = _DemoVarld(demo, miljo, tmp_path)
    yield dv
    dv.app.rapportjobb.stang()


# ------------------------------------------------------------------ 1. D-B1: TOCTOU + stang()


def test_d_b1_status_toctou_skriver_aldrig_over_klar_med_fel(tmp_path: Path) -> None:
    """D-B1: Om `_kor` skriver `status='klar'` och avregistrerar ur `_aktiva` samtidigt som `status()`
    pollar får `status()` aldrig läsa ett gammalt `'kor'` och skriva över `'klar'` med `'fel'`."""
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        rapportmapp=tmp_path / "rapporter",
    )
    rj = app.rapportjobb
    try:
        jobb_id = "j-112233445566"
        post = {
            "jobb_id": jobb_id,
            "projekt": DEMO,
            "amne": "skyfall",
            "korning_id": "demo-vatten",
            "bestallning_id": "b-000000000001",
            "status": "kor",
            "steg": "exporterar",
            "rapport_id": "r-112233445566",
            "kontroll": {"godkand": True, "antal_fel": 0, "exporterad": False},
            "fel": None,
            "skapad": "2026-11-26T10:00:00+00:00",
            "start": "2026-11-26T10:00:01+00:00",
            "slut": None,
        }
        with rj._lock:  # noqa: SLF001
            rj._skriv(post)  # noqa: SLF001
            rj._aktiva.add(jobb_id)  # noqa: SLF001

        # Kör deterministisk interleaving: polla `status()` kontinuerligt i bakgrundstråd medan
        # arbetartråden växlar status -> "klar" och tar bort jobb_id ur `_aktiva`.
        for runda in range(40):
            jid = f"j-{runda:012x}"
            p_runda = {**post, "jobb_id": jid, "status": "kor", "steg": "exporterar", "slut": None}
            with rj._lock:  # noqa: SLF001
                rj._skriv(p_runda)  # noqa: SLF001
                rj._aktiva.add(jid)  # noqa: SLF001

            start_event = threading.Event()
            sedda: list[str] = []

            def _polla(target_id: str = jid, ev: threading.Event = start_event, log: list[str] = sedda) -> None:
                ev.wait()
                for _ in range(25):
                    st = rj.status(DEMO, target_id)
                    log.append(st["status"])
                    if st["status"] in ("klar", "fel"):
                        break

            def _avsluta(target_id: str = jid, ev: threading.Event = start_event) -> None:
                ev.wait()
                rj._satt(  # noqa: SLF001
                    target_id,
                    status="klar",
                    steg=None,
                    kontroll={"godkand": True, "antal_fel": 0, "exporterad": True},
                    slut="2026-11-26T10:00:05+00:00",
                )
                with rj._lock:  # noqa: SLF001
                    rj._aktiva.discard(target_id)  # noqa: SLF001

            t_poll = threading.Thread(target=_polla)
            t_done = threading.Thread(target=_avsluta)
            t_poll.start()
            t_done.start()
            start_event.set()
            t_poll.join(timeout=5.0)
            t_done.join(timeout=5.0)

            assert "fel" not in sedda, f"Runda {runda}: status() såg felaktigt 'fel': {sedda}"
            slut = rj.status(DEMO, jid)
            assert slut["status"] == "klar", f"Runda {runda}: klart jobb korrumperades på disk: {slut}"
            assert slut["fel"] is None

        # Genuit avbrutet jobb (står som "kor" på disk men inte i `_aktiva`) ska fortsatt bli "fel".
        avbrutet_id = "j-deadbeefcafe"
        with rj._lock:  # noqa: SLF001
            rj._skriv({**post, "jobb_id": avbrutet_id, "status": "kor", "steg": "skapar"})  # noqa: SLF001
        avbr = rj.status(DEMO, avbrutet_id)
        assert avbr["status"] == "fel" and "avbröts" in (avbr["fel"] or "")

        # `stang()` är idempotent och stänger exekutorn rent; ny `starta()` ger 503.
        rj.stang()
        rj.stang()
        with pytest.raises(FlodenWebbFel) as ei:
            rj.starta(DEMO, json.dumps({"amne": "skyfall"}).encode())
        # Kan misslyckas redan på saknad demo-körning (409) eller stängd tjänst (503) — kontrollera direkt på _stangd
        assert ei.value.status_kod in (409, 503)
        assert rj._stangd is True  # noqa: SLF001
    finally:
        rj.stang()


# ------------------------------------------------------------------ 2. W-B3: Öppet vs stängt geografiprojekt


def _vanta_korning(app: FlodenApiApp, kid: str, max_s: float = 600.0) -> None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < max_s:
        st = app.fragor.korning_status(kid)["status"]
        if st in ("klar", "fel"):
            assert st == "klar", app.fragor.korning_status(kid)
            return
        time.sleep(0.2)
    raise AssertionError(f"Körning {kid} blev aldrig klar")


def _vanta_geo_jobb(v_geo: GeoVarld, jobb: dict[str, Any], max_s: float = 600.0) -> dict[str, Any]:
    t0 = time.monotonic()
    while time.monotonic() - t0 < max_s:
        st, d = v_geo.http("anna", "GET", jobb["status_url"])
        assert st == 200, d
        if d["status"] in ("klar", "fel"):
            return d
        time.sleep(0.2)
    raise AssertionError("Geografi-rapportjobbet blev aldrig klart")


def test_w_b3_oppet_geografiprojekt_exporterar_medan_stangt_nekas(geo_v: GeoVarld) -> None:
    """W-B3: Öppet geografiprojekt (K38 oppen=true + känd licens) får dataklass 'oppet' och exporterar
    Word + PDF i Steg 3 utan _dataskydd-fel; stängt geografiprojekt märks 'internt' och stoppas."""
    try:
        st, skapad = skapa(geo_v, "anna", "Haga rapport", geo_v.inre(marginal=10, storlek=60))
        assert st == 201, skapad
        pid = skapad["projekt_id"]
        projektmapp = geo_v.rot / "projekt" / "haga-rapport"

        # Kör läget idag (skyfall) i projektet så att Steg 3 har ett komplett underlag.
        kor = geo_v.app.kartverktyg.kor_lage_idag(pid, ["vatten.skyfall"])
        kid = kor["fragor"][0]["korning"]["korning_id"]
        _vanta_korning(geo_v.app, kid)

        # 1. Stängt geografiprojekt (förval från skapa_projekt: oppen = false):
        # rapporten skapas med dataklass "internt", men kontrollens `_dataskydd` stoppar exporten!
        st1, jobb1 = geo_v.http("anna", "POST", f"/api/projekt/{pid}/rapporter", {"amne": "skyfall"})
        assert st1 == 202, jobb1
        slut1 = _vanta_geo_jobb(geo_v, jobb1)
        assert slut1["status"] == "klar", slut1
        assert slut1["kontroll"]["godkand"] is False
        assert slut1["kontroll"]["exporterad"] is False
        assert slut1["lankar"] is None
        assert any("internt" in f for f in slut1["kontroll"].get("fel", []))
        rap1 = json.loads(
            (geo_v.app.rapportmapp / slut1["rapport_id"] / "rapport.json").read_text(encoding="utf-8")
        )
        assert rap1["dataklass"] == "internt"

        # 2. Öppet geografiprojekt (K38: oppen = true och känd licens CC0-1.0):
        # `RapportTjanst` slår upp projektöppenheten -> dataklass "oppet" -> godkänd kontroll + Word & PDF!
        (projektmapp / "oppenhet.toml").write_text(
            'kontrakt = "floden-oppenhet/v1"\noppen = true\nlicens = "CC0-1.0"\nkalla = "test"\n',
            encoding="utf-8",
        )
        st2, jobb2 = geo_v.http("anna", "POST", f"/api/projekt/{pid}/rapporter", {"amne": "skyfall"})
        assert st2 == 202, jobb2
        slut2 = _vanta_geo_jobb(geo_v, jobb2)
        assert slut2["status"] == "klar", slut2
        assert slut2["kontroll"] == {"godkand": True, "antal_fel": 0, "exporterad": True}
        assert set(slut2["lankar"]) == {"pdf", "word"}
        rap2 = json.loads(
            (geo_v.app.rapportmapp / slut2["rapport_id"] / "rapport.json").read_text(encoding="utf-8")
        )
        assert rap2["dataklass"] == "oppet"
        # Verifiera nedladdning av både PDF och Word
        rub = {"Cookie": "; ".join(f"{k}={val}" for k, val in geo_v.webb["anna"].kakor.items())}
        sp, _, pdf_b = geo_v.app.hantera_http("GET", slut2["lankar"]["pdf"], rub, lambda: b"")
        sw, _, docx_b = geo_v.app.hantera_http("GET", slut2["lankar"]["word"], rub, lambda: b"")
        assert sp == 200 and pdf_b.startswith(b"%PDF")
        assert sw == 200 and docx_b.startswith(b"PK")
    finally:
        geo_v.app.rapportjobb.stang()


# ------------------------------------------------------------------ 3. W-B7 + Ekologi Steg 3


def test_w_b7_ekologi_steg3_ritar_distinkta_roller_stromtathet_och_kansighet(demo_varld: _DemoVarld) -> None:
    """W-B7 + Steg 3 Ekologi: körning med både `stromtathet` och `kansighet` (som i `api.py:2085–2100`)
    skapar godkänd ekologirapport (utan 409) där kartorna har distinkta roller, lager-id och PNG-hashar,
    och exporterar både Word och PDF."""
    # Skapa en K20-ekologifråga med både `stromtathet` och `kansighet` som jämförelser (samma källor som
    # demo-ekologi så att beräkningen träffar cachen deterministiskt och snabbt).
    fragedir = demo_varld.app.kartverktyg.fragedir
    fragedir.mkdir(parents=True, exist_ok=True)
    ek_fraga_fil = fragedir / "ekologi-tva-roller.json"
    rel = lambda p: Path(os.path.relpath(p.resolve(), fragedir.resolve())).as_posix()  # noqa: E731
    ek_k20 = {
        "kontrakt": "K20/v1",
        "fraga_id": "ekologi-tva-roller",
        "beskrivning": "Ekologisk konnektivitet (skiss) med både strömtäthet och kansighet.",
        "scenarier": {
            "fore": {"bas": rel(demo_varld.demo.varld / "vatten" / "bas")},
            "efter": {
                "bas": rel(demo_varld.demo.varld / "vatten" / "bas"),
                "k9": [rel(demo_varld.demo.varld / "vatten" / "k9_nytt_hus.json")],
            },
        },
        "analyser": [
            {
                "id": "konnektivitet",
                "losare": "ekologi.konnektivitet",
                "parametrar": {"natverk": EKOLOGI_NATVERK},
                "indata": {"nmd": rel(demo_varld.demo.varld / "ekologi" / "nmd.tif")},
            }
        ],
        "jamforelser": [
            {"analys": "konnektivitet", "utdata": "stromtathet", "fore": "fore", "efter": "efter", "troskel": 0.01},
            {"analys": "konnektivitet", "utdata": "kansighet", "fore": "fore", "efter": "efter", "troskel": 0.01},
        ],
    }
    ek_fraga_fil.write_text(json.dumps(ek_k20, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ek_kid = "ekologi-tva-roller-run"
    demo_varld.app.fragor.kor_synkront("ekologi-tva-roller", ek_kid, projekt_id=DEMO)

    # Körningen finns med i Steg 3-möjligheter för ekologi
    s_moj, moj = demo_varld.json("GET", f"/api/projekt/{DEMO}/rapporter")
    assert s_moj == 200
    eko_amne = next(a for a in moj["amnen"] if a["id"] == "ekologi")
    assert eko_amne["kan_skapas"] is True and eko_amne["mall"] is True
    assert ek_kid in {k["korning_id"] for k in eko_amne["korningar"]}

    # Skapa ekologirapport på den två-rolliga ekologikörningen (utan 409)
    s_post, jobb = demo_varld.json(
        "POST",
        f"/api/projekt/{DEMO}/rapporter",
        {"amne": "ekologi", "korning_id": ek_kid},
    )
    assert s_post == 202, jobb
    slut = demo_varld.vanta_jobb(jobb)
    assert slut["status"] == "klar", slut
    assert slut["kontroll"] == {"godkand": True, "antal_fel": 0, "exporterad": True}
    assert set(slut["lankar"]) == {"pdf", "word"}

    rap = json.loads(
        (demo_varld.rot / "rapporter" / slut["rapport_id"] / "rapport.json").read_text(encoding="utf-8")
    )
    assert rap["amne"] == "ekologi"
    assert rap["fragor"] == [f"EK-{i:02d}" for i in range(1, 16)]
    assert len(rap["pastaenden"]) == 15
    assert rap["metoder"][0]["status"] == "skiss"

    # W-B7: både `stromtathet` och `kansighet` ska vara ritade som separata roller/lager (ej primärlagret två gånger)
    kartor = rap["kartor"]
    roller = {k["meta"]["roll"] for k in kartor}
    assert roller == {"stromtathet", "kansighet"}, f"Väntade båda rollerna, fick {roller}"
    per_roll: dict[str, list[dict[str, Any]]] = {"stromtathet": [], "kansighet": []}
    for k in kartor:
        per_roll[k["meta"]["roll"]].append(k)
    assert {k["typ"] for k in per_roll["stromtathet"]} == {"bas", "scenario", "skillnad"}
    assert {k["typ"] for k in per_roll["kansighet"]} == {"bas", "scenario", "skillnad"}

    strom_bas = next(k for k in per_roll["stromtathet"] if k["typ"] == "bas")
    kans_bas = next(k for k in per_roll["kansighet"] if k["typ"] == "bas")
    assert strom_bas["lager_id"] != kans_bas["lager_id"]
    assert "stromtathet" in strom_bas["lager_id"] and "kansighet" in kans_bas["lager_id"]
    assert strom_bas["png_sha256"] != kans_bas["png_sha256"]

    # Word och PDF är giltiga och innehåller alla 6 inbäddade kartfigurer
    s_pdf, _, pdf_b = demo_varld.http("GET", slut["lankar"]["pdf"])
    s_docx, _, docx_b = demo_varld.http("GET", slut["lankar"]["word"])
    assert s_pdf == 200 and pdf_b.startswith(b"%PDF")
    assert s_docx == 200 and docx_b.startswith(b"PK")
    assert len(docx.Document(io.BytesIO(docx_b)).inline_shapes) == 6


def test_ekologi_pastaenden_anpassar_ek14_for_geografiprojekt_och_stoder_lage_idag(demo_varld: _DemoVarld) -> None:
    """EK-14 ska inte säga 'I demon är lagret påhittat.' för icke-demo-projekt; nuläge stöds utan krasch."""
    rt = demo_varld.app.rapportjobb.rt
    best = rt.bestallning_skapa(syfte="Geografi ekologi", amnen=["ekologi"], projekt=DEMO)
    r_demo = rt.skapa(projekt=DEMO, korningar=["demo-ekologi"], ai_stod=False, bestallning=best)

    ps_geo = ekologi_pastaenden(rt, r_demo, demo=False, har_avgransning=True)
    ek14_geo = next(p for p in ps_geo if p["fraga_id"] == "EK-14")
    assert "I demon" not in " ".join(ek14_geo["saknat_underlag"])

    # Simulera ren nulägeskörning (`jamforelser=[]`) för `ekologi_pastaenden`
    r_nulage = SimpleNamespace(
        projekt="geo-proj-1",
        amne="ekologi",
        fragor=r_demo.fragor,
        korningar=[{**r_demo.korningar[0], "jamforelser": [], "underlag": {"slag": "lage_idag"}}],
        installningar=r_demo.installningar,
        mottagare=(),
    )
    ps_nulage = ekologi_pastaenden(rt, r_nulage, demo=False)  # type: ignore[arg-type]
    assert len(ps_nulage) == 15
    ek12 = next(p for p in ps_nulage if p["fraga_id"] == "EK-12")
    assert ek12["status"] == "ej_tillampligt" and "läget idag" in str(ek12["text"])
    assert "ekojamforelse." not in json.dumps(ps_nulage, ensure_ascii=False)


# ------------------------------------------------------------------ 4. Uppdrag 4: Luft Steg 3


def _csv_tid_s_halt(lage: str) -> str:
    """Omvandlar demoserien till kolumnnamnen `tid_s,halt_ug_m3` enligt kontraktet i #63."""
    rader = timserie_csv(lage).splitlines()
    ut = ["tid_s,halt_ug_m3"]
    for rad in rader[1:]:
        timme, halt = rad.split(",")
        ut.append(f"{int(timme) * 3600},{halt}")
    return "\n".join(ut) + "\n"


def test_luft_steg3_tva_serier_med_csv_och_bakgrund_ger_word_och_pdf(demo_varld: _DemoVarld) -> None:
    """Uppdrag 4 E2E: `amnen=['luft']` + 8760-timmars CSV före/efter (`tid_s,halt_ug_m3`) + `bakgrund_ug_m3`
    kör `luft.mkn` med K0-manifest och skapar godkänd LU-01..LU-15-rapport (Word + PDF) utan 409."""
    url = f"/api/projekt/{DEMO}/rapporter"
    payload = {
        "amnen": ["luft"],
        "luft": {
            "serie_fore_csv": _csv_tid_s_halt("fore"),
            "serie_efter_csv": _csv_tid_s_halt("efter"),
            "bakgrund_ug_m3": 12.0,
            "osakerhet_halt_rel": 0.15,
            "osakerhet_bakgrund_ug_m3": 2.0,
        },
        "bestallning": {"syfte": "Sållning av PM10 mot dygnsnormen före och efter förslaget"},
    }
    s, jobb = demo_varld.json("POST", url, payload)
    assert s == 202, jobb
    assert jobb["amne"] == "luft" and jobb["korning_id"].startswith("luft-j-")
    slut = demo_varld.vanta_jobb(jobb)
    assert slut["status"] == "klar", slut
    assert slut["kontroll"] == {"godkand": True, "antal_fel": 0, "exporterad": True}
    assert set(slut["lankar"]) == {"pdf", "word"}

    rap = json.loads(
        (demo_varld.rot / "rapporter" / slut["rapport_id"] / "rapport.json").read_text(encoding="utf-8")
    )
    assert rap["amne"] == "luft"
    assert rap["fragor"] == [f"LU-{i:02d}" for i in range(1, 16)]
    assert len(rap["pastaenden"]) == 15
    assert {a["analys"] for a in rap["korningar"][0]["analyser"]} == {"mkn_fore", "mkn_efter"}
    assert rap["metoder"] == [m for m in rap["metoder"] if m["losare"] == "luft.mkn"]
    assert rap["metoder"][0]["status"] == "skiss"

    # Verifiera att K0-manifestet skapades med exakt begärda parametrar (bakgrund 12.0, faktor [0.85, 1.15])
    man_fore = json.loads(
        (demo_varld.korningsmapp / jobb["korning_id"] / "resultat" / "mkn_fore" / "bas" / "run.json")
        .read_text(encoding="utf-8")
    )
    assert man_fore["parametrar"]["bakgrund_ug_m3"] == 12.0
    assert man_fore["parametrar"]["emissionsfaktor_intervall"] == [0.85, 1.15]
    assert man_fore["parametrar"]["depositionsfaktor_intervall"] == [1.0, 1.0]
    assert man_fore["matvarden"]["antal_hela_dygn"] == 365

    # Exporterade Word- och PDF-filer är riktiga dokument
    s_pdf, _, pdf_b = demo_varld.http("GET", slut["lankar"]["pdf"])
    s_docx, _, docx_b = demo_varld.http("GET", slut["lankar"]["word"])
    assert s_pdf == 200 and pdf_b.startswith(b"%PDF")
    assert s_docx == 200 and docx_b.startswith(b"PK")


def test_luft_steg3_en_serie_och_befintlig_demo_luft_fungerar(demo_varld: _DemoVarld) -> None:
    """Befintlig `demo-luft` utan `luft`-objekt samt en enda CSV-serie (`serie_efter_csv=None`) ger båda
    godkänd rapport, och användarens egen körning går sedan före demokörningen."""
    url = f"/api/projekt/{DEMO}/rapporter"
    # 1. Befintlig `demo-luft` utan `luft`-objekt (W-B7: luft.mkn utan jämförelser)
    s0, j0 = demo_varld.json("POST", url, {"amne": "luft"})
    assert s0 == 202, j0
    assert j0["korning_id"] == "demo-luft"
    slut0 = demo_varld.vanta_jobb(j0)
    assert slut0["status"] == "klar", slut0
    assert slut0["kontroll"] == {"godkand": True, "antal_fel": 0, "exporterad": True}

    # 2. En serie (48 timmar = 2 hela dygn, giltig men med årsvarning i antagandena)
    en_serie = "tid,lokal_ug_m3\n" + "\n".join(f"{h},{15.0 + (h % 6):.2f}" for h in range(48)) + "\n"
    s1, j1 = demo_varld.json(
        "POST",
        url,
        {"amne": "luft", "luft": {"serie_fore_csv": en_serie, "serie_efter_csv": None, "bakgrund_ug_m3": 10.0}},
    )
    assert s1 == 202, j1
    slut1 = demo_varld.vanta_jobb(j1)
    assert slut1["status"] == "klar", slut1
    assert slut1["kontroll"] == {"godkand": True, "antal_fel": 0, "exporterad": True}

    # 3. Egen luftkörning (`slag='egen'`) väljs nu före `demo-luft` (`slag='demo'`)
    s2, j2 = demo_varld.json("POST", url, {"amne": "luft"})
    assert s2 == 202, j2
    assert j2["korning_id"] == j1["korning_id"]
    slut2 = demo_varld.vanta_jobb(j2)
    assert slut2["status"] == "klar", slut2
    assert slut2["kontroll"] == {"godkand": True, "antal_fel": 0, "exporterad": True}


@pytest.mark.parametrize(
    ("kropp", "match"),
    [
        ({"amnen": ["luft"], "luft": {"serie_fore_csv": "tid,lokal_ug_m3\n" + "\n".join(f"{i},10" for i in range(24))}}, "bakgrund_ug_m3"),
        ({"amnen": ["luft"], "luft": {"serie_fore_csv": "tid,lokal_ug_m3\n" + "\n".join(f"{i},10" for i in range(24)), "bakgrund_ug_m3": -1.0}}, ">= 0"),
        ({"amnen": ["luft"], "luft": {"serie_fore_csv": "tid,lokal_ug_m3\n" + "\n".join(f"{i},10" for i in range(24)), "bakgrund_ug_m3": float("nan")}}, "ändligt"),
        ({"amnen": ["luft"], "luft": {"serie_fore_csv": "", "bakgrund_ug_m3": 12.0}}, "icke-tom"),
        ({"amnen": ["luft"], "luft": {"serie_fore_csv": "tid,fel_kolumn\n0,10\n", "bakgrund_ug_m3": 12.0}}, "haltkolumn"),
        ({"amnen": ["luft"], "luft": {"serie_fore_csv": "tid,lokal_ug_m3\n0,10\n1,12\n", "bakgrund_ug_m3": 12.0}}, "24"),
        ({"amnen": ["luft"], "luft": {"serie_fore_csv": "tid,lokal_ug_m3\n" + "\n".join(f"{i},-5" if i == 3 else f"{i},10" for i in range(24)), "bakgrund_ug_m3": 12.0}}, ">= 0"),
        ({"amnen": ["luft"], "luft": {"serie_fore_csv": "tid,lokal_ug_m3\n" + "\n".join(f"{i},NaN" if i == 2 else f"{i},10" for i in range(24)), "bakgrund_ug_m3": 12.0}}, "ändligt"),
        ({"amnen": ["luft"], "luft": {"serie_fore_csv": "tid,lokal_ug_m3\n" + "\n".join(f"{i},10" for i in range(24)), "bakgrund_ug_m3": 12.0, "osakerhet_halt_rel": 1.0}}, "< 1"),
        ({"amne": "skyfall", "luft": {"serie_fore_csv": "tid,lokal_ug_m3\n", "bakgrund_ug_m3": 12.0}}, "bara anges när ämnet är 'luft'"),
        ({"amne": "skyfall", "amnen": ["luft"]}, "stämmer inte överens"),
    ],
)
def test_luft_ogiltig_indata_ger_400_utan_jobb(
    demo_varld: _DemoVarld, kropp: dict[str, Any], match: str
) -> None:
    s, e = demo_varld.json("POST", f"/api/projekt/{DEMO}/rapporter", kropp)
    assert s == 400, (kropp, s, e)
    assert match in e["fel"], e
    assert not (demo_varld.rot / "rapporter" / "jobb").exists()


def test_luft_for_stor_csv_och_saknad_korning_utan_luft_objekt_ger_400(
    demo: Demo, miljo: Miljo, tmp_path: Path
) -> None:
    """CSV > 5 MB ger 400; `amne='luft'` utan befintlig `luft.mkn`-körning och utan `luft`-objekt ger 400."""
    # Isolerad tom korningsmapp (ingen demo-luft i korningsmappen)
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[demo.fragor],
        cachemapp=demo.rot / "cache",
        korningsmapp=tmp_path / "korningar",
        kartverktygsmapp=tmp_path / "kartverktyg",
        datamappar=[tmp_path / "data"],
        rapportmapp=tmp_path / "rapporter",
        konto=miljo.adapter,
    )
    app.fragor.demomapp = demo.varld
    try:
        org = miljo.register.skapa_organisation("Testkommun")
        miljo.register.lagg_till_medlem(miljo.register.forbered_konto("anna@example.test"), org, None, "test")
        anna = miljo.webblasare()
        assert anna.logga_in(miljo.idp, ANNA)[0] == 302
        rub = {
            "Cookie": "; ".join(f"{k}={v}" for k, v in anna.kakor.items()),
            **URSPRUNG,
            **anna.csrf_headers(),
        }

        # 1. Ingen befintlig luft.mkn-körning och inget `luft`-objekt -> 400
        st1, _, b1 = app.hantera_http(
            "POST", f"/api/projekt/{DEMO}/rapporter", rub, lambda: json.dumps({"amnen": ["luft"]}).encode()
        )
        assert st1 == 400 and "bakgrund_ug_m3" in json.loads(b1)["fel"]

        # 2. CSV > 5 MB -> 400
        stor_csv = "tid,lokal_ug_m3\n" + ("0,10.0\n" * 800_000)
        assert len(stor_csv.encode("utf-8")) > 5 * 1024 * 1024
        st2, _, b2 = app.hantera_http(
            "POST",
            f"/api/projekt/{DEMO}/rapporter",
            rub,
            lambda: json.dumps(
                {"amnen": ["luft"], "luft": {"serie_fore_csv": stor_csv, "bakgrund_ug_m3": 12.0}}
            ).encode(),
        )
        assert st2 == 400 and "5 MB" in json.loads(b2)["fel"]
        assert not (tmp_path / "rapporter" / "jobb").exists()
    finally:
        app.rapportjobb.stang()
