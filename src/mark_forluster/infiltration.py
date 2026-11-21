"""Deterministisk Green–Ampt-infiltration per cell och för en hel regntidsserie."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from mark_forluster.parametrar import (
    ForlustFel,
    JordartParametrar,
    effektiv_markparametrar,
)

MM_PER_M = 1000.0
SEKUNDER_PER_TIMME = 3600.0


@dataclass(frozen=True)
class GreenAmptStegResultat:
    """Resultat för en enskild cell/punkt över en regntidsserie (enheter i meter)."""

    regn_totalt_m: float
    initial_forlust_m: float
    infiltration_m: float
    netto_regn_m: float
    infiltration_serie_m: NDArray[np.float64]
    netto_regn_serie_m: NDArray[np.float64]

    @property
    def total_forlust_m(self) -> float:
        """Total förlust (initial ytmagasinering + infiltration) i meter."""
        return self.initial_forlust_m + self.infiltration_m

    @property
    def massbalans_fel_m(self) -> float:
        """Avvikelse `regn - (initial + infiltration + netto)` i meter."""
        return self.regn_totalt_m - (
            self.initial_forlust_m + self.infiltration_m + self.netto_regn_m
        )


@dataclass(frozen=True)
class InfiltrationResultat:
    """Rasterresultat av cellvis Green–Ampt-infiltration över hela regnhändelsen."""

    regn_totalt_m: float
    initial_forlust_m: NDArray[np.float32]
    infiltration_m: NDArray[np.float32]
    total_forlust_m: NDArray[np.float32]
    netto_regn_m: NDArray[np.float32]
    max_massbalans_fel_m: float


def _validera_regntidsserie(
    regn_tid_s: NDArray[np.float64] | list[float] | tuple[float, ...],
    regn_intensitet_mm_h: NDArray[np.float64] | list[float] | tuple[float, ...],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Validerar att regnets tidsserie är strikt ökande, icke-negativ och ändlig."""
    t = np.asarray(regn_tid_s, dtype=np.float64)
    intens = np.asarray(regn_intensitet_mm_h, dtype=np.float64)

    if t.ndim != 1 or intens.ndim != 1:
        raise ForlustFel("Regntidsserien måste vara endimensionell.")
    if t.size < 2:
        raise ForlustFel("Regntidsserien måste innehålla minst 2 tidpunkter.")
    if t.size != intens.size:
        raise ForlustFel(
            f"regn_tid_s ({t.size}) och regn_intensitet_mm_h ({intens.size}) "
            "måste ha samma längd."
        )
    if not np.all(np.isfinite(t)) or not np.all(np.isfinite(intens)):
        raise ForlustFel("Regntidsserien får inte innehålla NaN eller oändliga värden.")
    if np.any(np.diff(t) <= 0.0):
        raise ForlustFel("regn_tid_s måste vara strikt monotont ökande.")
    if np.any(intens < 0.0):
        raise ForlustFel("regn_intensitet_mm_h måste vara >= 0.")
    return t, intens


def _los_implicit_green_ampt(
    f_start_m: float,
    dt_s: float,
    ksat_m_s: float,
    psi_dtheta_m: float,
) -> float:
    """Löser `F - F_0 - psi*dtheta * ln((F + psi*dtheta)/(F_0 + psi*dtheta)) = K * dt`.

    Använder Newton–Raphson med garanterat konvergensfönster för positiv monoton funktion.
    """
    if ksat_m_s <= 0.0 or dt_s <= 0.0:
        return f_start_m
    if psi_dtheta_m <= 0.0:
        return f_start_m + ksat_m_s * dt_s

    kdt = ksat_m_s * dt_s
    bas = f_start_m + psi_dtheta_m
    if bas <= 0.0:
        return f_start_m + kdt

    # Startgissning: garanterat överskattad och >= f_start_m
    f_curr = f_start_m + kdt + math.sqrt(2.0 * psi_dtheta_m * kdt)
    for _ in range(30):
        kvot = (f_curr + psi_dtheta_m) / bas
        g = f_curr - f_start_m - psi_dtheta_m * math.log(kvot) - kdt
        deriv = f_curr / (f_curr + psi_dtheta_m)
        if deriv <= 0.0:
            break
        steg = g / deriv
        f_next = max(f_start_m, f_curr - steg)
        if abs(f_next - f_curr) <= 1e-13 * max(1.0, f_next):
            f_curr = f_next
            break
        f_curr = f_next
    return f_curr


