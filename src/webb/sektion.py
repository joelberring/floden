"""Deterministisk, vektoriserad sektions- och tvärsnittsmotor (SCALGO-liknande profil).

All beräkning sker deterministiskt i SWEREF 99 18 00 (EPSG:3011) och RH 2000:
- Sampling längs 2-punktslinje eller polylinje med `np.interp` (inkluderar alltid start och slut).
- Rasteravläsning med **närmaste cell** (identiskt med `markhojd` och `avlasning`) och
  "djupast inne"-regel när flera överlappande K1-rutor ges.
- Takmaskning: på takceller (`tak == True`) blir vattendjup och vattenyta alltid `None`.
- Vattenytans höjd i RH 2000 beräknas deterministiskt i backend (`mark + djup` när `djup > 0`).
- Byggnadsfotavtryck samplas vektoriserat med bbox-filtrerad `shapely.contains_xy`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from shapely import contains_xy
from shapely.geometry import Polygon

KONTRAKT = "floden-sektion/v1"
EPSG = 3011
HOJDSYSTEM = "RH 2000"

MIN_PUNKTER = 2
MAX_PUNKTER = 100
MAX_LANGD_M = 50_000.0
STANDARD_MAX_SAMPLING_PUNKTER = 300
STANDARD_PIXELSTORLEK_M = 0.5


@dataclass(frozen=True)
class RasterLager:
    """Ett 2D-raster i EPSG:3011 (rad 0 = norr) för närmaste-cell-sampling."""

    raster: NDArray[Any]
    x_min: float
    y_max: float
    pixelstorlek_x: float
    pixelstorlek_y: float

    @classmethod
    def fran_geotransform(cls, raster: NDArray[Any], geo: Any) -> RasterLager:
        """Skapar `RasterLager` ur `(matris, geo)` som `las_terrang` / `las_geotiff` returnerar."""
        return cls(
            raster=np.asarray(raster),
            x_min=float(geo.x_min),
            y_max=float(geo.y_max),
            pixelstorlek_x=float(geo.pixelstorlek_x),
            pixelstorlek_y=float(geo.pixelstorlek_y),
        )

    @classmethod
    def fran_mosaik(cls, mosaik: Any) -> RasterLager:
        """Skapar `RasterLager` ur en `webb.resultatlager.Mosaik`."""
        px = float(mosaik.pixelstorlek_m)
        return cls(
            raster=np.asarray(mosaik.raster),
            x_min=float(mosaik.utbredning.x_min),
            y_max=float(mosaik.utbredning.y_max),
            pixelstorlek_x=px,
            pixelstorlek_y=px,
        )


def _normalisera_rutor(kalla: Any) -> list[RasterLager]:
    """Accepterar `RasterLager`, `Mosaik`, `(arr, geo)`-tupel eller sekvens av dessa."""
    if kalla is None:
        return []
    if isinstance(kalla, RasterLager):
        return [kalla]
    if (
        hasattr(kalla, "raster")
        and hasattr(kalla, "utbredning")
        and hasattr(kalla, "pixelstorlek_m")
    ):
        return [RasterLager.fran_mosaik(kalla)]
    if (
        isinstance(kalla, tuple)
        and len(kalla) == 2
        and isinstance(kalla[0], np.ndarray)
        and hasattr(kalla[1], "x_min")
        and hasattr(kalla[1], "y_max")
    ):
        return [RasterLager.fran_geotransform(kalla[0], kalla[1])]
    if isinstance(kalla, Sequence) and not isinstance(kalla, str | bytes):
        ut: list[RasterLager] = []
        for del_ in kalla:
            ut.extend(_normalisera_rutor(del_))
        return ut
    raise ValueError(f"Ogiltig rasterkälla för sektion: {type(kalla).__name__}.")


def _normalisera_tak(tak: Any, djup_rutor: Sequence[RasterLager]) -> list[RasterLager]:
    """Normaliserar takmask (2D-matris på samma rutnät som `djup_rutor[0]` eller rasterrutor)."""
    if tak is None:
        return []
    if isinstance(tak, np.ndarray):
        if not djup_rutor:
            return []
        ref = djup_rutor[0]
        arr = np.asarray(tak, dtype=bool)
        if arr.shape != ref.raster.shape:
            raise ValueError(
                f"Takmaskens form {arr.shape} matchar inte djuprastrets form {ref.raster.shape}."
            )
        return [
            RasterLager(
                raster=arr,
                x_min=ref.x_min,
                y_max=ref.y_max,
                pixelstorlek_x=ref.pixelstorlek_x,
                pixelstorlek_y=ref.pixelstorlek_y,
            )
        ]
    return _normalisera_rutor(tak)


def validera_sektion_punkter(punkter: Any) -> list[tuple[float, float]]:
    """Validerar sektionslinjens hörnpunkter i SWEREF 99 18 00 (EPSG:3011).

    Krav:
    - Sekvens med `2 <= len(punkter) <= 100`.
    - Varje punkt är `[e, n]` med två ändliga tal (ej `bool`).
    - Total längd `0 < L <= 50_000 m`.
    """
    if isinstance(punkter, str | bytes | Mapping) or not isinstance(punkter, Sequence):
        raise ValueError("Sektionslinjen måste vara en lista med punkter [[e, n], ...].")
    if len(punkter) < MIN_PUNKTER or len(punkter) > MAX_PUNKTER:
        raise ValueError(
            f"Sektionslinjen måste ha mellan {MIN_PUNKTER} och {MAX_PUNKTER} punkter, "
            f"fick {len(punkter)}."
        )
    ut: list[tuple[float, float]] = []
    for idx, p in enumerate(punkter):
        if isinstance(p, str | bytes | Mapping) or not isinstance(p, Sequence) or len(p) != 2:
            raise ValueError(f"Punkt {idx} måste vara ett par [e, n] i EPSG:3011.")
        e_raw, n_raw = p[0], p[1]
        if (
            isinstance(e_raw, bool)
            or isinstance(n_raw, bool)
            or not isinstance(e_raw, int | float)
            or not isinstance(n_raw, int | float)
        ):
            raise ValueError(f"Punkt {idx} måste innehålla två tal i EPSG:3011.")
        e, n = float(e_raw), float(n_raw)
        if not (math.isfinite(e) and math.isfinite(n)):
            raise ValueError(f"Punkt {idx} har ogiltiga (icke-ändliga) koordinater.")
        ut.append((e, n))

    total_langd = sum(
        math.hypot(ut[i + 1][0] - ut[i][0], ut[i + 1][1] - ut[i][1]) for i in range(len(ut) - 1)
    )
    if not math.isfinite(total_langd) or total_langd <= 0.0:
        raise ValueError("Sektionslinjens längd måste vara större än 0 m.")
    if total_langd > MAX_LANGD_M:
        raise ValueError(
            f"Sektionslinjen får vara högst {MAX_LANGD_M:g} m lång, fick {total_langd:.1f} m."
        )
    return ut


def sampla_polylinje(
    punkter: Any,
    steg_m: float | None = None,
    max_punkter: int = STANDARD_MAX_SAMPLING_PUNKTER,
    pixelstorlek_m: float = STANDARD_PIXELSTORLEK_M,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64], float]:
    """Samplar en 2-punktslinje eller polylinje vektoriserat längs hela sträckan.

    Returnerar `(avstand_m, e, n, effektivt_steg_m)` och inkluderar alltid start- och slutpunkt.
    """
    giltiga = validera_sektion_punkter(punkter)
    if isinstance(max_punkter, bool) or not isinstance(max_punkter, int) or max_punkter < 2:
        raise ValueError(f"max_punkter måste vara ett heltal >= 2, fick {max_punkter!r}.")
    if (
        isinstance(pixelstorlek_m, bool)
        or not isinstance(pixelstorlek_m, int | float)
        or not math.isfinite(float(pixelstorlek_m))
        or float(pixelstorlek_m) <= 0.0
    ):
        raise ValueError(f"pixelstorlek_m måste vara ett positivt tal, fick {pixelstorlek_m!r}.")
    if steg_m is not None and (
        isinstance(steg_m, bool)
        or not isinstance(steg_m, int | float)
        or not math.isfinite(float(steg_m))
        or float(steg_m) <= 0.0
    ):
        raise ValueError(f"steg_m måste vara ett positivt tal eller None, fick {steg_m!r}.")

    # Filtrera bort ev. upprepade identiska punkter så kumulativt avstånd ökar strikt.
    e_lista = [giltiga[0][0]]
    n_lista = [giltiga[0][1]]
    for e_i, n_i in giltiga[1:]:
        if math.hypot(e_i - e_lista[-1], n_i - n_lista[-1]) > 0.0:
            e_lista.append(e_i)
            n_lista.append(n_i)
    e_vert = np.asarray(e_lista, dtype=np.float64)
    n_vert = np.asarray(n_lista, dtype=np.float64)
    seg = np.hypot(np.diff(e_vert), np.diff(n_vert))
    kum = np.concatenate(([0.0], np.cumsum(seg)))
    langd = float(kum[-1])

    min_steg = langd / float(max_punkter - 1)
    onskat = float(pixelstorlek_m) if steg_m is None else float(steg_m)
    eff_steg = max(onskat, min_steg)

    # Bygg samplingsavstånd: [0, eff_steg, 2*eff_steg, ..., langd] med högst max_punkter.
    if eff_steg >= langd:
        avstand = np.array([0.0, langd], dtype=np.float64)
    else:
        n_hela = int(math.floor(langd / eff_steg))
        if n_hela + 1 > max_punkter or (
            n_hela + 1 == max_punkter and (langd - n_hela * eff_steg) > 1e-9 * max(1.0, langd)
        ):
            avstand = np.linspace(0.0, langd, max_punkter, dtype=np.float64)
            eff_steg = langd / float(max_punkter - 1)
        else:
            bas = np.arange(n_hela + 1, dtype=np.float64) * eff_steg
            if langd - float(bas[-1]) > 1e-9 * max(1.0, langd):
                avstand = np.concatenate((bas, [langd]))
            else:
                bas[-1] = langd
                avstand = bas

    e_ut = np.interp(avstand, kum, e_vert)
    n_ut = np.interp(avstand, kum, n_vert)
    return avstand, e_ut, n_ut, float(eff_steg)


def _sampla_raster_rutor(
    rutor: Sequence[RasterLager],
    e: NDArray[np.float64],
    n: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Samplar ett eller flera överlappande raster med närmaste cell och 'djupast inne'-regel.

    Utanför alla rutor eller där värdet är NaN returneras `np.nan`.
    """
    ut = np.full(e.shape, np.nan, dtype=np.float64)
    if not rutor:
        return ut
    basta_avst = np.full(e.shape, -np.inf, dtype=np.float64)
    har_andligt = np.zeros(e.shape, dtype=bool)
    for ruta in rutor:
        arr = ruta.raster
        h, b = arr.shape[:2]
        if h == 0 or b == 0:
            continue
        px_x = ruta.pixelstorlek_x
        px_y = ruta.pixelstorlek_y
        kol = np.floor((e - ruta.x_min) / px_x).astype(np.int64)
        rad = np.floor((ruta.y_max - n) / px_y).astype(np.int64)
        inom = (kol >= 0) & (kol < b) & (rad >= 0) & (rad < h)
        if not np.any(inom):
            continue
        idx = np.flatnonzero(inom)
        r_i = rad[idx]
        c_i = kol[idx]
        vals = np.asarray(arr[r_i, c_i], dtype=np.float64)
        andlig = np.isfinite(vals)
        if not np.any(andlig):
            continue
        idx_ok = idx[andlig]
        r_ok = r_i[andlig]
        c_ok = c_i[andlig]
        vals_ok = vals[andlig]
        avst_x = np.minimum((c_ok + 0.5) * px_x, (b - c_ok - 0.5) * px_x)
        avst_y = np.minimum((r_ok + 0.5) * px_y, (h - r_ok - 0.5) * px_y)
        avst = np.minimum(avst_x, avst_y)
        battre = (~har_andligt[idx_ok]) | (avst > basta_avst[idx_ok])
        if np.any(battre):
            valda = idx_ok[battre]
            ut[valda] = vals_ok[battre]
            basta_avst[valda] = avst[battre]
            har_andligt[valda] = True
    return ut


