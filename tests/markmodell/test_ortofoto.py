"""Enhetstester för deterministisk ortofotoklassning och förändringsdetektering."""

from __future__ import annotations

import base64
import io
import unittest

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from markmodell.hinder_oppningar import (
    BYGGNAD,
    GRAS,
    GRONT_TAK,
    GRUS,
    HARDGJORT,
    OKANT,
    SKOG,
    VATTEN,
)
from markmodell.ortofoto import (
    OrtofotoFel,
    OrtofotoParametrar,
    berakna_vegetationsindex,
    detektera_forandringar_mot_laser,
    klassificera_ortofoto,
    kombinera_ortofoto_med_markmodell,
    rendera_markslag_png_base64,
)


def _skapa_syntetisk_stadsmiljo(
    *,
    med_nir: bool,
) -> tuple[
    NDArray[np.uint8],
    NDArray[np.float32],
    NDArray[np.bool_],
    NDArray[np.uint8],
    NDArray[np.bool_],
]:
    """Skapar en 80x80 syntetisk stadsmiljö med 8 distinkta zoner.

    Zonindelning (8 kolumner/band om 10x80 pixlar eller block):
    - Rad 0..39, Kol 0..19:   Asfalt / hårdgjord (HARDGJORT)
    - Rad 0..39, Kol 20..39:  Gräsmatta (GRAS, ndsm=0.1 m)
    - Rad 0..39, Kol 40..59:  Skog / trädkrona (SKOG, ndsm=8.0 m)
    - Rad 0..39, Kol 60..79:  Grusplan (GRUS)
    - Rad 40..79, Kol 0..19:  Vatten / damm (VATTEN)
    - Rad 40..79, Kol 20..39: Vanlig byggnad (BYGGNAD, byggnad_mask=True)
    - Rad 40..79, Kol 40..59: Grönt tak (GRONT_TAK, byggnad_mask=True)
    - Rad 40..79, Kol 60..79: Djup skugga (OKANT, skuggmask=True)
    """
    hojd, bredd = 80, 80
    band = 4 if med_nir else 3
    bild = np.zeros((hojd, bredd, band), dtype=np.uint8)
    ndsm = np.zeros((hojd, bredd), dtype=np.float32)
    byggnad_mask = np.zeros((hojd, bredd), dtype=bool)
    facit = np.zeros((hojd, bredd), dtype=np.uint8)
    forvantad_skugga = np.zeros((hojd, bredd), dtype=bool)

    def _fyll(
        r0: int,
        r1: int,
        k0: int,
        k1: int,
        rgb_nir: tuple[int, int, int, int],
        klass: int,
        *,
        hojd_m: float = 0.0,
        ar_byggnad: bool = False,
        ar_skugga: bool = False,
    ) -> None:
        if med_nir:
            bild[r0:r1, k0:k1, :] = np.array(rgb_nir, dtype=np.uint8)
        else:
            bild[r0:r1, k0:k1, :] = np.array(rgb_nir[:3], dtype=np.uint8)
        ndsm[r0:r1, k0:k1] = np.float32(hojd_m)
        byggnad_mask[r0:r1, k0:k1] = ar_byggnad
        facit[r0:r1, k0:k1] = np.uint8(klass)
        forvantad_skugga[r0:r1, k0:k1] = ar_skugga

    # 1. Asfalt (mörkgrå, neutral, ingen vegetation)
    _fyll(0, 40, 0, 20, (78, 80, 84, 72), HARDGJORT)
    # 2. Gräs (grön + hög NIR, låg höjd)
    _fyll(0, 40, 20, 40, (68, 138, 52, 195), GRAS, hojd_m=0.1)
    # 3. Skog (mörkare grön + hög NIR, ndsm=8 m)
    _fyll(0, 40, 40, 60, (48, 108, 42, 175), SKOG, hojd_m=8.0)
    # 4. Grusplan (ljus, varm ton)
    _fyll(0, 40, 60, 80, (186, 168, 132, 160), GRUS)
    # 5. Vatten (blågrön, låg NIR)
    _fyll(40, 80, 0, 20, (32, 86, 128, 18), VATTEN)
    # 6. Vanlig byggnad (tegelrött tak, känd byggnad)
    _fyll(40, 80, 20, 40, (160, 78, 66, 95), BYGGNAD, hojd_m=9.0, ar_byggnad=True)
    # 7. Grönt tak (vegetationsklätt tak, känd byggnad)
    _fyll(40, 80, 40, 60, (72, 142, 58, 190), GRONT_TAK, hojd_m=9.5, ar_byggnad=True)
    # 8. Djup hus-skugga (mycket låg luminans)
    _fyll(40, 80, 60, 80, (14, 15, 18, 16), OKANT, ar_skugga=True)

    return bild, ndsm, byggnad_mask, facit, forvantad_skugga


