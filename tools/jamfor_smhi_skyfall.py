# ruff: noqa: E501
"""CLI-verktyg för T09b: deterministisk skyfallsjämförelse mot SMHI/LstO-raster och kommunala kontrollpunkter.

Exempel (raster + kontrollpunkter + vattenmask):
    python tools/jamfor_smhi_skyfall.py \\
        --floden-raster utdata/max_djup.tif \\
        --ref-raster data/referens/smhi_100ar.tif \\
        --ref-punkter data/referens/goteborg_checkpoints.json \\
        --mask-vatten k1/markslag.tif \\
        --omrade lindholmen \\
        --ut-json utdata/t09b_lindholmen.json \\
        --ut-md utdata/t09b_lindholmen.md

Exempel (enbart kontrollpunkter utan externt SMHI-raster):
    python tools/jamfor_smhi_skyfall.py \\
        --floden-raster utdata/max_djup.tif \\
        --ref-punkter data/referens/goteborg_checkpoints.json \\
        --omrade guldheden \\
        --ut-json utdata/t09b_guldheden.json
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
import tracemalloc
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from numpy.typing import NDArray
from pyproj import CRS, Transformer
from rasterio.transform import Affine
from rasterio.warp import Resampling, reproject

from benchmark.format import maskininfo, skapa_dokument
from gemensamt.hash import sha256_fil
from gemensamt.manifest import hamta_kodversion

STANDARD_TROSKLAR_M: tuple[float, ...] = (0.05, 0.10, 0.30, 0.50)
VATTEN_MARKSLAG_KODER: frozenset[int] = frozenset({8, 9})


def _rent_tal(varde: float | int | None) -> float | None:
    if varde is None:
        return None
    f = float(varde)
    return f if math.isfinite(f) else None


def _valfritt_tal(ra: Any) -> float | None:
    if ra is None:
        return None
    if isinstance(ra, str):
        s = ra.strip()
        if not s:
            return None
        v = float(s)
    elif isinstance(ra, bool):
        raise ValueError(f"Ogiltigt talvärde: {ra!r}")
    else:
        v = float(ra)
    if not math.isfinite(v):
        raise ValueError(f"Icke-ändligt talvärde: {ra!r}")
    return v


def bygg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="jamfor_smhi_skyfall",
        description=(
            "Jämför Flodens skyfallsraster (max_djup.tif) deterministiskt mot valfritt "
            "SMHI/LstO-referensraster och/eller kommunala kontrollpunkter (K37 JSON + Markdown)."
        ),
    )
    ap.add_argument(
        "--floden-raster",
        type=Path,
        required=True,
        help="Sökväg till Flodens maxdjupsraster (GeoTIFF, meter, EPSG:3011).",
    )
    ap.add_argument(
        "--ref-raster",
        type=Path,
        default=None,
        help="Valfri sökväg till SMHI- eller LstO-referensraster (GeoTIFF, omprojiceras automatiskt).",
    )
    ap.add_argument(
        "--skala-m",
        type=float,
        default=None,
        help=(
            "Valfri gemensam cellstorlek i meter för rasterjämförelse (t.ex. 10.0 för SMHI-skala). "
            "Utelämnad = aligna referensrastret till Flodens rutnät."
        ),
    )
    ap.add_argument(
        "--ref-punkter",
        type=Path,
        default=None,
        help="Valfri sökväg till kontrollpunkter (JSON eller CSV; EPSG:3007/3006/4326/3011).",
    )
    ap.add_argument(
        "--mask-vatten",
        type=Path,
        default=None,
        help="Valfri sökväg till markslag.tif; exkluderar celler med markslag i {8, 9}.",
    )
    ap.add_argument(
        "--troskel-m",
        type=float,
        default=0.10,
        help="Primär vattendjupströskel i meter för våtmarks-IoU (förval: 0.10 m).",
    )
    ap.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Valfri sökväg till Flodens run.json för spårbarhetsmetadata.",
    )
    ap.add_argument(
        "--omrade",
        type=str,
        default=None,
        help="Valfritt områdes-id (t.ex. haga, lindholmen, guldheden) för filtrering av kontrollpunkter.",
    )
    ap.add_argument(
        "--ut-json",
        type=Path,
        default=None,
        help="Skriv K37-kompatibel JSON-rapport (allow_nan=False) till denna fil.",
    )
    ap.add_argument(
        "--ut-md",
        type=Path,
        default=None,
        help="Skriv Markdown-rapport till denna fil.",
    )
    ap.add_argument(
        "--krav-godkand",
        action="store_true",
        help="Returnera exitkod 1 om någon djupkontrollpunkt ligger utanför sitt referensintervall.",
    )
    return ap


def _las_raster_float(sokvag: Path) -> tuple[NDArray[np.float64], Affine, CRS, float]:
    with rasterio.open(sokvag) as src:
        if src.crs is None:
            raise ValueError(f"Rastret saknar CRS: {sokvag}")
        data = src.read(1).astype(np.float64)
        if src.nodata is not None:
            if math.isnan(float(src.nodata)):
                pass
            else:
                data[data == float(src.nodata)] = np.nan
        cell_m = float(abs(src.transform.a))
        return data, src.transform, CRS.from_user_input(src.crs), cell_m


def _omprojicera_till_rutnat(
    kalla: NDArray[np.float64],
    src_transform: Affine,
    src_crs: CRS,
    *,
    dst_shape: tuple[int, int],
    dst_transform: Affine,
    dst_crs: CRS,
    resampling: Resampling,
    src_nodata: float | None = np.nan,
    dst_nodata: float = np.nan,
) -> NDArray[np.float64]:
    ut = np.full(dst_shape, dst_nodata, dtype=np.float64)
    reproject(
        source=kalla,
        destination=ut,
        src_transform=src_transform,
        src_crs=src_crs,
        src_nodata=src_nodata,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        dst_nodata=dst_nodata,
        resampling=resampling,
    )
    return ut


def _bygg_skalat_rutnat(
    src_shape: tuple[int, int],
    src_transform: Affine,
    skala_m: float,
) -> tuple[tuple[int, int], Affine]:
    h, w = src_shape
    x0 = float(src_transform.c)
    y1 = float(src_transform.f)
    x1 = x0 + w * float(src_transform.a)
    y0 = y1 + h * float(src_transform.e)
    bredd_m = abs(x1 - x0)
    hojd_m = abs(y1 - y0)
    ny_w = max(1, int(math.ceil(bredd_m / skala_m - 1e-9)))
    ny_h = max(1, int(math.ceil(hojd_m / skala_m - 1e-9)))
    ny_transform = Affine(skala_m, 0.0, min(x0, x1), 0.0, -skala_m, max(y0, y1))
    return (ny_h, ny_w), ny_transform


def _las_vattenmask_pa_rutnat(
    mask_fil: Path,
    *,
    dst_shape: tuple[int, int],
    dst_transform: Affine,
    dst_crs: CRS,
) -> NDArray[np.bool_]:
    with rasterio.open(mask_fil) as src:
        if src.crs is None:
            raise ValueError(f"Vattenmasken saknar CRS: {mask_fil}")
        ra = src.read(1).astype(np.int32)
        src_crs = CRS.from_user_input(src.crs)
        if (
            ra.shape == dst_shape
            and src.transform == dst_transform
            and src_crs == dst_crs
        ):
            alignad = ra
        else:
            alignad = np.zeros(dst_shape, dtype=np.int32)
            reproject(
                source=ra,
                destination=alignad,
                src_transform=src.transform,
                src_crs=src_crs,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                resampling=Resampling.nearest,
            )
    return np.isin(alignad, list(VATTEN_MARKSLAG_KODER))


def _berakna_troskel_matt(
    floden: NDArray[np.float64],
    ref: NDArray[np.float64],
    troskel_m: float,
    cell_yta_m2: float,
) -> dict[str, Any]:
    vat_f = floden >= troskel_m
    vat_r = ref >= troskel_m
    tp = int(np.count_nonzero(vat_f & vat_r))
    fp = int(np.count_nonzero(vat_f & ~vat_r))
    fn = int(np.count_nonzero(~vat_f & vat_r))
    union = tp + fp + fn
    iou = (tp / union) if union > 0 else None
    precision = (tp / (tp + fp)) if (tp + fp) > 0 else None
    recall = (tp / (tp + fn)) if (tp + fn) > 0 else None
    return {
        "troskel_m": round(float(troskel_m), 4),
        "tp_celler": tp,
        "fp_celler": fp,
        "fn_celler": fn,
        "iou_vat": _rent_tal(iou),
        "precision_vat": _rent_tal(precision),
        "recall_vat": _rent_tal(recall),
        "vat_yta_floden_m2": float(np.count_nonzero(vat_f)) * cell_yta_m2,
        "vat_yta_ref_m2": float(np.count_nonzero(vat_r)) * cell_yta_m2,
    }


def jamfor_rastrer(
    floden_fil: Path,
    ref_fil: Path,
    *,
    mask_vatten_fil: Path | None = None,
    troskel_m: float = 0.10,
    skala_m: float | None = None,
) -> dict[str, Any]:
    """Deterministisk cell-för-cell-jämförelse mellan Floden-raster och referensraster."""
    if not math.isfinite(troskel_m) or troskel_m < 0.0:
        raise ValueError(f"troskel_m måste vara >= 0, fick {troskel_m!r}.")
    if skala_m is not None and (not math.isfinite(skala_m) or skala_m <= 0.0):
        raise ValueError(f"skala_m måste vara > 0, fick {skala_m!r}.")

    f_ra, f_tr, f_crs, f_res = _las_raster_float(floden_fil)
    r_ra, r_tr, r_crs, r_res = _las_raster_float(ref_fil)

    if skala_m is not None:
        dst_shape, dst_tr = _bygg_skalat_rutnat(f_ra.shape, f_tr, skala_m)
        dst_crs = f_crs
        f_resamp = Resampling.average if skala_m > f_res * 1.01 else Resampling.bilinear
        r_resamp = Resampling.average if skala_m > r_res * 1.01 else Resampling.bilinear
        f_arr = _omprojicera_till_rutnat(
            f_ra, f_tr, f_crs, dst_shape=dst_shape, dst_transform=dst_tr, dst_crs=dst_crs, resampling=f_resamp
        )
        r_arr = _omprojicera_till_rutnat(
            r_ra, r_tr, r_crs, dst_shape=dst_shape, dst_transform=dst_tr, dst_crs=dst_crs, resampling=r_resamp
        )
        cell_m = skala_m
    else:
        dst_shape, dst_tr, dst_crs = f_ra.shape, f_tr, f_crs
        f_arr = f_ra
        if r_ra.shape == dst_shape and r_tr == dst_tr and r_crs == dst_crs:
            r_arr = r_ra
        else:
            r_resamp = Resampling.average if f_res > r_res * 1.01 else Resampling.bilinear
            r_arr = _omprojicera_till_rutnat(
                r_ra, r_tr, r_crs, dst_shape=dst_shape, dst_transform=dst_tr, dst_crs=dst_crs, resampling=r_resamp
            )
        cell_m = f_res

    giltig = np.isfinite(f_arr) & np.isfinite(r_arr)
    maskerade_vattenceller = 0
    if mask_vatten_fil is not None:
        vmask = _las_vattenmask_pa_rutnat(
            mask_vatten_fil, dst_shape=dst_shape, dst_transform=dst_tr, dst_crs=dst_crs
        )
        maskerade_vattenceller = int(np.count_nonzero(giltig & vmask))
        giltig = giltig & ~vmask

    n_jamforda = int(np.count_nonzero(giltig))
    cell_yta_m2 = float(cell_m * cell_m)

    if n_jamforda == 0:
        return {
            "antal_celler_jamforda": 0,
            "maskerade_vattenceller": maskerade_vattenceller,
            "cellstorlek_m": cell_m,
            "primar_troskel_m": troskel_m,
            "mae_m": None,
            "rmse_m": None,
            "bias_m": None,
            "p95_abs_fel_m": None,
            "max_abs_fel_m": None,
            "iou_vat": None,
            "precision_vat": None,
            "recall_vat": None,
            "vat_yta_floden_m2": 0.0,
            "vat_yta_ref_m2": 0.0,
            "trosklar": [],
        }

    fv = f_arr[giltig]
    rv = r_arr[giltig]
    diff = fv - rv
    abs_diff = np.abs(diff)

    alla_trosklar = sorted({round(float(t), 4) for t in (*STANDARD_TROSKLAR_M, troskel_m)})
    troskel_lista = [_berakna_troskel_matt(fv, rv, t, cell_yta_m2) for t in alla_trosklar]
    primar = _berakna_troskel_matt(fv, rv, troskel_m, cell_yta_m2)

    return {
        "antal_celler_jamforda": n_jamforda,
        "maskerade_vattenceller": maskerade_vattenceller,
        "cellstorlek_m": cell_m,
        "primar_troskel_m": troskel_m,
        "mae_m": float(np.mean(abs_diff)),
        "rmse_m": float(np.sqrt(np.mean(diff * diff))),
        "bias_m": float(np.mean(diff)),
        "p95_abs_fel_m": float(np.percentile(abs_diff, 95.0)),
        "max_abs_fel_m": float(np.max(abs_diff)),
        "iou_vat": primar["iou_vat"],
        "precision_vat": primar["precision_vat"],
        "recall_vat": primar["recall_vat"],
        "vat_yta_floden_m2": primar["vat_yta_floden_m2"],
        "vat_yta_ref_m2": primar["vat_yta_ref_m2"],
        "trosklar": troskel_lista,
    }


def las_kontrollpunkter(sokvag: Path, *, omrade: str | None = None) -> list[dict[str, Any]]:
    """Läser kontrollpunkter ur JSON (`punkter`-lista) eller CSV."""
    suffix = sokvag.suffix.lower()
    if suffix == ".json":
        ra = json.loads(sokvag.read_text(encoding="utf-8"))
        if isinstance(ra, Mapping) and isinstance(ra.get("punkter"), list):
            punkter = list(ra["punkter"])
        elif isinstance(ra, list):
            punkter = list(ra)
        else:
            raise ValueError(f"Ogiltigt kontrollpunkts-JSON i {sokvag}: förväntade lista eller 'punkter'.")
    elif suffix == ".csv":
        punkter = []
        with sokvag.open("r", encoding="utf-8", newline="") as fh:
            for rad in csv.DictReader(fh):
                punkter.append(
                    {
                        "id": str(rad["id"]).strip(),
                        "omrade": str(rad.get("omrade", "")).strip(),
                        "namn": str(rad.get("namn", rad["id"])).strip(),
                        "typ": str(rad.get("typ", "skyfallsdjup")).strip() or "skyfallsdjup",
                        "epsg": int(rad.get("epsg") or 3007),
                        "e_m": float(rad["e_m"]),
                        "n_m": float(rad["n_m"]),
                        "ref_djup_m": _valfritt_tal(rad.get("ref_djup_m")),
                        "ref_djup_min_m": _valfritt_tal(rad.get("ref_djup_min_m")),
                        "ref_djup_max_m": _valfritt_tal(rad.get("ref_djup_max_m")),
                    }
                )
    else:
        raise ValueError(f"Okänt filformat för --ref-punkter: {sokvag} (stöd: .json, .csv).")

    ut: list[dict[str, Any]] = []
    for p in punkter:
        if omrade is not None and str(p.get("omrade", "")).lower() != omrade.lower():
            continue
        if "e_m" not in p or "n_m" not in p or "id" not in p:
            raise ValueError(f"Kontrollpunkt saknar id/e_m/n_m: {p!r}")
        ut.append(dict(p))
    return ut


def _bedom_mot_intervall(
    varde: float | None,
    *,
    ref_m: float | None,
    ref_min_m: float | None,
    ref_max_m: float | None,
) -> tuple[bool | None, float | None]:
    """Returnerar (inom_intervall, avvikelse_m). Avvikelse är 0.0 inom intervall."""
    if varde is None:
        return None, None
    lo = ref_min_m if ref_min_m is not None else ref_m
    hi = ref_max_m if ref_max_m is not None else ref_m
    if lo is None and hi is None:
        return None, None
    if lo is not None and varde < lo:
        return False, float(varde - lo)
    if hi is not None and varde > hi:
        return False, float(varde - hi)
    # Inom intervallet: om exakt punktvärde finns, returnera signerad diff mot ref_m; annars 0.0
    if ref_m is not None and ref_min_m is None and ref_max_m is None:
        return True, float(varde - ref_m)
    return True, 0.0


def _fonster_max(arr: NDArray[np.float64], rad: int, kol: int, halvbredd: int) -> float | None:
    h, w = arr.shape
    r0 = max(0, rad - halvbredd)
    r1 = min(h, rad + halvbredd + 1)
    c0 = max(0, kol - halvbredd)
    c1 = min(w, kol + halvbredd + 1)
    sub = arr[r0:r1, c0:c1]
    giltiga = sub[np.isfinite(sub)]
    if giltiga.size == 0:
        return None
    return float(np.max(giltiga))


def jamfor_kontrollpunkter(
    floden_fil: Path,
    punkter_fil: Path,
    *,
    omrade: str | None = None,
    mask_vatten_fil: Path | None = None,
) -> dict[str, Any]:
    """Utvärderar kontrollpunkter (cell, 3x3 max, 5x5 max) mot Flodens raster."""
    f_arr, f_tr, f_crs, _ = _las_raster_float(floden_fil)
    vmask: NDArray[np.bool_] | None = None
    if mask_vatten_fil is not None:
        vmask = _las_vattenmask_pa_rutnat(
            mask_vatten_fil, dst_shape=f_arr.shape, dst_transform=f_tr, dst_crs=f_crs
        )

    punkter = las_kontrollpunkter(punkter_fil, omrade=omrade)
    transformers: dict[int, Transformer] = {}
    h, w = f_arr.shape

    ut_punkter: list[dict[str, Any]] = []
    n_djup_utvarderade = 0
    n_inom_cell = 0
    n_inom_3x3 = 0
    n_inom_5x5 = 0
    abs_avvikelser: list[float] = []

    for p in punkter:
        src_epsg = int(p.get("epsg", 3007))
        if src_epsg not in transformers:
            transformers[src_epsg] = Transformer.from_crs(
                CRS.from_epsg(src_epsg), f_crs, always_xy=True
            )
        e_in, n_in = float(p["e_m"]), float(p["n_m"])
        x_dst, y_dst = transformers[src_epsg].transform(e_in, n_in)
        row_i, col_i = rasterio.transform.rowcol(f_tr, float(x_dst), float(y_dst))
        row, col = int(row_i), int(col_i)
        utanfor = not (0 <= row < h and 0 <= col < w)

        cell_v: float | None = None
        max_3x3: float | None = None
        max_5x5: float | None = None
        i_vatten: bool | None = None
        if not utanfor:
            cell_v = _rent_tal(float(f_arr[row, col]))
            max_3x3 = _fonster_max(f_arr, row, col, 1)
            max_5x5 = _fonster_max(f_arr, row, col, 2)
            if vmask is not None:
                i_vatten = bool(vmask[row, col])

        typ = str(p.get("typ", "skyfallsdjup"))
        ref_m = _valfritt_tal(p.get("ref_djup_m"))
        ref_min_m = _valfritt_tal(p.get("ref_djup_min_m"))
        ref_max_m = _valfritt_tal(p.get("ref_djup_max_m"))

        inom_cell: bool | None = None
        inom_3x3: bool | None = None
        inom_5x5: bool | None = None
        avv_cell: float | None = None
        avv_3x3: float | None = None

        if typ == "skyfallsdjup" and not utanfor:
            inom_cell, avv_cell = _bedom_mot_intervall(
                cell_v, ref_m=ref_m, ref_min_m=ref_min_m, ref_max_m=ref_max_m
            )
            inom_3x3, avv_3x3 = _bedom_mot_intervall(
                max_3x3, ref_m=ref_m, ref_min_m=ref_min_m, ref_max_m=ref_max_m
            )
            inom_5x5, _ = _bedom_mot_intervall(
                max_5x5, ref_m=ref_m, ref_min_m=ref_min_m, ref_max_m=ref_max_m
            )
            if inom_cell is not None:
                n_djup_utvarderade += 1
                if inom_cell:
                    n_inom_cell += 1
                if inom_3x3:
                    n_inom_3x3 += 1
                if inom_5x5:
                    n_inom_5x5 += 1
                if avv_cell is not None:
                    abs_avvikelser.append(abs(avv_cell))

        ut_punkter.append(
            {
                "id": str(p["id"]),
                "omrade": str(p.get("omrade", "")),
                "namn": str(p.get("namn", p["id"])),
                "typ": typ,
                "kalla_epsg": src_epsg,
                "kalla_e_m": e_in,
                "kalla_n_m": n_in,
                "intern_e_m": round(float(x_dst), 3),
                "intern_n_m": round(float(y_dst), 3),
                "rad": row if not utanfor else None,
                "kol": col if not utanfor else None,
                "utanfor_raster": utanfor,
                "i_vattenmask": i_vatten,
                "ref_djup_m": ref_m,
                "ref_djup_min_m": ref_min_m,
                "ref_djup_max_m": ref_max_m,
                "floden_cell_m": cell_v,
                "floden_max_3x3_m": max_3x3,
                "floden_max_5x5_m": max_5x5,
                "avvikelse_cell_m": _rent_tal(avv_cell),
                "avvikelse_3x3_m": _rent_tal(avv_3x3),
                "inom_intervall": inom_cell,
                "inom_intervall_3x3": inom_3x3,
                "inom_intervall_5x5": inom_5x5,
            }
        )

    return {
        "antal_punkter_totalt": len(ut_punkter),
        "antal_djup_utvarderade": n_djup_utvarderade,
        "antal_inom_intervall_cell": n_inom_cell,
        "antal_inom_intervall_3x3": n_inom_3x3,
        "antal_inom_intervall_5x5": n_inom_5x5,
        "medel_abs_avvikelse_cell_m": (
            float(np.mean(abs_avvikelser)) if abs_avvikelser else None
        ),
        "punkter": ut_punkter,
    }


def _fmt_tal(v: float | None, dec: int = 4, enhet: str = "") -> str:
    if v is None:
        return "–"
    return f"{v:.{dec}f}{enhet}"


def formatera_rapport_md(dok: Mapping[str, Any]) -> str:
    """Formaterar K37-jämförelsedokumentet som läsbar Markdown-rapport."""
    fall = dok["delar"]["skyfall"]["fall"][0]
    det = fall["detaljer"]
    rader: list[str] = [
        f"# T09b Skyfallsjämförelse — `{fall['id']}`",
        "",
        f"- **Format:** `{dok['format']}`",
        f"- **Skapad (UTC):** `{dok['skapad_utc']}`",
        f"- **Kodversion:** `{dok['kodversion']}`",
    ]
    if det.get("omrade"):
        rader.append(f"- **Område:** `{det['omrade']}`")
    rader.append("")

    rj = det.get("raster_jamforelse")
    if rj is not None:
        rader.extend(
            [
                "## Rasterjämförelse (Floden vs referensraster)",
                "",
                f"- **Jämförda celler:** `{rj['antal_celler_jamforda']}` (cellstorlek `{rj['cellstorlek_m']:g} m`, maskerade vattenceller: `{rj['maskerade_vattenceller']}`)",
                f"- **MAE:** `{_fmt_tal(rj['mae_m'], 4, ' m')}` | **RMSE:** `{_fmt_tal(rj['rmse_m'], 4, ' m')}` | **Bias:** `{_fmt_tal(rj['bias_m'], 4, ' m')}` | **P95 |fel|:** `{_fmt_tal(rj['p95_abs_fel_m'], 4, ' m')}`",
                f"- **Våtmarks-IoU (≥ {rj['primar_troskel_m']:g} m):** `{_fmt_tal(rj['iou_vat'], 4)}` (precision `{_fmt_tal(rj['precision_vat'], 4)}`, recall `{_fmt_tal(rj['recall_vat'], 4)}`)",
                f"- **Våt yta (Floden / Referens):** `{rj['vat_yta_floden_m2']:.1f} m²` / `{rj['vat_yta_ref_m2']:.1f} m²`",
                "",
            ]
        )
        if rj.get("trosklar"):
            rader.extend(
                [
                    "| Tröskel (m) | IoU | Precision | Recall | Våt yta Floden (m²) | Våt yta Ref (m²) |",
                    "|---:|---:|---:|---:|---:|---:|",
                ]
            )
            for t in rj["trosklar"]:
                rader.append(
                    f"| {t['troskel_m']:.2f} | {_fmt_tal(t['iou_vat'], 4)} | "
                    f"{_fmt_tal(t['precision_vat'], 4)} | {_fmt_tal(t['recall_vat'], 4)} | "
                    f"{t['vat_yta_floden_m2']:.1f} | {t['vat_yta_ref_m2']:.1f} |"
                )
            rader.append("")

    pj = det.get("punkt_jamforelse")
    if pj is not None:
        rader.extend(
            [
                "## Kontrollpunkter",
                "",
                f"- **Utvärderade djuppunkter:** `{pj['antal_djup_utvarderade']}` / `{pj['antal_punkter_totalt']}` "
                f"(inom intervall: cell `{pj['antal_inom_intervall_cell']}`, 3×3 `{pj['antal_inom_intervall_3x3']}`, 5×5 `{pj['antal_inom_intervall_5x5']}`)",
                "",
                "| ID | Område | Ref (m) | Cell (m) | 3×3 max (m) | 5×5 max (m) | Inom intervall |",
                "|---|---|---|---:|---:|---:|---|",
            ]
        )
        for p in pj["punkter"]:
            if p["ref_djup_min_m"] is not None or p["ref_djup_max_m"] is not None:
                lo_s = f"{p['ref_djup_min_m']:.2f}" if p["ref_djup_min_m"] is not None else "–"
                hi_s = f"{p['ref_djup_max_m']:.2f}" if p["ref_djup_max_m"] is not None else "∞"
                ref_s = f"{lo_s}–{hi_s}"
            elif p["ref_djup_m"] is not None:
                ref_s = f"{p['ref_djup_m']:.2f}"
            else:
                ref_s = f"({p['typ']})"
            if p["utanfor_raster"]:
                status_s = "utanför raster"
            elif p["inom_intervall"] is True:
                status_s = "ja"
            elif p["inom_intervall"] is False:
                status_s = "nej"
            else:
                status_s = "info"
            rader.append(
                f"| `{p['id']}` | `{p['omrade']}` | {ref_s} | "
                f"{_fmt_tal(p['floden_cell_m'], 3)} | {_fmt_tal(p['floden_max_3x3_m'], 3)} | "
                f"{_fmt_tal(p['floden_max_5x5_m'], 3)} | {status_s} |"
            )
        rader.append("")

    rader.extend(["## Indata-proveniens (SHA-256)", ""])
    for nyckel, post in det["proveniens"]["indata_sha256"].items():
        rader.append(f"- `{nyckel}`: `{post['fil']}` (`{post['sha256']}`)")
    rader.append("")
    return "\n".join(rader)


def kor_jamforelse(
    *,
    floden_raster: Path,
    ref_raster: Path | None = None,
    ref_punkter: Path | None = None,
    mask_vatten: Path | None = None,
    manifest: Path | None = None,
    troskel_m: float = 0.10,
    skala_m: float | None = None,
    omrade: str | None = None,
) -> dict[str, Any]:
    """Kör komplett T09b-jämförelse och returnerar ett K37-validerat dokument."""
    if ref_raster is None and ref_punkter is None:
        raise ValueError("Ange minst en av --ref-raster eller --ref-punkter.")

    tracemalloc.start()
    t0 = time.perf_counter()
    try:
        raster_res: dict[str, Any] | None = None
        if ref_raster is not None:
            raster_res = jamfor_rastrer(
                floden_raster,
                ref_raster,
                mask_vatten_fil=mask_vatten,
                troskel_m=troskel_m,
                skala_m=skala_m,
            )

        punkt_res: dict[str, Any] | None = None
        if ref_punkter is not None:
            punkt_res = jamfor_kontrollpunkter(
                floden_raster,
                ref_punkter,
                omrade=omrade,
                mask_vatten_fil=mask_vatten,
            )

        manifest_meta: dict[str, Any] | None = None
        if manifest is not None:
            manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(manifest_data, Mapping):
                manifest_meta = {
                    "kodversion": manifest_data.get("kodversion"),
                    "skapad_utc": manifest_data.get("skapad_utc"),
                    "parametrar": manifest_data.get("parametrar"),
                }

        indata_sha: dict[str, dict[str, str]] = {
            "floden_raster": {"fil": floden_raster.name, "sha256": sha256_fil(floden_raster)},
        }
        for nyckel, fil in (
            ("ref_raster", ref_raster),
            ("ref_punkter", ref_punkter),
            ("mask_vatten", mask_vatten),
            ("manifest", manifest),
        ):
            if fil is not None:
                indata_sha[nyckel] = {"fil": fil.name, "sha256": sha256_fil(fil)}
    finally:
        _, topp_b = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    tid_s = time.perf_counter() - t0

    avvikelse: dict[str, Any] | None = None
    if raster_res is not None and raster_res["mae_m"] is not None:
        avvikelse = {
            "varde": float(raster_res["mae_m"]),
            "enhet": "m",
            "godkand": None,
        }
    elif punkt_res is not None and punkt_res["medel_abs_avvikelse_cell_m"] is not None:
        avvikelse = {
            "varde": float(punkt_res["medel_abs_avvikelse_cell_m"]),
            "enhet": "m",
            "godkand": (
                punkt_res["antal_inom_intervall_cell"] == punkt_res["antal_djup_utvarderade"]
                if punkt_res["antal_djup_utvarderade"] > 0
                else None
            ),
        }

    fall_id = f"t09b-{omrade}" if omrade else "t09b-skyfall"
    fall_post: dict[str, Any] = {
        "id": fall_id,
        "status": "ok",
        "tid_s": {"median": round(tid_s, 6)},
        "minne": {
            "rss_topp_mb": None,
            "tracemalloc_topp_mb": round(topp_b / (1024.0 * 1024.0), 3),
        },
        "avvikelse": avvikelse,
        "detaljer": {
            "omrade": omrade,
            "proveniens": {
                "indata_sha256": indata_sha,
                "manifest": manifest_meta,
            },
            "raster_jamforelse": raster_res,
            "punkt_jamforelse": punkt_res,
        },
    }

    return skapa_dokument(
        kalla="server",
        kodversion=hamta_kodversion(),
        maskin=maskininfo(),
        installning={
            "verktyg": "jamfor_smhi_skyfall",
            "omrade": omrade,
            "troskel_m": troskel_m,
            "skala_m": skala_m,
            "har_ref_raster": ref_raster is not None,
            "har_ref_punkter": ref_punkter is not None,
            "har_vattenmask": mask_vatten is not None,
        },
        delar={
            "skyfall": {
                "status": "kord",
                "fall": [fall_post],
            }
        },
        anmarkning="T09b deterministisk jämförelse av Flodens skyfallsresultat mot referensdata.",
    )


def main(argv: Sequence[str] | None = None) -> int:
    ap = bygg_parser()
    args = ap.parse_args(argv)

    if args.ref_raster is None and args.ref_punkter is None:
        print(
            "Fel: ange minst en av --ref-raster eller --ref-punkter.",
            file=sys.stderr,
        )
        return 2

    for flagga, fil in (
        ("--floden-raster", args.floden_raster),
        ("--ref-raster", args.ref_raster),
        ("--ref-punkter", args.ref_punkter),
        ("--mask-vatten", args.mask_vatten),
        ("--manifest", args.manifest),
    ):
        if fil is not None and not fil.is_file():
            print(f"Fel: filen för {flagga} saknas: {fil}", file=sys.stderr)
            return 2

    try:
        dok = kor_jamforelse(
            floden_raster=args.floden_raster,
            ref_raster=args.ref_raster,
            ref_punkter=args.ref_punkter,
            mask_vatten=args.mask_vatten,
            manifest=args.manifest,
            troskel_m=args.troskel_m,
            skala_m=args.skala_m,
            omrade=args.omrade,
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError, KeyError) as exc:
        print(f"Fel: {exc}", file=sys.stderr)
        return 2

    rapport_md = formatera_rapport_md(dok)

    if args.ut_json is not None:
        args.ut_json.parent.mkdir(parents=True, exist_ok=True)
        args.ut_json.write_text(
            json.dumps(dok, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    if args.ut_md is not None:
        args.ut_md.parent.mkdir(parents=True, exist_ok=True)
        args.ut_md.write_text(rapport_md, encoding="utf-8")

    print(rapport_md)
    if args.ut_json is not None:
        print(f"JSON skriven till: {args.ut_json}")
    if args.ut_md is not None:
        print(f"Markdown skriven till: {args.ut_md}")

    det = dok["delar"]["skyfall"]["fall"][0]["detaljer"]
    if args.krav_godkand:
        pj = det.get("punkt_jamforelse")
        if (
            pj is not None
            and pj["antal_djup_utvarderade"] > 0
            and pj["antal_inom_intervall_cell"] < pj["antal_djup_utvarderade"]
        ):
            return 1
        rj = det.get("raster_jamforelse")
        if rj is not None and rj["antal_celler_jamforda"] == 0:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
