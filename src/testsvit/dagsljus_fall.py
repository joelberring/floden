"""Grupp E: dagsljus mot analytiska facit och oberoende strålgång (`floden testsvit --grupp E`).

Fallen delar geometri och facit med `tests/dagsljus/`. Källor för facit:

- Gaturum, SVF: Oke (1981), SVF = (cos atan(H/d1) + cos atan(H/d2)) / 2 (härledning i
  `docs/DAGSLJUS.md`).
- VSC och normering: BRE (Littlefair), CIE-mulen himmel L = (1 + 2 sin g) / 3; oskymd vertikal
  fasad = 39,6 % av horisontell belysning; kvadratur med Gauss–Legendre.
- Soltimmar: oberoende strålgång (steg 0,02 m) mot ett prismafält.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from dagsljus.horisont import Hojdfalt, horisont_mipmap, riktningar_rad
from dagsljus.matt import (
    VSC_OSKYMD,
    halvplansriktningar,
    himmelsintegral,
    soltimmar_fran_profiler,
    svf_fran_horisont,
    vsc_fran_halvplansprofil,
)
from dagsljus.solposition import dygnsfonster_utc, solposition

Falt = NDArray[np.float64]


def plan(n: int, cell_m: float) -> Hojdfalt:
    return Hojdfalt(np.zeros((n, n)), cell_m, 0.0, n * cell_m)


def kanjon_falt(
    cell_m: float, bredd_m: float, hojd_m: float, halv_x_m: float = 80.0, halv_y_m: float = 400.0
) -> Hojdfalt:
    """Gaturum längs norr–söder, symmetriskt kring x = 0, y = 0 (en cell är centrerad där).

    Gatan (z=0) är |x| <= bredd/2, utanför är väggar med höjden `hojd_m` ut till fältets kant.
    Fältet har udda antal celler så att x = 0 och y = 0 är cellmittpunkter. Är `bredd_m` en udda
    multipel av `cell_m` ligger väggytorna exakt på cellgranser (x = +-bredd/2).
    """
    nx = 2 * int(round(halv_x_m / cell_m)) + 1
    ny = 2 * int(round(halv_y_m / cell_m)) + 1
    xc = (np.arange(nx) - (nx - 1) / 2.0) * cell_m
    z = np.zeros((ny, nx))
    z[:, np.abs(xc) > bredd_m / 2.0 + 1e-9] = hojd_m
    return Hojdfalt(z, cell_m, -nx * cell_m / 2.0, ny * cell_m / 2.0)


def vsc_kanjon_facit(hojd_m: float, avstand_m: float, rmax_m: float | None = None) -> float:
    """VSC på en vertikal fasad mitt emot en oändlig vägg (höjd H, avstånd d):
    (1/N) Int I(h(psi)) cos psi dpsi med h = atan(H cos psi / d), Gauss–Legendre-kvadratur.

    Med `rmax_m` ser strålarna bara väggen om d / cos psi <= rmax; för större |psi| är h = 0
    (så beter sig horisontberäkningen med begransad räckvidd). Integralen delas vid psi_c.
    """
    from dagsljus.matt import N_HORISONTELL_OSKYMD

    x, w = np.polynomial.legendre.leggauss(300)

    def del_(a: float, b: float, med_vagg: bool) -> float:
        psi = 0.5 * (b - a) * x + 0.5 * (b + a)
        hh = np.arctan(hojd_m * np.cos(psi) / avstand_m) if med_vagg else np.zeros_like(psi)
        return float(np.sum(w * himmelsintegral(hh) * np.cos(psi)) * 0.5 * (b - a))

    if rmax_m is None or rmax_m <= avstand_m:
        psi_c = math.pi / 2.0 if rmax_m is None else 0.0
    else:
        psi_c = math.acos(avstand_m / rmax_m)
    tot = 2.0 * del_(0.0, psi_c, True) if psi_c > 0 else 0.0
    if psi_c < math.pi / 2.0:
        tot += 2.0 * del_(psi_c, math.pi / 2.0, False)
    return tot / N_HORISONTELL_OSKYMD


def svf_kanjon_facit(hojd_m: float, d1_m: float, d2_m: float, rmax_m: float | None = None) -> float:
    """SVF för en punkt på gatan i ett oändligt gaturum (Oke 1981, generell form):
    SVF = (cos atan(H/d1) + cos atan(H/d2)) / 2. Härledning i `docs/DAGSLJUS.md`.

    Med `rmax_m` ger riktningar som träffar väggen först bortom rmax h = 0. Då är
    1 - SVF = (1/2pi) Int sin^2 h dphi, summerat över båda väggarna med kvadratur.
    """
    if rmax_m is None:
        return 0.5 * (math.cos(math.atan(hojd_m / d1_m)) + math.cos(math.atan(hojd_m / d2_m)))
    x, w = np.polynomial.legendre.leggauss(400)
    forlust = 0.0
    for d in (d1_m, d2_m):
        # Väggen på sidan med avstånd d: riktning vinkel a från normalen, träff vid d / cos a.
        a_c = math.acos(d / rmax_m) if rmax_m > d else 0.0
        a = 0.5 * a_c * (x + 1.0)
        h = np.arctan(hojd_m * np.cos(a) / d)
        forlust += 2.0 * float(np.sum(w * np.sin(h) ** 2) * 0.5 * a_c)
    return 1.0 - forlust / (2.0 * math.pi)


def blocktest_geometri() -> tuple[Hojdfalt, NDArray[np.float64], NDArray[np.float64]]:
    """Block 30 m (Ö–V) x 20 m (N–S), höjd 15 m, centrerat i origo; punkter norr, öster och väster
    om det (där skuggan faller ett vårdagjämningsdygn i Stockholm)."""
    n, cell = 801, 1.0
    xc = (np.arange(n) - n // 2) * cell
    xx, yy = np.meshgrid(xc, -xc)
    z = np.zeros((n, n))
    z[(np.abs(xx) <= 15) & (np.abs(yy) <= 10)] = 15.0
    f = Hojdfalt(z, cell, -n * cell / 2, n * cell / 2)
    pts = (
        [(0.0, y) for y in (11.5, 14.5, 18.5, 25.5, 35.5, 50.5)]
        + [(x, 18.5) for x in (-20.5, -15.5, -10.5, 10.5, 20.5, 25.5)]
        + [(-30.5, 8.5), (30.5, -12.5)]
    )
    return f, np.array([p[0] for p in pts]), np.array([p[1] for p in pts])


def bruteforce_timmar(
    f: Hojdfalt,
    px: float,
    py: float,
    sol_h: NDArray[np.float64],
    sol_az: NDArray[np.float64],
    steg_h: float,
) -> float:
    """Oberoende strålgång: för varje tidssteg, gå mot solen i steg om 0,02 m och kontrollera
    om någon cell sticker upp över solstrålen. Delar ingen kod med horisontberäkningen."""
    s = np.arange(0.02, 300.0, 0.02)
    antal = 0
    for e, a in zip(sol_h, sol_az, strict=True):
        if e <= 0:
            continue
        xs, ys = px + s * math.sin(a), py + s * math.cos(a)
        j = np.floor((xs - f.x_min_m) / f.cell_m).astype(int)
        i = np.floor((f.y_max_m - ys) / f.cell_m).astype(int)
        ok = (j >= 0) & (j < f.bredd) & (i >= 0) & (i < f.hojd)
        z = np.where(ok, f.z_m[np.clip(i, 0, f.hojd - 1), np.clip(j, 0, f.bredd - 1)], -1e9)
        if np.all(z <= s * math.tan(e)):
            antal += 1
    return antal * steg_h


@dataclass(frozen=True)
class DagsljusFall:
    """Ett facitfall: uppmätt avvikelse mot grans."""

    id: str
    namn: str
    avvikelse: float
    grans: float
    enhet: str
    kortid_s: float

    @property
    def godkand(self) -> bool:
        return self.avvikelse <= self.grans


def _tid(f: Callable[[], float]) -> tuple[float, float]:
    t0 = time.perf_counter()
    v = f()
    return float(v), time.perf_counter() - t0


def kor_dagsljus_testfall() -> tuple[list[DagsljusFall], str]:
    """Kör grupp E och returnerar resultat och en markdown-tabell."""
    R, H = 250.0, 20.0

    def e1() -> float:
        k = 256
        v = float(vsc_fran_halvplansprofil(np.zeros(k)))
        return max(abs(v - VSC_OSKYMD), abs(float(svf_fran_horisont(np.zeros(64))) - 1.0))

    def e2() -> float:
        n = 800
        z = np.zeros((n, n))
        z[:, n // 2 + 20 :] = H
        f = Hojdfalt(z, 1.0, 0.0, float(n))
        d = 19.5
        x, y = np.array([n // 2 + 0.5]), np.array([n // 2 + 0.5])
        fel = 0.0
        for p in np.radians(np.arange(-75, 76, 5)):
            r = horisont_mipmap(
                f, x, f.y_max_m - y, np.zeros(1), np.array([math.pi / 2 + p]), 250.0
            )
            fel = max(fel, abs(float(r.h_rad[0]) - math.atan(H * math.cos(p) / d)))
        return fel

    def e3() -> float:
        fel = 0.0
        for cell, w in ((2.0, 22.0), (1.0, 21.0), (0.5, 21.5)):
            f = kanjon_falt(cell, w, H, halv_x_m=R, halv_y_m=R)
            k = 64
            r = horisont_mipmap(f, np.zeros(k), np.zeros(k), np.zeros(k), riktningar_rad(k), R)
            fel = max(
                fel, abs(float(svf_fran_horisont(r.h_rad)) - svf_kanjon_facit(H, w / 2, w / 2))
            )
        return fel

    def e4() -> float:
        fel = 0.0
        for cell, w in ((2.0, 22.0), (1.0, 21.0), (0.5, 21.5)):
            f = kanjon_falt(cell, w, H, halv_x_m=R, halv_y_m=R)
            kf = 1024
            phi = halvplansriktningar(np.array([math.pi / 2]), kf)[0]
            r = horisont_mipmap(
                f, np.full(kf, -w / 2 + 0.5 * cell), np.zeros(kf), np.zeros(kf), phi, R
            )
            facit = vsc_kanjon_facit(H, w - 0.5 * cell, R)
            fel = max(fel, abs(float(vsc_fran_halvplansprofil(r.h_rad)) - facit))
        return fel

    def e5() -> float:
        f, x, y = blocktest_geometri()
        steg_h = 5.0 / 60.0
        sh, sa = solposition(dygnsfonster_utc("2026-03-20", 18.07, 5.0), 59.33, 18.07)
        k = 512
        hk = np.empty((k, x.size))
        for i, phi in enumerate(riktningar_rad(k)):
            hk[i] = horisont_mipmap(f, x, y, np.zeros(x.size), np.full(x.size, phi), 300.0).h_rad
        t = soltimmar_fran_profiler(hk, sh, sa, steg_h)
        bf = np.array(
            [bruteforce_timmar(f, a, b, sh, sa, steg_h) for a, b in zip(x, y, strict=True)]
        )
        return float(np.abs(t - bf).mean())

    def _fore_efter_block(hojd_efter_m: float) -> tuple[Hojdfalt, Hojdfalt]:
        """Block 30 x 20 m centrerat i origo: höjd 12 m före och `hojd_efter_m` efter."""
        n = 801
        xc = (np.arange(n) - n // 2) * 1.0
        xx, yy = np.meshgrid(xc, -xc)
        fot = (np.abs(xx) <= 15) & (np.abs(yy) <= 10)
        z0, z1 = np.zeros((n, n)), np.zeros((n, n))
        z0[fot], z1[fot] = 12.0, hojd_efter_m
        return Hojdfalt(z0, 1.0, -n / 2.0, n / 2.0), Hojdfalt(z1, 1.0, -n / 2.0, n / 2.0)

    def _timmar_punkter(f: Hojdfalt, px: NDArray[np.float64], py: NDArray[np.float64]) -> Falt:
        k = 180
        sh, sa = solposition(dygnsfonster_utc("2026-03-20", 18.07, 10.0), 59.33, 18.07)
        hk = np.empty((k, px.size))
        for i, phi in enumerate(riktningar_rad(k)):
            hk[i] = horisont_mipmap(
                f, px, py, np.zeros(px.size), np.full(px.size, phi), radie_fe
            ).h_rad
        return soltimmar_fran_profiler(hk, sh, sa, 10.0 / 60.0)

    hmax_fe, hmin_fe = 20.0, 15.0
    radie_fe = hmax_fe / math.tan(math.radians(hmin_fe))  # 74,6 m

    def e6() -> float:
        """Höjt block: soltimmar får aldrig öka (utanför blockets egna celler); mått = största
        ökning i timmar över 300 markpunkter i en ring 1-60 m från blocket."""
        f0, f1 = _fore_efter_block(hmax_fe)
        gx, gy = np.meshgrid(np.arange(-60.5, 61.0, 6.0), np.arange(-60.5, 61.0, 6.0))
        px, py = gx.ravel(), gy.ravel()
        ute = ~((np.abs(px) <= 16) & (np.abs(py) <= 11))
        px, py = px[ute], py[ute]
        d = _timmar_punkter(f1, px, py) - _timmar_punkter(f0, px, py)
        assert d.min() < -0.5  # fler skuggtimmar finns (testet mäter inte tomt)
        return max(0.0, float(d.max()))

    def e7() -> float:
        """Punkter bortom påverkansradien (H_max / tan h_min) från det höjda blocket är exakt
        oförändrade: största absoluta skillnad i soltimmar över 240 punkter 92-150 m bort."""
        f0, f1 = _fore_efter_block(hmax_fe)
        v = np.radians(np.arange(0.0, 360.0, 3.0))
        pr = np.concatenate([(radie_fe + 18.0) + 0.0 * v, 150.0 + 0.0 * v])
        px = np.concatenate([pr[: v.size] * np.sin(v), pr[v.size :] * np.sin(v)])
        py = np.concatenate([pr[: v.size] * np.cos(v), pr[v.size :] * np.cos(v)])
        # Blockets celler når 15,5 m från origo i x: avståndet till blocket är minst radie + 2,5 m.
        d = _timmar_punkter(f1, px, py) - _timmar_punkter(f0, px, py)
        return float(np.abs(d).max())

    spec = [
        ("E1", "Fri plan: VSC = 39,6 % (BRE), SVF = 1", e1, 3e-6, "andel"),
        ("E2", "Oändlig vägg: h = atan(H cos psi / d)", e2, 1e-12, "rad"),
        ("E3", "Gaturum H/W: SVF mitt i gatan mot Oke (K = 64)", e3, 1e-9, "andel"),
        ("E4", "Gaturum: VSC vid fasadfot mot kvadratur (K/2 = 1024)", e4, 1e-5, "andel"),
        ("E5", "Skuggstudie: soltimmar mot strålgång (K = 512), medelfel", e5, 0.03, "h"),
        ("E6", "Före/efter: höjt block ger aldrig fler soltimmar (K = 180)", e6, 1e-9, "h"),
        ("E7", "Före/efter: punkter bortom påverkansradien exakt oförändrade", e7, 0.0, "h"),
    ]
    res: list[DagsljusFall] = []
    for id_, namn, f, grans, enhet in spec:
        v, t = _tid(f)
        res.append(DagsljusFall(id_, namn, v, grans, enhet, t))
    rader = [
        "| Fall | Beskrivning | Avvikelse | Gräns | Enhet | Körtid (s) | Status |",
        "| --- | --- | ---: | ---: | --- | ---: | --- |",
    ]
    for r in res:
        rader.append(
            f"| {r.id} | {r.namn} | {r.avvikelse:.3g} | {r.grans:.3g} | {r.enhet} | "
            f"{r.kortid_s:.1f} | {'GODKAND' if r.godkand else 'UNDERKAND'} |"
        )
    return res, "\n".join(rader)
