# ruff: noqa: E501
"""Nytt projekt i webben och via MCP: `GET /api/geografier`, `POST /api/projekt` och verktyget `projekt_skapa`.

Allt byggs av samma kod som `floden projekt skapa` (`kommuner.projekt.skapa_projekt`); det här lagret lägger
till det kommandot inte behöver, eftersom det körs av en person i webben och inte av en administratör på
servern:

- **Behörighet** (`Register.organisationer_for_skapa`): superadmin, eller organisationsadmin eller ägare i en
  organisation. Enkel medlem och ledare får inte. Skaparen blir `utredare` och `skapad_av`; projektet är privat
  om inte `organisation_synlig` anges (`docs/KONTO.md`).
- **Inget befintligt projekt återanvänds.** Kommandot låter en omkörning ge samma projekt; här nekas ett namn som
  redan finns i organisationen eller som ger samma projektmapp, så att ingen kan få en roll i ett privat projekt
  (eller skriva över ett annat) genom att skapa ett med samma namn.
- **Inloggning krävs.** Utan inloggning finns inga roller; lokalt används `floden projekt skapa`.
- **Inga personuppgifter i loggar.** Bara konto-id och projekt-id loggas, aldrig namn, avgränsning eller adress.
  Namnet nekas om det liknar en e-postadress eller ett personnummer.

Svaret följer kontrakt K48 (`docs/DATAKONTRAKT.md`).
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shapely.geometry import box

from geografi.modell import (
    LAGERMAPP,
    GeografiFel,
    las_manifest,
    tolka_ref,
    versionsmapp,
)
from kommuner.konfig import KommunKonfig, KommunKonfigFel, hitta_kommun_for_geografi
from kommuner.projekt import skapa_projekt, slug, tolka_avgransning_objekt
from kommuner.skapa import SkapaFel
from konto.register import BehorighetFel, RegisterFel
from webb.fel import FlodenWebbFel

if TYPE_CHECKING:
    from webb.api import FlodenApiApp

_logg = logging.getLogger(__name__)

MIN_YTA_M2 = 100.0
"""Minsta avgränsning (10 x 10 m): mindre område ger inga meningsfulla frågor."""
NAMN_MAX = 120
_EPOST = re.compile(r"[^@\s]+@[^@\s]+")
_PERSONNUMMER = re.compile(r"(?<!\d)(\d{6}|\d{8})[-+]?\d{4}(?!\d)")
_FEL_ORG = (
    "Du får inte skapa projekt här: det kräver superadmin, eller organisationsadmin eller ägare i en organisation."
)


def _polygon(b: list[float]) -> dict[str, Any]:
    x0, y0, x1, y1 = b
    return {
        "type": "Polygon",
        "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
    }


class _RegisterIWebben:
    """Ger `skapa_projekt` den registrering webben vill ha: skaparen som utredare och `skapad_av`, delningen som
    valts och inget återanvänt projekt (`Register.skapa_projekt_av`) i stället för serverkommandots."""

    def __init__(self, register: Any, organisation_synlig: bool) -> None:
        self.register = register
        self.organisation_synlig = organisation_synlig

    def registrera_projekt_for_konto(
        self, konto_id: str, organisation_id: str, namn: str, forvaltning_id: str | None = None
    ) -> tuple[str, bool]:
        pid = self.register.skapa_projekt_av(
            konto_id, organisation_id, namn, organisation_synlig=self.organisation_synlig
        )
        return str(pid), True


class ProjektSkapaTjanst:
    """Geografierna och nya projekt för ett konto. Delas av webbens API och MCP-verktyget."""

    def __init__(self, app: FlodenApiApp, *, geomapp: Path, projektmapp: Path) -> None:
        self.app = app
        self.geomapp = geomapp
        self.projektmapp = projektmapp
        self.rot = app.konfig_fil.resolve().parent
        self._las = threading.Lock()
        self.kommun_for: Callable[[str], KommunKonfig] | None = None
        """Byter ut kommunfilens uppslag (tester)."""

    # ------------------------------------------------------------------ geografier

    def _register(self) -> Any:
        reg = self.app.konto.register
        if not self.app.konto.aktiv or reg is None:
            raise FlodenWebbFel(
                "Att skapa projekt kräver inloggning: rollerna (och skaparen som utredare) finns i kontosystemet. "
                "Lokalt utan inloggning: floden projekt skapa (docs/KOM-IGANG.md).",
                status_kod=403,
            )
        return reg

    def geografier(self, konto_id: str) -> dict[str, Any]:
        """`GET /api/geografier`: de mappade geografierna (K46) med giltig kärna och yttre gräns i EPSG:3011, och om
        kontot får skapa projekt i dem (och i vilka organisationer). Manifestets lager läses inte (ingen
        kontrollsummering); skapandet gör den."""
        reg = self._register()
        orgar = reg.organisationer_for_skapa(konto_id)
        ut: list[dict[str, Any]] = []
        if self.geomapp.is_dir():
            for idmapp in sorted(p for p in self.geomapp.iterdir() if p.is_dir() and p.name != LAGERMAPP):
                for vmapp in sorted(
                    (p for p in idmapp.iterdir() if p.is_dir()),
                    key=lambda p: (len(p.name), p.name),
                ):
                    ref = f"{idmapp.name}@{vmapp.name}"
                    try:
                        d, sha = las_manifest(self.geomapp, ref, verifiera=False)
                        karna = [float(v) for v in d["karna_3011"]]
                        gransen = [float(v) for v in d["gransen_3011"]]
                    except (GeografiFel, KeyError, TypeError, ValueError):
                        _logg.warning("geografi hoppas över (manifestet går inte att läsa): %s", ref)
                        continue
                    ut.append(
                        {
                            "ref": ref,
                            "id": str(d.get("id", idmapp.name)),
                            "version": str(d.get("version", vmapp.name)),
                            "namn": str(d.get("namn", idmapp.name)),
                            "kommun": str(d.get("kommun", "")),
                            "epsg": 3011,
                            "karna_3011": karna,
                            "karna_polygon_3011": _polygon(karna),
                            "gransen_3011": gransen,
                            "gransen_polygon_3011": _polygon(gransen),
                            "buffert_m": (d.get("buffert") or {}).get("vald_m"),
                            "varningar": [str(v) for v in d.get("varningar", [])],
                            "manifest_sha256": sha,
                            "far_skapa_projekt": bool(orgar),
                        }
                    )
        return {
            "geografier": ut,
            "far_skapa_projekt": bool(orgar),
            "organisationer": orgar,
        }

    # ------------------------------------------------------------------ nytt projekt

    @staticmethod
    def _namn(namn: Any) -> str:
        if not isinstance(namn, str) or not namn.strip():
            raise FlodenWebbFel("Ange ett projektnamn.", status_kod=400)
        n = namn.strip()
        if len(n) > NAMN_MAX or any(ord(t) < 32 for t in n):
            raise FlodenWebbFel(
                f"Projektnamnet ska vara högst {NAMN_MAX} tecken utan styrtecken.", status_kod=400
            )
        if _EPOST.search(n) or _PERSONNUMMER.search(n):
            raise FlodenWebbFel(
                "Projektnamnet ser ut som en e-postadress eller ett personnummer. Skriv inga personuppgifter i namnet.",
                status_kod=400,
            )
        return n

    def skapa(self, konto_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """`POST /api/projekt` och MCP `projekt_skapa`. Fel är `FlodenWebbFel` med HTTP-status: 400 indata (även
        avgränsning utanför geografins yttre gräns), 403 behörighet, 404 okänd geografi, 409 namnet är upptaget."""
        reg = self._register()
        okanda = set(payload) - {"geografi", "namn", "avgransning", "organisation_synlig", "organisation"}
        if okanda:
            raise FlodenWebbFel(f"Okända fält: {', '.join(sorted(okanda))}.", status_kod=400)
        orgar = reg.organisationer_for_skapa(konto_id)
        if not orgar:
            _logg.info("skapa projekt nekat konto=%s", konto_id)
            raise FlodenWebbFel(_FEL_ORG, status_kod=403)
        valda = payload.get("organisation")
        if valda is not None:
            if not isinstance(valda, str) or valda not in {o["id"] for o in orgar}:
                raise FlodenWebbFel(_FEL_ORG, status_kod=403)
            org = valda
        elif len(orgar) == 1:
            org = orgar[0]["id"]
        else:
            raise FlodenWebbFel(
                "Ange organisation: du får skapa projekt i flera. Alternativ: "
                + ", ".join(f"{o['id']} ({o['namn']})" for o in orgar)
                + ".",
                status_kod=400,
            )
        synlig = payload.get("organisation_synlig", False)
        if not isinstance(synlig, bool):
            raise FlodenWebbFel("organisation_synlig ska vara sant eller falskt.", status_kod=400)
        namn = self._namn(payload.get("namn"))
        ref = payload.get("geografi")
        if not isinstance(ref, str):
            raise FlodenWebbFel("Ange geografi som id@version, till exempel goteborg-test@v1.", status_kod=400)
        try:
            gid, version = tolka_ref(ref)
        except GeografiFel as exc:
            raise FlodenWebbFel(str(exc), status_kod=400) from exc
        if not (versionsmapp(self.geomapp, gid, version) / "manifest.json").is_file():
            raise FlodenWebbFel(f"Geografin {ref} finns inte.", status_kod=404)
        try:
            avgr = tolka_avgransning_objekt(payload.get("avgransning"))
        except SkapaFel as exc:
            raise FlodenWebbFel(str(exc), status_kod=400) from exc
        if (avgr[2] - avgr[0]) * (avgr[3] - avgr[1]) < MIN_YTA_M2:
            raise FlodenWebbFel(
                f"Avgränsningen är för liten: minst {MIN_YTA_M2:.0f} m² (till exempel 10 x 10 m).", status_kod=400
            )
        try:
            kommun = (
                self.kommun_for(gid) if self.kommun_for else hitta_kommun_for_geografi(gid, rot=self.rot)
            )
        except KommunKonfigFel as exc:
            raise FlodenWebbFel(f"Geografin {ref} saknar kommunfil på servern: {exc}", status_kod=409) from exc
        try:
            pid = slug(namn)
        except SkapaFel as exc:
            raise FlodenWebbFel(str(exc), status_kod=400) from exc
        mapp = self.projektmapp / pid
        with self._las:
            if mapp.exists() or reg.projektnamn_finns(org, namn):
                raise FlodenWebbFel(
                    "Namnet är upptaget (det ger samma projekt-id som ett befintligt projekt). Välj ett annat namn.",
                    status_kod=409,
                )
            try:
                res = skapa_projekt(
                    kommun,
                    ref=ref,
                    avgransning=avgr,
                    namn=namn,
                    konto_id=konto_id,
                    geo_rot=self.geomapp,
                    projektrot=self.projektmapp,
                    konfig={"konto": {"inloggning": "pa"}},
                    konfig_fil=self.rot / "config.toml",
                    organisation_id=org,
                    skriv=lambda _t: None,
                    register=_RegisterIWebben(reg, synlig),
                )
            except BaseException as exc:
                shutil.rmtree(mapp, ignore_errors=True)
                self._angra(reg, konto_id, org, namn)
                self._till_webbfel(exc, ref)
                raise
        from kommuner.projekt import las_projekt

        projekt = las_projekt(res.mapp)
        rid = str(projekt.get("register_id", ""))
        varningar = list(res.varningar)
        if _inte_rektangel(payload.get("avgransning")):
            varningar.insert(
                0,
                "Avgränsningen är en polygon: projektet räknas på dess omslutande rektangel "
                f"({', '.join(f'{v:.0f}' for v in avgr)}, EPSG:3011).",
            )
        _logg.info("projekt skapat konto=%s projekt=%s geografi=%s", konto_id, rid, ref)
        return {
            "kontrakt": "K48/v1",
            "projekt_id": rid,
            "namn": namn,
            "organisation": org,
            "organisation_synlig": synlig,
            "geografi": ref,
            "avgransning_3011": list(avgr),
            "roll": "utredare",
            "varningar": varningar,
        }

    def _angra(self, reg: Any, konto_id: str, org: str, namn: str) -> None:
        """Tar bort registerposten om bygget föll efter att den skapats (bara skaparens egen, utan andra roller)."""
        try:
            for p in reg.projekt_med_effektiv_roll(konto_id):
                if p["namn"] == namn:
                    reg.ta_bort_nytt_projekt(konto_id, str(p["id"]))
        except Exception:  # noqa: BLE001 - ett fel här får inte dölja det ursprungliga
            _logg.warning("registerposten för ett misslyckat projekt kunde inte tas bort konto=%s", konto_id)

    @staticmethod
    def _till_webbfel(exc: BaseException, ref: str) -> None:
        if isinstance(exc, FlodenWebbFel):
            raise exc
        if isinstance(exc, BehorighetFel):
            raise FlodenWebbFel(_FEL_ORG, status_kod=403) from exc
        if isinstance(exc, RegisterFel):
            raise FlodenWebbFel(str(exc), status_kod=409) from exc
        if isinstance(exc, SkapaFel):
            # Bland annat: avgränsningen ligger utanför geografins yttre gräns (där data saknas).
            raise FlodenWebbFel(str(exc), status_kod=400) from exc
        if isinstance(exc, GeografiFel):
            raise FlodenWebbFel(f"Geografin {ref} går inte att använda: {exc}", status_kod=409) from exc
        # Allt annat (inklusive KeyboardInterrupt) går vidare orört; typen loggas utan värden.
        _logg.error("oväntat fel när ett projekt skulle skapas: %s", type(exc).__name__)


def _inte_rektangel(avgransning: Any) -> bool:
    """Sant om avgränsningen är en polygon som inte är sin egen omslutande rektangel."""
    if isinstance(avgransning, Mapping):
        try:
            ring = (avgransning.get("geometry") or avgransning)["coordinates"][0]
            pts = [(float(p[0]), float(p[1])) for p in ring]
        except (KeyError, TypeError, ValueError, IndexError):
            return False
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        omslutande = box(min(xs), min(ys), max(xs), max(ys))
        from shapely.geometry import Polygon

        try:
            poly = Polygon(pts)
        except ValueError:
            return False
        return bool(abs(poly.area - omslutande.area) > 1e-6 * max(omslutande.area, 1.0))
    return False
