# SPDX-License-Identifier: MIT
"""Vindtunnelvalidering mot CEDVAL A1-1 (Universität Hamburg, EWTL).

Modulen skiljer strikt mellan tre informationskällor:

1. **Publicerad geometri och inloppsprofil** för CEDVAL A1-1 (Leitl & Schatzmann
   2010, EWTL, Universität Hamburg): en fristående rektangulär byggnad med
   fullskalemått ``L_x × L_y × H = 20 m × 30 m × 25 m`` (modellskala 1:200 i
   vindtunneln: ``0,10 × 0,15 × 0,125 m``), neutral logaritmisk gränsskiktsprofil
   ``U(z) = (u_* / κ) ln((z + z_0) / z_0)`` med ``z_0 = 0,7 m`` fullskala
   (``0,0035 m`` modellskala), samt publicerat litteraturintervall för
   återanslutningslängd i lä ``X_R / H ≈ 1,0–1,8`` (Franke m.fl. 2007 / COST 732;
   Leitl & Schatzmann 2010).
2. **Deterministisk 3D-beräkning med Flodens egna vindlösare** (`snabbvind` eller
   `lbm`) på CEDVAL A1-1-geometrin med uppmätt masskonservering, no-slip i
   byggnadsceller, inloppsprofilfel, uppströms vinddämpning och
   lävirvel/vindskugga.
3. **Valfri fältjämförelse mot lokalt nedladdade CEDVAL A1-1-mätdata**
   (`jamfor_mot_cedval_csv`) med FAIRMODE/COST 732-mått (`RMSE`, `NMSE`, `FB`,
   `FAC2`, `hit_rate` q). Modulen innehåller **aldrig** påhittade eller
   incheckade experimentella vindtunneldata; saknad eller ogiltig CSV-fil ger
   tydligt fel (`FileNotFoundError` / `ValueError`).

Koordinatkonvention (CEDVAL A1-1 fullskala)
-------------------------------------------
* Origo ``(x = 0, y = 0, z = 0)`` ligger i byggnadens bascentrum på markplanet.
* Vinden blåser i ``+x``-riktningen (meteorologisk västvind 270°).
* Byggnaden upptar ``x ∈ [-L_x/2, +L_x/2]``, ``y ∈ [-L_y/2, +L_y/2]``,
  ``z ∈ [0, H]``.
* Återanslutningslängden ``X_R`` mäts nedströms från byggnadens baksida
  (``x = +L_x/2``) längs symmetrilinjen ``y = 0`` i första cellagret över mark.

Licensgräns: `src/luft/` är MIT-licensierat och importerar enbart stdlib,
`numpy`, `scipy`, `luft.*` samt `gemensamt.*` (inga GPL-paket).
"""

from __future__ import annotations

import csv
import math
import os
import platform
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, overload

import numpy as np
import numpy.typing as npt
from scipy.interpolate import RegularGridInterpolator

from gemensamt.hash import sha256_fil
from gemensamt.manifest import hamta_kodversion
from luft.lbm import LbmParametrar, kor_les
from luft.snabbvind import kor_snabbvind
from luft.typer import FloatArray, Inflode, Vindfalt, Voxelgrid

CEDVAL_FALL_ID = "CEDVAL-A1-1"
CEDVAL_KALLA = (
    "Universität Hamburg, Environmental Wind Tunnel Laboratory (EWTL), "
    "CEDVAL A1-1 (Leitl & Schatzmann 2010)"
)
CEDVAL_MODELLSKALA = 200.0
CEDVAL_XR_OVER_H_LITTERATUR_MIN = 1.0
CEDVAL_XR_OVER_H_LITTERATUR_MAX = 1.8

# COST 732 / VDI 3783 Blatt 9 / Chang & Hanna (2004) valideringskriterier
COST732_FAC2_GRANS = 0.5
COST732_FB_ABS_GRANS = 0.3
COST732_NMSE_GRANS = 4.0
COST732_HIT_RATE_GRANS = 0.66
COST732_HIT_RATE_REL_TOL = 0.25  # D = 0,25
COST732_HIT_RATE_ABS_TOL = 0.05  # W = 0,05 (normaliserad hastighet u/U_ref)

K37_FORMAT = "floden-benchmark/v1"
LosareNamn = Literal["snabbvind", "lbm"]
_CSV_KOLUMNER = ("x_m", "y_m", "z_m", "u_norm", "v_norm", "w_norm")


# --------------------------------------------------------------------------------------
# Specifikation och analytisk ABL-profil
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CedvalA11Spec:
    """Publicerad fullskalespecifikation för CEDVAL A1-1 och beräkningsdomän.

    Standardvärdena för byggnaden och inloppsprofilen motsvarar CEDVAL A1-1 i
    fullskala (1:200 × vindtunnelmodell). Domänmåtten följer COST 732:s
    rekommendationer (≥ 5 H uppströms, ≥ 10 H nedströms, ≥ 4 H per sida,
    ≥ 5 H domänhöjd, blockeringsgrad < 3 %).
    """

    langd_x_m: float = 20.0
    bredd_y_m: float = 30.0
    hojd_h_m: float = 25.0
    skrovlighet_z0_m: float = 0.7
    u_ref_m_s: float = 6.0
    z_ref_m: float = 25.0
    kappa: float = 0.41
    modellskala: float = CEDVAL_MODELLSKALA
    uppstroms_m: float = 125.0
    nedstroms_m: float = 250.0
    sidomarginal_m: float = 100.0
    domanhojd_m: float = 125.0
    cellstorlek_m: float = 5.0
    turbulensintensitet: float = 0.0

    def __post_init__(self) -> None:
        positiva = (
            ("langd_x_m", self.langd_x_m),
            ("bredd_y_m", self.bredd_y_m),
            ("hojd_h_m", self.hojd_h_m),
            ("skrovlighet_z0_m", self.skrovlighet_z0_m),
            ("u_ref_m_s", self.u_ref_m_s),
            ("z_ref_m", self.z_ref_m),
            ("kappa", self.kappa),
            ("modellskala", self.modellskala),
            ("uppstroms_m", self.uppstroms_m),
            ("nedstroms_m", self.nedstroms_m),
            ("sidomarginal_m", self.sidomarginal_m),
            ("domanhojd_m", self.domanhojd_m),
            ("cellstorlek_m", self.cellstorlek_m),
        )
        for namn, varde in positiva:
            if not math.isfinite(varde) or varde <= 0.0:
                raise ValueError(f"{namn} måste vara ett ändligt positivt tal, fick {varde!r}.")
        if self.z_ref_m <= self.skrovlighet_z0_m:
            raise ValueError("z_ref_m måste vara strikt större än skrovlighet_z0_m.")
        if self.domanhojd_m <= self.hojd_h_m:
            raise ValueError("domanhojd_m måste vara strikt större än hojd_h_m.")
        if not math.isfinite(self.turbulensintensitet) or self.turbulensintensitet < 0.0:
            raise ValueError("turbulensintensitet måste vara ändlig och >= 0.")

    @property
    def u_star_m_s(self) -> float:
        """Friktionshastighet u_* (m/s) ur den neutrala logaritmiska profilen."""
        return (
            self.kappa
            * self.u_ref_m_s
            / math.log((self.z_ref_m + self.skrovlighet_z0_m) / self.skrovlighet_z0_m)
        )

    @property
    def total_langd_x_m(self) -> float:
        """Total domänlängd i x-riktning (m)."""
        return self.uppstroms_m + self.langd_x_m + self.nedstroms_m

    @property
    def total_bredd_y_m(self) -> float:
        """Total domänbredd i y-riktning (m)."""
        return 2.0 * self.sidomarginal_m + self.bredd_y_m

    @property
    def blockeringsgrad(self) -> float:
        """Nominell blockeringsgrad (byggnadens frontarea / domänens tvärsnittsarea)."""
        return (self.bredd_y_m * self.hojd_h_m) / (self.total_bredd_y_m * self.domanhojd_m)


