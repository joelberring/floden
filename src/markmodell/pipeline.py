"""Hela beräkningskedjan för att bygga en K1-markmodellruta och jämförelsebild."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw

from gemensamt.hash import sha256_bytes, sha256_fil
from gemensamt.manifest import FilPost, hamta_kodversion, skapa_manifest, skriv_manifest
from markmodell.brytlinjer import Brytlinje, SnapStatistik, brann_in_brytlinjer, fast_losa_andar
from markmodell.hinder_oppningar import (
    BYGGNAD,
    HARDGJORT,
    SKOG,
    Byggnad,
    Oppning,
    brann_in_byggnader,
    brann_in_oppningar,
)
from markmodell.interpolering import interpolera_markpunkter
from markmodell.k1_skrivare import (
    skriv_brytlinjer_gpkg,
    skriv_geotiff,
    skriv_oppningar_gpkg,
)
from markmodell.osakerhet import OsakerhetParametrar, berakna_osakerhet
from markmodell.recept import KallPost, MarkmodellRecept, skriv_recept
from markmodell.rutnat import Ruta


class PipelineFel(Exception):
    """Fel vid körning av markmodellens beräkningskedja."""


@dataclass(frozen=True)
class RutaResultat:
    """Resultat och sökvägar från en byggd K1-markmodellruta."""

    ruta: Ruta
    katalog: Path
    mark_z: NDArray[np.float32]
    osakerhet_z: NDArray[np.float32]
    markslag: NDArray[np.uint8]
    snap_statistik: SnapStatistik
    recept: MarkmodellRecept
    recept_sha256: str
    varningar: list[str]
    filer: dict[str, Path]


def _hasha_array_och_parametrar(
    mark_punkter_e: NDArray[np.float64] | None,
    mark_punkter_n: NDArray[np.float64] | None,
    mark_punkter_z: NDArray[np.float64] | None,
    grund_z: NDArray[np.float32] | None,
    brytlinjer: Sequence[Brytlinje],
    byggnader: Sequence[Byggnad],
    oppningar: Sequence[Oppning],
) -> list[KallPost]:
    """Skapar deterministiska källposter för in-memory-indata i receptet."""
    kallor: list[KallPost] = []
    if mark_punkter_e is not None and mark_punkter_n is not None and mark_punkter_z is not None:
        buf = (
            np.ascontiguousarray(mark_punkter_e, dtype=np.float64).tobytes()
            + np.ascontiguousarray(mark_punkter_n, dtype=np.float64).tobytes()
            + np.ascontiguousarray(mark_punkter_z, dtype=np.float64).tobytes()
        )
        kallor.append(
            KallPost(roll="laser_markpunkter", kalla="minne:punktmoln", sha256=sha256_bytes(buf))
        )
    if grund_z is not None:
        buf_g = np.ascontiguousarray(grund_z, dtype=np.float32).tobytes()
        kallor.append(
            KallPost(roll="grundraster", kalla="minne:grundraster", sha256=sha256_bytes(buf_g))
        )

    bryt_json = json.dumps(
        [{"typ": b.typ, "hojd_m": b.hojd_m, "punkter": b.punkter} for b in brytlinjer],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    kallor.append(
        KallPost(roll="brytlinjer", kalla="baskarta", sha256=sha256_bytes(bryt_json))
    )

    bygg_json = json.dumps(
        [
            {
                "polygon": bg.polygon,
                "hinderhojd_m": bg.hinderhojd_m,
                "takhoyd_rh2000": bg.takhoyd_rh2000,
            }
            for bg in byggnader
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    kallor.append(
        KallPost(roll="byggnader", kalla="baskarta", sha256=sha256_bytes(bygg_json))
    )

    opp_json = json.dumps(
        [
            {
                "typ": o.typ,
                "bredd_m": o.bredd_m,
                "botten_rh2000": o.botten_rh2000,
                "kalla": o.kalla,
                "linje": o.linje,
            }
            for o in oppningar
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    kallor.append(
        KallPost(roll="oppningar", kalla="oppningar", sha256=sha256_bytes(opp_json))
    )
    return kallor


def bygg_markmodell_ruta(
    ruta: Ruta,
    ut_mapp: str | Path,
    *,
    grund_z: NDArray[np.float32] | None = None,
    mark_punkter_e: NDArray[np.float64] | None = None,
    mark_punkter_n: NDArray[np.float64] | None = None,
    mark_punkter_z: NDArray[np.float64] | None = None,
    laser_klasser: NDArray[np.int32] | NDArray[np.uint8] | None = None,
    brytlinjer: Sequence[Brytlinje] = (),
    byggnader: Sequence[Byggnad] = (),
    oppningar: Sequence[Oppning] = (),
    markslag_grund: NDArray[np.uint8] | None = None,
    punkt_tathet_per_m2: NDArray[np.float32] | None = None,
    avstand_till_punkt_m: NDArray[np.float32] | None = None,
    under_vegetation: NDArray[np.bool_] | None = None,
    skanning_alder_ar: float = 0.0,
    snaptolerans_m: float = 0.25,
    max_avstand_m: float = 3.0,
    osakerhet_parametrar: OsakerhetParametrar | None = None,
    extra_kallor: Sequence[KallPost] = (),
    kodversion: str | None = None,
    start_tid: str | None = None,
) -> RutaResultat:
    """Kör hela markmodellkedjan för en ruta och skriver komplett K1-katalog + `run.json`.

    Antingen `grund_z` eller punktmolnet (`mark_punkter_e`, `mark_punkter_n`, `mark_punkter_z`)
    måste anges. Om punktmoln anges beräknas `grund_z`, avståndskarta och punkttäthet
    automatiskt via `interpolera_markpunkter`.
    """
    t_start = time.perf_counter()
    mal_katalog = Path(ut_mapp) / ruta.namn
    mal_katalog.mkdir(parents=True, exist_ok=True)

    rader, kolumner = ruta.form_rc

    if mark_punkter_e is not None and mark_punkter_n is not None and mark_punkter_z is not None:
        z_bas, avst_karta, tathet_karta = interpolera_markpunkter(
            mark_punkter_e,
            mark_punkter_n,
            mark_punkter_z,
            ruta,
            max_avstand_m=max_avstand_m,
            klasser=laser_klasser,
        )
        if grund_z is not None:
            if grund_z.shape != ruta.form_rc:
                raise PipelineFel(f"grund_z har form {grund_z.shape}, förväntade {ruta.form_rc}.")
            z_bas = np.array(grund_z, dtype=np.float32, copy=True)
    elif grund_z is not None:
        if grund_z.shape != ruta.form_rc:
            raise PipelineFel(f"grund_z har form {grund_z.shape}, förväntade {ruta.form_rc}.")
        z_bas = np.array(grund_z, dtype=np.float32, copy=True)
        tathet_karta = (
            np.array(punkt_tathet_per_m2, dtype=np.float32, copy=True)
            if punkt_tathet_per_m2 is not None
            else np.full((rader, kolumner), 2.0, dtype=np.float32)
        )
        avst_karta = (
            np.array(avstand_till_punkt_m, dtype=np.float32, copy=True)
            if avstand_till_punkt_m is not None
            else np.full((rader, kolumner), 0.5, dtype=np.float32)
        )
    else:
        raise PipelineFel(
            "Antingen grund_z eller (mark_punkter_e, mark_punkter_n, mark_punkter_z) måste anges."
        )

    if punkt_tathet_per_m2 is not None:
        tathet_karta = np.array(punkt_tathet_per_m2, dtype=np.float32, copy=True)
    if avstand_till_punkt_m is not None:
        avst_karta = np.array(avstand_till_punkt_m, dtype=np.float32, copy=True)

    if markslag_grund is not None:
        if markslag_grund.shape != ruta.form_rc:
            raise PipelineFel(
                f"markslag_grund har form {markslag_grund.shape}, förväntade {ruta.form_rc}."
            )
        slag = np.array(markslag_grund, dtype=np.uint8, copy=True)
    else:
        slag = np.full((rader, kolumner), HARDGJORT, dtype=np.uint8)

    # Steg 5: fäst lösa ändar på brytlinjer och bränn in 4-sammanhängande
    snappade_linjer, snap_stat = fast_losa_andar(brytlinjer, tolerans_m=snaptolerans_m)
    z_med_bryt = brann_in_brytlinjer(z_bas, ruta, snappade_linjer, snappa=False)

    # Steg 4: byggnader som hinder
    z_med_bygg, slag = brann_in_byggnader(z_med_bryt, slag, ruta, byggnader)

    # Steg 6: trummor, portar och passager
    z_slut, slag = brann_in_oppningar(z_med_bygg, slag, ruta, oppningar)

    # Steg 7: osäkerhet per cell
    under_bygg_mask = slag == BYGGNAD
    if under_vegetation is not None:
        if under_vegetation.shape != ruta.form_rc:
            raise PipelineFel(
                f"under_vegetation har form {under_vegetation.shape}, förväntade {ruta.form_rc}."
            )
        under_veg_mask = np.asarray(under_vegetation, dtype=bool)
    else:
        under_veg_mask = slag == SKOG

    osak_res = berakna_osakerhet(
        punkt_tathet_per_m2=tathet_karta,
        avstand_till_punkt_m=avst_karta,
        under_byggnad=under_bygg_mask,
        under_vegetation=under_veg_mask,
        skanning_alder_ar=skanning_alder_ar,
        parametrar=osakerhet_parametrar,
    )

    # Steg 9: skriv K1-filer
    mark_tif = skriv_geotiff(mal_katalog / "mark.tif", z_slut, ruta, nodata=-9999.0)
    osakerhet_tif = skriv_geotiff(
        mal_katalog / "osakerhet.tif", osak_res.sigma_z_m, ruta, nodata=-9999.0
    )
    markslag_tif = skriv_geotiff(mal_katalog / "markslag.tif", slag, ruta, nodata=0)
    brytlinjer_gpkg = skriv_brytlinjer_gpkg(
        mal_katalog / "brytlinjer.gpkg", snappade_linjer, ruta
    )
    oppningar_gpkg = skriv_oppningar_gpkg(mal_katalog / "oppningar.gpkg", oppningar, ruta)

    ver = kodversion if kodversion is not None else hamta_kodversion()
    kallor = _hasha_array_och_parametrar(
        mark_punkter_e,
        mark_punkter_n,
        mark_punkter_z,
        grund_z,
        brytlinjer,
        byggnader,
        oppningar,
    ) + list(extra_kallor)

    osak_p = osakerhet_parametrar if osakerhet_parametrar is not None else OsakerhetParametrar()
    installningar: dict[str, Any] = {
        "snaptolerans_m": snaptolerans_m,
        "max_avstand_m": max_avstand_m,
        "skanning_alder_ar": skanning_alder_ar,
        "osakerhet_max_avstand_varning_m": osak_p.max_avstand_varning_m,
        "storlek_m": ruta.storlek_m,
        "overlapp_m": ruta.overlapp_m,
        "upplosning_m": ruta.upplosning_m,
    }
    recept = MarkmodellRecept(
        ruta_namn=ruta.namn,
        yttre_bbox=ruta.yttre_bbox,
        upplosning_m=ruta.upplosning_m,
        kodversion=ver,
        kallor=tuple(kallor),
        installningar=installningar,
    )
    recept_sha = recept.recept_sha256()
    recept_fil = skriv_recept(recept, mal_katalog / "recept.json")

    utdata_poster = [
        FilPost(roll="mark", fil="mark.tif", sha256=sha256_fil(mark_tif)),
        FilPost(roll="osakerhet", fil="osakerhet.tif", sha256=sha256_fil(osakerhet_tif)),
        FilPost(roll="markslag", fil="markslag.tif", sha256=sha256_fil(markslag_tif)),
        FilPost(roll="brytlinjer", fil="brytlinjer.gpkg", sha256=sha256_fil(brytlinjer_gpkg)),
        FilPost(roll="oppningar", fil="oppningar.gpkg", sha256=sha256_fil(oppningar_gpkg)),
        FilPost(roll="recept", fil="recept.json", sha256=sha256_fil(recept_fil)),
    ]
    indata_poster = [
        FilPost(roll=k.roll, fil=k.kalla, sha256=k.sha256) for k in recept.kallor
    ]

    kortid_s = round(time.perf_counter() - t_start, 4)
    manifest = skapa_manifest(
        "markmodell",
        indata=indata_poster,
        parametrar={**installningar, "ruta": ruta.namn, "recept_sha256": recept_sha},
        utdata=utdata_poster,
        matvarden={
            "antal_celler": int(z_slut.size),
            "snap_losa_fore": snap_stat.antal_losa_andar_fore,
            "snap_slutna": snap_stat.antal_slutna_andar,
            "osakerhet_medel_m": round(osak_res.medel_sigma_m, 4),
            "osakerhet_max_m": round(osak_res.max_sigma_m, 4),
            "kortid_s": kortid_s,
        },
        varningar=osak_res.varningar,
        kodversion=ver,
        start=start_tid,
    )
    run_fil = skriv_manifest(manifest, mal_katalog / "run.json")

    return RutaResultat(
        ruta=ruta,
        katalog=mal_katalog,
        mark_z=z_slut,
        osakerhet_z=osak_res.sigma_z_m,
        markslag=slag,
        snap_statistik=snap_stat,
        recept=recept,
        recept_sha256=recept_sha,
        varningar=list(osak_res.varningar),
        filer={
            "mark": mark_tif,
            "osakerhet": osakerhet_tif,
            "markslag": markslag_tif,
            "brytlinjer": brytlinjer_gpkg,
            "oppningar": oppningar_gpkg,
            "recept": recept_fil,
            "run": run_fil,
        },
    )


def _skala_till_rgb_terrain(
    matris: NDArray[np.float32],
    vmin: float,
    vmax: float,
) -> NDArray[np.uint8]:
    """Färgsätter höjddata i en tydlig terrängskala (blå-grön-gul-brun-vit)."""
    spännvidd = vmax - vmin if vmax > vmin else 1.0
    norm = np.clip((matris.astype(np.float64) - vmin) / spännvidd, 0.0, 1.0)
    # 4-stopps färginterpolering: låg (blå/grön) -> medel (sand) -> hög (mörkbrun/grå)
    r = np.interp(norm, [0.0, 0.35, 0.70, 1.0], [45.0, 115.0, 210.0, 240.0])
    g = np.interp(norm, [0.0, 0.35, 0.70, 1.0], [110.0, 175.0, 185.0, 240.0])
    b = np.interp(norm, [0.0, 0.35, 0.70, 1.0], [160.0, 95.0, 125.0, 245.0])
    return np.stack([r, g, b], axis=-1).astype(np.uint8)


def _skala_till_rgb_diff(
    diff: NDArray[np.float32],
    max_abs: float,
) -> NDArray[np.uint8]:
    """Färgsätter höjddifferens (markmodell - referens): blå (lägre), vit (0), röd (högre)."""
    grans = max_abs if max_abs > 1e-6 else 1.0
    norm = np.clip(diff.astype(np.float64) / grans, -1.0, 1.0)
    r = np.where(norm >= 0.0, 255.0, 255.0 * (1.0 + norm))
    g = 255.0 * (1.0 - np.abs(norm))
    b = np.where(norm <= 0.0, 255.0, 255.0 * (1.0 - norm))
    return np.stack([r, g, b], axis=-1).astype(np.uint8)


def skapa_jamforelsebild(
    mark_z: NDArray[np.float32],
    referens_z: NDArray[np.float32],
    ruta: Ruta,
    ut_fil: str | Path,
    tvarsnitt_rad: int | None = None,
) -> Path:
    """Genererar PNG-jämförelsebild (markmodell, referens, differens och tvärsnitt).

    Visar uppifrån/vänster till höger:
    - Panel 1: Flodens markmodell (`mark_z`) med markerad tvärsnittslinje.
    - Panel 2: Referensmodell (t.ex. Lantmäteriets 1 m-modell, `referens_z`).
    - Panel 3: Differenskarta (`mark_z - referens_z`) som tydliggör kantstenar/trummor.
    - Nedre panel: Höjdprofil (tvärsnitt längs vald rad) för båda modellerna.
    """
    if mark_z.shape != ruta.form_rc or referens_z.shape != ruta.form_rc:
        raise PipelineFel(
            f"mark_z {mark_z.shape} och referens_z {referens_z.shape} "
            f"måste matcha rutans form {ruta.form_rc}."
        )

    rader, kolumner = ruta.form_rc
    rad = rader // 2 if tvarsnitt_rad is None else int(tvarsnitt_rad)
    if not (0 <= rad < rader):
        raise PipelineFel(f"tvarsnitt_rad {rad} ligger utanför [0, {rader - 1}].")

    giltig_min = float(min(np.nanmin(mark_z), np.nanmin(referens_z)))
    # Använd 98:e percentilen så enstaka 10 m-byggnader inte trycker ihop gatans nyanser
    giltig_max = float(
        max(np.nanpercentile(mark_z, 98.0), np.nanpercentile(referens_z, 98.0))
    )
    if giltig_max <= giltig_min:
        giltig_max = giltig_min + 1.0

    diff = (mark_z - referens_z).astype(np.float32)
    max_abs_diff = float(np.nanpercentile(np.abs(diff), 98.0))
    max_abs_diff = max(max_abs_diff, 0.15)

    panel_w = 240
    panel_h = max(120, int(round(panel_w * (rader / kolumner))))
    marginal = 16
    rubrik_h = 26
    profil_h = 150

    rgb_mark = _skala_till_rgb_terrain(mark_z, giltig_min, giltig_max)
    rgb_ref = _skala_till_rgb_terrain(referens_z, giltig_min, giltig_max)
    rgb_diff = _skala_till_rgb_diff(diff, max_abs_diff)

    img_mark = Image.fromarray(rgb_mark, mode="RGB").resize(
        (panel_w, panel_h), Image.Resampling.NEAREST
    )
    img_ref = Image.fromarray(rgb_ref, mode="RGB").resize(
        (panel_w, panel_h), Image.Resampling.NEAREST
    )
    img_diff = Image.fromarray(rgb_diff, mode="RGB").resize(
        (panel_w, panel_h), Image.Resampling.NEAREST
    )

    total_w = marginal * 4 + panel_w * 3
    total_h = marginal * 3 + rubrik_h + panel_h + profil_h + 24
    duk = Image.new("RGB", (total_w, total_h), (250, 250, 250))
    draw = ImageDraw.Draw(duk)

    x_paneler = [marginal, marginal * 2 + panel_w, marginal * 3 + panel_w * 2]
    y_top = marginal + rubrik_h
    titlar = [
        f"Floden ({ruta.namn})",
        "Referens (Lantmateriet)",
        "Differens (Floden - Ref)",
    ]
    for xp, titel, bild in zip(x_paneler, titlar, (img_mark, img_ref, img_diff), strict=True):
        draw.text((xp, marginal), titel, fill=(20, 20, 20))
        duk.paste(bild, (xp, y_top))
        # Rita streck för valt tvärsnitt
        y_rad = y_top + int(round((rad / max(1, rader - 1)) * (panel_h - 1)))
        draw.line([(xp, y_rad), (xp + panel_w - 1, y_rad)], fill=(20, 20, 20), width=1)
        draw.rectangle([xp, y_top, xp + panel_w - 1, y_top + panel_h - 1], outline=(80, 80, 80))

    # Nedre panel: tvärsnitt längs vald rad
    px0 = marginal
    px1 = total_w - marginal
    py0 = y_top + panel_h + marginal + 18
    py1 = total_h - marginal
    draw.text(
        (px0, py0 - 18),
        f"Tvarsnitt rad {rad} (svart = Floden, bla = Referens)",
        fill=(20, 20, 20),
    )
    draw.rectangle([px0, py0, px1, py1], fill=(255, 255, 255), outline=(100, 100, 100))

    profil_mark = mark_z[rad, :].astype(np.float64)
    profil_ref = referens_z[rad, :].astype(np.float64)
    p_min = float(min(np.nanmin(profil_mark), np.nanmin(profil_ref)))
    p_max = float(max(np.nanmax(profil_mark), np.nanmax(profil_ref)))
    if p_max <= p_min:
        p_max = p_min + 1.0
    p_span = p_max - p_min

    def _till_px(kol_arr: NDArray[np.float64], z_arr: NDArray[np.float64]) -> list[tuple[int, int]]:
        pts: list[tuple[int, int]] = []
        w_in = max(1, (px1 - px0) - 8)
        h_in = max(1, (py1 - py0) - 12)
        for k_val, z_val in zip(kol_arr, z_arr, strict=True):
            if not np.isfinite(z_val):
                continue
            x = int(round(px0 + 4 + (k_val / max(1, kolumner - 1)) * w_in))
            y = int(round((py1 - 6) - ((z_val - p_min) / p_span) * h_in))
            pts.append((x, y))
        return pts

    xs = np.arange(kolumner, dtype=np.float64)
    pts_ref = _till_px(xs, profil_ref)
    pts_mark = _till_px(xs, profil_mark)
    if len(pts_ref) >= 2:
        draw.line(pts_ref, fill=(40, 100, 210), width=2)
    if len(pts_mark) >= 2:
        draw.line(pts_mark, fill=(20, 20, 20), width=2)

    mal = Path(ut_fil)
    mal.parent.mkdir(parents=True, exist_ok=True)
    duk.save(mal, format="PNG")
    return mal
