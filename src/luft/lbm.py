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
  `Π^neq = Σ c_i c_i (f_i − f_i^eq) + ½(uF + Fu)` (Guo-korrektion).
  Jämvikten (andra ordningens Hermite), den reguljariserade icke-jämviktsdelen och
  Guos källterm är alla linjärkombinationer av samma tio basfunktioner
  `w_i · {1, 3 c_i, 4,5 Q_i}`. Kollisionen görs därför som två matrisprodukter:
  momenten `(δρ, j, Π) = H · f̄` (10 × 19) och tillbaka `f̄* = R · a` (19 × 10), där
  koefficienterna är
  `a₀ = δρ`, `a_j = j + F` och `a_Π = ρuu + (1 − 1/τ) Π^neq + (1 − 1/(2τ))(uF + Fu)`.
  Det är matematiskt samma operator som `f^eq + (1 − 1/τ) f^neq,reg + S_i` med Guos
  `S_i = w_i (1 − 1/(2τ)) [3 (c_i − u)·F + 9 (c_i·u)(c_i·F)]`, med ett tillägg: första
  ordningens icke-jämviktsmoment (`j − ρu = −F/2`) relaxeras som i BGK, så att
  rörelsemängden ökar med exakt `F` per steg. (Version L-a projicerade bort det och gav
  därför effektiv kraft `(3/2 − 1/(2τ)) F`, dvs. halva kraften vid `τ → ½` – uppmätt
  `u_max` 0,66 av analytisk Poiseuille vid `τ = 0,6`.)
* **LES:** Smagorinsky `ν_t = (C_s Δ)² |S|` med `|S| = √(2 S:S)` lokalt ur
  `S = −Π^neq / (2 ρ c_s² τ)`. Eftersom `τ = 3(ν₀ + ν_t) + ½` beror på `|S|`, som i sin tur
  beror på `τ`, löses andragradsekvationen exakt (Hou m.fl. 1996, Malaspinas & Sagaut 2012):
  `τ = ½ (τ₀ + √(τ₀² + 18 √2 (C_s Δ)² √(Π^neq:Π^neq) / ρ))`, `ν_t = (τ − τ₀)/3`.
  Inga finita differenser behövs; allt är lokalt i cellen.
* **Väggmodell (valbar, `vaggmodell="loglag"`):** jämviktsväggmodell av Schumanns typ
  (Schumann 1975, J. Comput. Phys. 18:376–404; översikt Piomelli & Balaras 2002,
  Annu. Rev. Fluid Mech. 34:349–374): väggskjuvningen ges av log-lagen i stället för att
  lösas upp. I LBM görs det lokalt i två delar:
  1. Väggarna blir friktionsfria: bounce-back ersätts med spegelreflektion av
     normalkomponenten (`_vaggreflektioner`), så att no-slip-friktionen försvinner.
  2. Väggskjuvningen läggs som volymkraft i första cellen via Guos kraftterm,
     `F = −ρ u_τ² û_t / Δ` per vägg, där `u_t` är den momentana hastighetens
     tangentiella del (normalkomponenten nollad) och `u_τ` fås ur den grova log-lagen
     vid `y_p = Δ/2` (halvvägs till väggen): `|u_t| = (u_τ/κ) ln((y_p + z₀)/z₀)`. Det
     ger `F = −ρ (κ/ln((½ + z₀/Δ)/(z₀/Δ)))² |u_t| u_t` – en kvadratisk dragkraft som
     vegetationen. Samma `ln((z + z₀)/z₀)` som i `Inflode.profil_m_s`, så att mark och
     inflödesprofil är konsistenta när `z0_vagg_m = Inflode.z0_m`.
  Totala väggskjuvningen blir därmed exakt log-lagens, oberoende av upplösningen i
  första cellen (med ν-justering i väggcellen, som först prövades, styr `ν` inte
  bounce-back-väggens rörelsemängdsutbyte vid `τ ≈ ½`: uppmätt sämre profil).
  Allt är lokalt per cell och fungerar likadant med numpy och cupy. Inåtgående hörn
  och kanter (t.ex. gaturummets hörn mot marken) har ingen entydig spegling och får
  bounce-back (no-slip) utan väggkraft; antalet redovisas.
* **Kraftterm:** Guo m.fl. (2002), se ovan; `ρu = Σ c_i f_i + F/2`.
* **Vegetation:** dragkraft per volym `F = −ρ C_d · LAD · |U| U` (fysikaliska enheter, N/m³).
  Accelerationen `a = −C_d · LAD · |U| U` (m/s²) omräknas till gitterenheter med
  `a_lat = a · dt²/dx` och `U = u_lat · dx/dt`, vilket ger
  `a_lat = −C_d · LAD · dx · |u_lat| u_lat` – dvs. `α = C_d · LAD · dx` är dimensionslös
  och `F_lat = −ρ α |u| u`. Eftersom `u` beror på `F` (`u = u* + F/(2ρ)`) löses
  `u = u* − ½ α |u| u` exakt: `u = u* · 2 / (1 + √(1 + 2 α |u*|))`.
