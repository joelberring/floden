"""Grundmodell-rättningar och experimentläge för projektåtgärder i 0,25 m-raster."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from gemensamt.hash import sha256_bytes
from markmodell.hinder_oppningar import BYGGNAD as MARKSLAG_BYGGNAD
from markmodell.hinder_oppningar import GILTIGA_MARKSLAG, HARDGJORT
from testsvit.k1_io import GeoTransformInfo

GILTIGA_PASSAGETYPER: frozenset[str] = frozenset({"bro", "trumma", "port"})
_KANT_TOL_M = 1e-9


class ExperimentFel(Exception):
    """Fel vid validering, serialisering eller tillämpning av rättning/experiment."""


def _validera_id(varde: Any, faltnamn: str) -> str:
    if not isinstance(varde, str) or not varde.strip():
        raise ExperimentFel(f"Fältet '{faltnamn}' måste vara en icke-tom sträng.")
    return varde.strip()


def _validera_bool(varde: Any, faltnamn: str) -> bool:
    if not isinstance(varde, bool):
        raise ExperimentFel(f"Fältet '{faltnamn}' måste vara bool, fick {type(varde).__name__}.")
    return varde


def _validera_andlig(
    varde: Any,
    faltnamn: str,
    *,
    minst: float | None = None,
    strikt_storre: float | None = None,
) -> float:
    if isinstance(varde, bool) or not isinstance(varde, int | float):
        raise ExperimentFel(f"Fältet '{faltnamn}' måste vara ett tal, fick {varde!r}.")
    tal = float(varde)
    if not math.isfinite(tal):
        raise ExperimentFel(f"Fältet '{faltnamn}' måste vara ändligt, fick {varde!r}.")
    if minst is not None and tal < minst:
        raise ExperimentFel(f"Fältet '{faltnamn}' måste vara >= {minst}, fick {tal}.")
    if strikt_storre is not None and tal <= strikt_storre:
        raise ExperimentFel(f"Fältet '{faltnamn}' måste vara > {strikt_storre}, fick {tal}.")
    return tal


def _validera_punkt(punkt: Any, sammanhang: str) -> tuple[float, float]:
    if not isinstance(punkt, Sequence) or isinstance(punkt, str | bytes) or len(punkt) != 2:
        raise ExperimentFel(f"{sammanhang} måste ha exakt 2 koordinater (E, N), fick {punkt!r}.")
    e = _validera_andlig(punkt[0], f"{sammanhang}.E")
    n = _validera_andlig(punkt[1], f"{sammanhang}.N")
    return (e, n)


def _validera_punkter(
    punkter: Any,
    minst_antal: int,
    sammanhang: str,
) -> tuple[tuple[float, float], ...]:
    if not isinstance(punkter, Sequence) or isinstance(punkter, str | bytes):
        raise ExperimentFel(f"{sammanhang} måste vara en sekvens av punkter.")
    if len(punkter) < minst_antal:
        raise ExperimentFel(
            f"{sammanhang} måste ha minst {minst_antal} punkter, fick {len(punkter)}."
        )
    return tuple(_validera_punkt(p, f"{sammanhang}[{idx}]") for idx, p in enumerate(punkter))


def _validera_markslag_kod(kod: Any, sammanhang: str) -> int:
    if isinstance(kod, bool) or not isinstance(kod, int):
        raise ExperimentFel(f"{sammanhang} måste vara ett heltal 0..9, fick {kod!r}.")
    if kod not in GILTIGA_MARKSLAG:
        raise ExperimentFel(f"Ogiltig markslagskod i {sammanhang}: {kod} (måste vara 0..9).")
    return int(kod)


@dataclass(frozen=True)
class OppnaPassage:
    """Grundmodell-rättning som öppnar en falsk damm (bro, trumma eller port)."""

    passage_id: str
    typ: str
    linje: tuple[tuple[float, float], ...]
    bredd_m: float = 4.0
    botten_rh2000: float | None = None
    aktiv: bool = True
    kommentar: str = ""

    def __post_init__(self) -> None:
        pid = _validera_id(self.passage_id, "passage_id")
        if not isinstance(self.typ, str):
            raise ExperimentFel(f"OppnaPassage.typ måste vara en sträng, fick {self.typ!r}.")
        norm_typ = self.typ.strip().lower()
        if norm_typ not in GILTIGA_PASSAGETYPER:
            tillatna = ", ".join(sorted(GILTIGA_PASSAGETYPER))
            raise ExperimentFel(
                f"Ogiltig passagetyp '{self.typ}' för '{pid}'. Giltiga: {tillatna}."
            )
        linje = _validera_punkter(self.linje, 2, f"OppnaPassage '{pid}'.linje")
        bredd = _validera_andlig(self.bredd_m, "bredd_m", strikt_storre=0.0)
        botten = (
            _validera_andlig(self.botten_rh2000, "botten_rh2000")
            if self.botten_rh2000 is not None
            else None
        )
        aktiv = _validera_bool(self.aktiv, "aktiv")
        if not isinstance(self.kommentar, str):
            raise ExperimentFel("OppnaPassage.kommentar måste vara en sträng.")

        object.__setattr__(self, "passage_id", pid)
        object.__setattr__(self, "typ", norm_typ)
        object.__setattr__(self, "linje", linje)
        object.__setattr__(self, "bredd_m", bredd)
        object.__setattr__(self, "botten_rh2000", botten)
        object.__setattr__(self, "aktiv", aktiv)

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar passagen till JSON-kompatibelt lexikon."""
        return {
            "passage_id": self.passage_id,
            "typ": self.typ,
            "linje": [[p[0], p[1]] for p in self.linje],
            "bredd_m": self.bredd_m,
            "botten_rh2000": self.botten_rh2000,
            "aktiv": self.aktiv,
            "kommentar": self.kommentar,
        }

    @classmethod
    def fran_dict(cls, data: Any) -> OppnaPassage:
        """Läser och validerar en `OppnaPassage` från JSON-objekt."""
        if not isinstance(data, Mapping):
            raise ExperimentFel("OppnaPassage måste vara ett JSON-objekt.")
        for falt in ("passage_id", "typ", "linje"):
            if falt not in data:
                raise ExperimentFel(f"OppnaPassage saknar obligatoriskt fält '{falt}'.")
        return cls(
            passage_id=data["passage_id"],
            typ=data["typ"],
            linje=_validera_punkter(data["linje"], 2, "OppnaPassage.linje"),
            bredd_m=data.get("bredd_m", 4.0),
            botten_rh2000=data.get("botten_rh2000"),
            aktiv=data.get("aktiv", True),
            kommentar=data.get("kommentar", ""),
        )


