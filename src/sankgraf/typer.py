"""Datatyper och K2/K6/K0-I/O för sänkgrafmodulen (T02)."""

from __future__ import annotations

import bisect
import importlib
import importlib.util
import json
import math
import sqlite3
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost, skapa_manifest, skriv_manifest
from testsvit.k1_io import (
    EPSG_SWEREF99_1800,
    PIXELSTORLEK_M,
    WKT_EPSG_3011,
    WKT_EPSG_4326,
    BrytlinjePost,
    GeoTransformInfo,
    OppningPost,
    las_gpkg_tabell,
    skriv_geotiff,
)

if TYPE_CHECKING:
    from sankgraf.takvatten import TakvattenFordelning

KONTRAKT_K2 = "K2/v1"
KONTRAKT_K2_V2 = "K2/v2"
"""K2/v2 (2026-11-22): som v1 men med slutsänkor (rotnod utan överfall, `typ = "slutsanka"`).

En graf utan slutsänkor skrivs som K2/v1, byte för byte som förut; en graf med minst en
slutsänka skrivs som K2/v2. Läsaren tar båda (`KONTRAKT_K2_LASBARA`)."""
KONTRAKT_K2_LASBARA = (KONTRAKT_K2, KONTRAKT_K2_V2)

TYP_SANKA = "sanka"
TYP_SLUTSANKA = "slutsanka"
SLUTSANKA_VARNING = (
    "Slutet område: vattnet har ingen väg ut i modellen; i verkligheten avgör brunnar, portar "
    "och trösklar."
)
"""Varningen som lösarna lämnar när resultatet innehåller en slutsänka (se K2/v2)."""

HAV_NOD_ID = -1
BYGGNAD_NOD_ID = -2

MARKSLAG_HARDGJORT = 1
MARKSLAG_BYGGNAD = 9

HAR_PYARROW: bool = importlib.util.find_spec("pyarrow") is not None

# Classic TIFF-konstanter för int64 GeoTIFF (K2 cell_till_nod.tif)
_TIFF_TYPE_SHORT = 3
_TIFF_TYPE_LONG = 4
_TIFF_TYPE_DOUBLE = 12

_TAG_IMAGE_WIDTH = 256
_TAG_IMAGE_LENGTH = 257
_TAG_BITS_PER_SAMPLE = 258
_TAG_COMPRESSION = 259
_TAG_PHOTOMETRIC = 262
_TAG_STRIP_OFFSETS = 273
_TAG_SAMPLES_PER_PIXEL = 277
_TAG_ROWS_PER_STRIP = 278
_TAG_STRIP_BYTE_COUNTS = 279
_TAG_SAMPLE_FORMAT = 339
_TAG_MODEL_PIXEL_SCALE = 33550
_TAG_MODEL_TIEPOINT = 33922
_TAG_GEO_KEY_DIRECTORY = 34735


class SankGrafFel(Exception):
    """Fel vid konstruktion, filtrering, fyllning eller I/O av sänkgraf."""


class InstangtOmradeFel(SankGrafFel):
    """Ett instängt markområde som inte kan bli en slutsänka.

    Sedan K2/v2 blir ett område som helt omges av byggnader en slutsänka (se
    `SankNod.typ`). Felet finns kvar för området som (delvis) omges av celler utan höjd
    (NaN i `mark.tif`): där är hindrets nivå okänd och vattnet kan lika gärna rinna ut,
    så modellen gissar inte. ``antal_celler`` är områdets storlek, ``rad``/``kol`` en cell i det.
    """

    def __init__(self, antal_celler: int, rad: int, kol: int) -> None:
        self.antal_celler = antal_celler
        self.rad = rad
        self.kol = kol
        super().__init__(
            f"Instängt område: {antal_celler} markceller (t.ex. rad {rad}, kolumn {kol}) "
            "saknar utloppssadel mot randen och omges delvis av celler utan höjd."
        )


@dataclass(frozen=True)
class VolymPunkt:
    """En punkt `(z, v_m3)` på en sänknods volymkurva (RH 2000, m³)."""

    z: float
    v_m3: float


@dataclass(frozen=True)
class YtPunkt:
    """En punkt `(z, a_m2)` på en sänknods ytkurva (RH 2000, m²)."""

    z: float
    a_m2: float