def _sampla_tak_rutor(
    rutor: Sequence[RasterLager],
    e: NDArray[np.float64],
    n: NDArray[np.float64],
) -> NDArray[np.bool_]:
    """Samplar takmask (närmaste cell, djupast inne vid överlapp)."""
    ut = np.zeros(e.shape, dtype=bool)
    if not rutor:
        return ut
    basta_avst = np.full(e.shape, -np.inf, dtype=np.float64)
    for ruta in rutor:
        arr = ruta.raster
        h, b = arr.shape[:2]
        if h == 0 or b == 0:
            continue
        px_x = ruta.pixelstorlek_x
        px_y = ruta.pixelstorlek_y
        kol = np.floor((e - ruta.x_min) / px_x).astype(np.int64)
        rad = np.floor((ruta.y_max - n) / px_y).astype(np.int64)
        inom = (kol >= 0) & (kol < b) & (rad >= 0) & (rad < h)
        if not np.any(inom):
            continue
        idx = np.flatnonzero(inom)
        r_i = rad[idx]
        c_i = kol[idx]
        avst_x = np.minimum((c_i + 0.5) * px_x, (b - c_i - 0.5) * px_x)
        avst_y = np.minimum((r_i + 0.5) * px_y, (h - r_i - 0.5) * px_y)
        avst = np.minimum(avst_x, avst_y)
        battre = avst > basta_avst[idx]
        if np.any(battre):
            valda = idx[battre]
            ut[valda] = np.asarray(arr[r_i[battre], c_i[battre]], dtype=bool)
            basta_avst[valda] = avst[battre]
    return ut


