"""Planering och körning av utredningsfrågor (T20).

Varje analys × scenario blir ett eller flera jobb:
- globala lösare (`paverkansradie_m` = None): ett jobb över hela scenariot,
- rutvisa lösare: ett jobb per ruta, vars nyckel bara beror på rutor inom radien.

Nyckeln är sha256 av kanonisk JSON över lösarens namn och version, de validerade
parametrarna, extra indata (sha256), berörda rutors innehållshash och nycklarna för de
jobb man beror på. Samma nyckel betyder samma resultat, så ett jobb med en nyckel som
redan finns i cachen körs inte igen.
"""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost, skapa_manifest, skriv_manifest
from karna.analyslosare import (
    Analyslosare,
    BeroendeResultat,
    LosarFel,
    LosarKontext,
    LosarUtdata,
    hamta_losare,
)
from karna.hashning import kanonisk_json
from karna.hashning import nyckel_for as kanonisk_hash
from karna.resultatcache import ResultatCache
from karna.rutscenario import Ruta, RutScenario, materialisera_scenario
from karna.utredning import Analys, KorbarFraga

KONTRAKT_SAMMANSTALLNING = "K20/v1-sammanstallning"


@dataclass
class Jobb:
    """Ett planerat jobb. `ruta` är None för globala jobb."""

    analys: Analys
    losare: Analyslosare
    scenario: RutScenario
    parametrar: dict[str, Any]
    radie_m: float | None
    ruta: Ruta | None
    grannar: tuple[Ruta, ...]
    beroende_nycklar: dict[str, list[tuple[str, str]]]
    nyckel_objekt: dict[str, Any]
    nyckel: str
    i_cache: bool = False
    kortid_s: float | None = None
    utdata: dict[str, Any] = field(default_factory=dict)

    @property
    def etikett(self) -> str:
        del_ruta = f"/{self.ruta.ruta_id}" if self.ruta is not None else ""
        return f"{self.analys.id}/{self.scenario.id}{del_ruta}"


@dataclass
class Plan:
    """Alla jobb för en fråga, i körordning."""

    fraga: KorbarFraga
    scenarier: dict[str, RutScenario]
    jobb: list[Jobb]

    def jobb_for(self, analys_id: str, scenario_id: str) -> list[Jobb]:
        return [
            j for j in self.jobb if j.analys.id == analys_id and j.scenario.id == scenario_id
        ]

    def sammanfattning(self) -> dict[str, int]:
        traffar = sum(1 for j in self.jobb if j.i_cache)
        return {"jobb": len(self.jobb), "i_cache": traffar, "att_kora": len(self.jobb) - traffar}


def _indata_hashar(analys: Analys, losare: Analyslosare) -> dict[str, str]:
    saknas = sorted(set(losare.indataroller) - set(analys.indata))
    if saknas:
        raise LosarFel(f"Analys '{analys.id}' ({losare.namn}) saknar indata för rollerna {saknas}.")
    okanda = sorted(
        set(analys.indata) - set(losare.indataroller) - set(losare.valfria_indataroller)
    )
    if okanda:
        raise LosarFel(
            f"Analys '{analys.id}': {losare.namn} känner inte till indataroller {okanda}."
        )
    ut: dict[str, str] = {}
    for roll, fil in sorted(analys.indata.items()):
        if not fil.is_file():
            raise LosarFel(f"Analys '{analys.id}': indata '{roll}' finns inte: {fil}")
        ut[roll] = sha256_fil(fil)
    return ut


