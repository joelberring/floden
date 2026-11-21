"""Homogen atmosfär för referenskärnan, och bryggan till den allmänna `refraktion.Atmosfar`.

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.3.2 (ekv. 2–3), 5.4.2 (ekv. 10) och 5.9.

Metoden beskriver ljudhastighetsprofilen med koefficienterna A, B och C samt deras
fluktuationer s_A och s_B. Omräkningen från vind och temperaturgradient (`buller.typer.Vader`)
finns i `refraktion.atmosfar_ur_vader` (P-R1). `HomogenAtmosfar` och `homogen_atmosfar` är
kvar för specialfallet A = B = 0 och s_A = s_B = 0 (de accepterar inget annat); kärnan och
delmodell 1–2 räknar med `Atmosfar` och tar emot båda typerna genom `som_atmosfar`.
"""

from __future__ import annotations

from dataclasses import dataclass

from buller.nord2000.frekvenser import ljudhastighet_m_s
from buller.nord2000.refraktion import Atmosfar
from buller.typer import BullerFel, Vader

# Råhetslängd i `som_atmosfar`. Den används inte i någon formel när A = s_A = 0 (ekv. 2 och
# 18–20 innehåller z₀ bara tillsammans med A), men `Atmosfar` kräver ett värde större än 0.
_Z0_UTAN_BETYDELSE_M = 1.0


@dataclass(frozen=True)
class HomogenAtmosfar:
    """Homogen atmosfär: ξ = 0 och c = c₀ = c̄ = C = Coft(t₀) (AV 1106/07 avsnitt 5.5.2)."""

    temperatur_c: float
    relativ_fuktighet_procent: float
    lufttryck_kpa: float
    cv2: float
    ct2: float

    @property
    def ljudhastighet_m_s(self) -> float:
        """C = Coft(t₀), AV 1106/07 avsnitt 5.3.2, ekv. (3)."""
        return ljudhastighet_m_s(self.temperatur_c)


def homogen_atmosfar(vader: Vader) -> HomogenAtmosfar:
    """Kontrollerar att vädret är utan refraktion och returnerar den homogena atmosfären.

    Villkoret är vind = 0, temperaturgradient = 0 och deras standardavvikelser = 0. Med
    standardavvikelse > 0 krävs strålgång med A₊ och B₊ (AV 1106/07 ekv. 10 och 112) även när
    medelvärdet är noll, och det ingår inte ännu.
    """
    if vader.vind_m_s != 0.0 or vader.dt_dz_k_m != 0.0:
        raise BullerFel(
            "Nord2000Karna täcker ännu inte refraktion (vind eller temperaturgradient skild "
            "från 0): AV 1106/07 avsnitt 5.5 är inte implementerat."
        )
    if vader.std_vind_m_s != 0.0 or vader.std_dt_dz_k_m != 0.0:
        raise BullerFel(
            "Nord2000Karna täcker ännu inte fluktuerande refraktion (standardavvikelse för vind "
            "eller temperaturgradient skild från 0): koherensfaktorn F_Δτ i AV 1106/07 avsnitt "
            "5.9.2 kräver strålgång med A₊ och B₊."
        )
    if vader.cv2 < 0.0 or vader.ct2 < 0.0:
        raise BullerFel("Turbulensparametrarna Cv² och CT² får inte vara negativa.")
    return HomogenAtmosfar(
        temperatur_c=vader.temperatur_c,
        relativ_fuktighet_procent=vader.relativ_fuktighet_procent,
        lufttryck_kpa=vader.lufttryck_kpa,
        cv2=vader.cv2,
        ct2=vader.ct2,
    )


def som_atmosfar(atmosfar: HomogenAtmosfar | Atmosfar) -> Atmosfar:
    """`HomogenAtmosfar` uttryckt som `refraktion.Atmosfar` med A = B = s_A = s_B = 0.

    t₀ = t_air = `temperatur_c` (samma val som i `refraktion.atmosfar_ur_vader`). En `Atmosfar`
    returneras oförändrad.
    """
    if isinstance(atmosfar, Atmosfar):
        return atmosfar
    return Atmosfar(
        a_m_s=0.0,
        b_per_s=0.0,
        s_a_m_s=0.0,
        s_b_per_s=0.0,
        z0_m=_Z0_UTAN_BETYDELSE_M,
        temperatur_mark_c=atmosfar.temperatur_c,
        temperatur_luft_c=atmosfar.temperatur_c,
        relativ_fuktighet_procent=atmosfar.relativ_fuktighet_procent,
        lufttryck_kpa=atmosfar.lufttryck_kpa,
        cv2=atmosfar.cv2,
        ct2=atmosfar.ct2,
    )
