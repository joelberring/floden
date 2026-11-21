"""Grundkarteimport (ASCII-DXF), topologirensning, laserhöjdsättning och barriär-IDW."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from markmodell.brytlinjer import (
    STANDARDHOJDER_M,
    Brytlinje,
    fast_losa_andar,
    normalisera_brytlinjetyp,
)
from markmodell.hinder_oppningar import Byggnad, Oppning
from markmodell.interpolering import filtrera_laser_klasser
from markmodell.rutnat import Ruta

GILTIGA_BRYTLINJE_ROLLER: frozenset[str] = frozenset(STANDARDHOJDER_M.keys())
GILTIGA_OPPNING_ROLLER: frozenset[str] = frozenset({"trumma", "port"})
GILTIGA_GRUNDKARTA_ROLLER: frozenset[str] = (
    GILTIGA_BRYTLINJE_ROLLER | GILTIGA_OPPNING_ROLLER | frozenset({"byggnad"})
)

STANDARD_STOCKHOLM_LAGERMAPPNING: dict[str, tuple[str, float]] = {
    "KANTSTEN": ("kantsten", 0.12),
    "VAGKANT": ("kantsten", 0.12),
    "VÄGKANT": ("kantsten", 0.12),
    "MUR": ("mur", 1.0),
    "STODMUR": ("stodmur", 1.5),
    "STÖDMUR": ("stodmur", 1.5),
    "STAKET": ("staket", 0.15),
    "SLANTKANT": ("bankkant", 0.30),
    "SLÄNTKANT": ("bankkant", 0.30),
    "BANKKANT": ("bankkant", 0.30),
    "HUSLIV": ("byggnad", 10.0),
    "BYGGNAD": ("byggnad", 10.0),
    "TRUMMA": ("trumma", 1.0),
    "PORT": ("port", 2.5),
}

_DEGENERERAD_LANGD_M = 1e-4
_MIN_BYGGNAD_AREA_M2 = 1e-4
_DUPLICERAD_PUNKT_TOL_M = 1e-9


class GrundkartaFel(Exception):
    """Fel vid inläsning, topologirensning, höjdsättning eller barriärinterpolering."""


def _normalisera_roll(roll: str) -> str:
    """Normaliserar och validerar en Floden-roll för baskartelager."""
    if not isinstance(roll, str):
        raise GrundkartaFel(f"Roll måste vara en sträng, fick {type(roll).__name__}.")
    nyckel = roll.strip().lower()
    if nyckel == "stödmur":
        nyckel = "stodmur"
    if nyckel not in GILTIGA_GRUNDKARTA_ROLLER:
        tillatna = ", ".join(sorted(GILTIGA_GRUNDKARTA_ROLLER))
        raise GrundkartaFel(f"Ogiltig grundkarteroll '{roll}'. Giltiga roller: {tillatna}.")
    return nyckel


@dataclass(frozen=True)
class LagerMappning:
    """Mappningsregel från baskarte-/DXF-lagerprefix till Floden-roll och standardmått."""

    prefix: str
    roll: str
    standard_m: float

    def __post_init__(self) -> None:
        if not isinstance(self.prefix, str) or not self.prefix.strip():
            raise GrundkartaFel("Lagerprefix i LagerMappning får inte vara tomt.")
        norm_prefix = self.prefix.strip().upper()
        object.__setattr__(self, "prefix", norm_prefix)

        norm_roll = _normalisera_roll(self.roll)
        object.__setattr__(self, "roll", norm_roll)

        if not math.isfinite(self.standard_m):
            raise GrundkartaFel(f"Ogiltigt standardmått i LagerMappning: {self.standard_m}.")
        if norm_roll in GILTIGA_BRYTLINJE_ROLLER:
            if self.standard_m < 0.0:
                raise GrundkartaFel(
                    f"Standardhöjd för '{norm_roll}' måste vara >= 0, fick {self.standard_m}."
                )
        elif self.standard_m <= 0.0:
            raise GrundkartaFel(
                f"Standardmått för '{norm_roll}' måste vara > 0, fick {self.standard_m}."
            )

    def matchar(self, lagernamn: str) -> bool:
        """Returnerar True om lagernamnet matchar prefixet (skiftlägesokänsligt)."""
        norm = lagernamn.strip().upper()
        return norm == self.prefix or norm.startswith(self.prefix)


@dataclass(frozen=True)
class TopologiRapport:
    """Sammanfattning av topologirensning och huslivsstängning."""

    antal_segment_in: int
    antal_dubbletter_borttagna: int
    antal_degenererade_borttagna: int
    antal_stangda_byggnader: int
    antal_oppna_husliv_varningar: int
    antal_forenklade_punkter: int


@dataclass(frozen=True)
class BaskartaResultat:
    """Strukturerat resultat från import och rensning av en baskarta (DXF)."""

    brytlinjer: tuple[Brytlinje, ...]
    byggnader: tuple[Byggnad, ...]
    oppningar: tuple[Oppning, ...]
    okanda_lager: tuple[str, ...]
    topologi: TopologiRapport
    varningar: tuple[str, ...]


@dataclass(frozen=True)
class LinjeLaserAnalys:
    """Resultat från ensidig laserhöjdsättning och sidopassning för en brytlinje."""

    linje_index: int
    typ: str
    vanster_median_z: float | None
    hoger_median_z: float | None
    uppmatt_sprang_m: float | None
    sidoforskjutning_m: float | None
    antal_punkter_vanster: int
    antal_punkter_hoger: int
    osaker: bool


@dataclass(frozen=True)
class BarriarInterpoleringResultat:
    """Resultat från ensidig brytlinjestyrd markinterpolering (barriär-IDW)."""

    mark_z: NDArray[np.float32]
    avstand_m: NDArray[np.float32]
    barriar_blockerad_mask: NDArray[np.bool_]
    antal_blockerade_celler: int


def _rensa_punktsekvens(
    punkter: Sequence[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Validerar ändliga 2D-koordinater och tar bort direkt på varandra följande dubbletter."""
    rensade: list[tuple[float, float]] = []
    for p in punkter:
        e = float(p[0])
        n = float(p[1])
        if not (math.isfinite(e) and math.isfinite(n)):
            raise GrundkartaFel(f"Ogiltig koordinat: {p!r}")
        if (
            rensade
            and math.hypot(e - rensade[-1][0], n - rensade[-1][1]) <= _DUPLICERAD_PUNKT_TOL_M
        ):
            continue
        rensade.append((e, n))
    return rensade


def _polylinje_langd(punkter: Sequence[tuple[float, float]]) -> float:
    """Beräknar total euklidisk längd längs en polylinje."""
    total = 0.0
    for idx in range(len(punkter) - 1):
        e0, n0 = punkter[idx]
        e1, n1 = punkter[idx + 1]
        total += math.hypot(e1 - e0, n1 - n0)
    return total


def _signerad_area(ring: Sequence[tuple[float, float]]) -> float:
    """Beräknar signerad shoelace-area (positiv för moturs/CCW ring)."""
    n_pts = len(ring)
    if n_pts < 3:
        return 0.0
    horn = ring[:-1] if ring[0] == ring[-1] and n_pts > 3 else ring
    m = len(horn)
    if m < 3:
        return 0.0
    # Skifta till lokal origo för att undvika kancellering i EPSG:3011
    e_ref, n_ref = horn[0]
    dubbel_area = 0.0
    for idx in range(m):
        e0, n0 = horn[idx]
        e1, n1 = horn[(idx + 1) % m]
        dubbel_area += (e0 - e_ref) * (n1 - n_ref) - (e1 - e_ref) * (n0 - n_ref)
    return 0.5 * dubbel_area


def _avstand_punkt_till_segment(
    pe: float,
    pn: float,
    e0: float,
    n0: float,
    e1: float,
    n1: float,
) -> float:
    """Vinkelrätt eller ändpunktsavstånd från punkt `(pe, pn)` till segmentet `(e0,n0)-(e1,n1)`."""
    de = e1 - e0
    dn = n1 - n0
    l2 = de * de + dn * dn
    if l2 <= 1e-24:
        return math.hypot(pe - e0, pn - n0)
    t = ((pe - e0) * de + (pn - n0) * dn) / l2
    t_klippt = max(0.0, min(1.0, t))
    proj_e = e0 + t_klippt * de
    proj_n = n0 + t_klippt * dn
    return math.hypot(pe - proj_e, pn - proj_n)


