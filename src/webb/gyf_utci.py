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
import hashlib
import json
import math
import re
import threading
import urllib.parse
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
from dagsljus.matt import svf_fran_horisont
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
    ytmodell_fran,
)
from karna.geometri.lasa import vegetation_pa_marknat
from karna.geometri.rasterfil import ruta_fran_geo
from karna.ritat import RitatFel
from karna.scenario import Andring, ScenarioFel, las_scenario
from markmodell.byggnadsregister import ByggnadsPost, markhojd_under_byggnad
from markmodell.hinder_oppningar import BYGGNAD, HARDGJORT, brann_in_byggnader
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
KOORDINATSYSTEM_EPSG: Final[str] = "EPSG:3011"

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
    """Trådsäker, minnesbegränsad cache av värmestress-PNG:er per `(projekt_id, lager_id)`."""

    def __init__(self, max_poster: int = 64) -> None:
        self._max_poster = max(1, int(max_poster))
        self._lock = threading.Lock()
        self._data: OrderedDict[tuple[str, str], tuple[bytes, Utbredning]] = OrderedDict()

    def satt(self, projekt_id: str, lager_id: str, png: bytes, utbredning: Utbredning) -> None:
        nyckel = (projekt_id, lager_id)
        with self._lock:
            self._data[nyckel] = (png, utbredning)
            self._data.move_to_end(nyckel)
            while len(self._data) > self._max_poster:
                self._data.popitem(last=False)

    def hamta(self, projekt_id: str, lager_id: str) -> tuple[bytes, Utbredning] | None:
        if not _LAGER_ID_RE.match(lager_id):
            raise FlodenWebbFel("Ogiltigt värmestresslager-id.", status_kod=400)
        nyckel = (projekt_id, lager_id)
        with self._lock:
            post = self._data.get(nyckel)
            if post is not None:
                self._data.move_to_end(nyckel)
            return post


class _SvfCache:
    """Trådsäker LRU-cache för geometriberoende SVF-fält så väderjusteringar går på <0,1 s."""

    def __init__(self, max_poster: int = 16) -> None:
        self._max_poster = max(1, int(max_poster))
        self._lock = threading.Lock()
        self._data: OrderedDict[tuple[str, int, float], NDArray[np.float64]] = OrderedDict()

    def hamta(self, nyckel: tuple[str, int, float]) -> NDArray[np.float64] | None:
        with self._lock:
            arr = self._data.get(nyckel)
            if arr is not None:
                self._data.move_to_end(nyckel)
            return arr

    def satt(self, nyckel: tuple[str, int, float], svf: NDArray[np.float64]) -> None:
        with self._lock:
            self._data[nyckel] = svf
            self._data.move_to_end(nyckel)
            while len(self._data) > self._max_poster:
                self._data.popitem(last=False)


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


def _brann_byggnader_mask(
    terrang: NDArray[np.float32],
    geo: GeoTransformInfo,
    byggnader: Sequence[ByggnadsPost],
) -> tuple[NDArray[np.float32], NDArray[np.bool_]]:
    """Bygger DSM och fotavtrycksmask från terräng och byggnader (samma regel som K1 v2)."""
    if not byggnader:
        return terrang.copy(), np.zeros(terrang.shape, dtype=np.bool_)
    dsm, slag = brann_in_byggnader(
        terrang,
        np.zeros(terrang.shape, dtype=np.uint8),
        ruta_fran_geo(geo),
        [b.till_byggnad() for b in byggnader],
    )
    return np.asarray(dsm, dtype=np.float32), np.asarray(slag == BYGGNAD, dtype=np.bool_)