def planera(fraga: KorbarFraga, cache: ResultatCache) -> Plan:
    """Materialiserar scenarier och räknar fram alla jobbnycklar utan att köra lösarna."""
    scenarier = {sid: materialisera_scenario(spec, cache) for sid, spec in fraga.scenarier.items()}
    jobb: list[Jobb] = []
    # (analys, scenario) -> lista av (ruta_id eller "*", nyckel)
    nycklar: dict[tuple[str, str], list[tuple[str, str]]] = {}
    radier: dict[str, float | None] = {}

    for analys in fraga.analysordning():
        losare = hamta_losare(analys.losare)
        # Lösaren får exakt det som hashas (t.ex. 3.0 blir 3), så samma nyckel alltid
        # betyder samma indata till lösaren.
        parametrar = json.loads(kanonisk_json(losare.validera_parametrar(analys.parametrar)))
        radie = losare.paverkansradie_m(parametrar)
        if radie is not None and (radie < 0.0 or radie != radie):
            raise LosarFel(f"{losare.namn}: påverkansradien måste vara >= 0, fick {radie}.")
        radier[analys.id] = radie
        indata = _indata_hashar(analys, losare)

        for sid in analys.scenarier:
            scen = scenarier[sid]
            enheter: list[tuple[Ruta | None, tuple[Ruta, ...]]]
            if radie is None:
                enheter = [(None, ())]
            else:
                enheter = [(r, scen.grannar(r, radie)) for r in scen.rutor]

            for ruta, grannar in enheter:
                rutor = scen.rutor if ruta is None else (ruta, *grannar)
                rut_ids = {r.ruta_id for r in rutor}
                ber: dict[str, list[tuple[str, str]]] = {}
                for b in analys.beror_pa:
                    alla = nycklar[(b, sid)]
                    if radier[b] is None or ruta is None:
                        ber[b] = list(alla)
                    else:
                        ber[b] = [(rid, n) for rid, n in alla if rid in rut_ids]
                objekt = {
                    "slag": "jobb",
                    "losare": losare.namn,
                    "version": losare.version,
                    "parametrar": parametrar,
                    "indata": indata,
                    "radie_m": radie,
                    "ruta": None if ruta is None else ruta.ruta_id,
                    "rutor": sorted([r.ruta_id, r.hash] for r in rutor),
                    "beroenden": {k: sorted(v) for k, v in sorted(ber.items())},
                }
                nyckel = kanonisk_hash(objekt)
                j = Jobb(
                    analys=analys,
                    losare=losare,
                    scenario=scen,
                    parametrar=parametrar,
                    radie_m=radie,
                    ruta=ruta,
                    grannar=grannar,
                    beroende_nycklar=ber,
                    nyckel_objekt=objekt,
                    nyckel=nyckel,
                    i_cache=cache.finns("jobb", nyckel),
                )
                jobb.append(j)
                nycklar.setdefault((analys.id, sid), []).append(
                    ("*" if ruta is None else ruta.ruta_id, nyckel)
                )
    return Plan(fraga=fraga, scenarier=scenarier, jobb=jobb)


def _beroende_resultat(j: Jobb, cache: ResultatCache, plan: Plan) -> dict[str, BeroendeResultat]:
    ut: dict[str, BeroendeResultat] = {}
    for b, lista in j.beroende_nycklar.items():
        b_losare = plan.fraga.analys(b).losare
        if len(lista) == 1 and lista[0][0] == "*":
            ut[b] = BeroendeResultat(
                analys_id=b, losare=b_losare, mapp=cache.jobbmapp(lista[0][1]) / "utdata"
            )
        else:
            ut[b] = BeroendeResultat(
                analys_id=b,
                losare=b_losare,
                per_ruta={rid: cache.jobbmapp(n) / "utdata" for rid, n in lista},
            )
    return ut