def forenkla_linje_dp(
    punkter: Sequence[tuple[float, float]],
    tolerans_m: float = 0.02,
) -> tuple[tuple[float, float], ...]:
    """Deterministisk Douglas–Peucker-förenkling som alltid behåller ändpunkter."""
    if not math.isfinite(tolerans_m) or tolerans_m < 0.0:
        raise GrundkartaFel(f"Ogiltig förenklingstolerans: {tolerans_m}")

    validerade: list[tuple[float, float]] = []
    for p in punkter:
        e = float(p[0])
        n = float(p[1])
        if not (math.isfinite(e) and math.isfinite(n)):
            raise GrundkartaFel(f"Ogiltig koordinat i forenkla_linje_dp: {p!r}")
        validerade.append((e, n))

    antal = len(validerade)
    if antal <= 2:
        return tuple(validerade)

    behall = [False] * antal
    behall[0] = True
    behall[-1] = True

    stack: list[tuple[int, int]] = [(0, antal - 1)]
    while stack:
        start, slut = stack.pop()
        if slut - start <= 1:
            continue

        e0, n0 = validerade[start]
        e1, n1 = validerade[slut]
        max_dist = -1.0
        max_idx = -1
        for idx in range(start + 1, slut):
            pe, pn = validerade[idx]
            dist = _avstand_punkt_till_segment(pe, pn, e0, n0, e1, n1)
            if dist > max_dist:
                max_dist = dist
                max_idx = idx

        if max_idx != -1 and max_dist > tolerans_m:
            behall[max_idx] = True
            stack.append((start, max_idx))
            stack.append((max_idx, slut))

    return tuple(p for p, ok in zip(validerade, behall, strict=True) if ok)


def _bygg_sluten_byggnad(
    ring: Sequence[tuple[float, float]],
    hinderhojd_m: float,
) -> Byggnad | None:
    """Validerar och returnerar en sluten `Byggnad` om ringen har >= 3 hörn och positiv area."""
    rensad = _rensa_punktsekvens(ring)
    if len(rensad) >= 2 and math.hypot(
        rensad[0][0] - rensad[-1][0], rensad[0][1] - rensad[-1][1]
    ) <= _DUPLICERAD_PUNKT_TOL_M:
        rensad = rensad[:-1]
    if len(rensad) < 3:
        return None
    area = _signerad_area(rensad)
    if abs(area) <= _MIN_BYGGNAD_AREA_M2:
        return None
    if area < 0.0:
        rensad.reverse()
    sluten = tuple(rensad) + (rensad[0],)
    return Byggnad(polygon=sluten, hinderhojd_m=hinderhojd_m)


def _kedja_oppna_kanter(
    kanter: list[tuple[int, int, list[tuple[float, float]]]],
) -> list[tuple[tuple[float, float], ...]]:
    """Slår ihop kvarvarande öppna grafkanter längs gemensamma grad-2-noder."""
    if not kanter:
        return []

    adj: dict[int, list[int]] = {}
    for k_idx, (u, v, _) in enumerate(kanter):
        adj.setdefault(u, []).append(k_idx)
        adj.setdefault(v, []).append(k_idx)

    anvand = [False] * len(kanter)
    kedjor: list[tuple[tuple[float, float], ...]] = []

    def bygg_kedja_fran(start_nod: int, forsta_kant: int) -> list[tuple[float, float]]:
        anvand[forsta_kant] = True
        u, v, pts = kanter[forsta_kant]
        if start_nod == u:
            kedja = list(pts)
            curr = v
        else:
            kedja = list(reversed(pts))
            curr = u

        while True:
            kvar = [ki for ki in adj.get(curr, ()) if not anvand[ki]]
            if not kvar or len(adj.get(curr, ())) != 2:
                break
            nasta = kvar[0]
            anvand[nasta] = True
            nu, nv, npts = kanter[nasta]
            if curr == nu:
                kedja.extend(npts[1:])
                curr = nv
            else:
                kedja.extend(list(reversed(npts))[1:])
                curr = nu
        return kedja

    # Börja vid noder med grad != 2 (ändpunkter och förgreningar) i deterministisk ordning
    for nod in sorted(adj.keys()):
        if len(adj[nod]) == 2:
            continue
        for k_idx in sorted(adj[nod]):
            if not anvand[k_idx]:
                kedjor.append(tuple(bygg_kedja_fran(nod, k_idx)))

    # Eventuella återstående isolerade komponenter (t.ex. noll-area-loopar)
    for k_idx in range(len(kanter)):
        if not anvand[k_idx]:
            u = kanter[k_idx][0]
            kedjor.append(tuple(bygg_kedja_fran(u, k_idx)))

    return kedjor


