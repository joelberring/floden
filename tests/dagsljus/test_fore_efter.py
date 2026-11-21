"""Skuggstudie före/efter (Dagsljus fas 1b): soltimmar och VSC på K1 v2-kvarter, K9-scenarier.

Egenskaperna som testas, uppmätta på ett syntetiskt kvarter (7 x 7 rutor om 40 m à 1 m,
K = 180, vårdagjämning, H_max = 20 m, h_min = 15 grader, påverkansradie 74,6 m):

(a) celler och fasadpunkter utanför radien är exakt oförändrade,
(b) lokal omräkning räknar bara om rutor inom radien,
(c) ett hus som höjs ger fler skuggtimmar norr om det och aldrig färre någonstans (soltimmar och
    VSC är monotona),
(d) en borttagen byggnad ger exakt samma resultat som en bas byggd utan den.
"""

from __future__ import annotations

import csv
import math
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from numpy.typing import NDArray

from dagsljus.exempelkvarter import (
    bygg_rutnat,
    byggnads_id,
    polygon_andring,
    rektangel,
    skriv_k9,
)
from karna.analyslosare import LosarFel
from karna.korplan import kor_utredning, planera
from karna.paverkan import dagsljus_paverkansradie_m, ruta_inom_radie
from karna.resultatcache import ResultatCache
from karna.scenario import Andring
from karna.utredning import fraga_fran_dict
from testsvit.k1_io import las_geotiff

REPO = Path(__file__).resolve().parents[2]
E0, N0, RUTA_M, ANTAL = 150000.0, 6576000.0, 40.0, 7
SIDA_M = RUTA_M * ANTAL
HMAX_M, HMIN_GRADER = 20.0, 15.0
RADIE_M = dagsljus_paverkansradie_m(HMAX_M, HMIN_GRADER)
SOL = {
    "hojd_max_m": HMAX_M,
    "h_min_grader": HMIN_GRADER,
    "azimutriktningar": 180,
    "datum": ["2026-03-20"],
    "steg_min": 10,
}
VSC = {"hojd_max_m": HMAX_M, "h_min_grader": HMIN_GRADER, "azimutriktningar": 64}
HOJD_FORE_M, HOJD_EFTER_M = 12.0, 18.0


def _hus() -> dict[str, Any]:
    """Kvarteret: X (huset som ändras) mitt i, N norr och S söder om det (10 m gap), F1/F2 långt
    bort (> radien från X)."""
    x0, y0 = E0, N0
    return {
        "x": rektangel(x0 + 130, x0 + 150, y0 + 130, y0 + 142, HOJD_FORE_M, "x"),
        "n": rektangel(x0 + 125, x0 + 155, y0 + 152, y0 + 164, 10.0, "n"),
        "s": rektangel(x0 + 125, x0 + 155, y0 + 108, y0 + 120, 10.0, "s"),
        "f1": rektangel(x0 + 10, x0 + 30, y0 + 10, y0 + 22, 15.0, "f1"),
        "f2": rektangel(x0 + 250, x0 + 270, y0 + 250, y0 + 262, 15.0, "f2"),
    }


def _fraga_dict(
    bas: dict[str, Path], k9: dict[str, Path], urval: list[str], scenarier: list[str]
) -> dict[str, Any]:
    sc: dict[str, Any] = {}
    for sid in scenarier:
        sc[sid] = {"bas": str(bas[sid])}
        if sid in k9:
            sc[sid]["k9"] = [str(k9[sid])]
    return {
        "kontrakt": "K20/v1",
        "fraga_id": "fore-efter-test",
        "scenarier": sc,
        "analyser": [
            {"id": "sol", "losare": "dagsljus.soltimmar_mark", "parametrar": SOL},
            {
                "id": "vsc",
                "losare": "dagsljus.vsc_fasad",
                "parametrar": {**VSC, "byggnader": urval, "fasad_steg_m": 4.0},
            },
        ],
    }


def _sy(ut: Path, analys: str, scen: str) -> list[Path]:
    return sorted((ut / analys / scen).glob("mm_*"))


