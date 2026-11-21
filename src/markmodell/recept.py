"""Recept och kanonisk hashing för markmodellrutor (kontrakt K1/v1)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gemensamt.hash import sha256_bytes

KONTRAKT_K1 = "K1/v1"
KONTRAKT_K1_V2 = "K1/v2"
GILTIGA_KONTRAKT_K1: frozenset[str] = frozenset({KONTRAKT_K1, KONTRAKT_K1_V2})


class ReceptFel(Exception):
    """Fel vid skapande, validering eller läsning av `recept.json`."""


@dataclass(frozen=True)
class KallPost:
    """En indatakälla med deterministisk sha256-kontrollsumma."""

    roll: str
    kalla: str
    sha256: str

    def __post_init__(self) -> None:
        if not self.roll.strip():
            raise ReceptFel("KallPost.roll får inte vara tom.")
        if not self.kalla.strip():
            raise ReceptFel("KallPost.kalla får inte vara tom.")
        if not self.sha256.strip():
            raise ReceptFel("KallPost.sha256 får inte vara tom.")

    def till_dict(self) -> dict[str, str]:
        return {"roll": self.roll, "kalla": self.kalla, "sha256": self.sha256}

    @classmethod
    def fran_dict(cls, data: Any) -> KallPost:
        if not isinstance(data, dict):
            raise ReceptFel("Källpost i recept måste vara ett objekt.")
        for falt in ("roll", "kalla", "sha256"):
            if falt not in data or not isinstance(data[falt], str):
                raise ReceptFel(f"Källpost saknar strängfält '{falt}'.")
        return cls(roll=data["roll"], kalla=data["kalla"], sha256=data["sha256"])


@dataclass(frozen=True)
class MarkmodellRecept:
    """Receptet (`recept.json`) vars sha256 utgör markmodell-rutans identitet."""

    ruta_namn: str
    yttre_bbox: tuple[float, float, float, float]
    upplosning_m: float
    kodversion: str
    kallor: tuple[KallPost, ...] = field(default_factory=tuple)
    installningar: dict[str, Any] = field(default_factory=dict)
    kontrakt: str = KONTRAKT_K1

    def __post_init__(self) -> None:
        if self.kontrakt not in GILTIGA_KONTRAKT_K1:
            raise ReceptFel(
                f"Ogiltigt receptkontrakt '{self.kontrakt}', förväntade "
                f"{' eller '.join(sorted(GILTIGA_KONTRAKT_K1))}."
            )
        if not self.ruta_namn.strip():
            raise ReceptFel("ruta_namn får inte vara tom.")
        if not self.kodversion.strip():
            raise ReceptFel("kodversion får inte vara tom.")

    def till_dict(self) -> dict[str, Any]:
        """Returnerar receptet som JSON-serialiserbart lexikon i stabil nyckelordning."""
        sorterade_kallor = sorted(
            (k.till_dict() for k in self.kallor),
            key=lambda p: (p["roll"], p["kalla"], p["sha256"]),
        )
        return {
            "kontrakt": self.kontrakt,
            "ruta": self.ruta_namn,
            "epsg": 3011,
            "hojdsystem": "RH 2000",
            "yttre_bbox": list(self.yttre_bbox),
            "upplosning_m": self.upplosning_m,
            "kodversion": self.kodversion,
            "kallor": sorterade_kallor,
            "installningar": self.installningar,
        }

    def kanonisk_json(self) -> str:
        """Kanonisk JSON-representation för exakt deterministisk hashing."""
        return json.dumps(
            self.till_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )

    def recept_sha256(self) -> str:
        """Rutans unika identitet: SHA-256 av receptets kanoniska innehåll."""
        return sha256_bytes(self.kanonisk_json().encode("utf-8"))

    @classmethod
    def fran_dict(cls, data: Any) -> MarkmodellRecept:
        if not isinstance(data, dict):
            raise ReceptFel("Recept måste vara ett JSON-objekt.")
        for falt in (
            "kontrakt",
            "ruta",
            "yttre_bbox",
            "upplosning_m",
            "kodversion",
            "kallor",
            "installningar",
        ):
            if falt not in data:
                raise ReceptFel(f"Recept saknar obligatoriskt fält '{falt}'.")
        bbox_raw = data["yttre_bbox"]
        if not isinstance(bbox_raw, list) or len(bbox_raw) != 4:
            raise ReceptFel("yttre_bbox i recept måste vara en lista med 4 tal.")
        bbox = (
            float(bbox_raw[0]),
            float(bbox_raw[1]),
            float(bbox_raw[2]),
            float(bbox_raw[3]),
        )
        kallor = tuple(KallPost.fran_dict(k) for k in data["kallor"])
        if not isinstance(data["installningar"], dict):
            raise ReceptFel("installningar i recept måste vara ett objekt.")
        return cls(
            kontrakt=str(data["kontrakt"]),
            ruta_namn=str(data["ruta"]),
            yttre_bbox=bbox,
            upplosning_m=float(data["upplosning_m"]),
            kodversion=str(data["kodversion"]),
            kallor=kallor,
            installningar=dict(data["installningar"]),
        )


def skriv_recept(recept: MarkmodellRecept, sokvag: str | Path) -> Path:
    """Skriver `recept.json` till `sokvag` (med deterministisk formatering)."""
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    data = recept.till_dict()
    data["recept_sha256"] = recept.recept_sha256()
    text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    mal.write_text(text, encoding="utf-8")
    return mal


def las_recept(sokvag: str | Path) -> MarkmodellRecept:
    """Läser och validerar `recept.json`."""
    kalla = Path(sokvag)
    try:
        data = json.loads(kalla.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReceptFel(f"Kunde inte läsa recept {kalla}: {exc}") from exc
    return MarkmodellRecept.fran_dict(data)
