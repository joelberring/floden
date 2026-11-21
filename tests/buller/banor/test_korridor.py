"""Tester för `Korridorindex`: inga missade banor, andel falska träffar, fel och determinism."""

from __future__ import annotations

import inspect
import unittest

import numpy as np
import shapely
from numpy.typing import NDArray

from buller.banor.banfinnare import Banuppsattning, bygg_banuppsattning
from buller.banor.korridor import Korridorindex
from buller.scen.bullerscen import Bullerscen
from buller.scen.fasad import fasadpunkter
from buller.scen.kallsegment import kallsegment_fran_vagar
from buller.scen.syntetisk import syntetisk_scen
from buller.typer import Bana, BullerFel, Terrangprofil

ANTAL_RUTOR = 250


def _bana(m: int, k: int, e: list[float], n: list[float]) -> Bana:
    profil = Terrangprofil(
        x_m=np.array([0.0, 1.0]),
        z_m=np.zeros(2),
        flodesresistivitet_kNsm4=np.ones(1),
        rahet_m=np.zeros(1),
        kalla_hojd_m=0.5,
        mottagare_hojd_m=2.0,
    )
    return Bana(m, k, "direkt", profil, np.array(e), np.array(n))


class TestKorridorindexMotExaktGeometri(unittest.TestCase):
    """Acceptans (c): ingen berörd bana missas på slumpade rutor."""

    uppsattning: Banuppsattning
    scen: Bullerscen
    linjer: NDArray[np.object_]

    @classmethod
    def setUpClass(cls) -> None:
        scen = syntetisk_scen(sida_m=300.0, cell_m=1.0, tomt_m=50.0, fro=41)
        mottagare = fasadpunkter(
            scen,
            avstand_fran_fasad_m=2.0,
            punktavstand_m=15.0,
            forsta_vaning_hojd_m=2.0,
            vaningshojd_m=3.0,
            max_vaningar=1,
        ).mottagare[::3]
        kallor = kallsegment_fran_vagar(scen.vagar, max_langd_m=20.0, delkallhojder_m=[0.3])
        cls.uppsattning = bygg_banuppsattning(scen, mottagare, kallor, maxradie_m=140.0, steg_m=5.0)
        cls.linjer = np.array(
            [
                shapely.LineString(np.column_stack([b.plan_e_m, b.plan_n_m]))
                for b in cls.uppsattning.banor
            ],
            dtype=object,
        )
        cls.scen = scen

    def _rutor(self, slump: np.random.Generator) -> list[tuple[float, float, float, float]]:
        rutor = []
        for _ in range(ANTAL_RUTOR):
            sida_e, sida_n = slump.choice([0.5, 5.0, 20.0, 60.0], 2) * slump.uniform(0.5, 1.5, 2)
            e0 = self.scen.e_min_m + slump.uniform(-40.0, 320.0)
            n0 = self.scen.n_min_m + slump.uniform(-40.0, 320.0)
            rutor.append((float(e0), float(n0), float(e0 + sida_e), float(n0 + sida_n)))
        return rutor

    def test_inga_missar_och_andel_falska_traffar(self) -> None:
        banor = self.uppsattning.banor
        self.assertGreater(len(banor), 3000)
        self.assertGreater(sum(b.typ == "reflex" for b in banor), 500)
        m_id = np.array([b.mottagare_id for b in banor])
        k_id = np.array([b.kalla_id for b in banor])
        for halvbredd_m, cell_m, forfina in (
            (0.0, 25.0, True),
            (5.0, 25.0, True),
            (5.0, 25.0, False),
            (30.0, 10.0, True),
            (12.0, 80.0, False),
        ):
            index = Korridorindex(banor, halvbredd_m, cell_m=cell_m, forfina=forfina)
            slump = np.random.default_rng(51)
            exakta_totalt = 0
            funna_totalt = 0
            for ruta in self._rutor(slump):
                avstand = shapely.distance(self.linjer, shapely.box(*ruta))
                exakta = np.flatnonzero(avstand <= halvbredd_m)
                funna = index.berorda_banor(*ruta)
                missade = np.setdiff1d(exakta, funna)
                self.assertEqual(missade.size, 0, f"missade banor {missade[:5]} i ruta {ruta}")
                exakta_par = set(zip(m_id[exakta].tolist(), k_id[exakta].tolist(), strict=True))
                funna_par = index.berorda_par(*ruta)
                self.assertTrue(exakta_par <= funna_par)
                self.assertEqual(
                    funna_par, set(zip(m_id[funna].tolist(), k_id[funna].tolist(), strict=True))
                )
                exakta_totalt += int(exakta.size)
                funna_totalt += int(funna.size)
            self.assertGreater(exakta_totalt, 1000)
            falska = (funna_totalt - exakta_totalt) / funna_totalt
            print(
                f"\n[B6 c] halvbredd {halvbredd_m} m, cell {cell_m} m, förfining {forfina}: "
                f"{ANTAL_RUTOR} rutor, {exakta_totalt} berörda banor, {funna_totalt} funna, "
                f"0 missade, andel falska träffar {falska:.4f}"
            )
            if forfina:
                self.assertLess(falska, 0.001)

    def test_determinism(self) -> None:
        a = Korridorindex(self.uppsattning.banor, 5.0)
        b = self.uppsattning.korridorindex(5.0)
        for ruta in self._rutor(np.random.default_rng(3))[:40]:
            np.testing.assert_array_equal(a.berorda_banor(*ruta), b.berorda_banor(*ruta))
            self.assertEqual(a.berorda_par(*ruta), b.berorda_par(*ruta))


