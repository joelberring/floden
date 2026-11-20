"""Enhetstester för `gemensamt.manifest` (kontrakt K0/v1)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from gemensamt.manifest import (
    KONTRAKT_K0,
    FilPost,
    ManifestFel,
    RunManifest,
    hamta_kodversion,
    las_manifest,
    skapa_manifest,
    skriv_manifest,
)


class TestManifest(unittest.TestCase):
    def test_skriv_och_las_tillbaka_k0(self) -> None:
        manifest = skapa_manifest(
            "dynamik",
            indata=[FilPost(roll="sankgraf", fil="sankgraf.parquet", sha256="abc123")],
            parametrar={"regn": "100-arsregn", "tidssteg_s": 5.0},
            utdata=[FilPost(roll="max_djup", fil="max_djup.tif", sha256="def456")],
            matvarden={"volymfel_rel": 0.0004, "kortid_s": 2.1},
            varningar=["12 celler utan markpunkter inom 3 m"],
            kodversion="deadbeef1234",
            start="2026-11-20T17:00:00+01:00",
        )

        with tempfile.TemporaryDirectory() as tmp:
            fil = Path(tmp) / "ut" / "run.json"
            skriv_manifest(manifest, fil)
            self.assertTrue(fil.is_file())

            last = las_manifest(fil)
            self.assertEqual(last, manifest)
            self.assertEqual(last.kontrakt, KONTRAKT_K0)
            self.assertEqual(last.modul, "dynamik")
            self.assertEqual(last.indata[0].roll, "sankgraf")
            self.assertEqual(last.matvarden["volymfel_rel"], 0.0004)

    def test_avvisar_felaktigt_kontrakt(self) -> None:
        with self.assertRaises(ManifestFel):
            RunManifest(
                kontrakt="K0/v99",
                modul="dynamik",
                kodversion="abc",
                start="2026-11-20T17:00:00+01:00",
            )

    def test_avvisar_saknat_obligatoriskt_falt_vid_lasning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fil = Path(tmp) / "run.json"
            fil.write_text(
                json.dumps({"kontrakt": "K0/v1", "modul": "dynamik"}),
                encoding="utf-8",
            )
            with self.assertRaises(ManifestFel):
                las_manifest(fil)

    def test_avvisar_ogiltig_filpost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fil = Path(tmp) / "run.json"
            fil.write_text(
                json.dumps(
                    {
                        "kontrakt": "K0/v1",
                        "modul": "dynamik",
                        "kodversion": "abc",
                        "start": "2026-11-20T17:00:00+01:00",
                        "indata": [{"roll": "sankgraf", "fil": "x.parquet"}],
                        "parametrar": {},
                        "utdata": [],
                        "matvarden": {},
                        "varningar": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ManifestFel):
                las_manifest(fil)

    def test_hamta_kodversion_utan_git_returnerar_okand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(hamta_kodversion(repo_rot=tmp), "okand")


if __name__ == "__main__":
    unittest.main()
