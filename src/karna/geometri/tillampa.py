"""K9-ändringar på en K1 v2-ruta: byggnader och vegetation, konsekvent med `mark.tif`.

Invarianten i K1 v2 är `mark.tif = öppningar(byggnader(terrang.tif))`, där byggnaderna är
registret i `byggnader.gpkg` (platta tak på `takhojd_rh2000`) och öppningarna är `oppningar.gpkg`.
Efter en ändring byggs `mark.tif` och `markslag.tif` om med exakt samma funktioner som
markmodellens bygge använder, så att ändringen blir identisk med att ha byggt rutan med den
ändringen från början. Celler som ingen ändring rör får samma värden som förut.

Ändringstyper (K9): `byggnad_ny`, `byggnad_hojd`, `byggnad_bort`, `vegetation`. Projektlappar (K3)
på en v2-ruta ändrar terrängen (höjdytor, gator, magasin, markslag) och byggnaderna i lappen läggs
i registret. En K3-/K9-ändring under en befintlig byggnad ändrar terrängen men byggnaden finns
kvar tills `byggnad_bort`.
"""

from __future__ import annotations

import json
import math
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import shapely
from numpy.typing import NDArray

from karna.geometri.lasa import k1_version
from karna.geometri.rasterfil import (
    las_float_raster,
    las_raster,
    ruta_fran_geo,
    skriv_raster,
)
from karna.scenario import Andring, ScenarioFel
from markmodell.byggnadsregister import (
    ByggnadsPost,
    las_byggnader_gpkg,
    markhojd_under_byggnad,
    skriv_byggnader_gpkg,
)
from markmodell.hinder_oppningar import (
    BYGGNAD,
    HARDGJORT,
    Oppning,
    brann_in_byggnader,
    brann_in_oppningar,
    byggnad_maske,
)
from testsvit.k1_io import GeoTransformInfo

if TYPE_CHECKING:
    from lapp.kontrakt import Gatusektion, ProjektLapp

# Höjs när tillämpningen på v2-rutor ändras (ingår i rutornas lappnyckel).
GEOMETRI_VERSION = "1"
GEOMETRI_TYPER: frozenset[str] = frozenset({"byggnad_bort", "byggnad_hojd", "vegetation"})
VEGETATION_STEG_M = 1.0

Ring = tuple[tuple[float, float], ...]


def valj_k1_version(mappar: Sequence[Path]) -> int:
    """K1-versionen för en uppsättning rutor; blandade versioner ger fel."""
    versioner = {k1_version(m) for m in mappar}
    if len(versioner) > 1:
        raise ScenarioFel(
            "Basen blandar K1 v1- och v2-rutor; bygg om alla rutor med v2 "
            "(byggnader.gpkg och terrang.tif)."
        )
    return versioner.pop() if versioner else 1


def _tal(e: Mapping[str, Any], namn: str, a: Andring) -> float:
    v = e.get(namn)
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
        raise ScenarioFel(f"{a.typ} '{a.andring_id}': '{namn}' måste vara ett tal, fick {v!r}.")
    return float(v)


def _polygon(a: Andring) -> tuple[Ring, tuple[Ring, ...]]:
    g = a.geometri
    if g is None or g.get("type") != "Polygon" or not g.get("coordinates"):
        raise ScenarioFel(f"{a.typ} '{a.andring_id}': geometrin måste vara en GeoJSON Polygon.")
    ringar: list[Ring] = []
    for r in g["coordinates"]:
        pts = [(float(p[0]), float(p[1])) for p in r]
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) < 3:
            raise ScenarioFel(f"{a.typ} '{a.andring_id}': en ring har färre än 3 hörn.")
        ringar.append(tuple(pts))
    return ringar[0], tuple(ringar[1:])


def verifiera_byggnadsreferenser(mappar: Sequence[Path], andringar: Sequence[Andring]) -> None:
    """Varje `byggnads_id` i `byggnad_bort`/`byggnad_hojd` ska finnas i en ruta (eller skapas)."""
    refs = {
        str(a.egenskaper["byggnads_id"])
        for a in andringar
        if a.typ in ("byggnad_bort", "byggnad_hojd")
    }
    if not refs:
        return
    kanda = {a.andring_id for a in andringar if a.typ == "byggnad_ny"}
    for m in mappar:
        kanda |= {b.byggnad_id for b in las_byggnader_gpkg(m / "byggnader.gpkg")}
    saknas = sorted(refs - kanda)
    if saknas:
        raise ScenarioFel(
            f"Okända byggnads_id (finns inte i något byggnadsregister i basen): {saknas[:10]}"
        )


