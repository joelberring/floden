# ruff: noqa: E501
"""Klient för SLU Artdatabanken Species Observation System (SOS) API v1 med hård OSL- och PII-spärr.

Säkerhetsinvarianter (OSL 20 kap. 1 § och `AGENTS.md`):
1. Strikt HTTPS + värd-allowlist (`api.artdatabanken.se`). API-nyckeln skickas enbart i
   headern `Ocp-Apim-Subscription-Key` och skrivs ALDRIG till loggar, felmeddelanden,
   returvärden eller manifest.
2. Begär enbart publika observationer av rödlistade (`NT, VU, EN, CR, RE`) och/eller fridlysta
   arter inom kärna + buffert (förval senaste 10 år).
3. **Hård klient-side OSL-spärr (fail-closed):** Varje observation där någon flagga indikerar
   skyddsklassning, sekretess eller diffusering (`isProtected`, `protected`, `sensitive`,
   `diffused`, `coordinateUncertaintyInMeters >= 5000`, `blurRadius > 0`, `accessLevel != public`,
   `informationWithheld`, `dataGeneralizations` m.fl.) **kastas ovillkorligt**: inga koordinater
   eller artuppgifter sparas för posten, den räknas enbart i den anonyma heltalsräknaren
   `antal_skyddsklassade_filtrerade`.
4. **0 PII:** Observatörsnamn (`recordedBy`, `reportedBy`, `user`, `email`), kommentarer och
   fria fritextfält läses/sparas ALDRIG — enbart explicit tillåtna fält (`taxon_id`,
   `svenskt_namn`, `vetenskapligt_namn`, `rodlistekategori`, `fridlyst`, `fyndar`,
   `osakerhet_m`, `e_m`, `n_m` i EPSG:3011).
5. Returnerar alltid den obligatoriska ansvarsvarningen `OSL_VARNING`.
"""

from __future__ import annotations

import inspect
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from shapely.geometry import Point, box

from data_inlasning.cache import DataInlasningFel
from gemensamt.hash import sha256_bytes
from gemensamt.koordinater import EPSG_INTERN, transformera

EPSG_WGS84: Final[int] = 4326
EPSG_SWEREF99_TM: Final[int] = 3006

ARTDATABANKEN_SOS_BAS_URL: Final[str] = "https://api.artdatabanken.se/species-observation-system/v1"
TILLATNA_ARTDATABANKEN_VARDAR: Final[frozenset[str]] = frozenset({"api.artdatabanken.se"})

RODLISTEKATEGORIER: Final[frozenset[str]] = frozenset({"NT", "VU", "EN", "CR", "RE"})
ALLA_RODLISTEKODER: Final[frozenset[str]] = frozenset({"LC", "NT", "VU", "EN", "CR", "RE", "DD", "NE", "NA"})
MAX_TILLATEN_OSAKERHET_M: Final[float] = 5000.0
OSL_VARNING: Final[str] = (
    "Sekretessprövade skyddsklassade arter ingår inte — kontrollera alltid via behörig ekolog eller Länsstyrelsen."
)

