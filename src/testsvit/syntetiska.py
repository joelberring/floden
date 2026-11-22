"""Deterministisk generator för syntetiska valideringsfall A1–A11 (uppdrag T09a; A10–A11 K2/v2)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw

from testsvit.k1_io import (
    PIXELSTORLEK_M,
    BrytlinjePost,
    OppningPost,
    skriv_brytlinjer_gpkg,
    skriv_geotiff,
    skriv_k1_recept,
    skriv_oppningar_gpkg,
    skriv_regn_csv,
)

# Stockholmskoordinater i SWEREF 99 18 00 (EPSG:3011)
BAS_E = 150000.0
BAS_N = 6576000.0

# Markslagskoder enligt kontrakt K1/v1
MARKSLAG_HARDGJORT = 1
MARKSLAG_GRAS = 4
MARKSLAG_GRUS = 7
MARKSLAG_BYGGNAD = 9


@dataclass(frozen=True)
class SyntetisktFallSpec:
    """Specifikation av ett komplett syntetiskt K1+K4+facit-fall."""

    mappnamn: str
    id: str
    namn: str
    beskrivning: str
    x_min: float
    y_max: float
    mark: NDArray[np.float32]
    osakerhet: NDArray[np.float32]
    markslag: NDArray[np.uint8]
    brytlinjer: list[BrytlinjePost]
    oppningar: list[OppningPost]
    regn: list[tuple[float, float]]
    facit: dict[str, Any]
    parametrar: dict[str, Any]
    tvarsnitt_rad: int | None = None


def _koordinatgrid(
    bredd: int,
    hojd: int,
    x_min: float,
    y_max: float,
    pixelstorlek: float = PIXELSTORLEK_M,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Returnerar cellcentras (x, y)-koordinater i EPSG:3011."""
    xs = x_min + (np.arange(bredd, dtype=np.float64) + 0.5) * pixelstorlek
    ys = y_max - (np.arange(hojd, dtype=np.float64) + 0.5) * pixelstorlek
    grid_x, grid_y = np.meshgrid(xs, ys)
    return grid_x, grid_y


