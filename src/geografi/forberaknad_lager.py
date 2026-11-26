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
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from gemensamt.lokal_cache import (
    las_lokal_cache_konfig,
    markera_cache_anvand,
    stada_lokal_cache,
)
from gemensamt.manifest import ManifestFel, las_manifest
from gemensamt.objektlagring import (
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
STANDARD_RUTSTORLEK_M: float = 500.0

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
    ren = varde.strip()
    if not ren or "/" in ren or "\\" in ren or ".." in ren or not _SEGMENT_RE.match(ren):
        raise ForberaknadLagerFel(f"Ogiltigt {namn}: {varde!r}.")
    return ren


def _validera_rel_fil(fil: str) -> str:
    if "\\" in fil:
        raise ForberaknadLagerFel(f"Ogiltigt filnamn (innehåller '\\'): {fil!r}.")
    ren = fil.strip().lstrip("/")
    if not ren:
        raise ForberaknadLagerFel("Filnamn får inte vara tomt.")
    delar = [d for d in ren.split("/") if d]
    if any(d in (".", "..") for d in delar):
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


def _kontrollera_manifest_dataskydd(manifest_fil: Path, *, sammanhang: str) -> None:
    """Kontrollerar att `run.json` inte anger `dataklass != "oppen"`."""
    try:
        raa = json.loads(manifest_fil.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
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
    """Deterministisk läs- och lokal LRU-cache-klient för förberäknade kommunrutor."""

    def __init__(
        self,
        objektlagring: ObjektLagring,
        cache_rot: Path,
        max_gb: float = 10.0,
        *,
        max_alder_dagar: float = 14.0,
        stada_vid_hamtning: bool = True,
    ) -> None:
        if not math.isfinite(max_gb) or max_gb <= 0.0:
            raise ForberaknadLagerFel(f"max_gb måste vara > 0, fick {max_gb}.")
        if not math.isfinite(max_alder_dagar) or max_alder_dagar <= 0.0:
            raise ForberaknadLagerFel(f"max_alder_dagar måste vara > 0, fick {max_alder_dagar}.")
        self._lagring = objektlagring
        self._cache_rot = Path(cache_rot)
        self._max_gb = float(max_gb)
        self._max_alder_dagar = float(max_alder_dagar)
        self._stada_vid_hamtning = bool(stada_vid_hamtning)
        self._saknade_nycklar: set[str] = set()

    @property
    def cache_rot(self) -> Path:
        return self._cache_rot

    @property
    def max_gb(self) -> float:
        return self._max_gb

    @property
    def kalla_typ(self) -> Literal["s3", "lokal"]:
        return "s3" if isinstance(self._lagring, S3Lagring) else "lokal"

    def _rutamapp(self, kommun: str, rut_id: str) -> Path:
        return self._cache_rot / "geografi" / kommun / rut_id

    def _ladda_ner_atomiskt(self, nyckel: str, mal: Path) -> Path:
        """Laddar ner `nyckel` via temporärfil i målmappen följt av `os.replace`."""
        _kontrollera_nyckel_dataskydd(nyckel)
        mal.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_namn = tempfile.mkstemp(prefix=".floden_rut_", dir=str(mal.parent))
        tmp_path = Path(tmp_namn)
        try:
            os.close(fd)
            self._lagring.ladda_ner_fil(nyckel, tmp_path)
            if mal.name == "run.json":
                _kontrollera_manifest_dataskydd(tmp_path, sammanhang=nyckel)
            os.replace(tmp_path, mal)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        markera_cache_anvand(mal)
        return mal

    def lista_kommuner(self) -> list[str]:
        """Returnerar sorterad lista över kommuner under `geografi/<kommun>/<rut_id>/...`."""
        nycklar = self._lagring.lista_prefix("geografi/")
        kommuner: set[str] = set()
        for nyckel in nycklar:
            delar = nyckel.split("/")
            if len(delar) >= 4 and delar[0] == "geografi" and delar[1] and delar[2]:
                if delar[1].lower() in _FORBJUDNA_DATAKLASS_SEGMENT:
                    continue
                kommuner.add(delar[1])
        return sorted(kommuner)

    def lista_rutor(self, kommun: str) -> list[str]:
        """Returnerar sorterad lista över rut-id under `geografi/<kommun>/<rut_id>/...`."""
        ren_kommun = _validera_segment(kommun, "kommun")
        _kontrollera_nyckel_dataskydd(f"geografi/{ren_kommun}")
        prefix = f"geografi/{ren_kommun}/"
        nycklar = self._lagring.lista_prefix(prefix)
        rutor: set[str] = set()
        for nyckel in nycklar:
            delar = nyckel.split("/")
            if len(delar) >= 4 and delar[0] == "geografi" and delar[1] == ren_kommun and delar[2]:
                if any(d.lower() in _FORBJUDNA_DATAKLASS_SEGMENT for d in delar[2:]):
                    continue
                rutor.add(delar[2])
        return sorted(rutor)

    def hamta_ruta(
        self,
        kommun: str,
        rut_id: str,
        filer: Sequence[str] = STANDARD_RUTFILER,
    ) -> Path:
        """Hämtar efterfrågade filer för en ruta till lokal cache (atomiskt + LRU-mtime).

        Om `run.json` och efterfrågade filer redan finns lokalt sker ingen ny S3-hämtning.
        Avvisar alltid rutor med `dataklass != "oppen"`.
        """
        ren_kommun = _validera_segment(kommun, "kommun")
        ren_ruta = _validera_segment(rut_id, "rut_id")
        if not filer:
            raise ForberaknadLagerFel("Minst en fil måste anges vid hamta_ruta.")

        normaliserade = [_validera_rel_fil(f) for f in filer]
        for filnamn in normaliserade:
            _kontrollera_nyckel_dataskydd(geografi_nyckel(ren_kommun, ren_ruta, filnamn))

        rutamapp = self._rutamapp(ren_kommun, ren_ruta)
        lokal_run = rutamapp / "run.json"
        run_nyckel = geografi_nyckel(ren_kommun, ren_ruta, "run.json")

        # Snabb lokal cache-träff: om `run.json` finns lokalt och varje efterfrågad fil
        # antingen finns lokalt eller redan konstaterats saknas i S3 -> 0 S3-anrop!
        if lokal_run.is_file():
            _kontrollera_manifest_dataskydd(lokal_run, sammanhang=f"{ren_kommun}/{ren_ruta}")
            saknas_lokalt = [
                f
                for f in normaliserade
                if not (rutamapp / f).is_file()
                and geografi_nyckel(ren_kommun, ren_ruta, f) not in self._saknade_nycklar
            ]
            if not saknas_lokalt:
                markera_cache_anvand(lokal_run)
                for f in normaliserade:
                    p = rutamapp / f
                    if p.is_file():
                        markera_cache_anvand(p)
                return rutamapp

        # Kontrollera alltid `run.json` först för dataskydd (även om ej i `filer`).
        hamtade_nagot = False
        if not lokal_run.is_file() and run_nyckel not in self._saknade_nycklar:
            if self._lagring.finns(run_nyckel):
                self._ladda_ner_atomiskt(run_nyckel, lokal_run)
                hamtade_nagot = True
            else:
                self._saknade_nycklar.add(run_nyckel)
        elif lokal_run.is_file():
            _kontrollera_manifest_dataskydd(lokal_run, sammanhang=f"{ren_kommun}/{ren_ruta}")

        hittade_filer = 0
        for filnamn in normaliserade:
            mal = rutamapp / filnamn
            if mal.is_file():
                markera_cache_anvand(mal)
                hittade_filer += 1
                continue
            nyckel = geografi_nyckel(ren_kommun, ren_ruta, filnamn)
            if nyckel in self._saknade_nycklar:
                continue
            if self._lagring.finns(nyckel):
                self._ladda_ner_atomiskt(nyckel, mal)
                hamtade_nagot = True
                hittade_filer += 1
            else:
                self._saknade_nycklar.add(nyckel)

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
) -> ForberaknadKommunLager | None:
    """Skapar `ForberaknadKommunLager` ur `FLODEN_S3_*` / `[lagring]`, eller `None` om S3 ej aktiv.

    - Returnerar `None` direkt när S3 inte är konfigurerat (importerar aldrig `boto3`).
    - Kastar `ObjektLagringFel` (inget tyst fel) om S3 är aktiverat men saknar nycklar/bucket/boto3.
    """
    env = os.environ if miljo is None else miljo
    lk = las_lagring_konfig(konfig, miljo=env)
    s3_uttrycklig = lk.typ == "s3"
    s3_implicit = (
        not env.get("FLODEN_S3_TYP", "").strip()
        and lk.bucket is not None
        and lk.endpoint is not None
    )
    if not s3_uttrycklig and not s3_implicit:
        return None

    if s3_implicit and lk.typ != "s3":
        from dataclasses import replace

        lk = replace(lk, typ="s3")

    cache_konf = las_lokal_cache_konfig(konfig, miljo=env)
    vald_rot = Path(cache_rot) if cache_rot is not None else cache_konf.mapp
    vald_max_gb = float(max_gb) if max_gb is not None else cache_konf.max_gb
    lagring = skapa_lagring(lk, miljo=env, klient=klient)
    return ForberaknadKommunLager(
        lagring,
        vald_rot,
        max_gb=vald_max_gb,
        max_alder_dagar=cache_konf.max_alder_dagar,
    )


__all__ = [
    "DataskyddFel",
    "ForberaknadKommunLager",
    "ForberaknadLagerFel",
    "ObjektLagringFel",
    "STANDARD_RUTFILER",
    "STANDARD_RUTSTORLEK_M",
    "forberaknad_lager_fran_miljo",
    "tolka_rut_bbox_3011",
]
