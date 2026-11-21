"""Analytiskt facit: våg över horisontellt plan med Manning-friktion (Hunter m.fl. 2005).

Källa: Hunter, N. M., Horritt, M. S., Bates, P. D., Wilson, M. D. & Werner, M. G. F. (2005),
"An adaptive time step solution for raster-based storage cell modelling of floodplain
inundation", Advances in Water Resources 28. Bates, P. D., Horritt, M. S. & Fewtrell, T. J.
(2010), "A simple inertial formulation of the shallow water equations for efficient
two-dimensional flood inundation modelling", Journal of Hydrology 387, använde samma lösning
(avsnitt 4.1) för att validera lokal tröghet.

Härledning (friktionsdominerad våg, tröghet försummad): på horisontell botten är vattenytans
lutning S = −∂h/∂x, och Manning ger u = h^{2/3} S^{1/2} / n. En våg som går med konstant
hastighet u har h = h(ξ), ξ = x − ut, så

    −dh/dξ = n² u² / h^{4/3}  ⇒  h^{4/3} dh = −n² u² dξ  ⇒  (3/7) h^{7/3} = C − n² u² ξ

    h(x, t) = [ (7/3) (C − n² u² (x − u t)) ]^{3/7},   h = 0 där uttrycket är ≤ 0.

Med C = 0 står fronten vid x = u t. I litteraturen skrivs uttrycket ibland med ett extra
minustecken (−(7/3)(C − …)); det motsvarar en annan riktning på x-axeln, inte en annan lösning.
Här används formen ovan, som går i +x-riktningen. Inflödet ges som tvingat djup h(x₀, t) i
första kolumnen (Bates 2010 gör likadant med ett tidsberoende randdjup).

Testet mäter alltså den lokala tröghetslösarens avvikelse från den friktionsdominerade
lösningen; Bates (2010) rapporterar att skillnaden är liten när u = 1 m/s och n = 0,01.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from testsvit.k1_io import GeoTransformInfo
from tvad.kalltermer import Kalltermer, TvingatDjup
from tvad.lokal_troghet import kor_2d_lokal_troghet

MARKSLAG_GRAS = 4


def hunter_djup_m(
    x_m: NDArray[np.float64] | float, t_s: float, *, n: float, u: float, c: float = 0.0
) -> NDArray[np.float64]:
    """Analytiskt djup h(x, t) enligt formeln i modulens docstring."""
    x = np.asarray(x_m, dtype=np.float64)
    arg = (7.0 / 3.0) * (c - n * n * u * u * (x - u * t_s))
    return np.asarray(np.where(arg > 0.0, np.maximum(arg, 0.0) ** (3.0 / 7.0), 0.0))


@dataclass(frozen=True)
class HunterMatning:
    dx_m: float
    t_s: float
    rmse_m: float
    max_fel_m: float
    front_fel_m: float
    volymfel_rel: float
    max_djup_m: float
    kortid_s: float
    antal_tidssteg: int


def kor_hunter(
    dx_m: float,
    t_s: float,
    *,
    n: float = 0.01,
    u: float = 1.0,
    langd_m: float | None = None,
    rader: int = 3,
    front_troskel_m: float = 0.01,
) -> HunterMatning:
    """Kör 2D-lösaren i en kanal på horisontell botten och jämför med Hunter-lösningen vid t_s."""
    if dx_m <= 0 or t_s <= 0:
        raise ValueError("dx_m och t_s måste vara > 0.")
    langd = langd_m if langd_m is not None else u * t_s * 1.25 + 10 * dx_m
    kolumner = int(math.ceil(langd / dx_m))
    z = np.zeros((rader, kolumner), dtype=np.float32)
    ms = np.full((rader, kolumner), MARKSLAG_GRAS, dtype=np.uint8)
    geo = GeoTransformInfo(
        bredd=kolumner, hojd=rader, x_min=0.0, y_max=rader * dx_m,
        pixelstorlek_x=dx_m, pixelstorlek_y=dx_m, epsg=3011, dtype="float32",
    )
    x_mitt = (np.arange(kolumner) + 0.5) * dx_m
    mask = np.zeros((rader, kolumner), dtype=np.bool_)
    mask[:, 0] = True
    x0 = float(x_mitt[0])

    def rand(t: float) -> float:
        return float(hunter_djup_m(x0, t, n=n, u=u))

    res = kor_2d_lokal_troghet(
        z, ms, geo, [0.0, t_s], [0.0, 0.0],
        gles_front=True, oppna_rander=False, manning_tabell={MARKSLAG_GRAS: n},
        kalltermer=Kalltermer(tvingat_djup=TvingatDjup(mask, rand)),
    )
    h_num = np.asarray(res.slut_djup_m, dtype=np.float64)[rader // 2, :]
    h_ana = hunter_djup_m(x_mitt, t_s, n=n, u=u)
    jamfor = (h_ana > 0.0) | (h_num > 0.0)
    jamfor[0] = False  # randcellen är tvingad
    fel = h_num[jamfor] - h_ana[jamfor]

    def front(h: NDArray[np.float64]) -> float:
        vat = np.nonzero(h > front_troskel_m)[0]
        return float(x_mitt[vat[-1]]) if vat.size else 0.0

    return HunterMatning(
        dx_m=dx_m,
        t_s=t_s,
        rmse_m=float(np.sqrt(np.mean(fel * fel))) if fel.size else 0.0,
        max_fel_m=float(np.max(np.abs(fel))) if fel.size else 0.0,
        front_fel_m=front(h_num) - front(h_ana),
        volymfel_rel=res.volymfel_rel,
        max_djup_m=float(np.max(h_num)),
        kortid_s=res.kortid_s,
        antal_tidssteg=res.antal_tidssteg,
    )


@dataclass(frozen=True)
class HunterFall:
    namn: str
    n: float
    u: float
    t_s: tuple[float, ...]
    dx_m: tuple[float, ...]
    krav: bool
    """False = bara upplysning (fallet ligger utanför lösningens antaganden)."""


HUNTER_FALL: tuple[HunterFall, ...] = (
    HunterFall("D1 friktionsdominerad (Fr ≈ 0,1)", 0.05, 0.2, (1800.0, 3600.0),
               (20.0, 10.0, 5.0), True),
    HunterFall("D2 Bates 2010-parametrar (Fr ≈ 0,4, upplysning)", 0.01, 1.0, (1200.0,),
               (25.0,), False),
)

D1_MAX_RMSE_M = 0.01
"""Krav för finaste rutnätet i D1. Konvergenskravet: RMSE minskar för varje förfining."""


def kor_hunter_svit() -> tuple[list[tuple[HunterFall, HunterMatning]], bool, str]:
    """Grupp D (analytiska 2D-facit). Returnerar mätningar, om kraven klaras och en tabell.

    D2 visar att lokal tröghet avviker från den friktionsdominerade lösningen när Froudetalet
    inte är litet (u = 1 m/s, n = 0,01 ger Fr ≈ 0,4): för en våg med konstant hastighet tar
    lokal och konvektiv acceleration ut varandra i de fullständiga ekvationerna, men lokal
    tröghet behåller bara den lokala, en skillnad av storleken Fr². D2 är därför inget krav.
    """
    rader: list[tuple[HunterFall, HunterMatning]] = []
    ok = True
    for fall in HUNTER_FALL:
        for t in fall.t_s:
            matningar = [kor_hunter(dx, t, n=fall.n, u=fall.u) for dx in fall.dx_m]
            rader.extend((fall, m) for m in matningar)
            if fall.krav:
                rmse = [m.rmse_m for m in matningar]
                ok &= all(b < a for a, b in zip(rmse, rmse[1:], strict=False))
                ok &= rmse[-1] < D1_MAX_RMSE_M
                ok &= all(m.volymfel_rel < 1e-9 for m in matningar)
                ok &= abs(matningar[-1].front_fel_m) <= 2.0 * fall.dx_m[-1]
    rubrik = (
        "| Fall | dx (m) | t (s) | RMSE (m) | max fel (m) | frontfel (m) | volymfel | "
        "körtid (s) |\n| --- | --- | --- | --- | --- | --- | --- | --- |"
    )
    rad_text = [
        f"| {f.namn} | {m.dx_m:g} | {m.t_s:g} | {m.rmse_m:.4f} | {m.max_fel_m:.4f} | "
        f"{m.front_fel_m:+.1f} | {m.volymfel_rel:.1e} | {m.kortid_s:.2f} |"
        for f, m in rader
    ]
    return rader, ok, "\n".join([rubrik, *rad_text])