def _raster(ut: Path, analys: str, scen: str, fil: str) -> NDArray[np.float64]:
    """Sammanfogar rutornas raster (utan överlapp) till ett kvarter; rad 0 = norr."""
    n = int(SIDA_M)
    ark = np.full((n, n), np.nan)
    for mapp in _sy(ut, analys, scen):
        a, geo = las_geotiff(mapp / fil)
        j0 = round((geo.x_min - E0) / 1.0)
        i0 = round((N0 + SIDA_M - geo.y_max) / 1.0)
        ark[i0 : i0 + a.shape[0], j0 : j0 + a.shape[1]] = a
    assert not np.isnan(ark).any()
    return ark


def _vsc(ut: Path, scen: str) -> dict[str, dict[str, str]]:
    ut_rader: dict[str, dict[str, str]] = {}
    for mapp in _sy(ut, "vsc", scen):
        with (mapp / "vsc.csv").open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                assert r["fasad_id"] not in ut_rader
                ut_rader[r["fasad_id"]] = r
    return ut_rader


def _avstand_till(poly: Any) -> NDArray[np.float64]:
    n = int(SIDA_M)
    xs = E0 + np.arange(n) + 0.5
    ys = N0 + SIDA_M - np.arange(n) - 0.5
    gx, gy = np.meshgrid(xs, ys)
    return np.asarray(shapely.distance(poly, shapely.points(gx, gy)))