def _fall_a1_enkel_sanka() -> SyntetisktFallSpec:
    # 60 x 60 celler = 15 m x 15 m vid 0,25 m upplösning.
    # Hela området sluttar svagt in mot sänkan eller är plant runt sänkan?
    # Enkel rektangulär plan botten (20x20 celler = 5 m x 5 m = 25 m^2)
    # omgiven av branta väggar upp till överfallskant på z = 10.50 m
    # med sänkbotten z = 10.00 m -> djup 0.50 m, area 25.0 m^2, volym 12.50 m^3.
    # Övrig kant runt hela 15x15 m avrinningsyta sluttar inåt sänkan (10.55..10.90 m)
    # utom en utloppssadel på östra kanten vid z = 10.50 m.
    # Avrinningsyta innan överfall = 15 * 15 = 225 m^2 (minus utloppsränna).
    # För helt exakt analytisk beräkning: slutna ytterkanter på z=10.80 m utom sadel vid
    # z=10.50 m -> hela området 15x15 m = 225 m^2 dränerar till sänkan!
    bredd, hojd = 60, 60
    x_min, y_max = BAS_E, BAS_N + hojd * PIXELSTORLEK_M

    # Basplatå z = 10.60 m som lutar svagt mot centrumsänkan, plus hög yttervall z = 10.90 m
    mark = np.full((hojd, bredd), 10.65, dtype=np.float32)
    mark[0, :] = 10.90
    mark[-1, :] = 10.90
    mark[:, 0] = 10.90
    mark[:, -1] = 10.90

    # Central rektangulär sänka: rader 20..40, kolumner 20..40 -> 20x20 celler = 5m x 5m = 25 m^2
    # Botten z = 10.00 m, lodrät kant till z = 10.65 m, sadel mot öster vid z = 10.50 m
    mark[20:40, 20:40] = 10.00
    # Överfallssadel från sänkan till östra randen (rader 28..32, kolumner 40..60) på z = 10.50 m
    mark[28:32, 40:60] = 10.50

    # Hela innanför yttervallen (58*58 = 3364 celler minus sadeln? Sadel har z=10.50 ut till randen,
    # så regn som faller på kolumn 40..59 rinner vid z=10.50 inte in i sänkan om den är plan,
    # men rinner ut när nivån når 10.50 m. Låt oss därför ha sadel bara på kolumn 40..44 vid z=10.50
    # och nedströms slänt z=10.40..10.20 för kolumn 44..60? Då rinner regn på kolumn 44..60 ut.)
    # Ännu renare analytiskt facit:
    # Sätt z = 10.65 runt hela 14x14 m inre bassäng (rader 2..58, kol 2..58),
    # sänkan 20..40 x 20..40 har botten 10.00 m,
    # och bara EN utloppscell i östra vallen (rad 30, kol 58..60) har z = 10.50 m,
    # medan resten av rad 30 har en svag lutning mot sänkan.
    # Gör alla ytor utom sänkan och yttervallen sluttande in mot sänkan:
    ii, jj = np.meshgrid(np.arange(hojd), np.arange(bredd), indexing="ij")
    dist = np.maximum(np.abs(ii - 29.5), np.abs(jj - 29.5))
    # Utanför sänkan (dist >= 10) sluttar terrängen inåt: 10.52 + 0.015 * (dist - 10)
    mark = (10.52 + 0.015 * np.maximum(dist - 10.0, 0.0)).astype(np.float32)
    mark[20:40, 20:40] = 10.00
    # Yttervall på 10.95 m runt om, utom överfallspunkt i öster (rader 28..32, kol 56..60)
    mark[0:2, :] = 10.95
    mark[-2:, :] = 10.95
    mark[:, 0:2] = 10.95
    mark[:, -2:] = 10.95
    # Överfallssadel vid östra kanten: z = 10.50 m vid kol 56..60 (rader 28..32)
    # och förbindelse dit genom vallen ligger över 10.50 m? Nej, överfallsnivån är minsta maxhöjden
    # längs vägen ut. Om vi sätter rader 28..32, kol 40..60 till 10.50 m så rinner regnet på
    # dessa 4x20 = 80 celler (5 m^2) ut direkt om lutningen är utåt, annars inåt.
    # Vi sätter rader 28..32, kol 40..57 till 10.500 m lutande svagt inåt? Nej:
    # Sätt sadelpunkten vid kol 57..60 (3 celler = 0.75 m lång utgång), höjd 10.50 m,
    # och hela övriga området (inklusive 40..57) sluttar in mot sänkan (10.50..10.80 m) utom att
    # rad 29..31 kol 40..57 har höjd 10.50 m endast vid kol 57, dvs 10.50 + 0.002*(57-j)?
    # Nej, då blir sänkan ändå 25 m^2 med överfallsnivå 10.50 m! Låt oss räkna exakt:
    # Om allt mellan rad 2..58, kol 2..58 sluttar in mot sänkan (minst 10.505 m), och
    # kol 57..60 rad 29..31 är sadeln = 10.500 m (och kol 58..60 är 10.45 m så den rinner ut),
    # då är avrinningsarean in till sänkan exakt 56 * 56 celler * 0.0625 m^2/cell = 196.0 m^2!
    mark = (10.505 + 0.01 * np.maximum(dist - 10.0, 0.0)).astype(np.float32)
    mark[20:40, 20:40] = 10.00
    mark[0:2, :] = 10.95
    mark[-2:, :] = 10.95
    mark[:, 0:2] = 10.95
    mark[:, -2:] = 10.95
    # Sadelns tröskel på kolumn 57 (z = 10.500 m) och utlopp på kolumn 58..60 (z = 10.48 m)
    # Vatten i sänkan når z = 10.500 m? För att vattnet ska nå kolumn 57 vid z = 10.500 m
    # måste det finnas en väg dit <= 10.500 m!
    # Så vi låter en 1 m bred ränna (rader 28..32) från sänkan (kol 40) till utloppet (kol 58)
    # ligga på z = 10.500 m vid kol 57 och 10.499..10.450 i sänkan/utåt?
    # Enklare och 100% entydigt:
    # Hela domänen 15x15 m = 60x60 celler har avrinningsyta = sänkan + sluttande plan runtom.
    # Gör sänkan som en rak skål upp till z = 10.50 m:
    # Sänkan (rader 10..50, kol 10..50 -> 40x40 = 1600 celler = 100.0 m^2) har botten 10.00 m.
    # Runt sänkan (kol/rad < 10 eller >= 50) är en vall på 10.80 m, utom vid östra tröskeln
    # (rader 28..32, kol 50..60) som är exakt 10.50 m (kol 50..52 = 10.50 m, kol 52..60 = 10.45 m).
    # Då är sänkans yta exakt 100.0 m^2, avrinningsytan som rinner in i sänkan är
    # (60*60 - 4*10) celler = 3560 * 0.0625 = 222.5 m^2 (eller om vi lägger tröskeln vid kol 58..60
    # med höjd 10.50 m och vall 10.80 m på bara sista rad/kolumn 0,59 så blir hela
    # 58*58 = 3364 celler = 210.25 m^2, minus tröskeln).
    # Allra renast: sänkan är en vertikalväggad bassäng på 100.0 m^2 (40x40 celler, z=10.00 m),
    # omgiven av 10.80 m mur runtom (hela randen 10 celler tjock) UTOM en 2 m bred överfallströskel
    # (rader 26..34, kol 50..60) på z = 10.50 m. Regn faller enbart eller över hela domänen?
    # I K4 är regn likformigt över hela domänen! På murkrönet (z=10.80 m) rinner regnet in i
    # bassängen om murkrönet sluttar inåt (10.80 på ytterkant -> 10.55 på innerkant)!
    # Och vid tröskeln (rader 28..32 = 4 celler = 1.0 m bred, kol 50..60 = 10 celler = 2.5 m)
    # rinner vatten över vid z = 10.50 m. Om kol 50..51 = 10.50 m och kol 51..60 = 10.40 m,
    # rinner regn på kol 51..60 (4*9 = 36 celler = 2.25 m^2) ut direkt, och resten
    # (3600 - 36 = 3564 celler = 222.75 m^2) rinner till sänkan!
    mark = np.full((hojd, bredd), 10.80, dtype=np.float32)
    # Sluttande krage runt bassängen så alt regn på kragen rinner inåt mot bassängen
    dist_kant = np.maximum(np.abs(ii - 29.5), np.abs(jj - 29.5))
    mark = (10.55 + 0.02 * np.maximum(dist_kant - 20.0, 0.0)).astype(np.float32)
    # Bassängen: 40x40 celler (10.0 m x 10.0 m = 100.0 m^2) på z = 10.00 m
    mark[10:50, 10:50] = 10.00
    # Tröskel vid rader 28..32 (1.0 m bred):
    # kol 50..52 (2 celler = 0.50 m) vid z = 10.50 m (överfallsnivå)
    # kol 52..60 (8 celler = 2.00 m) sluttar utåt från 10.49 m ned till 10.42 m
    mark[28:32, 50:52] = 10.50
    for j in range(52, 60):
        mark[28:32, j] = np.float32(10.49 - 0.01 * (j - 52))

    # Exakta analytiska storheter
    botten_z = 10.00
    overfall_z = 10.50
    sanka_area_m2 = 40 * 40 * (PIXELSTORLEK_M**2)  # 100.0 m^2
    max_djup_m = overfall_z - botten_z  # 0.50 m
    slutvolym_m3 = sanka_area_m2 * max_djup_m  # 50.0 m^3

    # Regn: 72 mm/h = 0.072 / 3600 = 2.0e-5 m/s konstant i 3600 s
    intensitet_mm_h = 72.0
    intensitet_m_s = intensitet_mm_h / 1000.0 / 3600.0
    # Avrinningsyta som rinner till sänkan:
    # Total yta (60*60) minus de 4*10=40 cellerna i och nedströms tröskeln (kol 50..59, rad 28..31)
    mark[28:32, 50] = 10.500
    for j in range(51, 60):
        mark[28:32, j] = np.float32(10.49 - 0.01 * (j - 51))

    avrinnings_celler = 60 * 60 - 4 * 10
    avrinningsyta_m2 = avrinnings_celler * (PIXELSTORLEK_M**2)  # 222.50 m^2
    inflode_m3_s = avrinningsyta_m2 * intensitet_m_s
    t_overfall_s = slutvolym_m3 / inflode_m3_s

    osakerhet = np.full((hojd, bredd), 0.02, dtype=np.float32)
    markslag = np.full((hojd, bredd), MARKSLAG_HARDGJORT, dtype=np.uint8)

    regn = [(0.0, intensitet_mm_h), (3600.0, intensitet_mm_h)]
    facit = {
        "fall_id": "A1",
        "typ": "analytisk",
        "botten_rh2000": botten_z,
        "overfall_rh2000": overfall_z,
        "sanka_area_m2": sanka_area_m2,
        "avrinningsyta_m2": avrinningsyta_m2,
        "slutvolym_m3": round(slutvolym_m3, 6),
        "max_djup_m": round(max_djup_m, 6),
        "intensitet_mm_h": intensitet_mm_h,
        "t_overfall_s": round(t_overfall_s, 3),
        "toleranser": {"volymfel_rel_max": 0.001, "maxdjupsfel_m_max": 0.01},
    }
    return SyntetisktFallSpec(
        mappnamn="A1_enkel_sanka",
        id="A1",
        namn="Enkel sänka, konstant regn",
        beskrivning="Skålformad bassäng med känd area (100 m²) och överfallsnivå (10,50 m).",
        x_min=x_min,
        y_max=y_max,
        mark=mark,
        osakerhet=osakerhet,
        markslag=markslag,
        brytlinjer=[],
        oppningar=[],
        regn=regn,
        facit=facit,
        parametrar={"fall": "A1", "sanka_area_m2": sanka_area_m2, "max_djup_m": max_djup_m},
        tvarsnitt_rad=30,
    )


