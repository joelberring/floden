"""Webbens tjänst för Flodens utredningsfrågor (K20): lista, planera, köra och läsa resultat.

Allt läser och körs via kärnans API (`karna.utredning.las_fraga`, `karna.korplan.planera` och
`kor_utredning`, `karna.resultatcache.ResultatCache`, `karna.analyslosare`). Webben räknar
ingenting själv: den väljer fråga, visar planen, startar körningen i en bakgrundstråd i samma
process (ingen extern kö) och läser det kärnan skrev i resultatmappen.

Körningar ligger på lokal disk under `korningsmapp/<körnings-id>/`:

    korning.json       status (webbens egen, ingår inte i kärnans K0/K22)
    resultat/          kärnans utmapp: sammanstallning.json, run.json, <analys>/<scenario>/...
    webb/              PNG-förhandsvisningar och lager.json (cache av det som ritats)

Inloggning och konton finns inte; servern är till för lokal användning.
"""

from __future__ import annotations

import csv
import importlib
import json
import re
import secrets
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from karna.analyslosare import LosarFel, hamta_losare
from karna.korplan import Plan, kor_utredning, planera
from karna.resultatcache import CacheFel, ResultatCache
from karna.rutscenario import ScenarioFel
from karna.utredning import FragaFel, KorbarFraga, las_fraga
from webb.fel import FlodenWebbFel
from webb.moduler import losarinfo
from webb.resultatlager import (
    LagerFel,
    Mosaik,
    etikett_och_enhet,
    farg_for_roll,
    rita_png,
    sammanfoga,
    teckenforklaring,
    varde_intervall,
)

_ID = re.compile(r"^[a-z0-9][a-z0-9_.\-]*$")
_FRAGEFEL = (FragaFel, LosarFel, ScenarioFel, CacheFel)
MAX_TABELLRADER = 200


def _nu() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _kontrollera_id(varde: str, vad: str) -> str:
    if not _ID.match(varde) or ".." in varde:
        raise FlodenWebbFel(f"Ogiltigt {vad}: {varde!r}.", status_kod=400)
    return varde


@dataclass
class Korning:
    """En körning av en fråga. Skrivs till `korning.json` vid varje statusbyte."""

    korning_id: str
    ref: str
    fraga_id: str
    fraga_fil: str
    mapp: Path
    status: str = "ko"  # ko | kor | klar | fel
    skapad: str = field(default_factory=_nu)
    start: str | None = None
    slut: str | None = None
    fel: str | None = None
    jobb_totalt: int = 0
    plan: Plan | None = None
    """Planen hålls i minnet så länge körningen pågår (för framstegsmätning)."""

    @property
    def utmapp(self) -> Path:
        return self.mapp / "resultat"

    def som_dict(self) -> dict[str, Any]:
        return {
            "korning_id": self.korning_id,
            "ref": self.ref,
            "fraga_id": self.fraga_id,
            "fraga_fil": self.fraga_fil,
            "status": self.status,
            "skapad": self.skapad,
            "start": self.start,
            "slut": self.slut,
            "fel": self.fel,
            "jobb_totalt": self.jobb_totalt,
        }


