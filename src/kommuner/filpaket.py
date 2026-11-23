"""Öppna filpaket (ZIP) från en kommun: hämtas en gång till en cache med sha256 och öppnas därifrån.

Göteborgs Stad publicerar baskartan och höjdmodellen som nedladdningsbara filpaket (CC0, utan
inloggning) på opengeodata.goteborg.se. Paketen är stora; därför hämtas varje adress en gång,
strömmas till disk (aldrig hela filen i minnet), får sin sha256 beräknad under hämtningen och
ligger sedan innehållsadresserat i cachen: `<cache>/<sha256>/<filnamn>`. Adressen pekar ut paketet
i `<cache>/index.json` så att nästa körning slipper hämta om (använd `tvinga=True` för att hämta på nytt).

Regler:
- Bara https till en tillåten värd (samma kontroll som WFS-källorna, `kontrollera_url`).
- Inget packas upp i förväg. ZIP-medlemmar läses direkt (GDAL /vsizip/) eller packas upp en och en
  till en tillfällig katalog med ett kontrollerat namn (inga `..` eller absoluta sökvägar).
- Inga nycklar eller personuppgifter: det är öppna data utan konto.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from gemensamt.hash import sha256_fil
from kommuner.wfs import KommunKallaFel, kontrollera_url

INDEX = "index.json"
STANDARD_MAX_BYTES = 8 * 1024**3
_BLOCK = 1 << 20

# (url, målfil) -> None. Skriver hela svaret till målfilen. Utbytbar i tester.
FilHamtare = Callable[[str, Path], None]


@dataclass(frozen=True)
class Filpaket:
    url: str
    fil: Path
    sha256: str
    storlek: int
    hamtad_nu: bool

    @property
    def namn(self) -> str:
        return self.fil.name


def standard_filhamtare(timeout_s: float = 120.0, max_bytes: int = STANDARD_MAX_BYTES) -> FilHamtare:
    """Strömmande hämtning med storleksgräns. Fel blir `KommunKallaFel` med värdnamn och orsak."""

    def hamta(url: str, mal: Path) -> None:
        host = urllib.parse.urlsplit(url).hostname or "?"
        req = urllib.request.Request(url, headers={"User-Agent": "Floden/1.0 (open-data-client)"})
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp, mal.open("wb") as ut:  # noqa: S310 - https, tillåten värd
                total = 0
                while True:
                    bit = resp.read(_BLOCK)
                    if not bit:
                        break
                    total += len(bit)
                    if total > max_bytes:
                        raise KommunKallaFel(
                            f"{host}: filen är större än gränsen {max_bytes / 1024**3:.0f} GiB; avbröt."
                        )
                    ut.write(bit)
        except urllib.error.HTTPError as exc:
            raise KommunKallaFel(
                f"{host} svarade med HTTP {exc.code} för {url}. Adressen kan vara ändrad: kontrollera "
                "den på kommunens datasida."
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise KommunKallaFel(
                f"{host} svarar inte ({type(exc).__name__}). Kontrollera nätet och försök igen."
            ) from exc

    return hamta


def _las_index(cache: Path) -> dict[str, dict[str, object]]:
    f = cache / INDEX
    if not f.is_file():
        return {}
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _skriv_index(cache: Path, index: dict[str, dict[str, object]]) -> None:
    tmp = cache / (INDEX + ".tmp")
    tmp.write_text(json.dumps(index, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, cache / INDEX)


def _filnamn(url: str) -> str:
    n = Path(urllib.parse.unquote(urllib.parse.urlsplit(url).path)).name
    n = re.sub(r"[^A-Za-z0-9._-]", "_", n)
    if not n or n.startswith("."):
        raise KommunKallaFel(f"Adressen {url} pekar inte ut en fil.")
    return n


def hamta_filpaket(
    url: str,
    cache: Path,
    tillatna_varddomaner: Sequence[str],
    *,
    forvantad_sha256: str | None = None,
    tvinga: bool = False,
    hamtare: FilHamtare | None = None,
) -> Filpaket:
    """Hämtar `url` till cachen (en gång) och returnerar paketet med sha256.

    `forvantad_sha256` (om kommunfilen anger en) avvisar ett paket som inte stämmer.
    """
    kontrollera_url(url, tillatna_varddomaner)
    cache.mkdir(parents=True, exist_ok=True)
    index = _las_index(cache)
    post = index.get(url)
    if post and not tvinga:
        sha = str(post.get("sha256", ""))
        namn = str(post.get("fil", ""))
        fil = cache / sha / namn
        if sha and namn and fil.is_file() and fil.stat().st_size == post.get("storlek"):
            if forvantad_sha256 and forvantad_sha256 != sha:
                raise KommunKallaFel(f"{namn}: sha256 {sha[:12]} stämmer inte med kommunfilen.")
            return Filpaket(url, fil, sha, int(fil.stat().st_size), False)

    namn = _filnamn(url)
    with tempfile.TemporaryDirectory(dir=cache, prefix=".hamtar-") as tmpd:
        tmp = Path(tmpd) / namn
        (hamtare or standard_filhamtare())(url, tmp)
        if not tmp.is_file() or tmp.stat().st_size == 0:
            raise KommunKallaFel(f"{url} gav en tom fil.")
        sha = sha256_fil(tmp)
        if forvantad_sha256 and forvantad_sha256 != sha:
            raise KommunKallaFel(f"{namn}: sha256 {sha[:12]} stämmer inte med kommunfilen.")
        mal = cache / sha
        mal.mkdir(exist_ok=True)
        os.replace(tmp, mal / namn)
    fil = cache / sha / namn
    index[url] = {"sha256": sha, "fil": namn, "storlek": fil.stat().st_size}
    _skriv_index(cache, index)
    return Filpaket(url, fil, sha, int(fil.stat().st_size), True)


def lokalt_filpaket(fil: Path) -> Filpaket:
    """Ett redan nedladdat paket på disk (t.ex. `--hojdmodell-fil`)."""
    if not fil.is_file():
        raise KommunKallaFel(f"Filen finns inte: {fil}")
    return Filpaket(fil.resolve().as_uri(), fil.resolve(), sha256_fil(fil), fil.stat().st_size, False)


def zip_medlemmar(paket: Path, andelser: Sequence[str]) -> list[str]:
    """ZIP-medlemmarna vars namn slutar på någon av `andelser` (skiftlägesokänsligt), säkra namn."""
    try:
        with zipfile.ZipFile(paket) as z:
            namn = z.namelist()
    except zipfile.BadZipFile as exc:
        raise KommunKallaFel(f"{paket.name} är ingen giltig ZIP-fil ({exc}).") from exc
    ut = []
    for n in namn:
        if n.endswith("/") or not n.lower().endswith(tuple(a.lower() for a in andelser)):
            continue
        if n.startswith("/") or ".." in Path(n).parts or "\\" in n or ":" in n:
            continue  # osäker sökväg: används aldrig
        ut.append(n)
    return sorted(ut)


def vsizip(paket: Path, medlem: str) -> str:
    """GDAL-sökväg till en medlem i en ZIP utan uppackning."""
    return f"/vsizip/{paket.resolve().as_posix()}/{medlem}"


def packa_upp_medlem(paket: Path, medlem: str, mal_mapp: Path, *, max_bytes: int = STANDARD_MAX_BYTES) -> Path:
    """Packar upp en medlem under ett kontrollerat namn (aldrig medlemmens egen sökväg)."""
    if medlem.startswith("/") or ".." in Path(medlem).parts:
        raise KommunKallaFel(f"Osäker sökväg i ZIP-filen: {medlem!r}.")
    mal_mapp.mkdir(parents=True, exist_ok=True)
    mal = mal_mapp / hashlib.sha256(medlem.encode()).hexdigest()[:12]
    mal = mal.with_suffix(Path(medlem).suffix.lower())
    with zipfile.ZipFile(paket) as z:
        info = z.getinfo(medlem)
        if info.file_size > max_bytes:
            raise KommunKallaFel(f"{medlem} är större än gränsen vid uppackning.")
        with z.open(info) as k, mal.open("wb") as ut:
            tot = 0
            while bit := k.read(_BLOCK):
                tot += len(bit)
                if tot > max_bytes:
                    raise KommunKallaFel(f"{medlem} är större än gränsen vid uppackning.")
                ut.write(bit)
    return mal
