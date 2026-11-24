# ruff: noqa: E501
"""Valfritt lager för inventerade träd som punkter: noder även på öppen mark (K47 `[[valfritt_lager]]`).

Inläsning ur GeoPackage, Shapefile eller GeoJSON (eller det lagrade lagret `tradpunkter.csv` i en geografi,
K46) med fältnamn och urval ur nätverkets konfiguration. **Dataminimering:** bara punkten (EPSG:3011),
trädslaget och stamomkretsen/diametern (cm) behålls. Filen läses med en kolumnlista (`columns=`), så andra
attribut (adress, fastighet, inventerarens namn) läses aldrig in och sparas aldrig. Geometrier som inte är
punkter avvisas.

Ett träd blir en nod så här (`lagg_tradnoder`): en skiva med radien `buffert_m` (Nacka buffrade punktträd 20 m)
läggs i klassrastret som nätverkets trädklass (NMD 116, ädellöv), utom på celler i `ej_pa_klasser` (byggnad,
annan exploaterad yta, väg, vatten). Cellen som innehåller punkten tas alltid med om den får bli nod. Sammanhängande
skivor och skog i rastret bildar en fläck. Fläckar som rör en skiva undantas från minsta area om konfigurationen
säger det (`undantar_min_area`, ett Flodenantagande). Ett nytt hus (NMD 51) ovanpå ett träd tar bort cellerna.
"""

from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from ekologi.natverk import ENHETER_LANGD, TradFalt, TradFilter, TradNod
from ekologi.typer import EkologiFel
from gemensamt.hash import sha256_fil

EPSG_ANALYS = 3011
TRADLAGER_FORMAT = "floden-tradpunkter/v1"
TRADLAGER_FIL = "tradpunkter.csv"
TRADLAGER_KALLA = "Inventerade träd (fil som användaren själv hämtat; bara punkt, trädslag och stamomfång sparas)"
KOLUMNER = ("e_m", "n_m", "tradslag", "stamomkrets_cm", "diameter_cm")
_FORMAT_ENDAST_GEOMETRI = {".gpkg", ".shp", ".geojson", ".json"}


class TradFel(EkologiFel):
    """Trädfilen går inte att läsa eller uppfyller inte kraven (tydligt fel, aldrig tyst)."""


@dataclass(frozen=True)
class Tradpunkt:
    """Det enda som sparas om ett träd."""

    e_m: float
    n_m: float
    tradslag: str
    stamomkrets_cm: float | None = None
    diameter_cm: float | None = None


@dataclass(frozen=True)
class TradInlasning:
    punkter: tuple[Tradpunkt, ...]
    antal_i_filen: int
    antal_avvisade: int
    """Rader utan giltig punkt eller med koordinater som inte är ändliga."""
    varningar: tuple[str, ...] = ()
    fil_sha256: str | None = None


@dataclass
class TradUrval:
    punkter: tuple[Tradpunkt, ...]
    antal_fore: int
    bortfiltrerade_tradslag: int = 0
    bortfiltrerade_matt: int = 0
    saknar_matt: int = 0
    varningar: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ inläsning