class TestKorridorindexSmaFall(unittest.TestCase):
    def setUp(self) -> None:
        self.banor = [
            _bana(1, 10, [0.0, 100.0], [0.0, 0.0]),
            _bana(1, 11, [0.0, 0.0], [0.0, 100.0]),
            _bana(2, 10, [50.0, 50.0, 100.0], [50.0, 100.0, 100.0]),  # reflexbana med knäck
        ]

    def test_signatur_enligt_overenskommelse(self) -> None:
        signatur = inspect.signature(Korridorindex.berorda_par)
        self.assertEqual(
            list(signatur.parameters), ["self", "e_min_m", "n_min_m", "e_max_m", "n_max_m"]
        )

    def test_kanda_svar(self) -> None:
        index = Korridorindex(self.banor, 2.0, cell_m=10.0)
        self.assertEqual(index.berorda_par(40.0, -1.0, 41.0, 1.0), {(1, 10)})
        self.assertEqual(index.berorda_par(-1.0, -1.0, 1.0, 1.0), {(1, 10), (1, 11)})
        self.assertEqual(index.berorda_par(70.0, 99.0, 71.0, 101.0), {(2, 10)})
        # 1,9 m från banan: inom korridoren. 3 m från: utanför.
        self.assertEqual(index.berorda_par(40.0, 1.9, 41.0, 5.0), {(1, 10)})
        self.assertEqual(index.berorda_par(40.0, 3.0, 41.0, 5.0), set())
        self.assertEqual(index.berorda_par(1000.0, 1000.0, 1001.0, 1001.0), set())
        self.assertEqual(index.berorda_par(-1000.0, -1000.0, -999.0, -999.0), set())
        self.assertEqual(
            index.berorda_par(-1000.0, -1000.0, 1000.0, 1000.0), {(1, 10), (1, 11), (2, 10)}
        )

    def test_tomt_index(self) -> None:
        index = Korridorindex([], 5.0)
        self.assertEqual(index.berorda_par(0.0, 0.0, 1.0, 1.0), set())

    def test_fel(self) -> None:
        for halvbredd, cell in ((-1.0, 10.0), (float("nan"), 10.0), (1.0, 0.0)):
            with self.subTest(halvbredd=halvbredd, cell=cell), self.assertRaises(BullerFel):
                Korridorindex(self.banor, halvbredd, cell_m=cell)
        index = Korridorindex(self.banor, 1.0)
        with self.assertRaisesRegex(BullerFel, "felvänd"):
            index.berorda_par(10.0, 0.0, 0.0, 1.0)
        with self.assertRaises(BullerFel):
            index.berorda_par(float("nan"), 0.0, 1.0, 1.0)
        with self.assertRaises(BullerFel):
            Korridorindex([_bana(1, 1, [0.0], [0.0])], 1.0)


if __name__ == "__main__":
    unittest.main()
