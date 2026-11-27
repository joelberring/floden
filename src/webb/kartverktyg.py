# ruff: noqa: E501
"""Kartverktyget: rita ett planförslag i kartan och räkna före och efter (webbens API).

Klienten ritar bara geometri (hus och höjder). Allt annat görs här, med kärnans egna delar:

- **Kontroll** av det ritade (`karna.ritat`, `karna.scenario`, `karna.geometri.tillampa`).
- **K9-scenario** per bas (samma `bas_recept`-regel som `floden demo`): ett ritat hus ger exakt
  samma K9 och därmed samma jobbnycklar och resultat som en handskriven K9-fil.
- **K20-fråga** i den lokala frågemappen (`<kartverktygsmapp>/fragor`, inte i git), byggd ur de
  demofrågor (mallar) som projektet har; körs sedan av webbens vanliga frågetjänst.
- **Sparade scenarier** som lokala filer (`<kartverktygsmapp>/<projekt>/scenarier/<namn>.json`).
- **Underlag**: WMS (GetCapabilities och GetMap via servern som proxy) och ortofoto ur
  `floden data lantmateriet` om det finns lokalt som GeoTIFF.

Inget i detta ritar resultat eller räknar mått: det som visas efter en körning kommer ur kärnans
filer via `FragaTjanst`.
"""

from __future__ import annotations

import http.client
import io
import ipaddress
import json
import logging
import math
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET  # noqa: S405 - svaret begränsas i storlek, inga externa entiteter hämtas
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
from shapely.geometry import Polygon

from gemensamt import avgransning as avgr_mod
from gemensamt.hash import sha256_fil
from gemensamt.objektlagring import ObjektLagringFel
from geografi.forberaknad_lager import (
    STANDARD_RUTSTORLEK_M,
    ForberaknadKommunLager,
    ForberaknadLagerFel,
    tolka_rut_bbox_3011,
)
from geografi.modell import GeografiFel
from geografi.modell import las_manifest as las_geo_manifest
from karna import ritlager as ritl
from karna.geometri import las_byggnader
from karna.geometri.tillampa import valj_k1_version, verifiera_byggnadsreferenser
from karna.k9_till_k1 import dela_andringar
from karna.ritat import (
    HOJD_MAX_M,
    VANINGSHOJD_M,
    RitatFel,
    kontrollera_andringar,
    normalisera_linje,
    normalisera_ring,
)
from karna.rutscenario import Ruta, las_bas
from karna.scenario import Andring, Planscenario, ScenarioFel, skriv_scenario
from karna.utredning import las_fraga
from webb.fel import FlodenWebbFel
from webb.felforklaring import SLUTET_INFO
from webb.fragor import FragaTjanst, skriv_atomiskt_text
from webb.moduler import visningsnamn
from webb.projektkalla import (
    Mall,
    Projektkalla,
    bygg_geografikalla,
    hitta_projektmapp,
    stampel_for,
)
from webb.sektion import RasterLager, berakna_sektion, validera_sektion_punkter

FORMAT = "floden-kartverktyg/v1"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,59}$")
_PROJEKT_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")
MAX_WMS_BYTES = 12 * 1024 * 1024
MAX_CAPABILITIES_BYTES = 4 * 1024 * 1024
WMS_TIDSGRANS_S = 20.0
MAX_WMS_OMDIRIGERINGAR = 5


