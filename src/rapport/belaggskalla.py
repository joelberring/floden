# ruff: noqa: E501
"""Belägg ur Flodens körningar: en enda källa för rapportens tal (R1).

Rapporten räknar ingenting. Ett belägg hämtas med en **adress** ur samma fasad som MCP-servern och
webben använder (`floden_mcp.karna_api.FlodenTjanst`):

| Adress | Källa | Exempel |
| --- | --- | --- |
| `jamforelse.<namn>.<mått>` | `FlodenTjanst.jamfor` = `FragaTjanst.marksiffror` (mark; takmasken som webben) | `jamforelse.skyfall_max_djup_efter_minus_fore.yta_okning_m2` |
| `matvarde.<analys>.<scenario>[.<ruta>].<namn>` | jobbets K0-manifest via `resultat_sammanfattning` | `matvarde.skyfall.efter.lagrat_m3` |
| `omrade.<mottagare>.<lager_id>.<statistik>` | `FlodenTjanst.resultat_varde` med rapportens polygon (tak uteslutna) | `omrade.garden.skyfall.efter.max_djup.max` |

Mått i jämförelser: `max_okning`, `max_minskning`, `yta_okning_m2`, `yta_minskning_m2`.
Statistik i område: `min`, `medel`, `median`, `max`, `yta_okning_m2`, `yta_minskning_m2`.

Ett mått får bara användas om dess storhet, tidsdefinition och statistik är kända
(`MATTKATALOG`). Annars vägras belägget: ett maxdjup ska inte kunna läsas som en vattennivå.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from floden_mcp.belagg import enhet_for
from floden_mcp.karna_api import FlodenFel, FlodenTjanst
from floden_mcp.korlogg import hash_av
from gemensamt.hash import sha256_fil
from rapport.pastaende import Belagg, PastaendeFel


class BelaggFel(PastaendeFel):
    """Belägget kan inte hämtas ur någon körning."""


@dataclass(frozen=True)
class Matt:
    """Vad ett mått är: storhet, tidsdefinition, statistik och upplösning (för precisionen)."""

    storhet: str
    tidsdefinition: str
    statistik: str
    upplosning: float | None = None
    """None = cellens yta (ytmått) eller ur lagret."""


_STATISK_TID = "läget vid regnets slut (statisk fyllning, inget förlopp)"
_DYNAMIK_TID = "högsta värde per cell under hela händelsen"
_REGN = "regnets parameter (indata, ingen statistik)"

VATTEN_MATVARDEN: Final[dict[str, Matt]] = {
    "max_djup_m": Matt(
        "största vattendjup över markytan",
        "",
        "största värde bland beräkningsområdets celler",
        0.001,
    ),
    "lagrat_m3": Matt("stående vattenvolym i sänkor", "", "summa över beräkningsområdet", 1.0),
    "ut_m3": Matt(
        "volym som rinner ut över beräkningsområdets kant",
        "summa under regnet",
        "summa över beräkningsområdets kant",
        1.0,
    ),
    "regn_mm": Matt("regnmängd", "under regnets varaktighet", _REGN, 0.1),
    "aterkomsttid_ar": Matt("regnets återkomsttid", "", _REGN, 1.0),
    "varaktighet_min": Matt("regnets varaktighet", "", _REGN, 1.0),
    "klimatfaktor": Matt("klimatfaktor på hela regnkurvan", "", _REGN, 0.01),
    "avtappning_mm_h": Matt(
        "avtappning till ledningsnätet", "under regnet", "parameter, lika i hela området", 0.1
    ),
    "takvatten_andel_ledning": Matt(
        "andel av takvattnet som går till ledningsnätet", "", "parameter", 0.01
    ),
    "volymfel_rel": Matt(
        "volymbalansens relativa fel (regn mot lagrat, utflöde och förluster)",
        "hela körningen",
        "en siffra per körning",
        1e-16,
    ),
    "takvatten_takyta_m2": Matt(
        "takyta vars regn leds till marken", "", "summa över byggnaderna", 1.0
    ),
}

MATTKATALOG: Final[dict[tuple[str, str], Matt]] = {
    ("vatten.statisk_fyllning", "max_djup"): Matt(
        "vattendjup över markytan", _STATISK_TID, "per cell", 0.001
    ),
    ("vatten.dynamik", "max_djup"): Matt(
        "vattendjup över markytan", _DYNAMIK_TID, "per cell", 0.001
    ),
    **{
        ("vatten.statisk_fyllning", f"matvarde:{n}"): Matt(
            m.storhet, m.tidsdefinition or _STATISK_TID, m.statistik, m.upplosning
        )
        for n, m in VATTEN_MATVARDEN.items()
    },
    **{
        ("vatten.dynamik", f"matvarde:{n}"): Matt(
            m.storhet, m.tidsdefinition or _DYNAMIK_TID, m.statistik, m.upplosning
        )
        for n, m in VATTEN_MATVARDEN.items()
    },
}

JAMFORELSEMATT: Final[dict[str, str]] = {
    "max_okning": "största ökning i en cell på mark (efter minus före)",
    "max_minskning": "största minskning i en cell på mark (före minus efter)",
    "yta_okning_m2": "yta på mark där värdet ökat mer än tröskeln",
    "yta_minskning_m2": "yta på mark där värdet minskat mer än tröskeln",
}
OMRADESSTATISTIK: Final[tuple[str, ...]] = (
    "min",
    "medel",
    "median",
    "max",
    "yta_okning_m2",
    "yta_minskning_m2",
)
_IGNORERA_PARAM: Final[frozenset[str]] = frozenset({"nyckel"})
HELA_OMRADET_MARK = "mark i hela beräkningsområdet; byggnadernas tak undantagna"
HELA_OMRADET = "hela beräkningsområdet (en siffra per körning)"


class Mottagare(Protocol):
    @property
    def namn(self) -> str: ...
    @property
    def polygon(self) -> Sequence[Sequence[float]]: ...
    @property
    def beskrivning(self) -> str: ...


class BelaggKalla(Protocol):
    """Det rapporten behöver av Floden: belägg, körningsfakta, kartor och metodstatus."""

    def hamta(self, adress: str, korning_id: str, mottagare: Mapping[str, Mottagare]) -> Belagg: ...


def _sv(v: float) -> str:
    return f"{v:g}".replace(".", ",")


class FlodenBelaggKalla:
    """Belägg ur `FlodenTjanst` (samma tal som webben och MCP-servern)."""

    def __init__(self, tjanst: FlodenTjanst) -> None:
        self.tjanst = tjanst

    # ------------------------------------------------------------------ hjälp

    def utmapp(self, korning_id: str) -> Path:
        try:
            k = self.tjanst.fragor._hamta(korning_id)  # noqa: SLF001 - samma körningsregister
        except Exception as exc:  # FlodenWebbFel: okänd körning
            raise BelaggFel(f"Körningen '{korning_id}' finns inte.") from exc
        if k.status != "klar":
            raise BelaggFel(f"Körningen '{korning_id}' är inte klar (status {k.status}).")
        return Path(k.utmapp)

    def _manifest(self, korning_id: str, rel: str) -> tuple[dict[str, Any], str]:
        fil = self.utmapp(korning_id) / rel
        if not fil.is_file():
            raise BelaggFel(f"K0-manifestet {rel} saknas i körningen '{korning_id}'.")
        return json.loads(fil.read_text(encoding="utf-8")), sha256_fil(fil)

    def _resultat(self, korning_id: str) -> dict[str, Any]:
        try:
            return self.tjanst._resultat(korning_id)  # noqa: SLF001
        except FlodenFel as exc:
            raise BelaggFel(str(exc)) from exc

    def _losare(self, korning_id: str, analys: str) -> str:
        res = self._resultat(korning_id)
        for t in res["tabeller"]:
            if t["analys"] == analys:
                return str(t["losare"])
        analyser = sorted({t["analys"] for t in res["tabeller"]})
        raise BelaggFel(
            f"Analysen '{analys}' finns inte i körningen '{korning_id}'. Analyser: "
            + ", ".join(analyser)
            + "."
        )

    def jobbparametrar(self, korning_id: str, analys: str, scenario: str) -> dict[str, Any]:
        """Analysens parametrar för ett scenario ur jobbets K0-manifest (utan jobbnyckeln)."""
        rel = f"{analys}/{scenario}/run.json"
        fil = self.utmapp(korning_id) / rel
        if not fil.is_file():
            # rutvisa jobb: ta första rutans manifest (alla rutor har samma parametrar)
            kandidater = sorted((self.utmapp(korning_id) / analys / scenario).glob("*/run.json"))
            if not kandidater:
                raise BelaggFel(f"Inget K0-manifest för {analys}/{scenario} i '{korning_id}'.")
            fil = kandidater[0]
        p = dict(json.loads(fil.read_text(encoding="utf-8")).get("parametrar", {}))
        return {k: v for k, v in p.items() if k not in _IGNORERA_PARAM}

    def _jamforbarhet(self, korning_id: str, analys: str, scenarier: Sequence[str]) -> str:
        hashar = [hash_av(self.jobbparametrar(korning_id, analys, s)) for s in scenarier]
        if len(set(hashar)) == 1:
            return hashar[0]
        return "EJ_JAMFORBAR:" + "/".join(
            f"{s}={h[:12]}" for s, h in zip(scenarier, hashar, strict=True)
        )

    def regn_text(self, korning_id: str, analys: str, scenario: str) -> str | None:
        p = self.jobbparametrar(korning_id, analys, scenario)
        if "aterkomsttid_ar" not in p:
            return None
        delar = [
            str(p.get("idf_rapport") or p.get("idf") or "regn"),
            f"återkomsttid {_sv(float(p['aterkomsttid_ar']))} år",
        ]
        if p.get("varaktighet_min") is not None:
            delar.append(f"varaktighet {_sv(float(p['varaktighet_min']))} min")
        if p.get("klimatfaktor") is not None:
            delar.append(f"klimatfaktor {_sv(float(p['klimatfaktor']))}")
        if p.get("regntyp"):
            delar.append(f"{p['regntyp']}regn")
        return ", ".join(delar)

    @staticmethod
    def _matt(losare: str, nyckel: str, adress: str) -> Matt:
        m = MATTKATALOG.get((losare, nyckel))
        if m is None:
            raise BelaggFel(
                f"Måttet i '{adress}' ({losare}, {nyckel}) har ingen känd tidsdefinition och "
                "statistik i rapportens måttkatalog, så det kan inte användas i en rapport "
                "(risk att t.ex. ett maxdjup läses som vattennivå)."
            )
        return m

    # ------------------------------------------------------------------ hämta

    def hamta(self, adress: str, korning_id: str, mottagare: Mapping[str, Mottagare]) -> Belagg:
        delar = adress.strip().split(".")
        if len(delar) < 3:
            raise BelaggFel(
                f"Adressen '{adress}' är ofullständig. Former: jamforelse.<namn>.<mått>, "
                "matvarde.<analys>.<scenario>.<namn>, omrade.<mottagare>.<lager_id>.<statistik>."
            )
        typ = delar[0]
        if typ == "jamforelse":
            return self._jamforelse(adress, korning_id, ".".join(delar[1:-1]), delar[-1])
        if typ == "matvarde":
            if len(delar) not in (4, 5):
                raise BelaggFel(
                    f"Adressen '{adress}' ska vara matvarde.<analys>.<scenario>[.<ruta>].<namn>."
                )
            ruta = delar[3] if len(delar) == 5 else None
            return self._matvarde(adress, korning_id, delar[1], delar[2], ruta, delar[-1])
        if typ == "omrade":
            if len(delar) < 5:
                raise BelaggFel(
                    f"Adressen '{adress}' ska vara omrade.<mottagare>.<lager_id>.<statistik>."
                )
            return self._omrade(
                adress, korning_id, delar[1], ".".join(delar[2:-1]), delar[-1], mottagare
            )
        raise BelaggFel(
            f"Okänd beläggstyp '{typ}' i '{adress}'. Tillåtna: jamforelse, matvarde, omrade."
        )

    def _jamforelse(self, adress: str, korning_id: str, namn: str, matt: str) -> Belagg:
        if matt not in JAMFORELSEMATT:
            raise BelaggFel(
                f"Okänt jämförelsemått '{matt}' i '{adress}'. Tillåtna: "
                + ", ".join(JAMFORELSEMATT)
                + "."
            )
        try:
            svar = self.tjanst.jamfor(korning_id)
        except FlodenFel as exc:
            raise BelaggFel(str(exc)) from exc
        post = next((j for j in svar["jamforelser"] if j["namn"] == namn), None)
        if post is None:
            namnen = ", ".join(j["namn"] for j in svar["jamforelser"])
            raise BelaggFel(
                f"Jämförelsen '{namn}' finns inte i körningen '{korning_id}'. Jämförelser: {namnen}."
            )
        if not post["yta_mark_enbart"] or not post["tak_maskat"]:
            raise BelaggFel(
                f"Jämförelsen '{namn}' saknar takmask (markraster), så talen räknar byggnadernas "
                "tak som mark. Sådana tal används inte i rapporter."
            )
        losare = self._losare(korning_id, post["analys"])
        grund = self._matt(losare, post["utdata"], adress)
        res = self._resultat(korning_id)
        lager = next(la for la in res["lager"] if la["id"] == f"skillnad.{namn}")
        px = float(lager["pixelstorlek_m"])
        upplosning = px * px if matt.startswith("yta") else float(grund.upplosning or 0.001)
        enhet = post[matt]["enhet"]
        tros = _sv(float(post["troskel"]))
        statistik = JAMFORELSEMATT[matt].replace("tröskeln", f"tröskeln {tros} {lager['enhet']}")
        manifest, msha = self._manifest(korning_id, "run.json")
        rasterfil = str(post["rasterfil"])
        fil = self.utmapp(korning_id) / rasterfil
        return Belagg(
            adress=adress,
            korning_id=korning_id,
            fil=rasterfil,
            fil_sha256=sha256_fil(fil) if fil.is_file() else None,
            manifest="run.json",
            manifest_sha256=msha,
            analys=str(post["analys"]),
            losare=losare,
            scenarier=(str(post["fore"]), str(post["efter"])),
            mottagare="hela beräkningsområdet",
            namnare=HELA_OMRADET_MARK,
            matt=f"{post['utdata']}:{matt}",
            storhet=f"skillnad i {grund.storhet}",
            enhet=enhet,
            tidsdefinition=grund.tidsdefinition,
            statistik=statistik,
            varde=float(post[matt]["varde"]),
            upplosning=upplosning,
            jamforbarhet=self._jamforbarhet(
                korning_id, str(post["analys"]), (str(post["fore"]), str(post["efter"]))
            ),
            regn=self.regn_text(korning_id, str(post["analys"]), str(post["efter"])),
        )

    def _matvarde(
        self,
        adress: str,
        korning_id: str,
        analys: str,
        scenario: str,
        ruta: str | None,
        namn: str,
    ) -> Belagg:
        losare = self._losare(korning_id, analys)
        grund = self._matt(losare, f"matvarde:{namn}", adress)
        try:
            svar = self.tjanst.resultat_sammanfattning(
                korning_id, analys=analys, scenario=scenario, ruta=ruta, max_rader=500
            )
        except FlodenFel as exc:
            raise BelaggFel(str(exc)) from exc
        rader = [r for t in svar["matvarden"] for r in t["rader"]]
        if not rader:
            raise BelaggFel(
                f"Scenariot '{scenario}' finns inte för analysen '{analys}' i '{korning_id}'."
            )
        if len(rader) > 1 and ruta is None:
            raise BelaggFel(
                f"Analysen '{analys}' har flera rutor i scenariot '{scenario}'; ange rutan: "
                f"matvarde.{analys}.{scenario}.<ruta>.{namn}."
            )
        rad = rader[0]
        if (
            namn not in rad["varden"]
            or not isinstance(rad["varden"][namn], int | float)
            or (isinstance(rad["varden"][namn], bool))
        ):
            raise BelaggFel(f"Mätvärdet '{namn}' finns inte som tal i {analys}/{scenario}.")
        rel = str(rad["belagg"]["fil"])
        _manifest, msha = self._manifest(korning_id, rel)
        return Belagg(
            adress=adress,
            korning_id=korning_id,
            fil=rel,
            fil_sha256=msha,
            manifest=rel,
            manifest_sha256=msha,
            analys=analys,
            losare=losare,
            scenarier=(scenario,),
            mottagare=f"scenario {scenario}" + (f", ruta {ruta}" if ruta else ""),
            namnare=HELA_OMRADET,
            matt=f"matvarden:{namn}",
            storhet=grund.storhet,
            enhet=enhet_for(namn),
            tidsdefinition=grund.tidsdefinition,
            statistik=grund.statistik,
            varde=float(rad["varden"][namn]),
            upplosning=float(grund.upplosning or 0.0),
            jamforbarhet=self._jamforbarhet(korning_id, analys, (scenario,)),
            regn=self.regn_text(korning_id, analys, scenario),
        )

    def _omrade(
        self,
        adress: str,
        korning_id: str,
        mottagarnamn: str,
        lager_id: str,
        stat: str,
        mottagare: Mapping[str, Mottagare],
    ) -> Belagg:
        if stat not in OMRADESSTATISTIK:
            raise BelaggFel(
                f"Okänd statistik '{stat}' i '{adress}'. Tillåtna: {', '.join(OMRADESSTATISTIK)}."
            )
        m = mottagare.get(mottagarnamn)
        if m is None:
            raise BelaggFel(
                f"Mottagaren '{mottagarnamn}' finns inte i rapporten. Lägg till den (namn och "
                "polygon) när rapporten skapas."
            )
        res = self._resultat(korning_id)
        la = next((x for x in res["lager"] if x["id"] == lager_id), None)
        if la is None:
            raise BelaggFel(
                f"Lagret '{lager_id}' finns inte i '{korning_id}'. Lager: "
                + ", ".join(x["id"] for x in res["lager"])
                + "."
            )
        try:
            svar = self.tjanst.resultat_varde(
                korning_id,
                analys=la["analys"],
                roll=la["roll"],
                omrade=[list(p) for p in m.polygon],
            )
        except FlodenFel as exc:
            raise BelaggFel(str(exc)) from exc
        st = next(s for s in svar["statistik"] if s["lager_id"] == lager_id)
        v = st.get(stat)
        if v is None:
            raise BelaggFel(f"Mottagaren '{mottagarnamn}' har inga markceller med värde i lagret.")
        losare = self._losare(korning_id, la["analys"])
        grund = self._matt(losare, la["roll"], adress)
        px = float(st["cellstorlek_m"])
        scen = (la["fore"], la["efter"]) if la["typ"] == "skillnad" else (la["scenario"],)
        fil = str(v["belagg"]["fil"])
        _m, msha = self._manifest(korning_id, "run.json")
        stat_text = {
            "min": "minsta värde",
            "medel": "medelvärde",
            "median": "median",
            "max": "största värde",
            "yta_okning_m2": "yta där värdet ökat mer än tröskeln",
            "yta_minskning_m2": "yta där värdet minskat mer än tröskeln",
        }[stat]
        return Belagg(
            adress=adress,
            korning_id=korning_id,
            fil=fil,
            fil_sha256=None,
            manifest="run.json",
            manifest_sha256=msha,
            analys=str(la["analys"]),
            losare=losare,
            scenarier=tuple(str(s) for s in scen),
            mottagare=f"mottagare {mottagarnamn}",
            namnare=f"markceller i mottagaren {mottagarnamn} ({m.beskrivning}); tak undantagna",
            matt=f"{la['roll']}:{stat}",
            storhet=("skillnad i " if la["typ"] == "skillnad" else "") + grund.storhet,
            enhet=v["enhet"],
            tidsdefinition=grund.tidsdefinition,
            statistik=f"{stat_text} över {st['antal_celler_med_varde']} celler",
            varde=float(v["varde"]),
            upplosning=px * px if stat.startswith("yta") else float(grund.upplosning or 0.001),
            jamforbarhet=self._jamforbarhet(korning_id, str(la["analys"]), scen),
            regn=self.regn_text(korning_id, str(la["analys"]), scen[-1]),
        )


__all__ = [
    "HELA_OMRADET",
    "HELA_OMRADET_MARK",
    "JAMFORELSEMATT",
    "MATTKATALOG",
    "VATTEN_MATVARDEN",
    "OMRADESSTATISTIK",
    "BelaggFel",
    "BelaggKalla",
    "FlodenBelaggKalla",
    "Matt",
    "Mottagare",
]
