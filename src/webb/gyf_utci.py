"""Webb-API-hjälpare för Svensk Grönytefaktor (GYF, U16) och Värmestress UTCI/T_mrt (U15).

Alla beräkningar är 100 % deterministiska i SWEREF 99 18 00 (EPSG:3011) och RH 2000:
- GYF beräknas med `ekologi.gyf.berakna_gyf_fran_raster` och `jamfor_gyf` mot de kommunala
  skissprofilerna (`stockholm`, `goteborg`, `malmo`) i `data/gyf/profiler.toml`.
- Värmestress beräknar verklig Sky View Factor (`svf`) och solskuggmask (`skuggmask`) direkt ur
  projektets K1 v2-geometri (`terrang.tif` + `byggnader.gpkg` + valfri `vegetation.tif`) via
  Flodens dagsljusmotor (`dagsljus.horisont` + `dagsljus.matt`) och kör
  `mikroklimat.berakna_varmestress` / `jamfor_varmestress`.
- Scenarier (K50-ritlager och K9-ändringar) tillämpas rent i minnet utan att skriva i projektmappar
  eller ändra rådata.
- Renderade PNG-lager (`utci_c`, `t_mrt_c`) ritas med `webb.resultatlager` (takmaskade via
  `MASKADE_ROLLER`) och cachas säkert per `(projekt_id, lager_id)` i minnet.
"""

from __future__ import annotations

import contextlib
import gc
import hashlib
import json
import math
import os
import re
import threading
import urllib.parse
import warnings
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

import numpy as np
import shapely
from numpy.typing import NDArray
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from dagsljus.horisont import (
    Hojdfalt,
    HorisontFel,
    horisont_riktning_ruta,
    horisont_ruta_iter,
)
from ekologi.gyf import (
    K1_MARKSLAG_BYGGNAD,
    GyfResultat,
    GyfTillagg,
    berakna_gyf_fran_raster,
    gyf_jamforelse_till_dict,
    gyf_till_dict,
    jamfor_gyf,
    ladda_gyf_profiler,
)
from ekologi.typer import EkologiFel
from gemensamt.hash import sha256_bytes
from karna import ritlager as ritl
from karna.geometri import (
    GeometriFel,
    k1_version,
    las_byggnader,
    las_markslag,
    las_terrang,
    las_vegetation,
)
from karna.geometri.lasa import vegetation_pa_marknat
from karna.ritat import RitatFel
from karna.scenario import Andring, ScenarioFel, las_scenario
from markmodell.byggnadsregister import ByggnadsPost
from markmodell.hinder_oppningar import HARDGJORT
from mikroklimat import (
    UTCI_STRESSKLASSER,
    VarmestressResultat,
    berakna_varmestress,
    jamfor_varmestress,
    varmestress_jamforelse_till_dict,
    varmestress_till_dict,
)
from testsvit.k1_io import GeoTransformInfo
from webb.fel import FlodenWebbFel
from webb.kartverktyg import KartverktygTjanst
from webb.projekt import PROJEKT_ID, ProjektTjanst
from webb.projektkalla import Projektkalla
from webb.resultatlager import (
    LagerFel,
    Utbredning,
    etikett_och_enhet,
    farg_for_roll,
    rita_bild,
    teckenforklaring,
    varde_intervall,
)

FORMAT_GYF_WEBB: Final[str] = "floden-gyf-webb/v1"
FORMAT_VARMESTRESS_WEBB: Final[str] = "floden-varmestress-webb/v1"
FORMAT_VADER_LUFT_WEBB: Final[str] = "floden-vader-luft-webb/v1"
FORMAT_LUFT_SKISS_WEBB: Final[str] = "floden-luft-skiss-webb/v1"
KOORDINATSYSTEM_EPSG: Final[str] = "EPSG:3011"

STANDARD_MAX_LUFT_SKISS_SEGMENT: Final[int] = 200
FLODEN_MAX_LUFT_SKISS_SEGMENT_ENV: Final[str] = "FLODEN_MAX_LUFT_SKISS_SEGMENT"
STANDARD_MAX_LUFT_SKISS_TIMMAR: Final[int] = 8784
FLODEN_MAX_LUFT_SKISS_TIMMAR_ENV: Final[str] = "FLODEN_MAX_LUFT_SKISS_TIMMAR"
_MKN_NO2_ARSMEDEL_UG_M3: Final[float] = 40.0
_MKN_PM10_ARSMEDEL_UG_M3: Final[float] = 40.0
_MKN_PM10_DYGN_90_4_UG_M3: Final[float] = 50.0
_K_NO_O3_STANDARD_PPB_1_S: Final[float] = 4.4e-4
_J_NO2_STANDARD_DAG_1_S: Final[float] = 5.0e-3

STANDARD_GYF_PROFIL: Final[str] = "stockholm"

STANDARD_T_LUFT_C: Final[float] = 30.0
STANDARD_RH_PCT: Final[float] = 45.0
STANDARD_VIND_10M_M_S: Final[float] = 1.5
STANDARD_SOL_DIREKT_W_M2: Final[float] = 750.0
STANDARD_SOL_DIFFUS_W_M2: Final[float] = 120.0
STANDARD_SOLHOJD_GRAD: Final[float] = 50.0
STANDARD_SOLAZIMUT_GRAD: Final[float] = 180.0
STANDARD_ANTAL_RIKTNINGAR: Final[int] = 16
STANDARD_MAX_AVSTAND_M: Final[float] = 200.0

TRAD_HOJD_GRANS_M: Final[float] = 2.0
BUSK_HOJD_MIN_M: Final[float] = 0.5
MAX_VARMESTRESS_CELLER: Final[int] = 360_000

STANDARD_MAX_WEBB_PNG_CACHE_MB: Final[float] = 32.0
FLODEN_MAX_WEBB_PNG_CACHE_MB_ENV: Final[str] = "FLODEN_MAX_WEBB_PNG_CACHE_MB"
STANDARD_MAX_WEBB_SVF_CACHE_MB: Final[float] = 32.0
FLODEN_MAX_WEBB_SVF_CACHE_MB_ENV: Final[str] = "FLODEN_MAX_WEBB_SVF_CACHE_MB"
STANDARD_MAX_GYF_CELLER: Final[int] = 4_000_000
FLODEN_MAX_GYF_CELLER_ENV: Final[str] = "FLODEN_MAX_GYF_CELLER"
STANDARD_MAX_VARMESTRESS_PROJEKT_CELLER: Final[int] = 4_000_000
FLODEN_MAX_VARMESTRESS_CELLER_ENV: Final[str] = "FLODEN_MAX_VARMESTRESS_CELLER"
_WEBB_GC_TROSKEL_CELLER: Final[int] = 500_000


def _las_miljo_cache_bytes(env_namn: str, standard_mb: float) -> int:
    """Läser cache-bytebudget (MB) ur en miljövariabel (`0` inaktiverar cachen helt)."""
    raw = os.environ.get(env_namn)
    if raw is None or not raw.strip():
        return max(1, int(standard_mb * 1024.0 * 1024.0))
    text = raw.strip()
    try:
        val = float(text)
    except ValueError as exc:
        raise FlodenWebbFel(
            f"Ogiltigt värde i {env_namn}={raw!r}: ange ett icke-negativt tal i MB "
            "(0 = inaktivera minnescache).",
            status_kod=500,
        ) from exc
    if not math.isfinite(val) or val < 0.0:
        raise FlodenWebbFel(
            f"Ogiltigt värde i {env_namn}={raw!r}: värdet måste vara ett ändligt tal >= 0 "
            "(0 = inaktivera minnescache).",
            status_kod=500,
        )
    if val == 0.0:
        return 0
    return max(1, int(val * 1024.0 * 1024.0))


def _las_miljo_grans_bytes(env_namn: str, standard_mb: float) -> int:
    """Läser storleksgräns i MB ur en miljövariabel (`0` eller tomt = standard)."""
    raw = os.environ.get(env_namn)
    if raw is None or not raw.strip():
        return max(1, int(standard_mb * 1024.0 * 1024.0))
    text = raw.strip()
    try:
        val = float(text)
    except ValueError as exc:
        raise FlodenWebbFel(
            f"Ogiltigt värde i {env_namn}={raw!r}: ange ett icke-negativt tal i MB "
            "(0 eller tomt = standard).",
            status_kod=500,
        ) from exc
    if not math.isfinite(val) or val < 0.0:
        raise FlodenWebbFel(
            f"Ogiltigt värde i {env_namn}={raw!r}: värdet måste vara ett ändligt tal >= 0 "
            "(0 eller tomt = standard).",
            status_kod=500,
        )
    eff_mb = standard_mb if val == 0.0 else val
    return max(1, int(eff_mb * 1024.0 * 1024.0))


def _las_miljo_celltak(env_namn: str, standard: int) -> int:
    """Läser cell- eller objekttak ur en miljövariabel (`0` eller tomt = standard)."""
    raw = os.environ.get(env_namn)
    if raw is None or not raw.strip():
        return standard
    text = raw.strip()
    try:
        val = int(text)
    except ValueError as exc:
        raise FlodenWebbFel(
            f"Ogiltigt värde i {env_namn}={raw!r}: ange ett icke-negativt heltal "
            "(0 eller tomt = standard).",
            status_kod=500,
        ) from exc
    if val < 0:
        raise FlodenWebbFel(
            f"Ogiltigt värde i {env_namn}={raw!r}: värdet får inte vara negativt "
            "(0 eller tomt = standard).",
            status_kod=500,
        )
    return standard if val == 0 else val


def max_webb_png_cache_bytes() -> int:
    """Konfigurerbar bytebudget per webb-PNG-cache (`FLODEN_MAX_WEBB_PNG_CACHE_MB`, 32 MB)."""
    return _las_miljo_cache_bytes(
        FLODEN_MAX_WEBB_PNG_CACHE_MB_ENV, STANDARD_MAX_WEBB_PNG_CACHE_MB
    )


def max_webb_svf_cache_bytes() -> int:
    """Konfigurerbar bytebudget för webbens SVF-cache (`FLODEN_MAX_WEBB_SVF_CACHE_MB`, 32 MB)."""
    return _las_miljo_cache_bytes(
        FLODEN_MAX_WEBB_SVF_CACHE_MB_ENV, STANDARD_MAX_WEBB_SVF_CACHE_MB
    )


def max_gyf_celler() -> int:
    """Fail-fast celltak för GYF-analys (`FLODEN_MAX_GYF_CELLER`, standard 4 000 000)."""
    return _las_miljo_celltak(FLODEN_MAX_GYF_CELLER_ENV, STANDARD_MAX_GYF_CELLER)


def max_varmestress_projekt_celler() -> int:
    """Fail-fast projektcelltak för värmestress (`FLODEN_MAX_VARMESTRESS_CELLER`, 4 000 000)."""
    return _las_miljo_celltak(
        FLODEN_MAX_VARMESTRESS_CELLER_ENV, STANDARD_MAX_VARMESTRESS_PROJEKT_CELLER
    )


@dataclass(frozen=True)
class _ProjektGeometri:
    """Sammanfogat K1 v2-underlag i EPSG:3011 för ett projekt (rent i minnet)."""

    omrade_id: str
    geo: GeoTransformInfo
    terrang_m: NDArray[np.float32]
    markslag: NDArray[np.float64]  # heltal 0..9 på giltiga celler, NaN utanför rutor
    har_data: NDArray[np.bool_]
    vegetation_m: NDArray[np.float32]
    byggnader: tuple[ByggnadsPost, ...]
    bygg_mask: NDArray[np.bool_]
    tomt_mask: NDArray[np.bool_]
    kalla: Projektkalla | None
    stampel: str


@dataclass(frozen=True)
class _ScenarioTillstand:
    """In-memory-tillämpat scenario för före/efter-analys av GYF och värmestress."""

    terrang_m: NDArray[np.float32]
    markslag: NDArray[np.float64]
    vegetation_m: NDArray[np.float32]
    byggnader: tuple[ByggnadsPost, ...]
    bygg_mask: NDArray[np.bool_]
    yt_overlagring: dict[str, NDArray[np.bool_]]
    nya_trad_mask: NDArray[np.bool_]
    nya_busk_mask: NDArray[np.bool_]
    nyckel: str


_LAGER_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,120}$")


class VarmestressLagerCache:
    """Trådsäker, bytebegränsad LRU-cache av värmestress-PNG:er per `(projekt_id, lager_id)`."""

    def __init__(self, max_poster: int = 64, *, max_bytes: int | None = None) -> None:
        self._max_poster = max(1, int(max_poster))
        if max_bytes is not None and (isinstance(max_bytes, bool) or int(max_bytes) < 0):
            raise ValueError(f"max_bytes måste vara >= 0, fick {max_bytes!r}.")
        self._max_bytes_fast: int | None = None if max_bytes is None else int(max_bytes)
        self._lock = threading.Lock()
        self._data: OrderedDict[tuple[str, str], tuple[bytes, Utbredning]] = OrderedDict()
        self._total_bytes = 0

    def _budget_bytes(self) -> int:
        return (
            self._max_bytes_fast
            if self._max_bytes_fast is not None
            else max_webb_png_cache_bytes()
        )

    @property
    def antal_bytes(self) -> int:
        with self._lock:
            return self._total_bytes

    @property
    def antal_poster(self) -> int:
        with self._lock:
            return len(self._data)

    def rensa(self) -> None:
        with self._lock:
            self._data.clear()
            self._total_bytes = 0

    def satt(self, projekt_id: str, lager_id: str, png: bytes, utbredning: Utbredning) -> None:
        nyckel = (projekt_id, lager_id)
        budget = self._budget_bytes()
        storlek = len(png)
        with self._lock:
            gammal = self._data.pop(nyckel, None)
            if gammal is not None:
                self._total_bytes -= len(gammal[0])
            if budget <= 0:
                self._data.clear()
                self._total_bytes = 0
                return
            if storlek > budget:
                while self._data and self._total_bytes > budget:
                    _, (bort_png, _) = self._data.popitem(last=False)
                    self._total_bytes -= len(bort_png)
                return
            self._data[nyckel] = (png, utbredning)
            self._data.move_to_end(nyckel)
            self._total_bytes += storlek
            while self._data and (
                len(self._data) > self._max_poster or self._total_bytes > budget
            ):
                _, (bort_png, _) = self._data.popitem(last=False)
                self._total_bytes -= len(bort_png)

    def hamta(self, projekt_id: str, lager_id: str) -> tuple[bytes, Utbredning] | None:
        if not _LAGER_ID_RE.match(lager_id):
            raise FlodenWebbFel("Ogiltigt värmestresslager-id.", status_kod=400)
        if self._budget_bytes() <= 0:
            return None
        nyckel = (projekt_id, lager_id)
        with self._lock:
            post = self._data.get(nyckel)
            if post is not None:
                self._data.move_to_end(nyckel)
            return post


class _SvfCache:
    """Trådsäker, bytebegränsad LRU-cache för geometriberoende SVF-fält."""

    def __init__(self, max_poster: int = 16, *, max_bytes: int | None = None) -> None:
        self._max_poster = max(1, int(max_poster))
        if max_bytes is not None and (isinstance(max_bytes, bool) or int(max_bytes) < 0):
            raise ValueError(f"max_bytes måste vara >= 0, fick {max_bytes!r}.")
        self._max_bytes_fast: int | None = None if max_bytes is None else int(max_bytes)
        self._lock = threading.Lock()
        self._data: OrderedDict[tuple[str, int, float], NDArray[np.float64]] = OrderedDict()
        self._total_bytes = 0

    def _budget_bytes(self) -> int:
        return (
            self._max_bytes_fast
            if self._max_bytes_fast is not None
            else max_webb_svf_cache_bytes()
        )

    @property
    def antal_bytes(self) -> int:
        with self._lock:
            return self._total_bytes

    @property
    def antal_poster(self) -> int:
        with self._lock:
            return len(self._data)

    def rensa(self) -> None:
        with self._lock:
            self._data.clear()
            self._total_bytes = 0

    def hamta(self, nyckel: tuple[str, int, float]) -> NDArray[np.float64] | None:
        if self._budget_bytes() <= 0:
            return None
        with self._lock:
            arr = self._data.get(nyckel)
            if arr is not None:
                self._data.move_to_end(nyckel)
            return arr

    def satt(self, nyckel: tuple[str, int, float], svf: NDArray[np.float64]) -> None:
        budget = self._budget_bytes()
        storlek = int(svf.nbytes)
        with self._lock:
            gammal = self._data.pop(nyckel, None)
            if gammal is not None:
                self._total_bytes -= int(gammal.nbytes)
            if budget <= 0:
                self._data.clear()
                self._total_bytes = 0
                return
            if storlek > budget:
                while self._data and self._total_bytes > budget:
                    _, bort_arr = self._data.popitem(last=False)
                    self._total_bytes -= int(bort_arr.nbytes)
                return
            self._data[nyckel] = svf
            self._data.move_to_end(nyckel)
            self._total_bytes += storlek
            while self._data and (
                len(self._data) > self._max_poster or self._total_bytes > budget
            ):
                _, bort_arr = self._data.popitem(last=False)
                self._total_bytes -= int(bort_arr.nbytes)


