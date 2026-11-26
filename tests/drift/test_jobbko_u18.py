# SPDX-License-Identifier: GPL-3.0-or-later
"""Enhetstester för U18: S5k pull-jobbkö, S10 organisationskvoter och kostnadsbok."""

from __future__ import annotations

import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest

from drift import (
    JobbKo,
    JobbKoFel,
    KvotOverskridenFel,
    LeaseOgiltigFel,
    OrgKvot,
    PiiForbjudetFel,
)


class _StyrbarKlocka:
    """Deterministisk testklocka i UTC-sekunder utan sleep."""

    def __init__(self, start_iso: str = "2026-11-15T10:00:00Z") -> None:
        dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        self.nu = float(dt.timestamp())

    def __call__(self) -> float:
        return self.nu

    def fram(self, sekunder: float) -> float:
        self.nu += sekunder
        return self.nu


def test_sqlite_wal_och_filrattigheter(tmp_path: Path) -> None:
    db_fil = tmp_path / "drift" / "jobbko.sqlite"
    JobbKo(db_fil)
    assert db_fil.is_file()
    mode = stat.S_IMODE(db_fil.stat().st_mode)
    assert mode == 0o600

    conn = sqlite3.connect(db_fil)
    try:
        journal = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()
    assert journal == "wal"
    assert version == 1


def test_s10_forval_kvot_noll_nekar_tills_kvot_tilldelats(tmp_path: Path) -> None:
    klocka = _StyrbarKlocka()
    ko = JobbKo(tmp_path / "ko.sqlite", klocka=klocka)

    assert ko.hamta_kvot("org-kommun-1") == OrgKvot(max_aktiva_jobb=0, manadsbudget_sek=0.0)

    with pytest.raises(KvotOverskridenFel) as exc:
        ko.lagg_till(
            "org-kommun-1",
            "konto-001",
            "proj-haga",
            "2d",
            {"scenarier": ["bas"]},
            12.50,
        )
    assert exc.value.orsak == "max_aktiva_jobb"
    assert exc.value.org_id == "org-kommun-1"
    assert exc.value.grans == 0.0

    # Tilldela kvot: nu accepteras alla 4 tillåtna jobbtyper.
    ko.satt_kvot("org-kommun-1", max_aktiva_jobb=5, manadsbudget_sek=250.0)
    assert ko.hamta_kvot("org-kommun-1") == OrgKvot(max_aktiva_jobb=5, manadsbudget_sek=250.0)

    for typ in ("2d", "lbm", "batch", "rapport"):
        post = ko.lagg_till(
            "org-kommun-1",
            "konto-001",
            "proj-haga",
            typ,
            {"typ_test": typ},
            10.0,
        )
        assert post.status == "ko"
        assert post.typ == typ


def test_s10_kvot_max_aktiva_och_manadsbudget_aldrig_tyst_overskridande(tmp_path: Path) -> None:
    klocka = _StyrbarKlocka("2026-11-10T08:00:00Z")
    ko = JobbKo(tmp_path / "ko.sqlite", klocka=klocka)
    ko.satt_kvot("org-a", max_aktiva_jobb=2, manadsbudget_sek=100.0)

    j1 = ko.lagg_till("org-a", "k-1", "p-1", "2d", {"r": 1}, 40.0)
    j2 = ko.lagg_till("org-a", "k-1", "p-1", "lbm", {"r": 2}, 40.0)

    # Tredje jobbet nekas på max_aktiva_jobb = 2.
    with pytest.raises(KvotOverskridenFel) as exc_aktiva:
        ko.lagg_till("org-a", "k-1", "p-1", "batch", {"r": 3}, 1.0)
    assert exc_aktiva.value.orsak == "max_aktiva_jobb"
    assert exc_aktiva.value.aktuellt == 2.0
    assert exc_aktiva.value.grans == 2.0

    # Slutför j1 med faktisk kostnad 55.0 SEK -> aktiv plats frigörs, men 55 + 40 = 95 SEK bokat!
    leasad = ko.hamta_nasta("arb-1")
    assert leasad is not None and leasad.jobb_id == j1.jobb_id
    ko.slutfor(j1.jobb_id, "arb-1", 55.0, {"sha256": "abc123"})

    # Nytt jobb på 6.0 SEK ska nekas: 55.0 (faktisk) + 40.0 (reserverad för j2) + 6.0 = 101 > 100.
    with pytest.raises(KvotOverskridenFel) as exc_budget:
        ko.lagg_till("org-a", "k-1", "p-1", "rapport", {"r": 4}, 6.0)
    assert exc_budget.value.orsak == "manadsbudget_sek"
    assert exc_budget.value.aktuellt == pytest.approx(95.0)
    assert exc_budget.value.grans == pytest.approx(100.0)
    assert exc_budget.value.begard == pytest.approx(6.0)

    # Exakt 5.0 SEK ryms (55 + 40 + 5 = 100.0 SEK).
    j3 = ko.lagg_till("org-a", "k-1", "p-1", "rapport", {"r": 5}, 5.0)
    assert j3.status == "ko"

    # Slutför j2 med 0 SEK (under uppskattningen) -> frigör 40 SEK i budget!
    leasad2 = ko.hamta_nasta("arb-1")
    assert leasad2 is not None and leasad2.jobb_id == j2.jobb_id
    ko.slutför(j2.jobb_id, "arb-1", 10.0, {"ok": True})

    bok = ko.kostnadsbok_manad("org-a", "2026-11")
    assert bok.faktisk_kostnad_sek == pytest.approx(65.0)
    assert bok.reserverad_kostnad_sek == pytest.approx(5.0)
    assert bok.total_kostnad_sek == pytest.approx(70.0)
    assert bok.kvarvarande_budget_sek == pytest.approx(30.0)
    assert bok.antal_jobb == 3
    assert bok.antal_klara == 2
    assert bok.antal_aktiva == 1
    assert bok.larmniva == "50%"