class _PinnadHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS-anslutning som ansluter direkt till en redan validerad IP-adress (DNS-pinning)
    men behåller ursprungligt värdnamn för TLS SNI och certifikatverifiering."""

    def __init__(
        self,
        pinned_ip: str,
        port: int,
        *,
        sni_host: str,
        timeout: float = WMS_TIDSGRANS_S,
        context: ssl.SSLContext | None = None,
    ) -> None:
        ssl_ctx = context or ssl.create_default_context()
        super().__init__(
            pinned_ip,
            port=port,
            timeout=timeout,
            context=ssl_ctx,
        )
        self._pinned_ip = pinned_ip
        self._sni_host = sni_host
        self._ssl_ctx = ssl_ctx

    def connect(self) -> None:
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._ssl_ctx.wrap_socket(sock, server_hostname=self._sni_host)
MAX_BYGGNADER = 20_000
MAX_BRYTLINJER = 20_000
ORTOFOTO_MAX_PX = 2048
GEOMETRILAGER = ("skuggning", "hojdkurvor", "markhojd", "vegetation", "terrang")
"""Lagren `geometri/<namn>.png` som kan ritas (`terrang` är bara för intern läsning)."""

_PROJEKT_ID = "demokvarteret"

LAGE_IDAG_PREFIX = "lage-idag-"
"""Frågeid-prefix för körningar av läget idag (U5a): bara `fore`, inget ritat förslag."""


_STANDARDJAMFORELSE = {
    "vatten.statisk_fyllning": ("max_djup", 0.01),
    "vatten.dynamik": ("max_djup", 0.01),
    "vatten.2d": ("max_djup", 0.01),
    "dagsljus.soltimmar_mark": ("soltimmar", 0.1),
    "dagsljus.soltimmar_sammanhangande": ("sammanhangande_soltimmar", 0.5),
    "dagsljus.skuggmask_vid_klockslag": ("skuggmask", 0.5),
    "ekologi.konnektivitet": ("stromtathet", 0.01),
}
"""Lösare -> (utdata, tröskel) för jämförelsen före/efter när mallen saknar jämförelser (som `skriv_fragor`)."""

STANDARD_MAX_2D_CELLER = 250_000
"""Standardgräns för antal rasterceller i en 2D-skyfallskörning via webben (förhindrar oavsiktligt gigantiska jobb)."""
BERAKNINGSOMFANG_VARNING_ANDEL = 0.75
"""Andel av `FLODEN_MAX_SANKGRAF_CELLER` från vilken Steg 2 visar lugn förvarning före körning."""
_STANDARD_RUT_OVERLAPP_M = 25.0
"""Standardöverlapp per sida för K1-rutor (m) vid O(1)-uppskattning ur geografimanifest."""
_STANDARD_SKYFALL_FOKUS_MARGINAL_M = 100.0
"""Standardmarginal (m) runt projektets avgränsning för automatiskt skyfallsfokus på äldre projekt."""
_GILTIGA_SKYFALL_METODER = frozenset({"graf", "2d"})
_TVAD_EJ_STODDA_NYCKLAR = ("persistens_faktor", "idf_kalla", "idf_klimatfaktor")

_LOSARE_MED_AVTAPPNING = frozenset({"vatten.statisk_fyllning", "vatten.dynamik", "vatten.2d"})
_LOSARE_MED_REGN = frozenset({"vatten.statisk_fyllning", "vatten.dynamik", "vatten.2d"})
_LOSARE_MED_SOL = frozenset(
    {
        "dagsljus.soltimmar_mark",
        "dagsljus.soltimmar_sammanhangande",
        "dagsljus.skuggmask_vid_klockslag",
    }
)
_SOL_NYCKLAR = frozenset(
    {"datum_iso", "start_timme", "slut_timme", "sammanhangande", "inkludera_vsc", "klockslag_utc"}
)
_DATUM_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DAGSLJUS_GEMENSAMMA_PAR = (
    "azimutriktningar",
    "hojd_max_m",
    "h_min_grader",
    "cellstorlek_m",
    "omsampling",
    "vegetation",
    "enhet",
)
_REGN_NYCKLAR = frozenset(
    {"aterkomsttid_ar", "varaktighet_min", "klimatfaktor", "regn_mm", "regntyp", "berakningstyp"}
)
_GILTIGA_REGNTYPER = frozenset({"cds", "block"})
_GILTIGA_BERAKNINGSTYPER = frozenset({"statisk", "dynamisk"})
_IDF_NYCKLAR_ATT_RENSA = (
    "idf",
    "aterkomsttid_ar",
    "klimatfaktor",
    "regntyp",
    "idf_kalla",
    "idf_klimatfaktor",
)


def _skyfall_metod(v: Any) -> str:
    """Validerar valfri skyfallsmetod (`"graf"` eller `"2d"`, standard `"graf"`)."""
    if v is None:
        return "graf"
    if not isinstance(v, str) or v not in _GILTIGA_SKYFALL_METODER:
        raise _fel(f"Ogiltig skyfall_metod: {v!r}. Ange 'graf' eller '2d'.", 400)
    return v


def kontrollera_2d_cellgrans(
    antal_celler: int, max_2d_celler: int = STANDARD_MAX_2D_CELLER
) -> None:
    """Kontrollerar att 2D-domänen inte överskrider tillåten cellgräns (annars tydligt ValueError)."""
    if isinstance(max_2d_celler, bool) or not isinstance(max_2d_celler, int) or max_2d_celler < 1:
        raise ValueError(f"max_2d_celler måste vara ett heltal >= 1, fick {max_2d_celler!r}.")
    if antal_celler > max_2d_celler:
        raise ValueError(
            f"Området har {antal_celler} rasterceller, vilket överskrider webbgränsen för "
            f"2D-skyfall ({max_2d_celler} celler). Välj metoden 'graf' eller ett mindre projektområde."
        )


def _berakna_2d_celler(mall: Mall) -> int:
    """Beräknar antal rasterceller i 2D-domänen ur K1-rutornas `mark.tif`-huvuden (O(1) utan pixelinläsning)."""
    import rasterio

    if not mall.rutor:
        return 0
    geos: list[tuple[float, float, float, float, float, int, int]] = []
    for r in mall.rutor:
        fil = r.mapp / "mark.tif"
        if not fil.is_file():
            continue
        with rasterio.open(fil) as ds:
            px = abs(float(ds.transform.a))
            b = ds.bounds
            geos.append(
                (
                    float(b.left),
                    float(b.bottom),
                    float(b.right),
                    float(b.top),
                    px,
                    int(ds.width),
                    int(ds.height),
                )
            )
    if not geos:
        return 0
    if len(geos) == 1:
        return geos[0][5] * geos[0][6]
    px0 = min(g[4] for g in geos)
    if px0 <= 0.0:
        return sum(g[5] * g[6] for g in geos)
    x0 = min(g[0] for g in geos)
    y0 = min(g[1] for g in geos)
    x1 = max(g[2] for g in geos)
    y1 = max(g[3] for g in geos)
    return max(1, round((x1 - x0) / px0)) * max(1, round((y1 - y0) / px0))


def _avtappning(v: Any) -> float | None:
    """Avtappning ur kartverktyget (mm/h, >= 0), eller None (lösarens standard 0 mm/h)."""
    if v is None or v == "":
        return None
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
        raise _fel(f"Avtappningen måste vara ett tal i mm/h, fick {v!r}.", 400)
    if float(v) < 0.0:
        raise _fel("Avtappningen får inte vara negativ.", 400)
    return float(v)


def _regn(v: Any) -> dict[str, Any] | None:
    """Validerar valfria regnparametrar ur kartverktyget, eller None (mallens förval gäller).

    Strikt validering utan tysta fel (HTTP 400 vid ogiltiga typer, okända fält, motstridiga
    kombinationer eller värden utanför intervallen):
    - `regn_mm` i `(0, 500]`
    - `aterkomsttid_ar` i `[1, 10000]`
    - `varaktighet_min` i `[5, 2880]`
    - `klimatfaktor` i `[0.5, 3.0]`
    - `regntyp` i `{"cds", "block"}`
    - `berakningstyp` i `{"statisk", "dynamisk"}`
    """
    if v is None:
        return None
    if not isinstance(v, Mapping):
        raise _fel("Regninställningen måste vara ett JSON-objekt.", 400)
    okanda = sorted(str(k) for k in v if k not in _REGN_NYCKLAR)
    if okanda:
        raise _fel(f"Okända regnparametrar: {', '.join(okanda)}.", 400)
    granser: dict[str, tuple[float, float, bool, str]] = {
        "regn_mm": (0.0, 500.0, True, "mm"),
        "aterkomsttid_ar": (1.0, 10000.0, False, "år"),
        "varaktighet_min": (5.0, 2880.0, False, "min"),
        "klimatfaktor": (0.5, 3.0, False, ""),
    }
    ut: dict[str, Any] = {}
    for nyckel, (lo, hi, strikt_lo, enhet) in granser.items():
        raa = v.get(nyckel)
        if raa is None:
            continue
        if isinstance(raa, bool) or not isinstance(raa, int | float) or not math.isfinite(float(raa)):
            raise _fel(f"Regnparametern '{nyckel}' måste vara ett ändligt tal, fick {raa!r}.", 400)
        val = float(raa)
        under = val <= lo if strikt_lo else val < lo
        if under or val > hi:
            parentes = f"({lo:g}, {hi:g}]" if strikt_lo else f"[{lo:g}, {hi:g}]"
            enhet_txt = f" {enhet}" if enhet else ""
            raise _fel(
                f"Regnparametern '{nyckel}' måste ligga i {parentes}{enhet_txt}, fick {val:g}.",
                400,
            )
        ut[nyckel] = val
    for nyckel, giltiga in (
        ("regntyp", _GILTIGA_REGNTYPER),
        ("berakningstyp", _GILTIGA_BERAKNINGSTYPER),
    ):
        raa_s = v.get(nyckel)
        if raa_s is None:
            continue
        if not isinstance(raa_s, str) or raa_s not in giltiga:
            alternativ = "', '".join(sorted(giltiga))
            raise _fel(
                f"Regnparametern '{nyckel}' måste vara '{alternativ}', fick {raa_s!r}.",
                400,
            )
        ut[nyckel] = raa_s
    if not ut:
        return None
    if "regn_mm" in ut and ("aterkomsttid_ar" in ut or "klimatfaktor" in ut):
        raise _fel(
            "Ange antingen 'regn_mm' eller IDF-parametrar ('aterkomsttid_ar'/'klimatfaktor'), inte båda.",
            400,
        )
    if ut.get("regntyp") == "cds" and "regn_mm" in ut:
        raise _fel(
            "CDS-regn kräver återkomsttid (IDF) och kan inte kombineras med direkt 'regn_mm'.",
            400,
        )
    if ut.get("regntyp") == "cds" and "varaktighet_min" in ut:
        var_min = float(ut["varaktighet_min"])
        if var_min < 5.0 or abs(var_min / 5.0 - round(var_min / 5.0)) > 1e-9:
            raise _fel(
                f"CDS-regn kräver att 'varaktighet_min' är en jämn multipel av 5 min (>= 5), fick {var_min:g}.",
                400,
            )
    return ut


def _tillampa_regn(
    losare: str,
    parametrar: dict[str, Any],
    regn: Mapping[str, Any] | None,
    *,
    avtappning_mm_h: float | None = None,
) -> None:
    """Skriver in validerade regnparametrar på en skyfallslösare (`vatten.statisk_fyllning`,
    `vatten.dynamik` eller `vatten.2d`).

    Vid `regn is None` ändras ingenting (100 % oförändrat beteende).
    """
    if regn is None or losare not in _LOSARE_MED_REGN:
        return
    if "regn_mm" in regn:
        for nyckel in _IDF_NYCKLAR_ATT_RENSA:
            parametrar.pop(nyckel, None)
        if losare == "vatten.dynamik":
            raw_var = regn.get("varaktighet_min")
            if raw_var is not None:
                var_min = float(raw_var)
            else:
                mall_var = parametrar.get("varaktighet_min")
                var_min = (
                    float(mall_var)
                    if isinstance(mall_var, int | float) and not isinstance(mall_var, bool) and float(mall_var) > 0.0
                    else 60.0
                )
            parametrar.pop("varaktighet_min", None)
            parametrar.pop("regn_mm", None)
            d_s = var_min * 60.0
            intens = float(regn["regn_mm"]) / (var_min / 60.0)
            parametrar["regn_tid_s"] = [0.0, d_s]
            parametrar["regn_mm_h"] = [intens, intens]
            parametrar["regntyp"] = "block"
            return
        if losare == "vatten.2d":
            parametrar.pop("regn_tid_s", None)
            parametrar.pop("regn_mm_h", None)
            parametrar["regn_mm"] = float(regn["regn_mm"])
            if "varaktighet_min" in regn:
                parametrar["varaktighet_min"] = float(regn["varaktighet_min"])
            else:
                parametrar.pop("varaktighet_min", None)
            return
        parametrar["regn_mm"] = float(regn["regn_mm"])
        if "varaktighet_min" in regn:
            parametrar["varaktighet_min"] = float(regn["varaktighet_min"])
        else:
            avt_par = parametrar.get("avtappning_mm_h")
            har_avt = (avtappning_mm_h is not None and avtappning_mm_h > 0.0) or (
                isinstance(avt_par, int | float) and not isinstance(avt_par, bool) and float(avt_par) > 0.0
            )
            if not har_avt:
                parametrar.pop("varaktighet_min", None)
        return

    # IDF-parametrar (aterkomsttid_ar, varaktighet_min, klimatfaktor, regntyp).
    if "idf" in parametrar:
        parametrar.pop("regn_mm", None)
        parametrar.pop("regn_tid_s", None)
        parametrar.pop("regn_mm_h", None)
        parametrar.pop("idf_klimatfaktor", None)
        for nyckel in ("aterkomsttid_ar", "varaktighet_min", "klimatfaktor"):
            if nyckel in regn:
                parametrar[nyckel] = float(regn[nyckel])
        if "regntyp" in regn:
            parametrar["regntyp"] = str(regn["regntyp"])
        if parametrar.get("regntyp") == "cds":
            eff_var = float(parametrar.get("varaktighet_min", 60.0))
            if eff_var < 5.0 or abs(eff_var / 5.0 - round(eff_var / 5.0)) > 1e-9:
                raise _fel(
                    f"CDS-regn kräver att 'varaktighet_min' är en jämn multipel av 5 min (>= 5), fick {eff_var:g}.",
                    400,
                )
        return

    # Mall utan registrerad IDF-källa (t.ex. kommunprojekt med enbart `regn_mm`).
    if "aterkomsttid_ar" not in regn and "klimatfaktor" not in regn:
        if losare == "vatten.dynamik" and "regn_tid_s" not in parametrar:
            if regn.get("regntyp") == "cds":
                raise _fel(
                    "CDS-regn kräver återkomsttid (IDF) när projektets skyfallsmall saknar IDF-källa.",
                    400,
                )
            if "regn_mm" in parametrar:
                mall_mm = float(parametrar.pop("regn_mm"))
                raw_var = regn.get("varaktighet_min", parametrar.pop("varaktighet_min", 60.0))
                var_min = float(raw_var) if raw_var is not None else 60.0
                d_s = var_min * 60.0
                intens = mall_mm / (var_min / 60.0)
                parametrar["regn_tid_s"] = [0.0, d_s]
                parametrar["regn_mm_h"] = [intens, intens]
                parametrar["regntyp"] = "block"
        elif losare == "vatten.2d":
            if regn.get("regntyp") == "cds":
                raise _fel(
                    "CDS-regn kräver återkomsttid (IDF) när projektets skyfallsmall saknar IDF-källa.",
                    400,
                )
            if "varaktighet_min" in regn and "regn_mm" in parametrar:
                parametrar["varaktighet_min"] = float(regn["varaktighet_min"])
        elif "varaktighet_min" in regn and losare == "vatten.statisk_fyllning":
            parametrar["varaktighet_min"] = float(regn["varaktighet_min"])
        return
    if "aterkomsttid_ar" not in regn:
        raise _fel(
            "Projektets skyfallsmall saknar IDF-källa; ange 'aterkomsttid_ar' eller direkt 'regn_mm'.",
            400,
        )
    from gemensamt.regn import RegnFel, generera_blockregn, generera_cds_regn

    var_min = float(regn.get("varaktighet_min", 60.0))
    kf = float(regn.get("klimatfaktor", 1.0))
    regntyp = str(regn.get("regntyp", "block"))
    try:
        if losare in ("vatten.dynamik", "vatten.2d") and regntyp == "cds":
            serie = generera_cds_regn(
                float(regn["aterkomsttid_ar"]),
                klimatfaktor=kf,
                varaktighet_min=var_min,
            )
        else:
            serie = generera_blockregn(
                float(regn["aterkomsttid_ar"]),
                klimatfaktor=kf,
                varaktighet_min=var_min,
            )
    except RegnFel as exc:
        raise _fel(f"Ogiltiga regnparametrar: {exc}", 400) from exc
    if losare == "vatten.dynamik":
        parametrar.pop("regn_mm", None)
        parametrar.pop("varaktighet_min", None)
        parametrar["regn_tid_s"] = list(serie.tid_s)
        parametrar["regn_mm_h"] = list(serie.intensitet_mm_h)
        parametrar["regntyp"] = regntyp
    elif losare == "vatten.2d":
        parametrar.pop("regn_mm", None)
        parametrar.pop("varaktighet_min", None)
        for nyckel in _IDF_NYCKLAR_ATT_RENSA:
            parametrar.pop(nyckel, None)
        parametrar["regn_tid_s"] = list(serie.tid_s)
        parametrar["regn_mm_h"] = list(serie.intensitet_mm_h)
    else:
        parametrar["regn_mm"] = round(float(serie.total_djup_mm), 3)
        parametrar["varaktighet_min"] = var_min


def _sol(v: Any) -> dict[str, Any] | None:
    """Validerar valfria dagsljusparametrar (`sol`) ur kartverktyget, eller None (mallens förval gäller).

    Strikt validering (HTTP 400):
    - Inga okända nycklar (tillåtna: `datum_iso`, `start_timme`, `slut_timme`, `sammanhangande`, `inkludera_vsc`)
    - `datum_iso`: giltigt `YYYY-MM-DD` via `datetime.date.fromisoformat`
    - `start_timme`: ändligt tal i `[0, 23]`
    - `slut_timme`: ändligt tal i `[1, 24]`
    - Om båda ges: `start_timme < slut_timme`
    - `sammanhangande` och `inkludera_vsc`: `bool` eller `None`
    """
    if v is None:
        return None
    if not isinstance(v, Mapping):
        raise _fel("Solinställningen måste vara ett JSON-objekt.", 400)
    okanda = sorted(str(k) for k in v if k not in _SOL_NYCKLAR)
    if okanda:
        raise _fel(f"Okända solparametrar: {', '.join(okanda)}.", 400)
    ut: dict[str, Any] = {}
    raa_datum = v.get("datum_iso")
    if raa_datum is not None:
        if not isinstance(raa_datum, str) or not _DATUM_ISO_RE.match(raa_datum):
            raise _fel(
                f"Solparametern 'datum_iso' måste ha formatet ÅÅÅÅ-MM-DD, fick {raa_datum!r}.",
                400,
            )
        try:
            date.fromisoformat(raa_datum)
        except ValueError as exc:
            raise _fel(f"Ogiltigt datum i 'datum_iso': {raa_datum!r}.", 400) from exc
        ut["datum_iso"] = raa_datum
    for nyckel, lo, hi in (("start_timme", 0.0, 23.0), ("slut_timme", 1.0, 24.0)):
        raa = v.get(nyckel)
        if raa is None:
            continue
        if isinstance(raa, bool) or not isinstance(raa, int | float) or not math.isfinite(float(raa)):
            raise _fel(f"Solparametern '{nyckel}' måste vara ett ändligt tal, fick {raa!r}.", 400)
        val = float(raa)
        if val < lo or val > hi:
            raise _fel(
                f"Solparametern '{nyckel}' måste ligga i [{lo:g}, {hi:g}], fick {val:g}.",
                400,
            )
        ut[nyckel] = val
    if "start_timme" in ut and "slut_timme" in ut and ut["start_timme"] >= ut["slut_timme"]:
        raise _fel(
            f"Solparametern 'start_timme' ({ut['start_timme']:g}) måste vara mindre än 'slut_timme' ({ut['slut_timme']:g}).",
            400,
        )
    for nyckel in ("sammanhangande", "inkludera_vsc"):
        raa_b = v.get(nyckel)
        if raa_b is None:
            continue
        if not isinstance(raa_b, bool):
            raise _fel(f"Solparametern '{nyckel}' måste vara sant eller falskt, fick {raa_b!r}.", 400)
        ut[nyckel] = raa_b
    raa_kl = v.get("klockslag_utc")
    if raa_kl is not None:
        if isinstance(raa_kl, bool) or not isinstance(raa_kl, int | float) or not math.isfinite(float(raa_kl)):
            raise _fel(
                f"Solparametern 'klockslag_utc' måste vara ett ändligt tal, fick {raa_kl!r}.",
                400,
            )
        val_kl = float(raa_kl)
        if val_kl < 0.0 or val_kl > 24.0:
            raise _fel(
                f"Solparametern 'klockslag_utc' måste ligga i [0, 24], fick {val_kl:g}.",
                400,
            )
        ut["klockslag_utc"] = val_kl
    if "klockslag_utc" in ut and ut.get("sammanhangande") is True:
        raise _fel(
            "Solparametern 'klockslag_utc' (momentan skuggmask) kan inte kombineras med 'sammanhangande=true'.",
            400,
        )
    return ut or None


def _tillampa_sol(
    losare: str,
    parametrar: dict[str, Any],
    sol: Mapping[str, Any] | None,
) -> None:
    """Skriver in validerade dagsljusparametrar på dagsljuslösare (`soltimmar_mark`,
    `soltimmar_sammanhangande` eller `skuggmask_vid_klockslag`)."""
    if sol is None or losare not in _LOSARE_MED_SOL:
        return
    if losare == "dagsljus.skuggmask_vid_klockslag":
        for nyckel in ("steg_min", "start_timme", "slut_timme"):
            parametrar.pop(nyckel, None)
        if "datum_iso" in sol:
            parametrar["datum"] = str(sol["datum_iso"])
        elif isinstance(parametrar.get("datum"), list | tuple):
            mall_datum = parametrar["datum"]
            if mall_datum:
                parametrar["datum"] = str(mall_datum[0])
            else:
                parametrar.pop("datum", None)
        if "klockslag_utc" in sol:
            kl = float(sol["klockslag_utc"])
            parametrar["klockslag"] = [0.0 if kl == 24.0 else kl]
            parametrar["utc_offset_h"] = 0.0
        return
    if "datum_iso" in sol:
        parametrar["datum"] = [str(sol["datum_iso"])]
    if "start_timme" in sol:
        parametrar["start_timme"] = float(sol["start_timme"])
    if "slut_timme" in sol:
        parametrar["slut_timme"] = float(sol["slut_timme"])
    start_eff = float(parametrar.get("start_timme", 0.0))
    slut_eff = float(parametrar.get("slut_timme", 24.0))
    if not (0.0 <= start_eff < slut_eff <= 24.0):
        raise _fel(
            f"Soltidsfönstret måste uppfylla 0 <= start_timme < slut_timme <= 24, fick [{start_eff:g}, {slut_eff:g}].",
            400,
        )


def slug(namn: str) -> str:
    """Filnamn och scenario-id ur ett namn (a-z, 0-9, bindestreck). Åäö blir aao."""
    s = namn.strip().lower()
    for a, b in (("å", "a"), ("ä", "a"), ("ö", "o"), ("é", "e")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9_\-]+", "-", s).strip("-_")
    return s[:60]


def _fel(text: str, status: int = 422) -> FlodenWebbFel:
    return FlodenWebbFel(text, status_kod=status)


def _rektangel(r: Ruta) -> tuple[float, float, float, float]:
    return (r.x_min, r.y_min, r.x_max, r.y_max)



_EKVIDISTANSER_M = (0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0)


def _hojdkurvor_rgba(glob: np.ndarray[Any, Any], cell: float) -> tuple[np.ndarray[Any, Any], float]:
    """Höjdkurvor ur markhöjderna som transparent RGBA (norr uppåt) och ekvidistansen (m).

    Ekvidistansen väljs ur höjdspannet (högst ca 25 kurvor). Matrisen interpoleras linjärt till ungefär
    en halv meter per pixel (högst 4096 px) så att kurvorna blir tunna linjer och inte rasterceller;
    kurvorna är ca 1,5 m breda och var femte (indexkurva) kraftigare. Visning av indata, inget resultat."""
    from scipy import ndimage

    ok = np.isfinite(glob)
    if not ok.any():
        return np.zeros((*glob.shape, 4), np.uint8), _EKVIDISTANSER_M[0]
    lag, hog = float(np.nanmin(glob)), float(np.nanmax(glob))
    spann = max(hog - lag, 1e-6)
    ekv = next((e for e in _EKVIDISTANSER_M if spann / e <= 25.0), _EKVIDISTANSER_M[-1])
    faktor = int(np.clip(round(cell / 0.5), 1, max(1, 4096 // max(glob.shape))))
    fylld = np.where(ok, glob, lag)
    if faktor > 1:
        z = ndimage.zoom(fylld, faktor, order=1, mode="nearest", grid_mode=True)
        giltig = ndimage.zoom(ok.astype(np.float32), faktor, order=0, mode="nearest", grid_mode=True) > 0.5
    else:
        z, giltig = fylld, ok
    nivaer = np.floor((z - lag) / ekv).astype(np.int32)
    kant = np.zeros(z.shape, dtype=bool)
    kant[:, :-1] |= nivaer[:, :-1] != nivaer[:, 1:]
    kant[:-1, :] |= nivaer[:-1, :] != nivaer[1:, :]
    kant &= giltig
    # Indexkurvan: kurvan vid en nivå som är jämnt delbar med 5 (nivån på den högre sidan), ritad en pixel bredare.
    hogre = np.maximum(nivaer, np.pad(nivaer[:, 1:], ((0, 0), (0, 1)), constant_values=-1))
    hogre = np.maximum(hogre, np.pad(nivaer[1:, :], ((0, 1), (0, 0)), constant_values=-1))
    index = kant & (hogre % 5 == 0)
    index = ndimage.binary_dilation(index, iterations=2) & giltig
    kant = ndimage.binary_dilation(kant, iterations=1) & giltig
    rgba = np.zeros((*z.shape, 4), np.uint8)
    rgba[kant] = (138, 90, 43, 190)
    rgba[index] = (110, 66, 24, 255)
    return rgba, ekv


def _markhojd_rgba(glob: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
    """Markhöjden i färg (grönt lågt, gult, brunt högt; 2:a till 98:e percentilen) som halvtransparent RGBA."""
    ok = np.isfinite(glob)
    rgba = np.zeros((*glob.shape, 4), np.uint8)
    if not ok.any():
        return rgba
    lag, hog = (float(x) for x in np.nanpercentile(glob, (2, 98)))
    t = np.clip((np.where(ok, glob, lag) - lag) / max(hog - lag, 1e-6), 0.0, 1.0)
    stopp = np.array([[0.0, 120, 170, 110], [0.5, 235, 225, 140], [1.0, 150, 105, 70]])
    for k in range(3):
        rgba[..., k] = np.interp(t, stopp[:, 0], stopp[:, k + 1]).astype(np.uint8)
    rgba[..., 3] = np.where(ok, 200, 0).astype(np.uint8)
    return rgba


class KartverktygTjanst:
    """Ritade planförslag: kontroll, K9/K20, sparade scenarier och underlag."""

    def __init__(
        self,
        fragor: FragaTjanst,
        mapp: Path,
        *,
        datamappar: Sequence[Path] = (),
        tillat_loopback: bool = False,
        geomapp: Path | None = None,
        projektrot: Path | None = None,
        kraver_registerid: bool = False,
        forberaknad_lager: ForberaknadKommunLager | None = None,
        max_2d_celler: int = STANDARD_MAX_2D_CELLER,
    ) -> None:
        if isinstance(max_2d_celler, bool) or not isinstance(max_2d_celler, int) or max_2d_celler < 1:
            raise ValueError(f"max_2d_celler måste vara ett heltal >= 1, fick {max_2d_celler!r}.")
        self.fragor = fragor
        self.geomapp = geomapp
        self.projektrot = projektrot if projektrot is not None else fragor.projektrot
        self.kraver_registerid = kraver_registerid
        """Med inloggning på nås projekt i en geografi bara med registrets id (rollerna hänger på det)."""
        self.forberaknad_lager = forberaknad_lager
        self.max_2d_celler = max_2d_celler
        self.mapp = mapp
        self.datamappar = list(datamappar)
        self.tillat_loopback = tillat_loopback
        self.fragedir = mapp / "fragor"
        if self.fragedir not in fragor.fragemappar:
            fragor.fragemappar.append(self.fragedir)
        self._mallar: dict[str, Mall] | None = None
        self._byggnader: dict[str, list[dict[str, Any]]] = {}
        self._kallor: dict[str, Projektkalla] = {}
        self._caps: dict[str, dict[str, Any]] = {}
        self._terrang: dict[str, tuple[Any, list[tuple[np.ndarray[Any, Any], Any]]]] = {}

    def _kontrollera_2d_grans(self, mall: Mall) -> None:
        """Stoppar för stora 2D-körningar innan K20 skrivs (`ValueError` -> HTTP 400)."""
        antal = _berakna_2d_celler(mall)
        try:
            kontrollera_2d_cellgrans(antal, self.max_2d_celler)
        except ValueError as exc:
            raise _fel(str(exc), 400) from exc

    def glom_projekt(self, projekt_id: str) -> None:
        """Glömmer det som cachats i minnet för ett projekt som raderats."""
        for cache in (self._kallor, self._byggnader, self._caps, self._terrang):
            cache.pop(projekt_id, None)

    # ------------------------------------------------------------------ mallar och bas

    def _demo_refs(self) -> list[tuple[str, str]]:
        d = self.fragor.demo()
        if not d.get("finns"):
            return []
        delar = d.get("delar", {})
        ut = []
        for ref, kort in (
            ("demo_dagsljus", "dagsljus"),
            ("demo_vatten", "vatten"),
            ("demo_ekologi", "ekologi"),
        ):
            if isinstance(delar.get(ref), dict) and "jobb" in delar[ref]:
                ut.append((ref, kort))
        return ut

    def mallar(self) -> dict[str, Mall]:
        if self._mallar is not None:
            return self._mallar
        ut: dict[str, Mall] = {}
        for ref, kort in self._demo_refs():
            try:
                fil = self.fragor._fil_for(ref)  # noqa: SLF001 - samma paket
                fraga = las_fraga(fil)
            except Exception:  # noqa: BLE001 - en trasig mall hindrar inte de andra
                continue
            fore = next(
                (s for s in fraga.scenarier.values() if not s.k9 and not s.lappar), None
            )
            if fore is None:
                continue
            try:
                rutor = las_bas(fore.bas)
            except ScenarioFel:
                continue
            ut[kort] = Mall(ref, kort, fraga, fore.bas, rutor, fore.id)
        if ut:
            self._mallar = ut
        return ut

    def projektmapp(self, projekt: str) -> Path | None:
        """Mappen för ett projekt i en geografi (`None` för demokvarteret och okända id)."""
        if projekt == _PROJEKT_ID:
            return None
        return hitta_projektmapp(
            self.projektrot, projekt, tillat_projektid=not self.kraver_registerid
        )

    def kalla(self, projekt: str) -> Projektkalla:
        """Projektkällan: demokvarteret (ur demokörningarna) eller ett projekt i en geografi (ur dess egna
        frågor, klippt till projektets rutor). Okänt projekt ger 404."""
        if projekt == _PROJEKT_ID:
            m = self.mallar()
            if not m:
                raise _fel("Demokvarteret är inte byggt. Kör `floden demo` och ladda om sidan.", 404)
            return Projektkalla(
                id=projekt, slag="demo", namn="Demokvarteret", paahittat=True, oppen=True,
                licens=None, mallar=m,
            )
        mapp = self.projektmapp(projekt)
        if mapp is None:
            raise _fel(f"Okänt projekt '{projekt}'.", 404)
        stampel = stampel_for(mapp)
        k = self._kallor.get(projekt)
        if k is None or k.stampel != stampel or k.mapp != mapp:
            k = bygg_geografikalla(projekt, mapp, geomapp=self.geomapp, fragor=self.fragor)
            self._kallor[projekt] = k
            self._byggnader.pop(projekt, None)
        return k

    def _kravs_projekt(self, projekt: str) -> dict[str, Mall]:
        return dict(self.kalla(projekt).mallar)

    @staticmethod
    def _bas_recept(mall: Mall) -> tuple[str, ...]:
        return tuple(
            sha256_fil(r.mapp / "recept.json")
            for r in mall.rutor
            if (r.mapp / "recept.json").is_file()
        )

    def _rutor_alla(self, mallar: Mapping[str, Mall]) -> list[tuple[float, float, float, float]]:
        return [_rektangel(r) for m in mallar.values() for r in m.rutor]

    def _fokus(self, mallar: Mapping[str, Mall]) -> list[float] | None:
        ordning = [mallar["dagsljus"]] if "dagsljus" in mallar else []
        ordning.extend(m for k, m in mallar.items() if k != "dagsljus")
        for m in ordning:
            for a in m.fraga.analyser:
                if a.fokus is not None:
                    return list(a.fokus)
        return None

    def _fallback_vatten_fokus(self, kalla: Projektkalla) -> list[float] | None:
        """Automatiskt skyfallsfokus för redan skapade geografiprojekt vars `skyfall.toml` saknar `fokus`.

        Läser `vatten_ruta_3011` eller `avgransning_3011` ur `projekt.json`. Om `vatten_ruta_3011`
        sparades som hela geografins `gransen_3011` (före ruturvalsfixen) härleds i stället
        `avgransning_3011` ± tillrinningsmarginal klippt till geografin så att befintliga projekt
        på servern bara laddar berörda rutor.
        """
        if kalla.slag != "geografi" or kalla.mapp is None:
            return None
        projekt: dict[str, Any] = {}
        fil = kalla.mapp / "projekt.json"
        if fil.is_file():
            try:
                raw = json.loads(fil.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    projekt = raw
            except (OSError, ValueError):
                projekt = {}

        def _box4(v: Any) -> tuple[float, float, float, float] | None:
            if not isinstance(v, list | tuple) or len(v) != 4:
                return None
            try:
                x0, y0, x1, y1 = (float(c) for c in v)
            except (TypeError, ValueError):
                return None
            if not all(math.isfinite(c) for c in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
                return None
            return (x0, y0, x1, y1)

        avgr = _box4(projekt.get("avgransning_3011")) or kalla.avgransning
        v_ruta = _box4(projekt.get("vatten_ruta_3011"))
        gransen: tuple[float, float, float, float] | None = None
        if self.geomapp is not None and kalla.geografi:
            try:
                manifest, _ = las_geo_manifest(self.geomapp, kalla.geografi, verifiera=False)
                gransen = _box4(manifest.get("gransen_3011"))
            except GeografiFel:
                gransen = None

        if v_ruta is not None:
            ar_hela_geo = gransen is not None and all(
                abs(a - b) <= 1e-3 for a, b in zip(v_ruta, gransen, strict=True)
            )
            if not ar_hela_geo:
                return list(v_ruta)

        if avgr is None:
            return list(v_ruta) if v_ruta is not None else None

        m = _STANDARD_SKYFALL_FOKUS_MARGINAL_M
        if gransen is not None:
            x0 = max(gransen[0], avgr[0] - m)
            y0 = max(gransen[1], avgr[1] - m)
            x1 = min(gransen[2], avgr[2] + m)
            y1 = min(gransen[3], avgr[3] + m)
            if x1 > x0 and y1 > y0:
                return [x0, y0, x1, y1]
        return [avgr[0] - m, avgr[1] - m, avgr[2] + m, avgr[3] + m]

    # ------------------------------------------------------------------ projektinfo

    def _alla_byggnader(self, kalla: Projektkalla) -> list[dict[str, Any]]:
        if kalla.id in self._byggnader:
            return self._byggnader[kalla.id]
        mallar = kalla.mallar
        form = kalla.projektform()
        sedda: dict[str, dict[str, Any]] = {}
        for kort, m in mallar.items():
            for r in m.rutor:
                for b in las_byggnader(r.mapp):
                    if b.byggnad_id in sedda:
                        sedda[b.byggnad_id]["baser"].append(kort)
                        continue
                    sedda[b.byggnad_id] = {
                        "id": b.byggnad_id,
                        "ring": [[float(x), float(y)] for x, y in b.polygon],
                        "hal": [[[float(x), float(y)] for x, y in h] for h in b.hal],
                        "hojd_m": round(float(b.hojd_m), 2),
                        "baser": [kort],
                    }
        for post in sedda.values():
            post["baser"] = sorted(set(post["baser"]))
        lista = sorted(sedda.values(), key=lambda p: str(p["id"]))
        if form is not None:
            # Byggnader i rutorna men utanför avgränsningen är sammanhang: de ritas, men räknas inte till projektet.
            for post in lista:
                post["i_projektet"] = bool(
                    Polygon(post["ring"], post["hal"]).intersects(form)
                )
        self._byggnader[kalla.id] = lista
        return lista

    def _alla_brytlinjer(self, kalla: Projektkalla) -> list[dict[str, Any]]:
        """Basens brytlinjer (K1 `brytlinjer.gpkg`) som fästmål och för att visa vad som finns (R3).

        En linje som ligger i flera rutor (överlapp) tas en gång. Sorterad, så att svaret är stabilt."""
        from sankgraf.typer import las_brytlinjer_fran_gpkg

        sedda: set[tuple[Any, ...]] = set()
        lista: list[dict[str, Any]] = []
        for m in kalla.mallar.values():
            for r in m.rutor:
                fil = r.mapp / "brytlinjer.gpkg"
                if not fil.is_file():
                    continue
                try:
                    poster = las_brytlinjer_fran_gpkg(fil)
                except Exception:  # noqa: BLE001 - en trasig fil hindrar inte kartan
                    continue
                for p in poster:
                    kord = tuple((round(float(x), 2), round(float(y), 2)) for x, y in p.koordinater)
                    nyckel = (p.typ, round(float(p.hojd_m), 3), kord)
                    if nyckel in sedda:
                        continue
                    sedda.add(nyckel)
                    lista.append({"typ": p.typ, "hojd_m": round(float(p.hojd_m), 3), "koordinater": [list(k) for k in kord]})
        lista.sort(key=lambda d: (d["typ"], d["hojd_m"], d["koordinater"]))
        return lista

    def _analyser(self, mallar: Mapping[str, Mall]) -> list[dict[str, Any]]:
        ut: list[dict[str, Any]] = []
        for kort, m in mallar.items():
            for a in m.fraga.analyser:
                ut.append(
                    {
                        "id": f"{kort}.{a.id}",
                        "namn": visningsnamn(a.losare),
                        "mall": kort,
                        "analys": a.id,
                        "forval": a.id != "vsc",
                    }
                )
        return ut

    @staticmethod
    def _regn_forval(mallar: Mapping[str, Mall]) -> dict[str, Any] | None:
        """Projektets förvalda regnparametrar ur skyfallsmallen, eller `None` om projektet saknar skyfallsmall."""
        mall = mallar.get("vatten")
        if mall is None:
            return None
        analys = next((a for a in mall.fraga.analyser if a.losare in _LOSARE_MED_REGN), None)
        if analys is None:
            return None
        p = analys.parametrar

        def _tal_eller_none(nyckel: str) -> float | None:
            v = p.get(nyckel)
            if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
                return None
            return float(v)

        idf_id = p.get("idf") if isinstance(p.get("idf"), str) else None
        aterkomsttid = _tal_eller_none("aterkomsttid_ar")
        varaktighet = _tal_eller_none("varaktighet_min")
        klimatfaktor = _tal_eller_none("klimatfaktor")
        if klimatfaktor is None:
            klimatfaktor = _tal_eller_none("idf_klimatfaktor")
        regn_mm = _tal_eller_none("regn_mm")
        kalla: str | None = idf_id or (p.get("idf_kalla") if isinstance(p.get("idf_kalla"), str) else None)

        if idf_id is not None and aterkomsttid is not None:
            from gemensamt.regn import RegnFel, hamta_idf_kalla, skapa_regn_fran_idf_kalla

            try:
                idf_kalla = hamta_idf_kalla(idf_id)
                eff_kf = klimatfaktor if klimatfaktor is not None else float(idf_kalla.standard_klimatfaktor)
                eff_var = varaktighet if varaktighet is not None else 60.0
                typ = str(p.get("regntyp", "block"))
                serie = skapa_regn_fran_idf_kalla(
                    idf_id,
                    aterkomsttid,
                    klimatfaktor=eff_kf,
                    typ=typ,
                    varaktighet_min=eff_var,
                )
                klimatfaktor = eff_kf
                varaktighet = eff_var
                regn_mm = float(serie.total_djup_mm)
            except RegnFel:
                pass

        return {
            "aterkomsttid_ar": aterkomsttid,
            "varaktighet_min": varaktighet,
            "klimatfaktor": klimatfaktor,
            "regn_mm": regn_mm,
            "kalla": kalla,
        }

    def _forberaknad_bas(
        self,
        kalla: Projektkalla,
        utbredning: tuple[float, float, float, float],
    ) -> dict[str, Any] | None:
        """Returnerar metadata om förberäknade rutor i `ForberaknadKommunLager` för projektet, annars None."""
        lager = self.forberaknad_lager
        if lager is None or not kalla.geografi:
            return None
        kommun: str | None = None
        if self.geomapp is not None:
            try:
                manifest, _ = las_geo_manifest(self.geomapp, kalla.geografi, verifiera=False)
                raw_kommun = manifest.get("kommun")
                if isinstance(raw_kommun, str) and raw_kommun.strip():
                    kommun = raw_kommun.strip()
            except GeografiFel:
                pass
        if kommun is None:
            kommun = kalla.geografi.split("@", 1)[0].strip()
        if not kommun:
            return None
        bbox = kalla.avgransning if kalla.avgransning is not None else utbredning
        try:
            rutor = lager.hitta_rutor_for_utbredning(kommun, bbox)
        except (ForberaknadLagerFel, ObjektLagringFel) as exc:
            logging.getLogger(__name__).warning(
                "Kunde inte läsa förberäknade rutor för kommun %s: %s", kommun, exc
            )
            return None
        if not rutor:
            return None
        return {
            "kalla": lager.kalla_typ,
            "kommun": kommun,
            "antal_rutor": len(rutor),
        }

    @staticmethod
    def _las_max_sankgraf_celler() -> int:
        """Läser aktuellt sänkgraf-celltak ur `FLODEN_MAX_SANKGRAF_CELLER` utan att krascha status/UI."""
        from karna.analyslosare import LosarFel
        from karna.inbyggda import STANDARD_MAX_SANKGRAF_CELLER, max_sankgraf_celler

        try:
            return int(max_sankgraf_celler())
        except LosarFel as exc:
            logging.getLogger(__name__).warning(
                "Ogiltigt värde i FLODEN_MAX_SANKGRAF_CELLER (%s); faller tillbaka på standard %d.",
                exc,
                STANDARD_MAX_SANKGRAF_CELLER,
            )
            return int(STANDARD_MAX_SANKGRAF_CELLER)

    @staticmethod
    def _formatera_celler_sv(celler: int, max_celler: int) -> tuple[str, str, str]:
        if max_celler >= 100_000:
            c_txt = f"{celler / 1_000_000:.1f}".replace(".", ",")
            m_txt = f"{max_celler / 1_000_000:.1f}".replace(".", ",")
            return c_txt, m_txt, "M celler"
        return (
            f"{celler:,}".replace(",", " "),
            f"{max_celler:,}".replace(",", " "),
            "celler",
        )

    def _berakningsomfang(self, kalla: Projektkalla) -> dict[str, Any] | None:
        """O(1)-uppskattning av antal berörda rutor och rasterceller för skyfall utan pixelinläsning.

        Använder enbart geografimanifestets metadata (`parametrar.upplosning_m`, `ruta_storlek_m`,
        `lager.k1_rutnat.rutor`), projektets skyfallsfokus/rutlista och `FLODEN_MAX_SANKGRAF_CELLER`.
        Returnerar `None` för projekt utan geografi (t.ex. `demokvarteret`) eller utan läsbart manifest.
        """
        if kalla.slag != "geografi" or not kalla.geografi or self.geomapp is None:
            return None
        try:
            manifest, _ = las_geo_manifest(self.geomapp, kalla.geografi, verifiera=False)
        except GeografiFel:
            return None

        par = manifest.get("parametrar")
        par_m: Mapping[str, Any] = par if isinstance(par, Mapping) else {}
        upplosning_m = self._tal_eller_none(par_m.get("upplosning_m"))
        if upplosning_m is None or upplosning_m <= 0.0:
            upplosning_m = self._tal_eller_none(manifest.get("upplosning_m"))
        if upplosning_m is None or upplosning_m <= 0.0:
            upplosning_m = 1.0

        ruta_storlek_m = self._tal_eller_none(par_m.get("ruta_storlek_m"))
        if ruta_storlek_m is None or ruta_storlek_m <= 0.0:
            ruta_storlek_m = STANDARD_RUTSTORLEK_M

        overlapp_m = self._tal_eller_none(par_m.get("overlapp_m"))
        if overlapp_m is None or overlapp_m < 0.0:
            overlapp_m = _STANDARD_RUT_OVERLAPP_M

        # Hämta skyfallsfokus i samma ordning som `_k20`/`_lage_k20` (utan rasterläsning).
        fokus: tuple[float, float, float, float] | None = None
        vatten_mall = kalla.mallar.get("vatten")
        if vatten_mall is not None:
            for a in vatten_mall.fraga.analyser:
                if a.losare in _LOSARE_MED_REGN and a.fokus is not None and len(a.fokus) == 4:
                    fx0, fy0, fx1, fy1 = (float(c) for c in a.fokus)
                    if all(math.isfinite(c) for c in (fx0, fy0, fx1, fy1)) and fx1 > fx0 and fy1 > fy0:
                        fokus = (fx0, fy0, fx1, fy1)
                        break
        if fokus is None:
            fb = self._fallback_vatten_fokus(kalla)
            if fb is not None and len(fb) == 4:
                fokus = (float(fb[0]), float(fb[1]), float(fb[2]), float(fb[3]))
        if fokus is None and kalla.avgransning is not None:
            fokus = kalla.avgransning

        # 1. Primärt: rutor ur geografimanifestets `lager.k1_rutnat.rutor` (O(1) på rut-ID).
        lager_raw = manifest.get("lager")
        k1_raw = lager_raw.get("k1_rutnat") if isinstance(lager_raw, Mapping) else None
        rut_ids_raw = k1_raw.get("rutor") if isinstance(k1_raw, Mapping) else None
        rut_bboxes: list[tuple[float, float, float, float]] = []
        if isinstance(rut_ids_raw, list):
            for rid in rut_ids_raw:
                if not isinstance(rid, str):
                    continue
                bbox = tolka_rut_bbox_3011(rid, rutstorlek_m=ruta_storlek_m)
                if bbox is not None:
                    rut_bboxes.append(bbox)

        antal_rutor = 0
        uppskattade_celler = 0
        if rut_bboxes:
            if fokus is not None:
                fx0, fy0, fx1, fy1 = fokus
                valda = [
                    b
                    for b in rut_bboxes
                    if (b[0] - overlapp_m) < fx1
                    and (b[2] + overlapp_m) > fx0
                    and (b[1] - overlapp_m) < fy1
                    and (b[3] + overlapp_m) > fy0
                ]
                if not valda:
                    valda = rut_bboxes
            else:
                valda = rut_bboxes
            antal_rutor = len(valda)
            if antal_rutor == 1:
                sida_m = ruta_storlek_m + 2.0 * overlapp_m
                n_sida = max(1, round(sida_m / upplosning_m))
                uppskattade_celler = n_sida * n_sida
            else:
                x_min = min(b[0] for b in valda)
                y_min = min(b[1] for b in valda)
                x_max = max(b[2] for b in valda)
                y_max = max(b[3] for b in valda)
                uppskattade_celler = max(1, round((x_max - x_min) / upplosning_m)) * max(
                    1, round((y_max - y_min) / upplosning_m)
                )
        else:
            # 2. Reserv: projektmallens redan inlästa `Ruta`-utbredningar (inga rasterpixlar läses).
            mall_kandidat = vatten_mall or next(iter(kalla.mallar.values()), None)
            if mall_kandidat is not None and mall_kandidat.rutor:
                rutor_m = mall_kandidat.rutor
                antal_rutor = len(rutor_m)
                if antal_rutor == 1:
                    r0 = rutor_m[0]
                    uppskattade_celler = max(
                        1, round((float(r0.x_max) - float(r0.x_min)) / upplosning_m)
                    ) * max(1, round((float(r0.y_max) - float(r0.y_min)) / upplosning_m))
                else:
                    x_min = min(float(r.x_min) for r in rutor_m)
                    y_min = min(float(r.y_min) for r in rutor_m)
                    x_max = max(float(r.x_max) for r in rutor_m)
                    y_max = max(float(r.y_max) for r in rutor_m)
                    b_m = max(upplosning_m, (x_max - x_min) - 2.0 * overlapp_m)
                    h_m = max(upplosning_m, (y_max - y_min) - 2.0 * overlapp_m)
                    uppskattade_celler = max(1, round(b_m / upplosning_m)) * max(
                        1, round(h_m / upplosning_m)
                    )
            else:
                box_reserv = fokus
                if box_reserv is None:
                    g_raw = manifest.get("gransen_3011")
                    if isinstance(g_raw, list) and len(g_raw) == 4:
                        vals = [self._tal_eller_none(c) for c in g_raw]
                        if all(v is not None for v in vals):
                            gx0, gy0, gx1, gy1 = (float(v) for v in vals if v is not None)
                            if gx1 > gx0 and gy1 > gy0:
                                box_reserv = (gx0, gy0, gx1, gy1)
                if box_reserv is None:
                    return None
                bx0, by0, bx1, by1 = box_reserv
                antal_rutor = max(
                    1,
                    math.ceil((bx1 - bx0) / ruta_storlek_m)
                    * math.ceil((by1 - by0) / ruta_storlek_m),
                )
                uppskattade_celler = max(1, round((bx1 - bx0) / upplosning_m)) * max(
                    1, round((by1 - by0) / upplosning_m)
                )

        max_celler = self._las_max_sankgraf_celler()
        andel = uppskattade_celler / max_celler
        if uppskattade_celler > max_celler:
            niva = "over_tak"
            c_txt, m_txt, enhet = self._formatera_celler_sv(uppskattade_celler, max_celler)
            meddelande: str | None = (
                f"Området överstiger serverns cellgräns ({c_txt} / {m_txt} {enhet}). "
                "Rita en mindre avgränsning eller kör ett ämne i taget."
            )
        elif andel >= BERAKNINGSOMFANG_VARNING_ANDEL:
            niva = "varning"
            c_txt, m_txt, enhet = self._formatera_celler_sv(uppskattade_celler, max_celler)
            meddelande = (
                f"Området är nära serverns cellgräns ({c_txt} / {m_txt} {enhet}). "
                "Rita en mindre avgränsning eller kör ett ämne i taget."
            )
        else:
            niva = "ok"
            meddelande = None

        return {
            "antal_rutor": int(antal_rutor),
            "uppskattade_celler": int(uppskattade_celler),
            "max_sankgraf_celler": int(max_celler),
            "andel_av_tak": round(float(andel), 4),
            "upplosning_m": float(upplosning_m),
            "niva": niva,
            "meddelande": meddelande,
        }

    def projektinfo(self, projekt: str) -> dict[str, Any]:
        kalla = self.kalla(projekt)
        mallar = dict(kalla.mallar)
        rutor = self._rutor_alla(mallar)
        utbr = (
            min(r[0] for r in rutor),
            min(r[1] for r in rutor),
            max(r[2] for r in rutor),
            max(r[3] for r in rutor),
        )
        byggn = self._alla_byggnader(kalla)
        trunkerad = len(byggn) > MAX_BYGGNADER
        brytl = self._alla_brytlinjer(kalla)
        return {
            "id": projekt,
            "paahittat": kalla.paahittat,
            # Additivt: vad för slags projekt, dess avgränsning och dataklass (K38; stängt = inte öppna data).
            "slag": kalla.slag,
            "namn": kalla.namn,
            "geografi": kalla.geografi,
            "forberaknad_bas": self._forberaknad_bas(kalla, utbr),
            "berakningsomfang": self._berakningsomfang(kalla),
            "avgransning_3011": list(kalla.avgransning) if kalla.avgransning else None,
            "avgransning_polygon_3011": (
                avgr_mod.till_geojson(kalla.polygon) if kalla.polygon is not None else None
            ),
            "oppen": kalla.oppen,
            "licens": kalla.licens,
            "varningar": list(kalla.varningar),
            "epsg": 3011,
            "hojd_max_m": HOJD_MAX_M,
            "vaningshojd_m": VANINGSHOJD_M,
            "utbredning": {
                "x_min": utbr[0],
                "y_min": utbr[1],
                "x_max": utbr[2],
                "y_max": utbr[3],
            },
            "baser": {
                k: {
                    "rutor": [
                        {"x_min": r.x_min, "y_min": r.y_min, "x_max": r.x_max, "y_max": r.y_max}
                        for r in m.rutor
                    ],
                    "analyser": [a.id for a in m.fraga.analyser],
                }
                for k, m in mallar.items()
            },
            "fokus": self._fokus(mallar),
            "byggnader": byggn[:MAX_BYGGNADER],
            "byggnader_trunkerade": trunkerad,
            # Additivt (R3): basens brytlinjer (fästmål och visning).
            "brytlinjer": brytl[:MAX_BRYTLINJER],
            "brytlinjer_trunkerade": len(brytl) > MAX_BRYTLINJER,
            "analyser": self._analyser(mallar),
            "regn_forval": self._regn_forval(mallar),
            "scenarier": self.lista_scenarier(projekt)["scenarier"],
            "ortofoto": self.ortofoto_info(projekt),
            # Additivt (R2, K50): förvalda ritlager och rollerna (vad ett objekt blir i beräkningen).
            "ritlager": {
                "kontrakt": ritl.KONTRAKT,
                "lager": ritl.standardlager(),
                "roller": ritl.roller_for_api(),
            },
        }

    # ------------------------------------------------------------------ kontroll

    def _normalisera(self, andringar: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        ut: list[dict[str, Any]] = []
        for a in andringar:
            if not isinstance(a, Mapping):
                raise _fel("En ändring har fel form.")
            d: dict[str, Any] = {
                "typ": a.get("typ"),
                "andring_id": str(a.get("andring_id", "")).strip(),
                "geometri": a.get("geometri"),
                "egenskaper": dict(a.get("egenskaper") or {}),
            }
            h = d["egenskaper"].get("hojd_m")
            if isinstance(h, int | float) and not isinstance(h, bool):
                d["egenskaper"]["hojd_m"] = float(h)  # 18 och 18.0 ska ge samma K9-nyckel
            g = d["geometri"]
            if (
                d["typ"] in ("byggnad_ny", "markhojd")
                and isinstance(g, Mapping)
                and g.get("type") == "Polygon"
                and g.get("coordinates")
            ):
                d["geometri"] = {
                    "type": "Polygon",
                    "coordinates": [normalisera_ring(g["coordinates"][0])],
                }
            elif (
                d["typ"] in ("brytlinje_ny", "brytlinje_bort", "oppning_ny")
                and isinstance(g, Mapping)
                and g.get("type") == "LineString"
                and isinstance(g.get("coordinates"), list)
            ):
                d["geometri"] = {"type": "LineString", "coordinates": normalisera_linje(g["coordinates"])}
            for k in ("hojd_m", "bredd_m", "botten_rh2000", "tolerans_m"):
                v = d["egenskaper"].get(k)
                if isinstance(v, int | float) and not isinstance(v, bool):
                    d["egenskaper"][k] = float(v)
            ut.append(d)
        return ut

    def validera(self, projekt: str, kropp: Mapping[str, Any]) -> dict[str, Any]:
        """Kontrollerar ritade ändringar.

        Svar: `giltig`, `fel`, `varningar`, `information`, `hus`, `andringar`. `information` är
        upplysningar som inte hindrar något (t.ex. att ett hus stänger in en gård).
        """
        mallar = self._kravs_projekt(projekt)
        lista = kropp.get("andringar")
        if not isinstance(lista, list):
            raise _fel("Anropet saknar 'andringar'.", 400)
        andringar = self._normalisera(lista)
        fel: list[str] = []
        varningar: list[str] = []
        information: list[str] = []
        hus: list[dict[str, Any]] = []
        skarvar: dict[str, Any] | None = None
        ritdok: dict[str, Any] | None = None
        try:
            if kropp.get("ritlager") is not None:
                # K50: rollen avgör vilken K9-ändring varje ritat objekt blir (karna.ritlager.oversatt).
                ritdok = ritl.kontrollera_dokument(kropp["ritlager"])
                k9_lista, _ = ritl.sla_ihop(andringar, ritdok)
                andringar = self._normalisera(k9_lista)
            res = kontrollera_andringar(andringar, self._rutor_alla(mallar))
            hus = res["hus"]
            varningar.extend(res["varningar"])
            # Kärnans egen kontroll av ändringarna (typ, id, dubbletter, byggnadsreferenser).
            k9_per_bas = self._k9_per_bas(mallar, andringar, "kontroll")
            varningar.extend(self._paverkar_inte(mallar, k9_per_bas))
            varningar.extend(self._utanfor_avgransning(self.kalla(projekt), andringar))
            information.extend(self._instangt_vatten(mallar, k9_per_bas))
            skarv_text, skarvar = self._skarvar(mallar, k9_per_bas)
            varningar.extend(skarv_text)
        except (RitatFel, ScenarioFel) as exc:
            fel.append(str(exc))
        return {
            "giltig": not fel,
            "fel": fel,
            "varningar": varningar,
            "information": information,
            "hus": hus,
            "andringar": andringar,
            "skarvar": skarvar,
            "ritlager": ritdok,
        }

    def skarvatgard(self, projekt: str, kropp: Mapping[str, Any]) -> dict[str, Any]:
        """Föreslår och verifierar en åtgärd för skarvkontrollens fynd (R5, `sankgraf.skarvatgard`).

        Tar samma kropp som `validera`. Förslaget (slänt, sedan utlopp) tillämpas på K9-ändringarna
        och skarvkontrollen körs om för varje förslag; svaret är `floden-skarvatgard/v1`. Inget
        sparas eller ändras: klienten tillämpar förslaget efter bekräftelse och kontrollerar igen.
        """
        from sankgraf.skarvatgard import foresla

        v = self.validera(projekt, kropp)
        if not v["giltig"]:
            raise _fel("Förslaget har fel som måste rättas först: " + " ".join(v["fel"]))
        mallar = self._kravs_projekt(projekt)

        def kor(lista: list[dict[str, Any]]) -> dict[str, Any] | None:
            try:
                k9 = self._k9_per_bas(mallar, self._normalisera(lista), "skarvatgard")
            except (RitatFel, ScenarioFel):
                return {"antal": 10**9, "tillkommande_volym_m3": float("inf"), "fynd": []}
            return self._skarvar(mallar, k9)[1]

        ut = foresla(v["andringar"], v["skarvar"], kor)
        ut["skarvar"] = v["skarvar"]
        return ut

    def _skarvar(
        self, mallar: Mapping[str, Mall], k9: Mapping[str, tuple[Planscenario, list[str]]]
    ) -> tuple[list[str], dict[str, Any] | None]:
        """Skarvkontrollen (R3): nya eller djupare sänkor vid kanten på ny mark (`markhojd`).

        Tillämpar bara markytorna på vattnets rutor i en tillfällig mapp och jämför sänkorna före
        och efter (`sankgraf.skarvkontroll`). Svar: varningstexter och ett `floden-skarvkontroll/v1`
        (None när förslaget saknar ny mark).
        """
        import tempfile

        from karna.geometri.tillampa import tillampa_pa_v2_ruta
        from karna.k9_till_k1 import k9_till_lapp
        from sankgraf.skarvkontroll import SkarvKontrollFel, kontrollera_skarvar_k1

        mall = mallar.get("vatten")
        if mall is None or "vatten" not in k9:
            return [], None
        sc, _ = k9["vatten"]
        mark = tuple(a for a in sc.andringar if a.typ == "markhojd")
        if not mark:
            return [], None
        mappar = [r.mapp for r in mall.rutor]
        if valj_k1_version(mappar) < 2:
            return [], None
        # Brytlinjer och öppningar (R3) räknas med: ett utlopp (R5) ska synas i kontrollen, och en
        # mur kan ändra vilka sänkor som uppstår. Lappens yta för kontrollen är fortfarande bara
        # markytorna (polygonerna nedan).
        vektor = tuple(
            a for a in sc.andringar if a.typ in ("brytlinje_ny", "brytlinje_bort", "oppning_ny")
        )
        lapp = k9_till_lapp(mark + vektor, Planscenario("skarv", sc.bas_recept, mark + vektor))
        polygoner = [
            tuple((float(x), float(y)) for x, y in (a.geometri or {})["coordinates"][0][:-1])
            for a in mark
        ]
        fynd: list[Any] = []
        ovriga = 0
        try:
            for r in mall.rutor:
                recept = r.mapp / "recept.json"
                with tempfile.TemporaryDirectory(prefix="floden-skarv-") as tmp:
                    ut = Path(tmp) / "ruta"
                    andrad = tillampa_pa_v2_ruta(
                        r.mapp, ut, lapp=lapp, sektioner=None, geometri=(),
                        bas_recept_sha256=sha256_fil(recept) if recept.is_file() else "",
                    )
                    if not andrad:
                        continue
                    res = kontrollera_skarvar_k1(r.mapp, ut, polygoner)
                    if res is not None:
                        fynd.append(res)
                        ovriga += res.ovriga_nya_antal
        except SkarvKontrollFel as exc:
            return [f"Skarvkontrollen kunde inte köras: {exc}"], None
        if not fynd:
            return [], None
        text = [t for res in fynd for t in res.varningar()]
        sammanslagen = fynd[0].som_dict()
        for extra in fynd[1:]:
            d = extra.som_dict()
            sammanslagen["fynd"] += d["fynd"]
            sammanslagen["antal"] += d["antal"]
            sammanslagen["tillkommande_volym_m3"] = round(
                sammanslagen["tillkommande_volym_m3"] + d["tillkommande_volym_m3"], 2
            )
        return text, sammanslagen

    # ------------------------------------------------------------------ K9 per bas

    def _k9_per_bas(
        self,
        mallar: Mapping[str, Mall],
        andringar: Sequence[Mapping[str, Any]],
        scenario_id: str,
        beskrivning: str = "",
    ) -> dict[str, tuple[Planscenario, list[str]]]:
        """Ett K9 per bas (samma regel som demon): `bas_recept` ur basens recept.json.

        Höjdändringar och borttagningar av byggnader som inte finns i basens rutor tas inte med
        där (de påverkar inte den basen) och redovisas.
        """
        ut: dict[str, tuple[Planscenario, list[str]]] = {}
        for kort, m in mallar.items():
            recept = self._bas_recept(m)
            if not recept:
                raise RitatFel(f"Basen {m.bas} saknar recept.json.")
            ids = {b.byggnad_id for r in m.rutor for b in las_byggnader(r.mapp)}
            med: list[Andring] = []
            utelamnade: list[str] = []
            for a in andringar:
                if a["typ"] in ("byggnad_hojd", "byggnad_bort"):
                    bid = str((a.get("egenskaper") or {}).get("byggnads_id", ""))
                    if bid not in ids:
                        utelamnade.append(str(a["andring_id"]))
                        continue
                med.append(Andring.fran_dict(a))
            sc = Planscenario(scenario_id, recept, tuple(med), beskrivning=beskrivning)
            mappar = [r.mapp for r in m.rutor]
            _, geometri = dela_andringar(med, valj_k1_version(mappar), scenario_id)
            verifiera_byggnadsreferenser(mappar, geometri)
            ut[kort] = (sc, utelamnade)
        return ut

    def _instangt_vatten(
        self, mallar: Mapping[str, Mall], k9: Mapping[str, tuple[Planscenario, list[str]]]
    ) -> list[str]:
        """Information före körning: ritade hus som stänger in ett markområde i vattnets rutor.

        Byggnader är ogenomträngliga i vattenmodellen, så ett område som helt omges av dem blir en
        slutsänka (K2/v2): vattnet blir stående där. Det är inget fel, men användaren ska veta det.
        Kontrollen tillämpar förslaget på rutorna i en tillfällig mapp och söker områden som inte
        når rutans kant (och inte fanns redan i basen).
        """
        import tempfile

        from karna.geometri.tillampa import tillampa_pa_v2_ruta
        from sankgraf.fyllning import instangda_omraden_i_k1_mapp

        mall = mallar.get("vatten")
        if mall is None or "vatten" not in k9:
            return []
        sc, _ = k9["vatten"]
        andringar = list(sc.andringar)
        mappar = [r.mapp for r in mall.rutor]
        version = valj_k1_version(mappar)
        if version < 2 or not andringar:
            return []
        _, geometri = dela_andringar(andringar, version, "kontroll")
        if not geometri:
            return []
        for r in mall.rutor:
            recept = r.mapp / "recept.json"
            with tempfile.TemporaryDirectory(prefix="floden-instangt-") as tmp:
                ut = Path(tmp) / "ruta"
                andrad = tillampa_pa_v2_ruta(
                    r.mapp,
                    ut,
                    lapp=None,
                    sektioner=None,
                    geometri=geometri,
                    bas_recept_sha256=sha256_fil(recept) if recept.is_file() else "",
                )
                if not andrad:
                    continue
                if instangda_omraden_i_k1_mapp(ut) and not instangda_omraden_i_k1_mapp(r.mapp):
                    return [SLUTET_INFO]
        return []

    @staticmethod
    def _utanfor_avgransning(kalla: Projektkalla, andringar: Sequence[Mapping[str, Any]]) -> list[str]:
        """Varning för ett nytt hus helt utanför projektets avgränsning (polygon eller rektangel): det räknas inte till
        projektet i resultat och mått, men påverkar omgivningen. Demokvarteret har ingen avgränsning."""
        form = kalla.projektform()
        if form is None:
            return []
        ut: list[str] = []
        for a in andringar:
            g = a.get("geometri")
            if a.get("typ") != "byggnad_ny" or not isinstance(g, Mapping) or not g.get("coordinates"):
                continue
            try:
                hus = Polygon(g["coordinates"][0])
            except (TypeError, ValueError):
                continue
            if hus.is_valid and not hus.intersects(form):
                ut.append(
                    f"Huset '{a['andring_id']}' ligger utanför projektets avgränsning: det räknas inte "
                    "till projektet i resultat och nyckeltal, men påverkar omgivningen."
                )
        return ut

    def _paverkar_inte(
        self, mallar: Mapping[str, Mall], k9: Mapping[str, tuple[Planscenario, list[str]]]
    ) -> list[str]:
        """Varningar: delar av förslaget som inte når en analys område."""
        ut: list[str] = []
        for kort, (sc, utel) in k9.items():
            namn = {
                "dagsljus": "soltimmarna",
                "vatten": "vattenberäkningen",
                "ekologi": "ekologiberäkningen",
            }.get(kort, kort)
            for aid in utel:
                ut.append(
                    f"Ändringen '{aid}' ligger utanför området för {namn} och räknas inte med där."
                )
            ny = [a for a in sc.andringar if a.typ == "byggnad_ny"]
            rutor = mallar[kort].rutor
            for a in ny:
                g = a.geometri or {}
                xs = [p[0] for p in g["coordinates"][0]]
                ys = [p[1] for p in g["coordinates"][0]]
                traff = any(
                    max(xs) >= r.x_min and min(xs) <= r.x_max and max(ys) >= r.y_min
                    and min(ys) <= r.y_max
                    for r in rutor
                )
                if not traff:
                    ut.append(
                        f"Huset '{a.andring_id}' ligger utanför området för {namn} och "
                        "påverkar inte den beräkningen."
                    )
        return ut

    # ------------------------------------------------------------------ sparade scenarier

    def _scenariomapp(self, projekt: str) -> Path:
        return self.mapp / projekt / "scenarier"

    def _fil(self, projekt: str, namn_slug: str) -> Path:
        if not _SLUG.match(namn_slug):
            raise _fel(f"Ogiltigt scenarionamn '{namn_slug}'.", 400)
        return self._scenariomapp(projekt) / f"{namn_slug}.json"

    def _las(self, projekt: str, namn_slug: str) -> dict[str, Any]:
        fil = self._fil(projekt, namn_slug)
        if not fil.is_file():
            raise _fel(f"Det finns inget sparat scenario '{namn_slug}'.", 404)
        try:
            d = json.loads(fil.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise _fel(f"Scenariofilen {fil.name} går inte att läsa: {exc}") from exc
        if not isinstance(d, dict) or d.get("format") != FORMAT:
            raise _fel(f"Scenariofilen {fil.name} har okänt format.")
        return d

    def lista_scenarier(self, projekt: str) -> dict[str, Any]:
        self._kravs_projekt(projekt)
        ut: list[dict[str, Any]] = []
        mapp = self._scenariomapp(projekt)
        if mapp.is_dir():
            for fil in sorted(mapp.glob("*.json")):
                try:
                    d = json.loads(fil.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(d, dict) or d.get("format") != FORMAT:
                    continue
                korningar = d.get("korningar", {})
                har_korning = False
                if isinstance(korningar, Mapping):
                    for kpost in korningar.values():
                        kid = kpost.get("korning_id") if isinstance(kpost, Mapping) else None
                        if isinstance(kid, str) and kid:
                            korning = self.fragor.korning_eller_none(kid)
                            if korning is not None and korning.status == "klar":
                                har_korning = True
                                break
                ut.append(
                    {
                        "slug": fil.stem,
                        "namn": d.get("namn", fil.stem),
                        "andrad": d.get("andrad"),
                        "antal_andringar": len(d.get("andringar", [])),
                        "korningar": korningar,
                        "har_korning": har_korning,
                    }
                )
        return {"scenarier": ut}

    def scenarier_per_korning(self, projekt: str) -> dict[str, dict[str, str]]:
        """Körnings-id -> `{slug, namn}` för sparade förslag i projektet som har en körning.

        Läser scenariofilerna direkt (utan `_kravs_projekt`). Bara den senaste körningen per
        analysmall finns kvar i filen; äldre körningar av samma förslag har ingen koppling."""
        ut: dict[str, dict[str, str]] = {}
        if not _PROJEKT_ID_RE.match(projekt):
            return ut
        mapp = self._scenariomapp(projekt)
        if not mapp.is_dir():
            return ut
        for fil in sorted(mapp.glob("*.json")):
            try:
                d = json.loads(fil.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(d, dict) or d.get("format") != FORMAT:
                continue
            for k in (d.get("korningar") or {}).values():
                if isinstance(k, dict) and isinstance(k.get("korning_id"), str):
                    ut[k["korning_id"]] = {"slug": fil.stem, "namn": str(d.get("namn", fil.stem))}
        return ut

    def finns_scenario(self, projekt: str, namn_slug: str) -> bool:
        """Finns ett sparat förslag med det namnet (slug) i projektet?"""
        if not (_SLUG.match(namn_slug) and _PROJEKT_ID_RE.match(projekt)):
            return False
        return self._fil(projekt, namn_slug).is_file()

    def hamta_scenario(self, projekt: str, namn_slug: str) -> dict[str, Any]:
        """Det sparade förslaget. Additivt: `ritlager` (K50; ett förslag utan lager får de förvalda) och
        `fria_andringar` (K9 som inte kommer ur ritade objekt, t.ex. höjdändring av en befintlig byggnad)."""
        self._kravs_projekt(projekt)
        d = self._las(projekt, namn_slug)
        try:
            doc = ritl.kontrollera_dokument(d.get("ritlager"))
            fria = ritl.fria_andringar(d.get("andringar", []), doc)
        except RitatFel as exc:
            raise _fel(f"Förslagets lager går inte att läsa: {exc}") from exc
        return {**d, "ritlager": doc, "fria_andringar": fria}

    def spara_scenario(
        self, projekt: str, namn_slug: str | None, kropp: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Sparar (skapar eller skriver över) ett scenario efter kontroll av ändringarna."""
        self._kravs_projekt(projekt)
        namn = str(kropp.get("namn", "")).strip()
        if not namn:
            raise _fel("Ge scenariot ett namn.", 400)
        ny_slug = namn_slug or slug(namn)
        if not ny_slug:
            raise _fel("Namnet måste innehålla minst en bokstav eller siffra.", 400)
        v = self.validera(projekt, kropp)
        if not v["giltig"]:
            raise _fel("; ".join(v["fel"]))
        fil = self._fil(projekt, ny_slug)
        gammal: dict[str, Any] = {}
        if fil.is_file():
            try:
                gammal = json.loads(fil.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                gammal = {}
        nu = datetime.now(UTC).isoformat(timespec="seconds")
        d = {
            "format": FORMAT,
            "namn": namn,
            "slug": ny_slug,
            "projekt": projekt,
            "skapad": gammal.get("skapad", nu),
            "andrad": nu,
            "beskrivning": str(kropp.get("beskrivning", "")),
            "andringar": v["andringar"],
            "korningar": gammal.get("korningar", {}),
        }
        if v.get("ritlager") is not None:
            d["ritlager"] = v["ritlager"]  # K50, additivt; `andringar` är redan hela K9 (fria + översatta)
        skriv_atomiskt_text(fil, json.dumps(d, indent=2, ensure_ascii=False) + "\n")
        return d

    def byt_namn(self, projekt: str, namn_slug: str, nytt_namn: str) -> dict[str, Any]:
        self._kravs_projekt(projekt)
        d = self._las(projekt, namn_slug)
        namn = nytt_namn.strip()
        if not namn:
            raise _fel("Ge scenariot ett namn.", 400)
        ny = slug(namn)
        if not ny:
            raise _fel("Namnet måste innehålla minst en bokstav eller siffra.", 400)
        if ny != namn_slug and self._fil(projekt, ny).is_file():
            raise _fel(f"Det finns redan ett scenario som heter '{ny}'.", 409)
        d.update(namn=namn, slug=ny, andrad=datetime.now(UTC).isoformat(timespec="seconds"))
        d["korningar"] = {}  # frågefilerna heter efter scenariot: nya körs efter namnbytet
        mal = self._fil(projekt, ny)
        skriv_atomiskt_text(mal, json.dumps(d, indent=2, ensure_ascii=False) + "\n")
        if ny != namn_slug:
            self._fil(projekt, namn_slug).unlink(missing_ok=True)
        return d

    def ta_bort_scenario(self, projekt: str, namn_slug: str) -> dict[str, Any]:
        self._kravs_projekt(projekt)
        fil = self._fil(projekt, namn_slug)
        if not fil.is_file():
            raise _fel(f"Det finns inget sparat scenario '{namn_slug}'.", 404)
        fil.unlink()
        return {"borttaget": namn_slug}

    # ------------------------------------------------------------------ frågor och körning

    def _skriv_fragor(
        self,
        projekt: str,
        namn_slug: str,
        valda: Sequence[str],
        avtappning_mm_h: float | None = None,
        *,
        prefix: str = "rita",
        parametrar_over: Mapping[str, Mapping[str, Any]] | None = None,
        regn: Mapping[str, Any] | None = None,
        sol: Mapping[str, Any] | None = None,
        skyfall_metod: str = "graf",
    ) -> list[dict[str, Any]]:
        """Skriver K9 (per bas) och K20 (per mall) för ett sparat scenario. Returnerar poster.

        `parametrar_over` (analys-id -> parametrar) ersätter mallens parametrar för analysen."""
        kalla = self.kalla(projekt)
        mallar = dict(kalla.mallar)
        fragedir = kalla.fragedir or self.fragedir
        if kalla.fraga_infix:
            prefix = f"{prefix}-{kalla.fraga_infix}"  # projekt med samma scenarionamn ska inte dela frågefil
        d = self._las(projekt, namn_slug)
        urval: dict[str, list[str]] = {}
        for v in valda:
            kort, _, aid = v.partition(".")
            if kort not in mallar or aid not in {a.id for a in mallar[kort].fraga.analyser}:
                raise _fel(f"Okänd analys '{v}'.", 400)
            urval.setdefault(kort, []).append(aid)
        if not urval:
            raise _fel("Välj minst en analys att räkna.", 400)
        k9 = self._k9_per_bas(
            mallar, d["andringar"], namn_slug, beskrivning=f"Ritat i kartan: {d['namn']}"
        )
        stangt = ritl.har_stangt_innehall(d.get("ritlager"), kalla.oppen)
        vatten_fokus = self._fallback_vatten_fokus(kalla)
        poster: list[dict[str, Any]] = []
        for kort, analyser in urval.items():
            m = mallar[kort]
            sc, _ = k9[kort]
            if not sc.andringar:
                raise _fel(
                    f"Förslaget har ingen ändring som berör området för {kort}; "
                    "välj en annan analys eller rita inom området."
                )
            k9_fil = self._k9_fil(projekt, namn_slug, kort, stangt)
            skriv_scenario(sc, k9_fil)
            fragedir.mkdir(parents=True, exist_ok=True)
            max_slug = 60 - len(prefix) - 2 - len(kort)
            fraga_id = f"{prefix}-{namn_slug[:max_slug]}-{kort}"
            fil = fragedir / f"{fraga_id}.json"
            innehall = self._k20(
                m, fil, k9_fil, fraga_id, d["namn"], analyser,
                avtappning_mm_h=avtappning_mm_h, parametrar_over=parametrar_over, regn=regn, sol=sol,
                skyfall_metod=skyfall_metod, vatten_fokus=vatten_fokus,
            )
            skriv_atomiskt_text(fil, json.dumps(innehall, indent=2, ensure_ascii=False) + "\n")
            ref = self._ref_for(fil)
            poster.append(
                {"mall": kort, "ref": ref, "fraga_id": fraga_id, "k9": str(k9_fil.name),
                 "k9_nyckel": sc.nyckel, "analyser": [str(a["id"]) for a in innehall["analyser"]]}
            )
        return poster

    def _k9_fil(self, projekt: str, namn_slug: str, kort: str, stangt: bool) -> Path:
        """K9-filen för en körning. Ett förslag med objekt i ett stängt ritlager (K50, t.ex. Stockholms
        baskarta) skrivs till `k9_stangda/`, som är markerad `oppen = false` (K38): körningen av det
        lämnas då inte ut via MCP. Den andra platsens gamla fil tas bort så att klassen inte kan hänga kvar."""
        namn = f"{namn_slug}__{kort}.json"
        oppen_fil = self.mapp / projekt / "k9" / namn
        stangd_mapp = self.mapp / projekt / "k9_stangda"
        stangd_fil = stangd_mapp / namn
        if stangt:
            stangd_mapp.mkdir(parents=True, exist_ok=True)
            (stangd_mapp / "oppenhet.toml").write_text(
                'kontrakt = "floden-oppenhet/v1"\noppen = false\n'
                'kalla = "Förslag med objekt i ett stängt ritlager (K50)"\n',
                encoding="utf-8",
            )
            oppen_fil.unlink(missing_ok=True)
            return stangd_fil
        stangd_fil.unlink(missing_ok=True)
        return oppen_fil

    def _ref_for(self, fil: Path) -> str:
        for ref, f in self.fragor._fragefiler().items():  # noqa: SLF001
            if f.resolve() == fil.resolve():
                return ref
        raise _fel("Den skapade frågan hittades inte i frågemapparna.", 500)

    @staticmethod
    def _rel(fran: Path, till: Path) -> str:
        import os

        return Path(os.path.relpath(till.resolve(), fran.parent.resolve())).as_posix()

    @staticmethod
    def _valj_med_vsc(
        m: Mall,
        analyser: Sequence[str],
        sol: Mapping[str, Any] | None,
        scenarier: list[str],
    ) -> tuple[list[Any], dict[str, Any] | None]:
        """Returnerar `(valda_analyser, syntetisk_vsc_post)` och lägger till VSC vid `sol.inkludera_vsc=True`."""
        valda = [a for a in m.fraga.analyser if a.id in analyser]
        if not (sol is not None and sol.get("inkludera_vsc") is True):
            return valda, None
        dagsljus_valda = [a for a in valda if a.losare.startswith("dagsljus.")]
        if not dagsljus_valda or any(a.losare == "dagsljus.vsc_fasad" for a in valda):
            return valda, None
        mall_vsc = next((a for a in m.fraga.analyser if a.losare == "dagsljus.vsc_fasad"), None)
        if mall_vsc is not None:
            befintliga = {a.id for a in valda}
            mall_per_id = {a.id: a for a in m.fraga.analyser}
            for dep in mall_vsc.beror_pa:
                if dep not in befintliga and dep in mall_per_id:
                    valda.append(mall_per_id[dep])
                    befintliga.add(dep)
            valda.append(mall_vsc)
            return valda, None
        ref = dagsljus_valda[0]
        befintliga = {a.id for a in valda}
        vsc_id = "vsc" if "vsc" not in befintliga else "vsc_fasad"
        par = {k: v for k, v in ref.parametrar.items() if k in _DAGSLJUS_GEMENSAMMA_PAR}
        synt: dict[str, Any] = {
            "id": vsc_id,
            "losare": "dagsljus.vsc_fasad",
            "scenarier": list(scenarier),
            "parametrar": par,
        }
        if ref.fokus is not None:
            synt["fokus"] = list(ref.fokus)
        return valda, synt

    def _k20(
        self,
        m: Mall,
        fil: Path,
        k9_fil: Path,
        fraga_id: str,
        scenarionamn: str,
        analyser: Sequence[str],
        *,
        avtappning_mm_h: float | None = None,
        parametrar_over: Mapping[str, Mapping[str, Any]] | None = None,
        regn: Mapping[str, Any] | None = None,
        sol: Mapping[str, Any] | None = None,
        skyfall_metod: str = "graf",
        vatten_fokus: Sequence[float] | None = None,
    ) -> dict[str, Any]:
        bas = self._rel(fil, m.bas)
        valda, synt_vsc = self._valj_med_vsc(m, analyser, sol, ["fore", "ritat"])
        valda_ids = {a.id for a in valda}
        if synt_vsc is not None:
            valda_ids.add(str(synt_vsc["id"]))
        dynamisk = regn is not None and regn.get("berakningstyp") == "dynamisk"
        sammanhangande = sol is not None and sol.get("sammanhangande") is True
        har_klockslag = sol is not None and "klockslag_utc" in sol
        ut_analyser: list[dict[str, Any]] = []
        losare_per_analys: dict[str, str] = {}
        for a in valda:
            if skyfall_metod == "2d" and a.losare in ("vatten.statisk_fyllning", "vatten.dynamik"):
                self._kontrollera_2d_grans(m)
                losare = "vatten.2d"
            elif dynamisk and a.losare == "vatten.statisk_fyllning":
                losare = "vatten.dynamik"
            elif har_klockslag and a.losare == "dagsljus.soltimmar_mark":
                losare = "dagsljus.skuggmask_vid_klockslag"
            elif sammanhangande and a.losare == "dagsljus.soltimmar_mark":
                losare = "dagsljus.soltimmar_sammanhangande"
            else:
                losare = a.losare
            losare_per_analys[a.id] = losare
            post: dict[str, Any] = {
                "id": a.id,
                "losare": losare,
                "scenarier": ["fore", "ritat"],
                "parametrar": dict(a.parametrar),
            }
            if parametrar_over and a.id in parametrar_over:
                post["parametrar"] = {**post["parametrar"], **parametrar_over[a.id]}
            if losare == "vatten.2d":
                for k_rensa in _TVAD_EJ_STODDA_NYCKLAR:
                    post["parametrar"].pop(k_rensa, None)
                if "idf" not in post["parametrar"]:
                    post["parametrar"].pop("regntyp", None)
                post["parametrar"]["rannsten_1d"] = "auto"
                post["parametrar"].setdefault("anvand_rust", None)
            if avtappning_mm_h is not None and losare in _LOSARE_MED_AVTAPPNING:
                post["parametrar"]["avtappning_mm_h"] = float(avtappning_mm_h)
            _tillampa_regn(losare, post["parametrar"], regn, avtappning_mm_h=avtappning_mm_h)
            _tillampa_sol(losare, post["parametrar"], sol)
            if a.fokus is not None:
                post["fokus"] = list(a.fokus)
            elif vatten_fokus is not None and losare in _LOSARE_MED_REGN:
                post["fokus"] = list(vatten_fokus)
            if a.beror_pa:
                post["beror_pa"] = [b for b in a.beror_pa if b in valda_ids]
            if a.indata:
                post["indata"] = {k: self._rel(fil, p) for k, p in a.indata.items()}
            ut_analyser.append(post)
        if synt_vsc is not None:
            ut_analyser.append(synt_vsc)
        sedda: set[tuple[str, str]] = set()
        jamf: list[dict[str, Any]] = []
        for j in m.fraga.jamforelser:
            if j.analys not in valda_ids:
                continue
            utdata = j.utdata
            troskel = j.troskel
            eff_losare_j = losare_per_analys.get(j.analys)
            if (
                eff_losare_j
                in ("dagsljus.soltimmar_sammanhangande", "dagsljus.skuggmask_vid_klockslag")
                and utdata == "soltimmar"
            ):
                utdata, troskel = _STANDARDJAMFORELSE[eff_losare_j]
            if (j.analys, utdata) in sedda:
                continue
            sedda.add((j.analys, utdata))
            jamf.append(
                {"analys": j.analys, "utdata": utdata, "fore": "fore", "efter": "ritat",
                 "troskel": troskel}
            )
        if not m.fraga.jamforelser:
            # Ett projekt i en geografi utan scenarier i sina frågor (nuläget): samma jämförelser som
            # `kommuner.projekt.skriv_fragor` skriver för ett scenario.
            for a in valda:
                eff_losare = losare_per_analys[a.id]
                if eff_losare in _STANDARDJAMFORELSE:
                    utdata, troskel = _STANDARDJAMFORELSE[eff_losare]
                    jamf.append(
                        {"analys": a.id, "utdata": utdata, "fore": "fore", "efter": "ritat", "troskel": troskel}
                    )
                    if eff_losare == "ekologi.konnektivitet":
                        jamf.append(
                            {"analys": a.id, "utdata": "kansighet", "fore": "fore", "efter": "ritat", "troskel": troskel}
                        )
        return {
            "kontrakt": "K20/v1",
            "fraga_id": fraga_id,
            "beskrivning": f"Ritat förslag '{scenarionamn}': före och efter.",
            "scenarier": {
                "fore": {"bas": bas},
                "ritat": {"bas": bas, "k9": [self._rel(fil, k9_fil)]},
            },
            "analyser": ut_analyser,
            "jamforelser": jamf,
        }

    def _tid_per_losare(self) -> dict[str, float]:
        """Medianen av tidigare körtider (s) per lösare ur körningarnas sammanställningar."""
        tider: dict[str, list[float]] = {}
        mapp = self.fragor.korningsmapp
        if mapp.is_dir():
            for fil in mapp.glob("*/resultat/sammanstallning.json"):
                try:
                    d = json.loads(fil.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                for j in d.get("jobb", []):
                    if not j.get("fran_cache") and isinstance(j.get("kortid_s"), int | float):
                        tider.setdefault(str(j["losare"]), []).append(float(j["kortid_s"]))
        return {k: float(np.median(v)) for k, v in tider.items() if v}

    def planera(
        self,
        projekt: str,
        namn_slug: str,
        valda: Sequence[str],
        avtappning_mm_h: float | None = None,
        regn: Mapping[str, Any] | None = None,
        sol: Mapping[str, Any] | None = None,
        skyfall_metod: str | None = None,
    ) -> dict[str, Any]:
        """Planen för att räkna före och efter: vad som räknas om, radie och uppskattad tid."""
        poster = self._skriv_fragor(
            projekt,
            namn_slug,
            valda,
            avtappning_mm_h=_avtappning(avtappning_mm_h),
            regn=_regn(regn),
            sol=_sol(sol),
            skyfall_metod=_skyfall_metod(skyfall_metod),
        )
        return {"slug": namn_slug, "fragor": self._planposter(poster)}

    def _planposter(self, poster: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Plan, påverkansradie och uppskattad tid (ur tidigare körtider) per skriven fråga."""
        tider = self._tid_per_losare()
        ut: list[dict[str, Any]] = []
        for p in poster:
            plan = self.fragor.planera_fraga(p["ref"])
            radier: dict[str, float | None] = {}
            tid_s = 0.0
            tid_kand = True
            for j in plan["jobb"]:
                radier[j["analys"]] = j["radie_m"]
                if j["i_cache"]:
                    continue
                t = tider.get(j["losare"])
                if t is None:
                    tid_kand = False
                else:
                    tid_s += t
            ut.append(
                {
                    **p,
                    "plan": plan,
                    "paverkansradie_m": radier,
                    "uppskattad_tid_s": round(tid_s, 1) if tid_kand else None,
                    "tid_text": (
                        None if tid_kand else "Ingen tidigare körning att uppskatta tiden efter."
                    ),
                }
            )
        return ut

    def kor(
        self,
        projekt: str,
        namn_slug: str,
        valda: Sequence[str],
        avtappning_mm_h: float | None = None,
        regn: Mapping[str, Any] | None = None,
        sol: Mapping[str, Any] | None = None,
        skyfall_metod: str | None = None,
    ) -> dict[str, Any]:
        """Startar körningen. `avtappning_mm_h` (valfri) gäller vattnets analyser i körningen;
        utan den gäller standard 0 mm/h (ledningsnätet antas fullt). `regn` (valfri) styr
        regnparametrarna för skyfallslösarna; `sol` (valfri) styr dagsljusparametrarna;
        `skyfall_metod` (`"graf"` eller `"2d"`, standard `"graf"`) väljer skyfallsmetod."""
        metod = _skyfall_metod(skyfall_metod)
        poster = self._skriv_fragor(
            projekt,
            namn_slug,
            valda,
            avtappning_mm_h=_avtappning(avtappning_mm_h),
            regn=_regn(regn),
            sol=_sol(sol),
            skyfall_metod=metod,
        )
        d = self._las(projekt, namn_slug)
        korningar: dict[str, Any] = dict(d.get("korningar", {}))
        ut: list[dict[str, Any]] = []
        for p in poster:
            svar = self.fragor.starta_korning(p["ref"], projekt)
            post_k: dict[str, Any] = {
                "korning_id": svar["korning_id"],
                "ref": p["ref"],
                "k9_nyckel": p["k9_nyckel"],
            }
            if p["mall"] == "vatten":
                post_k["skyfall_metod"] = metod
            korningar[p["mall"]] = post_k
            ut.append({**p, "korning": svar})
        d["korningar"] = korningar
        fil = self._fil(projekt, namn_slug)
        skriv_atomiskt_text(fil, json.dumps(d, indent=2, ensure_ascii=False) + "\n")
        return {"slug": namn_slug, "fragor": ut}

    # ------------------------------------------------------------------ läget idag (U5a)

    def _lage_fil(self, projekt: str) -> Path:
        return self.mapp / projekt / "lage-idag.json"

    def _las_lage(self, projekt: str) -> dict[str, Any]:
        fil = self._lage_fil(projekt)
        if not fil.is_file():
            return {}
        try:
            d = json.loads(fil.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return d if isinstance(d, dict) else {}

    def _lage_k20(
        self,
        m: Mall,
        fil: Path,
        fraga_id: str,
        analyser: Sequence[str],
        avtappning_mm_h: float | None,
        regn: Mapping[str, Any] | None = None,
        sol: Mapping[str, Any] | None = None,
        skyfall_metod: str = "graf",
        vatten_fokus: Sequence[float] | None = None,
    ) -> dict[str, Any]:
        """K20 för läget idag: ett enda scenario (`fore`, projektets befintliga läge), inga jämförelser."""
        bas = self._rel(fil, m.bas)
        valda, synt_vsc = self._valj_med_vsc(m, analyser, sol, ["fore"])
        valda_ids = {a.id for a in valda}
        if synt_vsc is not None:
            valda_ids.add(str(synt_vsc["id"]))
        dynamisk = regn is not None and regn.get("berakningstyp") == "dynamisk"
        sammanhangande = sol is not None and sol.get("sammanhangande") is True
        har_klockslag = sol is not None and "klockslag_utc" in sol
        ut_analyser: list[dict[str, Any]] = []
        for a in valda:
            if skyfall_metod == "2d" and a.losare in ("vatten.statisk_fyllning", "vatten.dynamik"):
                self._kontrollera_2d_grans(m)
                losare = "vatten.2d"
            elif dynamisk and a.losare == "vatten.statisk_fyllning":
                losare = "vatten.dynamik"
            elif har_klockslag and a.losare == "dagsljus.soltimmar_mark":
                losare = "dagsljus.skuggmask_vid_klockslag"
            elif sammanhangande and a.losare == "dagsljus.soltimmar_mark":
                losare = "dagsljus.soltimmar_sammanhangande"
            else:
                losare = a.losare
            post: dict[str, Any] = {
                "id": a.id,
                "losare": losare,
                "scenarier": ["fore"],
                "parametrar": dict(a.parametrar),
            }
            if losare == "vatten.2d":
                for k_rensa in _TVAD_EJ_STODDA_NYCKLAR:
                    post["parametrar"].pop(k_rensa, None)
                if "idf" not in post["parametrar"]:
                    post["parametrar"].pop("regntyp", None)
                post["parametrar"]["rannsten_1d"] = "auto"
                post["parametrar"].setdefault("anvand_rust", None)
            if avtappning_mm_h is not None and losare in _LOSARE_MED_AVTAPPNING:
                post["parametrar"]["avtappning_mm_h"] = float(avtappning_mm_h)
            _tillampa_regn(losare, post["parametrar"], regn, avtappning_mm_h=avtappning_mm_h)
            _tillampa_sol(losare, post["parametrar"], sol)
            if a.fokus is not None:
                post["fokus"] = list(a.fokus)
            elif vatten_fokus is not None and losare in _LOSARE_MED_REGN:
                post["fokus"] = list(vatten_fokus)
            if a.beror_pa:
                post["beror_pa"] = [b for b in a.beror_pa if b in valda_ids]
            if a.indata:
                post["indata"] = {k: self._rel(fil, p) for k, p in a.indata.items()}
            ut_analyser.append(post)
        if synt_vsc is not None:
            ut_analyser.append(synt_vsc)
        return {
            "kontrakt": "K20/v1",
            "fraga_id": fraga_id,
            "beskrivning": "Läget idag: projektets befintliga läge, utan ritat förslag.",
            "scenarier": {"fore": {"bas": bas}},
            "analyser": ut_analyser,
            "jamforelser": [],
        }

    def _skriv_lage_fragor(
        self,
        projekt: str,
        valda: Sequence[str],
        avtappning_mm_h: float | None = None,
        regn: Mapping[str, Any] | None = None,
        sol: Mapping[str, Any] | None = None,
        skyfall_metod: str = "graf",
    ) -> list[dict[str, Any]]:
        """Skriver en K20 per mall (bara `fore`) för de valda analyserna. Returnerar poster som `_skriv_fragor`."""
        kalla = self.kalla(projekt)
        mallar = dict(kalla.mallar)
        fragedir = kalla.fragedir or self.fragedir
        urval: dict[str, list[str]] = {}
        for v in valda:
            kort, _, aid = v.partition(".")
            if kort not in mallar or aid not in {a.id for a in mallar[kort].fraga.analyser}:
                raise _fel(f"Okänd analys '{v}'.", 400)
            urval.setdefault(kort, []).append(aid)
        if not urval:
            raise _fel("Välj minst en analys att räkna.", 400)
        vatten_fokus = self._fallback_vatten_fokus(kalla)
        poster: list[dict[str, Any]] = []
        fragedir.mkdir(parents=True, exist_ok=True)
        proj = re.sub(r"[^a-z0-9_\-]+", "-", projekt.lower()).strip("-")[:38] or "projekt"
        for kort, analyser in urval.items():
            fraga_id = f"{LAGE_IDAG_PREFIX}{proj}-{kort}"
            fil = fragedir / f"{fraga_id}.json"
            innehall = self._lage_k20(
                mallar[kort], fil, fraga_id, analyser, avtappning_mm_h, regn, sol, skyfall_metod,
                vatten_fokus=vatten_fokus,
            )
            skriv_atomiskt_text(fil, json.dumps(innehall, indent=2, ensure_ascii=False) + "\n")
            poster.append(
                {
                    "mall": kort,
                    "ref": self._ref_for(fil),
                    "fraga_id": fraga_id,
                    "analyser": [str(a["id"]) for a in innehall["analyser"]],
                }
            )
        return poster

    def planera_lage_idag(
        self,
        projekt: str,
        valda: Sequence[str],
        avtappning_mm_h: float | None = None,
        regn: Mapping[str, Any] | None = None,
        sol: Mapping[str, Any] | None = None,
        skyfall_metod: str | None = None,
    ) -> dict[str, Any]:
        """Planen för läget idag (bara `fore`): jobb, vad som finns i cachen och uppskattad tid."""
        poster = self._skriv_lage_fragor(
            projekt,
            valda,
            _avtappning(avtappning_mm_h),
            _regn(regn),
            _sol(sol),
            _skyfall_metod(skyfall_metod),
        )
        fragor = self._planposter(poster)
        tider = [f["uppskattad_tid_s"] for f in fragor]
        return {
            "lage": "idag",
            "fragor": fragor,
            "uppskattad_tid_s": (
                round(sum(float(t) for t in tider), 1) if all(t is not None for t in tider) else None
            ),
        }

    def kor_lage_idag(
        self,
        projekt: str,
        valda: Sequence[str],
        avtappning_mm_h: float | None = None,
        regn: Mapping[str, Any] | None = None,
        sol: Mapping[str, Any] | None = None,
        skyfall_metod: str | None = None,
    ) -> dict[str, Any]:
        """Startar körningen av läget idag och sparar den som projektets egen (`projekt_id` på körningen,
        och `<projekt>/lage-idag.json` pekar ut den senaste per mall). Samma körkö, samma cache och
        samma roll (utredare) som övriga körningar; en pågående körning av samma fråga återanvänds."""
        metod = _skyfall_metod(skyfall_metod)
        poster = self._skriv_lage_fragor(
            projekt,
            valda,
            _avtappning(avtappning_mm_h),
            _regn(regn),
            _sol(sol),
            metod,
        )
        d = self._las_lage(projekt)
        korningar: dict[str, Any] = dict(d.get("korningar", {}))
        ut: list[dict[str, Any]] = []
        for p in poster:
            svar = self.fragor.starta_korning(p["ref"], projekt)
            post_k: dict[str, Any] = {
                "korning_id": svar["korning_id"],
                "ref": p["ref"],
                "fraga_id": p["fraga_id"],
                "analyser": p["analyser"],
            }
            if p["mall"] == "vatten":
                post_k["skyfall_metod"] = metod
            korningar[p["mall"]] = post_k
            ut.append({**p, "korning": svar})
        fil = self._lage_fil(projekt)
        skriv_atomiskt_text(
            fil,
            json.dumps({"lage": "idag", "korningar": korningar}, indent=2, ensure_ascii=False) + "\n",
        )
        return {"lage": "idag", "fragor": ut}

    @staticmethod
    def _regn_text_fran_resultat(res: Mapping[str, Any]) -> str | None:
        """Formaterar läsbar regntext ur körningens `antaganden.regn`, eller None om okänt."""
        antag = res.get("antaganden")
        if not isinstance(antag, Mapping):
            return None
        regn_lista = antag.get("regn")
        if not isinstance(regn_lista, list) or not regn_lista:
            return None
        r = regn_lista[0]
        if not isinstance(r, Mapping):
            return None
        delar: list[str] = []
        aterkomst = r.get("aterkomsttid_ar")
        if isinstance(aterkomst, int | float) and not isinstance(aterkomst, bool):
            delar.append(f"{float(aterkomst):g}-årsregn")
        var_min = r.get("varaktighet_min")
        if isinstance(var_min, int | float) and not isinstance(var_min, bool):
            delar.append(f"{float(var_min):g} min")
        kf = r.get("klimatfaktor")
        if (
            isinstance(kf, int | float)
            and not isinstance(kf, bool)
            and abs(float(kf) - 1.0) > 1e-9
        ):
            delar.append(f"kf {float(kf):g}")
        regn_mm = r.get("regn_mm")
        if isinstance(regn_mm, int | float) and not isinstance(regn_mm, bool):
            mm_txt = f"{float(regn_mm):g} mm"
            return f"{', '.join(delar)} ({mm_txt})" if delar else mm_txt
        return ", ".join(delar) if delar else None

    @staticmethod
    def _tal_eller_none(v: Any) -> float | None:
        if isinstance(v, bool) or not isinstance(v, int | float):
            return None
        f = float(v)
        return f if math.isfinite(f) else None

    def _forsta_jobb_manifest(
        self, korning_id: str, losare_mangd: frozenset[str]
    ) -> dict[str, Any] | None:
        """Läser `run.json` för första jobbet med en lösare i `losare_mangd`, eller None."""
        try:
            k = self.fragor._hamta(korning_id)  # noqa: SLF001 - samma paket
            s = self.fragor._sammanstallning(k)  # noqa: SLF001 - samma paket
        except FlodenWebbFel:
            return None
        for j in s.get("jobb", []):
            if str(j.get("losare")) not in losare_mangd:
                continue
            try:
                m = json.loads(
                    (self.fragor._jobbmapp(k, j) / "run.json").read_text(encoding="utf-8")  # noqa: SLF001
                )
            except (OSError, ValueError):
                continue
            if isinstance(m, dict):
                return m
        return None

    def _regn_och_avtappning_fran_resultat(
        self,
        mallar: Mapping[str, Mall],
        korning_id: str,
        res: Mapping[str, Any],
    ) -> tuple[dict[str, Any] | None, float | None]:
        """Extraherar faktiska strukturerade regn- och avtappningsparametrar ur körningens K0/antaganden (B5)."""
        antag = res.get("antaganden")
        antag_m = antag if isinstance(antag, Mapping) else {}
        analyser_raw = antag_m.get("analyser")
        analyser = analyser_raw if isinstance(analyser_raw, list) else []
        vatten_analys = next(
            (a for a in analyser if isinstance(a, Mapping) and str(a.get("losare")) in _LOSARE_MED_REGN),
            None,
        )
        regn_lista = antag_m.get("regn")
        antag_regn = (
            regn_lista[0]
            if isinstance(regn_lista, list) and regn_lista and isinstance(regn_lista[0], Mapping)
            else None
        )
        if vatten_analys is None and antag_regn is None:
            return None, None

        losare = str(vatten_analys.get("losare")) if vatten_analys is not None else "vatten.statisk_fyllning"
        skyfall_metod = "2d" if losare == "vatten.2d" else "graf"
        berakningstyp = "dynamisk" if losare in ("vatten.dynamik", "vatten.2d") else "statisk"
        manifest = self._forsta_jobb_manifest(korning_id, _LOSARE_MED_REGN)
        job_par = manifest.get("parametrar", {}) if isinstance(manifest, Mapping) else {}
        job_mv = manifest.get("matvarden", {}) if isinstance(manifest, Mapping) else {}
        analys_par = (
            vatten_analys.get("parametrar", {})
            if vatten_analys is not None and isinstance(vatten_analys.get("parametrar"), Mapping)
            else {}
        )

        aterkomst = self._tal_eller_none(
            (antag_regn or {}).get("aterkomsttid_ar")
            if (antag_regn or {}).get("aterkomsttid_ar") is not None
            else job_par.get("aterkomsttid_ar")
        )
        var_min = self._tal_eller_none(
            (antag_regn or {}).get("varaktighet_min")
            if (antag_regn or {}).get("varaktighet_min") is not None
            else job_par.get("varaktighet_min")
        )
        kf = self._tal_eller_none(
            (antag_regn or {}).get("klimatfaktor")
            if (antag_regn or {}).get("klimatfaktor") is not None
            else (
                job_par.get("klimatfaktor")
                if job_par.get("klimatfaktor") is not None
                else job_par.get("idf_klimatfaktor")
            )
        )
        regn_mm = self._tal_eller_none(
            (antag_regn or {}).get("regn_mm")
            if (antag_regn or {}).get("regn_mm") is not None
            else (job_mv.get("regn_mm") if job_mv.get("regn_mm") is not None else job_par.get("regn_mm"))
        )
        tider = job_par.get("regn_tid_s")
        mm_h = job_par.get("regn_mm_h")
        if isinstance(tider, list) and len(tider) >= 2:
            if var_min is None:
                t0 = self._tal_eller_none(tider[0])
                t1 = self._tal_eller_none(tider[-1])
                if t0 is not None and t1 is not None and t1 > t0:
                    var_min = round((t1 - t0) / 60.0, 3)
            if regn_mm is None and isinstance(mm_h, list) and len(mm_h) == len(tider):
                tot_mm = 0.0
                for idx in range(len(tider) - 1):
                    ta = self._tal_eller_none(tider[idx])
                    tb = self._tal_eller_none(tider[idx + 1])
                    ia = self._tal_eller_none(mm_h[idx])
                    ib = self._tal_eller_none(mm_h[idx + 1])
                    if ta is not None and tb is not None and ia is not None and ib is not None and tb > ta:
                        tot_mm += 0.5 * (ia + ib) * ((tb - ta) / 3600.0)
                if tot_mm > 0.0:
                    regn_mm = round(tot_mm, 3)

        raw_kalla = (antag_regn or {}).get("kalla") or job_par.get("idf_kalla") or job_par.get("idf")
        kalla = str(raw_kalla) if isinstance(raw_kalla, str) and raw_kalla else None
        raw_regntyp = (antag_regn or {}).get("regntyp") or job_par.get("regntyp") or analys_par.get("regntyp")
        regntyp: str | None = (
            str(raw_regntyp)
            if raw_regntyp in ("cds", "block")
            else ("block" if aterkomst is None else None)
        )

        # Jämför körningens faktiska regn mot projektets förvalda skyfallsmall.
        forval = self._regn_forval(mallar)
        ar_standard = False
        if forval is not None:
            f_ar = self._tal_eller_none(forval.get("aterkomsttid_ar"))
            f_var = self._tal_eller_none(forval.get("varaktighet_min"))
            f_kf = self._tal_eller_none(forval.get("klimatfaktor"))
            f_mm = self._tal_eller_none(forval.get("regn_mm"))
            f_kalla = str(forval["kalla"]) if isinstance(forval.get("kalla"), str) and forval["kalla"] else None
            if f_ar is not None:
                ar_standard = (
                    aterkomst is not None
                    and abs(aterkomst - f_ar) < 1e-6
                    and (f_var is None or (var_min is not None and abs(var_min - f_var) < 1e-6))
                    and (f_kf is None or (kf is not None and abs(kf - f_kf) < 1e-6))
                    and kalla == f_kalla
                )
            elif f_mm is not None:
                ar_standard = (
                    aterkomst is None
                    and regn_mm is not None
                    and abs(regn_mm - f_mm) < 1e-2
                    and (f_var is None or var_min is None or abs(var_min - f_var) < 1e-3)
                )

        # Avtappning: skilj explicit angiven (även 0,0) från oangiven standard (None).
        vatten_lista = antag_m.get("vatten")
        antag_vatten = (
            vatten_lista[0]
            if isinstance(vatten_lista, list) and vatten_lista and isinstance(vatten_lista[0], Mapping)
            else None
        )
        avt_val = self._tal_eller_none(
            (antag_vatten or {}).get("avtappning_mm_h")
            if (antag_vatten or {}).get("avtappning_mm_h") is not None
            else (
                job_par.get("avtappning_mm_h")
                if job_par.get("avtappning_mm_h") is not None
                else analys_par.get("avtappning_mm_h")
            )
        )
        avt_angiven = job_par.get("avtappning_angiven")
        if avt_angiven is None:
            avt_angiven = analys_par.get("avtappning_angiven")
        if avt_val is not None and avt_val > 0.0:
            avtappning_mm_h: float | None = avt_val
        elif avt_val is not None and avt_angiven is True:
            avtappning_mm_h = avt_val
        else:
            avtappning_mm_h = None

        regn_info: dict[str, Any] = {
            "aterkomsttid_ar": aterkomst,
            "varaktighet_min": var_min,
            "klimatfaktor": kf,
            "regn_mm": regn_mm,
            "berakningstyp": berakningstyp,
            "regntyp": regntyp,
            "kalla": kalla,
            "ar_standard": ar_standard,
            "skyfall_metod": skyfall_metod,
        }
        return regn_info, avtappning_mm_h

    def _sol_info_fran_resultat(
        self,
        mallar: Mapping[str, Mall],
        korning_id: str,
        res: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Extraherar faktiska strukturerade solparametrar ur körningens K0/antaganden (B14)."""
        antag = res.get("antaganden")
        antag_m = antag if isinstance(antag, Mapping) else {}
        analyser_raw = antag_m.get("analyser")
        analyser = analyser_raw if isinstance(analyser_raw, list) else []
        sol_analys = next(
            (a for a in analyser if isinstance(a, Mapping) and str(a.get("losare")) in _LOSARE_MED_SOL),
            None,
        )
        if sol_analys is None:
            return None
        losare = str(sol_analys.get("losare"))
        manifest = self._forsta_jobb_manifest(korning_id, _LOSARE_MED_SOL)
        job_par = manifest.get("parametrar", {}) if isinstance(manifest, Mapping) else {}
        analys_par = (
            sol_analys.get("parametrar", {})
            if isinstance(sol_analys.get("parametrar"), Mapping)
            else {}
        )
        raw_datum = job_par.get("datum")
        if isinstance(raw_datum, list) and raw_datum and isinstance(raw_datum[0], str):
            datum_iso: str | None = raw_datum[0]
        elif isinstance(raw_datum, str) and raw_datum:
            datum_iso = raw_datum
        elif isinstance(analys_par.get("datum"), str):
            datum_iso = str(analys_par["datum"])
        else:
            datum_iso = None

        sammanhangande = losare == "dagsljus.soltimmar_sammanhangande"
        inkludera_vsc = any(
            isinstance(a, Mapping) and str(a.get("losare")) == "dagsljus.vsc_fasad" for a in analyser
        )
        klockslag_utc: float | None = None
        start_timme: float | None = None
        slut_timme: float | None = None
        if losare == "dagsljus.skuggmask_vid_klockslag":
            raw_kl = job_par.get("klockslag")
            if isinstance(raw_kl, list) and raw_kl:
                kl0 = self._tal_eller_none(raw_kl[0])
                off = self._tal_eller_none(job_par.get("utc_offset_h")) or 0.0
                if kl0 is not None:
                    klockslag_utc = round(kl0 - off, 6)
        else:
            st_val = self._tal_eller_none(
                job_par.get("start_timme")
                if job_par.get("start_timme") is not None
                else analys_par.get("start_timme")
            )
            sl_val = self._tal_eller_none(
                job_par.get("slut_timme")
                if job_par.get("slut_timme") is not None
                else analys_par.get("slut_timme")
            )
            if st_val is not None and st_val > 0.0:
                start_timme = st_val
            if sl_val is not None and sl_val < 24.0:
                slut_timme = sl_val

        # Jämför mot projektets förvalda dagsljusmall.
        dag_mall = mallar.get("dagsljus")
        mall_datum: str | None = None
        if dag_mall is not None:
            sol_m = next(
                (a for a in dag_mall.fraga.analyser if a.losare == "dagsljus.soltimmar_mark"), None
            )
            if sol_m is not None:
                md = sol_m.parametrar.get("datum")
                if isinstance(md, list | tuple) and md:
                    mall_datum = str(md[0])
                elif isinstance(md, str):
                    mall_datum = md
        ar_standard = (
            losare == "dagsljus.soltimmar_mark"
            and not sammanhangande
            and not inkludera_vsc
            and klockslag_utc is None
            and start_timme is None
            and slut_timme is None
            and (mall_datum is None or datum_iso == mall_datum)
        )
        return {
            "datum_iso": datum_iso,
            "start_timme": start_timme,
            "slut_timme": slut_timme,
            "sammanhangande": sammanhangande,
            "inkludera_vsc": inkludera_vsc,
            "klockslag_utc": klockslag_utc,
            "ar_standard": ar_standard,
        }

    def status_lage_idag(self, projekt: str) -> dict[str, Any]:
        """Körningarnas status för läget idag och, för klara, vyerna (bara före) för kartan."""
        kalla = self.kalla(projekt)
        mallar = dict(kalla.mallar)
        ut: list[dict[str, Any]] = []
        for kort, k in self._las_lage(projekt).get("korningar", {}).items():
            try:
                st = self.fragor.korning_status(k["korning_id"])
            except FlodenWebbFel:
                continue
            post: dict[str, Any] = {
                "mall": kort,
                "status": st,
                "vyer": [],
                "regn_text": None,
                "regn": None,
                "skyfall_metod": (
                    k.get("skyfall_metod")
                    if k.get("skyfall_metod") in _GILTIGA_SKYFALL_METODER
                    else ("graf" if kort == "vatten" else None)
                ),
                "avtappning_mm_h": None,
                "sol": None,
            }
            if st["status"] == "klar":
                try:
                    res = self.fragor.resultat(k["korning_id"])
                    post["vyer"] = self._lage_vyer(res, k["korning_id"])
                    post["varningar"] = [str(v["text"]) for v in res.get("varningar", [])]
                    regn_info, avt_mm_h = self._regn_och_avtappning_fran_resultat(
                        mallar, k["korning_id"], res
                    )
                    post["regn"] = regn_info
                    if regn_info is not None and regn_info.get("skyfall_metod") in _GILTIGA_SKYFALL_METODER:
                        post["skyfall_metod"] = regn_info["skyfall_metod"]
                    post["avtappning_mm_h"] = avt_mm_h
                    regn_text = self._regn_text_fran_resultat(res)
                    if regn_text is None and regn_info and regn_info.get("regn_mm") is not None:
                        regn_text = f"{float(regn_info['regn_mm']):g} mm"
                    post["regn_text"] = regn_text
                    post["sol"] = self._sol_info_fran_resultat(mallar, k["korning_id"], res)
                except FlodenWebbFel:
                    pass
            ut.append(post)
        return {
            "lage": "idag",
            "korningar": ut,
            "berakningsomfang": self._berakningsomfang(kalla),
        }

    def lage_idag_korningar(self, projekt: str) -> list[str]:
        """Körnings-id för projektets senaste körning av läget idag per mall."""
        return [str(k["korning_id"]) for k in self._las_lage(projekt).get("korningar", {}).values()]

    @staticmethod
    def _avsnitt_for_lager(la: Mapping[str, Any]) -> str:
        """Visningsavsnitt (domän) ur lagrets `doman`-fält, med reserv för äldre lager utan domän."""
        doman = str(la.get("doman") or "").strip()
        if doman:
            return doman
        return {"skyfall": "vatten", "konnektivitet": "ekologi"}.get(
            str(la.get("analys", "")), "dagsljus"
        )

    @staticmethod
    def _lage_vyer(res: Mapping[str, Any], korning_id: str) -> list[dict[str, Any]]:
        """Vyer för läget idag: en per analys med primärt lager i `fore`. Ingen efter- eller skillnadsvy
        (`efter_lager` är null, `skillnad_lager` pekar på samma lager som `fore_lager`, `lage_idag` är sant)."""
        vyer: list[dict[str, Any]] = []
        for la in res["lager"]:
            if la["typ"] == "skillnad" or not la["primar"] or la["scenario"] != "fore":
                continue
            vyer.append(
                {
                    "id": f"{korning_id}:{la['id']}",
                    "avsnitt": KartverktygTjanst._avsnitt_for_lager(la),
                    "rubrik": f"Läget idag: {la.get('etikett') or la['analys']}",
                    "mattnamn": la.get("etikett") or la["analys"],
                    "korning_id": korning_id,
                    "fore_lager": str(la["id"]),
                    "efter_lager": None,
                    "skillnad_lager": str(la["id"]),
                    "markering_lager": None,
                    "efter_namn": "Läget idag",
                    "andring": "Projektets befintliga läge, utan ritat förslag.",
                    "lage_idag": True,
                }
            )
        return vyer

    def kor_ekologi(
        self,
        projekt: str,
        namn_slug: str | None,
        parametrar: Mapping[str, Any],
        konto_projekt: str | None = None,
        *,
        skrivskyddad: bool = False,
        arbetsmapp: Path | None = None,
    ) -> dict[str, Any]:
        """Kör (eller återanvänder ur cachen) ekologianalysen före/efter synkront.

        `namn_slug` är ett sparat scenario; utan det används projektets förslag (K9 i mallen, eller
        ett tomt nulägesscenario för geografiprojekt som ännu saknar sparat scenario). Körningen får
        ett id som beror på projekt, parametrar och scenariots K9-nyckel, så samma anrop ger samma
        körning. Med `skrivskyddad=True` skrivs temporära K9-/K20-filer bara i `arbetsmapp`
        (intern cachekatalog) utan att mutera projektets mappar eller scenarier. Fel i lösaren
        (t.ex. för många fläckar) kastas som 400.
        """
        import hashlib

        kalla = self.kalla(projekt)
        mallar = dict(kalla.mallar)
        m = mallar.get("ekologi")
        if m is None:
            raise _fel(
                "Projektet har ingen ekologianalys (kör `floden demo`)."
                if projekt == _PROJEKT_ID
                else "Projektets geografi saknar ekologiunderlag (NMD-lager / ekologi.toml). "
                "Mappa geografin med --nmd och skriv om projektets frågor.",
                404,
            )
        analys = next((a.id for a in m.fraga.analyser if a.losare == "ekologi.konnektivitet"), None)
        if analys is None:
            raise _fel("Ekologimallen saknar lösaren ekologi.konnektivitet.", 500)
        fragedir = (
            (arbetsmapp or (self.fragor.cachemapp / "_ekologi_las" / projekt))
            if skrivskyddad
            else (kalla.fragedir or self.fragedir)
        )
        proj_tag = f"-{kalla.fraga_infix}" if kalla.fraga_infix else ""
        over = {analys: dict(parametrar)}
        tag = hashlib.sha256(json.dumps(over, sort_keys=True).encode()).hexdigest()[:8]
        if namn_slug is None:
            k9 = next((s.k9 for s in m.fraga.scenarier.values() if s.k9), ())
            if k9:
                k9_fil = k9[-1]
                sc_namn = "projektets förslag"
            else:
                recept = self._bas_recept(m)
                if not recept:
                    raise _fel(f"Basen {m.bas} saknar recept.json.", 500)
                k9_fil = (
                    (fragedir / "k9" / "api-ekologi-nulage.json")
                    if skrivskyddad
                    else (self.mapp / projekt / "k9" / "api-ekologi-nulage.json")
                )
                skriv_scenario(
                    Planscenario("nulage", recept, (), beskrivning="Nuläge utan ändring"),
                    k9_fil,
                )
                sc_namn = "nuläge"
            fraga_id = f"api-ekologi{proj_tag}-{tag}"
            fil = fragedir / f"{fraga_id}.json"
            innehall = self._k20(
                m, fil, k9_fil, fraga_id, sc_namn, [analys], parametrar_over=over
            )
            skriv_atomiskt_text(fil, json.dumps(innehall, indent=2, ensure_ascii=False) + "\n")
            scen_nyckel = sha256_fil(k9_fil)
            korning_id = (
                f"api-ekologi{proj_tag}-{tag}-{hashlib.sha256(scen_nyckel.encode()).hexdigest()[:8]}"
            )
            if skrivskyddad:
                status, _ = self.fragor.kor_fil_synkront(fil, korning_id, konto_projekt or projekt)
                return {"korning_id": korning_id, "status": status, "analys": analys, "ref": fraga_id}
            ref = self._ref_for(fil)
        elif skrivskyddad:
            d = self._las(projekt, namn_slug)
            k9_map = self._k9_per_bas(
                mallar, d["andringar"], namn_slug, beskrivning=f"Ritat i kartan: {d['namn']}"
            )
            sc, _ = k9_map["ekologi"]
            if not sc.andringar:
                raise _fel(
                    "Förslaget har ingen ändring som berör området för ekologi; "
                    "välj en annan analys eller rita inom området."
                )
            stangt = ritl.har_stangt_innehall(d.get("ritlager"), kalla.oppen)
            k9_namn = f"{namn_slug}__ekologi.json"
            oppen_fil = fragedir / "k9" / k9_namn
            stangd_mapp = fragedir / "k9_stangda"
            stangd_fil = stangd_mapp / k9_namn
            if stangt:
                stangd_mapp.mkdir(parents=True, exist_ok=True)
                skriv_atomiskt_text(
                    stangd_mapp / "oppenhet.toml",
                    'kontrakt = "floden-oppenhet/v1"\noppen = false\n'
                    'kalla = "Förslag med objekt i ett stängt ritlager (K50)"\n',
                )
                oppen_fil.unlink(missing_ok=True)
                k9_fil = stangd_fil
            else:
                stangd_fil.unlink(missing_ok=True)
                k9_fil = oppen_fil
            skriv_scenario(sc, k9_fil)
            fraga_id = f"api{tag}-{namn_slug[:24]}-ekologi"
            fil = fragedir / f"{fraga_id}.json"
            innehall = self._k20(
                m, fil, k9_fil, fraga_id, d["namn"], [analys], parametrar_over=over
            )
            skriv_atomiskt_text(fil, json.dumps(innehall, indent=2, ensure_ascii=False) + "\n")
            scen_nyckel = f"{sc.nyckel}:stangt" if stangt else sc.nyckel
            korning_id = (
                f"api-ekologi{proj_tag}-{tag}-{hashlib.sha256(scen_nyckel.encode()).hexdigest()[:8]}"
            )
            status, _ = self.fragor.kor_fil_synkront(fil, korning_id, konto_projekt or projekt)
            return {"korning_id": korning_id, "status": status, "analys": analys, "ref": fraga_id}
        else:
            poster = self._skriv_fragor(
                projekt, namn_slug, [f"ekologi.{analys}"], prefix=f"api{tag}", parametrar_over=over
            )
            p = poster[0]
            ref = str(p["ref"])
            scen_nyckel = str(p["k9_nyckel"])
            korning_id = (
                f"api-ekologi{proj_tag}-{tag}-{hashlib.sha256(scen_nyckel.encode()).hexdigest()[:8]}"
            )
        status, _ = self.fragor.kor_synkront(ref, korning_id, konto_projekt or projekt)
        return {"korning_id": korning_id, "status": status, "analys": analys, "ref": ref}

    def status(self, projekt: str, namn_slug: str) -> dict[str, Any]:
        """Körningarnas status och, för klara, vyerna (före, efter, skillnad) för kartan."""
        kalla = self.kalla(projekt)
        mallar = dict(kalla.mallar)
        d = self._las(projekt, namn_slug)
        ut: list[dict[str, Any]] = []
        for kort, k in d.get("korningar", {}).items():
            try:
                st = self.fragor.korning_status(k["korning_id"])
            except FlodenWebbFel:
                continue
            post: dict[str, Any] = {
                "mall": kort,
                "status": st,
                "vyer": [],
                "regn_text": None,
                "regn": None,
                "skyfall_metod": (
                    k.get("skyfall_metod")
                    if k.get("skyfall_metod") in _GILTIGA_SKYFALL_METODER
                    else ("graf" if kort == "vatten" else None)
                ),
                "avtappning_mm_h": None,
                "sol": None,
            }
            if st["status"] == "klar":
                try:
                    res = self.fragor.resultat(k["korning_id"])
                    post["vyer"] = self._vyer(res, d["namn"], k["korning_id"])
                    post["varningar"] = [str(v["text"]) for v in res.get("varningar", [])]
                    regn_info, avt_mm_h = self._regn_och_avtappning_fran_resultat(
                        mallar, k["korning_id"], res
                    )
                    post["regn"] = regn_info
                    if regn_info is not None and regn_info.get("skyfall_metod") in _GILTIGA_SKYFALL_METODER:
                        post["skyfall_metod"] = regn_info["skyfall_metod"]
                    post["avtappning_mm_h"] = avt_mm_h
                    regn_text = self._regn_text_fran_resultat(res)
                    if regn_text is None and regn_info and regn_info.get("regn_mm") is not None:
                        regn_text = f"{float(regn_info['regn_mm']):g} mm"
                    post["regn_text"] = regn_text
                    post["sol"] = self._sol_info_fran_resultat(mallar, k["korning_id"], res)
                except FlodenWebbFel:
                    pass
            ut.append(post)
        return {
            "slug": namn_slug,
            "korningar": ut,
            "berakningsomfang": self._berakningsomfang(kalla),
        }

    @staticmethod
    def _vyer(res: Mapping[str, Any], namn: str, korning_id: str) -> list[dict[str, Any]]:
        """Vyer (som projektsidans) för en klar körning: en per jämförelse med skillnadslager."""
        vyer: list[dict[str, Any]] = []
        for la in res["lager"]:
            if la["typ"] != "skillnad" or not la["primar"]:
                continue

            def lager_for(scen: str, analys: str = la["analys"]) -> str | None:
                for x in res["lager"]:
                    if (
                        x["analys"] == analys and x["scenario"] == scen and x["primar"]
                        and x["typ"] != "skillnad"
                    ):
                        return str(x["id"])
                return None

            def markering_for(scen: str, analys: str = la["analys"]) -> str | None:
                # Kontrollraster för slutna områden (K2/v2, `slutsanka.tif`) i efter-läget.
                for x in res["lager"]:
                    if x["analys"] == analys and x["scenario"] == scen and x["roll"] == "slutsanka":
                        return str(x["id"])
                return None

            vyer.append(
                {
                    "id": f"{korning_id}:{la['id']}",
                    "avsnitt": KartverktygTjanst._avsnitt_for_lager(la),
                    "rubrik": f"{namn}: {la.get('etikett') or la['analys']}",
                    "mattnamn": la.get("etikett") or la["analys"],
                    "korning_id": korning_id,
                    "fore_lager": lager_for(la["fore"]),
                    "efter_lager": lager_for(la["efter"]),
                    "skillnad_lager": la["id"],
                    "markering_lager": markering_for(la["efter"]),
                    "efter_namn": namn,
                    "andring": f"Ritat förslag: {namn}.",
                }
            )
        return vyer

    # ------------------------------------------------------------------ WMS (proxy)

    @staticmethod
    def _splitta_adress(url: str) -> urllib.parse.SplitResult:
        delar = urllib.parse.urlsplit(url.strip())
        if delar.scheme not in ("http", "https") or not delar.hostname:
            raise _fel("Adressen måste börja med http:// eller https://.", 400)
        return delar

    def _kontrollera_och_los_upp(self, url: str) -> tuple[urllib.parse.SplitResult, str, int]:
        """Validerar URL, gör en enda DNS-uppslagning och kontrollerar alla returnerade IP-adresser.

        Returnerar `(delar, pinnad_ip, port)` så att anslutningen kan pinnas direkt mot den
        validerade IP-adressen utan ny DNS-uppslagning (skydd mot DNS-rebinding).
        """
        delar = self._splitta_adress(url)
        assert delar.hostname is not None
        port = delar.port or (443 if delar.scheme == "https" else 80)
        try:
            traffar = socket.getaddrinfo(delar.hostname, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise _fel(f"Adressen {delar.hostname} går inte att nå: {exc}", 502) from exc
        if not traffar:
            raise _fel(f"Adressen {delar.hostname} gav inga IP-adresser.", 502)
        pinnad_ip: str | None = None
        for t in traffar:
            ip_str = str(t[4][0])
            raw_ip = ipaddress.ip_address(ip_str)
            ip = (
                raw_ip.ipv4_mapped
                if isinstance(raw_ip, ipaddress.IPv6Address) and raw_ip.ipv4_mapped is not None
                else raw_ip
            )
            if ip.is_loopback:
                if not self.tillat_loopback:
                    raise _fel("Adresser på den här datorn är inte tillåtna som underlag.", 400)
                if pinnad_ip is None:
                    pinnad_ip = ip_str
                continue
            if (
                ip.is_private
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
                or not ip.is_global
            ):
                raise _fel("Den adressen är inte tillåten.", 400)
            if pinnad_ip is None:
                pinnad_ip = ip_str
        assert pinnad_ip is not None
        return delar, pinnad_ip, port

    def _kontrollera_adress(self, url: str) -> urllib.parse.SplitResult:
        delar, _, _ = self._kontrollera_och_los_upp(url)
        return delar

    @staticmethod
    def _host_header(hostname: str, scheme: str, port: int) -> str:
        host = f"[{hostname}]" if ":" in hostname else hostname
        standard_port = 443 if scheme == "https" else 80
        return host if port == standard_port else f"{host}:{port}"

    def _hamta(self, url: str, max_bytes: int) -> tuple[bytes, str]:
        """Hämtar URL med DNS-pinning: ansluter direkt till validerad IP med korrekt Host/SNI."""
        aktuell = url
        for _ in range(MAX_WMS_OMDIRIGERINGAR + 1):
            delar, pinnad_ip, port = self._kontrollera_och_los_upp(aktuell)
            assert delar.hostname is not None
            host_header = self._host_header(delar.hostname, delar.scheme, port)
            sokvag = delar.path or "/"
            if delar.query:
                sokvag = f"{sokvag}?{delar.query}"
            conn: http.client.HTTPConnection
            if delar.scheme == "https":
                conn = _PinnadHTTPSConnection(
                    pinnad_ip, port, sni_host=delar.hostname, timeout=WMS_TIDSGRANS_S
                )
            else:
                conn = http.client.HTTPConnection(pinnad_ip, port, timeout=WMS_TIDSGRANS_S)
            try:
                conn.request(
                    "GET",
                    sokvag,
                    headers={"Host": host_header, "User-Agent": "Floden-kartverktyg"},
                )
                svar = conn.getresponse()
                if svar.status in (301, 302, 303, 307, 308):
                    plats = svar.getheader("Location")
                    svar.read(0)
                    if not plats:
                        raise _fel(f"Tjänsten svarade med fel {svar.status}.", 502)
                    aktuell = urllib.parse.urljoin(aktuell, plats)
                    continue
                if svar.status >= 400:
                    raise _fel(f"Tjänsten svarade med fel {svar.status}.", 502)
                data: bytes = svar.read(max_bytes + 1)
                typ = svar.headers.get_content_type()
            except FlodenWebbFel:
                raise
            except (http.client.HTTPException, OSError, ValueError) as exc:
                raise _fel(f"Tjänsten gick inte att nå: {exc}", 502) from exc
            finally:
                conn.close()
            if len(data) > max_bytes:
                raise _fel("Svaret från tjänsten är för stort.", 502)
            return data, typ
        raise _fel("För många omdirigeringar från tjänsten.", 502)

    @staticmethod
    def _lokalt(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def wms_lager(self, kropp: Mapping[str, Any]) -> dict[str, Any]:
        """GetCapabilities via servern: titel, version och lager med stödda koordinatsystem."""
        url = str(kropp.get("url", "")).strip()
        delar = self._splitta_adress(url)
        fraga = {
            k: v
            for k, v in urllib.parse.parse_qsl(delar.query, keep_blank_values=True)
            if k.lower() not in ("request", "service", "version")
        }
        fraga.update({"SERVICE": "WMS", "REQUEST": "GetCapabilities"})
        bas = urllib.parse.urlunsplit(
            (delar.scheme, delar.netloc, delar.path, urllib.parse.urlencode(fraga), "")
        )
        data, _ = self._hamta(bas, MAX_CAPABILITIES_BYTES)
        if b"<!ENTITY" in data:
            raise _fel("Tjänstens svar innehåller otillåtna definitioner.", 502)
        try:
            rot = ET.fromstring(data)  # noqa: S314 - storlek begränsad, entiteter nekade ovan
        except ET.ParseError as exc:
            raise _fel(f"Svaret från tjänsten är inte en WMS-beskrivning: {exc}", 502) from exc
        if "WMS_Capabilities" not in self._lokalt(rot.tag) and "WMT_MS_Capabilities" not in (
            self._lokalt(rot.tag)
        ):
            raise _fel("Svaret från tjänsten är inte en WMS-beskrivning.", 502)
        version = rot.get("version", "1.3.0")
        titel = ""
        for e in rot.iter():
            if self._lokalt(e.tag) == "Title" and e.text:
                titel = e.text.strip()
                break
        lager: list[dict[str, Any]] = []

        def gå(e: ET.Element, arvt: list[str]) -> None:
            crs = list(arvt)
            namn = ""
            lt = ""
            barn: list[ET.Element] = []
            for c in e:
                t = self._lokalt(c.tag)
                if t in ("CRS", "SRS") and c.text:
                    crs.extend(x.strip().upper() for x in c.text.split())
                elif t == "Name" and c.text:
                    namn = c.text.strip()
                elif t == "Title" and c.text:
                    lt = c.text.strip()
                elif t == "Layer":
                    barn.append(c)
            if namn:
                stod = "3857" if any(x in ("EPSG:3857", "EPSG:900913") for x in crs) else (
                    "4326" if "EPSG:4326" in crs else None
                )
                lager.append({"namn": namn, "titel": lt or namn, "stod": stod})
            for c in barn:
                gå(c, crs)

        for e in rot.iter():
            if self._lokalt(e.tag) == "Capability":
                for c in e:
                    if self._lokalt(c.tag) == "Layer":
                        gå(c, [])
                break
        if not lager:
            raise _fel("Tjänsten har inga lager att visa.", 502)
        return {
            "url": url,
            "titel": titel,
            "version": version,
            "lager": lager,
            "anvandbara": [x for x in lager if x["stod"]],
        }

    def wms_bild(self, query: Mapping[str, list[str]]) -> tuple[bytes, str]:
        """GetMap för en MapLibre-bricka: `bbox` i EPSG:3857, `crs` 3857 eller 4326."""

        def q(namn: str, standard: str = "") -> str:
            return query.get(namn, [standard])[0]

        url = q("url")
        delar = self._splitta_adress(url)
        lager = q("lager")
        if not lager:
            raise _fel("Lager saknas.", 400)
        try:
            bbox = [float(x) for x in q("bbox").split(",")]
            bredd = int(q("bredd", "256"))
            hojd = int(q("hojd", "256"))
        except ValueError as exc:
            raise _fel("bbox, bredd och hojd måste vara tal.", 400) from exc
        if len(bbox) != 4 or not all(math.isfinite(x) for x in bbox):
            raise _fel("bbox ska vara fyra tal.", 400)
        if not (16 <= bredd <= 2048 and 16 <= hojd <= 2048):
            raise _fel("Bildstorleken är utanför gränserna.", 400)
        crs = q("crs", "3857")
        version = q("version", "1.3.0")
        if version not in ("1.1.1", "1.3.0"):
            raise _fel("WMS-versionen stöds inte.", 400)
        if crs == "4326":
            r = 6378137.0

            def lonlat(x: float, y: float) -> tuple[float, float]:
                return (
                    math.degrees(x / r),
                    math.degrees(2 * math.atan(math.exp(y / r)) - math.pi / 2),
                )

            lo0, la0 = lonlat(bbox[0], bbox[1])
            lo1, la1 = lonlat(bbox[2], bbox[3])
            box = [la0, lo0, la1, lo1] if version == "1.3.0" else [lo0, la0, lo1, la1]
            kod = "EPSG:4326"
        elif crs == "3857":
            box, kod = bbox, "EPSG:3857"
        else:
            raise _fel("Koordinatsystemet stöds inte (EPSG:3857 eller EPSG:4326).", 400)
        fraga = {
            k: v
            for k, v in urllib.parse.parse_qsl(delar.query, keep_blank_values=True)
            if k.lower() not in ("request", "service", "version", "layers", "styles", "bbox",
                                 "width", "height", "format", "crs", "srs", "transparent")
        }
        fraga.update(
            {
                "SERVICE": "WMS", "VERSION": version, "REQUEST": "GetMap", "LAYERS": lager,
                "STYLES": "", "FORMAT": "image/png", "TRANSPARENT": "TRUE",
                "WIDTH": str(bredd), "HEIGHT": str(hojd),
                "CRS" if version == "1.3.0" else "SRS": kod,
                "BBOX": ",".join(f"{x:.9f}" for x in box),
            }
        )
        mal = urllib.parse.urlunsplit(
            (delar.scheme, delar.netloc, delar.path, urllib.parse.urlencode(fraga), "")
        )
        data, typ = self._hamta(mal, MAX_WMS_BYTES)
        if typ.lower() not in ("image/png", "image/jpeg", "image/webp"):
            raise _fel(
                f"Tjänsten svarade inte med en tillåten rasterbild (fick {typ!r}).",
                502,
            )
        return data, typ

    # ------------------------------------------------------------------ geometri (visning)

    def _geometri_delar(
        self, projekt: str, vilket: str
    ) -> list[tuple[np.ndarray[Any, Any], Any]]:
        """K1 v2-rutornas `terrang.tif` (eller `vegetation.tif`) för projektet, som (matris, geotransform)."""
        from karna.geometri import las_terrang, las_vegetation

        mallar = self._kravs_projekt(projekt)
        if vilket not in GEOMETRILAGER:
            raise _fel(f"Okänt geometrilager '{vilket}'.", 404)
        mall = max(mallar.values(), key=lambda m: len(m.rutor))
        delar: list[tuple[np.ndarray[Any, Any], Any]] = []
        for r in mall.rutor:
            if vilket == "vegetation":
                v = las_vegetation(r.mapp)
                if v is not None:
                    delar.append(v)
            else:
                delar.append(las_terrang(r.mapp))
        if not delar:
            raise _fel("Det finns inget sådant lager i projektets rutor.", 404)
        return delar

    @staticmethod
    def _mosaik(
        delar: Sequence[tuple[np.ndarray[Any, Any], Any]], max_px: int = ORTOFOTO_MAX_PX
    ) -> tuple[np.ndarray[Any, Any], float, tuple[float, float, float, float]]:
        """Rutorna som en matris (norr uppåt) i den finaste pixelstorleken; nedsamplad till högst `max_px`.

        Ger (matris, cellstorlek efter nedsamplingen, (x_min, y_min, x_max, y_max))."""
        px = min(g.pixelstorlek_x for _, g in delar)
        x0 = min(g.x_min for _, g in delar)
        y1 = max(g.y_max for _, g in delar)
        x1 = max(g.x_min + g.bredd * g.pixelstorlek_x for _, g in delar)
        y0 = min(g.y_max - g.hojd * g.pixelstorlek_y for _, g in delar)
        b = round((x1 - x0) / px)
        h = round((y1 - y0) / px)
        glob = np.full((h, b), np.nan, dtype=np.float32)
        for arr, g in delar:
            f = round(g.pixelstorlek_x / px)
            a = np.repeat(np.repeat(np.asarray(arr, dtype=np.float32), f, 0), f, 1)
            c0 = round((g.x_min - x0) / px)
            r0 = round((y1 - g.y_max) / px)
            glob[r0 : r0 + a.shape[0], c0 : c0 + a.shape[1]] = a
        steg = max(1, math.ceil(max(h, b) / max_px))
        return glob[::steg, ::steg], px * steg, (x0, y0, x1, y1)

    def geometri_png(self, projekt: str, vilket: str) -> tuple[bytes, dict[str, str]]:
        """Terrängskuggning, höjdkurvor, markhöjd i färg eller vegetation som bild i EPSG:3011, ritad ur K1 v2-rutornas filer.

        Det är en visning av indata (skuggning av `terrang.tif`, höjden i `vegetation.tif`),
        inget resultat från någon lösare. Höjdkurvorna ritas i transparent PNG med en ekvidistans
        som väljs ur höjdspannet (rubriken `X-Floden-Ekvidistans`, meter).
        """
        from PIL import Image

        delar = self._geometri_delar(projekt, vilket)
        glob, cell, (x0, y0, x1, y1) = self._mosaik(delar)
        extra: dict[str, str] = {}
        if vilket == "skuggning":
            z = np.where(np.isfinite(glob), glob, np.nanmean(glob))
            # Låg relief ger en platt grå bild; höjden överdrivs så att marken syns (rubriken `X-Floden-Overdrift`).
            spann = float(np.nanpercentile(glob, 98) - np.nanpercentile(glob, 2))
            overdrift = float(np.clip(40.0 / max(spann, 1e-6), 1.0, 8.0))
            dzdy, dzdx = np.gradient(z * overdrift, cell)
            az, al = math.radians(315.0), math.radians(45.0)
            lutning = np.arctan(np.hypot(dzdx, dzdy))
            aspekt = np.arctan2(dzdy, -dzdx)
            skugga = np.sin(al) * np.cos(lutning) + np.cos(al) * np.sin(lutning) * np.cos(
                az - aspekt
            )
            grå = (np.clip(skugga, 0, 1) * 200 + 30).astype(np.uint8)
            alfa = np.where(np.isfinite(glob), 255, 0).astype(np.uint8)
            rgba = np.dstack([grå, grå, grå, alfa])
            extra["X-Floden-Overdrift"] = f"{overdrift:g}"
        elif vilket == "hojdkurvor":
            rgba, ekv = _hojdkurvor_rgba(glob, cell)
            extra["X-Floden-Ekvidistans"] = f"{ekv:g}"
        elif vilket == "markhojd":
            rgba = _markhojd_rgba(glob)
            lag, hog = float(np.nanmin(glob)), float(np.nanmax(glob))
            extra["X-Floden-Hojdspann"] = f"{lag:.2f},{hog:.2f}"
        else:
            vh = np.nan_to_num(glob, nan=0.0)
            alfa = np.where(vh >= 0.5, np.clip(80 + vh * 8, 0, 190), 0).astype(np.uint8)
            rgba = np.dstack(
                [np.full(vh.shape, 60, np.uint8), np.full(vh.shape, 140, np.uint8),
                 np.full(vh.shape, 70, np.uint8), alfa]
            )
        buf = io.BytesIO()
        Image.fromarray(rgba, "RGBA").save(buf, format="PNG")
        return buf.getvalue(), {"X-Floden-Utbredning": f"{x0},{y0},{x1},{y1}", **extra}

    def markhojd(self, projekt: str, e: float, n: float) -> dict[str, Any]:
        """Markhöjden (m RH 2000, `terrang.tif`: marken utan byggnader) i punkten (e, n), för avläsning under pekaren.

        Läser cellen ur projektets rutor; inget räknas om. `hojd_m` är `None` utanför rutorna eller där
        terrängen saknar värde, och `inom` säger om punkten ligger i en ruta."""
        if not (math.isfinite(e) and math.isfinite(n)):
            raise _fel("Ange e och n som tal (SWEREF 99 18 00).", 400)
        delar = self._terrang_cache(projekt)
        for arr, g in delar:
            kol = int((e - g.x_min) // g.pixelstorlek_x)
            rad = int((g.y_max - n) // g.pixelstorlek_y)
            if 0 <= kol < arr.shape[1] and 0 <= rad < arr.shape[0]:
                v = float(arr[rad, kol])
                return {
                    "inom": True,
                    "hojd_m": v if math.isfinite(v) else None,
                    "enhet": "m",
                    "hojdsystem": "RH 2000",
                    "kalla": "terrang.tif (marken utan byggnader)",
                }
        return {"inom": False, "hojd_m": None, "enhet": "m", "hojdsystem": "RH 2000", "kalla": "terrang.tif (marken utan byggnader)"}

    def _terrang_cache(self, projekt: str) -> list[tuple[np.ndarray[Any, Any], Any]]:
        """Terrängrutorna i minnet (en gång per projekt; byts när projektets filer ändrats)."""
        kalla = self.kalla(projekt)
        nyckel = (projekt, getattr(kalla, "stampel", None))
        gammal = self._terrang.get(projekt)
        if gammal is None or gammal[0] != nyckel:
            gammal = (nyckel, self._geometri_delar(projekt, "terrang"))
            self._terrang[projekt] = gammal
        return gammal[1]

    def sektion(
        self,
        projekt: str,
        punkter: Any,
        *,
        korning_id: str | None = None,
        fore_lager: str | None = None,
        efter_lager: str | None = None,
        steg_m: float | None = None,
    ) -> dict[str, Any]:
        """Beräknar deterministisk sektion/tvärsnitt längs en linje/polylinje (`floden-sektion/v1`).

        Utan `korning_id` samplas projektets terräng (`terrang.tif`) och byggnader.
        Med `korning_id` läses scenariots markhöjder, vattendjup + takmask (`<lager_id>.npy` /
        `<lager_id>.tak.npy`) och scenariots byggnader (före och ev. efter).
        """
        kalla = self.kalla(projekt)
        try:
            validera_sektion_punkter(punkter)
        except ValueError as exc:
            raise _fel(str(exc), 400) from exc

        eff_steg: float | None = None
        if steg_m is not None:
            if (
                isinstance(steg_m, bool)
                or not isinstance(steg_m, int | float)
                or not math.isfinite(float(steg_m))
                or float(steg_m) <= 0.0
            ):
                raise _fel(f"steg_m måste vara ett ändligt tal > 0, fick {steg_m!r}.", 400)
            eff_steg = float(steg_m)

        for f_namn, f_val in (
            ("korning_id", korning_id),
            ("fore_lager", fore_lager),
            ("efter_lager", efter_lager),
        ):
            if f_val is not None and not isinstance(f_val, str):
                raise _fel(f"Fältet '{f_namn}' måste vara en text eller null.", 400)

        kid = korning_id.strip() if isinstance(korning_id, str) and korning_id.strip() else None
        fl = fore_lager.strip() if isinstance(fore_lager, str) and fore_lager.strip() else None
        el = efter_lager.strip() if isinstance(efter_lager, str) and efter_lager.strip() else None

        if kid is None:
            if fl is not None or el is not None:
                raise _fel("Ange 'korning_id' när 'fore_lager' eller 'efter_lager' anges.", 400)
            terrang_fore: Any = self._terrang_cache(projekt)
            byggnader_fore: Any = self._alla_byggnader(kalla)
            try:
                return berakna_sektion(
                    punkter,
                    terrang_fore,
                    byggnader_fore=byggnader_fore,
                    steg_m=eff_steg,
                )
            except ValueError as exc:
                raise _fel(str(exc), 400) from exc

        from karna.geometri import las_terrang

        k = self.fragor._hamta(kid)  # noqa: SLF001 - samma paket
        s = self.fragor._sammanstallning(k)  # noqa: SLF001 - samma paket
        res = self.fragor.resultat(kid)
        lager_per_id = {str(la["id"]): la for la in res.get("lager", [])}

        def _las_djup_och_tak(lager_id: str) -> tuple[str, RasterLager, Any]:
            la = lager_per_id.get(lager_id)
            if la is None:
                raise _fel(f"Okänt lager '{lager_id}' i körningen '{kid}'.", 404)
            roll = str(la.get("roll") or "")
            typ = str(la.get("typ") or "")
            if roll != "max_djup" or typ == "skillnad":
                raise _fel(
                    f"Lagret '{lager_id}' (roll '{roll}', typ '{typ}') kan inte användas som "
                    "vattendjup i sektion; endast vattendjupslager ('max_djup') stöds.",
                    400,
                )
            npy_fil = k.mapp / "webb" / f"{lager_id}.npy"
            if not npy_fil.is_file():
                raise _fel(f"Rasterdata saknas för lager '{lager_id}'.", 404)
            arr = np.load(npy_fil, mmap_mode="r")
            u = la["utbredning"]
            px = float(la["pixelstorlek_m"])
            raster = RasterLager(
                raster=arr,
                x_min=float(u["x_min"]),
                y_max=float(u["y_max"]),
                pixelstorlek_x=px,
                pixelstorlek_y=px,
            )
            takfil = k.mapp / "webb" / f"{lager_id}.tak.npy"
            tak = np.load(takfil, mmap_mode="r") if takfil.is_file() else None
            return str(la["scenario"]), raster, tak

        fore_scen: str | None = None
        efter_scen: str | None = None
        djup_fore: RasterLager | None = None
        tak_fore: Any = None
        djup_efter: RasterLager | None = None
        tak_efter: Any = None
        if fl is not None:
            fore_scen, djup_fore, tak_fore = _las_djup_och_tak(fl)
        if el is not None:
            efter_scen, djup_efter, tak_efter = _las_djup_och_tak(el)
        if fore_scen is None:
            scenarier = list(s.get("scenarier", ()))
            fore_scen = str(scenarier[0]) if scenarier else None

        rutor_per_scenario, _ = self.fragor._scenariorutor(k, s)  # noqa: SLF001 - samma paket
        if (
            rutor_per_scenario is not None
            and fore_scen is not None
            and rutor_per_scenario.get(fore_scen)
        ):
            terrang_fore = [las_terrang(mapp) for mapp in rutor_per_scenario[fore_scen].values()]
        else:
            terrang_fore = self._terrang_cache(projekt)

        terrang_efter: Any = None
        if (
            rutor_per_scenario is not None
            and efter_scen is not None
            and rutor_per_scenario.get(efter_scen)
        ):
            terrang_efter = [las_terrang(mapp) for mapp in rutor_per_scenario[efter_scen].values()]

        bygg_data = res.get("byggnader")
        bygg_per_scen = bygg_data.get("scenarier", {}) if isinstance(bygg_data, Mapping) else {}
        byggnader_fore = bygg_per_scen.get(fore_scen) if fore_scen is not None else None
        if byggnader_fore is None:
            byggnader_fore = self._alla_byggnader(kalla)
        byggnader_efter = bygg_per_scen.get(efter_scen) if efter_scen is not None else None

        try:
            return berakna_sektion(
                punkter,
                terrang_fore,
                terrang_efter=terrang_efter,
                djup_fore=djup_fore,
                tak_fore=tak_fore,
                djup_efter=djup_efter,
                tak_efter=tak_efter,
                byggnader_fore=byggnader_fore,
                byggnader_efter=byggnader_efter,
                steg_m=eff_steg,
            )
        except ValueError as exc:
            raise _fel(str(exc), 400) from exc

    # ------------------------------------------------------------------ ortofoto (GeoTIFF)

    def _orto_filer(self) -> list[Path]:
        ut: list[Path] = []
        for rot in self.datamappar:
            if not rot.is_dir():
                continue
            manifest = [rot / "lantmateriet_manifest.json"]
            manifest += list(rot.glob("*/lantmateriet_manifest.json"))
            manifest += list(rot.glob("*/*/lantmateriet_manifest.json"))
            for m in manifest:
                if not m.is_file():
                    continue
                try:
                    d = json.loads(m.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                for f in d.get("filer", []):
                    if f.get("roll") == "orto" and str(f.get("fil", "")).lower().endswith(
                        (".tif", ".tiff")
                    ):
                        p = (m.parent / str(f["fil"])).resolve()
                        if p.is_file() and p.is_relative_to(m.parent.resolve()):
                            ut.append(p)
        return sorted(set(ut))

    def ortofoto_info(self, projekt: str) -> dict[str, Any]:
        if projekt != _PROJEKT_ID:
            return {"finns": False}  # geografin lagrar inget ortofoto; de lokala filerna hör till demon
        filer = self._orto_filer()
        if not filer:
            return {"finns": False}
        return {
            "finns": True,
            "antal_filer": len(filer),
            "png": f"/api/kartverktyg/{projekt}/ortofoto.png",
            "kalla": "©Lantmäteriet (ortofoto, hämtat lokalt)",
        }

    def ortofoto_png(self, projekt: str) -> tuple[bytes, dict[str, str]]:
        """Ortofoto över projektets utsträckning som PNG i EPSG:3011 (omprojicerat vid behov)."""
        import rasterio
        from PIL import Image
        from rasterio.crs import CRS
        from rasterio.transform import from_origin
        from rasterio.warp import Resampling, reproject, transform_bounds

        mallar = self._kravs_projekt(projekt)
        filer = self._orto_filer() if projekt == _PROJEKT_ID else []
        if not filer:
            raise _fel("Det finns inget lokalt ortofoto.", 404)
        rutor = self._rutor_alla(mallar)
        x0, y0 = min(r[0] for r in rutor), min(r[1] for r in rutor)
        x1, y1 = max(r[2] for r in rutor), max(r[3] for r in rutor)
        res = max((x1 - x0), (y1 - y0)) / ORTOFOTO_MAX_PX
        res = max(res, 0.25)
        bredd, hojd = math.ceil((x1 - x0) / res), math.ceil((y1 - y0) / res)
        dst_t = from_origin(x0, y0 + hojd * res, res, res)
        dst_crs = CRS.from_epsg(3011)
        rgb = np.zeros((3, hojd, bredd), dtype=np.uint8)
        tackt = np.zeros((hojd, bredd), dtype=bool)
        for f in filer:
            with rasterio.open(f) as ds:
                if ds.crs is None or ds.count < 3:
                    continue
                b = transform_bounds(dst_crs, ds.crs, x0, y0, x1, y1)
                if b[2] < ds.bounds.left or b[0] > ds.bounds.right or b[3] < ds.bounds.bottom or (
                    b[1] > ds.bounds.top
                ):
                    continue
                band = np.zeros((3, hojd, bredd), dtype=np.uint8)
                for i in range(3):
                    reproject(
                        source=rasterio.band(ds, i + 1), destination=band[i],
                        src_transform=ds.transform, src_crs=ds.crs,
                        dst_transform=dst_t, dst_crs=dst_crs, resampling=Resampling.bilinear,
                    )
                giltig = band.any(axis=0) & ~tackt
                for i in range(3):
                    rgb[i][giltig] = band[i][giltig]
                tackt |= giltig
        if not tackt.any():
            raise _fel("Ortofotot täcker inte projektets område.", 404)
        rgba = np.dstack([rgb.transpose(1, 2, 0), (tackt * 255).astype(np.uint8)])
        buf = io.BytesIO()
        Image.fromarray(rgba, "RGBA").save(buf, format="PNG")
        return buf.getvalue(), {
            "X-Floden-Utbredning": f"{x0},{y0},{x1},{y1}",
        }
