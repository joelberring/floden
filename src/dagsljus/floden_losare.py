"""Dagsljuslösare enligt K21 (`docs/ANSLUTA_LOSARE.md`): upptäcks automatiskt av kärnan.

- `dagsljus.horisont`: horisontprofiler per cell (uint16, 0,01 grader) för den egna rutan.
- `dagsljus.soltimmar_mark`: soltimmar per dygn (medel över valda datum) för markceller.
- `dagsljus.svf_mark`: himmelsexponering (SVF) för horisontella ytor.
- `dagsljus.vsc_fasad`: VSC (BRE, mulen CIE-himmel) för fasadpunkter: K11 ur byggnadsregistret
  (standard) eller en CSV-fil.

Höjdfältet är ytmodellen (DSM) ur K1 v2 via `karna.geometri.las_ytmodell`; K1 v1-rutor vägras
(fast hinderhöjd). Byggnader med gissad höjd (`hojdkalla = standard`) ger varning och flaggade
resultat. Alla lösare är rutvisa med påverkansradien H_max / tan(h_min) (konservativ). Strålar
går högst så långt, så hinder som skymmer mindre än h_min bortom radien ingår inte; se
`docs/DAGSLJUS.md`.
"""

from __future__ import annotations

import csv
import gc
import hashlib
import json
import math
import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
from numpy.typing import NDArray

from dagsljus.horisont import (
    Hojdfalt,
    horisont_mipmap,
    horisont_riktning_ruta,
    horisont_riktning_ruta_pa_enhet,
    riktningar_rad,
    uppdatera_horisont_inkrementellt,
)
from dagsljus.matt import (
    SolsynlighetAckumulator,
    SoltimmarAckumulator,
    halvplansriktningar,
    sammanhangande_soltimmar_fran_synlighet,
    solsynlighet_fasad,
    tidsfonster_mask,
    vsc_fran_halvplansprofil,
)
from dagsljus.ruta import (
    Mosaik,
    RutaFel,
    bygg_mosaik,
    fonster_koordinater,
    mitt_3011,
    uppskatta_mosaik_form,
)
from dagsljus.solposition import dygnsfonster_utc, lat_lon_fran_3011, solposition
from gemensamt.gpu import ar_gpu_backend, berakningsplats, hamta_backend, till_enhet
from karna.analyslosare import Analyslosare, LosarFel, LosarKontext, LosarUtdata
from karna.paverkan import dagsljus_paverkansradie_m

_GEMENSAMMA = {
    "azimutriktningar",
    "hojd_max_m",
    "h_min_grader",
    "cellstorlek_m",
    "omsampling",
    "vegetation",
    "enhet",
}
STANDARD_K = 64
"""Standard för azimutriktningar (horisont, SVF, VSC). Soltimmar har högre standard, se nedan."""
STANDARD_K_SOLTIMMAR = 180
"""Mätt mot strålgång i `docs/DAGSLJUS.md` avsnitt 6: K = 64 räcker inte för skuggstudier."""
_OMSAMPLING = ("medel", "max", "punkt")
_VSC_CHUNK_STRALAR = 200_000


def _tal(p: Mapping[str, Any], namn: str, standard: float, *, over: float | None = None) -> float:
    v = p.get(namn, standard)
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
        raise LosarFel(f"Parametern '{namn}' måste vara ett ändligt tal, fick {v!r}.")
    if over is not None and float(v) <= over:
        raise LosarFel(f"Parametern '{namn}' måste vara > {over}, fick {v}.")
    return float(v)


def _heltal(p: Mapping[str, Any], namn: str, standard: int, *, minst: int) -> int:
    v = p.get(namn, standard)
    if isinstance(v, bool) or not isinstance(v, int | float) or float(v) != int(v):
        raise LosarFel(f"Parametern '{namn}' måste vara ett heltal, fick {v!r}.")
    if int(v) < minst:
        raise LosarFel(f"Parametern '{namn}' måste vara >= {minst}, fick {v}.")
    return int(v)


def _gemensamma(p: Mapping[str, Any], standard_k: int = STANDARD_K) -> dict[str, Any]:
    h_min = _tal(p, "h_min_grader", 5.0, over=0.0)
    if h_min >= 90.0:
        raise LosarFel(f"h_min_grader måste vara < 90, fick {h_min}.")
    oms = p.get("omsampling", "medel")
    if oms not in _OMSAMPLING:
        raise LosarFel(f"omsampling måste vara en av {_OMSAMPLING}, fick {oms!r}.")
    veg = p.get("vegetation", False)
    if not isinstance(veg, bool):
        raise LosarFel(f"vegetation måste vara true/false, fick {veg!r}.")
    enhet = p.get("enhet", "cpu")
    if enhet not in ("cpu", "cuda", "auto"):
        raise LosarFel(f"enhet måste vara 'cpu', 'cuda' eller 'auto', fick {enhet!r}.")
    return {
        "azimutriktningar": _heltal(p, "azimutriktningar", standard_k, minst=8),
        "hojd_max_m": _tal(p, "hojd_max_m", 60.0, over=0.0),
        "h_min_grader": h_min,
        "cellstorlek_m": _tal(p, "cellstorlek_m", 1.0, over=0.0),
        "omsampling": oms,
        "vegetation": veg,
        "enhet": enhet,
    }


def _kontrollera_okanda(p: Mapping[str, Any], tillatna: set[str], namn: str) -> None:
    okanda = sorted(set(p) - tillatna)
    if okanda:
        raise LosarFel(f"{namn}: okända parametrar {okanda}. Tillåtna: {sorted(tillatna)}.")


def _radie_m(p: Mapping[str, Any]) -> float:
    """Sökavstånd för horisonten: H_max / tan(h_min)."""
    return dagsljus_paverkansradie_m(float(p["hojd_max_m"]), float(p["h_min_grader"]))


def _mosaik(k: LosarKontext) -> Mosaik:
    if k.ruta is None:
        raise LosarFel("Dagsljuslösare körs rutvis men fick ingen ruta.")
    max_celler = max_dagsljus_celler()
    grannar = tuple(g.mapp for g in k.grannar)
    cell_m = float(k.parametrar["cellstorlek_m"])
    antal_rutor = 1 + len(grannar)
    try:
        hojd, bredd = uppskatta_mosaik_form(k.ruta.mapp, grannar, cell_m)
    except RutaFel as exc:
        raise LosarFel(str(exc)) from exc
    kontrollera_dagsljus_celler(hojd * bredd, antal_rutor, max_celler)
    try:
        mosaik = bygg_mosaik(
            k.ruta.mapp,
            grannar,
            cell_m,
            k.parametrar["omsampling"],
            vegetation=bool(k.parametrar["vegetation"]),
        )
    except RutaFel as exc:
        raise LosarFel(str(exc)) from exc
    kontrollera_dagsljus_celler(int(mosaik.falt.z_m.size), antal_rutor, max_celler)
    return mosaik


def _gemensamma_varningar(k: LosarKontext, m: Mosaik) -> list[str]:
    v: list[str] = []
    if m.saknade_celler:
        v.append(
            f"{m.saknade_celler} celler i mosaikens utsträckning saknar höjddata (rutor som inte "
            "finns i scenariot); de antas inte skymma, så horisonter mot dem kan vara för låga"
        )
    spann = m.hojdspann_m
    if spann > float(k.parametrar["hojd_max_m"]):
        v.append(
            f"höjdskillnaden {spann:.1f} m i data överstiger hojd_max_m="
            f"{k.parametrar['hojd_max_m']} m: hinder högre än så kan skymma över h_min även "
            "bortom påverkansradien, och ingår då inte"
        )
    if m.vegetation:
        v.append(
            "vegetation ingår som ogenomskinliga hinder (vegetation.tif); trädkronor är delvis "
            "transparenta, särskilt lövträd utan löv på vårdagjämningen, så skuggan överskattas"
        )
    return v


def _standard_varning(m: Mosaik, antal_flaggade: int, antal_totalt: int, enhet: str) -> list[str]:
    """Varning när byggnader med gissad höjd (`hojdkalla = standard`) finns i mosaiken."""
    if not m.standard_byggnader:
        return []
    return [
        f"{len(m.standard_byggnader)} byggnader i mosaiken (varav {m.standard_byggnader_egen_ruta} "
        "i den egna rutans register) har standardhöjd (hojdkalla = standard: takhöjden är en "
        f"gissning, inte data). Resultatet beror på dem i {antal_flaggade} av {antal_totalt} "
        f"{enhet}, flaggade i standardhojd (1 = resultatet ändras om byggnaderna tas bort)"
    ]


@dataclass(frozen=True)
class _Pass:
    """Resultat av horisontpasset: trunkering och, om mosaiken har byggnader med standardhöjd,
    antal riktningar per cell där de sätter horisonten."""

    trunk: NDArray[np.int32]
    standard_riktningar: NDArray[np.int32] | None
    berakningsplats: str = "cpu_f32"


def _horisontpass(
    k: LosarKontext,
    m: Mosaik,
    mottagare: list[Callable[[int, float, NDArray[np.float32]], None]],
    mottagare_utan_standard: list[Callable[[int, float, NDArray[np.float32]], None]] | None = None,
    *,
    aktiva_riktningar: frozenset[int] | None = None,
    hoppa_over: list[Callable[[int], None]] | None = None,
    hoppa_over_utan_standard: list[Callable[[int], None]] | None = None,
) -> _Pass:
    """Beräknar horisonten för det egna fönstret riktning för riktning. `trunk` är antal
    riktningar per cell vars stråle lämnade höjdfältet före max avstånd.

    Har mosaiken byggnader med standardhöjd beräknas horisonten också utan dem
    (`falt_utan_standard`) och skickas till `mottagare_utan_standard`; det kostar ett varv till
    per riktning. När `aktiva_riktningar` anges (t.ex. solens azimutsektor i `SoltimmarMark`)
    hoppas inaktiva riktningar över utan att anropa `horisont_riktning_ruta`."""
    antal = int(k.parametrar["azimutriktningar"])
    radie = _radie_m(k.parametrar)
    enhet = str(k.parametrar.get("enhet", "cpu"))
    xp, enhet_namn = hamta_backend(enhet)
    pa_gpu = ar_gpu_backend(xp)
    z_xp = till_enhet(m.falt.z_m, xp, dtype=np.float32) if pa_gpu else None
    z0_xp = (
        till_enhet(m.falt_utan_standard.z_m, xp, dtype=np.float32)
        if pa_gpu and m.falt_utan_standard is not None
        else None
    )
    plats = berakningsplats(enhet_namn, np.float32)

    r0, r1, c0, c1 = m.fonster
    trunk = np.zeros((r1 - r0, c1 - c0), dtype=np.int32)
    std = None if m.falt_utan_standard is None else np.zeros_like(trunk)
    for i, phi in enumerate(riktningar_rad(antal)):
        if aktiva_riktningar is not None and i not in aktiva_riktningar:
            for g in hoppa_over or []:
                g(i)
            if m.falt_utan_standard is not None:
                for g in hoppa_over_utan_standard or []:
                    g(i)
            continue
        if pa_gpu:
            h, tr = horisont_riktning_ruta_pa_enhet(
                m.falt, z_xp, float(phi), radie, fonster=m.fonster, xp=xp
            )
        else:
            h, tr = horisont_riktning_ruta(m.falt, float(phi), radie, fonster=m.fonster)
        trunk += tr
        for f in mottagare:
            f(i, float(phi), h)
        if m.falt_utan_standard is not None and std is not None:
            if pa_gpu:
                h0, _ = horisont_riktning_ruta_pa_enhet(
                    m.falt_utan_standard, z0_xp, float(phi), radie, fonster=m.fonster, xp=xp
                )
            else:
                h0, _ = horisont_riktning_ruta(
                    m.falt_utan_standard, float(phi), radie, fonster=m.fonster
                )
            std += h0 != h
            for f in mottagare_utan_standard or []:
                f(i, float(phi), h0)
    del z_xp, z0_xp
    if m.falt.z_m.size >= _DAGSLJUS_GC_TROSKEL_CELLER:
        gc.collect()
    return _Pass(trunk, std, plats)


