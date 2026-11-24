"""Datatyper, validering och K3/v1-I/O för projektlappar (T04)."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from gemensamt.hash import sha256_bytes
from gemensamt.konfig import KonfigFel, las_konfig

KONTRAKT_K3 = "K3/v1"
GILTIGA_OPERATIONSTYPER: frozenset[str] = frozenset(
    {"hojdyta", "byggnad", "gata", "magasin", "markslag"}
)
GILTIGA_MARKSLAG: frozenset[int] = frozenset(range(10))
GILTIGA_KOORDINATORDNINGAR: frozenset[str] = frozenset({"NEZ", "ENZ"})


class LappFel(Exception):
    """Fel vid validering, inläsning, tillämpning eller omräkning av projektlapp."""


def _validera_andlig(varde: float, namn: str, *, minst: float | None = None) -> float:
    tal = float(varde)
    if not math.isfinite(tal):
        raise LappFel(f"Fältet '{namn}' måste vara ändligt, fick {varde!r}.")
    if minst is not None and tal < minst:
        raise LappFel(f"Fältet '{namn}' måste vara >= {minst}, fick {tal}.")
    return tal


def _validera_punkt(punkt: Sequence[float], sammanhang: str) -> tuple[float, float]:
    if len(punkt) != 2:
        raise LappFel(f"{sammanhang} måste ha exakt 2 koordinater (E, N), fick {len(punkt)}.")
    e = _validera_andlig(float(punkt[0]), f"{sammanhang}.E")
    n = _validera_andlig(float(punkt[1]), f"{sammanhang}.N")
    return (e, n)


def _validera_punkter(
    punkter: Sequence[Sequence[float]],
    minst_antal: int,
    sammanhang: str,
) -> tuple[tuple[float, float], ...]:
    if len(punkter) < minst_antal:
        raise LappFel(
            f"{sammanhang} måste ha minst {minst_antal} punkter, fick {len(punkter)}."
        )
    return tuple(_validera_punkt(p, f"{sammanhang}[{idx}]") for idx, p in enumerate(punkter))


@dataclass(frozen=True)
class Gatusektion:
    """Parametrisk gatusektion enligt `config.toml` under `[sektioner]` (K3/v1)."""

    namn: str
    total_bredd_m: float
    korbana_bredd_m: float
    gangbana_bredd_m: float
    kantsten_hojd_m: float
    tvarfall_korbana_andel: float
    tvarfall_gangbana_andel: float
    brunnsavstand_m: float

    def __post_init__(self) -> None:
        if not isinstance(self.namn, str) or not self.namn.strip():
            raise LappFel("Gatusektion.namn måste vara en icke-tom sträng.")
        total = _validera_andlig(self.total_bredd_m, "total_bredd_m", minst=0.0)
        korbana = _validera_andlig(self.korbana_bredd_m, "korbana_bredd_m", minst=0.0)
        gangbana = _validera_andlig(self.gangbana_bredd_m, "gangbana_bredd_m", minst=0.0)
        kantsten = _validera_andlig(self.kantsten_hojd_m, "kantsten_hojd_m", minst=0.0)
        tv_kor = _validera_andlig(
            self.tvarfall_korbana_andel, "tvarfall_korbana_andel", minst=0.0
        )
        tv_gang = _validera_andlig(
            self.tvarfall_gangbana_andel, "tvarfall_gangbana_andel", minst=0.0
        )
        brunn = _validera_andlig(self.brunnsavstand_m, "brunnsavstand_m", minst=0.0)

        if total <= 0.0:
            raise LappFel(f"total_bredd_m måste vara > 0, fick {total}.")
        if korbana <= 0.0:
            raise LappFel(f"korbana_bredd_m måste vara > 0, fick {korbana}.")
        if korbana > total + 1e-9:
            raise LappFel(
                f"korbana_bredd_m ({korbana}) får inte överstiga total_bredd_m ({total})."
            )
        if korbana + 2.0 * gangbana > total + 1e-6:
            raise LappFel(
                f"korbana_bredd_m ({korbana}) + 2 * gangbana_bredd_m ({gangbana}) "
                f"överstiger total_bredd_m ({total})."
            )

        object.__setattr__(self, "total_bredd_m", total)
        object.__setattr__(self, "korbana_bredd_m", korbana)
        object.__setattr__(self, "gangbana_bredd_m", gangbana)
        object.__setattr__(self, "kantsten_hojd_m", kantsten)
        object.__setattr__(self, "tvarfall_korbana_andel", tv_kor)
        object.__setattr__(self, "tvarfall_gangbana_andel", tv_gang)
        object.__setattr__(self, "brunnsavstand_m", brunn)

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar gatusektionen till ett JSON-kompatibelt lexikon."""
        return {
            "namn": self.namn,
            "total_bredd_m": self.total_bredd_m,
            "korbana_bredd_m": self.korbana_bredd_m,
            "gangbana_bredd_m": self.gangbana_bredd_m,
            "kantsten_hojd_m": self.kantsten_hojd_m,
            "tvarfall_korbana_andel": self.tvarfall_korbana_andel,
            "tvarfall_gangbana_andel": self.tvarfall_gangbana_andel,
            "brunnsavstand_m": self.brunnsavstand_m,
        }


