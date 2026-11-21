"""Horisontprofiler h(phi) över ett höjdfält (DSM = mark + byggnader).

Modell: höjdfältet är ett prismafält, varje cell är en rektangulär pelare med konstant höjd.
Från en punkt (x, y, z0) i azimut phi (norr = 0, medurs) är horisontvinkeln

    h(phi) = max(0, max över celler c som strålen korsar: atan((z_c - z0) / s_c))

där s_c är avståndet längs strålen (horisontellt) till den punkt där strålen går in i cellen.
För en prismacell är ingångspunkten den som ger störst vinkel, så modellen är exakt för
pelare (ingen punktsampling, ingen interpolation av kanter). Startcellen hoppas över.
Strålar avbryts vid `max_avstand_m`; strålar som lämnar fältet före det flaggas (`trunkerad`).

Tre implementationer med samma definition:

- `horisont_referens`: vektoriserad Amanatides–Woo-genomgång (cell för cell). Långsam men
  rättfram; facit för de andra.
- `horisont_riktning_ruta`: alla cellmittpunkter i en riktning på en gång. Alla mittpunkter ser
  samma relativa cellföljd, så den beräknas en gång och tillämpas som förskjutna skivor.
- `horisont_mipmap`: godtyckliga punkter (fasadpunkter) med hierarkisk genomgång över en
  max-pyramid; block hoppas över när deras högsta cell inte kan ge större vinkel än den hittills
  bästa. Ger samma resultat som referensen (testas).

Koordinater: EPSG:3011, rad 0 = norr. Cell (i, j) har mittpunkt
x = x_min + (j + 0,5)·cell_m, y = y_max - (i + 0,5)·cell_m.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

Falt = NDArray[np.float64]

# Två skärningar i cellkoordinater (cellenheter) närmare än så räknas som träff i hörnet,
# och strålen går diagonalt utan att räkna den mellanliggande cellen (noll genomskärningslängd).
TIE_TOL = 1e-9
_T_MIN = (
    1e-9  # minsta ingångsavstånd (cellenheter): punkt exakt på cellkant, hinder på noll avstånd
)
_AXEL_NOLL = 1e-12  # |riktningskomponent| under detta räknas som 0 (strålen parallell med axeln)


class HorisontFel(Exception):
    """Ogiltig indata till horisontberäkningen. Aldrig tyst: hellre fel än fel resultat."""


@dataclass(frozen=True)
class Hojdfalt:
    """Höjdfält (m, RH 2000) på ett regelbundet rutnät, rad 0 = norr."""

    z_m: Falt
    cell_m: float
    x_min_m: float
    y_max_m: float

    def __post_init__(self) -> None:
        if self.z_m.ndim != 2 or self.z_m.size == 0:
            raise HorisontFel(f"Höjdfältet måste vara 2D och icke-tomt, fick {self.z_m.shape}.")
        if not (math.isfinite(self.cell_m) and self.cell_m > 0):
            raise HorisontFel(f"cell_m måste vara > 0, fick {self.cell_m}.")
        icke_andlig = int(np.size(self.z_m) - np.count_nonzero(np.isfinite(self.z_m)))
        if icke_andlig:
            raise HorisontFel(
                f"Höjdfältet har {icke_andlig} celler som inte är ändliga tal (NaN/inf). "
                "Fyll eller maska dem före horisontberäkningen; de ska inte gissas."
            )

    @property
    def hojd(self) -> int:
        return int(self.z_m.shape[0])

    @property
    def bredd(self) -> int:
        return int(self.z_m.shape[1])

    def cellkoord(self, x_m: Falt, y_m: Falt) -> tuple[Falt, Falt]:
        """(kolumn, rad) som flyttal i cellenheter (cell (i, j) täcker [j, j+1) x [i, i+1))."""
        return (
            np.asarray((x_m - self.x_min_m) / self.cell_m, dtype=np.float64),
            np.asarray((self.y_max_m - y_m) / self.cell_m, dtype=np.float64),
        )


@dataclass(frozen=True)
class StralHorisont:
    """Resultat per stråle."""

    h_rad: Falt  # horisontvinkel, >= 0
    trunkerad: NDArray[np.bool_]  # strålen lämnade höjdfältet före max_avstand
    inuti_hinder: NDArray[np.bool_]  # startpunkten ligger under höjden i sin egen cell


def riktningar_rad(antal: int) -> Falt:
    """`antal` jämnt fördelade azimuter (norr = 0, medurs), phi_k = 2 pi k / antal."""
    if isinstance(antal, bool) or not isinstance(antal, int) or antal < 4:
        raise HorisontFel(f"Antal riktningar måste vara ett heltal >= 4, fick {antal!r}.")
    return np.asarray(2.0 * math.pi * np.arange(antal) / antal, dtype=np.float64)


def _komponenter(phi_rad: Falt) -> tuple[Falt, Falt]:
    """Riktningsvektor i (kolumn, rad)-led: öster = +kolumn, norr = -rad."""
    ex = np.sin(phi_rad)
    ey = -np.cos(phi_rad)
    ex = np.where(np.abs(ex) < _AXEL_NOLL, 0.0, ex)
    ey = np.where(np.abs(ey) < _AXEL_NOLL, 0.0, ey)
    return np.asarray(ex, dtype=np.float64), np.asarray(ey, dtype=np.float64)


def _validera_avstand(max_avstand_m: float) -> None:
    if not (math.isfinite(max_avstand_m) and max_avstand_m > 0):
        raise HorisontFel(f"max_avstand_m måste vara > 0, fick {max_avstand_m}.")


def stralsekvens(
    phi_rad: float, rmax_celler: float
) -> tuple[NDArray[np.int64], NDArray[np.int64], NDArray[np.float64]]:
    """Cellföljden (dj, di) och ingångsavstånd (cellenheter) för en stråle från en cellmittpunkt.

    Samma följd gäller för alla cellmittpunkter. Startcellen ingår inte.
    """
    ex, ey = (float(v[0]) for v in _komponenter(np.array([phi_rad])))
    return _sekvens_enkel(ex, ey, rmax_celler)


def _sekvens_enkel(
    ex: float, ey: float, rmax_celler: float
) -> tuple[NDArray[np.int64], NDArray[np.int64], NDArray[np.float64]]:
    sx = 1 if ex > 0 else -1
    sy = 1 if ey > 0 else -1
    tdx = math.inf if ex == 0.0 else 1.0 / abs(ex)
    tdy = math.inf if ey == 0.0 else 1.0 / abs(ey)
    tmx, tmy = 0.5 * tdx, 0.5 * tdy
    dj = di = 0
    dj_l: list[int] = []
    di_l: list[int] = []
    t_l: list[float] = []
    while True:
        t = min(tmx, tmy)
        if t > rmax_celler or math.isinf(t):
            break
        tie = abs(tmx - tmy) <= TIE_TOL
        steg_x = tie or tmx < tmy
        steg_y = tie or tmy < tmx
        if steg_x:
            dj += sx
            tmx += tdx
        if steg_y:
            di += sy
            tmy += tdy
        dj_l.append(dj)
        di_l.append(di)
        t_l.append(t)
    return (
        np.asarray(dj_l, dtype=np.int64),
        np.asarray(di_l, dtype=np.int64),
        np.asarray(t_l, dtype=np.float64),
    )


def _trunkering(
    falt: Hojdfalt, ox: Falt, oy: Falt, ex: Falt, ey: Falt, rmax_celler: float
) -> NDArray[np.bool_]:
    """Sant om strålen lämnar fältet på ett avstånd < rmax (analytiskt, ej genomgången)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        tx = np.where(
            ex > 0, (falt.bredd - ox) / ex, np.where(ex < 0, ox / -np.where(ex < 0, ex, 1), np.inf)
        )
        ty = np.where(
            ey > 0, (falt.hojd - oy) / ey, np.where(ey < 0, oy / -np.where(ey < 0, ey, 1), np.inf)
        )
    return np.asarray(np.minimum(tx, ty) < rmax_celler, dtype=np.bool_)


