"""`GET /api/moduler`: speglar kärnans registrerade lösare och analystyper, inte en fast lista."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any, ClassVar

from karna import analyslosare
from karna.analyslosare import Analyslosare, LosarKontext, LosarUtdata, lista_losare
from karna.fraga import registrera_analystyp
from webb.api import FlodenApiApp
from webb.moduler import MODULER, lista_moduler

_REPO_ROT = Path(__file__).resolve().parents[2]


class _ProvLuft(Analyslosare):
    namn: ClassVar[str] = "luft.webbprov"
    doman: ClassVar[str] = "luft"
    version: ClassVar[str] = "1"
    beskrivning: ClassVar[str] = "Provlösare som bara finns i testet."
    utdataroller: ClassVar[tuple[str, ...]] = ("halt",)

    def kor(self, kontext: LosarKontext) -> LosarUtdata:  # pragma: no cover - körs aldrig
        raise NotImplementedError


class TestModuler(unittest.TestCase):
    def test_get_moduler_listar_produktmodulerna_forst_med_status_ur_karnan(self) -> None:
        app = FlodenApiApp(
            testsvit_rot=_REPO_ROT / "testsvit", konfig_fil=_REPO_ROT / "config.toml"
        )
        status, headers, kropp = app.hantera_forfragan("GET", "/api/moduler")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        data: dict[str, Any] = json.loads(kropp.decode("utf-8"))
        ids = [m["modul_id"] for m in data["moduler"]]
        self.assertEqual(ids[:4], ["vatten", "luft", "buller", "dagsljus"])
        self.assertEqual(
            [m["nr"] for m in data["moduler"]], [f"{n:02d}" for n in range(1, len(ids) + 1)]
        )
        for m in data["moduler"]:
            self.assertIn(m["status"], ("i_drift", "kommande"))
            self.assertRegex(m["kartfarg"], r"^#[0-9a-f]{6}$")
            # Status följer lösarna och analystyperna, inte en flagga i webben.
            self.assertEqual(
                m["status"] == "i_drift", bool(m["losare"]) or bool(m["analystyper"]), m
            )

    def test_losarna_per_modul_ar_exakt_de_kärnan_har_registrerade(self) -> None:
        moduler = lista_moduler()["moduler"]
        fran_webben = sorted(los["namn"] for m in moduler for los in m["losare"])
        fran_karnan = sorted(los.namn for los in lista_losare())
        self.assertEqual(fran_webben, fran_karnan)
        per_modul = {m["modul_id"]: m for m in moduler}
        self.assertIn(
            "dagsljus.soltimmar_mark", [x["namn"] for x in per_modul["dagsljus"]["losare"]]
        )
        self.assertIn("vatten.statisk_fyllning", [x["namn"] for x in per_modul["vatten"]["losare"]])
        sol = next(
            x for x in per_modul["dagsljus"]["losare"] if x["namn"].endswith("soltimmar_mark")
        )
        self.assertEqual(sol["utdataroller"][0], "soltimmar")
        self.assertGreater(sol["radie_m"], 0.0)

    def test_modul_slas_pa_nar_en_losare_registreras(self) -> None:
        fore = {m["modul_id"]: m for m in lista_moduler()["moduler"]}
        self.assertEqual(fore["luft"]["status"], "kommande")
        analyslosare.registrera_losare(_ProvLuft())
        try:
            efter = {m["modul_id"]: m for m in lista_moduler()["moduler"]}
        finally:
            analyslosare._REGISTER.pop("luft.webbprov", None)
        self.assertEqual(efter["luft"]["status"], "i_drift")
        self.assertEqual([x["namn"] for x in efter["luft"]["losare"]], ["luft.webbprov"])
        self.assertEqual(fore["vatten"]["status"], efter["vatten"]["status"])

    def test_modul_slas_pa_nar_den_registrerar_en_analystyp(self) -> None:
        kommande = next(m.modul_id for m in MODULER if m.modul_id == "buller")
        fore = {m["modul_id"]: m for m in lista_moduler()["moduler"]}
        registrera_analystyp(f"test_webb_{kommande}", kommande)
        efter = {m["modul_id"]: m for m in lista_moduler()["moduler"]}
        self.assertEqual(efter[kommande]["status"], "i_drift")
        self.assertIn(f"test_webb_{kommande}", efter[kommande]["analystyper"])
        self.assertEqual(fore["vatten"]["status"], efter["vatten"]["status"])


if __name__ == "__main__":
    unittest.main()