def test_noll_pii_avvisar_epost_och_personuppgiftsnycklar(tmp_path: Path) -> None:
    ko = JobbKo(tmp_path / "ko.sqlite")
    ko.satt_kvot("org-1", max_aktiva_jobb=5, manadsbudget_sek=100.0)

    with pytest.raises(PiiForbjudetFel):
        ko.lagg_till("org-1", "anna.andersson@kommun.se", "proj-1", "2d", {}, 1.0)

    with pytest.raises(PiiForbjudetFel):
        ko.lagg_till("anna@kommun.se", "konto-1", "proj-1", "2d", {}, 1.0)

    with pytest.raises(PiiForbjudetFel):
        ko.lagg_till(
            "org-1",
            "konto-1",
            "proj-1",
            "rapport",
            {"projekt": "proj-1", "kontakt": {"epost": "test@example.test"}},
            1.0,
        )

    jobb = ko.lagg_till("org-1", "konto-1", "proj-1", "rapport", {"utdata": "pdf"}, 1.0)
    leasad = ko.hamta_nasta("arb-1")
    assert leasad is not None and leasad.jobb_id == jobb.jobb_id
    with pytest.raises(PiiForbjudetFel):
        ko.slutfor(jobb.jobb_id, "arb-1", 1.0, {"namn": "Anna Andersson"})


def test_prioritet_fifo_och_typfilter(tmp_path: Path) -> None:
    klocka = _StyrbarKlocka()
    ko = JobbKo(tmp_path / "ko.sqlite", klocka=klocka)
    ko.satt_kvot("org-1", max_aktiva_jobb=10, manadsbudget_sek=1000.0)

    j_lag = ko.lagg_till("org-1", "k-1", "p-1", "2d", {"nr": 1}, 5.0, prioritet=0)
    klocka.fram(1.0)
    j_hog_1 = ko.lagg_till("org-1", "k-1", "p-1", "lbm", {"nr": 2}, 5.0, prioritet=10)
    klocka.fram(1.0)
    j_hog_2 = ko.lagg_till("org-1", "k-1", "p-1", "2d", {"nr": 3}, 5.0, prioritet=10)

    # Typfilter '2d' ska plocka j_hog_2 före j_lag och lämna lbm orörd.
    ut1 = ko.hamta_nasta("arb-2d", typer=["2d"])
    assert ut1 is not None and ut1.jobb_id == j_hog_2.jobb_id

    # Utan filter kommer kvarvarande högprioriterat (lbm), sedan lågprioriterat (2d).
    ut2 = ko.hamta_nasta("arb-alla")
    assert ut2 is not None and ut2.jobb_id == j_hog_1.jobb_id
    ut3 = ko.hamta_nasta("arb-alla")
    assert ut3 is not None and ut3.jobb_id == j_lag.jobb_id
    assert ko.hamta_nasta("arb-alla") is None


