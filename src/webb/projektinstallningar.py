# ruff: noqa: E501
"""Projektinställningar som rör filer: byta namn och radera ett projekt (`/api/projekt/<id>/namn` och `/radera`).

Allt som bara rör registret (synlighet, medlemmar, inbjudningar, arkivering) ligger i `konto.kontoapi`
(`/konto/projekt/<id>/...`). Här finns det som också rör disken:

- **Byta namn** (`PUT /api/projekt/<id>/namn`, `{"namn"}`): projektets ägare, organisationsadmin och ägare i
  projektets organisation, och superadmin. Registrets namn och `namn` i projektmappens `projekt.json` byts;
  projektmappens namn, projektets id och körningarna ändras inte.
- **Radera permanent** (`POST /api/projekt/<id>/radera`, `{"bekrafta_namn"}`): bara projektets ägare och
  superadmin, och bara om projektets namn skrivs exakt. Tas bort: projektmappen (`data/lokal/projekt/<mapp>`
  med ritade förslag, frågor och projektets egna rutor), projektets körningar (`<korningsmapp>/<körnings-id>`
  vars `korning.json` har projektets id), rapporter och beställningar som hör till projektet, och i registret
  posten, projektrollerna och inbjudningarna. Aldrig: geografin, de delade cacherna (`data/cache/karna`) och
  allt som hör till något annat projekt. En pågående körning stoppar raderingen (409). Demokvarteret kan inte
  raderas. Händelsen `projekt-raderat` i registret har bara konto-id, projekt-id och organisations-id.

Raderingen tar bort filerna före registerposten, så att ett avbrutet försök kan göras om (att ta bort det som
redan är borta är inget fel).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from konto.register import BehorighetFel, RegisterFel
from webb.fel import FlodenWebbFel
from webb.projektkalla import PROJEKT_ID_DEMO, hitta_projektmapp
from webb.projektskapa import ProjektSkapaTjanst

if TYPE_CHECKING:
    from webb.api import FlodenApiApp

_logg = logging.getLogger(__name__)

_REGISTER_ID = re.compile(r"^[0-9a-f]{32}$")
_FEL_RATT = "Du har inte rätt att göra det här i projektet."


def _ta_bort_mapp(rot: Path, mapp: Path) -> bool:
    """Tar bort `mapp` om den är en egen mapp direkt under `rot` (aldrig `rot` själv, aldrig något utanför).
    En symbolisk länk tas bort som länk; det den pekar på rörs inte. Ger om något fanns att ta bort."""
    if mapp.is_symlink():
        mapp.unlink()
        return True
    if not mapp.exists():
        return False
    ren = mapp.resolve()
    if ren.parent != rot.resolve() or ren == rot.resolve():
        raise FlodenWebbFel(
            "Raderingen avbröts: en sökväg låg utanför projektets egna mappar.", status_kod=500
        )
    shutil.rmtree(ren)
    return True


def _poster_for_projekt(filer: Iterable[Path], projekt_id: str) -> list[Path]:
    """De JSON-filer ur `filer` vars fält `projekt` är projektets id (filer som inte går att läsa räknas inte)."""
    ut: list[Path] = []
    for fil in sorted(filer):
        try:
            d = json.loads(fil.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("projekt") == projekt_id and not fil.is_symlink():
            ut.append(fil)
    return ut


class ProjektInstallningarTjanst:
    """Byta namn och radera ett projekt. Delas av webbens API (och är provad direkt i tester)."""

    def __init__(self, app: FlodenApiApp) -> None:
        self.app = app
        self._las = threading.Lock()

    def _register(self) -> Any:
        reg = self.app.konto.register
        if not self.app.konto.aktiv or reg is None:
            raise FlodenWebbFel(
                "Projektinställningar kräver inloggning: rollerna finns i kontosystemet.",
                status_kod=403,
            )
        return reg

    @staticmethod
    def _projekt_id(projekt_id: str) -> str:
        if not _REGISTER_ID.match(projekt_id):
            raise FlodenWebbFel(_FEL_RATT, status_kod=403)
        return projekt_id

    @staticmethod
    def _text(payload: Mapping[str, Any], falt: str) -> str:
        varde = payload.get(falt)
        okanda = set(payload) - {falt}
        if okanda or not isinstance(varde, str):
            raise FlodenWebbFel(f"Ange bara {falt} (text).", status_kod=400)
        return varde

    # ------------------------------------------------------------------ byta namn

    def byt_namn(
        self, konto_id: str, projekt_id: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        reg = self._register()
        pid = self._projekt_id(projekt_id)
        namn = ProjektSkapaTjanst._namn(self._text(payload, "namn"))  # noqa: SLF001 - samma regler som vid skapande
        try:
            ny = reg.byt_projektnamn(konto_id, pid, namn)
        except BehorighetFel as exc:
            raise FlodenWebbFel(_FEL_RATT, status_kod=403) from exc
        except RegisterFel as exc:
            if str(exc) == "Okänt projekt":
                raise FlodenWebbFel(_FEL_RATT, status_kod=403) from exc
            raise FlodenWebbFel(
                str(exc), status_kod=409 if "finns redan" in str(exc) else 400
            ) from exc
        mapp = hitta_projektmapp(self.app.projektmapp, pid, tillat_projektid=False)
        if mapp is not None:
            self._skriv_namn(mapp / "projekt.json", ny)
        _logg.info("projekt omdopt konto=%s projekt=%s", konto_id, pid)
        return {"status": "namn-andrat", "projekt_id": pid, "namn": ny}

    @staticmethod
    def _skriv_namn(fil: Path, namn: str) -> None:
        try:
            d = json.loads(fil.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(d, dict):
            return
        d["namn"] = namn
        tmp = fil.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, fil)

    # ------------------------------------------------------------------ radera

    def radera(self, konto_id: str, projekt_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """`POST /api/projekt/<id>/radera`. Fel är `FlodenWebbFel`: 400 fel eller saknad bekräftelse, 403 saknad
        rätt (också okänt projekt), 409 demokvarteret eller en pågående körning."""
        reg = self._register()
        if projekt_id in (PROJEKT_ID_DEMO, self.app.konto.konfig.demoprojekt):
            raise FlodenWebbFel("Demokvarteret kan inte raderas.", status_kod=409)
        pid = self._projekt_id(projekt_id)
        bekrafta = self._text(payload, "bekrafta_namn")
        try:
            reg.kontrollera_radera(konto_id, pid, bekrafta)
        except BehorighetFel as exc:
            raise FlodenWebbFel(_FEL_RATT, status_kod=403) from exc
        except RegisterFel as exc:
            if str(exc) == "Okänt projekt":
                raise FlodenWebbFel(_FEL_RATT, status_kod=403) from exc
            raise FlodenWebbFel(str(exc), status_kod=400) from exc
        fragor = self.app.fragor
        with self._las:
            korningar = fragor.korningar_for_projekt(pid)
            if any(k.status in ("ko", "kor") for k in korningar):
                raise FlodenWebbFel(
                    "Projektet har en pågående körning. Vänta tills den är klar och försök igen.",
                    status_kod=409,
                )
            borttaget = {"projektmapp": 0, "korningar": 0, "rapporter": 0, "bestallningar": 0}
            korningsmapp = fragor.korningsmapp
            for k in korningar:
                if _ta_bort_mapp(korningsmapp, k.mapp):
                    borttaget["korningar"] += 1
            fragor.glom_korningar([k.korning_id for k in korningar])
            rapporter = Path(self.app.rapportmapp)
            for rapport in _poster_for_projekt(rapporter.glob("*/rapport.json"), pid):
                if _ta_bort_mapp(rapporter, rapport.parent):
                    borttaget["rapporter"] += 1
            for fil in _poster_for_projekt((rapporter / "bestallningar").glob("b-*.json"), pid):
                fil.unlink()
                borttaget["bestallningar"] += 1
            mapp = hitta_projektmapp(self.app.projektmapp, pid, tillat_projektid=False)
            if mapp is not None and _ta_bort_mapp(self.app.projektmapp, mapp):
                borttaget["projektmapp"] = 1
            self.app.kartverktyg.glom_projekt(pid)
            try:
                register = reg.radera_projekt(konto_id, pid)
            except BehorighetFel as exc:  # kan inte hända efter kontrollen; aldrig ett tyst fel
                raise FlodenWebbFel(_FEL_RATT, status_kod=403) from exc
        _logg.info("projekt raderat konto=%s projekt=%s %s", konto_id, pid, borttaget)
        return {"status": "raderat", "projekt_id": pid, "borttaget": {**borttaget, **register}}