def _fall_a2_tva_sankor_serie() -> SyntetisktFallSpec:
    # 100 bredd x 60 hojd = 25 m x 15 m vid 0,25 m upplösning.
    # Övre sänka (väster): botten 11.00 m, överfallssadel till nedre sänkan vid 11.30 m.
    # Nedre sänka (öster): botten 10.00 m, överfallssadel ut ur området vid 10.40 m.
    bredd, hojd = 100, 60
    x_min, y_max = BAS_E + 100.0, BAS_N + hojd * PIXELSTORLEK_M

    # Delavrinningsområden:
    # Västra halvan (kol 0..50): sluttar mot övre sänkan (rader 18..42, kol 10..34 = 24x24 = 36 m^2)
    # Östra halvan (kol 50..100): sluttar mot nedre sänkan (rader 14..46, kol 58..86 = 56 m^2)
    mark = np.full((hojd, bredd), 12.00, dtype=np.float32)

    ii, jj = np.meshgrid(np.arange(hojd), np.arange(bredd), indexing="ij")
    # Västra halvan (j < 50) sluttar mot centrum (30, 22)
    mask_v = jj < 50
    dist_v = np.maximum(np.abs(ii - 29.5), np.abs(jj - 21.5))
    mark[mask_v] = (11.35 + 0.015 * np.maximum(dist_v[mask_v] - 12.0, 0.0)).astype(np.float32)

    # Östra halvan (j >= 50) sluttar mot centrum (30, 72)
    mask_o = jj >= 50
    dist_o = np.maximum(np.abs(ii - 29.5), np.abs(jj - 71.5))
    mark[mask_o] = (10.45 + 0.015 * np.maximum(dist_o[mask_o] - 16.0, 0.0)).astype(np.float32)

    # Ytterkanter höga (12.00 m)
    mark[0:2, :] = 12.00
    mark[-2:, :] = 12.00
    mark[:, 0:2] = 12.00
    mark[:, -2:] = 12.00

    # Vattendelare mellan övre och nedre vid kol 48..52 (höjd 11.60 m utom vid sadel rader 28..32)
    mark[:, 48:52] = 11.60
    # Sadel från övre (kol 34) till nedre (kol 58):
    # kol 34..49 vid 11.30 m (överfallsnivå övre), kol 50..58 sluttar ned till 10.45 m
    mark[28:32, 34:50] = 11.30
    for j in range(50, 58):
        andel = (j - 49) / 9.0
        mark[28:32, j] = np.float32(11.30 - andel * 0.85)

    # Övre sänka: rader 18..42, kol 10..34 (24 x 24 celler = 6 m x 6 m = 36.0 m^2), z = 11.00 m
    mark[18:42, 10:34] = 11.00

    # Nedre sänka: rader 14..46, kol 58..86 (32 x 28 celler = 8 m x 7 m = 56.0 m^2), z = 10.00 m
    mark[14:46, 58:86] = 10.00

    # Yttre överfall från nedre sänkan (rader 28..32, kol 86..100):
    # kol 86..97: 10.40 m, kol 98..100: sluttar ut 10.39 -> 10.35 m
    mark[28:32, 86:98] = 10.40
    mark[28:32, 98] = 10.38
    mark[28:32, 99] = 10.35

    area_ovre = 24 * 24 * (PIXELSTORLEK_M**2)  # 36.0 m^2
    djup_ovre = 11.30 - 11.00  # 0.30 m
    volym_ovre = area_ovre * djup_ovre  # 10.80 m^3

    area_nedre = 32 * 28 * (PIXELSTORLEK_M**2)  # 56.0 m^2
    djup_nedre = 10.40 - 10.00  # 0.40 m
    volym_nedre = area_nedre * djup_nedre  # 22.40 m^3

    # Avrinningsytor: väster (kol 2..48) = 56*46 = 2576 celler = 161.0 m^2
    # öster (kol 50..98) = 56*48 = 2688 celler = 168.0 m^2
    avrinning_ovre_m2 = (56 * 46) * (PIXELSTORLEK_M**2)
    avrinning_nedre_egen_m2 = (56 * 48 - 4 * 14) * (PIXELSTORLEK_M**2)
    intensitet_mm_h = 72.0
    q_m_s = intensitet_mm_h / 1000.0 / 3600.0

    t_ovre = volym_ovre / (avrinning_ovre_m2 * q_m_s)
    # Nedre tar emot sitt eget regn från t=0, och övre sänkans tillrinning från t = t_ovre
    volym_nedre_vid_t_ovre = t_ovre * avrinning_nedre_egen_m2 * q_m_s
    if volym_nedre_vid_t_ovre >= volym_nedre:
        t_nedre = volym_nedre / (avrinning_nedre_egen_m2 * q_m_s)
    else:
        kvar = volym_nedre - volym_nedre_vid_t_ovre
        sammanlagt_inflode = (avrinning_ovre_m2 + avrinning_nedre_egen_m2) * q_m_s
        t_nedre = t_ovre + kvar / sammanlagt_inflode

    osakerhet = np.full((hojd, bredd), 0.02, dtype=np.float32)
    markslag = np.full((hojd, bredd), MARKSLAG_HARDGJORT, dtype=np.uint8)
    regn = [(0.0, intensitet_mm_h), (3600.0, intensitet_mm_h)]

    facit = {
        "fall_id": "A2",
        "typ": "analytisk",
        "overfallsordning": ["ovre", "nedre"],
        "ovre": {
            "botten_rh2000": 11.00,
            "overfall_rh2000": 11.30,
            "area_m2": area_ovre,
            "slutvolym_m3": round(volym_ovre, 6),
            "max_djup_m": round(djup_ovre, 6),
            "t_overfall_s": round(t_ovre, 3),
        },
        "nedre": {
            "botten_rh2000": 10.00,
            "overfall_rh2000": 10.40,
            "area_m2": area_nedre,
            "slutvolym_m3": round(volym_nedre, 6),
            "max_djup_m": round(djup_nedre, 6),
            "t_overfall_s": round(t_nedre, 3),
        },
        "intensitet_mm_h": intensitet_mm_h,
        "toleranser": {"volymfel_rel_max": 0.001, "maxdjupsfel_m_max": 0.01},
    }
    return SyntetisktFallSpec(
        mappnamn="A2_tva_sankor_serie",
        id="A2",
        namn="Två sänkor i serie",
        beskrivning=(
            "Övre sänka (11,00->11,30 m) rinner över sadel ned i nedre sänka (10,00->10,40 m)."
        ),
        x_min=x_min,
        y_max=y_max,
        mark=mark,
        osakerhet=osakerhet,
        markslag=markslag,
        brytlinjer=[],
        oppningar=[],
        regn=regn,
        facit=facit,
        parametrar={"fall": "A2", "overfallsordning": ["ovre", "nedre"]},
        tvarsnitt_rad=30,
    )