def _fyll_narmaste(
    slag: NDArray[np.uint8], fyll: NDArray[np.bool_], giltig: NDArray[np.bool_]
) -> NDArray[np.uint8]:
    """Fyller `fyll`-celler med markslag från närmaste giltiga granne (8-grannskap)."""
    ut = slag.copy()
    if not fyll.any():
        return ut
    rad, kol = np.nonzero(fyll)
    r0, r1 = max(int(rad.min()) - 1, 0), min(int(rad.max()) + 2, slag.shape[0])
    k0, k1 = max(int(kol.min()) - 1, 0), min(int(kol.max()) + 2, slag.shape[1])
    s = ut[r0:r1, k0:k1]
    kvar = fyll[r0:r1, k0:k1].copy()
    ok = giltig[r0:r1, k0:k1] & ~kvar
    forskjutningar = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]
    while kvar.any():
        nytt = np.zeros_like(kvar)
        for dr, dk in forskjutningar:
            kand_ok = np.zeros_like(ok)
            kand_v = np.zeros_like(s)
            rs = slice(max(dr, 0), s.shape[0] + min(dr, 0))
            rd = slice(max(-dr, 0), s.shape[0] + min(-dr, 0))
            cs = slice(max(dk, 0), s.shape[1] + min(dk, 0))
            cd = slice(max(-dk, 0), s.shape[1] + min(-dk, 0))
            kand_ok[rd, cd] = ok[rs, cs]
            kand_v[rd, cd] = s[rs, cs]
            ta = kvar & ~nytt & kand_ok
            s[ta] = kand_v[ta]
            nytt |= ta
        if not nytt.any():
            s[kvar] = np.uint8(HARDGJORT)  # ingen giltig granne alls
            break
        ok |= nytt
        kvar &= ~nytt
    return ut


def _rutmask_polygon(
    ring: Ring, hal: Sequence[Ring], geo: GeoTransformInfo
) -> tuple[NDArray[np.bool_], tuple[int, int, int, int]] | None:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    px = geo.pixelstorlek_x
    k0 = max(int(math.floor((min(xs) - geo.x_min) / px)) - 1, 0)
    k1 = min(int(math.ceil((max(xs) - geo.x_min) / px)) + 1, geo.bredd)
    r0 = max(int(math.floor((geo.y_max - max(ys)) / px)) - 1, 0)
    r1 = min(int(math.ceil((geo.y_max - min(ys)) / px)) + 1, geo.hojd)
    if k1 <= k0 or r1 <= r0:
        return None
    e = geo.x_min + (np.arange(k0, k1) + 0.5) * px
    n = geo.y_max - (np.arange(r0, r1) + 0.5) * px
    ge, gn = np.meshgrid(e, n)
    poly = shapely.Polygon(ring, list(hal))
    mask = shapely.contains_xy(poly, ge, gn)
    if not mask.any():
        return None
    return np.asarray(mask, dtype=bool), (r0, r1, k0, k1)


def _ny_post(
    a_id: str,
    ring: Ring,
    hal: Sequence[Ring],
    egenskaper: Mapping[str, Any],
    terrang: NDArray[np.float32],
    mm: Any,
    a: Andring | None,
    *,
    tak_rh2000: float | None,
    hojd_m: float | None,
    kalla: str,
) -> ByggnadsPost:
    mark = markhojd_under_byggnad(terrang, mm, ring, hal)
    if tak_rh2000 is None:
        if hojd_m is None:
            raise ScenarioFel(f"byggnad_ny '{a_id}': ange hojd_m (över mark) eller tak_rh2000.")
        tak_rh2000 = mark + hojd_m
    if tak_rh2000 - mark <= 0.0:
        raise ScenarioFel(f"byggnad_ny '{a_id}': taket ligger inte över marken.")
    vaningar = egenskaper.get("vaningar")
    return ByggnadsPost(
        byggnad_id=a_id,
        polygon=ring,
        hal=tuple(hal),
        takhojd_rh2000=tak_rh2000,
        markhojd_rh2000=mark,
        hojd_m=tak_rh2000 - mark,
        takform=None if egenskaper.get("takform") is None else str(egenskaper["takform"]),
        andamal=None if egenskaper.get("andamal") is None else str(egenskaper["andamal"]),
        vaningar=None if vaningar is None else int(vaningar),
        kalla=kalla,
        osakerhet_hojd_m=None,
        hojdkalla="andring",
    )


