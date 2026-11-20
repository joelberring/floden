"""Dynamiska valideringstester för syntetiska fall A1–A4, A9 och skibord/gångtid (T03)."""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

import numpy as np

from dynamik import (
    DynamikFel,
    EntrePunkt,
    applicera_initial_forlust,
    kor_dynamik_handelsestyrd,
    kor_dynamik_tidssteg,
    overfallsflode_m3_s,
    skapa_regn_intervall,
    utvardera_entreer,
)
from sankgraf.fyllning import kor_statisk_k1_mapp
from sankgraf.typer import SankGraf, SankNod, VolymPunkt, YtPunkt
from testsvit.k1_io import EPSG_SWEREF99_1800, GeoTransformInfo

REPO_ROT = Path(__file__).resolve().parents[2]
TESTSVIT_MAPP = REPO_ROT / "testsvit"


def _ladda_fall_och_facit(mappnamn: str) -> tuple[SankGraf, dict[str, object]]:
    fall_mapp = TESTSVIT_MAPP / mappnamn
    graf, _ = kor_statisk_k1_mapp(fall_mapp / "indata", regn_m=0.05)
    facit_raw: dict[str, object] = json.loads(
        (fall_mapp / "facit" / "facit.json").read_text(encoding="utf-8")
    )
    return graf, facit_raw


def _bygg_exakt_a2_graf() -> SankGraf:
    """Bygger en ren A2-graf med facits exakta avrinningsytor (161,0 och 164,5 m²)."""
    mark = np.full((10, 20), 12.0, dtype=np.float32)
    mark[2:6, 2:6] = 11.0
    mark[2:6, 12:16] = 10.0
    slag = np.ones((10, 20), dtype=np.uint8)
    ct = np.full((10, 20), -1, dtype=np.int64)
    ct[:, :10] = 1
    ct[:, 10:] = 0
    geo = GeoTransformInfo(
        bredd=20,
        hojd=10,
        x_min=150000.0,
        y_max=6576002.5,
        pixelstorlek_x=0.25,
        pixelstorlek_y=0.25,
        epsg=EPSG_SWEREF99_1800,
        dtype="float32",
    )
    nod_nedre = SankNod(
        nod_id=0,
        forald_id=None,
        barn_id=(),
        overfall_rh2000=10.40,
        overfall_x=150004.0,
        overfall_y=6576001.0,
        ut_till_id=None,
        volymkurva=(VolymPunkt(10.0, 0.0), VolymPunkt(10.40, 22.40)),
        ytkurva=(YtPunkt(10.0, 56.0), YtPunkt(10.40, 56.0)),
        avrinningsyta_m2=164.5,
        persistens_m=0.40,
        osakerhet_m=0.02,
        botten_rh2000=10.0,
        cell_antal=100,
    )
    nod_ovre = SankNod(
        nod_id=1,
        forald_id=None,
        barn_id=(),
        overfall_rh2000=11.30,
        overfall_x=150002.0,
        overfall_y=6576001.0,
        ut_till_id=0,
        volymkurva=(VolymPunkt(11.0, 0.0), VolymPunkt(11.30, 10.80)),
        ytkurva=(YtPunkt(11.0, 36.0), YtPunkt(11.30, 36.0)),
        avrinningsyta_m2=161.0,
        persistens_m=0.30,
        osakerhet_m=0.02,
        botten_rh2000=11.0,
        cell_antal=100,
    )
    return SankGraf(
        noder={0: nod_nedre, 1: nod_ovre},
        cell_till_nod=ct,
        flodesvagar=[],
        geo=geo,
        filtrerad=False,
        persistens_faktor=None,
        mark_z=mark,
        markslag=slag,
        ut_lov_id={0: None, 1: 0},
    )