@dataclass(frozen=True)
class HojdRattning:
    """Grundmodell-rättning för felhöjder, spikar eller felklassade byggnader."""

    rattning_id: str
    polygon: tuple[tuple[float, float], ...]
    z_rh2000: float | None = None
    delta_z_m: float | None = None
    utjamna_fran_rand: bool = False
    ta_bort_byggnad: bool = False
    aktiv: bool = True
    kommentar: str = ""

    def __post_init__(self) -> None:
        rid = _validera_id(self.rattning_id, "rattning_id")
        poly = _validera_punkter(self.polygon, 3, f"HojdRattning '{rid}'.polygon")
        z_fast = (
            _validera_andlig(self.z_rh2000, "z_rh2000")
            if self.z_rh2000 is not None
            else None
        )
        dz = (
            _validera_andlig(self.delta_z_m, "delta_z_m")
            if self.delta_z_m is not None
            else None
        )
        utjamna = _validera_bool(self.utjamna_fran_rand, "utjamna_fran_rand")
        ta_bort = _validera_bool(self.ta_bort_byggnad, "ta_bort_byggnad")
        aktiv = _validera_bool(self.aktiv, "aktiv")
        if not isinstance(self.kommentar, str):
            raise ExperimentFel("HojdRattning.kommentar måste vara en sträng.")

        antal_lagen = int(z_fast is not None) + int(dz is not None) + int(utjamna)
        if antal_lagen > 1:
            raise ExperimentFel(
                f"HojdRattning '{rid}' kan bara använda ett höjdläge åt gången "
                "(z_rh2000, delta_z_m eller utjamna_fran_rand)."
            )
        if antal_lagen == 0 and not ta_bort:
            raise ExperimentFel(
                f"HojdRattning '{rid}' måste ange z_rh2000, delta_z_m, "
                "utjamna_fran_rand=True eller ta_bort_byggnad=True."
            )

        object.__setattr__(self, "rattning_id", rid)
        object.__setattr__(self, "polygon", poly)
        object.__setattr__(self, "z_rh2000", z_fast)
        object.__setattr__(self, "delta_z_m", dz)
        object.__setattr__(self, "utjamna_fran_rand", utjamna)
        object.__setattr__(self, "ta_bort_byggnad", ta_bort)
        object.__setattr__(self, "aktiv", aktiv)

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar höjdrättningen till JSON-kompatibelt lexikon."""
        return {
            "rattning_id": self.rattning_id,
            "polygon": [[p[0], p[1]] for p in self.polygon],
            "z_rh2000": self.z_rh2000,
            "delta_z_m": self.delta_z_m,
            "utjamna_fran_rand": self.utjamna_fran_rand,
            "ta_bort_byggnad": self.ta_bort_byggnad,
            "aktiv": self.aktiv,
            "kommentar": self.kommentar,
        }

    @classmethod
    def fran_dict(cls, data: Any) -> HojdRattning:
        """Läser och validerar en `HojdRattning` från JSON-objekt."""
        if not isinstance(data, Mapping):
            raise ExperimentFel("HojdRattning måste vara ett JSON-objekt.")
        for falt in ("rattning_id", "polygon"):
            if falt not in data:
                raise ExperimentFel(f"HojdRattning saknar obligatoriskt fält '{falt}'.")
        return cls(
            rattning_id=data["rattning_id"],
            polygon=_validera_punkter(data["polygon"], 3, "HojdRattning.polygon"),
            z_rh2000=data.get("z_rh2000"),
            delta_z_m=data.get("delta_z_m"),
            utjamna_fran_rand=data.get("utjamna_fran_rand", False),
            ta_bort_byggnad=data.get("ta_bort_byggnad", False),
            aktiv=data.get("aktiv", True),
            kommentar=data.get("kommentar", ""),
        )


@dataclass(frozen=True)
class MarkslagRattning:
    """Grundmodell-rättning av markslagskod (0..9) inom en polygon."""

    rattning_id: str
    polygon: tuple[tuple[float, float], ...]
    markslag_kod: int
    aktiv: bool = True
    kommentar: str = ""

    def __post_init__(self) -> None:
        rid = _validera_id(self.rattning_id, "rattning_id")
        poly = _validera_punkter(self.polygon, 3, f"MarkslagRattning '{rid}'.polygon")
        kod = _validera_markslag_kod(self.markslag_kod, f"MarkslagRattning '{rid}'.markslag_kod")
        aktiv = _validera_bool(self.aktiv, "aktiv")
        if not isinstance(self.kommentar, str):
            raise ExperimentFel("MarkslagRattning.kommentar måste vara en sträng.")

        object.__setattr__(self, "rattning_id", rid)
        object.__setattr__(self, "polygon", poly)
        object.__setattr__(self, "markslag_kod", kod)
        object.__setattr__(self, "aktiv", aktiv)

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar markslagsrättningen till JSON-kompatibelt lexikon."""
        return {
            "rattning_id": self.rattning_id,
            "polygon": [[p[0], p[1]] for p in self.polygon],
            "markslag_kod": self.markslag_kod,
            "aktiv": self.aktiv,
            "kommentar": self.kommentar,
        }

    @classmethod
    def fran_dict(cls, data: Any) -> MarkslagRattning:
        """Läser och validerar en `MarkslagRattning` från JSON-objekt."""
        if not isinstance(data, Mapping):
            raise ExperimentFel("MarkslagRattning måste vara ett JSON-objekt.")
        for falt in ("rattning_id", "polygon", "markslag_kod"):
            if falt not in data:
                raise ExperimentFel(f"MarkslagRattning saknar obligatoriskt fält '{falt}'.")
        return cls(
            rattning_id=data["rattning_id"],
            polygon=_validera_punkter(data["polygon"], 3, "MarkslagRattning.polygon"),
            markslag_kod=data["markslag_kod"],
            aktiv=data.get("aktiv", True),
            kommentar=data.get("kommentar", ""),
        )


