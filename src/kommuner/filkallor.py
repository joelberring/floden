"""Löser kommunfilens `[filpaket]` till nedladdade paket i cachen och en höjdmodell att räkna på."""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from kommuner.filpaket import FilHamtare, Filpaket, hamta_filpaket, lokalt_filpaket
from kommuner.hojdmodell import Hojdmodell
from kommuner.konfig import KommunKonfig, PaketKalla
from kommuner.wfs import Hamtare, KommunKallaFel, kontrollera_url, standard_hamtare

_LANK = re.compile(r"""href\s*=\s*["']([^"'#?]+\.zip)["']""", re.IGNORECASE)


@dataclass
class Filkallor:
    hojd: Hojdmodell | None = None
    hojd_paket: list[Filpaket] = field(default_factory=list)
    baskarta_paket: list[Filpaket] = field(default_factory=list)


def paket_urlar(p: PaketKalla, tillatna: Sequence[str], hamta_bytes: Hamtare | None = None) -> list[str]:
    """Adresserna i kommunfilen, annars .zip-länkarna på indexsidan (värd och https kontrolleras)."""
    if p.urlar:
        return list(p.urlar)
    kontrollera_url(p.index_url, tillatna)
    try:
        html = (hamta_bytes or standard_hamtare())(p.index_url).decode("utf-8", errors="replace")
    except KommunKallaFel as exc:
        raise KommunKallaFel(
            f"{exc} Indexsidan {p.index_url} är inte verifierad. Ange paketens adresser själv: "
            "--hojdmodell-url <adress> (flera tillåts) eller --hojdmodell-fil <zip> för en nedladdad fil, "
            "eller fyll i [filpaket.hojdmodell] urlar i kommunfilen."
        ) from exc
    ut = []
    for m in _LANK.finditer(html):
        u = urllib.parse.urljoin(p.index_url, m.group(1))
        try:
            kontrollera_url(u, tillatna)
        except KommunKallaFel:
            continue
        if u not in ut:
            ut.append(u)
    if not ut:
        raise KommunKallaFel(
            f"Inga .zip-länkar på {p.index_url}. Ange adresserna med --hojdmodell-url eller i kommunfilen."
        )
    return ut


def _tacker(hoj: Hojdmodell, extent: tuple[float, float, float, float]) -> bool:
    from rasterio.warp import transform_bounds
    from shapely.geometry import box
    from shapely.ops import unary_union

    boxar = [box(*transform_bounds(pl.epsg, 3011, *pl.bounds)) for pl in hoj.tackning(extent)]
    return bool(boxar) and unary_union(boxar).buffer(1.0).covers(box(*extent))


def hamta_baskarta(
    kommun: KommunKonfig, cache: Path, *, hamtare: FilHamtare | None = None,
    hamta_bytes: Hamtare | None = None, skriv: Callable[[str], None] = print,
) -> list[Filpaket]:
    fp = kommun.filpaket
    assert fp is not None and fp.baskarta is not None
    ut = []
    for u in paket_urlar(fp.baskarta, fp.tillatna_varddomaner, hamta_bytes):
        skriv(f"Baskartan: {u}")
        ut.append(hamta_filpaket(u, cache, fp.tillatna_varddomaner, hamtare=hamtare))
        skriv(f"  {ut[-1].namn}: {ut[-1].storlek / 1e6:.0f} MB, sha256 {ut[-1].sha256[:12]}"
              + (" (hämtad nu)" if ut[-1].hamtad_nu else " (ur cachen)"))
    return ut


def hamta_hojdmodell(
    kommun: KommunKonfig, cache: Path, extent_3011: tuple[float, float, float, float], *,
    extra_urlar: Sequence[str] = (), extra_filer: Sequence[Path] = (),
    hamtare: FilHamtare | None = None, hamta_bytes: Hamtare | None = None,
    skriv: Callable[[str], None] = print,
) -> tuple[Hojdmodell, list[Filpaket]]:
    """Hämtar (en gång) de höjdpaket som behövs för `extent_3011`. Slutar när området är täckt."""
    fp = kommun.filpaket
    assert fp is not None and fp.hojdmodell is not None
    hm = fp.hojdmodell
    paket: list[Filpaket] = [lokalt_filpaket(f) for f in extra_filer]
    if extra_urlar:
        urlar = list(extra_urlar)
    else:
        urlar = paket_urlar(hm, fp.tillatna_varddomaner, hamta_bytes)
    hoj: Hojdmodell | None = Hojdmodell([p.fil for p in paket], epsg_antagen=hm.epsg) if paket else None
    for u in urlar:
        if hoj is not None and _tacker(hoj, extent_3011):
            break
        skriv(f"Höjdmodell: {u}")
        p = hamta_filpaket(u, cache, fp.tillatna_varddomaner, hamtare=hamtare)
        skriv(f"  {p.namn}: {p.storlek / 1e6:.0f} MB, sha256 {p.sha256[:12]}" + (" (hämtad nu)" if p.hamtad_nu else " (ur cachen)"))
        paket.append(p)
        try:
            hoj = Hojdmodell([q.fil for q in paket], epsg_antagen=hm.epsg)
        except KommunKallaFel:
            hoj = None
    if hoj is None or not _tacker(hoj, extent_3011):
        raise KommunKallaFel(
            "Höjdmodellens paket täcker inte hela området (E "
            f"{extent_3011[0]:.0f}-{extent_3011[2]:.0f}, N {extent_3011[1]:.0f}-{extent_3011[3]:.0f}, EPSG:3011)."
        )
    return hoj, paket