@dataclass(frozen=True)
class LappOperation:
    """En deklarerad operation i `lapp.json` enligt kontrakt K3/v1."""

    typ: str
    lager: str
    entre_rh2000: str | None = None
    sektion: str | None = None
    volym_m3: str | None = None

    def __post_init__(self) -> None:
        if self.typ not in GILTIGA_OPERATIONSTYPER:
            tillatna = ", ".join(sorted(GILTIGA_OPERATIONSTYPER))
            raise LappFel(f"Ogiltig operationstyp '{self.typ}'. Giltiga: {tillatna}.")
        if not isinstance(self.lager, str) or not self.lager.strip():
            raise LappFel("LappOperation.lager måste vara en icke-tom sträng.")

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar operationen i K3/v1-format."""
        ut: dict[str, Any] = {"typ": self.typ, "lager": self.lager}
        if self.entre_rh2000 is not None:
            ut["entre_rh2000"] = self.entre_rh2000
        if self.sektion is not None:
            ut["sektion"] = self.sektion
        if self.volym_m3 is not None:
            ut["volym_m3"] = self.volym_m3
        return ut

    @classmethod
    def fran_dict(cls, data: Any) -> LappOperation:
        """Läser och validerar en `LappOperation` från ett JSON-objekt."""
        if not isinstance(data, Mapping):
            raise LappFel("LappOperation måste vara ett JSON-objekt.")
        for falt in ("typ", "lager"):
            if falt not in data or not isinstance(data[falt], str):
                raise LappFel(f"LappOperation saknar strängfält '{falt}'.")
        entre = data.get("entre_rh2000")
        sektion = data.get("sektion")
        volym = data.get("volym_m3")
        if entre is not None and not isinstance(entre, str):
            raise LappFel("LappOperation.entre_rh2000 måste vara en sträng eller null.")
        if sektion is not None and not isinstance(sektion, str):
            raise LappFel("LappOperation.sektion måste vara en sträng eller null.")
        if volym is not None and not isinstance(volym, str):
            raise LappFel("LappOperation.volym_m3 måste vara en sträng eller null.")
        return cls(
            typ=data["typ"],
            lager=data["lager"],
            entre_rh2000=entre,
            sektion=sektion,
            volym_m3=volym,
        )


def _punkt3(p: Any, sammanhang: str) -> list[float]:
    if isinstance(p, str | bytes) or not isinstance(p, Sequence) or len(p) != 3:
        raise LappFel(f"{sammanhang} måste vara [E, N, z] (tre tal).")
    return [_validera_andlig(float(v), f"{sammanhang}[{i}]") for i, v in enumerate(p)]


def _linje3(rader: Any, sammanhang: str) -> list[list[float]]:
    if isinstance(rader, str | bytes) or not isinstance(rader, Sequence) or len(rader) < 2:
        raise LappFel(f"{sammanhang} måste vara en lista med minst två punkter [E, N, z].")
    return [_punkt3(p, f"{sammanhang}[{i}]") for i, p in enumerate(rader)]


LUTNING_FORMER: tuple[str, ...] = ("punkter", "a+b", "punkt+fall", "hojdlinje_a+hojdlinje_b")


def normalisera_lutning(raw: Any, sammanhang: str = "lutning") -> dict[str, Any]:
    """Validerar och normaliserar en lutande yta (K3 `HojdYta.lutning`, K9 `markhojd.lutning`).

    Fyra former (exakt en får anges), alla i EPSG:3011 och m RH 2000:

    - `{"punkter": [[E, N, z], ...]}` (minst tre, inte på en rak linje): plan i minsta kvadrat.
    - `{"a": [E, N, z], "b": [E, N, z]}`: plan som faller (stiger) från a till b, utan tvärfall.
    - `{"punkt": [E, N, z], "fall": 0.02, "riktning_grader": 190}`: plan genom punkten; `fall`
      är lutningen (m/m, kan vara negativ) nedåt i riktningen `riktning_grader` (azimut medurs
      från norr, 0 = norrut; förval 0).
    - `{"hojdlinje_a": [[E, N, z], ...], "hojdlinje_b": [[E, N, z], ...]}`: två höjdlinjer
      (polylinjer med z per punkt); ytan interpoleras linjärt mellan dem efter avståndet till
      respektive linje.
    """
    if not isinstance(raw, Mapping):
        raise LappFel(f"{sammanhang} måste vara ett JSON-objekt.")
    nycklar = set(raw)
    former = {
        "punkter": {"punkter"},
        "a+b": {"a", "b"},
        "punkt+fall": {"punkt", "fall", "riktning_grader"},
        "hojdlinje_a+hojdlinje_b": {"hojdlinje_a", "hojdlinje_b"},
    }
    obligatoriska = {
        "punkter": {"punkter"},
        "a+b": {"a", "b"},
        "punkt+fall": {"punkt", "fall"},
        "hojdlinje_a+hojdlinje_b": {"hojdlinje_a", "hojdlinje_b"},
    }
    traffar = [f for f, ob in obligatoriska.items() if ob <= nycklar]
    if len(traffar) != 1 or not nycklar <= former[traffar[0]]:
        raise LappFel(
            f"{sammanhang} måste vara exakt en av formerna {', '.join(LUTNING_FORMER)} "
            f"(fick nycklarna {sorted(nycklar)})."
        )
    form = traffar[0]
    if form == "punkter":
        pk = raw["punkter"]
        if isinstance(pk, str | bytes) or not isinstance(pk, Sequence) or len(pk) < 3:
            raise LappFel(f"{sammanhang}.punkter måste vara minst tre punkter [E, N, z].")
        punkter = [_punkt3(p, f"{sammanhang}.punkter[{i}]") for i, p in enumerate(pk)]
        xy = np.array([[p[0], p[1]] for p in punkter], dtype=np.float64)
        if np.linalg.matrix_rank(xy - xy.mean(axis=0), tol=1e-6) < 2:
            raise LappFel(f"{sammanhang}.punkter ligger på en rak linje; ytan är inte bestämd.")
        return {"punkter": punkter}
    if form == "a+b":
        a = _punkt3(raw["a"], f"{sammanhang}.a")
        b = _punkt3(raw["b"], f"{sammanhang}.b")
        if math.hypot(b[0] - a[0], b[1] - a[1]) <= 1e-9:
            raise LappFel(f"{sammanhang}: a och b har samma läge (E, N); riktningen är obestämd.")
        return {"a": a, "b": b}
    if form == "punkt+fall":
        punkt = _punkt3(raw["punkt"], f"{sammanhang}.punkt")
        fall = _validera_andlig(raw["fall"], f"{sammanhang}.fall")
        rikt = _validera_andlig(raw.get("riktning_grader", 0.0), f"{sammanhang}.riktning_grader")
        return {"punkt": punkt, "fall": fall, "riktning_grader": rikt}
    la = _linje3(raw["hojdlinje_a"], f"{sammanhang}.hojdlinje_a")
    lb = _linje3(raw["hojdlinje_b"], f"{sammanhang}.hojdlinje_b")
    return {"hojdlinje_a": la, "hojdlinje_b": lb}


@dataclass(frozen=True)
class HojdYta:
    """En ny markyta i projektlappen: plan, lutande yta, delta mot basen eller LandXML-TIN.

    Höjden anges med exakt ett av `hojd_rh2000` (plan), `lutning` (lutande yta, se
    `normalisera_lutning`), `delta_m` (basens höjd plus delta) eller `landxml` (TIN). De tre
    senare nya fälten är additiva (R4): en yta utan `lutning`, `delta_m` och slänt läses och
    räknas exakt som tidigare (hård kant, cellcentrum i polygonen).

    Släntfälten ger en övergångszon utanför polygonen ned (eller upp) till befintlig mark:
    `slant_forhallande` n (1:n; zonens bredd W = |Δ| · n där Δ är nivåskillnaden mot basen vid
    kanten) eller `slant_bredd_m` (fast bredd), och valfritt `slant_markslag` (förval 4, gräs).
    """

    yta_id: str
    polygon: tuple[tuple[float, float], ...] = ()
    hojd_rh2000: float | None = None
    landxml: str | None = None
    koordinat_ordning: str = "NEZ"
    osakerhet_m: float = 0.05
    markslag: int | None = None
    lutning: Mapping[str, Any] | None = field(default=None, hash=False)
    delta_m: float | None = None
    slant_forhallande: float | None = None
    slant_bredd_m: float | None = None
    slant_markslag: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.yta_id, str) or not self.yta_id.strip():
            raise LappFel("HojdYta.yta_id måste vara en icke-tom sträng.")
        osak = _validera_andlig(self.osakerhet_m, "osakerhet_m", minst=0.0)
        object.__setattr__(self, "osakerhet_m", osak)

        ordning = self.koordinat_ordning.strip().upper()
        if ordning not in GILTIGA_KOORDINATORDNINGAR:
            raise LappFel(
                f"Ogiltig koordinat_ordning '{self.koordinat_ordning}'. "
                f"Giltiga: {sorted(GILTIGA_KOORDINATORDNINGAR)}."
            )
        object.__setattr__(self, "koordinat_ordning", ordning)

        if self.markslag is not None and int(self.markslag) not in GILTIGA_MARKSLAG:
            raise LappFel(f"Ogiltigt markslag i HojdYta: {self.markslag} (måste vara 0..9).")

        har_landxml = isinstance(self.landxml, str) and bool(self.landxml.strip())
        har_polygon = len(self.polygon) > 0
        if not har_landxml and not har_polygon:
            raise LappFel(f"HojdYta '{self.yta_id}' måste ange antingen polygon eller landxml.")
        if har_polygon:
            rensad = _validera_punkter(self.polygon, 3, f"HojdYta '{self.yta_id}'.polygon")
            object.__setattr__(self, "polygon", rensad)
        har_slant = (
            self.slant_forhallande is not None
            or self.slant_bredd_m is not None
            or self.slant_markslag is not None
        )
        nya = [
            n
            for n, v in (("lutning", self.lutning), ("delta_m", self.delta_m))
            if v is not None
        ]
        if har_landxml and (nya or har_slant):
            raise LappFel(
                f"HojdYta '{self.yta_id}': lutning, delta_m och slänt kan inte kombineras med "
                "landxml."
            )
        givna = [
            n
            for n, v in (
                ("hojd_rh2000", self.hojd_rh2000),
                ("lutning", self.lutning),
                ("delta_m", self.delta_m),
            )
            if v is not None
        ]
        if not har_landxml and not givna:
            raise LappFel(
                f"HojdYta '{self.yta_id}' med polygon utan landxml måste ange hojd_rh2000, "
                "lutning eller delta_m."
            )
        if not har_landxml and len(givna) > 1:
            raise LappFel(
                f"HojdYta '{self.yta_id}': ange exakt en av hojd_rh2000, lutning och delta_m "
                f"(fick {', '.join(givna)})."
            )
        if self.hojd_rh2000 is not None:
            object.__setattr__(
                self, "hojd_rh2000", _validera_andlig(self.hojd_rh2000, "hojd_rh2000")
            )
        if self.lutning is not None:
            object.__setattr__(
                self,
                "lutning",
                normalisera_lutning(self.lutning, f"HojdYta '{self.yta_id}'.lutning"),
            )
        if self.delta_m is not None:
            object.__setattr__(self, "delta_m", _validera_andlig(self.delta_m, "delta_m"))
        if self.slant_forhallande is not None and self.slant_bredd_m is not None:
            raise LappFel(
                f"HojdYta '{self.yta_id}': ange slant_forhallande eller slant_bredd_m, inte båda."
            )
        if self.slant_forhallande is not None:
            fh = _validera_andlig(self.slant_forhallande, "slant_forhallande")
            if fh <= 0.0:
                raise LappFel(f"slant_forhallande måste vara > 0 (1:n, n > 0), fick {fh}.")
            object.__setattr__(self, "slant_forhallande", fh)
        if self.slant_bredd_m is not None:
            bm = _validera_andlig(self.slant_bredd_m, "slant_bredd_m")
            if bm <= 0.0:
                raise LappFel(f"slant_bredd_m måste vara > 0, fick {bm}.")
            object.__setattr__(self, "slant_bredd_m", bm)
        if self.slant_markslag is not None:
            if self.slant_forhallande is None and self.slant_bredd_m is None:
                raise LappFel("slant_markslag kräver slant_forhallande eller slant_bredd_m.")
            if int(self.slant_markslag) not in GILTIGA_MARKSLAG:
                raise LappFel(f"Ogiltigt slant_markslag: {self.slant_markslag} (måste vara 0..9).")
            object.__setattr__(self, "slant_markslag", int(self.slant_markslag))

    @property
    def har_nya_falt(self) -> bool:
        """True om ytan använder något av R4-fälten (lutning, delta_m eller slänt)."""
        return (
            self.lutning is not None
            or self.delta_m is not None
            or self.slant_forhallande is not None
            or self.slant_bredd_m is not None
        )

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar höjdytan till JSON-kompatibelt lexikon."""
        ut: dict[str, Any] = {
            "yta_id": self.yta_id,
            "polygon": [[p[0], p[1]] for p in self.polygon],
            "hojd_rh2000": self.hojd_rh2000,
            "landxml": self.landxml,
            "koordinat_ordning": self.koordinat_ordning,
            "osakerhet_m": self.osakerhet_m,
            "markslag": self.markslag,
        }
        # R4: nya fält skrivs bara när de används, så att äldre lappar får oförändrad hash.
        for namn in ("lutning", "delta_m", "slant_forhallande", "slant_bredd_m", "slant_markslag"):
            varde = getattr(self, namn)
            if varde is not None:
                ut[namn] = varde
        return ut

    @classmethod
    def fran_dict(cls, data: Any) -> HojdYta:
        """Läser en `HojdYta` från JSON-objekt."""
        if not isinstance(data, Mapping):
            raise LappFel("HojdYta måste vara ett JSON-objekt.")
        raw_poly = data.get("polygon", [])
        if not isinstance(raw_poly, list):
            raise LappFel("HojdYta.polygon måste vara en lista.")
        poly = tuple(_validera_punkt(p, "HojdYta.polygon") for p in raw_poly)
        hojd = data.get("hojd_rh2000")
        slag = data.get("markslag")
        nya: dict[str, Any] = {}
        if data.get("lutning") is not None:
            nya["lutning"] = data["lutning"]
        for namn in ("delta_m", "slant_forhallande", "slant_bredd_m"):
            if data.get(namn) is not None:
                nya[namn] = float(data[namn])
        if data.get("slant_markslag") is not None:
            nya["slant_markslag"] = int(data["slant_markslag"])
        return cls(
            yta_id=str(data.get("yta_id", "")),
            polygon=poly,
            hojd_rh2000=float(hojd) if hojd is not None else None,
            landxml=str(data["landxml"]) if data.get("landxml") is not None else None,
            koordinat_ordning=str(data.get("koordinat_ordning", "NEZ")),
            osakerhet_m=float(data.get("osakerhet_m", 0.05)),
            markslag=int(slag) if slag is not None else None,
            **nya,
        )


