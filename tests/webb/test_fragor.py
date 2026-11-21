"""Webb-API för utredningsfrågor (K20): lista, planera, köra och läsa resultat.

Körs mot små syntetiska K1 v2-kvarter (dagsljus.exempelkvarter, 2 x 2 rutor om 40 m) och
testsvitens A1 (vatten), med temporär cache och körningsmapp. Facit är kärnans egna filer:
webben får inte lägga till eller ändra något tal.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

import numpy as np

from dagsljus.exempelkvarter import (
    bygg_rutnat,
    byggnads_id,
    rektangel,
    skriv_k9,
)
from karna.scenario import Andring
from testsvit.k1_io import las_geotiff
from webb.api import FlodenApiApp

REPO = Path(__file__).resolve().parents[2]
E0, N0 = 150000.0, 6576000.0
SOL = {
    "hojd_max_m": 20.0,
    "h_min_grader": 15.0,
    "azimutriktningar": 36,
    "datum": ["2026-03-20"],
    "steg_min": 60,
}
VSC = {"hojd_max_m": 20.0, "h_min_grader": 15.0, "azimutriktningar": 32, "fasad_steg_m": 8.0}


def _anrop(app: FlodenApiApp, metod: str, sokvag: str) -> tuple[int, dict[str, str], bytes]:
    return app.hantera_forfragan(metod, sokvag)


def _json(app: FlodenApiApp, metod: str, sokvag: str) -> tuple[int, dict[str, Any]]:
    status, _, kropp = _anrop(app, metod, sokvag)
    data: dict[str, Any] = json.loads(kropp.decode("utf-8"))
    return status, data


def _vanta_klar(app: FlodenApiApp, korning_id: str, *, sekunder: float = 180.0) -> dict[str, Any]:
    slut = time.monotonic() + sekunder
    while time.monotonic() < slut:
        status, d = _json(app, "GET", f"/api/korningar/{korning_id}")
        assert status == 200, d
        if d["status"] in ("klar", "fel"):
            return d
        time.sleep(0.1)
    raise AssertionError("Körningen blev inte klar i tid.")


def _inline(d: dict[str, Any]) -> str:
    """TOML:s inline-tabell (JSON-värden är giltiga TOML-värden för tal och strängar)."""
    return "{ " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in d.items()) + " }"


def _toml_fraga(bas: Path, k9_hojd: Path, k9_ny: Path) -> str:
    return f"""kontrakt = "K20/v1"
fraga_id = "webb-test"
beskrivning = "Liten före/efter för webbtester."

[scenarier.bas]
bas = "{bas.as_posix()}"

[scenarier.hojd]
bas = "{bas.as_posix()}"
k9 = ["{k9_hojd.as_posix()}"]

[[analyser]]
id = "sol"
losare = "dagsljus.soltimmar_mark"
parametrar = {_inline(SOL)}

[[analyser]]
id = "vsc"
losare = "dagsljus.vsc_fasad"
parametrar = {_inline(VSC)}

