"""Fasadpunkter (kontrakt K11): mätpunkter längs fasader, genererade ur byggnadsregistret.

Punkterna ingår inte i K1; de skapas av den här funktionen så att dagsljus, buller och luft får
samma punkter. Per fasadsegment (en kant i polygonen) läggs `ceil(L / steg_m)` punkter mitt i lika
långa delsegment; per våning en punkt på höjden
`markhojd + (v - 1) * vaningshojd_m + hojd_over_golv_m`.
Normalen pekar ut från byggnaden (in i innergårdar för hål). Fasader som sitter mot en annan
byggnad (gemensam brandvägg) ges inga punkter.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from numpy.typing import NDArray

from karna.geometri.lasa import RutaLike, alla_byggnader
from karna.geometri.rasterfil import GeometriFel
from markmodell.byggnadsregister import ByggnadsPost, Ring

KONTRAKT_K11 = "K11/v1"
GRANNTEST_AVSTAND_M = 0.05


@dataclass(frozen=True)
class Fasadpunkter:
    """Kolumner som numpy-arrayer, lika långa. Se K11 i `docs/DATAKONTRAKT.md`."""

    fasadpunkt_id: NDArray[np.str_]
    byggnad_id: NDArray[np.str_]
    vaning: NDArray[np.int32]
    e_m: NDArray[np.float64]
    n_m: NDArray[np.float64]
    z_rh2000: NDArray[np.float64]
    hojd_over_mark_m: NDArray[np.float64]
    normal_azimut_grader: NDArray[np.float64]
    normal_e: NDArray[np.float64]
    normal_n: NDArray[np.float64]
    delbredd_m: NDArray[np.float64]

    def __len__(self) -> int:
        return int(self.e_m.size)

    def till_geodataframe(self) -> Any:
        import geopandas as gpd

        return gpd.GeoDataFrame(
            {
                "fasadpunkt_id": self.fasadpunkt_id,
                "byggnad_id": self.byggnad_id,
                "vaning": self.vaning,
                "z_rh2000": self.z_rh2000,
                "hojd_over_mark_m": self.hojd_over_mark_m,
                "normal_azimut_grader": self.normal_azimut_grader,
                "normal_e": self.normal_e,
                "normal_n": self.normal_n,
                "delbredd_m": self.delbredd_m,
            },
            geometry=gpd.points_from_xy(self.e_m, self.n_m),
            crs="EPSG:3011",
        )

    def skriv_geoparquet(self, sokvag: str | Path) -> Path:
        mal = Path(sokvag)
        mal.parent.mkdir(parents=True, exist_ok=True)
        self.till_geodataframe().to_parquet(mal)
        return mal


def _kanter(post: ByggnadsPost) -> list[tuple[int, int, tuple[float, float], tuple[float, float]]]:
    """(ring, kant, start, slut) med yttre ring moturs och hål medurs (utåt = till höger)."""
    ut = []
    ringar: list[Ring] = [post.polygon, *post.hal]
    for r, ring in enumerate(ringar):
        for i, a in enumerate(ring):
            ut.append((r, i, a, ring[(i + 1) % len(ring)]))
    return ut


def fasadpunkter(
    rutor: Sequence[RutaLike],
    steg_m: float,
    vaningshojd_m: float,
    *,
    hojd_over_golv_m: float = 1.5,
) -> Fasadpunkter:
    """Fasadpunkter för alla byggnader i `rutor`, en gång per `byggnad_id` (sorterat på id).

    En byggnad som finns i flera rutor ger punkter en gång. Antal våningar: registrets `vaningar`
    om det finns, annars `max(1, floor(hojd_m / vaningshojd_m))`; punkter över taket utelämnas.
    """
    if not (math.isfinite(steg_m) and steg_m > 0.0):
        raise GeometriFel(f"steg_m måste vara > 0, fick {steg_m}.")
    if not (math.isfinite(vaningshojd_m) and vaningshojd_m > 0.0):
        raise GeometriFel(f"vaningshojd_m måste vara > 0, fick {vaningshojd_m}.")
    register = alla_byggnader(list(rutor))
    ordnade = [register[k] for k in sorted(register)]
    geoms = [b.geometri() for b in ordnade]
    traed = shapely.STRtree(geoms)

    kol: dict[str, list[Any]] = {
        k: []
        for k in (
            "id", "bid", "vaning", "e", "n", "z", "h", "az", "ne", "nn", "dl"
        )
    }
    for idx, post in enumerate(ordnade):
        antal_vaningar = (
            post.vaningar
            if post.vaningar is not None and post.vaningar > 0
            else max(1, int(post.hojd_m // vaningshojd_m))
        )
        grannar = [
            int(j) for j in traed.query(shapely.box(*geoms[idx].bounds).buffer(0.2)) if j != idx
        ]
        for r, k, (x0, y0), (x1, y1) in _kanter(post):
            dx, dy = x1 - x0, y1 - y0
            langd = math.hypot(dx, dy)
            if langd <= 0.0:
                continue
            ux, uy = dx / langd, dy / langd
            nx, ny = uy, -ux  # höger om färdriktningen = utåt
            antal = max(1, math.ceil(langd / steg_m - 1e-9))
            az = math.degrees(math.atan2(nx, ny)) % 360.0
            for i in range(antal):
                t = (i + 0.5) / antal
                px, py = x0 + dx * t, y0 + dy * t
                if grannar:
                    qx = np.array([px + nx * GRANNTEST_AVSTAND_M])
                    qy = np.array([py + ny * GRANNTEST_AVSTAND_M])
                    if any(bool(shapely.contains_xy(geoms[j], qx, qy)[0]) for j in grannar):
                        continue
                for v in range(1, antal_vaningar + 1):
                    h = (v - 1) * vaningshojd_m + hojd_over_golv_m
                    if h >= post.hojd_m:
                        break
                    kol["id"].append(f"{post.byggnad_id}:{r}-{k}-{i}:{v}")
                    kol["bid"].append(post.byggnad_id)
                    kol["vaning"].append(v)
                    kol["e"].append(px)
                    kol["n"].append(py)
                    kol["z"].append(post.markhojd_rh2000 + h)
                    kol["h"].append(h)
                    kol["az"].append(az)
                    kol["ne"].append(nx)
                    kol["nn"].append(ny)
                    kol["dl"].append(langd / antal)
    return Fasadpunkter(
        fasadpunkt_id=np.array(kol["id"], dtype=np.str_),
        byggnad_id=np.array(kol["bid"], dtype=np.str_),
        vaning=np.array(kol["vaning"], dtype=np.int32),
        e_m=np.array(kol["e"], dtype=np.float64),
        n_m=np.array(kol["n"], dtype=np.float64),
        z_rh2000=np.array(kol["z"], dtype=np.float64),
        hojd_over_mark_m=np.array(kol["h"], dtype=np.float64),
        normal_azimut_grader=np.array(kol["az"], dtype=np.float64),
        normal_e=np.array(kol["ne"], dtype=np.float64),
        normal_n=np.array(kol["nn"], dtype=np.float64),
        delbredd_m=np.array(kol["dl"], dtype=np.float64),
    )
