# ruff: noqa: E501
"""Belägg för Grönytefaktor (`gyf.*`) och termiskt mikroklimat (`utci.*`) i skissunderlag (R2, additivt).

Läser uteslutande deterministiska JSON-resultat från `src/ekologi/gyf.py` (`GyfResultat` -> `gyf.json`
eller `run.json`) och `src/mikroklimat/fysik.py` (`VarmestressResultat` -> `utci.json` eller `run.json`).
Rapporten räknar eller gissar aldrig egna tal; varje belägg bär källfilens relativa sökväg, SHA-256,
K0-manifestets SHA-256, enhet, nämnare och `metodstatus = "skiss"`.

Adresser (`FlodenBelaggKalla.hamta`):

| Adress | Källa |
| --- | --- |
| `gyf.<scenario>.<mått>` | `GyfResultat` JSON (`gyf`, `riktmarke`, `eko_effektiv_area_m2`, `grund_eko_effektiv_area_m2`, `tillagg_eko_effektiv_area_m2`, `flaggad_area_m2`, `flaggad_andel_pct`, `total_area_m2`, `tomt_area_m2`, `saknas_till_mal_m2`) |
| `gyf.<scenario>.delfaktor.<faktor_id>.<fält>` | enskild delfaktor ur `GyfResultat.delfaktorer` (`area_m2`, `eko_effektiv_area_m2`, `faktor`, `andel_av_tomt_pct`, `bidrag_gyf`, `antal_celler`) |
| `gyf.<scenario>.tillagg.<tillagg_id>.<fält>` | enskild tilläggsfaktor ur `GyfResultat.tillagg` (`area_m2`, `eko_effektiv_area_m2`, `faktor`, `andel_av_tomt_pct`, `bidrag_gyf`) |
| `utci.<scenario>.<mått>` | `VarmestressResultat` JSON (`utci_medel_c`, `utci_min_c`, `utci_p90_c`, `utci_max_c`, `t_mrt_medel_c`, `t_mrt_min_c`, `t_mrt_p90_c`, `t_mrt_max_c`, `andel_ingen_stress`, `andel_mattlig_varmestress`, `andel_stark_varmestress`, `andel_mycket_stark_varmestress`, `andel_extrem_varmestress`, `andel_utanfor_giltighetsomrade`, `andel_stark_varmestress_pct`, `andel_utanfor_giltighetsomrade_pct`) |
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from floden_mcp.korlogg import hash_av
from gemensamt.hash import sha256_fil
from rapport.pastaende import Belagg
from rapport.sprak import scenarionamn

if TYPE_CHECKING:
    from rapport.belaggskalla import FlodenBelaggKalla
    from rapport.skissrapport import Rapport, RapportTjanst

GYF_LOSARE: Final[str] = "ekologi.gyf"
UTCI_LOSARE: Final[str] = "mikroklimat.utci"
GYF_FORMAT: Final[str] = "floden-gyf-resultat/v1"
UTCI_FORMAT: Final[str] = "floden-mikroklimat/v1-skiss"
METODSTATUS_SKISS: Final[str] = "skiss"
_SCENARIO_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

_GYF_TID: Final[str] = "ingen tidsdimension (statisk kvartersmarksmodell: ett läge, inget förlopp)"
_UTCI_TID: Final[str] = "givet väder- och soltillfälle (statisk 6-riktnings T_mrt och UTCI, metodstatus skiss)"
_UTCI_NAMNARE: Final[str] = "giltiga markceller i mikroklimatrastret (UTCI/T_mrt, metodstatus skiss)"

GYF_SKALAR_MATT: Final[tuple[str, ...]] = (
    "gyf",
    "riktmarke",
    "eko_effektiv_area_m2",
    "grund_eko_effektiv_area_m2",
    "tillagg_eko_effektiv_area_m2",
    "flaggad_area_m2",
    "flaggad_andel_pct",
    "total_area_m2",
    "tomt_area_m2",
    "saknas_till_mal_m2",
)
GYF_DELFAKTOR_FALT: Final[tuple[str, ...]] = (
    "area_m2",
    "eko_effektiv_area_m2",
    "faktor",
    "andel_av_tomt_pct",
    "bidrag_gyf",
    "antal_celler",
)
GYF_TILLAGG_FALT: Final[tuple[str, ...]] = (
    "area_m2",
    "eko_effektiv_area_m2",
    "faktor",
    "andel_av_tomt_pct",
    "bidrag_gyf",
)

# mått -> (json-nyckel i statistik, skala, enhet, upplösning, storhet, statistiktext)
UTCI_MATT: Final[dict[str, tuple[str, float, str, float, str, str]]] = {
    "utci_medel_c": (
        "utci_medel_c",
        1.0,
        "°C",
        0.1,
        "ekvivalent temperatur UTCI",
        "medelvärde över giltiga celler i mikroklimatrastret",
    ),
    "utci_min_c": (
        "utci_min_c",
        1.0,
        "°C",
        0.1,
        "ekvivalent temperatur UTCI",
        "lägsta värde bland giltiga celler i mikroklimatrastret",
    ),
    "utci_p90_c": (
        "utci_p90_c",
        1.0,
        "°C",
        0.1,
        "ekvivalent temperatur UTCI (90:e percentil)",
        "90:e percentilen bland giltiga celler i mikroklimatrastret",
    ),
    "utci_max_c": (
        "utci_max_c",
        1.0,
        "°C",
        0.1,
        "ekvivalent temperatur UTCI",
        "största värde bland giltiga celler i mikroklimatrastret",
    ),
    "t_mrt_medel_c": (
        "t_mrt_medel_c",
        1.0,
        "°C",
        0.1,
        "medelstrålningstemperatur T_mrt",
        "medelvärde över giltiga celler i mikroklimatrastret",
    ),
    "t_mrt_min_c": (
        "t_mrt_min_c",
        1.0,
        "°C",
        0.1,
        "medelstrålningstemperatur T_mrt",
        "lägsta värde bland giltiga celler i mikroklimatrastret",
    ),
    "t_mrt_p90_c": (
        "t_mrt_p90_c",
        1.0,
        "°C",
        0.1,
        "medelstrålningstemperatur T_mrt (90:e percentil)",
        "90:e percentilen bland giltiga celler i mikroklimatrastret",
    ),
    "t_mrt_max_c": (
        "t_mrt_max_c",
        1.0,
        "°C",
        0.1,
        "medelstrålningstemperatur T_mrt",
        "största värde bland giltiga celler i mikroklimatrastret",
    ),
    "andel_ingen_stress": (
        "andel_ingen_stress",
        1.0,
        "andel",
        0.01,
        "andel giltiga celler med ingen termisk stress (UTCI 9–26 °C)",
        "andel av giltiga celler i mikroklimatrastret",
    ),
    "andel_mattlig_varmestress": (
        "andel_mattlig_varmestress",
        1.0,
        "andel",
        0.01,
        "andel giltiga celler med måttlig värmestress (UTCI 26–32 °C)",
        "andel av giltiga celler i mikroklimatrastret",
    ),
    "andel_stark_varmestress": (
        "andel_stark_varmestress",
        1.0,
        "andel",
        0.01,
        "andel giltiga celler med minst stark värmestress (UTCI > 32 °C)",
        "andel av giltiga celler i mikroklimatrastret",
    ),
    "andel_mycket_stark_varmestress": (
        "andel_mycket_stark_varmestress",
        1.0,
        "andel",
        0.01,
        "andel giltiga celler med minst mycket stark värmestress (UTCI > 38 °C)",
        "andel av giltiga celler i mikroklimatrastret",
    ),
    "andel_extrem_varmestress": (
        "andel_extrem_varmestress",
        1.0,
        "andel",
        0.01,
        "andel giltiga celler med extrem värmestress (UTCI > 46 °C)",
        "andel av giltiga celler i mikroklimatrastret",
    ),
    "andel_utanfor_giltighetsomrade": (
        "andel_utanfor_giltighetsomrade",
        1.0,
        "andel",
        0.01,
        "andel celler utanför Bröde m.fl. (2012) giltighetsområde",
        "andel av totala antalet celler i mikroklimatrastret",
    ),
    "andel_stark_varmestress_pct": (
        "andel_stark_varmestress",
        100.0,
        "%",
        0.1,
        "andel giltiga celler med minst stark värmestress (UTCI > 32 °C)",
        "procentandel av giltiga celler i mikroklimatrastret",
    ),
    "andel_utanfor_giltighetsomrade_pct": (
        "andel_utanfor_giltighetsomrade",
        100.0,
        "%",
        0.1,
        "andel celler utanför Bröde m.fl. (2012) giltighetsområde",
        "procentandel av totala antalet celler i mikroklimatrastret",
    ),
}


def _fel(text: str) -> Exception:
    from rapport.belaggskalla import BelaggFel

    return BelaggFel(text)


def _ar_gyf_dok(dok: Any) -> bool:
    return (
        isinstance(dok, Mapping)
        and (dok.get("format") == GYF_FORMAT or ("gyf" in dok and "tomt_area_m2" in dok))
    )


def _ar_utci_dok(dok: Any) -> bool:
    return (
        isinstance(dok, Mapping)
        and isinstance(dok.get("statistik"), Mapping)
        and (
            dok.get("format") == UTCI_FORMAT
            or "utci_medel_c" in dok["statistik"]
        )
    )


def _las_json(fil: Path) -> dict[str, Any] | None:
    try:
        ra = json.loads(fil.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return dict(ra) if isinstance(ra, Mapping) else None


def _andligt_tal(varde: Any, namn: str) -> float:
    if isinstance(varde, bool) or not isinstance(varde, int | float):
        raise _fel(f"Fältet '{namn}' saknas eller är inget tal ({varde!r}).")
    tal = float(varde)
    if not math.isfinite(tal):
        raise _fel(f"Fältet '{namn}' är inte ett ändligt tal ({varde!r}).")
    return tal


def _krav_skiss(dok: Mapping[str, Any], typ: str, fil_rel: str) -> None:
    status = str(dok.get("metodstatus") or METODSTATUS_SKISS)
    if status != METODSTATUS_SKISS:
        raise _fel(
            f"{typ}-resultatfilen {fil_rel} har metodstatus '{status}', men endast "
            f"'{METODSTATUS_SKISS}' stöds i skissunderlag (nivå 1)."
        )


# ---------------------------------------------------------------------- filsökning


def _saker_fil_inom(fil: Path, utmapp_abs: Path) -> bool:
    """Avvisar symlänkar och alla sökvägar som efter upplösning pekar utanför `utmapp_abs`."""
    if fil.is_symlink() or not fil.is_file():
        return False
    try:
        return fil.resolve().is_relative_to(utmapp_abs)
    except OSError:
        return False


def hitta_gyf_fil(utmapp: Path, scenario: str) -> Path | None:
    """Hittar deterministisk GYF-resultatfil (`gyf.json` eller GYF-`run.json`) för ett scenario."""
    if not utmapp.is_dir() or not _SCENARIO_RE.fullmatch(scenario):
        return None
    utmapp_abs = utmapp.resolve()
    kandidater: list[Path] = [
        utmapp / "gyf" / scenario / "gyf.json",
        utmapp / "gyf" / scenario / "run.json",
        utmapp / scenario / "gyf.json",
        *sorted(utmapp.glob(f"*/{scenario}/gyf.json")),
        *sorted(utmapp.glob(f"*/{scenario}/*/gyf.json")),
        utmapp / f"gyf_{scenario}.json",
    ]
    sedda: set[Path] = set()
    for fil in kandidater:
        if fil in sedda or not _saker_fil_inom(fil, utmapp_abs):
            continue
        sedda.add(fil)
        dok = _las_json(fil)
        if _ar_gyf_dok(dok):
            return fil
    return None


def hitta_utci_fil(utmapp: Path, scenario: str) -> Path | None:
    """Hittar deterministisk UTCI-resultatfil (`utci.json` eller UTCI-`run.json`) för ett scenario."""
    if not utmapp.is_dir() or not _SCENARIO_RE.fullmatch(scenario):
        return None
    utmapp_abs = utmapp.resolve()
    kandidater: list[Path] = [
        utmapp / "utci" / scenario / "utci.json",
        utmapp / "utci" / scenario / "run.json",
        utmapp / "mikroklimat" / scenario / "utci.json",
        utmapp / "mikroklimat" / scenario / "run.json",
        utmapp / scenario / "utci.json",
        *sorted(utmapp.glob(f"*/{scenario}/utci.json")),
        *sorted(utmapp.glob(f"*/{scenario}/*/utci.json")),
        utmapp / f"utci_{scenario}.json",
    ]
    sedda: set[Path] = set()
    for fil in kandidater:
        if fil in sedda or not _saker_fil_inom(fil, utmapp_abs):
            continue
        sedda.add(fil)
        dok = _las_json(fil)
        if _ar_utci_dok(dok):
            return fil
    return None


def hitta_gyf_scenarier(utmapp: Path) -> dict[str, Path]:
    """Returnerar sorterat uppslag `scenario -> fil` för alla giltiga GYF-resultat i körningen."""
    if not utmapp.is_dir():
        return {}
    utmapp_abs = utmapp.resolve()
    hittade: dict[str, Path] = {}
    for monster in ("gyf/*/gyf.json", "gyf/*/run.json", "*/gyf.json", "*/*/gyf.json", "*/*/*/gyf.json"):
        for fil in sorted(utmapp.glob(monster)):
            if not _saker_fil_inom(fil, utmapp_abs):
                continue
            dok = _las_json(fil)
            if not _ar_gyf_dok(dok):
                continue
            rel = fil.relative_to(utmapp).parts
            scen = rel[1] if len(rel) >= 3 and rel[0] == "gyf" else (rel[-2] if len(rel) >= 2 else "bas")
            if len(rel) == 4 and rel[-1] == "gyf.json":
                scen = rel[1]
            if _SCENARIO_RE.fullmatch(scen):
                hittade.setdefault(scen, fil)
    for fil in sorted(utmapp.glob("gyf_*.json")):
        if not _saker_fil_inom(fil, utmapp_abs):
            continue
        dok = _las_json(fil)
        if _ar_gyf_dok(dok):
            scen = fil.stem.removeprefix("gyf_")
            if _SCENARIO_RE.fullmatch(scen):
                hittade.setdefault(scen, fil)
    return dict(sorted(hittade.items()))


def hitta_utci_scenarier(utmapp: Path) -> dict[str, Path]:
    """Returnerar sorterat uppslag `scenario -> fil` för alla giltiga UTCI-resultat i körningen."""
    if not utmapp.is_dir():
        return {}
    utmapp_abs = utmapp.resolve()
    hittade: dict[str, Path] = {}
    for monster in (
        "utci/*/utci.json",
        "utci/*/run.json",
        "mikroklimat/*/utci.json",
        "mikroklimat/*/run.json",
        "*/utci.json",
        "*/*/utci.json",
        "*/*/*/utci.json",
    ):
        for fil in sorted(utmapp.glob(monster)):
            if not _saker_fil_inom(fil, utmapp_abs):
                continue
            dok = _las_json(fil)
            if not _ar_utci_dok(dok):
                continue
            rel = fil.relative_to(utmapp).parts
            if (len(rel) >= 3 and rel[0] in ("utci", "mikroklimat")) or (
                len(rel) == 4 and rel[-1] == "utci.json"
            ):
                scen = rel[1]
            else:
                scen = rel[-2] if len(rel) >= 2 else "bas"
            if _SCENARIO_RE.fullmatch(scen):
                hittade.setdefault(scen, fil)
    for fil in sorted(utmapp.glob("utci_*.json")):
        if not _saker_fil_inom(fil, utmapp_abs):
            continue
        dok = _las_json(fil)
        if _ar_utci_dok(dok):
            scen = fil.stem.removeprefix("utci_")
            if _SCENARIO_RE.fullmatch(scen):
                hittade.setdefault(scen, fil)
    return dict(sorted(hittade.items()))


# ---------------------------------------------------------------------- GYF-belägg


def _gyf_namnare(profil_id: str) -> str:
    return f"tomtarea inom GYF-avgränsningen (profil {profil_id}, metodstatus {METODSTATUS_SKISS})"


def _gyf_jamforbarhet(dok: Mapping[str, Any]) -> str:
    return hash_av(
        {
            "losare": GYF_LOSARE,
            "profil_id": dok.get("profil_id"),
            "riktmarke": dok.get("riktmarke"),
            "cellstorlek_m": dok.get("cellstorlek_m"),
            "profilfil_sha256": dok.get("profilfil_sha256", ""),
            "metodstatus": METODSTATUS_SKISS,
        }
    )


def _gyf_cell_area(dok: Mapping[str, Any]) -> float:
    cell = dok.get("cellstorlek_m")
    if isinstance(cell, int | float) and not isinstance(cell, bool) and cell > 0:
        return float(cell) * float(cell)
    return 1.0


def _gyf_skalar_info(dok: Mapping[str, Any], matt: str) -> tuple[float, str, float, str, str]:
    cell_area = _gyf_cell_area(dok)
    if matt == "gyf":
        return (
            _andligt_tal(dok.get("gyf"), "gyf"),
            "andel",
            0.01,
            "grönytefaktor (GYF)",
            "ekoeffektiv yta delad med tomtarea (skiss)",
        )
    if matt == "riktmarke":
        return (
            _andligt_tal(dok.get("riktmarke"), "riktmarke"),
            "andel",
            0.01,
            "kommunprofilens riktmärke för GYF",
            "parameter (indata) i kommunprofilen, gäller alla nämnare",
        )
    if matt == "eko_effektiv_area_m2":
        return (
            _andligt_tal(dok.get("eko_effektiv_area_m2"), "eko_effektiv_area_m2"),
            "m²",
            cell_area,
            "ekoeffektiv yta (grundyta plus tillägg)",
            "viktad summa över tomtarean",
        )
    if matt == "grund_eko_effektiv_area_m2":
        return (
            _andligt_tal(dok.get("grund_eko_effektiv_area_m2"), "grund_eko_effektiv_area_m2"),
            "m²",
            cell_area,
            "ekoeffektiv grundyta i GYF",
            "viktad summa av grundytans delfaktorer",
        )
    if matt == "tillagg_eko_effektiv_area_m2":
        return (
            _andligt_tal(dok.get("tillagg_eko_effektiv_area_m2"), "tillagg_eko_effektiv_area_m2"),
            "m²",
            cell_area,
            "ekoeffektiv tilläggsyta i GYF",
            "viktad summa av tilläggsfaktorer",
        )
    if matt == "flaggad_area_m2":
        return (
            _andligt_tal(dok.get("flaggad_area_m2"), "flaggad_area_m2"),
            "m²",
            cell_area,
            "flaggad yta med okända eller ogiltiga markslagskoder (bidrag 0)",
            "summa av flaggade celler inom tomtarean",
        )
    if matt == "flaggad_andel_pct":
        return (
            _andligt_tal(dok.get("flaggad_andel_pct"), "flaggad_andel_pct"),
            "%",
            0.1,
            "andel av tomtarean med okända eller ogiltiga markslagskoder",
            "procentandel av tomtarean",
        )
    if matt in ("total_area_m2", "tomt_area_m2"):
        return (
            _andligt_tal(dok.get("tomt_area_m2"), "tomt_area_m2"),
            "m²",
            cell_area,
            "total tomtarea i GYF-beräkningen",
            "summa av aktiva celler inom tomtgränsen",
        )
    if matt == "saknas_till_mal_m2":
        return (
            _andligt_tal(dok.get("saknas_till_mal_m2"), "saknas_till_mal_m2"),
            "m²",
            cell_area,
            "ekoeffektiv yta som saknas till kommunprofilens riktmärke",
            "differens mot kommunprofilens riktmärke",
        )
    raise _fel(
        f"Okänt GYF-mått '{matt}'. Tillåtna: {', '.join(GYF_SKALAR_MATT)} "
        "eller delfaktor.<faktor_id>.<fält> / tillagg.<tillagg_id>.<fält>."
    )


def _gyf_delfaktor_info(
    dok: Mapping[str, Any], faktor_id: str, falt: str
) -> tuple[float, str, float, str, str]:
    if falt not in GYF_DELFAKTOR_FALT:
        raise _fel(
            f"Okänt delfaktorfält '{falt}'. Tillåtna: {', '.join(GYF_DELFAKTOR_FALT)}."
        )
    lista = dok.get("delfaktorer")
    if not isinstance(lista, list):
        raise _fel("GYF-resultatfilen saknar listan 'delfaktorer'.")
    post = next((d for d in lista if isinstance(d, Mapping) and d.get("faktor_id") == faktor_id), None)
    if post is None:
        kanda = ", ".join(str(d.get("faktor_id")) for d in lista if isinstance(d, Mapping))
        raise _fel(f"GYF-delfaktorn '{faktor_id}' finns inte. Tillgängliga: {kanda}.")
    namn = str(post.get("namn") or faktor_id)
    cell_area = _gyf_cell_area(dok)
    varde = _andligt_tal(post.get(falt), f"delfaktor.{faktor_id}.{falt}")
    if falt == "area_m2":
        return varde, "m²", cell_area, f"area för GYF-delfaktor '{namn}'", "summa över delfaktorns celler"
    if falt == "eko_effektiv_area_m2":
        return varde, "m²", cell_area, f"ekoeffektiv yta för GYF-delfaktor '{namn}'", "viktad yta för delfaktorn"
    if falt == "faktor":
        return (
            varde,
            "andel",
            0.01,
            f"vikt för GYF-delfaktor '{namn}'",
            "parameter (indata) i kommunprofilen, gäller alla nämnare",
        )
    if falt == "andel_av_tomt_pct":
        return varde, "%", 0.1, f"ytandel av tomtarean för GYF-delfaktor '{namn}'", "procentandel av tomtarean"
    if falt == "bidrag_gyf":
        return varde, "andel", 0.001, f"GYF-bidrag från delfaktor '{namn}'", "ekoeffektiv yta delad med tomtarea"
    return varde, "st", 1.0, f"antal celler för GYF-delfaktor '{namn}'", "antal celler"


def _gyf_tillagg_info(
    dok: Mapping[str, Any], tillagg_id: str, falt: str
) -> tuple[float, str, float, str, str]:
    if falt not in GYF_TILLAGG_FALT:
        raise _fel(
            f"Okänt tilläggsfält '{falt}'. Tillåtna: {', '.join(GYF_TILLAGG_FALT)}."
        )
    lista = dok.get("tillagg")
    if not isinstance(lista, list):
        raise _fel("GYF-resultatfilen saknar listan 'tillagg'.")
    post = next((t for t in lista if isinstance(t, Mapping) and t.get("tillagg_id") == tillagg_id), None)
    if post is None:
        kanda = ", ".join(str(t.get("tillagg_id")) for t in lista if isinstance(t, Mapping))
        raise _fel(f"GYF-tilläggsfaktorn '{tillagg_id}' finns inte. Tillgängliga: {kanda}.")
    namn = str(post.get("namn") or tillagg_id)
    cell_area = _gyf_cell_area(dok)
    varde = _andligt_tal(post.get(falt), f"tillagg.{tillagg_id}.{falt}")
    if falt == "area_m2":
        return varde, "m²", cell_area, f"area för GYF-tillägg '{namn}'", "angiven yta för tilläggsfaktorn"
    if falt == "eko_effektiv_area_m2":
        return varde, "m²", cell_area, f"ekoeffektiv yta för GYF-tillägg '{namn}'", "viktad yta för tilläggsfaktorn"
    if falt == "faktor":
        return (
            varde,
            "andel",
            0.01,
            f"vikt för GYF-tillägg '{namn}'",
            "parameter (indata) i kommunprofilen, gäller alla nämnare",
        )
    if falt == "andel_av_tomt_pct":
        return varde, "%", 0.1, f"ytandel av tomtarean för GYF-tillägg '{namn}'", "procentandel av tomtarean"
    return varde, "andel", 0.001, f"GYF-bidrag från tillägg '{namn}'", "ekoeffektiv yta delad med tomtarea"


def _gyf_belagg(
    kalla: FlodenBelaggKalla, adress: str, korning_id: str, delar: list[str]
) -> Belagg:
    if len(delar) not in (3, 5):
        raise _fel(
            f"Adressen '{adress}' ska vara gyf.<scenario>.<mått>, "
            "gyf.<scenario>.delfaktor.<faktor_id>.<fält> eller "
            "gyf.<scenario>.tillagg.<tillagg_id>.<fält>."
        )
    scenario = delar[1]
    utmapp = kalla.utmapp(korning_id)
    fil = hitta_gyf_fil(utmapp, scenario)
    if fil is None:
        raise _fel(
            f"Ingen GYF-resultatfil (gyf.json) finns för scenariot '{scenario}' i körningen '{korning_id}'."
        )
    rel = fil.relative_to(utmapp).as_posix()
    dok = _las_json(fil)
    if dok is None or not _ar_gyf_dok(dok):
        raise _fel(f"GYF-resultatfilen {rel} i '{korning_id}' kunde inte tolkas som GyfResultat.")
    _krav_skiss(dok, "GYF", rel)
    _m, msha = kalla._manifest(korning_id, "run.json")  # noqa: SLF001

    if len(delar) == 3:
        matt_id = delar[2]
        varde, enhet, upp, storhet, stat = _gyf_skalar_info(dok, matt_id)
        matt_nyckel = f"gyf:{matt_id}"
    elif delar[2] == "delfaktor":
        varde, enhet, upp, storhet, stat = _gyf_delfaktor_info(dok, delar[3], delar[4])
        matt_nyckel = f"gyf:delfaktor:{delar[3]}:{delar[4]}"
    elif delar[2] == "tillagg":
        varde, enhet, upp, storhet, stat = _gyf_tillagg_info(dok, delar[3], delar[4])
        matt_nyckel = f"gyf:tillagg:{delar[3]}:{delar[4]}"
    else:
        raise _fel(
            f"Okänd GYF-underadress '{delar[2]}' i '{adress}'. Använd 'delfaktor' eller 'tillagg'."
        )

    profil_id = str(dok.get("profil_id") or "okand")
    return Belagg(
        adress=adress,
        korning_id=korning_id,
        fil=rel,
        fil_sha256=sha256_fil(fil),
        manifest="run.json",
        manifest_sha256=msha,
        analys="gyf",
        losare=GYF_LOSARE,
        scenarier=(scenario,),
        mottagare=f"scenario {scenario}",
        namnare=_gyf_namnare(profil_id),
        matt=matt_nyckel,
        storhet=storhet,
        enhet=enhet,
        tidsdefinition=_GYF_TID,
        statistik=stat,
        varde=varde,
        upplosning=upp,
        jamforbarhet=_gyf_jamforbarhet(dok),
    )


# ---------------------------------------------------------------------- UTCI-belägg


def _utci_jamforbarhet(dok: Mapping[str, Any]) -> str:
    return hash_av(
        {
            "losare": UTCI_LOSARE,
            "format": dok.get("format"),
            "form": dok.get("form"),
            "vinkelfaktorer": dok.get("vinkelfaktorer"),
            "metodstatus": METODSTATUS_SKISS,
        }
    )


def _utci_belagg(
    kalla: FlodenBelaggKalla, adress: str, korning_id: str, delar: list[str]
) -> Belagg:
    if len(delar) != 3:
        raise _fel(f"Adressen '{adress}' ska vara utci.<scenario>.<mått>.")
    _t, scenario, matt = delar
    spec = UTCI_MATT.get(matt)
    if spec is None:
        raise _fel(
            f"Okänt UTCI-mått '{matt}' i '{adress}'. Tillåtna: {', '.join(UTCI_MATT)}."
        )
    utmapp = kalla.utmapp(korning_id)
    fil = hitta_utci_fil(utmapp, scenario)
    if fil is None:
        raise _fel(
            f"Ingen UTCI-resultatfil (utci.json) finns för scenariot '{scenario}' i körningen '{korning_id}'."
        )
    rel = fil.relative_to(utmapp).as_posix()
    dok = _las_json(fil)
    if dok is None or not _ar_utci_dok(dok):
        raise _fel(
            f"UTCI-resultatfilen {rel} i '{korning_id}' kunde inte tolkas som VarmestressResultat."
        )
    _krav_skiss(dok, "UTCI", rel)
    _m, msha = kalla._manifest(korning_id, "run.json")  # noqa: SLF001

    json_nyckel, skala, enhet, upp, storhet, stat = spec
    statistik = dok["statistik"]
    varde = _andligt_tal(statistik.get(json_nyckel), f"statistik.{json_nyckel}") * skala

    return Belagg(
        adress=adress,
        korning_id=korning_id,
        fil=rel,
        fil_sha256=sha256_fil(fil),
        manifest="run.json",
        manifest_sha256=msha,
        analys="utci",
        losare=UTCI_LOSARE,
        scenarier=(scenario,),
        mottagare=f"scenario {scenario}",
        namnare=_UTCI_NAMNARE,
        matt=f"utci:{matt}",
        storhet=storhet,
        enhet=enhet,
        tidsdefinition=_UTCI_TID,
        statistik=stat,
        varde=varde,
        upplosning=upp,
        jamforbarhet=_utci_jamforbarhet(dok),
    )


def gyf_utci_belagg(
    kalla: FlodenBelaggKalla, adress: str, korning_id: str, delar: list[str]
) -> Belagg:
    """Belägg för `gyf.…` och `utci.…` ur körningens deterministiska resultatfiler."""
    if delar[0] == "gyf":
        return _gyf_belagg(kalla, adress, korning_id, delar)
    return _utci_belagg(kalla, adress, korning_id, delar)


# ---------------------------------------------------------------------- adresser & antaganden


def gyf_adresser(utmapp: Path) -> list[str]:
    """Tillgängliga `gyf.*`-beläggsadresser för en körnings utmapp (tom lista om GYF saknas)."""
    ut: list[str] = []
    for scen, fil in hitta_gyf_scenarier(utmapp).items():
        dok = _las_json(fil)
        if not dok:
            continue
        ut += [f"gyf.{scen}.{m}" for m in GYF_SKALAR_MATT]
        for df in dok.get("delfaktorer", ()):
            if isinstance(df, Mapping) and df.get("faktor_id"):
                fid = str(df["faktor_id"])
                ut += [f"gyf.{scen}.delfaktor.{fid}.{f}" for f in ("area_m2", "eko_effektiv_area_m2", "bidrag_gyf")]
    return list(dict.fromkeys(ut))


def utci_adresser(utmapp: Path) -> list[str]:
    """Tillgängliga `utci.*`-beläggsadresser för en körnings utmapp (tom lista om UTCI saknas)."""
    ut: list[str] = []
    for scen, fil in hitta_utci_scenarier(utmapp).items():
        dok = _las_json(fil)
        if not dok:
            continue
        stat = dok.get("statistik", {})
        for matt, (nyckel, *_rest) in UTCI_MATT.items():
            v = stat.get(nyckel)
            if isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(float(v)):
                ut.append(f"utci.{scen}.{matt}")
    return list(dict.fromkeys(ut))


def gyf_antaganden(utmapp: Path) -> list[str]:
    """Deterministiska antagandetexter för GYF när körningen har GYF-resultat (tom lista annars)."""
    ut: list[str] = []
    for scen, fil in hitta_gyf_scenarier(utmapp).items():
        dok = _las_json(fil)
        if not dok:
            continue
        profil = dok.get("profil_namn") or dok.get("profil_id") or "okänd profil"
        rikt = _sv_tal(dok.get("riktmarke"), 2)
        ut.append(
            f"Grönytefaktor (GYF, scenario {scenarionamn(scen)}): profil {profil}, riktmärke {rikt}. "
            "Schablonvikter för kvartersmark i tidig sållning; metodstatus: skiss."
        )
        for v in dok.get("varningar", ()):
            ut.append(f"GYF ({scenarionamn(scen)}): {v}")
    return list(dict.fromkeys(ut))


def utci_antaganden(utmapp: Path) -> list[str]:
    """Deterministiska antagandetexter för UTCI när körningen har UTCI-resultat (tom lista annars)."""
    ut: list[str] = []
    for scen, fil in hitta_utci_scenarier(utmapp).items():
        dok = _las_json(fil)
        if not dok:
            continue
        ut.append(
            f"Termiskt mikroklimat (UTCI/T_mrt, scenario {scenarionamn(scen)}): 6-riktnings "
            "medelstrålningstemperatur (ISO 7726 / SOLWEIG) och UTCI enligt Bröde m.fl. (2012). "
            "Metodstatus: skiss."
        )
        for v in dok.get("varningar", ()):
            ut.append(f"UTCI ({scenarionamn(scen)}): {v}")
    return list(dict.fromkeys(ut))


# ---------------------------------------------------------------------- sammanfattningstabeller


def _sv_tal(v: Any, dec: int = 2) -> str:
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
        return "–"
    return f"{float(v):.{dec}f}".replace(".", ",").replace("-", "−")


def _sv_area(v: Any) -> str:
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
        return "–"
    tal = float(v)
    if abs(tal) >= 100.0:
        return f"{tal:.0f}\u00a0m²"
    return f"{tal:.1f}".replace(".", ",") + "\u00a0m²"


def _sv_pct_fran_andel(v: Any) -> str:
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
        return "–"
    return f"{float(v) * 100.0:.1f}".replace(".", ",") + "\u00a0%"


def gyf_tabellrader(r: Rapport, tj: RapportTjanst) -> list[tuple[str, ...]]:
    """Deterministiska rader för GYF-sammanfattningstabell om rapporten har `gyf.*`-belägg.

    Returnerar tom lista när inga `gyf.*`-belägg finns i rapporten (no-op för befintliga rapporter).
    """
    par: list[tuple[str, str]] = []
    for p in r.pastaenden:
        for b in p.belagg:
            if b.losare == GYF_LOSARE or b.adress.startswith("gyf."):
                scen = b.scenarier[0] if b.scenarier else b.adress.split(".")[1]
                nyckel = (b.korning_id, scen)
                if nyckel not in par:
                    par.append(nyckel)
    if not par:
        return []

    rader: list[tuple[str, ...]] = []
    for korning_id, scen in par:
        try:
            utmapp = tj.kalla.utmapp(korning_id)
        except Exception:  # noqa: BLE001
            continue
        fil = hitta_gyf_fil(utmapp, scen)
        dok = _las_json(fil) if fil is not None else None
        if dok is None:
            continue
        rader.append(
            (
                scenarionamn(scen) if r.installningar.sbk else scen,
                str(dok.get("profil_id") or "–"),
                _sv_tal(dok.get("gyf"), 2),
                _sv_tal(dok.get("riktmarke"), 2),
                _sv_area(dok.get("eko_effektiv_area_m2")),
                _sv_area(dok.get("flaggad_area_m2")),
                _sv_area(dok.get("tomt_area_m2")),
                str(dok.get("metodstatus") or METODSTATUS_SKISS),
            )
        )
    return rader


def utci_tabellrader(r: Rapport, tj: RapportTjanst) -> list[tuple[str, ...]]:
    """Deterministiska rader för UTCI-sammanfattningstabell om rapporten har `utci.*`-belägg.

    Returnerar tom lista när inga `utci.*`-belägg finns i rapporten (no-op för befintliga rapporter).
    """
    par: list[tuple[str, str]] = []
    for p in r.pastaenden:
        for b in p.belagg:
            if b.losare == UTCI_LOSARE or b.adress.startswith("utci."):
                scen = b.scenarier[0] if b.scenarier else b.adress.split(".")[1]
                nyckel = (b.korning_id, scen)
                if nyckel not in par:
                    par.append(nyckel)
    if not par:
        return []

    rader: list[tuple[str, ...]] = []
    for korning_id, scen in par:
        try:
            utmapp = tj.kalla.utmapp(korning_id)
        except Exception:  # noqa: BLE001
            continue
        fil = hitta_utci_fil(utmapp, scen)
        dok = _las_json(fil) if fil is not None else None
        if dok is None:
            continue
        st = dok.get("statistik", {})
        rader.append(
            (
                scenarionamn(scen) if r.installningar.sbk else scen,
                f"{_sv_tal(st.get('utci_medel_c'), 1)}\u00a0°C",
                f"{_sv_tal(st.get('utci_p90_c'), 1)}\u00a0°C",
                f"{_sv_tal(st.get('utci_max_c'), 1)}\u00a0°C",
                f"{_sv_tal(st.get('t_mrt_medel_c'), 1)}\u00a0°C",
                _sv_pct_fran_andel(st.get("andel_stark_varmestress")),
                _sv_pct_fran_andel(st.get("andel_utanfor_giltighetsomrade")),
                str(dok.get("metodstatus") or METODSTATUS_SKISS),
            )
        )
    return rader


__all__ = [
    "GYF_DELFAKTOR_FALT",
    "GYF_FORMAT",
    "GYF_LOSARE",
    "GYF_SKALAR_MATT",
    "GYF_TILLAGG_FALT",
    "METODSTATUS_SKISS",
    "UTCI_FORMAT",
    "UTCI_LOSARE",
    "UTCI_MATT",
    "gyf_adresser",
    "gyf_antaganden",
    "gyf_tabellrader",
    "gyf_utci_belagg",
    "hitta_gyf_fil",
    "hitta_gyf_scenarier",
    "hitta_utci_fil",
    "hitta_utci_scenarier",
    "utci_adresser",
    "utci_antaganden",
    "utci_tabellrader",
]