@dataclass(frozen=True)
class SankNod:
    """En nod i sänkgrafen enligt kontrakt K2 (`docs/DATAKONTRAKT.md`).

    **Slutsänka** (`typ == "slutsanka"`, K2/v2): en rotnod vars område helt omges av
    byggnader. Den har inget överfall i terrängen (`overfall_rh2000` är null i filen) men ett
    hinder: `hinder_rh2000`, den lägsta hindercellen (byggnadens höjd i `mark.tif`) som gränsar
    till området. Volymkurvan slutar vid hindret. I Python bär noden `overfall_rh2000 =
    hinder_rh2000`, så att alla lösare räknar vatten över hindret som utflöde ur modellen
    (`ut_till_id` är None); lösarna flaggar då att hindret nåtts.
    """

    nod_id: int
    forald_id: int | None
    barn_id: tuple[int, ...]
    overfall_rh2000: float
    overfall_x: float
    overfall_y: float
    ut_till_id: int | None
    volymkurva: tuple[VolymPunkt, ...]
    ytkurva: tuple[YtPunkt, ...]
    avrinningsyta_m2: float
    persistens_m: float
    osakerhet_m: float
    botten_rh2000: float
    cell_antal: int
    typ: str = TYP_SANKA
    hinder_rh2000: float | None = None

    def __post_init__(self) -> None:
        if self.typ not in (TYP_SANKA, TYP_SLUTSANKA):
            raise SankGrafFel(f"Nod {self.nod_id}: okänd nodtyp {self.typ!r}.")
        if self.typ == TYP_SLUTSANKA:
            if self.forald_id is not None or self.ut_till_id is not None:
                raise SankGrafFel(f"Slutsänkan {self.nod_id} måste vara en rotnod utan utlopp.")
            if self.hinder_rh2000 is None or not math.isfinite(self.hinder_rh2000):
                raise SankGrafFel(f"Slutsänkan {self.nod_id} saknar ändligt hinder_rh2000.")
        elif self.hinder_rh2000 is not None:
            raise SankGrafFel(f"Nod {self.nod_id}: hinder_rh2000 gäller bara slutsänkor.")

    @property
    def ar_slutsanka(self) -> bool:
        """True om noden är en slutsänka (K2/v2): rotnod utan överfall i terrängen."""
        return self.typ == TYP_SLUTSANKA

    @property
    def ar_lov(self) -> bool:
        """True om noden är en lövsänka (saknar barnnoder)."""
        return len(self.barn_id) == 0

    @property
    def max_volym_m3(self) -> float:
        """Maximal lagringsvolym (m³) upp till `overfall_rh2000`."""
        return self.volymkurva[-1].v_m3 if self.volymkurva else 0.0

    def volym_vid_niva(self, z_rh2000: float) -> float:
        """Beräknar exakt lagrad volym (m³) vid vattennivån `z_rh2000`."""
        if not self.volymkurva:
            return 0.0
        z = float(z_rh2000)
        if z <= self.volymkurva[0].z:
            return 0.0
        if z >= self.volymkurva[-1].z:
            return self.volymkurva[-1].v_m3

        z_varden = [p.z for p in self.volymkurva]
        idx = bisect.bisect_right(z_varden, z)
        if idx <= 0:
            return 0.0
        if idx >= len(self.volymkurva):
            return self.volymkurva[-1].v_m3
        p0 = self.volymkurva[idx - 1]
        p1 = self.volymkurva[idx]
        dz = p1.z - p0.z
        if dz <= 0.0:
            return p1.v_m3
        andel = (z - p0.z) / dz
        return p0.v_m3 + andel * (p1.v_m3 - p0.v_m3)

    def niva_vid_volym(self, v_m3: float) -> float:
        """Beräknar exakt vattennivå (RH 2000) för en given lagrad volym `v_m3`."""
        if not self.volymkurva:
            return self.botten_rh2000
        v = float(v_m3)
        if v <= 0.0:
            return self.volymkurva[0].z
        if v >= self.volymkurva[-1].v_m3:
            return self.volymkurva[-1].z

        v_varden = [p.v_m3 for p in self.volymkurva]
        idx = bisect.bisect_right(v_varden, v)
        if idx <= 0:
            return self.volymkurva[0].z
        if idx >= len(self.volymkurva):
            return self.volymkurva[-1].z
        p0 = self.volymkurva[idx - 1]
        p1 = self.volymkurva[idx]
        dv = p1.v_m3 - p0.v_m3
        if dv <= 0.0:
            return p1.z
        andel = (v - p0.v_m3) / dv
        return p0.z + andel * (p1.z - p0.z)

    def area_vid_niva(self, z_rh2000: float) -> float:
        """Returnerar vattenytans area (m²) vid vattennivån `z_rh2000`."""
        if not self.ytkurva:
            return 0.0
        z = float(z_rh2000)
        if z < self.ytkurva[0].z:
            return 0.0
        if z >= self.ytkurva[-1].z:
            return self.ytkurva[-1].a_m2

        z_varden = [p.z for p in self.ytkurva]
        idx = bisect.bisect_right(z_varden, z) - 1
        return self.ytkurva[max(0, idx)].a_m2

    def till_k2_dict(self, *, v2: bool = False) -> dict[str, Any]:
        """Serialiserar noden till K2-fälten (plus botten/cell_antal för inspekterbarhet).

        Med `v2` (K2/v2) får noden `typ` och `hinder_rh2000`, och en slutsänka
        `overfall_rh2000 = None`. Utan `v2` är fälten exakt K2/v1 (slutsänka ger fel).
        """
        if self.ar_slutsanka and not v2:
            raise SankGrafFel(f"Slutsänkan {self.nod_id} kan inte skrivas som K2/v1.")
        d: dict[str, Any] = {
            "nod_id": int(self.nod_id),
            "forald_id": int(self.forald_id) if self.forald_id is not None else None,
            "barn_id": [int(b) for b in self.barn_id],
            "overfall_rh2000": None if self.ar_slutsanka else float(self.overfall_rh2000),
            "overfall_x": float(self.overfall_x),
            "overfall_y": float(self.overfall_y),
            "ut_till_id": int(self.ut_till_id) if self.ut_till_id is not None else None,
            "volymkurva": [{"z": float(p.z), "v_m3": float(p.v_m3)} for p in self.volymkurva],
            "ytkurva": [{"z": float(p.z), "a_m2": float(p.a_m2)} for p in self.ytkurva],
            "avrinningsyta_m2": float(self.avrinningsyta_m2),
            "persistens_m": float(self.persistens_m),
            "osakerhet_m": float(self.osakerhet_m),
            "botten_rh2000": float(self.botten_rh2000),
            "cell_antal": int(self.cell_antal),
        }
        if v2:
            d["typ"] = self.typ
            d["hinder_rh2000"] = (
                float(self.hinder_rh2000) if self.hinder_rh2000 is not None else None
            )
        return d


@dataclass(frozen=True)
class FlodesVag:
    """En flödesväg från en cellgrupp till sin mottagande sänknod eller randutlopp."""

    id: int
    nod_id: int
    koordinater: tuple[tuple[float, float], ...]
    langd_m: float
    lutning: float
    gangtid_s: float
    referensdjup_m: float


