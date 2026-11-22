# ruff: noqa: E501
"""Provflöde mot en riktig `floden mcp`-process över stdio.

Utforska demon, rita ett hus, planera, kör, läs skillnaden i soltimmar och maxdjup och hämta en
karta. Allt går genom MCP-protokollet; skriptet räknar ingenting själv. Kräver att demovärlden
finns (`floden demo`) eller byggs av skriptet om `data/demo` saknas.

    PYTHONPATH=src python tools/mcp_provflode.py [--rot .] [--hojd 24]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp_types import Implementation


def rubrik(text: str) -> None:
    print(f"\n=== {text}")


def visa(obj: Any, max_tecken: int = 700) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=1)
    print(text if len(text) <= max_tecken else text[:max_tecken] + " …")


async def anropa(c: Client, namn: str, argument: dict[str, Any] | None = None) -> Any:
    r = await c.call_tool(namn, argument or {})
    if r.is_error:
        raise SystemExit(f"{namn} misslyckades: {r.content[0]}")
    return r


async def flode(rot: Path, hojd: float) -> None:
    par = StdioServerParameters(
        command=sys.executable,
        args=["-m", "floden.cli", "mcp", "--rot", str(rot)],
        env={
            **os.environ,
            "PYTHONPATH": str(rot / "src"),
            "FLODEN_MCP_MODELL": "provflode-skript",
            "FLODEN_MCP_ROLL": "utforskare",
        },
    )
    async with Client(par, client_info=Implementation(name="mcp_provflode", version="1")) as c:
        verktyg = await c.list_tools()
        rubrik(f"Verktyg ({len(verktyg.tools)}): " + ", ".join(t.name for t in verktyg.tools))

        rubrik("1. Utforska demokvarteret")
        info = (await anropa(c, "projekt_info")).structured_content
        print(
            f"dataklass={info['dataklass']} epsg={info['epsg']} byggnader={info['antal_byggnader']}"
        )
        print("analyser:", ", ".join(a["id"] for a in info["tillgangliga_analyser"]))
        geo = (await anropa(c, "geometri_sammanfattning")).structured_content
        visa(geo["byggnader"])
        metoder = (await anropa(c, "metoder_lista")).structured_content["metoder"]
        print("metoder:", {m["namn"]: m["validering"]["status"] for m in metoder})

        rubrik("2. Rita ett hus (samma plats som demons, annan höjd)")
        sys.path.insert(0, str(rot / "src"))
        from demovarld.demo import LITEN
        from demovarld.varld import nytt_hus_polygon

        ring = [[e, n] for e, n in nytt_hus_polygon(LITEN.varld())]
        hus = {"id": "mitt_hus", "polygon": ring, "hojd_m": hojd}
        namn = f"provflode {hojd:g} m"
        kolla = (
            await anropa(c, "scenario_skapa", {"namn": namn, "hus": [hus], "bara_kontroll": True})
        ).structured_content
        print("kontroll:", {k: kolla[k] for k in ("giltig", "sparat", "fel") if k in kolla})
        sc = (await anropa(c, "scenario_skapa", {"namn": namn, "hus": [hus]})).structured_content
        print("sparat:", sc["sparat"], "scenario:", sc["scenario"])

        rubrik("3. Körplan")
        plan = (await anropa(c, "korplan", {"scenario": sc["scenario"]})).structured_content
        for f in plan["fragor"]:
            print(f["mall"], f["plan"]["sammanfattning"])

        rubrik("4. Kör och vänta")
        kor = (await anropa(c, "kor", {"scenario": sc["scenario"]})).structured_content
        ids = [k["korning_id"] for k in kor["korningar"]]
        print("körnings-id:", ids)
        v = (await anropa(c, "vanta", {"korning_ids": ids, "tidsgrans_s": 900})).structured_content
        print("alla_klara:", v["alla_klara"])

        rubrik("5. Skillnaden (Floden räknar, skriptet läser)")
        vatten_id = None
        for kid in ids:
            j = (await anropa(c, "jamfor", {"korning_id": kid})).structured_content["jamforelser"][
                0
            ]
            vatten_id = kid if j["analys"] == "skyfall" else vatten_id
            for nyckel in ("max_minskning", "max_okning", "yta_minskning_m2", "yta_okning_m2"):
                m = j.get(nyckel)
                if m and m.get("varde") is not None:
                    b = m["belagg"]
                    print(
                        f"{j['analys']:8} {nyckel:17} {m['varde']:.3f} {m['enhet']:3} belägg: {b['korning_id']} / {b['fil']} / {b['matt']}"
                    )

        rubrik("6. Karta")
        assert vatten_id
        r = await anropa(c, "karta_bild", {"korning_id": vatten_id, "analys": "skyfall"})
        bild = next(x for x in r.content if x.type == "image")
        png = base64.b64decode(bild.data)
        ut = rot / "data" / "lokal" / "mcp" / "provflode_karta.png"
        ut.parent.mkdir(parents=True, exist_ok=True)
        ut.write_bytes(png)
        print(f"PNG {len(png)} byte, {bild.mime_type}, sparad {ut}")
        visa(
            {
                k: r.structured_content[k]
                for k in ("dataklass", "metodstatus", "enhet", "typ")
                if k in r.structured_content
            }
        )

        rubrik("7. Körloggen")
        logg = (await anropa(c, "korlogg_hamta", {"senaste": 5})).structured_content
        print("hashkedja:", logg["hashkedja"])
        for rad in logg["rader"]:
            print(rad["verktyg"], rad["utfall"], rad["klient"]["namn"], rad["modell"], rad["roll"])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--rot", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--hojd", type=float, default=24.0)
    a = p.parse_args()
    anyio.run(flode, a.rot.resolve(), a.hojd)


if __name__ == "__main__":
    main()