@dataclass(frozen=True)
class GrundmodellRattning:
    """Samlade rättningar av grundmodellen (påverkar både före och efter)."""

    rattning_id: str = "grundmodell-rattning-1"
    passager: tuple[OppnaPassage, ...] = field(default_factory=tuple)
    hojd_rattningar: tuple[HojdRattning, ...] = field(default_factory=tuple)
    markslag_rattningar: tuple[MarkslagRattning, ...] = field(default_factory=tuple)
    beskrivning: str = ""

    def __post_init__(self) -> None:
        rid = _validera_id(self.rattning_id, "rattning_id")
        if not isinstance(self.beskrivning, str):
            raise ExperimentFel("GrundmodellRattning.beskrivning måste vara en sträng.")
        object.__setattr__(self, "rattning_id", rid)
        object.__setattr__(self, "passager", tuple(self.passager))
        object.__setattr__(self, "hojd_rattningar", tuple(self.hojd_rattningar))
        object.__setattr__(self, "markslag_rattningar", tuple(self.markslag_rattningar))

    def har_aktiva(self) -> bool:
        """Returnerar True om minst en rättning i samlingen är aktiv."""
        return (
            any(p.aktiv for p in self.passager)
            or any(h.aktiv for h in self.hojd_rattningar)
            or any(m.aktiv for m in self.markslag_rattningar)
        )

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar grundmodellrättningen till JSON-kompatibelt lexikon."""
        return {
            "rattning_id": self.rattning_id,
            "passager": [p.till_dict() for p in self.passager],
            "hojd_rattningar": [h.till_dict() for h in self.hojd_rattningar],
            "markslag_rattningar": [m.till_dict() for m in self.markslag_rattningar],
            "beskrivning": self.beskrivning,
        }

    def sha256(self) -> str:
        """Deterministisk SHA-256 över kanonisk JSON-representation."""
        kanonisk = json.dumps(
            self.till_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        return sha256_bytes(kanonisk.encode("utf-8"))

    @classmethod
    def fran_dict(cls, data: Any) -> GrundmodellRattning:
        """Läser och validerar en `GrundmodellRattning` från JSON-objekt."""
        if not isinstance(data, Mapping):
            raise ExperimentFel("GrundmodellRattning måste vara ett JSON-objekt.")
        raw_passager = data.get("passager", ())
        raw_hojd = data.get("hojd_rattningar", ())
        raw_markslag = data.get("markslag_rattningar", ())
        if (
            not isinstance(raw_passager, Sequence)
            or isinstance(raw_passager, str | bytes)
            or not isinstance(raw_hojd, Sequence)
            or isinstance(raw_hojd, str | bytes)
            or not isinstance(raw_markslag, Sequence)
            or isinstance(raw_markslag, str | bytes)
        ):
            raise ExperimentFel("GrundmodellRattning-listor måste vara JSON-arrayer.")

        return cls(
            rattning_id=data.get("rattning_id", "grundmodell-rattning-1"),
            passager=tuple(OppnaPassage.fran_dict(p) for p in raw_passager),
            hojd_rattningar=tuple(HojdRattning.fran_dict(h) for h in raw_hojd),
            markslag_rattningar=tuple(MarkslagRattning.fran_dict(m) for m in raw_markslag),
            beskrivning=str(data.get("beskrivning", "")),
        )


@dataclass(frozen=True)
class RattningResultat:
    """Resultat efter tillämpning av `GrundmodellRattning` på basrastrer."""

    mark_z: NDArray[np.float32]
    osakerhet_z: NDArray[np.float32]
    markslag: NDArray[np.uint8]
    antal_andrade_celler: int
    antal_aktiva_rattningar: int
    rattning_sha256: str


@dataclass(frozen=True)
class DikeAtgard:
    """Experimentåtgärd: trapetsformat dike (sänker marken längs linje)."""

    atgard_id: str
    linje: tuple[tuple[float, float], ...]
    botten_bredd_m: float = 1.0
    slantslutning: float = 2.0
    djup_m: float | None = 0.40
    botten_start_rh2000: float | None = None
    botten_slut_rh2000: float | None = None
    aktiv: bool = True

    def __post_init__(self) -> None:
        aid = _validera_id(self.atgard_id, "atgard_id")
        linje = _validera_punkter(self.linje, 2, f"DikeAtgard '{aid}'.linje")
        botten_bredd = _validera_andlig(self.botten_bredd_m, "botten_bredd_m", minst=0.0)
        slant = _validera_andlig(self.slantslutning, "slantslutning", strikt_storre=0.0)
        djup = (
            _validera_andlig(self.djup_m, "djup_m", minst=0.0)
            if self.djup_m is not None
            else None
        )
        b_start = (
            _validera_andlig(self.botten_start_rh2000, "botten_start_rh2000")
            if self.botten_start_rh2000 is not None
            else None
        )
        b_slut = (
            _validera_andlig(self.botten_slut_rh2000, "botten_slut_rh2000")
            if self.botten_slut_rh2000 is not None
            else None
        )
        aktiv = _validera_bool(self.aktiv, "aktiv")

        if (b_start is None) != (b_slut is None):
            raise ExperimentFel(
                f"DikeAtgard '{aid}': både botten_start_rh2000 och botten_slut_rh2000 "
                "måste anges tillsammans."
            )
        if b_start is None and djup is None:
            raise ExperimentFel(
                f"DikeAtgard '{aid}': måste ange djup_m eller "
                "(botten_start_rh2000, botten_slut_rh2000)."
            )

        object.__setattr__(self, "atgard_id", aid)
        object.__setattr__(self, "linje", linje)
        object.__setattr__(self, "botten_bredd_m", botten_bredd)
        object.__setattr__(self, "slantslutning", slant)
        object.__setattr__(self, "djup_m", djup)
        object.__setattr__(self, "botten_start_rh2000", b_start)
        object.__setattr__(self, "botten_slut_rh2000", b_slut)
        object.__setattr__(self, "aktiv", aktiv)

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar dikeåtgärden till JSON-kompatibelt lexikon."""
        return {
            "atgard_id": self.atgard_id,
            "linje": [[p[0], p[1]] for p in self.linje],
            "botten_bredd_m": self.botten_bredd_m,
            "slantslutning": self.slantslutning,
            "djup_m": self.djup_m,
            "botten_start_rh2000": self.botten_start_rh2000,
            "botten_slut_rh2000": self.botten_slut_rh2000,
            "aktiv": self.aktiv,
        }

    @classmethod
    def fran_dict(cls, data: Any) -> DikeAtgard:
        """Läser och validerar en `DikeAtgard` från JSON-objekt."""
        if not isinstance(data, Mapping):
            raise ExperimentFel("DikeAtgard måste vara ett JSON-objekt.")
        for falt in ("atgard_id", "linje"):
            if falt not in data:
                raise ExperimentFel(f"DikeAtgard saknar obligatoriskt fält '{falt}'.")
        return cls(
            atgard_id=data["atgard_id"],
            linje=_validera_punkter(data["linje"], 2, "DikeAtgard.linje"),
            botten_bredd_m=data.get("botten_bredd_m", 1.0),
            slantslutning=data.get("slantslutning", 2.0),
            djup_m=data.get("djup_m", 0.40),
            botten_start_rh2000=data.get("botten_start_rh2000"),
            botten_slut_rh2000=data.get("botten_slut_rh2000"),
            aktiv=data.get("aktiv", True),
        )


