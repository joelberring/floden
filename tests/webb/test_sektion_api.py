"""API- och behörighetstester för `POST /api/kartverktyg/<projekt>/sektion`."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from demovarld.demo import LITEN
from demovarld.varld import nytt_hus_polygon
from tests.konto.conftest import ANNA, Miljo, Webblasare
from tests.webb.conftest import Demo
from webb.api import FlodenApiApp, _projekt_krav
from webb.sektion import EPSG, HOJDSYSTEM, KONTRAKT

REPO = Path(__file__).resolve().parents[2]
P = "/api/kartverktyg/demokvarteret"
BO = {
    "sub": "sub-bo-sektion",
    "email": "bo@example.test",
    "email_verified": True,
    "name": "Bo Bygg",
}


def _http(
    app: FlodenApiApp,
    metod: str,
    sokvag: str,
    webb: Webblasare | None = None,
    extra: dict[str, str] | None = None,
    kropp: bytes = b"",
) -> tuple[int, dict[str, str], bytes]:
    rubriker = dict(extra or {})
    if webb is not None and webb.kakor:
        rubriker["Cookie"] = "; ".join(f"{k}={v}" for k, v in webb.kakor.items())
    status, hs, k = app.hantera_http(metod, sokvag, rubriker, lambda: kropp)
    return status, {n.lower(): v for n, v in hs}, k


def _skriv_korning(app: FlodenApiApp, korning_id: str, projekt_id: str | None = None) -> None:
    mapp = app.fragor.korningsmapp / korning_id
    mapp.mkdir(parents=True)
    post: dict[str, Any] = {
        "korning_id": korning_id,
        "ref": "demo",
        "fraga_id": "demo",
        "fraga_fil": "demo.toml",
        "status": "klar",
        "skapad": "2026-11-23T10:00:00+00:00",
        "jobb_totalt": 0,
    }
    if projekt_id is not None:
        post["projekt_id"] = projekt_id
    (mapp / "korning.json").write_text(json.dumps(post), encoding="utf-8")


class _Kv:
    def __init__(self, demo: Demo, rot: Path) -> None:
        self.demo = demo
        self.rot = rot
        self.app = FlodenApiApp(
            testsvit_rot=REPO / "testsvit",
            konfig_fil=REPO / "config.toml",
            fragemappar=[demo.fragor],
            cachemapp=demo.rot / "cache",
            korningsmapp=demo.rot / "korningar",
            kartverktygsmapp=rot / "kartverktyg",
            datamappar=[rot],
        )
        self.app.fragor.demomapp = demo.varld

    def json(self, verb: str, sokvag: str, kropp: Any = None) -> tuple[int, dict[str, Any]]:
        data = b"" if kropp is None else json.dumps(kropp).encode("utf-8")
        status, _, ut = self.app.hantera_forfragan(verb, sokvag, data)
        return status, json.loads(ut)


@pytest.fixture(scope="module")
def kv(demo: Demo, tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Kv]:
    yield _Kv(demo, tmp_path_factory.mktemp("sektion-api"))


def _linje_over_nytt_hus() -> list[list[float]]:
    """Linje genom sydvästra gården och mitt genom nya huset i demovärlden."""
    poly = nytt_hus_polygon(LITEN.varld())
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    mid_y = 0.5 * (min(ys) + max(ys))
    return [[min(xs) - 25.0, mid_y], [max(xs) + 25.0, mid_y]]


# ------------------------------------------------------------------ behörighet och autentisering


def test_projekt_krav_sektion_kraver_bara_lasare() -> None:
    assert _projekt_krav("POST", "/api/kartverktyg/demokvarteret/sektion") == (
        "demokvarteret",
        "lasare",
    )
    assert _projekt_krav("POST", "/api/kartverktyg/proj-1/sektion") == ("proj-1", "lasare")
    # Skrivande endpoints kräver fortsatt utredare
    assert _projekt_krav("POST", "/api/kartverktyg/demokvarteret/validera") == (
        "demokvarteret",
        "utredare",
    )


def test_sektion_behorighet_och_korsprojekt_korning_id(
    demo: Demo, tmp_path: Path, miljo: Miljo
) -> None:
    r = miljo.register
    org = r.skapa_organisation("Teststad")
    admin = r.forbered_konto("admin@example.test")
    r.gor_organisationsadmin(admin, org)
    anna_projekt = r.skapa_projekt(org, "Annas projekt")
    bo_projekt = r.skapa_projekt(org, "Bos projekt")
    r.bjud_in(admin, anna_projekt, str(ANNA["email"]))
    r.bjud_in(admin, bo_projekt, str(BO["email"]), "utredare")

    # Sätt demoprojekt_roll = "lasare" (inte utredare) för att bevisa att lasare räcker!
    miljo.adapter.konfig = replace(miljo.adapter.konfig, demoprojekt_roll="lasare")
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[demo.fragor],
        cachemapp=demo.rot / "cache",
        korningsmapp=tmp_path / "korningar",
        kartverktygsmapp=tmp_path / "kartverktyg",
        datamappar=[tmp_path],
        konto=miljo.adapter,
    )
    app.fragor.demomapp = demo.varld
    _skriv_korning(app, "k-bo", bo_projekt)
    _skriv_korning(app, "k-anna", anna_projekt)

    anna = miljo.webblasare()
    bo = miljo.webblasare()
    assert anna.logga_in(miljo.idp, ANNA)[0] == 302
    assert bo.logga_in(miljo.idp, BO)[0] == 302

    linje = _linje_over_nytt_hus()
    kropp = json.dumps({"punkter": linje}).encode("utf-8")

    # Oinloggad -> 401
    st_anon, _, _ = _http(app, "POST", f"{P}/sektion", None, None, kropp)
    assert st_anon == 401

    # Anna (lasare på demokvarteret) får 200 på sektion men 403 på utredare-endpoint!
    st_ok, _, svar_ok = _http(app, "POST", f"{P}/sektion", anna, anna.csrf_headers(), kropp)
    assert st_ok == 200, svar_ok
    data = json.loads(svar_ok)
    assert data["kontrakt"] == KONTRAKT

    st_val, _, _ = _http(
        app,
        "POST",
        f"{P}/validera",
        anna,
        anna.csrf_headers(),
        json.dumps({"andringar": []}).encode("utf-8"),
    )
    assert st_val == 403

    # Korsprojekt-korning_id (både Bos och Annas eget andra projekt) -> 404 på demokvarteret
    for frammande_kid in ("k-bo", "k-anna", "finns-inte"):
        k_kropp = json.dumps({"punkter": linje, "korning_id": frammande_kid}).encode("utf-8")
        st_kors, _, svar_kors = _http(
            app, "POST", f"{P}/sektion", anna, anna.csrf_headers(), k_kropp
        )
        assert st_kors == 404, (frammande_kid, svar_kors)

    # Obehörig användare på demokvarteret när demoprojekt_roll="av" -> 403/404
    miljo.adapter.konfig = replace(miljo.adapter.konfig, demoprojekt_roll="av")
    st_av, _, _ = _http(app, "POST", f"{P}/sektion", anna, anna.csrf_headers(), kropp)
    assert st_av in (403, 404)

    # Bo saknar roll i Annas projekt -> 403/404
    st_bo, _, _ = _http(
        app, "POST", f"/api/kartverktyg/{anna_projekt}/sektion", bo, bo.csrf_headers(), kropp
    )
    assert st_bo in (403, 404)


def test_korsprojekt_korning_ger_404_aven_utan_inloggning(kv: _Kv) -> None:
    _skriv_korning(kv.app, "k-annat-projekt", "annat-projekt")
    status, svar = kv.json(
        "POST",
        f"{P}/sektion",
        {"punkter": _linje_over_nytt_hus(), "korning_id": "k-annat-projekt"},
    )
    assert status == 404, svar


# ------------------------------------------------------------------ 400 / 404 validering


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"punkter": None},
        {"punkter": "inte-lista"},
        {"punkter": [[151_000, 6_580_000]]},
        {"punkter": [[151_000 + i, 6_580_000] for i in range(101)]},
        {"punkter": [[151_000, 6_580_000], [151_000, 6_580_000]]},
        {"punkter": [[151_000, 6_580_000], [210_000, 6_580_000]]},
        {"punkter": [[True, 6_580_000], [151_010, 6_580_000]]},
        {"punkter": [[151_000, 6_580_000], [151_020, 6_580_000]], "steg_m": 0},
        {"punkter": [[151_000, 6_580_000], [151_020, 6_580_000]], "steg_m": -2.0},
        {"punkter": [[151_000, 6_580_000], [151_020, 6_580_000]], "steg_m": True},
        {"punkter": [[151_000, 6_580_000], [151_020, 6_580_000]], "steg_m": "1.0"},
        {"punkter": [[151_000, 6_580_000], [151_020, 6_580_000]], "korning_id": 123},
        {
            "punkter": [[151_000, 6_580_000], [151_020, 6_580_000]],
            "fore_lager": "skyfall.bas.max_djup",
        },
    ],
)
def test_ogiltiga_sektion_anrop_ger_400(kv: _Kv, payload: dict[str, Any]) -> None:
    status, svar = kv.json("POST", f"{P}/sektion", payload)
    assert status == 400, (payload, svar)


def test_okant_projekt_och_okant_lager_ger_404(kv: _Kv) -> None:
    linje = _linje_over_nytt_hus()
    assert kv.json("POST", "/api/kartverktyg/finns-inte/sektion", {"punkter": linje})[0] == 404

    demo_vatten_id = kv.app.fragor.demo()["delar"]["demo_vatten"]["korning_id"]
    st_lager, svar_lager = kv.json(
        "POST",
        f"{P}/sektion",
        {"punkter": linje, "korning_id": demo_vatten_id, "fore_lager": "finns.inte.max_djup"},
    )
    assert st_lager == 404, svar_lager


# ------------------------------------------------------------------ utan körning


def test_sektion_utan_korning_ger_terrang_och_byggnader(kv: _Kv) -> None:
    info = kv.json("GET", P)[1]
    bygg = info["byggnader"][0]
    xs = [p[0] for p in bygg["ring"]]
    ys = [p[1] for p in bygg["ring"]]
    cy = 0.5 * (min(ys) + max(ys))
    linje = [[min(xs) - 10.0, cy], [max(xs) + 10.0, cy]]

    status, svar = kv.json("POST", f"{P}/sektion", {"punkter": linje, "steg_m": 1.0})
    assert status == 200, svar
    assert svar["kontrakt"] == KONTRAKT
    assert svar["epsg"] == EPSG
    assert svar["hojdsystem"] == HOJDSYSTEM
    assert svar["har_efter"] is False

    n = len(svar["avstand_m"])
    assert n >= 10
    assert svar["avstand_m"][0] == 0.0
    assert all(v is not None for v in svar["mark_fore_m"])
    assert all(v is None for v in svar["mark_efter_m"])
    assert all(v is None for v in svar["vatten_djup_fore_m"])
    assert all(v is None for v in svar["vatten_yta_fore_m"])
    assert any(svar["byggnad_fore"])
    assert any(t is not None for t in svar["takhojd_fore_m"])

    # Kontrollera exakt överensstämmelse med pekavläsningen /hojd på flera punkter
    for idx in (0, n // 2, n - 1):
        e_i, n_i = svar["e"][idx], svar["n"][idx]
        st_h, hojd = kv.json("GET", f"{P}/hojd?e={e_i}&n={n_i}")
        assert st_h == 200 and hojd["inom"]
        assert svar["mark_fore_m"][idx] == pytest.approx(hojd["hojd_m"], abs=1e-3)


# ------------------------------------------------------------------ med riktig körning


def test_sektion_med_riktig_korning_vatten_och_takmask(kv: _Kv) -> None:
    import numpy as np

    demo_vatten_id = kv.app.fragor.demo()["delar"]["demo_vatten"]["korning_id"]
    res = kv.app.fragor.resultat(demo_vatten_id)
    fore_lager = next(
        la["id"]
        for la in res["lager"]
        if la["roll"] == "max_djup" and la["typ"] == "bas"
    )
    efter_lager = next(
        la["id"]
        for la in res["lager"]
        if la["roll"] == "max_djup" and la["typ"] == "scenario"
    )

    # Hitta djupaste omaskade vattencellen i före-lagret och dra en polylinje genom den
    # och tvärs över det nya huset i efter-scenariot.
    lr = kv.app.fragor.lager_raster(demo_vatten_id, fore_lager)
    arr = np.asarray(lr["raster"], dtype=np.float64)
    tak = lr["tak"]
    giltig = np.isfinite(arr) & (~np.asarray(tak, dtype=bool) if tak is not None else True)
    rad, kol = np.unravel_index(int(np.argmax(np.where(giltig, arr, -1.0))), arr.shape)
    u = lr["lager"]["utbredning"]
    px = float(lr["lager"]["pixelstorlek_m"])
    vat_e = float(u["x_min"]) + (float(kol) + 0.5) * px
    vat_n = float(u["y_max"]) - (float(rad) + 0.5) * px

    poly = nytt_hus_polygon(LITEN.varld())
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    hus_e = 0.5 * (min(xs) + max(xs))
    hus_n = 0.5 * (min(ys) + max(ys))
    linje = [[vat_e, vat_n], [hus_e, hus_n], [max(xs) + 10.0, hus_n]]

    status, svar = kv.json(
        "POST",
        f"{P}/sektion",
        {
            "punkter": linje,
            "korning_id": demo_vatten_id,
            "fore_lager": fore_lager,
            "efter_lager": efter_lager,
            "steg_m": 1.0,
        },
    )
    assert status == 200, svar
    assert svar["kontrakt"] == KONTRAKT
    assert svar["har_efter"] is True

    # Mark före & efter finns
    assert any(v is not None for v in svar["mark_fore_m"])
    assert any(v is not None for v in svar["mark_efter_m"])

    # Startpunkten (djupaste vattencellen) ska ha positivt vattendjup och matcha avläsningen
    assert svar["vatten_djup_fore_m"][0] is not None and svar["vatten_djup_fore_m"][0] > 0.0
    avl = kv.app.fragor.avlasning(demo_vatten_id, fore_lager, vat_e, vat_n)
    avl_fore = next(v for v in avl["varden"] if v["lager_id"] == fore_lager)
    assert svar["vatten_djup_fore_m"][0] == pytest.approx(avl_fore["varde"], abs=1e-3)

    # Vattendjup och vattenyta är fysiskt konsistenta (vatten_yta == mark + djup)
    for m, d, yta in zip(
        svar["mark_fore_m"],
        svar["vatten_djup_fore_m"],
        svar["vatten_yta_fore_m"],
        strict=True,
    ):
        if d is not None and d > 0.0 and m is not None:
            assert yta == pytest.approx(m + d, abs=2e-4)

    # Nya huset i efter-läget: byggnad_efter=True där byggnad_fore=False,
    # och på takceller ska vatten_djup_efter_m och vatten_yta_efter_m vara maskade (None).
    nya_hus_idx = [
        i
        for i, (bf, be) in enumerate(
            zip(svar["byggnad_fore"], svar["byggnad_efter"], strict=True)
        )
        if (not bf) and be
    ]
    assert len(nya_hus_idx) >= 3, "Linjen borde skära det nya huset i efter-scenariot"
    for i in nya_hus_idx:
        assert svar["takhojd_efter_m"][i] is not None
        assert svar["mark_efter_m"][i] is not None
        assert svar["takhojd_efter_m"][i] > svar["mark_efter_m"][i]
    # Inre punkter av nya huset ska vara takmaskade i vattenlagret
    inre = nya_hus_idx[len(nya_hus_idx) // 2]
    assert svar["vatten_djup_efter_m"][inre] is None
    assert svar["vatten_yta_efter_m"][inre] is None

    samm = svar["sammanfattning"]
    assert samm["max_djup_fore_m"] is not None and samm["max_djup_fore_m"] > 0.0
    assert samm["max_djup_efter_m"] is not None and samm["max_djup_efter_m"] > 0.0
    assert samm["oversvammad_bredd_002m_fore_m"] is not None
    assert samm["oversvammad_bredd_002m_efter_m"] is not None
