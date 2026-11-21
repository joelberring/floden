"""Tester för körning i skiss- och rapportläge, manifest och utdata."""

from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import numpy as np

from buller.lagen import (
    SKISSMARKNING,
    BullerKorning,
    Karninfo,
    SvitStatus,
    Toleransprofil,
    kor_buller,
    las_inbyggd_profil,
    skriv_korning,
)
from buller.operator import ANTAGANDE_OVRE_GRANS, Rektangel, las_overforingslager
from buller.operator.lager import a_vagd_niva_db
from buller.operator.syntetisk import (
    SyntetiskScen,
    skapa_syntetisk_scen,
    syntetisk_emission_db,
    syntetisk_vagning_db,
)
from buller.typer import BullerFel, Flyttal, Kallsegment, Mottagare, Vader
from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost, las_manifest
from tests.buller.operator.test_lager import RaknandeParberaknare

SYNTETISK_KARNA = Karninfo(namn="syntetisk", version="0", nord2000_konform=False)
# Låtsas-konform kärna: bara för att pröva rapportlägets flöde på den syntetiska scenen.
LATSAS_KONFORM_KARNA = Karninfo(namn="syntetisk-latsas", version="0", nord2000_konform=True)
GODKAND_SVIT = SvitStatus(
    grupp="N", godkand=True, antal_fall=12, antal_godkanda=12, referens="test"
)


def kor(
    scen: SyntetiskScen,
    profil: Toleransprofil,
    *,
    lw_db: Flyttal | None = None,
    karna: Karninfo = SYNTETISK_KARNA,
    parberaknare: Any = None,
    forskjutning_db: float = 0.0,
    **ovrigt: Any,
) -> BullerKorning:
    mottagare = [
        Mottagare(id=1000 + i, e_m=float(e), n_m=float(n), hojd_over_mark_m=2.0)
        for i, (e, n) in enumerate(zip(scen.mottagare_e_m, scen.mottagare_n_m, strict=True))
    ]
    kallor = [
        Kallsegment(
            id=5000 + i, e_m=float(e), n_m=float(n), hojd_over_mark_m=0.5, langd_m=5.0, vag_id="v"
        )
        for i, (e, n) in enumerate(zip(scen.kalla_e_m, scen.kalla_n_m, strict=True))
    ]
    vaderklasser = [Vader(vind_m_s=float(i), vikt=float(v)) for i, v in
                    enumerate(scen.vaderklass_vikter)]  # fmt: skip
    return kor_buller(
        mottagare=mottagare,
        kallor=kallor,
        lw_db=syntetisk_emission_db(scen) if lw_db is None else lw_db,
        vaderklasser=vaderklasser,
        parberaknare=scen.parberaknare() if parberaknare is None else parberaknare,
        ovre_grans=scen.ovre_grans(forskjutning_db),
        a_vagning_db=syntetisk_vagning_db(),
        karna=karna,
        profil=profil,
        reflexordning=ovrigt.pop("reflexordning", profil.reflexordning),
        kodversion="test",
        start="2026-11-21T12:00:00+01:00",
        **ovrigt,
    )