def test_heartbeat_och_kraschaterhamtning_med_retry_tak(tmp_path: Path) -> None:
    klocka = _StyrbarKlocka("2026-11-20T12:00:00Z")
    ko = JobbKo(tmp_path / "ko.sqlite", klocka=klocka)
    ko.satt_kvot("org-1", max_aktiva_jobb=5, manadsbudget_sek=100.0)

    jobb = ko.lagg_till("org-1", "k-1", "p-1", "2d", {"ruta": "r1"}, 25.0, max_forsok=2)

    # Försök 1: arbetare-1 leasar 100 s och förnyar vid +60 s.
    l1 = ko.hamta_nasta("arb-1", lease_sekunder=100)
    assert l1 is not None
    assert l1.forsok == 1
    assert l1.lease_utgar_utc == pytest.approx(klocka.nu + 100.0)

    klocka.fram(60.0)
    # Fel arbetare får inte förnya leasen.
    with pytest.raises(LeaseOgiltigFel):
        ko.fornya_lease(jobb.jobb_id, "arb-annan", lease_sekunder=100)

    fornyad = ko.fornya_lease(jobb.jobb_id, "arb-1", lease_sekunder=120)
    assert fornyad.lease_utgar_utc == pytest.approx(klocka.nu + 120.0)

    # Vid +100 s efter förnyelse är leasen fortfarande giltig -> inget återtas.
    klocka.fram(100.0)
    assert ko.atertag_utgangna_leasar().antal_atertagna == 0

    # Vid +25 s till har leasen löpt ut: zombie-arbetare får inte slutföra eller förnya!
    klocka.fram(25.0)
    with pytest.raises(LeaseOgiltigFel):
        ko.fornya_lease(jobb.jobb_id, "arb-1")
    with pytest.raises(LeaseOgiltigFel):
        ko.slutfor(jobb.jobb_id, "arb-1", 10.0)

    # Kraschåterhämtning återköar jobbet (försök 1 < max_forsok 2).
    ater1 = ko.atertag_utgangna_leasar()
    assert ater1.aterkoade == (jobb.jobb_id,)
    assert ater1.misslyckade == ()

    # Försök 2: arbetare-2 plockar upp jobbet men kraschar också.
    l2 = ko.hamta_nasta("arb-2", lease_sekunder=50)
    assert l2 is not None and l2.jobb_id == jobb.jobb_id and l2.forsok == 2

    klocka.fram(60.0)
    ater2 = ko.atertag_utgangna_leasar()
    assert ater2.aterkoade == ()
    assert ater2.misslyckade == (jobb.jobb_id,)

    slut = ko.hamta_jobb(jobb.jobb_id)
    assert slut is not None
    assert slut.status == "misslyckad"
    assert slut.forsok == 2
    assert slut.faktisk_kostnad_sek == 0.0
    assert slut.feltext is not None and "2/2" in slut.feltext

    # Budgetreservationen (25 SEK) frigjordes när jobbet misslyckades med 0 SEK faktisk kostnad.
    bok = ko.kostnadsbok_manad("org-1", "2026-11")
    assert bok.reserverad_kostnad_sek == 0.0
    assert bok.faktisk_kostnad_sek == 0.0
    assert bok.kvarvarande_budget_sek == pytest.approx(100.0)
    assert bok.antal_misslyckade == 1


def test_misslyckas_med_delkostnad_och_aterko(tmp_path: Path) -> None:
    klocka = _StyrbarKlocka("2026-11-22T09:00:00Z")
    ko = JobbKo(tmp_path / "ko.sqlite", klocka=klocka)
    ko.satt_kvot("org-1", max_aktiva_jobb=5, manadsbudget_sek=100.0)

    j = ko.lagg_till("org-1", "k-1", "p-1", "batch", {"steg": 1}, 30.0, max_forsok=2)
    l1 = ko.hamta_nasta("arb-1")
    assert l1 is not None
    # Första försöket ber om återkö vid tillfälligt fel.
    efter_ater = ko.misslyckas(j.jobb_id, "arb-1", "tillfalligt fel", aterko_om_forsok_kvar=True)
    assert efter_ater.status == "ko"

    # Andra försöket misslyckas permanent med förbrukad beräkningskostnad 12.35 SEK.
    l2 = ko.hamta_nasta("arb-2")
    assert l2 is not None and l2.forsok == 2
    slut = ko.misslyckas(j.jobb_id, "arb-2", "OOM", faktisk_kostnad_sek=12.35)
    assert slut.status == "misslyckad"
    assert slut.faktisk_kostnad_sek == pytest.approx(12.35)

    bok = ko.kostnadsbok_manad("org-1", "2026-11")
    assert bok.faktisk_kostnad_sek == pytest.approx(12.35)
    assert bok.kvarvarande_budget_sek == pytest.approx(87.65)


