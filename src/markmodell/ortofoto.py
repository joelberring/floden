"""Deterministisk flygbildstolkning (ortofotoklassning) och förändringsdetektering mot laser."""

from __future__ import annotations

import base64
import io
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from markmodell.hinder_oppningar import (
    BERG,
    BYGGNAD,
    GRAS,
    GRONT_TAK,
    GRUS,
    HARDGJORT,
    OKANT,
    SKOG,
    TAK,
    VATTEN,
)

_MARKSLAG_NAMN: dict[int, str] = {
    OKANT: "okant",
    HARDGJORT: "hardgjort",
    TAK: "tak",
    GRONT_TAK: "gront_tak",
    GRAS: "gras",
    SKOG: "skog",
    BERG: "berg",
    GRUS: "grus",
    VATTEN: "vatten",
    BYGGNAD: "byggnad",
}

_MARKSLAG_PALETT_RGBA: dict[int, tuple[int, int, int, int]] = {
    OKANT: (128, 128, 128, 255),
    HARDGJORT: (75, 80, 88, 255),
    TAK: (176, 88, 72, 255),
    GRONT_TAK: (122, 184, 96, 255),
    GRAS: (96, 168, 82, 255),
    SKOG: (38, 102, 52, 255),
    BERG: (148, 142, 134, 255),
    GRUS: (212, 188, 140, 255),
    VATTEN: (56, 136, 212, 255),
    BYGGNAD: (196, 68, 56, 255),
}


class OrtofotoFel(Exception):
    """Fel vid validering, klassning eller förändringsdetektering i ortofoto."""


@dataclass(frozen=True)
class OrtofotoParametrar:
    """Deterministiska tröskelvärden och osäkerhetstillägg för ortofotoklassning."""

    ndvi_vegetation_troskel: float = 0.22
    exg_vegetation_troskel: float = 0.08
    ndwi_vatten_troskel: float = 0.15
    skugga_luminans_troskel: float = 0.12
    skog_ndsm_troskel_m: float = 2.0
    grus_ljushet_troskel: float = 0.55
    grus_matten_max: float = 0.22
    osakerhet_skugga_tillagg_m: float = 0.15
    osakerhet_forandring_tillagg_m: float = 0.25

    def __post_init__(self) -> None:
        varden = (
            ("ndvi_vegetation_troskel", self.ndvi_vegetation_troskel),
            ("exg_vegetation_troskel", self.exg_vegetation_troskel),
            ("ndwi_vatten_troskel", self.ndwi_vatten_troskel),
            ("skugga_luminans_troskel", self.skugga_luminans_troskel),
            ("skog_ndsm_troskel_m", self.skog_ndsm_troskel_m),
            ("grus_ljushet_troskel", self.grus_ljushet_troskel),
            ("grus_matten_max", self.grus_matten_max),
            ("osakerhet_skugga_tillagg_m", self.osakerhet_skugga_tillagg_m),
            ("osakerhet_forandring_tillagg_m", self.osakerhet_forandring_tillagg_m),
        )
        for namn, varde in varden:
            if not math.isfinite(varde):
                raise OrtofotoFel(f"Parameter '{namn}' måste vara ändlig, fick {varde}.")
        if not 0.0 <= self.skugga_luminans_troskel <= 1.0:
            raise OrtofotoFel(
                "skugga_luminans_troskel måste ligga i [0, 1], "
                f"fick {self.skugga_luminans_troskel}."
            )
        if not 0.0 <= self.grus_ljushet_troskel <= 1.0:
            raise OrtofotoFel(
                f"grus_ljushet_troskel måste ligga i [0, 1], fick {self.grus_ljushet_troskel}."
            )
        if not 0.0 <= self.grus_matten_max <= 1.0:
            raise OrtofotoFel(
                f"grus_matten_max måste ligga i [0, 1], fick {self.grus_matten_max}."
            )
        if self.skog_ndsm_troskel_m < 0.0:
            raise OrtofotoFel(
                f"skog_ndsm_troskel_m får inte vara negativ, fick {self.skog_ndsm_troskel_m}."
            )
        if self.osakerhet_skugga_tillagg_m < 0.0 or self.osakerhet_forandring_tillagg_m < 0.0:
            raise OrtofotoFel("Osäkerhetstillägg får inte vara negativa.")