def _kontrollera_start(falt: Hojdfalt, ox: Falt, oy: Falt) -> None:
    ute = ~((ox >= 0) & (ox < falt.bredd) & (oy >= 0) & (oy < falt.hojd))
    if ute.any():
        raise HorisontFel(
            f"{int(ute.sum())} punkter ligger utanför höjdfältet (ingen extrapolering)."
        )


def _starttillstand(
    falt: Hojdfalt, ox: Falt, oy: Falt, ex: Falt, ey: Falt
) -> tuple[NDArray[np.int64], NDArray[np.int64], Falt, Falt]:
    """Startcell (rad, kol) och cellkantsavstånd tmx, tmy till första kantkorsningen."""
    cj = np.floor(ox).astype(np.int64)
    ci = np.floor(oy).astype(np.int64)
    with np.errstate(divide="ignore", invalid="ignore"):
        tmx = np.where(
            ex > 0,
            (cj + 1 - ox) / ex,
            np.where(ex < 0, (ox - cj) / -np.where(ex < 0, ex, 1), np.inf),
        )
        tmy = np.where(
            ey > 0,
            (ci + 1 - oy) / ey,
            np.where(ey < 0, (oy - ci) / -np.where(ey < 0, ey, 1), np.inf),
        )
    return ci, cj, np.asarray(tmx, dtype=np.float64), np.asarray(tmy, dtype=np.float64)


