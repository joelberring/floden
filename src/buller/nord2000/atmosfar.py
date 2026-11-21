"""Atmosfärens tillstånd för referenskärnan: i detta skede endast homogen atmosfär.

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.3.2 (ekv. 2–3), 5.4.2 (ekv. 10) och 5.9.

Metoden beskriver ljudhastighetsprofilen med koefficienterna A, B och C samt deras
fluktuationer s_A och s_B. Omräkningen från vind och temperaturgradient (`buller.typer.Vader`)
till A och B finns inte i AV 1106/07 och hör till refraktionspaketet. Här accepteras därför
bara väder med A = B = 0 och s_A = s_B = 0; allt annat ger `BullerFel`.
"""

from __future__ import annotations

from dataclasses import dataclass

from buller.nord2000.frekvenser import ljudhastighet_m_s
from buller.typer import BullerFel, Vader


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