def stang_husliv_till_byggnader(
    segment: Sequence[Sequence[tuple[float, float]]],
    *,
    tolerans_m: float = 0.25,
    hinderhojd_m: float = 10.0,
) -> tuple[list[Byggnad], list[tuple[tuple[float, float], ...]]]:
    """Kopplar ihop lösa huslivssegment (glapp <= `tolerans_m`) till slutna byggnadspolygoner.

    Öppna husliv som inte bildar sluten polygon med positiv area stängs aldrig med gissning
    utan returneras i `oppna_rester` så att anroparen kan varna användaren.
    """
    if not math.isfinite(tolerans_m) or tolerans_m < 0.0:
        raise GrundkartaFel(f"Ogiltig snaptolerans för husliv: {tolerans_m}")
    if not math.isfinite(hinderhojd_m) or hinderhojd_m <= 0.0:
        raise GrundkartaFel(f"Ogiltig hinderhöjd för byggnader: {hinderhojd_m}")

    byggnader: list[Byggnad] = []
    oppna_segment: list[list[tuple[float, float]]] = []
    oppna_rester: list[tuple[tuple[float, float], ...]] = []

    for ra_seg in segment:
        pts = _rensa_punktsekvens(ra_seg)
        if len(pts) < 2 or _polylinje_langd(pts) < _DEGENERERAD_LANGD_M:
            continue

        # Kontrollera om segmentet redan är slutet på egen hand (inkl. litet glapp <= tolerans_m)
        if (
            len(pts) >= 3
            and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) <= tolerans_m
        ):
            slutpunkt = (
                0.5 * (pts[0][0] + pts[-1][0]),
                0.5 * (pts[0][1] + pts[-1][1]),
            )
            ring_pts = [slutpunkt, *pts[1:-1], slutpunkt]
            bygg = _bygg_sluten_byggnad(ring_pts, hinderhojd_m)
            if bygg is not None:
                byggnader.append(bygg)
                continue

        oppna_segment.append(pts)

    if not oppna_segment:
        return byggnader, oppna_rester

    # Klustra ändpunkter med deterministisk union-find inom tolerans_m
    andposter: list[tuple[int, int, tuple[float, float]]] = []
    for s_idx, pts in enumerate(oppna_segment):
        andposter.append((s_idx, 0, pts[0]))
        andposter.append((s_idx, 1, pts[-1]))

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
            if de * de + dn * dn <= tol2 and andposter[i][0] != andposter[j][0]:
                slaihop(i, j)

    kluster: dict[int, list[int]] = {}
    for i in range(n_andar):
        kluster.setdefault(hitta(i), []).append(i)

    rot_till_nod: dict[int, int] = {}
    nod_koord: list[tuple[float, float]] = []
    for rot in sorted(kluster.keys()):
        medlemmar = kluster[rot]
        me = sum(andposter[m][2][0] for m in medlemmar) / len(medlemmar)
        mn = sum(andposter[m][2][1] for m in medlemmar) / len(medlemmar)
        rot_till_nod[rot] = len(nod_koord)
        nod_koord.append((me, mn))

    # Bygg grafkanter och filtrera exakta dubblettkanter mellan samma nodpar
    kanter: list[tuple[int, int, list[tuple[float, float]]]] = []
    sedda_kanter: set[tuple[int, int, int]] = set()
    for s_idx, pts in enumerate(oppna_segment):
        u = rot_till_nod[hitta(2 * s_idx)]
        v = rot_till_nod[hitta(2 * s_idx + 1)]
        snappade_pts = [nod_koord[u], *pts[1:-1], nod_koord[v]]
        snappade_pts = _rensa_punktsekvens(snappade_pts)
        if len(snappade_pts) < 2:
            continue
        if u == v:
            bygg = _bygg_sluten_byggnad(snappade_pts, hinderhojd_m)
            if bygg is not None:
                byggnader.append(bygg)
            else:
                oppna_rester.append(tuple(snappade_pts))
            continue

        # Undvik dubbla identiska väggar i exakt samma nodpar med samma längd
        langd_nyckel = round(_polylinje_langd(snappade_pts) * 1000.0)
        nyckel = (min(u, v), max(u, v), langd_nyckel)
        if nyckel in sedda_kanter and len(snappade_pts) == 2:
            continue
        sedda_kanter.add(nyckel)
        kanter.append((u, v, snappade_pts))

    if not kanter:
        return byggnader, oppna_rester

    # 2-kärnedekomponering: skala bort lösa grenar (grad 1) innan cykelextraktion
    aktiv_kant = [True] * len(kanter)
    nod_kanter: dict[int, set[int]] = {}
    for k_idx, (u, v, _) in enumerate(kanter):
        nod_kanter.setdefault(u, set()).add(k_idx)
        nod_kanter.setdefault(v, set()).add(k_idx)

    grad_ett = [nod for nod, ks in nod_kanter.items() if len(ks) == 1]
    while grad_ett:
        nod = grad_ett.pop()
        ks = nod_kanter.get(nod)
        if not ks or len(ks) != 1:
            continue
        k_idx = next(iter(ks))
        if not aktiv_kant[k_idx]:
            continue
        aktiv_kant[k_idx] = False
        u, v, _ = kanter[k_idx]
        nod_kanter[u].discard(k_idx)
        nod_kanter[v].discard(k_idx)
        andra = v if nod == u else u
        if len(nod_kanter[andra]) == 1:
            grad_ett.append(andra)

    # Planär halvkant-traversering på 2-kärnan för att extrahera minimala moturs byggnadsytor
    # Varje aktiv kant k har två halvkantar: 2*k (u -> v) och 2*k + 1 (v -> u).
    kerna_kanter = [k_idx for k_idx, ok in enumerate(aktiv_kant) if ok]
    utgaende: dict[int, list[tuple[float, int]]] = {}
    halv_slut: dict[int, int] = {}
    halv_punkter: dict[int, list[tuple[float, float]]] = {}

    for k_idx in kerna_kanter:
        u, v, pts = kanter[k_idx]
        # Halvkant u -> v
        h_fram = 2 * k_idx
        halv_slut[h_fram] = v
        halv_punkter[h_fram] = pts
        vinkel_fram = math.atan2(pts[1][1] - pts[0][1], pts[1][0] - pts[0][0])
        utgaende.setdefault(u, []).append((vinkel_fram, h_fram))

        # Halvkant v -> u
        h_bak = 2 * k_idx + 1
        pts_bak = list(reversed(pts))
        halv_slut[h_bak] = u
        halv_punkter[h_bak] = pts_bak
        vinkel_bak = math.atan2(pts_bak[1][1] - pts_bak[0][1], pts_bak[1][0] - pts_bak[0][0])
        utgaende.setdefault(v, []).append((vinkel_bak, h_bak))

    utgaende_ordnad: dict[int, list[int]] = {}
    halv_pos_i_nod: dict[int, int] = {}
    for nod, poster in utgaende.items():
        poster.sort(key=lambda item: (item[0], item[1]))
        lista = [h for _, h in poster]
        utgaende_ordnad[nod] = lista
        for pos, h in enumerate(lista):
            halv_pos_i_nod[h] = pos

    besokt_halv: set[int] = set()
    anvand_i_byggnad: set[int] = set()

    for k_idx in kerna_kanter:
        for start_h in (2 * k_idx, 2 * k_idx + 1):
            if start_h in besokt_halv:
                continue
            cykel_halv: list[int] = []
            curr_h = start_h
            max_steg = 2 * len(kerna_kanter) + 2
            sluten = False
            for _ in range(max_steg):
                if curr_h in besokt_halv:
                    sluten = curr_h == start_h
                    break
                besokt_halv.add(curr_h)
                cykel_halv.append(curr_h)

                mal_nod = halv_slut[curr_h]
                rev_h = curr_h ^ 1
                lista = utgaende_ordnad[mal_nod]
                pos = halv_pos_i_nod[rev_h]
                # Föregående i CCW-ordning ger skarpaste vänstersväng (begränsad moturs yta)
                curr_h = lista[(pos - 1) % len(lista)]

            if not sluten or not cykel_halv:
                continue

            # Kontrollera att cykeln inte bara är en fram-och-tillbaka-slits över samma kanter
            ring_pts: list[tuple[float, float]] = []
            for h in cykel_halv:
                h_pts = halv_punkter[h]
                if not ring_pts:
                    ring_pts.extend(h_pts)
                else:
                    ring_pts.extend(h_pts[1:])

            # Endast moturs begränsade ytor (positiv signerad area) utgör byggnader;
            # yttre omslutande yta har negativ area och hoppas över.
            area = _signerad_area(ring_pts)
            if area > _MIN_BYGGNAD_AREA_M2:
                bygg = _bygg_sluten_byggnad(ring_pts, hinderhojd_m)
                if bygg is not None:
                    byggnader.append(bygg)
                    for h in cykel_halv:
                        anvand_i_byggnad.add(h // 2)

    oavslutade_kanter = [
        kanter[k_idx] for k_idx in range(len(kanter)) if k_idx not in anvand_i_byggnad
    ]
    oppna_rester.extend(_kedja_oppna_kanter(oavslutade_kanter))

    return byggnader, oppna_rester


def _max_punkt_till_polylinje_avstand(
    punkter: Sequence[tuple[float, float]],
    mal: Sequence[tuple[float, float]],
) -> float:
    """Beräknar största avståndet från varje punkt i `punkter` till polylinjen `mal`."""
    max_d = 0.0
    for pe, pn in punkter:
        min_d = math.inf
        for idx in range(len(mal) - 1):
            e0, n0 = mal[idx]
            e1, n1 = mal[idx + 1]
            d = _avstand_punkt_till_segment(pe, pn, e0, n0, e1, n1)
            if d < min_d:
                min_d = d
        if min_d > max_d:
            max_d = min_d
    return max_d


def _ar_nara_dubblett(
    a: Brytlinje,
    b: Brytlinje,
    tolerans_m: float,
) -> bool:
    """Avgör riktningsoberoende om två brytlinjer av samma typ är dubbletter inom `tolerans_m`."""
    if a.typ != b.typ:
        return False

    pa = a.punkter
    pb = b.punkter
    # Snabb ändpunktskontroll i framåt- eller bakåtriktning
    fram = (
        math.hypot(pa[0][0] - pb[0][0], pa[0][1] - pb[0][1]) <= tolerans_m
        and math.hypot(pa[-1][0] - pb[-1][0], pa[-1][1] - pb[-1][1]) <= tolerans_m
    )
    bak = (
        math.hypot(pa[0][0] - pb[-1][0], pa[0][1] - pb[-1][1]) <= tolerans_m
        and math.hypot(pa[-1][0] - pb[0][0], pa[-1][1] - pb[0][1]) <= tolerans_m
    )
    if not (fram or bak):
        return False

    # Längdkontroll
    la = _polylinje_langd(pa)
    lb = _polylinje_langd(pb)
    if abs(la - lb) > max(2.0 * tolerans_m, 0.05 * max(la, lb)):
        return False

    return (
        _max_punkt_till_polylinje_avstand(pa, pb) <= tolerans_m
        and _max_punkt_till_polylinje_avstand(pb, pa) <= tolerans_m
    )


def rensa_baskarte_topologi(
    brytlinjer: Sequence[Brytlinje],
    *,
    snap_tolerans_m: float = 0.25,
    dubblett_tolerans_m: float = 0.05,
    forenkling_tolerans_m: float = 0.02,
) -> tuple[list[Brytlinje], TopologiRapport]:
    """Rensar brytlinjer från baskarta: degenererade, förenkling, dubbletter och snapping."""
    if not math.isfinite(snap_tolerans_m) or snap_tolerans_m < 0.0:
        raise GrundkartaFel(f"Ogiltig snap_tolerans_m: {snap_tolerans_m}")
    if not math.isfinite(dubblett_tolerans_m) or dubblett_tolerans_m < 0.0:
        raise GrundkartaFel(f"Ogiltig dubblett_tolerans_m: {dubblett_tolerans_m}")
    if not math.isfinite(forenkling_tolerans_m) or forenkling_tolerans_m < 0.0:
        raise GrundkartaFel(f"Ogiltig forenkling_tolerans_m: {forenkling_tolerans_m}")

    antal_in = len(brytlinjer)
    antal_degenererade = 0
    antal_forenklade = 0
    forenklade_linjer: list[Brytlinje] = []

    for linje in brytlinjer:
        rensade_pts = _rensa_punktsekvens(linje.punkter)
        if len(rensade_pts) < 2 or _polylinje_langd(rensade_pts) < _DEGENERERAD_LANGD_M:
            antal_degenererade += 1
            continue

        dp_pts = forenkla_linje_dp(rensade_pts, tolerans_m=forenkling_tolerans_m)
        antal_forenklade += len(linje.punkter) - len(dp_pts)
        if len(dp_pts) < 2 or _polylinje_langd(dp_pts) < _DEGENERERAD_LANGD_M:
            antal_degenererade += 1
            continue

        forenklade_linjer.append(
            Brytlinje(typ=linje.typ, hojd_m=linje.hojd_m, punkter=dp_pts)
        )

    # Ta bort exakta/nära-duplicerade linjer (oberoende av ritriktning) med bbox-prefilter
    unika: list[Brytlinje] = []
    unika_bbox: list[tuple[float, float, float, float]] = []
    antal_dubbletter = 0

    for linje in forenklade_linjer:
        pe = [p[0] for p in linje.punkter]
        pn = [p[1] for p in linje.punkter]
        bbox = (min(pe), min(pn), max(pe), max(pn))

        ar_dubblett = False
        for bef, (be0, bn0, be1, bn1) in zip(unika, unika_bbox, strict=True):
            if (
                bbox[2] + dubblett_tolerans_m < be0
                or bbox[0] - dubblett_tolerans_m > be1
                or bbox[3] + dubblett_tolerans_m < bn0
                or bbox[1] - dubblett_tolerans_m > bn1
            ):
                continue
            if _ar_nara_dubblett(linje, bef, dubblett_tolerans_m):
                ar_dubblett = True
                break

        if ar_dubblett:
            antal_dubbletter += 1
        else:
            unika.append(linje)
            unika_bbox.append(bbox)

    snappade, _ = fast_losa_andar(unika, tolerans_m=snap_tolerans_m)

    rapport = TopologiRapport(
        antal_segment_in=antal_in,
        antal_dubbletter_borttagna=antal_dubbletter,
        antal_degenererade_borttagna=antal_degenererade,
        antal_stangda_byggnader=0,
        antal_oppna_husliv_varningar=0,
        antal_forenklade_punkter=antal_forenklade,
    )
    return snappade, rapport


def _bygg_lagerregler(
    lagermappning: Mapping[str, tuple[str, float]] | None,
) -> tuple[LagerMappning, ...]:
    """Konverterar en lagermappnings-dict till validerade och längdsorterade regler."""
    kalla = STANDARD_STOCKHOLM_LAGERMAPPNING if lagermappning is None else lagermappning
    regler: list[LagerMappning] = []
    for prefix, (roll, standard_m) in kalla.items():
        regler.append(LagerMappning(prefix=prefix, roll=roll, standard_m=float(standard_m)))
    # Längsta prefix först så mer specifika lagernamn alltid vinner deterministiskt
    regler.sort(key=lambda r: (-len(r.prefix), r.prefix))
    return tuple(regler)


def _matcha_lager(lager: str, regler: Sequence[LagerMappning]) -> LagerMappning | None:
    """Matchar lagernamn mot regler: först exakt träff, sedan längsta prefixträff."""
    norm = lager.strip().upper()
    for regel in regler:
        if norm == regel.prefix:
            return regel
    for regel in regler:
        if regel.matchar(norm):
            return regel
    return None


def _tolka_dxf_float(varde: str, kontext: str) -> float:
    """Tolkar flyttal i ASCII-DXF och kräver ändligt värde."""
    try:
        tal = float(varde)
    except ValueError as exc:
        raise GrundkartaFel(f"Ogiltigt tal i DXF ({kontext}): {varde!r}") from exc
    if not math.isfinite(tal):
        raise GrundkartaFel(f"Icke-ändlig koordinat i DXF ({kontext}): {varde!r}")
    return tal


def _tolka_dxf_int(varde: str, kontext: str) -> int:
    """Tolkar heltal i ASCII-DXF."""
    try:
        return int(varde)
    except ValueError as exc:
        raise GrundkartaFel(f"Ogiltigt heltal i DXF ({kontext}): {varde!r}") from exc


def _parsa_dxf_par(dxf_text: str) -> list[tuple[int, str]]:
    """Läser par av `(gruppkod, värde)` från ASCII-DXF-text."""
    rader = dxf_text.splitlines()
    if len(rader) < 2:
        raise GrundkartaFel("DXF-innehållet är tomt eller för kort.")

    par: list[tuple[int, str]] = []
    idx = 0
    n_rader = len(rader)
    while idx < n_rader:
        kod_rad = rader[idx].strip()
        if not kod_rad:
            idx += 1
            continue
        if idx + 1 >= n_rader:
            raise GrundkartaFel(f"Oparat slut i DXF efter gruppkod {kod_rad!r}.")
        kod = _tolka_dxf_int(kod_rad, f"rad {idx + 1}")
        varde = rader[idx + 1].strip()
        par.append((kod, varde))
        idx += 2
    return par


def _extrahera_dxf_entiteter(
    par: Sequence[tuple[int, str]],
) -> tuple[list[tuple[str, list[tuple[float, float]], float | None]], bool]:
    """Extraherar `LINE`, `LWPOLYLINE` och `POLYLINE`/`VERTEX`/`SEQEND` från `ENTITIES`."""
    entiteter: list[tuple[str, list[tuple[float, float]], float | None]] = []
    hittade_entities = False
    i = 0
    n = len(par)

    while i < n:
        kod, val = par[i]
        if (
            kod == 0
            and val.upper() == "SECTION"
            and i + 1 < n
            and par[i + 1][0] == 2
            and par[i + 1][1].upper() == "ENTITIES"
        ):
            hittade_entities = True
            i += 2
            # Läs entiteter fram till ENDSEC
            while i < n:
                ekod, eval_ = par[i]
                if ekod == 0 and eval_.upper() in ("ENDSEC", "EOF"):
                    i += 1
                    break
                if ekod != 0:
                    i += 1
                    continue

                typ = eval_.upper()
                i += 1
                if typ == "LINE":
                    lager = "0"
                    x0: float | None = None
                    y0: float | None = None
                    x1: float | None = None
                    y1: float | None = None
                    z_varden: list[float] = []
                    while i < n and par[i][0] != 0:
                        gk, gv = par[i]
                        if gk == 8:
                            lager = gv
                        elif gk == 10:
                            x0 = _tolka_dxf_float(gv, "LINE 10")
                        elif gk == 20:
                            y0 = _tolka_dxf_float(gv, "LINE 20")
                        elif gk == 11:
                            x1 = _tolka_dxf_float(gv, "LINE 11")
                        elif gk == 21:
                            y1 = _tolka_dxf_float(gv, "LINE 21")
                        elif gk in (30, 31):
                            z_varden.append(_tolka_dxf_float(gv, f"LINE {gk}"))
                        i += 1
                    if x0 is None or y0 is None or x1 is None or y1 is None:
                        raise GrundkartaFel(
                            f"Ofullständig LINE-entitet på lager '{lager}' (saknar 10/20/11/21)."
                        )
                    z_medel = sum(z_varden) / len(z_varden) if z_varden else None
                    entiteter.append((lager, [(x0, y0), (x1, y1)], z_medel))

                elif typ == "LWPOLYLINE":
                    lager = "0"
                    flagga = 0
                    pts: list[tuple[float, float]] = []
                    akt_x: float | None = None
                    elevation: float | None = None
                    while i < n and par[i][0] != 0:
                        gk, gv = par[i]
                        if gk == 8:
                            lager = gv
                        elif gk == 70:
                            flagga = _tolka_dxf_int(gv, "LWPOLYLINE 70")
                        elif gk == 38:
                            elevation = _tolka_dxf_float(gv, "LWPOLYLINE 38")
                        elif gk == 10:
                            if akt_x is not None:
                                raise GrundkartaFel(
                                    f"LWPOLYLINE på lager '{lager}' har X utan matchande Y."
                                )
                            akt_x = _tolka_dxf_float(gv, "LWPOLYLINE 10")
                        elif gk == 20:
                            if akt_x is None:
                                raise GrundkartaFel(
                                    f"LWPOLYLINE på lager '{lager}' har Y utan föregående X."
                                )
                            pts.append((akt_x, _tolka_dxf_float(gv, "LWPOLYLINE 20")))
                            akt_x = None
                        i += 1
                    if akt_x is not None:
                        raise GrundkartaFel(
                            f"LWPOLYLINE på lager '{lager}' slutar med X utan Y."
                        )
                    if (flagga & 1) != 0 and len(pts) >= 2 and pts[0] != pts[-1]:
                        pts.append(pts[0])
                    entiteter.append((lager, pts, elevation))

                elif typ == "POLYLINE":
                    lager = "0"
                    flagga = 0
                    pts = []
                    z_varden = []
                    while i < n and par[i][0] != 0:
                        gk, gv = par[i]
                        if gk == 8:
                            lager = gv
                        elif gk == 70:
                            flagga = _tolka_dxf_int(gv, "POLYLINE 70")
                        elif gk == 30:
                            z_varden.append(_tolka_dxf_float(gv, "POLYLINE 30"))
                        i += 1

                    while i < n:
                        if par[i][0] != 0:
                            i += 1
                            continue
                        sub_typ = par[i][1].upper()
                        if sub_typ == "SEQEND":
                            i += 1
                            while i < n and par[i][0] != 0:
                                i += 1
                            break
                        if sub_typ == "VERTEX":
                            i += 1
                            vx: float | None = None
                            vy: float | None = None
                            v_flagga = 0
                            while i < n and par[i][0] != 0:
                                gk, gv = par[i]
                                if gk == 10:
                                    vx = _tolka_dxf_float(gv, "VERTEX 10")
                                elif gk == 20:
                                    vy = _tolka_dxf_float(gv, "VERTEX 20")
                                elif gk == 30:
                                    z_varden.append(_tolka_dxf_float(gv, "VERTEX 30"))
                                elif gk == 70:
                                    v_flagga = _tolka_dxf_int(gv, "VERTEX 70")
                                i += 1
                            ar_polyface_index = (v_flagga & 128) != 0 and (v_flagga & 64) == 0
                            if not ar_polyface_index:
                                if vx is None or vy is None:
                                    raise GrundkartaFel(
                                        f"VERTEX i POLYLINE på lager '{lager}' saknar 10/20."
                                    )
                                pts.append((vx, vy))
                        else:
                            # Oväntat slut utan SEQEND: avsluta POLYLINE och låt yttre loop
                            # behandla nästa entitet.
                            break

                    if (flagga & 1) != 0 and len(pts) >= 2 and pts[0] != pts[-1]:
                        pts.append(pts[0])
                    z_medel = sum(z_varden) / len(z_varden) if z_varden else None
                    entiteter.append((lager, pts, z_medel))
                else:
                    # Hoppa över övriga entitetstyper (TEXT, POINT, INSERT m.fl.)
                    while i < n and par[i][0] != 0:
                        i += 1
            continue
        i += 1

    return entiteter, hittade_entities


def las_baskarta_dxf(
    sokvag_eller_text: str | Path,
    *,
    ar_text: bool = False,
    lagermappning: Mapping[str, tuple[str, float]] | None = None,
    snap_tolerans_m: float = 0.25,
) -> BaskartaResultat:
    """Läser en ASCII-DXF baskarta, mappar lager, stänger husliv och topologirensar brytlinjer."""
    if not math.isfinite(snap_tolerans_m) or snap_tolerans_m < 0.0:
        raise GrundkartaFel(f"Ogiltig snap_tolerans_m: {snap_tolerans_m}")

    if ar_text:
        if not isinstance(sokvag_eller_text, str):
            raise GrundkartaFel("När ar_text=True måste sokvag_eller_text vara en sträng.")
        dxf_text = sokvag_eller_text
    else:
        sokvag = Path(sokvag_eller_text)
        if not sokvag.is_file():
            raise GrundkartaFel(f"DXF-filen hittades inte: {sokvag}")
        try:
            dxf_text = sokvag.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise GrundkartaFel(f"Kunde inte läsa DXF-filen {sokvag}: {exc}") from exc

    regler = _bygg_lagerregler(lagermappning)
    par = _parsa_dxf_par(dxf_text)
    entiteter, hittade_entities = _extrahera_dxf_entiteter(par)

    varningar: list[str] = []
    if not hittade_entities:
        varningar.append("DXF-filen saknar en ENTITIES-sektion.")

    ra_brytlinjer: list[Brytlinje] = []
    husliv_per_hojd: dict[float, list[list[tuple[float, float]]]] = {}
    oppningar: list[Oppning] = []
    okanda_lager_set: set[str] = set()
    extra_degenererade = 0
    antal_mappade_segment = 0

    for lager, pts, z_varde in entiteter:
        regel = _matcha_lager(lager, regler)
        if regel is None:
            okanda_lager_set.add(lager.strip() or "0")
            continue

        antal_mappade_segment += 1
        rensade = _rensa_punktsekvens(pts)
        if len(rensade) < 2 or _polylinje_langd(rensade) < _DEGENERERAD_LANGD_M:
            extra_degenererade += 1
            continue

        if regel.roll in GILTIGA_BRYTLINJE_ROLLER:
            ra_brytlinjer.append(
                Brytlinje(
                    typ=normalisera_brytlinjetyp(regel.roll),
                    hojd_m=regel.standard_m,
                    punkter=tuple(rensade),
                )
            )
        elif regel.roll == "byggnad":
            husliv_per_hojd.setdefault(regel.standard_m, []).append(rensade)
        elif regel.roll in GILTIGA_OPPNING_ROLLER:
            botten_z = 0.0 if z_varde is None else z_varde
            if z_varde is None:
                varningar.append(
                    f"Öppning ({regel.roll}) på lager '{lager}' saknar Z-värde i DXF; "
                    "botten_rh2000 satt till 0.0 m och behöver höjdsättas."
                )
            oppningar.append(
                Oppning(
                    typ=regel.roll,
                    bredd_m=regel.standard_m,
                    botten_rh2000=botten_z,
                    kalla=f"baskarta_dxf:{lager}",
                    linje=tuple(rensade),
                )
            )

    okanda_lager = tuple(sorted(okanda_lager_set))
    for okant in okanda_lager:
        varningar.append(f"Okänt baskartelager ignorerat: '{okant}'.")

    byggnader: list[Byggnad] = []
    oppna_husliv: list[tuple[tuple[float, float], ...]] = []
    for hinderhojd in sorted(husliv_per_hojd.keys()):
        b_del, o_del = stang_husliv_till_byggnader(
            husliv_per_hojd[hinderhojd],
            tolerans_m=snap_tolerans_m,
            hinderhojd_m=hinderhojd,
        )
        byggnader.extend(b_del)
        oppna_husliv.extend(o_del)

    for idx, oppet in enumerate(oppna_husliv):
        varningar.append(
            f"Öppet husliv #{idx + 1} ({len(oppet)} punkter) kunde inte stängas inom "
            f"{snap_tolerans_m:.2f} m och hoppades över utan gissning."
        )

    rensade_bryt, bryt_rapport = rensa_baskarte_topologi(
        ra_brytlinjer,
        snap_tolerans_m=snap_tolerans_m,
    )

    topologi = TopologiRapport(
        antal_segment_in=antal_mappade_segment,
        antal_dubbletter_borttagna=bryt_rapport.antal_dubbletter_borttagna,
        antal_degenererade_borttagna=bryt_rapport.antal_degenererade_borttagna
        + extra_degenererade,
        antal_stangda_byggnader=len(byggnader),
        antal_oppna_husliv_varningar=len(oppna_husliv),
        antal_forenklade_punkter=bryt_rapport.antal_forenklade_punkter,
    )

    return BaskartaResultat(
        brytlinjer=tuple(rensade_bryt),
        byggnader=tuple(byggnader),
        oppningar=tuple(oppningar),
        okanda_lager=okanda_lager,
        topologi=topologi,
        varningar=tuple(varningar),
    )


def _projicera_punkter_pa_polylinje(
    punkter_e: NDArray[np.float64],
    punkter_n: NDArray[np.float64],
    linje_punkter: tuple[tuple[float, float], ...],
    max_avstand_m: float,
) -> tuple[NDArray[np.bool_], NDArray[np.float64]]:
    """Projicerar punkter ortogonalt på en polylinje och returnerar signerat sidoavstånd.

    Positivt `d_perp` ligger till vänster om linjens riktning, negativt till höger.
    Endast punkter som projicerar inom något segment (`0 <= t <= 1`) och inom
    `max_avstand_m` inkluderas.
    """
    n_pts = punkter_e.size
    basta_abs_d = np.full(n_pts, np.inf, dtype=np.float64)
    basta_d_perp = np.zeros(n_pts, dtype=np.float64)

    for idx in range(len(linje_punkter) - 1):
        e0, n0 = linje_punkter[idx]
        e1, n1 = linje_punkter[idx + 1]
        de = e1 - e0
        dn = n1 - n0
        langd = math.hypot(de, dn)
        if langd <= 1e-9:
            continue

        te = de / langd
        tn = dn / langd
        # Vänsternormal = (-tn, te)
        ne = -tn
        nn = te

        pe = punkter_e - e0
        pn = punkter_n - n0
        langd_proj = pe * te + pn * tn
        inom_seg = (langd_proj >= 0.0) & (langd_proj <= langd)
        if not np.any(inom_seg):
            continue

        d_perp = pe[inom_seg] * ne + pn[inom_seg] * nn
        abs_d = np.abs(d_perp)

        giltig_lokal = abs_d <= max_avstand_m
        if not np.any(giltig_lokal):
            continue

        seg_indices = np.nonzero(inom_seg)[0][giltig_lokal]
        d_perp_g = d_perp[giltig_lokal]
        abs_d_g = abs_d[giltig_lokal]

        battre = abs_d_g < basta_abs_d[seg_indices]
        uppdatera = seg_indices[battre]
        basta_abs_d[uppdatera] = abs_d_g[battre]
        basta_d_perp[uppdatera] = d_perp_g[battre]

    traff = np.isfinite(basta_abs_d)
    return traff, basta_d_perp


def _skatta_sidoforskjutning(
    d_perp: NDArray[np.float64],
    z_lokal: NDArray[np.float64],
    sok_max_m: float,
    antal_steg: int = 41,
) -> float | None:
    """Skattar sidoförskjutning genom att söka det sidoläge som bäst separerar höjdnivåerna.

    Kombinerar robust L1-stegpassning runt sidomedianer med maximal höjdskillnad mellan
    `d_perp > offset` och `d_perp < offset`. Vid exakta oavgjorda lägen (t.ex. glapp i
    punktmolnet mitt på språnget) tas medelvärdet av de optimala lägena.
    """
    if d_perp.size < 6:
        return None
    if sok_max_m == 0.0:
        har_v = int(np.count_nonzero(d_perp > 0.0)) >= 3
        har_h = int(np.count_nonzero(d_perp < 0.0)) >= 3
        return 0.0 if (har_v and har_h) else None

    kandidater = np.linspace(-sok_max_m, sok_max_m, antal_steg, dtype=np.float64)
    basta_fel = math.inf
    basta_sprang = -1.0
    optimala_offsets: list[float] = []
    n_tot = float(d_perp.size)

    for offset_val in kandidater:
        offset = float(offset_val)
        v_mask = d_perp >= offset
        h_mask = ~v_mask
        if int(np.count_nonzero(v_mask)) < 3 or int(np.count_nonzero(h_mask)) < 3:
            continue

        z_v = z_lokal[v_mask]
        z_h = z_lokal[h_mask]
        med_v = float(np.median(z_v))
        med_h = float(np.median(z_h))
        sprang = abs(med_v - med_h)

        # Robust medel-L1-residual mot tvåsidig stegfunktion (strikt partition av alla punkter)
        l1_fel = float(np.sum(np.abs(z_v - med_v)) + np.sum(np.abs(z_h - med_h))) / n_tot
        # Symmetrisk skattning av själva språnggränsen mellan närmaste höger- och vänsterpunkt
        grans_mitt = 0.5 * (float(np.max(d_perp[h_mask])) + float(np.min(d_perp[v_mask])))
        grans_mitt = max(-sok_max_m, min(sok_max_m, grans_mitt))

        if l1_fel < basta_fel - 1e-12:
            basta_fel = l1_fel
            basta_sprang = sprang
            optimala_offsets = [grans_mitt]
        elif abs(l1_fel - basta_fel) <= 1e-12:
            if sprang > basta_sprang + 1e-9:
                basta_sprang = sprang
                optimala_offsets = [grans_mitt]
            elif abs(sprang - basta_sprang) <= 1e-9:
                optimala_offsets.append(grans_mitt)

    if not optimala_offsets:
        return None
    if basta_sprang <= 1e-6:
        return 0.0
    return float(sum(optimala_offsets) / len(optimala_offsets))


def hojdsatt_brytlinjer_fran_laser(
    brytlinjer: Sequence[Brytlinje],
    punkter_e: NDArray[np.float64],
    punkter_n: NDArray[np.float64],
    punkter_z: NDArray[np.float64],
    *,
    sidoband_min_m: float = 0.15,
    sidoband_max_m: float = 1.5,
    sok_sidoforskjutning_max_m: float = 0.5,
    klasser: NDArray[np.int32] | NDArray[np.uint8] | None = None,
) -> tuple[list[Brytlinje], list[LinjeLaserAnalys]]:
    """Höjdsätter brytlinjer från laserpunktmoln och skattar eventuell sidoförskjutning."""
    if not math.isfinite(sidoband_min_m) or sidoband_min_m < 0.0:
        raise GrundkartaFel(f"Ogiltigt sidoband_min_m: {sidoband_min_m}")
    if not math.isfinite(sidoband_max_m) or sidoband_max_m <= sidoband_min_m:
        raise GrundkartaFel(
            f"sidoband_max_m ({sidoband_max_m}) måste vara större än "
            f"sidoband_min_m ({sidoband_min_m})."
        )
    if not math.isfinite(sok_sidoforskjutning_max_m) or sok_sidoforskjutning_max_m < 0.0:
        raise GrundkartaFel(f"Ogiltigt sok_sidoforskjutning_max_m: {sok_sidoforskjutning_max_m}")

    e, n, z = filtrera_laser_klasser(punkter_e, punkter_n, punkter_z, klasser=klasser)

    uppdaterade: list[Brytlinje] = []
    analyser: list[LinjeLaserAnalys] = []
    sok_radie = sidoband_max_m + sok_sidoforskjutning_max_m

    for idx, linje in enumerate(brytlinjer):
        if e.size == 0:
            uppdaterade.append(linje)
            analyser.append(
                LinjeLaserAnalys(
                    linje_index=idx,
                    typ=linje.typ,
                    vanster_median_z=None,
                    hoger_median_z=None,
                    uppmatt_sprang_m=None,
                    sidoforskjutning_m=None,
                    antal_punkter_vanster=0,
                    antal_punkter_hoger=0,
                    osaker=True,
                )
            )
            continue

        le = [p[0] for p in linje.punkter]
        ln = [p[1] for p in linje.punkter]
        bbox_mask = (
            (e >= min(le) - sok_radie)
            & (e <= max(le) + sok_radie)
            & (n >= min(ln) - sok_radie)
            & (n <= max(ln) + sok_radie)
        )
        if not np.any(bbox_mask):
            uppdaterade.append(linje)
            analyser.append(
                LinjeLaserAnalys(
                    linje_index=idx,
                    typ=linje.typ,
                    vanster_median_z=None,
                    hoger_median_z=None,
                    uppmatt_sprang_m=None,
                    sidoforskjutning_m=None,
                    antal_punkter_vanster=0,
                    antal_punkter_hoger=0,
                    osaker=True,
                )
            )
            continue

        e_sub = e[bbox_mask]
        n_sub = n[bbox_mask]
        z_sub = z[bbox_mask]

        traff, d_perp_alla = _projicera_punkter_pa_polylinje(
            e_sub, n_sub, linje.punkter, max_avstand_m=sok_radie
        )
        d_perp = d_perp_alla[traff]
        z_lokal = z_sub[traff]

        # Skatta sidoförskjutning inom [-sidoband_max_m, +sidoband_max_m]
        sok_mask = np.abs(d_perp) <= sidoband_max_m
        sido_offset = _skatta_sidoforskjutning(
            d_perp[sok_mask],
            z_lokal[sok_mask],
            sok_max_m=sok_sidoforskjutning_max_m,
        )

        # Utvärdera sidobanden centrerade kring skattad sidoförskjutning (om båda sidor har >= 3
        # punkter), annars kring originalpositionen 0.0.
        eff_offset = 0.0 if sido_offset is None else sido_offset
        d_just = d_perp - eff_offset
        v_mask = (d_just >= sidoband_min_m) & (d_just <= sidoband_max_m)
        h_mask = (d_just <= -sidoband_min_m) & (d_just >= -sidoband_max_m)
        if (
            eff_offset != 0.0
            and (int(np.count_nonzero(v_mask)) < 3 or int(np.count_nonzero(h_mask)) < 3)
        ):
            v_mask = (d_perp >= sidoband_min_m) & (d_perp <= sidoband_max_m)
            h_mask = (d_perp <= -sidoband_min_m) & (d_perp >= -sidoband_max_m)

        antal_v = int(np.count_nonzero(v_mask))
        antal_h = int(np.count_nonzero(h_mask))
        med_v = float(np.median(z_lokal[v_mask])) if antal_v > 0 else None
        med_h = float(np.median(z_lokal[h_mask])) if antal_h > 0 else None

        if antal_v >= 3 and antal_h >= 3 and med_v is not None and med_h is not None:
            sprang = abs(med_v - med_h)
            osaker = False
            uppdaterade.append(
                Brytlinje(typ=linje.typ, hojd_m=sprang, punkter=linje.punkter)
            )
        else:
            sprang = abs(med_v - med_h) if (med_v is not None and med_h is not None) else None
            osaker = True
            uppdaterade.append(linje)

        analyser.append(
            LinjeLaserAnalys(
                linje_index=idx,
                typ=linje.typ,
                vanster_median_z=med_v,
                hoger_median_z=med_h,
                uppmatt_sprang_m=sprang,
                sidoforskjutning_m=sido_offset,
                antal_punkter_vanster=antal_v,
                antal_punkter_hoger=antal_h,
                osaker=osaker,
            )
        )

    return uppdaterade, analyser


def _extrahera_barriarsegment(
    brytlinjer: Sequence[Brytlinje],
    e_ref: float,
    n_ref: float,
) -> tuple[
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
]:
    """Konverterar brytlinjer till lokala segmentvektorer + bbox för snabb 2D-skärning."""
    ce_lista: list[float] = []
    cn_lista: list[float] = []
    de_lista: list[float] = []
    dn_lista: list[float] = []

    for linje in brytlinjer:
        for idx in range(len(linje.punkter) - 1):
            e0, n0 = linje.punkter[idx]
            e1, n1 = linje.punkter[idx + 1]
            if math.hypot(e1 - e0, n1 - n0) <= _DUPLICERAD_PUNKT_TOL_M:
                continue
            ce_lista.append(e0 - e_ref)
            cn_lista.append(n0 - n_ref)
            de_lista.append(e1 - e_ref)
            dn_lista.append(n1 - n_ref)

    ce = np.asarray(ce_lista, dtype=np.float64)
    cn = np.asarray(cn_lista, dtype=np.float64)
    de = np.asarray(de_lista, dtype=np.float64)
    dn = np.asarray(dn_lista, dtype=np.float64)
    s_emin = np.minimum(ce, de)
    s_emax = np.maximum(ce, de)
    s_nmin = np.minimum(cn, dn)
    s_nmax = np.maximum(cn, dn)
    return ce, cn, de, dn, s_emin, s_emax, s_nmin, s_nmax


def _segment_skars_av_barriar(
    ae: NDArray[np.float64],
    an: NDArray[np.float64],
    be: NDArray[np.float64],
    bn: NDArray[np.float64],
    ce: NDArray[np.float64],
    cn: NDArray[np.float64],
    de: NDArray[np.float64],
    dn: NDArray[np.float64],
    s_emin: NDArray[np.float64],
    s_emax: NDArray[np.float64],
    s_nmin: NDArray[np.float64],
    s_nmax: NDArray[np.float64],
    seg_indices: NDArray[np.int64],
) -> NDArray[np.bool_]:
    """Vektoriserat 2D-skärningstest: True om sträckan `A -> B` skär någon barriär i `seg_indices`.

    Hanterar skärning genom segmenthörn och punkter på barriären, samtidigt som en cell A
    som råkar ligga exakt på barriären inte spärras mot samtliga punkter.
    """
    blockerad = np.zeros(ae.shape, dtype=bool)
    if ae.size == 0 or seg_indices.size == 0:
        return blockerad

    eps = 1e-9
    ab_emin = np.minimum(ae, be)
    ab_emax = np.maximum(ae, be)
    ab_nmin = np.minimum(an, bn)
    ab_nmax = np.maximum(an, bn)
    ab_de = be - ae
    ab_dn = bn - an

    for s_idx_val in seg_indices:
        s_idx = int(s_idx_val)
        kvar = ~blockerad
        if not np.any(kvar):
            break

        # Bbox-prefilter mot barriärsegmentet
        overlapp = (
            kvar
            & (ab_emin <= s_emax[s_idx] + eps)
            & (ab_emax >= s_emin[s_idx] - eps)
            & (ab_nmin <= s_nmax[s_idx] + eps)
            & (ab_nmax >= s_nmin[s_idx] - eps)
        )
        if not np.any(overlapp):
            continue

        idx = np.nonzero(overlapp)[0]
        ae_k = ae[idx]
        an_k = an[idx]
        be_k = be[idx]
        bn_k = bn[idx]
        ab_de_k = ab_de[idx]
        ab_dn_k = ab_dn[idx]

        ce_s = ce[s_idx]
        cn_s = cn[s_idx]
        cd_de = de[s_idx] - ce_s
        cd_dn = dn[s_idx] - cn_s

        o1 = ab_de_k * (cn_s - an_k) - ab_dn_k * (ce_s - ae_k)
        o2 = ab_de_k * (dn[s_idx] - an_k) - ab_dn_k * (de[s_idx] - ae_k)
        o3 = cd_de * (an_k - cn_s) - cd_dn * (ae_k - ce_s)
        o4 = cd_de * (bn_k - cn_s) - cd_dn * (be_k - ce_s)

        cd_skilda = (o1 * o2) <= 0.0
        ab_skilda = ((o3 * o4) < 0.0) | ((np.abs(o3) > eps) & (np.abs(o4) <= eps))
        skars = cd_skilda & ab_skilda
        if np.any(skars):
            blockerad[idx[skars]] = True

    return blockerad


def interpolera_med_brytlinjer(
    punkter_e: NDArray[np.float64],
    punkter_n: NDArray[np.float64],
    punkter_z: NDArray[np.float64],
    ruta: Ruta,
    brytlinjer: Sequence[Brytlinje],
    *,
    max_avstand_m: float = 3.0,
    idw_potens: float = 2.0,
    klasser: NDArray[np.int32] | NDArray[np.uint8] | None = None,
) -> BarriarInterpoleringResultat:
    """Ensidig barriärstyrd IDW-interpolering där brytlinjer spärrar siktlinjen cell -> punkt.

    En laserpunkt `p` får bara bidra till cell `c` om sträckan `c -> p` inte skär något
    brytlinjesegment. Om ingen oskuren punkt finns inom `max_avstand_m` söks närmaste
    oskurna punkt i hela punktmängden; saknas oskurna punkter helt flaggas cellen i
    `barriar_blockerad_mask` och närmaste punkt används som reserv.
    """
    if not math.isfinite(max_avstand_m) or max_avstand_m <= 0.0:
        raise GrundkartaFel(f"Ogiltigt max_avstand_m: {max_avstand_m}")
    if not math.isfinite(idw_potens) or idw_potens <= 0.0:
        raise GrundkartaFel(f"Ogiltig idw_potens: {idw_potens}")

    e_alla, n_alla, z_alla = filtrera_laser_klasser(
        punkter_e, punkter_n, punkter_z, klasser=klasser
    )
    if e_alla.size == 0:
        raise GrundkartaFel("Inga giltiga markpunkter kvar efter klassfiltrering.")

    # Deterministisk sortering av punkter
    ordning = np.lexsort((z_alla, n_alla, e_alla))
    e_alla = e_alla[ordning]
    n_alla = n_alla[ordning]
    z_alla = z_alla[ordning]

    e_min_y, n_min_y, _, _ = ruta.yttre_bbox
    e_lok = e_alla - e_min_y
    n_lok = n_alla - n_min_y

    ce, cn, de, dn, s_emin, s_emax, s_nmin, s_nmax = _extrahera_barriarsegment(
        brytlinjer, e_ref=e_min_y, n_ref=n_min_y
    )
    alla_seg_idx = np.arange(ce.size, dtype=np.int64)

    rader, kolumner = ruta.form_rc
    e_vektor, n_vektor = ruta.cell_centra_en()
    e_vektor_lok = e_vektor - e_min_y
    n_vektor_lok = n_vektor - n_min_y

    mark_z = np.empty((rader, kolumner), dtype=np.float32)
    avstand_m = np.empty((rader, kolumner), dtype=np.float32)
    blockerad_mask = np.zeros((rader, kolumner), dtype=bool)

    max_r2 = max_avstand_m * max_avstand_m
    halv_potens = 0.5 * idw_potens
    block_storlek = 16

    for r0 in range(0, rader, block_storlek):
        r1 = min(rader, r0 + block_storlek)
        n_b_max = float(n_vektor_lok[r0])
        n_b_min = float(n_vektor_lok[r1 - 1])

        for k0 in range(0, kolumner, block_storlek):
            k1 = min(kolumner, k0 + block_storlek)
            e_b_min = float(e_vektor_lok[k0])
            e_b_max = float(e_vektor_lok[k1 - 1])

            # Kandiderande laserpunkter inom max_avstand_m från blockets bbox
            p_mask = (
                (e_lok >= e_b_min - max_avstand_m)
                & (e_lok <= e_b_max + max_avstand_m)
                & (n_lok >= n_b_min - max_avstand_m)
                & (n_lok <= n_b_max + max_avstand_m)
            )
            grid_e, grid_n = np.meshgrid(e_vektor_lok[k0:k1], n_vektor_lok[r0:r1])
            b_rader, b_kol = grid_e.shape
            n_celler = b_rader * b_kol
            ce_flat = grid_e.ravel()
            cn_flat = grid_n.ravel()

            z_block = np.empty(n_celler, dtype=np.float64)
            avst_block = np.empty(n_celler, dtype=np.float64)
            har_lokal = np.zeros(n_celler, dtype=bool)

            if np.any(p_mask):
                ek = e_lok[p_mask]
                nk = n_lok[p_mask]
                zk = z_alla[p_mask]

                dx = ce_flat[:, None] - ek[None, :]
                dy = cn_flat[:, None] - nk[None, :]
                d2 = dx * dx + dy * dy
                inom = d2 <= max_r2

                # Bbox-prefilter av barriärsegment mot blockets sökområde
                if ce.size > 0:
                    seg_mask = (
                        (s_emax >= e_b_min - max_avstand_m)
                        & (s_emin <= e_b_max + max_avstand_m)
                        & (s_nmax >= n_b_min - max_avstand_m)
                        & (s_nmin <= n_b_max + max_avstand_m)
                    )
                    lokala_seg = np.nonzero(seg_mask)[0].astype(np.int64)
                else:
                    lokala_seg = alla_seg_idx

                if lokala_seg.size > 0 and np.any(inom):
                    ci, pi = np.nonzero(inom)
                    skurna = _segment_skars_av_barriar(
                        ce_flat[ci],
                        cn_flat[ci],
                        ek[pi],
                        nk[pi],
                        ce,
                        cn,
                        de,
                        dn,
                        s_emin,
                        s_emax,
                        s_nmin,
                        s_nmax,
                        lokala_seg,
                    )
                    if np.any(skurna):
                        inom[ci[skurna], pi[skurna]] = False

                har_lokal = np.any(inom, axis=1)
                if np.any(har_lokal):
                    d2_lok = d2[har_lokal]
                    inom_lok = inom[har_lokal]
                    d2_safe = np.where(d2_lok > 1e-12, d2_lok, 1e-12)
                    vikter = np.where(inom_lok, np.power(d2_safe, -halv_potens), 0.0)
                    vikt_sum = np.sum(vikter, axis=1)
                    z_block[har_lokal] = np.sum(vikter * zk[None, :], axis=1) / vikt_sum

                    d2_giltig = np.where(inom_lok, d2_lok, np.inf)
                    avst_block[har_lokal] = np.sqrt(np.min(d2_giltig, axis=1))

            # Reserv för celler utan oskurna punkter inom max_avstand_m:
            # sök närmaste oskurna punkt i hela punktmängden; flagga om helt instängd.
            if not np.all(har_lokal):
                saknade_idx = np.nonzero(~har_lokal)[0]
                for c_idx_val in saknade_idx:
                    c_idx = int(c_idx_val)
                    c_e = ce_flat[c_idx]
                    c_n = cn_flat[c_idx]
                    d2_alla = (e_lok - c_e) ** 2 + (n_lok - c_n) ** 2

                    if ce.size > 0:
                        ae_vec = np.full(e_lok.size, c_e, dtype=np.float64)
                        an_vec = np.full(n_lok.size, c_n, dtype=np.float64)
                        skurna_alla = _segment_skars_av_barriar(
                            ae_vec,
                            an_vec,
                            e_lok,
                            n_lok,
                            ce,
                            cn,
                            de,
                            dn,
                            s_emin,
                            s_emax,
                            s_nmin,
                            s_nmax,
                            alla_seg_idx,
                        )
                        oskurna = ~skurna_alla
                    else:
                        oskurna = np.ones(e_lok.size, dtype=bool)

                    if np.any(oskurna):
                        d2_giltig = np.where(oskurna, d2_alla, np.inf)
                        min_i = int(np.argmin(d2_giltig))
                        z_block[c_idx] = float(z_alla[min_i])
                        avst_block[c_idx] = math.sqrt(float(d2_alla[min_i]))
                    else:
                        min_i = int(np.argmin(d2_alla))
                        z_block[c_idx] = float(z_alla[min_i])
                        avst_block[c_idx] = math.sqrt(float(d2_alla[min_i]))
                        lok_r = c_idx // b_kol
                        lok_k = c_idx % b_kol
                        blockerad_mask[r0 + lok_r, k0 + lok_k] = True

            mark_z[r0:r1, k0:k1] = z_block.reshape((b_rader, b_kol)).astype(np.float32)
            avstand_m[r0:r1, k0:k1] = avst_block.reshape((b_rader, b_kol)).astype(np.float32)

    antal_blockerade = int(np.count_nonzero(blockerad_mask))
    return BarriarInterpoleringResultat(
        mark_z=mark_z,
        avstand_m=avstand_m,
        barriar_blockerad_mask=blockerad_mask,
        antal_blockerade_celler=antal_blockerade,
    )
