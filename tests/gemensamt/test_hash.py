"""Enhetstester för `gemensamt.hash`."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from gemensamt.hash import sha256_bytes, sha256_fil


class TestHash(unittest.TestCase):
    def test_sha256_bytes_kant_svar(self) -> None:
        data = b"floden-skyfall"
        forvantad = hashlib.sha256(data).hexdigest()
        self.assertEqual(sha256_bytes(data), forvantad)
        self.assertEqual(
            sha256_bytes(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )

    def test_sha256_fil_strommande_over_flera_block(self) -> None:
        innehall = b"0123456789abcdef" * 150_000  # ~2.4 MB (> 1 MiB blockstorlek)
        with tempfile.TemporaryDirectory() as tmp:
            fil = Path(tmp) / "stor.bin"
            fil.write_bytes(innehall)
            self.assertEqual(sha256_fil(fil), sha256_bytes(innehall))

    def test_sha256_fil_saknad_fil_ger_fel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(FileNotFoundError):
            sha256_fil(Path(tmp) / "saknas.bin")

    def test_sha256_fil_katalog_ger_fel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(IsADirectoryError):
            sha256_fil(Path(tmp))


if __name__ == "__main__":
    unittest.main()