class TestOrtofoto(unittest.TestCase):
    def test_vegetationsindex_rgb_och_nir(self) -> None:
        rgb_bild, _, _, _, _ = _skapa_syntetisk_stadsmiljo(med_nir=False)
        exg, har_nir_rgb = berakna_vegetationsindex(rgb_bild)
        self.assertFalse(har_nir_rgb)
        self.assertEqual(exg.dtype, np.float32)
        self.assertGreater(float(np.mean(exg[0:40, 20:40])), 0.25)
        self.assertLess(float(np.mean(exg[0:40, 0:20])), 0.05)

        nir_bild, _, _, _, _ = _skapa_syntetisk_stadsmiljo(med_nir=True)
        ndvi, har_nir = berakna_vegetationsindex(nir_bild)
        self.assertTrue(har_nir)
        self.assertGreater(float(np.mean(ndvi[0:40, 20:40])), 0.45)
        self.assertLess(float(np.mean(ndvi[40:80, 0:20])), -0.20)

        # Flyttalsnormalisering ger samma resultat som uint8
        ndvi_float, _ = berakna_vegetationsindex(nir_bild.astype(np.float32) / 255.0)
        np.testing.assert_allclose(ndvi, ndvi_float, atol=1e-5)

    def test_syntetisk_stadsmiljo_med_nir(self) -> None:
        bild, ndsm, byggnad_mask, facit, forvantad_skugga = _skapa_syntetisk_stadsmiljo(
            med_nir=True
        )
        res = klassificera_ortofoto(bild, ndsm_m=ndsm, byggnad_mask=byggnad_mask)
        self.assertTrue(res.har_nir)

        solbelyst = ~forvantad_skugga
        traffsakerhet = float(np.mean(res.markslag[solbelyst] == facit[solbelyst]))
        self.assertGreaterEqual(traffsakerhet, 0.95)

        np.testing.assert_array_equal(res.skuggmask, forvantad_skugga)
        self.assertTrue(np.all(res.konfidens[forvantad_skugga] <= 0.35))
        self.assertTrue(np.all(res.markslag[forvantad_skugga] == OKANT))
        self.assertAlmostEqual(res.skugga_andel, 0.125, places=6)

        sammanfattning = res.sammanfattning_dict()
        self.assertEqual(sammanfattning["form_rc"], [80, 80])
        self.assertEqual(sammanfattning["antal_pixlar"], 6400)
        self.assertAlmostEqual(sum(res.klass_andelar.values()), 1.0, places=6)

    def test_syntetisk_stadsmiljo_ren_rgb(self) -> None:
        bild, ndsm, byggnad_mask, facit, forvantad_skugga = _skapa_syntetisk_stadsmiljo(
            med_nir=False
        )
        res = klassificera_ortofoto(bild, ndsm_m=ndsm, byggnad_mask=byggnad_mask)
        self.assertFalse(res.har_nir)

        solbelyst = ~forvantad_skugga
        traffsakerhet = float(np.mean(res.markslag[solbelyst] == facit[solbelyst]))
        self.assertGreaterEqual(traffsakerhet, 0.95)

        np.testing.assert_array_equal(res.skuggmask, forvantad_skugga)
        self.assertTrue(np.all(res.konfidens[forvantad_skugga] <= 0.35))
        self.assertTrue(np.all(res.markslag[forvantad_skugga] == OKANT))

    def test_forandringsdetektering_hardgjord_och_ny_byggnad(self) -> None:
        # 40x40 raster med pixelstorlek 0.25 m -> 10x10 m område (100 m²)
        form = (40, 40)
        gammal_markslag = np.full(form, GRAS, dtype=np.uint8)
        gammal_mark_z = np.full(form, 12.0, dtype=np.float32)

        # Nytt ortofoto: gräs överallt utom:
        # - Parkering på f.d. gräs: 10x10 pixlar = 100 pixlar = 6.25 m²
        # - Ny byggnad: 8x8 pixlar = 64 pixlar = 4.00 m² (+6 m i DSM)
        bild = np.zeros((40, 40, 4), dtype=np.uint8)
        bild[:, :] = np.array([68, 140, 52, 195], dtype=np.uint8)  # Gräs
        bild[4:14, 4:14] = np.array([75, 78, 82, 70], dtype=np.uint8)  # Ny asfaltparkering
        bild[20:28, 20:28] = np.array([150, 72, 62, 90], dtype=np.uint8)  # Nytt tak (ej i gammal)

        ny_dsm_z = np.full(form, 12.0, dtype=np.float32)
        ny_dsm_z[20:28, 20:28] = 18.0  # +6 m byggnad

        ny_klassning = klassificera_ortofoto(bild)
        rapport = detektera_forandringar_mot_laser(
            gammal_markslag,
            ny_klassning,
            pixelstorlek_m=0.25,
            gammal_mark_z=gammal_mark_z,
            ny_dsm_z=ny_dsm_z,
            hojd_troskel_m=1.5,
        )

        # Parkering (100 px) -> ny hårdgörning (6.25 m²)
        # Ny byggnad (64 px) klassas också som HARDGJORT i RGB+NIR utan byggnad_mask
        # men är framför allt ny byggnad via DSM (+6 m) -> båda zonerna ska fångas korrekt
        self.assertEqual(int(np.count_nonzero(rapport.nya_byggnader_mask)), 64)
        self.assertAlmostEqual(rapport.nya_byggnader_m2, 4.0, places=5)
        self.assertGreaterEqual(rapport.nya_hardgjorda_m2, 6.25)
        self.assertEqual(
            int(np.count_nonzero(rapport.forandringsmask)),
            int(np.count_nonzero(rapport.nya_hardgjorda_mask | rapport.nya_byggnader_mask)),
        )
        self.assertGreaterEqual(len(rapport.varningar), 2)
        self.assertTrue(any("Ny hårdgörning" in v for v in rapport.varningar))
        self.assertTrue(any("Nya byggnader" in v for v in rapport.varningar))

        rapport_dict = rapport.till_dict()
        self.assertAlmostEqual(rapport_dict["nya_byggnader_m2"], 4.0, places=5)

    def test_kombinera_ortofoto_med_markmodell_bevarar_byggnader_och_okar_osakerhet(self) -> None:
        bild, ndsm, byggnad_mask, _, forvantad_skugga = _skapa_syntetisk_stadsmiljo(med_nir=True)
        klassning = klassificera_ortofoto(bild, ndsm_m=ndsm, byggnad_mask=byggnad_mask)

        markslag_bas = np.full((80, 80), OKANT, dtype=np.uint8)
        # Baskartans byggnader (både vanligt och grönt tak markeras som BYGGNAD i basen)
        markslag_bas[40:80, 20:60] = BYGGNAD
        # Befintlig väg i basen som inte får skrivas över
        markslag_bas[0:10, 0:20] = HARDGJORT

        osakerhet_bas = np.full((80, 80), 0.06, dtype=np.float32)

        # Simulera en förändringszon i övre vänstra asfaltområdet (ej där basen redan var HARDGJORT)
        gammal = np.full((80, 80), GRAS, dtype=np.uint8)
        gammal[40:80, 20:60] = BYGGNAD
        rapport = detektera_forandringar_mot_laser(gammal, klassning, pixelstorlek_m=0.25)

        params = OrtofotoParametrar()
        ny_markslag, ny_osakerhet = kombinera_ortofoto_med_markmodell(
            markslag_bas,
            osakerhet_bas,
            klassning,
            forandringar=rapport,
            parametrar=params,
        )

        # Baskartans vanliga byggnad bevaras som BYGGNAD
        self.assertTrue(np.all(ny_markslag[40:80, 20:40] == BYGGNAD))
        # Grönt tak uppdateras till GRONT_TAK
        self.assertTrue(np.all(ny_markslag[40:80, 40:60] == GRONT_TAK))
        # Okända solbelysta gräs/skogs/vattenceller fylls i från ortofotot
        self.assertTrue(np.all(ny_markslag[0:40, 20:40] == GRAS))
        self.assertTrue(np.all(ny_markslag[0:40, 40:60] == SKOG))
        self.assertTrue(np.all(ny_markslag[40:80, 0:20] == VATTEN))

        # Osäkerheten ökar exakt enligt tilläggen i skugga och förändringszon
        self.assertTrue(
            np.allclose(
                ny_osakerhet[forvantad_skugga],
                0.06 + params.osakerhet_skugga_tillagg_m,
                atol=1e-5,
            )
        )
        self.assertTrue(
            np.allclose(
                ny_osakerhet[0:40, 0:20],
                0.06 + params.osakerhet_forandring_tillagg_m,
                atol=1e-5,
            )
        )
        # Opåverkad gräscell behåller grundosäkerhet
        self.assertAlmostEqual(float(ny_osakerhet[10, 25]), 0.06, places=5)

    def test_rendera_markslag_png_base64(self) -> None:
        markslag = np.array(
            [
                [HARDGJORT, GRAS, SKOG, GRUS],
                [VATTEN, BYGGNAD, GRONT_TAK, OKANT],
            ],
            dtype=np.uint8,
        )
        res = rendera_markslag_png_base64(markslag)
        self.assertEqual(res["bredd_px"], 4)
        self.assertEqual(res["hojd_px"], 2)
        self.assertTrue(res["data_uri"].startswith("data:image/png;base64,"))

        raw = base64.b64decode(res["png_base64"])
        self.assertTrue(raw.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(io.BytesIO(raw)) as img:
            self.assertEqual(img.size, (4, 2))
            self.assertEqual(img.mode, "RGBA")

    def test_validering_felhantering(self) -> None:
        with self.assertRaises(OrtofotoFel):
            berakna_vegetationsindex(np.zeros((10, 10), dtype=np.uint8))
        with self.assertRaises(OrtofotoFel):
            berakna_vegetationsindex(np.full((4, 4, 3), 1.5, dtype=np.float32))
        with self.assertRaises(OrtofotoFel):
            klassificera_ortofoto(
                np.zeros((10, 10, 3), dtype=np.uint8),
                ndsm_m=np.zeros((8, 10), dtype=np.float32),
            )
        with self.assertRaises(OrtofotoFel):
            OrtofotoParametrar(skugga_luminans_troskel=1.5)


if __name__ == "__main__":
    unittest.main()