def _fall_a3_tva_sankor_sammanslagning() -> SyntetisktFallSpec:
    # 100 bredd x 60 hojd = 25 m x 15 m.
    # Två syskonsänkor åtskilda av en inre sadel vid z = 10.30 m,
    # omgivna av en gemensam yttre skål upp till yttre överfallströskel vid z = 10.60 m.
    bredd, hojd = 100, 60
    x_min, y_max = BAS_E + 200.0, BAS_N + hojd * PIXELSTORLEK_M

    mark = np.full((hojd, bredd), 10.85, dtype=np.float32)
    # Gemensam yttre bassäng: rader 10..50, kol 10..90 (40x80 celler = 200.0 m^2) på nivå 10.30 m
    mark[10:50, 10:90] = 10.30
    # Mellanvägg mellan sänkorna (kol 46..54) på 10.30 m (inre sadel!)
    # Vänster lövsänka: rader 18..42, kol 14..46 (24x32 = 768 celler = 48.0 m^2) på z = 10.00 m
    mark[18:42, 14:46] = 10.00
    # Höger lövsänka: rader 18..42, kol 54..86 (24x32 = 768 celler = 48.0 m^2) på z = 10.10 m
    mark[18:42, 54:86] = 10.10

    # Sluttande krage runt gemensamma bassängen så allt regn innanför ytterkant rinner in
    ii, jj = np.meshgrid(np.arange(hojd), np.arange(bredd), indexing="ij")
    ut_v = np.maximum(10 - jj, 0) + np.maximum(jj - 89, 0)
    ut_y = np.maximum(10 - ii, 0) + np.maximum(ii - 49, 0)
    utanfor = (ut_v > 0) | (ut_y > 0)
    dist = np.maximum(ut_v, ut_y)
    mark[utanfor] = (10.65 + 0.02 * dist[utanfor]).astype(np.float32)

    # Yttre överfallströskel i norra/östra delen (rader 28..32, kol 90..100) vid z = 10.60 m
    mark[28:32, 90:92] = 10.60
    for j in range(92, 100):
        mark[28:32, j] = np.float32(10.59 - 0.01 * (j - 92))

    # Volymer fram till inre sadel (z = 10.30 m):
    area_vanster = 24 * 32 * (PIXELSTORLEK_M**2)  # 48.0 m^2
    vol_vanster_sadel = area_vanster * (10.30 - 10.00)  # 14.40 m^3
    area_hoger = 24 * 32 * (PIXELSTORLEK_M**2)  # 48.0 m^2
    vol_hoger_sadel = area_hoger * (10.30 - 10.10)  # 9.60 m^3

    # Volym ovanför inre sadel (10.30 -> 10.60 m) över hela gemensamma bassängen (200 m^2)
    area_gemensam = 40 * 80 * (PIXELSTORLEK_M**2)  # 200.0 m^2
    vol_gemensam_ovre = area_gemensam * (10.60 - 10.30)  # 60.0 m^3
    total_slutvolym_m3 = vol_vanster_sadel + vol_hoger_sadel + vol_gemensam_ovre  # 84.0 m^3

    # Symmetrisk tillrinning till varje halva fram till sammanslagning:
    # Varje halva får ca halva avrinningsytan minus tröskeln:
    total_celler = 60 * 100 - 4 * 10  # 5960 celler = 372.5 m^2
    avrinningsyta_tot = total_celler * (PIXELSTORLEK_M**2)
    avrinningsyta_halva = avrinningsyta_tot / 2.0
    intensitet_mm_h = 72.0
    q_m_s = intensitet_mm_h / 1000.0 / 3600.0

    # Höger lövsänka når inre sadel först (t_hoger_sadel), därefter rinner överskottet till vänster,
    # tills båda når z = 10.30 m samtidigt vid t_sammanslagning = (V_v + V_h) / (A_tot * q):
    t_hoger_sadel_s = vol_hoger_sadel / (avrinningsyta_halva * q_m_s)
    t_sammanslagning_s = (vol_vanster_sadel + vol_hoger_sadel) / (avrinningsyta_tot * q_m_s)
    t_yttre_overfall_s = total_slutvolym_m3 / (avrinningsyta_tot * q_m_s)

    osakerhet = np.full((hojd, bredd), 0.02, dtype=np.float32)
    markslag = np.full((hojd, bredd), MARKSLAG_HARDGJORT, dtype=np.uint8)
    regn = [(0.0, intensitet_mm_h), (3600.0, intensitet_mm_h)]

    facit = {
        "fall_id": "A3",
        "typ": "analytisk",
        "inre_sadel_rh2000": 10.30,
        "gemensam_slutniva_rh2000": 10.60,
        "vanster_botten_rh2000": 10.00,
        "hoger_botten_rh2000": 10.10,
        "t_hoger_nar_sadel_s": round(t_hoger_sadel_s, 3),
        "t_sammanslagning_s": round(t_sammanslagning_s, 3),
        "t_yttre_overfall_s": round(t_yttre_overfall_s, 3),
        "slutvolym_m3": round(total_slutvolym_m3, 6),
        "max_djup_m": 0.60,
        "intensitet_mm_h": intensitet_mm_h,
        "toleranser": {"volymfel_rel_max": 0.001, "maxdjupsfel_m_max": 0.01},
    }
    return SyntetisktFallSpec(
        mappnamn="A3_tva_sankor_sammanslagning",
        id="A3",
        namn="Två sänkor som slås ihop",
        beskrivning="Syskonsänkor fylls först separat och slås ihop över sadel vid 10,30 m.",
        x_min=x_min,
        y_max=y_max,
        mark=mark,
        osakerhet=osakerhet,
        markslag=markslag,
        brytlinjer=[],
        oppningar=[],
        regn=regn,
        facit=facit,
        parametrar={"fall": "A3", "inre_sadel_rh2000": 10.30, "yttre_overfall_rh2000": 10.60},
        tvarsnitt_rad=30,
    )


def _fall_a4_lutande_plan() -> SyntetisktFallSpec:
    # 120 bredd x 40 hojd = 30.0 m x 10.0 m vid 0,25 m upplösning.
    # Rent lutande plan från väster till öster med 2,0 % lutning (S = 0.02).
    # Inga sänkor -> total lagrad volym i sänkor = 0.0 m^3.
    bredd, hojd = 120, 40
    x_min, y_max = BAS_E + 300.0, BAS_N + hojd * PIXELSTORLEK_M
    lutning = 0.02
    manning_n = 0.015  # slät asfalt
    ref_djup_m = 0.05  # 5 cm referensdjup för gångtid

    gx, _ = _koordinatgrid(bredd, hojd, x_min, y_max)
    rel_x = gx - x_min
    z_max = 12.00
    mark = (z_max - lutning * rel_x).astype(np.float32)

    flodeslangd_m = bredd * PIXELSTORLEK_M  # 30.0 m
    # Manning: v = (1/n) * h^(2/3) * S^(1/2)
    hastighet_m_s = (1.0 / manning_n) * (ref_djup_m ** (2.0 / 3.0)) * math.sqrt(lutning)
    gangtid_s = flodeslangd_m / hastighet_m_s

    osakerhet = np.full((hojd, bredd), 0.01, dtype=np.float32)
    markslag = np.full((hojd, bredd), MARKSLAG_HARDGJORT, dtype=np.uint8)
    regn = [(0.0, 50.0), (1800.0, 50.0)]

    facit = {
        "fall_id": "A4",
        "typ": "analytisk",
        "antal_sankor": 0,
        "lutning": lutning,
        "flodesriktning_deg": 90.0,
        "flodeslangd_m": flodeslangd_m,
        "manning_n": manning_n,
        "referensdjup_m": ref_djup_m,
        "hastighet_m_s": round(hastighet_m_s, 6),
        "gangtid_s": round(gangtid_s, 3),
        "slutvolym_m3": 0.0,
        "max_djup_m": 0.0,
        "toleranser": {"volymfel_rel_max": 0.001, "maxdjupsfel_m_max": 0.01},
    }
    return SyntetisktFallSpec(
        mappnamn="A4_lutande_plan",
        id="A4",
        namn="Lutande plan",
        beskrivning="Likformigt 2 % lutande asfaltplan (30 m) utan sänkor, Manning n=0,015.",
        x_min=x_min,
        y_max=y_max,
        mark=mark,
        osakerhet=osakerhet,
        markslag=markslag,
        brytlinjer=[],
        oppningar=[],
        regn=regn,
        facit=facit,
        parametrar={"fall": "A4", "lutning": lutning, "manning_n": manning_n},
        tvarsnitt_rad=20,
    )


