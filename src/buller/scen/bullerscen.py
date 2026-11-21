"""Bullerscen: tunn adapter i minnet över terräng, markslag, byggnader och vägar.

Scenen äger ingen lagring, rutindelning eller cache på disk. Den håller numpy-arrayer och
`typer.Byggnad` och kan därför matas från den gemensamma geometrikärnan när den finns.

Rasterkonvention (samma som K1:s GeoTIFF): rad 0 ligger längst i norr, kolumn 0 längst i
väster. `e_min_m` och `n_max_m` är rastrets nordvästra hörn (cellkant, inte cellmitt).
Cellvärden gäller i cellmitt. Terrängen är markhöjd *utan* byggnader, RH 2000.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from numpy.typing import NDArray

from buller.typer import BullerFel, Byggnad, Flyttal

Heltal = NDArray[np.int64]
Boolar = NDArray[np.bool_]

# Största tillåtna överlapp mellan två byggnadspolygoner innan scenen underkänns.
_MAX_OVERLAPP_M2 = 1e-4
# K1-koder som räknas som byggnadsceller när terrängen fylls i under byggnader.
K1_BYGGNADSKODER: tuple[int, ...] = (2, 3, 9)
_EPSG_SWEREF99_1800 = 3011


@dataclass(frozen=True, eq=False)
class Vag:
    """Vägmitt (eller körfältsmitt) som polylinje i plan."""

    id: str
    e_m: Flyttal  # form (k,), k >= 2
    n_m: Flyttal
    korfalt: int = 0


@dataclass(frozen=True, eq=False)
class Vaggtabell:
    """Alla byggnaders väggsegment som platta arrayer (form (antal väggar,))."""

    a_e_m: Flyttal
    a_n_m: Flyttal
    b_e_m: Flyttal
    b_n_m: Flyttal
    normal_e: Flyttal  # utåtriktad enhetsnormal
    normal_n: Flyttal
    langd_m: Flyttal
    byggnad_index: Heltal  # index i `Bullerscen.byggnader`
    tak_rh2000_m: Flyttal
    reflektionsforlust_db: Flyttal

    @property
    def antal(self) -> int:
        return int(self.a_e_m.shape[0])


def _som_flyttal(varde: Any, vad: str) -> Flyttal:
    try:
        arr = np.asarray(varde, dtype=np.float64)
    except (TypeError, ValueError) as fel:
        raise BullerFel(f"{vad} går inte att tolka som flyttal.") from fel
    return arr


def _polygonarea_m2(e_m: Flyttal, n_m: Flyttal) -> float:
    """Tecknad area (positiv = moturs i E/N-planet)."""
    return 0.5 * float(np.sum(e_m * np.roll(n_m, -1) - np.roll(e_m, -1) * n_m))


@dataclass(frozen=True, eq=False)
class Bullerscen:
    """Indata till profilextraktion och banfinnare."""

    terrang_rh2000_m: Flyttal  # form (rader, kolumner), markhöjd utan byggnader
    markslag: NDArray[np.uint8]  # samma form, koder enligt K1
    e_min_m: float
    n_max_m: float
    cell_m: float
    byggnader: tuple[Byggnad, ...] = ()
    vagar: tuple[Vag, ...] = field(default=())

    def __post_init__(self) -> None:
        terrang = _som_flyttal(self.terrang_rh2000_m, "Terrängrastret")
        if terrang.ndim != 2 or terrang.shape[0] < 2 or terrang.shape[1] < 2:
            raise BullerFel(
                f"Terrängrastret ska vara 2D med minst 2 × 2 celler, fick form {terrang.shape}."
            )
        if not bool(np.all(np.isfinite(terrang))):
            antal = int(np.count_nonzero(~np.isfinite(terrang)))
            raise BullerFel(f"Terrängrastret innehåller {antal} icke-ändliga höjder.")
        markslag = np.asarray(self.markslag)
        if markslag.dtype != np.dtype(np.uint8):
            raise BullerFel(f"Markslagsrastret ska vara uint8 (K1-koder), fick {markslag.dtype}.")
        if markslag.shape != terrang.shape:
            raise BullerFel(
                f"Markslagsrastret har form {markslag.shape} men terrängen {terrang.shape}."
            )
        for namn, varde in (("e_min_m", self.e_min_m), ("n_max_m", self.n_max_m)):
            if not math.isfinite(varde):
                raise BullerFel(f"Rastrets ursprung {namn} är inte ändligt.")
        if not math.isfinite(self.cell_m) or self.cell_m <= 0.0:
            raise BullerFel(f"Cellstorleken ska vara > 0 m, fick {self.cell_m}.")
        object.__setattr__(self, "terrang_rh2000_m", terrang)
        object.__setattr__(self, "markslag", markslag)
        object.__setattr__(self, "byggnader", tuple(self.byggnader))
        object.__setattr__(self, "vagar", tuple(self.vagar))
        self._validera_byggnader()
        self._validera_vagar()

    # ------------------------------------------------------------------ validering

    def _validera_byggnader(self) -> None:
        sedda: set[str] = set()
        for byggnad in self.byggnader:
            if byggnad.id in sedda:
                raise BullerFel(f"Byggnads-id '{byggnad.id}' förekommer flera gånger.")
            sedda.add(byggnad.id)
            e_m = _som_flyttal(byggnad.e_m, f"Byggnad '{byggnad.id}' e_m")
            n_m = _som_flyttal(byggnad.n_m, f"Byggnad '{byggnad.id}' n_m")
            if e_m.ndim != 1 or e_m.shape != n_m.shape or e_m.shape[0] < 3:
                raise BullerFel(
                    f"Byggnad '{byggnad.id}' ska ha minst tre hörn och lika långa e_m och n_m."
                )
            if not bool(np.all(np.isfinite(e_m)) and np.all(np.isfinite(n_m))):
                raise BullerFel(f"Byggnad '{byggnad.id}' har icke-ändliga koordinater.")
            if e_m[0] == e_m[-1] and n_m[0] == n_m[-1]:
                raise BullerFel(
                    f"Byggnad '{byggnad.id}' upprepar startpunkten; hörnen ska anges utan den."
                )
            if not math.isfinite(byggnad.tak_rh2000_m):
                raise BullerFel(f"Byggnad '{byggnad.id}' saknar ändlig takhöjd.")
            if not math.isfinite(byggnad.reflektionsforlust_db) or (
                byggnad.reflektionsforlust_db < 0.0
            ):
                raise BullerFel(
                    f"Byggnad '{byggnad.id}' har ogiltig reflektionsförlust "
                    f"({byggnad.reflektionsforlust_db} dB)."
                )
        if not self.byggnader:
            return
        polygoner = self.polygoner
        ogiltiga = np.flatnonzero(~shapely.is_valid(polygoner) | (shapely.area(polygoner) <= 0.0))
        if ogiltiga.size:
            namn = self.byggnader[int(ogiltiga[0])].id
            raise BullerFel(
                f"Byggnad '{namn}' har ogiltig polygon (självskärning eller ingen area); "
                f"{ogiltiga.size} byggnader berörs."
            )
        i, j = self.byggnadstrad.query(polygoner, predicate="intersects")
        olika = i < j
        i, j = i[olika], j[olika]
        if i.size:
            overlapp_m2 = shapely.area(shapely.intersection(polygoner[i], polygoner[j]))
            for_stora = np.flatnonzero(overlapp_m2 > _MAX_OVERLAPP_M2)
            if for_stora.size:
                k = int(for_stora[0])
                raise BullerFel(
                    f"Byggnaderna '{self.byggnader[int(i[k])].id}' och "
                    f"'{self.byggnader[int(j[k])].id}' överlappar med {overlapp_m2[k]:.3f} m² "
                    f"({for_stora.size} överlappande par). Rätta byggnadsdata först."
                )
        # Takhöjd ska ligga över marken där byggnaden står (prövas i hörn inom rastret).
        for byggnad in self.byggnader:
            e_m = np.asarray(byggnad.e_m, dtype=np.float64)
            n_m = np.asarray(byggnad.n_m, dtype=np.float64)
            inne = self.inom_raster(e_m, n_m)
            if bool(np.any(inne)):
                mark_m = float(np.min(self.markhojd_m(e_m[inne], n_m[inne])))
                if byggnad.tak_rh2000_m <= mark_m:
                    raise BullerFel(
                        f"Byggnad '{byggnad.id}' har takhöjd {byggnad.tak_rh2000_m:.2f} m "
                        f"som inte ligger över marken ({mark_m:.2f} m RH 2000)."
                    )

    def _validera_vagar(self) -> None:
        sedda: set[tuple[str, int]] = set()
        for vag in self.vagar:
            nyckel = (vag.id, vag.korfalt)
            if nyckel in sedda:
                raise BullerFel(f"Väg '{vag.id}' körfält {vag.korfalt} förekommer flera gånger.")
            sedda.add(nyckel)
            validera_vag(vag)

    # ------------------------------------------------------------------ raster

    @property
    def antal_rader(self) -> int:
        return int(self.terrang_rh2000_m.shape[0])

    @property
    def antal_kolumner(self) -> int:
        return int(self.terrang_rh2000_m.shape[1])

    @property
    def e_max_m(self) -> float:
        return self.e_min_m + self.antal_kolumner * self.cell_m

    @property
    def n_min_m(self) -> float:
        return self.n_max_m - self.antal_rader * self.cell_m

    def inom_raster(self, e_m: Flyttal, n_m: Flyttal) -> Boolar:
        """Sant för punkter inom rastrets utbredning (kanterna inräknade)."""
        e = np.asarray(e_m, dtype=np.float64)
        n = np.asarray(n_m, dtype=np.float64)
        inne: Boolar = (
            (e >= self.e_min_m) & (e <= self.e_max_m) & (n >= self.n_min_m) & (n <= self.n_max_m)
        )
        return inne

    def _krav_inom_raster(self, e_m: Flyttal, n_m: Flyttal, vad: str) -> None:
        inne = self.inom_raster(e_m, n_m)
        if not bool(np.all(inne)):
            k = int(np.flatnonzero(~inne.ravel())[0])
            raise BullerFel(
                f"{vad}: {int(np.count_nonzero(~inne))} punkter ligger utanför terrängrastret, "
                f"t.ex. E {float(np.ravel(e_m)[k]):.2f}, N {float(np.ravel(n_m)[k]):.2f}."
            )

    def markhojd_m(self, e_m: Flyttal, n_m: Flyttal) -> Flyttal:
        """Markhöjd (RH 2000) med bilinjär interpolation mellan cellmitt.

        I den yttersta halva cellen längs rastrets kant hålls värdet konstant (ingen
        extrapolation). Punkter utanför rastret ger `BullerFel`.
        """
        e = np.asarray(e_m, dtype=np.float64)
        n = np.asarray(n_m, dtype=np.float64)
        self._krav_inom_raster(e, n, "Markhöjd")
        kol = np.clip((e - self.e_min_m) / self.cell_m - 0.5, 0.0, self.antal_kolumner - 1.0)
        rad = np.clip((self.n_max_m - n) / self.cell_m - 0.5, 0.0, self.antal_rader - 1.0)
        k0 = np.minimum(np.floor(kol).astype(np.int64), self.antal_kolumner - 2)
        r0 = np.minimum(np.floor(rad).astype(np.int64), self.antal_rader - 2)
        fk = kol - k0
        fr = rad - r0
        z = self.terrang_rh2000_m
        ovre = z[r0, k0] * (1.0 - fk) + z[r0, k0 + 1] * fk
        nedre = z[r0 + 1, k0] * (1.0 - fk) + z[r0 + 1, k0 + 1] * fk
        hojd: Flyttal = ovre * (1.0 - fr) + nedre * fr
        return hojd

    def markslag_i(self, e_m: Flyttal, n_m: Flyttal) -> NDArray[np.uint8]:
        """Markslagskod i den cell som punkten ligger i. Utanför rastret ger `BullerFel`."""
        e = np.asarray(e_m, dtype=np.float64)
        n = np.asarray(n_m, dtype=np.float64)
        self._krav_inom_raster(e, n, "Markslag")
        kol = np.clip(
            np.floor((e - self.e_min_m) / self.cell_m).astype(np.int64),
            0,
            self.antal_kolumner - 1,
        )
        rad = np.clip(
            np.floor((self.n_max_m - n) / self.cell_m).astype(np.int64), 0, self.antal_rader - 1
        )
        koder: NDArray[np.uint8] = self.markslag[rad, kol]
        return koder

    def andel_okant_markslag(self) -> float:
        """Andel av rastrets celler med markslag 0 (okänt)."""
        return float(np.count_nonzero(self.markslag == 0)) / float(self.markslag.size)

    # ------------------------------------------------------------------ byggnader

    @cached_property
    def polygoner(self) -> NDArray[np.object_]:
        """Byggnadspolygoner som shapely-geometrier, i samma ordning som `byggnader`."""
        geometrier = [
            shapely.Polygon(
                np.column_stack(
                    [np.asarray(b.e_m, dtype=np.float64), np.asarray(b.n_m, dtype=np.float64)]
                )
            )
            for b in self.byggnader
        ]
        polygoner: NDArray[np.object_] = np.array(geometrier, dtype=object)
        return polygoner

    @cached_property
    def byggnadstrad(self) -> Any:
        """Rumsligt index (STR-träd) över byggnadspolygonerna."""
        return shapely.STRtree(self.polygoner)

    @cached_property
    def tak_rh2000_m(self) -> Flyttal:
        """Takhöjd per byggnad, form (antal byggnader,)."""
        return np.array([b.tak_rh2000_m for b in self.byggnader], dtype=np.float64)

    @cached_property
    def byggnadsindex(self) -> dict[str, int]:
        """Byggnads-id → index i `byggnader`."""
        return {b.id: i for i, b in enumerate(self.byggnader)}

    @cached_property
    def vaggar(self) -> Vaggtabell:
        """Väggsegment för alla byggnader med utåtriktad normal."""
        a_e: list[Flyttal] = []
        a_n: list[Flyttal] = []
        b_e: list[Flyttal] = []
        b_n: list[Flyttal] = []
        tecken: list[Flyttal] = []
        index: list[Heltal] = []
        for i, byggnad in enumerate(self.byggnader):
            e_m = np.asarray(byggnad.e_m, dtype=np.float64)
            n_m = np.asarray(byggnad.n_m, dtype=np.float64)
            a_e.append(e_m)
            a_n.append(n_m)
            b_e.append(np.roll(e_m, -1))
            b_n.append(np.roll(n_m, -1))
            moturs = _polygonarea_m2(e_m, n_m) > 0.0
            tecken.append(np.full(e_m.shape[0], 1.0 if moturs else -1.0))
            index.append(np.full(e_m.shape[0], i, dtype=np.int64))
        if not a_e:
            tom = np.zeros(0)
            return Vaggtabell(
                tom, tom, tom, tom, tom, tom, tom, np.zeros(0, dtype=np.int64), tom, tom
            )
        ae, an = np.concatenate(a_e), np.concatenate(a_n)
        be, bn = np.concatenate(b_e), np.concatenate(b_n)
        riktning = np.concatenate(tecken)
        byggnad_index = np.concatenate(index)
        langd = np.hypot(be - ae, bn - an)
        if bool(np.any(langd <= 0.0)):
            k = int(np.flatnonzero(langd <= 0.0)[0])
            raise BullerFel(
                f"Byggnad '{self.byggnader[int(byggnad_index[k])].id}' har två hörn i samma punkt."
            )
        # Moturs polygon: utåt ligger till höger om gångriktningen, (dn, -de).
        normal_e = riktning * (bn - an) / langd
        normal_n = -riktning * (be - ae) / langd
        forlust = np.array([b.reflektionsforlust_db for b in self.byggnader], dtype=np.float64)
        return Vaggtabell(
            a_e_m=ae,
            a_n_m=an,
            b_e_m=be,
            b_n_m=bn,
            normal_e=normal_e,
            normal_n=normal_n,
            langd_m=langd,
            byggnad_index=byggnad_index,
            tak_rh2000_m=self.tak_rh2000_m[byggnad_index],
            reflektionsforlust_db=forlust[byggnad_index],
        )

    def byggnad_vid(self, e_m: Flyttal, n_m: Flyttal) -> Heltal:
        """Index för den byggnad punkten ligger strikt inuti, annars -1."""
        e = np.atleast_1d(np.asarray(e_m, dtype=np.float64))
        n = np.atleast_1d(np.asarray(n_m, dtype=np.float64))
        traff = np.full(e.shape[0], -1, dtype=np.int64)
        if self.byggnader and e.shape[0]:
            punkt, byggnad = self.byggnadstrad.query(shapely.points(e, n), predicate="within")
            traff[punkt] = byggnad
        return traff

    def i_byggnad(self, e_m: Flyttal, n_m: Flyttal) -> Boolar:
        """Sant för punkter som ligger strikt inuti någon byggnad."""
        inne: Boolar = self.byggnad_vid(e_m, n_m) >= 0
        return inne


def validera_vag(vag: Vag) -> None:
    """Kontrollerar en vägpolylinje. Fel ger `BullerFel`."""
    e_m = _som_flyttal(vag.e_m, f"Väg '{vag.id}' e_m")
    n_m = _som_flyttal(vag.n_m, f"Väg '{vag.id}' n_m")
    if e_m.ndim != 1 or e_m.shape != n_m.shape or e_m.shape[0] < 2:
        raise BullerFel(f"Väg '{vag.id}' ska ha minst två punkter och lika långa e_m och n_m.")
    if not bool(np.all(np.isfinite(e_m)) and np.all(np.isfinite(n_m))):
        raise BullerFel(f"Väg '{vag.id}' har icke-ändliga koordinater.")
    if bool(np.any(np.hypot(np.diff(e_m), np.diff(n_m)) <= 0.0)):
        raise BullerFel(f"Väg '{vag.id}' har två på varandra följande punkter i samma läge.")


def _fyll_maskerade(hojd_m: Flyttal, mask: Boolar) -> Flyttal:
    """Fyller maskerade celler inifrån kanten med medelvärdet av redan kända grannar."""
    if bool(np.all(mask)):
        raise BullerFel("Hela rastret är byggnadsceller; det finns ingen mark att fylla från.")
    z = hojd_m.copy()
    kvar = mask.copy()
    while bool(np.any(kvar)):
        kand = np.where(kvar, 0.0, z)
        giltig = (~kvar).astype(np.float64)
        summa = np.zeros_like(z)
        antal = np.zeros_like(z)
        summa[1:, :] += kand[:-1, :]
        antal[1:, :] += giltig[:-1, :]
        summa[:-1, :] += kand[1:, :]
        antal[:-1, :] += giltig[1:, :]
        summa[:, 1:] += kand[:, :-1]
        antal[:, 1:] += giltig[:, :-1]
        summa[:, :-1] += kand[:, 1:]
        antal[:, :-1] += giltig[:, 1:]
        nya = kvar & (antal > 0.0)
        z[nya] = summa[nya] / antal[nya]
        kvar &= ~nya
    return z


def bullerscen_fran_k1_ruta(
    mapp: str | Path,
    byggnader: Sequence[Byggnad] = (),
    vagar: Sequence[Vag] = (),
    *,
    byggnadskoder: Sequence[int] = K1_BYGGNADSKODER,
) -> Bullerscen:
    """Valfri inläsare: bygger en scen ur en K1-ruta (`mark.tif` och `markslag.tif`).

    K1:s `mark.tif` innehåller byggnader som hinder. Celler vars markslag finns i
    `byggnadskoder` ersätts därför med mark som fylls i från omgivande celler. Höjden under
    byggnader är alltså en utfyllnad, inte en mätning. Byggnader och vägar finns inte som
    vektorer i K1 och skickas in av anroparen.
    """
    from testsvit.k1_io import K1FormatFel, las_geotiff

    rot = Path(mapp)
    try:
        mark, info = las_geotiff(rot / "mark.tif")
        markslag, info_slag = las_geotiff(rot / "markslag.tif")
    except (K1FormatFel, OSError) as fel:
        raise BullerFel(f"K1-rutan i {rot} gick inte att läsa: {fel}") from fel
    if info.dtype != "float32" or info_slag.dtype != "uint8":
        raise BullerFel(f"K1-rutan i {rot}: mark.tif ska vara float32 och markslag.tif uint8.")
    if info.epsg != _EPSG_SWEREF99_1800 or info_slag.epsg != _EPSG_SWEREF99_1800:
        raise BullerFel(f"K1-rutan i {rot} ligger inte i SWEREF 99 18 00 (EPSG:3011).")
    if info.pixelstorlek_x != info.pixelstorlek_y:
        raise BullerFel(f"K1-rutan i {rot} har inte kvadratiska celler.")
    samma_rutnat = (
        mark.shape == markslag.shape
        and info.x_min == info_slag.x_min
        and info.y_max == info_slag.y_max
        and info.pixelstorlek_x == info_slag.pixelstorlek_x
    )
    if not samma_rutnat:
        raise BullerFel(f"K1-rutan i {rot}: mark.tif och markslag.tif har olika rutnät.")
    slag = np.asarray(markslag, dtype=np.uint8)
    terrang = _fyll_maskerade(
        np.asarray(mark, dtype=np.float64), np.isin(slag, np.asarray(byggnadskoder))
    )
    return Bullerscen(
        terrang_rh2000_m=terrang,
        markslag=slag,
        e_min_m=info.x_min,
        n_max_m=info.y_max,
        cell_m=info.pixelstorlek_x,
        byggnader=tuple(byggnader),
        vagar=tuple(vagar),
    )
