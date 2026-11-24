# ruff: noqa: E501
"""Referensflöde utan AI: skissunderlag skyfall, dagsljus, ekologi eller luft för demokvarteret, Word och PDF.

Skapar en rapport (K33) över körningen `demo-vatten`, lägger fasta påståenden (K32) för SK-01 till
SK-15 (`rapport.referensflode`), kontrollerar och exporterar. Deterministiskt: inga modeller.
Kräver demon (`floden demo`).

    PYTHONPATH=src python tools/rapport_demo.py [--amne skyfall|dagsljus|ekologi|luft] [--rot .] [--ut data/lokal/rapporter]
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
    p.add_argument("--amne", choices=("skyfall", "dagsljus", "ekologi", "luft"), default="skyfall")
    p.add_argument("--korning", default=None, help="Standard demo-vatten (skyfall), demo-dagsljus, demo-ekologi, demo-luft.")
    p.add_argument("--roll", choices=("planhandlaggare", "byggaktor", "granskare"), default="planhandlaggare",
                   help="Dagsljus, ekologi och luft: beställningens mottagare.")
    p.add_argument("--alla-fragor", action="store_true",
                   help="Dagsljus: ta med alla DL- och SO-frågor (förval: bara de där Floden kan ge underlag).")
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
    from rapport.referensflode_dagsljus import kontekst_fran_rapport
    from rapport.referensflode_dagsljus import pastaenden as pastaenden_dagsljus
    from rapport.referensflode_ekologi import kontekst_fran_rapport as kontekst_ekologi
    from rapport.referensflode_ekologi import pastaenden as pastaenden_ekologi
    from rapport.referensflode_luft import kontekst_fran_rapport as kontekst_luft
    from rapport.referensflode_luft import pastaenden as pastaenden_luft
    from rapport.skissexport import ExportStoppad
    from rapport.skisskontroll import kontroll_text
    from rapport.skissrapport import RapportTjanst
    from webb.api import FlodenApiApp

    app = FlodenApiApp(testsvit_rot=rot / "testsvit", konfig_fil=rot / "config.toml")
    tjanst = FlodenTjanst(app, rot=rot)
    rt = RapportTjanst(tjanst, rapportmapp=a.ut)
    korning = a.korning or {"dagsljus": "demo-dagsljus", "ekologi": "demo-ekologi", "luft": "demo-luft"}.get(
        a.amne, "demo-vatten"
    )
    if a.amne == "luft":
        best = rt.bestallning_skapa(
            syfte="Skissa hur PM10 förhåller sig till dygnsnormen före och efter ett förslag i demokvarteret, som intervall ur två påhittade timserier",
            amnen=["luft"],
            projekt="demokvarteret",
            mottagare_roll=a.roll,
        )
        print(f"Beställning {best.bestallning_id}: {len(best.urval)} frågor ({best.utelamnade} utelämnade)")
        r = rt.skapa(
            projekt="demokvarteret",
            korningar=[korning],
            mottagare=[],
            ai_stod=False,
            av="referensflode",
            installningar=installningar,
            bestallning=best,
        )
        kw_lista = pastaenden_luft(kontekst_luft(rt, r), r.fragor)
    elif a.amne == "ekologi":
        best = rt.bestallning_skapa(
            syfte="Skissa hur ett nytt hus i demokvarteret påverkar ett skogsspridningssamband (grön infrastruktur) för barrskogsmesar",
            amnen=["ekologi"],
            projekt="demokvarteret",
            mottagare_roll=a.roll,
            scenario={"fore": "fore", "efter": ["efter"]},
        )
        print(f"Beställning {best.bestallning_id}: {len(best.urval)} frågor ({best.utelamnade} utelämnade)")
        r = rt.skapa(
            projekt="demokvarteret",
            korningar=[korning],
            mottagare=[],
            ai_stod=False,
            av="referensflode",
            installningar=installningar,
            bestallning=best,
        )
        kw_lista = pastaenden_ekologi(kontekst_ekologi(rt, r), r.fragor)
    elif a.amne == "dagsljus":
        best = rt.bestallning_skapa(
            syfte="Skissa hur ett höjt hus och ett nytt hus i demokvarteret påverkar sol på marken "
            "och dagsljus vid fasad",
            amnen=["dagsljus", "sol"],
            projekt="demokvarteret",
            mottagare_roll=a.roll,
            bara_med_underlag=not a.alla_fragor,
            scenario={"fore": "bas", "efter": ["hojd", "nytt_hus"]},
        )
        print(f"Beställning {best.bestallning_id}: {len(best.urval)} frågor ({best.utelamnade} utelämnade)")
        r = rt.skapa(
            projekt="demokvarteret",
            korningar=[korning],
            mottagare=[gardens_mottagare()],
            ai_stod=False,
            av="referensflode",
            installningar=installningar,
            bestallning=best,
        )
        kw_lista = pastaenden_dagsljus(kontekst_fran_rapport(rt, r), r.fragor)
    else:
        r = rt.skapa(
            projekt="demokvarteret",
            amne="skyfall",
            korningar=[korning],
            titel="Skissunderlag skyfall – demokvarteret, nytt hus i sydvästra gården",
            mottagare=[gardens_mottagare()],
            ai_stod=False,
            av="referensflode",
            installningar=installningar,
        )
        kw_lista = pastaenden(sprakprofil=r.installningar.sprakprofil)
    print(
        f"Rapport {r.rapport_id}: {len(r.fragor)} frågor, {len(r.kartor)} kartor, "
        f"språkprofil {r.installningar.sprakprofil}"
    )
    for kw in kw_lista:
        pa = rt.lagg_till_pastaende(
            r.rapport_id, forfattare="referensflode", av="referensflode", **kw
        )
        print(f"  {pa.pastaende_id} {pa.fraga_id} {pa.status:16s} {pa.fylld_text()[:90]}")

    if a.inplantera_fel:
        fil = rt.arkiv.rapportmapp(r.rapport_id) / "rapport.json"
        data = json.loads(fil.read_text(encoding="utf-8"))
        b = next(x for x in data["pastaenden"] if x["belagg"])["belagg"][0]
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
            namn = f"skissunderlag_{a.amne}_demo" + f.suffix
            shutil.copyfile(f, mal / namn)
            print(f"Kopierade till {mal / namn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