def _kor_jobb(j: Jobb, cache: ResultatCache, plan: Plan) -> None:
    def skrivare(tmp: Path) -> dict[str, Any]:
        utmapp = tmp / "utdata"
        utmapp.mkdir()
        # Rutvisa jobb ser bara rutorna i nyckeln, så att en lösare inte kan läsa en ruta
        # vars ändring inte syns i nyckeln.
        synligt = (
            j.scenario
            if j.ruta is None
            else replace(j.scenario, rutor=(j.ruta, *j.grannar))
        )
        kontext = LosarKontext(
            utmapp=utmapp,
            scenario=synligt,
            parametrar=dict(j.parametrar),
            indata=dict(j.analys.indata),
            beroenden=_beroende_resultat(j, cache, plan),
            ruta=j.ruta,
            grannar=j.grannar,
        )
        t0 = time.perf_counter()
        resultat = j.losare.kor(kontext)
        kortid = time.perf_counter() - t0
        if not isinstance(resultat, LosarUtdata):
            raise LosarFel(f"{j.losare.namn} returnerade inte LosarUtdata.")
        saknas = sorted(set(j.losare.utdataroller) - set(resultat.filer))
        if saknas:
            raise LosarFel(f"{j.losare.namn} lämnade inte utdataroller {saknas} ({j.etikett}).")
        poster: list[FilPost] = []
        filer: dict[str, dict[str, str]] = {}
        for roll, namn in sorted(resultat.filer.items()):
            fil = utmapp / namn
            if not fil.is_file():
                raise LosarFel(f"{j.losare.namn}: utdata '{roll}' finns inte: {namn}")
            sha = sha256_fil(fil)
            poster.append(FilPost(roll=roll, fil=namn, sha256=sha))
            filer[roll] = {"fil": namn, "sha256": sha}
        matvarden = json.loads(kanonisk_json(dict(resultat.matvarden)))
        (tmp / "nyckel.json").write_text(
            json.dumps(j.nyckel_objekt, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (tmp / "utdata.json").write_text(
            json.dumps(
                {"filer": filer, "matvarden": matvarden, "varningar": list(resultat.varningar)},
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        manifest = skapa_manifest(
            modul=f"karna:{j.losare.namn}",
            indata=[
                FilPost(roll=f"ruta:{rid}", fil=rid, sha256=h)
                for rid, h in j.nyckel_objekt["rutor"]
            ]
            + [
                FilPost(roll=f"indata:{roll}", fil=str(j.analys.indata[roll]), sha256=h)
                for roll, h in j.nyckel_objekt["indata"].items()
            ],
            parametrar={
                "losare_version": j.losare.version,
                "nyckel": j.nyckel,
                **j.parametrar,
            },
            utdata=poster,
            matvarden={**matvarden, "kortid_s": round(kortid, 6)},
            varningar=list(resultat.varningar),
        )
        skriv_manifest(manifest, tmp / "run.json")
        return {"kortid_s": kortid}

    t0 = time.perf_counter()
    cache.skapa("jobb", j.nyckel, skrivare)
    j.kortid_s = time.perf_counter() - t0


def las_jobbutdata(cache: ResultatCache, nyckel: str) -> dict[str, Any]:
    """Läser `utdata.json` för ett cachat jobb."""
    data: dict[str, Any] = json.loads(
        (cache.jobbmapp(nyckel) / "utdata.json").read_text(encoding="utf-8")
    )
    return data


@dataclass
class Korresultat:
    """Resultatet av en körd fråga."""

    plan: Plan
    utmapp: Path
    sammanstallning: dict[str, Any]


def kor_utredning(
    fraga: KorbarFraga,
    cache: ResultatCache,
    utmapp: str | Path,
    *,
    plan: Plan | None = None,
) -> Korresultat:
    """Kör alla jobb som inte finns i cachen och sammanställer resultatet i `utmapp`."""
    from karna.jamfor import kor_jamforelse

    t_start = time.perf_counter()
    plan = plan or planera(fraga, cache)
    for j in plan.jobb:
        if j.i_cache:
            j.kortid_s = 0.0
        else:
            _kor_jobb(j, cache, plan)
        j.utdata = las_jobbutdata(cache, j.nyckel)

    ut = Path(utmapp)
    if ut.exists():
        # Töm bara en mapp som är ett tidigare frågeresultat, aldrig en godtycklig katalog.
        if any(ut.iterdir()) and not (ut / "sammanstallning.json").is_file():
            raise LosarFel(
                f"Utmappen {ut} finns och är inte ett tidigare frågeresultat; "
                "välj en ny eller tom mapp."
            )
        shutil.rmtree(ut)
    ut.mkdir(parents=True)
    for j in plan.jobb:
        mal = ut / j.analys.id / j.scenario.id
        if j.ruta is not None:
            mal = mal / j.ruta.ruta_id
        shutil.copytree(cache.jobbmapp(j.nyckel) / "utdata", mal)
        shutil.copy2(cache.jobbmapp(j.nyckel) / "run.json", mal / "run.json")

    jamforelser = [kor_jamforelse(jf, plan, cache, ut / "jamforelser") for jf in fraga.jamforelser]

    sammanstallning: dict[str, Any] = {
        "kontrakt": KONTRAKT_SAMMANSTALLNING,
        "fraga_id": fraga.fraga_id,
        "beskrivning": fraga.beskrivning,
        "scenarier": {
            sid: {
                "nyckel": s.nyckel,
                "rutor": {r.ruta_id: r.hash for r in s.rutor},
                "lappar": [str(p) for p in s.lappar],
            }
            for sid, s in plan.scenarier.items()
        },
        "jobb": [
            {
                "analys": j.analys.id,
                "losare": j.losare.namn,
                "version": j.losare.version,
                "scenario": j.scenario.id,
                "ruta": None if j.ruta is None else j.ruta.ruta_id,
                "nyckel": j.nyckel,
                "fran_cache": j.i_cache,
                "kortid_s": round(j.kortid_s or 0.0, 6),
                "matvarden": j.utdata.get("matvarden", {}),
                "varningar": j.utdata.get("varningar", []),
            }
            for j in plan.jobb
        ],
        "jamforelser": jamforelser,
        "sammanfattning": {
            **plan.sammanfattning(),
            "total_tid_s": round(time.perf_counter() - t_start, 6),
        },
    }
    (ut / "sammanstallning.json").write_text(
        json.dumps(sammanstallning, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    indata = []
    if fraga.kalla is not None:
        indata.append(FilPost(roll="fraga", fil=str(fraga.kalla), sha256=sha256_fil(fraga.kalla)))
    manifest = skapa_manifest(
        modul="karna",
        indata=indata,
        parametrar={"fraga_id": fraga.fraga_id},
        utdata=[
            FilPost(
                roll="sammanstallning",
                fil="sammanstallning.json",
                sha256=sha256_fil(ut / "sammanstallning.json"),
            )
        ],
        matvarden=dict(sammanstallning["sammanfattning"]),
    )
    skriv_manifest(manifest, ut / "run.json")
    return Korresultat(plan=plan, utmapp=ut, sammanstallning=sammanstallning)


def beskriv_plan(plan: Plan) -> list[Mapping[str, Any]]:
    """Läsbar beskrivning av planen, för `floden fraga plan`."""
    return [
        {
            "jobb": j.etikett,
            "losare": f"{j.losare.namn}@{j.losare.version}",
            "radie_m": j.radie_m,
            "grannar": [g.ruta_id for g in j.grannar],
            "nyckel": j.nyckel[:12],
            "status": "i cache" if j.i_cache else "körs",
        }
        for j in plan.jobb
    ]
