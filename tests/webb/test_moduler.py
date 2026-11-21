"""`GET /api/moduler`: speglar kärnans registrerade lösare och analystyper, inte en fast lista."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any, ClassVar

from karna import analyslosare
from karna.analyslosare import Analyslosare, LosarKontext, LosarUtdata, lista_losare
from webb.api import FlodenApiApp
from webb.moduler import las_modulstatus, lista_moduler, visningsnamn

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
    def test_get_moduler_listar_exakt_de_fyra_produktmodulerna(self) -> None:
        app = FlodenApiApp(
            testsvit_rot=_REPO_ROT / "testsvit", konfig_fil=_REPO_ROT / "config.toml"
        )
        status, headers, kropp = app.hantera_forfragan("GET", "/api/moduler")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        data: dict[str, Any] = json.loads(kropp.decode("utf-8"))
        ids = [m["modul_id"] for m in data["moduler"]]
        # Geometri är ett verktyg i kärnan, inte en modul, och ska inte visas som kort.
        self.assertEqual(ids, ["vatten", "luft", "buller", "dagsljus"])
        self.assertEqual(
            [m["nr"] for m in data["moduler"]], [f"{n:02d}" for n in range(1, len(ids) + 1)]
        )
        for m in data["moduler"]:
            self.assertRegex(m["kartfarg"], r"^#[0-9a-f]{6}$")
            self.assertTrue(m["status_text"])
            self.assertNotIn("[", m["status_text"])

    def test_losarna_per_modul_ar_de_kärnan_har_registrerade_i_produktmodulerna(self) -> None:
        moduler = lista_moduler()["moduler"]
        fran_webben = sorted(los["namn"] for m in moduler for los in m["losare"])
        fran_karnan = sorted(
            los.namn for los in lista_losare() if los.doman in {m["modul_id"] for m in moduler}
        )
        self.assertEqual(fran_webben, fran_karnan)
        per_modul = {m["modul_id"]: m for m in moduler}
        sol = next(
            x for x in per_modul["dagsljus"]["losare"] if x["namn"].endswith("soltimmar_mark")
        )
        self.assertEqual(sol["utdataroller"][0], "soltimmar")
        self.assertEqual(sol["visningsnamn"], "Soltimmar på mark")
        self.assertGreater(sol["radie_m"], 0.0)

    def test_status_i_demon_luft_och_buller_med_exakt_text(self) -> None:
        m = {x["modul_id"]: x for x in lista_moduler({"vatten", "dagsljus"})["moduler"]}
        self.assertEqual(m["vatten"]["status"], "finns_i_demon")
        self.assertEqual(m["vatten"]["status_text"], "Finns i demon")
        self.assertEqual(m["dagsljus"]["status_text"], "Finns i demon")
        self.assertEqual(m["luft"]["status"], "finns_ej_forberaknad")
        self.assertEqual(
            m["luft"]["status_text"],
            "Finns, men en beräkning tar mer än tio minuter – inte förberäknad i demon",
        )
        self.assertEqual(m["buller"]["status"], "under_utveckling")
        buller = las_modulstatus()["buller"]
        self.assertEqual(
            m["buller"]["status_text"],
            "Under utveckling – metodkontroll pågår "
            f"({buller['testfall_godkanda']} av {buller['testfall_totalt']} officiella "
            "testfall godkända)",
        )

    def test_utan_forkord_demo_pastas_inte_att_modulen_finns_i_demon(self) -> None:
        m = {x["modul_id"]: x for x in lista_moduler(set())["moduler"]}
        self.assertEqual(m["vatten"]["status"], "finns")
        self.assertEqual(m["vatten"]["status_text"], "Finns, men är inte förberäknad i demon")

    def test_buller_forblir_under_utveckling_aven_om_en_analystyp_registreras(self) -> None:
        # Statusen för buller kommer från metodkontrollen (modulstatus.toml), inte från att något
        # registrerats: ett registrerat verktyg gör inte metoden godkänd.
        from karna.fraga import registrera_analystyp

        registrera_analystyp("test_webb_buller", "buller")
        efter = {m["modul_id"]: m for m in lista_moduler(set())["moduler"]}
        self.assertEqual(efter["buller"]["status"], "under_utveckling")

    def test_luft_slas_pa_nar_en_losare_registreras_men_forblir_oberäknad_i_demon(self) -> None:
        analyslosare.registrera_losare(_ProvLuft())
        try:
            efter = {m["modul_id"]: m for m in lista_moduler(set())["moduler"]}
        finally:
            analyslosare._REGISTER.pop("luft.webbprov", None)
        self.assertIn("luft.webbprov", [x["namn"] for x in efter["luft"]["losare"]])
        self.assertEqual(efter["luft"]["status"], "finns_ej_forberaknad")

    def test_visningsnamn_ar_vardagliga(self) -> None:
        self.assertEqual(visningsnamn("vatten.statisk_fyllning"), "Maxdjup vid skyfall")
        self.assertEqual(visningsnamn("x.okand_losare"), "Okand losare")

    def test_buller_siffrorna_stammer_med_kallan(self) -> None:
        buller = las_modulstatus()["buller"]
        kalla = (_REPO_ROT / buller["kalla"]).read_text(encoding="utf-8")
        self.assertIn(f"**{buller['testfall_godkanda']} godkända**", kalla)
        self.assertIn(f"({buller['testfall_totalt']} fall ur", kalla)


if __name__ == "__main__":
    unittest.main()