@dataclass(frozen=True)
class LappByggnad:
    """En planerad byggnad i projektlappen med entréhöjd (`entre_rh2000`) och entrépunkt."""

    byggnad_id: str
    polygon: tuple[tuple[float, float], ...]
    entre_rh2000: float
    entre_punkt: tuple[float, float]
    hinderhojd_m: float = 10.0
    tak_rh2000: float | None = None
    gransdjup_m: float = 0.10

    def __post_init__(self) -> None:
        if not isinstance(self.byggnad_id, str) or not self.byggnad_id.strip():
            raise LappFel("LappByggnad.byggnad_id måste vara en icke-tom sträng.")
        rensad_poly = _validera_punkter(
            self.polygon, 3, f"LappByggnad '{self.byggnad_id}'.polygon"
        )
        entre_z = _validera_andlig(self.entre_rh2000, "entre_rh2000")
        entre_p = _validera_punkt(self.entre_punkt, f"LappByggnad '{self.byggnad_id}'.entre_punkt")
        hinder = _validera_andlig(self.hinderhojd_m, "hinderhojd_m", minst=0.0)
        grans = _validera_andlig(self.gransdjup_m, "gransdjup_m", minst=0.0)
        if grans <= 0.0:
            raise LappFel(f"gransdjup_m måste vara > 0, fick {grans}.")
        tak = (
            _validera_andlig(self.tak_rh2000, "tak_rh2000")
            if self.tak_rh2000 is not None
            else None
        )

        object.__setattr__(self, "polygon", rensad_poly)
        object.__setattr__(self, "entre_rh2000", entre_z)
        object.__setattr__(self, "entre_punkt", entre_p)
        object.__setattr__(self, "hinderhojd_m", hinder)
        object.__setattr__(self, "tak_rh2000", tak)
        object.__setattr__(self, "gransdjup_m", grans)

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar byggnaden till JSON-kompatibelt lexikon."""
        return {
            "byggnad_id": self.byggnad_id,
            "polygon": [[p[0], p[1]] for p in self.polygon],
            "entre_rh2000": self.entre_rh2000,
            "entre_punkt": [self.entre_punkt[0], self.entre_punkt[1]],
            "hinderhojd_m": self.hinderhojd_m,
            "tak_rh2000": self.tak_rh2000,
            "gransdjup_m": self.gransdjup_m,
        }

    @classmethod
    def fran_dict(cls, data: Any) -> LappByggnad:
        """Läser en `LappByggnad` från JSON-objekt."""
        if not isinstance(data, Mapping):
            raise LappFel("LappByggnad måste vara ett JSON-objekt.")
        for falt in ("byggnad_id", "polygon", "entre_rh2000", "entre_punkt"):
            if falt not in data:
                raise LappFel(f"LappByggnad saknar obligatoriskt fält '{falt}'.")
        tak = data.get("tak_rh2000")
        return cls(
            byggnad_id=str(data["byggnad_id"]),
            polygon=_validera_punkter(data["polygon"], 3, "LappByggnad.polygon"),
            entre_rh2000=float(data["entre_rh2000"]),
            entre_punkt=_validera_punkt(data["entre_punkt"], "LappByggnad.entre_punkt"),
            hinderhojd_m=float(data.get("hinderhojd_m", 10.0)),
            tak_rh2000=float(tak) if tak is not None else None,
            gransdjup_m=float(data.get("gransdjup_m", 0.10)),
        )


@dataclass(frozen=True)
class LappGata:
    """En parametrisk gata i projektlappen: mittlinje + gatusektion + valfri höjdprofil."""

    gata_id: str
    mittlinje: tuple[tuple[float, float], ...]
    sektion: str
    profil_rh2000: tuple[float, ...] | None = None
    markslag_korbana: int = 1
    markslag_gangbana: int = 1
    osakerhet_m: float = 0.02

    def __post_init__(self) -> None:
        if not isinstance(self.gata_id, str) or not self.gata_id.strip():
            raise LappFel("LappGata.gata_id måste vara en icke-tom sträng.")
        if not isinstance(self.sektion, str) or not self.sektion.strip():
            raise LappFel("LappGata.sektion måste vara en icke-tom sträng.")
        rensad = _validera_punkter(self.mittlinje, 2, f"LappGata '{self.gata_id}'.mittlinje")
        object.__setattr__(self, "mittlinje", rensad)

        if self.profil_rh2000 is not None:
            if len(self.profil_rh2000) != len(rensad):
                raise LappFel(
                    f"LappGata '{self.gata_id}'.profil_rh2000 måste ha samma längd som "
                    f"mittlinje ({len(rensad)}), fick {len(self.profil_rh2000)}."
                )
            rensad_prof = tuple(
                _validera_andlig(z, f"profil_rh2000[{i}]")
                for i, z in enumerate(self.profil_rh2000)
            )
            object.__setattr__(self, "profil_rh2000", rensad_prof)

        if int(self.markslag_korbana) not in GILTIGA_MARKSLAG:
            raise LappFel(f"Ogiltigt markslag_korbana: {self.markslag_korbana}")
        if int(self.markslag_gangbana) not in GILTIGA_MARKSLAG:
            raise LappFel(f"Ogiltigt markslag_gangbana: {self.markslag_gangbana}")
        object.__setattr__(
            self, "osakerhet_m", _validera_andlig(self.osakerhet_m, "osakerhet_m", minst=0.0)
        )

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar gatan till JSON-kompatibelt lexikon."""
        return {
            "gata_id": self.gata_id,
            "mittlinje": [[p[0], p[1]] for p in self.mittlinje],
            "sektion": self.sektion,
            "profil_rh2000": list(self.profil_rh2000) if self.profil_rh2000 is not None else None,
            "markslag_korbana": self.markslag_korbana,
            "markslag_gangbana": self.markslag_gangbana,
            "osakerhet_m": self.osakerhet_m,
        }

    @classmethod
    def fran_dict(cls, data: Any) -> LappGata:
        """Läser en `LappGata` från JSON-objekt."""
        if not isinstance(data, Mapping):
            raise LappFel("LappGata måste vara ett JSON-objekt.")
        for falt in ("gata_id", "mittlinje", "sektion"):
            if falt not in data:
                raise LappFel(f"LappGata saknar obligatoriskt fält '{falt}'.")
        raw_prof = data.get("profil_rh2000")
        prof = tuple(float(z) for z in raw_prof) if raw_prof is not None else None
        return cls(
            gata_id=str(data["gata_id"]),
            mittlinje=_validera_punkter(data["mittlinje"], 2, "LappGata.mittlinje"),
            sektion=str(data["sektion"]),
            profil_rh2000=prof,
            markslag_korbana=int(data.get("markslag_korbana", 1)),
            markslag_gangbana=int(data.get("markslag_gangbana", 1)),
            osakerhet_m=float(data.get("osakerhet_m", 0.02)),
        )