@dataclass(frozen=True)
class VallAtgard:
    """Experimentåtgärd: trapetsformad vall eller mur (höjer marken längs linje)."""

    atgard_id: str
    linje: tuple[tuple[float, float], ...]
    kron_bredd_m: float = 1.0
    slantslutning: float = 1.5
    hojd_over_mark_m: float | None = 0.50
    kron_rh2000: float | None = None
    aktiv: bool = True

    def __post_init__(self) -> None:
        aid = _validera_id(self.atgard_id, "atgard_id")
        linje = _validera_punkter(self.linje, 2, f"VallAtgard '{aid}'.linje")
        kron_bredd = _validera_andlig(self.kron_bredd_m, "kron_bredd_m", minst=0.0)
        slant = _validera_andlig(self.slantslutning, "slantslutning", strikt_storre=0.0)
        hojd_over = (
            _validera_andlig(self.hojd_over_mark_m, "hojd_over_mark_m", minst=0.0)
            if self.hojd_over_mark_m is not None
            else None
        )
        kron = (
            _validera_andlig(self.kron_rh2000, "kron_rh2000")
            if self.kron_rh2000 is not None
            else None
        )
        aktiv = _validera_bool(self.aktiv, "aktiv")

        if kron is None and hojd_over is None:
            raise ExperimentFel(
                f"VallAtgard '{aid}': måste ange hojd_over_mark_m eller kron_rh2000."
            )

        object.__setattr__(self, "atgard_id", aid)
        object.__setattr__(self, "linje", linje)
        object.__setattr__(self, "kron_bredd_m", kron_bredd)
        object.__setattr__(self, "slantslutning", slant)
        object.__setattr__(self, "hojd_over_mark_m", hojd_over)
        object.__setattr__(self, "kron_rh2000", kron)
        object.__setattr__(self, "aktiv", aktiv)

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar vallåtgärden till JSON-kompatibelt lexikon."""
        return {
            "atgard_id": self.atgard_id,
            "linje": [[p[0], p[1]] for p in self.linje],
            "kron_bredd_m": self.kron_bredd_m,
            "slantslutning": self.slantslutning,
            "hojd_over_mark_m": self.hojd_over_mark_m,
            "kron_rh2000": self.kron_rh2000,
            "aktiv": self.aktiv,
        }

    @classmethod
    def fran_dict(cls, data: Any) -> VallAtgard:
        """Läser och validerar en `VallAtgard` från JSON-objekt."""
        if not isinstance(data, Mapping):
            raise ExperimentFel("VallAtgard måste vara ett JSON-objekt.")
        for falt in ("atgard_id", "linje"):
            if falt not in data:
                raise ExperimentFel(f"VallAtgard saknar obligatoriskt fält '{falt}'.")
        return cls(
            atgard_id=data["atgard_id"],
            linje=_validera_punkter(data["linje"], 2, "VallAtgard.linje"),
            kron_bredd_m=data.get("kron_bredd_m", 1.0),
            slantslutning=data.get("slantslutning", 1.5),
            hojd_over_mark_m=data.get("hojd_over_mark_m", 0.50),
            kron_rh2000=data.get("kron_rh2000"),
            aktiv=data.get("aktiv", True),
        )


@dataclass(frozen=True)
class GataHojdsattningAtgard:
    """Experimentåtgärd: höjdsätter körbana längs linje med tvärfall och kantsten."""

    atgard_id: str
    linje: tuple[tuple[float, float], ...]
    bredd_m: float = 6.0
    delta_z_m: float | None = None
    start_rh2000: float | None = None
    slut_rh2000: float | None = None
    tvarfall: float = 0.02
    kantsten_hojd_m: float = 0.12
    aktiv: bool = True

    def __post_init__(self) -> None:
        aid = _validera_id(self.atgard_id, "atgard_id")
        linje = _validera_punkter(self.linje, 2, f"GataHojdsattningAtgard '{aid}'.linje")
        bredd = _validera_andlig(self.bredd_m, "bredd_m", strikt_storre=0.0)
        dz = (
            _validera_andlig(self.delta_z_m, "delta_z_m")
            if self.delta_z_m is not None
            else None
        )
        z_start = (
            _validera_andlig(self.start_rh2000, "start_rh2000")
            if self.start_rh2000 is not None
            else None
        )
        z_slut = (
            _validera_andlig(self.slut_rh2000, "slut_rh2000")
            if self.slut_rh2000 is not None
            else None
        )
        tvarfall = _validera_andlig(self.tvarfall, "tvarfall", minst=0.0)
        kantsten = _validera_andlig(self.kantsten_hojd_m, "kantsten_hojd_m", minst=0.0)
        aktiv = _validera_bool(self.aktiv, "aktiv")

        if (z_start is None) != (z_slut is None):
            raise ExperimentFel(
                f"GataHojdsattningAtgard '{aid}': både start_rh2000 och slut_rh2000 "
                "måste anges tillsammans."
            )
        har_abs = z_start is not None
        har_rel = dz is not None
        if har_abs == har_rel:
            raise ExperimentFel(
                f"GataHojdsattningAtgard '{aid}': ange exakt ett av delta_z_m eller "
                "(start_rh2000, slut_rh2000)."
            )

        object.__setattr__(self, "atgard_id", aid)
        object.__setattr__(self, "linje", linje)
        object.__setattr__(self, "bredd_m", bredd)
        object.__setattr__(self, "delta_z_m", dz)
        object.__setattr__(self, "start_rh2000", z_start)
        object.__setattr__(self, "slut_rh2000", z_slut)
        object.__setattr__(self, "tvarfall", tvarfall)
        object.__setattr__(self, "kantsten_hojd_m", kantsten)
        object.__setattr__(self, "aktiv", aktiv)

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar gatuhöjdsättningen till JSON-kompatibelt lexikon."""
        return {
            "atgard_id": self.atgard_id,
            "linje": [[p[0], p[1]] for p in self.linje],
            "bredd_m": self.bredd_m,
            "delta_z_m": self.delta_z_m,
            "start_rh2000": self.start_rh2000,
            "slut_rh2000": self.slut_rh2000,
            "tvarfall": self.tvarfall,
            "kantsten_hojd_m": self.kantsten_hojd_m,
            "aktiv": self.aktiv,
        }

    @classmethod
    def fran_dict(cls, data: Any) -> GataHojdsattningAtgard:
        """Läser och validerar en `GataHojdsattningAtgard` från JSON-objekt."""
        if not isinstance(data, Mapping):
            raise ExperimentFel("GataHojdsattningAtgard måste vara ett JSON-objekt.")
        for falt in ("atgard_id", "linje"):
            if falt not in data:
                raise ExperimentFel(f"GataHojdsattningAtgard saknar obligatoriskt fält '{falt}'.")
        return cls(
            atgard_id=data["atgard_id"],
            linje=_validera_punkter(data["linje"], 2, "GataHojdsattningAtgard.linje"),
            bredd_m=data.get("bredd_m", 6.0),
            delta_z_m=data.get("delta_z_m"),
            start_rh2000=data.get("start_rh2000"),
            slut_rh2000=data.get("slut_rh2000"),
            tvarfall=data.get("tvarfall", 0.02),
            kantsten_hojd_m=data.get("kantsten_hojd_m", 0.12),
            aktiv=data.get("aktiv", True),
        )


