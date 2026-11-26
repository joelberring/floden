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

import contextlib
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
    ren_rot = rot.resolve()
    if mapp.is_symlink():
        if mapp.parent.resolve() != ren_rot or mapp.name in ("", ".", ".."):
            raise FlodenWebbFel(
                "Raderingen avbröts: en sökväg låg utanför projektets egna mappar.", status_kod=500
            )
        mapp.unlink()
        return True
    if not mapp.exists():
        return False
    ren = mapp.resolve()
    if ren.parent != ren_rot or ren == ren_rot:
        raise FlodenWebbFel(
            "Raderingen avbröts: en sökväg låg utanför projektets egna mappar.", status_kod=500
        )
    shutil.rmtree(ren)
    return True


def _ta_bort_fil(rot: Path, fil: Path) -> bool:
    """Tar bort `fil` om den är en fil direkt under `rot` (aldrig något utanför)."""
    ren_rot = rot.resolve()
    if fil.is_symlink():
        if fil.parent.resolve() != ren_rot or fil.name in ("", ".", ".."):
            raise FlodenWebbFel(
                "Raderingen avbröts: en sökväg låg utanför projektets egna mappar.", status_kod=500
            )
        fil.unlink()
        return True
    if not fil.exists():
        return False
    ren = fil.resolve()
    if ren.parent != ren_rot or not ren.is_file():
        raise FlodenWebbFel(
            "Raderingen avbröts: en sökväg låg utanför projektets egna mappar.", status_kod=500
        )
    ren.unlink()
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

    def _har_pagaende_rapportjobb(self, pid: str) -> bool:
        """Finns det ett aktivt rapportjobb i minnet för projektet?"""
        rj = getattr(self.app, "_rapportjobb", None)
        if rj is None:
            return False
        aktiva = getattr(rj, "_aktiva", None)
        las = getattr(rj, "_las", None)
        if not aktiva or not callable(las):
            return False
        rj_lock = getattr(rj, "_lock", None)
        ctx = rj_lock if rj_lock is not None else contextlib.nullcontext()
        with ctx:
            for jid in list(aktiva):
                post = las(jid)
                if (
                    isinstance(post, Mapping)
                    and post.get("projekt") == pid
                    and post.get("status") in ("ko", "kor")
                ):
                    return True
        return False

    def _rensa_projektcache(self, pid: str, korning_ids: Iterable[str]) -> int:
        """Rensar projektets och dess körningars cacheposter i minnet (stöder valfria beroenden)."""
        rensade = 0
        kids = tuple(korning_ids)
        modell_cache = getattr(self.app, "_projektmodell_cache", None)
        if isinstance(modell_cache, dict):
            app_lock = getattr(self.app, "_lock", None)
            ctx = app_lock if app_lock is not None else contextlib.nullcontext()
            with ctx:
                nycklar = [
                    k
                    for k in list(modell_cache)
                    if k == pid or (isinstance(k, tuple) and bool(k) and k[0] == pid)
                ]
                for nyckel in nycklar:
                    modell_cache.pop(nyckel, None)
                    rensade += 1
        vy3d = getattr(self.app, "_vy3d", None)
        if vy3d is not None:
            vy_lock = getattr(vy3d, "_lock", None)
            ctx = vy_lock if vy_lock is not None else contextlib.nullcontext()
            with ctx:
                terrang = getattr(vy3d, "_terrang_per_projekt", None)
                if isinstance(terrang, dict) and pid in terrang:
                    terrang.pop(pid, None)
                    rensade += 1
                spann = getattr(vy3d, "_spann", None)
                if isinstance(spann, dict):
                    prefix = f"{pid}|"
                    for nyckel in [
                        k
                        for k in list(spann)
                        if isinstance(k, str)
                        and (
                            k.startswith(prefix)
                            or any(f"/{kid}/" in k or f"\\{kid}\\" in k for kid in kids)
                        )
                    ]:
                        spann.pop(nyckel, None)
                        rensade += 1
                minne = getattr(vy3d, "_minne", None)
                if isinstance(minne, dict) and minne:
                    rensade += len(minne)
                    minne.clear()
        rj = getattr(self.app, "_rapportjobb", None)
        rt = getattr(rj, "_rt", None) if rj is not None else None
        rt_tjanst = getattr(rt, "tjanst", None) if rt is not None else None
        byggnader = getattr(rt_tjanst, "_byggnader", None) if rt_tjanst is not None else None
        if isinstance(byggnader, dict) and pid in byggnader:
            byggnader.pop(pid, None)
            rensade += 1
        return rensade

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
            if self._har_pagaende_rapportjobb(pid):
                raise FlodenWebbFel(
                    "Projektet har ett pågående rapportjobb. Vänta tills det är klart och försök igen.",
                    status_kod=409,
                )
            borttaget = {"projektmapp": 0, "korningar": 0, "rapporter": 0, "bestallningar": 0}
            korning_ids = [k.korning_id for k in korningar]
            korningsmapp = fragor.korningsmapp
            for k in korningar:
                if _ta_bort_mapp(korningsmapp, k.mapp):
                    borttaget["korningar"] += 1
            fragor.glom_korningar(korning_ids)
            rapporter = Path(self.app.rapportmapp)
            for rapport in _poster_for_projekt(rapporter.glob("*/rapport.json"), pid):
                if _ta_bort_mapp(rapporter, rapport.parent):
                    borttaget["rapporter"] += 1
            bestallningsmapp = rapporter / "bestallningar"
            if bestallningsmapp.is_dir():
                for fil in _poster_for_projekt(bestallningsmapp.glob("b-*.json"), pid):
                    if _ta_bort_fil(bestallningsmapp, fil):
                        borttaget["bestallningar"] += 1
            rj = getattr(self.app, "_rapportjobb", None)
            jobbmapp = Path(getattr(rj, "mapp", None) or (rapporter / "jobb"))
            borttagna_jobb = 0
            if jobbmapp.is_dir():
                for fil in _poster_for_projekt(jobbmapp.glob("*.json"), pid):
                    if _ta_bort_fil(jobbmapp, fil):
                        borttagna_jobb += 1
            mapp = hitta_projektmapp(self.app.projektmapp, pid, tillat_projektid=False)
            if mapp is not None and _ta_bort_mapp(self.app.projektmapp, mapp):
                borttaget["projektmapp"] = 1
            kv = getattr(self.app, "kartverktyg", None)
            kv_borttagen = 0
            if kv is not None:
                kv_mapp = getattr(kv, "mapp", None)
                if kv_mapp is not None and _ta_bort_mapp(Path(kv_mapp), Path(kv_mapp) / pid):
                    kv_borttagen = 1
                glom = getattr(kv, "glom_projekt", None)
                if callable(glom):
                    glom(pid)
            rensad_cache = self._rensa_projektcache(pid, korning_ids)
            try:
                register = reg.radera_projekt(konto_id, pid)
            except BehorighetFel as exc:  # kan inte hända efter kontrollen; aldrig ett tyst fel
                raise FlodenWebbFel(_FEL_RATT, status_kod=403) from exc
        _logg.info(
            "projekt raderat konto=%s projekt=%s %s kartverktyg=%d rapportjobb=%d cache=%d",
            konto_id,
            pid,
            borttaget,
            kv_borttagen,
            borttagna_jobb,
            rensad_cache,
        )
        return {"status": "raderat", "projekt_id": pid, "borttaget": {**borttaget, **register}}