@dataclass(frozen=True)
class LappMagasin:
    """Ett fördröjningsmagasin i projektlappen med känd volym (`volym_m3`)."""

    magasin_id: str
    polygon: tuple[tuple[float, float], ...]
    volym_m3: float
    botten_rh2000: float | None = None
    markslag: int = 4
    osakerhet_m: float = 0.03

    def __post_init__(self) -> None:
        if not isinstance(self.magasin_id, str) or not self.magasin_id.strip():
            raise LappFel("LappMagasin.magasin_id måste vara en icke-tom sträng.")
        rensad = _validera_punkter(self.polygon, 3, f"LappMagasin '{self.magasin_id}'.polygon")
        vol = _validera_andlig(self.volym_m3, "volym_m3", minst=0.0)
        botten = (
            _validera_andlig(self.botten_rh2000, "botten_rh2000")
            if self.botten_rh2000 is not None
            else None
        )
        if int(self.markslag) not in GILTIGA_MARKSLAG:
            raise LappFel(f"Ogiltigt markslag i LappMagasin: {self.markslag}")
        osak = _validera_andlig(self.osakerhet_m, "osakerhet_m", minst=0.0)

        object.__setattr__(self, "polygon", rensad)
        object.__setattr__(self, "volym_m3", vol)
        object.__setattr__(self, "botten_rh2000", botten)
        object.__setattr__(self, "osakerhet_m", osak)

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar magasinet till JSON-kompatibelt lexikon."""
        return {
            "magasin_id": self.magasin_id,
            "polygon": [[p[0], p[1]] for p in self.polygon],
            "volym_m3": self.volym_m3,
            "botten_rh2000": self.botten_rh2000,
            "markslag": self.markslag,
            "osakerhet_m": self.osakerhet_m,
        }

    @classmethod
    def fran_dict(cls, data: Any) -> LappMagasin:
        """Läser ett `LappMagasin` från JSON-objekt."""
        if not isinstance(data, Mapping):
            raise LappFel("LappMagasin måste vara ett JSON-objekt.")
        for falt in ("magasin_id", "polygon", "volym_m3"):
            if falt not in data:
                raise LappFel(f"LappMagasin saknar obligatoriskt fält '{falt}'.")
        botten = data.get("botten_rh2000")
        return cls(
            magasin_id=str(data["magasin_id"]),
            polygon=_validera_punkter(data["polygon"], 3, "LappMagasin.polygon"),
            volym_m3=float(data["volym_m3"]),
            botten_rh2000=float(botten) if botten is not None else None,
            markslag=int(data.get("markslag", 4)),
            osakerhet_m=float(data.get("osakerhet_m", 0.03)),
        )


@dataclass(frozen=True)
class LappMarkslag:
    """Ändring av markslagskod (0..9) inom en polygon i projektlappen."""

    omrade_id: str
    polygon: tuple[tuple[float, float], ...]
    markslag: int

    def __post_init__(self) -> None:
        if not isinstance(self.omrade_id, str) or not self.omrade_id.strip():
            raise LappFel("LappMarkslag.omrade_id måste vara en icke-tom sträng.")
        rensad = _validera_punkter(self.polygon, 3, f"LappMarkslag '{self.omrade_id}'.polygon")
        if int(self.markslag) not in GILTIGA_MARKSLAG:
            raise LappFel(f"Ogiltigt markslag: {self.markslag} (måste vara 0..9).")
        object.__setattr__(self, "polygon", rensad)
        object.__setattr__(self, "markslag", int(self.markslag))

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar markslagsändringen till JSON-kompatibelt lexikon."""
        return {
            "omrade_id": self.omrade_id,
            "polygon": [[p[0], p[1]] for p in self.polygon],
            "markslag": self.markslag,
        }

    @classmethod
    def fran_dict(cls, data: Any) -> LappMarkslag:
        """Läser en `LappMarkslag` från JSON-objekt."""
        if not isinstance(data, Mapping):
            raise LappFel("LappMarkslag måste vara ett JSON-objekt.")
        for falt in ("omrade_id", "polygon", "markslag"):
            if falt not in data:
                raise LappFel(f"LappMarkslag saknar obligatoriskt fält '{falt}'.")
        return cls(
            omrade_id=str(data["omrade_id"]),
            polygon=_validera_punkter(data["polygon"], 3, "LappMarkslag.polygon"),
            markslag=int(data["markslag"]),
        )


