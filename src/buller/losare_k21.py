"""K21-skisslösare för vägtrafikbuller (`buller.nord2000`) ovanpå Flodens bullermotor.

Kopplar Flodens befintliga bullerdelar till kärnans K20/K21-körmotor:
- Gemensam K1 v2-geometri (`terrang.tif`, `mark.tif`, `markslag.tif`, `byggnader.gpkg`) samt
  K9-ändringar (nya/ändrade/rivna byggnader i `byggnader.gpkg` och murar/skärmar i
  `brytlinjer.gpkg`).
- Källmodell för vägtrafik: Nord2000 Road (`buller.emission.modell.Vagemission`, SP Rapport
  2006:12). Trafikindata (`vagsegment`) måste alltid anges uttryckligen; lösaren hittar aldrig
  på trafik.
- Utbredningskärna: `buller.nord2000.Nord2000Karna` (förval, med TC-101327 terrängförenkling för
  långa profiler) eller valfritt `buller.akustik.BaslinjeKarna`. Båda körs genom
  `buller.banor.banfinnare.bygg_banuppsattning` och `buller.lagen.korning.kor_buller` i skissläge.

Hårda invarianter (se `AGENTS.md` och `docs/buller/k21.md`):
- `nord2000_konform` är alltid `False`.
- `metodstatus` är alltid `"skiss"`.
- `rapport_sparrad` är alltid `True` (formella bullerrapporter förblir spärrade).
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar, Final, cast

import numpy as np
from numpy.typing import NDArray

from karna.analyslosare import Analyslosare, LosarFel, LosarKontext, LosarUtdata

FORMAT_BULLER_K21: Final[str] = "floden-buller-k21/v1"
METODSTATUS: Final[str] = "skiss"
NORD2000_KONFORM: Final[bool] = False
RAPPORT_SPARRAD: Final[bool] = True
SKISSMARKNING_K21: Final[str] = "Skiss – ej för beslut"
SKISSVARNING: Final[str] = (
    "Skiss – ej för beslut: buller.nord2000 kör en ofullständigt verifierad Nord2000-kärna "
    "(nord2000_konform=False, testsvit grupp N är inte godkänd). Formella bullerrapporter är "
    "spärrade."
)

STANDARD_MOTTAGARHOJD_M: Final[float] = 2.0
STANDARD_SEGMENT_LANGD_M: Final[float] = 10.0
STANDARD_FASAD_STEG_M: Final[float] = 5.0
STANDARD_FASAD_AVSTAND_M: Final[float] = 0.5
STANDARD_VANINGSHOJD_M: Final[float] = 3.0
STANDARD_FASAD_HOJD_OVER_GOLV_M: Final[float] = 1.5
STANDARD_MAX_RASTERCELLER: Final[int] = 2_500
STANDARD_AUTO_MAX_CELLER: Final[int] = 400
STANDARD_SKARM_TJOCKLEK_M: Final[float] = 0.2
STANDARD_REFLEXORDNING: Final[int] = 1
PERIOD_DYGN_S: Final[float] = 86_400.0
RIKTVARDE_LAG_DBA: Final[float] = 55.0
RIKTVARDE_HOG_DBA: Final[float] = 60.0

# Brytlinjetyper från K1/K9 (`brytlinjer.gpkg`) som verkar som vertikala bullerskärmar.
BULLERSKARM_BRYTLINJE_TYPER: Final[frozenset[str]] = frozenset(
    {"mur", "stodmur", "staket", "bullerskarm"}
)

# Konservativ marginal över fri sfärisk spridning för Nord2000Karna: täcker hård mark (+6,02 dB),
# skålformad terräng i delmodell 3 (upp mot +9 dB) och flera markreflexer i delmodell 8.
_NORD2000_OVRE_GRANS_MARGINAL_DB: Final[float] = 15.0
_MIN_AVSTAND_KALLA_MOTTAGARE_M: Final[float] = 0.05
_RASTER_KANT_MARGINAL_M: Final[float] = 1.0e-3
_GP_ENVELOPE_BYTES: Final[dict[int, int]] = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}

_TILLATNA_PARAMETRAR: Final[frozenset[str]] = frozenset(
    {
        "vagsegment",
        "mottagarhojd_m",
        "raster_cell_m",
        "max_rasterceller",
        "segment_langd_m",
        "fasad_steg_m",
        "fasad_avstand_m",
        "vaningshojd_m",
        "max_vaningar",
        "max_fasadpunkter",
        "reflexordning",
        "karna",
        "okand_mark_klass",
        "byggnader_med_hal",
        "bullerskarmar",
        "land",
        "utsnitt_3011",
        "fasad_utsnitt_3011",
    }
)
_TILLATNA_VAG_NYCKLAR: Final[frozenset[str]] = frozenset(
    {"id", "koordinater", "adt_fordon_dygn", "hastighet_km_h", "andel_tung_pct", "korfalt"}
)
_TILLATNA_SKARM_NYCKLAR: Final[frozenset[str]] = frozenset(
    {"id", "koordinater", "hojd_m", "tjocklek_m", "reflektionsforlust_db"}
)
_GILTIGA_IMPEDANSKLASSER: Final[frozenset[str]] = frozenset({"A", "B", "C", "D", "E", "F", "G"})
_GILTIGA_LANDER: Final[frozenset[str]] = frozenset({"SE", "NO", "FI", "DK"})


class BullerK21Fel(LosarFel, ValueError):
    """Fel i K21-lösaren `buller.nord2000` (ärver både `LosarFel` och `ValueError`)."""


@dataclass(frozen=True)
class _FasadMeta:
    """Metadata för en K11-fasadpunkt som förs vidare till `fasad_buller.geojson`."""

    fasadpunkt_id: str
    byggnad_id: str
    vaning: int
    hojd_over_mark_m: float
    z_rh2000_m: float
    fasadnormal_deg: float
    e_m: float
    n_m: float


@dataclass(frozen=True)
class _SkarmLinje:
    """En bullerskärmslinje i plan (från `brytlinjer.gpkg` eller `parametrar.bullerskarmar`)."""

    skarm_id: str
    koordinater: tuple[tuple[float, float], ...]
    hojd_m: float
    tjocklek_m: float
    reflektionsforlust_db: float
    z_kron_rh2000: tuple[float, ...] | None = None


def _krav_tal(
    obj: Mapping[str, Any],
    nyckel: str,
    sammanhang: str,
    *,
    minst: float | None = None,
    over: float | None = None,
    hogst: float | None = None,
) -> float:
    if nyckel not in obj:
        raise BullerK21Fel(f"{sammanhang}: obligatoriskt fält '{nyckel}' saknas.")
    v = obj[nyckel]
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise BullerK21Fel(f"{sammanhang}: '{nyckel}' måste vara ett tal, fick {v!r}.")
    fv = float(v)
    if not math.isfinite(fv):
        raise BullerK21Fel(f"{sammanhang}: '{nyckel}' måste vara ändligt, fick {v!r}.")
    if minst is not None and fv < minst:
        raise BullerK21Fel(f"{sammanhang}: '{nyckel}' måste vara >= {minst:g}, fick {fv:g}.")
    if over is not None and fv <= over:
        raise BullerK21Fel(f"{sammanhang}: '{nyckel}' måste vara > {over:g}, fick {fv:g}.")
    if hogst is not None and fv > hogst:
        raise BullerK21Fel(f"{sammanhang}: '{nyckel}' måste vara <= {hogst:g}, fick {fv:g}.")
    return fv


def _valfritt_tal(
    obj: Mapping[str, Any],
    nyckel: str,
    standard: float,
    sammanhang: str,
    *,
    minst: float | None = None,
    over: float | None = None,
    hogst: float | None = None,
) -> float:
    if nyckel not in obj:
        return standard
    return _krav_tal(obj, nyckel, sammanhang, minst=minst, over=over, hogst=hogst)


def _valfritt_heltal(
    obj: Mapping[str, Any],
    nyckel: str,
    standard: int,
    sammanhang: str,
    *,
    minst: int | None = None,
    hogst: int | None = None,
) -> int:
    if nyckel not in obj:
        return standard
    v = obj[nyckel]
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
        raise BullerK21Fel(f"{sammanhang}: '{nyckel}' måste vara ett heltal, fick {v!r}.")
    if float(v) != int(v):
        raise BullerK21Fel(f"{sammanhang}: '{nyckel}' måste vara ett heltal, fick {v!r}.")
    iv = int(v)
    if minst is not None and iv < minst:
        raise BullerK21Fel(f"{sammanhang}: '{nyckel}' måste vara >= {minst}, fick {iv}.")
    if hogst is not None and iv > hogst:
        raise BullerK21Fel(f"{sammanhang}: '{nyckel}' måste vara <= {hogst}, fick {iv}.")
    return iv


def _validera_koordinatlista(ra: Any, sammanhang: str) -> list[list[float]]:
    if not isinstance(ra, list | tuple) or len(ra) < 2:
        raise BullerK21Fel(
            f"{sammanhang}: 'koordinater' måste vara en lista med minst två [E, N]-punkter "
            "i EPSG:3011."
        )
    ut: list[list[float]] = []
    for i, pt in enumerate(ra):
        if not isinstance(pt, list | tuple) or len(pt) < 2:
            raise BullerK21Fel(
                f"{sammanhang}.koordinater[{i}]: punkt måste vara [E, N], fick {pt!r}."
            )
        e_ra, n_ra = pt[0], pt[1]
        if (
            isinstance(e_ra, bool)
            or isinstance(n_ra, bool)
            or not isinstance(e_ra, int | float)
            or not isinstance(n_ra, int | float)
        ):
            raise BullerK21Fel(
                f"{sammanhang}.koordinater[{i}]: koordinater måste vara tal, fick {pt!r}."
            )
        e, n = float(e_ra), float(n_ra)
        if not (math.isfinite(e) and math.isfinite(n)):
            raise BullerK21Fel(
                f"{sammanhang}.koordinater[{i}]: koordinater måste vara ändliga, fick {pt!r}."
            )
        if ut and math.hypot(e - ut[-1][0], n - ut[-1][1]) <= 1.0e-9:
            raise BullerK21Fel(
                f"{sammanhang}.koordinater[{i}]: två på varandra följande punkter sammanfaller."
            )
        ut.append([e, n])
    return ut


def _validera_vagsegment(ra: Any, namn: str) -> list[dict[str, Any]]:
    if ra is None:
        raise BullerK21Fel(
            f"{namn}: parametern 'vagsegment' saknas. Trafikindata (koordinater i EPSG:3011, "
            "adt_fordon_dygn, hastighet_km_h, andel_tung_pct) måste alltid anges uttryckligen."
        )
    if not isinstance(ra, list | tuple) or len(ra) == 0:
        raise BullerK21Fel(
            f"{namn}: 'vagsegment' måste vara en icke-tom lista; trafikdata får aldrig antas."
        )
    ut: list[dict[str, Any]] = []
    sedda: set[tuple[str, int]] = set()
    for idx, post in enumerate(ra):
        ctx = f"{namn}.vagsegment[{idx}]"
        if not isinstance(post, Mapping):
            raise BullerK21Fel(f"{ctx}: varje vägsegment måste vara ett objekt.")
        okanda = sorted(set(post) - _TILLATNA_VAG_NYCKLAR)
        if okanda:
            raise BullerK21Fel(f"{ctx}: okända fält {okanda}.")
        seg_id_ra = post.get("id", f"vag_{idx + 1}")
        if not isinstance(seg_id_ra, str) or not seg_id_ra.strip():
            raise BullerK21Fel(f"{ctx}: 'id' måste vara en icke-tom text.")
        seg_id = seg_id_ra.strip()
        korfalt = _valfritt_heltal(post, "korfalt", 0, ctx, minst=0)
        if (seg_id, korfalt) in sedda:
            raise BullerK21Fel(
                f"{ctx}: duplicerat vägsegment ('{seg_id}', körfält {korfalt})."
            )
        sedda.add((seg_id, korfalt))
        koord = _validera_koordinatlista(post.get("koordinater"), ctx)
        adt = _krav_tal(post, "adt_fordon_dygn", ctx, minst=0.0)
        hast = _krav_tal(post, "hastighet_km_h", ctx, over=0.0)
        tung = _krav_tal(post, "andel_tung_pct", ctx, minst=0.0, hogst=100.0)
        ut.append(
            {
                "id": seg_id,
                "korfalt": korfalt,
                "koordinater": koord,
                "adt_fordon_dygn": adt,
                "hastighet_km_h": hast,
                "andel_tung_pct": tung,
            }
        )
    return ut


def _validera_bullerskarmar(ra: Any, namn: str) -> list[dict[str, Any]]:
    if ra is None:
        return []
    if not isinstance(ra, list | tuple):
        raise BullerK21Fel(f"{namn}: 'bullerskarmar' måste vara en lista.")
    ut: list[dict[str, Any]] = []
    sedda: set[str] = set()
    for idx, post in enumerate(ra):
        ctx = f"{namn}.bullerskarmar[{idx}]"
        if not isinstance(post, Mapping):
            raise BullerK21Fel(f"{ctx}: varje bullerskärm måste vara ett objekt.")
        okanda = sorted(set(post) - _TILLATNA_SKARM_NYCKLAR)
        if okanda:
            raise BullerK21Fel(f"{ctx}: okända fält {okanda}.")
        sid_ra = post.get("id", f"skarm_{idx + 1}")
        if not isinstance(sid_ra, str) or not sid_ra.strip():
            raise BullerK21Fel(f"{ctx}: 'id' måste vara en icke-tom text.")
        sid = sid_ra.strip()
        if sid in sedda:
            raise BullerK21Fel(f"{ctx}: duplicerat skärm-id '{sid}'.")
        sedda.add(sid)
        ut.append(
            {
                "id": sid,
                "koordinater": _validera_koordinatlista(post.get("koordinater"), ctx),
                "hojd_m": _krav_tal(post, "hojd_m", ctx, over=0.0),
                "tjocklek_m": _valfritt_tal(
                    post, "tjocklek_m", STANDARD_SKARM_TJOCKLEK_M, ctx, over=0.0
                ),
                "reflektionsforlust_db": _valfritt_tal(
                    post, "reflektionsforlust_db", 1.0, ctx, minst=0.0
                ),
            }
        )
    return ut


def _avkoda_gp_linestring(blob: bytes | bytearray) -> tuple[
    tuple[tuple[float, float], ...], tuple[float, ...] | None
]:
    """Avkodar en GeoPackage-WKB LineString (2D eller Z) utan beroende till vattenpaketen."""
    import shapely

    if len(blob) < 8 or bytes(blob[:2]) != b"GP":
        raise BullerK21Fel("Ogiltig GeoPackage-geometriheader i brytlinjer.gpkg.")
    flags = int(blob[3])
    env_code = (flags >> 1) & 0x07
    env_bytes = _GP_ENVELOPE_BYTES.get(env_code)
    if env_bytes is None:
        raise BullerK21Fel(f"Okänd GeoPackage-envelope-kod {env_code} i brytlinjer.gpkg.")
    wkb_start = 8 + env_bytes
    if len(blob) <= wkb_start:
        raise BullerK21Fel("Trunkerad WKB-geometri i brytlinjer.gpkg.")
    geom = shapely.from_wkb(bytes(blob[wkb_start:]))
    if geom is None or geom.is_empty or geom.geom_type != "LineString":
        gtyp = getattr(geom, "geom_type", None)
        raise BullerK21Fel(
            f"Brytlinje i brytlinjer.gpkg måste vara LineString, fick {gtyp!r}."
        )
    koord_raw = list(geom.coords)
    if len(koord_raw) < 2:
        raise BullerK21Fel("Brytlinje i brytlinjer.gpkg har färre än 2 punkter.")
    xy = tuple((float(c[0]), float(c[1])) for c in koord_raw)
    if len(koord_raw[0]) >= 3:
        z_vals = tuple(float(c[2]) for c in koord_raw)
        if all(math.isfinite(z) for z in z_vals):
            return xy, z_vals
    return xy, None


def _las_brytlinje_skarmar(rutor: Sequence[Any]) -> list[_SkarmLinje]:
    """Läser skärmande brytlinjer (`mur`, `stodmur`, `staket`, `bullerskarm`) ur K1-rutor."""
    from testsvit.k1_io import K1FormatFel, las_gpkg_tabell

    sedda: set[tuple[str, float, tuple[tuple[float, float], ...], tuple[float, ...] | None]] = set()
    ut: list[_SkarmLinje] = []
    for ruta in rutor:
        fil = Path(ruta.mapp) / "brytlinjer.gpkg"
        if not fil.is_file():
            continue
        try:
            srs_id, rader = las_gpkg_tabell(fil, "brytlinjer")
        except K1FormatFel as exc:
            raise BullerK21Fel(f"Kunde inte läsa {fil}: {exc}") from exc
        if srs_id != 3011:
            raise BullerK21Fel(f"{fil} har srs_id={srs_id}, förväntade EPSG:3011.")
        for rad in rader:
            typ = str(rad.get("typ", "")).strip().lower()
            hojd = float(rad.get("hojd_m", 0.0))
            if typ not in BULLERSKARM_BRYTLINJE_TYPER or not (math.isfinite(hojd) and hojd > 0.0):
                continue
            geom_blob = rad.get("geom")
            if not isinstance(geom_blob, bytes | bytearray):
                continue
            koord, z_kron = _avkoda_gp_linestring(geom_blob)
            nyckel = (typ, hojd, koord, z_kron)
            if nyckel in sedda:
                continue
            sedda.add(nyckel)
            ut.append(
                _SkarmLinje(
                    skarm_id=f"bryt_{typ}_{len(ut) + 1}",
                    koordinater=koord,
                    hojd_m=hojd,
                    tjocklek_m=STANDARD_SKARM_TJOCKLEK_M,
                    reflektionsforlust_db=1.0,
                    z_kron_rh2000=z_kron,
                )
            )
    ut.sort(key=lambda s: (s.koordinater, s.hojd_m, s.skarm_id))
    return ut


def _validera_bbox_3011(ra: Any, nyckel: str, namn: str) -> list[float] | None:
    if ra is None:
        return None
    if not isinstance(ra, list | tuple) or len(ra) != 4:
        raise BullerK21Fel(
            f"{namn}: '{nyckel}' måste vara [e_min, n_min, e_max, n_max] i EPSG:3011."
        )
    ut: list[float] = []
    for v in ra:
        if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
            raise BullerK21Fel(f"{namn}: '{nyckel}' får bara innehålla ändliga tal, fick {ra!r}.")
        ut.append(float(v))
    if ut[0] >= ut[2] or ut[1] >= ut[3]:
        raise BullerK21Fel(
            f"{namn}: '{nyckel}' måste uppfylla e_min < e_max och n_min < n_max, fick {ra!r}."
        )
    return ut


def _valj_cellstorlek_m(
    rutor: Sequence[Any],
    begard_cell_m: float | None,
    max_rasterceller: int,
    *,
    utsnitt_3011: Sequence[float] | None = None,
) -> float:
    """Bestämmer rutnätsupplösning som heltalsmultipel av K1-rutornas pixelstorlek."""
    from karna.geometri import GeometriFel, las_terrang

    geos = []
    for r in rutor:
        try:
            _, geo = las_terrang(r.mapp)
        except GeometriFel as exc:
            raise BullerK21Fel(
                f"buller.nord2000: K1 v2-geometri saknas eller ogiltig: {exc}"
            ) from exc
        geos.append(geo)
    px = float(geos[0].pixelstorlek_x)
    for g in geos[1:]:
        if abs(float(g.pixelstorlek_x) - px) > 1.0e-6 or abs(float(g.pixelstorlek_y) - px) > 1.0e-6:
            raise BullerK21Fel("buller.nord2000: rutorna har olika eller icke-kvadratiska pixlar.")

    x_min = min(float(g.x_min) for g in geos)
    x_max = max(float(g.x_max) for g in geos)
    y_min = min(float(g.y_min) for g in geos)
    y_max = max(float(g.y_max) for g in geos)
    if utsnitt_3011 is not None:
        ux0, uy0, ux1, uy1 = (float(v) for v in utsnitt_3011)
        kx0 = max(x_min, ux0)
        kx1 = min(x_max, ux1)
        ky0 = max(y_min, uy0)
        ky1 = min(y_max, uy1)
        if kx1 <= kx0 or ky1 <= ky0:
            raise BullerK21Fel(
                "buller.nord2000: angivet 'utsnitt_3011' överlappar inte projektets K1-rutor."
            )
        x_min, x_max, y_min, y_max = kx0, kx1, ky0, ky1

    def _antal_celler(c: float) -> int:
        x0 = math.floor(x_min / c + 1.0e-6) * c
        x1 = math.ceil(x_max / c - 1.0e-6) * c
        y0 = math.floor(y_min / c + 1.0e-6) * c
        y1 = math.ceil(y_max / c - 1.0e-6) * c
        return max(1, round((x1 - x0) / c)) * max(1, round((y1 - y0) / c))

    if begard_cell_m is not None:
        faktor = round(begard_cell_m / px)
        if faktor < 1 or abs(begard_cell_m / px - faktor) > 1.0e-6:
            raise BullerK21Fel(
                f"buller.nord2000: raster_cell_m={begard_cell_m:g} måste vara en heltalsmultipel "
                f"av K1-rutans pixelstorlek {px:g} m."
            )
        n_celler = _antal_celler(begard_cell_m)
        if n_celler > max_rasterceller:
            raise BullerK21Fel(
                f"buller.nord2000: rutnätet blir {n_celler} celler vid raster_cell_m="
                f"{begard_cell_m:g} m, över max_rasterceller={max_rasterceller}. Öka "
                "raster_cell_m eller höj max_rasterceller."
            )
        return float(begard_cell_m)

    # Automatisk cellstorlek: exakt K1-pixel för små rutor (px >= 2 m och <= 400 celler),
    # annars minsta heltalsmultipel >= 4 m med högst min(400, max_rasterceller) celler.
    grans = min(STANDARD_AUTO_MAX_CELLER, max_rasterceller)
    if px >= 2.0 and _antal_celler(px) <= grans:
        return px
    f = max(1, math.ceil(4.0 / px - 1.0e-9))
    while _antal_celler(f * px) > grans:
        f += 1
    return float(f * px)


def _rensa_dubbelhorn(
    coords: Sequence[Sequence[float]], tol_m: float = 1.0e-7
) -> list[tuple[float, float]]:
    """Tar bort på varandra följande sammanfallande hörn (samt slutpunkt lika med startpunkt)."""
    ut: list[tuple[float, float]] = []
    for pt in coords:
        e, n = float(pt[0]), float(pt[1])
        if not ut or math.hypot(e - ut[-1][0], n - ut[-1][1]) > tol_m:
            ut.append((e, n))
    while len(ut) > 1 and math.hypot(ut[-1][0] - ut[0][0], ut[-1][1] - ut[0][1]) <= tol_m:
        ut.pop()
    return ut


def _markhojd_bilinjar_min(
    e_arr: NDArray[np.float64],
    n_arr: NDArray[np.float64],
    terrang_rh2000_m: NDArray[np.float64],
    e_min_m: float,
    n_max_m: float,
    cell_m: float,
) -> float | None:
    """Samma bilinjära markhöjdsinterpolation som `Bullerscen.markhojd_m` (för takhöjdskontroll)."""
    n_rad, n_kol = terrang_rh2000_m.shape
    e_max_m = e_min_m + n_kol * cell_m
    n_min_m = n_max_m - n_rad * cell_m
    inne = (e_arr >= e_min_m) & (e_arr <= e_max_m) & (n_arr >= n_min_m) & (n_arr <= n_max_m)
    if not bool(np.any(inne)):
        return None
    ei = e_arr[inne]
    ni = n_arr[inne]
    kol = np.clip((ei - e_min_m) / cell_m - 0.5, 0.0, n_kol - 1.0)
    rad = np.clip((n_max_m - ni) / cell_m - 0.5, 0.0, n_rad - 1.0)
    k0 = np.minimum(np.floor(kol).astype(np.int64), n_kol - 2)
    r0 = np.minimum(np.floor(rad).astype(np.int64), n_rad - 2)
    fk = kol - k0
    fr = rad - r0
    z = terrang_rh2000_m
    ovre = z[r0, k0] * (1.0 - fk) + z[r0, k0 + 1] * fk
    nedre = z[r0 + 1, k0] * (1.0 - fk) + z[r0 + 1, k0 + 1] * fk
    return float(np.min(ovre * (1.0 - fr) + nedre * fr))


def _skarm_till_byggnader(
    skarmar: Sequence[_SkarmLinje],
    terrang_rh2000_m: NDArray[np.float64],
    e_min_m: float,
    n_max_m: float,
    cell_m: float,
    befintliga_polygoner: Sequence[Any],
    befintliga_idn: frozenset[str] = frozenset(),
) -> tuple[list[Any], list[str]]:
    """Omvandlar bullerskärmslinjer till smala, hålfria `Byggnad`-hinder utan överlapp.

    Högre skärmar placeras före lägre så att akustisk skärmning bevaras vid korsande/parallella
    murar. Eventuella ring- eller hålpolygoner (t.ex. sluten gårdsmur runt ett hus eller annan mur)
    delas med `_polygon_utan_hal` så att hål aldrig fylls igen till överlappande polygoner.
    """
    import shapely

    from buller.typer import Byggnad

    n_rad, n_kol = terrang_rh2000_m.shape
    e_max_m = e_min_m + n_kol * cell_m
    n_min_m = n_max_m - n_rad * cell_m
    raster_box = shapely.box(
        e_min_m + _RASTER_KANT_MARGINAL_M,
        n_min_m + _RASTER_KANT_MARGINAL_M,
        e_max_m - _RASTER_KANT_MARGINAL_M,
        n_max_m - _RASTER_KANT_MARGINAL_M,
    )

    def _markhojd_punkt(e: float, n: float) -> float:
        kol = min(max(int(math.floor((e - e_min_m) / cell_m)), 0), n_kol - 1)
        rad = min(max(int(math.floor((n_max_m - n) / cell_m)), 0), n_rad - 1)
        return float(terrang_rh2000_m[rad, kol])

    # Högre skärm får företräde vid korsande eller tätt liggande murar.
    sorterade_skarmar = sorted(
        skarmar,
        key=lambda s: (
            -(max(s.z_kron_rh2000) if s.z_kron_rh2000 is not None else s.hojd_m),
            -s.hojd_m,
            s.skarm_id,
        ),
    )

    upptagna: list[Any] = list(befintliga_polygoner)
    anvanda_idn: set[str] = set(befintliga_idn)
    hinder: list[Any] = []
    varningar: list[str] = []

    for sk in sorterade_skarmar:
        linje = shapely.LineString(sk.koordinater).intersection(raster_box)
        if linje.is_empty or float(linje.length) <= 1.0e-3:
            varningar.append(
                f"Bullerskärm '{sk.skarm_id}' ligger utanför beräkningsområdet och hoppades över."
            )
            continue
        # Bevel-hörn förhindrar långa mitre-spetsar vid skarpa vinklar i kommunala murpolylinjer.
        buff = linje.buffer(sk.tjocklek_m * 0.5, cap_style="flat", join_style="bevel")
        if not buff.is_valid:
            buff = shapely.make_valid(buff)
        buff = buff.intersection(raster_box)
        if upptagna:
            union_upptagna = shapely.union_all(upptagna)
            # Liten marginal (1 cm) mot hus/murar för att undvika degenererade överlapp.
            buff = buff.difference(union_upptagna.buffer(0.01))
        if buff.is_empty:
            varningar.append(
                f"Bullerskärm '{sk.skarm_id}' överlappar helt befintliga hinder och hoppades över."
            )
            continue
        delar: list[Any] = []
        for g in shapely.get_parts(buff):
            if g.geom_type != "Polygon" or float(g.area) <= 1.0e-3:
                continue
            if not g.is_valid:
                g = shapely.make_valid(g)
            for del_g in shapely.get_parts(g):
                if del_g.geom_type != "Polygon" or float(del_g.area) <= 1.0e-3:
                    continue
                for utan_hal in _polygon_utan_hal(del_g):
                    if float(utan_hal.area) > 1.0e-3:
                        delar.append(utan_hal)
        if not delar:
            varningar.append(
                f"Bullerskärm '{sk.skarm_id}' överlappar helt befintliga hinder och hoppades över."
            )
            continue
        for del_idx, poly in enumerate(delar):
            coords = _rensa_dubbelhorn(list(poly.exterior.coords))
            if len(coords) < 3:
                continue
            ren_poly = shapely.Polygon(coords)
            if not ren_poly.is_valid or float(ren_poly.area) <= 1.0e-3:
                continue
            e_arr = np.asarray([c[0] for c in coords], dtype=np.float64)
            n_arr = np.asarray([c[1] for c in coords], dtype=np.float64)
            mark_max = max(_markhojd_punkt(e, n) for e, n in coords)
            bilinjar_min = _markhojd_bilinjar_min(
                e_arr, n_arr, terrang_rh2000_m, e_min_m, n_max_m, cell_m
            )
            min_tillatet_tak = (
                max(mark_max, bilinjar_min) + 0.05 if bilinjar_min is not None else mark_max + 0.05
            )
            if sk.z_kron_rh2000 is not None:
                tak_m = max(max(sk.z_kron_rh2000), min_tillatet_tak)
            else:
                tak_m = max(mark_max + sk.hojd_m, min_tillatet_tak)
            bid = sk.skarm_id if len(delar) == 1 else f"{sk.skarm_id}#{del_idx}"
            while bid in anvanda_idn:
                bid = f"{bid}_s"
            anvanda_idn.add(bid)
            hinder.append(
                Byggnad(
                    id=bid,
                    e_m=e_arr,
                    n_m=n_arr,
                    tak_rh2000_m=float(tak_m),
                    reflektionsforlust_db=float(sk.reflektionsforlust_db),
                )
            )
            upptagna.append(ren_poly)
    return hinder, varningar


_BYGGNAD_OVERLAPP_TOL_M2 = 1.0e-4
_BYGGNAD_KLIPP_TOL_M2 = 1.0e-5
_MIN_KLIPPT_BYGGNAD_AREA_M2 = 0.25
_MIN_KLIPPT_SKARM_AREA_M2 = 1.0e-3


def _polygon_utan_hal(poly: Any) -> list[Any]:
    """Delar en polygon med eventuella hål i hålfria polygoner (deterministisk lodrät skärning)."""
    import shapely
    from shapely.ops import split

    delar: list[Any] = [poly]
    for _ in range(len(poly.interiors) + 4):
        nya: list[Any] = []
        klar = True
        for d in delar:
            if not d.interiors:
                nya.append(d)
                continue
            klar = False
            hal = shapely.Polygon(d.interiors[0])
            x = float(hal.representative_point().x)
            _, y0, _, y1 = d.bounds
            snitt = split(d, shapely.LineString([(x, y0 - 1.0), (x, y1 + 1.0)]))
            splittade = [g for g in snitt.geoms if g.geom_type == "Polygon" and float(g.area) > 0.0]
            if not splittade:
                nya.append(shapely.Polygon(d.exterior))
            else:
                nya.extend(splittade)
        delar = nya
        if klar:
            break
    return [d if not d.interiors else shapely.Polygon(d.exterior) for d in delar]


def _rensa_overlappande_byggnader(
    byggnader: Sequence[Any],
    *,
    skarm_ids: frozenset[str] = frozenset(),
) -> tuple[list[Any], list[str]]:
    """Klipper överlappande hinderpolygoner (byggnader och bullerskärmar/murar).

    `Bullerscen` behåller sitt strikta överlappskrav (<= 1e-4 m²). I kommunala baskartor (bl.a.
    Göteborgs sluten kvartersstad) förekommer både 3D-takplan på olika höjdnivåer som överlappar
    i plan, millimetertunna väggöverlapp och buffrade murar (`bryt_mur_*`) intill täta hus. Här
    ges högre hinder företräde (bevarar akustisk skärmning); lägre överlappande delar klipps bort
    deterministiskt och redovisas öppet som varning. Om inga par överlappar > 1e-4 m² returneras
    listan oförändrad utan varning.
    """
    import shapely

    from buller.typer import Byggnad

    if len(byggnader) < 2:
        return list(byggnader), []

    polys = [shapely.Polygon(np.column_stack([b.e_m, b.n_m])) for b in byggnader]
    trad = shapely.STRtree(polys)
    par_i, par_j = trad.query(polys, predicate="intersects")
    har_overlapp = False
    for i_np, j_np in zip(par_i, par_j, strict=True):
        i, j = int(i_np), int(j_np)
        if i >= j:
            continue
        if float(polys[i].intersection(polys[j]).area) > _BYGGNAD_OVERLAPP_TOL_M2:
            har_overlapp = True
            break
    if not har_overlapp:
        return list(byggnader), []

    ordning = sorted(
        range(len(byggnader)),
        key=lambda i: (-float(byggnader[i].tak_rh2000_m), str(byggnader[i].id), i),
    )
    placerade_bygg: list[Any] = []
    placerade_poly: list[Any] = []
    placerade_bounds: list[tuple[float, float, float, float]] = []
    anvanda_idn: set[str] = set()
    antal_klippta = 0
    antal_borttagna = 0
    max_overlapp_m2 = 0.0

    for idx in ordning:
        b = byggnader[idx]
        p = polys[idx]
        if not p.is_valid:
            p = shapely.make_valid(p)
        min_area = (
            _MIN_KLIPPT_SKARM_AREA_M2
            if str(b.id) in skarm_ids
            else _MIN_KLIPPT_BYGGNAD_AREA_M2
        )
        bx0, by0, bx1, by1 = p.bounds
        traffar: list[Any] = []
        for q, qb in zip(placerade_poly, placerade_bounds, strict=True):
            if qb[2] < bx0 or qb[0] > bx1 or qb[3] < by0 or qb[1] > by1:
                continue
            if p.intersects(q) and float(p.intersection(q).area) > _BYGGNAD_KLIPP_TOL_M2:
                traffar.append(q)

        if not traffar:
            anvanda_idn.add(str(b.id))
            placerade_bygg.append(b)
            placerade_poly.append(p)
            placerade_bounds.append((bx0, by0, bx1, by1))
            continue

        union_traff = shapely.union_all(traffar)
        overlapp_area = float(p.intersection(union_traff).area)
        if overlapp_area > max_overlapp_m2:
            max_overlapp_m2 = overlapp_area

        rest = p.difference(union_traff)
        delar: list[Any] = []
        for g in shapely.get_parts(rest):
            if g.geom_type != "Polygon" or float(g.area) < min_area:
                continue
            if not g.is_valid:
                g = shapely.make_valid(g)
            for del_g in shapely.get_parts(g):
                if del_g.geom_type != "Polygon" or float(del_g.area) < min_area:
                    continue
                for utan_hal in _polygon_utan_hal(del_g):
                    if float(utan_hal.area) >= min_area:
                        delar.append(utan_hal)

        if not delar:
            antal_borttagna += 1
            continue

        antal_klippta += 1
        for del_nr, del_poly in enumerate(delar):
            coords = _rensa_dubbelhorn(list(del_poly.exterior.coords))
            if len(coords) < 3:
                continue
            ren_poly = shapely.Polygon(coords)
            if not ren_poly.is_valid or float(ren_poly.area) < min_area:
                continue
            e_arr = np.asarray([c[0] for c in coords], dtype=np.float64)
            n_arr = np.asarray([c[1] for c in coords], dtype=np.float64)
            bid = str(b.id) if len(delar) == 1 else f"{b.id}#klipp{del_nr}"
            while bid in anvanda_idn:
                bid = f"{bid}_k"
            anvanda_idn.add(bid)
            placerade_bygg.append(
                Byggnad(
                    id=bid,
                    e_m=e_arr,
                    n_m=n_arr,
                    tak_rh2000_m=float(b.tak_rh2000_m),
                    reflektionsforlust_db=float(b.reflektionsforlust_db),
                )
            )
            placerade_poly.append(ren_poly)
            placerade_bounds.append(ren_poly.bounds)

    varning = (
        f"Överlappande byggnadspolygoner i K1-underlaget (t.ex. sammansatta takplan i tät "
        f"kvartersstad, max {max_overlapp_m2:.1f} m²): {antal_klippta} klipptes och "
        f"{antal_borttagna} helt överlappade togs bort med företräde för högre takhöjd."
    )
    return placerade_bygg, [varning]


def _forbered_byggnader_for_raster(
    byggnader: Sequence[Any],
    terrang_rh2000_m: NDArray[np.float64],
    e_min_m: float,
    n_max_m: float,
    cell_m: float,
) -> tuple[list[Any], list[str]]:
    """Klipper byggnader som sticker ut över rasterkanten och utelämnar tak <= grov marknivå.

    I K1 v2 kan en byggnad som skär en 500 m-rutgräns sticka ut utanför det valda rastret (vilket
    annars ger `Profilextraktion: punkter ligger utanför terrängrastret` vid diffraktion/reflex).
    Vid omsampling till grövre bullerceller i kuperad terräng kan dessutom låga byggnader/däck i
    sluttning hamna under den bilinjärt interpolerade grova markytan. Båda hanteras deterministiskt
    med öppna varningar medan `Bullerscen` behåller sina strikta geometriska och akustiska krav.
    """
    import shapely

    from buller.typer import Byggnad

    n_rad, n_kol = terrang_rh2000_m.shape
    if n_rad < 2 or n_kol < 2 or not byggnader:
        return list(byggnader), []
    e_max_m = e_min_m + n_kol * cell_m
    n_min_m = n_max_m - n_rad * cell_m
    raster_box = shapely.box(
        e_min_m + _RASTER_KANT_MARGINAL_M,
        n_min_m + _RASTER_KANT_MARGINAL_M,
        e_max_m - _RASTER_KANT_MARGINAL_M,
        n_max_m - _RASTER_KANT_MARGINAL_M,
    )

    klippta_kant = 0
    kandidater: list[Any] = []
    for b in byggnader:
        p = shapely.Polygon(np.column_stack([b.e_m, b.n_m]))
        if raster_box.covers(p):
            kandidater.append(b)
            continue
        klippt = p.intersection(raster_box)
        delar = [
            u
            for g in shapely.get_parts(klippt)
            if g.geom_type == "Polygon" and float(g.area) >= _MIN_KLIPPT_BYGGNAD_AREA_M2
            for u in _polygon_utan_hal(g)
            if float(u.area) >= _MIN_KLIPPT_BYGGNAD_AREA_M2
        ]
        klippta_kant += 1
        for del_nr, del_poly in enumerate(delar):
            coords = list(del_poly.exterior.coords)[:-1]
            if len(coords) < 3:
                continue
            bid = str(b.id) if len(delar) == 1 else f"{b.id}#kant{del_nr}"
            kandidater.append(
                Byggnad(
                    id=bid,
                    e_m=np.asarray([c[0] for c in coords], dtype=np.float64),
                    n_m=np.asarray([c[1] for c in coords], dtype=np.float64),
                    tak_rh2000_m=float(b.tak_rh2000_m),
                    reflektionsforlust_db=float(b.reflektionsforlust_db),
                )
            )

    kvar: list[Any] = []
    utelamnade = 0
    z = terrang_rh2000_m
    for b in kandidater:
        e = np.asarray(b.e_m, dtype=np.float64)
        n = np.asarray(b.n_m, dtype=np.float64)
        inne = (e >= e_min_m) & (e <= e_max_m) & (n >= n_min_m) & (n <= n_max_m)
        if bool(np.any(inne)):
            ei = e[inne]
            ni = n[inne]
            kol = np.clip((ei - e_min_m) / cell_m - 0.5, 0.0, n_kol - 1.0)
            rad = np.clip((n_max_m - ni) / cell_m - 0.5, 0.0, n_rad - 1.0)
            k0 = np.minimum(np.floor(kol).astype(np.int64), n_kol - 2)
            r0 = np.minimum(np.floor(rad).astype(np.int64), n_rad - 2)
            fk = kol - k0
            fr = rad - r0
            ovre = z[r0, k0] * (1.0 - fk) + z[r0, k0 + 1] * fk
            nedre = z[r0 + 1, k0] * (1.0 - fk) + z[r0 + 1, k0 + 1] * fk
            mark_m = float(np.min(ovre * (1.0 - fr) + nedre * fr))
            if float(b.tak_rh2000_m) <= mark_m:
                utelamnade += 1
                continue
        kvar.append(b)

    varningar: list[str] = []
    if klippta_kant:
        varningar.append(
            f"{klippta_kant} byggnad(er) sträckte sig över beräkningsområdets ytterkant och "
            "klipptes till rastrets utbredning."
        )
    if utelamnade:
        varningar.append(
            f"{utelamnade} byggnad(er) i K1-underlaget hade takhöjd vid eller under det grova "
            f"bullerrastrets marknivå (cell {cell_m:g} m, t.ex. låg volym i sluttning) och "
            "utelämnades från bullerscenen."
        )
    return kvar, varningar


def _klipp_vag_till_raster(
    koord: Sequence[Sequence[float]],
    e_min_m: float,
    n_min_m: float,
    e_max_m: float,
    n_max_m: float,
) -> tuple[list[tuple[NDArray[np.float64], NDArray[np.float64]]], bool]:
    """Klipper en vägpolylinje till rastrets insida. Returnerar (delar, blev_klippt)."""
    import shapely

    linje = shapely.LineString([(float(p[0]), float(p[1])) for p in koord])
    box = shapely.box(
        e_min_m + _RASTER_KANT_MARGINAL_M,
        n_min_m + _RASTER_KANT_MARGINAL_M,
        e_max_m - _RASTER_KANT_MARGINAL_M,
        n_max_m - _RASTER_KANT_MARGINAL_M,
    )
    if box.covers(linje):
        arr = np.asarray(linje.coords, dtype=np.float64)
        return [(arr[:, 0], arr[:, 1])], False
    klippt = linje.intersection(box)
    if klippt.is_empty:
        return [], True
    delar: list[tuple[NDArray[np.float64], NDArray[np.float64]]] = []
    for del_g in shapely.get_parts(klippt):
        if del_g.geom_type == "LineString" and float(del_g.length) > 1.0e-3:
            arr = np.asarray(del_g.coords, dtype=np.float64)
            delar.append((arr[:, 0], arr[:, 1]))
    return delar, True


def _nord2000_ovre_grans(avstand_m: float) -> NDArray[np.float64]:
    """Konservativ övre gräns (dB) per tersband för en enskild bana i `Nord2000Karna`."""
    from buller.typer import ANTAL_BAND

    grans = (
        -10.0 * math.log10(4.0 * math.pi * avstand_m * avstand_m)
        + _NORD2000_OVRE_GRANS_MARGINAL_DB
    )
    return np.full(ANTAL_BAND, grans, dtype=np.float64)


class _Nord2000ForenkladKarna:
    """Adapter runt `Nord2000Karna` som tillämpar TC-101327 terrängförenkling för >12 punkter.

    Bevarar `nord2000_konform = False` direkt från den underliggande kärnan. Om en enskild bana
    faller utanför `Nord2000Karna`:s nuvarande delmodelltäckning (t.ex. Delmodell 7 vid brant
    takkant direkt ovanför en skärmad fasadmottagare) räknas den deterministiskt med
    `BaslinjeKarna` (Kurze–Anderson) och räknas i `antal_reservbanor` för tydlig flaggning.
    """

    def __init__(self) -> None:
        from buller.akustik import BaslinjeKarna
        from buller.nord2000 import Nord2000Karna

        self._karna = Nord2000Karna()
        self._reserv = BaslinjeKarna()
        if self._karna.nord2000_konform is not False or self._reserv.nord2000_konform is not False:
            raise BullerK21Fel(
                "Hård invariant bruten: nord2000_konform måste vara False för alla kärnor."
            )
        self.namn: str = self._karna.namn
        self.version: str = self._karna.version
        self.nord2000_konform: bool = self._karna.nord2000_konform
        self.antal_reservbanor: int = 0
        self.reserv_orsaker: list[str] = []

    def overforing_db(self, profil: Any, vader: Any) -> NDArray[np.float64]:
        from buller.nord2000.terrang_forenkling import REGLER_TC_101327, forenkla_terrang
        from buller.typer import BullerFel

        p = forenkla_terrang(profil, REGLER_TC_101327) if profil.x_m.shape[0] > 12 else profil
        try:
            return self._karna.overforing_db(p, vader)
        except (BullerFel, ValueError, ZeroDivisionError) as exc:
            self.antal_reservbanor += 1
            orsak = str(exc) or type(exc).__name__
            if orsak not in self.reserv_orsaker:
                self.reserv_orsaker.append(orsak)
            return self._reserv.overforing_db(p, vader)


def _flytta_bort_fran_kalla(
    e: float,
    n: float,
    kalla_e: NDArray[np.float64],
    kalla_n: NDArray[np.float64],
    scen: Any,
    polygoner: Sequence[Any],
) -> tuple[float, float]:
    """Säkerställer minst 5 cm horisontellt avstånd till närmaste källpunkt (undviker nollben)."""
    import shapely

    if kalla_e.size == 0:
        return e, n
    d2 = (kalla_e - e) ** 2 + (kalla_n - n) ** 2
    if float(np.min(d2)) >= _MIN_AVSTAND_KALLA_MOTTAGARE_M**2:
        return e, n
    steg = _MIN_AVSTAND_KALLA_MOTTAGARE_M
    for de, dn in ((steg, 0.0), (-steg, 0.0), (0.0, steg), (0.0, -steg), (steg, steg)):
        ce = min(
            max(e + de, scen.e_min_m + _RASTER_KANT_MARGINAL_M),
            scen.e_max_m - _RASTER_KANT_MARGINAL_M,
        )
        cn = min(
            max(n + dn, scen.n_min_m + _RASTER_KANT_MARGINAL_M),
            scen.n_max_m - _RASTER_KANT_MARGINAL_M,
        )
        if float(np.min((kalla_e - ce) ** 2 + (kalla_n - cn) ** 2)) < steg * steg * 0.5:
            continue
        pt = shapely.Point(ce, cn)
        if not any(poly.covers(pt) for poly in polygoner):
            return ce, cn
    return e, n


def _statistik_db(varden: NDArray[np.float64]) -> dict[str, float | None]:
    """Deterministiska nyckelvärden (max, aritmetisk/energimedel, andel över 55/60 dB(A))."""
    giltiga = varden[np.isfinite(varden)]
    if giltiga.size == 0:
        return {
            "max": None,
            "medel": None,
            "energimedel": None,
            "min": None,
            "over_55_pct": 0.0,
            "over_60_pct": 0.0,
        }
    topp = float(np.max(giltiga))
    energi_medel = topp + 10.0 * math.log10(
        float(np.mean(np.power(10.0, (giltiga - topp) / 10.0)))
    )
    return {
        "max": round(topp, 4),
        "medel": round(float(np.mean(giltiga)), 4),
        "energimedel": round(float(energi_medel), 4),
        "min": round(float(np.min(giltiga)), 4),
        "over_55_pct": round(100.0 * float(np.mean(giltiga > RIKTVARDE_LAG_DBA)), 4),
        "over_60_pct": round(100.0 * float(np.mean(giltiga > RIKTVARDE_HOG_DBA)), 4),
    }


class BullerNord2000(Analyslosare):
    """K21-skisslösare `buller.nord2000` för vägtrafikbuller (LAeq,24h i dB(A)) på K1 v2 + K9."""

    namn: ClassVar[str] = "buller.nord2000"
    doman: ClassVar[str] = "buller"
    version: ClassVar[str] = "1"
    beskrivning: ClassVar[str] = (
        "Vägtrafikbuller (LAeq,24h i dB(A), skiss) med Flodens Nord2000-motor på K1 v2-geometri "
        "och K9-scenarier. Kräver uttryckliga vagsegment; nord2000_konform=False och formella "
        "bullerrapporter är spärrade."
    )
    indataroller: ClassVar[tuple[str, ...]] = ()
    valfria_indataroller: ClassVar[tuple[str, ...]] = ()
    utdataroller: ClassVar[tuple[str, ...]] = (
        "ekvivalent_ljudniva",
        "fasad_buller",
        "buller_sammanfattning",
    )

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        okanda = sorted(set(parametrar) - _TILLATNA_PARAMETRAR)
        if okanda:
            raise BullerK21Fel(
                f"{self.namn}: okända parametrar {okanda}. Tillåtna: "
                f"{sorted(_TILLATNA_PARAMETRAR)}."
            )
        vagsegment = _validera_vagsegment(parametrar.get("vagsegment"), self.namn)
        mottagarhojd = _valfritt_tal(
            parametrar, "mottagarhojd_m", STANDARD_MOTTAGARHOJD_M, self.namn, over=0.0
        )
        raster_cell_ra = parametrar.get("raster_cell_m")
        raster_cell = (
            None
            if raster_cell_ra is None
            else _krav_tal(parametrar, "raster_cell_m", self.namn, over=0.0)
        )
        max_rasterceller = _valfritt_heltal(
            parametrar, "max_rasterceller", STANDARD_MAX_RASTERCELLER, self.namn, minst=1
        )
        segment_langd = _valfritt_tal(
            parametrar, "segment_langd_m", STANDARD_SEGMENT_LANGD_M, self.namn, over=0.0
        )
        fasad_steg = _valfritt_tal(
            parametrar, "fasad_steg_m", STANDARD_FASAD_STEG_M, self.namn, over=0.0
        )
        fasad_avstand = _valfritt_tal(
            parametrar, "fasad_avstand_m", STANDARD_FASAD_AVSTAND_M, self.namn, over=0.0
        )
        vaningshojd = _valfritt_tal(
            parametrar, "vaningshojd_m", STANDARD_VANINGSHOJD_M, self.namn, over=0.0
        )
        max_van_ra = parametrar.get("max_vaningar")
        max_vaningar = (
            None
            if max_van_ra is None
            else _valfritt_heltal(parametrar, "max_vaningar", 1, self.namn, minst=1)
        )
        max_fp_ra = parametrar.get("max_fasadpunkter")
        max_fasadpunkter = (
            None
            if max_fp_ra is None
            else _valfritt_heltal(parametrar, "max_fasadpunkter", 1, self.namn, minst=1)
        )
        utsnitt_3011 = _validera_bbox_3011(
            parametrar.get("utsnitt_3011"), "utsnitt_3011", self.namn
        )
        fasad_utsnitt_3011 = _validera_bbox_3011(
            parametrar.get("fasad_utsnitt_3011"), "fasad_utsnitt_3011", self.namn
        )
        reflexordning = _valfritt_heltal(
            parametrar, "reflexordning", STANDARD_REFLEXORDNING, self.namn, minst=0, hogst=1
        )
        karna = parametrar.get("karna", "nord2000")
        if karna not in ("nord2000", "baslinje"):
            raise BullerK21Fel(
                f"{self.namn}: 'karna' måste vara 'nord2000' eller 'baslinje', fick {karna!r}."
            )
        okand_klass = parametrar.get("okand_mark_klass")
        if okand_klass is not None and (
            not isinstance(okand_klass, str) or okand_klass not in _GILTIGA_IMPEDANSKLASSER
        ):
            raise BullerK21Fel(
                f"{self.namn}: 'okand_mark_klass' måste vara en av "
                f"{sorted(_GILTIGA_IMPEDANSKLASSER)}, fick {okand_klass!r}."
            )
        hal = parametrar.get("byggnader_med_hal", "dela")
        if hal not in ("fel", "dela", "yttre_ring"):
            raise BullerK21Fel(
                f"{self.namn}: 'byggnader_med_hal' måste vara 'fel', 'dela' eller 'yttre_ring', "
                f"fick {hal!r}."
            )
        land = parametrar.get("land")
        if land is not None and (not isinstance(land, str) or land not in _GILTIGA_LANDER):
            raise BullerK21Fel(
                f"{self.namn}: 'land' måste vara en av {sorted(_GILTIGA_LANDER)}, "
                f"fick {land!r}."
            )
        bullerskarmar = _validera_bullerskarmar(parametrar.get("bullerskarmar"), self.namn)
        return {
            "vagsegment": vagsegment,
            "mottagarhojd_m": mottagarhojd,
            "raster_cell_m": raster_cell,
            "max_rasterceller": max_rasterceller,
            "segment_langd_m": segment_langd,
            "fasad_steg_m": fasad_steg,
            "fasad_avstand_m": fasad_avstand,
            "vaningshojd_m": vaningshojd,
            "max_vaningar": max_vaningar,
            "max_fasadpunkter": max_fasadpunkter,
            "reflexordning": reflexordning,
            "karna": karna,
            "okand_mark_klass": okand_klass,
            "byggnader_med_hal": hal,
            "bullerskarmar": bullerskarmar,
            "land": land,
            "utsnitt_3011": utsnitt_3011,
            "fasad_utsnitt_3011": fasad_utsnitt_3011,
        }

    def paverkansradie_m(self, parametrar: Mapping[str, Any]) -> float | None:
        # Global över scenariots rutor: vägar, skärmar och 1:a ordningens reflexer kan spänna
        # över rutgränserna och [[jamforelser]] skriver ett gemensamt diff.tif.
        return None

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        import shapely

        from buller.akustik import BaslinjeKarna
        from buller.akustik.band import a_vagning_db
        from buller.banor.banfinnare import bygg_banuppsattning
        from buller.emission.modell import Trafikflode, Vagemission, Vagforhallanden
        from buller.lagen.korning import kor_buller
        from buller.lagen.profil import SKISSMARKNING, las_inbyggd_profil
        from buller.scen.bullerscen import Bullerscen, Vag
        from buller.scen.k1v2 import HalLage, _byggnader
        from buller.scen.kallsegment import dela_vagar
        from buller.typer import BullerFel, Kallsegment, Mottagare, Vader
        from gemensamt.cog import skriv_komprimerad_cog
        from karna.geometri import GeometriFel, fasadpunkter
        from karna.geometri.lasa import alla_byggnader
        from karna.geometri.mosaik import bygg_ytmosaik

        p = self.validera_parametrar(kontext.parametrar)
        rutor = kontext.rutor_att_lasa()
        if not rutor:
            raise BullerK21Fel(f"{self.namn}: scenariot saknar K1-rutor.")

        varningar: list[str] = [SKISSVARNING]

        # 1. Läs K1 v2-geometri (inkl. K9-ändrade byggnader och brytlinjeskärmar).
        utsnitt = p["utsnitt_3011"]
        cell_m = _valj_cellstorlek_m(
            rutor,
            p["raster_cell_m"],
            int(p["max_rasterceller"]),
            utsnitt_3011=utsnitt,
        )
        rut_mappar = [r.mapp for r in rutor]
        try:
            mos = bygg_ytmosaik(rut_mappar, cell_m)
            byggnads_poster = tuple(alla_byggnader(rut_mappar).values())
        except GeometriFel as exc:
            raise BullerK21Fel(f"{self.namn}: K1 v2-geometrin gick inte att läsa: {exc}") from exc

        varningar.extend(mos.varningar)
        if mos.saknade_celler:
            varningar.append(
                f"{mos.saknade_celler} rutnätsceller täcks inte av någon K1-ruta "
                "(markslag okänt, terräng medel)."
            )

        # Beskär mosaik och hinder till beräkningsutsnitt (`utsnitt_3011`) om begärt.
        mos_terrang = mos.terrang_rh2000_m
        mos_markslag = mos.markslag
        e_min_m = float(mos.x_min_m)
        n_max_m = float(mos.y_max_m)
        n_rad, n_kol = mos.form
        if utsnitt is not None:
            ux0, uy0, ux1, uy1 = (float(v) for v in utsnitt)
            c0 = max(0, min(n_kol - 2, int(math.floor((ux0 - e_min_m) / cell_m))))
            c1 = max(c0 + 2, min(n_kol, int(math.ceil((ux1 - e_min_m) / cell_m))))
            r0 = max(0, min(n_rad - 2, int(math.floor((n_max_m - uy1) / cell_m))))
            r1 = max(r0 + 2, min(n_rad, int(math.ceil((n_max_m - uy0) / cell_m))))
            if r0 > 0 or r1 < n_rad or c0 > 0 or c1 < n_kol:
                mos_terrang = np.ascontiguousarray(mos_terrang[r0:r1, c0:c1])
                mos_markslag = np.ascontiguousarray(mos_markslag[r0:r1, c0:c1])
                e_min_m = e_min_m + c0 * cell_m
                n_max_m = n_max_m - r0 * cell_m
                n_rad, n_kol = mos_terrang.shape
        e_max_m = e_min_m + n_kol * cell_m
        n_min_m = n_max_m - n_rad * cell_m

        if utsnitt is not None and byggnads_poster:
            filtrerade_bygg = []
            for bp in byggnads_poster:
                pe = [float(pt[0]) for pt in bp.polygon]
                pn = [float(pt[1]) for pt in bp.polygon]
                if max(pe) < e_min_m or min(pe) > e_max_m or max(pn) < n_min_m or min(pn) > n_max_m:
                    continue
                filtrerade_bygg.append(bp)
            byggnads_poster = tuple(filtrerade_bygg)

        hal_lage = cast(HalLage, str(p["byggnader_med_hal"]))
        try:
            riktiga_byggnader, _, bygg_varningar = _byggnader(byggnads_poster, hal_lage)
        except BullerFel as exc:
            raise BullerK21Fel(f"{self.namn}: {exc}") from exc
        varningar.extend(bygg_varningar)

        riktiga_byggnader, overlapp_varningar = _rensa_overlappande_byggnader(riktiga_byggnader)
        varningar.extend(overlapp_varningar)
        riktiga_byggnader, raster_bygg_varningar = _forbered_byggnader_for_raster(
            riktiga_byggnader,
            mos_terrang,
            e_min_m,
            n_max_m,
            cell_m,
        )
        varningar.extend(raster_bygg_varningar)

        byggnad_poly = [
            shapely.Polygon(np.column_stack([b.e_m, b.n_m])) for b in riktiga_byggnader
        ]

        # Bullerskärmar från K9/K1 brytlinjer.gpkg samt eventuella explicita parametrar.
        skarm_linjer = _las_brytlinje_skarmar(rutor)
        if utsnitt is not None and skarm_linjer:
            skarm_linjer = [
                sk
                for sk in skarm_linjer
                if max(pt[0] for pt in sk.koordinater) >= e_min_m
                and min(pt[0] for pt in sk.koordinater) <= e_max_m
                and max(pt[1] for pt in sk.koordinater) >= n_min_m
                and min(pt[1] for pt in sk.koordinater) <= n_max_m
            ]
        for sk_dict in p["bullerskarmar"]:
            skarm_linjer.append(
                _SkarmLinje(
                    skarm_id=str(sk_dict["id"]),
                    koordinater=tuple(
                        (float(c[0]), float(c[1])) for c in sk_dict["koordinater"]
                    ),
                    hojd_m=float(sk_dict["hojd_m"]),
                    tjocklek_m=float(sk_dict["tjocklek_m"]),
                    reflektionsforlust_db=float(sk_dict["reflektionsforlust_db"]),
                )
            )
        skarm_hinder, skarm_varningar = _skarm_till_byggnader(
            skarm_linjer,
            mos_terrang,
            e_min_m,
            n_max_m,
            cell_m,
            byggnad_poly,
            befintliga_idn=frozenset(str(b.id) for b in riktiga_byggnader),
        )
        varningar.extend(skarm_varningar)
        if skarm_hinder:
            skarm_id_set = frozenset(str(s.id) for s in skarm_hinder)
            alla_rensade, skarm_overlapp_varningar = _rensa_overlappande_byggnader(
                [*riktiga_byggnader, *skarm_hinder],
                skarm_ids=skarm_id_set,
            )
            varningar.extend(skarm_overlapp_varningar)
            riktiga_byggnader = [
                h for h in alla_rensade if str(h.id).split("#klipp", 1)[0] not in skarm_id_set
            ]
            skarm_hinder = [
                h for h in alla_rensade if str(h.id).split("#klipp", 1)[0] in skarm_id_set
            ]
            byggnad_poly = [
                shapely.Polygon(np.column_stack([b.e_m, b.n_m])) for b in riktiga_byggnader
            ]
            alla_hinder = tuple(alla_rensade)
        else:
            alla_hinder = tuple(riktiga_byggnader)

        # 2. Klipp vägar till rastret och bygg Bullerscen.
        vagar: list[Vag] = []
        vag_spec_per_nyckel: dict[tuple[str, int], dict[str, Any]] = {}
        for seg in p["vagsegment"]:
            delar, klippt = _klipp_vag_till_raster(
                seg["koordinater"], e_min_m, n_min_m, e_max_m, n_max_m
            )
            if not delar:
                raise BullerK21Fel(
                    f"{self.namn}: vägsegment '{seg['id']}' ligger helt utanför "
                    "beräkningsområdets utsträckning."
                )
            if klippt:
                varningar.append(
                    f"Vägsegment '{seg['id']}' sträcker sig utanför beräkningsområdet och "
                    "klipptes till rastrets utbredning."
                )
            for del_nr, (ve, vn) in enumerate(delar):
                vid = str(seg["id"]) if len(delar) == 1 else f"{seg['id']}#{del_nr}"
                kf = int(seg["korfalt"])
                vagar.append(Vag(id=vid, e_m=ve, n_m=vn, korfalt=kf))
                vag_spec_per_nyckel[(vid, kf)] = seg

        try:
            scen = Bullerscen(
                terrang_rh2000_m=mos_terrang,
                markslag=mos_markslag,
                e_min_m=e_min_m,
                n_max_m=n_max_m,
                cell_m=cell_m,
                byggnader=alla_hinder,
                vagar=tuple(vagar),
            )
        except BullerFel as exc:
            raise BullerK21Fel(f"{self.namn}: ogiltig bullerscen: {exc}") from exc

        okant_mark_andel = scen.andel_okant_markslag()
        if okant_mark_andel > 0.0 and p["okand_mark_klass"] is not None:
            varningar.append(
                f"{okant_mark_andel:.1%} av cellerna har okänt markslag (K1-kod 0) och fick "
                f"uttrycklig impedansklass '{p['okand_mark_klass']}'."
            )

        # 3. Emission (Nord2000 Road, SP Rapport 2006:12) och aktiva källsegment.
        emission = Vagemission()
        delkallhojder = emission.delkallhojder_m
        forhallanden = Vagforhallanden(land=p["land"]) if p["land"] is not None else None
        try:
            vagdelar = dela_vagar(scen.vagar, max_langd_m=float(p["segment_langd_m"]))
        except BullerFel as exc:
            raise BullerK21Fel(f"{self.namn}: {exc}") from exc

        aktiva_kallor: list[Kallsegment] = []
        lw_rader: list[NDArray[np.float64]] = []
        total_adt = sum(float(seg["adt_fordon_dygn"]) for seg in p["vagsegment"])
        if total_adt > 0.0:
            for vd in vagdelar:
                seg_spec = vag_spec_per_nyckel[(vd.vag_id, vd.korfalt)]
                adt = float(seg_spec["adt_fordon_dygn"])
                if adt <= 0.0:
                    continue
                hast = float(seg_spec["hastighet_km_h"])
                tung_andel = float(seg_spec["andel_tung_pct"]) / 100.0
                trafik: list[Trafikflode] = []
                n_latt = adt * (1.0 - tung_andel)
                n_tung = adt * tung_andel
                if n_latt > 0.0:
                    trafik.append(Trafikflode(1, n_latt, hast))
                if n_tung > 0.0:
                    trafik.append(Trafikflode(3, n_tung, hast))
                lw_seg = emission.ljudeffekt_db(
                    trafik,
                    vd.langd_m,
                    PERIOD_DYGN_S,
                    delkallhojder,
                    forhallanden,
                )
                for h_idx, hojd_m in enumerate(delkallhojder):
                    rad_db = lw_seg[h_idx, :]
                    if not bool(np.any(np.isfinite(rad_db))):
                        continue
                    aktiva_kallor.append(
                        Kallsegment(
                            id=len(aktiva_kallor),
                            e_m=vd.mitt_e_m,
                            n_m=vd.mitt_n_m,
                            hojd_over_mark_m=float(hojd_m),
                            langd_m=vd.langd_m,
                            vag_id=vd.vag_id,
                            korfalt=vd.korfalt,
                        )
                    )
                    lw_rader.append(rad_db)

        alla_poly = list(scen.polygoner)
        if aktiva_kallor and alla_poly:
            ke_all = np.asarray([k.e_m for k in aktiva_kallor], dtype=np.float64)
            kn_all = np.asarray([k.n_m for k in aktiva_kallor], dtype=np.float64)
            k_pts = shapely.points(ke_all, kn_all)
            traff_k, _ = scen.byggnadstrad.query(k_pts, predicate="intersects")
            if traff_k.size > 0:
                k0 = aktiva_kallor[int(traff_k[0])]
                raise BullerK21Fel(
                    f"{self.namn}: vägsegment '{k0.vag_id}' (E {k0.e_m:.2f}, N {k0.n_m:.2f}) "
                    "hamnar inuti eller på kanten av en byggnad eller bullerskärm."
                )

        # 4. Byggnadsmask och mottagare (raster + K11-fasadpunkter).
        e_kol = e_min_m + (np.arange(n_kol, dtype=np.float64) + 0.5) * cell_m
        n_rad_koord = n_max_m - (np.arange(n_rad, dtype=np.float64) + 0.5) * cell_m
        grid_e, grid_n = np.meshgrid(e_kol, n_rad_koord)
        grid_pts = shapely.points(grid_e.ravel(), grid_n.ravel())

        byggnad_mask = np.asarray(mos_markslag == 9, dtype=np.bool_)
        if byggnad_poly:
            bygg_trad = shapely.STRtree(byggnad_poly)
            traff_b, _ = bygg_trad.query(grid_pts, predicate="intersects")
            if traff_b.size > 0:
                b_flat = byggnad_mask.ravel()
                b_flat[traff_b] = True
                byggnad_mask = b_flat.reshape((n_rad, n_kol))

        hinder_mask = np.zeros((n_rad, n_kol), dtype=np.bool_)
        if skarm_hinder:
            skarm_poly = [shapely.Polygon(np.column_stack([s.e_m, s.n_m])) for s in skarm_hinder]
            skarm_trad = shapely.STRtree(skarm_poly)
            traff_s, _ = skarm_trad.query(grid_pts, predicate="intersects")
            if traff_s.size > 0:
                h_flat = hinder_mask.ravel()
                h_flat[traff_s] = True
                hinder_mask = h_flat.reshape((n_rad, n_kol)) & ~byggnad_mask

        kalla_e_arr = np.asarray([k.e_m for k in aktiva_kallor], dtype=np.float64)
        kalla_n_arr = np.asarray([k.n_m for k in aktiva_kallor], dtype=np.float64)

        mottagarhojd_m = float(p["mottagarhojd_m"])
        raster_mottagare: list[Mottagare] = []
        raster_rc: list[tuple[int, int]] = []
        for r in range(n_rad):
            for c in range(n_kol):
                if bool(byggnad_mask[r, c]) or bool(hinder_mask[r, c]):
                    continue
                e_eval, n_eval = _flytta_bort_fran_kalla(
                    float(e_kol[c]),
                    float(n_rad_koord[r]),
                    kalla_e_arr,
                    kalla_n_arr,
                    scen,
                    alla_poly,
                )
                raster_mottagare.append(
                    Mottagare(
                        id=r * n_kol + c,
                        e_m=e_eval,
                        n_m=n_eval,
                        hojd_over_mark_m=mottagarhojd_m,
                    )
                )
                raster_rc.append((r, c))

        fasad_mottagare: list[Mottagare] = []
        fasad_meta: list[_FasadMeta] = []
        if byggnads_poster:
            try:
                fp = fasadpunkter(
                    rut_mappar,
                    steg_m=float(p["fasad_steg_m"]),
                    vaningshojd_m=float(p["vaningshojd_m"]),
                    hojd_over_golv_m=STANDARD_FASAD_HOJD_OVER_GOLV_M,
                )
            except GeometriFel as exc:
                raise BullerK21Fel(
                    f"{self.namn}: K11-fasadpunkter kunde inte genereras: {exc}"
                ) from exc

            avst = float(p["fasad_avstand_m"])
            max_van: int | None = p["max_vaningar"]
            max_fp: int | None = p["max_fasadpunkter"]
            fp_utsnitt = p["fasad_utsnitt_3011"]
            tillatna_bygg_idn = frozenset(bp.byggnad_id for bp in byggnads_poster)
            fp_e = fp.e_m + fp.normal_e * avst
            fp_n = fp.n_m + fp.normal_n * avst
            inom = scen.inom_raster(fp_e, fp_n)
            if fp_utsnitt is not None:
                fx0, fy0, fx1, fy1 = (float(v) for v in fp_utsnitt)
                inom_fokus = (fp_e >= fx0) & (fp_e <= fx1) & (fp_n >= fy0) & (fp_n <= fy1)
            else:
                inom_fokus = np.ones(fp_e.shape, dtype=np.bool_)
            i_hinder = np.zeros(fp_e.shape, dtype=np.bool_)
            if alla_poly and bool((inom & inom_fokus).any()):
                in_idx = np.flatnonzero(inom & inom_fokus)
                traff_f, _ = scen.byggnadstrad.query(
                    shapely.points(fp_e[in_idx], fp_n[in_idx]), predicate="intersects"
                )
                if traff_f.size > 0:
                    i_hinder[in_idx[traff_f]] = True

            kandidat_rader: list[tuple[str, str, int, float, float, float, float, float]] = []
            hoppade_fasad = 0
            for idx_fp in range(len(fp)):
                bid_str = str(fp.byggnad_id[idx_fp])
                if utsnitt is not None and bid_str not in tillatna_bygg_idn:
                    continue
                if not bool(inom_fokus[idx_fp]):
                    continue
                van = int(fp.vaning[idx_fp])
                if max_van is not None and van > max_van:
                    continue
                if not bool(inom[idx_fp]) or bool(i_hinder[idx_fp]):
                    hoppade_fasad += 1
                    continue
                e_fp = float(fp_e[idx_fp])
                n_fp = float(fp_n[idx_fp])
                e_fp, n_fp = _flytta_bort_fran_kalla(
                    e_fp, n_fp, kalla_e_arr, kalla_n_arr, scen, alla_poly
                )
                kandidat_rader.append(
                    (
                        bid_str,
                        str(fp.fasadpunkt_id[idx_fp]),
                        van,
                        float(fp.hojd_over_mark_m[idx_fp]),
                        float(fp.z_rh2000[idx_fp]),
                        float(fp.normal_azimut_grader[idx_fp]),
                        e_fp,
                        n_fp,
                    )
                )
            if hoppade_fasad > 0:
                varningar.append(
                    f"{hoppade_fasad} K11-fasadpunkter hamnade utanför rastret eller i ett "
                    f"hinder efter {avst:g} m utflyttning och utelämnades."
                )
            if max_fp is not None and len(kandidat_rader) > max_fp:
                tot_kand = len(kandidat_rader)
                if kalla_e_arr.size > 0:
                    kandidat_rader.sort(
                        key=lambda rad: (
                            float(
                                np.min(
                                    (kalla_e_arr - rad[6]) ** 2 + (kalla_n_arr - rad[7]) ** 2
                                )
                            ),
                            rad[0],
                            rad[1],
                        )
                    )
                else:
                    kandidat_rader.sort(key=lambda rad: (rad[0], rad[1]))
                kandidat_rader = kandidat_rader[:max_fp]
                varningar.append(
                    f"Snabbskiss utvärderade de {max_fp} fasadpunkter som ligger närmast aktiva "
                    f"vägsegment (av {tot_kand} i beräkningsområdet); ange max_fasadpunkter eller "
                    "fasad_steg_m uttryckligen för full fasadupplösning."
                )
            kandidat_rader.sort(key=lambda rad: (rad[0], rad[1]))
            bas_id = n_rad * n_kol
            for offset, (bid, fid, van, h_mark, z_rh, norm_deg, e_fp, n_fp) in enumerate(
                kandidat_rader
            ):
                fasad_mottagare.append(
                    Mottagare(
                        id=bas_id + offset,
                        e_m=e_fp,
                        n_m=n_fp,
                        hojd_over_mark_m=h_mark,
                        byggnad_id=bid,
                        vaning=van,
                        fasadnormal_deg=norm_deg,
                    )
                )
                fasad_meta.append(
                    _FasadMeta(
                        fasadpunkt_id=fid,
                        byggnad_id=bid,
                        vaning=van,
                        hojd_over_mark_m=h_mark,
                        z_rh2000_m=z_rh,
                        fasadnormal_deg=norm_deg,
                        e_m=e_fp,
                        n_m=n_fp,
                    )
                )

        # 5. Kör utbredningsmotorn (Nord2000Karna eller BaslinjeKarna) i skissläge.
        karna_obj: Any
        ovre_grans_bana: Callable[[float], NDArray[np.float64]]
        if p["karna"] == "nord2000":
            karna_obj = _Nord2000ForenkladKarna()
            ovre_grans_bana = _nord2000_ovre_grans
        else:
            karna_obj = BaslinjeKarna()
            ovre_grans_bana = karna_obj.ovre_grans_db

        # Hård invariant: kärnan får aldrig hävda Nord2000-konformitet i denna skisslösare.
        if karna_obj.nord2000_konform is not False:
            raise BullerK21Fel(
                f"{self.namn}: otillåtet tillstånd – nord2000_konform måste vara False."
            )

        skiss_profil = las_inbyggd_profil("skiss")
        if int(p["reflexordning"]) != skiss_profil.reflexordning:
            skiss_profil = replace(skiss_profil, reflexordning=int(p["reflexordning"]))
        if skiss_profil.lage != "skiss" or skiss_profil.markning != SKISSMARKNING:
            raise BullerK21Fel(f"{self.namn}: endast skissläge är tillåtet.")

        alla_mottagare = [*raster_mottagare, *fasad_mottagare]
        n_raster_m = len(raster_mottagare)
        n_fasad_m = len(fasad_mottagare)

        raster_laeq = np.full((n_rad, n_kol), np.nan, dtype=np.float32)
        fasad_laeq = np.full(n_fasad_m, np.nan, dtype=np.float64)
        fasad_felgrans = np.zeros(n_fasad_m, dtype=np.float64)
        max_klustringsfel_db: float | None = 0.0
        antal_banor = 0

        if not aktiva_kallor:
            varningar.append(
                "Total trafikvolym är 0 fordon/dygn; ingen trafikbullerenergi att beräkna."
            )
            max_klustringsfel_db = None
        elif alla_mottagare:
            lw_db = np.vstack(lw_rader)
            vaderklasser = (Vader(),)
            try:
                banor = bygg_banuppsattning(
                    scen,
                    alla_mottagare,
                    aktiva_kallor,
                    maxradie_m=float(skiss_profil.maxradie_m or 2000.0),
                    reflexordning=skiss_profil.reflexordning,
                    egen_fasad_reflex=False,
                    steg_m=max(1.0, min(cell_m, 4.0)),
                    okand_klass=p["okand_mark_klass"],
                )
                antal_banor = len(banor.banor)
                if bool(banor.okant_markslag.any()):
                    n_okand_b = int(np.count_nonzero(banor.okant_markslag))
                    varningar.append(
                        f"{n_okand_b} utbredningsbanor passerar okänt markslag (K1-kod 0) "
                        f"med impedansklass '{p['okand_mark_klass']}'."
                    )
                korning = kor_buller(
                    mottagare=alla_mottagare,
                    kallor=aktiva_kallor,
                    lw_db=lw_db,
                    vaderklasser=vaderklasser,
                    parberaknare=banor.parberaknare(karna_obj, vaderklasser),
                    ovre_grans=banor.ovre_grans_par(ovre_grans_bana),
                    a_vagning_db=a_vagning_db(),
                    karna=karna_obj,
                    profil=skiss_profil,
                    reflexordning=skiss_profil.reflexordning,
                    antal_banor=antal_banor,
                )
            except BullerFel as exc:
                raise BullerK21Fel(f"{self.namn}: {exc}") from exc

            varningar.extend(korning.manifest.varningar)
            reserv_antal = int(getattr(karna_obj, "antal_reservbanor", 0))
            if reserv_antal > 0:
                orsaker = sorted(set(getattr(karna_obj, "reserv_orsaker", ())))
                varningar.append(
                    f"{reserv_antal} baneanrop låg utanför Nord2000-kärnans nuvarande "
                    "delmodelltäckning och räknades deterministiskt med BaslinjeKarna "
                    f"(Kurze–Anderson): {'; '.join(orsaker[:3])}."
                )
            niva_a = korning.resultat.niva_a_db
            fel_arr = korning.resultat.felgrans_db
            max_f = float(np.max(fel_arr)) if fel_arr.size else 0.0
            max_klustringsfel_db = round(max_f, 6) if math.isfinite(max_f) else None

            for idx_r, (r, c) in enumerate(raster_rc):
                v_db = float(niva_a[idx_r])
                if math.isfinite(v_db):
                    raster_laeq[r, c] = np.float32(v_db)
            for idx_f in range(n_fasad_m):
                v_db = float(niva_a[n_raster_m + idx_f])
                fasad_laeq[idx_f] = v_db if math.isfinite(v_db) else np.nan
                f_db = float(fel_arr[n_raster_m + idx_f])
                fasad_felgrans[idx_f] = f_db if math.isfinite(f_db) else np.nan

        # Garanti: byggnadsceller är alltid NaN i rastret.
        raster_laeq[byggnad_mask] = np.float32(np.nan)

        # 6. Skriv deterministiska utdata.
        skriv_komprimerad_cog(
            kontext.utmapp / "ekvivalent_ljudniva_dba.tif",
            raster_laeq,
            x_min=e_min_m,
            y_max=n_max_m,
            pixelstorlek=cell_m,
            epsg=3011,
            nodata=float("nan"),
        )

        fasad_features: list[dict[str, Any]] = []
        for idx_f, meta in enumerate(fasad_meta):
            laeq_val = float(fasad_laeq[idx_f])
            laeq_json = round(laeq_val, 4) if math.isfinite(laeq_val) else None
            fel_val = float(fasad_felgrans[idx_f])
            fel_json = round(fel_val, 4) if math.isfinite(fel_val) else None
            fasad_features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [round(meta.e_m, 4), round(meta.n_m, 4)],
                    },
                    "properties": {
                        "fasadpunkt_id": meta.fasadpunkt_id,
                        "byggnad_id": meta.byggnad_id,
                        "vaning": meta.vaning,
                        "hojd_over_mark_m": round(meta.hojd_over_mark_m, 4),
                        "z_rh2000_m": round(meta.z_rh2000_m, 4),
                        "fasadnormal_deg": round(meta.fasadnormal_deg, 4),
                        "laeq_24h_dba": laeq_json,
                        "lafmax_dba": None,
                        "felgrans_db": fel_json,
                        "over_55_dba": bool(laeq_json is not None and laeq_val > RIKTVARDE_LAG_DBA),
                        "over_60_dba": bool(laeq_json is not None and laeq_val > RIKTVARDE_HOG_DBA),
                    },
                }
            )
        fasad_fc: dict[str, Any] = {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::3011"}},
            "properties": {
                "format": FORMAT_BULLER_K21,
                "epsg": 3011,
                "hojdsystem": "RH 2000",
                "nord2000_konform": NORD2000_KONFORM,
                "metodstatus": METODSTATUS,
                "rapport_sparrad": RAPPORT_SPARRAD,
                "markning": SKISSMARKNING_K21,
            },
            "features": fasad_features,
        }
        (kontext.utmapp / "fasad_buller.geojson").write_text(
            json.dumps(fasad_fc, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        raster_stat = _statistik_db(np.asarray(raster_laeq, dtype=np.float64).ravel())
        fasad_stat = _statistik_db(fasad_laeq)
        unika_varningar = list(dict.fromkeys(varningar))

        sammanfattning: dict[str, Any] = {
            "format": FORMAT_BULLER_K21,
            "losare": self.namn,
            "version": self.version,
            "koordinatsystem": "EPSG:3011",
            "hojdsystem": "RH 2000",
            "nord2000_konform": NORD2000_KONFORM,
            "metodstatus": METODSTATUS,
            "rapport_sparrad": RAPPORT_SPARRAD,
            "markning": SKISSMARKNING_K21,
            "skissvarning": SKISSVARNING,
            "karna": {
                "namn": str(karna_obj.namn),
                "version": str(karna_obj.version),
                "nord2000_konform": bool(karna_obj.nord2000_konform),
            },
            "laeq_max_dba": raster_stat["max"],
            "laeq_medel_dba": raster_stat["medel"],
            "laeq_energimedel_dba": raster_stat["energimedel"],
            "laeq_min_dba": raster_stat["min"],
            "andel_over_55_dba_pct": raster_stat["over_55_pct"],
            "andel_over_60_dba_pct": raster_stat["over_60_pct"],
            "antal_fasadpunkter": n_fasad_m,
            "fasad_laeq_max_dba": fasad_stat["max"],
            "fasad_laeq_medel_dba": fasad_stat["medel"],
            "fasad_andel_over_55_dba_pct": fasad_stat["over_55_pct"] if n_fasad_m > 0 else None,
            "fasad_andel_over_60_dba_pct": fasad_stat["over_60_pct"] if n_fasad_m > 0 else None,
            "lafmax_notering": (
                "LAFmax beräknas inte: maximalnivå enligt SP Rapport 2006:12 avsnitt 2.6 "
                "är ännu inte införd i källmodellen."
            ),
            "mottagarhojd_m": mottagarhojd_m,
            "raster_cell_m": cell_m,
            "reflexordning": int(skiss_profil.reflexordning),
            "antal_giltiga_rasterceller": int(np.count_nonzero(np.isfinite(raster_laeq))),
            "antal_maskade_byggnadsceller": int(np.count_nonzero(byggnad_mask)),
            "antal_vagsegment": len(p["vagsegment"]),
            "antal_kallsegment": len(aktiva_kallor),
            "antal_banor": antal_banor,
            "antal_byggnader": len(byggnads_poster),
            "antal_bullerskarmar": len(skarm_hinder),
            "antal_baslinje_reservbanor": int(getattr(karna_obj, "antal_reservbanor", 0)),
            "garanterad_klustringsfelgrans_max_db": max_klustringsfel_db,
            "varningar": unika_varningar,
        }
        (kontext.utmapp / "buller_sammanfattning.json").write_text(
            json.dumps(sammanfattning, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        matvarden: dict[str, Any] = {
            "laeq_max_dba": raster_stat["max"],
            "laeq_medel_dba": raster_stat["medel"],
            "laeq_energimedel_dba": raster_stat["energimedel"],
            "andel_over_55_dba_pct": raster_stat["over_55_pct"],
            "andel_over_60_dba_pct": raster_stat["over_60_pct"],
            "antal_fasadpunkter": n_fasad_m,
            "fasad_laeq_max_dba": fasad_stat["max"],
            "nord2000_konform": NORD2000_KONFORM,
            "metodstatus": METODSTATUS,
            "rapport_sparrad": RAPPORT_SPARRAD,
            "karna_namn": str(karna_obj.namn),
            "karna_version": str(karna_obj.version),
            "mottagarhojd_m": mottagarhojd_m,
            "raster_cell_m": cell_m,
            "antal_giltiga_rasterceller": int(np.count_nonzero(np.isfinite(raster_laeq))),
            "antal_maskade_byggnadsceller": int(np.count_nonzero(byggnad_mask)),
            "antal_vagsegment": len(p["vagsegment"]),
            "antal_kallsegment": len(aktiva_kallor),
            "antal_byggnader": len(byggnads_poster),
            "antal_bullerskarmar": len(skarm_hinder),
            "antal_baslinje_reservbanor": int(getattr(karna_obj, "antal_reservbanor", 0)),
            "garanterad_klustringsfelgrans_max_db": max_klustringsfel_db,
        }
        return LosarUtdata(
            filer={
                "ekvivalent_ljudniva": "ekvivalent_ljudniva_dba.tif",
                "fasad_buller": "fasad_buller.geojson",
                "buller_sammanfattning": "buller_sammanfattning.json",
            },
            matvarden=matvarden,
            varningar=tuple(unika_varningar),
        )


LOSARE: list[Analyslosare] = [BullerNord2000()]
