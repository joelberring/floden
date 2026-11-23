"""Kartverktyget: rita ett planförslag i kartan och räkna före och efter (webbens API).

Klienten ritar bara geometri (hus och höjder). Allt annat görs här, med kärnans egna delar:

- **Kontroll** av det ritade (`karna.ritat`, `karna.scenario`, `karna.geometri.tillampa`).
- **K9-scenario** per bas (samma `bas_recept`-regel som `floden demo`): ett ritat hus ger exakt
  samma K9 och därmed samma jobbnycklar och resultat som en handskriven K9-fil.
- **K20-fråga** i den lokala frågemappen (`<kartverktygsmapp>/fragor`, inte i git), byggd ur de
  demofrågor (mallar) som projektet har; körs sedan av webbens vanliga frågetjänst.
- **Sparade scenarier** som lokala filer (`<kartverktygsmapp>/<projekt>/scenarier/<namn>.json`).
- **Underlag**: WMS (GetCapabilities och GetMap via servern som proxy) och ortofoto ur
  `floden data lantmateriet` om det finns lokalt som GeoTIFF.

Inget i detta ritar resultat eller räknar mått: det som visas efter en körning kommer ur kärnans
filer via `FragaTjanst`.
"""

from __future__ import annotations

import io
import ipaddress
import json
import math
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET  # noqa: S405 - svaret begränsas i storlek, inga externa entiteter hämtas
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from gemensamt.hash import sha256_fil
from karna.geometri import las_byggnader
from karna.geometri.tillampa import valj_k1_version, verifiera_byggnadsreferenser
from karna.k9_till_k1 import dela_andringar
from karna.ritat import (
    HOJD_MAX_M,
    VANINGSHOJD_M,
    RitatFel,
    kontrollera_andringar,
    normalisera_ring,
)
from karna.rutscenario import Ruta, las_bas
from karna.scenario import Andring, Planscenario, ScenarioFel, skriv_scenario
from karna.utredning import KorbarFraga, las_fraga
from webb.fel import FlodenWebbFel
from webb.felforklaring import SLUTET_INFO
from webb.fragor import FragaTjanst
from webb.moduler import visningsnamn

FORMAT = "floden-kartverktyg/v1"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,59}$")
MAX_WMS_BYTES = 12 * 1024 * 1024
MAX_CAPABILITIES_BYTES = 4 * 1024 * 1024
WMS_TIDSGRANS_S = 20.0
MAX_BYGGNADER = 20_000
ORTOFOTO_MAX_PX = 2048

_PROJEKT_ID = "demokvarteret"


_LOSARE_MED_AVTAPPNING = frozenset({"vatten.statisk_fyllning", "vatten.dynamik"})


def _avtappning(v: Any) -> float | None:
    """Avtappning ur kartverktyget (mm/h, >= 0), eller None (lösarens standard 0 mm/h)."""
    if v is None or v == "":
        return None
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(float(v)):
        raise _fel(f"Avtappningen måste vara ett tal i mm/h, fick {v!r}.", 400)
    if float(v) < 0.0:
        raise _fel("Avtappningen får inte vara negativ.", 400)
    return float(v)


def slug(namn: str) -> str:
    """Filnamn och scenario-id ur ett namn (a-z, 0-9, bindestreck). Åäö blir aao."""
    s = namn.strip().lower()
    for a, b in (("å", "a"), ("ä", "a"), ("ö", "o"), ("é", "e")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9_\-]+", "-", s).strip("-_")
    return s[:60]


def _fel(text: str, status: int = 422) -> FlodenWebbFel:
    return FlodenWebbFel(text, status_kod=status)


@dataclass(frozen=True)
class Mall:
    """En demofråga som mall för ritade frågor: en bas, ett scenario utan ändring, analyser."""

    ref: str
    kort: str
    """Kort namn i frågornas id: `dagsljus`, `vatten`."""
    fraga: KorbarFraga
    bas: Path
    rutor: tuple[Ruta, ...]
    fore_id: str


def _rektangel(r: Ruta) -> tuple[float, float, float, float]:
    return (r.x_min, r.y_min, r.x_max, r.y_max)