_GLOBAL_SVF_CACHE = _SvfCache()


# ------------------------------------------------------------------ parametertolkning


def _tolka_andligt_tal(
    payload: Mapping[str, Any],
    namn: str,
    standard: float,
    *,
    min_v: float | None = None,
    max_v: float | None = None,
    strikt_min: bool = False,
) -> float:
    raw = payload.get(namn)
    if raw is None or raw == "":
        varde = float(standard)
    elif isinstance(raw, bool):
        raise FlodenWebbFel(
            f"Fältet '{namn}' måste vara ett tal, inte sant/falskt.", status_kod=400
        )
    elif isinstance(raw, int | float):
        varde = float(raw)
    elif isinstance(raw, str):
        try:
            varde = float(raw.strip().replace(",", "."))
        except ValueError as exc:
            raise FlodenWebbFel(
                f"Fältet '{namn}' måste vara ett tal, fick {raw!r}.", status_kod=400
            ) from exc
    else:
        raise FlodenWebbFel(f"Fältet '{namn}' måste vara ett tal.", status_kod=400)
    if not math.isfinite(varde):
        raise FlodenWebbFel(f"Fältet '{namn}' måste vara ett ändligt tal.", status_kod=400)
    if min_v is not None and (
        (strikt_min and varde <= min_v) or (not strikt_min and varde < min_v)
    ):
        op = ">" if strikt_min else ">="
        raise FlodenWebbFel(
            f"Fältet '{namn}' måste uppfylla {op} {min_v:g}, fick {varde:g}.", status_kod=400
        )
    if max_v is not None and varde > max_v:
        raise FlodenWebbFel(
            f"Fältet '{namn}' måste uppfylla <= {max_v:g}, fick {varde:g}.", status_kod=400
        )
    return varde


def _tolka_heltal(
    payload: Mapping[str, Any],
    namn: str,
    standard: int,
    *,
    min_v: int,
    max_v: int,
) -> int:
    raw = payload.get(namn)
    if raw is None or raw == "":
        return standard
    if isinstance(raw, bool):
        raise FlodenWebbFel(f"Fältet '{namn}' måste vara ett heltal.", status_kod=400)
    if isinstance(raw, int):
        varde = raw
    elif isinstance(raw, float) and math.isfinite(raw) and raw.is_integer():
        varde = int(raw)
    elif isinstance(raw, str):
        try:
            f = float(raw.strip())
        except ValueError as exc:
            raise FlodenWebbFel(f"Fältet '{namn}' måste vara ett heltal.", status_kod=400) from exc
        if not math.isfinite(f) or not f.is_integer():
            raise FlodenWebbFel(f"Fältet '{namn}' måste vara ett heltal.", status_kod=400)
        varde = int(f)
    else:
        raise FlodenWebbFel(f"Fältet '{namn}' måste vara ett heltal.", status_kod=400)
    if varde < min_v or varde > max_v:
        raise FlodenWebbFel(
            f"Fältet '{namn}' måste ligga mellan {min_v} och {max_v}, fick {varde}.",
            status_kod=400,
        )
    return varde


