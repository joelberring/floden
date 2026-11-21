"""Byggnader (hinder), markslagskoder och öppningar (trummor, portar, passager)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from markmodell.brytlinjer import _supercover_segment
from markmodell.rutnat import Ruta

# Markslagskoder exakt enligt kontrakt K1/v1 i docs/DATAKONTRAKT.md
OKANT = 0
HARDGJORT = 1
TAK = 2
GRONT_TAK = 3
GRAS = 4
SKOG = 5
BERG = 6
GRUS = 7
VATTEN = 8
BYGGNAD = 9

GILTIGA_MARKSLAG: frozenset[int] = frozenset(
    {OKANT, HARDGJORT, TAK, GRONT_TAK, GRAS, SKOG, BERG, GRUS, VATTEN, BYGGNAD}
)

GILTIGA_OPPNINGSTYPER: frozenset[str] = frozenset({"trumma", "port", "passage"})


class HinderOppningFel(Exception):
    """Fel vid validering eller inbränning av byggnader och öppningar."""


@dataclass(frozen=True)
class Byggnad:
    """Byggnadsfotavtryck i EPSG:3011 med hinderhöjd eller absolut takhöjd."""

    polygon: tuple[tuple[float, float], ...]
    hinderhojd_m: float = 10.0
    takhoyd_rh2000: float | None = None
    hal: tuple[tuple[tuple[float, float], ...], ...] = ()
    """Innergårdar (hål) som ringar; celler i hål räknas inte som byggnad (jämn-udda-regeln)."""

    def __post_init__(self) -> None:
        if len(self.polygon) < 3:
            raise HinderOppningFel("En byggnadspolygon måste ha minst 3 hörn.")
        for ring in self.hal:
            if len(ring) < 3:
                raise HinderOppningFel("Ett hål i en byggnadspolygon måste ha minst 3 hörn.")
        if not math.isfinite(self.hinderhojd_m) or self.hinderhojd_m <= 0.0:
            raise HinderOppningFel(f"Ogiltig hinderhöjd: {self.hinderhojd_m}")
        if self.takhoyd_rh2000 is not None and not math.isfinite(self.takhoyd_rh2000):
            raise HinderOppningFel(f"Ogiltig takhöjd: {self.takhoyd_rh2000}")

        rensad: list[tuple[float, float]] = []
        for p in self.polygon:
            e = float(p[0])
            n = float(p[1])
            if not (math.isfinite(e) and math.isfinite(n)):
                raise HinderOppningFel(f"Ogiltig koordinat i byggnadspolygon: {p!r}")
            rensad.append((e, n))
        object.__setattr__(self, "polygon", tuple(rensad))


@dataclass(frozen=True)
class Oppning:
    """Öppning (trumma, port eller passage) som bryter igenom hinder/bankar."""

    typ: str
    bredd_m: float
    botten_rh2000: float
    kalla: str
    linje: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        norm_typ = self.typ.strip().lower()
        if norm_typ not in GILTIGA_OPPNINGSTYPER:
            tillatna = ", ".join(sorted(GILTIGA_OPPNINGSTYPER))
            raise HinderOppningFel(f"Ogiltig öppningstyp '{self.typ}'. Giltiga: {tillatna}.")
        object.__setattr__(self, "typ", norm_typ)

        if not math.isfinite(self.bredd_m) or self.bredd_m <= 0.0:
            raise HinderOppningFel(f"Ogiltig öppningsbredd: {self.bredd_m}")
        if not math.isfinite(self.botten_rh2000):
            raise HinderOppningFel(f"Ogiltig bottennivå: {self.botten_rh2000}")
        if not isinstance(self.kalla, str) or not self.kalla.strip():
            raise HinderOppningFel("Öppning måste ange en icke-tom källa.")
        if len(self.linje) < 2:
            raise HinderOppningFel("En öppningslinje måste ha minst 2 punkter.")

        rensad: list[tuple[float, float]] = []
        for p in self.linje:
            e = float(p[0])
            n = float(p[1])
            if not (math.isfinite(e) and math.isfinite(n)):
                raise HinderOppningFel(f"Ogiltig koordinat i öppning: {p!r}")
            rensad.append((e, n))
        object.__setattr__(self, "linje", tuple(rensad))


def _punkter_i_polygon(
    e_rutnat: NDArray[np.float64],
    n_rutnat: NDArray[np.float64],
    polygon: tuple[tuple[float, float], ...],
) -> NDArray[np.bool_]:
    """Vektoriserad ray-casting point-in-polygon med numpy."""
    innanfor = np.zeros(e_rutnat.shape, dtype=bool)
    antal = len(polygon)
    # Stödjer både sluten och öppen ring
    horn = polygon[:-1] if polygon[0] == polygon[-1] and antal > 3 else polygon
    m = len(horn)
    for i in range(m):
        e1, n1 = horn[i]
        e2, n2 = horn[(i + 1) % m]
        korsar_n = (n1 > n_rutnat) != (n2 > n_rutnat)
        if not np.any(korsar_n):
            continue
        lutning = (e2 - e1) / ((n2 - n1) if n2 != n1 else 1e-18)
        skarning_e = e1 + (n_rutnat - n1) * lutning
        innanfor ^= korsar_n & (e_rutnat < skarning_e)
    return innanfor


def byggnad_maske(
    byggnad: Byggnad,
    ruta: Ruta,
) -> tuple[NDArray[np.bool_], tuple[int, int, int, int]] | None:
    """Byggnadens celler som en delmask och dess fönster `(rad0, rad1, kol0, kol1)` i rutan.

    Cellcentrum avgör (strålkastning, jämn-udda-regeln så att innergårdar blir hål). Samma
    funktion används av markmodellens bygge och av kärnans redigering, så att en byggnad som tas
    bort lämnar exakt de celler den la till. Returnerar `None` om byggnaden inte träffar någon cell.
    """
    e_min_y, n_min_y, e_max_y, n_max_y = ruta.yttre_bbox
    rader, kolumner = ruta.form_rc
    pe = [p[0] for p in byggnad.polygon]
    pn = [p[1] for p in byggnad.polygon]
    b_emin, b_emax = min(pe), max(pe)
    b_nmin, b_nmax = min(pn), max(pn)

    if b_emax < e_min_y or b_emin > e_max_y or b_nmax < n_min_y or b_nmin > n_max_y:
        return None

    kol0 = max(0, int(math.floor((b_emin - e_min_y) / ruta.upplosning_m)) - 1)
    kol1 = min(kolumner, int(math.ceil((b_emax - e_min_y) / ruta.upplosning_m)) + 1)
    rad0 = max(0, int(math.floor((n_max_y - b_nmax) / ruta.upplosning_m)) - 1)
    rad1 = min(rader, int(math.ceil((n_max_y - b_nmin) / ruta.upplosning_m)) + 1)
    if kol1 <= kol0 or rad1 <= rad0:
        return None

    e_vektor, n_vektor = ruta.cell_centra_en()
    sub_e, sub_n = np.meshgrid(e_vektor[kol0:kol1], n_vektor[rad0:rad1])
    mask = _punkter_i_polygon(sub_e, sub_n, byggnad.polygon)
    for ring in byggnad.hal:
        mask &= ~_punkter_i_polygon(sub_e, sub_n, ring)
    if not np.any(mask):
        return None
    return mask, (rad0, rad1, kol0, kol1)


def brann_in_byggnader(
    mark_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
    ruta: Ruta,
    byggnader: Sequence[Byggnad],
) -> tuple[NDArray[np.float32], NDArray[np.uint8]]:
    """Markerar byggnadsceller med markslag `BYGGNAD` (9) och höjer `mark_z`."""
    if mark_z.shape != ruta.form_rc or markslag.shape != ruta.form_rc:
        raise HinderOppningFel(
            f"Form på mark_z {mark_z.shape} eller markslag {markslag.shape} "
            f"matchar inte {ruta.form_rc}."
        )

    ny_z = np.array(mark_z, dtype=np.float32, copy=True)
    ny_slag = np.array(markslag, dtype=np.uint8, copy=True)
    if not byggnader:
        return ny_z, ny_slag

    for byggnad in byggnader:
        traff = byggnad_maske(byggnad, ruta)
        if traff is None:
            continue
        mask, (rad0, rad1, kol0, kol1) = traff

        z_sub = ny_z[rad0:rad1, kol0:kol1]
        slag_sub = ny_slag[rad0:rad1, kol0:kol1]

        # Undvik att höja samma cell dubbelt om överlappande byggnader förekommer
        nya_bygg_mask = mask & (slag_sub != BYGGNAD)
        if byggnad.takhoyd_rh2000 is not None:
            takhoyd = np.float32(byggnad.takhoyd_rh2000)
            z_sub[mask] = np.maximum(z_sub[mask], takhoyd)
        else:
            z_sub[nya_bygg_mask] = z_sub[nya_bygg_mask] + np.float32(byggnad.hinderhojd_m)

        slag_sub[mask] = np.uint8(BYGGNAD)

    return ny_z, ny_slag


def _distans_till_segment(
    e_rutnat: NDArray[np.float64],
    n_rutnat: NDArray[np.float64],
    e0: float,
    n0: float,
    e1: float,
    n1: float,
) -> NDArray[np.float64]:
    """Beräknar minsta euklidiska avståndet från varje cellcentrum till segmentet."""
    de = e1 - e0
    dn = n1 - n0
    l2 = de * de + dn * dn
    if l2 == 0.0:
        return np.hypot(e_rutnat - e0, n_rutnat - n0)
    t = ((e_rutnat - e0) * de + (n_rutnat - n0) * dn) / l2
    t = np.clip(t, 0.0, 1.0)
    proj_e = e0 + t * de
    proj_n = n0 + t * dn
    return np.hypot(e_rutnat - proj_e, n_rutnat - proj_n)


def brann_in_oppningar(
    mark_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
    ruta: Ruta,
    oppningar: Sequence[Oppning],
) -> tuple[NDArray[np.float32], NDArray[np.uint8]]:
    """Bränner in trummor, portar och passager genom bankar och byggnader.

    Längs öppningens linje och inom halva `bredd_m` sänks höjden till högst
    `botten_rh2000` (så att en trumma genom en vägbank eller en port genom en
    byggnad bildar en obruten passage). Dessutom inkluderas 4-sammanhängande
    supercover-celler längs mittlinjen så att även smala öppningar garanterat
    inte bryts diagonalt. Celler som tidigare var `BYGGNAD` ändras till `HARDGJORT`.
    """
    if mark_z.shape != ruta.form_rc or markslag.shape != ruta.form_rc:
        raise HinderOppningFel(
            f"Form på mark_z {mark_z.shape} eller markslag {markslag.shape} "
            f"matchar inte {ruta.form_rc}."
        )

    ny_z = np.array(mark_z, dtype=np.float32, copy=True)
    ny_slag = np.array(markslag, dtype=np.uint8, copy=True)
    if not oppningar:
        return ny_z, ny_slag

    e_vektor, n_vektor = ruta.cell_centra_en()
    e_min_y, n_min_y, e_max_y, n_max_y = ruta.yttre_bbox
    rader, kolumner = ruta.form_rc

    for oppning in oppningar:
        halv_bredd = 0.5 * oppning.bredd_m
        le = [p[0] for p in oppning.linje]
        ln = [p[1] for p in oppning.linje]
        o_emin, o_emax = min(le) - halv_bredd, max(le) + halv_bredd
        o_nmin, o_nmax = min(ln) - halv_bredd, max(ln) + halv_bredd

        if o_emax < e_min_y or o_emin > e_max_y or o_nmax < n_min_y or o_nmin > n_max_y:
            continue

        kol0 = max(0, int(math.floor((o_emin - e_min_y) / ruta.upplosning_m)) - 1)
        kol1 = min(kolumner, int(math.ceil((o_emax - e_min_y) / ruta.upplosning_m)) + 1)
        rad0 = max(0, int(math.floor((n_max_y - o_nmax) / ruta.upplosning_m)) - 1)
        rad1 = min(rader, int(math.ceil((n_max_y - o_nmin) / ruta.upplosning_m)) + 1)
        if kol1 <= kol0 or rad1 <= rad0:
            continue

        sub_e, sub_n = np.meshgrid(e_vektor[kol0:kol1], n_vektor[rad0:rad1])
        oppning_mask = np.zeros(sub_e.shape, dtype=bool)

        for idx in range(len(oppning.linje) - 1):
            e0, n0 = oppning.linje[idx]
            e1, n1 = oppning.linje[idx + 1]
            dist = _distans_till_segment(sub_e, sub_n, e0, n0, e1, n1)
            oppning_mask |= dist <= halv_bredd
            for rad, kol in _supercover_segment(e0, n0, e1, n1, ruta):
                if rad0 <= rad < rad1 and kol0 <= kol < kol1:
                    oppning_mask[rad - rad0, kol - kol0] = True

        if not np.any(oppning_mask):
            continue

        z_sub = ny_z[rad0:rad1, kol0:kol1]
        slag_sub = ny_slag[rad0:rad1, kol0:kol1]

        botten = np.float32(oppning.botten_rh2000)
        z_sub[oppning_mask] = np.minimum(z_sub[oppning_mask], botten)

        var_byggnad = oppning_mask & (slag_sub == BYGGNAD)
        slag_sub[var_byggnad] = np.uint8(HARDGJORT)

    return ny_z, ny_slag