@dataclass
class SankGraf:
    """Komplett sänkgraf enligt K2/v1 med rutnät, noder, flödesvägar och terrängreferens."""

    noder: dict[int, SankNod]
    cell_till_nod: NDArray[np.int64]
    flodesvagar: list[FlodesVag]
    geo: GeoTransformInfo
    filtrerad: bool
    persistens_faktor: float | None
    mark_z: NDArray[np.float32]
    markslag: NDArray[np.uint8]
    ut_lov_id: dict[int, int | None] = field(default_factory=dict)
    overfall_celler: dict[int, tuple[tuple[int, int], ...]] = field(default_factory=dict)
    takvatten: TakvattenFordelning | None = None
    """Takvattnets fördelning (`sankgraf.takvatten`, Joels beslut 2026-11-22), eller None om
    regn på tak inte räknas. Skrivs inte i K2: lösarna bygger den ur rastret och byggnaderna."""

    def lovnoder(self) -> list[SankNod]:
        """Returnerar alla lövnoder i deterministisk `nod_id`-ordning."""
        return [self.noder[nid] for nid in sorted(self.noder) if self.noder[nid].ar_lov]

    def rotnoder(self) -> list[SankNod]:
        """Returnerar alla rotnoder (`forald_id is None`) i `nod_id`-ordning."""
        return [
            self.noder[nid]
            for nid in sorted(self.noder)
            if self.noder[nid].forald_id is None
        ]

    def lov_under_nod(self, nod_id: int) -> list[int]:
        """Returnerar alla lövnoders ID i delträdet under `nod_id`."""
        if nod_id not in self.noder:
            raise SankGrafFel(f"Okänt nod_id: {nod_id}")
        resultat: list[int] = []
        stack = [nod_id]
        while stack:
            aktuell = stack.pop()
            nod = self.noder[aktuell]
            if nod.ar_lov:
                resultat.append(aktuell)
            else:
                stack.extend(reversed(nod.barn_id))
        return resultat

    def total_avrinningsyta_m2(self, nod_id: int) -> float:
        """Summan av avrinningsytor för alla löv under `nod_id`."""
        return sum(self.noder[lid].avrinningsyta_m2 for lid in self.lov_under_nod(nod_id))

    def slutsankor(self) -> list[SankNod]:
        """Slutsänkorna (K2/v2) i `nod_id`-ordning."""
        return [self.noder[nid] for nid in sorted(self.noder) if self.noder[nid].ar_slutsanka]

    @property
    def har_slutsankor(self) -> bool:
        return any(n.ar_slutsanka for n in self.noder.values())

    @property
    def kontrakt(self) -> str:
        """K2-versionen grafen skrivs som: v1 utan slutsänkor, annars v2."""
        return KONTRAKT_K2_V2 if self.har_slutsankor else KONTRAKT_K2

    def slutsanka_mask(self) -> NDArray[np.bool_]:
        """Celler som hör till en slutsänka (deras löv ligger under en slutsänkas rot)."""
        lov = [lid for s in self.slutsankor() for lid in self.lov_under_nod(s.nod_id)]
        if not lov:
            return np.zeros(self.cell_till_nod.shape, dtype=np.bool_)
        return np.asarray(np.isin(self.cell_till_nod, np.asarray(lov, dtype=np.int64)))


@dataclass(frozen=True)
class SlutsankaResultat:
    """Hur en slutsänka fylldes: nivå, djup och om vattnet nådde hindret."""

    nod_id: int
    cell_antal: int
    area_m2: float
    hinder_rh2000: float
    niva_rh2000: float
    max_djup_m: float
    volym_m3: float
    over_hinder_m3: float

    @property
    def nadde_hinder(self) -> bool:
        return self.over_hinder_m3 > 0.0

    def till_dict(self) -> dict[str, Any]:
        return {
            "nod_id": self.nod_id,
            "cell_antal": self.cell_antal,
            "area_m2": self.area_m2,
            "hinder_rh2000": self.hinder_rh2000,
            "niva_rh2000": self.niva_rh2000,
            "max_djup_m": self.max_djup_m,
            "volym_m3": self.volym_m3,
            "over_hinder_m3": self.over_hinder_m3,
            "nadde_hinder": self.nadde_hinder,
        }


def slutsanka_varningar(slutsankor: Sequence[SlutsankaResultat]) -> tuple[str, ...]:
    """Varningstexter för lösarnas utdata (tom om inga slutsänkor finns)."""
    if not slutsankor:
        return ()
    ut = [SLUTSANKA_VARNING]
    nadda = [s for s in slutsankor if s.nadde_hinder]
    if nadda:
        m3 = sum(s.over_hinder_m3 for s in nadda)
        ut.append(
            f"Vattnet nådde hindret (lägsta byggnaden runt området) i {len(nadda)} av "
            f"{len(slutsankor)} slutna områden; {m3:.1f} m³ räknas som utflöde över hindret, "
            "vilket modellen inte beskriver."
        )
    return tuple(ut)


@dataclass(frozen=True)
class StatiskFyllningResultat:
    """Resultat från statisk Fill–Spill–Merge-fyllning av en sänkgraf."""

    djup_m: NDArray[np.float32]
    vattenniva_rh2000: NDArray[np.float32]
    nod_niva_rh2000: dict[int, float]
    nod_volym_m3: dict[int, float]
    in_m3: float
    lagrat_m3: float
    ut_m3: float
    volymfel_rel: float
    utflodes_celler: list[tuple[int, int]]
    slutsankor: tuple[SlutsankaResultat, ...] = ()
    slutsanka_mask: NDArray[np.bool_] | None = None
    regn_mark_m3: float = 0.0
    """Regn på markceller (aktiva celler). `in_m3` = detta + takvatten till mark och ut ur rutan."""
    regn_tak_m3: float = 0.0
    """Regn på byggnadsceller. Med takvatten av ingår det inte i `in_m3` (redovisas ändå)."""
    takvatten: bool = False
    takvatten_till_mark_m3: float = 0.0
    takvatten_ut_rand_m3: float = 0.0
    takvatten_till_ledning_m3: float = 0.0
    avtappat_m3: float = 0.0
    """Avtappning till ledningsnätet (`avtappning_mm_h` × varaktighet, per löv högst dess
    direkta tillrinning)."""

    def volymbalans(self) -> dict[str, float | bool]:
        """Regn på hela rutan (mark + tak) = lagrat + utflöde + avtappning + tak till ledning
        (+ tak som inte räknas när takvattnet är av). Infiltration ingår i nettoregnet."""
        return volymbalans_dict(
            regn_mark_m3=self.regn_mark_m3,
            regn_tak_m3=self.regn_tak_m3,
            takvatten=self.takvatten,
            lagrat_m3=self.lagrat_m3,
            ut_m3=self.ut_m3,
            infiltrerat_m3=0.0,
            avtappat_m3=self.avtappat_m3,
            tak_ledning_m3=self.takvatten_till_ledning_m3,
        )