def _tdelta(e: Falt) -> Falt:
    with np.errstate(divide="ignore"):
        return np.asarray(
            np.where(e == 0.0, np.inf, 1.0 / np.abs(np.where(e == 0.0, 1, e))), dtype=np.float64
        )


def horisont_referens(
    falt: Hojdfalt,
    x_m: Falt,
    y_m: Falt,
    z0_m: Falt,
    phi_rad: Falt,
    max_avstand_m: float,
) -> StralHorisont:
    """Referensimplementation: en stråle per element i `x_m, y_m, z0_m, phi_rad` (lika långa)."""
    _validera_avstand(max_avstand_m)
    x_m, y_m, z0_m, phi_rad = (
        np.atleast_1d(np.asarray(a, dtype=np.float64)) for a in (x_m, y_m, z0_m, phi_rad)
    )
    if not (x_m.shape == y_m.shape == z0_m.shape == phi_rad.shape) or x_m.ndim != 1:
        raise HorisontFel("x, y, z0 och phi måste vara endimensionella och lika långa.")
    n = x_m.size
    rmax_c = max_avstand_m / falt.cell_m
    ox, oy = falt.cellkoord(x_m, y_m)
    _kontrollera_start(falt, ox, oy)
    ex, ey = _komponenter(phi_rad)
    ci, cj, tmx, tmy = _starttillstand(falt, ox, oy, ex, ey)
    tdx, tdy = _tdelta(ex), _tdelta(ey)
    sx = np.where(ex > 0, 1, -1).astype(np.int64)
    sy = np.where(ey > 0, 1, -1).astype(np.int64)
    inuti = falt.z_m[ci, cj] > z0_m + 1e-9
    best = np.zeros(n)
    idx = np.arange(n)
    max_steg = int(2 * rmax_c) + 8
    for _ in range(max_steg):
        if idx.size == 0:
            break
        t = np.minimum(tmx, tmy)
        ok = (t <= rmax_c) & np.isfinite(t)
        tie = np.abs(tmx - tmy) <= TIE_TOL
        steg_x = tie | (tmx < tmy)
        steg_y = tie | (tmy < tmx)
        cj = cj + np.where(steg_x, sx[idx], 0)
        ci = ci + np.where(steg_y, sy[idx], 0)
        tmx = np.where(steg_x, tmx + tdx[idx], tmx)
        tmy = np.where(steg_y, tmy + tdy[idx], tmy)
        ok &= (cj >= 0) & (cj < falt.bredd) & (ci >= 0) & (ci < falt.hojd)
        if not ok.all():
            idx, ci, cj, tmx, tmy, t = (a[ok] for a in (idx, ci, cj, tmx, tmy, t))
            if idx.size == 0:
                break
        slope = (falt.z_m[ci, cj] - z0_m[idx]) / (np.maximum(t, _T_MIN) * falt.cell_m)
        best[idx] = np.maximum(best[idx], slope)
    else:
        if idx.size:
            raise HorisontFel("Referensgenomgången konvergerade inte (internt fel).")
    return StralHorisont(
        h_rad=np.arctan(best),
        trunkerad=_trunkering(falt, ox, oy, ex, ey, rmax_c),
        inuti_hinder=np.asarray(inuti, dtype=np.bool_),
    )


