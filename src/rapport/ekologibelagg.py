# ruff: noqa: E501
"""Belägg för ekologisk konnektivitet (habitatnätverk, ECA och PC), ur körningens K0-manifest och `ekologi.json` (R2).

Lösaren `ekologi.konnektivitet` (K45) skriver ett K0-manifest per scenario (och ruta) med mätvärdena
`eca_m2`, `eca_min_m2`, `eca_max_m2`, `pc`, `pc_min`, `pc_max`, `antal_flackar`,
`total_habitat_area_m2` och `cellstorlek_m`, och en `ekologi.json` med nätverkets parametrar.
Rapporten räknar inget själv. Enskilda mått hämtas med den vanliga adressen
`matvarde.<analys>.<scenario>[.<ruta>].<mått>` (`belaggskalla.EKOLOGI_MATT`); den här modulen
lägger till två adresstyper:

| Adress | Mått |
| --- | --- |
| `ekojamforelse.<analys>.<fore>.<efter>[.<ruta>].<mått>` | skillnad efter minus före: `delta_eca_m2`, `delta_eca_procent`, `delta_pc`, `delta_antal_flackar`, `delta_total_habitat_area_m2` |
| `ekonatverk.<analys>.<scenario>[.<ruta>].<parameter>` | nätverkets parametrar ur körningen: `troskel_m`, `d50_m`, `troskel_hog_m`, `d50_hog_m`, `min_area_m2`, `cellstorlek_m`, `analys_area_m2` |

Skillnaden räknas bara när före och efter är jämförbara: samma nätverk (kontrollsumma), samma
huvudscenario, samma cellstorlek och samma analysparametrar (annars `BelaggFel`). Skillnaden är
differensen av två belägg; kontrollsumman är en hash av båda körningsfilernas kontrollsummor.
Nätverksparametrarna är indata till måttet och gäller alla nämnare (statistik "parameter (indata)").
Resultatet är *potentiell livsmiljö, okänd kvalitet*: markunderlaget (NMD) säger inget om ålder,
struktur eller naturvärde.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Final

from floden_mcp.belagg import enhet_for
from floden_mcp.karna_api import FlodenFel
from gemensamt.hash import sha256_fil
from rapport.pastaende import Belagg
from rapport.sprak import scenarionamn

if TYPE_CHECKING:
    from rapport.belaggskalla import FlodenBelaggKalla

EKOLOGI_LOSARE: Final[str] = "ekologi.konnektivitet"
DELTAMATT: Final[dict[str, str]] = {
    "delta_eca_m2": "eca_m2",
    "delta_eca_procent": "eca_m2",
    "delta_pc": "pc",
    "delta_antal_flackar": "antal_flackar",
    "delta_total_habitat_area_m2": "total_habitat_area_m2",
}
"""Jämförelsemått -> mätvärdet i manifestet som differensen tas av."""
NATVERKSPARAMETRAR: Final[tuple[str, ...]] = (
    "troskel_m",
    "d50_m",
    "troskel_hog_m",
    "d50_hog_m",
    "min_area_m2",
    "cellstorlek_m",
    "analys_area_m2",
)
_PARAM_STAT: Final[str] = "parameter (indata) i nätverkets fil i körningen, gäller alla nämnare"
_TID: Final[str] = "ingen tidsdimension (statisk landskapsmodell: ett läge, inget förlopp)"


def _fel(text: str) -> Exception:
    from rapport.belaggskalla import BelaggFel

    return BelaggFel(text)


def _dela(delar: list[str], antal_fasta: int, adress: str, form: str) -> tuple[list[str], str | None]:
    """Delar adressen i fasta delar och en valfri ruta (näst sista delen)."""
    if len(delar) == antal_fasta:
        return delar, None
    if len(delar) == antal_fasta + 1:
        ruta = delar[-2]
        return [*delar[:-2], delar[-1]], ruta
    raise _fel(f"Adressen '{adress}' ska vara {form}.")


def _dok(
    kalla: FlodenBelaggKalla, korning_id: str, analys: str, scenario: str, ruta: str | None
) -> tuple[dict[str, Any], str, str, str, str]:
    """`ekologi.json` för (analys, scenario, ruta): (dok, relativ sökväg, sha256, manifest, manifest-sha256)."""
    try:
        svar = kalla.tjanst.resultat_sammanfattning(
            korning_id, analys=analys, scenario=scenario, ruta=ruta, max_rader=500
        )
    except FlodenFel as exc:
        raise _fel(str(exc)) from exc
    rader = [r for t in svar["matvarden"] for r in t["rader"]]
    if not rader:
        raise _fel(f"Scenariot '{scenario}' finns inte för analysen '{analys}' i '{korning_id}'.")
    if len(rader) > 1 and ruta is None:
        raise _fel(
            f"Analysen '{analys}' har flera rutor i scenariot '{scenario}'; ange rutan i adressen."
        )
    manifest_rel = str(rader[0]["belagg"]["fil"])
    utmapp = kalla.utmapp(korning_id)
    fil = (utmapp / manifest_rel).with_name("ekologi.json")
    if not fil.is_file():
        raise _fel(f"Resultatfilen ekologi.json saknas för {analys}/{scenario} i '{korning_id}'.")
    _m, msha = kalla._manifest(korning_id, manifest_rel)  # noqa: SLF001
    dok = json.loads(fil.read_text(encoding="utf-8"))
    return dok, str(fil.relative_to(utmapp)), sha256_fil(fil), manifest_rel, msha


def _losare(kalla: FlodenBelaggKalla, korning_id: str, analys: str, adress: str) -> str:
    losare = kalla._losare(korning_id, analys)  # noqa: SLF001
    if losare != EKOLOGI_LOSARE:
        raise _fel(f"Analysen '{analys}' i '{adress}' är inte ekologisk konnektivitet ({losare}).")
    return losare


def _natverksvarde(dok: Mapping[str, Any], parameter: str) -> tuple[float, str, str, float]:
    """(värde, enhet, storhet, upplösning) för en nätverksparameter."""
    nv = dok.get("natverk", {})
    scen = nv.get("scenarier") or []
    hog: Mapping[str, Any] | None = max(scen, key=lambda s: float(s["troskel_m"])) if scen else None
    antagen = bool(nv.get("d50_antagen"))
    if parameter == "troskel_m":
        return float(nv["troskel_m"]), "m", "spridningströskel i effektiva meter, huvudscenariot", 1.0
    if parameter == "d50_m":
        return (
            float(nv["d50_m"]), "m",
            "d50, avståndet i effektiva meter där spridningssannolikheten är hälften, huvudscenariot"
            + (" (antagande: sannolikheten p saknas i källan)" if antagen else " (angiven i källan)"),
            1.0,
        )  # fmt: skip
    if parameter in ("troskel_hog_m", "d50_hog_m") and hog is None:
        raise _fel("Nätverkets scenarier saknas i ekologi.json.")
    if parameter == "troskel_hog_m" and hog is not None:
        return float(hog["troskel_m"]), "m", "längsta spridningströskel i effektiva meter bland nätverkets scenarier", 1.0
    if parameter == "d50_hog_m" and hog is not None:
        return (
            float(hog["d50_m"]), "m",
            "d50 vid nätverkets längsta spridningströskel"
            + (" (antagande: sannolikheten p saknas i källan)" if hog.get("d50_antagen") else ""),
            1.0,
        )  # fmt: skip
    if parameter == "min_area_m2":
        return float(nv["min_area_m2"]), "m²", "minsta area för en habitatfläck", 1.0
    if parameter == "cellstorlek_m":
        return float(dok["cellstorlek_m"]), "m", "rastrets cellstorlek", 0.1
    if parameter == "analys_area_m2":
        return float(dok["analys_area_m2"]), "m²", "analyserad yta (rutan med kontextbuffert)", 1.0
    raise _fel(f"Okänd nätverksparameter '{parameter}'. Tillåtna: {', '.join(NATVERKSPARAMETRAR)}.")


def _natverk_belagg(
    kalla: FlodenBelaggKalla, adress: str, korning_id: str, delar: list[str]
) -> Belagg:
    form = "ekonatverk.<analys>.<scenario>[.<ruta>].<parameter>"
    d, ruta = _dela(delar, 4, adress, form)
    _t, analys, scenario, parameter = d
    losare = _losare(kalla, korning_id, analys, adress)
    dok, fil, sha, manifest, msha = _dok(kalla, korning_id, analys, scenario, ruta)
    try:
        varde, enhet, storhet, upp = _natverksvarde(dok, parameter)
    except (KeyError, TypeError, ValueError) as exc:
        raise _fel(f"Parametern '{parameter}' saknas i ekologi.json ({exc}).") from exc
    return Belagg(
        adress=adress,
        korning_id=korning_id,
        fil=fil,
        fil_sha256=sha,
        manifest=manifest,
        manifest_sha256=msha,
        analys=analys,
        losare=losare,
        scenarier=(scenario,),
        mottagare=f"scenario {scenario}" + (f", ruta {ruta}" if ruta else ""),
        namnare="parameter i nätverkets definition (gäller hela beräkningsområdet)",
        matt=f"natverk:{parameter}",
        storhet=storhet,
        enhet=enhet,
        tidsdefinition=_TID,
        statistik=_PARAM_STAT,
        varde=varde,
        upplosning=upp,
        jamforbarhet=kalla._jamforbarhet(korning_id, analys, (scenario,)),  # noqa: SLF001
    )


def _jamfor_belagg(
    kalla: FlodenBelaggKalla, adress: str, korning_id: str, delar: list[str]
) -> Belagg:
    form = "ekojamforelse.<analys>.<fore>.<efter>[.<ruta>].<mått>"
    d, ruta = _dela(delar, 5, adress, form)
    _t, analys, fore_id, efter_id, matt = d
    if matt not in DELTAMATT:
        raise _fel(f"Okänt jämförelsemått '{matt}' i '{adress}'. Tillåtna: {', '.join(DELTAMATT)}.")
    losare = _losare(kalla, korning_id, analys, adress)
    namn = DELTAMATT[matt]
    dok_f, fil_f, sha_f, man_f, msha_f = _dok(kalla, korning_id, analys, fore_id, ruta)
    dok_e, fil_e, sha_e, man_e, msha_e = _dok(kalla, korning_id, analys, efter_id, ruta)
    nf, ne = dok_f.get("natverk", {}), dok_e.get("natverk", {})
    for falt, a, b in (
        ("nätverkets kontrollsumma", nf.get("sha256"), ne.get("sha256")),
        ("huvudscenario", nf.get("scenario"), ne.get("scenario")),
        ("cellstorlek", dok_f.get("cellstorlek_m"), dok_e.get("cellstorlek_m")),
        ("tröskel", nf.get("troskel_m"), ne.get("troskel_m")),
    ):
        if a != b:
            raise _fel(
                f"{fore_id} och {efter_id} är inte jämförbara i '{adress}': {falt} skiljer ({a} mot {b})."
            )
    jfr = kalla._jamforbarhet(korning_id, analys, (fore_id, efter_id))  # noqa: SLF001
    # Mätvärdena hämtas som vanliga belägg (samma katalog, samma enhet), differensen tas här.
    pre = ".".join(["matvarde", analys, "{}", *([ruta] if ruta else []), namn])
    bf = kalla.hamta(pre.format(fore_id), korning_id, {})
    be = kalla.hamta(pre.format(efter_id), korning_id, {})
    skillnad = be.varde - bf.varde
    enhet: str | None = bf.enhet
    upp = bf.upplosning
    storhet = f"skillnad i {bf.storhet}"
    if matt == "delta_eca_procent":
        if not (math.isfinite(bf.varde) and bf.varde > 0):
            raise _fel(f"Läget före har ECA {bf.varde!r}; procentuell förändring kan inte räknas.")
        skillnad = skillnad / bf.varde * 100.0
        enhet = "%"
        upp = 100.0 * bf.upplosning / bf.varde
        storhet = f"relativ förändring av {bf.storhet} (efter minus före, i procent av läget före)"
    elif matt == "delta_antal_flackar":
        enhet = enhet_for("antal")
    sha = hashlib.sha256(f"{sha_f}:{sha_e}:{msha_f}:{msha_e}".encode()).hexdigest()
    return replace(
        bf,
        adress=adress,
        fil=f"{fil_f} + {fil_e}",
        fil_sha256=sha,
        manifest=f"{man_f} + {man_e}" if man_f != man_e else man_f,
        manifest_sha256=hashlib.sha256(f"{msha_f}:{msha_e}".encode()).hexdigest(),
        analys=analys,
        losare=losare,
        scenarier=(fore_id, efter_id),
        mottagare="hela beräkningsområdet" if ruta is None else f"ruta {ruta}",
        matt=f"{namn}:{matt}",
        storhet=storhet,
        enhet=enhet,
        statistik=f"differens efter minus före av {bf.statistik}",
        varde=float(skillnad),
        upplosning=float(upp),
        jamforbarhet=jfr,
    )


def _forsta_dok(
    kalla: FlodenBelaggKalla, korning_id: str, analys: str, scenario: str
) -> dict[str, Any] | None:
    """`ekologi.json` för scenariot (första rutan), eller None om filen saknas."""
    bas = kalla.utmapp(korning_id) / analys / scenario
    kandidater = [bas / "ekologi.json", *sorted(bas.glob("*/ekologi.json"))]
    fil = next((f for f in kandidater if f.is_file()), None)
    return None if fil is None else dict(json.loads(fil.read_text(encoding="utf-8")))


def ekologiantaganden(
    kalla: FlodenBelaggKalla, korning_id: str, analyser: Mapping[str, list[str]]
) -> list[str]:
    """Antaganden och förutsättningar som Floden själv skriver, ur nätverkets `ekologi.json`.

    `analyser`: analysens id -> scenarier. Texten står i rapportens antagandelista (inte som
    påstående): nätverk och källa, "potentiell livsmiljö, okänd kvalitet", nätverkets
    antaganden (bland dem "p saknas i källan; d50 = tröskel/2"), markunderlag, intervallets
    innebörd och lösarens varningar."""
    ut: list[str] = []
    varningar: list[str] = []
    for analys, scenarier in analyser.items():
        dok = _forsta_dok(kalla, korning_id, analys, scenarier[0]) if scenarier else None
        if dok is None:
            continue
        nv = dok.get("natverk", {})
        ut.append(
            f"Habitatnätverk {nv.get('namn')} ({nv.get('underlag')}, nätverk {nv.get('natverk_id')}). "
            f"{nv.get('kalla_rad', '')}".strip()
        )
        etikett = str(dok.get("resultat_etikett", "Potentiell livsmiljö, okänd kvalitet"))
        ut.append(
            f"Resultatet gäller {etikett[:1].lower() + etikett[1:]}: markunderlaget säger inget "
            "om ålder, struktur eller naturvärde. Metodstatus: skiss."
        )
        ut.extend(str(a) for a in nv.get("antaganden", []))
        ut.extend(f"Saknas i källan: {x['text']}" for x in nv.get("saknas", []) if x.get("text"))
        kr = nv.get("klassraster", {})
        mu = kr.get("markunderlag")
        rollnamn = {"nmd": "NMD (indataroll nmd)", "geografi": "NMD ur projektets geografi"}
        ut.append(
            f"Markunderlag: {rollnamn.get(str(kr.get('roll')), kr.get('roll'))}, cellstorlek "
            f"{_sv(dok.get('cellstorlek_m'))} m, habitatklasser NMD "
            + ", ".join(str(k) for k in nv.get("habitat_klasser", []))
            + (f"; geografi {mu.get('ref')}" if isinstance(mu, dict) and mu.get("ref") else "")
            + ". Byggnader ur planförslaget läggs ovanpå som NMD 51."
        )
        ut.append(
            "Intervallet är källbaserat: lägsta och högsta värde mellan nätverkets "
            "tröskelscenarier. Det är ingen statistisk konfidens."
        )
        for sc in scenarier:
            d2 = dok if sc == scenarier[0] else _forsta_dok(kalla, korning_id, analys, sc)
            if d2:
                varningar += [f"Scenario {scenarionamn(sc)}: {v}" for v in d2.get("varningar", [])]
    ut.extend(varningar)
    return list(dict.fromkeys(ut))


def _sv(v: Any) -> str:
    try:
        return f"{float(v):g}".replace(".", ",")
    except (TypeError, ValueError):
        return str(v)


def ekologi_belagg(
    kalla: FlodenBelaggKalla, adress: str, korning_id: str, delar: list[str]
) -> Belagg:
    """Belägg för `ekojamforelse.…` och `ekonatverk.…` (se modulens beskrivning)."""
    if delar[0] == "ekonatverk":
        return _natverk_belagg(kalla, adress, korning_id, delar)
    return _jamfor_belagg(kalla, adress, korning_id, delar)


__all__ = ["DELTAMATT", "EKOLOGI_LOSARE", "NATVERKSPARAMETRAR", "ekologi_belagg", "ekologiantaganden"]
