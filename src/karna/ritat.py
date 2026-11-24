"""Kontroll av ritade planändringar (K9) innan de körs: geometri, höjd och utbredning.

Kartverktyget i webben låter en planarkitekt rita hus direkt i kartan. Klienten ritar bara
geometri; **all kontroll sker här** (och i `karna.scenario`/`karna.geometri.tillampa`, som
körs för samma ändringar vid tillämpningen), så att ett felaktigt hus får ett tydligt
felmeddelande i stället för att tyst hoppas över.

Kontrollerna som kärnans tillämpning saknar:

- byggnadens polygon ska vara giltig: minst tre olika hörn, ingen självkorsning, yta över
  `MIN_YTA_M2`;
- höjden ska vara > 0 och högst `HOJD_MAX_M` (200 m);
- polygonen ska ligga helt inom projektets K1-utsträckning (kärnans tillämpning hoppar annars
  tyst över ett hus utanför rutorna).

`bta_uppskattning` är en **uppskattning** (yta x antal våningar vid en våningshöjd), märkt så i
gränssnittet; den är inget resultat från någon lösare.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from shapely.validation import explain_validity

HOJD_MAX_M = 200.0
"""Största höjd (m) som accepteras för ett ritat hus (över mark)."""
MIN_YTA_M2 = 1.0
VANINGSHOJD_M = 3.0
"""Våningshöjd för BTA-uppskattningen."""
RITAD_TYPER: frozenset[str] = frozenset(
    {"byggnad_ny", "byggnad_hojd", "byggnad_bort", "markhojd"}
)
"""`markhojd` (ny markyta med `niva_rh2000`) tillkom med skarvkontrollen (R3); ritas först i R3."""

Rektangel = tuple[float, float, float, float]
"""(x_min, y_min, x_max, y_max) i EPSG:3011."""


class RitatFel(ValueError):
    """Ett ritat scenario som inte går att köra. Meddelandet är skrivet för en planarkitekt."""


def normalisera_ring(punkter: Sequence[Sequence[float]]) -> list[list[float]]:
    """Sluten ring, moturs, hörnen avrundade till 1 cm. Första hörnet behålls.

    Avrundningen ger stabila nycklar (samma ritade hus ger samma K9) och orienteringen följer
    K1 v2 (yttre ring moturs).
    """
    pts = [[round(float(p[0]), 2), round(float(p[1]), 2)] for p in punkter]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    # Efterföljande dubbletter (samma punkt klickad två gånger) tas bort.
    ren: list[list[float]] = []
    for p in pts:
        if not ren or p != ren[-1]:
            ren.append(p)
    if len(ren) > 1 and ren[0] == ren[-1]:
        ren.pop()
    if len(ren) >= 3 and _signerad_yta(ren) < 0:
        ren = [ren[0], *reversed(ren[1:])]
    return [*ren, ren[0]] if ren else []


def _signerad_yta(ring: Sequence[Sequence[float]]) -> float:
    s = 0.0
    for i, (x1, y1) in enumerate(ring):
        x2, y2 = ring[(i + 1) % len(ring)]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def yta_m2(ring: Sequence[Sequence[float]]) -> float:
    """Polygonens yta (m²) enligt snörmetoden."""
    pts = list(ring)
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return abs(_signerad_yta(pts)) if len(pts) >= 3 else 0.0


def vaningar(hojd_m: float, vaningshojd_m: float = VANINGSHOJD_M) -> int:
    """Antal våningar vid vald våningshöjd (minst 1), som K11."""
    return max(1, math.floor(hojd_m / vaningshojd_m))


def bta_uppskattning(
    yta: float, hojd_m: float, vaningshojd_m: float = VANINGSHOJD_M
) -> dict[str, float]:
    """Uppskattad BTA: yta x våningar. En uppskattning, inget lösarresultat."""
    n = vaningar(hojd_m, vaningshojd_m)
    return {
        "yta_m2": float(yta),
        "vaningar": float(n),
        "vaningshojd_m": float(vaningshojd_m),
        "bta_m2": float(yta) * n,
    }


def kontrollera_hojd(hojd: Any, vad: str) -> float:
    """Höjd över mark: ett ändligt tal > 0 och högst `HOJD_MAX_M`."""
    if isinstance(hojd, bool) or not isinstance(hojd, int | float) or not math.isfinite(hojd):
        raise RitatFel(f"{vad}: höjden måste vara ett tal i meter.")
    if hojd <= 0:
        raise RitatFel(f"{vad}: höjden måste vara större än 0 m (du angav {hojd:g} m).")
    if hojd > HOJD_MAX_M:
        raise RitatFel(
            f"{vad}: höjden {hojd:g} m är för stor; största tillåtna höjd är {HOJD_MAX_M:g} m. "
            "Kontrollera att höjden är angiven i meter."
        )
    return float(hojd)


def kontrollera_ring(ring: Sequence[Sequence[float]], vad: str) -> Polygon:
    """Giltig yttre ring: tre hörn eller fler, ingen självkorsning, rimlig yta."""
    pts = [(float(p[0]), float(p[1])) for p in ring]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(set(pts)) < 3:
        raise RitatFel(f"{vad}: huset behöver minst tre olika hörn.")
    if any(not (math.isfinite(x) and math.isfinite(y)) for x, y in pts):
        raise RitatFel(f"{vad}: ett hörn har ogiltiga koordinater.")
    poly = Polygon(pts)
    if not poly.is_valid:
        orsak = explain_validity(poly)
        if "Self-intersection" in orsak or "Ring Self-intersection" in orsak:
            raise RitatFel(
                f"{vad}: husets kanter korsar varandra. Flytta hörnen så att linjerna inte "
                "korsar, eller rita om huset."
            )
        raise RitatFel(f"{vad}: huset har ingen giltig form ({orsak}).")
    if poly.area < MIN_YTA_M2:
        raise RitatFel(
            f"{vad}: huset är för litet ({poly.area:.2f} m²). Minsta yta är {MIN_YTA_M2:g} m²."
        )
    return poly


def kontrollera_andringar(
    andringar: Sequence[Mapping[str, Any]],
    projektets_rutor: Sequence[Rektangel],
) -> dict[str, Any]:
    """Kontrollerar ritade ändringar (K9-ändringar som ordböcker) mot projektets utsträckning.

    Kastar `RitatFel` vid första felet. Returnerar `{"hus": [...]}` med yta och BTA-uppskattning
    per nytt hus, och eventuella varningar. Kärnans egen tillämpning (`karna.scenario`,
    `karna.geometri.tillampa`) kontrollerar resten när scenariot byggs och planeras.
    """
    if not projektets_rutor:
        raise RitatFel("Projektet saknar K1-rutor; det går inte att rita här.")
    yta_union = unary_union([box(*r) for r in projektets_rutor])
    hus: list[dict[str, Any]] = []
    varningar: list[str] = []
    for a in andringar:
        typ = a.get("typ")
        aid = str(a.get("andring_id", ""))
        if typ not in RITAD_TYPER:
            raise RitatFel(
                f"Ändringen '{aid}' är av typen '{typ}', som kartverktyget inte ritar. "
                f"Tillåtna: {', '.join(sorted(RITAD_TYPER))}."
            )
        e = a.get("egenskaper") or {}
        if typ == "byggnad_ny":
            namn = f"Nytt hus '{aid}'"
            g = a.get("geometri")
            if not isinstance(g, Mapping) or g.get("type") != "Polygon" or not g.get("coordinates"):
                raise RitatFel(f"{namn}: saknar polygon.")
            poly = kontrollera_ring(g["coordinates"][0], namn)
            hojd = kontrollera_hojd(e.get("hojd_m"), namn)
            if not yta_union.covers(poly):
                if yta_union.intersects(poly):
                    raise RitatFel(
                        f"{namn} ligger delvis utanför projektets område. Rita huset helt inom "
                        "det område som har data (de mörka ytorna i lagret Planförslag visar det)."
                    )
                raise RitatFel(
                    f"{namn} ligger utanför projektets område. Rita huset inom det område "
                    "som har data."
                )
            hus.append(
                {
                    "andring_id": aid,
                    "hojd_m": hojd,
                    **bta_uppskattning(poly.area, hojd),
                }
            )
        elif typ == "byggnad_hojd":
            kontrollera_hojd(e.get("hojd_m"), f"Höjdändring '{aid}'")
        elif typ == "markhojd":
            namn = f"Ny markyta '{aid}'"
            g = a.get("geometri")
            if not isinstance(g, Mapping) or g.get("type") != "Polygon" or not g.get("coordinates"):
                raise RitatFel(f"{namn}: saknar polygon.")
            poly = kontrollera_ring(g["coordinates"][0], namn)
            niva = e.get("niva_rh2000")
            if (
                isinstance(niva, bool)
                or not isinstance(niva, int | float)
                or not math.isfinite(niva)
            ):
                raise RitatFel(f"{namn}: saknar nivå (niva_rh2000, m RH 2000).")
            if not yta_union.intersects(poly):
                raise RitatFel(f"{namn} ligger utanför projektets område.")
        elif typ == "byggnad_bort" and not e.get("byggnads_id"):
            raise RitatFel(f"Borttagning '{aid}': saknar byggnads_id.")
    if not andringar:
        varningar.append("Scenariot har inga ändringar än.")
    return {"hus": hus, "varningar": varningar}
