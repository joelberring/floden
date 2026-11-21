"""Tester för första ordningens reflexer: brute force-jämförelse och kända svar."""

from __future__ import annotations

import math
import unittest

import numpy as np

from buller.banor.banfinnare import bygg_banuppsattning, hitta_banor
from buller.banor.reflex import SIDOTOLERANS_M, forsta_ordningens_reflexer
from buller.scen.bullerscen import Bullerscen
from buller.scen.fasad import fasadpunkter
from buller.scen.kallsegment import kallsegment_fran_vagar
from buller.scen.syntetisk import syntetisk_scen
from buller.typer import ANTAL_BAND, BullerFel, Byggnad, Kallsegment, Mottagare
from tests.buller.scen.referens import (
    bilinjar_markhojd_m,
    fria_punkter,
    punkt_i_nagon_byggnad,
)

MAXRADIE_M = 120.0
FASADMARGINAL_M = 0.05


def _brute_force_reflexer(
    scen: Bullerscen,
    mottagare: list[tuple[float, float, float]],
    kallor: list[tuple[float, float, float]],
    maxradie_m: float,
) -> dict[tuple[int, int, int], tuple[float, float]]:
    """Alla (mottagare, källa, vägg) → reflexpunkt, sökt över samtliga väggsegment.

    Skriven oberoende av `buller.banor.reflex`: här speglas källan (inte mottagaren) och
    reflexpunkten fås som skärning mellan två sträckor. Höjderna anges i RH 2000.
    """
    traffar: dict[tuple[int, int, int], tuple[float, float]] = {}
    vaggnr = 0
    for byggnad in scen.byggnader:
        antal = byggnad.e_m.shape[0]
        area2 = sum(
            float(byggnad.e_m[i] * byggnad.n_m[(i + 1) % antal])
            - float(byggnad.e_m[(i + 1) % antal] * byggnad.n_m[i])
            for i in range(antal)
        )
        for i in range(antal):
            ae, an = float(byggnad.e_m[i]), float(byggnad.n_m[i])
            be, bn = float(byggnad.e_m[(i + 1) % antal]), float(byggnad.n_m[(i + 1) % antal])
            langd = math.hypot(be - ae, bn - an)
            # Utåtriktad normal: till höger om gångriktningen för en moturs polygon.
            ne, nn = (bn - an) / langd, -(be - ae) / langd
            if area2 < 0.0:
                ne, nn = -ne, -nn
            for m, (re, rn, rz) in enumerate(mottagare):
                d_r = (re - ae) * ne + (rn - an) * nn
                if d_r <= SIDOTOLERANS_M:
                    continue
                for k, (se, sn, sz) in enumerate(kallor):
                    d_s = (se - ae) * ne + (sn - an) * nn
                    if d_s <= SIDOTOLERANS_M:
                        continue
                    # Spegelkällan och skärningen mellan sträckan spegelkälla–mottagare och väggen.
                    spe, spn = se - 2.0 * d_s * ne, sn - 2.0 * d_s * nn
                    ue, un = re - spe, rn - spn
                    ve, vn = be - ae, bn - an
                    namnare = ue * vn - un * ve
                    if namnare == 0.0:
                        continue
                    t = ((ae - spe) * vn - (an - spn) * ve) / namnare
                    v = ((ae - spe) * un - (an - spn) * ue) / namnare
                    if not (0.0 <= v <= 1.0):
                        continue
                    pe, pn = spe + t * ue, spn + t * un
                    forsta = math.hypot(pe - se, pn - sn)
                    andra = math.hypot(re - pe, rn - pn)
                    if forsta + andra > maxradie_m:
                        continue
                    stralhojd = sz + (rz - sz) * forsta / (forsta + andra)
                    if byggnad.tak_rh2000_m <= stralhojd:
                        continue
                    if punkt_i_nagon_byggnad(
                        scen, pe + FASADMARGINAL_M * ne, pn + FASADMARGINAL_M * nn
                    ):
                        continue
                    traffar[(m, k, vaggnr + i)] = (pe, pn)
        vaggnr += antal
    return traffar


