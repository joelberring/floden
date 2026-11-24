"""Skarvkontroll: nya eller djupare sänkor längs kanten på ny mark (R3).

Design: `docs/design/RITVERKTYG.md`.

Ny mark (en höjdyta i en projektlapp, K3, eller ett K9 `markhojd`) läggs in i baskartans
markmodell. Där den möter basen kan det bli en **skarv**: en kant eller ett dike som stänger
vattnets väg ner och skapar en ny lokal sänka där vatten blir stående. Kontrollen jämför
sänkorna **före** och **efter** och redovisar de som är nya eller djupare och som ligger inom ett
band kring lappens kant: läge, yta, volym och största djup, och ett förslag på åtgärd.

Sänkorna kommer ur den befintliga sänkgrafen (`sankgraf.hierarki.bygg_sankgraf`, Fill–Spill–
Merge): en lövsänka är ett område som fylls till sitt eget överfall innan vatten rinner vidare.
Fyllnadsdjupet i en cell är `överfall - markhöjd` i cellens lövsänka (0 utanför sänkor).

Regler (se `docs/DATAKONTRAKT.md`, tillägget `floden-skarvkontroll/v1`):

- Bara fysik: alla tal är beräknade ur markmodellerna (m, m², m³, RH 2000, EPSG:3011).
- En sänka räknas om fyllnadsdjupet i dess celler är minst `min_djup_m` efter (och, för
  "ny", minst så mycket mindre före) eller har ökat med minst `min_okning_m` ("djupare").
- Kontrollen ersätter inte vattenberäkningen: den varnar tidigt, medan förslaget ritas.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from sankgraf.hierarki import bygg_sankgraf
from sankgraf.typer import (
    SankGraf,
    SankGrafFel,
    las_brytlinjer_fran_gpkg,
    las_oppningar_fran_gpkg,
)
from testsvit.k1_io import BrytlinjePost, GeoTransformInfo, OppningPost, las_geotiff

KONTRAKT_SKARV = "floden-skarvkontroll/v1"

STANDARD_BAND_M = 10.0
"""Bandets halva bredd (m) på var sida om lappens kant."""
STANDARD_MIN_DJUP_M = 0.05
"""Minsta fyllnadsdjup (m) för att en sänka ska räknas (under det är det brus och vattenhinnor)."""
STANDARD_MIN_OKNING_M = 0.03
"""Minsta ökning (m) av fyllnadsdjupet mot före för att en sänka ska räknas som djupare."""
STANDARD_MARGINAL_M = 40.0
"""Marginal (m) runt lappen och bandet när rastret beskärs för kontrollen."""
MAX_TEXTER = 3
"""Så många fynd skrivs ut som text i kontrollen (resten finns i `skarvar.fynd`)."""
MAX_CELLER = 1_500_000
"""Största antal celler i det beskurna rastret. Större fönster nekas (sänkgrafen byggs i Python)."""


class SkarvKontrollFel(SankGrafFel):
    """Kontrollen går inte att köra (oförenliga raster, tom lapp eller för stort fönster)."""


@dataclass(frozen=True)
class SkarvFynd:
    """En ny eller djupare sänka längs lappens kant."""

    nr: int
    typ: str
    """`ny` (fanns inte som sänka före) eller `djupare`."""
    x_m: float
    y_m: float
    """Sänkans lägsta cell (cellcentrum, EPSG:3011)."""
    botten_rh2000: float
    overfall_rh2000: float
    overfall_x_m: float
    overfall_y_m: float
    storsta_djup_m: float
    """Största fyllnadsdjup efter (m)."""
    djup_fore_m: float
    """Största fyllnadsdjup före i samma celler (m); 0 för en ny sänka."""
    yta_m2: float
    volym_m3: float
    """Sänkans hela lagringsvolym efter, upp till överfallet."""
    tillkommande_volym_m3: float
    """Volymen som ytan lägger till jämfört med före (summan av ökningen per cell)."""
    avstand_till_kant_m: float
    """Kortaste avståndet från en cell med ökat djup till lappens kant."""
    atgard: str

    def som_dict(self) -> dict[str, Any]:
        return {
            "nr": self.nr,
            "typ": self.typ,
            "x_m": _r(self.x_m, 2),
            "y_m": _r(self.y_m, 2),
            "botten_rh2000": _r(self.botten_rh2000, 3),
            "overfall_rh2000": _r(self.overfall_rh2000, 3),
            "overfall_x_m": _r(self.overfall_x_m, 2),
            "overfall_y_m": _r(self.overfall_y_m, 2),
            "storsta_djup_m": _r(self.storsta_djup_m, 3),
            "djup_fore_m": _r(self.djup_fore_m, 3),
            "yta_m2": _r(self.yta_m2, 1),
            "volym_m3": _r(self.volym_m3, 2),
            "tillkommande_volym_m3": _r(self.tillkommande_volym_m3, 2),
            "avstand_till_kant_m": _r(self.avstand_till_kant_m, 1),
            "atgard": self.atgard,
        }

    def text(self) -> str:
        """Meningen som visas för användaren (decimalkomma, enheter)."""
        namn = "Ny sänka" if self.typ == "ny" else "Djupare sänka"
        return (
            f"{namn} vid skarven ({_sv(self.x_m, 0)} E, {_sv(self.y_m, 0)} N): största djup "
            f"{_sv(self.storsta_djup_m, 2)} m, yta {_sv(self.yta_m2, 0)} m², volym "
            f"{_sv(self.volym_m3, 1)} m³ (varav {_sv(self.tillkommande_volym_m3, 1)} m³ är nytt). "
            f"{self.atgard}"
        )


@dataclass(frozen=True)
class SkarvKontrollResultat:
    """Resultatet av skarvkontrollen."""

    fynd: tuple[SkarvFynd, ...]
    band_m: float
    min_djup_m: float
    min_okning_m: float
    celler_i_band: int
    ovriga_nya_antal: int = 0
    """Nya eller djupare sänkor som inte ligger vid kanten (inne i ytan eller längre bort)."""
    ovriga_tillkommande_volym_m3: float = 0.0
    varningar_text: tuple[str, ...] = field(default=())

    @property
    def tillkommande_volym_m3(self) -> float:
        return float(sum(f.tillkommande_volym_m3 for f in self.fynd))

    def varningar(self) -> list[str]:
        """Texter till `varningar` i kartverktygets kontroll (steg 2)."""
        ut = [f.text() for f in self.fynd[:MAX_TEXTER]]
        if len(self.fynd) > MAX_TEXTER:
            ut.append(
                f"Skarvkontrollen hittade {len(self.fynd) - MAX_TEXTER} sänkor till "
                "(lista: skarvar.fynd)."
            )
        if self.ovriga_nya_antal:
            ut.append(
                f"{self.ovriga_nya_antal} ny(a) eller djupare sänka(or) ligger inte vid skarven "
                f"(tillkommande volym {_sv(self.ovriga_tillkommande_volym_m3, 1)} m³); "
                "de ingår inte i skarvkontrollen."
            )
        ut.extend(self.varningar_text)
        return ut

    def som_dict(self) -> dict[str, Any]:
        return {
            "kontrakt": KONTRAKT_SKARV,
            "band_m": self.band_m,
            "min_djup_m": self.min_djup_m,
            "min_okning_m": self.min_okning_m,
            "antal": len(self.fynd),
            "tillkommande_volym_m3": _r(self.tillkommande_volym_m3, 2),
            "ovriga_nya_antal": self.ovriga_nya_antal,
            "ovriga_tillkommande_volym_m3": _r(self.ovriga_tillkommande_volym_m3, 2),
            "fynd": [f.som_dict() for f in self.fynd],
        }


def _r(v: float, n: int) -> float:
    return round(float(v), n)


def _sv(v: float, n: int) -> str:
    return f"{v:.{n}f}".replace(".", ",")


# ---------------------------------------------------------------------------- rastermatte


def fyllnadsdjup(graf: SankGraf) -> NDArray[np.float64]:
    """Fyllnadsdjup (m) per cell: `överfall - markhöjd` i cellens lövsänka, 0 annars.

    Cellerna i en lövsänka får djupet till lövets eget överfall (där vattnet rinner vidare till
    en granne). Celler utanför sänkor (negativa nod-id: hav, byggnad) och celler med okänd höjd
    får 0.
    """
    z = np.asarray(graf.mark_z, dtype=np.float64)
    nod = np.asarray(graf.cell_till_nod, dtype=np.int64)
    djup = np.zeros(z.shape, dtype=np.float64)
    lov = graf.lovnoder()
    if not lov:
        return djup
    storsta = max(n.nod_id for n in lov)
    overfall = np.full(storsta + 1, np.nan, dtype=np.float64)
    for n in lov:
        overfall[n.nod_id] = n.overfall_rh2000
    giltig = (nod >= 0) & (nod <= storsta)
    of = np.full(z.shape, np.nan, dtype=np.float64)
    of[giltig] = overfall[nod[giltig]]
    ok = np.isfinite(of) & np.isfinite(z)
    djup[ok] = np.maximum(of[ok] - z[ok], 0.0)
    return djup


def avstand_till_kant_m(
    lapp_mask: NDArray[np.bool_], pixelstorlek_x: float, pixelstorlek_y: float
) -> NDArray[np.float64]:
    """Avstånd (m) från varje cell till lappens kant, på båda sidor om den.

    Inne i lappen: avståndet till närmaste cell utanför; utanför: till närmaste cell i lappen.
    Cellcentrum ligger en halv cell från kanten, så en cell intill kanten får ungefär en halv
    cellbredd.
    """
    from scipy import ndimage

    sampling = (float(pixelstorlek_y), float(pixelstorlek_x))
    inne = ndimage.distance_transform_edt(lapp_mask, sampling=sampling)
    ute = ndimage.distance_transform_edt(~lapp_mask, sampling=sampling)
    return np.asarray(np.where(lapp_mask, inne, ute), dtype=np.float64)


# ---------------------------------------------------------------------------- kontrollen


def kontrollera_skarvar(
    mark_fore: NDArray[np.float32],
    mark_efter: NDArray[np.float32],
    markslag: NDArray[np.uint8],
    osakerhet: NDArray[np.float32],
    geo: GeoTransformInfo,
    lapp_mask: NDArray[np.bool_],
    *,
    markslag_efter: NDArray[np.uint8] | None = None,
    brytlinjer_fore: Sequence[BrytlinjePost] = (),
    brytlinjer_efter: Sequence[BrytlinjePost] | None = None,
    oppningar_fore: Sequence[OppningPost] = (),
    oppningar_efter: Sequence[OppningPost] | None = None,
    band_m: float = STANDARD_BAND_M,
    min_djup_m: float = STANDARD_MIN_DJUP_M,
    min_okning_m: float = STANDARD_MIN_OKNING_M,
) -> SkarvKontrollResultat:
    """Hittar nya eller djupare sänkor inom `band_m` från lappens kant.

    `lapp_mask` är lappens yta (True där ny mark läggs) på samma raster. Före och efter räknas
    med samma sänkgraf och samma inställningar. Fynden sorteras efter tillkommande volym
    (störst först), sedan läge, så att samma indata ger samma lista.
    """
    form = (geo.hojd, geo.bredd)
    for namn, a in (("mark_fore", mark_fore), ("mark_efter", mark_efter), ("markslag", markslag),
                    ("osakerhet", osakerhet), ("lapp_mask", lapp_mask)):
        if a.shape != form:
            raise SkarvKontrollFel(f"{namn} har formen {a.shape}, men rastret är {form}.")
    if not np.any(lapp_mask):
        raise SkarvKontrollFel("Lappens yta täcker inga celler i rastret; inget att kontrollera.")
    if band_m <= 0 or min_djup_m <= 0 or min_okning_m <= 0:
        raise SkarvKontrollFel("band_m, min_djup_m och min_okning_m måste vara större än noll.")

    slag_efter = markslag if markslag_efter is None else markslag_efter
    graf_fore = bygg_sankgraf(
        mark_fore, osakerhet, markslag, geo, brytlinjer=brytlinjer_fore, oppningar=oppningar_fore
    )
    graf_efter = bygg_sankgraf(
        mark_efter,
        osakerhet,
        slag_efter,
        geo,
        brytlinjer=brytlinjer_fore if brytlinjer_efter is None else brytlinjer_efter,
        oppningar=oppningar_fore if oppningar_efter is None else oppningar_efter,
    )
    d_fore = fyllnadsdjup(graf_fore)
    d_efter = fyllnadsdjup(graf_efter)
    okning = np.maximum(d_efter - d_fore, 0.0)

    dx = float(geo.pixelstorlek_x)
    dy = float(geo.pixelstorlek_y)
    cell_m2 = dx * dy
    dist = avstand_till_kant_m(lapp_mask, dx, dy)
    band = dist <= band_m
    z_efter = np.asarray(graf_efter.mark_z, dtype=np.float64)
    nod_efter = np.asarray(graf_efter.cell_till_nod, dtype=np.int64)

    kandidater: list[tuple[float, float, float, SkarvFynd, bool]] = []
    ovriga = 0
    ovriga_vol = 0.0
    for lov in graf_efter.lovnoder():
        celler = nod_efter == lov.nod_id
        if not np.any(celler):
            continue
        dj = d_efter[celler]
        if float(dj.max()) < min_djup_m:
            continue
        ok = okning[celler]
        ar_ny = float(d_fore[celler].max()) < min_djup_m
        if float(ok.max()) < (min_djup_m if ar_ny else min_okning_m):
            continue
        tillkommande = float(ok.sum()) * cell_m2
        rr, kk = np.nonzero(celler)
        vid_kant = bool(np.any(band[rr, kk] & (ok > 0.0)))
        if not vid_kant:
            ovriga += 1
            ovriga_vol += tillkommande
            continue
        zc = z_efter[rr, kk]
        i = int(np.argmin(np.where(np.isfinite(zc), zc, np.inf)))
        r0, k0 = int(rr[i]), int(kk[i])
        x, y = _cell_xy(geo, r0, k0)
        ox, oy = lov.overfall_x, lov.overfall_y
        avst = float(dist[rr, kk][ok > 0.0].min())
        fynd = SkarvFynd(
            nr=0,
            typ="ny" if ar_ny else "djupare",
            x_m=x,
            y_m=y,
            botten_rh2000=float(zc[i]),
            overfall_rh2000=float(lov.overfall_rh2000),
            overfall_x_m=float(ox),
            overfall_y_m=float(oy),
            storsta_djup_m=float(dj.max()),
            djup_fore_m=float(d_fore[celler].max()),
            yta_m2=float(np.count_nonzero(dj >= min_djup_m)) * cell_m2,
            volym_m3=float(lov.max_volym_m3),
            tillkommande_volym_m3=tillkommande,
            avstand_till_kant_m=avst,
            atgard=_atgard(float(dj.max()), float(lov.overfall_rh2000), ox, oy, avst),
        )
        kandidater.append((-tillkommande, y, x, fynd, ar_ny))
    kandidater.sort(key=lambda t: (round(t[0], 9), round(t[1], 6), round(t[2], 6)))
    fynd_lista = tuple(
        SkarvFynd(**{**k[3].__dict__, "nr": i + 1}) for i, k in enumerate(kandidater)
    )
    return SkarvKontrollResultat(
        fynd=fynd_lista,
        band_m=float(band_m),
        min_djup_m=float(min_djup_m),
        min_okning_m=float(min_okning_m),
        celler_i_band=int(np.count_nonzero(band)),
        ovriga_nya_antal=ovriga,
        ovriga_tillkommande_volym_m3=ovriga_vol,
    )


def _cell_xy(geo: GeoTransformInfo, rad: int, kol: int) -> tuple[float, float]:
    x = geo.x_min + (kol + 0.5) * float(geo.pixelstorlek_x)
    y = geo.y_max - (rad + 0.5) * float(geo.pixelstorlek_y)
    return float(x), float(y)


def _atgard(djup: float, overfall: float, ox: float, oy: float, avstand: float) -> str:
    """Förslag på åtgärd, ur tal som redan är beräknade (inget nytt påstående om fysik)."""
    return (
        "Åtgärd: ge ytan fall mot överfallet "
        f"({_sv(ox, 0)} E, {_sv(oy, 0)} N, nivå {_sv(overfall, 2)} m RH 2000) eller lägg ett "
        "utlopp i kanten; jämna ut skarven med en slänt i stället för en kant."
    )


# ---------------------------------------------------------------------------- K1-rutor


def _las_k1_mapp(
    mapp: Path,
) -> tuple[
    NDArray[np.float32],
    NDArray[np.float32],
    NDArray[np.uint8],
    GeoTransformInfo,
    list[BrytlinjePost],
    list[OppningPost],
]:
    mark_raw, geo = las_geotiff(mapp / "mark.tif")
    osak_raw, _ = las_geotiff(mapp / "osakerhet.tif")
    slag_raw, _ = las_geotiff(mapp / "markslag.tif")
    bryt = mapp / "brytlinjer.gpkg"
    opp = mapp / "oppningar.gpkg"
    return (
        np.asarray(mark_raw, dtype=np.float32),
        np.asarray(osak_raw, dtype=np.float32),
        np.asarray(slag_raw, dtype=np.uint8),
        geo,
        las_brytlinjer_fran_gpkg(bryt) if bryt.is_file() else [],
        las_oppningar_fran_gpkg(opp) if opp.is_file() else [],
    )


def _fonster(
    geo: GeoTransformInfo,
    lapp_mask: NDArray[np.bool_],
    marginal_celler_x: int,
    marginal_celler_y: int,
) -> tuple[slice, slice]:
    rr, kk = np.nonzero(lapp_mask)
    r0 = max(0, int(rr.min()) - marginal_celler_y)
    r1 = min(geo.hojd, int(rr.max()) + 1 + marginal_celler_y)
    k0 = max(0, int(kk.min()) - marginal_celler_x)
    k1 = min(geo.bredd, int(kk.max()) + 1 + marginal_celler_x)
    return slice(r0, r1), slice(k0, k1)


def kontrollera_skarvar_k1(
    mapp_fore: Path | str,
    mapp_efter: Path | str,
    lapp_polygoner: Sequence[Sequence[tuple[float, float]]],
    *,
    band_m: float = STANDARD_BAND_M,
    min_djup_m: float = STANDARD_MIN_DJUP_M,
    min_okning_m: float = STANDARD_MIN_OKNING_M,
    marginal_m: float = STANDARD_MARGINAL_M,
) -> SkarvKontrollResultat | None:
    """Skarvkontroll på två K1-rutor (före och efter) för en eller flera lappytor.

    Rastret beskärs till lappens fönster plus bandet och `marginal_m` (sänkgrafen byggs i Python).
    Beskärningen gör att en sänka vars överfall ligger utanför fönstret ses som avrinnande; före
    och efter beskärs lika, så skillnaden mellan dem är den som redovisas. Ger None om lappen
    inte rör rutan. Större fönster än `MAX_CELLER` ger `SkarvKontrollFel`.
    """
    from lapp.tillampa import _polygon_mask

    mf, osak, slag, geo, bryt, opp = _las_k1_mapp(Path(mapp_fore))
    me, _, slag_e, geo_e, bryt_e, opp_e = _las_k1_mapp(Path(mapp_efter))
    if (geo.hojd, geo.bredd) != (geo_e.hojd, geo_e.bredd) or geo.x_min != geo_e.x_min:
        raise SkarvKontrollFel("Rutorna före och efter har olika raster.")
    mask = np.zeros((geo.hojd, geo.bredd), dtype=np.bool_)
    for p in lapp_polygoner:
        mask |= np.asarray(_polygon_mask(tuple(p), geo), dtype=np.bool_)
    if not np.any(mask):
        return None
    marg = band_m + marginal_m
    rs, ks = _fonster(
        geo,
        mask,
        math.ceil(marg / float(geo.pixelstorlek_x)),
        math.ceil(marg / float(geo.pixelstorlek_y)),
    )
    h, b = rs.stop - rs.start, ks.stop - ks.start
    if h * b > MAX_CELLER:
        raise SkarvKontrollFel(
            f"Området för skarvkontrollen är för stort ({h * b} celler; högst {MAX_CELLER})."
        )
    geo_f = GeoTransformInfo(
        bredd=b,
        hojd=h,
        x_min=geo.x_min + ks.start * float(geo.pixelstorlek_x),
        y_max=geo.y_max - rs.start * float(geo.pixelstorlek_y),
        pixelstorlek_x=geo.pixelstorlek_x,
        pixelstorlek_y=geo.pixelstorlek_y,
        epsg=geo.epsg,
        dtype=geo.dtype,
    )
    return kontrollera_skarvar(
        mf[rs, ks],
        me[rs, ks],
        slag[rs, ks],
        osak[rs, ks],
        geo_f,
        mask[rs, ks],
        markslag_efter=slag_e[rs, ks],
        brytlinjer_fore=bryt,
        brytlinjer_efter=bryt_e,
        oppningar_fore=opp,
        oppningar_efter=opp_e,
        band_m=band_m,
        min_djup_m=min_djup_m,
        min_okning_m=min_okning_m,
    )