def _fall_a5_gata_kantsten() -> SyntetisktFallSpec:
    # 80 bredd x 60 hojd = 20 m x 15 m vid 0,25 m upplösning.
    # Tvärsektion längs y (norr->söder):
    # - Rader 0..16 (4 m norr): gångbana som lutar svagt mot gatan (10.16..10.12 m)
    # - Rader 16..44 (7 m): krönt körbana med ränndal vid rad 16 och rad 43 (10.00 m),
    #   mittkrön vid rad 30 på z = 10.08 m (tvärfall ~2,3 %)
    # - Rader 44..60 (4 m söder): gångbana/gård bortom kantsten, z = 9.98..9.90 m (lägre bakom!)
    # Kantsten i `brytlinjer.gpkg` med höjd 0,12 m längs rad 16 och rad 44!
    # Vatten i södra rännstenen (z_botten=10.00 m) hålls kvar av 0.12 m kantsten upp till z=10.12 m,
    # innan det svämmar över ut i den lägre södra gården (z=9.95 m).
    bredd, hojd = 80, 60
    x_min, y_max = BAS_E + 400.0, BAS_N + hojd * PIXELSTORLEK_M

    mark = np.zeros((hojd, bredd), dtype=np.float32)
    # Norra gångbanan (rader 0..16)
    for i in range(0, 16):
        mark[i, :] = np.float32(10.20 - 0.005 * i)
    # Körbana (rader 16..44): rännsten 10.00 vid 16 och 43, krön 10.08 vid 29..30
    for i in range(16, 44):
        avst_kron = abs(i - 29.5)
        mark[i, :] = np.float32(10.08 - (avst_kron / 13.5) * 0.08)
    # Södra sidan (rader 44..60): ligger lägre utanför gatan (9.95 m) så om kantstenen saknades
    # skulle vattnet rinna rakt söderut!
    for i in range(44, 60):
        mark[i, :] = np.float32(9.98 - 0.005 * (i - 44))

    # Gatan stängd i väst/öst vid z=10.20 m (rad 16..44) så den fungerar som kvarhållen ränna
    mark[16:44, 0:2] = 10.20
    mark[16:44, -2:] = 10.20

    # Kantsten som brytlinjer i K1 (längs y-gränsen mellan körbana och gångbana)
    y_norr_kant = y_max - 16 * PIXELSTORLEK_M
    y_soder_kant = y_max - 44 * PIXELSTORLEK_M
    x_slut = x_min + bredd * PIXELSTORLEK_M - 0.5
    brytlinjer = [
        BrytlinjePost(
            typ="kantsten",
            hojd_m=0.12,
            koordinater=((x_min + 0.5, y_norr_kant), (x_slut, y_norr_kant)),
        ),
        BrytlinjePost(
            typ="kantsten",
            hojd_m=0.12,
            koordinater=((x_min + 0.5, y_soder_kant), (x_slut, y_soder_kant)),
        ),
    ]

    osakerhet = np.full((hojd, bredd), 0.015, dtype=np.float32)
    markslag = np.full((hojd, bredd), MARKSLAG_HARDGJORT, dtype=np.uint8)
    markslag[46:60, :] = MARKSLAG_GRAS

    # Måttligt regn (15 mm/h i 1800 s = 7.5 mm) stannar helt i rännstenen (< 12 cm djup)
    regn = [(0.0, 15.0), (1800.0, 15.0)]
    facit = {
        "fall_id": "A5",
        "typ": "kantstensfall",
        "rannsten_botten_rh2000": 10.00,
        "kantsten_hojd_m": 0.12,
        "kantsten_overfall_rh2000": 10.12,
        "forvantat_beteende": (
            "Vatten stannar i rännstenen norr om södra kantstenen (djup < 0,12 m)."
        ),
        "vatten_pa_sodra_gard_m3": 0.0,
        "max_djup_rannsten_m": 0.06,
        "toleranser": {"volymfel_rel_max": 0.001, "maxdjupsfel_m_max": 0.01},
    }
    return SyntetisktFallSpec(
        mappnamn="A5_gata_kantsten",
        id="A5",
        namn="Gata med kantsten och rännsten",
        beskrivning="Krönt gata med 12 cm kantsten i brytlinjer.gpkg; vatten hålls i rännstenen.",
        x_min=x_min,
        y_max=y_max,
        mark=mark,
        osakerhet=osakerhet,
        markslag=markslag,
        brytlinjer=brytlinjer,
        oppningar=[],
        regn=regn,
        facit=facit,
        parametrar={"fall": "A5", "kantsten_hojd_m": 0.12},
        tvarsnitt_rad=None,
    )


def _fall_a6_sankt_kantsten_infart() -> SyntetisktFallSpec:
    # Samma gatugeometri som A5, men med en 3,0 m bred sänkt kantsten (infart, höjd 0,02 m)
    # vid x = x_min + 8.5..11.5 m (kolumner 34..46) längs södra kantstenen.
    # Större regn (60 mm/h i 1800 s = 30 mm) gör att vattennivån når över 10,02 m men
    # stannar under 10,12 m -> allt överskott lämnar gatan exakt via infarten!
    a5 = _fall_a5_gata_kantsten()
    bredd, hojd = 80, 60
    x_min = BAS_E + 500.0
    y_max = BAS_N + hojd * PIXELSTORLEK_M
    mark = a5.mark.copy()

    y_norr_kant = y_max - 16 * PIXELSTORLEK_M
    y_soder_kant = y_max - 44 * PIXELSTORLEK_M
    x_slut = x_min + bredd * PIXELSTORLEK_M - 0.5
    infart_x0 = x_min + 8.5
    infart_x1 = x_min + 11.5

    brytlinjer = [
        BrytlinjePost(
            typ="kantsten",
            hojd_m=0.12,
            koordinater=((x_min + 0.5, y_norr_kant), (x_slut, y_norr_kant)),
        ),
        BrytlinjePost(
            typ="kantsten",
            hojd_m=0.12,
            koordinater=((x_min + 0.5, y_soder_kant), (infart_x0, y_soder_kant)),
        ),
        BrytlinjePost(
            typ="kantsten",
            hojd_m=0.02,  # Sänkt kantsten vid infart
            koordinater=((infart_x0, y_soder_kant), (infart_x1, y_soder_kant)),
        ),
        BrytlinjePost(
            typ="kantsten",
            hojd_m=0.12,
            koordinater=((infart_x1, y_soder_kant), (x_slut, y_soder_kant)),
        ),
    ]

    regn = [(0.0, 60.0), (1800.0, 60.0)]
    facit = {
        "fall_id": "A6",
        "typ": "kantstensfall",
        "infart_x_intervall": [infart_x0, infart_x1],
        "infart_bredd_m": 3.0,
        "kantsten_normal_hojd_m": 0.12,
        "kantsten_infart_hojd_m": 0.02,
        "utlopp_rh2000": 10.02,
        "forvantat_beteende": "Vatten lämnar gatan exakt genom den 3 m breda sänkta infarten.",
        "toleranser": {"volymfel_rel_max": 0.001, "maxdjupsfel_m_max": 0.01},
    }
    return SyntetisktFallSpec(
        mappnamn="A6_sankt_kantsten_infart",
        id="A6",
        namn="Sänkt kantsten vid infart",
        beskrivning="Gata med 3 m sänkt kantsten (2 cm) vid infart; utflöde sker enbart där.",
        x_min=x_min,
        y_max=y_max,
        mark=mark,
        osakerhet=a5.osakerhet.copy(),
        markslag=a5.markslag.copy(),
        brytlinjer=brytlinjer,
        oppningar=[],
        regn=regn,
        facit=facit,
        parametrar={"fall": "A6", "infart_bredd_m": 3.0, "infart_kantsten_m": 0.02},
        tvarsnitt_rad=40,
    )