def _harled_operationer(
    hojdytor: Sequence[HojdYta],
    byggnader: Sequence[LappByggnad],
    gator: Sequence[LappGata],
    magasin: Sequence[LappMagasin],
    markslag_andringar: Sequence[LappMarkslag],
) -> tuple[LappOperation, ...]:
    ops: list[LappOperation] = []
    if hojdytor:
        ops.append(LappOperation(typ="hojdyta", lager="hojdytor"))
    if byggnader:
        ops.append(LappOperation(typ="byggnad", lager="byggnader", entre_rh2000="entre_rh2000"))
    if gator:
        sektion_namn = gator[0].sektion
        ops.append(LappOperation(typ="gata", lager="gator", sektion=sektion_namn))
    if magasin:
        ops.append(LappOperation(typ="magasin", lager="magasin", volym_m3="volym_m3"))
    if markslag_andringar:
        ops.append(LappOperation(typ="markslag", lager="markslag"))
    return tuple(ops)


@dataclass(frozen=True)
class ProjektLapp:
    """Projektlapp enligt kontrakt K3/v1 (`docs/DATAKONTRAKT.md`)."""

    lapp_id: str
    bas_recept: tuple[str, ...]
    operationer: tuple[LappOperation, ...] = field(default_factory=tuple)
    hojdytor: tuple[HojdYta, ...] = field(default_factory=tuple)
    byggnader: tuple[LappByggnad, ...] = field(default_factory=tuple)
    gator: tuple[LappGata, ...] = field(default_factory=tuple)
    magasin: tuple[LappMagasin, ...] = field(default_factory=tuple)
    markslag_andringar: tuple[LappMarkslag, ...] = field(default_factory=tuple)
    kontrakt: str = KONTRAKT_K3
    beskrivning: str = ""

    def __post_init__(self) -> None:
        if self.kontrakt != KONTRAKT_K3:
            raise LappFel(
                f"Ogiltigt lappkontrakt '{self.kontrakt}', förväntade '{KONTRAKT_K3}'."
            )
        if not isinstance(self.lapp_id, str) or not self.lapp_id.strip():
            raise LappFel("ProjektLapp.lapp_id måste vara en icke-tom sträng.")

        recept_lista = tuple(str(r) for r in self.bas_recept)
        if not recept_lista or any(not r.strip() for r in recept_lista):
            raise LappFel("ProjektLapp.bas_recept måste innehålla minst en icke-tom sha256.")
        object.__setattr__(self, "bas_recept", recept_lista)

        ops = tuple(self.operationer)
        if not ops:
            ops = _harled_operationer(
                self.hojdytor,
                self.byggnader,
                self.gator,
                self.magasin,
                self.markslag_andringar,
            )
        object.__setattr__(self, "operationer", ops)
        object.__setattr__(self, "hojdytor", tuple(self.hojdytor))
        object.__setattr__(self, "byggnader", tuple(self.byggnader))
        object.__setattr__(self, "gator", tuple(self.gator))
        object.__setattr__(self, "magasin", tuple(self.magasin))
        object.__setattr__(self, "markslag_andringar", tuple(self.markslag_andringar))

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar projektlappen till ett K3/v1-lexikon."""
        ut: dict[str, Any] = {
            "kontrakt": self.kontrakt,
            "lapp_id": self.lapp_id,
            "bas_recept": list(self.bas_recept),
            "operationer": [op.till_dict() for op in self.operationer],
        }
        if self.beskrivning:
            ut["beskrivning"] = self.beskrivning
        if self.hojdytor:
            ut["hojdytor"] = [y.till_dict() for y in self.hojdytor]
        if self.byggnader:
            ut["byggnader"] = [b.till_dict() for b in self.byggnader]
        if self.gator:
            ut["gator"] = [g.till_dict() for g in self.gator]
        if self.magasin:
            ut["magasin"] = [m.till_dict() for m in self.magasin]
        if self.markslag_andringar:
            ut["markslag"] = [ms.till_dict() for ms in self.markslag_andringar]
        return ut

    def kanonisk_json(self) -> str:
        """Deterministisk kanonisk JSON-sträng för kontrollsumma."""
        return json.dumps(
            self.till_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )

    def lapp_sha256(self) -> str:
        """Deterministisk SHA-256 över projektlappens kanoniska innehåll."""
        return sha256_bytes(self.kanonisk_json().encode("utf-8"))

    @classmethod
    def fran_dict(cls, data: Any) -> ProjektLapp:
        """Läser och validerar en `ProjektLapp` från ett deserialiserat K3/v1-objekt."""
        if not isinstance(data, Mapping):
            raise LappFel("Projektlapp måste vara ett JSON-objekt.")
        obligatoriska = ("kontrakt", "lapp_id", "bas_recept", "operationer")
        saknade = [f for f in obligatoriska if f not in data]
        if saknade:
            raise LappFel(
                f"Projektlapp saknar obligatoriska K3/v1-fält: {', '.join(saknade)}."
            )
        if data["kontrakt"] != KONTRAKT_K3:
            raise LappFel(
                f"Ogiltigt lappkontrakt '{data['kontrakt']}', förväntade '{KONTRAKT_K3}'."
            )

        raw_recept = data["bas_recept"]
        if not isinstance(raw_recept, list) or not all(isinstance(r, str) for r in raw_recept):
            raise LappFel("ProjektLapp.bas_recept måste vara en lista av strängar.")

        raw_ops = data["operationer"]
        if not isinstance(raw_ops, list):
            raise LappFel("ProjektLapp.operationer måste vara en lista.")
        ops = tuple(LappOperation.fran_dict(op) for op in raw_ops)

        hojdytor = tuple(HojdYta.fran_dict(item) for item in data.get("hojdytor", ()))
        byggnader = tuple(LappByggnad.fran_dict(item) for item in data.get("byggnader", ()))
        gator = tuple(LappGata.fran_dict(item) for item in data.get("gator", ()))
        magasin = tuple(LappMagasin.fran_dict(item) for item in data.get("magasin", ()))
        markslag_raw = data.get("markslag", data.get("markslag_andringar", ()))
        markslag_andringar = tuple(LappMarkslag.fran_dict(item) for item in markslag_raw)

        return cls(
            kontrakt=str(data["kontrakt"]),
            lapp_id=str(data["lapp_id"]),
            bas_recept=tuple(str(r) for r in raw_recept),
            operationer=ops,
            hojdytor=hojdytor,
            byggnader=byggnader,
            gator=gator,
            magasin=magasin,
            markslag_andringar=markslag_andringar,
            beskrivning=str(data.get("beskrivning", "")),
        )


def skriv_lapp_k3(lapp: ProjektLapp, sokvag: str | Path) -> Path:
    """Skriver en validerad projektlapp (`lapp.json`) enligt kontrakt K3/v1."""
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(lapp.till_dict(), indent=2, ensure_ascii=False) + "\n"
    mal.write_text(text, encoding="utf-8")
    return mal


def las_lapp_k3(sokvag: str | Path) -> ProjektLapp:
    """Läser och validerar en projektlapp (`lapp.json`) enligt kontrakt K3/v1."""
    fil = Path(sokvag)
    try:
        text = fil.read_text(encoding="utf-8")
    except OSError as exc:
        raise LappFel(f"Kunde inte läsa lappfilen {fil}: {exc}") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LappFel(f"Ogiltig JSON i lappfilen {fil}: {exc}") from exc

    return ProjektLapp.fran_dict(data)


def las_gatusektioner_fran_konfig(
    konfig_fil: str | Path = "config.toml",
    *,
    lokal_fil: str | Path | None = None,
) -> dict[str, Gatusektion]:
    """Läser alla `[sektioner.<namn>]` ur `config.toml` och validerar dem som `Gatusektion`."""
    try:
        konfig = las_konfig(bas_fil=konfig_fil, lokal_fil=lokal_fil)
    except KonfigFel as exc:
        raise LappFel(f"Kunde inte läsa gatusektioner ur {konfig_fil}: {exc}") from exc

    raw_sektioner = konfig.get("sektioner")
    if not isinstance(raw_sektioner, Mapping) or not raw_sektioner:
        raise LappFel(f"Konfigurationsfilen {konfig_fil} saknar tabellen [sektioner].")

    obligatoriska = (
        "total_bredd_m",
        "korbana_bredd_m",
        "gangbana_bredd_m",
        "kantsten_hojd_m",
        "tvarfall_korbana_andel",
        "tvarfall_gangbana_andel",
        "brunnsavstand_m",
    )
    resultat: dict[str, Gatusektion] = {}
    for namn, spec in raw_sektioner.items():
        if not isinstance(spec, Mapping):
            raise LappFel(f"Gatusektion '{namn}' måste vara en tabell i {konfig_fil}.")
        saknade = [f for f in obligatoriska if f not in spec]
        if saknade:
            raise LappFel(
                f"Gatusektion '{namn}' saknar obligatoriska fält: {', '.join(saknade)}."
            )
        resultat[str(namn)] = Gatusektion(
            namn=str(namn),
            total_bredd_m=float(spec["total_bredd_m"]),
            korbana_bredd_m=float(spec["korbana_bredd_m"]),
            gangbana_bredd_m=float(spec["gangbana_bredd_m"]),
            kantsten_hojd_m=float(spec["kantsten_hojd_m"]),
            tvarfall_korbana_andel=float(spec["tvarfall_korbana_andel"]),
            tvarfall_gangbana_andel=float(spec["tvarfall_gangbana_andel"]),
            brunnsavstand_m=float(spec["brunnsavstand_m"]),
        )
    return resultat
