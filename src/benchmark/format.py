"""Formatet `floden-benchmark/v1` (K37): bygga, kontrollera och jämföra benchmarkdokument.

Rena funktioner utan beräkningar, så att de går att testa utan att köra något tungt.
"""

from __future__ import annotations

import math
import os
import platform
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

FORMAT = "floden-benchmark/v1"
KALLOR = ("server", "webblasare")
DELAR = ("skyfall", "dagsljus", "luft", "buller")
DELSTATUS = ("kord", "hoppas_over", "kraver_lokal_kallmapp")
FALLSTATUS = ("ok", "fel", "hoppas_over")
STANDARD_TROSKEL_PROCENT = 25.0
"""Förval för försämringsflaggan. Delade maskiner varierar tiotals procent mellan körningar."""


class BenchmarkFormatFel(ValueError):
    """Dokumentet följer inte `floden-benchmark/v1`."""


def _cpu_modell() -> str:
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            for rad in fh:
                if rad.lower().startswith("model name"):
                    return rad.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine() or "okänd"


def _minne_totalt_mb() -> float | None:
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for rad in fh:
                if rad.startswith("MemTotal:"):
                    return round(int(rad.split()[1]) / 1024.0, 0)
    except (OSError, ValueError, IndexError):
        pass
    return None


def _rust_info() -> dict[str, Any]:
    """Rust-kärnan (`rust/tvad_karna`) är valfri och används bara av `tvad.rust_backend`.

    Benchmarksviten kör Python-lösarna, så `anvands_i_svit` är alltid falskt; `tillganglig`
    anger om binären finns att anropa."""
    try:
        from tvad.rust_backend import hitta_tvad_karna

        sokvag: str | None = str(hitta_tvad_karna())
    except Exception:  # noqa: BLE001 - saknad binär och andra fel betyder samma sak här
        sokvag = None
    return {"tillganglig": sokvag is not None, "sokvag": sokvag, "anvands_i_svit": False}


def maskininfo() -> dict[str, Any]:
    """Maskinen och biblioteken benchmarken kördes på (K37 `maskin`)."""
    import numpy

    try:
        antal_tillgangliga: int | None = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        antal_tillgangliga = None
    return {
        "cpu": _cpu_modell(),
        "karnor": os.cpu_count(),
        "karnor_tillgangliga": antal_tillgangliga,
        "minne_totalt_mb": _minne_totalt_mb(),
        "os": platform.platform(),
        "python": platform.python_version(),
        "numpy": numpy.__version__,
        "rust_karna": _rust_info(),
        "gpu": None,
        "webblasare": None,
    }


def nu_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def skapa_dokument(
    *,
    kalla: str,
    kodversion: str,
    maskin: Mapping[str, Any],
    installning: Mapping[str, Any],
    delar: Mapping[str, Any],
    anmarkning: str = "",
    skapad_utc: str | None = None,
) -> dict[str, Any]:
    """Sätter ihop ett dokument och kontrollerar det (kastar `BenchmarkFormatFel`)."""
    dok: dict[str, Any] = {
        "format": FORMAT,
        "kalla": kalla,
        "skapad_utc": skapad_utc or nu_utc(),
        "kodversion": kodversion,
        "maskin": dict(maskin),
        "installning": dict(installning),
        "anmarkning": anmarkning,
        "delar": dict(delar),
    }
    kontrollera(dok)
    return dok


