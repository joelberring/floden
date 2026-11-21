"""Solposition: kända värden för Stockholm (59,33 N, 18,07 E) och avstämning mot NREL SPA."""

from __future__ import annotations

import math
import unittest

import numpy as np

from dagsljus.solposition import (
    SolFel,
    dygnsfonster_utc,
    lat_lon_fran_3011,
    solposition,
)

LAT, LON = 59.33, 18.07


def _hojd_max_grader(datum: str) -> float:
    """Dygnets högsta solhöjd (grader) för Stockholm, söks på 30 s upplösning."""
    t = np.datetime64(datum + "T09:00:00") + np.arange(0, 4 * 3600, 30).astype("timedelta64[s]")
    h, _ = solposition(t, LAT, LON)
    return float(np.degrees(h.max()))


class TestSolposition(unittest.TestCase):
    def test_sann_middag_solstand_och_dagjamning(self) -> None:
        """h_max = 90 - lat + deklination. Deklination: +-23,44 grader vid solstånden (skevhet
        23,4393 grader, Meeus kap. 22) och 0 vid dagjämningen. Aktuella tider (USNO/Meeus):
        dagjämning 2026-03-20 14:46 UTC, sommarsolstånd 2026-06-21 08:24 UTC, vintersolstånd
        2026-12-21 20:50 UTC. Dygnets maximum ligger några timmar från händelsen, så toleransen
        är 0,15 grader för dagjämningen (deklinationen ändras 0,4 grader/dygn) och 0,05 grader
        för solstånden."""
        self.assertAlmostEqual(_hojd_max_grader("2026-06-21"), 90 - LAT + 23.44, delta=0.05)
        self.assertAlmostEqual(_hojd_max_grader("2026-12-21"), 90 - LAT - 23.44, delta=0.05)
        self.assertAlmostEqual(_hojd_max_grader("2026-03-20"), 90 - LAT, delta=0.15)

    def test_uppgiftens_ungefarliga_varden(self) -> None:
        self.assertAlmostEqual(_hojd_max_grader("2026-03-20"), 30.7, delta=0.2)
        self.assertAlmostEqual(_hojd_max_grader("2026-06-21"), 54.1, delta=0.1)
        self.assertAlmostEqual(_hojd_max_grader("2026-12-21"), 7.2, delta=0.1)

    def test_mot_nrel_spa(self) -> None:
        """Referensvärden: NREL SPA via pvlib 0.16.1 (`get_solarposition`, nrel_numpy),
        elevation utan refraktion. NOAA-algoritmen avviker < 0,02 grader (uppmätt 0,013 grader i
        höjd och 0,016 grader i azimut över 2000–2040)."""
        fall = [
            ("2026-03-20T10:47:00", 30.5808, 177.6269),
            ("2026-06-21T10:40:00", 54.0670, 176.2753),
            ("2026-12-21T10:53:00", 7.2173, 181.6736),
            ("2026-06-21T03:00:00", 7.2940, 55.2334),
            ("2026-12-21T13:30:00", 0.6196, 217.0510),
            ("2026-03-20T16:00:00", 6.9925, 258.1013),
        ]
        t = np.array([np.datetime64(f[0]) for f in fall])
        h, a = solposition(t, LAT, LON)
        for i, (_, eh, ea) in enumerate(fall):
            self.assertAlmostEqual(math.degrees(h[i]), eh, delta=0.02)
            self.assertAlmostEqual(math.degrees(a[i]), ea, delta=0.02)

    def test_azimut_ost_vast_och_syd(self) -> None:
        t = np.datetime64("2026-03-20T04:30:00") + np.arange(0, 14 * 3600, 600).astype(
            "timedelta64[s]"
        )
        h, a = solposition(t, LAT, LON)
        upp = h > 0
        az = np.degrees(a[upp])
        self.assertLess(az[0], 100.0)  # på morgonen i öster
        self.assertGreater(az[-1], 260.0)  # på kvällen i väster
        self.assertTrue(np.all(np.diff(az) > 0))  # monotont medurs

    def test_refraktion_hojer_solen_vid_horisonten(self) -> None:
        t = np.array([np.datetime64("2026-03-20T04:55:00")])
        h0, _ = solposition(t, LAT, LON)
        h1, _ = solposition(t, LAT, LON, refraktion=True)
        self.assertGreater(float(h1[0] - h0[0]), math.radians(0.1))

    def test_lat_lon_fran_sweref(self) -> None:
        """På centralmeridianen (18 grader E, falsk östning 150 000 m, skalfaktor 1) är
        N = meridianbågen från ekvatorn (GRS80, numerisk integration av
        a(1-e^2) Int (1 - e^2 sin^2 p)^(-3/2) dp), oberoende av pyproj."""
        a_grs80, f = 6378137.0, 1.0 / 298.257222101
        e2 = f * (2.0 - f)
        p = np.linspace(0.0, math.radians(LAT), 200001)
        integrand = (1.0 - e2 * np.sin(p) ** 2) ** -1.5
        bage = (
            a_grs80
            * (1.0 - e2)
            * float(np.sum((integrand[1:] + integrand[:-1]) / 2.0 * np.diff(p)))
        )
        lat, lon = lat_lon_fran_3011(150_000.0, bage)
        self.assertAlmostEqual(lat, LAT, delta=1e-6)
        self.assertAlmostEqual(lon, 18.0, delta=1e-9)
        # 1 km öster om centralmeridianen: ca 0,0176 grader (1000 m / (111,3 km cos 59,33)).
        _, lon2 = lat_lon_fran_3011(151_000.0, bage)
        self.assertAlmostEqual(
            lon2 - 18.0, 1000.0 / (111_320.0 * math.cos(math.radians(LAT))), delta=1e-4
        )

    def test_ogiltig_indata_ger_fel(self) -> None:
        with self.assertRaises(SolFel):
            solposition(np.array([1.0]), LAT, LON)  # type: ignore[arg-type]
        with self.assertRaises(SolFel):
            solposition(np.array([np.datetime64("2026-03-20T10:00:00")]), 95.0, LON)

    def test_dygnsfonster(self) -> None:
        t = dygnsfonster_utc("2026-03-20", LON, 10.0)
        self.assertEqual(len(t), 144)
        self.assertEqual(np.unique(np.diff(t)).size, 1)
        with self.assertRaises(SolFel):
            dygnsfonster_utc("2026-03-20", LON, 7.0)
        with self.assertRaises(SolFel):
            dygnsfonster_utc("inte-ett-datum", LON, 10.0)


if __name__ == "__main__":
    unittest.main()
