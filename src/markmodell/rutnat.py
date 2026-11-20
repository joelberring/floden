"""Rutnät i SWEREF 99 18 00 (EPSG:3011) och skarvkontroll mellan rutor."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


class RutnatFel(Exception):
    """Fel vid definition eller hantering av markmodellens rutnät."""


@dataclass(frozen=True)
class Ruta:
    """En markmodell-ruta i EPSG:3011 med valfritt överlapp.

    Standardmått enligt kontrakt K1/v1 är 500 x 500 m inre yta med 25 m
    överlapp åt alla håll och 0,25 m pixelupplösning.
    """

    e_min_m: float
    n_min_m: float
    storlek_m: float = 500.0
    overlapp_m: float = 25.0
    upplosning_m: float = 0.25

    def __post_init__(self) -> None:
        if not math.isfinite(self.e_min_m) or not math.isfinite(self.n_min_m):
            raise RutnatFel("Rutans hörnkoordinater måste vara ändliga tal.")
        if not math.isfinite(self.storlek_m) or self.storlek_m <= 0.0:
            raise RutnatFel(f"Ogiltig rutstorlek: {self.storlek_m}")
        if not math.isfinite(self.overlapp_m) or self.overlapp_m < 0.0:
            raise RutnatFel(f"Ogiltigt överlapp: {self.overlapp_m}")
        if not math.isfinite(self.upplosning_m) or self.upplosning_m <= 0.0:
            raise RutnatFel(f"Ogiltig upplösning: {self.upplosning_m}")

        antal_rader_f = (self.storlek_m + 2.0 * self.overlapp_m) / self.upplosning_m
        antal_rader = round(antal_rader_f)
        if not math.isclose(antal_rader_f, antal_rader, rel_tol=0.0, abs_tol=1e-9):
            raise RutnatFel(
                "Rutans totala bredd (storlek_m + 2 * overlapp_m) måste vara "
                "jämnt delbar med upplosning_m."
            )
        overlapp_celler = self.overlapp_m / self.upplosning_m
        if not math.isclose(overlapp_celler, round(overlapp_celler), rel_tol=0.0, abs_tol=1e-9):
            raise RutnatFel("Överlapp måste vara jämnt delbart med upplosning_m.")

    @property
    def namn(self) -> str:
        """Rutans namn enligt K1: `mm_<E>_<N>` (sydvästra inre hörnet i hela meter)."""
        return f"mm_{int(round(self.e_min_m))}_{int(round(self.n_min_m))}"

    @property
    def inre_bbox(self) -> tuple[float, float, float, float]:
        """Inre avgränsning utan överlapp: `(e_min, n_min, e_max, n_max)`."""
        return (
            self.e_min_m,
            self.n_min_m,
            self.e_min_m + self.storlek_m,
            self.n_min_m + self.storlek_m,
        )

    @property
    def yttre_bbox(self) -> tuple[float, float, float, float]:
        """Yttre avgränsning inklusive överlapp: `(e_min, n_min, e_max, n_max)`."""
        return (
            self.e_min_m - self.overlapp_m,
            self.n_min_m - self.overlapp_m,
            self.e_min_m + self.storlek_m + self.overlapp_m,
            self.n_min_m + self.storlek_m + self.overlapp_m,
        )

    @property
    def form_rc(self) -> tuple[int, int]:
        """Rasterdimensioner `(antal_rader, antal_kolumner)` för yttre bbox."""
        n = int(round((self.storlek_m + 2.0 * self.overlapp_m) / self.upplosning_m))
        return (n, n)

    @property
    def affine_parametrar(self) -> tuple[float, float, float, float, float, float]:
        """Affine-parametrar `(a, b, c, d, e, f)` i norr-upp orientering.

        Motsvarar `rasterio.transform.from_origin(e_min_yttre, n_max_yttre, dx, dy)`.
        """
        e_min, _, _, n_max = self.yttre_bbox
        return (self.upplosning_m, 0.0, e_min, 0.0, -self.upplosning_m, n_max)

    def koordinat_till_cell(self, e_m: float, n_m: float) -> tuple[int, int]:
        """Omvandlar `(e_m, n_m)` i EPSG:3011 till rasterindex `(rad, kol)`.

        Rad 0 är överst (längst norrut i yttre bbox) och kolumn 0 är längst västerut.
        """
        e_min, _, _, n_max = self.yttre_bbox
        kol = int(math.floor((e_m - e_min) / self.upplosning_m))
        rad = int(math.floor((n_max - n_m) / self.upplosning_m))
        return rad, kol

    def cell_till_koordinat(self, rad: int, kol: int) -> tuple[float, float]:
        """Returnerar cellcentrum `(e_m, n_m)` i EPSG:3011 för `(rad, kol)`."""
        e_min, _, _, n_max = self.yttre_bbox
        e_m = e_min + (kol + 0.5) * self.upplosning_m
        n_m = n_max - (rad + 0.5) * self.upplosning_m
        return e_m, n_m

    def cell_centra_en(self) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """Returnerar 1D-vektorer `(e_vektor, n_vektor)` med cellcentra längs kol/rad."""
        rader, kolumner = self.form_rc
        e_min, _, _, n_max = self.yttre_bbox
        e_vektor = e_min + (np.arange(kolumner, dtype=np.float64) + 0.5) * self.upplosning_m
        n_vektor = n_max - (np.arange(rader, dtype=np.float64) + 0.5) * self.upplosning_m
        return e_vektor, n_vektor

    def ar_inom_raster(self, rad: int, kol: int) -> bool:
        """Returnerar True om `(rad, kol)` ligger innanför yttre rastret."""
        rader, kolumner = self.form_rc
        return 0 <= rad < rader and 0 <= kol < kolumner


@dataclass(frozen=True)
class SkarvRapport:
    """Resultat från skarvkontroll i gemensamt överlapp mellan två rutor."""

    ruta_a: str
    ruta_b: str
    antal_gemensamma_celler: int
    antal_nan: int
    max_avvikelse_m: float
    medel_avvikelse_m: float
    godkand: bool
    meddelande: str


def skapa_rutnat(
    bbox_epsg3011: tuple[float, float, float, float],
    storlek_m: float = 500.0,
    overlapp_m: float = 25.0,
    upplosning_m: float = 0.25,
) -> list[Ruta]:
    """Skapar ett konsekvent rutnät i EPSG:3011 som täcker hela `bbox_epsg3011`.

    Rutorna justeras mot multiplar av `storlek_m` från origo (0, 0) i EPSG:3011
    så att intilliggande körningar alltid får identiska rutgränser och namn.
    """
    e_min, n_min, e_max, n_max = bbox_epsg3011
    if not all(math.isfinite(v) for v in bbox_epsg3011):
        raise RutnatFel("Områdets bbox måste innehålla ändliga koordinater.")
    if e_max <= e_min or n_max <= n_min:
        raise RutnatFel(f"Ogiltig bbox (e_max <= e_min eller n_max <= n_min): {bbox_epsg3011}")
    if not math.isfinite(storlek_m) or storlek_m <= 0.0:
        raise RutnatFel(f"Ogiltig rutstorlek: {storlek_m}")

    e_idx_start = math.floor(e_min / storlek_m)
    e_idx_slut = math.ceil(e_max / storlek_m)
    n_idx_start = math.floor(n_min / storlek_m)
    n_idx_slut = math.ceil(n_max / storlek_m)

    rutor: list[Ruta] = []
    for n_idx in range(n_idx_start, n_idx_slut):
        for e_idx in range(e_idx_start, e_idx_slut):
            rutor.append(
                Ruta(
                    e_min_m=e_idx * storlek_m,
                    n_min_m=n_idx * storlek_m,
                    storlek_m=storlek_m,
                    overlapp_m=overlapp_m,
                    upplosning_m=upplosning_m,
                )
            )
    return rutor


def kontrollera_skarv(
    ruta_a: Ruta,
    raster_a: NDArray[np.float32] | NDArray[np.float64],
    ruta_b: Ruta,
    raster_b: NDArray[np.float32] | NDArray[np.float64],
    max_avvikelse_m: float = 0.005,
) -> SkarvRapport:
    """Kontrollerar höjdskarv och hål (NaN) i överlappet mellan två rutor."""
    if raster_a.shape != ruta_a.form_rc:
        raise RutnatFel(
            f"Raster för {ruta_a.namn} har form {raster_a.shape}, förväntade {ruta_a.form_rc}."
        )
    if raster_b.shape != ruta_b.form_rc:
        raise RutnatFel(
            f"Raster för {ruta_b.namn} har form {raster_b.shape}, förväntade {ruta_b.form_rc}."
        )
    if not math.isclose(ruta_a.upplosning_m, ruta_b.upplosning_m, rel_tol=0.0, abs_tol=1e-9):
        raise RutnatFel("Rutorna måste ha samma upplösning för skarvkontroll.")

    uppl = ruta_a.upplosning_m
    ea_min, na_min, ea_max, na_max = ruta_a.yttre_bbox
    eb_min, nb_min, eb_max, nb_max = ruta_b.yttre_bbox

    gem_e_min = max(ea_min, eb_min)
    gem_e_max = min(ea_max, eb_max)
    gem_n_min = max(na_min, nb_min)
    gem_n_max = min(na_max, nb_max)

    if gem_e_max <= gem_e_min + 1e-9 or gem_n_max <= gem_n_min + 1e-9:
        return SkarvRapport(
            ruta_a=ruta_a.namn,
            ruta_b=ruta_b.namn,
            antal_gemensamma_celler=0,
            antal_nan=0,
            max_avvikelse_m=0.0,
            medel_avvikelse_m=0.0,
            godkand=False,
            meddelande="Rutorna har inget gemensamt överlapp att kontrollera.",
        )

    kol_a0 = round((gem_e_min - ea_min) / uppl)
    kol_a1 = round((gem_e_max - ea_min) / uppl)
    rad_a0 = round((na_max - gem_n_max) / uppl)
    rad_a1 = round((na_max - gem_n_min) / uppl)

    kol_b0 = round((gem_e_min - eb_min) / uppl)
    kol_b1 = round((gem_e_max - eb_min) / uppl)
    rad_b0 = round((nb_max - gem_n_max) / uppl)
    rad_b1 = round((nb_max - gem_n_min) / uppl)

    utsnitt_a = np.asarray(raster_a[rad_a0:rad_a1, kol_a0:kol_a1], dtype=np.float64)
    utsnitt_b = np.asarray(raster_b[rad_b0:rad_b1, kol_b0:kol_b1], dtype=np.float64)

    if utsnitt_a.shape != utsnitt_b.shape or utsnitt_a.size == 0:
        raise RutnatFel(
            f"Oförenliga utsnitt i skarv: {utsnitt_a.shape} vs {utsnitt_b.shape}."
        )

    ogiltiga = ~np.isfinite(utsnitt_a) | ~np.isfinite(utsnitt_b)
    antal_nan = int(np.count_nonzero(ogiltiga))
    antal_celler = int(utsnitt_a.size)

    if antal_nan == antal_celler:
        return SkarvRapport(
            ruta_a=ruta_a.namn,
            ruta_b=ruta_b.namn,
            antal_gemensamma_celler=antal_celler,
            antal_nan=antal_nan,
            max_avvikelse_m=float("inf"),
            medel_avvikelse_m=float("inf"),
            godkand=False,
            meddelande=f"Alla {antal_nan} celler i överlappet saknar giltigt värde.",
        )

    giltiga_diff = np.abs(utsnitt_a[~ogiltiga] - utsnitt_b[~ogiltiga])
    max_diff = float(np.max(giltiga_diff))
    medel_diff = float(np.mean(giltiga_diff))

    godkand = antal_nan == 0 and max_diff <= max_avvikelse_m
    if antal_nan > 0:
        meddelande = (
            f"Skarv underkänd: {antal_nan} celler med NaN/Inf i överlapp "
            f"(max höjdavvikelse {max_diff:.4f} m)."
        )
    elif max_diff > max_avvikelse_m:
        meddelande = (
            f"Skarv underkänd: max avvikelse {max_diff:.4f} m överstiger "
            f"gränsen {max_avvikelse_m:.4f} m."
        )
    else:
        meddelande = (
            f"Skarv godkänd över {antal_celler} celler "
            f"(max avvikelse {max_diff:.4f} m)."
        )

    return SkarvRapport(
        ruta_a=ruta_a.namn,
        ruta_b=ruta_b.namn,
        antal_gemensamma_celler=antal_celler,
        antal_nan=antal_nan,
        max_avvikelse_m=max_diff,
        medel_avvikelse_m=medel_diff,
        godkand=godkand,
        meddelande=meddelande,
    )