def test_kostnadsbok_exakt_summering_och_manadsseparation(tmp_path: Path) -> None:
    klocka = _StyrbarKlocka("2026-11-30T23:55:00Z")
    ko = JobbKo(tmp_path / "ko.sqlite", klocka=klocka)
    ko.satt_kvot("org-1", max_aktiva_jobb=10, manadsbudget_sek=100.0)

    # Tre jobb i november med flyttalskänsliga belopp: 10.10 + 20.20 + 50.05 = 80.35 exakt!
    for belopp in (10.10, 20.20, 50.05):
        j = ko.lagg_till("org-1", "k-1", "p-1", "2d", {}, belopp)
        leasad = ko.hamta_nasta("arb-1")
        assert leasad is not None and leasad.jobb_id == j.jobb_id
        ko.slutfor(j.jobb_id, "arb-1", belopp)

    nov = ko.kostnadsbok_manad("org-1", "2026-11")
    assert nov.faktisk_kostnad_sek == 80.35
    assert nov.kvarvarande_budget_sek == 19.65
    assert nov.antal_jobb == 3
    assert nov.larmniva == "80%"

    # Hoppa till december: ny månad har full 100.0 SEK budget!
    klocka.fram(600.0)  # 2026-12-01T00:05:00Z
    j_dec = ko.lagg_till("org-1", "k-1", "p-1", "lbm", {}, 100.0)
    assert j_dec.ar_manad == "2026-12"
    dec = ko.kostnadsbok_manad("org-1", "2026-12")
    assert dec.reserverad_kostnad_sek == 100.0
    assert dec.kvarvarande_budget_sek == 0.0
    assert dec.larmniva == "100%"
    assert nov.antal_jobb == 3


def test_samtidig_atomisk_leasing_och_kvotgrans_flera_tradar(tmp_path: Path) -> None:
    ko = JobbKo(tmp_path / "samtidig.sqlite")
    ko.satt_kvot("org-race", max_aktiva_jobb=20, manadsbudget_sek=1000.0)

    skapade_idn = {
        ko.lagg_till("org-race", "k-1", "p-1", "2d", {"idx": i}, 5.0).jobb_id for i in range(20)
    }

    def plocka(worker_nr: int) -> list[str]:
        mina: list[str] = []
        while True:
            post = ko.hamta_nasta(f"arb-{worker_nr}", lease_sekunder=120)
            if post is None:
                break
            mina.append(post.jobb_id)
        return mina

    with ThreadPoolExecutor(max_workers=8) as pool:
        resultat = list(pool.map(plocka, range(8)))

    alla_leasade = [jid for grupp in resultat for jid in grupp]
    # Varje jobb får leasas EXAKT en gång: inga dubbletter, inga tappade jobb!
    assert len(alla_leasade) == 20
    assert set(alla_leasade) == skapade_idn

    # Testa även samtidig inläggning mot en tight S10-budget (max 5 jobb á 20 SEK i 100 SEK budget).
    ko.satt_kvot("org-tight", max_aktiva_jobb=10, manadsbudget_sek=100.0)

    def forsok_lagg_till(i: int) -> bool:
        try:
            ko.lagg_till("org-tight", "k-1", "p-1", "batch", {"i": i}, 20.0)
            return True
        except KvotOverskridenFel:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        utfall = list(pool.map(forsok_lagg_till, range(12)))

    assert sum(utfall) == 5
    assert len(ko.lista_jobb(org_id="org-tight")) == 5


def test_ogiltiga_parametrar_ger_tydligt_fel(tmp_path: Path) -> None:
    ko = JobbKo(tmp_path / "ko.sqlite")
    ko.satt_kvot("org-1", max_aktiva_jobb=5, manadsbudget_sek=100.0)

    with pytest.raises(JobbKoFel):
        ko.lagg_till("org-1", "k-1", "p-1", "okand_typ", {}, 1.0)
    with pytest.raises(JobbKoFel):
        ko.lagg_till("org-1", "k-1", "p-1", "2d", {}, -1.0)
    with pytest.raises(JobbKoFel):
        ko.lagg_till("org-1", "k-1", "p-1", "2d", {"nan": float("nan")}, 1.0)
    with pytest.raises(JobbKoFel):
        ko.kostnadsbok_manad("org-1", "2026-13")
    with pytest.raises(JobbKoFel):
        ko.hamta_nasta("arb-1", lease_sekunder=0)
