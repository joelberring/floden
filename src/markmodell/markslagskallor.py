"""Markslag ur flera källor: prioritetsordning, källfil och osäkerhet (K1 v2-tillägg).

Prioritetsordning (förslag, se "Öppna beslut" i `docs/BESLUT.md`), högst först:

1. baskartans ytor (DWG-skraffyrer via lagerprofilen; därefter byggnader och öppningar som
   markmodellens bygge bränner in),
2. belagd yta ur laserintensitet (`markmodell.laser_belagt`, ger bara markslag 1),
3. ortofototolkning (`markmodell.ortofoto`; `OKANT` räknas som "ingen uppgift"),
4. standardklass (förval 1 hårdgjort, som markmodellen alltid haft).

Där källor motsäger varandra vinner den högre prioriteten, men cellen får en flagga. Per cell skrivs
`markslag_kalla.tif` (uint8):

- bit 0–1: källan som avgjorde (`KALLA_STANDARD` 0, `KALLA_ORTOFOTO` 1, `KALLA_BELAGD_LASER` 2,
  `KALLA_BASKARTA` 3),
- bit 2 (värde 4, `FLAGGA_MOTSAGELSE`): en lägre prioriterad källa (eller en annan baskartayta) sa
  något annat,
- bit 3 (värde 8, `FLAGGA_SVAG`): avgörande källa är ortofoto med konfidens under `SVAG_KONFIDENS`,
- bit 4 (värde 16, `FLAGGA_OSAKER_KALLA`): avgörande baskartayta ligger på ett lager med status
  Odefinierad (Stockholms grammatik): ytan är med men osäkrare.

Inom baskartan gäller `BASKARTA_PRIORITET` när ytor överlappar (och cellen flaggas).
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from gemensamt.hash import sha256_bytes
from markmodell.dwg import MarkslagsYta
from markmodell.hinder_oppningar import (
    BERG,
    BYGGNAD,
    GRAS,
    GRONT_TAK,
    GRUS,
    HARDGJORT,
    OKANT,
    SKOG,
    TAK,
    VATTEN,
    _punkter_i_polygon,
)
from markmodell.laser_belagt import (
    STANDARD_BELAGT,
    BelagtInstallningar,
    BelagtResultat,
    belagd_yta,
    mask_pa_rutnat,
    punktstatistik,
)
from markmodell.rutnat import Ruta

KALLA_STANDARD = 0
KALLA_ORTOFOTO = 1
KALLA_BELAGD_LASER = 2
KALLA_BASKARTA = 3
KALLA_MASK = 3
FLAGGA_MOTSAGELSE = 4
FLAGGA_SVAG = 8
FLAGGA_OSAKER_KALLA = 16

SVAG_KONFIDENS = 0.35
"""Ortofotoklassning under denna konfidens flaggas som svag (skuggor ligger under 0,35)."""

PRIORITET_TEXT = "baskarta>belagd_laser>ortofoto>standard"

BASKARTA_PRIORITET: tuple[int, ...] = (
    BYGGNAD,
    VATTEN,
    HARDGJORT,
    TAK,
    GRONT_TAK,
    BERG,
    GRUS,
    GRAS,
    SKOG,
)
"""Ordning mellan baskartans egna ytor när de överlappar, högst först."""


class MarkslagKallorFel(ValueError):
    """Ogiltig indata till markslagskombinationen."""


@dataclass(frozen=True)
class MarkslagKallor:
    markslag: NDArray[np.uint8]
    kalla: NDArray[np.uint8]
    """Källa (bit 0–1) och flaggor (bit 2–3) per cell, se modulens dokumentation."""

    def antal_per_kalla(self) -> dict[str, int]:
        k = self.kalla & KALLA_MASK
        return {
            "standard": int(np.sum(k == KALLA_STANDARD)),
            "ortofoto": int(np.sum(k == KALLA_ORTOFOTO)),
            "belagd_laser": int(np.sum(k == KALLA_BELAGD_LASER)),
            "baskarta": int(np.sum(k == KALLA_BASKARTA)),
        }

    def antal_motsagelser(self) -> int:
        return int(np.sum((self.kalla & FLAGGA_MOTSAGELSE) != 0))

    def antal_osakra(self) -> int:
        return int(np.sum((self.kalla & FLAGGA_OSAKER_KALLA) != 0))

    def antal_svaga(self) -> int:
        return int(np.sum((self.kalla & FLAGGA_SVAG) != 0))


def rasterisera_ytor(
    ytor: Sequence[MarkslagsYta], ruta: Ruta
) -> tuple[NDArray[np.uint8], NDArray[np.bool_], NDArray[np.bool_]]:
    """Baskartans ytor på rutnätet: (markslagskod, konflikt, osäker); kod 0 = ingen yta.

    Cellcentrum avgör; ringarna i en yta tolkas med jämn–udda-regeln (hål blir hål).
    """
    rader, kolumner = ruta.form_rc
    e_vektor, n_vektor = ruta.cell_centra_en()
    per_kod: dict[int, NDArray[np.bool_]] = {}
    sakra: dict[int, NDArray[np.bool_]] = {}  # celler täckta av minst en yta utan osäkerhetsflagga
    for yta in ytor:
        if not yta.ringar:
            continue
        punkter = [p for ring in yta.ringar for p in ring]
        e_lo, e_hi = min(p[0] for p in punkter), max(p[0] for p in punkter)
        n_lo, n_hi = min(p[1] for p in punkter), max(p[1] for p in punkter)
        kol = np.nonzero((e_vektor >= e_lo) & (e_vektor <= e_hi))[0]
        rad = np.nonzero((n_vektor >= n_lo) & (n_vektor <= n_hi))[0]
        if kol.size == 0 or rad.size == 0:
            continue
        sub_e, sub_n = np.meshgrid(e_vektor[kol], n_vektor[rad])
        inne = np.zeros(sub_e.shape, dtype=bool)
        for ring in yta.ringar:
            if len(ring) >= 3:
                inne ^= _punkter_i_polygon(
                    sub_e, sub_n, tuple((float(a), float(b)) for a, b in ring)
                )
        mask = per_kod.setdefault(yta.markslag, np.zeros((rader, kolumner), dtype=bool))
        mask[np.ix_(rad, kol)] |= inne
        if not yta.osaker:
            sak = sakra.setdefault(yta.markslag, np.zeros((rader, kolumner), dtype=bool))
            sak[np.ix_(rad, kol)] |= inne
    kod = np.zeros((rader, kolumner), dtype=np.uint8)
    antal = np.zeros((rader, kolumner), dtype=np.int16)
    for k in reversed(BASKARTA_PRIORITET):  # lägst först, högst skriver sist
        if k in per_kod:
            kod[per_kod[k]] = np.uint8(k)
            antal += per_kod[k]
    okanda = sorted(set(per_kod) - set(BASKARTA_PRIORITET))
    if okanda:
        raise MarkslagKallorFel(f"Markslagskoder utan plats i BASKARTA_PRIORITET: {okanda}")
    osaker = np.zeros((rader, kolumner), dtype=bool)
    for k, mask in per_kod.items():
        vinner = kod == k
        osaker |= vinner & mask & ~sakra.get(k, np.zeros_like(mask))
    return kod, antal > 1, osaker


def kombinera_markslag(
    form_rc: tuple[int, int],
    *,
    baskarta_kod: NDArray[np.uint8] | None = None,
    baskarta_konflikt: NDArray[np.bool_] | None = None,
    baskarta_osaker: NDArray[np.bool_] | None = None,
    belagd_mask: NDArray[np.bool_] | None = None,
    ortofoto: NDArray[np.uint8] | None = None,
    ortofoto_konfidens: NDArray[np.floating[Any]] | None = None,
    standard: int = HARDGJORT,
    svag_konfidens: float = SVAG_KONFIDENS,
) -> MarkslagKallor:
    """Slår ihop källorna enligt prioritetsordningen och ger källa och flaggor per cell."""

    def kontrollera(namn: str, a: NDArray[Any] | None) -> None:
        if a is not None and a.shape != form_rc:
            raise MarkslagKallorFel(f"{namn} har form {a.shape}, förväntade {form_rc}.")

    for namn, a in (
        ("baskarta_kod", baskarta_kod),
        ("baskarta_konflikt", baskarta_konflikt),
        ("baskarta_osaker", baskarta_osaker),
        ("belagd_mask", belagd_mask),
        ("ortofoto", ortofoto),
        ("ortofoto_konfidens", ortofoto_konfidens),
    ):
        kontrollera(namn, a)
    if not math.isfinite(svag_konfidens):
        raise MarkslagKallorFel("svag_konfidens måste vara ändlig.")

    slag = np.full(form_rc, standard, dtype=np.uint8)
    kalla = np.zeros(form_rc, dtype=np.uint8)
    bas = np.zeros(form_rc, dtype=bool) if baskarta_kod is None else baskarta_kod != 0
    bel = np.zeros(form_rc, dtype=bool) if belagd_mask is None else np.asarray(belagd_mask, bool)
    ort = np.zeros(form_rc, dtype=bool) if ortofoto is None else np.asarray(ortofoto) != OKANT
    if ortofoto is not None:
        slag[ort] = ortofoto[ort]
        kalla[ort] = KALLA_ORTOFOTO
    slag[bel] = np.uint8(HARDGJORT)
    kalla[bel] = KALLA_BELAGD_LASER
    if baskarta_kod is not None:
        slag[bas] = baskarta_kod[bas]
        kalla[bas] = KALLA_BASKARTA

    ort_kod = np.zeros(form_rc, dtype=np.uint8) if ortofoto is None else ortofoto
    mot = bas & ((bel & (slag != HARDGJORT)) | (ort & (ort_kod != slag)))
    if baskarta_konflikt is not None:
        mot |= bas & baskarta_konflikt
    mot |= (~bas) & bel & ort & (ort_kod != HARDGJORT)
    svag = np.zeros(form_rc, dtype=bool)
    if ortofoto_konfidens is not None:
        svag = (kalla == KALLA_ORTOFOTO) & (np.asarray(ortofoto_konfidens) < svag_konfidens)
    kalla = kalla | (mot * np.uint8(FLAGGA_MOTSAGELSE)) | (svag * np.uint8(FLAGGA_SVAG))
    if baskarta_osaker is not None:
        kalla = kalla | ((bas & baskarta_osaker) * np.uint8(FLAGGA_OSAKER_KALLA))
    return MarkslagKallor(markslag=slag, kalla=kalla.astype(np.uint8))


def efter_inbranning(
    fore: MarkslagKallor, slutligt: NDArray[np.uint8], inbrand: NDArray[np.bool_]
) -> MarkslagKallor:
    """Byggnader och öppningar som bränts in är baskarta: de tar över källan i `inbrand`-cellerna.

    Skiljer sig värdet från vad en lägre källa hade (ortofoto eller belagd yta) flaggas cellen;
    standardklassen räknas inte som en uppgift. Redan satta flaggor från baskartan behålls.
    """
    kalla = fore.kalla.copy()
    gammal = fore.kalla & KALLA_MASK
    skiljer = (
        inbrand
        & (gammal != KALLA_STANDARD)
        & (gammal != KALLA_BASKARTA)
        & (fore.markslag != slutligt)
    )
    behall = np.where(
        gammal == KALLA_BASKARTA, fore.kalla & (FLAGGA_MOTSAGELSE | FLAGGA_OSAKER_KALLA), 0
    ).astype(np.uint8)
    ny = np.uint8(KALLA_BASKARTA) | behall | (skiljer * np.uint8(FLAGGA_MOTSAGELSE))
    kalla[inbrand] = ny[inbrand]
    return MarkslagKallor(markslag=np.array(slutligt, dtype=np.uint8, copy=True), kalla=kalla)


def belagd_mask_for_ruta(
    moln: Any,
    ruta: Ruta,
    *,
    installningar: BelagtInstallningar = STANDARD_BELAGT,
    cell_m: float = 2.0,
) -> tuple[NDArray[np.bool_], BelagtResultat]:
    """Belagd yta ur ett punktmoln med intensitet, samplad på rutans rutnät (rad 0 i norr)."""
    stat = punktstatistik(moln, ruta.yttre_bbox, cell_m=cell_m)
    res = belagd_yta(stat, installningar=installningar)
    rader, kolumner = ruta.form_rc
    mask = mask_pa_rutnat(
        res,
        stat,
        min_e=ruta.yttre_bbox[0],
        min_n=ruta.yttre_bbox[1],
        cell_m=ruta.upplosning_m,
        bredd=kolumner,
        hojd=rader,
    )
    return mask.astype(bool), res


def ytor_sha256(ytor: Sequence[MarkslagsYta]) -> str:
    """Deterministisk kontrollsumma för baskartans markslagsytor (till recept.json)."""
    data = [
        {
            "markslag": y.markslag,
            "lager": y.lager,
            "ringar": [[list(p) for p in r] for r in y.ringar],
        }
        for y in ytor
    ]
    return sha256_bytes(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def installningar_post(
    *, standard: int, belagd: Mapping[str, float] | None, svag_konfidens: float
) -> dict[str, Any]:
    """Inställningarna som skrivs i receptet när markslagskällorna används."""
    post: dict[str, Any] = {
        "markslag_prioritet": PRIORITET_TEXT,
        "markslag_standard": int(standard),
        "markslag_svag_konfidens": float(svag_konfidens),
        "markslag_baskarta_prioritet": list(BASKARTA_PRIORITET),
    }
    if belagd is not None:
        post["markslag_belagd_installningar"] = dict(sorted(belagd.items()))
    return post
