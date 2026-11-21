"""Enhetstester för `markmodell.grundkarta` (DXF-import, topologi, laserpassning, barriär-IDW)."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from markmodell.brytlinjer import Brytlinje
from markmodell.grundkarta import (
    STANDARD_STOCKHOLM_LAGERMAPPNING,
    GrundkartaFel,
    LagerMappning,
    forenkla_linje_dp,
    hojdsatt_brytlinjer_fran_laser,
    interpolera_med_brytlinjer,
    las_baskarta_dxf,
    rensa_baskarte_topologi,
    stang_husliv_till_byggnader,
)
from markmodell.interpolering import LAS_BROKLASS, LAS_MARKKLASS, interpolera_markpunkter
from markmodell.rutnat import Ruta


def _bygg_syntetisk_dxf() -> str:
    """Bygger en komplett ASCII-DXF med LINE, LWPOLYLINE, POLYLINE/VERTEX/SEQEND m.m."""
    rader: list[str] = [
        "0",
        "SECTION",
        "2",
        "HEADER",
        "0",
        "ENDSEC",
        "0",
        "SECTION",
        "2",
        "ENTITIES",
        # --- 4 separata LINE-entiteter för husliv med 5 cm glapp vid varje hörn ---
        # Sydvägg (150000.00, 6576000.00) -> (150010.00, 6576000.00)
        "0",
        "LINE",
        "8",
        "HUSLIV",
        "10",
        "150000.00",
        "20",
        "6576000.00",
        "11",
        "150010.00",
        "21",
        "6576000.00",
        # Östvägg med 5 cm glapp i söder och norr
        "0",
        "LINE",
        "8",
        "husliv",
        "10",
        "150010.03",
        "20",
        "6576000.04",
        "11",
        "150010.00",
        "21",
        "6576008.00",
        # Nordvägg (ritad i omvänd riktning med 5 cm glapp)
        "0",
        "LINE",
        "8",
        "HUSLIV_HUVUD",
        "10",
        "150000.04",
        "20",
        "6576008.03",
        "11",
        "150009.97",
        "21",
        "6576008.04",
        # Västvägg med 5 cm glapp vid båda ändar
        "0",
        "LINE",
        "8",
        "HUSLIV",
        "10",
        "150000.00",
        "20",
        "6576008.00",
        "11",
        "150000.03",
        "21",
        "6576000.04",
        # --- Öppet husliv (3 väggar, 6 m glapp) -> ska INTE stängas utan ge varning ---
        "0",
        "LINE",
        "8",
        "BYGGNAD_OPPEN",
        "10",
        "150030.0",
        "20",
        "6576000.0",
        "11",
        "150036.0",
        "21",
        "6576000.0",
        "0",
        "LINE",
        "8",
        "BYGGNAD_OPPEN",
        "10",
        "150036.0",
        "20",
        "6576000.0",
        "11",
        "150036.0",
        "21",
        "6576005.0",
        "0",
        "LINE",
        "8",
        "BYGGNAD_OPPEN",
        "10",
        "150036.0",
        "20",
        "6576005.0",
        "11",
        "150030.0",
        "21",
        "6576005.0",
        # --- LWPOLYLINE kantsten ---
        "0",
        "LWPOLYLINE",
        "8",
        "KANTSTEN_BEF",
        "90",
        "3",
        "70",
        "0",
        "10",
        "150000.0",
        "20",
        "6576015.0",
        "10",
        "150005.0",
        "20",
        "6576015.0",
        "10",
        "150010.0",
        "20",
        "6576015.0",
        # --- POLYLINE + VERTEX + SEQEND för stödmur ---
        "0",
        "POLYLINE",
        "8",
        "STODMUR",
        "66",
        "1",
        "70",
        "0",
        "0",
        "VERTEX",
        "8",
        "STODMUR",
        "10",
        "150000.0",
        "20",
        "6576020.0",
        "0",
        "VERTEX",
        "8",
        "STODMUR",
        "10",
        "150008.0",
        "20",
        "6576020.0",
        "0",
        "VERTEX",
        "8",
        "STODMUR",
        "10",
        "150012.0",
        "20",
        "6576023.0",
        "0",
        "SEQEND",
        # --- Trumma med Z-värde ---
        "0",
        "LINE",
        "8",
        "TRUMMA",
        "10",
        "150015.0",
        "20",
        "6576010.0",
        "30",
        "9.5",
        "11",
        "150020.0",
        "21",
        "6576010.0",
        "31",
        "9.5",
        # --- Okänt lager ---
        "0",
        "LINE",
        "8",
        "ELKABEL_OKAND",
        "10",
        "150000.0",
        "20",
        "6576030.0",
        "11",
        "150010.0",
        "21",
        "6576030.0",
        "0",
        "ENDSEC",
        "0",
        "EOF",
    ]
    return "\n".join(rader) + "\n"


def _generera_steg_punktmoln(
    ruta: Ruta,
    grans_n: float,
    z_soder: float,
    z_norr: float,
    steg_m: float = 0.50,
    brus_sigma_m: float = 0.0,
    fro: int = 123,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Genererar regelbundet markpunktmoln med skarpt höjdsprång vid `n = grans_n`."""
    e_min, n_min, e_max, n_max = ruta.yttre_bbox
    e_vals = np.arange(e_min + 0.5 * steg_m, e_max, steg_m, dtype=np.float64)
    n_vals = np.arange(n_min + 0.5 * steg_m, n_max, steg_m, dtype=np.float64)
    # Undvik punkter exakt på brytlinjen så båda sidor har väldefinierad nivå
    n_vals = n_vals[np.abs(n_vals - grans_n) > 0.02]
    ge, gn = np.meshgrid(e_vals, n_vals)
    pe = ge.ravel()
    pn = gn.ravel()
    pz = np.where(pn > grans_n, z_norr, z_soder).astype(np.float64)
    if brus_sigma_m > 0.0:
        rng = np.random.default_rng(fro)
        pz = pz + rng.normal(0.0, brus_sigma_m, size=pz.size)
    return pe, pn, pz