@dataclass(frozen=True)
class MarkJusteringAtgard:
    """Experimentåtgärd: schakt eller uppfyllnad inom en polygon."""

    atgard_id: str
    polygon: tuple[tuple[float, float], ...]
    delta_z_m: float | None = None
    z_rh2000: float | None = None
    aktiv: bool = True

    def __post_init__(self) -> None:
        aid = _validera_id(self.atgard_id, "atgard_id")
        poly = _validera_punkter(self.polygon, 3, f"MarkJusteringAtgard '{aid}'.polygon")
        dz = (
            _validera_andlig(self.delta_z_m, "delta_z_m")
            if self.delta_z_m is not None
            else None
        )
        z_fast = (
            _validera_andlig(self.z_rh2000, "z_rh2000")
            if self.z_rh2000 is not None
            else None
        )
        aktiv = _validera_bool(self.aktiv, "aktiv")

        if (dz is not None) == (z_fast is not None):
            raise ExperimentFel(
                f"MarkJusteringAtgard '{aid}': ange exakt ett av delta_z_m eller z_rh2000."
            )

        object.__setattr__(self, "atgard_id", aid)
        object.__setattr__(self, "polygon", poly)
        object.__setattr__(self, "delta_z_m", dz)
        object.__setattr__(self, "z_rh2000", z_fast)
        object.__setattr__(self, "aktiv", aktiv)

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar markjusteringen till JSON-kompatibelt lexikon."""
        return {
            "atgard_id": self.atgard_id,
            "polygon": [[p[0], p[1]] for p in self.polygon],
            "delta_z_m": self.delta_z_m,
            "z_rh2000": self.z_rh2000,
            "aktiv": self.aktiv,
        }

    @classmethod
    def fran_dict(cls, data: Any) -> MarkJusteringAtgard:
        """Läser och validerar en `MarkJusteringAtgard` från JSON-objekt."""
        if not isinstance(data, Mapping):
            raise ExperimentFel("MarkJusteringAtgard måste vara ett JSON-objekt.")
        for falt in ("atgard_id", "polygon"):
            if falt not in data:
                raise ExperimentFel(f"MarkJusteringAtgard saknar obligatoriskt fält '{falt}'.")
        return cls(
            atgard_id=data["atgard_id"],
            polygon=_validera_punkter(data["polygon"], 3, "MarkJusteringAtgard.polygon"),
            delta_z_m=data.get("delta_z_m"),
            z_rh2000=data.get("z_rh2000"),
            aktiv=data.get("aktiv", True),
        )


@dataclass(frozen=True)
class ExperimentAtgarder:
    """Samlade experimentåtgärder för efter-scenariot."""

    experiment_id: str = "experiment-1"
    diken: tuple[DikeAtgard, ...] = field(default_factory=tuple)
    vallar: tuple[VallAtgard, ...] = field(default_factory=tuple)
    gator: tuple[GataHojdsattningAtgard, ...] = field(default_factory=tuple)
    mark_justeringar: tuple[MarkJusteringAtgard, ...] = field(default_factory=tuple)
    beskrivning: str = ""

    def __post_init__(self) -> None:
        eid = _validera_id(self.experiment_id, "experiment_id")
        if not isinstance(self.beskrivning, str):
            raise ExperimentFel("ExperimentAtgarder.beskrivning måste vara en sträng.")
        object.__setattr__(self, "experiment_id", eid)
        object.__setattr__(self, "diken", tuple(self.diken))
        object.__setattr__(self, "vallar", tuple(self.vallar))
        object.__setattr__(self, "gator", tuple(self.gator))
        object.__setattr__(self, "mark_justeringar", tuple(self.mark_justeringar))

    def har_aktiva(self) -> bool:
        """Returnerar True om minst en experimentåtgärd är aktiv."""
        return (
            any(d.aktiv for d in self.diken)
            or any(v.aktiv for v in self.vallar)
            or any(g.aktiv for g in self.gator)
            or any(m.aktiv for m in self.mark_justeringar)
        )

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar experimentåtgärderna till JSON-kompatibelt lexikon."""
        return {
            "experiment_id": self.experiment_id,
            "diken": [d.till_dict() for d in self.diken],
            "vallar": [v.till_dict() for v in self.vallar],
            "gator": [g.till_dict() for g in self.gator],
            "mark_justeringar": [m.till_dict() for m in self.mark_justeringar],
            "beskrivning": self.beskrivning,
        }

    def sha256(self) -> str:
        """Deterministisk SHA-256 över kanonisk JSON-representation."""
        kanonisk = json.dumps(
            self.till_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        return sha256_bytes(kanonisk.encode("utf-8"))

    @classmethod
    def fran_dict(cls, data: Any) -> ExperimentAtgarder:
        """Läser och validerar en `ExperimentAtgarder` från JSON-objekt."""
        if not isinstance(data, Mapping):
            raise ExperimentFel("ExperimentAtgarder måste vara ett JSON-objekt.")
        raw_diken = data.get("diken", ())
        raw_vallar = data.get("vallar", ())
        raw_gator = data.get("gator", ())
        raw_mark = data.get("mark_justeringar", ())
        for raw, namn in (
            (raw_diken, "diken"),
            (raw_vallar, "vallar"),
            (raw_gator, "gator"),
            (raw_mark, "mark_justeringar"),
        ):
            if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
                raise ExperimentFel(f"ExperimentAtgarder.{namn} måste vara en JSON-array.")

        return cls(
            experiment_id=data.get("experiment_id", "experiment-1"),
            diken=tuple(DikeAtgard.fran_dict(d) for d in raw_diken),
            vallar=tuple(VallAtgard.fran_dict(v) for v in raw_vallar),
            gator=tuple(GataHojdsattningAtgard.fran_dict(g) for g in raw_gator),
            mark_justeringar=tuple(MarkJusteringAtgard.fran_dict(m) for m in raw_mark),
            beskrivning=str(data.get("beskrivning", "")),
        )


@dataclass(frozen=True)
class ExperimentResultat:
    """Resultat efter tillämpning av `ExperimentAtgarder` på basrastrer."""

    mark_z: NDArray[np.float32]
    osakerhet_z: NDArray[np.float32]
    markslag: NDArray[np.uint8]
    antal_andrade_celler: int
    antal_aktiva_atgarder: int
    schakt_volym_m3: float
    fyll_volym_m3: float
    experiment_sha256: str


def _validera_indata_rastrer(
    mark_z: NDArray[np.float32],
    osakerhet_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
    geo: GeoTransformInfo,
) -> None:
    if mark_z.ndim != 2 or osakerhet_z.shape != mark_z.shape or markslag.shape != mark_z.shape:
        raise ExperimentFel(
            f"Oförenliga rasterformer: mark_z={mark_z.shape}, "
            f"osakerhet_z={osakerhet_z.shape}, markslag={markslag.shape}."
        )
    if mark_z.shape != (geo.hojd, geo.bredd):
        raise ExperimentFel(
            f"GeoTransformInfo ({geo.hojd}x{geo.bredd}) matchar inte raster ({mark_z.shape})."
        )
    if geo.pixelstorlek_x <= 0.0 or geo.pixelstorlek_y <= 0.0:
        raise ExperimentFel(
            f"Pixelstorlek måste vara > 0, fick ({geo.pixelstorlek_x}, {geo.pixelstorlek_y})."
        )


def _polygon_mask(
    polygon: Sequence[tuple[float, float]],
    geo: GeoTransformInfo,
) -> NDArray[np.bool_]:
    """Deterministisk vektoriserad punkt-i-polygon-mask på cellcentra."""
    hojd = geo.hojd
    bredd = geo.bredd
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    mask = np.zeros((hojd, bredd), dtype=bool)
    if len(polygon) < 3:
        return np.asarray(mask, dtype=np.bool_)

    xs = [float(p[0]) for p in polygon]
    ys = [float(p[1]) for p in polygon]
    e_min, e_max = min(xs), max(xs)
    n_min, n_max = min(ys), max(ys)

    if e_max < geo.x_min or e_min > geo.x_max or n_max < geo.y_min or n_min > geo.y_max:
        return np.asarray(mask, dtype=np.bool_)

    kol0 = max(0, math.floor((e_min - geo.x_min) / dx))
    kol1 = min(bredd, math.ceil((e_max - geo.x_min) / dx) + 1)
    rad0 = max(0, math.floor((geo.y_max - n_max) / dy))
    rad1 = min(hojd, math.ceil((geo.y_max - n_min) / dy) + 1)
    if kol1 <= kol0 or rad1 <= rad0:
        return np.asarray(mask, dtype=np.bool_)

    e_vek = geo.x_min + (np.arange(kol0, kol1, dtype=np.float64) + 0.5) * dx
    n_vek = geo.y_max - (np.arange(rad0, rad1, dtype=np.float64) + 0.5) * dy
    sub_e, sub_n = np.meshgrid(e_vek, n_vek)

    inuti = np.zeros(sub_e.shape, dtype=bool)
    pa_kant = np.zeros(sub_e.shape, dtype=bool)

    ring = list(polygon)
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring.pop()
    n_horn = len(ring)
    if n_horn < 3:
        return np.asarray(mask, dtype=np.bool_)

    for idx in range(n_horn):
        e0, n0 = float(ring[idx][0]), float(ring[idx][1])
        e1, n1 = float(ring[(idx + 1) % n_horn][0]), float(ring[(idx + 1) % n_horn][1])

        de = e1 - e0
        dn = n1 - n0
        l2 = de * de + dn * dn
        if l2 > 0.0:
            t = np.clip(((sub_e - e0) * de + (sub_n - n0) * dn) / l2, 0.0, 1.0)
            dist = np.hypot(sub_e - (e0 + t * de), sub_n - (n0 + t * dn))
            pa_kant |= dist <= _KANT_TOL_M

        korsar_y = (n0 > sub_n) != (n1 > sub_n)
        if np.any(korsar_y):
            dn_saker = n1 - n0
            if dn_saker != 0.0:
                x_kors = e0 + (sub_n - n0) * (e1 - e0) / dn_saker
                inuti ^= korsar_y & (sub_e < x_kors)

    mask[rad0:rad1, kol0:kol1] = inuti | pa_kant
    return np.asarray(mask, dtype=np.bool_)


def _sampla_markhojd(
    mark_z: NDArray[np.float32],
    geo: GeoTransformInfo,
    e_m: float,
    n_m: float,
) -> float:
    kol = min(geo.bredd - 1, max(0, math.floor((e_m - geo.x_min) / float(geo.pixelstorlek_x))))
    rad = min(geo.hojd - 1, max(0, math.floor((geo.y_max - n_m) / float(geo.pixelstorlek_y))))
    val = float(mark_z[rad, kol])
    if not math.isfinite(val):
        raise ExperimentFel(
            f"Kunde inte sampla markhöjd vid ({e_m}, {n_m}): cell ({rad}, {kol}) är inte ändlig."
        )
    return val


def _supercover_celler(
    e0: float,
    n0: float,
    e1: float,
    n1: float,
    geo: GeoTransformInfo,
) -> list[tuple[int, int]]:
    """4-sammanhängande supercover-rasterisering av ett linjesegment i rasternätet."""
    dx_m = float(geo.pixelstorlek_x)
    dy_m = float(geo.pixelstorlek_y)
    x0 = (e0 - geo.x_min) / dx_m
    y0 = (geo.y_max - n0) / dy_m
    x1 = (e1 - geo.x_min) / dx_m
    y1 = (geo.y_max - n1) / dy_m

    kol = math.floor(x0)
    rad = math.floor(y0)
    slut_kol = math.floor(x1)
    slut_rad = math.floor(y1)
    celler: list[tuple[int, int]] = [(rad, kol)]

    dx = x1 - x0
    dy = y1 - y0
    steg_x = 1 if dx > 0.0 else (-1 if dx < 0.0 else 0)
    steg_y = 1 if dy > 0.0 else (-1 if dy < 0.0 else 0)
    if steg_x == 0 and steg_y == 0:
        return celler

    t_dx = abs(1.0 / dx) if dx != 0.0 else math.inf
    t_dy = abs(1.0 / dy) if dy != 0.0 else math.inf
    if dx > 0.0:
        t_max_x = ((kol + 1.0) - x0) * t_dx
    elif dx < 0.0:
        t_max_x = (x0 - kol) * t_dx
    else:
        t_max_x = math.inf

    if dy > 0.0:
        t_max_y = ((rad + 1.0) - y0) * t_dy
    elif dy < 0.0:
        t_max_y = (y0 - rad) * t_dy
    else:
        t_max_y = math.inf

    max_steg = abs(slut_kol - kol) + abs(slut_rad - rad) + 4
    eps = 1e-12
    for _ in range(max_steg):
        if kol == slut_kol and rad == slut_rad:
            break
        if abs(t_max_x - t_max_y) <= eps:
            if steg_x != 0:
                celler.append((rad, kol + steg_x))
            if steg_y != 0:
                celler.append((rad + steg_y, kol))
            kol += steg_x
            rad += steg_y
            t_max_x += t_dx
            t_max_y += t_dy
            celler.append((rad, kol))
        elif t_max_x < t_max_y:
            kol += steg_x
            t_max_x += t_dx
            celler.append((rad, kol))
        else:
            rad += steg_y
            t_max_y += t_dy
            celler.append((rad, kol))

    if (slut_rad, slut_kol) not in celler:
        sr, sc = celler[-1]
        if abs(slut_rad - sr) == 1 and abs(slut_kol - sc) == 1:
            celler.append((sr, slut_kol))
        celler.append((slut_rad, slut_kol))
    return celler


def _polylinje_langder(linje: tuple[tuple[float, float], ...]) -> tuple[list[float], float]:
    cum: list[float] = [0.0]
    for idx in range(len(linje) - 1):
        seg = math.hypot(linje[idx + 1][0] - linje[idx][0], linje[idx + 1][1] - linje[idx][1])
        cum.append(cum[-1] + seg)
    return cum, cum[-1]


def _subgrid_bbox(
    linje: Sequence[tuple[float, float]],
    padding_m: float,
    geo: GeoTransformInfo,
) -> tuple[int, int, int, int] | None:
    xs = [float(p[0]) for p in linje]
    ys = [float(p[1]) for p in linje]
    e_min = min(xs) - padding_m
    e_max = max(xs) + padding_m
    n_min = min(ys) - padding_m
    n_max = max(ys) + padding_m

    if e_max < geo.x_min or e_min > geo.x_max or n_max < geo.y_min or n_min > geo.y_max:
        return None

    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    kol0 = max(0, math.floor((e_min - geo.x_min) / dx) - 1)
    kol1 = min(geo.bredd, math.ceil((e_max - geo.x_min) / dx) + 1)
    rad0 = max(0, math.floor((geo.y_max - n_max) / dy) - 1)
    rad1 = min(geo.hojd, math.ceil((geo.y_max - n_min) / dy) + 1)
    if kol1 <= kol0 or rad1 <= rad0:
        return None
    return rad0, rad1, kol0, kol1


def _projicera_pa_polylinje(
    sub_e: NDArray[np.float64],
    sub_n: NDArray[np.float64],
    linje: tuple[tuple[float, float], ...],
    *,
    oppen_anda: bool = False,
) -> tuple[
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.bool_],
]:
    """Beräknar minsta avstånd, projicerad punkt (E, N) och båglängdsandel längs `linje`."""
    cum_len, total_len = _polylinje_langder(linje)
    n_seg = len(linje) - 1

    basta_dist = np.full(sub_e.shape, math.inf, dtype=np.float64)
    basta_pe = np.zeros(sub_e.shape, dtype=np.float64)
    basta_pn = np.zeros(sub_e.shape, dtype=np.float64)
    basta_frac = np.zeros(sub_e.shape, dtype=np.float64)
    har_traff = np.zeros(sub_e.shape, dtype=bool)

    for idx in range(n_seg):
        e0, n0 = linje[idx]
        e1, n1 = linje[idx + 1]
        de = e1 - e0
        dn = n1 - n0
        l2 = de * de + dn * dn
        seg_len = math.sqrt(l2)

        if l2 <= 1e-18:
            t_raw = np.zeros(sub_e.shape, dtype=np.float64)
            t = t_raw
            giltig = np.ones(sub_e.shape, dtype=bool)
        else:
            t_raw = ((sub_e - e0) * de + (sub_n - n0) * dn) / l2
            giltig = np.ones(sub_e.shape, dtype=bool)
            if oppen_anda:
                if idx == 0:
                    giltig &= t_raw >= -_KANT_TOL_M
                if idx == n_seg - 1:
                    giltig &= t_raw <= 1.0 + _KANT_TOL_M
            t = np.clip(t_raw, 0.0, 1.0)

        proj_e = e0 + t * de
        proj_n = n0 + t * dn
        dist = np.hypot(sub_e - proj_e, sub_n - proj_n)
        frac = (cum_len[idx] + t * seg_len) / total_len if total_len > 0.0 else np.zeros_like(t)

        battre = giltig & (dist < basta_dist)
        basta_dist = np.where(battre, dist, basta_dist)
        basta_pe = np.where(battre, proj_e, basta_pe)
        basta_pn = np.where(battre, proj_n, basta_pn)
        basta_frac = np.where(battre, frac, basta_frac)
        har_traff |= battre

    return basta_dist, basta_pe, basta_pn, basta_frac, np.asarray(har_traff, dtype=np.bool_)


