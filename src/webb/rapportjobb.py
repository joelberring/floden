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

import csv
import io
import json
import logging
import math
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

AMNEN_ORDNING: Final[tuple[str, ...]] = ("skyfall", "dagsljus", "ekologi", "luft")
STEG: Final[tuple[tuple[str, str], ...]] = (
    ("skapar", "Skapar rapporten och ritar kartor"),
    ("paastaenden", "Skriver påståenden med belägg"),
    ("kontrollerar", "Kontrollerar rapporten"),
    ("exporterar", "Exporterar PDF och Word"),
)
_JOBB_RE = re.compile(r"^j-[0-9a-f]{12}$")
_BEST_FALT = {"syfte", "skede", "mottagare_roll", "amnen", "villkor", "fragor", "bara_med_underlag", "scenario", "projekt"}
_LUFT_FALT = {
    "serie_fore_csv",
    "serie_efter_csv",
    "bakgrund_ug_m3",
    "osakerhet_halt_rel",
    "osakerhet_bakgrund_ug_m3",
    "emissionsfaktor_intervall",
    "depositionsfaktor_intervall",
    "grans_dygn_ug_m3",
    "max_dygn_ar",
    "receptor",
}
_LUFT_HALT_KOLUMNER: Final[tuple[str, ...]] = ("lokal_ug_m3", "halt_ug_m3", "halt_bidrag_ug_m3")
_LUFT_TID_KOLUMNER: Final[tuple[str, ...]] = ("tid", "tid_s", "timme")
_MAX_LUFT_CSV_BYTES: Final[int] = 5 * 1024 * 1024
_MAX_FRAGOR = 200
STANDARD_SYFTE = "Tidig sållning av frågorna för förslaget."
LUFT_INDATA_TEXT = (
    "Skissunderlaget för luft (PM10, luft.mkn) bygger på en extern timserie (CSV) och en uttryckligt "
    "angiven bakgrundshalt (µg/m³). Ange timserie och bakgrund för att skapa rapporten."
)


def _nu() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _krav_tal(varde: Any, namn: str, *, minst: float | None = None, over: float | None = None, under: float | None = None) -> float:
    if isinstance(varde, bool) or not isinstance(varde, int | float):
        raise FlodenWebbFel(f"{namn} måste vara ett ändligt tal.", status_kod=400)
    tal = float(varde)
    if not math.isfinite(tal):
        raise FlodenWebbFel(f"{namn} måste vara ett ändligt tal (inte NaN eller oändligt).", status_kod=400)
    if minst is not None and tal < minst:
        raise FlodenWebbFel(f"{namn} måste vara >= {minst:g} (fick {tal:g}).", status_kod=400)
    if over is not None and tal <= over:
        raise FlodenWebbFel(f"{namn} måste vara > {over:g} (fick {tal:g}).", status_kod=400)
    if under is not None and tal >= under:
        raise FlodenWebbFel(f"{namn} måste vara < {under:g} (fick {tal:g}).", status_kod=400)
    return tal


def _tolka_intervall(varde: Any, namn: str, *, strikt_positiv: bool) -> list[float]:
    if not isinstance(varde, list | tuple) or len(varde) != 2:
        raise FlodenWebbFel(f"{namn} måste vara två tal [lägst, högst].", status_kod=400)
    lo = _krav_tal(varde[0], f"{namn}[0]", over=0.0 if strikt_positiv else None, minst=None if strikt_positiv else 0.0)
    hi = _krav_tal(varde[1], f"{namn}[1]", over=0.0 if strikt_positiv else None, minst=None if strikt_positiv else 0.0)
    if lo > hi:
        raise FlodenWebbFel(f"{namn}: undre gräns ({lo:g}) får inte vara större än övre ({hi:g}).", status_kod=400)
    return [lo, hi]


