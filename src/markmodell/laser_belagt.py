"""Belagd yta (hårdgjort) ur laserns intensitet – trösklar lyfta från O-Tid.

Källa: joelberring/otid, `scripts/geo-paved.ts` (regeln och `DEFAULT_PAVED`),
`scripts/geo-generalize.ts` (`openMask`) och punktstatistiken i `tools/lasclip/src/main.rs`
(`point_stats`), commit 1d63297, MIT, © 2026 Joel Berring. Ringgeneraliseringen i O-Tid
(räta vinklar, rektanglar) lyfts inte: Floden behöver en mask på rutnätet, inte ritade ytor.

Regeln, i 2 m-celler:

1. Öppen cell: har punkter, markträffarnas andel ≥ 200/255, vegetationens höjd ≤ 0,6 m, inte vatten.
   Hus faller bort: under tak finns inga markträffar.
2. Intensiteten (markträffarnas medel / 64, 0–255) medianfiltreras 3 × 3 över celler
   med markträffar.
3. Gränsen anpassas till skanningen: mörka toppen = vanligaste värdet på den mörka sidan om
   Otsu-gränsen; gräns = 2,5 × toppen, aldrig över Otsu och aldrig under 1,5 × toppen. Utan två
   tydliga toppar ges ingen belagd yta (och skälet sägs).
4. Belagd = öppen och filtrerad intensitet ≤ gränsen; öppnas ett steg, hål < 20 m² fylls,
   ytor < 40 m² tas bort.

O-Tids mätning mot sprintkartan Bandhagen–Hagsätra (ISSprOM 2019): precision 0,75 och
täckning 0,52 per 2 m-cell; 11 % av det utpekade var tomtmark (uppfarter). Resultatet är
därför **kandidater** för markslag 1 (hårdgjort), inte facit. Hur de vägs mot baskartans
ytor och ortofotot är ett öppet beslut.

Index: alla raster här har formen (bredd, hojd) och indexeras [x, y], x österut och y norrut
från rutans sydvästra hörn (samma ordning som O-Tids `x * höjd + y`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from data_inlasning.lantmateriet import PunktMoln

KLASS_MARK = 2
KLASS_VATTEN = 9
KLASSER_BRUS = (7, 18)
MAX_OVER_MARK_M = 60.0
MARKSLAG_HARDGJORT = 1


@dataclass(frozen=True)
class BelagtInstallningar:
    """Trösklarna i O-Tids `DEFAULT_PAVED`. Ändras de ska det stå i recept.json (parametrar)."""

    mark_andel: int = 200
    """Markträffarnas andel (0–255) för en öppen cell."""
    max_hojd_m: float = 0.6
    """Vegetationens högsta höjd över mark för en öppen cell."""
    toppfaktor: float = 2.5
    min_toppfaktor: float = 1.5
    min_oppna_celler: int = 2000
    min_mork_andel: float = 0.05
    fyll_hal_m2: float = 20.0
    min_yta_m2: float = 40.0

    def som_dict(self) -> dict[str, float]:
        return {k: float(v) for k, v in self.__dict__.items()}


STANDARD_BELAGT = BelagtInstallningar()


@dataclass(frozen=True)
class Punktstatistik:
    """Punktstatistik per cell (motsvarar banden 0, 1, 2 och 6 i lasclips `punkter.bin`)."""

    min_e: float
    min_n: float
    cell_m: float
    hogst_over_mark_m: NDArray[np.float32]
    """Högsta höjd över mark; 0 där bara markträffar finns, NaN utan punkter."""
    antal: NDArray[np.float32]
    mark: NDArray[np.float32]
    intensitet: NDArray[np.float32]
    """Markträffarnas medelintensitet (rå LAS-intensitet), NaN utan markträffar."""

    @property
    def bredd(self) -> int:
        return int(self.antal.shape[0])

    @property
    def hojd(self) -> int:
        return int(self.antal.shape[1])


@dataclass(frozen=True)
class BelagtResultat:
    mask: NDArray[np.uint8]
    belagd_ha: float
    oppna_celler: int
    grans: float | None = None
    mork_topp: int | None = None
    otsu: int | None = None
    skal: str | None = None
    """Varför ingen belagd yta gavs (None när en gräns kunde sättas)."""
    installningar: dict[str, float] = field(default_factory=dict)


def otsu_grans(histogram: NDArray[np.int64] | list[int]) -> int | None:
    """Otsu-gränsen t ("högst t" mot "över t").

    Mittersta värdet vid lika delning; None om allt ligger i ett värde.
    """
    h = [int(v) for v in histogram]
    total = sum(h)
    summa = sum(i * v for i, v in enumerate(h))
    bast, forsta, sista = -1.0, None, 0
    under = under_summa = 0
    for t in range(len(h) - 1):
        under += h[t]
        under_summa += t * h[t]
        over = total - under
        if under == 0 or over == 0:
            continue
        mellan = under * over * (under_summa / under - (summa - under_summa) / over) ** 2
        if mellan > bast * (1 + 1e-12):
            bast, forsta, sista = mellan, t, t
        elif mellan >= bast * (1 - 1e-12):
            sista = t
    return None if forsta is None else (forsta + sista) // 2


def belagd_grans(
    histogram: NDArray[np.int64] | list[int],
    installningar: BelagtInstallningar = STANDARD_BELAGT,
) -> tuple[float, int, int] | str:
    """(gräns, mörk topp, Otsu) ur de öppna cellernas histogram, eller skälet."""
    h = [int(v) for v in histogram]
    oppna = sum(h)
    if oppna < installningar.min_oppna_celler:
        return "för få öppna celler"
    otsu = otsu_grans(h)
    if otsu is None:
        return "en enda intensitet"
    topp = 0
    for v in range(1, otsu + 1):
        if h[v] > h[topp]:
            topp = v
    if topp <= 0 or installningar.min_toppfaktor * topp > otsu:
        return "ingen mörk topp"
    grans = max(installningar.min_toppfaktor * topp, min(installningar.toppfaktor * topp, otsu))
    mork = sum(h[v] for v in range(len(h)) if v <= grans)
    if mork < installningar.min_mork_andel * oppna:
        return "för få mörka celler"
    return grans, topp, otsu


def _js_avrunda(x: NDArray[np.float64] | float) -> NDArray[np.float64]:
    """Avrundning som JavaScripts `Math.round` (x,5 uppåt), så att cellerna blir som i O-Tid."""
    ut: NDArray[np.float64] = np.floor(np.asarray(x, dtype=np.float64) + 0.5)
    return ut


def _grannar4(mask: NDArray[np.bool_], varde: bool) -> NDArray[np.bool_]:
    """Sant där någon 4-granne har `varde`; utanför bilden räknas inte."""
    ut = np.zeros_like(mask)
    lika = mask == varde
    ut[1:, :] |= lika[:-1, :]
    ut[:-1, :] |= lika[1:, :]
    ut[:, 1:] |= lika[:, :-1]
    ut[:, :-1] |= lika[:, 1:]
    return ut


def oppna_mask(mask: NDArray[np.bool_]) -> NDArray[np.bool_]:
    """Ett stegs öppning med fyra grannar (O-Tids `openMask(…, 1)`): krymp, sedan väx."""
    krympt = mask & ~_grannar4(mask, False)
    return krympt | _grannar4(krympt, True)


def _komponenter(
    mask: NDArray[np.bool_], varde: bool
) -> list[tuple[NDArray[np.intp], NDArray[np.intp], bool]]:
    """Sammanhängande delar (4 grannar) med `mask == varde`: (x-index, y-index, når kanten)."""
    b, h = mask.shape
    sett = np.zeros(mask.shape, dtype=bool)
    lika = mask == varde
    delar: list[tuple[NDArray[np.intp], NDArray[np.intp], bool]] = []
    for sx, sy in zip(*np.nonzero(lika), strict=True):
        if sett[sx, sy]:
            continue
        ko = [(int(sx), int(sy))]
        sett[sx, sy] = True
        xs: list[int] = []
        ys: list[int] = []
        kant = False
        while ko:
            x, y = ko.pop()
            xs.append(x)
            ys.append(y)
            if x == 0 or y == 0 or x == b - 1 or y == h - 1:
                kant = True
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < b and 0 <= ny < h and lika[nx, ny] and not sett[nx, ny]:
                    sett[nx, ny] = True
                    ko.append((nx, ny))
        delar.append((np.array(xs, dtype=np.intp), np.array(ys, dtype=np.intp), kant))
    return delar


def belagd_yta(
    stat: Punktstatistik,
    *,
    vatten: Callable[[float, float], bool] | None = None,
    installningar: BelagtInstallningar = STANDARD_BELAGT,
) -> BelagtResultat:
    """Belagd yta ur punktstatistiken (O-Tids `pavedFromPoints` utan ringarna)."""
    b, h, cell = stat.bredd, stat.hojd, stat.cell_m
    n, mark, ints, topp = stat.antal, stat.mark, stat.intensitet, stat.hogst_over_mark_m
    har_mark = (n > 0) & (mark > 0) & np.isfinite(ints)
    intensitet = np.full((b, h), -1, dtype=np.int32)
    intensitet[har_mark] = np.clip(_js_avrunda(ints[har_mark] / 64.0), 0, 255).astype(np.int32)
    with np.errstate(invalid="ignore", divide="ignore"):
        andel = np.where(n > 0, _js_avrunda(255.0 * mark / np.where(n > 0, n, 1)), 0.0)
    topp0 = np.where(np.isfinite(topp), topp, 0.0)
    oppen = har_mark & (andel >= installningar.mark_andel) & (topp0 <= installningar.max_hojd_m)
    if vatten is not None:
        for x, y in zip(*np.nonzero(oppen), strict=True):
            if vatten(stat.min_e + (x + 0.5) * cell, stat.min_n + (y + 0.5) * cell):
                oppen[x, y] = False

    filtrerad = np.zeros((b, h), dtype=np.int32)
    histogram = np.zeros(256, dtype=np.int64)
    for x, y in zip(*np.nonzero(oppen), strict=True):
        omr = intensitet[max(0, x - 1) : x + 2, max(0, y - 1) : y + 2]
        nara = np.sort(omr[omr >= 0])
        median = int(nara[len(nara) // 2])
        filtrerad[x, y] = median
        histogram[median] += 1
    oppna_celler = int(oppen.sum())

    valt = belagd_grans(histogram, installningar)
    if isinstance(valt, str):
        return BelagtResultat(
            mask=np.zeros((b, h), dtype=np.uint8),
            belagd_ha=0.0,
            oppna_celler=oppna_celler,
            skal=valt,
            installningar=installningar.som_dict(),
        )
    grans, mork_topp, otsu = valt
    mask = oppna_mask(oppen & (filtrerad <= grans))
    area = cell * cell
    for xs, ys, kant in _komponenter(mask, False):
        if not kant and len(xs) * area < installningar.fyll_hal_m2:
            mask[xs, ys] = True
    for xs, ys, _ in _komponenter(mask, True):
        if len(xs) * area < installningar.min_yta_m2:
            mask[xs, ys] = False
    celler = int(mask.sum())
    return BelagtResultat(
        mask=mask.astype(np.uint8),
        belagd_ha=float(_js_avrunda(celler * area / 100)) / 100,
        oppna_celler=oppna_celler,
        grans=float(_js_avrunda(grans * 10)) / 10,
        mork_topp=mork_topp,
        otsu=otsu,
        installningar=installningar.som_dict(),
    )


def _mark_1m_med_grannar(
    e: NDArray[np.float64],
    n: NDArray[np.float64],
    z: NDArray[np.float64],
    min_e: float,
    min_n: float,
    bredd: int,
    hojd: int,
) -> NDArray[np.float64]:
    """Markhöjd i 1 m-celler (medel av markpunkter); luckor fylls från ringar upp till 3 m bort."""
    ix = np.clip(((e - min_e) // 1.0).astype(np.intp), 0, bredd - 1)
    iy = np.clip(((n - min_n) // 1.0).astype(np.intp), 0, hojd - 1)
    summa = np.zeros((bredd, hojd))
    antal = np.zeros((bredd, hojd))
    np.add.at(summa, (ix, iy), z)
    np.add.at(antal, (ix, iy), 1.0)
    medel = np.where(antal > 0, summa / np.where(antal > 0, antal, 1), np.nan)
    ut = np.full((bredd, hojd), np.nan)
    kvar = np.ones((bredd, hojd), dtype=bool)
    pad = 3
    p = np.pad(medel, pad, constant_values=np.nan)
    for rackvidd in range(0, 4):
        tot = np.zeros((bredd, hojd))
        cnt = np.zeros((bredd, hojd))
        for dx in range(-rackvidd, rackvidd + 1):
            for dy in range(-rackvidd, rackvidd + 1):
                if max(abs(dx), abs(dy)) != rackvidd:
                    continue
                s = p[pad + dx : pad + dx + bredd, pad + dy : pad + dy + hojd]
                ok = np.isfinite(s)
                tot += np.where(ok, s, 0.0)
                cnt += ok
        satt = kvar & (cnt > 0)
        ut[satt] = tot[satt] / cnt[satt]
        kvar &= ~satt
    return ut


def punktstatistik(
    moln: PunktMoln,
    bbox: tuple[float, float, float, float],
    *,
    cell_m: float = 2.0,
) -> Punktstatistik:
    """Punktstatistik i `cell_m`-celler ur ett punktmoln med intensitet (lasclips `point_stats`).

    Markhöjden är medel av markpunkter i 1 m-celler (närmaste ring upp till 3 m där det saknas).
    Brus (klass 7, 18), vatten och punkter mer än 60 m över mark räknas inte.
    """
    if moln.intensitet is None:
        raise ValueError("Punktmolnet saknar intensitet; läs det med las_las_punktmoln.")
    min_e, min_n, max_e, max_n = bbox
    if not (min_e < max_e and min_n < max_n):
        raise ValueError(f"Tom ruta: {bbox}")
    inne = (moln.e_m >= min_e) & (moln.e_m <= max_e) & (moln.n_m >= min_n) & (moln.n_m <= max_n)
    e, n, z = moln.e_m[inne], moln.n_m[inne], moln.z_rh2000[inne]
    k, i = moln.klass[inne], moln.intensitet[inne].astype(np.float64)
    b = int(np.ceil((max_e - min_e) / cell_m))
    h = int(np.ceil((max_n - min_n) / cell_m))
    b1 = int(np.ceil(max_e - min_e))
    h1 = int(np.ceil(max_n - min_n))

    ar_mark = k == KLASS_MARK
    dem = _mark_1m_med_grannar(e[ar_mark], n[ar_mark], z[ar_mark], min_e, min_n, b1, h1)
    ok = ~np.isin(k, (*KLASSER_BRUS, KLASS_VATTEN))
    jx = np.clip(((e - min_e) // 1.0).astype(np.intp), 0, b1 - 1)
    jy = np.clip(((n - min_n) // 1.0).astype(np.intp), 0, h1 - 1)
    mark_z = dem[jx, jy]
    over = z - mark_z
    ok &= np.isfinite(mark_z) & (over <= MAX_OVER_MARK_M)

    cx = np.clip(((e - min_e) // cell_m).astype(np.intp), 0, b - 1)[ok]
    cy = np.clip(((n - min_n) // cell_m).astype(np.intp), 0, h - 1)[ok]
    over, gm, ii = over[ok], ar_mark[ok], i[ok]

    antal = np.zeros((b, h), dtype=np.float64)
    mark = np.zeros((b, h), dtype=np.float64)
    isum = np.zeros((b, h), dtype=np.float64)
    hogst = np.full((b, h), -np.inf)
    np.add.at(antal, (cx, cy), 1.0)
    np.add.at(mark, (cx, cy), gm.astype(np.float64))
    np.add.at(isum, (cx, cy), np.where(gm, ii, 0.0))
    # Markträffar ger höjden 0; övriga sin höjd över mark (aldrig under 0).
    np.maximum.at(hogst, (cx, cy), np.where(gm, 0.0, np.maximum(over, 0.0)))
    hogst_ut = np.where(antal > 0, hogst, np.nan).astype(np.float32)
    ints = np.where(mark > 0, isum / np.where(mark > 0, mark, 1), np.nan).astype(np.float32)
    return Punktstatistik(
        min_e=min_e,
        min_n=min_n,
        cell_m=cell_m,
        hogst_over_mark_m=hogst_ut,
        antal=antal.astype(np.float32),
        mark=mark.astype(np.float32),
        intensitet=ints,
    )


def mask_pa_rutnat(
    resultat: BelagtResultat,
    stat: Punktstatistik,
    *,
    min_e: float,
    min_n: float,
    cell_m: float,
    bredd: int,
    hojd: int,
) -> NDArray[np.uint8]:
    """Kandidatmasken samplad (närmaste cell) på ett annat rutnät, t.ex. K1:s 0,25 m.

    Returnerar formen (rader, kolumner) = (hojd, bredd) med rad 0 i norr, som K1-rastren.
    Celler utanför statistiken blir 0.
    """
    kol = np.arange(bredd)
    rad = np.arange(hojd)
    e = min_e + (kol + 0.5) * cell_m
    n = min_n + (hojd - rad - 0.5) * cell_m
    sx = np.floor((e - stat.min_e) / stat.cell_m).astype(np.intp)
    sy = np.floor((n - stat.min_n) / stat.cell_m).astype(np.intp)
    ok_x = (sx >= 0) & (sx < stat.bredd)
    ok_y = (sy >= 0) & (sy < stat.hojd)
    ut = np.zeros((hojd, bredd), dtype=np.uint8)
    yy, xx = np.meshgrid(np.nonzero(ok_y)[0], np.nonzero(ok_x)[0], indexing="ij")
    ut[yy, xx] = resultat.mask[sx[xx], sy[yy]]
    return ut