def _rand_utanfor(
    mask: NDArray[np.bool_],
    mark_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
) -> NDArray[np.bool_]:
    """1-cellsrandsring precis utanför `mask` (4-grannar; föredrar icke-byggnad)."""
    hojd, bredd = mask.shape
    utvidgad = np.zeros((hojd, bredd), dtype=bool)
    utvidgad[1:, :] |= mask[:-1, :]
    utvidgad[:-1, :] |= mask[1:, :]
    utvidgad[:, 1:] |= mask[:, :-1]
    utvidgad[:, :-1] |= mask[:, 1:]
    utanfor = utvidgad & ~mask & np.isfinite(mark_z)
    icke_bygg = utanfor & (markslag != MARKSLAG_BYGGNAD)
    if np.any(icke_bygg):
        return np.asarray(icke_bygg, dtype=np.bool_)
    if np.any(utanfor):
        return np.asarray(utanfor, dtype=np.bool_)

    # Reserv med 8-grannar om alla 4-grannar råkade sakna giltiga celler
    utvidgad8 = utvidgad.copy()
    utvidgad8[1:, 1:] |= mask[:-1, :-1]
    utvidgad8[1:, :-1] |= mask[:-1, 1:]
    utvidgad8[:-1, 1:] |= mask[1:, :-1]
    utvidgad8[:-1, :-1] |= mask[1:, 1:]
    utanfor8 = utvidgad8 & ~mask & np.isfinite(mark_z)
    icke_bygg8 = utanfor8 & (markslag != MARKSLAG_BYGGNAD)
    if np.any(icke_bygg8):
        return np.asarray(icke_bygg8, dtype=np.bool_)
    return np.asarray(utanfor8, dtype=np.bool_)