def _sampla_byggnader(
    byggnader: Sequence[Mapping[str, Any]] | None,
    e: NDArray[np.float64],
    n: NDArray[np.float64],
    mark: NDArray[np.float64],
) -> tuple[list[bool], list[float | None]]:
    """Vektoriserad byggnadssampling med bbox-filtrering + `shapely.contains_xy`."""
    antal = int(e.size)
    traff_arr = np.zeros(antal, dtype=bool)
    tak_arr = np.full(antal, np.nan, dtype=np.float64)
    if not byggnader or antal == 0:
        return [False] * antal, [None] * antal

    linje_min_e = float(np.min(e))
    linje_max_e = float(np.max(e))
    linje_min_n = float(np.min(n))
    linje_max_n = float(np.max(n))

    for post in byggnader:
        ring = post.get("ring")
        if not ring or len(ring) < 3:
            continue
        xs = [float(p[0]) for p in ring]
        ys = [float(p[1]) for p in ring]
        if (
            max(xs) < linje_min_e
            or min(xs) > linje_max_e
            or max(ys) < linje_min_n
            or min(ys) > linje_max_n
        ):
            continue
        hal = post.get("hal") or []
        try:
            poly = Polygon(ring, holes=hal)
        except (TypeError, ValueError):
            continue
        if poly.is_empty:
            continue
        inom = np.asarray(contains_xy(poly, e, n), dtype=bool)
        if not np.any(inom):
            continue
        traff_arr |= inom

        tak_raw = post.get("takhojd_rh2000")
        hojd_raw = post.get("hojd_m")
        if (
            tak_raw is not None
            and not isinstance(tak_raw, bool)
            and isinstance(tak_raw, int | float)
            and math.isfinite(float(tak_raw))
        ):
            tak_val = np.full(antal, float(tak_raw), dtype=np.float64)
        elif (
            hojd_raw is not None
            and not isinstance(hojd_raw, bool)
            and isinstance(hojd_raw, int | float)
            and math.isfinite(float(hojd_raw))
        ):
            tak_val = mark + float(hojd_raw)
        else:
            continue
        uppdatera = inom & np.isfinite(tak_val) & (~np.isfinite(tak_arr) | (tak_val > tak_arr))
        tak_arr = np.where(uppdatera, tak_val, tak_arr)

    tak_lista: list[float | None] = [
        round(float(v), 4) if math.isfinite(float(v)) else None for v in tak_arr
    ]
    return [bool(x) for x in traff_arr], tak_lista


