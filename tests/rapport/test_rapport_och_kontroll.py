"""Enhetstester för rapportgenerering, kartografi, reproducerbarhet och talkontroll (T08)."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image

from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost, las_manifest, skapa_manifest
from rapport import (
    GILTIGA_RAPPORTTYPER,
    GransvardeKontroll,
    KartReferens,
    RapportKontrollFel,
    RapportUnderlag,
    TabellData,
    generera_rapport,
    kontrollera_rapport,
    las_underlag_json,
    rendera_diffkarta_png,
    rendera_djupkarta_png,
    skriv_underlag_json,
)


def _skapa_exempel_underlag(
    rapport_typ: str,
    kart_fil: Path,
    *,
    godkand: bool = True,
) -> RapportUnderlag:
    manifest = skapa_manifest(
        "dynamik",
        indata=[FilPost(roll="sankgraf", fil="sankgraf.parquet", sha256="abc123def456")],
        parametrar={"regn": "100-arsregn", "tidssteg_s": 5.0},
        utdata=[FilPost(roll="max_djup", fil="max_djup.tif", sha256="9876543210fe")],
        matvarden={"volymfel_procent": 0.04, "kortid_s": 2.1},
        varningar=["Inga varningar"],
        kodversion="deadbeef1234",
        start="2026-11-20T17:00:00+01:00",
    )
    return RapportUnderlag(
        rapport_typ=rapport_typ,
        titel=f"Provutredning Hägersten-Älvsjö ({rapport_typ})",
        omrade="Hägersten-Älvsjö provområde",
        manifest=manifest,
        metod=(
            "Kopplad 2D/graf-modellering med dimensionerande skyfall "
            "och Green-Ampt-infiltration."
        ),
        osakerhet_text="Ensemble med P10/P50/P90 visar högst känslighet i lågpunkter.",
        kalla_angivelser=("©Lantmäteriet", "SGU", "SMHI"),
        tal={
            "max_djup_m": 0.42,
            "volymfel_procent": 0.04,
            "drabbade_entreer_antal": 2 if godkand else 5,
            "max_insatstid_min": 8.5,
        },
        tabeller=[
            TabellData(
                titel="Sammanfattning per delområde",
                kolumner=["Delområde", "Maxdjup (m)", "Drabbade entréer"],
                rader=[
                    ["Rosteriet", 0.42, 2 if godkand else 5],
                    ["Parkstråket", 0.18, 0],
                ],
            )
        ],
        kartor=[
            KartReferens(
                kart_id="djupkarta",
                titel="Maximalt vattendjup",
                fil=kart_fil.name,
                kalla="©Lantmäteriet / Floden",
            )
        ],
        gransvarden=[
            GransvardeKontroll(
                namn="Maxdjup vid entré",
                uppmatt=0.12 if godkand else 0.35,
                grans=0.2,
                enhet="m",
                godkand=godkand,
            )
        ],
    )


class TestRapportOchKontroll(unittest.TestCase):
    def test_generera_alla_fem_rapporttyper_och_docx_struktur(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rot = Path(tmp)
            djup = np.array([[0.0, 0.25], [0.55, 1.1]], dtype=np.float64)
            karta = rendera_djupkarta_png(
                djup, rot / "djupkarta.png", titel="Maximalt vattendjup"
            )

            for rtyp in GILTIGA_RAPPORTTYPER:
                ut_mapp = rot / f"ut_{rtyp}"
                underlag = _skapa_exempel_underlag(rtyp, karta, godkand=True)
                res = generera_rapport(underlag, ut_mapp, kart_rot=rot)

                self.assertTrue(res.kontroll.godkand)
                self.assertTrue(res.filer["md"].is_file())
                self.assertTrue(res.filer["html"].is_file())
                self.assertTrue(res.filer["docx"].is_file())
                self.assertTrue(res.underlag_fil.is_file())
                self.assertTrue(res.manifest_fil.is_file())

                md_text = res.filer["md"].read_text(encoding="utf-8")
                self.assertIn(underlag.titel, md_text)
                self.assertIn(underlag.korning_id, md_text)
                self.assertIn("0.42", md_text)

                html_text = res.filer["html"].read_text(encoding="utf-8")
                self.assertIn("@media print", html_text)
                self.assertIn(underlag.korning_id, html_text)

                with zipfile.ZipFile(res.filer["docx"], "r") as zf:
                    namn = set(zf.namelist())
                    self.assertIn("[Content_Types].xml", namn)
                    self.assertIn("_rels/.rels", namn)
                    self.assertIn("word/document.xml", namn)
                    doc_xml = zf.read("word/document.xml").decode("utf-8")
                    ET.fromstring(doc_xml)
                    self.assertIn(underlag.titel, doc_xml)
                    self.assertIn(underlag.korning_id, doc_xml)
                    self.assertIn("0.42", doc_xml)

                manifest = las_manifest(res.manifest_fil)
                self.assertEqual(manifest.modul, "rapport")
                self.assertEqual(manifest.parametrar["korning_id"], underlag.korning_id)

    def test_inplanterat_pahittat_tal_stoppar_rapport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rot = Path(tmp)
            ut_mapp = rot / "ut"
            karta = rendera_djupkarta_png(
                np.zeros((4, 4), dtype=np.float64),
                rot / "djupkarta.png",
                titel="Testkarta",
            )
            underlag = _skapa_exempel_underlag("skyfallsutredning", karta, godkand=True)

            def _hallucinerad_text(u: RapportUnderlag) -> str:
                return (
                    "# {{meta:titel}}\n\n"
                    "Körnings-id: `{{meta:korning_id}}`\n\n"
                    "Maximalt djup är {{tal:max_djup_m:.2f}} m, "
                    "men vid korsningen blir djupet 0,87 m.\n\n"
                    "{{karta:djupkarta}}\n"
                )

            with self.assertRaises(RapportKontrollFel) as ctx:
                generera_rapport(
                    underlag,
                    ut_mapp,
                    text_leverantor=_hallucinerad_text,
                    kart_rot=rot,
                )

            self.assertTrue(any("0,87" in rad for rad in ctx.exception.avvikelser))
            self.assertFalse((ut_mapp / "rapport.md").exists())
            self.assertFalse((ut_mapp / "rapport.html").exists())
            self.assertFalse((ut_mapp / "rapport.docx").exists())

    def test_saknad_karta_stoppar_rapport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rot = Path(tmp)
            ut_mapp = rot / "ut"
            underlag = _skapa_exempel_underlag(
                "skyfallsutredning", rot / "saknas.png", godkand=True
            )

            with self.assertRaises(RapportKontrollFel) as ctx:
                generera_rapport(underlag, ut_mapp, kart_rot=rot)

            self.assertTrue(any("saknas" in rad.lower() for rad in ctx.exception.avvikelser))
            self.assertFalse((ut_mapp / "rapport.md").exists())

    def test_motstridig_slutsats_stoppar_rapport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rot = Path(tmp)
            karta = rendera_djupkarta_png(
                np.ones((4, 4), dtype=np.float64) * 0.35,
                rot / "djupkarta.png",
                titel="Testkarta",
            )
            underlag = _skapa_exempel_underlag("skyfallsutredning", karta, godkand=False)

            def _felaktig_slutsats(u: RapportUnderlag) -> str:
                return (
                    "# {{meta:titel}}\n\n"
                    "Körnings-id: `{{meta:korning_id}}`\n\n"
                    "{{sektion:nyckeltal}}\n\n"
                    "Slutsats: alla gränsvärden klaras utan problem.\n\n"
                    "{{karta:djupkarta}}\n"
                )

            with self.assertRaises(RapportKontrollFel) as ctx:
                generera_rapport(
                    underlag,
                    rot / "ut",
                    text_leverantor=_felaktig_slutsats,
                    kart_rot=rot,
                )

            self.assertTrue(any("Motstridig slutsats" in rad for rad in ctx.exception.avvikelser))
            self.assertFalse((rot / "ut" / "rapport.md").exists())

    def test_exakt_reproducerbarhet_fran_underlag_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rot = Path(tmp)
            karta = rendera_djupkarta_png(
                np.array([[0.1, 0.3], [0.4, 0.2]], dtype=np.float64),
                rot / "djupkarta.png",
                titel="Testkarta",
            )
            underlag = _skapa_exempel_underlag("alternativjamforelse", karta, godkand=True)
            underlag_fil = skriv_underlag_json(underlag, rot / "sparad_underlag.json")
            aterlast = las_underlag_json(underlag_fil)

            self.assertEqual(aterlast.korning_id, underlag.korning_id)

            res1 = generera_rapport(underlag, rot / "korning1", kart_rot=rot)
            res2 = generera_rapport(aterlast, rot / "korning2", kart_rot=rot)

            self.assertEqual(sha256_fil(res1.filer["md"]), sha256_fil(res2.filer["md"]))
            self.assertEqual(sha256_fil(res1.filer["html"]), sha256_fil(res2.filer["html"]))
            self.assertEqual(sha256_fil(res1.filer["docx"]), sha256_fil(res2.filer["docx"]))

    def test_kartrendering_djup_och_diff_png(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rot = Path(tmp)
            djup = np.linspace(0.0, 1.0, 24 * 32, dtype=np.float64).reshape((24, 32))
            diff = np.linspace(-0.4, 0.4, 24 * 32, dtype=np.float64).reshape((24, 32))

            djup_fil = rendera_djupkarta_png(
                djup, rot / "djup.png", titel="Djupkarta 100-årsregn"
            )
            diff_fil = rendera_diffkarta_png(
                diff, rot / "diff.png", titel="Skillnad före/efter"
            )

            self.assertTrue(djup_fil.is_file())
            self.assertTrue(diff_fil.is_file())

            with Image.open(djup_fil) as img1:
                self.assertEqual(img1.format, "PNG")
                self.assertGreater(img1.width, 300)
                self.assertGreater(img1.height, 250)

            with Image.open(diff_fil) as img2:
                self.assertEqual(img2.format, "PNG")
                self.assertGreater(img2.width, 300)
                self.assertGreater(img2.height, 250)

    def test_kontrollera_rapport_med_kommadecimal_avrundning_och_vitlista(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rot = Path(tmp)
            karta = rendera_djupkarta_png(
                np.zeros((4, 4), dtype=np.float64),
                rot / "djupkarta.png",
                titel="Testkarta",
            )
            underlag = _skapa_exempel_underlag("planbeskrivning", karta, godkand=True)
            text = (
                f"# {underlag.titel}\n\n"
                f"Körnings-id: `{underlag.korning_id}` (kod `{underlag.manifest.kodversion}`, "
                f"{underlag.manifest.kontrakt}, {underlag.manifest.start})\n\n"
                "Beräknat i EPSG:3011 / RH 2000. P10 och P90 redovisas. "
                "Maxdjup är 0,42 m och volymfel 0,04 %.\n\n"
                f"![Karta]({karta.name})\n"
            )
            res = kontrollera_rapport(text, underlag, kart_rot=rot)
            self.assertTrue(res.godkand)


if __name__ == "__main__":
    unittest.main()
