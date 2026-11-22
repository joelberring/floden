"""Takvatten: regn på byggnader avrinner till marken längs fasaderna (Joels beslut 2026-11-22).

Regn som faller på ett tak (markslag 9 i `mark.tif`) blir inte kvar på taket och försvinner inte:
det läggs i markcellerna närmast utanför husets takfötter. Takets egen fördröjning, gröna tak och
stuprörens verkliga lägen modelleras inte. Reglerna (se `docs/VATTEN.md`):

- **Byggnad** = polygon ur `byggnader.gpkg` (K1 v2) eller, när registret saknas, en
  fyrgrannsammanhängande grupp byggnadsceller. En byggnadscell utanför alla polygoner hör till
  närmaste polygonbyggnad i samma cellgrupp.
- **Fasadlinjer:** polygonens kanter (yttre ring moturs, hål medurs, normalen åt höger = ut ur
  huset); på varandra följande kanter som byter riktning mindre än 10° slås ihop till en linje.
- **Takform** (`takform`, valfri): `platt` → alla fasadlinjer i proportion till längden;
  `pulpet` med `takfall_azimut_grader` → de linjer vars normal pekar högst 45° från takfallet,
  annars som okänd; `sadel`/`brutet`/`mansard` och **okänd** (saknas eller okänd text) →
  brutet tak/sadeltak med nock längs husets längdaxel: vattnet går till **takfötterna**, de
  linjer som är minst lika långa som husets djup bakom dem (djupet mäts från linjens mitt rakt in
  i huset). För en rektangel är det de två långsidorna, hälften var. Fördelningen mellan
  takfötterna är i proportion till längden (L-form: två yttre och två inre långsidor).
- **Ring** (polygon med hål, t.ex. ett slutet kvarter runt en gård) med sadel/okänd takform:
  hälften till innersidan (gården), hälften till yttersidan (gatan), var för sig i proportion till
  längden.
- **Längs linjen** fördelas vattnet jämnt per meter (prov var fjärdedels cell) i den första
  markcellen utanför fasaden. Prov som hamnar i en annan byggnad (brandvägg), i en cell utan
  höjd eller i en öppning (port) räknas inte; linjens vatten går då till dess övriga prov.
  Prov utanför rutan räknas som utflöde ur modellen (som vatten över rutkanten).
- **Andel till ledningsnät** (`andel_ledning`, standard 0) tas bort innan fördelningen och
  redovisas.

Allt är linjärt i regnet: `fordela` flyttar varje byggnads takvolym enligt fasta vikter, så
statisk fyllning, dynamik och 2D använder samma fördelning (`extra_area_m2` för likformigt regn).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from testsvit.k1_io import GeoTransformInfo

MARKSLAG_BYGGNAD = 9

TAKFORM_PLATT = "platt"
TAKFORM_PULPET = "pulpet"
TAKFORM_SADEL = "sadel"
TAKFORM_OKAND = "okand"

_TAKFORM_ALIAS: dict[str, str] = {
    "platt": TAKFORM_PLATT,
    "plant": TAKFORM_PLATT,
    "flat": TAKFORM_PLATT,
    "pulpet": TAKFORM_PULPET,
    "pulpettak": TAKFORM_PULPET,
    "skillion": TAKFORM_PULPET,
    "sadel": TAKFORM_SADEL,
    "sadeltak": TAKFORM_SADEL,
    "gabled": TAKFORM_SADEL,
    "brutet": TAKFORM_SADEL,
    "brutet tak": TAKFORM_SADEL,
    "mansard": TAKFORM_SADEL,
    "mansardtak": TAKFORM_SADEL,
    "gambrel": TAKFORM_SADEL,
}

REGEL_SADEL = "sadel_takfot"
REGEL_RING = "ring_hälften_in_hälften_ut"
REGEL_PLATT = "platt_alla_fasader"
REGEL_PULPET = "pulpet_lägre_sida"
REGEL_KOMPLEX = "via_grannbyggnad"

_SAMMANSLAG_GRADER = 10.0
_PULPET_MAX_VINKEL_GRADER = 45.0
_PROV_PER_CELL = 4
_STEG_UT = (0.5, 1.0, 1.5, 2.0)

TAKVATTEN_ANTAGANDE = (
    "Takvatten: med, okänd takform räknas som brutet tak (hälften till varje långsida)"
)
TAKVATTEN_AV_ANTAGANDE = "Takvatten: av (regn på tak ingår inte i beräkningen)"


class TakvattenFel(Exception):
    """Takvattnet kan inte fördelas utan att gissa (t.ex. ett tak utan någon väg till mark)."""


def tolka_takform(text: str | None) -> tuple[str, bool]:
    """Takformsklass och om texten var okänd (`(TAKFORM_OKAND, True)`)."""
    if text is None or not str(text).strip():
        return TAKFORM_OKAND, False
    klass = _TAKFORM_ALIAS.get(str(text).strip().lower())
    if klass is None:
        return TAKFORM_OKAND, True
    return klass, False


@dataclass(frozen=True)
class TakPolygon:
    """En byggnad ur registret, så som takvattnet behöver den (EPSG:3011)."""

    byggnad_id: str
    polygon: tuple[tuple[float, float], ...]
    hal: tuple[tuple[tuple[float, float], ...], ...] = ()
    takform: str | None = None
    takfall_azimut_grader: float | None = None
    """Takfallets riktning för pulpettak (grader medurs från norr, mot den lägre sidan)."""


@dataclass(frozen=True)
class ByggnadsTakvatten:
    """Hur en byggnads takvatten fördelades (för redovisning)."""

    byggnad_id: str
    kalla: str  # "register" eller "raster"
    takform: str | None
    regel: str
    tak_celler: int
    takyta_m2: float
    andel_till_mark: float
    andel_ut_ur_rutan: float
    linjer: tuple[dict[str, Any], ...]
    anmarkningar: tuple[str, ...] = ()

    def till_dict(self) -> dict[str, Any]:
        return {
            "byggnad_id": self.byggnad_id,
            "kalla": self.kalla,
            "takform": self.takform,
            "regel": self.regel,
            "tak_celler": self.tak_celler,
            "takyta_m2": self.takyta_m2,
            "andel_till_mark": self.andel_till_mark,
            "andel_ut_ur_rutan": self.andel_ut_ur_rutan,
            "linjer": [dict(x) for x in self.linjer],
            "anmarkningar": list(self.anmarkningar),
        }


@dataclass(frozen=True)
class TakvattenFordelning:
    """Linjär fördelning av takvatten från byggnadsceller till markceller.

    `tak_grupp[r, c]` är byggnadens index för en takcell (−1 annars). För byggnad `g` går andelen
    `mal_vikt[k]` av takvolymen (efter ledningsandelen) till cellen `mal_cell[k]` (platt index) där
    `mal_grupp[k] == g`, och `ut_vikt[g]` lämnar rutan. Per byggnad är summan 1.
    """

    form: tuple[int, int]
    cell_area_m2: float
    andel_ledning: float
    tak_grupp: NDArray[np.int64]
    mal_cell: NDArray[np.int64]
    mal_grupp: NDArray[np.int64]
    mal_vikt: NDArray[np.float64]
    ut_vikt: NDArray[np.float64]
    byggnader: tuple[ByggnadsTakvatten, ...] = ()
    flaggor: Mapping[str, int] = field(default_factory=dict)

    @property
    def antal_byggnader(self) -> int:
        return len(self.byggnader)

    @property
    def tom(self) -> bool:
        """Inga takceller: fördelningen ändrar ingenting."""
        return self.antal_byggnader == 0

    @cached_property
    def tak_mask(self) -> NDArray[np.bool_]:
        return np.asarray(self.tak_grupp >= 0, dtype=np.bool_)

    @cached_property
    def takyta_m2(self) -> float:
        return float(np.count_nonzero(self.tak_grupp >= 0)) * self.cell_area_m2

    def fordela(
        self, tak_volym_m3: NDArray[np.float64]
    ) -> tuple[NDArray[np.float64], float, float]:
        """Flyttar volymen i takcellerna (m³, hela rutnätet) till marken.

        Returnerar `(till_mark_m3, ut_ur_rutan_m3, till_ledning_m3)`; `till_mark_m3` har samma
        form som rutnätet och är 0 i takcellerna. Summan av de tre är takvolymen.
        """
        vol = np.asarray(tak_volym_m3, dtype=np.float64)
        if vol.shape != self.form:
            raise TakvattenFel(f"Takvolymen har form {vol.shape}, fördelningen {self.form}.")
        hojd, bredd = self.form
        if self.tom:
            return np.zeros(self.form, dtype=np.float64), 0.0, 0.0
        mask = self.tak_grupp >= 0
        tak_v = vol[mask]
        if not np.all(np.isfinite(tak_v)) or np.any(tak_v < 0.0):
            raise TakvattenFel("Regnet på taken innehåller NaN/Inf eller negativa värden.")
        per_grupp = np.bincount(
            self.tak_grupp[mask], weights=tak_v, minlength=self.antal_byggnader
        )
        kvar = 1.0 - self.andel_ledning
        till_mark: NDArray[np.float64] = np.bincount(
            self.mal_cell,
            weights=per_grupp[self.mal_grupp] * self.mal_vikt * kvar,
            minlength=hojd * bredd,
        ).reshape(self.form).astype(np.float64)
        ut = float(np.sum(per_grupp * self.ut_vikt) * kvar)
        ledning = float(np.sum(per_grupp) * self.andel_ledning)
        return till_mark, ut, ledning

    @cached_property
    def _likformig(self) -> tuple[NDArray[np.float64], float, float]:
        area = np.where(self.tak_grupp >= 0, self.cell_area_m2, 0.0)
        return self.fordela(area)

    @property
    def extra_area_m2(self) -> NDArray[np.float64]:
        """Takyta (m²) som avvattnas till varje markcell, för likformigt regn."""
        return self._likformig[0]

    @property
    def ut_area_m2(self) -> float:
        """Takyta vars vatten lämnar rutan (fasader utanför rutans kant)."""
        return self._likformig[1]

    @property
    def ledning_area_m2(self) -> float:
        """Takyta vars vatten går till ledningsnätet (`andel_ledning`)."""
        return self._likformig[2]

    def kontrollera_mot(self, markslag: NDArray[np.uint8], mark_z: NDArray[Any]) -> None:
        """Fel om fördelningen inte hör till rastret (takceller och mål måste stämma)."""
        if markslag.shape != self.form:
            raise TakvattenFel(f"Takvattnet byggdes för {self.form}, rastret är {markslag.shape}.")
        tak = markslag == MARKSLAG_BYGGNAD
        if not np.array_equal(tak, self.tak_mask):
            raise TakvattenFel(
                "Takvattnets takceller stämmer inte med byggnadscellerna (markslag 9); "
                "fördelningen måste byggas om för det här rastret."
            )
        if self.mal_cell.size:
            z = np.asarray(mark_z, dtype=np.float64).ravel()[self.mal_cell]
            slag = markslag.ravel()[self.mal_cell]
            if np.any(slag == MARKSLAG_BYGGNAD) or not np.all(np.isfinite(z)):
                raise TakvattenFel("Takvattnets målceller är inte markceller med höjd.")

    def sammanfattning(self) -> dict[str, Any]:
        """Mätvärden och antaganden för manifest och webb (inga gissade tal)."""
        regler: dict[str, int] = {}
        for b in self.byggnader:
            regler[b.regel] = regler.get(b.regel, 0) + 1
        text = TAKVATTEN_ANTAGANDE
        if self.andel_ledning > 0.0:
            text += f"; {self.andel_ledning:.0%} av takvattnet till ledningsnätet"
        return {
            "takvatten": True,
            "takvatten_antagande": text,
            "takvatten_antal_byggnader": self.antal_byggnader,
            "takvatten_takyta_m2": self.takyta_m2,
            "takvatten_andel_ledning": self.andel_ledning,
            "takvatten_regler": dict(sorted(regler.items())),
            "takvatten_flaggor": dict(sorted(self.flaggor.items())),
        }


def tom_fordelning(
    form: tuple[int, int], cell_area_m2: float, andel_ledning: float = 0.0
) -> TakvattenFordelning:
    return TakvattenFordelning(
        form=form,
        cell_area_m2=cell_area_m2,
        andel_ledning=andel_ledning,
        tak_grupp=np.full(form, -1, dtype=np.int64),
        mal_cell=np.zeros(0, dtype=np.int64),
        mal_grupp=np.zeros(0, dtype=np.int64),
        mal_vikt=np.zeros(0, dtype=np.float64),
        ut_vikt=np.zeros(0, dtype=np.float64),
    )


# --- geometri ------------------------------------------------------------------------------


@dataclass
class _Linje:
    """En fasadlinje: sammanslagna kanter i en ring (0 = yttre ring, >= 1 = hål)."""

    ring: int
    kanter: list[tuple[tuple[float, float], tuple[float, float]]]

    @property
    def langd(self) -> float:
        return float(sum(math.dist(a, b) for a, b in self.kanter))

    def normal(self) -> tuple[float, float]:
        """Längdviktad utåtnormal (höger om färdriktningen)."""
        nx = sum(b[1] - a[1] for a, b in self.kanter)
        ny = sum(-(b[0] - a[0]) for a, b in self.kanter)
        n = math.hypot(nx, ny)
        return (nx / n, ny / n) if n > 0 else (0.0, 0.0)

    def langsta_kant(self) -> tuple[tuple[float, float], tuple[float, float]]:
        return max(self.kanter, key=lambda k: math.dist(k[0], k[1]))


def _kanter_ur_ring(koord: Sequence[tuple[float, float]]) -> list[tuple[tuple[float, float], ...]]:
    pts = [(float(x), float(y)) for x, y in koord]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    ut: list[tuple[tuple[float, float], ...]] = []
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        if math.dist(a, b) > 1e-9:
            ut.append((a, b))
    return ut


def _riktning(k: tuple[tuple[float, float], ...]) -> float:
    (ax, ay), (bx, by) = k[0], k[1]
    return math.atan2(by - ay, bx - ax)


def _slå_ihop(kanter: list[tuple[tuple[float, float], ...]], ring: int) -> list[_Linje]:
    """Slår ihop på varandra följande kanter med riktningsändring under 10° (även över start)."""
    if not kanter:
        return []
    grans = math.radians(_SAMMANSLAG_GRADER)

    def lika(k1: tuple[tuple[float, float], ...], k2: tuple[tuple[float, float], ...]) -> bool:
        d = abs((_riktning(k2) - _riktning(k1) + math.pi) % (2 * math.pi) - math.pi)
        return d < grans

    n = len(kanter)
    # Börja vid en kant som inte fortsätter föregående, så att ringens start inte delar en linje.
    start = next((i for i in range(n) if not lika(kanter[i - 1], kanter[i])), 0)
    ordnade = kanter[start:] + kanter[:start]
    linjer: list[_Linje] = []
    for k in ordnade:
        a, b = k[0], k[1]
        if linjer and lika(linjer[-1].kanter[-1], k):
            linjer[-1].kanter.append((a, b))
        else:
            linjer.append(_Linje(ring=ring, kanter=[(a, b)]))
    return linjer


def _linjer(poly: Any) -> list[_Linje]:
    from shapely.geometry.polygon import orient

    p = orient(poly, sign=1.0)  # yttre moturs, hål medurs
    ut = _slå_ihop(_kanter_ur_ring(list(p.exterior.coords)), 0)
    for i, hal in enumerate(p.interiors, start=1):
        ut.extend(_slå_ihop(_kanter_ur_ring(list(hal.coords)), i))
    return ut


def _djup_bakom(poly: Any, linje: _Linje) -> float:
    """Husets djup bakom linjen: från mitten av linjens längsta kant rakt in i huset."""
    from shapely.geometry import LineString, Point

    (ax, ay), (bx, by) = linje.langsta_kant()
    nx, ny = by - ay, -(bx - ax)
    nn = math.hypot(nx, ny)
    nx, ny = nx / nn, ny / nn
    mx, my = 0.5 * (ax + bx), 0.5 * (ay + by)
    x0, y0, x1, y1 = poly.bounds
    lang = 2.0 * math.hypot(x1 - x0, y1 - y0) + 1.0
    eps = 1e-7
    start = (mx - nx * eps, my - ny * eps)
    stråle = LineString([start, (mx - nx * lang, my - ny * lang)])
    snitt = stråle.intersection(poly)
    delar = [snitt] if snitt.geom_type == "LineString" else list(getattr(snitt, "geoms", []))
    p0 = Point(start)
    for d in delar:
        if d.geom_type == "LineString" and d.distance(p0) < 1e-6:
            return float(d.length) + eps
    return 0.0


def _linjevikter(
    poly: Any, linjer: list[_Linje], klass: str, azimut: float | None
) -> tuple[str, dict[int, float], list[str]]:
    """Regel, andel per linje (summa 1) och anmärkningar."""
    anm: list[str] = []
    langder = [li.langd for li in linjer]

    def proportionellt(idx: list[int]) -> dict[int, float]:
        tot = sum(langder[i] for i in idx)
        return {i: langder[i] / tot for i in idx} if tot > 0 else {}

    alla = list(range(len(linjer)))
    if klass == TAKFORM_PULPET:
        if azimut is not None and math.isfinite(azimut):
            dx, dy = math.sin(math.radians(azimut)), math.cos(math.radians(azimut))
            grans = math.cos(math.radians(_PULPET_MAX_VINKEL_GRADER)) - 1e-9
            idx = [
                i for i, li in enumerate(linjer)
                if li.normal()[0] * dx + li.normal()[1] * dy >= grans
            ]
            if idx:
                return REGEL_PULPET, proportionellt(idx), anm
            anm.append("pulpet: ingen fasad i takfallets riktning, räknas som okänd takform")
        else:
            anm.append("pulpet utan takfallets riktning, räknas som okänd takform")
        klass = TAKFORM_OKAND
    if klass == TAKFORM_PLATT:
        return REGEL_PLATT, proportionellt(alla), anm
    # Sadel, brutet, mansard och okänd.
    inre = [i for i, li in enumerate(linjer) if li.ring > 0]
    if inre:
        yttre = [i for i, li in enumerate(linjer) if li.ring == 0]
        v = {i: 0.5 * a for i, a in proportionellt(yttre).items()}
        v.update({i: 0.5 * a for i, a in proportionellt(inre).items()})
        return REGEL_RING, v, anm
    takfot = [i for i, li in enumerate(linjer) if langder[i] >= _djup_bakom(poly, li) * (1 - 1e-9)]
    if not takfot:
        anm.append("ingen fasadlinje är lika lång som husets djup; alla fasader används")
        return REGEL_PLATT, proportionellt(alla), anm
    return REGEL_SADEL, proportionellt(takfot), anm


@dataclass
class _Prov:
    celler: NDArray[np.int64]
    vikter: NDArray[np.float64]
    ut: float
    ogiltiga: int
    i_oppning: int


def _prova_linje(
    linje: _Linje,
    vikt: float,
    *,
    grupp: int,
    tak_grupp: NDArray[np.int64],
    mark_ok: NDArray[np.bool_],
    oppning: NDArray[np.bool_] | None,
    geo: GeoTransformInfo,
) -> _Prov:
    """Prov jämnt längs linjen, flyttade ut från fasaden tills de hamnar i en markcell."""
    dx, dy = float(geo.pixelstorlek_x), float(geo.pixelstorlek_y)
    s = min(dx, dy) / _PROV_PER_CELL
    hojd, bredd = tak_grupp.shape
    xs: list[NDArray[np.float64]] = []
    ys: list[NDArray[np.float64]] = []
    ws: list[NDArray[np.float64]] = []
    ns: list[NDArray[np.float64]] = []
    L = linje.langd
    for (ax, ay), (bx, by) in linje.kanter:
        lk = math.dist((ax, ay), (bx, by))
        n = max(1, math.ceil(lk / s - 1e-9))
        t = (np.arange(n, dtype=np.float64) + 0.5) / n
        xs.append(ax + (bx - ax) * t)
        ys.append(ay + (by - ay) * t)
        ws.append(np.full(n, vikt * (lk / n) / L))
        nx, ny = (by - ay) / lk, -(bx - ax) / lk
        ns.append(np.tile([nx, ny], (n, 1)))
    x = np.concatenate(xs)
    y = np.concatenate(ys)
    w = np.concatenate(ws)
    nrm = np.concatenate(ns)
    kvar = np.ones(x.size, dtype=np.bool_)
    cell = np.full(x.size, -1, dtype=np.int64)
    ut_mask = np.zeros(x.size, dtype=np.bool_)
    ogiltig = np.zeros(x.size, dtype=np.bool_)
    oppn = np.zeros(x.size, dtype=np.bool_)
    m = min(dx, dy)
    for steg in _STEG_UT:
        if not np.any(kvar):
            break
        d = steg * m + 1e-6
        qx = x + nrm[:, 0] * d
        qy = y + nrm[:, 1] * d
        kol = np.floor((qx - geo.x_min) / dx).astype(np.int64)
        rad = np.floor((geo.y_max - qy) / dy).astype(np.int64)
        utanfor = (kol < 0) | (kol >= bredd) | (rad < 0) | (rad >= hojd)
        ny_ut = kvar & utanfor
        ut_mask |= ny_ut
        kvar &= ~ny_ut
        if not np.any(kvar):
            break
        r = np.clip(rad, 0, hojd - 1)
        c = np.clip(kol, 0, bredd - 1)
        g = tak_grupp[r, c]
        ok = mark_ok[r, c]
        if oppning is not None:
            i_opp = kvar & ok & oppning[r, c]
            oppn |= i_opp
            kvar &= ~i_opp
            ok = ok & ~oppning[r, c]
        traff = kvar & ok
        cell[traff] = (r * bredd + c)[traff]
        kvar &= ~traff
        # Egen byggnad: fortsätt utåt. Annan byggnad eller cell utan höjd: ogiltigt.
        annan = kvar & (g != grupp)
        ogiltig |= annan
        kvar &= ~annan
    ogiltig |= kvar  # fastnade i egna byggnaden efter alla steg
    giltig = cell >= 0
    return _Prov(
        celler=cell[giltig],
        vikter=w[giltig],
        ut=float(np.sum(w[ut_mask])),
        ogiltiga=int(np.count_nonzero(ogiltig)),
        i_oppning=int(np.count_nonzero(oppn)),
    )


def _cellpolygon(mask: NDArray[np.bool_], x0: float, y0: float, dx: float, dy: float) -> Any:
    """Polygon (med hål) för en fyrgrannsammanhängande cellgrupp, i världskoordinater."""
    import rasterio.features
    from affine import Affine
    from shapely.geometry import shape
    from shapely.ops import unary_union

    trans = Affine(dx, 0.0, x0, 0.0, -dy, y0)
    delar = [
        shape(g)
        for g, v in rasterio.features.shapes(
            mask.astype(np.uint8), mask=mask, connectivity=4, transform=trans
        )
        if v == 1
    ]
    poly = unary_union(delar) if len(delar) > 1 else delar[0]
    if poly.geom_type != "Polygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    return poly.simplify(0.75 * max(dx, dy), preserve_topology=True)


def _punkter_i_polygon(
    xx: NDArray[np.float64], yy: NDArray[np.float64], ring: Sequence[tuple[float, float]]
) -> NDArray[np.bool_]:
    """Strålkastning (jämn-udda), samma regel som markmodellens inbränning av byggnader."""
    innanfor = np.zeros(xx.shape, dtype=bool)
    pts = list(ring)
    if len(pts) > 3 and pts[0] == pts[-1]:
        pts = pts[:-1]
    m = len(pts)
    for i in range(m):
        e1, n1 = pts[i]
        e2, n2 = pts[(i + 1) % m]
        korsar = (n1 > yy) != (n2 > yy)
        if not np.any(korsar):
            continue
        lut = (e2 - e1) / ((n2 - n1) if n2 != n1 else 1e-18)
        innanfor ^= korsar & (xx < e1 + (yy - n1) * lut)
    return innanfor


def bygg_takvatten(
    mark_z: NDArray[Any],
    markslag: NDArray[np.uint8],
    geo: GeoTransformInfo,
    *,
    byggnader: Sequence[TakPolygon] = (),
    andel_ledning: float = 0.0,
    oppning_mask: NDArray[np.bool_] | None = None,
) -> TakvattenFordelning:
    """Bygger takvattnets fördelning för ett raster (efter öppningar) och dess byggnader.

    `oppning_mask` markerar celler som en öppning (port) gjort till mark; dit läggs inget
    takvatten (redovisas i flaggorna).
    """
    from scipy import ndimage
    from shapely.geometry import Polygon

    andel = float(andel_ledning)
    if not (math.isfinite(andel) and 0.0 <= andel <= 1.0):
        raise TakvattenFel(f"takvatten_andel_ledning måste ligga i [0, 1], fick {andel_ledning}.")
    slag = np.asarray(markslag, dtype=np.uint8)
    z = np.asarray(mark_z, dtype=np.float64)
    if slag.shape != z.shape or slag.shape != (geo.hojd, geo.bredd):
        raise TakvattenFel("mark_z, markslag och geotransformen har olika form.")
    form = (int(slag.shape[0]), int(slag.shape[1]))
    dx, dy = float(geo.pixelstorlek_x), float(geo.pixelstorlek_y)
    cell_area = dx * dy
    tak = slag == MARKSLAG_BYGGNAD
    if not np.any(tak):
        return tom_fordelning(form, cell_area, andel)
    mark_ok = np.asarray(~tak & np.isfinite(z), dtype=np.bool_)
    if oppning_mask is not None and oppning_mask.shape != form:
        raise TakvattenFel(f"oppning_mask har form {oppning_mask.shape}, rastret {form}.")
    hojd, bredd = form

    # 1. Takceller per registerbyggnad (cellmitt i polygonen, samma regel som inbränningen).
    etikett = np.full(form, -1, dtype=np.int64)
    poster = sorted(byggnader, key=lambda b: b.byggnad_id)
    flaggor: dict[str, int] = {}

    def flagga(namn: str, antal: int = 1) -> None:
        if antal:
            flaggor[namn] = flaggor.get(namn, 0) + int(antal)

    for i, b in enumerate(poster):
        xs = [p[0] for p in b.polygon]
        ys = [p[1] for p in b.polygon]
        c0 = max(0, int(math.floor((min(xs) - geo.x_min) / dx)) - 1)
        c1 = min(bredd, int(math.ceil((max(xs) - geo.x_min) / dx)) + 1)
        r0 = max(0, int(math.floor((geo.y_max - max(ys)) / dy)) - 1)
        r1 = min(hojd, int(math.ceil((geo.y_max - min(ys)) / dy)) + 1)
        if c1 <= c0 or r1 <= r0:
            continue
        cx = geo.x_min + (np.arange(c0, c1) + 0.5) * dx
        cy = geo.y_max - (np.arange(r0, r1) + 0.5) * dy
        xx, yy = np.meshgrid(cx, cy)
        inne = _punkter_i_polygon(xx, yy, b.polygon)
        for h in b.hal:
            inne &= ~_punkter_i_polygon(xx, yy, h)
        sub = etikett[r0:r1, c0:c1]
        tak_sub = tak[r0:r1, c0:c1]
        flagga("takceller_i_flera_polygoner", int(np.count_nonzero(inne & tak_sub & (sub >= 0))))
        sub[inne & tak_sub & (sub < 0)] = i

    # 2. Byggnadsceller utan polygon: närmaste polygonbyggnad i samma cellgrupp, annars en
    #    egen byggnad ur cellerna (rastret).
    komp, antal_komp = ndimage.label(tak, structure=ndimage.generate_binary_structure(2, 1))
    utan = tak & (etikett < 0)
    if np.any(utan) and np.any(etikett >= 0):
        _, (ri, ci) = ndimage.distance_transform_edt(etikett < 0, return_indices=True)
        narmast = etikett[ri, ci]
        samma = komp[ri, ci] == komp
        tilldela = utan & samma & (narmast >= 0)
        flagga("takceller_utan_polygon_till_granne", int(np.count_nonzero(tilldela)))
        etikett[tilldela] = narmast[tilldela]
    grupper: list[tuple[str, str, Any, TakPolygon | None]] = []  # (id, kalla, poly, post)
    grupp_av_etikett: dict[int, int] = {}
    for i, b in enumerate(poster):
        if np.any(etikett == i):
            grupp_av_etikett[i] = len(grupper)
            grupper.append((b.byggnad_id, "register", Polygon(b.polygon, list(b.hal)), b))
    tak_grupp = np.full(form, -1, dtype=np.int64)
    for i, g in grupp_av_etikett.items():
        tak_grupp[etikett == i] = g
    rest = tak & (tak_grupp < 0)
    if np.any(rest):
        rk, n_rk = ndimage.label(rest, structure=ndimage.generate_binary_structure(2, 1))
        objekt = ndimage.find_objects(rk)
        for j, sl in enumerate(objekt, start=1):
            if sl is None:
                continue
            m = rk[sl] == j
            r0, c0 = sl[0].start, sl[1].start
            poly = _cellpolygon(m, geo.x_min + c0 * dx, geo.y_max - r0 * dy, dx, dy)
            g = len(grupper)
            tak_grupp[sl][m] = g
            grupper.append((f"raster_{r0}_{c0}", "raster", poly, None))
        flagga("byggnader_ur_rastret", n_rk)

    # 3. Fördelning per byggnad.
    mal_c: list[NDArray[np.int64]] = []
    mal_g: list[NDArray[np.int64]] = []
    mal_w: list[NDArray[np.float64]] = []
    ut_vikt = np.zeros(len(grupper), dtype=np.float64)
    rapporter: list[ByggnadsTakvatten] = []
    for g, (bid, kalla, poly, post) in enumerate(grupper):
        takform = post.takform if post is not None else None
        klass, okand_text = tolka_takform(takform)
        if okand_text:
            flagga("okand_takform_text")
        if klass == TAKFORM_OKAND:
            flagga("takform_okand")
        azimut = post.takfall_azimut_grader if post is not None else None
        linjer = _linjer(poly)
        regel, vikter, anm = _linjevikter(poly, linjer, klass, azimut)
        if anm:
            flagga("takform_ersatt")
        prov = {
            i: _prova_linje(
                linjer[i], v, grupp=g, tak_grupp=tak_grupp, mark_ok=mark_ok,
                oppning=oppning_mask, geo=geo,
            )
            for i, v in vikter.items()
        }
        flagga("prov_i_oppning_undvikna", sum(p.i_oppning for p in prov.values()))
        flagga("prov_mot_annan_byggnad_eller_utan_hojd", sum(p.ogiltiga for p in prov.values()))
        # Linjer utan något giltigt prov: deras andel går till byggnadens övriga linjer i
        # samma regelgrupp (för ring: samma sida), annars till alla övriga.
        mottaget = {i: float(np.sum(p.vikter)) + p.ut for i, p in prov.items()}
        mottagit = mottaget.__getitem__

        tomma = [i for i in vikter if mottagit(i) <= 0.0]
        if tomma:
            flagga("fasadlinjer_utan_mark", len(tomma))
            anm.append(f"{len(tomma)} takfotslinje(r) utan mark utanför; andelen går till övriga")
        andel_linje = dict(vikter)
        if regel == REGEL_RING:
            for sida in (0, 1):
                ids = [i for i in vikter if (linjer[i].ring > 0) == bool(sida)]
                tot = sum(vikter[i] for i in ids)
                ok = [i for i in ids if i not in tomma]
                okv = sum(vikter[i] for i in ok)
                for i in ids:
                    andel_linje[i] = vikter[i] / okv * tot if (i in ok and okv > 0) else 0.0
        ok_alla = [i for i in vikter if i not in tomma]
        tot_ok = sum(andel_linje[i] for i in ok_alla)
        celler: list[NDArray[np.int64]] = []
        vikt_c: list[NDArray[np.float64]] = []
        ut_g = 0.0
        if tot_ok > 0.0:
            for i in ok_alla:
                p = prov[i]
                skala = andel_linje[i] / tot_ok / mottagit(i)
                celler.append(p.celler)
                vikt_c.append(p.vikter * skala)
                ut_g += p.ut * skala
        else:
            # Ingen takfot når mark: alla fasader, sedan cellgruppens fasadceller.
            regel_fore = regel
            alla = {i: li.langd for i, li in enumerate(linjer)}
            tot_l = sum(alla.values())
            prov2 = {
                i: _prova_linje(
                    linjer[i], v / tot_l, grupp=g, tak_grupp=tak_grupp, mark_ok=mark_ok,
                    oppning=oppning_mask, geo=geo,
                )
                for i, v in alla.items()
            }
            tot2 = sum(float(np.sum(p.vikter)) + p.ut for p in prov2.values())
            if tot2 > 0.0:
                regel = REGEL_PLATT
                anm.append(f"{regel_fore}: ingen takfot når mark; alla fasader används")
                flagga("byggnader_takfot_saknas_alla_fasader")
                for p in prov2.values():
                    celler.append(p.celler)
                    vikt_c.append(p.vikter / tot2)
                    ut_g += p.ut / tot2
            else:
                regel = REGEL_KOMPLEX
                anm.append("ingen egen fasad mot mark; vattnet går till byggnadsgruppens fasader")
                flagga("byggnader_via_grannbyggnad")
                kmask = komp == komp[tak_grupp == g][0]
                kors = ndimage.generate_binary_structure(2, 1)
                kant = ndimage.binary_dilation(kmask, structure=kors) & ~kmask & mark_ok
                if oppning_mask is not None:
                    kant &= ~oppning_mask
                idx = np.flatnonzero(kant)
                if idx.size == 0:
                    rand = (
                        kmask[0, :].any() or kmask[-1, :].any()
                        or kmask[:, 0].any() or kmask[:, -1].any()
                    )
                    if not rand:
                        raise TakvattenFel(
                            f"Byggnad {bid}: taket har ingen väg till mark (ingen markcell runt "
                            "byggnadsgruppen); takvattnet kan inte fördelas."
                        )
                    ut_g = 1.0
                else:
                    celler.append(idx.astype(np.int64))
                    vikt_c.append(np.full(idx.size, 1.0 / idx.size))
        unika: NDArray[np.int64]
        summa: NDArray[np.float64]
        if celler:
            alla_c = np.concatenate(celler)
            alla_w = np.concatenate(vikt_c)
            u, inv = np.unique(alla_c, return_inverse=True)
            unika = np.asarray(u, dtype=np.int64)
            summa = np.asarray(np.bincount(inv, weights=alla_w), dtype=np.float64)
        else:
            unika = np.zeros(0, dtype=np.int64)
            summa = np.zeros(0, dtype=np.float64)
        # Exakt summa 1 per byggnad (avrundningen läggs på den största vikten).
        total = float(np.sum(summa)) + ut_g
        if total <= 0.0:
            raise TakvattenFel(f"Byggnad {bid}: tom fördelning.")
        summa = summa / total
        ut_g = ut_g / total
        if summa.size:
            k = int(np.argmax(summa))
            summa[k] += 1.0 - (float(np.sum(summa)) + ut_g)
        mal_c.append(unika.astype(np.int64))
        mal_g.append(np.full(unika.size, g, dtype=np.int64))
        mal_w.append(summa)
        ut_vikt[g] = ut_g
        n_tak = int(np.count_nonzero(tak_grupp == g))
        linjeposter = tuple(
            {
                "ring": "inner" if linjer[i].ring > 0 else "yttre",
                "langd_m": round(linjer[i].langd, 6),
                "normal_azimut_grader": round(
                    math.degrees(math.atan2(linjer[i].normal()[0], linjer[i].normal()[1])) % 360.0,
                    3,
                ),
                "andel": andel_linje.get(i, 0.0),
            }
            for i in sorted(vikter)
        )
        rapporter.append(
            ByggnadsTakvatten(
                byggnad_id=bid,
                kalla=kalla,
                takform=takform,
                regel=regel,
                tak_celler=n_tak,
                takyta_m2=n_tak * cell_area,
                andel_till_mark=float(np.sum(summa)),
                andel_ut_ur_rutan=ut_g,
                linjer=linjeposter,
                anmarkningar=tuple(anm),
            )
        )
    return TakvattenFordelning(
        form=form,
        cell_area_m2=cell_area,
        andel_ledning=andel,
        tak_grupp=tak_grupp,
        mal_cell=np.concatenate(mal_c) if mal_c else np.zeros(0, dtype=np.int64),
        mal_grupp=np.concatenate(mal_g) if mal_g else np.zeros(0, dtype=np.int64),
        mal_vikt=np.concatenate(mal_w) if mal_w else np.zeros(0, dtype=np.float64),
        ut_vikt=ut_vikt,
        byggnader=tuple(rapporter),
        flaggor=flaggor,
    )


# --- K1-rutor -------------------------------------------------------------------------------


def las_takpolygoner(byggnader_gpkg: str | Path) -> tuple[TakPolygon, ...]:
    """Byggnaderna ur `byggnader.gpkg` (K1 v2) med `takform` och, om kolumnen finns,
    `takfall_azimut_grader` (valfri, för pulpettak)."""
    import sqlite3

    from markmodell.byggnadsregister import LAGER_BYGGNADER, las_byggnader_gpkg

    fil = Path(byggnader_gpkg)
    poster = las_byggnader_gpkg(fil)
    azimut: dict[str, float | None] = {}
    conn = sqlite3.connect(f"file:{fil}?mode=ro", uri=True)
    try:
        kol = {r[1] for r in conn.execute(f"PRAGMA table_info({LAGER_BYGGNADER})")}
        if "takfall_azimut_grader" in kol:
            for bid, az in conn.execute(
                f"SELECT byggnad_id, takfall_azimut_grader FROM {LAGER_BYGGNADER}"
            ):
                azimut[str(bid)] = None if az is None else float(az)
    finally:
        conn.close()
    return tuple(
        TakPolygon(
            byggnad_id=p.byggnad_id,
            polygon=tuple(p.polygon),
            hal=tuple(tuple(r) for r in p.hal),
            takform=p.takform,
            takfall_azimut_grader=azimut.get(p.byggnad_id),
        )
        for p in poster
    )


def takvatten_for_k1_mapp(
    k1_mapp: str | Path,
    mark_eff: NDArray[Any],
    markslag_eff: NDArray[np.uint8],
    geo: GeoTransformInfo,
    *,
    andel_ledning: float = 0.0,
) -> TakvattenFordelning:
    """Takvattnet för en K1-ruta: byggnader ur `byggnader.gpkg` (om v2) och öppningarnas
    celler ur skillnaden mellan rutans `markslag.tif` och det effektiva markslaget."""
    from testsvit.k1_io import las_geotiff

    mapp = Path(k1_mapp)
    gpkg = mapp / "byggnader.gpkg"
    poster = las_takpolygoner(gpkg) if gpkg.is_file() else ()
    oppning = None
    slag_fil = mapp / "markslag.tif"
    if slag_fil.is_file():
        raa, _ = las_geotiff(slag_fil)
        raa8 = np.asarray(raa, dtype=np.uint8)
        if raa8.shape == markslag_eff.shape:
            oppning = np.asarray(
                (raa8 == MARKSLAG_BYGGNAD) & (markslag_eff != MARKSLAG_BYGGNAD), dtype=np.bool_
            )
    return bygg_takvatten(
        mark_eff, markslag_eff, geo, byggnader=poster, andel_ledning=andel_ledning,
        oppning_mask=oppning,
    )


def med_takvatten(graf: Any, takvatten: TakvattenFordelning | None) -> Any:
    """Kopia av `SankGraf` med takvattnet (kontrollerat mot grafens effektiva raster)."""
    import dataclasses

    if takvatten is not None:
        takvatten.kontrollera_mot(graf.markslag, graf.mark_z)
    return dataclasses.replace(graf, takvatten=takvatten)


def med_takvatten_graf(
    graf: Any, k1_mapp: str | Path | None = None, *, andel_ledning: float = 0.0
) -> Any:
    """Bygger takvattnet ur grafens effektiva raster (och K1-rutans byggnader om `k1_mapp`)."""
    if k1_mapp is not None:
        tak = takvatten_for_k1_mapp(
            k1_mapp, graf.mark_z, graf.markslag, graf.geo, andel_ledning=andel_ledning
        )
    else:
        tak = bygg_takvatten(graf.mark_z, graf.markslag, graf.geo, andel_ledning=andel_ledning)
    return med_takvatten(graf, tak)