def _punkt_vikter(avstand_m: NDArray[np.float64]) -> NDArray[np.float64]:
    """Representerad delsträcka (m) per samplingspunkt så att `sum(vikter) == langd_m`."""
    n = int(avstand_m.size)
    if n <= 1:
        return np.zeros(n, dtype=np.float64)
    diff = np.diff(avstand_m)
    vikter = np.empty(n, dtype=np.float64)
    vikter[0] = 0.5 * diff[0]
    vikter[-1] = 0.5 * diff[-1]
    if n > 2:
        vikter[1:-1] = 0.5 * (diff[:-1] + diff[1:])
    return vikter


def _oversvammad_bredd(
    djup: Sequence[float | None],
    vikter: NDArray[np.float64],
    troskel_m: float,
    har_lager: bool,
) -> float | None:
    if not har_lager:
        return None
    bredd = 0.0
    for d, w in zip(djup, vikter, strict=True):
        if d is not None and d >= troskel_m:
            bredd += float(w)
    return round(bredd, 3)


def _min_max(varden: Sequence[float | None]) -> tuple[float | None, float | None]:
    giltiga = [v for v in varden if v is not None]
    if not giltiga:
        return None, None
    return round(min(giltiga), 4), round(max(giltiga), 4)


def _max_eller_none(varden: Sequence[float | None]) -> float | None:
    giltiga = [v for v in varden if v is not None]
    return round(max(giltiga), 4) if giltiga else None


