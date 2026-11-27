"""Läs- och cache-klient för förberäknade kommunrutor i objektlagring (S3 / lokal).

Kopplar ihop förberäknade rutor från GPU/Colab (`src/karna/batch.py`) i Hetzner Object
Storage (eller lokal `LokalLagring` för offline-test) med lokal LRU-cache på webbservern
(`src/gemensamt/lokal_cache.py`).

Nyckelformat i objektlagringen (matchar `gemensamt.objektlagring.geografi_nyckel`):
    `geografi/<kommun>/<rut_id>/<fil>`

Dataskyddsregel (`AGENTS.md`):
    Endast öppen data (`dataklass == "oppen"`) får läsas från S3/objektlagring.
    Rutor eller nycklar som markerats med annan dataklass (t.ex. `intern`, `skyddad`)
    avvisas alltid med `DataskyddFel`.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from gemensamt.hash import sha256_fil
from gemensamt.lokal_cache import (
    las_lokal_cache_konfig,
    markera_cache_anvand,
    stada_lokal_cache,
)
from gemensamt.manifest import ManifestFel, las_manifest
from gemensamt.objektlagring import (
    LokalLagring,
    ObjektLagring,
    ObjektLagringFel,
    S3Lagring,
    geografi_nyckel,
    las_lagring_konfig,
    skapa_lagring,
)

STANDARD_RUTFILER: tuple[str, ...] = (
    "mark.tif",
    "sankgraf.parquet",
    "max_djup.tif",
    "run.json",
)
PRECOMPUTED_GPU_FILER: tuple[str, ...] = (
    "vatten2d/max_djup.tif",
    "dagsljus/svf.tif",
    "run.json",
)
PRECOMPUTED_MODUL_FILER: Mapping[str, str] = {
    "vatten2d": "max_djup.tif",
    "dagsljus": "svf.tif",
}
STANDARD_RUTSTORLEK_M: float = 500.0
STANDARD_NEGATIV_CACHE_TTL_S: float = 300.0
STANDARD_FORBERAKNAT_DIR: Path = Path("data/forberaknat")
FLODEN_FORBERAKNAT_DIR_ENV: str = "FLODEN_FORBERAKNAT_DIR"

_TIFF_MAGI: tuple[bytes, ...] = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
_RUT_ID_RE = re.compile(r"^mm_(-?\d+)_(-?\d+)$")
_FORBJUDNA_DATAKLASS_SEGMENT: frozenset[str] = frozenset(
    {
        "intern",
        "skyddad",
        "dataklass=intern",
        "dataklass=skyddad",
        "dataklass_intern",
        "dataklass_skyddad",
    }
)


class ForberaknadLagerFel(Exception):
    """Fel vid läsning eller cachning av förberäknade kommunrutor."""


class DataskyddFel(ForberaknadLagerFel):
    """Avvisning av icke-öppen data (`dataklass != "oppen"`)."""


def _validera_segment(varde: str, namn: str) -> str:
    if not isinstance(varde, str) or "\x00" in varde:
        raise ForberaknadLagerFel(f"Ogiltigt {namn}: {varde!r}.")
    ren = varde.strip()
    if not ren or "/" in ren or "\\" in ren or ".." in ren or not _SEGMENT_RE.match(ren):
        raise ForberaknadLagerFel(f"Ogiltigt {namn}: {varde!r}.")
    return ren


def _validera_rel_fil(fil: str) -> str:
    if not isinstance(fil, str) or "\x00" in fil:
        raise ForberaknadLagerFel(f"Ogiltigt filnamn: {fil!r}.")
    if "\\" in fil:
        raise ForberaknadLagerFel(f"Ogiltigt filnamn (innehåller '\\'): {fil!r}.")
    ren = fil.strip()
    if not ren:
        raise ForberaknadLagerFel("Filnamn får inte vara tomt.")
    if ren.startswith("/"):
        raise ForberaknadLagerFel(f"Ogiltigt filnamn (absolut sökväg / path traversal): {fil!r}.")
    delar = [d for d in ren.split("/") if d]
    if not delar or any(d in (".", "..") for d in delar):
        raise ForberaknadLagerFel(f"Ogiltigt filnamn (path traversal): {fil!r}.")
    return "/".join(delar)


def _kontrollera_nyckel_dataskydd(nyckel: str) -> None:
    """Avvisar nycklar vars segment uttryckligen anger icke-öppen dataklass."""
    for seg in nyckel.strip().split("/"):
        low = seg.strip().lower()
        if not low:
            continue
        if low in _FORBJUDNA_DATAKLASS_SEGMENT:
            raise DataskyddFel(
                f"Dataskyddsspärr: objektnyckeln {nyckel!r} anger icke-öppen data ({seg!r})."
            )
        if low.startswith("dataklass=") and low != "dataklass=oppen":
            raise DataskyddFel(
                f"Dataskyddsspärr: objektnyckeln {nyckel!r} har {seg!r} (endast 'oppen' tillåts)."
            )


def _kontrollera_manifest_dataskydd(manifest_fil: Path, *, sammanhang: str) -> dict[str, Any]:
    """Kontrollerar att `run.json` är giltig JSON och inte anger `dataklass != "oppen"`."""
    try:
        raa = json.loads(manifest_fil.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ForberaknadLagerFel(f"Kunde inte läsa manifest för {sammanhang}: {exc}") from exc

    if not isinstance(raa, Mapping):
        raise ForberaknadLagerFel(f"Ogiltigt manifest (ej JSON-objekt) för {sammanhang}.")

    # Kontrollera både toppnivå och `parametrar.dataklass` (K0-manifest från batch.py).
    dataklass: Any = None
    parametrar = raa.get("parametrar")
    if isinstance(parametrar, Mapping) and "dataklass" in parametrar:
        dataklass = parametrar.get("dataklass")
    elif "dataklass" in raa:
        dataklass = raa.get("dataklass")

    if dataklass is not None and str(dataklass).strip().lower() != "oppen":
        raise DataskyddFel(
            f"Dataskyddsspärr: ruta {sammanhang} har dataklass={dataklass!r}; "
            "endast öppen data ('oppen') får läsas från förberäknat lager."
        )

    # Validera K0-strukturen om fältet 'kontrakt' finns (utan att tyst svälja trasiga manifest).
    if "kontrakt" in raa:
        try:
            las_manifest(manifest_fil)
        except ManifestFel as exc:
            raise ForberaknadLagerFel(f"Ogiltigt K0-manifest för {sammanhang}: {exc}") from exc
    return dict(raa)


def _forvantad_sha256(manifest: Mapping[str, Any] | None, filnamn: str) -> str | None:
    """Returnerar förväntad SHA-256 ur `run.json` (`utdata`) om filen finns listad."""
    if not isinstance(manifest, Mapping):
        return None
    utdata = manifest.get("utdata")
    if not isinstance(utdata, list):
        return None
    for post in utdata:
        if (
            isinstance(post, Mapping)
            and post.get("fil") == filnamn
            and isinstance(post.get("sha256"), str)
            and post["sha256"].strip()
        ):
            return str(post["sha256"]).strip().lower()
    return None


def validera_forberaknad_geotiff(fil: Path, *, las_data: bool = True) -> dict[str, Any]:
    """Verifierar att en förberäknad GeoTIFF/ZSTD-COG går att öppna och avkoda utan fel.

    Kastar `ForberaknadLagerFel` vid tom, trunkerad eller korrupt rasterfil.
    """
    try:
        storlek = fil.stat().st_size
    except OSError as exc:
        raise ForberaknadLagerFel(f"Kunde inte läsa rasterfilen {fil.name}: {exc}") from exc
    if storlek <= 0:
        raise ForberaknadLagerFel(f"Korrupt eller tom GeoTIFF/COG-fil: {fil.name}.")

    try:
        import rasterio
    except ImportError as exc:
        raise ForberaknadLagerFel(
            f"Kan inte verifiera GeoTIFF/COG ({fil.name}): 'rasterio' saknas."
        ) from exc

    try:
        with rasterio.open(fil) as ds:
            if ds.width <= 0 or ds.height <= 0 or ds.count < 1:
                raise ForberaknadLagerFel(
                    f"Ogiltiga dimensioner i GeoTIFF/COG {fil.name}: "
                    f"{ds.width}x{ds.height} ({ds.count} band)."
                )
            if las_data:
                ds.read(1)
            return {
                "bredd": int(ds.width),
                "hojd": int(ds.height),
                "band": int(ds.count),
                "crs": str(ds.crs) if ds.crs is not None else None,
            }
    except ForberaknadLagerFel:
        raise
    except Exception as exc:  # noqa: BLE001 - RasterioIOError / GDAL-fel vid trasig ZSTD-ström
        raise ForberaknadLagerFel(
            f"Korrupt eller trunkerad GeoTIFF/COG ({fil.name}): {exc}"
        ) from exc


def _validera_lokal_sokvag_inom_rot(
    rot: Path, rel_delar: Sequence[str], *, sammanhang: str
) -> None:
    """Kontrollerar att varken slutsökväg eller mellanliggande symlänkar lämnar `rot`."""
    try:
        rot_abs = rot.resolve()
    except OSError as exc:
        raise ForberaknadLagerFel(f"Kunde inte lösa lagringsroten för {sammanhang}: {exc}") from exc
    if not rot_abs.is_dir():
        return
    aktuell = rot_abs
    for seg in rel_delar:
        aktuell = aktuell / seg
        if aktuell.is_symlink():
            try:
                mal = aktuell.resolve()
            except OSError as exc:
                raise ForberaknadLagerFel(
                    f"Säkerhetsfel: oläsbar symlänk i lagringen för {sammanhang}: {exc}"
                ) from exc
            if not mal.is_relative_to(rot_abs):
                raise ForberaknadLagerFel(
                    f"Säkerhetsfel: symlänk i lagringen pekar utanför roten ({sammanhang})."
                )
    if aktuell.exists() or aktuell.is_symlink():
        try:
            mal = aktuell.resolve()
        except OSError as exc:
            raise ForberaknadLagerFel(
                f"Säkerhetsfel: ogiltig sökväg i lagringen för {sammanhang}: {exc}"
            ) from exc
        if not mal.is_relative_to(rot_abs):
            raise ForberaknadLagerFel(
                f"Säkerhetsfel: sökväg i lagringen pekar utanför roten ({sammanhang})."
            )


class _SaknadKatalogLagring(ObjektLagring):
    """Lässkyddad lagring för en lokal förberäknad katalog som ännu inte finns på disk.

    Skapar aldrig katalogen och returnerar rent tomt svar ('ej förberäknad').
    """

    def __init__(self, rot: Path) -> None:
        self._rot = rot

    @property
    def rot(self) -> Path:
        return self._rot

    def finns(self, nyckel: str) -> bool:
        del nyckel
        return False

    def ladda_upp_fil(
        self,
        lokal_fil: str | Path,
        nyckel: str,
        *,
        sha256: str | None = None,
    ) -> str:
        del lokal_fil, nyckel, sha256
        raise ObjektLagringFel(f"Lokal förberäknad katalog saknas: {self._rot}")

    def ladda_ner_fil(self, nyckel: str, lokal_fil: str | Path) -> Path:
        del lokal_fil
        raise ObjektLagringFel(
            f"Objektet {nyckel!r} saknas (lokal förberäknad katalog finns inte: {self._rot})."
        )

    def radera_prefix(self, prefix: str) -> int:
        del prefix
        return 0

    def lista_prefix(self, prefix: str = "") -> list[str]:
        del prefix
        return []

    def presigned_url(self, nyckel: str, giltig_s: int = 3600) -> str:
        del giltig_s
        raise ObjektLagringFel(
            f"Objektet {nyckel!r} saknas (lokal förberäknad katalog finns inte: {self._rot})."
        )


def las_forberaknat_dir(
    konfig: Mapping[str, Any] | None = None,
    *,
    miljo: Mapping[str, str] | None = None,
    bas_rot: str | Path | None = None,
) -> Path:
    """Läser katalog för lokala förberäknade rutor (`FLODEN_FORBERAKNAT_DIR`/`data/forberaknat`)."""
    env = os.environ if miljo is None else miljo
    raw = env.get(FLODEN_FORBERAKNAT_DIR_ENV)
    if raw is not None and raw.strip():
        vald = Path(raw.strip()).expanduser()
    else:
        sektion = konfig.get("lagring", {}) if isinstance(konfig, Mapping) else {}
        fran_konfig = (
            sektion.get("forberaknat_dir") if isinstance(sektion, Mapping) else None
        )
        if isinstance(fran_konfig, str) and fran_konfig.strip():
            vald = Path(fran_konfig.strip()).expanduser()
        else:
            vald = STANDARD_FORBERAKNAT_DIR
    if bas_rot is not None and not vald.is_absolute():
        vald = Path(bas_rot) / vald
    return vald


def tolka_rut_bbox_3011(
    rut_id: str,
    *,
    rutstorlek_m: float = STANDARD_RUTSTORLEK_M,
) -> tuple[float, float, float, float] | None:
    """Tolkar `mm_<E>_<N>` till inre bbox `(e_min, n_min, e_max, n_max)` i EPSG:3011."""
    if not math.isfinite(rutstorlek_m) or rutstorlek_m <= 0.0:
        raise ForberaknadLagerFel(f"rutstorlek_m måste vara > 0, fick {rutstorlek_m}.")
    m = _RUT_ID_RE.match(rut_id.strip())
    if m is None:
        return None
    e_min = float(int(m.group(1)))
    n_min = float(int(m.group(2)))
    return (e_min, n_min, e_min + rutstorlek_m, n_min + rutstorlek_m)


def _bbox_overlappar(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    """True om två rektanglar `(xmin, ymin, xmax, ymax)` har positivt ytöverlapp."""
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


class ForberaknadKommunLager:
    """Deterministisk, trådsäker läs- och LRU-cache-klient för förberäknade kommunrutor."""

    def __init__(
        self,
        objektlagring: ObjektLagring,
        cache_rot: str | Path | None = None,
        max_gb: float | None = None,
        *,
        max_alder_dagar: float | None = None,
        stada_vid_hamtning: bool = True,
        negativ_cache_ttl_s: float = STANDARD_NEGATIV_CACHE_TTL_S,
        verifiera_geotiff: bool = False,
        konfig: Mapping[str, Any] | None = None,
        miljo: Mapping[str, str] | None = None,
        bas_rot: str | Path | None = None,
        klocka: Callable[[], float] | None = None,
    ) -> None:
        cache_konf = (
            las_lokal_cache_konfig(konfig, miljo=miljo)
            if cache_rot is None or konfig is not None or miljo is not None
            else None
        )
        vald_rot = (
            Path(cache_rot)
            if cache_rot is not None
            else (cache_konf.mapp if cache_konf is not None else Path("data/cache/s3_lru"))
        )
        if bas_rot is not None and not vald_rot.is_absolute():
            vald_rot = Path(bas_rot) / vald_rot
        vald_max_gb = (
            float(max_gb)
            if max_gb is not None
            else (cache_konf.max_gb if cache_konf is not None else 10.0)
        )
        vald_max_alder = (
            float(max_alder_dagar)
            if max_alder_dagar is not None
            else (cache_konf.max_alder_dagar if cache_konf is not None else 14.0)
        )
        if not math.isfinite(vald_max_gb) or vald_max_gb <= 0.0:
            raise ForberaknadLagerFel(f"max_gb måste vara > 0, fick {vald_max_gb}.")
        if not math.isfinite(vald_max_alder) or vald_max_alder <= 0.0:
            raise ForberaknadLagerFel(f"max_alder_dagar måste vara > 0, fick {vald_max_alder}.")
        if not math.isfinite(negativ_cache_ttl_s) or negativ_cache_ttl_s < 0.0:
            raise ForberaknadLagerFel(
                f"negativ_cache_ttl_s måste vara >= 0 och ändligt, fick {negativ_cache_ttl_s}."
            )
        self._lagring = objektlagring
        self._cache_rot = vald_rot
        self._max_gb = vald_max_gb
        self._max_alder_dagar = vald_max_alder
        self._stada_vid_hamtning = bool(stada_vid_hamtning)
        self._negativ_cache_ttl_s = float(negativ_cache_ttl_s)
        self._verifiera_geotiff = bool(verifiera_geotiff)
        self._klocka: Callable[[], float] = time.monotonic if klocka is None else klocka
        self._lock = threading.RLock()
        self._saknade_nycklar: dict[str, float] = {}

    @classmethod
    def fran_lokal_katalog(
        cls,
        katalog: str | Path = STANDARD_FORBERAKNAT_DIR,
        cache_rot: str | Path | None = None,
        max_gb: float | None = None,
        *,
        max_alder_dagar: float | None = None,
        stada_vid_hamtning: bool = True,
        negativ_cache_ttl_s: float = STANDARD_NEGATIV_CACHE_TTL_S,
        verifiera_geotiff: bool = False,
        konfig: Mapping[str, Any] | None = None,
        miljo: Mapping[str, str] | None = None,
        bas_rot: str | Path | None = None,
        klocka: Callable[[], float] | None = None,
    ) -> ForberaknadKommunLager:
        """Skapar `ForberaknadKommunLager` direkt mot en lokal katalog utan S3.

        Om `katalog` saknas skapas den inte utan lagret returnerar rent 'ej förberäknad'
        (tomma listor) utan krasch.
        """
        rot = Path(katalog).expanduser()
        if bas_rot is not None and not rot.is_absolute():
            rot = Path(bas_rot) / rot
        if rot.exists() and not rot.is_dir():
            raise ForberaknadLagerFel(
                f"Lokal förberäknad sökväg är inte en katalog: {rot}."
            )
        lagring: ObjektLagring = (
            LokalLagring(rot) if rot.is_dir() else _SaknadKatalogLagring(rot)
        )
        return cls(
            lagring,
            cache_rot=cache_rot,
            max_gb=max_gb,
            max_alder_dagar=max_alder_dagar,
            stada_vid_hamtning=stada_vid_hamtning,
            negativ_cache_ttl_s=negativ_cache_ttl_s,
            verifiera_geotiff=verifiera_geotiff,
            konfig=konfig,
            miljo=miljo,
            bas_rot=bas_rot,
            klocka=klocka,
        )

    @property
    def cache_rot(self) -> Path:
        return self._cache_rot

    @property
    def max_gb(self) -> float:
        return self._max_gb

    @property
    def max_alder_dagar(self) -> float:
        return self._max_alder_dagar

    @property
    def negativ_cache_ttl_s(self) -> float:
        return self._negativ_cache_ttl_s

    @property
    def kalla_typ(self) -> Literal["s3", "lokal"]:
        return "s3" if isinstance(self._lagring, S3Lagring) else "lokal"

    @property
    def lokal_rot(self) -> Path | None:
        if isinstance(self._lagring, LokalLagring | _SaknadKatalogLagring):
            return self._lagring.rot
        return None

    @property
    def ar_tillganglig(self) -> bool:
        """True om lagringskällan finns tillgänglig (för lokal lagring att katalogen finns)."""
        if isinstance(self._lagring, LokalLagring | _SaknadKatalogLagring):
            return self._lagring.rot.is_dir()
        return True

    def rensa_negativ_cache(self) -> None:
        """Tömmer alla negativcache-poster under lås."""
        with self._lock:
            self._saknade_nycklar.clear()

    def _ar_negativ_cachad(self, nyckel: str, nu_s: float) -> bool:
        if self._negativ_cache_ttl_s <= 0.0:
            self._saknade_nycklar.pop(nyckel, None)
            return False
        tidsstampel_s = self._saknade_nycklar.get(nyckel)
        if tidsstampel_s is None:
            return False
        if (nu_s - tidsstampel_s) < self._negativ_cache_ttl_s:
            return True
        self._saknade_nycklar.pop(nyckel, None)
        return False

    def _markera_saknad(self, nyckel: str, nu_s: float) -> None:
        if self._negativ_cache_ttl_s > 0.0:
            self._saknade_nycklar[nyckel] = nu_s

    def _rensa_saknad(self, nyckel: str) -> None:
        self._saknade_nycklar.pop(nyckel, None)

    def _kontrollera_lagring_sokvag(self, nyckel: str) -> None:
        if isinstance(self._lagring, LokalLagring):
            _validera_lokal_sokvag_inom_rot(
                self._lagring.rot, nyckel.split("/"), sammanhang=nyckel
            )

    def _kontrollera_cache_sokvag(self, rel_delar: Sequence[str], *, sammanhang: str) -> None:
        if self._cache_rot.exists():
            _validera_lokal_sokvag_inom_rot(self._cache_rot, rel_delar, sammanhang=sammanhang)

    def _rutamapp(self, kommun: str, rut_id: str) -> Path:
        return self._cache_rot / "geografi" / kommun / rut_id

    def _validera_nedladdad_fil(
        self,
        tmp_path: Path,
        filnamn: str,
        *,
        nyckel: str,
        manifest: Mapping[str, Any] | None,
        verifiera_geotiff: bool,
    ) -> dict[str, Any] | None:
        """Validerar manifest/metadata-JSON, ev. SHA-256 och GeoTIFF/ZSTD-COG före atomisk flytt."""
        if filnamn == "run.json":
            return _kontrollera_manifest_dataskydd(tmp_path, sammanhang=nyckel)

        if filnamn.lower().endswith(".json"):
            try:
                meta = json.loads(tmp_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ForberaknadLagerFel(
                    f"Korrupt metadata-JSON för {nyckel}: {exc}"
                ) from exc
            if not isinstance(meta, Mapping):
                raise ForberaknadLagerFel(
                    f"Ogiltig metadata-JSON (ej JSON-objekt) för {nyckel}."
                )

        forvantad_sha = _forvantad_sha256(manifest, filnamn)
        if forvantad_sha is not None:
            faktisk_sha = sha256_fil(tmp_path).lower()
            if faktisk_sha != forvantad_sha:
                raise ForberaknadLagerFel(
                    f"Korrupt eller trunkerad fil för {nyckel}: SHA-256 stämmer inte "
                    f"(förväntade {forvantad_sha[:12]}…, fick {faktisk_sha[:12]}…)."
                )

        if filnamn.lower().endswith((".tif", ".tiff")):
            try:
                storlek = tmp_path.stat().st_size
                with tmp_path.open("rb") as fh:
                    magi = fh.read(4)
            except OSError as exc:
                raise ForberaknadLagerFel(f"Kunde inte läsa {nyckel}: {exc}") from exc
            if storlek <= 0:
                raise ForberaknadLagerFel(f"Tom eller trunkerad GeoTIFF/COG för {nyckel}.")
            if verifiera_geotiff or self._verifiera_geotiff or magi in _TIFF_MAGI:
                validera_forberaknad_geotiff(tmp_path, las_data=True)
        return None

    def _ladda_ner_atomiskt(
        self,
        nyckel: str,
        mal: Path,
        *,
        filnamn: str | None = None,
        manifest: Mapping[str, Any] | None = None,
        verifiera_geotiff: bool = False,
    ) -> tuple[Path, dict[str, Any] | None]:
        """Laddar ner `nyckel` via temporärfil i målmappen följt av validering och `os.replace`."""
        _kontrollera_nyckel_dataskydd(nyckel)
        self._kontrollera_lagring_sokvag(nyckel)
        mal.parent.mkdir(parents=True, exist_ok=True)
        try:
            cache_abs = self._cache_rot.resolve()
            mal_abs = mal.resolve()
        except OSError as exc:
            raise ForberaknadLagerFel(f"Ogiltig cachesökväg för {nyckel}: {exc}") from exc
        if not mal_abs.is_relative_to(cache_abs) or mal.is_symlink():
            raise ForberaknadLagerFel(
                f"Säkerhetsfel: cachemål pekar utanför cacheroten ({nyckel})."
            )
        fd, tmp_namn = tempfile.mkstemp(prefix=".floden_rut_", dir=str(mal.parent))
        tmp_path = Path(tmp_namn)
        eff_fil = filnamn or mal.name
        parsed_manifest: dict[str, Any] | None = None
        try:
            os.close(fd)
            try:
                self._lagring.ladda_ner_fil(nyckel, tmp_path)
            except ObjektLagringFel as exc:
                raise ForberaknadLagerFel(
                    f"Kunde inte hämta {nyckel} från lagringen: {exc}"
                ) from exc
            parsed_manifest = self._validera_nedladdad_fil(
                tmp_path,
                eff_fil,
                nyckel=nyckel,
                manifest=manifest,
                verifiera_geotiff=verifiera_geotiff,
            )
            os.replace(tmp_path, mal)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        markera_cache_anvand(mal)
        return mal, parsed_manifest

    def lista_kommuner(self) -> list[str]:
        """Returnerar sorterad lista över kommuner under `geografi/<kommun>/<rut_id>/...`."""
        if isinstance(self._lagring, LokalLagring) and not self._lagring.rot.is_dir():
            return []
        nycklar = self._lagring.lista_prefix("geografi/")
        kommuner: set[str] = set()
        for nyckel in nycklar:
            delar = nyckel.split("/")
            if len(delar) >= 4 and delar[0] == "geografi" and delar[1] and delar[2]:
                if delar[3] == "k1":
                    continue
                if delar[1].lower() in _FORBJUDNA_DATAKLASS_SEGMENT:
                    continue
                kommuner.add(delar[1])
        return sorted(kommuner)

    def lista_rutor(self, kommun: str) -> list[str]:
        """Returnerar sorterad lista över rut-id under `geografi/<kommun>/<rut_id>/...`."""
        ren_kommun = _validera_segment(kommun, "kommun")
        _kontrollera_nyckel_dataskydd(f"geografi/{ren_kommun}")
        if isinstance(self._lagring, LokalLagring):
            if not self._lagring.rot.is_dir():
                return []
            _validera_lokal_sokvag_inom_rot(
                self._lagring.rot,
                ("geografi", ren_kommun),
                sammanhang=f"geografi/{ren_kommun}",
            )
        prefix = f"geografi/{ren_kommun}/"
        nycklar = self._lagring.lista_prefix(prefix)
        rutor: set[str] = set()
        for nyckel in nycklar:
            delar = nyckel.split("/")
            if len(delar) >= 4 and delar[0] == "geografi" and delar[1] == ren_kommun and delar[2]:
                if delar[3] == "k1":
                    continue
                if any(d.lower() in _FORBJUDNA_DATAKLASS_SEGMENT for d in delar[2:]):
                    continue
                rutor.add(delar[2])
        return sorted(rutor)

    def hamta_ruta(
        self,
        kommun: str,
        rut_id: str,
        filer: Sequence[str] = STANDARD_RUTFILER,
        *,
        verifiera_geotiff: bool = False,
    ) -> Path:
        """Hämtar efterfrågade filer för en ruta till lokal cache (atomiskt + LRU-mtime).

        Om `run.json` och efterfrågade filer redan finns lokalt sker ingen ny S3-hämtning.
        Avvisar alltid rutor med `dataklass != "oppen"`, path-traversal, symlänkar ut ur roten
        samt trasig metadata-JSON eller trunkerad/korrupt GeoTIFF/COG.
        """
        ren_kommun = _validera_segment(kommun, "kommun")
        ren_ruta = _validera_segment(rut_id, "rut_id")
        if not filer:
            raise ForberaknadLagerFel("Minst en fil måste anges vid hamta_ruta.")

        normaliserade = [_validera_rel_fil(f) for f in filer]
        run_nyckel = geografi_nyckel(ren_kommun, ren_ruta, "run.json")
        _kontrollera_nyckel_dataskydd(run_nyckel)
        self._kontrollera_lagring_sokvag(run_nyckel)
        for filnamn in normaliserade:
            nyckel = geografi_nyckel(ren_kommun, ren_ruta, filnamn)
            _kontrollera_nyckel_dataskydd(nyckel)
            self._kontrollera_lagring_sokvag(nyckel)

        self._kontrollera_cache_sokvag(
            ("geografi", ren_kommun, ren_ruta), sammanhang=f"{ren_kommun}/{ren_ruta}"
        )
        for filnamn in normaliserade:
            self._kontrollera_cache_sokvag(
                ("geografi", ren_kommun, ren_ruta, *filnamn.split("/")),
                sammanhang=f"{ren_kommun}/{ren_ruta}/{filnamn}",
            )
        rutamapp = self._rutamapp(ren_kommun, ren_ruta)
        lokal_run = rutamapp / "run.json"
        eff_verifiera = bool(verifiera_geotiff or self._verifiera_geotiff)

        with self._lock:
            nu_s = self._klocka()
            manifest_data: dict[str, Any] | None = None
            # Snabb lokal cache-träff: om `run.json` finns lokalt och varje efterfrågad fil
            # antingen finns lokalt eller har giltig (ej utgången) negativcache -> 0 S3-anrop!
            if lokal_run.is_file():
                manifest_data = _kontrollera_manifest_dataskydd(
                    lokal_run, sammanhang=f"{ren_kommun}/{ren_ruta}"
                )
                saknas_lokalt = [
                    f
                    for f in normaliserade
                    if not (rutamapp / f).is_file()
                    and not self._ar_negativ_cachad(
                        geografi_nyckel(ren_kommun, ren_ruta, f), nu_s
                    )
                ]
                if not saknas_lokalt:
                    lokala = [
                        (f, rutamapp / f) for f in normaliserade if (rutamapp / f).is_file()
                    ]
                    if not lokala:
                        raise ForberaknadLagerFel(
                            f"Inga efterfrågade filer hittades i lagringen för "
                            f"{ren_kommun}/{ren_ruta} (sökte: {', '.join(normaliserade)})."
                        )
                    if eff_verifiera:
                        for f_namn, p in lokala:
                            if f_namn.lower().endswith((".tif", ".tiff")):
                                validera_forberaknad_geotiff(p, las_data=True)
                    markera_cache_anvand(lokal_run)
                    for _, p in lokala:
                        markera_cache_anvand(p)
                    return rutamapp

            # Kontrollera alltid `run.json` först för dataskydd (även om ej i `filer`).
            hamtade_nagot = False
            run_saknas_nu = False
            if not lokal_run.is_file() and not self._ar_negativ_cachad(run_nyckel, nu_s):
                if self._lagring.finns(run_nyckel):
                    _, manifest_data = self._ladda_ner_atomiskt(
                        run_nyckel, lokal_run, filnamn="run.json"
                    )
                    self._rensa_saknad(run_nyckel)
                    hamtade_nagot = True
                else:
                    self._markera_saknad(run_nyckel, self._klocka())
                    run_saknas_nu = True
            elif lokal_run.is_file() and manifest_data is None:
                manifest_data = _kontrollera_manifest_dataskydd(
                    lokal_run, sammanhang=f"{ren_kommun}/{ren_ruta}"
                )

            hittade_filer = 0
            for filnamn in normaliserade:
                mal = rutamapp / filnamn
                nyckel = geografi_nyckel(ren_kommun, ren_ruta, filnamn)
                if mal.is_file():
                    if eff_verifiera and filnamn.lower().endswith((".tif", ".tiff")):
                        validera_forberaknad_geotiff(mal, las_data=True)
                    markera_cache_anvand(mal)
                    self._rensa_saknad(nyckel)
                    hittade_filer += 1
                    continue
                if (nyckel == run_nyckel and run_saknas_nu) or self._ar_negativ_cachad(
                    nyckel, nu_s
                ):
                    continue
                if self._lagring.finns(nyckel):
                    self._ladda_ner_atomiskt(
                        nyckel,
                        mal,
                        filnamn=filnamn,
                        manifest=manifest_data,
                        verifiera_geotiff=eff_verifiera,
                    )
                    self._rensa_saknad(nyckel)
                    hamtade_nagot = True
                    hittade_filer += 1
                else:
                    self._markera_saknad(nyckel, self._klocka())

            if hittade_filer == 0:
                raise ForberaknadLagerFel(
                    f"Inga efterfrågade filer hittades i lagringen för {ren_kommun}/{ren_ruta} "
                    f"(sökte: {', '.join(normaliserade)})."
                )

            if hamtade_nagot and self._stada_vid_hamtning:
                stada_lokal_cache(
                    self._cache_rot,
                    max_gb=self._max_gb,
                    max_alder_dagar=self._max_alder_dagar,
                )
            return rutamapp

    def las_raster(
        self,
        kommun: str,
        rut_id: str,
        filnamn: str = "mark.tif",
    ) -> tuple[Any, dict[str, Any]]:
        """Hämtar och läser en förberäknad GeoTIFF/ZSTD-COG med strikt validering.

        Returnerar `(array, metadata)` och kastar alltid `ForberaknadLagerFel` vid korrupt fil.
        """
        ren_fil = _validera_rel_fil(filnamn)
        mapp = self.hamta_ruta(kommun, rut_id, filer=(ren_fil,), verifiera_geotiff=True)
        fil = mapp / ren_fil
        try:
            import rasterio

            with rasterio.open(fil) as ds:
                arr = ds.read(1)
                meta = {
                    "bredd": int(ds.width),
                    "hojd": int(ds.height),
                    "crs": str(ds.crs) if ds.crs is not None else None,
                    "bounds": (
                        float(ds.bounds.left),
                        float(ds.bounds.bottom),
                        float(ds.bounds.right),
                        float(ds.bounds.top),
                    ),
                    "nodata": ds.nodata,
                }
            return arr, meta
        except ForberaknadLagerFel:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ForberaknadLagerFel(
                f"Kunde inte läsa förberäknat raster {kommun}/{rut_id}/{ren_fil}: {exc}"
            ) from exc

    def hamta_forberaknad_modul(
        self,
        kommun: str,
        rut_id: str,
        modul: str,
        *,
        filnamn: str | None = None,
        verifiera_geotiff: bool = True,
    ) -> Path:
        """Hämtar nästlat förberäknat GPU-modulraster via LRU-cache (path-traversal-säkert).

        Stöder modulerna i `PRECOMPUTED_MODUL_FILER` (`vatten2d` -> `vatten2d/max_djup.tif`,
        `dagsljus` -> `dagsljus/svf.tif`) utan att ändra `STANDARD_RUTFILER`.
        """
        ren_kommun = _validera_segment(kommun, "kommun")
        ren_ruta = _validera_segment(rut_id, "rut_id")
        ren_modul = _validera_segment(modul, "modul")
        if ren_modul not in PRECOMPUTED_MODUL_FILER:
            tillatna = ", ".join(sorted(PRECOMPUTED_MODUL_FILER))
            raise ForberaknadLagerFel(
                f"Okänd förberäknad modul {modul!r}. Tillåtna moduler: {tillatna}."
            )
        vald_fil = (
            PRECOMPUTED_MODUL_FILER[ren_modul]
            if filnamn is None
            else _validera_segment(filnamn, "filnamn")
        )
        rel_fil = _validera_rel_fil(f"{ren_modul}/{vald_fil}")
        rutamapp = self.hamta_ruta(
            ren_kommun,
            ren_ruta,
            filer=(rel_fil,),
            verifiera_geotiff=verifiera_geotiff,
        )
        mal = rutamapp / rel_fil
        try:
            cache_abs = self._cache_rot.resolve()
            mal_abs = mal.resolve()
        except OSError as exc:
            raise ForberaknadLagerFel(
                f"Ogiltig cachesökväg för {ren_kommun}/{ren_ruta}/{rel_fil}: {exc}"
            ) from exc
        if not mal_abs.is_relative_to(cache_abs) or mal.is_symlink() or not mal.is_file():
            raise ForberaknadLagerFel(
                f"Förberäknad modulfil saknas eller är ogiltig: {ren_kommun}/{ren_ruta}/{rel_fil}."
            )
        return mal

    def hitta_rutor_for_utbredning(
        self,
        kommun: str,
        utbredning_3011: tuple[float, float, float, float],
        *,
        rutstorlek_m: float = STANDARD_RUTSTORLEK_M,
    ) -> list[str]:
        """Returnerar sorterade `rut_id` som täcker `utbredning_3011` (utan nedladdning)."""
        if len(utbredning_3011) != 4 or not all(math.isfinite(float(v)) for v in utbredning_3011):
            raise ForberaknadLagerFel(
                f"Ogiltig utbredning_3011 (kräver 4 ändliga tal): {utbredning_3011!r}."
            )
        xmin, ymin, xmax, ymax = (float(v) for v in utbredning_3011)
        if xmax <= xmin or ymax <= ymin:
            raise ForberaknadLagerFel(
                f"Ogiltig utbredning_3011 (xmax <= xmin eller ymax <= ymin): {utbredning_3011!r}."
            )
        mal_bbox = (xmin, ymin, xmax, ymax)
        matchade: list[str] = []
        for rut_id in self.lista_rutor(kommun):
            rut_bbox = tolka_rut_bbox_3011(rut_id, rutstorlek_m=rutstorlek_m)
            if rut_bbox is not None and _bbox_overlappar(rut_bbox, mal_bbox):
                matchade.append(rut_id)
        return matchade

    def hamta_for_utbredning(
        self,
        kommun: str,
        utbredning_3011: tuple[float, float, float, float],
        *,
        filer: Sequence[str] = STANDARD_RUTFILER,
        rutstorlek_m: float = STANDARD_RUTSTORLEK_M,
    ) -> list[Path]:
        """Hittar och hämtar alla förberäknade 500×500 m-rutor som täcker `utbredning_3011`."""
        rutor = self.hitta_rutor_for_utbredning(
            kommun, utbredning_3011, rutstorlek_m=rutstorlek_m
        )
        return [self.hamta_ruta(kommun, rut_id, filer=filer) for rut_id in rutor]


def forberaknad_lager_fran_miljo(
    cache_rot: str | Path | None = None,
    *,
    konfig: Mapping[str, Any] | None = None,
    miljo: Mapping[str, str] | None = None,
    max_gb: float | None = None,
    klient: Any | None = None,
    bas_rot: str | Path | None = None,
    negativ_cache_ttl_s: float = STANDARD_NEGATIV_CACHE_TTL_S,
) -> ForberaknadKommunLager | None:
    """Skapar `ForberaknadKommunLager` ur `FLODEN_S3_*`/[lagring] eller lokal katalog.

    - När `FLODEN_S3_TYP=s3` (eller `FLODEN_S3_BUCKET` + `FLODEN_S3_ENDPOINT` är satta utan
      `FLODEN_S3_TYP`) används S3-lagring (100 % bakåtkompatibelt; kastar `ObjektLagringFel`
      om nycklar/bucket/boto3 saknas).
    - När `FLODEN_S3_TYP` inte är satt eller är `lokal` används lokal förberäknad katalog
      (`FLODEN_FORBERAKNAT_DIR`, standard `data/forberaknat` upplöst mot `bas_rot`) via
      `LokalLagring` om katalogen finns på disk (importerar aldrig `boto3`).
    - Saknas katalogen returneras `None` rent ("ej förberäknad", aldrig krasch och ingen tom
      katalog skapas).
    - Med `cache_rot=None` används `[lagring.lokal_cache] mapp` / `FLODEN_LOKAL_CACHE_MAPP`
      (upplöst mot `bas_rot` om angiven och sökvägen är relativ).
    """
    env = os.environ if miljo is None else miljo
    lk = las_lagring_konfig(konfig, miljo=env)
    s3_uttrycklig = lk.typ == "s3"
    s3_implicit = (
        not env.get("FLODEN_S3_TYP", "").strip()
        and lk.bucket is not None
        and lk.endpoint is not None
    )
    if s3_uttrycklig or s3_implicit:
        if s3_implicit and lk.typ != "s3":
            from dataclasses import replace

            lk = replace(lk, typ="s3")

        lagring = skapa_lagring(lk, miljo=env, klient=klient)
        return ForberaknadKommunLager(
            lagring,
            cache_rot=cache_rot,
            max_gb=max_gb,
            negativ_cache_ttl_s=negativ_cache_ttl_s,
            konfig=konfig,
            miljo=env,
            bas_rot=bas_rot,
        )

    lokal_katalog = las_forberaknat_dir(konfig, miljo=env, bas_rot=bas_rot)
    if lokal_katalog.exists() and not lokal_katalog.is_dir():
        raise ForberaknadLagerFel(
            f"{FLODEN_FORBERAKNAT_DIR_ENV} pekar på en fil, inte en katalog: {lokal_katalog}."
        )
    if not lokal_katalog.is_dir():
        return None
    return ForberaknadKommunLager(
        LokalLagring(lokal_katalog),
        cache_rot=cache_rot,
        max_gb=max_gb,
        negativ_cache_ttl_s=negativ_cache_ttl_s,
        konfig=konfig,
        miljo=env,
        bas_rot=bas_rot,
    )


def hamta_forberaknad_modul(
    kommun: str,
    rut_id: str,
    modul: str,
    *,
    lager: ForberaknadKommunLager | None = None,
    filnamn: str | None = None,
    verifiera_geotiff: bool = True,
    cache_rot: str | Path | None = None,
    konfig: Mapping[str, Any] | None = None,
    miljo: Mapping[str, str] | None = None,
    bas_rot: str | Path | None = None,
) -> Path:
    """Hämtar en förberäknad GPU-modulfil (`vatten2d/max_djup.tif`, `dagsljus/svf.tif`)."""
    eff_lager = lager
    if eff_lager is None:
        eff_lager = forberaknad_lager_fran_miljo(
            cache_rot=cache_rot,
            konfig=konfig,
            miljo=miljo,
            bas_rot=bas_rot,
        )
    if eff_lager is None:
        raise ForberaknadLagerFel(
            "Inget förberäknat lager (S3 eller lokal katalog) är konfigurerat."
        )
    return eff_lager.hamta_forberaknad_modul(
        kommun,
        rut_id,
        modul,
        filnamn=filnamn,
        verifiera_geotiff=verifiera_geotiff,
    )


__all__ = [
    "DataskyddFel",
    "FLODEN_FORBERAKNAT_DIR_ENV",
    "ForberaknadKommunLager",
    "ForberaknadLagerFel",
    "ObjektLagringFel",
    "PRECOMPUTED_GPU_FILER",
    "PRECOMPUTED_MODUL_FILER",
    "STANDARD_FORBERAKNAT_DIR",
    "STANDARD_NEGATIV_CACHE_TTL_S",
    "STANDARD_RUTFILER",
    "STANDARD_RUTSTORLEK_M",
    "forberaknad_lager_fran_miljo",
    "hamta_forberaknad_modul",
    "las_forberaknat_dir",
    "tolka_rut_bbox_3011",
    "validera_forberaknad_geotiff",
]
