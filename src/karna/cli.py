"""CLI för kärnan: `floden fraga {kor,plan,losare}` (T20)."""

from __future__ import annotations

import argparse
import json
from typing import Any

from karna.cache import CacheFel, ResultatCache
from karna.fraga import FragaFel, las_fraga
from karna.korning import beskriv_plan, kor_fraga, planera
from karna.losare import LosarFel, lista_losare
from karna.scenario import ScenarioFel

STANDARD_CACHE = "data/cache/karna"


def lagg_till_parser(subparsers: Any) -> None:
    """Lägger till `fraga` i huvudparserns underkommandon."""
    p = subparsers.add_parser(
        "fraga",
        help="Utredningsfrågor (K20): kör, planera och lista lösare",
        description="Gemensam kärna: kör en utredningsfråga över alla domäner med cache.",
    )
    sub = p.add_subparsers(dest="fraga_kommando")

    kor = sub.add_parser("kor", help="Kör en fråga (bara jobb som inte finns i cachen)")
    kor.add_argument("fraga", help="Frågefil (.toml eller .json)")
    kor.add_argument("--ut", default=None, help="Utmapp (standard: resultat/<fraga_id>)")
    kor.add_argument("--cache", default=STANDARD_CACHE, help="Cachekatalog")

    plan = sub.add_parser("plan", help="Visa jobb, nycklar och cachestatus utan att köra")
    plan.add_argument("fraga", help="Frågefil (.toml eller .json)")
    plan.add_argument("--cache", default=STANDARD_CACHE, help="Cachekatalog")

    sub.add_parser("losare", help="Lista registrerade lösare")


def kor_kommando(args: argparse.Namespace) -> int:
    """Kör `floden fraga ...`."""
    try:
        if args.fraga_kommando == "losare":
            for los in lista_losare():
                try:
                    r = los.paverkansradie_m(los.validera_parametrar({}))
                    radie = "global" if r is None else f"{r:g} m"
                except LosarFel:
                    radie = "parametrar"
                print(
                    f"{los.namn:32s} v{los.version:4s} {los.doman:9s} "
                    f"radie={radie:10s} {los.beskrivning}"
                )
            return 0
        if args.fraga_kommando not in ("kor", "plan"):
            print("Ange ett underkommando: kor, plan eller losare.")
            return 2
        fraga = las_fraga(args.fraga)
        cache = ResultatCache(args.cache)
        plan = planera(fraga, cache)
        if args.fraga_kommando == "plan":
            print(json.dumps(beskriv_plan(plan), indent=2, ensure_ascii=False))
            print(json.dumps(plan.sammanfattning(), ensure_ascii=False))
            return 0
        ut = args.ut or f"resultat/{fraga.fraga_id}"
        res = kor_fraga(fraga, cache, ut, plan=plan)
        s = res.sammanstallning["sammanfattning"]
        print(
            f"{fraga.fraga_id}: {s['jobb']} jobb, {s['i_cache']} från cache, "
            f"{s['att_kora']} körda på {s['total_tid_s']:.2f} s -> {res.utmapp}"
        )
        for jf in res.sammanstallning["jamforelser"]:
            print(
                f"  {jf['namn']}: max ökning {jf['max_okning']:.4f}, "
                f"yta ökning {jf['yta_okning_m2']:.1f} m² (tröskel {jf['troskel']})"
            )
        return 0
    except (FragaFel, LosarFel, ScenarioFel, CacheFel) as exc:
        print(f"Fel: {exc}")
        return 1
