# SPDX-License-Identifier: MIT
"""Gemensamma datatyper för luftdelen (kontrakt KL1/v1, `docs/LUFT.md`).

Alla moduler i `luft` bygger mot dessa typer. Ändra dem inte utan att höja
kontraktsversionen och skriva varför i `docs/BESLUT.md`.

Konventioner
------------
* Rutnätet är likformigt med cellstorlek `dx_m` i alla tre riktningar.
* Index `(i, j, k)` = (x, y, z). Axel x pekar *medvinds*: inflödet ligger vid
  `i = 0`. Rutnätet är alltså roterat så att vinden alltid blåser i +x.
  Vilken meteorologisk vindriktning det motsvarar står i `vindriktning_grad`.
* `origo_*` är koordinaten för hörnet (lägsta x, y, z) av cell (0, 0, 0) i det
  *roterade* systemet; `rotation_centrum_x/y_m` (SWEREF 99 18 00) och
  `vindriktning_grad` räcker för att transformera tillbaka.
* Marken ligger under `k = 0`. Celler där `fast` är sant är byggnad eller
  terräng och har ingen luft.
* Enheter i SI. Halter i g/m³, utsläpp i g/s.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

KONTRAKT_KL1 = "KL1/v1"

BoolArray = npt.NDArray[np.bool_]
FloatArray = npt.NDArray[np.floating[Any]]


class LuftFel(Exception):
    """Fel i luftdelen. Kastas hellre än att ett osäkert resultat lämnas ut."""


@dataclass(frozen=True)
class Voxelgrid:
    """Stadens geometri som tredimensionellt rutnät för en vindriktning."""

    dx_m: float
    fast: BoolArray  # (nx, ny, nz), True = byggnad/terräng
    lad_m2_m3: FloatArray  # (nx, ny, nz), bladytetäthet, 0 = ingen vegetation
    vindriktning_grad: float = 270.0  # meteorologisk riktning vinden kommer IFRÅN
    origo_x_m: float = 0.0
    origo_y_m: float = 0.0
    origo_z_m: float = 0.0  # RH 2000 för markplanets nivå
    rotation_centrum_x_m: float = 0.0
    rotation_centrum_y_m: float = 0.0

    def __post_init__(self) -> None:
        if self.dx_m <= 0:
            raise LuftFel("dx_m måste vara positiv.")
        if self.fast.ndim != 3:
            raise LuftFel("fast måste vara tredimensionell (nx, ny, nz).")
        if self.fast.dtype != np.bool_:
            raise LuftFel("fast måste ha dtype bool.")
        if self.lad_m2_m3.shape != self.fast.shape:
            raise LuftFel("lad_m2_m3 måste ha samma form som fast.")
        if np.any(self.lad_m2_m3 < 0):
            raise LuftFel("lad_m2_m3 får inte vara negativ.")

    @property
    def form(self) -> tuple[int, int, int]:
        nx, ny, nz = self.fast.shape
        return int(nx), int(ny), int(nz)

    @property
    def luft(self) -> BoolArray:
        result: BoolArray = ~self.fast
        return result

    def cellcentrum_z_m(self) -> FloatArray:
        """Höjd över markplanet för cellcentrum i varje lager (nz,)."""
        nz = self.form[2]
        z: FloatArray = (np.arange(nz, dtype=np.float64) + 0.5) * self.dx_m
        return z


@dataclass(frozen=True)
class Inflode:
    """Neutral logaritmisk vindprofil vid inflödet."""

    u_ref_m_s: float  # vindhastighet på referenshöjden
    z_ref_m: float = 10.0
    z0_m: float = 0.5  # råhetslängd uppströms
    turbulensintensitet: float = 0.0  # 0 = inga pålagda störningar

    def __post_init__(self) -> None:
        if self.u_ref_m_s <= 0 or self.z_ref_m <= 0 or self.z0_m <= 0:
            raise LuftFel("u_ref_m_s, z_ref_m och z0_m måste vara positiva.")
        if self.z_ref_m <= self.z0_m:
            raise LuftFel("z_ref_m måste vara större än z0_m.")

    def profil_m_s(self, z_m: FloatArray) -> FloatArray:
        """u(z) = u_ref · ln((z+z0)/z0) / ln((z_ref+z0)/z0)."""
        z = np.asarray(z_m, dtype=np.float64)
        u: FloatArray = (
            self.u_ref_m_s
            * np.log((z + self.z0_m) / self.z0_m)
            / np.log((self.z_ref_m + self.z0_m) / self.z0_m)
        )
        return u


@dataclass(frozen=True)
class Vindfalt:
    """Tidsmedelvärdesbildat vindfält från en LES-körning (en vindriktning).

    Fältet är normerat mot `inflode.u_ref_m_s`. Vid neutral skiktning skalar
    hastigheter och turbulent viskositet linjärt med referensvinden, så samma
    fält används för alla vindhastigheter i samma riktning (se `docs/LUFT.md`).
    """

    grid: Voxelgrid
    inflode: Inflode
    u_m_s: FloatArray  # (3, nx, ny, nz), medelhastighet, 0 i fasta celler
    nu_t_m2_s: FloatArray  # (nx, ny, nz), medel turbulent viskositet (SGS + upplöst)
    medelperiod_s: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        nx, ny, nz = self.grid.form
        if self.u_m_s.shape != (3, nx, ny, nz):
            raise LuftFel(f"u_m_s ska ha formen (3, {nx}, {ny}, {nz}).")
        if self.nu_t_m2_s.shape != (nx, ny, nz):
            raise LuftFel(f"nu_t_m2_s ska ha formen ({nx}, {ny}, {nz}).")
        if not np.all(np.isfinite(self.u_m_s)) or not np.all(np.isfinite(self.nu_t_m2_s)):
            raise LuftFel("Vindfältet innehåller NaN/Inf – körningen har divergerat.")
        if np.any(self.nu_t_m2_s < 0):
            raise LuftFel("nu_t_m2_s får inte vara negativ.")

    def skalat(self, u_ref_m_s: float) -> Vindfalt:
        """Samma riktning, ny referensvind (neutral skalning)."""
        if u_ref_m_s <= 0:
            raise LuftFel("u_ref_m_s måste vara positiv.")
        f = u_ref_m_s / self.inflode.u_ref_m_s
        nytt_inflode = Inflode(
            u_ref_m_s=u_ref_m_s,
            z_ref_m=self.inflode.z_ref_m,
            z0_m=self.inflode.z0_m,
            turbulensintensitet=self.inflode.turbulensintensitet,
        )
        return Vindfalt(
            grid=self.grid,
            inflode=nytt_inflode,
            u_m_s=self.u_m_s * f,
            nu_t_m2_s=self.nu_t_m2_s * f,
            medelperiod_s=self.medelperiod_s / f,
            metadata={**self.metadata, "skalat_fran_u_ref_m_s": self.inflode.u_ref_m_s},
        )