def green_ampt_tidsserie(
    regn_tid_s: NDArray[np.float64] | list[float] | tuple[float, ...],
    regn_intensitet_mm_h: NDArray[np.float64] | list[float] | tuple[float, ...],
    ksat_mm_h: float,
    psi_m: float,
    delta_theta: float,
    initial_mm: float = 2.0,
    max_lagring_m: float | None = None,
) -> GreenAmptStegResultat:
    """Beräknar kumulativ Green–Ampt-infiltration och ytavrinning för en punkt/cell.

    Args:
        regn_tid_s: Tidpunkter i sekunder (strikt ökande, minst 2 värden). Intervallet
            `[tid[i], tid[i+1]]` använder intensiteten `intensitet[i]`.
        regn_intensitet_mm_h: Regnintensitet i mm/h vid motsvarande tidpunkt.
        ksat_mm_h: Mättad hydraulisk konduktivitet i mm/h (`>= 0`).
        psi_m: Sughöjd vid vätningsfronten i meter (`>= 0`).
        delta_theta: Tillgängligt fuktunderskott `theta_s - theta_i` i m³/m³ (`>= 0`).
        initial_mm: Initial ytmagasinering/interception i mm (`>= 0`).
        max_lagring_m: Eventuellt tak för maximal markinfiltration (t.ex. från grundvatten)
            i meter. `None` betyder obegränsad jordprofil.

    Returns:
        `GreenAmptStegResultat` med totaler och stegvisa tidsserier i meter.
    """
    t, intens = _validera_regntidsserie(regn_tid_s, regn_intensitet_mm_h)

    for namn, varde in (
        ("ksat_mm_h", ksat_mm_h),
        ("psi_m", psi_m),
        ("delta_theta", delta_theta),
        ("initial_mm", initial_mm),
    ):
        if not math.isfinite(float(varde)) or float(varde) < 0.0:
            raise ForlustFel(f"Parametern '{namn}' måste vara ändlig och >= 0, fick {varde}.")

    if max_lagring_m is not None and (
        not math.isfinite(float(max_lagring_m)) or float(max_lagring_m) < 0.0
    ):
        raise ForlustFel(f"max_lagring_m måste vara ändlig och >= 0, fick {max_lagring_m}.")

    n_steg = t.size - 1
    infil_serie = np.zeros(n_steg, dtype=np.float64)
    netto_serie = np.zeros(n_steg, dtype=np.float64)

    ksat_m_s = float(ksat_mm_h) / (MM_PER_M * SEKUNDER_PER_TIMME)
    psi_dtheta = float(psi_m) * float(delta_theta)
    initial_tak_m = float(initial_mm) / MM_PER_M
    infil_tak_m = math.inf if max_lagring_m is None else float(max_lagring_m)

    init_fylld = 0.0
    f_kum = 0.0
    netto_kum = 0.0
    regn_totalt = 0.0

    for i in range(n_steg):
        dt = float(t[i + 1] - t[i])
        i_m_s = float(intens[i]) / (MM_PER_M * SEKUNDER_PER_TIMME)
        regn_steg = i_m_s * dt
        regn_totalt += regn_steg

        # 1. Fyll initial ytmagasinering innan vatten når infiltration/avrinning
        init_utrymme = max(0.0, initial_tak_m - init_fylld)
        till_init = min(regn_steg, init_utrymme)
        init_fylld += till_init
        kvar_av_regn = regn_steg - till_init

        if kvar_av_regn <= 0.0:
            infil_serie[i] = f_kum
            netto_serie[i] = netto_kum
            continue

        # 2. Kontrollera tillgänglig marklagring (grundvattentak)
        lagring_utrymme = max(0.0, infil_tak_m - f_kum)
        if ksat_m_s <= 0.0 or lagring_utrymme <= 0.0:
            netto_kum += kvar_av_regn
            infil_serie[i] = f_kum
            netto_serie[i] = netto_kum
            continue

        # Regnet fyller först den initiala magasineringen; infiltrationen börjar när den är
        # full. Resten av steget har alltså full intensitet under den återstående tiden
        # (tidigare späddes intensiteten ut över hela steget, vilket gav för mycket
        # infiltration med långa steg).
        dt_eff = dt - (till_init / i_m_s if i_m_s > 0.0 else 0.0)
        if dt_eff <= 0.0:
            dt_eff = dt
        eff_i_m_s = kvar_av_regn / dt_eff

        # Nuvarande potentiell infiltrationskapacitet vid f_kum
        kap_start = (
            math.inf
            if f_kum <= 0.0 and psi_dtheta > 0.0
            else ksat_m_s * (1.0 + psi_dtheta / max(f_kum, 1e-12))
        )

        if eff_i_m_s <= ksat_m_s:
            # Intensiteten understiger Ksat -> ingen dammbildning möjlig
            df_pot = kvar_av_regn
        elif eff_i_m_s >= kap_start:
            # Dammbildning redan vid stegets start -> lös implicit Green–Ampt
            f_slut_pot = _los_implicit_green_ampt(f_kum, dt_eff, ksat_m_s, psi_dtheta)
            df_pot = max(0.0, min(kvar_av_regn, f_slut_pot - f_kum))
        else:
            # Dammbildning uppstår inuti tidssteget (Mein–Larson-villkor)
            # F_p = (psi * delta_theta) / (i / Ksat - 1)
            f_p = psi_dtheta / (eff_i_m_s / ksat_m_s - 1.0)
            if f_kum + kvar_av_regn <= f_p:
                df_pot = kvar_av_regn
            else:
                df_fore_p = max(0.0, f_p - f_kum)
                dt_fore_p = df_fore_p / eff_i_m_s
                dt_efter_p = max(0.0, dt_eff - dt_fore_p)
                f_slut_pot = _los_implicit_green_ampt(
                    f_kum + df_fore_p, dt_efter_p, ksat_m_s, psi_dtheta
                )
                df_pot = max(0.0, min(kvar_av_regn, f_slut_pot - f_kum))

        df = min(df_pot, lagring_utrymme)
        f_kum += df
        netto_kum += kvar_av_regn - df
        infil_serie[i] = f_kum
        netto_serie[i] = netto_kum

    return GreenAmptStegResultat(
        regn_totalt_m=regn_totalt,
        initial_forlust_m=init_fylld,
        infiltration_m=f_kum,
        netto_regn_m=netto_kum,
        infiltration_serie_m=infil_serie,
        netto_regn_serie_m=netto_serie,
    )