@dataclass(frozen=True)
class OrtofotoKlassningResultat:
    """Resultat från deterministisk klassning av ortofoto i K1-markslag."""

    markslag: NDArray[np.uint8]
    konfidens: NDArray[np.float32]
    skuggmask: NDArray[np.bool_]
    vegetationsindex: NDArray[np.float32]
    har_nir: bool
    klass_andelar: dict[str, float]
    skugga_andel: float

    def sammanfattning_dict(self) -> dict[str, Any]:
        """Returnerar JSON-serialiserbar sammanfattning av klassningen."""
        rader, kolumner = self.markslag.shape
        return {
            "form_rc": [int(rader), int(kolumner)],
            "antal_pixlar": int(self.markslag.size),
            "har_nir": bool(self.har_nir),
            "skugga_andel": float(self.skugga_andel),
            "medel_konfidens": float(np.mean(self.konfidens)) if self.konfidens.size > 0 else 0.0,
            "klass_andelar": {k: float(v) for k, v in self.klass_andelar.items()},
        }


@dataclass(frozen=True)
class ForandringsRapport:
    """Rapport över markslags- och höjdförändringar mellan laserdata och nytt ortofoto."""

    forandringsmask: NDArray[np.bool_]
    nya_hardgjorda_mask: NDArray[np.bool_]
    nya_byggnader_mask: NDArray[np.bool_]
    andrad_area_m2: float
    nya_hardgjorda_m2: float
    nya_byggnader_m2: float
    varningar: tuple[str, ...]

    def till_dict(self) -> dict[str, Any]:
        """Returnerar JSON-serialiserbar sammanfattning av förändringsrapporten."""
        return {
            "andrad_area_m2": float(self.andrad_area_m2),
            "nya_hardgjorda_m2": float(self.nya_hardgjorda_m2),
            "nya_byggnader_m2": float(self.nya_byggnader_m2),
            "antal_andrade_pixlar": int(np.count_nonzero(self.forandringsmask)),
            "antal_nya_hardgjorda_pixlar": int(np.count_nonzero(self.nya_hardgjorda_mask)),
            "antal_nya_byggnader_pixlar": int(np.count_nonzero(self.nya_byggnader_mask)),
            "varningar": list(self.varningar),
        }


def _normalisera_ortofoto(
    bild: NDArray[np.floating[Any]] | NDArray[np.uint8],
) -> tuple[NDArray[np.float64], bool]:
    """Validerar ortofoto-array `(H, W, 3|4)` och normaliserar till float64 `[0, 1]`."""
    arr = np.asarray(bild)
    if arr.ndim != 3 or arr.shape[0] < 1 or arr.shape[1] < 1 or arr.shape[2] not in (3, 4):
        raise OrtofotoFel(
            f"Ortofoto måste ha form (H, W, 3) eller (H, W, 4), fick {arr.shape}."
        )

    if arr.dtype == np.uint8:
        norm = arr.astype(np.float64) / 255.0
    elif np.issubdtype(arr.dtype, np.floating):
        norm = arr.astype(np.float64)
        if not np.all(np.isfinite(norm)):
            raise OrtofotoFel("Ortofoto i flyttalsformat får inte innehålla NaN eller Inf.")
        min_val = float(np.min(norm))
        max_val = float(np.max(norm))
        if min_val < -1e-6 or max_val > 1.0 + 1e-6:
            raise OrtofotoFel(
                f"Flyttalsortofoto måste ligga i [0.0, 1.0], fick [{min_val:.4g}, {max_val:.4g}]."
            )
        norm = np.clip(norm, 0.0, 1.0)
    else:
        raise OrtofotoFel(
            f"Ostödd datatyp '{arr.dtype}' för ortofoto. Förväntade uint8 eller flyttal."
        )

    har_nir = arr.shape[2] == 4
    return norm, har_nir


