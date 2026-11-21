"""Enhetstester för Flodens lokala webb-API, rendering och webbklientstruktur (T10)."""

from __future__ import annotations

import base64
import io
import json
import unittest
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from gemensamt.manifest import KONTRAKT_K0, RunManifest
from webb.api import FlodenApiApp
from webb.rendering import rendera_diff_png_base64, rendera_djup_png_base64

_REPO_ROT = Path(__file__).resolve().parents[2]


def _avkoda_png_base64(b64_text: str) -> Image.Image:
    raw = base64.b64decode(b64_text)
    return Image.open(io.BytesIO(raw))


class TestWebbApiOchKlient(unittest.TestCase):
    """Verifierar alla endpoints i `FlodenApiApp`, PNG-transparens och `webb/`-struktur."""

    def setUp(self) -> None:
        self.app = FlodenApiApp(
            testsvit_rot=_REPO_ROT / "testsvit",
            konfig_fil=_REPO_ROT / "config.toml",
        )

    def test_get_halsa_returnerar_epsg_3011_och_kallor(self) -> None:
        status, headers, kropp = self.app.hantera_forfragan("GET", "/api/halsa")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")

        data: dict[str, Any] = json.loads(kropp.decode("utf-8"))
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["epsg"], 3011)
        self.assertEqual(data["hojdsystem"], "RH 2000")
        self.assertEqual(data["kallor"], ["©Lantmäteriet", "SGU", "SMHI", "Floden"])
        sektion_namn = [s["namn"] for s in data["gatusektioner"]]
        self.assertIn("standard-12m", sektion_namn)

    def test_get_omraden_listar_alla_nio_syntetiska_fall(self) -> None:
        status, _, kropp = self.app.hantera_forfragan("GET", "/api/omraden")
        self.assertEqual(status, 200)
        data = json.loads(kropp.decode("utf-8"))
        self.assertEqual(data["epsg"], 3011)
        koder = {o["kod"] for o in data["omraden"]}
        for nr in range(1, 10):
            self.assertIn(f"A{nr}", koder)

    def test_get_omrade_lager_a3_returnerar_hillshade_geojson_och_percentiler(self) -> None:
        status, _, kropp = self.app.hantera_forfragan(
            "GET", "/api/omraden/A3_tva_sankor_sammanslagning/lager"
        )
        self.assertEqual(status, 200)
        data = json.loads(kropp.decode("utf-8"))
        self.assertEqual(data["kod"], "A3")
        self.assertEqual(data["epsg"], 3011)
        self.assertEqual(len(data["bbox_3011"]), 4)
        self.assertEqual(len(data["bbox_wgs84"]), 4)

        hs_img = _avkoda_png_base64(data["hillshade"]["png_base64"])
        self.assertEqual(hs_img.format, "PNG")
        self.assertEqual(hs_img.size, (data["bredd_px"], data["hojd_px"]))

        self.assertEqual(data["sankor"]["type"], "FeatureCollection")
        self.assertGreaterEqual(len(data["sankor"]["features"]), 2)
        self.assertEqual(data["flodesvagar"]["type"], "FeatureCollection")
        self.assertGreater(len(data["flodesvagar"]["features"]), 0)

        osak = data["osakerhet"]
        self.assertLessEqual(osak["p10"]["max_djup_m"], osak["p50"]["max_djup_m"] + 1e-6)
        self.assertLessEqual(osak["p50"]["max_djup_m"], osak["p90"]["max_djup_m"] + 1e-6)
        self.assertIn("©Lantmäteriet", data["kallor"])

    def test_rendering_transparent_vid_nolldjup_och_nolldiff(self) -> None:
        djup = np.array([[0.0, 0.2], [0.0, 0.5]], dtype=np.float32)
        djup_res = rendera_djup_png_base64(djup)
        djup_img = _avkoda_png_base64(djup_res["png_base64"]).convert("RGBA")
        self.assertEqual(djup_img.getpixel((0, 0))[3], 0)
        self.assertGreater(djup_img.getpixel((1, 0))[3], 100)

        diff = np.array([[0.0, 0.1], [-0.1, 0.0]], dtype=np.float32)
        diff_res = rendera_diff_png_base64(diff)
        diff_img = _avkoda_png_base64(diff_res["png_base64"]).convert("RGBA")
        self.assertEqual(diff_img.getpixel((0, 0))[3], 0)
        self.assertGreater(diff_img.getpixel((1, 0))[3], 100)
        self.assertGreater(diff_img.getpixel((0, 1))[3], 100)

    def test_post_kor_med_k3_lapp_landxml_gata_och_byggnad(self) -> None:
        # Använd A3-områdets koordinater: x=[150200, 150225], y=[6576000, 6576015]
        landxml = """<?xml version="1.0" encoding="UTF-8"?>
        <LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2" version="1.2">
          <Surfaces>
            <Surface name="uppfyllnad">
              <Definition surfType="TIN">
                <Pnts>
                  <P id="1">6576002.0 150202.0 10.75</P>
                  <P id="2">6576002.0 150208.0 10.75</P>
                  <P id="3">6576007.0 150208.0 10.75</P>
                  <P id="4">6576007.0 150202.0 10.75</P>
                </Pnts>
                <Faces>
                  <F>1 2 3</F>
                  <F>1 3 4</F>
                </Faces>
              </Definition>
            </Surface>
          </Surfaces>
        </LandXML>
        """
        payload = {
            "omrade_id": "A3_tva_sankor_sammanslagning",
            "intensitet_mm_h": 72.0,
            "varaktighet_s": 3600.0,
            "lapp_id": "webb-test-a3",
            "landxml": landxml,
            "gator": [
                {
                    "gata_id": "ny-gata-1",
                    "mittlinje": [[150202.0, 6576011.0], [150222.0, 6576011.0]],
                    "sektion": "standard-12m",
                }
            ],
            "byggnader": [
                {
                    "byggnad_id": "hus-1",
                    "polygon": [
                        [150215.0, 6576002.0],
                        [150220.0, 6576002.0],
                        [150220.0, 6576005.0],
                        [150215.0, 6576005.0],
                    ],
                    "entre_rh2000": 10.50,
                    "entre_punkt": [150214.5, 6576003.5],
                }
            ],
        }

        status, _, kropp = self.app.hantera_forfragan(
            "POST",
            "/api/kor",
            json.dumps(payload).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        res = json.loads(kropp.decode("utf-8"))

        self.assertTrue(res["har_lapp"])
        self.assertEqual(res["lapp_id"], "webb-test-a3")
        self.assertGreaterEqual(res["fordrojningsvolym_m3"], 0.0)
        self.assertGreaterEqual(res["grann_forsamring_volym_m3"], 0.0)
        self.assertGreater(res["kortid_s"], 0.0)
        self.assertIn("nya_drabbade_entreer", res)
        self.assertEqual(len(res["projekt_entreer"]), 1)

        for nyckel in ("fore", "efter", "differens"):
            img = _avkoda_png_base64(res[nyckel]["png_base64"])
            self.assertEqual(img.format, "PNG")

        osak = res["osakerhet"]
        self.assertLessEqual(osak["p10"]["max_djup_m"], osak["p50"]["max_djup_m"] + 1e-6)
        self.assertLessEqual(osak["p50"]["max_djup_m"], osak["p90"]["max_djup_m"] + 1e-6)

        manifest = RunManifest.fran_dict(res["manifest"])
        self.assertEqual(manifest.kontrakt, KONTRAKT_K0)
        self.assertEqual(res["kallor"], ["©Lantmäteriet", "SGU", "SMHI", "Floden"])

    def test_ogiltiga_forfragningar_ger_tydligt_fel_400_eller_404(self) -> None:
        status_404, _, kropp_404 = self.app.hantera_forfragan(
            "GET", "/api/omraden/finns_inte/lager"
        )
        self.assertEqual(status_404, 404)
        self.assertIn("fel", json.loads(kropp_404.decode("utf-8")))

        status_400, _, kropp_400 = self.app.hantera_forfragan(
            "POST", "/api/kor", b"{ogiltig json"
        )
        self.assertEqual(status_400, 400)
        self.assertIn("fel", json.loads(kropp_400.decode("utf-8")))

        status_saknas, _, kropp_saknas = self.app.hantera_forfragan(
            "POST", "/api/kor", b"{}"
        )
        self.assertEqual(status_saknas, 400)
        self.assertIn("omrade_id", json.loads(kropp_saknas.decode("utf-8"))["fel"])

    def test_get_regn_listar_sverige_och_stockholmsregn(self) -> None:
        status, _, kropp = self.app.hantera_forfragan("GET", "/api/regn")
        self.assertEqual(status, 200)
        data = json.loads(kropp.decode("utf-8"))
        self.assertEqual(data["kontrakt"], "K4/v1")
        self.assertIn(20.0, data["standard_aterkomsttider_ar"])
        self.assertIn(50.0, data["standard_aterkomsttider_ar"])
        self.assertIn(500.0, data["standard_aterkomsttider_ar"])
        self.assertIn(1.24, data["standard_klimatfaktorer"])
        self.assertIn(1.4, data["standard_klimatfaktorer"])

        ids = {f["regn_id"] for f in data["forval"]}
        self.assertIn("stockholm_cds_6h_100ar_kf124", ids)
        self.assertIn("stockholm_cds_6h_100ar_kf140", ids)
        self.assertIn("stockholm_cds_6h_500ar_kf140", ids)
        self.assertIn("sverige_cds_6h_20ar_kf125", ids)

        status_sthlm, _, kropp_sthlm = self.app.hantera_forfragan(
            "GET", "/api/regn?region=Stockholm"
        )
        self.assertEqual(status_sthlm, 200)
        sthlm = json.loads(kropp_sthlm.decode("utf-8"))
        self.assertTrue(all(f["region"] == "Stockholm" for f in sthlm["forval"]))

    def test_post_kor_med_stockholmsregn_forval(self) -> None:
        payload = {
            "omrade_id": "A3_tva_sankor_sammanslagning",
            "regn_id": "stockholm_cds_6h_100ar_kf140",
        }
        status, _, kropp = self.app.hantera_forfragan(
            "POST",
            "/api/kor",
            json.dumps(payload).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        res = json.loads(kropp.decode("utf-8"))
        self.assertEqual(res["regn"]["regn_id"], "stockholm_cds_6h_100ar_kf140")
        self.assertAlmostEqual(res["regn"]["klimatfaktor"], 1.4, places=6)
        self.assertGreater(res["regn"]["total_djup_mm"], 115.0)
        self.assertGreater(res["fore"]["max_djup_m"], 0.1)

    def test_post_risk_beraknar_kumulativ_oversvamningsrisk(self) -> None:
        payload_100 = {
            "omrade_id": "A3_tva_sankor_sammanslagning",
            "gransdjup_m": 0.20,
            "max_aterkomsttid_ar": 100.0,
            "klimatfaktor": 1.24,
            "tidshorisont_ar": 50.0,
        }
        status, _, kropp = self.app.hantera_forfragan(
            "POST",
            "/api/risk",
            json.dumps(payload_100).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        res = json.loads(kropp.decode("utf-8"))
        self.assertEqual(res["epsg"], 3011)
        self.assertEqual(res["gransdjup_m"], 0.20)
        self.assertEqual(res["max_aterkomsttid_ar"], 100.0)
        self.assertEqual(res["klimatfaktor"], 1.24)
        self.assertEqual(res["aterkomsttider_ar"], [2.0, 5.0, 10.0, 20.0, 50.0, 100.0])
        self.assertGreater(res["sammanfattning"]["area_over_grans_m2"], 0.0)
        self.assertGreater(res["sammanfattning"]["max_kumulativ_sannolikhet"], 0.0)
        self.assertLessEqual(res["sammanfattning"]["max_kumulativ_sannolikhet"], 1.0)
        self.assertEqual(len(res["steg"]), 6)

        risk_img = _avkoda_png_base64(res["kumulativ_risk_karta"]["png_base64"]).convert("RGBA")
        self.assertEqual(risk_img.format, None)
        self.assertGreater(risk_img.size[0], 0)
        self.assertEqual(risk_img.getpixel((0, 0))[3], 0)

        manifest = RunManifest.fran_dict(res["manifest"])
        self.assertEqual(manifest.kontrakt, KONTRAKT_K0)

        # Saknat gransdjup_m måste ge tydligt 400-fel
        status_fel, _, kropp_fel = self.app.hantera_forfragan(
            "POST",
            "/api/risk",
            json.dumps({"omrade_id": "A3_tva_sankor_sammanslagning"}).encode("utf-8"),
        )
        self.assertEqual(status_fel, 400)
        self.assertIn("gransdjup_m", json.loads(kropp_fel.decode("utf-8"))["fel"])

    def test_post_kor_och_risk_med_t06_infiltration(self) -> None:
        bas_payload = {
            "omrade_id": "A3_tva_sankor_sammanslagning",
            "regn_id": "stockholm_cds_6h_100ar_kf124",
        }
        infil_payload = {
            **bas_payload,
            "forluster": {
                "aktivera": True,
                "jordart": "sand",
                "grundvatten": "normal",
                "tjale": False,
                "initial_mm": 2.0,
            },
        }
        s1, _, k1 = self.app.hantera_forfragan(
            "POST", "/api/kor", json.dumps(bas_payload).encode("utf-8")
        )
        s2, _, k2 = self.app.hantera_forfragan(
            "POST", "/api/kor", json.dumps(infil_payload).encode("utf-8")
        )
        self.assertEqual(s1, 200)
        self.assertEqual(s2, 200)
        r1 = json.loads(k1.decode("utf-8"))
        r2 = json.loads(k2.decode("utf-8"))
        self.assertIsNone(r1["forluster"])
        self.assertIsNotNone(r2["forluster"])
        self.assertEqual(r2["forluster"]["jordart"], "sand")
        self.assertLess(
            r2["forluster"]["netto_avrinning_medel_mm"],
            r2["forluster"]["brutto_regn_mm"],
        )
        self.assertLessEqual(r2["fore"]["max_djup_m"], r1["fore"]["max_djup_m"])

        s_risk, _, k_risk = self.app.hantera_forfragan(
            "POST",
            "/api/risk",
            json.dumps(
                {
                    "omrade_id": "A3_tva_sankor_sammanslagning",
                    "gransdjup_m": 0.20,
                    "max_aterkomsttid_ar": 100.0,
                    "klimatfaktor": 1.24,
                    "jordart": "sand",
                }
            ).encode("utf-8"),
        )
        self.assertEqual(s_risk, 200)
        rr = json.loads(k_risk.decode("utf-8"))
        self.assertEqual(rr["gransdjup_m"], 0.20)

    def test_post_rapport_med_talkontroll_och_export(self) -> None:
        for fmt in ("html", "md", "docx"):
            payload = {
                "omrade_id": "A3_tva_sankor_sammanslagning",
                "rapporttyp": "skyfall_oversikt",
                "format": fmt,
                "regn_id": "stockholm_cds_6h_100ar_kf124",
            }
            status, _, kropp = self.app.hantera_forfragan(
                "POST", "/api/rapport", json.dumps(payload).encode("utf-8")
            )
            self.assertEqual(status, 200, f"format={fmt}: {kropp.decode('utf-8')}")
            res = json.loads(kropp.decode("utf-8"))
            self.assertTrue(res["kontroll_godkand"])
            self.assertGreater(res["antal_kontrollerade_tal"], 0)
            self.assertGreater(res["storlek_bytes"], 100)
            raw = base64.b64decode(res["innehall_base64"])
            self.assertEqual(len(raw), res["storlek_bytes"])
            if fmt == "docx":
                self.assertTrue(raw.startswith(b"PK"))
            if fmt == "html":
                html_text = raw.decode("utf-8")
                self.assertIn("data:image/png;base64,", html_text)

    def test_post_kor_med_grundmodell_rattning_och_experiment(self) -> None:
        omrade = self.app.ladda_omrade("A3_tva_sankor_sammanslagning")
        geo = omrade.geo
        cx = 0.5 * (geo.x_min + geo.x_max)
        cy = 0.5 * (geo.y_min + geo.y_max)

        payload = {
            "omrade_id": "A3_tva_sankor_sammanslagning",
            "intensitet_mm_h": 60.0,
            "varaktighet_s": 1800.0,
            "grundmodell_rattning": {
                "rattning_id": "ratt-test-1",
                "passager": [
                    {
                        "passage_id": "bro-1",
                        "typ": "bro",
                        "linje": [[cx - 2.0, cy], [cx + 2.0, cy]],
                        "bredd_m": 1.5,
                        "aktiv": True,
                    }
                ],
            },
            "experiment": {
                "experiment_id": "exp-test-1",
                "diken": [
                    {
                        "atgard_id": "dike-1",
                        "linje": [[cx - 3.0, cy - 2.0], [cx + 3.0, cy - 2.0]],
                        "djup_m": 0.4,
                        "botten_bredd_m": 1.0,
                        "slantslutning": 1.0,
                        "aktiv": True,
                    },
                    {
                        "atgard_id": "dike-inaktiv",
                        "linje": [[cx - 3.0, cy + 3.0], [cx + 3.0, cy + 3.0]],
                        "djup_m": 0.8,
                        "aktiv": False,
                    },
                ],
                "vallar": [
                    {
                        "atgard_id": "vall-1",
                        "linje": [[cx - 2.0, cy + 2.0], [cx + 2.0, cy + 2.0]],
                        "hojd_over_mark_m": 0.5,
                        "kron_bredd_m": 0.5,
                        "slantslutning": 1.0,
                        "aktiv": True,
                    }
                ],
                "gator": [
                    {
                        "atgard_id": "gata-1",
                        "linje": [[cx - 2.0, cy - 3.0], [cx + 2.0, cy - 3.0]],
                        "bredd_m": 2.0,
                        "delta_z_m": 0.15,
                        "aktiv": True,
                    }
                ],
            },
        }
        status, _, kropp = self.app.hantera_forfragan(
            "POST", "/api/kor", json.dumps(payload).encode("utf-8")
        )
        self.assertEqual(status, 200, kropp.decode("utf-8"))
        res = json.loads(kropp.decode("utf-8"))
        self.assertTrue(res["har_lapp"])
        self.assertIsNotNone(res["grundmodell_rattning"])
        self.assertEqual(res["grundmodell_rattning"]["antal_aktiva_rattningar"], 1)
        self.assertIsNotNone(res["experiment"])
        self.assertEqual(res["experiment"]["antal_aktiva_atgarder"], 3)
        self.assertGreater(res["experiment"]["antal_andrade_celler"], 0)
        self.assertGreater(res["experiment"]["schakt_volym_m3"], 0.0)
        self.assertGreater(res["experiment"]["fyll_volym_m3"], 0.0)
        manifest = RunManifest.fran_dict(res["manifest"])
        self.assertIn("grundmodell_rattning_sha256", manifest.parametrar)
        self.assertIn("experiment_sha256", manifest.parametrar)

    def test_post_rutt_kumulativ_vagrisk_och_testvardessparr(self) -> None:
        # Utan bekräftade testvärden måste skarp spärr returnera 400
        s_sparr, _, k_sparr = self.app.hantera_forfragan(
            "POST",
            "/api/rutt",
            json.dumps(
                {
                    "omrade_id": "A3_tva_sankor_sammanslagning",
                    "fordon_typ": "slacksbil",
                    "gransdjup_m": 0.25,
                    "tillat_testvarden": False,
                }
            ).encode("utf-8"),
        )
        self.assertEqual(s_sparr, 400)
        self.assertIn("tillat_testvarden", json.loads(k_sparr.decode("utf-8"))["fel"])

        # Med bekräftade testvärden returneras kumulativ vägrisk och GeoJSON
        s_ok, _, k_ok = self.app.hantera_forfragan(
            "POST",
            "/api/rutt",
            json.dumps(
                {
                    "omrade_id": "A3_tva_sankor_sammanslagning",
                    "fordon_typ": "slacksbil",
                    "gransdjup_m": 0.20,
                    "max_aterkomsttid_ar": 100.0,
                    "klimatfaktor": 1.24,
                    "tidshorisont_ar": 50.0,
                    "tillat_testvarden": True,
                }
            ).encode("utf-8"),
        )
        self.assertEqual(s_ok, 200)
        res = json.loads(k_ok.decode("utf-8"))
        self.assertTrue(res["testvarden_anvanda"])
        self.assertEqual(res["antal_kanter_totalt"], 12)
        self.assertGreater(res["antal_kanter_med_risk"], 0)
        self.assertEqual(res["vagnat_geojson"]["type"], "FeatureCollection")
        self.assertEqual(len(res["vagnat_geojson"]["features"]), 12)
        manifest = RunManifest.fran_dict(res["manifest"])
        self.assertEqual(manifest.kontrakt, KONTRAKT_K0)

    def test_webbklientens_filer_finns_och_har_giltig_struktur(self) -> None:
        webb_rot = _REPO_ROT / "webb"
        pkg = json.loads((webb_rot / "package.json").read_text(encoding="utf-8"))
        self.assertIn("maplibre-gl", pkg["dependencies"])
        self.assertIn("typescript", pkg["devDependencies"])
        self.assertIn("vite", pkg["devDependencies"])
        self.assertIn("build", pkg["scripts"])
        self.assertIn("typecheck", pkg["scripts"])

        tsconfig = json.loads((webb_rot / "tsconfig.json").read_text(encoding="utf-8"))
        self.assertTrue(tsconfig["compilerOptions"]["strict"])

        index_html = (webb_rot / "index.html").read_text(encoding="utf-8")
        self.assertIn("/src/main.ts", index_html)
        self.assertIn("maplibre-karta", index_html)
        self.assertIn("regn-forval", index_html)
        self.assertIn("risk-knapp", index_html)
        self.assertIn("rita-bro-knapp", index_html)
        self.assertIn("rita-dike-knapp", index_html)
        self.assertIn("rita-vall-knapp", index_html)

        for filnamn in ("types.ts", "api.ts", "karta.ts", "main.ts", "style.css"):
            sokvag = webb_rot / "src" / filnamn
            self.assertTrue(sokvag.is_file(), f"Saknar webbklientfil: {sokvag}")
            self.assertGreater(sokvag.stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()
