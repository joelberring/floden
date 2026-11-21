"""Bygger webbklienten (`webb/`, TypeScript och Vite) när `floden webb` startar.

`floden webb` ska räcka för att få något att titta på: finns ingen byggd klient (`webb/dist`) eller
är källfilerna nyare än bygget körs `npm ci` (om `node_modules` saknas eller `package-lock.json`
ändrats) och `npm run build`. Servern serverar sedan klienten och API:t från samma port.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path


class KlientByggeFel(Exception):
    """Klienten kunde inte byggas (saknad npm eller misslyckat bygge)."""


def _senaste_andring(mapp: Path, undvik: tuple[str, ...]) -> float:
    senast = 0.0
    for fil in mapp.rglob("*"):
        if fil.is_file() and not any(d in fil.parts for d in undvik):
            senast = max(senast, fil.stat().st_mtime)
    return senast


def klient_ar_aktuell(webb: Path) -> bool:
    """Sant om `dist/index.html` finns och är nyare än alla källfiler och beroendefilerna."""
    index = webb / "dist" / "index.html"
    if not index.is_file():
        return False
    kallor = [
        webb / n for n in ("index.html", "package.json", "package-lock.json", "vite.config.ts")
    ]
    nyast = max(
        [k.stat().st_mtime for k in kallor if k.is_file()] + [_senaste_andring(webb / "src", ())]
    )
    return index.stat().st_mtime >= nyast


def sakerstall_klient(
    webb: Path,
    *,
    tvinga: bool = False,
    logg: Callable[[str], None] = print,
    kor: Callable[[list[str], Path], int] | None = None,
) -> bool:
    """Bygger klienten om den saknas eller är gammal. Returnerar True om något byggdes."""
    if not (webb / "package.json").is_file():
        raise KlientByggeFel(f"Hittar ingen webbklient i {webb} (package.json saknas).")
    if not tvinga and klient_ar_aktuell(webb):
        return False
    npm = shutil.which("npm")
    if npm is None and kor is None:
        raise KlientByggeFel(
            "npm hittades inte. Installera Node.js (nodejs.org eller `brew install node`) och "
            "kör kommandot igen."
        )

    def _kor(kommando: list[str], cwd: Path) -> int:
        return subprocess.run(kommando, cwd=cwd, check=False).returncode

    kor = kor or _kor
    npm = npm or "npm"
    nm = webb / "node_modules"
    lock = webb / "package-lock.json"
    inst = nm / ".package-lock.json"
    if (
        tvinga
        or not nm.is_dir()
        or (lock.is_file() and inst.is_file() and lock.stat().st_mtime > inst.stat().st_mtime)
    ):
        logg("Installerar webbens beroenden (npm ci) ...")
        if kor([npm, "ci"], webb) != 0:
            raise KlientByggeFel("npm ci misslyckades.")
    logg("Bygger webbklienten (npm run build) ...")
    if kor([npm, "run", "build"], webb) != 0:
        raise KlientByggeFel("npm run build misslyckades.")
    return True
