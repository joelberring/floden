"""Oberoende, enkla referensimplementationer för testerna (skalär Python, inga genvägar).

Ingenting här använder `buller.scen.profil` eller shapely. Referenserna är långsamma med
flit: de ska vara lätta att granska.
"""

from __future__ import annotations

import math

import numpy as np

from buller.scen.bullerscen import Bullerscen
from buller.scen.syntetisk import URSPRUNG_E_M, URSPRUNG_N_M, terranghojd_m
from buller.typer import Byggnad, Flyttal


def bilinjar_markhojd_m(scen: Bullerscen, e_m: float, n_m: float) -> float:
    """Bilinjär interpolation mellan cellmitt, skriven direkt ur definitionen."""
    z = scen.terrang_rh2000_m
    kol = (e_m - scen.e_min_m) / scen.cell_m - 0.5
    rad = (scen.n_max_m - n_m) / scen.cell_m - 0.5
    kol = min(max(kol, 0.0), z.shape[1] - 1.0)
    rad = min(max(rad, 0.0), z.shape[0] - 1.0)
    k0 = min(int(math.floor(kol)), z.shape[1] - 2)
    r0 = min(int(math.floor(rad)), z.shape[0] - 2)
    fk, fr = kol - k0, rad - r0
    return float(
        z[r0, k0] * (1 - fk) * (1 - fr)
        + z[r0, k0 + 1] * fk * (1 - fr)
        + z[r0 + 1, k0] * (1 - fk) * fr
        + z[r0 + 1, k0 + 1] * fk * fr
    )


def punkt_i_polygon(e_m: float, n_m: float, byggnad: Byggnad) -> bool:
    """Strålkastning österut: udda antal korsningar betyder att punkten ligger inuti."""
    inne = False
    antal = byggnad.e_m.shape[0]
    for i in range(antal):
        e0, n0 = float(byggnad.e_m[i]), float(byggnad.n_m[i])
        e1, n1 = float(byggnad.e_m[(i + 1) % antal]), float(byggnad.n_m[(i + 1) % antal])
        if (n0 > n_m) != (n1 > n_m) and e_m < e0 + (n_m - n0) * (e1 - e0) / (n1 - n0):
            inne = not inne
    return inne


def punkt_i_nagon_byggnad(scen: Bullerscen, e_m: float, n_m: float) -> bool:
    return any(punkt_i_polygon(e_m, n_m, b) for b in scen.byggnader)


def byggnadsintervall(
    byggnad: Byggnad, se: float, sn: float, te: float, tn: float
) -> list[tuple[float, float]]:
    """Intervall (avstånd längs snittet) där snittet går genom byggnaden (jämn–udda-regeln)."""
    de, dn = te - se, tn - sn
    langd = math.hypot(de, dn)
    korsningar: list[float] = []
    antal = byggnad.e_m.shape[0]
    for i in range(antal):
        pe, pn = float(byggnad.e_m[i]), float(byggnad.n_m[i])
        qe, qn = float(byggnad.e_m[(i + 1) % antal]), float(byggnad.n_m[(i + 1) % antal])
        ve, vn = qe - pe, qn - pn
        namnare = de * vn - dn * ve
        if namnare == 0.0:
            continue
        u = ((pe - se) * vn - (pn - sn) * ve) / namnare
        v = ((pe - se) * dn - (pn - sn) * de) / namnare
        if 0.0 <= u <= 1.0 and 0.0 <= v < 1.0:
            korsningar.append(u * langd)
    korsningar.sort()
    assert len(korsningar) % 2 == 0, "snittets ändpunkter ska ligga utanför byggnaderna"
    return [(korsningar[i], korsningar[i + 1]) for i in range(0, len(korsningar), 2)]