def _finaste_pixelstorlek(rutor: Sequence[RasterLager]) -> float:
    if not rutor:
        return STANDARD_PIXELSTORLEK_M
    return min(min(r.pixelstorlek_x, r.pixelstorlek_y) for r in rutor)


def berakna_sektion(
    punkter: Any,
    terrang_fore: Any,
    *,
    terrang_efter: Any = None,
    djup_fore: Any = None,
    tak_fore: Any = None,
    djup_efter: Any = None,
    tak_efter: Any = None,
    byggnader_fore: Sequence[Mapping[str, Any]] | None = None,
    byggnader_efter: Sequence[Mapping[str, Any]] | None = None,
    steg_m: float | None = None,
    max_punkter: int = STANDARD_MAX_SAMPLING_PUNKTER,
) -> dict[str, Any]:
    """Beräknar en komplett, deterministisk sektion enligt kontraktet `floden-sektion/v1`."""
    terrang_fore_rutor = _normalisera_rutor(terrang_fore)
    terrang_efter_rutor = _normalisera_rutor(terrang_efter)
    djup_fore_rutor = _normalisera_rutor(djup_fore)
    djup_efter_rutor = _normalisera_rutor(djup_efter)
    tak_fore_rutor = _normalisera_tak(tak_fore, djup_fore_rutor)
    tak_efter_rutor = _normalisera_tak(tak_efter, djup_efter_rutor)

    px = _finaste_pixelstorlek(
        terrang_fore_rutor or terrang_efter_rutor or djup_fore_rutor or djup_efter_rutor
    )
    avstand_arr, e_arr, n_arr, eff_steg = sampla_polylinje(
        punkter,
        steg_m=steg_m,
        max_punkter=max_punkter,
        pixelstorlek_m=px,
    )

    har_efter = bool(terrang_efter_rutor or djup_efter_rutor or byggnader_efter is not None)
    antal = int(avstand_arr.size)

    mark_fore_raw = _sampla_raster_rutor(terrang_fore_rutor, e_arr, n_arr)
    mark_efter_raw = (
        _sampla_raster_rutor(terrang_efter_rutor, e_arr, n_arr)
        if terrang_efter_rutor
        else np.full(antal, np.nan, dtype=np.float64)
    )

    djup_fore_raw = _sampla_raster_rutor(djup_fore_rutor, e_arr, n_arr)
    tak_fore_mask = _sampla_tak_rutor(tak_fore_rutor, e_arr, n_arr)
    djup_efter_raw = (
        _sampla_raster_rutor(djup_efter_rutor, e_arr, n_arr)
        if djup_efter_rutor
        else np.full(antal, np.nan, dtype=np.float64)
    )
    tak_efter_mask = (
        _sampla_tak_rutor(tak_efter_rutor, e_arr, n_arr)
        if tak_efter_rutor
        else np.zeros(antal, dtype=bool)
    )

    mark_fore_m: list[float | None] = [
        round(float(v), 4) if math.isfinite(float(v)) else None for v in mark_fore_raw
    ]
    mark_efter_m: list[float | None] = [
        round(float(v), 4) if math.isfinite(float(v)) else None for v in mark_efter_raw
    ]

    vatten_djup_fore_m: list[float | None] = []
    vatten_yta_fore_m: list[float | None] = []
    for i in range(antal):
        d_raw = float(djup_fore_raw[i])
        m_raw = float(mark_fore_raw[i])
        if tak_fore_mask[i] or not math.isfinite(d_raw):
            vatten_djup_fore_m.append(None)
            vatten_yta_fore_m.append(None)
        else:
            d_val = max(0.0, d_raw)
            vatten_djup_fore_m.append(round(d_val, 4))
            if d_val > 0.0 and math.isfinite(m_raw):
                vatten_yta_fore_m.append(round(m_raw + d_val, 4))
            else:
                vatten_yta_fore_m.append(None)

    vatten_djup_efter_m: list[float | None] = []
    vatten_yta_efter_m: list[float | None] = []
    for i in range(antal):
        d_raw = float(djup_efter_raw[i])
        m_raw = (
            float(mark_efter_raw[i])
            if math.isfinite(float(mark_efter_raw[i]))
            else float(mark_fore_raw[i])
        )
        if not djup_efter_rutor or tak_efter_mask[i] or not math.isfinite(d_raw):
            vatten_djup_efter_m.append(None)
            vatten_yta_efter_m.append(None)
        else:
            d_val = max(0.0, d_raw)
            vatten_djup_efter_m.append(round(d_val, 4))
            if d_val > 0.0 and math.isfinite(m_raw):
                vatten_yta_efter_m.append(round(m_raw + d_val, 4))
            else:
                vatten_yta_efter_m.append(None)

    byggnad_fore, takhojd_fore_m = _sampla_byggnader(byggnader_fore, e_arr, n_arr, mark_fore_raw)
    mark_ref_efter = np.where(np.isfinite(mark_efter_raw), mark_efter_raw, mark_fore_raw)
    if byggnader_efter is not None:
        byggnad_efter, takhojd_efter_m = _sampla_byggnader(
            byggnader_efter, e_arr, n_arr, mark_ref_efter
        )
    else:
        byggnad_efter = [False] * antal
        takhojd_efter_m = [None] * antal

    vikter = _punkt_vikter(avstand_arr)
    mark_fore_min, mark_fore_max = _min_max(mark_fore_m)
    mark_efter_min, mark_efter_max = _min_max(mark_efter_m)

    sammanfattning: dict[str, Any] = {
        "langd_m": round(float(avstand_arr[-1]), 3),
        "antal_punkter": antal,
        "steg_m": round(float(eff_steg), 4),
        "mark_fore_min_m": mark_fore_min,
        "mark_fore_max_m": mark_fore_max,
        "mark_efter_min_m": mark_efter_min,
        "mark_efter_max_m": mark_efter_max,
        "max_djup_fore_m": _max_eller_none(vatten_djup_fore_m),
        "max_djup_efter_m": _max_eller_none(vatten_djup_efter_m),
        "oversvammad_bredd_002m_fore_m": _oversvammad_bredd(
            vatten_djup_fore_m, vikter, 0.02, bool(djup_fore_rutor)
        ),
        "oversvammad_bredd_002m_efter_m": _oversvammad_bredd(
            vatten_djup_efter_m, vikter, 0.02, bool(djup_efter_rutor)
        ),
        "oversvammad_bredd_010m_fore_m": _oversvammad_bredd(
            vatten_djup_fore_m, vikter, 0.10, bool(djup_fore_rutor)
        ),
        "oversvammad_bredd_010m_efter_m": _oversvammad_bredd(
            vatten_djup_efter_m, vikter, 0.10, bool(djup_efter_rutor)
        ),
    }

    return {
        "kontrakt": KONTRAKT,
        "epsg": EPSG,
        "hojdsystem": HOJDSYSTEM,
        "har_efter": har_efter,
        "avstand_m": [round(float(x), 3) for x in avstand_arr],
        "e": [round(float(x), 3) for x in e_arr],
        "n": [round(float(x), 3) for x in n_arr],
        "mark_fore_m": mark_fore_m,
        "mark_efter_m": mark_efter_m,
        "vatten_djup_fore_m": vatten_djup_fore_m,
        "vatten_djup_efter_m": vatten_djup_efter_m,
        "vatten_yta_fore_m": vatten_yta_fore_m,
        "vatten_yta_efter_m": vatten_yta_efter_m,
        "byggnad_fore": byggnad_fore,
        "byggnad_efter": byggnad_efter,
        "takhojd_fore_m": takhojd_fore_m,
        "takhojd_efter_m": takhojd_efter_m,
        "sammanfattning": sammanfattning,
    }
