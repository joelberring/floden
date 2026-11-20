"""Enhets- och fysiktester för parametriska gatuprofiler (T01b)."""

from __future__ import annotations

import math
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from markmodell.gatuprofiler import (
    TYP_GANGBANA_LUTNING,
    TYP_KANTSTEN_HOJD_M,
    TYP_OSAKERHET_Z_M,
    TYP_TVARFALL,
    GatuKorridor,
    GatuProfilFel,
    InfartSankning,
    SektionsProfil,
    brytlinjer_till_poster,
    hitta_rannstens_lagpunkter,
    passa_tvarsnitt_huber,
    rastrera_gatuprofil,
    rekonstruera_gatuprofil,
    rita_tvarsnitt_png,
    utjamna_profil_bandad,
    utvardera_sektionsprofil,
)
from markmodell.hinder_oppningar import GRAS, HARDGJORT
from markmodell.rutnat import Ruta
from sankgraf.fyllning import statisk_fyllning
from sankgraf.hierarki import bygg_sankgraf
from testsvit.k1_io import GeoTransformInfo


def _generera_syntetiskt_punktmoln(
    korridor: GatuKorridor,
    *,
    nock_z_fn_start: float = 10.08,
    langslutning: float = 0.0,
    sanka_djup_m: float = 0.0,
    tvarfall: float = 0.025,
    kantsten_m: float = 0.12,
    gangbana_lutning: float = 0.02,
    brus_sigma_m: float = 0.008,
    outlier_andel: float = 0.15,
    outlier_hojd_m: float = 1.50,
    punkter_per_m2: float = 12.0,
    fro: int = 42,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Genererar syntetiskt laserpunktmoln (inkl. parkerade bilar) längs en rak östgående gata."""
    rng = np.random.default_rng(fro)
    e0, n0 = korridor.mittlinje[0]
    e1, n1 = korridor.mittlinje[-1]
    langd = math.hypot(e1 - e0, n1 - n0)
    te = (e1 - e0) / langd
    tn = (n1 - n0) / langd
    he = tn
    hn = -te

    tot_w = korridor.total_halvbredd_m
    w_k = korridor.korbana_halvbredd_m
    antal = int(round(langd * (2.0 * tot_w) * punkter_per_m2))

    s_arr = rng.uniform(0.0, langd, size=antal)
    d_arr = rng.uniform(-tot_w, tot_w, size=antal)

    e_arr = e0 + s_arr * te + d_arr * he
    n_arr = n0 + s_arr * tn + d_arr * hn

    # Längsgående nockhöjd (linjär lutning + ev. parabolisk svacka i mitten)
    s_norm = (s_arr - 0.5 * langd) / max(0.5 * langd, 1e-6)
    nock_z = nock_z_fn_start + langslutning * s_arr - sanka_djup_m * (1.0 - s_norm * s_norm)

    if korridor.bombering:
        z_korbana = nock_z - tvarfall * np.abs(d_arr)
        z_rv = nock_z - tvarfall * w_k
        z_rh = nock_z - tvarfall * w_k
    else:
        z_korbana = nock_z - tvarfall * d_arr
        z_rv = nock_z + tvarfall * w_k
        z_rh = nock_z - tvarfall * w_k

    kv_arr = np.full(antal, kantsten_m, dtype=np.float64)
    kh_arr = np.full(antal, kantsten_m, dtype=np.float64)
    for inf in korridor.infarter:
        i_mask = (s_arr >= inf.s_start_m) & (s_arr <= inf.s_slut_m)
        if inf.galler_sida("vanster"):
            kv_arr = np.where(i_mask, inf.kantsten_hojd_m, kv_arr)
        if inf.galler_sida("hoger"):
            kh_arr = np.where(i_mask, inf.kantsten_hojd_m, kh_arr)

    z_gv = z_rv + kv_arr + gangbana_lutning * (-d_arr - w_k)
    z_gh = z_rh + kh_arr + gangbana_lutning * (d_arr - w_k)
    z_sann = np.where(d_arr < -w_k, z_gv, np.where(d_arr > w_k, z_gh, z_korbana))
    z_arr = z_sann + rng.normal(0.0, brus_sigma_m, size=antal)

    # Lägg till parkerade bilar (+1,5 m outliers)
    if outlier_andel > 0.0:
        n_out = int(round(antal * outlier_andel))
        out_idx = rng.choice(antal, size=n_out, replace=False)
        z_arr[out_idx] += rng.uniform(0.8 * outlier_hojd_m, 1.2 * outlier_hojd_m, size=n_out)

    return (
        np.asarray(e_arr, dtype=np.float64),
        np.asarray(n_arr, dtype=np.float64),
        np.asarray(z_arr, dtype=np.float64),
    )


class TestGatuprofiler(unittest.TestCase):
    """Testsvit för T01b – Gatuprofiler."""

    def test_huber_robust_mot_parkerade_bilar(self) -> None:
        """15 % parkerade bilar (+1,5 m) ger medelfel < 0,01 m i kantsten och < 0,002 i tvärfall."""
        korridor = GatuKorridor(
            gata_id="test-storgatan",
            mittlinje=((150000.0, 6580000.0), (150040.0, 6580000.0)),
            korbana_halvbredd_m=3.25,
            gangbana_bredd_m=2.75,
            bombering=True,
        )
        pe, pn, pz = _generera_syntetiskt_punktmoln(
            korridor,
            nock_z_fn_start=10.08,
            tvarfall=0.025,
            kantsten_m=0.12,
            gangbana_lutning=0.02,
            brus_sigma_m=0.008,
            outlier_andel=0.15,
            outlier_hojd_m=1.50,
            punkter_per_m2=14.0,
            fro=123,
        )

        res = rekonstruera_gatuprofil(korridor, pe, pn, pz, sektionsavstand_m=2.0)
        self.assertGreater(len(res.sektioner), 15)
        self.assertTrue(all(not s.ar_typvarde for s in res.sektioner))

        kantstensfel: list[float] = []
        tvarfallsfel: list[float] = []
        for s in res.sektioner:
            kantstensfel.append(abs(s.kantsten_vanster_m - 0.12))
            kantstensfel.append(abs(s.kantsten_hoger_m - 0.12))
            tvarfallsfel.append(abs(s.tvarfall_vanster - 0.025))
            tvarfallsfel.append(abs(s.tvarfall_hoger - 0.025))

        medelfel_kantsten_m = float(np.mean(kantstensfel))
        medelfel_tvarfall = float(np.mean(tvarfallsfel))

        self.assertLess(medelfel_kantsten_m, 0.01)
        self.assertLess(medelfel_tvarfall, 0.002)

    def test_huber_bilar_koncentrerade_pa_korbana_och_enkelsidigt_tvarfall(self) -> None:
        """Kontrollerar enskild sektionspassning vid bilar längs rännsten och enkelsidigt fall."""
        rng = np.random.default_rng(77)
        w_k = 3.25
        w_g = 2.75
        d = rng.uniform(-(w_k + w_g), w_k + w_g, size=240)
        sann = SektionsProfil(
            s_m=10.0,
            nock_z_rh2000=12.50,
            tvarfall_vanster=-0.030,
            tvarfall_hoger=0.030,
            kantsten_vanster_m=0.11,
            kantsten_hoger_m=0.13,
            gangbana_lutning_vanster=0.02,
            gangbana_lutning_hoger=0.02,
            osakerhet_z_m=0.01,
            antal_punkter=240,
            ar_typvarde=False,
        )
        z = utvardera_sektionsprofil(sann, d, w_k) + rng.normal(0.0, 0.006, size=d.size)

        # 15 % parkerade bilar placerade specifikt längs höger körbanekant (d i [1.2, 3.2])
        kandidater = np.flatnonzero((d >= 1.2) & (d <= 3.2))
        n_bilar = min(36, int(kandidater.size // 3))
        bilar = rng.choice(kandidater, size=n_bilar, replace=False)
        z[bilar] += 1.55

        passad = passa_tvarsnitt_huber(
            d,
            z,
            s_m=10.0,
            korbana_halvbredd_m=w_k,
            gangbana_bredd_m=w_g,
            bombering=False,
        )
        self.assertFalse(passad.ar_typvarde)
        self.assertLess(abs(passad.kantsten_vanster_m - 0.11), 0.01)
        self.assertLess(abs(passad.kantsten_hoger_m - 0.13), 0.01)
        self.assertLess(abs(passad.tvarfall_hoger - 0.030), 0.002)
        self.assertAlmostEqual(passad.tvarfall_vanster, -passad.tvarfall_hoger, places=10)

    def test_bandad_thomas_exakt_mot_tat_losare_och_linjar_tid(self) -> None:
        """Thomas-utjämnaren matchar tät np.linalg.solve (< 1e-12) och kör 500 sektioner < 50 ms."""
        rng = np.random.default_rng(9)
        m = 60
        sann = 10.0 + 0.05 * np.sin(np.linspace(0.0, 2.0 * math.pi, m))
        y = sann + rng.normal(0.0, 0.03, size=m)
        w = rng.uniform(10.0, 100.0, size=m)
        # Sätt även en datalucka i mitten för att pröva grannstöd
        w[25:30] = 0.0
        lam = 40.0

        x_thomas, std_thomas = utjamna_profil_bandad(y, w, lam)

        # Bygg motsvarande täta tridiagonala matris T
        t_mat = np.zeros((m, m), dtype=np.float64)
        for i in range(m):
            t_mat[i, i] = float(w[i])
        for i in range(m - 1):
            t_mat[i, i] += lam
            t_mat[i + 1, i + 1] += lam
            t_mat[i, i + 1] -= lam
            t_mat[i + 1, i] -= lam

        x_tat = np.linalg.solve(t_mat, w * y)
        std_tat = np.sqrt(np.diag(np.linalg.inv(t_mat)))

        max_fel_x = float(np.max(np.abs(x_thomas - x_tat)))
        max_fel_std = float(np.max(np.abs(std_thomas - std_tat)))
        self.assertLess(max_fel_x, 1e-12)
        self.assertLess(max_fel_std, 1e-12)

        # Brusreducering jämfört med råsignalen (utanför dataluckan)
        obs = w > 0.0
        diff_fore = y[obs] - sann[obs]
        diff_efter = x_thomas[obs] - sann[obs]
        rmse_fore = float(np.sqrt(np.mean(diff_fore * diff_fore)))
        rmse_efter = float(np.sqrt(np.mean(diff_efter * diff_efter)))
        self.assertLess(rmse_efter, rmse_fore)

        # Högre osäkerhet inne i dataluckan än på observerade grannar
        self.assertGreater(float(std_thomas[27]), float(std_thomas[20]))

        # Körtidstest på 500 sektioner (< 50 ms)
        m_stor = 500
        y_stor = rng.normal(10.0, 0.05, size=m_stor)
        w_stor = rng.uniform(50.0, 200.0, size=m_stor)
        t0 = time.perf_counter()
        x_stor, std_stor = utjamna_profil_bandad(y_stor, w_stor, 150.0)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        self.assertEqual(x_stor.shape, (m_stor,))
        self.assertEqual(std_stor.shape, (m_stor,))
        self.assertLess(dt_ms, 50.0)

    def test_typvarden_och_osakerhetsmarkering(self) -> None:
        """Gata utan markpunkter ger Stockholms typvärden och ar_typvarde == True."""
        korridor = GatuKorridor(
            gata_id="gata-utan-laser",
            mittlinje=((150100.0, 6580100.0), (150120.0, 6580100.0)),
            korbana_halvbredd_m=3.25,
            gangbana_bredd_m=2.75,
        )
        res = rekonstruera_gatuprofil(
            korridor,
            [],
            [],
            [],
            sektionsavstand_m=2.0,
            fallback_nock_z_rh2000=15.0,
        )
        self.assertGreater(len(res.sektioner), 0)
        for s in res.sektioner:
            self.assertTrue(s.ar_typvarde)
            self.assertEqual(s.antal_punkter, 0)
            self.assertAlmostEqual(s.nock_z_rh2000, 15.0, places=6)
            self.assertAlmostEqual(s.kantsten_vanster_m, TYP_KANTSTEN_HOJD_M, places=6)
            self.assertAlmostEqual(s.kantsten_hoger_m, TYP_KANTSTEN_HOJD_M, places=6)
            self.assertAlmostEqual(s.tvarfall_vanster, TYP_TVARFALL, places=6)
            self.assertAlmostEqual(s.tvarfall_hoger, TYP_TVARFALL, places=6)
            self.assertAlmostEqual(s.gangbana_lutning_vanster, TYP_GANGBANA_LUTNING, places=6)
            self.assertAlmostEqual(s.gangbana_lutning_hoger, TYP_GANGBANA_LUTNING, places=6)
            self.assertGreaterEqual(s.osakerhet_z_m, TYP_OSAKERHET_Z_M)

        # Delvis data (endast körbana, saknar gångbana) -> ar_typvarde == True och typkantsten
        d_k = np.linspace(-3.0, 3.0, 40)
        z_k = 10.0 - 0.025 * np.abs(d_k)
        sek_delvis = passa_tvarsnitt_huber(
            d_k, z_k, korbana_halvbredd_m=3.25, gangbana_bredd_m=2.75
        )
        self.assertTrue(sek_delvis.ar_typvarde)
        self.assertAlmostEqual(sek_delvis.kantsten_vanster_m, 0.12, places=6)
        self.assertAlmostEqual(sek_delvis.kantsten_hoger_m, 0.12, places=6)
        self.assertGreaterEqual(sek_delvis.osakerhet_z_m, TYP_OSAKERHET_Z_M)

    def test_hitta_rannstens_lagpunkter(self) -> None:
        """Identifierar rännstensbrunnar i lokal svacka på båda sidor, inga på rak lutning."""
        korridor = GatuKorridor(
            gata_id="svackgatan",
            mittlinje=((150000.0, 6580000.0), (150040.0, 6580000.0)),
            korbana_halvbredd_m=3.25,
            gangbana_bredd_m=2.75,
        )
        pe, pn, pz = _generera_syntetiskt_punktmoln(
            korridor,
            nock_z_fn_start=11.0,
            sanka_djup_m=0.30,
            brus_sigma_m=0.004,
            outlier_andel=0.05,
            fro=21,
        )
        res = rekonstruera_gatuprofil(korridor, pe, pn, pz, sektionsavstand_m=2.0)
        self.assertEqual(len(res.brunnar), 2)
        sidor = {b.sida for b in res.brunnar}
        self.assertEqual(sidor, {"vanster", "hoger"})
        for brunn in res.brunnar:
            self.assertAlmostEqual(brunn.s_m, 20.0, delta=2.5)
            self.assertAlmostEqual(brunn.e_m, 150020.0, delta=2.5)
            forv_n = 6580003.25 if brunn.sida == "vanster" else 6579996.75
            self.assertAlmostEqual(brunn.n_m, forv_n, places=3)

        # Monotont lutande gata -> inga inre lågpunkter
        sektioner_lut = [
            SektionsProfil(
                s_m=float(i * 2.0),
                nock_z_rh2000=12.0 - 0.04 * i,
                tvarfall_vanster=0.025,
                tvarfall_hoger=0.025,
                kantsten_vanster_m=0.12,
                kantsten_hoger_m=0.12,
                gangbana_lutning_vanster=0.02,
                gangbana_lutning_hoger=0.02,
                osakerhet_z_m=0.01,
                antal_punkter=50,
                ar_typvarde=False,
            )
            for i in range(15)
        ]
        self.assertEqual(hitta_rannstens_lagpunkter(korridor, sektioner_lut), [])

    def test_rastrering_lamnar_omgivning_oforandrad_och_stoder_ruta(self) -> None:
        """Celler utanför korridoren rörs aldrig; markslag blir HARDGJORT inuti."""
        ruta = Ruta(e_min_m=150000.0, n_min_m=6580000.0, storlek_m=20.0, overlapp_m=0.0)
        rader, kolumner = ruta.form_rc
        grund_z = np.full((rader, kolumner), 8.5, dtype=np.float32)
        grund_osak = np.full((rader, kolumner), 0.25, dtype=np.float32)
        grund_slag = np.full((rader, kolumner), GRAS, dtype=np.uint8)

        korridor = GatuKorridor(
            gata_id="kortgatan",
            mittlinje=((150004.0, 6580010.0), (150016.0, 6580010.0)),
            korbana_halvbredd_m=2.0,
            gangbana_bredd_m=1.0,
        )
        res = rekonstruera_gatuprofil(
            korridor, [], [], [], sektionsavstand_m=2.0, fallback_nock_z_rh2000=10.0
        )
        ny_z, ny_osak, ny_slag = rastrera_gatuprofil(grund_z, grund_osak, grund_slag, ruta, res)

        # Inga mutationer på indata
        self.assertTrue(
            np.array_equal(grund_slag, np.full((rader, kolumner), GRAS, dtype=np.uint8))
        )

        # Celler långt utanför korridoren (t.ex. rad 0 och sista raden) helt oförändrade
        self.assertTrue(np.all(ny_z[0:5, :] == np.float32(8.5)))
        self.assertTrue(np.all(ny_osak[0:5, :] == np.float32(0.25)))
        self.assertTrue(np.all(ny_slag[0:5, :] == GRAS))

        # Celler mitt i korridoren har fått HARDGJORT och parametrisk nockhöjd ~10.0 m
        mitt_rad = rader // 2
        mitt_kol = kolumner // 2
        self.assertEqual(int(ny_slag[mitt_rad, mitt_kol]), HARDGJORT)
        self.assertAlmostEqual(float(ny_z[mitt_rad, mitt_kol]), 10.0, delta=0.02)

    def test_a5_fysik_kantsten_haller_kvar_vatten_i_rannsten(self) -> None:
        """A5-fysik med T02: måttligt regn stannar i rännstenen, kraftigt regn svämmar över."""
        bredd, hojd = 80, 68  # 20 m x 17 m vid 0,25 m upplösning
        x_min = 150400.0
        y_max = 6580017.0
        geo = GeoTransformInfo(
            bredd=bredd,
            hojd=hojd,
            x_min=x_min,
            y_max=y_max,
            pixelstorlek_x=0.25,
            pixelstorlek_y=0.25,
            epsg=3011,
            dtype="float32",
        )
        y_mitt = y_max - 8.5  # rad 34 mitt i domänen

        korridor = GatuKorridor(
            gata_id="a5-gata",
            mittlinje=((x_min + 0.5, y_mitt), (x_min + 19.5, y_mitt)),
            korbana_halvbredd_m=3.25,
            gangbana_bredd_m=2.0,
            bombering=True,
        )
        pe, pn, pz = _generera_syntetiskt_punktmoln(
            korridor,
            nock_z_fn_start=10.08,
            tvarfall=0.025,
            kantsten_m=0.12,
            gangbana_lutning=0.02,
            brus_sigma_m=0.006,
            outlier_andel=0.15,
            outlier_hojd_m=1.50,
            punkter_per_m2=15.0,
            fro=505,
        )
        profil_res = rekonstruera_gatuprofil(korridor, pe, pn, pz, sektionsavstand_m=1.5)

        # Lägre gård/omgivning (9,50 m) utanför gatukorridoren så översvämmande vatten rinner av
        mark_z = np.full((hojd, bredd), 9.50, dtype=np.float32)
        osak_z = np.full((hojd, bredd), 0.05, dtype=np.float32)
        markslag = np.full((hojd, bredd), GRAS, dtype=np.uint8)

        mark_z, osak_z, markslag = rastrera_gatuprofil(mark_z, osak_z, markslag, geo, profil_res)

        # Stäng gatans kortändar i väst/öst så vattnet hålls i rännstenen (som i A5)
        rad_korr_start = int(round((y_max - (y_mitt + korridor.total_halvbredd_m)) / 0.25))
        rad_korr_slut = int(round((y_max - (y_mitt - korridor.total_halvbredd_m)) / 0.25))
        mark_z[rad_korr_start:rad_korr_slut, 0:3] = np.float32(10.50)
        mark_z[rad_korr_start:rad_korr_slut, -3:] = np.float32(10.50)

        graf = bygg_sankgraf(mark_z, osak_z, markslag, geo)

        # Måttligt regn (10 mm): 100 % stannar i rännstenen, 0,000 m på gångbana/gård
        res_mod = statisk_fyllning(graf, 0.010)
        self.assertLess(res_mod.volymfel_rel, 0.001)

        rad_kb_norr = int(round((y_max - (y_mitt + korridor.korbana_halvbredd_m)) / 0.25))
        rad_kb_soder = int(round((y_max - (y_mitt - korridor.korbana_halvbredd_m)) / 0.25))
        djup_korbana = res_mod.djup_m[rad_kb_norr:rad_kb_soder, 3:-3]
        djup_norr_gangbana_gard = res_mod.djup_m[:rad_kb_norr, :]
        djup_soder_gangbana_gard = res_mod.djup_m[rad_kb_soder:, :]

        self.assertGreater(float(np.max(djup_korbana)), 0.01)
        self.assertLess(float(np.max(djup_korbana)), 0.12)
        self.assertEqual(float(np.max(djup_norr_gangbana_gard)), 0.0)
        self.assertEqual(float(np.max(djup_soder_gangbana_gard)), 0.0)
        self.assertEqual(len(res_mod.utflodes_celler), 0)

        # Kraftigt regn (250 mm): fyller rännstenen över kantstenen och svämmar över
        res_stor = statisk_fyllning(graf, 0.250)
        self.assertLess(res_stor.volymfel_rel, 0.001)
        max_djup_stor = float(np.max(res_stor.djup_m[rad_kb_norr:rad_kb_soder, 3:-3]))
        self.assertGreaterEqual(max_djup_stor, 0.12)
        self.assertGreater(len(res_stor.utflodes_celler), 0)

    def test_a6_fysik_sankt_kantsten_vid_infart_styr_utflode(self) -> None:
        """A6-fysik med T02: vid överflödning lämnar vattnet gatan exakt genom 3 m infarten."""
        bredd, hojd = 80, 68  # 20 m x 17 m vid 0,25 m upplösning
        x_min = 150500.0
        y_max = 6580017.0
        geo = GeoTransformInfo(
            bredd=bredd,
            hojd=hojd,
            x_min=x_min,
            y_max=y_max,
            pixelstorlek_x=0.25,
            pixelstorlek_y=0.25,
            epsg=3011,
            dtype="float32",
        )
        y_mitt = y_max - 8.5

        # Infart 3,0 m bred (s i [8,0; 11,0] => E i [x_min + 8,5; x_min + 11,5]) på höger sida
        infart = InfartSankning(s_start_m=8.0, s_slut_m=11.0, sida="hoger", kantsten_hojd_m=0.02)
        korridor = GatuKorridor(
            gata_id="a6-gata-infart",
            mittlinje=((x_min + 0.5, y_mitt), (x_min + 19.5, y_mitt)),
            korbana_halvbredd_m=3.25,
            gangbana_bredd_m=2.0,
            bombering=True,
            infarter=(infart,),
        )
        pe, pn, pz = _generera_syntetiskt_punktmoln(
            korridor,
            nock_z_fn_start=10.08,
            tvarfall=0.025,
            kantsten_m=0.12,
            gangbana_lutning=0.02,
            brus_sigma_m=0.006,
            outlier_andel=0.15,
            outlier_hojd_m=1.50,
            punkter_per_m2=15.0,
            fro=606,
        )
        profil_res = rekonstruera_gatuprofil(korridor, pe, pn, pz, sektionsavstand_m=1.5)

        # Kontrollera att kantstensbrytlinjerna har ett skarpt 0,02 m infartssegment
        infart_bryt = [b for b in profil_res.kantstens_brytlinjer if b.hojd_m <= 0.025]
        self.assertEqual(len(infart_bryt), 1)
        self.assertAlmostEqual(infart_bryt[0].hojd_m, 0.02, places=6)

        # 1) Fysiktest med rastrerad gatuprofil (körbana + kantsten + gångbana + sänkt infart)
        mark_z = np.full((hojd, bredd), 9.50, dtype=np.float32)
        osak_z = np.full((hojd, bredd), 0.05, dtype=np.float32)
        markslag = np.full((hojd, bredd), GRAS, dtype=np.uint8)

        mark_z, osak_z, markslag = rastrera_gatuprofil(mark_z, osak_z, markslag, geo, profil_res)

        rad_korr_start = int(round((y_max - (y_mitt + korridor.total_halvbredd_m)) / 0.25))
        rad_korr_slut = int(round((y_max - (y_mitt - korridor.total_halvbredd_m)) / 0.25))
        mark_z[rad_korr_start:rad_korr_slut, 0:3] = np.float32(10.50)
        mark_z[rad_korr_start:rad_korr_slut, -3:] = np.float32(10.50)

        graf = bygg_sankgraf(mark_z, osak_z, markslag, geo)
        # Regn som fyller södra rännstenen över den sänkta infarten men under normal 12 cm kantsten
        res = statisk_fyllning(graf, 0.060)
        self.assertLess(res.volymfel_rel, 0.001)
        self.assertGreater(len(res.utflodes_celler), 0)

        infart_e0 = x_min + 0.5 + infart.s_start_m
        infart_e1 = x_min + 0.5 + infart.s_slut_m
        for rad, kol in res.utflodes_celler:
            cell_e = geo.x_min + (kol + 0.5) * geo.pixelstorlek_x
            cell_n = geo.y_max - (rad + 0.5) * geo.pixelstorlek_y
            self.assertGreaterEqual(cell_e, infart_e0 - 0.25)
            self.assertLessEqual(cell_e, infart_e1 + 0.25)
            # Utflöde sker på södra (höger) sidan av gatan
            self.assertLess(cell_n, y_mitt)

        # 2) Kontrollera även att kantstens_brytlinjer styr utflödet exakt i bygg_sankgraf
        korridor_utan_gb = GatuKorridor(
            gata_id="a6-kantstenslinje",
            mittlinje=((x_min + 0.5, y_mitt), (x_min + 19.5, y_mitt)),
            korbana_halvbredd_m=3.25,
            gangbana_bredd_m=0.0,
            bombering=True,
            infarter=(infart,),
        )
        res_kb = rekonstruera_gatuprofil(
            korridor_utan_gb, [], [], [], sektionsavstand_m=1.5, fallback_nock_z_rh2000=10.08
        )
        mark_kb = np.full((hojd, bredd), 9.90, dtype=np.float32)
        osak_kb = np.full((hojd, bredd), 0.02, dtype=np.float32)
        slag_kb = np.full((hojd, bredd), GRAS, dtype=np.uint8)
        mark_kb, osak_kb, slag_kb = rastrera_gatuprofil(mark_kb, osak_kb, slag_kb, geo, res_kb)
        rad_kb_n = int(round((y_max - (y_mitt + 3.25)) / 0.25))
        rad_kb_s = int(round((y_max - (y_mitt - 3.25)) / 0.25))
        mark_kb[rad_kb_n:rad_kb_s, 0:3] = np.float32(10.50)
        mark_kb[rad_kb_n:rad_kb_s, -3:] = np.float32(10.50)

        poster = brytlinjer_till_poster(res_kb.kantstens_brytlinjer)
        graf_kb = bygg_sankgraf(mark_kb, osak_kb, slag_kb, geo, brytlinjer=poster)
        fyll_kb = statisk_fyllning(graf_kb, 0.030)
        self.assertLess(fyll_kb.volymfel_rel, 0.001)
        self.assertGreater(len(fyll_kb.utflodes_celler), 0)
        for _rad, kol in fyll_kb.utflodes_celler:
            cell_e = geo.x_min + (kol + 0.5) * geo.pixelstorlek_x
            self.assertGreaterEqual(cell_e, infart_e0 - 0.25)
            self.assertLessEqual(cell_e, infart_e1 + 0.25)

    def test_rita_tvarsnitt_png_skapar_giltig_bild(self) -> None:
        """rita_tvarsnitt_png skapar en läsbar PNG med råpunkter, outliers och profil."""
        rng = np.random.default_rng(11)
        w_k = 3.25
        w_g = 2.75
        d = rng.uniform(-(w_k + w_g), w_k + w_g, size=180)
        ref = SektionsProfil(
            s_m=12.0,
            nock_z_rh2000=10.08,
            tvarfall_vanster=0.025,
            tvarfall_hoger=0.025,
            kantsten_vanster_m=0.12,
            kantsten_hoger_m=0.12,
            gangbana_lutning_vanster=0.02,
            gangbana_lutning_hoger=0.02,
            osakerhet_z_m=0.012,
            antal_punkter=180,
            ar_typvarde=False,
        )
        z = utvardera_sektionsprofil(ref, d, w_k) + rng.normal(0.0, 0.008, size=d.size)
        z[:25] += 1.50
        passad = passa_tvarsnitt_huber(
            d, z, s_m=12.0, korbana_halvbredd_m=w_k, gangbana_bredd_m=w_g
        )

        with tempfile.TemporaryDirectory() as tmp:
            ut_fil = Path(tmp) / "tvarsnitt_s12.png"
            sparad = rita_tvarsnitt_png(
                passad,
                w_k,
                w_g,
                ut_fil,
                punkter_d_m=d,
                punkter_z_m=z,
            )
            self.assertEqual(sparad, ut_fil)
            self.assertTrue(ut_fil.is_file())
            self.assertGreater(ut_fil.stat().st_size, 1000)
            with Image.open(ut_fil) as bild:
                self.assertEqual(bild.format, "PNG")
                self.assertEqual(bild.size, (900, 480))
                arr = np.asarray(bild)
                self.assertGreater(int(np.std(arr)), 5)

    def test_valideringsfel_ger_tydliga_undantag(self) -> None:
        """Ogiltiga indata ger tydliga GatuProfilFel utan tysta fel."""
        with self.assertRaises(GatuProfilFel):
            InfartSankning(s_start_m=5.0, s_slut_m=4.0, sida="hoger")
        with self.assertRaises(GatuProfilFel):
            InfartSankning(s_start_m=1.0, s_slut_m=4.0, sida="mitten")
        with self.assertRaises(GatuProfilFel):
            GatuKorridor(gata_id="tom", mittlinje=((0.0, 0.0),))
        with self.assertRaises(GatuProfilFel):
            utjamna_profil_bandad([1.0, 2.0], [1.0, -1.0], 1.0)


if __name__ == "__main__":
    unittest.main()
