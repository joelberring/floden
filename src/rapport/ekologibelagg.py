# ruff: noqa: E501
"""Belägg för ekologisk konnektivitet (habitatnätverk, ECA och PC) samt ekologiskt
skrivbordsunderlag (`lager.skyddad_natur` och `lager.artfynd_publika`), ur körningens
K0-manifest, `ekologi.json` och verifierade skrivbordsfiler (R2).

Lösaren `ekologi.konnektivitet` (K45) skriver ett K0-manifest per scenario (och ruta) med mätvärdena
`eca_m2`, `eca_min_m2`, `eca_max_m2`, `pc`, `pc_min`, `pc_max`, `antal_flackar`,
`total_habitat_area_m2` och `cellstorlek_m`, och en `ekologi.json` med nätverkets parametrar.
Rapporten räknar inget själv. Enskilda mått hämtas med den vanliga adressen
`matvarde.<analys>.<scenario>[.<ruta>].<mått>` (`belaggskalla.EKOLOGI_MATT`); den här modulen
lägger till tre adresstyper:

| Adress | Mått |
| --- | --- |
| `ekojamforelse.<analys>.<fore>.<efter>[.<ruta>].<mått>` | skillnad efter minus före: `delta_eca_m2`, `delta_eca_procent`, `delta_pc`, `delta_antal_flackar`, `delta_total_habitat_area_m2` |
| `ekonatverk.<analys>.<scenario>[.<ruta>].<parameter>` | nätverkets parametrar ur körningen: `troskel_m`, `d50_m`, `troskel_hog_m`, `d50_hog_m`, `min_area_m2`, `cellstorlek_m`, `analys_area_m2` |
| `ekoskrivbord.<skyddad_natur / artfynd_publika>.<mått>` | deterministiska SHA-256-belägg ur `lager.skyddad_natur` och `lager.artfynd_publika` för `EK-01`, `EK-02`, `EK-05` och `EK-06` |

Skillnaden räknas bara när före och efter är jämförbara: samma nätverk (kontrollsumma), samma
huvudscenario, samma cellstorlek och samma analysparametrar (annars `BelaggFel`). Skillnaden är
differensen av två belägg; kontrollsumman är en hash av båda körningsfilernas kontrollsummor.
Nätverksparametrarna är indata till måttet och gäller alla nämnare (statistik "parameter (indata)").
Resultatet är *potentiell livsmiljö, okänd kvalitet*: markunderlaget (NMD) säger inget om ålder,
struktur eller naturvärde.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from data_inlasning.artdatabanken import OSL_VARNING
from floden_mcp.belagg import enhet_for
from floden_mcp.karna_api import FlodenFel
from floden_mcp.korlogg import hash_av
from gemensamt.hash import sha256_fil
from geografi.ekologi_skrivbord import (
    ARTLISTA_FIL,
    FORMAT_ARTFYND_PUBLIKA,
    FORMAT_SKYDDAD_NATUR,
    SKRIVBORD_SAMMANFATTNING_FIL,
    bygg_nvi_faltchecklista,
)
from rapport.pastaende import Belagg
from rapport.sprak import scenarionamn

if TYPE_CHECKING:
    from rapport.belaggskalla import FlodenBelaggKalla
    from rapport.skissrapport import Rapport, RapportTjanst

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
_SKRIVBORD_STAT: Final[str] = "parameter (indata) i verifierat ekologiskt skrivbordsunderlag (skiss), gäller alla nämnare"
_TID: Final[str] = "ingen tidsdimension (statisk landskapsmodell: ett läge, inget förlopp)"
_SKRIVBORD_TID: Final[str] = "ögonblicksbild vid geografimappning (öppna registerdata, metodstatus skiss)"

# mått -> (json-stig som tupel, enhet, upplösning, storhet)
SKRIVBORD_SKYDD_MATT: Final[dict[str, tuple[tuple[str, ...], str | None, float, str]]] = {
    "antal_skyddade_omraden": (
        ("skyddade_omraden", "antal"),
        None,
        1.0,
        "antal registrerade skyddade områden (Naturvårdsverket NVR och Natura 2000) inom bufferten",
    ),
    "antal_skydd_overlapp": (
        ("skyddade_omraden", "antal_overlappar_karna"),
        None,
        1.0,
        "antal skyddade områden som överlappar planområdet",
    ),
    "overlapp_skydd_m2": (
        ("skyddade_omraden", "overlapp_karna_m2"),
        "m²",
        1.0,
        "överlappande yta med skyddade områden inom planområdet",
    ),
    "minsta_avstand_skydd_m": (
        ("skyddade_omraden", "minsta_avstand_m"),
        "m",
        1.0,
        "minsta avstånd från planområdet till närmaste skyddade område",
    ),
    "antal_natura2000": (
        ("skyddade_omraden", "antal_natura2000"),
        None,
        1.0,
        "antal Natura 2000-områden inom bufferten",
    ),
    "antal_skogliga_varden": (
        ("skogliga_varden", "antal"),
        None,
        1.0,
        "antal registrerade skogliga naturvärdesobjekt (Skogsstyrelsen) inom bufferten",
    ),
    "antal_skog_overlapp": (
        ("skogliga_varden", "antal_overlappar_karna"),
        None,
        1.0,
        "antal skogliga naturvärdesobjekt som överlappar planområdet",
    ),
    "overlapp_skog_m2": (
        ("skogliga_varden", "overlapp_karna_m2"),
        "m²",
        1.0,
        "överlappande yta med skogliga naturvärden inom planområdet",
    ),
    "minsta_avstand_skog_m": (
        ("skogliga_varden", "minsta_avstand_m"),
        "m",
        1.0,
        "minsta avstånd från planområdet till närmaste skogliga naturvärde",
    ),
    "antal_nyckelbiotoper": (
        ("skogliga_varden", "antal_nyckelbiotoper"),
        None,
        1.0,
        "antal registrerade nyckelbiotoper (Skogsstyrelsen) inom bufferten",
    ),
    "antal_sumpskogar": (
        ("skogliga_varden", "antal_sumpskogar"),
        None,
        1.0,
        "antal registrerade sumpskogar (Skogsstyrelsen) inom bufferten",
    ),
    "antal_naturvarden": (
        ("skogliga_varden", "antal_naturvarden"),
        None,
        1.0,
        "antal objekt med naturvärde (Skogsstyrelsen) inom bufferten",
    ),
    "antal_biotopskydd": (
        ("skogliga_varden", "antal_biotopskydd"),
        None,
        1.0,
        "antal skogliga biotopskyddsområden (Skogsstyrelsen) inom bufferten",
    ),
    "antal_naturvardsavtal": (
        ("skogliga_varden", "antal_naturvardsavtal"),
        None,
        1.0,
        "antal naturvårdsavtal (Skogsstyrelsen) inom bufferten",
    ),
    "vmi_antal_polygoner": (
        ("vmi", "antal_polygoner"),
        None,
        1.0,
        "antal VMI-våtmarkspolygoner (äldre inventering 1981–2005) i utsnittet",
    ),
    "vmi_overlapp_m2": (
        ("vmi", "overlapp_karna_m2"),
        "m²",
        1.0,
        "överlappande yta med känd VMI-våtmark (1981–2005) inom planområdet",
    ),
    "buffert_m": (
        ("buffert_m",),
        "m",
        1.0,
        "sökbuffert kring planområdet för skrivbordskontroll av skyddad natur",
    ),
}

SKRIVBORD_ART_MATT: Final[dict[str, tuple[tuple[str, ...], str | None, float, str]]] = {
    "antal_publika_fynd": (
        ("antal_publika_fynd",),
        None,
        1.0,
        "antal publika rödlistade eller fridlysta artfynd (SLU Artdatabanken SOS, exklusive skyddsklassade arter)",
    ),
    "antal_unika_arter": (
        ("antal_unika_arter",),
        None,
        1.0,
        "antal unika rödlistade eller fridlysta arter i publika fynd (exklusive skyddsklassade arter)",
    ),
    "antal_rodlistade_arter": (
        ("antal_rodlistade_arter",),
        None,
        1.0,
        "antal unika rödlistade arter (NT–RE) i publika fynd",
    ),
    "antal_fridlysta_arter": (
        ("antal_fridlysta_arter",),
        None,
        1.0,
        "antal unika fridlysta arter i publika fynd",
    ),
    "antal_skyddsklassade_filtrerade": (
        ("antal_skyddsklassade_filtrerade",),
        None,
        1.0,
        "antal skyddsklassade eller diffuserade poster som filtrerats bort utan koordinater enligt OSL 20 kap. 1 §",
    ),
    "buffert_m": (
        ("buffert_m",),
        "m",
        1.0,
        "sökbuffert kring planområdet för publika artfynd",
    ),
}


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


# ------------------------------------------------------------------ skrivbordsunderlag (SHA-256)


def _saker_fil_inom(fil: Path, rot_abs: Path) -> bool:
    if fil.is_symlink() or not fil.is_file():
        return False
    try:
        return fil.resolve().is_relative_to(rot_abs)
    except OSError:
        return False


def _las_json_obj(fil: Path) -> dict[str, Any] | None:
    try:
        ra = json.loads(fil.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return dict(ra) if isinstance(ra, Mapping) else None


def _hitta_skrivbord_fil(utmapp: Path, lagertyp: str) -> tuple[Path, str] | None:
    """Hittar verifierad JSON-fil för `skyddad_natur` eller `artfynd_publika` i `utmapp`.

    Returnerar `(fil, relativ_sokvag)` eller `None`. Avvisar symlänkar och sökvägsflykt.
    """
    if not utmapp.is_dir():
        return None
    utmapp_abs = utmapp.resolve()
    if lagertyp == "skyddad_natur":
        filnamn = SKRIVBORD_SAMMANFATTNING_FIL
        forvantat_format = FORMAT_SKYDDAD_NATUR
        kandidater = [
            utmapp / "ekologi_skrivbord" / filnamn,
            utmapp / "skyddad_natur" / filnamn,
            utmapp / filnamn,
        ]
    elif lagertyp == "artfynd_publika":
        filnamn = ARTLISTA_FIL
        forvantat_format = FORMAT_ARTFYND_PUBLIKA
        kandidater = [
            utmapp / "ekologi_skrivbord" / filnamn,
            utmapp / "artfynd_publika" / filnamn,
            utmapp / filnamn,
        ]
    else:
        return None

    for fil in kandidater:
        if not _saker_fil_inom(fil, utmapp_abs):
            continue
        dok = _las_json_obj(fil)
        if dok is not None and dok.get("format") == forvantat_format:
            return fil, str(fil.relative_to(utmapp))
    return None


def _lookup_path(dok: Mapping[str, Any], stig: tuple[str, ...]) -> Any:
    nu: Any = dok
    for nyckel in stig:
        if not isinstance(nu, Mapping) or nyckel not in nu:
            return None
        nu = nu[nyckel]
    return nu


def _eko_jamforbarhet_for_korning(
    kalla: FlodenBelaggKalla, korning_id: str, fallback_nyckel: str
) -> tuple[str, str]:
    """Hämtar (analys_id, jamforbarhet) från körningens ekologianalys så skrivbordsbelägg kan kombineras i samma påstående."""
    try:
        res = kalla._resultat(korning_id)  # noqa: SLF001
        for tab in res.get("tabeller", ()):
            if tab.get("losare") == EKOLOGI_LOSARE and tab.get("rader"):
                analys = str(tab["analys"])
                scen = str(tab["rader"][0]["scenario"])
                return analys, kalla._jamforbarhet(korning_id, analys, (scen,))  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass
    return "ekologi_skrivbord", hash_av({"ekologi_skrivbord": fallback_nyckel})


def _skrivbord_belagg(
    kalla: FlodenBelaggKalla, adress: str, korning_id: str, delar: list[str]
) -> Belagg:
    if len(delar) != 3:
        raise _fel(
            f"Adressen '{adress}' ska vara ekoskrivbord.<skyddad_natur|artfynd_publika>.<mått>."
        )
    _t, lagertyp, matt = delar
    if lagertyp == "skyddad_natur":
        katalog = SKRIVBORD_SKYDD_MATT
    elif lagertyp == "artfynd_publika":
        katalog = SKRIVBORD_ART_MATT
    else:
        raise _fel(
            f"Okänt skrivbordslager '{lagertyp}' i '{adress}'. Tillåtna: skyddad_natur, artfynd_publika."
        )
    if matt not in katalog:
        raise _fel(
            f"Okänt skrivbordsmått '{matt}' för '{lagertyp}'. Tillåtna: {', '.join(sorted(katalog))}."
        )

    utmapp = kalla.utmapp(korning_id)
    hittad = _hitta_skrivbord_fil(utmapp, lagertyp)
    if hittad is None:
        raise _fel(
            f"Skrivbordsunderlaget '{lagertyp}' saknas i körningen '{korning_id}'."
        )
    fil, fil_rel = hittad
    dok = _las_json_obj(fil)
    if dok is None:
        raise _fel(f"Skrivbordsfilen {fil_rel} kunde inte tolkas som JSON.")
    stig, enhet, upp, storhet = katalog[matt]
    ra_varde = _lookup_path(dok, stig)
    if ra_varde is None or isinstance(ra_varde, bool) or not isinstance(ra_varde, int | float):
        raise _fel(
            f"Måttet '{matt}' saknas eller har inget numeriskt värde i {fil_rel} (fick {ra_varde!r})."
        )
    varde = float(ra_varde)
    if not math.isfinite(varde):
        raise _fel(f"Måttet '{matt}' i {fil_rel} är inte ett ändligt tal.")

    run_fil = utmapp / "run.json"
    if not run_fil.is_file():
        raise _fel(f"K0-manifestet run.json saknas i körningen '{korning_id}'.")
    msha = sha256_fil(run_fil)
    fsha = sha256_fil(fil)
    analys_id, jfr = _eko_jamforbarhet_for_korning(kalla, korning_id, fsha)

    return Belagg(
        adress=adress,
        korning_id=korning_id,
        fil=fil_rel,
        fil_sha256=fsha,
        manifest="run.json",
        manifest_sha256=msha,
        analys=analys_id,
        losare=EKOLOGI_LOSARE,
        scenarier=("skrivbord",),
        mottagare="hela beräkningsområdet",
        namnare="plan- och buffertområde i verifierat skrivbordsunderlag (skiss)",
        matt=f"skrivbord:{lagertyp}:{matt}",
        storhet=storhet,
        enhet=enhet,
        tidsdefinition=_SKRIVBORD_TID,
        statistik=_SKRIVBORD_STAT,
        varde=varde,
        upplosning=upp,
        jamforbarhet=jfr,
    )


def skrivbord_adresser(utmapp: Path) -> list[str]:
    """Tillgängliga `ekoskrivbord.*`-beläggsadresser för en körnings utmapp (tom lista om lagren saknas)."""
    ut: list[str] = []
    for lagertyp, katalog in (
        ("skyddad_natur", SKRIVBORD_SKYDD_MATT),
        ("artfynd_publika", SKRIVBORD_ART_MATT),
    ):
        hittad = _hitta_skrivbord_fil(utmapp, lagertyp)
        if hittad is None:
            continue
        dok = _las_json_obj(hittad[0])
        if dok is None:
            continue
        for matt, (stig, *_rest) in katalog.items():
            v = _lookup_path(dok, stig)
            if isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(float(v)):
                ut.append(f"ekoskrivbord.{lagertyp}.{matt}")
    return ut


def skrivbord_antaganden(utmapp: Path) -> list[str]:
    """Deterministiska antaganden och OSL-begränsningar från skrivbordslager i `utmapp`."""
    ut: list[str] = []
    skydd_h = _hitta_skrivbord_fil(utmapp, "skyddad_natur")
    if skydd_h is not None:
        dok = _las_json_obj(skydd_h[0])
        if dok is not None:
            ut.append(
                f"Skrivbordskontroll av skyddad natur och skogliga värden: Naturvårdsverket (Skyddad natur, Natura 2000, VMI) "
                f"och Skogsstyrelsen (CC0), buffert {_sv(dok.get('buffert_m'))} m kring planområdet."
            )
            vmi = dok.get("vmi")
            if isinstance(vmi, Mapping) and vmi.get("tillganglig"):
                ut.append(
                    "VMI (Våtmarksinventeringen, 1981–2005) är en äldre inventering och visar känd våtmark som "
                    "kvalitetsinformation, inte aktuell utbredning."
                )
    art_h = _hitta_skrivbord_fil(utmapp, "artfynd_publika")
    if art_h is not None:
        dok = _las_json_obj(art_h[0])
        if dok is not None:
            period = dok.get("period_ar")
            per_txt = f" ({period[0]}–{period[1]})" if isinstance(period, list) and len(period) == 2 else ""
            ut.append(
                f"Publika artfynd ur SLU Artdatabanken SOS{per_txt}: enbart publika fynd av rödlistade och fridlysta arter; "
                "inga personuppgifter och inga skyddsklassade koordinater lagras."
            )
            ut.append(OSL_VARNING)
    elif skydd_h is not None:
        ut.append(OSL_VARNING)
    return list(dict.fromkeys(ut))


def skrivbord_pastaendeforslag(utmapp: Path) -> dict[str, dict[str, Any]]:
    """Deterministiska påståenden med `ekoskrivbord.*`-belägg som lyfter EK-01, EK-02, EK-05 och EK-06.

    Följer 100 % rapportens 9/9-regler: inga bare tal utanför platshållare, inga förbjudna ord om
    verklig förekomst/hög kvalitet, 0 PII, `metodstatus="skiss"` och obligatorisk OSL-begränsning.
    """
    skydd_h = _hitta_skrivbord_fil(utmapp, "skyddad_natur")
    art_h = _hitta_skrivbord_fil(utmapp, "artfynd_publika")
    if skydd_h is None and art_h is None:
        return {}

    skydd_dok = _las_json_obj(skydd_h[0]) if skydd_h is not None else None
    art_dok = _las_json_obj(art_h[0]) if art_h is not None else None
    ut: dict[str, dict[str, Any]] = {}

    if skydd_dok is not None:
        # EK-01: skyddade områden och värdekärnor i skrivbordskontrollen
        bel_01 = [
            "ekoskrivbord.skyddad_natur.antal_skyddade_omraden",
            "ekoskrivbord.skyddad_natur.antal_natura2000",
            "ekoskrivbord.skyddad_natur.buffert_m",
        ]
        txt_01 = (
            "Skrivbordskontrollen mot Naturvårdsverkets öppna register visar "
            "{{matt:ekoskrivbord.skyddad_natur.antal_skyddade_omraden|enhet=nej}} registrerade skyddade områden "
            "(varav {{matt:ekoskrivbord.skyddad_natur.antal_natura2000|enhet=nej}} inom EU:s skyddsnätverk) "
            "inom {{matt:ekoskrivbord.skyddad_natur.buffert_m|avrundning=1}} från planområdet."
        )
        ut["EK-01"] = {
            "fraga_id": "EK-01",
            "status": "underlag_saknas",
            "fordjupning": "ja",
            "text": txt_01,
            "belagg": bel_01,
            "mottagare": "hela_omradet",
            "saknat_underlag": [
                "Kompletterande kommunala naturvårdsprogram och fältavgränsning av värdekärnor (NVI)."
            ],
        }

        # EK-02: påverkansområde och buffert i skrivbordskontrollen
        bel_02 = [
            "ekoskrivbord.skyddad_natur.buffert_m",
            "ekoskrivbord.skyddad_natur.antal_skydd_overlapp",
            "ekoskrivbord.skyddad_natur.antal_skog_overlapp",
        ]
        txt_02 = (
            "Skrivbordskontrollen täcker en sökbuffert på {{matt:ekoskrivbord.skyddad_natur.buffert_m|avrundning=1}} "
            "kring planområdet: {{matt:ekoskrivbord.skyddad_natur.antal_skydd_overlapp|enhet=nej}} skyddade områden "
            "och {{matt:ekoskrivbord.skyddad_natur.antal_skog_overlapp|enhet=nej}} skogliga naturvärdesobjekt "
            "överlappar planområdet."
        )
        ut["EK-02"] = {
            "fraga_id": "EK-02",
            "status": "underlag_saknas",
            "fordjupning": "ja",
            "text": txt_02,
            "belagg": bel_02,
            "mottagare": "hela_omradet",
            "saknat_underlag": [
                "Motivering av påverkansområdets utbredning och bedömning av indirekt påverkan utanför bufferten."
            ],
        }

        # EK-06: nyckelbiotoper, sumpskogar, VMI och skogliga värden
        bel_06 = [
            "ekoskrivbord.skyddad_natur.antal_skogliga_varden",
            "ekoskrivbord.skyddad_natur.antal_nyckelbiotoper",
            "ekoskrivbord.skyddad_natur.antal_sumpskogar",
            "ekoskrivbord.skyddad_natur.vmi_overlapp_m2",
        ]
        txt_06 = (
            "Skogsstyrelsens öppna register redovisar {{matt:ekoskrivbord.skyddad_natur.antal_skogliga_varden|enhet=nej}} "
            "skogliga naturvärdesobjekt inom bufferten, varav {{matt:ekoskrivbord.skyddad_natur.antal_nyckelbiotoper|enhet=nej}} "
            "nyckelbiotoper och {{matt:ekoskrivbord.skyddad_natur.antal_sumpskogar|enhet=nej}} sumpskogar. "
            "Överlappande yta med känd våtmark ur VMI (äldre inventering) uppgår till "
            "{{matt:ekoskrivbord.skyddad_natur.vmi_overlapp_m2|avrundning=1}}. "
            "Uppgifterna avser potentiell livsmiljö, okänd kvalitet och ersätter inte naturvärdesinventering i fält."
        )
        ut["EK-06"] = {
            "fraga_id": "EK-06",
            "status": "underlag_saknas",
            "fordjupning": "ja",
            "text": txt_06,
            "belagg": bel_06,
            "mottagare": "hela_omradet",
            "saknat_underlag": [
                "Fältinventering av skogens ålder, struktur, skyddsvärda träd och naturvärdesklass (NVI)."
            ],
        }

    # EK-05: områdesskydd + artskyddskontroll med obligatorisk OSL-begränsning
    bel_05: list[str] = []
    delar_05: list[str] = []
    if skydd_dok is not None:
        bel_05 += [
            "ekoskrivbord.skyddad_natur.overlapp_skydd_m2",
            "ekoskrivbord.skyddad_natur.antal_biotopskydd",
        ]
        delar_05.append(
            "Skrivbordskontrollen visar {{matt:ekoskrivbord.skyddad_natur.overlapp_skydd_m2|avrundning=1}} överlapp med "
            "registrerade skyddade områden och {{matt:ekoskrivbord.skyddad_natur.antal_biotopskydd|enhet=nej}} "
            "skogliga biotopskyddsområden inom bufferten."
        )
    if art_dok is not None:
        bel_05 += [
            "ekoskrivbord.artfynd_publika.antal_unika_arter",
            "ekoskrivbord.artfynd_publika.antal_publika_fynd",
            "ekoskrivbord.artfynd_publika.antal_skyddsklassade_filtrerade",
        ]
        delar_05.append(
            "I SLU Artdatabanken finns {{matt:ekoskrivbord.artfynd_publika.antal_publika_fynd|enhet=nej}} publika "
            "fynd fördelade på {{matt:ekoskrivbord.artfynd_publika.antal_unika_arter|enhet=nej}} rödlistade eller "
            "fridlysta arter inom bufferten, medan "
            "{{matt:ekoskrivbord.artfynd_publika.antal_skyddsklassade_filtrerade|enhet=nej}} skyddsklassade eller "
            "diffuserade poster filtrerats bort utan koordinater."
        )
    delar_05.append(
        "Underlaget prövar inte förbud, föreskrifter eller tillståndsfrågor, och "
        "sekretessprövade skyddsklassade arter ingår inte utan ska alltid kontrolleras via behörig ekolog eller Länsstyrelsen."
    )
    ut["EK-05"] = {
        "fraga_id": "EK-05",
        "status": "underlag_saknas",
        "fordjupning": "ja",
        "text": " ".join(delar_05),
        "belagg": bel_05,
        "mottagare": "hela_omradet",
        "saknat_underlag": [
            "Sekretessprövat utdrag för skyddsklassade arter via behörig ekolog eller Länsstyrelsen samt juridisk prövning av områdesskydd och artskydd."
        ],
    }
    return ut


def _sammanfattning_fran_rapport(r: Rapport, tj: RapportTjanst) -> dict[str, Any] | None:
    """Läser verifierad skrivbordssammanfattning och artlista från rapportens körningar (None om inga finns)."""
    skydd_dok: dict[str, Any] | None = None
    art_dok: dict[str, Any] | None = None
    for k in r.korningar:
        try:
            utmapp = tj.kalla.utmapp(str(k["korning_id"]))
        except Exception:  # noqa: BLE001
            continue
        if skydd_dok is None:
            sh = _hitta_skrivbord_fil(utmapp, "skyddad_natur")
            if sh is not None:
                skydd_dok = _las_json_obj(sh[0])
        if art_dok is None:
            ah = _hitta_skrivbord_fil(utmapp, "artfynd_publika")
            if ah is not None:
                art_dok = _las_json_obj(ah[0])

    if skydd_dok is None and art_dok is None:
        return None
    return {
        "skyddad_natur_tillganglig": skydd_dok is not None,
        "artfynd_tillganglig": art_dok is not None,
        "skyddade_omraden": (skydd_dok or {}).get("skyddade_omraden") or {},
        "skogliga_varden": (skydd_dok or {}).get("skogliga_varden") or {},
        "vmi": (skydd_dok or {}).get("vmi") or {},
        "artfynd_publika": art_dok,
        "buffert_m": (skydd_dok or art_dok or {}).get("buffert_m"),
    }


def ekologi_skrivbord_tabellrader(r: Rapport, tj: RapportTjanst) -> list[tuple[str, ...]]:
    """Deterministiska tabellrader för skrivbordsunderlaget (tom lista om inga skrivbordslager finns)."""
    sam = _sammanfattning_fran_rapport(r, tj)
    if sam is None:
        return []

    rader: list[tuple[str, ...]] = []
    if sam.get("skyddad_natur_tillganglig"):
        sk = sam["skyddade_omraden"]
        avst_sk = sk.get("minsta_avstand_m")
        avst_sk_txt = f"{_sv(avst_sk)}\u00a0m" if avst_sk is not None else "–"
        rader.append(
            (
                "Skyddad natur och Natura 2000",
                "Naturvårdsverket (CC0)",
                f"{int(sk.get('antal') or 0)} objekt ({int(sk.get('antal_overlappar_karna') or 0)} överlapp)",
                f"{_sv(sk.get('overlapp_karna_m2') or 0)}\u00a0m² · avstånd {avst_sk_txt}",
            )
        )
        sg = sam["skogliga_varden"]
        avst_sg = sg.get("minsta_avstand_m")
        avst_sg_txt = f"{_sv(avst_sg)}\u00a0m" if avst_sg is not None else "–"
        rader.append(
            (
                "Nyckelbiotoper, sumpskogar och skogsvärden",
                "Skogsstyrelsen (CC0)",
                f"{int(sg.get('antal') or 0)} objekt ({int(sg.get('antal_nyckelbiotoper') or 0)} nyckelbiotoper, {int(sg.get('antal_sumpskogar') or 0)} sumpskogar)",
                f"{_sv(sg.get('overlapp_karna_m2') or 0)}\u00a0m² · avstånd {avst_sg_txt}",
            )
        )
        vmi = sam.get("vmi") or {}
        if vmi.get("tillganglig"):
            rader.append(
                (
                    "Våtmarksinventeringen (VMI, 1981–2005)",
                    "Naturvårdsverket (CC0)",
                    f"{int(vmi.get('antal_polygoner') or 0)} polygoner (äldre inventering)",
                    f"överlapp {_sv(vmi.get('overlapp_karna_m2') or 0)}\u00a0m²",
                )
            )
        else:
            rader.append(("Våtmarksinventeringen (VMI)", "Ej mappad", "–", "Kontrollera våtmarker i fält"))
    else:
        rader.append(("Skyddad natur och Skogsstyrelsen", "Ej mappad", "–", "Kontrollera öppna register"))

    art = sam.get("artfynd_publika")
    if isinstance(art, Mapping):
        n_fynd = int(art.get("antal_publika_fynd") or 0)
        n_art = int(art.get("antal_unika_arter") or 0)
        n_skydd = int(art.get("antal_skyddsklassade_filtrerade") or 0)
        rader.append(
            (
                "Publika artfynd (rödlistade och fridlysta)",
                "SLU Artdatabanken SOS (publika fynd)",
                f"{n_art} arter ({n_fynd} fynd)",
                f"{n_skydd} skyddsklassade filtrerade utan koordinater",
            )
        )
    else:
        rader.append(
            (
                "Publika artfynd (SLU Artdatabanken)",
                "Ej hämtad (nyckel saknas eller ej mappad)",
                "–",
                "Kontrollera via behörig ekolog eller Länsstyrelsen",
            )
        )
    return rader


def ekologi_nvi_checklista_rader(r: Rapport, tj: RapportTjanst) -> list[tuple[str, str, str, str]]:
    """Deterministiska tabellrader för 'Checklista inför fältinventering (NVI)' i ekologiska rapporter."""
    if r.amne != "ekologi":
        return []
    sam = _sammanfattning_fran_rapport(r, tj) or {
        "skyddad_natur_tillganglig": False,
        "artfynd_tillganglig": False,
        "skyddade_omraden": {},
        "skogliga_varden": {},
        "vmi": {"tillganglig": False},
        "artfynd_publika": None,
    }
    punkter = bygg_nvi_faltchecklista(sam)
    return [
        (p["moment"], p["prioritet"], p["underlag"], p["atgard"])
        for p in punkter
    ]


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
    """Antaganden och förutsättningar som Floden själv skriver, ur nätverkets `ekologi.json` och skrivbordsunderlag."""
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
    with contextlib.suppress(Exception):
        ut.extend(skrivbord_antaganden(kalla.utmapp(korning_id)))
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
    """Belägg för `ekojamforelse.…`, `ekonatverk.…` och `ekoskrivbord.…` (se modulens beskrivning)."""
    if delar[0] == "ekonatverk":
        return _natverk_belagg(kalla, adress, korning_id, delar)
    if delar[0] == "ekoskrivbord":
        return _skrivbord_belagg(kalla, adress, korning_id, delar)
    return _jamfor_belagg(kalla, adress, korning_id, delar)


__all__ = [
    "DELTAMATT",
    "EKOLOGI_LOSARE",
    "NATVERKSPARAMETRAR",
    "OSL_VARNING",
    "SKRIVBORD_ART_MATT",
    "SKRIVBORD_SKYDD_MATT",
    "ekologi_belagg",
    "ekologi_nvi_checklista_rader",
    "ekologi_skrivbord_tabellrader",
    "ekologiantaganden",
    "skrivbord_adresser",
    "skrivbord_antaganden",
    "skrivbord_pastaendeforslag",
]