# Nycklar (gemener) vars sanningsvärde eller icke-tomma sträng indikerar skyddsklassning/diffusering.
_OSL_BOOL_NYCKLAR: Final[frozenset[str]] = frozenset(
    {
        "isprotected",
        "protected",
        "issensitive",
        "sensitive",
        "diffused",
        "isdiffused",
        "ishidden",
        "hidden",
        "isconfidential",
        "confidential",
        "isredacted",
        "redacted",
        "obscured",
        "isobscured",
        "generalized",
        "isgeneralized",
        "skyddsklassad",
        "sekretess",
        "diffuserad",
        "issensitivespecies",
        "hasprotectedlocation",
    }
)
_OSL_TEXT_NYCKLAR: Final[frozenset[str]] = frozenset(
    {
        "informationwithheld",
        "datageneralizations",
        "protectionreason",
        "sensitivityreason",
    }
)
_OSL_RADIE_NYCKLAR: Final[frozenset[str]] = frozenset(
    {
        "blurradius",
        "blurradiusinmeters",
        "diffusionradius",
        "diffusionradiusinmeters",
        "diffusiongridsize",
        "generalizationmeters",
    }
)
_OSL_OSAKERHET_NYCKLAR: Final[frozenset[str]] = frozenset(
    {
        "coordinateuncertaintyinmeters",
        "accuracy",
        "uncertaintyinmeters",
        "coordinateaccuracy",
    }
)
_OSL_ACCESS_NYCKLAR: Final[frozenset[str]] = frozenset(
    {
        "accesslevel",
        "publicationaccess",
        "visibility",
    }
)
_OSL_NIVA_NYCKLAR: Final[frozenset[str]] = frozenset(
    {
        "sensitivitylevel",
        "protectionlevel",
        "securitylevel",
        "skyddsklass",
    }
)
_PUBLIKA_ACCESSVARDEN: Final[frozenset[str]] = frozenset({"public", "open", "publik", "öppen"})
_OPROTECTED_NIVAVARDEN: Final[frozenset[str]] = frozenset(
    {"", "0", "none", "false", "public", "open", "ej skyddsklassad", "unprotected"}
)
_FALSKA_STRANGAR: Final[frozenset[str]] = frozenset({"", "0", "false", "no", "nej", "none", "null"})
_EPOST_RE: Final[re.Pattern[str]] = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_AR_RE: Final[re.Pattern[str]] = re.compile(r"\b((?:19|20)\d{2})\b")

SosHamtareFn = Callable[..., bytes]


class ArtdatabankenFel(DataInlasningFel):
    """Fel vid anrop eller tolkning mot SLU Artdatabanken SOS API (innehåller aldrig API-nyckel)."""


@dataclass(frozen=True)
class PubliktArtfynd:
    """Ett publikt, dataminimerat artfynd utan PII eller skyddsklassade koordinater (EPSG:3011)."""

    taxon_id: int
    svenskt_namn: str
    vetenskapligt_namn: str
    rodlistekategori: str | None
    fridlyst: bool
    fyndar: int
    osakerhet_m: float | None
    e_m: float
    n_m: float

    def som_geojson_feature(self) -> dict[str, Any]:
        return {
            "type": "Feature",
            "properties": {
                "taxon_id": self.taxon_id,
                "svenskt_namn": self.svenskt_namn,
                "vetenskapligt_namn": self.vetenskapligt_namn,
                "rodlistekategori": self.rodlistekategori,
                "fridlyst": self.fridlyst,
                "fyndar": self.fyndar,
                "osakerhet_m": self.osakerhet_m,
            },
            "geometry": {
                "type": "Point",
                "coordinates": [round(self.e_m, 2), round(self.n_m, 2)],
            },
        }


@dataclass(frozen=True)
class ArtSammanfattning:
    """Samlad post per unik rödlistad/fridlyst art bland publika fynd (0 PII)."""

    taxon_id: int
    svenskt_namn: str
    vetenskapligt_namn: str
    rodlistekategori: str | None
    fridlyst: bool
    antal_fynd: int
    senaste_fyndar: int
    minsta_avstand_m: float

    def som_dict(self) -> dict[str, Any]:
        return {
            "taxon_id": self.taxon_id,
            "svenskt_namn": self.svenskt_namn,
            "vetenskapligt_namn": self.vetenskapligt_namn,
            "rodlistekategori": self.rodlistekategori,
            "fridlyst": self.fridlyst,
            "antal_fynd": self.antal_fynd,
            "senaste_fyndar": self.senaste_fyndar,
            "minsta_avstand_m": round(self.minsta_avstand_m, 2),
        }


@dataclass(frozen=True)
class ArtfyndResultat:
    """Deterministiskt, OSL- och PII-filtrerat resultat från SLU Artdatabanken SOS."""

    karna_3011: tuple[float, float, float, float]
    buffert_m: float
    start_ar: int
    slut_ar: int
    fynd: tuple[PubliktArtfynd, ...]
    artlista: tuple[ArtSammanfattning, ...]
    antal_skyddsklassade_filtrerade: int
    antal_bortfiltrerade_ej_prioriterade: int
    kalla_sha256: str
    osl_varning: str = OSL_VARNING
    varningar: tuple[str, ...] = ()
    licens: str = "CC BY 4.0 / CC0"
    kalla: str = "SLU Artdatabanken SOS API v1 (endast publika fynd)"