class TestLagen(unittest.TestCase):
    scen: SyntetiskScen
    skiss: Toleransprofil
    rapport: Toleransprofil

    @classmethod
    def setUpClass(cls) -> None:
        cls.scen = skapa_syntetisk_scen(
            antal_mottagare=30, antal_kallor=500, antal_vaderklasser=2, sida_m=1000.0
        )
        cls.skiss = las_inbyggd_profil("skiss")
        cls.rapport = las_inbyggd_profil("rapport")

    # ---------------------------------------------------------------- acceptans (e)

    def test_rapportlaget_vagrar_icke_konform_karna(self) -> None:
        with self.assertRaisesRegex(BullerFel, "Nord2000-konform"):
            kor(self.scen, self.rapport, karna=SYNTETISK_KARNA, testsvit_n=GODKAND_SVIT)

    def test_rapportlaget_kraver_godkand_testsvit_grupp_n(self) -> None:
        daliga: list[SvitStatus | None] = [
            None,
            dataclasses.replace(GODKAND_SVIT, godkand=False),
            dataclasses.replace(GODKAND_SVIT, antal_godkanda=11),
            dataclasses.replace(GODKAND_SVIT, grupp="A"),
            dataclasses.replace(GODKAND_SVIT, antal_fall=0, antal_godkanda=0),
        ]
        for status in daliga:
            with self.subTest(status=status), self.assertRaisesRegex(BullerFel, "grupp N"):
                kor(self.scen, self.rapport, karna=LATSAS_KONFORM_KARNA, testsvit_n=status)

    def test_rapportlaget_kor_med_konform_karna_och_godkand_svit(self) -> None:
        korning = kor(
            self.scen, self.rapport, karna=LATSAS_KONFORM_KARNA, testsvit_n=GODKAND_SVIT
        )
        self.assertEqual(korning.resultat.lage, "rapport")
        self.assertNotEqual(korning.resultat.markning, SKISSMARKNING)
        self.assertLessEqual(float(np.max(korning.resultat.felgrans_db)), 0.1 + 1e-9)
        parametrar = korning.manifest.parametrar
        self.assertEqual(parametrar["testsvit_n"]["godkand"], True)
        self.assertEqual(parametrar["karna"]["nord2000_konform"], True)
        self.assertLessEqual(korning.manifest.matvarden["uppmatt_klustringsfel_db"], 0.1)

    def test_skisslaget_marker_resultat_och_manifest(self) -> None:
        korning = kor(self.scen, self.skiss)
        self.assertEqual(korning.resultat.markning, "Skiss – ej för beslut")
        self.assertEqual(korning.manifest.parametrar["markning"], "Skiss – ej för beslut")
        self.assertEqual(korning.manifest.parametrar["lage"], "skiss")
        self.assertTrue(any("inte Nord2000-konform" in v for v in korning.manifest.varningar))
        # Även med konform kärna och godkänd svit är skissen märkt.
        korning = kor(self.scen, self.skiss, karna=LATSAS_KONFORM_KARNA, testsvit_n=GODKAND_SVIT)
        self.assertEqual(korning.resultat.markning, "Skiss – ej för beslut")
        # En profil kan inte stänga av märkningen.
        with self.assertRaises(BullerFel):
            dataclasses.replace(self.skiss, markning="")

    # ---------------------------------------------------------------- resultat

    def test_resultatet_haller_felgransen_mot_oberoende_facit(self) -> None:
        lw_db = syntetisk_emission_db(self.scen)
        facit = a_vagd_niva_db(self.scen.referensniva_db(lw_db), syntetisk_vagning_db())
        for profil, karna, svit in (
            (self.skiss, SYNTETISK_KARNA, None),
            (self.rapport, LATSAS_KONFORM_KARNA, GODKAND_SVIT),
        ):
            with self.subTest(lage=profil.lage):
                korning = kor(self.scen, profil, karna=karna, testsvit_n=svit)
                resultat = korning.resultat
                self.assertEqual(resultat.niva_db.shape, (30, 27))
                np.testing.assert_array_equal(resultat.mottagare_id, np.arange(1000, 1030))
                fel = facit - resultat.niva_a_db
                self.assertTrue(bool(np.all(fel >= -1e-9)))
                self.assertTrue(bool(np.all(fel <= resultat.felgrans_db + 1e-9)))
                self.assertLessEqual(float(np.max(fel)), profil.klustring_felgrans_db)

    def test_skillnaden_skiss_minus_rapport_ar_inom_skissens_felgrans(self) -> None:
        """B9: samma fråga körbar i båda lägen; skillnaden mäts."""
        skiss = kor(self.scen, self.skiss).resultat
        rapport = kor(
            self.scen, self.rapport, karna=LATSAS_KONFORM_KARNA, testsvit_n=GODKAND_SVIT
        ).resultat
        skillnad = skiss.niva_a_db - rapport.niva_a_db
        self.assertLessEqual(float(np.max(np.abs(skillnad))), 0.5)

    # ---------------------------------------------------------------- acceptans (f)

    def test_samma_indata_ger_samma_lager_och_manifestparametrar(self) -> None:
        a = kor(self.scen, self.skiss)
        b = kor(self.scen, self.skiss)
        self.assertEqual(a.lager.identitet, b.lager.identitet)
        self.assertEqual(
            json.dumps(a.manifest.parametrar, sort_keys=True),
            json.dumps(b.manifest.parametrar, sort_keys=True),
        )
        np.testing.assert_array_equal(a.resultat.niva_db, b.resultat.niva_db)
        annan = kor(self.scen, self.skiss, lw_db=syntetisk_emission_db(self.scen) + 1.0)
        self.assertNotEqual(
            annan.manifest.parametrar["emission_sha256"], a.manifest.parametrar["emission_sha256"]
        )

    def test_manifestet_foljer_kb4(self) -> None:
        post = FilPost(roll="bullerscen", fil="scen.gpkg", sha256="0" * 64)
        korning = kor(self.scen, self.skiss, antal_banor=1234, indata=[post])
        manifest = korning.manifest
        self.assertEqual(manifest.modul, "buller")
        self.assertEqual(manifest.kontrakt, "K0/v1")
        self.assertEqual(manifest.indata, [post])
        p = manifest.parametrar
        self.assertEqual(
            p["karna"], {"namn": "syntetisk", "version": "0", "nord2000_konform": False}
        )
        self.assertEqual(p["toleransprofil"]["namn"], "skiss")
        self.assertEqual(p["toleransprofil"]["sha256"], self.skiss.sha256)
        self.assertEqual(len(p["vaderklasser"]), 2)
        self.assertAlmostEqual(sum(v["vikt"] for v in p["vaderklasser"]), 1.0)
        self.assertEqual(
            p["kontrakt_buller"], {"KB1": "KB1/v1", "KB2": "KB2/v1", "KB3": "KB3/v1"}
        )
        self.assertEqual(p["antagande_ovre_grans"], ANTAGANDE_OVRE_GRANS)
        self.assertEqual(p["lager_identitet"], korning.lager.identitet)
        m = manifest.matvarden
        self.assertEqual(m["antal_mottagare"], 30)
        self.assertEqual(m["antal_kallor"], 500)
        self.assertEqual(m["antal_banor"], 1234)
        self.assertGreater(m["kortid_s"], 0.0)
        self.assertEqual(m["antal_kontrollmottagare"], 2)
        self.assertLessEqual(m["uppmatt_klustringsfel_db"], m["garanterad_felgrans_max_db"] + 1e-6)
        self.assertGreater(m["andel_beraknade_par"], 0.0)
        json.dumps(manifest.till_dict(), allow_nan=False)
        utan = kor(self.scen, self.skiss)
        self.assertIsNone(utan.manifest.matvarden["antal_banor"])
        self.assertTrue(any("Antal banor" in v for v in utan.manifest.varningar))

    # ---------------------------------------------------------------- återanvändning

    def test_andrad_emission_med_tidigare_lager_kraver_ingen_ny_geometri(self) -> None:
        profil = dataclasses.replace(self.skiss, kontrollmottagare_antal=0)
        forsta = kor(self.scen, profil)
        raknare = RaknandeParberaknare(self.scen)
        ny_lw_db = syntetisk_emission_db(self.scen) - 3.0  # samma spektrum, lägre nivå
        andra = kor(
            self.scen, profil, lw_db=ny_lw_db, parberaknare=raknare, tidigare_lager=forsta.lager
        )
        self.assertEqual(raknare.antal_anrop, 0)
        self.assertEqual(andra.lager.identitet, forsta.lager.identitet)
        self.assertEqual(andra.manifest.matvarden["antal_beraknade_par"], 0)
        self.assertEqual(
            andra.manifest.parametrar["tidigare_lager_identitet"], forsta.lager.identitet
        )
        np.testing.assert_allclose(
            andra.resultat.niva_db, forsta.resultat.niva_db - 3.0, atol=1e-9
        )
        ny = kor(self.scen, profil, lw_db=ny_lw_db)
        self.assertLessEqual(
            float(np.max(np.abs(ny.resultat.niva_a_db - andra.resultat.niva_a_db))), 0.01
        )

    def test_ny_emission_som_bryter_felgransen_ger_omklustring(self) -> None:
        forsta = kor(self.scen, self.skiss)
        slump = np.random.default_rng(4)
        ny_lw_db = syntetisk_emission_db(self.scen) + slump.uniform(-20.0, 20.0, size=(500, 1))
        andra = kor(self.scen, self.skiss, lw_db=ny_lw_db, tidigare_lager=forsta.lager)
        self.assertGreater(andra.manifest.matvarden["antal_omklustrade_mottagare"], 0)
        self.assertLessEqual(float(np.max(andra.resultat.felgrans_db)), 0.5 + 1e-9)
        facit = a_vagd_niva_db(self.scen.referensniva_db(ny_lw_db), syntetisk_vagning_db())
        self.assertLessEqual(float(np.max(facit - andra.resultat.niva_a_db)), 0.5)

    def test_inkrementell_korning_efter_flyttad_byggnad(self) -> None:
        profil = dataclasses.replace(
            self.rapport, klustring_felgrans_db=0.0, kontrollmottagare_antal=0
        )
        gammal = self.scen.byggnad
        ny = Rektangel(
            gammal.e_min_m + 100.0, gammal.n_min_m + 80.0, gammal.e_max_m + 100.0,
            gammal.n_max_m + 80.0,
        )  # fmt: skip
        efter = self.scen.med_byggnad(ny)
        rapportkrav: dict[str, Any] = {"karna": LATSAS_KONFORM_KARNA, "testsvit_n": GODKAND_SVIT}
        forsta = kor(self.scen, profil, **rapportkrav)
        full = kor(efter, profil, **rapportkrav)
        inkrementell = kor(
            efter,
            profil,
            tidigare_lager=forsta.lager,
            andrad_yta=gammal.union(ny),
            korridorindex=_IndexMedId(efter),
            **rapportkrav,
        )
        self.assertEqual(inkrementell.lager.identitet, full.lager.identitet)
        np.testing.assert_array_equal(inkrementell.resultat.niva_db, full.resultat.niva_db)
        omraknade = inkrementell.manifest.matvarden["antal_omraknade_par_inkrementellt"]
        self.assertGreater(omraknade, 0)
        self.assertLess(omraknade, 30 * 500)
        self.assertEqual(
            inkrementell.manifest.parametrar["andrad_yta"],
            dataclasses.asdict(gammal.union(ny)),
        )

    # ---------------------------------------------------------------- fel

    def test_inkonsekvent_indata_ger_fel(self) -> None:
        with self.assertRaisesRegex(BullerFel, "reflexordning"):
            kor(self.scen, self.skiss, reflexordning=self.skiss.reflexordning + 1)
        forsta = kor(self.scen, self.skiss)
        annan_scen = skapa_syntetisk_scen(
            antal_mottagare=30, antal_kallor=500, antal_vaderklasser=2, sida_m=1000.0, fro=8
        )
        with self.assertRaisesRegex(BullerFel, "tidigare lagret"):
            kor(annan_scen, self.skiss, tidigare_lager=forsta.lager)
        with self.assertRaises(BullerFel):
            kor(self.scen, self.skiss, andrad_yta=Rektangel(0.0, 0.0, 1.0, 1.0))
        with self.assertRaises(BullerFel):
            kor(
                self.scen,
                self.skiss,
                andrad_yta=Rektangel(0.0, 0.0, 1.0, 1.0),
                korridorindex=_IndexMedId(self.scen),
            )

    def test_rapportlaget_vagrar_nar_felgransen_inte_kan_garanteras(self) -> None:
        trang = dataclasses.replace(self.rapport, maxradie_m=150.0)
        with self.assertRaisesRegex(BullerFel, "kan inte garanteras"):
            kor(self.scen, trang, karna=LATSAS_KONFORM_KARNA, testsvit_n=GODKAND_SVIT)
        skiss = kor(self.scen, dataclasses.replace(self.skiss, maxradie_m=150.0))
        self.assertTrue(any("kan inte garanteras" in v for v in skiss.manifest.varningar))
        self.assertGreater(float(np.max(skiss.resultat.felgrans_db)), 0.5)

    def test_felaktig_ovre_grans_stoppar_korningen(self) -> None:
        with self.assertRaisesRegex(BullerFel, "Övre gränsen håller inte"):
            kor(self.scen, self.skiss, forskjutning_db=-30.0)

    # ---------------------------------------------------------------- utdata

    def test_skrivning_till_fil_med_kontrollsummor(self) -> None:
        korning = kor(self.scen, self.skiss)
        with tempfile.TemporaryDirectory() as katalog:
            manifest = skriv_korning(korning, Path(katalog) / "a")
            igen = skriv_korning(korning, Path(katalog) / "b")
            self.assertEqual({p.roll for p in manifest.utdata},
                             {"buller_resultat", "overforingslager"})  # fmt: skip
            for post in manifest.utdata:
                self.assertEqual(sha256_fil(Path(katalog) / "a" / post.fil), post.sha256)
            self.assertEqual(manifest.utdata, igen.utdata, "samma körning ger samma filer")
            last = las_manifest(Path(katalog) / "a" / "run.json")
            self.assertEqual(last.utdata, manifest.utdata)
            self.assertEqual(last.parametrar["markning"], SKISSMARKNING)
            with np.load(Path(katalog) / "a" / "buller_resultat.npz", allow_pickle=False) as data:
                self.assertEqual(str(data["markning"]), "Skiss – ej för beslut")
                self.assertEqual(str(data["lage"]), "skiss")
                np.testing.assert_array_equal(data["niva_db"], korning.resultat.niva_db)
                np.testing.assert_array_equal(data["niva_a_db"], korning.resultat.niva_a_db)
                np.testing.assert_array_equal(data["felgrans_db"], korning.resultat.felgrans_db)
                self.assertEqual(data["tersband_hz"].shape, (27,))
            lager = las_overforingslager(Path(katalog) / "a" / "overforingslager.npz")
            self.assertEqual(lager.identitet, korning.lager.identitet)
            self.assertEqual(korning.manifest.utdata, [], "ursprungsmanifestet är orört")


class _IndexMedId:
    """Den syntetiska scenens korridorindex, översatt till testets id-serier."""

    def __init__(self, scen: SyntetiskScen) -> None:
        self._index = scen.korridorindex()

    def berorda_par(
        self, e_min_m: float, n_min_m: float, e_max_m: float, n_max_m: float
    ) -> set[tuple[int, int]]:
        return {
            (1000 + m, 5000 + k)
            for m, k in self._index.berorda_par(e_min_m, n_min_m, e_max_m, n_max_m)
        }


if __name__ == "__main__":
    unittest.main()
