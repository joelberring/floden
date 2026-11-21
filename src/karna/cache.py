"""Innehållsadresserad resultatcache enligt kontrakt K22/v1 (T20).

Layout:

```
<cache_rot>/
  jobb/<nn>/<nyckel>/
    nyckel.json     det kanoniska objekt som hashades till <nyckel>
    utdata/         lösarens filer
    utdata.json     roller, sha256, mätvärden och varningar från lösaren
    run.json        K0-manifest
  scenarier/<nn>/<nyckel>/ruta/   K1-ruta med lappen tillämpad (saknas om rutan inte ändrades)
```

En post skrivs först i en temporär katalog och flyttas sedan på plats med en atomär
`rename`. En halvskriven post kan därför aldrig läsas som en träff.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

KONTRAKT_K22 = "K22/v1"


class CacheFel(Exception):
    """Fel i resultatcachen."""


class ResultatCache:
    """Cache med en katalog per nyckel, sharded på de två första hextecknen."""

    def __init__(self, rot: str | Path) -> None:
        self.rot = Path(rot)
        self.rot.mkdir(parents=True, exist_ok=True)

    def _post(self, slag: str, nyckel: str) -> Path:
        if len(nyckel) != 64 or any(c not in "0123456789abcdef" for c in nyckel):
            raise CacheFel(f"Ogiltig nyckel '{nyckel}', förväntade 64 hextecken.")
        return self.rot / slag / nyckel[:2] / nyckel

    def jobbmapp(self, nyckel: str) -> Path:
        return self._post("jobb", nyckel)

    def scenariomapp(self, nyckel: str) -> Path:
        return self._post("scenarier", nyckel)

    def finns(self, slag: str, nyckel: str) -> bool:
        mapp = self._post(slag, nyckel)
        return (mapp / ".klar").is_file()

    def skapa(
        self,
        slag: str,
        nyckel: str,
        skrivare: Callable[[Path], dict[str, Any] | None],
    ) -> Path:
        """Skapar en post atomärt. `skrivare` fyller en temporär katalog.

        Om posten redan finns returneras den direkt utan att `skrivare` anropas.
        """
        mal = self._post(slag, nyckel)
        if (mal / ".klar").is_file():
            return mal
        mal.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=f".{nyckel[:12]}-", dir=mal.parent))
        try:
            meta = skrivare(tmp)
            (tmp / ".klar").write_text(
                json.dumps({"kontrakt": KONTRAKT_K22, **(meta or {})}, ensure_ascii=False),
                encoding="utf-8",
            )
            if mal.exists() and not (mal / ".klar").is_file():
                # En ofullständig post från en avbruten körning; ersätt den.
                shutil.rmtree(mal, ignore_errors=True)
            try:
                tmp.rename(mal)
            except OSError:
                # En annan process hann före. Deras post är lika giltig som vår.
                if (mal / ".klar").is_file():
                    shutil.rmtree(tmp, ignore_errors=True)
                    return mal
                raise
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return mal
