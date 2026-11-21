"""`GET /api/moduler`: modullistan som webben ritar, och påslag via kärnans frågelager."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from karna.fraga import registrera_analystyp
from webb.api import FlodenApiApp
from webb.moduler import MODULER, lista_moduler

_REPO_ROT = Path(__file__).resolve().parents[2]


class TestModuler(unittest.TestCase):
    def test_get_moduler_listar_fyra_moduler_med_vatten_i_drift(self) -> None:
        app = FlodenApiApp(
            testsvit_rot=_REPO_ROT / "testsvit", konfig_fil=_REPO_ROT / "config.toml"
        )
        status, headers, kropp = app.hantera_forfragan("GET", "/api/moduler")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        data: dict[str, Any] = json.loads(kropp.decode("utf-8"))
        ids = [m["modul_id"] for m in data["moduler"]]
        self.assertEqual(ids, ["vatten", "luft", "buller", "dagsljus"])
        self.assertEqual([m["nr"] for m in data["moduler"]], ["01", "02", "03", "04"])
        status_per = {m["modul_id"]: m["status"] for m in data["moduler"]}
        self.assertEqual(status_per["vatten"], "i_drift")
        for m in data["moduler"]:
            self.assertIn(m["status"], ("i_drift", "kommande"))
            self.assertRegex(m["kartfarg"], r"^#[0-9a-f]{6}$")

    def test_modul_slas_pa_nar_den_registrerar_en_analystyp(self) -> None:
        kommande = next(m.modul_id for m in MODULER if not m.i_drift)
        fore = {m["modul_id"]: m for m in lista_moduler()["moduler"]}
        registrera_analystyp(f"test_webb_{kommande}", kommande)
        efter = {m["modul_id"]: m for m in lista_moduler()["moduler"]}
        self.assertEqual(efter[kommande]["status"], "i_drift")
        self.assertIn(f"test_webb_{kommande}", efter[kommande]["analystyper"])
        self.assertEqual(fore["vatten"]["status"], efter["vatten"]["status"])


if __name__ == "__main__":
    unittest.main()
