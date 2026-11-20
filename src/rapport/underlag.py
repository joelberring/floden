"""Strukturerat rapportunderlag och deterministiskt körnings-id (T08)."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gemensamt.hash import sha256_bytes
from gemensamt.manifest import RunManifest

GILTIGA_RAPPORTTYPER: tuple[str, ...] = (
    "skyfallsutredning",
    "ruttanalys",
    "grundvatten_vattenbalans",
    "alternativjamforelse",
    "planbeskrivning",
)

STANDARD_KALLOR: tuple[str, ...] = ("©Lantmäteriet", "SGU", "SMHI")


class RapportUnderlagFel(Exception):
    """Fel vid skapande, läsning eller validering av rapportunderlag."""


def _validera_icke_tom_strang(varde: Any, faltnamn: str) -> str:
    if not isinstance(varde, str) or not varde.strip():
        raise RapportUnderlagFel(f"Fältet '{faltnamn}' måste vara en icke-tom sträng.")
    return varde


def _ar_giltigt_tal(varde: Any) -> bool:
    return (
        isinstance(varde, int | float)
        and not isinstance(varde, bool)
        and math.isfinite(float(varde))
    )


@dataclass(frozen=True)
class KartReferens:
    """Metadata för en kartbild som ingår i rapporten."""

    kart_id: str
    titel: str
    fil: str
    kalla: str

    def __post_init__(self) -> None:
        _validera_icke_tom_strang(self.kart_id, "KartReferens.kart_id")
        _validera_icke_tom_strang(self.titel, "KartReferens.titel")
        _validera_icke_tom_strang(self.fil, "KartReferens.fil")
        _validera_icke_tom_strang(self.kalla, "KartReferens.kalla")

    def till_dict(self) -> dict[str, str]:
        """Serialiserar kartreferensen till lexikon."""
        return {
            "kart_id": self.kart_id,
            "titel": self.titel,
            "fil": self.fil,
            "kalla": self.kalla,
        }

    @classmethod
    def fran_dict(cls, data: Any, sammanhang: str = "kartor") -> KartReferens:
        """Skapar en `KartReferens` från ett JSON-objekt."""
        if not isinstance(data, dict):
            raise RapportUnderlagFel(f"{sammanhang} måste vara ett JSON-objekt.")
        for falt in ("kart_id", "titel", "fil", "kalla"):
            if falt not in data:
                raise RapportUnderlagFel(f"{sammanhang} saknar obligatoriskt fält '{falt}'.")
        return cls(
            kart_id=data["kart_id"],
            titel=data["titel"],
            fil=data["fil"],
            kalla=data["kalla"],
        )


@dataclass(frozen=True)
class TabellData:
    """Strukturerad tabell i rapportunderlaget."""

    titel: str
    kolumner: list[str]
    rader: list[list[str | float | int]]

    def __post_init__(self) -> None:
        _validera_icke_tom_strang(self.titel, "TabellData.titel")
        if not isinstance(self.kolumner, list) or not self.kolumner:
            raise RapportUnderlagFel("TabellData.kolumner måste vara en icke-tom lista.")
        for idx, kol in enumerate(self.kolumner):
            _validera_icke_tom_strang(kol, f"TabellData.kolumner[{idx}]")
        if not isinstance(self.rader, list):
            raise RapportUnderlagFel("TabellData.rader måste vara en lista.")
        antal_kol = len(self.kolumner)
        for rad_idx, rad in enumerate(self.rader):
            if not isinstance(rad, list) or len(rad) != antal_kol:
                raise RapportUnderlagFel(
                    f"TabellData.rader[{rad_idx}] måste ha exakt {antal_kol} kolumner."
                )
            for kol_idx, cell in enumerate(rad):
                if isinstance(cell, bool) or not isinstance(cell, str | int | float):
                    raise RapportUnderlagFel(
                        f"Ogiltig celltyp i TabellData.rader[{rad_idx}][{kol_idx}]."
                    )
                if isinstance(cell, float) and not math.isfinite(cell):
                    raise RapportUnderlagFel(
                        f"Icke-ändligt tal i TabellData.rader[{rad_idx}][{kol_idx}]."
                    )

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar tabellen till JSON-kompatibelt lexikon."""
        return {
            "titel": self.titel,
            "kolumner": list(self.kolumner),
            "rader": [list(rad) for rad in self.rader],
        }

    @classmethod
    def fran_dict(cls, data: Any, sammanhang: str = "tabeller") -> TabellData:
        """Skapar en `TabellData` från ett JSON-objekt."""
        if not isinstance(data, dict):
            raise RapportUnderlagFel(f"{sammanhang} måste vara ett JSON-objekt.")
        for falt in ("titel", "kolumner", "rader"):
            if falt not in data:
                raise RapportUnderlagFel(f"{sammanhang} saknar obligatoriskt fält '{falt}'.")
        return cls(
            titel=data["titel"],
            kolumner=list(data["kolumner"]),
            rader=[list(rad) for rad in data["rader"]],
        )