def friktionshastighet_u_star_m_s(
    *,
    u_ref_m_s: float,
    z_ref_m: float,
    z0_m: float,
    kappa: float = 0.41,
) -> float:
    """Beräknar friktionshastigheten u_* = κ · U_ref / ln((z_ref + z0) / z0) (m/s)."""
    _validera_abl_parametrar(u_ref_m_s=u_ref_m_s, z_ref_m=z_ref_m, z0_m=z0_m, kappa=kappa)
    return kappa * u_ref_m_s / math.log((z_ref_m + z0_m) / z0_m)


def _validera_abl_parametrar(
    *,
    u_ref_m_s: float,
    z_ref_m: float,
    z0_m: float,
    kappa: float,
) -> None:
    for namn, varde in (
        ("u_ref_m_s", u_ref_m_s),
        ("z_ref_m", z_ref_m),
        ("z0_m", z0_m),
        ("kappa", kappa),
    ):
        if not math.isfinite(varde) or varde <= 0.0:
            raise ValueError(f"{namn} måste vara ett ändligt positivt tal, fick {varde!r}.")
    if z_ref_m <= z0_m:
        raise ValueError("z_ref_m måste vara strikt större än z0_m.")


@overload
def abl_log_profil_m_s(
    z_m: float | int,
    *,
    u_ref_m_s: float,
    z_ref_m: float,
    z0_m: float,
    kappa: float = 0.41,
) -> float: ...


@overload
def abl_log_profil_m_s(
    z_m: npt.NDArray[np.floating[Any]] | Sequence[float],
    *,
    u_ref_m_s: float,
    z_ref_m: float,
    z0_m: float,
    kappa: float = 0.41,
) -> npt.NDArray[np.float64]: ...


def abl_log_profil_m_s(
    z_m: float | int | npt.NDArray[np.floating[Any]] | Sequence[float],
    *,
    u_ref_m_s: float,
    z_ref_m: float,
    z0_m: float,
    kappa: float = 0.41,
) -> float | npt.NDArray[np.float64]:
    """Exakt analytisk neutral logaritmisk ABL-vindprofil U(z) (m/s).

    .. math::

        U(z) = \\frac{u_*}{\\kappa} \\ln\\!\\left(\\frac{z + z_0}{z_0}\\right)
             = U_{\\text{ref}} \\frac{\\ln((z + z_0)/z_0)}{\\ln((z_{\\text{ref}} + z_0)/z_0)}
        \\quad (z > 0), \\qquad U(z) = 0 \\quad (z \\le 0).
    """
    _validera_abl_parametrar(u_ref_m_s=u_ref_m_s, z_ref_m=z_ref_m, z0_m=z0_m, kappa=kappa)
    namnare = math.log((z_ref_m + z0_m) / z0_m)
    if isinstance(z_m, int | float | np.integer | np.floating):
        zf = float(z_m)
        if not math.isfinite(zf):
            raise ValueError(f"z_m måste vara ändligt, fick {z_m!r}.")
        if zf <= 0.0:
            return 0.0
        return float(u_ref_m_s * math.log((zf + z0_m) / z0_m) / namnare)

    z_arr = np.asarray(z_m, dtype=np.float64)
    if not np.all(np.isfinite(z_arr)):
        raise ValueError("z_m får inte innehålla NaN eller Inf.")
    z_pos = np.maximum(z_arr, 0.0)
    u_arr: npt.NDArray[np.float64] = np.where(
        z_arr > 0.0,
        u_ref_m_s * np.log((z_pos + z0_m) / z0_m) / namnare,
        0.0,
    )
    return u_arr


# --------------------------------------------------------------------------------------
# Domänbyggare
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CedvalDoman:
    """Voxeliserad 3D-beräkningsdomän för CEDVAL A1-1 med byggnad och inloppsprofil."""

    spec: CedvalA11Spec
    grid: Voxelgrid
    inflode: Inflode
    cellstorlek_m: float
    x_m: npt.NDArray[np.float64]
    y_m: npt.NDArray[np.float64]
    z_m: npt.NDArray[np.float64]
    inloppsprofil_m_s: npt.NDArray[np.float64]
    byggnad_i_intervall: tuple[int, int]
    byggnad_j_intervall: tuple[int, int]
    byggnad_k_intervall: tuple[int, int]
    byggnad_x_min_m: float
    byggnad_x_max_m: float
    byggnad_y_min_m: float
    byggnad_y_max_m: float
    byggnad_z_max_m: float

    @property
    def antal_celler_totalt(self) -> int:
        return int(self.grid.fast.size)

    @property
    def antal_solidceller(self) -> int:
        return int(np.count_nonzero(self.grid.fast))

    @property
    def antal_luftceller(self) -> int:
        return self.antal_celler_totalt - self.antal_solidceller

    @property
    def faktisk_blockeringsgrad(self) -> float:
        """Voxeliserad blockeringsgrad på rutnätet."""
        _, ny, nz = self.grid.form
        ny_b = self.byggnad_j_intervall[1] - self.byggnad_j_intervall[0]
        nz_b = self.byggnad_k_intervall[1] - self.byggnad_k_intervall[0]
        return float((ny_b * nz_b) / (ny * nz))


