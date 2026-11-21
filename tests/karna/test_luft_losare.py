"""Luftlösarna på K1 v2-rutor via K20: före/efter, jämförelse, och samma resultat som gamla vägen.

Litet kvarter (2 x 2 rutor om 40 m, 4 m celler, 2 vindriktningar, kort LES): testerna kontrollerar
kedjan och identiteten mot luftens egen kedja (`bygg_vindbibliotek` på ett handbyggt höjdraster),
inte fysikens noggrannhet (det gör `tests/luft/`).
"""

from __future__ import annotations

import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

import numpy as np

from dagsljus.exempelkvarter import bygg_rutnat, byggnads_id, rektangel, skriv_k9
from karna.analyslosare import LosarFel, hamta_losare
from karna.korplan import kor_utredning
from karna.resultatcache import ResultatCache
from karna.scenario import Andring
from karna.utredning import fraga_fran_dict
from testsvit.k1_io import las_geotiff

REPO = Path(__file__).resolve().parents[2]
E0, N0, SIDA = 150000.0, 6576000.0, 80.0
HUS = {"a": (8.0, 28.0, 8.0, 20.0, 12.0), "b": (44.0, 60.0, 48.0, 68.0, 20.0)}  # jämna 4 m
PAR: dict[str, Any] = {
    "dx_m": 4.0,
    "riktningar": 2,
    "genomstromningar": 0.3,
    "buffert_uppstroms_m": 40.0,
    "buffert_nedstroms_m": 80.0,
    "buffert_sidor_m": 30.0,
}


def _fraga(
    tmp: Path,
    bas: Path,
    k9: Path | None,
    analyser: list[dict[str, Any]],
    jamfor: list[dict[str, Any]] | None = None,
) -> Any:
    sc: dict[str, Any] = {"fore": {"bas": str(bas)}}
    if k9 is not None:
        sc["efter"] = {"bas": str(bas), "k9": [str(k9)]}
    d: dict[str, Any] = {
        "kontrakt": "K20/v1",
        "fraga_id": "luft-test",
        "scenarier": sc,
        "analyser": analyser,
    }
    if jamfor:
        d["jamforelser"] = jamfor
    return fraga_fran_dict(d, REPO)


