# ruff: noqa: E501
"""Projektinställningar: vem får ändra namn, medlemmar och synlighet, arkivera och radera (register och HTTP)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

import konto.databas as db_modul
from konto.databas import Databas
from konto.register import BehorighetFel, Register, RegisterFel
from tests.konto.conftest import Miljo
from tests.konto.test_admin_webb import anrop, bygg_varld


@dataclass
class V:
    r: Register
    org: str
    annan_org: str
    p: str
    id: dict[str, str]


def _varld(miljo: Miljo) -> V:
    r = miljo.register
    org, annan = r.skapa_organisation("Kommun"), r.skapa_organisation("Granne")
    ids = {
        n: r.forbered_konto(f"{n}@example.test")
        for n in ("agare", "utredare", "lasare", "orgadmin", "super", "fremling", "granne")
    }
    r.ge_superadmin(ids["super"])
    r.gor_organisationsadmin(ids["orgadmin"], org)
    r.gor_organisationsadmin(ids["granne"], annan)
    p = r.skapa_projekt(org, "Projektet")
    with r.db.transaktion() as c:
        for n, roll in (("agare", "agare"), ("utredare", "utredare"), ("lasare", "lasare")):
            c.execute(
                "INSERT INTO projektbehorighet (konto_id, projekt_id, roll, kalla, skapad)"
                " VALUES (?, ?, ?, 'manuell', 0)",
                (ids[n], p, roll),
            )
    return V(r, org, annan, p, ids)


def test_lasare_och_utredare_ser_men_andrar_inget(miljo: Miljo) -> None:
    v = _varld(miljo)
    for vem in ("lasare", "utredare"):
        u = v.id[vem]
        d = v.r.projektinstallningar(u, v.p)
        assert d["far_andra"] is False and d["far_arkivera_radera"] is False
        assert d["inbjudningar"] == [] and all("epost" not in m for m in d["medlemmar"])
        with pytest.raises(BehorighetFel):
            v.r.byt_projektnamn(u, v.p, "Nytt")
        with pytest.raises(BehorighetFel):
            v.r.bjud_in(u, v.p, "ny@example.test", "lasare")
        with pytest.raises(BehorighetFel):
            v.r.ge_projektroll(u, v.id["lasare"], v.p, "utredare")
        with pytest.raises(BehorighetFel):
            v.r.aterkalla_projektroll(u, v.id["lasare"], v.p)
        with pytest.raises(BehorighetFel):
            v.r.satt_arkiverad(u, v.p, True)
        with pytest.raises(BehorighetFel):
            v.r.kontrollera_radera(u, v.p, "Projektet")
    # Utomstående ser inte ens att projektet finns.
    for vem in ("fremling", "granne"):
        with pytest.raises(RegisterFel):
            v.r.projektinstallningar(v.id[vem], v.p)


def test_agare_andrar_namn_medlemmar_och_delning(miljo: Miljo) -> None:
    v = _varld(miljo)
    a = v.id["agare"]
    d = v.r.projektinstallningar(a, v.p)
    assert d["far_andra"] and d["far_andra_delning"] and d["far_arkivera_radera"]
    assert {m["roll"] for m in d["medlemmar"]} == {"agare", "utredare", "lasare"}
    assert v.r.byt_projektnamn(a, v.p, "  Nytt namn ") == "Nytt namn"
    v.r.ge_projektroll(a, v.id["lasare"], v.p, "utredare")
    assert v.r.projektroll(v.id["lasare"], v.p) == "utredare"
    v.r.bjud_in(a, v.p, "ny@example.test", "agare")
    assert [(i["epost"], i["roll"]) for i in v.r.projekt_inbjudningar(a, v.p)] == [
        ("ny@example.test", "agare")
    ]
    v.r.satt_organisation_synlig(a, v.p, True)
    v.r.aterkalla_projektroll(a, v.id["lasare"], v.p)
    assert v.r.projektroll(v.id["lasare"], v.p) is None


def test_organisationsadmin_andrar_medlemmar_men_arkiverar_inte(miljo: Miljo) -> None:
    v = _varld(miljo)
    o = v.id["orgadmin"]
    assert v.r.byt_projektnamn(o, v.p, "Admin-namn") == "Admin-namn"
    v.r.ge_projektroll(o, v.id["fremling"], v.p, "lasare")
    d = v.r.projektinstallningar(o, v.p)
    assert d["far_andra"] is True and d["far_arkivera_radera"] is False
    with pytest.raises(BehorighetFel):
        v.r.satt_arkiverad(o, v.p, True)
    with pytest.raises(BehorighetFel):
        v.r.kontrollera_radera(o, v.p, "Admin-namn")
    # En admin i en annan organisation får ingenting.
    with pytest.raises(BehorighetFel):
        v.r.byt_projektnamn(v.id["granne"], v.p, "Fel")


def test_superadmin_arkiverar_och_raderar_utan_projektroll(miljo: Miljo) -> None:
    v = _varld(miljo)
    s = v.id["super"]
    v.r.satt_arkiverad(s, v.p, True)
    assert v.r.kontrollera_radera(s, v.p, "Projektet") == "Projektet"


def test_namnet_ska_vara_unikt_och_giltigt(miljo: Miljo) -> None:
    v = _varld(miljo)
    v.r.skapa_projekt(v.org, "Annat")
    with pytest.raises(RegisterFel):
        v.r.byt_projektnamn(v.id["agare"], v.p, "Annat")
    with pytest.raises(RegisterFel):
        v.r.byt_projektnamn(v.id["agare"], v.p, "  ")
    assert v.r.byt_projektnamn(v.id["agare"], v.p, "Projektet") == "Projektet"  # samma namn är ok


def test_projektet_har_alltid_en_agare(miljo: Miljo) -> None:
    v = _varld(miljo)
    a = v.id["agare"]
    with pytest.raises(RegisterFel):
        v.r.aterkalla_projektroll(a, a, v.p)
    with pytest.raises(RegisterFel):
        v.r.ge_projektroll(a, a, v.p, "utredare")
    v.r.ge_projektroll(a, v.id["utredare"], v.p, "agare")  # en andra ägare
    v.r.ge_projektroll(a, a, v.p, "utredare")  # nu får den första sänka sig själv
    assert v.r.projektroll(a, v.p) == "utredare"


def test_organisationsadmin_far_ta_bort_sista_agaren(miljo: Miljo) -> None:
    v = _varld(miljo)
    v.r.aterkalla_projektroll(v.id["orgadmin"], v.id["agare"], v.p)
    assert v.r.projektroll(v.id["agare"], v.p) is None


def test_skaparen_ar_agare_aven_som_utredare(miljo: Miljo) -> None:
    r = miljo.register
    org = r.skapa_organisation("Kommun")
    admin = r.forbered_konto("a@example.test")
    r.gor_organisationsadmin(admin, org)
    p = r.skapa_projekt_av(admin, org, "Skapat")
    assert r.projektroll(admin, p) == "utredare" and r.ar_projektagare(admin, p)
    r.satt_arkiverad(admin, p, True)


def test_arkiverade_syns_inte_i_listan_men_gar_att_aterstalla(miljo: Miljo) -> None:
    v = _varld(miljo)
    a = v.id["agare"]
    assert [p["id"] for p in v.r.projekt_med_effektiv_roll(a)] == [v.p]
    v.r.satt_arkiverad(a, v.p, True)
    assert v.r.projekt_med_effektiv_roll(a) == []
    assert [p["id"] for p in v.r.projekt_med_effektiv_roll(a, arkiverade=True)] == [v.p]
    assert (
        v.r.effektiv_projektroll(a, v.p).roll == "agare"
    )  # rätten finns kvar, projektet är bara dolt
    v.r.satt_arkiverad(a, v.p, False)
    assert [p["id"] for p in v.r.projekt_med_effektiv_roll(a)] == [v.p]
    assert v.r.projekt_med_effektiv_roll(a, arkiverade=True) == []


def test_radering_tar_bort_roller_och_inbjudningar_och_loggar_utan_personuppgifter(
    miljo: Miljo,
) -> None:
    v = _varld(miljo)
    a = v.id["agare"]
    v.r.bjud_in(a, v.p, "hemlig.person@example.test", "lasare")
    andra = v.r.skapa_projekt(v.org, "Orort")
    v.r.bjud_in(v.id["orgadmin"], andra, "kvar@example.test", "lasare")
    with pytest.raises(RegisterFel):
        v.r.kontrollera_radera(a, v.p, "fel namn")
    assert v.r.radera_projekt(a, v.p) == {"roller": 3, "inbjudningar": 1}
    assert not v.r.projekt_finns(v.p) and v.r.projekt_finns(andra)
    with v.r.db.lasning() as c:
        assert c.execute("SELECT COUNT(*) FROM projektbehorighet").fetchone()[0] == 0
        assert [r[0] for r in c.execute("SELECT epost FROM projektinbjudan")] == [
            "kvar@example.test"
        ]
        rad = c.execute("SELECT * FROM handelse WHERE handelse = 'projekt-raderat'").fetchone()
    assert rad["konto_id"] == a and v.p in rad["detalj"] and v.org in rad["detalj"]
    text = " ".join(str(x) for x in tuple(rad))
    assert "Projektet" not in text and "example.test" not in text and "hemlig" not in text
    with pytest.raises(RegisterFel):
        v.r.radera_projekt(a, v.p)


def test_migrering_till_v7_behaller_roller_och_ger_agare(tmp_path: Path) -> None:
    sokvag = tmp_path / "v6.sqlite"
    c = sqlite3.connect(sokvag)
    c.execute("PRAGMA foreign_keys = ON")
    for skript in db_modul.MIGRERINGAR[:6]:
        for sats in skript.split(";\n"):
            if sats.strip():
                c.execute(sats)
    c.execute("PRAGMA user_version = 6")
    c.executescript(
        """
        INSERT INTO konto (id, epost, epost_verifierad, namn, skapad, superadmin)
          VALUES ('k1', 'a@example.test', 1, 'A', 0, 0);
        INSERT INTO organisation (id, namn, skapad) VALUES ('o1', 'Gammal', 0);
        INSERT INTO projekt (id, organisation_id, namn, skapad) VALUES ('p1', 'o1', 'Gammalt', 0);
        INSERT INTO projektbehorighet (konto_id, projekt_id, roll, kalla, skapad)
          VALUES ('k1', 'p1', 'utredare', 'manuell', 0);
        INSERT INTO projektinbjudan (id, projekt_id, epost, roll, skapad_av, skapad, giltig_till)
          VALUES ('i1', 'p1', 'x@example.test', 'lasare', 'k1', 0, 99999999999);
        """
    )
    c.commit()
    c.close()
    db = Databas(sokvag)
    with db.transaktion() as c2:
        assert c2.execute("PRAGMA user_version").fetchone()[0] == 7
        assert c2.execute("SELECT roll FROM projektbehorighet").fetchone()[0] == "utredare"
        assert c2.execute("SELECT arkiverad FROM projekt").fetchone()[0] is None
        c2.execute("UPDATE projektbehorighet SET roll = 'agare'")
        c2.execute("UPDATE projektinbjudan SET roll = 'agare'")
        with pytest.raises(sqlite3.IntegrityError):
            c2.execute("UPDATE projektbehorighet SET roll = 'chef'")
        c2.execute("DELETE FROM projekt")  # ON DELETE CASCADE finns kvar efter ombyggnaden
        assert c2.execute("SELECT COUNT(*) FROM projektbehorighet").fetchone()[0] == 0
        assert c2.execute("SELECT COUNT(*) FROM projektinbjudan").fetchone()[0] == 0
        index = {r["name"] for r in c2.execute("PRAGMA index_list(projektinbjudan)")}
        assert "projektinbjudan_epost" in index


# --- HTTP (/konto/projekt/<id>/...) --------------------------------------------------------------


def test_http_installningar_och_andringar_per_roll(miljo: Miljo) -> None:
    v = bygg_varld(miljo)
    # Läsaren (inbjuden) ser sidan men utan e-post, och nekas alla ändringar.
    st, d = anrop(v.anvandare, "GET", f"/konto/projekt/{v.p1}/installningar")
    assert st == 200 and d["din_roll"] == "lasare" and d["far_andra"] is False
    assert all("epost" not in m for m in d["medlemmar"])
    bas = f"/konto/projekt/{v.p1}"
    for metod, sokv, data in (
        ("PUT", f"{bas}/arkiv", {"arkiverad": True}),
        ("POST", f"{bas}/inbjudningar", {"epost": "x@example.test", "roll": "lasare"}),
        ("PUT", f"{bas}/medlemmar/{v.id['anvandare']}", {"roll": "utredare"}),
        ("DELETE", f"{bas}/medlemmar/{v.id['anvandare']}", None),
    ):
        assert anrop(v.anvandare, metod, sokv, data)[0] == 403, sokv
    # Organisationsadmin: får bjuda in och ändra roll men inte arkivera (inte ägare i projektet).
    st, d = anrop(v.admin1, "GET", f"/konto/projekt/{v.p1}/installningar")
    assert st == 200 and d["far_andra"] is True and d["far_arkivera_radera"] is False
    assert (
        anrop(
            v.admin1, "POST", f"{bas}/inbjudningar", {"epost": "ny@example.test", "roll": "agare"}
        )[0]
        == 201
    )
    assert (
        anrop(v.admin1, "PUT", f"{bas}/medlemmar/{v.id['anvandare']}", {"roll": "utredare"})[0]
        == 200
    )
    assert anrop(v.admin1, "PUT", f"{bas}/arkiv", {"arkiverad": True})[0] == 403
    # Den andra organisationens admin ser inte projektet; superadmin arkiverar och återställer.
    assert anrop(v.admin2, "GET", f"/konto/projekt/{v.p1}/installningar")[0] == 404
    assert anrop(v.admin2, "PUT", f"{bas}/arkiv", {"arkiverad": True})[0] == 403
    assert (
        anrop(v.superadmin, "PUT", f"{bas}/arkiv", {"arkiverad": True})[1]["status"] == "arkiverat"
    )
    st, d = anrop(v.superadmin, "GET", f"/konto/projekt/{v.p1}/installningar")
    assert d["projekt"]["arkiverad"] is True
    assert (
        anrop(v.superadmin, "PUT", f"{bas}/arkiv", {"arkiverad": False})[1]["status"]
        == "aterstallt"
    )
    # Utan CSRF nekas skrivande anrop.
    assert anrop(v.superadmin, "PUT", f"{bas}/arkiv", {"arkiverad": True}, csrf=False)[0] == 403
    # Återkalla inbjudan.
    st, d = anrop(v.admin1, "GET", f"/konto/projekt/{v.p1}/installningar")
    oppen = [i for i in d["inbjudningar"] if i["status"] == "oppen"]
    assert anrop(v.admin1, "DELETE", f"{bas}/inbjudningar/{oppen[0]['id']}")[0] == 200