def bygg_cedval_a11_doman(
    spec: CedvalA11Spec | None = None,
    cellstorlek_m: float | None = None,
) -> CedvalDoman:
    """Bygger 3D-solidmask, cellkoordinater och ABL-inloppsprofil för CEDVAL A1-1.

    Byggnaden placeras symmetriskt kring ``(x = 0, y = 0)`` på markplanet
    ``z = 0``. När ``cellstorlek_m`` delar byggnadsmåtten (t.ex. 5,0, 2,5 eller
    1,0 m) täcker solidmasken exakt ``[-10, 10] × [-15, 15] × [0, 25] m``.
    """
    bas = CedvalA11Spec() if spec is None else spec
    dx = bas.cellstorlek_m if cellstorlek_m is None else float(cellstorlek_m)
    if not math.isfinite(dx) or dx <= 0.0:
        raise ValueError(f"cellstorlek_m måste vara ändlig och positiv, fick {dx!r}.")
    eff_spec = replace(bas, cellstorlek_m=dx)

    nx_upp = max(1, round(eff_spec.uppstroms_m / dx))
    nx_bygg = max(1, round(eff_spec.langd_x_m / dx))
    nx_ned = max(1, round(eff_spec.nedstroms_m / dx))
    ny_sida = max(1, round(eff_spec.sidomarginal_m / dx))
    ny_bygg = max(1, round(eff_spec.bredd_y_m / dx))
    nz_bygg = max(1, round(eff_spec.hojd_h_m / dx))
    nz = max(nz_bygg + 1, round(eff_spec.domanhojd_m / dx))

    nx = nx_upp + nx_bygg + nx_ned
    ny = 2 * ny_sida + ny_bygg

    origo_x_m = -(nx_upp + 0.5 * nx_bygg) * dx
    origo_y_m = -(ny_sida + 0.5 * ny_bygg) * dx
    origo_z_m = 0.0

    fast = np.zeros((nx, ny, nz), dtype=np.bool_)
    i0, i1 = nx_upp, nx_upp + nx_bygg
    j0, j1 = ny_sida, ny_sida + ny_bygg
    k0, k1 = 0, nz_bygg
    fast[i0:i1, j0:j1, k0:k1] = True

    grid = Voxelgrid(
        dx_m=dx,
        fast=fast,
        lad_m2_m3=np.zeros((nx, ny, nz), dtype=np.float64),
        vindriktning_grad=270.0,
        origo_x_m=origo_x_m,
        origo_y_m=origo_y_m,
        origo_z_m=origo_z_m,
    )
    inflode = Inflode(
        u_ref_m_s=eff_spec.u_ref_m_s,
        z_ref_m=eff_spec.z_ref_m,
        z0_m=eff_spec.skrovlighet_z0_m,
        turbulensintensitet=eff_spec.turbulensintensitet,
    )

    x_m = origo_x_m + (np.arange(nx, dtype=np.float64) + 0.5) * dx
    y_m = origo_y_m + (np.arange(ny, dtype=np.float64) + 0.5) * dx
    z_m = np.asarray(grid.cellcentrum_z_m(), dtype=np.float64)
    inloppsprofil_m_s = abl_log_profil_m_s(
        z_m,
        u_ref_m_s=eff_spec.u_ref_m_s,
        z_ref_m=eff_spec.z_ref_m,
        z0_m=eff_spec.skrovlighet_z0_m,
        kappa=eff_spec.kappa,
    )

    return CedvalDoman(
        spec=eff_spec,
        grid=grid,
        inflode=inflode,
        cellstorlek_m=dx,
        x_m=x_m,
        y_m=y_m,
        z_m=z_m,
        inloppsprofil_m_s=inloppsprofil_m_s,
        byggnad_i_intervall=(i0, i1),
        byggnad_j_intervall=(j0, j1),
        byggnad_k_intervall=(k0, k1),
        byggnad_x_min_m=origo_x_m + i0 * dx,
        byggnad_x_max_m=origo_x_m + i1 * dx,
        byggnad_y_min_m=origo_y_m + j0 * dx,
        byggnad_y_max_m=origo_y_m + j1 * dx,
        byggnad_z_max_m=origo_z_m + k1 * dx,
    )


# --------------------------------------------------------------------------------------
# Deterministisk 3D-körning och fysikalisk diagnostik
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CedvalResultat:
    """Uppmätta fysik- och valideringsmått från en CEDVAL A1-1-körning."""

    doman: CedvalDoman
    vindfalt: Vindfalt
    losare: LosareNamn
    cellstorlek_m: float
    max_divergens_1_s: float
    rms_divergens_1_s: float
    projektion_divergens_rel: float | None
    max_hastighet_i_solid_m_s: float
    inloppsprofil_max_fel_m_s: float
    inloppsprofil_rel_fel: float
    uppstroms_dampning_norm: float
    uppstroms_inlopps_mark_norm: float
    har_recirkulationszon: bool
    ateranslutning_x_r_m: float | None
    ateranslutning_x_r_over_h: float | None
    ateranslutning_inom_litteratur: bool | None
    min_u_x_la_norm: float
    kortid_s: float
    metodstatus: Literal["skiss"] = "skiss"
    jamford_mot_vindtunnel_data: bool = False
    kodversion: str = "okand"
    anmarkningar: tuple[str, ...] = ()