class TestLuftLosare(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        kand = [
            rektangel(E0 + a, E0 + b, N0 + c, N0 + d, h, k) for k, (a, b, c, d, h) in HUS.items()
        ]
        cls.bas = bygg_rutnat(
            cls.tmp / "bas", kand, e_min_m=E0, n_min_m=N0, antal=2, rutstorlek_m=40.0
        )
        cls.k9 = skriv_k9(
            cls.tmp / "hojd.json",
            cls.bas,
            "hojd",
            [Andring("byggnad_hojd", "h", None, {"byggnads_id": byggnads_id("b"), "hojd_m": 28.0})],
        )
        cls.vader = cls.tmp / "vader.csv"
        rng = np.random.default_rng(1)
        with cls.vader.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["tid", "vindriktning_grad", "vindhastighet_m_s"])
            for i in range(400):
                w.writerow([i, float(rng.choice([0.0, 180.0])), float(rng.weibull(2.0) * 6.0)])
        an = [
            {
                "id": "komfort",
                "losare": "luft.vindkomfort",
                "parametrar": PAR,
                "indata": {"vader": str(cls.vader)},
            }
        ]
        cls.res = kor_utredning(
            _fraga(
                cls.tmp,
                cls.bas,
                cls.k9,
                an,
                [{"analys": "komfort", "utdata": "forstarkning", "fore": "fore", "efter": "efter"}],
            ),
            ResultatCache(cls.tmp / "cache"),
            cls.tmp / "ut",
        )
        cls.sam = json.loads((cls.tmp / "ut" / "sammanstallning.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _tif(self, scen: str, fil: str) -> np.ndarray[Any, Any]:
        a, _ = las_geotiff(self.tmp / "ut" / "komfort" / scen / fil)
        return np.asarray(a, dtype=np.float32)

    def test_registrerad_globalt_och_med_alla_utdata(self) -> None:
        losare = hamta_losare("luft.vindkomfort")
        self.assertIsNone(losare.paverkansradie_m(losare.validera_parametrar({})))
        jobb = [j for j in self.sam["jobb"] if j["analys"] == "komfort"]
        self.assertEqual(sorted(j["scenario"] for j in jobb), ["efter", "fore"])
        self.assertTrue(all(j["ruta"] is None for j in jobb))  # ett globalt jobb per scenario
        for j in jobb:
            self.assertEqual(j["matvarden"]["antal_rutor"], 4.0)
            self.assertEqual(j["matvarden"]["saknade_celler"], 0.0)
        self.assertEqual(jobb[0]["matvarden"]["hogsta_byggnad_m"] in (20.0, 28.0), True)

    def test_k9_hojd_ger_ny_nyckel_och_andrat_falt_med_jamforelse(self) -> None:
        j = {x["scenario"]: x for x in self.sam["jobb"]}
        self.assertNotEqual(j["fore"]["nyckel"], j["efter"]["nyckel"])
        self.assertEqual(j["efter"]["matvarden"]["hogsta_byggnad_m"], 28.0)
        self.assertEqual(j["fore"]["matvarden"]["hogsta_byggnad_m"], 20.0)
        jf = self.sam["jamforelser"][0]
        self.assertGreater(jf["max_okning"] + jf["max_minskning"], 0.0)
        diff = self._tif("efter", "forstarkning.tif") - self._tif("fore", "forstarkning.tif")
        self.assertGreater(float(np.nanmax(np.abs(diff))), 0.0)
        self.assertTrue((self.tmp / "ut" / "jamforelser" / jf["namn"] / "diff.tif").is_file())

    def test_samma_resultat_som_gamla_vagen(self) -> None:
        """K1 v2 → lösaren ger exakt samma rastret som luftens egen kedja på ett handbyggt
        raster."""
        from luft.export import skriv_geotiff_float
        from luft.kedja import Domanspec, bygg_vindbibliotek
        from luft.lbm import LbmParametrar
        from luft.typer import Inflode
        from luft.vindbibliotek import Vaderserie
        from luft.vindkomfort import berakna_komfort

        n = int(SIDA / 4.0)
        h = np.zeros((n, n))
        for x0, x1, y0, y1, hojd in HUS.values():
            h[int((SIDA - y1) / 4) : int((SIDA - y0) / 4), int(x0 / 4) : int(x1 / 4)] = hojd
        # hojd_doman_m: lösarens standard 2 x högsta byggnad avrundat uppåt till hel cell
        spec = Domanspec(h, 4.0, E0, N0 + SIDA, 4.0, 40.0, 40.0, 80.0, 30.0)
        g = 0.3
        bib = bygg_vindbibliotek(
            spec,
            [0.0, 180.0],
            Inflode(5.0, 10.0, 0.5),
            LbmParametrar(upprampning_genomstromningar=g, medel_genomstromningar=g),
        )
        res = berakna_komfort(bib, Vaderserie.las_csv(self.vader), spec.utbredning, 4.0, "nen8100")
        forst = np.mean(np.stack(res.forstarkning_per_riktning, axis=0), axis=0)
        facit = self.tmp / "facit.tif"
        skriv_geotiff_float(facit, forst, res.ruta)
        a, _ = las_geotiff(facit)
        np.testing.assert_array_equal(
            np.asarray(a, dtype=np.float32), self._tif("fore", "forstarkning.tif")
        )

    def test_okanda_parametrar_ger_fel(self) -> None:
        losare = hamta_losare("luft.vindkomfort")
        for fel in (
            {"dx": 4.0},
            {"riktningar": 0},
            {"omsampling": "punkt"},
            {"vegetation": "ja"},
            {"kriterium": "annat"},
            {"dx_m": -1.0},
        ):
            with self.subTest(fel=fel), self.assertRaises(LosarFel):
                losare.validera_parametrar(fel)

    def test_oforandrat_scenario_aterbruk_ur_cache(self) -> None:
        cache = ResultatCache(self.tmp / "cache")
        an = [
            {
                "id": "komfort",
                "losare": "luft.vindkomfort",
                "parametrar": PAR,
                "indata": {"vader": str(self.vader)},
            }
        ]
        kor_utredning(_fraga(self.tmp, self.bas, None, an), cache, self.tmp / "ut2")
        sam = json.loads((self.tmp / "ut2" / "sammanstallning.json").read_text(encoding="utf-8"))
        self.assertTrue(all(x["fran_cache"] for x in sam["jobb"] if x["analys"] == "komfort"))


class TestLuftHalt(unittest.TestCase):
    def test_halt_pa_k1_v2_andras_av_nytt_hus(self) -> None:
        import geopandas as gpd
        from shapely.geometry import LineString

        tmp = Path(tempfile.mkdtemp())
        try:
            kand = [
                rektangel(E0 + a, E0 + b, N0 + c, N0 + d, h, k)
                for k, (a, b, c, d, h) in HUS.items()
            ]
            bas = bygg_rutnat(tmp / "bas", kand, e_min_m=E0, n_min_m=N0, antal=2, rutstorlek_m=40.0)
            k9 = skriv_k9(
                tmp / "hojd.json",
                bas,
                "hojd",
                [
                    Andring(
                        "byggnad_hojd", "h", None, {"byggnads_id": byggnads_id("b"), "hojd_m": 28.0}
                    )
                ],
            )
            vagar = tmp / "vagar.gpkg"
            gpd.GeoDataFrame(
                {"emission_g_m_s": [1.4e-4]},
                geometry=[LineString([(E0, N0 + 36), (E0 + 80, N0 + 36)])],
                crs="EPSG:3011",
            ).to_file(vagar, driver="GPKG")
            vader = tmp / "vader.csv"
            with vader.open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["tid", "vindriktning_grad", "vindhastighet_m_s"])
                for i in range(100):
                    w.writerow([i, 0.0 if i % 2 else 180.0, 4.0])
            an = [
                {
                    "id": "halt",
                    "losare": "luft.halt",
                    "parametrar": PAR,
                    "indata": {"vagar": str(vagar), "vader": str(vader)},
                }
            ]
            kor_utredning(_fraga(tmp, bas, k9, an), ResultatCache(tmp / "cache"), tmp / "ut")
            fore, _ = las_geotiff(tmp / "ut" / "halt" / "fore" / "arsmedel.tif")
            efter, _ = las_geotiff(tmp / "ut" / "halt" / "efter" / "arsmedel.tif")
            self.assertGreater(float(np.nanmax(fore)), 0.0)
            self.assertTrue(np.isfinite(np.nanmax(efter)))
            self.assertGreater(float(np.nanmax(np.abs(np.asarray(efter) - np.asarray(fore)))), 0.0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestExempelfilen(unittest.TestCase):
    def test_exempelfilen_planerar_globalt_mot_demokvarteret(self) -> None:
        """Exempelfrågan luft_fore_efter.toml laddas och planeras: ett globalt jobb per scenario."""
        from dagsljus.exempelkvarter import bygg_demokvarter
        from karna.korplan import planera
        from karna.utredning import las_fraga

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "exempel" / "fragor").mkdir(parents=True)
        for namn in ("luft_fore_efter.toml", "luft_vader_testvarde.csv"):
            shutil.copy(REPO / "exempel" / "fragor" / namn, tmp / "exempel" / "fragor" / namn)
        bygg_demokvarter(tmp / "data" / "cache" / "exempel" / "dagsljus-kvarter")
        fraga = las_fraga(tmp / "exempel" / "fragor" / "luft_fore_efter.toml")
        plan = planera(fraga, ResultatCache(tmp / "cache"))
        self.assertEqual(len(plan.jobb), 2)  # 2 scenarier x 1 globalt jobb
        self.assertTrue(all(j.ruta is None for j in plan.jobb))
        self.assertNotEqual(plan.jobb[0].nyckel, plan.jobb[1].nyckel)


if __name__ == "__main__":
    unittest.main()