def horisont_riktning_ruta(
    falt: Hojdfalt,
    phi_rad: float,
    max_avstand_m: float,
    *,
    fonster: tuple[int, int, int, int] | None = None,
    radblock: int = 64,
    dtyp: type[np.float32] | type[np.float64] = np.float32,
) -> tuple[NDArray[np.float32], NDArray[np.bool_]]:
    """Horisontvinkel (rad) och trunkeringsflagga för cellmittpunkter i en riktning.

    `fonster = (rad0, rad1, kol0, kol1)` begränsar vilka celler som beräknas (källa: hela
    fältet); utan fönster beräknas alla. Cellmittpunkten är punktens läge och z0 = höjden i den
    egna cellen (yta ovanpå marken eller taket). Resultatet är identiskt med
    `horisont_referens` i cellmittpunkterna (testat) inom flyttalsprecisionen för `dtyp`.
    """
    _validera_avstand(max_avstand_m)
    z = falt.z_m.astype(dtyp)
    ny, nx = z.shape
    r_a, r_b, c_a, c_b = fonster if fonster is not None else (0, ny, 0, nx)
    if not (0 <= r_a < r_b <= ny and 0 <= c_a < c_b <= nx):
        raise HorisontFel(f"Ogiltigt fönster {fonster} för fält {ny}x{nx}.")
    rmax_c = max_avstand_m / falt.cell_m
    dj, di, t = stralsekvens(phi_rad, rmax_c)
    w = (1.0 / (t * falt.cell_m)).astype(dtyp)
    best = np.zeros((r_b - r_a, c_b - c_a), dtype=dtyp)
    for r0 in range(r_a, r_b, radblock):
        r1 = min(r_b, r0 + radblock)
        zb = z[r0:r1, c_a:c_b]
        bb = best[r0 - r_a : r1 - r_a]
        buf = np.empty((r1 - r0, c_b - c_a), dtype=dtyp)
        for k in range(len(dj)):
            djk, dik = int(dj[k]), int(di[k])
            a0, a1 = max(r0, -dik), min(r1, ny - dik)
            q0, q1 = max(c_a, -djk), min(c_b, nx - djk)
            if a0 >= a1 or q0 >= q1:
                continue
            tgt = (slice(a0 - r0, a1 - r0), slice(q0 - c_a, q1 - c_a))
            v = buf[tgt]
            np.subtract(z[a0 + dik : a1 + dik, q0 + djk : q1 + djk], zb[tgt], out=v)
            np.multiply(v, w[k], out=v)
            np.maximum(bb[tgt], v, out=bb[tgt])
    h = np.arctan(best).astype(np.float32)
    ex, ey = (float(a[0]) for a in _komponenter(np.array([phi_rad])))
    jj, ii = np.meshgrid(np.arange(c_a, c_b) + 0.5, np.arange(r_a, r_b) + 0.5)
    trunk = _trunkering(falt, jj, ii, np.full_like(jj, ex), np.full_like(jj, ey), rmax_c)
    return h, trunk


def horisont_ruta_iter(
    falt: Hojdfalt, antal_riktningar: int, max_avstand_m: float, **kw: object
) -> Iterator[tuple[int, float, NDArray[np.float32], NDArray[np.bool_]]]:
    """Genererar `(k, phi_k, h_rad, trunkerad)` för alla riktningar (minnessnålt)."""
    for k, phi in enumerate(riktningar_rad(antal_riktningar)):
        h, trunk = horisont_riktning_ruta(falt, float(phi), max_avstand_m, **kw)  # type: ignore[arg-type]
        yield k, float(phi), h, trunk


@dataclass(frozen=True)
class MaxPyramid:
    """Max-pyramid över höjdfältet: nivå l har celler om 2^l x 2^l ursprungsceller."""

    nivaer: tuple[Falt, ...]

    @classmethod
    def bygg(cls, falt: Hojdfalt, max_niva: int = 9) -> MaxPyramid:
        niv = [falt.z_m]
        while len(niv) <= max_niva and max(niv[-1].shape) > 1:
            a = niv[-1]
            ph, pw = a.shape[0] % 2, a.shape[1] % 2
            if ph or pw:
                a = np.pad(a, ((0, ph), (0, pw)), constant_values=-np.inf)
            niv.append(
                np.maximum(
                    np.maximum(a[0::2, 0::2], a[1::2, 0::2]),
                    np.maximum(a[0::2, 1::2], a[1::2, 1::2]),
                )
            )
        return cls(tuple(niv))


