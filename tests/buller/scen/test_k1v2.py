"""Bullerscen ur K1 v2: samma scen som minnesadaptern, K9 ändrar bara berörda objekt, byggnad_id
följer med.

Känt svar: ett syntetiskt K1 v2-kvarter byggt med markmodellens egna byggare
(`dagsljus.exempelkvarter`,
platt mark på 0 m, rektanglar på jämna 2 m, takhöjder som är multipler av våningshöjden 3 m) ger
exakt
samma terräng, markslag, byggnadspolygoner och fasadpunkter som en scen byggd för hand i minnet.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
import shapely

from buller.scen import (
    Bullerscen,
    bullerscen_fran_k1_v2,
    fasadpunkter,
    k1v2_scen,
    mottagar_id,
    standardtabell,
)
from buller.typer import BullerFel, Byggnad
from dagsljus.exempelkvarter import bygg_rutnat, byggnads_id, rektangel, skriv_k9
from karna.geometri import las_markklasser
from karna.resultatcache import ResultatCache
from karna.rutscenario import materialisera_scenario
from karna.scenario import Andring
from karna.utredning import ScenarioSpec

E0, N0, SIDA, CELL = 150000.0, 6576000.0, 80.0, 2.0
HUS = {  # id: (x0, x1, y0, y1, höjd)
    "a": (10.0, 30.0, 10.0, 22.0, 12.0),
    "b": (44.0, 60.0, 48.0, 70.0, 24.0),
    "c": (46.0, 54.0, 6.0, 14.0, 9.0),
}
FASAD = {"avstand_fran_fasad_m": 2.0, "fasad_steg_m": 4.0, "vaningshojd_m": 3.0}


def _gammal_scen() -> Bullerscen:
    """Gamla vägen: samma geometri som minnesarrayer och `Byggnad`."""
    n = int(SIDA / CELL)
    slag = np.ones((n, n), dtype=np.uint8)
    byggnader = []
    for k, (x0, x1, y0, y1, h) in HUS.items():
        slag[int((SIDA - y1) / CELL) : int((SIDA - y0) / CELL), int(x0 / CELL) : int(x1 / CELL)] = 9
        byggnader.append(
            Byggnad(
                id=byggnads_id(k),
                e_m=E0 + np.array([x0, x1, x1, x0]),
                n_m=N0 + np.array([y0, y0, y1, y1]),
                tak_rh2000_m=h,
            )
        )
    return Bullerscen(
        terrang_rh2000_m=np.zeros((n, n)),
        markslag=slag,
        e_min_m=E0,
        n_max_m=N0 + SIDA,
        cell_m=CELL,
        byggnader=tuple(byggnader),
    )


class _Bas(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        kand = [
            rektangel(E0 + a, E0 + b, N0 + c, N0 + d, h, k) for k, (a, b, c, d, h) in HUS.items()
        ]
        cls.bas = bygg_rutnat(
            cls.tmp / "bas", kand, e_min_m=E0, n_min_m=N0, antal=2, rutstorlek_m=40.0
        )
        cls.rutor = sorted(cls.bas.glob("mm_*"))
        cls.cache = ResultatCache(cls.tmp / "cache")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def k9_rutor(self, namn: str, andringar: list[Andring]) -> list[Path]:
        k9 = skriv_k9(self.tmp / f"{namn}.json", self.bas, namn, andringar)
        return [
            r.mapp
            for r in materialisera_scenario(
                ScenarioSpec(namn, self.bas, k9=(k9,)), self.cache
            ).rutor
        ]


def _nyckel(m: object) -> tuple[object, ...]:
    return (
        round(m.e_m, 6),
        round(m.n_m, 6),
        round(m.hojd_over_mark_m, 6),
        m.vaning,  # type: ignore[attr-defined]
        round(m.fasadnormal_deg, 6),
        m.byggnad_id,
    )  # type: ignore[attr-defined]


class TestSammaSomMinnesadaptern(_Bas):
    def test_scen_lika_via_k1_v2_och_gamla_vagen(self) -> None:
        ny, id_map, varn, matv = bullerscen_fran_k1_v2(self.rutor, cell_m=CELL)
        gl = _gammal_scen()
        np.testing.assert_array_equal(ny.terrang_rh2000_m, gl.terrang_rh2000_m)
        np.testing.assert_array_equal(ny.markslag, gl.markslag)
        self.assertEqual((ny.e_min_m, ny.n_max_m, ny.cell_m), (gl.e_min_m, gl.n_max_m, gl.cell_m))
        self.assertEqual(varn, ())
        self.assertEqual(matv["antal_byggnader_scen"], 3.0)
        nb = {b.id: b for b in ny.byggnader}
        self.assertEqual(sorted(nb), sorted(b.id for b in gl.byggnader))
        for b in gl.byggnader:
            self.assertEqual(nb[b.id].tak_rh2000_m, b.tak_rh2000_m)
            p1 = shapely.Polygon(np.column_stack([nb[b.id].e_m, nb[b.id].n_m]))
            p2 = shapely.Polygon(np.column_stack([b.e_m, b.n_m]))
            self.assertTrue(p1.equals(p2), b.id)
        self.assertEqual(id_map, {k: k for k in nb})

        # härledda väggar (längd, normal) lika som mängd
        def vagg(s: Bullerscen) -> list[tuple[float, ...]]:
            v = s.vaggar
            return sorted(
                zip(
                    np.round(v.langd_m, 6),
                    np.round(v.normal_e, 6),
                    np.round(v.normal_n, 6),
                    np.round(v.tak_rh2000_m, 6),
                    strict=True,
                )
            )

        self.assertEqual(vagg(ny), vagg(gl))

    def test_fasadpunkter_k11_lika_som_bullrets_egna(self) -> None:
        k = k1v2_scen(self.rutor, cell_m=CELL, **FASAD)
        gl = _gammal_scen()
        eg = fasadpunkter(
            gl,
            avstand_fran_fasad_m=2.0,
            punktavstand_m=4.0,
            forsta_vaning_hojd_m=1.5,
            vaningshojd_m=3.0,
        )
        self.assertEqual(sorted(map(_nyckel, k.mottagare)), sorted(map(_nyckel, eg.mottagare)))
        self.assertGreater(len(k.mottagare), 40)
        self.assertEqual(k.matvarden["antal_mottagare"], float(len(k.mottagare)))
        self.assertEqual(len({m.id for m in k.mottagare}), len(k.mottagare))

    def test_byggnad_id_foljer_med_till_mottagarna(self) -> None:
        k = k1v2_scen(self.rutor, cell_m=CELL, **FASAD)
        ids = {byggnads_id(x) for x in HUS}
        self.assertEqual({m.byggnad_id for m in k.mottagare}, ids)
        by = {b.id: shapely.Polygon(np.column_stack([b.e_m, b.n_m])) for b in k.scen.byggnader}
        for m in k.mottagare:
            # närmaste byggnad är den mottagarens byggnad_id pekar på, på avståndet 2 m ut från
            # fasaden
            d = {i: p.distance(shapely.Point(m.e_m, m.n_m)) for i, p in by.items()}
            self.assertEqual(min(d, key=d.__getitem__), m.byggnad_id)
            self.assertAlmostEqual(d[m.byggnad_id], 2.0, places=6)
        # stabilt id: hash av K11:s fasadpunkt_id
        for m in k.mottagare:
            self.assertEqual(mottagar_id(k.fasadpunkt_id[m.id]), m.id)
            self.assertTrue(k.fasadpunkt_id[m.id].startswith(m.byggnad_id + ":"))

    def test_markslag_oversattas_ur_gemensamma_tabellen(self) -> None:
        t = standardtabell()
        g = las_markklasser()
        self.assertEqual(
            t.klass(1).flodesresistivitet_kNsm4,
            g.egenskaper_for(1, "buller")["flodesresistivitet_kNsm4"],
        )
        self.assertTrue(t.preliminar)


class TestK9(_Bas):
    def test_byggnad_hojd_andrar_bara_den_byggnaden(self) -> None:
        rutor = self.k9_rutor(
            "hojd",
            [Andring("byggnad_hojd", "h", None, {"byggnads_id": byggnads_id("b"), "hojd_m": 30.0})],
        )
        fore = k1v2_scen(self.rutor, cell_m=CELL, **FASAD)
        efter = k1v2_scen(rutor, cell_m=CELL, **FASAD)
        np.testing.assert_array_equal(efter.scen.terrang_rh2000_m, fore.scen.terrang_rh2000_m)
        np.testing.assert_array_equal(efter.scen.markslag, fore.scen.markslag)
        f = {b.id: b for b in fore.scen.byggnader}
        e = {b.id: b for b in efter.scen.byggnader}
        self.assertEqual(sorted(f), sorted(e))
        for i in f:
            if i == byggnads_id("b"):
                self.assertEqual(e[i].tak_rh2000_m, 30.0)
            else:
                self.assertEqual(e[i].tak_rh2000_m, f[i].tak_rh2000_m)
            np.testing.assert_array_equal(e[i].e_m, f[i].e_m)
        # mottagare: andra byggnaders punkter är identiska (samma id och läge), b får fler våningar
        mf = {m.id: m for m in fore.mottagare}
        me = {m.id: m for m in efter.mottagare}
        for i, m in mf.items():
            if m.byggnad_id != byggnads_id("b"):
                self.assertEqual(me[i], m)
        self.assertTrue(set(mf) <= set(me))  # inga punkter försvinner när huset höjs
        ny = [m for i, m in me.items() if i not in mf]
        self.assertGreater(len(ny), 0)
        self.assertTrue(all(m.byggnad_id == byggnads_id("b") for m in ny))
        self.assertEqual(max(m.vaning for m in ny), 10)  # 30 m / 3 m

    def test_byggnad_ny_ger_ett_nytt_scenobjekt_med_k9_id(self) -> None:
        from dagsljus.exempelkvarter import polygon_andring

        rutor = self.k9_rutor(
            "ny",
            [
                polygon_andring(
                    "byggnad_ny", "ny1", E0 + 62, E0 + 72, N0 + 20, N0 + 30, {"hojd_m": 15.0}
                )
            ],
        )
        fore = k1v2_scen(self.rutor, cell_m=CELL, **FASAD)
        efter = k1v2_scen(rutor, cell_m=CELL, **FASAD)
        nya = {b.id for b in efter.scen.byggnader} - {b.id for b in fore.scen.byggnader}
        self.assertEqual(len(nya), 1)
        (nid,) = nya
        self.assertEqual(
            sorted(b.id for b in fore.scen.byggnader),
            sorted(set(b.id for b in efter.scen.byggnader) - nya),
        )
        self.assertEqual(
            {m.byggnad_id for m in efter.mottagare} - {m.byggnad_id for m in fore.mottagare}, {nid}
        )
        self.assertEqual(int(np.count_nonzero(efter.scen.markslag != fore.scen.markslag)), 25)


class TestHalOchFel(_Bas):
    def test_byggnad_med_hal_ger_fel_om_inte_valt(self) -> None:
        import shapely.geometry as sg

        from markmodell.byggnadsregister import ByggnadsPost

        post = ByggnadsPost(
            byggnad_id="b_gard",
            polygon=((0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)),
            hal=(((6.0, 6.0), (6.0, 14.0), (14.0, 14.0), (14.0, 6.0)),),
            takhojd_rh2000=15.0,
            markhojd_rh2000=0.0,
            hojd_m=15.0,
            kalla="test",
        )
        from buller.scen.k1v2 import _byggnader

        with self.assertRaises(BullerFel):
            _byggnader([post], "fel")
        yttre, _, v1 = _byggnader([post], "yttre_ring")
        self.assertEqual(len(yttre), 1)
        self.assertEqual(len(v1), 1)
        delar, id_map, v2 = _byggnader([post], "dela")
        pol = [shapely.Polygon(np.column_stack([b.e_m, b.n_m])) for b in delar]
        facit = sg.Polygon(post.polygon, post.hal)
        self.assertAlmostEqual(sum(p.area for p in pol), facit.area, places=9)  # arean bevaras
        self.assertAlmostEqual(
            shapely.union_all(pol).symmetric_difference(facit).area, 0.0, places=9
        )
        self.assertEqual(set(id_map.values()), {"b_gard"})
        self.assertEqual(len(v2), 1)
        self.assertGreater(len(delar), 1)

    def test_okand_cellstorlek_och_v1_ger_buller_fel(self) -> None:
        with self.assertRaises(BullerFel):
            bullerscen_fran_k1_v2(self.rutor, cell_m=0.6)
        v1 = self.tmp / "v1" / "mm_0_0"
        shutil.copytree(self.rutor[0], v1)
        (v1 / "byggnader.gpkg").unlink()
        with self.assertRaises(BullerFel):
            bullerscen_fran_k1_v2([v1], cell_m=CELL)

    def test_avstand_ska_vara_positivt(self) -> None:
        with self.assertRaises(BullerFel):
            k1v2_scen(
                self.rutor,
                cell_m=CELL,
                avstand_fran_fasad_m=0.0,
                fasad_steg_m=4.0,
                vaningshojd_m=3.0,
            )


if __name__ == "__main__":
    unittest.main()
