"""Projekt i webben (`GET /api/projekt`, `GET /api/projekt/<id>`).

Det enda projektet i den lokala webben är **Demokvarteret**, det påhittade kvarteret som
`floden demo` bygger och kör. Projektsidans tal kommer ur demokörningarnas egna resultat:

- lösarnas mätvärden (`sammanstallning.json`, t.ex. största djup före och efter),
- regnet som lösaren faktiskt använde (jobbens `run.json`),
- ytor och största förändring på **mark**, räknade ur kärnans skillnadsraster med takmasken
  som webben redan ritar bilderna med (byggnadernas tak är inte mark). Kärnans egen jämförelse
  räknar alla celler, tak inräknade, och används därför inte för dessa två mått.

Ett mått som inte går att räkna ur körningen utelämnas. Inga tal är hårdkodade här; texterna
om vad som ändrats i kvarteret kommer ur K9-scenariofilerna (`beskrivning`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from demovarld.fragor import FRAGA_DAGSLJUS, FRAGA_VATTEN
from webb.fel import FlodenWebbFel
from webb.fragor import FragaTjanst
from webb.moduler import visningsnamn

PROJEKT_ID = "demokvarteret"
NAMN = "Demokvarteret"
UNDERTITEL = "påhittat kvarter"

# Gränser för måtten (timmar respektive meter); kärnans jämförelsetrösklar är 0,1 h och 0,01 m.
SOL_GRANS_H = 1.0

_SV_MANADER = (
    "januari februari mars april maj juni juli augusti september oktober november december"
).split()


def _sv(tal: float, decimaler: int = 1) -> str:
    """Svenskt talformat för text (decimalkomma)."""
    return f"{tal:.{decimaler}f}".replace(".", ",")


def _datum_text(iso: str) -> str:
    try:
        _, m, d = (int(x) for x in iso.split("-"))
        text = f"{d} {_SV_MANADER[m - 1]}"
    except (ValueError, IndexError):
        return iso
    return f"{text} (vårdagjämning)" if (m, d) in ((3, 20), (3, 21)) else text


def _timmar_text(minuter: float) -> str:
    if minuter % 60 == 0:
        h = int(minuter // 60)
        return f"{h} timme" if h == 1 else f"{h} timmar"
    return f"{_sv(minuter, 0)} minuter"



def _vattenantaganden(res: dict[str, Any]) -> list[str]:
    """Takvattnets och avtappningens texter ur körningens manifest (lösarens egna)."""
    ut: list[str] = []
    for v in res.get("antaganden", {}).get("vatten", []):
        for t in (v.get("takvatten_text"), v.get("avtappning_text")):
            if t and t not in ut:
                ut.append(str(t))
    return ut

class ProjektTjanst:
    """Demokvarteret som projekt, byggt på frågetjänstens körningar."""

    def __init__(self, fragor: FragaTjanst) -> None:
        self.fragor = fragor

    # ------------------------------------------------------------------ hjälp

    def _demo_delar(self) -> dict[str, dict[str, Any]]:
        d = self.fragor.demo()
        if not d.get("finns"):
            return {}
        delar: dict[str, dict[str, Any]] = d.get("delar", {})
        return {
            ref: delar[ref]
            for ref in (FRAGA_DAGSLJUS, FRAGA_VATTEN)
            if ref in delar and "korning_id" in delar[ref]
        }

    def _klar(self, korning_id: str) -> bool:
        try:
            return bool(self.fragor.korning_status(korning_id)["status"] == "klar")
        except FlodenWebbFel:
            return False

    @staticmethod
    def ar_projekt(projekt_id: str) -> bool:
        """Är `projekt_id` ett projekt som projektsidan känner till (byggt eller inte)?"""
        return projekt_id == PROJEKT_ID

    def markmodell_mapp(self, projekt_id: str) -> Path:
        """Projektets K1-mapp (en ruta med `mark.tif`, `markslag.tif`, `byggnader.gpkg` ...).

        Demokvarteret: vattenrutan (fokusområdet), samma K1-ruta som vattenlösaren räknar på.
        Inget krav på att demokörningarna är klara; bara markmodellen behövs. Okänt projekt eller
        ingen byggd markmodell ger 404."""
        if not self.ar_projekt(projekt_id):
            raise FlodenWebbFel(f"Okänt projekt '{projekt_id}'.", status_kod=404)
        rot = self.fragor.demomapp
        rutor = (
            sorted(p for p in (rot / "vatten" / "bas").glob("mm_*") if (p / "mark.tif").is_file())
            if rot is not None
            else []
        )
        if len(rutor) != 1:
            raise FlodenWebbFel(
                f"Projektet '{projekt_id}' har ingen byggd markmodell. Bygg den med: floden demo",
                status_kod=404,
            )
        return rutor[0]

    def finns(self) -> bool:
        delar = self._demo_delar()
        return bool(delar) and all(self._klar(d["korning_id"]) for d in delar.values())

    def _k9_text(self, rel: str, reserv: str) -> str:
        mapp = self.fragor.demomapp
        if mapp is None:
            return reserv
        try:
            d = json.loads((mapp / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return reserv
        text = d.get("beskrivning")
        return text if isinstance(text, str) and text else reserv

    def _marksiffror(
        self, korning_id: str, lager: dict[str, Any], troskel: float
    ) -> dict[str, float] | None:
        """Ytor (m²) och största förändring på mark i ett skillnadslager, utan byggnadernas tak."""
        return self.fragor.marksiffror(korning_id, lager, troskel)

    @staticmethod
    def _skillnadslager(res: dict[str, Any], efter: str) -> dict[str, Any] | None:
        for la in res["lager"]:
            if la["typ"] == "skillnad" and la.get("efter") == efter and la["primar"]:
                return la  # type: ignore[no-any-return]
        return None

    @staticmethod
    def _lager_for(res: dict[str, Any], analys: str, scenario: str) -> str | None:
        for la in res["lager"]:
            traff = la["analys"] == analys and la["scenario"] == scenario and la["primar"]
            if traff and la["typ"] != "skillnad":
                return str(la["id"])
        return None

    @staticmethod
    def _teknisk(res: dict[str, Any], fraga: dict[str, Any]) -> dict[str, Any]:
        s = res["sammanfattning"]
        losare = [
            {
                "visningsnamn": visningsnamn(a["losare"]),
                "namn": a["losare"],
                "version": a["version"],
                "kodversion": a.get("kodversion"),
            }
            for a in res["antaganden"]["analyser"]
        ]
        return {
            "fraga_id": res["fraga_id"],
            "korning_id": res["korning"]["korning_id"],
            "fraga_fil": fraga.get("fil"),
            "losare": losare,
            "jobb": s["jobb"],
            "fran_cache": s["i_cache"],
            "korda": s["att_kora"],
            "total_tid_s": s["total_tid_s"],
            "koordinatsystem": "SWEREF 99 18 00 (EPSG:3011)",
            "manifest": "run.json per beräkning, i körningens resultatmapp",
        }

    # ------------------------------------------------------------------ avsnitt

    def _dagsljus(
        self, delar: dict[str, dict[str, Any]]
    ) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, Any]]]:
        ref = FRAGA_DAGSLJUS
        kid = delar[ref]["korning_id"]
        res = self.fragor.resultat(kid)
        fraga = self.fragor.hamta_fraga(ref)
        kolumner = [
            ("hojd", "Höjt hus", "dagsljus/k9_hojd.json", "Ett befintligt hus höjs."),
            ("nytt_hus", "Nytt hus", "dagsljus/k9_nytt_hus.json", "Ett nytt hus byggs."),
        ]
        siffror: dict[str, dict[str, float] | None] = {}
        vyer: list[dict[str, Any]] = []
        andringar: list[dict[str, str]] = []
        for sid, rubrik, k9, reserv in kolumner:
            lager = self._skillnadslager(res, sid)
            if lager is None:
                continue
            siffror[sid] = self._marksiffror(kid, lager, SOL_GRANS_H)
            text = self._k9_text(k9, reserv)
            andringar.append({"id": sid, "rubrik": rubrik, "text": text})
            vyer.append(
                {
                    "id": f"dagsljus-{sid}",
                    "avsnitt": "dagsljus",
                    "rubrik": f"Dagsljus: {rubrik.lower()}",
                    "mattnamn": "Soltimmar på marken",
                    "korning_id": kid,
                    "fore_lager": self._lager_for(res, lager["analys"], lager["fore"]),
                    "efter_lager": self._lager_for(res, lager["analys"], sid),
                    "skillnad_lager": lager["id"],
                    "efter_namn": rubrik,
                    "andring": text,
                }
            )
        datum = next(
            (a["parametrar"].get("datum") for a in fraga.get("analyser", []) if a["id"] == "sol"),
            None,
        )
        dag = _datum_text(str(datum[0])) if isinstance(datum, list) and datum else None
        rader: list[dict[str, Any]] = []
        if all(siffror.get(sid) for sid, *_ in kolumner):
            rader = [
                {
                    "namn": f"Mark som förlorar mer än {_sv(SOL_GRANS_H, 0)} timme sol",
                    "enhet": "m²",
                    "decimaler": 0,
                    "varden": {sid: siffror[sid]["yta_minskning_m2"] for sid, *_ in kolumner},  # type: ignore[index]
                },
                {
                    "namn": "Största minskning av soltimmar på mark",
                    "enhet": "timmar",
                    "decimaler": 1,
                    "varden": {sid: siffror[sid]["max_minskning"] for sid, *_ in kolumner},  # type: ignore[index]
                },
            ]
        avsnitt = {
            "id": "dagsljus",
            "rubrik": "Dagsljus: soltimmar på marken",
            "ingress": (
                "Hur många timmar solen når marken"
                + (f" en dag, {dag}" if dag else "")
                + ", före och efter en förändring i kvarteret. "
                + "Byggnadernas tak räknas inte som mark."
            ),
            "kolumner": [{"id": sid, "rubrik": rub} for sid, rub, *_ in kolumner],
            "matt": rader,
            "teknisk": self._teknisk(res, fraga),
        }
        return avsnitt, andringar, vyer

    def _vatten(
        self, delar: dict[str, dict[str, Any]]
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
        ref = FRAGA_VATTEN
        kid = delar[ref]["korning_id"]
        res = self.fragor.resultat(kid)
        fraga = self.fragor.hamta_fraga(ref)
        tab = next((t for t in res["tabeller"] if t["losare"].startswith("vatten.")), None)
        per_scenario: dict[str, dict[str, Any]] = {}
        if tab is not None:
            per_scenario = {r["scenario"]: r["varden"] for r in tab["rader"]}
        lager = self._skillnadslager(res, "efter")
        fore, efter = "fore", "efter"
        rader: list[dict[str, Any]] = []
        if fore in per_scenario and efter in per_scenario:
            for kol, namn, enhet, dec in (
                ("max_djup_m", "Största vattendjup i området", "m", 2),
                ("lagrat_m3", "Vatten som blir stående i sänkor", "m³", 0),
            ):
                if all(isinstance(per_scenario[s].get(kol), int | float) for s in (fore, efter)):
                    rader.append(
                        {
                            "namn": namn,
                            "enhet": enhet,
                            "decimaler": dec,
                            "varden": {s: float(per_scenario[s][kol]) for s in (fore, efter)},
                        }
                    )
        marksiffror = None if lager is None else self._marksiffror(kid, lager, float(
            next((j["troskel"] for j in res["jamforelser"] if j["analys"] == lager["analys"]), 0.01)
        ))
        andra: list[dict[str, Any]] = []
        if marksiffror is not None:
            andra = [
                {
                    "namn": "Mark där vattnet blir djupare än 1 cm",
                    "enhet": "m²",
                    "decimaler": 0,
                    "varden": {"efter": marksiffror["yta_okning_m2"]},
                },
                {
                    "namn": "Största ökning av djupet",
                    "enhet": "m",
                    "decimaler": 2,
                    "varden": {"efter": marksiffror["max_okning"]},
                },
            ]
        regn = next(iter(res["antaganden"]["regn"]), None)
        regn_ut: dict[str, Any] | None = None
        if regn and regn.get("kalla") and regn.get("regn_mm") is not None:
            delar_text = [str(regn.get("rapport") or regn["kalla"])]
            if regn.get("aterkomsttid_ar") is not None:
                delar_text.append(f"{_sv(float(regn['aterkomsttid_ar']), 0)} år")
            if regn.get("varaktighet_min") is not None:
                delar_text.append(_timmar_text(float(regn["varaktighet_min"])))
            if regn.get("klimatfaktor") is not None:
                delar_text.append(f"klimatfaktor {_sv(float(regn['klimatfaktor']), 1)}")
            regn_ut = {
                "kalla": regn.get("rapport") or regn["kalla"],
                "aterkomsttid_ar": regn.get("aterkomsttid_ar"),
                "varaktighet_min": regn.get("varaktighet_min"),
                "klimatfaktor": regn.get("klimatfaktor"),
                "regn_mm": regn["regn_mm"],
                "text": f"{', '.join(delar_text)} = {_sv(float(regn['regn_mm']), 1)} mm",
            }
        vyer: list[dict[str, Any]] = []
        if lager is not None:
            vyer.append(
                {
                    "id": "vatten-nytt-hus",
                    "avsnitt": "vatten",
                    "rubrik": "Vatten: nytt hus",
                    "mattnamn": "Största vattendjup",
                    "korning_id": kid,
                    "fore_lager": self._lager_for(res, lager["analys"], lager["fore"]),
                    "efter_lager": self._lager_for(res, lager["analys"], "efter"),
                    "skillnad_lager": lager["id"],
                    "efter_namn": "Nytt hus",
                    "andring": self._k9_text("vatten/k9_nytt_hus.json", "Ett nytt hus byggs."),
                }
            )
        avsnitt = {
            "id": "vatten",
            "rubrik": "Vatten: skyfall",
            "ingress": (
                "Var regnvattnet blir stående vid ett skyfall, före och efter att ett nytt hus "
                "byggs. Statisk beräkning: maxdjup i sänkor, inget flöde i tid. Regnet på "
                "taken rinner ned längs fasaderna."
            ),
            "kolumner": [
                {"id": fore, "rubrik": "Före"},
                {"id": efter, "rubrik": "Efter: nytt hus"},
            ],
            "matt": rader + andra,
            "regn": regn_ut,
            "antaganden": _vattenantaganden(res),
            "teknisk": self._teknisk(res, fraga),
        }
        return avsnitt, vyer, regn_ut

    def marksiffror_for(self, korning_id: str, lager_id: str) -> dict[str, Any]:
        """Ytor och största förändring på mark för ett skillnadslager (Resultat-steget).

        Samma funktion och samma regler som projektsidans tal (`FragaTjanst.marksiffror`, utan
        byggnadernas tak): tröskeln är soltimmar 1 timme (som projektsidan) och annars
        jämförelsens egen tröskel i körningen. Finns inget markvärde är `siffror` `None`."""
        res = self.fragor.resultat(korning_id)
        lager = next((x for x in res["lager"] if x["id"] == lager_id), None)
        if lager is None:
            raise FlodenWebbFel(f"Okänt lager '{lager_id}'.", status_kod=404)
        if lager["typ"] != "skillnad":
            raise FlodenWebbFel("Markytor finns bara för skillnadslager.", status_kod=422)
        if lager["roll"] == "soltimmar":
            troskel = SOL_GRANS_H
        else:
            troskel = float(
                next(
                    (j["troskel"] for j in res["jamforelser"] if j["analys"] == lager["analys"]),
                    0.01,
                )
            )
        return {
            "lager_id": lager_id,
            "enhet": lager.get("enhet"),
            "troskel": troskel,
            "siffror": self._marksiffror(korning_id, lager, troskel),
        }

    # ------------------------------------------------------------------ API

    def lista(self) -> dict[str, Any]:
        if not self.finns():
            return {"projekt": []}
        return {
            "projekt": [
                {
                    "id": PROJEKT_ID,
                    "namn": NAMN,
                    "undertitel": UNDERTITEL,
                    "paahittat": True,
                    "beskrivning": (
                        "Fyra kvarter med gator, gårdar och en sänka. "
                        "Två förändringar är beräknade: dagsljus och skyfall."
                    ),
                }
            ]
        }

    def hamta(self, projekt_id: str) -> dict[str, Any]:
        if projekt_id != PROJEKT_ID or not self.finns():
            raise FlodenWebbFel(f"Okänt projekt '{projekt_id}'.", status_kod=404)
        delar = self._demo_delar()
        dagsljus, andringar, vyer_d = self._dagsljus(delar)
        vatten, vyer_v, _ = self._vatten(delar)
        demo = self.fragor.demo()
        varld = demo.get("varld", {})
        return {
            "id": PROJEKT_ID,
            "namn": NAMN,
            "undertitel": UNDERTITEL,
            "paahittat": True,
            "paahittat_text": (
                "Kvarteret är påhittat: inga verkliga data och inga verkliga byggnader. "
                "Det finns för att visa hur Floden räknar och visar resultat."
            ),
            "beskrivning": (
                "Fyra kvarter med gator, gårdar och en skålformad sänka. Två förändringar är "
                "beräknade: dagsljus (en höjd byggnad och ett nytt hus) och skyfall (ett nytt hus)."
            ),
            "demo_nyckel": varld.get("nyckel"),
            "andringar": andringar,
            "avsnitt": [dagsljus, vatten],
            "vyer": [*vyer_d, *vyer_v],
        }