* **Randvillkor:** inflöde `i = 0` = hastighetsrand med icke-jämviktsextrapolation
  (Guo, Zheng & Shi 2002, Chin. Phys. 11:366–374): `f = f^eq(ρ₁, u_in) + f^neq₁` med
  `ρ` och `f^neq` från cellen innanför och logaritmisk profil `u_in`
  (`Inflode.profil_m_s`, normerad så att `u_lat = u_lattice` vid `z_ref`), med valfri
  störning; utflöde `i = nx−1` = tryckrand `ρ = 1` med samma extrapolation av
  hastighet och `f^neq` från `nx−2`; `y` periodisk; tak = friktionsfri spegling
  (halvvägs); mark under `k = 0` och alla `fast`-celler = halvvägs bounce-back.
  Domänen startar med inflödesprofilen i alla luftceller (`start="profil"`).
  Detta väljs med `inflodesrand="extrapolation"`, `utflodesrand="tryck"`,
  `start="profil"` och **rekommenderas**. Standard är fortfarande L-a:s val
  (jämviktsinflöde med `ρ = 1`, noll gradient ut, start från vila) för
  bakåtkompatibilitet, men det saknar tryckreferens: uppmätt bara 0,67–0,73 av
  profilens hastighet en cell in och `ρ ≈ 1,06` (64 × 16 × 16, tom domän), dvs. hela
  vindfältet blir ≈ 30 % för svagt. Med rätt flöde blir hastigheterna högre: L-a-demons
  kvarter (dx 4 m, höghus 45 m) gav max Mach 0,451 vid u_lattice 0,08 (mot 0,279 med
  L-a:s rand), så demon behöver lägre u_lattice eller `"auto"` när randen byts.
* **Inflödesturbulens:** `"brus"` (standard, som i L-a) = tids- och rumsligt filtrerat
  brus; `"virvlar"` = syntetisk virvelmetod (SEM, Jarrin m.fl. 2006), se
  `SyntetiskaVirvlar`.
* **Enheter:** `dt = dx · u_lattice / u_ref`. Hastighet `U = u_lat · u_ref / u_lattice`,
  viskositet `ν = ν_lat · dx² / dt`.
* **Prestanda:** kollisionen är två matrisprodukter (BLAS sgemm i numpy, cuBLAS i cupy)
  med förallokerade utdata (`matmul(..., out=)`) och strömningen skriver till en
  förallokerad buffert med skivkopior i stället för `roll`. Allt i float32.

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

`Vindfalt.u_std_m_s` är standardavvikelsen över medelperioden för den horisontella
hastigheten `|u_h| = √(u_x² + u_y²)` (samma prover som medelvärdet), 0 i fasta celler.

Begränsningar
-------------
* Väggmodellen antar jämvikts-log-lag (neutral, ingen tryckgradient, momentan
  hastighet i första cellen) och använder samma `z₀` för mark och fasader. Inåtgående
  hörn har no-slip i stället för väggmodell.
* Utflöde med noll gradient kan bli instabilt vid kraftigt bakåtflöde vid randen;
  lägg gärna en buffert medvinds.