def _vegetationsindex_fran_norm(
    norm: NDArray[np.float64],
    har_nir: bool,
) -> NDArray[np.float32]:
    """Beräknar `NDVI` eller normaliserad `ExG` från en redan normaliserad array."""
    r = norm[:, :, 0]
    g = norm[:, :, 1]
    b = norm[:, :, 2]

    if har_nir:
        nir = norm[:, :, 3]
        index = (nir - r) / (nir + r + 1e-6)
    else:
        index = (2.0 * g - r - b) / (r + g + b + 1e-6)

    return np.asarray(index, dtype=np.float32)


def berakna_vegetationsindex(
    bild: NDArray[np.floating[Any]] | NDArray[np.uint8],
) -> tuple[NDArray[np.float32], bool]:
    """Beräknar `NDVI` om NIR-band finns (4 band), annars normaliserad `ExG` (3 band)."""
    norm, har_nir = _normalisera_ortofoto(bild)
    return _vegetationsindex_fran_norm(norm, har_nir), har_nir


def klassificera_ortofoto(
    bild: NDArray[np.floating[Any]] | NDArray[np.uint8],
    *,
    ndsm_m: NDArray[np.floating[Any]] | None = None,
    byggnad_mask: NDArray[np.bool_] | None = None,
    parametrar: OrtofotoParametrar | None = None,
) -> OrtofotoKlassningResultat:
    """Klassificerar RGB eller RGB+NIR ortofoto deterministiskt till K1-markslag."""
    p = parametrar if parametrar is not None else OrtofotoParametrar()
    norm, har_nir = _normalisera_ortofoto(bild)
    veg_idx = _vegetationsindex_fran_norm(norm, har_nir)
    veg = veg_idx.astype(np.float64)

    rader, kolumner = norm.shape[:2]
    form_rc = (rader, kolumner)

    if ndsm_m is not None:
        ndsm_arr = np.asarray(ndsm_m, dtype=np.float64)
        if ndsm_arr.shape != form_rc:
            raise OrtofotoFel(
                f"ndsm_m har form {ndsm_arr.shape}, förväntade {form_rc}."
            )
    else:
        ndsm_arr = None

    if byggnad_mask is not None:
        bygg_raw = np.asarray(byggnad_mask)
        if bygg_raw.shape != form_rc:
            raise OrtofotoFel(
                f"byggnad_mask har form {bygg_raw.shape}, förväntade {form_rc}."
            )
        bygg_mask = bygg_raw.astype(bool)
    else:
        bygg_mask = np.zeros(form_rc, dtype=bool)

    r = norm[:, :, 0]
    g = norm[:, :, 1]
    b = norm[:, :, 2]
    luminans = 0.2126 * r + 0.7152 * g + 0.0722 * b

    c_max = np.maximum(np.maximum(r, g), b)
    c_min = np.minimum(np.minimum(r, g), b)
    matten = (c_max - c_min) / (c_max + 1e-6)

    veg_troskel = p.ndvi_vegetation_troskel if har_nir else p.exg_vegetation_troskel

    if har_nir:
        nir = norm[:, :, 3]
        ndwi = (g - nir) / (g + nir + 1e-6)
        vatten_kandidat = (ndwi > p.ndwi_vatten_troskel) & (nir < 0.25)
        vatten_marginal = np.minimum(ndwi - p.ndwi_vatten_troskel, 0.25 - nir)
    else:
        ndwi = np.zeros(form_rc, dtype=np.float64)
        ljusgra = (luminans >= p.grus_ljushet_troskel) & (matten <= p.grus_matten_max)
        vatten_kandidat = (b > r + 0.08) & (b > g + 0.03) & (~ljusgra)
        vatten_marginal = np.minimum(b - r - 0.08, b - g - 0.03)

    # Skugga: låg luminans och inte vatten (vatten har typiskt mycket låg reflektion i NIR/RGB)
    skuggmask = (luminans < p.skugga_luminans_troskel) & (~vatten_kandidat)

    markslag = np.full(form_rc, HARDGJORT, dtype=np.uint8)
    konfidens = np.zeros(form_rc, dtype=np.float32)

    # 5. Obebyggd / hårdgjord mark (basnivå före högre prioritet)
    grus_varm = (luminans >= p.grus_ljushet_troskel) & ((r - b) > 0.05) & (matten <= 0.45)
    grus_ljus = (
        (luminans >= p.grus_ljushet_troskel)
        & (matten <= p.grus_matten_max)
        & ((r - b) > 0.02)
    )
    grus_mask = grus_varm | grus_ljus

    # Konfidens för HARDGJORT (baserat på marginal till skugga, vegetation och grus)
    skugg_marg = luminans - p.skugga_luminans_troskel
    veg_avst_marg = veg_troskel - veg
    grus_avst_marg = np.where(
        luminans < p.grus_ljushet_troskel,
        p.grus_ljushet_troskel - luminans,
        np.maximum(0.05 - (r - b), 0.02),
    )
    hard_marg = np.minimum(skugg_marg, np.minimum(veg_avst_marg, grus_avst_marg))
    konfidens[:, :] = np.clip(0.45 + 2.5 * np.maximum(hard_marg, 0.0), 0.45, 0.95).astype(
        np.float32
    )

    if np.any(grus_mask):
        markslag[grus_mask] = np.uint8(GRUS)
        lum_marg = luminans - p.grus_ljushet_troskel
        farg_marg = np.maximum((r - b) - 0.02, 0.0)
        grus_konf = np.clip(0.55 + 1.5 * lum_marg + 1.2 * farg_marg, 0.50, 0.95)
        konfidens[grus_mask] = grus_konf[grus_mask].astype(np.float32)

    # 4. Vegetation (GRAS / SKOG)
    veg_mask = veg >= veg_troskel
    if np.any(veg_mask):
        veg_skala = 1.2 if har_nir else 2.5
        veg_konf = np.clip(0.55 + veg_skala * (veg - veg_troskel), 0.50, 0.97)
        if ndsm_arr is not None:
            giltig_ndsm = np.isfinite(ndsm_arr)
            skog_mask = veg_mask & giltig_ndsm & (ndsm_arr >= p.skog_ndsm_troskel_m)
            gras_mask = veg_mask & (~skog_mask)
            hojd_marg = np.where(
                giltig_ndsm,
                np.abs(ndsm_arr - p.skog_ndsm_troskel_m),
                0.5,
            )
            hojd_konf = np.clip(0.55 + 0.2 * hojd_marg, 0.50, 0.97)
            eff_veg_konf = np.minimum(veg_konf, hojd_konf)
            markslag[gras_mask] = np.uint8(GRAS)
            markslag[skog_mask] = np.uint8(SKOG)
            konfidens[veg_mask] = eff_veg_konf[veg_mask].astype(np.float32)
        else:
            markslag[veg_mask] = np.uint8(GRAS)
            konfidens[veg_mask] = veg_konf[veg_mask].astype(np.float32)

    # 3. Vatten
    if np.any(vatten_kandidat):
        markslag[vatten_kandidat] = np.uint8(VATTEN)
        v_skala = 1.5 if har_nir else 2.0
        v_konf = np.clip(0.60 + v_skala * np.maximum(vatten_marginal, 0.0), 0.50, 0.98)
        konfidens[vatten_kandidat] = v_konf[vatten_kandidat].astype(np.float32)

    # 1. Skugga (överskuggar markklass om inte känd byggnad)
    skugga_ej_byggnad = skuggmask & (~bygg_mask)
    if np.any(skugga_ej_byggnad):
        markslag[skugga_ej_byggnad] = np.uint8(OKANT)
        skugg_ratio = luminans / max(p.skugga_luminans_troskel, 1e-6)
        skugg_konf = np.clip(0.10 + 0.25 * skugg_ratio, 0.05, 0.35)
        konfidens[skugga_ej_byggnad] = skugg_konf[skugga_ej_byggnad].astype(np.float32)

    # 2. Känd byggnad från baskarta (högst prioritet)
    if np.any(bygg_mask):
        gront_tak_mask = bygg_mask & (veg > veg_troskel)
        vanligt_tak_mask = bygg_mask & (~gront_tak_mask)
        markslag[vanligt_tak_mask] = np.uint8(BYGGNAD)
        markslag[gront_tak_mask] = np.uint8(GRONT_TAK)
        konfidens[bygg_mask] = np.float32(0.95)

    totalt = float(markslag.size)
    klass_andelar: dict[str, float] = {}
    for kod, namn in _MARKSLAG_NAMN.items():
        klass_andelar[namn] = float(np.count_nonzero(markslag == kod)) / totalt

    skugga_andel = float(np.count_nonzero(skuggmask)) / totalt

    return OrtofotoKlassningResultat(
        markslag=markslag,
        konfidens=konfidens,
        skuggmask=skuggmask,
        vegetationsindex=veg_idx,
        har_nir=har_nir,
        klass_andelar=klass_andelar,
        skugga_andel=skugga_andel,
    )


