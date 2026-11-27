# ruff: noqa: E501
"""Fasaden mot kärnan: allt MCP-verktygen gör går genom samma tjänster som webben.

`FlodenTjanst` räknar ingenting. Den anropar `webb.fragor.FragaTjanst` (K20-frågor, körningar,
resultat, avläsning), `webb.kartverktyg.KartverktygTjanst` (ritade planförslag, K9, planering) och
kärnans egna funktioner (`karna.geometri`, `karna.utredning`, `karna.analyslosare`) och formar om
svaret till det som ett verktyg lämnar ut: tal med belägg, tydliga fel på svenska.

Varje metod returnerar en vanlig `dict` (JSON-kompatibel) och kastar `FlodenFel` när något inte går.
`dataklass` sätts av MCP-lagret (`floden_mcp.server`), inte här.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import statistics
import time
import tomllib
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

import numpy as np
from numpy.typing import NDArray
from shapely.geometry import Polygon, box

from floden_mcp.belagg import belagg, enhet_for, varde_med_belagg
from floden_mcp.karta import rendera_karta
from gemensamt.hash import sha256_fil
from gemensamt.manifest import visningsversion
from karna.analyslosare import LosarFel, lista_losare
from karna.geometri import GeometriFel, las_byggnader, las_terrang
from karna.ritat import HOJD_MAX_M, RitatFel
from karna.scenario import ScenarioFel
from karna.utredning import FragaFel, fraga_fran_dict, las_fraga
from webb.api import FlodenApiApp
from webb.fel import FlodenWebbFel
from webb.moduler import losarinfo, visningsnamn

PROJEKT_ID = "demokvarteret"
STANDARD_METODSTATUS = Path(__file__).resolve().parents[2] / "data" / "mcp" / "metodstatus.toml"
MAX_KORNINGSID_PER_VANTA = 20
KLARA = ("klar", "fel")
MARKSLAG_VATTEN = 8
_MAX_KARTA_RASTER_PX = 2048
_PROJEKT_UTSNITT_MARGINAL = 0.10
_T = TypeVar("_T")


class FlodenFel(Exception):
    """Ett fel som verktyget ska förklara för AI:n och användaren. `kod` är maskinläsbar."""

    def __init__(self, meddelande: str, kod: str = "fel") -> None:
        super().__init__(meddelande)
        self.kod = kod


@contextmanager
def _oversatt() -> Iterator[None]:
    """Gör kärnans och webbens fel till `FlodenFel` med klarspråk."""
    try:
        yield
    except FlodenFel:
        raise
    except FlodenWebbFel as exc:
        kod = {404: "okand", 409: "ej_klar", 400: "fel_indata", 422: "ogiltig"}.get(
            exc.status_kod, f"http_{exc.status_kod}"
        )
        raise FlodenFel(str(exc), kod) from exc
    except (RitatFel, ScenarioFel, FragaFel, LosarFel, GeometriFel) as exc:
        raise FlodenFel(str(exc), "ogiltig") from exc


def _ring(punkter: Any, vad: str) -> list[list[float]]:
    """Polygonens hörn som [[e, n], ...] (EPSG:3011); ändligt och minst tre hörn."""
    if not isinstance(punkter, Sequence) or isinstance(punkter, str) or len(punkter) < 3:
        raise FlodenFel(f"{vad}: ange minst tre hörn som [e, n] i EPSG:3011.", "fel_indata")
    ut: list[list[float]] = []
    for p in punkter:
        try:
            e, n = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError) as exc:
            raise FlodenFel(f"{vad}: varje hörn ska vara [e, n]-tal.", "fel_indata") from exc
        if not (math.isfinite(e) and math.isfinite(n)):
            raise FlodenFel(f"{vad}: hörnen måste vara ändliga tal.", "fel_indata")
        ut.append([e, n])
    return ut


def _rektangel(r: Any, vad: str) -> tuple[float, float, float, float]:
    try:
        x0, y0, x1, y1 = (float(v) for v in r)
    except (TypeError, ValueError) as exc:
        raise FlodenFel(f"{vad}: ange [e_min, n_min, e_max, n_max].", "fel_indata") from exc
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
        raise FlodenFel(
            f"{vad}: e_max och n_max måste vara större än e_min och n_min.", "fel_indata"
        )
    return x0, y0, x1, y1


def _kort_tid(t: str | None) -> str | None:
    return t


def _ett_av(scenario: str | None, fraga: str | None, lage_idag: bool) -> None:
    if (scenario is not None) + (fraga is not None) + bool(lage_idag) != 1:
        raise FlodenFel(
            "Ange antingen scenario (ett sparat scenario), fraga (ref) eller lage_idag=true "
            "(projektets befintliga läge utan förslag).",
            "fel_indata",
        )


def _standard_utsnitt_for_projekt(
    avgr: tuple[float, float, float, float],
    utbredning: Mapping[str, Any],
) -> tuple[float, float, float, float] | None:
    """Standardutsnitt kring projektets `avgransning_3011` (~10 % marginal) klämt till rastret.

    Returnerar `None` när projektets avgränsning redan täcker hela lagret eller ligger utanför det.
    """
    rx0 = float(utbredning["x_min"])
    ry0 = float(utbredning["y_min"])
    rx1 = float(utbredning["x_max"])
    ry1 = float(utbredning["y_max"])
    ax0, ay0, ax1, ay1 = avgr
    if ax1 <= rx0 or ax0 >= rx1 or ay1 <= ry0 or ay0 >= ry1:
        return None
    if ax0 <= rx0 and ay0 <= ry0 and ax1 >= rx1 and ay1 >= ry1:
        return None
    pad_x = (ax1 - ax0) * _PROJEKT_UTSNITT_MARGINAL
    pad_y = (ay1 - ay0) * _PROJEKT_UTSNITT_MARGINAL
    cx0 = max(rx0, ax0 - pad_x)
    cy0 = max(ry0, ay0 - pad_y)
    cx1 = min(rx1, ax1 + pad_x)
    cy1 = min(ry1, ay1 + pad_y)
    if cx1 <= cx0 or cy1 <= cy0:
        return None
    if (cx1 - cx0) >= (rx1 - rx0) - 1e-6 and (cy1 - cy0) >= (ry1 - ry0) - 1e-6:
        return None
    return (cx0, cy0, cx1, cy1)


def _rendera_karta_med_vatten(
    *,
    raster: NDArray[np.float32],
    tak: NDArray[np.bool_] | None,
    vatten_mask: NDArray[np.bool_],
    lager: Mapping[str, Any],
    byggnader: Sequence[Mapping[str, Any]] | None,
    korning_id: str,
    kodversion: str,
    skiss: bool,
    utsnitt: tuple[float, float, float, float] | None = None,
    max_px: int = 900,
    rubrik: str | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Samma kartrendering som `floden_mcp.karta.rendera_karta`, med permanent vattenyta (`markslag == 8`)."""
    from rapport.figurer import berakna_skalstock_m, figurstil_for_lager, rendera_kartfigur
    from testsvit.k1_io import GeoTransformInfo

    u = lager["utbredning"]
    px = float(lager["pixelstorlek_m"])
    ox_m, oy_m = float(u["x_min"]), float(u["y_max"])
    hojd_c, bredd_c = raster.shape
    c0, c1, r0, r1 = 0, bredd_c, 0, hojd_c
    if utsnitt is not None:
        x0, y0, x1, y1 = utsnitt
        c0 = max(0, int(math.floor((x0 - ox_m) / px)))
        c1 = min(bredd_c, int(math.ceil((x1 - ox_m) / px)))
        r0 = max(0, int(math.floor((oy_m - y1) / px)))
        r1 = min(hojd_c, int(math.ceil((oy_m - y0) / px)))
        if c1 <= c0 or r1 <= r0:
            raise FlodenFel("Utsnittet ligger utanför lagrets utsträckning.", "fel_indata")

    sub = raster[r0:r1, c0:c1]
    tak_sub = None if tak is None else tak[r0:r1, c0:c1]
    vatten_sub = vatten_mask[r0:r1, c0:c1]

    faktor = max(1, math.ceil(max(sub.shape) / _MAX_KARTA_RASTER_PX))
    sub_vis = sub[::faktor, ::faktor] if faktor > 1 else sub
    tak_vis = (
        None
        if tak_sub is None
        else (tak_sub[::faktor, ::faktor] if faktor > 1 else tak_sub)
    )
    vatten_vis = vatten_sub[::faktor, ::faktor] if faktor > 1 else vatten_sub
    px_bild = px * faktor

    h_c, b_c = sub_vis.shape
    x_min = ox_m + c0 * px
    y_max = oy_m - r0 * px
    x_max = x_min + b_c * px_bild
    y_min = y_max - h_c * px_bild

    geo = GeoTransformInfo(
        bredd=int(b_c),
        hojd=int(h_c),
        x_min=x_min,
        y_max=y_max,
        pixelstorlek_x=px_bild,
        pixelstorlek_y=px_bild,
        epsg=3011,
        dtype="float32",
    )

    scen = (
        f"{lager['efter']} − {lager['fore']}"
        if lager["typ"] == "skillnad"
        else str(lager["scenario"])
    )
    typtext = {
        "bas": "bas",
        "scenario": "scenario",
        "skillnad": "skillnad (efter minus före)",
    }[lager["typ"]]
    if rubrik is None:
        rubrik = f"{lager['etikett']}, {typtext}: {scen}".replace("_", " ")

    stil = figurstil_for_lager(lager)
    karta_max_px = max(120, int(max_px))
    skala_vis = karta_max_px / max(b_c, h_c)
    vis_w = max(200, int(round(b_c * skala_vis)))
    langd_m, _ = berakna_skalstock_m(geo, vis_w)

    anmarkning = (
        "SKISS: metoden är inte klassad för formell rapport. "
        "Ingen AI har räknat eller ritat talen."
        if skiss
        else "Ingen AI har räknat eller ritat talen."
    )
    duk = rendera_kartfigur(
        sub_vis,
        stil,
        titel=rubrik,
        undertitel=f"Körning {korning_id} · EPSG:3011",
        metodstatus="Skissunderlag" if skiss else "Rapportmetod",
        geo=geo,
        byggnad_mask=tak_vis,
        vatten_mask=vatten_vis if bool(np.any(vatten_vis)) else None,
        byggnader_3011=byggnader,
        anmarkning=anmarkning,
        kalla=f"Floden {kodversion} · Ritad ur körningens GeoTIFF",
        karta_max_px=karta_max_px,
        legend_placering="sida",
    )
    ut = io.BytesIO()
    duk.save(ut, format="PNG", optimize=True)
    return ut.getvalue(), {
        "bild_px": [duk.width, duk.height],
        "kartutsnitt": [x_min, y_min, x_max, y_max],
        "kartutsnitt_beskrivning": "[e_min, n_min, e_max, n_max] i EPSG:3011 (m)",
        "cellstorlek_m": px_bild,
        "enhet": lager["enhet"] or None,
        "skala": lager["skala"],
        "nedsampling": faktor,
        "norr": "uppat",
        "skalstock_m": float(langd_m),
        "kartmotor": "rapport.figurer",
        "figurstil": stil.namn,
    }