def _tal(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def kontrollera(dok: Any) -> None:
    """Kastar `BenchmarkFormatFel` med en svensk förklaring om dokumentet bryter mot K37."""
    if not isinstance(dok, Mapping):
        raise BenchmarkFormatFel("Dokumentet måste vara ett objekt.")
    if dok.get("format") != FORMAT:
        raise BenchmarkFormatFel(f"format måste vara {FORMAT!r}, fick {dok.get('format')!r}.")
    if dok.get("kalla") not in KALLOR:
        raise BenchmarkFormatFel(f"kalla måste vara en av {KALLOR}, fick {dok.get('kalla')!r}.")
    for nyckel in ("skapad_utc", "kodversion"):
        if not isinstance(dok.get(nyckel), str) or not dok[nyckel]:
            raise BenchmarkFormatFel(f"{nyckel} saknas.")
    for nyckel in ("maskin", "installning", "delar"):
        if not isinstance(dok.get(nyckel), Mapping):
            raise BenchmarkFormatFel(f"{nyckel} måste vara ett objekt.")
    for namn, del_ in dok["delar"].items():
        if not isinstance(del_, Mapping):
            raise BenchmarkFormatFel(f"delar.{namn} måste vara ett objekt.")
        if del_.get("status") not in DELSTATUS:
            raise BenchmarkFormatFel(
                f"delar.{namn}.status måste vara en av {DELSTATUS}, fick {del_.get('status')!r}."
            )
        if del_["status"] != "kord" and not del_.get("skal"):
            raise BenchmarkFormatFel(f"delar.{namn} är inte körd och måste ange skal.")
        fall = del_.get("fall", [])
        if not isinstance(fall, list):
            raise BenchmarkFormatFel(f"delar.{namn}.fall måste vara en lista.")
        for f in fall:
            _kontrollera_fall(f"delar.{namn}", f)


def _kontrollera_fall(var: str, f: Any) -> None:
    if not isinstance(f, Mapping) or not isinstance(f.get("id"), str) or not f["id"]:
        raise BenchmarkFormatFel(f"{var}: varje fall måste ha ett id.")
    var = f"{var}.{f['id']}"
    if f.get("status") not in FALLSTATUS:
        raise BenchmarkFormatFel(f"{var}.status måste vara en av {FALLSTATUS}.")
    if f["status"] != "ok":
        if not f.get("skal"):
            raise BenchmarkFormatFel(f"{var} har status {f['status']} och måste ange skal.")
        return
    tid = f.get("tid_s")
    if not isinstance(tid, Mapping) or not _tal(tid.get("median")) or tid["median"] < 0:
        raise BenchmarkFormatFel(f"{var}.tid_s.median måste vara ett tal >= 0.")
    minne = f.get("minne")
    if not isinstance(minne, Mapping):
        raise BenchmarkFormatFel(f"{var}.minne måste vara ett objekt (får vara tomt).")
    for k, v in minne.items():
        if v is not None and not _tal(v):
            raise BenchmarkFormatFel(f"{var}.minne.{k} måste vara ett tal eller null.")
    av = f.get("avvikelse")
    if av is not None:
        if not isinstance(av, Mapping) or not _tal(av.get("varde")):
            raise BenchmarkFormatFel(f"{var}.avvikelse.varde måste vara ett tal (eller hela null).")
        if not isinstance(av.get("enhet"), str):
            raise BenchmarkFormatFel(f"{var}.avvikelse.enhet saknas.")


# ----------------------------------------------------------------------------- jämförelse


def _hamta(dok: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    ut: dict[tuple[str, str], Mapping[str, Any]] = {}
    for del_namn, del_ in dok.get("delar", {}).items():
        for f in del_.get("fall", []):
            if f.get("status") == "ok":
                ut[(del_namn, f["id"])] = f
    return ut


def _matt(f: Mapping[str, Any]) -> dict[str, float | None]:
    minne = f.get("minne", {})
    av = f.get("avvikelse")
    return {
        "tid_s": float(f["tid_s"]["median"]),
        "rss_topp_mb": minne.get("rss_topp_mb"),
        "tracemalloc_topp_mb": minne.get("tracemalloc_topp_mb"),
        "avvikelse": float(av["varde"]) if isinstance(av, Mapping) else None,
    }


def andring_procent(ny: float, gammal: float) -> float | None:
    """Förändring i procent; None när det gamla värdet är 0 (kvoten är då inte definierad)."""
    if gammal == 0.0:
        return None
    return (ny - gammal) / abs(gammal) * 100.0


def jamfor_dokument(
    ny: Mapping[str, Any],
    gammal: Mapping[str, Any],
    troskel_procent: float = STANDARD_TROSKEL_PROCENT,
) -> dict[str, Any]:
    """Jämför två dokument fall för fall (del + id) på tid, minne och avvikelse.

    Ett mått flaggas som försämring när det ökat med mer än `troskel_procent` (allt är
    "mindre är bättre"). Avvikelsen flaggas bara om den ökat och den gamla var > 0.
    Fall som bara finns i ena dokumentet listas som `bara_i_ny` och `bara_i_gammal`.
    """
    if not (math.isfinite(troskel_procent) and troskel_procent >= 0.0):
        raise BenchmarkFormatFel("Tröskeln måste vara ett tal >= 0.")
    kontrollera(ny)
    kontrollera(gammal)
    a, b = _hamta(ny), _hamta(gammal)
    rader: list[dict[str, Any]] = []
    for nyckel in sorted(a.keys() & b.keys()):
        mn, mg = _matt(a[nyckel]), _matt(b[nyckel])
        for matt in ("tid_s", "rss_topp_mb", "tracemalloc_topp_mb", "avvikelse"):
            vn, vg = mn[matt], mg[matt]
            if vn is None or vg is None:
                continue
            ch = andring_procent(vn, vg)
            rader.append(
                {
                    "del": nyckel[0],
                    "fall": nyckel[1],
                    "matt": matt,
                    "gammal": vg,
                    "ny": vn,
                    "andring_procent": ch,
                    "forsamring": ch is not None and ch > troskel_procent,
                }
            )
    return {
        "troskel_procent": troskel_procent,
        "rader": rader,
        "forsamringar": [r for r in rader if r["forsamring"]],
        "bara_i_ny": sorted(f"{d}/{i}" for d, i in a.keys() - b.keys()),
        "bara_i_gammal": sorted(f"{d}/{i}" for d, i in b.keys() - a.keys()),
        "annan_maskin": _maskin_nyckel(ny) != _maskin_nyckel(gammal),
    }


def _maskin_nyckel(dok: Mapping[str, Any]) -> tuple[Any, ...]:
    m = dok.get("maskin", {})
    return (m.get("cpu"), m.get("karnor"), dok.get("kalla"))


# ----------------------------------------------------------------------------- tabeller


def _fmt(v: float | None, enhet: str = "", dec: int = 3) -> str:
    if v is None:
        return "–"
    if v != 0 and (abs(v) < 1e-3 or abs(v) >= 1e5):
        return f"{v:.2e}{enhet}"
    return f"{v:.{dec}f}{enhet}"


def _kort(text: str, n: int = 70) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def tabell(dok: Mapping[str, Any]) -> str:
    """Terminaltabell: en rad per fall, en rad per del som inte körts."""
    rubrik = ["Del", "Fall", "Tid, median (s)", "RSS topp (MB)", "Tracemalloc (MB)", "Avvikelse"]
    rader: list[list[str]] = []
    for del_namn, del_ in dok["delar"].items():
        if del_["status"] != "kord":
            rader.append([del_namn, f"({del_['status']})", "–", "–", "–", _kort(del_["skal"])])
            continue
        for f in del_["fall"]:
            if f["status"] != "ok":
                rader.append(
                    [del_namn, f["id"], "–", "–", "–", _kort(f"{f['status']}: {f['skal']}")]
                )
                continue
            av = f.get("avvikelse")
            if av:
                godk = {True: " ok", False: " ÖVER GRÄNS", None: ""}[av.get("godkand")]
                avtext = f"{_fmt(av['varde'], dec=4)} {av['enhet']}{godk}"
            else:
                avtext = "inget facit"
            rader.append(
                [
                    del_namn,
                    f["id"],
                    _fmt(f["tid_s"]["median"]),
                    _fmt(f["minne"].get("rss_topp_mb"), dec=1),
                    _fmt(f["minne"].get("tracemalloc_topp_mb"), dec=1),
                    avtext,
                ]
            )
    bredd = [max(len(r[i]) for r in [rubrik, *rader]) for i in range(len(rubrik))]

    def rad(r: list[str]) -> str:
        return "| " + " | ".join(c.ljust(bredd[i]) for i, c in enumerate(r)) + " |"

    skiljelinje = "|-" + "-|-".join("-" * b for b in bredd) + "-|"
    return "\n".join([rad(rubrik), skiljelinje, *(rad(r) for r in rader)])


def jamforelsetabell(j: Mapping[str, Any]) -> str:
    rubrik = ["Del", "Fall", "Mått", "Gammal", "Ny", "Förändring", ""]
    rader = []
    for r in j["rader"]:
        ch = r["andring_procent"]
        rader.append(
            [
                r["del"],
                r["fall"],
                r["matt"],
                _fmt(r["gammal"], dec=4),
                _fmt(r["ny"], dec=4),
                "–" if ch is None else f"{ch:+.1f} %",
                "FÖRSÄMRING" if r["forsamring"] else "",
            ]
        )
    bredd = [max(len(c[i]) for c in [rubrik, *rader]) for i in range(len(rubrik))]

    def rad(c: list[str]) -> str:
        return "| " + " | ".join(x.ljust(bredd[i]) for i, x in enumerate(c)) + " |"

    delar = [
        rad(rubrik),
        "|-" + "-|-".join("-" * b for b in bredd) + "-|",
        *(rad(c) for c in rader),
    ]
    delar.append(
        f"\nTröskel för försämring: {j['troskel_procent']:g} % ökning. "
        f"Flaggade mått: {len(j['forsamringar'])}."
    )
    if j["annan_maskin"]:
        delar.append("OBS: dokumenten är från olika maskiner eller källor; jämförelsen är osäker.")
    if j["bara_i_ny"]:
        delar.append("Bara i nya: " + ", ".join(j["bara_i_ny"]))
    if j["bara_i_gammal"]:
        delar.append("Bara i gamla: " + ", ".join(j["bara_i_gammal"]))
    return "\n".join(delar)
