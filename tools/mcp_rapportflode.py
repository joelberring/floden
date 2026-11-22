# ruff: noqa: E501
"""Rapportflöde via MCP: skissunderlag skyfall där påståendena läggs genom `floden mcp` (stdio).

Samma påståenden som referensflödet (`rapport.referensflode`), men varje steg är ett MCP-anrop:
`fragekatalog_sok`, `rapport_skapa`, `pastaende_lagg_till`, `rapport_kontrollera`,
`rapport_exportera` (Word och PDF) och `rapport_hamta`. Visar också att ett påstående med en egen
siffra (utan belägg) vägras. Skriptet räknar ingenting själv. Kräver demon (`floden demo`).

    PYTHONPATH=src python tools/mcp_rapportflode.py [--rot .]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp_types import Implementation


async def anropa(c: Client, namn: str, argument: dict[str, Any] | None = None) -> dict[str, Any]:
    r = await c.call_tool(namn, argument or {})
    if r.is_error:
        raise SystemExit(f"{namn} misslyckades: {r.content[0].text}")  # type: ignore[union-attr]
    return dict(r.structured_content or {})


async def flode(rot: Path, modell: str) -> None:
    sys.path.insert(0, str(rot / "src"))
    from rapport.referensflode import gardens_mottagare, pastaenden

    par = StdioServerParameters(
        command=sys.executable,
        args=["-m", "floden.cli", "mcp", "--rot", str(rot)],
        env={
            **os.environ,
            "PYTHONPATH": str(rot / "src"),
            "FLODEN_MCP_MODELL": modell,
            "FLODEN_MCP_ROLL": "skribent",
        },
    )
    async with Client(par, client_info=Implementation(name="mcp_rapportflode", version="1")) as c:
        kat = await anropa(c, "fragekatalog_sok", {"omrade": "skyfall"})
        print(f"Frågekatalogen: {kat['antal']} skyfallsfrågor (källäge {kat['granskningsdatum']})")
        r = await anropa(
            c,
            "rapport_skapa",
            {
                "korningar": ["demo-vatten"],
                "amne": "skyfall",
                "mottagare": [gardens_mottagare()],
                "titel": "Skissunderlag skyfall – demokvarteret (via MCP)",
            },
        )
        rid = r["rapport_id"]
        print(
            f"Rapport {rid}: {len(r['fragor'])} frågor, {len(r['kartor'])} kartor, "
            f"{len(r['belagg_adresser'])} beläggsadresser"
        )
        fel = await c.call_tool(
            "pastaende_lagg_till",
            {
                "rapport_id": rid,
                "fraga_id": "SK-06",
                "status": "visat",
                "fordjupning": "ja",
                "text_med_platshallare": "Vattnet blir 20 cm djupare.",
                "belagg": [],
            },
        )
        print("Påstående med egen siffra:", "vägrat" if fel.is_error else "ANTAGET (fel!)")
        print("  ", fel.content[0].text.splitlines()[0][:150])  # type: ignore[union-attr]
        for kw in pastaenden():
            arg: dict[str, Any] = {"rapport_id": rid, "text_med_platshallare": kw["text"]}
            arg.update({k: v for k, v in kw.items() if k != "text"})
            p = await anropa(c, "pastaende_lagg_till", arg)
            print(f"  {p['pastaende_id']} {p['fraga_id']} {p['status']:16s} {p['fylld_text'][:80]}")
        k = await anropa(c, "rapport_kontrollera", {"rapport_id": rid})
        print("Kontroll:", "godkänd" if k["godkand"] else "UNDERKÄND")
        for fmt in ("word", "pdf"):
            e = await anropa(c, "rapport_exportera", {"rapport_id": rid, "format": fmt})
            print(f"Export {fmt}: {e['fil']} ({e['storlek_byte']} byte)")
        h = await anropa(c, "rapport_hamta", {"rapport_id": rid})
        print(f"rapport_hamta: {len(h['pastaenden'])} påståenden, exporter {len(h['exporter'])}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--rot", default=str(Path(__file__).resolve().parents[1]))
    p.add_argument("--modell", default="mcp-rapportflode (fasta påståenden)")
    a = p.parse_args()
    anyio.run(flode, Path(a.rot).resolve(), a.modell)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
