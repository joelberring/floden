"""Tester för LandXML TIN-import, K3/v1 JSON-I/O, gatusektion och lapptillämpning (T04)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from lapp.kontrakt import (
    KONTRAKT_K3,
    HojdYta,
    LappByggnad,
    LappFel,
    LappGata,
    LappMagasin,
    LappMarkslag,
    ProjektLapp,
    las_gatusektioner_fran_konfig,
    las_lapp_k3,
    skriv_lapp_k3,
)
from lapp.landxml import las_landxml_tin, rastrera_landxml_tin
from lapp.tillampa import rastrera_parametrisk_gata, tillampa_lapp
from sankgraf.hierarki import bygg_sankgraf
from sankgraf.typer import MARKSLAG_BYGGNAD
from testsvit.k1_io import EPSG_SWEREF99_1800, GeoTransformInfo

_REPO_ROT = Path(__file__).resolve().parents[2]


def _skapa_geo(
    bredd: int,
    hojd: int,
    *,
    x_min: float = 150000.0,
    y_max: float = 6580000.0,
    pixel: float = 0.25,
) -> GeoTransformInfo:
    return GeoTransformInfo(
        bredd=bredd,
        hojd=hojd,
        x_min=x_min,
        y_max=y_max,
        pixelstorlek_x=pixel,
        pixelstorlek_y=pixel,
        epsg=EPSG_SWEREF99_1800,
        dtype="float32",
    )


class TestLandXmlOchK3(unittest.TestCase):
    """Verifierar LandXML-barycentrik, K3-kontrakt och parametrisk 12 m-gata."""

    def test_landxml_lutande_plan_exakt_barycentrisk_hojd(self) -> None:
        """Barycentrisk interpolation på lutande triangelplan ska ge exakt höjd (< 1e-5 m)."""
        geo = _skapa_geo(40, 40, x_min=150000.0, y_max=6580010.0, pixel=0.25)
        e0, n0 = 150001.0, 6580001.0
        e1, n1 = 150009.0, 6580001.0
        e2, n2 = 150009.0, 6580009.0
        e3, n3 = 150001.0, 6580009.0

        # Exakt lutande plan: z(E, N) = 12.5 + 0.15 * (E - e0) - 0.08 * (N - n0)
        def plan_z(e_val: float, n_val: float) -> float:
            return 12.5 + 0.15 * (e_val - e0) - 0.08 * (n_val - n0)

        # Standard LandXML använder koordinatordningen Northing Easting Elevation (NEZ)
        xml_str = f"""<?xml version="1.0" encoding="UTF-8"?>
        <LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2" version="1.2">
          <Surfaces>
            <Surface name="projektyta-plan">
              <Definition surfType="TIN">
                <Pnts>
                  <P id="1">{n0:.4f} {e0:.4f} {plan_z(e0, n0):.6f}</P>
                  <P id="2">{n1:.4f} {e1:.4f} {plan_z(e1, n1):.6f}</P>
                  <P id="3">{n2:.4f} {e2:.4f} {plan_z(e2, n2):.6f}</P>
                  <P id="4">{n3:.4f} {e3:.4f} {plan_z(e3, n3):.6f}</P>
                </Pnts>
                <Faces>
                  <F>1 2 3</F>
                  <F>1 3 4</F>
                  <F i="1">2 4 3</F>
                </Faces>
              </Definition>
            </Surface>
          </Surfaces>
        </LandXML>
        """
        yta = las_landxml_tin(xml_str)
        self.assertEqual(yta.namn, "projektyta-plan")
        self.assertEqual(yta.antal_punkter, 4)
        self.assertEqual(yta.antal_trianglar, 2)  # Osynlig triangel (i="1") ska ignoreras

        z_raster, tackt = rastrera_landxml_tin(yta, geo)
        self.assertEqual(z_raster.shape, (40, 40))
        self.assertEqual(z_raster.dtype, np.dtype(np.float32))
        self.assertEqual(tackt.dtype, np.dtype(np.bool_))
        # 8 m x 8 m vid 0,25 m upplösning = 32 x 32 = 1024 täckta celler
        self.assertEqual(int(np.sum(tackt)), 32 * 32)
        self.assertTrue(bool(np.all(np.isnan(z_raster[~tackt]))))

        # Verifiera exakt planekvation i varje täckt cellcentrum
        rader, kolumner = np.nonzero(tackt)
        max_fel = 0.0
        for r_np, c_np in zip(rader, kolumner, strict=True):
            r, c = int(r_np), int(c_np)
            ec = geo.x_min + (c + 0.5) * geo.pixelstorlek_x
            nc = geo.y_max - (r + 0.5) * geo.pixelstorlek_y
            facit = plan_z(ec, nc)
            fel = abs(float(z_raster[r, c]) - facit)
            if fel > max_fel:
                max_fel = fel
        self.assertLess(max_fel, 1e-5)

        # Kontrollera även konfigurerbar koordinatordning ENZ
        xml_enz = f"""<LandXML>
          <Surfaces>
            <Surface name="enz-triangel">
              <Definition>
                <Pnts>
                  <P id="a">{e0} {n0} 10.0</P>
                  <P id="b">{e1} {n1} 11.0</P>
                  <P id="c">{e2} {n2} 12.0</P>
                </Pnts>
                <Faces><F>a b c</F></Faces>
              </Definition>
            </Surface>
          </Surfaces>
        </LandXML>"""
        yta_enz = las_landxml_tin(xml_enz, koordinat_ordning="ENZ")
        self.assertEqual(yta_enz.punkter["a"], (e0, n0, 10.0))
        self.assertEqual(yta_enz.punkter["b"], (e1, n1, 11.0))

    def test_k3_skriv_och_las_roundtrip(self) -> None:
        """ProjektLapp enligt K3/v1 ska serialiseras, läsas tillbaka och valideras strikt."""
        lapp = ProjektLapp(
            lapp_id="rosteriet-alt-a",
            bas_recept=("a" * 64,),
            beskrivning="Testprojekt med byggnad, gata, magasin och höjdyta",
            hojdytor=(
                HojdYta(
                    yta_id="plan-1",
                    polygon=(
                        (150002.0, 6580002.0),
                        (150008.0, 6580002.0),
                        (150008.0, 6580008.0),
                        (150002.0, 6580008.0),
                    ),
                    hojd_rh2000=14.2,
                ),
            ),
            byggnader=(
                LappByggnad(
                    byggnad_id="hus-1",
                    polygon=(
                        (150003.0, 6580003.0),
                        (150006.0, 6580003.0),
                        (150006.0, 6580006.0),
                        (150003.0, 6580006.0),
                    ),
                    entre_rh2000=14.5,
                    entre_punkt=(150004.5, 6580002.75),
                ),
            ),
            gator=(
                LappGata(
                    gata_id="gata-1",
                    mittlinje=((150000.0, 6580015.0), (150020.0, 6580015.0)),
                    sektion="standard-12m",
                    profil_rh2000=(15.0, 14.6),
                ),
            ),
            magasin=(
                LappMagasin(
                    magasin_id="mag-1",
                    polygon=(
                        (150010.0, 6580002.0),
                        (150014.0, 6580002.0),
                        (150014.0, 6580006.0),
                        (150010.0, 6580006.0),
                    ),
                    volym_m3=12.0,
                ),
            ),
            markslag_andringar=(
                LappMarkslag(
                    omrade_id="park-1",
                    polygon=(
                        (150015.0, 6580002.0),
                        (150018.0, 6580002.0),
                        (150018.0, 6580005.0),
                        (150015.0, 6580005.0),
                    ),
                    markslag=4,
                ),
            ),
        )
        self.assertEqual(lapp.kontrakt, KONTRAKT_K3)
        self.assertEqual(len(lapp.operationer), 5)

        with tempfile.TemporaryDirectory() as tmpdir:
            fil = skriv_lapp_k3(lapp, Path(tmpdir) / "lapp.json")
            inlast = las_lapp_k3(fil)
            self.assertEqual(inlast, lapp)
            self.assertEqual(inlast.lapp_sha256(), lapp.lapp_sha256())

            ogiltig_fil = Path(tmpdir) / "fel.json"
            ogiltig_fil.write_text('{"kontrakt": "K3/v99", "lapp_id": "x"}', encoding="utf-8")
            with self.assertRaises(LappFel):
                las_lapp_k3(ogiltig_fil)

    def test_parametrisk_gatusektion_standard_12m_fran_konfig(self) -> None:
        """Gatusektionen standard-12m ska ge exakt krön, tvärfall, kantstenssprång och gångbana."""
        sektioner = las_gatusektioner_fran_konfig(_REPO_ROT / "config.toml")
        self.assertIn("standard-12m", sektioner)
        sek = sektioner["standard-12m"]
        self.assertAlmostEqual(sek.total_bredd_m, 12.0)
        self.assertAlmostEqual(sek.korbana_bredd_m, 6.5)
        self.assertAlmostEqual(sek.kantsten_hojd_m, 0.12)
        self.assertAlmostEqual(sek.tvarfall_korbana_andel, 0.025)
        self.assertAlmostEqual(sek.tvarfall_gangbana_andel, 0.02)

        # 20 m x 20 m rutnät (80 x 80 celler à 0,25 m), öst-västlig gata längs N = y_max - 10.0
        geo = _skapa_geo(80, 80, x_min=150000.0, y_max=6580020.0, pixel=0.25)
        bas_z = np.full((80, 80), 20.0, dtype=np.float32)
        bas_slag = np.full((80, 80), 4, dtype=np.uint8)
        bas_z_kopia = bas_z.copy()

        n_mitt = geo.y_max - 10.0
        gata = LappGata(
            gata_id="huvudgata",
            mittlinje=((150002.0, n_mitt), (150018.0, n_mitt)),
            sektion="standard-12m",
            profil_rh2000=(20.0, 20.0),
        )
        res = rastrera_parametrisk_gata(gata, sek, bas_z, bas_slag, geo)

        # Basrastrerna får aldrig muteras
        np.testing.assert_array_equal(bas_z, bas_z_kopia)
        self.assertEqual(len(res.brytlinjer), 2)
        self.assertEqual(res.brytlinjer[0].typ, "kantsten")
        self.assertAlmostEqual(res.brytlinjer[0].hojd_m, 0.12)

        # Kontrollera tvärprofil vid mittkolumnen (c = 40, E = 150010.125)
        kol = 40
        # Närmast mittlinjen: d = 0.125 m (rad 39 och 40)
        z_kron = float(res.mark_z[40, kol])
        self.assertAlmostEqual(z_kron, 20.0 - 0.025 * 0.125, places=5)

        # Yttersta körbanecellen före kantsten: d = 3.125 m (rad 52: 52.5*0.25 - 10.0 = 3.125)
        z_rannsten_cell = float(res.mark_z[52, kol])
        self.assertAlmostEqual(z_rannsten_cell, 20.0 - 0.025 * 3.125, places=5)

        # Första gångbanecellen direkt utanför kantstenen: d = 3.375 m (rad 53)
        z_gang_inner = float(res.mark_z[53, kol])
        forvantad_gang_inner = (20.0 - 0.025 * 3.25) + 0.12 + 0.02 * (3.375 - 3.25)
        self.assertAlmostEqual(z_gang_inner, forvantad_gang_inner, places=5)
        # Kantstenssprånget ska höja gångbanan tydligt ovanför rännstenen
        self.assertGreater(z_gang_inner - z_rannsten_cell, 0.11)

        # Yttersta gångbanecellen: d = 5.875 m (rad 63) ska vara högre än inre gångbanan
        z_gang_ytter = float(res.mark_z[63, kol])
        self.assertGreater(z_gang_ytter, z_gang_inner)
        self.assertEqual(int(res.markslag[40, kol]), 1)
        self.assertEqual(int(res.markslag[53, kol]), 1)

    def test_tillampa_lapp_muterar_ej_bas_och_skapar_exakt_magasinvolym(self) -> None:
        """tillampa_lapp ska bevara basrastrer, länka bas_recept_sha256 och ge exakt volym."""
        geo = _skapa_geo(40, 40, x_min=150000.0, y_max=6580010.0, pixel=0.25)
        bas_z = np.full((40, 40), 10.0, dtype=np.float32)
        bas_osak = np.full((40, 40), 0.05, dtype=np.float32)
        bas_slag = np.full((40, 40), 4, dtype=np.uint8)
        bas_z_ref = bas_z.copy()
        bas_slag_ref = bas_slag.copy()

        lapp = ProjektLapp(
            lapp_id="mag-och-hus",
            bas_recept=("1" * 64,),
            magasin=(
                LappMagasin(
                    magasin_id="m1",
                    polygon=(
                        (150002.0, 6580002.0),
                        (150006.0, 6580002.0),
                        (150006.0, 6580006.0),
                        (150002.0, 6580006.0),
                    ),
                    volym_m3=8.0,
                ),
            ),
            byggnader=(
                LappByggnad(
                    byggnad_id="b1",
                    polygon=(
                        (150007.0, 6580007.0),
                        (150009.0, 6580007.0),
                        (150009.0, 6580009.0),
                        (150007.0, 6580009.0),
                    ),
                    entre_rh2000=10.3,
                    entre_punkt=(150006.75, 6580008.0),
                ),
            ),
        )

        till = tillampa_lapp(
            bas_z, bas_osak, bas_slag, geo, lapp, bas_recept_sha256="1" * 64
        )
        np.testing.assert_array_equal(bas_z, bas_z_ref)
        np.testing.assert_array_equal(bas_slag, bas_slag_ref)
        self.assertEqual(till.bas_recept_sha256, "1" * 64)
        self.assertEqual(till.recept_data["bas_recept_sha256"], "1" * 64)
        self.assertEqual(len(till.nytt_recept_sha256), 64)
        self.assertNotEqual(till.nytt_recept_sha256, till.bas_recept_sha256)
        self.assertEqual(len(till.registrerade_entreer), 1)
        self.assertTrue(bool(np.any(till.markslag == MARKSLAG_BYGGNAD)))

        # Verifiera i sänkgrafen att magasinet har exakt 8,0 m³ lagringsvolym
        graf = bygg_sankgraf(till.mark_z, till.osakerhet_z, till.markslag, geo)
        self.assertEqual(len(graf.lovnoder()), 1)
        self.assertAlmostEqual(graf.lovnoder()[0].max_volym_m3, 8.0, places=4)


if __name__ == "__main__":
    unittest.main()