class TestForeEfter(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        h = _hus()
        alla = list(h.values())
        cls.bas = bygg_rutnat(
            cls.tmp / "bas", alla, e_min_m=E0, n_min_m=N0, antal=ANTAL, rutstorlek_m=RUTA_M
        )
        cls.utan = bygg_rutnat(
            cls.tmp / "utan",
            [v for k, v in h.items() if k != "x"],
            e_min_m=E0,
            n_min_m=N0,
            antal=ANTAL,
            rutstorlek_m=RUTA_M,
        )
        cls.k9 = {
            "hojd": skriv_k9(
                cls.tmp / "hojd.json",
                cls.bas,
                "hojd",
                [
                    Andring(
                        "byggnad_hojd",
                        "hojd_x",
                        None,
                        {"byggnads_id": byggnads_id("x"), "hojd_m": HOJD_EFTER_M},
                    )
                ],
            ),
            "bort": skriv_k9(
                cls.tmp / "bort.json",
                cls.bas,
                "bort",
                [Andring("byggnad_bort", "bort_x", None, {"byggnads_id": byggnads_id("x")})],
            ),
            "ny": skriv_k9(
                cls.tmp / "ny.json",
                cls.bas,
                "ny",
                [
                    polygon_andring(
                        "byggnad_ny",
                        "ny_hus",
                        E0 + 200,
                        E0 + 212,
                        N0 + 130,
                        N0 + 142,
                        {"hojd_m": 16.0},
                    )
                ],
            ),
        }
        cls.urval = sorted(byggnads_id(k) for k in ("n", "s", "f1", "f2"))
        bas_per_scen = {s: cls.bas for s in ("fore", "hojd", "bort", "ny")}
        bas_per_scen["utan"] = cls.utan
        k9 = {s: cls.k9[s] for s in ("hojd", "bort", "ny")}
        cls.cache = ResultatCache(cls.tmp / "cache")
        cls.ut = cls.tmp / "ut"
        fraga = fraga_fran_dict(_fraga_dict(bas_per_scen, k9, cls.urval, list(bas_per_scen)), REPO)
        cls.res = kor_utredning(fraga, cls.cache, cls.ut)
        cls.bas_per_scen, cls.k9_per_scen = bas_per_scen, k9
        cls.x_poly = shapely.box(E0 + 130, N0 + 130, E0 + 150, N0 + 142)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_a_utanfor_radien_exakt_oforandrat(self) -> None:
        fore = _raster(self.ut, "sol", "fore", "soltimmar.tif")
        efter = _raster(self.ut, "sol", "hojd", "soltimmar.tif")
        avst = _avstand_till(self.x_poly)
        lang = avst > RADIE_M + 2.0
        self.assertGreater(int(lang.sum()), 0.4 * lang.size)  # en stor del av kvarteret
        self.assertTrue(np.array_equal(fore[lang], efter[lang]))  # exakt, inte "nästan"
        self.assertTrue((fore != efter).any())
        # Fasadpunkter: F1 och F2 ligger bortom radien från X, N och S inom den.
        v0, v1 = _vsc(self.ut, "fore"), _vsc(self.ut, "hojd")
        self.assertEqual(set(v0), set(v1))
        for fid, r0 in v0.items():
            bid = r0["byggnad_id"]
            r1 = v1[fid]
            if bid in (byggnads_id("f1"), byggnads_id("f2")):
                self.assertEqual(r0["vsc_procent"], r1["vsc_procent"], fid)
        self.assertTrue(any(r["byggnad_id"] == byggnads_id("f1") for r in v0.values()))

    def test_b_lokal_omrakning_bara_rutor_inom_radien(self) -> None:
        cache = ResultatCache(self.tmp / "cache_b")
        urval = self.urval
        only_fore = fraga_fran_dict(
            _fraga_dict(self.bas_per_scen, self.k9_per_scen, urval, ["fore"]), REPO
        )
        kor_utredning(only_fore, cache, self.tmp / "ut_b")
        bada = fraga_fran_dict(
            _fraga_dict(self.bas_per_scen, self.k9_per_scen, urval, ["fore", "hojd"]), REPO
        )
        plan = planera(bada, cache)
        fore, hojd = plan.scenarier["fore"], plan.scenarier["hojd"]
        andrade = {
            r.ruta_id
            for r in hojd.rutor
            if r.hash != next(f.hash for f in fore.rutor if f.ruta_id == r.ruta_id)
        }
        self.assertEqual(andrade, {"mm_150120_6576120"})  # bara rutan med huset
        bbox = {r.ruta_id: (r.x_min, r.y_min, r.x_max, r.y_max) for r in hojd.rutor}
        forvantat: set[str] = set()
        for rid in andrade:
            forvantat |= set(ruta_inom_radie(bbox[rid], bbox.items(), RADIE_M))
        self.assertEqual(len(forvantat), 25)  # 5 x 5 av 7 x 7 rutor (avstånd <= 74,6 m)
        for analys in ("sol", "vsc"):
            nya = {
                j.ruta.ruta_id
                for j in plan.jobb_for(analys, "hojd")
                if j.ruta is not None and not j.i_cache
            }
            self.assertEqual(nya, forvantat, analys)
            fore_ok = [j for j in plan.jobb_for(analys, "fore") if not j.i_cache]
            self.assertEqual(fore_ok, [], analys)  # före är redan beräknat
        # Utan ändringen (före mot före) räknas ingenting om.
        igen = planera(only_fore, cache)
        self.assertTrue(all(j.i_cache for j in igen.jobb))

    def test_c_hus_som_hojs_monotont_och_skugga_norrut(self) -> None:
        for scen in ("hojd", "ny"):
            fore = _raster(self.ut, "sol", "fore", "soltimmar.tif")
            efter = _raster(self.ut, "sol", scen, "soltimmar.tif")
            diff = efter - fore
            # Byggnadens egna celler byter yta (mark -> tak) och ingår inte: taket ser mer sol.
            fot = (
                shapely.box(E0 + 130, N0 + 130, E0 + 150, N0 + 142)
                if scen == "hojd"
                else (shapely.box(E0 + 200, N0 + 130, E0 + 212, N0 + 142))
            )
            utanfor_fot = _avstand_till(fot) > 0.0
            self.assertLessEqual(float(diff[utanfor_fot].max()), 1e-9, scen)  # aldrig fler
            self.assertLess(float(diff.min()), -0.5, scen)
            v0, v1 = _vsc(self.ut, "fore"), _vsc(self.ut, scen)
            for fid, r0 in v0.items():
                a, b = r0["vsc_procent"], v1[fid]["vsc_procent"]
                if a and b:
                    self.assertLessEqual(float(b), float(a) + 1e-9, (scen, fid))
        fore = _raster(self.ut, "sol", "fore", "soltimmar.tif")
        diff = _raster(self.ut, "sol", "hojd", "soltimmar.tif") - fore
        n = int(SIDA_M)
        rad = lambda y: n - 1 - int(y)  # noqa: E731  (rad för cell med mittpunkt y_m över N0)
        # Gapet norr om X är redan helt skuggat av X (12 m hög, 2-8 m bort, sol <= 30 grader),
        # så skillnaden syns på N:s tak (10 m, 10 m norr om X) som förlorar sol av det högre huset.
        # S:s tak (10 m, 10 m söder om X) ligger på solsidan och är oförändrat.
        nord = diff[rad(155) : rad(152) + 1, 132:149]  # N:s tak, y 152-155 m
        syd = diff[rad(118) : rad(112) + 1, 132:149]  # S:s tak, y 112-118 m
        self.assertTrue(np.all(nord < 0.0))  # varje cell får färre soltimmar
        self.assertLess(float(nord.mean()), -1.0)
        self.assertTrue(np.all(syd == 0.0))
        # N:s södra fasad (mot X) och S:s norra fasad får lägre VSC; N:s norra fasad påverkas inte.
        v0, v1 = _vsc(self.ut, "fore"), _vsc(self.ut, "hojd")
        sank = {
            (r["byggnad_id"], r["normal_azimut_grader"]): float(r["vsc_procent"])
            - float(v0[f]["vsc_procent"])
            for f, r in v1.items()
        }
        self.assertLess(sank[(byggnads_id("n"), "180.0")], -0.01)
        self.assertLess(sank[(byggnads_id("s"), "0.0")], -0.01)

    def test_d_borttagen_byggnad_lika_med_bas_utan_den(self) -> None:
        bort = _raster(self.ut, "sol", "bort", "soltimmar.tif")
        utan = _raster(self.ut, "sol", "utan", "soltimmar.tif")
        self.assertTrue(np.array_equal(bort, utan))
        fore = _raster(self.ut, "sol", "fore", "soltimmar.tif")
        self.assertFalse(np.array_equal(bort, fore))
        vb, vu = _vsc(self.ut, "bort"), _vsc(self.ut, "utan")
        self.assertEqual(set(vb), set(vu))
        for fid in vb:
            for kol in ("vsc_procent", "status", "x_m", "y_m", "z_m", "normal_azimut_grader"):
                self.assertEqual(vb[fid][kol], vu[fid][kol], (fid, kol))
        self.assertGreater(len(vb), 0)

    def test_urval_hittar_byggnaderna(self) -> None:
        """Ett stavfel i byggnader ger inga punkter; sammanställningen visar antalet."""
        antal = sum(
            j["matvarden"]["fasadpunkter"]
            for j in self.res.sammanstallning["jobb"]
            if j["analys"] == "vsc" and j["scenario"] == "fore"
        )
        self.assertEqual(antal, len(_vsc(self.ut, "fore")))
        self.assertGreater(antal, 20)
        self.assertEqual({r["byggnad_id"] for r in _vsc(self.ut, "fore").values()}, set(self.urval))
        self.assertTrue(math.isfinite(RADIE_M))


class TestStandardhojd(unittest.TestCase):
    """Byggnad med gissad höjd (hojdkalla = standard): varning och flaggade resultat."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        e, n = E0, N0
        cls.std = rektangel(e + 50, e + 70, n + 50, n + 62, None, "std")  # standardhöjd 10 m
        cls.kand = rektangel(e + 50, e + 70, n + 75, n + 87, 12.0, "kand")  # 13 m norr om std
        cls.bas = bygg_rutnat(
            cls.tmp / "bas", [cls.std, cls.kand], e_min_m=e, n_min_m=n, antal=4, rutstorlek_m=40.0
        )
        cls.k9 = skriv_k9(
            cls.tmp / "hojd.json",
            cls.bas,
            "satt",
            [
                Andring(
                    "byggnad_hojd",
                    "satt_std",
                    None,
                    {"byggnads_id": byggnads_id("std"), "hojd_m": 10.0},
                )
            ],
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _kor(self, med_k9: bool) -> tuple[Any, Path]:
        sc: dict[str, Any] = {"s": {"bas": str(self.bas)}}
        if med_k9:
            sc["s"]["k9"] = [str(self.k9)]
        fraga = fraga_fran_dict(
            {
                "kontrakt": "K20/v1",
                "fraga_id": "std",
                "scenarier": sc,
                "analyser": [
                    {"id": "sol", "losare": "dagsljus.soltimmar_mark", "parametrar": SOL},
                    {"id": "svf", "losare": "dagsljus.svf_mark", "parametrar": VSC},
                    {"id": "vsc", "losare": "dagsljus.vsc_fasad", "parametrar": VSC},
                ],
            },
            REPO,
        )
        ut = self.tmp / ("ut_k9" if med_k9 else "ut")
        res = kor_utredning(fraga, ResultatCache(self.tmp / ("c_k9" if med_k9 else "c")), ut)
        return res, ut

    def test_varning_och_flaggade_celler_och_punkter(self) -> None:
        res, ut = self._kor(False)
        jobb = res.sammanstallning["jobb"]
        for analys in ("sol", "svf", "vsc"):
            self.assertTrue(
                any(
                    "standardhöjd" in v
                    for j in jobb
                    if j["analys"] == analys
                    for v in j["varningar"]
                ),
                analys,
            )
        # Celler: flaggade nära den gissade byggnaden, aldrig bortom radien.
        n = 160
        flagga = np.full((n, n), np.nan)
        for m in sorted((ut / "sol" / "s").glob("mm_*")):
            a, geo = las_geotiff(m / "standardhojd.tif")
            j0, i0 = round(geo.x_min - E0), round(N0 + n - geo.y_max)
            flagga[i0 : i0 + a.shape[0], j0 : j0 + a.shape[1]] = a
        self.assertEqual(set(np.unique(flagga).tolist()), {0.0, 1.0})
        xs, ys = E0 + np.arange(n) + 0.5, N0 + n - np.arange(n) - 0.5
        gx, gy = np.meshgrid(xs, ys)
        avst = np.asarray(
            shapely.distance(
                shapely.box(E0 + 50, N0 + 50, E0 + 70, N0 + 62), shapely.points(gx, gy)
            )
        )
        self.assertTrue(np.all(flagga[avst > RADIE_M + 2.0] == 0.0))
        norr = (gx > E0 + 55) & (gx < E0 + 65) & (gy > N0 + 63) & (gy < N0 + 70)
        self.assertTrue(np.all(flagga[norr] == 1.0))  # skuggan norr om den gissade byggnaden
        # Fasadpunkter: på den gissade byggnaden, och på granne vars VSC den påverkar.
        rader = []
        for m in sorted((ut / "vsc" / "s").glob("mm_*")):
            with (m / "vsc.csv").open(encoding="utf-8") as fh:
                rader += list(csv.DictReader(fh))
        pa_std = [r for r in rader if r["byggnad_id"] == byggnads_id("std")]
        self.assertTrue(pa_std and all(r["standardhojd"] == "1" for r in pa_std))
        syd = [
            r
            for r in rader
            if r["byggnad_id"] == byggnads_id("kand") and r["normal_azimut_grader"] == "180.0"
        ]
        # Punkter under den gissade takhöjden (10 m) beror på den; punkten över taket gör det inte.
        lagt = [r for r in syd if float(r["z_m"]) < 10.0]
        hogt = [r for r in syd if float(r["z_m"]) > 10.0]
        self.assertTrue(lagt and all(r["standardhojd"] == "1" for r in lagt))
        self.assertTrue(hogt and all(r["standardhojd"] == "0" for r in hogt))
        norrfasad = [
            r
            for r in rader
            if r["byggnad_id"] == byggnads_id("kand") and r["normal_azimut_grader"] == "0.0"
        ]
        self.assertTrue(norrfasad and all(r["standardhojd"] == "0" for r in norrfasad))

    def test_satt_hojd_tar_bort_flaggan(self) -> None:
        res, ut = self._kor(True)
        for j in res.sammanstallning["jobb"]:
            self.assertFalse(any("standardhöjd" in v for v in j["varningar"]), j["analys"])
        for m in sorted((ut / "sol" / "s").glob("mm_*")):
            a, _ = las_geotiff(m / "standardhojd.tif")
            self.assertEqual(float(a.max()), 0.0)

    def test_k11_med_csv_ger_samma_vsc(self) -> None:
        """K11-punkterna skrivna som CSV och körda via CSV-rollen ger samma VSC som K11 direkt."""
        from karna.geometri import fasadpunkter

        ruta = sorted(self.bas.glob("mm_*"))
        f = fasadpunkter(ruta, 3.0, 3.0)
        csv_fil = self.tmp / "punkter.csv"
        with csv_fil.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["fasad_id", "x_m", "y_m", "z_m", "normal_azimut_grader"])
            for i in range(len(f)):
                w.writerow(
                    [
                        f.fasadpunkt_id[i],
                        repr(float(f.e_m[i])),
                        repr(float(f.n_m[i])),
                        repr(float(f.z_rh2000[i])),
                        repr(float(f.normal_azimut_grader[i])),
                    ]
                )

        def kor(indata: dict[str, str], namn: str) -> dict[str, str]:
            fraga = fraga_fran_dict(
                {
                    "kontrakt": "K20/v1",
                    "fraga_id": "k11",
                    "scenarier": {"s": {"bas": str(self.bas)}},
                    "analyser": [
                        {
                            "id": "vsc",
                            "losare": "dagsljus.vsc_fasad",
                            "parametrar": VSC,
                            "indata": indata,
                        }
                    ],
                },
                REPO,
            )
            ut = self.tmp / namn
            kor_utredning(fraga, ResultatCache(self.tmp / ("c_" + namn)), ut)
            ut_rader: dict[str, str] = {}
            for m in sorted((ut / "vsc" / "s").glob("mm_*")):
                with (m / "vsc.csv").open(encoding="utf-8") as fh:
                    for r in csv.DictReader(fh):
                        ut_rader[r["fasad_id"]] = r["vsc_procent"]
            return ut_rader

        k11 = kor({}, "k11_ut")
        via_csv = kor({"fasadpunkter": str(csv_fil)}, "csv_ut")
        self.assertGreater(len(k11), 50)
        self.assertEqual(k11, via_csv)

    def test_byggnader_med_csv_ger_fel(self) -> None:
        fraga = fraga_fran_dict(
            {
                "kontrakt": "K20/v1",
                "fraga_id": "fel",
                "scenarier": {"s": {"bas": str(self.bas)}},
                "analyser": [
                    {
                        "id": "vsc",
                        "losare": "dagsljus.vsc_fasad",
                        "parametrar": {**VSC, "byggnader": ["b_x"]},
                        "indata": {"fasadpunkter": str(self.tmp / "punkter.csv")},
                    }
                ],
            },
            REPO,
        )
        (self.tmp / "punkter.csv").write_text(
            "fasad_id,x_m,y_m,z_m,normal_azimut_grader\na,1,1,1,0\n", encoding="utf-8"
        )
        with self.assertRaises(LosarFel):
            kor_utredning(fraga, ResultatCache(self.tmp / "c_fel"), self.tmp / "ut_fel")


class TestExempelfraga(unittest.TestCase):
    """Den incheckade exempelfrågan laddas och planeras mot demokvarteret (utan att köras)."""

    def test_exempelfilen_planerar_mot_demokvarteret(self) -> None:
        from dagsljus.exempelkvarter import bygg_demokvarter
        from karna.utredning import las_fraga

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "exempel" / "fragor").mkdir(parents=True)
        fil = tmp / "exempel" / "fragor" / "dagsljus_fore_efter.toml"
        shutil.copy(REPO / "exempel" / "fragor" / "dagsljus_fore_efter.toml", fil)
        bygg_demokvarter(tmp / "data" / "cache" / "exempel" / "dagsljus-kvarter")
        fraga = las_fraga(fil)
        self.assertEqual(set(fraga.scenarier), {"bas", "hojd", "nytt_hus"})
        plan = planera(fraga, ResultatCache(tmp / "cache"))
        self.assertEqual(len(plan.jobb), 3 * 9 * 2)  # 3 scenarier x 9 rutor x 2 analyser
        bas, hojd = plan.scenarier["bas"], plan.scenarier["hojd"]
        andrade = [
            r.ruta_id for r, b in zip(hojd.rutor, bas.rutor, strict=True) if r.hash != b.hash
        ]
        self.assertEqual(len(andrade), 1)  # det låga huset ligger i en ruta
        # Radien 514 m täcker hela kvarteret: alla rutor räknas om för den ändringen.
        nya = {j.ruta.ruta_id for j in plan.jobb_for("sol", "hojd") if j.ruta and not j.i_cache}
        self.assertEqual(len(nya), 9)


if __name__ == "__main__":
    unittest.main()
