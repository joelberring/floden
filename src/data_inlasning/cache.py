"""Deterministisk lokal data-cache med SHA-256-kontrollsummor och sidecar-metadata."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gemensamt.hash import sha256_bytes, sha256_fil

Hamtare = Callable[[str, Mapping[str, str] | None], bytes]


class DataInlasningFel(Exception):
    """Fel vid hämtning, cache-verifiering eller tolkning av svenska geodata."""


@dataclass(frozen=True)
class CachePost:
    """En cachad rådatafil med verifierad SHA-256-kontrollsumma och källspårning."""

    nyckel: str
    fil: Path
    sha256: str
    kalla: str
    url: str
    fran_cache: bool


def _standard_hamtare(url: str, headers: Mapping[str, str] | None = None) -> bytes:
    """Hämtar rådata via HTTP(S) med Python stdlib (`urllib.request`)."""
    req_headers = dict(headers) if headers is not None else {}
    req_headers.setdefault("User-Agent", "floden-data-inlasning/0.1")
    req = urllib.request.Request(url, headers=req_headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return bytes(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        raise DataInlasningFel(f"Kunde inte hämta data från '{url}': {exc}") from exc


def _validera_filnamn(filnamn: str) -> str:
    """Validerar relativt filnamn så att cache-filer inte kan lämna cache-katalogen."""
    if not isinstance(filnamn, str) or not filnamn.strip():
        raise DataInlasningFel("Filnamn för cachepost får inte vara tomt.")
    p = Path(filnamn)
    if p.is_absolute() or ".." in p.parts:
        raise DataInlasningFel(f"Ogiltigt filnamn för cachepost: '{filnamn}'.")
    return p.name


def _berakna_cache_nyckel(url: str, headers: Mapping[str, str] | None) -> str:
    """Beräknar deterministisk SHA-256-nyckel från URL och relevanta rubriker."""
    kanonisk_headers = (
        sorted((str(k), str(v)) for k, v in headers.items()) if headers is not None else []
    )
    payload = json.dumps(
        {"url": url, "headers": kanonisk_headers},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(payload)


class LokalDataCache:
    """Innehållsverifierad lokal cache för rådata med `.meta.json`-sidecar."""

    def __init__(
        self,
        rot: str | Path = "data/cache",
        *,
        hamtare: Hamtare | None = None,
    ) -> None:
        self.rot = Path(rot)
        self._hamtare: Hamtare = _standard_hamtare if hamtare is None else hamtare

    def _sokvagar(self, nyckel: str, filnamn: str) -> tuple[Path, Path]:
        rent_namn = _validera_filnamn(filnamn)
        fil = self.rot / f"{nyckel[:16]}_{rent_namn}"
        meta_fil = self.rot / f"{fil.name}.meta.json"
        return fil, meta_fil

    def hamta_eller_lasa(
        self,
        url: str,
        *,
        filnamn: str,
        kalla: str,
        headers: Mapping[str, str] | None = None,
    ) -> CachePost:
        """Returnerar en cachepost från disk om SHA-256 matchar, annars hämtas och sparas den.

        Raises:
            DataInlasningFel: Om indata är ogiltig, hämtningen misslyckas eller
                en befintlig cachefil/metadata inte klarar SHA-256-verifiering.
        """
        if not isinstance(url, str) or not url.strip():
            raise DataInlasningFel("URL får inte vara tom.")
        if not isinstance(kalla, str) or not kalla.strip():
            raise DataInlasningFel("Källa får inte vara tom.")

        nyckel = _berakna_cache_nyckel(url, headers)
        fil, meta_fil = self._sokvagar(nyckel, filnamn)

        if fil.exists() or meta_fil.exists():
            if not fil.exists():
                raise DataInlasningFel(
                    f"Korrupt cache: metadatafil '{meta_fil}' saknar datafil '{fil}'."
                )
            if not meta_fil.exists():
                raise DataInlasningFel(
                    f"Korrupt cache: datafil '{fil}' saknar metadatafil '{meta_fil}'."
                )
            try:
                meta = json.loads(meta_fil.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise DataInlasningFel(
                    f"Korrupt cache-metadata i '{meta_fil}': {exc}"
                ) from exc
            if not isinstance(meta, dict):
                raise DataInlasningFel(f"Korrupt cache-metadata i '{meta_fil}': ej JSON-objekt.")
            for falt in ("sha256", "kalla", "url"):
                if falt not in meta or not isinstance(meta[falt], str) or not meta[falt].strip():
                    raise DataInlasningFel(
                        f"Korrupt cache-metadata i '{meta_fil}': saknar fält '{falt}'."
                    )

            forvantad_sha = meta["sha256"]
            faktisk_sha = sha256_fil(fil)
            if faktisk_sha != forvantad_sha:
                raise DataInlasningFel(
                    f"Korrupt cachefil '{fil}': förväntad SHA-256 {forvantad_sha}, "
                    f"fick {faktisk_sha}."
                )
            if meta["url"] != url:
                raise DataInlasningFel(
                    f"Cache-nyckelkollision eller manipulerad metadata i '{meta_fil}': "
                    f"URL '{meta['url']}' != '{url}'."
                )
            return CachePost(
                nyckel=nyckel,
                fil=fil,
                sha256=faktisk_sha,
                kalla=meta["kalla"],
                url=meta["url"],
                fran_cache=True,
            )

        data = self._hamtare(url, headers)
        if not isinstance(data, bytes):
            raise DataInlasningFel(
                f"Hämtare returnerade {type(data).__name__} i stället för bytes."
            )

        sha256 = sha256_bytes(data)
        self.rot.mkdir(parents=True, exist_ok=True)
        fil.write_bytes(data)
        meta_obj = {
            "nyckel": nyckel,
            "sha256": sha256,
            "kalla": kalla,
            "url": url,
            "storlek_bytes": len(data),
        }
        meta_fil.write_text(
            json.dumps(meta_obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return CachePost(
            nyckel=nyckel,
            fil=fil,
            sha256=sha256,
            kalla=kalla,
            url=url,
            fran_cache=False,
        )

    def hamta_json(
        self,
        url: str,
        *,
        filnamn: str,
        kalla: str,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[dict[str, Any], CachePost]:
        """Hämtar (eller läser från cache) och tolkar ett JSON-objekt."""
        post = self.hamta_eller_lasa(url, filnamn=filnamn, kalla=kalla, headers=headers)
        try:
            innehall = json.loads(post.fil.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataInlasningFel(f"Kunde inte tolka JSON i '{post.fil}': {exc}") from exc
        if not isinstance(innehall, dict):
            raise DataInlasningFel(
                f"Förväntade ett JSON-objekt i '{post.fil}', fick {type(innehall).__name__}."
            )
        return innehall, post
