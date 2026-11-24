# ruff: noqa: E501, F811
"""Nytt projekt i webben: `GET /api/geografier` och `POST /api/projekt` (K48).

SYNTETISKT: geografin är den påhittade `goteborg-test` ur `tests/kommuner/test_skapa.py` (falsk Lantmäteriserver och
falskt WFS, inget nät). Testet visar behörighet, avgränsning inom/delvis utanför/utanför, CSRF och att projektet
registreras med skaparen som utredare; inte att Göteborgs verkliga data ser ut så.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from geografi.modell import las_manifest
from konto.register import Register
from tests.kommuner.test_skapa import geo  # noqa: F401  (fixturen: mappad geografi)
from tests.konto.conftest import ANNA, Miljo, Webblasare
from webb.api import FlodenApiApp

REPO = Path(__file__).resolve().parents[2]
BO = {"sub": "sub-bo-x2", "email": "bo@example.test", "email_verified": True, "name": "Bo Exempel"}
CY = {"sub": "sub-cy-x3", "email": "cy@example.test", "email_verified": True, "name": "Cy Exempel"}
DI = {"sub": "sub-di-x4", "email": "di@example.test", "email_verified": True, "name": "Di Exempel"}
URSPRUNG = {"Origin": "https://floden.test"}


class Varld:
    def __init__(self, geo: dict[str, Any], miljo: Miljo) -> None:
        rot: Path = geo["rot"]
        self.rot = rot
        self.miljo = miljo
        self.reg: Register = miljo.register
        self.app = FlodenApiApp(
            testsvit_rot=REPO / "testsvit",
            konfig_fil=rot / "config.toml",
            fragemappar=[rot / "fragor"],
            cachemapp=rot / "cache",
            korningsmapp=rot / "korningar",
            konto=miljo.adapter,
            geomapp=rot / "geo",
            projektmapp=rot / "projekt",
            rapportmapp=rot / "rapporter",
        )
        self.app.projekt_skapa.kommun_for = lambda _g: geo["kommun"]
        d, _ = las_manifest(rot / "geo", "goteborg-test@v1")
        self.karna: list[float] = list(d["karna_3011"])
        self.gransen: list[float] = list(d["gransen_3011"])
        self.org = self.reg.skapa_organisation("Exempelkommun")
        self.annat_org = self.reg.skapa_organisation("Grannkommun")
        # Anna: organisationsadmin, Bo: enkel medlem, Cy: superadmin (ingen organisation), Di: ägare i andra organisationen.
        anna = self.reg.forbered_konto("anna@example.test")
        self.reg.gor_organisationsadmin(anna, self.org)
        bo = self.reg.forbered_konto("bo@example.test")
        self.reg.lagg_till_medlem(bo, self.org, None, "test")
        cy = self.reg.forbered_konto("cy@example.test")
        self.reg.ge_superadmin(cy)
        di = self.reg.forbered_konto("di@example.test")
        self.reg.gor_organisationsadmin(di, self.annat_org)
        self.konton = {"anna": anna, "bo": bo, "cy": cy, "di": di}
        self.webb: dict[str, Webblasare] = {}
        for namn, claims in (("anna", ANNA), ("bo", BO), ("cy", CY), ("di", DI)):
            w = miljo.webblasare()
            assert w.logga_in(miljo.idp, claims)[0] == 302
            self.webb[namn] = w

    def http(
        self, vem: str | None, metod: str, sokvag: str, kropp: Any = None, csrf: bool = True
    ) -> tuple[int, dict[str, Any]]:
        w = self.webb[vem] if vem else None
        rub: dict[str, str] = {}
        if w is not None:
            rub["Cookie"] = "; ".join(f"{k}={v}" for k, v in w.kakor.items())
            if metod != "GET" and csrf:
                rub.update(URSPRUNG)
                rub.update(w.csrf_headers())
        data = b"" if kropp is None else json.dumps(kropp).encode()
        status, _, k = self.app.hantera_http(metod, sokvag, rub, lambda: data)
        return status, json.loads(k) if k else {}

    def inre(self, marginal: float = 10.0, storlek: float | None = None) -> list[float]:
        k = self.karna
        return [k[0] + marginal, k[1] + marginal, k[0] + marginal + (storlek or 60), k[1] + marginal + (storlek or 60)]


@pytest.fixture
def v(geo: dict[str, Any], miljo: Miljo) -> Varld:
    return Varld(geo, miljo)


def skapa(v: Varld, vem: str, namn: str, avgr: Any, **extra: Any) -> tuple[int, dict[str, Any]]:
    return v.http(vem, "POST", "/api/projekt", {"geografi": "goteborg-test@v1", "namn": namn, "avgransning": avgr, **extra})


def test_geografierna_visar_karna_och_vem_som_far_skapa(v: Varld) -> None:
    status, d = v.http("anna", "GET", "/api/geografier")
    assert status == 200 and d["far_skapa_projekt"] is True
    g = next(x for x in d["geografier"] if x["ref"] == "goteborg-test@v1")
    assert g["karna_3011"] == v.karna and g["gransen_3011"] == v.gransen and g["epsg"] == 3011
    assert g["karna_polygon_3011"]["type"] == "Polygon" and g["far_skapa_projekt"] is True
    assert [o["id"] for o in d["organisationer"]] == [v.org]
    assert {x["ref"] for x in d["geografier"]} >= {"goteborg-test@v1", "goteborg-test@v2"}
    # En vanlig medlem ser geografierna men får inte skapa; superadmin får i alla organisationer.
    sb, db = v.http("bo", "GET", "/api/geografier")
    assert sb == 200 and db["far_skapa_projekt"] is False and not db["organisationer"]
    sc, dc = v.http("cy", "GET", "/api/geografier")
    assert dc["far_skapa_projekt"] is True and {o["id"] for o in dc["organisationer"]} == {v.org, v.annat_org}
    # Utloggad: 401.
    assert v.http(None, "GET", "/api/geografier")[0] == 401


def test_organisationsadmin_skapar_projekt_och_blir_utredare(v: Varld) -> None:
    status, d = skapa(v, "anna", "Haga prov", v.inre())
    assert status == 201, d
    assert d["kontrakt"] == "K48/v1" and d["roll"] == "utredare" and d["organisation_synlig"] is False
    assert d["geografi"] == "goteborg-test@v1" and d["avgransning_3011"] == v.inre()
    anna = v.konton["anna"]
    assert v.reg.projektroll(anna, d["projekt_id"]) == "utredare"
    assert v.reg.effektiv_projektroll(anna, d["projekt_id"]).roll == "utredare"
    lista = v.reg.projekt_med_effektiv_roll(anna)
    assert [(p["id"], p["organisation_synlig"]) for p in lista] == [(d["projekt_id"], False)]
    # Projektmappen byggdes av samma kod som `floden projekt skapa` och bär registrets id.
    pj = json.loads((v.rot / "projekt" / "haga-prov" / "projekt.json").read_text(encoding="utf-8"))
    assert pj["register_id"] == d["projekt_id"] and pj["skapad_av_konto"] == anna
    # Projektet syns i Annas lista, inte i Bos.
    assert [p["id"] for p in v.http("anna", "GET", "/api/projekt")[1]["projekt"]] == [d["projekt_id"]]
    assert v.http("bo", "GET", "/api/projekt")[1]["projekt"] == []


def test_synligt_for_organisationen_foljer_effektiv_projektroll(v: Varld) -> None:
    st, d = skapa(v, "anna", "Delat", v.inre(), organisation_synlig=True)
    assert st == 201 and d["organisation_synlig"] is True
    # En andra administratör i organisationen får utredare via organisationsrollen; en medlem och en annan organisation inte.
    admin2 = v.reg.forbered_konto("admin2@example.test")
    v.reg.gor_organisationsadmin(admin2, v.org)
    assert v.reg.effektiv_projektroll(admin2, d["projekt_id"]).roll == "utredare"
    assert v.reg.effektiv_projektroll(v.konton["bo"], d["projekt_id"]).roll is None
    assert v.reg.effektiv_projektroll(v.konton["di"], d["projekt_id"]).roll is None
    # Privat (förval): den andra administratören får ingenting.
    st2, p = skapa(v, "anna", "Privat", v.inre())
    assert st2 == 201 and v.reg.effektiv_projektroll(admin2, p["projekt_id"]).roll is None


def test_vanlig_medlem_utan_organisation_och_utloggad_nekas(v: Varld) -> None:
    for vem in ("bo",):
        status, d = skapa(v, vem, "Nej", v.inre())
        assert status == 403 and "superadmin" in d["fel"]
    assert skapa(v, None, "Nej", v.inre())[0] == 401
    assert not (v.rot / "projekt" / "nej").exists()  # inget byggdes
    # Organisation man inte administrerar nekas, och ger samma svar som obehörighet.
    status, _ = skapa(v, "anna", "Nej2", v.inre(), organisation=v.annat_org)
    assert status == 403


def test_superadmin_ar_tvingad_att_valja_organisation_nar_flera_finns(v: Varld) -> None:
    status, d = skapa(v, "cy", "Super", v.inre())
    assert status == 400 and "Ange organisation" in d["fel"]
    status, d = skapa(v, "cy", "Super", v.inre(), organisation=v.annat_org)
    assert status == 201
    cy = v.konton["cy"]
    assert v.reg.projektroll(cy, d["projekt_id"]) == "utredare"
    # Superadmin är skapare och utredare men ger ingen rätt till andras projekt (Annas privata).
    _, a = skapa(v, "anna", "Annas", v.inre())
    assert v.reg.effektiv_projektroll(cy, a["projekt_id"]).roll is None


def test_avgransning_inom_delvis_utanfor_och_utanfor(v: Varld) -> None:
    k, g = v.karna, v.gransen
    # Inom kärnan: inga varningar om kärnan.
    st, d = skapa(v, "anna", "Inne", v.inre())
    assert st == 201 and not [w for w in d["varningar"] if "giltiga kärna" in w]
    # Delvis utanför kärnan men inom yttre gräns: varning, inget fel.
    st, d = skapa(v, "anna", "Kant", [k[0] - 20, k[1] - 20, k[0] + 40, k[1] + 40])
    assert st == 201 and any("giltiga kärna" in w for w in d["varningar"])
    # Utanför geografins yttre gräns: 400, inget projekt och ingen registerpost kvar.
    antal = len(v.reg.projekt_med_effektiv_roll(v.konton["anna"]))
    st, d = skapa(v, "anna", "Ute", [g[2] + 10, g[3] + 10, g[2] + 50, g[3] + 50])
    assert st == 400 and "utanför geografin" in d["fel"]
    assert len(v.reg.projekt_med_effektiv_roll(v.konton["anna"])) == antal
    assert not (v.rot / "projekt" / "ute").exists()
    # GeoJSON-polygon sparas som polygon; rektangeln runt den är beräkningsrutan (se test_polygon_avgransning.py).
    i = v.inre(marginal=20)
    poly = {"type": "Polygon", "coordinates": [[[i[0], i[1]], [i[2], i[1]], [i[2], i[3]], [i[0] + 30, i[3]], [i[0], i[1]]]]}
    st, d = skapa(v, "anna", "Poly", poly)
    assert st == 201 and d["avgransning_3011"] == i and d["avgransning_slag"] == "polygon" and "polygon" in d["info"][0]


def test_ogiltig_indata_ger_400_eller_404(v: Varld) -> None:
    ok = v.inre()
    assert v.http("anna", "POST", "/api/projekt", {"geografi": "finns-inte@v1", "namn": "X", "avgransning": ok})[0] == 404
    assert v.http("anna", "POST", "/api/projekt", {"geografi": "dåligt", "namn": "X", "avgransning": ok})[0] == 400
    assert skapa(v, "anna", "", ok)[0] == 400
    assert skapa(v, "anna", "anna@example.test", ok)[0] == 400  # personuppgift i namnet
    assert skapa(v, "anna", "X", [0, 0, 5, 5])[0] == 400  # för liten
    assert skapa(v, "anna", "X", [0, 0, 1, "a"])[0] == 400
    assert skapa(v, "anna", "X", {"type": "Polygon", "coordinates": []})[0] == 400
    assert skapa(v, "anna", "X", ok, organisation_synlig="ja")[0] == 400
    assert v.http("anna", "POST", "/api/projekt", {"geografi": "goteborg-test@v1", "namn": "X", "avgransning": ok, "extra": 1})[0] == 400


def test_csrf_kravs_for_att_skapa(v: Varld) -> None:
    body = {"geografi": "goteborg-test@v1", "namn": "Utan csrf", "avgransning": v.inre()}
    assert v.http("anna", "POST", "/api/projekt", body, csrf=False)[0] == 403
    assert not (v.rot / "projekt" / "utan-csrf").exists()
    w = v.webb["anna"]
    rub = {"Cookie": "; ".join(f"{k}={x}" for k, x in w.kakor.items()), "Origin": "https://annan.test", **w.csrf_headers()}
    assert v.app.hantera_http("POST", "/api/projekt", rub, lambda: json.dumps(body).encode())[0] == 403


def test_namn_som_finns_ger_409_och_ger_aldrig_roll_i_ett_annat_projekt(v: Varld) -> None:
    st, d = skapa(v, "anna", "Dubblett", v.inre())
    assert st == 201
    assert skapa(v, "anna", "Dubblett", v.inre())[0] == 409
    # En annan organisation med samma namn ger samma projektmapp: nekas, och Di får ingen roll i Annas projekt.
    st2, _ = skapa(v, "di", "Dubblett", v.inre())
    assert st2 == 409
    assert v.reg.effektiv_projektroll(v.konton["di"], d["projekt_id"]).roll is None
    pj = json.loads((v.rot / "projekt" / "dubblett" / "projekt.json").read_text(encoding="utf-8"))
    assert pj["register_id"] == d["projekt_id"]


def test_inloggning_av_nekar_att_skapa(geo: dict[str, Any], tmp_path: Path) -> None:
    app = FlodenApiApp(testsvit_rot=REPO / "testsvit", konfig_fil=geo["rot"] / "config.toml", geomapp=geo["rot"] / "geo", projektmapp=geo["rot"] / "projekt")
    status, _, k = app.hantera_http("POST", "/api/projekt", {}, lambda: b"{}")
    assert status == 403 and "inloggning" in json.loads(k)["fel"]


def test_inga_namn_eller_adresser_i_loggen(v: Varld, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    st, _ = skapa(v, "anna", "Hemligt namn", v.inre())
    assert st == 201
    # Bara Flodens egna loggare och allt från INFO och uppåt: rasterios DEBUG-rader nämner filsökvägar (slug), det gör
    # inte produktionsloggen (INFO).
    rader = [r for r in caplog.records if r.levelno >= 20 or r.name.split(".")[0] in {"konto", "webb", "kommuner"}]
    text = "\n".join(f"{r.name}: {r.getMessage()}" for r in rader)
    assert "projekt skapat konto=" in text
    for hemligt in ("Hemligt namn", "hemligt-namn", "anna@example.test", "Anna Exempel"):
        assert hemligt not in text, hemligt


def test_projekt_i_geografin_kan_fa_en_rapport_i_webben(v: Varld) -> None:
    """Hela kedjan för ett projekt som inte är demokvarteret: skapa i webben, ritat hus (K9), körning av skyfall och
    dagsljus, och därefter rapport via jobbet. Scenarierna heter `bas` och husets namn, inte `fore` och `efter`."""
    import time

    from kommuner.projekt import kor_projekt, skapa_scenario_hus

    st, d = skapa(v, "anna", "Haga rapport", v.inre(marginal=30, storlek=80))
    assert st == 201, d
    pid = d["projekt_id"]
    mapp = v.rot / "projekt" / "haga-rapport"
    cx = (d["avgransning_3011"][0] + d["avgransning_3011"][2]) / 2
    cy = (d["avgransning_3011"][1] + d["avgransning_3011"][3]) / 2
    skapa_scenario_hus(
        mapp, kommun=v.app.projekt_skapa.kommun_for("goteborg-test"), geo_rot=v.rot / "geo", namn="Nytt hus",
        centrum=(cx, cy + 25), bredd_m=12, djup_m=10, hojd_m=20, skriv=lambda _: None,
    )  # fmt: skip
    kor = kor_projekt(mapp, rot=v.rot, skriv=lambda _: None, tjanst=v.app.fragor)
    assert {k: x["status"] for k, x in kor.items()} == {"skyfall": "klar", "dagsljus": "klar"}
    # Körningarna bär registrets id, så projektet ser dem och inget annat projekt gör det.
    status, lista = v.http("anna", "GET", f"/api/projekt/{pid}/rapporter")
    assert status == 200
    per = {a["id"]: a for a in lista["amnen"]}
    assert per["skyfall"]["kan_skapas"] and per["skyfall"]["forval_korning"].startswith("haga-rapport-skyfall")
    assert per["dagsljus"]["kan_skapas"] and not per["ekologi"]["kan_skapas"]
    for amne in ("skyfall", "dagsljus"):
        s, jobb = v.http("anna", "POST", f"/api/projekt/{pid}/rapporter", {"amne": amne})
        assert s == 202, jobb
        t0 = time.monotonic()
        while time.monotonic() - t0 < 600:
            _, jobb = v.http("anna", "GET", jobb["status_url"])
            if jobb["status"] in ("klar", "fel"):
                break
            time.sleep(0.5)
        assert jobb["status"] == "klar", jobb
        # Lantmäteriets härledda data är stängda tills en människa kontrollerat villkoren (K38): rapporten skapas
        # och kontrolleras, men kontrollen spärrar exporten och säger varför. Ingen länk till PDF eller Word.
        assert jobb["rapport_id"], jobb
        assert jobb["kontroll"]["godkand"] is False and jobb["kontroll"]["exporterad"] is False, jobb["kontroll"]
        assert any("dataklassen" in f for f in jobb["kontroll"]["fel"]), jobb["kontroll"]
        assert jobb["lankar"] is None
    # En annan organisations administratör ser varken projektet, körningarna eller rapporterna.
    assert v.http("di", "GET", f"/api/projekt/{pid}/rapporter")[0] == 403
    assert v.http("di", "POST", f"/api/projekt/{pid}/rapporter", {"amne": "skyfall"})[0] == 403