def tillampa_pa_v2_ruta(
    mapp: Path,
    ut: Path,
    *,
    lapp: ProjektLapp | None,
    sektioner: Mapping[str, Gatusektion] | None,
    geometri: Sequence[Andring],
    bas_recept_sha256: str,
    kalla: str = "plan",
) -> bool:
    """Tillämpar en lapp (K3, utan dess byggnader) och K9-geometriändringar på en v2-ruta.

    Skriver den nya rutan i `ut` och returnerar True, eller skriver inget och returnerar False om
    ingenting i rutan ändras.
    """
    from lapp.tillampa import tillampa_lapp
    from sankgraf.typer import las_brytlinjer_fran_gpkg, las_oppningar_fran_gpkg
    from testsvit.k1_io import skriv_brytlinjer_gpkg, skriv_oppningar_gpkg

    mark, geo = las_float_raster(mapp / "mark.tif")
    osak, _ = las_float_raster(mapp / "osakerhet.tif")
    slag_raw, _ = las_raster(mapp / "markslag.tif")
    slag = np.asarray(slag_raw, dtype=np.uint8)
    terrang, _ = las_float_raster(mapp / "terrang.tif")
    poster0 = las_byggnader_gpkg(mapp / "byggnader.gpkg")
    veg_fil = mapp / "vegetation.tif"
    veg0, veg_geo0 = las_float_raster(veg_fil) if veg_fil.is_file() else (None, None)
    mm = ruta_fran_geo(geo)
    bryt0 = las_brytlinjer_fran_gpkg(mapp / "brytlinjer.gpkg") if (
        mapp / "brytlinjer.gpkg"
    ).is_file() else []
    opp0 = las_oppningar_fran_gpkg(mapp / "oppningar.gpkg") if (
        mapp / "oppningar.gpkg"
    ).is_file() else []

    # 1. Terrängändringar (K3 utan byggnader)
    terrang1, osak1, slag1 = terrang, osak, slag
    bryt1, opp1 = bryt0, opp0
    lapp_k3 = None
    lapp_ran = False
    nya_k3_byggnader = list(lapp.byggnader) if lapp is not None else []
    if lapp is not None and (
        lapp.hojdytor
        or lapp.gator
        or lapp.magasin
        or lapp.markslag_andringar
        or lapp.har_vektorandringar
    ):
        lapp_k3 = replace(lapp, byggnader=(), operationer=())
        res = tillampa_lapp(
            terrang,
            osak,
            slag,
            geo,
            lapp_k3,
            bas_recept_sha256=bas_recept_sha256,
            bas_brytlinjer=bryt0,
            bas_oppningar=opp0,
            sektioner=sektioner,
        )
        terrang1 = np.asarray(res.mark_z, dtype=np.float32)
        osak1 = np.asarray(res.osakerhet_z, dtype=np.float32)
        slag1 = np.asarray(res.markslag, dtype=np.uint8)
        bryt1, opp1 = list(res.alla_brytlinjer), list(res.oppningar)
        lapp_ran = True

    # 2. Byggnadsregistret
    reg: dict[str, ByggnadsPost] = {p.byggnad_id: p for p in poster0}
    borttagna: list[tuple[ByggnadsPost, Andring | None]] = []
    ex, ey = geo.x_min, geo.y_min
    ex1, ey1 = geo.x_max, geo.y_max

    def _utanfor(ring: Ring) -> bool:
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return max(xs) < ex or min(xs) > ex1 or max(ys) < ey or min(ys) > ey1

    for kb in sorted(nya_k3_byggnader, key=lambda b: b.byggnad_id):
        if _utanfor(kb.polygon):
            continue
        if kb.byggnad_id in reg:
            raise ScenarioFel(f"byggnad '{kb.byggnad_id}' finns redan i registret.")
        reg[kb.byggnad_id] = _ny_post(
            kb.byggnad_id, kb.polygon, (), {}, terrang1, mm, None,
            tak_rh2000=kb.tak_rh2000, hojd_m=kb.hinderhojd_m, kalla=kalla,
        )
    steg = {"byggnad_ny": 0, "byggnad_hojd": 1, "byggnad_bort": 2, "vegetation": 3}
    ordnade = sorted(
        (a for a in geometri if a.typ in steg), key=lambda a: (steg[a.typ], a.andring_id)
    )
    veg_andringar: list[Andring] = []
    for a in ordnade:
        e = a.egenskaper
        if a.typ == "byggnad_ny":
            ring, hal = _polygon(a)
            if _utanfor(ring):
                continue
            if a.andring_id in reg:
                raise ScenarioFel(f"byggnad_ny '{a.andring_id}': id:t finns redan i registret.")
            tak = _tal(e, "tak_rh2000", a) if "tak_rh2000" in e else None
            hojd = _tal(e, "hojd_m", a) if "hojd_m" in e else None
            if hojd is not None and hojd <= 0:
                raise ScenarioFel(f"byggnad_ny '{a.andring_id}': hojd_m måste vara > 0.")
            reg[a.andring_id] = _ny_post(
                a.andring_id, ring, hal, e, terrang1, mm, a,
                tak_rh2000=tak, hojd_m=hojd, kalla=kalla,
            )
        elif a.typ == "byggnad_hojd":
            bid = str(e["byggnads_id"])
            if bid not in reg:
                continue  # finns i en annan ruta; verifierat på scenarionivå
            givna = [k for k in ("hojd_m", "tak_rh2000", "delta_m") if k in e]
            if len(givna) != 1:
                raise ScenarioFel(
                    f"byggnad_hojd '{a.andring_id}': ange exakt en av hojd_m, tak_rh2000, delta_m."
                )
            p = reg[bid]
            if givna[0] == "hojd_m":
                ny_tak = p.markhojd_rh2000 + _tal(e, "hojd_m", a)
            elif givna[0] == "tak_rh2000":
                ny_tak = _tal(e, "tak_rh2000", a)
            else:
                ny_tak = p.takhojd_rh2000 + _tal(e, "delta_m", a)
            if ny_tak - p.markhojd_rh2000 <= 0.0:
                raise ScenarioFel(f"byggnad_hojd '{a.andring_id}': taket hamnar under marken.")
            reg[bid] = p.med_tak(ny_tak)
        elif a.typ == "byggnad_bort":
            bid = str(e["byggnads_id"])
            if bid in reg:
                borttagna.append((reg.pop(bid), a))
        else:
            veg_andringar.append(a)

    nya_poster = tuple(reg[k] for k in sorted(reg))
    register_andrat = nya_poster != poster0

    # 3. Vegetation
    veg1, veg_geo1 = veg0, veg_geo0
    for a in veg_andringar:
        hojd = _tal(a.egenskaper, "hojd_m", a)
        if hojd < 0:
            raise ScenarioFel(f"vegetation '{a.andring_id}': hojd_m måste vara >= 0.")
        ring, hal = _polygon(a)
        if veg1 is None or veg_geo1 is None:
            f = round(VEGETATION_STEG_M / geo.pixelstorlek_x)
            if f < 1 or geo.hojd % f or geo.bredd % f:
                f = 1
            veg_geo1 = GeoTransformInfo(
                bredd=geo.bredd // f,
                hojd=geo.hojd // f,
                x_min=geo.x_min,
                y_max=geo.y_max,
                pixelstorlek_x=geo.pixelstorlek_x * f,
                pixelstorlek_y=geo.pixelstorlek_y * f,
                epsg=geo.epsg,
                dtype="float32",
            )
            veg1 = np.zeros((veg_geo1.hojd, veg_geo1.bredd), dtype=np.float32)
        traff = _rutmask_polygon(ring, hal, veg_geo1)
        if traff is None:
            continue
        mask, (r0, r1, k0, k1) = traff
        veg1 = veg1.copy() if veg1 is veg0 else veg1
        veg1[r0:r1, k0:k1][mask] = np.float32(hojd)
    veg_andrad = (veg1 is not None) and (
        veg0 is None or not np.array_equal(veg1, veg0)
    )

    # 4. mark.tif och markslag.tif ur terräng + register + öppningar (samma steg som bygget)
    mark2, slag2 = mark, slag1
    if register_andrat or lapp_ran:
        bygg = [p.till_byggnad() for p in nya_poster]
        fyll_slag = slag1
        if borttagna:
            fyll_slag = slag1.copy()
            _, kvar_slag = brann_in_byggnader(
                np.zeros(slag1.shape, dtype=np.float32),
                np.zeros(slag1.shape, dtype=np.uint8),
                mm,
                bygg,
            )
            kvar = kvar_slag == BYGGNAD
            borta = np.zeros(slag1.shape, dtype=bool)
            for p, bort_a in borttagna:
                traff = byggnad_maske(p.till_byggnad(), mm)
                if traff is None:
                    continue
                m, (r0, r1, k0, k1) = traff
                borta[r0:r1, k0:k1] |= m
                kod = None if bort_a is None else bort_a.egenskaper.get("markslag")
                if kod is not None:
                    sub = fyll_slag[r0:r1, k0:k1]
                    sub[m & ~kvar] = np.uint8(int(kod))
                    borta[r0:r1, k0:k1] &= ~(m & ~kvar)
            borta &= ~kvar
            fyll_slag = _fyll_narmaste(fyll_slag, borta, ~borta & (fyll_slag != BYGGNAD))
        z3, s3 = brann_in_byggnader(terrang1, fyll_slag, mm, bygg)
        mark2, slag2 = brann_in_oppningar(
            z3,
            s3,
            mm,
            [
                Oppning(
                    typ=o.typ,
                    bredd_m=o.bredd_m,
                    botten_rh2000=o.botten_rh2000,
                    kalla=o.kalla,
                    linje=tuple(o.koordinater),
                )
                for o in opp1
            ],
        )
        mark2 = np.asarray(mark2, dtype=np.float32)

    terrang_andrad = not np.array_equal(terrang1, terrang)
    andrat = (
        register_andrat
        or veg_andrad
        or terrang_andrad
        or not np.array_equal(mark2, mark)
        or not np.array_equal(slag2, slag)
        or not np.array_equal(osak1, osak)
        or list(bryt1) != list(bryt0)
        or list(opp1) != list(opp0)
    )
    if not andrat:
        return False

    # 5. Skriv
    ut.mkdir(parents=True, exist_ok=True)
    shutil.copytree(mapp, ut, dirs_exist_ok=True)
    bbox = (geo.x_min, geo.y_min, geo.x_max, geo.y_max)
    skriv_raster(ut / "mark.tif", mark2, geo)
    skriv_raster(ut / "markslag.tif", slag2, geo)
    if not np.array_equal(osak1, osak):
        skriv_raster(ut / "osakerhet.tif", osak1, geo)
    if terrang_andrad:
        skriv_raster(ut / "terrang.tif", terrang1, geo)
    if register_andrat:
        skriv_byggnader_gpkg(ut / "byggnader.gpkg", nya_poster, bbox)
    if veg_andrad and veg1 is not None and veg_geo1 is not None:
        skriv_raster(ut / "vegetation.tif", veg1, veg_geo1)
    if lapp_ran:
        skriv_brytlinjer_gpkg(ut / "brytlinjer.gpkg", bryt1, bbox_fallback=bbox)
        skriv_oppningar_gpkg(ut / "oppningar.gpkg", opp1, bbox_fallback=bbox)
    gamla = {p.byggnad_id: p for p in poster0}
    recept: dict[str, Any] = {
        "kontrakt": "K1+K9/v2",
        "k1_version": 2,
        "bas_recept_sha256": bas_recept_sha256,
        "epsg": geo.epsg,
        "bbox": list(bbox),
        "upplosning_m": [geo.pixelstorlek_x, geo.pixelstorlek_y],
        "geometri_version": GEOMETRI_VERSION,
        "geometriandringar": sorted(
            (a.till_dict() for a in geometri), key=lambda d: d["andring_id"]
        ),
        "lapp_id": None if lapp is None else lapp.lapp_id,
        "lapp_sha256": None if lapp is None else lapp.lapp_sha256(),
        "byggnader_nya": sorted(set(reg) - set(gamla)),
        "byggnader_bort": sorted(set(gamla) - set(reg)),
        "byggnader_andrade": sorted(
            k for k in set(reg) & set(gamla) if reg[k] != gamla[k]
        ),
        "antal_andrade_celler": int(np.count_nonzero(mark2 != mark)),
    }
    (ut / "recept.json").write_text(
        json.dumps(recept, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    return True