def horisont_mipmap(
    falt: Hojdfalt,
    x_m: Falt,
    y_m: Falt,
    z0_m: Falt,
    phi_rad: Falt,
    max_avstand_m: float,
    *,
    pyramid: MaxPyramid | None = None,
) -> StralHorisont:
    """Samma resultat som `horisont_referens`, men med hierarkiska hopp över max-pyramiden.

    Ett block hoppas över när (blockets högsta cell - z0) / (avstånd till blockets ingång)
    inte överstiger bästa vinkeln hittills; då kan ingen cell i blocket (alla på större avstånd
    och lägre höjd) förbättra den. Annars delas blocket. Cellnivån (0) utvärderas exakt.
    """
    _validera_avstand(max_avstand_m)
    x_m, y_m, z0_m, phi_rad = (
        np.atleast_1d(np.asarray(a, dtype=np.float64)) for a in (x_m, y_m, z0_m, phi_rad)
    )
    if not (x_m.shape == y_m.shape == z0_m.shape == phi_rad.shape) or x_m.ndim != 1:
        raise HorisontFel("x, y, z0 och phi måste vara endimensionella och lika långa.")
    pyr = pyramid or MaxPyramid.bygg(falt)
    lmax = len(pyr.nivaer) - 1
    n = x_m.size
    rmax_c = max_avstand_m / falt.cell_m
    ox, oy = falt.cellkoord(x_m, y_m)
    _kontrollera_start(falt, ox, oy)
    ex, ey = _komponenter(phi_rad)
    ci, cj, tmx, tmy = _starttillstand(falt, ox, oy, ex, ey)
    inuti = falt.z_m[ci, cj] > z0_m + 1e-9
    trunk = _trunkering(falt, ox, oy, ex, ey, rmax_c)
    best = np.zeros(n)
    # Aktiv mängd i kompakta arrayer.
    idx = np.arange(n)
    t = np.minimum(tmx, tmy)  # ingång till första cellen efter startcellen
    lev = np.zeros(n, dtype=np.int64)
    a_ox, a_oy, a_ex, a_ey, a_z0 = ox, oy, ex, ey, z0_m
    delta = TIE_TOL
    max_iter = 40 * (int(rmax_c) + 100)
    for _ in range(max_iter):
        if idx.size == 0:
            break
        tn = t + delta
        cx = np.floor(a_ox + tn * a_ex).astype(np.int64)
        cy = np.floor(a_oy + tn * a_ey).astype(np.int64)
        ok = (t <= rmax_c) & np.isfinite(t) & (cx >= 0) & (cx < falt.bredd) & (cy >= 0)
        ok &= cy < falt.hojd
        if not ok.all():
            idx, t, tn, lev, a_ox, a_oy, a_ex, a_ey, a_z0, cx, cy = (
                a[ok] for a in (idx, t, tn, lev, a_ox, a_oy, a_ex, a_ey, a_z0, cx, cy)
            )
            if idx.size == 0:
                break
        bx = cx >> lev
        by = cy >> lev
        m = np.empty(idx.size)
        for niva in np.unique(lev):
            sel = lev == niva
            m[sel] = pyr.nivaer[int(niva)][by[sel], bx[sel]]
        b = (1 << lev).astype(np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            tx = np.where(
                a_ex > 0,
                ((bx + 1) * b - a_ox) / a_ex,
                np.where(a_ex < 0, (bx * b - a_ox) / np.where(a_ex < 0, a_ex, 1), np.inf),
            )
            ty = np.where(
                a_ey > 0,
                ((by + 1) * b - a_oy) / a_ey,
                np.where(a_ey < 0, (by * b - a_oy) / np.where(a_ey < 0, a_ey, 1), np.inf),
            )
        t_ut = np.maximum(np.minimum(tx, ty), tn)
        slope = (m - a_z0) / (np.maximum(t, _T_MIN) * falt.cell_m)
        cell = lev == 0
        # Cellnivå: utvärdera och gå vidare. Högre nivå: hoppa över eller dela.
        upd = cell | (slope > best[idx])
        best[idx] = np.where(cell, np.maximum(best[idx], slope), best[idx])
        gar_vidare = cell | ~upd
        delas = ~gar_vidare
        t = np.where(gar_vidare, t_ut, t)
        lev = np.where(gar_vidare, np.minimum(lev + 1, lmax), np.where(delas, lev - 1, lev))
    else:
        raise HorisontFel("Mipmap-genomgången konvergerade inte (internt fel).")
    return StralHorisont(
        h_rad=np.arctan(best), trunkerad=trunk, inuti_hinder=np.asarray(inuti, dtype=np.bool_)
    )


Metod = Literal["referens", "mipmap"]