def volymbalans_dict(
    *,
    regn_mark_m3: float,
    regn_tak_m3: float,
    takvatten: bool,
    lagrat_m3: float,
    ut_m3: float,
    infiltrerat_m3: float,
    avtappat_m3: float,
    tak_ledning_m3: float,
) -> dict[str, float | bool]:
    """Gemensam volymbalans för vattnets lösare (`docs/VATTEN.md`).

    `regn_totalt_m3` = regn på mark + tak. Ut ur balansen: lagrat, utflöde (över rutkant eller
    slutsänkans hinder), infiltration, avtappning, takvatten till ledningsnät och, med takvatten
    av, regnet på taken (räknas inte). `volymfel_rel` = |regn − summan| / regn.
    """
    regn = float(regn_mark_m3) + float(regn_tak_m3)
    bortsett = 0.0 if takvatten else float(regn_tak_m3)
    summa = (
        float(lagrat_m3) + float(ut_m3) + float(infiltrerat_m3) + float(avtappat_m3)
        + float(tak_ledning_m3) + bortsett
    )
    return {
        "regn_totalt_m3": regn,
        "regn_mark_m3": float(regn_mark_m3),
        "regn_tak_m3": float(regn_tak_m3),
        "takvatten": bool(takvatten),
        "tak_ej_raknat_m3": bortsett,
        "lagrat_m3": float(lagrat_m3),
        "utflode_m3": float(ut_m3),
        "infiltrerat_m3": float(infiltrerat_m3),
        "avtappat_m3": float(avtappat_m3),
        "tak_till_ledning_m3": float(tak_ledning_m3),
        "volymfel_rel": abs(regn - summa) / regn if regn > 0.0 else 0.0,
    }