def _fall_a7_vagbank_trumma() -> SyntetisktFallSpec:
    # 100 bredd x 60 hojd = 25 m x 15 m vid 0,25 m upplösning.
    # Dalgång som sluttar från väster (11.50 m) till öster (10.00 m), med V-tvärsnitt mot rad 30.
    # Vägbank korsar dalgången i kolumner 44..56 (3 m bred bank) med krön z = 11.80 m.
    # Utan trumma skapas en falsk damm uppströms vägbanken upp till z = 11.80 m.
    # Med trumma i `oppningar.gpkg` (botten 10.45 m, bredd 1.0 m) passerar vattnet banken.
    bredd, hojd = 100, 60
    x_min, y_max = BAS_E + 600.0, BAS_N + hojd * PIXELSTORLEK_M

    ii, jj = np.meshgrid(np.arange(hojd), np.arange(bredd), indexing="ij")
    dalbotten = 11.20 - 0.012 * jj  # 11.20 m i väst -> 10.01 m i öst
    sidolutning = 0.04 * np.abs(ii - 29.5)  # dalväggar upp till +1.18 m på sidorna
    mark = (dalbotten + sidolutning).astype(np.float32)

    # Vägbank vid kolumner 44..56: vägkrön 11.80 m tvärs över dalen
    mark[:, 44:56] = np.maximum(mark[:, 44:56], 11.80).astype(np.float32)

    trumma_y = y_max - 29.5 * PIXELSTORLEK_M
    trumma_x0 = x_min + 43.5 * PIXELSTORLEK_M
    trumma_x1 = x_min + 56.5 * PIXELSTORLEK_M
    botten_trumma = float(dalbotten[30, 50])  # ~10.60 m

    oppningar = [
        OppningPost(
            typ="trumma",
            bredd_m=1.0,
            botten_rh2000=round(botten_trumma, 3),
            kalla="syntetisk-trumma-a7",
            koordinater=((trumma_x0, trumma_y), (trumma_x1, trumma_y)),
        )
    ]

    osakerhet = np.full((hojd, bredd), 0.02, dtype=np.float32)
    markslag = np.full((hojd, bredd), MARKSLAG_GRAS, dtype=np.uint8)
    markslag[:, 44:56] = MARKSLAG_HARDGJORT
    regn = [(0.0, 50.0), (1800.0, 50.0)]

    facit = {
        "fall_id": "A7",
        "typ": "oppningsfall",
        "vagbank_kron_rh2000": 11.80,
        "trumma_botten_rh2000": round(botten_trumma, 3),
        "trumma_bredd_m": 1.0,
        "med_trumma": {
            "falsk_damm": False,
            "max_uppstroms_vattenniva_rh2000": round(botten_trumma + 0.10, 3),
        },
        "utan_trumma": {
            "falsk_damm": True,
            "uppdamd_niva_rh2000": 11.80,
        },
        "toleranser": {"volymfel_rel_max": 0.001, "maxdjupsfel_m_max": 0.01},
    }
    return SyntetisktFallSpec(
        mappnamn="A7_vagbank_trumma",
        id="A7",
        namn="Vägbank med och utan trumma",
        beskrivning="Sluttande dalgång korsad av vägbank (11,80 m) med trumma i oppningar.gpkg.",
        x_min=x_min,
        y_max=y_max,
        mark=mark,
        osakerhet=osakerhet,
        markslag=markslag,
        brytlinjer=[],
        oppningar=oppningar,
        regn=regn,
        facit=facit,
        parametrar={"fall": "A7", "vagbank_kron_rh2000": 11.80, "trumma_bredd_m": 1.0},
        tvarsnitt_rad=30,
    )


def _fall_a8_port_byggnad() -> SyntetisktFallSpec:
    # 80 bredd x 60 hojd = 20 m x 15 m vid 0,25 m upplösning.
    # Sluttande innergård från väst (11.00 m) mot gata i öst (10.20 m).
    # En byggnad (markslag 9, +10 m hinder = 20.60 m) blockerar hela y-bredden på kolumner 40..56,
    # men har en körport i `oppningar.gpkg` (bredd 3,0 m, botten på gårdsnivå) i mitten.
    bredd, hojd = 80, 60
    x_min, y_max = BAS_E + 700.0, BAS_N + hojd * PIXELSTORLEK_M

    _, jj = np.meshgrid(np.arange(hojd), np.arange(bredd), indexing="ij")
    mark_underlag = (11.00 - 0.01 * jj).astype(np.float32)  # 11.00 -> 10.21 m
    mark = mark_underlag.copy()

    # Byggnad som hinder längs hela y-axeln (rader 0..60, kolumner 40..56): +10 m hinderhöjd
    mark[:, 40:56] = mark_underlag[:, 40:56] + np.float32(10.0)

    port_y = y_max - 29.5 * PIXELSTORLEK_M
    port_x0 = x_min + 39.5 * PIXELSTORLEK_M
    port_x1 = x_min + 56.5 * PIXELSTORLEK_M
    port_botten = float(mark_underlag[30, 48])  # ~10.52 m

    oppningar = [
        OppningPost(
            typ="port",
            bredd_m=3.0,
            botten_rh2000=round(port_botten, 3),
            kalla="syntetisk-port-a8",
            koordinater=((port_x0, port_y), (port_x1, port_y)),
        )
    ]

    osakerhet = np.full((hojd, bredd), 0.02, dtype=np.float32)
    markslag = np.full((hojd, bredd), MARKSLAG_HARDGJORT, dtype=np.uint8)
    markslag[:, 40:56] = MARKSLAG_BYGGNAD
    regn = [(0.0, 50.0), (1800.0, 50.0)]

    facit = {
        "fall_id": "A8",
        "typ": "oppningsfall",
        "byggnad_hinderhojd_m": 10.0,
        "port_bredd_m": 3.0,
        "port_botten_rh2000": round(port_botten, 3),
        "forvantat_beteende": "Vatten passerar genom byggnadens port i stället för att dämmas.",
        "falsk_uppdamning_med_port": False,
        "toleranser": {"volymfel_rel_max": 0.001, "maxdjupsfel_m_max": 0.01},
    }
    return SyntetisktFallSpec(
        mappnamn="A8_port_byggnad",
        id="A8",
        namn="Port genom byggnad",
        beskrivning=(
            "Sluttande gård med byggnadshinder (markslag 9, +10 m) och 3 m port i oppningar."
        ),
        x_min=x_min,
        y_max=y_max,
        mark=mark,
        osakerhet=osakerhet,
        markslag=markslag,
        brytlinjer=[],
        oppningar=oppningar,
        regn=regn,
        facit=facit,
        parametrar={"fall": "A8", "port_bredd_m": 3.0, "byggnad_hinderhojd_m": 10.0},
        tvarsnitt_rad=30,
    )


def _fall_a9_brus_persistens() -> SyntetisktFallSpec:
    # 80 bredd x 80 hojd = 20 m x 20 m vid 0,25 m upplösning.
    # Ett sluttande plan med EN verklig sänka (djup ~0,50 m, osäkerhet 0,03 m)
    # plus deterministiskt litet terrängbrus (amplitud 0,02 m, alltså max ~0,04 m topp-till-dal).
    # Persistensfiltret ska ta bort brussänkorna (< osäkerhet) och behålla exakt 1 verklig sänka.
    bredd, hojd = 80, 80
    x_min, y_max = BAS_E + 800.0, BAS_N + hojd * PIXELSTORLEK_M

    ii, jj = np.meshgrid(np.arange(hojd), np.arange(bredd), indexing="ij")
    # Grundlutning mot öster (10.80 m -> 10.20 m)
    basplan = 10.80 - 0.0075 * jj

    # Deterministiskt sinus/cosinus-brus + fast RNG-brus på +-0.02 m
    rng = np.random.default_rng(42)
    brus = 0.015 * np.sin(0.55 * ii) * np.cos(0.55 * jj) + rng.uniform(-0.005, 0.005, (hojd, bredd))

    # Verklig sänka centrerad vid (40, 40) med radie 10 celler (2.5 m) och djup 0.50 m
    r2 = ((ii - 40.0) ** 2 + (jj - 40.0) ** 2) / (8.0**2)
    verklig_sanka = -0.50 * np.exp(-r2)

    mark = (basplan + brus + verklig_sanka).astype(np.float32)
    osakerhet = np.full((hojd, bredd), 0.03, dtype=np.float32)
    markslag = np.full((hojd, bredd), MARKSLAG_GRUS, dtype=np.uint8)
    regn = [(0.0, 50.0), (1800.0, 50.0)]

    botten_min = float(np.min(mark[32:48, 32:48]))
    facit = {
        "fall_id": "A9",
        "typ": "persistensfall",
        "antal_verkliga_sankor": 1,
        "verklig_sanka_djup_m": 0.50,
        "verklig_sanka_botten_rh2000": round(botten_min, 4),
        "brus_amplitud_m": 0.02,
        "osakerhet_m": 0.03,
        "slumpfro": 42,
        "forvantat_beteende": "Persistensfiltret behåller exakt 1 verklig sänka och rensar brus.",
        "toleranser": {"volymfel_rel_max": 0.001, "maxdjupsfel_m_max": 0.01},
    }
    return SyntetisktFallSpec(
        mappnamn="A9_brus_persistens",
        id="A9",
        namn="Brus i terrängen",
        beskrivning="Sluttande plan med 0,50 m verklig sänka och +-0,02 m deterministiskt brus.",
        x_min=x_min,
        y_max=y_max,
        mark=mark,
        osakerhet=osakerhet,
        markslag=markslag,
        brytlinjer=[],
        oppningar=[],
        regn=regn,
        facit=facit,
        parametrar={"fall": "A9", "brus_amplitud_m": 0.02, "slumpfro": 42},
        tvarsnitt_rad=40,
    )


