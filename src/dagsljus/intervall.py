"""Modern matematik för dagsljus: Dozier–Frews O(N) horisont och monotona höjdintervall.

1. **Dozier–Frews 1D övre konvexa hölje (`horisont_1d_konvext_holje`):**
   Beräknar exakta horisontvinklar längs en 1D-profil i strikt O(N) tid (oberoende av
   sökradie) med en monoton stack, i stället för O(N²) / O(N · r_max).
2. **Monoton intervallanalys för byggnadshöjdsosäkerhet (`kor_monoton_svf_intervall`):**
   Himmelsandelen (SVF) är strikt antiton (monotont avtagande) i byggnadshöjderna.
   Två körningar (z_bygg - σ och z_bygg + σ) ger därför en matematiskt garanterad
   inneslutning [SVF_min, SVF_max] för varje markcell utan Monte Carlo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from dagsljus.horisont import (
    Falt,
    Hojdfalt,
    HorisontFel,
    horisont_riktning_ruta,
    riktningar_rad,
)
from dagsljus.matt import svf_fran_horisont


@dataclass(frozen=True)
class SvfIntervallResultat:
    """Monotont garanterat himmelsandelsintervall [svf_min, svf_max] per markcell."""

    svf_min: Falt
    svf_max: Falt
    bredd_max: float
    osaker_troskel_mask: NDArray[np.bool_] | None
    antal_osakra_celler: int


def horisont_1d_konvext_holje(
    z_m: NDArray[np.float64] | NDArray[np.float32],
    dx_m: float,
    *,
    riktning: int = 1,
) -> Falt:
    """Dozier–Frews O(N)-algoritm (övre konvext hölje) för 1D-horisontvinklar (rad).

    För varje punkt i beräknas exakt:
        h[i] = max(0, max_{j i riktning} atan((z[j] - z[i]) / (|j - i| * dx_m)))

    `riktning = +1` ser mot ökande index (höger), `riktning = -1` mot minskande index (vänster).
    Tidskomplexitet: strikt O(N) — varje punkt läggs på och tas av stacken högst en gång.
    """
    if not (math.isfinite(dx_m) and dx_m > 0.0):
        raise HorisontFel(f"dx_m måste vara ändligt och > 0, fick {dx_m}.")
    if riktning not in (1, -1):
        raise HorisontFel(f"riktning måste vara +1 eller -1, fick {riktning}.")

    z = np.asarray(z_m, dtype=np.float64)
    if z.ndim != 1:
        raise HorisontFel(f"z_m måste vara 1D, fick form {z.shape}.")
    n = int(z.size)
    if n == 0:
        return np.zeros(0, dtype=np.float64)
    if not np.all(np.isfinite(z)):
        raise HorisontFel("z_m innehåller NaN eller inf.")

    if riktning == -1:
        return horisont_1d_konvext_holje(z[::-1], dx_m, riktning=1)[::-1].copy()

    # Skanna från höger till vänster med monoton stack som håller det övre konvexa höljet.
    # För punkt i: toppen på stacken j1 ger lägre lutning än näst översta j2 omm
    # (z[j1] - z[i]) * (j2 - i) <= (z[j2] - z[i]) * (j1 - i); då kan j1 aldrig vara
    # horisont för i eller någon punkt till vänster om i -> poppa j1!
    h_rad = np.zeros(n, dtype=np.float64)
    stack_idx: list[int] = [n - 1]
    inv_dx = 1.0 / float(dx_m)

    for i in range(n - 2, -1, -1):
        zi = float(z[i])
        while len(stack_idx) >= 2:
            j1 = stack_idx[-1]
            j2 = stack_idx[-2]
            if (float(z[j1]) - zi) * (j2 - i) <= (float(z[j2]) - zi) * (j1 - i):
                stack_idx.pop()
            else:
                break
        j_best = stack_idx[-1]
        dz = float(z[j_best]) - zi
        if dz > 0.0:
            h_rad[i] = math.atan(dz * inv_dx / float(j_best - i))
        stack_idx.append(i)

    return h_rad


def kor_monoton_svf_intervall(
    mark_z_m: NDArray[np.float64] | NDArray[np.float32],
    byggnad_hojd_m: NDArray[np.float64] | NDArray[np.float32],
    osakerhet_byggnad_m: float | NDArray[np.float64],
    *,
    cell_m: float,
    x_min_m: float = 0.0,
    y_max_m: float = 0.0,
    antal_riktningar: int = 36,
    max_avstand_m: float = 200.0,
    svf_troskel: float | None = None,
) -> SvfIntervallResultat:
    """Beräknar monotont garanterat SVF-intervall [svf_min, svf_max] vid höjdosäkerhet ±σ.

    Byggnadsceller definieras som celler med `byggnad_hojd_m > 0`. Eftersom SVF är strikt
    antiton i byggnadshöjderna ger två körningar (`z_bygg - σ` och `z_bygg + σ`) garanterade
    övre och undre gränser för alla markceller.
    """
    mark = np.asarray(mark_z_m, dtype=np.float64)
    bygg = np.asarray(byggnad_hojd_m, dtype=np.float64)
    if mark.ndim != 2 or bygg.shape != mark.shape:
        raise HorisontFel(
            "mark_z_m och byggnad_hojd_m måste vara 2D med samma form, "
            f"fick {mark.shape} / {bygg.shape}."
        )
    sigma = np.broadcast_to(np.asarray(osakerhet_byggnad_m, dtype=np.float64), mark.shape)
    if np.any(sigma < 0.0) or not np.all(np.isfinite(sigma)):
        raise HorisontFel("osakerhet_byggnad_m måste vara ändligt och >= 0.")

    ar_bygg = bygg > 0.0
    z_lo = mark + np.where(ar_bygg, np.maximum(0.0, bygg - sigma), 0.0)
    z_hi = mark + np.where(ar_bygg, bygg + sigma, 0.0)

    hf_lo = Hojdfalt(z_m=z_lo, cell_m=cell_m, x_min_m=x_min_m, y_max_m=y_max_m)
    hf_hi = Hojdfalt(z_m=z_hi, cell_m=cell_m, x_min_m=x_min_m, y_max_m=y_max_m)
    rikt = riktningar_rad(antal_riktningar)

    h_lo = np.stack(
        [horisont_riktning_ruta(hf_lo, float(phi), max_avstand_m)[0] for phi in rikt], axis=0
    )
    h_hi = np.stack(
        [horisont_riktning_ruta(hf_hi, float(phi), max_avstand_m)[0] for phi in rikt], axis=0
    )

    # Lägre hus -> högre SVF (svf_max); högre hus -> lägre SVF (svf_min)
    svf_max = svf_fran_horisont(h_lo, axel=0)
    svf_min = svf_fran_horisont(h_hi, axel=0)
    mark_mask = ~ar_bygg
    bredd = float(np.max(svf_max[mark_mask] - svf_min[mark_mask])) if np.any(mark_mask) else 0.0

    osaker_mask: NDArray[np.bool_] | None = None
    antal_osakra = 0
    if svf_troskel is not None:
        osaker_mask = mark_mask & (svf_min < float(svf_troskel)) & (svf_max >= float(svf_troskel))
        antal_osakra = int(np.count_nonzero(osaker_mask))

    return SvfIntervallResultat(
        svf_min=svf_min,
        svf_max=svf_max,
        bredd_max=bredd,
        osaker_troskel_mask=osaker_mask,
        antal_osakra_celler=antal_osakra,
    )