def detektera_forandringar_mot_laser(
    gammal_markslag: NDArray[np.uint8],
    ny_klassning: OrtofotoKlassningResultat,
    *,
    pixelstorlek_m: float = 0.25,
    gammal_mark_z: NDArray[np.floating[Any]] | None = None,
    ny_dsm_z: NDArray[np.floating[Any]] | None = None,
    hojd_troskel_m: float = 1.5,
) -> ForandringsRapport:
    """Detekterar ny hårdgörning och nya byggnader/schakt mot äldre lasermodell."""
    if not math.isfinite(pixelstorlek_m) or pixelstorlek_m <= 0.0:
        raise OrtofotoFel(f"pixelstorlek_m måste vara > 0, fick {pixelstorlek_m}.")
    if not math.isfinite(hojd_troskel_m) or hojd_troskel_m <= 0.0:
        raise OrtofotoFel(f"hojd_troskel_m måste vara > 0, fick {hojd_troskel_m}.")

    gammal = np.asarray(gammal_markslag, dtype=np.uint8)
    form_rc = ny_klassning.markslag.shape
    if gammal.ndim != 2 or gammal.shape != form_rc:
        raise OrtofotoFel(
            f"gammal_markslag har form {gammal.shape}, förväntade {form_rc}."
        )

    if (gammal_mark_z is None) != (ny_dsm_z is None):
        raise OrtofotoFel("Både gammal_mark_z och ny_dsm_z måste anges tillsammans.")

    var_genomslapplig = (gammal == GRAS) | (gammal == SKOG) | (gammal == GRUS)
    nu_hardgjord = (
        (ny_klassning.markslag == HARDGJORT)
        & (ny_klassning.konfidens >= 0.5)
        & (~ny_klassning.skuggmask)
    )
    nya_hardgjorda_mask = var_genomslapplig & nu_hardgjord

    nya_byggnader_mask = np.zeros(form_rc, dtype=bool)
    if gammal_mark_z is not None and ny_dsm_z is not None:
        gammal_z = np.asarray(gammal_mark_z, dtype=np.float64)
        ny_z = np.asarray(ny_dsm_z, dtype=np.float64)
        if gammal_z.shape != form_rc or ny_z.shape != form_rc:
            raise OrtofotoFel(
                f"Höjdraster måste ha form {form_rc}, fick {gammal_z.shape} och {ny_z.shape}."
            )
        diff_z = ny_z - gammal_z
        ej_befintlig_bygg_skog = ~(
            (gammal == BYGGNAD)
            | (gammal == TAK)
            | (gammal == GRONT_TAK)
            | (gammal == SKOG)
        )
        nya_byggnader_mask = (
            np.isfinite(diff_z) & (diff_z >= hojd_troskel_m) & ej_befintlig_bygg_skog
        )

    forandringsmask = nya_hardgjorda_mask | nya_byggnader_mask
    pixel_area = float(pixelstorlek_m) * float(pixelstorlek_m)

    antal_hard = int(np.count_nonzero(nya_hardgjorda_mask))
    antal_bygg = int(np.count_nonzero(nya_byggnader_mask))
    antal_andrade = int(np.count_nonzero(forandringsmask))

    nya_hardgjorda_m2 = antal_hard * pixel_area
    nya_byggnader_m2 = antal_bygg * pixel_area
    andrad_area_m2 = antal_andrade * pixel_area

    varningar: list[str] = []
    if antal_hard > 0:
        varningar.append(
            f"Ny hårdgörning detekterad: {nya_hardgjorda_m2:.2f} m² ({antal_hard} pixlar)"
        )
    if antal_bygg > 0:
        varningar.append(
            f"Nya byggnader eller höjdförändringar (>= {hojd_troskel_m:g} m): "
            f"{nya_byggnader_m2:.2f} m² ({antal_bygg} pixlar)"
        )

    return ForandringsRapport(
        forandringsmask=forandringsmask,
        nya_hardgjorda_mask=nya_hardgjorda_mask,
        nya_byggnader_mask=nya_byggnader_mask,
        andrad_area_m2=andrad_area_m2,
        nya_hardgjorda_m2=nya_hardgjorda_m2,
        nya_byggnader_m2=nya_byggnader_m2,
        varningar=tuple(varningar),
    )


