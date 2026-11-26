"""Jämförelser mellan scenarier (T20): `efter − fore` för ett rasterresultat.

Fungerar likadant för alla domäner så länge resultatet är ett K1-justerat GeoTIFF. Rutvisa
analyser jämförs ruta för ruta och summeras.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

# Plan importeras bara för typning för att undvika cirkulär import.
from karna.korplan import Jobb, Plan
from karna.resultatcache import ResultatCache
from karna.rutscenario import Ruta
from karna.utredning import Jamforelse


def _agarmask(ruta: Ruta, alla: tuple[Ruta, ...], geo: Any) -> np.ndarray[Any, Any]:
    """Celler som räknas till `ruta` när rutor överlappar (K1 har 25 m överlapp).

    En cell ägs av den ruta där cellens mittpunkt ligger djupast inne (störst minsta
    avstånd till kanten); lika djup avgörs av ruta-id. Ytor räknas då en gång.
    """
    kol = geo.x_min + (np.arange(geo.bredd) + 0.5) * geo.pixelstorlek_x
    rad = geo.y_max - (np.arange(geo.hojd) + 0.5) * geo.pixelstorlek_y
    x, y = np.meshgrid(kol, rad)

    def djup(r: Ruta) -> np.ndarray[Any, Any]:
        return np.asarray(
            np.minimum(np.minimum(x - r.x_min, r.x_max - x), np.minimum(y - r.y_min, r.y_max - y))
        )

    egen = djup(ruta)
    agd = np.ones(egen.shape, dtype=bool)
    for annan in alla:
        if annan.ruta_id == ruta.ruta_id or ruta.avstand_m(annan) > 0.0:
            continue
        d = djup(annan)
        if annan.ruta_id < ruta.ruta_id:
            agd &= egen > d
        else:
            agd &= egen >= d
    return agd


class JamforFel(Exception):
    """Fel vid jämförelse mellan scenarier."""


def _rasterfil(j: Jobb, cache: ResultatCache, roll: str) -> Path:
    filer = j.utdata.get("filer", {})
    if roll not in filer:
        raise JamforFel(
            f"Jämförelse: {j.etikett} har ingen utdata '{roll}'. Finns: {sorted(filer)}."
        )
    namn = str(filer[roll]["fil"])
    if not namn.lower().endswith((".tif", ".tiff")):
        raise JamforFel(f"Jämförelse stöder bara GeoTIFF, '{roll}' är {namn}.")
    return cache.jobbmapp(j.nyckel) / "utdata" / namn


def kor_jamforelse(
    jf: Jamforelse, plan: Plan, cache: ResultatCache, utmapp: Path
) -> dict[str, Any]:
    """Skriver differensraster och returnerar mått för sammanställningen."""
    from testsvit.k1_io import las_geotiff, skriv_geotiff

    fore = {(j.ruta.ruta_id if j.ruta else "*"): j for j in plan.jobb_for(jf.analys, jf.fore)}
    efter = {(j.ruta.ruta_id if j.ruta else "*"): j for j in plan.jobb_for(jf.analys, jf.efter)}
    if set(fore) != set(efter):
        raise JamforFel(
            f"Jämförelse {jf.analys}: scenarierna '{jf.fore}' och '{jf.efter}' har olika rutor "
            f"({sorted(fore)} mot {sorted(efter)})."
        )
    namn = f"{jf.analys}_{jf.utdata}_{jf.efter}_minus_{jf.fore}"
    mal = utmapp / namn
    mal.mkdir(parents=True, exist_ok=True)

    max_okning = 0.0
    max_minskning = 0.0
    yta_okning_m2 = 0.0
    yta_minskning_m2 = 0.0
    oforandrade_rutor = 0
    per_ruta: dict[str, dict[str, float]] = {}
    for rid in sorted(fore):
        jf_f, jf_e = fore[rid], efter[rid]
        if jf_f.nyckel == jf_e.nyckel:
            oforandrade_rutor += 1
        a_raw, geo_a = las_geotiff(_rasterfil(jf_f, cache, jf.utdata))
        b_raw, geo_b = las_geotiff(_rasterfil(jf_e, cache, jf.utdata))
        if (
            geo_a.bredd,
            geo_a.hojd,
            geo_a.x_min,
            geo_a.y_max,
            geo_a.pixelstorlek_x,
            geo_a.pixelstorlek_y,
        ) != (
            geo_b.bredd,
            geo_b.hojd,
            geo_b.x_min,
            geo_b.y_max,
            geo_b.pixelstorlek_x,
            geo_b.pixelstorlek_y,
        ):
            raise JamforFel(f"Jämförelse {namn}/{rid}: rastren har olika georeferens.")
        a = np.asarray(a_raw, dtype=np.float64)
        b = np.asarray(b_raw, dtype=np.float64)
        giltig = np.isfinite(a) & np.isfinite(b)
        diff = np.where(giltig, b - a, np.nan)
        giltig &= np.isfinite(diff)
        cell_m2 = geo_a.pixelstorlek_x * geo_a.pixelstorlek_y
        if jf_e.ruta is not None:
            giltig = giltig & _agarmask(jf_e.ruta, jf_e.scenario.rutor, geo_a)
        upp = giltig & (diff > jf.troskel)
        ned = giltig & (diff < -jf.troskel)
        if np.any(giltig):
            giltiga_diff = diff[giltig]
            r_max = float(np.max(giltiga_diff))
            r_min = float(np.min(giltiga_diff))
        else:
            r_max = 0.0
            r_min = 0.0
        per_ruta[rid] = {
            "max_okning": max(r_max, 0.0),
            "max_minskning": max(-r_min, 0.0),
            "yta_okning_m2": float(np.count_nonzero(upp)) * cell_m2,
            "yta_minskning_m2": float(np.count_nonzero(ned)) * cell_m2,
        }
        max_okning = max(max_okning, r_max)
        max_minskning = max(max_minskning, -r_min)
        yta_okning_m2 += per_ruta[rid]["yta_okning_m2"]
        yta_minskning_m2 += per_ruta[rid]["yta_minskning_m2"]
        filnamn = "diff.tif" if rid == "*" else f"{rid}.tif"
        skriv_geotiff(
            mal / filnamn,
            np.asarray(diff, dtype=np.float32),
            x_min=geo_a.x_min,
            y_max=geo_a.y_max,
            pixelstorlek=geo_a.pixelstorlek_x,
        )

    return {
        "namn": namn,
        "analys": jf.analys,
        "utdata": jf.utdata,
        "fore": jf.fore,
        "efter": jf.efter,
        "troskel": jf.troskel,
        "max_okning": max_okning,
        "max_minskning": max_minskning,
        "yta_okning_m2": yta_okning_m2,
        "yta_minskning_m2": yta_minskning_m2,
        "rutor": len(fore),
        "rutor_med_samma_nyckel": oforandrade_rutor,
        "per_ruta": per_ruta,
        "mapp": str(mal),
    }