def _bygg_exakt_a3_graf() -> SankGraf:
    """Bygger en ren A3-graf med facits exakta symmetriska avrinningsytor (186,25 m²/halva)."""
    mark = np.full((10, 20), 10.80, dtype=np.float32)
    mark[2:6, 2:6] = 10.0
    mark[2:6, 12:16] = 10.1
    slag = np.ones((10, 20), dtype=np.uint8)
    ct = np.full((10, 20), -1, dtype=np.int64)
    ct[:, :10] = 0
    ct[:, 10:] = 1
    geo = GeoTransformInfo(
        bredd=20,
        hojd=10,
        x_min=150200.0,
        y_max=6576002.5,
        pixelstorlek_x=0.25,
        pixelstorlek_y=0.25,
        epsg=EPSG_SWEREF99_1800,
        dtype="float32",
    )
    nod_v = SankNod(
        nod_id=0,
        forald_id=2,
        barn_id=(),
        overfall_rh2000=10.30,
        overfall_x=150202.5,
        overfall_y=6576001.0,
        ut_till_id=1,
        volymkurva=(VolymPunkt(10.0, 0.0), VolymPunkt(10.30, 14.40)),
        ytkurva=(YtPunkt(10.0, 48.0), YtPunkt(10.30, 48.0)),
        avrinningsyta_m2=186.25,
        persistens_m=0.30,
        osakerhet_m=0.02,
        botten_rh2000=10.0,
        cell_antal=100,
    )
    nod_h = SankNod(
        nod_id=1,
        forald_id=2,
        barn_id=(),
        overfall_rh2000=10.30,
        overfall_x=150202.5,
        overfall_y=6576001.0,
        ut_till_id=0,
        volymkurva=(VolymPunkt(10.1, 0.0), VolymPunkt(10.30, 9.60)),
        ytkurva=(YtPunkt(10.1, 48.0), YtPunkt(10.30, 48.0)),
        avrinningsyta_m2=186.25,
        persistens_m=0.20,
        osakerhet_m=0.02,
        botten_rh2000=10.1,
        cell_antal=100,
    )
    nod_p = SankNod(
        nod_id=2,
        forald_id=None,
        barn_id=(0, 1),
        overfall_rh2000=10.60,
        overfall_x=150204.5,
        overfall_y=6576001.0,
        ut_till_id=None,
        volymkurva=(
            VolymPunkt(10.0, 0.0),
            VolymPunkt(10.1, 4.80),
            VolymPunkt(10.30, 24.0),
            VolymPunkt(10.60, 84.0),
        ),
        ytkurva=(
            YtPunkt(10.0, 48.0),
            YtPunkt(10.1, 96.0),
            YtPunkt(10.30, 200.0),
            YtPunkt(10.60, 200.0),
        ),
        avrinningsyta_m2=0.0,
        persistens_m=0.60,
        osakerhet_m=0.02,
        botten_rh2000=10.0,
        cell_antal=200,
    )
    return SankGraf(
        noder={0: nod_v, 1: nod_h, 2: nod_p},
        cell_till_nod=ct,
        flodesvagar=[],
        geo=geo,
        filtrerad=False,
        persistens_faktor=None,
        mark_z=mark,
        markslag=slag,
        ut_lov_id={0: 1, 1: 0, 2: None},
    )


