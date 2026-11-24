"""Mark i nivåer (R4): lutande yta, `delta_m`, exakt kantavstånd och slänt mot befintlig mark.

Design: `docs/design/RITVERKTYG.md` avsnitt 6. Används av `lapp.tillampa.tillampa_lapp` för en
`HojdYta` som har något av fälten `lutning`, `delta_m` eller slänt (`slant_forhallande`,
`slant_bredd_m`). En höjdyta utan dem tillämpas som förut (hård kant, cellcentrum i polygonen);
den vägen rörs inte här.

Skillnaderna mot den gamla cellmasken:

- **Exakt kantavstånd.** Avståndet från cellcentrum till polygonens kant (och den närmaste
  kantpunkten) räknas med polygonens exakta geometri, inte med vilka celler som ligger inne.
- **Ingen halvcellstrappa.** Utan slänt får celler som polygonen bara delvis täcker
  `z = f · z_ny + (1 − f) · z_bas`, där f är den exakta täckta andelen (shapely). Med slänt är
  höjden en kontinuerlig funktion av läget (platt eller lutande yta inne, rak ramp utanför), så
  det finns ingen kant att trappa.
- **Slänt.** Utanför polygonen, på avståndet d från närmaste kantpunkt p:
  `z = z_bas + Δ(p) · (1 − d / W(p))` för d < W(p), där Δ(p) = z_ny(p) − z_bas(p) och
  W(p) = |Δ(p)| · n (förhållande 1:n) eller en fast bredd. Slänten är rak med lutningen 1/n och
  möter basen utan hörn där kanten är rak. Där Δ ≈ 0 blir W ≈ 0 (inget att jämna).

Allt är deterministiskt (numpy och shapely, inga slumptal) och i EPSG:3011 och m RH 2000.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import shapely
from numpy.typing import NDArray

from lapp.kontrakt import HojdYta, LappFel
from sankgraf.typer import MARKSLAG_BYGGNAD
from testsvit.k1_io import GeoTransformInfo

GRAS = 4
"""Markslag på slänten om inget annat anges (K1-kod 4)."""
_EPS = 1e-9


# ---------------------------------------------------------------------------- yta i z


def _plankoefficienter(
    lutning: Mapping[str, Any],
) -> tuple[float, float, float, float, float] | None:
    """(x0, y0, z0, gx, gy) för plan-formerna, annars None (höjdlinjer)."""
    if "punkt" in lutning:
        x0, y0, z0 = (float(v) for v in lutning["punkt"])
        az = math.radians(float(lutning["riktning_grader"]))
        fall = float(lutning["fall"])
        return x0, y0, z0, -fall * math.sin(az), -fall * math.cos(az)
    if "a" in lutning:
        ax, ay, az_ = (float(v) for v in lutning["a"])
        bx, by, bz = (float(v) for v in lutning["b"])
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        k = (bz - az_) / l2
        return ax, ay, az_, k * dx, k * dy
    if "punkter" in lutning:
        p = np.array(lutning["punkter"], dtype=np.float64)
        x0, y0 = float(p[:, 0].mean()), float(p[:, 1].mean())
        a = np.column_stack([p[:, 0] - x0, p[:, 1] - y0, np.ones(len(p))])
        (gx, gy, z0), *_ = np.linalg.lstsq(a, p[:, 2], rcond=None)
        return x0, y0, float(z0), float(gx), float(gy)
    return None


def _narmaste_pa_linje(
    linje: Sequence[Sequence[float]], x: NDArray[np.float64], y: NDArray[np.float64]
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Avstånd och interpolerad z till närmaste punkt på polylinjen (E, N, z)."""
    best = np.full(x.shape, np.inf, dtype=np.float64)
    zb = np.full(x.shape, np.nan, dtype=np.float64)
    for i in range(len(linje) - 1):
        e0, n0, z0 = (float(v) for v in linje[i])
        e1, n1, z1 = (float(v) for v in linje[i + 1])
        de, dn = e1 - e0, n1 - n0
        l2 = de * de + dn * dn
        if l2 <= _EPS:
            continue
        t = np.clip(((x - e0) * de + (y - n0) * dn) / l2, 0.0, 1.0)
        d = np.hypot(x - (e0 + t * de), y - (n0 + t * dn))
        battre = d < best
        best = np.where(battre, d, best)
        zb = np.where(battre, z0 + t * (z1 - z0), zb)
    return best, zb


