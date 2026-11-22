"""Modern matematik för buller: submodulär skärmoptimering (CELF) och akustisk bifurkation.

1. **Submodulär bullerskärmsoptimering (`valj_bullerskarmar_k`):**
   Använder CELF (lazy greedy) för att välja topp-k skärmsegment eller åtgärder som
   maximerar minskat dB-överskridande över riktvärde (t.ex. 55/60 dBA) eller antal
   nygodkända bostäder utan dubbelräkning av överlappande skärmar.
2. **Akustisk skuggzons-bifurkationsdetektor (`detektera_akustiska_bifurkationer`):**
   Identifierar mottagare där ljudhastighetsgradienten dc/dz inom ett väderintervall
   kan passera den refraktiva skuggzonsgränsen `d_skugga = sqrt(2 R h_s) + sqrt(2 R h_r)`,
   där dämpningen i Nord2000 hoppar kraftigt och enkel monotonitet bryter.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

STANDARD_RIKTVARDE_DB = 55.0
STANDARD_C0_M_S = 340.0


class BullerOptimeringFel(ValueError):
    """Ogiltig indata till bulleroptimering eller skuggzonsanalys."""


@dataclass(frozen=True)
class BullerSkarmVal:
    """En vald bullerskärm/åtgärd ur submodulär CELF-optimering."""

    skarm_id: str
    marginal_vinst: float
    kumulativ_vinst: float
    vinst_ensam: float
    nya_godkanda: int


@dataclass(frozen=True)
class AkustiskBifurkation:
    """Skuggzonsstatus och monotonitetsgaranti för en mottagare över ett väderintervall."""

    mottagare_idx: int
    avstand_m: float
    kritisk_dc_dz_per_s: float
    skuggzon_bifurkation: bool
    monoton_garanterad: bool


def valj_bullerskarmar_k(
    bas_niva_db: NDArray[np.float64] | NDArray[np.float32],
    skarm_reduktion_db: Mapping[str, NDArray[np.float64] | NDArray[np.float32]],
    *,
    k: int = 5,
    riktvarde_db: float = STANDARD_RIKTVARDE_DB,
    vikter: NDArray[np.float64] | None = None,
    mal: str = "overskridande_db",
) -> list[BullerSkarmVal]:
    """Submodulär k-optimering med CELF för val av bullerskärmar/åtgärder.

    För varje mottagare i med basnivå L_i och redan uppnådd dämpning D_i = max_{s in S} ΔL_{i,s}:
    - `mal="overskridande_db"`: maximerar viktad minskning av överskridandet
      `sum_i w_i * min(max(0, L_i - riktvarde_db), D_i)` (strikt monoton submodulär).
    - `mal="antal_godkanda"`: maximerar viktat antal mottagare som går från
      `L_i > riktvarde_db` till `L_i - D_i <= riktvarde_db` (strikt monoton submodulär).
    """
    if k <= 0 or not skarm_reduktion_db:
        return []
    if mal not in ("overskridande_db", "antal_godkanda"):
        raise BullerOptimeringFel(
            f"mal måste vara 'overskridande_db' eller 'antal_godkanda', fick {mal!r}."
        )

    bas = np.asarray(bas_niva_db, dtype=np.float64)
    if bas.ndim != 1:
        raise BullerOptimeringFel(f"bas_niva_db måste vara 1D, fick form {bas.shape}.")
    m = int(bas.size)
    if m == 0:
        return []

    w = np.ones(m, dtype=np.float64) if vikter is None else np.asarray(vikter, dtype=np.float64)
    if w.shape != (m,) or np.any(w < 0.0) or not np.all(np.isfinite(w)):
        raise BullerOptimeringFel("vikter måste vara 1D med längd M och värden >= 0.")

    overskridande = np.maximum(0.0, bas - float(riktvarde_db))
    behov = overskridande > 0.0

    skarmar: dict[str, NDArray[np.float64]] = {}
    for sid in sorted(skarm_reduktion_db):
        arr = np.asarray(skarm_reduktion_db[sid], dtype=np.float64)
        if arr.shape != (m,):
            raise BullerOptimeringFel(f"Skärm '{sid}' har form {arr.shape}, förväntade {(m,)}.")
        skarmar[sid] = np.maximum(0.0, arr)

    def utvardera_marginal(
        damp_nu: NDArray[np.float64],
        kand_damp: NDArray[np.float64],
    ) -> tuple[float, int]:
        ny_damp = np.maximum(damp_nu, kand_damp)
        if mal == "overskridande_db":
            fore = np.minimum(overskridande, damp_nu)
            efter = np.minimum(overskridande, ny_damp)
            vinst = float(np.sum(w * (efter - fore)))
        else:
            godk_fore = behov & (damp_nu >= overskridande)
            godk_efter = behov & (ny_damp >= overskridande)
            vinst = float(np.sum(w[godk_efter & ~godk_fore]))
        nya_ok = int(
            np.count_nonzero(behov & (damp_nu < overskridande) & (ny_damp >= overskridande))
        )
        return vinst, nya_ok

    damp_nu = np.zeros(m, dtype=np.float64)
    vinst_ensam: dict[str, float] = {}
    heap: list[tuple[float, str, int]] = []
    for sid, arr in skarmar.items():
        v0, _ = utvardera_marginal(damp_nu, arr)
        vinst_ensam[sid] = v0
        if v0 > 0.0:
            heapq.heappush(heap, (-v0, sid, 0))

    valda_ids: set[str] = set()
    resultat: list[BullerSkarmVal] = []
    kumulativ = 0.0

    for steg in range(k):
        if not heap:
            break
        vald_id: str | None = None
        vald_marg = 0.0
        while heap:
            neg_v, sid, utv_steg = heapq.heappop(heap)
            if sid in valda_ids:
                continue
            if utv_steg == steg:
                vald_id = sid
                vald_marg = -neg_v
                break
            ny_v, _ = utvardera_marginal(damp_nu, skarmar[sid])
            if ny_v > 0.0:
                heapq.heappush(heap, (-ny_v, sid, steg))

        if vald_id is None or vald_marg <= 0.0:
            break

        _, nya_ok = utvardera_marginal(damp_nu, skarmar[vald_id])
        valda_ids.add(vald_id)
        damp_nu = np.maximum(damp_nu, skarmar[vald_id])
        kumulativ += vald_marg
        resultat.append(
            BullerSkarmVal(
                skarm_id=vald_id,
                marginal_vinst=vald_marg,
                kumulativ_vinst=kumulativ,
                vinst_ensam=vinst_ensam[vald_id],
                nya_godkanda=nya_ok,
            )
        )

    return resultat


def detektera_akustiska_bifurkationer(
    avstand_m: NDArray[np.float64] | NDArray[np.float32],
    h_kalla_m: float,
    h_mottagare_m: float,
    *,
    dc_dz_intervall: tuple[float, float] = (-0.05, 0.05),
    c0_m_s: float = STANDARD_C0_M_S,
) -> list[AkustiskBifurkation]:
    """Flaggar mottagare som korsar Nord2000:s refraktiva skuggzonsgräns inom dc/dz-intervallet.

    I motvind (`dc/dz < 0`) kröker ljudstrålar uppåt med krökningsradie `R = c0 / |dc/dz|`.
    Den kritiska gradienten där skuggzonen exakt når avståndet `d` är:
        g_krit = -c0 * (sqrt(2 * h_s) + sqrt(2 * h_r))² / d²

    Om `g_min <= g_krit <= g_max` kan mottagaren växla mellan direkt sikt och skuggzon
    inom väderintervallet (`skuggzon_bifurkation = True`, `monoton_garanterad = False`).
    """
    if h_kalla_m < 0.0 or h_mottagare_m < 0.0 or c0_m_s <= 0.0:
        raise BullerOptimeringFel("Höjder måste vara >= 0 och c0_m_s > 0.")
    g_lo, g_hi = float(dc_dz_intervall[0]), float(dc_dz_intervall[1])
    if not (math.isfinite(g_lo) and math.isfinite(g_hi) and g_lo <= g_hi):
        raise BullerOptimeringFel(f"Ogiltigt dc_dz_intervall: {dc_dz_intervall}.")

    d_arr = np.asarray(avstand_m, dtype=np.float64)
    if d_arr.ndim != 1 or np.any(d_arr <= 0.0):
        raise BullerOptimeringFel("avstand_m måste vara 1D med positiva avstånd.")

    term = math.sqrt(2.0 * float(h_kalla_m)) + math.sqrt(2.0 * float(h_mottagare_m))
    taljare = -float(c0_m_s) * (term * term)

    ut: list[AkustiskBifurkation] = []
    for idx, d in enumerate(d_arr.tolist()):
        g_krit = taljare / (d * d)
        bif = g_lo <= g_krit <= g_hi
        ut.append(
            AkustiskBifurkation(
                mottagare_idx=idx,
                avstand_m=float(d),
                kritisk_dc_dz_per_s=g_krit,
                skuggzon_bifurkation=bif,
                monoton_garanterad=not bif,
            )
        )
    return ut
