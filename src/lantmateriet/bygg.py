"""Bygg K1 v2-rutor för ett provområde ur hämtad laser, ortofoto och byggnader.

Tar manifestet från `floden data lantmateriet` och områdets polygon, delar området i K1-rutor på ett
fast nät (`mm_<E>_<N>`), klipper laserpunkterna per ruta (LAZ via `tools/lasclip`, LAS direkt) och
kör markmodellens egen kedja (`markmodell.pipeline.bygg_markmodell_ruta`). Receptet pekar på
manifestets sha256 (roll `lantmateriet_manifest`) och på varje laserfils sha256.

Lantmäteriets öppna laser har bara markklasserna 1, 2, 7, 9, 17 och 18, alltså ingen klass 6
(byggnad). För att byggnadernas höjd ändå ska kunna sättas ur laser räknas oklassade punkter (1)
inne i en byggnadspolygon som tak (klass 6) i höjdsättningen; det går att stänga av
(`tak_ur_laser=False`) och står i varningarna och i `omrade_bygg.json`.
"""

from __future__ import annotations

import json
import math
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from numpy.typing import NDArray

from data_inlasning.cache import DataInlasningFel
from data_inlasning.lantmateriet import las_las_punktmoln
from data_inlasning.lasclip import hitta_lasclip, klipp_laser
from gemensamt.hash import sha256_bytes, sha256_fil
from gemensamt.manifest import FilPost, las_manifest
from gemensamt.manifest import skriv_manifest as skriv_run_manifest
from lantmateriet.hamta import KLIPPT_BYGGNADER, MANIFEST_NAMN, las_manifest_lm
from lantmateriet.omrade import Omrade, rutor_for_omrade
from lantmateriet.stac import ROLL_LASER, ROLL_ORTO, wgs84_bbox
from markmodell.byggnadsregister import ByggnadsKandidat
from markmodell.hinder_oppningar import BYGGNAD, Byggnad, brann_in_byggnader
from markmodell.k1_skrivare import skriv_geotiff
from markmodell.markslagskallor import belagd_mask_for_ruta
from markmodell.ortofoto import klassificera_ortofoto, kombinera_ortofoto_med_markmodell
from markmodell.pipeline import PipelineFel, bygg_markmodell_ruta
from markmodell.recept import KallPost, las_recept
from markmodell.rutnat import Ruta

LAS_BYGGNADSKLASS = 6
LAS_OKLASSAD = 1
SAMMANFATTNING = "omrade_bygg.json"


@dataclass
class ByggResultat:
    ut: Path
    rutor: list[str] = field(default_factory=list)
    hoppade: list[str] = field(default_factory=list)
    utan_laser: list[str] = field(default_factory=list)
    varningar: list[str] = field(default_factory=list)
    sammanfattning: Path | None = None


def _punkter_ur_fil(
    fil: Path, bbox_3011: tuple[float, float, float, float], epsg: int, binar: str | Path | None
) -> (
    tuple[
        NDArray[np.float64],
        NDArray[np.float64],
        NDArray[np.float64],
        NDArray[np.uint8],
        NDArray[np.uint16] | None,
    ]
    | None
):
    """Punkter inom `bbox_3011` ur en LAS/LAZ-fil (EPSG:3011), eller `None` om inga."""
    from pyproj import Transformer

    e0, n0, e1, n1 = bbox_3011
    if epsg == 3011:
        bb = bbox_3011
    else:
        t = Transformer.from_crs(3011, epsg, always_xy=True)
        xs, ys = t.transform([e0, e1, e0, e1], [n0, n0, n1, n1])
        bb = (float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys)))
    if fil.suffix.lower() == ".laz":
        with tempfile.TemporaryDirectory(prefix="floden-lm-") as tmp:
            ut = Path(tmp) / "klipp.las"
            antal = klipp_laser(fil, ut, bb, binar=binar)
            if antal == 0 or not ut.is_file():
                return None
            moln = las_las_punktmoln(ut, kall_epsg=epsg)
    else:
        moln = las_las_punktmoln(fil, kall_epsg=epsg)
    m = (moln.e_m >= e0) & (moln.e_m <= e1) & (moln.n_m >= n0) & (moln.n_m <= n1)
    if not bool(np.any(m)):
        return None
    inten = moln.intensitet[m] if moln.intensitet is not None else None
    return moln.e_m[m], moln.n_m[m], moln.z_rh2000[m], moln.klass[m], inten