class TestAFallDynamik(unittest.TestCase):
    """Validerar dynamiklösarna mot A1–A4, A9 samt fysiska överfalls- och fördröjningsegenskaper."""

    def test_a1_fyllnadstid_och_volymbalans(self) -> None:
        graf, facit = _ladda_fall_och_facit("A1_enkel_sanka")
        intens = float(facit["intensitet_mm_h"])  # type: ignore[arg-type]
        t_facit = float(facit["t_overfall_s"])  # type: ignore[arg-type]
        slut_tid_s = 13000.0

        res_h = kor_dynamik_handelsestyrd(graf, [0.0, slut_tid_s], [intens, intens])
        res_t = kor_dynamik_tidssteg(
            graf, [0.0, slut_tid_s], [intens, intens], dt_s=5.0, overfall_lage="omedelbar"
        )

        for res in (res_h, res_t):
            self.assertEqual(res.overfallsordning, [0])
            self.assertLess(res.volymfel_rel, 0.001)
            t_ov = res.noder[0].t_overfall_s
            self.assertIsNotNone(t_ov)
            assert t_ov is not None
            rel_fel_facit = abs(t_ov - t_facit) / t_facit
            self.assertLess(rel_fel_facit, 0.01)

            # Maxdjup i bassängbotten exakt 0,50 m (< 1 cm fel)
            botten_djup = float(res.max_djup_m[30, 30])
            self.assertLess(abs(botten_djup - 0.50), 0.01)

            # Ankomsttider i botten: exakt 20 % och 40 % av t_overfall för vertikalväggad bassäng
            t01 = float(res.tid_till_0_1m_s[30, 30])
            t02 = float(res.tid_till_0_2m_s[30, 30])
            self.assertAlmostEqual(t01, 0.2 * t_ov, delta=2.0)
            self.assertAlmostEqual(t02, 0.4 * t_ov, delta=2.0)
            # Utanför bassängen (hög krage) nås aldrig 0,1 m -> NaN
            self.assertTrue(math.isnan(float(res.tid_till_0_1m_s[2, 2])))

        # Händelsestyrd och tidsstegad överensstämmer till < 0,01 %
        assert res_h.noder[0].t_overfall_s is not None
        assert res_t.noder[0].t_overfall_s is not None
        self.assertLess(
            abs(res_h.noder[0].t_overfall_s - res_t.noder[0].t_overfall_s)
            / res_h.noder[0].t_overfall_s,
            1e-4,
        )

    def test_a2_overfallsordning_och_tidpunkter_dem_och_exakt_facit(self) -> None:
        graf_dem, facit = _ladda_fall_och_facit("A2_tva_sankor_serie")
        intens = float(facit["intensitet_mm_h"])  # type: ignore[arg-type]
        ovre_facit: dict[str, float] = facit["ovre"]  # type: ignore[assignment]
        nedre_facit: dict[str, float] = facit["nedre"]  # type: ignore[assignment]
        t_ovre_facit = float(ovre_facit["t_overfall_s"])
        t_nedre_facit = float(nedre_facit["t_overfall_s"])
        q_m_s = intens / 1000.0 / 3600.0

        # 1. På DEM-grafen: exakt överfallsordning [ovre=1, nedre=0], exakt match mot grafens
        # analytiska fyllnadstider och exakt tidskvot t_nedre / t_ovre mot facit
        ovre_id = max(graf_dem.noder, key=lambda nid: graf_dem.noder[nid].botten_rh2000)
        nedre_id = min(graf_dem.noder, key=lambda nid: graf_dem.noder[nid].botten_rh2000)
        a_ovre = graf_dem.noder[ovre_id].avrinningsyta_m2
        a_nedre = graf_dem.noder[nedre_id].avrinningsyta_m2
        v_ovre = graf_dem.noder[ovre_id].max_volym_m3
        v_nedre = graf_dem.noder[nedre_id].max_volym_m3
        t_ovre_graf = v_ovre / (a_ovre * q_m_s)
        t_nedre_graf = t_ovre_graf + (v_nedre - t_ovre_graf * a_nedre * q_m_s) / (
            (a_ovre + a_nedre) * q_m_s
        )

        res_h_dem = kor_dynamik_handelsestyrd(graf_dem, [0.0, 6500.0], [intens, intens])
        res_t_dem = kor_dynamik_tidssteg(
            graf_dem, [0.0, 6500.0], [intens, intens], dt_s=2.0, overfall_lage="omedelbar"
        )
        for res in (res_h_dem, res_t_dem):
            self.assertEqual(res.overfallsordning, [ovre_id, nedre_id])
            self.assertLess(res.volymfel_rel, 0.001)
            t_ov = res.noder[ovre_id].t_overfall_s
            t_ne = res.noder[nedre_id].t_overfall_s
            self.assertIsNotNone(t_ov)
            self.assertIsNotNone(t_ne)
            assert t_ov is not None and t_ne is not None
            self.assertLess(abs(t_ov - t_ovre_graf) / t_ovre_graf, 1e-4)
            self.assertLess(abs(t_ne - t_nedre_graf) / t_nedre_graf, 1e-4)
            kvot_graf = t_ne / t_ov
            kvot_facit = t_nedre_facit / t_ovre_facit
            self.assertLess(abs(kvot_graf - kvot_facit) / kvot_facit, 0.01)

        # 2. På exakt facit-A2-graf (avrinningsytor 161,0 och 164,5 m²): < 0,01 % mot facit.json
        graf_exakt = _bygg_exakt_a2_graf()
        res_h_ex = kor_dynamik_handelsestyrd(graf_exakt, [0.0, 6500.0], [intens, intens])
        res_t_ex = kor_dynamik_tidssteg(
            graf_exakt, [0.0, 6500.0], [intens, intens], dt_s=2.0, overfall_lage="omedelbar"
        )
        for res in (res_h_ex, res_t_ex):
            self.assertEqual(res.overfallsordning, [1, 0])
            self.assertLess(res.volymfel_rel, 0.001)
            t_ov = res.noder[1].t_overfall_s
            t_ne = res.noder[0].t_overfall_s
            assert t_ov is not None and t_ne is not None
            self.assertLess(abs(t_ov - t_ovre_facit) / t_ovre_facit, 0.001)
            self.assertLess(abs(t_ne - t_nedre_facit) / t_nedre_facit, 0.001)

    def test_a3_sammanslagning_och_yttre_overfall(self) -> None:
        graf_dem, facit = _ladda_fall_och_facit("A3_tva_sankor_sammanslagning")
        intens = float(facit["intensitet_mm_h"])  # type: ignore[arg-type]
        t_hoger_facit = float(facit["t_hoger_nar_sadel_s"])  # type: ignore[arg-type]
        t_merge_facit = float(facit["t_sammanslagning_s"])  # type: ignore[arg-type]
        t_yttre_facit = float(facit["t_yttre_overfall_s"])  # type: ignore[arg-type]
        q_m_s = intens / 1000.0 / 3600.0

        # Identifiera vänster (botten 10.0), höger (botten 10.1) och förälder (rot)
        rot_id = next(nid for nid, n in graf_dem.noder.items() if n.forald_id is None)
        lov_ids = [nid for nid, n in graf_dem.noder.items() if n.ar_lov]
        hoger_id = max(lov_ids, key=lambda nid: graf_dem.noder[nid].botten_rh2000)
        t_hoger_graf = graf_dem.noder[hoger_id].max_volym_m3 / (
            graf_dem.noder[hoger_id].avrinningsyta_m2 * q_m_s
        )

        res_h = kor_dynamik_handelsestyrd(graf_dem, [0.0, 13000.0], [intens, intens])
        res_t = kor_dynamik_tidssteg(
            graf_dem, [0.0, 13000.0], [intens, intens], dt_s=5.0, overfall_lage="omedelbar"
        )
        for res in (res_h, res_t):
            self.assertLess(res.volymfel_rel, 0.001)
            self.assertEqual(res.overfallsordning, [hoger_id, rot_id])
            t_hoger = res.noder[hoger_id].t_overfall_s
            t_merge = res.noder[rot_id].t_sammanslagning_s
            t_yttre = res.noder[rot_id].t_overfall_s
            assert t_hoger is not None and t_merge is not None and t_yttre is not None

            # Sammanslagningstid och yttre överfallstid inom < 1 % direkt mot facit.json
            self.assertLess(abs(t_merge - t_merge_facit) / t_merge_facit, 0.01)
            self.assertLess(abs(t_yttre - t_yttre_facit) / t_yttre_facit, 0.01)
            # Höger sadeltid exakt (< 0,01 %) mot DEM-grafens analytiska avrinningsyta
            self.assertLess(abs(t_hoger - t_hoger_graf) / t_hoger_graf, 1e-4)

            # Maxdjup i vänster botten = 0,60 m och i höger botten = 0,50 m
            self.assertLess(abs(float(res.max_djup_m[30, 30]) - 0.60), 0.01)
            self.assertLess(abs(float(res.max_djup_m[30, 70]) - 0.50), 0.01)

        # Exakt facit-A3-graf (symmetrisk 186,25 m²/halva): alla tre tider < 0,01 % mot facit.json
        graf_ex = _bygg_exakt_a3_graf()
        res_h_ex = kor_dynamik_handelsestyrd(graf_ex, [0.0, 13000.0], [intens, intens])
        res_t_ex = kor_dynamik_tidssteg(
            graf_ex, [0.0, 13000.0], [intens, intens], dt_s=5.0, overfall_lage="omedelbar"
        )
        for res in (res_h_ex, res_t_ex):
            self.assertLess(res.volymfel_rel, 0.001)
            t_h = res.noder[1].t_overfall_s
            t_m = res.noder[2].t_sammanslagning_s
            t_y = res.noder[2].t_overfall_s
            assert t_h is not None and t_m is not None and t_y is not None
            self.assertLess(abs(t_h - t_hoger_facit) / t_hoger_facit, 1e-3)
            self.assertLess(abs(t_m - t_merge_facit) / t_merge_facit, 1e-3)
            self.assertLess(abs(t_y - t_yttre_facit) / t_yttre_facit, 1e-3)

    def test_a4_lutande_plan_noll_noder(self) -> None:
        graf, _ = _ladda_fall_och_facit("A4_lutande_plan")
        self.assertEqual(len(graf.noder), 0)

        res_h = kor_dynamik_handelsestyrd(graf, [0.0, 1800.0], [50.0, 50.0])
        res_t = kor_dynamik_tidssteg(graf, [0.0, 1800.0], [50.0, 50.0], dt_s=10.0)
        for res in (res_h, res_t):
            self.assertEqual(len(res.noder), 0)
            self.assertEqual(res.overfallsordning, [])
            self.assertEqual(res.lagrat_m3, 0.0)
            self.assertAlmostEqual(res.ut_m3, res.in_m3, places=9)
            self.assertLess(res.volymfel_rel, 0.001)
            self.assertEqual(float(np.max(res.max_djup_m)), 0.0)
            self.assertTrue(bool(np.all(np.isnan(res.tid_till_0_1m_s))))
            self.assertTrue(bool(np.all(np.isnan(res.tid_till_0_2m_s))))

    def test_a9_flernivatrad_matchar_statisk_fyllning(self) -> None:
        graf, stat = kor_statisk_k1_mapp(
            TESTSVIT_MAPP / "A9_brus_persistens" / "indata", regn_m=0.05
        )
        self.assertGreater(len(graf.noder), 50)
        res = kor_dynamik_handelsestyrd(graf, [0.0, 3600.0], [50.0, 50.0])
        self.assertLess(res.volymfel_rel, 0.001)
        max_diff = float(np.max(np.abs(res.max_djup_m - stat.djup_m)))
        self.assertLess(max_diff, 1e-4)

    def test_skibord_och_varierande_regn_och_gangtid(self) -> None:
        graf, _ = _ladda_fall_och_facit("A2_tva_sankor_serie")
        # Regn med efterförlopp (torrt efter 6500 s så skibordsöverhöjden dräneras)
        tider = [0.0, 6500.0]
        intens = [72.0, 72.0]

        res_om = kor_dynamik_tidssteg(
            graf, tider, intens, dt_s=5.0, slut_tid_s=7500.0, overfall_lage="omedelbar"
        )
        res_smal = kor_dynamik_tidssteg(
            graf,
            tider,
            intens,
            dt_s=5.0,
            slut_tid_s=7500.0,
            overfall_lage="skibord",
            overfall_bredd_m=0.5,
        )
        res_bred = kor_dynamik_tidssteg(
            graf,
            tider,
            intens,
            dt_s=5.0,
            slut_tid_s=7500.0,
            overfall_lage="skibord",
            overfall_bredd_m=20.0,
        )

        self.assertLess(res_smal.volymfel_rel, 0.001)
        self.assertLess(res_bred.volymfel_rel, 0.001)

        # Smalare skibord ger större överhöjd och större fördröjning till nedre sänkan (nod 0)
        max_z_smal = max(res_smal.noder[1].niva_rh2000)
        max_z_bred = max(res_bred.noder[1].niva_rh2000)
        self.assertGreater(max_z_smal, graf.noder[1].overfall_rh2000 + 0.005)
        self.assertGreater(max_z_smal, max_z_bred)

        assert res_om.noder[0].t_overfall_s is not None
        assert res_smal.noder[0].t_overfall_s is not None
        assert res_bred.noder[0].t_overfall_s is not None
        self.assertGreater(res_smal.noder[0].t_overfall_s, res_om.noder[0].t_overfall_s)
        self.assertLess(
            abs(res_bred.noder[0].t_overfall_s - res_om.noder[0].t_overfall_s),
            abs(res_smal.noder[0].t_overfall_s - res_om.noder[0].t_overfall_s),
        )
        # Efter regnets slut (t=7500 s) har vattennivån sjunkit tillbaka mot överfallskanten
        self.assertLess(res_smal.noder[1].niva_rh2000[-1], max_z_smal)

        # Styckvis linjärt (triangelformat) regn: händelsestyrd == tidsstegad
        tri_t = [0.0, 3000.0, 6000.0]
        tri_i = [0.0, 144.0, 0.0]
        res_h_tri = kor_dynamik_handelsestyrd(graf, tri_t, tri_i)
        res_t_tri = kor_dynamik_tidssteg(graf, tri_t, tri_i, dt_s=2.0, overfall_lage="omedelbar")
        self.assertLess(res_h_tri.volymfel_rel, 0.001)
        self.assertLess(res_t_tri.volymfel_rel, 0.001)
        assert res_h_tri.noder[1].t_overfall_s is not None
        assert res_t_tri.noder[1].t_overfall_s is not None
        self.assertLess(
            abs(res_h_tri.noder[1].t_overfall_s - res_t_tri.noder[1].t_overfall_s)
            / res_h_tri.noder[1].t_overfall_s,
            1e-3,
        )

        # Gångtidsfördröjning ger massbevarande senare fyllning
        res_gang = kor_dynamik_tidssteg(
            graf, [0.0, 6500.0], [72.0, 72.0], dt_s=5.0, gangtid_fordrojning=True
        )
        self.assertLess(res_gang.volymfel_rel, 0.001)
        assert res_gang.noder[1].t_overfall_s is not None
        assert res_om.noder[1].t_overfall_s is not None
        self.assertGreater(res_gang.noder[1].t_overfall_s, res_om.noder[1].t_overfall_s)

    def test_nodmodell_hjalpfunktioner_och_entreer(self) -> None:
        # Bredskronsöverfall Q = Cd * B * dh^1.5
        self.assertEqual(overfallsflode_m3_s(10.0, 10.5, 2.0), 0.0)
        q_ber = overfallsflode_m3_s(10.75, 10.50, 2.0, cd=1.7)
        self.assertAlmostEqual(q_ber, 1.7 * 2.0 * math.pow(0.25, 1.5), places=12)
        with self.assertRaises(DynamikFel):
            overfallsflode_m3_s(10.6, 10.5, -1.0)

        # Initialförlust
        iv, tot = skapa_regn_intervall([0.0, 3600.0], [36.0, 36.0])
        self.assertAlmostEqual(tot, 0.036, places=12)
        netto_iv, netto_m = applicera_initial_forlust(iv, 6.0)
        self.assertAlmostEqual(netto_m, 0.030, places=12)
        self.assertEqual(len(netto_iv), 2)

        # Entréutvärdering på A1
        graf, _ = _ladda_fall_och_facit("A1_enkel_sanka")
        res = kor_dynamik_handelsestyrd(graf, [0.0, 13000.0], [72.0, 72.0])
        e_botten = graf.geo.x_min + 30.5 * graf.geo.pixelstorlek_x
        n_botten = graf.geo.y_max - 30.5 * graf.geo.pixelstorlek_y
        e_kant = graf.geo.x_min + 2.5 * graf.geo.pixelstorlek_x
        n_kant = graf.geo.y_max - 2.5 * graf.geo.pixelstorlek_y
        entreer = [
            EntrePunkt("E1", e_botten, n_botten, gransdjup_m=0.10),
            EntrePunkt("E2", e_botten, n_botten, gransdjup_m=0.25),
            EntrePunkt("E3", e_kant, n_kant, gransdjup_m=0.10),
        ]
        ut = utvardera_entreer(res, graf, entreer)
        self.assertAlmostEqual(ut[0].max_djup_m, 0.50, places=2)
        self.assertIsNotNone(ut[0].tid_till_gransdjup_s)
        assert ut[0].tid_till_gransdjup_s is not None and ut[1].tid_till_gransdjup_s is not None
        self.assertGreater(ut[1].tid_till_gransdjup_s, ut[0].tid_till_gransdjup_s)
        self.assertEqual(ut[2].max_djup_m, 0.0)
        self.assertIsNone(ut[2].tid_till_gransdjup_s)


if __name__ == "__main__":
    unittest.main()