def berakna_cell_infiltration(
    markslag: NDArray[np.uint8],
    jordart: NDArray[np.uint8] | NDArray[np.str_] | str | int,
    regn_tid_s: NDArray[np.float64] | list[float] | tuple[float, ...],
    regn_intensitet_mm_h: NDArray[np.float64] | list[float] | tuple[float, ...],
    *,
    initial_mm: float = 2.0,
    tjale: bool = False,
    hog_markfukt: bool = False,
    grundvatten_tak_m: NDArray[np.float32] | NDArray[np.float64] | None = None,
    tabell: dict[str, JordartParametrar] | None = None,
) -> InfiltrationResultat:
    """Vektoriserad Green–Ampt-beräkning för ett helt markslags- och jordartsraster.

    Unika kombinationer av `(markslag, jordart, kvantiserat grundvattentak)` löses en
    gång och mappas ut över hela rastret. Vid kontinuerligt grundvattentak beräknas
    cellerna per unik `(markslag, jordart)`-grupp, och om grundvattentaket begränsar
    några celler beräknas endast de berörda cellernas unika taknivåer (avrundade till
    0,1 mm för effektivitet, eller exakt per unik nivå om få).
    """
    ms = np.asarray(markslag)
    if ms.ndim != 2:
        raise ForlustFel(f"markslag måste vara ett 2D-raster, fick form {ms.shape}.")
    if ms.size == 0:
        raise ForlustFel("markslag får inte vara tomt.")
    if not np.issubdtype(ms.dtype, np.integer):
        raise ForlustFel(f"markslag måste ha heltalstyp (uint8), fick {ms.dtype}.")

    if not math.isfinite(float(initial_mm)) or float(initial_mm) < 0.0:
        raise ForlustFel(f"initial_mm måste vara ändlig och >= 0, fick {initial_mm}.")

    if isinstance(jordart, str | int) and not isinstance(jordart, bool):
        ja_arr: NDArray[np.uint8] | NDArray[np.str_] = np.full(ms.shape, jordart)
    else:
        ja_arr = np.asarray(jordart)
        if ja_arr.shape != ms.shape:
            raise ForlustFel(
                f"jordart har form {ja_arr.shape}, förväntade {ms.shape} som markslag."
            )

    if grundvatten_tak_m is not None:
        tak_arr = np.asarray(grundvatten_tak_m, dtype=np.float64)
        if tak_arr.shape != ms.shape:
            raise ForlustFel(
                f"grundvatten_tak_m har form {tak_arr.shape}, förväntade {ms.shape}."
            )
        if not np.all(np.isfinite(tak_arr)) or np.any(tak_arr < 0.0):
            raise ForlustFel("grundvatten_tak_m måste innehålla ändliga värden >= 0.")
    else:
        tak_arr = None

    # Validera regntidsserien direkt
    t_arr, i_arr = _validera_regntidsserie(regn_tid_s, regn_intensitet_mm_h)

    init_out = np.zeros(ms.shape, dtype=np.float64)
    infil_out = np.zeros(ms.shape, dtype=np.float64)
    netto_out = np.zeros(ms.shape, dtype=np.float64)

    ms_flat = ms.ravel()
    ja_flat = ja_arr.ravel()
    unika_ms = np.unique(ms_flat)
    unika_ja = np.unique(ja_flat)

    regn_totalt_m = 0.0
    for ms_val in unika_ms:
        ms_int = int(ms_val)
        ms_mask = ms_flat == ms_val
        for ja_val in unika_ja:
            kombo_mask = ms_mask & (ja_flat == ja_val)
            if not np.any(kombo_mask):
                continue

            ja_nyckel: str | int = (
                int(ja_val) if np.issubdtype(ja_arr.dtype, np.integer) else str(ja_val)
            )
            eff = effektiv_markparametrar(
                ms_int,
                ja_nyckel,
                hog_markfukt=hog_markfukt,
                tjale=tjale,
                tabell=tabell,
            )

            bas_res = green_ampt_tidsserie(
                t_arr,
                i_arr,
                ksat_mm_h=eff.ksat_mm_h,
                psi_m=eff.psi_m,
                delta_theta=eff.delta_theta,
                initial_mm=float(initial_mm) * eff.initial_faktor,
                max_lagring_m=None,
            )
            regn_totalt_m = bas_res.regn_totalt_m

            if tak_arr is None:
                init_out.ravel()[kombo_mask] = bas_res.initial_forlust_m
                infil_out.ravel()[kombo_mask] = bas_res.infiltration_m
                netto_out.ravel()[kombo_mask] = bas_res.netto_regn_m
            else:
                tak_kombo = tak_arr.ravel()[kombo_mask]
                obegransad = tak_kombo >= bas_res.infiltration_m
                if np.all(obegransad):
                    init_out.ravel()[kombo_mask] = bas_res.initial_forlust_m
                    infil_out.ravel()[kombo_mask] = bas_res.infiltration_m
                    netto_out.ravel()[kombo_mask] = bas_res.netto_regn_m
                else:
                    # För obegränsade celler används basresultatet direkt
                    idx_kombo = np.flatnonzero(kombo_mask)
                    idx_obeg = idx_kombo[obegransad]
                    init_out.ravel()[idx_obeg] = bas_res.initial_forlust_m
                    infil_out.ravel()[idx_obeg] = bas_res.infiltration_m
                    netto_out.ravel()[idx_obeg] = bas_res.netto_regn_m

                    # För takbegränsade celler är grundvattentaket strikt bindande:
                    # totalt infiltrerad volym blir exakt taket (eftersom obegränsad
                    # infiltration > tak) och resterande regn efter initial förlust blir
                    # ytavrinning (netto_regn_m).
                    idx_beg = idx_kombo[~obegransad]
                    tak_beg = tak_kombo[~obegransad]
                    init_out.ravel()[idx_beg] = bas_res.initial_forlust_m
                    infil_out.ravel()[idx_beg] = tak_beg
                    netto_out.ravel()[idx_beg] = np.maximum(
                        0.0,
                        bas_res.regn_totalt_m - bas_res.initial_forlust_m - tak_beg,
                    )

    total_out = init_out + infil_out
    max_fel = float(np.max(np.abs(regn_totalt_m - (total_out + netto_out))))

    return InfiltrationResultat(
        regn_totalt_m=regn_totalt_m,
        initial_forlust_m=init_out.astype(np.float32),
        infiltration_m=infil_out.astype(np.float32),
        total_forlust_m=total_out.astype(np.float32),
        netto_regn_m=netto_out.astype(np.float32),
        max_massbalans_fel_m=max_fel,
    )