def _centrumlinje_y(ux_3d: FloatArray, ny: int, k: int) -> npt.NDArray[np.float64]:
    """Extraherar u_x längs symmetriplanet y = 0 på höjdlager k."""
    if ny % 2 == 0:
        return np.asarray(
            0.5 * (ux_3d[:, ny // 2 - 1, k] + ux_3d[:, ny // 2, k]),
            dtype=np.float64,
        )
    return np.asarray(ux_3d[:, ny // 2, k], dtype=np.float64)


def _berakna_inre_divergens_1_s(vindfalt: Vindfalt) -> tuple[float, float]:
    """Beräknar max och RMS av |∇·u| (1/s) med centraldifferens i inre luftceller."""
    u = vindfalt.u_m_s
    dx = vindfalt.grid.dx_m
    luft = vindfalt.grid.luft
    nx, ny, nz = vindfalt.grid.form
    if nx < 3 or ny < 3 or nz < 3:
        return 0.0, 0.0
    inre = (
        luft[1:-1, 1:-1, 1:-1]
        & luft[2:, 1:-1, 1:-1]
        & luft[:-2, 1:-1, 1:-1]
        & luft[1:-1, 2:, 1:-1]
        & luft[1:-1, :-2, 1:-1]
        & luft[1:-1, 1:-1, 2:]
        & luft[1:-1, 1:-1, :-2]
    )
    if not bool(np.any(inre)):
        return 0.0, 0.0
    div = (
        (u[0, 2:, 1:-1, 1:-1] - u[0, :-2, 1:-1, 1:-1])
        + (u[1, 1:-1, 2:, 1:-1] - u[1, 1:-1, :-2, 1:-1])
        + (u[2, 1:-1, 1:-1, 2:] - u[2, 1:-1, 1:-1, :-2])
    ) / (2.0 * dx)
    inre_vals = np.abs(div[inre])
    max_div = float(np.max(inre_vals))
    rms_div = float(np.sqrt(np.mean(inre_vals * inre_vals)))
    return max_div, rms_div


def _hitta_ateranslutning_m(
    x_la_m: npt.NDArray[np.float64],
    ux_la_m_s: npt.NDArray[np.float64],
    byggnad_x_max_m: float,
) -> tuple[bool, float | None]:
    """Söker recirkulationszon (u_x < 0) och linjärt interpolerad återanslutning bakom byggnad."""
    if ux_la_m_s.size == 0 or not bool(np.any(ux_la_m_s < 0.0)):
        return False, None
    idx_min = int(np.argmin(ux_la_m_s))
    for m in range(idx_min, ux_la_m_s.size - 1):
        u0 = float(ux_la_m_s[m])
        u1 = float(ux_la_m_s[m + 1])
        if u0 < 0.0 <= u1:
            fraktion = (-u0) / (u1 - u0)
            x_ater = float(x_la_m[m] + (x_la_m[m + 1] - x_la_m[m]) * fraktion)
            return True, x_ater - byggnad_x_max_m
    return True, None


def kor_cedval_a11_validering(
    spec: CedvalA11Spec | None = None,
    *,
    cellstorlek_m: float | None = None,
    losare: LosareNamn = "snabbvind",
    lbm_parametrar: LbmParametrar | None = None,
    backend: str = "numpy",
    periodisk_y: bool = True,
) -> CedvalResultat:
    """Kör vald 3D-vindlösare på CEDVAL A1-1-geometrin och mäter fysikdiagnostik.

    Returnerar alltid `metodstatus="skiss"` och `jamford_mot_vindtunnel_data=False`.
    För experimentell jämförelse mot lokala vindtunneldata, skicka resultatet
    vidare till :func:`jamfor_mot_cedval_csv`.
    """
    if losare not in ("snabbvind", "lbm"):
        raise ValueError(f"Okänd lösare {losare!r}; tillåtna värden är 'snabbvind' och 'lbm'.")

    doman = bygg_cedval_a11_doman(spec, cellstorlek_m=cellstorlek_m)
    t0 = time.perf_counter()
    if losare == "snabbvind":
        vindfalt = kor_snabbvind(doman.grid, doman.inflode, periodisk_y=periodisk_y)
    else:
        param = (
            lbm_parametrar
            if lbm_parametrar is not None
            else LbmParametrar(
                u_lattice="auto",
                upprampning_genomstromningar=1.0,
                medel_genomstromningar=1.0,
                vaggmodell="loglag",
                inflodesrand="extrapolation",
                utflodesrand="tryck",
                start="profil",
            )
        )
        vindfalt = kor_les(doman.grid, doman.inflode, param=param, backend=backend)
    kortid_s = time.perf_counter() - t0

    u = vindfalt.u_m_s
    fast = doman.grid.fast
    u_ref = doman.spec.u_ref_m_s
    _, ny, _ = doman.grid.form
    i0, i1 = doman.byggnad_i_intervall

    # 1. No-slip i byggnadsceller
    max_solid = float(np.max(np.linalg.norm(u[:, fast], axis=0))) if bool(np.any(fast)) else 0.0

    # 2. Masskonservering (inre centraldifferens + ev. Poisson-ytflödesrest)
    max_div, rms_div = _berakna_inre_divergens_1_s(vindfalt)
    raw_div_rel = vindfalt.metadata.get("divergens_rel")
    proj_div_rel = float(raw_div_rel) if isinstance(raw_div_rel, int | float) else None

    # 3. Inloppsprofilfel vid i = 0 (alla luftceller på inloppsplanet)
    inlopp_luft = ~fast[0]
    inlopps_diff = np.abs(u[0, 0] - doman.inloppsprofil_m_s[None, :])
    inlopp_max_fel = float(np.max(inlopps_diff[inlopp_luft])) if bool(np.any(inlopp_luft)) else 0.0
    inlopp_rel_fel = inlopp_max_fel / u_ref

    # 4. Centrumlinje vid marknivå (k = 0): uppströms dämpning och lävirvel/vindskugga
    cl0 = _centrumlinje_y(u[0], ny, 0)
    uppstroms_mark_norm = float(doman.inloppsprofil_m_s[0] / u_ref)
    uppstroms_dampning_norm = float(cl0[i0 - 1] / u_ref)

    ux_la = cl0[i1:]
    x_la = doman.x_m[i1:]
    min_u_x_la_norm = float(np.min(ux_la) / u_ref)
    har_recirk, x_r_m = _hitta_ateranslutning_m(x_la, ux_la, doman.byggnad_x_max_m)
    x_r_over_h = None if x_r_m is None else float(x_r_m / doman.spec.hojd_h_m)
    inom_litt: bool | None = (
        None
        if x_r_over_h is None
        else bool(
            CEDVAL_XR_OVER_H_LITTERATUR_MIN <= x_r_over_h <= CEDVAL_XR_OVER_H_LITTERATUR_MAX
        )
    )

    anmarkningar: list[str] = [
        "Ej jämförd mot lokala experimentella vindtunneldata från EWTL Hamburg "
        "(jamford_mot_vindtunnel_data=False); status är därför 'skiss'.",
    ]
    celler_per_h = doman.spec.hojd_h_m / doman.cellstorlek_m
    if celler_per_h < 10.0:
        anmarkningar.append(
            f"Grov upplösning (dx = {doman.cellstorlek_m:g} m, "
            f"H/dx = {celler_per_h:.1f} celler); skarp vindtunnelvalidering enligt COST 732 "
            "kräver minst 10–20 celler per byggnadshöjd (dx <= 1,25–2,5 m)."
        )
    elif celler_per_h < 20.0:
        anmarkningar.append(
            f"Måttlig upplösning (dx = {doman.cellstorlek_m:g} m, "
            f"H/dx = {celler_per_h:.1f} celler); för fin LES-validering rekommenderas "
            "H/dx >= 20 (dx <= 1,25 m)."
        )
    if losare == "snabbvind" and not har_recirk:
        anmarkningar.append(
            "Skisslösaren snabbvind (Röckle + Poisson-projektion) ger tydlig vindskugga "
            "i lä men kan efter tryckprojektion sakna negativ återströmning (u_x < 0) på "
            "grovt rutnät; kör losare='lbm' för upplöst lävirvel."
        )

    return CedvalResultat(
        doman=doman,
        vindfalt=vindfalt,
        losare=losare,
        cellstorlek_m=doman.cellstorlek_m,
        max_divergens_1_s=max_div,
        rms_divergens_1_s=rms_div,
        projektion_divergens_rel=proj_div_rel,
        max_hastighet_i_solid_m_s=max_solid,
        inloppsprofil_max_fel_m_s=inlopp_max_fel,
        inloppsprofil_rel_fel=inlopp_rel_fel,
        uppstroms_dampning_norm=uppstroms_dampning_norm,
        uppstroms_inlopps_mark_norm=uppstroms_mark_norm,
        har_recirkulationszon=har_recirk,
        ateranslutning_x_r_m=x_r_m,
        ateranslutning_x_r_over_h=x_r_over_h,
        ateranslutning_inom_litteratur=inom_litt,
        min_u_x_la_norm=min_u_x_la_norm,
        kortid_s=kortid_s,
        metodstatus="skiss",
        jamford_mot_vindtunnel_data=False,
        kodversion=hamta_kodversion(),
        anmarkningar=tuple(anmarkningar),
    )


# --------------------------------------------------------------------------------------
# FAIRMODE / COST 732-valideringsmått och lokal CEDVAL CSV-jämförelse
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cost732Matt:
    """FAIRMODE / COST 732 / VDI 3783 Blatt 9-valideringsmått för en komponent."""

    antal_punkter: int
    rmse: float
    nmse: float | None
    fb: float | None
    fac2: float
    hit_rate: float
    bias: float
    korrelation_r: float | None

    def till_dict(self) -> dict[str, Any]:
        return {
            "antal_punkter": self.antal_punkter,
            "rmse": self.rmse,
            "nmse": self.nmse,
            "fb": self.fb,
            "fac2": self.fac2,
            "hit_rate": self.hit_rate,
            "bias": self.bias,
            "korrelation_r": self.korrelation_r,
        }


def berakna_cost732_matt(
    observerat: Sequence[float] | npt.NDArray[np.floating[Any]],
    modellerat: Sequence[float] | npt.NDArray[np.floating[Any]],
    *,
    rel_tol: float = COST732_HIT_RATE_REL_TOL,
    abs_tol: float = COST732_HIT_RATE_ABS_TOL,
) -> Cost732Matt:
    """Beräknar RMSE, NMSE, FB, FAC2 och hit rate q enligt COST 732 / Chang & Hanna (2004).

    * ``RMSE = sqrt(mean((P - O)^2))``
    * ``NMSE = mean((O - P)^2) / (mean(O) * mean(P))`` (``None`` om ``mean(O)*mean(P) <= 0``)
    * ``FB   = 2 * (mean(O) - mean(P)) / (mean(O) + mean(P))`` (``None`` om nämnaren ≈ 0)
    * ``FAC2 = andel punkter med 0,5 <= P / O <= 2,0`` (och ``P == 0`` då ``O == 0``)
    * ``q    = andel punkter med |P - O| <= max(rel_tol * |O|, abs_tol)``
    """
    obs = np.asarray(observerat, dtype=np.float64)
    pred = np.asarray(modellerat, dtype=np.float64)
    if obs.ndim != 1 or pred.ndim != 1 or obs.size != pred.size:
        raise ValueError("observerat och modellerat måste vara 1D-serier av samma längd.")
    if obs.size == 0:
        raise ValueError("Kan inte beräkna COST 732-mått på tom serie.")
    if not (np.all(np.isfinite(obs)) and np.all(np.isfinite(pred))):
        raise ValueError("observerat och modellerat får inte innehålla NaN eller Inf.")
    if rel_tol < 0.0 or abs_tol < 0.0 or not (math.isfinite(rel_tol) and math.isfinite(abs_tol)):
        raise ValueError("rel_tol och abs_tol måste vara ändliga och >= 0.")

    diff = pred - obs
    mse = float(np.mean(diff * diff))
    rmse = math.sqrt(mse)
    mean_o = float(np.mean(obs))
    mean_p = float(np.mean(pred))
    bias = mean_p - mean_o

    sum_means = mean_o + mean_p
    fb: float | None = (
        float(2.0 * (mean_o - mean_p) / sum_means) if abs(sum_means) > 1e-15 else None
    )
    prod_means = mean_o * mean_p
    nmse: float | None = float(mse / prod_means) if prod_means > 1e-15 else None

    noll_obs = obs == 0.0
    kvot = np.divide(pred, obs, out=np.full_like(pred, np.nan), where=~noll_obs)
    i_fac2 = np.where(noll_obs, pred == 0.0, (kvot >= 0.5) & (kvot <= 2.0))
    fac2 = float(np.mean(i_fac2))

    traff = np.abs(diff) <= np.maximum(rel_tol * np.abs(obs), abs_tol)
    hit_rate = float(np.mean(traff))

    std_o = float(np.std(obs))
    std_p = float(np.std(pred))
    if std_o > 1e-15 and std_p > 1e-15:
        korr_val = float(np.mean((obs - mean_o) * (pred - mean_p)) / (std_o * std_p))
        korr: float | None = max(-1.0, min(1.0, korr_val))
    else:
        korr = None

    return Cost732Matt(
        antal_punkter=int(obs.size),
        rmse=rmse,
        nmse=nmse,
        fb=fb,
        fac2=fac2,
        hit_rate=hit_rate,
        bias=bias,
        korrelation_r=korr,
    )


@dataclass(frozen=True)
class CedvalFaltJamforelse:
    """Resultat av fältjämförelse mot lokal CEDVAL A1-1-mätfil (CSV)."""

    csv_sokvag: str
    csv_sha256: str
    antal_rader_totalt: int
    antal_jamforda: int
    antal_utanfor_doman: int
    antal_i_solid: int
    u_norm: Cost732Matt
    v_norm: Cost732Matt
    w_norm: Cost732Matt
    hastighet_norm: Cost732Matt
    godkand_cost732: bool

    @property
    def rmse(self) -> float:
        """RMSE för normaliserad strömhastighet u_norm = u_x / U_ref."""
        return self.u_norm.rmse

    @property
    def nmse(self) -> float | None:
        """NMSE för u_norm."""
        return self.u_norm.nmse

    @property
    def fb(self) -> float | None:
        """Fractional bias (FB) för u_norm."""
        return self.u_norm.fb

    @property
    def fac2(self) -> float:
        """FAC2 för u_norm."""
        return self.u_norm.fac2

    @property
    def hit_rate(self) -> float:
        """COST 732 / VDI 3783 hit rate q för u_norm."""
        return self.u_norm.hit_rate

    def till_dict(self) -> dict[str, Any]:
        return {
            "csv_sokvag": self.csv_sokvag,
            "csv_sha256": self.csv_sha256,
            "antal_rader_totalt": self.antal_rader_totalt,
            "antal_jamforda": self.antal_jamforda,
            "antal_utanfor_doman": self.antal_utanfor_doman,
            "antal_i_solid": self.antal_i_solid,
            "godkand_cost732": self.godkand_cost732,
            "u_norm": self.u_norm.till_dict(),
            "v_norm": self.v_norm.till_dict(),
            "w_norm": self.w_norm.till_dict(),
            "hastighet_norm": self.hastighet_norm.till_dict(),
        }


def _las_cedval_csv(csv_sokvag: str | Path) -> tuple[Path, npt.NDArray[np.float64]]:
    """Läser lokal CEDVAL-CSV med kolumnerna x_m, y_m, z_m, u_norm, v_norm, w_norm."""
    sokvag = Path(csv_sokvag)
    if not sokvag.is_file():
        raise FileNotFoundError(
            f"Lokal CEDVAL A1-1-mätfil saknas: '{sokvag}'. "
            "Ladda ned officiella EWTL-mätdata lokalt innan fältjämförelse körs."
        )
    rader: list[list[float]] = []
    with sokvag.open("r", encoding="utf-8", newline="") as fh:
        lasare = csv.DictReader(fh)
        if lasare.fieldnames is None:
            raise ValueError(f"CEDVAL-CSV '{sokvag}' saknar kolumnrubriker.")
        rensade_rubriker = [k.strip() for k in lasare.fieldnames if k is not None]
        saknade = [kol for kol in _CSV_KOLUMNER if kol not in rensade_rubriker]
        if saknade:
            raise ValueError(
                f"CEDVAL-CSV '{sokvag}' saknar obligatoriska kolumner: {', '.join(saknade)}. "
                f"Förväntade: {', '.join(_CSV_KOLUMNER)}."
            )
        for radnr, rad in enumerate(lasare, start=2):
            normaliserad = {
                k.strip(): (v.strip() if isinstance(v, str) else "")
                for k, v in rad.items()
                if k is not None
            }
            if all(normaliserad.get(kol, "") == "" for kol in _CSV_KOLUMNER):
                continue
            try:
                varden = [float(normaliserad[kol]) for kol in _CSV_KOLUMNER]
            except ValueError as exc:
                raise ValueError(
                    f"Ogiltigt tal på rad {radnr} i CEDVAL-CSV '{sokvag}': {exc}"
                ) from exc
            if not all(math.isfinite(v) for v in varden):
                raise ValueError(f"Icke-ändligt tal (NaN/Inf) på rad {radnr} i '{sokvag}'.")
            rader.append(varden)

    if not rader:
        raise ValueError(f"CEDVAL-CSV '{sokvag}' innehåller inga datapunkter.")
    return sokvag, np.asarray(rader, dtype=np.float64)


def jamfor_mot_cedval_csv(
    resultat: CedvalResultat,
    csv_sokvag: str | Path,
    *,
    rel_tol: float = COST732_HIT_RATE_REL_TOL,
    abs_tol: float = COST732_HIT_RATE_ABS_TOL,
) -> CedvalFaltJamforelse:
    """Jämför modellerat vindfält mot lokalt nedladdad CEDVAL A1-1 CSV-mätfil.

    Förväntade kolumner (fullskala, origo i byggnadens bascentrum):
    ``x_m, y_m, z_m, u_norm, v_norm, w_norm`` där hastigheterna är normerade med
    ``U_ref``. Kastar ``FileNotFoundError`` om filen saknas och ``ValueError``
    vid ogiltigt format eller om inga punkter ligger i luftceller i domänen.
    """
    sokvag, data = _las_cedval_csv(csv_sokvag)
    doman = resultat.doman
    grid = doman.grid
    dx = doman.cellstorlek_m
    nx, ny, nz = grid.form

    x_min_dom = grid.origo_x_m
    x_max_dom = grid.origo_x_m + nx * dx
    y_min_dom = grid.origo_y_m
    y_max_dom = grid.origo_y_m + ny * dx
    z_min_dom = grid.origo_z_m
    z_max_dom = grid.origo_z_m + nz * dx

    px, py, pz = data[:, 0], data[:, 1], data[:, 2]
    inom_doman = (
        (px >= x_min_dom)
        & (px <= x_max_dom)
        & (py >= y_min_dom)
        & (py <= y_max_dom)
        & (pz >= z_min_dom)
        & (pz <= z_max_dom)
    )
    antal_utanfor = int(np.count_nonzero(~inom_doman))

    # Kontrollera om punkt faller i solid byggnadscell
    ix = np.clip(np.floor((px - x_min_dom) / dx).astype(np.int64), 0, nx - 1)
    iy = np.clip(np.floor((py - y_min_dom) / dx).astype(np.int64), 0, ny - 1)
    iz = np.clip(np.floor((pz - z_min_dom) / dx).astype(np.int64), 0, nz - 1)
    i_solid = inom_doman & grid.fast[ix, iy, iz]
    antal_i_solid = int(np.count_nonzero(i_solid))

    giltig = inom_doman & (~i_solid)
    antal_jamforda = int(np.count_nonzero(giltig))
    if antal_jamforda == 0:
        raise ValueError(
            f"Inga mätpunkter i '{sokvag}' ligger i luftceller inom beräkningsdomänen "
            f"(totalt {data.shape[0]} rader: {antal_utanfor} utanför domän, "
            f"{antal_i_solid} i byggnad)."
        )

    # Trilinjär interpolation i modellens 3D-vindfält (klampad inom halva randcellen)
    qx = np.clip(px[giltig], float(doman.x_m[0]), float(doman.x_m[-1]))
    qy = np.clip(py[giltig], float(doman.y_m[0]), float(doman.y_m[-1]))
    qz = np.clip(pz[giltig], float(doman.z_m[0]), float(doman.z_m[-1]))
    punkter = np.column_stack([qx, qy, qz])

    u_ref = doman.spec.u_ref_m_s
    u_norm_3d = resultat.vindfalt.u_m_s / u_ref
    pred_komp: list[npt.NDArray[np.float64]] = []
    for axel in range(3):
        interp = RegularGridInterpolator(
            (doman.x_m, doman.y_m, doman.z_m),
            u_norm_3d[axel],
            method="linear",
            bounds_error=False,
            fill_value=np.nan,
        )
        pred_komp.append(np.asarray(interp(punkter), dtype=np.float64))

    obs_u = data[giltig, 3]
    obs_v = data[giltig, 4]
    obs_w = data[giltig, 5]
    pred_u, pred_v, pred_w = pred_komp

    obs_hast = np.sqrt(obs_u * obs_u + obs_v * obs_v + obs_w * obs_w)
    pred_hast = np.sqrt(pred_u * pred_u + pred_v * pred_v + pred_w * pred_w)

    matt_u = berakna_cost732_matt(obs_u, pred_u, rel_tol=rel_tol, abs_tol=abs_tol)
    matt_v = berakna_cost732_matt(obs_v, pred_v, rel_tol=rel_tol, abs_tol=abs_tol)
    matt_w = berakna_cost732_matt(obs_w, pred_w, rel_tol=rel_tol, abs_tol=abs_tol)
    matt_hast = berakna_cost732_matt(obs_hast, pred_hast, rel_tol=rel_tol, abs_tol=abs_tol)

    godkand = (
        matt_u.fac2 >= COST732_FAC2_GRANS
        and matt_u.hit_rate >= COST732_HIT_RATE_GRANS
        and (matt_u.fb is None or abs(matt_u.fb) <= COST732_FB_ABS_GRANS)
        and (matt_u.nmse is None or matt_u.nmse <= COST732_NMSE_GRANS)
    )

    return CedvalFaltJamforelse(
        csv_sokvag=str(sokvag),
        csv_sha256=sha256_fil(sokvag),
        antal_rader_totalt=int(data.shape[0]),
        antal_jamforda=antal_jamforda,
        antal_utanfor_doman=antal_utanfor,
        antal_i_solid=antal_i_solid,
        u_norm=matt_u,
        v_norm=matt_v,
        w_norm=matt_w,
        hastighet_norm=matt_hast,
        godkand_cost732=godkand,
    )


# --------------------------------------------------------------------------------------
# K37-kompatibel JSON och Markdown-rapport
# --------------------------------------------------------------------------------------


def _maskininfo_k37() -> dict[str, Any]:
    """Bygger K37-kompatibel `maskin`-ordbok utan externa GPL-beroenden."""
    cpu = platform.processor() or platform.machine() or "okänd"
    minne_mb: float | None = None
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            for rad in fh:
                if rad.lower().startswith("model name"):
                    cpu = rad.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for rad in fh:
                if rad.startswith("MemTotal:"):
                    minne_mb = round(int(rad.split()[1]) / 1024.0, 0)
                    break
    except (OSError, ValueError, IndexError):
        pass
    try:
        karnor_tillg: int | None = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        karnor_tillg = None
    return {
        "cpu": cpu,
        "karnor": os.cpu_count(),
        "karnor_tillgangliga": karnor_tillg,
        "minne_totalt_mb": minne_mb,
        "os": platform.platform(),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "rust_karna": {
            "tillganglig": False,
            "sokvag": None,
            "anvands_i_svit": False,
        },
        "gpu": None,
        "webblasare": None,
    }


def cedval_till_dict(
    resultat: CedvalResultat,
    falt: CedvalFaltJamforelse | None = None,
    *,
    skapad_utc: str | None = None,
) -> dict[str, Any]:
    """Serialiserar CEDVAL A1-1-resultat till ett K37-kompatibelt JSON-dokument.

    Dokumentet följer ``floden-benchmark/v1`` (kontrakt K37) så att det kan
    kontrolleras och jämföras med samma verktyg som övriga benchmarkkörningar.
    Inga ``NaN``- eller ``Inf``-värden förekommer.
    """
    spec = resultat.doman.spec
    nx, ny, nz = resultat.doman.grid.form
    jamford_data = falt is not None

    avvikelse: dict[str, Any] | None
    if falt is not None:
        avvikelse = {
            "varde": falt.u_norm.rmse,
            "enhet": "u/U_ref (RMSE)",
            "grans": None,
            "godkand": falt.godkand_cost732,
            "beskrivning": (
                "RMSE för normaliserad strömhastighet u_x/U_ref mot lokal CEDVAL A1-1-mätfil "
                "(COST 732)"
            ),
            "matt": {
                "rmse_u": falt.u_norm.rmse,
                "nmse_u": falt.u_norm.nmse,
                "fb_u": falt.u_norm.fb,
                "fac2_u": falt.u_norm.fac2,
                "hit_rate_u": falt.u_norm.hit_rate,
                "rmse_hastighet": falt.hastighet_norm.rmse,
                "fac2_hastighet": falt.hastighet_norm.fac2,
                "hit_rate_hastighet": falt.hastighet_norm.hit_rate,
                "antal_punkter": falt.antal_jamforda,
            },
        }
        anmarkning = (
            f"CEDVAL A1-1 ({resultat.losare}, dx={resultat.cellstorlek_m:g} m), "
            f"metodstatus='{resultat.metodstatus}'. Jämförd mot lokal mätfil "
            f"'{falt.csv_sokvag}' ({falt.antal_jamforda} punkter)."
        )
    else:
        avvikelse = None
        anmarkning = (
            f"CEDVAL A1-1 ({resultat.losare}, dx={resultat.cellstorlek_m:g} m), "
            f"metodstatus='{resultat.metodstatus}'. Ingen lokal vindtunnel-CSV "
            "tillhandahållen (jamford_mot_vindtunnel_data=False); endast "
            "geometrisk och numerisk fysikdiagnostik."
        )

    extra: dict[str, Any] = {
        "cedval_kalla": CEDVAL_KALLA,
        "metodstatus": resultat.metodstatus,
        "jamford_mot_vindtunnel_data": jamford_data,
        "losare": resultat.losare,
        "cellstorlek_m": resultat.cellstorlek_m,
        "rutnat_form": [nx, ny, nz],
        "antal_celler_totalt": resultat.doman.antal_celler_totalt,
        "antal_solidceller": resultat.doman.antal_solidceller,
        "antal_luftceller": resultat.doman.antal_luftceller,
        "blockeringsgrad": resultat.doman.faktisk_blockeringsgrad,
        "max_divergens_1_s": resultat.max_divergens_1_s,
        "rms_divergens_1_s": resultat.rms_divergens_1_s,
        "projektion_divergens_rel": resultat.projektion_divergens_rel,
        "max_hastighet_i_solid_m_s": resultat.max_hastighet_i_solid_m_s,
        "inloppsprofil_max_fel_m_s": resultat.inloppsprofil_max_fel_m_s,
        "inloppsprofil_rel_fel": resultat.inloppsprofil_rel_fel,
        "uppstroms_dampning_norm": resultat.uppstroms_dampning_norm,
        "uppstroms_inlopps_mark_norm": resultat.uppstroms_inlopps_mark_norm,
        "har_recirkulationszon": resultat.har_recirkulationszon,
        "ateranslutning_x_r_m": resultat.ateranslutning_x_r_m,
        "ateranslutning_x_r_over_h": resultat.ateranslutning_x_r_over_h,
        "ateranslutning_inom_litteratur": resultat.ateranslutning_inom_litteratur,
        "litteratur_x_r_over_h_intervall": [
            CEDVAL_XR_OVER_H_LITTERATUR_MIN,
            CEDVAL_XR_OVER_H_LITTERATUR_MAX,
        ],
        "min_u_x_la_norm": resultat.min_u_x_la_norm,
        "cedval_spec": {
            "langd_x_m": spec.langd_x_m,
            "bredd_y_m": spec.bredd_y_m,
            "hojd_h_m": spec.hojd_h_m,
            "skrovlighet_z0_m": spec.skrovlighet_z0_m,
            "u_ref_m_s": spec.u_ref_m_s,
            "z_ref_m": spec.z_ref_m,
            "kappa": spec.kappa,
            "u_star_m_s": spec.u_star_m_s,
            "modellskala": spec.modellskala,
            "uppstroms_m": spec.uppstroms_m,
            "nedstroms_m": spec.nedstroms_m,
            "sidomarginal_m": spec.sidomarginal_m,
            "domanhojd_m": spec.domanhojd_m,
        },
        "anmarkningar": list(resultat.anmarkningar),
        "faltjamforelse": None if falt is None else falt.till_dict(),
    }

    fall_post: dict[str, Any] = {
        "id": CEDVAL_FALL_ID,
        "namn": f"CEDVAL A1-1 ({resultat.losare}, dx={resultat.cellstorlek_m:g} m)",
        "status": "ok",
        "tid_s": {
            "median": resultat.kortid_s,
            "min": resultat.kortid_s,
            "max": resultat.kortid_s,
            "n": 1,
            "alla": [resultat.kortid_s],
        },
        "minne": {},
        "avvikelse": avvikelse,
        "extra": extra,
    }

    hoppad_skal = "Fristående CEDVAL A1-1-valideringskörning (bara luft)."
    return {
        "format": K37_FORMAT,
        "kalla": "server",
        "skapad_utc": skapad_utc or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kodversion": resultat.kodversion or "okand",
        "maskin": _maskininfo_k37(),
        "installning": {
            "uppvarmning": 0,
            "upprepningar": 1,
            "tidsmatt": "enstaka körning, time.perf_counter",
            "minnesmatt": "ej mätt i fristående CEDVAL-körning",
        },
        "anmarkning": anmarkning,
        "delar": {
            "skyfall": {"status": "hoppas_over", "skal": hoppad_skal, "fall": []},
            "dagsljus": {"status": "hoppas_over", "skal": hoppad_skal, "fall": []},
            "luft": {"status": "kord", "skal": None, "fall": [fall_post]},
            "buller": {"status": "hoppas_over", "skal": hoppad_skal, "fall": []},
        },
    }


def _fmt_tal(varde: float | None, dec: int = 4) -> str:
    if varde is None:
        return "–"
    if varde != 0.0 and (abs(varde) < 1e-3 or abs(varde) >= 1e5):
        return f"{varde:.2e}"
    return f"{varde:.{dec}f}"


def formatera_cedval_rapport_md(
    resultat: CedvalResultat,
    falt: CedvalFaltJamforelse | None = None,
) -> str:
    """Formaterar en reproducerbar Markdown-rapport för CEDVAL A1-1-validering."""
    spec = resultat.doman.spec
    nx, ny, nz = resultat.doman.grid.form
    jamford = falt is not None

    jamford_text = "Ja" if jamford else "Nej (`jamford_mot_vindtunnel_data = False`)"
    skala = spec.modellskala
    xr_intervall = f"{CEDVAL_XR_OVER_H_LITTERATUR_MIN:g}–{CEDVAL_XR_OVER_H_LITTERATUR_MAX:g}"
    inlopp_fel_str = (
        f"{_fmt_tal(resultat.inloppsprofil_max_fel_m_s)} "
        f"({100.0 * resultat.inloppsprofil_rel_fel:.3f} %)"
    )
    uppstroms_str = (
        f"{resultat.uppstroms_dampning_norm:.4f} "
        f"(inlopp: {resultat.uppstroms_inlopps_mark_norm:.4f})"
    )
    recirk_str = "Ja" if resultat.har_recirkulationszon else "Nej"
    xr_m_str = _fmt_tal(resultat.ateranslutning_x_r_m, dec=2)
    xr_h_str = _fmt_tal(resultat.ateranslutning_x_r_over_h, dec=3)
    proj_div_str = _fmt_tal(resultat.projektion_divergens_rel)
    solid_str = _fmt_tal(resultat.max_hastighet_i_solid_m_s)

    rader: list[str] = [
        f"# CEDVAL A1-1 – Valideringsrapport ({resultat.losare})",
        "",
        f"- **Metodstatus:** `{resultat.metodstatus}`",
        f"- **Jämförd mot lokala vindtunneldata:** {jamford_text}",
        f"- **Kodversion:** `{resultat.kodversion}`",
        f"- **Referensgeometri (publicerad):** {CEDVAL_KALLA}",
        "",
        "## 1. Publicerad geometri och inloppsprofil",
        "",
        "| Parameter | Värde | Enhet | Källa / anmärkning |",
        "| --- | ---: | --- | --- |",
        f"| Byggnadslängd `L_x` | {spec.langd_x_m:g} | m | "
        f"Publicerad CEDVAL A1-1 (1:{skala:g} -> {spec.langd_x_m / skala:.3f} m) |",
        f"| Byggnadsbredd `L_y` | {spec.bredd_y_m:g} | m | "
        f"Publicerad CEDVAL A1-1 (1:{skala:g} -> {spec.bredd_y_m / skala:.3f} m) |",
        f"| Byggnadshöjd `H` | {spec.hojd_h_m:g} | m | "
        f"Publicerad CEDVAL A1-1 (1:{skala:g} -> {spec.hojd_h_m / skala:.3f} m) |",
        f"| Skrovlighetslängd `z_0` | {spec.skrovlighet_z0_m:g} | m | "
        f"Neutral ABL-profil (modellskala {spec.skrovlighet_z0_m / skala:.4f} m) |",
        f"| Referenshastighet `U_ref` | {spec.u_ref_m_s:g} | m/s | "
        f"Vid referenshöjd `z_ref = {spec.z_ref_m:g} m` |",
        f"| Friktionshastighet `u_*` | {spec.u_star_m_s:.4f} | m/s | "
        f"Analytisk med `κ = {spec.kappa:g}` |",
        f"| Litteraturintervall `X_R / H` | {xr_intervall} | – | "
        "Publicerat intervall i litteraturen (COST 732 / EWTL) |",
        "",
        "## 2. Uppmätta resultat från Flodens 3D-vindlösare",
        "",
        "| Mått | Uppmätt värde | Enhet |",
        "| --- | ---: | --- |",
        f"| Lösare | `{resultat.losare}` | – |",
        f"| Cellstorlek `dx` | {resultat.cellstorlek_m:g} | m |",
        f"| Rutnät (`nx × ny × nz`) | `{nx} × {ny} × {nz}` "
        f"({resultat.doman.antal_celler_totalt:,} celler) | st |",
        f"| Solidceller (byggnad) | {resultat.doman.antal_solidceller:,} | st |",
        f"| Blockeringsgrad | {100.0 * resultat.doman.faktisk_blockeringsgrad:.2f} | % |",
        f"| Körtid | {resultat.kortid_s:.3f} | s |",
        f"| Max hastighet i solid (no-slip) | {solid_str} | m/s |",
        f"| Max inre divergens `|∇·u|` | {_fmt_tal(resultat.max_divergens_1_s)} | 1/s |",
        f"| RMS inre divergens `|∇·u|` | {_fmt_tal(resultat.rms_divergens_1_s)} | 1/s |",
        f"| Relativ ytflödesdivergens (projektion) | {proj_div_str} | – |",
        f"| Inloppsprofil maxfel | {inlopp_fel_str} | m/s |",
        f"| Uppströms dämpning vid mark (`u_x / U_ref`) | {uppstroms_str} | – |",
        f"| Minsta `u_x / U_ref` i lä (centrumlinje, mark) | {resultat.min_u_x_la_norm:.4f} | – |",
        f"| Recirkulationszon (`u_x < 0` i lä) | {recirk_str} | – |",
        f"| Återanslutningslängd `X_R` | {xr_m_str} | m |",
        f"| Normaliserad återanslutning `X_R / H` | {xr_h_str} | – |",
        "",
        "## 3. Fältjämförelse mot lokala CEDVAL A1-1-mätdata (COST 732)",
        "",
    ]

    if falt is None:
        rader.extend(
            [
                "Ingen lokal CEDVAL A1-1-mätfil angavs (`--cedval-csv`). Inga experimentella "
                "vindtunneldata finns incheckade i repot och inga mätvärden uppskattas eller "
                "hittas på.",
                "",
                "För fältjämförelse: ladda ned officiella CEDVAL A1-1-data från Universität "
                "Hamburg (EWTL), konvertera till fullskala med kolumnerna "
                "`x_m,y_m,z_m,u_norm,v_norm,w_norm` och kör:",
                "",
                "```bash",
                "python tools/validera_cedval.py --losare lbm --cellstorlek-m 2.0 "
                "--cedval-csv /sokvag/till/cedval_a11.csv",
                "```",
            ]
        )
    else:
        rader.extend(
            [
                f"- **Mätfil:** `{falt.csv_sokvag}` (SHA-256: `{falt.csv_sha256[:16]}…`)",
                f"- **Punkter:** {falt.antal_jamforda} jämförda av {falt.antal_rader_totalt} "
                f"({falt.antal_utanfor_doman} utanför domän, {falt.antal_i_solid} i solid byggnad)",
                f"- **COST 732 acceptans (`u_norm`):** "
                f"{'Godkänd' if falt.godkand_cost732 else 'Ej godkänd'} "
                f"(krav: FAC2 ≥ {COST732_FAC2_GRANS:g}, q ≥ {COST732_HIT_RATE_GRANS:g}, "
                f"|FB| ≤ {COST732_FB_ABS_GRANS:g}, NMSE ≤ {COST732_NMSE_GRANS:g})",
                "",
                "| Komponent | RMSE | NMSE | FB | FAC2 | Hit rate `q` | Bias | `r` |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for etikett, m in (
            ("`u_norm` (`u_x/U_ref`)", falt.u_norm),
            ("`v_norm` (`u_y/U_ref`)", falt.v_norm),
            ("`w_norm` (`u_z/U_ref`)", falt.w_norm),
            ("`hastighet_norm` (`|u|/U_ref`)", falt.hastighet_norm),
        ):
            rader.append(
                f"| {etikett} | {_fmt_tal(m.rmse)} | {_fmt_tal(m.nmse)} | {_fmt_tal(m.fb)} | "
                f"{_fmt_tal(m.fac2)} | {_fmt_tal(m.hit_rate)} | {_fmt_tal(m.bias)} | "
                f"{_fmt_tal(m.korrelation_r)} |"
            )

    if resultat.anmarkningar:
        rader.extend(["", "## 4. Anmärkningar och begränsningar", ""])
        for anm in resultat.anmarkningar:
            rader.append(f"- {anm}")

    rader.append("")
    return "\n".join(rader)