def _skriv_raster(sokvag: Path, a: NDArray[Any], m: Mosaik) -> None:
    from testsvit.k1_io import skriv_geotiff

    x_min, y_max = fonster_koordinater(m)
    skriv_geotiff(
        sokvag,
        np.asarray(a, dtype=np.float32),
        x_min=x_min,
        y_max=y_max,
        pixelstorlek=m.falt.cell_m,
    )


def _trunk_varning(trunk: NDArray[np.int32], antal: int) -> list[str]:
    n = int(np.count_nonzero(trunk))
    if not n:
        return []
    return [
        f"{n} av {trunk.size} celler har strålar som lämnar höjdfältet före påverkansradien "
        f"(rutan ligger vid kanten av scenariots data); se trunkering.tif (andel av {antal} "
        "riktningar)"
    ]


def _skriv_standardflagga(
    k: LosarKontext, m: Mosaik, flagga: NDArray[np.bool_] | None, form: tuple[int, int]
) -> tuple[int, list[str]]:
    """Skriver `standardhojd.tif` (1 = cellens resultat beror på en byggnad med standardhöjd,
    alltid skriven, noll om inga sådana byggnader finns) och returnerar antal flaggade celler
    samt varning."""
    ark = np.zeros(form) if flagga is None else flagga.astype(np.float64)
    _skriv_raster(k.utmapp / "standardhojd.tif", ark, m)
    antal = int(np.count_nonzero(ark))
    return antal, _standard_varning(m, antal, int(ark.size), "celler")


class _DagsljusLosare(Analyslosare):
    doman: ClassVar[str] = "dagsljus"
    version: ClassVar[str] = "2"
    _egna: ClassVar[set[str]] = set()
    _standard_k: ClassVar[int] = STANDARD_K

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        _kontrollera_okanda(parametrar, _GEMENSAMMA | self._egna, self.namn)
        return _gemensamma(parametrar, self._standard_k)

    def paverkansradie_m(self, parametrar: Mapping[str, Any]) -> float | None:
        # Horisonten räknas mot cellens närmaste kant, inte dess mitt. Påverkansradien till
        # kärnan får därför en celldiagonals marginal så att den är konservativ: en punkt exakt
        # på H/tan(h_min) från en cellkant ändrades annars (0,333 h i fas 1b-mätningen).
        return _radie_m(parametrar) + math.sqrt(2.0) * float(parametrar["cellstorlek_m"])


_HORISONT_CACHE_MAX = 16
_HORISONT_MAX_ANDRAD_ANDEL = 0.25
STANDARD_MAX_HORISONT_CACHE_MB = 128.0
_FLODEN_MAX_HORISONT_CACHE_MB_ENV = "FLODEN_MAX_HORISONT_CACHE_MB"
STANDARD_MAX_DAGSLJUS_CELLER = 4_500_000
_FLODEN_MAX_DAGSLJUS_CELLER_ENV = "FLODEN_MAX_DAGSLJUS_CELLER"
_DAGSLJUS_GC_TROSKEL_CELLER = 500_000


def max_horisont_cache_mb() -> float:
    """Läser bytebudget (MB) för `_HORISONT_CACHE` ur `FLODEN_MAX_HORISONT_CACHE_MB`.

    Tomt ger standard (`STANDARD_MAX_HORISONT_CACHE_MB` = 128,0 MB). `0` inaktiverar
    minnescachen helt. Ogiltigt värde (icke-tal, NaN/inf eller negativt) ger `LosarFel`.
    """
    raw = os.environ.get(_FLODEN_MAX_HORISONT_CACHE_MB_ENV)
    if raw is None or not raw.strip():
        return STANDARD_MAX_HORISONT_CACHE_MB
    text = raw.strip()
    try:
        val = float(text)
    except ValueError as exc:
        raise LosarFel(
            f"Ogiltigt värde i {_FLODEN_MAX_HORISONT_CACHE_MB_ENV}={raw!r}: "
            "ange ett icke-negativt tal i MB (0 = inaktivera minnescache)."
        ) from exc
    if not math.isfinite(val) or val < 0.0:
        raise LosarFel(
            f"Ogiltigt värde i {_FLODEN_MAX_HORISONT_CACHE_MB_ENV}={raw!r}: "
            "värdet måste vara ett ändligt tal >= 0 (0 = inaktivera minnescache)."
        )
    return val


def max_horisont_cache_bytes() -> int:
    """Bytebudget för `_HORISONT_CACHE` (0 innebär att minnescachen är avstängd)."""
    mb = max_horisont_cache_mb()
    if mb == 0.0:
        return 0
    return max(1, int(mb * 1024.0 * 1024.0))


def max_dagsljus_celler() -> int:
    """Läser celltaket för dagsljusmosaiken ur `FLODEN_MAX_DAGSLJUS_CELLER`.

    Tomt eller `0` ger standard (`STANDARD_MAX_DAGSLJUS_CELLER` = 4 500 000 celler).
    Ogiltigt värde (icke-heltal eller negativt) ger `LosarFel` (inga tysta fel).
    """
    raw = os.environ.get(_FLODEN_MAX_DAGSLJUS_CELLER_ENV)
    if raw is None or not raw.strip():
        return STANDARD_MAX_DAGSLJUS_CELLER
    text = raw.strip()
    try:
        val = int(text)
    except ValueError as exc:
        raise LosarFel(
            f"Ogiltigt värde i {_FLODEN_MAX_DAGSLJUS_CELLER_ENV}={raw!r}: "
            "ange ett icke-negativt heltal (0 eller tomt = standard)."
        ) from exc
    if val < 0:
        raise LosarFel(
            f"Ogiltigt värde i {_FLODEN_MAX_DAGSLJUS_CELLER_ENV}={raw!r}: "
            "värdet får inte vara negativt (0 eller tomt = standard)."
        )
    return STANDARD_MAX_DAGSLJUS_CELLER if val == 0 else val


def kontrollera_dagsljus_celler(
    n_celler: int, antal_rutor: int, max_celler: int | None = None
) -> None:
    """Fail-fast kontroll av dagsljusmosaikens cellantal före horisontberäkning."""
    grans = max_dagsljus_celler() if max_celler is None else max_celler
    if n_celler > grans:
        rut_txt = "ruta" if antal_rutor == 1 else "rutor"
        raise LosarFel(
            f"Dagsljusmosaiken omfattar {n_celler} celler ({antal_rutor} {rut_txt}), vilket "
            f"överstiger serverns minnesgräns på {grans} celler "
            f"({_FLODEN_MAX_DAGSLJUS_CELLER_ENV}). "
            "Öka cellstorlek_m, minska hojd_max_m/påverkansradien eller kör på en maskin med "
            "mer RAM."
        )


@dataclass(frozen=True)
class _HorisontCachePost:
    """Cachad horisontkub (float32, rad) för inkrementell omräkning vid lokal DSM-ändring."""

    dsm_sha256: str
    falt: Hojdfalt
    fonster: tuple[int, int, int, int]
    antal_riktningar: int
    max_avstand_m: float
    h_rad: NDArray[np.float32]
    trunk: NDArray[np.int32]
    trunk_riktningar: frozenset[int]
    giltiga_riktningar: frozenset[int]
    falt_utan_standard: Hojdfalt | None = None
    h0_rad: NDArray[np.float32] | None = None


@dataclass(frozen=True)
class _HorisontKubResultat:
    """Resultat från `_berakna_horisont_kub` (full, inkrementell eller cacheträff)."""

    h_rad: NDArray[np.float32]
    h0_rad: NDArray[np.float32] | None
    trunk: NDArray[np.int32]
    berakningsplats: str
    inkrementell: bool
    inkrementell_orsak: str
    andrade_celler: int


_HORISONT_LOCK = threading.Lock()
_HORISONT_CACHE: OrderedDict[tuple[Any, ...], _HorisontCachePost] = OrderedDict()
_HORISONT_RECEPT_INDEX: dict[tuple[Any, ...], tuple[Any, ...]] = {}
_HORISONT_GRID_INDEX: dict[tuple[Any, ...], tuple[Any, ...]] = {}


def _horisont_post_bytes(post: _HorisontCachePost) -> int:
    """Exakt storlek i byte (`nbytes`) för alla NumPy-arrayer i en cachad horisontpost."""
    totalt = int(post.falt.z_m.nbytes) + int(post.h_rad.nbytes) + int(post.trunk.nbytes)
    if post.falt_utan_standard is not None:
        totalt += int(post.falt_utan_standard.z_m.nbytes)
    if post.h0_rad is not None:
        totalt += int(post.h0_rad.nbytes)
    return totalt


def _horisont_cache_bytes_locked() -> int:
    """Total byte-storlek i `_HORISONT_CACHE` (anropas med `_HORISONT_LOCK`)."""
    sedda: set[int] = set()
    totalt = 0
    for post in _HORISONT_CACHE.values():
        pid = id(post)
        if pid not in sedda:
            sedda.add(pid)
            totalt += _horisont_post_bytes(post)
    return totalt


def horisont_cache_bytes() -> int:
    """Totalt antal NumPy-byte som för närvarande hålls i `_HORISONT_CACHE`."""
    with _HORISONT_LOCK:
        return _horisont_cache_bytes_locked()


def _rensa_horisont_index_for_locked(bort_nyckel: tuple[Any, ...]) -> None:
    for idx_karta in (_HORISONT_RECEPT_INDEX, _HORISONT_GRID_INDEX):
        for k in [kk for kk, vv in idx_karta.items() if vv == bort_nyckel]:
            del idx_karta[k]


def rensa_horisont_cache() -> None:
    """Rensar modulens minnescache för bas-horisonter (används i tester/benchmarks)."""
    with _HORISONT_LOCK:
        _HORISONT_CACHE.clear()
        _HORISONT_RECEPT_INDEX.clear()
        _HORISONT_GRID_INDEX.clear()


def _dsm_sha256(falt: Hojdfalt) -> str:
    return hashlib.sha256(np.ascontiguousarray(falt.z_m, dtype=np.float64).tobytes()).hexdigest()


