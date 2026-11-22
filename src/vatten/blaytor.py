"""Blåytor vid X mm – systemdesign 4.1, förhandsvisningsnivån för skyfall.

Ett tunt lager över sänkgrafen (K2, `sankgraf.hierarki.bygg_sankgraf`) och dess exakta,
volymbevarande Fill–Spill–Merge (`sankgraf.fyllning.statisk_fyllning`). Inget simuleras i tid:
regnet läggs ut likformigt (eller som raster), varje cell skickar sin volym till sin lövsänka,
och fulla sänkor rinner över i trädet. När trädet finns tar varje X bara millisekunder, så en
`Blaytemodell` kan svara på många regnmängder efter varandra.

Begränsningar, avsiktliga i förhandsvisningen:
- ingen tid: allt regn har nått sina sänkor (inga ledningar, ingen avdunstning);
- regn på byggnadsceller (markslag 9) avrinner till marken längs fasaderna (takvatten, standard
  på; `sankgraf.takvatten`, okänd takform = brutet tak); med `takvatten=False` räknas det inte;
- ett område som helt omges av byggnader är en slutsänka (K2/v2): vattnet blir stående där
  tills det når hindret, och resultatet får varningen och `slutsanka.tif`;
- förluster bara som initialförlust i mm per markslag, och bara om de anges uttryckligen.

Resultatet skrivs K6-likt: `max_djup.tif` + `run.json` (K0), plus `blaytor.json` per sänka.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost
from sankgraf.fyllning import statisk_fyllning
from sankgraf.hierarki import bygg_sankgraf
from sankgraf.persistens import filtrera_persistens
from sankgraf.takvatten import TAKVATTEN_AV_ANTAGANDE, med_takvatten, med_takvatten_graf
from sankgraf.typer import (
    MARKSLAG_BYGGNAD,
    SankGraf,
    SankGrafFel,
    StatiskFyllningResultat,
    las_brytlinjer_fran_gpkg,
    las_oppningar_fran_gpkg,
    skriv_statiskt_resultat_k6,
    slutsanka_matvarden,
    slutsanka_varningar,
)
from testsvit.k1_io import BrytlinjePost, GeoTransformInfo, OppningPost, las_geotiff

FYLLD_TOL_M3 = 1e-9


class BlaytorFel(ValueError):
    """Ogiltiga indata till blåytor."""


@dataclass(frozen=True)
class K1Indata:
    """De K1-filer (v1) som sänkgrafen behöver, inlästa."""

    mark_z: NDArray[np.float32]
    osakerhet_z: NDArray[np.float32]
    markslag: NDArray[np.uint8]
    geo: GeoTransformInfo
    brytlinjer: tuple[BrytlinjePost, ...]
    oppningar: tuple[OppningPost, ...]
    filer: dict[str, Path]


def k1_filer(mapp: str | Path) -> dict[str, Path]:
    """K1-filerna i en mapp, per roll. mark/osakerhet/markslag krävs; gpkg-lagren är valfria."""
    m = Path(mapp)
    filer: dict[str, Path] = {}
    for roll, namn in (("mark", "mark.tif"), ("osakerhet", "osakerhet.tif"),
                       ("markslag", "markslag.tif")):
        p = m / namn
        if not p.is_file():
            raise BlaytorFel(f"K1-filen {namn} saknas i '{m}'.")
        filer[roll] = p
    for roll, namn in (("brytlinjer", "brytlinjer.gpkg"), ("oppningar", "oppningar.gpkg")):
        if (m / namn).is_file():
            filer[roll] = m / namn
    return filer


def las_k1(filer: Mapping[str, Path]) -> K1Indata:
    mark_raw, geo = las_geotiff(filer["mark"])
    osak_raw, _ = las_geotiff(filer["osakerhet"])
    slag_raw, _ = las_geotiff(filer["markslag"])
    bryt = tuple(las_brytlinjer_fran_gpkg(filer["brytlinjer"])) if "brytlinjer" in filer else ()
    opp = tuple(las_oppningar_fran_gpkg(filer["oppningar"])) if "oppningar" in filer else ()
    return K1Indata(
        mark_z=np.asarray(mark_raw, dtype=np.float32),
        osakerhet_z=np.asarray(osak_raw, dtype=np.float32),
        markslag=np.asarray(slag_raw, dtype=np.uint8),
        geo=geo,
        brytlinjer=bryt,
        oppningar=opp,
        filer=dict(filer),
    )


def bygg_graf(k1: K1Indata, persistens_faktor: float | None = None) -> SankGraf:
    graf = bygg_sankgraf(
        k1.mark_z, k1.osakerhet_z, k1.markslag, k1.geo,
        brytlinjer=k1.brytlinjer, oppningar=k1.oppningar,
    )
    if persistens_faktor is not None:
        graf = filtrera_persistens(graf, faktor=persistens_faktor)
    return graf


@dataclass(frozen=True)
class SankaUtfall:
    nod_id: int
    niva_rh2000: float
    volym_m3: float
    max_volym_m3: float
    fylld: bool
    """Sänkan är full till sin överfallsnivå (vatten rinner vidare)."""


@dataclass(frozen=True)
class BlaytorResultat:
    regn_mm: float
    netto_regn_mm_medel: float
    djup_m: NDArray[np.float32]
    vattenniva_rh2000: NDArray[np.float32]
    sankor: tuple[SankaUtfall, ...]
    in_m3: float
    lagrat_m3: float
    ut_m3: float
    volymfel_rel: float
    kortid_s: float
    fyllning: StatiskFyllningResultat
    takvatten_matvarden: dict[str, Any] = field(default_factory=dict)

    def blayta_m2_for(self, cell_area_m2: float, min_djup_m: float = 0.0) -> float:
        return float(np.count_nonzero(self.djup_m > min_djup_m)) * cell_area_m2

    def matvarden(self) -> dict[str, Any]:
        return {
            "regn_mm": self.regn_mm,
            "netto_regn_mm_medel": self.netto_regn_mm_medel,
            "in_m3": self.in_m3,
            "lagrat_m3": self.lagrat_m3,
            "ut_m3": self.ut_m3,
            "volymfel_rel": self.volymfel_rel,
            "max_djup_m": float(np.max(self.djup_m)) if self.djup_m.size else 0.0,
            "antal_fyllda_sankor": sum(1 for s in self.sankor if s.fylld),
            "kortid_s": self.kortid_s,
            # Slutna områden (K2/v2), bara när de finns: se `sankgraf.typer.SLUTSANKA_VARNING`.
            **slutsanka_matvarden(self.fyllning.slutsankor),
            **self.takvatten_matvarden,
            "volymbalans": self.fyllning.volymbalans(),
            **(
                {"varningar": list(slutsanka_varningar(self.fyllning.slutsankor))}
                if self.fyllning.slutsankor
                else {}
            ),
        }


def netto_regn_mm(
    markslag: NDArray[np.uint8],
    regn_mm: float,
    initialforlust_mm: Mapping[int, float] | None,
) -> NDArray[np.float64]:
    """Regn minus initialförlust per markslag, aldrig under 0. Utan förluster: regnet rakt av.

    Saknas ett markslag som förekommer (utom byggnad) i `initialforlust_mm` blir det fel –
    ingen förlust gissas.
    """
    if not (math.isfinite(regn_mm) and regn_mm >= 0):
        raise BlaytorFel(f"Regnmängden måste vara ändlig och ≥ 0 mm, fick {regn_mm}.")
    ut = np.full(markslag.shape, float(regn_mm), dtype=np.float64)
    if initialforlust_mm is None:
        return ut
    forekommande = {int(k) for k in np.unique(markslag)} - {MARKSLAG_BYGGNAD}
    saknas = sorted(forekommande - {int(k) for k in initialforlust_mm})
    if saknas:
        raise BlaytorFel(f"Initialförlust saknas för markslag {saknas}.")
    for kod, mm in initialforlust_mm.items():
        if not (math.isfinite(mm) and mm >= 0):
            raise BlaytorFel(f"Initialförlust för markslag {kod} måste vara ≥ 0 mm, fick {mm}.")
        ut[markslag == int(kod)] = max(float(regn_mm) - float(mm), 0.0)
    return ut


def blaytor_vid_mm(
    graf: SankGraf,
    regn_mm: float,
    *,
    initialforlust_mm: Mapping[int, float] | None = None,
    takvatten: bool = True,
    takvatten_andel_ledning: float = 0.0,
) -> BlaytorResultat:
    """Vilka sänkor fylls, och hur djupt, när `regn_mm` mm har runnit av (K2 in, K6-likt ut).

    `takvatten` (standard på): bygger takvattnet ur grafens raster om grafen inte redan bär det
    (byggnader som cellgrupper, okänd takform); `False` = regn på tak räknas inte.
    """
    t0 = time.perf_counter()
    if not takvatten:
        graf = med_takvatten(graf, None)
    elif graf.takvatten is None or abs(
        graf.takvatten.andel_ledning - float(takvatten_andel_ledning)
    ) > 1e-12:
        graf = med_takvatten_graf(graf, andel_ledning=takvatten_andel_ledning)
    netto = netto_regn_mm(graf.markslag, regn_mm, initialforlust_mm)
    fyll = statisk_fyllning(graf, netto / 1000.0)
    sankor = tuple(
        SankaUtfall(
            nod_id=nid,
            niva_rh2000=float(fyll.nod_niva_rh2000[nid]),
            volym_m3=float(fyll.nod_volym_m3.get(nid, 0.0)),
            max_volym_m3=float(nod.max_volym_m3),
            fylld=float(fyll.nod_volym_m3.get(nid, 0.0)) >= nod.max_volym_m3 - FYLLD_TOL_M3
            and nod.max_volym_m3 > 0,
        )
        for nid, nod in sorted(graf.noder.items())
        if nid in fyll.nod_niva_rh2000
    )
    aktiv = graf.markslag != MARKSLAG_BYGGNAD
    return BlaytorResultat(
        regn_mm=float(regn_mm),
        netto_regn_mm_medel=float(np.mean(netto[aktiv])) if np.any(aktiv) else 0.0,
        djup_m=fyll.djup_m,
        vattenniva_rh2000=fyll.vattenniva_rh2000,
        sankor=sankor,
        in_m3=fyll.in_m3,
        lagrat_m3=fyll.lagrat_m3,
        ut_m3=fyll.ut_m3,
        volymfel_rel=fyll.volymfel_rel,
        kortid_s=time.perf_counter() - t0,
        fyllning=fyll,
        takvatten_matvarden=(
            graf.takvatten.sammanfattning()
            if graf.takvatten is not None
            else {"takvatten": False, "takvatten_antagande": TAKVATTEN_AV_ANTAGANDE}
        ),
    )


class Blaytemodell:
    """Håller sänkgrafen i minnet och svarar på många regnmängder (svep, förhandsvisning)."""

    def __init__(self, graf: SankGraf) -> None:
        if not graf.noder and graf.mark_z.size == 0:
            raise SankGrafFel("Tom sänkgraf.")
        self.graf = graf

    @classmethod
    def fran_k1_mapp(cls, mapp: str | Path, persistens_faktor: float | None = None) -> Blaytemodell:
        # Takvattnet med rutans byggnader (byggnader.gpkg om K1 v2); `vid_mm(takvatten=False)`
        # räknar utan.
        graf = bygg_graf(las_k1(k1_filer(mapp)), persistens_faktor)
        return cls(med_takvatten_graf(graf, mapp))

    def vid_mm(
        self,
        regn_mm: float,
        *,
        initialforlust_mm: Mapping[int, float] | None = None,
        takvatten: bool = True,
    ) -> BlaytorResultat:
        return blaytor_vid_mm(
            self.graf, regn_mm, initialforlust_mm=initialforlust_mm, takvatten=takvatten
        )


def skriv_blaytor(
    resultat: BlaytorResultat,
    geo: GeoTransformInfo,
    ut_mapp: str | Path,
    *,
    indata_filer: Mapping[str, Path] | None = None,
    parametrar: Mapping[str, Any] | None = None,
) -> dict[str, Path]:
    """Skriver `max_djup.tif` + `run.json` (via K6-skrivaren) och `blaytor.json` per sänka."""
    mal = Path(ut_mapp)
    poster = [FilPost(r, p.name, sha256_fil(p)) for r, p in sorted((indata_filer or {}).items())]
    p: dict[str, Any] = {"regn_mm": resultat.regn_mm, "analys": "blaytor_mm"}
    p.update(parametrar or {})
    filer = skriv_statiskt_resultat_k6(resultat.fyllning, geo, mal, indata=poster, parametrar=p)
    tabell = mal / "blaytor.json"
    tabell.write_text(
        json.dumps(
            {
                "regn_mm": resultat.regn_mm,
                "sankor": [s.__dict__ for s in resultat.sankor],
                "matvarden": resultat.matvarden(),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    filer["blaytor"] = tabell
    return filer
