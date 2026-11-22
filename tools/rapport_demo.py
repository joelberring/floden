"""Referensflöde utan AI: skissunderlag skyfall för demokvarterets vattenfråga, Word och PDF.

Skapar en rapport (K33) över körningen `demo-vatten`, lägger fasta påståenden (K32) för SK-01 till
SK-15 (`rapport.referensflode`), kontrollerar och exporterar. Deterministiskt: inga modeller.
Kräver demon (`floden demo`).

    PYTHONPATH=src python tools/rapport_demo.py [--rot .] [--ut data/lokal/rapporter]
        [--exempel docs/exempel] [--inplantera-fel]
        [--sprakprofil sbk|neutral] [--numrerade-rubriker] [--numrerade-figurer]
        [--avsandare TEXT] [--diarienummer TEXT] [--handlaggare TEXT] [--accentfarg HEX]
        [--logotyp FIL] [--typsnitt-brodtext FIL] [--typsnitt-rubriker FIL]

`--inplantera-fel` ändrar ett lagrat tal i rapporten och visar att exporten stoppas.
Inställningarna läggs ovanpå förvalen och `[rapport]` i `config.local.toml` (docs/RAPPORTER.md).
Sökvägar till logotyp och typsnitt skrivs aldrig ut; bara filernas sha256 lagras.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--rot", default=str(Path(__file__).resolve().parents[1]))
    p.add_argument("--ut", default=None, help="Rapportmapp (standard data/lokal/rapporter).")
    p.add_argument("--exempel", default=None, help="Kopiera Word och PDF hit (t.ex. docs/exempel).")
    p.add_argument("--korning", default="demo-vatten")
    p.add_argument("--inplantera-fel", action="store_true")
    p.add_argument("--sprakprofil", choices=("sbk", "neutral"), default=None)
    p.add_argument("--numrerade-rubriker", action="store_true", default=None)
    p.add_argument("--numrerade-figurer", action="store_true", default=None)
    for namn in ("avsandare", "diarienummer", "handlaggare", "accentfarg"):
        p.add_argument(f"--{namn}", default=None)
    for namn in ("logotyp", "typsnitt-brodtext", "typsnitt-rubriker"):
        p.add_argument(f"--{namn}", default=None, metavar="FIL")
    a = p.parse_args(argv)
    installningar = {
        k: v
        for k in (
            "sprakprofil", "numrerade_rubriker", "numrerade_figurer", "avsandare",
            "diarienummer", "handlaggare", "accentfarg", "logotyp", "typsnitt_brodtext",
            "typsnitt_rubriker",
        )
        if (v := getattr(a, k)) is not None
    }  # fmt: skip
    rot = Path(a.rot).resolve()
    sys.path.insert(0, str(rot / "src"))
    os.chdir(rot)

    from floden_mcp.karna_api import FlodenTjanst
    from rapport.referensflode import gardens_mottagare, pastaenden
    from rapport.skissexport import ExportStoppad
    from rapport.skisskontroll import kontroll_text
    from rapport.skissrapport import RapportTjanst
    from webb.api import FlodenApiApp

    app = FlodenApiApp(testsvit_rot=rot / "testsvit", konfig_fil=rot / "config.toml")
    tjanst = FlodenTjanst(app, rot=rot)
    rt = RapportTjanst(tjanst, rapportmapp=a.ut)
    r = rt.skapa(
        projekt="demokvarteret",
        amne="skyfall",
        korningar=[a.korning],
        titel="Skissunderlag skyfall – demokvarteret, nytt hus i sydvästra gården",
        mottagare=[gardens_mottagare()],
        ai_stod=False,
        av="referensflode",
        installningar=installningar,
    )
    print(
        f"Rapport {r.rapport_id}: {len(r.fragor)} frågor, {len(r.kartor)} kartor, "
        f"språkprofil {r.installningar.sprakprofil}"
    )
    for kw in pastaenden(sprakprofil=r.installningar.sprakprofil):
        pa = rt.lagg_till_pastaende(
            r.rapport_id, forfattare="referensflode", av="referensflode", **kw
        )
        print(f"  {pa.pastaende_id} {pa.fraga_id} {pa.status:16s} {pa.fylld_text()[:90]}")

    if a.inplantera_fel:
        fil = rt.arkiv.rapportmapp(r.rapport_id) / "rapport.json"
        data = json.loads(fil.read_text(encoding="utf-8"))
        b = data["pastaenden"][5]["belagg"][0]
        print(f"Inplanterat fel: {b['adress']} {b['varde']} -> {b['varde'] * 1.1}")
        b["varde"] = b["varde"] * 1.1
        fil.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            rt.exportera(r.rapport_id, "pdf", av="referensflode")
        except ExportStoppad as exc:
            print(str(exc))
            return 0
        print("FEL: exporten stoppades inte.")
        return 1

    k = rt.kontrollera(r.rapport_id, av="referensflode")
    print(kontroll_text(k))
    filer = [rt.exportera(r.rapport_id, fmt, av="referensflode") for fmt in ("word", "pdf")]
    for f in filer:
        print(f"Skrev {f} ({f.stat().st_size} byte)")
    if a.exempel:
        mal = Path(a.exempel)
        mal.mkdir(parents=True, exist_ok=True)
        for f in filer:
            namn = "skissunderlag_skyfall_demo" + f.suffix
            shutil.copyfile(f, mal / namn)
            print(f"Kopierade till {mal / namn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