def markera_tak(
    e: NDArray[np.float64],
    n: NDArray[np.float64],
    klass: NDArray[np.uint8],
    kandidater: list[ByggnadsKandidat],
) -> tuple[NDArray[np.uint8], int]:
    """Oklassade punkter (1) inne i en byggnadspolygon blir klass 6 (tak).

    Returnerar (klasser, antal punkter som ändrades).
    """
    ut = klass.copy()
    antal = 0
    oklassad = klass == LAS_OKLASSAD
    for k in kandidater:
        xs = [p[0] for p in k.polygon]
        ys = [p[1] for p in k.polygon]
        grov = oklassad & (e >= min(xs)) & (e <= max(xs)) & (n >= min(ys)) & (n <= max(ys))
        idx = np.flatnonzero(grov)
        if idx.size == 0:
            continue
        pol = shapely.Polygon(k.polygon, list(k.hal))
        inne = shapely.contains_xy(pol, e[idx], n[idx])
        valda = idx[inne]
        ut[valda] = LAS_BYGGNADSKLASS
        antal += int(valda.size)
    return ut, antal


def orto_for_ruta(filer: list[Path], ruta: Ruta) -> NDArray[np.uint8] | None:
    """RGB-bild (rader, kolumner, 3) i rutans rutnät ur GeoTIFF-filer, eller `None`."""
    import rasterio
    from pyproj import Transformer
    from rasterio.transform import Affine
    from rasterio.warp import Resampling, reproject

    rader, kol = ruta.form_rc
    ut = np.zeros((3, rader, kol), dtype=np.uint8)
    fylld = np.zeros((rader, kol), dtype=bool)
    e_v, n_v = ruta.cell_centra_en()
    ee, nn = np.meshgrid(e_v, n_v)
    transform = Affine(*ruta.affine_parametrar)
    for fil in filer:
        with rasterio.open(fil) as src:
            if src.count < 3 or src.crs is None:
                continue
            epsg = src.crs.to_epsg() or 3006
            if epsg == 3011:
                x, y = ee, nn
            else:
                x, y = Transformer.from_crs(3011, epsg, always_xy=True).transform(ee, nn)
            b = src.bounds
            tacker = (x >= b.left) & (x <= b.right) & (y >= b.bottom) & (y <= b.top) & ~fylld
            if not bool(np.any(tacker)):
                continue
            tmp = np.zeros((3, rader, kol), dtype=np.uint8)
            reproject(
                source=rasterio.band(src, [1, 2, 3]),
                destination=tmp,
                dst_transform=transform,
                dst_crs="EPSG:3011",
                resampling=Resampling.average,
            )
            for i in range(3):
                ut[i][tacker] = tmp[i][tacker]
            fylld |= tacker
    if not bool(np.any(fylld)):
        return None
    return np.asarray(np.moveaxis(ut, 0, -1), dtype=np.uint8)


def _param_sha(params: dict[str, Any]) -> str:
    return sha256_bytes(
        json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    )


def _redan_byggd(katalog: Path, manifest_sha: str, param_sha: str) -> bool:
    rp = katalog / "recept.json"
    if not rp.is_file() or not (katalog / "mark.tif").is_file():
        return False
    try:
        kallor = {(k.roll, k.sha256) for k in las_recept(rp).kallor}
    except Exception:
        return False
    return ("lantmateriet_manifest", manifest_sha) in kallor and (
        "omrade_bygg_parametrar",
        param_sha,
    ) in kallor


