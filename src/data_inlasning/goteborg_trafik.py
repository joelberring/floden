# SPDX-License-Identifier: GPL-3.0-or-later
"""Inläsare för Göteborgs Stads öppna trafikmängdskatalog (1970–2020, ÅMVD).

Göteborgs Stad publicerar uppmätta kommunala trafikmängder per gata och delsträcka på
`https://trafikmangder.stadsbyggnad.goteborg.se/1970-2018/` (1 469 gator i ren HTML,
ISO-8859-1, med mätningar t.o.m. 2020).

Viktiga principer (`AGENTS.md`):
- Göteborgs katalog redovisar **ÅMVD** (årsmedelvardagsdygnstrafik, helgfria mån–fre),
  INTE ÅDT (årsmedeldygnstrafik). Denna modul omvandlar ALDRIG tyst ÅMVD till ÅDT och hittar
  aldrig på saknad tung andel eller skyltad hastighet.
- Vid export till Flodens kommunala trafik-CSV (`las_kommunal_trafik_fil`) sätts alltid
  `matt_typ = "ÅMVD"`, `osaker = "true"` och explicit källhänvisning med mätår.
- Strikt HTTPS + värd-allowlist (`trafikmangder.stadsbyggnad.goteborg.se`), SHA-256-cachad
  hämtning samt injicerbar `hamtare` för 100 % nätverksfria enhetstester.
"""

from __future__ import annotations

import csv
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Final

from data_inlasning.cache import DataInlasningFel, LokalDataCache
from gemensamt.hash import sha256_bytes

GOTEBORG_TRAFIKMANGD_BAS_URL: Final[str] = (
    "https://trafikmangder.stadsbyggnad.goteborg.se/1970-2018"
)
TILLATNA_TRAFIKMANGD_VARDAR: Final[frozenset[str]] = frozenset(
    {"trafikmangder.stadsbyggnad.goteborg.se"}
)
GOTEBORG_TRAFIKMANGD_KALLA: Final[str] = (
    "Göteborgs Stad Trafikmängdskatalogen (1970–2020, ÅMVD)"
)
_AR_RE: Final[re.Pattern[str]] = re.compile(r"^\d{4}$")

HamtareFn = Callable[[str], bytes]


class GoteborgTrafikFel(DataInlasningFel):
    """Fel vid hämtning eller tolkning av Göteborgs Stads trafikmängdskatalog."""


@dataclass(frozen=True)
class GoteborgTrafikMatning:
    """En uppmätt årsrad för en gatusnitt-delsträcka i Göteborgs trafikmängdskatalog (ÅMVD)."""

    gatunamn: str
    delstracka: str
    ar: int
    amvd_totalt: float
    riktning: str | None = None  # "MC" (mot centrum), "FC" (från centrum) eller None (båda)
    amvd_tung: float | None = None
    andel_tung_pct: float | None = None
    maxtim_mot_centrum: float | None = None
    maxtim_fran_centrum: float | None = None
    maxtim_summa: float | None = None
    skyltad_km_h: float | None = None
    median_km_h: float | None = None
    p85_km_h: float | None = None
    kalla_url: str | None = None


def kontrollera_trafikmangd_url(url: str) -> str:
    """Kontrollerar strikt HTTPS och tillåten värd (SSRF-skydd)."""
    if not isinstance(url, str) or not url.strip():
        raise GoteborgTrafikFel("URL mot Göteborgs trafikmängdskatalog får inte vara tom.")
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme != "https":
        raise GoteborgTrafikFel(f"Endast HTTPS tillåts, fick {url!r}.")
    if parsed.username is not None or parsed.password is not None:
        raise GoteborgTrafikFel("Inloggningsuppgifter i URL tillåts inte.")
    host = (parsed.hostname or "").lower()
    if host not in TILLATNA_TRAFIKMANGD_VARDAR:
        raise GoteborgTrafikFel(
            f"Värden {host!r} är inte tillåten för Göteborgs trafikmängdskatalog."
        )
    return url.strip()