def referensprofil(
    scen: Bullerscen,
    se: float,
    sn: float,
    te: float,
    tn: float,
    steg_m: float,
    skarm_dx_m: float,
) -> tuple[list[float], list[float]]:
    """Profilens punkter (x, z) enligt reglerna i `buller.scen.profil`, punkt för punkt."""
    langd = math.hypot(te - se, tn - sn)

    def mark(x: float) -> float:
        return bilinjar_markhojd_m(scen, se + (te - se) * x / langd, sn + (tn - sn) * x / langd)

    intervall: list[tuple[float, float, float]] = []
    for byggnad in scen.byggnader:
        for a, b in byggnadsintervall(byggnad, se, sn, te, tn):
            if b - a >= 1e-6:
                intervall.append((a, b, byggnad.tak_rh2000_m))
    intervall.sort()

    fogade = _fogade(intervall, skarm_dx_m)
    punkter: list[tuple[float, float]] = []
    for i, (a, b, tak) in enumerate(fogade):
        fog_vanster = i > 0 and intervall[i][0] - intervall[i - 1][1] < 3 * skarm_dx_m
        fog_hoger = i + 1 < len(intervall) and intervall[i + 1][0] - b < 3 * skarm_dx_m
        d = min(skarm_dx_m, (b - a) / 3.0)
        if fog_vanster:
            punkter.append((a, tak))
        else:
            punkter.append((a, mark(a)))
            punkter.append((a + d, tak))
        punkter.append((b - d, tak))
        if not fog_hoger:
            punkter.append((b, mark(b)))

    antal_steg = max(1, math.ceil(langd / steg_m))
    for j in range(antal_steg + 1):
        x = langd if j == antal_steg else j * langd / antal_steg
        ande = j in (0, antal_steg)
        nara = any(a - skarm_dx_m < x < b + skarm_dx_m for a, b, _ in fogade)
        if ande or not nara:
            punkter.append((x, mark(x)))
    punkter.sort()
    return [p[0] for p in punkter], [p[1] for p in punkter]


def _fogade(
    intervall: list[tuple[float, float, float]], skarm_dx_m: float
) -> list[tuple[float, float, float]]:
    """Intervallen efter hopfogning (början flyttad till föregående slut i en fog)."""
    ut: list[tuple[float, float, float]] = []
    for a, b, tak in intervall:
        if ut and a - ut[-1][1] < 3 * skarm_dx_m:
            a = ut[-1][1]
        ut.append((a, b, tak))
    return ut


def ythojd_punktvis_m(scen: Bullerscen, e_m: Flyttal, n_m: Flyttal) -> Flyttal:
    """Punktvis yta i den syntetiska scenen: tak om punkten ligger i ett hus, annars den
    analytiska markytan (inte rastret). Vektoriserad strålkastning, hus för hus."""
    hojd = terranghojd_m(e_m - URSPRUNG_E_M, n_m - URSPRUNG_N_M)
    for byggnad in scen.byggnader:
        be, bn = byggnad.e_m, byggnad.n_m
        kand = np.flatnonzero(
            (e_m >= be.min()) & (e_m <= be.max()) & (n_m >= bn.min()) & (n_m <= bn.max())
        )
        if kand.size == 0:
            continue
        pe, pn = e_m[kand], n_m[kand]
        inne = np.zeros(kand.shape[0], dtype=np.bool_)
        for i in range(be.shape[0]):
            e0, n0 = be[i], bn[i]
            e1, n1 = be[(i + 1) % be.shape[0]], bn[(i + 1) % be.shape[0]]
            if n0 == n1:
                continue
            korsar = ((n0 > pn) != (n1 > pn)) & (pe < e0 + (pn - n0) * (e1 - e0) / (n1 - n0))
            inne ^= korsar
        hojd[kand[inne]] = byggnad.tak_rh2000_m
    return hojd


def fria_punkter(
    scen: Bullerscen, slump: np.random.Generator, antal: int, kant_m: float = 2.0
) -> tuple[Flyttal, Flyttal]:
    """Slumpade punkter inom scenen som inte ligger i eller alldeles intill en byggnad."""
    import shapely

    hus = shapely.union_all(shapely.buffer(scen.polygoner, 0.1))
    e_ut: list[float] = []
    n_ut: list[float] = []
    while len(e_ut) < antal:
        e = scen.e_min_m + slump.uniform(kant_m, scen.e_max_m - scen.e_min_m - kant_m, antal)
        n = scen.n_min_m + slump.uniform(kant_m, scen.n_max_m - scen.n_min_m - kant_m, antal)
        fri = ~shapely.contains_xy(hus, e, n)
        e_ut.extend(e[fri].tolist())
        n_ut.extend(n[fri].tolist())
    return np.array(e_ut[:antal]), np.array(n_ut[:antal])