def _ruta_recept_nycklar(ruta: Any) -> tuple[str, str]:
    """Returnerar `(egen_nyckel, bas_nyckel)` för en K1-ruta."""
    from gemensamt.hash import sha256_fil

    mapp = Path(ruta.mapp)
    egen = str(getattr(ruta, "hash", "") or mapp.name)
    bas = egen
    recept_fil = mapp / "recept.json"
    if recept_fil.is_file():
        try:
            egen = sha256_fil(recept_fil)
            data = json.loads(recept_fil.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                b = data.get("bas_recept_sha256")
                if isinstance(b, str) and b:
                    bas = b
        except (OSError, ValueError):
            pass
    if bas == egen:
        klar_fil = mapp.parent / ".klar"
        if klar_fil.is_file():
            try:
                klar = json.loads(klar_fil.read_text(encoding="utf-8"))
                if isinstance(klar, dict):
                    b = klar.get("bas")
                    if isinstance(b, str) and b:
                        bas = b
            except (OSError, ValueError):
                pass
    return egen, bas


def _hamta_exakt_horisont(primar_nyckel: tuple[Any, ...]) -> _HorisontCachePost | None:
    if max_horisont_cache_bytes() <= 0:
        return None
    with _HORISONT_LOCK:
        post = _HORISONT_CACHE.get(primar_nyckel)
        if post is not None:
            _HORISONT_CACHE.move_to_end(primar_nyckel)
        return post


def _hamta_bas_horisont(
    bas_recept_nyckel: tuple[Any, ...],
    grid_nyckel: tuple[Any, ...],
    *,
    ar_andrad_fran_bas: bool,
    egen_dsm_sha: str,
) -> _HorisontCachePost | None:
    if max_horisont_cache_bytes() <= 0:
        return None
    with _HORISONT_LOCK:
        primar: tuple[Any, ...] | None = None
        if ar_andrad_fran_bas:
            primar = _HORISONT_RECEPT_INDEX.get(bas_recept_nyckel)
        if primar is None:
            kand = _HORISONT_GRID_INDEX.get(grid_nyckel)
            if kand is not None and (ar_andrad_fran_bas or kand[0] != egen_dsm_sha):
                primar = kand
        if primar is None:
            return None
        post = _HORISONT_CACHE.get(primar)
        if post is not None:
            _HORISONT_CACHE.move_to_end(primar)
        return post


def _spara_horisont_i_cache(
    primar_nyckel: tuple[Any, ...],
    recept_nyckel: tuple[Any, ...],
    grid_nyckel: tuple[Any, ...],
    post: _HorisontCachePost,
    *,
    ar_andrad_fran_bas: bool,
) -> None:
    budget_bytes = max_horisont_cache_bytes()
    with _HORISONT_LOCK:
        if budget_bytes <= 0:
            _HORISONT_CACHE.clear()
            _HORISONT_RECEPT_INDEX.clear()
            _HORISONT_GRID_INDEX.clear()
            return
        befintlig = _HORISONT_CACHE.get(primar_nyckel)
        if befintlig is not None and not befintlig.giltiga_riktningar.issubset(
            post.giltiga_riktningar
        ):
            # Slå ihop giltiga riktningar så att en delmängd (t.ex. soltimmar) aldrig skriver
            # över redan beräknade riktningar (t.ex. från SVF eller horisont).
            h_ihop = befintlig.h_rad.copy()
            h0_ihop = None if befintlig.h0_rad is None else befintlig.h0_rad.copy()
            for k_idx in post.giltiga_riktningar:
                h_ihop[k_idx] = post.h_rad[k_idx]
                if h0_ihop is not None and post.h0_rad is not None:
                    h0_ihop[k_idx] = post.h0_rad[k_idx]
            post = _HorisontCachePost(
                dsm_sha256=post.dsm_sha256,
                falt=post.falt,
                fonster=post.fonster,
                antal_riktningar=post.antal_riktningar,
                max_avstand_m=post.max_avstand_m,
                h_rad=h_ihop,
                trunk=befintlig.trunk,
                trunk_riktningar=befintlig.trunk_riktningar,
                giltiga_riktningar=befintlig.giltiga_riktningar | post.giltiga_riktningar,
                falt_utan_standard=post.falt_utan_standard,
                h0_rad=h0_ihop,
            )
        post_bytes = _horisont_post_bytes(post)
        if post_bytes > budget_bytes:
            # Cacha aldrig en enskild post som ensam överskrider budgeten.
            if primar_nyckel in _HORISONT_CACHE:
                del _HORISONT_CACHE[primar_nyckel]
                _rensa_horisont_index_for_locked(primar_nyckel)
            while _HORISONT_CACHE and _horisont_cache_bytes_locked() > budget_bytes:
                bort_nyckel, _ = _HORISONT_CACHE.popitem(last=False)
                _rensa_horisont_index_for_locked(bort_nyckel)
            return
        _HORISONT_CACHE[primar_nyckel] = post
        _HORISONT_CACHE.move_to_end(primar_nyckel)
        _HORISONT_RECEPT_INDEX[recept_nyckel] = primar_nyckel
        if not ar_andrad_fran_bas or grid_nyckel not in _HORISONT_GRID_INDEX:
            _HORISONT_GRID_INDEX[grid_nyckel] = primar_nyckel
        while _HORISONT_CACHE and (
            len(_HORISONT_CACHE) > _HORISONT_CACHE_MAX
            or _horisont_cache_bytes_locked() > budget_bytes
        ):
            bort_nyckel, _ = _HORISONT_CACHE.popitem(last=False)
            _rensa_horisont_index_for_locked(bort_nyckel)


def _berakna_trunk(
    falt: Hojdfalt,
    antal: int,
    radie: float,
    fonster: tuple[int, int, int, int],
    aktiva_riktningar: frozenset[int] | None = None,
) -> NDArray[np.int32]:
    """Analytisk trunkeringsräkning för valda riktningar (bitidentisk med `_horisontpass`)."""
    from dagsljus.horisont import _trunkering_ruta

    r0, r1, c0, c1 = fonster
    rmax_c = radie / falt.cell_m
    trunk = np.zeros((r1 - r0, c1 - c0), dtype=np.int32)
    for i, phi in enumerate(riktningar_rad(antal)):
        if aktiva_riktningar is not None and i not in aktiva_riktningar:
            continue
        trunk += _trunkering_ruta(falt, float(phi), rmax_c, r0, r1, c0, c1)
    return trunk


def _samma_geometri(
    post: _HorisontCachePost,
    m: Mosaik,
    antal: int,
    radie: float,
) -> bool:
    return (
        post.falt.z_m.shape == m.falt.z_m.shape
        and post.falt.cell_m == m.falt.cell_m
        and post.falt.x_min_m == m.falt.x_min_m
        and post.falt.y_max_m == m.falt.y_max_m
        and post.fonster == m.fonster
        and post.antal_riktningar == antal
        and post.max_avstand_m == radie
    )


def _kvalificerar_inkrementell(
    bas_post: _HorisontCachePost,
    m: Mosaik,
    antal: int,
    radie: float,
    pa_gpu: bool,
) -> tuple[bool, str, int]:
    """Kontrollerar om `bas_post` kan uppdateras inkrementellt mot `m`."""
    if pa_gpu:
        return False, "gpu_ej_inkrementell", 0
    if not _samma_geometri(bas_post, m, antal, radie):
        return False, "olika_geometri", 0
    if (m.falt_utan_standard is None) != (bas_post.falt_utan_standard is None):
        return False, "standardhojd_andrad", 0
    andrade_celler = int(np.count_nonzero(m.falt.z_m != bas_post.falt.z_m))
    andel = float(andrade_celler) / float(m.falt.z_m.size)
    std_for_stor = (
        m.falt_utan_standard is not None
        and bas_post.falt_utan_standard is not None
        and (
            bas_post.h0_rad is None
            or float(
                np.count_nonzero(m.falt_utan_standard.z_m != bas_post.falt_utan_standard.z_m)
            )
            / float(m.falt_utan_standard.z_m.size)
            >= _HORISONT_MAX_ANDRAD_ANDEL
        )
    )
    if andel >= _HORISONT_MAX_ANDRAD_ANDEL or std_for_stor:
        return False, "for_stor_andring", andrade_celler
    return True, "lokal_uppdatering", andrade_celler


def _berakna_horisont_kub(
    kontext: LosarKontext,
    m: Mosaik,
    *,
    aktiva_riktningar: frozenset[int] | None = None,
) -> _HorisontKubResultat:
    """Hämtar, uppdaterar inkrementellt eller beräknar horisontkuben (float32, rad) för `m`.

    Delas mellan `dagsljus.horisont`, `dagsljus.svf_mark` och `dagsljus.soltimmar_mark`:
    1. Exakt DSM-träff i den trådsäkra minnescachen återanvänder redan beräknade riktningar och
       kompletterar endast saknade riktningar (t.ex. SVF direkt efter soltimmar utan dubbeljobb).
    2. Vid lokal byggnads-/DSM-ändring (< 25 % ändrade celler) uppdateras en cachad bas-horisont
       med `uppdatera_horisont_inkrementellt` (bitidentiskt med full beräkning).
    3. I övriga fall (kall cache, GPU, > 25 % ändring eller ändrad standardhöjd) sker transparent
       fallback till full `_horisontpass`.
    """
    r0, r1, c0, c1 = m.fonster
    antal = int(kontext.parametrar["azimutriktningar"])
    radie = _radie_m(kontext.parametrar)
    enhet = str(kontext.parametrar.get("enhet", "cpu"))
    # Validera cachebudget tidigt (inga tysta fel vid ogiltig FLODEN_MAX_HORISONT_CACHE_MB).
    max_horisont_cache_bytes()
    xp, enhet_namn = hamta_backend(enhet)
    pa_gpu = ar_gpu_backend(xp)
    plats = berakningsplats(enhet_namn, np.float32)
    behov: frozenset[int] = (
        frozenset(range(antal)) if aktiva_riktningar is None else aktiva_riktningar
    )

    assert kontext.ruta is not None
    rutor_alla = (kontext.ruta, *kontext.grannar)
    par_nycklar = [(r.ruta_id, *_ruta_recept_nycklar(r)) for r in rutor_alla]
    egen_recept_tup = tuple((rid, egen) for rid, egen, _ in par_nycklar)
    bas_recept_tup = tuple((rid, bas) for rid, _, bas in par_nycklar)
    ar_andrad_fran_bas = bas_recept_tup != egen_recept_tup

    dsm_sha = _dsm_sha256(m.falt)
    dsm0_sha = "" if m.falt_utan_standard is None else _dsm_sha256(m.falt_utan_standard)
    primar_nyckel = (dsm_sha, dsm0_sha, antal, radie, m.fonster)
    recept_nyckel = (egen_recept_tup, antal, radie, m.fonster)
    bas_recept_nyckel = (bas_recept_tup, antal, radie, m.fonster)
    grid_nyckel = (
        kontext.ruta.ruta_id,
        m.falt.z_m.shape,
        m.fonster,
        m.falt.x_min_m,
        m.falt.y_max_m,
        m.falt.cell_m,
        antal,
        radie,
    )

    # 1. Exakt DSM-träff i minnescachen.
    exakt_post = None if pa_gpu else _hamta_exakt_horisont(primar_nyckel)
    if (
        exakt_post is not None
        and _samma_geometri(exakt_post, m, antal, radie)
        and (m.falt_utan_standard is None) == (exakt_post.falt_utan_standard is None)
    ):
        saknade = behov - exakt_post.giltiga_riktningar
        if not saknade:
            trunk = (
                exakt_post.trunk.copy()
                if behov == exakt_post.trunk_riktningar
                else _berakna_trunk(m.falt, antal, radie, m.fonster, aktiva_riktningar)
            )
            return _HorisontKubResultat(
                h_rad=exakt_post.h_rad,
                h0_rad=exakt_post.h0_rad,
                trunk=trunk,
                berakningsplats=plats,
                inkrementell=True,
                inkrementell_orsak="cache_traff",
                andrade_celler=0,
            )
        # Komplettera enbart saknade riktningar (t.ex. SVF efter soltimmar på samma DSM).
        h_ex = exakt_post.h_rad.copy()
        h0_ex = None if exakt_post.h0_rad is None else exakt_post.h0_rad.copy()
        andrade_ex = 0
        orsak_ex = "delvis_cache_traff"
        kvar = saknade
        bas_for_saknade = _hamta_bas_horisont(
            bas_recept_nyckel,
            grid_nyckel,
            ar_andrad_fran_bas=ar_andrad_fran_bas,
            egen_dsm_sha=dsm_sha,
        )
        if bas_for_saknade is not None and bas_for_saknade.dsm_sha256 != dsm_sha:
            ok_b, _ors_b, n_andr = _kvalificerar_inkrementell(
                bas_for_saknade, m, antal, radie, pa_gpu
            )
            inkr_bar = saknade & bas_for_saknade.giltiga_riktningar
            if ok_b and inkr_bar:
                h_del = uppdatera_horisont_inkrementellt(
                    bas_for_saknade.h_rad,
                    bas_for_saknade.falt,
                    m.falt,
                    radie,
                    fonster=m.fonster,
                    aktiva_riktningar=inkr_bar,
                )
                for k_idx in inkr_bar:
                    h_ex[k_idx] = h_del[k_idx]
                if (
                    h0_ex is not None
                    and m.falt_utan_standard is not None
                    and bas_for_saknade.falt_utan_standard is not None
                    and bas_for_saknade.h0_rad is not None
                ):
                    h0_del = uppdatera_horisont_inkrementellt(
                        bas_for_saknade.h0_rad,
                        bas_for_saknade.falt_utan_standard,
                        m.falt_utan_standard,
                        radie,
                        fonster=m.fonster,
                        aktiva_riktningar=inkr_bar,
                    )
                    for k_idx in inkr_bar:
                        h0_ex[k_idx] = h0_del[k_idx]
                kvar = saknade - inkr_bar
                andrade_ex = n_andr
                orsak_ex = "lokal_uppdatering"
        if kvar:
            def _lagra_kvar(i: int, _phi: float, h: NDArray[np.float32]) -> None:
                h_ex[i] = h

            def _lagra_kvar0(i: int, _phi: float, h: NDArray[np.float32]) -> None:
                assert h0_ex is not None
                h0_ex[i] = h

            _horisontpass(
                kontext,
                m,
                [_lagra_kvar],
                [_lagra_kvar0] if h0_ex is not None else None,
                aktiva_riktningar=kvar,
            )
        trunk_ex = _berakna_trunk(m.falt, antal, radie, m.fonster, aktiva_riktningar)
        _spara_horisont_i_cache(
            primar_nyckel,
            recept_nyckel,
            grid_nyckel,
            _HorisontCachePost(
                dsm_sha256=dsm_sha,
                falt=m.falt,
                fonster=m.fonster,
                antal_riktningar=antal,
                max_avstand_m=radie,
                h_rad=h_ex,
                trunk=trunk_ex,
                trunk_riktningar=behov,
                giltiga_riktningar=exakt_post.giltiga_riktningar | saknade,
                falt_utan_standard=m.falt_utan_standard,
                h0_rad=h0_ex,
            ),
            ar_andrad_fran_bas=ar_andrad_fran_bas,
        )
        if not kvar and m.falt.z_m.size >= _DAGSLJUS_GC_TROSKEL_CELLER:
            gc.collect()
        return _HorisontKubResultat(
            h_rad=h_ex,
            h0_rad=h0_ex,
            trunk=trunk_ex,
            berakningsplats=plats,
            inkrementell=True,
            inkrementell_orsak=orsak_ex,
            andrade_celler=andrade_ex,
        )

    # 2. Inkrementell uppdatering från cachad bas-horisont vid lokal DSM-ändring.
    bas_post = _hamta_bas_horisont(
        bas_recept_nyckel,
        grid_nyckel,
        ar_andrad_fran_bas=ar_andrad_fran_bas,
        egen_dsm_sha=dsm_sha,
    )
    inkrementell = False
    inkrementell_orsak = "bas_saknas"
    andrade_celler = 0
    h_rad: NDArray[np.float32] | None = None
    h0_rad: NDArray[np.float32] | None = None
    trunk_res: NDArray[np.int32] | None = None

    if bas_post is not None:
        ok, orsak_b, n_andr = _kvalificerar_inkrementell(bas_post, m, antal, radie, pa_gpu)
        andrade_celler = n_andr
        if not ok:
            inkrementell_orsak = orsak_b
        else:
            gemensamma = behov & bas_post.giltiga_riktningar
            saknade_b = behov - bas_post.giltiga_riktningar
            if not behov or gemensamma:
                if gemensamma:
                    h_rad = uppdatera_horisont_inkrementellt(
                        bas_post.h_rad,
                        bas_post.falt,
                        m.falt,
                        radie,
                        fonster=m.fonster,
                        aktiva_riktningar=gemensamma,
                    )
                    if (
                        m.falt_utan_standard is not None
                        and bas_post.falt_utan_standard is not None
                        and bas_post.h0_rad is not None
                    ):
                        h0_rad = uppdatera_horisont_inkrementellt(
                            bas_post.h0_rad,
                            bas_post.falt_utan_standard,
                            m.falt_utan_standard,
                            radie,
                            fonster=m.fonster,
                            aktiva_riktningar=gemensamma,
                        )
                else:
                    h_rad = np.zeros((antal, r1 - r0, c1 - c0), dtype=np.float32)
                    if m.falt_utan_standard is not None:
                        h0_rad = np.zeros((antal, r1 - r0, c1 - c0), dtype=np.float32)
                if saknade_b:
                    assert h_rad is not None

                    def _lagra_s(i: int, _phi: float, h: NDArray[np.float32]) -> None:
                        assert h_rad is not None
                        h_rad[i] = h

                    def _lagra_s0(i: int, _phi: float, h: NDArray[np.float32]) -> None:
                        assert h0_rad is not None
                        h0_rad[i] = h

                    _horisontpass(
                        kontext,
                        m,
                        [_lagra_s],
                        [_lagra_s0] if h0_rad is not None else None,
                        aktiva_riktningar=saknade_b,
                    )
                trunk_res = (
                    bas_post.trunk.copy()
                    if behov == bas_post.trunk_riktningar
                    else _berakna_trunk(m.falt, antal, radie, m.fonster, aktiva_riktningar)
                )
                inkrementell = True
                inkrementell_orsak = "lokal_uppdatering"
            else:
                inkrementell_orsak = "saknar_riktningar"

    # 3. Full beräkning (kall cache, GPU eller ändring som inte kvalificerar).
    korde_horisontpass = False
    if not inkrementell or h_rad is None or trunk_res is None:
        h_rad = np.zeros((antal, r1 - r0, c1 - c0), dtype=np.float32)
        h0_rad = (
            np.zeros((antal, r1 - r0, c1 - c0), dtype=np.float32)
            if m.falt_utan_standard is not None
            else None
        )

        def lagra_rad(i: int, _phi: float, h: NDArray[np.float32]) -> None:
            assert h_rad is not None
            h_rad[i] = h

        def lagra_rad0(i: int, _phi: float, h: NDArray[np.float32]) -> None:
            assert h0_rad is not None
            h0_rad[i] = h

        pas = _horisontpass(
            kontext,
            m,
            [lagra_rad],
            [lagra_rad0] if h0_rad is not None else None,
            aktiva_riktningar=aktiva_riktningar,
        )
        korde_horisontpass = True
        trunk_res = pas.trunk
        plats = pas.berakningsplats

    _spara_horisont_i_cache(
        primar_nyckel,
        recept_nyckel,
        grid_nyckel,
        _HorisontCachePost(
            dsm_sha256=dsm_sha,
            falt=m.falt,
            fonster=m.fonster,
            antal_riktningar=antal,
            max_avstand_m=radie,
            h_rad=h_rad,
            trunk=trunk_res,
            trunk_riktningar=behov,
            giltiga_riktningar=behov,
            falt_utan_standard=m.falt_utan_standard,
            h0_rad=h0_rad,
        ),
        ar_andrad_fran_bas=ar_andrad_fran_bas,
    )
    if not korde_horisontpass and m.falt.z_m.size >= _DAGSLJUS_GC_TROSKEL_CELLER:
        gc.collect()
    return _HorisontKubResultat(
        h_rad=h_rad,
        h0_rad=h0_rad,
        trunk=trunk_res,
        berakningsplats=plats,
        inkrementell=inkrementell,
        inkrementell_orsak=inkrementell_orsak,
        andrade_celler=andrade_celler,
    )


class Horisont(_DagsljusLosare):
    namn: ClassVar[str] = "dagsljus.horisont"
    beskrivning: ClassVar[str] = (
        "Horisontprofil h(phi) i K azimutriktningar per cell (DSM = mark + byggnader)."
    )
    utdataroller: ClassVar[tuple[str, ...]] = (
        "horisont",
        "horisont_meta",
        "trunkering",
        "standardhojd",
    )

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        m = _mosaik(kontext)
        antal = int(kontext.parametrar["azimutriktningar"])
        radie = _radie_m(kontext.parametrar)
        kub = _berakna_horisont_kub(kontext, m)
        lagr = np.rint(np.degrees(kub.h_rad.astype(np.float64)) * 100.0).astype(np.uint16)
        trunk = kub.trunk
        std_riktn = (
            None if kub.h0_rad is None else np.sum(kub.h0_rad != kub.h_rad, axis=0, dtype=np.int32)
        )

        np.save(kontext.utmapp / "horisont.npy", lagr)
        x_min, y_max = fonster_koordinater(m)
        (kontext.utmapp / "horisont.json").write_text(
            json.dumps(
                {
                    "kontrakt": "dagsljus-horisont/v1",
                    "enhet": "0,01 grader (uint16)",
                    "form": ["riktning", "rad", "kolumn"],
                    "azimutriktningar": antal,
                    "azimut": "phi_k = 2 pi k / K, norr = 0, medurs",
                    "x_min_m": x_min,
                    "y_max_m": y_max,
                    "cellstorlek_m": m.falt.cell_m,
                    "epsg": 3011,
                    "max_avstand_m": radie,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        _skriv_raster(kontext.utmapp / "trunkering.tif", trunk / antal, m)
        n_std, std_var = _skriv_standardflagga(
            kontext,
            m,
            None if std_riktn is None else std_riktn > 0,
            trunk.shape,
        )
        return LosarUtdata(
            filer={
                "horisont": "horisont.npy",
                "horisont_meta": "horisont.json",
                "trunkering": "trunkering.tif",
                "standardhojd": "standardhojd.tif",
            },
            matvarden={
                "horisont_max_grader": float(lagr.max()) / 100.0,
                "celler": int(trunk.size),
                "azimutriktningar": antal,
                "celler_standardhojd": n_std,
                "berakningsplats": kub.berakningsplats,
                "inkrementell": kub.inkrementell,
                "inkrementell_orsak": kub.inkrementell_orsak,
                "andrade_celler": kub.andrade_celler,
            },
            varningar=tuple(
                _gemensamma_varningar(kontext, m) + _trunk_varning(trunk, antal) + std_var
            ),
        )


class SvfMark(_DagsljusLosare):
    namn: ClassVar[str] = "dagsljus.svf_mark"
    beskrivning: ClassVar[str] = "Himmelsexponering (SVF) för horisontella markytor och tak."
    utdataroller: ClassVar[tuple[str, ...]] = ("svf", "trunkering", "standardhojd")

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        m = _mosaik(kontext)
        r0, r1, c0, c1 = m.fonster
        antal = int(kontext.parametrar["azimutriktningar"])
        kub = _berakna_horisont_kub(kontext, m)
        summa = np.zeros((r1 - r0, c1 - c0), dtype=np.float64)
        for i in range(antal):
            summa += np.sin(kub.h_rad[i].astype(np.float64)) ** 2
        svf = 1.0 - summa / antal
        flagga: NDArray[np.bool_] | None = None
        if kub.h0_rad is not None:
            summa0 = np.zeros((r1 - r0, c1 - c0), dtype=np.float64)
            for i in range(antal):
                summa0 += np.sin(kub.h0_rad[i].astype(np.float64)) ** 2
            flagga = (1.0 - summa0 / antal) != svf
        trunk = kub.trunk
        _skriv_raster(kontext.utmapp / "svf.tif", svf, m)
        _skriv_raster(kontext.utmapp / "trunkering.tif", trunk / antal, m)
        n_std, std_var = _skriv_standardflagga(kontext, m, flagga, svf.shape)
        return LosarUtdata(
            filer={
                "svf": "svf.tif",
                "trunkering": "trunkering.tif",
                "standardhojd": "standardhojd.tif",
            },
            matvarden={
                "svf_medel": float(svf.mean()),
                "svf_min": float(svf.min()),
                "celler": int(svf.size),
                "celler_standardhojd": n_std,
                "berakningsplats": kub.berakningsplats,
                "inkrementell": kub.inkrementell,
                "inkrementell_orsak": kub.inkrementell_orsak,
                "andrade_celler": kub.andrade_celler,
            },
            varningar=tuple(
                _gemensamma_varningar(kontext, m) + _trunk_varning(trunk, antal) + std_var
            ),
        )


def _validera_tidsfonster(parametrar: Mapping[str, Any]) -> tuple[float, float]:
    start = _tal(parametrar, "start_timme", 0.0)
    slut = _tal(parametrar, "slut_timme", 24.0)
    if not (0.0 <= start < slut <= 24.0):
        raise LosarFel(
            f"Tidsfönstret måste uppfylla 0 <= start_timme < slut_timme <= 24, "
            f"fick [{start}, {slut}]."
        )
    return start, slut


def _validera_solparametrar(parametrar: Mapping[str, Any]) -> dict[str, Any]:
    datum = parametrar.get("datum", ["2026-03-20"])
    if (
        not isinstance(datum, list | tuple)
        or not datum
        or not all(isinstance(d, str) for d in datum)
    ):
        raise LosarFel(f"datum måste vara en icke-tom lista av 'ÅÅÅÅ-MM-DD', fick {datum!r}.")
    steg = _tal(parametrar, "steg_min", 10.0, over=0.0)
    if abs(1440.0 / steg - round(1440.0 / steg)) > 1e-9:
        raise LosarFel(f"1440 måste vara delbart med steg_min, fick {steg}.")
    refr = parametrar.get("refraktion", False)
    if not isinstance(refr, bool):
        raise LosarFel(f"refraktion måste vara true/false, fick {refr!r}.")
    start, slut = _validera_tidsfonster(parametrar)
    return {
        "datum": sorted(str(d) for d in datum),
        "steg_min": steg,
        "refraktion": refr,
        "start_timme": start,
        "slut_timme": slut,
    }


def _solpositioner_per_datum(
    datum: list[str],
    lat: float,
    lon: float,
    steg_min: float,
    *,
    refraktion: bool,
    start_timme: float = 0.0,
    slut_timme: float = 24.0,
) -> list[tuple[NDArray[np.float64], NDArray[np.float64]]]:
    from dagsljus.solposition import SolFel

    mask = tidsfonster_mask(steg_min, start_timme, slut_timme)
    ut: list[tuple[NDArray[np.float64], NDArray[np.float64]]] = []
    try:
        for d in datum:
            tider = dygnsfonster_utc(d, lon, steg_min)[mask]
            ut.append(solposition(tider, lat, lon, refraktion=refraktion))
    except SolFel as exc:
        raise LosarFel(str(exc)) from exc
    return ut


class SoltimmarMark(_DagsljusLosare):
    namn: ClassVar[str] = "dagsljus.soltimmar_mark"
    beskrivning: ClassVar[str] = "Soltimmar per dygn (medel över valda datum) för markceller."
    utdataroller: ClassVar[tuple[str, ...]] = ("soltimmar", "trunkering", "standardhojd")
    _egna: ClassVar[set[str]] = {"datum", "steg_min", "refraktion", "start_timme", "slut_timme"}
    _standard_k: ClassVar[int] = STANDARD_K_SOLTIMMAR

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        ut = super().validera_parametrar(parametrar)
        ut.update(_validera_solparametrar(parametrar))
        return ut

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        m = _mosaik(kontext)
        r0, r1, c0, c1 = m.fonster
        antal = int(kontext.parametrar["azimutriktningar"])
        x_min, y_max = fonster_koordinater(m)
        e, n = mitt_3011((x_min, y_max), (r1 - r0, c1 - c0), m.falt.cell_m)
        lat, lon = lat_lon_fran_3011(e, n)
        datum = list(kontext.parametrar["datum"])
        steg_min = float(kontext.parametrar["steg_min"])
        start_h = float(kontext.parametrar.get("start_timme", 0.0))
        slut_h = float(kontext.parametrar.get("slut_timme", 24.0))
        per_dag = _solpositioner_per_datum(
            datum,
            lat,
            lon,
            steg_min,
            refraktion=bool(kontext.parametrar["refraktion"]),
            start_timme=start_h,
            slut_timme=slut_h,
        )
        sol_h = np.concatenate([h for h, _ in per_dag])
        sol_az = np.concatenate([a for _, a in per_dag])
        form = (r1 - r0, c1 - c0)
        steg_h = steg_min / 60.0 / len(datum)
        ack = SoltimmarAckumulator(antal, sol_h, sol_az, steg_h, form)
        aktiva = ack.aktiva_riktningar()
        kub = _berakna_horisont_kub(kontext, m, aktiva_riktningar=aktiva)
        ack0 = (
            SoltimmarAckumulator(antal, sol_h, sol_az, steg_h, form)
            if kub.h0_rad is not None
            else None
        )
        for i in range(antal):
            if i in aktiva:
                ack.mata(i, kub.h_rad[i])
                if ack0 is not None and kub.h0_rad is not None:
                    ack0.mata(i, kub.h0_rad[i])
            else:
                ack.mata(i, None)
                if ack0 is not None:
                    ack0.mata(i, None)
        trunk = kub.trunk
        timmar = ack.timmar()
        _skriv_raster(kontext.utmapp / "soltimmar.tif", timmar, m)
        _skriv_raster(kontext.utmapp / "trunkering.tif", trunk / antal, m)
        flagga = None if ack0 is None else ack0.timmar() != timmar
        n_std, std_var = _skriv_standardflagga(kontext, m, flagga, timmar.shape)
        var = _gemensamma_varningar(kontext, m) + _trunk_varning(trunk, antal) + std_var
        if antal < 128:
            var.append(
                f"azimutriktningar={antal}: soltimmar nära skuggkanter kan avvika flera tiondels "
                "timmar (uppmätt på syntetiskt block, docs/DAGSLJUS.md); använd >= 180 för "
                "skuggstudier"
            )
        return LosarUtdata(
            filer={
                "soltimmar": "soltimmar.tif",
                "trunkering": "trunkering.tif",
                "standardhojd": "standardhojd.tif",
            },
            matvarden={
                "soltimmar_medel_h": float(timmar.mean()),
                "soltimmar_min_h": float(timmar.min()),
                "soltimmar_max_h": float(timmar.max()),
                "datum": len(datum),
                "start_timme": start_h,
                "slut_timme": slut_h,
                "lat_grader": lat,
                "lon_grader": lon,
                "celler_standardhojd": n_std,
                "berakningsplats": kub.berakningsplats,
                "inkrementell": kub.inkrementell,
                "inkrementell_orsak": kub.inkrementell_orsak,
                "andrade_celler": kub.andrade_celler,
            },
            varningar=tuple(var),
        )


class SammanhangandeSoltimmarMark(_DagsljusLosare):
    namn: ClassVar[str] = "dagsljus.soltimmar_sammanhangande"
    beskrivning: ClassVar[str] = (
        "Längsta sammanhängande solperiod (h) per dygn/tidsfönster för markceller."
    )
    utdataroller: ClassVar[tuple[str, ...]] = (
        "sammanhangande_soltimmar",
        "trunkering",
        "standardhojd",
    )
    _egna: ClassVar[set[str]] = {"datum", "steg_min", "refraktion", "start_timme", "slut_timme"}
    _standard_k: ClassVar[int] = STANDARD_K_SOLTIMMAR

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        ut = super().validera_parametrar(parametrar)
        ut.update(_validera_solparametrar(parametrar))
        return ut

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        m = _mosaik(kontext)
        r0, r1, c0, c1 = m.fonster
        antal = int(kontext.parametrar["azimutriktningar"])
        x_min, y_max = fonster_koordinater(m)
        e, n = mitt_3011((x_min, y_max), (r1 - r0, c1 - c0), m.falt.cell_m)
        lat, lon = lat_lon_fran_3011(e, n)
        datum = list(kontext.parametrar["datum"])
        steg_min = float(kontext.parametrar["steg_min"])
        start_h = float(kontext.parametrar.get("start_timme", 0.0))
        slut_h = float(kontext.parametrar.get("slut_timme", 24.0))
        per_dag = _solpositioner_per_datum(
            datum,
            lat,
            lon,
            steg_min,
            refraktion=bool(kontext.parametrar["refraktion"]),
            start_timme=start_h,
            slut_timme=slut_h,
        )
        dag_langder = [h.size for h, _ in per_dag]
        sol_h = np.concatenate([h for h, _ in per_dag])
        sol_az = np.concatenate([a for _, a in per_dag])
        form = (r1 - r0, c1 - c0)
        ack = SolsynlighetAckumulator(antal, sol_h, sol_az, form)
        ack0 = SolsynlighetAckumulator(antal, sol_h, sol_az, form)
        pas = _horisontpass(
            kontext,
            m,
            [lambda i, _p, h: ack.mata(i, h)],
            [lambda i, _p, h: ack0.mata(i, h)],
            aktiva_riktningar=ack.aktiva_riktningar(),
            hoppa_over=[lambda i: ack.mata(i, None)],
            hoppa_over_utan_standard=[lambda i: ack0.mata(i, None)],
        )
        steg_h = steg_min / 60.0

        def _medel_sammanhangande(syn: NDArray[np.bool_]) -> NDArray[np.float64]:
            summa = np.zeros(form, dtype=np.float64)
            pos = 0
            for ln in dag_langder:
                summa += sammanhangande_soltimmar_fran_synlighet(syn[pos : pos + ln], steg_h)
                pos += ln
            return summa / len(dag_langder)

        samman = _medel_sammanhangande(ack.synlighet())
        _skriv_raster(kontext.utmapp / "sammanhangande_soltimmar.tif", samman, m)
        _skriv_raster(kontext.utmapp / "trunkering.tif", pas.trunk / antal, m)
        flagga = (
            None
            if pas.standard_riktningar is None
            else _medel_sammanhangande(ack0.synlighet()) != samman
        )
        n_std, std_var = _skriv_standardflagga(kontext, m, flagga, samman.shape)
        var = _gemensamma_varningar(kontext, m) + _trunk_varning(pas.trunk, antal) + std_var
        return LosarUtdata(
            filer={
                "sammanhangande_soltimmar": "sammanhangande_soltimmar.tif",
                "trunkering": "trunkering.tif",
                "standardhojd": "standardhojd.tif",
            },
            matvarden={
                "sammanhangande_medel_h": float(samman.mean()),
                "sammanhangande_min_h": float(samman.min()),
                "sammanhangande_max_h": float(samman.max()),
                "datum": len(datum),
                "start_timme": start_h,
                "slut_timme": slut_h,
                "lat_grader": lat,
                "lon_grader": lon,
                "celler_standardhojd": n_std,
                "berakningsplats": pas.berakningsplats,
            },
            varningar=tuple(var),
        )


class SkuggmaskKlockslag(_DagsljusLosare):
    namn: ClassVar[str] = "dagsljus.skuggmask_vid_klockslag"
    beskrivning: ClassVar[str] = (
        "Skuggmask (uint8: 1 = sol, 0 = skugga) vid valda klockslag (t.ex. kl. 09, 12, 15, 18)."
    )
    utdataroller: ClassVar[tuple[str, ...]] = (
        "skuggmask",
        "skuggmask_meta",
        "trunkering",
        "standardhojd",
    )
    _egna: ClassVar[set[str]] = {"datum", "klockslag", "refraktion", "utc_offset_h"}
    _standard_k: ClassVar[int] = STANDARD_K_SOLTIMMAR

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        ut = super().validera_parametrar(parametrar)
        datum = parametrar.get("datum", "2026-03-20")
        if not isinstance(datum, str) or not datum.strip():
            raise LosarFel(f"datum måste vara en sträng 'ÅÅÅÅ-MM-DD', fick {datum!r}.")
        klockslag = parametrar.get("klockslag", [9.0, 12.0, 15.0, 18.0])
        if (
            not isinstance(klockslag, list | tuple)
            or not klockslag
            or any(
                isinstance(t, bool)
                or not isinstance(t, int | float)
                or not (math.isfinite(float(t)) and 0.0 <= float(t) < 24.0)
                for t in klockslag
            )
        ):
            raise LosarFel(
                f"klockslag måste vara en icke-tom lista av timmar i [0, 24), fick {klockslag!r}."
            )
        refr = parametrar.get("refraktion", False)
        if not isinstance(refr, bool):
            raise LosarFel(f"refraktion måste vara true/false, fick {refr!r}.")
        utc_off = parametrar.get("utc_offset_h")
        if utc_off is not None and (
            isinstance(utc_off, bool)
            or not isinstance(utc_off, int | float)
            or not (-14.0 <= float(utc_off) <= 14.0)
        ):
            raise LosarFel(
                f"utc_offset_h måste vara ett tal i [-14, 14] eller None, fick {utc_off!r}."
            )
        ut.update(
            {
                "datum": str(datum),
                "klockslag": [float(t) for t in klockslag],
                "refraktion": refr,
                "utc_offset_h": None if utc_off is None else float(utc_off),
            }
        )
        return ut

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        from dagsljus.solposition import SolFel

        m = _mosaik(kontext)
        r0, r1, c0, c1 = m.fonster
        antal = int(kontext.parametrar["azimutriktningar"])
        x_min, y_max = fonster_koordinater(m)
        e, n = mitt_3011((x_min, y_max), (r1 - r0, c1 - c0), m.falt.cell_m)
        lat, lon = lat_lon_fran_3011(e, n)
        datum = str(kontext.parametrar["datum"])
        klockslag = [float(t) for t in kontext.parametrar["klockslag"]]
        utc_off = kontext.parametrar.get("utc_offset_h")
        offset_h = lon / 15.0 if utc_off is None else float(utc_off)
        try:
            bas_s = np.datetime64(datum, "s").astype(np.int64)
            tider = np.array(
                [
                    np.datetime64(0, "s")
                    + np.timedelta64(int(round(bas_s + (t - offset_h) * 3600.0)), "s")
                    for t in klockslag
                ]
            )
            sol_h, sol_az = solposition(
                tider, lat, lon, refraktion=bool(kontext.parametrar["refraktion"])
            )
        except (SolFel, ValueError) as exc:
            raise LosarFel(str(exc)) from exc
        form = (r1 - r0, c1 - c0)
        ack = SolsynlighetAckumulator(antal, sol_h, sol_az, form)
        ack0 = SolsynlighetAckumulator(antal, sol_h, sol_az, form)
        pas = _horisontpass(
            kontext,
            m,
            [lambda i, _p, h: ack.mata(i, h)],
            [lambda i, _p, h: ack0.mata(i, h)],
            aktiva_riktningar=ack.aktiva_riktningar(),
            hoppa_over=[lambda i: ack.mata(i, None)],
            hoppa_over_utan_standard=[lambda i: ack0.mata(i, None)],
        )
        mask = ack.synlighet().astype(np.uint8)
        kontext.utmapp.mkdir(parents=True, exist_ok=True)
        np.save(kontext.utmapp / "skuggmask.npy", mask)
        (kontext.utmapp / "skuggmask.json").write_text(
            json.dumps(
                {
                    "kontrakt": "dagsljus-skuggmask/v1",
                    "varden": {"1": "sol", "0": "skugga"},
                    "form": ["klockslag", "rad", "kolumn"],
                    "datum": datum,
                    "klockslag": klockslag,
                    "utc_offset_h": offset_h,
                    "tidsreferens": "lokal_medelsoltid" if utc_off is None else f"UTC{offset_h:+g}",
                    "x_min_m": x_min,
                    "y_max_m": y_max,
                    "cellstorlek_m": m.falt.cell_m,
                    "epsg": 3011,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        _skriv_raster(kontext.utmapp / "trunkering.tif", pas.trunk / antal, m)
        flagga = (
            None
            if pas.standard_riktningar is None
            else np.any(ack0.synlighet().astype(np.uint8) != mask, axis=0)
        )
        n_std, std_var = _skriv_standardflagga(kontext, m, flagga, form)
        var = _gemensamma_varningar(kontext, m) + _trunk_varning(pas.trunk, antal) + std_var
        return LosarUtdata(
            filer={
                "skuggmask": "skuggmask.npy",
                "skuggmask_meta": "skuggmask.json",
                "trunkering": "trunkering.tif",
                "standardhojd": "standardhojd.tif",
            },
            matvarden={
                "solandel_medel": float(mask.mean()),
                "antal_klockslag": len(klockslag),
                "lat_grader": lat,
                "lon_grader": lon,
                "celler_standardhojd": n_std,
                "berakningsplats": pas.berakningsplats,
            },
            varningar=tuple(var),
        )


_FASAD_KOLUMNER = ("fasad_id", "x_m", "y_m", "z_m", "normal_azimut_grader")


def las_fasadpunkter(sokvag: Path) -> list[dict[str, Any]]:
    """Läser fasadpunkter (CSV; kolumner `fasad_id,x_m,y_m,z_m,normal_azimut_grader`).

    Position (x_m, y_m, z_m) är en punkt på fasadytan (z = fönstrets mitt) i EPSG:3011/RH 2000,
    `normal_azimut_grader` är fasadens utåtriktade normal, norr = 0, medurs.
    """
    if sokvag.suffix.lower() != ".csv":
        raise LosarFel(f"fasadpunkter måste vara en CSV-fil (.csv), fick {sokvag.name}.")
    with sokvag.open("r", encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh)
        saknas = [c for c in _FASAD_KOLUMNER if c not in (rd.fieldnames or [])]
        if saknas:
            raise LosarFel(
                f"{sokvag.name} saknar kolumner {saknas}; krävs: {list(_FASAD_KOLUMNER)}."
            )
        rader: list[dict[str, Any]] = []
        for nr, rad in enumerate(rd, start=2):
            try:
                x, y, z, az = (float(rad[c]) for c in _FASAD_KOLUMNER[1:])
            except (TypeError, ValueError) as exc:
                raise LosarFel(f"{sokvag.name} rad {nr}: ogiltigt tal ({exc}).") from exc
            if not all(math.isfinite(v) for v in (x, y, z, az)):
                raise LosarFel(f"{sokvag.name} rad {nr}: x, y, z och azimut måste vara ändliga.")
            if not (0.0 <= az < 360.0):
                raise LosarFel(f"{sokvag.name} rad {nr}: azimut {az} utanför [0, 360).")
            rader.append({"fasad_id": rad["fasad_id"], "x": x, "y": y, "z": z, "az": az})
    ids = [r["fasad_id"] for r in rader]
    if len(set(ids)) != len(ids):
        raise LosarFel(f"{sokvag.name}: fasad_id måste vara unika.")
    return rader


def _agd_av_ruta(k: LosarKontext, x: float, y: float) -> bool:
    """Rutan äger punkten om den ligger djupast inne i den (K1 har 25 m överlapp); lika djupt
    avgörs av ruta-id. Samma regel som kärnans jämförelser använder för celler."""
    assert k.ruta is not None

    def djup(r: Any) -> float:
        return float(min(x - r.x_min, r.x_max - x, y - r.y_min, r.y_max - y))

    egen = djup(k.ruta)
    if egen < 0:
        return False
    for g in k.grannar:
        d = djup(g)
        if d < 0:
            continue
        if g.ruta_id < k.ruta.ruta_id and d >= egen:
            return False
        if g.ruta_id > k.ruta.ruta_id and d > egen:
            return False
    return True


_UT_KOLUMNER = (
    *_FASAD_KOLUMNER,
    "vsc_procent",
    "trunkerade_riktningar",
    "status",
    "standardhojd",
    "byggnad_id",
)


def _k11_punkter(k: LosarKontext, m: Mosaik) -> list[dict[str, Any]]:
    """Fasadpunkter (K11) ur byggnadsregistret för rutan och dess grannar inom radien. Rutan
    äger punkterna som ligger djupast inne i den; `byggnader` (valfritt urval av byggnad_id)
    begränsar punkterna. Punkter på byggnader med standardhöjd flaggas."""
    from karna.geometri import GeometriFel, fasadpunkter

    p = k.parametrar
    try:
        f = fasadpunkter(
            [r.mapp for r in k.rutor_att_lasa()],
            float(p["fasad_steg_m"]),
            float(p["vaningshojd_m"]),
            hojd_over_golv_m=float(p["hojd_over_golv_m"]),
        )
    except GeometriFel as exc:
        raise LosarFel(f"fasadpunkter (K11): {exc}") from exc
    urval = set(p["byggnader"])
    ut: list[dict[str, Any]] = []
    for i in range(len(f)):
        bid = str(f.byggnad_id[i])
        if urval and bid not in urval:
            continue
        x, y = float(f.e_m[i]), float(f.n_m[i])
        if not _agd_av_ruta(k, x, y):
            continue
        ut.append(
            {
                "fasad_id": str(f.fasadpunkt_id[i]),
                "x": x,
                "y": y,
                "z": float(f.z_rh2000[i]),
                "az": float(f.normal_azimut_grader[i]),
                "byggnad_id": bid,
                "standard": bid in m.standard_byggnader,
            }
        )
    return ut


@dataclass(frozen=True)
class _VscResultat:
    vsc: NDArray[np.float64]
    trunk_ant: NDArray[np.int64]
    status: list[str]


def _vsc_pa_falt(
    falt: Any,
    x0: NDArray[np.float64],
    y0: NDArray[np.float64],
    z0: NDArray[np.float64],
    az_n: NDArray[np.float64],
    radie: float,
    kf: int,
) -> _VscResultat:
    from dagsljus.horisont import MaxPyramid

    n = x0.size
    vsc = np.full(n, np.nan)
    trunk_ant = np.zeros(n, dtype=np.int64)
    status = ["ok"] * n
    if not n:
        return _VscResultat(vsc, trunk_ant, status)
    ox, oy = falt.cellkoord(x0, y0)
    inne = (ox >= 0) & (ox < falt.bredd) & (oy >= 0) & (oy < falt.hojd)
    for i in np.nonzero(~inne)[0]:
        status[int(i)] = "utanfor_data"
    chunk = max(1, _VSC_CHUNK_STRALAR // kf)
    ids = np.nonzero(inne)[0]
    pyr = MaxPyramid.bygg(falt)
    for a in range(0, ids.size, chunk):
        s = ids[a : a + chunk]
        phi = halvplansriktningar(az_n[s], kf)
        res = horisont_mipmap(
            falt,
            np.repeat(x0[s], kf),
            np.repeat(y0[s], kf),
            np.repeat(z0[s], kf),
            phi.ravel(),
            radie,
            pyramid=pyr,
        )
        h = res.h_rad.reshape(len(s), kf)
        v = vsc_fran_halvplansprofil(h)
        inuti = res.inuti_hinder.reshape(len(s), kf)[:, 0]
        tr = res.trunkerad.reshape(len(s), kf).sum(axis=1)
        for q, idx in enumerate(s):
            trunk_ant[idx] = int(tr[q])
            if inuti[q]:
                status[int(idx)] = "inuti_hinder"
            else:
                vsc[idx] = float(v[q])
    del pyr
    if falt.z_m.size >= _DAGSLJUS_GC_TROSKEL_CELLER:
        gc.collect()
    return _VscResultat(vsc, trunk_ant, status)


class VscFasad(_DagsljusLosare):
    namn: ClassVar[str] = "dagsljus.vsc_fasad"
    beskrivning: ClassVar[str] = (
        "Vertical Sky Component (BRE, mulen CIE-himmel) på fasadpunkter: K11 ur byggnadsregistret "
        "eller en CSV-fil."
    )
    valfria_indataroller: ClassVar[tuple[str, ...]] = ("fasadpunkter",)
    utdataroller: ClassVar[tuple[str, ...]] = ("vsc",)
    _egna: ClassVar[set[str]] = {
        "halvplansriktningar",
        "utanfor_m",
        "fasad_steg_m",
        "vaningshojd_m",
        "hojd_over_golv_m",
        "byggnader",
    }

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        ut = super().validera_parametrar(parametrar)
        standard_k = max(4, int(ut["azimutriktningar"]) // 2)
        ut["halvplansriktningar"] = _heltal(parametrar, "halvplansriktningar", standard_k, minst=4)
        ut["utanfor_m"] = _tal(parametrar, "utanfor_m", 0.5 * float(ut["cellstorlek_m"]), over=0.0)
        ut["fasad_steg_m"] = _tal(parametrar, "fasad_steg_m", 3.0, over=0.0)
        ut["vaningshojd_m"] = _tal(parametrar, "vaningshojd_m", 3.0, over=0.0)
        ut["hojd_over_golv_m"] = _tal(parametrar, "hojd_over_golv_m", 1.5, over=0.0)
        urval = parametrar.get("byggnader", [])
        if not isinstance(urval, list | tuple) or not all(isinstance(b, str) for b in urval):
            raise LosarFel(f"byggnader måste vara en lista av byggnad_id (text), fick {urval!r}.")
        ut["byggnader"] = sorted(set(urval))
        return ut

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        m = _mosaik(kontext)
        csv_fil = kontext.indata.get("fasadpunkter")
        if csv_fil is not None:
            if kontext.parametrar["byggnader"]:
                raise LosarFel(
                    "byggnader (urval av byggnad_id) gäller bara fasadpunkter ur K11; med en "
                    "CSV-fil i indata styr filen vilka punkter som räknas."
                )
            punkter = [
                {**p, "byggnad_id": "", "standard": False}
                for p in las_fasadpunkter(csv_fil)
                if _agd_av_ruta(kontext, p["x"], p["y"])
            ]
            kalla = "csv"
        else:
            punkter = _k11_punkter(kontext, m)
            kalla = "k11"
        radie = _radie_m(kontext.parametrar)
        kf = int(kontext.parametrar["halvplansriktningar"])
        ut_m = float(kontext.parametrar["utanfor_m"])
        n = len(punkter)
        az_n = np.radians([p["az"] for p in punkter])
        # Minst halva cellens projektion längs fasadnormalen (0,5·cell axelparallellt,
        # √2/2·cell vid 45°) så att diagonala väggar inte fastnar i egen rastercell.
        cell_proj = (
            0.5 * (1.0 + 1e-6) * m.falt.cell_m * (np.abs(np.sin(az_n)) + np.abs(np.cos(az_n)))
        )
        ut_eff = np.maximum(ut_m, cell_proj)
        x0 = np.array([p["x"] for p in punkter]) + ut_eff * np.sin(az_n)
        y0 = np.array([p["y"] for p in punkter]) + ut_eff * np.cos(az_n)
        z0 = np.array([p["z"] for p in punkter])
        res = _vsc_pa_falt(m.falt, x0, y0, z0, az_n, radie, kf)
        vsc, trunk_ant, status = res.vsc, res.trunk_ant, res.status
        # Byggnader med standardhöjd: flagga punkter på dem och punkter vars VSC ändras när
        # byggnaderna tas bort (samma fält utan dem).
        std_flagga = np.array([bool(p["standard"]) for p in punkter], dtype=bool)
        if m.falt_utan_standard is not None and n:
            utan = _vsc_pa_falt(m.falt_utan_standard, x0, y0, z0, az_n, radie, kf)
            std_flagga |= (utan.vsc != vsc) & ~(np.isnan(utan.vsc) & np.isnan(vsc))
            std_flagga |= np.array([a != b for a, b in zip(utan.status, status, strict=True)])
        with (kontext.utmapp / "vsc.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(_UT_KOLUMNER)
            for i, p in enumerate(punkter):
                w.writerow(
                    [
                        p["fasad_id"],
                        repr(p["x"]),
                        repr(p["y"]),
                        repr(p["z"]),
                        repr(p["az"]),
                        "" if math.isnan(vsc[i]) else f"{100.0 * vsc[i]:.4f}",
                        int(trunk_ant[i]),
                        status[i],
                        int(std_flagga[i]),
                        p["byggnad_id"],
                    ]
                )
        giltiga = vsc[np.isfinite(vsc)]
        var = _gemensamma_varningar(kontext, m)
        for namn, text in (
            (
                "inuti_hinder",
                "startpunkten ligger under höjden i sin egen cell (punkten sitter i en "
                "byggnad/under ett tak i DSM); VSC lämnas tom, kontrollera läget",
            ),
            ("utanfor_data", "startpunkten ligger utanför höjdfältet; VSC lämnas tom"),
        ):
            antal = status.count(namn)
            if antal:
                var.append(f"{antal} fasadpunkter: {text}")
        n_trunk = int(np.count_nonzero(trunk_ant))
        if n_trunk:
            var.append(
                f"{n_trunk} fasadpunkter har strålar som lämnar höjdfältet före påverkansradien "
                "(kolumnen trunkerade_riktningar)"
            )
        n_std = int(np.count_nonzero(std_flagga))
        if m.standard_byggnader:
            var += _standard_varning(m, n_std, n, "fasadpunkter")
            var[-1] += " eller sitter på en sådan byggnad"
        return LosarUtdata(
            filer={"vsc": "vsc.csv"},
            matvarden={
                "fasadpunkter": n,
                "fasadkalla": kalla,
                "fasadpunkter_standardhojd": n_std,
                "vsc_min_procent": float(100.0 * giltiga.min()) if giltiga.size else None,
                "vsc_medel_procent": float(100.0 * giltiga.mean()) if giltiga.size else None,
            },
            varningar=tuple(var),
        )


_SOL_FASAD_KOLUMNER = (
    *_FASAD_KOLUMNER,
    "soltimmar_h",
    "sammanhangande_h",
    "trunkerade_riktningar",
    "status",
    "standardhojd",
    "byggnad_id",
)


@dataclass(frozen=True)
class _SolFasadResultat:
    soltimmar_h: NDArray[np.float64]
    sammanhangande_h: NDArray[np.float64]
    trunk_ant: NDArray[np.int64]
    status: list[str]


def _sol_fasad_pa_falt(
    falt: Any,
    x0: NDArray[np.float64],
    y0: NDArray[np.float64],
    z0: NDArray[np.float64],
    az_n: NDArray[np.float64],
    radie: float,
    kf: int,
    per_dag: list[tuple[NDArray[np.float64], NDArray[np.float64]]],
    steg_h: float,
) -> _SolFasadResultat:
    from dagsljus.horisont import MaxPyramid

    n = x0.size
    sol_h_ut = np.full(n, np.nan, dtype=np.float64)
    sam_h_ut = np.full(n, np.nan, dtype=np.float64)
    trunk_ant = np.zeros(n, dtype=np.int64)
    status = ["ok"] * n
    if not n:
        return _SolFasadResultat(sol_h_ut, sam_h_ut, trunk_ant, status)
    ox, oy = falt.cellkoord(x0, y0)
    inne = (ox >= 0) & (ox < falt.bredd) & (oy >= 0) & (oy < falt.hojd)
    for i in np.nonzero(~inne)[0]:
        status[int(i)] = "utanfor_data"
    chunk = max(1, _VSC_CHUNK_STRALAR // kf)
    ids = np.nonzero(inne)[0]
    pyr = MaxPyramid.bygg(falt)
    n_dagar = len(per_dag)
    for a in range(0, ids.size, chunk):
        s = ids[a : a + chunk]
        phi = halvplansriktningar(az_n[s], kf)
        res = horisont_mipmap(
            falt,
            np.repeat(x0[s], kf),
            np.repeat(y0[s], kf),
            np.repeat(z0[s], kf),
            phi.ravel(),
            radie,
            pyramid=pyr,
        )
        h = res.h_rad.reshape(len(s), kf)
        inuti = res.inuti_hinder.reshape(len(s), kf)[:, 0]
        tr = res.trunkerad.reshape(len(s), kf).sum(axis=1)
        tot_h = np.zeros(len(s), dtype=np.float64)
        sam_h = np.zeros(len(s), dtype=np.float64)
        for sh, saz in per_dag:
            syn = solsynlighet_fasad(h, az_n[s], sh, saz)
            tot_h += syn.sum(axis=0, dtype=np.int32) * steg_h
            sam_h += sammanhangande_soltimmar_fran_synlighet(syn, steg_h)
        tot_h /= n_dagar
        sam_h /= n_dagar
        for q, idx in enumerate(s):
            trunk_ant[idx] = int(tr[q])
            if inuti[q]:
                status[int(idx)] = "inuti_hinder"
            else:
                sol_h_ut[idx] = float(tot_h[q])
                sam_h_ut[idx] = float(sam_h[q])
    del pyr
    if falt.z_m.size >= _DAGSLJUS_GC_TROSKEL_CELLER:
        gc.collect()
    return _SolFasadResultat(sol_h_ut, sam_h_ut, trunk_ant, status)


class SoltimmarFasad(_DagsljusLosare):
    namn: ClassVar[str] = "dagsljus.soltimmar_fasad"
    beskrivning: ClassVar[str] = (
        "Direkta soltimmar och längsta sammanhängande soltid på K11-fasadpunkter "
        "(med fasadnormalens synfält + horisontprofil)."
    )
    valfria_indataroller: ClassVar[tuple[str, ...]] = ("fasadpunkter",)
    utdataroller: ClassVar[tuple[str, ...]] = ("soltimmar_fasad",)
    _egna: ClassVar[set[str]] = {
        "datum",
        "steg_min",
        "refraktion",
        "start_timme",
        "slut_timme",
        "halvplansriktningar",
        "utanfor_m",
        "fasad_steg_m",
        "vaningshojd_m",
        "hojd_over_golv_m",
        "byggnader",
    }
    _standard_k: ClassVar[int] = STANDARD_K_SOLTIMMAR

    def validera_parametrar(self, parametrar: Mapping[str, Any]) -> dict[str, Any]:
        ut = super().validera_parametrar(parametrar)
        ut.update(_validera_solparametrar(parametrar))
        standard_k = max(4, int(ut["azimutriktningar"]) // 2)
        ut["halvplansriktningar"] = _heltal(parametrar, "halvplansriktningar", standard_k, minst=4)
        ut["utanfor_m"] = _tal(parametrar, "utanfor_m", 0.5 * float(ut["cellstorlek_m"]), over=0.0)
        ut["fasad_steg_m"] = _tal(parametrar, "fasad_steg_m", 3.0, over=0.0)
        ut["vaningshojd_m"] = _tal(parametrar, "vaningshojd_m", 3.0, over=0.0)
        ut["hojd_over_golv_m"] = _tal(parametrar, "hojd_over_golv_m", 1.5, over=0.0)
        urval = parametrar.get("byggnader", [])
        if not isinstance(urval, list | tuple) or not all(isinstance(b, str) for b in urval):
            raise LosarFel(f"byggnader måste vara en lista av byggnad_id (text), fick {urval!r}.")
        ut["byggnader"] = sorted(set(urval))
        return ut

    def kor(self, kontext: LosarKontext) -> LosarUtdata:
        m = _mosaik(kontext)
        csv_fil = kontext.indata.get("fasadpunkter")
        if csv_fil is not None:
            if kontext.parametrar["byggnader"]:
                raise LosarFel(
                    "byggnader (urval av byggnad_id) gäller bara fasadpunkter ur K11; med en "
                    "CSV-fil i indata styr filen vilka punkter som räknas."
                )
            punkter = [
                {**p, "byggnad_id": "", "standard": False}
                for p in las_fasadpunkter(csv_fil)
                if _agd_av_ruta(kontext, p["x"], p["y"])
            ]
            kalla = "csv"
        else:
            punkter = _k11_punkter(kontext, m)
            kalla = "k11"
        r0, r1, c0, c1 = m.fonster
        x_min, y_max = fonster_koordinater(m)
        e, nn = mitt_3011((x_min, y_max), (r1 - r0, c1 - c0), m.falt.cell_m)
        lat, lon = lat_lon_fran_3011(e, nn)
        datum = list(kontext.parametrar["datum"])
        steg_min = float(kontext.parametrar["steg_min"])
        start_h = float(kontext.parametrar.get("start_timme", 0.0))
        slut_h = float(kontext.parametrar.get("slut_timme", 24.0))
        per_dag = _solpositioner_per_datum(
            datum,
            lat,
            lon,
            steg_min,
            refraktion=bool(kontext.parametrar["refraktion"]),
            start_timme=start_h,
            slut_timme=slut_h,
        )
        radie = _radie_m(kontext.parametrar)
        kf = int(kontext.parametrar["halvplansriktningar"])
        ut_m = float(kontext.parametrar["utanfor_m"])
        n = len(punkter)
        az_n = np.radians([p["az"] for p in punkter])
        cell_proj = (
            0.5 * (1.0 + 1e-6) * m.falt.cell_m * (np.abs(np.sin(az_n)) + np.abs(np.cos(az_n)))
        )
        ut_eff = np.maximum(ut_m, cell_proj)
        x0 = np.array([p["x"] for p in punkter]) + ut_eff * np.sin(az_n)
        y0 = np.array([p["y"] for p in punkter]) + ut_eff * np.cos(az_n)
        z0 = np.array([p["z"] for p in punkter])
        steg_h = steg_min / 60.0
        res = _sol_fasad_pa_falt(m.falt, x0, y0, z0, az_n, radie, kf, per_dag, steg_h)
        std_flagga = np.array([bool(p["standard"]) for p in punkter], dtype=bool)
        if m.falt_utan_standard is not None and n:
            utan = _sol_fasad_pa_falt(
                m.falt_utan_standard, x0, y0, z0, az_n, radie, kf, per_dag, steg_h
            )
            std_flagga |= (utan.soltimmar_h != res.soltimmar_h) & ~(
                np.isnan(utan.soltimmar_h) & np.isnan(res.soltimmar_h)
            )
            std_flagga |= np.array(
                [a != b for a, b in zip(utan.status, res.status, strict=True)]
            )
        kontext.utmapp.mkdir(parents=True, exist_ok=True)
        with (kontext.utmapp / "soltimmar_fasad.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(_SOL_FASAD_KOLUMNER)
            for i, p in enumerate(punkter):
                w.writerow(
                    [
                        p["fasad_id"],
                        repr(p["x"]),
                        repr(p["y"]),
                        repr(p["z"]),
                        repr(p["az"]),
                        "" if math.isnan(res.soltimmar_h[i]) else f"{res.soltimmar_h[i]:.4f}",
                        ""
                        if math.isnan(res.sammanhangande_h[i])
                        else f"{res.sammanhangande_h[i]:.4f}",
                        int(res.trunk_ant[i]),
                        res.status[i],
                        int(std_flagga[i]),
                        p["byggnad_id"],
                    ]
                )
        giltiga = res.soltimmar_h[np.isfinite(res.soltimmar_h)]
        var = _gemensamma_varningar(kontext, m)
        for namn, text in (
            (
                "inuti_hinder",
                "startpunkten ligger under höjden i sin egen cell (punkten sitter i en "
                "byggnad/under ett tak i DSM); soltimmar lämnas tom, kontrollera läget",
            ),
            ("utanfor_data", "startpunkten ligger utanför höjdfältet; soltimmar lämnas tom"),
        ):
            antal_status = res.status.count(namn)
            if antal_status:
                var.append(f"{antal_status} fasadpunkter ({namn}): {text}")
        n_trunk = int(np.count_nonzero(res.trunk_ant))
        if n_trunk:
            var.append(
                f"{n_trunk} fasadpunkter har strålar som lämnar höjdfältet före påverkansradien "
                "(kolumnen trunkerade_riktningar)"
            )
        antal_az = int(kontext.parametrar["azimutriktningar"])
        if 2 * kf < 128:
            var.append(
                f"halvplansriktningar={kf} (motsvarar {2 * kf} azimutriktningar, "
                f"azimutriktningar={antal_az}): soltimmar nära skuggkanter kan avvika flera "
                "tiondels timmar (uppmätt på syntetiskt block, docs/DAGSLJUS.md); använd >= 90 "
                "halvplansriktningar (>= 180 azimutriktningar) för skuggstudier"
            )
        n_std = int(np.count_nonzero(std_flagga))
        if m.standard_byggnader:
            var += _standard_varning(m, n_std, n, "fasadpunkter")
            var[-1] += " eller sitter på en sådan byggnad"
        return LosarUtdata(
            filer={"soltimmar_fasad": "soltimmar_fasad.csv"},
            matvarden={
                "fasadpunkter": n,
                "fasadkalla": kalla,
                "fasadpunkter_standardhojd": n_std,
                "soltimmar_min_h": float(giltiga.min()) if giltiga.size else None,
                "soltimmar_medel_h": float(giltiga.mean()) if giltiga.size else None,
                "soltimmar_max_h": float(giltiga.max()) if giltiga.size else None,
                "datum": len(datum),
                "start_timme": start_h,
                "slut_timme": slut_h,
            },
            varningar=tuple(var),
        )


LOSARE: list[Analyslosare] = [
    Horisont(),
    SoltimmarMark(),
    SammanhangandeSoltimmarMark(),
    SkuggmaskKlockslag(),
    SvfMark(),
    VscFasad(),
    SoltimmarFasad(),
]