class TestReflexMotBruteForce(unittest.TestCase):
    """Acceptans (b): reflexbanorna är desamma som vid sökning över alla väggsegment."""

    scen: Bullerscen
    mottagare: list[Mottagare]
    kallor: list[Kallsegment]
    m_xyz: list[tuple[float, float, float]]
    k_xyz: list[tuple[float, float, float]]
    facit: dict[tuple[int, int, int], tuple[float, float]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.scen = syntetisk_scen(sida_m=300.0, cell_m=1.0, tomt_m=50.0, fro=21)
        slump = np.random.default_rng(31)
        fasad = fasadpunkter(
            cls.scen,
            avstand_fran_fasad_m=2.0,
            punktavstand_m=12.0,
            forsta_vaning_hojd_m=2.0,
            vaningshojd_m=3.0,
        ).mottagare
        urval = slump.choice(len(fasad), 14, replace=False)
        fe, fn = fria_punkter(cls.scen, slump, 10)
        mottagare = [fasad[int(i)] for i in sorted(urval)]
        mottagare += [
            Mottagare(10_000 + i, float(fe[i]), float(fn[i]), float(slump.uniform(1.5, 30.0)))
            for i in range(10)
        ]
        cls.mottagare = [
            Mottagare(
                i, m.e_m, m.n_m, m.hojd_over_mark_m, m.byggnad_id, m.vaning, m.fasadnormal_deg
            )
            for i, m in enumerate(mottagare)
        ]
        alla_kallor = kallsegment_fran_vagar(
            cls.scen.vagar, max_langd_m=15.0, delkallhojder_m=[0.01, 0.75]
        )
        cls.kallor = [
            alla_kallor[int(i)] for i in sorted(slump.choice(len(alla_kallor), 45, False))
        ]
        cls.m_xyz = [
            (m.e_m, m.n_m, bilinjar_markhojd_m(cls.scen, m.e_m, m.n_m) + m.hojd_over_mark_m)
            for m in cls.mottagare
        ]
        cls.k_xyz = [
            (k.e_m, k.n_m, bilinjar_markhojd_m(cls.scen, k.e_m, k.n_m) + k.hojd_over_mark_m)
            for k in cls.kallor
        ]
        cls.facit = _brute_force_reflexer(cls.scen, cls.m_xyz, cls.k_xyz, MAXRADIE_M)

    def test_samma_reflexer_som_brute_force(self) -> None:
        m = np.array(self.m_xyz)
        k = np.array(self.k_xyz)
        reflexer = forsta_ordningens_reflexer(
            self.scen, m[:, 0], m[:, 1], m[:, 2], k[:, 0], k[:, 1], k[:, 2], MAXRADIE_M,
            fasadmarginal_m=FASADMARGINAL_M,
        )  # fmt: skip
        funna = {
            (int(a), int(b), int(c)): (float(pe), float(pn))
            for a, b, c, pe, pn in zip(
                reflexer.mottagare_index,
                reflexer.kalla_index,
                reflexer.vagg_index[:, 0],
                reflexer.punkt_e_m[:, 0],
                reflexer.punkt_n_m[:, 0],
                strict=True,
            )
        }
        print(
            f"\n[B6 b] {len(self.m_xyz)} mottagare × {len(self.k_xyz)} källor × "
            f"{self.scen.vaggar.antal} väggar: {len(self.facit)} reflexer i brute force, "
            f"{len(funna)} funna"
        )
        self.assertGreater(len(self.facit), 300)
        self.assertEqual(set(funna), set(self.facit))
        storsta = max(
            math.hypot(funna[n][0] - self.facit[n][0], funna[n][1] - self.facit[n][1])
            for n in funna
        )
        self.assertLess(storsta, 1e-6)
        # Sorterat på (mottagare, källa, vägg).
        nycklar = list(
            zip(
                reflexer.mottagare_index.tolist(),
                reflexer.kalla_index.tolist(),
                reflexer.vagg_index[:, 0].tolist(),
                strict=True,
            )
        )
        self.assertEqual(nycklar, sorted(nycklar))

    def test_banfinnaren_ger_samma_reflexbanor(self) -> None:
        banor = hitta_banor(
            self.scen, self.mottagare, self.kallor, maxradie_m=MAXRADIE_M, egen_fasad_reflex=True
        )
        vaggar = self.scen.vaggar
        facit = sorted(
            (
                self.mottagare[m].id,
                self.kallor[k].id,
                self.scen.byggnader[int(vaggar.byggnad_index[v])].id,
                round(pe, 5),
                round(pn, 5),
            )
            for (m, k, v), (pe, pn) in self.facit.items()
        )
        funna = sorted(
            (
                b.mottagare_id,
                b.kalla_id,
                b.reflekterande_byggnader[0],
                round(float(b.plan_e_m[1]), 5),
                round(float(b.plan_n_m[1]), 5),
            )
            for b in banor
            if b.typ == "reflex"
        )
        self.assertEqual(funna, facit)
        # Direkta banor: exakt de par som ligger inom maxradien.
        direkta = {(b.mottagare_id, b.kalla_id) for b in banor if b.typ == "direkt"}
        vantade = {
            (m.id, k.id)
            for m in self.mottagare
            for k in self.kallor
            if math.hypot(m.e_m - k.e_m, m.n_m - k.n_m) <= MAXRADIE_M
        }
        self.assertEqual(direkta, vantade)
        self.assertEqual(sum(b.typ == "direkt" for b in banor), len(vantade))

    def test_egen_fasad_reflekterar_inte_som_standard(self) -> None:
        med = hitta_banor(
            self.scen, self.mottagare, self.kallor, maxradie_m=MAXRADIE_M, egen_fasad_reflex=True
        )
        utan = hitta_banor(self.scen, self.mottagare, self.kallor, maxradie_m=MAXRADIE_M)
        egen = {m.id: m.byggnad_id for m in self.mottagare}
        i_egen_fasad = [
            b
            for b in med
            if b.typ == "reflex" and b.reflekterande_byggnader[0] == egen[b.mottagare_id]
        ]
        self.assertGreater(len(i_egen_fasad), 10)
        self.assertEqual(len(utan), len(med) - len(i_egen_fasad))
        self.assertFalse(
            any(
                b.typ == "reflex" and b.reflekterande_byggnader[0] == egen[b.mottagare_id]
                for b in utan
            )
        )

    def test_reflexordning(self) -> None:
        bara_direkta = hitta_banor(
            self.scen, self.mottagare, self.kallor, maxradie_m=MAXRADIE_M, reflexordning=0
        )
        self.assertTrue(all(b.typ == "direkt" for b in bara_direkta))
        with self.assertRaisesRegex(BullerFel, "Reflexordning"):
            hitta_banor(
                self.scen, self.mottagare, self.kallor, maxradie_m=MAXRADIE_M, reflexordning=2
            )


class TestReflexKandaSvar(unittest.TestCase):
    """En vägg, plan mark: reflexpunkt och profil går att räkna för hand."""

    def setUp(self) -> None:
        # Hus norr om N = 60 (sydfasad längs N = 60, E 20–80), 12 m högt över plan mark på 10 m.
        self.hus = Byggnad(
            "vagg",
            np.array([20.0, 80.0, 80.0, 20.0]),
            np.array([60.0, 60.0, 70.0, 70.0]),
            22.0,
            3.0,
        )
        self.scen = Bullerscen(
            terrang_rh2000_m=np.full((100, 100), 10.0),
            markslag=np.full((100, 100), 1, dtype=np.uint8),
            e_min_m=0.0,
            n_max_m=100.0,
            cell_m=1.0,
            byggnader=(self.hus,),
        )
        self.kalla = Kallsegment(7, 30.0, 40.0, 0.5, 10.0, "v")
        self.mottagare = Mottagare(3, 60.0, 50.0, 4.0)

    def test_reflexpunkt_profil_och_forlust(self) -> None:
        uppsattning = bygg_banuppsattning(
            self.scen, [self.mottagare], [self.kalla], maxradie_m=100.0, steg_m=100.0
        )
        self.assertEqual([b.typ for b in uppsattning.banor], ["direkt", "reflex"])
        direkt, reflex = uppsattning.banor
        np.testing.assert_allclose(direkt.profil.x_m, [0.0, math.hypot(30.0, 10.0)])
        np.testing.assert_array_equal(direkt.reflexforlust_db, np.zeros(ANTAL_BAND))
        self.assertEqual(direkt.reflekterande_byggnader, ())
        # Spegelmottagaren ligger i (60, 70). Linjen från (30, 40) dit korsar N = 60 i E = 50.
        np.testing.assert_allclose(reflex.plan_e_m, [30.0, 50.0, 60.0], atol=1e-9)
        np.testing.assert_allclose(reflex.plan_n_m, [40.0, 60.0, 50.0], atol=1e-9)
        ben1, ben2 = math.hypot(20.0, 20.0), math.hypot(10.0, 10.0)
        np.testing.assert_allclose(reflex.profil.x_m, [0.0, ben1, ben1 + ben2], atol=1e-9)
        np.testing.assert_allclose(reflex.profil.z_m, [10.0, 10.0, 10.0])
        self.assertEqual((reflex.profil.kalla_hojd_m, reflex.profil.mottagare_hojd_m), (0.5, 4.0))
        np.testing.assert_array_equal(reflex.reflexforlust_db, np.full(ANTAL_BAND, 3.0))
        self.assertEqual(reflex.reflekterande_byggnader, ("vagg",))
        self.assertEqual((reflex.mottagare_id, reflex.kalla_id), (3, 7))

    def _antal_reflexer(
        self, kalla: Kallsegment, mottagare: Mottagare, radie: float = 100.0
    ) -> int:
        banor = hitta_banor(self.scen, [mottagare], [kalla], maxradie_m=radie)
        return sum(b.typ == "reflex" for b in banor)

    def test_vaggen_maste_vara_hogre_an_stralen(self) -> None:
        # Strålens höjd i reflexpunkten: källan + 2/3 av skillnaden till mottagaren.
        lag = Mottagare(3, 60.0, 50.0, 17.0)  # stråle 10,5 + 2/3 · 16,5 = 21,5 < 22
        hog = Mottagare(3, 60.0, 50.0, 18.5)  # stråle 10,5 + 2/3 · 18 = 22,5 > 22
        self.assertEqual(self._antal_reflexer(self.kalla, lag), 1)
        self.assertEqual(self._antal_reflexer(self.kalla, hog), 0)

    def test_reflexpunkten_maste_ligga_pa_vaggen(self) -> None:
        # Spegelpunkten hamnar i E = 85 respektive 79, väggen slutar i E = 80.
        utanfor = Kallsegment(7, 80.0, 40.0, 0.5, 10.0, "v")
        innanfor = Kallsegment(7, 68.0, 40.0, 0.5, 10.0, "v")
        vid = Mottagare(3, 90.0, 40.0, 4.0)
        self.assertEqual(self._antal_reflexer(utanfor, vid), 0)
        self.assertEqual(self._antal_reflexer(innanfor, vid), 1)

    def test_maxradien_galler_banans_langd(self) -> None:
        langd = math.hypot(20.0, 20.0) + math.hypot(10.0, 10.0)
        self.assertEqual(self._antal_reflexer(self.kalla, self.mottagare, langd + 0.01), 1)
        self.assertEqual(self._antal_reflexer(self.kalla, self.mottagare, langd - 0.01), 0)

    def test_ingen_reflex_fran_baksidan(self) -> None:
        bakom = Mottagare(3, 60.0, 80.0, 4.0)  # norr om huset, källan söder om
        self.assertEqual(self._antal_reflexer(self.kalla, bakom), 0)


if __name__ == "__main__":
    unittest.main()
