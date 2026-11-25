"""Fysikalisk nivå (fas 3): 2D lokal tröghet som lösare i kärnans graf.

Lösare `vatten.2d` (version 1): K1-filer + planscenario (K9, utplattat) + regn (K4) →
`kor_2d_lokal_troghet` med valfria sänktermer (`tvad.kalltermer`) → K6 (`max_djup.tif`,
`max_hastighet.tif`, `fara.tif`, `tid_till_0_1m.tif`, `tid_till_0_2m.tif`, `run.json`).

Analystyper (modul "vatten"), alla samma beräkning och därmed samma cachenyckel:
`maxdjup_2d`, `fara_2d`, `tid_till_djup`. Parametrar:

- regn: `regn_id` (inbyggt förval, `gemensamt.regn`) eller `regn_tid_s` + `regn_mm_h` (K4).
- `slut_tid_s` (valfri), `subgrid_faktor` (1 = full 2D), `ignorera_typer`.
- `avtappning_mm_h` (likformig kapacitet, standard 0 mm/h: ledningsnätet antas fullt),
  `avtappning_omraden` (valfria polygoner med eget värde, `sankgraf.avtappning`) och
  `brunnar_igensatta_andel` (K5). Värdet och källan står i mätvärdena.
- `takvatten` (standard på) och `takvatten_andel_ledning` (standard 0): regn på byggnader
  avrinner till marken längs fasaderna (`sankgraf.takvatten`; byggnaderna som cellgrupper,
  okänd takform = brutet tak).

Version 2 (2026-11-22): takvatten som standard.
- `infiltration`: `{jordart = "...", initial_mm = ...}` (valfri; utan den ingen infiltration
  och ingen initial förlust).

Basens K1-filer hittas via `vatten.registrera_bas`.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

from gemensamt.regn import skapa_regn_fran_spec
from karna.fraga import AnalysSteg, registrera_analystyp
from karna.graf import Losare, Nod
from karna.scenario import Andring, Planscenario
from lapp.tillampa import tillampa_lapp
from sankgraf.avtappning import avtappning_raster, validera_omraden
from sankgraf.avtappning import beskrivning as avtappning_beskrivning
from sankgraf.hierarki import _applicera_oppningar
from sankgraf.takvatten import TAKVATTEN_AV_ANTAGANDE, bygg_takvatten
from tvad.kalltermer import Avtappning, Infiltration2D, Kalltermer
from tvad.lokal_troghet import kor_2d_lokal_troghet, skriv_tvad_resultat_k6
from tvad.rannsten_1d import (
    diskretisera_rannsten_1d,
    exportera_rannsten_flode_geojson,
    kor_kopplad_1d_2d,
)
from vatten.blaytor import BlaytorFel, las_k1
from vatten.losare import _BASER, K1_ROLLER, MODUL, _tak
from vatten.scenario_lapp import scenario_till_lapp

ANALYSTYPER_2D = ("maxdjup_2d", "fara_2d", "tid_till_djup")
_PARAMETRAR = {
    "regn_id", "regn_tid_s", "regn_mm_h", "slut_tid_s", "subgrid_faktor", "ignorera_typer",
    "avtappning_mm_h", "brunnar_igensatta_andel", "infiltration",
    "avtappning_omraden", "takvatten", "takvatten_andel_ledning",
    "rannsten_1d", "rannsten_korbana_sida",
}


def _regn(p: Mapping[str, Any]) -> tuple[list[float], list[float]]:
    if p.get("regn_id") is not None:
        if p.get("regn_tid_s") is not None:
            raise BlaytorFel("Ange antingen regn_id eller regn_tid_s/regn_mm_h, inte båda.")
        serie = skapa_regn_fran_spec(regn_id=str(p["regn_id"]))
        return list(serie.tid_s), list(serie.intensitet_mm_h)
    if p.get("regn_tid_s") is None or p.get("regn_mm_h") is None:
        raise BlaytorFel("2D-analysen kräver regn_id eller regn_tid_s + regn_mm_h.")
    return [float(t) for t in p["regn_tid_s"]], [float(i) for i in p["regn_mm_h"]]


def _kor_2d(indata: Mapping[str, Path], p: Mapping[str, Any], ut: Path) -> dict[str, Any]:
    k1 = las_k1({r: indata[r] for r in K1_ROLLER if r in indata})
    mark_z, markslag = k1.mark_z, k1.markslag
    oppningar = k1.oppningar
    brytlinjer = list(k1.brytlinjer)
    andrade = 0
    if p.get("andringar"):
        scenario = Planscenario(
            scenario_id="scenario",
            bas_recept=tuple(p["bas_recept"]),
            andringar=tuple(Andring.fran_dict(a) for a in p["andringar"]),
        )
        som = scenario_till_lapp(scenario, ignorera_typer=tuple(p.get("ignorera_typer", ())))
        t = tillampa_lapp(
            k1.mark_z, k1.osakerhet_z, k1.markslag, k1.geo, som.lapp,
            bas_recept_sha256=str(p["bas_recept"][0]),
            bas_brytlinjer=k1.brytlinjer, bas_oppningar=k1.oppningar,
        )
        mark_z, markslag = t.mark_z, t.markslag
        brytlinjer = list(t.alla_brytlinjer)  # basens (efter ev. borttag), gatornas och ritade
        oppningar = t.oppningar
        andrade = int(np.count_nonzero(t.andrad_mask))
    if oppningar:
        mark_z, markslag = _applicera_oppningar(mark_z, markslag, k1.geo, oppningar)

    avt_mm_h = float(p.get("avtappning_mm_h", 0.0))
    omr = validera_omraden(p.get("avtappning_omraden"))
    kap = avtappning_raster(k1.geo, avt_mm_h, omr)
    angiven = bool(p.get("avtappning_angiven", "avtappning_mm_h" in p))
    avt_info = avtappning_beskrivning(avt_mm_h, omr, angiven=angiven)
    avtappning = None
    if np.any(kap > 0.0):
        avtappning = Avtappning(
            kap,
            brunnar_igensatta_andel=float(p.get("brunnar_igensatta_andel", 0.0)),
            kalla=str(avt_info["avtappning_kalla"]),
        )
    tak = _tak(p)
    takvatten = (
        bygg_takvatten(
            mark_z, markslag, k1.geo, andel_ledning=float(tak["takvatten_andel_ledning"])
        )
        if tak["takvatten"]
        else None
    )
    infiltration = None
    if p.get("infiltration") is not None:
        inf = p["infiltration"]
        infiltration = Infiltration2D.fran_markslag(
            markslag,
            inf["jordart"],
            initial_mm=float(inf["initial_mm"]),
            hog_markfukt=bool(inf.get("hog_markfukt", False)),
            tjale=bool(inf.get("tjale", False)),
        )
    termer = Kalltermer(avtappning=avtappning, infiltration=infiltration)
    tider, mm_h = _regn(p)
    res = kor_2d_lokal_troghet(
        mark_z, markslag, k1.geo, tider, mm_h,
        slut_tid_s=p.get("slut_tid_s"),
        subgrid_faktor=int(p.get("subgrid_faktor", 1)),
        kalltermer=termer,
        takvatten=takvatten,
        brytlinjer=brytlinjer,
    )
    skriv_tvad_resultat_k6(res, k1.geo, ut, parametrar={"losare": "vatten.2d"})

    # Valfri 1D-rännstenslänk längs 3D-kantstenar (`rannsten_1d="auto"` aktiveras automatiskt
    # när K1 brytlinjer.gpkg innehåller 3D-kantstenar med `z_kron_rh2000`).
    rannsten_lage = p.get("rannsten_1d", "auto")
    kantstenar_3d = [
        b
        for b in brytlinjer
        if b.typ in ("kantsten", "sankt_kantsten") and b.z_kron_rh2000 is not None
    ]
    kor_1d = bool(
        (rannsten_lage is True and any(b.typ in ("kantsten", "sankt_kantsten") for b in brytlinjer))
        or (rannsten_lage == "auto" and kantstenar_3d)
    )
    rannsten_info: dict[str, Any] = {"rannsten_1d_aktiv": False}
    if kor_1d:
        lank_linjer = kantstenar_3d or [
            b for b in brytlinjer if b.typ in ("kantsten", "sankt_kantsten")
        ]
        lank = diskretisera_rannsten_1d(
            lank_linjer,
            mark_z,
            markslag,
            k1.geo,
            korbana_sida=str(p.get("rannsten_korbana_sida", "vanster")),
        )
        res_1d = kor_kopplad_1d_2d(
            mark_z,
            markslag,
            k1.geo,
            tider,
            mm_h,
            lank,
            brytlinjer=brytlinjer,
            slut_tid_s=p.get("slut_tid_s"),
        )
        gj_fil = exportera_rannsten_flode_geojson(res_1d, ut / "rannsten_flode.geojson")
        rannsten_info = {
            "rannsten_1d_aktiv": True,
            "rannsten_1d_fil": gj_fil.name,
            "rannsten_1d_segment": lank.antal,
            "rannsten_1d_max_djup_m": float(np.max(res_1d.max_djup_1d_m)),
            "rannsten_1d_overfall_sankt_m3": res_1d.overfall_sankt_m3,
            "rannsten_1d_overfall_hog_m3": res_1d.overfall_hog_m3,
            "rannsten_1d_volymfel_rel": res_1d.volymfel_rel,
        }

    return {
        "max_djup_m": float(np.nanmax(res.max_djup_m)),
        "max_hastighet_m_s": float(np.nanmax(res.max_hastighet_m_s)),
        "max_fara_m2_s": float(np.nanmax(res.fara_m2_s)),
        "in_m3": res.in_m3,
        "ut_m3": res.ut_m3,
        "lagrat_m3": res.lagrat_m3,
        "avtappat_m3": res.avtappat_m3,
        "infiltrerat_m3": res.infiltrerat_m3,
        "initial_forlust_m3": res.initial_forlust_m3,
        "volymfel_rel": res.volymfel_rel,
        "antal_tidssteg": res.antal_tidssteg,
        "kortid_s": res.kortid_s,
        "andrade_celler": andrade,
        "sanktermer": termer.beskrivning(),
        **avt_info,
        **rannsten_info,
        **(
            takvatten.sammanfattning()
            if takvatten is not None
            else {"takvatten": False, "takvatten_antagande": TAKVATTEN_AV_ANTAGANDE}
        ),
        "volymbalans": res.volymbalans(),
    }


LOSARE_2D = Losare("vatten.2d", "2", _kor_2d, modul=MODUL)


def nod_2d(scenario: Planscenario, parametrar: Mapping[str, Any]) -> Nod:
    """Nod för 2D-körning av scenariot (basen om scenariot saknar ändringar)."""
    okanda = set(parametrar) - _PARAMETRAR
    if okanda:
        raise BlaytorFel(f"Okända parametrar för 2D-analysen: {sorted(okanda)}.")
    if len(scenario.bas_recept) != 1:
        raise BlaytorFel("2D-analysen stöder i dag ett bas_recept (en K1-ruta) per scenario.")
    bas = scenario.bas_recept[0]
    if bas not in _BASER:
        raise BlaytorFel(f"Basen {bas[:12]}… är inte registrerad (registrera_bas).")
    _regn(parametrar)  # validera tidigt
    p = dict(parametrar)
    p.update(_tak(p))
    p["avtappning_angiven"] = "avtappning_mm_h" in parametrar
    p["avtappning_mm_h"] = float(p.get("avtappning_mm_h", 0.0))
    if "avtappning_omraden" in p:
        p["avtappning_omraden"] = validera_omraden(p["avtappning_omraden"])
    p["ignorera_typer"] = sorted(p.get("ignorera_typer", ()))
    p["bas_recept"] = list(scenario.bas_recept)
    p["andringar"] = sorted(
        (a.till_dict() for a in scenario.andringar), key=lambda d: d["andring_id"]
    )
    return Nod(LOSARE_2D, dict(_BASER[bas]), p)


def _bygg(steg: AnalysSteg, scenario: Planscenario) -> Nod:
    if scenario.forald is not None:
        raise BlaytorFel("2D-analysen väntar sig ett utplattat scenario (kör via kor_fraga).")
    return nod_2d(scenario, steg.parametrar)


def registrera_2d() -> None:
    """Anmäler 2D-analystyperna till kärnan (idempotent)."""
    for typ in ANALYSTYPER_2D:
        registrera_analystyp(typ, MODUL, _bygg)