def avkoda_gp_wkb(blob: bytes | bytearray) -> tuple[tuple[float, float], ...]:
    """Avkodar GeoPackage Binary WKB (Point eller LineString) till (x, y)-koordinater."""
    data = bytes(blob)
    if len(data) < 8 or data[:2] != b"GP":
        raise SankGrafFel("Ogiltig GeoPackage-geometriheader (saknar 'GP').")

    flags = data[3]
    envelope_ind = (flags >> 1) & 0x07
    envelope_langder = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
    if envelope_ind not in envelope_langder:
        raise SankGrafFel(f"Ogiltig GeoPackage-envelope-indikator: {envelope_ind}")

    offset = 8 + envelope_langder[envelope_ind]
    if offset + 5 > len(data):
        raise SankGrafFel("Trunkerad WKB-geometri i GeoPackage.")

    byte_order = data[offset]
    endian = "<" if byte_order == 1 else ">"
    geom_type = int(struct.unpack(f"{endian}I", data[offset + 1 : offset + 5])[0])
    pos = offset + 5

    # Stödjer både standard WKB och ISO WKB (Z/M-varianter)
    if geom_type & 0x80000000:
        has_z = True
        has_m = bool(geom_type & 0x40000000)
        bas_typ = geom_type & 0xFF
    else:
        bas_typ = geom_type % 1000
        has_z = (geom_type // 1000) in (1, 3)
        has_m = (geom_type // 1000) in (2, 3)

    dim = 2 + int(has_z) + int(has_m)
    punkt_bytes = dim * 8

    if bas_typ == 1:  # Point
        if pos + punkt_bytes > len(data):
            raise SankGrafFel("Trunkerad WKB Point.")
        x, y = struct.unpack(f"{endian}dd", data[pos : pos + 16])
        return ((float(x), float(y)),)

    if bas_typ == 2:  # LineString
        if pos + 4 > len(data):
            raise SankGrafFel("Trunkerad WKB LineString-längd.")
        n_punkter = int(struct.unpack(f"{endian}I", data[pos : pos + 4])[0])
        pos += 4
        if pos + n_punkter * punkt_bytes > len(data):
            raise SankGrafFel("Trunkerad WKB LineString-koordinater.")
        punkter: list[tuple[float, float]] = []
        for _ in range(n_punkter):
            x, y = struct.unpack(f"{endian}dd", data[pos : pos + 16])
            punkter.append((float(x), float(y)))
            pos += punkt_bytes
        return tuple(punkter)

    raise SankGrafFel(f"Ostödd WKB-geometrityp: {geom_type} (bas {bas_typ}).")


def las_brytlinjer_fran_gpkg(sokvag: str | Path) -> list[BrytlinjePost]:
    """Läser `brytlinjer.gpkg` (lager `brytlinjer`) till en lista av `BrytlinjePost`."""
    _, rader = las_gpkg_tabell(sokvag, "brytlinjer")
    poster: list[BrytlinjePost] = []
    for rad in rader:
        geom = rad.get("geom")
        if not isinstance(geom, bytes | bytearray):
            raise SankGrafFel(f"Brytlinjepost saknar giltig geometri i {sokvag}.")
        koord = avkoda_gp_wkb(geom)
        poster.append(
            BrytlinjePost(
                typ=str(rad["typ"]),
                hojd_m=float(rad["hojd_m"]),
                koordinater=koord,
            )
        )
    return poster


def las_oppningar_fran_gpkg(sokvag: str | Path) -> list[OppningPost]:
    """Läser `oppningar.gpkg` (lager `oppningar`) till en lista av `OppningPost`."""
    _, rader = las_gpkg_tabell(sokvag, "oppningar")
    poster: list[OppningPost] = []
    for rad in rader:
        geom = rad.get("geom")
        if not isinstance(geom, bytes | bytearray):
            raise SankGrafFel(f"Öppningspost saknar giltig geometri i {sokvag}.")
        koord = avkoda_gp_wkb(geom)
        poster.append(
            OppningPost(
                typ=str(rad["typ"]),
                bredd_m=float(rad["bredd_m"]),
                botten_rh2000=float(rad["botten_rh2000"]),
                kalla=str(rad["kalla"]),
                koordinater=koord,
            )
        )
    return poster


def skriv_geotiff_int64(
    sokvag: str | Path,
    raster: NDArray[np.int64],
    *,
    x_min: float,
    y_max: float,
    pixelstorlek: float = PIXELSTORLEK_M,
    epsg: int = EPSG_SWEREF99_1800,
) -> Path:
    """Skriver ett enkelbands int64 GeoTIFF i Classic TIFF-format (K2 `cell_till_nod.tif`)."""
    if raster.ndim != 2:
        raise SankGrafFel(f"Raster måste vara 2D, fick {raster.ndim}D.")
    hojd, bredd = raster.shape
    if hojd <= 0 or bredd <= 0:
        raise SankGrafFel(f"Raster måste ha positiv storlek, fick {hojd}x{bredd}.")
    if not math.isfinite(pixelstorlek) or pixelstorlek <= 0.0:
        raise SankGrafFel(f"Ogiltig pixelstorlek: {pixelstorlek}")

    arr = np.ascontiguousarray(raster, dtype="<i8")
    pixel_bytes = arr.tobytes(order="C")
    strip_byte_count = len(pixel_bytes)

    pixel_scale_bytes = struct.pack("<3d", float(pixelstorlek), float(pixelstorlek), 0.0)
    tiepoint_bytes = struct.pack("<6d", 0.0, 0.0, 0.0, float(x_min), float(y_max), 0.0)
    geokeys = (
        1, 1, 0, 3,
        1024, 0, 1, 1,
        1025, 0, 1, 1,
        3072, 0, 1, int(epsg),
    )
    geokey_bytes = struct.pack(f"<{len(geokeys)}H", *geokeys)

    ifd_offset = 8
    poster_utan_offset = [
        (_TAG_IMAGE_WIDTH, _TIFF_TYPE_LONG, 1, bredd),
        (_TAG_IMAGE_LENGTH, _TIFF_TYPE_LONG, 1, hojd),
        (_TAG_BITS_PER_SAMPLE, _TIFF_TYPE_SHORT, 1, 64),
        (_TAG_COMPRESSION, _TIFF_TYPE_SHORT, 1, 1),
        (_TAG_PHOTOMETRIC, _TIFF_TYPE_SHORT, 1, 1),
        (_TAG_STRIP_OFFSETS, _TIFF_TYPE_LONG, 1, -1),
        (_TAG_SAMPLES_PER_PIXEL, _TIFF_TYPE_SHORT, 1, 1),
        (_TAG_ROWS_PER_STRIP, _TIFF_TYPE_LONG, 1, hojd),
        (_TAG_STRIP_BYTE_COUNTS, _TIFF_TYPE_LONG, 1, strip_byte_count),
        (_TAG_SAMPLE_FORMAT, _TIFF_TYPE_SHORT, 1, 2),  # Signed int
        (_TAG_MODEL_PIXEL_SCALE, _TIFF_TYPE_DOUBLE, 3, -2),
        (_TAG_MODEL_TIEPOINT, _TIFF_TYPE_DOUBLE, 6, -3),
        (_TAG_GEO_KEY_DIRECTORY, _TIFF_TYPE_SHORT, len(geokeys), -4),
    ]

    ifd_storlek = 2 + len(poster_utan_offset) * 12 + 4
    pixel_scale_offset = ifd_offset + ifd_storlek
    tiepoint_offset = pixel_scale_offset + len(pixel_scale_bytes)
    geokey_offset = tiepoint_offset + len(tiepoint_bytes)
    strip_offset = geokey_offset + len(geokey_bytes)

    offset_karta = {
        -1: strip_offset,
        -2: pixel_scale_offset,
        -3: tiepoint_offset,
        -4: geokey_offset,
    }

    ifd_buf = bytearray(struct.pack("<H", len(poster_utan_offset)))
    for tag, tiff_type, count, val in poster_utan_offset:
        faktiskt_val = offset_karta.get(val, val)
        ifd_buf.extend(struct.pack("<HHII", tag, tiff_type, count, faktiskt_val))
    ifd_buf.extend(struct.pack("<I", 0))

    header = struct.pack("<2sHI", b"II", 42, ifd_offset)
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    with mal.open("wb") as fh:
        fh.write(header)
        fh.write(bytes(ifd_buf))
        fh.write(pixel_scale_bytes)
        fh.write(tiepoint_bytes)
        fh.write(geokey_bytes)
        fh.write(pixel_bytes)
    return mal


def _las_tiff_taggvarden(
    data: bytes,
    tiff_type: int,
    count: int,
    value_offset_bytes: bytes,
) -> tuple[float, ...]:
    typ_format = {
        _TIFF_TYPE_SHORT: ("H", 2),
        _TIFF_TYPE_LONG: ("I", 4),
        _TIFF_TYPE_DOUBLE: ("d", 8),
    }
    if tiff_type not in typ_format:
        raise SankGrafFel(f"TIFF-typ {tiff_type} stöds inte.")
    fmt_char, storlek = typ_format[tiff_type]
    total_bytes = count * storlek
    if total_bytes <= 4:
        raw = value_offset_bytes[:total_bytes]
    else:
        offset = int(struct.unpack("<I", value_offset_bytes)[0])
        raw = data[offset : offset + total_bytes]
    varden = struct.unpack(f"<{count}{fmt_char}", raw)
    return tuple(float(v) for v in varden)


def las_geotiff_int64(sokvag: str | Path) -> tuple[NDArray[np.int64], GeoTransformInfo]:
    """Läser ett okomprimerat little-endian int64 GeoTIFF i ren Python + NumPy."""
    fil = Path(sokvag)
    data = fil.read_bytes()
    if len(data) < 8 or data[:2] != b"II":
        raise SankGrafFel(f"Ogiltig little-endian TIFF-fil: {fil}")
    magic, ifd_offset = struct.unpack("<HI", data[2:8])
    if magic != 42:
        raise SankGrafFel(f"Ogiltig TIFF-version {magic} i {fil}")

    antal_taggar = int(struct.unpack("<H", data[ifd_offset : ifd_offset + 2])[0])
    taggar: dict[int, tuple[float, ...]] = {}
    pos = ifd_offset + 2
    for _ in range(antal_taggar):
        tag, tiff_type, count = struct.unpack("<HHI", data[pos : pos + 8])
        val_bytes = data[pos + 8 : pos + 12]
        if tiff_type in (_TIFF_TYPE_SHORT, _TIFF_TYPE_LONG, _TIFF_TYPE_DOUBLE):
            taggar[tag] = _las_tiff_taggvarden(data, tiff_type, count, val_bytes)
        pos += 12

    bredd = int(taggar[_TAG_IMAGE_WIDTH][0])
    hojd = int(taggar[_TAG_IMAGE_LENGTH][0])
    bits = int(taggar[_TAG_BITS_PER_SAMPLE][0])
    sample_format = int(taggar.get(_TAG_SAMPLE_FORMAT, (2.0,))[0])
    if bits != 64 or sample_format != 2:
        raise SankGrafFel(f"Förväntade int64 GeoTIFF, fick bits={bits}, format={sample_format}")

    offsets = [int(v) for v in taggar[_TAG_STRIP_OFFSETS]]
    counts = [int(v) for v in taggar[_TAG_STRIP_BYTE_COUNTS]]
    pixel_raw = b"".join(data[o : o + c] for o, c in zip(offsets, counts, strict=True))
    arr = np.asarray(
        np.frombuffer(pixel_raw, dtype=np.dtype("<i8")).reshape((hojd, bredd)),
        dtype=np.int64,
    ).copy()

    scale = taggar[_TAG_MODEL_PIXEL_SCALE]
    tiepoint = taggar[_TAG_MODEL_TIEPOINT]
    geokeys = [int(v) for v in taggar[_TAG_GEO_KEY_DIRECTORY]]
    epsg = 0
    if len(geokeys) >= 4:
        for idx in range(geokeys[3]):
            bas = 4 + idx * 4
            if bas + 3 < len(geokeys) and geokeys[bas] == 3072:
                epsg = geokeys[bas + 3]
                break

    info = GeoTransformInfo(
        bredd=bredd,
        hojd=hojd,
        x_min=float(tiepoint[3]),
        y_max=float(tiepoint[4]),
        pixelstorlek_x=float(scale[0]),
        pixelstorlek_y=float(scale[1]),
        epsg=epsg,
        dtype="int64",
    )
    return arr, info


def _packa_gp_wkb_linestring(
    koordinater: Sequence[tuple[float, float]],
    srs_id: int = EPSG_SWEREF99_1800,
) -> bytes:
    if len(koordinater) < 2:
        raise SankGrafFel("En flödesväg måste ha minst 2 koordinatpunkter.")
    gp_header = struct.pack("<2sBBi", b"GP", 0, 1, srs_id)
    buf = bytearray(gp_header)
    buf.extend(struct.pack("<BII", 1, 2, len(koordinater)))
    for x, y in koordinater:
        buf.extend(struct.pack("<dd", float(x), float(y)))
    return bytes(buf)


def skriv_flodesvagar_gpkg(
    sokvag: str | Path,
    flodesvagar: Sequence[FlodesVag],
    *,
    bbox_fallback: tuple[float, float, float, float] | None = None,
) -> Path:
    """Skriver `flodesvagar.gpkg` med lager `flodesvagar` enligt kontrakt K2/v1."""
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    if mal.exists():
        mal.unlink()

    xs = [float(x) for vag in flodesvagar for x, _ in vag.koordinater]
    ys = [float(y) for vag in flodesvagar for _, y in vag.koordinater]
    if xs:
        min_x, min_y, max_x, max_y = min(xs), min(ys), max(xs), max(ys)
    elif bbox_fallback is not None:
        min_x, min_y, max_x, max_y = bbox_fallback
    else:
        min_x, min_y, max_x, max_y = (0.0, 0.0, 0.0, 0.0)

    conn = sqlite3.connect(str(mal))
    try:
        conn.execute("PRAGMA application_id = 1196444487")
        conn.execute("PRAGMA user_version = 10200")
        conn.execute(
            """
            CREATE TABLE gpkg_spatial_ref_sys (
                srs_name TEXT NOT NULL,
                srs_id INTEGER NOT NULL PRIMARY KEY,
                organization TEXT NOT NULL,
                organization_coordsys_id INTEGER NOT NULL,
                definition TEXT NOT NULL,
                description TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE gpkg_contents (
                table_name TEXT NOT NULL PRIMARY KEY,
                data_type TEXT NOT NULL,
                identifier TEXT UNIQUE,
                description TEXT DEFAULT '',
                last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                min_x DOUBLE,
                min_y DOUBLE,
                max_x DOUBLE,
                max_y DOUBLE,
                srs_id INTEGER,
                CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id)
                    REFERENCES gpkg_spatial_ref_sys(srs_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE gpkg_geometry_columns (
                table_name TEXT NOT NULL,
                column_name TEXT NOT NULL,
                geometry_type_name TEXT NOT NULL,
                srs_id INTEGER NOT NULL,
                z TINYINT NOT NULL,
                m TINYINT NOT NULL,
                CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),
                CONSTRAINT fk_gc_tn FOREIGN KEY (table_name) REFERENCES gpkg_contents(table_name),
                CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id)
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO gpkg_spatial_ref_sys (
                srs_name, srs_id, organization, organization_coordsys_id, definition, description
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("Undefined cartesian SRS", -1, "NONE", -1, "undefined", "Undefined cartesian"),
                ("Undefined geographic SRS", 0, "NONE", 0, "undefined", "Undefined geographic"),
                ("WGS 84 geodetic", 4326, "EPSG", 4326, WKT_EPSG_4326, "WGS 84"),
                (
                    "SWEREF99 18 00",
                    EPSG_SWEREF99_1800,
                    "EPSG",
                    EPSG_SWEREF99_1800,
                    WKT_EPSG_3011,
                    "SWEREF 99 18 00",
                ),
            ],
        )
        conn.execute(
            """
            CREATE TABLE flodesvagar (
                fid INTEGER PRIMARY KEY AUTOINCREMENT,
                geom BLOB,
                flodesvag_id INTEGER NOT NULL,
                nod_id INTEGER NOT NULL,
                langd_m REAL NOT NULL,
                lutning REAL NOT NULL,
                gangtid_s REAL NOT NULL,
                referensdjup_m REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO gpkg_contents (
                table_name, data_type, identifier, description,
                last_change, min_x, min_y, max_x, max_y, srs_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "flodesvagar",
                "features",
                "flodesvagar",
                "Flödesvägar enligt Floden K2/v1",
                "2026-11-20T00:00:00.000Z",
                min_x,
                min_y,
                max_x,
                max_y,
                EPSG_SWEREF99_1800,
            ),
        )
        conn.execute(
            """
            INSERT INTO gpkg_geometry_columns (
                table_name, column_name, geometry_type_name, srs_id, z, m
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("flodesvagar", "geom", "LINESTRING", EPSG_SWEREF99_1800, 0, 0),
        )
        for vag in flodesvagar:
            wkb = _packa_gp_wkb_linestring(vag.koordinater, EPSG_SWEREF99_1800)
            conn.execute(
                """
                INSERT INTO flodesvagar (
                    geom, flodesvag_id, nod_id, langd_m, lutning, gangtid_s, referensdjup_m
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sqlite3.Binary(wkb),
                    int(vag.id),
                    int(vag.nod_id),
                    float(vag.langd_m),
                    float(vag.lutning),
                    float(vag.gangtid_s),
                    float(vag.referensdjup_m),
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return mal


def _skriv_sankgraf_parquet(graf: SankGraf, sokvag: Path) -> Path:
    """Skriver `sankgraf.parquet` via `pyarrow`.

    Exakt de 12 K2/v1-kolumnerna, eller (K2/v2, grafen har slutsänkor) 14 kolumner: även `typ`
    och `hinder_rh2000`, och `overfall_rh2000` null för slutsänkor.
    """
    v2 = graf.har_slutsankor
    pa: Any = importlib.import_module("pyarrow")
    pq: Any = importlib.import_module("pyarrow.parquet")

    volym_struct = pa.struct([("z", pa.float64()), ("v_m3", pa.float64())])
    yt_struct = pa.struct([("z", pa.float64()), ("a_m2", pa.float64())])
    schema = pa.schema(
        [
            ("nod_id", pa.int64()),
            ("forald_id", pa.int64()),
            ("barn_id", pa.list_(pa.int64())),
            ("overfall_rh2000", pa.float64()),
            ("overfall_x", pa.float64()),
            ("overfall_y", pa.float64()),
            ("ut_till_id", pa.int64()),
            ("volymkurva", pa.list_(volym_struct)),
            ("ytkurva", pa.list_(yt_struct)),
            ("avrinningsyta_m2", pa.float64()),
            ("persistens_m", pa.float64()),
            ("osakerhet_m", pa.float64()),
        ]
        + ([("typ", pa.string()), ("hinder_rh2000", pa.float64())] if v2 else [])
    )

    sorterade = [graf.noder[nid] for nid in sorted(graf.noder)]
    data: dict[str, list[Any]] = {
        "nod_id": [int(n.nod_id) for n in sorterade],
        "forald_id": [int(n.forald_id) if n.forald_id is not None else None for n in sorterade],
        "barn_id": [[int(b) for b in n.barn_id] for n in sorterade],
        "overfall_rh2000": [
            None if n.ar_slutsanka else float(n.overfall_rh2000) for n in sorterade
        ],
        "overfall_x": [float(n.overfall_x) for n in sorterade],
        "overfall_y": [float(n.overfall_y) for n in sorterade],
        "ut_till_id": [int(n.ut_till_id) if n.ut_till_id is not None else None for n in sorterade],
        "volymkurva": [
            [{"z": float(p.z), "v_m3": float(p.v_m3)} for p in n.volymkurva] for n in sorterade
        ],
        "ytkurva": [
            [{"z": float(p.z), "a_m2": float(p.a_m2)} for p in n.ytkurva] for n in sorterade
        ],
        "avrinningsyta_m2": [float(n.avrinningsyta_m2) for n in sorterade],
        "persistens_m": [float(n.persistens_m) for n in sorterade],
        "osakerhet_m": [float(n.osakerhet_m) for n in sorterade],
    }
    if v2:
        data["typ"] = [n.typ for n in sorterade]
        data["hinder_rh2000"] = [
            float(n.hinder_rh2000) if n.hinder_rh2000 is not None else None for n in sorterade
        ]

    tabell = pa.table(data, schema=schema)
    sokvag.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(tabell, str(sokvag))
    return sokvag


def skriv_sankgraf_k2(
    graf: SankGraf,
    ut_mapp: str | Path,
    *,
    indata: Sequence[FilPost] | None = None,
    parametrar: Mapping[str, Any] | None = None,
    kodversion: str | None = None,
    start: str | None = None,
) -> dict[str, Path]:
    """Skriver komplett sänkgraf enligt K2 (`parquet`, `json`, `tif`, `gpkg`, `run.json`).

    Utan slutsänkor K2/v1 (oförändrat), med slutsänkor K2/v2 (`SankGraf.kontrakt`).
    """
    kontrakt = graf.kontrakt
    v2 = kontrakt == KONTRAKT_K2_V2
    mal = Path(ut_mapp)
    mal.mkdir(parents=True, exist_ok=True)
    filer: dict[str, Path] = {}
    utdata_poster: list[FilPost] = []

    if HAR_PYARROW:
        parquet_fil = _skriv_sankgraf_parquet(graf, mal / "sankgraf.parquet")
        filer["sankgraf_parquet"] = parquet_fil
        utdata_poster.append(
            FilPost(roll="sankgraf_parquet", fil=parquet_fil.name, sha256=sha256_fil(parquet_fil))
        )

    json_fil = mal / "sankgraf.json"
    json_payload: dict[str, Any] = {
        "kontrakt": kontrakt,
        "epsg": graf.geo.epsg,
        "filtrerad": graf.filtrerad,
        "persistens_faktor": graf.persistens_faktor,
        "antal_noder": len(graf.noder),
        "noder": [graf.noder[nid].till_k2_dict(v2=v2) for nid in sorted(graf.noder)],
    }
    if v2:
        json_payload["antal_slutsankor"] = len(graf.slutsankor())
    json_fil.write_text(
        json.dumps(json_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    filer["sankgraf_json"] = json_fil
    utdata_poster.append(
        FilPost(roll="sankgraf_json", fil=json_fil.name, sha256=sha256_fil(json_fil))
    )

    cell_fil = skriv_geotiff_int64(
        mal / "cell_till_nod.tif",
        graf.cell_till_nod,
        x_min=graf.geo.x_min,
        y_max=graf.geo.y_max,
        pixelstorlek=graf.geo.pixelstorlek_x,
        epsg=graf.geo.epsg,
    )
    filer["cell_till_nod"] = cell_fil
    utdata_poster.append(
        FilPost(roll="cell_till_nod", fil=cell_fil.name, sha256=sha256_fil(cell_fil))
    )

    # Tillägg till K2/v1 (bakåtkompatibelt): det som behövs för att läsa tillbaka grafen exakt
    # (`sankgraf.k2_las.las_sankgraf_k2`). Kontraktets egna filer är oförändrade.
    topo_fil = mal / "sankgraf_topologi.json"
    topo_fil.write_text(
        json.dumps(
            {
                "kontrakt": kontrakt,
                "ut_lov_id": {str(k): v for k, v in sorted(graf.ut_lov_id.items())},
                "overfall_celler": {
                    str(k): [[int(r), int(c)] for r, c in v]
                    for k, v in sorted(graf.overfall_celler.items())
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    filer["sankgraf_topologi"] = topo_fil
    utdata_poster.append(
        FilPost(roll="sankgraf_topologi", fil=topo_fil.name, sha256=sha256_fil(topo_fil))
    )
    for roll, namn, raster in (
        ("mark_bearbetad", "mark_bearbetad.tif", np.asarray(graf.mark_z, dtype=np.float32)),
        ("markslag", "markslag.tif", np.asarray(graf.markslag, dtype=np.uint8)),
    ):
        rfil = skriv_geotiff(
            mal / namn,
            raster,
            x_min=graf.geo.x_min,
            y_max=graf.geo.y_max,
            pixelstorlek=graf.geo.pixelstorlek_x,
            epsg=graf.geo.epsg,
        )
        filer[roll] = rfil
        utdata_poster.append(FilPost(roll=roll, fil=rfil.name, sha256=sha256_fil(rfil)))

    bbox = (graf.geo.x_min, graf.geo.y_min, graf.geo.x_max, graf.geo.y_max)
    vag_fil = skriv_flodesvagar_gpkg(
        mal / "flodesvagar.gpkg",
        graf.flodesvagar,
        bbox_fallback=bbox,
    )
    filer["flodesvagar"] = vag_fil
    utdata_poster.append(
        FilPost(roll="flodesvagar", fil=vag_fil.name, sha256=sha256_fil(vag_fil))
    )

    param_dict: dict[str, Any] = {
        "kontrakt_ut": kontrakt,
        "grannskap": 4,
        "filtrerad": graf.filtrerad,
        "persistens_faktor": graf.persistens_faktor,
    }
    if parametrar is not None:
        param_dict.update(parametrar)

    manifest = skapa_manifest(
        modul="sankgraf",
        indata=list(indata) if indata is not None else [],
        parametrar=param_dict,
        utdata=utdata_poster,
        matvarden={
            "antal_noder": len(graf.noder),
            "antal_lovnoder": len(graf.lovnoder()),
            "antal_flodesvagar": len(graf.flodesvagar),
            **({"antal_slutsankor": len(graf.slutsankor())} if v2 else {}),
        },
        kodversion=kodversion,
        start=start,
    )
    run_fil = skriv_manifest(manifest, mal / "run.json")
    filer["run"] = run_fil
    return filer


def skriv_statiskt_resultat_k6(
    resultat: StatiskFyllningResultat,
    geo: GeoTransformInfo,
    ut_mapp: str | Path,
    *,
    indata: Sequence[FilPost] | None = None,
    parametrar: Mapping[str, Any] | None = None,
    kodversion: str | None = None,
    start: str | None = None,
) -> dict[str, Path]:
    """Skriver statiskt fyllnadsresultat enligt K6/v1 (`max_djup.tif` och `run.json`)."""
    mal = Path(ut_mapp)
    mal.mkdir(parents=True, exist_ok=True)

    djup_fil = skriv_geotiff(
        mal / "max_djup.tif",
        np.asarray(resultat.djup_m, dtype=np.float32),
        x_min=geo.x_min,
        y_max=geo.y_max,
        pixelstorlek=geo.pixelstorlek_x,
        epsg=geo.epsg,
    )

    param_dict: dict[str, Any] = {
        "nivå": "graf",
        "lage": "statisk",
        "epsg": geo.epsg,
    }
    if parametrar is not None:
        param_dict.update(parametrar)

    max_djup_varde = float(np.max(resultat.djup_m)) if resultat.djup_m.size > 0 else 0.0
    utdata = [FilPost(roll="max_djup", fil=djup_fil.name, sha256=sha256_fil(djup_fil))]
    filer: dict[str, Path] = {"max_djup": djup_fil}
    matvarden: dict[str, Any] = {
        "in_m3": float(resultat.in_m3),
        "lagrat_m3": float(resultat.lagrat_m3),
        "ut_m3": float(resultat.ut_m3),
        "volymfel_rel": float(resultat.volymfel_rel),
        "max_djup_m": max_djup_varde,
    }
    # Slutsänkor (K2/v2): flaggraster och mått bara när de finns, så att övriga resultat är
    # byte-identiska med tiden före K2/v2.
    if resultat.slutsankor and resultat.slutsanka_mask is not None:
        flagga = skriv_slutsanka_raster(mal / SLUTSANKA_FIL, resultat.slutsanka_mask, geo)
        filer["slutsanka"] = flagga
        utdata.append(FilPost(roll="slutsanka", fil=flagga.name, sha256=sha256_fil(flagga)))
        matvarden.update(slutsanka_matvarden(resultat.slutsankor))
    manifest = skapa_manifest(
        modul="sankgraf",
        indata=list(indata) if indata is not None else [],
        parametrar=param_dict,
        utdata=utdata,
        matvarden=matvarden,
        varningar=list(slutsanka_varningar(resultat.slutsankor)),
        kodversion=kodversion,
        start=start,
    )
    run_fil = skriv_manifest(manifest, mal / "run.json")
    filer["run"] = run_fil
    return filer


SLUTSANKA_FIL = "slutsanka.tif"
"""Flaggraster för slutna områden: 1,0 där cellen hör till en slutsänka, NaN annars (float32)."""


def skriv_slutsanka_raster(
    sokvag: str | Path, mask: NDArray[np.bool_], geo: GeoTransformInfo
) -> Path:
    """Skriver `slutsanka.tif` (1,0 i slutna områden, NaN annars) på grafens rutnät."""
    return skriv_geotiff(
        sokvag,
        np.where(mask, np.float32(1.0), np.float32(np.nan)).astype(np.float32),
        x_min=geo.x_min,
        y_max=geo.y_max,
        pixelstorlek=geo.pixelstorlek_x,
        epsg=geo.epsg,
    )


def slutsanka_matvarden(slutsankor: Sequence[SlutsankaResultat]) -> dict[str, Any]:
    """Mätvärden om slutsänkorna (tomt om inga finns)."""
    if not slutsankor:
        return {}
    return {
        "antal_slutsankor": len(slutsankor),
        "slutsanka_area_m2": float(sum(s.area_m2 for s in slutsankor)),
        "slutsanka_lagrat_m3": float(sum(s.volym_m3 for s in slutsankor)),
        "slutsanka_max_djup_m": float(max(s.max_djup_m for s in slutsankor)),
        "slutsanka_over_hinder_m3": float(sum(s.over_hinder_m3 for s in slutsankor)),
        "slutsankor": [s.till_dict() for s in slutsankor],
    }