def _sammanfoga_k1_rutor(
    rutor: Sequence[Path],
    *,
    krav_v2_terrang: bool,
) -> tuple[
    GeoTransformInfo,
    NDArray[np.float32],
    NDArray[np.float64],
    NDArray[np.bool_],
    NDArray[np.float32],
    tuple[ByggnadsPost, ...],
]:
    """Sammanfogar en eller flera K1-rutor med regeln 'djupast inne i rutan'."""
    if not rutor:
        raise FlodenWebbFel("Projektet saknar K1-rutor att analysera.", status_kod=404)

    delar: list[
        tuple[
            NDArray[np.float32],
            NDArray[np.uint8],
            NDArray[np.float32],
            GeoTransformInfo,
        ]
    ] = []
    bygg_per_id: dict[str, ByggnadsPost] = {}
    px: float | None = None

    for mapp in rutor:
        try:
            slag, geo_s = las_markslag(mapp)
        except (GeometriFel, OSError, ValueError) as exc:
            raise FlodenWebbFel(
                f"Kunde inte läsa markslag.tif i {mapp.name}: {exc}", status_kod=400
            ) from exc

        if abs(geo_s.pixelstorlek_x - geo_s.pixelstorlek_y) > 1e-9:
            raise FlodenWebbFel("Icke-kvadratiska celler i K1-rutan stöds inte.", status_kod=400)
        if px is None:
            px = float(geo_s.pixelstorlek_x)
        elif abs(px - float(geo_s.pixelstorlek_x)) > 1e-9:
            raise FlodenWebbFel(
                f"K1-rutorna har olika cellstorlek ({px} m och {geo_s.pixelstorlek_x} m).",
                status_kod=400,
            )

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

        delar.append((np.asarray(terr, dtype=np.float32), slag, veg_m, geo_s))

    assert px is not None
    byggnader = tuple(sorted(bygg_per_id.values(), key=lambda b: b.byggnad_id))

    if len(delar) == 1:
        terr0, slag0, veg0, geo0 = delar[0]
        har_data = np.ones(slag0.shape, dtype=np.bool_)
        return (
            geo0,
            terr0,
            slag0.astype(np.float64),
            har_data,
            veg0,
            byggnader,
        )

    x0 = min(g.x_min for _, _, _, g in delar)
    y1 = max(g.y_max for _, _, _, g in delar)
    x1 = max(g.x_min + g.bredd * px for _, _, _, g in delar)
    y0 = min(g.y_max - g.hojd * px for _, _, _, g in delar)
    bredd = round((x1 - x0) / px)
    hojd = round((y1 - y0) / px)

    terr_ut = np.full((hojd, bredd), np.nan, dtype=np.float32)
    slag_ut = np.full((hojd, bredd), np.nan, dtype=np.float64)
    veg_ut = np.zeros((hojd, bredd), dtype=np.float32)
    har_data = np.zeros((hojd, bredd), dtype=np.bool_)
    djupast = np.full((hojd, bredd), -np.inf, dtype=np.float32)

    for terr, slag, veg, g in delar:
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
        rutor, krav_v2_terrang=krav_v2_terrang
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
    mm = ruta_fran_geo(geo)

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
                mark_under = float(markhojd_under_byggnad(terr_saker, mm, yttre, hal_ringar))
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
        kartverktyg, projekt_tjanst, omrade_id, krav_v2_terrang=False
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

    svf_key = (cache_nyckel_geo, int(antal_riktningar), float(max_avstand_m))
    svf_cachad = _GLOBAL_SVF_CACHE.hamta(svf_key)
    if svf_cachad is not None:
        svf = svf_cachad.copy()
    else:
        try:
            horisonter = [
                h
                for _, _, h, _ in horisont_ruta_iter(
                    falt,
                    antal_riktningar,
                    max_avstand_m,
                    fonster=fonster,
                )
            ]
        except HorisontFel as exc:
            raise FlodenWebbFel(f"Horisontberäkning misslyckades: {exc}", status_kod=400) from exc
        svf = svf_fran_horisont(np.stack(horisonter, axis=0), axel=0)
        _GLOBAL_SVF_CACHE.satt(svf_key, svf)

    r0, r1, c0, c1 = fonster
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

    t_luft_c = _tolka_andligt_tal(
        payload, "t_luft_c", STANDARD_T_LUFT_C, min_v=-273.15, strikt_min=True
    )
    relativ_fuktighet_pct = _tolka_andligt_tal(
        payload, "relativ_fuktighet_pct", STANDARD_RH_PCT, min_v=0.0, max_v=100.0
    )
    vind_10m_m_s = _tolka_andligt_tal(payload, "vind_10m_m_s", STANDARD_VIND_10M_M_S, min_v=0.0)
    sol_direkt_w_m2 = _tolka_andligt_tal(
        payload, "sol_direkt_w_m2", STANDARD_SOL_DIREKT_W_M2, min_v=0.0
    )
    sol_diffus_w_m2 = _tolka_andligt_tal(
        payload, "sol_diffus_w_m2", STANDARD_SOL_DIFFUS_W_M2, min_v=0.0
    )
    solhojd_grad = _tolka_andligt_tal(
        payload, "solhojd_grad", STANDARD_SOLHOJD_GRAD, min_v=-90.0, max_v=90.0
    )
    solazimut_grad = _tolka_andligt_tal(
        payload, "solazimut_grad", STANDARD_SOLAZIMUT_GRAD, min_v=0.0, max_v=360.0
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
        kartverktyg, projekt_tjanst, omrade_id, krav_v2_terrang=True
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
        dsm_full = ytmodell_fran(terr_saker, geo, byggnader, vegetation_markrutnat=None)
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
    return {
        "format": FORMAT_VARMESTRESS_WEBB,
        "koordinatsystem": KOORDINATSYSTEM_EPSG,
        "omrade_id": omrade_id,
        "scenario": scenario,
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
