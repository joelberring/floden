"""Tester för den gemensamma kärnan (T20): K20-frågor, nycklar, cache, lappar och jämförelser."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from gemensamt.hash import sha256_fil
from karna.analyslosare import (
    Analyslosare,
    LosarFel,
    LosarKontext,
    LosarUtdata,
    hamta_losare,
    registrera_losare,
)
from karna.hashning import HashFel as KanoniskFel
from karna.hashning import nyckel_for as kanonisk_hash
from karna.korplan import kor_utredning as kor_fraga
from karna.korplan import planera
from karna.resultatcache import ResultatCache
from karna.utredning import FragaFel, fraga_fran_dict
from lapp.kontrakt import HojdYta, ProjektLapp, skriv_lapp_k3
from testsvit.k1_io import las_geotiff, skriv_geotiff

REPO = Path(__file__).resolve().parents[2]
A1 = REPO / "testsvit" / "A1_enkel_sanka" / "indata"


def _skriv_ruta(mapp: Path, x_min: float, y_max: float, n: int = 40, lutning: float = 0.0) -> None:
    """Syntetisk K1-ruta med n×n celler à 0,25 m och ett lutande plan."""
    mapp.mkdir(parents=True)
    kol = np.arange(n, dtype=np.float64)
    z = 10.0 + lutning * (x_min + 0.25 * kol)[None, :] * np.ones((n, 1)) - lutning * x_min
    kw = {"x_min": x_min, "y_max": y_max, "pixelstorlek": 0.25}
    skriv_geotiff(mapp / "mark.tif", np.asarray(z, dtype=np.float32), **kw)
    skriv_geotiff(mapp / "osakerhet.tif", np.full((n, n), 0.05, dtype=np.float32), **kw)
    skriv_geotiff(mapp / "markslag.tif", np.full((n, n), 4, dtype=np.uint8), **kw)
    (mapp / "recept.json").write_text(json.dumps({"ruta_id": mapp.name}), encoding="utf-8")


class Raknare(Analyslosare):
    """Testlösare som räknar körningar och läser sitt beroende."""

    namn: ClassVar[str] = "ovrigt.raknare"
    doman: ClassVar[str] = "ovrigt"
    version: ClassVar[str] = "1"
    utdataroller: ClassVar[tuple[str, ...]] = ("summa",)
    korningar: ClassVar[int] = 0

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        return {"faktor": float(parametrar.get("faktor", 1.0))}

    def paverkansradie_m(self, parametrar: Mapping[str, Any]) -> float | None:
        return 0.0

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        Raknare.korningar += 1
        assert kontext.ruta is not None
        lut = kontext.beroenden["lut"].per_ruta[kontext.ruta.ruta_id] / "lutning.tif"
        g, _ = las_geotiff(lut)
        summa = float(np.sum(g)) * float(kontext.parametrar["faktor"])
        (kontext.utmapp / "summa.json").write_text(json.dumps({"summa": summa}))
        return LosarUtdata(filer={"summa": "summa.json"}, matvarden={"summa": summa})


class Trasig(Analyslosare):
    namn: ClassVar[str] = "ovrigt.trasig"
    doman: ClassVar[str] = "ovrigt"
    version: ClassVar[str] = "1"

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        (kontext.utmapp / "halv.txt").write_text("halvfärdig")
        raise RuntimeError("avsiktligt fel")


registrera_losare(Raknare())
registrera_losare(Trasig())


class TestKanonisk(unittest.TestCase):
    def test_ordning_och_heltal(self) -> None:
        self.assertEqual(kanonisk_hash({"a": 1, "b": 2.0}), kanonisk_hash({"b": 2.0, "a": 1}))
        self.assertNotEqual(kanonisk_hash({"a": 1}), kanonisk_hash({"a": 1.5}))

    def test_nan_ger_fel(self) -> None:
        with self.assertRaises(KanoniskFel):
            kanonisk_hash({"a": float("nan")})


class TestFraga(unittest.TestCase):
    def _bas(self) -> dict[str, Any]:
        return {
            "kontrakt": "K20/v1",
            "fraga_id": "t",
            "scenarier": {"fore": {"bas": str(A1)}},
            "analyser": [{"id": "a", "losare": "geometri.lutning"}],
        }

    def test_okant_falt_ger_fel(self) -> None:
        d = self._bas()
        d["analyser"][0]["parameter"] = {}
        with self.assertRaisesRegex(FragaFel, "okända fält"):
            fraga_fran_dict(d, REPO)

    def test_cykel_ger_fel(self) -> None:
        d = self._bas()
        d["analyser"] = [
            {"id": "a", "losare": "geometri.lutning", "beror_pa": ["b"]},
            {"id": "b", "losare": "geometri.lutning", "beror_pa": ["a"]},
        ]
        with self.assertRaisesRegex(FragaFel, "Cykliskt"):
            fraga_fran_dict(d, REPO)

    def test_okant_scenario_ger_fel(self) -> None:
        d = self._bas()
        d["analyser"][0]["scenarier"] = ["finns_inte"]
        with self.assertRaisesRegex(FragaFel, "okänt scenario"):
            fraga_fran_dict(d, REPO)

    def test_okand_losare_ger_fel(self) -> None:
        with self.assertRaisesRegex(LosarFel, "Okänd lösare"):
            hamta_losare("dagsljus.finns_inte")


class TestSkyfallMedLapp(unittest.TestCase):
    """A1 (känt svar) före och efter en lapp, med cache och jämförelse."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.cache = ResultatCache(self.tmp / "cache")
        _, geo = las_geotiff(A1 / "mark.tif")
        lapp = ProjektLapp(
            lapp_id="fyll-halva-sankan",
            bas_recept=(sha256_fil(A1 / "recept.json"),),
            hojdytor=(
                HojdYta(
                    yta_id="uppfyllnad",
                    polygon=(
                        (geo.x_min + 2.5, geo.y_max - 12.5),
                        (geo.x_min + 7.5, geo.y_max - 12.5),
                        (geo.x_min + 7.5, geo.y_max - 2.5),
                        (geo.x_min + 2.5, geo.y_max - 2.5),
                    ),
                    hojd_rh2000=10.6,
                ),
            ),
        )
        self.lapp_fil = skriv_lapp_k3(lapp, self.tmp / "lapp.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fraga(self, regn_mm: float = 100.0) -> Any:
        return fraga_fran_dict(
            {
                "kontrakt": "K20/v1",
                "fraga_id": "a1-lapp",
                "scenarier": {
                    "fore": {"bas": str(A1)},
                    "efter": {"bas": str(A1), "lappar": [str(self.lapp_fil)]},
                },
                "analyser": [
                    {
                        "id": "skyfall",
                        "losare": "vatten.statisk_fyllning",
                        "parametrar": {"regn_mm": regn_mm},
                    }
                ],
                "jamforelser": [
                    {
                        "analys": "skyfall",
                        "utdata": "max_djup",
                        "fore": "fore",
                        "efter": "efter",
                        "troskel": 0.01,
                    }
                ],
            },
            REPO,
        )

    def test_kant_svar_cache_och_jamforelse(self) -> None:
        res = kor_fraga(self._fraga(), self.cache, self.tmp / "ut")
        jobb = {j["scenario"]: j for j in res.sammanstallning["jobb"]}
        # A1: 100 mm över avrinningsytan 222,5 m² = 22,25 m³ i en sänka på 100 m² -> 0,2225 m.
        self.assertAlmostEqual(jobb["fore"]["matvarden"]["max_djup_m"], 0.2225, delta=0.01)
        self.assertLess(abs(jobb["fore"]["matvarden"]["volymfel_rel"]), 1e-3)
        self.assertFalse(jobb["fore"]["fran_cache"])
        jf = res.sammanstallning["jamforelser"][0]
        self.assertGreater(jf["max_okning"], 0.05)  # halva sänkan fylld -> djupare vatten
        self.assertGreater(jf["yta_okning_m2"], 0.0)
        self.assertTrue((self.tmp / "ut" / "skyfall" / "fore" / "max_djup.tif").is_file())
        self.assertTrue((self.tmp / "ut" / "run.json").is_file())

        igen = kor_fraga(self._fraga(), self.cache, self.tmp / "ut2")
        self.assertEqual(igen.sammanstallning["sammanfattning"]["att_kora"], 0)
        self.assertEqual(
            res.sammanstallning["jamforelser"][0]["max_okning"],
            igen.sammanstallning["jamforelser"][0]["max_okning"],
        )

    def test_ny_parameter_ger_ny_nyckel(self) -> None:
        kor_fraga(self._fraga(100.0), self.cache, self.tmp / "ut")
        plan = planera(self._fraga(120.0), self.cache)
        self.assertEqual(plan.sammanfattning()["att_kora"], 2)
        plan_samma = planera(self._fraga(100.0), self.cache)
        self.assertEqual(plan_samma.sammanfattning()["att_kora"], 0)

    def test_lapp_andrar_scenariots_nyckel(self) -> None:
        plan = planera(self._fraga(), self.cache)
        self.assertNotEqual(plan.scenarier["fore"].nyckel, plan.scenarier["efter"].nyckel)


class TestLokalOmrakning(unittest.TestCase):
    """Tre rutor; en lapp i en ruta ska bara räkna om den rutan (och dess beroenden)."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.cache = ResultatCache(self.tmp / "cache")
        self.bas = self.tmp / "bas"
        self.bas.mkdir()
        for i in range(3):
            x0 = 150000.0 + 10.0 * i
            _skriv_ruta(self.bas / f"mm_{int(x0)}_6576000", x0, 6576010.0, lutning=0.1)
        lapp = ProjektLapp(
            lapp_id="platta-i-ruta-0",
            bas_recept=("syntetisk",),
            hojdytor=(
                HojdYta(
                    yta_id="platta",
                    polygon=(
                        (150002.0, 6576002.0),
                        (150006.0, 6576002.0),
                        (150006.0, 6576006.0),
                        (150002.0, 6576006.0),
                    ),
                    hojd_rh2000=12.0,
                ),
            ),
        )
        self.lapp_fil = skriv_lapp_k3(lapp, self.tmp / "lapp.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fraga(self, scen: dict[str, Any], faktor: float = 1.0) -> Any:
        return fraga_fran_dict(
            {
                "kontrakt": "K20/v1",
                "fraga_id": "lokal",
                "scenarier": scen,
                "analyser": [
                    {"id": "lut", "losare": "geometri.lutning"},
                    {
                        "id": "summa",
                        "losare": "ovrigt.raknare",
                        "beror_pa": ["lut"],
                        "parametrar": {"faktor": faktor},
                    },
                ],
            },
            REPO,
        )

    def test_bara_berord_ruta_raknas_om(self) -> None:
        fore = {"fore": {"bas": str(self.bas)}}
        res = kor_fraga(self._fraga(fore), self.cache, self.tmp / "ut")
        self.assertEqual(res.sammanstallning["sammanfattning"]["att_kora"], 6)

        bada = {**fore, "efter": {"bas": str(self.bas), "lappar": [str(self.lapp_fil)]}}
        plan = planera(self._fraga(bada), self.cache)
        att_kora = sorted(j.etikett for j in plan.jobb if not j.i_cache)
        self.assertEqual(
            att_kora, ["lut/efter/mm_150000_6576000", "summa/efter/mm_150000_6576000"]
        )
        fore_r = plan.scenarier["fore"].rutor
        efter_r = plan.scenarier["efter"].rutor
        self.assertEqual([r.hash for r in fore_r[1:]], [r.hash for r in efter_r[1:]])
        self.assertNotEqual(fore_r[0].hash, efter_r[0].hash)

        fore_antal = Raknare.korningar
        kor_fraga(self._fraga(bada), self.cache, self.tmp / "ut2", plan=plan)
        self.assertEqual(Raknare.korningar, fore_antal + 1)

    def test_parameter_i_beroende_led(self) -> None:
        fore = {"fore": {"bas": str(self.bas)}}
        kor_fraga(self._fraga(fore), self.cache, self.tmp / "ut")
        plan = planera(self._fraga(fore, faktor=2.0), self.cache)
        att_kora = sorted(j.analys.id for j in plan.jobb if not j.i_cache)
        self.assertEqual(att_kora, ["summa", "summa", "summa"])


class TestRegressioner(unittest.TestCase):
    """Fel som hittades vid granskning 2026-11-21."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.cache = ResultatCache(self.tmp / "cache")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_landxml_andring_ger_ny_scenarionyckel(self) -> None:
        _, geo = las_geotiff(A1 / "mark.tif")
        xml = self.tmp / "yta.xml"

        def skriv(z: float) -> None:
            e0, n0, e1, n1 = geo.x_min + 1, geo.y_min + 1, geo.x_max - 1, geo.y_max - 1
            xml.write_text(
                f"""<LandXML><Surfaces><Surface name="s"><Definition surfType="TIN"><Pnts>
<P id="1">{n0} {e0} {z}</P><P id="2">{n0} {e1} {z}</P><P id="3">{n1} {e1} {z}</P>
<P id="4">{n1} {e0} {z}</P></Pnts><Faces><F>1 2 3</F><F>1 3 4</F></Faces>
</Definition></Surface></Surfaces></LandXML>""",
                encoding="utf-8",
            )

        skriv(10.6)
        lapp = ProjektLapp(
            lapp_id="tin", bas_recept=("a",), hojdytor=(HojdYta(yta_id="t", landxml="yta.xml"),)
        )
        lf = skriv_lapp_k3(lapp, self.tmp / "lapp.json")  # relativ LandXML-sökväg
        d = {
            "kontrakt": "K20/v1",
            "fraga_id": "q",
            "scenarier": {"e": {"bas": str(A1), "lappar": [str(lf)]}},
            "analyser": [{"id": "lut", "losare": "geometri.lutning"}],
        }
        forsta = planera(fraga_fran_dict(d, REPO), self.cache).scenarier["e"].nyckel
        skriv(5.0)
        andra = planera(fraga_fran_dict(d, REPO), self.cache).scenarier["e"].nyckel
        self.assertNotEqual(forsta, andra)

    def test_overlapp_raknas_en_gang(self) -> None:
        bas = self.tmp / "bas"
        bas.mkdir()
        _skriv_ruta(bas / "mm_150000_6576000", 150000.0, 6576010.0)  # x 150000–150010
        _skriv_ruta(bas / "mm_150005_6576000", 150005.0, 6576010.0)  # 5 m överlapp
        lapp = ProjektLapp(
            lapp_id="p",
            bas_recept=("s",),
            hojdytor=(
                HojdYta(
                    yta_id="h",
                    polygon=(
                        (150006.0, 6576003.0),
                        (150009.0, 6576003.0),
                        (150009.0, 6576007.0),
                        (150006.0, 6576007.0),
                    ),
                    hojd_rh2000=12.0,
                ),
            ),
        )
        lf = skriv_lapp_k3(lapp, self.tmp / "l.json")
        d = {
            "kontrakt": "K20/v1",
            "fraga_id": "o",
            "scenarier": {"f": {"bas": str(bas)}, "e": {"bas": str(bas), "lappar": [str(lf)]}},
            "analyser": [{"id": "lut", "losare": "geometri.lutning"}],
            "jamforelser": [
                {"analys": "lut", "utdata": "lutning", "fore": "f", "efter": "e", "troskel": 1.0}
            ],
        }
        jf = kor_fraga(fraga_fran_dict(d, REPO), self.cache, self.tmp / "u").sammanstallning[
            "jamforelser"
        ][0]
        # Facit: samma fråga med bara den ena rutan, som ensam ser hela förändringen.
        en = self.tmp / "en"
        en.mkdir()
        shutil.copytree(bas / "mm_150000_6576000", en / "mm_150000_6576000")
        d_en = json.loads(json.dumps(d).replace(str(bas), str(en)))
        d_en["fraga_id"] = "o-en"
        facit = kor_fraga(fraga_fran_dict(d_en, REPO), self.cache, self.tmp / "u_en")
        facit_m2 = facit.sammanstallning["jamforelser"][0]["yta_okning_m2"]
        self.assertGreater(facit_m2, 0.0)
        # Förändringen ligger helt i överlappet och syns i båda rutorna, men räknas en gång.
        self.assertAlmostEqual(jf["yta_okning_m2"], facit_m2, places=6)

    def test_losaren_far_kanoniska_parametrar(self) -> None:
        sett: list[Any] = []

        class Typ(Analyslosare):
            namn: ClassVar[str] = "ovrigt.typ"
            doman: ClassVar[str] = "ovrigt"
            version: ClassVar[str] = "1"

            def validera_parametrar(self, p: Mapping[str, Any]) -> dict[str, Any]:
                return {"n": p["n"]}

            def kor(self, k: LosarKontext) -> LosarUtdata:
                sett.append(k.parametrar["n"])
                (k.utmapp / "o.txt").write_text(repr(k.parametrar["n"]))
                return LosarUtdata(filer={"o": "o.txt"})

        registrera_losare(Typ(), ersatt=True)
        for n in (3.0, 3):
            d = {
                "kontrakt": "K20/v1",
                "fraga_id": "t",
                "scenarier": {"f": {"bas": str(A1)}},
                "analyser": [{"id": "a", "losare": "ovrigt.typ", "parametrar": {"n": n}}],
            }
            kor_fraga(fraga_fran_dict(d, REPO), self.cache, self.tmp / f"u{n}")
        # Lösaren får exakt det som hashades: 3.0 och 3 är olika nycklar och olika indata.
        self.assertEqual([type(x) for x in sett], [float, int])

    def test_dubbla_scenarier_ger_fel(self) -> None:
        d = {
            "kontrakt": "K20/v1",
            "fraga_id": "t",
            "scenarier": {"f": {"bas": str(A1)}},
            "analyser": [{"id": "a", "losare": "geometri.lutning", "scenarier": ["f", "f"]}],
        }
        with self.assertRaisesRegex(FragaFel, "flera gånger"):
            fraga_fran_dict(d, REPO)

    def test_samtidiga_skrivningar(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        nyckel = "ab" * 32

        def skriv(_: int) -> Path:
            def fyll(tmp: Path) -> None:
                (tmp / "x.txt").write_text("x")

            return self.cache.skapa("jobb", nyckel, fyll)

        with ThreadPoolExecutor(max_workers=16) as pool:
            svar = list(pool.map(skriv, range(200)))
        self.assertEqual({str(p) for p in svar}, {str(self.cache.jobbmapp(nyckel))})
        self.assertTrue(self.cache.finns("jobb", nyckel))

    def test_utmapp_som_inte_ar_resultat_raderas_inte(self) -> None:
        ut = self.tmp / "viktig"
        ut.mkdir()
        (ut / "data.txt").write_text("behåll")
        d = {
            "kontrakt": "K20/v1",
            "fraga_id": "t",
            "scenarier": {"f": {"bas": str(A1)}},
            "analyser": [{"id": "a", "losare": "geometri.lutning"}],
        }
        with self.assertRaises(LosarFel):
            kor_fraga(fraga_fran_dict(d, REPO), self.cache, ut)
        self.assertTrue((ut / "data.txt").is_file())


class TestK9Scenario(unittest.TestCase):
    """K9-planscenarier (fas0-2) körs av T20-motorn via översättning till K3."""

    def setUp(self) -> None:
        from karna.scenario import Andring, Planscenario, skriv_scenario

        self.tmp = Path(tempfile.mkdtemp())
        self.cache = ResultatCache(self.tmp / "cache")
        _, geo = las_geotiff(A1 / "mark.tif")
        self.geo = geo
        ring = [
            [geo.x_min + 2.5, geo.y_max - 12.5],
            [geo.x_min + 7.5, geo.y_max - 12.5],
            [geo.x_min + 7.5, geo.y_max - 2.5],
            [geo.x_min + 2.5, geo.y_max - 2.5],
            [geo.x_min + 2.5, geo.y_max - 12.5],
        ]
        bas = (sha256_fil(A1 / "recept.json"),)
        self.alt_a = Planscenario("alt-a", bas, ())
        self.alt_b = self.alt_a.gren(
            "alt-b",
            [
                Andring(
                    "markhojd",
                    "uppfyllnad",
                    {"type": "Polygon", "coordinates": [ring]},
                    {"niva_rh2000": 10.6},
                )
            ],
        )
        self.fa = skriv_scenario(self.alt_a, self.tmp / "alt_a.json")
        self.fb = skriv_scenario(self.alt_b, self.tmp / "alt_b.json")
        self.ring = ring
        self.bas = bas

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fraga(self, scen: dict[str, Any]) -> Any:
        return fraga_fran_dict(
            {
                "kontrakt": "K20/v1",
                "fraga_id": "k9",
                "scenarier": scen,
                "analyser": [
                    {
                        "id": "skyfall",
                        "losare": "vatten.statisk_fyllning",
                        "parametrar": {"regn_mm": 100.0},
                    }
                ],
            },
            REPO,
        )

    def test_k9_ger_samma_svar_som_motsvarande_k3(self) -> None:
        lapp = ProjektLapp(
            lapp_id="k3",
            bas_recept=self.bas,
            hojdytor=(
                HojdYta(
                    yta_id="uppfyllnad",
                    polygon=tuple((p[0], p[1]) for p in self.ring[:-1]),
                    hojd_rh2000=10.6,
                ),
            ),
        )
        lf = skriv_lapp_k3(lapp, self.tmp / "k3.json")
        res = kor_fraga(
            self._fraga(
                {
                    "fore": {"bas": str(A1), "k9": [str(self.fa)]},
                    "k9": {"bas": str(A1), "k9": [str(self.fa), str(self.fb)]},
                    "k3": {"bas": str(A1), "lappar": [str(lf)]},
                }
            ),
            self.cache,
            self.tmp / "ut",
        )
        djup = {j["scenario"]: j["matvarden"]["max_djup_m"] for j in res.sammanstallning["jobb"]}
        self.assertAlmostEqual(djup["fore"], 0.2214, delta=0.001)  # tomt K9 = basen
        self.assertAlmostEqual(djup["k9"], djup["k3"], places=9)
        self.assertAlmostEqual(djup["k9"], 0.4428, delta=0.001)
        # Tomt K9-scenario ändrar inga rutor: samma nyckel som basen.
        bara_bas = planera(self._fraga({"fore": {"bas": str(A1)}}), self.cache)
        self.assertEqual(bara_bas.sammanfattning()["att_kora"], 0)

    def test_fel_bas_ger_fel(self) -> None:
        from karna.scenario import Planscenario, skriv_scenario

        fel = skriv_scenario(Planscenario("x", ("0" * 64,), ()), self.tmp / "fel.json")
        with self.assertRaisesRegex(Exception, "recept som inte finns i basen"):
            planera(self._fraga({"x": {"bas": str(A1), "k9": [str(fel)]}}), self.cache)

    def test_ostodd_andringstyp_ger_fel(self) -> None:
        from karna.scenario import Andring, skriv_scenario

        veg = self.alt_a.gren(
            "veg",
            [
                Andring(
                    "vegetation",
                    "trad",
                    {"type": "Polygon", "coordinates": [self.ring]},
                    {"hojd_m": 8.0},
                )
            ],
        )
        fv = skriv_scenario(veg, self.tmp / "veg.json")
        with self.assertRaisesRegex(Exception, "vegetation"):
            planera(
                self._fraga({"v": {"bas": str(A1), "k9": [str(self.fa), str(fv)]}}), self.cache
            )


class TestAtomaritet(unittest.TestCase):
    def test_fel_lamnar_ingen_cachepost(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            cache = ResultatCache(tmp / "cache")
            fraga = fraga_fran_dict(
                {
                    "kontrakt": "K20/v1",
                    "fraga_id": "trasig",
                    "scenarier": {"fore": {"bas": str(A1)}},
                    "analyser": [{"id": "x", "losare": "ovrigt.trasig"}],
                },
                REPO,
            )
            with self.assertRaises(RuntimeError):
                kor_fraga(fraga, cache, tmp / "ut")
            self.assertEqual(planera(fraga, cache).sammanfattning()["i_cache"], 0)
            self.assertEqual(list((tmp / "cache").rglob(".klar")), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_dubbelregistrering_ger_fel(self) -> None:
        class Annan(Raknare):
            pass

        with self.assertRaisesRegex(LosarFel, "redan registrerad"):
            registrera_losare(Annan())


if __name__ == "__main__":
    unittest.main()