[[jamforelser]]
analys = "sol"
utdata = "soltimmar"
fore = "bas"
efter = "hojd"
troskel = 0.1
"""


class TestWebbFragor(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        hus = [
            rektangel(E0 + 10, E0 + 30, N0 + 10, N0 + 22, 6.0, "a"),
            rektangel(E0 + 50, E0 + 70, N0 + 50, N0 + 62, 10.0, "b"),
            rektangel(E0 + 50, E0 + 70, N0 + 10, N0 + 22, None, "std"),  # standardhöjd -> varning
        ]
        cls.bas = bygg_rutnat(
            cls.tmp / "bas", hus, e_min_m=E0, n_min_m=N0, antal=2, rutstorlek_m=40.0
        )
        cls.k9 = skriv_k9(
            cls.tmp / "hojd.json",
            cls.bas,
            "hojd",
            [
                Andring(
                    "byggnad_hojd",
                    "hojd_b",
                    None,
                    {"byggnads_id": byggnads_id("b"), "hojd_m": 16.0},
                )
            ],
        )
        cls.fragor = cls.tmp / "fragor"
        cls.fragor.mkdir()
        (cls.fragor / "liten.toml").write_text(
            _toml_fraga(cls.bas, cls.k9, cls.k9), encoding="utf-8"
        )
        (cls.fragor / "trasig.toml").write_text('kontrakt = "K20/v9"\n', encoding="utf-8")
        (cls.fragor / "saknad_bas.toml").write_text(
            "# Bygg basen först.\n"
            'kontrakt = "K20/v1"\nfraga_id = "saknad-bas"\n'
            '[scenarier.bas]\nbas = "finns_inte"\n'
            '[[analyser]]\nid = "sol"\nlosare = "dagsljus.soltimmar_mark"\n',
            encoding="utf-8",
        )
        cls.app = FlodenApiApp(
            testsvit_rot=REPO / "testsvit",
            konfig_fil=REPO / "config.toml",
            fragemappar=[cls.fragor, REPO / "exempel" / "fragor"],
            cachemapp=cls.tmp / "cache",
            korningsmapp=cls.tmp / "korningar",
        )
        status, d = _json(cls.app, "POST", "/api/fragor/liten/kor")
        assert status == 202, d
        cls.korning_id: str = d["korning_id"]
        cls.slutstatus = _vanta_klar(cls.app, cls.korning_id)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ lista

    def test_lista_fragor_visar_filerna_och_felaktiga_som_ogiltiga(self) -> None:
        status, d = _json(self.app, "GET", "/api/fragor")
        self.assertEqual(status, 200)
        per_ref = {f["ref"]: f for f in d["fragor"]}
        self.assertIn("liten", per_ref)
        self.assertIn("dagsljus_fore_efter", per_ref)  # exempel/fragor ingår
        self.assertIn("vatten_a1_uppfyllnad", per_ref)
        liten = per_ref["liten"]
        self.assertTrue(liten["giltig"])
        self.assertEqual([a["id"] for a in liten["analyser"]], ["sol", "vsc"])
        self.assertEqual(liten["analyser"][0]["doman"], "dagsljus")
        self.assertEqual(len(liten["jamforelser"]), 1)
        self.assertEqual(liten["indata_saknas"], [])
        trasig = per_ref["trasig"]
        self.assertFalse(trasig["giltig"])
        self.assertIn("K20", trasig["fel"])
        saknad = per_ref["saknad_bas"]
        self.assertEqual(saknad["hjalp"], "Bygg basen först.")
        self.assertEqual(len(saknad["indata_saknas"]), 1)

    def test_okand_och_ogiltig_referens(self) -> None:
        self.assertEqual(_json(self.app, "GET", "/api/fragor/finns-inte")[0], 404)
        self.assertEqual(_json(self.app, "POST", "/api/fragor/finns-inte/plan")[0], 404)
        self.assertEqual(_json(self.app, "GET", "/api/fragor/..%2Fconfig")[0], 400)

    # ------------------------------------------------------------------ plan

    def test_plan_visar_jobb_rutor_och_cache(self) -> None:
        status, p = _json(self.app, "POST", "/api/fragor/liten/plan")
        self.assertEqual(status, 200, p)
        # 2 analyser x 2 scenarier x 4 rutor. Körningen i setUpClass har fyllt cachen.
        self.assertEqual(p["sammanfattning"], {"jobb": 16, "i_cache": 16, "att_kora": 0})
        self.assertEqual(p["per_analys"]["sol"]["jobb"], 8)
        self.assertEqual(
            p["utbredning"],
            {"x_min": E0, "y_min": N0, "x_max": E0 + 80.0, "y_max": N0 + 80.0},
        )
        rutor = p["scenarier"][0]["rutor"]
        self.assertEqual(len(rutor), 4)
        self.assertTrue(all(r["x_max"] - r["x_min"] == 40.0 for r in rutor))
        self.assertTrue(all(j["i_cache"] for j in p["jobb"]))
        self.assertEqual({j["analys"] for j in p["jobb"]}, {"sol", "vsc"})
        self.assertTrue(all(j["radie_m"] > 0 for j in p["jobb"]))

    def test_plan_utan_cache_visar_att_kora_och_andrar_ingenting_pa_disk(self) -> None:
        ny = FlodenApiApp(
            testsvit_rot=REPO / "testsvit",
            konfig_fil=REPO / "config.toml",
            fragemappar=[self.fragor],
            cachemapp=self.tmp / "tom-cache",
            korningsmapp=self.tmp / "tomma-korningar",
        )
        status, p = _json(ny, "POST", "/api/fragor/liten/plan")
        self.assertEqual(status, 200, p)
        self.assertEqual(p["sammanfattning"], {"jobb": 16, "i_cache": 0, "att_kora": 16})
        self.assertFalse((self.tmp / "tomma-korningar").exists())

    def test_plan_med_saknad_bas_ger_tydligt_fel(self) -> None:
        status, d = _json(self.app, "POST", "/api/fragor/saknad_bas/plan")
        self.assertEqual(status, 422)
        self.assertIn("finns_inte", d["fel"])
        status, d = _json(self.app, "POST", "/api/fragor/saknad_bas/kor")
        self.assertEqual(status, 422)

    # ------------------------------------------------------------------ körning

    def test_korningen_blev_klar_med_framsteg(self) -> None:
        d = self.slutstatus
        self.assertEqual(d["status"], "klar", d)
        self.assertEqual(d["framsteg"], {"klara": 16, "totalt": 16})
        self.assertIsNone(d["fel"])
        status, lista = _json(self.app, "GET", "/api/korningar")
        self.assertEqual(status, 200)
        self.assertIn(self.korning_id, [k["korning_id"] for k in lista["korningar"]])
        self.assertEqual(_json(self.app, "GET", "/api/korningar/finns-inte")[0], 404)

    def test_resultat_for_korning_som_inte_ar_klar_ger_409(self) -> None:
        ny = FlodenApiApp(
            testsvit_rot=REPO / "testsvit",
            konfig_fil=REPO / "config.toml",
            fragemappar=[self.fragor],
            cachemapp=self.tmp / "cache",
            korningsmapp=self.tmp / "korningar2",
        )
        # En körning som fortfarande ligger i kö: lägg in den utan att starta tråden.
        from webb.fragor import Korning

        k = Korning("ko-test", "liten", "webb-test", "x", self.tmp / "korningar2" / "ko-test")
        ny.fragor._lasta_korningar()
        ny.fragor._korningar[k.korning_id] = k
        status, d = _json(ny, "GET", "/api/korningar/ko-test/resultat")
        self.assertEqual(status, 409)
        self.assertIn("inte klar", d["fel"])

    # ------------------------------------------------------------------ resultat

    def _resultat(self) -> dict[str, Any]:
        status, r = _json(self.app, "GET", f"/api/korningar/{self.korning_id}/resultat")
        self.assertEqual(status, 200, r)
        return r

    def test_matvarden_ar_kärnans_egna(self) -> None:
        r = self._resultat()
        tab = {t["analys"]: t for t in r["tabeller"]}
        self.assertEqual(set(tab), {"sol", "vsc"})
        self.assertEqual(len(tab["sol"]["rader"]), 8)
        self.assertTrue(tab["sol"]["kolumner"])
        # Jämför mot kärnans sammanställning, siffra för siffra.
        s = json.loads(
            (
                self.tmp / "korningar" / self.korning_id / "resultat" / "sammanstallning.json"
            ).read_text(encoding="utf-8")
        )
        kalla = {(j["analys"], j["scenario"], j["ruta"]): j["matvarden"] for j in s["jobb"]}
        for t in r["tabeller"]:
            for rad in t["rader"]:
                self.assertEqual(rad["varden"], kalla[(t["analys"], rad["scenario"], rad["ruta"])])
        self.assertEqual(r["sammanfattning"]["jobb"], 16)
        self.assertEqual([j["efter"] for j in r["jamforelser"]], ["hojd"])
        jf = r["jamforelser"][0]
        self.assertGreater(max(jf["max_okning"], jf["max_minskning"]), 0.0)

    def test_varning_for_standardhojd_visas_med_var(self) -> None:
        r = self._resultat()
        texter = [w["text"].lower() for w in r["varningar"]]
        self.assertTrue(any("standardhöjd" in t or "standardhojd" in t for t in texter), texter)
        for w in r["varningar"]:
            self.assertEqual(w["kalla"], "losare")
            self.assertGreater(w["antal_jobb"], 0)
            self.assertTrue(w["analyser"])

    def test_lager_bas_scenario_och_skillnad_med_gemensam_skala(self) -> None:
        r = self._resultat()
        lager = {la["id"]: la for la in r["lager"]}
        self.assertEqual(lager["sol.bas.soltimmar"]["typ"], "bas")
        self.assertEqual(lager["sol.hojd.soltimmar"]["typ"], "scenario")
        diff = lager["skillnad.sol_soltimmar_hojd_minus_bas"]
        self.assertEqual(diff["typ"], "skillnad")
        self.assertTrue(diff["primar"])
        self.assertEqual(diff["fore"], "bas")
        # Samma färgskala för före och efter, annars går de inte att jämföra.
        self.assertEqual(lager["sol.bas.soltimmar"]["skala"], lager["sol.hojd.soltimmar"]["skala"])
        # Sekundära lager (flaggor) finns men är inte primära.
        self.assertFalse(lager["sol.bas.standardhojd"]["primar"])
        for la in lager.values():
            self.assertEqual(
                la["utbredning"],
                {"x_min": E0, "y_min": N0, "x_max": E0 + 80.0, "y_max": N0 + 80.0},
            )
            self.assertEqual((la["bredd_px"], la["hojd_px"]), (80, 80))

    def test_png_ar_en_bild_i_ratt_storlek_och_skillnaden_syns_bara_dar_den_finns(self) -> None:
        from io import BytesIO

        from PIL import Image

        r = self._resultat()
        diff = next(la for la in r["lager"] if la["typ"] == "skillnad")
        status, headers, png = _anrop(self.app, "GET", diff["png"])
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        bild = np.asarray(Image.open(BytesIO(png)).convert("RGBA"))
        self.assertEqual(bild.shape, (80, 80, 4))
        # Facit: kärnans diff-rasters egna celler. Synlig exakt där |diff| > tröskel (0,1 h).
        ytor = []
        for g in diff["geotiff"]:
            _, _, filinnehall = _anrop(self.app, "GET", g["url"])
            self.assertEqual(filinnehall[:2], b"II")  # TIFF, little endian
            ytor.append(g)
        self.assertEqual(len(ytor), 4)
        mapp = self.tmp / "korningar" / self.korning_id / "resultat" / "jamforelser"
        mapp = next(mapp.iterdir())
        fasit = np.full((80, 80), np.nan)
        for fil in mapp.glob("*.tif"):
            a, geo = las_geotiff(fil)
            c0, r0 = round(geo.x_min - E0), round(N0 + 80.0 - geo.y_max)
            fasit[r0 : r0 + a.shape[0], c0 : c0 + a.shape[1]] = a
        forvantad = np.isfinite(fasit) & (np.abs(fasit) > 0.1)
        self.assertTrue(forvantad.any())
        self.assertFalse(forvantad.all())
        np.testing.assert_array_equal(bild[..., 3] > 0, forvantad)

    def test_okand_lager_png_ger_404(self) -> None:
        status, _, _ = _anrop(self.app, "GET", f"/api/korningar/{self.korning_id}/lager/nej.png")
        self.assertEqual(status, 404)

    def test_filer_utanfor_resultatet_laser_inte(self) -> None:
        for sokvag in (
            f"/api/korningar/{self.korning_id}/fil/../korning.json",
            f"/api/korningar/{self.korning_id}/fil/%2e%2e/korning.json",
            f"/api/korningar/{self.korning_id}/fil/..%2F..%2Fkorning.json",
            f"/api/korningar/{self.korning_id}/fil/finns-inte.tif",
        ):
            status, _, kropp = _anrop(self.app, "GET", sokvag)
            self.assertEqual(status, 404, (sokvag, kropp[:100]))

    def test_datatabell_forhandsvisas(self) -> None:
        r = self._resultat()
        csv_fil = next(d for d in r["datatabeller"] if d["fil"].endswith(".csv"))
        status, tab = _json(self.app, "GET", csv_fil["forhandsvisning"])
        self.assertEqual(status, 200, tab)
        self.assertIn("fasad_id", tab["kolumner"])
        self.assertGreater(tab["antal_rader"], 0)

    def test_en_ny_server_laser_gamla_korningar_fran_disk(self) -> None:
        ny = FlodenApiApp(
            testsvit_rot=REPO / "testsvit",
            konfig_fil=REPO / "config.toml",
            fragemappar=[self.fragor],
            cachemapp=self.tmp / "cache",
            korningsmapp=self.tmp / "korningar",
        )
        status, d = _json(ny, "GET", f"/api/korningar/{self.korning_id}")
        self.assertEqual((status, d["status"]), (200, "klar"))
        status, r = _json(ny, "GET", f"/api/korningar/{self.korning_id}/resultat")
        self.assertEqual(status, 200)
        self.assertEqual(len(r["lager"]), len(self._resultat()["lager"]))


class TestWebbVatten(unittest.TestCase):
    """Vattenfrågan på A1 (blåytor före/efter): klassad vattenfärg och noll volymfel."""

    def test_vatten_a1_blaytor_fore_efter(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        app = FlodenApiApp(
            testsvit_rot=REPO / "testsvit",
            konfig_fil=REPO / "config.toml",
            fragemappar=[REPO / "exempel" / "fragor"],
            cachemapp=tmp / "cache",
            korningsmapp=tmp / "korningar",
        )
        status, p = _json(app, "POST", "/api/fragor/vatten_a1_uppfyllnad/plan")
        self.assertEqual((status, p["sammanfattning"]["att_kora"]), (200, 2))
        status, d = _json(app, "POST", "/api/fragor/vatten_a1_uppfyllnad/kor")
        self.assertEqual(status, 202)
        slut = _vanta_klar(app, d["korning_id"])
        self.assertEqual(slut["status"], "klar", slut)
        status, r = _json(app, "GET", f"/api/korningar/{d['korning_id']}/resultat")
        self.assertEqual(status, 200, r)
        lager = {la["id"]: la for la in r["lager"]}
        fore = lager["skyfall.fore.max_djup"]
        self.assertEqual(fore["farg"], "vatten")
        self.assertEqual(fore["teckenforklaring"]["typ"], "klasser")
        self.assertEqual(fore["enhet"], "m")
        # Skalan är lösarens eget största maxdjup, inte något webben räknat fram.
        varden = {x["scenario"]: x["varden"] for x in r["tabeller"][0]["rader"]}
        self.assertAlmostEqual(
            max(fore["skala"]["max"], 0.0), varden["efter"]["max_djup_m"], places=6
        )
        self.assertEqual(r["varningar"], [])
        self.assertLess(varden["fore"]["volymfel_rel"], 1e-9)


if __name__ == "__main__":
    unittest.main()
