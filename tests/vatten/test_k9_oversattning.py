"""K9→K3 finns bara i kärnan; vattnets lager ger byte-identiskt samma lappar och djup som förut.

Facit nedan är mätt med den gamla, egna översättningen i `vatten.scenario_lapp` (före borttagningen
av dubbletten) på testsvitens A1. Samma K9-scenarier ska ge samma K3-lapp (JSON och sha256), samma
K1-celler (mark, osäkerhet, markslag) och samma djup vid 300 mm. Scenarierna har inga överlappande
ändringar av samma typ, så kärnans id-ordning ändrar inget här (se `test_en_ordning…`).
"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from typing import Any

import numpy as np

from gemensamt.hash import sha256_fil
from karna.k9_till_k1 import k9_till_lapp
from karna.scenario import Andring, Planscenario, ScenarioFel, platta_ut
from vatten import ScenarioLappFel, k1_filer, las_k1, scenario_till_lapp, tillampa_scenario
from vatten.blaytor import blaytor_vid_mm, bygg_graf

A1 = Path(__file__).resolve().parents[2] / "testsvit" / "A1_enkel_sanka" / "indata"
FACIT: dict[str, Any] = {
    "beskr": "Ur planscenario 20c511bc53c9 (K9/v1)",
    "hog": {
        "andrad": 400,
        "djup": "09325989e69488eb",
        "lagrat_m3": 42.49999523162842,
        "mark_z": "dd56cf6d2a2f0fca",
        "markslag": "98683fd15f67a7d1",
        "maxdjup": 0.5,
        "osak": "d7cf9da7e10109ee",
    },
    "ignorerade": ["trad"],
    "kombi": {
        "andrad": 688,
        "djup": "555204b14acf5f18",
        "lagrat_m3": 45.49999237060547,
        "mark_z": "879eb2d912351aca",
        "markslag": "28ae118e5aa50170",
        "maxdjup": 0.8333330154418945,
        "osak": "807e432bd5d0e30c",
    },
    "lapp_id": "alla",
    "lapp_json": "9537e3f45ec2984ddc7dd928c2b4cfc62d9858171b666c389ed3cad9ea34980a",
    "lapp_sha": "6c3f800531f72c779a163620d72809d481b0ba296ba3aa566b71aab416a4ae20",
    "vanster": {
        "andrad": 1800,
        "djup": "5a1c98a23daac43c",
        "lagrat_m3": 25.0,
        "mark_z": "9564b6f109210b6c",
        "markslag": "98683fd15f67a7d1",
        "maxdjup": 0.5,
        "osak": "e054f57e5de70c00",
    },
}


def kv(e0: float, n0: float, e1: float, n1: float) -> dict[str, object]:
    return {"type": "Polygon", "coordinates": [[[e0, n0], [e1, n0], [e1, n1], [e0, n1], [e0, n0]]]}


def h(a: np.ndarray) -> str:  # type: ignore[type-arg]
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()[:16]


def alla() -> Planscenario:
    return Planscenario(
        "alla",
        ("r",),
        (
            Andring(
                "byggnad_ny",
                "hus",
                kv(0, 0, 4, 4),
                {"entre_rh2000": 11.0, "entre_punkt": [2, 0], "hojd_m": 18.0, "gransdjup_m": 0.2},
            ),
            Andring(
                "byggnad_ny",
                "hus2",
                kv(20, 0, 24, 4),
                {"entre_rh2000": 11.0, "entre_punkt": [22, 0]},
            ),
            Andring(
                "markhojd",
                "plan",
                kv(5, 5, 8, 8),
                {"niva_rh2000": 10.4, "osakerhet_m": 0.1, "markslag": 4},
            ),
            Andring("markhojd", "plan2", kv(5, 5, 8, 8), {"niva_rh2000": 10.2}),
            Andring("markslag", "gard", kv(0, 5, 3, 8), {"kod": 4}),
            Andring("magasin", "mag", kv(9, 0, 12, 3), {"volym_m3": 12.0}),
            Andring(
                "magasin",
                "mag2",
                kv(13, 0, 15, 3),
                {"volym_m3": 5.0, "botten_rh2000": 9.5, "markslag": 8},
            ),
            Andring(
                "gata",
                "g",
                {"type": "LineString", "coordinates": [[0, 9], [10, 9]]},
                {
                    "sektion": "standard-12m",
                    "profil_rh2000": [10.0, 10.1],
                    "markslag_korbana": 1,
                    "markslag_gangbana": 7,
                },
            ),
            Andring("vegetation", "trad", kv(1, 1, 2, 2), {"hojd_m": 8.0}),
        ),
    )


class TestK9TillK3EnDel(unittest.TestCase):
    def test_lapp_byte_identisk_mot_gamla_vattenoversattningen(self) -> None:
        som = scenario_till_lapp(alla(), ignorera_typer={"vegetation"})
        js = json.dumps(som.lapp.till_dict(), sort_keys=True).encode()
        self.assertEqual(hashlib.sha256(js).hexdigest(), FACIT["lapp_json"])
        self.assertEqual(som.lapp.lapp_sha256(), FACIT["lapp_sha"])
        self.assertEqual(
            (som.lapp.lapp_id, som.lapp.beskrivning), (FACIT["lapp_id"], FACIT["beskr"])
        )
        self.assertEqual(list(som.ignorerade), FACIT["ignorerade"])

    def test_vattnet_anvander_karnans_funktion_och_ordning(self) -> None:
        s = alla()
        andringar = [a for a in platta_ut(s, {s.nyckel: s}) if a.typ != "vegetation"]
        karnan = k9_till_lapp(andringar, s)
        vatten = scenario_till_lapp(s, ignorera_typer={"vegetation"}).lapp
        self.assertEqual(karnan.till_dict()["operationer"], vatten.till_dict()["operationer"])
        self.assertIs(ScenarioLappFel, ScenarioFel)

    def test_en_ordning_andring_id_aven_i_vattnet(self) -> None:
        """Överlappande ändringar med olika id: id-ordning (kärnans), inte listordning."""
        yta = kv(5, 5, 8, 8)
        s = Planscenario(
            "o",
            ("r",),
            (
                Andring("markhojd", "b", yta, {"niva_rh2000": 10.4}),
                Andring("markhojd", "a", yta, {"niva_rh2000": 10.2}),
            ),
        )
        lapp = scenario_till_lapp(s).lapp
        self.assertEqual([y.yta_id for y in lapp.hojdytor], ["a", "b"])  # inte listordningen b, a
        # samma lapp oavsett hur ändringarna listas
        s2 = Planscenario("o", ("r",), tuple(reversed(s.andringar)))
        self.assertEqual(
            scenario_till_lapp(s2).lapp.till_dict()["operationer"], lapp.till_dict()["operationer"]
        )

    def test_k1_celler_och_djup_ar_identiska_med_forut(self) -> None:
        k1 = las_k1(k1_filer(A1))
        bas = bygg_graf(k1)
        recept = sha256_fil(A1 / "recept.json")
        g = k1.geo
        e0, n1 = g.x_min + 10 * 0.25, g.y_max - 10 * 0.25
        hog = Andring("markhojd", "hog", kv(e0, n1 - 5, e0 + 5, n1), {"niva_rh2000": 10.3})
        ms = Andring("markslag", "ms", kv(e0 + 6, n1 - 4, e0 + 9, n1 - 1), {"kod": 4})
        mag = Andring("magasin", "mag", kv(e0 + 6, n1 - 9, e0 + 9, n1 - 6), {"volym_m3": 3.0})
        vanster = Andring(
            "markhojd",
            "v",
            kv(g.x_min, g.y_min, g.x_min + (g.x_max - g.x_min) / 2, g.y_max),
            {"niva_rh2000": 10.6},
        )
        for namn, andr in (("hog", [hog]), ("kombi", [hog, ms, mag]), ("vanster", [vanster])):
            tl = tillampa_scenario(
                bas, k1, Planscenario(namn, (recept,), tuple(andr)), bas_recept_sha256=recept
            )
            t, d = tl.tillampning, blaytor_vid_mm(tl.graf, 300.0)
            f = FACIT[namn]
            self.assertEqual(
                (h(t.mark_z), h(t.osakerhet_z), h(t.markslag), int(t.andrad_mask.sum())),
                (f["mark_z"], f["osak"], f["markslag"], f["andrad"]),
                namn,
            )
            self.assertEqual(
                (float(d.lagrat_m3), float(np.max(d.djup_m)), h(d.djup_m)),
                (f["lagrat_m3"], f["maxdjup"], f["djup"]),
                namn,
            )

    def test_fel_ar_oforandrade(self) -> None:
        for andring in (
            Andring("byggnad_bort", "x", None, {"byggnads_id": "b1"}),
            Andring("markhojd", "d", kv(0, 0, 1, 1), {"delta_m": 0.2, "niva_rh2000": 5.0}),
            Andring("byggnad_ny", "h", kv(0, 0, 1, 1), {"hojd_m": 3.0}),
        ):
            with self.assertRaises(ScenarioLappFel):
                scenario_till_lapp(Planscenario("s", ("r",), (andring,)))


if __name__ == "__main__":
    unittest.main()
