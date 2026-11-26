"""Serverrenderad karta med fast kartografi för MCP-verktyget `karta_bild` och skissrapporter.

Kartan ritas av Floden ur lagrets eget raster via ``rapport.figurer`` (inbäddade TrueType-typsnitt
Gelasio och Liberation Sans, ämnesanpassade skalor/teckenförklaringar, norrpil, skalstock och
spårbarhetsfot i SWEREF 99 18 00 / RH 2000). AI:n väljer bara vilket lager och vilket utsnitt.
"""

from __future__ import annotations

import io
import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray

from rapport.figurer import (
    berakna_skalstock_m,
    figurstil_for_lager,
    rendera_kartfigur,
)
from testsvit.k1_io import GeoTransformInfo

_MAX_RASTER_PX = 2048


class KartaFel(Exception):
    """Kartan kan inte ritas."""


def rendera_karta(
    *,
    raster: NDArray[np.float32],
    tak: NDArray[np.bool_] | None,
    lager: Mapping[str, Any],
    byggnader: Sequence[Mapping[str, Any]] | None,
    korning_id: str,
    kodversion: str,
    skiss: bool,
    utsnitt: tuple[float, float, float, float] | None = None,
    max_px: int = 900,
    rubrik: str | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Ritar lagret som PNG via ``rapport.figurer``.

    Returnerar ``(png, meta)`` där ``meta`` beskriver utsnitt, skala, upplösning och kartmotor.
    """
    u = lager["utbredning"]
    px = float(lager["pixelstorlek_m"])
    ox_m, oy_m = float(u["x_min"]), float(u["y_max"])
    hojd_c, bredd_c = raster.shape
    c0, c1, r0, r1 = 0, bredd_c, 0, hojd_c
    if utsnitt is not None:
        x0, y0, x1, y1 = utsnitt
        c0 = max(0, int(math.floor((x0 - ox_m) / px)))
        c1 = min(bredd_c, int(math.ceil((x1 - ox_m) / px)))
        r0 = max(0, int(math.floor((oy_m - y1) / px)))
        r1 = min(hojd_c, int(math.ceil((oy_m - y0) / px)))
        if c1 <= c0 or r1 <= r0:
            raise KartaFel("Utsnittet ligger utanför lagrets utsträckning.")

    sub = raster[r0:r1, c0:c1]
    tak_sub = None if tak is None else tak[r0:r1, c0:c1]

    faktor = max(1, math.ceil(max(sub.shape) / _MAX_RASTER_PX))
    sub_vis = sub[::faktor, ::faktor] if faktor > 1 else sub
    tak_vis = (
        None
        if tak_sub is None
        else (tak_sub[::faktor, ::faktor] if faktor > 1 else tak_sub)
    )
    px_bild = px * faktor

    h_c, b_c = sub_vis.shape
    x_min = ox_m + c0 * px
    y_max = oy_m - r0 * px
    x_max = x_min + b_c * px_bild
    y_min = y_max - h_c * px_bild

    geo = GeoTransformInfo(
        bredd=int(b_c),
        hojd=int(h_c),
        x_min=x_min,
        y_max=y_max,
        pixelstorlek_x=px_bild,
        pixelstorlek_y=px_bild,
        epsg=3011,
        dtype="float32",
    )

    scen = (
        f"{lager['efter']} − {lager['fore']}"
        if lager["typ"] == "skillnad"
        else str(lager["scenario"])
    )
    typtext = {
        "bas": "bas",
        "scenario": "scenario",
        "skillnad": "skillnad (efter minus före)",
    }[lager["typ"]]
    if rubrik is None:
        rubrik = f"{lager['etikett']}, {typtext}: {scen}".replace("_", " ")

    stil = figurstil_for_lager(lager)
    karta_max_px = max(120, int(max_px))
    skala_vis = karta_max_px / max(b_c, h_c)
    vis_w = max(200, int(round(b_c * skala_vis)))
    langd_m, _ = berakna_skalstock_m(geo, vis_w)

    anmarkning = (
        "SKISS: metoden är inte klassad för formell rapport. "
        "Ingen AI har räknat eller ritat talen."
        if skiss
        else "Ingen AI har räknat eller ritat talen."
    )
    duk = rendera_kartfigur(
        sub_vis,
        stil,
        titel=rubrik,
        undertitel=f"Körning {korning_id} · EPSG:3011",
        metodstatus="Skissunderlag" if skiss else "Rapportmetod",
        geo=geo,
        byggnad_mask=tak_vis,
        byggnader_3011=byggnader,
        anmarkning=anmarkning,
        kalla=f"Floden {kodversion} · Ritad ur körningens GeoTIFF",
        karta_max_px=karta_max_px,
        legend_placering="sida",
    )
    ut = io.BytesIO()
    duk.save(ut, format="PNG", optimize=True)
    return ut.getvalue(), {
        "bild_px": [duk.width, duk.height],
        "kartutsnitt": [x_min, y_min, x_max, y_max],
        "kartutsnitt_beskrivning": "[e_min, n_min, e_max, n_max] i EPSG:3011 (m)",
        "cellstorlek_m": px_bild,
        "enhet": lager["enhet"] or None,
        "skala": lager["skala"],
        "nedsampling": faktor,
        "norr": "uppat",
        "skalstock_m": float(langd_m),
        "kartmotor": "rapport.figurer",
        "figurstil": stil.namn,
    }
