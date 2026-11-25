"""Innehållet på sidan "Om Floden" (`data/om/floden.toml`) och det som genereras ur det.

Genererar (1) `docs/OM-FLODEN.md` och (2) datafilen till sidan `#/om` i webbklienten
(`webb/src/om/data.generated.ts`). Metodstatus (skiss/rapport) hämtas ur metodregistret och
`data/mcp/metodstatus.toml` och sätts aldrig här. Se `tools/generera_om.py`.

`kontrollera` säkerställer att varje sökväg finns, att varje belägg står i sin källfil, att körtider
och GPU-tider stämmer mot benchmarkfilerna, att faktorer går att räkna om och att inget står som
jämförelse med en annan produkt utan mätning.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from gemensamt import metodregister as mr

REPO_ROT = Path(__file__).resolve().parents[2]
OM_TOML = REPO_ROT / "data" / "om" / "floden.toml"
OM_MD = REPO_ROT / "docs" / "OM-FLODEN.md"
TS_UT = REPO_ROT / "webb" / "src" / "om" / "data.generated.ts"
GITHUB = mr.GITHUB
SMALT_MELLANSLAG = "\u202f"
AMNEN = ("skyfall", "dagsljus", "ekologi", "luft", "buller")
INGEN_UPPGIFT = "Inte angivet på sidan som lästes."
# "snabbare än" får bara stå i publicerade uppgifter om andra verktyg och i förklaringen att
# ingen sådan jämförelse görs.
JAMFORELSEORD = re.compile(r"(snabbare|fortare|bättre|effektivare) än", re.IGNORECASE)
PERSONUPPGIFTER = re.compile(r"Samuel|[\w.+-]+@[\w-]+\.[\w.-]+")


class OmFel(ValueError):
    pass


def las_om(sokvag: Path = OM_TOML) -> dict[str, Any]:
    with sokvag.open("rb") as f:
        return tomllib.load(f)


def tal_sv(v: float, decimaler: int) -> str:
    """Svensk sifferform som `webb/src/tal.ts` (decimalkomma, U+202F, riktigt minustecken)."""
    text = f"{abs(v):,.{decimaler}f}".replace(",", SMALT_MELLANSLAG).replace(".", ",")
    return ("\u2212" if v < 0 else "") + text


def faktor_for(post: dict[str, Any]) -> tuple[float, float] | None:
    """Faktor (lägst, högst) för en uppmätt jämförelse: ur a/b-tiderna eller angiven."""
    if "a_varde" in post and "b_varde" in post:
        f = post["b_varde"] / post["a_varde"]
        return (f, f)
    if "faktor_lag" in post:
        return (post["faktor_lag"], post["faktor_hog"])
    return None


def _strangar(x: Any) -> list[str]:
    if isinstance(x, str):
        return [x]
    if isinstance(x, dict):
        return [s for v in x.values() for s in _strangar(v)]
    if isinstance(x, list):
        return [s for v in x for s in _strangar(v)]
    return []


def _benchmark(rot: Path, sokvag: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((rot / sokvag).read_text(encoding="utf-8"))
    return doc


def _json_fall(doc: dict[str, Any], delnamn: str, fall_id: str) -> dict[str, Any] | None:
    fall = doc.get("delar", {}).get(delnamn, {}).get("fall", [])
    return next((f for f in fall if f.get("id") == fall_id), None)


def kontrollera(
    om: dict[str, Any],
    reg: dict[str, Any],
    metodstatus: dict[str, Any],
    rot: Path = REPO_ROT,
) -> list[str]:
    """Lista med fel; tom lista betyder att innehållet håller."""
    fel: list[str] = []

    def finns(post: str, kalla: str) -> None:
        if not (rot / kalla).exists():
            fel.append(f"{post}: sökvägen {kalla} finns inte")

    def belagg(post: str, p: dict[str, Any]) -> None:
        kalla = p.get("kalla")
        if kalla is None:
            return
        finns(post, kalla)
        text = p.get("belagg")
        if (
            text
            and (rot / kalla).is_file()
            and text not in (rot / kalla).read_text(encoding="utf-8")
        ):
            fel.append(f"{post}: beläggstexten {text!r} står inte i {kalla}")

    ids = [a.get("id") for a in om.get("amne", [])]
    if tuple(ids) != AMNEN:
        fel.append(f"ämnena ska vara {list(AMNEN)}, är {ids}")
    for a in om.get("amne", []):
        if a.get("doman") not in reg.get("doman", {}):
            fel.append(f"{a.get('id')}: okänd domän {a.get('doman')!r}")
        for lista in ("kan", "begransningar"):
            if not a.get(lista):
                fel.append(f"{a.get('id')}: {lista} saknas")
        for t in [*a.get("testat", []), *a.get("nyckeltal", [])]:
            belagg(f"{a.get('id')}/{t.get('text') or t.get('namn')}", t)
            if ("godkanda" in t) != ("av" in t):
                fel.append(f"{a.get('id')}: godkanda och av hör ihop")
            if "godkanda" in t and t["godkanda"] > t["av"]:
                fel.append(f"{a.get('id')}: fler godkända än fall")

    plan = om.get("plan", [])
    if not 5 <= len(plan) <= 10:
        fel.append(f"planen ska ha 5 till 10 punkter, har {len(plan)}")
    plantext = (rot / "PLAN.md").read_text(encoding="utf-8") if (rot / "PLAN.md").is_file() else ""
    for p in plan:
        for polett in re.split(r",\s*| till ", p["uppdrag"]):
            if polett.strip() and polett.strip() not in plantext:
                fel.append(f"plan {p['rubrik']!r}: {polett.strip()!r} står inte i PLAN.md")

    for u in om.get("uppmatt", []):
        pid = f"uppmatt {u.get('id')}"
        belagg(pid, u)
        fk = faktor_for(u)
        if fk is None:
            fel.append(f"{pid}: varken a/b-värden eller faktor_lag/faktor_hog")
        if "json_fall" in u:
            doc = _benchmark(rot, u["kalla"])
            f = _json_fall(doc, u["json_del"], u["json_fall"])
            if f is None:
                fel.append(f"{pid}: fallet {u['json_fall']} finns inte i {u['kalla']}")
            else:
                a_ms = round(f["tid_s"]["median"] * 1000, u["decimaler"])
                b_ms = round(f["extra"]["server_kortid_s"] * 1000, u["decimaler"])
                if a_ms != round(u["a_varde"], u["decimaler"]):
                    fel.append(f"{pid}: a_varde {u['a_varde']} stämmer inte mot källan ({a_ms} ms)")
                if abs(b_ms - u["b_varde"]) > 10 ** (-u["decimaler"]):
                    fel.append(f"{pid}: b_varde {u['b_varde']} stämmer inte mot källan ({b_ms} ms)")
    info = om.get("kortid_info", {})
    finns("kortid_info", info.get("kalla", ""))
    for k in om.get("kortid", []):
        doc = _benchmark(rot, info["kalla"])
        f = _json_fall(doc, k["json_del"], k["json_fall"])
        if f is None:
            fel.append(f"körtid {k['id']}: fallet {k['json_fall']} finns inte i {info['kalla']}")
        elif round(f["tid_s"]["median"], k["decimaler"]) != round(k["varde_s"], k["decimaler"]):
            fel.append(
                f"körtid {k['id']}: {k['varde_s']} s stämmer inte mot källan "
                f"({f['tid_s']['median']})"
            )

    for x in om.get("andra", []):
        aid = x.get("id", "?")
        for nyckel in ("amne", "verktyg", "upphov", "vad", "licens", "pris", "metod", "kortid"):
            if not x.get(nyckel):
                fel.append(f"andra {aid}: {nyckel} saknas")
        if not x.get("lankar"):
            fel.append(f"andra {aid}: länk till källan saknas")
        for lk in x.get("lankar", []):
            if not str(lk.get("url", "")).startswith("https://"):
                fel.append(f"andra {aid}: länken {lk.get('url')!r} är inte https")
    if not om.get("ej_matt", {}).get("text"):
        fel.append("avsnittet om det som inte är mätt saknas")
    elif "ingen jämförande mätning" not in om["ej_matt"]["text"].lower():
        fel.append("ej_matt ska säga att ingen jämförande mätning finns")
    for nyckel in ("skillnad", "saknas"):
        for p in om.get(nyckel, []):
            belagg(f"{nyckel} {p.get('rubrik')}", p)

    # Strikt: ingen jämförelse med en annan produkt utan mätning, och inga personuppgifter.
    for avsnitt, innehall in om.items():
        if avsnitt in ("andra", "ej_matt"):
            continue
        for s in _strangar(innehall):
            if JAMFORELSEORD.search(s):
                fel.append(f"{avsnitt}: jämförelseord utan mätning: {s[:60]!r}")
    for s in _strangar(om):
        if PERSONUPPGIFTER.search(s):
            fel.append(f"personuppgift i texten: {s[:60]!r}")
    return fel


def lank_till(sokvag: str, rot: Path = REPO_ROT) -> dict[str, Any]:
    href = None
    if (rot / sokvag).exists():
        typ = "tree" if (rot / sokvag).is_dir() else "blob"
        href = f"{GITHUB}/{typ}/main/{sokvag.rstrip('/')}"
    return {"text": sokvag, "href": href}


def _utan_belagg(t: dict[str, Any], rot: Path) -> dict[str, Any]:
    """Testat- eller nyckeltalspost för webben: beläggstexten är bara för kontrollen."""
    return {**{k: v for k, v in t.items() if k != "belagg"}, "kalla": lank_till(t["kalla"], rot)}


def _metoder_for(
    doman: str, reg: dict[str, Any], metodstatus: dict[str, Any]
) -> list[dict[str, str]]:
    return [
        {
            "id": m["id"],
            "namn": m["namn"],
            "status": mr.status_for(m, metodstatus),
            "flodeText": mr.FLODE_TEXT[m["anvandarflode"]],
        }
        for m in reg["metod"]
        if m["doman"] == doman
    ]


def data_for_webben(
    om: dict[str, Any],
    reg: dict[str, Any],
    metodstatus: dict[str, Any],
    rot: Path = REPO_ROT,
) -> dict[str, Any]:
    amnen = []
    for a in om["amne"]:
        metoder = _metoder_for(a["doman"], reg, metodstatus)
        amnen.append(
            {
                "id": a["id"],
                "namn": a["namn"],
                "metoder": metoder,
                "kan": list(a["kan"]),
                "testat": [_utan_belagg(t, rot) for t in a.get("testat", [])],
                "nyckeltal": [_utan_belagg(t, rot) for t in a.get("nyckeltal", [])],
                "begransningar": list(a["begransningar"]),
            }
        )
    alla = [m for a in amnen for m in a["metoder"]]
    uppmatt = []
    for u in om["uppmatt"]:
        lag, hog = faktor_for(u) or (None, None)
        uppmatt.append(
            {
                **{
                    k: v
                    for k, v in u.items()
                    if k not in ("belagg", "json_del", "json_fall", "faktor_lag", "faktor_hog")
                },
                "faktorLag": lag,
                "faktorHog": hog,
                "kalla": lank_till(u["kalla"], rot),
            }
        )
    kinfo = om["kortid_info"]
    return {
        "uppdaterad": om["om"]["uppdaterad"],
        "ingress": om["om"]["ingress"],
        "statusText": om["om"]["status_text"],
        "jamforelseIngress": om["om"]["jamforelse_ingress"],
        "kodbas": f"{GITHUB}/blob/main/docs/OM-FLODEN.md",
        "metodAntal": {
            "alla": len(alla),
            "skiss": sum(1 for m in alla if m["status"] == "skiss"),
            "rapport": sum(1 for m in alla if m["status"] == "rapport"),
        },
        "steg": [{"id": s["id"], "namn": s["namn"], "text": s["text"]} for s in om["steg"]],
        "amnen": amnen,
        "plan": [dict(p) for p in om["plan"]],
        "uppmatt": uppmatt,
        "kortid": {
            "kalla": lank_till(kinfo["kalla"], rot),
            "maskin": kinfo["maskin"],
            "rader": [
                {k: v for k, v in r.items() if k not in ("json_del", "json_fall")}
                for r in om["kortid"]
            ],
        },
        "andra": {
            "hamtad": om["andra_info"]["hamtad"],
            "anm": om["andra_info"]["anm"],
            "rader": [dict(x) for x in om["andra"]],
        },
        "ejMatt": dict(om["ej_matt"]),
        "skillnader": [{**s, "kalla": lank_till(s["kalla"], rot)} for s in om["skillnad"]],
        "saknas": [{**s, "kalla": lank_till(s["kalla"], rot)} for s in om["saknas"]],
    }


def ts_innehall(
    om: dict[str, Any], reg: dict[str, Any], metodstatus: dict[str, Any], rot: Path = REPO_ROT
) -> str:
    js = json.dumps(data_for_webben(om, reg, metodstatus, rot), ensure_ascii=False, indent=2)
    return (
        "// GENERERAD av tools/generera_om.py ur data/om/floden.toml, data/matematik/metoder.toml\n"
        "// och data/mcp/metodstatus.toml.\n"
        "// Redigera inte för hand; kör `python tools/generera_om.py`.\n"
        'import type { OmData } from "./typer.ts";\n\n'
        f"export const OM: OmData = {js};\n"
    )


# ------------------------------------------------------------------ Markdown


def _tal_rad(t: dict[str, Any]) -> str:
    """Testat- eller nyckeltalsrad som text med svensk sifferform."""
    ut: str = t.get("text") or t["namn"]
    if "godkanda" in t:
        return f"{ut}: {tal_sv(t['godkanda'], 0)} av {tal_sv(t['av'], 0)}"
    if "varde" in t:
        prefix = f"{t['prefix']} " if t.get("prefix") else ""
        enhet = f" {t['enhet']}" if t.get("enhet") else ""
        return f"{ut}: {prefix}{tal_sv(t['varde'], t.get('decimaler', 0))}{enhet}"
    return ut


def _faktor_text(lag: float, hog: float) -> str:
    def f(v: float) -> str:
        return tal_sv(v, 0 if v >= 10 else 1)

    return f(lag) if lag == hog else f"{f(lag)} till {f(hog)}"


def _kalla_md(k: dict[str, Any]) -> str:
    return f"[`{k['text']}`]({k['href']})" if k["href"] else f"`{k['text']}`"


def _cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def markdown(
    om: dict[str, Any], reg: dict[str, Any], metodstatus: dict[str, Any], rot: Path = REPO_ROT
) -> str:
    d = data_for_webben(om, reg, metodstatus, rot)
    ut: list[str] = [
        "<!-- GENERERAD av tools/generera_om.py ur data/om/floden.toml. Redigera inte. -->",
        "",
        "# Om Floden",
        "",
        f"Uppdaterad {d['uppdaterad']}. {d['ingress']}",
        "",
        f"**Metodstatus:** {d['statusText']} "
        f"Skiss: {tal_sv(d['metodAntal']['skiss'], 0)} av {tal_sv(d['metodAntal']['alla'], 0)} "
        "metoder i registret. Klassningen sätts bara i `data/mcp/metodstatus.toml`.",
        "",
        "## 1. Vad Floden kan nu",
        "",
        "### Arbetsflödet",
        "",
    ]
    for n, s in enumerate(d["steg"], start=1):
        ut.append(f"{n}. **{s['namn']}.** {s['text']}")
    for a in d["amnen"]:
        ut += ["", f"### {a['namn']}", "", "**Kan**", ""]
        ut += [f"- {k}" for k in a["kan"]]
        ut += ["", "**Metoder och metodstatus**", ""]
        ut += ["| Metod | Metodstatus | Var den nås |", "| --- | --- | --- |"]
        ut += [f"| {m['namn']} | {m['status']} | {m['flodeText']} |" for m in a["metoder"]]
        ut += ["", "**Testat**", ""]
        for t in [*a["testat"], *a["nyckeltal"]]:
            ut.append(f"- {_tal_rad(t)} (källa: {_kalla_md(t['kalla'])})")
        ut += ["", "**Begränsningar**", ""]
        ut += [f"- {b}" for b in a["begransningar"]]
    ut += ["", "## 2. Plan", "", "Ur `PLAN.md`; inga datum som inte står där.", ""]
    ut += ["| Område | Vad | Status | Uppdrag |", "| --- | --- | --- | --- |"]
    ut += [
        f"| {_cell(p['rubrik'])} | {_cell(p['text'])} | {p['status']} | {p['uppdrag']} |"
        for p in d["plan"]
    ]
    ut += ["", "## 3. Jämförelse med andra verktyg", "", d["jamforelseIngress"], ""]
    ut += ["### A. Uppmätt i Floden", ""]
    ut += [
        "Floden mot Floden. Varje tal har sin källa i repot och kontrolleras mot den.",
        "",
        "| Fall | Jämförelse | Tal | Maskin | Källa |",
        "| --- | --- | --- | --- | --- |",
    ]
    for u in d["uppmatt"]:
        if "a_varde" in u:
            jam = (
                f"{u['a_namn']} {tal_sv(u['a_varde'], u['decimaler'])} {u['enhet']} mot "
                f"{u['b_namn']} {tal_sv(u['b_varde'], u['decimaler'])} {u['enhet']}"
            )
        else:
            jam = "Hybrid mot full 2D (Floden)"
        faktor = f"cirka {_faktor_text(u['faktorLag'], u['faktorHog'])} gånger"
        ut.append(
            f"| {_cell(u['titel'])} | {_cell(jam)} | {faktor} | {_cell(u['maskin'])} | "
            f"{_kalla_md(u['kalla'])} |"
        )
    ut.append("")
    for u in d["uppmatt"]:
        ut.append(f"- *{u['titel']}:* {u['anm']}")
    k = d["kortid"]
    ut += [
        "",
        "**Körtider i Floden** (median av fem körningar efter en uppvärmning).",
        "",
        f"Maskin: {k['maskin']} Källa: {_kalla_md(k['kalla'])}.",
        "",
        "| Ämne | Fall | Körtid |",
        "| --- | --- | --- |",
    ]
    ut += [
        f"| {r['amne']} | {_cell(r['fall'])} | {tal_sv(r['varde_s'], r['decimaler'])} s |"
        for r in k["rader"]
    ]
    ut += ["", "### B. Offentliga uppgifter om andra verktyg", "", d["andra"]["anm"], ""]
    for x in d["andra"]["rader"]:
        ut += [f"#### {x['verktyg']} ({x['amne']})", "", f"- **Utgivare:** {x['upphov']}"]
        ut += [f"- **Vad som är publicerat:** {x['vad']}"]
        ut += [f"- **Metod:** {x['metod']}", f"- **Licens:** {x['licens']}"]
        ut += [f"- **Pris:** {x['pris']}", f"- **Körtid:** {x['kortid']}"]
        for t in x.get("tal", []):
            ut.append(f"- **Tillverkarens tal:** {_tal_rad(t)}")
        lankar = ", ".join(f"[{lk['text']}]({lk['url']})" for lk in x["lankar"])
        ut += [f"- **Källa (hämtad {d['andra']['hamtad']}):** {lankar}", ""]
    ut += ["### C. Det som inte är mätt", "", d["ejMatt"]["text"], ""]
    ut += ["### D. Skillnader som går att belägga", ""]
    ut += [
        f"- **{s['rubrik']}.** {s['text']} (källa: {_kalla_md(s['kalla'])})"
        for s in d["skillnader"]
    ]
    ut += ["", "### E. Vad andra verktyg har som Floden saknar", ""]
    ut += [
        f"- **{s['rubrik']}.** {s['text']} (källa: {_kalla_md(s['kalla'])})" for s in d["saknas"]
    ]
    ut.append("")
    return "\n".join(ut)
