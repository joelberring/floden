"""Delmodell 9: luftabsorption enligt Nord2000.

Källa: DELTA AV 1106/07 (rev. 2014), avsnitt 5.18, ekv. (286)–(287). Metoden anger själv att
ekv. (286) bygger på ISO 9613-1; här är formeln avläst ur AV 1106/07 och inte ur standarden.
`buller.akustik.luftabsorption` används inte, eftersom den är avläst ur andra källor och
dessutom saknar bandkorrektionen i ekv. (287).
"""

from __future__ import annotations

import math

import numpy as np

from buller.typer import BullerFel, Flyttal

_T0_K = 293.15
_T01_K = 273.16
_REFERENSTRYCK_KPA = 101.325  # 1 atm; ekv. (286) använder p_rel = p_a / p_r


def rentonsdampning_db_per_m(
    frekvens_hz: float,
    lufttemperatur_c: float,
    relativ_fuktighet_procent: float,
    lufttryck_kpa: float = _REFERENSTRYCK_KPA,
) -> float:
    """Dämpningskoefficient α(f) i dB/m för en ren ton, AV 1106/07 avsnitt 5.18, ekv. (286)."""
    if frekvens_hz <= 0.0:
        raise BullerFel("Frekvensen ska vara större än 0 Hz.")
    if not 0.0 <= relativ_fuktighet_procent <= 100.0:
        raise BullerFel("Relativ fuktighet ska ligga mellan 0 och 100 %.")
    if lufttryck_kpa <= 0.0 or lufttemperatur_c <= -273.15:
        raise BullerFel("Lufttryck och absolut temperatur ska vara större än 0.")
    t_k = lufttemperatur_c + 273.15
    p_rel = lufttryck_kpa / _REFERENSTRYCK_KPA
    t_rel = t_k / _T0_K
    h = relativ_fuktighet_procent / p_rel * 10.0 ** (-6.8346 * (_T01_K / t_k) ** 1.261 + 4.6151)
    f_ro = p_rel * (24.0 + 40400.0 * h * (h + 0.02) / (h + 0.391))
    f_rn = (
        p_rel * t_rel**-0.5 * (9.0 + 280.0 * h * math.exp(-4.170 * (t_rel ** (-1.0 / 3.0) - 1.0)))
    )
    f2 = frekvens_hz * frekvens_hz
    klassisk = 1.84e-11 / p_rel * t_rel**0.5
    syre = 0.01275 * math.exp(-2239.1 / t_k) / (f_ro + f2 / f_ro)
    kvave = 0.1068 * math.exp(-3352.0 / t_k) / (f_rn + f2 / f_rn)
    return float(8.686 * f2 * (klassisk + t_rel**-2.5 * (syre + kvave)))


def luftabsorption_db(
    frekvenser_hz: Flyttal,
    vagstracka_m: float,
    lufttemperatur_c: float,
    relativ_fuktighet_procent: float,
    lufttryck_kpa: float = _REFERENSTRYCK_KPA,
) -> Flyttal:
    """Utbredningseffekten ΔL_9 (≤ 0 dB) per tersband, AV 1106/07 avsnitt 5.18, ekv. (287).

    A_0 = α(f_0)·R är rentonsdämpningen vid bandets mittfrekvens; ekv. (287) räknar om den till
    tersbandsdämpning. Uttrycket inom parentes blir negativt för A_0 över ca 820 dB; då är
    formeln inte definierad och funktionen ger `BullerFel` (se `docs/buller/tolkningar.md`).
    """
    if vagstracka_m < 0.0:
        raise BullerFel("Vägsträckan får inte vara negativ.")
    resultat = np.empty(frekvenser_hz.shape[0], dtype=np.float64)
    for i, f in enumerate(frekvenser_hz):
        a0 = (
            rentonsdampning_db_per_m(
                float(f), lufttemperatur_c, relativ_fuktighet_procent, lufttryck_kpa
            )
            * vagstracka_m
        )
        bas = 1.0053255 - 0.00122622 * a0
        if bas <= 0.0:
            raise BullerFel(
                f"Luftabsorptionen A0 = {a0:.0f} dB vid {float(f):.0f} Hz ligger utanför det "
                "område där AV 1106/07 ekv. (287) är definierad."
            )
        resultat[i] = -a0 * bas**1.6
    return resultat