def tolka_json_kropp(kropp_bytes: bytes) -> dict[str, Any]:
    if not kropp_bytes.strip():
        raise FlodenWebbFel(
            "Tom förfrågningskropp; JSON-objekt med 'omrade_id' krävs.", status_kod=400
        )
    try:
        payload = json.loads(kropp_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise FlodenWebbFel(f"Ogiltig JSON i förfrågan: {exc}", status_kod=400) from exc
    if not isinstance(payload, Mapping):
        raise FlodenWebbFel("Förfrågningskroppen måste vara ett JSON-objekt.", status_kod=400)
    return dict(payload)


def tolka_query_payload(
    query: Mapping[str, Sequence[str]], nycklar: Sequence[str]
) -> dict[str, Any]:
    ut: dict[str, Any] = {}
    for namn in nycklar:
        vals = query.get(namn)
        if vals and vals[0] != "":
            ut[namn] = vals[0]
    return ut


# ------------------------------------------------------------------ geometriladdning


def _punkter_i_ring(
    e_rutnat: NDArray[np.float64],
    n_rutnat: NDArray[np.float64],
    ring: Sequence[tuple[float, float]],
) -> NDArray[np.bool_]:
    """Vektoriserad ray-casting point-in-polygon (samma regel som markmodellens K1-bygge)."""
    innanfor = np.zeros(e_rutnat.shape, dtype=np.bool_)
    antal = len(ring)
    if antal < 3:
        return innanfor
    horn = ring[:-1] if ring[0] == ring[-1] and antal > 3 else ring
    m = len(horn)
    for i in range(m):
        e1, n1 = float(horn[i][0]), float(horn[i][1])
        e2, n2 = float(horn[(i + 1) % m][0]), float(horn[(i + 1) % m][1])
        korsar_n = (n1 > n_rutnat) != (n2 > n_rutnat)
        if not np.any(korsar_n):
            continue
        lutning = (e2 - e1) / ((n2 - n1) if n2 != n1 else 1e-18)
        skarning_e = e1 + (n_rutnat - n1) * lutning
        innanfor ^= korsar_n & (e_rutnat < skarning_e)
    return innanfor


def _byggnad_maske_geo(
    polygon: Sequence[tuple[float, float]],
    hal: Sequence[Sequence[tuple[float, float]]],
    geo: GeoTransformInfo,
) -> tuple[NDArray[np.bool_], tuple[int, int, int, int]] | None:
    """Byggnadens cellmask i godtyckligt rektangulärt `GeoTransformInfo` (1×2, 2×1, 2×2 m.fl.)."""
    if len(polygon) < 3:
        return None
    px_x = float(geo.pixelstorlek_x)
    px_y = float(geo.pixelstorlek_y)
    e_min_y = float(geo.x_min)
    n_max_y = float(geo.y_max)
    e_max_y = e_min_y + geo.bredd * px_x
    n_min_y = n_max_y - geo.hojd * px_y

    pe = [float(p[0]) for p in polygon]
    pn = [float(p[1]) for p in polygon]
    b_emin, b_emax = min(pe), max(pe)
    b_nmin, b_nmax = min(pn), max(pn)
    if b_emax < e_min_y or b_emin > e_max_y or b_nmax < n_min_y or b_nmin > n_max_y:
        return None

    kol0 = max(0, int(math.floor((b_emin - e_min_y) / px_x)) - 1)
    kol1 = min(geo.bredd, int(math.ceil((b_emax - e_min_y) / px_x)) + 1)
    rad0 = max(0, int(math.floor((n_max_y - b_nmax) / px_y)) - 1)
    rad1 = min(geo.hojd, int(math.ceil((n_max_y - b_nmin) / px_y)) + 1)
    if kol1 <= kol0 or rad1 <= rad0:
        return None

    e_vek = e_min_y + (np.arange(kol0, kol1, dtype=np.float64) + 0.5) * px_x
    n_vek = n_max_y - (np.arange(rad0, rad1, dtype=np.float64) + 0.5) * px_y
    sub_e, sub_n = np.meshgrid(e_vek, n_vek)
    mask = _punkter_i_ring(sub_e, sub_n, polygon)
    for ring in hal:
        mask &= ~_punkter_i_ring(sub_e, sub_n, ring)
    if not np.any(mask):
        return None
    return mask, (rad0, rad1, kol0, kol1)


def _markhojd_under_byggnad_geo(
    terrang_z: NDArray[np.float32],
    geo: GeoTransformInfo,
    polygon: Sequence[tuple[float, float]],
    hal: Sequence[Sequence[tuple[float, float]]] = (),
) -> float:
    """Median av terrängen under byggnad på godtyckligt rektangulärt `GeoTransformInfo`."""
    traff = _byggnad_maske_geo(polygon, hal, geo)
    if traff is not None:
        mask, (r0, r1, k0, k1) = traff
        v = terrang_z[r0:r1, k0:k1][mask]
        if np.isfinite(v).any():
            return float(np.nanmedian(v))
        for marg in (2, 5, 10, 25, 50):
            ram = terrang_z[max(r0 - marg, 0) : r1 + marg, max(k0 - marg, 0) : k1 + marg]
            if np.isfinite(ram).any():
                return float(np.nanmedian(ram))
    cx = sum(float(p[0]) for p in polygon) / len(polygon)
    cy = sum(float(p[1]) for p in polygon) / len(polygon)
    kol = min(max(int(math.floor((cx - geo.x_min) / geo.pixelstorlek_x)), 0), geo.bredd - 1)
    rad = min(max(int(math.floor((geo.y_max - cy) / geo.pixelstorlek_y)), 0), geo.hojd - 1)
    v0 = float(terrang_z[rad, kol])
    if np.isfinite(v0):
        return v0
    for marg in (2, 5, 10, 25, 50):
        ram = terrang_z[max(rad - marg, 0) : rad + marg + 1, max(kol - marg, 0) : kol + marg + 1]
        if np.isfinite(ram).any():
            return float(np.nanmedian(ram))
    return v0


def _brann_byggnader_mask(
    terrang: NDArray[np.float32],
    geo: GeoTransformInfo,
    byggnader: Sequence[ByggnadsPost],
) -> tuple[NDArray[np.float32], NDArray[np.bool_]]:
    """Bygger DSM och fotavtrycksmask på godtyckligt rektangulärt rutnät (samma regel som K1 v2)."""
    ny_z = np.array(terrang, dtype=np.float32, copy=True)
    bygg_mask = np.zeros(terrang.shape, dtype=np.bool_)
    if not byggnader:
        return ny_z, bygg_mask

    for post in byggnader:
        b = post.till_byggnad()
        traff = _byggnad_maske_geo(b.polygon, b.hal, geo)
        if traff is None:
            continue
        mask, (rad0, rad1, kol0, kol1) = traff
        z_sub = ny_z[rad0:rad1, kol0:kol1]
        mask_sub = bygg_mask[rad0:rad1, kol0:kol1]
        nya_bygg_mask = mask & (~mask_sub)
        if b.takhoyd_rh2000 is not None:
            takhoyd = np.float32(b.takhoyd_rh2000)
            z_sub[mask] = np.maximum(z_sub[mask], takhoyd)
        else:
            z_sub[nya_bygg_mask] = z_sub[nya_bygg_mask] + np.float32(b.hinderhojd_m)
        mask_sub |= mask

    return ny_z, bygg_mask


def _las_geotiff_huvud(fil: Path) -> GeoTransformInfo:
    """Läser georeferens och dimensioner ur ett GeoTIFF-huvud utan att läsa pixlar."""
    if not fil.is_file():
        raise FlodenWebbFel(f"Saknar rasterfil: {fil.name} i {fil.parent.name}.", status_kod=400)
    try:
        import rasterio
    except ImportError:  # pragma: no cover - rasterio är ett beroende
        from testsvit.k1_io import las_geotiff

        try:
            _, geo = las_geotiff(fil)
            return geo
        except Exception as exc:  # noqa: BLE001
            raise FlodenWebbFel(
                f"Kunde inte läsa {fil.name} i {fil.parent.name}: {exc}", status_kod=400
            ) from exc
    try:
        with rasterio.open(fil) as src:
            t = src.transform
            epsg = src.crs.to_epsg() if src.crs is not None else 0
            return GeoTransformInfo(
                bredd=int(src.width),
                hojd=int(src.height),
                x_min=float(t.c),
                y_max=float(t.f),
                pixelstorlek_x=float(t.a),
                pixelstorlek_y=float(-t.e),
                epsg=int(epsg or 0),
                dtype=str(src.dtypes[0]),
            )
    except (OSError, rasterio.errors.RasterioError) as exc:
        raise FlodenWebbFel(
            f"Kunde inte läsa {fil.name} i {fil.parent.name}: {exc}", status_kod=400
        ) from exc


def _uppskatta_k1_mosaik_celler(
    rutor: Sequence[Path],
    *,
    filnamn: str = "markslag.tif",
    krav_samma_rutnat: bool = True,
) -> tuple[int, int, float, float, float, list[GeoTransformInfo]]:
    """Beräknar mosaikens `(hojd, bredd, x0, y1, px, geos)` ur TIFF-huvuden (O(1) RAM)."""
    if not rutor:
        raise FlodenWebbFel("Projektet saknar K1-rutor att analysera.", status_kod=404)
    geos: list[GeoTransformInfo] = []
    px: float | None = None
    for mapp in rutor:
        g = _las_geotiff_huvud(mapp / filnamn)
        if abs(g.pixelstorlek_x - g.pixelstorlek_y) > 1e-9:
            raise FlodenWebbFel("Icke-kvadratiska celler i K1-rutan stöds inte.", status_kod=400)
        if px is None:
            px = float(g.pixelstorlek_x)
        elif abs(px - float(g.pixelstorlek_x)) > 1e-9:
            if krav_samma_rutnat:
                raise FlodenWebbFel(
                    f"K1-rutorna har olika cellstorlek ({px} m och {g.pixelstorlek_x} m).",
                    status_kod=400,
                )
            px = min(px, float(g.pixelstorlek_x))
        geos.append(g)

    assert px is not None
    if len(geos) == 1:
        g0 = geos[0]
        return g0.hojd, g0.bredd, float(g0.x_min), float(g0.y_max), px, geos

    x0 = min(float(g.x_min) for g in geos)
    y1 = max(float(g.y_max) for g in geos)
    x1 = max(float(g.x_min) + g.bredd * float(g.pixelstorlek_x) for g in geos)
    y0 = min(float(g.y_max) - g.hojd * float(g.pixelstorlek_y) for g in geos)
    bredd = max(1, round((x1 - x0) / px))
    hojd = max(1, round((y1 - y0) / px))
    if krav_samma_rutnat:
        for g in geos:
            c0f = (g.x_min - x0) / px
            r0f = (y1 - g.y_max) / px
            if abs(c0f - round(c0f)) > 1e-3 or abs(r0f - round(r0f)) > 1e-3:
                raise FlodenWebbFel(
                    "K1-rutorna ligger inte på samma cellrutnät.", status_kod=400
                )
    return hojd, bredd, x0, y1, px, geos


def _kontrollera_webb_celltak(
    n_celler: int,
    antal_rutor: int,
    max_celler: int | None,
    *,
    kontext: str,
    env_namn: str | None = None,
) -> None:
    if max_celler is None or n_celler <= max_celler:
        return
    rut_txt = "ruta" if antal_rutor == 1 else "rutor"
    env_del = f" ({env_namn})" if env_namn else ""
    raise FlodenWebbFel(
        f"Projektområdet omfattar {n_celler} celler ({antal_rutor} {rut_txt}), vilket "
        f"överstiger serverns minnesgräns på {max_celler} celler för {kontext}{env_del}. "
        "Välj ett mindre projektområde eller kör på en maskin med mer RAM.",
        status_kod=400,
    )


def _las_en_k1_ruta_data(
    mapp: Path,
    *,
    krav_v2_terrang: bool,
    bygg_per_id: dict[str, ByggnadsPost],
) -> tuple[NDArray[np.float32], NDArray[np.uint8], NDArray[np.float32], GeoTransformInfo]:
    """Läser terräng, markslag, vegetation och byggnader för en enskild K1-ruta."""
    try:
        slag, geo_s = las_markslag(mapp)
    except (GeometriFel, OSError, ValueError) as exc:
        raise FlodenWebbFel(
            f"Kunde inte läsa markslag.tif i {mapp.name}: {exc}", status_kod=400
        ) from exc

    if abs(geo_s.pixelstorlek_x - geo_s.pixelstorlek_y) > 1e-9:
        raise FlodenWebbFel("Icke-kvadratiska celler i K1-rutan stöds inte.", status_kod=400)

    try:
        v = k1_version(mapp)
        if v == 2 or krav_v2_terrang:
            terr, geo_t = las_terrang(mapp)
            for b in las_byggnader(mapp):
                bygg_per_id.setdefault(b.byggnad_id, b)
        elif (mapp / "terrang.tif").is_file():
            terr, geo_t = las_terrang(mapp)
        else:
            from karna.geometri import las_mark

            terr, geo_t = las_mark(mapp)
    except (GeometriFel, OSError, ValueError) as exc:
        raise FlodenWebbFel(
            f"Kunde inte läsa K1 v2-geometri i {mapp.name}: {exc}", status_kod=400
        ) from exc

    if terr.shape != slag.shape or abs(geo_t.x_min - geo_s.x_min) > 1e-6:
        raise FlodenWebbFel(
            f"terrang.tif och markslag.tif matchar inte i {mapp.name}.", status_kod=400
        )

    veg_res = las_vegetation(mapp)
    if veg_res is not None:
        try:
            veg_m = vegetation_pa_marknat(veg_res[0], veg_res[1], geo_s)
        except GeometriFel:
            veg_m = np.zeros(slag.shape, dtype=np.float32)
    else:
        veg_m = np.zeros(slag.shape, dtype=np.float32)
    veg_m = np.where(np.isfinite(veg_m), np.maximum(veg_m, np.float32(0.0)), np.float32(0.0))
    return np.asarray(terr, dtype=np.float32), slag, veg_m, geo_s


def _sammanfoga_k1_rutor(
    rutor: Sequence[Path],
    *,
    krav_v2_terrang: bool,
    max_celler: int | None = None,
    max_celler_kontext: str = "analysen",
    max_celler_env: str | None = None,
) -> tuple[
    GeoTransformInfo,
    NDArray[np.float32],
    NDArray[np.float64],
    NDArray[np.bool_],
    NDArray[np.float32],
    tuple[ByggnadsPost, ...],
]:
    """Sammanfogar en eller flera K1-rutor med regeln 'djupast inne i rutan' (strömmande i RAM)."""
    if not rutor:
        raise FlodenWebbFel("Projektet saknar K1-rutor att analysera.", status_kod=404)

    hojd, bredd, x0, y1, px, _ = _uppskatta_k1_mosaik_celler(rutor, filnamn="markslag.tif")
    _kontrollera_webb_celltak(
        hojd * bredd,
        len(rutor),
        max_celler,
        kontext=max_celler_kontext,
        env_namn=max_celler_env,
    )

    bygg_per_id: dict[str, ByggnadsPost] = {}
    if len(rutor) == 1:
        terr0, slag0, veg0, geo0 = _las_en_k1_ruta_data(
            rutor[0], krav_v2_terrang=krav_v2_terrang, bygg_per_id=bygg_per_id
        )
        byggnader = tuple(sorted(bygg_per_id.values(), key=lambda b: b.byggnad_id))
        har_data = np.ones(slag0.shape, dtype=np.bool_)
        return (
            geo0,
            terr0,
            slag0.astype(np.float64),
            har_data,
            veg0,
            byggnader,
        )

    terr_ut = np.full((hojd, bredd), np.nan, dtype=np.float32)
    slag_ut = np.full((hojd, bredd), np.nan, dtype=np.float64)
    veg_ut = np.zeros((hojd, bredd), dtype=np.float32)
    har_data = np.zeros((hojd, bredd), dtype=np.bool_)
    djupast = np.full((hojd, bredd), -np.inf, dtype=np.float32)

    for mapp in rutor:
        terr, slag, veg, g = _las_en_k1_ruta_data(
            mapp, krav_v2_terrang=krav_v2_terrang, bygg_per_id=bygg_per_id
        )
        c0f = (g.x_min - x0) / px
        r0f = (y1 - g.y_max) / px
        if abs(c0f - round(c0f)) > 1e-3 or abs(r0f - round(r0f)) > 1e-3:
            raise FlodenWebbFel("K1-rutorna ligger inte på samma cellrutnät.", status_kod=400)
        c0i, r0i = round(c0f), round(r0f)
        h_i, b_i = slag.shape
        rr = np.arange(h_i, dtype=np.float32)
        cc = np.arange(b_i, dtype=np.float32)
        avst = np.minimum(
            np.minimum(rr[:, None] + 0.5, h_i - rr[:, None] - 0.5),
            np.minimum(cc[None, :] + 0.5, b_i - cc[None, :] - 0.5),
        )
        fonster = (slice(r0i, r0i + h_i), slice(c0i, c0i + b_i))
        battre = avst > djupast[fonster]
        terr_ut[fonster] = np.where(battre, terr, terr_ut[fonster])
        slag_ut[fonster] = np.where(battre, slag.astype(np.float64), slag_ut[fonster])
        veg_ut[fonster] = np.where(battre, veg, veg_ut[fonster])
        har_data[fonster] |= battre
        djupast[fonster] = np.where(battre, avst, djupast[fonster])
        del terr, slag, veg, avst, battre

    del djupast
    byggnader = tuple(sorted(bygg_per_id.values(), key=lambda b: b.byggnad_id))
    geo_ut = GeoTransformInfo(
        bredd=bredd,
        hojd=hojd,
        x_min=float(x0),
        y_max=float(y1),
        pixelstorlek_x=float(px),
        pixelstorlek_y=float(px),
        epsg=3011,
        dtype="float32",
    )
    return geo_ut, terr_ut, slag_ut, har_data, veg_ut, byggnader


def _tomt_mask_fran_form(
    form: BaseGeometry | None,
    geo: GeoTransformInfo,
    har_data: NDArray[np.bool_],
) -> NDArray[np.bool_]:
    if form is None:
        return har_data.copy()
    px = float(geo.pixelstorlek_x)
    e = geo.x_min + (np.arange(geo.bredd, dtype=np.float64) + 0.5) * px
    n = geo.y_max - (np.arange(geo.hojd, dtype=np.float64) + 0.5) * px
    ge, gn = np.meshgrid(e, n)
    inom = np.asarray(shapely.contains_xy(form, ge, gn), dtype=np.bool_)
    mask = inom & har_data
    if not np.any(mask):
        # Om polygonens kanter tangerar cellmittpunkter (smal rektangel) prövas intersects.
        mask = np.asarray(shapely.intersects_xy(form, ge, gn), dtype=np.bool_) & har_data
    if not np.any(mask):
        raise FlodenWebbFel(
            "Projektets avgränsning täcker inga giltiga celler i K1-markmodellen.",
            status_kod=400,
        )
    return mask


def _ladda_projekt_geometri(
    kartverktyg: KartverktygTjanst,
    projekt_tjanst: ProjektTjanst,
    omrade_id: str,
    *,
    krav_v2_terrang: bool,
    max_celler: int | None = None,
    max_celler_kontext: str = "analysen",
    max_celler_env: str | None = None,
) -> _ProjektGeometri:
    """Laddar projektets K1-geometri via `kartverktyg.kalla(omrade_id)` (både demo och geografi)."""
    kalla: Projektkalla | None = None
    rutor: tuple[Path, ...] = ()
    form: BaseGeometry | None = None
    stampel_delar: list[str] = [omrade_id]

    try:
        kalla = kartverktyg.kalla(omrade_id)
    except FlodenWebbFel as exc:
        if omrade_id == PROJEKT_ID and exc.status_kod == 404:
            # Demokvarteret kan ha byggd K1-markmodell utan färdiga demokörningar (t.ex. i tester).
            rutor = projekt_tjanst.markmodell_rutor(omrade_id)
        else:
            raise

    if kalla is not None:
        mall = (
            kalla.mallar.get("vatten")
            or kalla.mallar.get("dagsljus")
            or next(iter(kalla.mallar.values()), None)
        )
        if mall is None:
            raise FlodenWebbFel(f"Projektet '{omrade_id}' saknar K1-bas.", status_kod=404)
        rutor = tuple(r.mapp for r in mall.rutor if (r.mapp / "markslag.tif").is_file())
        form = kalla.projektform()
        stampel_delar.append(str(kalla.stampel))

    if not rutor:
        raise FlodenWebbFel(
            f"Projektet '{omrade_id}' saknar K1-rutor med markslag.tif.",
            status_kod=404,
        )

    for mapp in rutor:
        for filnamn in ("terrang.tif", "markslag.tif", "byggnader.gpkg", "vegetation.tif"):
            p = mapp / filnamn
            if p.is_file():
                with contextlib.suppress(OSError):
                    stampel_delar.append(f"{mapp.name}/{filnamn}:{p.stat().st_mtime_ns}")

    geo, terrang_m, markslag, har_data, veg_m, byggnader = _sammanfoga_k1_rutor(
        rutor,
        krav_v2_terrang=krav_v2_terrang,
        max_celler=max_celler,
        max_celler_kontext=max_celler_kontext,
        max_celler_env=max_celler_env,
    )
    tomt_mask = _tomt_mask_fran_form(form, geo, har_data)
    _, bygg_mask = _brann_byggnader_mask(
        np.where(np.isfinite(terrang_m), terrang_m, np.float32(0.0)),
        geo,
        byggnader,
    )
    # För v1-rutor utan byggnader.gpkg används markslag 9 som byggnadsmask.
    if not byggnader:
        bygg_mask = np.isfinite(markslag) & (markslag == float(K1_MARKSLAG_BYGGNAD))
    else:
        bygg_mask &= har_data

    stampel = sha256_bytes("|".join(stampel_delar).encode("utf-8"))[:16]
    return _ProjektGeometri(
        omrade_id=omrade_id,
        geo=geo,
        terrang_m=terrang_m,
        markslag=markslag,
        har_data=har_data,
        vegetation_m=veg_m,
        byggnader=byggnader,
        bygg_mask=bygg_mask,
        tomt_mask=tomt_mask,
        kalla=kalla,
        stampel=stampel,
    )


# ------------------------------------------------------------------ scenarioladdning & minne


def _rasterisera_polygon(
    geometri: Mapping[str, Any] | None,
    geo: GeoTransformInfo,
) -> NDArray[np.bool_] | None:
    if not isinstance(geometri, Mapping) or geometri.get("type") != "Polygon":
        return None
    koord = geometri.get("coordinates")
    if not isinstance(koord, Sequence) or not koord:
        return None
    ringar = [
        [(float(p[0]), float(p[1])) for p in r]
        for r in koord
        if isinstance(r, Sequence) and len(r) >= 3
    ]
    if not ringar:
        return None
    yttre = ringar[0]
    hal = ringar[1:]
    try:
        poly = Polygon(yttre, holes=hal)
    except (TypeError, ValueError):
        return None
    if poly.is_empty:
        return None
    minx, miny, maxx, maxy = poly.bounds
    px = float(geo.pixelstorlek_x)
    k0 = max(int(math.floor((minx - geo.x_min) / px)) - 1, 0)
    k1 = min(int(math.ceil((maxx - geo.x_min) / px)) + 1, geo.bredd)
    r0 = max(int(math.floor((geo.y_max - maxy) / px)) - 1, 0)
    r1 = min(int(math.ceil((geo.y_max - miny) / px)) + 1, geo.hojd)
    if k1 <= k0 or r1 <= r0:
        return None
    e = geo.x_min + (np.arange(k0, k1, dtype=np.float64) + 0.5) * px
    n = geo.y_max - (np.arange(r0, r1, dtype=np.float64) + 0.5) * px
    ge, gn = np.meshgrid(e, n)
    lokal = np.asarray(shapely.contains_xy(poly, ge, gn), dtype=np.bool_)
    if not np.any(lokal):
        return None
    mask = np.zeros((geo.hojd, geo.bredd), dtype=np.bool_)
    mask[r0:r1, k0:k1] = lokal
    return mask


def _fyll_borttagen_byggnad_markslag(
    markslag: NDArray[np.float64],
    fyll: NDArray[np.bool_],
    giltig: NDArray[np.bool_],
) -> NDArray[np.float64]:
    """Ersätter riven byggnads celler med närmaste giltiga markslag (annars hårdgjort=1)."""
    from scipy import ndimage

    ut = markslag.copy()
    if not np.any(fyll):
        return ut
    kalla_ok = giltig & np.isfinite(ut) & (~fyll) & (ut != float(K1_MARKSLAG_BYGGNAD))
    if not np.any(kalla_ok):
        ut[fyll] = float(HARDGJORT)
        return ut
    idx: Any = ndimage.distance_transform_edt(
        ~kalla_ok, return_distances=False, return_indices=True
    )
    ut[fyll] = ut[idx[0][fyll], idx[1][fyll]]
    return ut


def _ladda_scenario_andringar(
    kartverktyg: KartverktygTjanst,
    proj_geom: _ProjektGeometri,
    scenario: str | None,
    inline_ritlager: Mapping[str, Any] | None = None,
    inline_andringar: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], str] | None:
    """Hämtar K9-ändringar för ett scenario eller inline-förslag utan att skriva något på disk."""
    andringar: list[dict[str, Any]] = []
    if scenario is not None:
        if kartverktyg.finns_scenario(proj_geom.omrade_id, scenario):
            sc_dok = (
                kartverktyg.hamta_scenario(proj_geom.omrade_id, scenario)
                if proj_geom.kalla is not None
                else kartverktyg._las(proj_geom.omrade_id, scenario)  # noqa: SLF001 - samma paket
            )
            ra_andringar = sc_dok.get("andringar")
            if isinstance(ra_andringar, list):
                andringar.extend(dict(a) for a in ra_andringar if isinstance(a, Mapping))
            elif sc_dok.get("ritlager") is not None:
                try:
                    andringar.extend(ritl.oversatt(sc_dok["ritlager"]).k9)
                except RitatFel as exc:
                    raise FlodenWebbFel(
                        f"Ogiltigt ritlager i scenario '{scenario}': {exc}", status_kod=400
                    ) from exc
        else:
            hittad_fil: Path | None = None
            kalla = proj_geom.kalla
            if kalla is not None and kalla.mapp is not None:
                sfil = kalla.mapp / "scenarier.json"
                if sfil.is_file():
                    try:
                        raw = json.loads(sfil.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        raw = {}
                    for post in raw.get("scenarier", []) if isinstance(raw, Mapping) else []:
                        if isinstance(post, Mapping) and post.get("slug") == scenario:
                            for nyckel in ("k9_vatten", "k9_dagsljus", "k9"):
                                rel = post.get(nyckel)
                                if isinstance(rel, str) and (kalla.mapp / rel).is_file():
                                    hittad_fil = kalla.mapp / rel
                                    break
                            break
            if hittad_fil is None:
                raise FlodenWebbFel(
                    f"Det finns inget sparat scenario '{scenario}' i projektet "
                    f"'{proj_geom.omrade_id}'.",
                    status_kod=404,
                )
            try:
                sc_obj = las_scenario(hittad_fil)
            except (ScenarioFel, OSError) as exc:
                raise FlodenWebbFel(
                    f"Kunde inte läsa scenario '{scenario}': {exc}", status_kod=400
                ) from exc
            andringar.extend(a.till_dict() for a in sc_obj.andringar)

    if inline_ritlager is not None or inline_andringar is not None:
        fria = [dict(a) for a in (inline_andringar or ())]
        try:
            ihop, _ = ritl.sla_ihop(fria, inline_ritlager)
        except RitatFel as exc:
            raise FlodenWebbFel(f"Ogiltigt ritlager/förslag: {exc}", status_kod=400) from exc
        andringar.extend(ihop)

    if scenario is None and inline_ritlager is None and inline_andringar is None:
        return None

    # Deterministisk sortering efter andring_id (samma regel som karna.k9_till_k1).
    andringar.sort(key=lambda a: str(a.get("andring_id", "")))
    nyckel = sha256_bytes(
        json.dumps(andringar, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )[:16]
    return andringar, nyckel


def _tillampa_scenario_i_minnet(
    proj_geom: _ProjektGeometri,
    andringar: Sequence[Mapping[str, Any]],
    nyckel: str,
) -> _ScenarioTillstand:
    """Tillämpar K9/K50-ändringar i minnet på terräng, markslag, vegetation och byggnader."""
    geo = proj_geom.geo
    terrang = proj_geom.terrang_m.copy()
    markslag = proj_geom.markslag.copy()
    veg = proj_geom.vegetation_m.copy()
    bygg_per_id: dict[str, ByggnadsPost] = {b.byggnad_id: b for b in proj_geom.byggnader}
    yt_overlagring: dict[str, NDArray[np.bool_]] = {}
    nya_trad_mask = np.zeros(proj_geom.tomt_mask.shape, dtype=np.bool_)
    nya_busk_mask = np.zeros(proj_geom.tomt_mask.shape, dtype=np.bool_)
    borttagna_bygg: list[ByggnadsPost] = []

    for ra in andringar:
        try:
            a = Andring.fran_dict(ra)
        except ScenarioFel as exc:
            raise FlodenWebbFel(f"Ogiltig K9-ändring: {exc}", status_kod=400) from exc
        e = a.egenskaper

        if a.typ == "byggnad_bort":
            bid = str(e.get("byggnads_id", ""))
            gammal = bygg_per_id.pop(bid, None)
            if gammal is not None:
                borttagna_bygg.append(gammal)

        elif a.typ == "byggnad_hojd":
            bid = str(e.get("byggnads_id", ""))
            bef = bygg_per_id.get(bid)
            if bef is not None:
                if "tak_rh2000" in e and not isinstance(e["tak_rh2000"], bool):
                    ny_tak = float(e["tak_rh2000"])
                    ny_hojd = max(0.1, ny_tak - bef.markhojd_rh2000)
                elif "hojd_m" in e and not isinstance(e["hojd_m"], bool):
                    ny_hojd = max(0.1, float(e["hojd_m"]))
                    ny_tak = bef.markhojd_rh2000 + ny_hojd
                else:
                    continue
                bygg_per_id[bid] = replace(
                    bef,
                    takhojd_rh2000=ny_tak,
                    hojd_m=ny_hojd,
                    hojdkalla="andring",
                )

        elif a.typ == "byggnad_ny":
            mask = _rasterisera_polygon(a.geometri, geo)
            if mask is None:
                continue
            koord = (a.geometri or {}).get("coordinates") or []
            yttre = tuple((float(p[0]), float(p[1])) for p in koord[0])
            if len(yttre) > 1 and yttre[0] == yttre[-1]:
                yttre = yttre[:-1]
            hal_ringar = tuple(
                tuple(
                    (float(p[0]), float(p[1]))
                    for p in (r[:-1] if len(r) > 1 and r[0] == r[-1] else r)
                )
                for r in koord[1:]
            )
            terr_saker = np.where(np.isfinite(terrang), terrang, np.float32(0.0))
            try:
                mark_under = float(_markhojd_under_byggnad_geo(terr_saker, geo, yttre, hal_ringar))
            except Exception:  # noqa: BLE001 - fallback till medelvärde inom fotavtrycket
                vals = terrang[mask & np.isfinite(terrang)]
                mark_under = float(np.median(vals)) if vals.size else 0.0
            if "tak_rh2000" in e and not isinstance(e["tak_rh2000"], bool):
                tak_rh = float(e["tak_rh2000"])
                hojd_m = max(0.1, tak_rh - mark_under)
            else:
                hojd_raw = e.get("hojd_m", 9.0)
                hojd_m = max(0.1, float(hojd_raw)) if not isinstance(hojd_raw, bool) else 9.0
                tak_rh = mark_under + hojd_m
            bygg_per_id[a.andring_id] = ByggnadsPost(
                byggnad_id=a.andring_id,
                polygon=yttre,
                hal=hal_ringar,
                takhojd_rh2000=tak_rh,
                markhojd_rh2000=mark_under,
                hojd_m=hojd_m,
                takform=str(e["takform"]) if e.get("takform") is not None else None,
                andamal=str(e["andamal"]) if e.get("andamal") is not None else None,
                vaningar=int(e["vaningar"]) if e.get("vaningar") is not None else None,
                kalla="plan",
                osakerhet_hojd_m=None,
                hojdkalla="andring",
            )
            markslag[mask & proj_geom.har_data] = float(K1_MARKSLAG_BYGGNAD)
            veg[mask] = np.float32(0.0)
            nya_trad_mask &= ~mask
            nya_busk_mask &= ~mask

        elif a.typ == "markhojd":
            mask = _rasterisera_polygon(a.geometri, geo)
            if mask is None:
                continue
            if "niva_rh2000" in e and not isinstance(e["niva_rh2000"], bool):
                terrang[mask] = np.float32(float(e["niva_rh2000"]))
            elif "delta_m" in e and not isinstance(e["delta_m"], bool):
                terrang[mask] = terrang[mask] + np.float32(float(e["delta_m"]))
            if (
                "markslag" in e
                and e["markslag"] is not None
                and not isinstance(e["markslag"], bool)
            ):
                markslag[mask & proj_geom.har_data] = float(int(e["markslag"]))

        elif a.typ == "markslag":
            mask = _rasterisera_polygon(a.geometri, geo)
            if mask is None:
                continue
            kod = e.get("kod")
            if kod is not None and not isinstance(kod, bool):
                markslag[mask & proj_geom.har_data] = float(int(kod))

        elif a.typ == "magasin":
            mask = _rasterisera_polygon(a.geometri, geo)
            if mask is None:
                continue
            if (
                "markslag" in e
                and e["markslag"] is not None
                and not isinstance(e["markslag"], bool)
            ):
                markslag[mask & proj_geom.har_data] = float(int(e["markslag"]))
            else:
                bef_regn = yt_overlagring.get("regnbadd")
                yt_overlagring["regnbadd"] = (
                    (bef_regn | mask) if bef_regn is not None else mask.copy()
                )

        elif a.typ == "vegetation":
            mask = _rasterisera_polygon(a.geometri, geo)
            if mask is None:
                continue
            hojd_raw = e.get("hojd_m")
            if hojd_raw is not None and not isinstance(hojd_raw, bool):
                h_m = max(0.0, float(hojd_raw))
                veg[mask] = np.float32(h_m)
                if h_m >= TRAD_HOJD_GRANS_M:
                    nya_trad_mask |= mask
                    nya_busk_mask &= ~mask
                elif h_m >= BUSK_HOJD_MIN_M:
                    nya_busk_mask |= mask
                    nya_trad_mask &= ~mask
                else:
                    nya_trad_mask &= ~mask
                    nya_busk_mask &= ~mask

    bygg_efter = tuple(sorted(bygg_per_id.values(), key=lambda b: b.byggnad_id))
    _, bygg_mask_efter = _brann_byggnader_mask(
        np.where(np.isfinite(terrang), terrang, np.float32(0.0)),
        geo,
        bygg_efter,
    )
    bygg_mask_efter &= proj_geom.har_data

    if borttagna_bygg:
        _, bort_mask = _brann_byggnader_mask(
            np.where(np.isfinite(terrang), terrang, np.float32(0.0)),
            geo,
            borttagna_bygg,
        )
        rivna_celler = bort_mask & (~bygg_mask_efter) & (markslag == float(K1_MARKSLAG_BYGGNAD))
        markslag = _fyll_borttagen_byggnad_markslag(markslag, rivna_celler, proj_geom.har_data)

    return _ScenarioTillstand(
        terrang_m=terrang,
        markslag=markslag,
        vegetation_m=veg,
        byggnader=bygg_efter,
        bygg_mask=bygg_mask_efter,
        yt_overlagring=yt_overlagring,
        nya_trad_mask=nya_trad_mask,
        nya_busk_mask=nya_busk_mask,
        nyckel=nyckel,
    )


# ------------------------------------------------------------------ GYF (U16)


def _tolka_gyf_tillagg(raw: Any) -> list[GyfTillagg]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise FlodenWebbFel("Fältet 'tillagg' ska vara en lista av objekt.", status_kod=400)
    ut: list[GyfTillagg] = []
    for idx, post in enumerate(raw):
        if not isinstance(post, Mapping):
            raise FlodenWebbFel(f"tillagg[{idx}] måste vara ett JSON-objekt.", status_kod=400)
        tid = post.get("tillagg_id")
        if not isinstance(tid, str) or not tid.strip():
            raise FlodenWebbFel(f"tillagg[{idx}] saknar giltigt 'tillagg_id'.", status_kod=400)
        area = _tolka_andligt_tal(post, "area_m2", -1.0, min_v=0.0)
        faktor_raw = post.get("faktor")
        faktor: float | None = None
        if faktor_raw is not None:
            faktor = _tolka_andligt_tal(post, "faktor", 0.0, min_v=0.0)
        namn = str(post.get("namn", "")) if post.get("namn") is not None else ""
        try:
            ut.append(GyfTillagg(tillagg_id=tid.strip(), area_m2=area, faktor=faktor, namn=namn))
        except EkologiFel as exc:
            raise FlodenWebbFel(str(exc), status_kod=400) from exc
    return ut


def kor_gyf_analys(
    kartverktyg: KartverktygTjanst,
    projekt_tjanst: ProjektTjanst,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Deterministisk GYF-analys (med valfri före/efter-jämförelse) för `/api/analys/gyf`."""
    omrade_id = payload.get("omrade_id")
    if not isinstance(omrade_id, str) or not omrade_id.strip():
        raise FlodenWebbFel("Obligatoriskt fält 'omrade_id' saknas eller är tomt.", status_kod=400)
    omrade_id = omrade_id.strip()

    profil_raw = payload.get("profil", STANDARD_GYF_PROFIL)
    if profil_raw is None or profil_raw == "":
        profil_id = STANDARD_GYF_PROFIL
    elif isinstance(profil_raw, str):
        profil_id = profil_raw.strip().lower()
    else:
        raise FlodenWebbFel(
            "Fältet 'profil' måste vara text ('stockholm', 'goteborg' eller 'malmo').",
            status_kod=400,
        )

    profiler = ladda_gyf_profiler()
    gyf_profil = profiler.get(profil_id)
    if gyf_profil is None:
        giltiga = ", ".join(sorted(profiler))
        raise FlodenWebbFel(
            f"Okänd GYF-profil '{profil_id}'. Giltiga profiler: {giltiga}.",
            status_kod=400,
        )

    scenario_raw = payload.get("scenario")
    scenario: str | None = None
    if scenario_raw is not None and scenario_raw != "":
        if not isinstance(scenario_raw, str) or not scenario_raw.strip():
            raise FlodenWebbFel(
                "Fältet 'scenario' ska vara ett sparat scenarios id (text).", status_kod=400
            )
        scenario = scenario_raw.strip()

    inline_ritlager = payload.get("ritlager")
    if inline_ritlager is not None and not isinstance(inline_ritlager, Mapping):
        raise FlodenWebbFel("Fältet 'ritlager' måste vara ett K50-JSON-objekt.", status_kod=400)
    inline_andringar = payload.get("andringar")
    if inline_andringar is not None and not isinstance(inline_andringar, list):
        raise FlodenWebbFel(
            "Fältet 'andringar' måste vara en lista av K9-ändringar.", status_kod=400
        )

    extra_tillagg = _tolka_gyf_tillagg(payload.get("tillagg"))

    proj_geom = _ladda_projekt_geometri(
        kartverktyg,
        projekt_tjanst,
        omrade_id,
        krav_v2_terrang=False,
        max_celler=max_gyf_celler(),
        max_celler_kontext="GYF-analys",
        max_celler_env=FLODEN_MAX_GYF_CELLER_ENV,
    )
    cell_m = float(proj_geom.geo.pixelstorlek_x)
    cell_area_m2 = cell_m * cell_m

    sc_pack = _ladda_scenario_andringar(
        kartverktyg,
        proj_geom,
        scenario,
        inline_ritlager=inline_ritlager,
        inline_andringar=inline_andringar,
    )

    # Före: markslag + befintlig trädkrontäckning ur vegetation.tif (>= 2 m, ej på tak).
    bef_trad_fore = (
        proj_geom.tomt_mask & (~proj_geom.bygg_mask) & (proj_geom.vegetation_m >= TRAD_HOJD_GRANS_M)
    )
    tillagg_fore: list[GyfTillagg] = []
    if bool(np.any(bef_trad_fore)) and "befintligt_trad" in gyf_profil.tillaggsfaktorer:
        tillagg_fore.append(
            GyfTillagg(
                tillagg_id="befintligt_trad",
                area_m2=float(np.count_nonzero(bef_trad_fore)) * cell_area_m2,
            )
        )
    if sc_pack is None and extra_tillagg:
        tillagg_fore.extend(extra_tillagg)

    try:
        fore_res: GyfResultat = berakna_gyf_fran_raster(
            proj_geom.markslag,
            cell_m,
            profil=gyf_profil,
            tomt_mask=proj_geom.tomt_mask,
            tillagg=tillagg_fore,
        )
    except EkologiFel as exc:
        raise FlodenWebbFel(str(exc), status_kod=400) from exc

    fore_dict = gyf_till_dict(fore_res)
    efter_dict: dict[str, Any] | None = None
    jamforelse_dict: dict[str, Any] | None = None
    aktiv_dict: dict[str, Any] = fore_dict
    varningar: list[str] = list(fore_res.varningar)

    if sc_pack is not None:
        andringar, nyckel = sc_pack
        st_efter = _tillampa_scenario_i_minnet(proj_geom, andringar, nyckel)

        bevarade_trad = (
            proj_geom.tomt_mask
            & (~st_efter.bygg_mask)
            & (proj_geom.vegetation_m >= TRAD_HOJD_GRANS_M)
            & (st_efter.vegetation_m >= TRAD_HOJD_GRANS_M)
            & (~st_efter.nya_trad_mask)
        )
        nya_trad = proj_geom.tomt_mask & (~st_efter.bygg_mask) & st_efter.nya_trad_mask
        nya_buskar = (
            proj_geom.tomt_mask
            & (~st_efter.bygg_mask)
            & st_efter.nya_busk_mask
            & (~st_efter.nya_trad_mask)
        )
        tillagg_efter: list[GyfTillagg] = []
        if bool(np.any(bevarade_trad)) and "befintligt_trad" in gyf_profil.tillaggsfaktorer:
            tillagg_efter.append(
                GyfTillagg(
                    tillagg_id="befintligt_trad",
                    area_m2=float(np.count_nonzero(bevarade_trad)) * cell_area_m2,
                )
            )
        if bool(np.any(nya_trad)) and "trad_krontackning" in gyf_profil.tillaggsfaktorer:
            tillagg_efter.append(
                GyfTillagg(
                    tillagg_id="trad_krontackning",
                    area_m2=float(np.count_nonzero(nya_trad)) * cell_area_m2,
                )
            )
        if bool(np.any(nya_buskar)) and "buskskikt" in gyf_profil.tillaggsfaktorer:
            tillagg_efter.append(
                GyfTillagg(
                    tillagg_id="buskskikt",
                    area_m2=float(np.count_nonzero(nya_buskar)) * cell_area_m2,
                )
            )
        if extra_tillagg:
            tillagg_efter.extend(extra_tillagg)

        try:
            efter_res = berakna_gyf_fran_raster(
                st_efter.markslag,
                cell_m,
                profil=gyf_profil,
                tomt_mask=proj_geom.tomt_mask,
                yt_overlagring=st_efter.yt_overlagring or None,
                tillagg=tillagg_efter,
            )
            jfr = jamfor_gyf(fore_res, efter_res)
        except EkologiFel as exc:
            raise FlodenWebbFel(str(exc), status_kod=400) from exc

        efter_dict = gyf_till_dict(efter_res)
        jfr_ra = gyf_jamforelse_till_dict(jfr)
        jamforelse_dict = {
            "format": jfr_ra["format"],
            "profil_id": jfr_ra["profil_id"],
            "riktmarke": jfr_ra["riktmarke"],
            "delta_gyf": jfr_ra["delta_gyf"],
            "delta_eko_effektiv_area_m2": jfr_ra["delta_eko_effektiv_area_m2"],
            "delta_grund_eko_effektiv_area_m2": jfr_ra["delta_grund_eko_effektiv_area_m2"],
            "delta_tillagg_eko_effektiv_area_m2": jfr_ra["delta_tillagg_eko_effektiv_area_m2"],
            "delta_tomt_area_m2": jfr_ra["delta_tomt_area_m2"],
            "uppfyller_mal": bool(jfr.uppfyller_mal_efter),
            "uppfyller_mal_fore": bool(jfr.uppfyller_mal_fore),
            "uppfyller_mal_efter": bool(jfr.uppfyller_mal_efter),
            "saknas_till_mal_fore_m2": jfr_ra["saknas_till_mal_fore_m2"],
            "saknas_till_mal_m2": jfr_ra["saknas_till_mal_m2"],
            "per_delfaktor": jfr_ra["per_delfaktor"],
            "varningar": jfr_ra["varningar"],
        }
        aktiv_dict = efter_dict
        varningar = list(dict.fromkeys([*varningar, *jfr.varningar]))

    return {
        "format": FORMAT_GYF_WEBB,
        "koordinatsystem": KOORDINATSYSTEM_EPSG,
        "omrade_id": omrade_id,
        "profil": gyf_profil.profil_id,
        "profil_namn": gyf_profil.namn,
        "kommun": gyf_profil.kommun,
        "riktmarke": float(gyf_profil.riktmarke),
        "scenario": scenario,
        "metodstatus": fore_res.metodstatus,
        "fore": fore_dict,
        "efter": efter_dict,
        "jamforelse": jamforelse_dict,
        "delfaktorer": aktiv_dict["delfaktorer"],
        "tillagg": aktiv_dict["tillagg"],
        "flaggad_area_m2": float(aktiv_dict["flaggad_area_m2"]),
        "flaggad_andel_pct": float(aktiv_dict["flaggad_andel_pct"]),
        "varningar": varningar,
        "tillgangliga_profiler": [
            {
                "profil_id": p.profil_id,
                "namn": p.namn,
                "kommun": p.kommun,
                "riktmarke": float(p.riktmarke),
                "status": p.status,
            }
            for p in profiler.values()
        ],
    }


# ------------------------------------------------------------------ Värmestress UTCI / T_mrt (U15)


def _fonster_for_mask(mask: NDArray[np.bool_]) -> tuple[int, int, int, int]:
    rader, kol = np.nonzero(mask)
    if rader.size == 0:
        raise FlodenWebbFel("Projektområdet saknar aktiva celler.", status_kod=400)
    return int(rader.min()), int(rader.max()) + 1, int(kol.min()), int(kol.max()) + 1


def _fyll_icke_andlig_dsm(dsm: NDArray[np.float32]) -> NDArray[np.float64]:
    arr = np.asarray(dsm, dtype=np.float64)
    giltig = np.isfinite(arr)
    if np.all(giltig):
        return arr
    if not np.any(giltig):
        raise FlodenWebbFel("Ytmodellen saknar ändliga höjdvärden.", status_kod=400)
    from scipy import ndimage

    idx: Any = ndimage.distance_transform_edt(~giltig, return_distances=False, return_indices=True)
    return np.asarray(arr[idx[0], idx[1]], dtype=np.float64)


def _berakna_verklig_svf_och_skugga(
    dsm_m: NDArray[np.float32],
    geo: GeoTransformInfo,
    fonster: tuple[int, int, int, int],
    *,
    antal_riktningar: int,
    max_avstand_m: float,
    solhojd_grad: float,
    solazimut_grad: float,
    cache_nyckel_geo: str,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Beräknar verklig SVF och skuggmask ur DSM via dagsljusmotorn (`dagsljus.horisont`)."""
    dsm_ren = _fyll_icke_andlig_dsm(dsm_m)
    try:
        falt = Hojdfalt(
            z_m=dsm_ren,
            cell_m=float(geo.pixelstorlek_x),
            x_min_m=float(geo.x_min),
            y_max_m=float(geo.y_max),
        )
    except HorisontFel as exc:
        raise FlodenWebbFel(
            f"Ogiltigt höjdfält för värmestressanalys: {exc}", status_kod=400
        ) from exc

    r0, r1, c0, c1 = fonster
    svf_key = (cache_nyckel_geo, int(antal_riktningar), float(max_avstand_m))
    svf_cachad = _GLOBAL_SVF_CACHE.hamta(svf_key)
    if svf_cachad is not None:
        svf = svf_cachad.copy()
    else:
        summa = np.zeros((r1 - r0, c1 - c0), dtype=np.float64)
        antal_iter = 0
        try:
            for _, _, h, _ in horisont_ruta_iter(
                falt,
                antal_riktningar,
                max_avstand_m,
                fonster=fonster,
            ):
                h64 = np.asarray(h, dtype=np.float64)
                if h64.size and (h64.min() < -1e-12 or h64.max() > math.pi / 2 + 1e-12):
                    raise HorisontFel("Horisontvinklar måste ligga i [0, pi/2].")
                summa += np.sin(h64) ** 2
                antal_iter += 1
        except HorisontFel as exc:
            raise FlodenWebbFel(f"Horisontberäkning misslyckades: {exc}", status_kod=400) from exc
        if antal_iter == 0:
            raise FlodenWebbFel("Horisontberäkning saknar riktningar.", status_kod=400)
        svf = np.asarray(1.0 - summa / float(antal_iter), dtype=np.float64)
        _GLOBAL_SVF_CACHE.satt(svf_key, svf)

    if solhojd_grad <= 0.0:
        skuggmask = np.zeros((r1 - r0, c1 - c0), dtype=np.float64)
    else:
        sol_az_rad = math.radians(solazimut_grad % 360.0)
        sol_hojd_rad = math.radians(solhojd_grad)
        try:
            h_sol, _ = horisont_riktning_ruta(
                falt,
                sol_az_rad,
                max_avstand_m,
                fonster=fonster,
            )
        except HorisontFel as exc:
            raise FlodenWebbFel(f"Skuggberäkning misslyckades: {exc}", status_kod=400) from exc
        skuggmask = np.asarray(sol_hojd_rad > h_sol.astype(np.float64), dtype=np.float64)

    stor_mosaik = falt.z_m.size >= _WEBB_GC_TROSKEL_CELLER
    del dsm_ren, falt
    if stor_mosaik:
        gc.collect()
    return svf, skuggmask


def _sammanfatta_varmestress(
    res: VarmestressResultat,
    aktiv_mark_mask: NDArray[np.bool_],
) -> dict[str, Any]:
    d = varmestress_till_dict(res)
    stat = res.statistik
    antal_aktiva = int(np.count_nonzero(aktiv_mark_mask))
    utanfor_pct = (
        100.0 * float(stat.antal_utanfor_giltighetsomrade) / float(antal_aktiva)
        if antal_aktiva > 0
        else 0.0
    )
    stressklasser = [
        {
            "klass_id": int(k.klass_id),
            "namn_sv": k.namn_sv,
            "nedre_grans_c": k.nedre_grans_c,
            "ovre_grans_c": k.ovre_grans_c,
            "andel": float(stat.klassfordelning_andel[k.klass_id]),
            "andel_pct": 100.0 * float(stat.klassfordelning_andel[k.klass_id]),
        }
        for k in UTCI_STRESSKLASSER
    ]
    return {
        "utci_medel_c": stat.utci_medel_c,
        "utci_min_c": stat.utci_min_c,
        "utci_max_c": stat.utci_max_c,
        "utci_p90_c": stat.utci_p90_c,
        "tmrt_medel_c": stat.t_mrt_medel_c,
        "tmrt_min_c": stat.t_mrt_min_c,
        "tmrt_max_c": stat.t_mrt_max_c,
        "tmrt_p90_c": stat.t_mrt_p90_c,
        "antal_aktiva_markceller": antal_aktiva,
        "antal_giltiga_celler": int(stat.antal_giltiga_celler),
        "utanfor_giltighetsomrade_pct": float(utanfor_pct),
        "stressklasser": stressklasser,
        "statistik": d["statistik"],
        "kontrollsummor": d["kontrollsummor"],
        "varningar": list(res.varningar),
    }


def _rendera_varmestress_lager(
    *,
    omrade_id: str,
    roll: str,
    typ: str,
    lager_id: str,
    raster_f64: NDArray[np.float64],
    tak_mask_win: NDArray[np.bool_],
    utbredning: Utbredning,
    cell_m: float,
    lo: float,
    hi: float,
    lager_cache: VarmestressLagerCache | None,
) -> dict[str, Any]:
    skillnad = typ == "skillnad"
    farg = farg_for_roll(roll, skillnad=skillnad, lo=lo, hi=hi)
    etikett, enhet = etikett_och_enhet(roll)
    raster_f32 = np.asarray(raster_f64, dtype=np.float32)
    har_tak = bool(np.any(tak_mask_win))
    troskel = 0.05 if skillnad else 0.0
    try:
        bild = rita_bild(
            raster_f32,
            farg,
            lo=lo,
            hi=hi,
            troskel=troskel,
            dold=tak_mask_win if har_tak else None,
        )
    except LagerFel as exc:
        raise FlodenWebbFel(f"Kunde inte rendera {roll}-lager: {exc}", status_kod=500) from exc

    if lager_cache is not None:
        lager_cache.satt(omrade_id, lager_id, bild.png, utbredning)

    kodat_proj = urllib.parse.quote(omrade_id, safe="")
    kodat_id = urllib.parse.quote(lager_id, safe="")
    return {
        "id": lager_id,
        "roll": roll,
        "typ": typ,
        "etikett": etikett,
        "enhet": enhet,
        "farg": farg,
        "skala": {"min": float(-hi if skillnad else lo), "max": float(hi)},
        "teckenforklaring": teckenforklaring(farg, lo, hi, troskel=troskel),
        "utbredning": utbredning.som_dict(),
        "pixelstorlek_m": float(cell_m),
        "bredd_px": int(bild.bredd_px),
        "hojd_px": int(bild.hojd_px),
        "tak_maskat": har_tak,
        "png": f"/api/kartverktyg/{kodat_proj}/varmestress/{kodat_id}.png",
    }


def kor_varmestress_analys(
    kartverktyg: KartverktygTjanst,
    projekt_tjanst: ProjektTjanst,
    payload: Mapping[str, Any],
    *,
    lager_cache: VarmestressLagerCache | None = None,
) -> dict[str, Any]:
    """Deterministisk UTCI/T_mrt-analys ur K1 v2-geometri för `GET/POST /api/analys/varmestress`."""
    omrade_id = payload.get("omrade_id")
    if not isinstance(omrade_id, str) or not omrade_id.strip():
        raise FlodenWebbFel("Obligatoriskt fält 'omrade_id' saknas eller är tomt.", status_kod=400)
    omrade_id = omrade_id.strip()

    forval_ra = payload.get("forval")
    forval_id: str | None = None
    if forval_ra is not None and forval_ra != "":
        if not isinstance(forval_ra, str) or not forval_ra.strip():
            raise FlodenWebbFel("Fältet 'forval' måste vara en textsträng.", status_kod=400)
        forval_id = forval_ra.strip()

    # Läs valfritt SMHI-väderlager (`lager.vader_luft`) om projektet hör till en geografi.
    projekt_mapp = kartverktyg.projektmapp(omrade_id)
    vader_info: dict[str, Any] | None = None
    if projekt_mapp is not None:
        with contextlib.suppress(FlodenWebbFel):
            vader_info = hamta_projekt_vader_luft(projekt_mapp, geo_rot=kartverktyg.geomapp)

    platsens_vader_ut: dict[str, Any] | None = None
    if vader_info is not None and vader_info.get("har_vader_luft"):
        st = vader_info.get("station") or {}
        platsens_vader_ut = {
            "station_id": st.get("id"),
            "station_namn": st.get("namn"),
            "station_avstand_km": st.get("avstand_km"),
            "ar": st.get("ar"),
            "kalla": vader_info.get("kalla"),
            "kalla_text": vader_info.get("kalla_text"),
            "lager_sha256": vader_info.get("lager_sha256"),
            "forval": vader_info.get("utci_forval") or {},
        }

    # Standardparametrar, eventuellt förifyllda från platsens SMHI-värmebölja (p95 / p99).
    std_t_luft = STANDARD_T_LUFT_C
    std_rh = STANDARD_RH_PCT
    std_vind = STANDARD_VIND_10M_M_S
    std_sol_dir = STANDARD_SOL_DIREKT_W_M2
    std_sol_diff = STANDARD_SOL_DIFFUS_W_M2
    std_solhojd = STANDARD_SOLHOJD_GRAD
    std_solaz = STANDARD_SOLAZIMUT_GRAD
    forval_varningar: list[str] = []

    if forval_id in ("platsens_smhi_p95", "platsens_smhi_p99"):
        smhi_forval = (
            platsens_vader_ut["forval"].get(forval_id)
            if platsens_vader_ut is not None
            else None
        )
        if not isinstance(smhi_forval, Mapping):
            raise FlodenWebbFel(
                f"Projektet '{omrade_id}' saknar SMHI-väderlagret (lager.vader_luft) för "
                f"förvalet '{forval_id}'.",
                status_kod=400,
            )
        std_t_luft = float(smhi_forval["t_luft_c"])
        if smhi_forval.get("relativ_fuktighet_pct") is not None:
            std_rh = float(smhi_forval["relativ_fuktighet_pct"])
        elif payload.get("relativ_fuktighet_pct") in (None, ""):
            forval_varningar.append(
                f"SMHI-förvalet {forval_id} saknade samtida relativ luftfuktighet; "
                f"standardvärdet {STANDARD_RH_PCT:g} % användes."
            )
        if smhi_forval.get("vind_10m_m_s") is not None:
            std_vind = float(smhi_forval["vind_10m_m_s"])
        elif payload.get("vind_10m_m_s") in (None, ""):
            forval_varningar.append(
                f"SMHI-förvalet {forval_id} saknade samtida 10 m-vind; "
                f"standardvärdet {STANDARD_VIND_10M_M_S:g} m/s användes."
            )
        if smhi_forval.get("sol_direkt_w_m2") is not None:
            std_sol_dir = float(smhi_forval["sol_direkt_w_m2"])
        if smhi_forval.get("sol_diffus_w_m2") is not None:
            std_sol_diff = float(smhi_forval["sol_diffus_w_m2"])
        if smhi_forval.get("solhojd_grad") is not None:
            std_solhojd = float(smhi_forval["solhojd_grad"])
        if smhi_forval.get("solazimut_grad") is not None:
            std_solaz = float(smhi_forval["solazimut_grad"])
    elif forval_id is not None and forval_id not in ("varmebolja", "sommardag", "anpassa"):
        raise FlodenWebbFel(
            f"Okänt UTCI-förval '{forval_id}'. Giltiga förval: varmebolja, sommardag, "
            "platsens_smhi_p95, platsens_smhi_p99, anpassa.",
            status_kod=400,
        )

    t_luft_c = _tolka_andligt_tal(
        payload, "t_luft_c", std_t_luft, min_v=-273.15, strikt_min=True
    )
    relativ_fuktighet_pct = _tolka_andligt_tal(
        payload, "relativ_fuktighet_pct", std_rh, min_v=0.0, max_v=100.0
    )
    vind_10m_m_s = _tolka_andligt_tal(payload, "vind_10m_m_s", std_vind, min_v=0.0)
    sol_direkt_w_m2 = _tolka_andligt_tal(
        payload, "sol_direkt_w_m2", std_sol_dir, min_v=0.0
    )
    sol_diffus_w_m2 = _tolka_andligt_tal(
        payload, "sol_diffus_w_m2", std_sol_diff, min_v=0.0
    )
    solhojd_grad = _tolka_andligt_tal(
        payload, "solhojd_grad", std_solhojd, min_v=-90.0, max_v=90.0
    )
    solazimut_grad = _tolka_andligt_tal(
        payload, "solazimut_grad", std_solaz, min_v=0.0, max_v=360.0
    )
    antal_riktningar = _tolka_heltal(
        payload, "antal_riktningar", STANDARD_ANTAL_RIKTNINGAR, min_v=8, max_v=180
    )

    scenario_raw = payload.get("scenario")
    scenario: str | None = None
    if scenario_raw is not None and scenario_raw != "":
        if not isinstance(scenario_raw, str) or not scenario_raw.strip():
            raise FlodenWebbFel(
                "Fältet 'scenario' ska vara ett sparat scenarios id (text).", status_kod=400
            )
        scenario = scenario_raw.strip()

    inline_ritlager = payload.get("ritlager")
    if inline_ritlager is not None and not isinstance(inline_ritlager, Mapping):
        raise FlodenWebbFel("Fältet 'ritlager' måste vara ett K50-JSON-objekt.", status_kod=400)
    inline_andringar = payload.get("andringar")
    if inline_andringar is not None and not isinstance(inline_andringar, list):
        raise FlodenWebbFel(
            "Fältet 'andringar' måste vara en lista av K9-ändringar.", status_kod=400
        )

    proj_geom = _ladda_projekt_geometri(
        kartverktyg,
        projekt_tjanst,
        omrade_id,
        krav_v2_terrang=True,
        max_celler=max_varmestress_projekt_celler(),
        max_celler_kontext="värmestressanalys",
        max_celler_env=FLODEN_MAX_VARMESTRESS_CELLER_ENV,
    )
    geo = proj_geom.geo
    r0, r1, c0, c1 = _fonster_for_mask(proj_geom.tomt_mask)
    varningar: list[str] = []

    # Nedsampla bara om projektfönstret överstiger cellgränsen (flaggas alltid i varningar).
    win_h, win_b = r1 - r0, c1 - c0
    steg = 1
    if win_h * win_b > MAX_VARMESTRESS_CELLER:
        steg = max(2, math.ceil(math.sqrt((win_h * win_b) / MAX_VARMESTRESS_CELLER)))
        varningar.append(
            f"Projektfönstret ({win_h}×{win_b} celler) nedsamplades med faktor {steg} "
            f"(effektiv cellstorlek {geo.pixelstorlek_x * steg:g} m) för värmestressberäkningen."
        )

    def _forbered_dsm(
        terrang_m: NDArray[np.float32],
        byggnader: tuple[ByggnadsPost, ...],
        bygg_mask: NDArray[np.bool_],
        veg_m: NDArray[np.float32],
    ) -> tuple[
        NDArray[np.float32],
        GeoTransformInfo,
        tuple[int, int, int, int],
        NDArray[np.bool_],
        NDArray[np.bool_],
        NDArray[np.bool_],
    ]:
        terr_saker = np.where(np.isfinite(terrang_m), terrang_m, np.float32(0.0))
        dsm_full, _ = _brann_byggnader_mask(terr_saker, geo, byggnader)
        if steg == 1:
            tomt_w = proj_geom.tomt_mask[r0:r1, c0:c1]
            bygg_w = bygg_mask[r0:r1, c0:c1]
            veg_w = veg_m[r0:r1, c0:c1]
            return dsm_full, geo, (r0, r1, c0, c1), tomt_w, bygg_w, (veg_w >= TRAD_HOJD_GRANS_M)

        dsm_s = dsm_full[::steg, ::steg]
        geo_s = GeoTransformInfo(
            bredd=int(dsm_s.shape[1]),
            hojd=int(dsm_s.shape[0]),
            x_min=float(geo.x_min),
            y_max=float(geo.y_max),
            pixelstorlek_x=float(geo.pixelstorlek_x * steg),
            pixelstorlek_y=float(geo.pixelstorlek_y * steg),
            epsg=geo.epsg,
            dtype=geo.dtype,
        )
        sr0, sr1 = r0 // steg, min(int(dsm_s.shape[0]), math.ceil(r1 / steg))
        sc0, sc1 = c0 // steg, min(int(dsm_s.shape[1]), math.ceil(c1 / steg))
        tomt_w = proj_geom.tomt_mask[::steg, ::steg][sr0:sr1, sc0:sc1]
        bygg_w = bygg_mask[::steg, ::steg][sr0:sr1, sc0:sc1]
        veg_w = veg_m[::steg, ::steg][sr0:sr1, sc0:sc1]
        return dsm_s, geo_s, (sr0, sr1, sc0, sc1), tomt_w, bygg_w, (veg_w >= TRAD_HOJD_GRANS_M)

    dsm_fore, geo_eff, fonster_eff, tomt_win, bygg_fore_win, trad_raw_fore = _forbered_dsm(
        proj_geom.terrang_m,
        proj_geom.byggnader,
        proj_geom.bygg_mask,
        proj_geom.vegetation_m,
    )
    er0, er1, ec0, ec1 = fonster_eff
    cell_eff_m = float(geo_eff.pixelstorlek_x)
    max_avst_m = min(
        STANDARD_MAX_AVSTAND_M,
        float(math.hypot(geo_eff.bredd * cell_eff_m, geo_eff.hojd * cell_eff_m)),
    )
    max_avst_m = max(max_avst_m, cell_eff_m * 4.0)

    svf_fore_raw, skugga_fore_raw = _berakna_verklig_svf_och_skugga(
        dsm_fore,
        geo_eff,
        fonster_eff,
        antal_riktningar=antal_riktningar,
        max_avstand_m=max_avst_m,
        solhojd_grad=solhojd_grad,
        solazimut_grad=solazimut_grad,
        cache_nyckel_geo=f"{proj_geom.stampel}:fore:s{steg}:{fonster_eff}",
    )

    aktiv_fore = tomt_win & (~bygg_fore_win)
    if not np.any(aktiv_fore):
        raise FlodenWebbFel(
            "Projektområdet saknar fria markceller utanför byggnader för värmestressanalys.",
            status_kod=400,
        )

    svf_fore = np.where(aktiv_fore, svf_fore_raw, np.nan)
    skugga_fore = np.where(aktiv_fore, skugga_fore_raw, np.nan)
    trad_fore = trad_raw_fore & aktiv_fore

    solhojd_rad = math.radians(solhojd_grad)
    try:
        res_fore = berakna_varmestress(
            svf=svf_fore,
            skuggmask=skugga_fore,
            solhojd_rad=solhojd_rad,
            sol_direkt_w_m2=sol_direkt_w_m2,
            sol_diffus_w_m2=sol_diffus_w_m2,
            t_luft_c=t_luft_c,
            relativ_fuktighet_pct=relativ_fuktighet_pct,
            vind_10m_m_s=vind_10m_m_s,
            trad_mask=trad_fore,
        )
    except ValueError as exc:
        raise FlodenWebbFel(f"Ogiltiga värmestressparametrar: {exc}", status_kod=400) from exc

    fore_block = _sammanfatta_varmestress(res_fore, aktiv_fore)
    varningar.extend(res_fore.varningar)

    svf_medel_fore = float(np.nanmean(svf_fore))
    sol_andel_fore_pct = 100.0 * float(np.nanmean(skugga_fore))
    trad_andel_fore_pct = (
        100.0 * float(np.count_nonzero(trad_fore)) / float(np.count_nonzero(aktiv_fore))
    )

    sc_pack = _ladda_scenario_andringar(
        kartverktyg,
        proj_geom,
        scenario,
        inline_ritlager=inline_ritlager,
        inline_andringar=inline_andringar,
    )

    res_efter: VarmestressResultat | None = None
    efter_block: dict[str, Any] | None = None
    jfr_block: dict[str, Any] | None = None
    bygg_efter_win: NDArray[np.bool_] | None = None
    delta_utci: NDArray[np.float64] | None = None
    delta_tmrt: NDArray[np.float64] | None = None
    geo_meta_efter: dict[str, Any] = {}
    sc_nyckel = "nulage"

    if sc_pack is not None:
        andringar, sc_nyckel = sc_pack
        st_efter = _tillampa_scenario_i_minnet(proj_geom, andringar, sc_nyckel)
        dsm_efter, _, _, _, bygg_efter_win, trad_raw_efter = _forbered_dsm(
            st_efter.terrang_m,
            st_efter.byggnader,
            st_efter.bygg_mask,
            st_efter.vegetation_m,
        )
        svf_efter_raw, skugga_efter_raw = _berakna_verklig_svf_och_skugga(
            dsm_efter,
            geo_eff,
            fonster_eff,
            antal_riktningar=antal_riktningar,
            max_avstand_m=max_avst_m,
            solhojd_grad=solhojd_grad,
            solazimut_grad=solazimut_grad,
            cache_nyckel_geo=f"{proj_geom.stampel}:sc-{sc_nyckel}:s{steg}:{fonster_eff}",
        )
        aktiv_efter = tomt_win & (~bygg_efter_win)
        if not np.any(aktiv_efter):
            raise FlodenWebbFel(
                "Scenariot lämnar inga fria markceller utanför byggnader för värmestressanalys.",
                status_kod=400,
            )
        svf_efter = np.where(aktiv_efter, svf_efter_raw, np.nan)
        skugga_efter = np.where(aktiv_efter, skugga_efter_raw, np.nan)
        trad_efter = trad_raw_efter & aktiv_efter

        try:
            res_efter = berakna_varmestress(
                svf=svf_efter,
                skuggmask=skugga_efter,
                solhojd_rad=solhojd_rad,
                sol_direkt_w_m2=sol_direkt_w_m2,
                sol_diffus_w_m2=sol_diffus_w_m2,
                t_luft_c=t_luft_c,
                relativ_fuktighet_pct=relativ_fuktighet_pct,
                vind_10m_m_s=vind_10m_m_s,
                trad_mask=trad_efter,
            )
            jfr = jamfor_varmestress(res_fore, res_efter)
        except ValueError as exc:
            raise FlodenWebbFel(
                f"Kunde inte jämföra värmestress före/efter: {exc}", status_kod=400
            ) from exc

        efter_block = _sammanfatta_varmestress(res_efter, aktiv_efter)
        jfr_dict = varmestress_jamforelse_till_dict(jfr)
        jfr_block = {
            "delta_utci_medel_c": jfr.delta_utci_medel_c,
            "delta_utci_min_c": jfr.delta_utci_min_c,
            "delta_utci_max_c": jfr.delta_utci_max_c,
            "delta_tmrt_medel_c": jfr.delta_t_mrt_medel_c,
            "delta_t_mrt_medel_c": jfr.delta_t_mrt_medel_c,
            "delta_tmrt_min_c": jfr.delta_t_mrt_min_c,
            "delta_tmrt_max_c": jfr.delta_t_mrt_max_c,
            "delta_andel_stark_varmestress_pct": 100.0 * float(jfr.delta_andel_stark_varmestress),
            "delta_andel_mycket_stark_varmestress_pct": 100.0
            * float(jfr.delta_andel_mycket_stark_varmestress),
            "delta_andel_extrem_varmestress_pct": 100.0 * float(jfr.delta_andel_extrem_varmestress),
            "antal_jamforda_utci_celler": int(jfr.antal_jamforda_utci_celler),
            "antal_jamforda_tmrt_celler": int(jfr.antal_jamforda_tmrt_celler),
            "kontrollsummor": jfr_dict["kontrollsummor"],
            "varningar": list(jfr.varningar),
        }
        delta_utci = jfr.delta_utci_c
        delta_tmrt = jfr.delta_t_mrt_c
        varningar = list(dict.fromkeys([*varningar, *jfr.varningar]))
        geo_meta_efter = {
            "svf_medel_efter": float(np.nanmean(svf_efter)),
            "solbelyst_andel_efter_pct": 100.0 * float(np.nanmean(skugga_efter)),
            "trad_andel_efter_pct": (
                100.0 * float(np.count_nonzero(trad_efter)) / float(np.count_nonzero(aktiv_efter))
            ),
        }

    # Utbredning för fönstret i EPSG:3011
    utbredning = Utbredning(
        x_min=float(geo_eff.x_min + ec0 * cell_eff_m),
        y_min=float(geo_eff.y_max - er1 * cell_eff_m),
        x_max=float(geo_eff.x_min + ec1 * cell_eff_m),
        y_max=float(geo_eff.y_max - er0 * cell_eff_m),
    )

    param_signatur = {
        "omrade_id": omrade_id,
        "stampel": proj_geom.stampel,
        "sc": sc_nyckel,
        "t": t_luft_c,
        "rh": relativ_fuktighet_pct,
        "v": vind_10m_m_s,
        "dir": sol_direkt_w_m2,
        "diff": sol_diffus_w_m2,
        "sh": solhojd_grad,
        "sa": solazimut_grad,
        "k": antal_riktningar,
    }
    bas_tag = hashlib.sha256(
        json.dumps(param_signatur, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]

    # Gemensamma skalor för före och efter så lagren går att jämföra direkt
    utci_rastren = [np.asarray(res_fore.utci.utci_c, dtype=np.float32)]
    tmrt_rastren = [np.asarray(res_fore.tmrt.t_mrt_c, dtype=np.float32)]
    if res_efter is not None:
        utci_rastren.append(np.asarray(res_efter.utci.utci_c, dtype=np.float32))
        tmrt_rastren.append(np.asarray(res_efter.tmrt.t_mrt_c, dtype=np.float32))
    utci_lo, utci_hi = varde_intervall(utci_rastren)
    tmrt_lo, tmrt_hi = varde_intervall(tmrt_rastren)

    lager: dict[str, dict[str, Any] | None] = {
        "utci_c_fore": _rendera_varmestress_lager(
            omrade_id=omrade_id,
            roll="utci_c",
            typ="bas",
            lager_id=f"vs_{bas_tag}_utci_c_fore",
            raster_f64=res_fore.utci.utci_c,
            tak_mask_win=bygg_fore_win,
            utbredning=utbredning,
            cell_m=cell_eff_m,
            lo=utci_lo,
            hi=utci_hi,
            lager_cache=lager_cache,
        ),
        "t_mrt_c_fore": _rendera_varmestress_lager(
            omrade_id=omrade_id,
            roll="t_mrt_c",
            typ="bas",
            lager_id=f"vs_{bas_tag}_t_mrt_c_fore",
            raster_f64=res_fore.tmrt.t_mrt_c,
            tak_mask_win=bygg_fore_win,
            utbredning=utbredning,
            cell_m=cell_eff_m,
            lo=tmrt_lo,
            hi=tmrt_hi,
            lager_cache=lager_cache,
        ),
        "utci_c_efter": None,
        "t_mrt_c_efter": None,
        "utci_c_skillnad": None,
        "t_mrt_c_skillnad": None,
    }

    if (
        res_efter is not None
        and bygg_efter_win is not None
        and delta_utci is not None
        and delta_tmrt is not None
    ):
        tak_skillnad = bygg_fore_win | bygg_efter_win
        d_utci_lo, d_utci_hi = varde_intervall([np.asarray(delta_utci, dtype=np.float32)])
        d_tmrt_lo, d_tmrt_hi = varde_intervall([np.asarray(delta_tmrt, dtype=np.float32)])
        lager["utci_c_efter"] = _rendera_varmestress_lager(
            omrade_id=omrade_id,
            roll="utci_c",
            typ="scenario",
            lager_id=f"vs_{bas_tag}_utci_c_efter",
            raster_f64=res_efter.utci.utci_c,
            tak_mask_win=bygg_efter_win,
            utbredning=utbredning,
            cell_m=cell_eff_m,
            lo=utci_lo,
            hi=utci_hi,
            lager_cache=lager_cache,
        )
        lager["t_mrt_c_efter"] = _rendera_varmestress_lager(
            omrade_id=omrade_id,
            roll="t_mrt_c",
            typ="scenario",
            lager_id=f"vs_{bas_tag}_t_mrt_c_efter",
            raster_f64=res_efter.tmrt.t_mrt_c,
            tak_mask_win=bygg_efter_win,
            utbredning=utbredning,
            cell_m=cell_eff_m,
            lo=tmrt_lo,
            hi=tmrt_hi,
            lager_cache=lager_cache,
        )
        lager["utci_c_skillnad"] = _rendera_varmestress_lager(
            omrade_id=omrade_id,
            roll="utci_c",
            typ="skillnad",
            lager_id=f"vs_{bas_tag}_utci_c_skillnad",
            raster_f64=delta_utci,
            tak_mask_win=tak_skillnad,
            utbredning=utbredning,
            cell_m=cell_eff_m,
            lo=0.0,
            hi=max(abs(d_utci_lo), abs(d_utci_hi)),
            lager_cache=lager_cache,
        )
        lager["t_mrt_c_skillnad"] = _rendera_varmestress_lager(
            omrade_id=omrade_id,
            roll="t_mrt_c",
            typ="skillnad",
            lager_id=f"vs_{bas_tag}_t_mrt_c_skillnad",
            raster_f64=delta_tmrt,
            tak_mask_win=tak_skillnad,
            utbredning=utbredning,
            cell_m=cell_eff_m,
            lo=0.0,
            hi=max(abs(d_tmrt_lo), abs(d_tmrt_hi)),
            lager_cache=lager_cache,
        )

    aktiv = efter_block if efter_block is not None else fore_block
    varningar = [*forval_varningar, *varningar]
    return {
        "format": FORMAT_VARMESTRESS_WEBB,
        "koordinatsystem": KOORDINATSYSTEM_EPSG,
        "omrade_id": omrade_id,
        "scenario": scenario,
        "forval": forval_id,
        "metodstatus": res_fore.metodstatus,
        "parametrar": {
            "t_luft_c": float(t_luft_c),
            "relativ_fuktighet_pct": float(relativ_fuktighet_pct),
            "vind_10m_m_s": float(vind_10m_m_s),
            "sol_direkt_w_m2": float(sol_direkt_w_m2),
            "sol_diffus_w_m2": float(sol_diffus_w_m2),
            "solhojd_grad": float(solhojd_grad),
            "solazimut_grad": float(solazimut_grad),
            "antal_riktningar": int(antal_riktningar),
        },
        "platsens_vader": platsens_vader_ut,
        "geometri": {
            "cellstorlek_m": float(cell_eff_m),
            "utbredning": utbredning.som_dict(),
            "svf_medel_fore": svf_medel_fore,
            "solbelyst_andel_fore_pct": sol_andel_fore_pct,
            "trad_andel_fore_pct": trad_andel_fore_pct,
            **geo_meta_efter,
        },
        "utci_medel_c": aktiv["utci_medel_c"],
        "utci_min_c": aktiv["utci_min_c"],
        "utci_max_c": aktiv["utci_max_c"],
        "utci_p90_c": aktiv["utci_p90_c"],
        "tmrt_medel_c": aktiv["tmrt_medel_c"],
        "tmrt_min_c": aktiv["tmrt_min_c"],
        "tmrt_max_c": aktiv["tmrt_max_c"],
        "tmrt_p90_c": aktiv["tmrt_p90_c"],
        "utanfor_giltighetsomrade_pct": aktiv["utanfor_giltighetsomrade_pct"],
        "stressklasser": aktiv["stressklasser"],
        "fore": fore_block,
        "efter": efter_block,
        "jamforelse": jfr_block,
        "lager": lager,
        "varningar": list(dict.fromkeys(varningar)),
    }


# ------------------------------------------------------------------ Väderlager & Luft-skisskedja


def max_luft_skiss_segment() -> int:
    """Fail-fast tak för antal vägsegment i Luft-skissanalys (`FLODEN_MAX_LUFT_SKISS_SEGMENT`)."""
    return _las_miljo_celltak(FLODEN_MAX_LUFT_SKISS_SEGMENT_ENV, STANDARD_MAX_LUFT_SKISS_SEGMENT)


def max_luft_skiss_timmar() -> int:
    """Fail-fast tak för antal timmar i NORTRIP-skisskörning (`FLODEN_MAX_LUFT_SKISS_TIMMAR`)."""
    return _las_miljo_celltak(FLODEN_MAX_LUFT_SKISS_TIMMAR_ENV, STANDARD_MAX_LUFT_SKISS_TIMMAR)


def _hitta_geografi_manifest(
    projekt_mapp: Path | str | None,
    *,
    geo_rot: Path | str | None = None,
    manifest_fil: Path | str | None = None,
) -> Path | None:
    """Löser upp geografins K46-`manifest.json` för en projektmapp (eller `None` om saknas)."""
    from geografi.modell import MANIFEST_NAMN, STANDARD_GEOROT, GeografiFel, tolka_ref, versionsmapp

    if manifest_fil is not None:
        mf = Path(manifest_fil)
        return mf if mf.is_file() else None
    if projekt_mapp is None:
        return None
    pm = Path(projekt_mapp)
    pfil = pm / "projekt.json"
    if not pfil.is_file():
        return None
    try:
        pdok = json.loads(pfil.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(pdok, Mapping):
        return None

    expl_mf = pdok.get("geografi_manifest")
    if isinstance(expl_mf, str) and expl_mf.strip():
        cand_mf = Path(expl_mf)
        if cand_mf.is_file():
            return cand_mf

    ref = pdok.get("geografi")
    if not isinstance(ref, str) or "@" not in ref:
        return None
    try:
        gid, ver = tolka_ref(ref)
    except GeografiFel:
        return None

    kandidater: list[Path] = []
    if geo_rot is not None:
        kandidater.append(Path(geo_rot))
    kandidater.append(pm.parent.parent / "geo")
    kandidater.append(Path(STANDARD_GEOROT))

    for rot in kandidater:
        cand = versionsmapp(rot, gid, ver) / MANIFEST_NAMN
        if cand.is_file():
            return cand
    return None


def _ladda_projekt_vader_lager(
    projekt_mapp: Path | str | None,
    *,
    geo_rot: Path | str | None = None,
    manifest_fil: Path | str | None = None,
) -> Any | None:
    from geografi.modell import GeografiFel
    from geografi.vader_lager import las_vader_lager

    mf = _hitta_geografi_manifest(projekt_mapp, geo_rot=geo_rot, manifest_fil=manifest_fil)
    if mf is None:
        return None
    try:
        return las_vader_lager(mf)
    except GeografiFel as exc:
        raise FlodenWebbFel(f"Geografins väderlager är ogiltigt: {exc}", status_kod=409) from exc


def hamta_projekt_vader_luft(
    projekt_mapp: Path | str | None,
    *,
    geo_rot: Path | str | None = None,
    manifest_fil: Path | str | None = None,
) -> dict[str, Any]:
    """Läser SHA-256-verifierat `lager.vader_luft` för ett projekt (vindros, UTCI-förval, bakgrund).

    Returnerar alltid ett deterministiskt JSON-objekt utan påhittade värden. Saknar projektet
    eller dess geografi `lager.vader_luft` returneras `har_vader_luft = False`.
    """
    vl = _ladda_projekt_vader_lager(projekt_mapp, geo_rot=geo_rot, manifest_fil=manifest_fil)
    if vl is None:
        return {
            "format": FORMAT_VADER_LUFT_WEBB,
            "har_vader_luft": False,
            "geografi": None,
            "manifest_sha256": None,
            "lager_sha256": None,
            "station": None,
            "kalla_text": None,
            "kalla": None,
            "licens": None,
            "vindros": None,
            "utci_forval": None,
            "bakgrund_luft": None,
            "har_vader_timserie": False,
            "varningar": [
                "Projektets geografi saknar SMHI-väderlagret (lager.vader_luft). "
                "Vindros, platsens värmebölja och bakgrundsluft saknas."
            ],
        }

    return {
        "format": FORMAT_VADER_LUFT_WEBB,
        "har_vader_luft": True,
        "geografi": vl.ref,
        "manifest_sha256": vl.manifest_sha256,
        "lager_sha256": vl.lager_sha256,
        "station": {
            "id": vl.station_id,
            "namn": vl.station_namn,
            "avstand_km": vl.station_avstand_km,
            "ar": vl.ar,
        },
        "kalla_text": vl.kalla_text_kort(),
        "kalla": vl.kalla,
        "licens": vl.licens,
        "vindros": vl.vindros,
        "utci_forval": vl.utci_forval(),
        "bakgrund_luft": vl.bakgrund_luft,
        "har_vader_timserie": vl.har_vader_timserie,
        "varningar": list(vl.varningar),
    }


def _langd_fran_segment_dict(seg: Mapping[str, Any], ctx: str) -> float:
    raw_l = seg.get("langd_m")
    if raw_l is not None and raw_l != "":
        return _tolka_andligt_tal(seg, "langd_m", 0.0, min_v=0.0, strikt_min=True)
    koord = seg.get("koordinater")
    if isinstance(koord, Sequence) and not isinstance(koord, str | bytes) and len(koord) >= 2:
        tot = 0.0
        prev: tuple[float, float] | None = None
        for pt in koord:
            if not isinstance(pt, Sequence) or len(pt) < 2:
                raise FlodenWebbFel(f"{ctx}: ogiltig koordinat i 'koordinater'.", status_kod=400)
            e_v, n_v = float(pt[0]), float(pt[1])
            if not (math.isfinite(e_v) and math.isfinite(n_v)):
                raise FlodenWebbFel(f"{ctx}: koordinat måste vara ändlig.", status_kod=400)
            if prev is not None:
                tot += math.hypot(e_v - prev[0], n_v - prev[1])
            prev = (e_v, n_v)
        if tot > 0.0:
            return tot
    raise FlodenWebbFel(
        f"{ctx}: ange antingen 'langd_m' (> 0) eller 'koordinater' i EPSG:3011.",
        status_kod=400,
    )


def _extrahera_vindros_param(vindros: Mapping[str, Any]) -> dict[str, Any]:
    sektorer = vindros.get("sektorer")
    if not isinstance(sektorer, Sequence) or len(sektorer) != 16:
        raise FlodenWebbFel("Ogiltig vindros: exakt 16 sektorer krävs.", status_kod=400)
    medel_ra = vindros.get("medelvind_m_s")
    if (
        isinstance(medel_ra, bool)
        or not isinstance(medel_ra, int | float)
        or not math.isfinite(float(medel_ra))
    ):
        raise FlodenWebbFel("Vindros saknar ändligt fält 'medelvind_m_s'.", status_kod=400)
    medelvind = float(medel_ra)
    if medelvind <= 0.0:
        raise FlodenWebbFel("Vindrosens 'medelvind_m_s' måste vara > 0.", status_kod=400)

    p95_ra = vindros.get("p95_vind_m_s")
    p95 = (
        float(p95_ra)
        if isinstance(p95_ra, int | float) and math.isfinite(float(p95_ra))
        else None
    )
    svag_ra = vindros.get("andel_svag_vind_pct", 0.0)
    svag_pct = (
        float(svag_ra)
        if isinstance(svag_ra, int | float) and math.isfinite(float(svag_ra))
        else 0.0
    )

    # Harmoniskt viktad effektiv vindhastighet över 16 sektorer (ger fysisk vikt åt låg vind).
    sum_w = 0.0
    sum_w_inv_u = 0.0
    dom_namn = None
    dom_pct = -1.0
    for s in sektorer:
        if not isinstance(s, Mapping):
            continue
        andel = float(s.get("andel_pct", 0.0) or 0.0)
        if andel > dom_pct:
            dom_pct = andel
            dom_namn = str(s.get("namn", ""))
        u_s = s.get("medelvind_m_s")
        if (
            andel > 0.0
            and isinstance(u_s, int | float)
            and math.isfinite(float(u_s))
            and float(u_s) > 0.0
        ):
            sum_w += andel
            sum_w_inv_u += andel / max(float(u_s), 1.0)
    if svag_pct > 0.0:
        sum_w += svag_pct
        sum_w_inv_u += svag_pct / 1.0

    eff_vind = (sum_w / sum_w_inv_u) if sum_w > 0.0 and sum_w_inv_u > 0.0 else max(medelvind, 1.0)
    # Lågvindsfaktor för höga dygn (90,4-percentil dygn) ur svagvindandel och effektiv vind.
    lag_bas = 0.55 * medelvind + 0.45 * (1.0 - min(svag_pct, 50.0) / 100.0)
    lag_vind = max(1.0, min(eff_vind, lag_bas))
    return {
        "station_id": vindros.get("station_id"),
        "station_namn": vindros.get("station_namn"),
        "ar": vindros.get("ar"),
        "medelvind_m_s": round(medelvind, 3),
        "p95_vind_m_s": round(p95, 3) if p95 is not None else None,
        "andel_svag_vind_pct": round(svag_pct, 2),
        "dominerande_sektor": dom_namn,
        "dominerande_sektor_pct": round(dom_pct, 2) if dom_pct >= 0.0 else None,
        "effektiv_vind_m_s": round(eff_vind, 3),
        "dygn_lagvind_m_s": round(lag_vind, 3),
    }


def _las_bakgrund_varde(
    expl: Mapping[str, Any] | None,
    lager_bakg: Mapping[str, Any] | None,
    amne: str,
    expl_nyckel: str,
    stat_nyckel: str = "arsmedel_ug_m3",
) -> tuple[float | None, str | None]:
    if expl is not None and expl.get(expl_nyckel) not in (None, ""):
        val = _tolka_andligt_tal(expl, expl_nyckel, 0.0, min_v=0.0)
        return val, "explicit"
    if isinstance(lager_bakg, Mapping) and lager_bakg.get("tillganglig"):
        amnen = lager_bakg.get("amnen")
        if isinstance(amnen, Mapping):
            post = amnen.get(amne)
            if isinstance(post, Mapping):
                ra = post.get(stat_nyckel)
                if (
                    isinstance(ra, int | float)
                    and not isinstance(ra, bool)
                    and math.isfinite(float(ra))
                    and float(ra) >= 0.0
                ):
                    kalla_lbl = str(
                        lager_bakg.get("station_namn")
                        or lager_bakg.get("kalla")
                        or "lager.vader_luft"
                    )
                    return float(ra), kalla_lbl
    return None, None


def kor_luft_skiss_analys(
    projekt_mapp: Path | str | None,
    kropp: Mapping[str, Any],
    *,
    geo_rot: Path | str | None = None,
    manifest_fil: Path | str | None = None,
) -> dict[str, Any]:
    """Deterministisk Luft-skissanalys som kopplar väglänkar, SMHI-vindros, bakgrund och emission.

    Följer val 3A och `AGENTS.md`:
    - `0` påhittade värden: saknad ÅDT/hastighet/tung %, saknad vindros eller saknad emissionskälla
      (utan uttryckligt `anvand_skiss_schablon=True`) ger alltid tydligt 400-fel.
    - Kör riktig NORTRIP (`luft.emission.berakna_emission`) när timvis väderserie finns och
      fotostationär `NO₂`-kemi (`luft.kemi.no2_fotostationar`) när urban `NO₂`+`O₃`-bakgrund finns.
    - Respekterar CX23-tak (`max_luft_skiss_segment`, `max_luft_skiss_timmar`).
    - Märker alltid resultatet `metodstatus = "skiss"`.
    """
    from data_inlasning.smhi_luft_strang import SmhiLuftStrangFel, las_vaderserie_csv
    from luft.emission import Trafikprofil, Vagavsnitt, berakna_emission, testparametrar
    from luft.kemi import no2_fotostationar
    from luft.skiss_emissionsfaktorer import hamta_emissionsfaktorer
    from luft.typer import LuftFel

    if not isinstance(kropp, Mapping):
        raise FlodenWebbFel("Förfrågningskroppen måste vara ett JSON-objekt.", status_kod=400)

    # 1. Validera vägsegment (aldrig påhittad trafikdata, strikt CX23-tak).
    ra_seg = kropp.get("vagsegment")
    if not isinstance(ra_seg, list) or len(ra_seg) == 0:
        raise FlodenWebbFel(
            "Obligatoriskt fält 'vagsegment' saknas eller är tomt — Floden hittar aldrig "
            "på trafikdata.",
            status_kod=400,
        )
    max_seg = max_luft_skiss_segment()
    if len(ra_seg) > max_seg:
        raise FlodenWebbFel(
            f"Antal vägsegment ({len(ra_seg)}) överstiger serverns gräns på {max_seg} "
            f"({FLODEN_MAX_LUFT_SKISS_SEGMENT_ENV}).",
            status_kod=400,
        )

    segment_lista: list[dict[str, Any]] = []
    sedda_id: set[str] = set()
    for idx, post in enumerate(ra_seg):
        ctx = f"vagsegment[{idx}]"
        if not isinstance(post, Mapping):
            raise FlodenWebbFel(f"{ctx} måste vara ett JSON-objekt.", status_kod=400)
        seg_id = str(post.get("id") or post.get("lank_id") or f"vag_{idx + 1}").strip()
        if not seg_id or seg_id in sedda_id:
            raise FlodenWebbFel(f"{ctx}: unikt icke-tomt segment-id krävs.", status_kod=400)
        sedda_id.add(seg_id)

        if post.get("adt_fordon_dygn") is None or post.get("adt_fordon_dygn") == "":
            raise FlodenWebbFel(
                f"{ctx} ('{seg_id}'): ÅDT ('adt_fordon_dygn') saknas — Floden hittar aldrig "
                "på ÅDT.",
                status_kod=400,
            )
        if post.get("hastighet_km_h") is None or post.get("hastighet_km_h") == "":
            raise FlodenWebbFel(
                f"{ctx} ('{seg_id}'): skyltad hastighet ('hastighet_km_h') saknas.",
                status_kod=400,
            )
        adt = _tolka_andligt_tal(post, "adt_fordon_dygn", 0.0, min_v=0.0, max_v=500_000.0)
        hast = _tolka_andligt_tal(post, "hastighet_km_h", 0.0, min_v=5.0, max_v=130.0)
        if post.get("andel_tung_pct") not in (None, ""):
            tung_pct = _tolka_andligt_tal(post, "andel_tung_pct", 0.0, min_v=0.0, max_v=100.0)
        elif post.get("andel_tung") not in (None, ""):
            tung_pct = 100.0 * _tolka_andligt_tal(post, "andel_tung", 0.0, min_v=0.0, max_v=1.0)
        else:
            raise FlodenWebbFel(
                f"{ctx} ('{seg_id}'): tung andel ('andel_tung_pct') saknas.",
                status_kod=400,
            )
        langd_m = _langd_fran_segment_dict(post, ctx)
        korfalt = _tolka_heltal(post, "antal_korfalt", 2, min_v=1, max_v=12)
        vagbredd_m = _tolka_andligt_tal(
            post, "vagbredd_m", float(korfalt) * 3.5, min_v=2.0, max_v=60.0
        )
        avstand_m = _tolka_andligt_tal(post, "avstand_m", 10.0, min_v=2.0, max_v=500.0)
        gaturum_kvot = _tolka_andligt_tal(post, "gaturum_kvot", 0.5, min_v=0.0, max_v=4.0)
        dubb_l = _tolka_andligt_tal(post, "andel_dubbdack_latt", 0.0, min_v=0.0, max_v=1.0)
        dubb_t = _tolka_andligt_tal(post, "andel_dubbdack_tung", 0.0, min_v=0.0, max_v=1.0)

        segment_lista.append(
            {
                "id": seg_id,
                "gatunamn": str(post.get("gatunamn") or seg_id),
                "langd_m": langd_m,
                "adt_fordon_dygn": adt,
                "hastighet_km_h": hast,
                "andel_tung_pct": tung_pct,
                "andel_tung": tung_pct / 100.0,
                "antal_korfalt": korfalt,
                "vagbredd_m": vagbredd_m,
                "avstand_m": avstand_m,
                "gaturum_kvot": gaturum_kvot,
                "andel_dubbdack_latt": dubb_l,
                "andel_dubbdack_tung": dubb_t,
            }
        )

    # 2. Ladda emissionsfaktorer (val 3A: lokal CSV/TOML eller aktivt skiss-schablonval).
    anvand_schablon = kropp.get("anvand_skiss_schablon") is True
    ef_fil = kropp.get("emissionsfaktorer_fil")
    ef_csv = kropp.get("emissionsfaktorer_csv")
    ef_toml = kropp.get("emissionsfaktorer_toml")
    ef_sha = kropp.get("emissionsfaktorer_sha256")
    try:
        ef_tabell = hamta_emissionsfaktorer(
            fil=str(ef_fil) if isinstance(ef_fil, str) and ef_fil.strip() else None,
            csv_text=str(ef_csv) if isinstance(ef_csv, str) and ef_csv.strip() else None,
            toml_text=str(ef_toml) if isinstance(ef_toml, str) and ef_toml.strip() else None,
            forvantad_sha256=str(ef_sha) if isinstance(ef_sha, str) and ef_sha.strip() else None,
            anvand_skiss_schablon=anvand_schablon,
        )
    except LuftFel as exc:
        raise FlodenWebbFel(str(exc), status_kod=400) from exc

    # 3. Ladda väderlager (`lager.vader_luft`) eller explicit vindros.
    vl = _ladda_projekt_vader_lager(projekt_mapp, geo_rot=geo_rot, manifest_fil=manifest_fil)
    vindros_obj: Mapping[str, Any] | None = None
    if isinstance(kropp.get("vindros"), Mapping):
        vindros_obj = kropp["vindros"]
    elif vl is not None:
        vindros_obj = vl.vindros
    if vindros_obj is None:
        raise FlodenWebbFel(
            "SMHI-vindros saknas (varken lager.vader_luft i projektets geografi eller explicit "
            "'vindros' angiven) — Floden hittar aldrig på väderdata.",
            status_kod=400,
        )
    vind_param = _extrahera_vindros_param(vindros_obj)

    varningar: list[str] = [
        "Metodstatus: skiss. Tidig planeringsuppskattning baserad på linjekällespridning, "
        "SMHI-vindros och valda emissionsfaktorer; ersätter inte 3D-CFD/LES eller MKN-prövning."
    ]
    if ef_tabell.ar_schablon and ef_tabell.varning:
        varningar.append(ef_tabell.varning)

    # 4. Valfri timvis NORTRIP-körning för PM10-vägdamm om timserie finns.
    timserie_ra: str | None = None
    if isinstance(kropp.get("vader_timserie_csv"), str) and kropp["vader_timserie_csv"].strip():
        timserie_ra = kropp["vader_timserie_csv"]
    elif vl is not None and vl.har_vader_timserie:
        timserie_ra = vl.las_vader_timserie_csv()

    nortrip_per_seg: dict[str, dict[str, Any]] = {}
    nortrip_kord = False
    nortrip_antal_timmar = 0
    if timserie_ra is not None:
        try:
            vader_serie = las_vaderserie_csv(timserie_ra)
        except (SmhiLuftStrangFel, LuftFel, ValueError) as exc:
            raise FlodenWebbFel(
                f"Ogiltig vädertimserie för NORTRIP: {exc}", status_kod=400
            ) from exc
        max_tim = max_luft_skiss_timmar()
        if vader_serie.antal_timmar > max_tim:
            raise FlodenWebbFel(
                f"Vädertimserien har {vader_serie.antal_timmar} timmar, vilket överstiger gränsen "
                f"på {max_tim} timmar ({FLODEN_MAX_LUFT_SKISS_TIMMAR_ENV}).",
                status_kod=400,
            )
        # Kör NORTRIP med tabellens genomsnittliga avgasfaktorer + referensparametrar för vägdamm.
        ef_l_ref = ef_tabell.hamta_faktor("latt", segment_lista[0]["hastighet_km_h"])
        ef_t_ref = ef_tabell.hamta_faktor("tung", segment_lista[0]["hastighet_km_h"])
        nortrip_param = replace(
            testparametrar(),
            nox_latt_g_km=ef_l_ref.nox_g_km,
            nox_tung_g_km=ef_t_ref.nox_g_km,
            pm_avgas_latt_g_km=ef_l_ref.pm10_avgas_g_km,
            pm_avgas_tung_g_km=ef_t_ref.pm10_avgas_g_km,
            ar_testvarde=True,
        )
        vag_obj = [
            Vagavsnitt(
                id=s["id"],
                langd_m=s["langd_m"],
                adt_fordon_dygn=s["adt_fordon_dygn"],
                andel_tung=s["andel_tung"],
                andel_dubbdack_latt=s["andel_dubbdack_latt"],
                andel_dubbdack_tung=s["andel_dubbdack_tung"],
                hastighet_km_h=s["hastighet_km_h"],
                antal_korfalt=s["antal_korfalt"],
                vagbredd_m=s["vagbredd_m"],
            )
            for s in segment_lista
        ]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            em_res = berakna_emission(vag_obj, vader_serie, Trafikprofil.jamn(), nortrip_param)
        sam_em = em_res.sammanfattning()
        nortrip_kord = True
        nortrip_antal_timmar = em_res.antal_timmar
        for rad_av in sam_em["avsnitt"]:
            nortrip_per_seg[str(rad_av["id"])] = rad_av
        varningar.append(
            f"NORTRIP-vägdamm kördes timvis över {nortrip_antal_timmar} timmar med "
            "referensparametrar (v12) för vägslitage/fukt (metodstatus: skiss)."
        )
    else:
        varningar.append(
            "Timvis SMHI-väderserie saknas: PM10-slitage beräknas från "
            "emissionsfaktortabellen utan timvis NORTRIP-vägfuktmodell."
        )

    # 5. Deterministisk utsläpps- och linjekällespridning per segment.
    u_eff = float(vind_param["effektiv_vind_m_s"])
    u_lag = float(vind_param["dygn_lagvind_m_s"])
    dygn_faktor = u_eff / max(u_lag, 0.5)
    sqrt_2_pi = math.sqrt(2.0 / math.pi)

    per_segment_ut: list[dict[str, Any]] = []
    tot_nox_tillskott_ar = 0.0
    tot_nox_tillskott_dygn = 0.0
    tot_pm10_tillskott_ar = 0.0
    tot_pm10_tillskott_dygn = 0.0
    viktad_no2_andel_num = 0.0

    for seg in segment_lista:
        ef_l = ef_tabell.hamta_faktor("latt", seg["hastighet_km_h"])
        ef_t = ef_tabell.hamta_faktor("tung", seg["hastighet_km_h"])
        f_t = seg["andel_tung"]
        f_l = 1.0 - f_t
        fordon_per_s = seg["adt_fordon_dygn"] / 86_400.0

        ef_nox_g_km = f_l * ef_l.nox_g_km + f_t * ef_t.nox_g_km
        ef_pm10_avgas_g_km = f_l * ef_l.pm10_avgas_g_km + f_t * ef_t.pm10_avgas_g_km
        ef_pm10_slitage_g_km = f_l * ef_l.pm10_slitage_g_km + f_t * ef_t.pm10_slitage_g_km
        q_nox_g_m_s = fordon_per_s * ef_nox_g_km / 1000.0

        nt_post = nortrip_per_seg.get(seg["id"])
        if nt_post is not None:
            q_pm10_g_m_s = float(nt_post["arsmedel_pm10_g_m_s"])
            pm10_kalla = "nortrip_timvis"
        else:
            q_pm10_g_m_s = fordon_per_s * (ef_pm10_avgas_g_km + ef_pm10_slitage_g_km) / 1000.0
            pm10_kalla = "emissionsfaktortabell"

        # Direkt NO2-andel viktad på NOx-bidraget från lätta respektive tunga fordon.
        nox_l = f_l * ef_l.nox_g_km
        nox_t = f_t * ef_t.nox_g_km
        direkt_no2 = (
            (nox_l * ef_l.direkt_no2_andel + nox_t * ef_t.direkt_no2_andel) / (nox_l + nox_t)
            if (nox_l + nox_t) > 0.0
            else 0.0
        )

        # Gaussisk linjekälleskiss (neutral klass D + initial trafikblandning + gaturumsfaktor).
        d_m = seg["avstand_m"]
        sigma_z_d = 0.06 * d_m / math.sqrt(1.0 + 0.0015 * d_m)
        sigma_z_eff = math.hypot(1.5, sigma_z_d)
        canyon_faktor = 1.0 + seg["gaturum_kvot"]
        # µg/m³ per g/(m·s)
        sprid_faktor = canyon_faktor * sqrt_2_pi / (u_eff * sigma_z_eff) * 1.0e6

        c_nox_ar = q_nox_g_m_s * sprid_faktor
        c_nox_dygn = c_nox_ar * dygn_faktor
        c_pm10_ar = q_pm10_g_m_s * sprid_faktor
        c_pm10_dygn = c_pm10_ar * dygn_faktor

        tot_nox_tillskott_ar += c_nox_ar
        tot_nox_tillskott_dygn += c_nox_dygn
        tot_pm10_tillskott_ar += c_pm10_ar
        tot_pm10_tillskott_dygn += c_pm10_dygn
        viktad_no2_andel_num += c_nox_ar * direkt_no2

        seg_rad: dict[str, Any] = {
            "id": seg["id"],
            "gatunamn": seg["gatunamn"],
            "langd_m": round(seg["langd_m"], 2),
            "adt_fordon_dygn": round(seg["adt_fordon_dygn"], 1),
            "hastighet_km_h": round(seg["hastighet_km_h"], 1),
            "andel_tung_pct": round(seg["andel_tung_pct"], 2),
            "avstand_m": round(d_m, 1),
            "emission_nox_g_m_s": round(q_nox_g_m_s, 8),
            "emission_pm10_g_m_s": round(q_pm10_g_m_s, 8),
            "pm10_berakning": pm10_kalla,
            "direkt_no2_andel": round(direkt_no2, 4),
            "lokalt_tillskott_nox_arsmedel_ug_m3": round(c_nox_ar, 3),
            "lokalt_tillskott_nox_dygn_ug_m3": round(c_nox_dygn, 3),
            "lokalt_tillskott_pm10_arsmedel_ug_m3": round(c_pm10_ar, 3),
            "lokalt_tillskott_pm10_dygn_ug_m3": round(c_pm10_dygn, 3),
        }
        if nt_post is not None:
            seg_rad["nortrip"] = {
                "andel_uppvirvling_pm10": round(float(nt_post["andel_uppvirvling_pm10"]), 4),
                "andel_vata_timmar": round(float(nt_post["andel_vata_timmar"]), 4),
                "utslapp_pm10_kg_ar": round(float(nt_post["utslapp_pm10_kg_ar"]), 3),
            }
        per_segment_ut.append(seg_rad)

    eff_direkt_no2 = (
        viktad_no2_andel_num / tot_nox_tillskott_ar if tot_nox_tillskott_ar > 0.0 else 0.0
    )

    # 6. Koppla urban bakgrundshalt (explicit eller från lager.vader_luft — aldrig påhittad!).
    expl_bakg = kropp.get("bakgrund") if isinstance(kropp.get("bakgrund"), Mapping) else None
    lager_bakg = vl.bakgrund_luft if vl is not None else None
    no2_b_ar, no2_b_kalla = _las_bakgrund_varde(expl_bakg, lager_bakg, "NO2", "no2_arsmedel_ug_m3")
    pm10_b_ar, pm10_b_kalla = _las_bakgrund_varde(
        expl_bakg, lager_bakg, "PM10", "pm10_arsmedel_ug_m3"
    )
    pm10_b_p90, _ = _las_bakgrund_varde(
        expl_bakg, lager_bakg, "PM10", "pm10_p90_4_ug_m3", stat_nyckel="percentil_90_4_ug_m3"
    )
    o3_b_ar, o3_b_kalla = _las_bakgrund_varde(expl_bakg, lager_bakg, "O3", "o3_arsmedel_ug_m3")

    # Fotostationär NO2-kemi när både NO2- och O3-bakgrund finns (annars varning + direkt NO2).
    strang_glob_w: float | None = None
    if vl is not None and isinstance(vl.utci_sommar.get("strang"), Mapping):
        g_ra = vl.utci_sommar["strang"].get("global_p95_dag_w_m2")
        if isinstance(g_ra, int | float) and math.isfinite(float(g_ra)) and float(g_ra) > 0.0:
            strang_glob_w = float(g_ra)
    j_no2 = (
        min(1.0e-2, max(1.0e-3, 1.0e-5 * strang_glob_w))
        if strang_glob_w is not None
        else _J_NO2_STANDARD_DAG_1_S
    )

    no2_tot_ar: float | None = None
    no2_tillskott_ar: float | None = None
    no2_tillskott_dygn: float | None = None
    no2_kemi_metod: str
    if no2_b_ar is not None and o3_b_ar is not None:
        no2_tot_ar = round(
            float(
                no2_fotostationar(
                    tot_nox_tillskott_ar,
                    no2_b_ar,
                    o3_b_ar,
                    eff_direkt_no2,
                    j_no2,
                    _K_NO_O3_STANDARD_PPB_1_S,
                )
            ),
            3,
        )
        no2_tot_dygn = float(
            no2_fotostationar(
                tot_nox_tillskott_dygn,
                no2_b_ar,
                o3_b_ar,
                eff_direkt_no2,
                j_no2,
                _K_NO_O3_STANDARD_PPB_1_S,
            )
        )
        no2_tillskott_ar = round(max(0.0, no2_tot_ar - no2_b_ar), 3)
        no2_tillskott_dygn = round(max(0.0, no2_tot_dygn - no2_b_ar), 3)
        no2_kemi_metod = "fotostationar_no_o3_no2"
    else:
        no2_tillskott_ar = round(tot_nox_tillskott_ar * eff_direkt_no2, 3)
        no2_tillskott_dygn = round(tot_nox_tillskott_dygn * eff_direkt_no2, 3)
        no2_kemi_metod = "endast_direkt_no2_saknar_o3_eller_no2_bakgrund"
        varningar.append(
            "Urban bakgrund för NO₂ och/eller O₃ saknas: fotostationär NO₂-kemi kunde inte "
            "beräkna total NO₂-halt (visar enbart lokalt NOx- och direkt NO₂-tillskott)."
        )

    pm10_tot_ar = (
        round(pm10_b_ar + tot_pm10_tillskott_ar, 3) if pm10_b_ar is not None else None
    )
    pm10_tot_p90 = (
        round(pm10_b_p90 + tot_pm10_tillskott_dygn, 3) if pm10_b_p90 is not None else None
    )
    if pm10_b_ar is None:
        varningar.append(
            "Urban bakgrund för PM₁₀ saknas: visar enbart lokalt trafiktillskott utan totalhalt."
        )

    return {
        "format": FORMAT_LUFT_SKISS_WEBB,
        "metodstatus": "skiss",
        "ar_schablon": ef_tabell.ar_schablon,
        "emissionskalla": {
            "kalla": ef_tabell.kalla,
            "ar": ef_tabell.ar,
            "sha256": ef_tabell.sha256,
            "ar_schablon": ef_tabell.ar_schablon,
            "filnamn": ef_tabell.filnamn,
            "metodstatus": ef_tabell.metodstatus,
            "varning": ef_tabell.varning,
        },
        "nortrip_kord": nortrip_kord,
        "nortrip_antal_timmar": nortrip_antal_timmar,
        "vindros_param": vind_param,
        "no2_kemi_metod": no2_kemi_metod,
        "lokalt_tillskott": {
            "nox_arsmedel_ug_m3": round(tot_nox_tillskott_ar, 3),
            "nox_dygn_ug_m3": round(tot_nox_tillskott_dygn, 3),
            "no2_arsmedel_ug_m3": no2_tillskott_ar,
            "no2_dygn_ug_m3": no2_tillskott_dygn,
            "pm10_arsmedel_ug_m3": round(tot_pm10_tillskott_ar, 3),
            "pm10_dygn_ug_m3": round(tot_pm10_tillskott_dygn, 3),
        },
        "bakgrund": {
            "no2_arsmedel_ug_m3": no2_b_ar,
            "no2_kalla": no2_b_kalla,
            "pm10_arsmedel_ug_m3": pm10_b_ar,
            "pm10_p90_4_ug_m3": pm10_b_p90,
            "pm10_kalla": pm10_b_kalla,
            "o3_arsmedel_ug_m3": o3_b_ar,
            "o3_kalla": o3_b_kalla,
        },
        "totalt": {
            "no2_arsmedel_ug_m3": no2_tot_ar,
            "pm10_arsmedel_ug_m3": pm10_tot_ar,
            "pm10_dygn_90_4_ug_m3": pm10_tot_p90,
        },
        "mkn_jamforelse": {
            "no2_arsmedel_grans_ug_m3": _MKN_NO2_ARSMEDEL_UG_M3,
            "no2_arsmedel_klarar_skiss": (
                no2_tot_ar <= _MKN_NO2_ARSMEDEL_UG_M3 if no2_tot_ar is not None else None
            ),
            "pm10_arsmedel_grans_ug_m3": _MKN_PM10_ARSMEDEL_UG_M3,
            "pm10_arsmedel_klarar_skiss": (
                pm10_tot_ar <= _MKN_PM10_ARSMEDEL_UG_M3 if pm10_tot_ar is not None else None
            ),
            "pm10_dygn_90_4_grans_ug_m3": _MKN_PM10_DYGN_90_4_UG_M3,
            "pm10_dygn_90_4_klarar_skiss": (
                pm10_tot_p90 <= _MKN_PM10_DYGN_90_4_UG_M3 if pm10_tot_p90 is not None else None
            ),
        },
        "antal_segment": len(per_segment_ut),
        "per_segment": per_segment_ut,
        "varningar": list(dict.fromkeys(varningar)),
    }