class TestGrundkarta(unittest.TestCase):
    def test_lagermappning_och_standardtabell(self) -> None:
        self.assertIn("KANTSTEN", STANDARD_STOCKHOLM_LAGERMAPPNING)
        self.assertIn("HUSLIV", STANDARD_STOCKHOLM_LAGERMAPPNING)
        self.assertIn("STODMUR", STANDARD_STOCKHOLM_LAGERMAPPNING)
        self.assertIn("TRUMMA", STANDARD_STOCKHOLM_LAGERMAPPNING)
        self.assertIn("PORT", STANDARD_STOCKHOLM_LAGERMAPPNING)

        regel = LagerMappning(prefix="kantsten", roll="kantsten", standard_m=0.12)
        self.assertEqual(regel.prefix, "KANTSTEN")
        self.assertTrue(regel.matchar("KANTSTEN"))
        self.assertTrue(regel.matchar("kantsten_bef"))
        self.assertFalse(regel.matchar("MUR_01"))

        with self.assertRaises(GrundkartaFel):
            LagerMappning(prefix="", roll="kantsten", standard_m=0.12)
        with self.assertRaises(GrundkartaFel):
            LagerMappning(prefix="BAD", roll="okand_roll", standard_m=1.0)
        with self.assertRaises(GrundkartaFel):
            LagerMappning(prefix="BYGG", roll="byggnad", standard_m=0.0)

    def test_forenkla_linje_dp_behaller_andpunkter_och_horn(self) -> None:
        punkter = (
            (0.0, 0.0),
            (2.0, 0.005),
            (5.0, 0.0),
            (5.0, 4.0),
            (5.004, 7.0),
            (5.0, 10.0),
        )
        forenklad = forenkla_linje_dp(punkter, tolerans_m=0.02)
        self.assertEqual(forenklad, ((0.0, 0.0), (5.0, 0.0), (5.0, 10.0)))
        self.assertEqual(forenklad[0], punkter[0])
        self.assertEqual(forenklad[-1], punkter[-1])

        with self.assertRaises(GrundkartaFel):
            forenkla_linje_dp(punkter, tolerans_m=-0.01)

    def test_dxf_parsing_line_lwpolyline_polyline_husliv_och_okant_lager(self) -> None:
        dxf_text = _bygg_syntetisk_dxf()
        res = las_baskarta_dxf(dxf_text, ar_text=True, snap_tolerans_m=0.25)

        # 4 separata LINE-husliv med 5 cm glapp -> exakt 1 sluten byggnad
        self.assertEqual(len(res.byggnader), 1)
        self.assertEqual(res.topologi.antal_stangda_byggnader, 1)
        bygg = res.byggnader[0]
        self.assertEqual(bygg.polygon[0], bygg.polygon[-1])
        self.assertEqual(len(bygg.polygon), 5)

        # Öppet husliv -> varning och inte tyst stängd
        self.assertEqual(res.topologi.antal_oppna_husliv_varningar, 1)
        self.assertTrue(any("Öppet husliv" in v for v in res.varningar))

        # LWPOLYLINE kantsten (förenklad från 3 kolinjära punkter till 2) + POLYLINE stödmur
        self.assertEqual(len(res.brytlinjer), 2)
        typer = {b.typ for b in res.brytlinjer}
        self.assertEqual(typer, {"kantsten", "stodmur"})
        kant = next(b for b in res.brytlinjer if b.typ == "kantsten")
        stodmur = next(b for b in res.brytlinjer if b.typ == "stodmur")
        self.assertEqual(len(kant.punkter), 2)
        self.assertEqual(len(stodmur.punkter), 3)
        self.assertAlmostEqual(stodmur.hojd_m, 1.5)

        # Trumma med botten_rh2000 från DXF Z-kod 30/31
        self.assertEqual(len(res.oppningar), 1)
        self.assertEqual(res.oppningar[0].typ, "trumma")
        self.assertAlmostEqual(res.oppningar[0].botten_rh2000, 9.5)

        # Okänt lager rapporteras tydligt
        self.assertEqual(res.okanda_lager, ("ELKABEL_OKAND",))
        self.assertTrue(any("ELKABEL_OKAND" in v for v in res.varningar))

        # Kontrollera läsning från fil på disk + determinism
        with tempfile.TemporaryDirectory() as tmpdir:
            dxf_path = Path(tmpdir) / "baskarta.dxf"
            dxf_path.write_text(dxf_text, encoding="utf-8")
            res_fil = las_baskarta_dxf(dxf_path, ar_text=False, snap_tolerans_m=0.25)
        self.assertEqual(res_fil, res)

    def test_dxf_ogiltig_koordinat_ger_tydligt_fel(self) -> None:
        ogiltig_dxf = "\n".join(
            [
                "0",
                "SECTION",
                "2",
                "ENTITIES",
                "0",
                "LINE",
                "8",
                "KANTSTEN",
                "10",
                "nan",
                "20",
                "6576000.0",
                "11",
                "150005.0",
                "21",
                "6576000.0",
                "0",
                "ENDSEC",
                "0",
                "EOF",
            ]
        )
        with self.assertRaises(GrundkartaFel):
            las_baskarta_dxf(ogiltig_dxf, ar_text=True)

    def test_stang_husliv_radhus_med_delad_vagg(self) -> None:
        # Två radhus (0..5 och 5..10 i E, 0..6 i N) som delar mittvägg vid E = 5
        segment = [
            ((0.0, 0.0), (5.0, 0.0)),
            ((5.02, 0.01), (10.0, 0.0)),
            ((10.0, 0.02), (10.0, 6.0)),
            ((10.0, 6.01), (5.0, 6.0)),
            ((4.98, 6.01), (0.0, 6.0)),
            ((0.0, 5.98), (0.0, 0.02)),
            ((5.0, 0.02), (5.01, 5.99)),  # delad mellanvägg
        ]
        byggnader, oppna = stang_husliv_till_byggnader(segment, tolerans_m=0.25)
        self.assertEqual(len(byggnader), 2)
        self.assertEqual(len(oppna), 0)

    def test_topologirensning_dubblett_degenererad_kolinjar_och_snapping(self) -> None:
        l_kolinjar = Brytlinje(
            typ="kantsten",
            hojd_m=0.12,
            punkter=(
                (150000.0, 6576000.0),
                (150002.0, 6576000.0),
                (150004.0, 6576000.005),
                (150006.0, 6576000.0),
                (150010.0, 6576000.0),
            ),
        )
        # Omvänd dubblett inom 2 cm
        l_omvand_dubblett = Brytlinje(
            typ="kantsten",
            hojd_m=0.12,
            punkter=(
                (150010.01, 6576000.01),
                (150000.01, 6576000.01),
            ),
        )
        # Degenererad linje (längd 5e-5 m < 1e-4 m)
        l_degenererad = Brytlinje(
            typ="mur",
            hojd_m=1.0,
            punkter=((150020.0, 6576000.0), (150020.00005, 6576000.0)),
        )
        # Anslutande kantsten med 12 cm glapp -> ska snappas
        l_anslutande = Brytlinje(
            typ="kantsten",
            hojd_m=0.12,
            punkter=((150010.12, 6576000.0), (150010.12, 6576008.0)),
        )

        rensade, rapport = rensa_baskarte_topologi(
            [l_kolinjar, l_omvand_dubblett, l_degenererad, l_anslutande],
            snap_tolerans_m=0.25,
            dubblett_tolerans_m=0.05,
            forenkling_tolerans_m=0.02,
        )
        self.assertEqual(rapport.antal_segment_in, 4)
        self.assertEqual(rapport.antal_degenererade_borttagna, 1)
        self.assertEqual(rapport.antal_dubbletter_borttagna, 1)
        self.assertEqual(rapport.antal_forenklade_punkter, 3)
        self.assertEqual(len(rensade), 2)
        self.assertEqual(len(rensade[0].punkter), 2)
        # Lösa änden snappad ihop exakt
        self.assertEqual(rensade[0].punkter[-1], rensade[1].punkter[0])

    def test_barriar_idw_vs_vanlig_idw_stodmur_1_50m_och_kantsten_0_12m(self) -> None:
        # 40 x 40 celler (10 x 10 m vid 0,25 m upplösning, inget överlapp)
        ruta = Ruta(
            e_min_m=150000.0,
            n_min_m=6576000.0,
            storlek_m=10.0,
            overlapp_m=0.0,
            upplosning_m=0.25,
        )
        self.assertEqual(ruta.form_rc, (40, 40))
        grans_n = 6576005.0
        _, n_vektor = ruta.cell_centra_en()
        # Rader direkt intill brytlinjen (0,125 m norr resp. söder om gränsen)
        rad_norr = int(np.argmin(np.abs(n_vektor - (grans_n + 0.125))))
        rad_soder = int(np.argmin(np.abs(n_vektor - (grans_n - 0.125))))

        for typ, sprang_m, min_vanligt_fel_m in (
            ("stodmur", 1.50, 0.35),
            ("kantsten", 0.12, 0.030),
        ):
            with self.subTest(typ=typ, sprang_m=sprang_m):
                z_soder = 10.0
                z_norr = 10.0 + sprang_m
                pe, pn, pz = _generera_steg_punktmoln(
                    ruta, grans_n=grans_n, z_soder=z_soder, z_norr=z_norr, steg_m=0.50
                )
                barriar = Brytlinje(
                    typ=typ,
                    hojd_m=sprang_m,
                    punkter=((149995.0, grans_n), (150015.0, grans_n)),
                )

                res_barriar = interpolera_med_brytlinjer(
                    pe, pn, pz, ruta, [barriar], max_avstand_m=2.0
                )
                z_vanlig, _, _ = interpolera_markpunkter(
                    pe, pn, pz, ruta, max_avstand_m=2.0
                )

                self.assertEqual(res_barriar.antal_blockerade_celler, 0)
                # Kontrollera inre kolumner (undvik ytterkantseffekter)
                barriar_fel_norr = float(
                    np.max(np.abs(res_barriar.mark_z[rad_norr, 2:-2] - z_norr))
                )
                barriar_fel_soder = float(
                    np.max(np.abs(res_barriar.mark_z[rad_soder, 2:-2] - z_soder))
                )
                vanlig_fel_norr = float(
                    np.max(np.abs(z_vanlig[rad_norr, 2:-2] - z_norr))
                )
                vanlig_fel_soder = float(
                    np.max(np.abs(z_vanlig[rad_soder, 2:-2] - z_soder))
                )

                # Barriär-IDW ger < 1 mm fel direkt intill kanten på båda sidor
                self.assertLess(barriar_fel_norr, 0.001)
                self.assertLess(barriar_fel_soder, 0.001)
                # Vanlig IDW smetar ut språnget kraftigt på båda sidor
                self.assertGreater(vanlig_fel_norr, min_vanligt_fel_m)
                self.assertGreater(vanlig_fel_soder, min_vanligt_fel_m)

    def test_barriar_idw_prestanda_80x80_reserv_och_instangd_cell(self) -> None:
        # 80 x 80 celler (20 x 20 m vid 0,25 m upplösning) -> ska gå på < 0,5 s
        ruta = Ruta(
            e_min_m=150000.0,
            n_min_m=6576000.0,
            storlek_m=20.0,
            overlapp_m=0.0,
            upplosning_m=0.25,
        )
        pe, pn, pz = _generera_steg_punktmoln(
            ruta, grans_n=6576010.0, z_soder=10.0, z_norr=11.5, steg_m=0.50
        )
        mur = Brytlinje(
            typ="stodmur",
            hojd_m=1.5,
            punkter=((149998.0, 6576010.0), (150022.0, 6576010.0)),
        )
        t0 = time.perf_counter()
        res = interpolera_med_brytlinjer(pe, pn, pz, ruta, [mur], max_avstand_m=3.0)
        tid_s = time.perf_counter() - t0
        self.assertLess(tid_s, 0.5)
        self.assertEqual(res.mark_z.shape, (80, 80))
        self.assertEqual(res.antal_blockerade_celler, 0)

        # Reservsökning bortom max_avstand_m: punkter långt bort på rätt sida hittas
        # utan att korsa muren trots att närmare punkter finns på andra sidan.
        liten_ruta = Ruta(
            e_min_m=150000.0,
            n_min_m=6576000.0,
            storlek_m=6.0,
            overlapp_m=0.0,
            upplosning_m=0.5,
        )
        mur_del = Brytlinje(
            typ="stodmur",
            hojd_m=1.5,
            punkter=((149998.0, 6576003.0), (150008.0, 6576003.0)),
        )
        # Punkt på norra sidan ligger på 4 m avstånd (> max_avstand_m=1.5),
        # medan punkt på södra sidan ligger bara 0,5 m bort men på fel sida muren.
        res_reserv = interpolera_med_brytlinjer(
            np.array([150003.0, 150003.0], dtype=np.float64),
            np.array([6576002.75, 6576007.25], dtype=np.float64),
            np.array([10.0, 11.5], dtype=np.float64),
            liten_ruta,
            [mur_del],
            max_avstand_m=1.5,
        )
        self.assertEqual(res_reserv.antal_blockerade_celler, 0)
        rad_n, kol_n = liten_ruta.koordinat_till_cell(150003.0, 6576003.25)
        self.assertAlmostEqual(float(res_reserv.mark_z[rad_n, kol_n]), 11.5, places=4)

        # Sluten barriär utan markpunkter inuti -> cellerna inuti flaggas som blockerade
        pe_s, pn_s, pz_s = _generera_steg_punktmoln(
            liten_ruta, grans_n=6575990.0, z_soder=10.0, z_norr=10.0, steg_m=0.50
        )
        utanfor = ~(
            (pe_s >= 150002.0)
            & (pe_s <= 150004.0)
            & (pn_s >= 6576002.0)
            & (pn_s <= 6576004.0)
        )
        sluten_ring = Brytlinje(
            typ="mur",
            hojd_m=1.0,
            punkter=(
                (150002.0, 6576002.0),
                (150004.0, 6576002.0),
                (150004.0, 6576004.0),
                (150002.0, 6576004.0),
                (150002.0, 6576002.0),
            ),
        )
        res_stangd = interpolera_med_brytlinjer(
            pe_s[utanfor],
            pn_s[utanfor],
            pz_s[utanfor],
            liten_ruta,
            [sluten_ring],
            max_avstand_m=2.0,
        )
        self.assertGreater(res_stangd.antal_blockerade_celler, 0)
        self.assertEqual(
            res_stangd.antal_blockerade_celler,
            int(np.count_nonzero(res_stangd.barriar_blockerad_mask)),
        )
        rad_inuti, kol_inuti = liten_ruta.koordinat_till_cell(150003.0, 6576003.0)
        self.assertTrue(bool(res_stangd.barriar_blockerad_mask[rad_inuti, kol_inuti]))

    def test_laser_hojdsattning_och_sidoforskjutning_0_20m(self) -> None:
        # Kantsten ritad vid N = 6576005.0, men verkligt 0,12 m språng i laser ligger förskjutet
        # 0,20 m i sidled (både +0,20 m åt vänster och -0,20 m åt höger testas).
        kantsten = Brytlinje(
            typ="kantsten",
            hojd_m=0.05,  # felaktig initial höjd som ska uppdateras från laser
            punkter=((150000.0, 6576005.0), (150020.0, 6576005.0)),
        )
        ensam_linje = Brytlinje(
            typ="mur",
            hojd_m=1.0,
            punkter=((150200.0, 6576200.0), (150210.0, 6576200.0)),
        )

        rng = np.random.default_rng(77)
        n_pts = 800
        pe = rng.uniform(150000.0, 150020.0, size=n_pts)
        pn = rng.uniform(6576003.0, 6576007.0, size=n_pts)
        brus = rng.normal(0.0, 0.008, size=n_pts)

        # Lägg till bropunkter (klass 17) med +4 m som måste filtreras bort
        n_bro = 50
        bro_e = np.linspace(150001.0, 150019.0, n_bro, dtype=np.float64)
        bro_n = np.full(n_bro, 6576004.5, dtype=np.float64)
        bro_z = np.full(n_bro, 14.0, dtype=np.float64)
        alla_e = np.concatenate([pe, bro_e])
        alla_n = np.concatenate([pn, bro_n])
        klasser = np.concatenate(
            [
                np.full(pe.size, LAS_MARKKLASS, dtype=np.uint8),
                np.full(n_bro, LAS_BROKLASS, dtype=np.uint8),
            ]
        )

        for sann_offset in (0.20, -0.20):
            with self.subTest(sann_offset=sann_offset):
                pz = np.where(pn >= 6576005.0 + sann_offset, 10.12, 10.00) + brus
                alla_z = np.concatenate([pz, bro_z])

                uppdaterade, analyser = hojdsatt_brytlinjer_fran_laser(
                    [kantsten, ensam_linje],
                    alla_e,
                    alla_n,
                    alla_z,
                    sidoband_min_m=0.15,
                    sidoband_max_m=1.5,
                    sok_sidoforskjutning_max_m=0.5,
                    klasser=klasser,
                )

                self.assertEqual(len(uppdaterade), 2)
                self.assertEqual(len(analyser), 2)

                a0 = analyser[0]
                self.assertFalse(a0.osaker)
                self.assertIsNotNone(a0.uppmatt_sprang_m)
                self.assertIsNotNone(a0.sidoforskjutning_m)
                assert a0.uppmatt_sprang_m is not None
                assert a0.sidoforskjutning_m is not None
                self.assertAlmostEqual(a0.uppmatt_sprang_m, 0.12, delta=0.01)
                self.assertAlmostEqual(uppdaterade[0].hojd_m, a0.uppmatt_sprang_m, places=9)
                self.assertAlmostEqual(a0.sidoforskjutning_m, sann_offset, delta=0.06)

                a1 = analyser[1]
                self.assertTrue(a1.osaker)
                self.assertIsNone(a1.uppmatt_sprang_m)
                self.assertAlmostEqual(uppdaterade[1].hojd_m, 1.0)


if __name__ == "__main__":
    unittest.main()
