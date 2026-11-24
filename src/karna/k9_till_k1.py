"""Översättning av ett K9-planscenario till en K3-lapp, så att det kan materialiseras i K1-rutor.

K9 är det domänneutrala formatet för planändringar. Kärnans körmotor (T20) räknar på K1-rutor;
för att kunna göra det översätts K9-ändringarna till en K3-lapp som tillämpas med
`lapp.tillampa` (samma väg som vattnet redan använder).

Detta är den enda översättningen (`vatten.scenario_lapp` är ett tunt lager ovanpå), så samma
K9-scenario ger samma K1-rutor oavsett väg:

- `byggnad_ny` → `LappByggnad`: kräver `entre_rh2000`, `entre_punkt`; valfritt `hojd_m`,
  `tak_rh2000`, `gransdjup_m`.
- `markhojd` → `HojdYta`: kräver exakt en av `niva_rh2000`, `lutning`, `delta_m` eller `landxml`;
  valfritt `slant` (`forhallande` 1:n eller `bredd_m`, ev. `markslag`), `markslag`, `osakerhet_m`.
- `markslag` → `LappMarkslag`: kräver `kod`.
- `gata` → `LappGata`: kräver `sektion`; valfritt `profil_rh2000`, `markslag_korbana`,
  `markslag_gangbana`.
- `magasin` → `LappMagasin`: kräver `volym_m3`; valfritt `botten_rh2000`, `markslag`.
Övriga typer ger ett tydligt fel i stället för att hoppas över: ett scenario som bara delvis
tillämpas ger resultat som ser rätt ut men inte är det.

K1 v2 (byggnadsregister, terräng och vegetation): `byggnad_bort`, `byggnad_hojd` och `vegetation`
tillämpas av `karna.geometri.tillampa` i stället för via K3, och `byggnad_ny` går dit för v2-rutor
(utan krav på entré). På en v1-ruta ger de tre förstnämnda ett fel som säger att rutan måste
byggas om med v2.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from karna.hashning import nyckel_for
from karna.scenario import Andring, Planscenario, ScenarioFel, las_scenario, platta_ut

# Höjs när översättningen ändras (ingår i rutornas lappnyckel).
K9_TILL_K3_VERSION = "1"
STODDA_TYPER: frozenset[str] = frozenset({"markhojd", "markslag", "magasin", "gata", "byggnad_ny"})
GEOMETRI_TYPER: frozenset[str] = frozenset({"byggnad_bort", "byggnad_hojd", "vegetation"})


def dela_andringar(
    andringar: Sequence[Andring], k1_version: int, scenario_id: str = ""
) -> tuple[tuple[Andring, ...], tuple[Andring, ...]]:
    """Delar K9-ändringar i (K3-delen, geometridelen) för en K1-version.

    v2: `byggnad_ny` hör till geometridelen. v1: geometrityperna ger fel.
    """
    if k1_version < 2:
        fel = sorted({a.typ for a in andringar} & GEOMETRI_TYPER)
        if fel:
            raise ScenarioFel(
                f"Scenariot '{scenario_id}': {', '.join(fel)} kräver K1 v2 (byggnadsregister, "
                "terrang.tif och vegetation.tif), men basen är K1 v1. Bygg om rutorna med v2."
            )
        return tuple(andringar), ()
    geometri = tuple(a for a in andringar if a.typ in GEOMETRI_TYPER or a.typ == "byggnad_ny")
    k3 = tuple(a for a in andringar if a not in geometri)
    return k3, geometri


def _ring(geometri: Mapping[str, Any] | None, a: Andring) -> tuple[tuple[float, float], ...]:
    if geometri is None or geometri.get("type") != "Polygon":
        raise ScenarioFel(f"{a.typ} '{a.andring_id}': geometrin måste vara en GeoJSON Polygon.")
    koord = geometri.get("coordinates")
    if not isinstance(koord, Sequence) or not koord or len(koord) > 1:
        raise ScenarioFel(
            f"{a.typ} '{a.andring_id}': polygonen måste ha exakt en ring (hål stöds inte än)."
        )
    punkter = [(float(p[0]), float(p[1])) for p in koord[0]]
    if len(punkter) > 1 and punkter[0] == punkter[-1]:
        punkter = punkter[:-1]
    return tuple(punkter)


def _linje(geometri: Mapping[str, Any] | None, a: Andring) -> tuple[tuple[float, float], ...]:
    if geometri is None or geometri.get("type") != "LineString":
        raise ScenarioFel(f"{a.typ} '{a.andring_id}': geometrin måste vara en GeoJSON LineString.")
    return tuple((float(p[0]), float(p[1])) for p in geometri.get("coordinates", []))


def _tal(e: Mapping[str, Any], namn: str, a: Andring) -> float:
    if namn not in e:
        raise ScenarioFel(f"{a.typ} '{a.andring_id}' saknar egenskapen '{namn}'.")
    v = e[namn]
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise ScenarioFel(f"{a.typ} '{a.andring_id}': '{namn}' måste vara ett tal, fick {v!r}.")
    return float(v)


def markhojd_till_hojdyta(a: Andring) -> Any:
    """Översätter ett K9 `markhojd` (inkl. R4: `lutning`, `delta_m`, `slant`) till en `HojdYta`."""
    from lapp.kontrakt import HojdYta, LappFel

    e = a.egenskaper
    ms = e.get("markslag")
    hk: dict[str, Any] = {}
    if "osakerhet_m" in e:
        hk["osakerhet_m"] = _tal(e, "osakerhet_m", a)
    nivaer = [k for k in ("niva_rh2000", "lutning", "delta_m") if k in e]
    if "landxml" in e:
        extra = [k for k in ("lutning", "delta_m", "slant") if k in e]
        if extra:
            raise ScenarioFel(
                f"markhojd '{a.andring_id}': {', '.join(extra)} kan inte kombineras med landxml."
            )
        hk["landxml"] = str(e["landxml"])
        if a.geometri is not None:
            hk["polygon"] = _ring(a.geometri, a)
    elif len(nivaer) == 1:
        hk["polygon"] = _ring(a.geometri, a)
        if nivaer[0] == "niva_rh2000":
            hk["hojd_rh2000"] = _tal(e, "niva_rh2000", a)
        elif nivaer[0] == "delta_m":
            hk["delta_m"] = _tal(e, "delta_m", a)
        else:
            hk["lutning"] = e["lutning"]
    elif not nivaer:
        raise ScenarioFel(
            f"markhojd '{a.andring_id}' saknar niva_rh2000, lutning, delta_m eller landxml."
        )
    else:
        raise ScenarioFel(
            f"markhojd '{a.andring_id}': ange exakt en av niva_rh2000, lutning och delta_m "
            f"(fick {', '.join(nivaer)})."
        )
    if "slant" in e:
        sl = e["slant"]
        if not isinstance(sl, Mapping) or not set(sl) <= {"forhallande", "bredd_m", "markslag"}:
            raise ScenarioFel(
                f"markhojd '{a.andring_id}': slant måste vara {{forhallande | bredd_m, markslag?}}."
            )
        if "forhallande" in sl:
            hk["slant_forhallande"] = float(sl["forhallande"])
        if "bredd_m" in sl:
            hk["slant_bredd_m"] = float(sl["bredd_m"])
        if "markslag" in sl:
            hk["slant_markslag"] = int(sl["markslag"])
    try:
        return HojdYta(yta_id=a.andring_id, markslag=None if ms is None else int(ms), **hk)
    except LappFel as exc:
        raise ScenarioFel(f"markhojd '{a.andring_id}': {exc}") from exc


def k9_till_lapp(andringar: Sequence[Andring], scenario: Planscenario) -> Any:
    """Bygger en `ProjektLapp` (K3) av utplattade K9-ändringar (bara K3-typerna).

    Den enda K9→K3-översättningen i Floden: `vatten.scenario_lapp` anropar den. Ändringarna
    sorteras efter `andring_id` (en enda deterministisk ordning), så lappen och dess nyckel beror
    inte på listordningen. Överlappande ändringar med olika id tillämpas alltså i id-ordning.
    """
    from lapp.kontrakt import (
        LappByggnad,
        LappGata,
        LappMagasin,
        LappMarkslag,
        ProjektLapp,
    )

    ej = sorted({a.typ for a in andringar} - STODDA_TYPER)
    if ej:
        raise ScenarioFel(
            f"Scenariot '{scenario.scenario_id}' har ändringstyper som inte kan tillämpas på "
            f"K1-rutor än: {', '.join(ej)}. Stöds: {', '.join(sorted(STODDA_TYPER))}."
        )
    hojdytor, markslag, magasin, gator, byggnader = [], [], [], [], []
    for a in sorted(andringar, key=lambda x: x.andring_id):
        e = a.egenskaper
        if a.typ == "markhojd":
            hojdytor.append(markhojd_till_hojdyta(a))
        elif a.typ == "markslag":
            markslag.append(
                LappMarkslag(
                    omrade_id=a.andring_id,
                    polygon=_ring(a.geometri, a),
                    markslag=int(_tal(e, "kod", a)),
                )
            )
        elif a.typ == "magasin":
            botten = e.get("botten_rh2000")
            mk: dict[str, Any] = {}
            if "markslag" in e:
                mk["markslag"] = int(_tal(e, "markslag", a))
            magasin.append(
                LappMagasin(
                    magasin_id=a.andring_id,
                    polygon=_ring(a.geometri, a),
                    volym_m3=_tal(e, "volym_m3", a),
                    botten_rh2000=None if botten is None else float(botten),
                    **mk,
                )
            )
        elif a.typ == "gata":
            if "sektion" not in e:
                raise ScenarioFel(f"gata '{a.andring_id}' saknar egenskapen 'sektion'.")
            profil = e.get("profil_rh2000")
            gk: dict[str, Any] = {}
            for namn in ("markslag_korbana", "markslag_gangbana"):
                if namn in e:
                    gk[namn] = int(_tal(e, namn, a))
            gator.append(
                LappGata(
                    gata_id=a.andring_id,
                    mittlinje=_linje(a.geometri, a),
                    sektion=str(e["sektion"]),
                    profil_rh2000=None if profil is None else tuple(float(z) for z in profil),
                    **gk,
                )
            )
        elif a.typ == "byggnad_ny":
            if "entre_rh2000" not in e or "entre_punkt" not in e:
                raise ScenarioFel(
                    f"byggnad_ny '{a.andring_id}': K1-materialiseringen går via K3, som kräver "
                    "entre_rh2000 och entre_punkt [E, N]."
                )
            tak = e.get("tak_rh2000")
            punkt = e["entre_punkt"]
            kw: dict[str, Any] = {}
            if "hojd_m" in e:
                kw["hinderhojd_m"] = _tal(e, "hojd_m", a)
            if "gransdjup_m" in e:
                kw["gransdjup_m"] = _tal(e, "gransdjup_m", a)
            byggnader.append(
                LappByggnad(
                    byggnad_id=a.andring_id,
                    polygon=_ring(a.geometri, a),
                    entre_rh2000=_tal(e, "entre_rh2000", a),
                    entre_punkt=(float(punkt[0]), float(punkt[1])),
                    tak_rh2000=None if tak is None else float(tak),
                    **kw,
                )
            )
    return ProjektLapp(
        lapp_id=f"k9-{scenario.scenario_id}"[:120],
        bas_recept=scenario.bas_recept,
        beskrivning=f"Översatt från K9-scenariot {scenario.scenario_id} ({scenario.nyckel[:12]}).",
        hojdytor=tuple(hojdytor),
        byggnader=tuple(byggnader),
        gator=tuple(gator),
        magasin=tuple(magasin),
        markslag_andringar=tuple(markslag),
    )


def las_k9_kedja(filer: Sequence[Path]) -> tuple[Planscenario, tuple[Andring, ...], str]:
    """Läser en kedja K9-filer (förfäder först, scenariot sist).

    Returnerar scenariot, dess utplattade ändringar och en nyckel för översättningen som
    bara beror på vad som faktiskt tillämpas (inte på hur kedjan är uppdelad).
    """
    if not filer:
        raise ScenarioFel("Tom K9-kedja.")
    scenarier = [las_scenario(f) for f in filer]
    kanda = {s.nyckel: s for s in scenarier}
    sista = scenarier[-1]
    andringar = platta_ut(sista, kanda)
    nyckel = nyckel_for(
        {
            "slag": "k9_till_k3",
            "version": K9_TILL_K3_VERSION,
            "bas_recept": list(sista.bas_recept),
            "andringar": sorted((a.till_dict() for a in andringar), key=lambda d: d["andring_id"]),
        }
    )
    return sista, andringar, nyckel