class FragaTjanst:
    """Lista, planera, köra och läsa resultat för K20-frågor."""

    def __init__(
        self,
        *,
        rot: Path,
        fragemappar: list[Path],
        cachemapp: Path,
        korningsmapp: Path,
    ) -> None:
        self.rot = rot
        self.fragemappar = fragemappar
        self.cachemapp = cachemapp
        self.korningsmapp = korningsmapp
        self._cache: ResultatCache | None = None
        self._lock = threading.RLock()
        self._korningar: dict[str, Korning] = {}
        self._inlast = False
        self._exekutor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="floden-kor")

    # ------------------------------------------------------------------ hjälpfunktioner

    def cache(self) -> ResultatCache:
        with self._lock:
            if self._cache is None:
                self._cache = ResultatCache(self.cachemapp)
            return self._cache

    def _visa(self, sokvag: Path) -> str:
        try:
            return sokvag.resolve().relative_to(self.rot.resolve()).as_posix()
        except ValueError:
            return str(sokvag)

    def _fragefiler(self) -> dict[str, Path]:
        """ref -> fil. Ref är filnamnet utan ändelse (med -2, -3 vid krock mellan mappar)."""
        ut: dict[str, Path] = {}
        for mapp in self.fragemappar:
            if not mapp.is_dir():
                continue
            for fil in sorted([*mapp.glob("*.toml"), *mapp.glob("*.json")]):
                bas = re.sub(r"[^a-z0-9_\-]+", "-", fil.stem.lower()).strip("-") or "fraga"
                ref, n = bas, 2
                while ref in ut:
                    ref = f"{bas}-{n}"
                    n += 1
                ut[ref] = fil
        return ut

    def _fil_for(self, ref: str) -> Path:
        _kontrollera_id(ref, "frågereferens")
        fil = self._fragefiler().get(ref)
        if fil is None:
            raise FlodenWebbFel(f"Okänd fråga '{ref}'.", status_kod=404)
        return fil

    @staticmethod
    def _inledande_kommentar(fil: Path) -> str:
        """Frågefilens inledande `#`-kommentar (så står byggkommandot för demodata där)."""
        if fil.suffix.lower() != ".toml":
            return ""
        rader: list[str] = []
        for rad in fil.read_text(encoding="utf-8").splitlines():
            if rad.startswith("#"):
                rader.append(rad[1:].removeprefix(" "))
            elif rad.strip() == "" and rader:
                break
            else:
                break
        return "\n".join(rader)

    def _beskriv(self, ref: str, fil: Path) -> dict[str, Any]:
        post: dict[str, Any] = {
            "ref": ref,
            "fil": self._visa(fil),
            "hjalp": self._inledande_kommentar(fil),
        }
        try:
            fraga = las_fraga(fil)
        except FragaFel as exc:
            return {**post, "fraga_id": ref, "beskrivning": "", "giltig": False, "fel": str(exc)}
        problem: list[str] = []
        analyser: list[dict[str, Any]] = []
        for a in fraga.analyser:
            info: dict[str, Any] | None = None
            try:
                info = losarinfo(hamta_losare(a.losare))
            except LosarFel as exc:
                problem.append(str(exc))
            analyser.append(
                {
                    "id": a.id,
                    "losare": a.losare,
                    "doman": None if info is None else info["doman"],
                    "scenarier": list(a.scenarier),
                    "parametrar": dict(a.parametrar),
                    "beror_pa": list(a.beror_pa),
                }
            )
        scenarier: list[dict[str, Any]] = []
        saknas: list[str] = []
        for s in fraga.scenarier.values():
            for p in (s.bas, *s.k9, *s.lappar):
                if not p.exists():
                    saknas.append(self._visa(p))
            scenarier.append(
                {
                    "id": s.id,
                    "bas": self._visa(s.bas),
                    "k9": [self._visa(p) for p in s.k9],
                    "lappar": [self._visa(p) for p in s.lappar],
                }
            )
        return {
            **post,
            "fraga_id": fraga.fraga_id,
            "beskrivning": fraga.beskrivning,
            "giltig": not problem,
            "fel": "; ".join(problem) or None,
            "scenarier": scenarier,
            "analyser": analyser,
            "jamforelser": [
                {
                    "analys": j.analys,
                    "utdata": j.utdata,
                    "fore": j.fore,
                    "efter": j.efter,
                    "troskel": j.troskel,
                }
                for j in fraga.jamforelser
            ],
            "indata_saknas": sorted(set(saknas)),
        }

    def _las(self, ref: str) -> tuple[Path, KorbarFraga]:
        fil = self._fil_for(ref)
        try:
            return fil, las_fraga(fil)
        except FragaFel as exc:
            raise FlodenWebbFel(str(exc), status_kod=422) from exc

    # ------------------------------------------------------------------ frågor och plan

    def lista_fragor(self) -> dict[str, Any]:
        return {
            "fragemappar": [self._visa(m) for m in self.fragemappar],
            "fragor": [self._beskriv(ref, fil) for ref, fil in self._fragefiler().items()],
        }

    def hamta_fraga(self, ref: str) -> dict[str, Any]:
        return self._beskriv(ref, self._fil_for(ref))

    def _planera(self, fraga: KorbarFraga) -> Plan:
        try:
            return planera(fraga, self.cache())
        except _FRAGEFEL as exc:
            raise FlodenWebbFel(str(exc), status_kod=422) from exc
        except OSError as exc:
            raise FlodenWebbFel(f"Kunde inte planera frågan: {exc}", status_kod=422) from exc

    def planera_fraga(self, ref: str) -> dict[str, Any]:
        _, fraga = self._las(ref)
        plan = self._planera(fraga)
        return {"ref": ref, "fraga_id": fraga.fraga_id, **_beskriv_plan(plan)}

    # ------------------------------------------------------------------ körningar

    def _skriv_status(self, k: Korning) -> None:
        k.mapp.mkdir(parents=True, exist_ok=True)
        tmp = k.mapp / "korning.json.tmp"
        tmp.write_text(
            json.dumps(k.som_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        tmp.replace(k.mapp / "korning.json")

    def _lasta_korningar(self) -> None:
        with self._lock:
            if self._inlast:
                return
            self._inlast = True
            if not self.korningsmapp.is_dir():
                return
            for fil in sorted(self.korningsmapp.glob("*/korning.json")):
                try:
                    d = json.loads(fil.read_text(encoding="utf-8"))
                    k = Korning(
                        korning_id=d["korning_id"],
                        ref=d["ref"],
                        fraga_id=d["fraga_id"],
                        fraga_fil=d["fraga_fil"],
                        mapp=fil.parent,
                        status=d["status"],
                        skapad=d["skapad"],
                        start=d.get("start"),
                        slut=d.get("slut"),
                        fel=d.get("fel"),
                        jobb_totalt=int(d.get("jobb_totalt", 0)),
                    )
                except (OSError, ValueError, KeyError):
                    continue  # en trasig post hindrar inte de andra
                if k.status in ("ko", "kor"):
                    k.status = "fel"
                    k.fel = "Körningen avbröts när servern stoppades."
                    k.slut = k.slut or _nu()
                    self._skriv_status(k)
                self._korningar[k.korning_id] = k

    def starta_korning(self, ref: str) -> dict[str, Any]:
        fil, fraga = self._las(ref)
        self._lasta_korningar()
        with self._lock:
            for k in self._korningar.values():
                if k.ref == ref and k.status in ("ko", "kor"):
                    return {**self._status(k), "ateranvand": True}
        plan = self._planera(fraga)  # tydligt fel direkt, innan något köas
        korning_id = (
            f"{fraga.fraga_id}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        )
        k = Korning(
            korning_id=korning_id,
            ref=ref,
            fraga_id=fraga.fraga_id,
            fraga_fil=self._visa(fil),
            mapp=self.korningsmapp / korning_id,
            jobb_totalt=len(plan.jobb),
            plan=plan,
        )
        with self._lock:
            self._korningar[korning_id] = k
            self._skriv_status(k)
        self._exekutor.submit(self._kor, k, fraga, plan)
        return {**self._status(k), "ateranvand": False}

    def _kor(self, k: Korning, fraga: KorbarFraga, plan: Plan) -> None:
        with self._lock:
            k.status, k.start = "kor", _nu()
            self._skriv_status(k)
        try:
            kor_utredning(fraga, self.cache(), k.utmapp, plan=plan)
        except Exception as exc:  # noqa: BLE001 - felet sparas och visas, inget sväljs
            with self._lock:
                k.status, k.slut, k.fel = "fel", _nu(), f"{type(exc).__name__}: {exc}"
                k.plan = None
                self._skriv_status(k)
            return
        with self._lock:
            k.status, k.slut, k.plan = "klar", _nu(), None
            self._skriv_status(k)

    def _hamta(self, korning_id: str) -> Korning:
        _kontrollera_id(korning_id, "körnings-id")
        self._lasta_korningar()
        with self._lock:
            k = self._korningar.get(korning_id)
        if k is None:
            raise FlodenWebbFel(f"Okänd körning '{korning_id}'.", status_kod=404)
        return k

    def _status(self, k: Korning) -> dict[str, Any]:
        plan = k.plan
        if k.status == "klar":
            klara = k.jobb_totalt
        elif plan is not None:
            # Antal jobb vars nyckel nu finns i cachen: kärnans cache är facit för vad som är klart.
            klara = sum(1 for j in plan.jobb if self.cache().finns("jobb", j.nyckel))
        else:
            klara = 0
        return {**k.som_dict(), "framsteg": {"klara": klara, "totalt": k.jobb_totalt}}

    def korning_status(self, korning_id: str) -> dict[str, Any]:
        return self._status(self._hamta(korning_id))

    def lista_korningar(self) -> dict[str, Any]:
        self._lasta_korningar()
        with self._lock:
            alla = sorted(self._korningar.values(), key=lambda k: k.skapad, reverse=True)
        return {"korningar": [self._status(k) for k in alla]}

    # ------------------------------------------------------------------ resultat

    def _sammanstallning(self, k: Korning) -> dict[str, Any]:
        if k.status == "fel":
            raise FlodenWebbFel(f"Körningen misslyckades: {k.fel}", status_kod=409)
        if k.status != "klar":
            raise FlodenWebbFel(f"Körningen är inte klar (status: {k.status}).", status_kod=409)
        try:
            data: dict[str, Any] = json.loads(
                (k.utmapp / "sammanstallning.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise FlodenWebbFel(f"Resultatet kan inte läsas: {exc}", status_kod=500) from exc
        return data

    def _jobbmapp(self, k: Korning, j: dict[str, Any]) -> Path:
        mapp: Path = k.utmapp / str(j["analys"]) / str(j["scenario"])
        return mapp / str(j["ruta"]) if j.get("ruta") else mapp

    def _roller(self, k: Korning, j: dict[str, Any]) -> dict[str, str]:
        """roll -> filnamn för ett jobb, ur dess run.json (K0)."""
        manifest = json.loads((self._jobbmapp(k, j) / "run.json").read_text(encoding="utf-8"))
        return {p["roll"]: p["fil"] for p in manifest.get("utdata", [])}

    def _url_fil(self, k: Korning, rel: Path) -> str:
        return f"/api/korningar/{k.korning_id}/fil/{rel.relative_to(k.utmapp).as_posix()}"

    def resultat(self, korning_id: str) -> dict[str, Any]:
        k = self._hamta(korning_id)
        s = self._sammanstallning(k)
        lager, lagervarningar = self._lager(k, s)
        return {
            "korning": self._status(k),
            "fraga_id": s["fraga_id"],
            "beskrivning": s.get("beskrivning", ""),
            "sammanfattning": s["sammanfattning"],
            "scenarier": [
                {"id": sid, "nyckel": v["nyckel"], "rutor": len(v["rutor"])}
                for sid, v in s["scenarier"].items()
            ],
            "tabeller": _matvardestabeller(s),
            "jamforelser": [
                {k2: v for k2, v in jf.items() if k2 not in ("per_ruta", "mapp")}
                for jf in s["jamforelser"]
            ],
            "varningar": [*_varningar(s), *(_webbvarning(t) for t in lagervarningar)],
            "lager": lager,
            "datatabeller": self._datatabeller(k, s),
        }

    # Lager -------------------------------------------------------------------------------

    def _lager(self, k: Korning, s: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        cachefil = k.mapp / "webb" / "lager.json"
        with self._lock:
            if cachefil.is_file():
                d = json.loads(cachefil.read_text(encoding="utf-8"))
                return d["lager"], d["varningar"]
            lager, varningar = self._bygg_lager(k, s)
            cachefil.parent.mkdir(parents=True, exist_ok=True)
            cachefil.write_text(
                json.dumps({"lager": lager, "varningar": varningar}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            return lager, varningar

    def _bygg_lager(self, k: Korning, s: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        ut = k.mapp / "webb"
        ut.mkdir(parents=True, exist_ok=True)
        varningar: list[str] = []
        scenarioordning = list(s["scenarier"])
        fore = {jf["fore"] for jf in s["jamforelser"]}
        efter = {jf["efter"] for jf in s["jamforelser"]}

        def typ(sid: str) -> str:
            if sid in efter:
                return "scenario"
            return "bas" if sid in fore or sid == scenarioordning[0] else "scenario"

        # (analys, scenario) -> jobb
        grupper: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for j in s["jobb"]:
            grupper[(j["analys"], j["scenario"])].append(j)

        poster: list[dict[str, Any]] = []  # lager före rendering
        for (analys, sid), jobb in grupper.items():
            roller = self._roller(k, jobb[0])
            try:
                primar_ordning = list(hamta_losare(jobb[0]["losare"]).utdataroller)
            except LosarFel:
                primar_ordning = list(roller)
            for roll, namn in roller.items():
                if not namn.lower().endswith((".tif", ".tiff")):
                    continue
                filer = [self._jobbmapp(k, j) / namn for j in jobb]
                poster.append(
                    {
                        "id": f"{analys}.{sid}.{roll}",
                        "analys": analys,
                        "scenario": sid,
                        "roll": roll,
                        "typ": typ(sid),
                        "primar": bool(primar_ordning) and roll == primar_ordning[0],
                        "filer": filer,
                        "ruta_ider": [j.get("ruta") for j in jobb],
                        "skillnad": False,
                        "troskel": 0.0,
                    }
                )
        for jf in s["jamforelser"]:
            mapp = Path(k.utmapp / "jamforelser" / jf["namn"])
            filer = sorted(mapp.glob("*.tif"))
            poster.append(
                {
                    "id": f"skillnad.{jf['namn']}",
                    "analys": jf["analys"],
                    "scenario": jf["efter"],
                    "roll": jf["utdata"],
                    "typ": "skillnad",
                    "primar": True,
                    "filer": filer,
                    "ruta_ider": [None if f.stem == "diff" else f.stem for f in filer],
                    "skillnad": True,
                    "troskel": float(jf["troskel"]),
                    "fore": jf["fore"],
                    "efter": jf["efter"],
                }
            )

        mosaiker: dict[str, Mosaik] = {}
        for p in poster:
            try:
                mosaiker[p["id"]] = sammanfoga(p["filer"])
            except (LagerFel, OSError, ValueError) as exc:
                varningar.append(f"Lagret {p['id']} kunde inte visas: {exc}")
        # Gemensam skala per (analys, roll) så att bas och scenario går att jämföra.
        skalor: dict[tuple[str, str], tuple[float, float]] = {}
        for nyckel in {(p["analys"], p["roll"]) for p in poster if not p["skillnad"]}:
            ras = [
                mosaiker[p["id"]].raster
                for p in poster
                if (p["analys"], p["roll"]) == nyckel and not p["skillnad"] and p["id"] in mosaiker
            ]
            skalor[nyckel] = varde_intervall(ras)

        lager: list[dict[str, Any]] = []
        for p in poster:
            m = mosaiker.get(p["id"])
            if m is None:
                continue
            etikett, enhet = etikett_och_enhet(p["roll"])
            farg = farg_for_roll(p["roll"], skillnad=p["skillnad"])
            if p["skillnad"]:
                lo, hi = varde_intervall([m.raster])
                lo, hi = 0.0, max(abs(lo), abs(hi))
                namn = f"{etikett} · {p['efter']} − {p['fore']}"
            else:
                lo, hi = skalor[(p["analys"], p["roll"])]
                namn = f"{etikett} · {p['scenario']}"
            png, bredd, hojd, faktor = rita_png(
                m.raster, farg, lo=lo, hi=hi, troskel=float(p["troskel"])
            )
            (ut / f"{p['id']}.png").write_bytes(png)
            if faktor > 1:
                varningar.append(
                    f"Förhandsvisningen av {p['id']} är nedsamplad {faktor} gånger; "
                    "GeoTIFF-filerna har full upplösning."
                )
            lager.append(
                {
                    "id": p["id"],
                    "namn": namn,
                    "etikett": etikett,
                    "analys": p["analys"],
                    "scenario": p["scenario"],
                    "roll": p["roll"],
                    "typ": p["typ"],
                    "primar": p["primar"],
                    "enhet": enhet,
                    "farg": farg,
                    "skala": {"min": lo, "max": hi},
                    "teckenforklaring": teckenforklaring(farg, lo, hi, float(p["troskel"])),
                    "utbredning": m.utbredning.som_dict(),
                    "pixelstorlek_m": m.pixelstorlek_m,
                    "bredd_px": bredd,
                    "hojd_px": hojd,
                    "nedsampling": faktor,
                    "png": f"/api/korningar/{k.korning_id}/lager/{p['id']}.png",
                    "geotiff": [
                        {"ruta": rid, "fil": f.name, "url": self._url_fil(k, f)}
                        for rid, f in zip(p["ruta_ider"], p["filer"], strict=True)
                    ],
                    **({"fore": p["fore"], "efter": p["efter"]} if p["skillnad"] else {}),
                }
            )
        ordning = {"bas": 0, "scenario": 1, "skillnad": 2}
        lager.sort(key=lambda x: (not x["primar"], x["analys"], ordning[x["typ"]], x["id"]))
        return lager, varningar

    def lager_png(self, korning_id: str, lager_id: str) -> bytes:
        k = self._hamta(korning_id)
        _kontrollera_id(lager_id, "lager-id")
        self.resultat(korning_id)  # ritar lagren första gången
        fil = k.mapp / "webb" / f"{lager_id}.png"
        if not fil.is_file():
            raise FlodenWebbFel(f"Okänt lager '{lager_id}'.", status_kod=404)
        return fil.read_bytes()

    # Filer och tabeller --------------------------------------------------------------------

    def _fil_i_resultat(self, k: Korning, rel: str) -> Path:
        if k.status != "klar":
            raise FlodenWebbFel("Körningen är inte klar.", status_kod=409)
        rot = k.utmapp.resolve()
        fil = (rot / rel).resolve()
        if not fil.is_relative_to(rot) or not fil.is_file():
            raise FlodenWebbFel(f"Okänd fil '{rel}'.", status_kod=404)
        return fil

    def fil(self, korning_id: str, rel: str) -> tuple[bytes, str]:
        fil = self._fil_i_resultat(self._hamta(korning_id), rel)
        typer = {
            ".tif": "image/tiff",
            ".tiff": "image/tiff",
            ".csv": "text/csv; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
        }
        return fil.read_bytes(), typer.get(fil.suffix.lower(), "application/octet-stream")

    def _datatabeller(self, k: Korning, s: dict[str, Any]) -> list[dict[str, Any]]:
        ut: list[dict[str, Any]] = []
        for j in s["jobb"]:
            for roll, namn in self._roller(k, j).items():
                if namn.lower().endswith((".csv", ".parquet")):
                    fil = self._jobbmapp(k, j) / namn
                    ut.append(
                        {
                            "analys": j["analys"],
                            "scenario": j["scenario"],
                            "ruta": j.get("ruta"),
                            "roll": roll,
                            "fil": namn,
                            "url": self._url_fil(k, fil),
                            "forhandsvisning": self._url_fil(k, fil).replace("/fil/", "/tabell/"),
                        }
                    )
        return ut

    def tabell(self, korning_id: str, rel: str) -> dict[str, Any]:
        """Första raderna ur en CSV- eller Parquet-fil i resultatet."""
        fil = self._fil_i_resultat(self._hamta(korning_id), rel)
        if fil.suffix.lower() == ".csv":
            with fil.open(encoding="utf-8", newline="") as fh:
                läsare = csv.DictReader(fh)
                kolumner = list(läsare.fieldnames or [])
                rader: list[dict[str, str]] = []
                antal = 0
                for rad in läsare:
                    antal += 1
                    if len(rader) < MAX_TABELLRADER:
                        rader.append(rad)
            return {"fil": rel, "kolumner": kolumner, "rader": rader, "antal_rader": antal}
        if fil.suffix.lower() == ".parquet":
            pq: Any = importlib.import_module("pyarrow.parquet")
            tabell = pq.read_table(fil)
            ark = tabell.slice(0, MAX_TABELLRADER).to_pylist()
            return {
                "fil": rel,
                "kolumner": list(tabell.column_names),
                "rader": [{kol: _json_varde(v) for kol, v in r.items()} for r in ark],
                "antal_rader": tabell.num_rows,
            }
        raise FlodenWebbFel(f"Bara CSV och Parquet kan visas som tabell ('{rel}').", status_kod=415)


def _json_varde(v: Any) -> Any:
    return v if v is None or isinstance(v, str | int | float | bool) else str(v)


def _beskriv_plan(plan: Plan) -> dict[str, Any]:
    scenarier = []
    for sid, sc in plan.scenarier.items():
        scenarier.append(
            {
                "id": sid,
                "nyckel": sc.nyckel,
                "antal_rutor": len(sc.rutor),
                "rutor": [
                    {
                        "ruta_id": r.ruta_id,
                        "x_min": r.x_min,
                        "y_min": r.y_min,
                        "x_max": r.x_max,
                        "y_max": r.y_max,
                        "hash": r.hash[:12],
                    }
                    for r in sc.rutor
                ],
            }
        )
    alla = [r for sc in plan.scenarier.values() for r in sc.rutor]
    utbredning = (
        {
            "x_min": min(r.x_min for r in alla),
            "y_min": min(r.y_min for r in alla),
            "x_max": max(r.x_max for r in alla),
            "y_max": max(r.y_max for r in alla),
        }
        if alla
        else None
    )
    jobb = [
        {
            "etikett": j.etikett,
            "analys": j.analys.id,
            "scenario": j.scenario.id,
            "ruta": None if j.ruta is None else j.ruta.ruta_id,
            "losare": j.losare.namn,
            "version": j.losare.version,
            "radie_m": j.radie_m,
            "grannar": [g.ruta_id for g in j.grannar],
            "nyckel": j.nyckel[:12],
            "i_cache": j.i_cache,
        }
        for j in plan.jobb
    ]
    per_analys: dict[str, dict[str, int]] = {}
    for j in plan.jobb:
        p = per_analys.setdefault(j.analys.id, {"jobb": 0, "i_cache": 0, "att_kora": 0})
        p["jobb"] += 1
        p["i_cache" if j.i_cache else "att_kora"] += 1
    return {
        "sammanfattning": plan.sammanfattning(),
        "per_analys": per_analys,
        "scenarier": scenarier,
        "utbredning": utbredning,
        "jobb": jobb,
    }


def _matvardestabeller(s: dict[str, Any]) -> list[dict[str, Any]]:
    """Mätvärden per analys: en rad per jobb (scenario × ruta), kolumner = mätvärdesnamn."""
    per: dict[str, dict[str, Any]] = {}
    for j in s["jobb"]:
        t = per.setdefault(
            j["analys"],
            {"analys": j["analys"], "losare": j["losare"], "kolumner": [], "rader": []},
        )
        for namn in j["matvarden"]:
            if namn not in t["kolumner"]:
                t["kolumner"].append(namn)
        t["rader"].append(
            {
                "scenario": j["scenario"],
                "ruta": j.get("ruta"),
                "fran_cache": j["fran_cache"],
                "kortid_s": j["kortid_s"],
                "varden": j["matvarden"],
            }
        )
    return list(per.values())


_TAL_I_TEXT = re.compile(r"\d+(?:[.,]\d+)?")


def _varningar(s: dict[str, Any]) -> list[dict[str, Any]]:
    """Lösarnas varningar som grupper.

    Varningar som bara skiljer sig i tal (t.ex. antal fasadpunkter per ruta) hamnar i samma grupp;
    gruppen visar första texten och listar alla varianter. Inget tal ändras eller summeras.
    """
    grupper: dict[str, dict[str, Any]] = {}
    for j in s["jobb"]:
        for text in j["varningar"]:
            g = grupper.setdefault(
                _TAL_I_TEXT.sub("#", text),
                {
                    "text": text,
                    "antal_jobb": 0,
                    "analyser": set(),
                    "scenarier": set(),
                    "varianter": {},
                },
            )
            g["antal_jobb"] += 1
            g["analyser"].add(j["analys"])
            g["scenarier"].add(j["scenario"])
            g["varianter"][text] = g["varianter"].get(text, 0) + 1
    return [
        {
            "text": g["text"],
            "kalla": "losare",
            "antal_jobb": g["antal_jobb"],
            "analyser": sorted(g["analyser"]),
            "scenarier": sorted(g["scenarier"]),
            "varianter": (
                [{"text": t, "antal_jobb": n} for t, n in sorted(g["varianter"].items())]
                if len(g["varianter"]) > 1
                else []
            ),
        }
        for g in grupper.values()
    ]


def _webbvarning(text: str) -> dict[str, Any]:
    return {
        "text": text,
        "kalla": "webb",
        "antal_jobb": 0,
        "analyser": [],
        "scenarier": [],
        "varianter": [],
    }