@dataclass(frozen=True)
class GransvardeKontroll:
    """Resultat av kontroll mot ett beslutat gränsvärde."""

    namn: str
    uppmatt: float
    grans: float
    enhet: str
    godkand: bool

    def __post_init__(self) -> None:
        _validera_icke_tom_strang(self.namn, "GransvardeKontroll.namn")
        _validera_icke_tom_strang(self.enhet, "GransvardeKontroll.enhet")
        if not _ar_giltigt_tal(self.uppmatt):
            raise RapportUnderlagFel("GransvardeKontroll.uppmatt måste vara ett ändligt tal.")
        if not _ar_giltigt_tal(self.grans):
            raise RapportUnderlagFel("GransvardeKontroll.grans måste vara ett ändligt tal.")
        if not isinstance(self.godkand, bool):
            raise RapportUnderlagFel("GransvardeKontroll.godkand måste vara en bool.")

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar gränsvärdeskontrollen till JSON-kompatibelt lexikon."""
        return {
            "namn": self.namn,
            "uppmatt": float(self.uppmatt),
            "grans": float(self.grans),
            "enhet": self.enhet,
            "godkand": self.godkand,
        }

    @classmethod
    def fran_dict(cls, data: Any, sammanhang: str = "gransvarden") -> GransvardeKontroll:
        """Skapar en `GransvardeKontroll` från ett JSON-objekt."""
        if not isinstance(data, dict):
            raise RapportUnderlagFel(f"{sammanhang} måste vara ett JSON-objekt.")
        for falt in ("namn", "uppmatt", "grans", "enhet", "godkand"):
            if falt not in data:
                raise RapportUnderlagFel(f"{sammanhang} saknar obligatoriskt fält '{falt}'.")
        return cls(
            namn=data["namn"],
            uppmatt=float(data["uppmatt"]),
            grans=float(data["grans"]),
            enhet=data["enhet"],
            godkand=data["godkand"],
        )


def _kanonisk_last_for_id(
    *,
    rapport_typ: str,
    titel: str,
    omrade: str,
    manifest: RunManifest,
    metod: str,
    osakerhet_text: str,
    kalla_angivelser: tuple[str, ...],
    tal: dict[str, float | int],
    tabeller: list[TabellData],
    kartor: list[KartReferens],
    gransvarden: list[GransvardeKontroll],
) -> dict[str, Any]:
    return {
        "rapport_typ": rapport_typ,
        "titel": titel,
        "omrade": omrade,
        "manifest": manifest.till_dict(),
        "metod": metod,
        "osakerhet_text": osakerhet_text,
        "kalla_angivelser": list(kalla_angivelser),
        "tal": dict(tal),
        "tabeller": [tab.till_dict() for tab in tabeller],
        "kartor": [karta.till_dict() for karta in kartor],
        "gransvarden": [gv.till_dict() for gv in gransvarden],
    }


def berakna_korning_id(underlag: RapportUnderlag) -> str:
    """Beräknar deterministisk SHA-256 av manifest, tal, tabeller och metadata."""
    last = _kanonisk_last_for_id(
        rapport_typ=underlag.rapport_typ,
        titel=underlag.titel,
        omrade=underlag.omrade,
        manifest=underlag.manifest,
        metod=underlag.metod,
        osakerhet_text=underlag.osakerhet_text,
        kalla_angivelser=tuple(underlag.kalla_angivelser),
        tal=underlag.tal,
        tabeller=underlag.tabeller,
        kartor=underlag.kartor,
        gransvarden=underlag.gransvarden,
    )
    kanonisk_json = json.dumps(last, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256_bytes(kanonisk_json.encode("utf-8"))


@dataclass
class RapportUnderlag:
    """Samlat, verifierbart underlag för generering och kontroll av rapport."""

    rapport_typ: str
    titel: str
    omrade: str
    manifest: RunManifest
    metod: str
    osakerhet_text: str
    kalla_angivelser: Sequence[str] = STANDARD_KALLOR
    tal: dict[str, float | int] = field(default_factory=dict)
    tabeller: list[TabellData] = field(default_factory=list)
    kartor: list[KartReferens] = field(default_factory=list)
    gransvarden: list[GransvardeKontroll] = field(default_factory=list)
    korning_id: str = ""

    def __post_init__(self) -> None:
        if self.rapport_typ not in GILTIGA_RAPPORTTYPER:
            raise RapportUnderlagFel(
                f"Ogiltig rapport_typ '{self.rapport_typ}'. "
                f"Tillåtna: {', '.join(GILTIGA_RAPPORTTYPER)}."
            )
        _validera_icke_tom_strang(self.titel, "titel")
        _validera_icke_tom_strang(self.omrade, "omrade")
        _validera_icke_tom_strang(self.metod, "metod")
        _validera_icke_tom_strang(self.osakerhet_text, "osakerhet_text")

        if not isinstance(self.manifest, RunManifest):
            raise RapportUnderlagFel("Fältet 'manifest' måste vara ett RunManifest.")
        self.manifest.validera()

        if isinstance(self.kalla_angivelser, str) or not isinstance(
            self.kalla_angivelser, Sequence
        ):
            raise RapportUnderlagFel("kalla_angivelser måste vara en sekvens av strängar.")
        kallor_tuple = tuple(self.kalla_angivelser)
        if not kallor_tuple:
            raise RapportUnderlagFel("kalla_angivelser får inte vara tom.")
        for idx, kalla in enumerate(kallor_tuple):
            _validera_icke_tom_strang(kalla, f"kalla_angivelser[{idx}]")
        self.kalla_angivelser = kallor_tuple

        if not isinstance(self.tal, dict):
            raise RapportUnderlagFel("Fältet 'tal' måste vara ett lexikon.")
        for nyckel, varde in self.tal.items():
            _validera_icke_tom_strang(nyckel, "tal-nyckel")
            if not _ar_giltigt_tal(varde):
                raise RapportUnderlagFel(f"Ogiltigt talvärde för '{nyckel}': {varde!r}")

        if not isinstance(self.tabeller, list) or not all(
            isinstance(t, TabellData) for t in self.tabeller
        ):
            raise RapportUnderlagFel("Fältet 'tabeller' måste vara en lista av TabellData.")
        if not isinstance(self.kartor, list) or not all(
            isinstance(k, KartReferens) for k in self.kartor
        ):
            raise RapportUnderlagFel("Fältet 'kartor' måste vara en lista av KartReferens.")
        if not isinstance(self.gransvarden, list) or not all(
            isinstance(g, GransvardeKontroll) for g in self.gransvarden
        ):
            raise RapportUnderlagFel(
                "Fältet 'gransvarden' måste vara en lista av GransvardeKontroll."
            )

        if not isinstance(self.korning_id, str):
            raise RapportUnderlagFel("Fältet 'korning_id' måste vara en sträng.")
        if not self.korning_id.strip():
            self.korning_id = berakna_korning_id(self)

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar hela rapportunderlaget till JSON-kompatibelt lexikon."""
        data = _kanonisk_last_for_id(
            rapport_typ=self.rapport_typ,
            titel=self.titel,
            omrade=self.omrade,
            manifest=self.manifest,
            metod=self.metod,
            osakerhet_text=self.osakerhet_text,
            kalla_angivelser=tuple(self.kalla_angivelser),
            tal=self.tal,
            tabeller=self.tabeller,
            kartor=self.kartor,
            gransvarden=self.gransvarden,
        )
        data["korning_id"] = self.korning_id
        return data

    @classmethod
    def fran_dict(cls, data: Any) -> RapportUnderlag:
        """Läser och validerar ett `RapportUnderlag` från deserialiserad JSON."""
        if not isinstance(data, dict):
            raise RapportUnderlagFel("Rapportunderlaget måste vara ett JSON-objekt.")
        obligatoriska = (
            "rapport_typ",
            "titel",
            "omrade",
            "manifest",
            "metod",
            "osakerhet_text",
            "kalla_angivelser",
            "tal",
            "tabeller",
            "kartor",
            "gransvarden",
            "korning_id",
        )
        saknade = [falt for falt in obligatoriska if falt not in data]
        if saknade:
            raise RapportUnderlagFel(
                f"Rapportunderlaget saknar obligatoriska fält: {', '.join(saknade)}"
            )

        raw_tabeller = data["tabeller"]
        raw_kartor = data["kartor"]
        raw_grans = data["gransvarden"]
        if not isinstance(raw_tabeller, list):
            raise RapportUnderlagFel("Fältet 'tabeller' måste vara en lista.")
        if not isinstance(raw_kartor, list):
            raise RapportUnderlagFel("Fältet 'kartor' måste vara en lista.")
        if not isinstance(raw_grans, list):
            raise RapportUnderlagFel("Fältet 'gransvarden' måste vara en lista.")

        return cls(
            rapport_typ=data["rapport_typ"],
            titel=data["titel"],
            omrade=data["omrade"],
            manifest=RunManifest.fran_dict(data["manifest"]),
            metod=data["metod"],
            osakerhet_text=data["osakerhet_text"],
            kalla_angivelser=tuple(data["kalla_angivelser"]),
            tal=dict(data["tal"]),
            tabeller=[
                TabellData.fran_dict(t, sammanhang=f"tabeller[{i}]")
                for i, t in enumerate(raw_tabeller)
            ],
            kartor=[
                KartReferens.fran_dict(k, sammanhang=f"kartor[{i}]")
                for i, k in enumerate(raw_kartor)
            ],
            gransvarden=[
                GransvardeKontroll.fran_dict(g, sammanhang=f"gransvarden[{i}]")
                for i, g in enumerate(raw_grans)
            ],
            korning_id=data["korning_id"],
        )


def skriv_underlag_json(underlag: RapportUnderlag, sokvag: str | Path) -> Path:
    """Skriver `RapportUnderlag` som deterministisk JSON med `sort_keys=True`."""
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    innehall = json.dumps(underlag.till_dict(), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    mal.write_text(innehall, encoding="utf-8")
    return mal


def las_underlag_json(sokvag: str | Path) -> RapportUnderlag:
    """Läser och validerar ett `RapportUnderlag` från JSON-fil."""
    kalla = Path(sokvag)
    try:
        text = kalla.read_text(encoding="utf-8")
    except OSError as exc:
        raise RapportUnderlagFel(f"Kunde inte läsa underlagsfilen {kalla}: {exc}") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RapportUnderlagFel(f"Ogiltig JSON i underlagsfilen {kalla}: {exc}") from exc

    return RapportUnderlag.fran_dict(data)