def _sluten_gard_terrang() -> tuple[NDArray[np.float32], NDArray[np.uint8], float, float]:
    """Sluten kvadratisk gård 10 x 10 m (40 x 40 celler) i ett kvarter 18 x 18 m.

    88 x 88 celler = 22 x 22 m vid 0,25 m. Gården (rad/kolumn 24..63) har plant golv på
    10,00 m och omges helt av byggnader 4 m breda (markslag 9, rad/kolumn 8..79), med taket på
    22,00 m utom norra längan (rad 8..23) på 16,00 m: hindret. Gatan runt kvarteret (2 m) lutar
    2 % utåt från 9,95 m, så att vatten där lämnar området.
    """
    n = 88
    x_min, y_max = BAS_E + 1000.0, BAS_N + n * PIXELSTORLEK_M
    ii, jj = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
    avst_celler = np.minimum(np.minimum(ii, jj), np.minimum(n - 1 - ii, n - 1 - jj))
    # Gatan: 9,95 m vid huset, 2 % lutning utåt (avstånd i meter från husliv till cellens mitt).
    mark = (9.95 - 0.02 * (8 - avst_celler - 0.5) * PIXELSTORLEK_M).astype(np.float32)
    markslag = np.full((n, n), MARKSLAG_HARDGJORT, dtype=np.uint8)
    kvarter = (avst_celler >= 8)
    gard = (avst_celler >= 24)
    hus = kvarter & ~gard
    markslag[hus] = MARKSLAG_BYGGNAD
    mark[hus] = np.float32(22.0)
    mark[8:24, 8:80] = np.float32(16.0)  # norra längan: lägre tak, gårdens hinder
    mark[gard] = np.float32(10.0)
    return mark, markslag, x_min, y_max


def _fall_a10_sluten_gard() -> SyntetisktFallSpec:
    mark, markslag, x_min, y_max = _sluten_gard_terrang()
    n = mark.shape[0]
    osakerhet = np.full((n, n), 0.02, dtype=np.float32)
    regn = [(0.0, 300.0), (600.0, 300.0)]  # 300 mm/h i 10 min = 50 mm (kort, för 2D-jämförelsen)
    gard_area_m2 = 40 * 40 * PIXELSTORLEK_M**2
    facit = {
        "fall_id": "A10",
        "typ": "slutsanka",
        "gard_area_m2": gard_area_m2,
        "gard_celler": 1600,
        "regn_mm": 50.0,
        "regn_pa_tak_raknas": False,
        "volym_m3": gard_area_m2 * 0.050,
        "djup_m": 0.050,
        "hinder_rh2000": 16.0,
        "forklaring": (
            "Gården har ingen väg ut: allt regn på gården (taken räknas inte, regn på "
            "byggnadsceller ingår inte i modellen) blir stående. Djup = regnvolym / area."
        ),
        "toleranser": {"volymfel_rel_max": 0.001, "maxdjupsfel_m_max": 0.01},
    }
    return SyntetisktFallSpec(
        mappnamn="A10_sluten_gard",
        id="A10",
        namn="Sluten gård",
        beskrivning=(
            "Gård 10 x 10 m helt omgiven av byggnader (lägsta tak 16 m): slutsänka, djup = regn."
        ),
        x_min=x_min,
        y_max=y_max,
        mark=mark,
        osakerhet=osakerhet,
        markslag=markslag,
        brytlinjer=[],
        oppningar=[],
        regn=regn,
        facit=facit,
        parametrar={"fall": "A10", "gard_m": 10.0, "hinder_rh2000": 16.0},
        tvarsnitt_rad=44,
    )


def _fall_a11_sluten_gard_avtappning() -> SyntetisktFallSpec:
    mark, markslag, x_min, y_max = _sluten_gard_terrang()
    n = mark.shape[0]
    osakerhet = np.full((n, n), 0.02, dtype=np.float32)
    regn = [(0.0, 120.0), (900.0, 120.0)]  # 120 mm/h i 15 min = 30 mm
    # Avtappning 60 mm/h: netto +60 mm/h i 15 min (15 mm), sedan -60 mm/h: tomt efter 30 min.
    tider = [0.0, 450.0, 900.0, 1350.0, 1800.0, 2700.0]
    djup = [0.0, 0.0075, 0.015, 0.0075, 0.0, 0.0]
    facit = {
        "fall_id": "A11",
        "typ": "slutsanka_avtappning",
        "gard_area_m2": 40 * 40 * PIXELSTORLEK_M**2,
        "avtappning_mm_h": 60.0,
        "hinder_rh2000": 16.0,
        "slut_tid_s": 2700.0,
        "tider_s": tider,
        "djup_m": djup,
        "max_djup_m": 0.015,
        "tom_efter_s": 1800.0,
        "avtappat_m3": 100.0 * 0.030,
        "forklaring": (
            "Som A10 men gården tappas av med 60 mm/h (fas 3): djup(t) = (120 - 60) t under "
            "regnet, sedan minskar det med 60 mm/h till tomt."
        ),
        "toleranser": {"volymfel_rel_max": 0.001, "maxdjupsfel_m_max": 0.01},
    }
    return SyntetisktFallSpec(
        mappnamn="A11_sluten_gard_avtappning",
        id="A11",
        namn="Sluten gård med avtappning",
        beskrivning=(
            "Som A10 med avtappning 60 mm/h och 120 mm/h i 15 min: känt förlopp, tomt efter 30 min."
        ),
        x_min=x_min,
        y_max=y_max,
        mark=mark,
        osakerhet=osakerhet,
        markslag=markslag,
        brytlinjer=[],
        oppningar=[],
        regn=regn,
        facit=facit,
        parametrar={"fall": "A11", "gard_m": 10.0, "avtappning_mm_h": 60.0},
        tvarsnitt_rad=44,
    )


