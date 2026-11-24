"""Tillämpning av K3-projektlapp på K1-markmodellraster utan att ändra rådata (T04)."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from gemensamt.hash import sha256_bytes
from lapp.kontrakt import (
    Gatusektion,
    LappFel,
    LappGata,
    ProjektLapp,
    las_gatusektioner_fran_konfig,
)
from lapp.landxml import las_landxml_tin, rastrera_landxml_tin
from sankgraf.typer import MARKSLAG_BYGGNAD
from testsvit.k1_io import BrytlinjePost, GeoTransformInfo, OppningPost

_KANT_TOL_M = 1e-9


@dataclass(frozen=True)
class RegistreradEntre:
    """Entré registrerad från en byggnad i projektlappen (SWEREF 99 18 00, RH 2000)."""

    byggnad_id: str
    entre_punkt: tuple[float, float]
    entre_rh2000: float
    gransdjup_m: float


@dataclass(frozen=True)
class GatuRastreringResultat:
    """Resultat från rasterisering av en parametrisk gata i 0,25 m-rutnät."""

    mark_z: NDArray[np.float32]
    markslag: NDArray[np.uint8]
    tackt_mask: NDArray[np.bool_]
    brytlinjer: tuple[BrytlinjePost, ...]


@dataclass(frozen=True)
class LappTillampning:
    """Resultat från tillämpning av en `ProjektLapp` på en K1-markmodell."""

    lapp_id: str
    bas_recept_sha256: str
    nytt_recept_sha256: str
    recept_data: dict[str, Any]
    mark_z: NDArray[np.float32]
    osakerhet_z: NDArray[np.float32]
    markslag: NDArray[np.uint8]
    geo: GeoTransformInfo
    andrad_mask: NDArray[np.bool_]
    projekt_mask: NDArray[np.bool_]
    bas_brytlinjer: tuple[BrytlinjePost, ...]
    kantstens_brytlinjer: tuple[BrytlinjePost, ...]
    oppningar: tuple[OppningPost, ...]
    registrerade_entreer: tuple[RegistreradEntre, ...]
    magasin_botten_rh2000: dict[str, float]

    @property
    def alla_brytlinjer(self) -> tuple[BrytlinjePost, ...]:
        """Samtliga brytlinjer (bas + genererade gatukantstenar) för K1-export."""
        return self.bas_brytlinjer + self.kantstens_brytlinjer


def _polygon_mask(
    polygon: Sequence[tuple[float, float]],
    geo: GeoTransformInfo,
) -> NDArray[np.bool_]:
    """Deterministisk vektoriserad punkt-i-polygon-mask på `geo`:s cellcentrum."""
    hojd = geo.hojd
    bredd = geo.bredd
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    mask = np.zeros((hojd, bredd), dtype=bool)
    if len(polygon) < 3:
        return np.asarray(mask, dtype=np.bool_)

    xs = [float(p[0]) for p in polygon]
    ys = [float(p[1]) for p in polygon]
    e_min, e_max = min(xs), max(xs)
    n_min, n_max = min(ys), max(ys)

    if e_max < geo.x_min or e_min > geo.x_max or n_max < geo.y_min or n_min > geo.y_max:
        return np.asarray(mask, dtype=np.bool_)

    kol0 = max(0, math.floor((e_min - geo.x_min) / dx))
    kol1 = min(bredd, math.ceil((e_max - geo.x_min) / dx) + 1)
    rad0 = max(0, math.floor((geo.y_max - n_max) / dy))
    rad1 = min(hojd, math.ceil((geo.y_max - n_min) / dy) + 1)
    if kol1 <= kol0 or rad1 <= rad0:
        return np.asarray(mask, dtype=np.bool_)

    e_vek = geo.x_min + (np.arange(kol0, kol1, dtype=np.float64) + 0.5) * dx
    n_vek = geo.y_max - (np.arange(rad0, rad1, dtype=np.float64) + 0.5) * dy
    sub_e, sub_n = np.meshgrid(e_vek, n_vek)

    inuti = np.zeros(sub_e.shape, dtype=bool)
    pa_kant = np.zeros(sub_e.shape, dtype=bool)

    # Ta bort ev. duplicerad slutpunkt så slingan alltid är enkel
    ring = list(polygon)
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring.pop()
    n_horn = len(ring)
    if n_horn < 3:
        return np.asarray(mask, dtype=np.bool_)

    for idx in range(n_horn):
        e0, n0 = float(ring[idx][0]), float(ring[idx][1])
        e1, n1 = float(ring[(idx + 1) % n_horn][0]), float(ring[(idx + 1) % n_horn][1])

        # Exakt kontroll för punkter på segmentet
        de = e1 - e0
        dn = n1 - n0
        l2 = de * de + dn * dn
        if l2 > 0.0:
            t = np.clip(((sub_e - e0) * de + (sub_n - n0) * dn) / l2, 0.0, 1.0)
            dist = np.hypot(sub_e - (e0 + t * de), sub_n - (n0 + t * dn))
            pa_kant |= dist <= _KANT_TOL_M

        korsar_y = (n0 > sub_n) != (n1 > sub_n)
        if np.any(korsar_y):
            dn_saker = n1 - n0
            if dn_saker != 0.0:
                x_kors = e0 + (sub_n - n0) * (e1 - e0) / dn_saker
                inuti ^= korsar_y & (sub_e < x_kors)

    mask[rad0:rad1, kol0:kol1] = inuti | pa_kant
    return np.asarray(mask, dtype=np.bool_)


def _sampla_markhojd(
    mark_z: NDArray[np.float32],
    geo: GeoTransformInfo,
    e_m: float,
    n_m: float,
) -> float:
    kol = min(geo.bredd - 1, max(0, math.floor((e_m - geo.x_min) / geo.pixelstorlek_x)))
    rad = min(geo.hojd - 1, max(0, math.floor((geo.y_max - n_m) / geo.pixelstorlek_y)))
    val = float(mark_z[rad, kol])
    if not math.isfinite(val):
        raise LappFel(
            f"Kunde inte sampla markhöjd vid ({e_m}, {n_m}): cell ({rad}, {kol}) är inte ändlig."
        )
    return val


def _skapa_offset_linje(
    mittlinje: Sequence[tuple[float, float]],
    offset_m: float,
) -> tuple[tuple[float, float], ...]:
    """Skapar en sidoförskjuten polylinje (vänster > 0, höger < 0) längs `mittlinje`."""
    n_pts = len(mittlinje)
    seg_normaler: list[tuple[float, float]] = []
    for idx in range(n_pts - 1):
        de = float(mittlinje[idx + 1][0]) - float(mittlinje[idx][0])
        dn = float(mittlinje[idx + 1][1]) - float(mittlinje[idx][1])
        langd = math.hypot(de, dn)
        if langd <= 1e-12:
            seg_normaler.append((0.0, 0.0))
        else:
            # Vänsternormal (-dn, de) / langd
            seg_normaler.append((-dn / langd, de / langd))

    resultat: list[tuple[float, float]] = []
    for idx in range(n_pts):
        e_p, n_p = float(mittlinje[idx][0]), float(mittlinje[idx][1])
        if idx == 0:
            nx, ny = seg_normaler[0]
        elif idx == n_pts - 1:
            nx, ny = seg_normaler[-1]
        else:
            n0x, n0y = seg_normaler[idx - 1]
            n1x, n1y = seg_normaler[idx]
            mx, my = n0x + n1x, n0y + n1y
            m_len = math.hypot(mx, my)
            nx, ny = (n0x, n0y) if m_len <= 1e-12 else (mx / m_len, my / m_len)
        resultat.append((e_p + offset_m * nx, n_p + offset_m * ny))
    return tuple(resultat)


def rastrera_parametrisk_gata(
    gata: LappGata,
    sektion: Gatusektion,
    mark_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
    geo: GeoTransformInfo,
) -> GatuRastreringResultat:
    """Rasteriserar en parametrisk gata (krönt körbana, kantsten, gångbana) i 0,25 m-rastret.

    Muterar aldrig `mark_z` eller `markslag`. Tvärsektionen har krön på mittlinjen,
    tvärfall `tvarfall_korbana_andel` ut till rännstenen vid `±korbana_bredd_m / 2`,
    kantstenssprång `+kantsten_hojd_m`, samt gångbana med `tvarfall_gangbana_andel`
    inåt mot rännstenen upp till `±total_bredd_m / 2`.
    """
    hojd, bredd = mark_z.shape
    if hojd != geo.hojd or bredd != geo.bredd or markslag.shape != (hojd, bredd):
        raise LappFel("Rasterformer och GeoTransformInfo stämmer inte överens i gaturasterisering.")

    ny_z = np.array(mark_z, dtype=np.float32, copy=True)
    ny_slag = np.array(markslag, dtype=np.uint8, copy=True)
    tackt = np.zeros((hojd, bredd), dtype=bool)

    z_punkter: tuple[float, ...] = (
        gata.profil_rh2000
        if gata.profil_rh2000 is not None
        else tuple(_sampla_markhojd(mark_z, geo, e, n) for e, n in gata.mittlinje)
    )

    total_halv = 0.5 * sektion.total_bredd_m
    korbana_halv = 0.5 * sektion.korbana_bredd_m
    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)

    xs = [float(p[0]) for p in gata.mittlinje]
    ys = [float(p[1]) for p in gata.mittlinje]
    e_min = min(xs) - total_halv
    e_max = max(xs) + total_halv
    n_min = min(ys) - total_halv
    n_max = max(ys) + total_halv

    brytlinjer: list[BrytlinjePost] = []
    if sektion.kantsten_hojd_m > 0.0:
        for tecken in (1.0, -1.0):
            kant_linje = _skapa_offset_linje(gata.mittlinje, tecken * korbana_halv)
            brytlinjer.append(
                BrytlinjePost(
                    typ="kantsten",
                    hojd_m=float(sektion.kantsten_hojd_m),
                    koordinater=kant_linje,
                )
            )

    if e_max < geo.x_min or e_min > geo.x_max or n_max < geo.y_min or n_min > geo.y_max:
        return GatuRastreringResultat(
            mark_z=ny_z,
            markslag=ny_slag,
            tackt_mask=np.asarray(tackt, dtype=np.bool_),
            brytlinjer=tuple(brytlinjer),
        )

    kol0 = max(0, math.floor((e_min - geo.x_min) / dx))
    kol1 = min(bredd, math.ceil((e_max - geo.x_min) / dx) + 1)
    rad0 = max(0, math.floor((geo.y_max - n_max) / dy))
    rad1 = min(hojd, math.ceil((geo.y_max - n_min) / dy) + 1)
    if kol1 <= kol0 or rad1 <= rad0:
        return GatuRastreringResultat(
            mark_z=ny_z,
            markslag=ny_slag,
            tackt_mask=np.asarray(tackt, dtype=np.bool_),
            brytlinjer=tuple(brytlinjer),
        )

    e_vek = geo.x_min + (np.arange(kol0, kol1, dtype=np.float64) + 0.5) * dx
    n_vek = geo.y_max - (np.arange(rad0, rad1, dtype=np.float64) + 0.5) * dy
    sub_e, sub_n = np.meshgrid(e_vek, n_vek)

    basta_dist = np.full(sub_e.shape, math.inf, dtype=np.float64)
    basta_z_mitt = np.full(sub_e.shape, np.nan, dtype=np.float64)
    n_seg = len(gata.mittlinje) - 1

    for idx in range(n_seg):
        e0, n0 = gata.mittlinje[idx]
        e1, n1 = gata.mittlinje[idx + 1]
        z0 = float(z_punkter[idx])
        z1 = float(z_punkter[idx + 1])
        de = e1 - e0
        dn = n1 - n0
        l2 = de * de + dn * dn
        if l2 <= 1e-12:
            continue

        t_raw = ((sub_e - e0) * de + (sub_n - n0) * dn) / l2
        giltig = np.ones(sub_e.shape, dtype=bool)
        if idx == 0:
            giltig &= t_raw >= -1e-9
        if idx == n_seg - 1:
            giltig &= t_raw <= 1.0 + 1e-9

        t = np.clip(t_raw, 0.0, 1.0)
        dist = np.hypot(sub_e - (e0 + t * de), sub_n - (n0 + t * dn))
        z_proj = z0 + t * (z1 - z0)

        battre = giltig & (dist < basta_dist)
        basta_dist = np.where(battre, dist, basta_dist)
        basta_z_mitt = np.where(battre, z_proj, basta_z_mitt)

    sub_tackt = np.asarray(basta_dist <= total_halv + _KANT_TOL_M, dtype=np.bool_)
    if not np.any(sub_tackt):
        return GatuRastreringResultat(
            mark_z=ny_z,
            markslag=ny_slag,
            tackt_mask=np.asarray(tackt, dtype=np.bool_),
            brytlinjer=tuple(brytlinjer),
        )

    pa_korbana = sub_tackt & (basta_dist <= korbana_halv + _KANT_TOL_M)
    pa_gangbana = sub_tackt & ~pa_korbana

    z_sub = np.asarray(ny_z[rad0:rad1, kol0:kol1], dtype=np.float64)
    slag_sub = ny_slag[rad0:rad1, kol0:kol1]

    if np.any(pa_korbana):
        z_kor = basta_z_mitt[pa_korbana] - sektion.tvarfall_korbana_andel * basta_dist[pa_korbana]
        z_sub[pa_korbana] = z_kor
        slag_sub[pa_korbana] = np.uint8(gata.markslag_korbana)

    if np.any(pa_gangbana):
        z_rannsten = basta_z_mitt[pa_gangbana] - sektion.tvarfall_korbana_andel * korbana_halv
        z_gang = (
            z_rannsten
            + sektion.kantsten_hojd_m
            + sektion.tvarfall_gangbana_andel * (basta_dist[pa_gangbana] - korbana_halv)
        )
        z_sub[pa_gangbana] = z_gang
        slag_sub[pa_gangbana] = np.uint8(gata.markslag_gangbana)

    ny_z[rad0:rad1, kol0:kol1] = np.asarray(z_sub, dtype=np.float32)
    tackt[rad0:rad1, kol0:kol1] = sub_tackt

    return GatuRastreringResultat(
        mark_z=ny_z,
        markslag=ny_slag,
        tackt_mask=np.asarray(tackt, dtype=np.bool_),
        brytlinjer=tuple(brytlinjer),
    )


def _rand_grannar_utanfor(
    mask: NDArray[np.bool_],
    mark_z: NDArray[np.float32],
    markslag: NDArray[np.uint8],
) -> NDArray[np.bool_]:
    """Returnerar mask över aktiva 4-grannceller som ligger direkt utanför `mask`."""
    hojd, bredd = mask.shape
    utvidgad = np.zeros((hojd, bredd), dtype=bool)
    utvidgad[1:, :] |= mask[:-1, :]
    utvidgad[:-1, :] |= mask[1:, :]
    utvidgad[:, 1:] |= mask[:, :-1]
    utvidgad[:, :-1] |= mask[:, 1:]
    aktiv = (markslag != MARKSLAG_BYGGNAD) & np.isfinite(mark_z)
    return np.asarray(utvidgad & ~mask & aktiv, dtype=np.bool_)


def _berakna_andrad_mask(
    bas_mark_z: NDArray[np.float32],
    bas_osakerhet_z: NDArray[np.float32],
    bas_markslag: NDArray[np.uint8],
    ny_mark_z: NDArray[np.float32],
    ny_osakerhet_z: NDArray[np.float32],
    ny_markslag: NDArray[np.uint8],
) -> NDArray[np.bool_]:
    z_lika = (bas_mark_z == ny_mark_z) | (np.isnan(bas_mark_z) & np.isnan(ny_mark_z))
    osak_lika = (bas_osakerhet_z == ny_osakerhet_z) | (
        np.isnan(bas_osakerhet_z) & np.isnan(ny_osakerhet_z)
    )
    slag_lika = bas_markslag == ny_markslag
    return np.asarray(~(z_lika & osak_lika & slag_lika), dtype=np.bool_)


def tillampa_lapp(
    bas_mark_z: NDArray[np.float32],
    bas_osakerhet_z: NDArray[np.float32],
    bas_markslag: NDArray[np.uint8],
    bas_geo: GeoTransformInfo,
    lapp: ProjektLapp,
    *,
    bas_recept_sha256: str,
    bas_brytlinjer: Sequence[BrytlinjePost] = (),
    bas_oppningar: Sequence[OppningPost] = (),
    sektioner: Mapping[str, Gatusektion] | None = None,
) -> LappTillampning:
    """Tillämpar en `ProjektLapp` (K3/v1) på basrastrer och skapar ett nytt recept.

    Muterar aldrig `bas_mark_z`, `bas_osakerhet_z` eller `bas_markslag`.
    Tillämpningsordning:
    1. Höjdytor (inklusive LandXML-TIN)
    2. Parametriska gator (krönt körbana, kantsten, gångbana)
    3. Fördröjningsmagasin (exakt volym `volym_m3` under omgivande tröskelkant)
    4. Byggnader (hinderhöjd + markslag 9 + registrering av `entre_rh2000`)
    5. Markslagsändringar
    """
    if not isinstance(bas_recept_sha256, str) or not bas_recept_sha256.strip():
        raise LappFel("bas_recept_sha256 måste vara en icke-tom sha256-sträng.")
    if (
        bas_mark_z.ndim != 2
        or bas_osakerhet_z.shape != bas_mark_z.shape
        or bas_markslag.shape != bas_mark_z.shape
    ):
        raise LappFel(
            f"Oförenliga rasterformer: mark={bas_mark_z.shape}, "
            f"osakerhet={bas_osakerhet_z.shape}, markslag={bas_markslag.shape}."
        )
    if bas_mark_z.shape != (bas_geo.hojd, bas_geo.bredd):
        raise LappFel(
            f"GeoTransformInfo ({bas_geo.hojd}x{bas_geo.bredd}) matchar inte "
            f"raster ({bas_mark_z.shape})."
        )

    ny_mark_z = np.array(bas_mark_z, dtype=np.float32, copy=True)
    ny_osakerhet_z = np.array(bas_osakerhet_z, dtype=np.float32, copy=True)
    ny_markslag = np.array(bas_markslag, dtype=np.uint8, copy=True)
    projekt_mask = np.zeros(bas_mark_z.shape, dtype=bool)

    aktiva_sektioner: Mapping[str, Gatusektion]
    if sektioner is not None:
        aktiva_sektioner = sektioner
    elif lapp.gator:
        aktiva_sektioner = las_gatusektioner_fran_konfig()
    else:
        aktiva_sektioner = {}

    # 1. Höjdytor (LandXML-TIN eller konstant höjd inom polygon)
    for yta in lapp.hojdytor:
        if yta.landxml is not None:
            tin = las_landxml_tin(yta.landxml, koordinat_ordning=yta.koordinat_ordning)
            tin_z, tin_mask = rastrera_landxml_tin(tin, bas_geo)
            eff_mask = tin_mask
            if yta.polygon:
                eff_mask = np.asarray(
                    tin_mask & _polygon_mask(yta.polygon, bas_geo), dtype=np.bool_
                )
            if np.any(eff_mask):
                ny_mark_z[eff_mask] = tin_z[eff_mask]
                ny_osakerhet_z[eff_mask] = np.float32(yta.osakerhet_m)
                if yta.markslag is not None:
                    ny_markslag[eff_mask] = np.uint8(yta.markslag)
                projekt_mask |= eff_mask
        elif yta.har_nya_falt:
            # R4: lutande yta, delta_m och/eller slänt, med exakt kantavstånd (lapp.markniva).
            from lapp.markniva import tillampa_markniva

            ny_mask = tillampa_markniva(
                yta,
                ny_mark_z,
                ny_osakerhet_z,
                ny_markslag,
                bas_geo,
                _polygon_mask(yta.polygon, bas_geo),
            )
            projekt_mask |= ny_mask
        else:
            poly_mask = _polygon_mask(yta.polygon, bas_geo)
            if np.any(poly_mask) and yta.hojd_rh2000 is not None:
                ny_mark_z[poly_mask] = np.float32(yta.hojd_rh2000)
                ny_osakerhet_z[poly_mask] = np.float32(yta.osakerhet_m)
                if yta.markslag is not None:
                    ny_markslag[poly_mask] = np.uint8(yta.markslag)
                projekt_mask |= poly_mask

    # 2. Parametriska gator
    kantstens_brytlinjer: list[BrytlinjePost] = []
    for gata in lapp.gator:
        sektion = aktiva_sektioner.get(gata.sektion)
        if sektion is None:
            tillgangliga = ", ".join(sorted(aktiva_sektioner))
            raise LappFel(
                f"Okänd gatusektion '{gata.sektion}' för gata '{gata.gata_id}'. "
                f"Tillgängliga sektioner: {tillgangliga or '(inga)'}."
            )
        gatu_res = rastrera_parametrisk_gata(gata, sektion, ny_mark_z, ny_markslag, bas_geo)
        ny_mark_z = gatu_res.mark_z
        ny_markslag = gatu_res.markslag
        ny_osakerhet_z[gatu_res.tackt_mask] = np.float32(gata.osakerhet_m)
        projekt_mask |= gatu_res.tackt_mask
        kantstens_brytlinjer.extend(gatu_res.brytlinjer)

    # 3. Fördröjningsmagasin
    cell_area_m2 = float(bas_geo.pixelstorlek_x) * float(bas_geo.pixelstorlek_y)
    magasin_botten: dict[str, float] = {}
    for mag in lapp.magasin:
        mag_mask = _polygon_mask(mag.polygon, bas_geo)
        antal_celler = int(np.sum(mag_mask))
        if antal_celler == 0:
            raise LappFel(
                f"Magasin '{mag.magasin_id}' täcker inga celler inom rutnätet."
            )
        if mag.botten_rh2000 is not None:
            z_botten = float(mag.botten_rh2000)
            ny_mark_z[mag_mask] = np.float32(z_botten)
            ny_markslag[mag_mask] = np.uint8(mag.markslag)
            ny_osakerhet_z[mag_mask] = np.float32(mag.osakerhet_m)
            magasin_botten[mag.magasin_id] = z_botten
        elif mag.volym_m3 > 0.0:
            rand = _rand_grannar_utanfor(mag_mask, ny_mark_z, ny_markslag)
            z_rim = (
                float(np.min(ny_mark_z[rand]))
                if np.any(rand)
                else float(np.min(ny_mark_z[mag_mask]))
            )
            area_m2 = float(antal_celler) * cell_area_m2
            z_botten = z_rim - float(mag.volym_m3) / area_m2
            ny_mark_z[mag_mask] = np.float32(z_botten)
            ny_markslag[mag_mask] = np.uint8(mag.markslag)
            ny_osakerhet_z[mag_mask] = np.float32(mag.osakerhet_m)
            magasin_botten[mag.magasin_id] = z_botten
        else:
            magasin_botten[mag.magasin_id] = float(np.min(ny_mark_z[mag_mask]))
        projekt_mask |= mag_mask

    # 4. Byggnader (hinder + markslag 9 + entréer)
    registrerade_entreer: list[RegistreradEntre] = []
    for bygg in lapp.byggnader:
        bygg_mask = _polygon_mask(bygg.polygon, bas_geo)
        if np.any(bygg_mask):
            if bygg.tak_rh2000 is not None:
                ny_mark_z[bygg_mask] = np.float32(bygg.tak_rh2000)
            else:
                ny_mark_z[bygg_mask] = np.asarray(
                    ny_mark_z[bygg_mask] + np.float32(bygg.hinderhojd_m),
                    dtype=np.float32,
                )
            ny_markslag[bygg_mask] = np.uint8(MARKSLAG_BYGGNAD)
            projekt_mask |= bygg_mask
        registrerade_entreer.append(
            RegistreradEntre(
                byggnad_id=bygg.byggnad_id,
                entre_punkt=bygg.entre_punkt,
                entre_rh2000=bygg.entre_rh2000,
                gransdjup_m=bygg.gransdjup_m,
            )
        )

    # 5. Markslagsändringar
    for ms in lapp.markslag_andringar:
        ms_mask = _polygon_mask(ms.polygon, bas_geo)
        if np.any(ms_mask):
            ny_markslag[ms_mask] = np.uint8(ms.markslag)
            projekt_mask |= ms_mask

    andrad_mask = _berakna_andrad_mask(
        bas_mark_z,
        bas_osakerhet_z,
        bas_markslag,
        ny_mark_z,
        ny_osakerhet_z,
        ny_markslag,
    )

    recept_data: dict[str, Any] = {
        "kontrakt": "K1+K3/v1",
        "bas_recept_sha256": bas_recept_sha256,
        "lapp_id": lapp.lapp_id,
        "lapp_sha256": lapp.lapp_sha256(),
        "epsg": bas_geo.epsg,
        "bbox": [bas_geo.x_min, bas_geo.y_min, bas_geo.x_max, bas_geo.y_max],
        "upplosning_m": [bas_geo.pixelstorlek_x, bas_geo.pixelstorlek_y],
        "antal_andrade_celler": int(np.sum(andrad_mask)),
        "antal_projekt_celler": int(np.sum(projekt_mask)),
        "sektioner": {
            g.sektion: aktiva_sektioner[g.sektion].till_dict()
            for g in lapp.gator
            if g.sektion in aktiva_sektioner
        },
    }
    kanonisk = json.dumps(recept_data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    nytt_recept_sha256 = sha256_bytes(kanonisk.encode("utf-8"))

    return LappTillampning(
        lapp_id=lapp.lapp_id,
        bas_recept_sha256=bas_recept_sha256,
        nytt_recept_sha256=nytt_recept_sha256,
        recept_data=recept_data,
        mark_z=ny_mark_z,
        osakerhet_z=ny_osakerhet_z,
        markslag=ny_markslag,
        geo=bas_geo,
        andrad_mask=andrad_mask,
        projekt_mask=np.asarray(projekt_mask, dtype=np.bool_),
        bas_brytlinjer=tuple(bas_brytlinjer),
        kantstens_brytlinjer=tuple(kantstens_brytlinjer),
        oppningar=tuple(bas_oppningar),
        registrerade_entreer=tuple(registrerade_entreer),
        magasin_botten_rh2000=magasin_botten,
    )
