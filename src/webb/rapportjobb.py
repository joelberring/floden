# ruff: noqa: E501
"""Skapa rapport i webben: `POST /api/projekt/<id>/rapporter` och jobbets status (K48).

Samma kod som MCP-verktygen `bestallning_skapa` och `rapport_skapa` (`RapportTjanst`), därefter påståenden ur
referensflödet (`rapport.webbflode`, utan AI), kontroll och export till PDF och Word. Rapporten tar tid (kartor,
kontroll, två exporter), så den körs som ett **jobb** med status, som körningarna (`webb.fragor`): ett anrop startar
(HTTP 202), `GET .../rapporter/jobb/<jobb_id>` följs tills `status` är `klar` eller `fel`. Jobbet sparas som
`<rapportmapp>/jobb/<jobb_id>.json`; ett jobb som var igång när servern stoppades blir `fel`. Ett jobb i taget
(kartritningen är tung); fler köas.

Behörighet prövas av `FlodenApiApp.hantera_http` (utredare i projektet, `effektiv_projektroll`). Körningen som
rapporten bygger på måste höra till projektet. Inga personuppgifter: jobbfilen och loggen har id:n, ämne och
status, aldrig syftestext.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.parse
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from webb.fel import FlodenWebbFel

if TYPE_CHECKING:
    from rapport.skissrapport import RapportTjanst
    from webb.api import FlodenApiApp

# `rapport.*` och `floden_mcp.*` importerar `webb.api`: de importeras först när de behövs (cirkelimport).

_logg = logging.getLogger(__name__)

AMNEN_ORDNING: Final[tuple[str, ...]] = ("skyfall", "dagsljus", "ekologi")
STEG: Final[tuple[tuple[str, str], ...]] = (
    ("skapar", "Skapar rapporten och ritar kartor"),
    ("paastaenden", "Skriver påståenden med belägg"),
    ("kontrollerar", "Kontrollerar rapporten"),
    ("exporterar", "Exporterar PDF och Word"),
)
_JOBB_RE = re.compile(r"^j-[0-9a-f]{12}$")
_BEST_FALT = {"syfte", "skede", "mottagare_roll", "amnen", "villkor", "fragor", "bara_med_underlag", "scenario", "projekt"}
STANDARD_SYFTE = "Tidig sållning av frågorna för förslaget."


def _nu() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class RapportJobbTjanst:
    def __init__(self, app: FlodenApiApp) -> None:
        self.app = app
        self.mapp = app.rapportmapp / "jobb"
        self._lock = threading.RLock()
        self._aktiva: set[str] = set()
        self._exekutor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="floden-rapport")
        self._rt: RapportTjanst | None = None

    @property
    def rt(self) -> RapportTjanst:
        """Samma `RapportTjanst` som MCP bygger (egen instans över samma rapportmapp)."""
        with self._lock:
            if self._rt is None:
                from floden_mcp.karna_api import FlodenTjanst
                from rapport.skissrapport import RapportTjanst

                rot = self.app.konfig_fil.resolve().parent
                self._rt = RapportTjanst(FlodenTjanst(self.app, rot=rot), rapportmapp=self.app.rapportmapp)
            return self._rt

    # ------------------------------------------------------------------ vad som går att skapa

    def _korningar_for(self, projekt: str, amne: str) -> list[dict[str, Any]]:
        """Klara körningar i projektet som rapporten kan bygga på (har ämnets analys och en jämförelse), nyast först."""
        ut: list[dict[str, Any]] = []
        for k in self.app.fragor.lista_korningar()["korningar"]:
            if k.get("status") != "klar" or self.app.projekt_for_korning(str(k["korning_id"])) != projekt:
                continue
            try:
                post = self.rt._korningspost(str(k["korning_id"]), amne)  # noqa: SLF001 - samma kontroll som rapport_skapa
            except Exception:  # noqa: BLE001 - körningen duger inte för ämnet
                continue
            if post["jamforelser"]:
                ut.append({"korning_id": post["korning_id"], "fraga_id": post["fraga_id"], "skapad": k.get("skapad")})
        return ut

    def mojligheter(self, projekt: str) -> dict[str, Any]:
        """Tillägg i `GET /api/projekt/<id>/rapporter` (K48): ämnena, om rapportmall finns och vilken körning som används."""
        from rapport.webbflode import AMNESNAMN, saknad_mall

        amnen: list[dict[str, Any]] = []
        for a in AMNEN_ORDNING:
            forklaring = saknad_mall(a)
            kor: list[dict[str, Any]] = []
            if forklaring is None:
                kor = self._korningar_for(projekt, a)
                if not kor:
                    forklaring = (
                        f"Det finns ingen klar beräkning av {AMNESNAMN[a].lower()} med före och efter i projektet. "
                        "Kör beräkningarna i steg 3 först."
                    )
            amnen.append(
                {
                    "id": a,
                    "namn": AMNESNAMN[a],
                    "kan_skapas": forklaring is None,
                    "forklaring": forklaring,
                    "mall": saknad_mall(a) is None,
                    "forval_korning": kor[0]["korning_id"] if kor else None,
                    "korningar": kor,
                }
            )
        return {"amnen": amnen}

    # ------------------------------------------------------------------ starta

    def _tolka(self, kropp: bytes) -> dict[str, Any]:
        try:
            d = json.loads(kropp.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise FlodenWebbFel("Kroppen ska vara JSON.", status_kod=400) from exc
        if not isinstance(d, dict):
            raise FlodenWebbFel("Kroppen ska vara ett JSON-objekt.", status_kod=400)
        okanda = set(d) - {"amne", "korning_id", "bestallning"}
        if okanda:
            raise FlodenWebbFel(f"Okända fält: {', '.join(sorted(okanda))}.", status_kod=400)
        return d

    def starta(self, projekt: str, kropp: bytes) -> dict[str, Any]:
        d = self._tolka(kropp)
        amne = d.get("amne", "skyfall")
        if amne not in AMNEN_ORDNING:
            raise FlodenWebbFel(f"Okänt ämne. Välj bland: {', '.join(AMNEN_ORDNING)}.", status_kod=400)
        from rapport.bestallning import BestallningFel
        from rapport.skissrapport import RapportFel
        from rapport.webbflode import AMNESNAMN, saknad_mall

        forklaring = saknad_mall(amne)
        if forklaring is not None:
            raise FlodenWebbFel(forklaring, status_kod=409)
        kid = d.get("korning_id")
        if kid is not None and not isinstance(kid, str):
            raise FlodenWebbFel("korning_id ska vara text.", status_kod=400)
        kandidater = self._korningar_for(projekt, amne)
        if kid is None:
            if not kandidater:
                raise FlodenWebbFel(
                    f"Det finns ingen klar beräkning av {AMNESNAMN[amne].lower()} med före och efter i projektet. "
                    "Kör beräkningarna i steg 3 först.",
                    status_kod=409,
                )
            kid = str(kandidater[0]["korning_id"])
        elif kid not in {k["korning_id"] for k in kandidater}:
            # Samma svar för en körning som saknas och en i ett annat projekt: ingen ledtråd om andras körningar.
            raise FlodenWebbFel(f"Körningen '{kid}' finns inte i projektet eller duger inte för {amne}.", status_kod=404)
        b = d.get("bestallning") or {}
        if not isinstance(b, dict) or set(b) - _BEST_FALT:
            raise FlodenWebbFel(
                f"bestallning: tillåtna fält är {', '.join(sorted(_BEST_FALT - {'projekt'}))}.", status_kod=400
            )
        if b.get("projekt", projekt) != projekt:
            raise FlodenWebbFel("bestallning.projekt ska vara projektet i adressen.", status_kod=400)
        try:
            best = self.rt.bestallning_skapa(
                syfte=str(b.get("syfte") or STANDARD_SYFTE),
                amnen=list(b.get("amnen") or (["dagsljus", "sol"] if amne == "dagsljus" else [amne])),
                projekt=projekt,
                skede=str(b.get("skede", "skiss")),
                mottagare_roll=str(b.get("mottagare_roll", "planhandlaggare")),
                fragor=list(b.get("fragor") or ()),
                villkor=list(b.get("villkor") or ()),
                bara_med_underlag=bool(b.get("bara_med_underlag", amne == "dagsljus")),
                scenario=b.get("scenario"),
            )
        except (BestallningFel, RapportFel) as exc:
            raise FlodenWebbFel(str(exc), status_kod=400) from exc
        if best.skede != "skiss":
            raise FlodenWebbFel(
                "Rapporten för planunderlag finns inte än (R3). Välj skedet skiss; beräkningarna körs som vanligt.",
                status_kod=409,
            )
        if best.rapportamne != amne:
            raise FlodenWebbFel(f"Beställningens ämnen hör till {best.rapportamne}, inte {amne}.", status_kod=400)
        jobb_id = "j-" + os.urandom(6).hex()
        post = {
            "jobb_id": jobb_id,
            "projekt": projekt,
            "amne": amne,
            "korning_id": kid,
            "bestallning_id": best.bestallning_id,
            "status": "ko",
            "steg": None,
            "rapport_id": None,
            "kontroll": None,
            "fel": None,
            "skapad": _nu(),
            "start": None,
            "slut": None,
        }
        with self._lock:
            self._skriv(post)
            self._aktiva.add(jobb_id)
        self._exekutor.submit(self._kor, jobb_id)
        _logg.info("rapportjobb startat jobb=%s projekt=%s amne=%s", jobb_id, projekt, amne)
        return self._svar(post)

    # ------------------------------------------------------------------ status

    def _fil(self, jobb_id: str) -> Path:
        return self.mapp / f"{jobb_id}.json"

    def _skriv(self, post: Mapping[str, Any]) -> None:
        self.mapp.mkdir(parents=True, exist_ok=True)
        fil = self._fil(str(post["jobb_id"]))
        tmp = fil.with_suffix(".tmp")
        tmp.write_text(json.dumps(post, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, fil)

    def _las(self, jobb_id: str) -> dict[str, Any] | None:
        if not _JOBB_RE.match(jobb_id):
            return None
        try:
            d = json.loads(self._fil(jobb_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return d if isinstance(d, dict) else None

    def _svar(self, p: Mapping[str, Any]) -> dict[str, Any]:
        steg_id = p.get("steg")
        nr = next((i + 1 for i, (s, _) in enumerate(STEG) if s == steg_id), 0)
        klar = p["status"] == "klar"
        lankar: dict[str, str] | None = None
        k = p.get("kontroll")
        if klar and p.get("rapport_id") and isinstance(k, dict) and k.get("exporterad"):
            bas = f"/api/projekt/{urllib.parse.quote(str(p['projekt']), safe='')}/rapporter/{p['rapport_id']}/fil"
            lankar = {"pdf": f"{bas}/pdf", "word": f"{bas}/word"}
        text = {
            "ko": "Väntar på sin tur.",
            "fel": "Rapporten kunde inte skapas.",
        }.get(str(p["status"]))
        if text is None:
            text = (
                ("Klar." if not k or k.get("exporterad") else "Rapporten är skapad men kontrollen underkände den.")
                if klar
                else dict(STEG).get(str(steg_id), "Startar.")
            )
        return {
            "kontrakt": "K48/v1",
            "jobb_id": p["jobb_id"],
            "projekt": p["projekt"],
            "amne": p["amne"],
            "korning_id": p["korning_id"],
            "bestallning_id": p["bestallning_id"],
            "status": p["status"],
            "steg": steg_id,
            "steg_nr": nr,
            "steg_totalt": len(STEG),
            "text": text,
            "rapport_id": p.get("rapport_id"),
            "kontroll": k,
            "lankar": lankar,
            "fel": p.get("fel"),
            "status_url": f"/api/projekt/{urllib.parse.quote(str(p['projekt']), safe='')}/rapporter/jobb/{p['jobb_id']}",
        }

    def status(self, projekt: str, jobb_id: str) -> dict[str, Any]:
        p = self._las(jobb_id)
        if p is None or p.get("projekt") != projekt:
            raise FlodenWebbFel(f"Okänt rapportjobb '{jobb_id}'.", status_kod=404)
        with self._lock:
            if p["status"] in ("ko", "kor") and jobb_id not in self._aktiva:
                p.update(status="fel", fel="Jobbet avbröts när servern stoppades.", slut=_nu())
                self._skriv(p)
        return self._svar(p)

    # ------------------------------------------------------------------ köra

    def _satt(self, jobb_id: str, **andringar: Any) -> dict[str, Any]:
        with self._lock:
            p = self._las(jobb_id) or {}
            p.update(andringar)
            self._skriv(p)
            return p

    def _kor(self, jobb_id: str) -> None:
        from rapport.bestallning import BestallningFel
        from rapport.pastaende import PastaendeFel
        from rapport.skissexport import ExportStoppad
        from rapport.skissrapport import RapportFel

        p = self._las(jobb_id)
        if p is None:
            return
        try:
            self._kor_inre(p)
        except (RapportFel, PastaendeFel, BestallningFel, ValueError) as exc:
            self._satt(jobb_id, status="fel", fel=str(exc), slut=_nu())
            _logg.info("rapportjobb fel jobb=%s typ=%s", jobb_id, type(exc).__name__)
        except ExportStoppad as exc:
            self._satt(jobb_id, status="fel", fel=f"Exporten stoppades av kontrollen: {exc}", slut=_nu())
        except Exception as exc:  # noqa: BLE001 - felet sparas på jobbet; loggen har bara typen
            self._satt(jobb_id, status="fel", fel=f"Oväntat fel ({type(exc).__name__}).", slut=_nu())
            _logg.exception("rapportjobb oväntat fel jobb=%s", jobb_id)
        finally:
            with self._lock:
                self._aktiva.discard(jobb_id)

    def _kor_inre(self, p: Mapping[str, Any]) -> None:
        from rapport.webbflode import mottagare_for, pastaenden_for

        jid, projekt = str(p["jobb_id"]), str(p["projekt"])
        rt = self.rt
        self._satt(jid, status="kor", start=_nu(), steg="skapar")
        r = rt.skapa(
            projekt=projekt,
            korningar=[str(p["korning_id"])],
            mottagare=mottagare_for(projekt == "demokvarteret"),
            ai_stod=False,
            av="webb",
            bestallning=str(p["bestallning_id"]),
        )
        self._satt(jid, rapport_id=r.rapport_id, steg="paastaenden")
        for kw in pastaenden_for(rt, r):
            rt.lagg_till_pastaende(r.rapport_id, forfattare="referensflode", av="webb", **kw)
        self._satt(jid, steg="kontrollerar")
        k = rt.kontrollera(r.rapport_id, av="webb")
        antal_fel = sum(len(x.get("fel", [])) for x in k["punkter"])
        kontroll: dict[str, Any] = {"godkand": bool(k["godkand"]), "antal_fel": antal_fel, "exporterad": False}
        if k["godkand"]:
            self._satt(jid, steg="exporterar", kontroll=kontroll)
            rt.exportera(r.rapport_id, "pdf", av="webb")
            rt.exportera(r.rapport_id, "word", av="webb")
            kontroll["exporterad"] = True
        self._satt(jid, status="klar", steg=None, kontroll=kontroll, slut=_nu())