class FlodenTjanst:
    """Alla Flodens MCP-funktioner som vanliga metoder (se modulens beskrivning)."""

    def __init__(
        self,
        app: FlodenApiApp,
        *,
        rot: Path,
        mcp_mapp: Path | None = None,
        metodstatus_fil: Path | None = None,
    ) -> None:
        self.app = app
        self.rot = rot
        self.mcp_mapp = mcp_mapp or rot / "data" / "lokal" / "mcp"
        self.metodstatus_fil = metodstatus_fil or STANDARD_METODSTATUS
        self._byggnader: dict[str, tuple[int, list[dict[str, Any]]]] = {}
        self._kodversion: str | None = None
        self.oppenhet: Any = None
        """Spärren för icke-öppna data (`floden_mcp.oppenhet.Oppenhet`); sätts av `bygg_server`."""

    # ------------------------------------------------------------------ delar

    @property
    def fragor(self) -> Any:
        return self.app.fragor

    @property
    def kv(self) -> Any:
        return self.app.kartverktyg

    @property
    def kodversion(self) -> str:
        if self._kodversion is None:
            self._kodversion = visningsversion(self.rot)
        return self._kodversion

    def _kravs_projekt(self, projekt: str) -> None:
        with _oversatt():
            self.kv._kravs_projekt(projekt)  # noqa: SLF001 - samma regel som webben (404, ej byggd)

    def projektmapp(self, projekt: str) -> Path | None:
        """Mappen för ett projekt i en geografi, eller None (demokvarteret, okänt id)."""
        return self.kv.projektmapp(projekt)  # type: ignore[no-any-return]

    def projekt_oppen(self, projekt: str) -> bool | None:
        """Är projektets underlag öppna data? None för demokvarteret och okända id (de följer policyn som förut).
        Samma regel som spärren (`Oppenhet.bedom` på projektmappen: K38 och `[oppenhet.lager]`)."""
        mapp = self.projektmapp(projekt)
        if mapp is None:
            return None
        if self.oppenhet is None:
            from webb.projektkalla import projekt_oppenhet

            return projekt_oppenhet(mapp)[0]
        return bool(self.oppenhet.bedom(mapp).oppen)

    def kravs_rapportprojekt(self, projekt: str) -> None:
        """Projektet en rapport eller beställning får gälla: demokvarteret (byggt), eller ett projekt i
        kontoregistret (projekt i en geografi; deras körningar bär registrets id). Annat ger 404."""
        if projekt != PROJEKT_ID and self.app._registrerat_projekt(projekt):  # noqa: SLF001
            return
        self._kravs_projekt(projekt)

    def metodstatus(self, losare: str) -> dict[str, Any]:
        """Klassning (`skiss` eller `rapport`) och underlag för en lösare, ur `metodstatus.toml`."""
        data: dict[str, Any] = {}
        if self.metodstatus_fil.is_file():
            with self.metodstatus_fil.open("rb") as fh:
                data = tomllib.load(fh)
        standard = dict(data.get("defaults", {}))
        post = {**standard, **dict(data.get(losare, {}))}
        return {
            "status": post.get("status", "skiss"),
            "underlag": list(post.get("underlag", [])),
            "motivering": post.get("motivering"),
            "anmarkning": post.get("anmarkning"),
        }

    # ------------------------------------------------------------------ läsa: projekt

    def projekt_lista(self, konto_id: str | None = None) -> dict[str, Any]:
        """Projekten som anroparen har tillgång till. Med inloggning på är det kontots projekt
        (`FlodenApiApp.projekt_lista_for`, samma lista som webben); utan konto på en server med
        inloggning är listan tom. Inloggning av (lokalt): demokvarteret som förut."""
        with _oversatt():
            lista = self.app.projekt_lista_for(konto_id)["projekt"]
            demo = self.fragor.demo()
        ut: list[dict[str, Any]] = []
        for p in lista:
            ut.append({**p, "paahittat": bool(p.get("paahittat"))})
        inloggning_pa = self.app.konto.aktiv
        tips = None
        if inloggning_pa and konto_id is None:
            tips = "Inloggning krävs: projekten visas per konto (anslut över HTTP med en session)."
        elif not demo.get("finns"):
            tips = "Demokvarteret är inte byggt. Kör `floden demo` på datorn där Floden körs."
        return {"projekt": ut, "demon_byggd": bool(demo.get("finns")), "tips": tips}

    def projekt_info(self, projekt: str) -> dict[str, Any]:
        with _oversatt():
            info = self.kv.projektinfo(projekt)
            demo = self.fragor.demo()
        baser = {
            k: {
                "antal_rutor": len(b["rutor"]),
                "rutor": [[r["x_min"], r["y_min"], r["x_max"], r["y_max"]] for r in b["rutor"]],
                "analyser": b["analyser"],
            }
            for k, b in info["baser"].items()
        }
        u = info["utbredning"]
        return {
            "projekt": projekt,
            "paahittat": info["paahittat"],
            "epsg": info["epsg"],
            "utbredning": [u["x_min"], u["y_min"], u["x_max"], u["y_max"]],
            "utbredning_beskrivning": "K1-utsträckning [e_min, n_min, e_max, n_max] i EPSG:3011 (m).",
            "fokus": info["fokus"],
            "baser": baser,
            "antal_byggnader": len(info["byggnader"]),
            "tillgangliga_analyser": info["analyser"],
            "hojd_max_m": info["hojd_max_m"],
            # K50: ritlagren och rollerna. Stängda lager lämnas aldrig ut med innehåll (`ritlager.for_mcp`).
            "ritlager": {
                **self._ritlager_for_mcp(info),
                "roller": info["ritlager"]["roller"],
            },
            "sparade_scenarier": info["scenarier"],
            "ortofoto": info["ortofoto"],
            "slag": info["slag"],
            "geografi": info["geografi"],
            "avgransning_3011": info["avgransning_3011"],
            "oppen": info["oppen"],
            "demo": {
                "lage": demo.get("lage"),
                "varld_nyckel": demo.get("varld", {}).get("nyckel"),
                "forkorda_korningar": {
                    ref: d.get("korning_id")
                    for ref, d in demo.get("delar", {}).items()
                    if isinstance(d, dict) and d.get("korning_id")
                },
            }
            if info["slag"] == "demo"
            else None,
            "kodversion": self.kodversion,
        }

    @staticmethod
    def _ritlager_for_mcp(info: Mapping[str, Any]) -> dict[str, Any]:
        from karna import ritlager

        doc = {"kontrakt": ritlager.KONTRAKT, "lager": info["ritlager"]["lager"], "objekt": []}
        return ritlager.for_mcp(doc, info.get("oppen"))

    def _byggnadsposter(self, projekt: str = PROJEKT_ID) -> list[dict[str, Any]]:
        kalla = self.kv.kalla(projekt)
        nyckel = hash(kalla.stampel)
        cachad = self._byggnader.get(projekt)
        if cachad is not None and cachad[0] == nyckel:
            return cachad[1]
        mallar = kalla.mallar
        sedda: dict[str, dict[str, Any]] = {}
        for kort, m in mallar.items():
            for r in m.rutor:
                for b in las_byggnader(r.mapp):
                    if b.byggnad_id in sedda:
                        sedda[b.byggnad_id]["baser"].append(kort)
                        continue
                    poly = Polygon(b.polygon, [list(h) for h in b.hal])
                    c = poly.centroid
                    sedda[b.byggnad_id] = {
                        "id": b.byggnad_id,
                        "hojd_m": round(float(b.hojd_m), 2),
                        "yta_m2": round(float(poly.area), 2),
                        "takform": b.takform or "okand",
                        "vaningar": b.vaningar,
                        "andamal": b.andamal,
                        "hojdkalla": b.hojdkalla,
                        "centrum": [round(float(c.x), 2), round(float(c.y), 2)],
                        "_bbox": poly.bounds,
                        "baser": [kort],
                    }
        lista = sorted(sedda.values(), key=lambda p: str(p["id"]))
        for p in lista:
            p["baser"] = sorted(set(p["baser"]))
        self._byggnader[projekt] = (nyckel, lista)
        return lista

    def geometri_sammanfattning(self, projekt: str) -> dict[str, Any]:
        self._kravs_projekt(projekt)
        with _oversatt():
            mallar = self.kv.kalla(projekt).mallar
            byggn = self._byggnadsposter(projekt)
            rutor_per_bas: dict[str, Any] = {}
            lagsta: float | None = None
            hogsta: float | None = None
            for kort, m in mallar.items():
                recept = [
                    sha256_fil(r.mapp / "recept.json")
                    for r in m.rutor
                    if (r.mapp / "recept.json").is_file()
                ]
                rutor_per_bas[kort] = {
                    "bas": self.fragor._visa(m.bas),  # noqa: SLF001
                    "antal_rutor": len(m.rutor),
                    "recept_sha256": recept,
                }
                for r in m.rutor:
                    z, _geo = las_terrang(r.mapp)
                    ok = z[np.isfinite(z)]
                    if ok.size:
                        lagsta = float(ok.min()) if lagsta is None else min(lagsta, float(ok.min()))
                        hogsta = float(ok.max()) if hogsta is None else max(hogsta, float(ok.max()))
        h = [float(b["hojd_m"]) for b in byggn]
        per_takform: dict[str, int] = {}
        per_hojdkalla: dict[str, int] = {}
        for b in byggn:
            per_takform[b["takform"]] = per_takform.get(b["takform"], 0) + 1
            per_hojdkalla[b["hojdkalla"]] = per_hojdkalla.get(b["hojdkalla"], 0) + 1
        return {
            "projekt": projekt,
            "kalla": {
                "typ": "k1_v2_underlag",
                "text": "Byggnadsregistret (byggnader.gpkg) och terrang.tif i K1 v2-rutorna; "
                "inga körningsresultat.",
                "baser": rutor_per_bas,
            },
            "byggnader": {
                "antal": len(byggn),
                "fotavtryck_summa_m2": round(sum(float(b["yta_m2"]) for b in byggn), 2),
                "hojd_m": (
                    {"min": min(h), "median": statistics.median(h), "max": max(h)} if h else None
                ),
                "per_takform": dict(sorted(per_takform.items())),
                "per_hojdkalla": dict(sorted(per_hojdkalla.items())),
                "anmarkning": (
                    "hojdkalla 'standard' betyder att höjden inte är mätt utan en standardhöjd."
                ),
            },
            "marknivå_rh2000_m": (
                None
                if lagsta is None
                else {"min": round(lagsta, 3), "max": round(hogsta or 0.0, 3)}
            ),
        }

    def byggnader_lista(
        self,
        projekt: str,
        omrade: Sequence[float] | None = None,
        max_antal: int = 100,
    ) -> dict[str, Any]:
        self._kravs_projekt(projekt)
        with _oversatt():
            alla = self._byggnadsposter(projekt)
        urval = alla
        if omrade is not None:
            filter_ = box(*_rektangel(omrade, "omrade"))
            urval = [b for b in alla if filter_.intersects(box(*b["_bbox"]))]
        max_antal = max(1, min(int(max_antal), 500))
        synliga = [{k: v for k, v in b.items() if not k.startswith("_")} for b in urval[:max_antal]]
        return {
            "projekt": projekt,
            "omrade": list(omrade) if omrade is not None else None,
            "antal_traffar": len(urval),
            "antal_visade": len(synliga),
            "trunkerad": len(urval) > len(synliga),
            "byggnader": synliga,
            "enheter": {"hojd_m": "m", "yta_m2": "m²", "centrum": "e, n i EPSG:3011 (m)"},
            "kalla": "byggnader.gpkg i K1 v2-rutorna (se geometri_sammanfattning)",
        }

    # ------------------------------------------------------------------ läsa: frågor, körningar

    def _projekt_id_i_mapp(self, pmapp: Path) -> str | None:
        """Projektets id ur `<projektmapp>/projekt.json` (register_id med inloggning på)."""
        try:
            d = json.loads((pmapp / "projekt.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(d, dict) or d.get("kontrakt") != "floden-projekt/v1":
            return None
        rid = d.get("register_id")
        if isinstance(rid, str) and rid.strip():
            return rid.strip()
        if not self.app.konto.aktiv:
            pid = d.get("projekt_id")
            if isinstance(pid, str) and pid.strip():
                return pid.strip()
        return None

    def fragefil_eller_none(self, ref: str) -> Path | None:
        """Frågefilen för `ref` utan fel (None om ogiltig eller okänd)."""
        try:
            fil: Path = self.fragor._fil_for(ref)  # noqa: SLF001
        except FlodenWebbFel:
            return None
        return fil

    def fraga_projekt(self, ref: str, fil: Path | None = None) -> str | None:
        """Ägarprojektet för frågan `ref`, eller `None` för fristående frågor utanför projekt.

        - Frågor under `<projektrot>/<mapp>/...` binds till projektets `register_id` ur
          `projekt.json` (eller `projekt_id` när inloggning är av och `register_id` saknas).
        - Demofrågor (`demo.json`, `demomapp`) och frågor i kartverktygets egen `fragedir`
          hör till `demokvarteret`.
        - Övriga fristående frågor ger `None` (kräver superadmin när inloggning är aktiv).
        """
        if fil is None:
            fil = self.fragefil_eller_none(ref)
        if fil is not None:
            ren = fil.resolve()
            projektrot = getattr(self.fragor, "projektrot", None)
            if projektrot is not None:
                ren_rot = projektrot.resolve()
                if ren.is_relative_to(ren_rot):
                    rel = ren.relative_to(ren_rot)
                    if len(rel.parts) >= 2:
                        return self._projekt_id_i_mapp(ren_rot / rel.parts[0])
                    return None
            demomapp = getattr(self.fragor, "demomapp", None)
            if demomapp is not None and ren.is_relative_to(demomapp.resolve()):
                return PROJEKT_ID
            kv_fragedir = self.kv.fragedir.resolve()
            if ren.is_relative_to(kv_fragedir):
                return PROJEKT_ID
        demo = self.fragor.demo()
        delar = demo.get("delar", {}) if demo.get("finns") else {}
        if isinstance(delar, Mapping) and ref in delar:
            return PROJEKT_ID
        return None

    def konto_far_se_fraga(
        self,
        konto_id: str | None,
        ref: str,
        fil: Path | None = None,
        *,
        minst: str = "lasare",
        behorighet: Callable[[str, str, str], bool] | None = None,
    ) -> bool:
        """Får kontot läsa/planera/köra frågan `ref`? Inloggning av: alltid. På: minst `minst` i
        frågans ägarprojekt; fristående frågor utanför projekt bara för superadmin."""
        if behorighet is None and not self.app.konto.aktiv:
            return True
        if konto_id is None:
            return False
        agarprojekt = self.fraga_projekt(ref, fil)
        if agarprojekt is None:
            return bool(self.app.konto.ar_superadmin(konto_id))
        if behorighet is not None:
            return bool(behorighet(konto_id, agarprojekt, minst))
        return bool(self.app.konto.har_roll(konto_id, agarprojekt, minst))

    def kontrollera_fraga_oppenhet(self, ref: str, fil: Path | None = None) -> None:
        """K38: kastar `OppenhetNekad` om frågefilen, ägarprojektet eller någon källa inte är öppen."""
        if self.oppenhet is None:
            return
        if fil is None:
            fil = self.fragefil_eller_none(ref)
        if fil is None:
            return
        self.oppenhet.kontrollera_sokvag(fil)
        agarprojekt = self.fraga_projekt(ref, fil)
        if agarprojekt is not None:
            pmapp = self.projektmapp(agarprojekt)
            if pmapp is not None:
                self.oppenhet.kontrollera_sokvag(pmapp)
        try:
            fraga = las_fraga(fil)
        except FragaFel:
            return
        for s in fraga.scenarier.values():
            for p in (s.bas, *s.k9, *s.lappar):
                self.oppenhet.kontrollera_sokvag(p)
        for a in fraga.analyser:
            for p in a.indata.values():
                self.oppenhet.kontrollera_sokvag(p)

    def fraga_oppen(self, ref: str, fil: Path | None = None) -> bool:
        """Är frågan och alla dess källor öppna enligt K38?"""
        if self.oppenhet is None:
            return True
        from floden_mcp.oppenhet import OppenhetNekad

        try:
            self.kontrollera_fraga_oppenhet(ref, fil)
        except OppenhetNekad:
            return False
        return True

    def _kravs_oppen_fraga(self, ref: str) -> None:
        if self.oppenhet is None:
            return
        from floden_mcp.oppenhet import OppenhetNekad

        try:
            self.kontrollera_fraga_oppenhet(ref)
        except OppenhetNekad as exc:
            raise FlodenFel(str(exc), "nekad_oppenhet") from exc

    def fragor_lista(
        self,
        konto_id: str | None = None,
        *,
        behorighet: Callable[[str, str, str], bool] | None = None,
    ) -> dict[str, Any]:
        with _oversatt():
            filer = self.fragor._fragefiler()  # noqa: SLF001
            d = self.fragor.lista_fragor()
        ut = []
        dolda_icke_oppna = 0
        for f in d["fragor"]:
            ref = str(f["ref"])
            fil = filer.get(ref)
            if not self.konto_far_se_fraga(konto_id, ref, fil, minst="lasare", behorighet=behorighet):
                continue
            if not self.fraga_oppen(ref, fil):
                dolda_icke_oppna += 1
                continue
            ut.append(
                {
                    "ref": ref,
                    "fraga_id": f["fraga_id"],
                    "projekt": self.fraga_projekt(ref, fil),
                    "beskrivning": f["beskrivning"],
                    "giltig": f["giltig"],
                    "fel": f.get("fel"),
                    "i_demon": f.get("i_demon", False),
                    "scenarier": [s["id"] for s in f.get("scenarier", [])],
                    "analyser": [
                        {"id": a["id"], "losare": a["losare"], "namn": a["visningsnamn"]}
                        for a in f.get("analyser", [])
                    ],
                    "jamforelser": f.get("jamforelser", []),
                    "indata_saknas": f.get("indata_saknas", []),
                }
            )
        svar: dict[str, Any] = {
            "kontrakt": "K20/v1",
            "fragor": ut,
            "tips": "Frågor med indata_saknas kan inte köras på den här datorn.",
        }
        if dolda_icke_oppna:
            svar["dolda_icke_oppna"] = dolda_icke_oppna
        return svar

    def korningar_lista(
        self, max_antal: int = 20, konto_id: str | None = None
    ) -> dict[str, Any]:
        """Körningarna anroparen får se (med inloggning på: `FlodenApiApp.korningar_for_konto`)."""
        with _oversatt():
            alla = self.app.korningar_for_konto(konto_id)["korningar"]
        max_antal = max(1, min(int(max_antal), 100))
        return {
            "antal": len(alla),
            "korningar": [
                {
                    k: s[k]
                    for k in (
                        "korning_id",
                        "ref",
                        "fraga_id",
                        "status",
                        "skapad",
                        "start",
                        "slut",
                        "fel",
                        "jobb_totalt",
                        "framsteg",
                    )
                }
                for s in alla[:max_antal]
            ],
            "trunkerad": len(alla) > max_antal,
        }

    def korning_status(self, korning_id: str) -> dict[str, Any]:
        with _oversatt():
            s = self.fragor.korning_status(korning_id)
        return {k: s[k] for k in s if k != "fel_forklaring"} | {
            "fel_forklaring": s.get("fel_forklaring")
        }

    # ------------------------------------------------------------------ läsa: resultat

    def _resultat(self, korning_id: str) -> dict[str, Any]:
        with _oversatt():
            r: dict[str, Any] = self.fragor.resultat(korning_id)
        return r

    @staticmethod
    def _jobbfil(analys: str, scenario: str, ruta: str | None) -> str:
        bas = f"{analys}/{scenario}" + (f"/{ruta}" if ruta else "")
        return f"{bas}/run.json"

    def resultat_sammanfattning(
        self,
        korning_id: str,
        analys: str | None = None,
        scenario: str | None = None,
        ruta: str | None = None,
        max_rader: int = 40,
    ) -> dict[str, Any]:
        res = self._resultat(korning_id)
        max_rader = max(1, min(int(max_rader), 500))
        enheter_per_lager = {
            (la["analys"], la["roll"]): la["enhet"] for la in res["lager"] if la["enhet"]
        }
        tabeller: list[dict[str, Any]] = []
        rader_totalt = 0
        kvar = max_rader
        for t in res["tabeller"]:
            if analys is not None and t["analys"] != analys:
                continue
            rader: list[dict[str, Any]] = []
            for rad in t["rader"]:
                if scenario is not None and rad["scenario"] != scenario:
                    continue
                if ruta is not None and rad["ruta"] != ruta:
                    continue
                rader_totalt += 1
                if kvar <= 0:
                    continue
                kvar -= 1
                skal = {
                    n: v
                    for n, v in rad["varden"].items()
                    if isinstance(v, int | float | bool | str | type(None))
                    and not (isinstance(v, str) and len(v) > 240)
                }
                rader.append(
                    {
                        "scenario": rad["scenario"],
                        "ruta": rad["ruta"],
                        "fran_cache": rad["fran_cache"],
                        "kortid_s": rad["kortid_s"],
                        "varden": skal,
                        "ovriga_matvarden": sorted(set(rad["varden"]) - set(skal)),
                        "belagg": belagg(
                            korning_id,
                            self._jobbfil(t["analys"], rad["scenario"], rad["ruta"]),
                            "matvarden",
                            None,
                            mottagare=f"{t['analys']}/{rad['scenario']}"
                            + (f"/{rad['ruta']}" if rad["ruta"] else ""),
                        ),
                    }
                )
            if rader:
                tabeller.append(
                    {
                        "analys": t["analys"],
                        "losare": t["losare"],
                        "metod": self.metodstatus(t["losare"])["status"],
                        "enheter": {
                            n: enhet_for(n) for n in t["kolumner"] if enhet_for(n) is not None
                        },
                        "rader": rader,
                    }
                )
        jamf = self._jamforelser(korning_id, res)
        if analys is not None:
            jamf = [j for j in jamf if j["analys"] == analys]
        return {
            "korning": {
                k: res["korning"][k]
                for k in ("korning_id", "ref", "fraga_id", "status", "slut", "jobb_totalt")
            },
            "beskrivning": res["beskrivning"],
            "scenarier": res["scenarier"],
            "jamforelser": jamf,
            "matvarden": tabeller,
            "rader_totalt": rader_totalt,
            "trunkerad": rader_totalt > max_rader,
            "enheter_rastren": {f"{a}.{r}": e for (a, r), e in sorted(enheter_per_lager.items())},
            "varningar": [
                {"text": v["text"], "kalla": v["kalla"], "antal_jobb": v["antal_jobb"]}
                for v in res["varningar"]
            ],
            "antaganden": res["antaganden"],
            "regel": (
                "Alla tal i matvarden och jamforelser är Flodens egna; rad- eller värdebeläggen "
                "pekar ut filen i körningens resultatmapp. Citera tal oförändrade."
            ),
        }

    def _jamforelser(self, korning_id: str, res: Mapping[str, Any]) -> list[dict[str, Any]]:
        ut: list[dict[str, Any]] = []
        skillnadslager = {
            la["id"].removeprefix("skillnad."): la for la in res["lager"] if la["typ"] == "skillnad"
        }
        for j in res["jamforelser"]:
            la = skillnadslager.get(j["namn"])
            enhet = la["enhet"] if la else None
            diff_fil = f"jamforelser/{j['namn']}/diff.tif"
            fil = (
                diff_fil
                if la and any(g["ruta"] is None for g in la["geotiff"])
                else (f"jamforelser/{j['namn']}/")
            )
            avgransad = self.fragor.projekt_avgransning(korning_id) is not None
            mott = f"{j['efter']} − {j['fore']} ({'inom projektets polygon' if avgransad else 'hela området'})"

            # Byggnadernas tak är inte mark: samma funktion och samma takmask som webbens
            # projektsida (`FragaTjanst.marksiffror`). Kärnans egen jämförelse räknar alla celler.
            mark = self.fragor.marksiffror(korning_id, la, float(j["troskel"])) if la else None
            tak_maskat = (
                la is not None
                and (
                    self.fragor._hamta(korning_id).mapp / "webb" / f"{la['id']}.tak.npy"  # noqa: SLF001
                ).is_file()
            )

            def v(
                namn: str,
                e: str | None,
                _j: Mapping[str, Any] = j,
                _m: str = mott,
                _mark: Mapping[str, float] | None = mark,
                _fil: str = fil,
                _avgr: bool = avgransad,
            ) -> Any:
                if _mark is not None:
                    return varde_med_belagg(
                        _mark[namn],
                        belagg(
                            korning_id,
                            _fil,
                            f"{_j['utdata']}:{namn}:mark",
                            e,
                            mottagare=f"jamforelser/{_j['namn']}: {_j['efter']} − {_j['fore']} "
                            + (
                                "(mark inom projektets polygon; byggnadernas tak undantagna)"
                                if _avgr
                                else "(mark; byggnadernas tak undantagna)"
                            ),
                        ),
                    )
                return varde_med_belagg(
                    _j[namn],
                    belagg(
                        korning_id,
                        "sammanstallning.json",
                        f"{_j['utdata']}:{namn}",
                        e,
                        mottagare=f"jamforelser/{_j['namn']}: {_m}",
                    ),
                )

            def karnans(namn: str, e: str | None, _j: Mapping[str, Any] = j) -> Any:
                return varde_med_belagg(
                    _j[namn],
                    belagg(
                        korning_id,
                        "sammanstallning.json",
                        f"{_j['utdata']}:{namn}",
                        e,
                        mottagare=f"jamforelser/{_j['namn']}: alla celler, tak inräknade",
                    ),
                )

            post: dict[str, Any] = {
                "namn": j["namn"],
                "analys": j["analys"],
                "utdata": j["utdata"],
                "fore": j["fore"],
                "efter": j["efter"],
                "betydelse": f"skillnad = {j['efter']} minus {j['fore']}",
                "yta_mark_enbart": mark is not None,
                "tak_maskat": tak_maskat,
                "avgransad_till_projektets_polygon": avgransad and mark is not None,
                "troskel": j["troskel"],
                "max_okning": v("max_okning", enhet),
                "max_minskning": v("max_minskning", enhet),
                "yta_okning_m2": v("yta_okning_m2", "m²"),
                "yta_minskning_m2": v("yta_minskning_m2", "m²"),
                "antal_rutor": j["rutor"],
                "rasterfil": fil,
            }
            if mark is not None:
                post["anmarkning"] = (
                    "Talen gäller mark: byggnadernas tak är undantagna (ett nytt hus är inte "
                    "ändrad mark). Kärnans omaskade tal, med tak, står i karnans_omaskade."
                    + (
                        " Projektet har en polygon: bara celler vars mittpunkt är inne i den räknas "
                        "(kärnans omaskade tal gäller hela beräkningsrutan)."
                        if avgransad
                        else ""
                    )
                )
                post["karnans_omaskade"] = {
                    n: karnans(n, "m²" if n.startswith("yta") else enhet)
                    for n in ("max_okning", "max_minskning", "yta_okning_m2", "yta_minskning_m2")
                }
            else:
                post["anmarkning"] = (
                    "Inget markraster fanns för jämförelsen; talen är kärnans (alla celler)."
                )
            ut.append(post)
        return ut

    def jamfor(
        self, korning_id: str, omrade: Sequence[Sequence[float]] | None = None
    ) -> dict[str, Any]:
        """Körningens egna jämförelser (före/efter), valfritt också inom en polygon."""
        res = self._resultat(korning_id)
        jamf = self._jamforelser(korning_id, res)
        if not jamf:
            raise FlodenFel(
                "Körningen har inga jämförelser. Jämförelser (före/efter) anges i frågan "
                "([[jamforelser]] i K20); skapa en fråga med `fraga_skapa` eller kör ett scenario.",
                "saknas",
            )
        inom: list[dict[str, Any]] = []
        if omrade is not None:
            ring = _ring(omrade, "omrade")
            for la in res["lager"]:
                if la["typ"] != "skillnad":
                    continue
                with _oversatt():
                    st = self.fragor.omradesstatistik(korning_id, la["id"], ring)
                inom.append(self._statistik_med_belagg(korning_id, la, st, "polygon"))
        return {
            "korning_id": korning_id,
            "jamforelser": jamf,
            "inom_omrade": inom or None,
            "regel": "Jämförelserna är registrerade i frågan och räknade av Floden; "
            "andra par kräver en ny fråga (fraga_skapa).",
        }

    # ------------------------------------------------------------------ avläsning

    def _lager_for(
        self, res: Mapping[str, Any], analys: str | None, roll: str | None
    ) -> list[dict[str, Any]]:
        lager: list[dict[str, Any]] = res["lager"]
        if analys is None:
            valbara = sorted({f"{la['analys']}.{la['roll']}" for la in lager})
            raise FlodenFel(
                "Ange analys (och vid behov roll). Valbara i körningen: " + ", ".join(valbara),
                "fel_indata",
            )
        traffar = [la for la in lager if la["analys"] == analys]
        if not traffar:
            analyser = sorted({la["analys"] for la in lager})
            raise FlodenFel(
                f"Analysen '{analys}' finns inte i körningen. Analyser: {', '.join(analyser)}.",
                "okand",
            )
        if roll is None:
            roll = next((la["roll"] for la in traffar if la["primar"]), traffar[0]["roll"])
        valda = [la for la in traffar if la["roll"] == roll]
        if not valda:
            roller = sorted({la["roll"] for la in traffar})
            raise FlodenFel(
                f"Rollen '{roll}' finns inte för analysen '{analys}'. Roller: {', '.join(roller)}.",
                "okand",
            )
        ordning = {"bas": 0, "scenario": 1, "skillnad": 2}
        return sorted(valda, key=lambda la: (ordning[la["typ"]], la["id"]))

    @staticmethod
    def _typtext(la: Mapping[str, Any]) -> str:
        return {"bas": "bas", "scenario": "scenario", "skillnad": "skillnad"}[la["typ"]]

    def _statistik_med_belagg(
        self, korning_id: str, la: Mapping[str, Any], st: Mapping[str, Any], vad: str
    ) -> dict[str, Any]:
        fil = (
            f"jamforelser/{la['id'].removeprefix('skillnad.')}/"
            if la["typ"] == "skillnad"
            else (f"{la['analys']}/{la['scenario']}/")
        )
        mott = (
            f"{vad}; {la['scenario']}"
            if la["typ"] != "skillnad"
            else (f"{vad}; {la['efter']} − {la['fore']}")
        )

        def v(namn: str, e: str | None) -> Any:
            if st.get(namn) is None:
                return None
            return varde_med_belagg(
                st[namn],
                belagg(korning_id, fil + "*.tif", f"{la['roll']}:{namn}", e, mottagare=mott),
            )

        ut: dict[str, Any] = {
            "lager_id": la["id"],
            "typ": self._typtext(la),
            "scenario": la["scenario"],
            "antal_celler_med_varde": st["antal_celler_med_varde"],
            "antal_celler_tak_uteslutna": st["antal_celler_tak_uteslutna"],
            "cellstorlek_m": st["cellstorlek_m"],
            "min": v("min", la["enhet"] or None),
            "medel": v("medel", la["enhet"] or None),
            "median": v("median", la["enhet"] or None),
            "max": v("max", la["enhet"] or None),
        }
        if la["typ"] == "skillnad":
            ut["troskel"] = st.get("troskel")
            ut["yta_okning_m2"] = v("yta_okning_m2", "m²")
            ut["yta_minskning_m2"] = v("yta_minskning_m2", "m²")
        return ut

    def resultat_varde(
        self,
        korning_id: str,
        analys: str | None = None,
        roll: str | None = None,
        e: float | None = None,
        n: float | None = None,
        omrade: Sequence[Sequence[float]] | None = None,
    ) -> dict[str, Any]:
        """Värdet i en punkt (e, n) eller statistik i en polygon, för bas, scenario och skillnad."""
        if (e is None) != (n is None):
            raise FlodenFel("Ange både e och n för en punkt.", "fel_indata")
        if (e is None) == (omrade is None):
            raise FlodenFel(
                "Ange antingen en punkt (e och n) eller en polygon (omrade).", "fel_indata"
            )
        res = self._resultat(korning_id)
        lager = self._lager_for(res, analys, roll)
        if e is not None and n is not None:
            if not (math.isfinite(e) and math.isfinite(n)):
                raise FlodenFel("e och n måste vara ändliga tal.", "fel_indata")
            with _oversatt():
                av = self.fragor.avlasning(korning_id, lager[0]["id"], float(e), float(n))
            varden: list[dict[str, Any]] = []
            for rad in av["varden"]:
                la = next(x for x in lager if x["id"] == rad["lager_id"])
                post: dict[str, Any] = {
                    "lager_id": rad["lager_id"],
                    "typ": self._typtext(la),
                    "scenario": rad["scenario"],
                    "inom_lagrets_utbredning": rad["inom"],
                    "ar_tak": rad["tak"],
                }
                if la["typ"] == "skillnad":
                    post["betydelse"] = f"{la['efter']} minus {la['fore']}"
                k = rad.get("kalla")
                if rad["inom"] and k is not None and rad["varde"] is not None:
                    vb = varde_med_belagg(
                        rad["varde"],
                        belagg(
                            korning_id,
                            k["fil"],
                            la["roll"],
                            rad["enhet"] or None,
                            mottagare=f"{rad['scenario']}" + (f"/{k['ruta']}" if k["ruta"] else ""),
                            cell=k["cell"],
                            fil_sha256=k["fil_sha256"],
                        ),
                    )
                    if rad["tak"]:
                        # Cellen är en byggnads tak (samma takmask som kartan): inget markvärde.
                        post["varde"] = None
                        post["tak_varde"] = vb
                        post["orsak"] = (
                            "Cellen är en byggnads tak, inte mark. Värdet står i tak_varde "
                            "och ska inte läsas som markvärde."
                        )
                    else:
                        post["varde"] = vb
                else:
                    post["varde"] = None
                    post["orsak"] = (
                        "Punkten ligger utanför lagrets utsträckning."
                        if not rad["inom"]
                        else "Cellen saknar värde."
                    )
                varden.append(post)
            return {
                "korning_id": korning_id,
                "analys": av["analys"],
                "roll": av["roll"],
                "punkt": [av["e"], av["n"]],
                "varden": varden,
                "anmarkning": "ar_tak: cellen är en byggnads tak (inte mark). Då ges inget "
                "markvärde (varde = null); Flodens värde för cellen står i tak_varde.",
            }
        ring = _ring(omrade, "omrade")
        statistik = []
        for la in lager:
            with _oversatt():
                st = self.fragor.omradesstatistik(korning_id, la["id"], ring)
            statistik.append(self._statistik_med_belagg(korning_id, la, st, "polygon"))
        return {
            "korning_id": korning_id,
            "analys": lager[0]["analys"],
            "roll": lager[0]["roll"],
            "omrade": ring,
            "statistik": statistik,
            "anmarkning": "Celler vars mittpunkt ligger i polygonen; byggnadernas tak räknas inte "
            "som mark (antalet uteslutna celler redovisas).",
        }

    # ------------------------------------------------------------------ karta

    def _projekt_avgransning_bbox(self, korning_id: str) -> tuple[float, float, float, float] | None:
        """Projektets uttryckliga `avgransning_3011` (`[e0, n0, e1, n1]`), eller `None`."""
        projekt_id = self.app.projekt_for_korning(korning_id)
        if not projekt_id or projekt_id == PROJEKT_ID:
            return None
        pmapp = self.projektmapp(projekt_id)
        if pmapp is not None:
            pfil = pmapp / "projekt.json"
            if pfil.is_file():
                try:
                    d = json.loads(pfil.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    d = None
                raw = d.get("avgransning_3011") if isinstance(d, dict) else None
                if isinstance(raw, Sequence) and not isinstance(raw, str) and len(raw) == 4:
                    try:
                        x0, y0, x1, y1 = (float(v) for v in raw)
                    except (TypeError, ValueError):
                        pass
                    else:
                        if all(math.isfinite(v) for v in (x0, y0, x1, y1)) and x1 > x0 and y1 > y0:
                            return (x0, y0, x1, y1)
        try:
            kalla = self.kv.kalla(projekt_id)
        except (FlodenWebbFel, OSError, KeyError, ValueError):
            return None
        avgr = getattr(kalla, "avgransning", None)
        if isinstance(avgr, Sequence) and len(avgr) == 4:
            try:
                x0, y0, x1, y1 = (float(v) for v in avgr)
            except (TypeError, ValueError):
                return None
            if all(math.isfinite(v) for v in (x0, y0, x1, y1)) and x1 > x0 and y1 > y0:
                return (x0, y0, x1, y1)
        return None

    def _k1_rutor_for_korning(self, korning_id: str, la: Mapping[str, Any]) -> list[Path]:
        """Hämtar K1-rutor med `markslag.tif` för en körning (för vattenmask `markslag == 8`)."""
        sedda: list[Path] = []
        sett: set[Path] = set()

        def lagg_till(mapp: Path) -> None:
            ren = mapp.resolve()
            if ren not in sett and (mapp / "markslag.tif").is_file():
                sett.add(ren)
                sedda.append(mapp)

        try:
            k = self.fragor._hamta(korning_id)  # noqa: SLF001
            sam_fil = k.utmapp / "sammanstallning.json"
            if sam_fil.is_file():
                sam = json.loads(sam_fil.read_text(encoding="utf-8"))
                rutor_per_scen, _ = self.fragor._scenariorutor(k, sam)  # noqa: SLF001
                if rutor_per_scen:
                    sids = (
                        [str(la.get("fore") or ""), str(la.get("efter") or "")]
                        if la.get("typ") == "skillnad"
                        else [str(la.get("scenario") or "")]
                    )
                    for sid in sids:
                        for mapp in rutor_per_scen.get(sid, {}).values():
                            lagg_till(Path(mapp))
                    if not sedda:
                        for rutmap in rutor_per_scen.values():
                            for mapp in rutmap.values():
                                lagg_till(Path(mapp))
            if not sedda:
                fraga_fil = self.fragor._fil_for_korning(k)  # noqa: SLF001
                fraga = las_fraga(fraga_fil)
                for spec in fraga.scenarier.values():
                    bas = Path(spec.bas)
                    if (bas / "markslag.tif").is_file():
                        lagg_till(bas)
                    elif bas.is_dir():
                        for barn in sorted(bas.iterdir()):
                            if barn.is_dir():
                                lagg_till(barn)
        except Exception:  # noqa: BLE001 - valfri visuell mask; faller tillbaka nedan
            pass

        if not sedda:
            projekt_id = self.app.projekt_for_korning(korning_id)
            if projekt_id:
                try:
                    mallar = self.kv.kalla(projekt_id).mallar
                    for mall in mallar.values():
                        for ruta in mall.rutor:
                            lagg_till(Path(ruta.mapp))
                except Exception:  # noqa: BLE001 - valfri visuell mask
                    pass
        return sedda

    def _vatten_mask_for_lager(
        self,
        korning_id: str,
        la: Mapping[str, Any],
        raster_shape: tuple[int, int],
    ) -> NDArray[np.bool_] | None:
        """Bygger boolesk mask för permanent vatten (`markslag == 8`) i lagrets cellrutnät."""
        from testsvit.k1_io import las_geotiff

        rutor = self._k1_rutor_for_korning(korning_id, la)
        if not rutor:
            return None
        u = la["utbredning"]
        px = float(la["pixelstorlek_m"])
        x_min, y_max = float(u["x_min"]), float(u["y_max"])
        h, b = raster_shape
        ut = np.zeros((h, b), dtype=np.bool_)
        for mapp in rutor:
            fil = mapp / "markslag.tif"
            if not fil.is_file():
                continue
            try:
                arr, geo = las_geotiff(fil)
            except (OSError, ValueError):
                continue
            vatten = np.asarray(arr) == MARKSLAG_VATTEN
            if not bool(vatten.any()):
                continue
            f = px / float(geo.pixelstorlek_x)
            if f >= 1.0 - 1e-9 and abs(f - round(f)) <= 1e-6:
                fi = round(f)
                if fi > 1:
                    hh, bb = (vatten.shape[0] // fi) * fi, (vatten.shape[1] // fi) * fi
                    vatten = vatten[:hh, :bb].reshape(hh // fi, fi, bb // fi, fi).any(axis=(1, 3))
            elif f > 0.0 and abs((1.0 / f) - round(1.0 / f)) <= 1e-6:
                up = round(1.0 / f)
                vatten = np.repeat(np.repeat(vatten, up, axis=0), up, axis=1)
            else:
                continue
            c0f = (float(geo.x_min) - x_min) / px
            r0f = (y_max - float(geo.y_max)) / px
            if abs(c0f - round(c0f)) > 1e-3 or abs(r0f - round(r0f)) > 1e-3:
                continue
            c0, r0 = round(c0f), round(r0f)
            rs, cs = max(0, r0), max(0, c0)
            re, ce = min(h, r0 + vatten.shape[0]), min(b, c0 + vatten.shape[1])
            if re <= rs or ce <= cs:
                continue
            ut[rs:re, cs:ce] |= vatten[rs - r0 : re - r0, cs - c0 : ce - c0]
        return ut if bool(ut.any()) else None

    def karta(
        self,
        korning_id: str,
        analys: str,
        typ: str = "skillnad",
        scenario: str | None = None,
        roll: str | None = None,
        utsnitt: Sequence[float] | None = None,
        max_px: int = 900,
        rubrik: str | None = None,
    ) -> tuple[bytes, dict[str, Any]]:
        """Kartan som PNG. `rubrik` ersätter bildens förvalda rubrik (rapporternas klarspråk)."""
        res = self._resultat(korning_id)
        lager = self._lager_for(res, analys, roll)
        typ = typ.strip().lower()
        if typ not in ("bas", "scenario", "skillnad"):
            raise FlodenFel("typ ska vara 'bas', 'scenario' eller 'skillnad'.", "fel_indata")
        kandidater = [la for la in lager if la["typ"] == typ]
        if scenario is not None:
            kandidater = [la for la in kandidater if la["scenario"] == scenario]
        if not kandidater:
            finns = ", ".join(f"{la['typ']}:{la['scenario']}" for la in lager)
            raise FlodenFel(f"Inget sådant lager. Tillgängligt: {finns}.", "okand")
        la = kandidater[0]
        if utsnitt is not None:
            omr: tuple[float, float, float, float] | None = _rektangel(utsnitt, "utsnitt")
        else:
            avgr = self._projekt_avgransning_bbox(korning_id)
            omr = (
                _standard_utsnitt_for_projekt(avgr, la["utbredning"])
                if avgr is not None
                else None
            )
        with _oversatt():
            data = self.fragor.lager_raster(korning_id, la["id"])
        losare = next((t["losare"] for t in res["tabeller"] if t["analys"] == analys), "")
        byggnader = None
        if res.get("byggnader"):
            sc = la["efter"] if la["typ"] == "skillnad" else la["scenario"]
            byggnader = res["byggnader"]["scenarier"].get(sc)
        status = self.metodstatus(losare) if losare else {"status": "skiss"}
        ar_skyfall = (
            str(la.get("doman") or "") == "vatten"
            or losare.startswith("vatten.")
            or str(la.get("roll") or "") == "max_djup"
        )
        vatten_mask = (
            self._vatten_mask_for_lager(
                korning_id,
                la,
                (int(data["raster"].shape[0]), int(data["raster"].shape[1])),
            )
            if ar_skyfall
            else None
        )
        if vatten_mask is not None:
            png, meta = _rendera_karta_med_vatten(
                raster=data["raster"],
                tak=data["tak"],
                vatten_mask=vatten_mask,
                lager=la,
                byggnader=byggnader,
                korning_id=korning_id,
                kodversion=self.kodversion,
                skiss=status["status"] != "rapport",
                utsnitt=omr,
                max_px=max(300, min(int(max_px), 2000)),
                rubrik=rubrik,
            )
        else:
            png, meta = rendera_karta(
                raster=data["raster"],
                tak=data["tak"],
                lager=la,
                byggnader=byggnader,
                korning_id=korning_id,
                kodversion=self.kodversion,
                skiss=status["status"] != "rapport",
                utsnitt=omr,
                max_px=max(300, min(int(max_px), 2000)),
                rubrik=rubrik,
            )
        sha = hashlib.sha256(png).hexdigest()
        mapp = self.mcp_mapp / "kartor"
        mapp.mkdir(parents=True, exist_ok=True)
        fil = mapp / f"{korning_id}_{la['id'].replace('.', '_')}_{sha[:12]}.png"
        fil.write_bytes(png)
        return png, {
            **meta,
            "korning_id": korning_id,
            "lager_id": la["id"],
            "analys": analys,
            "roll": la["roll"],
            "typ": la["typ"],
            "scenario": la["scenario"],
            "metodstatus": status["status"],
            "png_sha256": sha,
            "sparad_som": str(fil),
            "belagg": belagg(
                korning_id,
                (
                    f"jamforelser/{la['id'].removeprefix('skillnad.')}/"
                    if la["typ"] == "skillnad"
                    else f"{la['analys']}/{la['scenario']}/"
                )
                + "*.tif",
                la["roll"],
                la["enhet"] or None,
                mottagare="kartbild",
            ),
        }

    # ------------------------------------------------------------------ metoder

    def metoder_lista(self) -> dict[str, Any]:
        ut = []
        for los in lista_losare():
            info = losarinfo(los)
            ut.append(
                {
                    "namn": info["namn"],
                    "visningsnamn": visningsnamn(info["namn"]),
                    "doman": info["doman"],
                    "version": info["version"],
                    "beskrivning": info["beskrivning"],
                    "indataroller": info["indataroller"],
                    "utdataroller": info["utdataroller"],
                    "paverkansradie_m": info["radie_m"],
                    "validering": self.metodstatus(info["namn"]),
                }
            )
        return {
            "metoder": ut,
            "forklaring": (
                "validering.status är 'skiss' eller 'rapport'. 'rapport' sätts bara av en människa i "
                "data/mcp/metodstatus.toml; ingen metod är klassad som rapport än."
            ),
        }

    # ------------------------------------------------------------------ planera och köra

    @staticmethod
    def _hus_till_andring(i: int, h: Mapping[str, Any]) -> dict[str, Any]:
        namn = f"Hus {i + 1}"
        if (h.get("polygon") is None) == (h.get("rektangel") is None):
            raise FlodenFel(f"{namn}: ange antingen polygon eller rektangel.", "fel_indata")
        if h.get("polygon") is not None:
            ring = _ring(h["polygon"], namn)
        else:
            x0, y0, x1, y1 = _rektangel(h["rektangel"], namn)
            ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        if "hojd_m" not in h:
            raise FlodenFel(f"{namn}: ange hojd_m (höjd över mark i meter).", "fel_indata")
        egenskaper: dict[str, Any] = {"hojd_m": h["hojd_m"]}
        for valfri in ("takform", "andamal", "vaningar"):
            if h.get(valfri) is not None:
                egenskaper[valfri] = h[valfri]
        return {
            "typ": "byggnad_ny",
            "andring_id": str(h.get("id") or f"hus{i + 1}"),
            "geometri": {"type": "Polygon", "coordinates": [[*ring, ring[0]]]},
            "egenskaper": egenskaper,
        }

    def scenario_skapa(
        self,
        projekt: str,
        namn: str,
        hus: Sequence[Mapping[str, Any]] | None = None,
        rivna: Sequence[str] | None = None,
        hojdandringar: Sequence[Mapping[str, Any]] | None = None,
        andringar: Sequence[Mapping[str, Any]] | None = None,
        beskrivning: str = "",
        bara_kontroll: bool = False,
    ) -> dict[str, Any]:
        """Bygger ett K9-planscenario (kontrollerat av kärnan) och sparar det som lokal fil."""
        self._kravs_projekt(projekt)
        lista: list[dict[str, Any]] = []
        for i, h in enumerate(hus or []):
            lista.append(self._hus_till_andring(i, h))
        for bid in rivna or []:
            lista.append(
                {
                    "typ": "byggnad_bort",
                    "andring_id": f"bort_{bid}",
                    "geometri": None,
                    "egenskaper": {"byggnads_id": str(bid)},
                }
            )
        for i, ha in enumerate(hojdandringar or []):
            if "byggnads_id" not in ha or "hojd_m" not in ha:
                raise FlodenFel(f"Höjdändring {i + 1}: ange byggnads_id och hojd_m.", "fel_indata")
            lista.append(
                {
                    "typ": "byggnad_hojd",
                    "andring_id": f"hojd_{ha['byggnads_id']}",
                    "geometri": None,
                    "egenskaper": {"byggnads_id": str(ha["byggnads_id"]), "hojd_m": ha["hojd_m"]},
                }
            )
        for a in andringar or []:
            lista.append(dict(a))
        if not lista:
            raise FlodenFel(
                "Scenariot har inga ändringar. Ange hus, rivna, hojdandringar eller andringar (K9).",
                "fel_indata",
            )
        with _oversatt():
            v = self.kv.validera(projekt, {"andringar": lista})
        hus_ut = [
            {
                "andring_id": x["andring_id"],
                "hojd_m": x["hojd_m"],
                "yta_m2": x["yta_m2"],
                "uppskattad_bta_m2": x["bta_m2"],
                "vaningar_uppskattning": int(x["vaningar"]),
                "ar_uppskattning": True,
            }
            for x in v["hus"]
        ]
        ut: dict[str, Any] = {
            "projekt": projekt,
            "giltig": v["giltig"],
            "fel": v["fel"],
            "varningar": v["varningar"],
            "information": v["information"],
            "hus": hus_ut,
            "anmarkning_bta": "BTA och våningar är uppskattningar (yta x våningar vid 3 m "
            "våningshöjd), inte resultat från någon lösare.",
            "andringar_k9": v["andringar"],
            "sparat": False,
            "hojd_max_m": HOJD_MAX_M,
        }
        if not v["giltig"] or bara_kontroll:
            return ut
        namn = namn.strip()
        if not namn:
            raise FlodenFel("Ge scenariot ett namn.", "fel_indata")
        with _oversatt():
            fanns = {s["slug"] for s in self.kv.lista_scenarier(projekt)["scenarier"]}
            d = self.kv.spara_scenario(
                projekt, None, {"namn": namn, "beskrivning": beskrivning, "andringar": lista}
            )
        ut.update(
            sparat=True,
            scenario=d["slug"],
            namn=d["namn"],
            ersatte_befintligt=d["slug"] in fanns,
            antal_andringar=len(d["andringar"]),
        )
        return ut

    def _tillatna_rotter(self) -> list[Path]:
        roter = [self.kv.mapp, self.fragor.cachemapp]
        if self.fragor.demomapp is not None:
            roter.append(self.fragor.demomapp)
        return [r.resolve() for r in roter]

    def _kontrollera_sokvagar(self, fraga: Any) -> None:
        roter = self._tillatna_rotter()

        kv_rot = self.kv.mapp.resolve()
        kv_tillatna = {PROJEKT_ID, self.kv.fragedir.name}

        def ok(p: Path) -> bool:
            ren = p.resolve()
            if not any(ren.is_relative_to(r) for r in roter):
                return False
            if self.app.konto.aktiv and ren.is_relative_to(kv_rot):
                rel = ren.relative_to(kv_rot)
                if rel.parts and rel.parts[0] not in kv_tillatna:
                    return False
            return True

        if self.oppenhet is not None:
            from floden_mcp.oppenhet import OppenhetNekad

            try:
                for s in fraga.scenarier.values():
                    for p in (s.bas, *s.k9, *s.lappar):
                        self.oppenhet.kontrollera_sokvag(p)
                for a in fraga.analyser:
                    for p in a.indata.values():
                        self.oppenhet.kontrollera_sokvag(p)
            except OppenhetNekad as exc:
                raise FlodenFel(str(exc), "nekad_oppenhet") from exc

        for s in fraga.scenarier.values():
            for p in (s.bas, *s.k9, *s.lappar):
                if not ok(p):
                    raise FlodenFel(
                        f"Frågan pekar på {p}, som ligger utanför de mappar MCP får läsa i den "
                        "här versionen (demokvarteret och kartverktygets mapp). Data från andra "
                        "projekt kräver datapolicy (R4).",
                        "nekad_sokvag",
                    )
        for a in fraga.analyser:
            for p in a.indata.values():
                if not ok(p):
                    raise FlodenFel(f"Indata {p} ligger utanför tillåtna mappar.", "nekad_sokvag")

    def fraga_skapa(self, fraga: Mapping[str, Any]) -> dict[str, Any]:
        """Validerar en K20-fråga med kärnan och sparar den i kartverktygets frågemapp."""
        if not isinstance(fraga, Mapping):
            raise FlodenFel("Frågan måste vara ett K20-objekt.", "fel_indata")
        fragedir: Path = self.kv.fragedir
        with _oversatt():
            k = fraga_fran_dict(dict(fraga), rot=fragedir)
            self._kontrollera_sokvagar(k)
            for a in k.analyser:  # lösarens egen validering av parametrarna, som vid körning
                from karna.analyslosare import hamta_losare

                hamta_losare(a.losare).validera_parametrar(a.parametrar)
        fil = fragedir / f"{k.fraga_id}.json"
        ref_stam = re.sub(r"[^a-z0-9_\-]+", "-", fil.stem.lower()).strip("-")
        for annan in self.fragor._fragefiler().values():  # noqa: SLF001
            if (
                annan.resolve() != fil.resolve()
                and re.sub(r"[^a-z0-9_\-]+", "-", annan.stem.lower()).strip("-") == ref_stam
            ):
                raise FlodenFel(
                    f"Det finns redan en fråga med namnet '{ref_stam}' ({annan.name}). "
                    "Välj ett annat fraga_id.",
                    "finns_redan",
                )
        fragedir.mkdir(parents=True, exist_ok=True)
        ersatte = fil.is_file()
        tmp = fil.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(dict(fraga), indent=2, ensure_ascii=False) + "\n", "utf-8")
        tmp.replace(fil)
        with _oversatt():
            ref = self.kv._ref_for(fil)  # noqa: SLF001
            beskr = self.fragor.hamta_fraga(ref)
        return {
            "ref": ref,
            "fraga_id": k.fraga_id,
            "giltig": beskr["giltig"],
            "ersatte_befintlig": ersatte,
            "scenarier": [s["id"] for s in beskr.get("scenarier", [])],
            "analyser": [a["id"] for a in beskr.get("analyser", [])],
            "indata_saknas": beskr.get("indata_saknas", []),
            "nasta_steg": "Kör korplan(fraga=ref) för att se vad som räknas, sedan kor(fraga=ref).",
        }

    def _valda_analyser(self, projekt: str, analyser: Sequence[str] | None) -> list[str]:
        mallar = self.kv._kravs_projekt(projekt)  # noqa: SLF001
        tillgangliga = [
            f"{kort}.{a.id}"
            for kort, m in mallar.items()
            for a in m.fraga.analyser
        ]
        if analyser:
            valda = [str(a) for a in analyser]
            giltiga = set(tillgangliga)
            okanda = [a for a in valda if a not in giltiga]
            if okanda:
                raise FlodenFel(
                    f"Okänd analys '{okanda[0]}'. Tillgängliga analyser i projektet: "
                    + ", ".join(tillgangliga)
                    + ".",
                    "fel_indata",
                )
            return valda
        return [a for a in tillgangliga if not a.endswith(".vsc")]

    @staticmethod
    def _plan_sammanfattning(plan: Mapping[str, Any], detalj: bool) -> dict[str, Any]:
        ut: dict[str, Any] = {
            "sammanfattning": plan["sammanfattning"],
            "per_analys": plan["per_analys"],
            "scenarier": [
                {"id": s["id"], "nyckel": s["nyckel"], "antal_rutor": s["antal_rutor"]}
                for s in plan["scenarier"]
            ],
            "utbredning": plan["utbredning"],
            "plan_nyckel": plan.get("plan_nyckel"),
        }
        if detalj:
            ut["jobb"] = plan["jobb"]
        return ut

    def korplan(
        self,
        projekt: str = PROJEKT_ID,
        scenario: str | None = None,
        fraga: str | None = None,
        analyser: Sequence[str] | None = None,
        avtappning_mm_h: float | None = None,
        detalj: bool = False,
        lage_idag: bool = False,
    ) -> dict[str, Any]:
        """Vad en körning skulle räkna om och vad som redan finns i cachen (K22). Kör inget."""
        _ett_av(scenario, fraga, lage_idag)
        if lage_idag:
            self._kravs_projekt(projekt)
            valda = self._valda_analyser(projekt, analyser)
            with _oversatt():
                p = self.kv.planera_lage_idag(projekt, valda, avtappning_mm_h)
            return {
                "projekt": projekt,
                "lage": "idag",
                "valda_analyser": valda,
                "fragor": [
                    {
                        "mall": f["mall"],
                        "ref": f["ref"],
                        "fraga_id": f["fraga_id"],
                        "analyser": f["analyser"],
                        "plan": self._plan_sammanfattning(f["plan"], detalj),
                        "uppskattad_tid_s": f["uppskattad_tid_s"],
                        "tid_text": f["tid_text"],
                    }
                    for f in p["fragor"]
                ],
                "uppskattad_tid_s": p["uppskattad_tid_s"],
                "regel": "Läget idag: bara scenariot fore, ingen jämförelse. Cache (K22): jobb med oförändrad nyckel räknas inte om.",
            }
        if fraga is not None:
            self._kravs_oppen_fraga(fraga)
            with _oversatt():
                plan = self.fragor.planera_fraga(fraga)
            return {
                "fraga": fraga,
                "fraga_id": plan["fraga_id"],
                "plan": self._plan_sammanfattning(plan, detalj),
                "paverkansradie_m": sorted(
                    {j["radie_m"] for j in plan["jobb"] if j["radie_m"] is not None}
                ),
                "uppskattad_tid_s": self._tid_for(plan),
                "regel": "Cache (K22): jobb med oförändrad nyckel räknas inte om.",
            }
        self._kravs_projekt(projekt)
        valda = self._valda_analyser(projekt, analyser)
        with _oversatt():
            p = self.kv.planera(projekt, str(scenario), valda, avtappning_mm_h)
        poster = []
        for f in p["fragor"]:
            poster.append(
                {
                    "mall": f["mall"],
                    "ref": f["ref"],
                    "fraga_id": f["fraga_id"],
                    "analyser": f["analyser"],
                    "k9_nyckel": f["k9_nyckel"],
                    "plan": self._plan_sammanfattning(f["plan"], detalj),
                    "paverkansradie_m": f["paverkansradie_m"],
                    "uppskattad_tid_s": f["uppskattad_tid_s"],
                    "tid_text": f["tid_text"],
                }
            )
        return {
            "projekt": projekt,
            "scenario": scenario,
            "valda_analyser": valda,
            "fragor": poster,
            "regel": "Cache (K22): jobb med oförändrad nyckel räknas inte om. "
            "Påverkansradien avgör vilka rutor ett ritat hus kräver omräkning för.",
        }

    def _tid_for(self, plan: Mapping[str, Any]) -> float | None:
        tider = self.kv._tid_per_losare()  # noqa: SLF001
        tot = 0.0
        for j in plan["jobb"]:
            if j["i_cache"]:
                continue
            t = tider.get(j["losare"])
            if t is None:
                return None
            tot += t
        return round(tot, 1)

    def kor(
        self,
        projekt: str | None = PROJEKT_ID,
        scenario: str | None = None,
        fraga: str | None = None,
        analyser: Sequence[str] | None = None,
        avtappning_mm_h: float | None = None,
        lage_idag: bool = False,
    ) -> dict[str, Any]:
        """Startar körningen i bakgrunden (som webben) och returnerar körnings-id direkt."""
        _ett_av(scenario, fraga, lage_idag)
        if lage_idag:
            pid = projekt or PROJEKT_ID
            self._kravs_projekt(pid)
            valda = self._valda_analyser(pid, analyser)
            with _oversatt():
                k = self.kv.kor_lage_idag(pid, valda, avtappning_mm_h)
            return {
                "projekt": pid,
                "lage": "idag",
                "korningar": [
                    self._start_post(f["korning"], mall=f["mall"]) for f in k["fragor"]
                ],
                "nasta_steg": "Anropa vanta med körnings-id, sedan resultat_sammanfattning (bara före; ingen jämförelse).",
            }
        if fraga is not None:
            self._kravs_oppen_fraga(fraga)
            agarprojekt = self.fraga_projekt(fraga)
            if self.app.konto.aktiv and agarprojekt is not None and projekt != agarprojekt:
                raise FlodenFel(
                    f"Frågan '{fraga}' hör till projektet '{agarprojekt}', inte '{projekt}'.",
                    "behorighet",
                )
            with _oversatt():
                svar = self.fragor.starta_korning(fraga, projekt)
            return {
                "korningar": [self._start_post(svar, fraga=fraga)],
                "nasta_steg": "Anropa vanta med körnings-id, sedan resultat_sammanfattning.",
            }
        pid = projekt or PROJEKT_ID
        self._kravs_projekt(pid)
        valda = self._valda_analyser(pid, analyser)
        with _oversatt():
            k = self.kv.kor(pid, str(scenario), valda, avtappning_mm_h)
        return {
            "projekt": pid,
            "scenario": scenario,
            "korningar": [
                self._start_post(f["korning"], mall=f["mall"], k9_nyckel=f["k9_nyckel"])
                for f in k["fragor"]
            ],
            "nasta_steg": "Anropa vanta med körnings-id, sedan resultat_sammanfattning och jamfor.",
        }

    @staticmethod
    def _start_post(svar: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
        return {
            "korning_id": svar["korning_id"],
            "status": svar["status"],
            "fraga_id": svar["fraga_id"],
            "jobb_totalt": svar["jobb_totalt"],
            "ateranvande_pagaende_korning": bool(svar.get("ateranvand")),
            **extra,
        }

    def vanta_pa(self, korning_ids: Sequence[str]) -> list[dict[str, Any]]:
        """Aktuell status för körningarna (ett anrop; verktyget `vanta` upprepar det)."""
        if not korning_ids:
            raise FlodenFel("Ange minst ett korning_id.", "fel_indata")
        if len(korning_ids) > MAX_KORNINGSID_PER_VANTA:
            raise FlodenFel(f"Högst {MAX_KORNINGSID_PER_VANTA} körningar åt gången.", "fel_indata")
        ut = []
        for kid in korning_ids:
            with _oversatt():
                s = self.fragor.korning_status(kid)
            ut.append(
                {
                    "korning_id": kid,
                    "status": s["status"],
                    "framsteg": s["framsteg"],
                    "fel": s["fel"],
                    "fel_forklaring": s.get("fel_forklaring"),
                }
            )
        return ut

    def vanta(
        self,
        korning_ids: Sequence[str],
        tidsgrans_s: float = 120.0,
        paus_s: float = 0.5,
        framsteg: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> dict[str, Any]:
        """Väntar tills alla körningar är klara eller felat, högst `tidsgrans_s` sekunder."""
        tidsgrans_s = max(0.0, min(float(tidsgrans_s), 600.0))
        slut = time.monotonic() + tidsgrans_s
        while True:
            st = self.vanta_pa(korning_ids)
            if framsteg is not None:
                framsteg(st)
            klara = all(s["status"] in KLARA for s in st)
            if klara or time.monotonic() >= slut:
                break
            time.sleep(paus_s)
        return {
            "alla_klara": klara,
            "tidsgrans_nadd": not klara,
            "korningar": st,
            "nasta_steg": (
                "resultat_sammanfattning, resultat_varde, jamfor och karta_bild."
                if klara and all(s["status"] == "klar" for s in st)
                else "Anropa vanta igen, eller läs fel i korningar."
            ),
        }
