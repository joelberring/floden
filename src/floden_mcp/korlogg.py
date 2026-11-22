"""Körloggen: lokal, append-only JSONL med en rad per MCP-anrop (blir senare en rapportbilaga).

Varje rad säger vem som gjorde vad: klient (`clientInfo`), deklarerad modell och roll, verktyg,
hash av indata, körnings-id, dataklass och utfall. Loggen innehåller **inga argumentvärden**
(bara hash och namnen på argumenten), inga svar (bara hash av svaret), inga hemligheter och inga
personuppgifter (ett inloggat konto anges med sitt opaka konto-id, aldrig namn eller e-post).

Raderna är hashkedjade: `hash` = sha256 av föregående rads `hash` och radens innehåll, så att en
ändrad, borttagen eller omkastad rad syns i `verifiera()`. Kedjan skyddar mot misstag och
oavsiktliga ändringar, inte mot någon som kan skriva om hela filen.

Standardplats: `data/lokal/mcp/korlogg.jsonl` (utanför git).
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gemensamt.hash import sha256_bytes

FORMAT = "floden-korlogg/v1"
STANDARD_LOGG = "data/lokal/mcp/korlogg.jsonl"
NOLLHASH = "0" * 64
MAX_FALT = 160

try:  # fcntl finns inte på Windows; då skyddar bara låset inom processen
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


def kanonisk(post: Mapping[str, Any]) -> str:
    return json.dumps(post, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def hash_av(data: Any) -> str:
    """sha256 av kanonisk JSON (samma form som kärnans nycklar)."""
    return sha256_bytes(kanonisk({"v": data}).encode("utf-8"))


def rensa_text(text: object, langd: int = MAX_FALT) -> str | None:
    """Kort text utan kontrolltecken (deklarerad modell och roll kommer från klienten)."""
    if text is None:
        return None
    s = "".join(c for c in str(text) if c.isprintable()).strip()
    return s[:langd] or None


def nu() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Korlogg:
    """Append-only logg. Trådsäker, och säker mellan processer där `fcntl` finns."""

    def __init__(self, sokvag: str | Path) -> None:
        self.sokvag = Path(sokvag)
        self._lock = threading.Lock()

    @contextlib.contextmanager
    def _last(self) -> Iterator[None]:
        with self._lock:
            self.sokvag.parent.mkdir(parents=True, exist_ok=True)
            lasfil = self.sokvag.with_suffix(self.sokvag.suffix + ".lock")
            with lasfil.open("a") as fh:
                if fcntl is not None:
                    fcntl.flock(fh, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(fh, fcntl.LOCK_UN)

    def _sista_hash(self) -> str:
        if not self.sokvag.is_file() or self.sokvag.stat().st_size == 0:
            return NOLLHASH
        with self.sokvag.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            slut = fh.tell()
            lasta = min(slut, 65536)
            fh.seek(slut - lasta)
            rader = fh.read().splitlines()
        for rad in reversed(rader):
            if rad.strip():
                try:
                    return str(json.loads(rad)["hash"])
                except (ValueError, KeyError):
                    break
        raise KorloggFel(f"Körloggen {self.sokvag} har en trasig sista rad; skriver inte vidare.")

    def skriv(self, post: Mapping[str, Any]) -> dict[str, Any]:
        """Lägger till en rad och returnerar den som skrevs (med `foregaende` och `hash`)."""
        with self._last():
            foregaende = self._sista_hash()
            kropp = {"format": FORMAT, **post, "foregaende": foregaende}
            rad = {**kropp, "hash": sha256_bytes((foregaende + kanonisk(kropp)).encode("utf-8"))}
            with self.sokvag.open("a", encoding="utf-8") as fh:
                fh.write(kanonisk(rad) + "\n")
                fh.flush()
        return rad

    def las(
        self,
        *,
        korning_id: str | None = None,
        session: str | None = None,
        verktyg: str | None = None,
        senaste: int | None = None,
    ) -> list[dict[str, Any]]:
        """Läser raderna (äldst först) med valfria filter; `senaste` ger de n sista."""
        if not self.sokvag.is_file():
            return []
        ut: list[dict[str, Any]] = []
        with self.sokvag.open(encoding="utf-8") as fh:
            for rad in fh:
                if not rad.strip():
                    continue
                try:
                    p = json.loads(rad)
                except ValueError:
                    continue
                if session is not None and p.get("session") != session:
                    continue
                if verktyg is not None and p.get("verktyg") != verktyg:
                    continue
                if korning_id is not None and korning_id not in p.get("korning_id", []):
                    continue
                ut.append(p)
        return ut[-senaste:] if senaste else ut

    def verifiera(self) -> tuple[bool, int, int | None]:
        """Kontrollerar hashkedjan. Returnerar (ok, antal rader, radnummer för första felet)."""
        if not self.sokvag.is_file():
            return True, 0, None
        foregaende = NOLLHASH
        antal = 0
        with self.sokvag.open(encoding="utf-8") as fh:
            for nr, rad in enumerate(fh, start=1):
                if not rad.strip():
                    continue
                try:
                    p = json.loads(rad)
                except ValueError:
                    return False, antal, nr
                h = p.pop("hash", None)
                if p.get("foregaende") != foregaende or h != sha256_bytes(
                    (foregaende + kanonisk(p)).encode("utf-8")
                ):
                    return False, antal, nr
                foregaende = str(h)
                antal += 1
        return True, antal, None


class KorloggFel(Exception):
    """Körloggen kan inte skrivas."""
