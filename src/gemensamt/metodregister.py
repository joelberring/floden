"""Metodregistret (`data/matematik/metoder.toml`) och det som genereras ur det.

Genererar (1) metodtabellen och metodavsnitten i `docs/MATEMATIK.md` och (2) datafilen till sidan
"Matematik och forskning" i webbklienten (`webb/src/matematik/data.generated.ts`). Klassningen
skiss/rapport hämtas ur `data/mcp/metodstatus.toml` och sätts aldrig i registret.
Se `tools/generera_matematik.py`.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

REPO_ROT = Path(__file__).resolve().parents[2]
REGISTER = REPO_ROT / "data" / "matematik" / "metoder.toml"
METODSTATUS = REPO_ROT / "data" / "mcp" / "metodstatus.toml"
MATEMATIK_MD = REPO_ROT / "docs" / "MATEMATIK.md"
TS_UT = REPO_ROT / "webb" / "src" / "matematik" / "data.generated.ts"
GITHUB = "https://github.com/joelberring/floden"
MARKOR_START = "<!-- METODTABELL:START (genereras av tools/generera_matematik.py) -->"
MARKOR_SLUT = "<!-- METODTABELL:SLUT -->"
MARKOR_DOMANER = "<!-- Per domän, genererat ur samma register -->"

FLODE_TEXT = {
    "flode": "I användarflödet",
    "karta": "I kartsidan (#/karta), inte i flödet",
    "api": "Bara i API:t",
    "cli": "Kommandorad, MCP och testsvit",
    "fristaende": "Fristående modul",
}
_RUTER = ("src/", "tests/", "tools/", "webb/", "rust/", "docs/", "data/", "testsvit/")


class RegisterFel(ValueError):
    pass


def las_register(sokvag: Path = REGISTER) -> dict[str, Any]:
    with sokvag.open("rb") as f:
        return tomllib.load(f)


def las_metodstatus(sokvag: Path = METODSTATUS) -> dict[str, Any]:
    with sokvag.open("rb") as f:
        return tomllib.load(f)


def metodstatus_nycklar(metod: dict[str, Any]) -> list[str]:
    """Metodens nycklar i metodstatus.toml: `metodstatus` och eventuella `metodstatus_ocksa`."""
    forsta = [metod["metodstatus"]] if metod.get("metodstatus") else []
    return [*forsta, *metod.get("metodstatus_ocksa", [])]


def status_for(metod: dict[str, Any], metodstatus: dict[str, Any]) -> str:
    """`rapport` bara om alla nycklar är klassade så, annars `skiss`; utan nyckel: förvalet."""
    standard = str(metodstatus.get("defaults", {}).get("status", "skiss"))
    nycklar = metodstatus_nycklar(metod)
    if not nycklar:
        return standard
    stat = {str(metodstatus.get(n, {}).get("status", standard)) for n in nycklar}
    return "rapport" if stat == {"rapport"} else "skiss"


def sokvag_i(post: str) -> str | None:
    """Sökvägen i en post som `src/x.py (funktion)`; None när posten inte börjar med en sökväg."""
    forsta = post.split(" ", 1)[0].rstrip(",")
    if "/" in forsta and forsta.startswith(_RUTER):
        return forsta
    return None


def kontrollera(
    reg: dict[str, Any], metodstatus: dict[str, Any], rot: Path = REPO_ROT
) -> list[str]:
    """Lista med fel: saknade sökvägar, okänd domän/metodstatus/flödesnivå, dubbla id."""
    fel: list[str] = []
    sedda: set[str] = set()
    for m in reg.get("metod", []):
        mid = m.get("id", "?")
        if mid in sedda:
            fel.append(f"{mid}: dubbelt id")
        sedda.add(mid)
        if m.get("doman") not in reg.get("doman", {}):
            fel.append(f"{mid}: okänd domän {m.get('doman')!r}")
        if m.get("anvandarflode") not in FLODE_TEXT:
            fel.append(f"{mid}: okänd anvandarflode {m.get('anvandarflode')!r}")
        for nyckel in metodstatus_nycklar(m):
            if nyckel not in metodstatus:
                fel.append(f"{mid}: metodstatus {nyckel!r} finns inte i data/mcp/metodstatus.toml")
        if not m.get("formel"):
            fel.append(f"{mid}: formel saknas")
        for f in m.get("formel", []):
            if not f.get("latex") or not f.get("text"):
                fel.append(f"{mid}: formel utan latex eller text")
        for post in [*m.get("kod", []), *m.get("facit", [])]:
            s = sokvag_i(post)
            if s is not None and not (rot / s).exists():
                fel.append(f"{mid}: sökvägen {s} finns inte")
    for q in reg.get("fraga", []):
        for post in q.get("underlag", []):
            s = sokvag_i(post)
            if s is not None and not (rot / s).exists():
                fel.append(f"{q.get('id')}: sökvägen {s} finns inte")
    return fel


def _lank(post: str, rot: Path) -> dict[str, Any]:
    s = sokvag_i(post)
    href = None
    if s is not None and (rot / s).exists():
        typ = "tree" if (rot / s).is_dir() else "blob"
        href = f"{GITHUB}/{typ}/main/{s.rstrip('/')}"
    return {"text": post, "href": href}


def data_for_webben(
    reg: dict[str, Any], metodstatus: dict[str, Any], rot: Path = REPO_ROT
) -> dict[str, Any]:
    domaner = []
    for did, d in reg["doman"].items():
        metoder = []
        for m in reg["metod"]:
            if m["doman"] != did:
                continue
            metoder.append(
                {
                    "id": m["id"],
                    "namn": m["namn"],
                    "sammanfattning": m["sammanfattning"],
                    "formler": [f["text"] for f in m["formel"]],
                    "flode": m["anvandarflode"],
                    "flodeText": FLODE_TEXT[m["anvandarflode"]],
                    "status": status_for(m, metodstatus),
                    "kod": [_lank(p, rot) for p in m["kod"]],
                    "kontrakt": list(m.get("kontrakt", [])),
                    "facit": [_lank(p, rot) for p in m.get("facit", [])],
                    "anmarkning": m.get("anmarkning", ""),
                }
            )
        domaner.append({"id": did, "namn": d["namn"], "metoder": metoder})
    fragor = [
        {
            "id": q["id"],
            "titel": q["titel"],
            "kort": q["kort"],
            "underlag": [_lank(p, rot) for p in q.get("underlag", [])],
        }
        for q in reg["fraga"]
    ]
    return {
        "doman": domaner,
        "fragor": fragor,
        "flodeText": FLODE_TEXT,
        "kodbas": f"{GITHUB}/blob/main/docs/MATEMATIK.md",
    }


def ts_innehall(reg: dict[str, Any], metodstatus: dict[str, Any], rot: Path = REPO_ROT) -> str:
    js = json.dumps(data_for_webben(reg, metodstatus, rot), ensure_ascii=False, indent=2)
    return (
        "// GENERERAD av tools/generera_matematik.py ur data/matematik/metoder.toml\n"
        "// och data/mcp/metodstatus.toml.\n"
        "// Redigera inte för hand; kör `python tools/generera_matematik.py`.\n"
        'import type { MatematikData } from "./typer.ts";\n\n'
        f"export const MATEMATIK: MatematikData = {js};\n"
    )


def markdown_tabell(reg: dict[str, Any], metodstatus: dict[str, Any]) -> str:
    rader = [
        "| Domän | Metod | Kod | Kontrakt | Metodstatus | Var den nås |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for did, d in reg["doman"].items():
        for m in reg["metod"]:
            if m["doman"] != did:
                continue
            kod = "<br>".join(f"`{sokvag_i(p) or p}`" for p in m["kod"])
            kontrakt = ", ".join(m.get("kontrakt", [])) or "–"
            rader.append(
                f"| {d['namn']} | {m['namn']} | {kod} | {kontrakt} | "
                f"{status_for(m, metodstatus)} | {FLODE_TEXT[m['anvandarflode']]} |"
            )
    return "\n".join(rader)


def markdown_domaner(reg: dict[str, Any], metodstatus: dict[str, Any]) -> str:
    """Ett avsnitt per domän och metod: formel, kod, kontrakt, facittest och status."""
    ut: list[str] = []
    for n, (did, d) in enumerate(reg["doman"].items(), start=3):
        ut.append(f"## {n}. {d['namn']}\n")
        for m in reg["metod"]:
            if m["doman"] != did:
                continue
            ut.append(f"### {m['namn']} (`{m['id']}`)\n")
            ut.append(f"{m['sammanfattning']}\n")
            for f in m["formel"]:
                ut.append(f"$$ {f['latex']} $$\n")
            kod = ", ".join(f"`{p}`" for p in m["kod"])
            ut.append(f"- **Kod:** {kod}")
            ut.append(f"- **Kontrakt:** {', '.join(m.get('kontrakt', [])) or 'inget eget'}")
            facit = "; ".join(m.get("facit", [])) or "inga"
            ut.append(f"- **Facittest:** {facit}")
            ut.append(
                f"- **Status:** {status_for(m, metodstatus)} (metodstatus); "
                f"{FLODE_TEXT[m['anvandarflode']].lower()}"
            )
            if m.get("anmarkning"):
                ut.append(f"- **Observera:** {m['anmarkning']}")
            ut.append("")
    return "\n".join(ut)


def markdown_block(reg: dict[str, Any], metodstatus: dict[str, Any]) -> str:
    tabell = markdown_tabell(reg, metodstatus)
    return f"{tabell}\n\n{MARKOR_DOMANER}\n\n{markdown_domaner(reg, metodstatus)}"


def md_med_tabell(text: str, tabell: str) -> str:
    a = text.find(MARKOR_START)
    b = text.find(MARKOR_SLUT)
    if a < 0 or b < 0 or b < a:
        raise RegisterFel("Markörerna METODTABELL saknas i docs/MATEMATIK.md.")
    return f"{text[:a]}{MARKOR_START}\n\n{tabell}\n\n{text[b:]}"