def _rita_oversiktsbild(spec: SyntetisktFallSpec, sokvag: Path) -> Path:
    """Ritar en kompakt och tydlig PNG med höjdkarta och tvärsnittsprofil (NumPy + Pillow)."""
    mark = spec.mark.astype(np.float64)
    hojd, bredd = mark.shape

    # Klipp extrema byggnadshöjder för bättre kontrast om det finns byggnad (+10 m)
    p2, p98 = np.percentile(mark, [2, 98])
    if p98 - p2 < 1e-6:
        p98 = p2 + 1.0
    norm = np.clip((mark - p2) / (p98 - p2), 0.0, 1.0)

    # Enkel topografisk färgskala: blågrön (lågt) -> sand/grön -> brun/vit (högt)
    r = np.clip(40 + 205 * (norm**1.1), 0, 255).astype(np.uint8)
    g = np.clip(90 + 145 * np.sin(norm * math.pi * 0.85), 0, 255).astype(np.uint8)
    b = np.clip(180 * (1.0 - norm) + 40, 0, 255).astype(np.uint8)

    # Markera byggnadsceller (markslag == 9) i mörkgrått
    bygg_mask = spec.markslag == MARKSLAG_BYGGNAD
    r[bygg_mask] = 70
    g[bygg_mask] = 75
    b[bygg_mask] = 85

    rgb = np.stack([r, g, b], axis=-1)
    karta_img = Image.fromarray(rgb, mode="RGB").resize(
        (320, max(160, int(round(320 * hojd / bredd)))),
        resample=Image.Resampling.NEAREST,
    )

    karta_w, karta_h = karta_img.size
    profil_h = 120
    marginal = 16
    titel_h = 34
    canvas_w = karta_w + 2 * marginal
    canvas_h = titel_h + karta_h + profil_h + 3 * marginal

    canvas = Image.new("RGB", (canvas_w, canvas_h), (248, 249, 250))
    draw = ImageDraw.Draw(canvas)

    draw.text((marginal, 10), f"{spec.id}: {spec.namn} ({bredd}x{hojd} @ 0.25m)", fill=(20, 25, 30))
    canvas.paste(karta_img, (marginal, titel_h))
    draw.rectangle(
        [marginal - 1, titel_h - 1, marginal + karta_w, titel_h + karta_h],
        outline=(60, 65, 70),
    )

    # Rita tvärsnittsprofil
    profil_rad = spec.tvarsnitt_rad if spec.tvarsnitt_rad is not None else hojd // 2
    profil = mark[profil_rad, :]
    y_karta_rad = titel_h + int(round((profil_rad + 0.5) / hojd * karta_h))
    draw.line(
        [(marginal, y_karta_rad), (marginal + karta_w, y_karta_rad)],
        fill=(220, 40, 40),
        width=1,
    )

    py0 = titel_h + karta_h + marginal
    py1 = py0 + profil_h
    draw.rectangle(
        [marginal, py0, marginal + karta_w, py1],
        fill=(255, 255, 255),
        outline=(140, 145, 150),
    )

    z_min = float(np.min(profil))
    z_max = float(np.max(profil))
    spann = max(z_max - z_min, 0.05)
    punkter: list[tuple[int, int]] = []
    for idx in range(bredd):
        px = marginal + int(round(idx / max(bredd - 1, 1) * (karta_w - 1)))
        py = py1 - 12 - int(round((float(profil[idx]) - z_min) / spann * (profil_h - 26)))
        punkter.append((px, py))
    if len(punkter) >= 2:
        draw.line(punkter, fill=(25, 95, 180), width=2)

    draw.text(
        (marginal + 4, py0 + 3),
        f"Profil rad {profil_rad}: min {z_min:.2f} m, max {z_max:.2f} m",
        fill=(40, 45, 50),
    )

    sokvag.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(sokvag, format="PNG", optimize=True)
    return sokvag


def alla_syntetiska_specifikationer() -> list[SyntetisktFallSpec]:
    """Returnerar specifikationerna för alla syntetiska fall A1–A11 i ordning."""
    return [
        _fall_a1_enkel_sanka(),
        _fall_a2_tva_sankor_serie(),
        _fall_a3_tva_sankor_sammanslagning(),
        _fall_a4_lutande_plan(),
        _fall_a5_gata_kantsten(),
        _fall_a6_sankt_kantsten_infart(),
        _fall_a7_vagbank_trumma(),
        _fall_a8_port_byggnad(),
        _fall_a9_brus_persistens(),
        _fall_a10_sluten_gard(),
        _fall_a11_sluten_gard_avtappning(),
    ]


def skriv_syntetiskt_fall(spec: SyntetisktFallSpec, rot: str | Path = "testsvit") -> Path:
    """Skriver ett komplett syntetiskt testfall (`fall.toml`, `indata/*`, `facit/*`)."""
    fall_mapp = Path(rot) / spec.mappnamn
    indata_mapp = fall_mapp / "indata"
    facit_mapp = fall_mapp / "facit"
    indata_mapp.mkdir(parents=True, exist_ok=True)
    facit_mapp.mkdir(parents=True, exist_ok=True)

    hojd, bredd = spec.mark.shape
    bbox = (
        spec.x_min,
        spec.y_max - hojd * PIXELSTORLEK_M,
        spec.x_min + bredd * PIXELSTORLEK_M,
        spec.y_max,
    )

    mark_fil = skriv_geotiff(
        indata_mapp / "mark.tif", spec.mark, x_min=spec.x_min, y_max=spec.y_max
    )
    osakerhet_fil = skriv_geotiff(
        indata_mapp / "osakerhet.tif", spec.osakerhet, x_min=spec.x_min, y_max=spec.y_max
    )
    markslag_fil = skriv_geotiff(
        indata_mapp / "markslag.tif", spec.markslag, x_min=spec.x_min, y_max=spec.y_max
    )
    brytlinjer_fil = skriv_brytlinjer_gpkg(
        indata_mapp / "brytlinjer.gpkg", spec.brytlinjer, bbox_fallback=bbox
    )
    oppningar_fil = skriv_oppningar_gpkg(
        indata_mapp / "oppningar.gpkg", spec.oppningar, bbox_fallback=bbox
    )
    regn_fil = skriv_regn_csv(indata_mapp / "regn.csv", spec.regn)

    ruta_id = f"mm_{int(spec.x_min)}_{int(spec.y_max - hojd * PIXELSTORLEK_M)}"
    skriv_k1_recept(
        indata_mapp / "recept.json",
        ruta_id=ruta_id,
        filer_for_checksum={
            "mark": mark_fil,
            "osakerhet": osakerhet_fil,
            "markslag": markslag_fil,
            "brytlinjer": brytlinjer_fil,
            "oppningar": oppningar_fil,
            "regn": regn_fil,
        },
        parametrar=spec.parametrar,
    )

    facit_fil = facit_mapp / "facit.json"
    facit_fil.write_text(
        json.dumps(spec.facit, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _rita_oversiktsbild(spec, facit_mapp / "oversikt.png")

    toml_innehall = (
        f'id = "{spec.id}"\n'
        f'namn = "{spec.namn}"\n'
        'grupp = "A"\n'
        'typ = "syntetisk"\n'
        f'beskrivning = "{spec.beskrivning}"\n'
        'status = "redo"\n'
    )
    (fall_mapp / "fall.toml").write_text(toml_innehall, encoding="utf-8")
    return fall_mapp


def generera_alla_syntetiska_fall(rot: str | Path = "testsvit") -> list[Path]:
    """Genererar deterministiskt alla syntetiska testfall A1–A11 under `rot`."""
    mappar: list[Path] = []
    for spec in alla_syntetiska_specifikationer():
        mappar.append(skriv_syntetiskt_fall(spec, rot=rot))
    return mappar


if __name__ == "__main__":
    skapade = generera_alla_syntetiska_fall("testsvit")
    for mapp in skapade:
        print(f"Skapade {mapp}")
