# SPDX-License-Identifier: MIT
"""D3Q19 lattice Boltzmann-LES för vind runt byggnader (spår L, `docs/LUFT.md` 2.1).

Metod
-----
* **Gitter:** D3Q19, likformigt, `dx = 1` och `dt = 1` i gitterenheter, `c_s² = 1/3`.
  Fördelningarna lagras som avvikelse från vilotillståndet, `f̄_i = f_i − w_i`, i float32.
  Det ger betydligt bättre precision i float32 vid låga Machtal än att lagra `f_i`
  (värdena ligger kring 0 i stället för kring `w_i`).
* **Kollision:** reguljariserad BGK. Icke-jämviktsdelen ersätts med sin projektion på
  andra ordningens moment,
  `f_i^neq,reg = w_i/(2 c_s⁴) · Q_i:Π^neq`, `Q_i = c_i c_i − c_s² I`,
  `Π^neq = Σ c_i c_i (f_i − f_i^eq) + ½(uF + Fu)` (Guo-korrektion),
  och efter kollisionen `f_i = f_i^eq + (1 − 1/τ) f_i^neq,reg + S_i`.
* **LES:** Smagorinsky `ν_t = (C_s Δ)² |S|` med `|S| = √(2 S:S)` lokalt ur
  `S = −Π^neq / (2 ρ c_s² τ)`. Eftersom `τ = 3(ν₀ + ν_t) + ½` beror på `|S|`, som i sin tur
  beror på `τ`, löses andragradsekvationen exakt (Hou m.fl. 1996, Malaspinas & Sagaut 2012):
  `τ = ½ (τ₀ + √(τ₀² + 18 √2 (C_s Δ)² √(Π^neq:Π^neq) / ρ))`, `ν_t = (τ − τ₀)/3`.
  Inga finita differenser behövs; allt är lokalt i cellen.
* **Kraftterm:** Guo m.fl. (2002),
  `S_i = w_i (1 − 1/(2τ)) [3 (c_i − u)·F + 9 (c_i·u)(c_i·F)]`, `ρu = Σ c_i f_i + F/2`.
* **Vegetation:** dragkraft per volym `F = −ρ C_d · LAD · |U| U` (fysikaliska enheter, N/m³).
  Accelerationen `a = −C_d · LAD · |U| U` (m/s²) omräknas till gitterenheter med
  `a_lat = a · dt²/dx` och `U = u_lat · dx/dt`, vilket ger
  `a_lat = −C_d · LAD · dx · |u_lat| u_lat` – dvs. `α = C_d · LAD · dx` är dimensionslös
  och `F_lat = −ρ α |u| u`. Eftersom `u` beror på `F` (`u = u* + F/(2ρ)`) löses
  `u = u* − ½ α |u| u` exakt: `u = u* · 2 / (1 + √(1 + 2 α |u*|))`.
* **Randvillkor:** inflöde `i = 0` = jämvikt med `ρ = 1` och logaritmisk profil
  (`Inflode.profil_m_s`, normerad så att `u_lat = u_lattice` vid `z_ref`), med valfri
  störning; utflöde `i = nx−1` = noll gradient (kopia av `nx−2`); `y` periodisk;
  tak = friktionsfri spegling (halvvägs); mark under `k = 0` och alla `fast`-celler =
  halvvägs bounce-back.
* **Enheter:** `dt = dx · u_lattice / u_ref`. Hastighet `U = u_lat · u_ref / u_lattice`,
  viskositet `ν = ν_lat · dx² / dt`.

Turbulent viskositet i `Vindfalt`
---------------------------------
Transportmodellen (`docs/LUFT.md` 2.2) behöver en *total* turbulent viskositet för
medelfältet: det filtrerade SGS-bidraget plus den upplösta turbulensens blandning, som
försvinner ur tidsmedelvärdet men transporterar ämnen. Vi använder

    ν_t = ⟨ν_t,SGS⟩ + ν_t,res,   ν_t,res = C_μ^{1/4} · ℓ_m · √k_res,
    k_res = ½ tr(⟨u u⟩ − ⟨u⟩⟨u⟩),   ℓ_m = min(κ z, 0,1 H),

dvs. Prandtl–Kolmogorovs relation med blandningslängd (`C_μ = 0,09`, `κ = 0,41`,
`z` = cellcentrums höjd över marken, `H` = domänhöjden). Motivering: relationen kräver bara
`k`, som mäts direkt ur LES-statistiken (ingen `ε` behövs), och i ett neutralt
logaritmiskt skikt (`k = u*²/√C_μ`) ger den exakt `ν_t = κ z u*`, alltså den klassiska
ytskiktsviskositeten. Begränsningar: `ℓ_m` använder höjd över mark, inte avstånd till
närmaste fasad, och överskattar därför blandningen i djupa gaturum; taket `0,1 H` följer
Blackadars asymptotiska blandningslängd. Ansatsen är en v1-förenkling och bör jämföras mot
direkt uppmätt turbulent flöde `⟨u'c'⟩` när spridning valideras.

Begränsningar i v1
------------------
* Ingen väggmodell: med 1–2 m celler är väggskjuvningen underskattad vid byggnader.
* Inflödesstörningen är tids- och rumsligt filtrerat brus, inte en syntetisk
  turbulensgenerator med rätt spektrum.
* Utflöde med noll gradient kan bli instabilt vid kraftigt bakåtflöde vid randen;
  lägg gärna en buffert medvinds.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from luft.backend import hamta_backend, till_numpy
from luft.typer import KONTRAKT_KL1, FloatArray, Inflode, LuftFel, Vindfalt, Voxelgrid

# --------------------------------------------------------------------------------------
# D3Q19-konstanter
# --------------------------------------------------------------------------------------

C_D3Q19: np.ndarray[Any, np.dtype[np.int64]] = np.array(
    [
        [0, 0, 0],
        [1, 0, 0],
        [-1, 0, 0],
        [0, 1, 0],
        [0, -1, 0],
        [0, 0, 1],
        [0, 0, -1],
        [1, 1, 0],
        [-1, -1, 0],
        [1, -1, 0],
        [-1, 1, 0],
        [1, 0, 1],
        [-1, 0, -1],
        [1, 0, -1],
        [-1, 0, 1],
        [0, 1, 1],
        [0, -1, -1],
        [0, 1, -1],
        [0, -1, 1],
    ],
    dtype=np.int64,
)
W_D3Q19: np.ndarray[Any, np.dtype[np.float64]] = np.array(
    [1.0 / 3.0] + [1.0 / 18.0] * 6 + [1.0 / 36.0] * 12, dtype=np.float64
)


def _motsatt_index() -> list[int]:
    ut = []
    for c in C_D3Q19:
        ut.append(int(np.flatnonzero(np.all(-c == C_D3Q19, axis=1))[0]))
    return ut


def _spegel_z_index() -> list[int]:
    ut = []
    for c in C_D3Q19:
        m = np.array([c[0], c[1], -c[2]])
        ut.append(int(np.flatnonzero(np.all(m == C_D3Q19, axis=1))[0]))
    return ut


MOTSATT: list[int] = _motsatt_index()
SPEGEL_Z: list[int] = _spegel_z_index()

# Komponenter för symmetriska tensorer: xx, yy, zz, xy, xz, yz
_PAR = [(0, 0), (1, 1), (2, 2), (0, 1), (0, 2), (1, 2)]
_VIKT_PAR = np.array([1.0, 1.0, 1.0, 2.0, 2.0, 2.0])

C_MU = 0.09
KAPPA = 0.41
MACH_GRANS = 0.3


# --------------------------------------------------------------------------------------
# Parametrar
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LbmParametrar:
    """Parametrar för LES-körningen (`docs/LUFT.md` 3.1)."""

    u_lattice: float = 0.08  # gitterhastighet för u_ref
    smagorinsky_cs: float = 0.16
    nu_molekylar_m2_s: float = 1.5e-5
    cd_vegetation: float = 0.2
    upprampning_genomstromningar: float = 2.0  # tid före medelvärdesbildning, i domänlängder
    medel_genomstromningar: float = 2.0
    max_steg: int = 200_000
    # Tillägg utöver v1-gränssnittet (alla med standardvärden):
    medel_intervall_steg: int = 5  # prov till medelvärdet var n:e steg
    kontroll_intervall_steg: int = 50  # NaN/Mach-kontroll var n:e steg
    mach_max: float = MACH_GRANS
    storning_seed: int = 0  # seed för inflödesstörningen (deterministisk)
    blandningslangd_max_andel: float = 0.1  # ℓ_m ≤ andel · domänhöjd

    def __post_init__(self) -> None:
        if self.u_lattice <= 0:
            raise LuftFel("u_lattice måste vara positiv.")
        if self.u_lattice * math.sqrt(3.0) > self.mach_max:
            raise LuftFel(
                f"u_lattice={self.u_lattice} ger Mach {self.u_lattice * math.sqrt(3.0):.3f} "
                f"> {self.mach_max}. Välj u_lattice ≤ 0,1."
            )
        if self.smagorinsky_cs < 0 or self.nu_molekylar_m2_s < 0 or self.cd_vegetation < 0:
            raise LuftFel("smagorinsky_cs, nu_molekylar_m2_s och cd_vegetation får inte vara < 0.")
        if self.upprampning_genomstromningar < 0 or self.medel_genomstromningar <= 0:
            raise LuftFel("Upprampning måste vara ≥ 0 och medelperiod > 0 genomströmningar.")
        if min(self.max_steg, self.medel_intervall_steg, self.kontroll_intervall_steg) <= 0:
            raise LuftFel("max_steg och intervallen måste vara positiva.")


# --------------------------------------------------------------------------------------
# Kärna
# --------------------------------------------------------------------------------------


def _skifta_mask(
    fast: np.ndarray[Any, np.dtype[np.bool_]], c: np.ndarray[Any, Any], x_periodisk: bool
) -> np.ndarray[Any, np.dtype[np.bool_]]:
    """Returnerar `fast_ut(x − c)` med randutfyllnad.

    x: periodisk eller falskt utanför; y: periodisk; z: mark (sant) under k = 0 och
    kopia av översta lagret ovanför (taket speglar, så källan för en reflekterad
    population ligger i översta lagret).
    """
    px = "wrap" if x_periodisk else "constant"
    a = np.pad(fast, ((1, 1), (0, 0), (0, 0)), mode=px)  # type: ignore[call-overload]
    a = np.pad(a, ((0, 0), (1, 1), (0, 0)), mode="wrap")
    under = np.ones_like(a[:, :, :1])
    over = a[:, :, -1:]
    a = np.concatenate([under, a, over], axis=2)
    nx, ny, nz = fast.shape
    cx, cy, cz = (int(v) for v in c)
    ut: np.ndarray[Any, np.dtype[np.bool_]] = a[
        1 - cx : 1 - cx + nx, 1 - cy : 1 - cy + ny, 1 - cz : 1 - cz + nz
    ]
    return ut


class Lbm3d:
    """D3Q19 reguljariserad BGK-LES på ett rutnät, i gitterenheter.

    Lågnivåklass som `kor_les` bygger på; används direkt i testerna.

    Parametrar
    ----------
    fast:
        (nx, ny, nz) bool, fasta celler (bounce-back). Marken under k = 0 är alltid vägg.
    tau0:
        Molekylär relaxationstid `3 ν₀ + ½`.
    smagorinsky_cs:
        `C_s` (0 = laminär).
    xp:
        Array-modul (numpy eller cupy).
    alfa_vegetation:
        (nx, ny, nz) `C_d · LAD · dx`, eller None.
    acceleration_lat:
        Konstant volymkraft per massa (gitterenheter), t.ex. för kanalflöde.
    x_periodisk:
        Periodisk i x i stället för inflöde/utflöde.
    """

    def __init__(
        self,
        fast: np.ndarray[Any, np.dtype[np.bool_]],
        tau0: float,
        smagorinsky_cs: float,
        xp: Any,
        alfa_vegetation: FloatArray | None = None,
        acceleration_lat: tuple[float, float, float] = (0.0, 0.0, 0.0),
        x_periodisk: bool = False,
    ) -> None:
        if fast.ndim != 3:
            raise LuftFel("fast måste vara tredimensionell.")
        if tau0 <= 0.5:
            raise LuftFel(f"tau0={tau0} måste vara > 0,5.")
        self.xp = xp
        self.form: tuple[int, int, int] = (
            int(fast.shape[0]),
            int(fast.shape[1]),
            int(fast.shape[2]),
        )
        nx, ny, nz = self.form
        if nx < 3 or ny < 1 or nz < 2:
            raise LuftFel("Rutnätet är för litet (minst 3 × 1 × 2).")
        self.n = nx * ny * nz
        self.tau0 = float(tau0)
        self.cs2 = float(smagorinsky_cs) ** 2
        self.x_periodisk = x_periodisk
        f32 = np.float32

        self.c_np = C_D3Q19
        self.cmat = xp.asarray(C_D3Q19.astype(f32))  # (19, 3)
        self.cT = xp.asarray(C_D3Q19.T.astype(f32))  # (3, 19)
        self.w = xp.asarray(W_D3Q19.astype(f32))[:, None]  # (19, 1)
        h6 = np.zeros((6, 19))
        r6 = np.zeros((19, 6))
        for p, (a, b) in enumerate(_PAR):
            ccab = C_D3Q19[:, a] * C_D3Q19[:, b]
            h6[p] = ccab
            r6[:, p] = 4.5 * W_D3Q19 * (ccab - (1.0 / 3.0 if a == b else 0.0)) * _VIKT_PAR[p]
        self.h6 = xp.asarray(h6.astype(f32))
        self.r6 = xp.asarray(r6.astype(f32))

        self.fast_np = fast.astype(np.bool_)
        self.luft_d = xp.asarray(~self.fast_np.reshape(-1))
        self.fast_idx = xp.asarray(np.flatnonzero(self.fast_np.reshape(-1)))
        self.n_luft = int(np.count_nonzero(~self.fast_np))

        # Bounce-back: för varje riktning de luftceller vars källa x − c_i är fast.
        self.studs: list[tuple[int, int, Any]] = []
        for i in range(1, 19):
            kalla = _skifta_mask(self.fast_np, C_D3Q19[i], x_periodisk)
            m = kalla & ~self.fast_np
            if not x_periodisk:
                m[0] = False
                m[-1] = False
            idx = np.flatnonzero(m.reshape(-1))
            if idx.size:
                self.studs.append((i, MOTSATT[i], xp.asarray(idx)))
        self.tak_rikt = [i for i in range(19) if C_D3Q19[i, 2] == -1]

        self.alfa: Any = None
        if alfa_vegetation is not None and np.any(np.asarray(alfa_vegetation) > 0):
            a_np = np.asarray(alfa_vegetation, dtype=f32).reshape(-1)
            self.alfa = xp.asarray(a_np)
        g = np.asarray(acceleration_lat, dtype=f32)
        self.g: Any = xp.asarray(g)[:, None] if np.any(g != 0) else None

        self.f: Any = xp.zeros((19, nx, ny, nz), dtype=f32)  # f̄ = f − w
        self.inflode_f: Any = None  # (19, ny, nz)
        # Senaste makrostorheter (från kollisionen i senaste steget), platta (n,).
        self.rho: Any = xp.ones(self.n, dtype=f32)
        self.u: Any = xp.zeros((3, self.n), dtype=f32)
        self.nu_t: Any = xp.zeros(self.n, dtype=f32)
        self.tau: Any = xp.full(self.n, self.tau0, dtype=f32)

    # ---------------------------------------------------------------- hjälp

    def jamvikt(self, drho: Any, u: Any) -> Any:
        """f̄^eq = f^eq − w för platta fält `drho` (m,) och `u` (3, m)."""
        xp = self.xp
        cu = xp.tensordot(self.cmat, u, axes=(1, 0))
        usq = (u * u).sum(axis=0)
        rho = 1.0 + drho
        return self.w * (drho + rho * (3.0 * cu + 4.5 * cu * cu - 1.5 * usq))

    def initiera(self, u0: Any | None = None) -> None:
        """Sätter jämvikt med ρ = 1 och hastighet `u0` (3, nx, ny, nz), 0 i fasta celler."""
        xp = self.xp
        nx, ny, nz = self.form
        if u0 is None:
            self.f[...] = 0.0
            return
        u = xp.asarray(u0, dtype=np.float32).reshape(3, self.n) * self.luft_d
        self.f = self.jamvikt(xp.zeros(self.n, dtype=np.float32), u).reshape(19, nx, ny, nz)

    def satt_inflode(self, u_in: Any) -> None:
        """Sätter inflödets hastighet (3, ny, nz) i gitterenheter (jämvikt med ρ = 1)."""
        xp = self.xp
        _, ny, nz = self.form
        u = xp.asarray(u_in, dtype=np.float32).reshape(3, ny * nz)
        u = u * self.luft_d.reshape(self.form)[0].reshape(-1)
        self.inflode_f = self.jamvikt(xp.zeros(ny * nz, dtype=np.float32), u).reshape(19, ny, nz)

    def total_massa(self) -> float:
        """Total massa i luftcellerna (float64-summering)."""
        return float(self.xp.sum(self.f, dtype=self.xp.float64)) + float(self.n_luft)

    # ---------------------------------------------------------------- steg

    def _kollidera(self) -> Any:
        xp = self.xp
        f = self.f.reshape(19, self.n)
        drho = f.sum(axis=0)
        rho = 1.0 + drho
        m = xp.tensordot(self.cT, f, axes=(1, 0))  # (3, n)
        kraft: Any = None
        if self.g is not None:
            kraft = rho * self.g
            u = (m + 0.5 * kraft) / rho
        else:
            u = m / rho
        if self.alfa is not None:
            fart = xp.sqrt((u * u).sum(axis=0))
            u = u * (2.0 / (1.0 + xp.sqrt(1.0 + 2.0 * self.alfa * fart)))
            fart = xp.sqrt((u * u).sum(axis=0))
            fveg = -(rho * self.alfa * fart) * u
            kraft = fveg if kraft is None else kraft + fveg

        # Π^neq (6, n)
        pi = xp.tensordot(self.h6, f, axes=(1, 0))
        for p, (a, b) in enumerate(_PAR):
            pi[p] -= rho * u[a] * u[b]
            if a == b:
                pi[p] -= drho * (1.0 / 3.0)
            if kraft is not None:
                pi[p] += 0.5 * (u[a] * kraft[b] + u[b] * kraft[a])
        pp = (
            pi[0] * pi[0]
            + pi[1] * pi[1]
            + pi[2] * pi[2]
            + 2.0 * (pi[3] * pi[3] + pi[4] * pi[4] + pi[5] * pi[5])
        )
        if self.cs2 > 0:
            koef = 18.0 * math.sqrt(2.0) * self.cs2
            tau = 0.5 * (self.tau0 + xp.sqrt(self.tau0 * self.tau0 + koef * xp.sqrt(pp) / rho))
        else:
            tau = xp.full(self.n, self.tau0, dtype=np.float32)
        fneq = xp.tensordot(self.r6, pi, axes=(1, 0))
        ut = self.jamvikt(drho, u) + (1.0 - 1.0 / tau) * fneq
        if kraft is not None:
            cu = xp.tensordot(self.cmat, u, axes=(1, 0))
            cf = xp.tensordot(self.cmat, kraft, axes=(1, 0))
            uf = (u * kraft).sum(axis=0)
            ut += (self.w * (1.0 - 0.5 / tau)) * (3.0 * cf - 3.0 * uf + 9.0 * cu * cf)
        self.rho = rho
        self.u = u
        self.tau = tau
        self.nu_t = (tau - self.tau0) * (1.0 / 3.0)
        return ut.reshape(19, *self.form)

    def steg(self) -> None:
        """Ett tidssteg: kollision, strömning, randvillkor."""
        xp = self.xp
        fp = self._kollidera()
        fn = xp.empty_like(fp)
        for i in range(19):
            cx, cy, cz = (int(v) for v in self.c_np[i])
            if cx == 0 and cy == 0 and cz == 0:
                fn[i] = fp[i]
            else:
                fn[i] = xp.roll(fp[i], shift=(cx, cy, cz), axis=(0, 1, 2))
        # Tak: friktionsfri spegling (halvvägs). Population nedåt i översta lagret kommer
        # från spegelriktningen uppåt i cellen (x − cx, y − cy) i samma lager.
        for i in self.tak_rikt:
            cx, cy, _ = (int(v) for v in self.c_np[i])
            fn[i, :, :, -1] = xp.roll(fp[SPEGEL_Z[i], :, :, -1], shift=(cx, cy), axis=(0, 1))
        # Mark och fasta celler: halvvägs bounce-back.
        fnp = fn.reshape(19, self.n)
        fpp = fp.reshape(19, self.n)
        for i, o, idx in self.studs:
            fnp[i, idx] = fpp[o, idx]
        if self.fast_idx.size:
            fnp[:, self.fast_idx] = 0.0
        if not self.x_periodisk:
            fn[:, -1] = fn[:, -2]
            if self.inflode_f is not None:
                fn[:, 0] = self.inflode_f
        self.f = fn

    def makro(self) -> tuple[Any, Any]:
        """(ρ, u) i form (nx, ny, nz) och (3, nx, ny, nz) från senaste kollisionen."""
        return self.rho.reshape(self.form), self.u.reshape(3, *self.form)

    def max_mach(self) -> float:
        xp = self.xp
        usq = (self.u * self.u).sum(axis=0) * self.luft_d
        return float(xp.sqrt(xp.max(usq)) * math.sqrt(3.0))

    def ar_andlig(self) -> bool:
        return bool(self.xp.all(self.xp.isfinite(self.f)))


# --------------------------------------------------------------------------------------
# Inflödesstörning
# --------------------------------------------------------------------------------------


class _Storning:
    """Tids- och rumsligt filtrerat brus (AR(1) i tid, lådfilter i y–z), deterministiskt."""

    def __init__(self, ny: int, nz: int, korrelation_steg: float, seed: int) -> None:
        self.rng = np.random.default_rng(seed)
        self.a = math.exp(-1.0 / max(korrelation_steg, 1.0))
        self.b = math.sqrt(1.0 - self.a * self.a)
        self.eta = self._filtrerat((3, ny, nz))

    def _filtrerat(self, form: tuple[int, int, int]) -> np.ndarray[Any, np.dtype[np.float64]]:
        x = self.rng.standard_normal(form)
        for _ in range(3):
            x = (x + np.roll(x, 1, axis=1) + np.roll(x, -1, axis=1)) / 3.0
            xz = x.copy()
            xz[:, :, 1:-1] = (x[:, :, :-2] + x[:, :, 1:-1] + x[:, :, 2:]) / 3.0
            x = xz
        s = float(np.std(x))
        return x / s if s > 0 else x

    def nasta(self) -> np.ndarray[Any, np.dtype[np.float64]]:
        form = (int(self.eta.shape[0]), int(self.eta.shape[1]), int(self.eta.shape[2]))
        self.eta = self.a * self.eta + self.b * self._filtrerat(form)
        return self.eta


# --------------------------------------------------------------------------------------
# Huvudfunktion
# --------------------------------------------------------------------------------------


def kor_les(
    grid: Voxelgrid,
    inflode: Inflode,
    param: LbmParametrar = LbmParametrar(),  # noqa: B008 - fryst dataklass
    backend: str = "numpy",
    logg: Callable[[str], None] | None = None,
) -> Vindfalt:
    """Kör LBM-LES för en vindriktning och returnerar tidsmedelvärdesbildat vindfält.

    Körningen består av upprampning (`param.upprampning_genomstromningar` domänlängder,
    inflödet rampas upp från vila under första halvan) följd av medelvärdesbildning
    (`param.medel_genomstromningar`). En genomströmning är `nx · dx / u_ref` sekunder,
    dvs. `nx / u_lattice` steg.

    Kastar `LuftFel` om fältet blir NaN/Inf, om Machtalet överstiger `param.mach_max`
    eller om antalet steg överstiger `param.max_steg`.
    """
    xp = hamta_backend(backend)
    nx, ny, nz = grid.form
    dx = grid.dx_m
    ul = param.u_lattice
    dt_s = dx * ul / inflode.u_ref_m_s
    nu0_lat = param.nu_molekylar_m2_s * dt_s / dx**2
    tau0 = 3.0 * nu0_lat + 0.5

    z = grid.cellcentrum_z_m()
    u_in_z = ul * inflode.profil_m_s(z) / inflode.u_ref_m_s  # (nz,)
    mach_in = float(np.max(u_in_z)) * math.sqrt(3.0)
    if mach_in > param.mach_max:
        raise LuftFel(
            f"Inflödesprofilen når Mach {mach_in:.3f} > {param.mach_max} i domänens topp. "
            "Sänk u_lattice."
        )

    steg_per_genomstromning = nx / ul
    n_ramp = int(math.ceil(param.upprampning_genomstromningar * steg_per_genomstromning))
    n_medel = int(math.ceil(param.medel_genomstromningar * steg_per_genomstromning))
    n_tot = n_ramp + n_medel
    if n_tot > param.max_steg:
        raise LuftFel(f"Körningen kräver {n_tot} steg > max_steg={param.max_steg}.")

    alfa = param.cd_vegetation * grid.lad_m2_m3 * dx
    losare = Lbm3d(
        grid.fast,
        tau0=tau0,
        smagorinsky_cs=param.smagorinsky_cs,
        xp=xp,
        alfa_vegetation=alfa,
        x_periodisk=False,
    )
    losare.initiera(None)

    profil = np.zeros((3, ny, nz))
    profil[0] = u_in_z[None, :]
    storning: _Storning | None = None
    if inflode.turbulensintensitet > 0:
        storning = _Storning(ny, nz, korrelation_steg=4.0 / ul, seed=param.storning_seed)
    n_ramp_inflode = max(1, n_ramp // 2)

    sum_u = xp.zeros((3, losare.n), dtype=xp.float64)
    sum_uu = xp.zeros((6, losare.n), dtype=xp.float64)
    sum_nut = xp.zeros(losare.n, dtype=xp.float64)
    n_prov = 0
    max_mach = 0.0
    tau_min = float("inf")
    t0 = time.perf_counter()
    nasta_logg = 0.1

    def _logg(s: str) -> None:
        if logg is not None:
            logg(s)

    _logg(
        f"LBM-LES {nx}×{ny}×{nz} ({losare.n_luft} luftceller), dx={dx} m, dt={dt_s:.4g} s, "
        f"tau0={tau0:.6f}, steg: {n_ramp} upprampning + {n_medel} medel, backend={backend}"
    )
    for n in range(n_tot):
        r = math.sin(0.5 * math.pi * (n + 1) / n_ramp_inflode) ** 2 if n < n_ramp_inflode else 1.0
        u_in = profil * r
        if storning is not None:
            u_in = u_in + inflode.turbulensintensitet * r * u_in_z[None, None, :] * storning.nasta()
        losare.satt_inflode(xp.asarray(u_in))
        losare.steg()

        if n >= n_ramp and (n - n_ramp) % param.medel_intervall_steg == 0:
            u = losare.u
            sum_u += u
            for p, (a, b) in enumerate(_PAR):
                sum_uu[p] += u[a].astype(xp.float64) * u[b]
            sum_nut += losare.nu_t
            n_prov += 1

        if (n + 1) % param.kontroll_intervall_steg == 0 or n == n_tot - 1:
            if not losare.ar_andlig():
                raise LuftFel(f"LBM-fältet innehåller NaN/Inf efter steg {n + 1} – divergens.")
            mach = losare.max_mach()
            if not math.isfinite(mach):
                raise LuftFel(f"Icke ändligt Machtal efter steg {n + 1} – divergens.")
            max_mach = max(max_mach, mach)
            tau_min = min(tau_min, float(xp.min(losare.tau)))
            if mach > param.mach_max:
                raise LuftFel(
                    f"Machtal {mach:.3f} > {param.mach_max} efter steg {n + 1}. "
                    "Sänk u_lattice eller förfina rutnätet."
                )
            if (n + 1) / n_tot >= nasta_logg:
                tid = time.perf_counter() - t0
                _logg(
                    f"steg {n + 1}/{n_tot} ({100 * (n + 1) / n_tot:.0f} %), "
                    f"{(n + 1) / tid:.1f} steg/s, max Mach {mach:.3f}"
                )
                nasta_logg += 0.1
    tid = time.perf_counter() - t0
    if n_prov == 0:
        raise LuftFel("Inga prov i medelperioden.")

    # Medelvärden → fysikaliska enheter
    hast_skala = inflode.u_ref_m_s / ul
    visk_skala = dx * dx / dt_s
    luft = ~grid.fast
    medel_u = till_numpy(sum_u / n_prov).reshape(3, nx, ny, nz)
    medel_uu = till_numpy(sum_uu / n_prov).reshape(6, nx, ny, nz)
    medel_nut = till_numpy(sum_nut / n_prov).reshape(nx, ny, nz)
    varians = sum(medel_uu[p] - medel_u[a] * medel_u[b] for p, (a, b) in enumerate(_PAR[:3]))
    k_res = 0.5 * np.maximum(varians, 0.0) * hast_skala**2  # m²/s²
    hojd_m = nz * dx
    l_m = np.minimum(KAPPA * z, param.blandningslangd_max_andel * hojd_m)[None, None, :]
    nu_res = C_MU**0.25 * l_m * np.sqrt(k_res)
    nu_t = (medel_nut * visk_skala + nu_res) * luft
    u_m_s = medel_u * hast_skala * luft[None]

    mlups = losare.n * n_tot / tid / 1e6
    _logg(f"klar: {n_tot} steg på {tid:.1f} s ({n_tot / tid:.1f} steg/s, {mlups:.2f} MLUPS)")
    meta: dict[str, Any] = {
        "kontrakt": KONTRAKT_KL1,
        "metod": "D3Q19 reguljariserad BGK + Smagorinsky (LES), Guo-kraft",
        "backend": backend,
        "lbm_parametrar": asdict(param),
        "steg_upprampning": n_ramp,
        "steg_medel": n_medel,
        "steg_totalt": n_tot,
        "medelprov": n_prov,
        "dt_s": dt_s,
        "tau0": tau0,
        "tau_min": tau_min,
        "max_mach": max_mach,
        "divergerat": False,
        "tid_s": tid,
        "mlups": mlups,
        "nu_t_ansats": "medel(nu_t_SGS) + C_mu^(1/4)*min(kappa*z, 0.1*H)*sqrt(k_res)",
    }
    return Vindfalt(
        grid=grid,
        inflode=inflode,
        u_m_s=u_m_s.astype(np.float64),
        nu_t_m2_s=nu_t.astype(np.float64),
        medelperiod_s=n_medel * dt_s,
        metadata=meta,
    )