def _standard_hamta_bytes(url: str, *, timeout_s: float = 15.0) -> bytes:
    ren = kontrollera_trafikmangd_url(url)
    req = urllib.request.Request(
        ren,
        headers={"User-Agent": "Floden/1.0 (Goteborg traffic-catalog client)"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - HTTPS + allowlist
            return bytes(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        raise GoteborgTrafikFel(f"Kunde inte hämta {ren}: {exc}") from exc


def _tolka_valfritt_tal(ra: str) -> float | None:
    ren = ra.replace("\u00a0", "").replace(" ", "").replace(",", ".").strip()
    if not ren or ren in ("-", "–", "MC", "FC"):
        return None
    try:
        val = float(ren)
    except ValueError:
        return None
    if not math.isfinite(val) or val < 0.0:
        return None
    return val


class _TabellTolk(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rader: list[list[str]] = []
        self._i_tr = False
        self._i_cell = False
        self._rad: list[str] = []
        self._cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        t = tag.lower()
        if t == "tr":
            self._i_tr = True
            self._rad = []
        elif t in ("td", "th") and self._i_tr:
            self._i_cell = True
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("td", "th") and self._i_cell:
            self._i_cell = False
            self._rad.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
        elif t == "tr" and self._i_tr:
            self._i_tr = False
            if self._rad:
                self.rader.append(self._rad)

    def handle_data(self, data: str) -> None:
        if self._i_cell:
            self._cell.append(data)


def tolka_goteborg_trafikmangd_html(
    html_text: str,
    *,
    gatunamn: str,
    kalla_url: str | None = None,
) -> tuple[GoteborgTrafikMatning, ...]:
    """Tolkar en 12-kolumners HTML-sida från Göteborgs Stads trafikmängdskatalog."""
    ren_gata = gatunamn.strip()
    if not ren_gata:
        raise GoteborgTrafikFel("gatunamn får inte vara tomt.")
    if not isinstance(html_text, str) or not html_text.strip():
        raise GoteborgTrafikFel(f"Tom HTML-sida för '{ren_gata}'.")

    tolk = _TabellTolk()
    tolk.feed(html_text)

    ut: list[GoteborgTrafikMatning] = []
    aktiv_delstracka: str | None = None

    for r in tolk.rader:
        if len(r) < 12 or not _AR_RE.match(r[1]):
            continue
        if r[0]:
            aktiv_delstracka = r[0]
        if not aktiv_delstracka:
            continue
        amvd = _tolka_valfritt_tal(r[2])
        if amvd is None:
            continue
        rikt_ra = r[3].strip().upper()
        riktning = rikt_ra if rikt_ra in ("MC", "FC") else None
        ut.append(
            GoteborgTrafikMatning(
                gatunamn=ren_gata,
                delstracka=aktiv_delstracka,
                ar=int(r[1]),
                amvd_totalt=amvd,
                riktning=riktning,
                amvd_tung=_tolka_valfritt_tal(r[4]),
                andel_tung_pct=_tolka_valfritt_tal(r[5]),
                maxtim_mot_centrum=_tolka_valfritt_tal(r[6]),
                maxtim_fran_centrum=_tolka_valfritt_tal(r[7]),
                maxtim_summa=_tolka_valfritt_tal(r[8]),
                skyltad_km_h=_tolka_valfritt_tal(r[9]),
                median_km_h=_tolka_valfritt_tal(r[10]),
                p85_km_h=_tolka_valfritt_tal(r[11]),
                kalla_url=kalla_url,
            )
        )

    return tuple(ut)


def senaste_per_delstracka(
    matningar: Sequence[GoteborgTrafikMatning],
) -> tuple[GoteborgTrafikMatning, ...]:
    """Väljer senaste uppmätta år för varje `(gatunamn, delstracka)` i deterministisk ordning."""
    basta: dict[tuple[str, str], GoteborgTrafikMatning] = {}
    for m in matningar:
        nyckel = (m.gatunamn, m.delstracka)
        bef = basta.get(nyckel)
        if bef is None or m.ar > bef.ar:
            basta[nyckel] = m
    return tuple(basta[k] for k in sorted(basta))


def bygg_goteborg_gata_url(
    gatunamn: str,
    *,
    bas_url: str = GOTEBORG_TRAFIKMANGD_BAS_URL,
) -> str:
    """Bygger ISO-8859-1-procentkodad URL för en gata i Göteborgs trafikmängdskatalog."""
    ren = gatunamn.strip()
    if not ren:
        raise GoteborgTrafikFel("gatunamn får inte vara tomt.")
    forsta = urllib.parse.quote(ren[0].upper(), encoding="latin-1")
    fil = urllib.parse.quote(f"{ren}.html", encoding="latin-1")
    url = f"{bas_url.rstrip('/')}/{forsta}/{fil}"
    return kontrollera_trafikmangd_url(url)


def hamta_goteborg_gata_trafikmangder(
    gatunamn: str,
    *,
    bas_url: str = GOTEBORG_TRAFIKMANGD_BAS_URL,
    cache_mapp: str | Path | None = None,
    hamtare: HamtareFn | None = None,
    timeout_s: float = 15.0,
) -> tuple[tuple[GoteborgTrafikMatning, ...], str]:
    """Hämtar och tolkar mätningar för `gatunamn` ur Göteborgs öppna trafikmängdskatalog."""
    url = bygg_goteborg_gata_url(gatunamn, bas_url=bas_url)

    def _adapter(req_url: str, _headers: Mapping[str, str] | None = None) -> bytes:
        kontrollera_trafikmangd_url(req_url)
        if hamtare is not None:
            return hamtare(req_url)
        return _standard_hamta_bytes(req_url, timeout_s=timeout_s)

    if cache_mapp is not None:
        cache = LokalDataCache(cache_mapp, hamtare=_adapter)
        slug = re.sub(r"[^a-z0-9]+", "_", gatunamn.strip().lower()).strip("_") or "gata"
        post = cache.hamta_eller_lasa(
            url,
            filnamn=f"gbg_trafik_{slug}.html",
            kalla=GOTEBORG_TRAFIKMANGD_KALLA,
        )
        ra = post.fil.read_bytes()
        sha = post.sha256
    else:
        ra = _adapter(url)
        sha = sha256_bytes(ra)

    html_text = ra.decode("latin-1", errors="replace")
    return tolka_goteborg_trafikmangd_html(html_text, gatunamn=gatunamn, kalla_url=url), sha


def skriv_kommunal_trafik_csv(
    matningar: Sequence[GoteborgTrafikMatning],
    ut_fil: str | Path,
    *,
    bara_senaste_per_delstracka: bool = True,
) -> Path:
    """Skriver CSV för `trafikverket.las_kommunal_trafik_fil` med tydlig ÅMVD-märkning."""
    urval = senaste_per_delstracka(matningar) if bara_senaste_per_delstracka else tuple(matningar)
    fil = Path(ut_fil)
    fil.parent.mkdir(parents=True, exist_ok=True)
    with fil.open("w", encoding="utf-8", newline="") as fh:
        skrivare = csv.writer(fh)
        skrivare.writerow(
            [
                "gatunamn",
                "delstracka",
                "amvd",
                "matt_typ",
                "riktning",
                "andel_tung_pct",
                "hastighet_km_h",
                "median_km_h",
                "p85_km_h",
                "matar",
                "adt_kalla",
                "osaker",
                "kalla_url",
            ]
        )
        for m in urval:
            rikt_suffix = f", riktning {m.riktning}" if m.riktning else ""
            kalla_rad = f"{GOTEBORG_TRAFIKMANGD_KALLA} ({m.ar}{rikt_suffix})"
            skrivare.writerow(
                [
                    m.gatunamn,
                    m.delstracka,
                    f"{m.amvd_totalt:.0f}",
                    "ÅMVD",
                    m.riktning or "",
                    "" if m.andel_tung_pct is None else f"{m.andel_tung_pct:g}",
                    "" if m.skyltad_km_h is None else f"{m.skyltad_km_h:g}",
                    "" if m.median_km_h is None else f"{m.median_km_h:g}",
                    "" if m.p85_km_h is None else f"{m.p85_km_h:g}",
                    str(m.ar),
                    kalla_rad,
                    "true",
                    m.kalla_url or "",
                ]
            )
    return fil


__all__ = [
    "GOTEBORG_TRAFIKMANGD_BAS_URL",
    "GOTEBORG_TRAFIKMANGD_KALLA",
    "TILLATNA_TRAFIKMANGD_VARDAR",
    "GoteborgTrafikFel",
    "GoteborgTrafikMatning",
    "bygg_goteborg_gata_url",
    "hamta_goteborg_gata_trafikmangder",
    "kontrollera_trafikmangd_url",
    "senaste_per_delstracka",
    "skriv_kommunal_trafik_csv",
    "tolka_goteborg_trafikmangd_html",
]