"""

from __future__ import annotations

import itertools
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
U_LATTICE_MAX = 0.1  # riktvärdet u_lat ≤ 0,1 i docs/LUFT.md 2.1

VAGGMODELLER = ("ingen", "loglag")
INFLODESTURBULENS = ("brus", "virvlar")
INFLODESRANDER = ("jamvikt", "extrapolation")
UTFLODESRANDER = ("noll_gradient", "tryck")
STARTLAGEN = ("profil", "vila")

# Säkerhetsfaktor för u_lattice="auto": förväntad max |u| ≈ faktor × inflödesprofilens
# max i domänen. Värdet 2,5 är uppdragets (L-d) konservativa val. Uppmätta kvoter
# max Mach / inflödets max Mach (2026-11-21, randvillkor och start enligt L-d):
#   kub H = 8 celler utan inflödesturbulens (test L3)          1,43
#   vegetationszon (TestVegetation, u_lattice 0,1)             1,48
#   tom domän, SEM I = 0,15                                    1,94
#   kub H = 8 celler, SEM I = 0,15 (test_lbm_validering)       1,98
# (L-a-demons 0,25 vid u_lattice 0,08 mättes med det gamla inflödet, som bara gav
# ≈ 0,7 av profilens flöde.) Byar från inflödesturbulensen dominerar; 2,5 ger marginal
# även för hörnströmmar vid höga hus.
MACH_FAKTOR_STANDARD = 2.5


# --------------------------------------------------------------------------------------
# Parametrar
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LbmParametrar:
    """Parametrar för LES-körningen (`docs/LUFT.md` 3.1).

    `u_lattice` är gitterhastigheten för `u_ref`, eller `"auto"` för att välja den med
    `valj_u_lattice` (Machmål `mach_mal`, säkerhetsfaktor `mach_faktor`).
    """

    u_lattice: float | str = "auto"  # gitterhastighet för u_ref, eller "auto" (Mach-säkert)
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
    # L-d:
    vaggmodell: str = "ingen"  # "ingen" | "loglag"
    z0_vagg_m: float | None = None  # råhetslängd för väggmodellen; None = Inflode.z0_m
    inflodesturbulens: str = "brus"  # "brus" | "virvlar" (SEM, kräver langdskala)
    inflode_langdskala_m: float | None = None  # integral längdskala L för "virvlar"
    mach_mal: float = 0.15  # mål för förväntad max Mach när u_lattice="auto"
    mach_faktor: float = MACH_FAKTOR_STANDARD  # max |u| ≈ faktor × inflödets max
    # Randvillkor och start: standard sedan L-d = korrekt massflöde (L-a gav ~30 % för svagt).
    # Rekommenderat: "extrapolation", "tryck", "profil" – ger rätt flöde men högre Mach.
    inflodesrand: str = "extrapolation"  # "extrapolation" (Guo m.fl. 2002) | "jamvikt" (L-a)
    utflodesrand: str = "tryck"  # "tryck" (ρ = 1) | "noll_gradient" (L-a)
    vagg_provhojd_celler: int = 0  # väggmodellens provpunkt, celler ut från väggcellen
    start: str = "profil"  # "profil" (profilen överallt) | "vila" (L-a, rampas upp)

    def __post_init__(self) -> None:
        if isinstance(self.u_lattice, str):
            if self.u_lattice != "auto":
                raise LuftFel(f"u_lattice='{self.u_lattice}': ange ett tal eller 'auto'.")
        else:
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
        if self.vaggmodell not in VAGGMODELLER:
            raise LuftFel(f"vaggmodell='{self.vaggmodell}': välj en av {VAGGMODELLER}.")
        if self.z0_vagg_m is not None and self.z0_vagg_m <= 0:
            raise LuftFel("z0_vagg_m måste vara positiv.")
        if self.inflodesturbulens not in INFLODESTURBULENS:
            raise LuftFel(
                f"inflodesturbulens='{self.inflodesturbulens}': välj en av {INFLODESTURBULENS}."
            )
        if self.inflode_langdskala_m is not None and self.inflode_langdskala_m <= 0:
            raise LuftFel("inflode_langdskala_m måste vara positiv.")
        if self.inflodesrand not in INFLODESRANDER:
            raise LuftFel(f"inflodesrand='{self.inflodesrand}': välj en av {INFLODESRANDER}.")
        if self.utflodesrand not in UTFLODESRANDER:
            raise LuftFel(f"utflodesrand='{self.utflodesrand}': välj en av {UTFLODESRANDER}.")
        if self.start not in STARTLAGEN:
            raise LuftFel(f"start='{self.start}': välj en av {STARTLAGEN}.")
        if self.vagg_provhojd_celler < 0:
            raise LuftFel("vagg_provhojd_celler måste vara ≥ 0.")
        if not 0 < self.mach_mal <= self.mach_max:
            raise LuftFel(f"mach_mal måste ligga i (0, {self.mach_max}].")
        if self.mach_faktor < 1.0:
            raise LuftFel("mach_faktor måste vara ≥ 1.")


def valj_u_lattice(
    grid: Voxelgrid,
    inflode: Inflode,
    mach_mal: float = 0.15,
    faktor: float = MACH_FAKTOR_STANDARD,
) -> float:
    """Väljer `u_lattice` så att förväntad max Mach i domänen blir `mach_mal`.

    Förväntad max hastighet = `faktor` × inflödesprofilens max inom domänen (översta
    cellcentrum), se `MACH_FAKTOR_STANDARD` för motivering och uppmätt kvot. Ger

        u_lattice = mach_mal / (√3 · faktor · max_z u(z) / u_ref),

    begränsat uppåt till `U_LATTICE_MAX = 0,1`. Ett lägre `u_lattice` ger fler tidssteg
    (`∝ 1/u_lattice`) men inte sämre upplösning i rummet.
    """
    if mach_mal <= 0 or faktor <= 0:
        raise LuftFel("mach_mal och faktor måste vara positiva.")
    z = grid.cellcentrum_z_m()
    kvot = float(np.max(inflode.profil_m_s(z))) / inflode.u_ref_m_s
    if not math.isfinite(kvot) or kvot <= 0:
        raise LuftFel("Inflödesprofilen saknar positiv hastighet i domänen.")
    return min(mach_mal / (math.sqrt(3.0) * faktor * kvot), U_LATTICE_MAX)


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


def _skiv_par(s: int) -> list[tuple[slice, slice]]:
    """Skivor (mål, källa) för periodisk förskjutning `ut[x] = inn[x − s]` längs en axel."""
    if s == 0:
        return [(slice(None), slice(None))]
    if s > 0:
        return [(slice(s, None), slice(0, -s)), (slice(0, s), slice(-s, None))]
    return [(slice(0, s), slice(-s, None)), (slice(s, None), slice(0, -s))]


def _rullplan(skift: tuple[int, ...]) -> list[tuple[tuple[slice, ...], tuple[slice, ...]]]:
    """Kopieringsplan som motsvarar `roll(inn, skift)` men skriver till en befintlig array."""
    plan = []
    for kombination in itertools.product(*(_skiv_par(s) for s in skift)):
        plan.append((tuple(m for m, _ in kombination), tuple(k for _, k in kombination)))
    return plan


def _vaggreflektioner(
    fast: np.ndarray[Any, np.dtype[np.bool_]], x_periodisk: bool
) -> tuple[
    list[tuple[int, int, np.ndarray[Any, np.dtype[np.int64]]]],
    list[
        tuple[
            int,
            np.ndarray[Any, np.dtype[np.int64]],
            np.ndarray[Any, np.dtype[np.float32]],
            np.ndarray[Any, np.dtype[np.int64]],
        ]
    ],
    int,
]:
    """Spegelreflektion (friktionsfri vägg) i stället för bounce-back, för väggmodellen.

    För en inkommande riktning `i` vars källa `x − c_i` är fast speglas de komponenter
    `a` där sidogrannen `x − c_ia e_a` är fast (planvägg: normalkomponenten byter tecken,
    tangentiell rörelsemängd bevaras). Är bara diagonalgrannen fast (konvex kant) används
    bounce-back. I celler där spegelavbildningen inte är en bijektion mellan inkommande
    och utgående populationer (inåtgående hörn, tunna fasta skivor) används bounce-back
    i hela cellen, så att massan bevaras exakt.

    Returnerar
    ----------
    reflektioner:
        Lista `(i, r, idx)`: `f_i(x) = f*_r(x)` för cellerna `idx`.
    vaggceller:
        Per normalaxel `a`: `(a, idx, antal, bort)` = celler med spegelvägg vinkelrätt mot
        `a`, antal sådana väggar (1 eller 2) och riktningen bort från väggen längs `a`
        (+1, −1, eller 0 om väggar på båda sidor).
    n_bounceback_celler:
        Antal väggnära celler som föll tillbaka på bounce-back.
    """
    luft = ~fast
    n = fast.size
    sida = {}
    for a in range(3):
        for s in (1, -1):
            c = np.zeros(3, dtype=np.int64)
            c[a] = s
            sida[(a, s)] = _skifta_mask(fast, c, x_periodisk).reshape(-1)
    uppslag = {tuple(int(v) for v in c): q for q, c in enumerate(C_D3Q19)}
    r_karta = np.full((19, n), -1, dtype=np.int64)
    speglad = np.zeros(n, dtype=np.bool_)
    for i in range(1, 19):
        kalla = _skifta_mask(fast, C_D3Q19[i], x_periodisk) & luft
        if not x_periodisk:
            kalla[0] = False
            kalla[-1] = False
        idx = np.flatnonzero(kalla.reshape(-1))
        if not idx.size:
            continue
        c_r = np.repeat(C_D3Q19[i][None, :], idx.size, axis=0)
        flipp_nagon = np.zeros(idx.size, dtype=np.bool_)
        for a in range(3):
            ca = int(C_D3Q19[i, a])
            if ca == 0:
                continue
            flipp = sida[(a, ca)][idx]
            c_r[flipp, a] = -ca
            flipp_nagon |= flipp
        c_r[~flipp_nagon] = -C_D3Q19[i]
        speglad[idx] |= flipp_nagon & np.any(c_r != -C_D3Q19[i], axis=1)
        r_karta[i, idx] = [uppslag[(int(v[0]), int(v[1]), int(v[2]))] for v in c_r]
    # Bijektionskontroll per cell: använda utgående = motsatta till inkommande.
    anv = np.zeros((19, n), dtype=np.int64)
    forv = np.zeros((19, n), dtype=np.int64)
    for i in range(1, 19):
        idx = np.flatnonzero(r_karta[i] >= 0)
        anv[r_karta[i, idx], idx] += 1
        forv[MOTSATT[i], idx] += 1
    felaktig = np.any(anv != forv, axis=0)
    for i in range(1, 19):
        m = felaktig & (r_karta[i] >= 0)
        r_karta[i, m] = MOTSATT[i]
    speglad &= ~felaktig
    reflektioner: list[tuple[int, int, np.ndarray[Any, np.dtype[np.int64]]]] = []
    for i in range(1, 19):
        for r in np.unique(r_karta[i][r_karta[i] >= 0]):
            idx = np.flatnonzero(r_karta[i] == r).astype(np.int64)
            reflektioner.append((i, int(r), idx))
    vaggceller = []
    for a in range(3):
        antal = sida[(a, 1)].astype(np.int64) + sida[(a, -1)].astype(np.int64)
        m = speglad & (antal > 0) & luft.reshape(-1)
        if not x_periodisk:
            plan = np.zeros(fast.shape, dtype=np.bool_)
            plan[0] = True
            plan[-1] = True
            m &= ~plan.reshape(-1)
        idx = np.flatnonzero(m).astype(np.int64)
        if idx.size:
            bort = sida[(a, 1)][idx].astype(np.int64) - sida[(a, -1)][idx].astype(np.int64)
            vaggceller.append((a, idx, antal[idx].astype(np.float32), bort))
    vaggnara = np.any(r_karta >= 0, axis=0)
    return reflektioner, vaggceller, int(np.count_nonzero(vaggnara & ~speglad))


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
    vaggmodell:
        `"ingen"` (no-slip med bounce-back) eller `"loglag"` (se modulens docstring).
    z0_vagg_celler:
        Råhetslängd för väggmodellen i celler (`z0_m / dx`).
    vagg_provhojd_celler:
        Väggmodellens indata tas `h` celler ut från väggcellen längs normalen, på
        avståndet `y = h + ½` från väggen (0 = väggcellen själv). Faller tillbaka på
        väggcellen om provcellen är fast eller utanför rutnätet.
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
        vaggmodell: str = "ingen",
        z0_vagg_celler: float | None = None,
        vagg_provhojd_celler: int = 0,
        inflodesrand: str = "jamvikt",
        utflodesrand: str = "noll_gradient",
    ) -> None:
        if fast.ndim != 3:
            raise LuftFel("fast måste vara tredimensionell.")
        if tau0 <= 0.5:
            raise LuftFel(f"tau0={tau0} måste vara > 0,5.")
        if vaggmodell not in VAGGMODELLER:
            raise LuftFel(f"vaggmodell='{vaggmodell}': välj en av {VAGGMODELLER}.")
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
        # Momentmatris H (10, 19): δρ, j (3), Π (6); rekonstruktion R (19, 10).
        h10 = np.vstack([np.ones((1, 19)), C_D3Q19.T.astype(np.float64), h6])
        r10 = np.hstack([W_D3Q19[:, None], 3.0 * W_D3Q19[:, None] * C_D3Q19, r6])
        self.h10 = xp.asarray(h10.astype(f32))
        self.r10 = xp.asarray(r10.astype(f32))

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

        # Kopieringsplaner för strömning (3D) och takspegling (2D).
        self._stromplan = [_rullplan(tuple(int(v) for v in C_D3Q19[i])) for i in range(19)]
        self._takplan = {
            i: _rullplan((int(C_D3Q19[i, 0]), int(C_D3Q19[i, 1]))) for i in self.tak_rikt
        }

        # Väggmodell: spegelreflektion i stället för bounce-back + väggkraft i första cellen.
        self.vaggmodell = vaggmodell
        self.vaggceller: list[tuple[int, Any, Any, Any]] = []
        self.n_vagg = 0
        self.n_vagg_bounceback = 0
        if vaggmodell == "loglag":
            if z0_vagg_celler is None or z0_vagg_celler <= 0:
                raise LuftFel("vaggmodell='loglag' kräver z0_vagg_celler > 0.")
            if vagg_provhojd_celler < 0:
                raise LuftFel("vagg_provhojd_celler måste vara ≥ 0.")
            refl, vagg, n_bb = _vaggreflektioner(self.fast_np, x_periodisk)
            self.studs = [(i, r, xp.asarray(idx)) for i, r, idx in refl]
            for a, idx, antal, bort in vagg:
                prov, koef = self._provpunkter(a, idx, bort, vagg_provhojd_celler, z0_vagg_celler)
                self.vaggceller.append(
                    (a, xp.asarray(idx), xp.asarray(prov), xp.asarray(antal * koef))
                )
            if vagg:
                self.n_vagg = int(np.unique(np.concatenate([v[1] for v in vagg])).size)
            self.n_vagg_bounceback = n_bb

        self.alfa: Any = None
        if alfa_vegetation is not None and np.any(np.asarray(alfa_vegetation) > 0):
            a_np = np.asarray(alfa_vegetation, dtype=f32).reshape(-1)
            self.alfa = xp.asarray(a_np)
        g = np.asarray(acceleration_lat, dtype=f32)
        self.g: Any = xp.asarray(g)[:, None] if np.any(g != 0) else None

        self.f: Any = xp.zeros((19, nx, ny, nz), dtype=f32)  # f̄ = f − w
        # Förallokerade buffertar: momentkoefficienter, efter kollision, efter strömning.
        self._mom: Any = xp.empty((10, self.n), dtype=f32)
        self._fpost: Any = xp.empty((19, nx, ny, nz), dtype=f32)
        self._fbuf: Any = xp.empty((19, nx, ny, nz), dtype=f32)
        self.inflode_f: Any = None  # (19, ny, nz)
        self.inflode_u: Any = None  # (3, ny·nz)
        if inflodesrand not in INFLODESRANDER:
            raise LuftFel(f"inflodesrand='{inflodesrand}': välj en av {INFLODESRANDER}.")
        if utflodesrand not in UTFLODESRANDER:
            raise LuftFel(f"utflodesrand='{utflodesrand}': välj en av {UTFLODESRANDER}.")
        self.inflodesrand = inflodesrand
        self.utflodesrand = utflodesrand
        self._luft0: Any = self.luft_d.reshape(self.form)[0].reshape(-1)
        self._luft_ut: Any = self.luft_d.reshape(self.form)[-1].reshape(-1)
        # Senaste makrostorheter (från kollisionen i senaste steget), platta (n,).
        self.rho: Any = xp.ones(self.n, dtype=f32)
        self.u: Any = xp.zeros((3, self.n), dtype=f32)
        self.nu_t: Any = xp.zeros(self.n, dtype=f32)
        self.tau: Any = xp.full(self.n, self.tau0, dtype=f32)

    # ---------------------------------------------------------------- hjälp

    def _provpunkter(
        self,
        axel: int,
        idx: np.ndarray[Any, np.dtype[np.int64]],
        bort: np.ndarray[Any, np.dtype[np.int64]],
        h: int,
        z0_celler: float,
    ) -> tuple[np.ndarray[Any, np.dtype[np.int64]], np.ndarray[Any, np.dtype[np.float32]]]:
        """Provcell och koefficient `(κ/ln((y + z₀)/z₀))²` för väggceller längs `axel`."""
        koord = list(np.unravel_index(idx, self.form))
        ny_k = koord[axel] + h * bort
        langd = self.form[axel]
        if axel == 1 or (axel == 0 and self.x_periodisk):
            ok = np.ones(idx.size, dtype=np.bool_)
            ny_k = ny_k % langd
        else:
            ok = (ny_k >= 0) & (ny_k < langd)
            ny_k = np.clip(ny_k, 0, langd - 1)
        koord[axel] = ny_k
        prov = np.ravel_multi_index(tuple(koord), self.form).astype(np.int64)
        ok &= (bort != 0) & ~self.fast_np.reshape(-1)[prov]
        prov = np.where(ok, prov, idx)
        y = np.where(ok, h + 0.5, 0.5)
        koef = (KAPPA / np.log((y + z0_celler) / z0_celler)) ** 2
        return prov, koef.astype(np.float32)

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
        self.f[...] = self.jamvikt(xp.zeros(self.n, dtype=np.float32), u).reshape(19, nx, ny, nz)

    def satt_inflode(self, u_in: Any) -> None:
        """Sätter inflödets hastighet (3, ny, nz) i gitterenheter (jämvikt med ρ = 1)."""
        xp = self.xp
        _, ny, nz = self.form
        u = xp.asarray(u_in, dtype=np.float32).reshape(3, ny * nz)
        u = u * self._luft0
        self.inflode_u = u
        if self.inflodesrand == "extrapolation":
            self.inflode_f = None
        else:
            self.inflode_f = self.jamvikt(xp.zeros(ny * nz, dtype=np.float32), u).reshape(
                19, ny, nz
            )

    def _icke_jamvikt_extrapolation(self, f_inre: Any, u_rand: Any | None) -> Any:
        """Guo, Zheng & Shi (2002): `f = f^eq(ρ_inre, u_rand) + (f_inre − f^eq(ρ_inre, u_inre))`.

        `f_inre` (19, ny, nz) är grannplanet. Med `u_rand = None` (tryckrand) används
        `ρ = 1` och `u_inre` i stället.
        """
        xp = self.xp
        _, ny, nz = self.form
        fi = f_inre.reshape(19, ny * nz)
        drho = fi.sum(axis=0)
        rho = 1.0 + drho
        u_inre = xp.tensordot(self.cT, fi, axes=(1, 0)) / rho
        fneq = fi - self.jamvikt(drho, u_inre)
        if u_rand is None:
            feq = self.jamvikt(xp.zeros_like(drho), u_inre)
        else:
            feq = self.jamvikt(drho, u_rand)
        return (feq + fneq).reshape(19, ny, nz)

    def total_massa(self) -> float:
        """Total massa i luftcellerna (float64-summering)."""
        return float(self.xp.sum(self.f, dtype=self.xp.float64)) + float(self.n_luft)

    # ---------------------------------------------------------------- steg

    def _kollidera(self) -> Any:
        """Kollision i momentrummet; skriver resultatet till `self._fpost` och returnerar den."""
        xp = self.xp
        f = self.f.reshape(19, self.n)
        mom = self._mom
        xp.matmul(self.h10, f, out=mom)  # δρ, j, Σ c c f̄
        drho = mom[0].copy()
        rho = 1.0 + drho
        j = mom[1:4]
        kraft: Any = None
        if self.g is not None:
            kraft = rho * self.g
            u = (j + 0.5 * kraft) / rho
        else:
            u = j / rho
        if self.alfa is not None:
            fart = xp.sqrt((u * u).sum(axis=0))
            u = u * (2.0 / (1.0 + xp.sqrt(1.0 + 2.0 * self.alfa * fart)))
            fart = xp.sqrt((u * u).sum(axis=0))
            fveg = -(rho * self.alfa * fart) * u
            kraft = fveg if kraft is None else kraft + fveg

        if self.vaggceller:
            # Väggskjuvning ur log-lagen som volymkraft i första cellen (Δ = 1):
            # Löst implicit i u_t (samma kvadratiska form som vegetationen) så att
            # grova råa väggar aldrig överskjuter eller vänder hastigheten.
            fvagg = xp.zeros((3, self.n), dtype=np.float32)
            for a, idx, prov, koef in self.vaggceller:
                ut_t = u[:, prov].copy()
                ut_t[a] = 0.0
                fart_t = xp.sqrt((ut_t * ut_t).sum(axis=0))
                skala_w = 2.0 / (1.0 + xp.sqrt(1.0 + 2.0 * koef * fart_t))
                ut_eff = ut_t * skala_w
                fart_eff = fart_t * skala_w
                fvagg[:, idx] -= (koef * rho[idx] * fart_eff) * ut_eff
            u = u + fvagg / (2.0 * rho)
            kraft = fvagg if kraft is None else kraft + fvagg

        # Π^neq (6, n), på plats i mom[4:10]
        pi = mom[4:10]
        ruu = xp.empty((6, self.n), dtype=np.float32)
        for p, (a, b) in enumerate(_PAR):
            xp.multiply(u[a], u[b], out=ruu[p])
            ruu[p] *= rho
            pi[p] -= ruu[p]
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
        # Koefficienter a = (δρ, j + F, ρuu + (1 − 1/τ) Π^neq + (1 − 1/(2τ))(uF + Fu))
        omega = 1.0 - 1.0 / tau
        pi *= omega
        pi += ruu
        if kraft is not None:
            j += kraft
            g_fakt = 1.0 - 0.5 / tau
            for p, (a, b) in enumerate(_PAR):
                pi[p] += g_fakt * (u[a] * kraft[b] + u[b] * kraft[a])
        fp = self._fpost.reshape(19, self.n)
        xp.matmul(self.r10, mom, out=fp)
        self.rho = rho
        self.u = u
        self.tau = tau
        self.nu_t = (tau - self.tau0) * (1.0 / 3.0)
        return self._fpost

    def steg(self) -> None:
        """Ett tidssteg: kollision, strömning, randvillkor."""
        fp = self._kollidera()
        fn = self._fbuf
        for i in range(19):
            fn_i = fn[i]
            fp_i = fp[i]
            for mal, kalla in self._stromplan[i]:
                fn_i[mal] = fp_i[kalla]
        # Tak: friktionsfri spegling (halvvägs). Population nedåt i översta lagret kommer
        # från spegelriktningen uppåt i cellen (x − cx, y − cy) i samma lager.
        for i in self.tak_rikt:
            mal_plan = fn[i, :, :, -1]
            kalla_plan = fp[SPEGEL_Z[i], :, :, -1]
            for mal, kalla in self._takplan[i]:
                mal_plan[mal] = kalla_plan[kalla]
        # Mark och fasta celler: halvvägs bounce-back.
        fnp = fn.reshape(19, self.n)
        fpp = fp.reshape(19, self.n)
        for i, o, idx in self.studs:
            fnp[i, idx] = fpp[o, idx]
        if self.fast_idx.size:
            fnp[:, self.fast_idx] = 0.0
        if not self.x_periodisk:
            if self.utflodesrand == "tryck":
                ut_plan = self._icke_jamvikt_extrapolation(fn[:, -2], None)
                fn[:, -1] = ut_plan * self._luft_ut.reshape(1, *self.form[1:])
            else:
                fn[:, -1] = fn[:, -2]
            if self.inflode_u is not None:
                if self.inflodesrand == "extrapolation":
                    in_plan = self._icke_jamvikt_extrapolation(fn[:, 1], self.inflode_u)
                    fn[:, 0] = in_plan * self._luft0.reshape(1, *self.form[1:])
                elif self.inflode_f is not None:
                    fn[:, 0] = self.inflode_f
        self._fbuf = self.f
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


def _talt(x: np.ndarray[Any, np.dtype[np.float64]]) -> np.ndarray[Any, np.dtype[np.float64]]:
    """Formfunktion för SEM: `f(x) = √(3/2) (1 − |x|)` för |x| < 1, annars 0 (∫f² = 1)."""
    ut: np.ndarray[Any, np.dtype[np.float64]] = np.where(
        np.abs(x) < 1.0, math.sqrt(1.5) * (1.0 - np.abs(x)), 0.0
    )
    return ut


class SyntetiskaVirvlar:
    """Syntetisk virvelmetod (SEM) för inflödesturbulens, deterministisk.

    Jarrin, Benhamadouche, Laurence & Prosser (2006), "A synthetic-eddy-method for
    generating inflow conditions for large-eddy simulations", Int. J. Heat Fluid Flow
    27(4), 585–593. `N` virvlar med slumpade tecken `ε_k ∈ {−1, 1}³` ligger likformigt i
    en låda `x ∈ [−σ, σ]` × `y ∈ [0, ny)` (periodisk) × `z ∈ [−σ, nz − 1 + σ]` (index-
    enheter, inflödesplanet vid x = 0) och konvekteras med inflödesprofilens lokala
    hastighet `U(z_k)`. En virvel som lämnar lådan återföds vid `x = −σ` med nya
    slumptal; höjden dras med täthet ∝ `U(z)` så att virveltätheten i lådan förblir
    likformig (uppehållstiden är ∝ `1/U`). Signalen i planet är

        s_c(y, z) = √(V_B/σ³) / √N · Σ_k ε_k,c f(x_k/σ) f(Δy_k/σ) f(Δz_k/σ),

    som har väntevärde 0 och varians 1 i varje punkt för alla N (Jarrins normering med
    `∫f² = 1`); N = ⌈V_B/σ³⌉. Reynoldsspänningarna är isotropa och diagonala,
    `R = (I · U(z))² δ`, genom att anroparen skalar `s` med `I · U(z)` (Choleskyfaktorn
    av en diagonal tensor). Integrala längdskalan för formfunktionen är
    `L = ∫₀^∞ ρ(r) dr = (∫f)² / (2 ∫f²) · σ = 0,75 σ` (härlett ur f), så `σ = L / 0,75`.

    Parametrar
    ----------
    ny, nz:
        Inflödesplanets storlek.
    hastighet_lat:
        (nz,) inflödesprofilen i gitterenheter (konvektionshastighet per lager).
    langdskala_celler:
        Integral längdskala `L` i celler. Kräver `σ = L/0,75 ≥ 1` och `σ ≤ ny/2`.
    seed:
        Slumpfrö.
    """

    LANGD_PER_SIGMA = 0.75

    def __init__(
        self,
        ny: int,
        nz: int,
        hastighet_lat: FloatArray,
        langdskala_celler: float,
        seed: int,
    ) -> None:
        sigma = float(langdskala_celler) / self.LANGD_PER_SIGMA
        if sigma < 1.0:
            raise LuftFel(
                f"Inflödets längdskala {langdskala_celler:.2f} celler ger σ = {sigma:.2f} < 1 "
                "cell – virvlarna upplöses inte. Öka längdskalan eller förfina rutnätet."
            )
        if sigma > ny / 2:
            raise LuftFel(
                f"σ = {sigma:.1f} celler > ny/2 = {ny / 2:.1f}: virvlarna ryms inte i den "
                "periodiska bredden. Minska längdskalan eller bredda domänen."
            )
        hast = np.asarray(hastighet_lat, dtype=np.float64)
        if hast.shape != (nz,) or not np.all(np.isfinite(hast)) or np.any(hast <= 0):
            raise LuftFel("hastighet_lat måste ha formen (nz,) och vara positiv.")
        self.ny, self.nz, self.sigma = ny, nz, sigma
        self.hast = hast
        self.rng = np.random.default_rng(seed)
        self.z_min, self.z_max = -sigma, nz - 1.0 + sigma
        volym = 2.0 * sigma * ny * (self.z_max - self.z_min)
        self.n_virvlar = max(1, math.ceil(volym / sigma**3))
        self.skala = math.sqrt(volym / sigma**3) / math.sqrt(self.n_virvlar)
        n = self.n_virvlar
        self.x = self.rng.uniform(-sigma, sigma, n)
        self.y = self.rng.uniform(0.0, ny, n)
        self.z = self.rng.uniform(self.z_min, self.z_max, n)
        self.eps = self.rng.choice(np.array([-1.0, 1.0]), size=(n, 3))
        self._yj = np.arange(ny, dtype=np.float64)
        self._zk = np.arange(nz, dtype=np.float64)
        # Nyfödda virvlar dras med täthet ∝ U(z): uppehållstiden i lådan är ∝ 1/U(z), så
        # då blir virveltätheten i lådan likformig och variansen 1 på alla höjder.
        zg = np.linspace(self.z_min, self.z_max, 513)
        ug = self._konvektion(zg)
        kum = np.concatenate([[0.0], np.cumsum(0.5 * (ug[1:] + ug[:-1]) * np.diff(zg))])
        self._z_rutnat = zg
        self._z_kum = kum / kum[-1]

    def _konvektion(self, z: np.ndarray[Any, np.dtype[np.float64]]) -> Any:
        return np.interp(z, self._zk, self.hast)

    def signal(self) -> np.ndarray[Any, np.dtype[np.float64]]:
        """Nuvarande signal (3, ny, nz) med medel 0 och varians 1."""
        s = self.sigma
        fx = _talt(self.x / s)
        dy = self._yj[None, :] - self.y[:, None]
        dy -= self.ny * np.round(dy / self.ny)  # periodiskt minsta avstånd
        fy = _talt(dy / s)  # (N, ny)
        fz = _talt((self._zk[None, :] - self.z[:, None]) / s)  # (N, nz)
        ut = np.empty((3, self.ny, self.nz))
        for c in range(3):
            ut[c] = (fy.T * (self.eps[:, c] * fx)) @ fz
        ut *= self.skala
        return ut

    def nasta(self) -> np.ndarray[Any, np.dtype[np.float64]]:
        """Flyttar virvlarna ett tidssteg och returnerar signalen (3, ny, nz)."""
        self.x += self._konvektion(self.z)
        ute = self.x > self.sigma
        m = int(np.count_nonzero(ute))
        if m:
            self.x[ute] -= 2.0 * self.sigma
            self.y[ute] = self.rng.uniform(0.0, self.ny, m)
            self.z[ute] = np.interp(self.rng.uniform(0.0, 1.0, m), self._z_kum, self._z_rutnat)
            self.eps[ute] = self.rng.choice(np.array([-1.0, 1.0]), size=(m, 3))
        return self.signal()


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
    if isinstance(param.u_lattice, str):
        ul = valj_u_lattice(grid, inflode, param.mach_mal, param.mach_faktor)
    else:
        ul = float(param.u_lattice)
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

    z0_vagg_m = inflode.z0_m if param.z0_vagg_m is None else param.z0_vagg_m
    alfa = param.cd_vegetation * grid.lad_m2_m3 * dx
    losare = Lbm3d(
        grid.fast,
        tau0=tau0,
        smagorinsky_cs=param.smagorinsky_cs,
        xp=xp,
        alfa_vegetation=alfa,
        x_periodisk=False,
        vaggmodell=param.vaggmodell,
        z0_vagg_celler=z0_vagg_m / dx,
        vagg_provhojd_celler=param.vagg_provhojd_celler,
        inflodesrand=param.inflodesrand,
        utflodesrand=param.utflodesrand,
    )
    starta_med_profil = param.start == "profil"
    if starta_med_profil:
        u0 = np.zeros((3, nx, ny, nz), dtype=np.float32)
        u0[0] = u_in_z[None, None, :]
        losare.initiera(u0)
    else:
        losare.initiera(None)

    profil = np.zeros((3, ny, nz))
    profil[0] = u_in_z[None, :]
    storning: _Storning | SyntetiskaVirvlar | None = None
    if inflode.turbulensintensitet > 0:
        if param.inflodesturbulens == "virvlar":
            if param.inflode_langdskala_m is None:
                raise LuftFel("inflodesturbulens='virvlar' kräver inflode_langdskala_m.")
            storning = SyntetiskaVirvlar(
                ny,
                nz,
                np.maximum(u_in_z, 1e-3 * ul),
                param.inflode_langdskala_m / dx,
                seed=param.storning_seed,
            )
        else:
            storning = _Storning(ny, nz, korrelation_steg=4.0 / ul, seed=param.storning_seed)
    n_ramp_inflode = max(1, n_ramp // 2)

    sum_u = xp.zeros((3, losare.n), dtype=xp.float64)
    sum_uu = xp.zeros((6, losare.n), dtype=xp.float64)
    sum_nut = xp.zeros(losare.n, dtype=xp.float64)
    sum_uh = xp.zeros(losare.n, dtype=xp.float64)
    sum_uh2 = xp.zeros(losare.n, dtype=xp.float64)
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
        f"u_lattice={ul:.4g}, tau0={tau0:.6f}, steg: {n_ramp} upprampning + {n_medel} medel, "
        f"väggmodell={param.vaggmodell} ({losare.n_vagg} celler), backend={backend}"
    )
    for n in range(n_tot):
        r = math.sin(0.5 * math.pi * (n + 1) / n_ramp_inflode) ** 2 if n < n_ramp_inflode else 1.0
        u_in = profil if starta_med_profil else profil * r
        if storning is not None:
            u_in = u_in + inflode.turbulensintensitet * r * u_in_z[None, None, :] * storning.nasta()
        losare.satt_inflode(xp.asarray(u_in))
        losare.steg()

        if n >= n_ramp and (n - n_ramp) % param.medel_intervall_steg == 0:
            u = losare.u
            sum_u += u
            for p, (a, b) in enumerate(_PAR):
                sum_uu[p] += u[a].astype(xp.float64) * u[b]
            uh = xp.sqrt(u[0].astype(xp.float64) ** 2 + u[1].astype(xp.float64) ** 2)
            sum_uh += uh
            sum_uh2 += uh * uh
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
    medel_uh = till_numpy(sum_uh / n_prov).reshape(nx, ny, nz)
    medel_uh2 = till_numpy(sum_uh2 / n_prov).reshape(nx, ny, nz)
    u_std = np.sqrt(np.maximum(medel_uh2 - medel_uh**2, 0.0)) * hast_skala * luft
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
        "u_lattice": ul,
        "vaggmodell": param.vaggmodell,
        "z0_vagg_m": z0_vagg_m if param.vaggmodell != "ingen" else None,
        "inflodesturbulens": param.inflodesturbulens if storning is not None else None,
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
        u_std_m_s=u_std.astype(np.float64),
    )
