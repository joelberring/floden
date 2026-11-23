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
from shapely.geometry import Polygon, box

from floden_mcp.belagg import belagg, enhet_for, varde_med_belagg
from floden_mcp.karta import rendera_karta
from gemensamt.hash import sha256_fil
from gemensamt.manifest import hamta_kodversion
from karna.analyslosare import LosarFel, lista_losare
from karna.geometri import GeometriFel, las_byggnader, las_terrang
from karna.ritat import HOJD_MAX_M, RitatFel
from karna.scenario import ScenarioFel
from karna.utredning import FragaFel, fraga_fran_dict
from webb.api import FlodenApiApp
from webb.fel import FlodenWebbFel
from webb.moduler import losarinfo, visningsnamn

PROJEKT_ID = "demokvarteret"
STANDARD_METODSTATUS = Path(__file__).resolve().parents[2] / "data" / "mcp" / "metodstatus.toml"
MAX_KORNINGSID_PER_VANTA = 20
KLARA = ("klar", "fel")
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
        self._byggnader: list[dict[str, Any]] | None = None
        self._kodversion: str | None = None

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
            self._kodversion = hamta_kodversion(self.rot)
        return self._kodversion

    def _kravs_projekt(self, projekt: str) -> None:
        with _oversatt():
            self.kv._kravs_projekt(projekt)  # noqa: SLF001 - samma regel som webben (404, ej byggd)

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
            "sparade_scenarier": info["scenarier"],
            "ortofoto": info["ortofoto"],
            "demo": {
                "lage": demo.get("lage"),
                "varld_nyckel": demo.get("varld", {}).get("nyckel"),
                "forkorda_korningar": {
                    ref: d.get("korning_id")
                    for ref, d in demo.get("delar", {}).items()
                    if isinstance(d, dict) and d.get("korning_id")
                },
            },
            "kodversion": self.kodversion,
        }

    def _byggnadsposter(self) -> list[dict[str, Any]]:
        if self._byggnader is not None:
            return self._byggnader
        mallar = self.kv._kravs_projekt(PROJEKT_ID)  # noqa: SLF001
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
        self._byggnader = lista
        return lista

    def geometri_sammanfattning(self, projekt: str) -> dict[str, Any]:
        self._kravs_projekt(projekt)
        with _oversatt():
            mallar = self.kv.mallar()
            byggn = self._byggnadsposter()
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
            alla = self._byggnadsposter()
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

    def fragor_lista(self) -> dict[str, Any]:
        with _oversatt():
            d = self.fragor.lista_fragor()
        ut = []
        for f in d["fragor"]:
            ut.append(
                {
                    "ref": f["ref"],
                    "fraga_id": f["fraga_id"],
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
        return {
            "kontrakt": "K20/v1",
            "fragor": ut,
            "tips": "Frågor med indata_saknas kan inte köras på den här datorn.",
        }

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
            mott = f"{j['efter']} − {j['fore']} (hela området)"

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
                            "(mark; byggnadernas tak undantagna)",
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
        omr = None if utsnitt is None else _rektangel(utsnitt, "utsnitt")
        with _oversatt():
            data = self.fragor.lager_raster(korning_id, la["id"])
        losare = next((t["losare"] for t in res["tabeller"] if t["analys"] == analys), "")
        byggnader = None
        if res.get("byggnader"):
            sc = la["efter"] if la["typ"] == "skillnad" else la["scenario"]
            byggnader = res["byggnader"]["scenarier"].get(sc)
        status = self.metodstatus(losare) if losare else {"status": "skiss"}
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

        def ok(p: Path) -> bool:
            return any(p.resolve().is_relative_to(r) for r in roter)

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
        if analyser:
            return [str(a) for a in analyser]
        mallar = self.kv._kravs_projekt(projekt)  # noqa: SLF001
        return [
            f"{kort}.{a.id}"
            for kort, m in mallar.items()
            for a in m.fraga.analyser
            if a.id != "vsc"  # samma förval som webben (VSC är långsammare)
        ]

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
    ) -> dict[str, Any]:
        """Vad en körning skulle räkna om och vad som redan finns i cachen (K22). Kör inget."""
        if (scenario is None) == (fraga is None):
            raise FlodenFel(
                "Ange antingen scenario (ett sparat scenario) eller fraga (ref).", "fel_indata"
            )
        if fraga is not None:
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
        projekt: str = PROJEKT_ID,
        scenario: str | None = None,
        fraga: str | None = None,
        analyser: Sequence[str] | None = None,
        avtappning_mm_h: float | None = None,
    ) -> dict[str, Any]:
        """Startar körningen i bakgrunden (som webben) och returnerar körnings-id direkt."""
        if (scenario is None) == (fraga is None):
            raise FlodenFel(
                "Ange antingen scenario (ett sparat scenario) eller fraga (ref).", "fel_indata"
            )
        if fraga is not None:
            with _oversatt():
                svar = self.fragor.starta_korning(fraga)
            return {
                "korningar": [self._start_post(svar, fraga=fraga)],
                "nasta_steg": "Anropa vanta med körnings-id, sedan resultat_sammanfattning.",
            }
        self._kravs_projekt(projekt)
        valda = self._valda_analyser(projekt, analyser)
        with _oversatt():
            k = self.kv.kor(projekt, str(scenario), valda, avtappning_mm_h)
        return {
            "projekt": projekt,
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
