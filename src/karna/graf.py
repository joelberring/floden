"""Beräkningsgraf med innehållsadresserad cache.

Varje beräkning är en nod: `resultat = f(indatanycklar, lösarversion, parametrar)`. Nodens nyckel
är sha256 av just det, och resultatet sparas i `cache/<nyckel>/` med ett K0-manifest (`run.json`).
Samma fråga två gånger kostar inget. Ändras en indatafil eller en parameter får bara noderna
nedströms nya nycklar och räknas om; resten hämtas ur cachen.

En lösare är en vanlig funktion `f(indata, parametrar, utkatalog) -> matvarden`. `indata` är
en mappning roll → sökväg: en fil, eller katalogen med en föregående nods resultat.
Lösarens `version` ska höjas när koden ändras så att resultatet kan ändras.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost, las_manifest, skapa_manifest, skriv_manifest
from karna.hashning import kanonisk_json, nyckel_for

Losarfunktion = Callable[[Mapping[str, Path], Mapping[str, Any], Path], Mapping[str, Any] | None]


class GrafFel(RuntimeError):
    """Fel i beräkningsgrafen (cykel, saknad fil, lösaren skrev inget)."""


@dataclass(frozen=True)
class Losare:
    namn: str
    version: str
    funktion: Losarfunktion = field(compare=False)
    modul: str = "karna"
    """Flodens modul, t.ex. vatten, luft, buller, dagsljus (skrivs i run.json)."""


@dataclass(frozen=True)
class Nod:
    losare: Losare
    indata: Mapping[str, Nod | Path] = field(default_factory=dict)
    parametrar: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        kanonisk_json(dict(self.parametrar))  # parametrar måste gå att hasha


@dataclass(frozen=True)
class Resultat:
    nyckel: str
    katalog: Path
    fran_cache: bool
    matvarden: Mapping[str, Any]


class Cache:
    """Resultatkatalog per nyckel; katalogen finns bara när körningen är klar (atomiskt byte)."""

    def __init__(self, katalog: str | Path) -> None:
        self.katalog = Path(katalog)
        self.katalog.mkdir(parents=True, exist_ok=True)

    def sokvag(self, nyckel: str) -> Path:
        return self.katalog / nyckel

    def finns(self, nyckel: str) -> bool:
        return (self.sokvag(nyckel) / "run.json").is_file()


class Berakningsgraf:
    """Kör noder med cache. `raknade` visar vilka nycklar som räknades i denna instans."""

    def __init__(self, cache: Cache) -> None:
        self.cache = cache
        self.raknade: list[str] = []
        self._filnycklar: dict[tuple[str, int, int], str] = {}
        self._nycklar: dict[int, str] = {}

    def _filnyckel(self, fil: Path) -> str:
        if not fil.is_file():
            raise GrafFel(f"Indatafilen saknas: '{fil}'.")
        st = fil.stat()
        k = (str(fil.resolve()), st.st_size, st.st_mtime_ns)
        if k not in self._filnycklar:
            self._filnycklar[k] = sha256_fil(fil)
        return self._filnycklar[k]

    def nyckel(self, nod: Nod, _pa_vag: frozenset[int] = frozenset()) -> str:
        if id(nod) in _pa_vag:
            raise GrafFel("Cykel i beräkningsgrafen.")
        if id(nod) in self._nycklar:
            return self._nycklar[id(nod)]
        indata: dict[str, str] = {}
        for roll, kalla in nod.indata.items():
            if isinstance(kalla, Nod):
                indata[roll] = "nod:" + self.nyckel(kalla, _pa_vag | {id(nod)})
            else:
                indata[roll] = "fil:" + self._filnyckel(Path(kalla))
        n = nyckel_for(
            {
                "losare": nod.losare.namn,
                "version": nod.losare.version,
                "parametrar": dict(nod.parametrar),
                "indata": indata,
            }
        )
        self._nycklar[id(nod)] = n
        return n

    def kor(self, nod: Nod) -> Resultat:
        n = self.nyckel(nod)
        mal = self.cache.sokvag(n)
        if self.cache.finns(n):
            return Resultat(n, mal, True, dict(las_manifest(mal / "run.json").matvarden))

        sokvagar: dict[str, Path] = {}
        poster: list[FilPost] = []
        for roll, kalla in nod.indata.items():
            if isinstance(kalla, Nod):
                r = self.kor(kalla)
                sokvagar[roll] = r.katalog
                poster.append(FilPost(roll, str(r.katalog / "run.json"), r.nyckel))
            else:
                p = Path(kalla)
                sokvagar[roll] = p
                poster.append(FilPost(roll, str(p), self._filnyckel(p)))

        tmp = Path(tempfile.mkdtemp(prefix=f".{n[:12]}-", dir=self.cache.katalog))
        try:
            matvarden = dict(nod.losare.funktion(sokvagar, dict(nod.parametrar), tmp) or {})
            utdata = [
                FilPost(f.name, f.name, sha256_fil(f))
                for f in sorted(tmp.iterdir())
                if f.is_file() and f.name != "run.json"
            ]
            manifest = skapa_manifest(
                nod.losare.modul,
                indata=poster,
                parametrar={
                    "losare": nod.losare.namn,
                    "losarversion": nod.losare.version,
                    "nyckel": n,
                    **dict(nod.parametrar),
                },
                utdata=utdata,
                matvarden=matvarden,
            )
            skriv_manifest(manifest, tmp / "run.json")
            try:
                os.replace(tmp, mal)
            except OSError:
                # En annan process hann först med samma nyckel: dess resultat är likvärdigt.
                if not self.cache.finns(n):
                    raise
                shutil.rmtree(tmp, ignore_errors=True)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        self.raknade.append(n)
        return Resultat(n, mal, False, matvarden)