def _berakna_andrad_mask(
    bas_z: NDArray[np.float32],
    bas_osak: NDArray[np.float32],
    bas_slag: NDArray[np.uint8],
    ny_z: NDArray[np.float32],
    ny_osak: NDArray[np.float32],
    ny_slag: NDArray[np.uint8],
) -> NDArray[np.bool_]:
    z_lika = (bas_z == ny_z) | (np.isnan(bas_z) & np.isnan(ny_z))
    osak_lika = (bas_osak == ny_osak) | (np.isnan(bas_osak) & np.isnan(ny_osak))
    slag_lika = bas_slag == ny_slag
    return np.asarray(~(z_lika & osak_lika & slag_lika), dtype=np.bool_)


def _applicera_oppna_passage(
    ny_z: NDArray[np.float32],
    ny_slag: NDArray[np.uint8],
    geo: GeoTransformInfo,
    passage: OppnaPassage,
) -> None:
    halv_bredd = 0.5 * passage.bredd_m
    bbox = _subgrid_bbox(passage.linje, halv_bredd, geo)
    if bbox is None:
        return
    rad0, rad1, kol0, kol1 = bbox
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    e_vek = geo.x_min + (np.arange(kol0, kol1, dtype=np.float64) + 0.5) * dx
    n_vek = geo.y_max - (np.arange(rad0, rad1, dtype=np.float64) + 0.5) * dy
    sub_e, sub_n = np.meshgrid(e_vek, n_vek)

    dist, _, _, frac, _ = _projicera_pa_polylinje(sub_e, sub_n, passage.linje)
    mask = dist <= halv_bredd
    for idx in range(len(passage.linje) - 1):
        e0, n0 = passage.linje[idx]
        e1, n1 = passage.linje[idx + 1]
        for r, c in _supercover_celler(e0, n0, e1, n1, geo):
            if rad0 <= r < rad1 and kol0 <= c < kol1:
                mask[r - rad0, c - kol0] = True

    if not np.any(mask):
        return

    if passage.botten_rh2000 is not None:
        botten_grid = np.full(sub_e.shape, float(passage.botten_rh2000), dtype=np.float32)
    else:
        z_start = _sampla_markhojd(ny_z, geo, passage.linje[0][0], passage.linje[0][1])
        z_slut = _sampla_markhojd(ny_z, geo, passage.linje[-1][0], passage.linje[-1][1])
        botten_grid = np.asarray(z_start + frac * (z_slut - z_start), dtype=np.float32)

    z_sub = ny_z[rad0:rad1, kol0:kol1]
    slag_sub = ny_slag[rad0:rad1, kol0:kol1]
    z_sub[mask] = np.minimum(z_sub[mask], botten_grid[mask])
    slag_sub[mask & (slag_sub == MARKSLAG_BYGGNAD)] = np.uint8(HARDGJORT)


def tillampa_grundmodell_rattning(
    mark_z: NDArray[np.float32],
    osakerhet_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
    geo: GeoTransformInfo,
    rattning: GrundmodellRattning,
) -> RattningResultat:
    """Tillämpar grundmodell-rättningar (icke-destruktivt; kopierar alltid indata)."""
    _validera_indata_rastrer(mark_z, osakerhet_z, markslag, geo)

    ny_z = np.array(mark_z, dtype=np.float32, copy=True)
    ny_osak = np.array(osakerhet_z, dtype=np.float32, copy=True)
    ny_slag = np.array(markslag, dtype=np.uint8, copy=True)
    antal_aktiva = 0

    for hr in rattning.hojd_rattningar:
        if not hr.aktiv:
            continue
        antal_aktiva += 1
        mask = _polygon_mask(hr.polygon, geo)
        if not np.any(mask):
            continue
        if hr.z_rh2000 is not None:
            ny_z[mask] = np.float32(hr.z_rh2000)
        elif hr.delta_z_m is not None:
            ny_z[mask] = np.asarray(ny_z[mask] + np.float32(hr.delta_z_m), dtype=np.float32)
        elif hr.utjamna_fran_rand:
            rand = _rand_utanfor(mask, ny_z, ny_slag)
            if not np.any(rand):
                raise ExperimentFel(
                    f"HojdRattning '{hr.rattning_id}' kunde inte hitta giltiga randceller "
                    "utanför polygonen för utjämning."
                )
            median_z = float(np.median(ny_z[rand]))
            ny_z[mask] = np.float32(median_z)
        if hr.ta_bort_byggnad:
            ny_slag[mask & (ny_slag == MARKSLAG_BYGGNAD)] = np.uint8(HARDGJORT)

    for mr in rattning.markslag_rattningar:
        if not mr.aktiv:
            continue
        antal_aktiva += 1
        mask = _polygon_mask(mr.polygon, geo)
        if np.any(mask):
            ny_slag[mask] = np.uint8(mr.markslag_kod)

    for passage in rattning.passager:
        if not passage.aktiv:
            continue
        antal_aktiva += 1
        _applicera_oppna_passage(ny_z, ny_slag, geo, passage)

    andrad_mask = _berakna_andrad_mask(mark_z, osakerhet_z, markslag, ny_z, ny_osak, ny_slag)
    return RattningResultat(
        mark_z=ny_z,
        osakerhet_z=ny_osak,
        markslag=ny_slag,
        antal_andrade_celler=int(np.sum(andrad_mask)),
        antal_aktiva_rattningar=antal_aktiva,
        rattning_sha256=rattning.sha256(),
    )


def _sampla_proj_z(
    bas_z: NDArray[np.float32],
    proj_e: NDArray[np.float64],
    proj_n: NDArray[np.float64],
    geo: GeoTransformInfo,
) -> NDArray[np.float64]:
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    kol = np.clip(np.floor((proj_e - geo.x_min) / dx).astype(np.intp), 0, geo.bredd - 1)
    rad = np.clip(np.floor((geo.y_max - proj_n) / dy).astype(np.intp), 0, geo.hojd - 1)
    return np.asarray(bas_z[rad, kol], dtype=np.float64)


def _applicera_dike(
    ny_z: NDArray[np.float32],
    geo: GeoTransformInfo,
    dike: DikeAtgard,
) -> None:
    finita = np.isfinite(ny_z)
    if not np.any(finita):
        return
    z_min_r = float(np.min(ny_z[finita]))
    z_max_r = float(np.max(ny_z[finita]))
    if dike.botten_start_rh2000 is not None and dike.botten_slut_rh2000 is not None:
        min_botten = min(dike.botten_start_rh2000, dike.botten_slut_rh2000)
    else:
        assert dike.djup_m is not None
        min_botten = z_min_r - dike.djup_m

    max_skarning = max(0.0, z_max_r - min_botten)
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    pad = 0.5 * dike.botten_bredd_m + max_skarning * dike.slantslutning + max(dx, dy)
    bbox = _subgrid_bbox(dike.linje, pad, geo)
    if bbox is None:
        return
    rad0, rad1, kol0, kol1 = bbox

    e_vek = geo.x_min + (np.arange(kol0, kol1, dtype=np.float64) + 0.5) * dx
    n_vek = geo.y_max - (np.arange(rad0, rad1, dtype=np.float64) + 0.5) * dy
    sub_e, sub_n = np.meshgrid(e_vek, n_vek)

    dist, proj_e, proj_n, frac, _ = _projicera_pa_polylinje(sub_e, sub_n, dike.linje)
    if dike.botten_start_rh2000 is not None and dike.botten_slut_rh2000 is not None:
        botten = dike.botten_start_rh2000 + frac * (
            dike.botten_slut_rh2000 - dike.botten_start_rh2000
        )
    else:
        assert dike.djup_m is not None
        botten = _sampla_proj_z(ny_z, proj_e, proj_n, geo) - dike.djup_m

    z_mal = botten + np.maximum(0.0, dist - 0.5 * dike.botten_bredd_m) / dike.slantslutning
    z_sub = ny_z[rad0:rad1, kol0:kol1]
    giltig = np.isfinite(z_sub) & np.isfinite(z_mal)
    if np.any(giltig):
        z_sub[giltig] = np.minimum(z_sub[giltig], z_mal[giltig].astype(np.float32))


