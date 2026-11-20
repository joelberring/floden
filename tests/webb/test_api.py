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

        for filnamn in ("types.ts", "api.ts", "karta.ts", "main.ts", "style.css"):
            sokvag = webb_rot / "src" / filnamn
            self.assertTrue(sokvag.is_file(), f"Saknar webbklientfil: {sokvag}")
            self.assertGreater(sokvag.stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()
