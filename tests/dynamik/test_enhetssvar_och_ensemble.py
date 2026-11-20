"""Tester för enhetssvar/faltning, 500-scenariers ensemble och K6-export (T03)."""

from __future__ import annotations

import csv
import importlib
import json
import math
import tempfile
import unittest
from pathlib import Path
from typing import Any

import numpy as np

from dynamik import (
    HAR_PYARROW,
    KONTRAKT_K6,
    EnsembleScenario,
    EntrePunkt,
    bygg_enhetssvar,
    faltning_fore_overfall,
    kor_dynamik_handelsestyrd,
    kor_dynamik_tidssteg,
    kor_ensemble,
    mat_faltning_vinst,
    skriv_dynamik_resultat_k6,
    skriv_ensemble_k6,
)
from gemensamt.hash import sha256_fil
from gemensamt.manifest import las_manifest
from sankgraf.fyllning import kor_statisk_k1_mapp
from sankgraf.typer import SankGraf, SankNod, VolymPunkt, YtPunkt
from testsvit.k1_io import EPSG_SWEREF99_1800, GeoTransformInfo, las_geotiff

REPO_ROT = Path(__file__).resolve().parents[2]
TESTSVIT_MAPP = REPO_ROT / "testsvit"


def _bygg_syntetisk_24_nod_graf() -> SankGraf:
    """Bygger en deterministisk 24-noders graf (16 löv + 8 föräldrar i 4 kaskader)."""
    hojd, bredd = 40, 64
    mark = np.full((hojd, bredd), 14.0, dtype=np.float32)
    slag = np.ones((hojd, bredd), dtype=np.uint8)
    ct = np.full((hojd, bredd), -1, dtype=np.int64)
    geo = GeoTransformInfo(
        bredd=bredd,
        hojd=hojd,
        x_min=150000.0,
        y_max=6576010.0,
        pixelstorlek_x=0.25,
        pixelstorlek_y=0.25,
        epsg=EPSG_SWEREF99_1800,
        dtype="float32",
    )

    noder: dict[int, SankNod] = {}
    ut_lov_id: dict[int, int | None] = {}

    # 8 syskonpar (löv 0..15) med föräldrar 16..23 kopplade i 4 kaskader om 2 par
    for par in range(8):
        l_a = 2 * par
        l_b = 2 * par + 1
        p_id = 16 + par
        botten_bas = 12.0 - 0.5 * (par % 2)
        sadel_z = botten_bas + 0.30
        yttre_z = botten_bas + 0.65

        r0 = (par // 4) * 20
        r1 = r0 + 20
        c0 = (par % 4) * 16
        c_mid = c0 + 8
        c1 = c0 + 16
        ct[r0:r1, c0:c_mid] = l_a
        ct[r0:r1, c_mid:c1] = l_b
        mark[r0 + 2 : r1 - 2, c0 + 1 : c_mid - 1] = np.float32(botten_bas)
        mark[r0 + 2 : r1 - 2, c_mid + 1 : c1 - 1] = np.float32(botten_bas + 0.08)

        nasta_par = par + 1 if (par % 2 == 0) else None
        p_ut_till = (16 + nasta_par) if nasta_par is not None else None
        p_ut_lov = (2 * nasta_par) if nasta_par is not None else None

        vol_a = 6.0 + 0.4 * par
        vol_b = 4.5 + 0.3 * par
        area_a = 25.0 + 2.0 * par
        area_b = 22.0 + 1.5 * par

        noder[l_a] = SankNod(
            nod_id=l_a,
            forald_id=p_id,
            barn_id=(),
            overfall_rh2000=sadel_z,
            overfall_x=150000.0 + c_mid * 0.25,
            overfall_y=6576010.0 - (r0 + 10) * 0.25,
            ut_till_id=l_b,
            volymkurva=(VolymPunkt(botten_bas, 0.0), VolymPunkt(sadel_z, vol_a)),
            ytkurva=(YtPunkt(botten_bas, 20.0), YtPunkt(sadel_z, 20.0)),
            avrinningsyta_m2=area_a,
            persistens_m=0.30,
            osakerhet_m=0.02,
            botten_rh2000=botten_bas,
            cell_antal=160,
        )
        noder[l_b] = SankNod(
            nod_id=l_b,
            forald_id=p_id,
            barn_id=(),
            overfall_rh2000=sadel_z,
            overfall_x=150000.0 + c_mid * 0.25,
            overfall_y=6576010.0 - (r0 + 10) * 0.25,
            ut_till_id=l_a,
            volymkurva=(VolymPunkt(botten_bas + 0.08, 0.0), VolymPunkt(sadel_z, vol_b)),
            ytkurva=(YtPunkt(botten_bas + 0.08, 20.0), YtPunkt(sadel_z, 20.0)),
            avrinningsyta_m2=area_b,
            persistens_m=0.22,
            osakerhet_m=0.02,
            botten_rh2000=botten_bas + 0.08,
            cell_antal=160,
        )
        noder[p_id] = SankNod(
            nod_id=p_id,
            forald_id=None,
            barn_id=(l_a, l_b),
            overfall_rh2000=yttre_z,
            overfall_x=150000.0 + c1 * 0.25,
            overfall_y=6576010.0 - (r0 + 10) * 0.25,
            ut_till_id=p_ut_till,
            volymkurva=(
                VolymPunkt(botten_bas, 0.0),
                VolymPunkt(sadel_z, vol_a + vol_b),
                VolymPunkt(yttre_z, vol_a + vol_b + 16.0),
            ),
            ytkurva=(
                YtPunkt(botten_bas, 20.0),
                YtPunkt(sadel_z, 45.0),
                YtPunkt(yttre_z, 45.0),
            ),
            avrinningsyta_m2=0.0,
            persistens_m=0.65,
            osakerhet_m=0.02,
            botten_rh2000=botten_bas,
            cell_antal=320,
        )
        ut_lov_id[l_a] = l_b
        ut_lov_id[l_b] = l_a
        ut_lov_id[p_id] = p_ut_lov

    return SankGraf(
        noder=noder,
        cell_till_nod=ct,
        flodesvagar=[],
        geo=geo,
        filtrerad=False,
        persistens_faktor=None,
        mark_z=mark,
        markslag=slag,
        ut_lov_id=ut_lov_id,
    )


class TestEnhetssvarOchEnsemble(unittest.TestCase):
    """Testar enhetssvar + faltning, 500-scenariers ensemble och K6-export."""

    def test_enhetssvar_faltning_matchar_linjar_reservoar_och_ger_tidsvinst(self) -> None:
        graf, _ = kor_statisk_k1_mapp(
            TESTSVIT_MAPP / "A3_tva_sankor_sammanslagning" / "indata", regn_m=0.05
        )
        tabell = bygg_enhetssvar(graf, dt_s=10.0, horisont_s=2400.0)
        self.assertEqual(len(tabell.impulssvar_per_nod), 2)

        # Nytt regn med varierande blockintensitet före första överfall (< 2500 s)
        ny_tid = [0.0, 600.0, 600.0, 1200.0, 1200.0, 1800.0]
        ny_int = [30.0, 30.0, 80.0, 80.0, 20.0, 20.0]

        falt = faltning_fore_overfall(tabell, graf, ny_tid, ny_int)
        ref = kor_dynamik_tidssteg(
            graf, ny_tid, ny_int, dt_s=10.0, overfall_lage="omedelbar", gangtid_fordrojning=True
        )
        self.assertLess(ref.volymfel_rel, 0.001)
        self.assertIsNone(falt.tidigaste_overfall_s)

        for lid in sorted(tabell.impulssvar_per_nod):
            v_falt = falt.volym_m3[lid]
            v_ref = np.asarray(ref.noder[lid].volym_m3, dtype=np.float64)
            self.assertEqual(v_falt.shape, v_ref.shape)
            rel_fel = float(np.max(np.abs(v_falt - v_ref))) / float(np.max(v_ref))
            self.assertLess(rel_fel, 0.01)

        # Mät tidsvinst över 10 regnserier
        regnserier: list[tuple[list[float], list[float]]] = [
            ([0.0, 1800.0], [20.0 + 3.0 * k, 20.0 + 3.0 * k]) for k in range(10)
        ]
        vinst = mat_faltning_vinst(graf, regnserier, dt_s=10.0, horisont_s=1800.0)
        self.assertEqual(vinst.antal_regn, 10)
        self.assertLess(vinst.max_rel_volymfel_fore_overfall, 0.01)
        self.assertGreater(vinst.speedup_faktor, 1.0)

    def test_ensemble_500_scenarier_benchmark_och_percentiler(self) -> None:
        graf = _bygg_syntetisk_24_nod_graf()
        rng = np.random.default_rng(42)

        scenarier: list[EnsembleScenario] = []
        for idx in range(500):
            intens = float(rng.uniform(25.0, 130.0))
            varaktighet = float(rng.uniform(1200.0, 4800.0))
            avrinning = float(rng.uniform(0.65, 1.0))
            init_loss = float(rng.uniform(0.0, 5.0))
            brunn = float(rng.uniform(0.0, 0.002))
            scenarier.append(
                EnsembleScenario(
                    scenario_id=f"S{idx:04d}",
                    regn_tid_s=(0.0, varaktighet),
                    regn_intensitet_mm_h=(intens, intens),
                    avrinning_faktor=avrinning,
                    initial_forlust_mm=init_loss,
                    brunnar_avdrag_m3_s=brunn,
                )
            )

        ens = kor_ensemble(graf, scenarier, percentiler=(10.0, 50.0, 90.0))
        self.assertEqual(len(ens.scenarier), 500)
        self.assertLess(ens.max_volymfel_rel, 0.001)
        self.assertLess(ens.total_kortid_s, 60.0)

        p10 = ens.max_djup_percentiler_m[10.0]
        p50 = ens.max_djup_percentiler_m[50.0]
        p90 = ens.max_djup_percentiler_m[90.0]
        self.assertTrue(bool(np.all(p10 <= p50 + 1e-6)))
        self.assertTrue(bool(np.all(p50 <= p90 + 1e-6)))
        self.assertGreater(float(np.max(p90)), float(np.max(p10)))

        # Kontrollera även nodvisa percentiler
        for nid in graf.noder:
            self.assertLessEqual(
                ens.nod_max_niva_percentiler[10.0][nid],
                ens.nod_max_niva_percentiler[50.0][nid] + 1e-9,
            )
            self.assertLessEqual(
                ens.nod_max_niva_percentiler[50.0][nid],
                ens.nod_max_niva_percentiler[90.0][nid] + 1e-9,
            )

    def test_k6_export_och_reproducerbarhet(self) -> None:
        graf, _ = kor_statisk_k1_mapp(
            TESTSVIT_MAPP / "A3_tva_sankor_sammanslagning" / "indata", regn_m=0.05
        )
        res = kor_dynamik_handelsestyrd(graf, [0.0, 12000.0], [72.0, 72.0])
        entreer = [
            EntrePunkt(
                "E_vanster",
                graf.geo.x_min + 30.5 * graf.geo.pixelstorlek_x,
                graf.geo.y_max - 30.5 * graf.geo.pixelstorlek_y,
                gransdjup_m=0.10,
            ),
            EntrePunkt(
                "E_torr",
                graf.geo.x_min + 2.5 * graf.geo.pixelstorlek_x,
                graf.geo.y_max - 2.5 * graf.geo.pixelstorlek_y,
                gransdjup_m=0.10,
            ),
        ]

        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            filer1 = skriv_dynamik_resultat_k6(
                res,
                graf,
                tmp1,
                entreer=entreer,
                kodversion="test-sha",
                start="2026-11-20T19:00:00+01:00",
            )
            filer2 = skriv_dynamik_resultat_k6(
                res,
                graf,
                tmp2,
                entreer=entreer,
                kodversion="test-sha",
                start="2026-11-20T19:00:00+01:00",
            )

            for nyckel in ("max_djup", "tid_till_0_1m", "tid_till_0_2m", "noder_json", "entreer"):
                self.assertTrue(filer1[nyckel].is_file())
                self.assertEqual(sha256_fil(filer1[nyckel]), sha256_fil(filer2[nyckel]))

            # Läs tillbaka GeoTIFF och kontrollera värden + EPSG:3011
            djup_in, info = las_geotiff(filer1["max_djup"])
            self.assertEqual(info.epsg, EPSG_SWEREF99_1800)
            self.assertAlmostEqual(float(djup_in[30, 30]), 0.60, places=2)
            t01_in, _ = las_geotiff(filer1["tid_till_0_1m"])
            self.assertTrue(math.isfinite(float(t01_in[30, 30])))
            self.assertTrue(math.isnan(float(t01_in[2, 2])))

            # Kontrollera entreer.csv-kolumner
            with filer1["entreer"].open("r", encoding="utf-8", newline="") as fh:
                rader = list(csv.DictReader(fh))
            self.assertEqual(len(rader), 2)
            self.assertEqual(rader[0]["entre_id"], "E_vanster")
            self.assertGreater(float(rader[0]["max_djup_m"]), 0.55)
            self.assertTrue(bool(rader[0]["tid_till_gransdjup_s"]))
            self.assertEqual(rader[1]["entre_id"], "E_torr")
            self.assertEqual(rader[1]["tid_till_gransdjup_s"], "")

            # Kontrollera run.json (K0/v1)
            manifest = las_manifest(filer1["run"])
            self.assertEqual(manifest.modul, "dynamik")
            self.assertEqual(manifest.parametrar["niva"], "graf")
            self.assertEqual(manifest.parametrar["kontrakt_ut"], KONTRAKT_K6)
            self.assertLess(float(manifest.matvarden["volymfel_rel"]), 0.001)

            # Kontrollera ensemble-export i osakerhet/
            scenarier = [
                EnsembleScenario(f"A3_{k}", (0.0, 6000.0), (40.0 + 10.0 * k, 40.0 + 10.0 * k))
                for k in range(5)
            ]
            ens = kor_ensemble(graf, scenarier)
            ens_filer = skriv_ensemble_k6(
                ens,
                graf,
                Path(tmp1) / "ens_ut",
                kodversion="test-sha",
                start="2026-11-20T19:00:00+01:00",
            )
            for p_namn in ("p10_max_djup", "p50_max_djup", "p90_max_djup"):
                self.assertTrue(ens_filer[p_namn].is_file())
            ens_manifest = las_manifest(ens_filer["run"])
            self.assertEqual(ens_manifest.parametrar["niva"], "graf")
            self.assertEqual(ens_manifest.parametrar["lage"], "ensemble")
            sam_data = json.loads(ens_filer["ensemble_sammanfattning"].read_text(encoding="utf-8"))
            self.assertEqual(sam_data["antal_scenarier"], 5)

    @unittest.skipUnless(HAR_PYARROW, "pyarrow ej installerat lokalt")
    def test_noder_parquet_k6_schema(self) -> None:
        graf, _ = kor_statisk_k1_mapp(
            TESTSVIT_MAPP / "A1_enkel_sanka" / "indata", regn_m=0.05
        )
        res = kor_dynamik_handelsestyrd(graf, [0.0, 12000.0], [72.0, 72.0])
        pq: Any = importlib.import_module("pyarrow.parquet")

        with tempfile.TemporaryDirectory() as tmp:
            filer = skriv_dynamik_resultat_k6(res, graf, tmp)
            self.assertIn("noder_parquet", filer)
            tabell = pq.read_table(str(filer["noder_parquet"]))
            self.assertEqual(
                tabell.column_names,
                ["nod_id", "t_s", "niva_rh2000", "volym_m3"],
            )
            self.assertGreater(tabell.num_rows, 0)


if __name__ == "__main__":
    unittest.main()