def _applicera_vall(
    ny_z: NDArray[np.float32],
    geo: GeoTransformInfo,
    vall: VallAtgard,
) -> None:
    finita = np.isfinite(ny_z)
    if not np.any(finita):
        return
    z_min_r = float(np.min(ny_z[finita]))
    z_max_r = float(np.max(ny_z[finita]))
    if vall.kron_rh2000 is not None:
        max_kron = vall.kron_rh2000
    else:
        assert vall.hojd_over_mark_m is not None
        max_kron = z_max_r + vall.hojd_over_mark_m

    max_hojd = max(0.0, max_kron - z_min_r)
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    pad = 0.5 * vall.kron_bredd_m + max_hojd * vall.slantslutning + max(dx, dy)
    bbox = _subgrid_bbox(vall.linje, pad, geo)
    if bbox is None:
        return
    rad0, rad1, kol0, kol1 = bbox

    e_vek = geo.x_min + (np.arange(kol0, kol1, dtype=np.float64) + 0.5) * dx
    n_vek = geo.y_max - (np.arange(rad0, rad1, dtype=np.float64) + 0.5) * dy
    sub_e, sub_n = np.meshgrid(e_vek, n_vek)

    dist, proj_e, proj_n, _, _ = _projicera_pa_polylinje(sub_e, sub_n, vall.linje)
    # Säkerställ obruten 4-sammanhängande vallkrön även om kron_bredd_m är smal
    for idx in range(len(vall.linje) - 1):
        e0, n0 = vall.linje[idx]
        e1, n1 = vall.linje[idx + 1]
        for r, c in _supercover_celler(e0, n0, e1, n1, geo):
            if rad0 <= r < rad1 and kol0 <= c < kol1:
                dist[r - rad0, c - kol0] = min(
                    float(dist[r - rad0, c - kol0]), 0.5 * vall.kron_bredd_m
                )

    if vall.kron_rh2000 is not None:
        kron = np.full(sub_e.shape, vall.kron_rh2000, dtype=np.float64)
    else:
        assert vall.hojd_over_mark_m is not None
        kron = _sampla_proj_z(ny_z, proj_e, proj_n, geo) + vall.hojd_over_mark_m

    z_mal = kron - np.maximum(0.0, dist - 0.5 * vall.kron_bredd_m) / vall.slantslutning
    z_sub = ny_z[rad0:rad1, kol0:kol1]
    giltig = np.isfinite(z_sub) & np.isfinite(z_mal)
    if np.any(giltig):
        z_sub[giltig] = np.maximum(z_sub[giltig], z_mal[giltig].astype(np.float32))


def _applicera_gata(
    ny_z: NDArray[np.float32],
    ny_slag: NDArray[np.uint8],
    geo: GeoTransformInfo,
    gata: GataHojdsattningAtgard,
) -> None:
    halv_bredd = 0.5 * gata.bredd_m
    bbox = _subgrid_bbox(gata.linje, halv_bredd, geo)
    if bbox is None:
        return
    rad0, rad1, kol0, kol1 = bbox

    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    e_vek = geo.x_min + (np.arange(kol0, kol1, dtype=np.float64) + 0.5) * dx
    n_vek = geo.y_max - (np.arange(rad0, rad1, dtype=np.float64) + 0.5) * dy
    sub_e, sub_n = np.meshgrid(e_vek, n_vek)

    dist, proj_e, proj_n, frac, har_traff = _projicera_pa_polylinje(
        sub_e, sub_n, gata.linje, oppen_anda=True
    )
    inom = har_traff & (dist <= halv_bredd + _KANT_TOL_M)
    if not np.any(inom):
        return

    if gata.start_rh2000 is not None and gata.slut_rh2000 is not None:
        z_mitt = gata.start_rh2000 + frac * (gata.slut_rh2000 - gata.start_rh2000)
    else:
        assert gata.delta_z_m is not None
        z_mitt = _sampla_proj_z(ny_z, proj_e, proj_n, geo) + gata.delta_z_m

    z_gata = z_mitt - gata.tvarfall * dist
    if gata.kantsten_hojd_m > 0.0:
        kant_zon = min(0.5 * halv_bredd, max(dx, dy))
        pa_kantsten = inom & (dist >= halv_bredd - kant_zon - _KANT_TOL_M)
        z_rannsten = z_mitt - gata.tvarfall * halv_bredd
        z_gata = np.where(pa_kantsten, z_rannsten + gata.kantsten_hojd_m, z_gata)

    giltig = inom & np.isfinite(z_gata)
    if not np.any(giltig):
        return

    z_sub = ny_z[rad0:rad1, kol0:kol1]
    slag_sub = ny_slag[rad0:rad1, kol0:kol1]
    z_sub[giltig] = z_gata[giltig].astype(np.float32)
    slag_sub[giltig] = np.uint8(HARDGJORT)


def tillampa_experiment(
    mark_z: NDArray[np.float32],
    osakerhet_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
    geo: GeoTransformInfo,
    atgarder: ExperimentAtgarder,
) -> ExperimentResultat:
    """Tillämpar experimentåtgärder för efter-scenariot (icke-destruktivt)."""
    _validera_indata_rastrer(mark_z, osakerhet_z, markslag, geo)

    ny_z = np.array(mark_z, dtype=np.float32, copy=True)
    ny_osak = np.array(osakerhet_z, dtype=np.float32, copy=True)
    ny_slag = np.array(markslag, dtype=np.uint8, copy=True)
    antal_aktiva = 0

    for mj in atgarder.mark_justeringar:
        if not mj.aktiv:
            continue
        antal_aktiva += 1
        mask = _polygon_mask(mj.polygon, geo)
        if not np.any(mask):
            continue
        if mj.z_rh2000 is not None:
            ny_z[mask] = np.float32(mj.z_rh2000)
        elif mj.delta_z_m is not None:
            ny_z[mask] = np.asarray(ny_z[mask] + np.float32(mj.delta_z_m), dtype=np.float32)

    for gata in atgarder.gator:
        if not gata.aktiv:
            continue
        antal_aktiva += 1
        _applicera_gata(ny_z, ny_slag, geo, gata)

    for dike in atgarder.diken:
        if not dike.aktiv:
            continue
        antal_aktiva += 1
        _applicera_dike(ny_z, geo, dike)

    for vall in atgarder.vallar:
        if not vall.aktiv:
            continue
        antal_aktiva += 1
        _applicera_vall(ny_z, geo, vall)

    andrad_mask = _berakna_andrad_mask(mark_z, osakerhet_z, markslag, ny_z, ny_osak, ny_slag)

    cell_area_m2 = float(geo.pixelstorlek_x) * float(geo.pixelstorlek_y)
    finita = np.isfinite(mark_z) & np.isfinite(ny_z)
    dz = np.zeros(mark_z.shape, dtype=np.float64)
    dz[finita] = ny_z[finita].astype(np.float64) - mark_z[finita].astype(np.float64)
    schakt_volym_m3 = float(np.sum(-dz[dz < 0.0])) * cell_area_m2
    fyll_volym_m3 = float(np.sum(dz[dz > 0.0])) * cell_area_m2

    return ExperimentResultat(
        mark_z=ny_z,
        osakerhet_z=ny_osak,
        markslag=ny_slag,
        antal_andrade_celler=int(np.sum(andrad_mask)),
        antal_aktiva_atgarder=antal_aktiva,
        schakt_volym_m3=schakt_volym_m3,
        fyll_volym_m3=fyll_volym_m3,
        experiment_sha256=atgarder.sha256(),
    )