def _tal(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(str(v).replace(",", ".").strip()) if isinstance(v, str) else float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 0.0 else None


def _hitta(kolumner: list[str], kandidater: tuple[str, ...]) -> str | None:
    lag = {k.lower(): k for k in kolumner}
    for kand in kandidater:
        if kand.lower() in lag:
            return lag[kand.lower()]
    return None


def las_tradpunkter(
    sokvag: str | Path, falt: TradFalt, *, lagernamn: str | None = None
) -> TradInlasning:
    """Läser trädpunkter ur GeoPackage, Shapefile eller GeoJSON, omprojicerade till EPSG:3011.

    Bara kolumnerna för trädslag, stamomkrets och diameter (enligt `falt`) läses. Saknas fältet för trädslag
    är det ett fel (urvalet på trädslag kräver det); saknas mått går trädet ändå in utan mått.
    """
    import geopandas as gpd
    import pyogrio

    fil = Path(sokvag)
    if not fil.is_file():
        raise TradFel(f"Trädfilen {fil.name} finns inte.")
    if fil.suffix.lower() not in _FORMAT_ENDAST_GEOMETRI:
        raise TradFel(f"Trädfilen {fil.name}: format som inte stöds (GeoPackage, Shapefile eller GeoJSON).")
    try:
        info = pyogrio.read_info(fil, layer=lagernamn)
        namn = [str(f) for f in info["fields"]]
        c_slag = _hitta(namn, falt.tradslag)
        c_omkr = _hitta(namn, falt.stamomkrets) if falt.stamomkrets else None
        c_diam = _hitta(namn, falt.diameter) if falt.diameter else None
        if c_slag is None:
            raise TradFel(
                f"Trädfilen {fil.name} har inget fält för trädslag (sökte {list(falt.tradslag)}; filens fält: {sorted(namn)}). "
                "Fältnamnen anges i nätverkets [valfritt_lager.falt]."
            )
        kolumner = [c for c in (c_slag, c_omkr, c_diam) if c is not None]
        gdf = gpd.read_file(fil, layer=lagernamn, columns=kolumner, engine="pyogrio")
    except TradFel:
        raise
    except Exception as exc:  # noqa: BLE001 - pyogrio ger många feltyper
        raise TradFel(f"Trädfilen {fil.name} går inte att läsa: {exc}") from exc
    if gdf.crs is None:
        raise TradFel(f"Trädfilen {fil.name} saknar koordinatsystem.")
    gdf = gdf.to_crs(EPSG_ANALYS)
    antal = int(len(gdf))
    varningar: list[str] = []
    if c_omkr is None and c_diam is None:
        varningar.append("Trädfilen saknar fält för stamomkrets och diameter: träden saknar mått.")
    ut: list[Tradpunkt] = []
    avvisade = 0
    icke_punkt = 0
    kol = {c: list(gdf[c]) for c in kolumner}
    for i, g in enumerate(gdf.geometry):
        if g is None or g.is_empty:
            avvisade += 1
            continue
        if g.geom_type != "Point":
            icke_punkt += 1
            avvisade += 1
            continue
        if not (math.isfinite(g.x) and math.isfinite(g.y)):
            avvisade += 1
            continue
        slag = str(kol[c_slag][i]).strip()
        ut.append(
            Tradpunkt(
                round(float(g.x), 3),
                round(float(g.y), 3),
                "" if slag.lower() in ("nan", "none") else slag,
                _matt(kol.get(c_omkr or "", [None] * antal)[i], falt.stamomkrets_enhet),
                _matt(kol.get(c_diam or "", [None] * antal)[i], falt.diameter_enhet),
            )
        )
    if icke_punkt:
        varningar.append(f"{icke_punkt} objekt i trädfilen är inte punkter och lästes inte.")
    return TradInlasning(tuple(ut), antal, avvisade, tuple(varningar), sha256_fil(fil))


def _matt(varde: Any, enhet: str) -> float | None:
    v = _tal(varde)
    return None if v is None else round(v * ENHETER_LANGD[enhet], 3)


# ------------------------------------------------------------------ lagring (geografins lager tradpunkter)


def skriv_tradlager(punkter: tuple[Tradpunkt, ...] | list[Tradpunkt], ut_fil: str | Path, *, antal_i_filen: int | None = None, kallfil_sha256: str | None = None) -> Path:
    """Skriver `tradpunkter.csv` (deterministisk ordning) och `tradpunkter.json`. Inga andra uppgifter än de fem kolumnerna."""
    fil = Path(ut_fil)
    fil.parent.mkdir(parents=True, exist_ok=True)
    rader = sorted(punkter, key=lambda p: (p.e_m, p.n_m, p.tradslag, p.stamomkrets_cm or 0.0, p.diameter_cm or 0.0))
    with fil.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(KOLUMNER)
        for p in rader:
            w.writerow(
                [f"{p.e_m:.3f}", f"{p.n_m:.3f}", p.tradslag,
                 "" if p.stamomkrets_cm is None else f"{p.stamomkrets_cm:.3f}",
                 "" if p.diameter_cm is None else f"{p.diameter_cm:.3f}"]
            )
    meta = {
        "format": TRADLAGER_FORMAT,
        "lager": "inventerade träd (punkter)",
        "kalla": TRADLAGER_KALLA,
        "epsg": EPSG_ANALYS,
        "antal_punkter": len(rader),
        "antal_i_filen": antal_i_filen,
        "kallfil_sha256": kallfil_sha256,
        "sha256": sha256_fil(fil),
        "sparade_falt": list(KOLUMNER),
    }
    fil.with_suffix(".json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    return fil


def las_tradlager(sokvag: str | Path) -> TradInlasning:
    """Läser `tradpunkter.csv` (lagret i en geografi, eller en CSV med samma fem kolumner som indata)."""
    fil = Path(sokvag)
    try:
        with fil.open(encoding="utf-8", newline="") as fh:
            lasare = csv.DictReader(fh)
            if tuple(lasare.fieldnames or ()) != KOLUMNER:
                raise TradFel(f"{fil.name}: kolumnerna ska vara exakt {list(KOLUMNER)}.")
            ut: list[Tradpunkt] = []
            for nr, r in enumerate(lasare, start=2):
                e, n = _tal(r["e_m"]), _tal(r["n_m"])
                if e is None or n is None:
                    raise TradFel(f"{fil.name} rad {nr}: koordinaterna ska vara ändliga tal.")
                ut.append(Tradpunkt(e, n, str(r["tradslag"]), _tal(r["stamomkrets_cm"]), _tal(r["diameter_cm"])))
    except OSError as exc:
        raise TradFel(f"Trädlagret {fil.name} går inte att läsa: {exc.strerror}.") from exc
    return TradInlasning(tuple(ut), len(ut), 0, (), sha256_fil(fil))


def klipp_punkter(punkter: tuple[Tradpunkt, ...], utsnitt: tuple[float, float, float, float]) -> tuple[Tradpunkt, ...]:
    """Punkterna inom `utsnitt` (e_min, n_min, e_max, n_max), kanterna inkluderade."""
    e0, n0, e1, n1 = utsnitt
    return tuple(p for p in punkter if e0 <= p.e_m <= e1 and n0 <= p.n_m <= n1)


# ------------------------------------------------------------------ urval


def _ord(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-zåäöéü]+", text.lower()) if t}


def filtrera_trad(punkter: tuple[Tradpunkt, ...], filt: TradFilter) -> TradUrval:
    """Urval på trädslag (helt namn eller ett ord i namnet, skiftlägesokänsligt) och minsta stamomfång/diameter.

    Är en gräns satt och trädet saknar måttet (stamomkretsen kan ersättas av pi * diameter och tvärtom)
    sorteras det bort och räknas i `saknar_matt`: ett tyst antagande om måttet görs inte.
    """
    ok = {t.lower() for t in filt.tradslag}
    ut: list[Tradpunkt] = []
    u = TradUrval((), len(punkter))
    for p in punkter:
        if ok and not (p.tradslag.lower() in ok or _ord(p.tradslag) & ok):
            u.bortfiltrerade_tradslag += 1
            continue
        omkrets = p.stamomkrets_cm if p.stamomkrets_cm is not None else (math.pi * p.diameter_cm if p.diameter_cm is not None else None)
        diam = p.diameter_cm if p.diameter_cm is not None else (p.stamomkrets_cm / math.pi if p.stamomkrets_cm is not None else None)
        for grans, varde in ((filt.min_stamomkrets_cm, omkrets), (filt.min_diameter_cm, diam)):
            if grans is None:
                continue
            if varde is None:
                u.saknar_matt += 1
                break
            if varde < grans:
                u.bortfiltrerade_matt += 1
                break
        else:
            ut.append(p)
    u.punkter = tuple(ut)
    if u.saknar_matt:
        u.varningar.append(
            f"{u.saknar_matt} träd saknar stamomfång och diameter och sorterades bort av måttgränsen i urvalet."
        )
    return u


# ------------------------------------------------------------------ noder i rastret


@dataclass(frozen=True)
class TradNoder:
    klasser: NDArray[np.uint8]
    """Klassrastret med trädskivorna inlagda."""
    undantag_min_area: NDArray[np.bool_]
    """Celler vars fläck undantas från minsta area (skivornas celler)."""
    antal_trad: int
    antal_trad_i_rastret: int
    antal_utan_nod: int
    """Träd vars skiva bara täcker celler där noder inte får ligga (byggnad, väg, vatten) eller ligger utanför rastret."""
    antal_celler_nod: int
    antal_celler_andrade: int
    """Celler som bytte klass (var inte redan trädklass)."""


def lagg_tradnoder(
    klasser: NDArray[np.uint8] | NDArray[np.integer[Any]],
    punkter: tuple[Tradpunkt, ...],
    *,
    x_min_m: float,
    y_max_m: float,
    cell_m: float,
    nod: TradNod,
    forbjudna_extra: tuple[int, ...] = (),
) -> TradNoder:
    """Lägger trädskivorna i klassrastret (se modulens text). Ändrar inte indata."""
    if not (math.isfinite(cell_m) and cell_m > 0.0):
        raise TradFel(f"cell_m måste vara ändligt och > 0, fick {cell_m}.")
    ut = np.array(klasser, dtype=np.uint8, copy=True)
    ny, nx = ut.shape
    forbjuden = np.isin(klasser, [*nod.ej_pa_klasser, *forbjudna_extra])
    skiva = np.zeros((ny, nx), dtype=bool)
    i_rastret = 0
    utan_nod = 0
    r = nod.buffert_m
    for p in punkter:
        kol_p = (p.e_m - x_min_m) / cell_m
        rad_p = (y_max_m - p.n_m) / cell_m
        if not (0.0 <= kol_p < nx and 0.0 <= rad_p < ny):
            utan_nod += 1
            continue
        i_rastret += 1
        k0 = max(0, int(math.floor(kol_p - r / cell_m)) - 1)
        k1 = min(nx, int(math.ceil(kol_p + r / cell_m)) + 2)
        r0 = max(0, int(math.floor(rad_p - r / cell_m)) - 1)
        r1 = min(ny, int(math.ceil(rad_p + r / cell_m)) + 2)
        ee = x_min_m + (np.arange(k0, k1) + 0.5) * cell_m
        nn = y_max_m - (np.arange(r0, r1) + 0.5) * cell_m
        d2 = (ee[None, :] - p.e_m) ** 2 + (nn[:, None] - p.n_m) ** 2
        lokal = d2 <= r * r
        lokal[int(rad_p) - r0, int(kol_p) - k0] = True  # cellen med punkten tas alltid med (om den får bli nod)
        lokal &= ~forbjuden[r0:r1, k0:k1]
        if not lokal.any():
            utan_nod += 1
            continue
        skiva[r0:r1, k0:k1] |= lokal
    andrade = skiva & (ut != nod.klass)
    ut[skiva] = np.uint8(nod.klass)
    return TradNoder(
        klasser=ut,
        undantag_min_area=skiva if nod.undantar_min_area else np.zeros((ny, nx), dtype=bool),
        antal_trad=len(punkter),
        antal_trad_i_rastret=i_rastret,
        antal_utan_nod=utan_nod,
        antal_celler_nod=int(skiva.sum()),
        antal_celler_andrade=int(andrade.sum()),
    )