def kombinera_ortofoto_med_markmodell(
    markslag_bas: NDArray[np.uint8],
    osakerhet_z: NDArray[np.floating[Any]],
    klassning: OrtofotoKlassningResultat,
    *,
    forandringar: ForandringsRapport | None = None,
    parametrar: OrtofotoParametrar | None = None,
) -> tuple[NDArray[np.uint8], NDArray[np.float32]]:
    """Kombinerar baskartans markslag och höjdosäkerhet med ortofotoklassning."""
    p = parametrar if parametrar is not None else OrtofotoParametrar()
    form_rc = klassning.markslag.shape

    bas = np.asarray(markslag_bas, dtype=np.uint8)
    osak = np.asarray(osakerhet_z, dtype=np.float64)
    if bas.ndim != 2 or bas.shape != form_rc:
        raise OrtofotoFel(
            f"markslag_bas har form {bas.shape}, förväntade {form_rc}."
        )
    if osak.ndim != 2 or osak.shape != form_rc:
        raise OrtofotoFel(
            f"osakerhet_z har form {osak.shape}, förväntade {form_rc}."
        )
    if forandringar is not None and forandringar.forandringsmask.shape != form_rc:
        raise OrtofotoFel(
            f"forandringsmask har form {forandringar.forandringsmask.shape}, "
            f"förväntade {form_rc}."
        )

    ny_markslag = bas.copy()
    ny_osakerhet = osak.copy()

    # Fyll i okända celler från ortofotot (utan att ändra kända hinder/markslag)
    fyll_okant = (bas == OKANT) & (klassning.markslag != OKANT)
    ny_markslag[fyll_okant] = klassning.markslag[fyll_okant]

    # Uppdatera byggnad/tak till grönt tak om ortofotot visar vegetation på taket
    gront_tak_uppd = ((bas == BYGGNAD) | (bas == TAK)) & (klassning.markslag == GRONT_TAK)
    ny_markslag[gront_tak_uppd] = np.uint8(GRONT_TAK)

    # Öka höjdosäkerheten i skugga och i detekterade förändringszoner
    ny_osakerhet[klassning.skuggmask] += p.osakerhet_skugga_tillagg_m
    if forandringar is not None:
        ny_osakerhet[forandringar.forandringsmask] += p.osakerhet_forandring_tillagg_m

    return ny_markslag, ny_osakerhet.astype(np.float32)


def rendera_markslag_png_base64(markslag: NDArray[np.uint8]) -> dict[str, Any]:
    """Renderar ett 2D markslagsraster (K1-koder 0–9) till en base64-kodad översikts-PNG."""
    arr = np.asarray(markslag, dtype=np.uint8)
    if arr.ndim != 2 or arr.shape[0] < 1 or arr.shape[1] < 1:
        raise OrtofotoFel(f"markslag måste vara ett icke-tomt 2D-raster, fick {arr.shape}.")

    hojd, bredd = arr.shape
    rgba = np.zeros((hojd, bredd, 4), dtype=np.uint8)
    # Standardfärg för ev. okända koder utanför 0–9 blir OKANT
    rgba[:, :] = _MARKSLAG_PALETT_RGBA[OKANT]
    for kod, farg in _MARKSLAG_PALETT_RGBA.items():
        mask = arr == kod
        if np.any(mask):
            rgba[mask] = farg

    img = Image.fromarray(rgba, mode="RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return {
        "png_base64": b64,
        "data_uri": f"data:image/png;base64,{b64}",
        "bredd_px": int(bredd),
        "hojd_px": int(hojd),
    }