class KartverktygTjanst:
    """Ritade planförslag: kontroll, K9/K20, sparade scenarier och underlag."""

    def __init__(
        self,
        fragor: FragaTjanst,
        mapp: Path,
        *,
        datamappar: Sequence[Path] = (),
        tillat_loopback: bool = False,
    ) -> None:
        self.fragor = fragor
        self.mapp = mapp
        self.datamappar = list(datamappar)
        self.tillat_loopback = tillat_loopback
        self.fragedir = mapp / "fragor"
        if self.fragedir not in fragor.fragemappar:
            fragor.fragemappar.append(self.fragedir)
        self._mallar: dict[str, Mall] | None = None
        self._byggnader: list[dict[str, Any]] | None = None
        self._caps: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ mallar och bas

    def _demo_refs(self) -> list[tuple[str, str]]:
        d = self.fragor.demo()
        if not d.get("finns"):
            return []
        delar = d.get("delar", {})
        ut = []
        for ref, kort in (
            ("demo_dagsljus", "dagsljus"),
            ("demo_vatten", "vatten"),
            ("demo_ekologi", "ekologi"),
        ):
            if isinstance(delar.get(ref), dict) and "jobb" in delar[ref]:
                ut.append((ref, kort))
        return ut

    def mallar(self) -> dict[str, Mall]:
        if self._mallar is not None:
            return self._mallar
        ut: dict[str, Mall] = {}
        for ref, kort in self._demo_refs():
            try:
                fil = self.fragor._fil_for(ref)  # noqa: SLF001 - samma paket
                fraga = las_fraga(fil)
            except Exception:  # noqa: BLE001 - en trasig mall hindrar inte de andra
                continue
            fore = next(
                (s for s in fraga.scenarier.values() if not s.k9 and not s.lappar), None
            )
            if fore is None:
                continue
            try:
                rutor = las_bas(fore.bas)
            except ScenarioFel:
                continue
            ut[kort] = Mall(ref, kort, fraga, fore.bas, rutor, fore.id)
        if ut:
            self._mallar = ut
        return ut

    def _kravs_projekt(self, projekt: str) -> dict[str, Mall]:
        if projekt != _PROJEKT_ID:
            raise _fel(f"Okänt projekt '{projekt}'.", 404)
        m = self.mallar()
        if not m:
            raise _fel(
                "Demokvarteret är inte byggt. Kör `floden demo` och ladda om sidan.", 404
            )
        return m

    @staticmethod
    def _bas_recept(mall: Mall) -> tuple[str, ...]:
        return tuple(
            sha256_fil(r.mapp / "recept.json")
            for r in mall.rutor
            if (r.mapp / "recept.json").is_file()
        )

    def _rutor_alla(self, mallar: Mapping[str, Mall]) -> list[tuple[float, float, float, float]]:
        return [_rektangel(r) for m in mallar.values() for r in m.rutor]

    def _fokus(self, mallar: Mapping[str, Mall]) -> list[float] | None:
        for m in mallar.values():
            for a in m.fraga.analyser:
                if a.fokus is not None:
                    return list(a.fokus)
        return None

    # ------------------------------------------------------------------ projektinfo

    def _alla_byggnader(self, mallar: Mapping[str, Mall]) -> list[dict[str, Any]]:
        if self._byggnader is not None:
            return self._byggnader
        sedda: dict[str, dict[str, Any]] = {}
        for kort, m in mallar.items():
            for r in m.rutor:
                for b in las_byggnader(r.mapp):
                    if b.byggnad_id in sedda:
                        sedda[b.byggnad_id]["baser"].append(kort)
                        continue
                    sedda[b.byggnad_id] = {
                        "id": b.byggnad_id,
                        "ring": [[float(x), float(y)] for x, y in b.polygon],
                        "hal": [[[float(x), float(y)] for x, y in h] for h in b.hal],
                        "hojd_m": round(float(b.hojd_m), 2),
                        "baser": [kort],
                    }
        for post in sedda.values():
            post["baser"] = sorted(set(post["baser"]))
        lista = sorted(sedda.values(), key=lambda p: str(p["id"]))
        self._byggnader = lista
        return lista

    def _analyser(self, mallar: Mapping[str, Mall]) -> list[dict[str, Any]]:
        ut: list[dict[str, Any]] = []
        for kort, m in mallar.items():
            for a in m.fraga.analyser:
                ut.append(
                    {
                        "id": f"{kort}.{a.id}",
                        "namn": visningsnamn(a.losare),
                        "mall": kort,
                        "analys": a.id,
                        "forval": a.id != "vsc",
                    }
                )
        return ut

    def projektinfo(self, projekt: str) -> dict[str, Any]:
        mallar = self._kravs_projekt(projekt)
        rutor = self._rutor_alla(mallar)
        byggn = self._alla_byggnader(mallar)
        trunkerad = len(byggn) > MAX_BYGGNADER
        return {
            "id": projekt,
            "paahittat": True,
            "epsg": 3011,
            "hojd_max_m": HOJD_MAX_M,
            "vaningshojd_m": VANINGSHOJD_M,
            "utbredning": {
                "x_min": min(r[0] for r in rutor),
                "y_min": min(r[1] for r in rutor),
                "x_max": max(r[2] for r in rutor),
                "y_max": max(r[3] for r in rutor),
            },
            "baser": {
                k: {
                    "rutor": [
                        {"x_min": r.x_min, "y_min": r.y_min, "x_max": r.x_max, "y_max": r.y_max}
                        for r in m.rutor
                    ],
                    "analyser": [a.id for a in m.fraga.analyser],
                }
                for k, m in mallar.items()
            },
            "fokus": self._fokus(mallar),
            "byggnader": byggn[:MAX_BYGGNADER],
            "byggnader_trunkerade": trunkerad,
            "analyser": self._analyser(mallar),
            "scenarier": self.lista_scenarier(projekt)["scenarier"],
            "ortofoto": self.ortofoto_info(projekt),
        }

    # ------------------------------------------------------------------ kontroll

    def _normalisera(self, andringar: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        ut: list[dict[str, Any]] = []
        for a in andringar:
            if not isinstance(a, Mapping):
                raise _fel("En ändring har fel form.")
            d: dict[str, Any] = {
                "typ": a.get("typ"),
                "andring_id": str(a.get("andring_id", "")).strip(),
                "geometri": a.get("geometri"),
                "egenskaper": dict(a.get("egenskaper") or {}),
            }
            h = d["egenskaper"].get("hojd_m")
            if isinstance(h, int | float) and not isinstance(h, bool):
                d["egenskaper"]["hojd_m"] = float(h)  # 18 och 18.0 ska ge samma K9-nyckel
            g = d["geometri"]
            if (
                d["typ"] == "byggnad_ny"
                and isinstance(g, Mapping)
                and g.get("type") == "Polygon"
                and g.get("coordinates")
            ):
                d["geometri"] = {
                    "type": "Polygon",
                    "coordinates": [normalisera_ring(g["coordinates"][0])],
                }
            ut.append(d)
        return ut

    def validera(self, projekt: str, kropp: Mapping[str, Any]) -> dict[str, Any]:
        """Kontrollerar ritade ändringar.

        Svar: `giltig`, `fel`, `varningar`, `information`, `hus`, `andringar`. `information` är
        upplysningar som inte hindrar något (t.ex. att ett hus stänger in en gård).
        """
        mallar = self._kravs_projekt(projekt)
        lista = kropp.get("andringar")
        if not isinstance(lista, list):
            raise _fel("Anropet saknar 'andringar'.", 400)
        andringar = self._normalisera(lista)
        fel: list[str] = []
        varningar: list[str] = []
        information: list[str] = []
        hus: list[dict[str, Any]] = []
        try:
            res = kontrollera_andringar(andringar, self._rutor_alla(mallar))
            hus = res["hus"]
            varningar.extend(res["varningar"])
            # Kärnans egen kontroll av ändringarna (typ, id, dubbletter, byggnadsreferenser).
            k9_per_bas = self._k9_per_bas(mallar, andringar, "kontroll")
            varningar.extend(self._paverkar_inte(mallar, k9_per_bas))
            information.extend(self._instangt_vatten(mallar, k9_per_bas))
        except (RitatFel, ScenarioFel) as exc:
            fel.append(str(exc))
        return {
            "giltig": not fel,
            "fel": fel,
            "varningar": varningar,
            "information": information,
            "hus": hus,
            "andringar": andringar,
        }

    # ------------------------------------------------------------------ K9 per bas

    def _k9_per_bas(
        self,
        mallar: Mapping[str, Mall],
        andringar: Sequence[Mapping[str, Any]],
        scenario_id: str,
        beskrivning: str = "",
    ) -> dict[str, tuple[Planscenario, list[str]]]:
        """Ett K9 per bas (samma regel som demon): `bas_recept` ur basens recept.json.

        Höjdändringar och borttagningar av byggnader som inte finns i basens rutor tas inte med
        där (de påverkar inte den basen) och redovisas.
        """
        ut: dict[str, tuple[Planscenario, list[str]]] = {}
        for kort, m in mallar.items():
            recept = self._bas_recept(m)
            if not recept:
                raise RitatFel(f"Basen {m.bas} saknar recept.json.")
            ids = {b.byggnad_id for r in m.rutor for b in las_byggnader(r.mapp)}
            med: list[Andring] = []
            utelamnade: list[str] = []
            for a in andringar:
                if a["typ"] in ("byggnad_hojd", "byggnad_bort"):
                    bid = str((a.get("egenskaper") or {}).get("byggnads_id", ""))
                    if bid not in ids:
                        utelamnade.append(str(a["andring_id"]))
                        continue
                med.append(Andring.fran_dict(a))
            sc = Planscenario(scenario_id, recept, tuple(med), beskrivning=beskrivning)
            mappar = [r.mapp for r in m.rutor]
            _, geometri = dela_andringar(med, valj_k1_version(mappar), scenario_id)
            verifiera_byggnadsreferenser(mappar, geometri)
            ut[kort] = (sc, utelamnade)
        return ut

    def _instangt_vatten(
        self, mallar: Mapping[str, Mall], k9: Mapping[str, tuple[Planscenario, list[str]]]
    ) -> list[str]:
        """Information före körning: ritade hus som stänger in ett markområde i vattnets rutor.

        Byggnader är ogenomträngliga i vattenmodellen, så ett område som helt omges av dem blir en
        slutsänka (K2/v2): vattnet blir stående där. Det är inget fel, men användaren ska veta det.
        Kontrollen tillämpar förslaget på rutorna i en tillfällig mapp och söker områden som inte
        når rutans kant (och inte fanns redan i basen).
        """
        import tempfile

        from karna.geometri.tillampa import tillampa_pa_v2_ruta
        from sankgraf.fyllning import instangda_omraden_i_k1_mapp

        mall = mallar.get("vatten")
        if mall is None or "vatten" not in k9:
            return []
        sc, _ = k9["vatten"]
        andringar = list(sc.andringar)
        mappar = [r.mapp for r in mall.rutor]
        version = valj_k1_version(mappar)
        if version < 2 or not andringar:
            return []
        _, geometri = dela_andringar(andringar, version, "kontroll")
        if not geometri:
            return []
        for r in mall.rutor:
            recept = r.mapp / "recept.json"
            with tempfile.TemporaryDirectory(prefix="floden-instangt-") as tmp:
                ut = Path(tmp) / "ruta"
                andrad = tillampa_pa_v2_ruta(
                    r.mapp,
                    ut,
                    lapp=None,
                    sektioner=None,
                    geometri=geometri,
                    bas_recept_sha256=sha256_fil(recept) if recept.is_file() else "",
                )
                if not andrad:
                    continue
                if instangda_omraden_i_k1_mapp(ut) and not instangda_omraden_i_k1_mapp(r.mapp):
                    return [SLUTET_INFO]
        return []

    def _paverkar_inte(
        self, mallar: Mapping[str, Mall], k9: Mapping[str, tuple[Planscenario, list[str]]]
    ) -> list[str]:
        """Varningar: delar av förslaget som inte når en analys område."""
        ut: list[str] = []
        for kort, (sc, utel) in k9.items():
            namn = {
                "dagsljus": "soltimmarna",
                "vatten": "vattenberäkningen",
                "ekologi": "ekologiberäkningen",
            }.get(kort, kort)
            for aid in utel:
                ut.append(
                    f"Ändringen '{aid}' ligger utanför området för {namn} och räknas inte med där."
                )
            ny = [a for a in sc.andringar if a.typ == "byggnad_ny"]
            rutor = mallar[kort].rutor
            for a in ny:
                g = a.geometri or {}
                xs = [p[0] for p in g["coordinates"][0]]
                ys = [p[1] for p in g["coordinates"][0]]
                traff = any(
                    max(xs) >= r.x_min and min(xs) <= r.x_max and max(ys) >= r.y_min
                    and min(ys) <= r.y_max
                    for r in rutor
                )
                if not traff:
                    ut.append(
                        f"Huset '{a.andring_id}' ligger utanför området för {namn} och "
                        "påverkar inte den beräkningen."
                    )
        return ut

    # ------------------------------------------------------------------ sparade scenarier

    def _scenariomapp(self, projekt: str) -> Path:
        return self.mapp / projekt / "scenarier"

    def _fil(self, projekt: str, namn_slug: str) -> Path:
        if not _SLUG.match(namn_slug):
            raise _fel(f"Ogiltigt scenarionamn '{namn_slug}'.", 400)
        return self._scenariomapp(projekt) / f"{namn_slug}.json"

    def _las(self, projekt: str, namn_slug: str) -> dict[str, Any]:
        fil = self._fil(projekt, namn_slug)
        if not fil.is_file():
            raise _fel(f"Det finns inget sparat scenario '{namn_slug}'.", 404)
        try:
            d = json.loads(fil.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise _fel(f"Scenariofilen {fil.name} går inte att läsa: {exc}") from exc
        if not isinstance(d, dict) or d.get("format") != FORMAT:
            raise _fel(f"Scenariofilen {fil.name} har okänt format.")
        return d

    def lista_scenarier(self, projekt: str) -> dict[str, Any]:
        self._kravs_projekt(projekt)
        ut: list[dict[str, Any]] = []
        mapp = self._scenariomapp(projekt)
        if mapp.is_dir():
            for fil in sorted(mapp.glob("*.json")):
                try:
                    d = json.loads(fil.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(d, dict) or d.get("format") != FORMAT:
                    continue
                ut.append(
                    {
                        "slug": fil.stem,
                        "namn": d.get("namn", fil.stem),
                        "andrad": d.get("andrad"),
                        "antal_andringar": len(d.get("andringar", [])),
                        "korningar": d.get("korningar", {}),
                    }
                )
        return {"scenarier": ut}

    def hamta_scenario(self, projekt: str, namn_slug: str) -> dict[str, Any]:
        self._kravs_projekt(projekt)
        return self._las(projekt, namn_slug)

    def spara_scenario(
        self, projekt: str, namn_slug: str | None, kropp: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Sparar (skapar eller skriver över) ett scenario efter kontroll av ändringarna."""
        self._kravs_projekt(projekt)
        namn = str(kropp.get("namn", "")).strip()
        if not namn:
            raise _fel("Ge scenariot ett namn.", 400)
        ny_slug = namn_slug or slug(namn)
        if not ny_slug:
            raise _fel("Namnet måste innehålla minst en bokstav eller siffra.", 400)
        v = self.validera(projekt, kropp)
        if not v["giltig"]:
            raise _fel("; ".join(v["fel"]))
        fil = self._fil(projekt, ny_slug)
        gammal: dict[str, Any] = {}
        if fil.is_file():
            try:
                gammal = json.loads(fil.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                gammal = {}
        nu = datetime.now(UTC).isoformat(timespec="seconds")
        d = {
            "format": FORMAT,
            "namn": namn,
            "slug": ny_slug,
            "projekt": projekt,
            "skapad": gammal.get("skapad", nu),
            "andrad": nu,
            "beskrivning": str(kropp.get("beskrivning", "")),
            "andringar": v["andringar"],
            "korningar": gammal.get("korningar", {}),
        }
        fil.parent.mkdir(parents=True, exist_ok=True)
        tmp = fil.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(fil)
        return d

    def byt_namn(self, projekt: str, namn_slug: str, nytt_namn: str) -> dict[str, Any]:
        self._kravs_projekt(projekt)
        d = self._las(projekt, namn_slug)
        namn = nytt_namn.strip()
        if not namn:
            raise _fel("Ge scenariot ett namn.", 400)
        ny = slug(namn)
        if not ny:
            raise _fel("Namnet måste innehålla minst en bokstav eller siffra.", 400)
        if ny != namn_slug and self._fil(projekt, ny).is_file():
            raise _fel(f"Det finns redan ett scenario som heter '{ny}'.", 409)
        d.update(namn=namn, slug=ny, andrad=datetime.now(UTC).isoformat(timespec="seconds"))
        d["korningar"] = {}  # frågefilerna heter efter scenariot: nya körs efter namnbytet
        mal = self._fil(projekt, ny)
        mal.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if ny != namn_slug:
            self._fil(projekt, namn_slug).unlink(missing_ok=True)
        return d

    def ta_bort_scenario(self, projekt: str, namn_slug: str) -> dict[str, Any]:
        self._kravs_projekt(projekt)
        fil = self._fil(projekt, namn_slug)
        if not fil.is_file():
            raise _fel(f"Det finns inget sparat scenario '{namn_slug}'.", 404)
        fil.unlink()
        return {"borttaget": namn_slug}

    # ------------------------------------------------------------------ frågor och körning

    def _skriv_fragor(
        self,
        projekt: str,
        namn_slug: str,
        valda: Sequence[str],
        avtappning_mm_h: float | None = None,
        *,
        prefix: str = "rita",
        parametrar_over: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Skriver K9 (per bas) och K20 (per mall) för ett sparat scenario. Returnerar poster.

        `parametrar_over` (analys-id -> parametrar) ersätter mallens parametrar för analysen."""
        mallar = self._kravs_projekt(projekt)
        d = self._las(projekt, namn_slug)
        urval: dict[str, list[str]] = {}
        for v in valda:
            kort, _, aid = v.partition(".")
            if kort not in mallar or aid not in {a.id for a in mallar[kort].fraga.analyser}:
                raise _fel(f"Okänd analys '{v}'.", 400)
            urval.setdefault(kort, []).append(aid)
        if not urval:
            raise _fel("Välj minst en analys att räkna.", 400)
        k9 = self._k9_per_bas(
            mallar, d["andringar"], namn_slug, beskrivning=f"Ritat i kartan: {d['namn']}"
        )
        poster: list[dict[str, Any]] = []
        for kort, analyser in urval.items():
            m = mallar[kort]
            sc, _ = k9[kort]
            if not sc.andringar:
                raise _fel(
                    f"Förslaget har ingen ändring som berör området för {kort}; "
                    "välj en annan analys eller rita inom området."
                )
            k9_fil = self.mapp / projekt / "k9" / f"{namn_slug}__{kort}.json"
            skriv_scenario(sc, k9_fil)
            self.fragedir.mkdir(parents=True, exist_ok=True)
            max_slug = 60 - len(prefix) - 2 - len(kort)
            fraga_id = f"{prefix}-{namn_slug[:max_slug]}-{kort}"
            fil = self.fragedir / f"{fraga_id}.json"
            innehall = self._k20(
                m, fil, k9_fil, fraga_id, d["namn"], analyser,
                avtappning_mm_h=avtappning_mm_h, parametrar_over=parametrar_over,
            )
            fil.write_text(json.dumps(innehall, indent=2, ensure_ascii=False) + "\n", "utf-8")
            ref = self._ref_for(fil)
            poster.append(
                {"mall": kort, "ref": ref, "fraga_id": fraga_id, "k9": str(k9_fil.name),
                 "k9_nyckel": sc.nyckel, "analyser": analyser}
            )
        return poster

    def _ref_for(self, fil: Path) -> str:
        for ref, f in self.fragor._fragefiler().items():  # noqa: SLF001
            if f.resolve() == fil.resolve():
                return ref
        raise _fel("Den skapade frågan hittades inte i frågemapparna.", 500)

    @staticmethod
    def _rel(fran: Path, till: Path) -> str:
        import os

        return Path(os.path.relpath(till, fran.parent)).as_posix()

    def _k20(
        self,
        m: Mall,
        fil: Path,
        k9_fil: Path,
        fraga_id: str,
        scenarionamn: str,
        analyser: Sequence[str],
        *,
        avtappning_mm_h: float | None = None,
        parametrar_over: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        bas = self._rel(fil, m.bas)
        valda = [a for a in m.fraga.analyser if a.id in analyser]
        ut_analyser: list[dict[str, Any]] = []
        for a in valda:
            post: dict[str, Any] = {
                "id": a.id,
                "losare": a.losare,
                "scenarier": ["fore", "ritat"],
                "parametrar": dict(a.parametrar),
            }
            if parametrar_over and a.id in parametrar_over:
                post["parametrar"] = {**post["parametrar"], **parametrar_over[a.id]}
            if avtappning_mm_h is not None and a.losare in _LOSARE_MED_AVTAPPNING:
                post["parametrar"]["avtappning_mm_h"] = float(avtappning_mm_h)
            if a.fokus is not None:
                post["fokus"] = list(a.fokus)
            if a.beror_pa:
                post["beror_pa"] = [b for b in a.beror_pa if b in analyser]
            if a.indata:
                post["indata"] = {k: self._rel(fil, p) for k, p in a.indata.items()}
            ut_analyser.append(post)
        sedda: set[tuple[str, str]] = set()
        jamf: list[dict[str, Any]] = []
        for j in m.fraga.jamforelser:
            if j.analys not in analyser or (j.analys, j.utdata) in sedda:
                continue
            sedda.add((j.analys, j.utdata))
            jamf.append(
                {"analys": j.analys, "utdata": j.utdata, "fore": "fore", "efter": "ritat",
                 "troskel": j.troskel}
            )
        return {
            "kontrakt": "K20/v1",
            "fraga_id": fraga_id,
            "beskrivning": f"Ritat förslag '{scenarionamn}': före och efter.",
            "scenarier": {
                "fore": {"bas": bas},
                "ritat": {"bas": bas, "k9": [self._rel(fil, k9_fil)]},
            },
            "analyser": ut_analyser,
            "jamforelser": jamf,
        }

    def _tid_per_losare(self) -> dict[str, float]:
        """Medianen av tidigare körtider (s) per lösare ur körningarnas sammanställningar."""
        tider: dict[str, list[float]] = {}
        mapp = self.fragor.korningsmapp
        if mapp.is_dir():
            for fil in mapp.glob("*/resultat/sammanstallning.json"):
                try:
                    d = json.loads(fil.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                for j in d.get("jobb", []):
                    if not j.get("fran_cache") and isinstance(j.get("kortid_s"), int | float):
                        tider.setdefault(str(j["losare"]), []).append(float(j["kortid_s"]))
        return {k: float(np.median(v)) for k, v in tider.items() if v}

    def planera(
        self,
        projekt: str,
        namn_slug: str,
        valda: Sequence[str],
        avtappning_mm_h: float | None = None,
    ) -> dict[str, Any]:
        """Planen för att räkna före och efter: vad som räknas om, radie och uppskattad tid."""
        poster = self._skriv_fragor(
            projekt, namn_slug, valda, avtappning_mm_h=_avtappning(avtappning_mm_h)
        )
        tider = self._tid_per_losare()
        ut: list[dict[str, Any]] = []
        for p in poster:
            plan = self.fragor.planera_fraga(p["ref"])
            radier: dict[str, float | None] = {}
            tid_s = 0.0
            tid_kand = True
            for j in plan["jobb"]:
                radier[j["analys"]] = j["radie_m"]
                if j["i_cache"]:
                    continue
                t = tider.get(j["losare"])
                if t is None:
                    tid_kand = False
                else:
                    tid_s += t
            ut.append(
                {
                    **p,
                    "plan": plan,
                    "paverkansradie_m": radier,
                    "uppskattad_tid_s": round(tid_s, 1) if tid_kand else None,
                    "tid_text": (
                        None if tid_kand else "Ingen tidigare körning att uppskatta tiden efter."
                    ),
                }
            )
        return {"slug": namn_slug, "fragor": ut}

    def kor(
        self,
        projekt: str,
        namn_slug: str,
        valda: Sequence[str],
        avtappning_mm_h: float | None = None,
    ) -> dict[str, Any]:
        """Startar körningen. `avtappning_mm_h` (valfri) gäller vattnets analyser i körningen;
        utan den gäller standard 0 mm/h (ledningsnätet antas fullt)."""
        poster = self._skriv_fragor(
            projekt, namn_slug, valda, avtappning_mm_h=_avtappning(avtappning_mm_h)
        )
        d = self._las(projekt, namn_slug)
        korningar: dict[str, Any] = dict(d.get("korningar", {}))
        ut: list[dict[str, Any]] = []
        for p in poster:
            svar = self.fragor.starta_korning(p["ref"], projekt)
            korningar[p["mall"]] = {
                "korning_id": svar["korning_id"],
                "ref": p["ref"],
                "k9_nyckel": p["k9_nyckel"],
            }
            ut.append({**p, "korning": svar})
        d["korningar"] = korningar
        fil = self._fil(projekt, namn_slug)
        fil.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return {"slug": namn_slug, "fragor": ut}

    def kor_ekologi(
        self,
        projekt: str,
        namn_slug: str | None,
        parametrar: Mapping[str, Any],
        konto_projekt: str | None = None,
    ) -> dict[str, Any]:
        """Kör (eller återanvänder ur cachen) ekologianalysen före/efter synkront.

        `namn_slug` är ett sparat scenario; utan det används projektets förslag (demons K9).
        Körningen får ett id som beror på parametrarna och scenariots K9-nyckel, så samma anrop
        ger samma körning. Fel i lösaren (t.ex. för många fläckar) kastas som 400.
        """
        import hashlib

        mallar = self._kravs_projekt(projekt)
        m = mallar.get("ekologi")
        if m is None:
            raise _fel("Projektet har ingen ekologianalys (kör `floden demo`).", 404)
        analys = next((a.id for a in m.fraga.analyser if a.losare == "ekologi.konnektivitet"), None)
        if analys is None:
            raise _fel("Ekologimallen saknar lösaren ekologi.konnektivitet.", 500)
        over = {analys: dict(parametrar)}
        tag = hashlib.sha256(json.dumps(over, sort_keys=True).encode()).hexdigest()[:8]
        if namn_slug is None:
            k9 = next((s.k9 for s in m.fraga.scenarier.values() if s.k9), ())
            if not k9:
                raise _fel("Ekologimallen har inget förslag att jämföra med.", 500)
            fraga_id = f"api-ekologi-{tag}"
            fil = self.fragedir / f"{fraga_id}.json"
            self.fragedir.mkdir(parents=True, exist_ok=True)
            innehall = self._k20(m, fil, k9[-1], fraga_id, "projektets förslag", [analys],
                                 parametrar_over=over)
            scen_nyckel = sha256_fil(k9[-1])
        else:
            poster = self._skriv_fragor(
                projekt, namn_slug, [f"ekologi.{analys}"], prefix=f"api{tag}", parametrar_over=over
            )
            p = poster[0]
            fil = self.fragedir / f"{p['fraga_id']}.json"
            innehall = None
            scen_nyckel = str(p["k9_nyckel"])
            fraga_id = str(p["fraga_id"])
        if innehall is not None:
            fil.write_text(json.dumps(innehall, indent=2, ensure_ascii=False) + "\n", "utf-8")
        ref = self._ref_for(fil)
        korning_id = f"api-ekologi-{tag}-{hashlib.sha256(scen_nyckel.encode()).hexdigest()[:8]}"
        status, _ = self.fragor.kor_synkront(ref, korning_id, konto_projekt or projekt)
        return {"korning_id": korning_id, "status": status, "analys": analys, "ref": ref}

    def status(self, projekt: str, namn_slug: str) -> dict[str, Any]:
        """Körningarnas status och, för klara, vyerna (före, efter, skillnad) för kartan."""
        self._kravs_projekt(projekt)
        d = self._las(projekt, namn_slug)
        ut: list[dict[str, Any]] = []
        for kort, k in d.get("korningar", {}).items():
            try:
                st = self.fragor.korning_status(k["korning_id"])
            except FlodenWebbFel:
                continue
            post: dict[str, Any] = {"mall": kort, "status": st, "vyer": []}
            if st["status"] == "klar":
                try:
                    res = self.fragor.resultat(k["korning_id"])
                    post["vyer"] = self._vyer(res, d["namn"], k["korning_id"])
                    post["varningar"] = [str(v["text"]) for v in res.get("varningar", [])]
                except FlodenWebbFel:
                    pass
            ut.append(post)
        return {"slug": namn_slug, "korningar": ut}

    @staticmethod
    def _vyer(res: Mapping[str, Any], namn: str, korning_id: str) -> list[dict[str, Any]]:
        """Vyer (som projektsidans) för en klar körning: en per jämförelse med skillnadslager."""
        vyer: list[dict[str, Any]] = []
        for la in res["lager"]:
            if la["typ"] != "skillnad" or not la["primar"]:
                continue

            def lager_for(scen: str, analys: str = la["analys"]) -> str | None:
                for x in res["lager"]:
                    if (
                        x["analys"] == analys and x["scenario"] == scen and x["primar"]
                        and x["typ"] != "skillnad"
                    ):
                        return str(x["id"])
                return None

            def markering_for(scen: str, analys: str = la["analys"]) -> str | None:
                # Kontrollraster för slutna områden (K2/v2, `slutsanka.tif`) i efter-läget.
                for x in res["lager"]:
                    if x["analys"] == analys and x["scenario"] == scen and x["roll"] == "slutsanka":
                        return str(x["id"])
                return None

            vyer.append(
                {
                    "id": f"{korning_id}:{la['id']}",
                    "avsnitt": {"skyfall": "vatten", "konnektivitet": "ekologi"}.get(
                        la["analys"], "dagsljus"
                    ),
                    "rubrik": f"{namn}: {la.get('etikett') or la['analys']}",
                    "mattnamn": la.get("etikett") or la["analys"],
                    "korning_id": korning_id,
                    "fore_lager": lager_for(la["fore"]),
                    "efter_lager": lager_for(la["efter"]),
                    "skillnad_lager": la["id"],
                    "markering_lager": markering_for(la["efter"]),
                    "efter_namn": namn,
                    "andring": f"Ritat förslag: {namn}.",
                }
            )
        return vyer

    # ------------------------------------------------------------------ WMS (proxy)

    def _kontrollera_adress(self, url: str) -> urllib.parse.SplitResult:
        delar = urllib.parse.urlsplit(url.strip())
        if delar.scheme not in ("http", "https") or not delar.hostname:
            raise _fel("Adressen måste börja med http:// eller https://.", 400)
        try:
            traffar = socket.getaddrinfo(delar.hostname, delar.port or 80)
        except OSError as exc:
            raise _fel(f"Adressen {delar.hostname} går inte att nå: {exc}", 502) from exc
        for t in traffar:
            raw_ip = ipaddress.ip_address(t[4][0])
            ip = (
                raw_ip.ipv4_mapped
                if isinstance(raw_ip, ipaddress.IPv6Address) and raw_ip.ipv4_mapped is not None
                else raw_ip
            )
            if ip.is_loopback:
                if not self.tillat_loopback:
                    raise _fel("Adresser på den här datorn är inte tillåtna som underlag.", 400)
                continue
            if not ip.is_global:
                raise _fel("Den adressen är inte tillåten.", 400)
        return delar

    def _hamta(self, url: str, max_bytes: int) -> tuple[bytes, str]:
        self._kontrollera_adress(url)
        tjanst = self

        class _Omdirigering(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
                tjanst._kontrollera_adress(newurl)  # noqa: SLF001
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        oppnare = urllib.request.build_opener(_Omdirigering)
        req = urllib.request.Request(url, headers={"User-Agent": "Floden-kartverktyg"})  # noqa: S310
        try:
            with oppnare.open(req, timeout=WMS_TIDSGRANS_S) as svar:  # noqa: S310
                data: bytes = svar.read(max_bytes + 1)
                typ = svar.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            raise _fel(f"Tjänsten svarade med fel {exc.code}.", 502) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise _fel(f"Tjänsten gick inte att nå: {exc}", 502) from exc
        if len(data) > max_bytes:
            raise _fel("Svaret från tjänsten är för stort.", 502)
        return data, typ

    @staticmethod
    def _lokalt(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def wms_lager(self, kropp: Mapping[str, Any]) -> dict[str, Any]:
        """GetCapabilities via servern: titel, version och lager med stödda koordinatsystem."""
        url = str(kropp.get("url", "")).strip()
        delar = self._kontrollera_adress(url)
        fraga = {
            k: v
            for k, v in urllib.parse.parse_qsl(delar.query, keep_blank_values=True)
            if k.lower() not in ("request", "service", "version")
        }
        fraga.update({"SERVICE": "WMS", "REQUEST": "GetCapabilities"})
        bas = urllib.parse.urlunsplit(
            (delar.scheme, delar.netloc, delar.path, urllib.parse.urlencode(fraga), "")
        )
        data, _ = self._hamta(bas, MAX_CAPABILITIES_BYTES)
        if b"<!ENTITY" in data:
            raise _fel("Tjänstens svar innehåller otillåtna definitioner.", 502)
        try:
            rot = ET.fromstring(data)  # noqa: S314 - storlek begränsad, entiteter nekade ovan
        except ET.ParseError as exc:
            raise _fel(f"Svaret från tjänsten är inte en WMS-beskrivning: {exc}", 502) from exc
        if "WMS_Capabilities" not in self._lokalt(rot.tag) and "WMT_MS_Capabilities" not in (
            self._lokalt(rot.tag)
        ):
            raise _fel("Svaret från tjänsten är inte en WMS-beskrivning.", 502)
        version = rot.get("version", "1.3.0")
        titel = ""
        for e in rot.iter():
            if self._lokalt(e.tag) == "Title" and e.text:
                titel = e.text.strip()
                break
        lager: list[dict[str, Any]] = []

        def gå(e: ET.Element, arvt: list[str]) -> None:
            crs = list(arvt)
            namn = ""
            lt = ""
            barn: list[ET.Element] = []
            for c in e:
                t = self._lokalt(c.tag)
                if t in ("CRS", "SRS") and c.text:
                    crs.extend(x.strip().upper() for x in c.text.split())
                elif t == "Name" and c.text:
                    namn = c.text.strip()
                elif t == "Title" and c.text:
                    lt = c.text.strip()
                elif t == "Layer":
                    barn.append(c)
            if namn:
                stod = "3857" if any(x in ("EPSG:3857", "EPSG:900913") for x in crs) else (
                    "4326" if "EPSG:4326" in crs else None
                )
                lager.append({"namn": namn, "titel": lt or namn, "stod": stod})
            for c in barn:
                gå(c, crs)

        for e in rot.iter():
            if self._lokalt(e.tag) == "Capability":
                for c in e:
                    if self._lokalt(c.tag) == "Layer":
                        gå(c, [])
                break
        if not lager:
            raise _fel("Tjänsten har inga lager att visa.", 502)
        return {
            "url": url,
            "titel": titel,
            "version": version,
            "lager": lager,
            "anvandbara": [x for x in lager if x["stod"]],
        }

    def wms_bild(self, query: Mapping[str, list[str]]) -> tuple[bytes, str]:
        """GetMap för en MapLibre-bricka: `bbox` i EPSG:3857, `crs` 3857 eller 4326."""

        def q(namn: str, standard: str = "") -> str:
            return query.get(namn, [standard])[0]

        url = q("url")
        delar = self._kontrollera_adress(url)
        lager = q("lager")
        if not lager:
            raise _fel("Lager saknas.", 400)
        try:
            bbox = [float(x) for x in q("bbox").split(",")]
            bredd = int(q("bredd", "256"))
            hojd = int(q("hojd", "256"))
        except ValueError as exc:
            raise _fel("bbox, bredd och hojd måste vara tal.", 400) from exc
        if len(bbox) != 4 or not all(math.isfinite(x) for x in bbox):
            raise _fel("bbox ska vara fyra tal.", 400)
        if not (16 <= bredd <= 2048 and 16 <= hojd <= 2048):
            raise _fel("Bildstorleken är utanför gränserna.", 400)
        crs = q("crs", "3857")
        version = q("version", "1.3.0")
        if version not in ("1.1.1", "1.3.0"):
            raise _fel("WMS-versionen stöds inte.", 400)
        if crs == "4326":
            r = 6378137.0

            def lonlat(x: float, y: float) -> tuple[float, float]:
                return (
                    math.degrees(x / r),
                    math.degrees(2 * math.atan(math.exp(y / r)) - math.pi / 2),
                )

            lo0, la0 = lonlat(bbox[0], bbox[1])
            lo1, la1 = lonlat(bbox[2], bbox[3])
            box = [la0, lo0, la1, lo1] if version == "1.3.0" else [lo0, la0, lo1, la1]
            kod = "EPSG:4326"
        elif crs == "3857":
            box, kod = bbox, "EPSG:3857"
        else:
            raise _fel("Koordinatsystemet stöds inte (EPSG:3857 eller EPSG:4326).", 400)
        fraga = {
            k: v
            for k, v in urllib.parse.parse_qsl(delar.query, keep_blank_values=True)
            if k.lower() not in ("request", "service", "version", "layers", "styles", "bbox",
                                 "width", "height", "format", "crs", "srs", "transparent")
        }
        fraga.update(
            {
                "SERVICE": "WMS", "VERSION": version, "REQUEST": "GetMap", "LAYERS": lager,
                "STYLES": "", "FORMAT": "image/png", "TRANSPARENT": "TRUE",
                "WIDTH": str(bredd), "HEIGHT": str(hojd),
                "CRS" if version == "1.3.0" else "SRS": kod,
                "BBOX": ",".join(f"{x:.9f}" for x in box),
            }
        )
        mal = urllib.parse.urlunsplit(
            (delar.scheme, delar.netloc, delar.path, urllib.parse.urlencode(fraga), "")
        )
        data, typ = self._hamta(mal, MAX_WMS_BYTES)
        if typ.lower() not in ("image/png", "image/jpeg", "image/webp"):
            raise _fel(
                f"Tjänsten svarade inte med en tillåten rasterbild (fick {typ!r}).",
                502,
            )
        return data, typ

    # ------------------------------------------------------------------ geometri (visning)

    def geometri_png(self, projekt: str, vilket: str) -> tuple[bytes, dict[str, str]]:
        """Terrängskuggning eller vegetation som bild i EPSG:3011, ritad ur K1 v2-rutornas filer.

        Det är en visning av indata (skuggning av `terrang.tif`, höjden i `vegetation.tif`),
        inget resultat från någon lösare.
        """
        from PIL import Image

        from karna.geometri import las_terrang, las_vegetation

        mallar = self._kravs_projekt(projekt)
        if vilket not in ("skuggning", "vegetation"):
            raise _fel(f"Okänt geometrilager '{vilket}'.", 404)
        mall = max(mallar.values(), key=lambda m: len(m.rutor))
        delar: list[tuple[np.ndarray[Any, Any], Any]] = []
        for r in mall.rutor:
            if vilket == "skuggning":
                delar.append(las_terrang(r.mapp))
            else:
                v = las_vegetation(r.mapp)
                if v is not None:
                    delar.append(v)
        if not delar:
            raise _fel("Det finns inget sådant lager i projektets rutor.", 404)
        px = min(g.pixelstorlek_x for _, g in delar)
        x0 = min(g.x_min for _, g in delar)
        y1 = max(g.y_max for _, g in delar)
        x1 = max(g.x_min + g.bredd * g.pixelstorlek_x for _, g in delar)
        y0 = min(g.y_max - g.hojd * g.pixelstorlek_y for _, g in delar)
        b = round((x1 - x0) / px)
        h = round((y1 - y0) / px)
        glob = np.full((h, b), np.nan, dtype=np.float32)
        for arr, g in delar:
            f = round(g.pixelstorlek_x / px)
            a = np.repeat(np.repeat(np.asarray(arr, dtype=np.float32), f, 0), f, 1)
            c0 = round((g.x_min - x0) / px)
            r0 = round((y1 - g.y_max) / px)
            glob[r0 : r0 + a.shape[0], c0 : c0 + a.shape[1]] = a
        steg = max(1, math.ceil(max(h, b) / ORTOFOTO_MAX_PX))
        glob = glob[::steg, ::steg]
        cell = px * steg
        if vilket == "skuggning":
            z = np.where(np.isfinite(glob), glob, np.nanmean(glob))
            dzdy, dzdx = np.gradient(z, cell)
            az, al = math.radians(315.0), math.radians(45.0)
            lutning = np.arctan(np.hypot(dzdx, dzdy))
            aspekt = np.arctan2(dzdy, -dzdx)
            skugga = np.sin(al) * np.cos(lutning) + np.cos(al) * np.sin(lutning) * np.cos(
                az - aspekt
            )
            grå = (np.clip(skugga, 0, 1) * 200 + 30).astype(np.uint8)
            alfa = np.where(np.isfinite(glob), 255, 0).astype(np.uint8)
            rgba = np.dstack([grå, grå, grå, alfa])
        else:
            vh = np.nan_to_num(glob, nan=0.0)
            alfa = np.where(vh >= 0.5, np.clip(80 + vh * 8, 0, 190), 0).astype(np.uint8)
            rgba = np.dstack(
                [np.full(vh.shape, 60, np.uint8), np.full(vh.shape, 140, np.uint8),
                 np.full(vh.shape, 70, np.uint8), alfa]
            )
        buf = io.BytesIO()
        Image.fromarray(rgba, "RGBA").save(buf, format="PNG")
        return buf.getvalue(), {"X-Floden-Utbredning": f"{x0},{y0},{x1},{y1}"}

    # ------------------------------------------------------------------ ortofoto (GeoTIFF)

    def _orto_filer(self) -> list[Path]:
        ut: list[Path] = []
        for rot in self.datamappar:
            if not rot.is_dir():
                continue
            manifest = [rot / "lantmateriet_manifest.json"]
            manifest += list(rot.glob("*/lantmateriet_manifest.json"))
            manifest += list(rot.glob("*/*/lantmateriet_manifest.json"))
            for m in manifest:
                if not m.is_file():
                    continue
                try:
                    d = json.loads(m.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                for f in d.get("filer", []):
                    if f.get("roll") == "orto" and str(f.get("fil", "")).lower().endswith(
                        (".tif", ".tiff")
                    ):
                        p = (m.parent / str(f["fil"])).resolve()
                        if p.is_file() and p.is_relative_to(m.parent.resolve()):
                            ut.append(p)
        return sorted(set(ut))

    def ortofoto_info(self, projekt: str) -> dict[str, Any]:
        filer = self._orto_filer()
        if not filer:
            return {"finns": False}
        return {
            "finns": True,
            "antal_filer": len(filer),
            "png": f"/api/kartverktyg/{projekt}/ortofoto.png",
            "kalla": "©Lantmäteriet (ortofoto, hämtat lokalt)",
        }

    def ortofoto_png(self, projekt: str) -> tuple[bytes, dict[str, str]]:
        """Ortofoto över projektets utsträckning som PNG i EPSG:3011 (omprojicerat vid behov)."""
        import rasterio
        from PIL import Image
        from rasterio.crs import CRS
        from rasterio.transform import from_origin
        from rasterio.warp import Resampling, reproject, transform_bounds

        mallar = self._kravs_projekt(projekt)
        filer = self._orto_filer()
        if not filer:
            raise _fel("Det finns inget lokalt ortofoto.", 404)
        rutor = self._rutor_alla(mallar)
        x0, y0 = min(r[0] for r in rutor), min(r[1] for r in rutor)
        x1, y1 = max(r[2] for r in rutor), max(r[3] for r in rutor)
        res = max((x1 - x0), (y1 - y0)) / ORTOFOTO_MAX_PX
        res = max(res, 0.25)
        bredd, hojd = math.ceil((x1 - x0) / res), math.ceil((y1 - y0) / res)
        dst_t = from_origin(x0, y0 + hojd * res, res, res)
        dst_crs = CRS.from_epsg(3011)
        rgb = np.zeros((3, hojd, bredd), dtype=np.uint8)
        tackt = np.zeros((hojd, bredd), dtype=bool)
        for f in filer:
            with rasterio.open(f) as ds:
                if ds.crs is None or ds.count < 3:
                    continue
                b = transform_bounds(dst_crs, ds.crs, x0, y0, x1, y1)
                if b[2] < ds.bounds.left or b[0] > ds.bounds.right or b[3] < ds.bounds.bottom or (
                    b[1] > ds.bounds.top
                ):
                    continue
                band = np.zeros((3, hojd, bredd), dtype=np.uint8)
                for i in range(3):
                    reproject(
                        source=rasterio.band(ds, i + 1), destination=band[i],
                        src_transform=ds.transform, src_crs=ds.crs,
                        dst_transform=dst_t, dst_crs=dst_crs, resampling=Resampling.bilinear,
                    )
                giltig = band.any(axis=0) & ~tackt
                for i in range(3):
                    rgb[i][giltig] = band[i][giltig]
                tackt |= giltig
        if not tackt.any():
            raise _fel("Ortofotot täcker inte projektets område.", 404)
        rgba = np.dstack([rgb.transpose(1, 2, 0), (tackt * 255).astype(np.uint8)])
        buf = io.BytesIO()
        Image.fromarray(rgba, "RGBA").save(buf, format="PNG")
        return buf.getvalue(), {
            "X-Floden-Utbredning": f"{x0},{y0},{x1},{y1}",
        }
