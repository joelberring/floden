"""Syntetisk bullerscen för tester och mätning: lutande terräng, kvarter, hus och gator.

Scenen är helt bestämd av sina parametrar och fröet. Den innehåller roterade rektangulära
hus, vinkelhus (icke-konvexa) och sammanbyggda huspar med olika takhöjd.
"""

from __future__ import annotations

import math

import numpy as np
import shapely

from buller.scen.bullerscen import Bullerscen, Vag
from buller.typer import BullerFel, Byggnad, Flyttal

# Ursprung i SWEREF 99 18 00, valt för att ge koordinater av verklig storleksordning.
URSPRUNG_E_M = 150_000.0
URSPRUNG_N_M = 6_580_000.0

_GATUHALVBREDD_M = 4.0
_HUSMARGINAL_M = 8.0
_MARKSLAG_FLACKAR = (1, 4, 5, 7, 8)
_FLACK_M = 20.0


def terranghojd_m(x_m: Flyttal, y_m: Flyttal) -> Flyttal:
    """Scenens analytiska markyta (lokala koordinater från sydvästra hörnet)."""
    hojd: Flyttal = 20.0 + 0.03 * x_m + 0.02 * y_m + np.sin(x_m / 40.0) * np.cos(y_m / 55.0)
    return hojd


def syntetisk_scen(
    *,
    sida_m: float = 300.0,
    cell_m: float = 0.5,
    tomt_m: float = 50.0,
    fro: int = 1,
    okant_andel: float = 0.0,
) -> Bullerscen:
    """Bygger en kvadratisk scen med en byggnadstomt per `tomt_m` × `tomt_m` och gator emellan.

    `okant_andel` är andelen markslagsfläckar som sätts till kod 0 (okänt).
    """
    antal_tomter = int(round(sida_m / tomt_m))
    if antal_tomter < 2 or abs(antal_tomter * tomt_m - sida_m) > 1e-9:
        raise BullerFel("sida_m ska vara en heltalsmultipel (minst 2) av tomt_m.")
    antal_celler = int(round(sida_m / cell_m))
    if abs(antal_celler * cell_m - sida_m) > 1e-9:
        raise BullerFel("sida_m ska vara en heltalsmultipel av cell_m.")
    radie_m = tomt_m / 2.0 - _HUSMARGINAL_M
    if radie_m <= 2.0:
        raise BullerFel("tomt_m är för liten för att rymma ett hus.")
    slump = np.random.default_rng(fro)

    # Cellmitt, rad 0 längst i norr.
    x_m = (np.arange(antal_celler) + 0.5) * cell_m
    y_m = sida_m - (np.arange(antal_celler) + 0.5) * cell_m
    xx, yy = np.meshgrid(x_m, y_m)
    terrang = terranghojd_m(xx, yy)

    antal_flackar = int(math.ceil(sida_m / _FLACK_M))
    flackar = slump.choice(np.array(_MARKSLAG_FLACKAR, dtype=np.uint8), (antal_flackar,) * 2)
    if okant_andel > 0.0:
        flackar[slump.random((antal_flackar,) * 2) < okant_andel] = 0
    markslag = flackar[
        np.minimum((yy / _FLACK_M).astype(np.int64), antal_flackar - 1),
        np.minimum((xx / _FLACK_M).astype(np.int64), antal_flackar - 1),
    ].astype(np.uint8)
    till_gata_x = np.abs(xx - np.round(xx / tomt_m) * tomt_m)
    till_gata_y = np.abs(yy - np.round(yy / tomt_m) * tomt_m)
    markslag[(till_gata_x < _GATUHALVBREDD_M) | (till_gata_y < _GATUHALVBREDD_M)] = 1

    byggnader: list[Byggnad] = []

    def _lagg_till(horn_x: Flyttal, horn_y: Flyttal) -> None:
        mark = float(np.max(terranghojd_m(horn_x, horn_y)))
        byggnader.append(
            Byggnad(
                id=f"hus{len(byggnader):04d}",
                e_m=URSPRUNG_E_M + horn_x,
                n_m=URSPRUNG_N_M + horn_y,
                tak_rh2000_m=mark + float(slump.uniform(6.0, 25.0)),
                reflektionsforlust_db=float(slump.choice([1.0, 2.0])),
            )
        )

    for i in range(antal_tomter):
        for j in range(antal_tomter):
            mx, my = (i + 0.5) * tomt_m, (j + 0.5) * tomt_m
            slag = float(slump.random())
            if slag < 0.1:
                continue
            if slag < 0.6:
                halv_b = radie_m * float(slump.uniform(0.4, 0.75))
                halv_d = math.sqrt(radie_m**2 - halv_b**2) * float(slump.uniform(0.5, 0.95))
                vinkel = float(slump.uniform(0.0, math.pi))
                lx = np.array([-halv_b, halv_b, halv_b, -halv_b])
                ly = np.array([-halv_d, -halv_d, halv_d, halv_d])
                _lagg_till(
                    mx + lx * math.cos(vinkel) - ly * math.sin(vinkel),
                    my + lx * math.sin(vinkel) + ly * math.cos(vinkel),
                )
            elif slag < 0.8:
                s = radie_m * 0.7
                _lagg_till(
                    mx + np.array([-s, s, s, 0.0, 0.0, -s]),
                    my + np.array([-s, -s, 0.0, 0.0, s, s]),
                )
            else:
                s = radie_m * 0.7
                # Två hus med gemensam vägg; det andra anges medurs för att pröva båda varven.
                _lagg_till(mx + np.array([-s, 0.0, 0.0, -s]), my + np.array([-s, -s, s, s]) / 2.0)
                _lagg_till(mx + np.array([0.0, 0.0, s, s]), my + np.array([-s, s, s, -s]) / 2.0)

    for byggnad in byggnader:
        polygon = shapely.Polygon(np.column_stack([byggnad.e_m, byggnad.n_m]))
        e0, n0, e1, n1 = polygon.bounds
        k0 = max(0, int((e0 - URSPRUNG_E_M) / cell_m))
        k1 = min(antal_celler, int((e1 - URSPRUNG_E_M) / cell_m) + 1)
        r0 = max(0, int((URSPRUNG_N_M + sida_m - n1) / cell_m))
        r1 = min(antal_celler, int((URSPRUNG_N_M + sida_m - n0) / cell_m) + 1)
        inne = shapely.contains_xy(
            polygon, URSPRUNG_E_M + xx[r0:r1, k0:k1], URSPRUNG_N_M + yy[r0:r1, k0:k1]
        )
        markslag[r0:r1, k0:k1][inne] = 9

    vagar: list[Vag] = []
    langs = np.linspace(5.0, sida_m - 5.0, antal_tomter + 1)
    for k in range(1, antal_tomter):
        lage = np.full(langs.shape[0], k * tomt_m)
        vagar.append(Vag(f"ov{k:02d}", URSPRUNG_E_M + langs, URSPRUNG_N_M + lage))
        vagar.append(Vag(f"ns{k:02d}", URSPRUNG_E_M + lage, URSPRUNG_N_M + langs))

    return Bullerscen(
        terrang_rh2000_m=terrang,
        markslag=markslag,
        e_min_m=URSPRUNG_E_M,
        n_max_m=URSPRUNG_N_M + sida_m,
        cell_m=cell_m,
        byggnader=tuple(byggnader),
        vagar=tuple(vagar),
    )
