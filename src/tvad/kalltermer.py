"""Käll- och sänktermer per cell för 2D-lösaren (systemdesign 4.2, fas 3).

Termerna ligger utanför själva flödesberäkningen i `tvad.lokal_troghet` och påverkar bara
cellernas volym, en gång per tidssteg, i fast ordning efter regntillskottet:

1. **Initial ytmagasinering** (interception, småsänkor): fylls först, upp till `initial_m` per cell.
2. **Infiltration** (Green–Ampt per cell): potentiell ΔF ur den implicita Green–Ampt-ekvationen
   under dammbildning (samma ekvation som `mark_forluster.infiltration`), begränsad av vattnet
   som står i cellen och av markens lagringstak.
3. **Avtappning till dagvattennätet**: kapacitet i m/s per cell (från mm/h per område),
   begränsad av vattnet som står i cellen.

Allt som tas bort summeras (m³) och ingår i volymbalansen:
`in = ut + lagrat + initial + infiltrerat + avtappat`.

Varje term är en ren funktion av (volym, tillstånd, dt) per cell, utan grannar, så den kan
portas rakt av till en kompilerad kärna (Rust/GPU): se `SanktermTillstand.tillampa`.

Inga standardvärden används skarpt: kapacitet och markparametrar är indata. Saknas de körs
lösaren utan termen och resultatet säger det (`TvadResultat.sanktermer`).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from mark_forluster.parametrar import JordartParametrar, effektiv_markparametrar

_MM_H_TILL_M_S = 1.0 / 1000.0 / 3600.0


class KalltermFel(ValueError):
    """Ogiltiga käll-/sänktermer."""


@dataclass(frozen=True)
class Avtappning:
    """Avtappning till dagvattennätet.

    `kapacitet_mm_h` är kapaciteten per cell på det fina rutnätet (mm/h räknat på cellens yta).
    `brunnar_igensatta_andel` (K5) minskar kapaciteten likformigt:
    effektiv = kapacitet × (1 − andel).
    Celler med NaN eller 0 har ingen avtappning.
    """

    kapacitet_mm_h: NDArray[np.float64]
    brunnar_igensatta_andel: float = 0.0
    kalla: str = "indata"

    def __post_init__(self) -> None:
        kap = np.asarray(self.kapacitet_mm_h, dtype=np.float64)
        if kap.ndim != 2:
            raise KalltermFel("kapacitet_mm_h måste vara ett 2D-raster.")
        if np.any(kap[np.isfinite(kap)] < 0.0):
            raise KalltermFel("Avtappningskapaciteten får inte vara negativ.")
        andel = float(self.brunnar_igensatta_andel)
        if not (math.isfinite(andel) and 0.0 <= andel <= 1.0):
            raise KalltermFel(f"brunnar_igensatta_andel måste ligga i [0, 1], fick {andel}.")
        object.__setattr__(self, "kapacitet_mm_h", kap)

    def effektiv_m_s(self) -> NDArray[np.float64]:
        kap = np.where(np.isfinite(self.kapacitet_mm_h), self.kapacitet_mm_h, 0.0)
        return np.asarray(
            kap * _MM_H_TILL_M_S * (1.0 - float(self.brunnar_igensatta_andel)), dtype=np.float64
        )

    @staticmethod
    def likformig(form: tuple[int, int], kapacitet_mm_h: float, **kw: Any) -> Avtappning:
        return Avtappning(np.full(form, float(kapacitet_mm_h)), **kw)


def kapacitet_fran_omraden(
    form: tuple[int, int],
    x_min: float,
    y_max: float,
    pixelstorlek_m: float,
    omraden: Sequence[tuple[Mapping[str, Any], float]],
) -> NDArray[np.float64]:
    """Rasteriserar (GeoJSON-polygon i EPSG:3011, kapacitet mm/h) till ett kapacitetsraster.

    Celler vars mittpunkt ligger i flera områden får det senast angivna området. Celler utanför
    alla områden får 0 (ingen avtappning) – inget antaget värde.
    """
    from shapely import contains_xy
    from shapely.geometry import shape

    hojd, bredd = form
    kol = x_min + (np.arange(bredd) + 0.5) * pixelstorlek_m
    rad = y_max - (np.arange(hojd) + 0.5) * pixelstorlek_m
    xx, yy = np.meshgrid(kol, rad)
    ut = np.zeros(form, dtype=np.float64)
    for geometri, kapacitet in omraden:
        if not (math.isfinite(float(kapacitet)) and float(kapacitet) >= 0.0):
            raise KalltermFel(f"Ogiltig kapacitet {kapacitet} mm/h.")
        poly = shape(dict(geometri))
        ut[contains_xy(poly, xx, yy)] = float(kapacitet)
    return ut


@dataclass(frozen=True)
class Infiltration2D:
    """Green–Ampt-parametrar per cell på det fina rutnätet (samma storheter som i 0D-modellen)."""

    ksat_mm_h: NDArray[np.float64]
    psi_m: NDArray[np.float64]
    delta_theta: NDArray[np.float64]
    initial_mm: NDArray[np.float64]
    max_lagring_m: NDArray[np.float64] | None = None
    kalla: str = "indata"

    def __post_init__(self) -> None:
        form = np.asarray(self.ksat_mm_h).shape
        for namn in ("ksat_mm_h", "psi_m", "delta_theta", "initial_mm"):
            arr = np.asarray(getattr(self, namn), dtype=np.float64)
            if arr.shape != form or arr.ndim != 2:
                raise KalltermFel(f"{namn} måste vara ett 2D-raster med samma form.")
            if not np.all(np.isfinite(arr)) or np.any(arr < 0.0):
                raise KalltermFel(f"{namn} måste vara ändlig och >= 0 i alla celler.")
            object.__setattr__(self, namn, arr)
        if self.max_lagring_m is not None:
            ml = np.asarray(self.max_lagring_m, dtype=np.float64)
            if ml.shape != form or np.any(ml < 0.0):
                raise KalltermFel("max_lagring_m måste ha samma form och vara >= 0.")
            object.__setattr__(self, "max_lagring_m", ml)

    @staticmethod
    def fran_markslag(
        markslag: NDArray[np.uint8],
        jordart: str | int,
        *,
        initial_mm: float,
        hog_markfukt: bool = False,
        tjale: bool = False,
        tabell: dict[str, JordartParametrar] | None = None,
    ) -> Infiltration2D:
        """Parametrar ur K1-markslag och en jordart via `mark_forluster.effektiv_markparametrar`.

        `initial_mm` är bas för ytmagasineringen och skalas med markslagets `initial_faktor`
        (samma regel som i 0D-modellen).
        """
        ms = np.asarray(markslag, dtype=np.uint8)
        ksat = np.zeros(ms.shape)
        psi = np.zeros(ms.shape)
        dth = np.zeros(ms.shape)
        init = np.zeros(ms.shape)
        for kod in np.unique(ms):
            p = effektiv_markparametrar(
                int(kod), jordart, hog_markfukt=hog_markfukt, tjale=tjale, tabell=tabell
            )
            m = ms == kod
            ksat[m], psi[m], dth[m] = p.ksat_mm_h, p.psi_m, p.delta_theta
            init[m] = float(initial_mm) * p.initial_faktor
        return Infiltration2D(ksat, psi, dth, init, kalla=f"markslag+jordart:{jordart}")


@dataclass(frozen=True)
class TvingatDjup:
    """Randvillkor: djupet i `mask`-cellerna sätts till `djup_m(t)` efter varje tidssteg.

    Volymen som behövs för det räknas som inflöde (eller utflöde om den minskar) i balansen.
    Används för inflödesränder och analytiska testfall (Hunter m.fl. 2005).
    """

    mask: NDArray[np.bool_]
    djup_funktion: Callable[[float], float]

    def djup_m(self, t_s: float) -> float:
        d = float(self.djup_funktion(t_s))
        if not (math.isfinite(d) and d >= 0.0):
            raise KalltermFel(f"Tvingat djup måste vara ändligt och >= 0, fick {d} vid t={t_s}.")
        return d


@dataclass(frozen=True)
class Kalltermer:
    """Samlade käll-/sänktermer till `kor_2d_lokal_troghet(..., kalltermer=...)`."""

    avtappning: Avtappning | None = None
    infiltration: Infiltration2D | None = None
    tvingat_djup: TvingatDjup | None = None

    def beskrivning(self) -> dict[str, Any]:
        """Vad som användes, för manifestet (säger uttryckligen när en term saknas)."""
        return {
            "avtappning": (
                "saknas – körd utan avtappning"
                if self.avtappning is None
                else {
                    "kalla": self.avtappning.kalla,
                    "brunnar_igensatta_andel": float(self.avtappning.brunnar_igensatta_andel),
                }
            ),
            "infiltration": (
                "saknas – körd utan infiltration och initial förlust"
                if self.infiltration is None
                else {"modell": "green-ampt", "kalla": self.infiltration.kalla}
            ),
            "tvingat_djup": self.tvingat_djup is not None,
        }


def _blockmedel(
    fin: NDArray[np.float64], vikt: NDArray[np.float64], faktor: int, form_grov: tuple[int, int]
) -> NDArray[np.float64]:
    """Viktat medel över `faktor × faktor`-block (vikt = aktiv fin cellyta)."""
    hg, bg = form_grov
    pad_h, pad_b = hg * faktor - fin.shape[0], bg * faktor - fin.shape[1]
    f = np.pad(fin, ((0, pad_h), (0, pad_b)))
    w = np.pad(vikt, ((0, pad_h), (0, pad_b)))
    fb = (f * w).reshape(hg, faktor, bg, faktor).sum(axis=(1, 3))
    wb = w.reshape(hg, faktor, bg, faktor).sum(axis=(1, 3))
    return np.asarray(np.where(wb > 0.0, fb / np.where(wb > 0.0, wb, 1.0), 0.0), dtype=np.float64)


def _los_green_ampt_vektor(
    f0: NDArray[np.float64], kdt: NDArray[np.float64], psd: NDArray[np.float64]
) -> NDArray[np.float64]:
    """Vektoriserad lösning av F − F0 − ψΔθ·ln((F+ψΔθ)/(F0+ψΔθ)) = K·dt (Newton, monoton).

    Samma ekvation och startgissning som `mark_forluster.infiltration._los_implicit_green_ampt`.
    """
    ut = f0 + kdt
    m = (kdt > 0.0) & (psd > 0.0)
    if not np.any(m):
        return ut
    f_s, k_s, p_s = f0[m], kdt[m], psd[m]
    bas = f_s + p_s
    f = f_s + k_s + np.sqrt(2.0 * p_s * k_s)
    for _ in range(40):
        g = f - f_s - p_s * np.log((f + p_s) / bas) - k_s
        d = f / (f + p_s)
        f_ny = np.maximum(f_s, f - g / np.where(d > 0.0, d, 1.0))
        klar = np.abs(f_ny - f) <= 1e-13 * np.maximum(1.0, f_ny)
        f = f_ny
        if bool(np.all(klar)):
            break
    ut[m] = f
    return ut


@dataclass
class SanktermTillstand:
    """Termernas tillstånd på lösarens rutnät (fint eller grovt vid subgrid)."""

    kap_m_s: NDArray[np.float64] | None
    ksat_m_s: NDArray[np.float64] | None
    psi_dtheta_m: NDArray[np.float64] | None
    initial_tak_m: NDArray[np.float64] | None
    lagring_tak_m: NDArray[np.float64] | None
    initial_fylld_m: NDArray[np.float64] = field(default_factory=lambda: np.zeros((0, 0)))
    infiltrerat_m: NDArray[np.float64] = field(default_factory=lambda: np.zeros((0, 0)))
    avtappat_m3: float = 0.0
    infiltrerat_m3: float = 0.0
    initial_m3: float = 0.0

    @staticmethod
    def bygg(
        kalltermer: Kalltermer,
        form: tuple[int, int],
        *,
        aktiv_area_fin: NDArray[np.float64] | None = None,
        subgrid_faktor: int = 1,
    ) -> SanktermTillstand:
        """Termerna på lösarens rutnät. Vid subgrid aggregeras de fina rastren (ytviktat medel)."""

        def till_rutnat(fin: NDArray[np.float64]) -> NDArray[np.float64]:
            if subgrid_faktor == 1:
                if fin.shape != form:
                    raise KalltermFel(f"Termraster har form {fin.shape}, lösaren {form}.")
                return np.asarray(fin, dtype=np.float64)
            assert aktiv_area_fin is not None
            if fin.shape != aktiv_area_fin.shape:
                raise KalltermFel(
                    f"Termraster har form {fin.shape}, fina rutnätet {aktiv_area_fin.shape}."
                )
            return _blockmedel(fin, aktiv_area_fin, subgrid_faktor, form)

        kap = None
        if kalltermer.avtappning is not None:
            kap = till_rutnat(kalltermer.avtappning.effektiv_m_s())
        ksat = psd = init = tak = None
        inf = kalltermer.infiltration
        if inf is not None:
            ksat = till_rutnat(inf.ksat_mm_h * _MM_H_TILL_M_S)
            psd = till_rutnat(inf.psi_m * inf.delta_theta)
            init = till_rutnat(inf.initial_mm / 1000.0)
            if inf.max_lagring_m is not None:
                tak = till_rutnat(inf.max_lagring_m)
        return SanktermTillstand(
            kap_m_s=kap,
            ksat_m_s=ksat,
            psi_dtheta_m=psd,
            initial_tak_m=init,
            lagring_tak_m=tak,
            initial_fylld_m=np.zeros(form),
            infiltrerat_m=np.zeros(form),
        )

    def tillampa(
        self, volym: NDArray[np.float64], area: NDArray[np.float64], dt: float
    ) -> None:
        """Tar bort vatten ur `volym` (m³, ändras på plats) under `dt` sekunder.

        Ordning: initial ytmagasinering → infiltration → avtappning. Varje steg begränsas av
        vattnet som finns kvar i cellen, så volymen blir aldrig negativ.
        """
        if (
            self.initial_tak_m is None
            and self.ksat_m_s is None
            and self.kap_m_s is None
        ):
            return
        aktiv = area > 0.0
        safe = np.where(aktiv, area, 1.0)
        djup = np.where(aktiv, volym / safe, 0.0)

        if self.initial_tak_m is not None:
            ledigt = np.maximum(0.0, self.initial_tak_m - self.initial_fylld_m)
            d = np.minimum(djup, ledigt)
            self.initial_fylld_m += d
            djup -= d
            self.initial_m3 += float(np.sum(d * area))

        if self.ksat_m_s is not None and self.psi_dtheta_m is not None:
            m = (self.ksat_m_s > 0.0) & (djup > 0.0)
            if np.any(m):
                f0 = self.infiltrerat_m[m]
                f_pot = _los_green_ampt_vektor(f0, self.ksat_m_s[m] * dt, self.psi_dtheta_m[m])
                d = np.minimum(f_pot - f0, djup[m])
                if self.lagring_tak_m is not None:
                    d = np.minimum(d, np.maximum(0.0, self.lagring_tak_m[m] - f0))
                d = np.maximum(d, 0.0)
                self.infiltrerat_m[m] = f0 + d
                djup[m] -= d
                self.infiltrerat_m3 += float(np.sum(d * area[m]))

        if self.kap_m_s is not None:
            d = np.minimum(djup, self.kap_m_s * dt)
            djup -= d
            self.avtappat_m3 += float(np.sum(d * area))

        volym[...] = np.where(aktiv, np.maximum(djup, 0.0) * area, volym)