def bygg_omrade(
    omrade: Omrade,
    data_mapp: str | Path,
    ut: str | Path,
    *,
    byggnader_fil: str | Path | None = None,
    byggnader_lager: str | None = None,
    byggnader_epsg: int | None = None,
    ruta_storlek_m: float = 500.0,
    en_ruta: bool = False,
    overlapp_m: float = 0.0,
    upplosning_m: float = 1.0,
    punktmarginal_m: float = 25.0,
    anvand_orto: bool = True,
    belagd_yta: bool = False,
    tak_ur_laser: bool = True,
    tvinga: bool = False,
    verifiera: bool = False,
    lasclip_binar: str | Path | None = None,
    skriv: Callable[[str], None] = print,
) -> ByggResultat:
    """Bygger K1 v2-rutor i `ut/mm_<E>_<N>/`. Se modulens text."""
    from markmodell.byggnadskallor import las_byggnadskalla

    data = Path(data_mapp)
    mal = Path(ut)
    manifest = las_manifest_lm(data)
    manifest_sha = sha256_fil(data / MANIFEST_NAMN)
    filer: list[dict[str, Any]] = [f for f in manifest.get("filer", []) if isinstance(f, dict)]
    laser = [f for f in filer if f.get("roll") == ROLL_LASER]
    orto = [f for f in filer if f.get("roll") == ROLL_ORTO] if anvand_orto else []
    if not laser:
        raise DataInlasningFel(
            f"Manifestet i {data} har ingen laser. Hämta med: floden data lantmateriet --laser ..."
        )
    for f in laser + orto:
        p = data / str(f["fil"])
        if not p.is_file():
            raise DataInlasningFel(
                f"Filen {p} saknas. Kör hämtningen igen (hoppar över det som finns)."
            )
        if p.stat().st_size != f["storlek"]:
            raise DataInlasningFel(f"{p} har inte storleken i manifestet; kör hämtningen igen.")
        if verifiera and sha256_fil(p) != f["sha256"]:
            raise DataInlasningFel(f"{p}: sha256 stämmer inte med manifestet.")
    if any(str(f["fil"]).lower().endswith(".laz") for f in laser):
        hitta_lasclip(lasclip_binar)  # tydligt fel med byggkommando om den saknas

    res = ByggResultat(ut=mal)
    kandidater: list[ByggnadsKandidat] = []
    bygg_sha: str | None = None
    kalla_bygg: Path | None = Path(byggnader_fil) if byggnader_fil else None
    if kalla_bygg is None and (data / "byggnader" / KLIPPT_BYGGNADER).is_file():
        kalla_bygg = data / "byggnader" / KLIPPT_BYGGNADER
    if kalla_bygg is not None:
        bk = las_byggnadskalla(kalla_bygg, lager=byggnader_lager, fran_epsg=byggnader_epsg)
        kandidater = list(bk.kandidater)
        bygg_sha = bk.sha256
        res.varningar.extend(bk.varningar)
        skriv(f"Byggnader: {len(kandidater)} ur {kalla_bygg.name}")
    else:
        res.varningar.append(
            "Inga byggnader: varken --byggnader FIL eller hämtade byggnader. Marken byggs utan hus."
        )
        skriv("OBS: inga byggnader (se varningar)")
    if tak_ur_laser and kandidater:
        res.varningar.append(
            "Byggnadshöjd: Lantmäteriets laser saknar klass 6; oklassade punkter inne i byggnadens "
            "polygon räknas som tak (90:e percentilen, minst 0,5 m över marken). Träd över "
            "taket kan "
            "ge för höga hus. Byggnader utan sådana punkter får standardhöjd."
        )

    params: dict[str, Any] = {
        "ruta_storlek_m": ruta_storlek_m,
        "en_ruta": en_ruta,
        "overlapp_m": overlapp_m,
        "upplosning_m": upplosning_m,
        "punktmarginal_m": punktmarginal_m,
        "orto": [f["sha256"] for f in orto],
        "belagd_yta": belagd_yta,
        "tak_ur_laser": tak_ur_laser,
        "byggnader": bygg_sha,
        "omrade": omrade.sha256,
    }
    param_sha = _param_sha(params)
    bb_kand = [
        (
            min(p[0] for p in k.polygon),
            min(p[1] for p in k.polygon),
            max(p[0] for p in k.polygon),
            max(p[1] for p in k.polygon),
        )
        for k in kandidater
    ]

    if en_ruta:
        e0, n0, e1, n1 = omrade.bbox
        e_min, n_min = math.floor(e0), math.floor(n0)
        sida = max(e1 - e_min, n1 - n_min)
        storlek = math.ceil(sida / upplosning_m) * upplosning_m
        rutlista = [Ruta(float(e_min), float(n_min), storlek, overlapp_m, upplosning_m)]
        skriv(f"En ruta om {storlek:g} m, upplösning {upplosning_m:g} m")
    else:
        rutlista = [
            Ruta(e, n, ruta_storlek_m, overlapp_m, upplosning_m)
            for e, n in rutor_for_omrade(omrade.polygon, ruta_storlek_m)
        ]
        skriv(f"{len(rutlista)} rutor om {ruta_storlek_m:g} m, upplösning {upplosning_m:g} m")
    mal.mkdir(parents=True, exist_ok=True)
    for ruta in rutlista:
        katalog = mal / ruta.namn
        if not tvinga and _redan_byggd(katalog, manifest_sha, param_sha):
            skriv(f"{ruta.namn}: finns med samma indata, hoppar över")
            res.hoppade.append(ruta.namn)
            res.rutor.append(ruta.namn)
            continue
        yttre = ruta.yttre_bbox
        punkt_bb = (
            yttre[0] - punktmarginal_m,
            yttre[1] - punktmarginal_m,
            yttre[2] + punktmarginal_m,
            yttre[3] + punktmarginal_m,
        )
        ruta_wgs = wgs84_bbox(punkt_bb)
        delar: list[Any] = []
        anvanda: list[dict[str, Any]] = []
        for f in laser:
            bb = f.get("bbox_wgs84")
            if bb and (
                bb[2] < ruta_wgs[0]
                or bb[0] > ruta_wgs[2]
                or bb[3] < ruta_wgs[1]
                or bb[1] > ruta_wgs[3]
            ):
                continue
            pkt = _punkter_ur_fil(
                data / str(f["fil"]), punkt_bb, int(f.get("epsg") or 3006), lasclip_binar
            )
            if pkt is not None:
                delar.append(pkt)
                anvanda.append(f)
        if not delar:
            skriv(f"{ruta.namn}: inga laserpunkter, hoppar över")
            res.utan_laser.append(ruta.namn)
            res.varningar.append(f"{ruta.namn}: inga laserpunkter i rutan; rutan byggdes inte.")
            continue
        pe = np.concatenate([d[0] for d in delar])
        pn = np.concatenate([d[1] for d in delar])
        pz = np.concatenate([d[2] for d in delar])
        pk = np.concatenate([d[3] for d in delar])
        inten = (
            np.concatenate([d[4] for d in delar]) if all(d[4] is not None for d in delar) else None
        )
        ruta_kand = [
            k
            for k, b in zip(kandidater, bb_kand, strict=True)
            if not (b[2] < yttre[0] or b[0] > yttre[2] or b[3] < yttre[1] or b[1] > yttre[3])
        ]
        taken = 0
        if tak_ur_laser and ruta_kand:
            pk, taken = markera_tak(pe, pn, pk, ruta_kand)
        extra = [
            KallPost("lantmateriet_manifest", MANIFEST_NAMN, manifest_sha),
            KallPost("omrade_bygg_parametrar", "floden omrade bygg", param_sha),
            KallPost("omrade", omrade.fil.name, omrade.sha256),
            *(KallPost("laser_laz", str(f["fil"]), str(f["sha256"])) for f in anvanda),
            *(KallPost("ortofoto", str(f["fil"]), str(f["sha256"])) for f in orto),
        ]
        belagd = None
        belagd_inst: dict[str, float] | None = None
        if belagd_yta:
            if inten is None:
                raise PipelineFel("--belagd-yta kräver laser med intensitet.")
            from data_inlasning.lantmateriet import PunktMoln

            moln = PunktMoln(
                e_m=pe, n_m=pn, z_rh2000=pz, klass=pk, epsg=3011, kalla=ruta.namn, intensitet=inten
            )
            belagd, br = belagd_mask_for_ruta(moln, ruta)
            belagd_inst = br.installningar
        orto_bild = orto_for_ruta([data / str(f["fil"]) for f in orto], ruta) if orto else None
        bygg_mask: NDArray[np.bool_] | None = None
        orto_slag: NDArray[np.uint8] | None = None
        orto_konf: NDArray[np.float32] | None = None
        hus = [Byggnad(polygon=k.polygon, hal=k.hal) for k in ruta_kand]
        if orto_bild is not None:
            _, bs = brann_in_byggnader(
                np.zeros(ruta.form_rc, dtype=np.float32),
                np.zeros(ruta.form_rc, dtype=np.uint8),
                ruta,
                hus,
            )
            bygg_mask = bs == BYGGNAD
            kl = klassificera_ortofoto(orto_bild, byggnad_mask=bygg_mask)
            orto_slag, orto_konf = kl.markslag, kl.konfidens
        rr = bygg_markmodell_ruta(
            ruta,
            mal,
            mark_punkter_e=pe,
            mark_punkter_n=pn,
            mark_punkter_z=pz,
            laser_klasser=pk.astype(np.int32),
            byggnadskandidater=ruta_kand,
            byggnadshojd_fran_laser=True,
            belagd_yta_mask=belagd,
            belagd_installningar=belagd_inst,
            ortofoto_markslag=orto_slag,
            ortofoto_konfidens=orto_konf,
            extra_kallor=extra,
        )
        if orto_bild is not None:
            kl2 = klassificera_ortofoto(orto_bild, byggnad_mask=(rr.markslag == BYGGNAD))
            _, osak = kombinera_ortofoto_med_markmodell(rr.markslag, rr.osakerhet_z, kl2)
            skriv_geotiff(rr.katalog / "osakerhet.tif", osak, ruta, nodata=-9999.0)
            rm = las_manifest(rr.katalog / "run.json")
            rm.utdata = [
                FilPost(roll=q.roll, fil=q.fil, sha256=sha256_fil(rr.katalog / q.fil))
                for q in rm.utdata
            ]
            skriv_run_manifest(rm, rr.katalog / "run.json")
        for v in rr.varningar:
            res.varningar.append(f"{ruta.namn}: {v}")
        skriv(
            f"{ruta.namn}: {pe.size} punkter, {len(ruta_kand)} byggnader"
            + (f" ({taken} takpunkter)" if tak_ur_laser and ruta_kand else "")
            + (", ortofoto" if orto_bild is not None else "")
        )
        res.rutor.append(ruta.namn)

    sam = {
        "kontrakt": "omrade-bygg/v1",
        "manifest_sha256": manifest_sha,
        "omrade_sha256": omrade.sha256,
        "parametrar": params,
        "rutor": sorted(res.rutor),
        "utan_laser": sorted(res.utan_laser),
        "varningar": res.varningar,
    }
    res.sammanfattning = mal / SAMMANFATTNING
    res.sammanfattning.write_text(
        json.dumps(sam, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if not res.rutor:
        raise PipelineFel("Inga rutor byggdes: ingen laser i området? Se varningarna.")
    return res