# ------------------------------------------------------------------ säker URL & HTTP


def _kontrollera_sos_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise ArtdatabankenFel(f"Endast HTTPS tillåts för SLU Artdatabanken, fick schema {parsed.scheme!r}.")
    if parsed.username or parsed.password:
        raise ArtdatabankenFel("Inloggningsuppgifter i URL tillåts inte för SLU Artdatabanken.")
    host = (parsed.hostname or "").lower()
    if host not in TILLATNA_ARTDATABANKEN_VARDAR:
        raise ArtdatabankenFel(
            f"Värden {host!r} hör inte till tillåtna värdar för SLU Artdatabanken: "
            f"{sorted(TILLATNA_ARTDATABANKEN_VARDAR)}."
        )


def _sanera_feltext(text: str, hemlighet: str) -> str:
    if hemlighet and hemlighet in text:
        return text.replace(hemlighet, "***")
    return text


def _standard_sos_post(
    url: str,
    headers: Mapping[str, str],
    kropp: bytes,
    *,
    timeout_s: float = 25.0,
) -> bytes:
    _kontrollera_sos_url(url)
    req_headers = dict(headers)
    req_headers.setdefault("User-Agent", "Floden/1.0 (ekologi-oppnadata)")
    req = urllib.request.Request(url, data=kropp, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - HTTPS + allowlist
            return bytes(resp.read())
    except urllib.error.HTTPError as exc:
        raise ArtdatabankenFel(f"SLU Artdatabanken svarade med HTTP {exc.code}.") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ArtdatabankenFel(f"Kunde inte nå SLU Artdatabanken: {type(exc).__name__}.") from None


def _anropa_sos(
    hamtare: SosHamtareFn | None,
    url: str,
    headers: Mapping[str, str],
    kropp: bytes,
    api_nyckel: str,
    *,
    timeout_s: float = 25.0,
) -> bytes:
    _kontrollera_sos_url(url)
    if hamtare is None:
        return _standard_sos_post(url, headers, kropp, timeout_s=timeout_s)
    try:
        sig = inspect.signature(hamtare)
        pos = [
            p
            for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values())
        if varargs or len(pos) >= 3:
            ra = hamtare(url, headers, kropp)
        elif len(pos) == 2:
            ra = hamtare(url, headers)
        else:
            ra = hamtare(url)
    except ArtdatabankenFel:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ArtdatabankenFel(f"Fel i SLU Artdatabanken-hämtare: {_sanera_feltext(str(exc), api_nyckel)}") from None
    if not isinstance(ra, bytes):
        raise ArtdatabankenFel(f"Hämtaren returnerade {type(ra).__name__}, förväntade bytes.")
    return ra


# ------------------------------------------------------------------ OSL 20 kap. 1 § & 0 PII-filter


def _iter_nyckel_varden(obj: Any) -> list[tuple[str, Any]]:
    """Plattar rekursivt ut (nyckel_gemen, värde) ur en JSON-post (utan att tolka fritext)."""
    ut: list[tuple[str, Any]] = []
    stack: list[Any] = [obj]
    while stack:
        nu = stack.pop()
        if isinstance(nu, Mapping):
            for k, v in nu.items():
                ut.append((str(k).lower(), v))
                if isinstance(v, Mapping | list):
                    stack.append(v)
        elif isinstance(nu, list):
            for item in nu:
                if isinstance(item, Mapping | list):
                    stack.append(item)
    return ut


def _ar_sant_flagg(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, int | float):
        return math.isfinite(float(v)) and float(v) != 0.0
    if isinstance(v, str):
        return v.strip().lower() not in _FALSKA_STRANGAR
    return False


def _tal(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def ar_skyddsklassad_eller_diffuserad(post: Mapping[str, Any]) -> bool:
    """Returnerar True om observationen bär någon indikation på skyddsklassning, sekretess eller diffusering.

    Princip: fail-closed (vid minsta indikation kastas koordinaterna ovillkorligt).
    """
    for nyckel, varde in _iter_nyckel_varden(post):
        if nyckel in _OSL_BOOL_NYCKLAR and _ar_sant_flagg(varde):
            return True
        if nyckel in _OSL_TEXT_NYCKLAR and isinstance(varde, str) and varde.strip().lower() not in _FALSKA_STRANGAR:
            return True
        if nyckel in _OSL_RADIE_NYCKLAR:
            r = _tal(varde)
            if r is not None and r > 0.0:
                return True
            if isinstance(varde, str) and varde.strip().lower() not in _FALSKA_STRANGAR:
                return True
        if nyckel in _OSL_OSAKERHET_NYCKLAR and varde is not None:
            u = _tal(varde)
            if u is None or u >= MAX_TILLATEN_OSAKERHET_M or u < 0.0:
                return True
        if nyckel in _OSL_ACCESS_NYCKLAR and varde is not None:
            s = str(varde).strip().lower()
            if s and s not in _PUBLIKA_ACCESSVARDEN:
                return True
        if nyckel in _OSL_NIVA_NYCKLAR and varde is not None:
            s = str(varde).strip().lower()
            if s not in _OPROTECTED_NIVAVARDEN:
                return True
    return False


def _sanera_artnamn(varde: Any) -> str:
    if not isinstance(varde, str):
        return ""
    s = " ".join(varde.split()).strip()
    if not s or _EPOST_RE.search(s):
        return ""
    return s[:120]


def _extrahera_koordinat_3011(post: Mapping[str, Any]) -> tuple[float, float] | None:
    loc_raw = post.get("location")
    loc: Mapping[str, Any] = loc_raw if isinstance(loc_raw, Mapping) else post
    # 1. Explicit EPSG:3011
    e3011 = _tal(loc.get("e_m"))
    n3011 = _tal(loc.get("n_m"))
    if e3011 is not None and n3011 is not None:
        return (e3011, n3011)

    # 2. WGS 84 (decimalLatitude / decimalLongitude)
    lat = _tal(loc.get("decimalLatitude"))
    lon = _tal(loc.get("decimalLongitude"))
    if lat is None:
        lat = _tal(post.get("decimalLatitude"))
    if lon is None:
        lon = _tal(post.get("decimalLongitude"))
    if lat is not None and lon is not None and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
        return transformera(lon, lat, EPSG_WGS84, EPSG_INTERN)

    # 3. SWEREF 99 TM (east / north)
    east = _tal(loc.get("east") or loc.get("sweref99East") or post.get("east"))
    north = _tal(loc.get("north") or loc.get("sweref99North") or post.get("north"))
    if east is not None and north is not None:
        return transformera(east, north, EPSG_SWEREF99_TM, EPSG_INTERN)

    # 4. GeoJSON Point (location.point / location.geometry / geometry)
    for behallare in (loc.get("point"), loc.get("geometry"), post.get("geometry")):
        if isinstance(behallare, Mapping) and behallare.get("type") == "Point":
            coords = behallare.get("coordinates")
            if isinstance(coords, list | tuple) and len(coords) >= 2:
                x, y = _tal(coords[0]), _tal(coords[1])
                if x is not None and y is not None:
                    if abs(x) <= 180.0 and abs(y) <= 90.0:
                        return transformera(x, y, EPSG_WGS84, EPSG_INTERN)
                    return transformera(x, y, EPSG_SWEREF99_TM, EPSG_INTERN)
    return None


def _extrahera_fyndar(post: Mapping[str, Any]) -> int | None:
    ev_raw = post.get("event")
    ev: Mapping[str, Any] = ev_raw if isinstance(ev_raw, Mapping) else {}
    for kand in (
        post.get("fyndar"),
        post.get("year"),
        ev.get("startDate"),
        ev.get("endDate"),
        post.get("startDate"),
        post.get("eventDate"),
    ):
        if isinstance(kand, int) and not isinstance(kand, bool) and 1800 <= kand <= 2100:
            return kand
        if isinstance(kand, str):
            m = _AR_RE.search(kand)
            if m:
                return int(m.group(1))
    return None


def _extrahera_osakerhet_m(post: Mapping[str, Any]) -> float | None:
    loc_raw = post.get("location")
    loc: Mapping[str, Any] = loc_raw if isinstance(loc_raw, Mapping) else {}
    for kand in (
        loc.get("coordinateUncertaintyInMeters"),
        loc.get("accuracy"),
        post.get("coordinateUncertaintyInMeters"),
        post.get("accuracy"),
        post.get("osakerhet_m"),
    ):
        u = _tal(kand)
        if u is not None:
            return round(u, 2)
    return None


def _extrahera_taxon(post: Mapping[str, Any]) -> tuple[int, str, str, str | None, bool] | None:
    tax_raw = post.get("taxon")
    tax: Mapping[str, Any] = tax_raw if isinstance(tax_raw, Mapping) else {}
    tid_raw = tax.get("id") if tax.get("id") is not None else post.get("taxonId", post.get("taxon_id"))
    if isinstance(tid_raw, bool) or tid_raw is None:
        return None
    try:
        taxon_id = int(tid_raw)
    except (TypeError, ValueError):
        return None
    if taxon_id <= 0:
        return None

    sv = _sanera_artnamn(
        tax.get("vernacularName") or post.get("vernacularName") or post.get("svenskt_namn") or ""
    )
    vet = _sanera_artnamn(
        tax.get("scientificName") or post.get("scientificName") or post.get("vetenskapligt_namn") or ""
    )
    if not sv and not vet:
        return None

    attrs_raw = tax.get("attributes")
    attrs: Mapping[str, Any] = attrs_raw if isinstance(attrs_raw, Mapping) else {}
    rl_raw = tax.get("redListCategory") or attrs.get("redListCategory")
    if rl_raw is None:
        rl_raw = post.get("redListCategory") or post.get("rodlistekategori")
    rl: str | None = None
    if isinstance(rl_raw, str):
        kod = rl_raw.strip().upper()
        if kod in ALLA_RODLISTEKODER:
            rl = kod

    fridlyst = any(
        _ar_sant_flagg(v)
        for v in (
            tax.get("isProtectedSpecies"),
            tax.get("protectedSpecies"),
            tax.get("fridlyst"),
            attrs.get("isProtectedSpecies"),
            attrs.get("isNationallyProtected"),
            attrs.get("isEuProtected"),
            attrs.get("artskyddsforordningen"),
            post.get("isProtectedSpecies"),
            post.get("fridlyst"),
        )
    )
    return taxon_id, sv or vet, vet or sv, rl, fridlyst


def tolka_och_filtrera_sos_svar(
    poster: Sequence[Mapping[str, Any]],
    *,
    karna_3011: tuple[float, float, float, float],
    buffert_m: float = 1000.0,
    start_ar: int = 2016,
    slut_ar: int = 2026,
    endast_rodlistade_eller_fridlysta: bool = True,
    kalla_sha256: str = "",
) -> ArtfyndResultat:
    """Applicerar den hårda OSL 20 kap. 1 §- och 0 PII-spärren på råa SOS-observationsposter."""
    karna_box = box(*karna_3011)
    fynd: list[PubliktArtfynd] = []
    skyddade_filtrerade = 0
    ej_prioriterade = 0
    varningar: list[str] = []

    for post in poster:
        if not isinstance(post, Mapping):
            continue
        # HÅRD OSL-SPÄRR: kontrollera alltid FÖRST, innan några koordinater/artnamn ens tolkas.
        if ar_skyddsklassad_eller_diffuserad(post):
            skyddade_filtrerade += 1
            continue

        osak = _extrahera_osakerhet_m(post)
        if osak is not None and osak >= MAX_TILLATEN_OSAKERHET_M:
            skyddade_filtrerade += 1
            continue

        taxon = _extrahera_taxon(post)
        if taxon is None:
            continue
        taxon_id, sv_namn, vet_namn, rl, fridlyst = taxon

        if endast_rodlistade_eller_fridlysta and not (
            (rl in RODLISTEKATEGORIER) or fridlyst
        ):
            ej_prioriterade += 1
            continue

        koord = _extrahera_koordinat_3011(post)
        if koord is None:
            continue
        e_m, n_m = koord
        if not (math.isfinite(e_m) and math.isfinite(n_m)):
            continue

        fyndar = _extrahera_fyndar(post)
        if fyndar is None:
            continue

        fynd.append(
            PubliktArtfynd(
                taxon_id=taxon_id,
                svenskt_namn=sv_namn,
                vetenskapligt_namn=vet_namn,
                rodlistekategori=rl,
                fridlyst=fridlyst,
                fyndar=fyndar,
                osakerhet_m=osak,
                e_m=round(e_m, 2),
                n_m=round(n_m, 2),
            )
        )

    fynd.sort(key=lambda f: (f.taxon_id, -f.fyndar, f.e_m, f.n_m))

    # Aggregera artlista
    per_taxon: dict[int, list[PubliktArtfynd]] = {}
    for f in fynd:
        per_taxon.setdefault(f.taxon_id, []).append(f)

    artlista: list[ArtSammanfattning] = []
    for tid, grupp in sorted(per_taxon.items()):
        forsta = grupp[0]
        min_avst = min(float(Point(f.e_m, f.n_m).distance(karna_box)) for f in grupp)
        artlista.append(
            ArtSammanfattning(
                taxon_id=tid,
                svenskt_namn=forsta.svenskt_namn,
                vetenskapligt_namn=forsta.vetenskapligt_namn,
                rodlistekategori=forsta.rodlistekategori,
                fridlyst=any(f.fridlyst for f in grupp),
                antal_fynd=len(grupp),
                senaste_fyndar=max(f.fyndar for f in grupp),
                minsta_avstand_m=round(min_avst, 2),
            )
        )

    rl_ordning = {"RE": 0, "CR": 1, "EN": 2, "VU": 3, "NT": 4}
    artlista.sort(
        key=lambda a: (
            rl_ordning.get(a.rodlistekategori or "", 9),
            0 if a.fridlyst else 1,
            a.minsta_avstand_m,
            a.svenskt_namn.lower(),
            a.taxon_id,
        )
    )
    if skyddade_filtrerade > 0:
        varningar.append(
            f"{skyddade_filtrerade} skyddsklassad(e)/diffuserad(e) post(er) filtrerades bort helt utan koordinater (OSL 20 kap. 1 §)."
        )

    return ArtfyndResultat(
        karna_3011=karna_3011,
        buffert_m=float(buffert_m),
        start_ar=int(start_ar),
        slut_ar=int(slut_ar),
        fynd=tuple(fynd),
        artlista=tuple(artlista),
        antal_skyddsklassade_filtrerade=skyddade_filtrerade,
        antal_bortfiltrerade_ej_prioriterade=ej_prioriterade,
        kalla_sha256=kalla_sha256,
        osl_varning=OSL_VARNING,
        varningar=tuple(varningar),
    )


def _bbox_3011_till_wgs84_polygon(bbox_3011: tuple[float, float, float, float]) -> dict[str, Any]:
    e0, n0, e1, n1 = bbox_3011
    horn = [
        transformera(x, y, EPSG_INTERN, EPSG_WGS84)
        for x, y in ((e0, n0), (e1, n0), (e1, n1), (e0, n1), (e0, n0))
    ]
    return {
        "type": "Polygon",
        "coordinates": [[[round(lon, 6), round(lat, 6)] for lon, lat in horn]],
    }


def hamta_publika_artfynd(
    bbox_3011: tuple[float, float, float, float],
    *,
    api_nyckel: str,
    buffert_m: float = 1000.0,
    ar_bakat: int = 10,
    referensar: int | None = None,
    endast_rodlistade_eller_fridlysta: bool = True,
    sidstorlek: int = 500,
    max_sidor: int = 10,
    bas_url: str = ARTDATABANKEN_SOS_BAS_URL,
    hamtare: SosHamtareFn | None = None,
    timeout_s: float = 25.0,
) -> ArtfyndResultat:
    """Hämtar enbart publika rödlistade/fridlysta artfynd från SLU Artdatabanken SOS API v1.

    API-nyckeln skickas enbart i `Ocp-Apim-Subscription-Key` och exponeras aldrig i loggar/manifest.
    """
    if not isinstance(api_nyckel, str) or not api_nyckel.strip():
        raise ArtdatabankenFel("API-nyckel för SLU Artdatabanken saknas (krävs i Ocp-Apim-Subscription-Key).")
    if len(bbox_3011) != 4:
        raise ArtdatabankenFel("bbox_3011 måste innehålla fyra koordinater.")
    e0, n0, e1, n1 = (float(v) for v in bbox_3011)
    if not all(math.isfinite(v) for v in (e0, n0, e1, n1)) or e0 >= e1 or n0 >= n1:
        raise ArtdatabankenFel(f"Ogiltig bbox_3011: {bbox_3011}.")
    b = float(buffert_m)
    if not math.isfinite(b) or b < 0.0:
        raise ArtdatabankenFel(f"buffert_m måste vara >= 0, fick {buffert_m!r}.")
    if ar_bakat < 1 or ar_bakat > 100:
        raise ArtdatabankenFel(f"ar_bakat måste ligga mellan 1 och 100, fick {ar_bakat!r}.")
    if sidstorlek < 1 or max_sidor < 1:
        raise ArtdatabankenFel("sidstorlek och max_sidor måste vara >= 1.")

    _kontrollera_sos_url(bas_url)
    slut_ar = int(referensar) if referensar is not None else datetime.now(UTC).year
    start_ar = slut_ar - int(ar_bakat)

    sok_3011 = (e0 - b, n0 - b, e1 + b, n1 + b)
    filter_body: dict[str, Any] = {
        "date": {
            "startDate": f"{start_ar}-01-01",
            "endDate": f"{slut_ar}-12-31",
            "dateFilterType": "BetweenStartDateAndEndDate",
        },
        "geographics": {
            "geometries": [_bbox_3011_till_wgs84_polygon(sok_3011)],
        },
        "output": {
            "fieldSet": "Minimum",
            "sensitiveObservations": False,
        },
    }
    if endast_rodlistade_eller_fridlysta:
        filter_body["taxon"] = {
            "redListCategories": sorted(RODLISTEKATEGORIER),
            "includeProtectedSpecies": True,
        }

    kropp = json.dumps(filter_body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Ocp-Apim-Subscription-Key": api_nyckel.strip(),
    }

    alla_poster: list[Mapping[str, Any]] = []
    hashar: list[str] = []
    skip = 0
    for _ in range(max_sidor):
        params = urllib.parse.urlencode({"skip": str(skip), "take": str(int(sidstorlek))})
        url = f"{bas_url.rstrip('/')}/Observations/Search?{params}"
        ra = _anropa_sos(hamtare, url, headers, kropp, api_nyckel.strip(), timeout_s=timeout_s)
        hashar.append(sha256_bytes(ra))
        try:
            dok = json.loads(ra.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtdatabankenFel(f"SLU Artdatabanken svarade med ogiltig JSON: {exc}") from None
        if isinstance(dok, list):
            records = dok
            total = None
        elif isinstance(dok, Mapping):
            rec_raw = dok.get("records", dok.get("observations", dok.get("items", [])))
            if not isinstance(rec_raw, list):
                raise ArtdatabankenFel("SLU Artdatabanken-svaret saknar lista med observationer.")
            records = rec_raw
            total_raw = dok.get("totalCount")
            total = int(total_raw) if isinstance(total_raw, int) and not isinstance(total_raw, bool) else None
        else:
            raise ArtdatabankenFel("Oväntat svarsformat från SLU Artdatabanken.")

        for r in records:
            if isinstance(r, Mapping):
                alla_poster.append(r)

        if len(records) < sidstorlek:
            break
        skip += len(records)
        if total is not None and skip >= total:
            break

    return tolka_och_filtrera_sos_svar(
        alla_poster,
        karna_3011=(e0, n0, e1, n1),
        buffert_m=b,
        start_ar=start_ar,
        slut_ar=slut_ar,
        endast_rodlistade_eller_fridlysta=endast_rodlistade_eller_fridlysta,
        kalla_sha256=sha256_bytes(":".join(hashar).encode("utf-8")),
    )


__all__ = [
    "ALLA_RODLISTEKODER",
    "ARTDATABANKEN_SOS_BAS_URL",
    "MAX_TILLATEN_OSAKERHET_M",
    "OSL_VARNING",
    "RODLISTEKATEGORIER",
    "TILLATNA_ARTDATABANKEN_VARDAR",
    "ArtSammanfattning",
    "ArtdatabankenFel",
    "ArtfyndResultat",
    "PubliktArtfynd",
    "ar_skyddsklassad_eller_diffuserad",
    "hamta_publika_artfynd",
    "tolka_och_filtrera_sos_svar",
]
