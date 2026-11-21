"""Bullerscen ur K1 v2 (gemensam geometri): byggnader, terräng, markslag och fasadpunkter (K11).

Den befintliga minnesadaptern (`Bullerscen(...)` med handgjorda arrayer, `syntetisk_scen`,
`bullerscen_fran_k1_ruta`) är oförändrad och används av testfallen. Här byggs samma `Bullerscen` ur
K1 v2-rutor via `karna.geometri`, så att buller räknar på exakt den geometri som luft och dagsljus
läser, inklusive K9-scenarier (`byggnad_ny`, `byggnad_hojd`, `byggnad_bort`) som kärnan redan
tillämpat på rutorna:

- Byggnader (fotavtryck, takhöjd, `byggnad_id`) ur `byggnader.gpkg` (`las_byggnader`). Scenens
  byggnads-id är K1:s `byggnad_id` (stabilt), så resultat kan kopplas tillbaka till registret.
- Terräng ur `terrang.tif` (marken utan byggnader, med kantstenar) på `cell_m` (medel per block).
- Markslag ur `markslag.tif` (vanligaste koden per block). Markslag → impedans ligger i den
  gemensamma `data/markklasser.toml` och slås upp av banfinnaren (`buller.scen.markklass`).
- Mottagare ur K11 `fasadpunkter`: punkten flyttas `avstand_fran_fasad_m` ut längs fasadnormalen
  (K11-punkten ligger på fasadlinjen). `Mottagare.byggnad_id` är K1:s `byggnad_id` och
  `Mottagare.id` är en 56-bitars hash av `fasadpunkt_id` (stabil mellan scenarier, så före/efter och
  inkrementell omräkning kan para ihop mottagare; se `K1v2Scen.fasadpunkt_id`).

Begränsningar (redovisas i `K1v2Scen.varningar`, inget tyst):

- `Byggnad` i buller har bara en yttre ring. Ett register med hål (innergård) ger `BullerFel` som
  standard. `byggnader_med_hal="dela"` skär polygonen längs en lodrät linje genom varje hål i
  delar utan
  hål (delarna delar väggar som inte finns i verkligheten; effekten på reflexer är inte
  kontrollerad),
  `"yttre_ring"` fyller igen gården (överskattar skärmning och tappar gårdsfasaderna).
- Taket är platt (K1 v2). Mottagarhöjden är K11:s `hojd_over_mark_m`.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
import shapely
from shapely.ops import split

from buller.scen.bullerscen import Bullerscen, Vag
from buller.typer import BullerFel, Byggnad, Mottagare
from karna.geometri import Fasadpunkter, GeometriFel, fasadpunkter
from karna.geometri.lasa import RutaLike, alla_byggnader
from karna.geometri.mosaik import bygg_ytmosaik
from markmodell.byggnadsregister import ByggnadsPost

HalLage = Literal["fel", "dela", "yttre_ring"]


@dataclass(frozen=True)
class K1v2Scen:
    """Bullerscen med mottagare och redovisning av vad som skett på vägen från K1 v2."""

    scen: Bullerscen
    mottagare: tuple[Mottagare, ...]
    fasadpunkt_id: dict[int, str]  # Mottagare.id -> fasadpunkt_id (K11)
    k1_byggnad_id: dict[str, str]  # scenens byggnads-id -> K1:s byggnad_id
    varningar: tuple[str, ...]
    matvarden: dict[str, float]


def _delar_utan_hal(post: ByggnadsPost) -> list[tuple[tuple[float, float], ...]]:
    """Delar polygonen med hål i polygoner utan hål (lodrät skärning genom varje hål)."""
    delar = [shapely.Polygon(post.polygon, post.hal)]
    for _ in range(len(post.hal) + 1):
        nya: list[shapely.Polygon] = []
        klar = True
        for d in delar:
            if not d.interiors:
                nya.append(d)
                continue
            klar = False
            hal = shapely.Polygon(d.interiors[0])
            x = hal.centroid.x
            x0, y0, x1, y1 = d.bounds
            for g in split(d, shapely.LineString([(x, y0 - 1.0), (x, y1 + 1.0)])).geoms:
                if isinstance(g, shapely.Polygon) and g.area > 0.0:
                    nya.append(g)
        delar = nya
        if klar:
            break
    if any(d.interiors for d in delar):
        raise BullerFel(f"Byggnad {post.byggnad_id}: kunde inte dela polygonen med hål.")
    return [tuple((float(x), float(y)) for x, y in d.exterior.coords[:-1]) for d in delar]


def _byggnader(
    poster: Sequence[ByggnadsPost], hal: HalLage
) -> tuple[list[Byggnad], dict[str, str], list[str]]:
    ut: list[Byggnad] = []
    id_map: dict[str, str] = {}
    varningar: list[str] = []
    med_hal = [p for p in poster if p.hal]
    if med_hal and hal == "fel":
        raise BullerFel(
            f"{len(med_hal)} byggnader har hål (innergård), t.ex. {med_hal[0].byggnad_id}; buller "
            "har bara yttre ring. Ange byggnader_med_hal='dela' eller 'yttre_ring' (se "
            "docs/buller/KONTRAKT.md)."
        )
    if med_hal:
        varningar.append(
            f"{len(med_hal)} byggnader med hål hanterades med '{hal}': "
            + (
                "polygonerna delades i delar utan hål (delarna delar väggar som inte finns)"
                if hal == "dela"
                else "innergårdarna fylldes igen (skärmningen överskattas, gårdsfasader saknas)"
            )
        )
    for p in poster:
        if p.hal and hal == "dela":
            for i, ring in enumerate(_delar_utan_hal(p)):
                sid = f"{p.byggnad_id}#{i}"
                ut.append(_byggnad(sid, ring, p.takhojd_rh2000))
                id_map[sid] = p.byggnad_id
        else:
            ut.append(_byggnad(p.byggnad_id, p.polygon, p.takhojd_rh2000))
            id_map[p.byggnad_id] = p.byggnad_id
    return ut, id_map, varningar


def _byggnad(sid: str, ring: Sequence[tuple[float, float]], tak: float) -> Byggnad:
    r = np.asarray(ring, dtype=np.float64)
    return Byggnad(id=sid, e_m=r[:, 0], n_m=r[:, 1], tak_rh2000_m=float(tak))


def bullerscen_fran_k1_v2(
    rutor: Sequence[RutaLike],
    *,
    cell_m: float,
    vagar: Sequence[Vag] = (),
    byggnader_med_hal: HalLage = "fel",
) -> tuple[Bullerscen, dict[str, str], tuple[str, ...], dict[str, float]]:
    """Bullerscenen ur K1 v2-rutor. Returnerar (scen, scenens id -> K1 `byggnad_id`, varningar,
    mätvärden). `cell_m` är en heltalsmultipel av K1:s pixelstorlek (0,25 m; 0,5 m som i
    testscenen)."""
    try:
        m = bygg_ytmosaik(rutor, cell_m)
        poster = tuple(alla_byggnader(list(rutor)).values())
    except GeometriFel as exc:
        raise BullerFel(f"K1 v2-geometrin gick inte att läsa: {exc}") from exc
    byggnader, id_map, varningar = _byggnader(poster, byggnader_med_hal)
    scen = Bullerscen(
        terrang_rh2000_m=m.terrang_rh2000_m,
        markslag=m.markslag,
        e_min_m=m.x_min_m,
        n_max_m=m.y_max_m,
        cell_m=m.cell_m,
        byggnader=tuple(byggnader),
        vagar=tuple(vagar),
    )
    okant = scen.andel_okant_markslag()
    v = list(varningar)
    if m.saknade_celler:
        v.append(
            f"{m.saknade_celler} rutnätsceller täcks inte av någon ruta "
            "(markslag okänt, terräng medel)"
        )
    if okant > 0.0:
        v.append(
            f"{okant:.1%} av cellerna har okänt markslag (kod 0): banfinnaren flaggar dem, "
            "ingen impedans gissas"
        )
    matv = {
        "antal_rutor": float(m.antal_rutor),
        "antal_byggnader_k1": float(len(poster)),
        "antal_byggnader_scen": float(len(byggnader)),
        "okant_markslag_andel": float(okant),
        "saknade_celler": float(m.saknade_celler),
        "cell_m": float(m.cell_m),
    }
    return scen, id_map, tuple(v), matv


def mottagar_id(fasadpunkt_id: str) -> int:
    """Stabilt heltals-id för en fasadpunkt: första 7 byte av sha256(fasadpunkt_id) som heltal."""
    return int.from_bytes(hashlib.sha256(fasadpunkt_id.encode("utf-8")).digest()[:7], "big")


def mottagare_fran_k11(
    fp: Fasadpunkter, scen: Bullerscen, *, avstand_fran_fasad_m: float
) -> tuple[tuple[Mottagare, ...], dict[int, str], dict[str, int]]:
    """K11-punkter → buller `Mottagare`, flyttade ut längs normalen.

    Punkter vars flyttade läge ligger utanför terrängrastret eller inuti en byggnad utelämnas och
    räknas. `Mottagare.id` = `mottagar_id(fasadpunkt_id)`. Returnerar (mottagare, id ->
    fasadpunkt_id,
    räkneverk). Kollision mellan två id (osannolik) ger `BullerFel`.
    """
    if not (math.isfinite(avstand_fran_fasad_m) and avstand_fran_fasad_m > 0.0):
        raise BullerFel(f"avstand_fran_fasad_m ska vara > 0 m, fick {avstand_fran_fasad_m}.")
    e = fp.e_m + fp.normal_e * avstand_fran_fasad_m
    n = fp.n_m + fp.normal_n * avstand_fran_fasad_m
    inom = scen.inom_raster(e, n)
    i_byggnad = np.zeros(e.shape, dtype=bool)
    if scen.byggnader and bool(inom.any()):
        i_byggnad[inom] = scen.i_byggnad(e[inom], n[inom])
    giltig = inom & ~i_byggnad
    mott: list[Mottagare] = []
    id_till_fp: dict[int, str] = {}
    for k in np.flatnonzero(giltig):
        fid = str(fp.fasadpunkt_id[k])
        mid = mottagar_id(fid)
        if mid in id_till_fp:
            raise BullerFel(f"Mottagar-id {mid} krockar mellan {id_till_fp[mid]} och {fid}.")
        id_till_fp[mid] = fid
        mott.append(
            Mottagare(
                id=mid,
                e_m=float(e[k]),
                n_m=float(n[k]),
                hojd_over_mark_m=float(fp.hojd_over_mark_m[k]),
                byggnad_id=str(fp.byggnad_id[k]),
                vaning=int(fp.vaning[k]),
                fasadnormal_deg=float(fp.normal_azimut_grader[k]),
            )
        )
    rakna = {
        "antal_k11": len(fp),
        "antal_utanfor_raster": int(np.count_nonzero(~inom)),
        "antal_i_byggnad": int(np.count_nonzero(inom & i_byggnad)),
    }
    return tuple(mott), id_till_fp, rakna


def k1v2_scen(
    rutor: Sequence[RutaLike],
    *,
    cell_m: float,
    avstand_fran_fasad_m: float,
    fasad_steg_m: float,
    vaningshojd_m: float,
    hojd_over_golv_m: float = 1.5,
    vagar: Sequence[Vag] = (),
    byggnader_med_hal: HalLage = "fel",
) -> K1v2Scen:
    """Scen och mottagare (K11) ur K1 v2-rutor i ett steg.

    Inga standardvärden för `avstand_fran_fasad_m`, `fasad_steg_m` och `vaningshojd_m`: de är val
    som
    hör till bullermetodens anvisningar (B9), inte till geometrin.
    """
    scen, id_map, varn, matv = bullerscen_fran_k1_v2(
        rutor, cell_m=cell_m, vagar=vagar, byggnader_med_hal=byggnader_med_hal
    )
    try:
        fp = fasadpunkter(
            list(rutor),
            steg_m=fasad_steg_m,
            vaningshojd_m=vaningshojd_m,
            hojd_over_golv_m=hojd_over_golv_m,
        )
    except GeometriFel as exc:
        raise BullerFel(f"Fasadpunkter (K11) kunde inte genereras: {exc}") from exc
    mott, fp_id, rakna = mottagare_fran_k11(fp, scen, avstand_fran_fasad_m=avstand_fran_fasad_m)
    v = list(varn)
    if rakna["antal_utanfor_raster"] or rakna["antal_i_byggnad"]:
        v.append(
            f"{rakna['antal_utanfor_raster']} fasadpunkter hamnar utanför terrängrastret och "
            f"{rakna['antal_i_byggnad']} inuti en byggnad efter flytten "
            f"{avstand_fran_fasad_m:g} m ut; "
            "de utelämnas"
        )
    matv.update({k: float(x) for k, x in rakna.items()})
    matv["antal_mottagare"] = float(len(mott))
    return K1v2Scen(scen, mott, fp_id, id_map, tuple(v), matv)
