# SPDX-License-Identifier: GPL-3.0-or-later
"""Automatisk klient och filinläsare för SMHI MetObs, SMHI STRÅNG v1 och urban bakgrundsluft.

Principer (enligt `AGENTS.md` och `api_data_och_ekologi_plan.md`):
- Externa API:er anropas enbart vid geografimappning eller explicit cachad inläsning, aldrig mitt
  i en analyskörning.
- Strikt HTTPS + värd-allowlist (`opendata-download-metobs.smhi.se`,
  `opendata-download-metanalys.smhi.se`, `datavardluft.smhi.se`).
- Injicerbar `hamtare` för 100 % deterministiska, nätverksfria enhetstester.
- Kvalitetsflaggor (`G`/`Y`) filtreras explicit; underkända rader redovisas transparent.
- 0 påhittade värden: vid timeout eller fel mot Datavärdskap luftkvalitet returneras en tydlig
  varning och tomma mätvärden (`tillganglig = False`), aldrig gissade bakgrundshalter.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np

from data_inlasning.cache import DataInlasningFel, LokalDataCache
from data_inlasning.goteborg import (
    GOTEBORG_KALLA_LUFT,
    GOTEBORG_LUFT_CSV_URL,
    GOTEBORG_LUFT_LICENS_SPDX,
    GOTEBORG_LUFT_STATION_FEMMAN_ID,
    GOTEBORG_LUFT_STATION_FEMMAN_NAMN,
    GOTEBORG_LUFT_STATION_FEMMAN_WGS84,
    GoteborgDataFel,
    hamta_goteborg_luft_csv,
    tolka_goteborg_luft_json,
)
from data_inlasning.smhi import (
    PARAM_LUFTTRYCK,
    PARAM_NEDERBORD,
    PARAM_RELATIV_FUKTIGHET,
    PARAM_TEMPERATUR,
    PARAM_VINDHASTIGHET,
    PARAM_VINDRIKTNING,
    SMHI_METOBS_BAS_URL,
    SmhiStation,
    bygg_nortrip_vader_fran_smhi,
    hitta_narmaste_station,
    tolka_stationer,
)
from gemensamt.hash import sha256_bytes, sha256_fil
from gemensamt.koordinater import EPSG_INTERN, transformera
from geografi.smhi_vader import (
    PARAM_GLOBALSTRALNING,
    STANDARD_GODKANDA_KVALITETER,
    SmhiObservation,
    SmhiParameterSerie,
    SmhiVaderFel,
    VarmeboljaStatistik,
    Vindros16,
    berakna_varmebolja_utci,
    berakna_vindros_16,
    las_smhi_csv_text,
    las_smhi_json_text,
)
from luft.emission import VaderserieTimme

EPSG_WGS84: Final[int] = 4326

SMHI_STRANG_BAS_URL: Final[str] = (
    "https://opendata-download-metanalys.smhi.se/api/category/strang1g/version/1"
)
SMHI_DATAVARDLUFT_BAS_URL: Final[str] = "https://datavardluft.smhi.se/52North/api/v1"

TILLATNA_SMHI_VARDAR: Final[frozenset[str]] = frozenset(
    {
        "opendata-download-metobs.smhi.se",
        "opendata-download-metanalys.smhi.se",
        "datavardluft.smhi.se",
    }
)

# I SMHI STRÅNG v1 är:
#   116 = CIE UV-irradians (mW/m²) – INTE globalstrålning (används ej för UTCI)
#   117 = Globalstrålning (W/m²)
#   118 = Direkt normalstrålning (W/m²)
STRANG_PARAM_CIE_UV: Final[int] = 116  # CIE UV-irradians (mW/m²) – endast dokumentation
STRANG_PARAM_GLOBAL: Final[int] = 117  # Globalstrålning (W/m²)
STRANG_PARAM_DIREKT_NORMAL: Final[int] = 118  # Direkt normalstrålning (W/m²)

STANDARD_TIMEOUT_S: Final[float] = 20.0
DATAVARDLUFT_TIMEOUT_S: Final[float] = 5.0
SMHI_LICENS_SPDX: Final[str] = "CC-BY-4.0"
SMHI_LICENS_TEXT: Final[str] = "CC BY 4.0"
SMHI_KALLA_METOBS: Final[str] = "SMHI Öppna data (MetObs v1.0, CC BY 4.0). © SMHI."
SMHI_KALLA_STRANG: Final[str] = "SMHI Öppna data (STRÅNG v1, CC BY 4.0). © SMHI."
SMHI_KALLA_DATAVARDLUFT: Final[str] = "SMHI Datavärdskap luftkvalitet (öppna mätdata). © SMHI."

HamtareFn = Callable[[str], bytes]


class SmhiLuftStrangFel(DataInlasningFel):
    """Fel vid hämtning, värdkontroll eller tolkning av SMHI MetObs, STRÅNG eller luftkvalitet."""


# ------------------------------------------------------------------ URL-skydd & hämtning


def kontrollera_smhi_url(url: str) -> str:
    """Kontrollerar strikt HTTPS och att värden finns i SMHI:s allowlist (SSRF-skydd)."""
    if not isinstance(url, str) or not url.strip():
        raise SmhiLuftStrangFel("URL mot SMHI får inte vara tom.")
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme != "https":
        raise SmhiLuftStrangFel(f"Endast HTTPS tillåts mot SMHI-tjänster, fick {url!r}.")
    if parsed.username is not None or parsed.password is not None:
        raise SmhiLuftStrangFel("Inloggningsuppgifter i SMHI-URL tillåts inte.")
    host = (parsed.hostname or "").lower()
    if host not in TILLATNA_SMHI_VARDAR:
        tillatna = ", ".join(sorted(TILLATNA_SMHI_VARDAR))
        raise SmhiLuftStrangFel(
            f"Värden {host!r} är inte tillåten för SMHI-klienten (tillåtna: {tillatna})."
        )
    return url.strip()


def _standard_hamta_bytes(url: str, *, timeout_s: float) -> bytes:
    ren_url = kontrollera_smhi_url(url)
    req = urllib.request.Request(
        ren_url,
        headers={"User-Agent": "Floden/1.0 (SMHI open-data client)"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - HTTPS + allowlist
            return bytes(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        raise SmhiLuftStrangFel(f"Kunde inte hämta {ren_url}: {exc}") from exc


def _hamta_med_cache(
    url: str,
    *,
    filnamn: str,
    kalla: str,
    cache_mapp: str | Path | None = None,
    hamtare: HamtareFn | None = None,
    timeout_s: float = STANDARD_TIMEOUT_S,
) -> tuple[bytes, str]:
    """Hämtar rådata med strikt värdkontroll och valfri SHA-256-verifierad lokal cache."""
    ren_url = kontrollera_smhi_url(url)

    def _adapter(req_url: str, _headers: Mapping[str, str] | None = None) -> bytes:
        kontrollera_smhi_url(req_url)
        if hamtare is not None:
            return hamtare(req_url)
        return _standard_hamta_bytes(req_url, timeout_s=timeout_s)

    if cache_mapp is not None:
        cache = LokalDataCache(cache_mapp, hamtare=_adapter)
        try:
            post = cache.hamta_eller_lasa(ren_url, filnamn=filnamn, kalla=kalla)
        except DataInlasningFel as exc:
            raise SmhiLuftStrangFel(str(exc)) from exc
        data = post.fil.read_bytes()
        return data, post.sha256

    data = _adapter(ren_url)
    return data, sha256_bytes(data)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * r * math.asin(min(1.0, math.sqrt(a)))


def karna_mittpunkt_wgs84(
    karna_3011: tuple[float, float, float, float],
) -> tuple[float, float]:
    """Returnerar `(lon_grader, lat_grader)` i WGS 84 för mittpunkten av `karna_3011`."""
    e0, n0, e1, n1 = karna_3011
    if not all(math.isfinite(v) for v in (e0, n0, e1, n1)) or e0 >= e1 or n0 >= n1:
        raise SmhiLuftStrangFel(f"Ogiltig karna_3011: {karna_3011!r}.")
    e_mitt = 0.5 * (e0 + e1)
    n_mitt = 0.5 * (n0 + n1)
    lon, lat = transformera(e_mitt, n_mitt, EPSG_INTERN, EPSG_WGS84)
    return float(lon), float(lat)


# ------------------------------------------------------------------ SMHI MetObs live-hämtare


def hamta_metobs_stationer(
    parameter_id: int,
    *,
    bas_url: str = SMHI_METOBS_BAS_URL,
    cache_mapp: str | Path | None = None,
    hamtare: HamtareFn | None = None,
    timeout_s: float = STANDARD_TIMEOUT_S,
) -> tuple[list[SmhiStation], str]:
    """Hämtar och tolkar SMHI MetObs stationslista för `parameter_id`."""
    url = f"{bas_url.rstrip('/')}/parameter/{int(parameter_id)}.json"
    ra, sha = _hamta_med_cache(
        url,
        filnamn=f"smhi_metobs_p{int(parameter_id)}_stationer.json",
        kalla=SMHI_KALLA_METOBS,
        cache_mapp=cache_mapp,
        hamtare=hamtare,
        timeout_s=timeout_s,
    )
    try:
        data = json.loads(ra.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SmhiLuftStrangFel(f"Ogiltig JSON i SMHI stationslista ({url}): {exc}") from exc
    if not isinstance(data, Mapping):
        raise SmhiLuftStrangFel("SMHI stationslista måste vara ett JSON-objekt.")
    return tolka_stationer(data), sha


def valj_narmaste_smhi_station(
    karna_3011: tuple[float, float, float, float],
    parameter_id: int = PARAM_TEMPERATUR,
    *,
    bara_aktiva: bool = True,
    bas_url: str = SMHI_METOBS_BAS_URL,
    cache_mapp: str | Path | None = None,
    hamtare: HamtareFn | None = None,
    timeout_s: float = STANDARD_TIMEOUT_S,
) -> tuple[SmhiStation, float, str]:
    """Väljer närmaste aktiva SMHI MetObs-station för mittpunkten av `karna_3011`.

    Returnerar `(station, avstand_km, stationslista_sha256)`.
    """
    lon, lat = karna_mittpunkt_wgs84(karna_3011)
    stationer, sha = hamta_metobs_stationer(
        parameter_id,
        bas_url=bas_url,
        cache_mapp=cache_mapp,
        hamtare=hamtare,
        timeout_s=timeout_s,
    )
    station, avstand_km = hitta_narmaste_station(
        lat, lon, stationer, bara_aktiva=bara_aktiva
    )
    return station, float(avstand_km), sha


def valj_senaste_kompletta_ar(serie: SmhiParameterSerie) -> tuple[int, bool]:
    """Hittar senaste kompletta kalenderår i `serie`.

    Ett kalenderår räknas som komplett om det har godkända observationer i minst 11 av 12 månader
    (inkl. januari och december) och minst 7 000 timmar (eller om serien bara innehåller ett år
    returneras det året med `ar_komplett=False` så korta testserier fungerar transparent).
    """
    if not serie.observationer:
        raise SmhiLuftStrangFel("SMHI-serien saknar godkända observationer.")
    per_ar_manader: dict[int, set[int]] = defaultdict(set)
    per_ar_antal: dict[int, int] = defaultdict(int)
    for obs in serie.observationer:
        dt = datetime.fromtimestamp(obs.epoch_s, tz=UTC)
        per_ar_manader[dt.year].add(dt.month)
        per_ar_antal[dt.year] += 1

    kompletta = [
        y
        for y in sorted(per_ar_antal, reverse=True)
        if len(per_ar_manader[y]) >= 11
        and 1 in per_ar_manader[y]
        and 12 in per_ar_manader[y]
        and per_ar_antal[y] >= 7000
    ]
    if kompletta:
        return kompletta[0], True
    # Fallback: senaste året som har data (flaggas tydligt med ar_komplett=False).
    senaste_ar = max(per_ar_antal)
    return senaste_ar, False


def filtrera_smhi_serie_ar(
    serie: SmhiParameterSerie,
    *,
    ar: int | None = None,
    start_utc: datetime | None = None,
    slut_utc: datetime | None = None,
) -> SmhiParameterSerie:
    """Filtrerar en `SmhiParameterSerie` deterministiskt till angivet kalenderår eller intervall."""
    if ar is None and start_utc is None and slut_utc is None:
        return serie
    ut_obs: list[SmhiObservation] = []
    for obs in serie.observationer:
        dt = datetime.fromtimestamp(obs.epoch_s, tz=UTC)
        if ar is not None and dt.year != ar:
            continue
        if start_utc is not None and dt < start_utc:
            continue
        if slut_utc is not None and dt > slut_utc:
            continue
        ut_obs.append(obs)
    return SmhiParameterSerie(
        parameter_id=serie.parameter_id,
        parameter_namn=serie.parameter_namn,
        enhet=serie.enhet,
        station_id=serie.station_id,
        station_namn=serie.station_namn,
        observationer=tuple(ut_obs),
        antal_totalt=serie.antal_totalt,
        antal_godkanda=len(ut_obs),
        bortfiltrerade=serie.bortfiltrerade,
        bortfiltrerade_per_orsak=dict(serie.bortfiltrerade_per_orsak),
        flaggor_fordelning=dict(serie.flaggor_fordelning),
        godkanda_flaggor=serie.godkanda_flaggor,
    )


def hamta_metobs_parameter_serie(
    parameter_id: int,
    station_id: str,
    *,
    period: str = "corrected-archive",
    format_typ: str | None = None,
    godkanda_flaggor: frozenset[str] = STANDARD_GODKANDA_KVALITETER,
    ar: int | None = None,
    bas_url: str = SMHI_METOBS_BAS_URL,
    cache_mapp: str | Path | None = None,
    hamtare: HamtareFn | None = None,
    timeout_s: float = STANDARD_TIMEOUT_S,
) -> tuple[SmhiParameterSerie, str]:
    """Hämtar och kvalitetsfiltrerar en SMHI MetObs-parameter för `station_id` (`G`/`Y`)."""
    eff_fmt = format_typ or ("csv" if period == "corrected-archive" else "json")
    if eff_fmt not in ("csv", "json"):
        raise SmhiLuftStrangFel(f"Ogiltigt format_typ {eff_fmt!r}; ange 'csv' eller 'json'.")
    url = (
        f"{bas_url.rstrip('/')}/parameter/{int(parameter_id)}/station/{station_id}"
        f"/period/{period}/data.{eff_fmt}"
    )
    ra, sha = _hamta_med_cache(
        url,
        filnamn=f"smhi_metobs_p{int(parameter_id)}_s{station_id}_{period}.{eff_fmt}",
        kalla=SMHI_KALLA_METOBS,
        cache_mapp=cache_mapp,
        hamtare=hamtare,
        timeout_s=timeout_s,
    )
    try:
        text = ra.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = ra.decode("latin-1")

    try:
        if eff_fmt == "json" or text.lstrip().startswith("{"):
            serie = las_smhi_json_text(
                text, parameter_id=int(parameter_id), godkanda_flaggor=godkanda_flaggor
            )
        else:
            serie = las_smhi_csv_text(
                text, parameter_id=int(parameter_id), godkanda_flaggor=godkanda_flaggor
            )
    except SmhiVaderFel as exc:
        raise SmhiLuftStrangFel(
            f"Kunde inte tolka SMHI parameter {parameter_id} för station {station_id}: {exc}"
        ) from exc

    if ar is not None:
        serie = filtrera_smhi_serie_ar(serie, ar=ar)
    return serie, sha


# ------------------------------------------------------------------ SMHI STRÅNG v1-klient


@dataclass(frozen=True)
class StrangSommarResultat:
    """Solstrålning ur SMHI STRÅNG v1 (`117` global, `118` direkt normal) för sommaren."""

    lon_grader: float
    lat_grader: float
    fran_datum: str
    till_datum: str
    global_serie: SmhiParameterSerie
    direkt_normal_serie: SmhiParameterSerie | None
    global_p95_dag_w_m2: float | None
    global_p99_dag_w_m2: float | None
    direkt_normal_p95_dag_w_m2: float | None
    direkt_normal_p99_dag_w_m2: float | None
    global_sha256: str
    direkt_normal_sha256: str | None
    kalla: str = SMHI_KALLA_STRANG
    licens: str = SMHI_LICENS_SPDX

    def till_dict(self) -> dict[str, Any]:
        return {
            "lon_grader": round(self.lon_grader, 5),
            "lat_grader": round(self.lat_grader, 5),
            "fran_datum": self.fran_datum,
            "till_datum": self.till_datum,
            "antal_global_timmar": self.global_serie.antal_godkanda,
            "antal_direkt_normal_timmar": (
                self.direkt_normal_serie.antal_godkanda
                if self.direkt_normal_serie is not None
                else 0
            ),
            "global_p95_dag_w_m2": self.global_p95_dag_w_m2,
            "global_p99_dag_w_m2": self.global_p99_dag_w_m2,
            "direkt_normal_p95_dag_w_m2": self.direkt_normal_p95_dag_w_m2,
            "direkt_normal_p99_dag_w_m2": self.direkt_normal_p99_dag_w_m2,
            "global_sha256": self.global_sha256,
            "direkt_normal_sha256": self.direkt_normal_sha256,
            "kalla": self.kalla,
            "licens": self.licens,
        }


def bygg_strang_url(
    lon_grader: float,
    lat_grader: float,
    parameter_id: int,
    *,
    fran_datum: str,
    till_datum: str,
    bas_url: str = SMHI_STRANG_BAS_URL,
) -> str:
    """Bygger URL för SMHI STRÅNG v1 (`parameter 117` global eller `118` direkt normal)."""
    if parameter_id not in (STRANG_PARAM_GLOBAL, STRANG_PARAM_DIREKT_NORMAL):
        raise SmhiLuftStrangFel(
            f"Ogiltig STRÅNG-parameter {parameter_id}: använd 117 (globalstrålning, W/m²) eller "
            "118 (direkt normalstrålning, W/m²); 116 är CIE UV-irradians (mW/m²)."
        )
    if not (math.isfinite(lon_grader) and math.isfinite(lat_grader)):
        raise SmhiLuftStrangFel("Longitud och latitud måste vara ändliga tal.")
    q = urllib.parse.urlencode({"from": fran_datum, "to": till_datum})
    url = (
        f"{bas_url.rstrip('/')}/geotype/point/lon/{lon_grader:.4f}/lat/{lat_grader:.4f}"
        f"/parameter/{int(parameter_id)}/data.json?{q}"
    )
    return kontrollera_smhi_url(url)


def tolka_strang_json(
    ra_data: Any,
    *,
    parameter_id: int = STRANG_PARAM_GLOBAL,
) -> SmhiParameterSerie:
    """Tolkar SMHI STRÅNG v1 JSON (`[{"date_time": ..., "value": ...}]`) till parameter-serie."""
    if isinstance(ra_data, list):
        poster = ra_data
    elif isinstance(ra_data, Mapping):
        kandidat = ra_data.get("data") or ra_data.get("values") or ra_data.get("value")
        if not isinstance(kandidat, list):
            raise SmhiLuftStrangFel("STRÅNG-svaret saknar lista med tidsobservationer.")
        poster = kandidat
    else:
        raise SmhiLuftStrangFel("STRÅNG-svaret måste vara en JSON-lista eller ett objekt.")

    obs_lista: list[SmhiObservation] = []
    bort: dict[str, int] = defaultdict(int)
    totalt = 0

    for post in poster:
        totalt += 1
        if not isinstance(post, Mapping):
            bort["ogiltig_post"] += 1
            continue
        tid_ra = str(
            post.get("date_time")
            or post.get("dateTime")
            or post.get("time")
            or post.get("date")
            or ""
        ).strip()
        if not tid_ra:
            bort["ogiltig_tid"] += 1
            continue
        iso = tid_ra[:-1] + "+00:00" if tid_ra.endswith("Z") else tid_ra
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            bort["ogiltig_tid"] += 1
            continue
        dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
        epoch_s = int(round(dt.timestamp()))

        v_ra = post.get("value")
        if v_ra is None or isinstance(v_ra, bool):
            bort["ogiltigt_varde"] += 1
            continue
        try:
            val = float(v_ra)
        except (TypeError, ValueError):
            bort["ogiltigt_varde"] += 1
            continue
        if not math.isfinite(val) or val < 0.0 or val > 1500.0:
            bort["intervallfel"] += 1
            continue

        obs_lista.append(
            SmhiObservation(
                tid_utc=dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                epoch_s=epoch_s,
                varde=val,
                kvalitet="G",
            )
        )

    if totalt == 0:
        raise SmhiLuftStrangFel("STRÅNG-svaret innehåller inga observationer.")

    obs_lista.sort(key=lambda o: o.epoch_s)
    namn = (
        "Globalstrålning (STRÅNG 117)"
        if parameter_id == STRANG_PARAM_GLOBAL
        else "Direkt normalstrålning (STRÅNG 118)"
    )
    eff_param = PARAM_GLOBALSTRALNING if parameter_id == STRANG_PARAM_GLOBAL else parameter_id
    return SmhiParameterSerie(
        parameter_id=eff_param,
        parameter_namn=namn,
        enhet="W/m²",
        station_id="strang1g",
        station_namn="SMHI STRÅNG v1",
        observationer=tuple(obs_lista),
        antal_totalt=totalt,
        antal_godkanda=len(obs_lista),
        bortfiltrerade=totalt - len(obs_lista),
        bortfiltrerade_per_orsak=dict(sorted(bort.items())),
        flaggor_fordelning={"G": len(obs_lista)},
        godkanda_flaggor=frozenset({"G"}),
    )


def hamta_strang_parameter(
    lon_grader: float,
    lat_grader: float,
    parameter_id: int,
    *,
    fran_datum: str,
    till_datum: str,
    bas_url: str = SMHI_STRANG_BAS_URL,
    cache_mapp: str | Path | None = None,
    hamtare: HamtareFn | None = None,
    timeout_s: float = STANDARD_TIMEOUT_S,
) -> tuple[SmhiParameterSerie, str]:
    """Hämtar och tolkar en SMHI STRÅNG v1-parameter (`117` eller `118`) för en punkt."""
    url = bygg_strang_url(
        lon_grader,
        lat_grader,
        parameter_id,
        fran_datum=fran_datum,
        till_datum=till_datum,
        bas_url=bas_url,
    )
    ra, sha = _hamta_med_cache(
        url,
        filnamn=f"smhi_strang_p{int(parameter_id)}_{fran_datum}_{till_datum}.json",
        kalla=SMHI_KALLA_STRANG,
        cache_mapp=cache_mapp,
        hamtare=hamtare,
        timeout_s=timeout_s,
    )
    try:
        data = json.loads(ra.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SmhiLuftStrangFel(f"Ogiltig JSON från SMHI STRÅNG ({url}): {exc}") from exc
    return tolka_strang_json(data, parameter_id=parameter_id), sha


def _dagtids_percentil(serie: SmhiParameterSerie, percentil: float) -> float | None:
    dag_v = [o.varde for o in serie.observationer if o.varde > 0.0]
    if not dag_v:
        return None
    return round(float(np.percentile(np.asarray(dag_v, dtype=np.float64), percentil)), 2)


def hamta_strang_sommarstralning(
    lon_grader: float,
    lat_grader: float,
    *,
    ar: int,
    fran_datum: str | None = None,
    till_datum: str | None = None,
    bas_url: str = SMHI_STRANG_BAS_URL,
    cache_mapp: str | Path | None = None,
    hamtare: HamtareFn | None = None,
    timeout_s: float = STANDARD_TIMEOUT_S,
) -> StrangSommarResultat:
    """Hämtar STRÅNG globalstrålning (`117`) och direkt normalstrålning (`118`) för sommaren."""
    f_dat = fran_datum or f"{int(ar)}-06-01"
    t_dat = till_datum or f"{int(ar)}-08-31"
    global_serie, global_sha = hamta_strang_parameter(
        lon_grader,
        lat_grader,
        STRANG_PARAM_GLOBAL,
        fran_datum=f_dat,
        till_datum=t_dat,
        bas_url=bas_url,
        cache_mapp=cache_mapp,
        hamtare=hamtare,
        timeout_s=timeout_s,
    )
    direkt_serie: SmhiParameterSerie | None = None
    direkt_sha: str | None = None
    try:
        direkt_serie, direkt_sha = hamta_strang_parameter(
            lon_grader,
            lat_grader,
            STRANG_PARAM_DIREKT_NORMAL,
            fran_datum=f_dat,
            till_datum=t_dat,
            bas_url=bas_url,
            cache_mapp=cache_mapp,
            hamtare=hamtare,
            timeout_s=timeout_s,
        )
    except SmhiLuftStrangFel:
        direkt_serie = None
        direkt_sha = None

    return StrangSommarResultat(
        lon_grader=float(lon_grader),
        lat_grader=float(lat_grader),
        fran_datum=f_dat,
        till_datum=t_dat,
        global_serie=global_serie,
        direkt_normal_serie=direkt_serie,
        global_p95_dag_w_m2=_dagtids_percentil(global_serie, 95.0),
        global_p99_dag_w_m2=_dagtids_percentil(global_serie, 99.0),
        direkt_normal_p95_dag_w_m2=(
            _dagtids_percentil(direkt_serie, 95.0) if direkt_serie is not None else None
        ),
        direkt_normal_p99_dag_w_m2=(
            _dagtids_percentil(direkt_serie, 99.0) if direkt_serie is not None else None
        ),
        global_sha256=global_sha,
        direkt_normal_sha256=direkt_sha,
    )


# ------------------------------------------------------------------ Komplett SMHI-väderpaket


@dataclass(frozen=True)
class SmhiVaderPaket:
    """Sammanhållet väderunderlag ur SMHI MetObs + STRÅNG för K46 `lager.vader_luft`."""

    station_id: str
    station_namn: str
    station_avstand_km: float
    station_lat: float
    station_lon: float
    ar: int
    ar_komplett: bool
    period: str
    godkanda_flaggor: frozenset[str]
    vindros: Vindros16
    utci_sommar: VarmeboljaStatistik
    vader_timserie: VaderserieTimme
    vader_timserie_csv: str
    strang: StrangSommarResultat | None
    kallor_sha256: dict[str, str]
    varningar: tuple[str, ...]


def serialisera_vaderserie_csv(vader: VaderserieTimme) -> str:
    """Serialiserar `VaderserieTimme` till deterministisk CSV för `vader_timserie.csv.gz`."""
    buf = io.StringIO()
    skrivare = csv.writer(buf, delimiter=",", lineterminator="\n")
    har_tryck = vader.lufttryck_hpa is not None
    rubrik = [
        "tid_iso",
        "temperatur_c",
        "nederbord_mm_h",
        "relativ_fuktighet_pct",
        "vindhastighet_m_s",
    ]
    if har_tryck:
        rubrik.append("lufttryck_hpa")
    skrivare.writerow(rubrik)
    t0 = np.datetime64(vader.start, "h")
    for i in range(vader.antal_timmar):
        ti = str(t0 + np.timedelta64(i, "h"))
        rad = [
            ti,
            f"{float(vader.temperatur_c[i]):.3f}",
            f"{float(vader.nederbord_mm_h[i]):.3f}",
            f"{float(vader.relativ_fuktighet_procent[i]):.2f}",
            f"{float(vader.vindhastighet_m_s[i]):.3f}",
        ]
        if har_tryck and vader.lufttryck_hpa is not None:
            rad.append(f"{float(vader.lufttryck_hpa[i]):.2f}")
        skrivare.writerow(rad)
    return buf.getvalue()


def las_vaderserie_csv(text_eller_bytes: str | bytes) -> VaderserieTimme:
    """Läser tillbaka `VaderserieTimme` ur `vader_timserie.csv` eller `.csv.gz`."""
    if isinstance(text_eller_bytes, bytes):
        if text_eller_bytes[:2] == b"\x1f\x8b":
            text = gzip.decompress(text_eller_bytes).decode("utf-8")
        else:
            text = text_eller_bytes.decode("utf-8")
    else:
        text = text_eller_bytes

    lasare = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    tider: list[str] = []
    temp: list[float] = []
    ned: list[float] = []
    rh: list[float] = []
    vind: list[float] = []
    tryck: list[float] = []
    har_tryck = False

    for rad in lasare:
        tid = (rad.get("tid_iso") or "").strip()
        if not tid:
            continue
        tider.append(tid)
        temp.append(float(rad["temperatur_c"]))
        ned.append(float(rad["nederbord_mm_h"]))
        rh.append(float(rad["relativ_fuktighet_pct"]))
        vind.append(float(rad["vindhastighet_m_s"]))
        if "lufttryck_hpa" in rad and rad["lufttryck_hpa"] not in (None, ""):
            har_tryck = True
            tryck.append(float(rad["lufttryck_hpa"]))

    if not tider:
        raise SmhiLuftStrangFel("vader_timserie.csv saknar giltiga rader.")

    return VaderserieTimme(
        start=tider[0],
        temperatur_c=np.asarray(temp, dtype=np.float64),
        nederbord_mm_h=np.asarray(ned, dtype=np.float64),
        relativ_fuktighet_procent=np.asarray(rh, dtype=np.float64),
        vindhastighet_m_s=np.asarray(vind, dtype=np.float64),
        vindhojd_m=10.0,
        temperaturhojd_m=2.0,
        lufttryck_hpa=(
            np.asarray(tryck, dtype=np.float64)
            if har_tryck and len(tryck) == len(tider)
            else None
        ),
    )


def _serie_till_timkarta(serie: SmhiParameterSerie) -> dict[int, float]:
    return {th: obs.varde for th, obs in serie.per_timme().items()}


def hamta_smhi_vaderpaket(
    karna_3011: tuple[float, float, float, float],
    *,
    ar: int | None = None,
    period: str = "corrected-archive",
    godkanda_flaggor: frozenset[str] = STANDARD_GODKANDA_KVALITETER,
    inkludera_strang: bool = True,
    inkludera_lufttryck: bool = False,
    metobs_bas_url: str = SMHI_METOBS_BAS_URL,
    strang_bas_url: str = SMHI_STRANG_BAS_URL,
    cache_mapp: str | Path | None = None,
    hamtare: HamtareFn | None = None,
    timeout_s: float = STANDARD_TIMEOUT_S,
) -> SmhiVaderPaket:
    """Hämtar komplett SMHI MetObs + STRÅNG-väderpaket för projektets mittpunkt i EPSG:3011.

    Beräknar:
    - 16-sektors vindros (`Vindros16`),
    - sommar-/värmeböljepercentiler för UTCI (`VarmeboljaStatistik`, berikad med STRÅNG),
    - luckfri timvis väderserie (`VaderserieTimme` + CSV för NORTRIP).
    """
    lon, lat = karna_mittpunkt_wgs84(karna_3011)
    kallor_sha: dict[str, str] = {}
    varningar: list[str] = []

    station, avstand_km, st_sha = valj_narmaste_smhi_station(
        karna_3011,
        PARAM_TEMPERATUR,
        bara_aktiva=True,
        bas_url=metobs_bas_url,
        cache_mapp=cache_mapp,
        hamtare=hamtare,
        timeout_s=timeout_s,
    )
    kallor_sha["metobs_stationer_temp"] = st_sha

    temp_full, sha_t = hamta_metobs_parameter_serie(
        PARAM_TEMPERATUR,
        station.id,
        period=period,
        godkanda_flaggor=godkanda_flaggor,
        bas_url=metobs_bas_url,
        cache_mapp=cache_mapp,
        hamtare=hamtare,
        timeout_s=timeout_s,
    )
    kallor_sha["metobs_temperatur"] = sha_t

    if ar is None:
        valt_ar, ar_komplett = valj_senaste_kompletta_ar(temp_full)
        if not ar_komplett:
            varningar.append(
                f"SMHI-station {station.namn} ({station.id}) saknade komplett 12-månadersår i "
                f"{period}; använder tillgängliga observationer för {valt_ar}."
            )
    else:
        valt_ar = int(ar)
        temp_filtrerad_test = filtrera_smhi_serie_ar(temp_full, ar=valt_ar)
        if temp_filtrerad_test.antal_godkanda == 0:
            raise SmhiLuftStrangFel(
                f"Inga kvalitetsgodkända temperaturobservationer för år {valt_ar} vid station "
                f"{station.namn} ({station.id})."
            )
        _, ar_komplett = valj_senaste_kompletta_ar(temp_filtrerad_test)

    temp_serie = filtrera_smhi_serie_ar(temp_full, ar=valt_ar)
    if temp_serie.station_id is None:
        temp_serie = SmhiParameterSerie(
            parameter_id=temp_serie.parameter_id,
            parameter_namn=temp_serie.parameter_namn,
            enhet=temp_serie.enhet,
            station_id=station.id,
            station_namn=station.namn,
            observationer=temp_serie.observationer,
            antal_totalt=temp_serie.antal_totalt,
            antal_godkanda=temp_serie.antal_godkanda,
            bortfiltrerade=temp_serie.bortfiltrerade,
            bortfiltrerade_per_orsak=temp_serie.bortfiltrerade_per_orsak,
            flaggor_fordelning=temp_serie.flaggor_fordelning,
            godkanda_flaggor=temp_serie.godkanda_flaggor,
        )

    def _hamta_param_med_fallback(param_id: int, nyckel: str) -> SmhiParameterSerie:
        try:
            s, sha = hamta_metobs_parameter_serie(
                param_id,
                station.id,
                period=period,
                godkanda_flaggor=godkanda_flaggor,
                ar=valt_ar,
                bas_url=metobs_bas_url,
                cache_mapp=cache_mapp,
                hamtare=hamtare,
                timeout_s=timeout_s,
            )
            kallor_sha[f"metobs_{nyckel}"] = sha
            if s.antal_godkanda > 0:
                return s
        except SmhiLuftStrangFel:
            pass
        # Om primärstationen saknar parametern hämtas närmaste aktiva station för den parametern.
        st_alt, alt_km, alt_st_sha = valj_narmaste_smhi_station(
            karna_3011,
            param_id,
            bara_aktiva=True,
            bas_url=metobs_bas_url,
            cache_mapp=cache_mapp,
            hamtare=hamtare,
            timeout_s=timeout_s,
        )
        kallor_sha[f"metobs_stationer_{nyckel}"] = alt_st_sha
        s_alt, sha_alt = hamta_metobs_parameter_serie(
            param_id,
            st_alt.id,
            period=period,
            godkanda_flaggor=godkanda_flaggor,
            ar=valt_ar,
            bas_url=metobs_bas_url,
            cache_mapp=cache_mapp,
            hamtare=hamtare,
            timeout_s=timeout_s,
        )
        kallor_sha[f"metobs_{nyckel}"] = sha_alt
        varningar.append(
            f"Parameter {nyckel} ({param_id}) hämtades från alternativ SMHI-station "
            f"{st_alt.namn} ({st_alt.id}, {alt_km:.1f} km)."
        )
        return s_alt

    vind_u_serie = _hamta_param_med_fallback(PARAM_VINDHASTIGHET, "vindhastighet")
    vind_d_serie = _hamta_param_med_fallback(PARAM_VINDRIKTNING, "vindriktning")
    rh_serie = _hamta_param_med_fallback(PARAM_RELATIV_FUKTIGHET, "relativ_fuktighet")

    ned_karta: dict[int, float] = {}
    try:
        ned_serie = _hamta_param_med_fallback(PARAM_NEDERBORD, "nederbord")
        ned_karta = _serie_till_timkarta(ned_serie)
    except SmhiLuftStrangFel as exc:
        varningar.append(
            f"Nederbördsserie (param 7) saknades ({exc}); NORTRIP timserie använder 0 mm/h."
        )

    tryck_karta: dict[int, float] | None = None
    if inkludera_lufttryck:
        try:
            tryck_serie = _hamta_param_med_fallback(PARAM_LUFTTRYCK, "lufttryck")
            tryck_karta = _serie_till_timkarta(tryck_serie)
        except SmhiLuftStrangFel:
            tryck_karta = None

    strang_res: StrangSommarResultat | None = None
    if inkludera_strang:
        try:
            strang_res = hamta_strang_sommarstralning(
                lon,
                lat,
                ar=valt_ar,
                bas_url=strang_bas_url,
                cache_mapp=cache_mapp,
                hamtare=hamtare,
                timeout_s=timeout_s,
            )
            kallor_sha["strang_global_117"] = strang_res.global_sha256
            if strang_res.direkt_normal_sha256:
                kallor_sha["strang_direkt_normal_118"] = strang_res.direkt_normal_sha256
        except SmhiLuftStrangFel as exc:
            varningar.append(
                f"SMHI STRÅNG kunde inte hämtas ({exc}); UTCI saknar STRÅNG-strålning."
            )

    try:
        vindros = berakna_vindros_16(vind_u_serie, vind_d_serie)
    except SmhiVaderFel as exc:
        raise SmhiLuftStrangFel(f"Kunde inte beräkna 16-sektors vindros: {exc}") from exc

    # Sommar-/värmeböljepercentiler för UTCI (med STRÅNG globalstrålning om tillgänglig).
    try:
        utci_sommar = berakna_varmebolja_utci(
            temp_serie,
            relativ_fuktighet=rh_serie,
            vindhastighet=vind_u_serie,
            globalstralning=strang_res.global_serie if strang_res is not None else None,
        )
    except SmhiVaderFel:
        # Om urvalet saknar sommarmånader (t.ex. kort period) kör vi helår med tydlig varning.
        utci_sommar = berakna_varmebolja_utci(
            temp_serie,
            relativ_fuktighet=rh_serie,
            vindhastighet=vind_u_serie,
            globalstralning=strang_res.global_serie if strang_res is not None else None,
            sommarmanader=None,
        )
        varningar.append(
            "Sommarmånader (juni–augusti) saknades i urvalet; UTCI-percentiler beräknades över "
            "alla tillgängliga månader."
        )

    vader_timserie = bygg_nortrip_vader_fran_smhi(
        _serie_till_timkarta(temp_serie),
        ned_karta,
        _serie_till_timkarta(rh_serie),
        _serie_till_timkarta(vind_u_serie),
        lufttryck_hpa_per_h=tryck_karta,
    )
    vader_csv = serialisera_vaderserie_csv(vader_timserie)

    return SmhiVaderPaket(
        station_id=station.id,
        station_namn=station.namn,
        station_avstand_km=round(float(avstand_km), 2),
        station_lat=float(station.lat_grader),
        station_lon=float(station.lon_grader),
        ar=valt_ar,
        ar_komplett=ar_komplett,
        period=period,
        godkanda_flaggor=godkanda_flaggor,
        vindros=vindros,
        utci_sommar=utci_sommar,
        vader_timserie=vader_timserie,
        vader_timserie_csv=vader_csv,
        strang=strang_res,
        kallor_sha256=dict(sorted(kallor_sha.items())),
        varningar=tuple(varningar),
    )


# -------------------------------------------------------------- Urban bakgrundsluft (NO2, PM10, O3)


@dataclass(frozen=True)
class AmneBakgrundStatistik:
    """Statistik och dygnsserie för ett luftföroreningsämne (NO₂, PM₁₀, O₃ eller PM₂.₅)."""

    amne: str
    enhet: str
    arsmedel_ug_m3: float | None
    percentil_90_4_ug_m3: float | None
    max_dygn_ug_m3: float | None
    antal_timmar: int
    antal_dygn: int
    bortfiltrerade: int
    dygnsserie: tuple[tuple[str, float], ...]

    def till_dict(self) -> dict[str, Any]:
        return {
            "amne": self.amne,
            "enhet": self.enhet,
            "arsmedel_ug_m3": self.arsmedel_ug_m3,
            "percentil_90_4_ug_m3": self.percentil_90_4_ug_m3,
            "max_dygn_ug_m3": self.max_dygn_ug_m3,
            "antal_timmar": self.antal_timmar,
            "antal_dygn": self.antal_dygn,
            "bortfiltrerade": self.bortfiltrerade,
            "dygnsserie": [[d, v] for d, v in self.dygnsserie],
        }


@dataclass(frozen=True)
class BakgrundLuftResultat:
    """Urban bakgrundshalt (NO₂, PM₁₀, O₃) med stationsproveniens, SHA-256 och varningar."""

    tillganglig: bool
    station_id: str | None
    station_namn: str | None
    station_typ: str | None
    avstand_km: float | None
    kalla: str
    licens: str
    period_start: str | None
    period_slut: str | None
    sha256: str | None
    amnen: dict[str, AmneBakgrundStatistik]
    varningar: tuple[str, ...] = ()

    def till_dict(self) -> dict[str, Any]:
        return {
            "tillganglig": self.tillganglig,
            "station_id": self.station_id,
            "station_namn": self.station_namn,
            "station_typ": self.station_typ,
            "avstand_km": self.avstand_km,
            "kalla": self.kalla,
            "licens": self.licens,
            "period_start": self.period_start,
            "period_slut": self.period_slut,
            "sha256": self.sha256,
            "amnen": {k: v.till_dict() for k, v in sorted(self.amnen.items())},
            "varningar": list(self.varningar),
        }


def _normalisera_amnesnamn(ra: str) -> str | None:
    s = (
        ra.strip()
        .upper()
        .replace("₂", "2")
        .replace("₁", "1")
        .replace("₀", "0")
        .replace("₃", "3")
        .replace("₅", "5")
        .replace("_UG_M3", "")
        .replace("(µG/M³)", "")
        .replace("(UG/M3)", "")
        .replace("(µG/M3)", "")
        .strip()
    )
    for token in ("NO2", "KVAVEDIOXID", "NITROGEN DIOXIDE"):
        if s == token or s.startswith(token + " ") or s.startswith(token + "("):
            return "NO2"
    for token in ("PM10", "PARTIKLAR PM10"):
        if s == token or s.startswith("PM10"):
            return "PM10"
    for token in ("PM2.5", "PM25", "PM2,5"):
        if s == token or s.startswith(token):
            return "PM2.5"
    for token in ("O3", "OZON", "OZONE"):
        if s == token or s.startswith(token + " ") or s.startswith(token + "("):
            return "O3"
    return None


def _tolka_datum_nyckel(tid_str: str) -> str | None:
    ren = tid_str.strip()
    if len(ren) >= 10 and ren[4] == "-" and ren[7] == "-":
        datum_del = ren[:10]
        try:
            datetime.fromisoformat(datum_del)
            return datum_del
        except ValueError:
            return None
    try:
        ms = float(ren)
        if math.isfinite(ms):
            sec = ms / 1000.0 if abs(ms) > 1e11 else ms
            return datetime.fromtimestamp(sec, tz=UTC).strftime("%Y-%m-%d")
    except ValueError:
        pass
    return None


def _berakna_amne_statistik(
    amne: str,
    poster: Sequence[tuple[str, float]],
    *,
    bortfiltrerade: int = 0,
) -> AmneBakgrundStatistik:
    """Beräknar årsmedel, 90,4-percentil av dygnsmedel och sorterad dygnsserie."""
    giltiga_v: list[float] = []
    per_dag: dict[str, list[float]] = defaultdict(list)
    bort = int(bortfiltrerade)

    for datum_str, val in poster:
        if not math.isfinite(val) or val < 0.0:
            bort += 1
            continue
        dag = _tolka_datum_nyckel(datum_str)
        if dag is None:
            bort += 1
            continue
        giltiga_v.append(float(val))
        per_dag[dag].append(float(val))

    if not giltiga_v:
        return AmneBakgrundStatistik(
            amne=amne,
            enhet="µg/m³",
            arsmedel_ug_m3=None,
            percentil_90_4_ug_m3=None,
            max_dygn_ug_m3=None,
            antal_timmar=0,
            antal_dygn=0,
            bortfiltrerade=bort,
            dygnsserie=(),
        )

    dygnsserie = tuple(
        (dag, round(float(np.mean(np.asarray(vals, dtype=np.float64))), 3))
        for dag, vals in sorted(per_dag.items())
    )
    dygn_arr = np.asarray([v for _, v in dygnsserie], dtype=np.float64)
    arsmedel = round(float(np.mean(np.asarray(giltiga_v, dtype=np.float64))), 3)
    p90_4 = round(float(np.percentile(dygn_arr, 90.4)), 3)
    max_dygn = round(float(np.max(dygn_arr)), 3)
    return AmneBakgrundStatistik(
        amne=amne,
        enhet="µg/m³",
        arsmedel_ug_m3=arsmedel,
        percentil_90_4_ug_m3=p90_4,
        max_dygn_ug_m3=max_dygn,
        antal_timmar=len(giltiga_v),
        antal_dygn=len(dygnsserie),
        bortfiltrerade=bort,
        dygnsserie=dygnsserie,
    )


def _sammanstall_bakgrund_resultat(
    amnes_poster: Mapping[str, list[tuple[str, float]]],
    bort_per_amne: Mapping[str, int],
    *,
    station_id: str | None,
    station_namn: str | None,
    station_typ: str | None,
    avstand_km: float | None,
    kalla: str,
    licens: str,
    sha256: str | None,
    varningar: Sequence[str] = (),
) -> BakgrundLuftResultat:
    amnen: dict[str, AmneBakgrundStatistik] = {}
    alla_dagar: list[str] = []
    for amne, poster in sorted(amnes_poster.items()):
        stat = _berakna_amne_statistik(amne, poster, bortfiltrerade=bort_per_amne.get(amne, 0))
        if stat.antal_dygn > 0:
            amnen[amne] = stat
            alla_dagar.append(stat.dygnsserie[0][0])
            alla_dagar.append(stat.dygnsserie[-1][0])

    tillganglig = bool(amnen)
    varn_ut = list(varningar)
    if not tillganglig:
        varn_ut.append("Inga giltiga mätvärden (NO₂, PM₁₀ eller O₃) hittades i luftunderlaget.")
    return BakgrundLuftResultat(
        tillganglig=tillganglig,
        station_id=station_id,
        station_namn=station_namn,
        station_typ=station_typ,
        avstand_km=round(float(avstand_km), 2) if avstand_km is not None else None,
        kalla=kalla,
        licens=licens,
        period_start=min(alla_dagar) if alla_dagar else None,
        period_slut=max(alla_dagar) if alla_dagar else None,
        sha256=sha256,
        amnen=amnen,
        varningar=tuple(varn_ut),
    )


def hamta_datavardluft_bakgrund(
    karna_3011: tuple[float, float, float, float],
    *,
    timeout_s: float = DATAVARDLUFT_TIMEOUT_S,
    max_avstand_km: float = 50.0,
    bas_url: str = SMHI_DATAVARDLUFT_BAS_URL,
    cache_mapp: str | Path | None = None,
    hamtare: HamtareFn | None = None,
) -> BakgrundLuftResultat:
    """Kort-timeout klient mot SMHI Datavärdskap luftkvalitet (`datavardluft.smhi.se/52North`).

    Har alltid graciös, icke-kraschande fallback vid timeout eller tjänstefel (returnerar
    `tillganglig=False`, tom `amnen` och tydlig varning — aldrig påhittade värden).
    Värd-allowlist kontrolleras dock alltid strikt (SSRF-skydd kastar `SmhiLuftStrangFel`).
    """
    kontrollera_smhi_url(bas_url)
    lon, lat = karna_mittpunkt_wgs84(karna_3011)
    url_ts = f"{bas_url.rstrip('/')}/timeseries?expanded=true"
    kontrollera_smhi_url(url_ts)

    try:
        ra_ts, sha_ts = _hamta_med_cache(
            url_ts,
            filnamn="datavardluft_timeseries.json",
            kalla=SMHI_KALLA_DATAVARDLUFT,
            cache_mapp=cache_mapp,
            hamtare=hamtare,
            timeout_s=timeout_s,
        )
        ts_lista = json.loads(ra_ts.decode("utf-8"))
        if not isinstance(ts_lista, list):
            raise SmhiLuftStrangFel("Oväntat format från 52North /timeseries (inte JSON-lista).")
    except Exception as exc:  # noqa: BLE001 - graciös icke-kraschande fallback enligt plan
        return BakgrundLuftResultat(
            tillganglig=False,
            station_id=None,
            station_namn=None,
            station_typ=None,
            avstand_km=None,
            kalla=SMHI_KALLA_DATAVARDLUFT,
            licens=SMHI_LICENS_SPDX,
            period_start=None,
            period_slut=None,
            sha256=None,
            amnen={},
            varningar=(
                f"SMHI Datavärdskap luftkvalitet svarade inte inom {timeout_s:g} s ({exc}). "
                "Urban bakgrundshalt saknas — importera lokal CSV/JSON eller ange bakgrund "
                "uttryckligen (inga värden har hittats på).",
            ),
        )

    # Matcha närmaste station inom max_avstand_km med NO2, PM10 eller O3.
    # Varje post: (dist_km, st_id, st_namn, ts_id, amne)
    kandidater: list[tuple[float, str, str, str, str]] = []
    for post in ts_lista:
        if not isinstance(post, Mapping):
            continue
        station = post.get("station")
        if not isinstance(station, Mapping):
            continue
        geom = station.get("geometry")
        koord = geom.get("coordinates") if isinstance(geom, Mapping) else None
        if not isinstance(koord, Sequence) or len(koord) < 2:
            continue
        try:
            st_lon, st_lat = float(koord[0]), float(koord[1])
        except (TypeError, ValueError):
            continue
        dist_km = _haversine_km(lat, lon, st_lat, st_lon)
        if dist_km > max_avstand_km:
            continue
        params = post.get("parameters")
        phen = params.get("phenomenon") if isinstance(params, Mapping) else None
        phen_label = str(
            (phen.get("label") if isinstance(phen, Mapping) else "") or post.get("label") or ""
        )
        amne = _normalisera_amnesnamn(phen_label)
        if amne not in ("NO2", "PM10", "O3"):
            continue
        props_ra = station.get("properties")
        props: Mapping[str, Any] = props_ra if isinstance(props_ra, Mapping) else {}
        st_id = str(props.get("id") or station.get("id") or "")
        st_namn = str(props.get("label") or station.get("label") or st_id)
        ts_id = str(post.get("id") or "")
        if ts_id:
            kandidater.append((dist_km, st_id, st_namn, ts_id, amne))

    if not kandidater:
        return BakgrundLuftResultat(
            tillganglig=False,
            station_id=None,
            station_namn=None,
            station_typ=None,
            avstand_km=None,
            kalla=SMHI_KALLA_DATAVARDLUFT,
            licens=SMHI_LICENS_SPDX,
            period_start=None,
            period_slut=None,
            sha256=sha_ts,
            amnen={},
            varningar=(
                f"Ingen luftmätstation med NO₂/PM₁₀/O₃ hittades inom {max_avstand_km:g} km "
                "i SMHI Datavärdskap luftkvalitet.",
            ),
        )

    kandidater.sort(key=lambda x: x[0])
    bast_dist, bast_sid, bast_snamn, _, _ = kandidater[0]
    valda_per_amne: dict[str, tuple[float, str, str, str]] = {}
    for dist_km, st_id, st_namn, ts_id, amne in kandidater:
        if amne not in valda_per_amne:
            valda_per_amne[amne] = (dist_km, st_id, st_namn, ts_id)

    amnes_poster: dict[str, list[tuple[str, float]]] = defaultdict(list)
    bort: dict[str, int] = defaultdict(int)
    sha_delar: list[str] = [sha_ts]
    varningar: list[str] = []

    for amne, (_, _, _, ts_id) in sorted(valda_per_amne.items()):
        url_data = f"{bas_url.rstrip('/')}/timeseries/{urllib.parse.quote(ts_id, safe='')}/getData"
        try:
            ra_d, sha_d = _hamta_med_cache(
                url_data,
                filnamn=f"datavardluft_ts_{ts_id}_{amne}.json",
                kalla=SMHI_KALLA_DATAVARDLUFT,
                cache_mapp=cache_mapp,
                hamtare=hamtare,
                timeout_s=timeout_s,
            )
            sha_delar.append(sha_d)
            dok = json.loads(ra_d.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - graciös per-ämne fallback
            varningar.append(f"Kunde inte hämta {amne}-serie ({ts_id}) från Datavärdskapet: {exc}")
            continue
        varden = dok.get("values") if isinstance(dok, Mapping) else None
        if not isinstance(varden, list):
            continue
        for punkt in varden:
            if not isinstance(punkt, Mapping):
                bort[amne] += 1
                continue
            ts_ms = punkt.get("timestamp")
            val_ra = punkt.get("value")
            if ts_ms is None or val_ra is None or isinstance(val_ra, bool):
                bort[amne] += 1
                continue
            try:
                amnes_poster[amne].append((str(ts_ms), float(val_ra)))
            except (TypeError, ValueError):
                bort[amne] += 1

    samman_sha = sha256_bytes("|".join(sha_delar).encode("utf-8"))
    return _sammanstall_bakgrund_resultat(
        amnes_poster,
        bort,
        station_id=bast_sid or None,
        station_namn=bast_snamn or None,
        station_typ="urban_bakgrund",
        avstand_km=bast_dist,
        kalla=SMHI_KALLA_DATAVARDLUFT,
        licens=SMHI_LICENS_SPDX,
        sha256=samman_sha,
        varningar=varningar,
    )


def las_datavardluft_csv_text(
    text: str,
    *,
    station_id: str | None = None,
    station_namn: str | None = None,
    avstand_km: float | None = None,
    kalla: str = SMHI_KALLA_DATAVARDLUFT,
    licens: str = SMHI_LICENS_SPDX,
    sha256: str | None = None,
) -> BakgrundLuftResultat:
    """Deterministisk tolk för CSV från SMHI Datavärdskap luftkvalitet (`NO₂`, `PM₁₀`, `O₃`)."""
    if not isinstance(text, str) or not text.strip():
        raise SmhiLuftStrangFel("Luftkvalitets-CSV är tom.")
    ren_text = text.lstrip("\ufeff")
    rader = ren_text.splitlines()
    eff_sha = sha256 or sha256_bytes(text.encode("utf-8"))

    hittad_station = station_namn
    hittad_id = station_id
    rubrik_idx: int | None = None
    avgransare = ";"

    for i, rad in enumerate(rader):
        if not rad.strip():
            continue
        for sep in (";", ","):
            celler = [c.strip() for c in next(csv.reader([rad], delimiter=sep))]
            low = [c.lower() for c in celler]
            har_tid = any(
                k.startswith(("datum", "tid", "start", "date", "time")) for k in low
            )
            har_amne = any(_normalisera_amnesnamn(c) is not None for c in celler) or (
                any(k in ("ämne", "amne", "parameter", "komponent", "substance") for k in low)
                and any(k in ("värde", "varde", "value", "halt") for k in low)
            )
            if har_tid and har_amne:
                rubrik_idx = i
                avgransare = sep
                break
        if rubrik_idx is not None:
            break
        if ";" in rad or ":" in rad:
            delar = [c.strip() for c in rad.replace(":", ";", 1).split(";")]
            if len(delar) >= 2 and delar[0].lower() in ("station", "stationsnamn", "mätstation"):
                hittad_station = hittad_station or delar[1]
            elif len(delar) >= 2 and delar[0].lower() in (
                "stationsid",
                "stationsnummer",
                "station_id",
            ):
                hittad_id = hittad_id or delar[1]

    if rubrik_idx is None:
        raise SmhiLuftStrangFel(
            "Hittade ingen giltig rubrikrad med datum/tid och luftkolumner (NO2/PM10/O3)."
        )

    lasare = csv.reader(io.StringIO("\n".join(rader[rubrik_idx:])), delimiter=avgransare)
    rubrik = [c.strip() for c in next(lasare)]
    rubrik_low = [c.lower() for c in rubrik]

    idx_tid = next(
        (
            i
            for i, k in enumerate(rubrik_low)
            if k.startswith(("datum", "start", "date", "time", "tid"))
        ),
        None,
    )
    if idx_tid is None:
        raise SmhiLuftStrangFel("Luftkvalitets-CSV saknar tids- eller datumkolumn.")

    amne_kolumner: dict[int, str] = {}
    for i, kol in enumerate(rubrik):
        if i == idx_tid:
            continue
        norm = _normalisera_amnesnamn(kol)
        if norm is not None:
            amne_kolumner[i] = norm

    idx_lang_amne = next(
        (
            i
            for i, k in enumerate(rubrik_low)
            if k in ("ämne", "amne", "parameter", "komponent", "substance")
        ),
        None,
    )
    idx_lang_varde = next(
        (i for i, k in enumerate(rubrik_low) if k in ("värde", "varde", "value", "halt")),
        None,
    )

    amnes_poster: dict[str, list[tuple[str, float]]] = defaultdict(list)
    bort: dict[str, int] = defaultdict(int)

    for celler in lasare:
        if not celler or not any(c.strip() for c in celler):
            continue
        tid_cell = celler[idx_tid].strip() if idx_tid < len(celler) else ""
        if not tid_cell:
            continue
        if amne_kolumner:
            for col_i, amne in amne_kolumner.items():
                v_str = celler[col_i].strip() if col_i < len(celler) else ""
                if not v_str:
                    bort[amne] += 1
                    continue
                try:
                    val = float(v_str.replace(",", "."))
                except ValueError:
                    bort[amne] += 1
                    continue
                amnes_poster[amne].append((tid_cell, val))
        elif idx_lang_amne is not None and idx_lang_varde is not None:
            amne_ra = celler[idx_lang_amne].strip() if idx_lang_amne < len(celler) else ""
            amne_norm = _normalisera_amnesnamn(amne_ra)
            if amne_norm is None:
                continue
            v_str = celler[idx_lang_varde].strip() if idx_lang_varde < len(celler) else ""
            try:
                val = float(v_str.replace(",", "."))
            except ValueError:
                bort[amne_norm] += 1
                continue
            amnes_poster[amne_norm].append((tid_cell, val))

    return _sammanstall_bakgrund_resultat(
        amnes_poster,
        bort,
        station_id=hittad_id,
        station_namn=hittad_station,
        station_typ="urban_bakgrund",
        avstand_km=avstand_km,
        kalla=kalla,
        licens=licens,
        sha256=eff_sha,
    )


def las_goteborg_luft_json_data(
    ra_data: dict[str, Any] | list[dict[str, Any]],
    *,
    station: str = "Haga",
    avstand_km: float | None = None,
    sha256: str | None = None,
    kalla: str = "Göteborgs Stad Miljöförvaltningen (öppna luftdata).",
    licens: str = "CC0-1.0",
) -> BakgrundLuftResultat:
    """Tolkar Göteborgs Miljöförvaltnings luft-JSON via `tolka_goteborg_luft_json`."""
    try:
        serie = tolka_goteborg_luft_json(ra_data, station=station)
    except GoteborgDataFel as exc:
        raise SmhiLuftStrangFel(f"Kunde inte tolka Göteborgs luft-JSON: {exc}") from exc

    amnes_poster: dict[str, list[tuple[str, float]]] = defaultdict(list)
    bort: dict[str, int] = defaultdict(int)

    for idx, tid in enumerate(serie.tider_iso):
        for amne, arr in (
            ("NO2", serie.no2_ug_m3),
            ("PM10", serie.pm10_ug_m3),
            ("PM2.5", serie.pm25_ug_m3),
        ):
            v = float(arr[idx])
            if math.isfinite(v):
                amnes_poster[amne].append((tid, v))
            else:
                bort[amne] += 1

    # Plocka även O3 om fältet finns i JSON-posterna (tolka_goteborg_luft_json tar PM10/PM2.5/NO2).
    poster_ra = (
        ra_data.get("values", ra_data.get("data", []))
        if isinstance(ra_data, dict)
        else ra_data
    )
    if isinstance(poster_ra, list):
        for post in poster_ra:
            if not isinstance(post, Mapping):
                continue
            tid = str(post.get("time") or post.get("tid") or post.get("timestamp") or "").strip()
            if not tid:
                continue
            for nyckel in ("o3", "O3", "o3_ug_m3"):
                if nyckel in post and post[nyckel] not in (None, ""):
                    try:
                        amnes_poster["O3"].append((tid, float(post[nyckel])))
                    except (TypeError, ValueError):
                        bort["O3"] += 1
                    break

    kanonisk = json.dumps(ra_data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    eff_sha = sha256 or sha256_bytes(kanonisk.encode("utf-8"))
    return _sammanstall_bakgrund_resultat(
        amnes_poster,
        bort,
        station_id=None,
        station_namn=serie.station,
        station_typ="urban_bakgrund",
        avstand_km=avstand_km,
        kalla=kalla,
        licens=licens,
        sha256=eff_sha,
    )


def las_bakgrund_luft_fil(
    sokvag: str | Path,
    *,
    station_namn: str | None = None,
    avstand_km: float | None = None,
    kalla: str | None = None,
) -> BakgrundLuftResultat:
    """Läser lokal bakgrundsluftfil (SMHI CSV/JSON eller Göteborg JSON) med SHA-256."""
    fil = Path(sokvag)
    if not fil.is_file():
        raise SmhiLuftStrangFel(f"Luftkvalitetsfilen '{fil}' finns inte.")
    fil_sha = sha256_fil(fil)
    try:
        text = fil.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = fil.read_text(encoding="latin-1")

    strip = text.lstrip()
    if fil.suffix.lower() == ".json" or strip.startswith(("{", "[")):
        try:
            ra = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SmhiLuftStrangFel(f"Ogiltig JSON i luftkvalitetsfil '{fil}': {exc}") from exc

        eff_station = (
            station_namn
            or (str(ra.get("station", "")).strip() if isinstance(ra, dict) else "")
            or fil.stem
        )
        return las_goteborg_luft_json_data(
            ra,
            station=eff_station,
            avstand_km=avstand_km,
            sha256=fil_sha,
            kalla=kalla or f"Lokal luftfil ({fil.name})",
        )

    return las_datavardluft_csv_text(
        text,
        station_namn=station_namn,
        avstand_km=avstand_km,
        kalla=kalla or f"SMHI Datavärdskap luftkvalitet ({fil.name})",
        sha256=fil_sha,
    )


def las_goteborg_luft_csv_text(
    text: str,
    *,
    station_prefix: str = GOTEBORG_LUFT_STATION_FEMMAN_NAMN,
    ar: int | None = None,
    station_id: str | None = GOTEBORG_LUFT_STATION_FEMMAN_ID,
    station_namn: str | None = GOTEBORG_LUFT_STATION_FEMMAN_NAMN,
    station_typ: str = "urban_bakgrund",
    avstand_km: float | None = None,
    kalla: str = GOTEBORG_KALLA_LUFT,
    licens: str = GOTEBORG_LUFT_LICENS_SPDX,
    sha256: str | None = None,
) -> BakgrundLuftResultat:
    """Tolkar Göteborgs Miljöförvaltnings breda timdata-CSV (`Date,Time,Femman_NO2,...`).

    Filtrerar deterministiskt till angivet kalenderår `ar` (eller senaste kalenderår med >= 200
    mätdygn i filen) så att flerårsfiler aldrig blandas i ett enda årsmedelvärde.
    """
    if not isinstance(text, str) or not text.strip():
        raise SmhiLuftStrangFel("Göteborgs luftkvalitets-CSV är tom.")
    eff_sha = sha256 or sha256_bytes(text.encode("utf-8"))
    lasare = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    try:
        rubrik = [c.strip() for c in next(lasare)]
    except StopIteration as exc:
        raise SmhiLuftStrangFel("Göteborgs luftkvalitets-CSV saknar rubrikrad.") from exc

    rubrik_low = [c.lower() for c in rubrik]
    idx_datum = next((i for i, k in enumerate(rubrik_low) if k in ("date", "datum")), None)
    if idx_datum is None:
        raise SmhiLuftStrangFel("Göteborgs luftkvalitets-CSV saknar Date/Datum-kolumn.")

    pref_low = station_prefix.strip().lower() + "_"
    amne_map = {
        "no2": "NO2",
        "pm10": "PM10",
        "pm25": "PM2.5",
        "pm2_5": "PM2.5",
        "o3": "O3",
    }
    amne_kolumner: dict[int, str] = {}
    for i, kol_low in enumerate(rubrik_low):
        if kol_low.startswith(pref_low):
            suffix = kol_low[len(pref_low) :]
            amne = amne_map.get(suffix)
            if amne is not None:
                amne_kolumner[i] = amne

    if not amne_kolumner:
        raise SmhiLuftStrangFel(
            f"Hittade inga luftkolumner (NO2/PM10/PM25/O3) för station '{station_prefix}' "
            "i Göteborgs luftkvalitets-CSV."
        )

    # Läs alla rader uppdelade per kalenderår så att rätt år väljs deterministiskt.
    per_ar_poster: dict[int, dict[str, list[tuple[str, float]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    per_ar_dagar: dict[int, set[str]] = defaultdict(set)
    per_ar_bort: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for celler in lasare:
        if not celler or idx_datum >= len(celler):
            continue
        dag = _tolka_datum_nyckel(celler[idx_datum])
        if dag is None:
            continue
        rad_ar = int(dag[:4])
        if ar is not None and rad_ar != ar:
            continue
        har_varde_pa_rad = False
        for col_i, amne in amne_kolumner.items():
            v_str = celler[col_i].strip() if col_i < len(celler) else ""
            if not v_str:
                continue
            try:
                val = float(v_str.replace(",", "."))
            except ValueError:
                per_ar_bort[rad_ar][amne] += 1
                continue
            if not math.isfinite(val) or val < 0.0:
                per_ar_bort[rad_ar][amne] += 1
                continue
            per_ar_poster[rad_ar][amne].append((dag, val))
            har_varde_pa_rad = True
        if har_varde_pa_rad:
            per_ar_dagar[rad_ar].add(dag)

    varningar: list[str] = [
        f"Göteborgs Stad Miljöförvaltningen ({station_namn or station_prefix}, {station_typ}): "
        "ovaliderad timdata ur catalog.goteborg.se (används som tidigt skissunderlag)."
    ]
    if not per_ar_dagar:
        return BakgrundLuftResultat(
            tillganglig=False,
            station_id=station_id,
            station_namn=station_namn or station_prefix,
            station_typ=station_typ,
            avstand_km=round(float(avstand_km), 2) if avstand_km is not None else None,
            kalla=kalla,
            licens=licens,
            period_start=None,
            period_slut=None,
            sha256=eff_sha,
            amnen={},
            varningar=(
                f"Inga giltiga luftmätningar hittades för station '{station_prefix}'"
                + (f" år {ar}" if ar is not None else "")
                + " i Göteborgs luftkvalitets-CSV.",
            ),
        )

    if ar is not None and ar in per_ar_dagar:
        valt_ar = int(ar)
    else:
        kompletta = [y for y in sorted(per_ar_dagar, reverse=True) if len(per_ar_dagar[y]) >= 200]
        valt_ar = kompletta[0] if kompletta else max(per_ar_dagar)
        if not kompletta:
            n_dygn = len(per_ar_dagar[valt_ar])
            varningar.append(
                f"Kalenderåret {valt_ar} har färre än 200 mätdygn ({n_dygn} dygn)."
            )

    return _sammanstall_bakgrund_resultat(
        per_ar_poster[valt_ar],
        per_ar_bort[valt_ar],
        station_id=station_id,
        station_namn=station_namn or station_prefix,
        station_typ=station_typ,
        avstand_km=avstand_km,
        kalla=kalla,
        licens=licens,
        sha256=eff_sha,
        varningar=varningar,
    )


def hamta_goteborg_luft_bakgrund(
    karna_3011: tuple[float, float, float, float],
    *,
    ar: int | None = None,
    max_avstand_km: float = 30.0,
    url: str = GOTEBORG_LUFT_CSV_URL,
    cache_mapp: str | Path | None = None,
    hamtare: HamtareFn | None = None,
    timeout_s: float = STANDARD_TIMEOUT_S,
) -> BakgrundLuftResultat:
    """Hämtar urban bakgrundsluft från Göteborgs Stads öppna CSV-API (Femman, 0 API-nycklar).

    Returnerar alltid graciöst `tillganglig=False` om området ligger utanför `max_avstand_km`
    eller om tjänsten inte svarar — aldrig påhittade halter.
    """
    lon, lat = karna_mittpunkt_wgs84(karna_3011)
    femman_lat, femman_lon = GOTEBORG_LUFT_STATION_FEMMAN_WGS84
    dist_km = _haversine_km(lat, lon, femman_lat, femman_lon)
    if dist_km > max_avstand_km:
        return BakgrundLuftResultat(
            tillganglig=False,
            station_id=None,
            station_namn=None,
            station_typ=None,
            avstand_km=None,
            kalla=GOTEBORG_KALLA_LUFT,
            licens=GOTEBORG_LUFT_LICENS_SPDX,
            period_start=None,
            period_slut=None,
            sha256=None,
            amnen={},
            varningar=(
                f"Göteborgs bakgrundsstation Femman ligger {dist_km:.1f} km bort "
                f"(> {max_avstand_km:g} km) och används inte för området.",
            ),
        )

    try:
        ra, sha = hamta_goteborg_luft_csv(
            url=url,
            cache_mapp=cache_mapp,
            hamta_bytes=hamtare,
            timeout_s=timeout_s,
        )
        text = ra.decode("utf-8-sig")
    except Exception as exc:  # noqa: BLE001 - graciös icke-kraschande fallback
        return BakgrundLuftResultat(
            tillganglig=False,
            station_id=None,
            station_namn=None,
            station_typ=None,
            avstand_km=None,
            kalla=GOTEBORG_KALLA_LUFT,
            licens=GOTEBORG_LUFT_LICENS_SPDX,
            period_start=None,
            period_slut=None,
            sha256=None,
            amnen={},
            varningar=(
                f"Göteborgs öppna luftkvalitets-CSV kunde inte hämtas ({exc}). "
                "Urban bakgrundshalt saknas.",
            ),
        )

    res = las_goteborg_luft_csv_text(
        text,
        station_prefix=GOTEBORG_LUFT_STATION_FEMMAN_NAMN,
        ar=ar,
        station_id=GOTEBORG_LUFT_STATION_FEMMAN_ID,
        station_namn=GOTEBORG_LUFT_STATION_FEMMAN_NAMN,
        station_typ="urban_bakgrund",
        avstand_km=dist_km,
        kalla=GOTEBORG_KALLA_LUFT,
        licens=GOTEBORG_LUFT_LICENS_SPDX,
        sha256=sha,
    )
    if not res.tillganglig and ar is not None:
        # Om önskat SMHI-år saknas i Göteborgs CSV, pröva senaste kompletta år i CSV:n.
        return las_goteborg_luft_csv_text(
            text,
            station_prefix=GOTEBORG_LUFT_STATION_FEMMAN_NAMN,
            ar=None,
            station_id=GOTEBORG_LUFT_STATION_FEMMAN_ID,
            station_namn=GOTEBORG_LUFT_STATION_FEMMAN_NAMN,
            station_typ="urban_bakgrund",
            avstand_km=dist_km,
            kalla=GOTEBORG_KALLA_LUFT,
            licens=GOTEBORG_LUFT_LICENS_SPDX,
            sha256=sha,
        )
    return res


__all__ = [
    "DATAVARDLUFT_TIMEOUT_S",
    "SMHI_DATAVARDLUFT_BAS_URL",
    "SMHI_KALLA_DATAVARDLUFT",
    "SMHI_KALLA_METOBS",
    "SMHI_KALLA_STRANG",
    "SMHI_LICENS_SPDX",
    "SMHI_LICENS_TEXT",
    "SMHI_STRANG_BAS_URL",
    "STRANG_PARAM_CIE_UV",
    "STRANG_PARAM_DIREKT_NORMAL",
    "STRANG_PARAM_GLOBAL",
    "TILLATNA_SMHI_VARDAR",
    "AmneBakgrundStatistik",
    "BakgrundLuftResultat",
    "SmhiLuftStrangFel",
    "SmhiVaderPaket",
    "StrangSommarResultat",
    "bygg_strang_url",
    "filtrera_smhi_serie_ar",
    "hamta_datavardluft_bakgrund",
    "hamta_goteborg_luft_bakgrund",
    "hamta_metobs_parameter_serie",
    "hamta_metobs_stationer",
    "hamta_smhi_vaderpaket",
    "hamta_strang_parameter",
    "hamta_strang_sommarstralning",
    "karna_mittpunkt_wgs84",
    "kontrollera_smhi_url",
    "las_bakgrund_luft_fil",
    "las_datavardluft_csv_text",
    "las_goteborg_luft_csv_text",
    "las_goteborg_luft_json_data",
    "las_vaderserie_csv",
    "serialisera_vaderserie_csv",
    "tolka_strang_json",
    "valj_narmaste_smhi_station",
    "valj_senaste_kompletta_ar",
]
