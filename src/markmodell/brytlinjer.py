"""Brytlinjer (kantsten, mur, stödmur, staket, bankkant): snapping och inbränning."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from markmodell.rutnat import Ruta

STANDARDHOJDER_M: dict[str, float] = {
    "kantsten": 0.12,
    "sankt_kantsten": 0.02,
    "mur": 1.0,
    "stodmur": 1.5,
    "staket": 0.15,
    "bankkant": 0.30,
}

# Alias med svenskt tecken från docs/DATAKONTRAKT.md mappas deterministiskt till kanonisk nyckel.
_TYP_ALIAS: dict[str, str] = {
    "kantsten": "kantsten",
    "sankt_kantsten": "sankt_kantsten",
    "sänkt_kantsten": "sankt_kantsten",
    "mur": "mur",
    "stodmur": "stodmur",
    "stödmur": "stodmur",
    "staket": "staket",
    "bankkant": "bankkant",
}

GILTIGA_BRYTLINJETYPER: frozenset[str] = frozenset(STANDARDHOJDER_M.keys())


class BrytlinjeFel(Exception):
    """Fel vid validering, snapping eller rasterisering av brytlinjer."""


def normalisera_brytlinjetyp(typ: str) -> str:
    """Normaliserar och validerar brytlinjetyp enligt kontrakt K1."""
    if not isinstance(typ, str):
        raise BrytlinjeFel(f"Brytlinjetyp måste vara en sträng, fick {type(typ).__name__}.")
    nyckel = _TYP_ALIAS.get(typ.strip().lower())
    if nyckel is None:
        tillatna = ", ".join(sorted(GILTIGA_BRYTLINJETYPER))
        raise BrytlinjeFel(f"Ogiltig brytlinjetyp '{typ}'. Giltiga typer: {tillatna}.")
    return nyckel


@dataclass(frozen=True)
class Brytlinje:
    """En brytlinje som påverkar vattenflöden längs kantsten, mur eller bankkant.

    Valfria 3D-fält (`z_kron_rh2000`, `z_botten_rh2000`, `tvarfall`, `kalla`, `osakerhet_m`)
    är bakåtkompatibla tillägg till K1 `brytlinjer.gpkg`.
    """

    typ: str
    hojd_m: float
    punkter: tuple[tuple[float, float], ...]
    z_kron_rh2000: tuple[float, ...] | None = None
    z_botten_rh2000: tuple[float, ...] | None = None
    tvarfall: float | None = None
    kalla: str = "okand"
    osakerhet_m: float | None = None

    def __post_init__(self) -> None:
        kanonisk_typ = normalisera_brytlinjetyp(self.typ)
        object.__setattr__(self, "typ", kanonisk_typ)

        if not math.isfinite(self.hojd_m) or self.hojd_m < 0.0:
            raise BrytlinjeFel(f"Ogiltig brytlinjehöjd: {self.hojd_m} m (måste vara >= 0).")
        if len(self.punkter) < 2:
            raise BrytlinjeFel("En brytlinje måste ha minst två punkter.")

        rensade: list[tuple[float, float]] = []
        for p in self.punkter:
            e = float(p[0])
            n = float(p[1])
            if not (math.isfinite(e) and math.isfinite(n)):
                raise BrytlinjeFel(f"Ogiltig koordinat i brytlinje: {p!r}")
            rensade.append((e, n))
        object.__setattr__(self, "punkter", tuple(rensade))

        for falt_namn in ("z_kron_rh2000", "z_botten_rh2000"):
            z_seq = getattr(self, falt_namn)
            if z_seq is not None:
                if len(z_seq) != len(rensade):
                    raise BrytlinjeFel(
                        f"{falt_namn} måste ha samma längd som punkter "
                        f"({len(z_seq)} != {len(rensade)})."
                    )
                z_ren = tuple(float(z) for z in z_seq)
                if not all(math.isfinite(z) for z in z_ren):
                    raise BrytlinjeFel(f"{falt_namn} innehåller icke-ändliga värden.")
                object.__setattr__(self, falt_namn, z_ren)
        if self.tvarfall is not None:
            tf = float(self.tvarfall)
            if not math.isfinite(tf) or tf < 0.0:
                raise BrytlinjeFel(f"Ogiltigt tvärfall: {self.tvarfall}")
            object.__setattr__(self, "tvarfall", tf)
        if self.osakerhet_m is not None:
            osak = float(self.osakerhet_m)
            if not math.isfinite(osak) or osak < 0.0:
                raise BrytlinjeFel(f"Ogiltig osäkerhet: {self.osakerhet_m}")
            object.__setattr__(self, "osakerhet_m", osak)


@dataclass(frozen=True)
class SnapStatistik:
    """Sammanfattning från deterministisk snapping av lösa brytlinjeändar."""

    antal_linjer: int
    antal_losa_andar_fore: int
    antal_slutna_andar: int
    antal_losa_andar_efter: int
    tolerans_m: float


def _rakna_losa_andar(linjer: Sequence[Brytlinje]) -> int:
    """Räknar ändpunkter som inte delas exakt med någon annan ändpunkt."""
    frekvens: dict[tuple[float, float], int] = {}
    for linje in linjer:
        start = linje.punkter[0]
        slut = linje.punkter[-1]
        if start == slut:
            # Sluten ring har inga lösa ändar.
            continue
        frekvens[start] = frekvens.get(start, 0) + 1
        frekvens[slut] = frekvens.get(slut, 0) + 1
    return sum(1 for antal in frekvens.values() if antal == 1)


def fast_losa_andar(
    linjer: Sequence[Brytlinje],
    tolerans_m: float = 0.25,
) -> tuple[list[Brytlinje], SnapStatistik]:
    """Fäster ihop lösa ändpunkter inom `tolerans_m` i ett deterministiskt kluster.

    Alla ändpunkter som ligger inom `tolerans_m` från varandra (enkelkopplat eller
    girig komponentgruppering sorterad på koordinat) flyttas till komponentens
    gemensamma medelpunkt så att nätet blir slutet innan inbränning/triangulering.
    """
    if not math.isfinite(tolerans_m) or tolerans_m < 0.0:
        raise BrytlinjeFel(f"Ogiltig snaptolerans: {tolerans_m}")

    if not linjer:
        statistik = SnapStatistik(
            antal_linjer=0,
            antal_losa_andar_fore=0,
            antal_slutna_andar=0,
            antal_losa_andar_efter=0,
            tolerans_m=tolerans_m,
        )
        return [], statistik

    losa_fore = _rakna_losa_andar(linjer)
    if tolerans_m == 0.0:
        statistik = SnapStatistik(
            antal_linjer=len(linjer),
            antal_losa_andar_fore=losa_fore,
            antal_slutna_andar=0,
            antal_losa_andar_efter=losa_fore,
            tolerans_m=tolerans_m,
        )
        return list(linjer), statistik

    # Samla ändpunktsreferenser: (linje_idx, punkter_idx, koordinat)
    andposter: list[tuple[int, int, tuple[float, float]]] = []
    for idx, linje in enumerate(linjer):
        andposter.append((idx, 0, linje.punkter[0]))
        andposter.append((idx, len(linje.punkter) - 1, linje.punkter[-1]))

    # Räkna antalet exakt delade punkter för att endast snappa lösa ändar
    exakt_antal: dict[tuple[float, float], int] = {}
    for _, _, koord in andposter:
        exakt_antal[koord] = exakt_antal.get(koord, 0) + 1

    # Bygg union-find över ändpunkter med avstånd <= tolerans_m
    n_andar = len(andposter)
    foralder = list(range(n_andar))

    def hitta(x: int) -> int:
        while foralder[x] != x:
            foralder[x] = foralder[foralder[x]]
            x = foralder[x]
        return x

    def slaihop(a: int, b: int) -> None:
        ra = hitta(a)
        rb = hitta(b)
        if ra != rb:
            if ra < rb:
                foralder[rb] = ra
            else:
                foralder[ra] = rb

    tol2 = tolerans_m * tolerans_m
    # Sortering i E-led ger snabb beskärning och full determinism
    sorterade = sorted(range(n_andar), key=lambda i: (andposter[i][2][0], andposter[i][2][1], i))
    for pos_i in range(n_andar):
        i = sorterade[pos_i]
        ei, ni = andposter[i][2]
        for pos_j in range(pos_i + 1, n_andar):
            j = sorterade[pos_j]
            ej, nj = andposter[j][2]
            de = ej - ei
            if de > tolerans_m:
                break
            dn = nj - ni
            if de * de + dn * dn <= tol2:
                # Undvik att kollapsa en mycket kort linje med sig själv
                if andposter[i][0] == andposter[j][0]:
                    continue
                slaihop(i, j)

    # Beräkna målpunkt per komponent: medelvärde av redan knutna punkter om sådana finns,
    # annars medelvärde av alla punkter i komponenten.
    komponenter: dict[int, list[int]] = {}
    for i in range(n_andar):
        rot = hitta(i)
        komponenter.setdefault(rot, []).append(i)

    nya_punkter_per_linje: list[list[tuple[float, float]]] = [
        list(linje.punkter) for linje in linjer
    ]
    for medlemmar in komponenter.values():
        if len(medlemmar) < 2:
            continue
        unika_koord = {andposter[m][2] for m in medlemmar}
        if len(unika_koord) == 1:
            continue

        ankare = [
            andposter[m][2] for m in medlemmar if exakt_antal[andposter[m][2]] > 1
        ]
        kalla_koord = ankare if ankare else [andposter[m][2] for m in medlemmar]
        snappad_e = sum(k[0] for k in kalla_koord) / len(kalla_koord)
        snappad_n = sum(k[1] for k in kalla_koord) / len(kalla_koord)
        mal = (snappad_e, snappad_n)

        for m in medlemmar:
            linje_idx, p_idx, _ = andposter[m]
            nya_punkter_per_linje[linje_idx][p_idx] = mal

    resultat = [
        Brytlinje(
            typ=linje.typ,
            hojd_m=linje.hojd_m,
            punkter=tuple(nya_p),
            z_kron_rh2000=linje.z_kron_rh2000,
            z_botten_rh2000=linje.z_botten_rh2000,
            tvarfall=linje.tvarfall,
            kalla=linje.kalla,
            osakerhet_m=linje.osakerhet_m,
        )
        for linje, nya_p in zip(linjer, nya_punkter_per_linje, strict=True)
    ]
    losa_efter = _rakna_losa_andar(resultat)
    slutna = max(0, losa_fore - losa_efter)

    return resultat, SnapStatistik(
        antal_linjer=len(resultat),
        antal_losa_andar_fore=losa_fore,
        antal_slutna_andar=slutna,
        antal_losa_andar_efter=losa_efter,
        tolerans_m=tolerans_m,
    )


def _supercover_segment(
    e0: float,
    n0: float,
    e1: float,
    n1: float,
    ruta: Ruta,
) -> list[tuple[int, int]]:
    """4-sammanhängande supercover-rasterisering av ett linjesegment i rasternätet.

    Varje cell som segmentet skär (inklusive hörnkorsningar) returneras så att två
    successiva celler alltid delar minst en kant (4-grannskap). Därmed kan vatten
    inte läcka diagonalt genom en kantsten eller mur i en 4-granne-/8-granne-analys.
    """
    e_min, _, _, n_max = ruta.yttre_bbox
    uppl = ruta.upplosning_m

    # Kontinuerliga rutnätskoordinater (x = kolumn, y = rad)
    x0 = (e0 - e_min) / uppl
    y0 = (n_max - n0) / uppl
    x1 = (e1 - e_min) / uppl
    y1 = (n_max - n1) / uppl

    kol = int(math.floor(x0))
    rad = int(math.floor(y0))
    slut_kol = int(math.floor(x1))
    slut_rad = int(math.floor(y1))

    celler: list[tuple[int, int]] = [(rad, kol)]
    dx = x1 - x0
    dy = y1 - y0
    steg_x = 1 if dx > 0.0 else (-1 if dx < 0.0 else 0)
    steg_y = 1 if dy > 0.0 else (-1 if dy < 0.0 else 0)

    if steg_x == 0 and steg_y == 0:
        return celler

    t_delta_x = abs(1.0 / dx) if dx != 0.0 else math.inf
    t_delta_y = abs(1.0 / dy) if dy != 0.0 else math.inf

    if dx > 0.0:
        t_max_x = ((kol + 1.0) - x0) * t_delta_x
    elif dx < 0.0:
        t_max_x = (x0 - kol) * t_delta_x
    else:
        t_max_x = math.inf

    if dy > 0.0:
        t_max_y = ((rad + 1.0) - y0) * t_delta_y
    elif dy < 0.0:
        t_max_y = (y0 - rad) * t_delta_y
    else:
        t_max_y = math.inf

    max_steg = abs(slut_kol - kol) + abs(slut_rad - rad) + 4
    eps = 1e-12
    for _ in range(max_steg):
        if kol == slut_kol and rad == slut_rad:
            break
        if abs(t_max_x - t_max_y) <= eps:
            # Exakt hörnkorsning: lägg först till båda kantgrannarna för 4-sammanhang!
            if steg_x != 0:
                celler.append((rad, kol + steg_x))
            if steg_y != 0:
                celler.append((rad + steg_y, kol))
            kol += steg_x
            rad += steg_y
            t_max_x += t_delta_x
            t_max_y += t_delta_y
            celler.append((rad, kol))
        elif t_max_x < t_max_y:
            kol += steg_x
            t_max_x += t_delta_x
            celler.append((rad, kol))
        else:
            rad += steg_y
            t_max_y += t_delta_y
            celler.append((rad, kol))

    if (slut_rad, slut_kol) not in celler:
        # Säkerställ sista cellen och 4-sammanhang om den ligger diagonalt intill
        sista_rad, sista_kol = celler[-1]
        if abs(slut_rad - sista_rad) == 1 and abs(slut_kol - sista_kol) == 1:
            celler.append((sista_rad, slut_kol))
        celler.append((slut_rad, slut_kol))

    return celler


def brann_in_brytlinjer(
    mark_z: NDArray[np.float32],
    ruta: Ruta,
    linjer: Sequence[Brytlinje],
    snappa: bool = False,
    snaptolerans_m: float = 0.25,
) -> NDArray[np.float32]:
    """Bränner in brytlinjer i `mark_z` med 4-sammanhängande supercover-rasterisering.

    För varje cell som skärs av en brytlinje sätts ett tillägg så att höjden blir
    minst `grundhojd + hojd_m` (där den största inbrända brytlinjehöjden i cellen
    gäller om flera linjer korsas, så att upprepad applicering inte staplar höjder).
    """
    if mark_z.shape != ruta.form_rc:
        raise BrytlinjeFel(
            f"mark_z har form {mark_z.shape}, förväntade {ruta.form_rc} för {ruta.namn}."
        )

    aktiva_linjer: Sequence[Brytlinje]
    if snappa:
        aktiva_linjer, _ = fast_losa_andar(linjer, tolerans_m=snaptolerans_m)
    else:
        aktiva_linjer = linjer

    resultat = np.array(mark_z, dtype=np.float32, copy=True)
    if not aktiva_linjer:
        return resultat

    rader, kolumner = ruta.form_rc
    max_tillägg = np.zeros((rader, kolumner), dtype=np.float32)

    for linje in aktiva_linjer:
        hojd = np.float32(linje.hojd_m)
        if hojd <= 0.0:
            continue
        for idx in range(len(linje.punkter) - 1):
            e0, n0 = linje.punkter[idx]
            e1, n1 = linje.punkter[idx + 1]
            for rad, kol in _supercover_segment(e0, n0, e1, n1, ruta):
                if 0 <= rad < rader and 0 <= kol < kolumner and hojd > max_tillägg[rad, kol]:
                    max_tillägg[rad, kol] = hojd

    mask = max_tillägg > 0.0
    resultat[mask] = resultat[mask] + max_tillägg[mask]
    return resultat