def _tolka_luft_bool(varde: str | None, kolumn: str, namn: str, rad_nr: int) -> bool:
    text = (varde or "").strip().lower()
    if text in ("1", "true", "ja", "sant"):
        return True
    if text in ("0", "false", "nej", "falskt", ""):
        return False
    raise FlodenWebbFel(f"{namn} rad {rad_nr}: ogiltigt värde {varde!r} i '{kolumn}'.", status_kod=400)


def _validera_luft_csv(raw: Any, namn: str, *, receptor: str | None = None) -> str:
    """Validerar en extern eller Floden-genererad timserie-CSV för `luft.mkn`.

    För Flodens timserieformat (`halt_bidrag_ug_m3`, med valfria `receptor`, `svag_vind`,
    `ogiltig`) följs samma regler som `karna.luft_losare.LuftMkn` och originalkolumnerna
    bevaras så att `svag_vind`/`ogiltig` och receptorurval följer med till `luft.mkn`.
    Enkla externa serier (`lokal_ug_m3`/`halt_ug_m3`) normaliseras till `tid,lokal_ug_m3`.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise FlodenWebbFel(f"{namn} måste vara en icke-tom CSV-text.", status_kod=400)
    if len(raw.encode("utf-8")) > _MAX_LUFT_CSV_BYTES:
        raise FlodenWebbFel(f"{namn} är för stor (högst 5 MB).", status_kod=400)
    text = raw.lstrip("\ufeff")
    lasare = csv.reader(io.StringIO(text))
    rubrik = next(lasare, None)
    if not rubrik:
        raise FlodenWebbFel(f"{namn} saknar rubrikrad (ange t.ex. tid,lokal_ug_m3 eller tid_s,halt_ug_m3).", status_kod=400)
    norm_rubrik = [k.strip().lower() for k in rubrik]
    kol_per_namn = {k: i for i, k in enumerate(norm_rubrik) if k}
    if "halt_bidrag_ug_m3" in kol_per_namn:
        halt_kol = kol_per_namn["halt_bidrag_ug_m3"]
        rec_kol = kol_per_namn.get("receptor")
        svag_kol = kol_per_namn.get("svag_vind")
        ogilt_kol = kol_per_namn.get("ogiltig")
        alla_rader = [
            (rad_nr, rad)
            for rad_nr, rad in enumerate(lasare, start=2)
            if rad and any(cell.strip() for cell in rad)
        ]
        if rec_kol is not None:
            alla_rec = sorted(
                {rad[rec_kol].strip() if rec_kol < len(rad) else "" for _, rad in alla_rader}
            )
            if receptor is not None:
                valda_rader = [
                    (nr, rad)
                    for nr, rad in alla_rader
                    if (rad[rec_kol].strip() if rec_kol < len(rad) else "") == receptor
                ]
                if not valda_rader:
                    raise FlodenWebbFel(
                        f"{namn}: receptorn '{receptor}' saknas (tillgängliga: {alla_rec}).",
                        status_kod=400,
                    )
            elif "maxcell" in alla_rec:
                valda_rader = [
                    (nr, rad)
                    for nr, rad in alla_rader
                    if (rad[rec_kol].strip() if rec_kol < len(rad) else "") == "maxcell"
                ]
            elif len(alla_rec) <= 1:
                valda_rader = alla_rader
            else:
                raise FlodenWebbFel(
                    f"{namn} innehåller flera receptorer ({', '.join(alla_rec)}); "
                    "ange parametern 'receptor' eller en timserie med en enda receptor (eller 'maxcell').",
                    status_kod=400,
                )
        elif receptor is not None and receptor != "maxcell":
            raise FlodenWebbFel(
                f"{namn}: parametern receptor='{receptor}' angavs, men timserien saknar kolumnen 'receptor'.",
                status_kod=400,
            )
        else:
            valda_rader = alla_rader

        antal_giltiga = 0
        for rad_nr, rad in valda_rader:
            svag = (
                _tolka_luft_bool(rad[svag_kol] if svag_kol < len(rad) else "", "svag_vind", namn, rad_nr)
                if svag_kol is not None
                else False
            )
            ogilt = (
                _tolka_luft_bool(rad[ogilt_kol] if ogilt_kol < len(rad) else "", "ogiltig", namn, rad_nr)
                if ogilt_kol is not None
                else False
            )
            if ogilt or svag:
                continue
            if halt_kol >= len(rad):
                raise FlodenWebbFel(f"{namn} rad {rad_nr}: haltvärde saknas.", status_kod=400)
            halt_text = rad[halt_kol].strip()
            try:
                halt = float(halt_text)
            except ValueError as exc:
                raise FlodenWebbFel(f"{namn} rad {rad_nr}: '{halt_text}' är inte ett giltigt tal.", status_kod=400) from exc
            if not math.isfinite(halt) or halt < 0.0:
                raise FlodenWebbFel(
                    f"{namn} rad {rad_nr}: haltbidraget måste vara ett ändligt tal >= 0 (fick {halt_text!r}).",
                    status_kod=400,
                )
            antal_giltiga += 1
        if antal_giltiga < 24:
            raise FlodenWebbFel(
                f"{namn} måste innehålla minst 24 giltiga timvärden (1 helt dygn, utöver svag_vind/ogiltig), fick {antal_giltiga}.",
                status_kod=400,
            )
        buf = io.StringIO()
        skrivare = csv.writer(buf, lineterminator="\n")
        skrivare.writerow(norm_rubrik)
        for _, rad in valda_rader:
            skrivare.writerow(rad)
        return buf.getvalue()

    enkel_halt_kol = next((kol_per_namn[k] for k in _LUFT_HALT_KOLUMNER if k in kol_per_namn), None)
    if enkel_halt_kol is None:
        raise FlodenWebbFel(
            f"{namn} saknar haltkolumn (förväntade lokal_ug_m3, halt_ug_m3 eller halt_bidrag_ug_m3).",
            status_kod=400,
        )
    if receptor is not None:
        raise FlodenWebbFel(
            f"{namn}: parametern receptor='{receptor}' stöds inte för enkel timserie utan kolumnen 'halt_bidrag_ug_m3'.",
            status_kod=400,
        )
    rec_kol_enkel = kol_per_namn.get("receptor")
    tid_kol = next((kol_per_namn[k] for k in _LUFT_TID_KOLUMNER if k in kol_per_namn), None)
    ut_rader = ["tid,lokal_ug_m3"]
    antal = 0
    enkla_receptorer: set[str] = set()
    for rad_nr, rad in enumerate(lasare, start=2):
        if not rad or all(not cell.strip() for cell in rad):
            continue
        if rec_kol_enkel is not None:
            enkla_receptorer.add(rad[rec_kol_enkel].strip() if rec_kol_enkel < len(rad) else "")
        if enkel_halt_kol >= len(rad):
            raise FlodenWebbFel(f"{namn} rad {rad_nr}: haltvärde saknas.", status_kod=400)
        halt_text = rad[enkel_halt_kol].strip()
        try:
            halt = float(halt_text)
        except ValueError as exc:
            raise FlodenWebbFel(f"{namn} rad {rad_nr}: '{halt_text}' är inte ett giltigt tal.", status_kod=400) from exc
        if not math.isfinite(halt) or halt < 0.0:
            raise FlodenWebbFel(
                f"{namn} rad {rad_nr}: haltbidraget måste vara ett ändligt tal >= 0 (fick {halt_text!r}).",
                status_kod=400,
            )
        tid_text = (
            rad[tid_kol].strip().replace(",", " ").replace("\n", " ").replace("\r", " ")
            if tid_kol is not None and tid_kol < len(rad) and rad[tid_kol].strip()
            else str(antal)
        )
        ut_rader.append(f"{tid_text},{halt_text}")
        antal += 1
    if len(enkla_receptorer) > 1:
        raise FlodenWebbFel(
            f"{namn} innehåller flera receptorer ({', '.join(sorted(enkla_receptorer))}); skicka en timserie per receptor.",
            status_kod=400,
        )
    if antal < 24:
        raise FlodenWebbFel(
            f"{namn} måste innehålla minst 24 timvärden (1 helt dygn), fick {antal}.",
            status_kod=400,
        )
    return "\n".join(ut_rader) + "\n"


def _tolka_luft_indata(raw: Any) -> dict[str, Any]:
    """Validerar det valfria `luft`-objektet i `POST /api/projekt/<id>/rapporter` (Uppdrag 4)."""
    if not isinstance(raw, dict):
        raise FlodenWebbFel("Fältet 'luft' måste vara ett JSON-objekt.", status_kod=400)
    okanda = set(raw) - _LUFT_FALT
    if okanda:
        raise FlodenWebbFel(f"Okända fält i 'luft': {', '.join(sorted(okanda))}.", status_kod=400)
    if "bakgrund_ug_m3" not in raw or raw["bakgrund_ug_m3"] is None:
        raise FlodenWebbFel(
            "luft.bakgrund_ug_m3 måste anges som ett ändligt tal >= 0 (µg/m³); Floden hittar aldrig på en bakgrundshalt.",
            status_kod=400,
        )
    bakgrund = _krav_tal(raw["bakgrund_ug_m3"], "luft.bakgrund_ug_m3", minst=0.0)
    raw_receptor = raw.get("receptor")
    receptor: str | None = None
    if raw_receptor is not None:
        if not isinstance(raw_receptor, str) or not raw_receptor.strip():
            raise FlodenWebbFel("luft.receptor måste vara en icke-tom text.", status_kod=400)
        receptor = raw_receptor.strip()
    fore_csv = _validera_luft_csv(raw.get("serie_fore_csv"), "luft.serie_fore_csv", receptor=receptor)
    raw_efter = raw.get("serie_efter_csv")
    if raw_efter is None or (isinstance(raw_efter, str) and not raw_efter.strip()):
        efter_csv: str | None = None
    else:
        efter_csv = _validera_luft_csv(raw_efter, "luft.serie_efter_csv", receptor=receptor)

    osak_rel = (
        _krav_tal(raw["osakerhet_halt_rel"], "luft.osakerhet_halt_rel", minst=0.0, under=1.0)
        if raw.get("osakerhet_halt_rel") is not None
        else None
    )
    osak_bg = (
        _krav_tal(raw["osakerhet_bakgrund_ug_m3"], "luft.osakerhet_bakgrund_ug_m3", minst=0.0)
        if raw.get("osakerhet_bakgrund_ug_m3") is not None
        else None
    )
    parametrar: dict[str, Any] = {"bakgrund_ug_m3": bakgrund}
    if receptor is not None:
        parametrar["receptor"] = receptor
    if raw.get("emissionsfaktor_intervall") is not None:
        parametrar["emissionsfaktor_intervall"] = _tolka_intervall(
            raw["emissionsfaktor_intervall"], "luft.emissionsfaktor_intervall", strikt_positiv=False
        )
    elif osak_rel is not None:
        parametrar["emissionsfaktor_intervall"] = [round(1.0 - osak_rel, 6), round(1.0 + osak_rel, 6)]
    if raw.get("depositionsfaktor_intervall") is not None:
        parametrar["depositionsfaktor_intervall"] = _tolka_intervall(
            raw["depositionsfaktor_intervall"], "luft.depositionsfaktor_intervall", strikt_positiv=True
        )
    elif osak_rel is not None:
        parametrar["depositionsfaktor_intervall"] = [1.0, 1.0]
    if raw.get("grans_dygn_ug_m3") is not None:
        parametrar["grans_dygn_ug_m3"] = _krav_tal(raw["grans_dygn_ug_m3"], "luft.grans_dygn_ug_m3", over=0.0)
    if raw.get("max_dygn_ar") is not None:
        v_max = _krav_tal(raw["max_dygn_ar"], "luft.max_dygn_ar", minst=0.0)
        if v_max != int(v_max):
            raise FlodenWebbFel("luft.max_dygn_ar måste vara ett heltal >= 0.", status_kod=400)
        parametrar["max_dygn_ar"] = int(v_max)
    return {
        "serie_fore_csv": fore_csv,
        "serie_efter_csv": efter_csv,
        "parametrar": parametrar,
        "osakerhet_bakgrund_ug_m3": osak_bg,
    }


def _skriv_atomiskt(fil: Path, text: str) -> None:
    fil.parent.mkdir(parents=True, exist_ok=True)
    tmp = fil.parent / f".{fil.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, fil)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class RapportJobbTjanst:
    def __init__(self, app: FlodenApiApp) -> None:
        self.app = app
        self.mapp = app.rapportmapp / "jobb"
        self._lock = threading.RLock()
        self._aktiva: set[str] = set()
        self._stangd = False
        self._exekutor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="floden-rapport")
        self._rt: RapportTjanst | None = None

    def stang(self, *, vanta: bool = True) -> None:
        """Stänger bakgrundsexekutorn rent vid serveravstängning (idempotent)."""
        with self._lock:
            if self._stangd:
                return
            self._stangd = True
        self._exekutor.shutdown(wait=vanta, cancel_futures=True)

    @property
    def rt(self) -> RapportTjanst:
        """Samma `RapportTjanst` som MCP bygger (egen instans över samma rapportmapp)."""
        with self._lock:
            if self._rt is None:
                from floden_mcp.karna_api import FlodenTjanst
                from floden_mcp.oppenhet import Oppenhet
                from gemensamt.konfig import KonfigFel, las_konfig
                from rapport.skissrapport import RapportTjanst

                rot = self.app.konfig_fil.resolve().parent
                tjanst = FlodenTjanst(self.app, rot=rot)
                konfig: dict[str, Any] | None = None
                if self.app.konfig_fil.is_file():
                    try:
                        konfig = las_konfig(
                            self.app.konfig_fil,
                            self.app.konfig_fil.with_name("config.local.toml"),
                        )
                    except KonfigFel:
                        konfig = None
                tjanst.oppenhet = Oppenhet.for_tjanst(tjanst, konfig=konfig)
                self._rt = RapportTjanst(
                    tjanst,
                    rapportmapp=self.app.rapportmapp,
                    projekt_oppen=tjanst.projekt_oppen,
                )
            return self._rt

    # ------------------------------------------------------------------ vad som går att skapa

    def _korningar_for(self, projekt: str, amne: str) -> list[dict[str, Any]]:
        """Klara körningar i projektet som rapporten kan bygga på, i förvalsordning (`rapport.korningsval`)."""
        from rapport.korningsval import korningar_for

        return korningar_for(self.rt, projekt, amne)

    def mojligheter(self, projekt: str) -> dict[str, Any]:
        """Tillägg i `GET /api/projekt/<id>/rapporter` (K48): ämnena, om rapportmall finns och vilken körning som används."""
        from rapport.webbflode import AMNESNAMN, saknad_mall

        amnen: list[dict[str, Any]] = []
        for a in AMNEN_ORDNING:
            saknad = saknad_mall(a)
            kor: list[dict[str, Any]] = []
            if saknad is None:
                kor = self._korningar_for(projekt, a)
            if a == "luft" and saknad is None:
                kan_skapas = True
                forklaring: str | None = None if kor else LUFT_INDATA_TEXT
            else:
                forklaring = saknad
                if forklaring is None and not kor:
                    forklaring = (
                        f"Det finns ingen klar beräkning av {AMNESNAMN[a].lower()} (läget idag eller förslag) i projektet. "
                        "Kör en beräkning i steg 2 (Karta & analys) först."
                    )
                kan_skapas = forklaring is None
            amnen.append(
                {
                    "id": a,
                    "namn": AMNESNAMN[a],
                    "kan_skapas": kan_skapas,
                    "forklaring": forklaring,
                    "mall": saknad is None,
                    "forval_korning": kor[0]["korning_id"] if kor else None,
                    "forval_slag": kor[0]["slag"] if kor else None,
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
        okanda = set(d) - {"amne", "amnen", "korning_id", "bestallning", "fragor", "scenario", "luft"}
        if okanda:
            raise FlodenWebbFel(f"Okända fält: {', '.join(sorted(okanda))}.", status_kod=400)
        return d

    @staticmethod
    def _valj_amne(d: Mapping[str, Any]) -> str:
        raw_amne = d.get("amne")
        raw_amnen = d.get("amnen")
        amne_fran_lista: str | None = None
        if raw_amnen is not None:
            if not isinstance(raw_amnen, list) or not raw_amnen or not all(isinstance(a, str) for a in raw_amnen):
                raise FlodenWebbFel("Fältet 'amnen' ska vara en icke-tom lista med ämnesnamn.", status_kod=400)
            unika = list(dict.fromkeys(raw_amnen))
            if set(unika) == {"dagsljus", "sol"}:
                amne_fran_lista = "dagsljus"
            elif len(unika) == 1:
                amne_fran_lista = unika[0]
            else:
                raise FlodenWebbFel(
                    "Ange ett rapportämne per beställning (t.ex. ['skyfall'], ['ekologi'] eller ['luft']).",
                    status_kod=400,
                )
        if raw_amne is not None:
            if not isinstance(raw_amne, str):
                raise FlodenWebbFel("Fältet 'amne' ska vara text.", status_kod=400)
            if amne_fran_lista is not None and amne_fran_lista != raw_amne:
                raise FlodenWebbFel(
                    f"Fälten 'amne' ({raw_amne}) och 'amnen' ({raw_amnen}) stämmer inte överens.",
                    status_kod=400,
                )
            amne = raw_amne
        elif amne_fran_lista is not None:
            amne = amne_fran_lista
        else:
            amne = "skyfall"
        if amne not in AMNEN_ORDNING:
            raise FlodenWebbFel(f"Okänt ämne. Välj bland: {', '.join(AMNEN_ORDNING)}.", status_kod=400)
        return amne

    def _luft_bas_och_fragedir(self, projekt: str) -> tuple[Path, Path]:
        kalla = self.app.kartverktyg.kalla(projekt)
        forsta_mall = next(iter(kalla.mallar.values()), None)
        if forsta_mall is None:
            raise FlodenWebbFel(
                f"Projektet '{projekt}' saknar markmodell att koppla luftfrågan till.",
                status_kod=409,
            )
        fragedir = kalla.fragedir or self.app.kartverktyg.fragedir
        return forsta_mall.bas, fragedir

    def _skriv_luft_fraga(
        self,
        projekt: str,
        jobb_id: str,
        luft_data: Mapping[str, Any],
        bas: Path,
        fragedir: Path,
    ) -> tuple[str, str]:
        """Skriver validerade CSV-filer i jobbets arbetsmapp och K20-fråga för `luft.mkn`."""
        arbetsmapp = self.mapp / jobb_id
        arbetsmapp.mkdir(parents=True, exist_ok=True)
        fore_fil = arbetsmapp / "serie_fore.csv"
        _skriv_atomiskt(fore_fil, str(luft_data["serie_fore_csv"]))
        efter_fil: Path | None = None
        if luft_data.get("serie_efter_csv") is not None:
            efter_fil = arbetsmapp / "serie_efter.csv"
            _skriv_atomiskt(efter_fil, str(luft_data["serie_efter_csv"]))

        fragedir.mkdir(parents=True, exist_ok=True)
        fraga_id = f"luft-{jobb_id}"
        fraga_fil = fragedir / f"{fraga_id}.json"
        fil_mapp = fraga_fil.parent.resolve()
        rel_bas = Path(os.path.relpath(bas.resolve(), fil_mapp)).as_posix()
        parametrar = dict(luft_data["parametrar"])
        analyser: list[dict[str, Any]] = [
            {
                "id": "mkn_fore",
                "losare": "luft.mkn",
                "parametrar": parametrar,
                "indata": {"timserie": Path(os.path.relpath(fore_fil.resolve(), fil_mapp)).as_posix()},
            }
        ]
        if efter_fil is not None:
            analyser.append(
                {
                    "id": "mkn_efter",
                    "losare": "luft.mkn",
                    "parametrar": parametrar,
                    "indata": {"timserie": Path(os.path.relpath(efter_fil.resolve(), fil_mapp)).as_posix()},
                }
            )
        bg = float(parametrar["bakgrund_ug_m3"])
        osak_bg = luft_data.get("osakerhet_bakgrund_ug_m3")
        extra_bg = f" (angiven bakgrundsosäkerhet ±{float(osak_bg):g} µg/m³)" if osak_bg is not None else ""
        innehall = {
            "kontrakt": "K20/v1",
            "fraga_id": fraga_id,
            "beskrivning": (
                f"PM10 mot dygnsnormen (MKN) som intervall för projekt '{projekt}' ur extern timserie (CSV) "
                f"och uttryckligt angiven bakgrund {bg:g} µg/m³{extra_bg}."
            ),
            "scenarier": {"bas": {"bas": rel_bas}},
            "analyser": analyser,
        }
        _skriv_atomiskt(fraga_fil, json.dumps(innehall, indent=2, ensure_ascii=False) + "\n")
        ref = next(
            (r for r, f in self.app.fragor._fragefiler().items() if f.resolve() == fraga_fil.resolve()),  # noqa: SLF001
            None,
        )
        if ref is None:
            raise FlodenWebbFel("Den skapade luftfrågan hittades inte i frågemapparna.", status_kod=500)
        return ref, fraga_id

    def starta(self, projekt: str, kropp: bytes) -> dict[str, Any]:
        d = self._tolka(kropp)
        amne = self._valj_amne(d)
        from rapport.bestallning import BestallningFel
        from rapport.skissrapport import RapportFel
        from rapport.webbflode import saknad_mall

        forklaring = saknad_mall(amne)
        if forklaring is not None:
            raise FlodenWebbFel(forklaring, status_kod=409)
        from rapport.korningsval import UnderlagFel, valj_korning

        kid = d.get("korning_id")
        if kid is not None and not isinstance(kid, str):
            raise FlodenWebbFel("korning_id ska vara text.", status_kod=400)
        sc = d.get("scenario")
        if sc is not None and not isinstance(sc, str):
            raise FlodenWebbFel("scenario ska vara förslagets namn (slug) som text.", status_kod=400)

        raw_luft = d.get("luft")
        if raw_luft is not None and amne != "luft":
            raise FlodenWebbFel("Fältet 'luft' får bara anges när ämnet är 'luft'.", status_kod=400)
        if amne == "luft" and sc is not None:
            raise FlodenWebbFel(
                "Luftunderlaget har före och efter som två serier, inte som ett kartscenario; ange inget scenario.",
                status_kod=400,
            )

        luft_data: dict[str, Any] | None = None
        luft_bas_dir: tuple[Path, Path] | None = None
        valt: dict[str, Any] | None = None
        if amne == "luft" and raw_luft is not None:
            if kid is not None:
                raise FlodenWebbFel(
                    "Ange antingen korning_id för en befintlig luftkörning eller fältet 'luft' för ny timserie, inte båda.",
                    status_kod=400,
                )
            luft_data = _tolka_luft_indata(raw_luft)
            luft_bas_dir = self._luft_bas_och_fragedir(projekt)
        else:
            try:
                valt = valj_korning(self.rt, projekt, amne, korning_id=kid, scenario=sc)
            except UnderlagFel as exc:
                if amne == "luft" and exc.status == 409:
                    raise FlodenWebbFel(
                        "Det finns ingen klar luftberäkning (luft.mkn) i projektet. Ange fältet 'luft' med "
                        "serie_fore_csv och bakgrund_ug_m3 (>= 0).",
                        status_kod=400,
                    ) from exc
                raise FlodenWebbFel(str(exc), status_kod=exc.status) from exc
            kid = str(valt["korning_id"])

        b = d.get("bestallning") or {}
        if not isinstance(b, dict) or set(b) - _BEST_FALT:
            raise FlodenWebbFel(
                f"bestallning: tillåtna fält är {', '.join(sorted(_BEST_FALT - {'projekt'}))}.", status_kod=400
            )
        if b.get("projekt", projekt) != projekt:
            raise FlodenWebbFel("bestallning.projekt ska vara projektet i adressen.", status_kod=400)
        fragor_in = d.get("fragor")
        if fragor_in is not None:
            if (
                not isinstance(fragor_in, list)
                or len(fragor_in) > _MAX_FRAGOR
                or not all(isinstance(f, str) and len(f) <= 20 for f in fragor_in)
            ):
                raise FlodenWebbFel("fragor ska vara en lista med fråge-id ur frågekatalogen (till exempel SK-03).", status_kod=400)
            if b.get("fragor") and list(b["fragor"]) != fragor_in:
                raise FlodenWebbFel("Ange frågorna antingen i fragor eller i bestallning.fragor, inte olika i båda.", status_kod=400)
            b = {**b, "fragor": fragor_in}
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
        luft_fraga_ref: str | None = None
        if luft_data is not None and luft_bas_dir is not None:
            luft_fraga_ref, kid = self._skriv_luft_fraga(
                projekt, jobb_id, luft_data, luft_bas_dir[0], luft_bas_dir[1]
            )
            underlag = {
                "slag": "egen",
                "scenario": None,
                "fragor": list(best.urval),
                "demo": False,
            }
        else:
            assert valt is not None and kid is not None
            underlag = {
                "slag": valt["slag"],
                "scenario": valt["scenario"],
                "fragor": list(best.urval),
                "demo": valt["slag"] == "demo",
            }
        post: dict[str, Any] = {
            "jobb_id": jobb_id,
            "projekt": projekt,
            "amne": amne,
            "korning_id": kid,
            "underlag": underlag,
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
        if luft_fraga_ref is not None:
            post["luft_fraga_ref"] = luft_fraga_ref
        with self._lock:
            if self._stangd:
                raise FlodenWebbFel("Rapporttjänsten håller på att stängas.", status_kod=503)
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
        _skriv_atomiskt(fil, json.dumps(post, ensure_ascii=False, indent=2) + "\n")

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
            "underlag": p.get("underlag"),
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
        with self._lock:
            p = self._las(jobb_id)
            if p is None or p.get("projekt") != projekt:
                raise FlodenWebbFel(f"Okänt rapportjobb '{jobb_id}'.", status_kod=404)
            if p["status"] in ("ko", "kor") and jobb_id not in self._aktiva:
                p.update(status="fel", fel="Jobbet avbröts när servern stoppades.", slut=_nu())
                self._skriv(p)
            kopia = dict(p)
        return self._svar(kopia)

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

        try:
            with self._lock:
                p = self._las(jobb_id)
            if p is None:
                return
            self._kor_inre(p)
        except (RapportFel, PastaendeFel, BestallningFel, FlodenWebbFel, ValueError) as exc:
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
        luft_ref = p.get("luft_fraga_ref")
        if isinstance(luft_ref, str) and luft_ref:
            self.app.fragor.kor_synkront(luft_ref, str(p["korning_id"]), projekt_id=projekt)
        r = rt.skapa(
            projekt=projekt,
            korningar=[str(p["korning_id"])],
            mottagare=mottagare_for(projekt == "demokvarteret" and p["amne"] in ("skyfall", "dagsljus")),
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
        if not k["godkand"]:
            kontroll["fel"] = [str(f)[:300] for x in k["punkter"] for f in x.get("fel", [])][:10]
        if k["godkand"]:
            self._satt(jid, steg="exporterar", kontroll=kontroll)
            rt.exportera(r.rapport_id, "pdf", av="webb")
            rt.exportera(r.rapport_id, "word", av="webb")
            kontroll["exporterad"] = True
        self._satt(jid, status="klar", steg=None, kontroll=kontroll, slut=_nu())