def lutning_z(
    lutning: Mapping[str, Any], x: NDArray[np.float64], y: NDArray[np.float64]
) -> NDArray[np.float64]:
    """Den lutande ytans höjd (m RH 2000) i (x, y); `lutning` enligt `normalisera_lutning`."""
    koeff = _plankoefficienter(lutning)
    if koeff is not None:
        x0, y0, z0, gx, gy = koeff
        return np.asarray(z0 + gx * (x - x0) + gy * (y - y0), dtype=np.float64)
    da, za = _narmaste_pa_linje(lutning["hojdlinje_a"], x, y)
    db, zb = _narmaste_pa_linje(lutning["hojdlinje_b"], x, y)
    summa = da + db
    with np.errstate(invalid="ignore", divide="ignore"):
        z = np.where(summa > _EPS, (za * db + zb * da) / summa, za)
    return np.asarray(z, dtype=np.float64)


def _z_ny(
    yta: HojdYta, x: NDArray[np.float64], y: NDArray[np.float64], z_bas: NDArray[np.float64]
) -> NDArray[np.float64]:
    """Ytans nivå i (x, y): plan, lutande yta eller basens höjd plus `delta_m`."""
    if yta.lutning is not None:
        return lutning_z(yta.lutning, x, y)
    if yta.delta_m is not None:
        return np.asarray(z_bas + float(yta.delta_m), dtype=np.float64)
    return np.full(x.shape, float(yta.hojd_rh2000 or 0.0), dtype=np.float64)


# ---------------------------------------------------------------------------- raster


def _bilinjar(
    z: NDArray[np.float64], geo: GeoTransformInfo, x: NDArray[np.float64], y: NDArray[np.float64]
) -> NDArray[np.float64]:
    """Bilinjär höjd i (x, y) ur cellcentrum; närmaste cell där en granne saknar höjd."""
    gx = (x - geo.x_min) / float(geo.pixelstorlek_x) - 0.5
    gy = (geo.y_max - y) / float(geo.pixelstorlek_y) - 0.5
    gx = np.clip(gx, 0.0, geo.bredd - 1.0)
    gy = np.clip(gy, 0.0, geo.hojd - 1.0)
    k0 = np.minimum(np.floor(gx).astype(np.int64), geo.bredd - 2) if geo.bredd > 1 else (
        np.zeros(gx.shape, dtype=np.int64)
    )
    r0 = np.minimum(np.floor(gy).astype(np.int64), geo.hojd - 2) if geo.hojd > 1 else (
        np.zeros(gy.shape, dtype=np.int64)
    )
    k1 = np.minimum(k0 + 1, geo.bredd - 1)
    r1 = np.minimum(r0 + 1, geo.hojd - 1)
    fx = gx - k0
    fy = gy - r0
    z00, z01, z10, z11 = z[r0, k0], z[r0, k1], z[r1, k0], z[r1, k1]
    bil = (z00 * (1 - fx) + z01 * fx) * (1 - fy) + (z10 * (1 - fx) + z11 * fx) * fy
    narmast = z[np.clip(np.rint(gy).astype(np.int64), 0, geo.hojd - 1),
                np.clip(np.rint(gx).astype(np.int64), 0, geo.bredd - 1)]
    ok = np.isfinite(z00) & np.isfinite(z01) & np.isfinite(z10) & np.isfinite(z11)
    return np.asarray(np.where(ok, bil, narmast), dtype=np.float64)


