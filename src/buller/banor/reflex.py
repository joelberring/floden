"""Första ordningens speglande reflexer mot byggnadsfasader (spegelkällmetoden).

En reflex mellan källa S och mottagare R i väggen A–B godtas när

1. S och R ligger på väggens utsida (i den utåtriktade normalens halvplan),
2. reflexpunkten P, där linjen från S till R:s spegelbild korsar vägglinjen, ligger på
   väggsegmentet (ändpunkterna inräknade),
3. banans längd S–P–R är högst maxradien,
4. väggens krön (byggnadens takhöjd) ligger över strålen i P, där strålens höjd
   interpoleras rätlinjigt mellan källans och mottagarens höjd (RH 2000) längs banan,
5. fasaden är fri i P: punkten `fasadmarginal_m` utanför väggen ligger inte i någon byggnad
   (utesluter mellanväggar i sammanbyggda hus).

Skymda delsträckor sorteras inte bort här: byggnader i vägen kommer in som skärmar i
profilen. Högre reflexordning finns inte; `Reflexer` bär knäckpunkter per bana så att fler
reflexpunkter kan läggas till utan att banbygget ändras.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from buller.scen.bullerscen import Bullerscen, Heltal
from buller.typer import BullerFel, Flyttal

STANDARD_FASADMARGINAL_M = 0.05
# Minsta avstånd från vägglinjen för att en punkt ska räknas som utanför väggen.
SIDOTOLERANS_M = 1e-6


@dataclass(frozen=True, eq=False)
class Reflexer:
    """Godtagna reflexer som platta arrayer, sorterade på (mottagare, källa, vägg)."""

    mottagare_index: Heltal
    kalla_index: Heltal
    vagg_index: Heltal  # form (antal, ordning): väggar i den ordning strålen träffar dem
    punkt_e_m: Flyttal  # form (antal, ordning): reflexpunkter
    punkt_n_m: Flyttal

    @property
    def antal(self) -> int:
        return int(self.mottagare_index.shape[0])


def forsta_ordningens_reflexer(
    scen: Bullerscen,
    mottagare_e_m: Flyttal,
    mottagare_n_m: Flyttal,
    mottagare_rh2000_m: Flyttal,
    kalla_e_m: Flyttal,
    kalla_n_m: Flyttal,
    kalla_rh2000_m: Flyttal,
    maxradie_m: float,
    *,
    utesluten_byggnad: Heltal | None = None,
    fasadmarginal_m: float = STANDARD_FASADMARGINAL_M,
) -> Reflexer:
    """Hittar alla första ordningens reflexer med banlängd ≤ `maxradie_m`.

    `utesluten_byggnad` anger per mottagare index för en byggnad vars väggar inte får
    reflektera (t.ex. mottagarens egen fasad), eller -1.
    """
    if not (math.isfinite(maxradie_m) and maxradie_m > 0.0):
        raise BullerFel(f"Maxradien ska vara > 0 m, fick {maxradie_m}.")
    if not (math.isfinite(fasadmarginal_m) and fasadmarginal_m > 0.0):
        raise BullerFel(f"fasadmarginal_m ska vara > 0 m, fick {fasadmarginal_m}.")
    vaggar = scen.vaggar
    antal_m = int(mottagare_e_m.shape[0])
    m_delar: list[Heltal] = []
    k_delar: list[Heltal] = []
    v_delar: list[Heltal] = []
    pe_delar: list[Flyttal] = []
    pn_delar: list[Flyttal] = []
    if vaggar.antal and kalla_e_m.shape[0]:
        de = (vaggar.b_e_m - vaggar.a_e_m) / vaggar.langd_m
        dn = (vaggar.b_n_m - vaggar.a_n_m) / vaggar.langd_m
        for m in range(antal_m):
            re, rn = float(mottagare_e_m[m]), float(mottagare_n_m[m])
            # Källor som alls kan ge en bana inom maxradien.
            ki = np.flatnonzero(np.hypot(kalla_e_m - re, kalla_n_m - rn) <= maxradie_m)
            if ki.size == 0:
                continue
            # Väggar med mottagaren på utsidan och inom räckhåll.
            avst_r = (re - vaggar.a_e_m) * vaggar.normal_e + (rn - vaggar.a_n_m) * vaggar.normal_n
            langs_r = np.clip(
                (re - vaggar.a_e_m) * de + (rn - vaggar.a_n_m) * dn, 0.0, vaggar.langd_m
            )
            till_vagg = np.hypot(vaggar.a_e_m + langs_r * de - re, vaggar.a_n_m + langs_r * dn - rn)
            mojlig = (avst_r > SIDOTOLERANS_M) & (till_vagg <= maxradie_m)
            if utesluten_byggnad is not None and utesluten_byggnad[m] >= 0:
                mojlig &= vaggar.byggnad_index != utesluten_byggnad[m]
            vi = np.flatnonzero(mojlig)
            if vi.size == 0:
                continue
            # Matris (vägg, källa).
            ae, an = vaggar.a_e_m[vi, None], vaggar.a_n_m[vi, None]
            ne, nn = vaggar.normal_e[vi, None], vaggar.normal_n[vi, None]
            d_r = avst_r[vi, None]
            spegel_e = re - 2.0 * d_r * ne
            spegel_n = rn - 2.0 * d_r * nn
            ke, kn = kalla_e_m[None, ki], kalla_n_m[None, ki]
            d_s = (ke - ae) * ne + (kn - an) * nn
            utsida = d_s > SIDOTOLERANS_M
            andel = np.where(utsida, d_s, 1.0) / (np.where(utsida, d_s, 1.0) + d_r)
            pe = ke + (spegel_e - ke) * andel
            pn = kn + (spegel_n - kn) * andel
            langs = (pe - ae) * de[vi, None] + (pn - an) * dn[vi, None]
            banlangd = np.hypot(spegel_e - ke, spegel_n - kn)
            stralhojd = kalla_rh2000_m[None, ki] + andel * (
                float(mottagare_rh2000_m[m]) - kalla_rh2000_m[None, ki]
            )
            godtagen = (
                utsida
                & (langs >= 0.0)
                & (langs <= vaggar.langd_m[vi, None])
                & (banlangd <= maxradie_m)
                & (vaggar.tak_rh2000_m[vi, None] > stralhojd)
            )
            v_lok, k_lok = np.nonzero(godtagen)
            if v_lok.size == 0:
                continue
            p_e, p_n = pe[v_lok, k_lok], pn[v_lok, k_lok]
            vagg = vi[v_lok]
            fri = ~scen.i_byggnad(
                p_e + fasadmarginal_m * vaggar.normal_e[vagg],
                p_n + fasadmarginal_m * vaggar.normal_n[vagg],
            )
            kalla = ki[k_lok][fri]
            vagg, p_e, p_n = vagg[fri], p_e[fri], p_n[fri]
            ordning = np.lexsort((vagg, kalla))
            m_delar.append(np.full(ordning.shape[0], m, dtype=np.int64))
            k_delar.append(kalla[ordning])
            v_delar.append(vagg[ordning])
            pe_delar.append(p_e[ordning])
            pn_delar.append(p_n[ordning])
    if not m_delar:
        tom_i = np.zeros(0, dtype=np.int64)
        return Reflexer(
            tom_i, tom_i, np.zeros((0, 1), dtype=np.int64), np.zeros((0, 1)), np.zeros((0, 1))
        )
    return Reflexer(
        mottagare_index=np.concatenate(m_delar),
        kalla_index=np.concatenate(k_delar),
        vagg_index=np.concatenate(v_delar)[:, None],
        punkt_e_m=np.concatenate(pe_delar)[:, None],
        punkt_n_m=np.concatenate(pn_delar)[:, None],
    )
