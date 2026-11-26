# ruff: noqa: E501
"""Skapa rapport i webben (K48): `POST /api/projekt/<id>/rapporter`, jobbets status, behörighet och CSRF.

Mot demovärldens körningar (litet läge, påhittat). Skyfall och dagsljus körs hela vägen: skapa, påståenden med
belägg, kontroll, export till PDF och Word och nedladdning.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from rapport.webbflode import pastaenden_for, saknad_mall, skyfall_pastaenden
from tests.demovarld.test_demo import Demo
from tests.konto.conftest import ANNA, Miljo, Webblasare
from webb.api import FlodenApiApp

REPO = Path(__file__).resolve().parents[2]
BO = {"sub": "sub-bo-x2", "email": "bo@example.test", "email_verified": True, "name": "Bo Exempel"}
URSPRUNG = {"Origin": "https://floden.test"}
DEMO = "demokvarteret"


class Varld:
    def __init__(self, demo: Demo, miljo: Miljo, rot: Path) -> None:
        self.miljo = miljo
        self.demo = demo
        self.rot = rot
        self.app = FlodenApiApp(
            testsvit_rot=REPO / "testsvit",
            konfig_fil=REPO / "config.toml",
            fragemappar=[demo.fragor],
            cachemapp=demo.rot / "cache",
            korningsmapp=demo.rot / "korningar",
            kartverktygsmapp=rot / "kartverktyg",
            datamappar=[rot / "data"],
            rapportmapp=rot / "rapporter",
            konto=miljo.adapter,
        )
        self.app.fragor.demomapp = demo.varld
        org = miljo.register.skapa_organisation("Exempelkommun")
        for epost in ("anna@example.test", "bo@example.test"):
            miljo.register.lagg_till_medlem(miljo.register.forbered_konto(epost), org, None, "test")
        self.anna = miljo.webblasare()
        assert self.anna.logga_in(miljo.idp, ANNA)[0] == 302
        self.bo = miljo.webblasare()
        assert self.bo.logga_in(miljo.idp, BO)[0] == 302

    def http(
        self, w: Webblasare | None, metod: str, sokvag: str, kropp: Any = None, csrf: bool = True
    ) -> tuple[int, dict[str, str], bytes]:
        rub: dict[str, str] = {}
        if w is not None:
            rub["Cookie"] = "; ".join(f"{k}={v}" for k, v in w.kakor.items())
            if metod != "GET" and csrf:
                rub.update(URSPRUNG)
                rub.update(w.csrf_headers())
        data = b"" if kropp is None else json.dumps(kropp).encode()
        status, hs, k = self.app.hantera_http(metod, sokvag, rub, lambda: data)
        return status, {n.lower(): v for n, v in hs}, k

    def json(self, w: Webblasare | None, metod: str, sokvag: str, kropp: Any = None, csrf: bool = True) -> tuple[int, dict[str, Any]]:
        s, _, k = self.http(w, metod, sokvag, kropp, csrf)
        return s, json.loads(k)

    def vanta(self, w: Webblasare, jobb: dict[str, Any], max_s: float = 600.0) -> dict[str, Any]:
        t0 = time.monotonic()
        sett: list[str] = []
        while time.monotonic() - t0 < max_s:
            s, d = self.json(w, "GET", jobb["status_url"])
            assert s == 200, d
            if not sett or sett[-1] != d["status"]:
                sett.append(d["status"])
            if d["status"] in ("klar", "fel"):
                d["sett"] = sett
                return d
            time.sleep(0.5)
        raise AssertionError("jobbet blev aldrig klart")


@pytest.fixture
def v(demo: Demo, miljo: Miljo, tmp_path: Path) -> Varld:
    return Varld(demo, miljo, tmp_path)


def test_skyfall_ger_pdf_och_word_via_jobb_med_status(v: Varld) -> None:
    s, d = v.json(v.anna, "POST", f"/api/projekt/{DEMO}/rapporter", {"amne": "skyfall", "bestallning": {"syfte": "Sållning av skyfall för ett nytt hus"}})
    assert s == 202, d
    assert d["kontrakt"] == "K48/v1" and d["status"] in ("ko", "kor") and d["jobb_id"].startswith("j-")
    assert d["korning_id"] == "demo-vatten" and d["steg_totalt"] == 4
    slut = v.vanta(v.anna, d)
    assert slut["status"] == "klar", slut
    assert slut["kontroll"] == {"godkand": True, "antal_fel": 0, "exporterad": True}
    assert slut["rapport_id"].startswith("r-") and set(slut["lankar"]) == {"pdf", "word"}
    # Länkarna ger riktiga filer.
    s1, h1, pdf = v.http(v.anna, "GET", slut["lankar"]["pdf"])
    s2, _, word = v.http(v.anna, "GET", slut["lankar"]["word"])
    assert s1 == 200 and pdf.startswith(b"%PDF") and h1["content-type"] == "application/pdf"
    assert s2 == 200 and word.startswith(b"PK")
    # Rapporten finns i projektets lista och kan förhandsvisas; jobbet är bara synligt i sitt eget projekt.
    s3, lista = v.json(v.anna, "GET", f"/api/projekt/{DEMO}/rapporter")
    assert [r["rapport_id"] for r in lista["rapporter"]] == [slut["rapport_id"]]
    assert lista["rapporter"][0]["kontroll"]["godkand"] is True
    assert v.json(v.anna, "GET", f"/api/projekt/{DEMO}/rapporter/{slut['rapport_id']}")[0] == 200
    # Beställningen (K30) sparades och styrde frågorna.
    best = json.loads(next((v.rot / "rapporter" / "bestallningar").glob("b-*.json")).read_text(encoding="utf-8"))
    assert best["syfte"].startswith("Sållning") and best["projekt"] == DEMO and best["skede"] == "skiss"


def test_dagsljus_ger_rapport_med_beställning_for_mottagare(v: Varld) -> None:
    s, d = v.json(v.anna, "POST", f"/api/projekt/{DEMO}/rapporter", {"amne": "dagsljus", "bestallning": {"mottagare_roll": "byggaktor", "syfte": "Sol och dagsljus för två ändringar"}})
    assert s == 202, d
    assert d["korning_id"] == "demo-dagsljus"
    slut = v.vanta(v.anna, d)
    assert slut["status"] == "klar" and slut["kontroll"]["godkand"] is True, slut
    best = json.loads(next((v.rot / "rapporter" / "bestallningar").glob("b-*.json")).read_text(encoding="utf-8"))
    assert best["mottagare_roll"] == "byggaktor" and best["amnen"] == ["dagsljus", "sol"]


def test_amnen_visar_alla_fyra_amnen_med_mall_och_ekologi_kan_skapas(v: Varld) -> None:
    s, d = v.json(v.anna, "GET", f"/api/projekt/{DEMO}/rapporter")
    assert s == 200
    per = {a["id"]: a for a in d["amnen"]}
    assert [a["id"] for a in d["amnen"]] == ["skyfall", "dagsljus", "ekologi", "luft"]
    assert per["skyfall"]["kan_skapas"] and per["skyfall"]["forval_korning"] == "demo-vatten"
    assert per["dagsljus"]["kan_skapas"] and per["dagsljus"]["forval_korning"] == "demo-dagsljus"
    assert per["ekologi"]["kan_skapas"] and per["ekologi"]["mall"] and per["ekologi"]["forval_korning"] == "demo-ekologi"
    assert per["luft"]["kan_skapas"] and per["luft"]["mall"] and per["luft"]["forval_korning"] == "demo-luft"
    # Ekologi skapas numera i steg 3 utan 409 och exporterar PDF och Word.
    s2, jobb = v.json(v.anna, "POST", f"/api/projekt/{DEMO}/rapporter", {"amne": "ekologi"})
    assert s2 == 202, jobb
    assert jobb["korning_id"] == "demo-ekologi"
    slut = v.vanta(v.anna, jobb)
    assert slut["status"] == "klar", slut
    assert slut["kontroll"] == {"godkand": True, "antal_fel": 0, "exporterad": True}
    assert set(slut["lankar"]) == {"pdf", "word"}


def test_behorighet_csrf_och_indata(v: Varld) -> None:
    url = f"/api/projekt/{DEMO}/rapporter"
    # Utloggad: 401. Utan CSRF: 403, och inget skapas.
    assert v.json(None, "POST", url, {"amne": "skyfall"})[0] == 401
    assert v.json(v.anna, "POST", url, {"amne": "skyfall"}, csrf=False)[0] == 403
    assert not (v.rot / "rapporter" / "jobb").exists()
    # Läsare i projektet (demoprojekt_roll = lasare) får läsa listan men inte skapa.
    v.miljo.adapter.konfig = replace(v.miljo.adapter.konfig, demoprojekt_roll="lasare")
    assert v.json(v.bo, "GET", url)[0] == 200
    assert v.json(v.bo, "POST", url, {"amne": "skyfall"})[0] == 403
    v.miljo.adapter.konfig = replace(v.miljo.adapter.konfig, demoprojekt_roll="utredare")
    # Dålig indata.
    assert v.json(v.anna, "POST", url, {"amne": "buller"})[0] == 400
    assert v.json(v.anna, "POST", url, {"amne": "skyfall", "extra": 1})[0] == 400
    assert v.json(v.anna, "POST", url, {"amne": "skyfall", "bestallning": {"okänt": 1}})[0] == 400
    assert v.json(v.anna, "POST", url, {"amne": "skyfall", "bestallning": {"syfte": "Mejla anna@example.test"}})[0] == 400  # personuppgift i syftet
    s, e = v.json(v.anna, "POST", url, {"amne": "skyfall", "bestallning": {"skede": "planunderlag"}})
    assert s == 409 and "planunderlag" in e["fel"]
    # Körning som inte finns eller inte hör till projektet: 404, utan ledtråd.
    assert v.json(v.anna, "POST", url, {"amne": "skyfall", "korning_id": "finns-inte"})[0] == 404
    assert v.json(v.anna, "POST", url, {"amne": "skyfall", "korning_id": "demo-dagsljus"})[0] == 404  # fel ämne för körningen
    # Ett projekt kontot inte har roll i: 403 (som övriga projekt-API:t).
    assert v.json(v.anna, "POST", "/api/projekt/0000/rapporter", {"amne": "skyfall"})[0] == 403


def test_jobbet_ar_bundet_till_sitt_projekt_och_avbrutna_jobb_blir_fel(v: Varld) -> None:
    s, d = v.json(v.anna, "POST", f"/api/projekt/{DEMO}/rapporter", {"amne": "skyfall"})
    assert s == 202
    v.vanta(v.anna, d)
    # Samma jobb-id under ett annat projekt-id finns inte (och det projektet saknar roll: 403).
    assert v.json(v.anna, "GET", f"/api/projekt/0000/rapporter/jobb/{d['jobb_id']}")[0] == 403
    assert v.app.rapportjobb.status(DEMO, d["jobb_id"])["status"] == "klar"
    # Ett jobb som stod som pågående när servern stoppades (ingen aktiv tråd) blir fel vid nästa fråga.
    fil = v.rot / "rapporter" / "jobb" / "j-aaaaaaaaaaaa.json"
    post = json.loads((v.rot / "rapporter" / "jobb" / f"{d['jobb_id']}.json").read_text(encoding="utf-8"))
    post.update(jobb_id="j-aaaaaaaaaaaa", status="kor", steg="skapar")
    fil.write_text(json.dumps(post), encoding="utf-8")
    ny = FlodenApiApp(testsvit_rot=REPO / "testsvit", konfig_fil=REPO / "config.toml", rapportmapp=v.rot / "rapporter", konto=v.miljo.adapter)
    st = ny.rapportjobb.status(DEMO, "j-aaaaaaaaaaaa")
    assert st["status"] == "fel" and "avbröts" in st["fel"]
    # Id-formen kontrolleras: inget kommer ut ur jobbmappen.
    assert v.json(v.anna, "GET", f"/api/projekt/{DEMO}/rapporter/jobb/..%2F..%2Fx")[0] == 404


def test_webbflodet_laser_scenarierna_ur_korningen_och_gissar_inga_mottagare() -> None:
    """Ett projekt i en geografi har scenarierna `bas` och egna, inte `fore` och `efter`."""
    r = SimpleNamespace(
        projekt="p-i-geografi",
        installningar=SimpleNamespace(sprakprofil="sbk"),
        korningar=[
            {
                "analyser": [{"analys": "skyfall", "losare": "vatten.statisk_fyllning"}],
                "jamforelser": [
                    {"analys": "skyfall", "utdata": "max_djup", "fore": "bas", "efter": "nytt-hus", "namn": "skyfall_max_djup_nytt-hus_minus_bas"}
                ],
            }
        ],
    )
    ps = skyfall_pastaenden(r, demo=False)  # type: ignore[arg-type]
    text = json.dumps(ps, ensure_ascii=False)
    assert "matvarde.skyfall.bas." in text and "matvarde.skyfall.nytt-hus." in text
    assert ".fore." not in text and ".efter." not in text and "skyfall_max_djup_efter_minus_fore" not in text
    assert "jamforelse.skyfall_max_djup_nytt-hus_minus_bas." in text
    sk07 = next(p for p in ps if p["fraga_id"] == "SK-07")
    assert sk07["status"] == "underlag_saknas" and "garden" not in json.dumps(sk07)
    # Utan jämförelse i körningen: tydligt fel, ingen gissning.
    r.korningar[0]["jamforelser"] = []
    with pytest.raises(ValueError, match="jämförelse"):
        skyfall_pastaenden(r, demo=False)  # type: ignore[arg-type]
    assert saknad_mall("skyfall") is None and saknad_mall("dagsljus") is None
    assert saknad_mall("ekologi") is None and saknad_mall("luft") is None
    assert saknad_mall("okant") is not None
    assert callable(pastaenden_for)


def _rapport_json(v: Varld, rapport_id: str) -> dict[str, Any]:
    return json.loads((v.rot / "rapporter" / rapport_id / "rapport.json").read_text(encoding="utf-8"))


def _spara_forslag(v: Varld, slug: str, namn: str, korningar: dict[str, str]) -> None:
    mapp = v.rot / "kartverktyg" / DEMO / "scenarier"
    mapp.mkdir(parents=True, exist_ok=True)
    (mapp / f"{slug}.json").write_text(
        json.dumps({"format": "floden-kartverktyg/v1", "namn": namn, "slug": slug, "andringar": [], "korningar": {k: {"korning_id": i} for k, i in korningar.items()}}),
        encoding="utf-8",
    )


def test_valda_fragor_foljer_med_och_demokorningen_sags_i_rapporten(v: Varld) -> None:
    url = f"/api/projekt/{DEMO}/rapporter"
    s, d = v.json(v.anna, "POST", url, {"amne": "skyfall", "fragor": ["SK-01", "SK-06"]})
    assert s == 202, d
    assert d["underlag"] == {"slag": "demo", "scenario": None, "fragor": ["SK-01", "SK-06"], "demo": True}
    slut = v.vanta(v.anna, d)
    assert slut["status"] == "klar", slut
    rap = _rapport_json(v, slut["rapport_id"])
    assert rap["fragor"] == ["SK-01", "SK-06"]
    assert rap["korningar"][0]["underlag"]["slag"] == "demo"
    best = json.loads(next((v.rot / "rapporter" / "bestallningar").glob("b-*.json")).read_text(encoding="utf-8"))
    assert best["fragor"] == ["SK-01", "SK-06"] and best["urval"] == ["SK-01", "SK-06"]
    # Handlingsrutan säger vad rapporten bygger på.
    from rapport.skissmall import bygg_dokument

    text = json.dumps(bygg_dokument(v.app.rapportjobb.rt.arkiv.las(slut["rapport_id"]), v.app.rapportjobb.rt).block, default=str, ensure_ascii=False)
    assert "demokörningen" in text


def test_fragor_valideras_mot_huvudkatalogen_och_amnet(v: Varld) -> None:
    url = f"/api/projekt/{DEMO}/rapporter"
    assert v.json(v.anna, "POST", url, {"amne": "skyfall", "fragor": ["XX-99"]})[0] == 400
    assert v.json(v.anna, "POST", url, {"amne": "skyfall", "fragor": ["DL-01"]})[0] == 400  # fel ämne
    assert v.json(v.anna, "POST", url, {"amne": "skyfall", "fragor": "SK-01"})[0] == 400
    assert v.json(v.anna, "POST", url, {"amne": "skyfall", "fragor": [1]})[0] == 400
    assert v.json(v.anna, "POST", url, {"amne": "skyfall", "fragor": ["SK-01"], "bestallning": {"fragor": ["SK-02"]}})[0] == 400
    assert not (v.rot / "rapporter" / "jobb").exists()


def test_forslaget_styr_vilken_korning_rapporten_bygger_pa(v: Varld) -> None:
    url = f"/api/projekt/{DEMO}/rapporter"
    # Ett förslag som inte finns: 404. Ett som saknar körning: 409 med vad som ska göras.
    assert v.json(v.anna, "POST", url, {"amne": "skyfall", "scenario": "hus-a"})[0] == 404
    _spara_forslag(v, "hus-a", "Hus A", {})
    s, e = v.json(v.anna, "POST", url, {"amne": "skyfall", "scenario": "hus-a"})
    assert s == 409 and "steg 2" in e["fel"] and "steg 3" not in e["fel"]
    # Förslaget har en körning: den väljs (användarens förslag går före demokörningen) och förvalet i listan ändras.
    _spara_forslag(v, "hus-a", "Hus A", {"vatten": "demo-vatten"})
    per = {a["id"]: a for a in v.json(v.anna, "GET", url)[1]["amnen"]}
    assert per["skyfall"]["forval_slag"] == "forslag"
    assert per["skyfall"]["korningar"][0]["scenario"] == {"slug": "hus-a", "namn": "Hus A"}
    s, d = v.json(v.anna, "POST", url, {"amne": "skyfall", "scenario": "hus-a", "fragor": ["SK-01"]})
    assert s == 202, d
    assert d["korning_id"] == "demo-vatten" and d["underlag"]["slag"] == "forslag" and d["underlag"]["scenario"]["namn"] == "Hus A"
    slut = v.vanta(v.anna, d)
    assert slut["status"] == "klar", slut
    assert _rapport_json(v, slut["rapport_id"])["korningar"][0]["underlag"]["scenario"]["slug"] == "hus-a"
    # En körning som inte hör till förslaget: 400 (dagsljuskörningen duger inte för skyfall: 404).
    _spara_forslag(v, "hus-b", "Hus B", {})
    assert v.json(v.anna, "POST", url, {"amne": "skyfall", "scenario": "hus-b", "korning_id": "demo-vatten"})[0] == 400
    assert v.json(v.anna, "POST", url, {"amne": "skyfall", "scenario": "../x"})[0] == 404


def test_utan_rapportroll_ingen_rapport_pa_valen(v: Varld) -> None:
    v.miljo.adapter.konfig = replace(v.miljo.adapter.konfig, demoprojekt_roll="lasare")
    assert v.json(v.bo, "POST", f"/api/projekt/{DEMO}/rapporter", {"amne": "skyfall", "fragor": ["SK-01"]})[0] == 403
    assert not (v.rot / "rapporter" / "jobb").exists()


def test_belagg_sager_projektets_avgransning_nar_polygon_finns(v: Varld, monkeypatch: pytest.MonkeyPatch) -> None:
    kalla = v.app.rapportjobb.rt.kalla
    adress = "jamforelse.skyfall_max_djup_efter_minus_fore.yta_okning_m2"
    fore = kalla.hamta(adress, "demo-vatten", {})
    assert fore.mottagare == "hela beräkningsområdet" and "hela beräkningsområdet" in fore.namnare
    monkeypatch.setattr(type(kalla), "har_avgransning", lambda self, korning_id: True)
    efter = kalla.hamta(adress, "demo-vatten", {})
    assert efter.mottagare == "inom projektets avgränsning (polygon)"
    assert efter.namnare == "mark inom projektets avgränsning (polygon); byggnadernas tak undantagna"
    assert efter.varde == fore.varde  # texten ändras, inte talet (demon har ingen polygon att räkna på)


def test_lage_idag_kan_skapa_nulagesrapport_med_kartor_utan_409(v: Varld) -> None:
    """B12: en körning av läget idag är valbar i steg 3 utan 409 och skapar nulägesrapport med kartfigur."""
    import time

    from PIL import Image

    from rapport.figurer import FARG_BAKGRUND

    kv = v.app.kartverktyg
    k = kv.kor_lage_idag(DEMO, ["vatten.skyfall"])
    kid = k["fragor"][0]["korning"]["korning_id"]
    slut = time.monotonic() + 600
    while v.app.fragor.korning_status(kid)["status"] not in ("klar", "fel"):
        assert time.monotonic() < slut
        time.sleep(0.3)
    assert v.app.fragor.korning_status(kid)["status"] == "klar"
    assert v.app.korningsunderlag(kid)["slag"] == "lage_idag"
    url = f"/api/projekt/{DEMO}/rapporter"
    per = {a["id"]: a for a in v.json(v.anna, "GET", url)[1]["amnen"]}
    assert kid in {x["korning_id"] for x in per["skyfall"]["korningar"]}
    assert per["skyfall"]["forval_slag"] == "lage_idag"
    s, d = v.json(v.anna, "POST", url, {"amne": "skyfall", "korning_id": kid})
    assert s == 202, d
    assert d["underlag"]["slag"] == "lage_idag"
    klar = v.vanta(v.anna, d)
    assert klar["status"] == "klar", klar
    assert klar["kontroll"] == {"godkand": True, "antal_fel": 0, "exporterad": True}
    rap = _rapport_json(v, klar["rapport_id"])
    assert len(rap["kartor"]) >= 1
    assert rap["kartor"][0]["meta"]["kartmotor"] == "rapport.figurer"
    kartfil = v.rot / "rapporter" / klar["rapport_id"] / rap["kartor"][0]["fil"]
    with Image.open(kartfil) as img:
        assert img.getpixel((2, 2)) == FARG_BAKGRUND