def _tata_kant(
    ring: Sequence[tuple[float, float]], steg_m: float
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Punkter längs polygonkanten med högst `steg_m` mellan sig (hörnen ingår)."""
    xs: list[float] = []
    ys: list[float] = []
    n = len(ring)
    for i in range(n):
        e0, n0 = ring[i]
        e1, n1 = ring[(i + 1) % n]
        antal = max(1, math.ceil(math.hypot(e1 - e0, n1 - n0) / steg_m))
        for j in range(antal):
            t = j / antal
            xs.append(e0 + t * (e1 - e0))
            ys.append(n0 + t * (n1 - n0))
    return np.array(xs, dtype=np.float64), np.array(ys, dtype=np.float64)


def _oppna_ring(polygon: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    ring = [(float(p[0]), float(p[1])) for p in polygon]
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring.pop()
    return ring


def kantavstand(
    ring: Sequence[tuple[float, float]],
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    geo: GeoTransformInfo,
    rad0: int,
    kol0: int,
    reach_m: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Exakt avstånd till polygonens kant och närmaste kantpunkt, för cellcentra i ett fönster.

    `x`, `y` är cellcentra för fönstret som börjar i (rad0, kol0). Bara celler närmare än
    `reach_m` får ett avstånd (övriga inf, och kantpunkten NaN). Varje kantsegment räknas bara på
    sin egen ruta i fönstret, så en polygon med många hörn inte kostar hörn · fönster.
    """
    h, b = x.shape
    dx, dy = float(geo.pixelstorlek_x), float(geo.pixelstorlek_y)
    best = np.full((h, b), np.inf, dtype=np.float64)
    fx = np.full((h, b), np.nan, dtype=np.float64)
    fy = np.full((h, b), np.nan, dtype=np.float64)
    n = len(ring)
    for i in range(n):
        e0, n0 = ring[i]
        e1, n1 = ring[(i + 1) % n]
        de, dn = e1 - e0, n1 - n0
        l2 = de * de + dn * dn
        e_lo, e_hi = min(e0, e1) - reach_m, max(e0, e1) + reach_m
        n_lo, n_hi = min(n0, n1) - reach_m, max(n0, n1) + reach_m
        k_a = max(0, math.floor((e_lo - geo.x_min) / dx - 0.5) - kol0)
        k_b = min(b, math.ceil((e_hi - geo.x_min) / dx - 0.5) + 1 - kol0)
        r_a = max(0, math.floor((geo.y_max - n_hi) / dy - 0.5) - rad0)
        r_b = min(h, math.ceil((geo.y_max - n_lo) / dy - 0.5) + 1 - rad0)
        if k_b <= k_a or r_b <= r_a:
            continue
        sx = x[r_a:r_b, k_a:k_b]
        sy = y[r_a:r_b, k_a:k_b]
        if l2 <= _EPS:
            px = np.full(sx.shape, e0)
            py = np.full(sx.shape, n0)
        else:
            t = np.clip(((sx - e0) * de + (sy - n0) * dn) / l2, 0.0, 1.0)
            px = e0 + t * de
            py = n0 + t * dn
        d = np.hypot(sx - px, sy - py)
        sb = best[r_a:r_b, k_a:k_b]
        battre = d < sb
        sb[battre] = d[battre]
        fx[r_a:r_b, k_a:k_b][battre] = px[battre]
        fy[r_a:r_b, k_a:k_b][battre] = py[battre]
    langt = best > reach_m
    best[langt] = np.inf
    fx[langt] = np.nan
    fy[langt] = np.nan
    return best, fx, fy


def tackningsandel(
    ring: Sequence[tuple[float, float]],
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    dx: float,
    dy: float,
) -> NDArray[np.float64]:
    """Exakt andel (0..1) av varje cell (centrum x, y; storlek dx · dy) som ligger i polygonen."""
    poly = shapely.Polygon(ring)
    if not poly.is_valid:
        poly = shapely.make_valid(poly)
    rutor = shapely.box(x - dx / 2, y - dy / 2, x + dx / 2, y + dy / 2)
    area = shapely.area(shapely.intersection(poly, rutor))
    return np.asarray(np.clip(area / (dx * dy), 0.0, 1.0), dtype=np.float64)


# ---------------------------------------------------------------------------- tillämpning


def tillampa_markniva(
    yta: HojdYta,
    mark_z: NDArray[np.float32],
    osakerhet_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
    geo: GeoTransformInfo,
    inuti: NDArray[np.bool_],
) -> NDArray[np.bool_]:
    """Lägger en höjdyta med `lutning`, `delta_m` och/eller slänt i rastren (muterar dem).

    `inuti` är polygonens cellmask (cellcentrum i polygonen, som `lapp.tillampa._polygon_mask`);
    den avgör bara vad som är innanför. Avstånd och täckning är exakta. Returnerar de celler som
    ytan rört (yta och slänt).
    """
    if yta.landxml is not None or len(yta.polygon) < 3:
        raise LappFel(f"HojdYta '{yta.yta_id}': markniva kräver en polygon och ingen landxml.")
    ring = _oppna_ring(yta.polygon)
    dx, dy = float(geo.pixelstorlek_x), float(geo.pixelstorlek_y)
    rord = np.zeros(mark_z.shape, dtype=np.bool_)

    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    z_bas = np.asarray(mark_z, dtype=np.float64).copy()
    har_slant = yta.slant_forhallande is not None or yta.slant_bredd_m is not None

    reach = 0.5 * math.hypot(dx, dy) + 1e-6
    bredd_max = 0.0
    if har_slant:
        kx, ky = _tata_kant(ring, 0.5 * min(dx, dy))
        zb_k = _bilinjar(z_bas, geo, kx, ky)
        delta_k = _z_ny(yta, kx, ky, zb_k) - zb_k
        delta_k = delta_k[np.isfinite(delta_k)]
        if delta_k.size == 0:
            return rord
        stor = float(np.max(np.abs(delta_k)))
        bredd_max = (
            float(yta.slant_bredd_m)
            if yta.slant_bredd_m is not None
            else stor * float(yta.slant_forhallande or 0.0) * 1.1
        ) + 2.0 * max(dx, dy)
        if stor <= _EPS:
            har_slant = False
        else:
            reach = max(reach, bredd_max)

    kol0 = max(0, math.floor((min(xs) - reach - geo.x_min) / dx))
    kol1 = min(geo.bredd, math.ceil((max(xs) + reach - geo.x_min) / dx) + 1)
    rad0 = max(0, math.floor((geo.y_max - (max(ys) + reach)) / dy))
    rad1 = min(geo.hojd, math.ceil((geo.y_max - (min(ys) - reach)) / dy) + 1)
    if kol1 <= kol0 or rad1 <= rad0:
        return rord

    e_vek = geo.x_min + (np.arange(kol0, kol1, dtype=np.float64) + 0.5) * dx
    n_vek = geo.y_max - (np.arange(rad0, rad1, dtype=np.float64) + 0.5) * dy
    sx, sy = np.meshgrid(e_vek, n_vek)
    fonster = (slice(rad0, rad1), slice(kol0, kol1))
    inne = np.asarray(inuti[fonster], dtype=bool)
    zb = z_bas[fonster]
    slag_f = markslag[fonster]

    dist, fx, fy = kantavstand(ring, sx, sy, geo, rad0, kol0, reach)
    zn = _z_ny(yta, sx, sy, zb)

    ny_z = zb.copy()
    ytmask = np.zeros(inne.shape, dtype=bool)  # celler som får ytans markslag och osäkerhet
    slantmask = np.zeros(inne.shape, dtype=bool)

    if not har_slant:
        f = np.where(inne, 1.0, 0.0)
        rand = np.isfinite(dist) & (dist <= 0.5 * math.hypot(dx, dy) + 1e-6)
        if np.any(rand):
            f[rand] = tackningsandel(ring, sx[rand], sy[rand], dx, dy)
        hel = f >= 1.0 - 1e-12
        delvis = (f > 0.0) & ~hel
        ny_z[hel] = zn[hel]
        fd, zd, bd = f[delvis], zn[delvis], zb[delvis]
        ny_z[delvis] = np.where(np.isfinite(bd), fd * zd + (1 - fd) * bd, bd)
        ytmask = f >= 0.5
        beror = f > 0.0
    else:
        ny_z[inne] = zn[inne]
        ytmask = inne
        beror = inne.copy()
        zb_f = _bilinjar(z_bas, geo, np.nan_to_num(fx), np.nan_to_num(fy))
        delta_f = _z_ny(yta, fx, fy, zb_f) - zb_f
        if yta.slant_bredd_m is not None:
            w = np.full(delta_f.shape, float(yta.slant_bredd_m))
        else:
            w = np.abs(delta_f) * float(yta.slant_forhallande or 0.0)
        with np.errstate(invalid="ignore"):
            ute = (
                ~inne
                & np.isfinite(dist)
                & np.isfinite(delta_f)
                & np.isfinite(zb)
                & (w > _EPS)
                & (dist < w)
                & (slag_f != MARKSLAG_BYGGNAD)
            )
        if np.any(ute):
            ny_z[ute] = zb[ute] + delta_f[ute] * (1.0 - dist[ute] / w[ute])
            slantmask = ute
            beror |= ute

    mark_z[fonster] = np.asarray(ny_z, dtype=np.float32)
    if yta.markslag is not None:
        slag_f[ytmask] = np.uint8(yta.markslag)
    osak_f = osakerhet_z[fonster]
    osak_f[ytmask] = np.float32(yta.osakerhet_m)
    if np.any(slantmask):
        slag_f[slantmask] = np.uint8(GRAS if yta.slant_markslag is None else yta.slant_markslag)
        osak_f[slantmask] = np.float32(yta.osakerhet_m)
    rord[fonster] = beror
    return rord
